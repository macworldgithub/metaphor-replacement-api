"""Continuously sync the three scoped DealerSocket rooftops into the portal.

The worker re-reads the selected queues on every poll.  It does not use the
downloader's ``--resume`` behaviour, because a resumed export intentionally
skips queues that were already completed.  DealerSocket is read-only: this
process never calls a DealerSocket API and never saves, sends or writes back.

By default the first poll establishes a baseline and sends nothing.  New or
changed opportunities found on later polls are normalised to the Section 6.2
payload and POSTed to the portal backend with SMS journey enrolment disabled.
Use ``--send-existing`` only for a deliberate, non-messaging backfill.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import download_dealersocket_chrome as scraper

WORKER_VERSION = "1.0.0"
ROOFTOP_IDS = {
    "Cranbourne Kia": "cranbourne_kia",
    "Cranbourne MG": "cranbourne_mg",
    "Geely Cranbourne": "geely_cranbourne",
}
LIST_FINGERPRINT_FIELDS = (
    "dealershipId", "franchiseId", "contactId", "salesOpportunityId",
    "fullName", "opportunityType", "eventStatus", "franchiseName",
    "condition", "year", "make", "model", "assignedTo",
    "activityDateTime", "description", "lastUpdate", "age",
)
DETAIL_FINGERPRINT_FIELDS = tuple(
    field for field in scraper.FIELDS
    if field not in {
        "scraped_at", "source_url", "timeline_load_status", "snapshot_origin",
        "selected_dealerships", "categories", "matched_filter_ids",
    }
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def fingerprint(value: dict, fields: tuple[str, ...]) -> str:
    canonical = {field: value.get(field) for field in fields}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def split_name(value: str) -> tuple[str, str]:
    parts = str(value or "").strip().split(maxsplit=1)
    return (parts[0] if parts else "Unknown", parts[1] if len(parts) > 1 else "")


def split_vehicle(value: str) -> dict:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return {}
    parts = text.split(" ")
    vehicle: dict = {}
    if parts and re.fullmatch(r"(?:19|20)\d{2}", parts[0]):
        vehicle["year"] = int(parts.pop(0))
    if parts:
        vehicle["make"] = parts.pop(0)
    if parts:
        vehicle["model"] = " ".join(parts)
    elif vehicle.get("make"):
        vehicle["model"] = vehicle.pop("make")
    return vehicle


def parse_received(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def portal_payload(row: dict, *, mode: str, is_new: bool,
                   allow_journey: bool = False) -> dict:
    rooftop = str(row.get("rooftop") or "")
    if rooftop not in ROOFTOP_IDS:
        raise ValueError(f"Unmapped rooftop: {rooftop or '[blank]'}")
    opportunity_id = str(row.get("opportunity_id") or "").strip()
    if not opportunity_id:
        raise ValueError("Opportunity ID is missing")
    first, last = split_name(str(row.get("customer_name") or ""))
    phone = str(row.get("customer_phone") or row.get("customer_phone_raw") or "").strip()
    if not phone:
        raise ValueError("Customer phone is missing")
    detail_hash = fingerprint(row, DETAIL_FINGERPRINT_FIELDS)
    payload = {
        "dealerSocketOpportunityId": opportunity_id,
        "rooftopId": ROOFTOP_IDS[rooftop],
        "customer": {"firstName": first, "mobile": phone},
        "vehicle": split_vehicle(str(row.get("vehicle_of_interest") or "")),
        "leadSource": str(row.get("lead_source") or "DealerSocket").strip() or "DealerSocket",
        "isNewLead": bool(is_new),
        "startJourney": bool(allow_journey and is_new),
        "syncMode": mode,
        "sourceFingerprint": detail_hash,
        "scrapedAt": parse_received(str(row.get("scraped_at") or "")) or utcnow(),
    }
    customer_id = str(row.get("customer_id") or "").strip()
    if customer_id:
        payload["dealerSocketCustomerId"] = customer_id
    if last:
        payload["customer"]["lastName"] = last
    email = str(row.get("customer_email") or "").strip()
    if email:
        payload["customer"]["email"] = email
    salesperson = str(row.get("assigned_salesperson") or "").strip()
    if salesperson:
        payload["salesperson"] = {"name": salesperson}
    received = parse_received(str(row.get("received_timestamp") or ""))
    if received:
        payload["receivedAt"] = received
    source_url = str(row.get("source_url") or "").strip()
    if source_url:
        payload["sourceUrl"] = source_url
    comments = str(row.get("customer_comments") or row.get("description") or "").strip()
    if comments:
        payload["vehicle"]["comments"] = comments
    return payload


def request_json(url: str, secret: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Lead-Forward-Token": secret,
            "User-Agent": f"dealersocket-live-worker/{WORKER_VERSION}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:1500]
        raise RuntimeError(f"Backend HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Backend unavailable: {error.reason}") from error


async def post_json(url: str, secret: str, payload: dict, timeout: float) -> dict:
    return await asyncio.to_thread(request_json, url, secret, payload, timeout)


def load_seed_mappings(path: str | None) -> tuple[dict, set[str]]:
    if not path:
        return {}, set()
    source = scraper.load_scope_seed(path)
    mappings, conflicts = scraper.build_rooftop_map(source.get("records", {}).values())
    for key, value in source.get("mappings", {}).items():
        if key not in conflicts and key not in mappings:
            mappings[key] = value
    return mappings, conflicts | set(source.get("mapping_conflicts", []))


def read_state(path: Path) -> dict:
    if not path.exists():
        return {
            "version": 1,
            "worker_version": WORKER_VERSION,
            "started_at": utcnow(),
            "baseline_completed_at": None,
            "records": {},
            "polls": 0,
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("records"), dict):
        raise ValueError("Unsupported or invalid live worker state file")
    return value


def export_current(state_dir: Path, state: dict) -> None:
    rows = [entry["row"] for entry in state["records"].values() if entry.get("row")]
    scraper.write_csv(state_dir / "current-opportunities.csv", scraper.FIELDS, rows)
    for rooftop in scraper.ROOFTOPS:
        scraper.write_csv(
            state_dir / (scraper.slug(rooftop) + ".csv"), scraper.FIELDS,
            [row for row in rows if row.get("rooftop") == rooftop],
        )


async def heartbeat(args, state: dict, metrics: dict) -> None:
    payload = {
        "workerId": args.worker_id,
        "workerVersion": WORKER_VERSION,
        "status": metrics["status"],
        "pollStartedAt": metrics["started_at"],
        "pollCompletedAt": metrics.get("completed_at"),
        "intervalSeconds": args.interval,
        "filtersRead": metrics.get("filters_read", 0),
        "recordsSeen": metrics.get("records_seen", 0),
        "recordsChanged": metrics.get("records_changed", 0),
        "recordsSent": metrics.get("records_sent", 0),
        "errors": metrics.get("errors", 0),
        "lastError": metrics.get("last_error"),
        "baselineCompletedAt": state.get("baseline_completed_at"),
        "readOnly": True,
    }
    try:
        await post_json(args.heartbeat_url, args.secret, payload, args.http_timeout)
    except Exception as error:
        print(f"Heartbeat failed: {error}", file=sys.stderr, flush=True)


async def discover_jobs(chrome, selected_sites: list[str] | None) -> tuple[list, dict, dict]:
    discovery = await chrome.call("discover")
    if discovery.get("keyword"):
        raise RuntimeError("Clear the DealerSocket keyword filter before live sync.")
    available = {item["name"]: item["id"] for item in discovery["sites"]}
    sites = selected_sites or list(available)
    unavailable = set(sites) - set(available)
    if unavailable:
        raise RuntimeError("Unavailable dealership selector: " + ", ".join(sorted(unavailable)))
    jobs = []
    for site in sites:
        metadata = await chrome.call("site", name=site, site_id=available[site])
        for item in metadata["filters"]:
            _named, allowed = scraper.target_filter(item)
            category = scraper.ALIASES.get(item["category"], item["category"])
            if allowed and category in scraper.FAST_CATEGORIES:
                jobs.append((site, available[site], item, category))
    original = {
        "site_id": discovery["site"]["id"],
        "site_name": discovery["site"]["name"],
        "filter_id": discovery["filter"]["id"],
    }
    names = {item["id"]: item["name"] for item in discovery["sites"]}
    return jobs, names, original


async def poll(chrome, args, state: dict, seed_mappings: dict,
               mapping_conflicts: set[str]) -> dict:
    metrics = {
        "status": "running", "started_at": utcnow(), "filters_read": 0,
        "records_seen": 0, "records_changed": 0, "records_sent": 0,
        "errors": 0,
    }
    jobs, names, original = await discover_jobs(chrome, args.dealership)
    if not jobs:
        raise RuntimeError("No scoped Cranbourne Kia/MG/Geely Internet or Unsold queues were found.")
    listed: dict[str, tuple[dict, str, str, str, str]] = {}
    all_todos = []
    try:
        current_site = None
        for site, site_id, item, category in jobs:
            if current_site != site_id:
                await chrome.call("site", name=site, site_id=site_id)
                current_site = site_id
            result = await scraper.read_filter(chrome, site_id=site_id, filter_id=item["id"])
            metrics["filters_read"] += 1
            all_todos.extend(result["todos"])
            for todo in result["todos"]:
                listed[scraper.record_key(todo)] = (todo, site, site_id, str(item["id"]), category)
        mappings, conflicts = scraper.build_rooftop_map(
            [*(entry.get("row") or {} for entry in state["records"].values()), *all_todos]
        )
        conflicts |= mapping_conflicts
        for key, value in seed_mappings.items():
            if key not in conflicts and key not in mappings:
                mappings[key] = value
        candidates = {}
        for key, item in listed.items():
            todo, site, site_id, filter_id, category = item
            rooftop, basis = scraper.scoped_rooftop(todo, mappings, conflicts)
            if rooftop and scraper.sales_candidate(todo):
                candidates[key] = (todo, rooftop, basis, site, site_id, filter_id, category)
        metrics["records_seen"] = len(candidates)
        baseline = not state.get("baseline_completed_at")
        now = utcnow()
        for key, (todo, rooftop, basis, site, site_id, filter_id, category) in candidates.items():
            list_hash = fingerprint(todo, LIST_FINGERPRINT_FIELDS)
            previous = state["records"].get(key, {})
            previous.update({
                "opportunity_id": str(todo.get("salesOpportunityId") or ""),
                "rooftop": rooftop,
                "last_seen_at": now,
                "site": site,
                "site_id": site_id,
                "filter_id": filter_id,
                "category": category,
            })
            changed = previous.get("list_fingerprint") != list_hash
            previous["list_fingerprint"] = list_hash
            state["records"][key] = previous
            if baseline and not args.send_existing:
                continue
            if not changed and not previous.get("pending_payload"):
                continue
            metrics["records_changed"] += 1
            try:
                if previous.get("pending_payload") and not changed:
                    payload = previous["pending_payload"]
                    row = previous.get("row")
                else:
                    detail = await chrome.call(
                        "detail", todo=todo, site_id=site_id, filter_id=filter_id,
                        max_attempts=2, detail_timeout=args.detail_timeout * 1000,
                    )
                    raw = detail["record"]
                    actual, actual_basis = scraper.scoped_rooftop(raw, mappings, conflicts)
                    if actual != rooftop:
                        raise RuntimeError("Detail rooftop does not match the verified list rooftop")
                    row = scraper.scope_row(raw, actual, actual_basis or basis, names, "live_worker")
                    is_new = not baseline and not previous.get("detail_fingerprint")
                    mode = "backfill" if baseline else "live_scrape"
                    payload = portal_payload(
                        row, mode=mode, is_new=is_new,
                    )
                    previous["row"] = row
                    previous["detail_fingerprint"] = payload["sourceFingerprint"]
                    previous["pending_payload"] = payload
                if previous.get("last_sent_fingerprint") == payload["sourceFingerprint"]:
                    previous.pop("pending_payload", None)
                    continue
                response = await post_json(args.ingest_url, args.secret, payload, args.http_timeout)
                previous["last_sent_fingerprint"] = payload["sourceFingerprint"]
                previous["last_sent_at"] = utcnow()
                previous["last_intake_status"] = response.get("intakeStatus")
                previous.pop("pending_payload", None)
                previous.pop("last_error", None)
                metrics["records_sent"] += 1
            except Exception as error:
                previous["last_error"] = str(error)[:1500]
                previous["last_error_at"] = utcnow()
                metrics["errors"] += 1
                metrics["last_error"] = str(error)[:500]
                print(f"  {rooftop} {todo.get('salesOpportunityId')}: {error}",
                      file=sys.stderr, flush=True)
            finally:
                atomic_json(args.state_file, state)
                export_current(args.state_dir, state)
        if baseline:
            state["baseline_completed_at"] = utcnow()
        state["polls"] = int(state.get("polls", 0)) + 1
        state["last_poll_at"] = utcnow()
        metrics["status"] = "degraded" if metrics["errors"] else "healthy"
        return metrics
    finally:
        try:
            await chrome.call("restore", **original)
        except Exception:
            pass


async def run(args) -> int:
    from dotenv import load_dotenv
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    load_dotenv(args.env_file)
    args.secret = (args.secret or os.getenv("LEAD_FORWARD_SECRET", "")).strip()
    args.ingest_url = (args.ingest_url or os.getenv(
        "DEALERSOCKET_SYNC_API_URL", "https://www.cranbourne-backend.omnisuiteai.com/api/leads/ingest"
    )).strip()
    args.heartbeat_url = args.ingest_url.rsplit("/", 1)[0] + "/connector-heartbeat"
    if not args.secret:
        raise RuntimeError("Set LEAD_FORWARD_SECRET in .env; it must match the backend.")
    token = os.getenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "").strip()
    if not token or token.startswith(("http://", "https://")):
        raise RuntimeError("Set PLAYWRIGHT_MCP_EXTENSION_TOKEN to the Chrome extension token.")
    local_cli = os.getenv("PLAYWRIGHT_MCP_CLI", "").strip()
    node = shutil.which("node")
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if local_cli and (not Path(local_cli).is_file() or not node):
        raise RuntimeError("PLAYWRIGHT_MCP_CLI must name an installed MCP cli.js file.")
    if not local_cli and not npx:
        raise RuntimeError("Node.js/npx is missing.")
    args.state_dir.mkdir(parents=True, exist_ok=True)
    state = read_state(args.state_file)
    seed_mappings, mapping_conflicts = load_seed_mappings(args.seed_run)
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PLAYWRIGHT_", "DEALERSOCKET_"))
        and key != "LEAD_FORWARD_SECRET"
    }
    env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = token
    profile = os.getenv("PLAYWRIGHT_MCP_PROFILE_DIR_NAME")
    if profile:
        env["PLAYWRIGHT_MCP_PROFILE_DIR_NAME"] = profile
    command = node if local_cli else npx
    server_args = [local_cli] if local_cli else ["-y", "@playwright/mcp@0.0.83"]
    params = StdioServerParameters(
        command=command,
        args=server_args + ["--extension", "--browser", "chrome",
                            "--output-dir", str(args.state_dir)],
        cwd=str(scraper.BASE_DIR), env=env,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    print("Connecting to the signed-in Google Chrome session...", flush=True)
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=180),
            client_info=types.Implementation(
                name="dealersocket-live-sync", version=WORKER_VERSION,
            ),
        ) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            tool = next((name for name in ("browser_run_code_unsafe", "browser_run_code")
                         if name in names), None)
            if not tool:
                raise RuntimeError("The MCP server lacks the Playwright code tool.")
            await scraper.attach_dealersocket_tab(session)
            chrome = scraper.Chrome(session, tool, scraper.BROWSER_CODE)
            while not stop.is_set():
                metrics = {"status": "failed", "started_at": utcnow(), "errors": 1}
                try:
                    metrics = await poll(chrome, args, state, seed_mappings, mapping_conflicts)
                    print(
                        f"[{metrics['status']}] {metrics['records_seen']} seen; "
                        f"{metrics['records_changed']} changed; {metrics['records_sent']} sent; "
                        f"{metrics['errors']} errors",
                        flush=True,
                    )
                except Exception as error:
                    metrics["last_error"] = str(error)[:500]
                    print(f"Poll failed: {error}", file=sys.stderr, flush=True)
                metrics["completed_at"] = utcnow()
                state["last_metrics"] = metrics
                atomic_json(args.state_file, state)
                export_current(args.state_dir, state)
                await heartbeat(args, state, metrics)
                if args.once:
                    return 0 if metrics["status"] in {"healthy", "degraded"} else 2
                try:
                    await asyncio.wait_for(stop.wait(), timeout=args.interval)
                except asyncio.TimeoutError:
                    pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=scraper.BASE_DIR / ".env")
    parser.add_argument("--state-dir", type=Path, default=scraper.BASE_DIR / "live-sync")
    parser.add_argument("--seed-run", help="Prior scoped run/checkpoint used only for verified rooftop ID mappings.")
    parser.add_argument("--dealership", action="append", help="Restrict source selectors; repeatable.")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between fresh queue polls (minimum 30).")
    parser.add_argument("--detail-timeout", type=int, default=20)
    parser.add_argument("--http-timeout", type=float, default=15)
    parser.add_argument("--ingest-url")
    parser.add_argument("--secret", help=argparse.SUPPRESS)
    parser.add_argument("--worker-id", default="dealersocket-chrome-1")
    parser.add_argument("--once", action="store_true", help="Run one fresh poll and exit.")
    parser.add_argument("--send-existing", action="store_true",
                        help="On the first poll, backfill current rows with messaging disabled.")
    args = parser.parse_args()
    if args.interval < 30:
        parser.error("--interval must be at least 30 seconds.")
    if args.detail_timeout < 5 or args.http_timeout <= 0:
        parser.error("Timeouts are too small.")
    args.state_dir = args.state_dir.resolve()
    args.state_file = args.state_dir / "state.json"
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print("ERROR: " + scraper.redact("\n".join(scraper.exception_details(error))),
              file=sys.stderr)
        try:
            args.state_dir.mkdir(parents=True, exist_ok=True)
            (args.state_dir / "worker-error.log").write_text(
                scraper.redact("".join(traceback.format_exception(error))), encoding="utf-8",
            )
        except OSError:
            pass
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
