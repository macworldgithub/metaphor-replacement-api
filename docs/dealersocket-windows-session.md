# DealerSocket Windows session connector

This phase connects the portal to a dedicated Chrome profile on the Windows automation PC. An administrator completes normal DealerSocket login and any MFA in that browser. Future checks reuse that profile while DealerSocket accepts its authentication. No fixed session lifetime is promised. Expired/revoked authentication requires another interactive login.

The connector reads authentication and explicit rooftop-label evidence only. It does not ingest leads, enroll journeys, send SMS or write CRM activities. The two reference repositories are not runtime dependencies and are not modified.

## Windows setup

Use installed Google Chrome and Python 3.11 or newer. Install this project's requirements in your chosen environment. A virtual environment is recommended:

```powershell
cd "D:\Booran Metador Replacement\metaphor-replacement-api"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/configure_session.py --enable-login
.\.venv\Scripts\python.exe run_connector.py
```

If Python is not on PATH, use its absolute executable path to create the environment. On the current machine Python is installed at `C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe`.

`configure_session.py` writes a matching random connector key into this project's `.env` and the sibling Nest backend's `.env`. It preserves unrelated settings, never prints the key, reuses existing valid matching keys and stops if the two existing keys differ. `--enable-login` permits the admin action that opens a visible Chrome window. Run configuration only on the intended automation PC. Keep `.env` and `.private/` private; they are ignored by Git. Restrict the Windows folder's access to the operator/service identity.

The connector defaults to `127.0.0.1:8000`. `run_connector.py` deliberately disables reload and uses one worker and the Windows subprocess-capable event loop. Do not use the generic development runner with automatic reload for the browser worker. The startup key validation fails closed before serving an unconfigured connector.

Restart the Nest backend after configuration. Its `DEALERSOCKET_CONNECTOR_URL` defaults to `http://127.0.0.1:8000`. Start the existing frontend as usual. Log in as Group Admin / Super Admin, then open **Settings → DealerSocket**:

1. Choose **Open login on automation PC**.
2. Complete login/MFA in the dedicated Chrome window on that Windows PC.
3. Wait for **Connected**. Normal checks allow 20 seconds for the CRM to render; interactive login allows five minutes after navigation.
4. Review the rooftop evidence for Cranbourne Kia, Cranbourne MG and Geely Cranbourne. Missing explicit labels remain unverified; connection success does not imply all-rooftop coverage.
5. Use **Check connection** to reopen a closed automation browser using its saved profile.

The dedicated profile is `.private/dealersocket-chrome`, resolved against this project directory. It starts separately from your normal Chrome profile. Existing extension tokens and normal-browser login are not automatically imported. The same authenticated session may access all three rooftops if that account has the required permissions.

## Operation and recovery

The frontend polls cached status every five seconds. Once a browser is open, the connector checks every 30 seconds by navigating to the CRM homepage and verifying rendered CRM content. It does not open a browser simply because a page polls status. A status older than 90 seconds is not presented as connected. During an active lead read, homepage checks are intentionally suspended: status reports `connecting` with operation `read`, shown as **Lead read in progress**, instead of a stale disconnect. This does not refresh verification timestamps or rooftop evidence. Genuine login/error states still take priority. Check/login controls stay disabled while reading; normal periodic checks resume afterwards. The local OS profile lock prevents multiple connector processes from opening that profile. Do not run other scrapers against it.

When Chrome closes, status becomes disconnected at the next check; choose Check connection to reopen. If the identity provider or a password/OTP overlay appears, status becomes login required. Complete authentication through Open login. Passwords and MFA codes are entered into DealerSocket, never the portal. A browser/profile failure shows a sanitized error and releases resources. Logout is not performed when the worker shuts down; browser profile persistence is preserved.

For Windows Task Scheduler, use your Python executable as the program and the absolute `run_connector.py` path as the argument. Choose **Run only when user is logged on** so interactive login can appear on that desktop. Keep a single task instance. Automatic task registration is not performed by this implementation.

If Nest runs on a different server, its loopback URL does not reach this Windows PC. Provide an authenticated HTTPS connection to the Windows connector; do not expose its plain HTTP port publicly. The current runner binds loopback, so remote deployment needs a separately configured HTTPS proxy/tunnel.

## API boundaries

Python exposes `GET /dealersocket/status`, `GET /dealersocket/rooftops`, `POST /dealersocket/check`, and `POST /dealersocket/login/start`, protected by `X-Connector-Key`. The key must have at least 32 characters. POST endpoints return 202 immediately; poll status for completion.

Nest exposes Group-Admin-only `GET /api/integrations/dealersocket/status`, `POST /api/integrations/dealersocket/check`, and `POST /api/integrations/dealersocket/login/start`. Actions write administrative audit records. The frontend uses the Next proxy, its existing HTTP-only portal session, and same-origin checks. Both frontend proxy and Nest whitelist response fields. Cookies, browser storage and the connector key are not returned to the browser.

`sessionExpiresAt` stays null because no authenticated server-side expiry has been established. Rooftop labels are evidence only, not an exhaustive access probe. There is no bearer-token extraction or claim that MFA can be skipped. Optional automatic recovery completes the normal password and TOTP challenge, as described below.

## Automatic password and TOTP login

In Settings → DealerSocket connection, **Log in automatically · Show Chrome** calls the Group-Admin-only `POST /api/integrations/dealersocket/login/automatic/start` route. It opens visible Chrome on the connector machine and uses the saved session first, then completes password and TOTP if required. **Open manual login** continues to call `/login/start` and waits for a person. The two actions are deliberately separate. Visible automatic login requires both interactive login and automatic login to be enabled. It uses the same cooldown and seven-attempt limit as background recovery; it never resets a cooldown or forces logout of an accepted session. The public operation remains `login` while either visible mode is running.

The connector reuses its saved Chrome profile first. Set these values in this connector's ignored `.env` (never the frontend):

```dotenv
DEALERSOCKET_AUTO_LOGIN_ENABLED=true
DEALERSOCKET_USERNAME=your_account_email
DEALERSOCKET_PASSWORD=your_account_password
DEALERSOCKET_TOTP_SECRET=your_base32_authenticator_secret
DEALERSOCKET_AUTH_COOLDOWN_SECONDS=900
```

The configured authenticator uses SHA1, six digits, and a 30-second period. The server clock must be accurate. Codes are generated only for a detected verification field; when fewer than five seconds remain, the worker waits for the next period. No code, secret, password, page content or raw authentication exception is returned to the portal or logged. Settings also hide credentials from their normal representation. Restrict `.env` and `.private` access to the account running the service, and never commit them or enable Playwright tracing/screenshot capture during real login.

On an expired session, a session check attempts the normal username/password form and then the authenticator form. It only fills top-level fields on exact HTTPS hosts in `DEALERSOCKET_AUTH_HOSTS` (defaults: `bb.dealersocket.com,sso.dealersocket.com,na.login.solera.com`). An unexpected redirect requires manual review; do not add an unknown host just to make login proceed. Unsupported forms, CAPTCHA, rejected credentials/codes and additional challenges require manual login. No password guessing, recovery codes or CAPTCHA solving is implemented.

Retry state is atomically saved in `.private/dealersocket-auth-recovery.json`, outside the Chrome profile. The default cooldown is 15 minutes, with a maximum of seven attempts across connector restarts. Explicit rejection or an untrusted origin stops automatic attempts immediately. A successful verified login clears the attempt counter. Use the portal's **Open login on automation PC** for manual recovery; this does not auto-fill credentials. If the state file cannot be read or is corrupt, automatic attempts stop rather than resetting limits. Missing credentials report `auto_login_credentials_missing` without consuming an attempt. After updating `.env`, restart `run_connector.py` to load the new settings.

The portal displays sanitized recovery messages through the existing connection status. Codes include `auto_login_credentials_missing`, `auto_login_configuration`, `auto_login_cooldown`, `auto_login_manual_required` and `auto_login_untrusted_origin`. Once authentication is verified, the enabled scheduler retries a read that previously failed because of login/session failure, without waiting another hour. That read remains read-only and preserves the previous snapshot until a new one completes. Automatic authentication never enables SMS or CRM writeback.

Credentials supplied for this workspace have been stored only in the ignored connector `.env`. The account password still needs to be supplied locally before live recovery can be verified. Secrets exposed through chat should be rotated before production. Offline tests use public RFC vectors and synthetic credentials, never the configured live authenticator secret.

## Verification

```powershell
python -m unittest tests.test_dealersocket_session -v
python -m unittest tests.test_dealersocket_auth -v
$env:RUN_CONNECTOR_BROWSER_TEST='1'
python -m unittest tests.test_dealersocket_browser -v
python -m unittest tests.test_dealersocket_auth.LoginBrowserTests -v
python -m unittest tests.test_dealersocket_runner -v
Remove-Item Env:RUN_CONNECTOR_BROWSER_TEST
```

The opt-in Chrome test intercepts every request with synthetic pages. It verifies real browser selectors, persistent-cookie survival after restart and rejection of an OTP overlay without contacting DealerSocket. Run the backend `test/dealersocket-session.e2e-spec.ts` suite and frontend `tests/dealersocket.test.mjs` as well. Real login, account permissions and long-duration session validity still require the operator's live acceptance.
