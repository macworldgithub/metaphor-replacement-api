"""Opt-in Windows runner smoke test: temporary loopback server, no CRM access."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from urllib.request import Request, urlopen


@unittest.skipUnless(os.getenv("RUN_CONNECTOR_BROWSER_TEST") == "1", "Opt-in subprocess smoke test")
class ConnectorRunnerTests(unittest.TestCase):
    def test_runner_serves_authenticated_status_without_launching_browser(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        env = {**os.environ, "PORT": str(port),
               "DEALERSOCKET_LEAD_SYNC_ENABLED": "false",
               "DEALERSOCKET_CONNECTOR_KEY": "test-only-connector-key-at-least-32-characters"}
        root = Path(__file__).resolve().parents[1]
        process = subprocess.Popen([sys.executable, str(root / "run_connector.py")],
            cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                try:
                    request = Request(f"http://127.0.0.1:{port}/dealersocket/status",
                        headers={"X-Connector-Key": env["DEALERSOCKET_CONNECTOR_KEY"]})
                    with urlopen(request, timeout=1) as response:
                        data = json.load(response)
                        self.assertEqual(data["state"], "disconnected")
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                        self.assertIsNone(data["sessionExpiresAt"])
                    break
                except OSError:
                    if process.poll() is not None:
                        self.fail("Connector exited during startup")
                    time.sleep(.2)
            else:
                self.fail("Connector startup timed out")
        finally:
            process.terminate()
            process.wait(timeout=10)
