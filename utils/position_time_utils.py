"""
持仓时间工具类
解决系统重启后持仓时间丢失问题
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
from core.logger import logger


class PositionTimeUtils:
    """持仓时间工具类，解决持仓时间持久化问题"""
    
    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or str(Path(__file__).parent.parent / "data")
        self.position_time_file = Path(self.data_dir) / "position_times.json"
        self.position_times: Dict = self._load_position_times()
        
    def _load_position_times(self) -> Dict:
        """加载持仓时间数据"""
        try:
            if self.position_time_file.exists():
                with open(self.position_time_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Load position times error: {e}")
            return {}
            
    def _save_position_times(self):
        """保存持仓时间数据"""
        try:
            with open(self.position_time_file, 'w', encoding='utf-8') as f:
                json.dump(self.position_times, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Save position times error: {e}")
            
    def get_position_entry_time(self, symbol: str, binance_client) -> Optional[datetime]:
        """从交易所获取持仓的开仓时间"""
        try:
            # 首先检查本地持久化存储
            if symbol in self.position_times:
                entry_time_str = self.position_times[symbol].get("entry_time")
                if entry_time_str:
                    return datetime.fromisoformat(entry_time_str)
            
            # 从交易所获取订单历史，查找开仓时间
            orders = binance_client.client.get_orders(symbol=symbol, limit=50)
            
            # 按时间倒序排序，找到最新的开仓订单
            open_orders = [o for o in orders if o.get("status") in ["NEW", "PARTIALLY_FILLED"]]
            filled_orders = [o for o in orders if o.get("status") == "FILLED"]
            
            # 优先检查未完成订单
            for order in open_orders + filled_orders:
                order_time = order.get("time")
                if order_time:
                    # 将毫秒时间戳转换为datetime
                    entry_time = datetime.fromtimestamp(order_time / 1000)
                    
                    # 保存到本地持久化存储
                    self.position_times[symbol] = {
                        "entry_time": entry_time.isoformat(),
                        "last_updated": datetime.now().isoformat()
                    }
                    self._save_position_times()
                    
                    logger.info(f"Found entry time from exchange: {symbol} at {entry_time}")
                    return entry_time
            
            logger.warning(f"No entry time found for {symbol} from exchange")
            return None
            
        except Exception as e:
            logger.error(f"Get position entry time error: {e}")
            return None
            
    def update_position_entry_time(self, symbol: str, entry_time: datetime):
        """更新持仓开仓时间"""
        try:
            self.position_times[symbol] = {
                "entry_time": entry_time.isoformat(),
                "last_updated": datetime.now().isoformat()
            }
            self._save_position_times()
            logger.info(f"Updated position entry time: {symbol} at {entry_time}")
        except Exception as e:
            logger.error(f"Update position entry time error: {e}")
            
    def clear_position_entry_time(self, symbol: str):
        """清除持仓开仓时间"""
        try:
            if symbol in self.position_times:
                del self.position_times[symbol]
                self._save_position_times()
                logger.info(f"Cleared position entry time for {symbol}")
        except Exception as e:
            logger.error(f"Clear position entry time error: {e}")
            
    def calculate_hold_minutes_with_fallback(self, symbol: str, binance_client, 
                                           current_entry_time: datetime = None) -> int:
        """计算持仓时间，包含交易所回退机制"""
        try:
            # 优先使用当前持仓时间
            if current_entry_time:
                hold_seconds = (datetime.now() - current_entry_time).total_seconds()
                hold_minutes = int(hold_seconds / 60)
                
                # 如果持仓时间异常（0分钟），尝试从交易所获取
                if hold_minutes == 0:
                    exchange_entry_time = self.get_position_entry_time(symbol, binance_client)
                    if exchange_entry_time:
                        hold_seconds = (datetime.now() - exchange_entry_time).total_seconds()
                        hold_minutes = int(hold_seconds / 60)
                        logger.info(f"Recovered hold time from exchange: {symbol} - {hold_minutes} minutes")
                
                return hold_minutes
            
            # 如果没有当前持仓时间，直接从交易所获取
            exchange_entry_time = self.get_position_entry_time(symbol, binance_client)
            if exchange_entry_time:
                hold_seconds = (datetime.now() - exchange_entry_time).total_seconds()
                hold_minutes = int(hold_seconds / 60)
                return hold_minutes
            
            # 如果都无法获取，返回0
            return 0
            
        except Exception as e:
            logger.error(f"Calculate hold minutes with fallback error: {e}")
            return 0


# 全局实例
position_time_utils = PositionTimeUtils()