from typing import Dict
from core.logger import logger


class FeatureCompressor:
    def __init__(self):
        logger.info("FeatureCompressor initialized")
    
    def compress(self, indicators: Dict) -> Dict:
        try:
            ema = indicators.get("ema", {})
            macd = indicators.get("macd", {})
            momentum = indicators.get("momentum", {})
            atr = indicators.get("atr", 0)
            price = indicators.get("price", {}).get("close", 0)
            
            trend_direction = self._get_trend_direction(ema, macd)
            momentum_bias = self._get_momentum_bias(momentum, macd)
            volatility_level = self._get_volatility_level(atr, price)
            
            return {
                "market_summary": {
                    "trend": trend_direction,
                    "momentum": momentum_bias,
                    "volatility": volatility_level,
                    "macd_signal": self._get_macd_signal(macd)
                }
            }
        except Exception as e:
            logger.error(f"Feature compression error: {e}")
            return {
                "market_summary": {
                    "trend": "neutral",
                    "momentum": "neutral",
                    "volatility": "low",
                    "macd_signal": "neutral"
                }
            }
    
    def _get_macd_signal(self, macd: Dict) -> str:
        try:
            macd_line = macd.get("macd", 0)
            signal_line = macd.get("signal", 0)
            histogram = macd.get("hist", 0)
            
            if macd_line > signal_line and histogram > 0:
                return "bullish"
            elif macd_line < signal_line and histogram < 0:
                return "bearish"
            elif macd_line > 0 and histogram < 0:
                return "weakening_bullish"
            elif macd_line < 0 and histogram > 0:
                return "weakening_bearish"
            else:
                return "neutral"
        except Exception:
            return "neutral"
    
    def _get_trend_direction(self, ema: Dict, macd: Dict = None) -> str:
        try:
            ema9 = ema.get("ema9", 0)
            ema21 = ema.get("ema21", 0)
            
            ema_trend = "neutral"
            if ema9 > ema21:
                ema_trend = "bullish"
            elif ema9 < ema21:
                ema_trend = "bearish"
            
            if macd:
                macd_line = macd.get("macd", 0)
                signal_line = macd.get("signal", 0)
                histogram = macd.get("hist", 0)
                
                macd_trend = "neutral"
                if macd_line > signal_line and histogram > 0:
                    macd_trend = "bullish"
                elif macd_line < signal_line and histogram < 0:
                    macd_trend = "bearish"
                elif histogram > 0:
                    macd_trend = "bullish_weak"
                elif histogram < 0:
                    macd_trend = "bearish_weak"
                
                if ema_trend == macd_trend:
                    return ema_trend
                elif macd_trend in ["bullish", "bearish"]:
                    return macd_trend
                elif ema_trend in ["bullish", "bearish"]:
                    return ema_trend
                else:
                    return macd_trend if macd_trend != "neutral" else ema_trend
            
            return ema_trend
        except Exception:
            return "neutral"
    
    def _get_momentum_bias(self, momentum: Dict, macd: Dict = None) -> str:
        try:
            price_change_1m = momentum.get("price_change_1m", 0)
            price_change_5m = momentum.get("price_change_5m", 0)
            
            price_momentum = "neutral"
            if price_change_1m > price_change_5m:
                price_momentum = "strengthening"
            elif price_change_1m < price_change_5m:
                price_momentum = "weakening"
            
            if macd:
                histogram = macd.get("hist", 0)
                macd_line = macd.get("macd", 0)
                
                if histogram > 0 and macd_line > 0:
                    return "strengthening"
                elif histogram < 0 and macd_line < 0:
                    return "weakening"
                elif histogram < 0 and macd_line > 0:
                    return "weakening"
                elif histogram > 0 and macd_line < 0:
                    return "strengthening"
            
            return price_momentum
        except Exception:
            return "neutral"
    
    def _get_volatility_level(self, atr: float, price: float) -> str:
        try:
            if price == 0:
                return "low"
            
            volatility_ratio = atr / price
            
            if volatility_ratio > 0.006:
                return "high"
            elif volatility_ratio > 0.003:
                return "medium"
            else:
                return "low"
        except Exception:
            return "low"
    
    def format_summary_log(self, summary: Dict, symbol: str, timeframe: str) -> str:
        try:
            market_summary = summary.get("market_summary", {})
            return (
                f"[{symbol}][{timeframe}] "
                f"Trend={market_summary.get('trend', 'neutral')} | "
                f"Momentum={market_summary.get('momentum', 'neutral')} | "
                f"Volatility={market_summary.get('volatility', 'low')} | "
                f"MACD={market_summary.get('macd_signal', 'neutral')}"
            )
        except Exception:
            return f"[{symbol}][{timeframe}] Summary generated"
