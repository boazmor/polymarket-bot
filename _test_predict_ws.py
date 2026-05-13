"""Standalone test of predict_ws."""
import asyncio
import csv
import sys

sys.path.insert(0, "/root")
from ws_feeds.state import STATE
from ws_feeds.predict_ws import predict_ws_main


with open("/root/data_predict_btc_15m/markets.csv") as f:
    rows = list(csv.reader(f))
mid = int(rows[-1][1])
print(f"testing predict market_id={mid}")


async def test():
    task = asyncio.create_task(predict_ws_main(lambda: mid, STATE))
    for i in range(12):
        await asyncio.sleep(1)
        snap = STATE.snapshot()
        p = snap["predict"]
        print(f"  t={i}s  bid={p['best_bid']:.4f}  ask={p['best_ask']:.4f}  "
              f"ask_depth=${p['ask_depth_usd']:.2f}  age={p['age_ms']}ms  "
              f"conn={p['connected']}  errs={p['error_count']}")
    task.cancel()


asyncio.run(test())
