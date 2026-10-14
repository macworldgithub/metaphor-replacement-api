# DealerSocket opportunity downloader

Legacy version: use the updated `download_dealersocket_chrome.py` and
`dealersocket_csv_sync/README.md` for the Chrome extension CSV workflow.
The CDP-based script described below is not required for that workflow.

`download_dealersocket_opportunities.py` uses Playwright to reuse an already-authenticated DealerSocket browser session, select the three Cranbourne opportunity queues, open the **Event Details (With Notes)** report, and save Excel exports under `outputs/`.

The script intentionally does not contain the Playwright token or DealerSocket credentials. Set them only in the process environment supplied by the Playwright extension:

```powershell
$env:PLAYWRIGHT_CDP_ENDPOINT = "<CDP endpoint exposed by the Playwright extension>"
$env:PLAYWRIGHT_MCP_EXTENSION_TOKEN = "<your extension token>"
python .\download_dealersocket_opportunities.py
```

Optional output location:

```powershell
$env:DEALERSOCKET_OUTPUT_DIR = ".\outputs"
```

The current implementation targets these saved DealerSocket queues:

- Cranbourne Kia Internet Leads Unassigned
- Cranbourne MG Internet Leads Unassigned
- Geely Internet Leads - UNASSIGNED

The session must already be signed in and have access to the queues and report. The script only disconnects its Playwright client; it does not intentionally close the user's browser.
