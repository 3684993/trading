from typing import Dict, List
from core.logger import logger


class StrategyOptimizer:
    def __init__(self):
        self.stats: Dict = {}
        logger.info("StrategyOptimizer initialized")
    
    def update(self, trades: List[Dict]) -> Dict:
        try:
            if not trades:
                return self._get_empty_stats()
            
            recent_trades = trades[-100:] if len(trades) > 100 else trades
            
            win_rate = self._calculate_win_rate(recent_trades)
            avg_profit = self._calculate_avg_profit(recent_trades)
            avg_loss = self._calculate_avg_loss(recent_trades)
            profit_factor = self._calculate_profit_factor(recent_trades)
            avg_hold_minutes = self._calculate_avg_hold_minutes(recent_trades)
            total_pnl = self._calculate_total_pnl(recent_trades)
            max_drawdown = self._calculate_max_drawdown(recent_trades)
            sharpe_ratio = self._calculate_sharpe_ratio(recent_trades)
            
            self.stats = {
                "win_rate": round(win_rate, 4),
                "avg_profit": round(avg_profit, 4),
                "avg_loss": round(avg_loss, 4),
                "profit_factor": round(profit_factor, 4),
                "avg_hold_minutes": round(avg_hold_minutes, 1),
                "total_pnl": round(total_pnl, 4),
                "max_drawdown": round(max_drawdown, 4),
                "sharpe_ratio": round(sharpe_ratio, 4),
                "total_trades": len(recent_trades)
            }
            
            return self.stats
            
        except Exception as e:
            logger.error(f"Strategy optimizer update error: {e}")
            return self._get_empty_stats()
    
    def _get_empty_stats(self) -> Dict:
        return {
            "win_rate": 0.0,
            "avg_profit": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "avg_hold_minutes": 0.0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "total_trades": 0
        }
    
    def _calculate_win_rate(self, trades: List[Dict]) -> float:
        try:
            if not trades:
                return 0.0
            
            wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
            return wins / len(trades)
        except Exception:
            return 0.0
    
    def _calculate_avg_profit(self, trades: List[Dict]) -> float:
        try:
            profits = [t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) > 0]
            return sum(profits) / len(profits) if profits else 0.0
        except Exception:
            return 0.0
    
    def _calculate_avg_loss(self, trades: List[Dict]) -> float:
        try:
            losses = [t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) < 0]
            return sum(losses) / len(losses) if losses else 0.0
        except Exception:
            return 0.0
    
    def _calculate_profit_factor(self, trades: List[Dict]) -> float:
        try:
            gross_profit = sum(t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) > 0)
            gross_loss = abs(sum(t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) < 0))
            
            if gross_loss == 0:
                return gross_profit if gross_profit > 0 else 0.0
            
            return gross_profit / gross_loss
        except Exception:
            return 0.0
    
    def _calculate_avg_hold_minutes(self, trades: List[Dict]) -> float:
        try:
            hold_times = [t.get("hold_minutes", 0) for t in trades]
            return sum(hold_times) / len(hold_times) if hold_times else 0.0
        except Exception:
            return 0.0
    
    def _calculate_total_pnl(self, trades: List[Dict]) -> float:
        try:
            return sum(t.get("pnl_pct", 0) for t in trades)
        except Exception:
            return 0.0
    
    def _calculate_max_drawdown(self, trades: List[Dict]) -> float:
        try:
            if not trades:
                return 0.0
            
            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0
            
            for trade in trades:
                cumulative += trade.get("pnl_pct", 0)
                
                if cumulative > peak:
                    peak = cumulative
                
                dd = peak - cumulative
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
            avg_return = sum(returns) / len(returns)
            
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_dev = variance ** 0.5
            
            if std_dev == 0:
                return 0.0
            
            return avg_return / std_dev
        except Exception:
            return 0.0
    
    def get_stats(self) -> Dict:
        return self.stats
    
    def format_log(self, stats: Dict, symbol: str) -> str:
        try:
            return (
                f"[{symbol}] Learning: "
                f"WinRate={stats.get('win_rate', 0):.2f} | "
                f"ProfitFactor={stats.get('profit_factor', 0):.2f} | "
                f"AvgHold={stats.get('avg_hold_minutes', 0):.0f}min | "
                f"TotalPnL={stats.get('total_pnl', 0):.2f}%"
            )
        except Exception:
            return f"[{symbol}] Learning stats"
