import unittest
from app.models.dealersocket_leads import LeadReadOptions
from app.services.dealersocket_leads import collect_snapshot, rules
from tests.test_dealersocket_leads import FakeBrowser

class HyundaiScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_campaign_isolated_from_existing_rooftops(self):
        browser = FakeBrowser()
        browser.original['sites'] = [{'id':'100','name':'South Morang Hyundai'}, {'id':'200','name':'Cranbourne Kia'}]
        browser.rows = [{**browser.rows[0], 'franchiseName':'South Morang Hyundai'}, {**browser.rows[1], 'franchiseName':'Cranbourne Kia'}]
        result = await collect_snapshot(browser, LeadReadOptions(scope='south_morang_hyundai'))
        self.assertEqual(result['scope'], 'south_morang_hyundai')
        self.assertEqual([r['rooftop'] for r in result['records']], ['South Morang Hyundai'])
        self.assertEqual(browser.details, ['10'])
        self.assertEqual(rules.ROOFTOPS, ['Cranbourne Kia', 'Cranbourne MG', 'Geely Cranbourne'])
        self.assertTrue(browser.restored)
