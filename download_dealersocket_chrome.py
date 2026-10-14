"""Reuse signed-in Chrome through Playwright MCP's extension (no CDP port).

Install: python -m pip install "mcp>=1.28,<2" python-dotenv
Check:   python outputs/download_dealersocket_chrome.py --check
Export:  python outputs/download_dealersocket_chrome.py --download

Node.js/npx and the Playwright extension must be installed. Keep Chrome open.
Exports are limited to the three saved UNASSIGNED queues; the live report's
Excel export has not been verified. Missing controls cause an explicit error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
QUEUES = [
    ("cranbourne-kia", "Cranbourne Kia Internet Leads Unassigned"),
    ("cranbourne-mg", "Cranbourne MG Internet Leads Unassigned"),
    ("geely-cranbourne", "Geely Internet Leads - UNASSIGNED"),
]

PREFLIGHT = r"""async (page) => {
    const isDealerSocket = p => {
        try { return new URL(p.url()).hostname === 'bb.dealersocket.com'; }
        catch { return false; }
    };
    const target = page.context().pages().find(p =>
        isDealerSocket(p) && p.url().includes('/crm/employee'));
    if (!target) throw new Error('No shared DealerSocket Opportunities tab. '
        + 'Use the extension to share that tab with this script. '
        + 'If another client owns it, disconnect that client in the extension status page.');
    const ua = await target.evaluate(() => navigator.userAgent);
    if (!ua.includes('Chrome/') || /Edg\//.test(ua))
        throw new Error('The extension connected to another browser. '
            + 'Use the token from the Chrome profile where DealerSocket is signed in.');
    const queue = target.frameLocator('#app-crm')
        .locator('select[name="filter-dropdown"]');
    try { await queue.waitFor({timeout:20000}); }
    catch { throw new Error('DealerSocket opportunities are unavailable. '
        + 'Open Opportunities in the signed-in Chrome tab. '
        + 'If the session expired, complete DealerSocket login there.'); }
    const expected = __QUEUES__;
    const options = await queue.locator('option').allTextContents();
    const missing = expected.filter(x => !options.includes(x));
    if (missing.length) throw new Error('Missing queue access: ' + missing.join(', '));
    return {browser:'Google Chrome',authenticated:true,url:target.url(),queues:expected};
}""".replace("__QUEUES__", json.dumps([q for _, q in QUEUES]))

EXPORT = r"""async (page) => {
    const cfg = __CONFIG__;
    const target = page.context().pages().find(p => {
        try { return new URL(p.url()).hostname === 'bb.dealersocket.com'
            && p.url().includes('/crm/employee'); } catch { return false; }
    });
    if (!target) throw new Error('Shared Opportunities tab no longer available.');
    const crm = target.frameLocator('#app-crm');
    const dropdown = crm.locator('select[name="filter-dropdown"]');
    const previous = await dropdown.inputValue();
    let report;
    try {
        await dropdown.selectOption({label:cfg.queue});
        await target.waitForTimeout(2000);
        await crm.getByText(/\bResults? Found\b/i).waitFor({timeout:20000});
        const dialog = crm.getByRole('dialog');
        if (await dialog.isVisible()) await dialog.getByRole('button').click();
        await crm.locator('md-menu').getByRole('button').click();
        await crm.getByRole('menuitem',{name:'View a Report'}).click();
        const pending = target.context().waitForEvent('page',{timeout:15000});
        await crm.getByRole('link',{name:'Event Details (With Notes)',exact:true}).click();
        report = await pending;
        await report.waitForLoadState('domcontentloaded');
        let control;
        const deadline = Date.now() + 20000;
        while (!control && Date.now() < deadline) {
            for (const frame of report.frames()) {
                for (const role of ['button','link','menuitem']) {
                    const candidates = frame.getByRole(role,{name:/Excel|XLSX|\bXLS\b/i});
                    for (const item of await candidates.all()) {
                        if (await item.isVisible()) { control = item; break; }
                    }
                    if (control) break;
                }
                if (control) break;
            }
            if (!control) await report.waitForTimeout(500);
        }
        if (!control) throw new Error('This report exposes no verified Excel export control. '
            + 'Verify the Excel export button in the DealerSocket report.');

        const [download] = await Promise.all([
            report.waitForEvent('download', {timeout: 30000}),
            control.click()
        ]);
        const downloadPath = await download.path();
        return { success: true, path: downloadPath, filename: download.suggestedFilename() };
    } finally {
        if (report) await report.close();
        if (previous) {
            try { await dropdown.selectOption(previous); } catch (_) {}
        }
    }
}"""


async def run_in_chrome(js_code: str) -> dict | list | str:
    """Execute JavaScript in the shared browser via Playwright MCP stdio."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        raise SystemExit(
            "The 'mcp' package is required. Install it using:\n"
            '  python -m pip install "mcp>=1.28,<2" python-dotenv'
        )

    env = dict(os.environ)
    token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "")
    args = ["-y", "@playwright/mcp@latest", "--extension"]
    if token:
        args.extend(["--token", token])

    server_params = StdioServerParameters(
        command="npx",
        args=args,
        env=env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool(
                "browser_run_code_unsafe",
                arguments={"code": js_code},
            )

            results = []
            for item in response.content:
                if getattr(item, "type", None) == "text" or hasattr(item, "text"):
                    text = item.text
                    try:
                        results.append(json.loads(text))
                    except (json.JSONDecodeError, TypeError):
                        results.append(text)

            if len(results) == 1:
                return results[0]
            return results


async def run_check() -> None:
    print("Running DealerSocket preflight check via Playwright MCP extension...")
    try:
        result = await run_in_chrome(PREFLIGHT)
        print("✓ Preflight check succeeded:")
        print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)
    except Exception as e:
        print(f"✗ Preflight check failed: {e}", file=sys.stderr)
        sys.exit(1)


async def run_download() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = BASE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Beginning DealerSocket report downloads to: {output_dir}")

    for slug, queue_name in QUEUES:
        print(f"\n[+] Processing queue: {queue_name} ({slug})...")
        code = EXPORT.replace("__CONFIG__", json.dumps({"queue": queue_name}))
        try:
            res = await run_in_chrome(code)
            print(f"Response: {res}")
            if isinstance(res, dict) and res.get("path"):
                src = Path(res["path"])
                target_filename = f"dealersocket-{slug}-{stamp}.xlsx"
                dest = output_dir / target_filename
                if src.exists():
                    shutil.copyfile(src, dest)
                    print(f"✓ Saved export to: {dest}")
        except Exception as e:
            print(f"✗ Error processing {slug}: {e}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download DealerSocket opportunity reports via Chrome Playwright MCP extension."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check connection to DealerSocket in shared Chrome tab.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download Excel reports for all unassigned queues.",
    )

    args = parser.parse_args()
    if args.check:
        asyncio.run(run_check())
    elif args.download:
        asyncio.run(run_download())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()