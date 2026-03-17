import time
import signal
import sys
from typing import Dict, List, Optional
from datetime import datetime
from core.logger import logger, cleanup_old_logs
from core.formatted_output import formatted_output
from data.market_data import MarketDataService
from data.kline_cache import KlineCache
from indicators.indicator_engine import IndicatorEngine
from ai.feature_compressor import FeatureCompressor
from ai.llm_client import LLMClient
from ai.decision_engine import DecisionEngine
from agents.orderbook_agent import OrderBookAgent
from agents.liquidity_agent import LiquidityAgent
from agents.profit_optimizer import ProfitOptimizer
from agents.advanced_take_profit import advanced_take_profit
from agents.intelligent_close_decision import intelligent_close_decision
from core.position_manager import PositionManager
from execution.risk_manager import RiskManager
from execution.order_executor import OrderExecutor
from execution.execution_engine import ExecutionEngine
from learning.trade_memory import TradeMemory
from learning.reward_engine import RewardEngine
from learning.strategy_optimizer import StrategyOptimizer
from core.trade_guard import TradeGuard
from config.settings import settings
from core.target_position_engine import TargetPositionEngine
from core.risk_engine import RiskEngine
from core.entry_timing_filter import EntryTimingFilter
from execution.order_rationality_checker import OrderRationalityChecker
from core.order_adjuster import OrderAdjuster


class Scheduler:
    def __init__(
        self,
        market_data_service: MarketDataService,
        kline_cache: KlineCache,
        binance_client,
        indicator_engine: IndicatorEngine = None,
        feature_compressor: FeatureCompressor = None,
        llm_client: LLMClient = None,
        decision_engine: DecisionEngine = None,
        orderbook_agent: OrderBookAgent = None,
        liquidity_agent: LiquidityAgent = None,
        profit_optimizer: ProfitOptimizer = None,
        position_manager: PositionManager = None,
        risk_manager: RiskManager = None,
        order_executor: OrderExecutor = None,
        trade_memory: TradeMemory = None,
        reward_engine: RewardEngine = None,
        strategy_optimizer: StrategyOptimizer = None,
        interval: int = 5,  # 降频：从 60 秒改为 5 秒
        symbols: List[str] = None,
        timeframes: List[str] = None
    ):
        self.market_data = market_data_service
        self.cache = kline_cache
        self.binance_client = binance_client
        self.indicator_engine = indicator_engine or IndicatorEngine()
        self.feature_compressor = feature_compressor or FeatureCompressor()
        self.llm_client = llm_client or LLMClient()
        self.decision_engine = decision_engine or DecisionEngine(self.llm_client)
        self.orderbook_agent = orderbook_agent or OrderBookAgent()
        self.liquidity_agent = liquidity_agent or LiquidityAgent()
        self.profit_optimizer = profit_optimizer or ProfitOptimizer()
        self.position_manager = position_manager or PositionManager()
        self.risk_manager = risk_manager or RiskManager()
        self.trade_memory = trade_memory or TradeMemory()
        self.reward_engine = reward_engine or RewardEngine()
        self.strategy_optimizer = strategy_optimizer or StrategyOptimizer()
        self.interval = interval  # 5 秒
        self.symbols = symbols or [settings.symbol]
        self.timeframes = timeframes or settings.timeframes
        self._running = False
        self._cycle_count = 0
        
        self._last_position_state = None
        self._last_fill_time = datetime.now()
        self._last_log_cleanup = None
        self._order_check_state: Dict[str, Dict] = {}
        self._last_indicators: Dict[str, Dict] = {}
        self._last_market_summary: Dict[str, Dict] = {}
        self._last_trend_snapshot: Dict[str, Dict] = {}
        self._order_check_interval_active = 10
        self._order_check_interval_idle = 600
        self.target_position_engine = TargetPositionEngine()
        
        self.risk_engine = RiskEngine(self.position_manager, self.risk_manager)
        self.entry_timing_filter = EntryTimingFilter()
        self.order_manager = None
        self.order_adjuster = None
        self.order_rationality_checker = None  # 新增：委托合理性检查器

        self.trade_guard = TradeGuard(
            min_order_interval_seconds=settings.min_order_interval_seconds,
            trend_confirmation_count=settings.trend_confirmation_count
        )
        
        if order_executor:
            order_executor.trade_guard = self.trade_guard
        self.order_executor = order_executor
        self.execution_engine = None
        if self.order_executor:
            self.execution_engine = ExecutionEngine(
                order_executor=self.order_executor,
                binance_client=self.binance_client,
                position_manager=self.position_manager,
                cycle_interval_seconds=self.interval,  # 使用降频后的间隔
            )
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info(f"Scheduler initialized with {self.interval}s interval (降频)")
        logger.info(f"Monitoring symbols: {self.symbols}")
        logger.info(f"Timeframes: {self.timeframes}")
        logger.info(f"TradeGuard: interval={settings.min_order_interval_seconds}s, trend_confirm={settings.trend_confirmation_count}")

        # Startup log cleanup (keep only last 2 hours)
        deleted = cleanup_old_logs(max_age_seconds=7200)
        self._last_log_cleanup = datetime.now()
        if deleted:
            logger.info("[LOG_CLEANUP] deleted=%d", deleted)
        
        # AI 请求限流控制
        self._last_ai_request_time = {}  # 每个 symbol 最后一次 AI 请求时间
        self._ai_request_interval = 3  # AI 请求最小间隔 3 秒
    
    def _signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)
    
    def _execute_cycle(self) -> bool:
        self._cycle_count += 1
        cycle_start = datetime.now()

        if not self._last_log_cleanup or (datetime.now() - self._last_log_cleanup).total_seconds() >= 600:
            deleted = cleanup_old_logs(max_age_seconds=7200)
            if deleted:
                logger.info("[LOG_CLEANUP] deleted=%d", deleted)
            self._last_log_cleanup = datetime.now()
        
        # 使用新的格式化输出
        formatted_output.print_cycle_header(self._cycle_count, cycle_start)
        
        try:
            for symbol in self.symbols:
                symbol_cycle_start = time.perf_counter()
                primary_timeframe = self.timeframes[-1] if self.timeframes else "15m"
                
                # 0. 委托合理性检查（新增）- 每个周期开始时清理不合理委托
                # 强制调试输出
                logger.error(f"DEBUG: order_executor={self.order_executor is not None}, symbol={symbol}")
                
                if self.order_executor:
                    if not self.order_rationality_checker:
                        logger.error(f"DEBUG: 初始化 OrderRationalityChecker")
                        self.order_rationality_checker = OrderRationalityChecker(
                            self.order_executor,
                            max_orders=4,  # 最多 4 个委托
                            min_price_spacing=50.0,  # 最小 50 USDT，防止委托过于密集
                            order_timeout_minutes=30,  # 超时 30 分钟
                            max_orders_per_side=2  # 每个方向最多 2 个委托
                        )
                    
                    # 智能委托管理：统一检查并清理不合理委托（强制触发）
                    # 获取当前持仓数量
                    current_position = 0.0
                    if hasattr(self, 'order_executor') and self.order_executor:
                        try:
                            position = self.order_executor.client.get_position(symbol)
                            if position:
                                current_position = abs(float(position.get('position_amt', 0) or 0))
                        except Exception as e:
                            logger.error(f"获取持仓失败：{e}")
                    
                    logger.error(f"[SMART_ORDER] {symbol} 开始智能委托管理...")
                    logger.error(f"[SMART_ORDER] {symbol} 当前持仓：{current_position:.4f} BTC")
                    
                    try:
                        rationality_result = self.order_rationality_checker.check_and_cleanup(
                            symbol, 
                            position_size=current_position
                        )
                        
                        # 输出详细的智能管理日志
                        total_orders = rationality_result.get("total_orders", 0)
                        cancelled_count = rationality_result.get("cancelled_count", 0)
                        reasons = rationality_result.get("reasons", [])
                        error = rationality_result.get("error")
                        
                        logger.info(f"[SMART_ORDER] {symbol} 检查结果：总计 {total_orders} 个委托")
                        
                        if error:
                            logger.error(f"[SMART_ORDER] {symbol} 检查失败：{error}")
                        elif cancelled_count > 0:
                            logger.info(
                                f"[SMART_ORDER] {symbol} 智能管理：当前有 {total_orders} 个未成交委托，"
                                f"取消 {cancelled_count} 个不合理委托（原因：{', '.join(reasons)}）"
                            )
                        else:
                            logger.info(
                                f"[SMART_ORDER] {symbol} 智能管理：当前 {total_orders} 个委托都合理，无需调整"
                            )
                        
                        if cancelled_count > 0:
                            logger.info(
                                f"[RATIONALITY] {symbol} 清理了 {cancelled_count} 个不合理委托："
                                f"{', '.join(rationality_result.get('reasons', []))}"
                            )
                    
                    except Exception as e:
                        logger.error(f"[SMART_ORDER] {symbol} 智能管理异常：{e}")
                        import traceback
                        traceback.print_exc()
                
                stage_start = time.perf_counter()
                df = self.market_data.get_klines(primary_timeframe)
                
                if df.empty:
                    formatted_output.print_warning(f"[{symbol}] 数据为空")
                    continue
                
                self.cache.update(symbol, primary_timeframe, df)
                
                latest = df.iloc[-1]
                current_price = latest['close']
                volume = latest['volume']
                self._log_stage(symbol, "market_data", stage_start)
                
                stage_start = time.perf_counter()
                indicators = self.indicator_engine.calculate_indicators(df)
                
                regime = indicators.get("market_regime", {})
                momentum = indicators.get("momentum", {})
                
                # 使用新的格式化输出
                trend = regime.get('state', 'neutral')
                formatted_output.print_market_status(symbol, current_price, volume, trend)
                
                # 使用新的格式化输出 - 技术指标
                formatted_output.print_indicators(symbol, indicators)
                logger.info(
                    "[ANALYSIS] symbol=%s trend=%s rsi=%.1f macd=%.2f atr=%.2f",
                    symbol,
                    trend,
                    float(indicators.get("rsi", 0) or 0),
                    float(indicators.get("macd", {}).get("macd", 0) or 0),
                    float(indicators.get("atr", 0) or 0),
                )
                
                market_summary = self.feature_compressor.compress(indicators)
                self._last_indicators[symbol] = indicators
                self._last_market_summary[symbol] = market_summary.get("market_summary", {}) if isinstance(market_summary, dict) else {}
                
                # 订单簿和流动性分析（保持原有日志，但简化显示）
                orderbook_result = self.orderbook_agent.analyze(indicators, market_summary.get("market_summary", {}))
                liquidity_result = self.liquidity_agent.analyze(indicators, orderbook_result)
                self._log_stage(symbol, "analysis", stage_start)
                
                stage_start = time.perf_counter()
                
                # 先同步持仓状态并计算盈亏
                self.position_manager.sync_position_from_exchange(self.binance_client, symbol)
                self.position_manager.calculate_pnl(current_price)
                self.position_manager.calculate_hold_minutes()
                position_state = self.position_manager.get_position_state()
                
                # 使用新的格式化输出 - 持仓状态
                formatted_output.print_position_status(symbol, position_state)
                
                # 传递持仓信息给profit_optimizer进行高级止盈优化
                profit_result = self.profit_optimizer.optimize(
                    indicators, 
                    market_summary.get("market_summary", {}), 
                    regime,
                    position_state  # 传递持仓信息
                )
                self._log_stage(symbol, "position_sync", stage_start)
                
                stage_start = time.perf_counter()
                if position_state.get("has_position"):
                    # 趋势变化检测
                    trend_change = self._detect_trend_change(symbol, indicators, market_summary)
                    
                    # 智能平仓决策分析
                    close_analysis = intelligent_close_decision.analyze_trend_strength(indicators, market_summary)
                    
                    formatted_output.print_info(
                        f"趋势分析: 强度{close_analysis.get('strength_level', '未知')}, "
                        f"可靠性{close_analysis.get('reliability', '低')}, "
                        f"趋势变化{'是' if trend_change else '否'}"
                    )
                    
                    # 动态止损调整 - 基于15分钟趋势
                    try:
                        # 获取历史价格数据用于趋势分析
                        historical_prices = df['close'].tolist()
                        atr = indicators.get('atr', current_price * 0.01)  # 默认ATR为价格的1%
                        
                        # 执行动态止损调整
                        stop_loss_adjustment = self.position_manager.dynamic_adjust_stop_loss(
                            symbol, historical_prices, atr
                        )
                        
                        if stop_loss_adjustment.get("adjusted", False):
                            formatted_output.print_warning(
                                f"动态止损调整: {stop_loss_adjustment.get('old_stop_loss', 0):.2f} "
                                f"-> {stop_loss_adjustment.get('new_stop_loss', 0):.2f}"
                            )
                    except Exception as e:
                        formatted_output.print_warning(f"动态止损调整失败: {e}")

                    # 新增：动态追踪止损（分级锁盈）
                    trailing_result = self.position_manager.dynamic_trailing_stop(symbol, current_price)
                    if trailing_result.get("adjusted", False):
                        formatted_output.print_warning(
                            f"动态追踪止损: {trailing_result.get('old_stop_loss', 0):.2f} "
                            f"-> {trailing_result.get('new_stop_loss', 0):.2f} "
                            f"(profit={trailing_result.get('profit_pct', 0):.2f}%)"
                        )
                    
                    # 检查是否应该设置收益委托
                    hold_minutes = position_state.get("hold_minutes", 0)
                    pnl_pct = position_state.get("current_pnl_pct", 0)
                    
                    if advanced_take_profit.should_set_profit_trailing(hold_minutes, pnl_pct):
                        formatted_output.print_info(
                            f"持仓{hold_minutes}分钟，盈利{pnl_pct:.2f}%，建议设置收益委托"
                        )
                        # 这里可以添加具体的收益委托逻辑
                        # 例如：自动调整止损到盈亏平衡点上方
                        
                    trend_dir = market_summary.get("market_summary", {}).get("trend", "")
                    if not trend_dir:
                        trend_dir = indicators.get("market_regime", {}).get("state", "")
                    sl_tp_result = self.position_manager.check_local_sl_tp(
                        symbol,
                        current_price,
                        trend_direction=trend_dir,
                        trend_strength=float(indicators.get("market_regime", {}).get("trend_strength", 0) or 0),
                    )
                    if sl_tp_result.get("triggered"):
                        formatted_output.print_warning(f"本地止盈止损触发: {sl_tp_result.get('trigger_type')}")
                        if self.execution_engine:
                            decision = {
                                "action": "close_position",
                                "exit_reason": sl_tp_result.get("trigger_type", ""),
                                "is_stop_loss": str(sl_tp_result.get("trigger_type", "")).upper() == "STOP_LOSS",
                            }
                            close_result = self.execution_engine.execute(
                                decision,
                                symbol,
                                current_price,
                                position_state,
                                indicators,
                            )
                            formatted_output.print_execution_result(symbol, close_result)

                            if close_result.get("completed"):
                                if float(close_result.get("filled_size", 0) or 0) > 0:
                                    self._last_fill_time = datetime.now()
                                self._save_trade_to_memory(
                                    symbol, position_state, current_price, regime, market_summary
                                )
                                self.position_manager.update_position(
                                    symbol=symbol,
                                    position_size=0,
                                    entry_price=0
                                )
                                self.trade_guard.clear_position(symbol)
                        self._log_stage(symbol, "risk_exit", stage_start)
                        self._log_stage(symbol, "cycle_total", symbol_cycle_start)
                        continue
                
                risk_result = self.risk_engine.evaluate(
                    symbol=symbol,
                    position_state=position_state,
                    current_price=current_price,
                    trend_change=trend_change if position_state.get("has_position") else False,
                    market_summary=market_summary.get("market_summary", {}),
                    indicators=indicators,
                )

                if risk_result.get("action") == "force_close":
                    formatted_output.print_warning(f"风险触发 - 强制平仓({risk_result.get('reason', 'UNKNOWN')})")
                    if self.execution_engine:
                        decision = {
                            "action": "close_position",
                            "exit_reason": str(risk_result.get("reason", "RISK_FORCE")),
                            "is_stop_loss": False,
                        }
                        close_result = self.execution_engine.execute(
                            decision,
                            symbol,
                            current_price,
                            position_state,
                            indicators,
                        )
                        formatted_output.print_execution_result(symbol, close_result)

                        if close_result.get("completed"):
                            if float(close_result.get("filled_size", 0) or 0) > 0:
                                self._last_fill_time = datetime.now()
                            self._save_trade_to_memory(
                                symbol, position_state, current_price, regime, market_summary
                            )
                            self.position_manager.update_position(
                                symbol=symbol,
                                position_size=0,
                                entry_price=0
                            )
                            self.trade_guard.clear_position(symbol)
                    self._log_stage(symbol, "risk_exit", stage_start)
                    self._log_stage(symbol, "cycle_total", symbol_cycle_start)
                    continue
                
                self._log_stage(symbol, "risk_exit", stage_start)
                stage_start = time.perf_counter()
                context = {
                    "symbol": symbol,
                    "price": current_price,
                    "indicators": indicators,
                    "momentum": momentum,
                    "market_regime": regime,
                    "market_summary": market_summary.get("market_summary", {}),
                    "orderbook": orderbook_result,
                    "liquidity": liquidity_result,
                    "profit_optimizer": profit_result,
                    "position": position_state
                }
                
                ai_decision = self.decision_engine.generate_trade_decision(context)

                execution_decision = self.target_position_engine.update_target_position(
                    symbol=symbol,
                    ai_decision=ai_decision,
                    position_state=position_state,
                    market_summary=market_summary.get("market_summary", {}),
                )

                # 入场时机过滤（仅影响开仓/加仓执行，不改变AI逻辑）
                action_for_timing = str(execution_decision.get("action", "hold"))
                if action_for_timing in ["open_long", "open_short", "add_position"]:
                    trend_for_timing = "long" if action_for_timing in ["open_long", "add_position"] else "short"
                    trend_strength = float(indicators.get("market_regime", {}).get("trend_strength", 0) or 0)
                    ai_confidence = float(execution_decision.get("confidence", 0) or 0)
                    timing = self.entry_timing_filter.evaluate(
                        symbol=symbol,
                        trend=trend_for_timing,
                        current_price=current_price,
                        indicators_1m=indicators,
                        trend_strength=trend_strength,
                        ai_confidence=ai_confidence,
                        last_fill_time=self._last_fill_time,
                    )
                    if not timing.get("allow_entry", True):
                        execution_decision["action"] = "hold"
                        execution_decision["timing_state"] = timing.get("state")
                        execution_decision["timing_reason"] = timing.get("reason")
                        formatted_output.print_warning(
                            "Entry timing blocked: mode=%s reason=%s bb_pos=%.3f rsi=%.2f trend_strength=%.2f ai_conf=%.2f"
                            % (
                                timing.get("entry_mode"),
                                timing.get("reason"),
                                float(timing.get("bb_position", 0) or 0),
                                float(timing.get("rsi", 0) or 0),
                                float(timing.get("trend_strength", 0) or 0),
                                float(timing.get("ai_confidence", 0) or 0),
                            )
                        )

                # 使用新的格式化输出 - AI决策（显示目标仓位）
                formatted_output.print_ai_decision(symbol, execution_decision)

                position_state_for_guard = dict(position_state)
                position_state_for_guard["trend_strength"] = close_analysis.get("strength_level", "normal") if position_state.get("has_position") else "normal"

                validated = self.trade_guard.validate_decision(
                    execution_decision, symbol, position_state_for_guard, self.binance_client
                )

                if validated.get("modified"):
                    formatted_output.print_warning(
                        f"决策被TradeGuard修改: {execution_decision.get('action')} -> {validated.get('action')} ({validated.get('reason', '未知')})"
                    )
                    execution_decision["action"] = validated["action"]

                logger.info(
                    "[AI_DECISION] symbol=%s action=%s target=%.4f confidence=%.2f",
                    symbol,
                    execution_decision.get("action", "hold"),
                    float(execution_decision.get("target_size", 0) or 0),
                    float(execution_decision.get("confidence", 0) or 0),
                )
                self._log_stage(symbol, "decision", stage_start)

                stage_start = time.perf_counter()
                action = str(execution_decision.get("action", "hold"))
                target_size = self._resolve_decision_size(execution_decision, position_state)
                if action in ["close_position", "reverse_position"] and target_size <= 0 and position_state.get("has_position"):
                    target_size = float(position_state.get("position_size", 0) or 0)
                    execution_decision["target_size"] = target_size
                if action == "hold":
                    logger.info(
                        "[EXECUTION] symbol=%s action=hold status=NOOP reason=decision_hold target=%.6f",
                        symbol,
                        target_size,
                    )
                    formatted_output.print_execution_result(symbol, {"action": "hold", "success": True})
                    self._log_stage(symbol, "execution", stage_start)
                    self._log_stage(symbol, "cycle_total", symbol_cycle_start)
                    continue
                if action in ["open_long", "open_short", "add_position", "close_position", "reverse_position"] and target_size <= 0:
                    logger.info(
                        "[EXECUTION] symbol=%s action=%s status=SKIP reason=target_size_zero target=%.6f",
                        symbol,
                        action,
                        target_size,
                    )
                    formatted_output.print_execution_result(symbol, {"action": "hold", "success": True})
                    execution_decision["action"] = "hold"
                    self._log_stage(symbol, "execution", stage_start)
                    self._log_stage(symbol, "cycle_total", symbol_cycle_start)
                    continue

                entry_risk = self.risk_manager.check_entry_risk(execution_decision, position_state)
                
                if entry_risk.get("status") == "approved" and self.execution_engine:
                    # 仅在无执行任务时做委托清理，避免干扰执行引擎
                    if not self.execution_engine.has_active_task(symbol):
                        if not hasattr(self, 'order_manager') or self.order_manager is None:
                            from core.order_manager import OrderManager
                            market_analyzer = getattr(self, 'market_analyzer', None)
                            self.order_manager = OrderManager(self.order_executor, market_analyzer)
                            self.order_adjuster = OrderAdjuster(self.order_manager, self.order_executor)
                        try:
                            if self._should_check_orders(symbol):
                                inspection = self.order_manager.inspect_all_orders(symbol)
                                self.order_manager.auto_cleanup_orders(symbol, report=inspection)
                                self._record_order_snapshot(symbol, inspection.get("total_orders", 0))
                        except Exception as e:
                            formatted_output.print_warning(f"委托巡查异常: {e}")

                    exec_result = self.execution_engine.execute(
                        execution_decision, symbol, current_price, position_state, indicators
                    )

                    formatted_output.print_execution_result(symbol, exec_result)
                    if not exec_result or not exec_result.get("success", True):
                        logger.info(
                            "[EXECUTION] symbol=%s action=%s status=ERROR reason=%s",
                            symbol,
                            execution_decision.get("action", "hold"),
                            str(exec_result.get("error", "unknown") if isinstance(exec_result, dict) else "unknown"),
                        )
                else:
                    logger.info(
                        "[EXECUTION] symbol=%s action=%s status=SKIP reason=entry_risk_%s",
                        symbol,
                        action,
                        str(entry_risk.get("reason", "unknown")),
                    )
                    formatted_output.print_execution_result(symbol, {"action": "hold", "success": True})

                    if exec_result.get("completed"):
                        if float(exec_result.get("filled_size", 0) or 0) > 0:
                            self._last_fill_time = datetime.now()
                        action = execution_decision.get("action", "hold")

                        if action in ["open_long", "open_short"]:
                            total_size = float(exec_result.get("filled_size", 0) or 0)
                            avg_price = float(exec_result.get("avg_price", 0) or current_price)
                            side = "long" if action == "open_long" else "short"

                            if total_size > 0:
                                self.position_manager.update_position(
                                    symbol=symbol,
                                    position_size=total_size,
                                    entry_price=avg_price,
                                    side=side,
                                    expected_hold_minutes=execution_decision.get("expected_hold_minutes", 60),
                                    stop_loss=execution_decision.get("stop_loss", 0),
                                    take_profit=execution_decision.get("take_profit", 0)
                                )

                                if self.trade_guard:
                                    self.trade_guard.record_position_entry(
                                        symbol=symbol,
                                        entry_price=avg_price,
                                        position_size=total_size,
                                        side=side,
                                        expected_hold_minutes=execution_decision.get("expected_hold_minutes", 60),
                                        stop_loss=execution_decision.get("stop_loss", 0),
                                        take_profit=execution_decision.get("take_profit", 0)
                                    )

                                if self.order_executor:
                                    sl_tp_result = self.order_executor.set_stop_loss_take_profit(
                                        symbol, side, execution_decision.get("stop_loss", 0),
                                        execution_decision.get("take_profit", 0), total_size
                                    )
                                    formatted_output.print_sl_tp_info(symbol, sl_tp_result)
                                logger.info(
                                    "[POSITION] symbol=%s action=%s size=%.4f price=%.2f",
                                    symbol,
                                    action,
                                    total_size,
                                    avg_price,
                                )

                        elif action == "add_position":
                            added_size = float(exec_result.get("filled_size", 0) or 0)
                            avg_price = float(exec_result.get("avg_price", 0) or current_price)
                            if added_size > 0:
                                self.position_manager.add_to_position(
                                    symbol=symbol,
                                    add_size=added_size,
                                    add_price=avg_price
                                )
                                logger.info(
                                    "[POSITION] symbol=%s action=add_position size=%.4f price=%.2f",
                                    symbol,
                                    added_size,
                                    avg_price,
                                )
                                if self.trade_guard:
                                    self.trade_guard.update_position_metadata(
                                        symbol=symbol,
                                        position_size=position_state.get("position_size", 0) + added_size,
                                        stop_loss=execution_decision.get("stop_loss", 0),
                                        take_profit=execution_decision.get("take_profit", 0)
                                    )

                        elif action == "close_position":
                            self._last_position_state = position_state.copy()
                            self._save_trade_to_memory(
                                symbol, self._last_position_state, current_price, regime, market_summary
                            )
                            self.position_manager.update_position(
                                symbol=symbol,
                                position_size=0,
                                entry_price=0
                            )
                            self.trade_guard.clear_position(symbol)
                            logger.info("[POSITION] symbol=%s action=close_position size=0 price=%.2f", symbol, current_price)

                        elif action == "reverse_position":
                            self._last_position_state = position_state.copy()
                            self._save_trade_to_memory(
                                symbol, self._last_position_state, current_price, regime, market_summary
                            )
                self._log_stage(symbol, "execution", stage_start)
                self._log_stage(symbol, "cycle_total", symbol_cycle_start)
            
            # 使用新的格式化输出 - 学习统计
            stats = self.strategy_optimizer.update(self.trade_memory.trades)
            formatted_output.print_learning_stats(symbol, stats)
            
            cycle_end = datetime.now()
            duration = (cycle_end - cycle_start).total_seconds()
            
            # 使用新的格式化输出 - 周期结束
            formatted_output.print_cycle_footer(self._cycle_count, duration)
            
            return True
            
        except Exception as e:
            formatted_output.print_error(f"周期 #{self._cycle_count} 失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _save_trade_to_memory(
        self, 
        symbol: str, 
        position_state: dict, 
        exit_price: float,
        regime: dict,
        market_summary: dict
    ):
        try:
            if not position_state or not position_state.get("has_position"):
                return
            
            entry_price = position_state.get("entry_price", 0)
            pnl_pct = position_state.get("current_pnl_pct", 0)
            hold_minutes = position_state.get("hold_minutes", 0)
            side = position_state.get("side", "long")
            
            self.trade_memory.save_trade(
                symbol=symbol,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                hold_minutes=hold_minutes,
                market_regime=regime.get("state", "unknown"),
                trend=market_summary.get("trend", "neutral"),
                momentum=market_summary.get("momentum", "neutral"),
                volatility=market_summary.get("volatility", "low"),
                side=side
            )
            
            reward = self.reward_engine.calculate({
                "pnl_pct": pnl_pct,
                "drawdown_pct": position_state.get("drawdown_pct", 0),
                "hold_minutes": hold_minutes
            })
            logger.info(self.reward_engine.format_log(reward, symbol))
            
        except Exception as e:
            logger.error(f"Save trade to memory error: {e}")
    
    def start(self) -> None:
        """启动系统 - 基于交易流程的连续循环
        
        系统流程：
        1. 系统启动，从交易所获取委托、持仓情况
        2. 无持仓：建仓流程（分析→委托管理→检测持仓）
        3. 有持仓：持仓管理流程（分析→盈亏→委托管理→平仓检测）
        4. 重复循环，直到系统停止
        
        周期定义：从委托建仓到委托平仓才是一个完整周期
        """
        logger.info("="*60)
        logger.info("AI Quant Trader System Starting...")
        logger.info(f"Trading Environment: {settings.trading_env}")
        logger.info(f"TradeGuard: min_interval={settings.min_order_interval_seconds}s")
        logger.info("="*60)

        try:
            if self.trade_memory:
                self.trade_memory.clear_history()
                logger.info("[LOG_CLEANUP] trade_memory_reset=1")
        except Exception as e:
            logger.error(f"[SYSTEM_ERROR] trade_memory_reset_failed: {e}")
        
        self._running = True
        
        while self._running:
            try:
                # 执行交易流程（无固定周期，连续运行）
                self._execute_trading_loop()
                    
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(5)
    
    def _execute_trading_loop(self) -> None:
        """交易流程循环 - 基于持仓状态的连续处理"""
        loop_start = time.perf_counter()
        
        for symbol in self.symbols:
            if not self._running:
                return
            
            symbol_start = time.perf_counter()
            logger.info("[LOOP] %s 开始处理...", symbol)
            
            try:
                # 获取最新市场数据
                primary_timeframe = self.timeframes[-1] if self.timeframes else "15m"
                df = self.market_data.get_klines(primary_timeframe)
                
                if df.empty:
                    logger.warning(f"[{symbol}] 数据为空，跳过")
                    continue
                
                logger.info("[LOOP] %s 数据获取完成，开始分析...", symbol)
                
                self.cache.update(symbol, primary_timeframe, df)
                latest = df.iloc[-1]
                current_price = latest['close']
                volume = latest['volume']
                
                # 技术指标分析
                indicators = self.indicator_engine.calculate_indicators(df)
                regime = indicators.get("market_regime", {})
                momentum = indicators.get("momentum", {})
                
                logger.info("[LOOP] %s 指标计算完成，开始同步持仓...", symbol)
                
                # 订单簿和流动性分析
                market_summary = self.feature_compressor.compress(indicators)
                self._last_indicators[symbol] = indicators
                self._last_market_summary[symbol] = market_summary.get("market_summary", {}) if isinstance(market_summary, dict) else {}
                
                orderbook_result = self.orderbook_agent.analyze(indicators, market_summary.get("market_summary", {}))
                liquidity_result = self.liquidity_agent.analyze(indicators, orderbook_result)
                
                # 同步持仓状态
                self.position_manager.sync_position_from_exchange(self.binance_client, symbol)
                self.position_manager.calculate_pnl(current_price)
                self.position_manager.calculate_hold_minutes()
                position_state = self.position_manager.get_position_state()
                
                has_position = position_state.get("has_position", False)
                
                if has_position:
                    logger.info("[LOOP] %s 有持仓，执行持仓管理流程...", symbol)
                    # 有持仓：执行持仓管理流程
                    self._manage_position_flow(
                        symbol, current_price, df, indicators, market_summary,
                        position_state, regime, orderbook_result, liquidity_result
                    )
                else:
                    logger.info("[LOOP] %s 无持仓，执行建仓流程...", symbol)
                    # 无持仓：执行建仓流程
                    self._entry_position_flow(
                        symbol, current_price, df, indicators, market_summary,
                        position_state, regime, orderbook_result, liquidity_result
                    )
                
                # 趋势检测
                self._monitor_trend(symbol, indicators, market_summary)
                
                symbol_elapsed = time.perf_counter() - symbol_start
                logger.info("[LOOP] %s 处理完成，耗时 %.2f 秒", symbol, symbol_elapsed)
                
            except Exception as e:
                logger.error(f"Trading loop error for {symbol}: {e}")
                continue
        
        # 确保每 5 秒执行一轮
        elapsed = time.perf_counter() - loop_start
        remaining = self.interval - elapsed
        
        if remaining > 0:
            logger.info("[LOOP] 本轮总耗时 %.2f 秒，等待 %.2f 秒后开始下一轮", elapsed, remaining)
            time.sleep(remaining)
            logger.info("[LOOP] 等待完成，开始下一轮循环")
        else:
            logger.warning("[LOOP] 本轮处理超时 (%.2f 秒 > %d 秒)，立即开始下一轮", elapsed, self.interval)
    
    def _manage_position_flow(
        self, symbol: str, current_price: float, df, indicators: Dict, market_summary: Dict,
        position_state: Dict, regime: Dict, orderbook_result: Dict, liquidity_result: Dict
    ):
        """持仓管理流程：分析→盈亏→委托管理→平仓检测"""
        flow_start = time.perf_counter()
        logger.info("[FLOW] %s 持仓管理流程开始", symbol)
        
        try:
            # 1. 持仓分析 - 必须有持仓信息输出
            formatted_output.print_position_status(symbol, position_state)
            profit_result = self.profit_optimizer.optimize(
                indicators, 
                market_summary.get("market_summary", {}), 
                regime,
                position_state
            )
            logger.info("[FLOW] %s 持仓分析完成 (%.2f 秒) - 持仓尺寸：%.4f, 盈亏：%.2f USDT",
                       symbol, time.perf_counter() - flow_start,
                       float(position_state.get("position_size", 0) or 0),
                       float(position_state.get("current_pnl", 0) or 0))
            
            # 2. 趋势变化检测 - 必须有趋势分析结果
            trend_change = self._detect_trend_change(symbol, indicators, market_summary)
            close_analysis = intelligent_close_decision.analyze_trend_strength(indicators, market_summary)
            
            logger.info("[ANALYSIS] %s 趋势强度=%s 可靠性=%s 趋势变化=%s",
                       symbol,
                       close_analysis.get('strength_level', '未知'),
                       close_analysis.get('reliability', '低'),
                       "是" if trend_change else "否")
            
            # 输出详细趋势信息
            current_trend = market_summary.get("market_summary", {}).get("trend", "neutral")
            trend_strength = float(market_summary.get("market_summary", {}).get("trend_strength", 0) or 0)
            logger.info("[TREND] %s 当前趋势：%s, 强度：%.2f, 动量：%s",
                       symbol, current_trend, trend_strength,
                       market_summary.get("market_summary", {}).get("momentum", "neutral"))
            
            logger.info("[FLOW] %s 趋势检测完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 3. 动态止损调整 - 必须有止损价格输出
            try:
                historical_prices = df['close'].tolist()
                atr = indicators.get('atr', current_price * 0.01)
                stop_loss_adjustment = self.position_manager.dynamic_adjust_stop_loss(
                    symbol, historical_prices, atr
                )
                if stop_loss_adjustment.get("adjusted", False):
                    logger.warning("[POSITION] 动态止损调整：%.2f -> %.2f",
                                 stop_loss_adjustment.get('old_stop_loss', 0),
                                 stop_loss_adjustment.get('new_stop_loss', 0))
                else:
                    # 即使没有调整也要输出当前止损价
                    current_stop_loss = float(position_state.get("stop_loss", 0) or 0)
                    logger.info("[POSITION] 动态止损检查完成：当前止损价=%.2f, 未调整", current_stop_loss)
                logger.info("[FLOW] %s 动态止损检查完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            except Exception as e:
                logger.warning(f"动态止损调整失败：{e}")

            # 4. 动态追踪止损 - 必须有追踪结果
            trailing_result = self.position_manager.dynamic_trailing_stop(symbol, current_price)
            if trailing_result.get("adjusted", False):
                logger.warning("[POSITION] 动态追踪止损：%.2f -> %.2f (profit=%.2f%%)",
                             trailing_result.get('old_stop_loss', 0),
                             trailing_result.get('new_stop_loss', 0),
                             trailing_result.get('profit_pct', 0))
            else:
                # 输出追踪止损状态
                current_stop_loss = float(position_state.get("stop_loss", 0) or 0)
                distance_pct = ((current_price - current_stop_loss) / current_price * 100) if current_stop_loss > 0 else 0
                logger.info("[POSITION] 动态追踪止损检查：当前止损=%.2f, 距离现价=%.2f%%, 未调整",
                          current_stop_loss, distance_pct)
            logger.info("[FLOW] %s 追踪止损检查完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 5. 本地止盈止损检查 - 必须有止盈止损价格
            trend_dir = market_summary.get("market_summary", {}).get("trend", "")
            if not trend_dir:
                trend_dir = indicators.get("market_regime", {}).get("state", "")
            sl_tp_result = self.position_manager.check_local_sl_tp(
                symbol, current_price,
                trend_direction=trend_dir,
                trend_strength=float(indicators.get("market_regime", {}).get("trend_strength", 0) or 0),
            )
            if sl_tp_result.get("triggered"):
                logger.warning("[RISK_TRIGGER] 本地止盈止损触发：%s", sl_tp_result.get('trigger_type'))
                self._execute_close_position(
                    symbol, current_price, position_state, indicators,
                    sl_tp_result.get("trigger_type", ""), 
                    str(sl_tp_result.get("trigger_type", "")).upper() == "STOP_LOSS"
                )
                logger.info("[FLOW] %s 持仓管理流程完成（已平仓）", symbol)
                return
            else:
                # 输出止盈止损价格
                stop_loss = float(position_state.get("stop_loss", 0) or 0)
                take_profit = float(position_state.get("take_profit", 0) or 0)
                logger.info("[RISK] 止盈止损检查：止损=%.2f, 止盈=%.2f, 未触发", stop_loss, take_profit)
            
            logger.info("[FLOW] %s 止盈止损检查完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 6. 委托合理性检查（新增）- 持仓时也要检查委托
            if self.order_executor:
                if not self.order_rationality_checker:
                    logger.error(f"DEBUG: 初始化 OrderRationalityChecker")
                    self.order_rationality_checker = OrderRationalityChecker(
                        self.order_executor,
                        max_orders=4,
                        min_price_spacing=50.0,
                        order_timeout_minutes=30,
                        max_orders_per_side=2
                    )
                
                # 获取当前持仓数量
                current_position = abs(float(position_state.get("position_size", 0) or 0))
                
                logger.error(f"[SMART_ORDER] {symbol} 持仓管理：开始智能委托管理...")
                logger.error(f"[SMART_ORDER] {symbol} 当前持仓：{current_position:.4f} BTC")
                
                try:
                    rationality_result = self.order_rationality_checker.check_and_cleanup(
                        symbol, 
                        position_size=current_position
                    )
                    
                    # 输出详细的智能管理日志
                    total_orders = rationality_result.get("total_orders", 0)
                    cancelled_count = rationality_result.get("cancelled_count", 0)
                    reasons = rationality_result.get("reasons", [])
                    error = rationality_result.get("error")
                    
                    logger.error(f"[SMART_ORDER] {symbol} 检查结果：总计 {total_orders} 个委托")
                    
                    if error:
                        logger.error(f"[SMART_ORDER] {symbol} 检查失败：{error}")
                    elif cancelled_count > 0:
                        logger.error(
                            f"[SMART_ORDER] {symbol} 智能管理：当前有 {total_orders} 个未成交委托，"
                            f"取消 {cancelled_count} 个不合理委托（原因：{', '.join(reasons)}）"
                        )
                    else:
                        logger.error(
                            f"[SMART_ORDER] {symbol} 智能管理：当前 {total_orders} 个委托都合理，无需调整"
                        )
                    
                    if cancelled_count > 0:
                        logger.error(
                            f"[RATIONALITY] {symbol} 清理了 {cancelled_count} 个不合理委托："
                            f"{', '.join(rationality_result.get('reasons', []))}"
                        )
                
                except Exception as e:
                    logger.error(f"[SMART_ORDER] {symbol} 智能管理异常：{e}")
                    import traceback
                    traceback.print_exc()
            
            # 7. 风险评估 - 必须有风险指标输出
            risk_result = self.risk_engine.evaluate(
                symbol=symbol,
                position_state=position_state,
                current_price=current_price,
                trend_change=trend_change,
                market_summary=market_summary.get("market_summary", {}),
                indicators=indicators,
            )
            if risk_result.get("action") == "force_close":
                logger.warning("[RISK_TRIGGER] 风险触发 - 强制平仓：%s", risk_result.get('reason', 'UNKNOWN'))
                self._execute_close_position(
                    symbol, current_price, position_state, indicators,
                    str(risk_result.get("reason", "RISK_FORCE")), False
                )
                logger.info("[FLOW] %s 持仓管理流程完成（强制平仓）", symbol)
                return
            else:
                # 输出风险评估结果
                exposure_pct = float(risk_result.get("exposure_pct", 0) or 0)
                drawdown_pct = float(risk_result.get("drawdown_pct", 0) or 0)
                logger.info("[RISK] 风险评估：暴露=%.2f%%, 回撤=%.2f%%, 状态=正常", exposure_pct, drawdown_pct)
            
            logger.info("[FLOW] %s 风险评估完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 7. AI 决策 - 必须有决策内容
            now = datetime.now()
            last_ai_time = self._last_ai_request_time.get(symbol, datetime.min)
            time_since_last_ai = (now - last_ai_time).total_seconds()
            
            if time_since_last_ai < self._ai_request_interval:
                logger.info("[AI_RATE_LIMIT] %s 跳过 AI 决策 (剩余%.1f 秒)",
                          symbol, self._ai_request_interval - time_since_last_ai)
                logger.info("[AI_DECISION] %s action=hold (限流跳过)", symbol)
            else:
                # 始终进行 AI 决策分析，包括小仓位补仓机会
                # 修改：不再跳过 AI 决策，即使趋势稳定、盈利<5% 也要分析是否补仓
                momentum = indicators.get("momentum", {})
                context = {
                    "symbol": symbol,
                    "price": current_price,
                    "indicators": indicators,
                    "momentum": momentum,
                    "market_regime": regime,
                    "market_summary": market_summary.get("market_summary", {}),
                    "orderbook": orderbook_result,
                    "liquidity": liquidity_result,
                    "position": position_state
                }
                
                ai_start = time.perf_counter()
                ai_decision = self.decision_engine.generate_trade_decision(context)
                ai_elapsed = time.perf_counter() - ai_start
                
                self._last_ai_request_time[symbol] = now
                logger.info("[AI_PERF] %s AI 决策耗时：%.2f 秒", symbol, ai_elapsed)
                
                if ai_elapsed < 3.0:
                    logger.warning("[AI_PERF] %s AI 响应过快 (%.2f 秒)", symbol, ai_elapsed)
                
                # 输出 AI 决策详情
                logger.info("[AI_DECISION] %s action=%s target=%.4f confidence=%.2f 理由=%s",
                          symbol,
                          ai_decision.get("action", "hold"),
                          float(ai_decision.get("target_size", 0) or 0),
                          float(ai_decision.get("confidence", 0) or 0),
                          ai_decision.get("reason", "未提供"))
            
            logger.info("[FLOW] %s AI 决策完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 8. 委托管理 - 必须有委托状态
            self._manage_position_orders(
                symbol, current_price, indicators, market_summary, position_state
            )
            
            # 输出委托状态
            if hasattr(self, 'order_manager') and self.order_manager:
                inspection = self.order_manager.inspect_all_orders(symbol)
                order_count = inspection.get("total_orders", 0)
                
                if order_count > 0:
                    logger.info("[ORDER] %s 当前有%d 个未成交委托", symbol, order_count)
                    
                    # 输出委托详情
                    managed_orders = inspection.get("managed_orders", [])
                    for order in managed_orders:
                        order_side = order.get("side", "UNKNOWN")
                        order_price = float(order.get("price", 0) or 0)
                        order_size = float(order.get("size", 0) or 0)
                        order_type = order.get("type", "UNKNOWN")
                        
                        if "take_profit" in order_type.lower():
                            logger.info("[ORDER]   止盈委托：%s %.4f @ %.2f", order_side, order_size, order_price)
                        elif "limit" in order_type.lower():
                            logger.info("[ORDER]   限价委托：%s %.4f @ %.2f", order_side, order_size, order_price)
                else:
                    logger.info("[ORDER] %s 无未成交委托", symbol)
                    
                    # 无委托时，建议设置止盈委托
                    has_position = position_state.get("has_position", False)
                    if has_position:
                        current_pnl_pct = float(position_state.get("current_pnl_pct", 0) or 0)
                        if current_pnl_pct > 2.0:
                            logger.info("[ORDER] 建议：设置止盈委托锁定利润 (当前盈利 %.2f%%)", current_pnl_pct)
                        elif current_pnl_pct < -1.0:
                            logger.info("[ORDER] 建议：设置止盈委托减少损失 (当前亏损 %.2f%%)", current_pnl_pct)
            
            flow_elapsed = time.perf_counter() - flow_start
            logger.info("[FLOW] %s 持仓管理流程完成，总耗时 %.2f 秒", symbol, flow_elapsed)
                
        except Exception as e:
            logger.error(f"持仓管理流程失败：{e}")
            raise
    
    def _entry_position_flow(
        self, symbol: str, current_price: float, df, indicators: Dict, market_summary: Dict,
        position_state: Dict, regime: Dict, orderbook_result: Dict, liquidity_result: Dict
    ):
        """建仓流程：分析→委托管理→检测持仓"""
        flow_start = time.perf_counter()
        logger.info("[FLOW] %s 建仓流程开始", symbol)
        
        try:
            # 1. 市场分析
            formatted_output.print_market_status(symbol, current_price, float(df.iloc[-1]['volume']), regime.get('state', 'neutral'))
            formatted_output.print_indicators(symbol, indicators)
            
            logger.info("[ANALYSIS] %s trend=%s rsi=%.1f macd=%.2f atr=%.2f",
                       symbol,
                       regime.get('state', 'neutral'),
                       float(indicators.get("rsi", 0) or 0),
                       float(indicators.get("macd", {}).get("macd", 0) or 0),
                       float(indicators.get("atr", 0) or 0))
            logger.info("[FLOW] %s 市场分析完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 2. 风险评估（仅检查市场风险，不检查持仓风险）
            risk_result = self.risk_engine.evaluate(
                symbol=symbol,
                position_state=position_state,  # 无持仓
                current_price=current_price,
                trend_change=False,
                market_summary=market_summary.get("market_summary", {}),
                indicators=indicators,
            )
            # 建仓流程中，无持仓时不应该触发强制平仓
            # 只有市场极端风险才阻止开仓
            if risk_result.get("action") == "force_close" and risk_result.get("reason") == "MARKETExtreme":
                logger.warning("[RISK_TRIGGER] 市场极端风险，暂停开仓：%s", risk_result.get('reason', 'UNKNOWN'))
                return
            
            logger.info("[FLOW] %s 风险评估完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 3. AI 决策（限流：至少间隔 3 秒）
            now = datetime.now()
            last_ai_time = self._last_ai_request_time.get(symbol, datetime.min)
            time_since_last_ai = (now - last_ai_time).total_seconds()
            
            if time_since_last_ai < self._ai_request_interval:
                logger.info("[AI_RATE_LIMIT] %s 等待%.1f 秒后请求 AI (剩余%.1f 秒)",
                          symbol, time_since_last_ai, self._ai_request_interval - time_since_last_ai)
                # 跳过 AI 决策，使用上次的市场摘要
                ai_decision = {"action": "hold", "target_size": 0}
                execution_decision = {"action": "hold", "target_size": 0, "confidence": 0}
            else:
                momentum = indicators.get("momentum", {})
                context = {
                    "symbol": symbol,
                    "price": current_price,
                    "indicators": indicators,
                    "momentum": momentum,
                    "market_regime": regime,
                    "market_summary": market_summary.get("market_summary", {}),
                    "orderbook": orderbook_result,
                    "liquidity": liquidity_result,
                    "position": position_state
                }
                
                ai_start = time.perf_counter()
                ai_decision = self.decision_engine.generate_trade_decision(context)
                ai_elapsed = time.perf_counter() - ai_start
                
                # 记录 AI 请求时间
                self._last_ai_request_time[symbol] = now
                
                logger.info("[AI_PERF] %s AI 请求耗时：%.2f 秒", symbol, ai_elapsed)
                
                if ai_elapsed < 3.0:
                    logger.warning("[AI_PERF] %s AI 响应过快 (%.2f 秒)，可能未充分思考", symbol, ai_elapsed)
                
                execution_decision = self.target_position_engine.update_target_position(
                    symbol=symbol,
                    ai_decision=ai_decision,
                    position_state=position_state,
                    market_summary=market_summary.get("market_summary", {}),
                )
            
            logger.info("[FLOW] %s AI 决策完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 4. 入场时机过滤（简化，不过度阻止）
            self._apply_entry_timing_filter_light(execution_decision, symbol, current_price, indicators)
            
            # 5. TradeGuard 验证（仅检查间隔，不阻止方向）
            close_analysis = intelligent_close_decision.analyze_trend_strength(indicators, market_summary)
            position_state_for_guard = dict(position_state)
            position_state_for_guard["trend_strength"] = close_analysis.get("strength_level", "normal")
            validated = self.trade_guard.validate_decision(
                execution_decision, symbol, position_state_for_guard, self.binance_client
            )
            if validated.get("modified"):
                logger.warning("[TRADEGUARD] 决策被修改：%s -> %s (%s)",
                             execution_decision.get('action'),
                             validated.get('action'),
                             validated.get('reason', '未知'))
                execution_decision["action"] = validated["action"]

            logger.info("[AI_DECISION] %s action=%s target=%.4f confidence=%.2f",
                       symbol,
                       execution_decision.get("action", "hold"),
                       float(execution_decision.get("target_size", 0) or 0),
                       float(execution_decision.get("confidence", 0) or 0))
            logger.info("[FLOW] %s 决策验证完成 (%.2f 秒)", symbol, time.perf_counter() - flow_start)
            
            # 6. 无持仓无委托时必须委托
            self._force_entry_if_needed(
                symbol, current_price, execution_decision, position_state, indicators
            )
            
            flow_elapsed = time.perf_counter() - flow_start
            logger.info("[FLOW] %s 建仓流程完成，总耗时 %.2f 秒", symbol, flow_elapsed)
                
        except Exception as e:
            logger.error(f"建仓流程失败：{e}")
            raise
    
    def _force_entry_if_needed(
        self, symbol: str, current_price: float, execution_decision: Dict,
        position_state: Dict, indicators: Dict
    ):
        """无持仓无委托时必须委托（激进策略）"""
        try:
            action = str(execution_decision.get("action", "hold"))
            target_size = float(execution_decision.get("target_size", 0) or 0)
            
            # 如果 AI 决策已经是要开仓，执行即可
            if action in ["open_long", "open_short"] and target_size > 0:
                logger.info("[ORDER] %s AI 决策开仓，执行委托", symbol)
                self._execute_decision(
                    execution_decision, symbol, current_price, position_state, indicators, {}, {}
                )
                return
            
            # 如果 AI 决策是 hold，但无持仓无委托，强制创建试探性委托
            if action == "hold" and not position_state.get("has_position"):
                # 检查是否有执行中的任务
                if self.execution_engine and self.execution_engine.has_active_task(symbol):
                    logger.info("[ORDER] %s 有执行中的任务，跳过", symbol)
                    return
                
                # 检查委托数量
                open_orders = []
                if hasattr(self, 'order_manager') and self.order_manager:
                    inspection = self.order_manager.inspect_all_orders(symbol)
                    open_orders = inspection.get("managed_orders", [])
                
                # 无委托时必须创建委托（试探性建仓）
                if len(open_orders) == 0:
                    logger.info("[ORDER] %s 无持仓无委托，创建试探性建仓委托", symbol)
                    
                    # 创建试探性决策
                    trend = indicators.get("market_regime", {}).get("state", "neutral")
                    if trend == "bullish":
                        execution_decision["action"] = "open_long"
                        execution_decision["direction"] = "long"
                    elif trend == "bearish":
                        execution_decision["action"] = "open_short"
                        execution_decision["direction"] = "short"
                    else:
                        # 震荡市默认做多
                        execution_decision["action"] = "open_long"
                        execution_decision["direction"] = "long"
                    
                    # 设置目标仓位（最小仓位）
                    execution_decision["target_size"] = 0.002  # 最小 0.002 BTC
                    execution_decision["confidence"] = 0.5  # 中等置信度
                    
                    # 设置入场区间（当前价附近）
                    execution_decision["entry_range"] = [current_price * 0.999, current_price * 1.001]
                    
                    # 设置止损止盈
                    if execution_decision["action"] == "open_long":
                        execution_decision["stop_loss"] = current_price * 0.99  # 1% 止损
                        execution_decision["take_profit"] = current_price * 1.02  # 2% 止盈
                    else:
                        execution_decision["stop_loss"] = current_price * 1.01  # 1% 止损
                        execution_decision["take_profit"] = current_price * 0.98  # 2% 止盈
                    
                    self._execute_decision(
                        execution_decision, symbol, current_price, position_state, indicators, {}, {}
                    )
                else:
                    logger.info("[ORDER] %s 已有%d 个委托，无需新建", symbol, len(open_orders))
            else:
                logger.info("[ORDER] %s 有持仓或有委托，无需新建委托", symbol)
        except Exception as e:
            logger.error(f"强制建仓失败：{e}")
    
    def _manage_position_orders(
        self, symbol: str, current_price: float, indicators: Dict,
        market_summary: Dict, position_state: Dict
    ):
        """持仓中的委托管理：补仓委托/止盈委托 - 长期持续任务，不能跳过"""
        try:
            # 修改：委托管理是长期持续任务，即使有执行中的任务也不能跳过
            # 必须持续管理：检查委托、补仓、取消不合理委托
            
            # 委托巡查（必须执行）
            self._cleanup_orders(symbol)
            
            # 检查当前持仓和盈亏
            has_position = position_state.get("has_position", False)
            if not has_position:
                logger.debug("[ORDER] %s 无持仓，跳过委托管理", symbol)
                return
            
            current_pnl_pct = float(position_state.get("current_pnl_pct", 0) or 0)
            position_size = float(position_state.get("position_size", 0) or 0)
            entry_price = float(position_state.get("entry_price", 0) or 0)
            side = position_state.get("side", "long")
            
            # 1. 止盈委托（同时作为止损委托）- 必须创建
            self._create_take_profit_order(
                symbol, current_price, position_size, side, entry_price, current_pnl_pct
            )
            
            # 2. 补仓委托（根据市场情况和盈亏状态）
            # 不再仅依赖亏损判断，而是综合评估
            self._evaluate_and_create_add_position(
                symbol, current_price, position_size, side, entry_price, current_pnl_pct, indicators
            )
            
            # 3. 委托调整（必须执行）
            if hasattr(self, 'order_adjuster') and self.order_adjuster:
                summary = market_summary.get("market_summary", {}) if isinstance(market_summary, dict) else {}
                trend_direction = str(summary.get("trend", "neutral"))
                trend_strength = float(summary.get("trend_strength", 0) or 0)
                
                inspection = {}
                if hasattr(self, 'order_manager') and self.order_manager:
                    inspection = self.order_manager.inspect_all_orders(symbol)
                
                self.order_adjuster.adjust_orders(
                    symbol=symbol,
                    trend_direction=trend_direction,
                    trend_strength=trend_strength,
                    pullback_detected=not inspection.get("gap_ok", True),
                    trend_changed=bool(inspection.get("trend_changed", False)),
                )
        except Exception as e:
            logger.warning(f"持仓委托管理失败：{e}")
    
    def _create_take_profit_order(
        self, symbol: str, current_price: float, position_size: float, side: str,
        entry_price: float, current_pnl_pct: float
    ):
        """创建止盈委托（同时作为止损委托）- 方向必须与持仓相反
        
        根据币安官方文档：
        - reduceOnly: true 表示该委托只能减少现有持仓，不能开新仓
        - 止盈委托应该设置 reduceOnly=true
        - 止盈价格必须设置在 1 分钟布林线边缘，不能靠近开仓价
        """
        try:
            # 检查持仓是否有效
            if position_size <= 0:
                logger.debug("[ORDER] 持仓为 0，跳过止盈委托创建")
                return
            
            # 检查最小交易数量
            min_size = 0.002  # BTC 最小交易数量（根据 order_executor.py 的配置）
            if position_size < min_size:
                logger.info("[ORDER] 持仓太小 (%.4f < %.4f)，跳过止盈委托", position_size, min_size)
                return
            
            # 获取 1 分钟布林线数据
            bb_data_1m = self._get_bollinger_bands(symbol, "1m")
            bb_upper_1m = bb_data_1m.get("upper", 0)
            bb_lower_1m = bb_data_1m.get("lower", 0)
            bb_middle_1m = bb_data_1m.get("middle", 0)
            
            # 计算止盈价格 - 必须基于 1 分钟布林线边缘
            if side == "long":
                # 多头持仓：止盈在布林线上轨附近
                # 止盈必须在上轨附近，不能靠近开仓价
                if bb_upper_1m > 0:
                    # 止盈设置在上轨下方 0.5% 处，确保接近上轨
                    take_profit_price = bb_upper_1m * 0.995
                    
                    # 确保止盈价格高于开仓价至少 0.5%
                    min_tp = entry_price * 1.005
                    take_profit_price = max(take_profit_price, min_tp)
                    
                    logger.info(f"[ORDER] 1 分钟布林线上轨={bb_upper_1m:.2f}, 止盈={take_profit_price:.2f}")
                else:
                    # 没有布林线数据，使用默认值
                    take_profit_price = entry_price * 1.02
                    logger.warning(f"[ORDER] 无布林线数据，使用默认止盈={take_profit_price:.2f}")
                
                # 多头持仓：止盈委托应该是 SELL（反向）
                take_profit_side = "SELL"
                
            else:  # short
                # 空头持仓：止盈在布林线下轨附近
                # 止盈必须在下轨附近，不能靠近开仓价
                if bb_lower_1m > 0:
                    # 止盈设置在下轨上方 0.5% 处，确保接近下轨
                    take_profit_price = bb_lower_1m * 1.005
                    
                    # 确保止盈价格低于开仓价至少 0.5%
                    max_tp = entry_price * 0.995
                    take_profit_price = min(take_profit_price, max_tp)
                    
                    logger.info(f"[ORDER] 1 分钟布林线下轨={bb_lower_1m:.2f}, 止盈={take_profit_price:.2f}")
                else:
                    # 没有布林线数据，使用默认值
                    take_profit_price = entry_price * 0.98
                    logger.warning(f"[ORDER] 无布林线数据，使用默认止盈={take_profit_price:.2f}")
                
                # 空头持仓：止盈委托应该是 BUY（反向）
                take_profit_side = "BUY"
            
            # 验证止盈价格是否合理（不能靠近开仓价）
            price_distance_pct = abs(take_profit_price - entry_price) / entry_price * 100
            
            if price_distance_pct < 0.5:
                logger.warning(
                    f"[ORDER] 止盈价格过近：止盈={take_profit_price:.2f}, 开仓={entry_price:.2f}, "
                    f"距离={price_distance_pct:.3f}% < 0.5%，调整到最小 0.5%"
                )
                # 强制调整到至少 0.5% 的距离
                if side == "long":
                    take_profit_price = entry_price * 1.005
                else:
                    take_profit_price = entry_price * 0.995
            
            # ✅ 在创建新止盈委托之前，先取消所有旧的止盈委托
            # 目的：防止重复创建止盈委托
            cancelled_old_orders = False
            if hasattr(self, 'order_manager') and self.order_manager:
                inspection = self.order_manager.inspect_all_orders(symbol)
                managed_orders = inspection.get("managed_orders", [])
                
                # 查找所有同方向的止盈委托（与 take_profit_side 相同）
                old_take_profit_orders = []
                for order in managed_orders:
                    order_side = str(order.get("side", "")).upper()
                    order_quantity = float(order.get("quantity", 0) or 0)
                    
                    # 检查是否是止盈委托（方向与 take_profit_side 相同，数量与持仓相同）
                    if order_side == take_profit_side and abs(order_quantity - position_size) < 0.0001:
                        old_take_profit_orders.append(order)
                
                # 取消所有旧的止盈委托
                if len(old_take_profit_orders) > 0:
                    logger.info(f"[ORDER] 发现 {len(old_take_profit_orders)} 个旧止盈委托，准备取消...")
                    order_ids_to_cancel = [str(order.get("order_id", "")) for order in old_take_profit_orders]
                    
                    try:
                        cancel_result = self.order_executor.cancel_orders(symbol, order_ids_to_cancel)
                        if cancel_result.get("success"):
                            logger.info(f"[ORDER] ✅ 成功取消 {len(order_ids_to_cancel)} 个旧止盈委托")
                            cancelled_old_orders = True
                        else:
                            logger.warning(f"[ORDER] 取消旧止盈委托失败：{cancel_result.get('error', '未知错误')}")
                    except Exception as cancel_error:
                        logger.error(f"[ORDER] 取消旧止盈委托异常：{cancel_error}")
            
            # ✅ 关键修复：在创建前，再次检查是否已有止盈委托
            # 目的：防止取消操作还未完成时就创建新委托
            should_create = True
            
            if hasattr(self, 'order_manager') and self.order_manager:
                try:
                    # 等待 0.5 秒让取消操作完成
                    import time
                    time.sleep(0.5)
                    
                    # 重新检查委托
                    inspection = self.order_manager.inspect_all_orders(symbol)
                    managed_orders = inspection.get("managed_orders", [])
                    
                    # 检查是否已有止盈委托
                    for order in managed_orders:
                        order_side = str(order.get("side", "")).upper()
                        order_quantity = float(order.get("quantity", 0) or 0)
                        
                        if order_side == take_profit_side and abs(order_quantity - position_size) < 0.0001:
                            logger.info(f"[ORDER] 止盈委托已存在：{order.get('order_id')} @ {float(order.get('price', 0)):.2f}, 跳过创建")
                            should_create = False
                            break
                except Exception as check_error:
                    logger.warning(f"[ORDER] 检查止盈委托失败：{check_error}")
            
            # 创建新的止盈委托（只在确实没有时才创建）
            if should_create and self.order_executor:
                logger.info("[ORDER] 创建止盈委托：%s %.4f @ %.2f (止盈/止损) - 持仓方向：%s",
                          take_profit_side, position_size, take_profit_price, side.upper())
                
                # ✅ 使用普通限价委托实现止盈（不使用 reduceOnly）
                try:
                    # 四舍五入价格和数量到交易所精度
                    rounded_price = self.order_executor._round_price(symbol, take_profit_price)
                    rounded_size = self.order_executor._round_quantity(symbol, position_size)
                    
                    # 直接调用 API 创建止盈委托
                    result = self.order_executor.client.client.new_order(
                        symbol=symbol,
                        side=take_profit_side,
                        type="LIMIT",
                        price=rounded_price,
                        quantity=rounded_size,
                        timeInForce="GTC"
                    )
                    
                    if result.get("orderId"):
                        logger.info("[ORDER] ✅ 限价止盈委托已提交：订单 ID=%s", result.get("orderId"))
                        logger.info("[ORDER] 止盈详情：方向=%s, 价格=%.2f, 数量=%.4f",
                                  take_profit_side, rounded_price, rounded_size)
                    else:
                        logger.warning("[ORDER] 止盈委托提交失败：返回结果异常")
                        
                except Exception as order_error:
                    logger.error(f"[ORDER] 限价止盈委托提交失败：{order_error}")
            elif not should_create:
                logger.debug("[ORDER] 已有止盈委托，跳过创建")
            else:
                logger.warning("[ORDER] 无法创建止盈委托：order_executor 未初始化")
                
        except Exception as e:
            logger.warning(f"创建止盈委托失败：{e}")
    
    def _create_add_position_order(
        self, symbol: str, current_price: float, position_size: float, side: str,
        entry_price: float, current_pnl_pct: float
    ):
        """创建补仓委托"""
        try:
            # 计算补仓价格（在当前价下方补仓）
            if side == "long":
                add_price = current_price * 0.995  # 低于当前价 0.5%
            else:  # short
                add_price = current_price * 1.005  # 高于当前价 0.5%
            
            # 补仓尺寸（原仓位的一半）
            add_size = position_size * 0.5
            
            # 检查是否已有补仓委托
            has_add_order = False
            if hasattr(self, 'order_manager') and self.order_manager:
                inspection = self.order_manager.inspect_all_orders(symbol)
                managed_orders = inspection.get("managed_orders", [])
                for order in managed_orders:
                    # 检查是否是同方向的限价委托
                    if order.get("side", "") == ("BUY" if side == "long" else "SELL"):
                        has_add_order = True
                        break
            
            if not has_add_order and self.order_executor:
                logger.info("[ORDER] 创建补仓委托：%s %.4f @ %.2f (当前亏损 %.2f%%)",
                          "BUY" if side == "long" else "SELL",
                          add_size, add_price, current_pnl_pct)
                
                # 使用 open_position 创建补仓限价单
                result = self.order_executor.open_position(
                    symbol=symbol,
                    side="BUY" if side == "long" else "SELL",  # 同方向加仓
                    size=add_size,
                    order_type="limit",
                    price=add_price,
                    reduce_only=False  # 可以开新仓
                )
                
                if result.get("success"):
                    logger.info("[ORDER] 补仓委托已提交：订单 ID=%s", result.get("order_id", "UNKNOWN"))
                else:
                    logger.warning("[ORDER] 补仓委托提交失败：%s", result.get("error", "未知错误"))
            elif has_add_order:
                logger.debug("[ORDER] 已有补仓委托，跳过")
            else:
                logger.warning("[ORDER] 无法创建补仓委托：order_executor 未初始化")
                
        except Exception as e:
            logger.warning(f"创建补仓委托失败：{e}")
    
    def _evaluate_and_create_add_position(
        self, symbol: str, current_price: float, position_size: float, side: str,
        entry_price: float, current_pnl_pct: float, indicators: Dict
    ):
        """
        综合评估是否需要补仓
        
        补仓条件：
        1. 亏损补仓：current_pnl_pct < -2.0% (传统补仓)
        2. 趋势延续补仓：趋势与持仓方向一致，且置信度高
        3. 小仓位补仓：仓位 < 0.01 BTC，且市场机会好
        
        不补仓的情况：
        1. 已有补仓委托
        2. 总仓位已达上限
        3. 趋势不明朗或震荡
        """
        try:
            # 检查是否已有补仓委托
            has_add_order = False
            if hasattr(self, 'order_manager') and self.order_manager:
                inspection = self.order_manager.inspect_all_orders(symbol)
                managed_orders = inspection.get("managed_orders", [])
                
                for order in managed_orders:
                    order_type = str(order.get("type", "")).lower()
                    order_side = str(order.get("side", "")).upper()
                    position_side = side.upper()
                    
                    # 补仓委托：与持仓同方向，且不是止盈委托
                    if "add" in order_type or ("limit" in order_type and order_side == position_side):
                        has_add_order = True
                        logger.debug("[ORDER] 已有补仓委托，价格=%.2f", float(order.get("price", 0)))
                        break
            
            if has_add_order:
                logger.debug("[ORDER] 已有补仓委托，跳过评估")
                return
            
            # 条件 1: 亏损补仓（传统策略）
            if current_pnl_pct < -2.0:
                logger.info("[ORDER] 触发亏损补仓：亏损=%.2f%%", current_pnl_pct)
                self._create_add_position_order(
                    symbol, current_price, position_size, side, entry_price, current_pnl_pct
                )
                return
            
            # 条件 2: 趋势延续补仓（新策略）
            market_regime = indicators.get("market_regime", {})
            trend_state = market_regime.get("state", "neutral")
            trend_confidence = float(market_regime.get("confidence", 0) or 0)
            
            should_add = False
            reason = ""
            
            if side == "long" and trend_state == "bullish" and trend_confidence > 0.7:
                should_add = True
                reason = f"多头趋势延续，置信度={trend_confidence:.2f}"
            elif side == "short" and trend_state == "bearish" and trend_confidence > 0.7:
                should_add = True
                reason = f"空头趋势延续，置信度={trend_confidence:.2f}"
            
            # 条件 3: 小仓位补仓（仓位太小，需要增加）
            if position_size < 0.01 and current_pnl_pct > -1.0:
                # 小仓位且没有大幅亏损，可以考虑补仓
                if trend_confidence > 0.6:
                    should_add = True
                    reason = f"小仓位补仓，当前={position_size:.4f}, 置信度={trend_confidence:.2f}"
            
            # 执行补仓
            if should_add:
                logger.info("[ORDER] 触发趋势补仓：%s", reason)
                self._create_add_position_order(
                    symbol, current_price, position_size, side, entry_price, current_pnl_pct
                )
            else:
                logger.debug("[ORDER] 不满足补仓条件：趋势=%s, 置信度=%.2f, 仓位=%.4f", 
                           trend_state, trend_confidence, position_size)
                
        except Exception as e:
            logger.warning(f"补仓评估失败：{e}")
    
    def _manage_entry_orders(
        self, symbol: str, current_price: float, execution_decision: Dict,
        position_state: Dict, indicators: Dict
    ):
        """建仓中的委托管理：无委托必须委托，有委托调整间距"""
        try:
            action = str(execution_decision.get("action", "hold"))
            
            # 如果是 hold，不需要委托
            if action == "hold":
                logger.info("[ORDER] %s AI 决策持仓，无需委托", symbol)
                return
            
            # 检查是否有执行中的任务
            if self.execution_engine and self.execution_engine.has_active_task(symbol):
                logger.info("[ORDER] %s 有执行中的任务，跳过", symbol)
                return
            
            # 委托清理
            self._cleanup_orders(symbol)
            
            # 执行决策
            self._execute_decision(
                execution_decision, symbol, current_price, position_state, indicators, {}, {}
            )
        except Exception as e:
            logger.error(f"建仓委托管理失败：{e}")
    
    def _monitor_trend(self, symbol: str, indicators: Dict, market_summary: Dict):
        """趋势检测"""
        try:
            trend_snapshot = self._get_trend_snapshot(symbol)
            if trend_snapshot:
                prev_snapshot = self._last_trend_snapshot.get(symbol)
                trend_changed = prev_snapshot is None or prev_snapshot.get("trend") != trend_snapshot.get("trend")
                strength_changed = prev_snapshot is None or abs(float(prev_snapshot.get("strength", 0) or 0) - float(trend_snapshot.get("strength", 0) or 0)) >= 0.1
                if trend_changed or strength_changed:
                    logger.info("[MONITOR] %s 趋势检测：trend=%s strength=%.2f 变化=%s",
                              symbol,
                              trend_snapshot.get("trend"),
                              float(trend_snapshot.get("strength", 0) or 0),
                              "是" if trend_changed else "否")
                self._last_trend_snapshot[symbol] = trend_snapshot
        except Exception as e:
            logger.warning(f"趋势检测失败：{e}")
    
    def _apply_entry_timing_filter(self, execution_decision: Dict, symbol: str, current_price: float, indicators: Dict):
        """应用入场时机过滤"""
        action_for_timing = str(execution_decision.get("action", "hold"))
        if action_for_timing in ["open_long", "open_short", "add_position"]:
            trend_for_timing = "long" if action_for_timing in ["open_long", "add_position"] else "short"
            trend_strength = float(indicators.get("market_regime", {}).get("trend_strength", 0) or 0)
            ai_confidence = float(execution_decision.get("confidence", 0) or 0)
            timing = self.entry_timing_filter.evaluate(
                symbol=symbol,
                trend=trend_for_timing,
                current_price=current_price,
                indicators_1m=indicators,
                trend_strength=trend_strength,
                ai_confidence=ai_confidence,
                last_fill_time=self._last_fill_time,
            )
            if not timing.get("allow_entry", True):
                execution_decision["action"] = "hold"
                execution_decision["timing_state"] = timing.get("state")
                execution_decision["timing_reason"] = timing.get("reason")
                logger.info(
                    "[ENTRY_TIMING] %s 入场被阻止：mode=%s reason=%s bb_pos=%.3f rsi=%.2f trend_strength=%.2f ai_conf=%.2f",
                    symbol,
                    timing.get("entry_mode"),
                    timing.get("reason"),
                    float(timing.get("bb_position", 0) or 0),
                    float(timing.get("rsi", 0) or 0),
                    float(timing.get("trend_strength", 0) or 0),
                    float(timing.get("ai_confidence", 0) or 0),
                )
    
    def _apply_entry_timing_filter_light(self, execution_decision: Dict, symbol: str, current_price: float, indicators: Dict):
        """简化的入场时机过滤（仅记录，不阻止）"""
        action_for_timing = str(execution_decision.get("action", "hold"))
        if action_for_timing in ["open_long", "open_short", "add_position"]:
            trend_for_timing = "long" if action_for_timing in ["open_long", "add_position"] else "short"
            trend_strength = float(indicators.get("market_regime", {}).get("trend_strength", 0) or 0)
            ai_confidence = float(execution_decision.get("confidence", 0) or 0)
            timing = self.entry_timing_filter.evaluate(
                symbol=symbol,
                trend=trend_for_timing,
                current_price=current_price,
                indicators_1m=indicators,
                trend_strength=trend_strength,
                ai_confidence=ai_confidence,
                last_fill_time=self._last_fill_time,
            )
            if not timing.get("allow_entry", True):
                # 仅记录，不修改决策
                logger.info(
                    "[ENTRY_TIMING] %s 入场时机不佳但会执行：mode=%s reason=%s bb_pos=%.3f rsi=%.2f",
                    symbol,
                    timing.get("entry_mode"),
                    timing.get("reason"),
                    float(timing.get("bb_position", 0) or 0),
                    float(timing.get("rsi", 0) or 0),
                )
    
    def _execute_decision(
        self, execution_decision: Dict, symbol: str, current_price: float,
        position_state: Dict, indicators: Dict, regime: Dict, market_summary: Dict
    ):
        """执行交易决策"""
        action = str(execution_decision.get("action", "hold"))
        target_size = self._resolve_decision_size(execution_decision, position_state)
        
        # 处理平仓/反转决策
        if action in ["close_position", "reverse_position"] and target_size <= 0 and position_state.get("has_position"):
            target_size = float(position_state.get("position_size", 0) or 0)
            execution_decision["target_size"] = target_size
        
        # 保持持仓
        if action == "hold":
            logger.info("[EXECUTION] %s action=hold status=NOOP reason=decision_hold target=%.6f", symbol, target_size)
            return
        
        # 目标仓位为 0
        if action in ["open_long", "open_short", "add_position", "close_position", "reverse_position"] and target_size <= 0:
            logger.info("[EXECUTION] %s action=%s status=SKIP reason=target_size_zero target=%.6f", symbol, action, target_size)
            return

        # 风险检查
        entry_risk = self.risk_manager.check_entry_risk(execution_decision, position_state)
        if entry_risk.get("status") != "approved":
            logger.info("[EXECUTION] %s action=%s status=SKIP reason=entry_risk_%s", symbol, action, str(entry_risk.get("reason", "unknown")))
            return
        
        # 委托清理
        if not self.execution_engine.has_active_task(symbol):
            self._cleanup_orders(symbol)

        # 执行
        if self.execution_engine:
            exec_result = self.execution_engine.execute(
                execution_decision, symbol, current_price, position_state, indicators
            )
            
            if not exec_result or not exec_result.get("success", True):
                logger.info(
                    "[EXECUTION] %s action=%s status=ERROR reason=%s",
                    symbol, execution_decision.get("action", "hold"),
                    str(exec_result.get("error", "unknown") if isinstance(exec_result, dict) else "unknown"),
                )
    
    def _execute_close_position(
        self, symbol: str, current_price: float, position_state: Dict,
        indicators: Dict, exit_reason: str, is_stop_loss: bool
    ):
        """执行平仓操作"""
        if self.execution_engine:
            decision = {
                "action": "close_position",
                "exit_reason": exit_reason,
                "is_stop_loss": is_stop_loss,
            }
            close_result = self.execution_engine.execute(
                decision, symbol, current_price, position_state, indicators
            )

            if close_result.get("completed"):
                if float(close_result.get("filled_size", 0) or 0) > 0:
                    self._last_fill_time = datetime.now()
                regime = {}
                market_summary = {}
                self._save_trade_to_memory(symbol, position_state, current_price, regime, market_summary)
                self.position_manager.update_position(symbol=symbol, position_size=0, entry_price=0)
                self.trade_guard.clear_position(symbol)
    
    def _cleanup_orders(self, symbol: str):
        """委托清理与巡查"""
        try:
            if not hasattr(self, 'order_manager') or self.order_manager is None:
                from core.order_manager import OrderManager
                market_analyzer = getattr(self, 'market_analyzer', None)
                self.order_manager = OrderManager(self.order_executor, market_analyzer)
                self.order_adjuster = OrderAdjuster(self.order_manager, self.order_executor)
            
            if self._should_check_orders(symbol):
                inspection = self.order_manager.inspect_all_orders(symbol)
                self.order_manager.auto_cleanup_orders(symbol, report=inspection)
                self._record_order_snapshot(symbol, inspection.get("total_orders", 0))
        except Exception as e:
            logger.warning(f"委托巡查异常：{e}")
    
    def _run_intra_cycle_tasks(self) -> None:
        """主周期内执行轻量任务，避免整段休眠。
        
        监控任务包括：
        1. 持仓检测：同步交易所持仓状态，计算盈亏
        2. 委托检测：巡查未成交委托，管理委托状态
        3. 风险检测：检查止损止盈、强制平仓条件
        4. 趋势检测：监控趋势变化，调整策略
        """
        if self._running:
            try:
                for symbol in self.symbols:
                    has_output = False  # 标记本次是否有输出
                    
                    # 1) 委托检测与委托管理
                    if (not self.execution_engine or not self.execution_engine.has_active_task(symbol)) and hasattr(self, 'order_manager') and self.order_manager and self.order_executor:
                        if self._should_check_orders(symbol):
                            inspection = self.order_manager.inspect_all_orders(symbol)
                            self.order_manager.auto_cleanup_orders(symbol, report=inspection)
                            self._record_order_snapshot(symbol, inspection.get("total_orders", 0))
                            
                            # 输出委托检测结果
                            if inspection.get("total_orders", 0) > 0:
                                logger.info("[MONITOR] %s 委托检测：%d 个未成交委托，最小间距=%.1f", 
                                          symbol, inspection.get("total_orders", 0), 
                                          inspection.get("min_gap", 0))
                                has_output = True
                                if inspection.get("recommendations"):
                                    for rec in inspection.get("recommendations", []):
                                        logger.info("[MONITOR]   建议：%s", rec)
                            
                            if self.order_adjuster:
                                market_summary = {}
                                try:
                                    market_summary = self.market_data.get_market_summary(symbol)
                                except Exception:
                                    market_summary = {}
                                summary = market_summary.get("market_summary", {}) if isinstance(market_summary, dict) else {}
                                self.order_adjuster.adjust_orders(
                                    symbol=symbol,
                                    trend_direction=str(summary.get("trend", "neutral")),
                                    trend_strength=float(summary.get("trend_strength", 0) or 0),
                                    pullback_detected=not inspection.get("gap_ok", True),
                                    trend_changed=bool(inspection.get("trend_changed", False)),
                                )

                    # 2) 持仓检测
                    if self.position_manager and self.binance_client:
                        self.position_manager.sync_position_from_exchange(self.binance_client, symbol)
                        pos = self.position_manager.get_position_state()
                        
                        # 输出持仓检测结果
                        if pos.get("has_position"):
                            logger.info("[MONITOR] %s 持仓检测：side=%s size=%.4f pnl=%.2f USDT pnl%%=%.2f%% hold=%d 分钟",
                                      symbol, pos.get("side", "unknown"), 
                                      float(pos.get("position_size", 0) or 0),
                                      float(pos.get("current_pnl", 0) or 0),
                                      float(pos.get("current_pnl_pct", 0) or 0),
                                      int(pos.get("hold_minutes", 0) or 0))
                            has_output = True
                        
                        trend_snapshot = self._get_trend_snapshot(symbol)
                        if trend_snapshot:
                            prev_snapshot = self._last_trend_snapshot.get(symbol)
                            trend_changed = prev_snapshot is None or prev_snapshot.get("trend") != trend_snapshot.get("trend")
                            strength_changed = prev_snapshot is None or abs(float(prev_snapshot.get("strength", 0) or 0) - float(trend_snapshot.get("strength", 0) or 0)) >= 0.1
                            if trend_changed or strength_changed:
                                logger.info(
                                    "[MONITOR] %s 趋势检测：trend=%s strength=%.2f 变化=%s",
                                    symbol,
                                    trend_snapshot.get("trend"),
                                    float(trend_snapshot.get("strength", 0) or 0),
                                    "是" if trend_changed else "否",
                                )
                                has_output = True
                            self._last_trend_snapshot[symbol] = trend_snapshot

                        # 3) 风险检测
                        if pos.get('has_position') and self.risk_engine:
                            risk = self.risk_engine.evaluate(
                                symbol=symbol,
                                position_state=pos,
                                current_price=float(pos.get('current_price', 0) or 0),
                                trend_change=False,
                                market_summary={},
                                indicators={},
                            )
                            if risk.get('action') == 'force_close':
                                logger.warning("[MONITOR] %s 风险检测：触发强制平仓 reason=%s", 
                                             symbol, str(risk.get("reason", "UNKNOWN")))
                                has_output = True
                                if self.execution_engine:
                                    decision = {
                                        "action": "close_position",
                                        "exit_reason": str(risk.get("reason", "RISK_FORCE")),
                                        "is_stop_loss": False,
                                    }
                                    exec_result = self.execution_engine.execute(
                                        decision, symbol, float(pos.get('current_price', 0) or 0), pos, {}
                                    )
                                    if exec_result and exec_result.get("completed"):
                                        self.position_manager.update_position(symbol=symbol, position_size=0, entry_price=0)

                        # 4) 执行引擎心跳（处理进行中的任务）
                        if self.execution_engine and self.execution_engine.has_active_task(symbol):
                            try:
                                tick_result = self.execution_engine.tick(symbol, float(pos.get('current_price', 0) or 0), {})
                                if tick_result:
                                    if tick_result.get("completed") and tick_result.get("action") == "close_position":
                                        if float(tick_result.get("filled_size", 0) or 0) > 0:
                                            self._last_fill_time = datetime.now()
                                        self._save_trade_to_memory(symbol, pos, float(pos.get('current_price', 0) or 0), {}, {})
                                        self.position_manager.update_position(symbol=symbol, position_size=0, entry_price=0)
                                        self.trade_guard.clear_position(symbol)
                                        has_output = True
                                    elif tick_result.get("in_progress"):
                                        logger.info("[MONITOR] %s 执行中：state=%s remaining=%.4f elapsed=%.0fs",
                                                  symbol, tick_result.get("state", "UNKNOWN"),
                                                  float(tick_result.get("remaining_size", 0) or 0),
                                                  float(tick_result.get("elapsed", 0) or 0))
                                        has_output = True
                            except Exception as e:
                                logger.warning(f"Execution engine tick warning: {e}")
                    
                    # 如果本次循环没有任何输出，输出一个简洁的状态信息
                    if not has_output and not pos.get("has_position"):
                        logger.debug("[MONITOR] %s 监控正常：无持仓，无委托", symbol)
            except Exception as e:
                logger.warning(f"Intra-cycle task warning: {e}")

    def stop(self) -> None:
        logger.info("Stopping scheduler...")
        self._running = False
    
    def run_once(self) -> bool:
        return self._execute_cycle()

    def _should_check_orders(self, symbol: str, force: bool = False) -> bool:
        state = self._order_check_state.setdefault(symbol, {"last_check": None, "open_count": 0})
        if force:
            return True
        now = datetime.now()
        has_task = self.execution_engine.has_active_task(symbol) if self.execution_engine else False
        open_count = int(state.get("open_count", 0) or 0)
        interval = self._order_check_interval_active if (has_task or open_count > 0) else self._order_check_interval_idle
        last_check = state.get("last_check")
        if not last_check:
            return True
        return (now - last_check).total_seconds() >= interval

    def _record_order_snapshot(self, symbol: str, count: int) -> None:
        state = self._order_check_state.setdefault(symbol, {"last_check": None, "open_count": 0})
        state["last_check"] = datetime.now()
        state["open_count"] = int(count or 0)

    def _log_stage(self, symbol: str, stage: str, started_at: float) -> None:
        try:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        except Exception:
            elapsed_ms = 0
        stage_map = {
            "market_data": "行情采集",
            "analysis": "指标分析",
            "position_sync": "持仓同步",
            "risk_exit": "风控检查",
            "decision": "AI决策",
            "execution": "执行处理",
            "cycle_total": "周期总耗时",
        }
        stage_label = stage_map.get(stage, stage)
        logger.info("[STAGE] 交易对=%s 阶段=%s 耗时=%dms", symbol, stage_label, elapsed_ms)

    def _resolve_decision_size(self, decision: Dict, position_state: Dict) -> float:
        size = decision.get("target_size")
        if size is None:
            size = decision.get("size", 0)
        if isinstance(size, list):
            size = size[0] if size else 0
        try:
            return float(size or 0.0)
        except Exception:
            return 0.0

    def _get_trend_snapshot(self, symbol: str) -> Dict:
        summary = {}
        if hasattr(self.market_data, "get_market_summary"):
            try:
                summary = self.market_data.get_market_summary(symbol)
            except Exception:
                summary = {}
        if isinstance(summary, dict):
            summary = summary.get("market_summary", summary)
        if not summary:
            summary = self._last_market_summary.get(symbol, {})
        trend = str(summary.get("trend") or summary.get("state") or "")
        strength = float(summary.get("trend_strength", 0) or 0)
        return {"trend": trend, "strength": strength}
    
    def _detect_trend_change(self, symbol: str, indicators: Dict, market_summary: Dict) -> bool:
        """检测趋势变化"""
        try:
            # 获取当前趋势
            current_trend = market_summary.get("trend", "neutral")
            current_momentum = market_summary.get("momentum", "neutral")
            
            # 获取技术指标
            rsi = indicators.get("rsi", 50)
            macd = indicators.get("macd", {}).get("macd", 0)
            
            # 检查趋势反转信号
            trend_change_signals = []
            
            # RSI超买超卖反转
            if current_trend == "bullish" and rsi > 70:
                trend_change_signals.append("RSI超买可能反转")
            elif current_trend == "bearish" and rsi < 30:
                trend_change_signals.append("RSI超卖可能反转")
            
            # MACD反转信号
            if current_trend == "bullish" and macd < 0:
                trend_change_signals.append("MACD转负")
            elif current_trend == "bearish" and macd > 0:
                trend_change_signals.append("MACD转正")
            
            # 动量减弱
            if current_momentum == "weakening":
                trend_change_signals.append("动量减弱")
            
            # 如果有多个趋势变化信号，认为趋势可能改变
            if len(trend_change_signals) >= 2:
                logger.info(f"趋势变化检测: {', '.join(trend_change_signals)}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"趋势变化检测失败：{e}")
            return False
    
    def _get_bollinger_bands(self, symbol: str, timeframe: str = "1m") -> Dict:
        """
        获取指定时间周期的布林线数据
        
        Args:
            symbol: 交易对
            timeframe: 时间周期（默认 1m）
            
        Returns:
            布林线数据字典：{upper, middle, lower}
        """
        try:
            # 从 market_data 获取布林线数据
            if hasattr(self, 'market_data') and self.market_data:
                if hasattr(self.market_data, 'get_indicators'):
                    indicators = self.market_data.get_indicators(symbol, timeframe)
                    if indicators and 'bollinger' in indicators:
                        bb = indicators['bollinger']
                        return {
                            "upper": float(bb.get("upper", 0)),
                            "middle": float(bb.get("middle", 0)),
                            "lower": float(bb.get("lower", 0))
                        }
            
            # 备用方案：从 indicators 参数获取（如果在循环中调用）
            if hasattr(self, '_last_indicators') and self._last_indicators:
                indicators = self._last_indicators.get(symbol, {})
                if 'bollinger' in indicators:
                    bb = indicators['bollinger']
                    return {
                        "upper": float(bb.get("upper", 0)),
                        "middle": float(bb.get("middle", 0)),
                        "lower": float(bb.get("lower", 0))
                    }
            
            # 返回空数据
            logger.warning(f"无法获取{timeframe}布林线数据：{symbol}")
            return {"upper": 0, "middle": 0, "lower": 0}
            
        except Exception as e:
            logger.error(f"获取布林线数据失败：{symbol} {timeframe} - {e}")
            return {"upper": 0, "middle": 0, "lower": 0}
