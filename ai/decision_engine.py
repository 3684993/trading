import json
from typing import Dict, Optional
from core.logger import logger
from ai.llm_client import LLMClient
from agents.orderbook_agent import OrderBookAgent
from agents.liquidity_agent import LiquidityAgent
from agents.profit_optimizer import ProfitOptimizer
from agents.advanced_take_profit import advanced_take_profit
from agents.intelligent_close_decision import intelligent_close_decision


class DecisionEngine:
    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()

        self._last_decision: Dict = {}
        self._decision_history: list = []

        logger.info("DecisionEngine initialized")

    def generate_trade_decision(self, context: Dict) -> Dict:
        try:
            prompt = self._build_prompt(context)

            messages = [
                {
                    "role": "system",
                    "content": self._get_system_prompt()
                },
                {
                    "role": "user",
                    "content": prompt + " /no_think"
                }
            ]

            response = self.llm_client.chat_json(messages, temperature=0.3, max_tokens=2000)

            if response:
                validated = self._validate_decision(response, context)
                self._last_decision = validated.copy()
                self._decision_history.append({
                    "decision": validated.copy(),
                    "context": {
                        "price": context.get("price", 0),
                        "has_position": context.get("position", {}).get("has_position", False)
                    }
                })
                if len(self._decision_history) > 100:
                    self._decision_history = self._decision_history[-100:]
                return validated
            else:
                logger.warning("LLM returned no response, using default decision")
                return self._get_default_decision(context)

        except Exception as e:
            logger.error(f"Decision generation failed: {e}")
            return self._get_default_decision(context)

    def _get_system_prompt(self) -> str:
        return """You are a trading signal generator. Output ONLY valid JSON. No explanations.

MANDATORY ENTRY RANGE RULE:
- You MUST NOT output concrete order actions; only target position fields.
- You MUST use current_price, ATR and volatility in your reasoning.
- Compute entry_range using this formula exactly:
  entry_range = current_price ± max(ATR*0.5, 100)
- entry_range MUST cover current_price (low <= current_price <= high).

RULES:
1. Decide direction only: long/short/flat
2. Decide target_size only (0 means flat)
3. Always set stop_loss and take_profit
4. Respect volatility and trend context

REQUIRED FIELDS:
- current_price
- entry_range
- stop_loss
- take_profit

OUTPUT FORMAT (all keys required):
{"direction":"long|short|flat","target_size":0.02,"confidence":0.8,"current_price":70131.0,"entry_range":[70031.0,70231.0],"stop_loss":69900.0,"take_profit":70400.0}
"""

    def _build_prompt(self, context: Dict) -> str:
        price = float(context.get("price", 0) or 0)
        indicators = context.get("indicators", {})
        market_summary = context.get("market_summary", {})
        profit_optimizer = context.get("profit_optimizer", {})
        position = context.get("position", {})

        has_position = position.get("has_position", False)
        pnl_pct = position.get("current_pnl_pct", 0) if has_position else 0
        hold_minutes = position.get("hold_minutes", 0) if has_position else 0
        expected_hold = position.get("expected_hold_minutes", 60) if has_position else 60
        position_side = position.get("side", "none") if has_position else "none"

        bb_upper = indicators.get('bollinger', {}).get('upper', 0)
        bb_lower = indicators.get('bollinger', {}).get('lower', 0)
        atr = float(indicators.get('atr', 0) or 0)
        volatility = market_summary.get('volatility', 'low')

        trend = market_summary.get('trend', 'neutral')
        macd_signal = market_summary.get('macd_signal', 'neutral')

        entry_distance = max(atr * 0.5, 100.0)
        formula_low = price - entry_distance
        formula_high = price + entry_distance

        return f"""Price:{price:.2f}|Trend:{trend}|MACD:{macd_signal}|BB:[{bb_lower:.0f},{bb_upper:.0f}]|ATR:{atr:.2f}|Volatility:{volatility}|Pos:{'Y' if has_position else 'N'}|Side:{position_side}|PnL:{pnl_pct:.2f}%|Hold:{hold_minutes}min/{expected_hold}min|Entry:{profit_optimizer.get('optimal_entry',[0,0])}|SL:{profit_optimizer.get('stop_loss',0):.0f}|TP:{profit_optimizer.get('take_profit',0):.0f}

MANDATORY FORMULA:
entry_range = current_price ± max(ATR*0.5, 100)
For current data: current_price={price:.2f}, ATR={atr:.2f}, computed_range=[{formula_low:.2f}, {formula_high:.2f}] (must include current_price)

JSON:"""

    def _validate_decision(self, decision: Dict, context: Dict) -> Dict:
        indicators = context.get("indicators", {}) or {}
        context_price = float(context.get("price", 0) or 0)

        direction = str(decision.get("direction", "flat")).lower()
        if direction not in ["long", "short", "flat"]:
            direction = "flat"

        target_size = decision.get("target_size", 0.0)
        if not isinstance(target_size, (int, float)):
            target_size = 0.0
        target_size = max(0.0, float(target_size))

        current_price = decision.get("current_price", context_price)
        if not isinstance(current_price, (int, float)):
            current_price = context_price
        current_price = float(current_price)

        atr = float(indicators.get('atr', 0) or 0)
        entry_distance = max(atr * 0.5, 100.0)
        formula_low = round(current_price - entry_distance, 2)
        formula_high = round(current_price + entry_distance, 2)
        entry_range = [formula_low, formula_high]

        stop_loss = decision.get("stop_loss", 0)
        if not isinstance(stop_loss, (int, float)):
            stop_loss = 0

        take_profit = decision.get("take_profit", 0)
        if not isinstance(take_profit, (int, float)):
            take_profit = 0

        confidence = decision.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        confidence = max(0, min(1, float(confidence)))

        return {
            "direction": direction,
            "target_size": target_size,
            "current_price": current_price,
            "entry_range": entry_range,
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "confidence": confidence,
            "expected_hold_minutes": int(decision.get("expected_hold_minutes", 60) or 60)
        }

    def _get_default_decision(self, context: Dict) -> Dict:
        current_price = float(context.get("price", 0) or 0)
        atr = float((context.get("indicators", {}) or {}).get("atr", 0) or 0)
        entry_distance = max(atr * 0.5, 100.0)
        return {
            "direction": "flat",
            "target_size": 0.0,
            "current_price": current_price,
            "entry_range": [round(current_price - entry_distance, 2), round(current_price + entry_distance, 2)],
            "stop_loss": 0,
            "take_profit": 0,
            "expected_hold_minutes": 60,
            "confidence": 0.5
        }

    def format_decision_log(self, decision: Dict, symbol: str) -> str:
        try:
            return (
                f"[{symbol}] AI Decision: "
                f"direction={decision.get('direction', 'flat')} | "
                f"current_price={decision.get('current_price', 0):.2f} | "
                f"entry_range={decision.get('entry_range', [0, 0])} | "
                f"target_size={decision.get('target_size', 0.0):.4f} | "
                f"SL={decision.get('stop_loss', 0):.2f} | "
                f"TP={decision.get('take_profit', 0):.2f} | "
                f"hold={decision.get('expected_hold_minutes', 60)}min | "
                f"conf={decision.get('confidence', 0.5):.2f}"
            )
        except Exception:
            return f"[{symbol}] AI Decision generated"

    def get_last_decision(self) -> Dict:
        return self._last_decision.copy() if self._last_decision else {}

    def get_decision_history(self, limit: int = 10) -> list:
        return self._decision_history[-limit:] if self._decision_history else []
