import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from app.services.dealersocket_campaign_writer import CampaignLeadWrite, CampaignWriter, normalize_phone

class CampaignWriterTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.writer=CampaignWriter(SimpleNamespace(reader_busy=False,lock=asyncio.Lock(),task=None,state='connected',page=object()),self.directory.name)
        self.options=CampaignLeadWrite(leadId=uuid4(),contactId=123,mobile='+61412345678',salesManager='fixture-sm',generalManager='fixture-gm')
    def tearDown(self):
        self.directory.cleanup()
    def test_mobile_identity_normalizes_only_au_mobiles(self):
        self.assertEqual(normalize_phone('(0412) 345-678'),'+61412345678')
        self.assertEqual(normalize_phone('0399999999'),'')
    def test_writes_disabled_by_default(self):
        with patch('app.services.dealersocket_campaign_writer.settings.DEALERSOCKET_CAMPAIGN_WRITES_ENABLED',False):
            with self.assertRaises(PermissionError):self.writer.start(self.options)
    def test_uncertain_save_is_not_replayed_after_restart(self):
        self.writer.save(self.options.leadId,'saving')
        with patch('app.services.dealersocket_campaign_writer.settings.DEALERSOCKET_CAMPAIGN_WRITES_ENABLED',True):
            self.assertEqual(self.writer.start(self.options)['state'],'needs_review')
        self.assertIsNone(self.writer.task)
    def test_successful_job_is_idempotent(self):
        self.writer.save(self.options.leadId,'synced',opportunityId='456')
        with patch('app.services.dealersocket_campaign_writer.settings.DEALERSOCKET_CAMPAIGN_WRITES_ENABLED',True):
            self.assertEqual(self.writer.start(self.options)['opportunityId'],'456')
        self.assertIsNone(self.writer.task)
    def test_invalid_or_duplicate_manager_ids_block_writes(self):
        self.options.generalManager=self.options.salesManager
        with patch('app.services.dealersocket_campaign_writer.settings.DEALERSOCKET_CAMPAIGN_WRITES_ENABLED',True):
            with self.assertRaises(ValueError):self.writer.start(self.options)
