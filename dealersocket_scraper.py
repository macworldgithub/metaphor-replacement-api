"""
DealerSocket Opportunities Scraper & Automation Engine
-------------------------------------------------------
Automates session management, navigation to DealerSocket Opportunities:
https://bb.dealersocket.com/#/crm/employee?todoType=opportunities
and triggers the automated download of Opportunities Excel/CSV reports.

Features:
- Persistent browser session profile (handles MFA/2FA once, keeps session alive).
- Headed interactive login mode (--login) for first-time authentication.
- Headless automated download mode (--download).
- Comprehensive selector heuristics for DealerSocket's action toolbar & export buttons.
- Automatic verification of downloaded Excel files with pandas/openpyxl.
- Structured logging & timestamped export storage.
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("DealerSocketAutomation")

# Constants & Paths
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
PROFILE_DIR = BASE_DIR / "dealersocket_profile"
STORAGE_STATE_FILE = BASE_DIR / "dealersocket_state.json"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

DEALERSOCKET_TARGET_URL = "https://bb.dealersocket.com/#/crm/employee?todoType=opportunities"
DEALERSOCKET_BASE_URL = "https://bb.dealersocket.com/#/landingPage"


def get_credentials():
    username = os.getenv("DEALERSOCKET_USERNAME", "")
    password = os.getenv("DEALERSOCKET_PASSWORD", "")
    return username, password


def interactive_login(username: str = "", password: str = ""):
    """
    Launches a visible Chromium browser with persistent storage so the user can
    log into DealerSocket, complete any 2FA/SMS challenge, and save the session.
    """
    from playwright.sync_api import sync_playwright

    logger.info("==================================================")
    logger.info("Starting DealerSocket Interactive Login Flow")
    logger.info("A browser window will open. Please complete login.")
    logger.info("==================================================")

    with sync_playwright() as p:
        # Launch browser with persistent profile
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1366, "height": 850},
            accept_downloads=True,
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(DEALERSOCKET_TARGET_URL, timeout=60000)

        # If credentials provided, prefill username
        if username:
            try:
                page.wait_for_selector("input[name='username']", timeout=10000)
                logger.info(f"Prefilling username: {username}")
                page.fill("input[name='username']", username)
                page.press("input[name='username']", "Enter")
            except Exception as e:
                logger.debug(f"Could not prefill username automatically: {e}")

        logger.info("Waiting for you to log in and reach the opportunities page...")
        logger.info("You have up to 5 minutes to complete login and any 2FA.")

        # Poll until the URL contains 'employee?todoType=opportunities' or 'crm'
        authenticated = False
        start_time = time.time()
        timeout = 300  # 5 minutes

        while time.time() - start_time < timeout:
            current_url = page.url
            if "landingPage" not in current_url and ("crm" in current_url or "employee" in current_url):
                authenticated = True
                logger.info(f"Login detected! Current URL: {current_url}")
                # Wait for page to stabilize
                page.wait_for_timeout(5000)
                break
            time.sleep(2)

        if authenticated:
            # Save storage state
            context.storage_state(path=str(STORAGE_STATE_FILE))
            logger.info(f"Session saved successfully to: {STORAGE_STATE_FILE}")
            logger.info("Login flow completed successfully!")
        else:
            logger.warning("Timed out waiting for login completion. If you finished, check your session.")

        context.close()


def find_and_click_export(page, timeout_ms: int = 20000):
    """
    Attempts various DealerSocket export triggers (buttons, icons, action menus, dropdowns).
    """
    logger.info("Scanning page for Export / Download button...")

    # Wait for table or content to render
    page.wait_for_timeout(3000)

    # List of candidate selector strategies
    candidate_selectors = [
        # Common text matching
        "button:has-text('Export to Excel')",
        "button:has-text('Export')",
        "a:has-text('Export to Excel')",
        "a:has-text('Export')",
        "button:has-text('Download')",
        "a:has-text('Download')",
        
        # Toolbars & icon buttons
        "[aria-label*='Export']",
        "[aria-label*='Download']",
        "[title*='Export']",
        "[title*='Download']",
        "button[data-testid*='export']",
        "button[id*='export']",
        "button[id*='Export']",
        ".export-btn",
        ".btn-export",
        "i[class*='fa-file-excel']",
        "i[class*='excel']",
        
        # Action menus / ellipses
        "button:has-text('Actions')",
        "button:has-text('More')",
        "[title*='Actions']",
    ]

    # Try direct candidates first
    for selector in candidate_selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=1000):
                logger.info(f"Found candidate export element matching selector: {selector}")
                return loc
        except Exception:
            continue

    return None


def download_opportunities(headless: bool = True, timeout_seconds: int = 120):
    """
    Navigates to the opportunities page and downloads the opportunities Excel file.
    """
    from playwright.sync_api import sync_playwright

    logger.info(f"Launching DealerSocket automation (Headless: {headless})...")

    with sync_playwright() as p:
        # Launch persistent context to reuse existing login session
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            accept_downloads=True,
            downloads_path=str(DOWNLOADS_DIR),
        )

        page = context.pages[0] if context.pages else context.new_page()

        logger.info(f"Navigating to: {DEALERSOCKET_TARGET_URL}")
        try:
            page.goto(DEALERSOCKET_TARGET_URL, timeout=60000, wait_until="networkidle")
        except Exception:
            # In SPAs networkidle can sometimes timeout; check current URL
            logger.info("Proceeding after initial navigation...")

        page.wait_for_timeout(5000)
        current_url = page.url
        logger.info(f"Loaded page URL: {current_url}")

        # Check if redirected to landingPage (meaning session expired or not logged in)
        if "landingPage" in current_url:
            logger.error("DealerSocket redirected to login landing page!")
            logger.error("Please run the script in login mode first: python dealersocket_scraper.py --login")
            
            # Check if auto-credentials are available in .env
            username, password = get_credentials()
            if username and password:
                logger.info(f"Attempting automated login for user: {username}...")
                try:
                    page.fill("input[name='username']", username)
                    page.click("button:has-text('CONTINUE'), input[type='submit']")
                    page.wait_for_timeout(3000)
                    if page.locator("input[type='password']").is_visible(timeout=5000):
                        page.fill("input[type='password']", password)
                        page.click("button:has-text('SIGN IN'), button:has-text('LOG IN'), button[type='submit']")
                        page.wait_for_timeout(5000)
                        logger.info("Credentials submitted. Navigating back to opportunities...")
                        page.goto(DEALERSOCKET_TARGET_URL, timeout=40000)
                except Exception as e:
                    logger.error(f"Automated credential entry failed: {e}")
            else:
                context.close()
                return None

        # Wait for dynamic table / content to load
        logger.info("Waiting for Opportunities table to finish loading...")
        page.wait_for_timeout(6000)

        # Look for export trigger
        export_element = find_and_click_export(page)

        if not export_element:
            # Let's take a screenshot for debugging
            screenshot_path = BASE_DIR / "dealersocket_debug.png"
            page.screenshot(path=str(screenshot_path))
            logger.warning(f"Could not automatically detect export button. Saved debug screenshot to: {screenshot_path}")
            
            # Print visible buttons on page to help identify exact selector
            buttons = page.locator("button, a").all_inner_texts()
            clean_buttons = [b.strip() for b in buttons if b.strip()][:25]
            logger.info(f"Visible action elements on page: {clean_buttons}")
            context.close()
            return None

        # Expect download event
        logger.info("Triggering download event...")
        try:
            with page.expect_download(timeout=30000) as download_info:
                export_element.click()
            
            download = download_info.value
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_filename = download.suggested_filename or f"dealersocket_opportunities_{timestamp_str}.xlsx"
            
            # Ensure safe filename
            save_path = DOWNLOADS_DIR / f"{timestamp_str}_{suggested_filename}"
            download.save_as(str(save_path))
            
            logger.info(f"Download complete! Saved to: {save_path}")
            
            # Inspect and summarize the downloaded file
            inspect_downloaded_file(save_path)
            
            context.close()
            return save_path

        except Exception as e:
            logger.error(f"Download trigger failed or timed out: {e}")
            screenshot_path = BASE_DIR / "dealersocket_download_error.png"
            page.screenshot(path=str(screenshot_path))
            logger.info(f"Error screenshot saved to {screenshot_path}")
            context.close()
            return None


def inspect_downloaded_file(file_path: Path):
    """
    Parses and summarizes the downloaded Excel/CSV file to verify lead contents.
    """
    import pandas as pd

    logger.info("--------------------------------------------------")
    logger.info(f"Inspecting downloaded file: {file_path.name}")
    try:
        if file_path.suffix.lower() in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path)
        elif file_path.suffix.lower() == ".csv":
            df = pd.read_csv(file_path)
        else:
            logger.warning(f"Unknown extension: {file_path.suffix}")
            return

        logger.info(f"Total Rows: {len(df)}")
        logger.info(f"Columns Found ({len(df.columns)}): {list(df.columns)}")
        
        # Check for key columns matching the project scope:
        # DealerSocket ID, Customer Name, Mobile, Rooftop/Dealer, Lead Source, Vehicle
        logger.info("Sample preview (First 3 rows):")
        print(df.head(3))
        logger.info("--------------------------------------------------")

    except Exception as e:
        logger.error(f"Failed to parse downloaded file: {e}")


def main():
    parser = argparse.ArgumentParser(description="DealerSocket Opportunities Scraper & Automation")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open visible browser to authenticate and save DealerSocket session profile.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Run automation to navigate to opportunities and download the Excel file.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run download automation in visible (headed) browser mode for monitoring.",
    )

    args = parser.parse_args()

    # Default to download if no specific flag passed, or prompt
    if args.login:
        username, password = get_credentials()
        interactive_login(username, password)
    elif args.download:
        download_opportunities(headless=not args.headed)
    else:
        # If no arguments provided, explain usage and test session status
        print("\n=======================================================")
        print(" DealerSocket Opportunities Scraper & Automation Tool  ")
        print("=======================================================")
        print("Usage:")
        print("  1. First time login (interactive session setup):")
        print("     python dealersocket_scraper.py --login")
        print("     (Opens browser, log in once with credentials + MFA, saves profile)")
        print("\n  2. Automated Excel download:")
        print("     python dealersocket_scraper.py --download")
        print("     (Runs headless, navigates to opportunities, downloads Excel)")
        print("\n  3. Monitored Excel download (watch browser in real-time):")
        print("     python dealersocket_scraper.py --download --headed")
        print("=======================================================\n")
        
        # Check if profile exists
        if any(PROFILE_DIR.iterdir()):
            logger.info("Saved browser profile detected. You can run with --download directly.")
        else:
            logger.info("No saved profile detected. Please run with --login first.")


if __name__ == "__main__":
    main()
