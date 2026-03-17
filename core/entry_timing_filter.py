from __future__ import annotations

from typing import Dict, Optional
from datetime import datetime, timedelta

from core.logger import logger


class EntryTimingFilter:
    """Filter short-term extreme entries using 1m Bollinger/RSI/MA20."""

    def __init__(self) -> None:
        self._wait_state: Dict[str, Dict] = {}
        self._timeout_override_used: Dict[str, datetime] = {}

    def evaluate(
        self,
        symbol: str,
        trend: str,
        current_price: float,
        indicators_1m: Optional[Dict],
        trend_strength: float = 0.0,
        ai_confidence: float = 0.0,
        last_fill_time: Optional[datetime] = None,
    ) -> Dict:
        indicators_1m = indicators_1m or {}
        bb = indicators_1m.get("bollinger", {})
        upper = float(bb.get("upper", 0) or 0)
        lower = float(bb.get("lower", 0) or 0)
        ma20 = float(bb.get("middle", 0) or 0)
        rsi = float(indicators_1m.get("rsi", 50) or 50)

        bb_position = 0.5
        if upper > lower:
            bb_position = (float(current_price) - lower) / (upper - lower)

        trend_key = str(trend or "").lower()
        if trend_key in ["open_long", "long", "buy", "bullish", "trend_up"]:
            trend_key = "long"
        elif trend_key in ["open_short", "short", "sell", "bearish", "trend_down"]:
            trend_key = "short"

        state = self._wait_state.get(symbol)

        breakout_allowed = float(trend_strength or 0.0) > 0.7 or float(ai_confidence or 0.0) > 0.8
        if breakout_allowed:
            logger.info(
                "ENTRY_MODE=BREAKOUT symbol=%s bb_pos=%.3f rsi=%.2f trend_strength=%.3f ai_conf=%.3f",
                symbol,
                bb_position,
                rsi,
                float(trend_strength or 0.0),
                float(ai_confidence or 0.0),
            )
            return {
                "allow_entry": True,
                "state": "READY",
                "reason": "breakout_override",
                "entry_mode": "BREAKOUT",
                "bb_position": bb_position,
                "rsi": rsi,
                "ma20": ma20,
                "trend_strength": float(trend_strength or 0.0),
                "ai_confidence": float(ai_confidence or 0.0),
            }

        now = datetime.now()
        timeout_ready = False
        if last_fill_time:
            timeout_ready = now - last_fill_time >= timedelta(minutes=30)
        else:
            timeout_ready = True

        last_override = self._timeout_override_used.get(symbol)
        if last_override:
            if last_fill_time:
                if last_override >= last_fill_time:
                    timeout_ready = False
            else:
                # No fills yet: allow timeout override at most once per 30 minutes
                if now - last_override < timedelta(minutes=30):
                    timeout_ready = False

        # Long-side wait rule from spec
        if trend_key == "long" and bb_position > 0.85 and rsi > 65:
            self._wait_state[symbol] = {
                "status": "WAIT_PULLBACK",
                "reason": "short_term_pullback_expected",
            }
            if timeout_ready:
                self._timeout_override_used[symbol] = datetime.now()
                self._wait_state.pop(symbol, None)
                logger.info(
                    "ENTRY_MODE=TIMEOUT symbol=%s bb_pos=%.3f rsi=%.2f trend_strength=%.3f ai_conf=%.3f reason=short_term_pullback_expected",
                    symbol,
                    bb_position,
                    rsi,
                    float(trend_strength or 0.0),
                    float(ai_confidence or 0.0),
                )
                return {
                    "allow_entry": True,
                    "state": "READY",
                    "reason": "timeout_override",
                    "entry_mode": "TIMEOUT",
                    "bb_position": bb_position,
                    "rsi": rsi,
                    "ma20": ma20,
                    "trend_strength": float(trend_strength or 0.0),
                    "ai_confidence": float(ai_confidence or 0.0),
                }

            logger.info(
                "ENTRY_TIMING_WAIT symbol=%s entry_mode=PULLBACK bb_pos=%.3f rsi=%.2f trend_strength=%.2f ai_confidence=%.2f wait_reason=short_term_pullback_expected",
                symbol,
                bb_position,
                rsi,
                float(trend_strength or 0.0),
                float(ai_confidence or 0.0),
            )
            return {
                "allow_entry": False,
                "state": "WAIT_PULLBACK",
                "reason": "short_term_pullback_expected",
                "entry_mode": "PULLBACK",
                "bb_position": bb_position,
                "rsi": rsi,
                "ma20": ma20,
                "trend_strength": float(trend_strength or 0.0),
                "ai_confidence": float(ai_confidence or 0.0),
            }

        if state and state.get("status") == "WAIT_PULLBACK":
            pullback_ok = float(current_price) <= ma20 or bb_position < 0.6
            if not pullback_ok:
                if timeout_ready:
                    self._timeout_override_used[symbol] = datetime.now()
                    self._wait_state.pop(symbol, None)
                    logger.info(
                        "ENTRY_MODE=TIMEOUT symbol=%s bb_pos=%.3f rsi=%.2f trend_strength=%.3f ai_conf=%.3f reason=pullback_not_ready",
                        symbol,
                        bb_position,
                        rsi,
                        float(trend_strength or 0.0),
                        float(ai_confidence or 0.0),
                    )
                    return {
                        "allow_entry": True,
                        "state": "READY",
                        "reason": "timeout_override",
                        "entry_mode": "TIMEOUT",
                        "bb_position": bb_position,
                        "rsi": rsi,
                        "ma20": ma20,
                        "trend_strength": float(trend_strength or 0.0),
                        "ai_confidence": float(ai_confidence or 0.0),
                    }

                logger.info(
                    "ENTRY_TIMING_WAIT symbol=%s entry_mode=PULLBACK bb_pos=%.3f rsi=%.2f trend_strength=%.2f ai_confidence=%.2f wait_reason=pullback_not_ready",
                    symbol,
                    bb_position,
                    rsi,
                    float(trend_strength or 0.0),
                    float(ai_confidence or 0.0),
                )
                return {
                    "allow_entry": False,
                    "state": "WAIT_PULLBACK",
                    "reason": "pullback_not_ready",
                    "entry_mode": "PULLBACK",
                    "bb_position": bb_position,
                    "rsi": rsi,
                    "ma20": ma20,
                    "trend_strength": float(trend_strength or 0.0),
                    "ai_confidence": float(ai_confidence or 0.0),
                }
            self._wait_state.pop(symbol, None)

        return {
            "allow_entry": True,
            "state": "READY",
            "reason": "entry_timing_ok",
            "entry_mode": "NORMAL",
            "bb_position": bb_position,
            "rsi": rsi,
            "ma20": ma20,
            "trend_strength": float(trend_strength or 0.0),
            "ai_confidence": float(ai_confidence or 0.0),
        }
