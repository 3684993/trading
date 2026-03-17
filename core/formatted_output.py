"""
人性化终端输出格式化模块
提供易于阅读的交易状态信息显示
"""

import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from core.logger import logger


class FormattedOutput:
    """格式化输出类，提供人性化的交易信息显示"""
    
    def __init__(self):
        self.cycle_count = 0
        self.last_trade_time = None
        self.current_position = None
        
    def print_cycle_header(self, cycle_num: int, start_time: datetime):
        """打印周期开始信息"""
        self.cycle_count += 1
        
        header = f"\n{'='*80}"
        header += f"\n🔍 交易周期 #{cycle_num} | {start_time.strftime('%H:%M:%S')}"
        header += f"\n{'='*80}"
        
        print(header)
        
    def print_market_status(self, symbol: str, price: float, volume: float, trend: str):
        """打印市场状态信息"""
        trend_emoji = {
            "bullish": "📈",
            "bearish": "📉", 
            "neutral": "➡️",
            "volatile": "⚡"
        }.get(trend, "➡️")
        
        status = f"🏦 市场状态 | {symbol} | 价格: {price:,.2f} | 成交量: {volume:.1f} | 趋势: {trend_emoji} {trend}"
        print(status)
        
    def print_indicators(self, symbol: str, indicators: Dict):
        """打印技术指标信息"""
        try:
            # 获取技术指标值，正确处理字典结构
            rsi = indicators.get('rsi', 0)
            
            # MACD是一个字典，需要获取macd字段
            macd_dict = indicators.get('macd', {})
            macd_value = macd_dict.get('macd', 0) if isinstance(macd_dict, dict) else 0
            
            atr = indicators.get('atr', 0)
            
            rsi_status = "🔴超买" if rsi > 70 else "🟢超卖" if rsi < 30 else "🟡中性"
            macd_status = "🟢看涨" if macd_value > 0 else "🔴看跌"
            
            indicators_info = f"📊 技术指标 | RSI: {rsi:.1f} {rsi_status} | MACD: {macd_value:+.1f} {macd_status} | ATR: {atr:.1f}"
            print(indicators_info)
        except Exception as e:
            # 如果指标解析失败，显示简化信息
            print(f"📊 技术指标 | 数据解析错误: {str(e)[:50]}...")
        
    def print_position_status(self, symbol: str, position: Dict):
        """打印持仓状态信息（与交易所数据一致）"""
        if not position.get("has_position", False):
            print("💼 持仓状态 | 无持仓")
            self.current_position = None
            return
            
        side = position.get("side", "none")
        size = position.get("position_size", 0)
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", 0)
        unrealized_pnl = position.get("current_pnl", 0)  # 未实现盈亏（USDT）
        hold_minutes = position.get("hold_minutes", 0)
        
        # 使用PositionManager计算的交易所收益率
        exchange_pnl_pct = position.get("exchange_pnl_pct", 0)
        initial_margin = position.get("initial_margin", 0)
        
        # 计算盈亏平衡价格（损益两平价）
        if side == "long":
            breakeven_price = entry_price
        else:
            breakeven_price = entry_price
        
        side_emoji = "🟢" if side == "long" else "🔴" if side == "short" else "⚪"
        pnl_emoji = "💰" if unrealized_pnl > 0 else "💸" if unrealized_pnl < 0 else "⚪"
        
        # 显示与交易所一致的持仓信息
        position_info = (
            f"💼 持仓状态 | {side_emoji} {side.upper()} | "
            f"数量: {size:.4f} BTC | "
            f"开仓: {entry_price:,.2f} | "
            f"标记: {current_price:,.2f} | "
            f"盈亏: {pnl_emoji} {unrealized_pnl:+.2f} USDT | "
            f"收益率: {exchange_pnl_pct:+.2f}% | "
            f"持仓: {hold_minutes}分钟"
        )
        print(position_info)
        
        # 显示详细的交易所格式信息
        exchange_info = (
            f"   📊 交易所数据 | "
            f"保证金: {initial_margin:.2f} USDT | "
            f"杠杆: 20x | "
            f"模式: 全仓"
        )
        print(exchange_info)
        
        self.current_position = position
        
    def print_ai_decision(self, symbol: str, decision: Dict):
        """打印AI决策信息"""
        action = decision.get("action")
        direction = decision.get("direction", "flat")

        action_emoji = {
            "open_long": "🟢开多",
            "open_short": "🔴开空", 
            "add_position": "➕加仓",
            "close_position": "💸平仓",
            "reverse_position": "🔄反转",
            "hold": "⏸️持仓"
        }.get(action, {"long": "🟢目标做多", "short": "🔴目标做空", "flat": "⏸️目标空仓"}.get(direction, "⏸️持仓"))
        
        entry_range = decision.get("entry_range", [0, 0])
        stop_loss = decision.get("stop_loss", 0)
        take_profit = decision.get("take_profit", 0)
        confidence = decision.get("confidence", 0.5)
        target_size = decision.get("target_size", 0)
        
        decision_info = (
            f"🤖 AI决策 | {action_emoji} | "
            f"入场区间: {entry_range[0]:,.2f}-{entry_range[1]:,.2f} | "
            f"止损: {stop_loss:,.2f} | "
            f"止盈: {take_profit:,.2f} | "
            f"目标仓位: {float(target_size):.4f} | "
            f"置信度: {confidence:.1%}"
        )
        print(decision_info)
        
    def print_execution_result(self, symbol: str, result: Dict):
        """打印执行结果信息"""
        action = result.get("action", "hold")
        success = result.get("success", False)

        if result.get("in_progress"):
            state = result.get("state", "UNKNOWN")
            remaining = result.get("remaining_size", 0)
            slippage = result.get("slippage", 0)
            elapsed = result.get("elapsed", 0)
            print(
                f"⏳ 执行中 | {symbol} | state={state} | 剩余: {remaining:.4f} | "
                f"滑点估计: {slippage:.4%} | 用时: {elapsed:.0f}s"
            )
            return
        
        if action == "hold" and not result.get("error"):
            print("⚡ 执行结果 | 保持持仓，无操作")
            return
            
        if action == "open_long" or action == "open_short":
            if success:
                side = "多" if action == "open_long" else "空"
                size = result.get("size", 0)
                price = result.get("price", 0)
                print(f"✅ 执行结果 | 成功开{side} | 数量: {size:.4f} | 价格: {price:,.2f}")
                self.last_trade_time = datetime.now()
            else:
                print(f"❌ 执行结果 | 开仓失败 | 错误: {result.get('error', '未知')}")
                
        elif action == "close_position":
            if success:
                pnl = result.get("pnl_pct", 0)
                pnl_emoji = "💰" if pnl > 0 else "💸" if pnl < 0 else "⚪"
                print(f"✅ 执行结果 | 成功平仓 | 盈亏: {pnl_emoji} {pnl:+.2f}%")
                self.current_position = None
                self.last_trade_time = datetime.now()
            else:
                print(f"❌ 执行结果 | 平仓失败 | 错误: {result.get('error', '未知')}")
                
        elif action == "add_position":
            if success:
                size = result.get("size", 0)
                price = result.get("price", 0)
                print(f"✅ 执行结果 | 成功加仓 | 数量: {size:.4f} | 价格: {price:,.2f}")
                self.last_trade_time = datetime.now()
            else:
                print(f"❌ 执行结果 | 加仓失败 | 错误: {result.get('error', '未知')}")
                
        else:
            print(f"⚡ 执行结果 | {result.get('message', '无操作')}")
            
    def print_sl_tp_info(self, symbol: str, sl_tp_result: Dict):
        """打印止盈止损信息"""
        if sl_tp_result.get("mode") == "local_simulation":
            sl = sl_tp_result.get("stop_loss", 0)
            tp = sl_tp_result.get("take_profit", 0)
            print(f"🎯 止盈止损 | 本地模拟 | 止损: {sl:,.2f} | 止盈: {tp:,.2f}")
        else:
            sl_order = sl_tp_result.get("stop_loss_order")
            tp_order = sl_tp_result.get("take_profit_order")
            if sl_order and tp_order:
                print(f"🎯 止盈止损 | 已设置 | 止损单: {sl_order.get('stop_price', 0):,.2f} | 止盈单: {tp_order.get('stop_price', 0):,.2f}")
                
    def print_learning_stats(self, symbol: str, stats: Dict):
        """打印学习统计数据"""
        win_rate = stats.get("win_rate", 0)
        profit_factor = stats.get("profit_factor", 0)
        avg_hold = stats.get("avg_hold", 0)
        total_pnl = stats.get("total_pnl", 0)
        
        win_emoji = "🏆" if win_rate > 0.7 else "👍" if win_rate > 0.5 else "🤔"
        
        stats_info = (
            f"📚 学习统计 | 胜率: {win_emoji} {win_rate:.1%} | "
            f"盈亏比: {profit_factor:.2f} | "
            f"平均持仓: {avg_hold}分钟 | "
            f"总收益: {total_pnl:+.2f}%"
        )
        print(stats_info)
        
    def print_cycle_footer(self, cycle_num: int, duration: float):
        """打印周期结束信息"""
        footer = f"\n⏱️ 周期完成 | 耗时: {duration:.2f}秒"
        
        if self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds() / 60
            footer += f" | 上次交易: {time_since_last:.1f}分钟前"
            
        footer += f"\n{'='*80}\n"
        print(footer)
        
    def print_system_status(self, mode: str, symbols: list, interval: int):
        """打印系统状态信息"""
        status = f"\n🚀 系统状态 | 模式: {mode.upper()} | 交易对: {', '.join(symbols)} | 周期: {interval}秒"
        print(status)
        
    def print_error(self, error_msg: str):
        """打印错误信息"""
        print(f"\n❌ 系统错误 | {error_msg}")
        
    def print_warning(self, warning_msg: str):
        """打印警告信息"""
        print(f"⚠️ 系统警告 | {warning_msg}")
    
    def print_info(self, info_msg: str):
        """打印普通信息"""
        print(f"ℹ️ 系统信息 | {info_msg}")


# 全局实例
formatted_output = FormattedOutput()
