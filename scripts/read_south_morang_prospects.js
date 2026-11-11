async (page) => {
  const sites = [['10755','South Morang Kia'],['10876','South Morang Hyundai']];
  const filtersBySite = {'10755':[[128,'New Cars - Total Active Prospects'],[283,'USED - Active Leads']], '10876':[[125,'HY - Active Leads NO FOLLOW UP'],[69,'Used Cars - Total Active Prospects']]};
  const coverage = [], records = new Map();
  for (const [site, name] of sites) {
    const roof = page.locator('md-select#select_1');
    if ((await roof.innerText()).trim() !== name) {
      const backdrop = page.locator('.ds-side-nav-backdrop:visible');
      if (await backdrop.count()) await backdrop.evaluate(e => e.click());
      await roof.click({timeout:5000});
      await page.getByRole('option',{name,exact:true}).click({timeout:5000});
      await page.waitForURL('**/#/homepage');
      await page.locator('#app-loading-overlay').waitFor({state:'hidden'});
    }
    await page.goto('https://bb.dealersocket.com/#/crm/employee?todoType=opportunities&dealershipId='+site);
    const f = page.frameLocator('#app-crm');
    const select = f.locator('select[name="expanded-dealership-select"]');
    if (!await select.isVisible()) await f.locator('button[name="expandButton"]').evaluate(e => e.click());
    if ((await select.locator('option:checked').innerText()).trim() !== name) await select.selectOption({label:name});
    await f.locator('select[name="filter-dropdown"]').locator('option').filter({hasText:filtersBySite[site][0][1]}).waitFor({state:'attached'});
    const list = f.locator('crm-employee-dashboard-list-details');
    for (const [id, label] of filtersBySite[site]) {
      await f.locator('select[name="filter-dropdown"]').selectOption({label,},{timeout:10000});
      await page.waitForTimeout(1800);
      const result = await list.evaluate((e,{site,id}) => new Promise((resolve,reject) => {
        const started = Date.now();
        const timer = setInterval(() => {
          const c = window.angular.element(e).controller('crmEmployeeDashboardListDetails');
          if (String(c?.selectedDealership?.value) === site && Number(c.selectedFilter?.id) === id && !c.isLoadingTodos && !c.searchText) {
            clearInterval(timer);
            const displayed = [...e.querySelectorAll('[name="results-display"]')].map(x=>x.textContent.trim()).find(x=>/^[\d,]+\s+results? found$/i.test(x));
            resolve({total:displayed ? Number(displayed.match(/^[\d,]+/)[0].replace(/,/g,'')) : null, rows:(c.todos||[]).map(t=>({site:String(t.dealershipId),id:String(t.salesOpportunityId),contactId:String(t.contactId),franchise:t.franchiseName,name:t.fullName,status:t.eventStatus,assignee:t.assignedTo,vehicle:[t.year,t.make,t.model].filter(Boolean).join(' '),type:t.opportunityType}))});
          } else if (Date.now()-started > 30000) {
            clearInterval(timer);
            reject(Error('DealerSocket filter did not finish loading'));
          }
        },200);
      }), {site,id});
      if (result.total !== result.rows.length) throw Error(name+' / '+label+': loaded rows do not match the reported total');
      const scoped = result.rows.filter(row => row.site === site && String(row.type).toLowerCase() === 'sales');
      coverage.push({site,filterId:id,count:result.total,scoped:scoped.length});
      for (const {type,...row} of scoped) {
        if (!/^\d+$/.test(row.id)||!/^\d+$/.test(row.contactId)) throw Error('Missing prospect identifier in '+name+' / '+label);
        records.set(row.site+'|'+row.id,row);
      }
    }
  }
  return {capturedAt:new Date().toISOString(),coverage,records:[...records.values()]};
}
