from typing import Dict, List
import numpy as np
from core.logger import logger


class PerformanceAnalyzer:
    def __init__(self):
        logger.info("PerformanceAnalyzer initialized")
    
    def analyze(self, trades: List[Dict], equity_curve: List[Dict] = None) -> Dict:
        try:
            if not trades:
                return self._get_empty_stats()
            
            total_trades = len(trades)
            
            winning_trades = [t for t in trades if t.get("pnl", 0) > 0]
            losing_trades = [t for t in trades if t.get("pnl", 0) < 0]
            
            win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
            
            total_profit = sum(t.get("pnl", 0) for t in winning_trades)
            total_loss = abs(sum(t.get("pnl", 0) for t in losing_trades))
            
            avg_profit = total_profit / len(winning_trades) if winning_trades else 0
            avg_loss = total_loss / len(losing_trades) if losing_trades else 0
            
            profit_factor = total_profit / total_loss if total_loss > 0 else 0
            
            total_pnl = sum(t.get("pnl", 0) for t in trades)
            total_pnl_pct = sum(t.get("pnl_pct", 0) for t in trades)
            
            avg_hold_minutes = np.mean([t.get("hold_minutes", 0) for t in trades])
            
            max_drawdown = self._calculate_max_drawdown(equity_curve) if equity_curve else 0
            
            sharpe_ratio = self._calculate_sharpe_ratio(trades)
            
            max_consecutive_wins = self._calculate_max_consecutive(trades, "win")
            max_consecutive_losses = self._calculate_max_consecutive(trades, "loss")
            
            return {
                "total_trades": total_trades,
                "winning_trades": len(winning_trades),
                "losing_trades": len(losing_trades),
                "win_rate": round(win_rate * 100, 2),
                "total_profit": round(total_profit, 2),
                "total_loss": round(total_loss, 2),
                "net_pnl": round(total_pnl, 2),
                "net_pnl_pct": round(total_pnl_pct, 2),
                "avg_profit": round(avg_profit, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": round(profit_factor, 2),
                "avg_hold_minutes": round(avg_hold_minutes, 1),
                "max_drawdown": round(max_drawdown * 100, 2),
                "sharpe_ratio": round(sharpe_ratio, 2),
                "max_consecutive_wins": max_consecutive_wins,
                "max_consecutive_losses": max_consecutive_losses
            }
            
        except Exception as e:
            logger.error(f"Performance analysis error: {e}")
            return self._get_empty_stats()
    
    def _get_empty_stats(self) -> Dict:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_profit": 0.0,
            "total_loss": 0.0,
            "net_pnl": 0.0,
            "net_pnl_pct": 0.0,
            "avg_profit": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "avg_hold_minutes": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0
        }
    
    def _calculate_max_drawdown(self, equity_curve: List[Dict]) -> float:
        try:
            if not equity_curve:
                return 0.0
            
            peak = equity_curve[0].get("equity", 0)
            max_dd = 0.0
            
            for point in equity_curve:
                equity = point.get("equity", 0)
                
                if equity > peak:
                    peak = equity
                
                dd = (peak - equity) / peak if peak > 0 else 0
                
                if dd > max_dd:
                    max_dd = dd
            
            return max_dd
            
        except Exception:
            return 0.0
    
    def _calculate_sharpe_ratio(self, trades: List[Dict]) -> float:
        try:
            if len(trades) < 2:
                return 0.0
            
            returns = [t.get("pnl_pct", 0) for t in trades]
            
            avg_return = np.mean(returns)
            std_return = np.std(returns)
            
            if std_return == 0:
                return 0.0
            
            risk_free_rate = 0.0
            
            return (avg_return - risk_free_rate) / std_return
            
        except Exception:
            return 0.0
    
    def _calculate_max_consecutive(self, trades: List[Dict], trade_type: str) -> int:
        try:
            max_consecutive = 0
            current_consecutive = 0
            
            for trade in trades:
                pnl = trade.get("pnl", 0)
                
                if trade_type == "win" and pnl > 0:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                elif trade_type == "loss" and pnl < 0:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0
            
            return max_consecutive
            
        except Exception:
            return 0
    
    def print_report(self, stats: Dict, symbol: str = "BTCUSDT"):
        try:
            print("\n" + "="*60)
            print(f"BACKTEST RESULT - {symbol}")
            print("="*60)
            
            print(f"\nTotal Trades: {stats.get('total_trades', 0)}")
            print(f"Winning Trades: {stats.get('winning_trades', 0)}")
            print(f"Losing Trades: {stats.get('losing_trades', 0)}")
            print(f"Win Rate: {stats.get('win_rate', 0):.2f}%")
            
            print(f"\nTotal Profit: ${stats.get('total_profit', 0):.2f}")
            print(f"Total Loss: ${stats.get('total_loss', 0):.2f}")
            print(f"Net PnL: ${stats.get('net_pnl', 0):.2f}")
            print(f"Net PnL %: {stats.get('net_pnl_pct', 0):.2f}%")
            
            print(f"\nAvg Profit: ${stats.get('avg_profit', 0):.2f}")
            print(f"Avg Loss: ${stats.get('avg_loss', 0):.2f}")
            print(f"Profit Factor: {stats.get('profit_factor', 0):.2f}")
            
            print(f"\nAvg Hold Time: {stats.get('avg_hold_minutes', 0):.1f} min")
            print(f"Max Drawdown: {stats.get('max_drawdown', 0):.2f}%")
            print(f"Sharpe Ratio: {stats.get('sharpe_ratio', 0):.2f}")
            
            print(f"\nMax Consecutive Wins: {stats.get('max_consecutive_wins', 0)}")
            print(f"Max Consecutive Losses: {stats.get('max_consecutive_losses', 0)}")
            
            print("\n" + "="*60)
            
        except Exception as e:
            logger.error(f"Print report error: {e}")
    
    def format_summary_log(self, stats: Dict, symbol: str) -> str:
        try:
            return (
                f"[{symbol}] Backtest: "
                f"Trades={stats.get('total_trades', 0)} | "
                f"WinRate={stats.get('win_rate', 0):.1f}% | "
                f"ProfitFactor={stats.get('profit_factor', 0):.2f} | "
                f"MaxDD={stats.get('max_drawdown', 0):.2f}% | "
                f"Sharpe={stats.get('sharpe_ratio', 0):.2f}"
            )
        except Exception:
            return f"[{symbol}] Backtest completed"
