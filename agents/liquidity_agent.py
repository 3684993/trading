from typing import Dict
from core.logger import logger


class LiquidityAgent:
    def __init__(self):
        logger.info("LiquidityAgent initialized")
    
    def analyze(self, indicators: Dict, orderbook: Dict, order_size: float = 0.01) -> Dict:
        try:
            liquidity_level = self._assess_liquidity(indicators, orderbook)
            slippage_risk = self._calculate_slippage_risk(indicators, orderbook)
            expected_slippage = self._calculate_expected_slippage(orderbook, order_size)
            
            return {
                "liquidity_level": liquidity_level,
                "slippage_risk": slippage_risk,
                "expected_slippage": expected_slippage
            }
        except Exception as e:
            logger.error(f"Liquidity analysis error: {e}")
            return {
                "liquidity_level": "medium",
                "slippage_risk": 0.0,
                "expected_slippage": 0.0
            }
    
    def _assess_liquidity(self, indicators: Dict, orderbook: Dict) -> str:
        try:
            volume_change = indicators.get("momentum", {}).get("volume_change", 0)
            spread = orderbook.get("spread", 0)
            depth_ratio = orderbook.get("depth_ratio", 1)
            
            if volume_change > 0.2 and spread < 0.0001 and depth_ratio > 0.8:
                return "high"
            elif volume_change < -0.2 or spread > 0.001 or depth_ratio < 0.5:
                return "low"
            else:
                return "medium"
        except Exception:
            return "medium"
    
    def _calculate_slippage_risk(self, indicators: Dict, orderbook: Dict) -> float:
        try:
            spread = orderbook.get("spread", 0)
            imbalance = abs(orderbook.get("imbalance", 0))
            volatility = indicators.get("atr", 0) / max(indicators.get("price", {}).get("close", 1), 1)
            
            slippage = (spread * 100) + (imbalance * 0.01) + (volatility * 0.1)
            
            return round(min(1.0, slippage), 4)
        except Exception:
            return 0.0
    
    def _calculate_expected_slippage(self, orderbook: Dict, order_size: float) -> float:
        try:
            bid_depth = orderbook.get("bid_depth", 1)
            ask_depth = orderbook.get("ask_depth", 1)
            avg_depth = (bid_depth + ask_depth) / 2
            
            if avg_depth <= 0:
                return 0.0
            
            slippage = order_size / avg_depth
            
            return round(min(0.1, slippage), 4)
        except Exception:
            return 0.0
    
    def format_log(self, result: Dict, symbol: str) -> str:
        try:
            return (
                f"[{symbol}] Liquidity: "
                f"level={result.get('liquidity_level', 'medium')} | "
                f"slippage_risk={result.get('slippage_risk', 0):.4f} | "
                f"expected_slippage={result.get('expected_slippage', 0):.4f}"
            )
        except Exception:
            return f"[{symbol}] Liquidity analyzed"
