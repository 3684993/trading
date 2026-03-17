from typing import Dict, List, Optional
from datetime import datetime, timedelta
from core.logger import logger


class TradeGuard:
    def __init__(
        self,
        min_order_interval_seconds: int = 300,
        trend_confirmation_count: int = 3,
        max_pending_orders: int = 5
    ):
        self.min_order_interval_seconds = min_order_interval_seconds
        self.trend_confirmation_count = trend_confirmation_count
        self.max_pending_orders = max_pending_orders
        
        self.last_order_time: Dict[str, datetime] = {}
        self.pending_entry_orders: Dict[str, List[str]] = {}
        self.trend_confirmation: Dict[str, Dict] = {}
        self.position_metadata: Dict[str, Dict] = {}
        
        self.blocked_orders: List[Dict] = []
        
        logger.info(
            f"TradeGuard initialized: "
            f"interval={min_order_interval_seconds}s, "
            f"trend_confirm={trend_confirmation_count}"
        )
    
    def can_place_order(self, symbol: str, binance_client=None) -> Dict:
        reasons = []
        
        if symbol in self.pending_entry_orders and len(self.pending_entry_orders[symbol]) > 0:
            active_orders = self._check_pending_orders_status(symbol, binance_client)
            if active_orders > 0:
                reasons.append({
                    "reason": "PENDING_ORDERS_EXIST",
                    "detail": f"{active_orders} pending entry orders"
                })
        
        last_time = self.last_order_time.get(symbol)
        if last_time:
            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < self.min_order_interval_seconds:
                remaining = self.min_order_interval_seconds - elapsed
                reasons.append({
                    "reason": "ORDER_INTERVAL_NOT_MET",
                    "detail": f"{elapsed:.0f}s elapsed, {remaining:.0f}s remaining"
                })
        
        if reasons:
            for r in reasons:
                logger.info(f"[{symbol}] ORDER_BLOCKED_REASON: {r['reason']} - {r['detail']}")
            return {
                "allowed": False,
                "reasons": reasons
            }
        
        return {
            "allowed": True,
            "reasons": []
        }
    
    def _check_pending_orders_status(self, symbol: str, binance_client) -> int:
        if symbol not in self.pending_entry_orders:
            return 0
        
        if not binance_client:
            return len(self.pending_entry_orders[symbol])
        
        still_active = []
        for order_id in self.pending_entry_orders[symbol]:
            try:
                result = binance_client.client.query_order(symbol=symbol, orderId=order_id)
                status = result.get("status")
                if status in ["NEW", "PARTIALLY_FILLED"]:
                    still_active.append(order_id)
                else:
                    logger.info(f"[{symbol}] Order {order_id} completed (status={status})")
            except Exception as e:
                logger.warning(f"[{symbol}] Failed to query order {order_id}: {e}")
                still_active.append(order_id)
        
        self.pending_entry_orders[symbol] = still_active
        
        if not still_active:
            del self.pending_entry_orders[symbol]
            logger.info(f"[{symbol}] All pending orders cleared")
        
        return len(still_active)
    
    def validate_decision(
        self,
        decision: Dict,
        symbol: str,
        position_state: Dict,
        binance_client=None
    ) -> Dict:
        action = decision.get("action", "hold")
        
        if action == "hold":
            return {
                "valid": True,
                "action": "hold",
                "modified": False
            }
        
        if action in ["open_long", "open_short"]:
            can_place = self.can_place_order(symbol, binance_client)
            if not can_place["allowed"]:
                self._record_blocked_order(symbol, action, can_place["reasons"])
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "ORDER_BLOCKED",
                    "details": can_place["reasons"]
                }
        
        if action == "add_position":
            if not position_state.get("has_position"):
                logger.warning(f"[{symbol}] add_position rejected - no position exists")
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "NO_POSITION_TO_ADD"
                }
            
            pnl_pct = float(position_state.get("current_pnl_pct", 0) or 0)
            ai_confidence = float(decision.get("confidence", 0) or 0)

            # 新规则：允许小幅亏损加仓（>-0.5%），高置信度>0.75可覆盖该限制
            allow_add_position = pnl_pct > -0.5
            ignore_small_loss_rule = ai_confidence > 0.75

            if not allow_add_position and not ignore_small_loss_rule:
                logger.info(f"[{symbol}] add_position rejected - pnl too low ({pnl_pct:.2f}%), conf={ai_confidence:.2f}")
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "POSITION_NOT_PROFITABLE"
                }

            if ignore_small_loss_rule and pnl_pct <= -0.5:
                logger.warning(f"TRADEGUARD_OVERRIDE: {symbol} add_position allowed by confidence={ai_confidence:.2f} with pnl={pnl_pct:.2f}%")
            
            can_place = self.can_place_order(symbol, binance_client)
            if not can_place["allowed"]:
                self._record_blocked_order(symbol, action, can_place["reasons"])
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "ORDER_BLOCKED",
                    "details": can_place["reasons"]
                }
            
            if position_state.get("position_size", 0) >= 0.04:
                logger.info(f"[{symbol}] add_position rejected - position size limit reached")
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "POSITION_SIZE_LIMIT"
                }
        
        if action == "close_position":
            if not position_state.get("has_position"):
                logger.warning(f"[{symbol}] close_position rejected - no position to close")
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "NO_POSITION_TO_CLOSE"
                }
            
            min_hold_check = self._check_min_hold_time(symbol, position_state)
            if not min_hold_check["allowed"]:
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "MIN_HOLD_ENFORCED",
                    "details": min_hold_check
                }
        
        if action == "reverse_position":
            if not position_state.get("has_position"):
                return {
                    "valid": True,
                    "action": "open_long",
                    "modified": True,
                    "reason": "NO_POSITION_TO_REVERSE"
                }
            
            trend_check = self._check_trend_confirmation(symbol, decision, position_state)
            if not trend_check["confirmed"]:
                logger.info(f"[{symbol}] TREND_CONFIRMATION_WAIT: {trend_check['count']}/{self.trend_confirmation_count}")
                return {
                    "valid": True,
                    "action": "hold",
                    "modified": True,
                    "reason": "TREND_CONFIRMATION_WAIT",
                    "details": trend_check
                }
        
        return {
            "valid": True,
            "action": action,
            "modified": False
        }
    
    def _check_min_hold_time(self, symbol: str, position_state: Dict) -> Dict:
        """持仓最短时间规则（秒级）+ 盈利提前平仓 + 强趋势延长持仓。"""
        metadata = self.position_metadata.get(symbol, {})
        entry_time = metadata.get("entry_time")

        if not entry_time:
            return {"allowed": True, "reason": "No entry time recorded"}

        hold_seconds = (datetime.now() - entry_time).total_seconds()

        # 基础最短持仓：120秒
        required_hold_seconds = 120

        # 趋势强则延长持仓（额外+120秒）
        trend_strength = str(position_state.get("trend_strength", "normal")).lower()
        if trend_strength in ["strong", "very_strong", "high"]:
            required_hold_seconds += 120

        # 净利润（已扣手续费）>0 允许提前平仓
        pnl = float(position_state.get("current_pnl", position_state.get("pnl", 0.0)) or 0.0)
        fees = float(position_state.get("fees", 0.0) or 0.0)
        net_profit = pnl - fees

        if hold_seconds < required_hold_seconds and net_profit <= 0:
            remaining_seconds = required_hold_seconds - hold_seconds
            logger.info(
                f"[{symbol}] MIN_HOLD_ENFORCED: "
                f"{hold_seconds:.0f}s held, {required_hold_seconds}s required, "
                f"{remaining_seconds:.0f}s remaining, net_profit={net_profit:.4f}"
            )
            return {
                "allowed": False,
                "reason": "MIN_HOLD_TIME_NOT_MET",
                "held_seconds": hold_seconds,
                "required_seconds": required_hold_seconds,
                "remaining_seconds": remaining_seconds,
                "net_profit": net_profit,
            }

        if hold_seconds < required_hold_seconds and net_profit > 0:
            return {
                "allowed": True,
                "reason": "EARLY_CLOSE_ALLOWED_BY_NET_PROFIT",
                "held_seconds": hold_seconds,
                "required_seconds": required_hold_seconds,
                "net_profit": net_profit,
            }

        return {
            "allowed": True,
            "reason": "Min hold time satisfied",
            "held_seconds": hold_seconds,
            "required_seconds": required_hold_seconds,
            "net_profit": net_profit,
        }
    
    def _check_trend_confirmation(self, symbol: str, decision: Dict, position_state: Dict) -> Dict:
        current_side = position_state.get("side", "long")
        new_action = decision.get("action", "hold")
        
        if new_action != "reverse_position":
            return {"confirmed": True, "count": 0}
        
        new_side = "short" if current_side == "long" else "long"
        
        if symbol not in self.trend_confirmation:
            self.trend_confirmation[symbol] = {
                "target_side": new_side,
                "count": 1,
                "first_seen": datetime.now()
            }
            return {
                "confirmed": False,
                "count": 1,
                "required": self.trend_confirmation_count
            }
        
        confirmation = self.trend_confirmation[symbol]
        
        if confirmation["target_side"] != new_side:
            self.trend_confirmation[symbol] = {
                "target_side": new_side,
                "count": 1,
                "first_seen": datetime.now()
            }
            return {
                "confirmed": False,
                "count": 1,
                "required": self.trend_confirmation_count
            }
        
        confirmation["count"] += 1
        
        if confirmation["count"] >= self.trend_confirmation_count:
            del self.trend_confirmation[symbol]
            return {
                "confirmed": True,
                "count": confirmation["count"],
                "required": self.trend_confirmation_count
            }
        
        return {
            "confirmed": False,
            "count": confirmation["count"],
            "required": self.trend_confirmation_count
        }
    
    def record_order(self, symbol: str, order_id: str, order_data: Dict) -> None:
        self.last_order_time[symbol] = datetime.now()
        
        if symbol not in self.pending_entry_orders:
            self.pending_entry_orders[symbol] = []
        
        self.pending_entry_orders[symbol].append(order_id)
        
        if len(self.pending_entry_orders[symbol]) > self.max_pending_orders:
            self.pending_entry_orders[symbol] = self.pending_entry_orders[symbol][-self.max_pending_orders:]
        
        logger.info(f"[{symbol}] Order recorded: {order_id}")
    
    def record_position_entry(
        self,
        symbol: str,
        entry_price: float,
        position_size: float,
        side: str,
        expected_hold_minutes: int = 60,
        stop_loss: float = 0,
        take_profit: float = 0
    ) -> None:
        self.position_metadata[symbol] = {
            "entry_time": datetime.now(),
            "entry_price": entry_price,
            "position_size": position_size,
            "side": side,
            "expected_hold_minutes": expected_hold_minutes,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "scale_in_count": 1
        }
        
        if symbol in self.trend_confirmation:
            del self.trend_confirmation[symbol]
        
        logger.info(
            f"[{symbol}] Position metadata recorded: "
            f"entry={entry_price:.2f}, side={side}, "
            f"expected_hold={expected_hold_minutes}min"
        )
    
    def update_position_metadata(
        self,
        symbol: str,
        position_size: float = None,
        stop_loss: float = None,
        take_profit: float = None
    ) -> None:
        if symbol not in self.position_metadata:
            return
        
        if position_size is not None:
            self.position_metadata[symbol]["position_size"] = position_size
            self.position_metadata[symbol]["scale_in_count"] = \
                self.position_metadata[symbol].get("scale_in_count", 1) + 1
        
        if stop_loss is not None:
            self.position_metadata[symbol]["stop_loss"] = stop_loss
        
        if take_profit is not None:
            self.position_metadata[symbol]["take_profit"] = take_profit
    
    def clear_position(self, symbol: str) -> None:
        if symbol in self.position_metadata:
            del self.position_metadata[symbol]
            logger.info(f"[{symbol}] Position metadata cleared")
        
        if symbol in self.pending_entry_orders:
            del self.pending_entry_orders[symbol]
        
        if symbol in self.trend_confirmation:
            del self.trend_confirmation[symbol]
    
    def get_position_metadata(self, symbol: str) -> Optional[Dict]:
        return self.position_metadata.get(symbol)
    
    def _record_blocked_order(self, symbol: str, action: str, reasons: List[Dict]) -> None:
        self.blocked_orders.append({
            "symbol": symbol,
            "action": action,
            "reasons": reasons,
            "timestamp": datetime.now()
        })
        
        if len(self.blocked_orders) > 100:
            self.blocked_orders = self.blocked_orders[-100:]
    
    def get_local_sl_tp(self, symbol: str, current_price: float) -> Dict:
        metadata = self.position_metadata.get(symbol)
        if not metadata:
            return {"triggered": False}
        
        stop_loss = metadata.get("stop_loss", 0)
        take_profit = metadata.get("take_profit", 0)
        side = metadata.get("side", "long")
        
        if stop_loss <= 0 and take_profit <= 0:
            return {"triggered": False}
        
        triggered = False
        trigger_type = None
        
        if side == "long":
            if stop_loss > 0 and current_price <= stop_loss:
                triggered = True
                trigger_type = "STOP_LOSS"
                logger.info(f"[{symbol}] SLTP_SIMULATION_TRIGGERED: STOP_LOSS at {stop_loss}")
            elif take_profit > 0 and current_price >= take_profit:
                triggered = True
                trigger_type = "TAKE_PROFIT"
                logger.info(f"[{symbol}] SLTP_SIMULATION_TRIGGERED: TAKE_PROFIT at {take_profit}")
        else:
            if stop_loss > 0 and current_price >= stop_loss:
                triggered = True
                trigger_type = "STOP_LOSS"
                logger.info(f"[{symbol}] SLTP_SIMULATION_TRIGGERED: STOP_LOSS at {stop_loss}")
            elif take_profit > 0 and current_price <= take_profit:
                triggered = True
                trigger_type = "TAKE_PROFIT"
                logger.info(f"[{symbol}] SLTP_SIMULATION_TRIGGERED: TAKE_PROFIT at {take_profit}")
        
        return {
            "triggered": triggered,
            "trigger_type": trigger_type,
            "stop_loss": stop_loss,
            "take_profit": take_profit
        }
    
    def get_stats(self) -> Dict:
        return {
            "pending_orders_count": sum(len(v) for v in self.pending_entry_orders.values()),
            "active_positions": len(self.position_metadata),
            "blocked_orders_count": len(self.blocked_orders),
            "min_order_interval_seconds": self.min_order_interval_seconds,
            "trend_confirmation_count": self.trend_confirmation_count
        }
