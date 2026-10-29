"""Single-connector scheduled reads and atomic private snapshot storage. No CRM writes."""
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


class LeadSchedule:
    def __init__(self, reader, config=settings, path=None):
        self.reader, self.config = reader, config
        self.path = Path(path or config.DEALERSOCKET_LEAD_SNAPSHOT_PATH)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parents[2] / self.path
        self.next_run = None
        self.task = None

    def metadata(self):
        return {'enabled': self.config.DEALERSOCKET_LEAD_SYNC_ENABLED,
                'intervalSeconds': self.config.DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS,
                'nextRunAt': self.next_run.isoformat() if self.config.DEALERSOCKET_LEAD_SYNC_ENABLED and self.next_run else None}

    async def save(self, snapshot):
        def write():
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix('.tmp')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
                json.dump(snapshot, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        try:
            await asyncio.to_thread(write)
        except OSError:
            log.warning('DealerSocket snapshot could not be saved; current in-memory results remain available.')

    async def startup(self):
        try:
            def load():
                if self.path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('snapshot_too_large')
                return json.loads(self.path.read_text(encoding='utf-8'))
            previous = await asyncio.to_thread(load)
            if (previous.get('state') not in ('complete', 'partial') or previous.get('readOnly') is not True
                    or not isinstance(previous.get('records'), list) or len(previous['records']) > 1000):
                raise ValueError('invalid_snapshot')
            fetched = datetime.fromisoformat(previous['fetchedAt'])
            if fetched.tzinfo is None:
                raise ValueError('invalid_timestamp')
            self.reader.latest = previous
            # Do not postpone reads indefinitely if the host clock moved backwards.
            self.next_run = min(fetched, datetime.now(timezone.utc)) + timedelta(seconds=self.config.DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.next_run = datetime.now(timezone.utc)
        if self.config.DEALERSOCKET_LEAD_SYNC_ENABLED:
            self.task = asyncio.create_task(self._watch())

    async def tick(self, current=None):
        current = current or datetime.now(timezone.utc)
        # A successful reauthentication unblocks the failed read immediately.
        if (isinstance(getattr(self.reader, 'job', None), dict) and self.reader.job.get('state') == 'error'
                and self.reader.job.get('code') in ('login_required', 'session_unavailable')
                and self.reader.session.state == 'connected'):
            self.next_run = current
        if not self.config.DEALERSOCKET_LEAD_SYNC_ENABLED or self.next_run and current < self.next_run:
            return
        # Reads already in progress satisfy this cycle. Short session checks must
        # not discard the hour: retry admission after they release the browser.
        self.next_run = current + timedelta(seconds=self.config.DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS)
        try:
            self.reader.start(LeadReadOptions(maxRecords=1000, detailLimit=200))
        except RuntimeError:
            if self.reader.session.reader_busy:
                log.info('Scheduled DealerSocket read skipped: a lead read is already running.')
            else:
                self.next_run = current + timedelta(seconds=10)
                log.info('Scheduled DealerSocket read deferred: session operation busy; retrying shortly.')

    async def _watch(self):
        while True:
            # Allow the HTTP server to become available before the first cold-start read.
            # Poll between the session monitor's 30-second checks; using the same
            # cadence can repeatedly collide with its browser lock.
            await asyncio.sleep(5)
            try:
                await self.tick()
            except Exception:
                log.warning('Scheduled DealerSocket read could not start; retrying at the next interval.')

    async def shutdown(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
