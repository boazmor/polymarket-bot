# -*- coding: utf-8 -*-
"""
diag_no_browser.py — extract Polymarket target WITHOUT a browser.

Fetches the Polymarket page with plain HTTP and looks for the target price in:
  1. __NEXT_DATA__ JSON (Next.js hydration data — the most reliable source)
  2. Inline JSON in <script> tags
  3. Plain HTML text

If any of these find the target, we can rewrite the bot to NOT need Playwright
at all on Windows.

Run:
  py diag_no_browser.py
"""
import json
import re
import sys
import time

import requests


def extract_target_from_next_data(html: str, target_slug: str):
    """Polymarket is built on Next.js — the page contains a __NEXT_DATA__ script
    tag with all the page's hydration data.  Inside that JSON the events array
    contains many markets; we filter to the one whose slug matches the URL we
    requested, and return that event's priceToBeat.
    """
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return None, "__NEXT_DATA__ not found"
    raw = m.group(1)
    try:
        data = json.loads(raw)
    except Exception as e:
        return None, f"__NEXT_DATA__ parse error: {e}"

    # Find ALL events anywhere in the JSON tree
    events = []

    def walk_for_events(obj, path=""):
        if isinstance(obj, dict):
            # treat any dict that has both a 'slug' and 'eventMetadata' as a candidate
            if "slug" in obj and ("eventMetadata" in obj or "priceToBeat" in obj):
                events.append((path, obj))
            for k, v in obj.items():
                walk_for_events(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk_for_events(v, f"{path}[{i}]")

    walk_for_events(data)
    print(f"  Found {len(events)} event-shaped objects in __NEXT_DATA__")

    # 1. exact slug match
    matched = [e for e in events if e[1].get("slug") == target_slug]
    if matched:
        path, ev = matched[0]
        target = _read_price_to_beat(ev)
        if target is not None:
            print(f"  EXACT slug match @ {path}: slug={ev.get('slug')}  target={target}")
            return target, f"slug-match"
        print(f"  EXACT slug match found but no priceToBeat in eventMetadata")

    # 2. fallback: find any event with priceToBeat in BTC range
    print(f"  No exact slug match — listing first 10 events:")
    for path, ev in events[:10]:
        slug = ev.get("slug")
        target = _read_price_to_beat(ev)
        print(f"    slug={slug}  target={target}")
    return None, f"no event matched slug {target_slug!r}"


def _read_price_to_beat(event: dict):
    """Read priceToBeat from an event dict (in eventMetadata or top-level)."""
    meta = event.get("eventMetadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    for d in (meta, event):
        if not isinstance(d, dict):
            continue
        for k in ("priceToBeat", "price_to_beat", "targetPrice", "target_price"):
            v = d.get(k)
            if v is None:
                continue
            try:
                x = float(v)
            except (TypeError, ValueError):
                continue
            if 10000 <= x <= 500000:
                return x
    return None


def extract_target_from_inline_scripts(html: str):
    """Search any inline JSON for priceToBeat-like values."""
    patterns = [
        r'"priceToBeat"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
        r'"price_to_beat"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
        r'"targetPrice"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
        r'"line"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html):
            try:
                v = float(m.group(1))
                if 10000 <= v <= 500000:
                    return v, f"inline JSON pattern: {pat[:30]}..."
            except Exception:
                pass
    return None, "no inline JSON match"


def extract_target_from_visible_html(html: str):
    """Last resort — look for 'Price to Beat $XYZ' in raw HTML."""
    pats = [
        r'Price\s*to\s*Beat[\s\S]{0,200}?\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)',
        r'PRICE\s*TO\s*BEAT[\s\S]{0,200}?\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)',
    ]
    for pat in pats:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            try:
                v = float(m.group(1).replace(",", ""))
                if 10000 <= v <= 500000:
                    return v, f"visible HTML pattern"
            except Exception:
                pass
    return None, "no visible HTML match"


def main():
    print("Paste a Polymarket BTC 5-min URL (or press Enter for default):")
    url = input().strip()
    if not url:
        epoch = (int(time.time()) // 300) * 300
        url = f"https://polymarket.com/event/btc-updown-5m-{epoch}"
        print(f"Using default: {url}")

    # extract slug from URL for filtering
    m = re.search(r"/event/([^/?#]+)", url)
    target_slug = m.group(1) if m else url
    print(f"Target slug to match: {target_slug}")

    print(f"\n[1/4] Fetching page via HTTP (no browser)...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return
    html = r.text
    print(f"  HTTP {r.status_code}, {len(html):,} bytes")

    # Save dump for inspection
    dump = "no_browser_dump.html"
    with open(dump, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved raw HTML to: {dump}")

    print(f"\n[2/4] Trying __NEXT_DATA__ JSON extraction...")
    target, src = extract_target_from_next_data(html, target_slug)
    if target is not None:
        print(f"\n  SUCCESS via __NEXT_DATA__: target = ${target:,.2f}  (source: {src})")
        print(f"  >>> RECOMMENDATION: rewrite bot to use this method instead of Playwright.")
        return
    print(f"  Result: {src}")

    print(f"\n[3/4] Trying inline JSON regex...")
    target, src = extract_target_from_inline_scripts(html)
    if target is not None:
        print(f"\n  SUCCESS via inline JSON: target = ${target:,.2f}  (source: {src})")
        print(f"  >>> RECOMMENDATION: rewrite bot to use this method.")
        return
    print(f"  Result: {src}")

    print(f"\n[4/4] Trying visible HTML text...")
    target, src = extract_target_from_visible_html(html)
    if target is not None:
        print(f"\n  SUCCESS via visible HTML: target = ${target:,.2f}  (source: {src})")
        return
    print(f"  Result: {src}")

    print(f"\n>>> RESULT: HTTP-only extraction did NOT find the target.")
    print(f"  This means Polymarket renders the target ONLY via client-side JS,")
    print(f"  and there's no way around using a real browser (Playwright).")
    print(f"  Open {dump} and search for '76' or 'Beat' to verify.")


if __name__ == "__main__":
    main()
