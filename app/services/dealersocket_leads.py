"""Read-only CRM snapshots with private last-result persistence and scheduled reads."""
import asyncio
import importlib.util
import logging
from pathlib import Path
from time import monotonic
from uuid import uuid4
from urllib.parse import urlparse

from app.models.dealersocket_leads import LeadReadOptions
from app.services.dealersocket_lead_browser import LeadBrowser
from app.services.dealersocket_session import dealersocket_session, now
from app.services.dealersocket_lead_schedule import LeadSchedule

log = logging.getLogger(__name__)
DETAIL_READ_BUDGET_SECONDS = 420

# Reuse the existing scoped normalization/mapping rules, not its CLI/MCP runner.
_path = Path(__file__).resolve().parents[2] / "outputs/dealersocket_csv_sync/download_dealersocket_chrome.py"
_spec = importlib.util.spec_from_file_location("scoped_lead_rules", _path)
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)


async def collect_snapshot(browser, options, progress=None):
    # Each campaign uses an isolated rules module; never mutate the existing reader's scope.
    selected_rules = rules
    if options.scope == 'south_morang_hyundai':
        selected_rules = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(selected_rules)
        selected_rules.ROOFTOPS = ['South Morang Hyundai']
        def canonical(label):
            return 'South Morang Hyundai' if selected_rules.label_key(label) in {
                'southmoranghyundai', 'southmoranghyundainew', 'southmoranghyundaiused',
                'southmoranghyundaiusedcars', 'hyundaisouthmorangusedcars',
                'hyundaisouthmorang', 'hyundaisouthmorangnew', 'hyundaisouthmorangused',
            } else ''
        selected_rules.canonical_rooftop = canonical
        selected_rules.target_filter = lambda item: (True, selected_rules.ALIASES.get(item.get('category'), item.get('category')) in selected_rules.FAST_CATEGORIES)
    return await _collect_snapshot(browser, options, selected_rules, progress)


async def _collect_snapshot(browser, options, rules, progress=None):
    started = now()
    clock = monotonic()
    # Reserve time to package and save list rows before the outer ten-minute timeout.
    detail_deadline = clock + DETAIL_READ_BUDGET_SECONDS
    records, coverage, errors, review, listed, jobs = {}, [], [], [], {}, []
    if progress:
        progress("opening_opportunities")
    original = await browser.prepare()
    if original.get("keyword"):
        raise RuntimeError("keyword_filter_active")
    names = {str(site["id"]): site["name"] for site in original["sites"]}
    limited, snapshot = False, None
    try:
        for site in original["sites"]:
            if options.scope == 'south_morang_hyundai' and rules.label_key(site['name']) not in {'southmoranghyundai', 'booransouthmoranghyundai'}:
                continue
            if progress:
                progress("reading_filter_catalog", siteId=str(site["id"]))
            try:
                meta = await browser.site(site)
                for item in meta["filters"]:
                    if rules.target_filter(item)[1]:
                        jobs.append((site, item))
            except Exception:
                errors.append({"stage":"catalog", "siteId":str(site["id"]), "code":"catalog_read_failed"})
        for site, item in jobs:
            if progress:
                progress("reading_queue", siteId=str(site["id"]), filterId=str(item["id"]))
            item_coverage = {"siteId":str(site["id"]), "filterId":str(item["id"]),
                "filterName":item["name"], "status":"partial", "displayedCount":None,
                "loadedCount":0, "observedAt":None}
            coverage.append(item_coverage)
            try:
                result = await browser.read_filter(site, item)
                item_coverage.update(displayedCount=result["displayed_count"], loadedCount=len(result["todos"]), observedAt=now())
                if result.get("keyword"):
                    raise RuntimeError("keyword_filter_active")
                if result["displayed_count"] == len(result["todos"]):
                    item_coverage["status"] = "complete_selected_queue"
                else:
                    errors.append({"stage":"filter", "siteId":str(site["id"]), "filterId":str(item["id"]), "code":"queue_count_unverified"})
                for todo in result["todos"]:
                    listed[rules.record_key(todo)] = (todo, site, item, item_coverage["observedAt"])
            except Exception:
                errors.append({"stage":"filter", "siteId":str(site["id"]), "filterId":str(item["id"]), "code":"queue_read_failed"})
        mappings, conflicts = rules.build_rooftop_map([entry[0] for entry in listed.values()])
        buckets = {name:[] for name in rules.ROOFTOPS}
        for key, (todo, site, item, observed) in listed.items():
            if not rules.sales_candidate(todo):
                continue
            rooftop, basis = rules.scoped_rooftop(todo, mappings, conflicts)
            if not rooftop:
                label = str(todo.get("franchiseName") or "")
                if not label or "conflict" in basis or any(x in label.lower() for x in ("cranbourne", "geely")):
                    review.append({"siteId":str(site["id"]), "opportunityId":str(todo.get("salesOpportunityId") or ""), "reason":basis})
                continue
            if not todo.get("salesOpportunityId") or not todo.get("contactId"):
                review.append({"siteId":str(site["id"]), "reason":"missing_record_identity"})
                continue
            buckets[rooftop].append((key,todo,site,item,observed,rooftop,basis))
        candidate_counts = {name:len(bucket) for name,bucket in buckets.items()}
        # Bounded samples must not be consumed entirely by the first rooftop.
        ordered = []
        while any(buckets.values()) and len(ordered) < options.maxRecords:
            for bucket in buckets.values():
                if bucket and len(ordered) < options.maxRecords:
                    ordered.append(bucket.pop(0))
        limited = any(buckets.values())
        details, consecutive, detail_queue = 0, 0, None
        # Sampling is balanced above; extract the chosen rows by queue so a full
        # frontend refresh does not switch dealerships for every customer.
        for key,todo,site,item,observed,rooftop,basis in sorted(ordered, key=lambda entry:(str(entry[2]["id"]),str(entry[3]["id"]))):
            raw = rules.fallback(todo)
            raw.update(assigned_salesperson=todo.get("assignedTo") or "", last_updated_timestamp=todo.get("lastUpdate") or "")
            detail_at = None
            if details < options.detailLimit and consecutive < 3 and monotonic() < detail_deadline:
                details += 1
                if progress:
                    progress("reading_details", attempted=details, maximum=options.detailLimit)
                try:
                    async with asyncio.timeout(max(0.01, detail_deadline - monotonic())):
                        queue_key = (str(site["id"]), str(item["id"]))
                        if queue_key != detail_queue:
                            result = await browser.read_filter(site, item)
                            if result.get("keyword"):
                                raise RuntimeError("keyword_filter_active")
                            detail_queue = queue_key
                        candidate = await browser.detail(site, item, todo)
                    if candidate.get("restricted"):
                        review.append({"siteId":str(site["id"]), "opportunityId":str(todo["salesOpportunityId"]), "reason":"restricted_record"})
                        continue
                    actual, _ = rules.scoped_rooftop(candidate, mappings, conflicts)
                    if actual != rooftop or candidate.get("opportunity_id") != str(todo["salesOpportunityId"]) or candidate.get("customer_id") != str(todo["contactId"]):
                        raise RuntimeError("detail_identity_mismatch")
                    raw, detail_at, consecutive = candidate, now(), 0
                except Exception:
                    consecutive += 1
                    errors.append({"stage":"detail", "siteId":str(site["id"]), "opportunityId":str(todo["salesOpportunityId"]), "code":"detail_unverified"})
            row = rules.scope_row(raw, rooftop, basis, names, "live_connector")
            row.update(record_key=key, list_observed_at=observed, detail_observed_at=detail_at)
            # List snapshots remain valid evidence, but missing contact details are explicit.
            if not detail_at:
                row["validation_warnings"] += ";list_only_details_not_verified"
            records[key] = row
        roofs = [{"name":name, "recordCount":sum(r["rooftop"]==name for r in records.values()),
                  "matchedCandidateCount":candidate_counts[name],
                  "coverage":"records_observed" if any(r["rooftop"]==name for r in records.values()) else "not_verified_or_empty_selected_queues"}
                 for name in rules.ROOFTOPS]
        partial = bool(errors or review or limited or not jobs or any(not r["detail_extracted"] for r in records.values())
                       or any(roof["recordCount"] == 0 for roof in roofs))
        snapshot = {"state":"partial" if partial else "complete", "startedAt":started, "fetchedAt":now(),
            "scope": options.scope,
            "durationSeconds":round(monotonic()-clock,2), "records":list(records.values()),
            "rooftops":roofs, "coverage":coverage, "errors":errors, "review":review,
            "limited":limited, "detailReads":details, "readOnly":True,
            "coverageNote":"Fresh selected-queue snapshot; assigned/source/date restrictions may hide other leads. Absence does not imply CRM deletion. Newly observed does not prove new/reopened eligibility."}
        return snapshot
    finally:
        try:
            await browser.restore(original)
        except Exception:
            errors.append({"stage":"restore", "code":"original_filter_not_restored"})
            if snapshot is not None:
                snapshot["state"] = "partial"
        if snapshot is not None:
            snapshot["durationSeconds"] = round(monotonic()-clock,2)
            snapshot["completedAt"] = now()


class DealerSocketLeadReader:
    def __init__(self, session=dealersocket_session):
        self.session = session
        self.task = None
        self.job = None
        self.jobs_by_scope = {}
        self.latest = None
        self.latest_by_scope = {}
        self.schedule = LeadSchedule(self)

    def status(self, scope=None):
        # Unscoped callers are the existing Cranbourne portal, even while Hyundai reads.
        scope = scope or 'cranbourne'
        latest = self.latest_by_scope.get(scope) or (
            self.latest if self.latest and self.latest.get('scope', 'cranbourne') == scope else None)
        job = (self.job if self.job and self.job.get('scope', 'cranbourne') == scope
               else self.jobs_by_scope.get(scope))
        if latest:
            # A failed refresh must retain the successful snapshot's observation time.
            result = {**latest, **(job if job and job.get('state') in ('running', 'error') else {}),
                      'fetchedAt': latest['fetchedAt'], 'completedAt': latest.get('completedAt')}
        else:
            result = {**(job or {'state': 'idle', 'records': [], 'readOnly': True}),
                      'fetchedAt': None}
        return {**result, 'scope': scope, 'snapshotState': latest['state'] if latest else None,
                'schedule': self.schedule.metadata(scope)}

    def start(self, options):
        if self.task and not self.task.done() or self.session.reader_busy or self.session.lock.locked() or self.session.task and not self.session.task.done():
            raise RuntimeError("browser_busy")
        self.session.reader_busy = True
        self.job = {"state":"running", "jobId":str(uuid4()), "startedAt":now(), "readOnly":True, "scope":getattr(options, 'scope', 'cranbourne')}
        self.jobs_by_scope[self.job['scope']] = self.job
        log.info('DealerSocket lead refresh started job=%s scope=%s', self.job['jobId'], self.job['scope'])
        self.task = asyncio.create_task(self._run(options))
        return self.status(getattr(options, 'scope', 'cranbourne'))

    async def _run(self, options):
        job_clock = monotonic()
        try:
            async with asyncio.timeout(600):
                self.job["progress"] = {"stage":"checking_session"}
                await self.session._run("check")
                if self.session.state != "connected":
                    self.job = {**self.job, "state":"error", "code":"login_required" if self.session.state=="login_required" else "session_unavailable",
                                "message":self.session.message, "fetchedAt":now()}
                    log.warning('DealerSocket lead refresh stopped job=%s code=%s auth_code=%s', self.job['jobId'], self.job['code'], getattr(self.session, 'error_code', None))
                    return
                async with self.session.lock:
                    def progress(stage, **fields):
                        self.job["progress"] = {"stage":stage, **fields}
                    result = await collect_snapshot(LeadBrowser(self.session.page), options, progress)
                    if urlparse(self.session.page.url).hostname != "bb.dealersocket.com":
                        self.session.state, self.session.verified = "login_required", set()
                        raise PermissionError("login_required")
                    result["totalDurationSeconds"] = round(monotonic()-job_clock,2)
                    self.job = {**self.job, **result}
                    scope = getattr(options, 'scope', 'cranbourne')
                    latest = self.job.copy()
                    self.latest_by_scope[scope] = latest
                    if scope == 'cranbourne':
                        self.latest = latest
                    await self.schedule.save(latest)
                    log.info('DealerSocket lead refresh completed job=%s duration=%.1fs', self.job['jobId'], result['totalDurationSeconds'])
        except asyncio.CancelledError:
            self.job = {**self.job, "state":"error", "code":"read_cancelled"}
            raise
        except Exception as error:
            code = "login_required" if isinstance(error, PermissionError) else "read_timeout" if isinstance(error, TimeoutError) else "lead_read_failed"
            self.job = {**self.job, "state":"error", "code":code, "fetchedAt":now(),
                        "message":"Lead snapshot could not be verified. Check session and queue coverage; no CRM writes were performed."}
            log.warning('DealerSocket lead refresh failed job=%s code=%s stage=%s duration=%.1fs error_type=%s', self.job.get('jobId'), code, self.job.get('progress', {}).get('stage'), monotonic()-job_clock, type(error).__name__)
        finally:
            self.jobs_by_scope[getattr(options, 'scope', 'cranbourne')] = self.job
            self.session.reader_busy = False

    async def shutdown(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


dealersocket_leads = DealerSocketLeadReader()
