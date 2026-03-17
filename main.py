"""
AI 智能委托网格因子量化交易系统 - v4.0 过热保护版
启动方式：python main.py
登录：admin/admin

========================================================
v4.0 新增（在 v3.0 基础上）：

【核心问题】连续盈利后追高/追低，行情回撤导致亏损扩大
【解决方案】三层过热保护机制

层1 - 连胜冷却期（Win-Streak Cooldown）
  连续盈利 N 笔后，自动进入观望期，禁止追入
  冷静观察市场是否真的继续趋势，还是准备回撤

层2 - 市场过热检测（Market Overheat Detection）
  综合检测：累计涨幅、RSI 高位、布林带挤压、ATR 异常
  过热时：自动放大入场偏移（更深挂单），降低成交概率
  严重过热时：直接拒绝建仓

层3 - 累计涨幅熔断（Cumulative Move Circuit Breaker）
  追踪最近 N 笔交易的入场价格
  若当前价格相对首笔入场价累计涨幅超过阈值，触发熔断
  熔断期间禁止同向追入，只允许等待回撤后入场

额外改进：
- AI 提示词加入市场过热信息，辅助判断
- 冷却期结束后要求价格回归均线区间才允许再次建仓
- 连胜记录写入 trade_history，便于复盘分析
========================================================
"""

import os
import sys
import json
import time
import math
import logging
import threading
import requests
import psutil
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

from config.settings import settings
from binance.um_futures import UMFutures

# =====================================
# 单实例检测
# =====================================

def check_single_instance():
    current_pid = os.getpid()
    current_script = os.path.abspath(__file__)
    running_instances = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                if proc.info['cmdline'] and len(proc.info['cmdline']) > 1:
                    cmdline = ' '.join(proc.info['cmdline'])
                    if 'main.py' in cmdline and current_script in cmdline:
                        if proc.info['pid'] != current_pid:
                            running_instances.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if running_instances:
        print(f"❌ 系统已在运行，PID: {running_instances}")
        sys.exit(1)
    return True

check_single_instance()

sys.path.insert(0, str(Path(__file__).parent))

app = Flask(__name__, static_folder='static', static_url_path='')
app.secret_key = os.urandom(24)

# =====================================
# 配置
# =====================================

CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "system": {
        "name": "AI 智能委托网格因子量化交易系统",
        "version": "4.0.0",
        "mode": "testnet",
        "status": "stopped",
        "start_time": None,
        "admin_password": generate_password_hash("admin")
    },
    "exchange": {
        "api_key": settings.binance_api_key,
        "api_secret": settings.binance_api_secret,
        "testnet_url": "https://demo-fapi.binance.com",
        "live_url": "https://fapi.binance.com",
        "symbol": "BTCUSDT",
        "leverage": 10,
        "mode": "HEDGE",
        "socks5_proxy": settings.socks5_proxy
    },
    "ai": {
        "lmstudio_url": "http://192.168.1.114:1234/v1/chat/completions",
        "model": "qwen/qwen3-14b",
        "temperature": 0.2,
        "max_tokens": 700,
        "timeout": 25,
        "min_wait_time": 2
    },
    "trading": {
        "grid_levels": 5,
        "base_qty": 0.002,
        "entry_offset": 50,
        "grid_spacing": 80,
        "tp_distance": 130,
        "tp_spacing": 100,
        "tp_adjust_interval": 600,
        "tp_adjust_threshold": 0.2,
        "tp_min_profit": 0.5,
        "tp_qty_mismatch_tolerance": 0.0005,
        "tp_rebuild_cooldown": 60,
        "tp_distance_update_threshold": 20,
        "entry_adjust_interval": 300,
        "order_realign_cooldown": 60,
        "order_realign_strength_threshold": 50,
        "force_entry_timeout": 600,
        "force_entry_offset_ratio": 0.5,
        "force_entry_spacing_ratio": 0.8,
        "post_close_cooldown": 15,
        "post_close_cancel_delay": 0.3,
        "position_timeout_short": 300,
        "position_timeout_mid": 900,
        "position_timeout_long": 1800,
        "position_timeout_force": 3600,
        "ai_strength_high": 70,
        "ai_strength_medium": 40,
        "ai_strength_low": 20,
        "min_samples_for_trend": 3,
        "distance_shrink_threshold": 0.3,
        "distance_expand_threshold": 0.5,
        "profit_lock_min_pnl_pct": 0.3,
        "profit_lock_confirmations": 2,
        "profit_lock_cooldown": 60,
        "fee_rate": 0.0004,
        "min_qty": 0.001,
        "max_qty": 0.1,
        "min_order_value": 100,
        "max_log_buffer": 500,
        "ai_polling_interval": 5,
        "cancel_order_delay": 0.2,
        "close_market_max_attempts": 6,
        "close_market_retry_delay": 0.25,
        "batch_close_offset": 10,
        "stop_working_type": "MARK_PRICE",
        "timeframe_1h": True,
        "timeframe_30m": True,
        "timeframe_15m": True,
        "timeframe_5m": True,
        "timeframe_1m": True,
        "weight_1h": 0.4,
        "weight_30m": 0.3,
        "weight_15m": 0.15,
        "weight_5m": 0.1,
        "weight_1m": 0.05,
        "entry_confirm_min_strength": 35,
        "entry_confirm_candle_body": 0.3,
        "entry_fake_breakout_atr": 0.3,

        # =============================================
        # 【v4.0 新增】过热保护参数
        # =============================================

        # --- 层1：连胜冷却期 ---
        "win_streak_threshold": 2,         # 连续盈利多少笔后触发冷却（默认2笔）
        "win_streak_cooldown": 120,        # 冷却期时长（秒），冷却期内禁止追入
        "win_streak_require_pullback": True,  # 冷却结束后是否要求价格回归到均线附近才能再次入场
        "win_streak_pullback_pct": 0.3,    # 回归要求：价格距离 EMA20 在 ATR 的 N 倍以内

        # --- 层2：市场过热检测 ---
        "overheat_rsi_long": 75.0,         # 做多方向：RSI 超过此值视为过热
        "overheat_rsi_short": 25.0,        # 做空方向：RSI 低于此值视为过热
        "overheat_bb_percent_b_long": 0.88,  # 做多：布林带 %B 超过此值视为过热
        "overheat_bb_percent_b_short": 0.12, # 做空：布林带 %B 低于此值视为过热
        "overheat_offset_multiplier": 2.5, # 过热时入场偏移乘以此倍数（更深挂单）
        "overheat_block_entry": True,      # 严重过热时是否直接阻止建仓（True=阻止，False=仅加大偏移）
        "overheat_rsi_block_long": 80.0,   # 做多方向：RSI 超过此值直接阻止建仓
        "overheat_rsi_block_short": 20.0,  # 做空方向：RSI 低于此值直接阻止建仓

        # --- 层3：累计涨幅熔断 ---
        "circuit_breaker_enabled": True,   # 是否启用累计涨幅熔断
        "circuit_breaker_pct": 2.5,        # 累计涨幅百分比阈值（相对最近入场价，默认2.5%）
        "circuit_breaker_window": 3,       # 回溯最近 N 笔交易的入场价格
        "circuit_breaker_cooldown": 300,   # 熔断后的冷却期（秒），等待回撤后再允许入场
    },
    "risk": {
        "max_position": 0.1,
        "max_loss_percent": 2.0,
        "enable_auto_stop_loss": True,
        "enable_auto_reduce": True
    }
}


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        for section, values in DEFAULT_CONFIG.items():
            if section not in saved:
                saved[section] = values
            elif isinstance(values, dict):
                for k, v in values.items():
                    if k not in saved[section]:
                        saved[section][k] = v
        return saved
    else:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


config = load_config()
SYSTEM_START_TIME = time.time()

# =====================================
# 日志系统
# =====================================

log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)

file_handler = RotatingFileHandler(
    log_dir / "system.log", maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
)
file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

log_buffer = []
MAX_LOG_BUFFER = config["trading"].get("max_log_buffer", 500)


class LogBufferHandler(logging.Handler):
    def emit(self, record):
        msg = record.getMessage()
        if 'GET /api/' in msg or 'POST /api/' in msg:
            return
        log_buffer.append({
            "timestamp": datetime.now().isoformat(),
            "level": record.levelname,
            "message": self.format(record)
        })
        if len(log_buffer) > MAX_LOG_BUFFER:
            log_buffer.pop(0)


lbh = LogBufferHandler()
lbh.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))
logger.addHandler(lbh)

# =====================================
# Binance 客户端
# =====================================

proxies = None
if config["exchange"]["socks5_proxy"]:
    pu = config["exchange"]["socks5_proxy"]
    if pu.startswith("socks5://") and not pu.startswith("socks5h://"):
        pu = pu.replace("socks5://", "socks5h://")
    proxies = {"http": pu, "https": pu}

base_url = (
    config["exchange"]["testnet_url"]
    if config["system"]["mode"] == "testnet"
    else config["exchange"]["live_url"]
)

client = UMFutures(
    key=config["exchange"]["api_key"],
    secret=config["exchange"]["api_secret"],
    base_url=base_url,
    proxies=proxies
)

SYMBOL = config["exchange"]["symbol"]

# =====================================
# 全局状态
# =====================================

trading_state = {
    "running": False,
    "thread": None,
    "last_update": None,
    "total_trades": 0,
    "total_profit": 0.0,
    "win_count": 0,
    "loss_count": 0,
    "volume_24h": 0.0,
    "position": None,
    "orders": [],
    "position_open_time": None,
    "failed_close_attempts": 0,
    "price_distance_history": [],
    "last_position": 0,
    "last_entry_price": 0.0,
    "closed_positions_profit": 0.0,
    "no_position_start_time": None,
    "last_tp_distance": None,
    "last_tp_update_time": 0,
    "max_favorable_pnl_pct": 0.0,
    "adverse_trend_count": 0,
    "last_profit_lock_time": 0,
    "trade_history": [],
    "current_trade": None,
    # v3.0 冷静期
    "post_close_time": 0,
    "post_close_active": False,
    "last_order_realign_time": 0,

    # ============================================
    # v4.0 过热保护状态
    # ============================================

    # 层1：连胜跟踪
    "win_streak_count": 0,          # 当前连续盈利笔数
    "win_streak_direction": None,   # 连胜方向（LONG/SHORT），同方向才累计
    "win_streak_cooldown_active": False,  # 是否处于连胜冷却期
    "win_streak_cooldown_start": 0, # 连胜冷却期开始时间
    "win_streak_last_entry_price": 0.0,  # 连胜期间最后一笔的入场价格

    # 层2：过热检测（记录上次过热状态，避免重复日志）
    "last_overheat_level": "NORMAL",  # NORMAL / WARM / HOT / BURNING

    # 层3：熔断状态
    "circuit_breaker_active": False,  # 是否触发熔断
    "circuit_breaker_start": 0,       # 熔断开始时间
    "circuit_breaker_direction": None,  # 被熔断的方向（LONG/SHORT）
    "recent_entry_prices": [],        # 最近 N 笔入场价格（用于累计涨幅计算）
}

order_tracking = {
    'last_adjust_time': 0,
    'closest_price': None,
    'min_distance': float('inf'),
    'trend': 'UNKNOWN',
    'price_history': []
}

MIN_POSITION_QTY = 0.002

ai_context_memory = {
    "decisions": [],
    "max_history": 5
}

# =====================================
# 基础工具函数（与 v3 相同，保持不变）
# =====================================

def get_price():
    try:
        return float(client.ticker_price(symbol=SYMBOL)["price"])
    except Exception as e:
        logger.error(f"[PRICE] 获取价格失败：{e}")
        return 0.0


def get_orders():
    try:
        orders = client.get_orders(symbol=SYMBOL)
        return [o for o in orders if o["status"] in ["NEW", "PARTIALLY_FILLED"]]
    except Exception as e:
        logger.error(f"[ORDERS] 获取委托失败：{e}")
        return []


def cancel_order(order_id):
    try:
        client.cancel_order(symbol=SYMBOL, orderId=order_id)
        logger.info(f"[CANCEL] {order_id}")
        return True
    except Exception as e:
        logger.error(f"[CANCEL] 取消委托失败：{e}")
        return False


def cancel_all_orders(orders=None, reason=""):
    try:
        if orders is None:
            orders = get_orders()
        cancelled = 0
        for o in orders:
            oid = o.get("orderId")
            if oid and cancel_order(oid):
                cancelled += 1
            time.sleep(config["trading"].get("cancel_order_delay", 0.2))
        if cancelled:
            logger.info(f"[CANCEL_ALL] 已取消 {cancelled} 个委托 | reason={reason}")
        return cancelled
    except Exception as e:
        logger.warning(f"[CANCEL_ALL] 失败：{e}")
        return 0


_symbol_info = None


def get_precision():
    global _symbol_info
    if _symbol_info is None:
        try:
            ei = client.exchange_info()
            for s in ei['symbols']:
                if s['symbol'] == SYMBOL:
                    _symbol_info = s
                    break
        except Exception as e:
            logger.error(f"[PRECISION] {e}")
            return 1, 3
    if _symbol_info:
        ts = _symbol_info['filters'][0]['tickSize']
        ss = _symbol_info['filters'][2]['stepSize']
        pp = ts.find('1') - ts.find('.') if '.' in ts else 0
        qp = ss.find('1') - ss.find('.') if '.' in ss else 3
        return pp, qp
    return 1, 3


def floor_to_precision(value, precision):
    try:
        factor = 10 ** int(precision)
        return math.floor(float(value) * factor) / factor
    except Exception:
        return float(value) if value else 0.0


def limit_order(side, price, qty):
    try:
        pp, qp = get_precision()
        price = round(float(price), pp)
        qty = floor_to_precision(qty, qp)
        if qty <= 0:
            logger.warning(f"[LIMIT] 数量过小，跳过 | {side} qty={qty}")
            return None
        result = client.new_order(
            symbol=SYMBOL, side=side, type="LIMIT",
            price=str(price), quantity=str(qty), timeInForce="GTC"
        )
        logger.info(f"[ORDER_CREATE] {side} {qty:.{qp}f} @ {price:.{pp}f} | ID:{result.get('orderId')}")
        return result
    except Exception as e:
        logger.error(f"[ORDER] 委托失败：{e}")
        return None


def market_order(side, qty):
    try:
        _, qp = get_precision()
        qty = floor_to_precision(qty, qp)
        if qty <= 0:
            return None
        result = client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=str(qty))
        logger.info(f"[MARKET] {side} {qty}")
        return result
    except Exception as e:
        logger.error(f"[MARKET] 失败：{e}")
        return None


def close_position_market(reason="", cancel_open_orders=True):
    try:
        if cancel_open_orders:
            cancel_all_orders(reason=f"PRE_CLOSE:{reason}")
            time.sleep(config["trading"].get("post_close_cancel_delay", 0.3))
        for attempt in range(int(config["trading"].get("close_market_max_attempts", 6))):
            pos, _ = get_position()
            if abs(pos) <= 0:
                _activate_post_close_cooldown(reason)
                return True
            side = "SELL" if pos > 0 else "BUY"
            market_order(side, abs(pos))
            time.sleep(float(config["trading"].get("close_market_retry_delay", 0.25)))
        pos, _ = get_position()
        if abs(pos) > 0:
            logger.error(f"[CLOSE] 平仓后仍有持仓 {pos:.6f}")
            return False
        _activate_post_close_cooldown(reason)
        return True
    except Exception as e:
        logger.error(f"[CLOSE] 失败：{e}")
        return False


def _activate_post_close_cooldown(reason=""):
    cooldown = config["trading"].get("post_close_cooldown", 15)
    trading_state["post_close_time"] = time.time()
    trading_state["post_close_active"] = True
    remaining_orders = get_orders()
    if remaining_orders:
        logger.warning(f"[COOLDOWN] 平仓后发现剩余委托 {len(remaining_orders)} 个，清除")
        cancel_all_orders(remaining_orders, reason="POST_CLOSE_CLEANUP")
        time.sleep(0.3)
    logger.info(f"[COOLDOWN] 平仓冷静期激活 | 时长：{cooldown}s | 原因：{reason}")


def is_in_post_close_cooldown():
    if not trading_state.get("post_close_active", False):
        return False
    cooldown = config["trading"].get("post_close_cooldown", 15)
    elapsed = time.time() - trading_state.get("post_close_time", 0)
    if elapsed >= cooldown:
        trading_state["post_close_active"] = False
        logger.info(f"[COOLDOWN] 平仓冷静期结束")
        return False
    logger.info(f"[COOLDOWN] 冷静期中 | 剩余：{cooldown - elapsed:.1f}s")
    return True


def get_position():
    try:
        pos = client.get_position_risk(symbol=SYMBOL)
        if not pos:
            return 0.0, 0.0
        return float(pos[0]["positionAmt"]), float(pos[0]["entryPrice"])
    except Exception as e:
        logger.error(f"[POSITION] 获取失败：{e}")
        return 0.0, 0.0


def get_account_balance():
    try:
        account = client.account()
        for a in account.get('assets', []):
            if a['asset'] == 'USDT':
                return float(a['walletBalance'])
        return 0.0
    except Exception as e:
        logger.error(f"[BALANCE] 获取失败：{e}")
        return 0.0

# =====================================
# K线与技术指标
# =====================================

def klines(interval="1m", limit=120):
    try:
        data = client.klines(symbol=SYMBOL, interval=interval, limit=limit)
        result = {'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}
        for k in data:
            result['open'].append(float(k[1]))
            result['high'].append(float(k[2]))
            result['low'].append(float(k[3]))
            result['close'].append(float(k[4]))
            result['volume'].append(float(k[5]))
        return result
    except Exception as e:
        logger.error(f"[KLINES] 失败：{e}")
        return {'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}


def calculate_ema(data, period):
    if len(data) < period:
        return data[-1] if data else 0
    mult = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for i in range(period, len(data)):
        ema = (data[i] - ema) * mult + ema
    return ema


def calculate_rsi(data, period=14):
    if len(data) < period + 1:
        return 50
    gains = [max(data[i] - data[i-1], 0) for i in range(1, len(data))]
    losses = [max(data[i-1] - data[i], 0) for i in range(1, len(data))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100 if al == 0 else 100 - (100 / (1 + ag / al))


def calculate_macd(data, fast=12, slow=26, signal=9):
    if len(data) < slow + signal:
        return {'macd': 0, 'signal': 0, 'histogram': 0, 'trend': 'NEUTRAL'}
    macd_vals = [calculate_ema(data[:i+1], fast) - calculate_ema(data[:i+1], slow)
                 for i in range(slow, len(data))]
    macd_line = macd_vals[-1]
    sig_line = calculate_ema(macd_vals, signal) if len(macd_vals) >= signal else 0
    hist = macd_line - sig_line
    return {'macd': macd_line, 'signal': sig_line, 'histogram': hist,
            'trend': 'BULLISH' if hist > 0 else 'BEARISH' if hist < 0 else 'NEUTRAL'}


def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0
    tr_vals = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
               for i in range(1, len(closes))]
    return sum(tr_vals[-period:]) / period


def calculate_bollinger(closes, period=20, num_std=2):
    if len(closes) < period:
        return {'upper': 0, 'middle': 0, 'lower': 0, 'bandwidth': 0, 'percent_b': 0.5}
    recent = closes[-period:]
    mid = sum(recent) / period
    std = (sum((x - mid)**2 for x in recent) / period) ** 0.5
    upper = mid + num_std * std
    lower = mid - num_std * std
    bw = (upper - lower) / mid if mid else 0
    pb = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {'upper': upper, 'middle': mid, 'lower': lower, 'bandwidth': bw, 'percent_b': pb}


def calculate_candle_quality(opens, highs, lows, closes, lookback=3):
    if len(closes) < lookback + 1:
        return 0.5
    scores = []
    for i in range(-lookback, 0):
        cr = highs[i] - lows[i]
        scores.append(abs(closes[i] - opens[i]) / cr if cr else 0)
    return sum(scores) / len(scores) if scores else 0.5


def analyze_volume(volumes, closes):
    if len(volumes) < 20:
        return 'NORMAL'
    avg = sum(volumes[-20:]) / 20
    cur = volumes[-1]
    r = cur / avg if avg else 1
    return 'SURGE' if r > 1.5 else 'INCREASING' if r > 1.2 else 'DECREASING' if r < 0.8 else 'NORMAL'


def indicators(interval="1m"):
    kd = klines(interval=interval, limit=120)
    closes, highs, lows, volumes, opens = kd['close'], kd['high'], kd['low'], kd['volume'], kd['open']
    if len(closes) < 50:
        return {
            'price': 0, 'ema20': 0, 'ema50': 0, 'rsi': 50,
            'macd': {'macd': 0, 'signal': 0, 'histogram': 0, 'trend': 'NEUTRAL'},
            'atr': 0, 'volume_trend': 'NORMAL', 'trend': 'UNKNOWN',
            'bollinger': {'upper': 0, 'middle': 0, 'lower': 0, 'bandwidth': 0, 'percent_b': 0.5},
            'candle_quality': 0.5
        }
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    rsi = calculate_rsi(closes, 14)
    macd = calculate_macd(closes)
    atr = calculate_atr(highs, lows, closes, 14)
    vol_trend = analyze_volume(volumes, closes)
    bb = calculate_bollinger(closes)
    cq = calculate_candle_quality(opens, highs, lows, closes)
    price = closes[-1]
    trend = 'UP' if ema20 > ema50 and price > ema20 and rsi > 50 else \
            'DOWN' if ema20 < ema50 and price < ema20 and rsi < 50 else 'SIDEWAYS'
    return {
        'price': price, 'ema20': ema20, 'ema50': ema50, 'rsi': rsi,
        'macd': macd, 'atr': atr, 'volume_trend': vol_trend,
        'trend': trend, 'bollinger': bb, 'candle_quality': cq
    }

# =====================================
# 多时间周期趋势分析
# =====================================

def multi_timeframe_analysis():
    enabled_timeframes = []
    tf_cfg = {
        "1h": ("timeframe_1h", "weight_1h", 0.4),
        "30m": ("timeframe_30m", "weight_30m", 0.3),
        "15m": ("timeframe_15m", "weight_15m", 0.15),
        "5m": ("timeframe_5m", "weight_5m", 0.1),
        "1m": ("timeframe_1m", "weight_1m", 0.05)
    }
    total_w = 0
    for interval, (ck, wk, dw) in tf_cfg.items():
        if config["trading"].get(ck, True):
            w = config["trading"].get(wk, dw)
            enabled_timeframes.append((interval, w))
            total_w += w
    if not enabled_timeframes:
        enabled_timeframes = [("15m", 0.5), ("5m", 0.3), ("1m", 0.2)]
        total_w = 1.0
    if total_w != 1.0:
        enabled_timeframes = [(i, w / total_w) for i, w in enabled_timeframes]

    data_dict = {i: indicators(interval=i) for i, _ in enabled_timeframes}

    def score(data):
        s = 0
        mt = data['macd']['trend']
        s += 30 if mt == 'BULLISH' else -30 if mt == 'BEARISH' else 0
        rsi = data['rsi']
        if rsi > 70: s += 20
        elif rsi > 60: s += 12
        elif rsi > 55: s += 6
        elif rsi < 30: s -= 20
        elif rsi < 40: s -= 12
        elif rsi < 45: s -= 6
        p, e20, e50 = data['price'], data['ema20'], data['ema50']
        if p > e20 and e20 > e50: s += 20
        elif p < e20 and e20 < e50: s -= 20
        elif p > e20: s += 8
        elif p < e20: s -= 8
        bb = data.get('bollinger', {})
        pb = bb.get('percent_b', 0.5)
        bw = bb.get('bandwidth', 0)
        if bw > 0.03:
            if pb > 0.8: s += 15
            elif pb > 0.6: s += 8
            elif pb < 0.2: s -= 15
            elif pb < 0.4: s -= 8
        vt = data.get('volume_trend', 'NORMAL')
        s += 10 if vt == 'SURGE' else 6 if vt == 'INCREASING' else -6 if vt == 'DECREASING' else 0
        return max(-100, min(100, s))

    trend_scores = {i: score(data_dict[i]) for i, _ in enabled_timeframes}
    ws = sum(trend_scores[i] * w for i, w in enabled_timeframes)
    final_trend = "UP" if ws >= 30 else "DOWN" if ws <= -30 else "SIDEWAYS"
    signs = set(1 if s > 0 else -1 if s < 0 else 0 for s in trend_scores.values())
    confidence = "HIGH" if len(signs) == 1 and abs(ws) >= 60 else \
                 "MEDIUM" if len(signs) == 1 or abs(ws) >= 40 else "LOW"

    market_data = {}
    for interval, _ in enabled_timeframes:
        d = data_dict[interval]
        bb = d.get('bollinger', {})
        market_data[interval] = {
            "trend": d['trend'], "trend_score": trend_scores[interval],
            "price": d['price'], "ema20": round(d['ema20'], 2), "ema50": round(d['ema50'], 2),
            "rsi": round(d['rsi'], 2), "macd": d['macd']['trend'],
            "macd_value": round(d['macd']['macd'], 4), "volume_trend": d['volume_trend'],
            "atr": round(d['atr'], 2),
            "bb_upper": round(bb.get('upper', 0), 2), "bb_lower": round(bb.get('lower', 0), 2),
            "bb_bandwidth": round(bb.get('bandwidth', 0), 4),
            "bb_percent_b": round(bb.get('percent_b', 0.5), 3),
            "candle_quality": round(d.get('candle_quality', 0.5), 2),
        }

    short_term_trend = data_dict.get('1m', data_dict[enabled_timeframes[-1][0]])['trend']

    logger.info(f"[MTF] 趋势：{final_trend} | 强度：{ws:+.1f}% | 置信度：{confidence}")
    for i, w in enabled_timeframes:
        d = market_data[i]
        logger.info(f"  {i} | 权重:{w*100:.0f}% | 分:{trend_scores[i]:+.0f} | RSI:{d['rsi']:.1f} | %B:{d['bb_percent_b']:.2f}")

    return final_trend, ws, confidence, market_data, short_term_trend

# ============================================
# 【v4.0 核心新增】过热保护系统
# ============================================

def detect_market_overheat(trend, market_data):
    """
    层2：市场过热检测
    
    综合评估当前市场是否处于过热状态，返回过热等级和建议动作：
    - NORMAL:  正常，可以正常入场
    - WARM:    偏热，放大入场偏移（挂更深的限价单）
    - HOT:     过热，大幅放大偏移，减少网格层数
    - BURNING: 极端过热，直接阻止建仓，等待回撤
    
    检测维度（做多为例，做空相反）：
    1. RSI 高位（最关键指标）
    2. 布林带 %B 极端值（价格是否严重偏离均值）
    3. 布林带带宽（是否进入异常扩张）
    4. 多周期 RSI 共振超买
    """
    if trend not in ["UP", "DOWN"]:
        return "NORMAL", 1.0, "趋势不明确，不涉及过热检测"

    is_long = (trend == "UP")

    # 从配置读取阈值
    rsi_warm = config["trading"].get("overheat_rsi_long", 75.0) if is_long else \
               (100 - config["trading"].get("overheat_rsi_short", 25.0))
    rsi_block = config["trading"].get("overheat_rsi_block_long", 80.0) if is_long else \
                (100 - config["trading"].get("overheat_rsi_block_short", 20.0))
    bb_warm = config["trading"].get("overheat_bb_percent_b_long", 0.88) if is_long else \
              (1 - config["trading"].get("overheat_bb_percent_b_short", 0.12))
    offset_mult = config["trading"].get("overheat_offset_multiplier", 2.5)

    # 统计过热信号数量
    heat_signals = []
    block_signals = []

    for interval, data in market_data.items():
        rsi = data.get("rsi", 50)
        pb = data.get("bb_percent_b", 0.5)
        bw = data.get("bb_bandwidth", 0)

        # RSI 过热信号
        rsi_check = rsi if is_long else (100 - rsi)
        if rsi_check >= rsi_block:
            block_signals.append(f"{interval} RSI={rsi:.1f}（极端{'超买' if is_long else '超卖'}）")
        elif rsi_check >= rsi_warm:
            heat_signals.append(f"{interval} RSI={rsi:.1f}（{'超买' if is_long else '超卖'}）")

        # 布林带 %B 过热信号
        pb_check = pb if is_long else (1 - pb)
        if pb_check >= 0.95:
            block_signals.append(f"{interval} %B={pb:.2f}（贴近上轨）")
        elif pb_check >= bb_warm:
            heat_signals.append(f"{interval} %B={pb:.2f}（偏高）")

    # 判断过热等级
    if len(block_signals) >= 1:
        level = "BURNING"
        reason = f"极端过热信号：{'; '.join(block_signals)}"
    elif len(heat_signals) >= 3:
        level = "HOT"
        reason = f"多重过热信号：{'; '.join(heat_signals[:3])}"
    elif len(heat_signals) >= 2:
        level = "WARM"
        reason = f"轻度过热：{'; '.join(heat_signals[:2])}"
    else:
        level = "NORMAL"
        reason = "市场正常"

    # 计算偏移乘数
    level_mult = {
        "NORMAL": 1.0,
        "WARM": max(1.5, offset_mult * 0.6),
        "HOT": max(2.0, offset_mult * 0.8),
        "BURNING": offset_mult
    }

    mult = level_mult.get(level, 1.0)

    if level != trading_state.get("last_overheat_level", "NORMAL"):
        logger.warning(f"[OVERHEAT] 过热等级变更：{trading_state.get('last_overheat_level')} → {level} | {reason}")
        trading_state["last_overheat_level"] = level

    return level, mult, reason


def check_win_streak_cooldown(trend):
    """
    层1：连胜冷却期检查
    
    返回：(blocked: bool, reason: str, adjusted_offset_mult: float)
    - blocked=True：禁止建仓
    - adjusted_offset_mult：如果未被完全阻止，建议的偏移乘数（>1 表示挂更深的单）
    """
    ws_count = trading_state.get("win_streak_count", 0)
    ws_dir = trading_state.get("win_streak_direction")
    ws_active = trading_state.get("win_streak_cooldown_active", False)
    ws_start = trading_state.get("win_streak_cooldown_start", 0)
    cooldown_dur = config["trading"].get("win_streak_cooldown", 120)
    threshold = config["trading"].get("win_streak_threshold", 2)

    # 检查冷却期是否结束
    if ws_active:
        elapsed = time.time() - ws_start
        if elapsed < cooldown_dur:
            remaining = cooldown_dur - elapsed
            logger.info(f"[WIN_STREAK] 连胜冷却中 | 连胜：{ws_count}笔 | 剩余：{remaining:.0f}s")
            return True, f"连胜{ws_count}笔冷却期，剩余{remaining:.0f}s", 1.0

        # 冷却期结束，检查是否需要等待价格回归
        trading_state["win_streak_cooldown_active"] = False
        logger.info(f"[WIN_STREAK] 冷却期结束 | 连胜：{ws_count}笔")

        # 检查价格回归条件
        require_pullback = config["trading"].get("win_streak_require_pullback", True)
        if require_pullback and ws_dir:
            price = get_price()
            # 使用 15m 周期均线判断是否回归
            try:
                ind_15m = indicators("15m")
                ema20 = ind_15m['ema20']
                atr = ind_15m['atr']
                pullback_mult = config["trading"].get("win_streak_pullback_pct", 0.3)
                pullback_range = atr * pullback_mult

                if ws_dir == "LONG" and trend == "UP":
                    # 价格需要回到 EMA20 + 一定范围内才允许再次做多
                    dist_from_ema = price - ema20
                    if dist_from_ema > pullback_range * 3:
                        logger.warning(
                            f"[WIN_STREAK] 连胜后等待回归均线 | "
                            f"价格={price:.2f} EMA20={ema20:.2f} 距离={dist_from_ema:.2f} > {pullback_range*3:.2f}"
                        )
                        return True, f"连胜{ws_count}笔后等待价格回归均线（距EMA20={dist_from_ema:.1f}）", 1.0
                elif ws_dir == "SHORT" and trend == "DOWN":
                    dist_from_ema = ema20 - price
                    if dist_from_ema > pullback_range * 3:
                        return True, f"连胜{ws_count}笔后等待价格回归均线（距EMA20={dist_from_ema:.1f}）", 1.0
            except Exception as e:
                logger.warning(f"[WIN_STREAK] 回归检查失败（允许入场）：{e}")

        # 回归条件满足或不需要，重置连胜计数
        trading_state["win_streak_count"] = 0
        trading_state["win_streak_direction"] = None
        logger.info(f"[WIN_STREAK] 连胜计数已重置，允许正常入场")
        return False, "连胜冷却结束，允许入场", 1.0

    # 未在冷却期：检查是否连胜方向与当前趋势一致
    direction = "LONG" if trend == "UP" else "SHORT" if trend == "DOWN" else None
    if direction and ws_dir and direction == ws_dir and ws_count >= threshold:
        # 同方向连续达到阈值，激活冷却期
        logger.warning(
            f"[WIN_STREAK] ⚠️ 触发连胜冷却 | 方向：{ws_dir} | 连胜：{ws_count}笔 | "
            f"冷却：{cooldown_dur}s | 防止追高"
        )
        trading_state["win_streak_cooldown_active"] = True
        trading_state["win_streak_cooldown_start"] = time.time()
        return True, f"同方向连胜{ws_count}笔，冷却{cooldown_dur}s", 1.0

    return False, "连胜检查通过", 1.0


def check_circuit_breaker(trend, price):
    """
    层3：累计涨幅熔断
    
    追踪最近 N 笔入场价格，若当前价相对首笔入场价累计涨幅超过阈值，
    触发熔断，禁止同向追入。
    
    返回：(blocked: bool, reason: str)
    """
    if not config["trading"].get("circuit_breaker_enabled", True):
        return False, "熔断器未启用"

    # 检查熔断冷却期是否结束
    if trading_state.get("circuit_breaker_active", False):
        cb_start = trading_state.get("circuit_breaker_start", 0)
        cb_dir = trading_state.get("circuit_breaker_direction")
        cb_cooldown = config["trading"].get("circuit_breaker_cooldown", 300)
        elapsed = time.time() - cb_start

        if elapsed < cb_cooldown:
            # 检查是否是同方向
            current_dir = "LONG" if trend == "UP" else "SHORT" if trend == "DOWN" else None
            if current_dir == cb_dir:
                remaining = cb_cooldown - elapsed
                logger.info(f"[CIRCUIT_BREAKER] 熔断中 | 方向：{cb_dir} | 剩余：{remaining:.0f}s")
                return True, f"累计涨幅熔断中（{cb_dir}），剩余{remaining:.0f}s"
        else:
            trading_state["circuit_breaker_active"] = False
            trading_state["circuit_breaker_direction"] = None
            logger.info(f"[CIRCUIT_BREAKER] 熔断结束")

    # 检查最近 N 笔入场价格
    window = config["trading"].get("circuit_breaker_window", 3)
    threshold_pct = config["trading"].get("circuit_breaker_pct", 2.5)
    recent_entries = trading_state.get("recent_entry_prices", [])

    if len(recent_entries) < window:
        return False, f"入场记录不足（{len(recent_entries)}/{window}笔）"

    # 取最近 window 笔的最早入场价
    window_entries = recent_entries[-window:]
    earliest = window_entries[0]
    earliest_price = earliest.get("price", 0)
    earliest_dir = earliest.get("direction", "")

    if earliest_price <= 0 or not earliest_dir:
        return False, "入场价格记录无效"

    # 计算累计涨幅（只在同方向时检查）
    current_dir = "LONG" if trend == "UP" else "SHORT" if trend == "DOWN" else None
    if current_dir != earliest_dir:
        return False, "方向不同，不触发熔断"

    if current_dir == "LONG":
        move_pct = (price - earliest_price) / earliest_price * 100
    else:
        move_pct = (earliest_price - price) / earliest_price * 100

    if move_pct >= threshold_pct:
        logger.critical(
            f"[CIRCUIT_BREAKER] ⚡ 触发累计涨幅熔断 | "
            f"首笔入场：{earliest_price:.2f} | 当前：{price:.2f} | "
            f"累计涨幅：{move_pct:.2f}% >= {threshold_pct}% | 方向：{current_dir}"
        )
        trading_state["circuit_breaker_active"] = True
        trading_state["circuit_breaker_start"] = time.time()
        trading_state["circuit_breaker_direction"] = current_dir
        cooldown = config["trading"].get("circuit_breaker_cooldown", 300)
        return True, f"累计涨幅{move_pct:.2f}%触发熔断，等待{cooldown}s回撤"

    # 接近阈值时给出警告
    if move_pct >= threshold_pct * 0.8:
        logger.warning(
            f"[CIRCUIT_BREAKER] 接近熔断阈值 | "
            f"累计涨幅：{move_pct:.2f}% | 阈值：{threshold_pct}%"
        )

    return False, f"累计涨幅{move_pct:.2f}% < {threshold_pct}%"


def record_entry_price(direction, price):
    """记录入场价格（用于熔断检查）"""
    window = config["trading"].get("circuit_breaker_window", 3)
    recent = trading_state.get("recent_entry_prices", [])
    recent.append({
        "direction": direction,
        "price": price,
        "time": time.time()
    })
    # 只保留最近 window*2 条记录
    if len(recent) > window * 2:
        recent = recent[-(window * 2):]
    trading_state["recent_entry_prices"] = recent
    logger.debug(f"[ENTRY_RECORD] 记录入场 | {direction} @ {price:.2f} | 共{len(recent)}条")

# =====================================
# AI 决策（加入过热信息）
# =====================================

def ai_decision():
    """AI 决策 - v4.0 版（加入市场过热状态给 AI 参考）"""
    price = get_price()
    pos, entry = get_position()
    orders = get_orders()

    final_trend, trend_strength, confidence, market_data, short_term_trend = multi_timeframe_analysis()

    unrealized_pnl = 0
    pnl_percent = 0
    if pos != 0 and entry != 0:
        unrealized_pnl = (price - entry) * pos if pos > 0 else (entry - price) * abs(pos)
        pnl_percent = (unrealized_pnl / (abs(pos) * entry)) * 100

    buy_orders = [o for o in orders if o["side"] == "BUY"]
    sell_orders = [o for o in orders if o["side"] == "SELL"]

    # 历史决策
    history_lines = []
    for i, d in enumerate(ai_context_memory["decisions"]):
        age = int(time.time() - d.get('timestamp', time.time()))
        history_lines.append(f"  [{i+1}] {age}s前 | trend:{d['trend']} | strength:{d.get('trend_strength',0):+.0f}% | action:{d.get('action','?')}")
    history_str = "\n".join(history_lines) if history_lines else "  无历史记录"

    # 过热状态（给 AI 参考）
    overheat_level, _, overheat_reason = detect_market_overheat(final_trend, market_data)

    # 连胜状态（给 AI 参考）
    ws_count = trading_state.get("win_streak_count", 0)
    ws_dir = trading_state.get("win_streak_direction", "无")

    # 熔断状态
    cb_active = trading_state.get("circuit_breaker_active", False)

    # 各周期数据
    periods_str = ""
    for interval in market_data.keys():
        d = market_data[interval]
        periods_str += (
            f"{interval}: trend={d['trend']} score={d['trend_score']:+.0f} "
            f"RSI={d['rsi']:.1f} MACD={d['macd']} "
            f"%B={d['bb_percent_b']:.2f} ATR={d['atr']:.1f} "
            f"vol={d['volume_trend']} candle_q={d['candle_quality']:.2f}\n"
        )

    system_role = """你是专业的加密货币期货量化交易 AI，负责基于多时间周期技术指标做出交易决策。

【工作原则】
1. 严格遵守 JSON 输出格式，不允许 JSON 以外的任何文字
2. 利润保护优先于利润扩大
3. 市场过热时，倾向于保守操作而非追高

【禁止行为】
- 禁止在 LOW 置信度下输出强趋势结论
- 禁止在 RSI>78 时做多；禁止在 RSI<22 时做空
- 禁止在 %B>0.95 或 %B<0.05 时顺势加仓
- 禁止在 K线质量<0.3 时输出 AGGRESSIVE 入场模式
- 过热等级为 HOT 或 BURNING 时，禁止输出 MOMENTUM 入场模式
- 熔断激活时，禁止推荐与熔断方向相同的建仓动作"""

    user_content = f"""【当前市场状态】
价格={price:.2f} | 持仓={"LONG" if pos > 0 else "SHORT" if pos < 0 else "NONE"} qty={abs(pos):.4f} entry={entry:.2f}
浮盈={unrealized_pnl:.2f}U ({pnl_percent:.2f}%) | 买单={len(buy_orders)} 卖单={len(sell_orders)}
综合趋势={final_trend} | 强度={trend_strength:+.1f}% | 置信度={confidence}

【⚠️ 市场过热状态（重要参考）】
过热等级={overheat_level} | 说明：{overheat_reason}
连续盈利={ws_count}笔（方向：{ws_dir}） | 累计涨幅熔断={"已触发" if cb_active else "未触发"}

【各周期指标】
{periods_str}
【历史决策】
{history_str}

【分析任务】
Step1: 多周期趋势一致性
Step2: 市场阶段（BREAKOUT/PULLBACK/CONSOLIDATION/REVERSAL）
Step3: 过热风险评估（结合过热等级、连胜次数）
Step4: 最终策略（过热时应保守，HOT/BURNING 时优先等待回撤）

只输出以下 JSON：
{{
  "step1": "一致性结论（15字内）",
  "step2": "BREAKOUT|PULLBACK|CONSOLIDATION|REVERSAL",
  "step3": "过热风险说明（20字内）",
  "step4": "策略（25字内）",
  "trend": "UP|DOWN|SIDEWAYS",
  "trend_strength": "WEAK|MEDIUM|STRONG",
  "confidence": "HIGH|MEDIUM|LOW",
  "market_state": "TREND|RANGE|REVERSAL",
  "entry_mode": "MOMENTUM|PULLBACK|WAIT",
  "entry_offset": {config["trading"].get("entry_offset", 50)},
  "grid_spacing": {config["trading"].get("grid_spacing", 80)},
  "tp_distance": {config["trading"].get("tp_distance", 130)},
  "sl_distance": 200,
  "action": "HOLD|ADD|REDUCE|CLOSE_ALL",
  "reason": "理由（25字内）"
}}"""

    payload = {
        "model": config["ai"]["model"],
        "messages": [
            {"role": "system", "content": system_role},
            {"role": "user", "content": user_content}
        ],
        "temperature": config["ai"].get("temperature", 0.2),
        "max_tokens": config["ai"].get("max_tokens", 700)
    }

    try:
        start_time = time.time()
        r = requests.post(config["ai"]["lmstudio_url"], json=payload, timeout=config["ai"]["timeout"])
        elapsed = time.time() - start_time
        min_wait = config["ai"].get("min_wait_time", 2)
        if elapsed < min_wait:
            time.sleep(min_wait - elapsed)

        text = r.json()["choices"][0]["message"]["content"].strip()
        if "<thinking>" in text:
            text = text.split("</thinking>")[-1].strip()

        json_str = text
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            json_str = text.split("```")[1].split("```")[0].strip()
        else:
            si = text.find("{"); ei = text.rfind("}")
            if si != -1 and ei != -1:
                json_str = text[si:ei+1]

        data = json.loads(json_str)

        # 标准化
        sm = {"WEAK": 30, "MEDIUM": 55, "STRONG": 80}
        raw = data.get("trend_strength", trend_strength)
        if isinstance(raw, str) and raw in sm:
            ai_s = sm[raw] * (1 if final_trend == "UP" else -1 if final_trend == "DOWN" else 0)
        else:
            try:
                ai_s = float(raw)
            except (ValueError, TypeError):
                ai_s = float(trend_strength)

        data["trend_strength"] = ai_s
        data["strength"] = abs(ai_s)
        data["confidence"] = confidence
        data["short_term_trend"] = short_term_trend
        # 传递过热状态给委托管理模块
        data["overheat_level"] = overheat_level

        for field in ["entry_offset", "grid_spacing", "tp_distance", "sl_distance"]:
            try:
                data[field] = float(data.get(field, 100))
            except (ValueError, TypeError):
                data[field] = 100.0

        ai_context_memory["decisions"].append({
            "trend": data.get("trend", final_trend),
            "trend_strength": ai_s,
            "action": data.get("action", "HOLD"),
            "timestamp": time.time()
        })
        if len(ai_context_memory["decisions"]) > ai_context_memory["max_history"]:
            ai_context_memory["decisions"].pop(0)

        logger.info(f"[AI] 阶段:{data.get('step2','?')} | 趋势:{data.get('trend')} | 强度:{data.get('strength',0):.0f}% | 过热:{overheat_level} | 动作:{data.get('action')}")
        return data

    except requests.exceptions.Timeout:
        logger.warning("[AI] 请求超时")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"[AI] JSON 解析失败：{e}")
        return None
    except Exception as e:
        logger.error(f"[AI] 错误：{e}")
        return None

# =====================================
# 其余函数（与 v3 相同）
# =====================================

FEE_RATE = config["trading"]["fee_rate"]


def calculate_tp_price(pos, entry, tp_distance, level=0, spacing=None):
    qty = abs(pos)
    tp_spacing = config["trading"].get("tp_spacing", 100) if spacing is None else spacing
    fee_per_qty = entry * FEE_RATE if qty > 0 else 0
    tp_min = config["trading"].get("tp_min_profit", 0.5)
    if pos > 0:
        tp_price = entry + tp_distance + level * tp_spacing
        min_p = entry + (tp_min + fee_per_qty) / qty if qty > 0 else entry
    else:
        tp_price = entry - tp_distance - level * tp_spacing
        min_p = entry - (tp_min + fee_per_qty) / qty if qty > 0 else entry
    return tp_price, min_p, fee_per_qty


def create_tp(pos, entry, tp_distance):
    qty = abs(pos)
    side = "SELL" if pos > 0 else "BUY"
    pp, qp = get_precision()
    position_side = "LONG" if pos > 0 else "SHORT"
    logger.info(f"[TP_CREATE] {position_side} | qty:{qty:.4f} | entry:{entry:.2f}")
    if qty < MIN_POSITION_QTY:
        close_position_market(reason="TP_SMALL")
        trading_state["position_open_time"] = None
        return
    if qty < 0.01:
        tp_d = config["trading"].get("tp_distance", 130)
        tp_price, _, _ = calculate_tp_price(pos, entry, tp_d)
        limit_order(side, tp_price, qty)
        trading_state["last_tp_distance"] = tp_d
        trading_state["last_tp_update_time"] = time.time()
        return
    tp_s = config["trading"].get("tp_spacing", 100)
    plans = [{"level": 0, "ratio": 0.5}, {"level": 1, "ratio": 0.3}, {"level": 2, "ratio": 0.2}]
    for i, p in enumerate(plans):
        close_qty = qty * p["ratio"] if i < len(plans)-1 else qty - sum(qty*q["ratio"] for q in plans[:i])
        if close_qty < 0.001:
            continue
        tp_price, _, _ = calculate_tp_price(pos, entry, tp_distance, level=p["level"], spacing=tp_s)
        limit_order(side, tp_price, close_qty)
    trading_state["last_tp_distance"] = tp_distance
    trading_state["last_tp_update_time"] = time.time()


def batch_close(pos, entry, tp_distance=None):
    qty = abs(pos)
    if qty <= 0:
        return
    cancel_all_orders(reason="BATCH_CLOSE_PRE")
    time.sleep(0.3)
    price = get_price()
    side = "SELL" if pos > 0 else "BUY"
    off = config["trading"].get("batch_close_offset", 10)
    if qty < MIN_POSITION_QTY * 2:
        cp = price - off if side == "SELL" else price + off
        limit_order(side, cp, qty)
        _activate_post_close_cooldown("BATCH_CLOSE")
        return
    remaining = qty
    for i, ratio in enumerate([0.5, 0.3, 0.2]):
        cq = qty * ratio if i < 2 else remaining
        cp = price - off*(i+1) if side == "SELL" else price + off*(i+1)
        limit_order(side, cp, cq)
        remaining -= cq
        if remaining <= 0.001:
            break
    _activate_post_close_cooldown("BATCH_CLOSE")


def analyze_order_trend(orders, price):
    if not orders:
        return None, float('inf'), 'UNKNOWN'
    closest = min(orders, key=lambda o: abs(float(o["price"]) - price))
    min_dist = abs(float(closest["price"]) - price)
    ct = time.time()
    order_tracking['price_history'].append((ct, price, min_dist))
    order_tracking['price_history'] = [(t, p, d) for t, p, d in order_tracking['price_history'] if t > ct - 300]
    hist = order_tracking['price_history']
    trend = 'UNKNOWN'
    if len(hist) >= 2:
        rd = [d for _, _, d in hist[-10:]]
        if len(rd) >= 2:
            avg_r = sum(rd[-3:]) / min(3, len(rd))
            avg_o = sum(rd[:3]) / min(3, len(rd))
            if avg_r < avg_o * 0.95:
                trend = 'APPROACHING'
            elif avg_r > avg_o * 1.05:
                trend = 'DEPARTING'
            else:
                trend = 'STABLE'
    return closest, min_dist, trend


def resolve_tp_distance_threshold(tp_distance):
    cfg = config["trading"].get("tp_adjust_threshold", 0.2)
    if isinstance(cfg, (int, float)) and 0 < cfg < 1:
        return max(1.0, abs(tp_distance) * cfg)
    return cfg if isinstance(cfg, (int, float)) else tp_distance * 0.2


def adjust_orders_to_nearest(orders, price, trend, oldest_order_age=0, distance_threshold=None):
    adjusted = 0
    entry_interval = config["trading"].get("entry_adjust_interval", 300)
    thr = distance_threshold if distance_threshold is not None else entry_interval
    should = trend == 'DEPARTING' and oldest_order_age > entry_interval * 0.5 or oldest_order_age >= entry_interval
    if should:
        pp, qp = get_precision()
        for order in orders:
            try:
                op = float(order["price"])
                oq = float(order["origQty"]) - float(order.get("executedQty", 0))
                os = order["side"]
                if abs(op - price) <= thr:
                    continue
                np = price - thr if os == "BUY" else price + thr
                np = round(np, pp)
                if abs(np - op) < 1:
                    continue
                cancel_order(order["orderId"])
                time.sleep(0.15)
                if limit_order(os, np, oq):
                    adjusted += 1
            except Exception as e:
                logger.error(f"[ADJUST] {e}")
    return adjusted


def clear_same_direction_orders(pos, orders):
    if pos == 0:
        return 0
    target = "BUY" if pos > 0 else "SELL"
    same = [o for o in orders if o["side"] == target]
    cnt = sum(1 for o in same if cancel_order(o["orderId"]) and not time.sleep(0.2))
    if cnt:
        logger.warning(f"[RISK] 清除同向{target}委托：{cnt}")
    return cnt


def analyze_distance_trend():
    hist = trading_state["price_distance_history"]
    ms = config["trading"].get("min_samples_for_trend", 3)
    if len(hist) < ms:
        return 'UNKNOWN'
    recent = hist[-5:]
    if len(recent) < ms:
        return 'UNKNOWN'
    dists = [d for _, d, _ in recent]
    fa = sum(dists[:2]) / 2
    la = sum(dists[-2:]) / 2
    if fa == 0:
        return 'UNKNOWN'
    cr = (la - fa) / fa
    st = config["trading"].get("distance_shrink_threshold", 0.3)
    et = config["trading"].get("distance_expand_threshold", 0.5)
    return 'APPROACHING' if cr < -st else 'DEPARTING' if cr > et else 'STABLE'


def check_position_timeout(pos, entry, price):
    TIMEOUT = config["trading"].get("position_timeout_force", 3600)
    if pos == 0 or not trading_state.get("position_open_time"):
        return False
    age = time.time() - trading_state["position_open_time"]
    if age >= TIMEOUT:
        pnl = (price - entry) * pos if pos > 0 else (entry - price) * abs(pos)
        logger.error(f"[TIMEOUT] 持仓超时 | 盈亏：{pnl:.2f}U")
        close_position_market(reason="TIMEOUT")
        trading_state["position_open_time"] = None
        return True
    return False


def smart_loss_management(price, pos, entry, orders, ai):
    qty = abs(pos)
    pnl = (price - entry) * qty if pos > 0 else (entry - price) * qty
    pct = (pnl / (qty * entry)) * 100 if qty > 0 and entry > 0 else 0
    if pct > -0.5:
        return False
    trend = ai.get("trend", "SIDEWAYS")
    opposite = (pos > 0 and trend == "DOWN") or (pos < 0 and trend == "UP")
    if pct < -2.0:
        action = "CLOSE_ALL"
    elif pct < -1.0:
        action = "CLOSE_ALL" if (opposite or ai.get("confidence") == "HIGH") else "REDUCE_80"
    else:
        action = "REDUCE_50" if opposite else "HOLD"
    if action == "HOLD":
        return False
    logger.warning(f"[LOSS] 亏损处置 | {pct:.2f}% | 动作:{action}")
    if qty < MIN_POSITION_QTY:
        close_position_market(reason="LOSS_SMALL")
        return True
    ratio = 1.0 if "CLOSE" in action else 0.8 if "80" in action else 0.5
    rq = qty * ratio
    if rq * price < config["trading"]["min_order_value"]:
        rq = qty
    side = "SELL" if pos > 0 else "BUY"
    cp = price - 20 if side == "SELL" else price + 20
    limit_order(side, cp, round(rq, 3))
    return True

# =====================================
# 交易统计 - v4.0 版（含连胜更新）
# =====================================

def update_trade_statistics(pos, entry, price):
    last_pos = trading_state["last_position"]
    last_entry = trading_state["last_entry_price"]
    current_time = time.time()

    # 平仓
    if last_pos != 0 and pos == 0:
        profit = (price - last_entry) * last_pos if last_pos > 0 else (last_entry - price) * abs(last_pos)
        dur = current_time - (trading_state.get("position_open_time") or current_time)

        if trading_state["current_trade"]:
            trading_state["current_trade"].update({
                "close_time": current_time, "close_price": price,
                "profit": profit, "duration": dur, "status": "CLOSED"
            })
            trading_state["trade_history"].append(trading_state["current_trade"])
            trading_state["current_trade"] = None

        # 清理剩余委托
        orders = get_orders()
        if orders:
            logger.warning(f"[TRADE_CYCLE] 平仓后清除剩余委托 {len(orders)} 个")
            cancel_all_orders(orders, reason="POST_CLOSE_CLEANUP")
            time.sleep(0.5)

        trading_state["total_trades"] += 1
        trading_state["total_profit"] += profit
        trading_state["closed_positions_profit"] += profit
        trading_state["volume_24h"] += abs(last_pos) * price
        trading_state["position_open_time"] = None
        trading_state["failed_close_attempts"] = 0

        # ============================================
        # v4.0：更新连胜计数
        # ============================================
        direction = "LONG" if last_pos > 0 else "SHORT"

        if profit > 0:
            trading_state["win_count"] += 1
            prev_dir = trading_state.get("win_streak_direction")
            prev_count = trading_state.get("win_streak_count", 0)

            if prev_dir == direction:
                # 同方向继续盈利，累加
                trading_state["win_streak_count"] = prev_count + 1
            else:
                # 方向切换或第一笔，重新开始计数
                trading_state["win_streak_count"] = 1
                trading_state["win_streak_direction"] = direction

            trading_state["win_streak_last_entry_price"] = last_entry
            wsc = trading_state["win_streak_count"]
            wsd = trading_state["win_streak_direction"]
            threshold = config["trading"].get("win_streak_threshold", 2)
            logger.info(
                f"[WIN_STREAK] 盈利 | 方向:{direction} | 利润:{profit:.2f}U | "
                f"连胜:{wsc}笔 | 阈值:{threshold}"
            )
            if wsc >= threshold:
                logger.warning(
                    f"[WIN_STREAK] ⚠️ 连胜达到阈值 {wsc}/{threshold}，下次同向入场将触发冷却"
                )
        else:
            trading_state["loss_count"] += 1
            # 亏损则重置连胜计数
            old_count = trading_state.get("win_streak_count", 0)
            if old_count > 0:
                logger.info(f"[WIN_STREAK] 亏损，重置连胜计数（{old_count} → 0）")
            trading_state["win_streak_count"] = 0
            trading_state["win_streak_direction"] = None
            trading_state["win_streak_cooldown_active"] = False  # 亏损后取消冷却期

        ps = "LONG" if last_pos > 0 else "SHORT"
        logger.info(f"[TRADE_CYCLE] 结束 | {ps} | 利润:{profit:.2f}U | 时长:{dur:.0f}s")

    # 开仓
    elif last_pos == 0 and pos != 0:
        trading_state["position_open_time"] = current_time
        ps = "LONG" if pos > 0 else "SHORT"

        # 记录入场价（用于熔断检查）
        record_entry_price(ps, entry)

        trading_state["current_trade"] = {
            "trade_id": trading_state["total_trades"] + 1,
            "open_time": current_time, "open_price": entry,
            "side": ps, "quantity": abs(pos),
            "close_time": None, "close_price": None,
            "profit": None, "duration": None, "status": "OPEN"
        }
        logger.info(f"[TRADE_CYCLE] 开仓 | {ps} @ {entry:.2f} | qty={abs(pos):.4f}")

    trading_state["last_position"] = pos
    trading_state["last_entry_price"] = entry

# =====================================
# 入场质量检查（含过热保护）
# =====================================

def check_entry_quality(trend, market_data, ai):
    """入场质量检验 - v4.0 整合三层过热保护"""
    if trend not in ["UP", "DOWN"]:
        return False, "趋势不明确"

    strength = ai.get("strength", 0)
    min_str = config["trading"].get("entry_confirm_min_strength", 35)
    if strength < min_str:
        return False, f"趋势强度不足（{strength:.1f}% < {min_str}%）"

    if ai.get("confidence", "LOW") == "LOW":
        return False, "AI 置信度过低"

    # K线质量（只在有数据时检查）
    for interval, data in market_data.items():
        if interval in ["5m", "1m", "15m"] and isinstance(data, dict):
            cq = data.get("candle_quality", 1.0)
            mq = config["trading"].get("entry_confirm_candle_body", 0.3)
            if cq < mq:
                return False, f"{interval} K线质量差（{cq:.2f} < {mq}）"
            break

    # RSI 极端
    for interval, data in market_data.items():
        if interval in ["1h", "30m"] and isinstance(data, dict):
            rsi = data.get("rsi", 50)
            if trend == "UP" and rsi > config["trading"].get("overheat_rsi_block_long", 80):
                return False, f"{interval} RSI={rsi:.1f} 严重超买"
            if trend == "DOWN" and rsi < config["trading"].get("overheat_rsi_block_short", 20):
                return False, f"{interval} RSI={rsi:.1f} 严重超卖"

    # 布林带极端
    for interval, data in market_data.items():
        if interval in ["1h", "30m"] and isinstance(data, dict):
            pb = data.get("bb_percent_b", 0.5)
            if trend == "UP" and pb > 0.95:
                return False, f"{interval} %B={pb:.2f} 极端超买"
            if trend == "DOWN" and pb < 0.05:
                return False, f"{interval} %B={pb:.2f} 极端超卖"

    return True, "通过入场质量检验"

# =====================================
# 智能委托管理核心（v4.0）
# =====================================

def smart_order_management(price, pos, entry, orders, ai):
    """
    智能委托管理 v4.0
    
    新增优先级（在 v3 基础上）：
    第 0 优先级（最高）：三层过热保护检查
    """
    if not trading_state["running"]:
        return

    trend = ai.get("trend", "SIDEWAYS")
    offset = ai.get("entry_offset", config["trading"].get("entry_offset", 50))
    spacing = ai.get("grid_spacing", config["trading"].get("grid_spacing", 80))
    tp_distance = ai.get("tp_distance", config["trading"].get("tp_distance", 130))
    current_time = time.time()
    tp_adjusted = False

    oldest_order_age = 0
    if orders:
        for order in orders:
            ot = float(order.get("updateTime", current_time * 1000)) / 1000
            oldest_order_age = max(oldest_order_age, current_time - ot)

    logger.info(f"[SMART] 价格:{price:.2f} | 持仓:{pos:.4f} | 委托:{len(orders)} | 趋势:{trend}")

    # ===== 优先级1：趋势反转保护 =====
    if pos != 0 and orders:
        ps = "LONG" if pos > 0 else "SHORT"
        cts = "LONG" if trend == "UP" else "SHORT" if trend == "DOWN" else None
        if cts and ps != cts:
            logger.critical(f"[RISK] 趋势反转！持仓={ps} vs 趋势={cts}")
            clear_same_direction_orders(pos, orders)
            orders = get_orders()

    # ===== 优先级2：委托调整 =====
    entry_interval = config["trading"].get("entry_adjust_interval", 300)
    if orders and oldest_order_age >= entry_interval:
        skip_adj = False
        if pos != 0:
            upnl = (price - entry) * pos if pos > 0 else (entry - price) * abs(pos)
            ps = "LONG" if pos > 0 else "SHORT"
            if upnl > 0:
                same = [o for o in orders if
                        (ps == "LONG" and o["side"] == "BUY") or
                        (ps == "SHORT" and o["side"] == "SELL")]
                if same:
                    opp = [o for o in orders if o not in same]
                    if opp:
                        oa = max(current_time - float(o.get("updateTime", current_time * 1000)) / 1000 for o in opp)
                        _, _, ot = analyze_order_trend(opp, price)
                        adjust_orders_to_nearest(opp, price, ot, oa, resolve_tp_distance_threshold(tp_distance))
                        tp_adjusted = True
                    skip_adj = True
                    orders = get_orders()
        if not skip_adj:
            co, _, ot = analyze_order_trend(orders, price)
            to_adj = [co] if pos == 0 and co else orders
            adjust_orders_to_nearest(to_adj, price, ot, oldest_order_age)
            orders = get_orders()

    # ===== 无持仓模式 =====
    if pos == 0:
        trading_state["position_open_time"] = None
        trading_state["failed_close_attempts"] = 0
        trading_state["last_tp_distance"] = None
        trading_state["last_tp_update_time"] = 0
        trading_state["max_favorable_pnl_pct"] = 0.0
        trading_state["adverse_trend_count"] = 0

        # 检查平仓冷静期
        if is_in_post_close_cooldown():
            return

        if trading_state["no_position_start_time"] is None:
            trading_state["no_position_start_time"] = time.time()

        no_pos_dur = time.time() - trading_state["no_position_start_time"]
        FORCE_TIMEOUT = config["trading"].get("force_entry_timeout", 600)

        if len(orders) == 0:
            if trend in ["UP", "DOWN"]:

                # =====================================================
                # 【v4.0 核心】三层过热保护检查
                # =====================================================

                # 层1：连胜冷却期
                ws_blocked, ws_reason, _ = check_win_streak_cooldown(trend)
                if ws_blocked:
                    logger.warning(f"[OVERHEAT_GUARD] 🔒 层1阻止建仓：{ws_reason}")
                    return

                # 层3：累计涨幅熔断（在连胜冷却之后检查）
                cb_blocked, cb_reason = check_circuit_breaker(trend, price)
                if cb_blocked:
                    logger.warning(f"[OVERHEAT_GUARD] ⚡ 层3阻止建仓：{cb_reason}")
                    return

                # 层2：市场过热检测（决定是否阻止或调整偏移）
                overheat_level = ai.get("overheat_level", "NORMAL")
                block_entry = config["trading"].get("overheat_block_entry", True)
                if overheat_level == "BURNING" and block_entry:
                    logger.warning(f"[OVERHEAT_GUARD] 🔥 层2阻止建仓：市场极端过热（BURNING），等待回撤")
                    return

                # 计算最终入场偏移（过热时加大偏移，挂更深的限价单）
                overheat_mult_map = {
                    "NORMAL": 1.0,
                    "WARM": config["trading"].get("overheat_offset_multiplier", 2.5) * 0.6,
                    "HOT": config["trading"].get("overheat_offset_multiplier", 2.5) * 0.8,
                    "BURNING": config["trading"].get("overheat_offset_multiplier", 2.5)
                }
                oh_mult = overheat_mult_map.get(overheat_level, 1.0)

                actual_offset = config["trading"].get("entry_offset", 50) * oh_mult
                actual_spacing = config["trading"].get("grid_spacing", 80)

                if overheat_level != "NORMAL":
                    logger.warning(
                        f"[OVERHEAT_GUARD] 层2调整偏移 | 过热:{overheat_level} | "
                        f"偏移:{config['trading'].get('entry_offset', 50):.0f} × {oh_mult:.1f} = {actual_offset:.0f}"
                    )

                trading_state["no_position_start_time"] = None
                logger.info(f"[SMART] 创建{trend}委托 | offset={actual_offset:.0f} | spacing={actual_spacing}")

                if trend == "UP":
                    for i in range(config["trading"]["grid_levels"]):
                        limit_order("BUY", price - actual_offset - actual_spacing * i, config["trading"]["base_qty"])
                else:
                    for i in range(config["trading"]["grid_levels"]):
                        limit_order("SELL", price + actual_offset + actual_spacing * i, config["trading"]["base_qty"])

            elif no_pos_dur >= FORCE_TIMEOUT:
                # 强制入场时也要检查过热（过热时不强制追入）
                overheat_level = ai.get("overheat_level", "NORMAL")
                if overheat_level in ["HOT", "BURNING"]:
                    logger.warning(f"[OVERHEAT_GUARD] 强制入场被过热拦截 | 等待市场冷却")
                    return

                logger.warning(f"[SMART] 强制入场 | {no_pos_dur:.0f}s")
                trading_state["no_position_start_time"] = None
                ft = ai.get('short_term_trend', trend)
                if ft not in ["UP", "DOWN"]:
                    ft = "UP"
                fo = offset * config["trading"].get("force_entry_offset_ratio", 0.5)
                fs = spacing * config["trading"].get("force_entry_spacing_ratio", 0.8)
                if ft == "UP":
                    for i in range(config["trading"]["grid_levels"]):
                        limit_order("BUY", price - fo - fs * i, config["trading"]["base_qty"])
                else:
                    for i in range(config["trading"]["grid_levels"]):
                        limit_order("SELL", price + fo + fs * i, config["trading"]["base_qty"])
            else:
                rem = FORCE_TIMEOUT - no_pos_dur
                logger.info(f"[SMART] 等待中 | 距强制入场：{rem:.0f}s")

        else:
            # 有委托无持仓：检查方向
            desired = "BUY" if trend == "UP" else "SELL" if trend == "DOWN" else None
            if desired:
                osides = {str(o.get("side", "")).upper() for o in orders}
                str_ = ai.get("strength", 0)
                rth = config["trading"].get("order_realign_strength_threshold", 50)
                rcd = config["trading"].get("order_realign_cooldown", 60)
                lr = trading_state.get("last_order_realign_time", 0)
                if bool(osides) and (len(osides) > 1 or desired not in osides):
                    if str_ >= rth and (time.time() - lr) >= rcd:
                        cancel_all_orders(orders, reason="ENTRY_REALIGN")
                        trading_state["last_order_realign_time"] = time.time()
                        trading_state["no_position_start_time"] = None

                        # 重建时也检查过热
                        oh_level = ai.get("overheat_level", "NORMAL")
                        oh_mult = 1.0 if oh_level == "NORMAL" else \
                                  config["trading"].get("overheat_offset_multiplier", 2.5) * 0.6 if oh_level == "WARM" else \
                                  config["trading"].get("overheat_offset_multiplier", 2.5) * 0.8 if oh_level == "HOT" else \
                                  config["trading"].get("overheat_offset_multiplier", 2.5)
                        ao = config["trading"].get("entry_offset", 50) * oh_mult
                        as_ = config["trading"].get("grid_spacing", 80)
                        for i in range(config["trading"]["grid_levels"]):
                            p = price - ao - as_ * i if desired == "BUY" else price + ao + as_ * i
                            limit_order(desired, p, config["trading"]["base_qty"])
                        orders = get_orders()
            trading_state["no_position_start_time"] = None

    # ===== 有持仓模式 =====
    else:
        ps = "LONG" if pos > 0 else "SHORT"
        opp_side = "SELL" if pos > 0 else "BUY"
        qty = abs(pos)
        tp_orders = [o for o in orders if o["side"] == opp_side]

        upnl = (price - entry) * qty if pos > 0 else (entry - price) * qty
        pct = (upnl / (qty * entry)) * 100 if qty > 0 and entry > 0 else 0

        if pct > trading_state.get("max_favorable_pnl_pct", 0.0):
            trading_state["max_favorable_pnl_pct"] = pct

        ms = ai.get("market_state", "UNKNOWN")
        ai_action = ai.get("action", "HOLD")
        strength = ai.get("strength", 0)
        cts = "LONG" if trend == "UP" else "SHORT" if trend == "DOWN" else None
        trend_opp = cts is not None and ps != cts

        # 利润锁定
        pl_min = config["trading"].get("profit_lock_min_pnl_pct", 0.3)
        pl_conf = config["trading"].get("profit_lock_confirmations", 2)
        pl_cd = config["trading"].get("profit_lock_cooldown", 60)
        adv = trend_opp or ms == "REVERSAL" or ai_action in ["REDUCE", "CLOSE_ALL"]
        if pct >= pl_min and adv:
            trading_state["adverse_trend_count"] = trading_state.get("adverse_trend_count", 0) + 1
        else:
            trading_state["adverse_trend_count"] = 0

        if (pct >= pl_min and
                trading_state.get("adverse_trend_count", 0) >= pl_conf and
                (current_time - trading_state.get("last_profit_lock_time", 0)) >= pl_cd):
            logger.warning(f"[PROFIT_LOCK] 锁定利润 | {pct:.2f}%")
            close_position_market(reason="PROFIT_LOCK")
            trading_state["position_open_time"] = None
            trading_state["failed_close_attempts"] = 0
            trading_state["last_tp_distance"] = None
            trading_state["last_tp_update_time"] = 0
            trading_state["max_favorable_pnl_pct"] = 0.0
            trading_state["adverse_trend_count"] = 0
            trading_state["last_profit_lock_time"] = current_time
            return

        # 止盈一致性
        def rq(o):
            try:
                return max(0.0, float(o.get("origQty", 0)) - float(o.get("executedQty", 0)))
            except Exception:
                return 0.0

        tp_qty = sum(rq(o) for o in tp_orders)
        tp_tol = config["trading"].get("tp_qty_mismatch_tolerance", 0.0005)
        tp_rcd = config["trading"].get("tp_rebuild_cooldown", 60)
        need_rb = False
        rb_reason = ""

        if tp_orders and abs(tp_qty - qty) > tp_tol:
            need_rb = True
            rb_reason = f"数量不匹配"

        last_tp = trading_state.get("last_tp_distance")
        if last_tp is None and tp_orders and entry > 0:
            try:
                est = min(float(o.get("price", 0)) - entry if pos > 0 else entry - float(o.get("price", 0))
                          for o in tp_orders)
                if est > 0:
                    trading_state["last_tp_distance"] = float(est)
                    last_tp = float(est)
            except Exception:
                pass

        if last_tp is not None and abs(tp_distance - last_tp) >= config["trading"].get("tp_distance_update_threshold", 20):
            need_rb = True
            rb_reason = f"tp_distance变化"

        if need_rb and (current_time - trading_state.get("last_tp_update_time", 0)) >= tp_rcd:
            logger.info(f"[TP_SYNC] 重建止盈 | {rb_reason}")
            for o in tp_orders:
                oid = o.get("orderId")
                if oid:
                    cancel_order(oid)
                    time.sleep(0.2)
            create_tp(pos, entry, tp_distance)
            orders = get_orders()
            tp_orders = [o for o in orders if o["side"] == opp_side]

        check_position_timeout(pos, entry, price)

        # 记录距离历史
        co, md, ot = analyze_order_trend(orders, price)
        trading_state["price_distance_history"].append((current_time, md, price))
        trading_state["price_distance_history"] = [
            (t, d, p) for t, d, p in trading_state["price_distance_history"]
            if t > current_time - 1800
        ]

        if len(tp_orders) == 0:
            create_tp(pos, entry, tp_distance)
        else:
            if qty < MIN_POSITION_QTY:
                cancel_all_orders(tp_orders, reason="SMALL_POSITION")
                close_position_market(reason="SMART_TP_SMALL")
                trading_state["position_open_time"] = None
                return

            tp_interval = config["trading"].get("tp_adjust_interval", 600)
            try:
                if ai_action in ["REDUCE", "CLOSE_ALL"] or ms in ["RANGE", "REVERSAL"] or strength < config["trading"]["ai_strength_medium"]:
                    tp_interval = max(30, int(tp_interval * 0.5))
                elif ms == "TREND" and strength >= config["trading"]["ai_strength_high"]:
                    tp_interval = int(tp_interval * 2)
            except Exception:
                pass

            oldest_tp = max((current_time - float(o.get("updateTime", current_time * 1000)) / 1000)
                           for o in tp_orders) if tp_orders else 0

            if tp_orders and oldest_tp >= tp_interval and not tp_adjusted:
                _, _, tp_ot = analyze_order_trend(tp_orders, price)
                adjust_orders_to_nearest(tp_orders, price, tp_ot, oldest_tp,
                                         resolve_tp_distance_threshold(tp_distance))

        smart_loss_management(price, pos, entry, orders, ai)

# =====================================
# 交易引擎主循环
# =====================================

def trading_loop():
    logger.info("=" * 80)
    logger.info("AI 智能委托网格量化交易系统 v4.0 启动")
    logger.info("三层过热保护：连胜冷却 + 市场过热检测 + 累计涨幅熔断")
    logger.info("=" * 80)

    while trading_state["running"]:
        try:
            price = get_price()
            pos, entry = get_position()
            orders = get_orders()

            trading_state["last_update"] = datetime.now().isoformat()
            trading_state["position"] = {"amount": pos, "entry_price": entry}
            trading_state["orders"] = orders

            logger.info(f"[DATA] 价格:{price:.2f} | 持仓:{pos:.4f} | 委托:{len(orders)} | 连胜:{trading_state.get('win_streak_count',0)}")

            update_trade_statistics(pos, entry, price)
            ai = ai_decision()

            if not ai:
                logger.warning("[AI] 失败，等待下一轮")
                time.sleep(config["trading"].get("ai_polling_interval", 5))
                continue

            if not trading_state["running"]:
                break

            smart_order_management(price, pos, entry, orders, ai)
            time.sleep(config["trading"].get("ai_polling_interval", 5))

        except Exception as e:
            logger.error(f"[ERROR] {e}", exc_info=True)
            time.sleep(config["trading"].get("ai_polling_interval", 5))

    logger.info("交易引擎已停止")

# =====================================
# Web API（含新增状态接口）
# =====================================

def login_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({"error": "请先登录", "code": 401}), 401
        return f(*args, **kwargs)
    return dec


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/settings')
def settings_page():
    return send_from_directory('static', 'settings.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    if data.get('username') == 'admin' and check_password_hash(config["system"]["admin_password"], data.get('password', '')):
        session['logged_in'] = True
        return jsonify({"success": True, "username": "admin"})
    return jsonify({"success": False, "message": "用户名或密码错误"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.route('/api/dashboard', methods=['GET'])
@login_required
def dashboard():
    balance = get_account_balance()
    pos, entry = get_position()
    orders = get_orders()
    price = get_price()
    upnl = (price - entry) * pos if pos > 0 else (entry - price) * abs(pos) if pos < 0 else 0
    wr = trading_state["win_count"] / max(1, trading_state["win_count"] + trading_state["loss_count"]) * 100
    ct = time.time()
    pa = int(ct - trading_state["position_open_time"]) if trading_state.get("position_open_time") else 0
    npd = int(ct - trading_state["no_position_start_time"]) if trading_state.get("no_position_start_time") else 0
    fe = config["trading"].get("force_entry_timeout", 600)

    # v4.0 新增过热状态
    ws_count = trading_state.get("win_streak_count", 0)
    ws_cd_active = trading_state.get("win_streak_cooldown_active", False)
    ws_cd_rem = max(0, config["trading"].get("win_streak_cooldown", 120) - (ct - trading_state.get("win_streak_cooldown_start", 0))) if ws_cd_active else 0
    cb_active = trading_state.get("circuit_breaker_active", False)
    cb_rem = max(0, config["trading"].get("circuit_breaker_cooldown", 300) - (ct - trading_state.get("circuit_breaker_start", 0))) if cb_active else 0

    return jsonify({
        "total_profit": round(trading_state["total_profit"], 2),
        "total_amount": round(balance, 2),
        "win_rate": round(wr, 2),
        "volume_24h": round(trading_state["volume_24h"], 2),
        "status": {
            "position": {"amount": pos, "entry_price": entry, "unrealized_pnl": round(upnl, 2)},
            "orders": len(orders),
            "running": trading_state["running"],
            "last_update": trading_state["last_update"]
        },
        "time_status": {
            "position_age": pa,
            "no_position_duration": npd,
            "force_entry_remaining": max(0, fe - npd),
            "post_close_active": trading_state.get("post_close_active", False),
        },
        "overheat_status": {
            "win_streak_count": ws_count,
            "win_streak_direction": trading_state.get("win_streak_direction"),
            "win_streak_cooldown_active": ws_cd_active,
            "win_streak_cooldown_remaining": round(ws_cd_rem, 0),
            "overheat_level": trading_state.get("last_overheat_level", "NORMAL"),
            "circuit_breaker_active": cb_active,
            "circuit_breaker_remaining": round(cb_rem, 0),
            "circuit_breaker_direction": trading_state.get("circuit_breaker_direction"),
        },
        "recent_trades": trading_state["trade_history"][-5:],
        "total_trades": trading_state["total_trades"]
    })


@app.route('/api/trades', methods=['GET'])
@login_required
def get_trade_history():
    limit = request.args.get('limit', 50, type=int)
    return jsonify({"trades": trading_state["trade_history"][-limit:], "total": len(trading_state["trade_history"])})

@app.route('/api/status', methods=['GET'])
@login_required
def status():
    pos, entry = get_position()
    return jsonify({
        "running": trading_state["running"],
        "mode": config["system"]["mode"],
        "symbol": SYMBOL,
        "position": {"amount": pos, "entry_price": entry, "side": "LONG" if pos > 0 else "SHORT" if pos < 0 else "NONE"},
        "orders": get_orders(),
        "last_update": trading_state["last_update"]
    })

@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    limit = request.args.get('limit', 100, type=int)
    level = request.args.get('level', None)
    logs = [l for l in log_buffer if not level or l['level'] == level]
    return jsonify({"logs": logs[-limit:], "total": len(logs)})

@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    return jsonify({"system": config["system"], "exchange": config["exchange"],
                    "ai": config["ai"], "trading": config["trading"], "risk": config["risk"]})

@app.route('/api/settings', methods=['POST'])
@login_required
def update_settings():
    global config
    data = request.get_json()
    for sec in ["system", "exchange", "ai", "trading", "risk"]:
        if sec in data:
            config[sec].update(data[sec])
    save_config(config)
    logger.info("设置已更新")
    return jsonify({"success": True, "message": "设置已保存"})

@app.route('/api/control/start', methods=['POST'])
@login_required
def start_trading():
    if trading_state["running"]:
        return jsonify({"success": False, "message": "已在运行"})
    trading_state["running"] = True
    trading_state["thread"] = threading.Thread(target=trading_loop, daemon=True)
    trading_state["thread"].start()
    config["system"]["status"] = "running"
    config["system"]["start_time"] = datetime.now().isoformat()
    save_config(config)
    logger.info("交易引擎启动")
    return jsonify({"success": True, "message": "启动成功"})

@app.route('/api/control/stop', methods=['POST'])
@login_required
def stop_trading():
    if not trading_state["running"]:
        return jsonify({"success": False, "message": "未运行"})
    trading_state["running"] = False
    time.sleep(2)
    config["system"]["status"] = "stopped"
    save_config(config)
    logger.info("交易引擎停止")
    return jsonify({"success": True, "message": "停止成功"})

# =====================================
# 信号处理与清理
# =====================================

import signal, atexit


def cleanup_resources():
    logger.info("🛑 系统关闭，清理资源...")
    trading_state["running"] = False
    wc = 0
    while trading_state.get("thread") and trading_state["thread"].is_alive() and wc < 50:
        time.sleep(0.1); wc += 1
    try:
        orders = get_orders()
        if orders:
            cancel_all_orders(orders, reason="SHUTDOWN")
    except Exception as e:
        logger.error(f"清理失败：{e}")
    try:
        config["system"]["status"] = "stopped"
        save_config(config)
    except Exception:
        pass
    logger.info("👋 关闭完成")


def signal_handler(sig, frame):
    cleanup_resources()
    os._exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
atexit.register(cleanup_resources)

# =====================================
# 启动
# =====================================

if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info(f"v{config['system']['version']} | {config['system']['mode']} | {SYMBOL}")
    logger.info(f"[过热保护] 连胜阈值:{config['trading']['win_streak_threshold']}笔 | 冷却:{config['trading']['win_streak_cooldown']}s")
    logger.info(f"[过热保护] 过热RSI阈值:{config['trading']['overheat_rsi_long']} | 偏移倍数:{config['trading']['overheat_offset_multiplier']}x")
    logger.info(f"[过热保护] 熔断阈值:{config['trading']['circuit_breaker_pct']}% | 熔断冷却:{config['trading']['circuit_breaker_cooldown']}s")
    logger.info("=" * 80)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
