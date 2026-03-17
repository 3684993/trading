import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from core.logger import logger
from indicators.indicator_engine import IndicatorEngine
from ai.feature_compressor import FeatureCompressor
from ai.decision_engine import DecisionEngine
from ai.llm_client import LLMClient
from agents.orderbook_agent import OrderBookAgent
from agents.liquidity_agent import LiquidityAgent
from agents.profit_optimizer import ProfitOptimizer
from backtest.backtest_engine import BacktestEngine
from backtest.trade_simulator import TradeSimulator
from performance.performance_analyzer import PerformanceAnalyzer
from data.historical_loader import HistoricalLoader


class WalkForwardEngine:
    def __init__(
        self,
        train_start: datetime = None,
        train_end: datetime = None,
        test_start: datetime = None,
        test_end: datetime = None,
        window_size: int = 100,
        step_size: int = 10,
        initial_capital: float = 10000.0
    ):
        self.train_start = train_start
        self.train_end = train_end
        self.test_start = test_start
        self.test_end = test_end
        self.window_size = window_size
        self.step_size = step_size
        self.initial_capital = initial_capital
        
        self.indicator_engine = IndicatorEngine()
        self.feature_compressor = FeatureCompressor()
        self.orderbook_agent = OrderBookAgent()
        self.liquidity_agent = LiquidityAgent()
        self.profit_optimizer = ProfitOptimizer()
        
        self.llm_client = LLMClient()
        self.decision_engine = DecisionEngine(self.llm_client)
        
        self.simulator = TradeSimulator(initial_capital=initial_capital)
        self.backtest_engine = BacktestEngine(simulator=self.simulator, use_ai=True)
        self.analyzer = PerformanceAnalyzer()
        
        logger.info(
            f"WalkForwardEngine initialized: "
            f"train={train_start} to {train_end}, "
            f"test={test_start} to {test_end}, "
            f"window={window_size}, step={step_size}"
        )
    
    def run(self, df: pd.DataFrame, symbol: str = "BTCUSDT") -> Dict:
        try:
            logger.info(f"Running Walk Forward Testing on {len(df)} candles")
            
            total_windows = (len(df) - self.window_size) // self.step_size
            results = []
            
            for window_idx in range(total_windows):
                train_start_idx = window_idx * self.step_size
                train_end_idx = train_start_idx + self.window_size
                
                test_start_idx = train_end_idx
                test_end_idx = min(test_start_idx + self.window_size + self.step_size, len(df))
                
                train_df = df.iloc[train_start_idx:train_end_idx]
                test_df = df.iloc[test_start_idx:test_end_idx]
                
                logger.info(
                    f"Window {window_idx + 1}: "
                    f"Train {len(train_df)} candles, "
                    f"Test {len(test_df)} candles"
                )
                
                self.backtest_engine.run(train_df, symbol)
                
                test_results = []
                
                for i in range(0, len(test_df), self.step_size):
                    test_window_df = test_df.iloc[i:i+self.window_size]
                    
                    result = self.backtest_engine.run(test_window_df, symbol)
                    test_results.append(result)
                
                window_results = {
                    "window": window_idx + 1,
                    "train_start": train_df.iloc[0]["timestamp"],
                    "train_end": train_df.iloc[-1]["timestamp"],
                    "test_results": test_results
                }
                
                results.append(window_results)
            
            final_stats = self._calculate_walkforward_stats(results)
            
            return {
                "symbol": symbol,
                "total_windows": total_windows,
                "results": results,
                "final_stats": final_stats
            }
            
        except Exception as e:
            logger.error(f"Walk Forward error: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def _calculate_walkforward_stats(self, results: List[Dict]) -> Dict:
        try:
            all_stats = []
            
            for window in results:
                window_results = window.get("test_results", [])
                
                for test_result in window_results:
                    stats = test_result.get("stats", {})
                    all_stats.append(stats)
            
            if not all_stats:
                return {
                    "avg_win_rate": 0.0,
                    "avg_sharpe": 0.0,
                    "avg_profit_factor": 0.0,
                    "total_windows": len(results)
                }
            
            win_rates = [s.get("win_rate", 0) for s in all_stats]
            sharpe_ratios = [s.get("sharpe_ratio", 0) for s in all_stats]
            profit_factors = [s.get("profit_factor", 0) for s in all_stats]
            
            return {
                "avg_win_rate": sum(win_rates) / len(win_rates),
                "avg_sharpe": sum(sharpe_ratios) / len(sharpe_ratios),
                "avg_profit_factor": sum(profit_factors) / len(profit_factors),
                "total_windows": len(results)
            }
        except Exception as e:
            logger.error(f"Calculate walkforward stats error: {e}")
            return {
                "avg_win_rate": 0.0,
                "avg_sharpe": 0.0,
                "avg_profit_factor": 0.0,
                "total_windows": 0
            }
    
    def print_report(self, result: Dict):
        try:
            print("\n" + "="*60)
            print("WALK FORWARD RESULT")
            print("="*60)
            
            print(f"\nSymbol: {result.get('symbol', 'N/A')}")
            print(f"Total Windows: {result.get('total_windows', 0)}")
            print(f"Step Size: {result.get('step_size', 'N/A')}")
            print(f"Window Size: {result.get('window_size', 'N/A')}")
            
            final_stats = result.get("final_stats", {})
            print(f"\nAverage Win Rate: {final_stats.get('avg_win_rate', 0):.2f}%")
            print(f"Average Sharpe Ratio: {final_stats.get('avg_sharpe', 0):.2f}")
            print(f"Average Profit Factor: {final_stats.get('avg_profit_factor', 0):.2f}")
            
            print("\n" + "="*60)
            
        except Exception as e:
            print(f"Print report error: {e}")
