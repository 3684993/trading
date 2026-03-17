import numpy as np
import pandas as pd
from typing import Dict, Optional
from core.logger import logger


class IndicatorEngine:
    def __init__(self):
        self.min_periods = {
            "macd": 26,
            "bollinger": 20,
            "rsi": 14,
            "atr": 14,
            "ema9": 9,
            "ema21": 21,
            "vwap": 1
        }
        logger.info("IndicatorEngine initialized")
    
    def calculate_indicators(self, df: pd.DataFrame) -> Dict:
        if df is None or len(df) == 0:
            logger.warning("Empty DataFrame provided to IndicatorEngine")
            return self._get_empty_result()
        
        try:
            df = df.copy()
            
            result = {
                "price": {
                    "close": float(df["close"].iloc[-1])
                },
                "macd": self._calculate_macd(df),
                "bollinger": self._calculate_bollinger(df),
                "rsi": self._calculate_rsi(df),
                "atr": self._calculate_atr(df),
                "ema": self._calculate_ema(df),
                "vwap": self._calculate_vwap(df),
                "momentum": self._calculate_momentum_features(df),
                "market_regime": self._detect_market_regime(df)
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return self._get_empty_result()
    
    def _get_empty_result(self) -> Dict:
        return {
            "price": {"close": 0.0},
            "macd": {"macd": 0.0, "signal": 0.0, "hist": 0.0},
            "bollinger": {"upper": 0.0, "middle": 0.0, "lower": 0.0},
            "rsi": 0.0,
            "atr": 0.0,
            "ema": {"ema9": 0.0, "ema21": 0.0},
            "vwap": 0.0,
            "momentum": {
                "price_change_1m": 0.0,
                "price_change_5m": 0.0,
                "price_change_15m": 0.0,
                "volume_change": 0.0,
                "momentum_strength": 0.0,
                "trend_acceleration": 0.0
            },
            "market_regime": {
                "state": "unknown",
                "trend_strength": 0.0
            }
        }
    
    def _calculate_macd(self, df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
        try:
            ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
            ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
            macd_line = ema_fast - ema_slow
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()
            histogram = macd_line - signal_line
            
            return {
                "macd": float(macd_line.iloc[-1]),
                "signal": float(signal_line.iloc[-1]),
                "hist": float(histogram.iloc[-1])
            }
        except Exception as e:
            logger.error(f"MACD calculation error: {e}")
            return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    
    def _calculate_bollinger(self, df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> Dict:
        try:
            middle = df["close"].rolling(window=period).mean()
            std = df["close"].rolling(window=period).std()
            upper = middle + (std * std_dev)
            lower = middle - (std * std_dev)
            
            return {
                "upper": float(upper.iloc[-1]),
                "middle": float(middle.iloc[-1]),
                "lower": float(lower.iloc[-1])
            }
        except Exception as e:
            logger.error(f"Bollinger Bands calculation error: {e}")
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}
    
    def _calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            
            avg_gain = gain.rolling(window=period, min_periods=period).mean()
            avg_loss = loss.rolling(window=period, min_periods=period).mean()
            
            rs = avg_gain / avg_loss.replace(0, np.inf)
            rsi = 100.0 - (100.0 / (1.0 + rs))
            
            rsi = rsi.replace([np.inf, -np.inf], 50.0)
            rsi = rsi.fillna(50.0)
            
            return float(rsi.iloc[-1])
        except Exception as e:
            logger.error(f"RSI calculation error: {e}")
            return 0.0
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            high = df["high"]
            low = df["low"]
            close = df["close"]
            
            prev_close = close.shift(1)
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = true_range.rolling(window=period, min_periods=period).mean()
            
            return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
        except Exception as e:
            logger.error(f"ATR calculation error: {e}")
            return 0.0
    
    def _calculate_ema(self, df: pd.DataFrame) -> Dict:
        try:
            ema9 = df["close"].ewm(span=9, adjust=False).mean()
            ema21 = df["close"].ewm(span=21, adjust=False).mean()
            
            return {
                "ema9": float(ema9.iloc[-1]),
                "ema21": float(ema21.iloc[-1])
            }
        except Exception as e:
            logger.error(f"EMA calculation error: {e}")
            return {"ema9": 0.0, "ema21": 0.0}
    
    def _calculate_vwap(self, df: pd.DataFrame) -> float:
        try:
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            vwap = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()
            
            return float(vwap.iloc[-1])
        except Exception as e:
            logger.error(f"VWAP calculation error: {e}")
            return 0.0
    
    def _calculate_momentum_features(self, df: pd.DataFrame) -> Dict:
        try:
            close = df["close"]
            volume = df["volume"]
            
            price_change_1m = 0.0
            price_change_5m = 0.0
            price_change_15m = 0.0
            volume_change = 0.0
            
            if len(close) >= 2:
                price_change_1m = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            
            if len(close) >= 6:
                price_change_5m = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6]
            
            if len(close) >= 16:
                price_change_15m = (close.iloc[-1] - close.iloc[-16]) / close.iloc[-16]
            
            if len(volume) >= 5:
                volume_change = (volume.iloc[-1] - volume.iloc[-5]) / volume.iloc[-5]
            
            momentum_strength = abs(price_change_5m)
            
            trend_acceleration = price_change_1m - price_change_5m
            
            return {
                "price_change_1m": float(price_change_1m),
                "price_change_5m": float(price_change_5m),
                "price_change_15m": float(price_change_15m),
                "volume_change": float(volume_change),
                "momentum_strength": float(momentum_strength),
                "trend_acceleration": float(trend_acceleration)
            }
        except Exception as e:
            logger.error(f"Momentum features calculation error: {e}")
            return {
                "price_change_1m": 0.0,
                "price_change_5m": 0.0,
                "price_change_15m": 0.0,
                "volume_change": 0.0,
                "momentum_strength": 0.0,
                "trend_acceleration": 0.0
            }
    
    def _detect_market_regime(self, df: pd.DataFrame) -> Dict:
        try:
            ema_result = self._calculate_ema(df)
            atr = self._calculate_atr(df)
            price = df["close"].iloc[-1]
            
            ema9 = ema_result["ema9"]
            ema21 = ema_result["ema21"]
            
            ema9_series = df["close"].ewm(span=9, adjust=False).mean()
            
            if len(ema9_series) >= 2:
                ema9_slope = ema9_series.iloc[-1] - ema9_series.iloc[-2]
            else:
                ema9_slope = 0.0
            
            market_regime = "unknown"
            
            if atr / price > 0.005:
                market_regime = "volatile"
            elif ema9 > ema21 and ema9_slope > 0:
                market_regime = "trend_up"
            elif ema9 < ema21 and ema9_slope < 0:
                market_regime = "trend_down"
            elif abs(ema9 - ema21) / price < 0.001:
                market_regime = "range"
            else:
                market_regime = "range"
            
            trend_strength = abs(ema9 - ema21) / price
            
            return {
                "state": market_regime,
                "trend_strength": float(trend_strength)
            }
        except Exception as e:
            logger.error(f"Market regime detection error: {e}")
            return {
                "state": "unknown",
                "trend_strength": 0.0
            }
    
    def format_indicator_log(self, indicators: Dict, symbol: str, timeframe: str) -> str:
        try:
            rsi = indicators.get("rsi", 0)
            macd = indicators.get("macd", {}).get("macd", 0)
            
            return f"[{symbol}][{timeframe}] RSI={rsi:.1f} MACD={macd:.2f}"
        except Exception:
            return f"[{symbol}][{timeframe}] Indicators calculated"
    
    def get_enhanced_indicator_log(self, indicators: Dict, symbol: str, timeframe: str) -> str:
        try:
            rsi = indicators.get("rsi", 0)
            macd = indicators.get("macd", {}).get("macd", 0)
            regime = indicators.get("market_regime", {})
            momentum = indicators.get("momentum", {})
            
            regime_state = regime.get("state", "unknown")
            trend_strength = regime.get("trend_strength", 0)
            
            price_change_1m = momentum.get("price_change_1m", 0)
            price_change_5m = momentum.get("price_change_5m", 0)
            
            return (
                f"[{symbol}][{timeframe}] RSI={rsi:.1f} MACD={macd:.2f} | "
                f"Regime={regime_state} Strength={trend_strength:.4f} | "
                f"Momentum 1m={price_change_1m:.3f} 5m={price_change_5m:.3f}"
            )
        except Exception:
            return f"[{symbol}][{timeframe}] Indicators calculated"
    
    def get_full_indicator_log(self, indicators: Dict, symbol: str, timeframe: str) -> str:
        try:
            price = indicators.get("price", {}).get("close", 0)
            rsi = indicators.get("rsi", 0)
            macd = indicators.get("macd", {})
            bb = indicators.get("bollinger", {})
            atr = indicators.get("atr", 0)
            ema = indicators.get("ema", {})
            vwap = indicators.get("vwap", 0)
            momentum = indicators.get("momentum", {})
            regime = indicators.get("market_regime", {})
            
            return (
                f"[{symbol}][{timeframe}] "
                f"Price={price:.2f} | "
                f"RSI={rsi:.1f} | "
                f"MACD={macd.get('macd', 0):.2f} Signal={macd.get('signal', 0):.2f} | "
                f"BB_Upper={bb.get('upper', 0):.2f} BB_Lower={bb.get('lower', 0):.2f} | "
                f"ATR={atr:.2f} | "
                f"EMA9={ema.get('ema9', 0):.2f} EMA21={ema.get('ema21', 0):.2f} | "
                f"VWAP={vwap:.2f} | "
                f"Regime={regime.get('state', 'unknown')} Strength={regime.get('trend_strength', 0):.4f} | "
                f"Momentum 1m={momentum.get('price_change_1m', 0):.3f} 5m={momentum.get('price_change_5m', 0):.3f}"
            )
        except Exception:
            return f"[{symbol}][{timeframe}] Indicators calculated"
