from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from core.logger import logger


class SlippageEstimator:
    def __init__(self, binance_client, max_stale_seconds: float = 2.0) -> None:
        self.binance_client = binance_client
        self.max_stale_seconds = float(max_stale_seconds)

    def get_orderbook_snapshot(self, symbol: str, limit: int = 50) -> Dict:
        ts = time.time()
        data = None
        if not self.binance_client:
            return {"bids": [], "asks": [], "ts": ts}

        try:
            client = self.binance_client.client
            if hasattr(client, "depth"):
                data = client.depth(symbol=symbol, limit=limit)
            elif hasattr(client, "order_book"):
                data = client.order_book(symbol=symbol, limit=limit)
        except Exception as exc:
            logger.warning("ORDERBOOK_FETCH_FAILED: %s err=%s", symbol, exc)
            data = None

        bids = []
        asks = []
        if data:
            bids = data.get("bids", []) or []
            asks = data.get("asks", []) or []

        def _parse(levels: List) -> List[Tuple[float, float]]:
            parsed = []
            for lvl in levels:
                try:
                    price = float(lvl[0])
                    qty = float(lvl[1])
                    parsed.append((price, qty))
                except Exception:
                    continue
            return parsed

        bids_parsed = _parse(bids)
        asks_parsed = _parse(asks)

        best_bid = bids_parsed[0][0] if bids_parsed else 0.0
        best_ask = asks_parsed[0][0] if asks_parsed else 0.0

        return {
            "bids": bids_parsed,
            "asks": asks_parsed,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "ts": ts,
        }

    def estimate(self, symbol: str, side: str, size: float, current_price: float, indicators: Optional[Dict] = None) -> Dict:
        side = str(side or "").upper()
        size = float(size or 0.0)
        current_price = float(current_price or 0.0)
        indicators = indicators or {}

        snapshot = self.get_orderbook_snapshot(symbol)
        now = time.time()
        best_bid = float(snapshot.get("best_bid", 0.0) or 0.0)
        best_ask = float(snapshot.get("best_ask", 0.0) or 0.0)
        age = now - float(snapshot.get("ts", now))

        def depth_scan(levels: List[Tuple[float, float]], target: float) -> Optional[float]:
            total_qty = 0.0
            total_value = 0.0
            for price, qty in levels:
                take = min(target - total_qty, qty)
                total_value += take * price
                total_qty += take
                if total_qty >= target:
                    break
            if total_qty < target * 0.9:
                return None
            return total_value / total_qty if total_qty > 0 else None

        use_orderbook = bool(best_bid > 0 and best_ask > 0 and age <= self.max_stale_seconds)
        if use_orderbook and size > 0:
            if side == "BUY":
                avg_price = depth_scan(snapshot.get("asks", []), size)
                if avg_price is None:
                    use_orderbook = False
                else:
                    slippage = abs(avg_price - current_price) / current_price if current_price > 0 else 0.0
                    return {
                        "method": "orderbook_depth",
                        "estimated_price": avg_price,
                        "slippage": slippage,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                    }
            elif side == "SELL":
                avg_price = depth_scan(snapshot.get("bids", []), size)
                if avg_price is None:
                    use_orderbook = False
                else:
                    slippage = abs(avg_price - current_price) / current_price if current_price > 0 else 0.0
                    return {
                        "method": "orderbook_depth",
                        "estimated_price": avg_price,
                        "slippage": slippage,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                    }
            else:
                use_orderbook = False

        atr = float(indicators.get("atr", current_price * 0.01) or (current_price * 0.01 if current_price > 0 else 0.0))
        mid = current_price
        if best_bid > 0 and best_ask > 0:
            mid = (best_bid + best_ask) / 2.0

        if side == "BUY":
            estimated = mid + atr * 0.1
        elif side == "SELL":
            estimated = mid - atr * 0.1
        else:
            estimated = mid

        slippage = abs(estimated - current_price) / current_price if current_price > 0 else 0.0
        return {
            "method": "mid_atr_fallback",
            "estimated_price": estimated,
            "slippage": slippage,
            "best_bid": best_bid,
            "best_ask": best_ask,
        }
