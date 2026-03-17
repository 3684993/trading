"""
高级止盈策略优化
基于持仓时间和布林线边界优化止盈价格设置
"""

from typing import Dict, Optional, Tuple
from core.logger import logger


class AdvancedTakeProfit:
    """高级止盈策略优化"""
    
    def __init__(self):
        self.bb_timeframes = ["15m", "1h", "4h"]  # 多时间周期布林线分析
        
    def optimize_take_profit(self, price: float, indicators: Dict, 
                           position_info: Dict, market_summary: Dict) -> float:
        """
        优化止盈价格设置
        
        Args:
            price: 当前价格
            indicators: 技术指标
            position_info: 持仓信息
            market_summary: 市场概况
            
        Returns:
            优化的止盈价格
        """
        try:
            side = position_info.get("side", "long")
            hold_minutes = position_info.get("hold_minutes", 0)
            pnl_pct = position_info.get("current_pnl_pct", 0)
            
            # 获取布林线数据
            bb_data = indicators.get("bollinger", {})
            bb_upper = bb_data.get("upper", 0)
            bb_lower = bb_data.get("lower", 0)
            bb_middle = bb_data.get("middle", 0)
            
            # 基础止盈计算（基于ATR）
            atr = indicators.get("atr", price * 0.01)
            base_tp = self._calculate_base_take_profit(price, atr, side, market_summary)
            
            # 基于布林线边界优化止盈
            bb_optimized_tp = self._bollinger_optimized_take_profit(
                price, bb_upper, bb_lower, bb_middle, side, hold_minutes
            )
            
            # 基于持仓时间优化止盈
            time_optimized_tp = self._time_based_take_profit(
                price, pnl_pct, hold_minutes, side, market_summary
            )
            
            # 综合选择最优止盈价格
            final_tp = self._select_best_take_profit(
                base_tp, bb_optimized_tp, time_optimized_tp, 
                side, hold_minutes, pnl_pct
            )
            
            logger.info(f"止盈优化: 基础={base_tp:.2f}, 布林线={bb_optimized_tp:.2f}, "
                      f"时间={time_optimized_tp:.2f}, 最终={final_tp:.2f}")
            
            return final_tp
            
        except Exception as e:
            logger.error(f"Optimize take profit error: {e}")
            # 回退到基础止盈计算
            atr = indicators.get("atr", price * 0.01)
            return self._calculate_base_take_profit(price, atr, side, market_summary)
    
    def _calculate_base_take_profit(self, price: float, atr: float, 
                                  side: str, market_summary: Dict) -> float:
        """基础止盈计算 - 优化为超短线策略"""
        try:
            trend = market_summary.get("trend", "neutral")
            momentum = market_summary.get("momentum", "neutral")
            
            # 超短线策略：大幅降低止盈倍数
            # 对于超短线，目标收益应该更现实（0.3%-1.0%）
            if trend == "bullish" and momentum == "strengthening":
                tp_multiplier = 0.5  # 降低到 0.5 倍 ATR
            elif trend == "bullish":
                tp_multiplier = 0.4
            elif trend == "bearish" and momentum == "weakening":
                tp_multiplier = 0.5
            elif trend == "bearish":
                tp_multiplier = 0.4
            else:
                tp_multiplier = 0.3  # 中性趋势进一步降低
            
            # 确保止盈价格合理（最大涨幅不超过 1.5%）
            max_profit_pct = 0.015  # 1.5%
            
            if side == "long":
                take_profit = price + (atr * tp_multiplier)
                # 限制最大涨幅
                max_tp = price * (1 + max_profit_pct)
                take_profit = min(take_profit, max_tp)
            else:
                take_profit = price - (atr * tp_multiplier)
                # 限制最大跌幅
                min_tp = price * (1 - max_profit_pct)
                take_profit = max(take_profit, min_tp)
            
            return round(take_profit, 2)
            
        except Exception:
            # 超短线默认止盈：0.8%
            if side == "long":
                return price * 1.008
            else:
                return price * 0.992
    
    def _bollinger_optimized_take_profit(self, price: float, bb_upper: float, 
                                       bb_lower: float, bb_middle: float,
                                       side: str, hold_minutes: int) -> float:
        """基于布林线边界优化止盈"""
        try:
            if bb_upper <= 0 or bb_lower <= 0:
                return price * 1.02 if side == "long" else price * 0.98
            
            # 计算价格相对于布林线的位置
            if side == "long":
                # 多头：止盈目标在布林线上轨附近
                distance_to_upper = bb_upper - price
                
                # 如果价格已经接近上轨，设置更保守的止盈
                if distance_to_upper < (bb_upper - bb_middle) * 0.3:
                    # 接近上轨，止盈设置在上轨下方
                    take_profit = bb_upper * 0.995
                else:
                    # 距离上轨较远，设置在上轨附近
                    take_profit = bb_upper * 0.99
                
                # 确保止盈价格合理
                min_profit = price * 1.005  # 至少0.5%利润
                take_profit = max(take_profit, min_profit)
                
            else:
                # 空头：止盈目标在布林线下轨附近
                distance_to_lower = price - bb_lower
                
                # 如果价格已经接近下轨，设置更保守的止盈
                if distance_to_lower < (bb_middle - bb_lower) * 0.3:
                    # 接近下轨，止盈设置在下轨上方
                    take_profit = bb_lower * 1.005
                else:
                    # 距离下轨较远，设置在下轨附近
                    take_profit = bb_lower * 1.01
                
                # 确保止盈价格合理
                max_profit = price * 0.995  # 至少0.5%利润
                take_profit = min(take_profit, max_profit)
            
            return round(take_profit, 2)
            
        except Exception:
            return price * 1.02 if side == "long" else price * 0.98
    
    def _time_based_take_profit(self, price: float, pnl_pct: float, 
                              hold_minutes: int, side: str, market_summary: Dict) -> float:
        """基于持仓时间优化止盈"""
        try:
            # 根据持仓时间调整止盈策略
            if hold_minutes < 15:
                # 持仓时间短，设置保守止盈
                profit_target = 0.008  # 0.8%
            elif hold_minutes < 30:
                # 持仓时间中等，正常止盈
                profit_target = 0.015  # 1.5%
            elif hold_minutes < 60:
                # 持仓时间较长，考虑设置收益委托
                profit_target = 0.025  # 2.5%
            else:
                # 持仓时间很长，设置更保守的止盈保护利润
                profit_target = 0.015  # 1.5%  # 修复：长时间持仓应该保护利润，而不是追求更高利润
            
            # 根据当前盈亏调整止盈
            if pnl_pct > 0:
                # 已经盈利，可以收紧止盈保护利润
                if pnl_pct > profit_target:
                    # 超过目标利润，考虑设置更保守的止盈
                    profit_target = max(profit_target, pnl_pct * 0.8)
                else:
                    # 未达到目标利润，保持原目标
                    profit_target = profit_target
            
            # 根据市场趋势微调
            trend = market_summary.get("trend", "neutral")
            if (side == "long" and trend == "bullish") or (side == "short" and trend == "bearish"):
                profit_target *= 1.2  # 趋势有利，增加止盈目标
            elif trend == "neutral":
                profit_target *= 0.8  # 趋势不明朗，降低止盈目标
            
            # 确保止盈目标在合理范围内
            profit_target = min(profit_target, 0.05)  # 最大5%
            profit_target = max(profit_target, 0.005)  # 最小0.5%
            
            if side == "long":
                take_profit = price * (1 + profit_target)
            else:
                take_profit = price * (1 - profit_target)
            
            return round(take_profit, 2)
            
        except Exception:
            return price * 1.02 if side == "long" else price * 0.98
    
    def _select_best_take_profit(self, base_tp: float, bb_tp: float, 
                               time_tp: float, side: str, 
                               hold_minutes: int, pnl_pct: float) -> float:
        """选择最优止盈价格"""
        try:
            # 根据持仓时间和盈亏状态选择策略
            if hold_minutes < 30:
                # 持仓时间短，优先使用布林线策略（更技术性）
                selected_tp = bb_tp
            elif hold_minutes >= 30 and pnl_pct > 0:
                # 持仓时间长且盈利，优先使用时间策略（保护利润）
                selected_tp = time_tp
            else:
                # 其他情况使用基础策略
                selected_tp = base_tp
            
            # 确保止盈价格合理性
            if side == "long":
                # 多头：止盈必须高于入场价格
                min_tp = min(base_tp, bb_tp, time_tp) * 1.001  # 确保有利润空间
                selected_tp = max(selected_tp, min_tp)
            else:
                # 空头：止盈必须低于入场价格
                max_tp = max(base_tp, bb_tp, time_tp) * 0.999  # 确保有利润空间
                selected_tp = min(selected_tp, max_tp)
            
            return round(selected_tp, 2)
            
        except Exception:
            return base_tp
    
    def should_set_profit_trailing(self, hold_minutes: int, pnl_pct: float) -> bool:
        """判断是否应该设置收益委托"""
        try:
            # 持仓超过30分钟且有一定盈利时设置收益委托
            if hold_minutes >= 30 and pnl_pct >= 0.005:  # 0.5%盈利
                return True
            # 持仓超过60分钟时强制设置收益委托
            elif hold_minutes >= 60:
                return True
            else:
                return False
                
        except Exception:
            return False


# 全局实例
advanced_take_profit = AdvancedTakeProfit()