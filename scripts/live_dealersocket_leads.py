"""Read-only live worker: fresh terminal snapshots using the running connector.

python scripts/live_dealersocket_leads.py
python scripts/live_dealersocket_leads.py --watch --interval 30
No portal POSTs, enrollment, SMS, CRM writes, session exports or customer-data files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(base, key, path, body=None):
    request = Request(base + "/dealersocket/" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Connector-Key":key,"Accept":"application/json","Content-Type":"application/json"},
        method="POST" if body is not None else "GET")
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError({401:"Connector key was rejected.",403:"Connector access denied.",
            409:"Browser busy. Wait for login/check/another snapshot to finish, then retry.",
            503:"Connector is not configured."}.get(error.code, f"Connector HTTP {error.code}.")) from None
    except URLError:
        raise RuntimeError("Cannot reach connector. Start run_connector.py on the automation PC.") from None
    except (ValueError, TimeoutError):
        raise RuntimeError("Connector returned an invalid response or timed out. Retry after checking its status.") from None


def fetch_snapshot(base, key, options, report_progress=False):
    job = request_json(base, key, "leads/read", options)
    job_id = job.get("jobId")
    deadline = time.monotonic() + 630
    last_progress = None
    while job.get("state") == "running":
        if time.monotonic() > deadline:
            raise RuntimeError("Lead read timed out. Check connector status; a result is not certified.")
        time.sleep(1)
        job = request_json(base, key, "leads/status")
        progress = job.get("progress")
        if report_progress and progress and progress != last_progress:
            print("Read progress: " + clean(progress.get("stage"),80), file=sys.stderr, flush=True)
            last_progress = progress
        if job.get("jobId") != job_id:
            raise RuntimeError("Snapshot was replaced by another reader. Retry with one worker.")
    if job.get("state") not in ("complete", "partial"):
        raise RuntimeError("Login required on the automation PC." if job.get("code")=="login_required"
                           else "Lead read failed: " + str(job.get("code") or "unknown"))
    return job


def clean(value, limit=100):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value or "—"))[:limit]


def record_hash(row):
    fields = ("rooftop","customer_name","customer_phone","customer_email","vehicle_of_interest",
              "assigned_salesperson","opportunity_status","lead_source","received_timestamp","last_updated_timestamp","detail_extracted")
    return hashlib.sha256(json.dumps({k:row.get(k) for k in fields},sort_keys=True).encode()).hexdigest()


def display(snapshot, previous, summary_only=False):
    current = {row["record_key"]:record_hash(row) for row in snapshot["records"]}
    print(f"\nFresh snapshot: {snapshot['fetchedAt']} | {snapshot['state']} | {snapshot.get('totalDurationSeconds', snapshot['durationSeconds'])}s total | {len(current)} records", flush=True)
    if previous is None:
        print("First snapshot is the observation baseline; it does not establish new-lead eligibility.")
    else:
        print(f"Newly observed: {len(current.keys()-previous.keys())}; changed: {sum(k in previous and previous[k]!=v for k,v in current.items())}; no longer visible: {len(previous.keys()-current.keys())} (not proof of deletion).")
    for roof in snapshot["rooftops"]:
        print(f"\n{roof['name']}: {roof['recordCount']} displayed / {roof['matchedCandidateCount']} matched candidates | {roof['coverage']}")
        if not summary_only:
            for row in snapshot["records"]:
                if row["rooftop"] != roof["name"]:
                    continue
                print(f"  Opp {clean(row['opportunity_id'])} | Customer ID {clean(row['customer_id'])} | {clean(row['customer_name'])}")
                print(f"    Mobile: {clean(row.get('customer_phone'))} | Email: {clean(row.get('customer_email'))}")
                print(f"    Vehicle: {clean(row.get('vehicle_of_interest'))} | Owner: {clean(row.get('assigned_salesperson'))}")
                print(f"    Source: {clean(row.get('lead_source'))} | Status: {clean(row.get('opportunity_status'))} | Source timestamp: {clean(row.get('received_timestamp'))}")
                if row.get("missing_fields") or row.get("validation_warnings"):
                    print(f"    Warnings: {clean(row.get('validation_warnings'),300)} | Missing: {clean(row.get('missing_fields'),300)}")
    print(f"Selected queues: {len(snapshot['coverage'])}; errors: {len(snapshot['errors'])}; review: {len(snapshot['review'])}; record limit reached: {snapshot['limited']}")
    print(snapshot["coverageNote"], flush=True)
    return current


def main():
    os.chdir(ROOT)
    from app.config import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=30, help="Seconds AFTER each completed read (minimum 10)")
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--detail-limit", type=int, default=50, help="Max fresh detail reads per snapshot; 0 is list-only")
    parser.add_argument("--cycles", type=int, default=0, help="Watch iterations; 0 means until Ctrl+C")
    parser.add_argument("--json", action="store_true", help="Print complete snapshot JSON including customer data")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--connector-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.interval < 10 or not 1 <= args.max_records <= 1000 or not 0 <= args.detail_limit <= 200 or args.cycles < 0:
        parser.error("Use interval >=10, max-records 1–1000, detail-limit 0–200 and cycles >=0.")
    url = urlparse(args.connector_url)
    if url.username or url.password or url.query or url.fragment or url.path not in ("", "/") or not (
        url.scheme == "https" or url.scheme == "http" and url.hostname in ("127.0.0.1","localhost","::1")):
        parser.error("Use a loopback HTTP or trusted HTTPS connector origin, without credentials/path/query.")
    if len(settings.DEALERSOCKET_CONNECTOR_KEY) < 32:
        parser.error("Configure DEALERSOCKET_CONNECTOR_KEY first; do not paste the key into command arguments.")
    previous, cycle, code = None, 0, 0
    while True:
        try:
            snapshot = fetch_snapshot(args.connector_url.rstrip('/'), settings.DEALERSOCKET_CONNECTOR_KEY,
                                      {"maxRecords":args.max_records,"detailLimit":args.detail_limit},report_progress=not args.json)
            if args.json:
                print(json.dumps(snapshot,ensure_ascii=False),flush=True)
            else:
                previous = display(snapshot, previous, args.summary_only)
            code = 0 if snapshot["state"] == "complete" else 2
        except RuntimeError as error:
            print(str(error), file=sys.stderr, flush=True)
            code = 1
        cycle += 1
        if not args.watch or args.cycles and cycle >= args.cycles:
            return code
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nWorker stopped. An accepted connector read may finish; no further refreshes are scheduled.")
        raise SystemExit(130)
