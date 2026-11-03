"""Scoped UI writer for an existing, identity-verified Hyundai customer.

No write is enabled by default. A persisted 'saving' job is never replayed.
The exact customer mobile, dealership and both assignees are verified first.
"""
import asyncio
import json
import os
import re
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict
from app.config import settings
from app.services.dealersocket_session import dealersocket_session, now


class CampaignLeadWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    leadId: UUID
    contactId: int = Field(gt=0)
    mobile: str = Field(pattern=r'^\+614\d{8}$')
    salesManager: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    generalManager: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')


def normalize_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return '+61' + digits[1:] if re.fullmatch(r'04\d{8}', digits) else '+' + digits if re.fullmatch(r'614\d{8}', digits) else ''


class CampaignWriter:
    def __init__(self, session=dealersocket_session, directory=None):
        self.session, self.task = session, None
        self.directory = Path(directory or Path(__file__).resolve().parents[2] / '.private/campaign-write-jobs')

    def path(self, lead_id):
        return self.directory / (str(UUID(str(lead_id))) + '.json')

    def status(self, lead_id):
        try:
            value = json.loads(self.path(lead_id).read_text(encoding='utf-8'))
            # A process restart cannot prove whether a save completed.
            if value['state'] == 'saving' and not (self.task and not self.task.done()):
                value['state'] = 'needs_review'
            return value
        except FileNotFoundError:
            return {'leadId': str(lead_id), 'state': 'not_started'}

    def save(self, lead_id, state, **data):
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path(lead_id)
        temporary = target.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as file:
            json.dump({'leadId': str(lead_id), 'state': state, 'updatedAt': now(), **data}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)

    def start(self, options):
        if not settings.DEALERSOCKET_CAMPAIGN_WRITES_ENABLED:
            raise PermissionError('Campaign CRM writes are disabled until routing and a controlled test are approved.')
        if options.salesManager == options.generalManager:
            raise ValueError('Select separate Sales Manager and General Manager assignees.')
        previous = self.status(options.leadId)
        if previous['state'] in ('synced', 'saving', 'needs_review'):
            return previous
        if (self.task and not self.task.done() or self.session.reader_busy or self.session.lock.locked()
                or self.session.task and not self.session.task.done()):
            raise RuntimeError('browser_busy')
        if self.session.state != 'connected' or not self.session.page:
            raise RuntimeError('login_required')
        self.session.reader_busy = True
        self.save(options.leadId, 'preparing')
        self.task = asyncio.create_task(self.run(options))
        return self.status(options.leadId)

    async def run(self, options):
        saving = False
        marker = 'Booran SMH loyalty registration ' + str(options.leadId)
        try:
            async with asyncio.timeout(150), self.session.lock:
                page = self.session.page
                label = await page.locator('md-select#select_1 .md-select-value, md-select#select_1 md-select-value').first.inner_text()
                if ' '.join(label.split()).lower() != 'south morang hyundai':
                    raise ValueError('Select South Morang Hyundai in the global dealership selector first.')
                await page.goto(f'https://bb.dealersocket.com/#/crm/view/contact/{options.contactId}', wait_until='domcontentloaded')
                frame = None
                for _ in range(60):
                    for candidate in page.frames:
                        if candidate != page.main_frame and await candidate.locator('h1').filter(has_text=f'Customer ID {options.contactId}').count():
                            frame = candidate
                            break
                    if frame:
                        break
                    await asyncio.sleep(.5)
                if not frame:
                    raise ValueError('The exact customer profile could not be verified.')
                phones = await frame.locator('input[name=phoneNumber]').evaluate_all('els=>els.map(e=>e.value)')
                if options.mobile not in {normalize_phone(value) for value in phones}:
                    raise ValueError('Customer mobile does not match the captured lead.')
                await frame.locator('[ng-click="!ctrl.salesPermission.add || !ctrl.canAddSalesOpportunity() || ctrl.addNewSalesOpportunity()"]').click()
                await frame.locator('select[name=source]').select_option(label='Internet')
                await frame.locator('select[name=trackingCode]').select_option(label='Website')
                await frame.locator('select[name=primary]').select_option(value=options.salesManager)
                await frame.locator('select[name=franchise]').select_option(label='South Morang Hyundai')
                if not await frame.locator('select[name=secondaryAssigned]').is_visible():
                    await frame.get_by_role('button', name=re.compile('Show All Fields', re.I)).click()
                await frame.locator('select[name=secondaryAssigned]').select_option(value=options.generalManager)
                await frame.locator('input[name=description]').fill(marker)
                identity = await frame.locator('crm-add-sales-opportunity').evaluate("""e=>{
                    const c=window.angular.element(e).controller('crmAddSalesOpportunity');
                    return {site:String(c.salesOpportunity.dealershipId),contactSite:String(c.contact.siteId||c.contact.dealershipId),contact:String(c.contact.id),id:c.salesOpportunity.id};
                }""")
                if identity != {'site': '10876', 'contactSite': '10876', 'contact': str(options.contactId), 'id': None}:
                    raise ValueError('New opportunity identity or dealership changed.')
                if (await frame.locator('select[name=primary]').input_value() != options.salesManager
                        or await frame.locator('select[name=secondaryAssigned]').input_value() != options.generalManager):
                    raise ValueError('Assignee selection changed before saving.')
                self.save(options.leadId, 'saving', contactId=options.contactId)
                saving = True
                await frame.get_by_role('button', name='Save & Continue', exact=True).click()
                saved = await frame.wait_for_function("""marker=>{
                    for(const [tag,name] of [['crm-add-sales-opportunity','crmAddSalesOpportunity'],['crm-sales-opportunity-detail-view','crmSalesOpportunityDetailView']]){
                        const e=document.querySelector(tag),c=e&&window.angular.element(e).controller(name),s=c?.salesOpportunity;
                        if(s?.id && s.description===marker && String(s.dealershipId)==='10876')return {id:String(s.id)};
                    } return false;
                }""", arg=marker, timeout=30000)
                result = await saved.json_value()
                self.save(options.leadId, 'synced', opportunityId=result['id'], contactId=options.contactId,
                          salesManager=options.salesManager, generalManager=options.generalManager)
        except BaseException:
            self.save(options.leadId, 'needs_review' if saving else 'preparation_failed',
                      message='Reconcile in DealerSocket before retrying.' if saving else 'Verify session, customer identity, dealership and assignee configuration.')
        finally:
            self.session.reader_busy = False

    async def shutdown(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await self.task


campaign_writer = CampaignWriter()
