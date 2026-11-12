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
    await f.locator('crm-employee-dashboard-list-details').waitFor({timeout:30000});
    const select = f.locator('select[name="expanded-dealership-select"]');
    if (!await select.isVisible()) {
      const expand = f.locator('button[name="expandButton"]');
      if (await expand.count()) await expand.evaluate(e => e.click());
    }
    await select.waitFor({state:'attached',timeout:10000});
    if ((await select.locator('option:checked').innerText()).trim() !== name) await select.selectOption({label:name});
    await f.locator('select[name="filter-dropdown"]').locator('option').filter({hasText:filtersBySite[site][0][1]}).waitFor({state:'attached'});
    const frame = await (await page.locator('#app-crm').elementHandle()).contentFrame();
    const list = f.locator('crm-employee-dashboard-list-details');
    for (const [id, label] of filtersBySite[site]) {
      const filter = f.locator('select[name="filter-dropdown"]');
      if (!await filter.isVisible()) {
        await f.locator('button[name="expandButton"]').evaluate(button => button.click());
      }
      await filter.selectOption({label},{timeout:10000});
      await page.waitForTimeout(1800);
      const result = await list.evaluate((e,{site,id}) => new Promise((resolve,reject) => {
        const started = Date.now();
        const timer = setInterval(() => {
          const c = window.angular.element(e).controller('crmEmployeeDashboardListDetails');
          if (String(c?.selectedDealership?.value) === site && Number(c.selectedFilter?.id) === id && !c.isLoadingTodos && !c.searchText) {
            clearInterval(timer);
            const displayed = [...e.querySelectorAll('[name="results-display"]')].map(x=>x.textContent.trim()).find(x=>/^[\d,]+\s+results? found$/i.test(x));
            resolve({total:displayed ? Number(displayed.match(/^[\d,]+/)[0].replace(/,/g,'')) : null, rows:(c.todos||[]).map(t=>({site:String(t.dealershipId),id:String(t.salesOpportunityId),contactId:String(t.contactId),franchise:t.franchiseName,name:t.fullName,status:t.eventStatus,assignee:typeof t.assignedTo === 'object' ? (t.assignedTo?.text || t.assignedTo?.fullName || t.assignedTo?.name || '') : t.assignedTo,vehicle:[t.year,t.make,t.model].filter(Boolean).join(' '),type:t.opportunityType}))});
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
        const key = row.site + '|' + row.id;
        if (records.has(key)) continue;
        if (site === '10876') {
          await list.evaluate((element, row) => {
            const controller = window.angular.element(element).controller('crmEmployeeDashboardListDetails');
            const opportunity = controller.todos.find(todo =>
              String(todo.salesOpportunityId) === row.id && String(todo.contactId) === row.contactId);
            if (!opportunity) throw Error('Prospect changed while reading details');
            controller.changeTodo(opportunity, undefined);
          }, row);
          await frame.waitForFunction(row => {
            const element = document.querySelector('crm-sales-opportunity-detail-view');
            const controller = element && window.angular.element(element).controller('crmSalesOpportunityDetailView');
            return String(controller?.salesOpportunity?.id) === row.id &&
              String(controller?.contact?.id) === row.contactId &&
              !controller.isLoading && !(controller.numberItemsStillLoading > 0);
          }, row, { timeout: 15000 });
          const detail = await f.locator('crm-sales-opportunity-detail-view').evaluate(element => {
            const controller = window.angular.element(element).controller('crmSalesOpportunityDetailView');
            const { salesOpportunity, contact } = controller;
            const points = contact.contactPoints || {};
            const display = value => typeof value === 'string' ? value : value?.text || value?.fullName || value?.name || '';
            return {
              site: String(salesOpportunity.dealershipId),
              restricted: Boolean(contact.isRestricted || salesOpportunity.restricted),
              name: contact.fullName || '',
              mobile: display(points.savedMobile?.value || points._mobile?.value || points.mobile?.value),
              email: display(points.email?.value),
              assignee: display(salesOpportunity.primaryAssigned),
            };
          });
          if (detail.site !== site) throw Error('Prospect detail dealership mismatch');
          const digits = detail.mobile.replace(/\D/g, '');
          row.customerDetails = detail.restricted ? 'restricted' : 'verified';
          row.mobile = detail.restricted ? '' : /^04\d{8}$/.test(digits) ? '+61' + digits.slice(1) : /^614\d{8}$/.test(digits) ? '+' + digits : '';
          row.email = detail.restricted ? '' : detail.email;
          if (!detail.restricted) {
            row.name = detail.name || row.name;
            row.assignee = detail.assignee || row.assignee;
          }
        }
        records.set(key, row);
      }
    }
  }
  return {capturedAt:new Date().toISOString(),coverage,records:[...records.values()]};
}
