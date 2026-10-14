# DealerSocket: reuse signed-in Google Chrome

Updated version: `download_dealersocket_chrome.py` now scrapes individual records
to CSV across six dealership sites and eight categories. Follow
`dealersocket_csv_sync/README.md` for current setup, commands, outputs and limitations.
The report-export instructions below describe the earlier version.

Use `download_dealersocket_chrome.py` for the Chrome extension connection.
The previous `download_dealersocket_opportunities.py` uses direct CDP and is not
the extension-based version. Merely changing its browser executable cannot
attach it to an ordinary already-running Chrome session.

## Setup in your backend folder

1. Save the new Python script beside your `.env`.
2. Keep the signed-in DealerSocket Opportunities tab open in Google Chrome.
3. Install the Playwright extension in that same Chrome profile if needed:
   https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm
4. In that profile, click the extension icon and copy its current token into
   your local `.env`. Do not use a token from an Edge profile.
5. Install Node.js LTS if `npx --version` does not work.
6. Install Python dependencies in your activated virtual environment:

```powershell
python -m pip install "mcp>=1.28,<2" python-dotenv
```

The script uses the v1 MCP SDK API and pins the MCP server to 0.0.81. The first
invocation of npx may download that server package. It runs locally; no AI API
key or model is needed. Only the fixed automation code in this file is run.

## Relevant .env settings

```dotenv
PLAYWRIGHT_MCP_EXTENSION_TOKEN="<current token from your signed-in Chrome profile>"
DEALERSOCKET_OUTPUT_DIR="D:/Hammad/metaphor-replacement-api/downloads"
```

`PLAYWRIGHT_CDP_ENDPOINT` is not used. Username/password are not used by this
script; authentication comes from the existing Chrome session. Operating-system
environment variables take precedence over `.env`; remove an old value with
`Remove-Item Env:PLAYWRIGHT_MCP_EXTENSION_TOKEN -ErrorAction SilentlyContinue`
before running if you previously set a different one in PowerShell.

An optional `PLAYWRIGHT_MCP_PROFILE_DIR_NAME` can select the Chrome profile
directory (for example `Default` or `Profile 2`, not the visible profile name).
Get that directory name from Profile Path at `chrome://version`.

## Check the connection

```powershell
python download_dealersocket_chrome.py --check
```

Select/share the DealerSocket tab if the extension asks. Current extension
versions isolate each client's tabs into its own group. If another client owns
the tab, disconnect that client via the extension status page or share a
DealerSocket tab with this client. This is extension tab access, not DealerSocket
2FA. DealerSocket can still require login again when its session expires.

The check refuses Edge, requires the CRM queue selector and verifies access to
the three saved queues. It neither exports data nor fills login forms.

## Export attempt

```powershell
python download_dealersocket_chrome.py --download
```

Only these saved UNASSIGNED queues are selected:

- Cranbourne Kia Internet Leads Unassigned
- Cranbourne MG Internet Leads Unassigned
- Geely Internet Leads - UNASSIGNED

The code opens Event Details (With Notes), looks for a visible Excel export
control, captures the resulting download, preserves its .xls/.xlsx extension,
and checks its workbook container signature. It does not pretend an HTML page
or CSV is an XLSX file. It restores the queue and closes only the report tab it
opened. Failure produces a nonzero exit status; it never claims an incomplete
run is a complete sync.

## Verification and remaining limits

Verified live in this chat: the existing browser is Google Chrome, DealerSocket
is authenticated and the three queue names are present, without another login.
The report popup opened, but it exposed only a Print Report control and no
Excel export control during inspection. Consequently the Excel path remains
unverified, and this version is a connection fix plus an explicit export attempt,
not a finished ingestion pipeline. The standalone Python MCP client could not
be executed in this workspace because its `mcp` dependency is not installed;
Python syntax and the JavaScript preflight were checked separately.

No field completeness, freshness or source coverage is established by a valid
workbook signature. Native export discovery (or a verified record scraper),
pagination, real field mapping, assigned-lead/source coverage, incremental
tracking and backend import remain outstanding. No SMS, suppression decision,
CRM write-back or schedule is implemented here.

References:

- https://github.com/microsoft/playwright/blob/main/packages/extension/README.md
- https://github.com/microsoft/playwright-mcp
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/client.md
