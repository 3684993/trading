"""ExecutionPlanner - 负责将 AI 目标仓位拆分为可执行订单。"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional

from core.logger import logger
from config.settings import settings


class ExecutionPlanner:
    """交易执行规划器。

    规则：
    - min_trade_size = 0.005
    - max_trade_size = 0.02
    - max_pending_orders = 4
    """

    def __init__(
        self,
        min_trade_size: float = settings.PARAMS.get("min_trade_size", 0.005),
        max_trade_size: float = settings.PARAMS.get("max_trade_size", 0.02),
        max_pending_orders: int = settings.PARAMS.get("max_orders", 4),
        order_timeout_seconds: int = settings.PARAMS.get("order_timeout", 180),
        price_step: float = settings.PARAMS.get("price_gap", 100.0),
    ) -> None:
        self.min_trade_size = float(min_trade_size)
        self.max_trade_size = float(max_trade_size)
        self.max_position = float(max_trade_size)
        self.max_pending_orders = int(max_pending_orders)
        self.order_timeout_seconds = int(order_timeout_seconds)
        self.price_step = float(price_step)
        logger.info("ExecutionPlanner initialized")

    def calculate_position_progress(
        self,
        symbol: str,
        target_size: float,
        filled_size: float,
        pending_orders: List[Dict],
    ) -> Dict:
        pending_size = sum(float(order.get("size", 0.0)) for order in pending_orders)
        remaining_size = max(0.0, float(target_size) - float(filled_size))
        progress_pct = (float(filled_size) / float(target_size) * 100.0) if target_size > 0 else 0.0

        return {
            "symbol": symbol,
            "target_size": float(target_size),
            "filled_size": float(filled_size),
            "pending_size": pending_size,
            "remaining_size": remaining_size,
            "progress_pct": progress_pct,
            "target_reached": float(filled_size) >= float(target_size) and target_size > 0,
            "has_pending": pending_size > 0,
            "can_trade": remaining_size >= self.min_trade_size,
        }

    def generate_split_orders(
        self,
        symbol: str,
        side: str,
        target_size: float,
        base_price: float,
        remaining_size: float,
        current_price: Optional[float] = None,
    ) -> List[Dict]:
        """拆单：优先按 min_trade_size 拆分，最多 4 单，且每单不超过 max_trade_size。"""
        qty = max(0.0, min(float(remaining_size), self.max_position))
        if qty < self.min_trade_size:
            return []

        preferred_count = math.ceil(qty / self.min_trade_size)
        order_count = max(1, min(self.max_pending_orders, preferred_count))
        per_order = qty / order_count

        if per_order > self.max_trade_size:
            order_count = math.ceil(qty / self.max_trade_size)
            if order_count > self.max_pending_orders:
                logger.warning(
                    "ORDER_SPLIT_FAILED: %s remaining %.6f cannot satisfy max orders=%d and max_trade_size=%.6f",
                    symbol,
                    qty,
                    self.max_pending_orders,
                    self.max_trade_size,
                )
                return []
            per_order = qty / order_count

        # 示例：0.02 -> 4 个 0.005
        sizes = [round(per_order, 6)] * order_count
        sizes[-1] = round(qty - sum(sizes[:-1]), 6)

        price_levels = self._generate_price_levels(side=side, base_price=float(base_price), count=order_count)
        orders: List[Dict] = []
        logger.info(f"ORDER_SPLIT_EXECUTED: {symbol} qty={qty:.4f} orders={order_count} step={self.price_step}")
        for idx, (size, price) in enumerate(zip(sizes, price_levels), start=1):
            if not self.validate_order_size(size):
                return []
            if current_price is not None:
                distance_max = float(settings.PARAMS.get("distance_max", 400))
                if abs(float(price) - float(current_price)) > distance_max:
                    logger.warning("ORDER_BLOCKED_MAX_DISTANCE: %s price=%.2f current=%.2f", symbol, float(price), float(current_price))
                    continue
            orders.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "price": price,
                    "order_type": "limit",
                    "reduce_only": False,
                    "level": idx,
                    "total_levels": order_count,
                }
            )
        return orders

    def _generate_price_levels(self, side: str, base_price: float, count: int) -> List[float]:
        levels: List[float] = []
        side = side.lower()
        for i in range(count):
            delta = self.price_step * i
            if side == "long":
                levels.append(base_price - delta)
            else:
                levels.append(base_price + delta)
        return levels

    def validate_order_size(self, size: float) -> bool:
        size = float(size)
        return self.min_trade_size <= size <= self.max_trade_size

    def check_hold_time_constraint(self, entry_time: Optional[datetime], current_time: datetime) -> bool:
        if not entry_time:
            return True
        return (current_time - entry_time).total_seconds() >= 120

    def cancel_stale_orders(self, pending_orders: List[Dict], current_time: datetime) -> List[Dict]:
        stale: List[Dict] = []
        for order in pending_orders:
            ts = order.get("timestamp") or order.get("time")
            if isinstance(ts, (int, float)):
                order_time = datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts)
            elif isinstance(ts, str):
                try:
                    order_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    continue
            elif isinstance(ts, datetime):
                order_time = ts
            else:
                continue

            if (current_time - order_time).total_seconds() > self.order_timeout_seconds:
                stale.append(order)
        return stale

    def get_execution_summary(self, progress_info: Dict) -> str:
        return (
            f"{progress_info['symbol']} 执行进度: "
            f"{progress_info['filled_size']:.4f}/{progress_info['target_size']:.4f}, "
            f"挂单 {progress_info['pending_size']:.4f}, "
            f"进度 {progress_info['progress_pct']:.1f}%"
        )
