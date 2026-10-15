# Always-on read-only DealerSocket sync

The Python connector owns one browser and one hourly scheduler. Nest and Next only read its snapshots or request a manual refresh. Do not add a second Nest cron, external live-worker watch process, or additional connector workers. A second process cannot share the same Chrome profile. Multiple backend/frontend instances may use the one connector.

## Configuration

Set these in the connector `.env` (matching values are shown in `.env.example`):

```dotenv
DEALERSOCKET_LEAD_SYNC_ENABLED=true
DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS=3600
DEALERSOCKET_PROFILE_DIR=.private/dealersocket-chrome
DEALERSOCKET_LEAD_SNAPSHOT_PATH=.private/dealersocket-leads/latest.json
DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED=false
```

Set a private `DEALERSOCKET_CONNECTOR_KEY` of at least 32 random characters in both connector and Nest backend. On the same host, Nest uses `DEALERSOCKET_CONNECTOR_URL=http://127.0.0.1:8000`; Next uses the internal Nest `/api` base URL. Do not put the connector key in frontend/public variables. Keep port 8000 private. A separate host requires authenticated HTTPS through a private reverse proxy/tunnel because the backend rejects remote plain HTTP; the connector itself still binds loopback.

At cold start the first read is queued within about 30 seconds. A restored snapshot less than an hour old postpones the first read until it is due. While running, attempts are one hour apart, measured from each scheduled attempt (not wall-clock hour boundaries). Missed hours do not create catch-up jobs. A busy browser skips that attempt and tries at the next interval. The UI has no manual fetch button. The admin-only refresh API remains available for maintenance and does not change the scheduler's cadence.

Each attempt reads up to 1,000 scoped records and 200 customer details with a 10-minute read timeout. Current observed 91-record reads take about five minutes; larger/slow queues can be partial or time out. The last completed full or partial snapshot is saved atomically, including its original observation timestamps. A running/failed read shows that previous snapshot with a clear stale-data message. Login failure never marks old data as fresh. If the connector is unreachable, the UI displays a connection error; the persisted snapshot becomes available again when the connector starts.

The schedule and next attempt time appear in the lead screen, replacing the optional browser-based 30-second auto-refresh control. This scheduler does not send SMS, enroll journeys, ingest prospects into MongoDB, or write to DealerSocket. The existing portal dashboard/conversations continue using their existing data.

## Linux server with systemd

1. Deploy the **entire** Python project to `/opt/metaphor-replacement-api`, including `outputs/dealersocket_csv_sync/download_dealersocket_chrome.py` (normalization rules). Create a Python 3.12 virtual environment at `.venv` and install `requirements.txt`. Install Google Chrome stable and its platform dependencies; the connector launches Playwright using `channel="chrome"`.
2. Create a dedicated unprivileged `dealersocket` service user/group with home `/var/lib/dealersocket`. Give it read access to the project and `.env`, and ownership of `/var/lib/dealersocket`. Store credentials/session/cache outside publicly served directories. The supplied unit creates a private state directory and runs with `UMask=0077`. Set the absolute profile and snapshot paths shown in the unit in `.env` too, so maintenance/login uses those same paths.
3. Establish the DealerSocket session **on the server under that same service account and OS**. Use an authorized remote desktop/display session and temporarily enable interactive login. Start `run_connector.py`, then use the portal DealerSocket connection controls to open login and complete the normal login/MFA. Stop that connector before starting the service. Disable interactive login again for unattended headless operation. A Chrome profile copied from Windows is not a verified Linux login setup; browser storage encryption/account context can differ.
4. Review/copy `deploy/dealersocket-connector.service` into `/etc/systemd/system/`. Adjust the paths and service account if your layout differs. Run `systemctl daemon-reload` then `systemctl enable --now dealersocket-connector`. Check `systemctl status dealersocket-connector` and `journalctl -u dealersocket-connector`. Use your existing supervisors for Nest and Next, and rebuild/restart both after deploying this change.
5. Verify the portal shows **Server sync: every 60 minutes**, a next attempt time and a dated snapshot. Stop/restart the connector once to verify that the previous snapshot survives, and confirm a later scheduled attempt updates its timestamp without an open browser tab.

Normal scheduled checks use headless Chrome, so they do not require a graphical desktop. Human login/MFA does require an interactive display. If DealerSocket expires/revokes the session, the scheduler retries hourly and the portal reports login required while retaining old data. Server uptime does not guarantee an indefinitely authenticated DealerSocket session. On login-required, stop the service, start the connector under the same account/profile in an authorized desktop, complete login, then return to the service.

## Windows server

Use Python 3.12 and Google Chrome stable. Start with `python run_connector.py` from the project folder (the runner also normalizes its working directory). Configure the same `.env` variables above and keep the existing dedicated profile path. Use one Windows service supervisor, or a Task Scheduler task:

- Trigger: **At startup**. Action: the absolute Python executable, argument the absolute `run_connector.py` path, and **Start in** the project directory.
- Run as the dedicated Windows account that completed DealerSocket login. For unattended operation choose **Run whether user is logged on or not**, enter that account's credentials in Windows, and give it read access to the app and write access to its profile/cache directories.
- Disable the default task duration limit and battery/power conditions; configure restart on failure and **Do not start a new instance** when already running.
- Complete first login/MFA interactively as that same account; then scheduled reads use headless Chrome. Reauthentication requires temporarily stopping the task and opening the same connector/profile interactively. No duplicate connector process may be running.

Do not move a profile between OS accounts and assume its cookies will remain readable. Protect the `.private` folder and `.env` with Windows ACLs for that service account. A server/container deployment must preserve both profile and snapshot paths across updates/reboots; the unit's `/var/lib/dealersocket` is the Linux example. These files contain customer data/session material and should not be committed or served publicly.

## Deployment verification and limits

Offline tests cover cadence, busy skips, private persistence, restart loading, failed refresh retention, authorization of cached rows and schedule response whitelisting. The local Windows runtime is separately checked using the existing authorized session. The supplied Linux systemd unit and Windows startup-task instructions must be verified on your actual server; this task does not provision or claim to have deployed that server.
