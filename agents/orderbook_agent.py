from typing import Dict
from core.logger import logger


class OrderBookAgent:
    def __init__(self):
        logger.info("OrderBookAgent initialized")
    
    def analyze(self, indicators: Dict, market_summary: Dict) -> Dict:
        try:
            price = indicators.get("price", {}).get("close", 0)
            momentum = indicators.get("momentum", {})
            atr = indicators.get("atr", 0)
            
            spread = self._estimate_spread(indicators)
            imbalance = self._estimate_imbalance(momentum, market_summary)
            buy_pressure = self._detect_buy_pressure(momentum, market_summary)
            bid_depth = self._estimate_bid_depth(atr, price)
            ask_depth = self._estimate_ask_depth(atr, price)
            depth_ratio = self._calculate_depth_ratio(bid_depth, ask_depth)
            
            return {
                "spread": spread,
                "imbalance": imbalance,
                "buy_pressure": buy_pressure,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "depth_ratio": depth_ratio
            }
        except Exception as e:
            logger.error(f"OrderBook analysis error: {e}")
            return {
                "spread": 0.0,
                "imbalance": 0.0,
                "buy_pressure": False,
                "bid_depth": 0.0,
                "ask_depth": 0.0,
                "depth_ratio": 0.0
            }
    
    def _estimate_spread(self, indicators: Dict) -> float:
        try:
            atr = indicators.get("atr", 0)
            price = indicators.get("price", {}).get("close", 1)
            
            if price > 0:
                spread = (atr / price) * 0.1
                return round(spread, 6)
            return 0.0
        except Exception:
            return 0.0
    
    def _estimate_imbalance(self, momentum: Dict, market_summary: Dict) -> float:
        try:
            trend = market_summary.get("trend", "neutral")
            price_change_1m = momentum.get("price_change_1m", 0)
            
            if trend == "bullish":
                imbalance = 0.3 + (price_change_1m * 10)
            elif trend == "bearish":
                imbalance = -0.3 + (price_change_1m * 10)
            else:
                imbalance = price_change_1m * 10
            
            return round(max(-1.0, min(1.0, imbalance)), 2)
        except Exception:
            return 0.0
    
    def _detect_buy_pressure(self, momentum: Dict, market_summary: Dict) -> bool:
        try:
            trend = market_summary.get("trend", "neutral")
            momentum_bias = market_summary.get("momentum", "neutral")
            volume_change = momentum.get("volume_change", 0)
            
            if trend == "bullish" and momentum_bias == "strengthening":
                return True
            if trend == "bullish" and volume_change > 0:
                return True
            if momentum_bias == "strengthening" and volume_change > 0.1:
                return True
            
            return False
        except Exception:
            return False
    
    def _estimate_bid_depth(self, atr: float, price: float) -> float:
        try:
            if price <= 0 or atr <= 0:
                return 1.0
            
            depth = (atr / price) * 100
            return round(depth, 4)
        except Exception:
            return 1.0
    
    def _estimate_ask_depth(self, atr: float, price: float) -> float:
        try:
            if price <= 0 or atr <= 0:
                return 1.0
            
            depth = (atr / price) * 100
            return round(depth, 4)
        except Exception:
            return 1.0
    
    def _calculate_depth_ratio(self, bid_depth: float, ask_depth: float) -> float:
        try:
            if ask_depth <= 0:
                return 1.0
            
            ratio = bid_depth / ask_depth
            return round(ratio, 2)
        except Exception:
            return 1.0
    
    def format_log(self, result: Dict, symbol: str) -> str:
        try:
            return (
                f"[{symbol}] OrderBook: "
                f"spread={result.get('spread', 0):.6f} | "
                f"imbalance={result.get('imbalance', 0):.2f} | "
                f"buy_pressure={result.get('buy_pressure', False)} | "
                f"depth_ratio={result.get('depth_ratio', 0):.2f}"
            )
        except Exception:
            return f"[{symbol}] OrderBook analyzed"
