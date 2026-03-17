import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logger(name: str = "ai_quant_trader", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 关键修复：防止重复注册 handler
    if not logger.handlers:
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

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger


logger = setup_logger()
