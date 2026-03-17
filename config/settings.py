import os
from dotenv import load_dotenv
from pathlib import Path


class Settings:
    def __init__(self):
        env_path = Path(__file__).parent./ ".env"
        load_dotenv(env_path)
        
        self.binance_api_key = os.getenv("BINANCE_API_KEY", "")
        self.binance_api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.binance_testnet_url = os.getenv("BINANCE_TESTNET_URL", "https://demo-fapi.binance.com")
        self.socks5_proxy = os.getenv("SOCKS5_PROXY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.openai_base_url = os.getenv("OPENAI_BASE_URL", "")
        self.openai_model = os.getenv("OPENAI_MODEL", "")
        
        self.symbol = os.getenv("SYMBOL", "BTCUSDT")
        self.timeframes = ["1m", "5m", "15m"]
        self.kline_limit = 200
        self.loop_interval = 5  # 降频：从 60 秒改为 5 秒
        self.intra_cycle_check_seconds = int(os.getenv("INTRA_CYCLE_CHECK_SECONDS", "5"))
        
        trading_env = os.getenv("TRADING_ENV", "testnet").lower()
        self.trading_env = "testnet" if trading_env not in ["live", "production"] else "live"
        self.is_testnet = self.trading_env == "testnet"
        
        self.min_order_interval_seconds = int(os.getenv("MIN_ORDER_INTERVAL_SECONDS", "300"))
        self.trend_confirmation_count = int(os.getenv("TREND_CONFIRMATION_COUNT", "3"))
        self.min_hold_minutes = int(os.getenv("MIN_HOLD_MINUTES", "30"))
        self.max_hold_minutes = int(os.getenv("MAX_HOLD_MINUTES", "120"))
        
        self.max_position_size = float(os.getenv("MAX_POSITION_SIZE", "0.05"))
        self.max_drawdown_pct = float(os.getenv("MAX_DRAWDOWN_PCT", "-8.0"))
        self.max_loss_pct = float(os.getenv("MAX_LOSS_PCT", "-10.0"))
        
        self.min_trade_size = float(os.getenv("MIN_TRADE_SIZE", "0.005"))
        self.max_trade_size = float(os.getenv("MAX_TRADE_SIZE", "0.02"))
        self.max_orders = int(os.getenv("MAX_ORDERS", "4"))
        self.order_adjust_interval = int(os.getenv("ORDER_ADJUST_INTERVAL", "5"))
        self.order_timeout = int(os.getenv("ORDER_TIMEOUT", "180"))
        self.distance_cancel = float(os.getenv("DISTANCE_CANCEL", "300"))
        self.distance_max = float(os.getenv("DISTANCE_MAX", "400"))
        self.price_gap = float(os.getenv("PRICE_GAP", "100"))

        # 新增交易执行参数配置
        self.PARAMS = {
            "max_trade_size": float(os.getenv("MAX_TRADE_SIZE", "0.02")),
            "min_trade_size": float(os.getenv("MIN_TRADE_SIZE", "0.005")),
            "order_adjust_interval": int(os.getenv("ORDER_ADJUST_INTERVAL", "5")),
            "order_adjust_timeout": int(os.getenv("ORDER_ADJUST_TIMEOUT", "120")),
            "distance_cancel": float(os.getenv("DISTANCE_CANCEL", "300")),
            "distance_max": float(os.getenv("DISTANCE_MAX", "400")),
            "trend_strength_threshold": float(os.getenv("TREND_STRENGTH_THRESHOLD", "0.7")),
            "min_hold_time": int(os.getenv("MIN_HOLD_TIME", "120")),
            "max_pending_orders": int(os.getenv("MAX_PENDING_ORDERS", "4")),
            "order_timeout": int(os.getenv("ORDER_TIMEOUT", "180")),
            "price_step_ratio": float(os.getenv("PRICE_STEP_RATIO", "0.0005")),
            "max_orders": int(os.getenv("MAX_ORDERS", "4")),
            "price_gap": float(os.getenv("PRICE_GAP", "100")),
            "too_far_distance": float(os.getenv("TOO_FAR_DISTANCE", "300")),
            "signal_confirmations": int(os.getenv("SIGNAL_CONFIRMATIONS", "3"))
        }
        
    def validate(self):
        if not self.binance_api_key:
            raise ValueError("BINANCE_API_KEY is not set in .env file")
        if not self.binance_api_secret:
            raise ValueError("BINANCE_API_SECRET is not set in .env file")
        return True
    
    def get_trading_config(self) -> dict:
        return {
            "trading_env": self.trading_env,
            "is_testnet": self.is_testnet,
            "min_order_interval_seconds": self.min_order_interval_seconds,
            "trend_confirmation_count": self.trend_confirmation_count,
            "min_hold_minutes": self.min_hold_minutes,
            "max_hold_minutes": self.max_hold_minutes,
            "max_position_size": self.max_position_size,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_loss_pct": self.max_loss_pct,
            "intra_cycle_check_seconds": self.intra_cycle_check_seconds
        }


settings = Settings()
