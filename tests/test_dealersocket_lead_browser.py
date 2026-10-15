"""Installed Chrome read adapter fixture. Every request intercepted, no live CRM."""
import os
from pathlib import Path
import unittest

from app.models.dealersocket_leads import LeadReadOptions
from app.services.dealersocket_lead_browser import LeadBrowser
from app.services.dealersocket_leads import collect_snapshot


FIXTURE = r'''<html><body>
<select name="expanded-dealership-select"><option value="100">Fixture Site</option></select>
<select name="filter-dropdown"><option value="queue:1">Cranbourne Kia Internet Leads UNASSIGNED</option></select>
<crm-employee-dashboard-list-details><div name="results-display">3 RESULTS FOUND</div></crm-employee-dashboard-list-details>
<crm-employee-dashboard-opportunity-view></crm-employee-dashboard-opportunity-view>
<script>
const roofs=['Cranbourne Kia','Cranbourne MG','Geely Cranbourne'];
const rows=roofs.map((name,i)=>({dealershipId:'100',franchiseId:String(i),salesOpportunityId:String(i+10),contactId:String(i+100),
fullName:'Synthetic Customer',opportunityType:'sales',eventStatus:'Open',franchiseName:name}));
const c={dealerships:[{value:'100',text:'Fixture Site'}],selectedDealership:{value:'100',text:'Fixture Site'},
selectedFilter:{id:'1',siteId:'100',name:'Cranbourne Kia Internet Leads UNASSIGNED'},searchText:'',
categoryFilters:[{id:'1',siteId:'100',name:'Cranbourne Kia Internet Leads UNASSIGNED',filterCategory:'Internet Traffic',$$hashKey:'queue:1'}],
isLoadingTodos:false,todos:rows,
changeTodo(todo){
 const view=document.querySelector('crm-employee-dashboard-opportunity-view');
 view.innerHTML='<crm-sales-opportunity-detail-view></crm-sales-opportunity-detail-view>';
 view.firstElementChild.controller={isLoading:false,numberItemsStillLoading:0,
 salesOpportunity:{id:todo.salesOpportunityId,dealershipId:'100',franchise:{key:todo.franchiseId,text:todo.franchiseName},
 source:{text:'Website'},createdDate:'2026-10-15T09:00:00+11:00',status:{text:'Open'},vehiclesOfInterest:[{make:'Kia',model:'Fixture'}]},
 contact:{id:todo.contactId,fullName:todo.fullName,contactPoints:{mobile:{value:'0412345678'},email:{value:'fixture@example.test'}}}};
}};
window.angular={element:e=>({controller:name=>name==='crmEmployeeDashboardListDetails'?c:e.controller})};
window.fixtureSaveCalls=0;
</script></body></html>'''


@unittest.skipUnless(os.getenv('RUN_CONNECTOR_BROWSER_TEST')=='1','Opt-in installed Chrome fixture')
class LeadBrowserFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_reads_three_rooftops_and_details_without_writes(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page = await browser.new_page()
                async def fixture(route):
                    body = FIXTURE if route.request.url.endswith('/fixture-frame') else '<iframe src="https://bb.dealersocket.com/fixture-frame"></iframe>'
                    await route.fulfill(status=200,content_type='text/html',body=body)
                await page.route('**/*',fixture)
                result = await collect_snapshot(LeadBrowser(page),LeadReadOptions())
                self.assertEqual(result['state'],'complete',result['errors'])
                self.assertEqual(len(result['records']),3)
                self.assertTrue(all(row['detail_extracted'] for row in result['records']))
                self.assertTrue(all(row['customer_phone']=='+61412345678' for row in result['records']))
                self.assertEqual(await page.frames[1].evaluate('window.fixtureSaveCalls'),0)
            finally:
                await browser.close()
