"""Deterministic scheduling, isolation and restart tests; no live CRM access."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.models.dealersocket_leads import LeadReadOptions
from app.services.dealersocket_lead_schedule import LeadSchedule, SCOPES
from app.services.dealersocket_leads import DealerSocketLeadReader


def config(enabled=True):
    return SimpleNamespace(DEALERSOCKET_LEAD_SYNC_ENABLED=enabled,
        DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS=1800,
        DEALERSOCKET_LEAD_SNAPSHOT_PATH='unused.json')


def snapshot(scope='cranbourne'):
    return {'scope': scope, 'state': 'partial', 'readOnly': True,
            'fetchedAt': datetime.now(timezone.utc).isoformat(),
            'records': [{'record_key': scope}], 'rooftops': []}


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'private/latest.json'
        self.reader = SimpleNamespace(latest=None, latest_by_scope={}, job=None,
            session=SimpleNamespace(reader_busy=False, state='connected'))
        def start(options):
            self.reader.session.reader_busy = True
            self.reader.job = {'state': 'running', 'scope': options.scope}
        self.reader.start = Mock(side_effect=start)
        self.schedule = LeadSchedule(self.reader, config(), self.path)
        self.addAsyncCleanup(self.schedule.shutdown)

    def finish(self):
        self.reader.session.reader_busy = False
        self.reader.job = None

    async def test_both_scopes_refresh_every_thirty_minutes_without_overlap(self):
        current = datetime.now(timezone.utc)
        await self.schedule.tick(current)
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'cranbourne')
        await self.schedule.tick(current + timedelta(seconds=5))
        self.reader.start.assert_called_once()
        self.finish()
        await self.schedule.tick(current + timedelta(seconds=10))
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'south_morang_hyundai')
        self.finish()
        await self.schedule.tick(current + timedelta(minutes=29))
        self.assertEqual(self.reader.start.call_count, 2)
        await self.schedule.tick(current + timedelta(minutes=30))
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'cranbourne')
        self.finish()
        await self.schedule.tick(current + timedelta(minutes=30, seconds=10))
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'south_morang_hyundai')
        self.assertEqual(self.reader.start.call_count, 4)
        for call in self.reader.start.call_args_list:
            self.assertEqual((call.args[0].maxRecords, call.args[0].detailLimit), (1000, 200))

    async def test_long_read_leaves_other_scope_due(self):
        current = datetime.now(timezone.utc)
        await self.schedule.tick(current)
        await self.schedule.tick(current + timedelta(minutes=10))
        self.reader.start.assert_called_once()
        self.assertIsNone(self.schedule.next_runs['south_morang_hyundai'])
        self.finish()
        await self.schedule.tick(current + timedelta(minutes=10, seconds=5))
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'south_morang_hyundai')

    async def test_missed_intervals_do_not_create_catchup_burst(self):
        current = datetime.now(timezone.utc)
        for scope in SCOPES:
            await self.schedule.tick(current)
            self.finish()
        later = current + timedelta(hours=5)
        for scope in SCOPES:
            await self.schedule.tick(later)
            self.finish()
        await self.schedule.tick(later + timedelta(seconds=5))
        self.assertEqual(self.reader.start.call_count, 4)
        self.assertTrue(all(due == later + timedelta(minutes=30)
                            for due in self.schedule.next_runs.values()))

    async def test_session_contention_defers_without_dropping_either_scope(self):
        current = datetime.now(timezone.utc)
        self.schedule.next_runs = dict.fromkeys(SCOPES, current)
        self.reader.start.side_effect = RuntimeError('busy')
        await self.schedule.tick(current)
        await self.schedule.tick(current)
        self.assertTrue(all(due == current + timedelta(seconds=10)
                            for due in self.schedule.next_runs.values()))
        await self.schedule.tick(current + timedelta(seconds=5))
        self.assertEqual(self.reader.start.call_count, 2)
        self.reader.start.side_effect = None
        await self.schedule.tick(current + timedelta(seconds=10))
        await self.schedule.tick(current + timedelta(seconds=10))
        self.assertEqual([c.args[0].scope for c in self.reader.start.call_args_list[-2:]], list(SCOPES))

    async def test_recovered_session_retries_failed_hyundai_scope(self):
        current = datetime.now(timezone.utc)
        self.schedule.next_runs = dict.fromkeys(SCOPES, current + timedelta(minutes=30))
        self.reader.job = {'state': 'error', 'code': 'login_required', 'scope': 'south_morang_hyundai'}
        await self.schedule.tick(current)
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'south_morang_hyundai')
        self.assertEqual(self.schedule.metadata('cranbourne')['nextRunAt'],
                         (current + timedelta(minutes=30)).isoformat())

    async def test_disabled_schedule_restores_both_scopes_but_never_reads(self):
        for scope in SCOPES:
            await self.schedule.save(snapshot(scope))
        self.schedule.config = config(False)
        await self.schedule.startup()
        await self.schedule.tick(datetime.now(timezone.utc) + timedelta(days=1))
        self.assertEqual(set(self.reader.latest_by_scope), set(SCOPES))
        self.reader.start.assert_not_called()
        self.assertIsNone(self.schedule.task)
        self.assertIsNone(self.schedule.metadata('south_morang_hyundai')['nextRunAt'])

    async def test_restart_restores_separate_snapshots_and_due_times(self):
        previous = {scope: snapshot(scope) for scope in SCOPES}
        for value in previous.values():
            await self.schedule.save(value)
        await self.schedule.startup()
        self.assertEqual(self.reader.latest, previous['cranbourne'])
        self.assertEqual(self.reader.latest_by_scope, previous)
        await self.schedule.tick()
        self.reader.start.assert_not_called()
        self.assertIsNotNone(self.schedule.task)
        self.assertEqual(set(self.schedule.metadata()['scopes']), set(SCOPES))
        self.assertEqual(json.loads(self.path.read_text()), previous['cranbourne'])

    async def test_legacy_cranbourne_cache_does_not_delay_first_hyundai_read(self):
        previous = snapshot()
        previous.pop('scope')
        await self.schedule.save(previous)
        await self.schedule.startup()
        await self.schedule.tick()
        self.assertEqual(self.reader.latest, previous)
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'south_morang_hyundai')

    async def test_corrupt_hyundai_cache_does_not_discard_cranbourne(self):
        previous = snapshot()
        await self.schedule.save(previous)
        self.schedule.snapshot_path('south_morang_hyundai').write_text('invalid json')
        await self.schedule.startup()
        self.assertEqual(self.reader.latest, previous)
        self.assertNotIn('south_morang_hyundai', self.reader.latest_by_scope)
        await self.schedule.tick()
        self.assertEqual(self.reader.start.call_args.args[0].scope, 'south_morang_hyundai')

    async def test_invalid_snapshots_are_due_immediately(self):
        self.path.parent.mkdir()
        for content in ['invalid json', json.dumps({'state': 'error', 'readOnly': True,
                        'records': []}), 'x' * (8 * 1024 * 1024 + 1)]:
            self.path.write_text(content)
            await self.schedule.startup()
            await self.schedule.shutdown()
            self.assertIsNone(self.reader.latest)
            await self.schedule.tick()
            self.finish()
        self.assertEqual(self.reader.start.call_count, 3)

    async def test_cross_scope_cache_is_rejected(self):
        previous = snapshot('south_morang_hyundai')
        self.path.parent.mkdir()
        self.path.write_text(json.dumps(previous))
        await self.schedule.startup()
        self.assertIsNone(self.reader.latest)
        self.assertEqual(self.reader.latest_by_scope, {})

    async def test_failed_atomic_replace_preserves_both_durable_snapshots(self):
        previous = {scope: snapshot(scope) for scope in SCOPES}
        for value in previous.values():
            await self.schedule.save(value)
        with patch('app.services.dealersocket_lead_schedule.os.replace',
                   side_effect=OSError('disk failure')):
            await self.schedule.save({**previous['south_morang_hyundai'], 'records': []})
        for scope in SCOPES:
            self.assertEqual(json.loads(self.schedule.snapshot_path(scope).read_text()), previous[scope])

    async def test_watch_cancels_cleanly(self):
        self.schedule.task = asyncio.create_task(self.schedule._watch())
        await self.schedule.shutdown()
        self.assertTrue(self.schedule.task.cancelled())

    async def test_completed_reads_survive_restart_without_mixing_default_status(self):
        session = SimpleNamespace(reader_busy=False, lock=asyncio.Lock(), task=None,
            state='connected', message='Complete login',
            page=SimpleNamespace(url='https://bb.dealersocket.com/#/crm'), _run=AsyncMock())
        reader = DealerSocketLeadReader(session)
        reader.schedule = LeadSchedule(reader, config(False), self.path)
        for scope in SCOPES:
            previous = snapshot(scope)
            with patch('app.services.dealersocket_leads.LeadBrowser'), patch(
                    'app.services.dealersocket_leads.collect_snapshot', AsyncMock(return_value=previous)):
                started = reader.start(LeadReadOptions(scope=scope))
                self.assertEqual(started['scope'], scope)
                await reader.task
        self.assertEqual(reader.status()['records'], snapshot()['records'])
        self.assertEqual(reader.status('south_morang_hyundai')['records'],
                         snapshot('south_morang_hyundai')['records'])
        # A failed Hyundai refresh retains Hyundai's original observation timestamp.
        before = reader.status('south_morang_hyundai')
        session.state = 'login_required'
        reader.start(LeadReadOptions(scope='south_morang_hyundai'))
        await reader.task
        failed = reader.status('south_morang_hyundai')
        self.assertEqual(failed['state'], 'error')
        self.assertEqual(failed['code'], 'login_required')
        self.assertEqual(failed['records'], before['records'])
        self.assertEqual(failed['fetchedAt'], before['fetchedAt'])
        self.assertEqual(reader.status()['state'], 'partial')
        self.assertFalse(session.reader_busy)
        # A subsequent Cranbourne job must not hide Hyundai's failed refresh.
        session.state = 'connected'
        with patch('app.services.dealersocket_leads.LeadBrowser'), patch(
                'app.services.dealersocket_leads.collect_snapshot', AsyncMock(return_value=snapshot())):
            reader.start(LeadReadOptions())
            await reader.task
        self.assertEqual(reader.status('south_morang_hyundai')['code'], 'login_required')
        self.assertEqual(reader.status('south_morang_hyundai')['fetchedAt'], before['fetchedAt'])
        restored = DealerSocketLeadReader(SimpleNamespace())
        restored.schedule = LeadSchedule(restored, config(False), self.path)
        await restored.schedule.startup()
        for scope in SCOPES:
            self.assertEqual(restored.status(scope)['records'], snapshot(scope)['records'])
