async(page)=>{
  const lookup=__CONTACT_LOOKUP__,mobile=lookup.mobile;
  if(!/^\+614\d{8}$/.test(mobile))throw Error('Australian mobile required');
  const selected=await page.locator('md-select#select_1 .md-select-value,md-select#select_1 md-select-value').first().innerText();
  if(selected.trim()!=='South Morang Hyundai')throw Error('South Morang Hyundai must be selected');
  await page.goto('https://bb.dealersocket.com/#/crm/search/classic');
  await page.locator('iframe#app-crm').waitFor({state:'attached',timeout:60000});
  const f=await(await page.locator('iframe#app-crm').elementHandle()).contentFrame();
  await f.locator('input[name=phone]').fill('0'+mobile.slice(3));
  await f.getByRole('button',{name:/Search$/}).click();
  await f.waitForFunction(()=>{const e=document.querySelector('ds-advanced-search'),c=e&&window.angular.element(e).controller('dsAdvancedSearch');return c?.initialized&&!c.searchingContact&&c.contactSearchResults;});
  return f.locator('ds-advanced-search').evaluate((e,lookup)=>{
    const mobile=lookup.mobile;
    const c=window.angular.element(e).controller('dsAdvancedSearch'),r=c.contactSearchResults;
    if(String(c.siteIdService.getSiteId())!=='10876')throw Error('Search dealership changed');
    const normal=value=>{const n=String(value||'').replace(/\D/g,'');return /^04\d{8}$/.test(n)?'+61'+n.slice(1):/^614\d{8}$/.test(n)?'+'+n:'';};
    const matches=(r.contacts||[]).filter(p=>{const cp=p.contactPoints||{};return String(p.siteId||p.dealershipId)==='10876'&&!p.isRestricted&&[cp.savedMobile?.value,cp._mobile?.value,cp.mobile?.value].some(v=>normal(v)===mobile);});
    const narrowed=matches.length>1&&lookup.email?matches.filter(p=>String(p.contactPoints?.email?.value||'').trim().toLowerCase()===lookup.email.trim().toLowerCase()):matches;
    const ids=[...new Set(narrowed.map(p=>String(p.id)))];
    return ids.length===1&&r.totalCount<=25?{state:'matched',contactId:ids[0]}:{state:'needs_review',matchCount:ids.length,message:'A unique existing Hyundai customer could not be verified.'};
  },lookup);
}
