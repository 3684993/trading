"""
委托合理性检查模块

功能：
1. 从交易所拉取现有委托
2. 检查委托合理性（数量、间距、时间）
3. 自动取消不合理委托

不合理定义：
- 数量太多：超过最大委托数量限制
- 间隔太近：价格间距 < 10 USDT
- 时间太久：超过指定时间（默认 60 分钟）
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import os

logger = logging.getLogger(__name__)

# 委托日志文件路径
ORDER_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "orders.log")


class OrderRationalityChecker:
    """委托合理性检查器"""
    
    def __init__(self, order_executor, 
                 max_orders: int = 4,
                 min_price_spacing: float = 50.0,
                 order_timeout_minutes: int = 30,
                 max_orders_per_side: int = 2):
        """
        初始化检查器
        
        Args:
            order_executor: 委托执行器
            max_orders: 最大委托总数量（默认 4 个）
            min_price_spacing: 最小价格间距（默认 50 USDT）
            order_timeout_minutes: 委托超时时间（默认 30 分钟）
            max_orders_per_side: 每个方向最大委托数量（默认 2 个）
        """
        self.order_executor = order_executor
        self.max_orders = max_orders
        self.min_price_spacing = min_price_spacing
        self.order_timeout = timedelta(minutes=order_timeout_minutes)
        self.max_orders_per_side = max_orders_per_side
        
    def check_and_cleanup(self, symbol: str, side: Optional[str] = None, position_size: float = 0) -> Dict:
        """
        检查并清理不合理委托
        
        Args:
            symbol: 交易对
            side: 委托方向（可选，None 表示检查所有方向）
            
        Returns:
            清理结果字典
        """
        try:
            logger.info(f"[RATIONALITY] 开始检查 {symbol} 委托合理性...")
            
            # 1. 从交易所拉取现有委托
            orders = self._fetch_orders(symbol, side)
            
            if not orders:
                logger.info(f"[RATIONALITY] {symbol} 无未成交委托")
                return {
                    "success": True,
                    "symbol": symbol,
                    "total_orders": 0,
                    "cancelled_orders": [],
                    "reasons": []
                }
            
            logger.info(f"[RATIONALITY] {symbol} 当前有 {len(orders)} 个委托")
            
            # 2. 检查委托合理性（包括合并拆分委托）
            unreasonable_orders = self._find_unreasonable_orders(orders, position_size)
            
            if not unreasonable_orders:
                logger.info(f"[RATIONALITY] {symbol} 所有委托都合理")
                return {
                    "success": True,
                    "symbol": symbol,
                    "total_orders": len(orders),
                    "cancelled_orders": [],
                    "reasons": []
                }
            
            # 3. 批量取消不合理委托
            logger.info(f"[SMART_ORDER] {symbol} 准备取消 {len(unreasonable_orders)} 个不合理委托...")
            cancelled_ids = self._cancel_unreasonable_orders(symbol, unreasonable_orders)
            
            # 4. 记录清理原因
            reasons = self._build_reasons(unreasonable_orders)
            
            if cancelled_ids:
                logger.info(f"[SMART_ORDER] {symbol} ✅ 成功取消 {len(cancelled_ids)} 个不合理委托")
                logger.info(f"[SMART_ORDER] 取消原因：{', '.join(reasons)}")
                
                # 5. 检查是否需要重新创建委托
                if position_size > 0:
                    # 等待 2 秒让取消生效
                    import time
                    time.sleep(2)
                    
                    # 重新检查委托情况
                    remaining_orders = self._fetch_orders(symbol)
                    
                    # 判断是否需要重新创建
                    should_recreate = self._should_recreate_orders(
                        remaining_orders, position_size, cancelled_ids
                    )
                    
                    if should_recreate['need']:
                        logger.info(f"[SMART_ORDER] {symbol} 准备重新创建合理的委托...")
                        self._recreate_orders(symbol, position_size, should_recreate['config'])
            else:
                logger.warning(f"[SMART_ORDER] {symbol} ❌ 取消失败")
            
            return {
                "success": True,
                "symbol": symbol,
                "total_orders": len(orders),
                "cancelled_orders": cancelled_ids,
                "cancelled_count": len(cancelled_ids),
                "reasons": reasons,
                "unreasonable_orders": unreasonable_orders
            }
            
        except Exception as e:
            logger.error(f"[RATIONALITY] 检查失败：{e}")
            return {
                "success": False,
                "symbol": symbol,
                "error": str(e)
            }
    
    def _fetch_orders(self, symbol: str, side: Optional[str] = None) -> List[Dict]:
        """从交易所拉取现有委托"""
        try:
            orders = self.order_executor.get_existing_orders(symbol, side)
            return orders or []
        except Exception as e:
            logger.error(f"拉取委托失败：{e}")
            return []
    
    def _find_unreasonable_orders(self, orders: List[Dict], position_size: float = 0) -> List[Dict]:
        """查找不合理委托 - 按方向分组管理（包括合并拆分委托）"""
        unreasonable = []
        
        # 0. 持仓与委托关系检查（最高优先级）
        if position_size > 0:
            position_violation = self._check_position_order_ratio(orders, position_size)
            if position_violation:
                logger.error(f"[RATIONALITY] ⚠️ 持仓与委托关系严重不合理：{position_violation['reason']}")
                # 取消所有相关委托，强制重新设置
                unreasonable.extend(position_violation['orders_to_cancel'])
        
        # 1. 检测并合并拆分委托（优先处理）
        if position_size > 0:
            fragmented = self._find_fragmented_orders(orders, position_size)
            if fragmented:
                logger.warning(f"[RATIONALITY] 发现 {len(fragmented)} 个拆分委托，需要合并成 1 个")
                unreasonable.extend(fragmented)
        
        # 2. 按方向分组
        buy_orders = [o for o in orders if o.get("side", "").upper() == "BUY"]
        sell_orders = [o for o in orders if o.get("side", "").upper() == "SELL"]
        
        # 3. 检查每个方向的委托数量（每个方向最多 1 个）
        max_per_side = max(1, self.max_orders // 2)  # 每个方向至少 1 个
        
        for direction, direction_orders in [("BUY", buy_orders), ("SELL", sell_orders)]:
            if len(direction_orders) > max_per_side:
                excess_count = len(direction_orders) - max_per_side
                # 按时间排序，取消最新的（保留最早的）
                sorted_orders = sorted(direction_orders, key=lambda x: self._parse_order_time(x))
                unreasonable.extend(sorted_orders[excess_count:])  # 取消新的，保留旧的
                logger.warning(f"{direction} 委托数量过多：{len(direction_orders)} > {max_per_side}，标记取消 {excess_count} 个")
        
        # 4. 检查总数量
        if len(orders) > self.max_orders:
            excess_count = len(orders) - self.max_orders
            # 按时间排序，取消最新的（排除已经标记的）
            sorted_orders = sorted(orders, key=lambda x: self._parse_order_time(x))
            already_marked = {o.get("order_id") for o in unreasonable}
            for order in sorted_orders:
                if len(unreasonable) >= len(orders) - self.max_orders:
                    break
                if order.get("order_id") not in already_marked:
                    unreasonable.append(order)
                    already_marked.add(order.get("order_id"))
            logger.warning(f"总委托数量过多：{len(orders)} > {self.max_orders}")
        
        # 5. 检查间隔太近
        close_spacing_ids = self._find_close_spacing_orders(orders)
        unreasonable.extend(close_spacing_ids)
        
        # 6. 检查时间太久
        timeout_ids = self._find_timeout_orders(orders)
        unreasonable.extend(timeout_ids)
        
        # 去重
        seen_ids = set()
        unique_unreasonable = []
        for order in unreasonable:
            order_id = order.get("order_id", "")
            if order_id and order_id not in seen_ids:
                seen_ids.add(order_id)
                unique_unreasonable.append(order)
        
        return unique_unreasonable
    
    def _check_position_order_ratio(self, orders: List[Dict], position_size: float) -> Optional[Dict]:
        """
        检查持仓与委托的关系
        
        核心规则：
        1. 平仓委托总量不能超过持仓量
        2. 补仓委托总量不能超过持仓量的 50%
        
        Returns:
            如果有问题，返回 {'reason': str, 'orders_to_cancel': List}
            如果正常，返回 None
        """
        if position_size <= 0:
            return None
        
        # 按方向分组
        buy_orders = [o for o in orders if o.get("side", "").upper() == "BUY"]
        sell_orders = [o for o in orders if o.get("side", "").upper() == "SELL"]
        
        # 计算各方向委托总量
        buy_total = sum(float(o.get("quantity", 0) or 0) for o in buy_orders)
        sell_total = sum(float(o.get("quantity", 0) or 0) for o in sell_orders)
        
        result = None
        
        # 检查 1: 平仓委托是否超过持仓量
        # 假设当前持有多单，那么卖出委托是平仓委托
        if sell_total > position_size * 1.05:  # 允许 5% 误差
            logger.error(
                f"平仓委托总量 ({sell_total:.4f}) 超过持仓量 ({position_size:.4f}), "
                f"超出 {((sell_total/position_size - 1) * 100):.1f}%"
            )
            
            # 策略：取消所有卖出委托，然后重新设置一个合理的止盈委托
            result = {
                'reason': f'平仓委托超量：{sell_total:.4f} > {position_size:.4f}',
                'orders_to_cancel': [o.get("order_id") for o in sell_orders],
                'should_recreate': True,
                'recreate_config': {
                    'side': 'SELL',
                    'quantity': position_size,  # 使用实际持仓量
                    'type': 'take_profit'
                }
            }
        
        # 检查 2: 补仓委托是否超过持仓量的 50%
        if buy_total > position_size * 0.5:
            logger.error(
                f"补仓委托总量 ({buy_total:.4f}) 超过持仓量的 50% ({position_size * 0.5:.4f})"
            )
            
            if result:
                result['orders_to_cancel'].extend([o.get("order_id") for o in buy_orders])
            else:
                result = {
                    'reason': f'补仓委托超量：{buy_total:.4f} > {position_size * 0.5:.4f}',
                    'orders_to_cancel': [o.get("order_id") for o in buy_orders],
                    'should_recreate': True,
                    'recreate_config': {
                        'side': 'BUY',
                        'quantity': min(position_size * 0.5, 0.002),  # 最多补仓持仓的 50%
                        'type': 'add_position'
                    }
                }
        
        # 检查 3: 单个方向的委托是否被拆分
        if len(sell_orders) > 1 and sell_total > 0:
            avg_sell = sell_total / len(sell_orders)
            if avg_sell < position_size * 0.5:  # 平均每个委托小于持仓的 50%
                logger.error(f"卖出委托被拆分：{len(sell_orders)}个委托，平均每个 {avg_sell:.4f}")
                
                if result:
                    result['orders_to_cancel'].extend([o.get("order_id") for o in sell_orders])
                else:
                    result = {
                        'reason': f'卖出委托被拆分：{len(sell_orders)}个',
                        'orders_to_cancel': [o.get("order_id") for o in sell_orders],
                        'should_recreate': True,
                        'recreate_config': {
                            'side': 'SELL',
                            'quantity': position_size,
                            'type': 'take_profit'
                        }
                    }
        
        if len(buy_orders) > 1 and buy_total > 0:
            avg_buy = buy_total / len(buy_orders)
            if avg_buy < position_size * 0.3:  # 平均每个委托小于持仓的 30%
                logger.error(f"买入委托被拆分：{len(buy_orders)}个委托，平均每个 {avg_buy:.4f}")
                
                if result:
                    result['orders_to_cancel'].extend([o.get("order_id") for o in buy_orders])
                else:
                    result = {
                        'reason': f'买入委托被拆分：{len(buy_orders)}个',
                        'orders_to_cancel': [o.get("order_id") for o in buy_orders],
                        'should_recreate': False  # 补仓委托可以不重新创建
                    }
        
        return result
    
    def _find_fragmented_orders(self, orders: List[Dict], position_size: float = 0) -> List[Dict]:
        """查找并标记需要合并的拆分委托"""
        if len(orders) <= 1 or position_size <= 0:
            return []
        
        # 按方向分组
        buy_orders = [o for o in orders if o.get("side", "").upper() == "BUY"]
        sell_orders = [o for o in orders if o.get("side", "").upper() == "SELL"]
        
        fragmented_orders = []
        
        # 检查每个方向的委托
        for direction, direction_orders in [("BUY", buy_orders), ("SELL", sell_orders)]:
            if len(direction_orders) <= 1:
                continue
            
            # 计算该方向的总委托数量
            total_quantity = sum(float(o.get("quantity", 0) or 0) for o in direction_orders)
            avg_quantity = total_quantity / len(direction_orders)
            
            # 判断条件：委托数量 > 1 个，平均数量 < 持仓的 50%，总数量 > 持仓的 50%
            if (len(direction_orders) > 1 and 
                avg_quantity < position_size * 0.5 and
                total_quantity > position_size * 0.5):
                
                logger.warning(f"{direction} 委托被拆分：{len(direction_orders)}个委托，平均 {avg_quantity:.4f} < {position_size * 0.5:.4f}")
                
                # 保留最优价格（BUY 最高，SELL 最低），取消其他
                if direction == "BUY":
                    sorted_orders = sorted(direction_orders, key=lambda x: float(x.get("price", 0)), reverse=True)
                else:
                    sorted_orders = sorted(direction_orders, key=lambda x: float(x.get("price", 0)))
                
                keep_order = sorted_orders[0]
                for order in sorted_orders[1:]:
                    fragmented_orders.append(order)
                
                logger.warning(f"  保留：{keep_order.get('order_id')} @ {keep_order.get('price')}，取消 {len(direction_orders) - 1} 个")
        
        return fragmented_orders
    
    def _find_close_spacing_orders(self, orders: List[Dict]) -> List[Dict]:
        """查找间隔太近的委托 - 按方向分组管理"""
        # 使用智能版方法
        return self._find_close_spacing_orders_smart(orders)
    
    def _find_close_spacing_orders_smart(self, orders: List[Dict]) -> List[Dict]:
        """查找间隔太近的委托 - 智能聚类分析"""
        if len(orders) <= 1:
            return []
        
        # 按方向分组
        buy_orders = [o for o in orders if o.get("side", "").upper() == "BUY"]
        sell_orders = [o for o in orders if o.get("side", "").upper() == "SELL"]
        
        close_orders = []
        
        # 分别检查买入和卖出委托
        for direction, direction_orders in [("BUY", buy_orders), ("SELL", sell_orders)]:
            if len(direction_orders) <= 1:
                continue
            
            # 按价格排序
            sorted_orders = sorted(direction_orders, key=lambda x: float(x.get("price", 0) or 0))
            
            # 聚类分析：找出价格密集的委托群
            clusters = []
            current_cluster = [sorted_orders[0]]
            
            for i in range(1, len(sorted_orders)):
                prev_price = float(sorted_orders[i-1].get("price", 0) or 0)
                curr_price = float(sorted_orders[i].get("price", 0) or 0)
                spacing = curr_price - prev_price
                
                if spacing < self.min_price_spacing:
                    # 价格密集，加入当前簇
                    current_cluster.append(sorted_orders[i])
                else:
                    # 价格间隔足够，开始新簇
                    if len(current_cluster) > 1:
                        clusters.append(current_cluster)
                    current_cluster = [sorted_orders[i]]
            
            # 处理最后一个簇
            if len(current_cluster) > 1:
                clusters.append(current_cluster)
            
            # 处理每个密集簇
            for cluster in clusters:
                if len(cluster) <= 1:
                    continue
                
                logger.warning(
                    f"{direction} 委托价格密集：{len(cluster)}个委托，"
                    f"价格范围 {float(cluster[0].get('price', 0)):.2f} - {float(cluster[-1].get('price', 0)):.2f}"
                )
                
                # 保留最优价格的委托（BUY 保留最高，SELL 保留最低）
                if direction == "BUY":
                    # BUY：保留价格最高的
                    keep_order = max(cluster, key=lambda x: float(x.get("price", 0) or 0))
                else:
                    # SELL：保留价格最低的
                    keep_order = min(cluster, key=lambda x: float(x.get("price", 0) or 0))
                
                # 标记其他委托为不合理
                for order in cluster:
                    if order != keep_order:
                        close_orders.append(order)
                        logger.warning(
                            f"  标记取消：{order.get('order_id')} @ {float(order.get('price', 0)):.2f} "
                            f"(保留 {keep_order.get('order_id')} @ {float(keep_order.get('price', 0)):.2f})"
                        )
        
        return close_orders
    
    def _find_timeout_orders(self, orders: List[Dict]) -> List[Dict]:
        """查找时间太久的委托"""
        now = datetime.now()
        timeout_orders = []
        
        for order in orders:
            order_time = self._parse_order_time(order)
            if order_time:
                elapsed = now - order_time
                if elapsed > self.order_timeout:
                    timeout_orders.append(order)
                    logger.warning(
                        f"委托时间过久：{order.get('order_id')} "
                        f"已挂单 {elapsed.total_seconds()/60:.1f} 分钟 "
                        f"(限制={self.order_timeout.total_seconds()/60:.0f} 分钟)"
                    )
        
        return timeout_orders
    
    def _cancel_unreasonable_orders(self, symbol: str, orders: List[Dict]) -> List[str]:
        """强制批量取消不合理委托 - 失败则终止程序"""
        order_ids = [str(order.get("order_id", "")) for order in orders if order.get("order_id")]
        
        if not order_ids:
            logger.info("[SMART_ORDER] ✓ 没有需要取消的委托")
            return []
        
        # 记录需要取消的委托
        logger.error(f"[SMART_ORDER] ⚠️  发现 {len(order_ids)} 个不合理委托，准备强制取消")
        logger.error(f"[SMART_ORDER] 委托列表：{', '.join(order_ids)}")
        
        # 记录到文件
        for order in orders:
            order_id = str(order.get("order_id", ""))
            price = float(order.get('price', 0))
            quantity = float(order.get('quantity', 0))
            side = order.get('side', '')
            reason = order.get('unreasonable_reason', '不合理')
            
            try:
                with open(ORDER_LOG_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"  ❌ 取消：{order_id} @ {price:.2f} x {quantity:.4f} {side} - 原因：{reason}\n")
            except:
                pass
        
        # 强制取消 - 只尝试一次批量取消
        max_retries = 1
        for attempt in range(max_retries):
            try:
                logger.error(f"[SMART_ORDER] 第 {attempt+1} 次尝试批量取消...")
                result = self.order_executor.cancel_orders(symbol, order_ids)
                
                if result.get("success"):
                    cancelled = result.get("cancelled_ids", order_ids)
                    logger.error(f"[SMART_ORDER] ✅ 成功取消 {len(cancelled)}/{len(order_ids)} 个委托")
                    
                    # 记录成功取消的委托
                    for order_id in cancelled:
                        try:
                            with open(ORDER_LOG_FILE, 'a', encoding='utf-8') as f:
                                f.write(f"  ✅ 已取消：{order_id}\n")
                        except:
                            pass
                    
                    # 等待 3 秒让取消生效（交易所需要同步时间）
                    import time
                    time.sleep(3)
                    
                    # 验证是否真的取消了 - 重试 3 次
                    max_verify_retries = 3
                    for verify_attempt in range(max_verify_retries):
                        try:
                            remaining_orders = self._fetch_orders(symbol)
                            remaining_ids = [str(o.get("order_id", "")) for o in remaining_orders 
                                           if str(o.get("order_id", "")) in order_ids]
                            
                            if not remaining_ids:
                                logger.error(f"[SMART_ORDER] ✅ 所有不合理委托已成功取消")
                                return cancelled
                            
                            if verify_attempt < max_verify_retries - 1:
                                logger.warning(f"[SMART_ORDER] 验证失败，还有 {len(remaining_ids)} 个委托，{verify_attempt + 1}/3 重试中...")
                                time.sleep(1)  # 等待 1 秒再试
                            else:
                                logger.error(f"[SMART_ORDER] ❌ 取消后仍有 {len(remaining_ids)} 个委托未取消：{remaining_ids}")
                                logger.error(f"[SMART_ORDER] 程序终止 - 委托取消失败")
                                raise RuntimeError(f"委托取消失败：{len(remaining_ids)} 个委托仍存在于交易所")
                        except Exception as verify_error:
                            if verify_attempt == max_verify_retries - 1:
                                raise
                            logger.warning(f"[SMART_ORDER] 验证异常：{verify_error}，重试中...")
                            time.sleep(1)
                else:
                    error_msg = result.get('error', '未知错误')
                    logger.error(f"[SMART_ORDER] ❌ 批量取消失败：{error_msg}")
                    
            except Exception as e:
                logger.error(f"[SMART_ORDER] ❌ 取消异常：{e}")
                if attempt == max_retries - 1:
                    logger.error(f"[SMART_ORDER] 💀 达到最大重试次数，程序终止")
                    raise RuntimeError(f"委托取消完全失败：{e}")
        
        # 不应该到这里
        logger.error(f"[SMART_ORDER] ❌ 取消逻辑异常退出")
        raise RuntimeError("委托取消逻辑异常")
    
    def _build_reasons(self, orders: List[Dict]) -> List[str]:
        """构建详细的清理原因列表"""
        reasons = []
        
        # 统计各种原因
        too_many = 0
        too_close = 0
        too_old = 0
        fragmented = 0
        
        for order in orders:
            reason = order.get("unreasonable_reason", "")
            if "数量过多" in reason:
                too_many += 1
            elif "间隔过近" in reason or "价格密集" in reason:
                too_close += 1
            elif "时间过久" in reason:
                too_old += 1
            elif "拆分" in reason:
                fragmented += 1
        
        if too_many > 0:
            reasons.append(f"数量过多 ({too_many}个)")
        if too_close > 0:
            reasons.append(f"价格密集 ({too_close}个)")
        if too_old > 0:
            reasons.append(f"超时 ({too_old}个)")
        if fragmented > 0:
            reasons.append(f"拆分委托 ({fragmented}个)")
        
        return reasons
    
    def _should_recreate_orders(self, orders: List[Dict], position_size: float, 
                               cancelled_ids: List[str]) -> Dict:
        """
        判断是否需要重新创建委托
        
        Returns:
            {'need': bool, 'config': Dict}
        """
        # 分析取消的委托类型
        sell_cancelled = len([o for o in orders if o.get('order_id') in cancelled_ids 
                             and o.get('side', '').upper() == 'SELL'])
        buy_cancelled = len([o for o in orders if o.get('order_id') in cancelled_ids
                            and o.get('side', '').upper() == 'BUY'])
        
        result = {'need': False, 'config': {}}
        
        # 如果取消了卖出委托（止盈），需要重新创建
        if sell_cancelled > 0 and position_size > 0:
            result['need'] = True
            result['config'] = {
                'side': 'SELL',
                'quantity': position_size,
                'type': 'take_profit',
                'reason': '原止盈委托不合理已取消'
            }
        
        # 如果取消了买入委托（补仓），可以选择性重新创建
        # 暂时不自动重新创建补仓委托，等待主程序决策
        
        return result
    
    def _recreate_orders(self, symbol: str, position_size: float, config: Dict) -> bool:
        """
        重新创建委托
        
        Args:
            symbol: 交易对
            position_size: 持仓数量
            config: 委托配置 {'side', 'quantity', 'type'}
        """
        try:
            side = config.get('side', 'SELL')
            quantity = config.get('quantity', position_size)
            order_type = config.get('type', 'take_profit')
            
            logger.info(f"[SMART_ORDER] {symbol} 重新创建 {order_type} 委托：{side} {quantity}")
            
            # 获取当前价格
            current_price = self.order_executor.get_current_price(symbol)
            if not current_price:
                logger.error(f"[SMART_ORDER] {symbol} 无法获取当前价格，跳过重新创建")
                return False
            
            # 计算合理的价格
            if order_type == 'take_profit':
                # 止盈价格：在 1 分钟布林线上轨附近
                # TODO: 获取布林线数据
                # 暂时使用简单策略：开仓价 * 1.02 (2% 利润)
                # 需要scheduler 提供开仓价信息
                take_profit_price = current_price * 1.02
                
                # 四舍五入到整数
                take_profit_price = round(take_profit_price)
                
                logger.info(f"[SMART_ORDER] {symbol} 创建止盈委托：{side} {quantity} @ {take_profit_price:.2f}")
                
                # 创建限价委托
                result = self.order_executor.client.client.new_order(
                    symbol=symbol,
                    side=side,
                    type="LIMIT",
                    price=take_profit_price,
                    quantity=quantity,
                    timeInForce="GTC"
                )
                
                if result:
                    logger.info(f"[SMART_ORDER] {symbol} ✅ 止盈委托创建成功：{result.get('orderId')}")
                    return True
                else:
                    logger.error(f"[SMART_ORDER] {symbol} ❌ 止盈委托创建失败")
                    return False
            
            elif order_type == 'add_position':
                # 补仓委托：在 1 分钟布林线下轨附近
                # TODO: 获取布林线数据
                # 暂时使用简单策略：当前价 * 0.99 (1% 下跌)
                add_price = current_price * 0.99
                add_price = round(add_price)
                
                logger.info(f"[SMART_ORDER] {symbol} 创建补仓委托：{side} {quantity} @ {add_price:.2f}")
                
                result = self.order_executor.client.client.new_order(
                    symbol=symbol,
                    side=side,
                    type="LIMIT",
                    price=add_price,
                    quantity=quantity,
                    timeInForce="GTC"
                )
                
                if result:
                    logger.info(f"[SMART_ORDER] {symbol} ✅ 补仓委托创建成功：{result.get('orderId')}")
                    return True
                else:
                    logger.error(f"[SMART_ORDER] {symbol} ❌ 补仓委托创建失败")
                    return False
            
            return False
            
        except Exception as e:
            logger.error(f"[SMART_ORDER] {symbol} 重新创建委托失败：{e}")
            return False
    
    def _parse_order_time(self, order: Dict) -> Optional[datetime]:
        """解析委托时间"""
        try:
            # 尝试不同的时间字段
            time_fields = ["time", "timestamp", "updateTime", "createTime"]
            
            for field in time_fields:
                if field in order:
                    ts = order[field]
                    if isinstance(ts, (int, float)):
                        # 时间戳
                        if ts > 1e12:  # 毫秒
                            return datetime.fromtimestamp(ts / 1000)
                        else:  # 秒
                            return datetime.fromtimestamp(ts)
                    elif isinstance(ts, str):
                        # 字符串时间
                        try:
                            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except:
                            continue
            
            # 默认返回当前时间（不算超时）
            return datetime.now()
            
        except Exception as e:
            logger.debug(f"解析委托时间失败：{e}")
            return datetime.now()
    
    def update_config(self, **kwargs):
        """更新配置参数"""
        if "max_orders" in kwargs:
            self.max_orders = int(kwargs["max_orders"])
        if "min_price_spacing" in kwargs:
            self.min_price_spacing = float(kwargs["min_price_spacing"])
        if "order_timeout_minutes" in kwargs:
            self.order_timeout = timedelta(minutes=int(kwargs["order_timeout_minutes"]))
        
        logger.info(
            f"委托合理性检查器配置已更新："
            f"max_orders={self.max_orders}, "
            f"min_spacing={self.min_price_spacing}, "
            f"timeout={self.order_timeout.total_seconds()/60:.0f}分钟"
        )


# 便捷函数
def check_orders(order_executor, symbol: str, side: Optional[str] = None, **kwargs) -> Dict:
    """
    快速检查并清理不合理委托
    
    使用示例:
    result = check_orders(order_executor, "BTCUSDT")
    print(f"取消的委托：{result['cancelled_orders']}")
    """
    checker = OrderRationalityChecker(order_executor, **kwargs)
    return checker.check_and_cleanup(symbol, side)
