from typing import Dict, List
from core.logger import logger


class RewardEngine:
    def __init__(self):
        logger.info("RewardEngine initialized")
    
    def calculate(self, trade: Dict) -> Dict:
        try:
            pnl_pct = trade.get("pnl_pct", 0)
            drawdown_pct = trade.get("drawdown_pct", 0)
            hold_minutes = trade.get("hold_minutes", 0)
            
            score = self._calculate_score(pnl_pct, drawdown_pct, hold_minutes)
            risk_adjusted_score = self._calculate_risk_adjusted_score(pnl_pct, drawdown_pct)
            efficiency_score = self._calculate_efficiency_score(pnl_pct, hold_minutes)
            
            return {
                "score": round(score, 4),
                "risk_adjusted_score": round(risk_adjusted_score, 4),
                "efficiency_score": round(efficiency_score, 4),
                "grade": self._get_grade(score)
            }
            
        except Exception as e:
            logger.error(f"Reward calculation error: {e}")
            return {
                "score": 0.0,
                "risk_adjusted_score": 0.0,
                "efficiency_score": 0.0,
                "grade": "N/A"
            }
    
    def _calculate_score(self, pnl_pct: float, drawdown_pct: float, hold_minutes: int) -> float:
        try:
            score = pnl_pct - drawdown_pct
            
            if hold_minutes > 0:
                time_factor = min(1.0, 60 / hold_minutes)
                score = score * time_factor
            
            return score
        except Exception:
            return 0.0
    
    def _calculate_risk_adjusted_score(self, pnl_pct: float, drawdown_pct: float) -> float:
        try:
            if drawdown_pct == 0:
                return pnl_pct
            
            risk_adjusted = pnl_pct / (abs(drawdown_pct) + 1)
            return risk_adjusted
        except Exception:
            return 0.0
    
    def _calculate_efficiency_score(self, pnl_pct: float, hold_minutes: int) -> float:
        try:
            if hold_minutes <= 0:
                return 0.0
            
            hourly_return = (pnl_pct / hold_minutes) * 60
            return hourly_return
        except Exception:
            return 0.0
    
    def _get_grade(self, score: float) -> str:
        if score >= 2.0:
            return "A+"
        elif score >= 1.0:
            return "A"
        elif score >= 0.5:
            return "B+"
        elif score >= 0.0:
            return "B"
        elif score >= -0.5:
            return "C"
        elif score >= -1.0:
            return "D"
        else:
            return "F"
    
    def calculate_batch(self, trades: List[Dict]) -> Dict:
        try:
            if not trades:
                return {
                    "avg_score": 0.0,
                    "total_trades": 0,
                    "avg_risk_adjusted": 0.0
                }
            
            scores = []
            risk_adjusted_scores = []
            
            for trade in trades:
                result = self.calculate(trade)
                scores.append(result["score"])
                risk_adjusted_scores.append(result["risk_adjusted_score"])
            
            return {
                "avg_score": round(sum(scores) / len(scores), 4),
                "total_trades": len(trades),
                "avg_risk_adjusted": round(sum(risk_adjusted_scores) / len(risk_adjusted_scores), 4)
            }
            
        except Exception as e:
            logger.error(f"Batch calculation error: {e}")
            return {
                "avg_score": 0.0,
                "total_trades": 0,
                "avg_risk_adjusted": 0.0
            }
    
    def format_log(self, result: Dict, symbol: str) -> str:
        try:
            return (
                f"[{symbol}] Reward: "
                f"score={result.get('score', 0):.4f} | "
                f"risk_adj={result.get('risk_adjusted_score', 0):.4f} | "
                f"grade={result.get('grade', 'N/A')}"
            )
        except Exception:
            return f"[{symbol}] Reward calculated"
