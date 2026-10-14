"""Read DealerSocket opportunities using signed-in Chrome; generate local CSVs.

Standalone script: the browser adapter is embedded below.
Default: scoped Cranbourne Kia/MG/Geely sales-lead candidates only.
The legacy broad scan requires --all-rooftops. No SMS is sent.
No credentials, browser profile cloning, CDP port or customer-facing writes.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEALERSHIPS = ["Berwick MG", "Cranbourne Hyundai", "Dandenong Mitsubishi",
               "South Morang Hyundai", "South Morang Kia", "Southland Kia & Isuzu Ute"]
CATEGORIES = ["Daily Responsibilities", "Internet Traffic", "Lost Opportunities", "QA",
              "Revenue Radar", "Service Opportunities", "Sold Opportunities", "Unsold Traffic"]
ALIASES = {"Daily Responsibility": "Daily Responsibilities", "Quality Assurance (CSI)": "QA",
           "Revenue_Radar": "Revenue Radar"}
ROOFTOPS = ["Cranbourne Kia", "Cranbourne MG", "Geely Cranbourne"]
FAST_CATEGORIES = ["Internet Traffic", "Unsold Traffic"]
REQUIRED = ["opportunity_id", "customer_id", "rooftop", "customer_name", "customer_phone",
            "customer_email", "vehicle_of_interest", "lead_source", "assigned_salesperson", "received_timestamp"]
FIELDS = REQUIRED + ["dealership_id", "dealership_name", "franchise_id", "opportunity_type",
    "opportunity_status", "source_type", "tracking_code", "lead_source_basis", "assigned_salesperson_id",
    "received_timestamp_basis", "last_updated_timestamp", "customer_phone_raw", "mobile_valid",
    "customer_email_raw", "email_selection", "description", "customer_comments", "customer_comments_basis", "never_contact_via",
    "detail_extracted", "adapter", "model_id_present", "missing_fields", "validation_warnings",
    "source_url", "timeline_load_status", "scraped_at", "selected_dealerships", "categories", "matched_filter_ids",
    "rooftop_raw", "rooftop_mapping_basis", "new_lead_eligibility", "snapshot_origin"]
MEMBER_FIELDS = ["selected_dealership_id", "selected_dealership", "category", "filter_id", "filter_name",
                 "record_key", "dealership_id", "opportunity_type", "opportunity_id", "customer_id"]
FILTER_FIELDS = ["selected_dealership_id", "selected_dealership", "category", "filter_id", "filter_name",
                 "is_my_filter", "is_enterprise", "status", "displayed_count", "loaded_count",
                 "distinct_records", "detail_failures", "error"]


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def mobile(value):
    text = str(value or "").strip()
    if re.search(r"[A-Za-z]", text):
        return ""
    digits = re.sub(r"\D", "", text)
    if digits.startswith("0061"):
        digits = digits[2:]
    if re.fullmatch(r"04\d{8}", digits):
        return "+61" + digits[1:]
    if re.fullmatch(r"614\d{8}", digits):
        return "+" + digits
    if re.fullmatch(r"4\d{8}", digits):
        return "+61" + digits
    return ""


def record_key(todo):
    return "|".join(str(todo.get(k) or "").lower() for k in
                    ("dealershipId", "opportunityType", "salesOpportunityId", "contactId"))


def normalize(raw, sites):
    row = dict(raw)
    row["customer_phone"] = mobile(raw.get("customer_phone_raw"))
    row["mobile_valid"] = bool(row["customer_phone"])
    emails = re.findall(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
                        str(raw.get("customer_email_raw") or ""))
    row["customer_email"] = emails[0] if emails else ""
    row["email_selection"] = "first_of_multiple" if len(emails) > 1 else "single" if emails else "unavailable"
    row["dealership_name"] = sites.get(str(row.get("dealership_id")), "")
    row["missing_fields"] = ";".join(k for k in REQUIRED if not row.get(k))
    warnings = []
    if raw.get("customer_phone_raw") and not row["mobile_valid"]:
        warnings.append("invalid_or_non_mobile_phone")
    if len(emails) > 1:
        warnings.append("multiple_emails_first_used_raw_preserved")
    if row.get("received_timestamp_basis") == "opportunity.createdDate":
        warnings.append("received_time_uses_opportunity_creation_source_offset_unverified")
    if str(row.get("opportunity_type", "")).lower() != "sales":
        warnings.append("non_sales_record_not_a_new_sales_lead")
    if row.get("never_contact_via"):
        warnings.append("crm_contact_restriction_present")
    if not row.get("detail_extracted"):
        warnings.append("detail_extraction_failed_list_fields_only")
    if row.get("timeline_load_status") == "timeout":
        warnings.append("timeline_load_timeout_comments_may_be_incomplete")
    if not row.get("model_id_present"):
        warnings.append("detail_model_id_missing_list_id_preserved")
    row["validation_warnings"] = ";".join(warnings)
    row["scraped_at"] = utcnow()
    return row


def fallback(todo):
    return {"opportunity_id": str(todo.get("salesOpportunityId") or ""),
            "customer_id": str(todo.get("contactId") or ""),
            "dealership_id": str(todo.get("dealershipId") or ""),
            "opportunity_type": todo.get("opportunityType") or "",
            "rooftop": todo.get("franchiseName") or "",
            "franchise_id": str(todo.get("franchiseId") or ""),
            "customer_name": todo.get("fullName") or "",
            "vehicle_of_interest": " ".join(str(todo.get(k) or "") for k in ("year", "make", "model")).strip(),
            "opportunity_status": todo.get("eventStatus") or "", "detail_extracted": False,
            "received_timestamp_basis": "unavailable"}


def tool_text(result):
    blocks = [getattr(b, "text", "") for b in result.content if getattr(b, "type", "text") == "text"]
    text = "\n".join(blocks)
    if getattr(result, "isError", False) or text.lstrip().startswith("### Error"):
        raise RuntimeError(text.split("### Error", 1)[-1].strip()[:1500])
    return text


def parse_result(result):
    text = tool_text(result)
    if getattr(result, "structuredContent", None):
        data = result.structuredContent
        if isinstance(data, dict) and "ok" in data:
            return data
    payload = text.split("### Result\n", 1)[-1].split("\n### ", 1)[0].strip()
    data = json.loads(payload)
    if not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError("MCP returned an unexpected result, not an extraction success.")
    return data


def exception_details(error):
    """Expose real failures hidden by AnyIO/MCP's nested TaskGroup wrappers."""
    children = getattr(error, "exceptions", None)
    if children:
        lines = [line for child in children for line in exception_details(child)]
        return list(dict.fromkeys(lines))
    return [type(error).__name__ + ": " + str(error)]


def redact(text):
    for name in ("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "DEALERSOCKET_PASSWORD"):
        secret = os.getenv(name, "")
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


async def attach_dealersocket_tab(session):
    """Give this MCP client its own tab within the authenticated Chrome profile."""
    print("Connecting Chrome extension (share a tab if it prompts)...", flush=True)
    result = await session.call_tool("browser_tabs", arguments={"action": "list"})
    tabs = tool_text(result)
    if "https://bb.dealersocket.com/" not in tabs:
        print("Opening a DealerSocket tab for dealersocket-csv-sync in the same Chrome profile...", flush=True)
        url = os.getenv("DEALERSOCKET_OPPORTUNITIES_URL",
                        "https://bb.dealersocket.com/#/crm/employee?todoType=opportunities")
        if not url.startswith("https://bb.dealersocket.com/"):
            raise RuntimeError("DEALERSOCKET_OPPORTUNITIES_URL must use https://bb.dealersocket.com/.")
        result = await session.call_tool("browser_tabs", arguments={"action": "new", "url": url})
        tool_text(result)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row.get(k) is None else row.get(k, "") for k in fields})
    temporary.replace(path)


def checkpoint(path, state):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def resolve_errors(state, stage, *, site=None, filter_id=None, record_key=None):
    """Keep resolved failures as history, not as currently outstanding errors."""
    active = []
    for error in state["errors"]:
        matches = (error.get("stage") == stage
                   and (site is None or error.get("site") == site)
                   and (filter_id is None or str(error.get("filter_id")) == str(filter_id))
                   and (record_key is None or error.get("record_key") == record_key))
        if matches:
            state.setdefault("error_history", []).append({**error, "resolved_at": utcnow(),
                                                          "resolution": "retry_succeeded"})
        else:
            active.append(error)
    state["errors"] = active


def error_message(error):
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(error))[:1500]


async def read_filter(chrome, *, site_id, filter_id):
    """One bounded reload/reselection retry for transient UI loading failures."""
    for attempt in (1, 2):
        try:
            return await chrome.call("filter", site_id=site_id, filter_id=filter_id,
                                     recover=attempt > 1)
        except Exception as error:
            transient = any(word in str(error).lower() for word in
                            ("timeout", "did not settle", "not ready", "context was destroyed", "detached"))
            if attempt == 2 or not transient:
                raise
            print(f"  Filter {filter_id} did not finish loading; retrying once after reloading its tab...", flush=True)
            await asyncio.sleep(0.5)


def export_files(folder, state, sites, categories):
    records, members = state["records"], list(state["members"].values())
    by_record = defaultdict(list)
    by_group = defaultdict(set)
    for m in members:
        by_record[m["record_key"]].append(m)
        by_group[(m["selected_dealership"], m["category"])].add(m["record_key"])
    rows = []
    for key, record in records.items():
        row = dict(record)
        matches = by_record[key]
        row["selected_dealerships"] = json.dumps(sorted({m["selected_dealership"] for m in matches}))
        row["categories"] = json.dumps(sorted({m["category"] for m in matches}))
        row["matched_filter_ids"] = json.dumps(sorted({m["selected_dealership_id"] + ":" + m["filter_id"] for m in matches}))
        rows.append(row)
    by_key = {key: row for key, row in zip(records, rows)}
    write_csv(folder / "opportunities.csv", FIELDS, rows)
    write_csv(folder / "filter_memberships.csv", MEMBER_FIELDS, members)
    write_csv(folder / "filter_coverage.csv", FILTER_FIELDS, state["coverage"].values())
    write_csv(folder / "errors.csv", ["stage", "site", "filter_id", "record_key", "error"], state["errors"])
    write_csv(folder / "error_history.csv",
              ["stage", "site", "filter_id", "record_key", "error", "resolved_at", "resolution"],
              state.get("error_history", []))
    for site in sites:
        for category in categories:
            subset = [by_key[k] for k in sorted(by_group[(site, category)]) if k in by_key]
            write_csv(folder / "by_dealership" / slug(site) / (slug(category) + ".csv"), FIELDS, subset)
    summary = {"started_at": state["started_at"], "exported_at": utcnow(), "status": state["status"],
               "selected_dealerships": sites, "categories": categories, "unique_records": len(rows),
               "filter_memberships": len(members), "errors": len(state["errors"]),
               "resolved_errors": len(state.get("error_history", [])),
               "coverage_status": dict(Counter(x["status"] for x in state["coverage"].values())),
               "records_with_missing_fields": sum(bool(r.get("missing_fields")) for r in rows),
               "rows_with_all_payload_fields_present": sum(not r.get("missing_fields") for r in rows),
               "scope": "Saved filters accessible to this user; My filters remain user-specific.",
               "timestamp_note": "Sales received_timestamp uses opportunity.createdDate verbatim; lead-received semantics and source offset need validation.",
               "snapshot": "Records are cached within this run. A new invocation creates a fresh snapshot; --resume continues this snapshot."}
    checkpoint(folder / "run_summary.json", summary)
    return summary


BROWSER_CODE = r"""
async (page) => {
  // Read-only extraction from the page's currently loaded Angular components.
  // No API endpoints, credentials, login forms, save buttons or contact actions.
  const cfg = __CONFIG__;
  const isDealerSocket = p => {
    try { return new URL(p.url()).hostname === 'bb.dealersocket.com'; }
    catch { return false; }
  };
  const isOpportunities = p => isDealerSocket(p)
    && /^#\/crm\/employee\?/.test(new URL(p.url()).hash)
    && new URLSearchParams(new URL(p.url()).hash.split('?')[1]).get('todoType') === 'opportunities';
  const pages = page.context().pages().filter(p => !p.isClosed());
  const target = (isOpportunities(page) && page) || pages.find(isOpportunities)
    || (isDealerSocket(page) && page) || pages.find(isDealerSocket);
  if (!target) throw new Error('Share the signed-in DealerSocket tab with this MCP client in Chrome.');
  if (!isOpportunities(target)) {
    await target.goto('https://bb.dealersocket.com/#/crm/employee?todoType=opportunities',
      {waitUntil:'domcontentloaded', timeout:30000});
  } else if (cfg.op === 'filter' && cfg.recover) {
    // A timed-out selection may not fire another Angular change for the same value.
    // Reload only on the single explicit filter retry; never reload customer forms.
    await target.reload({waitUntil:'domcontentloaded', timeout:30000});
  }
  // browser_tabs/new can finish before the Angular shell creates the CRM frame.
  // Match the component, not a case-sensitive or version-specific iframe URL.
  const listSelector = 'crm-employee-dashboard-list-details';
  const started = Date.now();
  let frame;
  while (Date.now() - started < 55000) {
    if (target.isClosed()) throw new Error('The DealerSocket automation tab was closed. Keep it open during the scan.');
    for (const candidate of target.frames()) {
      try {
        const ready = await candidate.locator(listSelector).evaluate(e => {
          const c = window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
          return !!(c?.dealerships?.length && c.selectedDealership && c.selectedFilter
            && Array.isArray(c.categoryFilters));
        }, undefined, {timeout:500});
        if (ready) { frame = candidate; break; }
      } catch { /* Frame creation/navigation and component initialization are asynchronous. */ }
    }
    if (frame) break;
    try {
      if (Date.now() - started > 5000 && await target.locator('input[type="password"]').first().isVisible())
        throw new Error('DealerSocket is showing a sign-in form. Sign in to this Chrome profile, then retry.');
    } catch (error) {
      if (String(error.message).includes('showing a sign-in form')) throw error;
    }
    await target.waitForTimeout(250);
  }
  if (!frame) throw new Error('DealerSocket opened, but its Opportunities component was not ready within 55 seconds. '
    + 'Keep the automation tab open and retry --diagnose after the page finishes loading. '
    + 'This is a page-readiness failure, not evidence that you need a new extension token.');
  const ua = await target.evaluate(() => navigator.userAgent);
  if (!ua.includes('Chrome/') || ua.includes('Edg/')) throw new Error('Use the extension token from Google Chrome, not another browser.');
  const list = frame.locator(listSelector);
  const readList = async fn => list.evaluate(fn);
  const listView = async () => {
    for (let i=0; i<3; i++) {
      if (await frame.locator('select[name="filter-dropdown"]').isVisible()) return;
      const expand = frame.locator('button[name="expandButton"]');
      if (!await expand.isVisible()) break;
      await expand.click();
    }
    await frame.locator('select[name="filter-dropdown"]').waitFor({timeout:10000});
  };
  const ready = async (siteId, filterId=null) => {
    try { await frame.waitForFunction(({siteId,filterId}) => {
      const e=document.querySelector('crm-employee-dashboard-list-details');
      const c=e&&window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
      return c && !c.isLoadingTodos && String(c.selectedDealership?.value)===String(siteId)
        && String(c.selectedFilter?.siteId)===String(siteId)
        && c.categoryFilters?.length>0
        && c.categoryFilters.every(x=>String(x.siteId)===String(siteId))
        && (filterId===null || String(c.selectedFilter?.id)===String(filterId));
    }, {siteId,filterId}, {timeout:30000});
    } catch (error) {
      const observed=await readList(e=>{
        const c=window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
        return {site:c?.selectedDealership?.value,filter:c?.selectedFilter?.id,
          filterSite:c?.selectedFilter?.siteId,busy:c?.isLoadingTodos,
          loadedRows:c?.todos?.length,filterCount:c?.categoryFilters?.length};
      }).catch(()=>({component:'unavailable'}));
      throw new Error('Filter readiness timeout: expected site='+siteId+' filter='+filterId
        +'; observed='+JSON.stringify(observed)+'; '+String(error.message).split('\n')[0]);
    }
    // Wait for deferred Angular requests, not just the selection's immediate change.
    let previous='', stable=0;
    for (let i=0;i<40;i++) {
      const s=await readList(e=>{
        const c=angular.element(e).controller('crmEmployeeDashboardListDetails');
        return {busy:!!c.isLoadingTodos,site:String(c.selectedDealership.value),
          filter:String(c.selectedFilter.id),filterSite:String(c.selectedFilter.siteId),
          ids:(c.todos||[]).map(x=>[x.dealershipId,x.opportunityType,x.salesOpportunityId,x.contactId,x.activityId]).join('|')};
      });
      const key=JSON.stringify(s);
      stable=(!s.busy && s.site===String(siteId) && s.filterSite===String(siteId)
        && (filterId===null || s.filter===String(filterId)) && key===previous)?stable+1:0;
      if(stable>=2) return;
      previous=key;
      await frame.waitForTimeout(250);
    }
    throw new Error('Result list did not settle; this filter is not marked complete.');
  };
  const meta = async () => readList(e=>{
    const c=angular.element(e).controller('crmEmployeeDashboardListDetails');
    if(!c) throw new Error('DealerSocket page component changed; extraction adapter requires an update.');
    return {sites:c.dealerships.map(x=>({id:String(x.value),name:x.text})),
      site:{id:String(c.selectedDealership.value),name:c.selectedDealership.text},
      filter:{id:String(c.selectedFilter.id),name:c.selectedFilter.name},
      user:c.selectedDealershipUser?.text||'',keyword:c.searchText||'',
      filters:c.categoryFilters.map(x=>({id:String(x.id),name:x.name,category:x.filterCategory,
        site_id:String(x.siteId),is_my_filter:!!x.isMyFilter,is_enterprise:!!x.isEnterprise}))};
  });
  if(cfg.op==='discover') {
    await listView();
    const m=await meta(); await ready(m.site.id);
    return {ok:true,...await meta()};
  }
  if(cfg.op==='site') {
    await listView();
    const option=await frame.locator('select[name="expanded-dealership-select"] option').evaluateAll(
      (es,name)=>es.filter(x=>x.text===name).map(x=>x.value),cfg.name);
    if(option.length!==1) throw new Error('Dealership option unavailable or ambiguous: '+cfg.name);
    await frame.locator('select[name="expanded-dealership-select"]').selectOption(option[0]);
    await ready(cfg.site_id);
    return {ok:true,...await meta()};
  }
  if(cfg.op==='filter') {
    await listView();
    const option=await list.evaluate((e,id)=>{
      const c=angular.element(e).controller('crmEmployeeDashboardListDetails');
      const item=c.categoryFilters.find(x=>String(x.id)===String(id));
      if(!item) throw new Error('Filter no longer available: '+id);
      return item.$$hashKey;
    },cfg.filter_id);
    await frame.locator('select[name="filter-dropdown"]').selectOption(option);
    await ready(cfg.site_id,cfg.filter_id);
    const result=await readList(e=>{
      const c=angular.element(e).controller('crmEmployeeDashboardListDetails');
      const keys=['dealershipId','activityId','franchiseId','contactId','salesOpportunityId','fullName',
        'opportunityType','eventStatus','franchiseName','condition','year','make','model','assignedTo',
        'activityDateTime','activityMood','description','lastUpdate','age'];
      const todos=(c.todos||[]).map(x=>Object.fromEntries(keys.map(k=>[k,x[k]??null])));
      const labels=[...e.querySelectorAll('[name="results-display"]')]
        .map(x=>(x.textContent||'').trim()).filter(x=>/^\d[\d,]* RESULTS? FOUND$/i.test(x));
      const displayed=labels.length?Number(labels[0].replace(/[^0-9]/g,'')):null;
      return {todos,displayed_count:displayed,loaded_count:todos.length,keyword:c.searchText||''};
    });
    if(result.keyword) throw new Error('Keyword filter is active. Clear it before a complete scan.');
    if(result.displayed_count===null) throw new Error('Displayed result count is unavailable; refusing to certify list coverage.');
    if(result.displayed_count!==null && result.displayed_count!==result.loaded_count)
      throw new Error('Loaded rows do not match the displayed result count; cannot certify this filter.');
    return {ok:true,...result};
  }
  if(cfg.op==='detail') {
    const todo=cfg.todo;
    const key=x=>[String(x.dealershipId),String(x.opportunityType).toLowerCase(),String(x.salesOpportunityId),String(x.contactId)].join('|');
    const expected=key(todo);
    const matchesCard=(e,expected)=>{
      const x=window.angular?.element(e).controller('crmOpportunityCard')?.opportunity;
      return !!x&&[String(x.dealershipId),String(x.opportunityType).toLowerCase(),
        String(x.salesOpportunityId),String(x.contactId)].join('|')===expected;
    };
    const matchesDetail=({id,contact,type})=>{
      const angular=window.angular;
      const view=document.querySelector('crm-employee-dashboard-opportunity-view');
      if(!view||!angular)return false;
      const bindings=[['sales','crm-sales-opportunity-detail-view','crmSalesOpportunityDetailView','salesOpportunity','contact'],
        ['service','ds-service-opportunity-container','dsServiceOpportunityContainer','serviceOpportunity','owner'],
        ['qa','csi-update','csiUpdate','csiEvent','contact']];
      for(const [kind,tag,name,model,p] of bindings){
        if(kind!==String(type).toLowerCase())continue;
        const e=view.querySelector(tag);if(!e)continue;
        const c=angular.element(e).controller(name);
        const actual=c?.[model]?.id||c?.opportunityId||c?.serviceOpportunityId;
        const customer=c?.[p]?.id;
        const loading=c?.isLoading||c?.numberItemsStillLoading>0;
        if(String(actual)===String(id)&&String(customer)===String(contact)&&!loading)return true;
      }
      return false;
    };
    const detailIdentity={id:todo.salesOpportunityId,contact:todo.contactId,type:todo.opportunityType};
    const maxAttempts=Math.min(3,Math.max(1,Number(cfg.max_attempts)||3));
    let opened=false,lastError='No matching visible card';
    for(let attempt=1;attempt<=maxAttempts&&!opened;attempt++) {
      try {
        await listView();
        const index=await list.evaluate((e,{expected,siteId,filterId})=>{
          const c=angular.element(e).controller('crmEmployeeDashboardListDetails');
          if(c.isLoadingTodos || (siteId && String(c.selectedDealership?.value)!==String(siteId))
            || (filterId && String(c.selectedFilter?.id)!==String(filterId)))
            throw new Error('Filter changed or is still loading; refusing to select a different record.');
          return c.todos.findIndex(x=>[String(x.dealershipId),String(x.opportunityType).toLowerCase(),
            String(x.salesOpportunityId),String(x.contactId)].join('|')===expected);
        },{expected,siteId:cfg.site_id,filterId:cfg.filter_id});
        if(index<0) throw new Error('Record disappeared from the current filter; rerun this filter.');
        await list.evaluate((e,index)=>{
          const host=e.querySelector('md-virtual-repeat-container');
          const virtual=host&&angular.element(host).data().$mdVirtualRepeatContainerController;
          if(!virtual||typeof virtual.scrollToIndex!=='function')
            throw new Error('DealerSocket virtual-list controller unavailable.');
          virtual.scrollToIndex(index);
        },index);
        let clicked=false;
        for(let poll=0;poll<12&&!clicked;poll++) {
          await frame.waitForTimeout(150);
          // Never cache nth(): virtual scrolling recycles/reorders these elements.
          const cards=await list.locator('crm-opportunity-card').elementHandles();
          try {
            for(const card of cards) {
              if(!await card.evaluate(matchesCard,expected))continue;
              const controls=await card.$$('[ng-click="ctrl.select()"]');
              try {
                for(const control of controls) {
                  if(!await control.isVisible())continue;
                  await control.scrollIntoViewIfNeeded({timeout:1500});
                  if(!await card.evaluate(matchesCard,expected))break;
                  await control.click({timeout:2500});
                  clicked=true;break;
                }
              } catch(error) {
                // Virtual rows can detach or remain clipped during the click. A timeout
                // must reach the identity-checked selection fallback below.
                if(!/Timeout|detached|not attached/i.test(String(error?.name)+' '+String(error?.message)))throw error;
                lastError=String(error.message).split('\n')[0];
              } finally { await Promise.all(controls.map(x=>x.dispose())); }
              if(!clicked) {
                // The virtual list can recycle the correct card while all click targets are
                // clipped. Re-verify both IDs before selecting it through the existing binding.
                clicked=await card.evaluate((e,expected)=>{
                  const c=window.angular?.element(e).controller('crmOpportunityCard');
                  const x=c?.opportunity;
                  const actual=[String(x?.dealershipId),String(x?.opportunityType).toLowerCase(),
                    String(x?.salesOpportunityId),String(x?.contactId)].join('|');
                  if(actual!==expected||typeof c?.onSelect!=='function')return false;
                  c.onSelect({driver:undefined});
                  return true;
                },expected);
              }
              if(clicked)break;
            }
          } finally { await Promise.all(cards.map(x=>x.dispose())); }
        }
        if(!clicked)throw new Error('Virtual row has no matching visible selection control: '+expected);
        await frame.waitForFunction(matchesDetail,detailIdentity,{timeout:Math.min(12000,Math.max(1000,Number(cfg.detail_timeout)||12000))});
        opened=true;
      } catch(error) {
        lastError=String(error.message).split('\n')[0];
      }
    }
    if(!opened)throw new Error('Could not open and verify '+expected+' after '+maxAttempts+' attempts: '+lastError);
    // The timeline loads independently from the contact/opportunity header.
    let timelineState = 'not_available';
    if(await frame.locator('crm-employee-dashboard-opportunity-view crm-activity-timeline').count()) {
      try {
        await frame.waitForFunction(contact=>{
          const e=document.querySelector('crm-employee-dashboard-opportunity-view crm-activity-timeline');
          const c=e&&angular.element(e).controller('crmActivityTimeline');
          return c&&c.isWaiting===false&&String(c.contact?.id)===String(contact);
        },todo.contactId,{timeout:10000});
        timelineState='ready';
      } catch { timelineState='timeout'; }
    }
    await frame.waitForTimeout(500);
    const record=await frame.locator('crm-employee-dashboard-opportunity-view').evaluate((view,todo)=>{
      const get=(tag,name)=>{const e=view.querySelector(tag);return e&&angular.element(e).controller(name);};
      const sales=get('crm-sales-opportunity-detail-view','crmSalesOpportunityDetailView');
      const service=get('ds-service-opportunity-container','dsServiceOpportunityContainer');
      const qa=get('csi-update','csiUpdate');
      const c=sales||service||qa;
      const s=sales?.salesOpportunity||service?.serviceOpportunity||qa?.csiEvent||{};
      const p=c?.contact||c?.owner||{};
      const actual=s.id||c?.opportunityId||c?.serviceOpportunityId;
      if(String(actual)!==String(todo.salesOpportunityId)||String(p.id)!==String(todo.contactId))
        throw new Error('Detail identity changed before extraction; refusing to export the wrong customer.');
      if(p.isRestricted||s.restricted) throw new Error('This record is restricted for the signed-in user; details were not exported.');
      const value=x=>x==null?'':typeof x==='object'?String(x.text??x.value??''):String(x);
      const cp=p.contactPoints||{};
      const mobile=value(cp.savedMobile?.value||cp._mobile?.value||cp.mobile?.value||'');
      const notes=[...view.querySelectorAll('[name="activityWorkNoteText"]')].map(x=>x.innerText||'');
      const submitted=notes.find(n=>/Information from the Submitted Lead/i.test(n))||'';
      const sourceLine=submitted.match(/(?:^|\n)Lead Source:\s*([^\n]+)/i)?.[1]||'';
      const vehicles=s.vehiclesOfInterest||[s.vehicle].filter(Boolean);
      const vehicle=vehicles.map(v=>[v.year,v.make,v.model,v.trim].filter(Boolean).join(' ')).filter(Boolean).join('; ')
        ||[todo.year,todo.make,todo.model].filter(Boolean).join(' ');
      const status=value(s.status)||s.statusText||todo.eventStatus||'';
      const franchise=value(s.franchise)||todo.franchiseName||'';
      const created=s.createdDate||'';
      const contactId=p.id||todo.contactId;
      return {
        opportunity_id:String(todo.salesOpportunityId||''),customer_id:String(contactId||''),
        opportunity_type:String(todo.opportunityType||''),
        dealership_id:String(s.dealershipId||s.dealership?.key||todo.dealershipId||''),
        rooftop:franchise,franchise_id:String(s.franchise?.key||todo.franchiseId||''),
        customer_name:p.fullName||todo.fullName||'',customer_phone_raw:mobile,
        customer_email_raw:value(cp.email?.value),vehicle_of_interest:vehicle,
        lead_source:sourceLine||value(s.trackingCode)||value(s.source),
        lead_source_basis:sourceLine?'submitted_lead_note':value(s.trackingCode)?'tracking_code':'opportunity_source',
        source_type:value(s.source),tracking_code:value(s.trackingCode),
        assigned_salesperson:value(s.primaryAssigned||s.advisor||s.assignTo)||todo.assignedTo||'',
        assigned_salesperson_id:String(s.primaryAssigned?.value||s.advisor?.value||s.assignTo?.value||''),
        received_timestamp:created,received_timestamp_basis:created?'opportunity.createdDate':'unavailable',
        last_updated_timestamp:s.lastUpdatedOrSoldDate||s.lastUpdate||'',
        opportunity_status:status,description:s.description||todo.description||'',
        customer_comments:submitted.match(/Customer Comments:\s*([\s\S]*?)(?:Information from the Submitted Lead:|$)/i)?.[1]?.trim()||'',
        customer_comments_basis:submitted?'loaded_submitted_lead_note':'unavailable_in_loaded_timeline',
        never_contact_via:value(p.neverContactVia),
        detail_extracted:true,adapter:sales?'sales':service?'service':'qa',
        model_id_present:!!s.id
      };
    },todo);
    if(record.customer_id!==String(todo.contactId)) throw new Error('Customer identity mismatch; refusing stale record.');
    record.source_url=target.url();
    record.timeline_load_status=timelineState;
    return {ok:true,record};
  }
  if(cfg.op==='restore') {
    await listView();
    await frame.locator('select[name="expanded-dealership-select"]').selectOption({label:cfg.site_name});
    await ready(cfg.site_id);
    const option=await list.evaluate((e,id)=>angular.element(e).controller('crmEmployeeDashboardListDetails')
      .categoryFilters.find(x=>String(x.id)===String(id))?.$$hashKey,cfg.filter_id);
    if(option){await frame.locator('select[name="filter-dropdown"]').selectOption(option);await ready(cfg.site_id,cfg.filter_id);}
    return {ok:true};
  }
  throw new Error('Unknown operation: '+cfg.op);
}
"""


class Chrome:
    def __init__(self, session, tool, code):
        self.session, self.tool, self.code = session, tool, code

    async def call(self, op, **config):
        js = self.code.replace("__CONFIG__", json.dumps({"op": op, **config}))
        return parse_result(await self.session.call_tool(self.tool, arguments={"code": js}))


def label_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def canonical_rooftop(label):
    key = label_key(label)
    for rooftop in ROOFTOPS:
        words = rooftop.split()
        for base in (" ".join(words), " ".join(reversed(words))):
            if key in {label_key(base + suffix) for suffix in ("", " New", " Used")}:
                return rooftop
    return ""


def source_identity(row):
    return (str(row.get("dealership_id") or row.get("dealershipId") or ""),
            str(row.get("franchise_id") or row.get("franchiseId") or ""))


def build_rooftop_map(rows, configured=None):
    mappings, conflicts = {}, set()
    for row in rows:
        label = row.get("rooftop_raw") or row.get("rooftop") or row.get("franchiseName") or ""
        name = canonical_rooftop(label)
        site, franchise = source_identity(row)
        if not name or not site or not franchise:
            continue
        key = site + ":" + franchise
        if key in mappings and mappings[key]["rooftop"] != name:
            conflicts.add(key)
        mappings[key] = dict(dealership_id=site, franchise_id=franchise, rooftop=name,
                             source_label=label, basis="explicit_crm_franchise_label")
    for key in conflicts:
        mappings.pop(key, None)
    for row in configured or []:
        if row.get("rooftop") not in ROOFTOPS or not row.get("dealership_id") or not row.get("franchise_id"):
            raise ValueError("Each rooftop-map entry needs dealership_id, franchise_id and one of the three scoped rooftop names.")
        key = str(row["dealership_id"]) + ":" + str(row["franchise_id"])
        if key in conflicts or key in mappings and mappings[key]["rooftop"] != row["rooftop"]:
            raise ValueError("Configured rooftop mapping conflicts with CRM evidence for " + key)
        mappings[key] = {**row, "basis": "configured_mapping", "source_label": row.get("source_label", "")}
    return mappings, conflicts


def scoped_rooftop(row, mappings, conflicts=()):
    site, franchise = source_identity(row)
    key = site + ":" + franchise
    if key in conflicts:
        return "", "conflicting_crm_entity_labels"
    label = row.get("rooftop_raw") or row.get("rooftop") or row.get("franchiseName") or ""
    direct = canonical_rooftop(label)
    mapped = mappings.get(key, {}).get("rooftop", "")
    if direct and mapped and direct != mapped:
        return "", "conflicting_crm_entity_labels"
    if mapped and not direct and label and label_key(label) not in {"kia", "mg", "geely", "new", "used"}:
        return "", "entity_id_conflicts_with_explicit_franchise_label"
    return (direct, "explicit_crm_franchise_label") if direct else (mapped, "verified_site_franchise_id") if mapped else ("", "unmapped_franchise")


def sales_candidate(row):
    kind = str(row.get("opportunity_type") or row.get("opportunityType") or "").lower()
    status = set(re.findall(r"[a-z]+", str(row.get("opportunity_status") or row.get("eventStatus") or "").lower()))
    return kind == "sales" and not status.intersection({"sold", "lost", "closed", "delivered", "deleted", "inactive"})


def target_filter(f):
    name = label_key(f["name"])
    # The short Geely queue label was observed in the supplied saved filters.
    # It is only a queue-selection hint, never proof of a record's rooftop.
    named = any(label_key(alias) in name for alias in
                ["Cranbourne Kia", "Kia Cranbourne", "Cranbourne MG", "MG Cranbourne",
                 "Geely Cranbourne", "Cranbourne Geely", "Geely Internet Leads"])
    category = ALIASES.get(f["category"], f["category"])
    return named, category == "Internet Traffic" or category == "Unsold Traffic" and named


def scope_row(raw, rooftop, basis, names, origin):
    row = normalize(raw, names)
    if origin == "seed_checkpoint" and raw.get("scraped_at"):
        row["scraped_at"] = raw["scraped_at"]
    row["dealership_name"] = row.get("dealership_name") or raw.get("dealership_name", "")
    row.update(rooftop_raw=raw.get("rooftop_raw") or raw.get("rooftop") or "",
               rooftop=rooftop, rooftop_mapping_basis=basis,
               new_lead_eligibility="not_verified_no_sms_authorized", snapshot_origin=origin)
    row["missing_fields"] = ";".join(k for k in REQUIRED if not row.get(k))
    return row


def load_scope_seed(path):
    if not path:
        return {}
    source = Path(path).resolve()
    if source.is_dir():
        source /= "checkpoint.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data.get("records"), dict):
        raise ValueError("Seed must be an existing scraper checkpoint or its run directory.")
    return data


def scope_state(args, output):
    source = load_scope_seed(args.resume or args.seed_run)
    configured = json.loads(Path(args.rooftop_map).read_text(encoding="utf-8")) if args.rooftop_map else []
    if not isinstance(configured, list):
        raise ValueError("--rooftop-map must contain a JSON list of entity mappings.")
    resume = bool(args.resume and source.get("mode") == "cranbourne-fast-v1" and not args.export_only)
    selection = dict(sites=getattr(args, "dealership", None), categories=getattr(args, "category", None) or FAST_CATEGORIES)
    if resume:
        if source.get("selection", selection) != selection:
            raise ValueError("Scoped resume selection differs from its saved run. Repeat the original --dealership/--category options.")
        state, folder = source, Path(args.resume).resolve()
        if folder.is_file():
            folder = folder.parent
        mappings, conflicts = build_rooftop_map(state["records"].values(), configured)
    else:
        folder = output / datetime.now(timezone.utc).strftime("cranbourne-%Y%m%dT%H%M%S%fZ")
        state = dict(mode="cranbourne-fast-v1", started_at=utcnow(), records={}, members={},
                     coverage={}, errors=[], error_history=[], catalog=[], review=[], status="running")
        mappings, conflicts = build_rooftop_map(source.get("records", {}).values(), configured)
        for key, raw in source.get("records", {}).items():
            rooftop, basis = scoped_rooftop(raw, mappings, conflicts)
            if rooftop and sales_candidate(raw):
                state["records"][key] = scope_row(raw, rooftop, basis, {}, "seed_checkpoint")
        if source:
            print(f"Reusing {len(state['records'])} scoped records from the old checkpoint; old files will not be changed.", flush=True)
    conflicts |= set(source.get("mapping_conflicts", []))
    previous = state.get("mappings", {})
    for key, entry in previous.items():
        if key not in conflicts and key not in mappings:
            mappings[key] = entry
    state.update(mappings=mappings, mapping_conflicts=sorted(conflicts), status="running", selection=selection)
    state.setdefault("review", [])
    folder.mkdir(parents=True, exist_ok=True)
    return state, folder, configured


def export_scope(folder, state):
    export_files(folder, state, [], [])
    by_record = defaultdict(list)
    for member in state["members"].values():
        by_record[member["record_key"]].append(member)
    rows = []
    for key, record in state["records"].items():
        matches = by_record[key]
        rows.append(record | dict(selected_dealerships=json.dumps(sorted({m["selected_dealership"] for m in matches})),
            categories=json.dumps(sorted({m["category"] for m in matches})),
            matched_filter_ids=json.dumps(sorted({str(m["selected_dealership_id"]) + ":" + str(m["filter_id"]) for m in matches}))))
    counts = {}
    for rooftop in ROOFTOPS:
        subset = [r for r in rows if r["rooftop"] == rooftop]
        write_csv(folder / (slug(rooftop) + ".csv"), FIELDS, subset)
        counts[rooftop] = {"records": len(subset),
            "complete_payload_records": sum(not r.get("missing_fields") for r in subset),
            "coverage": "matching_records_found" if subset else "no_matching_records_captured_not_proof_of_no_leads"}
    write_csv(folder / "rooftop_mappings.csv", ["dealership_id", "franchise_id", "rooftop", "source_label", "basis"], state["mappings"].values())
    write_csv(folder / "filter_catalog.csv", ["site", "site_id", "filter_id", "filter_name", "category", "selected", "reason"], state["catalog"])
    write_csv(folder / "scope_review.csv", ["site", "filter_id", "record_key", "franchise_label", "franchise_id", "reason"], state["review"])
    summary = dict(status=state["status"], mode=state["mode"], started_at=state["started_at"],
        exported_at=utcnow(), unique_records=len(rows), errors=len(state["errors"]),
        resolved_errors=len(state.get("error_history", [])), rooftops=counts,
        coverage_status=dict(Counter(c["status"] for c in state["coverage"].values())),
        scope_review_records=len(state["review"]), mapping_conflicts=state["mapping_conflicts"],
        scope="Accessible Internet Traffic filters and explicitly Cranbourne-named Unsold Traffic filters. No broad history scan.",
        eligibility="Candidate sales records only. New/reopened lead status, cutover date, received-time semantics and consent are not verified. No SMS authorization.",
        empty_file_note="A header-only rooftop CSV does not establish that the rooftop has no leads.")
    checkpoint(folder / "checkpoint.json", state)
    checkpoint(folder / "run_summary.json", summary)
    return summary


async def scoped_sync(chrome, args, selected_sites, categories, output):
    state, folder, configured = scope_state(args, output)
    print(f"SCOPED CSV output: {folder}", flush=True)
    original = None
    calls, consecutive, visited = 0, 0, 0
    limited = bool(args.max_filters or args.max_records)
    try:
        discovery = await chrome.call("discover")
        if discovery.get("keyword"):
            raise RuntimeError("Clear the DealerSocket keyword filter before scanning.")
        available = {x["name"]: x["id"] for x in discovery["sites"]}
        names = {x["id"]: x["name"] for x in discovery["sites"]}
        sites = selected_sites or list(available)
        if set(sites) - set(available):
            raise RuntimeError("Unavailable dealership selector: " + ", ".join(set(sites) - set(available)))
        original = dict(site_id=discovery["site"]["id"], site_name=discovery["site"]["name"], filter_id=discovery["filter"]["id"])
        jobs, state["catalog"] = [], []
        for site in sites:
            print(f"Reading filter names only: {site}", flush=True)
            try:
                meta = await chrome.call("site", name=site, site_id=available[site])
                resolve_errors(state, "catalog", site=site)
                for f in meta["filters"]:
                    named, allowed = target_filter(f)
                    category = ALIASES.get(f["category"], f["category"])
                    selected = allowed and category in categories
                    state["catalog"].append(dict(site=site, site_id=available[site], filter_id=f["id"],
                        filter_name=f["name"], category=category, selected=selected,
                        reason="scoped_sales_candidate_filter" if selected else "excluded_category_or_unrelated_queue"))
                    if selected:
                        jobs.append((not named, site, available[site], f, category))
            except Exception as error:
                state["errors"].append(dict(stage="catalog", site=site, error=error_message(error)))
        print(f"Selected {len(jobs)} lead filters; no QA, service, sold, lost or Revenue Radar detail scans.", flush=True)
        current_site = None
        for _, site, site_id, f, category in sorted(jobs, key=lambda j: (j[0], j[1], j[3]["id"])):
            coverage_key = site_id + ":" + str(f["id"])
            if state["coverage"].get(coverage_key, {}).get("status") == "complete":
                continue
            if args.check:
                continue
            if args.max_filters and visited >= args.max_filters:
                break
            visited += 1
            print(f"[{site}] {f['name']} — checking list before opening any details", flush=True)
            coverage = dict(selected_dealership_id=site_id, selected_dealership=site, category=category,
                filter_id=f["id"], filter_name=f["name"], status="running", detail_failures=0)
            state["coverage"][coverage_key] = coverage
            try:
                if current_site != site_id:
                    await chrome.call("site", name=site, site_id=site_id)
                    current_site = site_id
                data = await read_filter(chrome, site_id=site_id, filter_id=f["id"])
                resolve_errors(state, "filter", site=site, filter_id=f["id"])
                state["review"] = [r for r in state["review"]
                                   if not (r["site"] == site and str(r["filter_id"]) == str(f["id"]))]
                todos = data["todos"]
                mappings, conflicts = build_rooftop_map([*state["records"].values(), *todos], configured)
                for key, value in state["mappings"].items():
                    if key not in conflicts and key not in mappings:
                        mappings[key] = value
                state["mappings"], state["mapping_conflicts"] = mappings, sorted(conflicts | set(state["mapping_conflicts"]))
                candidates = []
                for todo in todos:
                    rooftop, basis = scoped_rooftop(todo, mappings, state["mapping_conflicts"])
                    if not rooftop:
                        label = todo.get("franchiseName") or ""
                        if not label or "conflict" in basis or any(x in label.lower() for x in ("cranbourne", "geely")):
                            review = dict(site=site, filter_id=f["id"], record_key=record_key(todo),
                                franchise_label=label, franchise_id=todo.get("franchiseId"), reason=basis)
                            if review not in state["review"]:
                                state["review"].append(review)
                        continue
                    if not sales_candidate(todo):
                        continue
                    key = record_key(todo)
                    candidates.append((key, todo, rooftop, basis))
                    state["members"][coverage_key + ":" + key] = dict(selected_dealership_id=site_id,
                        selected_dealership=site, category=category, filter_id=f["id"], filter_name=f["name"],
                        record_key=key, dealership_id=todo.get("dealershipId"), opportunity_type=todo.get("opportunityType"),
                        opportunity_id=todo.get("salesOpportunityId"), customer_id=todo.get("contactId"))
                    if key not in state["records"]:
                        state["records"][key] = scope_row(fallback(todo), rooftop, basis, names, "list_only")
                coverage.update(displayed_count=data["displayed_count"], loaded_count=len(todos), distinct_records=len(candidates))
                print(f"  {len(todos)} listed; {len(candidates)} scoped sales candidates; {len(todos)-len(candidates)} skipped without opening.", flush=True)
                for key, todo, rooftop, basis in candidates:
                    if state["records"][key].get("detail_extracted"):
                        continue
                    if args.max_records and calls >= args.max_records:
                        continue
                    calls += 1
                    print(f"  {rooftop}: opportunity {todo['salesOpportunityId']} ({calls} detail reads)", flush=True)
                    try:
                        result = await chrome.call("detail", todo=todo, site_id=site_id, filter_id=f["id"], max_attempts=1, detail_timeout=6000)
                        raw = result["record"]
                        actual, actual_basis = scoped_rooftop(raw, mappings, state["mapping_conflicts"])
                        if actual != rooftop:
                            raise RuntimeError("Detail rooftop did not match verified list rooftop; record needs review.")
                        state["records"][key] = scope_row(raw, actual, actual_basis, names, "live_detail")
                        resolve_errors(state, "detail", record_key=key)
                        consecutive = 0
                    except Exception as error:
                        consecutive += 1
                        coverage["detail_failures"] += 1
                        state["errors"].append(dict(stage="detail", site=site, filter_id=f["id"], record_key=key, error=error_message(error)))
                        print("  Detail failed: " + error_message(error).splitlines()[0][:220], flush=True)
                        if consecutive >= args.fail_fast_after:
                            raise RuntimeError("Stopping after repeated detail failures; checkpoint saved. Inspect the selector before retrying.")
                    if calls % 10 == 0:
                        export_scope(folder, state)
                    if args.delay:
                        await asyncio.sleep(args.delay)
                coverage["status"] = "partial" if any(not state["records"][k].get("detail_extracted") for k, *_ in candidates) else "complete"
            except Exception as error:
                coverage.update(status="partial", error=error_message(error))
                state["errors"].append(dict(stage="filter", site=site, filter_id=f["id"], error=error_message(error)))
                print("  Filter incomplete: " + error_message(error).splitlines()[0][:220], flush=True)
            export_scope(folder, state)
            if consecutive >= args.fail_fast_after:
                break
        missing_rooftops = any(not any(r["rooftop"] == name for r in state["records"].values()) for name in ROOFTOPS)
        state["status"] = ("catalog_only" if args.check else "limited" if limited else "partial"
            if state["errors"] or state["review"] or not jobs or any(not r.get("detail_extracted") for r in state["records"].values())
            else "needs_rooftop_verification" if missing_rooftops else "complete_selected_filters")
    except BaseException:
        state["status"] = "interrupted"
        raise
    finally:
        print(json.dumps(export_scope(folder, state), indent=2), flush=True)
        if original:
            try:
                await chrome.call("restore", **original)
            except Exception:
                print("Could not restore the original UI filter; scoped CSVs are saved.", flush=True)
    return 0 if state["status"] in ("complete_selected_filters", "catalog_only") else 2


async def sync(chrome, args, selected_sites, categories, output):
    discovery = await chrome.call("discover")
    if discovery.get("keyword"):
        raise RuntimeError("Clear the DealerSocket keyword filter before scanning.")
    available = {x["name"]: x["id"] for x in discovery["sites"]}
    missing = set(selected_sites) - set(available)
    if missing:
        raise RuntimeError("Dealerships unavailable to this Chrome user: " + ", ".join(sorted(missing)))
    names_by_id = {x["id"]: x["name"] for x in discovery["sites"]}
    original = {"site_id": discovery["site"]["id"], "site_name": discovery["site"]["name"],
                "filter_id": discovery["filter"]["id"]}
    if args.check:
        inventory = []
        try:
            for name in selected_sites:
                m = await chrome.call("site", name=name, site_id=available[name])
                counts = Counter(ALIASES.get(f["category"], f["category"]) for f in m["filters"])
                item = {"dealership": name, "id": available[name], "filters_by_category": dict(counts)}
                inventory.append(item)
                print(json.dumps(item), flush=True)
        finally:
            await chrome.call("restore", **original)
        print("CHECK PASSED: signed-in Chrome and dealership filters accessible. No records exported.")
        return 0

    folder = Path(args.resume).resolve() if args.resume else output / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "checkpoint.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if args.resume else {
        "started_at": utcnow(), "records": {}, "members": {}, "coverage": {}, "errors": [],
        "selection": {"sites": selected_sites, "categories": categories}, "status": "running"}
    if state.get("selection") != {"sites": selected_sites, "categories": categories}:
        raise RuntimeError("Resume selection differs from the saved run. Use its original --dealership/--category options.")
    state.setdefault("error_history", [])
    state["status"] = "running"
    print(f"CSV output: {folder}", flush=True)
    calls, filter_count, limited = 0, 0, bool(args.max_filters or args.max_records)
    try:
        for name in selected_sites:
            site_id = available[name]
            meta = await chrome.call("site", name=name, site_id=site_id)
            filters = [f for f in meta["filters"] if ALIASES.get(f["category"], f["category"]) in categories]
            found_categories = {ALIASES.get(f["category"], f["category"]) for f in filters}
            for absent in set(categories) - found_categories:
                state["coverage"][site_id + ":category:" + absent] = {
                    "selected_dealership_id": site_id, "selected_dealership": name, "category": absent,
                    "status": "category_unavailable", "error": "No accessible saved filter in this category."}
            for f in filters:
                category = ALIASES.get(f["category"], f["category"])
                coverage_key = site_id + ":" + f["id"]
                if state["coverage"].get(coverage_key, {}).get("status") == "complete":
                    continue
                if args.max_filters and filter_count >= args.max_filters:
                    limited = True
                    break
                filter_count += 1
                coverage = {"selected_dealership_id": site_id, "selected_dealership": name,
                            "category": category, "filter_id": f["id"], "filter_name": f["name"],
                            "is_my_filter": f["is_my_filter"], "is_enterprise": f["is_enterprise"],
                            "status": "running", "detail_failures": 0}
                state["coverage"][coverage_key] = coverage
                print(f"[{name}] {category} / {f['name']}", flush=True)
                try:
                    result = await read_filter(chrome, site_id=site_id, filter_id=f["id"])
                    resolve_errors(state, "filter", site=name, filter_id=f["id"])
                    todos = result["todos"]
                    coverage.update(displayed_count=result["displayed_count"], loaded_count=len(todos),
                                    distinct_records=len({record_key(t) for t in todos}))
                    print(f"  {len(todos)} rows; reading each distinct opportunity", flush=True)
                    for todo in todos:
                        key = record_key(todo)
                        membership = {"selected_dealership_id": site_id, "selected_dealership": name,
                                      "category": category, "filter_id": f["id"], "filter_name": f["name"],
                                      "record_key": key, "dealership_id": str(todo.get("dealershipId") or ""),
                                      "opportunity_type": todo.get("opportunityType"),
                                      "opportunity_id": str(todo.get("salesOpportunityId") or ""),
                                      "customer_id": str(todo.get("contactId") or "")}
                        state["members"][coverage_key + ":" + key] = membership
                        old = state["records"].get(key)
                        if old and old.get("detail_extracted"):
                            resolve_errors(state, "detail", record_key=key)
                            continue
                        if args.max_records and calls >= args.max_records:
                            limited = True
                            if old is None:
                                state["records"][key] = normalize(fallback(todo), names_by_id)
                            continue
                        calls += 1
                        print(f"    {todo.get('opportunityType')} ID {todo.get('salesOpportunityId')} ({calls} details this invocation)", flush=True)
                        try:
                            data = await chrome.call("detail", todo=todo, site_id=site_id, filter_id=f["id"])
                            state["records"][key] = normalize(data["record"], names_by_id)
                            resolve_errors(state, "detail", record_key=key)
                        except Exception as exc:
                            coverage["detail_failures"] += 1
                            state["records"][key] = normalize(fallback(todo), names_by_id)
                            state["errors"].append({"stage": "detail", "site": name, "filter_id": f["id"],
                                                    "record_key": key, "error": error_message(exc)})
                            print(f"    Detail failed: {error_message(exc).splitlines()[0][:220]}", flush=True)
                        if calls % 20 == 0:
                            checkpoint(state_path, state)
                            export_files(folder, state, selected_sites, categories)
                        if args.delay:
                            await asyncio.sleep(args.delay)
                    any_unread = any(not state["records"].get(record_key(t), {}).get("detail_extracted") for t in todos)
                    coverage["status"] = "partial" if any_unread else "complete"
                except Exception as exc:
                    coverage.update(status="failed", error=error_message(exc))
                    state["errors"].append({"stage": "filter", "site": name, "filter_id": f["id"], "error": error_message(exc)})
                    print(f"  Filter failed: {str(exc).splitlines()[0][:180]}", flush=True)
                checkpoint(state_path, state)
                export_files(folder, state, selected_sites, categories)
            if args.max_filters and filter_count >= args.max_filters:
                break
        incomplete = any(x["status"] in ("failed", "partial", "running") for x in state["coverage"].values())
        state["status"] = "limited" if limited else "partial" if incomplete else "complete_available_filters"
    except BaseException:
        state["status"] = "interrupted"
        raise
    finally:
        checkpoint(state_path, state)
        summary = export_files(folder, state, selected_sites, categories)
        print(json.dumps(summary, indent=2), flush=True)
        try:
            await chrome.call("restore", **original)
        except Exception:
            print("Could not restore the original filter; CSV files and checkpoint are saved.", file=sys.stderr)
    return 0 if state["status"] == "complete_available_filters" else 2


async def run(args):
    if args.export_only:
        output = Path(args.output or os.getenv("DEALERSOCKET_OUTPUT_DIR", str(BASE_DIR / "downloads"))).resolve()
        state, folder, _ = scope_state(args, output)
        state["status"] = "offline_export_only_coverage_not_verified"
        print(f"Offline scoped CSV output: {folder}", flush=True)
        print(json.dumps(export_scope(folder, state), indent=2), flush=True)
        return 0
    from dotenv import load_dotenv
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client
    load_dotenv(args.env_file)
    token = os.getenv("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "").strip()
    if not token or token.startswith(("http://", "https://")):
        raise RuntimeError("Set PLAYWRIGHT_MCP_EXTENSION_TOKEN to the extension token from the signed-in Chrome profile.")
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    local_cli = os.getenv("PLAYWRIGHT_MCP_CLI", "").strip()
    node = shutil.which("node")
    if local_cli and (not Path(local_cli).is_file() or not node):
        raise RuntimeError("PLAYWRIGHT_MCP_CLI must name an installed MCP cli.js file, and Node.js must be on PATH.")
    if not local_cli and not npx:
        raise RuntimeError("Node.js/npx is missing. Install Node.js LTS and reopen PowerShell.")
    code = BROWSER_CODE
    selected_sites = args.dealership or (DEALERSHIPS if args.all_rooftops else None)
    categories = args.category or (CATEGORIES if args.all_rooftops else FAST_CATEGORIES)
    output = Path(args.output or os.getenv("DEALERSOCKET_OUTPUT_DIR", str(BASE_DIR / "downloads"))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAYWRIGHT_", "DEALERSOCKET_"))}
    env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = token
    profile = os.getenv("PLAYWRIGHT_MCP_PROFILE_DIR_NAME")
    if profile:
        env["PLAYWRIGHT_MCP_PROFILE_DIR_NAME"] = profile
    command = node if local_cli else npx
    server_args = [local_cli] if local_cli else ["-y", "@playwright/mcp@0.0.83"]
    params = StdioServerParameters(command=command,
        args=server_args + ["--extension", "--browser", "chrome", "--output-dir", str(output)],
        cwd=str(BASE_DIR), env=env)
    print("Connecting to the existing Google Chrome session...", flush=True)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120),
            client_info=types.Implementation(name="dealersocket-csv-sync", version="1.3.0")) as session:
            await session.initialize()
            print("MCP session initialized.", flush=True)
            names = {t.name for t in (await session.list_tools()).tools}
            tool = next((x for x in ("browser_run_code_unsafe", "browser_run_code") if x in names), None)
            if not tool:
                raise RuntimeError("The MCP server lacks the required Playwright code tool.")
            await attach_dealersocket_tab(session)
            if args.diagnose:
                result = await Chrome(session, tool, code).call("discover")
                print("CONNECTION PASSED: DealerSocket accessible in Chrome; "
                      f"{len(result['sites'])} dealerships available. No CSV export requested.", flush=True)
                return 0
            implementation = sync if args.all_rooftops else scoped_sync
            return await implementation(Chrome(session, tool, code), args, selected_sites, categories, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check all selected dealership filter catalogs only.")
    mode.add_argument("--diagnose", action="store_true", help="Test Chrome/tab access only, without scanning or exporting.")
    mode.add_argument("--download", action="store_true", help="Compatibility alias for the default CSV scrape.")
    mode.add_argument("--export-only", action="store_true", help="Export scoped rows from --seed-run/--resume without Chrome or network access.")
    parser.add_argument("--env-file", type=Path, default=BASE_DIR / ".env")
    parser.add_argument("--output", help="CSV output parent directory.")
    parser.add_argument("--resume", help="Continue an interrupted run directory, using its cached snapshot.")
    parser.add_argument("--seed-run", help="Reuse saved scoped details from an old run; write new Cranbourne output, leaving old files untouched.")
    parser.add_argument("--rooftop-map", help="Optional JSON list of explicit dealership_id/franchise_id/rooftop mappings; no guessed IDs.")
    parser.add_argument("--all-rooftops", action="store_true", help="Explicit legacy broad scan, including unrelated dealerships/categories. Not recommended for the scoped project.")
    parser.add_argument("--dealership", action="append", help="Restrict source site selectors; default discover their filter names only.")
    parser.add_argument("--category", action="append", choices=CATEGORIES, help="Default Internet Traffic plus Cranbourne-named Unsold Traffic queues.")
    parser.add_argument("--fail-fast-after", type=int, default=3, help="Stop scoped extraction after this many consecutive detail failures; default 3.")
    parser.add_argument("--max-filters", type=int, default=0, help="Diagnostic limit; 0 means all filters.")
    parser.add_argument("--max-records", type=int, default=0, help="Diagnostic detail-call limit; 0 means all records.")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds between detail visits.")
    args = parser.parse_args()
    if args.max_filters < 0 or args.max_records < 0 or args.delay < 0:
        parser.error("Limits and delay must be nonnegative.")
    if args.fail_fast_after < 1:
        parser.error("--fail-fast-after must be at least 1.")
    if args.resume and args.seed_run:
        parser.error("Use either --resume or --seed-run, not both.")
    if args.export_only and not (args.resume or args.seed_run):
        parser.error("--export-only requires --seed-run or --resume.")
    if args.all_rooftops and (args.seed_run or args.export_only):
        parser.error("--seed-run and --export-only are for scoped mode, not --all-rooftops.")
    if not args.all_rooftops and args.category and set(args.category) - set(FAST_CATEGORIES):
        parser.error("Scoped mode only scans Internet Traffic and named Unsold Traffic. Other categories require --all-rooftops.")
    try:
        code = asyncio.run(run(args))
    except ModuleNotFoundError:
        raise SystemExit('Install dependencies: python -m pip install "mcp>=1.28,<2" python-dotenv')
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        message = redact("\n".join(exception_details(exc)))
        print("ERROR: " + message, file=sys.stderr)
        if "did not connect" in message or "extension" in message.lower() and "timeout" in message.lower():
            print("Open the Playwright extension in the signed-in Chrome profile and use its current token. "
                  "If prompted, approve the client named dealersocket-csv-sync. "
                  "A pip update will not fix this handoff error.", file=sys.stderr)
        try:
            output = Path(args.output or os.getenv("DEALERSOCKET_OUTPUT_DIR", str(BASE_DIR / "downloads"))).resolve()
            output.mkdir(parents=True, exist_ok=True)
            log = output / datetime.now(timezone.utc).strftime("connection-error-%Y%m%dT%H%M%S%fZ.log")
            log.write_text(redact("".join(traceback.format_exception(exc))), encoding="utf-8")
            print(f"Full error details saved to: {log}", file=sys.stderr)
        except OSError:
            pass
        raise SystemExit(1) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
