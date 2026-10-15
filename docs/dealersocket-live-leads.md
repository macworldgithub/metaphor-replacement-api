# Read only live leads worker

Use the connector-based worker to show fresh DealerSocket records locally. It shares the dedicated Chrome session that was verified for login/restart. It does not start the older extension-based ingestion worker, POST records into Nest, enroll journeys, send SMS or create/update CRM records.

## Start

Restart the connector after updating code, from the Python project directory:

```powershell
cd "D:\Booran Metador Replacement\metaphor-replacement-api"
python run_connector.py
```

Use the same configured Python executable/virtual environment as the session connector. If Python is not on PATH, the installed executable on this machine is `C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe`.

In a second terminal, fetch once:

```powershell
python scripts/live-worker.py
```

Continuously read fresh snapshots:

```powershell
python scripts/live-worker.py --watch --interval 30
```

The interval starts after the previous complete read attempt, so actual refresh period is read duration plus 30 seconds. A five-minute scrape does not become a 30-second live feed. Duration and source observation timestamps are included. There is no scheduler or background watch left running when this command exits. Ctrl+C stops future refreshes; a previously accepted connector job may finish its one read.

Useful bounded checks:

```powershell
python scripts/live-worker.py --max-records 10 --detail-limit 3 --summary-only
python scripts/live-worker.py --watch --interval 30 --cycles 2 --detail-limit 0
python scripts/live-worker.py --json
```

`--detail-limit 0` shows fresh queue rows only; mobiles, emails and source timestamps that require detail reads remain missing. Defaults are 200 output records and 50 fresh detail reads. Caps are explicit and produce partial results when applicable. Three consecutive detail failures stop further detail attempts, retaining flagged list-only rows. No old detail cache or completed queue checkpoint is used as current data.

## Meaning of results

Only explicitly named Cranbourne Kia, Cranbourne MG and Geely-related Internet Traffic / Unsold Traffic saved filters are selected. Other dealership catalogs may be read to find those queues, but unrelated customer details are not opened. The short Geely queue name is only a hint: record rooftop identity still requires explicit CRM franchise labels or an unambiguous site/franchise mapping learned during this read.

Each row includes stable opportunity/customer identity, rooftop, available contact/vehicle/source/owner fields, source timestamps and missing-field warnings. Detail selection verifies site/filter/opportunity/customer identity. Restricted details are excluded. Ambiguous mappings are listed for review instead of guessed.

`complete` means selected-filter extraction completed with details and observations for all three roofs. It does not mean all CRM leads are accessible. Saved filters can exclude assigned leads, sources or dates. `partial` may mean missing rooftop observations, filtered coverage, record/detail limits, failed reads, unresolved identities or failed UI restoration. Header/summary counts do not establish new-lead consent or eligibility.

The first successful worker snapshot is an in-memory observation baseline. Later snapshots show newly observed and changed records. Newly observed does not prove that an opportunity was created/reopened after cutover. Disappearance does not prove deletion or closure. All records remain marked `not_verified_no_sms_authorized`.

The connector now also saves its last completed snapshot privately to `.private/dealersocket-leads/latest.json` (configurable), so results survive restart. The CLI itself only displays output; `--json` contains full returned customer data, so treat redirected output accordingly. Do not run watch mode alongside the enabled hourly connector scheduler, or another operator/scraper against the same automation tab while reading. See `dealersocket-server-deployment.md` for unattended hourly sync and private storage setup.

## Connector APIs and locking

`POST /dealersocket/leads/read` accepts only `{ "maxRecords": 200, "detailLimit": 50 }`, returns 202 with a job ID and runs one bounded read. `GET /dealersocket/leads/status` returns that job/result, including fresh coverage, row observation times, duration, errors and review flags. Both use the existing private `X-Connector-Key` authentication and no-store responses. These are internal connector APIs, not public browser endpoints.

The worker loads the key from this project's `.env` without printing it. It accepts only loopback HTTP or explicitly configured HTTPS origins and refuses redirects. Another active login/check/lead read returns 409. Lead reads block competing session actions and scheduled homepage navigation. The job has a ten-minute timeout and is cancelled during connector shutdown. No job queue accumulates behind a slow browser.

`outputs/dealersocket_csv_sync/dealersocket_live_worker.py` is an older, different ingestion worker. It can POST to the backend and is not the read-only command documented here. Do not use it for this phase.

## Tests

```powershell
python -m unittest tests.test_dealersocket_leads tests.test_dealersocket_session -v
```

Tests cover three-roof scoping, deduplication, ignoring unrelated/sold/service records, fresh repeated snapshots, missing/limited details, restricted records, sanitized errors, API authentication/options validation and serialization with login/session checks. A live read must still verify the current DealerSocket layout and accessible queues.
