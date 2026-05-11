"""Reusable Predict.fun trader module — auth, place limit order, query position.

Usage:
    from predict_trader import PredictTrader
    t = PredictTrader(api_key, private_key)
    order = t.place_limit_buy(market_id, outcome="Up", price=0.55, shares=3.5)
    pos = t.get_positions()
    fills = t.get_orders()
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct
from predict_sdk import (
    ChainId,
    OrderBuilder,
    BuildOrderInput,
    Side,
    SignatureType,
)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
BASE = "https://api.predict.fun/v1"


def _http(method, url, headers=None, body=None, timeout=20):
    data = body.encode() if isinstance(body, str) else body
    h = {"User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


class PredictTrader:
    def __init__(self, api_key: str, private_key: str, log_path: Optional[str] = None):
        self.api_key = api_key
        self.account = Account.from_key(private_key)
        self.address = self.account.address
        self.builder = OrderBuilder.make(
            chain_id=ChainId.BNB_MAINNET,
            signer=private_key,
        )
        self.jwt = None
        self.jwt_exp = 0
        self.log_path = log_path
        self._login()

    def _log(self, event: str, **kw):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **kw,
        }
        print(f"[{entry['ts'][11:19]}] {event}: " +
              json.dumps({k: v for k, v in kw.items() if k != "response"}, default=str)[:240])
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")

    def _login(self):
        h = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        code, body = _http("GET", f"{BASE}/auth/message?address={self.address}", headers=h)
        if code != 200:
            raise RuntimeError(f"auth_message failed: {body}")
        msg = json.loads(body)["data"]["message"]
        signed = self.account.sign_message(encode_defunct(text=msg))
        sig = "0x" + signed.signature.hex().lstrip("0x")
        payload = json.dumps({"signer": self.address, "signature": sig, "message": msg})
        code, body = _http("POST", f"{BASE}/auth", headers=h, body=payload)
        if code != 200:
            raise RuntimeError(f"auth post failed: {body}")
        self.jwt = json.loads(body)["data"]["token"]
        # JWT lasts 24h per earlier observation
        self.jwt_exp = time.time() + 23 * 3600
        self._log("authenticated", jwt_len=len(self.jwt))

    def _hj(self):
        if time.time() > self.jwt_exp:
            self._login()
        return {
            "x-api-key": self.api_key,
            "Authorization": f"Bearer {self.jwt}",
            "Content-Type": "application/json",
        }

    def get_market(self, market_id: int) -> dict:
        h = {"x-api-key": self.api_key}
        code, body = _http("GET", f"{BASE}/markets/{market_id}", headers=h)
        if code != 200:
            raise RuntimeError(f"get_market {market_id} failed: {body}")
        return json.loads(body).get("data", json.loads(body))

    def get_orderbook(self, market_id: int) -> dict:
        h = {"x-api-key": self.api_key}
        code, body = _http("GET", f"{BASE}/markets/{market_id}/orderbook", headers=h)
        if code != 200:
            raise RuntimeError(f"get_orderbook {market_id} failed: {body}")
        return json.loads(body)["data"]

    def get_orders(self, status: Optional[str] = None) -> list:
        url = f"{BASE}/orders" + (f"?status={status}" if status else "")
        code, body = _http("GET", url, headers=self._hj())
        return json.loads(body).get("data", []) if code == 200 else []

    def get_positions(self) -> list:
        code, body = _http("GET", f"{BASE}/positions", headers=self._hj())
        return json.loads(body).get("data", []) if code == 200 else []

    def place_limit(self, market_id: int, outcome_token_id: str, side: str,
                    price: float, shares: float, is_neg_risk: bool = False,
                    is_yield_bearing: bool = False, fee_rate_bps: int = 200) -> dict:
        """Place a limit order. Returns {orderId, orderHash} on success.

        Args:
            market_id: numeric market id from /v1/markets list
            outcome_token_id: onChainId of the outcome we're trading
            side: "BUY" or "SELL"
            price: price per share, 0 < price < 1
            shares: quantity in shares
            is_neg_risk: from market metadata
            is_yield_bearing: from market metadata
            fee_rate_bps: from market metadata
        """
        PRECISION = 10**18
        price_wei = int(round(price * PRECISION))
        qty_wei = int(round(shares * PRECISION))

        if side == "BUY":
            maker_amount = (price_wei * qty_wei) // PRECISION
            taker_amount = qty_wei
            side_enum = Side.BUY
        else:
            maker_amount = qty_wei
            taker_amount = (price_wei * qty_wei) // PRECISION
            side_enum = Side.SELL

        build_input = BuildOrderInput(
            side=side_enum,
            token_id=outcome_token_id,
            maker_amount=maker_amount,
            taker_amount=taker_amount,
            fee_rate_bps=fee_rate_bps,
            signature_type=SignatureType.EOA,
        )

        order = self.builder.build_order("LIMIT", build_input)
        typed_data = self.builder.build_typed_data(
            order,
            is_neg_risk=is_neg_risk,
            is_yield_bearing=is_yield_bearing,
        )
        signed = self.builder.sign_typed_data_order(typed_data)

        d = vars(signed) if hasattr(signed, "__dict__") else dict(signed)
        snake_to_camel = {
            "token_id": "tokenId",
            "maker_amount": "makerAmount",
            "taker_amount": "takerAmount",
            "fee_rate_bps": "feeRateBps",
            "signature_type": "signatureType",
        }
        order_body = {snake_to_camel.get(k, k): v for k, v in d.items()}
        order_body["side"] = int(order_body.get("side", 0))
        order_body["signatureType"] = int(order_body.get("signatureType", 0))
        order_body.pop("hash", None)
        sig = order_body.get("signature", "")
        if sig and not sig.startswith("0x"):
            order_body["signature"] = "0x" + sig

        payload = {
            "data": {
                "order": order_body,
                "pricePerShare": f"{price_wei}",
                "strategy": "LIMIT",
            }
        }
        body = json.dumps(payload, default=str)
        self._log("submit",
                  market_id=market_id, side=side, price=price, shares=shares,
                  maker_amount=str(maker_amount), taker_amount=str(taker_amount))

        code, txt = _http("POST", f"{BASE}/orders", headers=self._hj(), body=body)
        if code not in (200, 201):
            self._log("submit_failed", status=code, response=txt[:400])
            return {"error": txt, "status": code}

        resp = json.loads(txt)["data"]
        self._log("submit_ok",
                  order_id=resp.get("orderId"),
                  order_hash=resp.get("orderHash"),
                  code=resp.get("code"))
        return {
            "orderId": resp.get("orderId"),
            "orderHash": resp.get("orderHash"),
            "code": resp.get("code"),
            "raw": resp,
        }
