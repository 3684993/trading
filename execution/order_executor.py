from typing import Dict, List, Optional
from datetime import datetime
from core.logger import logger
from exchange.binance_client import BinanceClient
from config.settings import settings
from core.execution_planner import ExecutionPlanner
from core.order_manager import OrderManager


class OrderExecutor:
    def __init__(self, binance_client: BinanceClient, testnet: bool = True, trade_guard=None):
        self.client = binance_client
        self.testnet = testnet
        self.trade_guard = trade_guard
        self.pending_orders: Dict[str, Dict] = {}
        self.order_history: List[Dict] = []
        self.recently_cancelled: Dict[str, datetime] = {}
        self.tick_sizes: Dict[str, float] = {}
        self.step_sizes: Dict[str, float] = {}
        self.execution_planner = ExecutionPlanner()
        self.order_manager = OrderManager(self)
        
        logger.info(f"OrderExecutor initialized (testnet={testnet}, env={settings.trading_env})")
    
    def _get_symbol_info(self, symbol: str) -> Dict:
        try:
            exchange_info = self.client.client.exchange_info()
            for s in exchange_info.get("symbols", []):
                if s.get("symbol") == symbol:
                    filters = s.get("filters", [])
                    tick_size = 0.1
                    step_size = 0.001
                    for f in filters:
                        if f.get("filterType") == "PRICE_FILTER":
                            tick_size = float(f.get("tickSize", 0.1))
                        if f.get("filterType") == "LOT_SIZE":
                            step_size = float(f.get("stepSize", 0.001))
                    self.tick_sizes[symbol] = tick_size
                    self.step_sizes[symbol] = step_size
                    return {"tick_size": tick_size, "step_size": step_size}
            return {"tick_size": 0.1, "step_size": 0.001}
        except Exception as e:
            logger.error(f"Get symbol info error: {e}")
            return {"tick_size": 0.1, "step_size": 0.001}
    
    def _round_price(self, symbol: str, price: float) -> float:
        if symbol not in self.tick_sizes:
            self._get_symbol_info(symbol)
        tick_size = self.tick_sizes.get(symbol, 0.1)
        precision = len(str(tick_size).rstrip('0').split('.')[-1]) if '.' in str(tick_size) else 0
        return round(round(price / tick_size) * tick_size, precision)
    
    def _round_quantity(self, symbol: str, quantity: float) -> float:
        if symbol not in self.step_sizes:
            self._get_symbol_info(symbol)
        step_size = self.step_sizes.get(symbol, 0.001)
        precision = len(str(step_size).rstrip('0').split('.')[-1]) if '.' in str(step_size) else 0
        return round(round(quantity / step_size) * step_size, precision)
    
    def open_position(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "market",
        price: float = None,
        reduce_only: bool = False
    ) -> Dict:
        try:
            if side.lower() == "long":
                order_side = "BUY"
            elif side.lower() == "short":
                order_side = "SELL"
            else:
                order_side = "BUY"
            
            rounded_size = self._round_quantity(symbol, size)
            
            # 检查暴露量限制：仅开仓委托需要检查，平仓委托（reduce_only=True）跳过检查
            if not reduce_only:
                exposure_check = self.check_exposure_limit(symbol, order_side, rounded_size)
                if not exposure_check.get("allowed"):
                    logger.warning(
                        f"[ORDER_SUBMIT] symbol={symbol} reason=EXPOSURE_LIMIT_EXCEEDED "
                        f"size={rounded_size:.4f}, available={exposure_check.get('available_size', 0):.4f}, "
                        f"reason={exposure_check.get('reason', '')}"
                    )
                    return {
                        "success": False, 
                        "error": "EXPOSURE_LIMIT_EXCEEDED",
                        "available_size": exposure_check.get("available_size", 0)
                    }
            else:
                logger.debug("[ORDER] 平仓委托，跳过暴露量检查：size=%.4f", rounded_size)
            
            # 检查数量范围：最小 0.002 BTC，最大 0.1 BTC（更合理的范围）
            if rounded_size < 0.002 or rounded_size > 0.1:
                logger.warning("[ORDER_SUBMIT] symbol=%s reason=SIZE_OUT_OF_RANGE size=%.6f (范围：0.002-0.1)", symbol, rounded_size)
                return {"success": False, "error": "SIZE_OUT_OF_RANGE"}
            
            # 检查是否存在重复委托（限价单才检查）
            if order_type.lower() == "limit" and price:
                rounded_price = self._round_price(symbol, price)
                
                # 检查重复委托
                if self.check_duplicate_orders(symbol, order_side, rounded_price, rounded_size, check_price_only=True):  # 只检查价格
                    logger.warning(f"发现重复委托，跳过执行: {side} {rounded_size} {symbol} @ {rounded_price}")
                    return {
                        "success": False,
                        "error": "Duplicate order detected",
                        "duplicate": True
                    }
            
            params = {
                "symbol": symbol,
                "side": order_side,
                "type": order_type.upper(),
                "quantity": rounded_size
            }
            
            if reduce_only:
                params["reduceOnly"] = True
            
            if order_type.lower() == "limit":
                if not price:
                    raise ValueError("Limit order requires price")
                rounded_price = self._round_price(symbol, price)
                params["price"] = rounded_price
                params["timeInForce"] = "GTC"
                logger.info(f"Opening position: {side} {rounded_size} {symbol} @ {rounded_price} reduceOnly={reduce_only}")
            else:
                logger.info(f"Opening position: {side} {rounded_size} {symbol} @ market reduceOnly={reduce_only}")
            
            result = self.client.client.new_order(**params)
            
            order = {
                "order_id": result.get("orderId", f"sim_{datetime.now().timestamp()}"),
                "symbol": symbol,
                "side": side,
                "size": rounded_size,
                "price": price or float(result.get("avgPrice", 0) or 0),
                "type": order_type,
                "status": result.get("status", "FILLED"),
                "timestamp": datetime.now(),
                "reduce_only": reduce_only
            }
            
            self.order_history.append(order)
            
            logger.info(f"Order executed: {order['order_id']} - {order['status']}")
            
            return {
                "success": True,
                "order": order,
                "result": result
            }
            
        except Exception as e:
            logger.error(f"Open position error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }
    
    def close_position(
        self,
        symbol: str,
        position_side: str = "long",
        size: float = None,
        order_type: str = "market",
        price: float = None
    ) -> Dict:
        try:
            if str(order_type).lower() == "market":
                logger.warning("[ORDER_SUBMIT] symbol=%s reason=MARKET_CLOSE_DISABLED", symbol)
                return {"success": False, "error": "MARKET_CLOSE_DISABLED"}
            if size is not None:
                rounded_size = self._round_quantity(symbol, float(size))
                if rounded_size < 0.002 or rounded_size > 0.01:
                    logger.warning("[ORDER_SUBMIT] symbol=%s reason=SIZE_OUT_OF_RANGE size=%.6f", symbol, rounded_size)
                    return {"success": False, "error": "SIZE_OUT_OF_RANGE"}
            logger.info(f"Closing position: {symbol} side={position_side} size={size or 'all'}")
            
            if position_side.lower() == "long":
                close_side = "SELL"
            else:
                close_side = "BUY"
            
            params = {
                "symbol": symbol,
                "side": close_side,
                "type": order_type.upper(),
                "reduceOnly": True
            }
            
            if size:
                params["quantity"] = size
            
            if order_type.lower() == "limit":
                if not price:
                    raise ValueError("Limit order requires price")
                params["price"] = price
                params["timeInForce"] = "GTC"
            
            logger.info(f"Close order params: reduceOnly=True, side={close_side}")
            
            result = self.client.client.new_order(**params)
            
            order = {
                "order_id": result.get("orderId", f"sim_{datetime.now().timestamp()}"),
                "symbol": symbol,
                "side": "close",
                "size": size or 0,
                "price": price or float(result.get("avgPrice", 0) or 0),
                "type": order_type,
                "status": result.get("status", "FILLED"),
                "timestamp": datetime.now(),
                "reduce_only": True
            }
            
            self.order_history.append(order)
            
            logger.info(f"Position closed: {order['order_id']}")
            
            return {
                "success": True,
                "order": order,
                "result": result
            }
            
        except Exception as e:
            logger.error(f"Close position error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }
    
    def scale_in(
        self,
        symbol: str,
        side: str,
        entry_levels: List[float],
        sizes: List[float],
        min_price_spacing: float = None
    ) -> Dict:
        try:
            if len(entry_levels) != len(sizes):
                raise ValueError("entry_levels and sizes must have same length")
            
            # 检查交易所现有委托数量，防止无限制增加
            existing_orders = self.get_existing_orders(symbol, side)
            max_allowed_orders = 4  # 最大允许委托数量：4个
            
            if len(existing_orders) >= max_allowed_orders:
                logger.warning(f"委托数量已达上限: {len(existing_orders)}/{max_allowed_orders}，跳过新委托")
                return {
                    "success": False,
                    "error": f"委托数量已达上限: {len(existing_orders)}/{max_allowed_orders}",
                    "existing_orders_count": len(existing_orders)
                }
            
            # 优化价格间距：如果价格过于接近，合并委托
            optimized_levels, optimized_sizes = self._optimize_entry_levels(
                entry_levels, sizes, min_price_spacing
            )
            
            # 进一步限制委托数量，确保不超过最大限制
            remaining_slots = max_allowed_orders - len(existing_orders)
            if len(optimized_levels) > remaining_slots:
                logger.warning(f"委托数量限制: 优化后{len(optimized_levels)}个，但只能委托{remaining_slots}个")
                optimized_levels = optimized_levels[:remaining_slots]
                optimized_sizes = optimized_sizes[:remaining_slots]
            
            if len(optimized_levels) < len(entry_levels):
                logger.info(f"Scale-in optimized: {len(entry_levels)} -> {len(optimized_levels)} levels")
            
            logger.info(f"Scale-in: {side} {symbol} at {len(optimized_levels)} levels "
                       f"(现有{len(existing_orders)}个委托，剩余{remaining_slots}个位置)")
            
            orders = []
            total_size = 0.0
            
            for i, (price, sz) in enumerate(zip(optimized_levels, optimized_sizes)):
                # 检查是否超过最大委托数量
                if len(orders) >= remaining_slots:
                    logger.warning(f"已达到最大委托数量限制，停止委托")
                    break
                    
                result = self.open_position(
                    symbol=symbol,
                    side=side,
                    size=sz,
                    order_type="limit",
                    price=price,
                    reduce_only=False
                )
                
                if result.get("success"):
                    order = result.get("order")
                    orders.append(order)
                    total_size += order.get("size", sz)
                    self.pending_orders[order["order_id"]] = order
                    
                    if self.trade_guard:
                        self.trade_guard.record_order(symbol, order["order_id"], order)
                else:
                    logger.warning(f"Scale-in order {i+1} failed: {result.get('error')}")
            
            return {
                "success": len(orders) > 0,
                "orders": orders,
                "total_size": total_size,
                "existing_orders_count": len(existing_orders),
                "max_allowed_orders": max_allowed_orders
            }
            
        except Exception as e:
            logger.error(f"Scale-in error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _optimize_entry_levels(self, entry_levels: List[float], sizes: List[float], 
                              min_price_spacing: float = 100.0, max_levels: int = 4) -> tuple:
        """优化入口价格级别，确保严格的最小价格间距"""
        if len(entry_levels) <= 1:
            return entry_levels, sizes
        
        # 严格固定最小价格间距为100点
        min_price_spacing = 100.0
        
        # 按价格排序
        sorted_pairs = sorted(zip(entry_levels, sizes))
        
        # 如果价格范围太小，直接使用平均价格
        price_range = max(entry_levels) - min(entry_levels)
        if price_range < min_price_spacing:
            # 价格范围太小，无法满足100点间距，直接使用平均价格
            avg_price = sum(entry_levels) / len(entry_levels)
            total_size = sum(sizes)
            logger.warning(f"价格范围过小({price_range:.2f} < {min_price_spacing:.2f})，使用平均价格: {avg_price:.2f}")
            return [avg_price], [total_size]
        
        # 重新分配价格，确保严格的最小间距
        optimized_levels = []
        optimized_sizes = []
        
        # 从最低价格开始，按固定间距分配
        base_price = min(entry_levels)
        total_size = sum(sizes)
        
        # 计算可以分配的价格级别数量
        available_levels = min(max_levels, int(price_range / min_price_spacing) + 1)
        
        if available_levels <= 1:
            # 只能分配一个价格级别
            optimized_levels = [base_price + price_range / 2]  # 使用中间价格
            optimized_sizes = [total_size]
        else:
            # 分配多个价格级别，确保严格的最小间距
            for i in range(available_levels):
                price_level = base_price + i * min_price_spacing
                # 确保不超过最高价格
                if price_level > max(entry_levels):
                    break
                optimized_levels.append(price_level)
                # 按比例分配规模
                level_size = total_size / available_levels
                optimized_sizes.append(level_size)
        
        logger.info(f"Entry levels optimized: {len(entry_levels)} -> {len(optimized_levels)} "
                   f"(min_spacing={min_price_spacing:.2f}, max_levels={max_levels})")
        
        return optimized_levels, optimized_sizes
    
    def cancel_orders(self, symbol: str, order_ids: List[str] = None) -> Dict:
        try:
            cancelled = []
            
            if order_ids:
                for order_id in order_ids:
                    try:
                        # 始终调用 API 取消委托（包括测试网）
                        result = self.client.client.cancel_order(
                            symbol=symbol,
                            orderId=order_id
                        )
                        
                        if order_id in self.pending_orders:
                            del self.pending_orders[order_id]
                        
                        cancelled.append(order_id)
                        self.recently_cancelled[order_id] = datetime.now()
                        logger.info(f"Order cancelled: {order_id}")
                        logger.info("[ORDER_CANCEL] symbol=%s order=%s", symbol, order_id)
                        
                    except Exception as e:
                        logger.warning(f"Cancel order {order_id} failed: {e}")
            else:
                for order_id in list(self.pending_orders.keys()):
                    order = self.pending_orders[order_id]
                    if order.get("symbol") == symbol:
                        try:
                            # 始终调用 API 取消委托（包括测试网）
                            self.client.client.cancel_order(
                                symbol=symbol,
                                orderId=order_id
                            )
                            cancelled.append(order_id)
                            del self.pending_orders[order_id]
                            self.recently_cancelled[order_id] = datetime.now()
                            logger.info("[ORDER_CANCEL] symbol=%s order=%s", symbol, order_id)
                        except Exception as e:
                            logger.warning(f"Cancel order {order_id} failed: {e}")
            
            return {
                "success": True,
                "cancelled": cancelled
            }
            
        except Exception as e:
            logger.error(f"Cancel orders error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def set_stop_loss_take_profit(
        self,
        symbol: str,
        side: str,
        stop_loss: float,
        take_profit: float,
        quantity: float
    ) -> Dict:
        if self.testnet or settings.is_testnet:
            logger.info(f"[{symbol}] SL/TP SIMULATED (testnet): SL={stop_loss:.2f} TP={take_profit:.2f} qty={quantity}")
            logger.info(f"[{symbol}] 止盈止损本地模拟：止损={stop_loss:.2f}, 止盈={take_profit:.2f}")
            return {
                "success": True,
                "message": "SL/TP using local simulation in testnet mode",
                "mode": "local_simulation",
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }
        
        try:
            if stop_loss <= 0 or take_profit <= 0:
                logger.warning(f"[{symbol}] Invalid SL/TP values: SL={stop_loss}, TP={take_profit}")
                return {"success": False, "error": "Invalid SL/TP values"}
            
            close_side = "SELL" if side.lower() == "long" else "BUY"
            
            sl_result = None
            tp_result = None
            
            sl_price = self._round_price(symbol, stop_loss)
            result = self.client.client.new_order(
                symbol=symbol,
                side=close_side,
                type="STOP_MARKET",
                stopPrice=sl_price,
                closePosition=True
            )
            sl_result = {
                "order_id": result.get("orderId"),
                "type": "STOP_LOSS",
                "stop_price": sl_price,
                "status": result.get("status", "NEW")
            }
            logger.info(f"[{symbol}] Stop Loss order placed: {sl_price}")
            
            tp_price = self._round_price(symbol, take_profit)
            result = self.client.client.new_order(
                symbol=symbol,
                side=close_side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=tp_price,
                closePosition=True
            )
            tp_result = {
                "order_id": result.get("orderId"),
                "type": "TAKE_PROFIT",
                "stop_price": tp_price,
                "status": result.get("status", "NEW")
            }
            logger.info(f"[{symbol}] Take Profit order placed: {tp_price}")
            
            return {
                "success": True,
                "stop_loss_order": sl_result,
                "take_profit_order": tp_result
            }
            
        except Exception as e:
            logger.error(f"[{symbol}] Set SL/TP error: {e}")
            return {"success": False, "error": str(e)}
    
    def execute_decision(
        self,
        decision: Dict,
        symbol: str,
        current_price: float,
        position_state: Dict = None
    ) -> Dict:
        try:
            action = decision.get("action", "hold")
            position_state = position_state or {}
            
            if action == "hold":
                return {
                    "success": True,
                    "action": "hold",
                    "message": "No action required"
                }
            
            size = 0.0
            
            if action == "open_long":
                entry_range = decision.get("entry_range", [current_price])
                size_range = decision.get("size", [0.01])
                stop_loss = decision.get("stop_loss", 0)
                take_profit = decision.get("take_profit", 0)
                expected_hold = decision.get("expected_hold_minutes", 60)
                
                # 在执行新委托前，先取消所有挂单委托
                self.cancel_orders(symbol)
                
                if isinstance(entry_range, list) and len(entry_range) > 1:
                    sizes = size_range if isinstance(size_range, list) else [size_range[0]] * len(entry_range)
                    # 设置最小价格间距为价格范围的10%，避免委托过于密集
                    min_spacing = (max(entry_range) - min(entry_range)) * 0.1
                    result = self.scale_in(symbol, "long", entry_range, sizes, min_spacing)
                    size = sum(sizes)
                else:
                    price = entry_range[0] if isinstance(entry_range, list) else entry_range
                    size = size_range[0] if isinstance(size_range, list) else size_range
                    result = self.open_position(symbol, "long", size, "limit", price, reduce_only=False)
                
                if result.get("success"):
                    if self.trade_guard:
                        self.trade_guard.record_position_entry(
                            symbol=symbol,
                            entry_price=result.get("order", {}).get("price", current_price),
                            position_size=size,
                            side="long",
                            expected_hold_minutes=expected_hold,
                            stop_loss=stop_loss,
                            take_profit=take_profit
                        )
                    self.set_stop_loss_take_profit(symbol, "long", stop_loss, take_profit, size)
                
                return result
            
            elif action == "open_short":
                entry_range = decision.get("entry_range", [current_price])
                size_range = decision.get("size", [0.01])
                stop_loss = decision.get("stop_loss", 0)
                take_profit = decision.get("take_profit", 0)
                expected_hold = decision.get("expected_hold_minutes", 60)
                
                # 在执行新委托前，先取消所有挂单委托
                self.cancel_orders(symbol)
                
                if isinstance(entry_range, list) and len(entry_range) > 1:
                    sizes = size_range if isinstance(size_range, list) else [size_range[0]] * len(entry_range)
                    # 设置最小价格间距为价格范围的10%，避免委托过于密集
                    min_spacing = (max(entry_range) - min(entry_range)) * 0.1
                    result = self.scale_in(symbol, "short", entry_range, sizes, min_spacing)
                    size = sum(sizes)
                else:
                    price = entry_range[0] if isinstance(entry_range, list) else entry_range
                    size = size_range[0] if isinstance(size_range, list) else size_range
                    result = self.open_position(symbol, "short", size, "limit", price, reduce_only=False)
                
                if result.get("success"):
                    if self.trade_guard:
                        self.trade_guard.record_position_entry(
                            symbol=symbol,
                            entry_price=result.get("order", {}).get("price", current_price),
                            position_size=size,
                            side="short",
                            expected_hold_minutes=expected_hold,
                            stop_loss=stop_loss,
                            take_profit=take_profit
                        )
                    self.set_stop_loss_take_profit(symbol, "short", stop_loss, take_profit, size)
                
                return result
            
            elif action == "add_position":
                entry_range = decision.get("entry_range", [current_price])
                size_range = decision.get("size", [0.01])
                position_side = position_state.get("side", "long")
                stop_loss = decision.get("stop_loss", 0)
                take_profit = decision.get("take_profit", 0)
                
                price = entry_range[0] if isinstance(entry_range, list) else entry_range
                size = size_range[0] if isinstance(size_range, list) else size_range
                result = self.open_position(symbol, position_side, size, "limit", price, reduce_only=False)
                
                if result.get("success") and self.trade_guard:
                    self.trade_guard.update_position_metadata(
                        symbol=symbol,
                        position_size=position_state.get("position_size", 0) + size,
                        stop_loss=stop_loss,
                        take_profit=take_profit
                    )
                
                return result
            
            elif action == "close_position":
                if self.trade_guard:
                    self.trade_guard.clear_position(symbol)
                
                position_side = position_state.get("side", "long")
                position_size = position_state.get("position_size", 0)
                return self.close_position(symbol, position_side, position_size)
            
            elif action == "reverse_position":
                if self.trade_guard:
                    self.trade_guard.clear_position(symbol)
                
                position_side = position_state.get("side", "long")
                position_size = position_state.get("position_size", 0)
                
                close_result = self.close_position(symbol, position_side, position_size)
                
                if close_result.get("success"):
                    new_side = "short" if position_side == "long" else "long"
                    entry_range = decision.get("entry_range", [current_price])
                    size_range = decision.get("size", [0.01])
                    stop_loss = decision.get("stop_loss", 0)
                    take_profit = decision.get("take_profit", 0)
                    expected_hold = decision.get("expected_hold_minutes", 60)
                    
                    price = entry_range[0] if isinstance(entry_range, list) else entry_range
                    size = size_range[0] if isinstance(size_range, list) else size_range
                    
                    open_result = self.open_position(symbol, new_side, size, "limit", price, reduce_only=False)
                    
                    if open_result.get("success") and self.trade_guard:
                        self.trade_guard.record_position_entry(
                            symbol=symbol,
                            entry_price=open_result.get("order", {}).get("price", current_price),
                            position_size=size,
                            side=new_side,
                            expected_hold_minutes=expected_hold,
                            stop_loss=stop_loss,
                            take_profit=take_profit
                        )
                    
                    return {
                        "success": open_result.get("success", False),
                        "action": "reverse_position",
                        "close_result": close_result,
                        "open_result": open_result
                    }
                else:
                    return close_result
            
            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}"
                }
            
        except Exception as e:
            logger.error(f"Execute decision error: {e}")
            import traceback
            traceback.print_exc()
    
    def execute_intelligent_decision(
        self,
        decision: Dict,
        symbol: str,
        current_price: float,
        position_state: Dict = None
    ) -> Dict:
        """智能执行交易决策
        
        Args:
            decision: AI决策
            symbol: 交易对
            current_price: 当前价格
            position_state: 仓位状态
            
        Returns:
            执行结果
        """
        try:
            action = decision.get("action", "hold")
            position_state = position_state or {}
            
            if action == "hold":
                return {
                    "success": True,
                    "action": "hold",
                    "message": "No action required"
                }
            
            # 获取目标仓位大小（由 TargetPositionEngine 传入）
            target_size = decision.get("target_size", decision.get("size", [0.01]))
            if isinstance(target_size, list):
                target_size = target_size[0] if target_size else 0.01
            target_size = float(target_size or 0.01)
            logger.info(f"TARGET_POSITION_UPDATE: {symbol} target_size={target_size:.4f}")
            
            # 获取已成交数量
            filled_size = position_state.get("position_size", 0)
            
            # 获取未成交订单
            pending_orders = [order for order in self.pending_orders.values() 
                            if order.get("symbol") == symbol]
            
            # 计算仓位进度
            progress_info = self.execution_planner.calculate_position_progress(
                symbol, target_size, filled_size, pending_orders
            )
            
            # 检查是否达到目标仓位（但平仓操作除外）
            if progress_info["target_reached"] and action not in ["close_position", "reverse_position"]:
                logger.info(f"ORDER_BLOCKED_TARGET_REACHED: {symbol} - 目标仓位已达成")
                return {
                    "success": True,
                    "action": "hold",
                    "message": "Target position reached"
                }
            
            # 检查是否可以继续交易
            if not progress_info["can_trade"]:
                logger.info(f"ORDER_BLOCKED_TARGET_REACHED: {symbol} - 剩余数量不足最小交易单位")
                return {
                    "success": True,
                    "action": "hold",
                    "message": "Remaining size below minimum trade size"
                }
            
            # 委托管理前置巡检（主周期内也会巡检，这里做执行前最终校验）
            if not self.order_manager.can_create_orders(symbol):
                return {"success": True, "action": "hold", "message": "cancel cooldown active"}
            try:
                self.order_manager.inspect_all_orders(symbol, intended_side=("BUY" if action in ["open_long", "add_position"] else "SELL" if action in ["open_short"] else None))
                self.order_manager.auto_cleanup_orders(symbol)
            except Exception as e:
                logger.warning(f"OrderManager pre-check warning: {e}")

            # 根据不同的action执行相应的逻辑
            if action in ["open_long", "open_short"]:
                return self._execute_open_position(
                    action, decision, symbol, current_price, progress_info
                )
            elif action == "add_position":
                return self._execute_add_position(
                    decision, symbol, current_price, position_state, progress_info
                )
            elif action == "close_position":
                return self._execute_close_position(
                    decision, symbol, position_state
                )
            elif action == "reverse_position":
                return self._execute_reverse_position(
                    decision, symbol, current_price, position_state
                )
            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}"
                }
            
        except Exception as e:
            logger.error(f"Execute intelligent decision error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }
    
    def _execute_open_position(
        self,
        action: str,
        decision: Dict,
        symbol: str,
        current_price: float,
        progress_info: Dict
    ) -> Dict:
        """执行开仓操作"""
        # 在执行新委托前，检查是否有未成交订单
        # 如果有未成交订单，先检查是否需要调整
        if progress_info.get("has_pending", False):
            logger.info(f"检测到未成交订单，检查是否需要调整")
            # 如果有未成交订单，不再重复开仓，等待成交或超时
            return {
                "success": True,
                "action": "hold",
                "message": "等待未成交订单执行"
            }
        
        # 获取目标仓位大小
        target_size = progress_info["target_size"]
        remaining_size = progress_info["remaining_size"]
        
        # 生成分批委托订单
        side = "long" if action == "open_long" else "short"
        base_price = decision.get("entry_range", [current_price])[0]
        
        orders = self.execution_planner.generate_split_orders(
            symbol, side, target_size, base_price, remaining_size, current_price=current_price
        )
        
        if not orders:
            return {
                "success": False,
                "error": "No valid orders generated"
            }
        
        # 执行委托
        executed_orders = []
        for order in orders:
            result = self.open_position(
                symbol=order["symbol"],
                side=order["side"],
                size=order["size"],
                order_type=order["order_type"],
                price=order["price"],
                reduce_only=order["reduce_only"]
            )
            
            if result.get("success"):
                executed_orders.append(result["order"])
                # 添加到挂单列表
                self.pending_orders[result["order"]["order_id"]] = result["order"]
        
        # 设置止损止盈
        stop_loss = decision.get("stop_loss", 0)
        take_profit = decision.get("take_profit", 0)
        expected_hold = decision.get("expected_hold_minutes", 60)
        
        if executed_orders and self.trade_guard:
            self.trade_guard.record_position_entry(
                symbol=symbol,
                entry_price=base_price,
                position_size=target_size,
                side=side,
                expected_hold_minutes=expected_hold,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
        
        self.set_stop_loss_take_profit(symbol, side, stop_loss, take_profit, target_size)
        
        return {
            "success": len(executed_orders) > 0,
            "action": action,
            "orders": executed_orders,
            "total_size": sum(order["size"] for order in executed_orders)
        }
    
    def _execute_add_position(
        self,
        decision: Dict,
        symbol: str,
        current_price: float,
        position_state: Dict,
        progress_info: Dict
    ) -> Dict:
        """执行加仓操作"""
        # 委托前检查：确保在委托执行前进行检查
        
        # 1. 检查现有委托数量
        existing_orders = self.get_existing_orders(symbol)
        max_allowed_orders = 4
        
        if len(existing_orders) >= max_allowed_orders:
            logger.warning(f"委托前检查失败: 委托数量已达上限 {len(existing_orders)}/{max_allowed_orders}")
            return {
                "success": False,
                "action": "add_position",
                "error": f"委托数量已达上限: {len(existing_orders)}/{max_allowed_orders}"
            }
        
        # 2. 获取目标仓位大小
        target_size = progress_info["target_size"]
        remaining_size = progress_info["remaining_size"]
        
        # 3. 生成分批委托订单
        side = position_state.get("side", "long")
        base_price = decision.get("entry_range", [current_price])[0]
        
        orders = self.execution_planner.generate_split_orders(
            symbol, side, target_size, base_price, remaining_size, current_price=current_price
        )
        
        if not orders:
            return {
                "success": False,
                "error": "No valid orders generated"
            }
        
        # 4. 委托前价格检查：确保严格的价格间距≥100点
        if len(orders) > 1:
            prices = [order["price"] for order in orders]
            
            # 使用严格的价格优化算法
            optimized_prices, optimized_sizes = self._optimize_entry_levels(
                prices, [order["size"] for order in orders], min_price_spacing=100.0
            )
            
            # 检查优化后的价格间距
            if len(optimized_prices) > 1:
                min_spacing = min([abs(optimized_prices[i+1] - optimized_prices[i]) for i in range(len(optimized_prices)-1)])
                if min_spacing < 100.0:
                    logger.error(f"价格优化失败: 间距仍然过小 {min_spacing:.2f} < 100.0")
                    # 强制使用单一价格
                    avg_price = sum(optimized_prices) / len(optimized_prices)
                    total_size = sum(optimized_sizes)
                    optimized_prices = [avg_price]
                    optimized_sizes = [total_size]
                    logger.warning(f"强制使用单一价格: {avg_price:.2f}")
            
            # 重新生成订单
            orders = []
            for i, (price, size) in enumerate(zip(optimized_prices, optimized_sizes)):
                orders.append({
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "order_type": "limit",
                    "price": price,
                    "reduce_only": False
                })
        
        # 5. 执行委托（只有在检查通过后才执行）
        executed_orders = []
        for order in orders:
            # 检查是否超过最大委托数量
            if len(executed_orders) >= (max_allowed_orders - len(existing_orders)):
                logger.warning(f"委托执行停止: 已达到最大委托数量限制")
                break
                
            result = self.open_position(
                symbol=order["symbol"],
                side=order["side"],
                size=order["size"],
                order_type=order["order_type"],
                price=order["price"],
                reduce_only=order["reduce_only"]
            )
            
            if result.get("success"):
                executed_orders.append(result["order"])
                # 添加到挂单列表
                self.pending_orders[result["order"]["order_id"]] = result["order"]
        
        # 更新仓位元数据
        if executed_orders and self.trade_guard:
            stop_loss = decision.get("stop_loss", 0)
            take_profit = decision.get("take_profit", 0)
            
            self.trade_guard.update_position_metadata(
                symbol=symbol,
                position_size=target_size,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
        
        return {
            "success": len(executed_orders) > 0,
            "action": "add_position",
            "orders": executed_orders,
            "added_size": remaining_size
        }
    
    def _execute_close_position(
        self,
        decision: Dict,
        symbol: str,
        position_state: Dict
    ) -> Dict:
        """执行平仓操作"""
        # 检查最小持仓时间约束
        entry_time = position_state.get("entry_time")
        current_time = datetime.now()
        
        if not self.execution_planner.check_hold_time_constraint(entry_time, current_time):
            return {
                "success": False,
                "error": "Minimum hold time not reached"
            }
        
        # 取消所有挂单委托
        self.cancel_orders(symbol)
        
        # 执行平仓
        position_side = position_state.get("side", "long")
        position_size = position_state.get("position_size", 0)
        
        result = self.close_position(symbol, position_side, position_size)
        
        if result.get("success") and self.trade_guard:
            self.trade_guard.clear_position(symbol)
        
        return result
    
    def _execute_reverse_position(
        self,
        decision: Dict,
        symbol: str,
        current_price: float,
        position_state: Dict
    ) -> Dict:
        """执行反向开仓操作"""
        # 先平仓
        close_result = self._execute_close_position(decision, symbol, position_state)
        
        if not close_result.get("success"):
            return close_result
        
        # 再反向开仓
        position_side = position_state.get("side", "long")
        new_side = "short" if position_side == "long" else "long"
        
        # 修改action为开仓操作
        decision["action"] = "open_long" if new_side == "long" else "open_short"
        
        # 使用智能开仓逻辑
        return self._execute_open_position(
            decision["action"], decision, symbol, current_price, {
                "target_size": float(decision.get("target_size", decision.get("size", [0.01])[0] if isinstance(decision.get("size", [0.01]), list) else decision.get("size", 0.01))),
                "remaining_size": decision.get("size", [0.01])[0],
                "target_reached": False,
                "can_trade": True
            }
        )
    
    def get_existing_orders(self, symbol: str, side: str = None, price_tolerance: float = 0.01) -> list:
        """查询交易所现有订单，避免重复委托"""
        try:
            # 查询交易所活跃订单
            exchange_orders = self.client.get_open_orders(symbol)
            
            # 调试信息：显示原始订单数据
            logger.debug(f"交易所返回 {len(exchange_orders)} 个原始订单")
            for i, order in enumerate(exchange_orders):
                logger.debug(f"订单{i+1}: {order}")
            
            # 过滤条件：同方向、价格相近的订单
            similar_orders = []
            for order in exchange_orders:
                # 检查订单状态，只处理活跃订单
                status = order.get("status", "").upper()
                if status not in ["NEW", "PARTIALLY_FILLED"]:
                    continue
                    
                order_side = order.get("side", "").upper()
                order_price = float(order.get("price", 0))
                
                # 如果指定了方向，只检查同方向订单
                if side and order_side != side.upper():
                    continue
                    
                similar_orders.append({
                    "order_id": order.get("orderId"),
                    "side": order_side,
                    "price": order_price,
                    "quantity": float(order.get("origQty", 0)),
                    "status": status,
                    "time": order.get("time", 0)
                })
            
            logger.debug(f"查询到 {len(similar_orders)} 个活跃订单")
            return similar_orders
            
        except Exception as e:
            logger.error(f"查询现有订单失败：{e}")
            return []
    
    def get_current_price(self, symbol: str) -> float:
        """获取当前市场价格"""
        try:
            if hasattr(self, 'market_data') and self.market_data:
                maybe = self.market_data.get('price', 0) if isinstance(self.market_data, dict) else 0
                if maybe:
                    return float(maybe)

            ticker = self.client.client.ticker_price(symbol=symbol)
            return float(ticker.get('price', 0) or 0)
        except Exception as e:
            logger.error(f"获取当前价格失败：{e}")
            return 0.0
    
    def check_duplicate_orders(self, symbol: str, side: str, price: float, 
                             size: float, price_tolerance: float = 5.0,
                             check_price_only: bool = False) -> bool:
        """
        检查是否存在重复委托
        
        Args:
            check_price_only: 如果为 True，只检查价格是否相同（不管数量）
                             如果为 False，检查价格和数量都相似
        """
        try:
            existing_orders = self.get_existing_orders(symbol, side)
            
            for order in existing_orders:
                order_id = str(order.get("order_id", ""))
                if order_id and order_id in self.recently_cancelled:
                    elapsed = (datetime.now() - self.recently_cancelled[order_id]).total_seconds()
                    if elapsed < 30:
                        continue
                order_price = order.get("price", 0)
                order_size = order.get("quantity", 0)
                
                if check_price_only:
                    # 只检查价格是否相同（更严格的重复检查）
                    if abs(float(order_price) - float(price)) < 0.5:  # 价格差异 < 0.5 USDT
                        return True
                else:
                    # 检查价格是否接近（价格容差范围内）
                    price_diff_pct = abs(float(order_price) - float(price))
                    
                    # 检查数量和价格是否相似
                    if (price_diff_pct <= price_tolerance and 
                        abs(order_size - size) / max(order_size, size) <= 0.1):
                        logger.warning(f"发现重复委托: 价格={order_price:.2f} vs {price:.2f}, "
                                 f"数量={order_size:.4f} vs {size:.4f}")
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查重复委托失败：{e}")
            return False
    
    def check_exposure_limit(self, symbol: str, side: str, new_size: float) -> Dict:
        """
        检查暴露量限制：持仓 + 未成交委托 + 新委托 <= 0.02 BTC
        
        返回：
        - allowed: 是否允许委托
        - available_size: 还可委托的数量
        - reason: 原因说明
        """
        try:
            # 获取当前持仓
            position = self.client.get_position(symbol)
            # 使用 position_amt 获取持仓量（做多时为正，做空时为负）
            current_position = abs(position.get('position_amt', 0)) if position else 0
            
            # 获取现有未成交委托
            existing_orders = self.get_existing_orders(symbol, side)
            existing_orders_size = sum(order.get("quantity", 0) for order in existing_orders)
            
            # 最大持仓限制
            max_position_size = getattr(settings, 'max_position_size', 0.02)
            
            # 计算总暴露量
            total_exposure = current_position + existing_orders_size + new_size
            
            # 计算可用额度
            available_size = max(0, max_position_size - current_position - existing_orders_size)
            
            if total_exposure > max_position_size:
                logger.warning(
                    f"暴露量超限：持仓={current_position:.4f}, 现有委托={existing_orders_size:.4f}, "
                    f"新委托={new_size:.4f}, 总计={total_exposure:.4f}, 限制={max_position_size:.4f}, "
                    f"可用={available_size:.4f}"
                )
                return {
                    "allowed": False,
                    "available_size": available_size,
                    "reason": f"暴露量超限：总计{total_exposure:.4f} > 限制{max_position_size:.4f}"
                }
            
            return {
                "allowed": True,
                "available_size": available_size,
                "reason": f"暴露量检查通过：总计{total_exposure:.4f} <= 限制{max_position_size:.4f}"
            }
            
        except Exception as e:
            logger.error(f"检查暴露量失败：{e}")
            return {
                "allowed": False,
                "available_size": 0,
                "reason": f"检查失败：{str(e)}"
            }
    
    def cleanup_excessive_orders(self, symbol: str, side: str = None, max_orders: int = 4) -> Dict:
        """清理过多的委托订单"""
        try:
            existing_orders = self.get_existing_orders(symbol, side)
            
            if len(existing_orders) <= max_orders:
                return {
                    "success": True,
                    "cleaned": [],
                    "excessive_orders_count": 0,
                    "message": f"委托数量正常: {len(existing_orders)}/{max_orders}"
                }
            
            # 按价格排序，保留最重要的几个委托
            existing_orders.sort(key=lambda x: x.get("price", 0))
            
            # 保留前max_orders个委托，取消多余的
            orders_to_keep = existing_orders[:max_orders]
            orders_to_cancel = existing_orders[max_orders:]
            
            cancelled = []
            for order in orders_to_cancel:
                order_id = order.get("order_id")
                result = self.cancel_orders(symbol, [order_id])
                if result.get("success"):
                    cancelled.append(order_id)
                    logger.warning(f"清理过多委托: {order.get('side')} {order.get('quantity', 0):.4f} @ {order.get('price', 0):.2f}")
            
            return {
                "success": True,
                "cleaned": cancelled,
                "excessive_orders_count": len(orders_to_cancel),
                "message": f"清理了{len(cancelled)}个过多委托，保留{len(orders_to_keep)}个"
            }
            
        except Exception as e:
            logger.error(f"清理过多委托失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "cleaned": []
            }
    
    def cleanup_stale_orders(self, symbol: str) -> Dict:
        """清理超时订单"""
        current_time = datetime.now()
        pending_orders = [order for order in self.pending_orders.values() 
                        if order.get("symbol") == symbol]
        
        stale_orders = self.execution_planner.cancel_stale_orders(pending_orders, current_time)
        
        cancelled = []
        for order in stale_orders:
            result = self.cancel_orders(symbol, [order.get("order_id")])
            if result.get("success"):
                cancelled.extend(result.get("cancelled", []))
        
        return {
            "success": True,
            "cancelled": cancelled,
            "stale_orders_count": len(stale_orders)
        }
    
    def format_execution_log(self, result: Dict, symbol: str) -> str:
        try:
            if not result.get("success"):
                return f"Execution: FAILED - {result.get('error', 'Unknown error')}"
            
            action = result.get("action", "executed")
            order = result.get("order")
            orders = result.get("orders", [])
            
            if order:
                return (
                    f"Execution: {order.get('side', 'N/A').upper()} "
                    f"{order.get('size', 0):.4f} @ {order.get('price', 0):.2f} "
                    f"reduceOnly={order.get('reduce_only', False)}"
                )
            elif orders:
                total_size = sum(o.get("size", 0) for o in orders)
                return f"Execution: Scale-in {len(orders)} orders, total={total_size:.4f}"
            else:
                return f"Execution: {action}"
                
        except Exception as e:
            return f"Execution: Error - {e}"
