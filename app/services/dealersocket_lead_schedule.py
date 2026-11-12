"""Serialized per-scope CRM reads and atomic private snapshot storage."""
import asyncio
import json
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.config import settings
from app.models.dealersocket_leads import LeadReadOptions

log = logging.getLogger(__name__)
SCOPES = ('cranbourne', 'south_morang_hyundai')


class LeadSchedule:
    def __init__(self, reader, config=settings, path=None):
        self.reader, self.config = reader, config
        self.path = Path(path or config.DEALERSOCKET_LEAD_SNAPSHOT_PATH)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parents[2] / self.path
        self.next_runs = dict.fromkeys(SCOPES)
        self.task = None

    def metadata(self, scope='cranbourne'):
        due = self.next_runs[scope]
        return {'enabled': self.config.DEALERSOCKET_LEAD_SYNC_ENABLED,
                'intervalSeconds': self.config.DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS,
                'nextRunAt': due.isoformat() if self.config.DEALERSOCKET_LEAD_SYNC_ENABLED and due else None,
                'scopes': list(SCOPES)}

    def snapshot_path(self, scope):
        if scope not in SCOPES:
            raise ValueError('invalid_snapshot_scope')
        return self.path if scope == 'cranbourne' else self.path.with_name(
            f'{self.path.stem}-{scope}{self.path.suffix}')

    async def save(self, snapshot):
        path = self.snapshot_path(snapshot.get('scope', 'cranbourne'))
        def write():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix('.tmp')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
                json.dump(snapshot, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        try:
            await asyncio.to_thread(write)
        except OSError:
            log.warning('DealerSocket snapshot could not be saved; current in-memory results remain available.')

    async def startup(self):
        for scope in SCOPES:
            path = self.snapshot_path(scope)
            try:
                def load():
                    if path.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError('snapshot_too_large')
                    return json.loads(path.read_text(encoding='utf-8'))
                previous = await asyncio.to_thread(load)
                if (previous.get('scope', 'cranbourne') != scope
                        or previous.get('state') not in ('complete', 'partial')
                        or previous.get('readOnly') is not True
                        or not isinstance(previous.get('records'), list) or len(previous['records']) > 1000):
                    raise ValueError('invalid_snapshot')
                fetched = datetime.fromisoformat(previous['fetchedAt'])
                if fetched.tzinfo is None:
                    raise ValueError('invalid_timestamp')
                self.reader.latest_by_scope[scope] = previous
                if scope == 'cranbourne':
                    self.reader.latest = previous
                self.next_runs[scope] = min(fetched, datetime.now(timezone.utc)) + timedelta(
                    seconds=self.config.DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.next_runs[scope] = datetime.now(timezone.utc)
        if self.config.DEALERSOCKET_LEAD_SYNC_ENABLED:
            self.task = asyncio.create_task(self._watch())

    async def tick(self, current=None):
        current = current or datetime.now(timezone.utc)
        if not self.config.DEALERSOCKET_LEAD_SYNC_ENABLED:
            return
        # One browser serves both scopes. Keep the other scope due while a read runs.
        if self.reader.session.reader_busy:
            return
        job = getattr(self.reader, 'job', None)
        if (isinstance(job, dict) and job.get('state') == 'error'
                and job.get('code') in ('login_required', 'session_unavailable')
                and self.reader.session.state == 'connected'):
            scope = job.get('scope', 'cranbourne')
            if scope in self.next_runs:
                self.next_runs[scope] = current
        scope = min(SCOPES, key=lambda name: self.next_runs[name] or current)
        if self.next_runs[scope] and current < self.next_runs[scope]:
            return
        try:
            self.reader.start(LeadReadOptions(scope=scope, maxRecords=1000, detailLimit=200))
        except RuntimeError:
            # Login/session checks must not discard a scheduled scope.
            self.next_runs[scope] = current + timedelta(seconds=10)
            log.info('Scheduled DealerSocket read deferred scope=%s: browser busy.', scope)
        else:
            self.next_runs[scope] = current + timedelta(
                seconds=self.config.DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS)
            log.info('Scheduled DealerSocket read admitted scope=%s', scope)

    async def _watch(self):
        while True:
            await asyncio.sleep(5)
            try:
                await self.tick()
            except Exception:
                log.warning('Scheduled DealerSocket read could not start; retrying shortly.')

    async def shutdown(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
