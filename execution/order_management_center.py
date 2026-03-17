"""
委托管理中心 - 完全控制模式

核心理念：
1. 委托管理中心是唯一有权创建/取消委托的模块
2. 其他模块只能提交委托请求，不能直接调用 API
3. 统一管理持仓与委托的关系，确保：委托总量 <= 持仓量
4. 每次只保留每个方向 1 个最优委托
"""

from datetime import datetime
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class OrderManagementCenter:
    """委托管理中心 - 完全控制模式"""
    
    def __init__(self, order_executor):
        """
        初始化委托管理中心
        
        Args:
            order_executor: 委托执行器（唯一可以调用 API 的模块）
        """
        self.order_executor = order_executor
        self.order_requests = []  # 待处理的委托请求队列
        self.managed_symbols = set()  # 已接管的交易对
        
    def take_control(self, symbol: str) -> bool:
        """
        接管某个交易对的委托控制权
        
        Args:
            symbol: 交易对
            
        Returns:
            是否成功接管
        """
        logger.info(f"[ORDER_CENTER] {symbol} 开始接管委托控制权...")
        
        try:
            # 1. 获取当前持仓
            position = self._get_position(symbol)
            position_size = abs(float(position.get('position_amt', 0) or 0))
            
            # 2. 获取所有现有委托
            all_orders = self._get_all_orders(symbol)
            
            logger.info(f"[ORDER_CENTER] {symbol} 当前持仓：{position_size:.4f}, 委托数量：{len(all_orders)}")
            
            # 3. 分析委托合理性
            analysis = self._analyze_orders(all_orders, position_size)
            
            # 4. 执行接管操作
            if analysis['need_cleanup']:
                logger.warning(f"[ORDER_CENTER] {symbol} 需要清理：{analysis['reason']}")
                
                # 取消所有不合理的委托
                if analysis['orders_to_cancel']:
                    self._cancel_all_orders(symbol, analysis['orders_to_cancel'])
                
                # 重新设置合理的委托
                if analysis['should_recreate']:
                    self._recreate_orders(symbol, position_size, analysis['target_orders'])
            
            # 5. 标记为已接管
            self.managed_symbols.add(symbol)
            
            logger.info(f"[ORDER_CENTER] {symbol} 接管完成")
            return True
            
        except Exception as e:
            logger.error(f"[ORDER_CENTER] {symbol} 接管失败：{e}")
            return False
    
    def _analyze_orders(self, orders: List[Dict], position_size: float) -> Dict:
        """
        分析委托合理性
        
        Returns:
            分析结果：
            - need_cleanup: 是否需要清理
            - reason: 原因
            - orders_to_cancel: 需要取消的委托 ID 列表
            - should_recreate: 是否应该重新创建
            - target_orders: 目标委托配置
        """
        result = {
            'need_cleanup': False,
            'reason': '',
            'orders_to_cancel': [],
            'should_recreate': False,
            'target_orders': []
        }
        
        if not orders:
            return result
        
        # 按方向分组
        buy_orders = [o for o in orders if o.get('side', '').upper() == 'BUY']
        sell_orders = [o for o in orders if o.get('side', '').upper() == 'SELL']
        
        # 计算各方向的委托总量
        buy_total = sum(float(o.get('quantity', 0) or 0) for o in buy_orders)
        sell_total = sum(float(o.get('quantity', 0) or 0) for o in sell_orders)
        
        logger.info(f"委托分析：买入委托 {len(buy_orders)} 个 ({buy_total:.4f}), 卖出委托 {len(sell_orders)} 个 ({sell_total:.4f})")
        
        # 检查 1: 委托总量是否超过持仓量
        if position_size > 0:
            # 对于平仓委托，委托总量不应超过持仓量
            if sell_total > position_size * 1.1:  # 允许 10% 误差
                result['need_cleanup'] = True
                result['reason'] = f'平仓委托总量 ({sell_total:.4f}) 超过持仓量 ({position_size:.4f})'
                
                # 取消所有卖出委托，重新设置
                result['orders_to_cancel'] = [o.get('order_id') for o in sell_orders]
                result['should_recreate'] = True
                result['target_orders'] = [{
                    'side': 'SELL',
                    'quantity': position_size,
                    'priority': 'high'
                }]
                return result
        
        # 检查 2: 同一方向是否有多个委托
        if len(buy_orders) > 1:
            result['need_cleanup'] = True
            result['reason'] = f'买入委托被拆分 ({len(buy_orders)}个)'
            
            # 保留最优价格的委托，取消其他
            best_buy = max(buy_orders, key=lambda x: float(x.get('price', 0) or 0))
            result['orders_to_cancel'] = [o.get('order_id') for o in buy_orders if o != best_buy]
        
        if len(sell_orders) > 1:
            result['need_cleanup'] = True
            if not result['reason']:
                result['reason'] = f'卖出委托被拆分 ({len(sell_orders)}个)'
            else:
                result['reason'] += f', 卖出委托被拆分 ({len(sell_orders)}个)'
            
            # 保留最优价格的委托，取消其他
            best_sell = min(sell_orders, key=lambda x: float(x.get('price', 0) or 0))
            result['orders_to_cancel'].extend([o.get('order_id') for o in sell_orders if o != best_sell])
            
            result['should_recreate'] = False
        
        # 检查 3: 委托价格是否合理（例如止盈价格是否太近）
        # TODO: 添加价格合理性检查
        
        return result
    
    def _get_position(self, symbol: str) -> Dict:
        """获取持仓信息"""
        try:
            return self.order_executor.client.get_position(symbol) or {}
        except:
            return {}
    
    def _get_all_orders(self, symbol: str) -> List[Dict]:
        """获取所有未成交委托"""
        try:
            return self.order_executor.get_existing_orders(symbol) or []
        except:
            return []
    
    def _cancel_all_orders(self, symbol: str, order_ids: List[str]) -> bool:
        """批量取消所有指定委托"""
        if not order_ids:
            return True
        
        logger.warning(f"[ORDER_CENTER] {symbol} 批量取消 {len(order_ids)} 个委托...")
        
        try:
            result = self.order_executor.cancel_orders(symbol, order_ids)
            
            if result.get('success'):
                logger.info(f"[ORDER_CENTER] {symbol} ✅ 成功取消 {len(order_ids)} 个委托")
                return True
            else:
                logger.error(f"[ORDER_CENTER] {symbol} ❌ 取消失败：{result.get('error', '未知错误')}")
                return False
                
        except Exception as e:
            logger.error(f"[ORDER_CENTER] {symbol} 取消异常：{e}")
            return False
    
    def _recreate_orders(self, symbol: str, position_size: float, target_orders: List[Dict]) -> bool:
        """
        重新创建委托
        
        Args:
            symbol: 交易对
            position_size: 持仓数量
            target_orders: 目标委托配置列表
        """
        if not target_orders:
            return True
        
        logger.info(f"[ORDER_CENTER] {symbol} 重新创建 {len(target_orders)} 个委托...")
        
        for order_config in target_orders:
            side = order_config.get('side', 'SELL')
            quantity = order_config.get('quantity', position_size)
            
            # TODO: 根据策略计算合理的价格
            # 这里需要根据具体的交易策略来确定价格
            # 例如：止盈价格 = 开仓价 * 1.02 (2% 利润)
            
            logger.info(f"[ORDER_CENTER] {symbol} 准备创建委托：{side} {quantity} @ 待计算")
            
            # TODO: 调用 order_executor 创建委托
            # result = self.order_executor.execute_order(...)
        
        return True
    
    def request_order(self, symbol: str, side: str, price: float, quantity: float, 
                     order_type: str = 'limit') -> str:
        """
        接收委托请求（来自其他模块）
        
        Args:
            symbol: 交易对
            side: 方向 (BUY/SELL)
            price: 价格
            quantity: 数量
            order_type: 类型 (limit/market/stop_loss/take_profit)
            
        Returns:
            请求 ID
        """
        request_id = f"{symbol}_{datetime.now().timestamp()}"
        
        request = {
            'request_id': request_id,
            'symbol': symbol,
            'side': side.upper(),
            'price': price,
            'quantity': quantity,
            'order_type': order_type,
            'timestamp': datetime.now(),
            'status': 'pending'
        }
        
        self.order_requests.append(request)
        logger.info(f"[ORDER_CENTER] 收到委托请求：{request_id} {side} {quantity} @ {price}")
        
        # TODO: 立即处理请求队列
        self._process_request_queue(symbol)
        
        return request_id
    
    def _process_request_queue(self, symbol: str):
        """处理委托请求队列"""
        # 过滤出该交易的请求
        symbol_requests = [r for r in self.order_requests 
                         if r['symbol'] == symbol and r['status'] == 'pending']
        
        if not symbol_requests:
            return
        
        logger.info(f"[ORDER_CENTER] {symbol} 处理 {len(symbol_requests)} 个委托请求...")
        
        # TODO: 根据优先级和策略处理请求
        for request in symbol_requests:
            # 检查是否符合风控规则
            if self._check_risk_control(request):
                # 执行委托
                self._execute_request(request)
                request['status'] = 'completed'
            else:
                request['status'] = 'rejected'
                logger.warning(f"[ORDER_CENTER] 拒绝委托请求：{request['request_id']}")
    
    def _check_risk_control(self, request: Dict) -> bool:
        """风控检查"""
        # TODO: 实现风控逻辑
        # 1. 检查委托总量是否超过限制
        # 2. 检查价格是否合理
        # 3. 检查持仓是否足够
        return True
    
    def _execute_request(self, request: Dict) -> bool:
        """执行委托请求"""
        # TODO: 调用 order_executor 执行
        return True
    
    def get_managed_status(self, symbol: str) -> Dict:
        """获取某个交易对的管理状态"""
        if symbol not in self.managed_symbols:
            return {'managed': False}
        
        position = self._get_position(symbol)
        orders = self._get_all_orders(symbol)
        
        return {
            'managed': True,
            'position_size': abs(float(position.get('position_amt', 0) or 0)),
            'order_count': len(orders),
            'orders': orders
        }


# 便捷函数
def create_order_center(order_executor) -> OrderManagementCenter:
    """创建委托管理中心实例"""
    return OrderManagementCenter(order_executor)
