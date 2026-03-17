from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple

from core.logger import logger


@dataclass
class ExecutionTask:
    task_id: str
    symbol: str
    action: str
    intent: str
    side: str
    state: str
    created_at: datetime
    stage_started_at: datetime
    stage_index: int
    max_total_seconds: int
    stage_wait_seconds: int
    target_size: float
    remaining_size: float
    filled_size: float = 0.0
    filled_value: float = 0.0
    base_price: float = 0.0
    entry_range: Optional[list] = None
    original_prices: list = field(default_factory=list)
    active_orders: Dict[str, Dict] = field(default_factory=dict)
    defer_until: Optional[datetime] = None
    slippage_limit: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    exit_reason: str = ""
    last_slippage: float = 0.0
    last_price: float = 0.0
    last_update: datetime = field(default_factory=datetime.now)

    def elapsed_total(self) -> float:
        return (datetime.now() - self.created_at).total_seconds()

    def elapsed_stage(self) -> float:
        return (datetime.now() - self.stage_started_at).total_seconds()


class ExecutionTracker:
    def __init__(self) -> None:
        self._tasks: Dict[str, ExecutionTask] = {}

    def get(self, symbol: str) -> Optional[ExecutionTask]:
        return self._tasks.get(symbol)

    def set(self, task: ExecutionTask) -> None:
        self._tasks[task.symbol] = task

    def remove(self, symbol: str) -> None:
        if symbol in self._tasks:
            del self._tasks[symbol]

    def has_active(self, symbol: str) -> bool:
        return symbol in self._tasks

    def refresh_task(self, task: ExecutionTask, binance_client=None, order_executor=None) -> Tuple[float, float]:
        """Refresh order status and update filled size/value. Returns (filled_delta, value_delta)."""
        filled_delta = 0.0
        value_delta = 0.0

        for order_id in list(task.active_orders.keys()):
            meta = task.active_orders.get(order_id, {})
            detail = {}
            if binance_client:
                try:
                    detail = binance_client.get_order_by_id(task.symbol, order_id) or {}
                except Exception as exc:
                    logger.warning("ORDER_STATUS_FETCH_FAILED: %s order=%s err=%s", task.symbol, order_id, exc)
                    detail = {}

            if not detail and order_executor:
                try:
                    open_orders = order_executor.get_existing_orders(task.symbol)
                    open_ids = {str(o.get("order_id")) for o in open_orders}
                    if str(order_id) not in open_ids:
                        detail = {"status": "UNKNOWN"}
                except Exception:
                    detail = {}

            if not detail:
                continue

            status = str(detail.get("status", meta.get("status", ""))).upper()
            executed = float(detail.get("executedQty", meta.get("filled_qty", 0.0)) or 0.0)
            avg_price = float(detail.get("avgPrice", detail.get("price", meta.get("price", 0.0))) or 0.0)
            prev_filled = float(meta.get("filled_qty", 0.0) or 0.0)

            delta = max(0.0, executed - prev_filled)
            if delta > 0:
                filled_delta += delta
                value_delta += delta * (avg_price or float(meta.get("price", 0.0) or 0.0))

            meta["filled_qty"] = executed
            meta["avg_price"] = avg_price or float(meta.get("price", 0.0) or 0.0)
            meta["status"] = status
            task.active_orders[order_id] = meta

            if status in ["FILLED", "CANCELED", "REJECTED", "EXPIRED", "UNKNOWN"]:
                task.active_orders.pop(order_id, None)

        if filled_delta > 0:
            task.filled_size += filled_delta
            task.filled_value += value_delta

        task.remaining_size = max(0.0, float(task.target_size) - float(task.filled_size))
        task.last_update = datetime.now()
        return filled_delta, value_delta
