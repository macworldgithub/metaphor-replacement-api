"""Normal password/TOTP login. Never logs credentials, codes, or raw page content."""
import asyncio
import base64
import hashlib
import hmac
import re
import struct
import time
from urllib.parse import urlparse


class LoginRequired(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def totp(secret, timestamp=None):
    """RFC 6238: SHA1, six digits, 30 seconds. No secret-bearing exceptions."""
    try:
        value = re.sub(r"\s+", "", secret).upper()
        if not re.fullmatch(r"[A-Z2-7]+={0,6}", value):
            raise ValueError()
        key = base64.b32decode(value + "=" * (-len(value) % 8))
        if len(key) < 10:
            raise ValueError()
        counter = int(time.time() if timestamp is None else timestamp) // 30
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 15
        number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
        return f"{number % 1000000:06d}"
    except Exception:
        raise LoginRequired("auto_login_configuration", "Authenticator configuration is invalid.") from None


class DealerSocketLogin:
    def __init__(self, config):
        self.config = config
        self.hosts = {host.strip().lower() for host in config.DEALERSOCKET_AUTH_HOSTS.split(',') if host.strip()}

    def trusted(self, url):
        parsed = urlparse(url)
        return parsed.scheme == 'https' and parsed.hostname in self.hosts and not parsed.username and not parsed.password

    def validate(self):
        if (not self.config.DEALERSOCKET_USERNAME or not self.config.DEALERSOCKET_PASSWORD
                or not self.config.DEALERSOCKET_TOTP_SECRET
                or self.config.DEALERSOCKET_USERNAME.startswith('your_')
                or self.config.DEALERSOCKET_PASSWORD.startswith('your_')):
            raise LoginRequired('auto_login_credentials_missing', 'Automatic login needs a configured username, password and authenticator secret.')
        totp(self.config.DEALERSOCKET_TOTP_SECRET)

    async def field(self, page, selector):
        candidates = page.locator(selector)
        visible = []
        for index in range(await candidates.count()):
            item = candidates.nth(index)
            if await item.is_visible() and await item.is_enabled():
                visible.append(item)
        if len(visible) > 1:
            raise LoginRequired('auto_login_manual_required', 'Multiple login fields detected. Complete login manually.')
        return visible[0] if visible else None

    async def submit(self, page, field):
        if not self.trusted(page.url):
            raise LoginRequired('auto_login_untrusted_origin', 'Unexpected login provider. Complete login manually.')
        button = page.get_by_role('button', name=re.compile(r'^(sign in|log in|login|next|continue|verify|verify code|submit)$', re.I))
        for index in range(await button.count()):
            item = button.nth(index)
            if await item.is_visible() and await item.is_enabled():
                await item.click(timeout=5000)
                return
        await field.press('Enter', timeout=5000)

    async def run(self, page, probe):
        username = self.config.DEALERSOCKET_USERNAME
        password = self.config.DEALERSOCKET_PASSWORD
        secret = self.config.DEALERSOCKET_TOTP_SECRET
        self.validate()
        submitted = set()
        deadline = asyncio.get_running_loop().time() + 90
        while asyncio.get_running_loop().time() < deadline:
            if await probe():
                return
            if not self.trusted(page.url):
                raise LoginRequired('auto_login_untrusted_origin', 'Unexpected login provider. Complete login manually.')
            # No captcha solving, recovery-code use, or retrying rejected credentials.
            text = await page.locator('body').inner_text(timeout=5000)
            if re.search(r'captcha|verify you are human|account (?:is )?locked|too many attempts|incorrect password|invalid (?:password|credentials|code)|code (?:is )?(?:incorrect|invalid)|access denied', text, re.I):
                raise LoginRequired('auto_login_manual_required', 'Login was rejected or additional verification is required. Complete login manually.')
            otp = await self.field(page, 'input[autocomplete="one-time-code"], input[name="otp"], input[name="otpCode"], input[name="code"], input[name="verificationCode"], input[name="totp"], input#otp, input#code, input#verificationCode, input#otpCode, input#authenticatorCode')
            password_field = await self.field(page, 'input[type="password"]')
            user = await self.field(page, 'input[name="username"], input[name="email"], input[type="email"], input#username, input#signInName')
            if otp is not None and 'otp' not in submitted:
                remaining = 30 - time.time() % 30
                if remaining < 5:
                    await asyncio.sleep(remaining + .2)
                if not self.trusted(page.url):
                    raise LoginRequired('auto_login_untrusted_origin', 'Login origin changed. Complete login manually.')
                await otp.fill(totp(secret), timeout=5000)
                submitted.add('otp')
                await self.submit(page, otp)
            elif password_field is not None and 'password' not in submitted:
                if user is not None:
                    await user.fill(username, timeout=5000)
                    submitted.add('username')
                await password_field.fill(password, timeout=5000)
                submitted.add('password')
                await self.submit(page, password_field)
            elif user is not None and 'username' not in submitted and 'password' not in submitted:
                await user.fill(username, timeout=5000)
                submitted.add('username')
                await self.submit(page, user)
            await asyncio.sleep(1)
        raise LoginRequired('auto_login_manual_required', 'Automatic login did not complete. Check credentials or complete login manually.')
