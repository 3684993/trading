"""OrderManager - 统一管理委托订单生命周期。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger("ai_quant_trader")


class OrderManager:
    """委托管理模块。

    规则：
    1) 最大委托数量=4
    2) 价格间隔：10 <= 间距 <= 100 USDT
    3) 检查委托方向
    4) 检查委托与当前价格距离
    5) 取消过期订单
    6) 根据趋势判断是否取消委托
    """

    def __init__(self, order_executor, market_analyzer=None, order_timeout_minutes: int = 60):
        self.order_executor = order_executor
        self.market_analyzer = market_analyzer
        self.max_orders_per_symbol = 4
        self.min_price_spacing = 10.0   # 最小间距 10 USDT
        self.max_price_spacing = 100.0  # 最大间距 100 USDT
        self.max_price_distance_ratio = 0.02
        self.order_timeout = timedelta(minutes=order_timeout_minutes)
        self.inspection_records: Dict[str, Dict] = {}
        self.trend_check_enabled = True  # 启用趋势判断

    def inspect_all_orders(self, symbol: str, intended_side: Optional[str] = None) -> Dict:
        orders = self.order_executor.get_existing_orders(symbol) or []

        inspections = {
            "quantity_check": self._check_max_orders(orders),
            "spacing_check": self._check_price_spacing(orders),
            "direction_check": self._check_direction(orders, intended_side),
            "distance_check": self._check_price_distance(symbol, orders),
            "expiry_check": self._check_expiry(orders),
            "trend_check": self._check_trend_alignment(symbol, orders) if self.trend_check_enabled else {"status": "跳过", "message": "趋势检查已禁用"},
        }

        recommendations = self._build_recommendations(inspections)
        result = {
            "success": True,
            "symbol": symbol,
            "total_orders": len(orders),
            "inspections": inspections,
            "recommendations": recommendations,
        }
        self.inspection_records[symbol] = {"timestamp": datetime.now(), **result}
        return result

    def auto_cleanup_orders(self, symbol: str) -> Dict:
        """自动清理不合理委托：过期、过远、重复、间距过近"""
        report = self.inspect_all_orders(symbol)
        if not report.get("success"):
            return {"success": False, "symbol": symbol, "error": "inspect failed"}

        # 1. 过期订单
        expired_ids = report["inspections"].get("expiry_check", {}).get("expired_order_ids", [])
        
        # 2. 距离过远订单
        far_ids = report["inspections"].get("distance_check", {}).get("too_far_order_ids", [])
        
        # 3. 重复委托检查（新增）
        duplicate_ids = self._find_duplicate_orders(symbol, report.get("managed_orders", []))
        
        # 4. 间距过近委托（新增）
        close_spacing_ids = self._find_close_spacing_orders(symbol, report.get("managed_orders", []))
        
        # 合并所有需要取消的订单（去重）
        to_cancel = list(dict.fromkeys(expired_ids + far_ids + duplicate_ids + close_spacing_ids))
        
        cleanup_actions: List[str] = []
        if to_cancel:
            self.order_executor.cancel_orders(symbol, to_cancel)
            cleanup_actions.append(f"取消{len(to_cancel)}个订单")
            
            # 记录清理原因
            if expired_ids:
                cleanup_actions.append(f"过期：{len(expired_ids)}个")
            if far_ids:
                cleanup_actions.append(f"过远：{len(far_ids)}个")
            if duplicate_ids:
                cleanup_actions.append(f"重复：{len(duplicate_ids)}个")
            if close_spacing_ids:
                cleanup_actions.append(f"间距过近：{len(close_spacing_ids)}个")

        return {
            "success": True,
            "symbol": symbol,
            "cleanup_actions": cleanup_actions,
            "recommendations": report.get("recommendations", []),
        }
    
    def _find_duplicate_orders(self, symbol: str, orders: List[Dict]) -> List[str]:
        """查找重复委托（价格相同）"""
        if len(orders) <= 1:
            return []
        
        duplicate_ids = []
        price_map = {}  # price -> [order_ids]
        
        for order in orders:
            price = float(order.get("price", 0) or 0)
            order_id = order.get("order_id", "unknown")
            
            if price in price_map:
                # 发现重复价格，保留第一个，标记其他的为重复
                price_map[price].append(order_id)
                duplicate_ids.append(order_id)
                logger.warning(f"[CLEANUP] 发现重复价格委托：{order_id} @ {price:.2f}")
            else:
                price_map[price] = [order_id]
        
        return duplicate_ids
    
    def _find_close_spacing_orders(self, symbol: str, orders: List[Dict]) -> List[str]:
        """查找间距过近的委托（< 10 USDT）"""
        if len(orders) <= 1:
            return []
        
        # 按价格排序
        sorted_orders = sorted(orders, key=lambda x: float(x.get("price", 0) or 0))
        
        close_ids = []
        for i in range(len(sorted_orders) - 1):
            price1 = float(sorted_orders[i].get("price", 0) or 0)
            price2 = float(sorted_orders[i + 1].get("price", 0) or 0)
            spacing = abs(price2 - price1)
            
            if spacing < self.min_price_spacing:  # < 10 USDT
                # 标记价格较低的那个委托（保留价格较高的）
                order_id = sorted_orders[i].get("order_id", "unknown")
                if order_id not in close_ids:
                    close_ids.append(order_id)
                    logger.warning(f"[CLEANUP] 发现间距过近委托：{order_id} @ {price1:.2f} (间距={spacing:.2f})")
        
        return close_ids

    def _check_max_orders(self, orders: List[Dict]) -> Dict:
        count = len(orders)
        ok = count <= self.max_orders_per_symbol
        return {
            "status": "正常" if ok else "过多",
            "order_count": count,
            "max_allowed": self.max_orders_per_symbol,
            "within_limit": ok,
            "message": f"委托数量 {count}/{self.max_orders_per_symbol}",
        }

    def _check_price_spacing(self, orders: List[Dict]) -> Dict:
        """
        检查委托价格间距：10 <= 间距 <= 100 USDT
        
        规则：
        - 如果只有 0-1 个委托，跳过检查
        - 最小间距 >= 10 USDT（防止委托过于密集）
        - 最大间距 <= 100 USDT（防止委托过于分散）
        """
        if len(orders) <= 1:
            return {
                "status": "正常", 
                "meets_requirement": True, 
                "min_spacing": 0.0, 
                "max_spacing": 0.0,
                "min_required": self.min_price_spacing,
                "max_required": self.max_price_spacing,
                "message": "不足两笔，跳过"
            }

        # 按价格排序
        prices = sorted(float(order.get("price", 0.0)) for order in orders)
        
        # 计算所有相邻价格的间距
        spacings = [abs(prices[i + 1] - prices[i]) for i in range(len(prices) - 1)]
        min_spacing = min(spacings)
        max_spacing = max(spacings)
        avg_spacing = sum(spacings) / len(spacings)
        
        # 检查间距是否在合理范围内
        min_ok = min_spacing >= self.min_price_spacing
        max_ok = max_spacing <= self.max_price_spacing
        all_ok = min_ok and max_ok
        
        if not min_ok and not max_ok:
            status = "间距过小且过大"
        elif not min_ok:
            status = "间距过小"
        elif not max_ok:
            status = "间距过大"
        else:
            status = "正常"
        
        return {
            "status": status,
            "meets_requirement": all_ok,
            "min_spacing": min_spacing,
            "max_spacing": max_spacing,
            "avg_spacing": avg_spacing,
            "min_required": self.min_price_spacing,
            "max_required": self.max_price_spacing,
            "message": f"间距范围 [{min_spacing:.2f}, {max_spacing:.2f}], 要求 [{self.min_price_spacing:.2f}, {self.max_price_spacing:.2f}]",
            "violation_details": {
                "too_close": not min_ok,
                "too_far": not max_ok
            }
        }

    def _check_direction(self, orders: List[Dict], intended_side: Optional[str]) -> Dict:
        if not orders:
            return {"status": "正常", "direction_consistent": True, "message": "无挂单"}
        if not intended_side:
            return {"status": "跳过", "direction_consistent": True, "message": "未提供目标方向"}

        intended = intended_side.upper()
        mismatched = [o.get("order_id", "unknown") for o in orders if str(o.get("side", "")).upper() != intended]
        ok = len(mismatched) == 0
        return {
            "status": "正常" if ok else "方向不一致",
            "direction_consistent": ok,
            "intended_side": intended,
            "mismatched_order_ids": mismatched,
            "message": "方向一致" if ok else f"{len(mismatched)}个方向不一致",
        }

    def _check_price_distance(self, symbol: str, orders: List[Dict]) -> Dict:
        current_price = self.order_executor.get_current_price(symbol)
        if not current_price:
            return {"status": "跳过", "message": "无法获取最新价", "too_far_order_ids": []}

        too_far: List[str] = []
        analysis: List[Dict] = []
        for order in orders:
            op = float(order.get("price", 0.0))
            distance = abs(op - current_price) / current_price if current_price else 0.0
            oid = order.get("order_id", "unknown")
            over = distance > self.max_price_distance_ratio
            if over:
                too_far.append(oid)
            analysis.append({"order_id": oid, "distance_ratio": distance, "too_far": over})

        return {
            "status": "正常" if not too_far else "距离过远",
            "current_price": current_price,
            "threshold": self.max_price_distance_ratio,
            "analysis": analysis,
            "too_far_order_ids": too_far,
            "message": "价格距离正常" if not too_far else f"{len(too_far)}个距离过远",
        }

    def _check_expiry(self, orders: List[Dict]) -> Dict:
        now = datetime.now()
        expired: List[str] = []
        for order in orders:
            order_time = self._parse_order_time(order.get("time") or order.get("timestamp"))
            if order_time and now - order_time > self.order_timeout:
                expired.append(order.get("order_id", "unknown"))

        return {
            "status": "正常" if not expired else "存在过期委托",
            "expired_order_ids": expired,
            "message": "无过期订单" if not expired else f"{len(expired)}个过期订单",
        }
    
    def _check_trend_alignment(self, symbol: str, orders: List[Dict]) -> Dict:
        """
        根据趋势判断是否要取消委托订单
        
        逻辑：
        1. 获取当前市场趋势（通过 market_analyzer 或 order_executor）
        2. 检查委托方向与趋势是否一致
        3. 如果委托方向与趋势相反且超过一定时间，建议取消
        4. 如果趋势不明朗（震荡），保留所有委托
        """
        if not orders:
            return {"status": "正常", "message": "无挂单", "recommend_cancel": []}
        
        try:
            # 尝试获取市场趋势
            trend_info = self._get_market_trend(symbol)
            
            if not trend_info or trend_info.get("trend") == "volatile":
                # 震荡市场，保留所有委托
                return {
                    "status": "跳过",
                    "message": "震荡市场，保留所有委托",
                    "trend": "volatile",
                    "recommend_cancel": []
                }
            
            current_trend = trend_info.get("trend", "").upper()  # "UPTREND" or "DOWNTREND"
            strength = trend_info.get("strength", 0)  # 趋势强度 0-1
            
            recommend_cancel: List[str] = []
            analysis = []
            
            for order in orders:
                order_side = str(order.get("side", "")).upper()
                order_id = order.get("order_id", "unknown")
                order_price = float(order.get("price", 0))
                
                # 判断委托方向与趋势是否一致
                is_bullish_order = order_side == "BUY"
                is_bearish_order = order_side == "SELL"
                
                conflict = False
                reason = ""
                
                if current_trend == "UPTREND" and is_bearish_order:
                    # 上涨趋势中的卖单
                    if strength > 0.6:  # 趋势较强
                        conflict = True
                        reason = "上涨趋势中的卖单"
                elif current_trend == "DOWNTREND" and is_bullish_order:
                    # 下跌趋势中的买单
                    if strength > 0.6:  # 趋势较强
                        conflict = True
                        reason = "下跌趋势中的买单"
                
                if conflict:
                    recommend_cancel.append(order_id)
                
                analysis.append({
                    "order_id": order_id,
                    "side": order_side,
                    "price": order_price,
                    "conflict_with_trend": conflict,
                    "reason": reason
                })
            
            if recommend_cancel:
                status = f"存在{len(recommend_cancel)}个与趋势冲突的委托"
            else:
                status = "正常"
            
            return {
                "status": status,
                "trend": current_trend,
                "trend_strength": strength,
                "analysis": analysis,
                "recommend_cancel": recommend_cancel,
                "message": "所有委托与趋势一致" if not recommend_cancel else f"建议取消{len(recommend_cancel)}个冲突委托"
            }
            
        except Exception as e:
            logger.error(f"趋势检查失败：{e}")
            return {
                "status": "跳过",
                "message": f"趋势检查失败：{str(e)}",
                "recommend_cancel": []
            }
    
    def _get_market_trend(self, symbol: str) -> Optional[Dict]:
        """获取市场趋势信息"""
        try:
            # 如果有 market_analyzer，使用它
            if self.market_analyzer:
                return self.market_analyzer.get_trend(symbol)
            
            # 否则尝试从 order_executor 获取
            if hasattr(self.order_executor, 'get_trend_info'):
                return self.order_executor.get_trend_info(symbol)
            
            # 默认返回震荡市场
            return {"trend": "volatile", "strength": 0}
            
        except Exception as e:
            logger.debug(f"获取市场趋势失败：{e}")
            return None

    def _parse_order_time(self, value) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)) and value > 0:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return None
        return None

    def _build_recommendations(self, inspections: Dict) -> List[str]:
        recs: List[str] = []
        
        # 1. 委托数量检查
        if not inspections["quantity_check"].get("within_limit", True):
            recs.append(f"委托数量超限 ({inspections['quantity_check']['order_count']}/{inspections['quantity_check']['max_allowed']})，先取消多余订单")
        
        # 2. 价格间距检查
        spacing_violations = inspections["spacing_check"].get("violation_details", {})
        if spacing_violations.get("too_close"):
            min_sp = inspections["spacing_check"]["min_spacing"]
            recs.append(f"委托价格间距过小 ({min_sp:.2f} < {self.min_price_spacing:.2f})，需扩大间距到至少{self.min_price_spacing:.2f} USDT")
        if spacing_violations.get("too_far"):
            max_sp = inspections["spacing_check"]["max_spacing"]
            recs.append(f"委托价格间距过大 ({max_sp:.2f} > {self.max_price_spacing:.2f})，需缩小间距到最多{self.max_price_spacing:.2f} USDT")
        
        # 3. 方向检查
        if not inspections["direction_check"].get("direction_consistent", True):
            recs.append(f"存在反向订单 ({inspections['direction_check']['mismatched_order_ids']} 个)，建议撤销")
        
        # 4. 价格距离检查
        if inspections["distance_check"].get("too_far_order_ids"):
            recs.append(f"部分委托离现价过远 ({len(inspections['distance_check']['too_far_order_ids'])} 个)，建议撤单重挂")
        
        # 5. 过期订单检查
        if inspections["expiry_check"].get("expired_order_ids"):
            recs.append(f"存在过期订单 ({len(inspections['expiry_check']['expired_order_ids'])} 个)，建议立即取消")
        
        # 6. 趋势一致性检查
        trend_check = inspections.get("trend_check", {})
        if trend_check.get("recommend_cancel"):
            recs.append(f"趋势检查：建议取消{len(trend_check['recommend_cancel'])}个与趋势冲突的委托（当前趋势：{trend_check.get('trend', '未知')}，强度：{trend_check.get('strength', 0):.2f}）")
        
        return recs or ["所有委托状态正常"]

    def get_inspection_history(self, symbol: str, limit: int = 10) -> List[Dict]:
        if symbol not in self.inspection_records:
            return []
        return [self.inspection_records[symbol]][:limit]

    def update_config(self, new_config: Dict):
        """更新配置参数"""
        self.min_price_spacing = float(new_config.get("min_price_spacing", self.min_price_spacing))
        self.max_price_spacing = float(new_config.get("max_price_spacing", self.max_price_spacing))
        self.max_orders_per_symbol = int(new_config.get("max_orders_per_symbol", self.max_orders_per_symbol))
        self.max_price_distance_ratio = float(new_config.get("price_distance_threshold", self.max_price_distance_ratio))
        self.trend_check_enabled = bool(new_config.get("trend_check_enabled", self.trend_check_enabled))
        logger.info(f"OrderManager 配置已更新：min_spacing={self.min_price_spacing:.2f}, max_spacing={self.max_price_spacing:.2f}, max_orders={self.max_orders_per_symbol}, trend_check={self.trend_check_enabled}")
