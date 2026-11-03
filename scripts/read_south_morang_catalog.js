async(page)=>{
  const contactId=__CONTACT_ID__;
  if(!/^\d+$/.test(String(contactId)))throw Error('Verified customer ID required');
  const selected=await page.locator('md-select#select_1 .md-select-value,md-select#select_1 md-select-value').first().innerText();
  if(selected.trim()!=='South Morang Hyundai')throw Error('South Morang Hyundai must be selected');
  const original='https://bb.dealersocket.com/#/crm/employee?todoType=opportunities&dealershipId=10876&filterId=36';
  try{
    await page.goto(`https://bb.dealersocket.com/#/crm/view/contact/${contactId}`);
    await page.locator('iframe#app-crm').waitFor({state:'attached',timeout:60000});
    const f=await(await page.locator('iframe#app-crm').elementHandle()).contentFrame();
    await f.getByRole('heading',{name:new RegExp(`Customer ID ${contactId}$`)}).waitFor();
    await f.locator('[ng-click="!ctrl.salesPermission.add || !ctrl.canAddSalesOpportunity() || ctrl.addNewSalesOpportunity()"]').click();
    await f.locator('select[name=primary]').waitFor();
    return await f.locator('crm-add-sales-opportunity-details').evaluate(e=>{const c=window.angular.element(e).controller('crmAddSalesOpportunityDetails');if(String(c.salesOpportunity.dealershipId)!=='10876')throw Error('Catalog dealership mismatch');return{siteId:'10876',name:'South Morang Hyundai',observedAt:new Date().toISOString(),users:c.users,franchises:c.franchises,sources:[...e.querySelector('select[name=source]').options].map(o=>({value:o.value,text:o.textContent})),trackingCodes:[...e.querySelector('select[name=trackingCode]').options].map(o=>({value:o.value,text:o.textContent}))};});
  }finally{await page.goto(original);}
}
