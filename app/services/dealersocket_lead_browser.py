"""Read-only Playwright adapter for the existing DealerSocket Opportunities UI.

Only navigation, saved-filter selection, opportunity selection and model reads.
No arbitrary scripts/URLs from callers, CRM write APIs or save/contact actions.
"""
import asyncio
import json
from urllib.parse import urlparse

URL = "https://bb.dealersocket.com/#/crm/employee?todoType=opportunities"
TAG = "crm-employee-dashboard-list-details"
CTRL = "window.angular?.element(e).controller('crmEmployeeDashboardListDetails')"
META = """e => {
 const c=window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
 if (!c?.selectedDealership || !c.selectedFilter) throw Error('component_unavailable');
 return {sites:c.dealerships.map(x=>({id:String(x.value),name:x.text})),
 site:{id:String(c.selectedDealership.value),name:c.selectedDealership.text},
 filter:{id:String(c.selectedFilter.id),name:c.selectedFilter.name},keyword:c.searchText||'',
 filters:c.categoryFilters.map(x=>({id:String(x.id),name:x.name,category:x.filterCategory,
 site_id:String(x.siteId),key:x.$$hashKey}))};
} """
LIST = r"""e => {
 const c=window.angular.element(e).controller('crmEmployeeDashboardListDetails');
 const keys=['dealershipId','activityId','franchiseId','contactId','salesOpportunityId','fullName',
 'opportunityType','eventStatus','franchiseName','condition','year','make','model','assignedTo',
 'activityDateTime','description','lastUpdate'];
 const labels=[...e.querySelectorAll('[name="results-display"]')].map(x=>(x.textContent||'').trim())
 .filter(x=>/^\d[\d,]* RESULTS? FOUND$/i.test(x));
 return {todos:(c.todos||[]).map(x=>Object.fromEntries(keys.map(k=>[k,x[k]??null]))),
 displayed_count:labels.length?Number(labels[0].replace(/[^0-9]/g,'')):null,keyword:c.searchText||''};
} """
SELECT = """(e,a) => {
 const c=window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
 const key=x=>[x.dealershipId,x.opportunityType,x.salesOpportunityId,x.contactId].map(x=>String(x).toLowerCase()).join('|');
 if(!c || c.isLoadingTodos || String(c.selectedDealership?.value)!==a.site || String(c.selectedFilter?.id)!==a.filter)
 throw Error('queue_changed');
 const row=c.todos.find(x=>key(x)===key(a.todo));
 if(!row || typeof c.changeTodo!=='function') throw Error('selection_unavailable');
 c.changeTodo(row,undefined);
} """
DETAIL_READY = """a => {
 const e=document.querySelector('crm-employee-dashboard-opportunity-view crm-sales-opportunity-detail-view');
 const c=e && window.angular?.element(e).controller('crmSalesOpportunityDetailView');
 return !!c && String(c.salesOpportunity?.id||c.opportunityId)===String(a.salesOpportunityId)
 && String(c.contact?.id)===String(a.contactId) && !c.isLoading && !(c.numberItemsStillLoading>0);
} """
DETAIL = """(view,todo) => {
 const el=view.querySelector('crm-sales-opportunity-detail-view');
 const c=el && window.angular?.element(el).controller('crmSalesOpportunityDetailView');
 const s=c?.salesOpportunity||{}, p=c?.contact||{};
 if(String(s.id||c?.opportunityId)!==String(todo.salesOpportunityId)||String(p.id)!==String(todo.contactId))
 throw Error('identity_mismatch');
 if(p.isRestricted||s.restricted) return {restricted:true};
 const value=x=>x==null?'':typeof x==='object'?String(x.text??x.value??''):String(x);
 const cp=p.contactPoints||{}, vehicles=s.vehiclesOfInterest||[s.vehicle].filter(Boolean);
 return {opportunity_id:String(todo.salesOpportunityId),customer_id:String(p.id),opportunity_type:'sales',
 dealership_id:String(s.dealershipId||s.dealership?.key||todo.dealershipId||''),
 franchise_id:String(s.franchise?.key||todo.franchiseId||''),rooftop:value(s.franchise)||todo.franchiseName||'',
 customer_name:p.fullName||todo.fullName||'',customer_phone_raw:value(cp.savedMobile?.value||cp._mobile?.value||cp.mobile?.value),
 customer_email_raw:value(cp.email?.value),vehicle_of_interest:vehicles.map(v=>[v.year,v.make,v.model,v.trim].filter(Boolean).join(' ')).filter(Boolean).join('; ')
 ||[todo.year,todo.make,todo.model].filter(Boolean).join(' '),
 lead_source:value(s.trackingCode)||value(s.source),lead_source_basis:'tracking_code_or_opportunity_source',
 assigned_salesperson:value(s.primaryAssigned||s.advisor||s.assignTo)||todo.assignedTo||'',
 received_timestamp:s.createdDate||'',received_timestamp_basis:s.createdDate?'opportunity.createdDate':'unavailable',
 last_updated_timestamp:s.lastUpdatedOrSoldDate||s.lastUpdate||'',opportunity_status:value(s.status)||s.statusText||todo.eventStatus||'',
 never_contact_via:value(p.neverContactVia),detail_extracted:true,model_id_present:!!s.id};
} """


class LeadBrowser:
    def __init__(self, page):
        self.page, self.frame = page, None

    async def prepare(self):
        await self.page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        for _ in range(90):
            if urlparse(self.page.url).hostname != "bb.dealersocket.com":
                raise PermissionError("login_required")
            for frame in self.page.frames:
                host = urlparse(frame.url).hostname or ""
                if host != "dealersocket.com" and not host.endswith(".dealersocket.com"):
                    continue
                try:
                    ready = await frame.locator(TAG).evaluate(
                        "e=>{const c=" + CTRL + ";return !!(c?.dealerships?.length&&c.selectedFilter&&c.categoryFilters?.length);}", timeout=500)
                    if ready:
                        self.frame = frame
                        await self.list_view()
                        meta = await self.meta()
                        await self.settle(meta["site"]["id"])
                        return await self.meta()
                except Exception:
                    continue
            await asyncio.sleep(.5)
        raise RuntimeError("opportunities_not_ready")

    async def meta(self):
        return await self.frame.locator(TAG).evaluate(META)

    async def list_view(self):
        if not await self.frame.locator('select[name="filter-dropdown"]').is_visible():
            expand = self.frame.locator('button[name="expandButton"]')
            if await expand.is_visible():
                await expand.evaluate("e=>{if(!e.disabled)e.click();}")
        await self.frame.locator('select[name="filter-dropdown"]').wait_for(state="visible", timeout=10000)

    async def settle(self, site, filter_id=None):
        await self.frame.wait_for_function("""a=>{
          const e=document.querySelector('crm-employee-dashboard-list-details');
          const c=e&&window.angular?.element(e).controller('crmEmployeeDashboardListDetails');
          return c&&!c.isLoadingTodos&&String(c.selectedDealership?.value)===a.site
          &&String(c.selectedFilter?.siteId)===a.site&&c.categoryFilters?.length>0
          &&c.categoryFilters.every(x=>String(x.siteId)===a.site)
          &&(a.filter===null||String(c.selectedFilter?.id)===a.filter);
        }""", arg={"site": str(site), "filter": str(filter_id) if filter_id is not None else None},
        timeout=30000, polling=250)
        previous, stable = None, 0
        for _ in range(60):
            fingerprint = await self.frame.locator(TAG).evaluate("""e=>{
             const c=window.angular.element(e).controller('crmEmployeeDashboardListDetails');
             return JSON.stringify([!!c.isLoadingTodos,c.selectedDealership?.value,c.selectedFilter?.id,
             (c.todos||[]).map(x=>[x.dealershipId,x.salesOpportunityId,x.contactId])]);} """)
            stable = stable + 1 if previous == fingerprint and not json.loads(fingerprint)[0] else 0
            if stable >= 3:
                return
            previous = fingerprint
            await asyncio.sleep(.25)
        raise RuntimeError("queue_not_settled")

    async def site(self, site):
        await self.list_view()
        await self.frame.locator('select[name="expanded-dealership-select"]').select_option(label=site["name"])
        await self.settle(site["id"])
        return await self.meta()

    async def read_filter(self, site, item):
        await self.list_view()
        meta = await self.meta()
        if meta["site"]["id"] != str(site["id"]):
            await self.site(site)
            meta = await self.meta()
        option = next((x for x in meta["filters"] if x["id"] == str(item["id"])), None)
        if not option or not option.get("key"):
            raise RuntimeError("queue_unavailable")
        await self.frame.locator('select[name="filter-dropdown"]').select_option(value=option["key"])
        await self.settle(site["id"], item["id"])
        return await self.frame.locator(TAG).evaluate(LIST)

    async def detail(self, site, item, todo):
        await self.frame.locator(TAG).evaluate(SELECT, {"site":str(site["id"]),"filter":str(item["id"]),"todo":todo})
        await self.frame.wait_for_function(DETAIL_READY, arg=todo, timeout=15000, polling=250)
        return await self.frame.locator('crm-employee-dashboard-opportunity-view').evaluate(DETAIL, todo)

    async def restore(self, original):
        await self.site(original["site"])
        await self.read_filter(original["site"], original["filter"])
