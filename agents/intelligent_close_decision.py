"""
智能平仓决策模块
综合考虑持仓时间、收益率、净利润、趋势变化等多因子
"""

from typing import Dict, Tuple
from datetime import datetime
from core.logger import logger


class IntelligentCloseDecision:
    """智能平仓决策类"""
    
    def __init__(self):
        self.trade_cost_rate = 0.0015  # 交易成本率（0.15%，考虑开仓 + 平仓 + 滑点）
        self.min_net_profit_rate = 0.008  # 最小净利润率（0.8%，确保覆盖成本）
        self.min_hold_time_for_profit = 45  # 盈利持仓最小时间（分钟）
        self.min_profit_after_cost = 0.005  # 扣除成本后的最小盈利（0.5%）
        self.min_pnl_for_early_close = 0.05  # 早期平仓最小收益率（5%）
        
    def should_close_position(self, position: Dict, indicators: Dict, 
                            market_summary: Dict, trend_change: bool) -> Tuple[bool, str, float]:
        """
        智能判断是否应该平仓
        
        Args:
            position: 持仓信息
            indicators: 技术指标
            market_summary: 市场概况
            trend_change: 趋势是否改变
            
        Returns:
            Tuple[是否平仓, 平仓原因, 平仓优先级]
        """
        try:
            if not position.get("has_position", False):
                return False, "无持仓", 0.0
            
            # 获取关键参数
            hold_minutes = position.get("hold_minutes", 0)
            pnl_pct = position.get("current_pnl_pct", 0)  # 收益率百分比
            exchange_pnl_pct = position.get("exchange_pnl_pct", 0)  # 交易所收益率
            initial_margin = position.get("initial_margin", 0)  # 初始保证金
            
            # 计算净利润（考虑交易成本）
            net_profit_pct = self._calculate_net_profit(pnl_pct, hold_minutes)
            
            # 多因子平仓决策
            close_decisions = []
            
            # 因子1: 趋势改变（最高优先级）
            if trend_change:
                close_decisions.append((True, "趋势改变", 1.0))
            
            # 因子 2: 高收益短持仓（高优先级）- 必须覆盖成本且有显著盈利
            if hold_minutes < self.min_hold_time_for_profit and pnl_pct >= self.min_pnl_for_early_close:  # 5% 收益
                close_decisions.append((True, "高收益短持仓", 0.9))
            
            # 因子 3: 净利润为正且持仓时间适中
            if net_profit_pct >= self.min_net_profit_rate and self.min_hold_time_for_profit <= hold_minutes < 120:
                close_decisions.append((True, "净利润达标", 0.8))
            
            # 因子 4: 持仓时间达到预期且收益尚可
            expected_hold = position.get("expected_hold_minutes", 60)
            if hold_minutes >= expected_hold and net_profit_pct >= self.min_profit_after_cost:  # 扣除成本后盈利
                close_decisions.append((True, "持仓时间达标", 0.7))
            
            # 因子 5: 强制止损（中等优先级）- 考虑交易成本
            stop_loss_threshold = -0.05  # 亏损 5% 以上（考虑成本）
            if pnl_pct <= stop_loss_threshold:
                close_decisions.append((True, "强制止损", 0.6))
            
            # 因子 6: 趋势有利但持仓时间过长（低优先级）
            current_trend = market_summary.get("trend", "neutral")
            position_side = position.get("side", "long")
            trend_favorable = (position_side == "long" and current_trend == "bullish") or \
                            (position_side == "short" and current_trend == "bearish")
            
            if trend_favorable and hold_minutes >= 120:  # 趋势有利但持仓过长（2 小时）
                close_decisions.append((False, "趋势有利继续持仓", 0.3))
            
            # 因子 7: 持仓时间过短且收益不佳（高优先级阻止平仓）
            if hold_minutes < self.min_hold_time_for_profit and pnl_pct < self.min_pnl_for_early_close:  # 持仓过短且收益低
                close_decisions.append((False, "持仓时间过短收益不足", 0.9))  # 提高优先级到 0.9
            
            # 因子 8: 收益不足以覆盖成本（高优先级阻止平仓）
            if net_profit_pct < self.min_profit_after_cost:
                close_decisions.append((False, "收益不足以覆盖成本", 0.85))
            
            # 因子 9: 持仓时间极短（<15 分钟）且收益<2%，绝对阻止平仓
            if hold_minutes < 15 and pnl_pct < 0.02:
                close_decisions.append((False, "持仓时间极短禁止平仓", 0.95))  # 最高优先级
            
            # 综合决策：选择优先级最高的决策
            if close_decisions:
                # 按优先级排序
                close_decisions.sort(key=lambda x: x[2], reverse=True)
                best_decision = close_decisions[0]
                
                # 检查是否有阻止平仓的因子（优先级高于平仓因子）
                block_decisions = [d for d in close_decisions if not d[0] and d[2] > best_decision[2]]
                if block_decisions:
                    # 存在阻止平仓的更高优先级因子
                    block_decision = max(block_decisions, key=lambda x: x[2])
                    return False, block_decision[1], block_decision[2]
                
                return best_decision
            
            # 默认决策：持仓时间未达预期，继续持仓
            return False, "继续持仓", 0.5
            
        except Exception as e:
            logger.error(f"智能平仓决策失败: {e}")
            return False, "决策错误", 0.0
    
    def _calculate_net_profit(self, pnl_pct: float, hold_minutes: int) -> float:
        """计算净利润率（考虑交易成本和时间成本）"""
        try:
            # 基础交易成本（开仓+平仓）
            base_cost = self.trade_cost_rate * 2
            
            # 时间成本（持仓时间越长，机会成本越高）
            time_cost = min(hold_minutes / 240.0 * 0.001, 0.005)  # 最大0.5%
            
            # 净利润 = 毛利润 - 交易成本 - 时间成本
            net_profit = pnl_pct - base_cost - time_cost
            
            return max(net_profit, -0.1)  # 限制最大亏损为10%
            
        except Exception:
            return pnl_pct
    
    def get_close_recommendation(self, position: Dict, indicators: Dict, 
                               market_summary: Dict, trend_change: bool) -> Dict:
        """获取平仓建议详情"""
        should_close, reason, priority = self.should_close_position(
            position, indicators, market_summary, trend_change
        )
        
        # 计算关键指标
        hold_minutes = position.get("hold_minutes", 0)
        pnl_pct = position.get("current_pnl_pct", 0)
        net_profit_pct = self._calculate_net_profit(pnl_pct, hold_minutes)
        
        return {
            "should_close": should_close,
            "reason": reason,
            "priority": priority,
            "hold_minutes": hold_minutes,
            "pnl_pct": pnl_pct,
            "net_profit_pct": net_profit_pct,
            "recommendation": "平仓" if should_close else "继续持仓"
        }
    
    def analyze_trend_strength(self, indicators: Dict, market_summary: Dict) -> Dict:
        """分析趋势强度"""
        try:
            # 获取技术指标
            rsi = indicators.get("rsi", 50)
            macd = indicators.get("macd", {}).get("macd", 0)
            atr = indicators.get("atr", 0)
            bb_width = indicators.get("bollinger", {}).get("width", 0)
            
            # 获取市场概况
            trend = market_summary.get("trend", "neutral")
            momentum = market_summary.get("momentum", "neutral")
            volatility = market_summary.get("volatility", "normal")
            
            # 计算趋势强度分数（0-1）
            trend_score = 0.5
            
            # RSI趋势判断
            if trend == "bullish" and rsi < 70:
                trend_score += 0.2
            elif trend == "bearish" and rsi > 30:
                trend_score += 0.2
            
            # MACD趋势确认
            if (trend == "bullish" and macd > 0) or (trend == "bearish" and macd < 0):
                trend_score += 0.15
            
            # 动量判断
            if momentum == "strengthening":
                trend_score += 0.1
            elif momentum == "weakening":
                trend_score -= 0.1
            
            # 波动率判断
            if volatility == "high" and bb_width > atr * 2:
                trend_score -= 0.05  # 高波动率降低趋势可靠性
            
            # 归一化到0-1范围
            trend_score = max(0.0, min(1.0, trend_score))
            
            # 趋势强度等级
            if trend_score >= 0.8:
                strength_level = "很强"
            elif trend_score >= 0.6:
                strength_level = "强"
            elif trend_score >= 0.4:
                strength_level = "中等"
            else:
                strength_level = "弱"
            
            return {
                "trend_score": trend_score,
                "strength_level": strength_level,
                "reliability": "高" if trend_score >= 0.6 else "中" if trend_score >= 0.4 else "低"
            }
            
        except Exception as e:
            logger.error(f"趋势强度分析失败: {e}")
            return {"trend_score": 0.5, "strength_level": "未知", "reliability": "低"}


# 全局实例
intelligent_close_decision = IntelligentCloseDecision()