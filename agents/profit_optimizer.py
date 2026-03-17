from typing import Dict, List
from core.logger import logger
from agents.advanced_take_profit import advanced_take_profit


class ProfitOptimizer:
    def __init__(self):
        logger.info("ProfitOptimizer initialized")
    
    def optimize(self, indicators: Dict, market_summary: Dict, market_regime: Dict, 
                 position_info: Dict = None) -> Dict:
        try:
            price = indicators.get("price", {}).get("close", 0)
            atr = indicators.get("atr", 0)
            
            optimal_entry = self._calculate_optimal_entry(price, atr, market_summary)
            stop_loss = self._calculate_stop_loss(price, atr, market_summary)
            
            # 使用高级止盈策略优化止盈价格
            if position_info and position_info.get("has_position", False):
                # 有持仓时使用高级止盈策略
                take_profit = advanced_take_profit.optimize_take_profit(
                    price, indicators, position_info, market_summary
                )
            else:
                # 无持仓时使用基础止盈策略
                take_profit = self._calculate_take_profit(price, atr, market_summary)
            
            scale_levels = self._calculate_scale_levels(price, atr, market_regime)
            max_profit_target = self._calculate_profit_target(price, atr, market_summary)
            
            return {
                "optimal_entry": optimal_entry,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "scale_levels": scale_levels,
                "max_profit_target": max_profit_target
            }
        except Exception as e:
            logger.error(f"Profit optimization error: {e}")
            return {
                "optimal_entry": [0, 0],
                "stop_loss": 0,
                "take_profit": 0,
                "scale_levels": [],
                "max_profit_target": 0
            }
    
    def _calculate_optimal_entry(self, price: float, atr: float, market_summary: Dict) -> List[float]:
        try:
            trend = market_summary.get("trend", "neutral")
            
            if trend == "bullish":
                entry_low = price - (atr * 0.3)
                entry_high = price - (atr * 0.1)
            elif trend == "bearish":
                entry_low = price + (atr * 0.1)
                entry_high = price + (atr * 0.3)
            else:
                entry_low = price - (atr * 0.2)
                entry_high = price + (atr * 0.2)
            
            return [round(entry_low, 2), round(entry_high, 2)]
        except Exception:
            return [round(price * 0.99, 2), round(price * 1.01, 2)]
    
    def _calculate_stop_loss(self, price: float, atr: float, market_summary: Dict) -> float:
        try:
            trend = market_summary.get("trend", "neutral")
            volatility = market_summary.get("volatility", "low")
            
            if volatility == "high":
                sl_multiplier = 2.0
            elif volatility == "medium":
                sl_multiplier = 1.5
            else:
                sl_multiplier = 1.0
            
            if trend == "bullish":
                stop_loss = price - (atr * sl_multiplier)
            elif trend == "bearish":
                stop_loss = price + (atr * sl_multiplier)
            else:
                stop_loss = price - (atr * sl_multiplier)
            
            return round(stop_loss, 2)
        except Exception:
            return round(price * 0.98, 2)
    
    def _calculate_take_profit(self, price: float, atr: float, market_summary: Dict) -> float:
        try:
            trend = market_summary.get("trend", "neutral")
            momentum = market_summary.get("momentum", "neutral")
            
            if trend == "bullish" and momentum == "strengthening":
                tp_multiplier = 2.5
            elif trend == "bullish":
                tp_multiplier = 2.0
            elif trend == "bearish" and momentum == "weakening":
                tp_multiplier = 2.5
            elif trend == "bearish":
                tp_multiplier = 2.0
            else:
                tp_multiplier = 1.5
            
            if trend == "bullish":
                take_profit = price + (atr * tp_multiplier)
            elif trend == "bearish":
                take_profit = price - (atr * tp_multiplier)
            else:
                take_profit = price + (atr * tp_multiplier)
            
            return round(take_profit, 2)
        except Exception:
            return round(price * 1.02, 2)
    
    def _calculate_scale_levels(self, price: float, atr: float, market_regime: Dict) -> List[float]:
        try:
            state = market_regime.get("state", "range")
            
            if state == "volatile":
                levels = [
                    round(price - atr * 0.5, 2),
                    round(price - atr * 1.0, 2),
                    round(price - atr * 1.5, 2)
                ]
            elif state == "trend_up":
                levels = [
                    round(price - atr * 0.3, 2),
                    round(price - atr * 0.6, 2)
                ]
            elif state == "trend_down":
                levels = [
                    round(price + atr * 0.3, 2),
                    round(price + atr * 0.6, 2)
                ]
            else:
                levels = [
                    round(price - atr * 0.2, 2),
                    round(price + atr * 0.2, 2)
                ]
            
            return levels
        except Exception:
            return [round(price, 2)]
    
    def _calculate_profit_target(self, price: float, atr: float, market_summary: Dict) -> float:
        try:
            trend = market_summary.get("trend", "neutral")
            momentum = market_summary.get("momentum", "neutral")
            
            if trend == "bullish" and momentum == "strengthening":
                target = price + (atr * 3)
            elif trend == "bullish":
                target = price + (atr * 2)
            elif trend == "bearish" and momentum == "weakening":
                target = price - (atr * 3)
            elif trend == "bearish":
                target = price - (atr * 2)
            else:
                target = price + (atr * 1.5)
            
            return round(target, 2)
        except Exception:
            return round(price * 1.02, 2)
    
    def format_log(self, result: Dict, symbol: str) -> str:
        try:
            return (
                f"[{symbol}] ProfitOptimizer: "
                f"entry={result.get('optimal_entry', [0, 0])} | "
                f"SL={result.get('stop_loss', 0):.2f} | "
                f"TP={result.get('take_profit', 0):.2f} | "
                f"target={result.get('max_profit_target', 0):.2f}"
            )
        except Exception:
            return f"[{symbol}] ProfitOptimizer analyzed"
