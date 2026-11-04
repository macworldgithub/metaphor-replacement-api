async (page) => {
  const job=__CAMPAIGN_LEAD__;
  if(!/^[a-f0-9-]{36}$/i.test(job.leadId)||!/^\d+$/.test(String(job.contactId))||!/^\+614\d{8}$/.test(job.mobile)||!job.salesManager||!job.generalManager||job.salesManager===job.generalManager)throw Error('Invalid CRM write job');
  const selected=await page.locator('md-select#select_1 .md-select-value,md-select#select_1 md-select-value').first().innerText();
  if(selected.trim()!=='South Morang Hyundai')throw Error('South Morang Hyundai must be selected');
  await page.goto(`https://bb.dealersocket.com/#/crm/view/contact/${job.contactId}`);
  await page.locator('iframe#app-crm').waitFor({state:'attached',timeout:60000});
  const f=await(await page.locator('iframe#app-crm').elementHandle()).contentFrame();
  if(!f)throw Error('Authenticated CRM frame unavailable');
  await f.getByRole('heading',{name:new RegExp(`Customer ID ${job.contactId}$`)}).waitFor();
  const phones=await f.locator('input[name=phoneNumber]').evaluateAll(els=>els.map(e=>e.value));
  const normal=v=>{const n=String(v||'').replace(/\D/g,'');return /^04\d{8}$/.test(n)?'+61'+n.slice(1):/^614\d{8}$/.test(n)?'+'+n:'';};
  if(!phones.some(v=>normal(v)===job.mobile))throw Error('Customer mobile mismatch');
  await f.locator('[ng-click="!ctrl.salesPermission.add || !ctrl.canAddSalesOpportunity() || ctrl.addNewSalesOpportunity()"]').click();
  await f.locator('select[name=source]').selectOption({label:'Internet'});
  await f.locator('select[name=trackingCode]').selectOption({label:'Website'});
  await f.locator('select[name=primary]').selectOption(job.salesManager);
  await f.locator('select[name=franchise]').selectOption({label:'South Morang Hyundai'});
  if(!await f.locator('select[name=secondaryAssigned]').isVisible())await f.getByRole('button',{name:/Show All Fields/i}).click();
  await f.locator('select[name=secondaryAssigned]').selectOption(job.generalManager);
  const marker='Booran SMH loyalty registration '+job.leadId;
  await f.locator('input[name=description]').fill(marker);
  const identity=await f.locator('crm-add-sales-opportunity').evaluate(e=>{const c=window.angular.element(e).controller('crmAddSalesOpportunity');return{site:String(c.salesOpportunity.dealershipId),contactSite:String(c.contact.siteId||c.contact.dealershipId),contact:String(c.contact.id),id:c.salesOpportunity.id};});
  if(identity.site!=='10876'||identity.contactSite!=='10876'||identity.contact!==String(job.contactId)||identity.id!==null)throw Error('Opportunity identity mismatch');
  if(await f.locator('select[name=primary]').inputValue()!==job.salesManager||await f.locator('select[name=secondaryAssigned]').inputValue()!==job.generalManager)throw Error('Assignee verification failed');
  await f.getByRole('button',{name:'Save & Continue',exact:true}).click();
  const saved=await f.waitForFunction(({marker,primary,secondary})=>{for(const[tag,name]of[['crm-add-sales-opportunity','crmAddSalesOpportunity'],['crm-sales-opportunity-detail-view','crmSalesOpportunityDetailView']]){const e=document.querySelector(tag),c=e&&window.angular.element(e).controller(name),s=c?.salesOpportunity;if(/^[1-9]\d*$/.test(String(s?.id||''))&&s.description===marker&&String(s.dealershipId)==='10876'&&s.primaryAssigned?.value===primary&&s.secondaryAssigned?.value===secondary)return{id:String(s.id)};}return false;},{marker,primary:job.salesManager,secondary:job.generalManager},{timeout:30000});
  return{state:'synced',opportunityId:(await saved.jsonValue()).id,leadId:job.leadId};
}
