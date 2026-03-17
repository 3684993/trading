from typing import Dict, Optional
from core.logger import logger


class RiskManager:
    def __init__(
        self,
        max_position_size: float = 0.05,
        max_drawdown_pct: float = -8.0,
        max_loss_pct: float = -10.0,
        max_hold_minutes: int = 120
    ):
        self.max_position_size = max_position_size
        self.max_drawdown_pct = max_drawdown_pct
        self.max_loss_pct = max_loss_pct
        self.max_hold_minutes = max_hold_minutes
        
        self.risk_events: list = []
        
        logger.info(
            f"RiskManager initialized: "
            f"max_size={max_position_size}, "
            f"max_dd={max_drawdown_pct}%, "
            f"max_loss={max_loss_pct}%, "
            f"max_hold={max_hold_minutes}min"
        )
    
    def check_risk(self, position_state: Dict, decision: Dict = None) -> Dict:
        try:
            if not position_state.get("has_position"):
                return {
                    "status": "ok",
                    "action": None,
                    "reason": "No position"
                }
            
            position_size = position_state.get("position_size", 0)
            current_pnl_pct = position_state.get("current_pnl_pct", 0)
            drawdown_pct = position_state.get("drawdown_pct", 0)
            hold_minutes = position_state.get("hold_minutes", 0)
            
            risk_checks = []
            
            if position_size > self.max_position_size:
                risk_checks.append({
                    "type": "position_size_exceeded",
                    "value": position_size,
                    "limit": self.max_position_size
                })
            
            if current_pnl_pct < self.max_loss_pct:
                risk_checks.append({
                    "type": "max_loss_exceeded",
                    "value": current_pnl_pct,
                    "limit": self.max_loss_pct
                })
            
            if drawdown_pct > abs(self.max_drawdown_pct):
                risk_checks.append({
                    "type": "max_drawdown_exceeded",
                    "value": drawdown_pct,
                    "limit": self.max_drawdown_pct
                })
            
            if hold_minutes > self.max_hold_minutes:
                risk_checks.append({
                    "type": "max_hold_time_exceeded",
                    "value": hold_minutes,
                    "limit": self.max_hold_minutes
                })
            
            if risk_checks:
                risk_event = {
                    "timestamp": position_state.get("symbol"),
                    "checks": risk_checks,
                    "position_state": position_state
                }
                self.risk_events.append(risk_event)
                
                logger.warning(f"Risk triggered: {risk_checks}")
                
                return {
                    "status": "risk_triggered",
                    "action": "force_close",
                    "reason": risk_checks[0]["type"],
                    "details": risk_checks
                }
            
            return {
                "status": "ok",
                "action": None,
                "reason": "All checks passed"
            }
            
        except Exception as e:
            logger.error(f"Risk check error: {e}")
            return {
                "status": "error",
                "action": None,
                "reason": str(e)
            }
    
    def check_entry_risk(self, decision: Dict, position_state: Dict) -> Dict:
        try:
            action = decision.get("action", "hold")
            
            if action in ["open_long", "open_short"]:
                if position_state.get("has_position"):
                    return {
                        "status": "rejected",
                        "reason": "Position already exists"
                    }
                
                size_range = decision.get("size", [0.01, 0.02])
                max_size = max(size_range) if isinstance(size_range, list) else size_range
                
                if max_size > self.max_position_size:
                    return {
                        "status": "rejected",
                        "reason": f"Position size {max_size} exceeds limit {self.max_position_size}"
                    }
            
            return {
                "status": "approved",
                "reason": "Entry risk check passed"
            }
            
        except Exception as e:
            logger.error(f"Entry risk check error: {e}")
            return {
                "status": "error",
                "reason": str(e)
            }
    
    def format_risk_log(self, risk_result: Dict) -> str:
        try:
            status = risk_result.get("status", "unknown")
            action = risk_result.get("action")
            reason = risk_result.get("reason", "")
            
            if status == "ok":
                return "Risk Check: OK"
            elif status == "risk_triggered":
                return f"Risk Check: TRIGGERED - {reason} -> {action}"
            else:
                return f"Risk Check: {status} - {reason}"
                
        except Exception as e:
            return f"Risk Check: Error - {e}"
    
    def get_risk_summary(self) -> Dict:
        return {
            "total_risk_events": len(self.risk_events),
            "max_position_size": self.max_position_size,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_loss_pct": self.max_loss_pct,
            "max_hold_minutes": self.max_hold_minutes
        }
