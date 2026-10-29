"""Single-process Windows session worker. No lead ingestion, SMS or CRM writes."""
import asyncio
import os
import re
import json
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.models.dealersocket import DealerSocketStatus, RooftopAccess
from app.services.dealersocket_auth import DealerSocketLogin, LoginRequired

ROOFTOPS = (
    ("cranbourne_kia", "Cranbourne Kia"),
    ("cranbourne_mg", "Cranbourne MG"),
    ("geely_cranbourne", "Geely Cranbourne"),
)
BASE = "https://bb.dealersocket.com/#/homepage"
MAX_AUTH_ATTEMPTS = 7


def now():
    return datetime.now(timezone.utc).isoformat()


class ProfileLease:
    """OS lock is released on process exit; prevents two workers opening one profile."""
    def __init__(self, path):
        self.path = path
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            # Reading byte zero while another Windows process locks it raises EACCES.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("profile_busy") from None
        self.file = handle

    def release(self):
        if self.file:
            self.file.close()
            self.file = None


class DealerSocketSession:
    def __init__(self, config=settings):
        self.config = config
        self.context = None
        self.page = None
        self.playwright = None
        self.lease = None
        self.task = None
        self.monitor = None
        self.lock = asyncio.Lock()
        self.state = "disconnected"
        self.operation = None
        self.last_checked = None
        self.last_connected = None
        self.error_code = None
        self.message = "Connection has not been checked."
        self.verified = set()
        self.reader_busy = False
        self.auth_attempts = 0
        self.auth_next_attempt = 0
        self.auth_state_path = None
        if getattr(config, 'DEALERSOCKET_AUTO_LOGIN_ENABLED', False):
            profile = Path(config.DEALERSOCKET_PROFILE_DIR)
            if not profile.is_absolute():
                profile = Path(__file__).resolve().parents[2] / profile
            self.auth_state_path = profile.parent / 'dealersocket-auth-recovery.json'
            try:
                saved = json.loads(self.auth_state_path.read_text(encoding='utf-8'))
                self.auth_attempts = max(0, int(saved['attempts']))
                # Preserve an old terminal stop: earlier files did not distinguish
                # explicit rejection from an exhausted three-attempt budget.
                if 'maxAttempts' not in saved and self.auth_attempts >= 3:
                    self.auth_attempts = MAX_AUTH_ATTEMPTS
                self.auth_next_attempt = max(0, float(saved['nextAttemptAt']))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, KeyError):
                self.auth_attempts = MAX_AUTH_ATTEMPTS  # Corrupted recovery state must not reset lockout protection.

    def _save_auth_recovery(self):
        if self.auth_state_path:
            self.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.auth_state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'attempts': self.auth_attempts, 'maxAttempts': MAX_AUTH_ATTEMPTS,
                                             'nextAttemptAt': self.auth_next_attempt}), encoding='utf-8')
            os.replace(temporary, self.auth_state_path)

    async def _automatic_login(self):
        login = DealerSocketLogin(self.config)
        login.validate()
        if self.auth_attempts >= MAX_AUTH_ATTEMPTS:
            raise LoginRequired('auto_login_manual_required', 'Automatic login is stopped by its retry limit or a verification challenge. Complete login manually.')
        if time.time() < self.auth_next_attempt:
            raise LoginRequired('auto_login_cooldown', 'Automatic login is cooling down. Complete login manually or wait for the next retry.')
        # Persist before touching credentials so a restart cannot bypass retry limits.
        self.auth_attempts += 1
        self.auth_next_attempt = time.time() + self.config.DEALERSOCKET_AUTH_COOLDOWN_SECONDS
        self._save_auth_recovery()
        self.message = 'Completing normal DealerSocket password and authenticator verification.'
        try:
            await login.run(self.page, self._probe)
        except LoginRequired as error:
            if error.code in ('auto_login_manual_required', 'auto_login_untrusted_origin'):
                self.auth_attempts = MAX_AUTH_ATTEMPTS
                self._save_auth_recovery()
            raise

    def status(self):
        # Reads own the browser, intentionally suspending the homepage checks.
        # Report activity rather than either a stale disconnect or fresh authentication.
        reading = self.reader_busy and self.state == "connected" and self.operation is None
        # A cached success is not a promise that the session is still valid.
        stale = self.state == "connected" and self.last_checked and (
            datetime.now(timezone.utc) - datetime.fromisoformat(self.last_checked)
        ).total_seconds() > 90
        return DealerSocketStatus(
            state="connecting" if reading else "disconnected" if stale else self.state,
            operation="read" if reading else self.operation, lastCheckedAt=self.last_checked,
            lastConnectedAt=self.last_connected, errorCode=None if reading else "check_stale" if stale else self.error_code,
            message="Lead read in progress. Session checks resume when it finishes." if reading else
                    "Connection check is stale. Check again." if stale else self.message,
            interactiveLoginEnabled=self.config.DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED,
            rooftops=[RooftopAccess(id=id, name=name, verified=id in self.verified and not stale and not reading
                                   and self.state == "connected") for id, name in ROOFTOPS],
        )

    async def start(self, operation):
        if operation not in ("login", "check", "automatic_login"):
            raise ValueError("Unknown session operation")
        if self.reader_busy:
            raise RuntimeError("browser_busy")
        # No await before scheduling: admission is atomic on the service's one event loop.
        # Do not wait for the browser lock here; a human login may take five minutes.
        if self.task and not self.task.done():
            if operation == 'automatic_login':
                # Never present an already-running manual action as automatic login.
                raise RuntimeError('browser_busy')
            return self.status()
        if operation in ('login', 'automatic_login') and not self.config.DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED:
            raise PermissionError("Interactive login is disabled on the automation PC.")
        if operation == 'automatic_login' and not getattr(self.config, 'DEALERSOCKET_AUTO_LOGIN_ENABLED', False):
            raise PermissionError('Automatic login is disabled in the connector settings.')
        self.state, self.operation = "connecting", 'login' if operation == 'automatic_login' else operation
        self.error_code, self.verified = None, set()
        self.message = ('Opening Chrome for automatic password and authenticator verification.' if operation == 'automatic_login'
                        else "Opening manual login on the automation PC." if operation == "login" else "Checking DealerSocket access.")
        self.task = asyncio.create_task(self._run(operation))
        return self.status()

    async def _open(self, visible):
        if self.context:
            await self._close_browser()
        profile = Path(self.config.DEALERSOCKET_PROFILE_DIR)
        if not profile.is_absolute():
            profile = Path(__file__).resolve().parents[2] / profile
        self.lease = ProfileLease(profile.parent / (profile.name + ".lock"))
        self.lease.acquire()
        # Imported lazily: status/API tests never launch a browser or need Playwright.
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), channel="chrome", headless=not visible,
            viewport={"width": 1440, "height": 900}, timezone_id="Australia/Melbourne",
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await self.page.goto(BASE, wait_until="domcontentloaded", timeout=30000)

    async def _probe(self):
        self.last_checked = now()
        if not self.page or self.page.is_closed():
            self.state, self.verified = "disconnected", set()
            self.message = "Automation browser is closed. Check connection to reopen it."
            return False
        # Inspect only the known DealerSocket origin. A CRM-shaped URL alone is insufficient.
        if urlparse(self.page.url).hostname != "bb.dealersocket.com":
            self.state, self.verified = "login_required", set()
            self.message = "Complete DealerSocket login and any verification on the automation PC."
            return False
        frame = self.page.locator("iframe#app-homepage, iframe#app-crm")
        # Login overlays can coexist with a stale rendered CRM iframe.
        challenges = self.page.locator(
            'input[type="password"]:visible, input[autocomplete="one-time-code"]:visible'
        )
        if await challenges.count():
            self.state, self.verified = "login_required", set()
            self.message = "Complete DealerSocket login and any verification on the automation PC."
            return False
        authenticated = False
        for index in range(await frame.count()):
            candidate = frame.nth(index)
            if await candidate.is_visible():
                content = await candidate.element_handle()
                child = await content.content_frame() if content else None
                host = urlparse(child.url).hostname if child else None
                if child and host and (host == "dealersocket.com" or host.endswith(".dealersocket.com")):
                    # Require rendered CRM content, not just an iframe shell.
                    body = (await child.locator("body").inner_text(timeout=3000)).strip()
                    authenticated = bool(re.search(r"add quick up|daily responsibilities|opportunit(?:y|ies)|customer search", body, re.I))
                    authenticated = authenticated and not bool(re.search(r"verification code|enter (?:the )?code|sign in to|session expired", body, re.I))
                    if authenticated:
                        break
        if not authenticated:
            self.state, self.verified = "login_required", set()
            self.message = "Authenticated CRM content was not verified. Complete login or check again."
            return False
        self.state, self.error_code = "connected", None
        self.last_connected = self.last_checked
        self.message = "DealerSocket session verified. Expiry is controlled by DealerSocket."
        # Only explicit dealership labels count; generic brand names are not rooftop evidence.
        labels = await self.page.locator("md-select#select_1 md-option, md-select#select_1 .md-select-value, md-select#select_1 md-select-value").all_text_contents()
        normalized = [" ".join(label.lower().split()) for label in labels]
        self.verified = {id for id, name in ROOFTOPS if any(name.lower() in label for label in normalized)}
        return True

    async def _run(self, operation):
        try:
            async with self.lock:
                if operation in ('login', 'automatic_login') or not self.context or not self.page or self.page.is_closed():
                    await self._open(visible=operation in ('login', 'automatic_login'))
                else:
                    await self.page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
                # Login has five minutes for a human; regular checks allow SPA rendering.
                deadline = asyncio.get_running_loop().time() + (300 if operation == "login" else 20)
                while True:
                    if await self._probe() or not self.page or self.page.is_closed():
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        break
                    self.state = "connecting"
                    await asyncio.sleep(2)
                if (operation in ('check', 'automatic_login') and self.state != 'connected' and self.page and not self.page.is_closed()
                        and getattr(self.config, 'DEALERSOCKET_AUTO_LOGIN_ENABLED', False)):
                    await self._automatic_login()
                if self.state == 'connected' and self.auth_attempts:
                    self.auth_attempts = 0
                    self.auth_next_attempt = 0
                    self._save_auth_recovery()
        except LoginRequired as error:
            self.state, self.verified = 'login_required', set()
            self.error_code, self.message = error.code, str(error)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.state, self.verified = "error", set()
            self.error_code = "profile_busy" if str(error) == "profile_busy" else "browser_unavailable"
            self.message = ("Another connector owns the browser profile. Use one worker."
                            if self.error_code == "profile_busy" else
                            "Cannot verify the automation browser. Check Chrome installation, the Windows desktop and connector setup.")
            # Do not log raw Playwright exceptions, URLs, credentials or browser storage.
            await self._close_browser()
        finally:
            self.operation = None

    async def _watch(self):
        while True:
            await asyncio.sleep(max(10, self.config.DEALERSOCKET_CHECK_INTERVAL_SECONDS))
            if (self.context or getattr(self.config, 'DEALERSOCKET_AUTO_LOGIN_ENABLED', False)) and not self.reader_busy and not (self.task and not self.task.done()):
                # Use a full navigation so a stale loaded page is not treated as valid indefinitely.
                await self.start("check")

    async def startup(self):
        if not self.monitor or self.monitor.done():
            self.monitor = asyncio.create_task(self._watch())

    async def _close_browser(self):
        if self.context:
            with suppress(Exception):
                await self.context.close()
        if self.playwright:
            with suppress(Exception):
                await self.playwright.stop()
        if self.lease:
            self.lease.release()
        self.context = self.page = self.playwright = self.lease = None

    async def shutdown(self):
        for task in (self.monitor, self.task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await self._close_browser()


dealersocket_session = DealerSocketSession()
