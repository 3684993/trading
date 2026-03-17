from __future__ import annotations

from typing import Dict, Optional

from core.logger import logger


class RiskEngine:
    """缁熶竴椋庨櫓寮曟搸锛氭鎹熴€佹鐩堛€佸姩鎬佹鐩堛€佽秼鍔垮弽杞€€鍑恒€?"""

    def __init__(self, position_manager, base_risk_manager=None):
        self.position_manager = position_manager
        self.base_risk_manager = base_risk_manager

    def evaluate(
        self,
        symbol: str,
        position_state: Dict,
        current_price: float,
        trend_change: bool = False,
        market_summary: Optional[Dict] = None,
        indicators: Optional[Dict] = None,
    ) -> Dict:
        if not position_state.get("has_position"):
            return {"action": "hold", "reason": "NO_POSITION"}

        # 1) 本地止损止盈
        trend_direction = self._resolve_trend_direction(market_summary, indicators)
        sltp = self.position_manager.check_local_sl_tp(
            symbol, current_price, trend_direction=trend_direction, trend_strength=0.0
        )
        if sltp.get("triggered"):
            logger.warning("[RISK_TRIGGER] symbol=%s reason=%s", symbol, sltp.get("trigger_type", "SLTP"))
            return {"action": "force_close", "reason": sltp.get("trigger_type", "SLTP")}

        # 2) 趋势反转风险判断（过滤震荡/假反转）
        if trend_change:
            regime = indicators.get("market_regime", {}) if isinstance(indicators, dict) else {}
            state = str(regime.get("state", "") or "").lower()
            strength = float(regime.get("trend_strength", 0) or 0)
            if state in ["range", "neutral", "unknown"] or strength < 0.0015:
                logger.info(
                    "[RISK_TRIGGER] symbol=%s reason=FALSE_REVERSAL_BLOCKED state=%s strength=%.4f",
                    symbol,
                    state,
                    strength,
                )
            else:
                logger.warning("[RISK_TRIGGER] symbol=%s reason=TREND_REVERSE_EXIT", symbol)
                return {"action": "force_close", "reason": "TREND_REVERSE_EXIT"}

        # 3) 动态止盈/追踪止损
        trailing = self.position_manager.dynamic_trailing_stop(symbol, current_price)
        if trailing.get("adjusted"):
            logger.info(
                "TRAILING_STOP_TRIGGERED: %s old=%.4f new=%.4f", symbol,
                float(trailing.get("old_stop_loss", 0.0)),
                float(trailing.get("new_stop_loss", 0.0)),
            )

        # 4) 叠加基础风控规则
        if self.base_risk_manager:
            base = self.base_risk_manager.check_risk(position_state)
            if base.get("action") == "force_close":
                logger.warning("[RISK_TRIGGER] symbol=%s reason=BASE_RISK_ENGINE", symbol)
                return {"action": "force_close", "reason": "BASE_RISK_ENGINE"}

        return {"action": "hold", "reason": "RISK_OK"}

    @staticmethod
    def _resolve_trend_direction(market_summary: Optional[Dict], indicators: Optional[Dict]) -> str:
        if isinstance(market_summary, dict):
            trend = str(market_summary.get("trend", "") or "").lower()
            if trend:
                return trend
        if isinstance(indicators, dict):
            regime = indicators.get("market_regime", {}) if isinstance(indicators.get("market_regime", {}), dict) else {}
            state = str(regime.get("state", "") or "").lower()
            if state:
                return state
        return ""
