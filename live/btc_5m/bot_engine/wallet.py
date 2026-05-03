# -*- coding: utf-8 -*-
"""
wallet.py — Polymarket CLOB wallet wrapper.

Designed to be SHARED across all coin strategies. The multi-coin master
spawns ONE Wallet and passes it to every strategy instance. All 7 coins
trade through the same Polymarket Safe, so the same private key signs every
order.

Loads .env, creates ClobClient with signature_type=2 (Gnosis Safe proxy),
derives API credentials, and exposes order placement / cancellation / balance.

In dry-run mode the wallet is inert: place_buy returns a fake order_id and
balance returns None — strategy.py routes to its V3 simulation path instead.
"""
import os
import time
from typing import List, Optional, Tuple

from bot_config import (
    CLOB_HOST,
    POLYGON_CHAIN_ID,
    SAFE_ADDRESS as DEFAULT_SAFE_ADDRESS,
    SIGNATURE_TYPE_POLY_GNOSIS_SAFE as DEFAULT_SIGNATURE_TYPE,
)


class Wallet:
    """Polymarket CLOB wallet. One instance shared across all strategies."""

    SIGNATURE_TYPE_POLY_GNOSIS_SAFE = DEFAULT_SIGNATURE_TYPE
    SAFE_ADDRESS = DEFAULT_SAFE_ADDRESS

    def __init__(self, dry_run: bool, env_paths: Optional[List[str]] = None) -> None:
        self.dry_run = dry_run
        self.env_paths = env_paths or [
            "/root/.env",
            os.path.join(os.path.dirname(__file__), "..", ".env"),
        ]
        self.private_key: Optional[str] = None
        self.address: Optional[str] = None
        self.rpc_url: Optional[str] = None
        self.client = None
        self.connected: bool = False
        self.last_error: str = ""

    def _load_env(self) -> bool:
        try:
            from dotenv import load_dotenv
        except Exception:
            self.last_error = "python-dotenv not installed"
            return False
        loaded_any = False
        for p in self.env_paths:
            if os.path.exists(p):
                load_dotenv(p, override=True)
                loaded_any = True
        if not loaded_any:
            self.last_error = "no .env file found in known locations"
            return False
        self.private_key = (
            os.environ.get("PRIVATE_KEY")
            or os.environ.get("MY_PRIVATE_KEY")
            or os.environ.get("WALLET_PRIVATE_KEY")
        )
        self.address = os.environ.get("WALLET_ADDRESS") or os.environ.get("MY_ADDRESS")
        self.rpc_url = os.environ.get("POLYGON_RPC_URL")
        if not self.private_key or len(self.private_key) < 60:
            self.last_error = "private key missing or invalid in .env"
            return False
        return True

    def connect(self) -> bool:
        """Initialize ClobClient + derive API credentials. Returns True on success."""
        if self.dry_run:
            return True
        if not self._load_env():
            return False
        try:
            from py_clob_client_v2.client import ClobClient
        except Exception as e:
            self.last_error = f"py-clob-client-v2 not installed: {e}"
            return False
        try:
            self.client = ClobClient(
                host=CLOB_HOST,
                key=self.private_key,
                chain_id=POLYGON_CHAIN_ID,
                signature_type=self.SIGNATURE_TYPE_POLY_GNOSIS_SAFE,
                funder=self.SAFE_ADDRESS,
            )
            creds = self.client.create_or_derive_api_key()
            self.client.set_api_creds(creds)
            self.connected = True
            return True
        except Exception as e:
            self.last_error = f"CLOB connect failed: {type(e).__name__}: {e}"
            self.connected = False
            return False

    def get_usdc_balance(self) -> Optional[float]:
        if self.dry_run or not self.connected or self.client is None:
            return None
        try:
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self.SIGNATURE_TYPE_POLY_GNOSIS_SAFE,
            )
            resp = self.client.get_balance_allowance(params)
            if isinstance(resp, dict):
                bal_raw = resp.get("balance")
                if bal_raw is not None:
                    return float(int(bal_raw) / 1_000_000.0)
            return None
        except Exception as e:
            self.last_error = f"balance query failed: {type(e).__name__}: {e}"
            return None

    def place_buy(self, token_id: str, price: float, size_shares: float) -> Tuple[Optional[str], str]:
        """Place a GTC limit BUY on the CLOB.
        Returns (order_id, status). status: 'placed' / 'rejected:<reason>' /
        'error:<reason>' / 'dry_run' / 'not_connected'.
        """
        if self.dry_run:
            return ("DRYRUN-" + str(int(time.time() * 1000)), "dry_run")
        if not self.connected or self.client is None:
            return (None, "not_connected")
        try:
            from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
            args = OrderArgsV2(
                price=round(float(price), 4),
                size=round(float(size_shares), 4),
                side="BUY",
                token_id=str(token_id),
            )
            resp = self.client.create_and_post_order(args, order_type=OrderType.GTC)
            if isinstance(resp, dict):
                if resp.get("success") or resp.get("orderID") or resp.get("orderId"):
                    oid = str(resp.get("orderID") or resp.get("orderId") or "?")
                    return (oid, "placed")
                err = resp.get("errorMsg") or resp.get("error") or str(resp)
                return (None, f"rejected:{err}")
            return (None, "rejected:unexpected_response")
        except Exception as e:
            self.last_error = f"place_buy failed: {type(e).__name__}: {e}"
            return (None, f"error:{type(e).__name__}")

    def cancel(self, order_id: str) -> bool:
        if self.dry_run or not self.connected or self.client is None:
            return True
        try:
            r = self.client.cancel_orders([str(order_id)])
            if isinstance(r, dict):
                canceled = r.get("canceled", []) or []
                if str(order_id) in canceled:
                    return True
                self.last_error = f"cancel failed: {r}"
                return False
            return True
        except Exception as e:
            self.last_error = f"cancel failed: {type(e).__name__}: {e}"
            return False

    def fetch_open_orders(self) -> list:
        if self.dry_run or not self.connected or self.client is None:
            return []
        try:
            r = self.client.get_open_orders()
            if isinstance(r, dict):
                return r.get("data", []) or []
            return r or []
        except Exception as e:
            self.last_error = f"fetch_orders failed: {type(e).__name__}: {e}"
            return []
