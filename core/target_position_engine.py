from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict

from core.logger import logger
from config.settings import settings


class TargetPositionEngine:
    """AI目标仓位引擎：AI仅输出目标仓位，系统负责转为执行动作。"""

    def __init__(self):
        self.last_position_time = {}
        self.base_confidence_threshold = 0.55
        self.signal_confirmations = {}
        self.required_confirmations = int(settings.PARAMS.get("signal_confirmations", 3))

    def update_target_position(self, symbol: str, ai_decision: Dict, position_state: Dict, market_summary: Dict) -> Dict:
        direction = str(ai_decision.get("direction", "flat")).lower()
        target_size = float(ai_decision.get("target_size", 0.0) or 0.0)
        confidence = float(ai_decision.get("confidence", 0.0) or 0.0)

        if position_state.get("has_position"):
            self.last_position_time[symbol] = datetime.now()

        # 30分钟无持仓：降低开仓门槛
        threshold = self._get_confidence_threshold(symbol)
        if confidence < threshold and direction != "flat":
            logger.info(f"TARGET_POSITION_UPDATE: {symbol} confidence {confidence:.2f} < threshold {threshold:.2f}, hold")
            direction = "flat"
            target_size = 0.0

        current_side = position_state.get("side", "")
        has_position = bool(position_state.get("has_position"))

        # 连续3次相同信号才允许开仓
        if direction in ["long", "short"] and not has_position:
            key = (symbol, direction)
            prev = self.signal_confirmations.get(symbol, {"direction": None, "count": 0})
            if prev.get("direction") == direction:
                prev["count"] += 1
            else:
                prev = {"direction": direction, "count": 1}
            self.signal_confirmations[symbol] = prev

            if prev["count"] < self.required_confirmations:
                logger.info(
                    f"TARGET_POSITION_UPDATE: {symbol} signal confirm {prev['count']}/{self.required_confirmations}, hold"
                )
                direction = "flat"
                target_size = 0.0
        elif direction == "flat":
            self.signal_confirmations[symbol] = {"direction": "flat", "count": 0}

        if direction == "flat" or target_size <= 0:
            action = "close_position" if has_position else "hold"
        elif not has_position:
            action = "open_long" if direction == "long" else "open_short"
        else:
            if (direction == "long" and current_side == "long") or (direction == "short" and current_side == "short"):
                action = "add_position" if target_size > float(position_state.get("position_size", 0.0)) else "hold"
            else:
                action = "close_position"

        result = {
            "action": action,
            "direction": direction,
            "target_size": target_size,
            "size": [target_size, target_size],
            "current_price": float(ai_decision.get("current_price", 0.0) or 0.0),
            "entry_range": ai_decision.get("entry_range", [0, 0]),
            "stop_loss": float(ai_decision.get("stop_loss", 0.0) or 0.0),
            "take_profit": float(ai_decision.get("take_profit", 0.0) or 0.0),
            "confidence": confidence,
            "expected_hold_minutes": int(ai_decision.get("expected_hold_minutes", 60) or 60),
            "trend": market_summary.get("trend", "neutral"),
        }
        logger.info(f"TARGET_POSITION_UPDATE: {symbol} dir={direction} target={target_size:.4f} action={action}")
        return result

    def _get_confidence_threshold(self, symbol: str) -> float:
        last = self.last_position_time.get(symbol)
        if not last:
            return self.base_confidence_threshold - 0.1
        if datetime.now() - last > timedelta(minutes=30):
            return max(0.35, self.base_confidence_threshold - 0.15)
        return self.base_confidence_threshold
