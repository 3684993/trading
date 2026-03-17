import sys
import argparse
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.logger import logger
from config.settings import settings
from system.health_monitor import HealthMonitor
from exchange.binance_client import BinanceClient
from data.market_data import MarketDataService
from data.kline_cache import KlineCache
from indicators.indicator_engine import IndicatorEngine
from ai.feature_compressor import FeatureCompressor
from ai.llm_client import LLMClient
from ai.decision_engine import DecisionEngine
from agents.orderbook_agent import OrderBookAgent
from agents.liquidity_agent import LiquidityAgent
from agents.profit_optimizer import ProfitOptimizer
from core.position_manager import PositionManager
from execution.risk_manager import RiskManager
from execution.order_executor import OrderExecutor
from learning.trade_memory import TradeMemory
from learning.reward_engine import RewardEngine
from learning.strategy_optimizer import StrategyOptimizer
from core.scheduler import Scheduler
from backtest.backtest_runner import BacktestRunner
from research.sandbox_runner import StrategySandbox
from walkforward.walk_forward_engine import WalkForwardEngine
from data.historical_loader import HistoricalLoader


class SystemOrchestrator:
    def __init__(self):
        self.mode: Optional[str] = None
        self.health_monitor = HealthMonitor()
        
        self.binance_client: Optional[BinanceClient] = None
        self.market_data: Optional[MarketDataService] = None
        self.kline_cache: Optional[KlineCache] = None
        self.indicator_engine: Optional[IndicatorEngine] = None
        self.feature_compressor: Optional[FeatureCompressor] = None
        self.llm_client: Optional[LLMClient] = None
        self.decision_engine: Optional[DecisionEngine] = None
        self.orderbook_agent: Optional[OrderBookAgent] = None
        self.liquidity_agent: Optional[LiquidityAgent] = None
        self.profit_optimizer: Optional[ProfitOptimizer] = None
        self.position_manager: Optional[PositionManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.order_executor: Optional[OrderExecutor] = None
        self.trade_memory: Optional[TradeMemory] = None
        self.reward_engine: Optional[RewardEngine] = None
        self.strategy_optimizer: Optional[StrategyOptimizer] = None
        self.scheduler: Optional[Scheduler] = None
        
        logger.info("SystemOrchestrator initialized")
    
    def run(self, mode: str, **kwargs):
        try:
            self.mode = mode
            
            logger.info("="*60)
            logger.info(f"AI QUANT TRADER v1.0 - {mode.upper()} MODE")
            logger.info(f"Trading Environment: {settings.trading_env}")
            logger.info("="*60)
            
            health_status = self.health_monitor.check_all()
            self.health_monitor.print_status()
            
            if mode == "live":
                self._run_live(**kwargs)
            elif mode == "backtest":
                self._run_backtest(**kwargs)
            elif mode == "sandbox":
                self._run_sandbox(**kwargs)
            elif mode == "walkforward":
                self._run_walkforward(**kwargs)
            elif mode == "health":
                pass
            else:
                logger.error(f"Unknown mode: {mode}")
                self._print_usage()
                
        except Exception as e:
            logger.error(f"Orchestrator run error: {e}")
            import traceback
            traceback.print_exc()
    
    def _run_live(self, **kwargs):
        try:
            logger.info("Starting Live Trading mode...")
            
            self._init_live_components()
            
            self.scheduler = Scheduler(
                market_data_service=self.market_data,
                kline_cache=self.kline_cache,
                binance_client=self.binance_client,
                indicator_engine=self.indicator_engine,
                feature_compressor=self.feature_compressor,
                llm_client=self.llm_client,
                decision_engine=self.decision_engine,
                orderbook_agent=self.orderbook_agent,
                liquidity_agent=self.liquidity_agent,
                profit_optimizer=self.profit_optimizer,
                position_manager=self.position_manager,
                risk_manager=self.risk_manager,
                order_executor=self.order_executor,
                trade_memory=self.trade_memory,
                reward_engine=self.reward_engine,
                strategy_optimizer=self.strategy_optimizer,
                interval=settings.loop_interval,
                symbols=[settings.symbol],
                timeframes=settings.timeframes
            )
            
            self.scheduler.start()
            
        except KeyboardInterrupt:
            logger.info("Live trading stopped by user")
        except Exception as e:
            logger.error(f"Live trading error: {e}")
            import traceback
            traceback.print_exc()
    
    def _init_live_components(self):
        logger.info("Initializing live trading components...")
        
        working_proxy = self._get_working_proxy()
        
        self.binance_client = BinanceClient(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            base_url=settings.binance_testnet_url,
            socks5_proxy=working_proxy
        )
        
        self.market_data = MarketDataService(
            binance_client=self.binance_client,
            symbol=settings.symbol,
            kline_limit=settings.kline_limit
        )
        
        self.kline_cache = KlineCache()
        self.indicator_engine = IndicatorEngine()
        self.feature_compressor = FeatureCompressor()
        self.llm_client = LLMClient()
        self.decision_engine = DecisionEngine(self.llm_client)
        self.orderbook_agent = OrderBookAgent()
        self.liquidity_agent = LiquidityAgent()
        self.profit_optimizer = ProfitOptimizer()
        self.position_manager = PositionManager()
        self.risk_manager = RiskManager(
            max_position_size=settings.max_position_size,
            max_drawdown_pct=settings.max_drawdown_pct,
            max_loss_pct=settings.max_loss_pct,
            max_hold_minutes=settings.max_hold_minutes
        )
        self.order_executor = OrderExecutor(self.binance_client, testnet=settings.is_testnet)
        self.trade_memory = TradeMemory()
        self.reward_engine = RewardEngine()
        self.strategy_optimizer = StrategyOptimizer()
        
        logger.info("All live components initialized")
        logger.info(f"TradeGuard config: interval={settings.min_order_interval_seconds}s, trend_confirm={settings.trend_confirmation_count}")
    
    def _run_backtest(self, **kwargs):
        try:
            logger.info("Starting Backtest mode...")
            
            symbol = kwargs.get("symbol", settings.symbol)
            interval = kwargs.get("interval", "15m")
            days = kwargs.get("days", 30)
            capital = kwargs.get("capital", 10000.0)
            use_ai = kwargs.get("use_ai", False)
            
            runner = BacktestRunner(
                symbol=symbol,
                interval=interval,
                days=days,
                initial_capital=capital,
                use_ai=use_ai
            )
            
            runner.run()
            
        except Exception as e:
            logger.error(f"Backtest error: {e}")
            import traceback
            traceback.print_exc()
    
    def _run_sandbox(self, **kwargs):
        try:
            logger.info("Starting Strategy Sandbox mode...")
            
            symbol = kwargs.get("symbol", settings.symbol)
            interval = kwargs.get("interval", "15m")
            days = kwargs.get("days", 30)
            capital = kwargs.get("capital", 10000.0)
            iterations = kwargs.get("iterations", 10)
            
            loader = HistoricalLoader()
            df = loader.load_recent_days(symbol=symbol, interval=interval, days=days)
            
            if df.empty:
                logger.error("No data loaded for sandbox")
                return
            
            sandbox = StrategySandbox(
                initial_capital=capital,
                num_iterations=iterations
            )
            
            result = sandbox.run(df, symbol)
            sandbox.print_report(result)
            
        except Exception as e:
            logger.error(f"Sandbox error: {e}")
            import traceback
            traceback.print_exc()
    
    def _run_walkforward(self, **kwargs):
        try:
            logger.info("Starting Walk Forward mode...")
            
            symbol = kwargs.get("symbol", settings.symbol)
            interval = kwargs.get("interval", "15m")
            days = kwargs.get("days", 30)
            capital = kwargs.get("capital", 10000.0)
            window_size = kwargs.get("window_size", 100)
            step_size = kwargs.get("step_size", 10)
            
            loader = HistoricalLoader()
            df = loader.load_recent_days(symbol=symbol, interval=interval, days=days)
            
            if df.empty:
                logger.error("No data loaded for walk forward")
                return
            
            engine = WalkForwardEngine(
                initial_capital=capital,
                window_size=window_size,
                step_size=step_size
            )
            
            result = engine.run(df, symbol)
            engine.print_report(result)
            
        except Exception as e:
            logger.error(f"Walk Forward error: {e}")
            import traceback
            traceback.print_exc()
    
    def _get_working_proxy(self) -> Optional[str]:
        if not settings.socks5_proxy:
            return None
        
        proxy = settings.socks5_proxy
        if proxy.startswith("socks5://"):
            proxy = proxy.replace("socks5://", "socks5h://")
        
        return proxy
    
    def _print_usage(self):
        print("\n" + "="*60)
        print("AI QUANT TRADER v1.0")
        print("="*60)
        print("\nUsage:")
        print("  python main.py live          - Start live trading")
        print("  python main.py backtest      - Run backtest")
        print("  python main.py sandbox       - Run strategy sandbox")
        print("  python main.py walkforward   - Run walk forward testing")
        print("  python main.py health        - Check system health")
        print("\nOptions:")
        print("  --symbol SYMBOL      Trading symbol (default: BTCUSDT)")
        print("  --interval INTERVAL  Kline interval (default: 15m)")
        print("  --days DAYS          Days to backtest (default: 30)")
        print("  --capital CAPITAL    Initial capital (default: 10000)")
        print("  --use-ai             Use AI for decisions")
        print("\nEnvironment Variables (.env):")
        print("  TRADING_ENV          testnet | live")
        print("  MIN_ORDER_INTERVAL   Minimum seconds between orders (default: 300)")
        print("  TREND_CONFIRMATION   Confirmations needed for reversal (default: 3)")
        print("\n" + "="*60)


def main():
    parser = argparse.ArgumentParser(description="AI Quant Trader v1.0")
    parser.add_argument(
        "mode",
        choices=["live", "backtest", "sandbox", "walkforward", "health"],
        help="Running mode"
    )
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--interval", type=str, default="15m", help="Kline interval")
    parser.add_argument("--days", type=int, default=30, help="Days to backtest")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial capital")
    parser.add_argument("--use-ai", action="store_true", help="Use AI for decisions")
    parser.add_argument("--iterations", type=int, default=10, help="Sandbox iterations")
    parser.add_argument("--window-size", type=int, default=100, help="Walk forward window")
    parser.add_argument("--step-size", type=int, default=10, help="Walk forward step")
    
    args = parser.parse_args()
    
    orchestrator = SystemOrchestrator()
    
    orchestrator.run(
        mode=args.mode,
        symbol=args.symbol,
        interval=args.interval,
        days=args.days,
        capital=args.capital,
        use_ai=args.use_ai,
        iterations=args.iterations,
        window_size=args.window_size,
        step_size=args.step_size
    )


if __name__ == "__main__":
    main()
