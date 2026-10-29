"""Deterministic scheduler/restart/failure tests; no browser, CRM or customer writes."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services.dealersocket_lead_schedule import LeadSchedule
from app.services.dealersocket_leads import DealerSocketLeadReader


def config(enabled=True):
    return SimpleNamespace(DEALERSOCKET_LEAD_SYNC_ENABLED=enabled,
        DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS=3600, DEALERSOCKET_LEAD_SNAPSHOT_PATH='unused.json')


def snapshot():
    return {'state':'partial','readOnly':True,'fetchedAt':datetime.now(timezone.utc).isoformat(),
            'records':[{'record_key':'synthetic'}], 'rooftops':[]}


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_thirty_minute_interval(self):
        self.schedule.config.DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS = 1800
        current = datetime.now(timezone.utc)
        await self.schedule.tick(current)
        self.assertEqual(self.schedule.metadata()['intervalSeconds'], 1800)
        self.assertEqual(self.schedule.next_run, current + timedelta(minutes=30))
        await self.schedule.tick(current + timedelta(minutes=29))
        self.reader.start.assert_called_once()
        await self.schedule.tick(current + timedelta(minutes=30))
        self.assertEqual(self.reader.start.call_count, 2)

    async def test_recovered_session_immediately_retries_failed_read(self):
        self.reader.job = {'state': 'error', 'code': 'login_required'}
        self.reader.session.state = 'connected'
        current = datetime.now(timezone.utc)
        self.schedule.next_run = current + timedelta(hours=1)
        await self.schedule.tick(current)
        self.reader.start.assert_called_once()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'private/latest.json'
        self.reader=Mock(latest=None,session=SimpleNamespace(reader_busy=False))
        self.schedule=LeadSchedule(self.reader,config(),self.path)
        self.addAsyncCleanup(self.schedule.shutdown)

    async def test_hourly_cadence_starts_once_without_catchup_burst(self):
        current=datetime.now(timezone.utc)
        await self.schedule.tick(current)
        options=self.reader.start.call_args.args[0]
        self.assertEqual((options.maxRecords,options.detailLimit),(1000,200))
        await self.schedule.tick(current+timedelta(seconds=3599))
        self.assertEqual(self.reader.start.call_count,1)
        await self.schedule.tick(current+timedelta(hours=5))
        self.assertEqual(self.reader.start.call_count,2)
        self.assertEqual(self.schedule.next_run,current+timedelta(hours=6))

    async def test_running_lead_read_skips_hour_without_duplicate(self):
        self.reader.session.reader_busy=True
        self.reader.start.side_effect=RuntimeError('busy')
        current=datetime.now(timezone.utc)
        await self.schedule.tick(current)
        await self.schedule.tick(current+timedelta(seconds=30))
        self.assertEqual(self.reader.start.call_count,1)
        self.reader.start.side_effect=None
        await self.schedule.tick(current+timedelta(hours=1))
        self.assertEqual(self.reader.start.call_count,2)

    async def test_session_check_contention_retries_without_losing_hour(self):
        self.reader.start.side_effect=RuntimeError('browser_busy')
        current=datetime.now(timezone.utc)
        await self.schedule.tick(current)
        self.assertEqual(self.schedule.next_run,current+timedelta(seconds=10))
        await self.schedule.tick(current+timedelta(seconds=5))
        self.assertEqual(self.reader.start.call_count,1)
        # A long login remains a single pending attempt, not multiple queued jobs.
        await self.schedule.tick(current+timedelta(seconds=10))
        self.assertEqual(self.schedule.next_run,current+timedelta(seconds=20))
        self.reader.start.side_effect=None
        await self.schedule.tick(current+timedelta(seconds=20))
        self.assertEqual(self.reader.start.call_count,3)
        self.assertEqual(self.schedule.next_run,current+timedelta(seconds=3620))

    async def test_disabled_schedule_loads_snapshot_but_never_reads(self):
        previous=snapshot()
        await self.schedule.save(previous)
        self.schedule.config=config(False)
        await self.schedule.startup()
        await self.schedule.tick(datetime.now(timezone.utc)+timedelta(days=1))
        self.assertEqual(self.reader.latest,previous)
        self.reader.start.assert_not_called()
        self.assertIsNone(self.schedule.task)
        self.assertIsNone(self.schedule.metadata()['nextRunAt'])

    async def test_restart_restores_result_and_does_not_repeat_fresh_read(self):
        previous=snapshot()
        await self.schedule.save(previous)
        await self.schedule.startup()
        self.assertEqual(self.reader.latest,previous)
        await self.schedule.tick()
        self.reader.start.assert_not_called()
        self.assertIsNotNone(self.schedule.task)
        await self.schedule.tick(datetime.fromisoformat(previous['fetchedAt'])+timedelta(hours=1))
        self.reader.start.assert_called_once()

    async def test_corrupt_or_oversized_cache_is_ignored_and_due_immediately(self):
        self.path.parent.mkdir()
        for content in ['invalid json',json.dumps({'state':'error','readOnly':True,'records':[]}), 'x'*(8*1024*1024+1)]:
            self.path.write_text(content,encoding='utf-8')
            await self.schedule.startup()
            await self.schedule.shutdown()
            self.assertIsNone(self.reader.latest)
            await self.schedule.tick()
        self.assertEqual(self.reader.start.call_count,3)

    async def test_failed_atomic_replace_preserves_last_durable_snapshot(self):
        original=snapshot()
        await self.schedule.save(original)
        with patch('app.services.dealersocket_lead_schedule.os.replace',side_effect=OSError('disk failure')):
            await self.schedule.save({**original,'records':[]})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')),original)

    async def test_previous_snapshot_keeps_timestamp_after_failed_or_running_refresh(self):
        reader=DealerSocketLeadReader(SimpleNamespace())
        previous=snapshot()
        reader.latest=previous
        for state in ['running','error']:
            reader.job={'state':state,'readOnly':True,'fetchedAt':'2099-01-01T00:00:00+00:00','code':'login_required'}
            result=reader.status()
            self.assertEqual(result['state'],state)
            self.assertEqual(result['snapshotState'],'partial')
            self.assertEqual(result['fetchedAt'],previous['fetchedAt'])
            self.assertEqual(result['records'],previous['records'])
        reader.latest=None
        self.assertIsNone(reader.status()['fetchedAt'])

    async def test_watch_cancels_cleanly(self):
        self.schedule.task=asyncio.create_task(self.schedule._watch())
        await self.schedule.shutdown()
        self.assertTrue(self.schedule.task.cancelled())

    async def test_completed_read_is_durable_and_login_failure_retains_it(self):
        session=SimpleNamespace(reader_busy=False,lock=asyncio.Lock(),task=None,state='connected',message='Complete login',
            page=SimpleNamespace(url='https://bb.dealersocket.com/#/crm'),_run=AsyncMock())
        reader=DealerSocketLeadReader(session)
        reader.schedule=LeadSchedule(reader,config(False),self.path)
        previous=snapshot()
        with patch('app.services.dealersocket_leads.LeadBrowser'), patch(
                'app.services.dealersocket_leads.collect_snapshot',AsyncMock(return_value=previous)):
            reader.start(SimpleNamespace())
            await reader.task
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['records'],previous['records'])
        session.state='login_required'
        reader.start(SimpleNamespace())
        await reader.task
        self.assertEqual(reader.status()['state'],'error')
        self.assertEqual(reader.status()['code'],'login_required')
        self.assertEqual(reader.status()['records'],previous['records'])
        self.assertEqual(reader.status()['fetchedAt'],previous['fetchedAt'])
        self.assertFalse(session.reader_busy)
        restored=Mock(latest=None)
        schedule=LeadSchedule(restored,config(False),self.path)
        await schedule.startup()
        self.assertEqual(restored.latest['records'],previous['records'])
