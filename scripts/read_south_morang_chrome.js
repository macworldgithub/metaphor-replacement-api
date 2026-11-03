async (page) => {
  const stamp=()=>new Date().toISOString();
  const selected=await page.locator('md-select#select_1 .md-select-value,md-select#select_1 md-select-value').first().innerText();
  if(selected.trim()!=='South Morang Hyundai'){
    await page.locator('md-select#select_1').click();
    await page.getByRole('option',{name:'South Morang Hyundai',exact:true}).click();
    await page.waitForFunction(()=>{const e=document.querySelector('md-select#select_1 .md-select-value,md-select#select_1 md-select-value');return e?.textContent.trim()==='South Morang Hyundai';});
  }
  const startedAt=stamp(), records=new Map(),coverage=[];
  await page.goto('https://bb.dealersocket.com/#/crm/employee?todoType=opportunities&dealershipId=10876&filterId=36');
  await page.locator('iframe#app-crm').waitFor({state:'attached',timeout:60000});
  const frame=await(await page.locator('iframe#app-crm').elementHandle()).contentFrame();
  if(!frame)throw Error('Authenticated CRM frame unavailable');
  const list=frame.locator('crm-employee-dashboard-list-details');
  await list.waitFor();
  await frame.waitForFunction(()=>{const e=document.querySelector('crm-employee-dashboard-list-details'),c=e&&window.angular.element(e).controller('crmEmployeeDashboardListDetails');return c?.selectedDealership&&c.selectedFilter&&c.categoryFilters?.length&&!c.isLoadingTodos;});
  const initial=await list.evaluate(e=>{const c=window.angular.element(e).controller('crmEmployeeDashboardListDetails');return{site:String(c.selectedDealership.value),filter:String(c.selectedFilter.id),keyword:c.searchText||''};});
  if(initial.site!=='10876'||initial.keyword)throw Error('Hyundai scope or search state could not be verified');
  let details=0;
  async function select(id){
    if(!await frame.locator('select[name="filter-dropdown"]').isVisible()){
      const expand=frame.locator('button[name="expandButton"]');
      if(await expand.isVisible())await expand.evaluate(e=>{if(!e.disabled)e.click();});
    }
    await frame.locator('select[name="filter-dropdown"]').waitFor({state:'visible'});
    const key=await list.evaluate((e,id)=>window.angular.element(e).controller('crmEmployeeDashboardListDetails').categoryFilters.find(f=>String(f.id)===id)?.$$hashKey,id);
    if(!key)throw Error('Verified saved filter unavailable');
    await frame.locator('select[name="filter-dropdown"]').selectOption(key);
    await frame.waitForFunction(id=>{const e=document.querySelector('crm-employee-dashboard-list-details'),c=e&&window.angular.element(e).controller('crmEmployeeDashboardListDetails');return c&&String(c.selectedDealership.value)==='10876'&&String(c.selectedFilter.id)===id&&!c.isLoadingTodos;},id);
  }
  try{
    for(const id of ['36','35','135']){
      await select(id);
      const result=await list.evaluate(e=>{const c=window.angular.element(e).controller('crmEmployeeDashboardListDetails');return{filter:c.selectedFilter.name,todos:(c.todos||[]).map(t=>Object.fromEntries(['dealershipId','franchiseId','contactId','salesOpportunityId','franchiseName','fullName','opportunityType','eventStatus','assignedTo','lastUpdate','year','make','model'].map(k=>[k,t[k]??null]))),displayed:[...e.querySelectorAll('[name="results-display"]')].map(x=>x.textContent).find(x=>/\d.*RESULT/i.test(x))||null};});
      coverage.push({filterId:id,filterName:result.filter,loadedCount:result.todos.length,displayed:result.displayed,observedAt:stamp()});
      for(const todo of result.todos){
        if(String(todo.dealershipId)!=='10876'||!['South Morang Hyundai','South Morang Hyundai Used Cars'].includes(todo.franchiseName)||String(todo.opportunityType).toLowerCase()!=='sales')continue;
        const key=[todo.dealershipId,todo.salesOpportunityId,todo.contactId].join('|');
        if(records.has(key)||records.size>=100)continue;
        let row={record_key:key,opportunity_id:String(todo.salesOpportunityId),customer_id:String(todo.contactId),dealership_id:'10876',rooftop:'South Morang Hyundai',rooftop_raw:todo.franchiseName,customer_name:todo.fullName,customer_phone:'',customer_email:'',assigned_salesperson:todo.assignedTo,opportunity_status:todo.eventStatus,detail_extracted:false,list_observed_at:stamp(),new_lead_eligibility:'not_verified_no_sms_authorized'};
        if(details<10){
          details++;
          try{
            await list.evaluate((e,t)=>{const c=window.angular.element(e).controller('crmEmployeeDashboardListDetails');const found=c.todos.find(x=>String(x.salesOpportunityId)===String(t.salesOpportunityId)&&String(x.contactId)===String(t.contactId));if(!found)throw Error('Record changed');c.changeTodo(found,undefined);},todo);
            await frame.waitForFunction(t=>{const e=document.querySelector('crm-sales-opportunity-detail-view'),c=e&&window.angular.element(e).controller('crmSalesOpportunityDetailView');return c&&String(c.salesOpportunity?.id||c.opportunityId)===String(t.salesOpportunityId)&&String(c.contact?.id)===String(t.contactId)&&!c.isLoading&&!(c.numberItemsStillLoading>0);},todo,{timeout:15000});
            const detail=await frame.locator('crm-sales-opportunity-detail-view').evaluate(e=>{const c=window.angular.element(e).controller('crmSalesOpportunityDetailView'),s=c.salesOpportunity,p=c.contact,cp=p.contactPoints||{};const val=x=>x==null?'':typeof x==='object'?String(x.text??x.value??''):String(x);return{restricted:p.isRestricted||s.restricted,site:String(s.dealershipId),id:String(s.id),contact:String(p.id),customer_name:p.fullName,phone:val(cp.savedMobile?.value||cp._mobile?.value||cp.mobile?.value),customer_email:val(cp.email?.value),assigned_salesperson:val(s.primaryAssigned)||'',never_contact_via:val(p.neverContactVia)};});
            if(detail.restricted||detail.site!=='10876'||detail.id!==row.opportunity_id||detail.contact!==row.customer_id)continue;
            const digits=detail.phone.replace(/\D/g,'');
            row={...row,customer_name:detail.customer_name||row.customer_name,customer_phone:/^04\d{8}$/.test(digits)?'+61'+digits.slice(1):/^614\d{8}$/.test(digits)?'+'+digits:'',customer_email:detail.customer_email,assigned_salesperson:detail.assigned_salesperson||row.assigned_salesperson,never_contact_via:detail.never_contact_via,detail_extracted:true,detail_observed_at:stamp()};
          }catch{row.validation_warnings='details_not_verified';}
        }
        records.set(key,row);
      }
    }
  }finally{await select(initial.filter);}
  return{scope:'south_morang_hyundai',state:'partial',source:'authenticated_chrome_capture',readOnly:true,startedAt,fetchedAt:stamp(),records:[...records.values()],coverage,detailReads:details,coverageNote:'Authenticated Chrome observation of selected Hyundai new, used and current-month queues. Bounded to 100 records and 10 detail reads. This is not a full customer export or evidence of SMS consent.'};
}
