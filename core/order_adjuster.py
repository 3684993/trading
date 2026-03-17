from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from config.settings import settings
from core.logger import logger


class OrderAdjuster:
    """Dynamic order adjuster running in intra-cycle loop."""

    def __init__(self, order_manager, order_executor) -> None:
        self.order_manager = order_manager
        self.order_executor = order_executor
        self.max_orders = int(settings.PARAMS.get("max_orders", 4))
        self.price_gap = float(settings.PARAMS.get("price_gap", 100))
        self.distance_cancel = float(settings.PARAMS.get("distance_cancel", 300))
        self.distance_max = float(settings.PARAMS.get("distance_max", 400))
        self.adjust_timeout = int(settings.PARAMS.get("order_adjust_timeout", 120))
        self.trend_strength_threshold = float(settings.PARAMS.get("trend_strength_threshold", 0.7))

    def adjust_orders(
        self,
        symbol: str,
        trend_direction: str = "neutral",
        trend_strength: float = 0.0,
        pullback_detected: bool = False,
        trend_changed: bool = False,
    ) -> Dict:
        actions: List[Dict] = []
        orders = self.order_executor.get_existing_orders(symbol) or []
        current_price = float(self.order_executor.get_current_price(symbol) or 0.0)

        if trend_changed and orders:
            ids = [o.get("order_id") for o in orders if o.get("order_id") is not None]
            self.order_executor.cancel_orders(symbol, ids)
            logger.warning("ORDER_CANCEL_TIMEOUT: %s trend changed -> cancel_all count=%d", symbol, len(ids))
            return {"success": True, "actions": [{"type": "cancel_all", "count": len(ids)}]}

        now = datetime.now()
        for order in orders:
            oid = order.get("order_id")
            order_price = float(order.get("price", 0) or 0)
            order_side = str(order.get("side", "BUY")).upper()
            order_size = float(order.get("quantity", order.get("size", 0)) or 0)
            if not oid or order_price <= 0 or current_price <= 0 or order_size <= 0:
                continue

            distance = abs(order_price - current_price)
            order_time = self._parse_time(order.get("time") or order.get("timestamp"))
            age = (now - order_time).total_seconds() if order_time else 0

            should_replace = False
            reason = ""

            if distance > self.distance_cancel:
                should_replace = True
                reason = "distance"
                logger.warning("ORDER_CANCEL_DISTANCE: %s order=%s distance=%.2f", symbol, oid, distance)
            elif age > self.adjust_timeout:
                should_replace = True
                reason = "timeout"
                logger.warning("ORDER_CANCEL_TIMEOUT: %s order=%s age=%.0fs", symbol, oid, age)

            if should_replace:
                self.order_executor.cancel_orders(symbol, [oid])
                new_price = self._replacement_price(current_price, order_side)
                if abs(new_price - current_price) <= self.distance_max:
                    result = self.order_executor.open_position(
                        symbol=symbol,
                        side="long" if order_side == "BUY" else "short",
                        size=order_size,
                        order_type="limit",
                        price=new_price,
                        reduce_only=False,
                    )
                    logger.info(
                        "ORDER_REPLACE: %s old=%s reason=%s new_price=%.2f success=%s",
                        symbol,
                        oid,
                        reason,
                        new_price,
                        result.get("success", False),
                    )
                    actions.append({"type": "replace", "order_id": oid, "reason": reason, "new_price": new_price})
                continue

            if float(trend_strength or 0) > self.trend_strength_threshold:
                adjusted = self._trend_accelerate_price(order_price, current_price, order_side)
                if adjusted != order_price and abs(adjusted - current_price) <= self.distance_max:
                    self.order_executor.cancel_orders(symbol, [oid])
                    result = self.order_executor.open_position(
                        symbol=symbol,
                        side="long" if order_side == "BUY" else "short",
                        size=order_size,
                        order_type="limit",
                        price=adjusted,
                        reduce_only=False,
                    )
                    logger.info(
                        "ORDER_ADJUST: %s order=%s old=%.2f new=%.2f success=%s",
                        symbol,
                        oid,
                        order_price,
                        adjusted,
                        result.get("success", False),
                    )
                    actions.append({"type": "adjust", "order_id": oid, "old": order_price, "new": adjusted})

        if pullback_detected:
            open_orders = self.order_executor.get_existing_orders(symbol) or []
            if len(open_orders) < self.max_orders and self.order_manager.can_create_orders(symbol):
                side = "long" if str(trend_direction).lower() in ["long", "bullish", "trend_up"] else "short"
                add_price = self._replacement_price(current_price, "BUY" if side == "long" else "SELL")
                if abs(add_price - current_price) <= self.distance_max:
                    result = self.order_executor.open_position(
                        symbol=symbol,
                        side=side,
                        size=float(settings.PARAMS.get("min_trade_size", 0.005)),
                        order_type="limit",
                        price=add_price,
                        reduce_only=False,
                    )
                    logger.info("ORDER_ADD_PULLBACK: %s side=%s price=%.2f success=%s", symbol, side, add_price, result.get("success", False))
                    actions.append({"type": "add_pullback", "price": add_price, "success": result.get("success", False)})

        return {"success": True, "actions": actions}

    def _replacement_price(self, current_price: float, order_side: str) -> float:
        if order_side == "BUY":
            return max(0.0, current_price - self.price_gap)
        return current_price + self.price_gap

    def _trend_accelerate_price(self, old_price: float, current_price: float, order_side: str) -> float:
        move = min(80.0, max(20.0, abs(current_price - old_price) * 0.5))
        if order_side == "BUY":
            return min(current_price, old_price + move)
        return max(current_price, old_price - move)

    @staticmethod
    def _parse_time(ts):
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts)
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return None
        return None
