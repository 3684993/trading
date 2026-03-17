"""
动态止损调整策略
基于15分钟趋势变化动态调整止损价格
"""

from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from core.logger import logger


class DynamicStopLoss:
    """动态止损调整策略"""
    
    def __init__(self):
        self.trend_data = {}
        self.last_adjustment_time = {}
        
    def analyze_15min_trend(self, symbol: str, current_price: float, 
                          historical_prices: list) -> Dict:
        """
        分析15分钟趋势
        
        Args:
            symbol: 交易对
            current_price: 当前价格
            historical_prices: 历史价格列表（按时间顺序）
            
        Returns:
            趋势分析结果
        """
        try:
            if len(historical_prices) < 30:  # 至少需要30个数据点
                return {
                    "trend": "neutral",
                    "strength": 0,
                    "confidence": 0,
                    "support_level": current_price * 0.99,
                    "resistance_level": current_price * 1.01
                }
            
            # 计算15分钟窗口内的趋势
            recent_prices = historical_prices[-15:]  # 最近15个数据点
            older_prices = historical_prices[-30:-15]  # 前15个数据点
            
            if len(recent_prices) < 15 or len(older_prices) < 15:
                return {
                    "trend": "neutral",
                    "strength": 0,
                    "confidence": 0,
                    "support_level": current_price * 0.99,
                    "resistance_level": current_price * 1.01
                }
            
            # 计算移动平均
            recent_avg = sum(recent_prices) / len(recent_prices)
            older_avg = sum(older_prices) / len(older_prices)
            
            # 计算趋势方向和强度
            trend_direction = "up" if recent_avg > older_avg else "down"
            trend_strength = abs((recent_avg - older_avg) / older_avg) * 100
            
            # 计算趋势置信度
            recent_volatility = max(recent_prices) - min(recent_prices)
            older_volatility = max(older_prices) - min(older_prices)
            
            # 如果近期波动性降低，趋势更可靠
            confidence = 1.0 if recent_volatility < older_volatility else 0.7
            
            # 计算支撑阻力位
            support_level = min(recent_prices) * 0.995
            resistance_level = max(recent_prices) * 1.005
            
            return {
                "trend": trend_direction,
                "strength": trend_strength,
                "confidence": confidence,
                "support_level": support_level,
                "resistance_level": resistance_level,
                "recent_avg": recent_avg,
                "older_avg": older_avg
            }
            
        except Exception as e:
            logger.error(f"Analyze 15min trend error: {e}")
            return {
                "trend": "neutral",
                "strength": 0,
                "confidence": 0,
                "support_level": current_price * 0.99,
                "resistance_level": current_price * 1.01
            }
    
    def should_adjust_stop_loss(self, symbol: str, position_info: Dict, 
                               trend_analysis: Dict) -> bool:
        """
        判断是否需要调整止损
        
        Args:
            symbol: 交易对
            position_info: 持仓信息
            trend_analysis: 趋势分析结果
            
        Returns:
            是否需要调整止损
        """
        try:
            # 检查是否达到最小调整间隔（5分钟）
            last_adjustment = self.last_adjustment_time.get(symbol)
            if last_adjustment:
                time_since_last = (datetime.now() - last_adjustment).total_seconds() / 60
                if time_since_last < 5:  # 5分钟内不重复调整
                    return False
            
            side = position_info.get("side", "long")
            current_price = position_info.get("current_price", 0)
            entry_price = position_info.get("entry_price", 0)
            stop_loss = position_info.get("stop_loss", 0)
            hold_minutes = position_info.get("hold_minutes", 0)
            
            # 持仓时间太短（<5分钟）不调整
            if hold_minutes < 5:
                return False
            
            # 检查趋势强度
            trend_strength = trend_analysis.get("strength", 0)
            trend_confidence = trend_analysis.get("confidence", 0)
            
            # 趋势强度不足或置信度低时不调整
            if trend_strength < 0.1 or trend_confidence < 0.7:
                return False
            
            # 根据持仓方向判断趋势是否有利
            trend_direction = trend_analysis.get("trend", "neutral")
            
            if side == "long":
                # 多头持仓：趋势向上有利，向下不利
                if trend_direction == "down":
                    return True  # 趋势转跌，需要调整止损
                elif trend_direction == "up" and current_price > entry_price:
                    # 趋势向上且盈利，可以收紧止损
                    return True
            else:
                # 空头持仓：趋势向下有利，向上不利
                if trend_direction == "up":
                    return True  # 趋势转涨，需要调整止损
                elif trend_direction == "down" and current_price < entry_price:
                    # 趋势向下且盈利，可以收紧止损
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Should adjust stop loss error: {e}")
            return False
    
    def calculate_new_stop_loss(self, symbol: str, position_info: Dict, 
                              trend_analysis: Dict, atr: float) -> float:
        """
        计算新的止损价格
        
        Args:
            symbol: 交易对
            position_info: 持仓信息
            trend_analysis: 趋势分析结果
            atr: 平均真实波幅
            
        Returns:
            新的止损价格
        """
        try:
            side = position_info.get("side", "long")
            current_price = position_info.get("current_price", 0)
            entry_price = position_info.get("entry_price", 0)
            current_stop_loss = position_info.get("stop_loss", 0)
            hold_minutes = position_info.get("hold_minutes", 0)
            
            trend_direction = trend_analysis.get("trend", "neutral")
            support_level = trend_analysis.get("support_level", current_price * 0.99)
            resistance_level = trend_analysis.get("resistance_level", current_price * 1.01)
            
            # 基础止损距离（基于ATR）
            base_stop_distance = atr * 1.5  # 1.5倍ATR
            
            if side == "long":
                # 多头持仓止损计算
                if trend_direction == "down":
                    # 趋势转跌，立即调整止损到支撑位或当前价格下方
                    new_stop_loss = min(support_level, current_price - base_stop_distance)
                    
                    # 确保止损不低于当前止损（保护利润）
                    if current_stop_loss > 0:
                        new_stop_loss = max(new_stop_loss, current_stop_loss)
                    
                    logger.info(f"[{symbol}] 趋势转跌，止损调整至: {new_stop_loss:.2f}")
                    
                elif trend_direction == "up" and current_price > entry_price:
                    # 趋势向上且盈利，收紧止损到盈亏平衡点上方
                    new_stop_loss = entry_price * 1.002  # 盈亏平衡点上方0.2%
                    
                    # 确保止损不低于当前止损
                    if current_stop_loss > 0:
                        new_stop_loss = max(new_stop_loss, current_stop_loss)
                    
                    logger.info(f"[{symbol}] 趋势向上且盈利，止损收紧至: {new_stop_loss:.2f}")
                    
                else:
                    # 保持当前止损或使用支撑位
                    if current_stop_loss > 0:
                        new_stop_loss = current_stop_loss
                    else:
                        new_stop_loss = support_level
            
            else:
                # 空头持仓止损计算
                if trend_direction == "up":
                    # 趋势转涨，立即调整止损到阻力位或当前价格上方
                    new_stop_loss = max(resistance_level, current_price + base_stop_distance)
                    
                    # 确保止损不高于当前止损（保护利润）
                    if current_stop_loss > 0:
                        new_stop_loss = min(new_stop_loss, current_stop_loss)
                    
                    logger.info(f"[{symbol}] 趋势转涨，止损调整至: {new_stop_loss:.2f}")
                    
                elif trend_direction == "down" and current_price < entry_price:
                    # 趋势向下且盈利，收紧止损到盈亏平衡点下方
                    new_stop_loss = entry_price * 0.998  # 盈亏平衡点下方0.2%
                    
                    # 确保止损不高于当前止损
                    if current_stop_loss > 0:
                        new_stop_loss = min(new_stop_loss, current_stop_loss)
                    
                    logger.info(f"[{symbol}] 趋势向下且盈利，止损收紧至: {new_stop_loss:.2f}")
                    
                else:
                    # 保持当前止损或使用阻力位
                    if current_stop_loss > 0:
                        new_stop_loss = current_stop_loss
                    else:
                        new_stop_loss = resistance_level
            
            # 记录调整时间
            self.last_adjustment_time[symbol] = datetime.now()
            
            return new_stop_loss
            
        except Exception as e:
            logger.error(f"Calculate new stop loss error: {e}")
            return position_info.get("stop_loss", 0)
    
    def dynamic_stop_loss_adjustment(self, symbol: str, position_info: Dict, 
                                   historical_prices: list, atr: float) -> Dict:
        """
        动态止损调整主函数
        
        Args:
            symbol: 交易对
            position_info: 持仓信息
            historical_prices: 历史价格列表
            atr: 平均真实波幅
            
        Returns:
            调整结果
        """
        try:
            current_price = position_info.get("current_price", 0)
            
            # 分析15分钟趋势
            trend_analysis = self.analyze_15min_trend(symbol, current_price, historical_prices)
            
            # 判断是否需要调整止损
            should_adjust = self.should_adjust_stop_loss(symbol, position_info, trend_analysis)
            
            if not should_adjust:
                return {
                    "adjusted": False,
                    "reason": "No adjustment needed",
                    "current_stop_loss": position_info.get("stop_loss", 0),
                    "trend_analysis": trend_analysis
                }
            
            # 计算新的止损价格
            new_stop_loss = self.calculate_new_stop_loss(symbol, position_info, trend_analysis, atr)
            
            # 确保止损调整合理
            current_stop_loss = position_info.get("stop_loss", 0)
            
            if current_stop_loss > 0:
                # 多头：新止损必须高于原止损（保护利润）
                # 空头：新止损必须低于原止损（保护利润）
                side = position_info.get("side", "long")
                
                if side == "long":
                    if new_stop_loss < current_stop_loss:
                        logger.warning(f"[{symbol}] 新止损低于原止损，保持原止损")
                        new_stop_loss = current_stop_loss
                else:
                    if new_stop_loss > current_stop_loss:
                        logger.warning(f"[{symbol}] 新止损高于原止损，保持原止损")
                        new_stop_loss = current_stop_loss
            
            return {
                "adjusted": True,
                "new_stop_loss": new_stop_loss,
                "old_stop_loss": current_stop_loss,
                "reason": "Trend-based adjustment",
                "trend_analysis": trend_analysis
            }
            
        except Exception as e:
            logger.error(f"Dynamic stop loss adjustment error: {e}")
            return {
                "adjusted": False,
                "reason": f"Error: {str(e)}",
                "current_stop_loss": position_info.get("stop_loss", 0)
            }


# 全局实例
dynamic_stop_loss = DynamicStopLoss()