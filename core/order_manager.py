from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from core.logger import logger
from config.settings import settings


class OrderManager:
    def __init__(self, order_executor, market_analyzer=None):
        self.order_executor = order_executor
        self.max_orders = int(settings.PARAMS.get("max_orders", 4))
        self.price_gap = float(settings.PARAMS.get("price_gap", 100))
        self.order_timeout = 600
        self.distance_cancel = float(settings.PARAMS.get("distance_cancel", 300.0))
        self.distance_max = float(settings.PARAMS.get("distance_max", 400.0))
        self.market_analyzer = market_analyzer
        self.last_trend_by_symbol: Dict[str, str] = {}
        self.cancel_history: Dict[str, Dict] = {}
        self.cancel_cooldown_seconds = 300

    def inspect_all_orders(self, symbol: str, intended_side: Optional[str] = None) -> Dict:
        orders = self.order_executor.get_existing_orders(symbol) or []
        managed_orders = self._build_managed_orders(symbol, orders)

        if len(managed_orders) > self.max_orders:
            logger.debug("ORDER_COUNT_EXCEED: %s existing_orders=%d > %d", symbol, len(managed_orders), self.max_orders)

        valid_prices = sorted([o["price"] for o in managed_orders if o["price"] > 0])
        min_gap = min([abs(valid_prices[i + 1] - valid_prices[i]) for i in range(len(valid_prices) - 1)]) if len(valid_prices) > 1 else 0.0

        too_far_ids: List[str] = []
        low_prob_ids: List[str] = []
        wrong_dir_ids: List[str] = []
        recommendations: List[str] = []

        for o in managed_orders:
            if intended_side and str(o.get("side", "")).upper() != intended_side.upper():
                wrong_dir_ids.append(o["order_id"])

            if o["price"] <= 0 or o["distance"] is None:
                continue

            logger.debug(
                "ORDER_DISTANCE_CHECK: %s order=%s price=%.2f current=%.2f distance=%.2f",
                symbol,
                o["order_id"],
                o["price"],
                o["current_price"],
                o["distance"],
            )

            if o["distance"] > self.distance_cancel:
                too_far_ids.append(o["order_id"])
                recommendations.append(f"order={o['order_id']} 距离过远({o['distance']:.1f})")

            prob = self._estimate_fill_probability(o["price"], o["current_price"], str(o.get("side", "BUY")))
            if prob < 0.3:
                low_prob_ids.append(o["order_id"])

        stale_ids = self._find_stale_orders(managed_orders)
        trend_changed = self._detect_trend_change(symbol)
        if trend_changed:
            recommendations.append("趋势方向变化，建议撤销未成交订单")

        return {
            "success": True,
            "symbol": symbol,
            "total_orders": len(managed_orders),
            "managed_orders": managed_orders,
            "min_gap": min_gap,
            "gap_ok": min_gap >= self.price_gap if len(valid_prices) > 1 else True,
            "wrong_direction_order_ids": wrong_dir_ids,
            "too_far_order_ids": too_far_ids,
            "low_probability_order_ids": low_prob_ids,
            "stale_order_ids": stale_ids,
            "trend_changed": trend_changed,
            "recommendations": recommendations,
        }

    def can_create_orders(self, symbol: str) -> bool:
        info = self.cancel_history.get(symbol)
        if not info:
            return True

        elapsed = (datetime.now() - info["cancel_time"]).total_seconds()
        if elapsed < self.cancel_cooldown_seconds:
            logger.debug(
                "ORDER_CANCEL_REASON: %s cooldown_active reason=%s elapsed=%.0fs<%ds",
                symbol,
                info["reason"],
                elapsed,
                self.cancel_cooldown_seconds,
            )
            return False
        return True

    def auto_cleanup_orders(self, symbol: str, report: Optional[Dict] = None) -> Dict:
        report = report or self.inspect_all_orders(symbol)

        cancel_with_reason: List[tuple] = []
        for oid in report.get("too_far_order_ids", []):
            cancel_with_reason.append((oid, "DISTANCE"))
        for oid in report.get("stale_order_ids", []):
            cancel_with_reason.append((oid, "TIMEOUT"))
        if report.get("trend_changed"):
            for mo in report.get("managed_orders", []):
                cancel_with_reason.append((mo["order_id"], "TREND_CHANGED"))

        unique: Dict[str, str] = {}
        for oid, reason in cancel_with_reason:
            unique[str(oid)] = reason

        cancel_ids = list(unique.keys())
        cleanup_actions: List[str] = []
        if cancel_ids:
            self.order_executor.cancel_orders(symbol, cancel_ids)
            last_reason = list(unique.values())[-1]
            self.cancel_history[symbol] = {"reason": last_reason, "cancel_time": datetime.now()}

            for oid in cancel_ids:
                reason = unique[oid]
                if reason == "DISTANCE":
                    logger.info("[ORDER_CANCEL] symbol=%s reason=DISTANCE order=%s", symbol, oid)
                elif reason == "TIMEOUT":
                    logger.info("[ORDER_CANCEL] symbol=%s reason=TIMEOUT order=%s", symbol, oid)
                cleanup_actions.append(f"cancel {oid} ({reason})")

        return {"success": True, "cancelled": cancel_ids, "cleanup_actions": cleanup_actions}

    def _build_managed_orders(self, symbol: str, orders: List[Dict]) -> List[Dict]:
        current_price = float(self.order_executor.get_current_price(symbol) or 0.0)
        managed_orders: List[Dict] = []
        for o in orders:
            try:
                price = float(o.get("price", 0.0) or 0.0)
            except Exception:
                price = 0.0
            timestamp = o.get("time") or o.get("timestamp")
            order_id = str(o.get("order_id", "unknown"))
            size = float(o.get("size", o.get("quantity", 0.0)) or 0.0)
            distance = abs(price - current_price) if price > 0 and current_price > 0 else None
            managed_orders.append(
                {
                    "order_id": order_id,
                    "price": price,
                    "size": size,
                    "timestamp": timestamp,
                    "distance": distance,
                    "symbol": symbol,
                    "side": str(o.get("side", "")),
                    "current_price": current_price,
                }
            )
        return managed_orders

    def _find_stale_orders(self, orders: List[Dict]) -> List[str]:
        now = datetime.now()
        stale = []
        for o in orders:
            ts = o.get("timestamp")
            t = None
            if isinstance(ts, (int, float)):
                t = datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts)
            elif isinstance(ts, str):
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    pass
            elif isinstance(ts, datetime):
                t = ts
            if t and (now - t).total_seconds() > self.order_timeout:
                stale.append(o.get("order_id", "unknown"))
        return stale

    def _estimate_fill_probability(self, order_price: float, current_price: float, side: str) -> float:
        if current_price <= 0:
            return 0.0
        diff = abs(order_price - current_price) / current_price
        base = max(0.0, 1.0 - diff * 20)
        return min(1.0, base)

    def _detect_trend_change(self, symbol: str) -> bool:
        if not self.market_analyzer:
            return False
        try:
            summary = self.market_analyzer.get_market_summary(symbol)
            trend = str(summary.get("trend", "neutral"))
            prev = self.last_trend_by_symbol.get(symbol)
            self.last_trend_by_symbol[symbol] = trend
            return prev is not None and prev != trend
        except Exception:
            return False
