"""Read-only scope, freshness, isolation and failure tests. No live CRM access."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from app.config import settings
from app.main import app
from app.models.dealersocket_leads import LeadReadOptions
from app.services.dealersocket_leads import collect_snapshot, DealerSocketLeadReader
from app.services.dealersocket_session import DealerSocketSession


ROOFS = ["Cranbourne Kia", "Cranbourne MG", "Geely Cranbourne"]


class FakeBrowser:
    def __init__(self):
        self.reads, self.details, self.restored = [], [], False
        self.rows = [{"dealershipId":"100", "franchiseId":str(i), "salesOpportunityId":str(i+10),
            "contactId":str(i+100), "fullName":"Fixture Customer", "opportunityType":"sales",
            "eventStatus":"Open", "franchiseName":name} for i, name in enumerate(ROOFS)]
        self.original = {"sites":[{"id":"100","name":"Fixture Site"}], "site":{"id":"100","name":"Fixture Site"},
                         "filter":{"id":"1","name":"Original"}, "keyword":""}

    async def prepare(self):
        return self.original

    async def site(self, site):
        return {"filters":[{"id":"1", "name":"Cranbourne Kia Internet Leads UNASSIGNED", "category":"Internet Traffic"},
            {"id":"2","name":"Service Opportunities", "category":"Service Opportunities"}]}

    async def read_filter(self, site, item):
        self.reads.append(item["id"])
        return {"todos":deepcopy(self.rows),"displayed_count":len(self.rows),"keyword":""}

    async def detail(self, site, item, todo):
        self.details.append(todo["salesOpportunityId"])
        return {"opportunity_id":str(todo["salesOpportunityId"]),"customer_id":str(todo["contactId"]),
            "dealership_id":str(todo["dealershipId"]),"franchise_id":str(todo["franchiseId"]),
            "rooftop":todo["franchiseName"],"opportunity_type":"sales","opportunity_status":"Open",
            "customer_name":"Fixture Customer","customer_phone_raw":"0412345678","customer_email_raw":"fixture@example.test",
            "detail_extracted":True,"model_id_present":True}

    async def restore(self, original):
        self.restored = True


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_dedup_and_no_unrelated_detail_reads(self):
        browser = FakeBrowser()
        browser.rows += [dict(browser.rows[0]), {**browser.rows[0],"salesOpportunityId":"x","franchiseName":"Other Dealer"},
                         {**browser.rows[1],"salesOpportunityId":"sold","eventStatus":"Sold"}]
        result = await collect_snapshot(browser, LeadReadOptions())
        self.assertEqual(len(result["records"]),3)
        self.assertEqual(browser.details,["10","11","12"])
        self.assertNotIn("2",browser.reads)
        self.assertTrue(browser.restored)
        self.assertTrue(result["readOnly"])
        self.assertEqual(result["records"][0]["customer_phone"],"+61412345678")
        self.assertTrue(all(r["new_lead_eligibility"]=="not_verified_no_sms_authorized" for r in result["records"]))

    async def test_every_snapshot_reads_live_rows_and_does_not_reuse_old_records(self):
        browser = FakeBrowser()
        first = await collect_snapshot(browser,LeadReadOptions())
        browser.rows = [browser.rows[0]]
        second = await collect_snapshot(browser,LeadReadOptions())
        self.assertEqual(len(first["records"]),3)
        self.assertEqual(len(second["records"]),1)
        self.assertEqual(second["state"],"partial")

    async def test_limit_and_list_only_missing_details_are_explicit(self):
        result = await collect_snapshot(FakeBrowser(),LeadReadOptions(maxRecords=1,detailLimit=0))
        self.assertTrue(result["limited"])
        self.assertEqual(result["state"],"partial")
        self.assertFalse(result["records"][0]["detail_extracted"])
        self.assertIn("list_only",result["records"][0]["validation_warnings"])

    async def test_bounded_sample_reserves_space_for_each_rooftop(self):
        browser = FakeBrowser()
        browser.rows = [dict(browser.rows[0],salesOpportunityId=str(i+1000)) for i in range(20)] + browser.rows[1:]
        result = await collect_snapshot(browser,LeadReadOptions(maxRecords=3,detailLimit=0))
        self.assertEqual({r["rooftop"] for r in result["records"]},set(ROOFS))
        self.assertEqual([roof["matchedCandidateCount"] for roof in result["rooftops"]],[20,1,1])

    async def test_balanced_sample_extracts_by_queue_without_repeated_switching(self):
        class TwoQueues(FakeBrowser):
            async def site(self,site):
                return {"filters":[{"id":"1","name":"Cranbourne Kia Internet Leads","category":"Internet Traffic"},
                    {"id":"3","name":"Cranbourne MG Internet Leads","category":"Internet Traffic"}]}
            async def read_filter(self,site,item):
                self.reads.append(item["id"])
                rows=[r for r in self.rows if r['franchiseName']==('Cranbourne Kia' if item['id']=='1' else 'Cranbourne MG')]
                return {"todos":deepcopy(rows),"displayed_count":len(rows),"keyword":""}
        browser=TwoQueues()
        browser.rows=[browser.rows[0],dict(browser.rows[0],salesOpportunityId='13'),
                      browser.rows[1],dict(browser.rows[1],salesOpportunityId='14')]
        result=await collect_snapshot(browser,LeadReadOptions(maxRecords=4,detailLimit=4))
        self.assertEqual(len(result['records']),4)
        self.assertEqual(browser.reads,['1','3','1','3'])

    async def test_failures_are_partial_and_do_not_echo_secrets(self):
        browser = FakeBrowser()
        browser.detail = AsyncMock(side_effect=RuntimeError("private-token-value"))
        browser.restore = AsyncMock(side_effect=RuntimeError("private-token-value"))
        result = await collect_snapshot(browser,LeadReadOptions())
        self.assertEqual(result["state"],"partial")
        self.assertNotIn("private-token-value",str(result))

    async def test_restricted_or_mismatched_details_are_not_exported(self):
        browser = FakeBrowser()
        browser.detail = AsyncMock(return_value={"restricted":True})
        result = await collect_snapshot(browser,LeadReadOptions())
        self.assertEqual(result["records"],[])
        self.assertEqual(len(result["review"]),3)

    async def test_reader_serializes_with_session_checks(self):
        config = SimpleNamespace(DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED=True,
            DEALERSOCKET_PROFILE_DIR="unused",DEALERSOCKET_CHECK_INTERVAL_SECONDS=30)
        session = DealerSocketSession(config)
        gate = asyncio.Event()
        async def check(operation):
            await gate.wait()
            session.state = "login_required"
        session._run = check
        reader = DealerSocketLeadReader(session)
        reader.start(LeadReadOptions())
        with self.assertRaises(RuntimeError):
            reader.start(LeadReadOptions())
        with self.assertRaises(RuntimeError):
            await session.start("login")
        gate.set()
        await reader.task
        self.assertEqual(reader.status()["code"],"login_required")
        self.assertFalse(session.reader_busy)


class LeadAPITests(unittest.TestCase):
    def test_auth_validation_and_conflicting_reader(self):
        with TestClient(app) as client, patch.object(settings,"DEALERSOCKET_CONNECTOR_KEY","x"*40):
            self.assertEqual(client.get('/dealersocket/leads/status').status_code,401)
            headers={"X-Connector-Key":"x"*40}
            self.assertEqual(client.post('/dealersocket/leads/read',headers=headers,json={"maxRecords":0}).status_code,422)
            self.assertEqual(client.post('/dealersocket/leads/read',headers=headers,json={"sendSMS":True}).status_code,422)
            with patch('app.routers.dealersocket.dealersocket_leads.start',side_effect=RuntimeError("busy")):
                self.assertEqual(client.post('/dealersocket/leads/read',headers=headers,json={}).status_code,409)
            self.assertEqual(client.get('/dealersocket/leads/status',headers=headers).headers['cache-control'],'no-store')
            from app.services.dealersocket_session import dealersocket_session
            with patch.object(dealersocket_session,'reader_busy',True):
                self.assertEqual(client.post('/dealersocket/check',headers=headers).status_code,409)
                self.assertEqual(client.post('/dealersocket/login/start',headers=headers).status_code,409)
