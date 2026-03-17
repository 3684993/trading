from datetime import datetime, timezone
from typing import Optional


def get_current_timestamp_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def get_current_timestamp_s() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def ms_to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def datetime_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def format_timestamp(ms: int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    dt = ms_to_datetime(ms)
    return dt.strftime(fmt)


def get_timeframe_seconds(timeframe: str) -> int:
    timeframe_map = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400
    }
    return timeframe_map.get(timeframe, 60)
