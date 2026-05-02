# -*- coding: utf-8 -*-
"""smoke_test_v2.py — verify the updated LIVE_BTC_5M_V1_TEST5 bot can connect
to Polymarket V2 CLOB via py-clob-client-v2 and read its balance."""

from LIVE_BTC_5M_V1_TEST5 import Wallet

print("Connecting to Polymarket V2 CLOB...")
w = Wallet(dry_run=False)
ok = w.connect()
print(f"connect = {ok}")
print(f"last_error = {w.last_error!r}")

bal = w.get_usdc_balance()
print(f"balance = {bal}")

if ok and bal is not None:
    print(f"\n>>> ALL GOOD. Wallet connected. Balance ${bal:.2f}.")
    print(">>> Bot is ready to run with --live flag.")
else:
    print("\n>>> FAILED. See error above.")
