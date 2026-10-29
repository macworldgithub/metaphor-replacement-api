"""Offline tests. Browser and CRM access are faked; no customer actions."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.services.dealersocket_session import DealerSocketSession, ProfileLease


def config(**overrides):
    return SimpleNamespace(DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED=True,
                           DEALERSOCKET_PROFILE_DIR="unused-test-profile",
                           DEALERSOCKET_CHECK_INTERVAL_SECONDS=30, **overrides)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_actions_return_immediately_while_browser_is_busy(self):
        worker = DealerSocketSession(config())
        gate = asyncio.Event()
        worker._open = AsyncMock(side_effect=lambda **kwargs: None)
        async def open_browser(**kwargs):
            await gate.wait()
        worker._open.side_effect = open_browser
        await worker.start("login")
        await asyncio.sleep(0)
        result = await asyncio.wait_for(worker.start("check"), .2)
        self.assertEqual(result.operation, "login")
        self.assertEqual(worker._open.await_count, 1)
        await worker.shutdown()

    async def test_disabled_login_does_not_start_a_browser(self):
        worker = DealerSocketSession(config())
        worker.config.DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED = False
        with self.assertRaises(PermissionError):
            await worker.start("login")
        self.assertIsNone(worker.task)

    async def test_browser_failures_are_sanitized_and_lease_released(self):
        worker = DealerSocketSession(config())
        worker._open = AsyncMock(side_effect=RuntimeError("secret-cookie-value"))
        await worker.start("check")
        await worker.task
        result = worker.status().model_dump()
        self.assertEqual(result["state"], "error")
        self.assertNotIn("secret-cookie-value", str(result))
        self.assertIsNone(result["operation"])

    async def test_redirect_to_identity_provider_requires_login(self):
        worker = DealerSocketSession(config())
        worker.page = MagicMock(url="https://na.login.solera.com/login")
        worker.page.is_closed.return_value = False
        worker.verified = {"cranbourne_kia"}
        self.assertFalse(await worker._probe())
        self.assertEqual(worker.state, "login_required")
        self.assertFalse(any(roof.verified for roof in worker.status().rooftops))

    async def test_crm_url_alone_is_not_authentication(self):
        worker = DealerSocketSession(config())
        worker.page = MagicMock(url="https://bb.dealersocket.com/#/homepage")
        worker.page.is_closed.return_value = False
        worker.page.locator.return_value.count = AsyncMock(return_value=0)
        self.assertFalse(await worker._probe())
        self.assertEqual(worker.state, "login_required")

    async def test_authenticated_content_and_exact_rooftop_labels(self):
        worker = DealerSocketSession(config())
        page = MagicMock(url="https://bb.dealersocket.com/#/homepage")
        page.is_closed.return_value = False
        iframe = MagicMock()
        iframe.count = AsyncMock(return_value=1)
        candidate = iframe.nth.return_value
        candidate.is_visible = AsyncMock(return_value=True)
        handle = MagicMock()
        child = MagicMock(url="https://bb.dealersocket.com/DealerSocket.Web/Home")
        child.locator.return_value.inner_text = AsyncMock(return_value="Daily Responsibilities Add Quick Up")
        handle.content_frame = AsyncMock(return_value=child)
        candidate.element_handle = AsyncMock(return_value=handle)
        labels = MagicMock()
        labels.all_text_contents = AsyncMock(return_value=["Cranbourne Kia", "Geely Internet Leads"])
        challenges = MagicMock()
        challenges.count = AsyncMock(return_value=0)
        page.locator.side_effect = [iframe, challenges, labels]
        worker.page = page
        self.assertTrue(await worker._probe())
        self.assertEqual(worker.verified, {"cranbourne_kia"})
        self.assertIsNone(worker.status().sessionExpiresAt)
        page.url = "https://na.login.solera.com/verification"
        self.assertFalse(await worker._probe())
        self.assertEqual(worker.state, "login_required")

    async def test_stale_success_does_not_report_connected(self):
        worker = DealerSocketSession(config())
        worker.state = "connected"
        worker.verified = {"cranbourne_kia"}
        worker.last_checked = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
        result = worker.status()
        self.assertEqual(result.state, "disconnected")
        self.assertFalse(any(roof.verified for roof in result.rooftops))

    async def test_long_read_reports_activity_without_faking_a_fresh_check(self):
        worker = DealerSocketSession(config())
        worker.state = 'connected'
        worker.verified = {'cranbourne_kia'}
        worker.last_checked = worker.last_connected = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        worker.reader_busy = True
        result = worker.status()
        self.assertEqual((result.state, result.operation), ('connecting', 'read'))
        self.assertIsNone(result.errorCode)
        self.assertIn('Lead read in progress', result.message)
        self.assertEqual(result.lastCheckedAt, worker.last_checked)
        self.assertEqual(result.lastConnectedAt, worker.last_connected)
        self.assertFalse(any(roof.verified for roof in result.rooftops))
        # Once the read ends, an overdue check must still be treated as overdue.
        worker.reader_busy = False
        self.assertEqual(worker.status().errorCode, 'check_stale')
        worker.reader_busy = True
        for state in ['login_required', 'error', 'disconnected']:
            worker.state = state
            self.assertEqual(worker.status().state, state)
            self.assertNotEqual(worker.status().operation, 'read')

    async def test_visible_verification_overlay_revokes_cached_success(self):
        worker = DealerSocketSession(config())
        worker.state = "connected"
        worker.verified = {"cranbourne_kia"}
        worker.page = MagicMock(url="https://bb.dealersocket.com/#/homepage")
        worker.page.is_closed.return_value = False
        worker.page.locator.return_value.count = AsyncMock(return_value=1)
        self.assertFalse(await worker._probe())
        self.assertEqual(worker.state, "login_required")
        self.assertFalse(any(roof.verified for roof in worker.status().rooftops))

    async def test_unknown_operation_is_rejected_before_browser_launch(self):
        worker = DealerSocketSession(config())
        with self.assertRaises(ValueError):
            await worker.start("send_sms")
        self.assertIsNone(worker.task)

    async def test_profile_lock_excludes_second_process_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            first = ProfileLease(Path(folder) / "profile.lock")
            second = ProfileLease(Path(folder) / "profile.lock")
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()


class ConnectorAPITests(unittest.TestCase):
    def test_configuration_and_authentication_fail_closed(self):
        with TestClient(app) as client:
            with patch.object(settings, "DEALERSOCKET_CONNECTOR_KEY", ""):
                self.assertEqual(client.get("/dealersocket/status").status_code, 503)
            with patch.object(settings, "DEALERSOCKET_CONNECTOR_KEY", "x" * 40):
                self.assertEqual(client.get("/dealersocket/status").status_code, 401)
                response = client.get("/dealersocket/status", headers={"X-Connector-Key": "x" * 40})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("x" * 40, response.text)

    def test_check_and_login_are_distinct_and_never_run_sms(self):
        with TestClient(app) as client, patch.object(settings, "DEALERSOCKET_CONNECTOR_KEY", "x" * 40):
            headers = {"X-Connector-Key": "x" * 40}
            from app.routers.dealersocket import dealersocket_session
            with patch.object(dealersocket_session, "start", new_callable=AsyncMock) as start:
                start.return_value = dealersocket_session.status()
                self.assertEqual(client.post("/dealersocket/check", headers=headers).status_code, 202)
                self.assertEqual(client.post("/dealersocket/login/start", headers=headers).status_code, 202)
                self.assertEqual([call.args[0] for call in start.await_args_list], ["check", "login"])
                self.assertEqual(client.post('/dealersocket/login/automatic/start', headers=headers).status_code, 202)
                self.assertEqual(start.await_args_list[-1].args[0], 'automatic_login')
            with patch.object(dealersocket_session, "start", new_callable=AsyncMock, side_effect=PermissionError):
                self.assertEqual(client.post("/dealersocket/login/start", headers=headers).status_code, 403)


if __name__ == "__main__":
    unittest.main()
