import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path


LOG_ALLOW_TOKENS = [
    "[AI_DECISION]",
    "[ANALYSIS]",
    "ENTRY_TIMING",
    "ENTRY_MODE",
    "[EXECUTION]",
    "EXEC_STATE",
    "[ORDER_SUBMIT]",
    "[ORDER_FILLED]",
    "[ORDER_CANCEL]",
    "[SLIPPAGE]",
    "[POSITION]",
    "[RISK_TRIGGER]",
    "[STAGE]",
    "[LOG_CLEANUP]",
    "[SYSTEM_ERROR]",
    "[EXPOSURE]",
]


class TradingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            return True
        for token in LOG_ALLOW_TOKENS:
            if token in msg:
                return True
        return False


def cleanup_old_logs(max_age_seconds: int = 7200) -> int:
    log_dir = Path(__file__).parent.parent / "logs"
    if not log_dir.exists():
        return 0
    cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
    deleted = 0
    for log_file in log_dir.glob("*.log"):
        try:
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            if mtime < cutoff:
                log_file.unlink(missing_ok=True)
                deleted += 1
        except Exception:
            continue
    return deleted


def setup_logger(name: str = "ai_quant_trader", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if logger.handlers:
        return logger
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    file_handler = logging.FileHandler(
        log_dir / f"trading_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8"
    )
    file_handler.setLevel(level)
    
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    log_filter = TradingLogFilter()
    console_handler.addFilter(log_filter)
    file_handler.addFilter(log_filter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger


logger = setup_logger()
