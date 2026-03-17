import requests
from binance.um_futures import UMFutures
from core.logger import logger


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str, socks5_proxy: str = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.socks5_proxy = socks5_proxy
        
        self.proxies = None
        if socks5_proxy:
            proxy_url = socks5_proxy
            if proxy_url.startswith("socks5://") and not proxy_url.startswith("socks5h://"):
                proxy_url = proxy_url.replace("socks5://", "socks5h://")
            
            self.proxies = {
                "http": proxy_url,
                "https": proxy_url
            }
            logger.info(f"SOCKS5 proxy configured: {proxy_url}")
        
        self._client = self._create_client()
        logger.info(f"Binance client initialized with base URL: {base_url}")
    
    def _create_client(self) -> UMFutures:
        client_params = {
            "key": self.api_key,
            "secret": self.api_secret,
            "base_url": self.base_url
        }
        
        if self.proxies:
            client_params["proxies"] = self.proxies
        
        return UMFutures(**client_params)
    
    @property
    def client(self) -> UMFutures:
        return self._client
    
    def get_server_time(self) -> int:
        try:
            return self._client.server_time()
        except Exception as e:
            logger.error(f"Failed to get server time: {e}")
            return 0
    
    def klines(self, symbol: str, interval: str, limit: int = 200):
        try:
            return self._client.klines(symbol=symbol, interval=interval, limit=limit)
        except Exception as e:
            logger.error(f"Failed to get klines: {e}")
            raise
    
    def get_open_orders(self, symbol: str = None) -> list:
        """获取当前活跃订单"""
        try:
            # 添加详细的调试信息
            logger.debug(f"开始查询活跃订单，交易对：{symbol if symbol else '全部'}")
            
            # 检查 API 连接状态
            if not self._client:
                logger.error("Binance 客户端未初始化")
                return []
            
            # 使用正确的 API 调用方式 - 查询活跃订单
            if symbol:
                # 确保 symbol 格式正确
                symbol = symbol.upper().replace("/", "")
                logger.debug(f"查询指定交易对活跃订单：{symbol}")
                
                # 使用 get_all_orders 方法查询所有订单
                limit = 10
                all_orders = self._client.get_all_orders(symbol=symbol, limit=limit)
                all_orders = list(all_orders)[:limit]
                logger.debug(f"API 调用成功，limit={limit} 返回 {len(all_orders)} 个订单")
                
                # 过滤出活跃订单（NEW 或 PARTIALLY_FILLED 状态）
                orders = [order for order in all_orders if order.get('status') in ['NEW', 'PARTIALLY_FILLED']]
                logger.debug(f"活跃订单数量：{len(orders)}")
            else:
                logger.warning("查询所有交易对订单 - 不支持的操作")
                orders = []

            # 详细记录订单信息
            logger.debug(f"交易所 API 返回 {len(orders)} 个活跃订单")
            for i, order in enumerate(orders):
                logger.debug(f"订单{i+1}: ID={order.get('orderId')}, 方向={order.get('side')}, "
                           f"价格={order.get('price')}, 数量={order.get('origQty')}, "
                           f"状态={order.get('status')}")

            return orders
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            # 添加更详细的错误信息
            logger.error(f"错误详情：{type(e).__name__}, 参数：symbol={symbol}")
            return []
    
    def get_order_by_id(self, symbol: str, order_id: str) -> dict:
        """根据订单 ID 查询订单详情"""
        try:
            order = self._client.query_order(symbol=symbol, orderId=order_id)
            return order
        except Exception as e:
            logger.error(f"Failed to get order by ID: {e}")
            return {}
    
    def cancel_orders(self, symbol: str, order_ids: list) -> dict:
        """批量取消订单"""
        try:
            results = []
            for order_id in order_ids:
                result = self._client.cancel_order(symbol=symbol, orderId=order_id)
                results.append(result)
            
            logger.info(f"成功取消 {len(results)} 个订单")
            return {"success": True, "cancelled": results}
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")
            return {"success": False, "error": str(e)}
    
    def cancel_all_orders(self, symbol: str) -> dict:
        """取消指定交易对的所有活跃订单"""
        try:
            result = self._client.cancel_all_orders(symbol=symbol)
            logger.info(f"成功取消所有订单：{symbol}")
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return {"success": False, "error": str(e)}
    
    def place_order(self, symbol: str, side: str, order_type: str, 
                   quantity: float, price: float = None, **kwargs) -> dict:
        """下单"""
        try:
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": order_type.upper(),
                "quantity": quantity
            }
            
            if price:
                params["price"] = price
            
            params.update(kwargs)
            
            result = self._client.place_order(**params)
            logger.info(f"Order placed: {symbol} {side} {quantity} @ {price or 'MARKET'}")
            return result
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return {}
    
    def get_position(self, symbol: str) -> dict:
        """获取持仓信息"""
        try:
            account_info = self._client.account()
            positions = account_info.get("positions", [])
            
            for position in positions:
                if position.get("symbol") == symbol:
                    return {
                        "symbol": position.get("symbol"),
                        "position_amt": float(position.get("positionAmt", 0)),
                        "entry_price": float(position.get("entryPrice", 0)),
                        "mark_price": float(position.get("markPrice", 0)),
                        "unrealized_profit": float(position.get("unRealizedProfit", 0)),
                        "leverage": int(position.get("leverage", 1))
                    }
            
            return {"symbol": symbol, "position_amt": 0, "entry_price": 0, 
                   "mark_price": 0, "unrealized_profit": 0, "leverage": 1}
        except Exception as e:
            logger.error(f"Failed to get position: {e}")
            return {}
    
    def get_account_balance(self) -> dict:
        """获取账户余额"""
        try:
            account_info = self._client.account()
            return account_info
        except Exception as e:
            logger.error(f"Failed to get account balance: {e}")
            return {}
