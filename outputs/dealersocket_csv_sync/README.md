# DealerSocket scoped CSV scraper — version 1.3.0

The default is now the original three-rooftop project: **Cranbourne Kia,
Cranbourne MG and Geely Cranbourne**. It does not repeat the earlier eight-category
history scan.

## Run from the backend project directory

Keep the authenticated DealerSocket Google Chrome profile and its Playwright
extension open. The standalone Python file embeds the browser adapter; replacing
that one Python file is enough. No CDP port or Edge browser is used.

Install dependencies if needed:

```powershell
python -m pip install "mcp>=1.28,<2" python-dotenv
```

Node.js/npx must be on PATH. The local MCP server is pinned to
`@playwright/mcp@0.0.83`. Use the current Chrome extension token in your local
`.env`; never put a URL into the token value. Environment variables override
`.env`, so remove a stale terminal token if necessary:

```powershell
Remove-Item Env:PLAYWRIGHT_MCP_EXTENSION_TOKEN -ErrorAction SilentlyContinue
```

Start the fast scoped run, reusing any matching details from your old checkpoint:

```powershell
python .\outputs\dealersocket_csv_sync\download_dealersocket_chrome.py `
  --env-file .\.env `
  --output .\downloads `
  --seed-run .\downloads\run-20261014T063832107273Z
```

The old folder is read, never overwritten. New files go under
`downloads/cranbourne-<timestamp>/`. A message saying zero scoped records were
reused means the old broad scan had not captured these rooftops; it does not
mean there are no leads in DealerSocket.

For a fresh run, omit `--seed-run`. To resume a new scoped run, use
`--resume .\downloads\cranbourne-<timestamp>` instead. Passing an old broad
run to `--resume` safely treats it as a seed and creates new scoped output.
Do not run multiple scrapers against the same browser session.

To recheck Geely's exact queue even when the checkpoint says it is complete:

```powershell
.\.venv\Scripts\python.exe .\outputs\dealersocket_csv_sync\download_dealersocket_chrome.py `
  --env-file .\.env `
  --output .\downloads `
  --resume .\downloads\cranbourne-20261014T091316777956Z `
  --refresh-filter 10304:707
```

The refresh reads this queue again and refreshes its current records. Other completed
queues remain cached. Geely is limited to its named unassigned queue; the default
does not inspect generic assigned-sales or other dealers' lead queues. A completed
empty queue is a dated snapshot, so ordinary resume alone will not detect new rows.
Refresh is a manual snapshot operation, not a continuous portal synchronization worker.

## What makes this faster

1. Read the accessible dealership **filter catalogs only** to find candidate
   queues. Seeing another dealership name at this stage is not a detail scrape.
2. Select only explicitly Cranbourne Kia/MG or Geely-named Internet Traffic and
   Unsold Traffic queues. Other dealer and generic Internet Traffic queues are excluded.
3. Read each selected list once. Before opening any customer, require a sales
   record, a matching rooftop, and a status that is not sold/lost/closed/delivered.
4. Skip unrelated rooftops and QA/service records without opening them. Skip all
   QA, Service Opportunities, Sold Opportunities, Lost Opportunities, Revenue
   Radar and generic salesperson-history queues.
5. Deduplicate by source site, opportunity type, opportunity ID and customer ID;
   reuse already-extracted scoped details.
6. Use the identity-checked native card-selection binding in the current layout,
   with up to two bounded attempts and a 15-second detail wait. After
   three consecutive detail failures, stop and save rather than repeating
   failures over hundreds of records. `--fail-fast-after` changes that threshold.

On 14 October 2026, Geely queue 10304:707 was freshly verified with one record
explicitly labelled Geely Cranbourne, source dealership 10304 and franchise 21.
An earlier saved zero-row result was skipped by ordinary resume; an explicit
refresh corrects that stale snapshot. These IDs are evidence from that queue,
not a claim of access to all assigned Geely opportunities.

Browser extraction is still visible unattended Chrome automation, not headless.
Keep Chrome open and avoid manually changing the automation tab's filters.

Readiness and detail waits use 250 ms timer polling because animation-frame
polling can stall in hidden Chrome tabs. If the loaded list has no rendered
virtual cards, selection uses the existing list controller's changeTodo binding
after verifying the current site, queue, opportunity and customer IDs. The detail
view must still match both IDs before any fields are exported.

## Rooftop mapping and coverage

A DealerSocket source site selector is not necessarily a rooftop. The scraper
uses explicit CRM franchise labels such as Cranbourne Kia, Cranbourne MG and
Geely Cranbourne (including New/Used and reversed-location label variants).
It can learn a mapping from **source dealership ID + franchise ID** when that
pair has an explicit scoped label. Ambiguous brand-only labels are not guessed.

The previously observed short queue name "Geely Internet Leads" is only a hint
for selecting a queue. It does not establish that each record belongs to Geely
Cranbourne. Conflicting mappings or unresolved Cranbourne/Geely labels go into
`scope_review.csv`; their customer details are not opened.

Optional verified ID mappings can be supplied with `--rooftop-map`. Its JSON
is a list of objects with `dealership_id`, `franchise_id` and `rooftop`
(one of the three exact target names). Never invent IDs to make a queue pass.

This is deliberately a targeted saved-filter intake, not a promise of complete
DealerSocket coverage. Saved filters may be owner-, source-, date- or status-
restricted, including unassigned-only queues. New leads that exist outside
these accessible filters can be missed. Check the catalog and coverage before
using this as a production intake integration.

## Files saved automatically

```text
cranbourne-<timestamp>/
  cranbourne-kia.csv
  cranbourne-mg.csv
  geely-cranbourne.csv
  opportunities.csv
  rooftop_mappings.csv
  filter_catalog.csv
  filter_coverage.csv
  filter_memberships.csv
  scope_review.csv
  errors.csv
  error_history.csv
  run_summary.json
  checkpoint.json
```

The three rooftop CSVs and combined file contain only matched, non-closed sales
candidates, including flagged list-only fallbacks when detail extraction fails.
Empty files retain their column headers. **A header-only CSV is not evidence
that the rooftop has no leads.** The summary reports each rooftop's counts and
marks missing rooftop coverage for verification.

The standard fields include opportunity/customer IDs, canonical rooftop, name,
Australian mobile, email, vehicle, source, assigned salesperson and timestamp.
Additional fields preserve raw rooftop/phone/email, comments, source IDs,
restrictions, missing fields, source URL and extraction warnings. Missing values
remain blank, not invented.

Phones are normalized only when they match the Australian mobile pattern.
Landlines/invalid numbers remain blank in the normalized mobile field and are
flagged. Source timestamps are preserved. The current sales timestamp comes
from opportunity creation; its equivalence to lead-received/reopened time and
its source offset remain unverified.

Every row is marked `new_lead_eligibility=not_verified_no_sms_authorized`.
No hidden age cutoff is imposed. Being in these files does not prove the lead
is new/reopened after cutover, has consent, or is eligible for an SMS journey.
No message, customer update, note or appointment is created by the scraper.

CSV files use UTF-8 BOM and quoted fields. Import ID and phone columns as text
in Excel to preserve leading zeros and plus signs. Treat customer text as data;
do not execute formulas or links from imported cells.

Files/checkpoints are saved after each selected filter, every 10 detail reads,
and on Ctrl+C. Active failures are in `errors.csv`; successfully retried failures
move to `error_history.csv`. Snapshot origin distinguishes reused checkpoint
data from new live details.

## Optional commands

Export only what the old checkpoint already contains, without Chrome/network:

```powershell
python .\outputs\dealersocket_csv_sync\download_dealersocket_chrome.py `
  --export-only --seed-run .\downloads\run-20261014T063832107273Z `
  --output .\downloads
```

This creates new output with status `offline_export_only_coverage_not_verified`.
It cannot fetch missing leads.

`--diagnose` checks the Chrome connection. `--check` inventories candidate
filters without opening customer details. `--dealership` restricts source site
selectors, not canonical rooftop names. `--max-filters` and `--max-records`
are diagnostic limits and always mark a live export limited.

The old broad behavior is retained only behind `--all-rooftops`. Do not use
that switch for the three-rooftop task.

Exit 0 means the selected-filter extraction/export operation completed, not
full business-scope acceptance. Exit 2 means partial/limited coverage or missing
rooftop verification. Offline export success also exits 0 but explicitly marks
coverage unverified. Ctrl+C exits 130 and saves progress.

## Verification

Tests cover target-only detail calls among 500 unrelated rows, category/status
exclusions, site-scoped mappings, ambiguous/conflicting labels, separate CSVs,
deduplication, old-checkpoint reuse without overwrite, scoped resume and the
three-failure stop. Existing normalization, partial-export, filter-retry and
browser-fixture regressions are retained.

The complete live scoped export has not yet been verified. Local browser
fixtures are not proof that every live DealerSocket layout is supported.

## Continuous read-only portal sync

The CSV downloader is for dated snapshots. Do not use `--resume` as a live
sync loop because it intentionally skips queues already completed in that
snapshot. The live worker freshly reads the scoped queues on every poll,
fingerprints each opportunity, opens details only for new or changed records,
and sends the Section 6.2 payload to the Nest backend:

```powershell
python .\outputs\dealersocket_csv_sync\dealersocket_live_worker.py `
  --env-file .\.env `
  --state-dir .\downloads\live-sync `
  --seed-run .\downloads\cranbourne-20261014T091316777956Z `
  --interval 60 `
  --send-existing
```

The first launch above reconciles the current queues against the existing
89-record import. Existing opportunities are updated idempotently and any lead
that arrived after that CSV snapshot is added, so there is no cutover gap.
Every first-poll row is marked as backfill and cannot start an SMS journey.
After the baseline is saved, the same process continues sending only new or
changed records through the authenticated `POST /api/leads/ingest` endpoint.

If starting at an exact cutover time with no historical reconciliation, omit
`--send-existing`; the first poll then establishes a baseline and sends
nothing.

Set these values in the scraper `.env`:

```dotenv
DEALERSOCKET_SYNC_API_URL=https://www.cranbourne-backend.omnisuiteai.com/api/leads/ingest
LEAD_FORWARD_SECRET=<same value as the Nest backend>
```

The worker always sends `startJourney=false`; it has no switch that can
enable messaging. DealerSocket write-back is separately disabled in the
backend. Keep signed-in Chrome and the Playwright extension open, and do not
run the snapshot scraper and live worker against the extension at the same
time.

Worker state and continuously refreshed CSVs are saved in
`downloads/live-sync/`. The Automation Monitoring page reads the latest
worker heartbeat from the backend.
