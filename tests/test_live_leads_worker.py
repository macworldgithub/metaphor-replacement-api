"""CLI refresh timing and safety tests; connector traffic is mocked."""
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from app.config import settings
from scripts import live_dealersocket_leads as worker


class WorkerTests(unittest.TestCase):
    def test_watch_repeats_fresh_reads_and_waits_after_completion(self):
        snapshot = {"state":"partial","fetchedAt":"2026-10-15T07:00:00Z","durationSeconds":1,
                    "records":[],"rooftops":[],"coverage":[],"errors":[],"review":[],"limited":False,"coverageNote":"selected queues only"}
        with patch('sys.argv',['worker','--watch','--cycles','2','--interval','10','--summary-only']), \
             patch.object(settings,'DEALERSOCKET_CONNECTOR_KEY','x'*40), \
             patch.object(worker,'fetch_snapshot',return_value=snapshot) as fetch, \
             patch.object(worker.time,'sleep') as sleep, redirect_stdout(StringIO()):
            self.assertEqual(worker.main(),2)
            self.assertEqual(fetch.call_count,2)
            sleep.assert_called_once_with(10)
            self.assertEqual(fetch.call_args.args[2],{"maxRecords":200,"detailLimit":50})

    def test_control_characters_in_customer_values_are_not_executed(self):
        self.assertNotIn('\x1b',worker.clean('\x1b[2JCustomer'))

    def test_new_snapshot_replacement_is_rejected(self):
        with patch.object(worker,'request_json',side_effect=[{"state":"running","jobId":"one"},
                {"state":"complete","jobId":"two"}]),patch.object(worker.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'replaced'):
                worker.fetch_snapshot('http://127.0.0.1:8000','unused',{})
