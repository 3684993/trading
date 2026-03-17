import random
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
from core.logger import logger
from backtest.backtest_engine import BacktestEngine
from backtest.trade_simulator import TradeSimulator
from performance.performance_analyzer import PerformanceAnalyzer
from data.historical_loader import HistoricalLoader


class StrategySandbox:
    def __init__(
        self,
        initial_capital: float = 10000.0,
        num_iterations: int = 10
    ):
        self.initial_capital = initial_capital
        self.num_iterations = num_iterations
        
        self.simulator = TradeSimulator(initial_capital=initial_capital)
        self.backtest_engine = BacktestEngine(simulator=self.simulator, use_ai=False)
        self.analyzer = PerformanceAnalyzer()
        
        self.results: List[Dict] = []
        
        logger.info(
            f"StrategySandbox initialized: "
            f"capital={initial_capital}, "
            f"iterations={num_iterations}"
        )
    
    def run(
        self,
        df: pd.DataFrame,
        symbol: str = "BTCUSDT"
    ) -> Dict:
        try:
            logger.info(f"Running strategy sandbox with {self.num_iterations} iterations")
            
            self.results = []
            
            for i in range(self.num_iterations):
                params = self._generate_random_params()
                
                logger.info(f"Iteration {i+1}/{self.num_iterations}: {params}")
                
                result = self.backtest_engine.run(df, symbol)
                
                stats = self.analyzer.analyze(
                    result.get("trades", []),
                    result.get("equity_curve", [])
                )
                
                iteration_result = {
                    "iteration": i + 1,
                    "params": params,
                    "stats": stats,
                    "final_equity": result.get("final_equity", self.initial_capital),
                    "total_return": result.get("total_return", 0)
                }
                
                self.results.append(iteration_result)
            
            top_strategies = self._get_top_strategies(5)
            
            return {
                "symbol": symbol,
                "total_iterations": self.num_iterations,
                "results": self.results,
                "top_strategies": top_strategies
            }
            
        except Exception as e:
            logger.error(f"Strategy sandbox error: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def _generate_random_params(self) -> Dict:
        return {
            "rsi_threshold": random.uniform(30, 70),
            "macd_threshold": random.uniform(-100, 100),
            "stop_loss_pct": random.uniform(0.5, 3.0),
            "take_profit_pct": random.uniform(1.0, 5.0),
            "position_size": random.uniform(0.01, 0.05),
            "trend_strength_threshold": random.uniform(0.001, 0.01)
        }
    
    def _get_top_strategies(self, top_n: int = 5) -> List[Dict]:
        try:
            sorted_results = sorted(
                self.results,
                key=lambda x: x.get("stats", {}).get("sharpe_ratio", 0),
                reverse=True
            )
            
            return sorted_results[:top_n]
            
        except Exception as e:
            logger.error(f"Get top strategies error: {e}")
            return []
    
    def print_report(self, result: Dict):
        try:
            print("\n" + "="*60)
            print("STRATEGY SANDBOX RESULTS")
            print("="*60)
            
            print(f"\nSymbol: {result.get('symbol', 'N/A')}")
            print(f"Total Iterations: {result.get('total_iterations', 0)}")
            
            top_strategies = result.get("top_strategies", [])
            
            print(f"\nTop {len(top_strategies)} Strategies:")
            print("-"*60)
            
            for i, strategy in enumerate(top_strategies):
                stats = strategy.get("stats", {})
                params = strategy.get("params", {})
                
                print(f"\nStrategy #{i+1}:")
                print(f"  Win Rate: {stats.get('win_rate', 0):.2f}%")
                print(f"  Sharpe Ratio: {stats.get('sharpe_ratio', 0):.2f}")
                print(f"  Profit Factor: {stats.get('profit_factor', 0):.2f}")
                print(f"  Max Drawdown: {stats.get('max_drawdown', 0):.2f}%")
                print(f"  Total Return: {strategy.get('total_return', 0):.2f}%")
                print(f"  Params: RSI={params.get('rsi_threshold', 0):.1f}, "
                      f"SL={params.get('stop_loss_pct', 0):.1f}%, "
                      f"TP={params.get('take_profit_pct', 0):.1f}%")
            
            print("\n" + "="*60)
            
        except Exception as e:
            print(f"Print report error: {e}")
