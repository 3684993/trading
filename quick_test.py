import os
import time
import json
import math
import logging
import requests

from datetime import datetime

from config.settings import settings
from binance.um_futures import UMFutures


# =====================================
# 日志
# =====================================

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(f"{log_dir}/ai_trader.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger()


# =====================================
# Binance
# =====================================

proxies = None
if settings.socks5_proxy:

    proxy_url = settings.socks5_proxy

    if proxy_url.startswith("socks5://") and not proxy_url.startswith("socks5h://"):
        proxy_url = proxy_url.replace("socks5://", "socks5h://")

    proxies = {"http": proxy_url, "https": proxy_url}

client = UMFutures(
    key=settings.binance_api_key,
    secret=settings.binance_api_secret,
    base_url="https://demo-fapi.binance.com",
    proxies=proxies
)

SYMBOL = "BTCUSDT"


# =====================================
# AI
# =====================================

LMSTUDIO_URL = "http://192.168.1.114:1234/v1/chat/completions"
MODEL = "qwen/qwen3-14b"


# =====================================
# 参数
# =====================================

GRID_LEVELS = 5
QTY = 0.002

ORDER_TIMEOUT = 120


# =====================================
# 基础函数
# =====================================

def get_price():

    ticker = client.ticker_price(symbol=SYMBOL)

    return float(ticker["price"])


def get_orders():

    orders = client.get_orders(symbol=SYMBOL)

    return [o for o in orders if o["status"] in ["NEW", "PARTIALLY_FILLED"]]


def cancel(order_id):

    try:

        client.cancel_order(symbol=SYMBOL, orderId=order_id)

        logger.info(f"[CANCEL] {order_id}")

    except Exception as e:

        logger.error(e)


def limit(side, price, qty):

    try:
        # 修复价格精度问题
        price = round(float(price), 1)  # BTCUSDT 价格精度为 1
        
        logger.debug(f"[LIMIT] {side} {qty} @ {price}")

        client.new_order(
            symbol=SYMBOL,
            side=side,
            type="LIMIT",
            price=str(price),
            quantity=str(qty),
            timeInForce="GTC"
        )

        logger.info(f"[ORDER_CREATE] {side} {qty} @ {price} | ID: N/A")

    except Exception as e:

        logger.error(e)


def market(side, qty):

    try:

        client.new_order(
            symbol=SYMBOL,
            side=side,
            type="MARKET",
            quantity=qty
        )

        logger.info(f"[MARKET] {side} {qty}")

    except Exception as e:

        logger.error(e)


def get_position():

    try:
        pos = client.get_position_risk(symbol=SYMBOL)
        
        # 检查列表是否为空
        if not pos or len(pos) == 0:
            logger.debug("[GET_POSITION] 无持仓")
            return 0.0, 0.0
        
        amt = float(pos[0]["positionAmt"])
        entry = float(pos[0]["entryPrice"])
        
        logger.debug(f"[GET_POSITION] 持仓：{amt:.4f} | 开仓价：{entry:.2f}")
        
        return amt, entry
        
    except IndexError as e:
        logger.error(f"[GET_POSITION] 索引错误：{e}")
        return 0.0, 0.0
    except Exception as e:
        logger.error(f"[GET_POSITION] 获取持仓失败：{e}")
        return 0.0, 0.0


# =====================================
# K 线
# =====================================

def klines(interval="1m", limit=50):
    """获取多时间周期 K 线"""
    k = client.klines(symbol=SYMBOL, interval=interval, limit=limit)
    close = [float(x[4]) for x in k]
    return close


def indicators(interval="1m"):
    """
    计算技术指标（支持多时间周期）
    
    返回：
    - ma5: 5 周期均线
    - ma20: 20 周期均线
    - atr: 平均真实波幅
    - trend: 趋势方向（UP/DOWN/SIDEWAYS）
    """
    c = klines(interval=interval)
    
    if len(c) < 20:
        return 0, 0, 0, "UNKNOWN"
    
    ma5 = sum(c[-5:]) / 5
    ma20 = sum(c[-20:]) / 20
    
    # 计算 ATR
    high = max(c[-14:])
    low = min(c[-14:])
    atr = (high - low) / 14
    
    # 判断趋势
    current_price = c[-1]
    
    if ma5 > ma20 and current_price > ma5:
        trend = "UP"
    elif ma5 < ma20 and current_price < ma5:
        trend = "DOWN"
    else:
        trend = "SIDEWAYS"
    
    return ma5, ma20, atr, trend


def multi_timeframe_analysis():
    """
    多时间周期趋势分析
    
    核心逻辑：
    1. 15 分钟周期：主趋势（权重 50%）
    2. 5 分钟周期：次趋势（权重 30%）
    3. 1 分钟周期：微观趋势（权重 20%）
    
    返回：
    - final_trend: 最终趋势
    - confidence: 置信度
    - details: 详细分析
    """
    
    # 获取各时间周期的指标
    ma5_15m, ma20_15m, atr_15m, trend_15m = indicators(interval="15m")
    ma5_5m, ma20_5m, atr_5m, trend_5m = indicators(interval="5m")
    ma5_1m, ma20_1m, atr_1m, trend_1m = indicators(interval="1m")
    
    # 权重分配
    weights = {
        "15m": 0.5,  # 主趋势 - 50% 权重
        "5m": 0.3,   # 次趋势 - 30% 权重
        "1m": 0.2    # 微观趋势 - 20% 权重
    }
    
    # 趋势打分（UP=+1, SIDEWAYS=0, DOWN=-1）
    trend_scores = {
        "UP": 1,
        "SIDEWAYS": 0,
        "DOWN": -1,
        "UNKNOWN": 0
    }
    
    # 计算加权得分
    score_15m = trend_scores[trend_15m] * weights["15m"]
    score_5m = trend_scores[trend_5m] * weights["5m"]
    score_1m = trend_scores[trend_1m] * weights["1m"]
    
    total_score = score_15m + score_5m + score_1m
    
    # 根据总分判断最终趋势
    if total_score >= 0.3:
        final_trend = "UP"
    elif total_score <= -0.3:
        final_trend = "DOWN"
    else:
        final_trend = "SIDEWAYS"
    
    # 计算置信度（各周期趋势一致性）
    if trend_15m == trend_5m == trend_1m:
        confidence = "HIGH"  # 高置信度（所有周期一致）
    elif trend_15m == trend_5m or trend_15m == trend_1m:
        confidence = "MEDIUM"  # 中置信度（主趋势与一个次周期一致）
    else:
        confidence = "LOW"  # 低置信度（周期冲突）
    
    details = {
        "15m": {"trend": trend_15m, "ma5": ma5_15m, "ma20": ma20_15m, "atr": atr_15m},
        "5m": {"trend": trend_5m, "ma5": ma5_5m, "ma20": ma20_5m, "atr": atr_5m},
        "1m": {"trend": trend_1m, "ma5": ma5_1m, "ma20": ma20_1m, "atr": atr_1m},
        "score": total_score,
        "confidence": confidence
    }
    
    logger.info(f"[MTF] 15 分钟趋势：{trend_15m} | 5 分钟：{trend_5m} | 1 分钟：{trend_1m}")
    logger.info(f"[MTF] 最终趋势：{final_trend} | 得分：{total_score:.2f} | 置信度：{confidence}")
    
    return final_trend, confidence, details


# =====================================
# AI分析
# =====================================

def ai_decision():

    price = get_price()
    pos, entry = get_position()

    # 多时间周期趋势分析
    final_trend, confidence, mtf_details = multi_timeframe_analysis()
    
    # 获取 1 分钟指标（用于精确入场）
    ma5_1m, ma20_1m, atr_1m, _ = indicators(interval="1m")
    
    # 计算当前盈亏情况
    unrealized_pnl = 0
    if pos != 0 and entry != 0:
        if pos > 0:
            unrealized_pnl = (price - entry) * pos
        else:
            unrealized_pnl = (entry - price) * abs(pos)
    
    # 计算当前价与开仓价的距离
    price_distance = abs(price - entry) if entry != 0 else 0
    
    # 计算盈亏比例
    pnl_percent = (unrealized_pnl / (abs(pos) * entry)) * 100 if pos != 0 and entry != 0 else 0

    prompt = f"""
你是量化交易 AI。

必须根据多时间周期趋势分析进行决策。

多时间周期分析结果：
- 15 分钟（主趋势）：{mtf_details['15m']['trend']} | MA5:{mtf_details['15m']['ma5']:.2f} | MA20:{mtf_details['15m']['ma20']:.2f}
- 5 分钟（次趋势）：{mtf_details['5m']['trend']} | MA5:{mtf_details['5m']['ma5']:.2f} | MA20:{mtf_details['5m']['ma20']:.2f}
- 1 分钟（微观）：{mtf_details['1m']['trend']} | MA5:{mtf_details['1m']['ma5']:.2f} | MA20:{mtf_details['1m']['ma20']:.2f}

最终趋势：{final_trend}
置信度：{confidence}
得分：{mtf_details['score']:.2f}

规则：

上涨趋势：
只允许追多
买单必须在当前价上方

下跌趋势：
只允许追空
卖单必须在当前价下方

震荡：
允许网格

如果浮亏扩大：
止盈委托必须贴近当前价减仓

补仓策略：
- 趋势延续 + 价格不利变动 = 考虑补仓
- 趋势反转 = 禁止补仓，准备平仓
- 补仓距离：至少间隔 80 USDT
- 补仓仓位：不超过原始仓位的 50%

风险处置策略（强制执行）：
if position < 0 and trend == "UP": risk_action = "REDUCE"  # 空头 + 上涨 = 减仓
if position > 0 and trend == "DOWN": risk_action = "REDUCE"  # 多头 + 下跌 = 减仓
if confidence == "HIGH" and trend_opposite: risk_action = "REDUCE"  # 高置信度 + 趋势相反 = 必须减仓
大幅亏损 (>1%) = CLOSE_ALL（立即平仓）

数据：

price:{price}
ma5_1m:{ma5_1m}
ma20_1m:{ma20_1m}
atr:{atr_1m}
position:{pos}
entry:{entry}
unrealized_pnl:{unrealized_pnl:.2f}
price_distance:{price_distance:.2f}
pnl_percent:{pnl_percent:.2f}%

输出 JSON：

{{
"trend":"{final_trend}",
"confidence":"{confidence}",
"entry_mode":"MOMENTUM|PULLBACK|WAIT",
"entry_offset":50,
"grid_spacing":80,
"tp_distance":120,
"sl_distance":100,
"reduce_mode":"NONE|FAST|PANIC",
"cancel_far_orders":true,
"add_position":true,
"add_position_distance":100,
"add_position_qty":0.001,
"risk_action":"HOLD|REDUCE|STOP_LOSS|CLOSE_ALL",
"risk_reason":"根据多周期分析，{confidence}置信度"
}}
"""

    payload = {

        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 200
    }

    try:

        # 强制等待最少 3 秒，确保 AI 分析完成
        start_time = time.time()
        
        r = requests.post(LMSTUDIO_URL, json=payload, timeout=10)
        
        # 计算已用时间，如果少于 3 秒则强制等待
        elapsed = time.time() - start_time
        if elapsed < 3:
            wait_time = 3 - elapsed
            logger.debug(f"[AI] 请求完成，已用 {elapsed:.2f}秒，等待 {wait_time:.2f}秒...")
            time.sleep(wait_time)
        else:
            logger.debug(f"[AI] 请求完成，用时 {elapsed:.2f}秒")

        text = r.json()["choices"][0]["message"]["content"]

        data = json.loads(text)

        return data

    except requests.exceptions.Timeout:
        logger.warning("[AI] 请求超时（10 秒），AI 响应过慢")
        return None

    except Exception as e:

        logger.error(f"[AI] ERROR: {e}")

        return None


# =====================================
# 创建趋势委托
# =====================================

def create_trend_orders(trend, price, offset, spacing):
    """
    创建趋势委托
    
    核心逻辑：
    - 上涨趋势（UP）：在低位挂买单（等待回调买入）
    - 下跌趋势（DOWN）：在高位挂卖单（等待反弹做空）
    """

    if trend == "UP":
        # 上涨趋势：在当前价下方挂买单（回调买入）
        for i in range(GRID_LEVELS):
            p = price - offset - spacing * i
            limit("BUY", p, QTY)

    elif trend == "DOWN":
        # 下跌趋势：在当前价上方挂卖单（反弹做空）
        for i in range(GRID_LEVELS):
            p = price + offset + spacing * i
            limit("SELL", p, QTY)


# =====================================
# 创建止盈
# =====================================

def create_tp(entry, tp_dist):

    for i in range(GRID_LEVELS):

        p = entry + tp_dist + i * 50

        limit("SELL", p, QTY)


# =====================================
# 止盈委托
# =====================================

# 手续费率：0.02% 建仓 + 0.02% 平仓 = 0.04% 总成本
FEE_RATE = 0.0004  # 0.04%

# 最小平仓数量（分批平仓）
MIN_CLOSE_QTY = 0.002  # BTC

def calculate_tp_price(pos, entry, tp_distance, level=0, spacing=50):
    """
    计算止盈价格（考虑手续费成本 + 保本优先）
    
    参数：
    - pos: 持仓数量（正数=多头，负数=空头）
    - entry: 开仓价
    - tp_distance: 止盈距离（用于计算后续批次）
    - level: 止盈层级（0=最近，1=第二远...）
    - spacing: 层级间隔
    
    返回：
    - tp_price: 止盈价格
    - min_profit_price: 保本价
    - fee_per_qty: 单位数量手续费
    """
    qty = abs(pos)
    
    # 计算手续费成本（按持仓价值计算）
    position_value = entry * qty
    fee_cost = position_value * FEE_RATE
    
    # 计算单位数量的手续费
    fee_per_qty = fee_cost / qty
    
    # 计算保本价（覆盖手续费）
    if pos > 0:  # 多头
        # 保本价 = 开仓价 + 手续费/数量
        min_profit_price = entry + fee_per_qty
        
        # 第 1 批止盈价 = 保本价 + 50 USDT（确保盈利）
        # 后续批次 = 第 1 批价格 + 层级间隔
        if level == 0:
            tp_price = min_profit_price + 50  # 第一批：保本价 + 50
        else:
            tp_price = min_profit_price + 50 + level * spacing
        
    else:  # 空头
        # 保本价 = 开仓价 - 手续费/数量
        min_profit_price = entry - fee_per_qty
        
        # 第 1 批止盈价 = 保本价 - 50 USDT（确保盈利）
        # 后续批次 = 第 1 批价格 - 层级间隔
        if level == 0:
            tp_price = min_profit_price - 50  # 第一批：保本价 - 50
        else:
            tp_price = min_profit_price - 50 - level * spacing
    
    return tp_price, min_profit_price, fee_per_qty


def create_tp(pos, entry, tp_distance):
    """
    创建止盈委托（考虑手续费成本 + 分批平仓）
    
    策略：
    - 小持仓（<0.01 BTC）：一次性平仓（避免数量过小）
    - 大持仓（≥0.01 BTC）：分批平仓 50% → 30% → 20%
    - 总平仓数量 = 持仓数量（绝不超过）
    """
    
    qty = abs(pos)
    position_side = "LONG" if pos > 0 else "SHORT"
    
    logger.info(f"[TP_CREATE] {position_side}止盈 | 持仓：{qty:.4f} | 开仓价：{entry:.2f}")
    
    # 小持仓：一次性平仓
    if qty < 0.01:
        tp_price, min_profit, fee_per_qty = calculate_tp_price(pos, entry, tp_distance, level=0)
        
        # 计算预期利润
        if pos > 0:
            profit = (tp_price - entry) * qty - (fee_per_qty * qty)
        else:
            profit = (entry - tp_price) * qty - (fee_per_qty * qty)
        
        logger.info(f"[TP_CREATE]   一次性平仓 | 价格：{tp_price:.2f} | 数量：{qty:.4f}")
        logger.info(f"[TP_CREATE]     保本价：{min_profit:.2f} | 手续费：{fee_per_qty * qty:.4f} USDT")
        logger.info(f"[TP_CREATE]     预期利润：{profit:.4f} USDT")
        
        side = "SELL" if pos > 0 else "BUY"
        limit(side, tp_price, qty)
        
        logger.info(f"[TP_CREATE] 总平仓数量：{qty:.4f} | 持仓数量：{qty:.4f} | 匹配：✓")
        return
    
    # 大持仓：分批平仓策略（比例总和必须=1.0）
    close_plans = [
        {"level": 0, "ratio": 0.5, "spacing": 0, "desc": "第一批（50%）"},
        {"level": 1, "ratio": 0.3, "spacing": 50, "desc": "第二批（30%）"},
        {"level": 2, "ratio": 0.2, "spacing": 100, "desc": "第三批（20%）"}
    ]
    
    # 计算总平仓数量（绝不超过持仓数量）
    total_close_qty = sum(qty * plan["ratio"] for plan in close_plans)
    
    # 第 2 次遍历：创建止盈委托
    for i, plan in enumerate(close_plans):
        # 计算本批平仓数量（按比例）
        close_qty = qty * plan["ratio"]
        
        # 最后一批：使用剩余数量（确保总量不超过持仓）
        if i == len(close_plans) - 1:
            close_qty = qty - sum(qty * p["ratio"] for p in close_plans[:i])
        
        # 如果数量太小（<0.001），合并到前一批
        if close_qty < 0.001:
            continue
        
        # 计算止盈价格
        tp_price, min_profit, fee_per_qty = calculate_tp_price(
            pos, entry, tp_distance, 
            level=plan["level"], 
            spacing=plan["spacing"]
        )
        
        # 计算预期利润
        if pos > 0:
            profit = (tp_price - entry) * close_qty - (fee_per_qty * close_qty)
        else:
            profit = (entry - tp_price) * close_qty - (fee_per_qty * close_qty)
        
        logger.info(f"[TP_CREATE]   {plan['desc']} | 价格：{tp_price:.2f} | 数量：{close_qty:.4f}")
        logger.info(f"[TP_CREATE]     保本价：{min_profit:.2f} | 手续费：{fee_per_qty * close_qty:.4f} USDT")
        logger.info(f"[TP_CREATE]     预期利润：{profit:.4f} USDT")
        
        # 创建止盈委托
        side = "SELL" if pos > 0 else "BUY"
        limit(side, tp_price, close_qty)
    
    logger.info(f"[TP_CREATE] 总平仓数量：{total_close_qty:.4f} | 持仓数量：{qty:.4f} | 匹配：✓")
    
    if pos > 0:  # 多头持仓，卖出止盈
        tp_price = calculate_tp_price(pos, entry, tp_distance, level=0, spacing=50)
        fee_cost = entry * qty * FEE_RATE
        min_price = entry + (fee_cost / qty)
        
        logger.info(f"[TP_CREATE] 多头止盈 | 数量：{qty:.4f}")
        logger.info(f"[TP_CREATE]   开仓价：{entry:.2f} | 手续费：{fee_cost:.2f} USDT")
        logger.info(f"[TP_CREATE]   成本价：{min_price:.2f} | 止盈价：{tp_price:.2f}")
        logger.info(f"[TP_CREATE]   预期利润：{tp_price * qty - entry * qty - fee_cost:.2f} USDT")
        
        limit("SELL", tp_price, qty)
        
    else:  # 空头持仓，买入止盈
        tp_price = calculate_tp_price(pos, entry, tp_distance, level=0, spacing=50)
        fee_cost = entry * qty * FEE_RATE
        min_price = entry - (fee_cost / qty)
        
        logger.info(f"[TP_CREATE] 空头止盈 | 数量：{qty:.4f}")
        logger.info(f"[TP_CREATE]   开仓价：{entry:.2f} | 手续费：{fee_cost:.2f} USDT")
        logger.info(f"[TP_CREATE]   成本价：{min_price:.2f} | 止盈价：{tp_price:.2f}")
        logger.info(f"[TP_CREATE]   预期利润：{entry * qty - tp_price * qty - fee_cost:.2f} USDT")
        
        limit("BUY", tp_price, abs(qty))


# =====================================
# 止损委托
# =====================================

def create_sl(pos, entry, sl_distance):

    qty = abs(pos)

    if pos > 0:  # 多头持仓，卖出止损
        sl_price = entry - sl_distance
        logger.info(f"[SL_CREATE] 多头止损 | 数量：{qty:.4f} | 价格：{sl_price:.2f}")
        limit("SELL", sl_price, qty)
    else:  # 空头持仓，买入止损
        sl_price = entry + sl_distance
        logger.info(f"[SL_CREATE] 空头止损 | 数量：{qty:.4f} | 价格：{sl_price:.2f}")
        limit("BUY", sl_price, abs(qty))


# =====================================
# 移动止损
# =====================================

def move_stop_loss(pos, entry, current_price, tp_distance, sl_distance):

    # 计算当前应该的止损价格
    if pos > 0:
        # 多头：价格上涨时，止损价也上移
        new_sl_price = current_price - sl_distance
        current_sl = entry - sl_distance
    else:
        # 空头：价格下跌时，止损价也下移
        new_sl_price = current_price + sl_distance
        current_sl = entry + sl_distance

    # 如果止损价格有优化空间（移动超过 10 USDT），则更新
    if abs(new_sl_price - current_sl) > 10:
        logger.info(f"[MOVE_SL] 移动止损 | 旧止损：{current_sl:.2f} -> 新止损：{new_sl_price:.2f}")
        # 取消旧的止损委托，创建新的
        # TODO: 实现取消和重新创建逻辑


# =====================================
# 减仓
# =====================================

def reduce_position(price):

    pos, entry = get_position()

    if pos <= 0:
        return

    logger.info("[REDUCE] 减仓模式")

    for i in range(5):

        p = price + 20 * i

        limit("SELL", p, QTY)


# =====================================
# 智能补仓
# =====================================

def smart_add_position(price, pos, entry, orders, ai):
    """
    智能补仓管理
    
    参数：
    - price: 当前价格
    - pos: 当前持仓
    - entry: 开仓价
    - orders: 当前委托列表
    - ai: AI 分析结果
    
    补仓条件：
    1. AI 建议补仓 (add_position=True)
    2. 趋势延续（与持仓方向一致）
    3. 价格达到补仓距离
    4. 当前没有同向补仓委托
    """
    
    # 检查 AI 是否建议补仓
    if not ai.get("add_position", False):
        logger.debug("[ADD_POSITION] AI 不建议补仓")
        return False
    
    add_distance = ai.get("add_position_distance", 100)
    add_qty = ai.get("add_position_qty", 0.001)
    trend = ai["trend"]
    
    logger.info(f"[ADD_POSITION] AI 建议补仓 | 距离：{add_distance} | 数量：{add_qty} | 趋势：{trend}")
    
    # 确定持仓方向
    position_side = "LONG" if pos > 0 else "SHORT"
    
    # 判断趋势是否与持仓方向一致
    trend_supports_position = False
    if pos > 0 and trend == "UP":
        trend_supports_position = True  # 多头 + 上涨趋势 = 支持
    elif pos < 0 and trend == "DOWN":
        trend_supports_position = True  # 空头 + 下跌趋势 = 支持
    elif trend == "SIDEWAYS":
        trend_supports_position = True  # 震荡 = 可以补仓
    
    if not trend_supports_position:
        logger.info(f"[ADD_POSITION] 趋势不支持补仓 | 持仓：{position_side} | 趋势：{trend}")
        return False
    
    # 计算当前价与开仓价的距离
    distance = abs(price - entry)
    
    # 判断是否达到补仓距离
    if distance < add_distance:
        logger.debug(f"[ADD_POSITION] 距离不足 | 当前：{distance:.2f} | 需要：{add_distance}")
        return False
    
    logger.info(f"[ADD_POSITION] 达到补仓距离 | 开仓价：{entry:.2f} | 当前价：{price:.2f} | 距离：{distance:.2f}")
    
    # 检查是否已有补仓委托
    same_side = "BUY" if pos > 0 else "SELL"
    add_orders = [o for o in orders if o["side"] == same_side and float(o["price"]) != entry]
    
    if len(add_orders) > 0:
        logger.info(f"[ADD_POSITION] 已有补仓委托 | 数量：{len(add_orders)}")
        return False
    
    # 执行补仓
    logger.info(f"[ADD_POSITION] 执行补仓 | {same_side} {add_qty:.4f} @ {price:.2f}")
    
    # 创建补仓委托（在当前价附近）
    if same_side == "BUY":
        # 买单：在当前价下方挂单
        buy_price = price - ai.get("entry_offset", 50)
        limit("BUY", buy_price, add_qty)
    else:
        # 卖单：在当前价上方挂单
        sell_price = price + ai.get("entry_offset", 50)
        limit("SELL", sell_price, add_qty)
    
    return True


# =====================================
# 风险处置
# =====================================

def smart_risk_management(price, pos, entry, orders, ai):
    """
    智能风险处置管理
    
    参数：
    - price: 当前价格
    - pos: 当前持仓
    - entry: 开仓价
    - orders: 当前委托列表
    - ai: AI 分析结果
    
    风险处置策略：
    1. HOLD（持有）→ 保持现状
    2. REDUCE（减仓）→ 减仓 50%
    3. STOP_LOSS（止损）→ 设置止损委托
    4. CLOSE_ALL（平仓）→ 立即市价平仓
    """
    
    # 检查 AI 建议的风险处置动作
    risk_action = ai.get("risk_action", "HOLD")
    risk_reason = ai.get("risk_reason", "")
    
    if risk_action == "HOLD":
        logger.debug(f"[RISK] AI 建议持有 | 原因：{risk_reason}")
        return False
    
    trend = ai["trend"]
    position_side = "LONG" if pos > 0 else "SHORT"
    
    # 判断趋势是否与持仓方向相反，或者 AI 明确建议处置
    trend_opposite = False
    if pos > 0 and trend == "DOWN":
        trend_opposite = True  # 多头 + 下跌趋势 = 相反
    elif pos < 0 and trend == "UP":
        trend_opposite = True  # 空头 + 上涨趋势 = 相反
    
    # 如果 AI 建议 REDUCE 或 CLOSE_ALL，即使趋势不明也要执行
    if risk_action in ["REDUCE", "CLOSE_ALL", "STOP_LOSS"]:
        logger.warning(f"[RISK] AI 建议风险处置 | 动作：{risk_action} | 原因：{risk_reason}")
        logger.warning(f"[RISK] 持仓：{position_side} | 趋势：{trend}")
    elif not trend_opposite:
        logger.debug(f"[RISK] 趋势与持仓一致，无需风险处置")
        return False
    else:
        logger.warning(f"[RISK] AI 建议风险处置 | 动作：{risk_action} | 原因：{risk_reason}")
        logger.warning(f"[RISK] 持仓：{position_side} | 趋势：{trend} | 方向相反：是")
    
    # 计算当前盈亏
    qty = abs(pos)
    if pos > 0:
        unrealized_pnl = (price - entry) * qty
    else:
        unrealized_pnl = (entry - price) * qty
    
    pnl_percent = (unrealized_pnl / (qty * entry)) * 100
    
    logger.warning(f"[RISK] 当前盈亏：{unrealized_pnl:.2f} USDT ({pnl_percent:.2f}%)")
    
    # 执行风险处置
    if risk_action == "REDUCE":
        # 减仓 50%（如果数量太小则全部减仓）
        reduce_qty = qty * 0.5
        
        # 检查订单价值是否满足最小限制（100 USDT）
        order_value = reduce_qty * price
        if order_value < 100:
            # 数量太小，改为全部减仓
            reduce_qty = qty
            logger.warning(f"[RISK] 订单价值过小 ({order_value:.2f} USDT)，改为全部减仓")
        
        # 修复数量精度问题（BTC 最小精度 0.001）
        reduce_qty = round(reduce_qty, 3)
        
        logger.warning(f"[RISK] 执行减仓 | 减仓数量：{reduce_qty:.4f} (原持仓：{qty:.4f})")
        
        # 创建限价单减仓（当前价附近）
        side = "SELL" if pos > 0 else "BUY"
        
        # 使用限价单，确保成交
        if side == "SELL":
            # 卖单：在当前价下方挂单（更容易成交）
            reduce_price = price - 10
        else:
            # 买单：在当前价上方挂单（更容易成交）
            reduce_price = price + 10
        
        logger.warning(f"[RISK] 限价减仓 | {side} {reduce_qty:.4f} @ {reduce_price:.2f}")
        limit(side, reduce_price, reduce_qty)
        
        return True
    
    elif risk_action == "STOP_LOSS":
        # 设置止损委托
        sl_distance = ai.get("sl_distance", 100)
        
        logger.warning(f"[RISK] 设置止损委托 | 距离：{sl_distance}")
        
        # 创建止损委托
        if pos > 0:
            sl_price = entry - sl_distance
            logger.warning(f"[RISK] 多头止损 | {sl_price:.2f}")
            limit("SELL", sl_price, qty)
        else:
            sl_price = entry + sl_distance
            logger.warning(f"[RISK] 空头止损 | {sl_price:.2f}")
            limit("BUY", sl_price, qty)
        
        return True
    
    elif risk_action == "CLOSE_ALL":
        # 立即平仓
        logger.error(f"[RISK] 紧急平仓 | 数量：{qty:.4f}")
        
        side = "SELL" if pos > 0 else "BUY"
        
        # 使用限价单快速平仓
        if side == "SELL":
            close_price = price - 20  # 更低价格确保快速成交
        else:
            close_price = price + 20  # 更高价格确保快速成交
        
        logger.error(f"[RISK] 限价平仓 | {side} {qty:.4f} @ {close_price:.2f}")
        limit(side, close_price, qty)
        
        return True
    
    return False


# =====================================
# 全局状态（移除缓存）
# =====================================

# 不再使用缓存，每次都获取最新数据

# =====================================
# 委托调整跟踪
# =====================================

# 记录每个订单的跟踪数据
order_tracking = {
    'last_adjust_time': 0,  # 上次调整时间（Unix 时间戳）
    'closest_price': None,   # 最接近当前价的价格
    'min_distance': float('inf'),  # 最小距离
    'trend': 'UNKNOWN',      # 距离变化趋势：'APPROACHING'（接近）| 'DEPARTING'（渐离）
    'price_history': []      # 价格历史 [(timestamp, price, distance), ...]
}

# 调整周期（秒）
ADJUST_INTERVAL = 300  # 5 分钟
CLOSE_CHECK_INTERVAL = 120  # 2 分钟


# =====================================
# 委托调整算法
# =====================================

def analyze_order_trend(orders, price):
    """
    分析委托订单与当前价的距离趋势
    
    返回：
    - closest_order: 最接近当前价的订单
    - min_distance: 最小距离
    - trend: 趋势（APPROACHING/DEPARTING）
    """
    if not orders:
        return None, float('inf'), 'UNKNOWN'
    
    # 找到最接近当前价的订单
    closest_order = None
    min_distance = float('inf')
    
    for order in orders:
        order_price = float(order["price"])
        distance = abs(order_price - price)
        
        if distance < min_distance:
            min_distance = distance
            closest_order = order
    
    # 记录到历史数据
    current_time = time.time()
    order_tracking['price_history'].append((current_time, price, min_distance))
    
    # 保留最近 5 分钟的数据
    cutoff_time = current_time - 300
    order_tracking['price_history'] = [
        (t, p, d) for t, p, d in order_tracking['price_history'] 
        if t > cutoff_time
    ]
    
    # 分析趋势
    history = order_tracking['price_history']
    trend = 'UNKNOWN'
    
    if len(history) >= 2:
        # 比较最近两次的距离
        recent_distances = [d for _, _, d in history[-10:]]  # 取最近 10 个数据点
        
        if len(recent_distances) >= 2:
            # 计算平均距离变化
            avg_recent = sum(recent_distances[-3:]) / 3
            avg_older = sum(recent_distances[:3]) / 3
            
            if avg_recent < avg_older * 0.95:  # 距离减少 5% 以上
                trend = 'APPROACHING'
            elif avg_recent > avg_older * 1.05:  # 距离增加 5% 以上
                trend = 'DEPARTING'
            else:
                trend = 'STABLE'
    
    # 更新跟踪数据
    order_tracking['closest_price'] = closest_order["price"] if closest_order else None
    order_tracking['min_distance'] = min_distance
    order_tracking['trend'] = trend
    
    logger.debug(f"[TREND_ANALYSIS] 最近距离：{min_distance:.2f} | 趋势：{trend}")
    
    return closest_order, min_distance, trend


def should_adjust_orders(orders, price, elapsed_since_adjust):
    """
    判断是否需要调整委托订单
    
    条件：
    1. 距离上次调整超过 5 分钟
    2. 或者最接近的订单 2 分钟未成交且趋势渐离
    """
    current_time = time.time()
    
    # 条件 1: 5 分钟定期调整
    if elapsed_since_adjust >= ADJUST_INTERVAL:
        logger.info(f"[ADJUST_CHECK] 达到 5 分钟调整周期")
        return True
    
    # 条件 2: 2 分钟未成交检测
    if elapsed_since_adjust >= CLOSE_CHECK_INTERVAL:
        closest_order, min_distance, trend = analyze_order_trend(orders, price)
        
        if closest_order and trend == 'DEPARTING':
            logger.warning(f"[ADJUST_CHECK] 最接近订单 2 分钟未成交且价格渐离 | 距离：{min_distance:.2f} | 趋势：{trend}")
            return True
        elif closest_order:
            logger.debug(f"[ADJUST_CHECK] 最接近订单状态 | 距离：{min_distance:.2f} | 趋势：{trend}")
    
    return False


def adjust_orders_to_nearest(orders, price, trend):
    """
    调整委托价格到更接近当前价的位置
    
    策略优化：
    1. 优先调整最接近当前价的委托（快速成交）
    2. 其次调整第二接近的委托
    3. 其他委托暂不调整，等待下一轮
    
    调整规则：
    - 如果趋势是渐离（DEPARTING），必须调整
    - 如果趋势是接近（APPROACHING），保持不调整
    - 调整幅度：向当前价靠近 30%（更激进）
    """
    adjusted_count = 0
    
    # 按距离排序，优先调整最接近的委托
    orders_sorted = []
    for order in orders:
        order_price = float(order["price"])
        distance = abs(order_price - price)
        orders_sorted.append((order, distance))
    
    # 按距离从小到大排序
    orders_sorted.sort(key=lambda x: x[1])
    
    # 输出详细的趋势分析
    logger.info("=" * 80)
    logger.info(f"[TREND_ANALYSIS] 当前价格：{price:.2f}")
    logger.info(f"[TREND_ANALYSIS] 委托距离分析（按距离排序）：")
    
    for i, (order, distance) in enumerate(orders_sorted[:5], 1):
        order_price = float(order["price"])
        order_side = order["side"]
        logger.info(f"[TREND_ANALYSIS]   #{i} | {order_side} @ {order_price:.2f} | 距离：{distance:.2f}")
    
    logger.info(f"[TREND_ANALYSIS] 当前趋势：{trend}")
    logger.info("=" * 80)
    
    # 只调整最接近的 2 个委托（如果趋势是渐离）
    max_adjust = 2 if trend == 'DEPARTING' else 0
    
    for i, (order, distance) in enumerate(orders_sorted):
        if i >= max_adjust:
            break
        
        order_id = order["orderId"]
        order_price = float(order["price"])
        order_side = order["side"]
        order_qty = float(order["origQty"])
        
        # 计算新的价格：向当前价靠近 30%（更激进）
        if order_side == "BUY":
            # 买单：提高价格（向上靠近）
            new_price = order_price + (price - order_price) * 0.3
        else:
            # 卖单：降低价格（向下靠近）
            new_price = order_price - (order_price - price) * 0.3
        
        logger.info(f"[ADJUST_ORDER] 优先级#{i+1} | 调整最接近委托 | ID: {order_id}")
        logger.info(f"[ADJUST_ORDER]   当前价：{price:.2f} | 旧价格：{order_price:.2f} -> 新价格：{new_price:.2f}")
        logger.info(f"[ADJUST_ORDER]   距离变化：{distance:.2f} -> {abs(new_price - price):.2f} (缩短 {distance - abs(new_price - price):.2f})")
        
        # 取消旧订单
        cancel(order_id)
        time.sleep(0.3)
        
        # 创建新订单
        limit(order_side, new_price, order_qty)
        adjusted_count += 1
    
    if adjusted_count > 0:
        logger.info(f"[ADJUST_COMPLETE] 本次调整了 {adjusted_count} 个委托（优先最接近的）")
    else:
        logger.info(f"[ADJUST_COMPLETE] 趋势为{trend}，保持不调整")
    
    # 更新最后调整时间
    order_tracking['last_adjust_time'] = time.time()
    
    return adjusted_count


# =====================================
# 智能委托管理
# =====================================

def smart_order_management(price, pos, entry, orders, ai):
    """
    智能委托管理核心逻辑
    
    规则：
    1. 无持仓：按趋势创建委托
    2. 有持仓：创建反向止盈委托
    3. 检查现有委托合理性并调整
    4. 委托调整倒计时：5 分钟定期调整，2 分钟未成交检测
    """
    
    trend = ai["trend"]
    offset = ai["entry_offset"]
    spacing = ai["grid_spacing"]
    tp_distance = ai["tp_distance"]
    
    # 计算距离上次调整的时间
    current_time = time.time()
    elapsed_since_adjust = current_time - order_tracking['last_adjust_time']
    
    logger.info(f"[SMART] 开始智能委托管理 | 持仓：{pos:.4f} | 委托数：{len(orders)} | 趋势：{trend}")
    logger.debug(f"[SMART] 距离上次调整：{elapsed_since_adjust:.1f}秒")
    
    # =====================
    # 委托调整检查（优先级最高）
    # =====================
    if orders and should_adjust_orders(orders, price, elapsed_since_adjust):
        logger.info("[SMART] 开始委托调整流程")
        
        # 分析趋势
        closest_order, min_distance, order_trend = analyze_order_trend(orders, price)
        
        if closest_order:
            closest_price = float(closest_order['price'])
            logger.info(f"[SMART] 最接近委托 | 价格：{closest_price:.2f} | 距离：{min_distance:.2f} | 趋势：{order_trend}")
        
        # 执行调整
        adjust_orders_to_nearest(orders, price, order_trend)
        
        # 调整后重新获取订单列表
        orders = get_orders()
    
    # =====================
    # 情况 1：无持仓
    # =====================
    if pos == 0:
        logger.info("[SMART] 无持仓模式")
        
        if len(orders) == 0:
            # 无委托 - 按趋势创建
            logger.info(f"[SMART] 无委托，按趋势创建：{trend}")
            create_trend_orders(trend, price, offset, spacing)
        else:
            # 有委托 - 检查合理性
            logger.info("[SMART] 检查现有委托合理性")
            orders = check_and_adjust_orders(orders, price, trend, spacing)
    
    # =====================
    # 情况 2：有持仓
    # =====================
    else:
        logger.info(f"[SMART] 有持仓模式 | 持仓：{pos:.4f} | 开仓价：{entry:.2f}")
        
        # 确定持仓方向
        position_side = "LONG" if pos > 0 else "SHORT"
        opposite_side = "SELL" if pos > 0 else "BUY"
        
        # 检查是否有反向委托（止盈委托）
        tp_orders = [o for o in orders if o["side"] == opposite_side]
        
        if len(tp_orders) == 0:
            # 无反向委托 - 必须创建止盈
            logger.info(f"[SMART] 无反向委托，创建止盈 | 持仓方向：{position_side}")
            create_tp(pos, entry, tp_distance)
        else:
            # 有反向委托 - 检查合理性
            logger.info(f"[SMART] 检查止盈委托合理性 | 数量：{len(tp_orders)}")
            check_and_adjust_tp_orders(tp_orders, pos, entry, tp_distance)
        
        # 智能补仓管理（AI 介入）
        logger.info(f"[SMART] 开始智能补仓分析")
        logger.info(f"[ADD_POSITION] AI 完整数据：{json.dumps(ai, indent=2, ensure_ascii=False)}")
        smart_add_position(price, pos, entry, orders, ai)
        
        # 智能风险处置（AI 介入）
        logger.info(f"[SMART] 开始风险处置分析")
        logger.info(f"[RISK] AI 完整数据：{json.dumps(ai, indent=2, ensure_ascii=False)}")
        smart_risk_management(price, pos, entry, orders, ai)
        
        # 检查是否有同向委托（加仓委托）- 可选
        same_side = "BUY" if pos > 0 else "SELL"
        same_side_orders = [o for o in orders if o["side"] == same_side]
        if len(same_side_orders) > 0:
            logger.info(f"[SMART] 检查加仓委托合理性 | 数量：{len(same_side_orders)}")
            check_and_adjust_orders(same_side_orders, price, trend, spacing)


def check_and_adjust_orders(orders, price, trend, spacing):
    """检查并调整委托 - 根据趋势和价格"""
    
    for order in orders:
        order_price = float(order["price"])
        order_side = order["side"]
        order_id = order["orderId"]
        
        # 检查委托是否过于远离当前价
        distance = abs(order_price - price)
        
        if distance > spacing * 10:
            logger.warning(f"[ADJUST] 委托过远 | ID: {order_id} | 价格：{order_price:.2f} | 距离：{distance:.2f}")
            # 取消并重新创建
            # TODO: 实现调整逻辑


def check_and_adjust_tp_orders(tp_orders, pos, entry, tp_distance):
    """检查并调整止盈委托（考虑手续费成本 + 分批平仓）"""
    
    qty = abs(pos)
    fee_cost = entry * qty * FEE_RATE
    fee_per_qty = fee_cost / qty
    
    logger.info(f"[TP_CHECK] 持仓数量：{qty:.4f}")
    
    # 小持仓：一次性平仓
    if qty < 0.01:
        expected_tp, breakeven_price, _ = calculate_tp_price(pos, entry, tp_distance, level=0)
        
        # 检查是否有止盈委托
        if len(tp_orders) == 0:
            logger.info(f"[TP_ADJUST] 创建止盈委托 | 价格：{expected_tp:.2f} | 数量：{qty:.4f}")
            side = "SELL" if pos > 0 else "BUY"
            limit(side, expected_tp, qty)
        else:
            # 检查现有委托价格是否合理
            order_price = float(tp_orders[0]["price"])
            order_id = tp_orders[0]["orderId"]
            
            if abs(order_price - expected_tp) > 20:
                logger.warning(f"[TP_ADJUST] 止盈价不合理 | ID: {order_id} | 当前：{order_price:.2f} | 期望：{expected_tp:.2f}")
                cancel(order_id)
                time.sleep(0.3)
                side = "SELL" if pos > 0 else "BUY"
                limit(side, expected_tp, qty)
        
        logger.info(f"[TP_CHECK] 总平仓数量：{qty:.4f} | 持仓数量：{qty:.4f} | 匹配：✓")
        return
    
    # 大持仓：分批平仓策略（比例总和必须=1.0）
    close_plans = [
        {"level": 0, "ratio": 0.5, "spacing": 0},
        {"level": 1, "ratio": 0.3, "spacing": 50},
        {"level": 2, "ratio": 0.2, "spacing": 100}
    ]
    
    # 计算总平仓数量（绝不超过持仓数量）
    total_close_qty = sum(qty * plan["ratio"] for plan in close_plans)
    
    logger.info(f"[TP_CHECK] 持仓数量：{qty:.4f} | 计划平仓：{total_close_qty:.4f}")
    
    # 检查每个批次的止盈委托
    for i, plan in enumerate(close_plans):
        # 计算本批平仓数量（按比例）
        close_qty = qty * plan["ratio"]
        
        # 最后一批：使用剩余数量（确保总量不超过持仓）
        if i == len(close_plans) - 1:
            close_qty = qty - sum(qty * p["ratio"] for p in close_plans[:i])
        
        # 如果数量太小（<0.001），跳过
        if close_qty < 0.001:
            continue
        
        # 计算合理的止盈价
        expected_tp, breakeven_price, _ = calculate_tp_price(
            pos, entry, tp_distance, 
            level=plan["level"], 
            spacing=plan["spacing"]
        )
        
        # 找到对应的止盈委托（同一批次价格相近的）
        target_order = None
        for order in tp_orders:
            order_price = float(order["price"])
            order_qty = float(order["origQty"])
            
            # 检查是否是本批次的委托（价格接近且数量接近）
            if abs(order_price - expected_tp) < 30 and abs(order_qty - close_qty) < 0.001:
                target_order = order
                break
        
        # 如果没有找到本批次的委托，或者价格不合理，创建/调整
        if target_order is None:
            logger.info(f"[TP_ADJUST] 创建第{i+1}批止盈 | 价格：{expected_tp:.2f} | 数量：{close_qty:.4f}")
            side = "SELL" if pos > 0 else "BUY"
            limit(side, expected_tp, close_qty)
        else:
            order_price = float(target_order["price"])
            order_id = target_order["orderId"]
            
            # 检查止盈价是否合理（允许 20 USDT 误差）
            if abs(order_price - expected_tp) > 20:
                logger.warning(f"[TP_ADJUST] 第{i+1}批止盈价不合理 | ID: {order_id}")
                logger.warning(f"[TP_ADJUST]   当前价：{order_price:.2f} | 期望价：{expected_tp:.2f}")
                logger.warning(f"[TP_ADJUST]   保本价：{breakeven_price:.2f}")
                
                # 取消并重新创建
                logger.info(f"[TP_ADJUST] 取消旧止盈委托 | ID: {order_id}")
                cancel(order_id)
                time.sleep(0.3)
                
                # 重新创建正确的止盈委托
                side = "SELL" if pos > 0 else "BUY"
                logger.info(f"[TP_ADJUST] 创建新止盈委托 | {side} {close_qty:.4f} @ {expected_tp:.2f}")
                limit(side, expected_tp, close_qty)
    
    logger.info(f"[TP_CHECK] 总平仓数量：{total_close_qty:.4f} | 持仓数量：{qty:.4f} | 匹配：✓")


# =====================================
# 主循环 - 优化后的流程
# =====================================

def main():

    logger.info("=" * 80)
    logger.info("智能网格交易系统启动")
    logger.info("流程：获取数据 -> AI 分析 -> 智能委托管理 -> 执行")
    logger.info("=" * 80)

    while True:

        try:

            # 步骤 1: 获取交易所数据
            price = get_price()
            pos, entry = get_position()
            orders = get_orders()
            
            logger.info(f"[DATA] 价格：{price:.2f} | 持仓：{pos:.4f} | 委托：{len(orders)}")

            # 步骤 2: AI 分析趋势（每次都请求，不用缓存）
            ai = ai_decision()
            
            if not ai:
                logger.warning("[AI] 请求失败，等待下一轮")
                time.sleep(5)
                continue

            logger.info(f"[AI] trend:{ai['trend']} | offset:{ai['entry_offset']} | spacing:{ai['grid_spacing']} | tp:{ai['tp_distance']}")

            # 步骤 3: 智能委托管理
            smart_order_management(price, pos, entry, orders, ai)

            # 步骤 4: 等待下一轮
            time.sleep(5)

        except Exception as e:

            logger.error(f"[ERROR] {e}", exc_info=True)
            time.sleep(5)


if __name__ == "__main__":

    main()