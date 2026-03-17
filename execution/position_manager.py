from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from core.logger import logger
from risk.dynamic_stop_loss import dynamic_stop_loss


class PositionManager:
    """统一持仓管理入口。

    所有模块必须通过该类访问/更新持仓信息，禁止直接读取交易所仓位。
    统一字段：entry_price, position_size, entry_time, pnl, fees
    """

    def __init__(self):
        self.position: Optional[Dict] = None
        self.position_history: list = []
        self._last_sync_time: Optional[datetime] = None
        logger.info("PositionManager initialized")

    def sync_position_from_exchange(self, binance_client, symbol: str) -> Dict:
        """集中同步交易所仓位（仅允许在 PositionManager 内调用交易所）。"""
        try:
            positions = binance_client.client.get_position_risk(symbol=symbol)
            for pos in positions:
                if pos.get("symbol") != symbol:
                    continue

                amount = float(pos.get("positionAmt", 0) or 0)
                if abs(amount) < 1e-8:
                    self.position = None
                    return {"status": "no_position"}

                entry_price = float(pos.get("entryPrice", 0) or 0)
                pnl = float(pos.get("unRealizedProfit", 0) or 0)
                self.position = {
                    "symbol": symbol,
                    "side": "long" if amount > 0 else "short",
                    "entry_price": entry_price,
                    "position_size": abs(amount),
                    "entry_time": self.position.get("entry_time") if self.position else datetime.now(),
                    "pnl": pnl,
                    "fees": float(self.position.get("fees", 0.0)) if self.position else 0.0,
                    "current_price": entry_price,
                    "current_pnl": pnl,
                    "current_pnl_pct": 0.0,
                    "hold_minutes": 0,
                    "stop_loss": float(self.position.get("stop_loss", 0.0)) if self.position else 0.0,
                    "take_profit": float(self.position.get("take_profit", 0.0)) if self.position else 0.0,
                    "expected_hold_minutes": int(self.position.get("expected_hold_minutes", 60)) if self.position else 60,
                    "peak_price": entry_price,
                    "max_profit": 0.0,
                    "max_profit_pct": 0.0,
                    "drawdown": 0.0,
                    "drawdown_pct": 0.0,
                }
                self._last_sync_time = datetime.now()
                return {"status": "synced", "position": self.position}

            self.position = None
            return {"status": "no_position"}
        except Exception as e:
            logger.error(f"Sync position from exchange error: {e}")
            return {"status": "error", "message": str(e)}

    def update_position(
        self,
        symbol: str,
        position_size: float,
        entry_price: float,
        side: str = "long",
        timestamp: datetime = None,
        expected_hold_minutes: int = 60,
        stop_loss: float = 0,
        take_profit: float = 0,
    ) -> Dict:
        position_size = float(position_size or 0.0)
        entry_price = float(entry_price or 0.0)

        if position_size <= 0:
            if self.position:
                self.position["close_time"] = timestamp or datetime.now()
                self.position_history.append(self.position.copy())
            self.position = None
            return {"status": "closed"}

        entry_time = timestamp or (self.position.get("entry_time") if self.position else datetime.now())
        self.position = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "position_size": position_size,
            "entry_time": entry_time,
            "pnl": float(self.position.get("pnl", 0.0)) if self.position else 0.0,
            "fees": float(self.position.get("fees", 0.0)) if self.position else 0.0,
            "current_price": entry_price,
            "current_pnl": 0.0,
            "current_pnl_pct": 0.0,
            "hold_minutes": 0,
            "expected_hold_minutes": expected_hold_minutes,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "peak_price": entry_price,
            "max_profit": 0.0,
            "max_profit_pct": 0.0,
            "drawdown": 0.0,
            "drawdown_pct": 0.0,
        }
        return {"status": "opened", "position": self.position}

    def add_to_position(self, symbol: str, add_size: float, add_price: float) -> Dict:
        if not self.position or self.position.get("symbol") != symbol:
            return {"status": "error", "message": "No position to add to"}

        current_size = float(self.position.get("position_size", 0.0))
        current_entry = float(self.position.get("entry_price", 0.0))
        add_size = float(add_size)
        add_price = float(add_price)

        new_size = current_size + add_size
        new_entry = (current_size * current_entry + add_size * add_price) / new_size if new_size > 0 else current_entry

        self.position["position_size"] = new_size
        self.position["entry_price"] = new_entry
        self.position["entry_price"] = new_entry
        return {"status": "scaled", "new_size": new_size, "new_entry": new_entry}

    def calculate_pnl(self, current_price: float) -> Dict:
        if not self.position:
            return {"pnl": 0.0, "pnl_pct": 0.0}

        current_price = float(current_price)
        entry_price = float(self.position.get("entry_price", 0.0))
        size = float(self.position.get("position_size", 0.0))
        side = self.position.get("side", "long")

        if entry_price <= 0 or size <= 0:
            return {"pnl": 0.0, "pnl_pct": 0.0}

        pnl = (current_price - entry_price) * size if side == "long" else (entry_price - current_price) * size
        pnl_pct = ((current_price - entry_price) / entry_price * 100.0) if side == "long" else ((entry_price - current_price) / entry_price * 100.0)

        self.position["current_price"] = current_price
        self.position["current_pnl"] = pnl
        self.position["current_pnl_pct"] = pnl_pct
        self.position["pnl"] = pnl

        peak_price = float(self.position.get("peak_price", entry_price))
        if side == "long":
            peak_price = max(peak_price, current_price)
            drawdown = (peak_price - current_price) * size
            drawdown_pct = ((peak_price - current_price) / peak_price * 100.0) if peak_price > 0 else 0.0
            max_profit = (peak_price - entry_price) * size
        else:
            peak_price = min(peak_price, current_price)
            drawdown = (current_price - peak_price) * size
            drawdown_pct = ((current_price - peak_price) / peak_price * 100.0) if peak_price > 0 else 0.0
            max_profit = (entry_price - peak_price) * size

        self.position["peak_price"] = peak_price
        self.position["max_profit"] = max_profit
        self.position["max_profit_pct"] = (max_profit / (entry_price * size) * 100.0) if entry_price * size > 0 else 0.0
        self.position["drawdown"] = drawdown
        self.position["drawdown_pct"] = drawdown_pct

        return {"pnl": pnl, "pnl_pct": pnl_pct}

    def calculate_hold_minutes(self, binance_client=None) -> int:
        if not self.position or not self.position.get("entry_time"):
            return 0
        hold_minutes = int((datetime.now() - self.position["entry_time"]).total_seconds() / 60)
        self.position["hold_minutes"] = hold_minutes
        return hold_minutes

    def check_min_hold_time(self, symbol: str, trend_strength: str = "normal") -> Dict:
        if not self.position or self.position.get("symbol") != symbol:
            return {"allowed": True, "reason": "No position"}

        held_seconds = (datetime.now() - self.position.get("entry_time", datetime.now())).total_seconds()
        required_seconds = 120

        if str(trend_strength).lower() in ["strong", "very_strong", "high"]:
            required_seconds += 120

        pnl = float(self.position.get("current_pnl", self.position.get("pnl", 0.0)) or 0.0)
        fees = float(self.position.get("fees", 0.0) or 0.0)
        net_profit = pnl - fees

        if held_seconds < required_seconds and net_profit <= 0:
            return {
                "allowed": False,
                "reason": "MIN_HOLD_TIME_NOT_MET",
                "held_seconds": held_seconds,
                "required_seconds": required_seconds,
                "net_profit": net_profit,
            }

        if held_seconds < required_seconds and net_profit > 0:
            return {
                "allowed": True,
                "reason": "EARLY_CLOSE_ALLOWED_BY_NET_PROFIT",
                "held_seconds": held_seconds,
                "required_seconds": required_seconds,
                "net_profit": net_profit,
            }

        return {"allowed": True, "reason": "Min hold time satisfied", "net_profit": net_profit}

    def check_local_sl_tp(
        self,
        symbol: str,
        current_price: float,
        trend_direction: str = "",
        trend_strength: float = 0.0,
    ) -> Dict:
        if not self.position or self.position.get("symbol") != symbol:
            return {"triggered": False}

        side = self.position.get("side", "long")
        stop_loss = float(self.position.get("stop_loss", 0.0))
        take_profit = float(self.position.get("take_profit", 0.0))
        current_price = float(current_price)

        trigger_type = None
        if side == "long":
            if stop_loss > 0 and current_price <= stop_loss:
                trigger_type = "STOP_LOSS"
            elif take_profit > 0 and current_price >= take_profit:
                trigger_type = "TAKE_PROFIT"
        else:
            if stop_loss > 0 and current_price >= stop_loss:
                trigger_type = "STOP_LOSS"
            elif take_profit > 0 and current_price <= take_profit:
                trigger_type = "TAKE_PROFIT"

        if not trigger_type:
            return {"triggered": False, "stop_loss": stop_loss, "take_profit": take_profit}

        if trigger_type == "STOP_LOSS":
            return {"triggered": True, "trigger_type": "STOP_LOSS", "stop_loss": stop_loss, "take_profit": take_profit}

        # TAKE_PROFIT gating: min hold time, profit thresholds, trend alignment
        entry_time = self.position.get("entry_time")
        held_seconds = (datetime.now() - entry_time).total_seconds() if entry_time else 0.0
        entry_price = float(self.position.get("entry_price", 0.0) or 0.0)
        if entry_price <= 0:
            return {"triggered": True, "trigger_type": "TAKE_PROFIT", "stop_loss": stop_loss, "take_profit": take_profit}

        if side == "long":
            profit_rate = (current_price - entry_price) / entry_price * 100.0
        else:
            profit_rate = (entry_price - current_price) / entry_price * 100.0

        fees = float(self.position.get("fees", 0.0) or 0.0)
        size = float(self.position.get("position_size", 0.0) or 0.0)
        fee_rate = (fees / (entry_price * size) * 100.0) if entry_price * size > 0 else 0.0
        net_profit_rate = profit_rate - fee_rate

        min_hold_seconds = 120
        extend_seconds = 120

        trend_key = str(trend_direction or "").lower()
        aligned = False
        if side == "long" and trend_key in ["bullish", "trend_up", "up", "long"]:
            aligned = True
        if side == "short" and trend_key in ["bearish", "trend_down", "down", "short"]:
            aligned = True

        if net_profit_rate > 0.5:
            return {"triggered": True, "trigger_type": "TAKE_PROFIT", "stop_loss": stop_loss, "take_profit": take_profit}

        required_seconds = min_hold_seconds + (extend_seconds if aligned else 0)
        if held_seconds < required_seconds:
            logger.info(
                "[RISK_TRIGGER] symbol=%s reason=TAKE_PROFIT_DELAY hold=%.0fs<%ds profit=%.3f%% aligned=%s",
                symbol,
                held_seconds,
                required_seconds,
                profit_rate,
                aligned,
            )
            return {"triggered": False, "reason": "TAKE_PROFIT_DELAY"}

        if net_profit_rate < 0.2:
            logger.info(
                "[RISK_TRIGGER] symbol=%s reason=TAKE_PROFIT_TOO_SMALL profit=%.3f%% net=%.3f%%",
                symbol,
                profit_rate,
                net_profit_rate,
            )
            return {"triggered": False, "reason": "TAKE_PROFIT_TOO_SMALL"}

        return {"triggered": True, "trigger_type": "TAKE_PROFIT", "stop_loss": stop_loss, "take_profit": take_profit}

    def get_position_state(self) -> Dict:
        if not self.position:
            return {
                "has_position": False,
                "symbol": None,
                "entry_price": 0.0,
                "position_size": 0.0,
                "entry_time": None,
                "pnl": 0.0,
                "fees": 0.0,
            }

        return {
            "has_position": True,
            **self.position,
        }

    def set_stop_loss(self, symbol: str, stop_loss: float) -> None:
        if self.position and self.position.get("symbol") == symbol:
            self.position["stop_loss"] = float(stop_loss)

    def set_take_profit(self, symbol: str, take_profit: float) -> None:
        if self.position and self.position.get("symbol") == symbol:
            self.position["take_profit"] = float(take_profit)

    def format_position_log(self) -> str:
        state = self.get_position_state()
        if not state.get("has_position"):
            return "无持仓"
        return (
            f"{state['symbol']} {state['side']} size={state['position_size']:.4f} "
            f"entry={state['entry_price']:.2f} pnl={state.get('pnl', 0.0):.2f} fees={state.get('fees', 0.0):.4f}"
        )


    def dynamic_trailing_stop(self, symbol: str, current_price: float) -> Dict:
        """动态追踪止损。

        规则：
        - profit > 0.5%: SL = entry_price
        - profit > 1%: 锁定 0.3%
        - profit > 2%: 锁定 1%
        """
        if not self.position or self.position.get("symbol") != symbol:
            return {"adjusted": False, "reason": "No position"}

        entry_price = float(self.position.get("entry_price", 0.0))
        if entry_price <= 0:
            return {"adjusted": False, "reason": "Invalid entry"}

        side = self.position.get("side", "long")
        current_price = float(current_price)
        current_sl = float(self.position.get("stop_loss", 0.0) or 0.0)

        if side == "long":
            profit_pct = (current_price - entry_price) / entry_price * 100.0
            if profit_pct <= 0.5:
                return {"adjusted": False, "reason": "Profit below trailing threshold", "profit_pct": profit_pct}

            if profit_pct > 2.0:
                candidate_sl = entry_price * 1.01
            elif profit_pct > 1.0:
                candidate_sl = entry_price * 1.003
            else:
                candidate_sl = entry_price

            new_sl = max(current_sl, candidate_sl) if current_sl > 0 else candidate_sl

        else:
            profit_pct = (entry_price - current_price) / entry_price * 100.0
            if profit_pct <= 0.5:
                return {"adjusted": False, "reason": "Profit below trailing threshold", "profit_pct": profit_pct}

            if profit_pct > 2.0:
                candidate_sl = entry_price * 0.99
            elif profit_pct > 1.0:
                candidate_sl = entry_price * 0.997
            else:
                candidate_sl = entry_price

            new_sl = min(current_sl, candidate_sl) if current_sl > 0 else candidate_sl

        if current_sl > 0 and abs(new_sl - current_sl) < 1e-8:
            return {"adjusted": False, "reason": "No tighter SL", "profit_pct": profit_pct, "stop_loss": current_sl}

        self.position["stop_loss"] = float(round(new_sl, 4))
        return {
            "adjusted": True,
            "reason": "DYNAMIC_TRAILING_STOP",
            "profit_pct": profit_pct,
            "old_stop_loss": current_sl,
            "new_stop_loss": float(round(new_sl, 4)),
        }

    def dynamic_adjust_stop_loss(self, symbol: str, historical_prices: list, atr: float) -> Dict:
        if not self.position or self.position.get("symbol") != symbol:
            return {"adjusted": False, "reason": "No position"}

        side = self.position.get("side", "long")
        current_price = float(self.position.get("current_price", self.position.get("entry_price", 0.0)))
        result = dynamic_stop_loss.dynamic_stop_loss_adjustment(
            symbol=symbol,
            position_info=self.position,
            historical_prices=historical_prices or [],
            atr=float(atr or 0.0),
        )
        if result.get("adjusted"):
            self.position["stop_loss"] = float(result.get("new_stop_loss", self.position.get("stop_loss", 0.0)))
        return result

    def add_fee(self, fee: float) -> None:
        if self.position:
            self.position["fees"] = float(self.position.get("fees", 0.0)) + float(fee)
