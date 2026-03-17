from __future__ import annotations

from datetime import datetime
from execution.position_manager import PositionManager as _ExecutionPositionManager
from core.logger import logger


class PositionManager(_ExecutionPositionManager):
    """Core PositionManager with unified access contract."""

    def calculate_hold_minutes(self, binance_client=None) -> int:
        if not self.position:
            return 0
        if not self.position.get("entry_time"):
            self.position["entry_time"] = datetime.now()
        hold_minutes = int((datetime.now() - self.position["entry_time"]).total_seconds() / 60)
        self.position["hold_minutes"] = hold_minutes
        logger.info(f"POSITION_HOLD_TIME: {self.position.get('symbol', 'N/A')} 持仓：{hold_minutes:.1f} 分钟")
        return hold_minutes

    def update_position(self, *args, **kwargs):
        result = super().update_position(*args, **kwargs)
        if self.position:
            if not self.position.get("entry_time"):
                self.position["entry_time"] = datetime.now()
            self.position["size"] = float(self.position.get("position_size", 0.0))
            self.position["unrealized_pnl"] = float(self.position.get("current_pnl", self.position.get("pnl", 0.0)))
            self.position["scale_in_count"] = int(self.position.get("scale_in_count", 1))
        return result

    def add_to_position(self, symbol: str, add_size: float, add_price: float):
        result = super().add_to_position(symbol, add_size, add_price)
        if self.position and self.position.get("symbol") == symbol:
            self.position["size"] = float(self.position.get("position_size", 0.0))
            self.position["scale_in_count"] = int(self.position.get("scale_in_count", 1)) + 1
        return result

    def get_position_state(self):
        state = super().get_position_state()
        if state.get("has_position"):
            state["size"] = float(state.get("position_size", 0.0))
            state["unrealized_pnl"] = float(state.get("current_pnl", state.get("pnl", 0.0)))
            state["scale_in_count"] = int(state.get("scale_in_count", 1))
        return state

    def dynamic_trailing_stop(self, symbol: str, current_price: float):
        result = super().dynamic_trailing_stop(symbol, current_price)
        if result.get("adjusted"):
            logger.info(
                "TRAILING_STOP_TRIGGERED: %s old=%.4f new=%.4f profit=%.2f%%",
                symbol,
                float(result.get("old_stop_loss", 0.0)),
                float(result.get("new_stop_loss", 0.0)),
                float(result.get("profit_pct", 0.0)),
            )
        return result
