"""Quick standalone test of poly_ws on Helsinki."""
import asyncio
import json
import sys
import urllib.request

sys.path.insert(0, "/root")
from ws_feeds.state import STATE
from ws_feeds.poly_ws import poly_ws_main


def fetch_current_market():
    """Find a currently active 15-min BTC market on Polymarket."""
    import time
    epoch = (int(time.time()) // 900) * 900
    slug = f"btc-updown-15m-{epoch}"
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    m = data[0]
    tids = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
    outs = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    return tids[outs.index("Up")], tids[outs.index("Down")], m["slug"]


async def test():
    up_t, down_t, slug = fetch_current_market()
    print(f"testing market: {slug}")
    print(f"up_token: {up_t[:30]}...")
    task = asyncio.create_task(poly_ws_main([up_t], [down_t], STATE))
    for i in range(15):
        await asyncio.sleep(1)
        snap = STATE.snapshot()
        p = snap["poly"]
        print(f"  t={i}s  bid={p['best_bid']:.4f}  ask={p['best_ask']:.4f}  "
              f"ask_depth=${p['ask_depth_usd']:.2f}  age={p['age_ms']}ms  "
              f"conn={p['connected']}  errs={p['error_count']}")
    task.cancel()


asyncio.run(test())
