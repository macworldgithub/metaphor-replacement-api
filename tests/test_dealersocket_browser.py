"""Real Chrome test using intercepted synthetic pages only; no DealerSocket traffic."""
import os
import tempfile
import unittest
from pathlib import Path

from app.services.dealersocket_session import DealerSocketSession


@unittest.skipUnless(os.getenv("RUN_CONNECTOR_BROWSER_TEST") == "1", "Opt-in installed-Chrome fixture test")
class BrowserFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_profile_authentication_overlay_and_restart(self):
        from playwright.async_api import async_playwright
        worker = DealerSocketSession()
        main = '''<html><body><md-select id="select_1"><md-option>Cranbourne Kia</md-option>
          <md-option>Cranbourne MG</md-option><md-option>Geely Cranbourne</md-option></md-select>
          <iframe id="app-homepage" src="https://bb.dealersocket.com/fixture-frame"></iframe></body></html>'''
        async def fixture(route):
            body = "<body>Add Quick Up Daily Responsibilities</body>" if route.request.url.endswith("fixture-frame") else main
            await route.fulfill(status=200, content_type="text/html", body=body)
        with tempfile.TemporaryDirectory() as folder:
            async with async_playwright() as playwright:
                for attempt in range(2):
                    context = await playwright.chromium.launch_persistent_context(
                        str(Path(folder) / "profile"), channel="chrome", headless=True)
                    try:
                        # Intercept every network request; this test never reaches CRM.
                        await context.route("**/*", fixture)
                        worker.context = context
                        worker.page = context.pages[0]
                        await worker.page.goto("https://bb.dealersocket.com/#/homepage")
                        await worker.page.frame_locator("#app-homepage").locator("body").wait_for()
                        self.assertTrue(await worker._probe())
                        self.assertEqual(len(worker.verified), 3)
                        if attempt == 0:
                            await context.add_cookies([{"name": "fixture_session", "value": "synthetic",
                                "url": "https://bb.dealersocket.com", "expires": 2000000000}])
                        else:
                            self.assertTrue(any(c["name"] == "fixture_session" for c in await context.cookies()))
                        await worker.page.evaluate('document.body.insertAdjacentHTML("afterbegin", \'<input autocomplete="one-time-code">\')')
                        self.assertFalse(await worker._probe())
                        self.assertEqual(worker.state, "login_required")
                        self.assertFalse(worker.verified)
                    finally:
                        await context.close()
                        worker.context = worker.page = None
