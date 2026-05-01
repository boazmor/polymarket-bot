# -*- coding: utf-8 -*-
"""smoke_test.py — minimal import + class-construction sanity check.
Does NOT make any network calls or place orders. Just verifies the new code
path in LIVE_BTC_5M_V1 doesn't blow up on basic instantiation."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load LIVE_BTC_5M_V1 as a module without running its main()
import importlib.util
spec = importlib.util.spec_from_file_location("live_bot", "LIVE_BTC_5M_V1.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("[1] Module loaded OK")
print(f"    constants: MAX_BUY_USD={mod.MAX_BUY_USD}, MAX_DAILY_LOSS_USD={mod.MAX_DAILY_LOSS_USD}")
print(f"    constants: MAX_WALLET_USD={mod.MAX_WALLET_USD}, BOT40_MAKER_LEVELS={mod.BOT40_MAKER_LEVELS}")
print(f"    constants: BOT120_MIN_SEC={mod.BOT120_MIN_SEC}, MIN_DIST_BOT120={mod.MIN_DIST_BOT120}, BOT120_MAX_PRICE={mod.BOT120_MAX_PRICE}")

# Construct Wallet in dry-run (no network)
print("\n[2] Constructing Wallet (dry-run mode)...")
w = mod.Wallet(dry_run=True)
print(f"    wallet.dry_run={w.dry_run}, connected={w.connected}")
ok = w.connect()  # dry-run connect should be no-op returning True
print(f"    wallet.connect() in dry-run -> {ok}")
oid, status = w.place_buy("dummy_token", 0.30, 10.0)
print(f"    wallet.place_buy() in dry-run -> order_id={oid}, status={status}")

# Construct Wallet in LIVE mode (loads .env if available, doesn't actually connect to network)
print("\n[3] Constructing Wallet (LIVE mode)...")
local_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(local_env):
    print(f"    local .env exists at {local_env}")
    w2 = mod.Wallet(dry_run=False, env_paths=[local_env])
    loaded = w2._load_env()
    print(f"    _load_env() -> {loaded}, last_error={w2.last_error!r}")
    if loaded:
        kp = w2.private_key
        print(f"    private_key loaded: {len(kp)} chars  prefix={kp[:6]}... (NOT printed in full)")
        print(f"    address={w2.address}")
else:
    print(f"    no local .env at {local_env} — skipping live load test")

# Construct the main bot in dry-run
print("\n[4] Constructing the bot (dry-run)...")
binance = mod.BinanceEngine()
logger = mod.DualResearchLogger(data_dir="smoke_test_data")
bot = mod.Polymarket5mDualBot(binance, logger)
bot.dry_run = True
bot.wallet = mod.Wallet(dry_run=True)
print(f"    bot.dry_run={bot.dry_run}")
print(f"    bot.daily_realized_pnl={bot.daily_realized_pnl}")
print(f"    bot.killed_for_daily_loss={bot.killed_for_daily_loss}")

# Check the daily kill switch logic
print("\n[5] Testing daily kill switch...")
bot._update_daily_pnl(-25.0)
print(f"    after -$25 loss: pnl={bot.daily_realized_pnl}, killed={bot.killed_for_daily_loss}")
killed = bot._check_and_update_daily_kill()
print(f"    _check_and_update_daily_kill() -> {killed} (expected False, since dry_run)")

bot.dry_run = False  # simulate LIVE mode for the kill check
killed_live = bot._check_and_update_daily_kill()
print(f"    in LIVE mode at -$25: kill -> {killed_live} (expected False, under cap)")

bot._update_daily_pnl(-20.0)
print(f"    after another -$20 loss: pnl={bot.daily_realized_pnl}")
killed_live2 = bot._check_and_update_daily_kill()
print(f"    in LIVE mode at -$45: kill -> {killed_live2} (expected True, over $40 cap)")

print("\n[6] All smoke tests passed.")

# cleanup
import shutil
if os.path.exists("smoke_test_data"):
    shutil.rmtree("smoke_test_data")
