#!/usr/bin/env python3
"""
CRYPTO JET V17
15m erken hareket + 1H/4H/1D onay + pozisyon + görsel Telegram

Bu bir radar / sinyal botudur. Emir açmaz, kâr garanti etmez.
"""

from __future__ import annotations

import io
import os
import time
import math
import html
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

VERSION = "18.0"

DRY_RUN = os.getenv("CRYPTOJET_DRYRUN", "").strip().lower() in {"1", "true", "yes"}
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN and not DRY_RUN:
    raise ValueError("TELEGRAM_BOT_TOKEN yok. Test: export CRYPTOJET_DRYRUN=1")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
CB_ADV = "https://api.coinbase.com/api/v3/brokerage"
COINGECKO_API = os.getenv("COINGECKO_API", "https://api.coingecko.com/api/v3")
FNG_API = "https://api.alternative.me/fng/?limit=1"
COINGECKO_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
CG_SLEEP = float(os.getenv("CG_SLEEP", "1.15"))

session = requests.Session()
session.headers.update({"User-Agent": f"CryptoJet/{VERSION}", "Accept": "application/json"})
if COINGECKO_KEY:
    session.headers["x-cg-demo-api-key"] = COINGECKO_KEY
    session.headers["x-cg-pro-api-key"] = COINGECKO_KEY

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180"))
PRODUCT_REFRESH = 1800
ALERT_COOLDOWN = 1800
MAX_SCAN_COINS = int(os.getenv("MAX_SCAN_COINS", "55"))
MIN_QUOTE_VOLUME_USD = float(os.getenv("MIN_QUOTE_VOLUME_USD", "2500000"))
AUTO_MIN_LEVEL = os.getenv("AUTO_MIN_LEVEL", "ALARM")

# 15m early
TICK = 0.0010          # %0.10
MICRO_MIN = 0.0018     # %0.18
MICRO_MAX = 0.0120     # %1.20
BODY_MIN = 0.38
RVOL_OK, RVOL_STRONG = 1.40, 2.00

WATCH_MIN, ALARM_MIN, STRONG_MIN = 500, 630, 750
ELITE_MIN, EXTREME_MIN = 860, 925

GRAN = {
    "15M": "FIFTEEN_MINUTE",
    "1H": "ONE_HOUR",
    "2H": "TWO_HOUR",
    "4H": "FOUR_HOUR",
    "1D": "ONE_DAY",
}
GRAN_SEC = {"15M": 900, "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400}
CANDLE_TTL = {"15M": 45, "1H": 90, "2H": 180, "4H": 240, "1D": 600}
LEVEL_RANK = {"NONE": 0, "WATCH": 1, "ALARM": 2, "STRONG": 3, "ELITE": 4, "EXTREME": 5}

active_chat_id: Optional[int] = None
last_scan_time = 0.0
last_product_refresh = 0.0
products_cache: List[Dict] = []
alert_state: Dict[str, Dict] = {}
candle_cache: Dict[Tuple[str, str], Tuple[float, List[Dict]]] = {}
chart_cache: Dict[str, Tuple[float, dict]] = {}


def safe_float(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fmt_price(p) -> str:
    if p is None or p <= 0:
        return "—"
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    return f"${p:,.8f}"


def unix_now() -> int:
    return int(time.time())


# ===================== Telegram =====================

def _post_tg(method: str, data=None, files=None) -> bool:
    if DRY_RUN:
        print(f"[DRYRUN] {method}")
        return True
    if not TOKEN:
        return False
    try:
        r = session.post(f"{TELEGRAM_API}/{method}", data=data, files=files, timeout=30)
        if r.status_code != 200:
            print("TG", method, r.status_code, r.text[:160])
        return r.status_code == 200
    except Exception as e:
        print("TG hata:", e)
        return False


def send_message(chat_id, text: str, html_mode: bool = True) -> bool:
    if DRY_RUN:
        print("\n===== MSG =====\n" + text[:1600] + "\n===============\n")
        return True
    if not chat_id:
        return False
    payload = {"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True}
    if html_mode:
        payload["parse_mode"] = "HTML"
    return _post_tg("sendMessage", data=payload)


def send_photo(chat_id, png: bytes, caption: str) -> bool:
    if DRY_RUN:
        print(f"[DRYRUN] photo {len(png)}")
        return True
    if not chat_id:
        return False
    return _post_tg(
        "sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
        files={"photo": ("chart.png", png, "image/png")},
    )


def send_animation(chat_id, gif: bytes, caption: str) -> bool:
    if DRY_RUN:
        print(f"[DRYRUN] gif {len(gif)}")
        return True
    if not chat_id:
        return False
    return _post_tg(
        "sendAnimation",
        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
        files={"animation": ("jet.gif", gif, "image/gif")},
    )


# ===================== Data (CoinGecko ana, Coinbase yedek) =====================

_cg_last = 0.0
SKIP_SYM = {"USD", "USDC", "USDT", "DAI", "EUR", "GBP", "PYUSD", "FDUSD", "TUSD", "USDE", "USDS"}
STABLE_IDS = {"tether", "usd-coin", "binance-usd", "dai", "first-digital-usd", "true-usd", "ethena-usde"}
GECKO_ALIAS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2", "LINK": "chainlink",
    "DOT": "polkadot", "LTC": "litecoin", "BCH": "bitcoin-cash", "SUI": "sui",
    "NEAR": "near", "UNI": "uniswap", "AAVE": "aave", "PEPE": "pepe",
}


def cg_get(path: str, params=None):
    global _cg_last
    url = COINGECKO_API + path
    for attempt in range(4):
        wait = CG_SLEEP - (time.time() - _cg_last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = session.get(url, params=params, timeout=18)
            _cg_last = time.time()
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(8 + attempt * 4)
                continue
            print(f"CG {r.status_code} {path}: {r.text[:120]}")
        except Exception as e:
            print("CG", e)
            time.sleep(2)
    return None


def adv_get(path: str, params=None):
    for attempt in range(3):
        try:
            r = session.get(CB_ADV + path, params=params, timeout=16)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(1.6 * (attempt + 1))
                continue
        except Exception as e:
            print("CB", e)
            time.sleep(1.0)
    return None


def _bucket_candles(prices, volumes, bucket: int) -> List[Dict]:
    bars: Dict[int, Dict] = {}
    for ts_ms, px in prices or []:
        t = int(ts_ms / 1000)
        b = t - (t % bucket)
        px = safe_float(px)
        if px <= 0:
            continue
        bar = bars.get(b)
        if not bar:
            bars[b] = {"time": float(b), "open": px, "high": px, "low": px, "close": px, "volume": 0.0}
        else:
            bar["high"] = max(bar["high"], px)
            bar["low"] = min(bar["low"], px)
            bar["close"] = px
    for ts_ms, vol in volumes or []:
        t = int(ts_ms / 1000)
        b = t - (t % bucket)
        if b in bars:
            bars[b]["volume"] += safe_float(vol)
    return [bars[k] for k in sorted(bars)]


def _ohlc_rows(rows) -> List[Dict]:
    out = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        c = {
            "time": safe_float(row[0]) / 1000.0,
            "open": safe_float(row[1]),
            "high": safe_float(row[2]),
            "low": safe_float(row[3]),
            "close": safe_float(row[4]),
            "volume": 0.0,
        }
        if c["close"] > 0:
            out.append(c)
    out.sort(key=lambda x: x["time"])
    return out


def _group_daily(c4h: List[Dict]) -> List[Dict]:
    days: Dict[int, Dict] = {}
    for c in c4h:
        day = int(c["time"] - (c["time"] % 86400))
        d = days.get(day)
        if not d:
            days[day] = {
                "time": float(day), "open": c["open"], "high": c["high"],
                "low": c["low"], "close": c["close"], "volume": c["volume"],
            }
        else:
            d["high"] = max(d["high"], c["high"])
            d["low"] = min(d["low"], c["low"])
            d["close"] = c["close"]
            d["volume"] += c["volume"]
    return [days[k] for k in sorted(days)]


def _cb_fallback(symbol: str, tf: str, limit: int) -> List[Dict]:
    pid = f"{symbol}-USD"
    gran = GRAN.get(tf)
    if not gran:
        return []
    sec = GRAN_SEC[tf]
    end, start = unix_now(), unix_now() - sec * (limit + 8)
    data = adv_get(
        f"/market/products/{pid}/candles",
        {"granularity": gran, "start": str(start), "end": str(end), "limit": min(limit + 8, 350)},
    )
    rows = data.get("candles") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    candles = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        c = {
            "time": safe_float(row.get("start")),
            "low": safe_float(row.get("low")),
            "high": safe_float(row.get("high")),
            "open": safe_float(row.get("open")),
            "close": safe_float(row.get("close")),
            "volume": safe_float(row.get("volume")),
        }
        if c["close"] > 0:
            candles.append(c)
    candles.sort(key=lambda x: x["time"])
    return candles[-limit:]


def get_products(force: bool = False) -> List[Dict]:
    global products_cache, last_product_refresh
    now = time.time()
    if products_cache and not force and now - last_product_refresh < PRODUCT_REFRESH:
        return products_cache
    raw = cg_get("/coins/markets", {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": min(MAX_SCAN_COINS + 15, 100),
        "page": 1,
        "price_change_percentage": "1h,24h",
    })
    if not isinstance(raw, list):
        print("CoinGecko markets alınamadı, Coinbase yedek.")
        data = adv_get("/market/products", {"product_type": "SPOT", "limit": 1000})
        raw_cb = data.get("products") if isinstance(data, dict) else []
        out = []
        for p in raw_cb or []:
            base = p.get("base_currency_id") or ""
            if p.get("quote_currency_id") != "USD" or base in SKIP_SYM:
                continue
            if p.get("status") != "online" or p.get("trading_disabled"):
                continue
            price = safe_float(p.get("price"))
            qv = price * safe_float(p.get("volume_24h"))
            if qv < MIN_QUOTE_VOLUME_USD:
                continue
            out.append({
                "id": GECKO_ALIAS.get(base, base.lower()),
                "base": base,
                "price": price,
                "quote_vol": qv,
                "chg_24h": safe_float(p.get("price_percentage_change_24h")),
            })
        products_cache = sorted(out, key=lambda x: x["quote_vol"], reverse=True)[:MAX_SCAN_COINS]
        last_product_refresh = now
        return products_cache

    out = []
    for p in raw:
        try:
            gid = p.get("id") or ""
            base = (p.get("symbol") or "").upper()
            if gid in STABLE_IDS or base in SKIP_SYM:
                continue
            qv = safe_float(p.get("total_volume"))
            if qv < MIN_QUOTE_VOLUME_USD:
                continue
            out.append({
                "id": gid,
                "base": base,
                "price": safe_float(p.get("current_price")),
                "quote_vol": qv,
                "chg_24h": safe_float(p.get("price_change_percentage_24h")),
                "chg_1h": safe_float(p.get("price_change_percentage_1h_in_currency")),
            })
        except Exception:
            continue
    products_cache = out[:MAX_SCAN_COINS]
    last_product_refresh = now
    print(f"Likit coin (CoinGecko): {len(products_cache)}")
    return products_cache


def resolve_id(product_id: str) -> Tuple[str, str]:
    """return (gecko_id, base_symbol)"""
    pid = product_id.strip()
    if pid.endswith("-USD"):
        base = pid[:-4].upper()
        return GECKO_ALIAS.get(base, pid.lower()), base
    for p in products_cache:
        if p["id"] == pid or p["base"] == pid.upper():
            return p["id"], p["base"]
    if pid.upper() in GECKO_ALIAS:
        return GECKO_ALIAS[pid.upper()], pid.upper()
    return pid.lower(), pid.upper()


def get_candles(product_id: str, tf: str, limit: int = 80) -> List[Dict]:
    now = time.time()
    key = (product_id, tf)
    cached = candle_cache.get(key)
    if cached and now - cached[0] < CANDLE_TTL.get(tf, 90):
        return cached[1][-limit:]

    gid, base = resolve_id(product_id)
    candles: List[Dict] = []

    if tf in {"15M", "1H", "2H"}:
        packed = chart_cache.get(gid)
        if packed and now - packed[0] < 70:
            chart = packed[1]
        else:
            chart = cg_get(f"/coins/{gid}/market_chart", {"vs_currency": "usd", "days": "1"})
            if isinstance(chart, dict):
                chart_cache[gid] = (now, chart)
        if isinstance(chart, dict):
            candles = _bucket_candles(chart.get("prices"), chart.get("total_volumes"), GRAN_SEC[tf])
    elif tf == "4H":
        rows = cg_get(f"/coins/{gid}/ohlc", {"vs_currency": "usd", "days": "14"})
        candles = _ohlc_rows(rows)
    elif tf == "1D":
        rows = cg_get(f"/coins/{gid}/ohlc", {"vs_currency": "usd", "days": "30"})
        candles = _group_daily(_ohlc_rows(rows))

    if len(candles) < 8:
        fb = _cb_fallback(base, tf, limit)
        if fb:
            candles = fb

    candles = candles[-limit:]
    candle_cache[key] = (now, candles)
    return candles


# ===================== Teknik =====================

def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    res = sum(values[:period]) / period
    for v in values[period:]:
        res = (v - res) * k + res
    return res


def ema_series(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    res = sum(values[:period]) / period
    out[period - 1] = res
    for i in range(period, len(values)):
        res = (values[i] - res) * k + res
        out[i] = res
    return out


def atr(candles: List[Dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"])))
    return sum(trs[-period:]) / period


def bollinger_width(candles: List[Dict], period: int = 20) -> Optional[float]:
    if len(candles) < period:
        return None
    closes = [c["close"] for c in candles[-period:]]
    mid = sum(closes) / period
    std = math.sqrt(sum((x - mid) ** 2 for x in closes) / period)
    return (2 * std) / mid if mid > 0 else None


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(abs(min(d, 0.0)))
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def directional_strength(candles: List[Dict], lookback: int = 20) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    seq = candles[-lookback:]
    net = abs(seq[-1]["close"] - seq[0]["close"])
    path = sum(abs(seq[i]["close"] - seq[i - 1]["close"]) for i in range(1, len(seq)))
    return clamp(net / path, 0, 1) if path > 0 else 0.0


def relative_volume(candles: List[Dict], lookback: int = 16) -> float:
    if len(candles) < lookback + 1:
        return 1.0
    avg = sum(c["volume"] for c in candles[-lookback - 1:-1]) / lookback
    return candles[-1]["volume"] / avg if avg > 0 else 1.0


def body_ratio(c: Dict) -> float:
    rng = c["high"] - c["low"]
    if rng <= 0:
        return 0.0
    return abs(c["close"] - c["open"]) / rng


def last_change(candles: List[Dict]) -> float:
    if len(candles) < 2 or candles[-2]["close"] <= 0:
        return 0.0
    return (candles[-1]["close"] - candles[-2]["close"]) / candles[-2]["close"]


def get_tf_bias(candles: List[Dict]) -> str:
    if len(candles) < 26:
        return "NEUTRAL"
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50) if len(closes) >= 50 else None
    px = closes[-1]
    if e20 is None:
        return "NEUTRAL"
    if e50 is not None:
        if px > e20 > e50:
            return "LONG"
        if px < e20 < e50:
            return "SHORT"
    if px > e20:
        return "LONG"
    if px < e20:
        return "SHORT"
    return "NEUTRAL"


def swing_levels(candles: List[Dict], lookback: int = 24) -> Dict:
    if len(candles) < 8:
        return {"high": None, "low": None}
    w = candles[-lookback:-1] if len(candles) > lookback else candles[:-1]
    if not w:
        return {"high": None, "low": None}
    return {"high": max(c["high"] for c in w), "low": min(c["low"] for c in w)}


def structure_bias(candles: List[Dict]) -> str:
    if len(candles) < 20:
        return "NEUTRAL"
    mid = len(candles) // 2
    h1, h2 = max(c["high"] for c in candles[:mid]), max(c["high"] for c in candles[mid:])
    l1, l2 = min(c["low"] for c in candles[:mid]), min(c["low"] for c in candles[mid:])
    if h2 > h1 and l2 > l1:
        return "LONG"
    if h2 < h1 and l2 < l1:
        return "SHORT"
    return "NEUTRAL"


def calc_compression(candles: List[Dict]) -> Dict:
    empty = {"score": 0.0, "bb_width": None, "atr_ratio": None, "range_pct": None}
    if len(candles) < 32:
        return empty
    bb = bollinger_width(candles, 20)
    a1, a0 = atr(candles[-16:], 14), atr(candles[-30:-16], 14)
    atr_ratio = (a1 / a0) if a0 and a0 > 0 else 1.0
    recent = candles[-12:]
    hi, lo = max(c["high"] for c in recent), min(c["low"] for c in recent)
    mid = (hi + lo) / 2
    range_pct = (hi - lo) / mid if mid > 0 else 1
    score = 0.0
    if bb is not None:
        score += 42 if bb < 0.025 else 33 if bb < 0.036 else 22 if bb < 0.048 else 12 if bb < 0.062 else 0
    score += 32 if atr_ratio < 0.75 else 23 if atr_ratio < 0.85 else 13 if atr_ratio < 0.94 else 0
    score += 26 if range_pct < 0.018 else 19 if range_pct < 0.028 else 11 if range_pct < 0.040 else 0
    return {"score": clamp(score, 0, 100), "bb_width": bb, "atr_ratio": atr_ratio, "range_pct": range_pct}


def detect_early(c15: List[Dict]) -> Dict:
    empty = {
        "active": False, "early": False, "direction": None, "pct": 0.0,
        "rvol": 1.0, "volume_ok": False, "breakout": False,
        "body": 0.0, "body_ok": False,
    }
    if len(c15) < 12:
        return empty
    last = c15[-1]
    chg = last_change(c15)
    direction = "LONG" if chg > 0 else "SHORT" if chg < 0 else None
    rvol = relative_volume(c15, 16)
    body = body_ratio(last)
    sw = swing_levels(c15, 16)
    breakout = False
    if direction == "LONG" and sw["high"] and last["close"] > sw["high"] and last["close"] >= last["open"]:
        breakout = True
    if direction == "SHORT" and sw["low"] and last["close"] < sw["low"] and last["close"] <= last["open"]:
        breakout = True
    abs_m = abs(chg)
    return {
        "active": MICRO_MIN <= abs_m <= MICRO_MAX,
        "early": TICK <= abs_m < MICRO_MIN,
        "direction": direction if abs_m >= TICK or breakout else None,
        "pct": abs_m * 100,
        "rvol": rvol,
        "volume_ok": rvol >= RVOL_OK,
        "breakout": breakout,
        "body": body,
        "body_ok": body >= BODY_MIN,
    }


# ===================== Context =====================

def get_fear_greed() -> Dict:
    try:
        r = session.get(FNG_API, timeout=8)
        if r.status_code == 200:
            row = r.json().get("data", [{}])[0]
            return {"value": int(safe_float(row.get("value"), 50)), "label": row.get("value_classification", "Neutral")}
    except Exception as e:
        print("FNG", e)
    return {"value": 50, "label": "Neutral"}


def get_market_context() -> Dict:
    btc_price = btc_change = 0.0
    dominance = 52.0
    regime, btc_trend = "RANGE", "NEUTRAL"
    try:
        btc = cg_get("/coins/bitcoin", {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false", "sparkline": "false"})
        md = (btc or {}).get("market_data") or {}
        btc_price = safe_float((md.get("current_price") or {}).get("usd"))
        btc_change = safe_float(md.get("price_change_percentage_24h"))
    except Exception as e:
        print("BTC", e)
    if btc_price <= 0:
        for p in get_products():
            if p["id"] == "bitcoin" or p["base"] == "BTC":
                btc_price = p["price"]
                btc_change = p.get("chg_24h", 0.0)
                break

    c15 = get_candles("bitcoin", "15M", 40)
    c4h = get_candles("bitcoin", "4H", 60)
    c1d = get_candles("bitcoin", "1D", 50)
    btc_15 = last_change(c15) * 100 if len(c15) >= 2 else 0.0

    if len(c4h) >= 30:
        btc_trend = get_tf_bias(c4h)
        ds = directional_strength(c4h)
        bb = bollinger_width(c4h, 20)
        if bb is not None and bb < 0.035 and ds < 0.22:
            regime = "SQUEEZE"
        elif ds < 0.18:
            regime = "RANGE"
        elif btc_trend == "LONG" and btc_change > 0:
            regime = "BULL"
        elif btc_trend == "SHORT" and btc_change < 0:
            regime = "BEAR"
        else:
            regime = "TRANSITION"

    try:
        r = session.get(f"{COINGECKO_API}/global", timeout=10)
        if r.status_code == 200:
            dominance = safe_float(r.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 52))
    except Exception as e:
        print("DOM", e)

    if dominance >= 56:
        dom_bias = "BEARISH_ALT"
    elif dominance <= 48:
        dom_bias = "BULLISH_ALT"
    else:
        dom_bias = "NEUTRAL"

    fng = get_fear_greed()
    return {
        "btc_price": btc_price,
        "btc_change": btc_change,
        "btc_15m": btc_15,
        "regime": regime,
        "btc_trend": btc_trend,
        "dominance": dominance,
        "dom_bias": dom_bias,
        "btc_1d_bias": get_tf_bias(c1d) if len(c1d) >= 25 else "NEUTRAL",
        "fng": fng["value"],
        "fng_label": fng["label"],
    }


# ===================== Analiz =====================

@dataclass
class Result:
    product: Dict
    price: float
    decision: str
    score: int
    level: str
    position: str
    confidence: int
    invalidation: Optional[float]
    compression: Dict
    early: Dict
    tf_bias: Dict
    market: Dict
    checks: List[Tuple[str, bool]]
    parts: Dict = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    c15: List[Dict] = field(default_factory=list)
    c1h: List[Dict] = field(default_factory=list)
    swings4h: Dict = field(default_factory=dict)
    rel_btc: float = 0.0


def analyze(product: Dict, market: Dict) -> Optional[Result]:
    c15 = get_candles(product["id"], "15M", 50)
    c1h = get_candles(product["id"], "1H", 70)
    if len(c15) < 20 or len(c1h) < 36:
        return None
    c2h = get_candles(product["id"], "2H", 45)
    c4h = get_candles(product["id"], "4H", 55)
    c1d = get_candles(product["id"], "1D", 40)

    price = c15[-1]["close"]
    early = detect_early(c15)
    comp1 = calc_compression(c1h)
    comp4 = calc_compression(c4h) if len(c4h) >= 32 else {"score": 0.0}
    swings4h = swing_levels(c4h, 20) if len(c4h) >= 10 else {"high": None, "low": None}
    struct4 = structure_bias(c4h) if len(c4h) >= 20 else "NEUTRAL"

    bias = {
        "15M": get_tf_bias(c15),
        "1H": get_tf_bias(c1h),
        "2H": get_tf_bias(c2h) if len(c2h) >= 26 else "NEUTRAL",
        "4H": get_tf_bias(c4h) if len(c4h) >= 30 else "NEUTRAL",
        "1D": get_tf_bias(c1d) if len(c1d) >= 25 else "NEUTRAL",
    }

    coin_15 = last_change(c15) * 100
    rel_btc = coin_15 - market.get("btc_15m", 0.0)
    rsi_v = rsi([c["close"] for c in c1h], 14)
    rvol = safe_float(early.get("rvol"), 1.0)

    direction = early.get("direction")
    votes = [bias[k] for k in ("1H", "2H", "4H", "1D") if bias[k] != "NEUTRAL"]
    if not direction:
        if votes.count("LONG") >= 3:
            direction = "LONG"
        elif votes.count("SHORT") >= 3:
            direction = "SHORT"

    reasons, parts = [], {}
    parts["compression"] = comp1["score"] * 1.4 + comp4.get("score", 0) * 0.8
    if comp1["score"] >= 65:
        reasons.append(f"1H sıkışma {comp1['score']:.0f}")
    if comp4.get("score", 0) >= 55:
        reasons.append(f"4H sıkışma {comp4['score']:.0f}")

    ev = 0.0
    if early["active"]:
        ev += 95
        reasons.append(f"15m hareket %{early['pct']:.2f} {early.get('direction')}")
    elif early.get("early"):
        ev += 50
        reasons.append(f"15m tick %{early['pct']:.2f}")
    if early.get("breakout") and early.get("body_ok"):
        ev += 70
        reasons.append("15m gövdeli kırılım")
    elif early.get("breakout"):
        ev += 18
        reasons.append("15m fitil — zayıf")
    if early.get("volume_ok"):
        ev += 28
    if direction == "LONG" and rel_btc >= 0.15:
        ev += 20
        reasons.append(f"BTC'den güçlü ({rel_btc:+.2f}%)")
    elif direction == "SHORT" and rel_btc <= -0.15:
        ev += 20
        reasons.append(f"BTC'den zayıf ({rel_btc:+.2f}%)")
    parts["early"] = clamp(ev, 0, 220)
    parts["volume"] = 140 if rvol >= 2.5 else 110 if rvol >= RVOL_STRONG else 80 if rvol >= RVOL_OK else 30 if rvol >= 1.1 else 8

    mom = 40
    if rsi_v is not None:
        if 48 <= rsi_v <= 68:
            mom += 28
        elif rsi_v > 78 or rsi_v < 24:
            mom -= 22
    parts["momentum"] = clamp(mom, 0, 120)

    tf_pts = 0
    if direction:
        same = sum(1 for v in votes if v == direction)
        opp = sum(1 for v in votes if v != direction)
        tf_pts = same * 38 - opp * 30
        if bias["15M"] == direction:
            tf_pts += 15
        if struct4 == direction:
            tf_pts += 22
        elif struct4 not in ("NEUTRAL", direction):
            tf_pts -= 22
    parts["mtf"] = clamp(tf_pts, 0, 210)

    ctx = 48
    if direction == "LONG":
        if market["regime"] == "BULL":
            ctx += 28
        if market["btc_trend"] == "LONG":
            ctx += 16
        if market["dom_bias"] == "BULLISH_ALT":
            ctx += 16
        if market["fng"] <= 25 and market["regime"] != "BEAR":
            ctx += 8
        if market["regime"] == "BEAR" or market["btc_change"] < -1.8:
            ctx -= 48
            reasons.append("BTC long aleyhine")
        if market["dom_bias"] == "BEARISH_ALT":
            ctx -= 16
            reasons.append("BTC.D yüksek")
        if market["fng"] >= 80:
            ctx -= 10
            reasons.append("Aşırı greed")
    elif direction == "SHORT":
        if market["regime"] == "BEAR":
            ctx += 28
        if market["btc_trend"] == "SHORT":
            ctx += 16
        if market["regime"] == "BULL" and market["btc_change"] > 1.8:
            ctx -= 48
            reasons.append("BTC short aleyhine")
        if market["fng"] >= 75:
            ctx += 8
    parts["context"] = clamp(ctx, 0, 130)

    score = int(clamp(sum(parts.values()), 0, 1000))

    d = direction
    checks = [
        ("15m hareket", bool(early["active"] or early.get("early") or early.get("breakout"))),
        ("Gövde kaliteli", bool(early.get("body_ok"))),
        ("Hacim", rvol >= RVOL_OK),
        ("1H aynı yön", bias["1H"] == d and bool(d)),
        ("4H aynı yön", bias["4H"] == d and bool(d)),
        ("4H yapı uygun", struct4 in (d, "NEUTRAL") and bool(d)),
        ("Sıkışma/enerji", comp1["score"] >= 42 or comp4.get("score", 0) >= 42),
        ("BTC rejim uygun", not (
            (d == "LONG" and (market["regime"] == "BEAR" or market["btc_change"] < -2.2))
            or (d == "SHORT" and market["regime"] == "BULL" and market["btc_change"] > 2.2)
        )),
        ("BTC.D altcoin için", not (d == "LONG" and market["dom_bias"] == "BEARISH_ALT" and market["dominance"] >= 58)),
        ("Göreli güç", bool(d == "LONG" and rel_btc >= 0) or bool(d == "SHORT" and rel_btc <= 0) or abs(rel_btc) < 0.08),
    ]
    passed = sum(1 for _, ok in checks if ok)
    confidence = int(round(100 * passed / len(checks)))

    if score >= EXTREME_MIN:
        level = "EXTREME"
    elif score >= ELITE_MIN:
        level = "ELITE"
    elif score >= STRONG_MIN:
        level = "STRONG"
    elif score >= ALARM_MIN:
        level = "ALARM"
    elif score >= WATCH_MIN:
        level = "WATCH"
    else:
        level = "NONE"

    if early["active"] and early.get("direction"):
        decision = early["direction"]
    elif early.get("breakout") and early.get("body_ok") and early.get("direction"):
        decision = early["direction"]
    elif passed >= 6 and d:
        decision = d
    elif (comp1["score"] >= 68 or early.get("early")) and score >= 450:
        decision = "WATCH"
    else:
        decision = "BEKLE"

    hard_fail = (
        not d
        or (d == "LONG" and market["regime"] == "BEAR" and market["btc_change"] < -2)
        or (d == "SHORT" and market["regime"] == "BULL" and market["btc_change"] > 2)
        or (early.get("breakout") and not early.get("body_ok") and rvol < RVOL_OK)
        or (bias["4H"] not in ("NEUTRAL", d) and passed < 6)
    )
    if decision == "BEKLE" or level == "NONE" or not d or hard_fail or passed <= 4:
        position = "UYGUN DEĞİL"
    elif passed >= 8 and level in {"STRONG", "ELITE", "EXTREME"} and early.get("body_ok") and rvol >= RVOL_OK:
        position = f"UYGUN {d}"
    elif passed >= 6 and level in {"ALARM", "STRONG", "ELITE", "EXTREME"}:
        position = f"KOŞULLU {d}"
    elif passed >= 5 and level in {"WATCH", "ALARM"}:
        position = f"KOŞULLU {d}"
    else:
        position = "UYGUN DEĞİL"

    inv = swings4h.get("low") if d == "LONG" else swings4h.get("high") if d == "SHORT" else None

    return Result(
        product=product, price=price, decision=decision, score=score, level=level,
        position=position, confidence=confidence, invalidation=inv,
        compression=comp1, early=early, tf_bias=bias, market=market,
        checks=checks, parts=parts, reasons=reasons,
        c15=c15, c1h=c1h, swings4h=swings4h, rel_btc=rel_btc,
    )


# ===================== Görsel =====================

def _font(size: int):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def make_chart(r: Result) -> Optional[bytes]:
    c = r.c15[-48:] if len(r.c15) >= 20 else r.c1h[-60:]
    if len(c) < 12:
        return None
    tf_name = "15M" if c is r.c15[-48:] or (r.c15 and c[0] is r.c15[-48:][0] if r.c15 else False) else "1H"
    if len(r.c15) >= 20:
        c, tf_name = r.c15[-48:], "15M"
    else:
        c, tf_name = r.c1h[-60:], "1H"
    closes = [x["close"] for x in c]
    e20, e50 = ema_series(closes, 20), ema_series(closes, 50)
    fig, axes = plt.subplots(2, 1, figsize=(8.2, 5.2), gridspec_kw={"height_ratios": [3.2, 1]}, facecolor="#0b0f14")
    ax, axv = axes
    ax.set_facecolor("#0b0f14")
    axv.set_facecolor("#0b0f14")
    for a in axes:
        a.tick_params(colors="#8b9bb4", labelsize=8)
        for s in a.spines.values():
            s.set_color("#243044")
    xs = list(range(len(c)))
    for i, bar in enumerate(c):
        col = "#3dd68c" if bar["close"] >= bar["open"] else "#f07178"
        ax.plot([i, i], [bar["low"], bar["high"]], color=col, lw=0.8)
        lo, hi = min(bar["open"], bar["close"]), max(bar["open"], bar["close"])
        ax.plot([i, i], [lo, hi], color=col, lw=3.0)
    ax.plot(xs, e20, color="#7aa2f7", lw=1.2, label="EMA20")
    ax.plot(xs, e50, color="#c0a6ff", lw=1.2, label="EMA50")
    if r.swings4h.get("high"):
        ax.axhline(r.swings4h["high"], color="#f07178", ls="--", lw=0.8, alpha=0.65)
    if r.swings4h.get("low"):
        ax.axhline(r.swings4h["low"], color="#3dd68c", ls="--", lw=0.8, alpha=0.65)
    pos = r.position.replace("UYGUN DEĞİL", "UYGUN DEGIL").replace("KOŞULLU", "KOSULLU")
    ax.set_title(f"{r.product['base']}-USD  {tf_name}  {pos}  {r.score}/1000", color="#e6edf7", fontsize=11, pad=8)
    ax.legend(facecolor="#0b0f14", edgecolor="#243044", labelcolor="#c8d3e8", fontsize=7)
    vols = [x["volume"] for x in c]
    vcol = ["#3dd68c" if x["close"] >= x["open"] else "#f07178" for x in c]
    axv.bar(xs, vols, color=vcol, width=0.7, alpha=0.85)
    axv.set_ylabel("Vol", color="#8b9bb4", fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def make_alert_gif(r: Result) -> bytes:
    w, h = 720, 380
    pos_col = "#3dd68c" if r.position.startswith("UYGUN ") else "#ffcc66" if r.position.startswith("KOŞULLU") else "#f07178"
    frames = []
    for i in range(10):
        t = i / 9
        img = Image.new("RGB", (w, h), "#070b10")
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([16, 16, w - 16, h - 16], 28, outline="#1e2a3c", width=2)
        d.ellipse([36, 36, 88, 88], fill=pos_col)
        d.text((108, 38), f"CRYPTO JET  V{VERSION}", font=_font(20), fill="#9fb3c8")
        d.text((108, 68), r.product["base"], font=_font(38), fill="#e8eef7")
        d.text((36, 120), r.position, font=_font(28), fill=pos_col)
        d.text((36, 162), f"{r.decision}   {r.level}   {r.score}/1000", font=_font(18), fill="#c5d0e0")
        bar_x, bar_y, bar_w, bar_h = 36, 220, 648, 26
        d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], 10, fill="#152033")
        fill_w = int(bar_w * (r.score / 1000) * t)
        d.rounded_rectangle([bar_x, bar_y, bar_x + max(fill_w, 8), bar_y + bar_h], 10, fill=pos_col)
        d.text((36, 268), f"Guven %{r.confidence}   Onay {sum(1 for _,ok in r.checks if ok)}/{len(r.checks)}   vsBTC {r.rel_btc:+.2f}%", font=_font(16), fill="#9fb3c8")
        d.text((36, 304), f"15m %{r.early.get('pct',0):.2f}   rVol {r.early.get('rvol',0):.2f}x", font=_font(16), fill="#9fb3c8")
        frames.append(img)
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=90, loop=0)
    return buf.getvalue()


def caption_html(r: Result) -> str:
    checks = "\n".join(("✅ " if ok else "❌ ") + html.escape(n) for n, ok in r.checks)
    inv = fmt_price(r.invalidation)
    return (
        f"<b>{html.escape(r.product['base'])}</b>  {html.escape(r.position)}\n"
        f"{html.escape(r.decision)} · {html.escape(r.level)} · <b>{r.score}</b>/1000 · güven %{r.confidence}\n"
        f"{html.escape(fmt_price(r.price))} · 15m %{r.early.get('pct',0):.2f} · rVol {r.early.get('rvol',0):.2f}x · vsBTC {r.rel_btc:+.2f}%\n"
        f"TF 15m {r.tf_bias['15M']} | 1H {r.tf_bias['1H']} | 2H {r.tf_bias['2H']} | 4H {r.tf_bias['4H']} | 1D {r.tf_bias['1D']}\n"
        f"BTC 24s {r.market['btc_change']:+.2f}% · 15m {r.market['btc_15m']:+.2f}% · {html.escape(r.market['regime'])}\n"
        f"D {r.market['dominance']:.1f}% · F&G {r.market['fng']} {html.escape(r.market['fng_label'])}\n"
        f"İptal 4H {html.escape(inv)}\n\n{checks}"
    )


def should_alert(r: Result) -> bool:
    if r.level == "NONE":
        return False
    key = (r.decision, r.level, r.position, r.score // 40)
    now = time.time()
    prev = alert_state.get(r.product["id"])
    if prev and prev.get("key") == key and now - prev["time"] < ALERT_COOLDOWN:
        return False
    alert_state[r.product["id"]] = {"key": key, "time": now}
    return True


def push_visual(chat_id, r: Result):
    send_animation(chat_id, make_alert_gif(r), caption_html(r))
    chart = make_chart(r)
    if chart:
        send_photo(
            chat_id, chart,
            f"<b>{html.escape(r.product['base'])}</b> 15m · {html.escape(r.position)} · iptal {html.escape(fmt_price(r.invalidation))}",
        )
        if DRY_RUN:
            open(f"/home/workdir/artifacts/{r.product['base']}_v17.png", "wb").write(chart)
            open(f"/home/workdir/artifacts/{r.product['base']}_v17.gif", "wb").write(make_alert_gif(r))


# ===================== Scan / komut =====================

def market_scan(chat_id=None, send_alerts=False, only=None) -> List[Result]:
    products = get_products()
    if only:
        want = {x.upper() for x in only}
        products = [p for p in products if p["base"] in want] or [
            {"id": f"{s}-USD", "base": s, "quote_vol": 0, "price": 0, "chg_24h": 0} for s in want
        ]
    if not products:
        if chat_id:
            send_message(chat_id, "Coin listesi yok.", html_mode=False)
        return []
    market = get_market_context()
    results, strong = [], []
    print(
        f"Tarama {len(products)} | BTC {market['btc_change']:+.2f}% 15m {market['btc_15m']:+.2f}% | "
        f"{market['regime']} | D {market['dominance']:.1f} | F&G {market['fng']}"
    )
    for i, p in enumerate(products, 1):
        try:
            r = analyze(p, market)
            if not r:
                continue
            results.append(r)
            if r.level != "NONE":
                strong.append(r)
            print(f"[{i:3d}/{len(products)}] {p['base']:<8} {r.decision:<6} {r.score:4d} {r.level:<8} {r.position}")
            time.sleep(0.03)
        except Exception as e:
            print("analiz", p.get("base"), e)
    results.sort(key=lambda x: x.score, reverse=True)
    strong.sort(key=lambda x: x.score, reverse=True)
    min_rank = LEVEL_RANK.get(AUTO_MIN_LEVEL, 2)
    if chat_id and send_alerts:
        for r in strong:
            if LEVEL_RANK.get(r.level, 0) >= min_rank and should_alert(r):
                push_visual(chat_id, r)
    if chat_id:
        lines = [
            f"<b>CRYPTO JET V{VERSION}</b>",
            f"Coin {len(results)} · sinyal {len(strong)}",
            f"BTC {market['btc_change']:+.2f}% · 15m {market['btc_15m']:+.2f}% · {html.escape(market['regime'])}",
            f"D {market['dominance']:.1f}% · F&amp;G {market['fng']} {html.escape(market['fng_label'])}",
            "",
        ]
        for r in (strong[:15] if strong else results[:10]):
            lines.append(f"<code>{html.escape(r.product['base']):<6}</code> {r.score:>4}  {html.escape(r.position)}")
        send_message(chat_id, "\n".join(lines))
    return results


def handle_command(chat_id: int, text: str):
    global active_chat_id, last_scan_time
    t = text.strip().lower()
    if t == "/start":
        send_message(chat_id, f"""
<b>CRYPTO JET V{VERSION}</b>

15m erken hareket + 1H/4H/1D onay
BTC · BTC.D · Fear&amp;Greed · göreli güç

/jet otomatik (ALARM+)
/scan  /top  /btc  /status  /stop

Pozisyon: UYGUN · KOŞULLU · UYGUN DEĞİL
Emir tavsiyesi değildir.
""".strip())
    elif t == "/jet":
        active_chat_id = chat_id
        send_message(chat_id, "V17 açık. Tarama başlıyor.")
        market_scan(chat_id, True)
        last_scan_time = time.time()
    elif t == "/scan":
        send_message(chat_id, "Tarama başladı.")
        market_scan(chat_id, True)
        last_scan_time = time.time()
    elif t == "/top":
        results = market_scan(None, False)
        if not results:
            send_message(chat_id, "Sonuç yok.", html_mode=False)
            return
        lines = [f"<b>TOP V{VERSION}</b>"]
        for r in results[:20]:
            lines.append(f"{html.escape(r.product['base']):<6} {r.score:4d}  {html.escape(r.position)}")
        send_message(chat_id, "\n".join(lines))
    elif t == "/btc":
        m = get_market_context()
        send_message(chat_id, f"""
<b>MARKET</b>
BTC {html.escape(fmt_price(m['btc_price']))}  24s {m['btc_change']:+.2f}%  15m {m['btc_15m']:+.2f}%
Trend {html.escape(m['btc_trend'])} · 1D {html.escape(m['btc_1d_bias'])}
Rejim {html.escape(m['regime'])}
BTC.D {m['dominance']:.2f}%  {html.escape(m['dom_bias'])}
Fear&amp;Greed {m['fng']}  {html.escape(m['fng_label'])}
""".strip())
    elif t == "/status":
        send_message(chat_id, f"V{VERSION} {'AÇIK' if active_chat_id else 'KAPALI'}\nCoin {len(get_products())}\nMin {AUTO_MIN_LEVEL}")
    elif t == "/stop":
        active_chat_id = None
        send_message(chat_id, "Durdu.")
    elif t.startswith("/"):
        send_message(chat_id, "/jet /scan /top /btc /status /stop")


def automatic_scan():
    global last_scan_time
    if not active_chat_id:
        return
    now = time.time()
    if now - last_scan_time < SCAN_INTERVAL:
        return
    last_scan_time = now
    results = market_scan(None, False)
    min_rank = LEVEL_RANK.get(AUTO_MIN_LEVEL, 2)
    for r in results:
        if LEVEL_RANK.get(r.level, 0) >= min_rank and should_alert(r):
            push_visual(active_chat_id, r)


def main():
    print(f"CRYPTO JET V{VERSION}")
    if DRY_RUN:
        rs = market_scan(chat_id=1, send_alerts=True, only=["BTC", "ETH", "SOL", "XRP", "DOGE"])
        for r in rs:
            if r.level != "NONE":
                push_visual(1, r)
        return
    offset = 0
    while True:
        try:
            r = session.get(f"{TELEGRAM_API}/getUpdates", params={"offset": offset, "timeout": 10}, timeout=15)
            if r.status_code != 200:
                time.sleep(5)
                continue
            data = r.json()
            if not data.get("ok"):
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if msg.get("text"):
                    handle_command(msg["chat"]["id"], msg["text"])
            automatic_scan()
            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("loop", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
