# -*- coding: utf-8 -*-
"""
diag_playwright.py — Windows Playwright diagnostic for Polymarket pages.

What it does:
  1. Asks for a Polymarket 5-minute BTC URL.
  2. Launches headless Chromium via Playwright.
  3. Navigates and waits for the page to render.
  4. Dumps the rendered text to playwright_dump.txt.
  5. Searches for 'Price to Beat' and dollar amounts.
  6. Prints a clear PASS / FAIL diagnosis.

Run:
  py diag_playwright.py
"""

import asyncio
import re
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except Exception as e:
    print(f"ERROR: cannot import playwright — {type(e).__name__}: {e}")
    sys.exit(1)


async def main():
    print("Paste a Polymarket BTC 5-min URL (or press Enter for default):")
    url = input().strip()
    if not url:
        # Default: an active BTC 5m market URL pattern with current epoch
        import time
        epoch = (int(time.time()) // 300) * 300
        url = f"https://polymarket.com/event/btc-updown-5m-{epoch}"
        print(f"Using default: {url}")

    print(f"\n[1/5] Launching headless Chromium...")
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            except Exception as e:
                print(f"  FAIL: cannot launch Chromium — {type(e).__name__}: {e}")
                print(f"  -> likely Chromium binary missing. Run: py -m playwright install chromium")
                return

            print(f"[2/5] Opening page (60s timeout)...")
            try:
                page = await browser.new_page(
                    viewport={"width": 1600, "height": 1400},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if resp:
                    print(f"  HTTP status: {resp.status}")
                else:
                    print(f"  WARN: page.goto returned no response object")
            except Exception as e:
                print(f"  FAIL navigating: {type(e).__name__}: {e}")
                await browser.close()
                return

            print(f"[3/5] Waiting 8 seconds for JS-rendered content...")
            await page.wait_for_timeout(8000)

            print(f"[4/5] Extracting body text...")
            try:
                body_text = await page.locator("body").inner_text(timeout=10000)
            except Exception as e:
                print(f"  WARN: inner_text failed: {e}, falling back to text_content")
                try:
                    body_text = await page.text_content("body") or ""
                except Exception as e2:
                    print(f"  FAIL: cannot extract any text — {type(e2).__name__}: {e2}")
                    body_text = ""
            try:
                full_html = await page.content()
            except Exception:
                full_html = ""

            dump_path = Path(__file__).parent / "playwright_dump.txt"
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write("=== BODY INNER TEXT ===\n")
                f.write(body_text or "(empty)\n")
                f.write("\n\n=== FULL HTML LENGTH ===\n")
                f.write(f"{len(full_html)} chars\n")
                f.write("\n=== FIRST 3000 CHARS OF HTML ===\n")
                f.write(full_html[:3000])
            print(f"  Wrote dump to: {dump_path}")

            await browser.close()

            # Diagnose
            print(f"\n[5/5] Diagnosis:")
            text_len = len(body_text)
            html_len = len(full_html)
            print(f"  body inner text length: {text_len} chars")
            print(f"  full HTML length:       {html_len} chars")

            if text_len < 50 and html_len < 500:
                print(f"\n  >>> RESULT: page is essentially EMPTY")
                print(f"      Likely cause: Polymarket is detecting the headless browser and refusing to serve.")
                print(f"      Or the page isn't loading JS at all (network issue).")
                return

            has_label = bool(re.search(r"Price\s*to\s*Beat", body_text, re.IGNORECASE))
            print(f"  contains 'Price to Beat': {has_label}")

            if not has_label:
                print(f"\n  >>> RESULT: page loaded but does NOT contain the 'Price to Beat' label")
                print(f"      Possible causes:")
                print(f"        - URL is for an inactive/expired market (Polymarket showed a different layout)")
                print(f"        - Polymarket changed the label text (e.g. 'Price to beat:' vs 'Target price')")
                print(f"      Open {dump_path.name} and search for the actual target value to confirm.")
                return

            # Look for dollar amount near the label
            m = re.search(r"Price\s*to\s*Beat[\s\S]{0,400}?\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)",
                          body_text, re.IGNORECASE)
            if m:
                val = m.group(1).replace(",", "")
                print(f"\n  >>> RESULT: PASS — found 'Price to Beat' with value ${val}")
                print(f"      Playwright on Windows DOES render Polymarket correctly.")
                print(f"      The issue is in V3's regex/window logic, not Playwright itself.")
            else:
                print(f"\n  >>> RESULT: page contains 'Price to Beat' but no $ amount near it")
                print(f"      Open {dump_path.name} and look for the target — maybe it's in a different format.")
    except Exception as e:
        print(f"\nUnhandled error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    # IMPORTANT: do NOT set WindowsSelectorEventLoopPolicy — it breaks Playwright.
    # Playwright needs subprocess support which only ProactorEventLoop (the default
    # on Windows for Python 3.8+) provides.
    asyncio.run(main())
