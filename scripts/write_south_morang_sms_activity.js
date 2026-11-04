async (page) => {
  const job = __SMS_ACTIVITY__;
  if (!/^[a-f0-9-]{36}$/i.test(job.id) || !/^\d+$/.test(String(job.contactId)) || !/^\d+$/.test(String(job.opportunityId)) || !/^\+614\d{8}$/.test(job.mobile) || !['inbound', 'outbound', 'test'].includes(job.direction)) throw Error('Invalid SMS activity job');
  const selected = await page.locator('md-select#select_1 .md-select-value,md-select#select_1 md-select-value').first().innerText();
  if (selected.trim() !== 'South Morang Hyundai') throw Error('South Morang Hyundai must be selected');
  await page.goto(`https://bb.dealersocket.com/#/crm/employee?todoType=opportunities&dealershipId=10876&filterId=135&todoId=${job.opportunityId}&mood=2`);
  await page.locator('iframe#app-crm').waitFor({ state: 'attached', timeout: 60000 });
  const f = await (await page.locator('iframe#app-crm').elementHandle()).contentFrame();
  const detail = f.locator('crm-sales-opportunity-detail-view');
  await f.getByRole('heading', { name: new RegExp(`Opp ID ${job.opportunityId}$`) }).waitFor();
  const identity = await detail.evaluate(e => {
    const c = window.angular.element(e).controller('crmSalesOpportunityDetailView');
    const cp = c.contact.contactPoints || {};
    return { site: String(c.salesOpportunity.dealershipId), contact: String(c.contact.id), contactSite: String(c.contact.siteId || c.contact.dealershipId), opportunity: String(c.salesOpportunity.id), phones: [cp.savedMobile?.value, cp._mobile?.value, cp.mobile?.value] };
  });
  const normal = v => { const n = String(v || '').replace(/\D/g, ''); return /^04\d{8}$/.test(n) ? '+61' + n.slice(1) : /^614\d{8}$/.test(n) ? '+' + n : ''; };
  if (identity.site !== '10876' || identity.contactSite !== '10876' || identity.contact !== String(job.contactId) || identity.opportunity !== String(job.opportunityId) || !identity.phones.some(v => normal(v) === job.mobile)) throw Error('SMS activity customer identity mismatch');
  const marker = `[Booran SMS ${job.id}]`;
  if (await detail.getByText(marker, { exact: false }).count()) return { state: 'synced' };
  const note = `${marker}\n${job.direction.toUpperCase()} SMS · ${job.createdAt}\n${job.body}`;
  const input = f.locator('textarea[name=addWorkNote]');
  await input.fill(note);
  const noteIdentity = await f.locator('crm-add-work-note').evaluate(e => {
    const c = window.angular.element(e).controller('crmAddWorkNote');
    return { contact: String(c.contactId), site: String(c.dealershipId), event: String(c.eventId) };
  });
  if (noteIdentity.contact !== String(job.contactId) || noteIdentity.site !== '10876' || noteIdentity.event !== String(job.opportunityId)) throw Error('Work note identity mismatch');
  await f.locator('button[name=saveWorkNote]').evaluate(e => {
    const style = getComputedStyle(e);
    if (e.disabled || style.display === 'none' || style.visibility === 'hidden') throw Error('Work note submit is unavailable');
    e.click();
  });
  await detail.getByText(marker, { exact: false }).first().waitFor({ timeout: 30000 });
  // Verify persistence after a reload, rather than trusting a cleared textarea.
  await page.reload();
  await page.locator('iframe#app-crm').waitFor({ state: 'attached', timeout: 60000 });
  const verified = await (await page.locator('iframe#app-crm').elementHandle()).contentFrame();
  await verified.getByText(marker, { exact: false }).first().waitFor({ timeout: 30000 });
  return { state: 'synced' };
}
