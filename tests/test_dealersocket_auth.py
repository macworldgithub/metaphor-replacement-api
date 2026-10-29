"""Synthetic authentication tests; never submit credentials to a live service."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.dealersocket_auth import DealerSocketLogin, LoginRequired, totp
from app.services.dealersocket_session import DealerSocketSession


def config(folder, **changes):
    values = dict(DEALERSOCKET_AUTO_LOGIN_ENABLED=True, DEALERSOCKET_USERNAME='fixture@example.test',
                  DEALERSOCKET_PASSWORD='fixture-password',
                  DEALERSOCKET_TOTP_SECRET='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ',
                  DEALERSOCKET_AUTH_HOSTS='bb.dealersocket.com,na.login.solera.com',
                  DEALERSOCKET_AUTH_COOLDOWN_SECONDS=900,
                  DEALERSOCKET_PROFILE_DIR=str(Path(folder) / 'profile'),
                  DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED=True,
                  DEALERSOCKET_CHECK_INTERVAL_SECONDS=30)
    return SimpleNamespace(**{**values, **changes})


class TotpTests(unittest.TestCase):
    def test_rfc6238_sha1_vectors_reduced_to_six_digits(self):
        seed = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        for stamp, code in [(59, '287082'), (1111111109, '081804'), (1111111111, '050471'),
                            (1234567890, '005924'), (2000000000, '279037'), (20000000000, '353130')]:
            self.assertEqual(totp(seed, stamp), code)
        self.assertEqual(totp(seed.lower(), 59), '287082')

    def test_invalid_secret_has_sanitized_error(self):
        with self.assertRaises(LoginRequired) as error:
            totp('this-is-not-a-secret')
        self.assertNotIn('this-is-not-a-secret', str(error.exception))

    def test_trusted_origins_are_exact_https_hosts(self):
        with tempfile.TemporaryDirectory() as folder:
            login = DealerSocketLogin(config(folder))
            self.assertTrue(login.trusted('https://na.login.solera.com/login'))
            for url in ['http://na.login.solera.com', 'https://na.login.solera.com.evil.test',
                        'https://evil.test', 'https://user@na.login.solera.com/login']:
                self.assertFalse(login.trusted(url))


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_automatic_action_opens_chrome_and_runs_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            async def open_browser(**kwargs):
                worker.page = MagicMock()
                worker.page.is_closed.return_value = False
            worker._open = AsyncMock(side_effect=open_browser)
            worker._probe = AsyncMock(return_value=False)
            async def recover():
                worker.state = 'connected'
            worker._automatic_login = AsyncMock(side_effect=recover)
            loop = asyncio.get_running_loop()
            stamp = loop.time()
            with patch.object(loop, 'time', side_effect=[stamp, stamp + 21, stamp + 21, stamp + 21]):
                await worker._run('automatic_login')
            worker._open.assert_awaited_once_with(visible=True)
            worker._automatic_login.assert_awaited_once()
            self.assertEqual(worker.state, 'connected')

    async def test_visible_action_admission_preserves_public_login_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            worker._run = AsyncMock()
            result = await worker.start('automatic_login')
            self.assertEqual(result.operation, 'login')
            await worker.task
            worker._run.assert_awaited_once_with('automatic_login')

    async def test_visible_auto_action_requires_both_feature_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            for changes in [{'DEALERSOCKET_AUTO_LOGIN_ENABLED': False}, {'DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED': False}]:
                worker = DealerSocketSession(config(folder, **changes))
                with self.assertRaises(PermissionError):
                    await worker.start('automatic_login')
                self.assertIsNone(worker.task)

    async def test_visible_automatic_action_does_not_misidentify_running_manual_login(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            worker._run = AsyncMock(side_effect=lambda operation: asyncio.sleep(30))
            worker.task = asyncio.create_task(asyncio.sleep(30))
            try:
                with self.assertRaises(RuntimeError):
                    await worker.start('automatic_login')
            finally:
                worker.task.cancel()
                try:
                    await worker.task
                except asyncio.CancelledError:
                    pass
    async def test_missing_password_does_not_consume_an_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder, DEALERSOCKET_PASSWORD=''))
            with self.assertRaises(LoginRequired) as error:
                await worker._automatic_login()
            self.assertEqual(error.exception.code, 'auto_login_credentials_missing')
            self.assertEqual(worker.auth_attempts, 0)

    async def test_cooldown_survives_restart_and_blocks_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            with patch.object(DealerSocketLogin, 'run', new_callable=AsyncMock,
                              side_effect=LoginRequired('auto_login_timeout', 'Retry later')) as run:
                with self.assertRaises(LoginRequired):
                    await worker._automatic_login()
                restarted = DealerSocketSession(config(folder))
                with self.assertRaises(LoginRequired) as error:
                    await restarted._automatic_login()
                self.assertEqual(error.exception.code, 'auto_login_cooldown')
                self.assertEqual(run.await_count, 1)
                self.assertEqual(restarted.auth_attempts, 1)

    async def test_seven_attempts_stop_until_a_manual_login(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            with patch.object(DealerSocketLogin, 'run', new_callable=AsyncMock,
                              side_effect=LoginRequired('auto_login_timeout', 'Retry later')) as run:
                for _ in range(7):
                    worker.auth_next_attempt = 0
                    with self.assertRaises(LoginRequired):
                        await worker._automatic_login()
                restarted = DealerSocketSession(config(folder))
                with self.assertRaises(LoginRequired) as error:
                    await restarted._automatic_login()
                self.assertEqual(error.exception.code, 'auto_login_manual_required')
                self.assertEqual(run.await_count, 7)

    async def test_rejected_credentials_stop_repeated_automatic_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            with patch.object(DealerSocketLogin, 'run', new_callable=AsyncMock,
                              side_effect=LoginRequired('auto_login_manual_required', 'Manual login needed')):
                with self.assertRaises(LoginRequired):
                    await worker._automatic_login()
            self.assertEqual(DealerSocketSession(config(folder)).auth_attempts, 7)

    async def test_valid_saved_session_never_submits_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            worker.context = MagicMock()
            worker.page = MagicMock()
            worker.page.is_closed.return_value = False
            worker.page.goto = AsyncMock()
            async def probe():
                worker.state = 'connected'
                return True
            worker._probe = AsyncMock(side_effect=probe)
            worker._automatic_login = AsyncMock()
            await worker._run('check')
            worker._automatic_login.assert_not_awaited()

    async def test_session_check_invokes_recovery_then_clears_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            worker.context = MagicMock()
            worker.page = MagicMock()
            worker.page.is_closed.return_value = False
            worker.page.goto = AsyncMock()
            worker._probe = AsyncMock(return_value=False)
            async def recover():
                worker.auth_attempts = 1
                worker.state = 'connected'
            worker._automatic_login = AsyncMock(side_effect=recover)
            # Bound the pre-authentication render wait without a wall-clock delay.
            with patch('app.services.dealersocket_session.asyncio.sleep', new_callable=AsyncMock):
                loop = asyncio.get_running_loop()
                original = loop.time
                start = original()
                with patch.object(loop, 'time', side_effect=[start, start + 21, start + 21, start + 21]):
                    await worker._run('check')
            worker._automatic_login.assert_awaited_once()
            self.assertEqual(worker.auth_attempts, 0)

    async def test_untrusted_origin_does_not_fill_any_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            login = DealerSocketLogin(config(folder))
            page = MagicMock(url='https://evil.test/login')
            with self.assertRaises(LoginRequired) as error:
                await login.run(page, AsyncMock(return_value=False))
            self.assertEqual(error.exception.code, 'auto_login_untrusted_origin')
            page.locator.assert_not_called()


@unittest.skipUnless(os.getenv('RUN_CONNECTOR_BROWSER_TEST') == '1', 'Opt-in synthetic Chrome login test')
class LoginBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_totp_and_verified_crm_content(self):
        from playwright.async_api import async_playwright
        with tempfile.TemporaryDirectory() as folder:
            worker = DealerSocketSession(config(folder))
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(channel='chrome', headless=True)
                try:
                    page = await browser.new_page()
                    worker.page = page
                    async def fixture(route):
                        url = route.request.url
                        if url.endswith('/login'):
                            html = '''<form onsubmit="event.preventDefault();location.href='/otp'">
                            <input name="username"><input type="password"><button>Sign in</button></form>'''
                        elif url.endswith('/otp'):
                            html = '''<form onsubmit="event.preventDefault();if(document.querySelector('input').value.length===6)location.href='https://bb.dealersocket.com/home'">
                            <input id="otpCode" name="otpCode"><button>Verify</button></form>'''
                            html = html.replace("document.querySelector('input').value.length===6",
                                                "document.querySelector('input').value==='" + totp(worker.config.DEALERSOCKET_TOTP_SECRET, 61) + "'")
                        elif url.endswith('/frame'):
                            html = '<body>Daily Responsibilities Add Quick Up</body>'
                        else:
                            html = '''<md-select id="select_1"><md-option>Cranbourne Kia</md-option></md-select>
                            <iframe id="app-homepage" src="https://bb.dealersocket.com/frame"></iframe>'''
                        await route.fulfill(status=200, content_type='text/html', body=html)
                    await page.route('**/*', fixture)
                    await page.goto('https://na.login.solera.com/login')
                    with patch('app.services.dealersocket_auth.time.time', return_value=61):
                        await DealerSocketLogin(worker.config).run(page, worker._probe)
                    self.assertEqual(worker.state, 'connected')
                    self.assertEqual(worker.verified, {'cranbourne_kia'})
                    # Explicit rejection must stop rather than retry a code/password.
                    await page.goto('https://na.login.solera.com/login')
                    await page.locator('body').evaluate("body => body.innerHTML='<p>Incorrect password</p>'")
                    with self.assertRaises(LoginRequired) as error:
                        await DealerSocketLogin(worker.config).run(page, worker._probe)
                    self.assertEqual(error.exception.code, 'auto_login_manual_required')
                    await page.locator('body').evaluate("body => body.innerHTML='<p>Verify you are human</p>'")
                    with self.assertRaises(LoginRequired) as error:
                        await DealerSocketLogin(worker.config).run(page, worker._probe)
                    self.assertEqual(error.exception.code, 'auto_login_manual_required')
                finally:
                    await browser.close()
