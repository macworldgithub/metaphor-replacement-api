import unittest
from scripts.run_hyundai_chrome_worker import browser_child_environment


class ChromeWorkerEnvironmentTests(unittest.TestCase):
    def test_browser_process_never_inherits_campaign_or_database_secrets(self):
        private = ['MONGODB_URI', 'GOODSHOWROOM_API_KEY', 'MOBILEMESSAGE_API_PASSWORD', 'DEALERSOCKET_PASSWORD', 'PORTAL_API_KEY', 'LEAD_WEBHOOK_KEY', 'SMS_PRIVATE_KEY', 'CAMPAIGN_BACKEND_URL']
        source = {key: 'test-secret' for key in private}
        source.update({'PATH': 'system-path', 'PLAYWRIGHT_MCP_EXTENSION_TOKEN': 'supported-extension-token'})
        child = browser_child_environment(source)
        for key in private:
            self.assertNotIn(key, child)
        self.assertEqual(child['PATH'], 'system-path')
        self.assertEqual(child['PLAYWRIGHT_MCP_EXTENSION_TOKEN'], 'supported-extension-token')
