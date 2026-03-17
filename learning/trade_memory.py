import json
import csv
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
from core.logger import logger


class TradeMemory:
    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent / "data"
        self.data_dir.mkdir(exist_ok=True)
        
        self.json_path = self.data_dir / "trade_history.json"
        self.csv_path = self.data_dir / "trade_history.csv"
        
        self.trades: List[Dict] = []
        self._load_history()
        
        logger.info(f"TradeMemory initialized with {len(self.trades)} historical trades")
    
    def _load_history(self):
        try:
            if self.json_path.exists():
                with open(self.json_path, "r", encoding="utf-8") as f:
                    self.trades = json.load(f)
                logger.info(f"Loaded {len(self.trades)} trades from {self.json_path}")
        except Exception as e:
            logger.error(f"Load history error: {e}")
            self.trades = []
    
    def save_trade(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        hold_minutes: int,
        market_regime: str,
        trend: str,
        momentum: str,
        volatility: str,
        side: str = "long"
    ) -> Dict:
        try:
            trade = {
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct, 2),
                "hold_minutes": hold_minutes,
                "market_regime": market_regime,
                "trend": trend,
                "momentum": momentum,
                "volatility": volatility,
                "timestamp": datetime.now().isoformat(),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S")
            }
            
            self.trades.append(trade)
            
            self._save_to_json()
            self._save_to_csv(trade)
            
            logger.info(
                f"Trade saved: {symbol} {side} "
                f"entry={entry_price:.2f} exit={exit_price:.2f} "
                f"pnl={pnl_pct:.2f}% hold={hold_minutes}min"
            )
            
            return {"status": "saved", "trade": trade}
            
        except Exception as e:
            logger.error(f"Save trade error: {e}")
            return {"status": "error", "message": str(e)}
    
    def _save_to_json(self):
        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(self.trades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Save to JSON error: {e}")
    
    def _save_to_csv(self, trade: Dict):
        try:
            file_exists = self.csv_path.exists()
            
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                fieldnames = [
                    "symbol", "side", "entry_price", "exit_price", "pnl_pct",
                    "hold_minutes", "market_regime", "trend", "momentum",
                    "volatility", "timestamp", "date", "time"
                ]
                
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                
                if not file_exists:
                    writer.writeheader()
                
                writer.writerow(trade)
                
        except Exception as e:
            logger.error(f"Save to CSV error: {e}")
    
    def get_recent_trades(self, limit: int = 100) -> List[Dict]:
        try:
            return self.trades[-limit:] if len(self.trades) > limit else self.trades
        except Exception as e:
            logger.error(f"Get recent trades error: {e}")
            return []
    
    def get_trades_by_date(self, date: str) -> List[Dict]:
        try:
            return [t for t in self.trades if t.get("date") == date]
        except Exception as e:
            logger.error(f"Get trades by date error: {e}")
            return []
    
    def get_trades_by_symbol(self, symbol: str) -> List[Dict]:
        try:
            return [t for t in self.trades if t.get("symbol") == symbol]
        except Exception as e:
            logger.error(f"Get trades by symbol error: {e}")
            return []
    
    def get_winning_trades(self) -> List[Dict]:
        try:
            return [t for t in self.trades if t.get("pnl_pct", 0) > 0]
        except Exception as e:
            logger.error(f"Get winning trades error: {e}")
            return []
    
    def get_losing_trades(self) -> List[Dict]:
        try:
            return [t for t in self.trades if t.get("pnl_pct", 0) < 0]
        except Exception as e:
            logger.error(f"Get losing trades error: {e}")
            return []
    
    def clear_history(self):
        try:
            self.trades = []
            if self.json_path.exists():
                os.remove(self.json_path)
            if self.csv_path.exists():
                os.remove(self.csv_path)
            logger.info("Trade history cleared")
        except Exception as e:
            logger.error(f"Clear history error: {e}")
