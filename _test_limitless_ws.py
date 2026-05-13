"""Standalone test of limitless_ws."""
import asyncio
import csv
import sys

sys.path.insert(0, "/root")
from ws_feeds.state import STATE
from ws_feeds.limitless_ws import limitless_ws_main


with open("/root/data_limitless_btc_15m/markets.csv") as f:
    rows = list(csv.reader(f))
slug = rows[-1][2]
print(f"testing limitless slug={slug}")


async def test():
    task = asyncio.create_task(limitless_ws_main(lambda: slug, STATE))
    for i in range(14):
        await asyncio.sleep(1)
        snap = STATE.snapshot()
        l = snap["lim"]
        print(f"  t={i}s  bid={l['best_bid']:.4f}  ask={l['best_ask']:.4f}  "
              f"ask_depth=${l['ask_depth_usd']:.2f}  age={l['age_ms']}ms  "
              f"conn={l['connected']}  errs={l['error_count']}")
    task.cancel()


asyncio.run(test())
