from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from core.logger import logger
from config.settings import settings
from core.execution_planner import ExecutionPlanner
from execution.execution_tracker import ExecutionTask, ExecutionTracker
from execution.slippage_estimator import SlippageEstimator


ORDER_CREATED = "ORDER_CREATED"
PASSIVE_LIMIT = "PASSIVE_LIMIT"
ADJUST_LIMIT = "ADJUST_LIMIT"
AGGRESSIVE_LIMIT = "AGGRESSIVE_LIMIT"
MARKET_FALLBACK = "MARKET_FALLBACK"
ORDER_FILLED = "ORDER_FILLED"
ORDER_CANCELLED = "ORDER_CANCELLED"
ORDER_TIMEOUT = "ORDER_TIMEOUT"

ENTRY_ACTIONS = {"open_long", "open_short", "add_position"}
EXIT_ACTIONS = {"close_position", "reverse_position"}


class ExecutionEngine:
    def __init__(
        self,
        order_executor,
        binance_client=None,
        position_manager=None,
        execution_planner: Optional[ExecutionPlanner] = None,
        tracker: Optional[ExecutionTracker] = None,
        slippage_estimator: Optional[SlippageEstimator] = None,
        cycle_interval_seconds: int = 5,  # 降频：从 60 秒改为 5 秒
    ) -> None:
        self.order_executor = order_executor
        self.binance_client = binance_client
        self.position_manager = position_manager
        self.tracker = tracker or ExecutionTracker()
        self.slippage_estimator = slippage_estimator or SlippageEstimator(binance_client)
        self.execution_planner = execution_planner or ExecutionPlanner()

        self.entry_max_total_seconds = int(settings.PARAMS.get("entry_max_total_seconds", 180))
        self.entry_stage_wait_seconds = int(settings.PARAMS.get("entry_stage_wait_seconds", 90))
        self.exit_max_total_seconds = int(settings.PARAMS.get("exit_max_total_seconds", 60))
        self.exit_stage_wait_seconds = int(settings.PARAMS.get("exit_stage_wait_seconds", 15))
        self.max_price_adjust = float(settings.PARAMS.get("max_price_adjust", 120.0))
        self.max_orders = int(settings.PARAMS.get("max_orders", 4))
        # Hard constraints per latest requirements
        self.min_trade_size = 0.002
        self.max_trade_size = 0.01
        self.max_position_size = 0.02
        self.slippage_limit = float(settings.PARAMS.get("max_slippage_pct", 0.0015))
        self.stop_loss_slippage_limit = float(settings.PARAMS.get("stop_loss_slippage_pct", 0.003))
        self.defer_seconds = int(cycle_interval_seconds)

        logger.info("ExecutionEngine initialized")

    def has_active_task(self, symbol: str) -> bool:
        return self.tracker.has_active(symbol)

    def execute(
        self,
        decision: Dict,
        symbol: str,
        current_price: float,
        position_state: Optional[Dict] = None,
        indicators: Optional[Dict] = None,
    ) -> Dict:
        position_state = position_state or {}
        indicators = indicators or {}
        action = str(decision.get("action", "hold"))

        task = self.tracker.get(symbol)
        if task:
            if task.state == ORDER_TIMEOUT:
                if task.defer_until and datetime.now() < task.defer_until:
                    return self._advance_task(task, current_price, indicators)
                self.tracker.remove(symbol)
                task = None
            elif task.state not in [ORDER_FILLED, ORDER_CANCELLED]:
                if action != "hold" and action != task.action:
                    logger.info(
                        "[EXECUTION] symbol=%s state=%s reason=task_active action=%s",
                        symbol,
                        task.state,
                        action,
                    )
                return self._advance_task(task, current_price, indicators)

        if action == "hold":
            if task:
                return self._advance_task(task, current_price, indicators)
            logger.info(
                "[EXECUTION] symbol=%s action=hold status=NOOP reason=decision_hold",
                symbol,
            )
            return {"success": True, "action": "hold", "message": "No action required"}

        intent = "entry" if action in ENTRY_ACTIONS else "exit" if action in EXIT_ACTIONS else "hold"
        if intent == "hold":
            return {"success": True, "action": "hold", "message": "No actionable intent"}

        if task and task.intent != intent:
            self._cancel_task(task, reason="new_intent")
            task = None

        if not task:
            task = self._create_task(decision, symbol, current_price, position_state, intent)
            if not task:
                # 增加详细错误原因输出
                target_size = self._resolve_target_size(decision, position_state, action)
                position_size, open_orders_size, open_orders_count, total_exposure, _ = self._calculate_exposure(
                    symbol, position_state, task_remaining=float(target_size)
                )
                available = max(0.0, self.max_position_size - position_size - open_orders_size)
                
                error_reason = "unknown"
                if target_size <= 0:
                    error_reason = f"target_size_zero (target={target_size:.6f})"
                elif total_exposure >= self.max_position_size:
                    error_reason = f"exposure_limit (position={position_size:.6f}, open_orders={open_orders_size:.6f}, task={target_size:.6f}, total={total_exposure:.6f}, max={self.max_position_size:.6f})"
                elif available < self.min_trade_size:
                    error_reason = f"min_trade_size (available={available:.6f}, min={self.min_trade_size:.6f})"
                
                logger.error(
                    "[EXECUTION] symbol=%s action=%s status=FAILED reason=%s",
                    symbol, action, error_reason
                )
                return {"success": False, "action": action, "error": f"Failed to create task: {error_reason}"}

        return self._advance_task(task, current_price, indicators)

    def tick(self, symbol: str, current_price: float, indicators: Optional[Dict] = None) -> Optional[Dict]:
        task = self.tracker.get(symbol)
        if not task:
            return None
        return self._advance_task(task, current_price, indicators or {})

    def _create_task(
        self,
        decision: Dict,
        symbol: str,
        current_price: float,
        position_state: Dict,
        intent: str,
    ) -> Optional[ExecutionTask]:
        action = str(decision.get("action", "hold"))
        side = self._resolve_order_side(action, position_state)
        target_size = self._resolve_target_size(decision, position_state, action)
        if target_size <= 0:
            logger.info(
                "[EXECUTION] symbol=%s action=%s status=SKIP reason=target_size_zero target=%.6f",
                symbol,
                action,
                float(target_size),
            )
            return None

        position_size, open_orders_size, open_orders_count, total_exposure, _ = self._calculate_exposure(
            symbol, position_state, task_remaining=float(target_size)
        )
        if total_exposure >= self.max_position_size:
            logger.warning(
                "[EXPOSURE] symbol=%s blocked=1 reason=total_exposure_limit position=%.6f open_orders=%.6f task_remaining=%.6f total=%.6f max=%.6f",
                symbol,
                position_size,
                open_orders_size,
                float(target_size),
                total_exposure,
                self.max_position_size,
            )
            return None

        available = max(0.0, self.max_position_size - position_size - open_orders_size)
        if available < self.min_trade_size:
            logger.warning(
                "[EXPOSURE] symbol=%s blocked=1 reason=available_below_min position=%.6f open_orders=%.6f available=%.6f min_trade_size=%.6f",
                symbol,
                position_size,
                open_orders_size,
                available,
                self.min_trade_size,
            )
            return None
        if available < target_size:
            logger.info(
                "[EXPOSURE] symbol=%s reason=trim_task_size available=%.6f original_target=%.6f trimmed_target=%.6f",
                symbol,
                available,
                float(target_size),
                available,
            )
            target_size = available

        entry_range = decision.get("entry_range", [0.0, 0.0]) if isinstance(decision.get("entry_range", None), list) else [0.0, 0.0]
        base_price = float(entry_range[0] or 0.0)
        if base_price <= 0:
            base_price = float(current_price or 0.0)

        max_total = self.entry_max_total_seconds if intent == "entry" else self.exit_max_total_seconds
        stage_wait = self.entry_stage_wait_seconds if intent == "entry" else self.exit_stage_wait_seconds

        exit_reason = str(decision.get("exit_reason", ""))
        is_stop_loss = bool(decision.get("is_stop_loss") or exit_reason.upper() == "STOP_LOSS")
        slippage_limit = self.stop_loss_slippage_limit if is_stop_loss and intent == "exit" else self.slippage_limit

        task = ExecutionTask(
            task_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            intent=intent,
            side=side,
            state=ORDER_CREATED,
            created_at=datetime.now(),
            stage_started_at=datetime.now(),
            stage_index=0,
            max_total_seconds=max_total,
            stage_wait_seconds=stage_wait,
            target_size=float(target_size),
            remaining_size=float(target_size),
            base_price=base_price,
            entry_range=entry_range,
            original_prices=[],
            slippage_limit=slippage_limit,
            stop_loss=float(decision.get("stop_loss", 0.0) or 0.0),
            take_profit=float(decision.get("take_profit", 0.0) or 0.0),
            exit_reason=exit_reason,
        )

        if intent == "entry":
            task.original_prices = self._initial_entry_prices(task, current_price)
        else:
            task.original_prices = [current_price]

        self.tracker.set(task)
        self._log_state(task, reason="task_created", price=base_price, slippage=0.0)
        return task

    def _advance_task(self, task: ExecutionTask, current_price: float, indicators: Dict) -> Dict:
        filled_delta, value_delta = self.tracker.refresh_task(task, self.binance_client, self.order_executor)
        if filled_delta > 0:
            avg_price = value_delta / filled_delta if value_delta > 0 else float(task.last_price or current_price)
            logger.info(
                "[ORDER_FILLED] symbol=%s size=%.6f price=%.2f remaining=%.6f",
                task.symbol,
                float(filled_delta),
                float(avg_price),
                float(task.remaining_size),
            )

        if task.remaining_size < self.min_trade_size:
            return self._complete_task(task, reason="remaining_below_min")

        now = datetime.now()
        if task.defer_until and now < task.defer_until:
            return self._in_progress(task, message="deferred")
        if task.defer_until and now >= task.defer_until and task.state == ORDER_TIMEOUT:
            task.defer_until = None
            task.state = AGGRESSIVE_LIMIT if task.intent == "exit" else PASSIVE_LIMIT
            task.stage_started_at = now
            self._log_state(task, reason="defer_elapsed", price=current_price, slippage=task.last_slippage)

        if task.state == ORDER_CREATED:
            self._place_limit_orders(task, PASSIVE_LIMIT, current_price, indicators)
            task.state = PASSIVE_LIMIT
            task.stage_started_at = now
            return self._in_progress(task, message="passive_limit_placed")

        if task.state in [PASSIVE_LIMIT, ADJUST_LIMIT, AGGRESSIVE_LIMIT]:
            if not task.active_orders:
                self._place_limit_orders(task, task.state, current_price, indicators)
                return self._in_progress(task, message="limit_replaced")

            if self._should_advance(task):
                self._cancel_active_orders(task)
                next_state = MARKET_FALLBACK if task.elapsed_total() >= task.max_total_seconds else self._next_state(task.state)
                if task.intent == "exit" and next_state == MARKET_FALLBACK:
                    task.state = ORDER_TIMEOUT
                    task.defer_until = datetime.now() + timedelta(seconds=self.defer_seconds)
                    self._log_state(task, reason="exit_no_market", price=current_price, slippage=task.last_slippage)
                    return self._in_progress(task, message="exit_no_market")
                if next_state == MARKET_FALLBACK:
                    return self._execute_market_fallback(task, current_price, indicators)
                task.state = next_state
                task.stage_started_at = now
                self._place_limit_orders(task, task.state, current_price, indicators)
                return self._in_progress(task, message="stage_advanced")

            return self._in_progress(task, message="waiting_fill")

        if task.state == MARKET_FALLBACK:
            if task.intent == "exit":
                task.state = ORDER_TIMEOUT
                task.defer_until = datetime.now() + timedelta(seconds=self.defer_seconds)
                self._log_state(task, reason="exit_no_market", price=current_price, slippage=task.last_slippage)
                return self._in_progress(task, message="exit_no_market")
            return self._execute_market_fallback(task, current_price, indicators)

        if task.state == ORDER_TIMEOUT:
            return self._in_progress(task, message="timeout_wait")

        return self._in_progress(task, message="unknown_state")

    def _execute_market_fallback(self, task: ExecutionTask, current_price: float, indicators: Dict) -> Dict:
        if task.intent == "exit":
            task.state = ORDER_TIMEOUT
            task.defer_until = datetime.now() + timedelta(seconds=self.defer_seconds)
            self._log_state(task, reason="exit_no_market", price=current_price, slippage=task.last_slippage)
            return self._in_progress(task, message="exit_no_market")

        est = self.slippage_estimator.estimate(
            task.symbol,
            task.side,
            task.remaining_size,
            current_price,
            indicators,
        )
        slippage = float(est.get("slippage", 0.0) or 0.0)
        task.last_slippage = slippage
        task.last_price = float(est.get("estimated_price", current_price) or current_price)
        logger.info(
            "[SLIPPAGE] symbol=%s method=%s est_price=%.2f slippage=%.4f",
            task.symbol,
            str(est.get("method", "unknown")),
            float(task.last_price),
            float(slippage),
        )

        if slippage > task.slippage_limit:
            task.state = ORDER_TIMEOUT
            task.defer_until = datetime.now() + timedelta(seconds=self.defer_seconds)
            self._log_state(task, reason="slippage_blocked", price=task.last_price, slippage=slippage)
            return self._in_progress(task, message="slippage_blocked")

        result = self._place_market_order(task, current_price)
        if result.get("success"):
            task.filled_size += task.remaining_size
            task.remaining_size = 0.0
            return self._complete_task(task, reason="market_fallback")

        task.state = ORDER_TIMEOUT
        task.defer_until = datetime.now() + timedelta(seconds=self.defer_seconds)
        self._log_state(task, reason="market_failed", price=current_price, slippage=slippage)
        return self._in_progress(task, message="market_failed")

    def _place_limit_orders(self, task: ExecutionTask, stage: str, current_price: float, indicators: Dict) -> None:
        if task.remaining_size < self.min_trade_size:
            return

        position_state = self.position_manager.get_position_state() if self.position_manager else {}
        position_size, open_orders_size, open_orders_count, total_exposure, open_orders = self._calculate_exposure(
            task.symbol, position_state, task_remaining=float(task.remaining_size)
        )
        available_exposure = max(0.0, self.max_position_size - position_size)
        if open_orders_size > available_exposure:
            open_orders_size, open_orders = self._enforce_exposure_limit(
                task.symbol,
                current_price,
                open_orders,
                open_orders_size,
                available_exposure,
            )
        total_exposure = position_size + open_orders_size + float(task.remaining_size)
        if total_exposure >= self.max_position_size:
            task.state = ORDER_TIMEOUT
            task.defer_until = datetime.now() + timedelta(seconds=self.defer_seconds)
            self._log_state(task, reason="exposure_blocked", price=current_price, slippage=task.last_slippage)
            return

        available = max(0.0, self.max_position_size - position_size - open_orders_size)
        if available < self.min_trade_size:
            task.state = ORDER_TIMEOUT
            task.defer_until = datetime.now() + timedelta(seconds=self.defer_seconds)
            self._log_state(task, reason="exposure_blocked", price=current_price, slippage=task.last_slippage)
            return

        if available < task.remaining_size:
            task.remaining_size = available
            task.target_size = task.filled_size + task.remaining_size

        orders = self._build_orders(task, current_price)
        if not orders:
            self._log_state(task, reason="no_orders_generated", price=current_price, slippage=task.last_slippage)
            return

        open_orders_count = self._enforce_order_count(task.symbol, current_price, open_orders, len(orders))
        available_slots = max(0, self.max_orders - open_orders_count)
        if available_slots <= 0:
            self._log_state(task, reason="max_orders_reached", price=current_price, slippage=task.last_slippage)
            return
        if len(orders) > available_slots:
            logger.info(
                "[EXPOSURE] symbol=%s reason=order_slot_trim from=%d to=%d",
                task.symbol,
                len(orders),
                available_slots,
            )
            orders = orders[:available_slots]

        adjusted_orders = []
        best_bid, best_ask = self._get_best_bid_ask(symbol=task.symbol)
        if task.intent == "exit" and stage == PASSIVE_LIMIT and best_bid > 0 and best_ask > 0:
            passive_price = best_bid if task.side == "SELL" else best_ask
            task.original_prices = [passive_price for _ in orders]
            for order in orders:
                adjusted_orders.append({**order, "price": passive_price})
        else:
            for idx, order in enumerate(orders):
                original_price = task.original_prices[idx] if idx < len(task.original_prices) else order["price"]
                new_price = self._adjust_price(
                    symbol=task.symbol,
                    original_price=original_price,
                    current_price=current_price,
                    side=task.side,
                    stage=stage,
                    allow_cross=(stage == AGGRESSIVE_LIMIT),
                    best_bid=best_bid,
                    best_ask=best_ask,
                )
                adjusted_orders.append({**order, "price": new_price})

        for order in adjusted_orders:
            if task.intent == "exit":
                result = self.order_executor.close_position(
                    symbol=task.symbol,
                    position_side="long" if task.side == "SELL" else "short",
                    size=order["size"],
                    order_type="limit",
                    price=order["price"],
                )
            else:
                result = self.order_executor.open_position(
                    symbol=task.symbol,
                    side="long" if task.side == "BUY" else "short",
                    size=order["size"],
                    order_type="limit",
                    price=order["price"],
                    reduce_only=False,
                )

            if result.get("success"):
                logger.info(
                    "[ORDER_SUBMIT] symbol=%s side=%s price=%.2f size=%.6f type=limit stage=%s",
                    task.symbol,
                    task.side,
                    float(order["price"]),
                    float(order["size"]),
                    stage,
                )
                order_data = result.get("order", {})
                order_id = str(order_data.get("order_id", "")) or str(uuid.uuid4())
                task.active_orders[order_id] = {
                    "order_id": order_id,
                    "price": order["price"],
                    "size": order["size"],
                    "filled_qty": 0.0,
                    "status": "NEW",
                    "time": datetime.now().isoformat(),
                }

        task.last_price = float(adjusted_orders[0]["price"])
        self._log_state(task, reason=f"limit_{stage.lower()}", price=task.last_price, slippage=task.last_slippage)

    def _place_market_order(self, task: ExecutionTask, current_price: float) -> Dict:
        logger.info(
            "[ORDER_SUBMIT] symbol=%s side=%s price=%.2f size=%.6f type=market",
            task.symbol,
            task.side,
            float(current_price or 0.0),
            float(task.remaining_size or 0.0),
        )
        if task.intent == "exit":
            return self.order_executor.close_position(
                symbol=task.symbol,
                position_side="long" if task.side == "SELL" else "short",
                size=task.remaining_size,
                order_type="market",
            )
        return self.order_executor.open_position(
            symbol=task.symbol,
            side="long" if task.side == "BUY" else "short",
            size=task.remaining_size,
            order_type="market",
            reduce_only=False,
        )

    def _build_orders(self, task: ExecutionTask, current_price: float) -> List[Dict]:
        remaining = float(task.remaining_size)
        if remaining < self.min_trade_size:
            return []

        if task.intent == "entry":
            return self._build_entry_orders(task, current_price)

        sizes = self._split_size(remaining)
        price = float(current_price)
        return [{"size": size, "price": price} for size in sizes]

    def _build_entry_orders(self, task: ExecutionTask, current_price: float) -> List[Dict]:
        remaining = float(task.remaining_size)
        if remaining < self.min_trade_size:
            return []
        sizes = self._split_size(remaining)
        base_price = float(task.base_price or current_price)
        side = "long" if task.side == "BUY" else "short"
        price_gap = float(settings.PARAMS.get("price_gap", 100.0))
        distance_max = float(settings.PARAMS.get("distance_max", 400.0))
        orders: List[Dict] = []
        for idx, size in enumerate(sizes):
            delta = price_gap * idx
            price = base_price - delta if side == "long" else base_price + delta
            if current_price > 0 and abs(price - float(current_price)) > distance_max:
                price = float(current_price) - distance_max if side == "long" else float(current_price) + distance_max
            orders.append({"size": float(size), "price": float(price)})
        return orders

    def _split_size(self, total: float) -> List[float]:
        if total <= 0:
            return []
        max_possible = int(total / self.min_trade_size) if self.min_trade_size > 0 else 1
        count = max(1, min(self.max_orders, max_possible))
        per = total / count
        if per > self.max_trade_size:
            count = int(math.ceil(total / self.max_trade_size))
            count = max(1, min(self.max_orders, count))
            per = total / count
        sizes = [round(per, 6)] * count
        sizes[-1] = round(total - sum(sizes[:-1]), 6)
        return sizes

    def _get_open_orders(self, symbol: str) -> List[Dict]:
        if not self.order_executor:
            return []
        try:
            return self.order_executor.get_existing_orders(symbol) or []
        except Exception:
            return []

    def _calculate_exposure(
        self,
        symbol: str,
        position_state: Dict,
        task_remaining: float = 0.0,
    ) -> tuple:
        position_size = abs(float(position_state.get("position_size", 0.0) or 0.0))
        open_orders = self._get_open_orders(symbol)
        open_orders_size = 0.0
        for o in open_orders:
            open_orders_size += abs(float(o.get("quantity", o.get("size", 0.0)) or 0.0))
        total_exposure = position_size + open_orders_size + float(task_remaining or 0.0)

        logger.info(
            "[EXPOSURE] symbol=%s position=%.6f open_orders=%.6f task_remaining=%.6f total=%.6f max=%.6f count=%d",
            symbol,
            position_size,
            open_orders_size,
            float(task_remaining or 0.0),
            total_exposure,
            self.max_position_size,
            len(open_orders),
        )
        return position_size, open_orders_size, len(open_orders), total_exposure, open_orders

    def _enforce_order_count(
        self,
        symbol: str,
        current_price: float,
        open_orders: List[Dict],
        extra_needed: int,
    ) -> int:
        open_orders = open_orders or []
        open_orders_count = len(open_orders)
        if open_orders_count + extra_needed <= self.max_orders:
            return open_orders_count

        cancel_count = open_orders_count + extra_needed - self.max_orders

        def distance(order: Dict) -> float:
            price = float(order.get("price", 0.0) or 0.0)
            if price <= 0 or current_price <= 0:
                return float("inf")
            return abs(price - float(current_price))

        sorted_orders = sorted(open_orders, key=distance, reverse=True)
        cancel_ids = [str(o.get("order_id")) for o in sorted_orders[:cancel_count] if o.get("order_id")]
        if cancel_ids:
            self.order_executor.cancel_orders(symbol, cancel_ids)
            logger.info("[ORDER_CANCEL] symbol=%s reason=EXCEED_MAX_ORDERS count=%d", symbol, len(cancel_ids))
            open_orders_count = max(0, open_orders_count - len(cancel_ids))
        return open_orders_count

    def _enforce_exposure_limit(
        self,
        symbol: str,
        current_price: float,
        open_orders: List[Dict],
        open_orders_size: float,
        available_exposure: float,
    ) -> tuple:
        if open_orders_size <= available_exposure:
            return open_orders_size, open_orders

        def distance(order: Dict) -> float:
            price = float(order.get("price", 0.0) or 0.0)
            if price <= 0 or current_price <= 0:
                return float("inf")
            return abs(price - float(current_price))

        sorted_orders = sorted(open_orders, key=distance, reverse=True)
        cancel_ids = []
        remaining_size = float(open_orders_size)
        for order in sorted_orders:
            if remaining_size <= available_exposure:
                break
            qty = abs(float(order.get("quantity", order.get("size", 0.0)) or 0.0))
            order_id = str(order.get("order_id", ""))
            if not order_id:
                continue
            remaining_size -= qty
            cancel_ids.append(order_id)

        if cancel_ids:
            self.order_executor.cancel_orders(symbol, cancel_ids)
            logger.info("[ORDER_CANCEL] symbol=%s reason=EXPOSURE_LIMIT count=%d", symbol, len(cancel_ids))
            remaining_orders = [o for o in open_orders if str(o.get("order_id")) not in cancel_ids]
            return max(0.0, remaining_size), remaining_orders

        return open_orders_size, open_orders

    def _adjust_price(
        self,
        symbol: str,
        original_price: float,
        current_price: float,
        side: str,
        stage: str,
        allow_cross: bool,
        best_bid: float = 0.0,
        best_ask: float = 0.0,
    ) -> float:
        side = str(side).upper()
        original_price = float(original_price or current_price)
        current_price = float(current_price or original_price)
        diff = current_price - original_price
        direction = 1.0 if diff > 0 else -1.0 if diff < 0 else 0.0
        abs_diff = abs(diff)

        if stage == PASSIVE_LIMIT:
            price = original_price
        elif stage == ADJUST_LIMIT:
            move = min(self.max_price_adjust * 0.66, abs_diff)
            price = original_price + direction * move
        else:
            move = min(self.max_price_adjust, abs_diff)
            price = original_price + direction * move

        if not allow_cross and best_bid > 0 and best_ask > 0:
            if side == "BUY":
                price = min(price, best_bid)
            else:
                price = max(price, best_ask)

        if hasattr(self.order_executor, "_round_price"):
            try:
                return self.order_executor._round_price(symbol, price)
            except Exception:
                return price
        return price

    def _get_best_bid_ask(self, symbol: str) -> tuple:
        snapshot = self.slippage_estimator.get_orderbook_snapshot(symbol)
        best_bid = float(snapshot.get("best_bid", 0.0) or 0.0)
        best_ask = float(snapshot.get("best_ask", 0.0) or 0.0)
        if best_bid <= 0 or best_ask <= 0:
            return (0.0, 0.0)
        return (best_bid, best_ask)

    def _should_advance(self, task: ExecutionTask) -> bool:
        if task.elapsed_total() >= task.max_total_seconds:
            return True
        return task.elapsed_stage() >= task.stage_wait_seconds

    def _next_state(self, current: str) -> str:
        if current == PASSIVE_LIMIT:
            return ADJUST_LIMIT
        if current == ADJUST_LIMIT:
            return AGGRESSIVE_LIMIT
        return MARKET_FALLBACK

    def _cancel_active_orders(self, task: ExecutionTask) -> None:
        if not task.active_orders:
            return
        ids = list(task.active_orders.keys())
        self.order_executor.cancel_orders(task.symbol, ids)
        task.active_orders = {}
        logger.info("[ORDER_CANCEL] symbol=%s reason=cancel_active_orders count=%d", task.symbol, len(ids))
        self._log_state(task, reason="cancel_active_orders", price=task.last_price, slippage=task.last_slippage)

    def _complete_task(self, task: ExecutionTask, reason: str) -> Dict:
        task.state = ORDER_FILLED
        self._cancel_active_orders(task)
        self._log_state(task, reason=reason, price=task.last_price, slippage=task.last_slippage)
        self.tracker.remove(task.symbol)

        avg_price = 0.0
        if task.filled_size > 0 and task.filled_value > 0:
            avg_price = task.filled_value / task.filled_size
        return {
            "success": True,
            "action": task.action,
            "completed": True,
            "filled_size": task.filled_size,
            "avg_price": avg_price,
            "remaining_size": task.remaining_size,
            "state": task.state,
        }

    def _in_progress(self, task: ExecutionTask, message: str) -> Dict:
        return {
            "success": True,
            "action": task.action,
            "in_progress": True,
            "message": message,
            "state": task.state,
            "remaining_size": task.remaining_size,
            "filled_size": task.filled_size,
            "slippage": task.last_slippage,
            "elapsed": task.elapsed_total(),
        }

    def _resolve_order_side(self, action: str, position_state: Dict) -> str:
        if action == "open_long":
            return "BUY"
        if action == "add_position":
            return "BUY" if position_state.get("side", "long") == "long" else "SELL"
        if action == "open_short":
            return "SELL"
        if action == "close_position":
            return "SELL" if position_state.get("side", "long") == "long" else "BUY"
        if action == "reverse_position":
            return "SELL" if position_state.get("side", "long") == "long" else "BUY"
        return "BUY"

    def _resolve_target_size(self, decision: Dict, position_state: Dict, action: str) -> float:
        if action in ["open_long", "open_short"]:
            return float(decision.get("target_size", decision.get("size", [0.0])[0] if isinstance(decision.get("size", [0.0]), list) else decision.get("size", 0.0)) or 0.0)
        if action == "add_position":
            target = float(decision.get("target_size", 0.0) or 0.0)
            current = float(position_state.get("position_size", 0.0) or 0.0)
            return max(0.0, target - current)
        if action in ["close_position", "reverse_position"]:
            return float(position_state.get("position_size", 0.0) or 0.0)
        return 0.0

    def _initial_entry_prices(self, task: ExecutionTask, current_price: float) -> List[float]:
        orders = self._build_entry_orders(task, current_price)
        if not orders:
            return [float(task.base_price or current_price)]
        return [float(o.get("price", task.base_price or current_price)) for o in orders]

    def _log_state(self, task: ExecutionTask, reason: str, price: float, slippage: float) -> None:
        logger.info(
            "[EXECUTION] symbol=%s action=%s state=%s reason=%s price=%.2f remaining=%.6f slippage=%.4f elapsed=%.1fs",
            task.symbol,
            task.action,
            task.state,
            reason,
            float(price or 0.0),
            float(task.remaining_size or 0.0),
            float(slippage or 0.0),
            float(task.elapsed_total() or 0.0),
        )
