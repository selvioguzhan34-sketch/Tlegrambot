import os
import time
import math
import json
import threading
import requests
from collections import defaultdict, deque

# =========================================================
# CRYPTO JET V14.0 — EARLY MOVE ENGINE
#
# LIVE WebSocket + 1m local engine + 1H/2H/4H/1D MTF
# Compression / Momentum / Volume / Relative Strength
# Breakout + fake-breakout filter + Risk / Quality
# SMART STAGE ALERTS, SCORE: 0-1000
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"

# Full MTF refresh remains deliberately slow; live engine is fast.
SCAN_INTERVAL = 600
PRODUCT_REFRESH = 1800
CANDLE_TTL = 300
BTC_CONTEXT_TTL = 300
ALERT_COOLDOWN = 3600
MIN_CANDLES = 55
BTC_D_PROXY_COINS = 30
LIVE_LOOP_INTERVAL = 15
LIVE_HISTORY_MAX = 120
LIVE_WARMUP_SECONDS = 75

ALARM = 700
STRONG = 800
ELITE = 900
EXTREME = 950
ULTRA = 990
MAXIMUM = 1000

# True 1000-point calibration.
WEIGHTS = {
    "mtf": 250,
    "btc": 120,
    "market": 100,
    "relative": 100,
    "momentum": 120,
    "volume": 90,
    "compression": 80,
    "breakout": 80,
    "risk": 60,
}
assert sum(WEIGHTS.values()) == 1000

TF_WEIGHTS = {"1H": 160, "2H": 190, "4H": 250, "1D": 250}

active_chat_id = None
last_scan_time = 0
last_product_refresh = 0
products_cache = []
candle_cache = {}
btc_context_cache = {"time": 0, "data": None}
alert_state = {}

# product_id -> rolling live observations: (timestamp, price, trade_size)
live_state = defaultdict(lambda: {"ticks": deque(maxlen=LIVE_HISTORY_MAX), "last": 0.0, "last_time": 0.0})
live_lock = threading.RLock()
ws_thread = None
ws_stop = threading.Event()
ws_connected = False
ws_products_signature = ""

session = requests.Session()
session.headers.update({"User-Agent": "Crypto-Jet/14.0"})


def safe_float(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def pct(a, b):
    if not b:
        return 0.0
    return (a / b - 1.0) * 100.0


def send_message(chat_id, text):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    try:
        r = session.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
        return r.ok
    except Exception as e:
        print("Telegram error:", e)
        return False


def coinbase_get(path, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(COINBASE_API + path, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            print("Coinbase", r.status_code, path)
            return None
        except Exception as e:
            print("Coinbase request error:", e)
            time.sleep(1.2 * (attempt + 1))
    return None


def get_products(force=False):
    global products_cache, last_product_refresh
    now = time.time()
    if products_cache and not force and now - last_product_refresh < PRODUCT_REFRESH:
        return products_cache
    data = coinbase_get("/products")
    if not isinstance(data, list):
        return products_cache
    products = []
    for p in data:
        if p.get("quote_currency") != "USD":
            continue
        if p.get("status") not in (None, "online"):
            continue
        pid = p.get("id")
        if pid and not any(x["id"] == pid for x in products):
            products.append(p)
    products_cache = products
    last_product_refresh = now
    return products_cache


def parse_candles(rows):
    out = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            out.append({
                "time": int(row[0]), "low": safe_float(row[1]), "high": safe_float(row[2]),
                "open": safe_float(row[3]), "close": safe_float(row[4]), "volume": safe_float(row[5]),
            })
        except Exception:
            pass
    return sorted(out, key=lambda x: x["time"])


def get_candles(product_id, granularity=3600, limit=250, force=False):
    key = (product_id, granularity)
    now = time.time()
    cached = candle_cache.get(key)
    if cached and not force and now - cached["time"] < CANDLE_TTL:
        return cached["data"]
    seconds = granularity * min(max(limit, 60), 290)
    end = int(time.time())
    start = end - seconds
    data = coinbase_get(f"/products/{product_id}/candles", {"granularity": granularity, "start": start, "end": end})
    parsed = parse_candles(data)
    if len(parsed) >= min(MIN_CANDLES, limit // 2):
        candle_cache[key] = {"time": now, "data": parsed[-limit:]}
        return candle_cache[key]["data"]
    if cached:
        return cached["data"]
    return []


def aggregate_candles(candles, hours):
    if not candles:
        return []
    step = hours * 3600
    groups = defaultdict(list)
    for c in candles:
        groups[(c["time"] // step) * step].append(c)
    out = []
    for bucket in sorted(groups):
        g = sorted(groups[bucket], key=lambda x: x["time"])
        expected = set(range(bucket, bucket + step, 3600))
        actual = {int(x["time"]) for x in g}
        if not expected.issubset(actual):
            continue
        out.append({"time": bucket, "open": g[0]["open"], "high": max(x["high"] for x in g),
                    "low": min(x["low"] for x in g), "close": g[-1]["close"],
                    "volume": sum(x["volume"] for x in g)})
    return out


def get_timeframe_candles(product_id, force=False):
    h1 = get_candles(product_id, 3600, 250, force=force)
    d1 = get_candles(product_id, 86400, 120, force=force)
    return {"1H": h1, "2H": aggregate_candles(h1, 2), "4H": aggregate_candles(h1, 4), "1D": d1}


def closes(c): return [x["close"] for x in c]
def volumes(c): return [x["volume"] for x in c]


def ema(values, period):
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    out = [e]
    for v in values[period:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return []
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period; al = sum(losses[:period]) / period
    out = [100 if al == 0 else 100 - 100 / (1 + ag / al)]
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out.append(100 if al == 0 else 100 - 100 / (1 + ag / al))
    return out


def macd(values):
    e12, e26 = ema(values, 12), ema(values, 26)
    if not e12 or not e26:
        return [], [], []
    line = []
    off = 26 - 12
    for i in range(len(e26)):
        j = i + off
        if j < len(e12): line.append(e12[j] - e26[i])
    sig = ema(line, 9)
    if not sig: return line, [], []
    hist = []
    off = len(line) - len(sig)
    for i, s in enumerate(sig): hist.append(line[i + off] - s)
    return line, sig, hist


def atr(c, period=14):
    if len(c) <= period: return []
    tr = []
    for i, x in enumerate(c):
        if i == 0: tr.append(x["high"] - x["low"])
        else:
            prev = c[i - 1]["close"]
            tr.append(max(x["high"] - x["low"], abs(x["high"] - prev), abs(x["low"] - prev)))
    a = sum(tr[:period]) / period; out = [a]
    for x in tr[period:]:
        a = (a * (period - 1) + x) / period; out.append(a)
    return out


def adx(c, period=14):
    if len(c) < period * 2 + 2: return 0.0
    trs, plus, minus = [], [], []
    for i in range(1, len(c)):
        up = c[i]["high"] - c[i - 1]["high"]
        down = c[i - 1]["low"] - c[i]["low"]
        plus.append(up if up > down and up > 0 else 0); minus.append(down if down > up and down > 0 else 0)
        prev = c[i - 1]["close"]
        trs.append(max(c[i]["high"] - c[i]["low"], abs(c[i]["high"] - prev), abs(c[i]["low"] - prev)))
    tr = sum(trs[-period:]) / period; p = sum(plus[-period:]) / period; m = sum(minus[-period:]) / period
    if tr <= 0: return 0.0
    pdi, mdi = 100 * p / tr, 100 * m / tr; den = pdi + mdi
    return 0.0 if den == 0 else 100 * abs(pdi - mdi) / den


def bollinger(values, period=20, mult=2):
    if len(values) < period: return 0, 0, 0
    w = values[-period:]; mean = sum(w) / period
    sd = math.sqrt(sum((x - mean) ** 2 for x in w) / period)
    return mean, mean + mult * sd, mean - mult * sd


def vwap(c, period=30):
    w = c[-period:]; den = sum(x["volume"] for x in w)
    return sum(((x["high"] + x["low"] + x["close"]) / 3) * x["volume"] for x in w) / den if den else w[-1]["close"]


def volume_ratio(c, period=20):
    if len(c) < period + 1: return 1.0
    avg = sum(volumes(c[-period-1:-1])) / period
    return c[-1]["volume"] / avg if avg else 1.0


def obv_delta(c, period=10):
    if len(c) < period + 1: return 0
    obv = 0; vals = []
    for i in range(1, len(c)):
        if c[i]["close"] > c[i-1]["close"]: obv += c[i]["volume"]
        elif c[i]["close"] < c[i-1]["close"]: obv -= c[i]["volume"]
        vals.append(obv)
    return vals[-1] - vals[-1-period]


def timeframe_signal(c):
    if len(c) < MIN_CANDLES:
        return {"direction": "NEUTRAL", "quality": 0, "adx": 0, "rsi": 50, "vr": 1}
    x = closes(c); price = x[-1]
    e20, e50 = ema(x, 20)[-1], ema(x, 50)[-1]; rs = rsi(x, 14); rv = rs[-1] if rs else 50
    ml, ms, mh = macd(x); mac = ml[-1] if ml else 0; hist = mh[-1] if mh else 0
    ad = adx(c); vr = volume_ratio(c); vw = vwap(c); mid, upper, lower = bollinger(x); ob = obv_delta(c)
    lp = sp = 0.0
    lp += 40 if e20 > e50 else 0; sp += 40 if e20 < e50 else 0
    lp += 20 * clamp((rv - 50) / 25); sp += 20 * clamp((50 - rv) / 25)
    lp += 20 if mac > 0 and hist >= 0 else 0; sp += 20 if mac < 0 and hist <= 0 else 0
    lp += 15 if vr >= 1 else 0; sp += 15 if vr >= 1 else 0
    if ad >= 20:
        if e20 > e50: lp += 5
        elif e20 < e50: sp += 5
    lp += 5 if price > vw else 0; sp += 5 if price < vw else 0
    lp += 3 if price > mid else 0; sp += 3 if price < mid else 0
    lp += 2 if 55 < rv < 75 else 0; sp += 2 if 25 < rv < 45 else 0
    lp += 2 if ob > 0 else 0; sp += 2 if ob < 0 else 0
    total = 112.0
    if abs(lp - sp) < 12: direction = "NEUTRAL"; quality = int(max(lp, sp) / total * 100)
    elif lp > sp: direction = "LONG"; quality = int(clamp(lp / total) * 100)
    else: direction = "SHORT"; quality = int(clamp(sp / total) * 100)
    return {"direction": direction, "quality": quality, "adx": ad, "rsi": rv, "vr": vr, "price": price,
            "hist": hist, "ema20": e20, "ema50": e50, "vwap": vw, "upper": upper, "lower": lower, "obv": ob}


def analyze_mtf(frames):
    sig = {tf: timeframe_signal(c) for tf, c in frames.items()}
    dirs = [v["direction"] for v in sig.values() if v["direction"] in ("LONG", "SHORT")]
    longs, shorts = dirs.count("LONG"), dirs.count("SHORT")
    direction = "LONG" if longs > shorts else "SHORT" if shorts > longs else "NEUTRAL"
    agree = longs if direction == "LONG" else shorts if direction == "SHORT" else 0
    qualities = [sig[tf]["quality"] for tf in TF_WEIGHTS if sig[tf]["direction"] == direction]
    q = sum(qualities) / len(qualities) if qualities else 0
    aligned = agree == 4 and all(sig[tf]["direction"] == direction for tf in TF_WEIGHTS)
    return {"signals": sig, "direction": direction, "agree": agree, "quality": q, "aligned": aligned}


def get_btc_dominance():
    if COINMARKETCAP_API_KEY:
        try:
            r = session.get("https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest",
                            headers={"X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY}, timeout=15)
            if r.ok: return safe_float(r.json()["data"]["btc_dominance"])
        except Exception: pass
    try:
        r = session.get(COINGECKO_API + "/global", timeout=15)
        if r.ok: return safe_float(r.json()["data"]["market_cap_percentage"]["btc"])
    except Exception: pass
    return 0.0


def btc_d_proxy_direction():
    products = [p["id"] for p in get_products() if p["id"] != "BTC-USD"][:BTC_D_PROXY_COINS]
    if not products: return "FLAT", 0.0
    btc = get_candles("BTC-USD", 3600, 30)
    if len(btc) < 25: return "FLAT", 0.0
    br = pct(btc[-1]["close"], btc[-25]["close"]); alt_returns = []
    for pid in products:
        c = get_candles(pid, 3600, 30)
        if len(c) >= 25: alt_returns.append(pct(c[-1]["close"], c[-25]["close"]))
    if not alt_returns: return "FLAT", 0.0
    diff = br - sum(alt_returns) / len(alt_returns)
    if diff > 1.0: return "RISING", diff
    if diff < -1.0: return "FALLING", diff
    return "FLAT", diff


def market_regime(btc):
    s = btc["signals"]; bullish = sum(s[tf]["direction"] == "LONG" for tf in ("1H", "4H", "1D")); bearish = sum(s[tf]["direction"] == "SHORT" for tf in ("1H", "4H", "1D"))
    avg_adx = sum(s[tf]["adx"] for tf in ("1H", "4H", "1D")) / 3
    if bullish >= 2 and avg_adx >= 22: return "BULL"
    if bearish >= 2 and avg_adx >= 22: return "BEAR"
    if avg_adx < 18: return "CHOPPY"
    return "NEUTRAL"


def btc_context(force=False):
    now = time.time()
    if btc_context_cache["data"] and not force and now - btc_context_cache["time"] < BTC_CONTEXT_TTL:
        return btc_context_cache["data"]
    frames = get_timeframe_candles("BTC-USD", force=force); mtf = analyze_mtf(frames); dom = get_btc_dominance(); dom_dir, dom_diff = btc_d_proxy_direction()
    data = {"mtf": mtf, "dominance": dom, "dom_dir": dom_dir, "dom_diff": dom_diff}
    data["regime"] = market_regime(mtf); btc_context_cache.update({"time": now, "data": data})
    return data


def live_snapshot(product_id):
    with live_lock:
        state = live_state.get(product_id)
        if not state: return None
        ticks = list(state["ticks"])
    if not ticks: return None
    now = ticks[-1][0]
    def price_at(seconds):
        target = now - seconds
        for t, p, v in reversed(ticks):
            if t <= target: return p
        return ticks[0][1]
    p = ticks[-1][1]
    p15, p30, p60, p180, p300 = [price_at(x) for x in (15, 30, 60, 180, 300)]
    return {"price": p, "age": time.time() - ticks[-1][0],
            "move15": pct(p, p15), "move30": pct(p, p30), "move60": pct(p, p60),
            "move180": pct(p, p180), "move300": pct(p, p300),
            "ticks": ticks}


def live_candles(product_id, seconds=300):
    snap = live_snapshot(product_id)
    if not snap: return []
    cutoff = time.time() - seconds
    buckets = {}
    for ts, price, size in snap["ticks"]:
        if ts < cutoff: continue
        b = int(ts // 60) * 60
        x = buckets.setdefault(b, {"time": b, "open": price, "high": price, "low": price, "close": price, "volume": 0.0})
        x["high"] = max(x["high"], price); x["low"] = min(x["low"], price); x["close"] = price
        x["volume"] += max(0.0, size)
    return [buckets[k] for k in sorted(buckets)]


def early_move_engine(product_id, direction):
    snap = live_snapshot(product_id)
    if not snap or snap["age"] > 45:
        return {"score": 0, "stage": "NO LIVE", "move": 0, "compression": 0, "volume": 0, "momentum": 0, "relative": 0, "breakout": 0, "ready": False}
    lc = live_candles(product_id, 300)
    if len(lc) < 3:
        return {"score": 0, "stage": "WARMUP", "move": 0, "compression": 0, "volume": 0, "momentum": 0, "relative": 0, "breakout": 0, "ready": False}
    sign = 1 if direction == "LONG" else -1
    move = sign * max(snap["move30"], snap["move60"], snap["move180"] * 0.75)
    momentum = 50.0
    # Directional acceleration: short window outperforming longer window.
    short = sign * snap["move30"]; mid = sign * snap["move60"]; long = sign * snap["move180"]
    if short > 0: momentum += 15
    if short > mid / 2: momentum += 15
    if mid > long / 2: momentum += 15
    if max(short, mid, long) >= 0.35: momentum += 10
    momentum = min(100, momentum)

    # Local volume acceleration from trade sizes.
    recent = [x["volume"] for x in lc[-1:]]
    prev = [x["volume"] for x in lc[-min(21, len(lc)-1):-1]]
    avgv = sum(prev) / len(prev) if prev else 0
    vr = (recent[-1] / avgv) if recent and avgv > 0 else 1.0
    volume_score = 45
    if vr >= 1.2: volume_score += 15
    if vr >= 1.5: volume_score += 20
    if vr >= 2.0: volume_score += 10
    volume_score = min(100, volume_score)

    # Compression: narrow recent range + declining realized range.
    ranges = [(x["high"] - x["low"]) / max(x["close"], 1e-12) * 100 for x in lc[-12:]]
    recent_range = sum(ranges[-3:]) / max(1, len(ranges[-3:])); base_range = sum(ranges[:-3]) / max(1, len(ranges[:-3])) if len(ranges) > 3 else recent_range
    compression = 20
    if base_range > 0 and recent_range < base_range * 0.75: compression += 30
    if base_range > 0 and recent_range < base_range * 0.55: compression += 25
    if abs(snap["move60"]) < 0.35: compression += 15
    compression = min(100, compression)

    # Breakout proximity to recent 1m range.
    highs = [x["high"] for x in lc[:-1]]; lows = [x["low"] for x in lc[:-1]]
    hi = max(highs) if highs else snap["price"]; lo = min(lows) if lows else snap["price"]
    if direction == "LONG":
        proximity = clamp((snap["price"] - lo) / max(hi - lo, 1e-12)); breakout = proximity * 70
        breakout += 30 if snap["price"] >= hi * 0.998 else 0
    else:
        proximity = clamp((hi - snap["price"]) / max(hi - lo, 1e-12)); breakout = proximity * 70
        breakout += 30 if snap["price"] <= lo * 1.002 else 0
    breakout = min(100, breakout)

    # Stage: setup before move, then live movement.
    setup = 0.30 * compression + 0.30 * momentum + 0.20 * volume_score + 0.20 * breakout
    if move >= 1.0: stage = "ALARM"
    elif move >= 0.5: stage = "MOVE"
    elif setup >= 68: stage = "READY"
    elif compression >= 60: stage = "SQUEEZE"
    else: stage = "WATCH"
    return {"score": int(round(setup)), "stage": stage, "move": move, "compression": int(compression),
            "volume": int(volume_score), "momentum": int(momentum), "relative": 0, "breakout": int(breakout),
            "volume_ratio": vr, "ready": setup >= 68}


def relative_strength(mtf, btc, frames, live=None):
    coin_c = frames.get("1H", []); btc_c = get_timeframe_candles("BTC-USD").get("1H", [])
    if live and live.get("age", 999) <= 45:
        with live_lock:
            bt = list(live_state.get("BTC-USD", {}).get("ticks", []))
        if bt:
            btc_now = bt[-1][1]; target = bt[-1][0] - 300; old = bt[0][1]
            for t, p, _ in reversed(bt):
                if t <= target: old = p; break
            btc_move = pct(btc_now, old)
            coin_move = live.get("move300", 0)
            edge = coin_move - btc_move
        else: edge = 0.0
    elif len(coin_c) >= 25 and len(btc_c) >= 25:
        coin_ret = pct(coin_c[-1]["close"], coin_c[-25]["close"]); btc_ret = pct(btc_c[-1]["close"], btc_c[-25]["close"])
        edge = coin_ret - btc_ret
    else:
        cs = mtf["signals"]["1H"]; bs = btc["mtf"]["signals"]["1H"]; edge = 0.0
        score = 50 + max(0, cs["quality"] - bs["quality"]) * 0.45
        return {"score": int(clamp(score / 100) * 100), "label": "POZİTİF" if score >= 55 else "ZAYIF", "edge": edge}
    if mtf["direction"] == "SHORT": edge = -edge
    score = 50 + max(-25, min(50, edge * 8.0))
    score = int(clamp(score / 100) * 100)
    label = "🔥 BTC'DEN GÜÇLÜ" if score >= 75 else "POZİTİF" if score >= 55 else "ZAYIF"
    return {"score": score, "label": label, "edge": edge}


def momentum_acceleration(mtf):
    s1, s2, s4 = mtf["signals"]["1H"], mtf["signals"]["2H"], mtf["signals"]["4H"]
    score = 50.0
    if mtf["direction"] == "LONG":
        if s1["hist"] > 0: score += 18
        if s1["hist"] >= s2["hist"]: score += 12
        if s1["rsi"] > s4["rsi"]: score += 12
        if s1["rsi"] > 55: score += 8
    elif mtf["direction"] == "SHORT":
        if s1["hist"] < 0: score += 18
        if s1["hist"] <= s2["hist"]: score += 12
        if s1["rsi"] < s4["rsi"]: score += 12
        if s1["rsi"] < 45: score += 8
    return {"score": int(clamp(score / 100) * 100), "label": "🔥 HIZLANIYOR" if score >= 75 else "POZİTİF" if score >= 55 else "ZAYIF"}


def breakout_analysis(frames, direction):
    c = frames.get("1H", [])
    if len(c) < 25: return {"score": 10, "label": "YOK", "confirmed": False, "fake_risk": False, "vr": 1}
    last = c[-1]; prior = c[-21:-1]; resistance = max(x["high"] for x in prior); support = min(x["low"] for x in prior)
    vr = volume_ratio(c); rng = max(last["high"] - last["low"], 1e-12); body = abs(last["close"] - last["open"])
    close_pos = (last["close"] - last["low"]) / rng; upper_wick = last["high"] - max(last["open"], last["close"]); lower_wick = min(last["open"], last["close"]) - last["low"]
    fake = confirmed = False; score = 10; label = "YOK"
    if direction == "LONG" and last["close"] > resistance:
        confirmed = vr >= 1.15 and close_pos >= 0.60 and upper_wick <= body * 1.8; fake = not confirmed
        score = 75 if vr >= 1.5 and confirmed else 60 if confirmed else 30; label = "KIRILIM + HACİM" if vr >= 1.5 and confirmed else "KIRILIM" if confirmed else "SAHTE KIRILIM RİSKİ"
    elif direction == "SHORT" and last["close"] < support:
        confirmed = vr >= 1.15 and close_pos <= 0.40 and lower_wick <= body * 1.8; fake = not confirmed
        score = 75 if vr >= 1.5 and confirmed else 60 if confirmed else 30; label = "KIRILIM + HACİM" if vr >= 1.5 and confirmed else "KIRILIM" if confirmed else "SAHTE KIRILIM RİSKİ"
    return {"score": score, "label": label, "confirmed": confirmed, "fake_risk": fake, "vr": vr, "resistance": resistance, "support": support}


def compression_historical(frames):
    c = frames.get("1H", [])
    if len(c) < 30: return {"score": 0, "label": "YOK"}
    vals = closes(c)
    _, u1, l1 = bollinger(vals[-40:], 20, 2); bw1 = (u1 - l1) / max(vals[-1], 1e-12)
    _, u2, l2 = bollinger(vals[-60:-20], 20, 2) if len(vals) >= 60 else (0, 0, 0)
    bw2 = (u2 - l2) / max(vals[-21], 1e-12) if u2 else bw1
    score = 25
    if bw1 < bw2 * 0.85: score += 30
    if bw1 < bw2 * 0.65: score += 20
    ar = atr(c, 14); ad = ar[-1] / max(vals[-1], 1e-12) if ar else 0
    if ad < 0.012: score += 15
    score = min(100, score)
    return {"score": int(score), "label": "🔥 SIKIŞMA" if score >= 65 else "HAZIRLIK" if score >= 50 else "NORMAL", "bandwidth": bw1}


def risk_quality(mtf, live_move=0):
    d = mtf["direction"]
    if d == "NEUTRAL": return {"score": 20, "label": "ZAYIF"}
    s = mtf["signals"]["1H"]; score = 75.0
    if d == "LONG" and s["rsi"] > 78: score -= 25
    if d == "SHORT" and s["rsi"] < 22: score -= 25
    if s["adx"] < 15: score -= 20
    if s["vr"] < 0.65: score -= 10
    if mtf["agree"] < 3: score -= 12
    # Avoid rewarding late chasing.
    if abs(live_move) >= 2.0: score -= 15
    score = max(0, min(60, score * 60 / 75))
    return {"score": int(score), "label": "DÜŞÜK RİSK" if score >= 45 else "ORTA RİSK" if score >= 30 else "YÜKSEK RİSK"}


def score_result(mtf, btc, frames, product_id=None):
    direction = mtf["direction"]
    if direction == "NEUTRAL": return {"score": 0, "direction": direction}
    live = live_snapshot(product_id) if product_id else None
    live_move = max((abs(live.get(k, 0)) for k in ("move30", "move60", "move180")), default=0) if live else 0

    # MTF 250
    mtf_score = (mtf["quality"] / 100) * 165 + {1: 10, 2: 30, 3: 65, 4: 85}.get(mtf["agree"], 0)
    mtf_score = min(WEIGHTS["mtf"], mtf_score)

    # BTC 120
    bdir = btc["mtf"]["direction"]; btc_score = 60
    if bdir == direction: btc_score += 40
    elif bdir != "NEUTRAL": btc_score -= 25
    if btc["mtf"]["aligned"] and bdir == direction: btc_score += 20
    btc_score = max(0, min(WEIGHTS["btc"], btc_score))

    # Market + BTC.D 100
    regime = btc["regime"]; market_score = 45
    if (direction == "LONG" and regime == "BULL") or (direction == "SHORT" and regime == "BEAR"): market_score += 35
    elif regime == "CHOPPY": market_score -= 20
    dom_dir = btc["dom_dir"]
    if direction == "LONG": market_score += 20 if dom_dir == "FALLING" else -10 if dom_dir == "RISING" else 0
    else: market_score += 20 if dom_dir == "RISING" else -10 if dom_dir == "FALLING" else 0
    market_score = max(0, min(WEIGHTS["market"], market_score))

    rel = relative_strength(mtf, btc, frames, live)
    mom = momentum_acceleration(mtf)
    brk = breakout_analysis(frames, direction)
    comp = compression_historical(frames)
    early = early_move_engine(product_id, direction) if product_id else {"score": 0, "stage": "OFF", "move": 0, "compression": 0, "volume": 0, "momentum": 0, "breakout": 0}

    # Live engine replaces/augments short-term components without exceeding caps.
    momentum_component = min(WEIGHTS["momentum"], mom["score"] * 0.60 + early["momentum"] * 0.40)
    volume_component = min(WEIGHTS["volume"], early["volume"] * 0.65 + min(100, brk.get("vr", 1) * 45) * 0.35)
    compression_component = min(WEIGHTS["compression"], comp["score"] * 0.45 + early["compression"] * 0.55)
    breakout_component = min(WEIGHTS["breakout"], brk["score"] * 0.50 + early["breakout"] * 0.50)
    risk = risk_quality(mtf, early.get("move", 0))

    total = round(mtf_score + btc_score + market_score + rel["score"] + momentum_component + volume_component + compression_component + breakout_component + risk["score"])
    total = max(0, min(1000, total))
    return {"score": total, "direction": direction, "mtf": mtf_score, "btc": btc_score, "market": market_score,
            "relative": rel, "momentum": {"score": int(momentum_component), "label": mom["label"]},
            "volume": int(volume_component), "compression": int(compression_component),
            "breakout": {**brk, "score": int(breakout_component)}, "risk": risk,
            "early": early, "regime": regime, "dom_dir": dom_dir, "dominance": btc["dominance"],
            "mtf_agree": mtf["agree"], "mtf_aligned": mtf["aligned"], "mtf_signals": mtf["signals"], "btc_direction": bdir}


def level(score):
    if score >= MAXIMUM: return "MAXIMUM"
    if score >= ULTRA: return "ULTRA"
    if score >= EXTREME: return "EXTREME"
    if score >= ELITE: return "ELITE"
    if score >= STRONG: return "STRONG"
    if score >= ALARM: return "ALARM"
    if score >= 600: return "WATCH"
    return "NORMAL"


def alert_bucket(score):
    return score // 25


def should_alert(symbol, result):
    score, direction = result["score"], result["direction"]
    early = result.get("early", {}); stage = early.get("stage", "")
    if direction == "NEUTRAL": return False, ""
    now = time.time(); prev = alert_state.get(symbol); bucket = alert_bucket(score)
    if not prev:
        if score >= ALARM:
            alert_state[symbol] = {"direction": direction, "bucket": bucket, "score": score, "stage": stage, "time": now, "previous_score": None}
            return True, "NEW"
        if stage in ("SQUEEZE", "READY") and score >= 600:
            alert_state[symbol] = {"direction": direction, "bucket": bucket, "score": score, "stage": stage, "time": now}
            return True, "SIKIŞMA / HAZIRLIK"
        return False, ""
    reason = ""
    if direction != prev["direction"]: reason = "REVERSAL"
    elif stage in ("MOVE", "ALARM") and prev.get("stage") not in ("MOVE", "ALARM"): reason = "HAREKET BAŞLADI"
    elif score >= ALARM and bucket > prev.get("bucket", -1): reason = "SİNYAL GÜÇLENDİ"
    elif stage == "READY" and prev.get("stage") == "SQUEEZE": reason = "HAZIRLIK GÜÇLENDİ"
    elif now - prev.get("time", 0) >= ALERT_COOLDOWN and score >= ALARM: reason = "YENİLEME"
    if reason:
        old_score = prev.get("score")
        alert_state[symbol] = {"direction": direction, "bucket": bucket, "score": score, "stage": stage, "time": now, "previous_score": old_score}
        return True, reason
    # Keep strongest state even when not alerting.
    if score > prev.get("score", 0) or stage != prev.get("stage"):
        prev.update({"direction": direction, "bucket": max(bucket, prev.get("bucket", 0)), "score": score, "stage": stage})
    return False, ""


def dominance_label(direction, dom_dir):
    if direction == "LONG" and dom_dir == "FALLING": return "🟢 ALT LEHİNE"
    if direction == "SHORT" and dom_dir == "RISING": return "🔴 BTC LEHİNE"
    if dom_dir == "RISING": return "🟣 BTC GÜÇLENİYOR"
    if dom_dir == "FALLING": return "🟣 ALT GÜÇLENİYOR"
    return "🟣 NÖTR"


def bar(score, width=18):
    n = int(round(clamp(score / 100) * width))
    return "█" * n + "░" * (width - n)


def build_alert(symbol, r, reason):
    icon = "🟢" if r["direction"] == "LONG" else "🔴"; sig = r.get("mtf_signals", {}); e = r.get("early", {})
    move = e.get("move", 0); stage = e.get("stage", "WATCH")
    stage_icon = {"SQUEEZE": "🟡", "READY": "🟠", "MOVE": "🟢", "ALARM": "🚀"}.get(stage, "⚪")
    mtf_text = " ".join(f"{tf} {'🟢' if sig.get(tf, {}).get('direction') == 'LONG' else '🔴' if sig.get(tf, {}).get('direction') == 'SHORT' else '⚪'}" for tf in ("1H", "2H", "4H", "1D"))
    prev = alert_state.get(symbol, {}); prev_score = prev.get("previous_score")
    change = f"\n📈 Skor: {prev_score} → {r['score']}" if prev_score is not None else ""
    return (
        f"🚀 JET ALARM — {level(r['score'])}\n━━━━━━━━━━━━━━━━\n\n"
        f"🪙 {symbol}\n{icon} {r['direction']}\n\n"
        f"JET SCORE\n🔥 {r['score']}/1000{change}\n\n"
        f"⚡ DURUM  {stage_icon} {stage}\n📈 HAREKET  {move:+.2f}%\n"
        f"🎯 GÜÇ\n{bar(r['score'])} {r['score']/10:.0f}%\n\n"
        f"┌─────────────────────┐\n"
        f"│ MTF        {r['mtf_agree']}/4 {'🟢' if r['mtf_aligned'] else '🟡'} │\n"
        f"│ BTC        {'🟢' if r['btc_direction']==r['direction'] else '⚠️'}     │\n"
        f"│ BTC.D      {dominance_label(r['direction'], r['dom_dir'])[:13]:<13} │\n"
        f"│ MOMENTUM   {'🔥' if r['momentum']['score']>=75 else '✅'}     │\n"
        f"│ VOLUME     {'🔥' if r['volume']>=70 else '✅'}     │\n"
        f"│ SIKIŞMA    {'🔥' if r['compression']>=60 else '✅'}     │\n"
        f"│ BREAKOUT   {'🔥' if r['breakout']['score']>=65 else '⚠️'}     │\n"
        f"│ RİSK       {'✅' if r['risk']['score']>=45 else '⚠️'}     │\n"
        f"└─────────────────────┘\n\n"
        f"⏱ MTF  {mtf_text}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"MTF          {r['mtf']:.0f}/{WEIGHTS['mtf']}\n"
        f"BTC          {r['btc']:.0f}/{WEIGHTS['btc']}\n"
        f"MARKET       {r['market']:.0f}/{WEIGHTS['market']}\n"
        f"RELATIVE     {r['relative']['score']}/{WEIGHTS['relative']}\n"
        f"MOMENTUM     {r['momentum']['score']}/{WEIGHTS['momentum']}\n"
        f"VOLUME       {r['volume']}/{WEIGHTS['volume']}\n"
        f"SIKIŞMA      {r['compression']}/{WEIGHTS['compression']}\n"
        f"BREAKOUT     {r['breakout']['score']}/{WEIGHTS['breakout']}\n"
        f"RISK         {r['risk']['score']}/{WEIGHTS['risk']}\n"
        f"━━━━━━━━━━━━━━━━\nTOTAL        {r['score']}/1000\n\n"
        f"⚡ OLAY: {reason}"
    )


def build_watch(symbol, r, reason):
    e = r.get("early", {}); icon = "🟢" if r["direction"] == "LONG" else "🔴"
    return (f"🟡 JET WATCH\n━━━━━━━━━━━━━━━━\n\n🪙 {symbol}\n{icon} {r['direction']}\n\n"
            f"JET SCORE  {r['score']}/1000\n\n📦 SIKIŞMA  {bar(e.get('compression',0), 18)}\n"
            f"⚡ HAZIRLIK  {e.get('stage','WATCH')}\n\n📊 Hacim      {'↑' if e.get('volume',0)>=60 else '→'}\n"
            f"📈 Momentum   {'↑' if e.get('momentum',0)>=60 else '→'}\n🎯 Breakout   {'YAKIN' if e.get('breakout',0)>=65 else 'NORMAL'}\n\n"
            f"━━━━━━━━━━━━━━━━\n⚠️ {reason}")


def market_scan(chat_id, live_only=False):
    global last_scan_time
    last_scan_time = time.time(); btc = btc_context(); products = get_products(); results = []; alerts = []
    for p in products:
        pid = p.get("id")
        if not pid or pid == "BTC-USD": continue
        try:
            frames = get_timeframe_candles(pid)
            if len(frames["1H"]) < MIN_CANDLES or len(frames["1D"]) < MIN_CANDLES: continue
            mtf = analyze_mtf(frames); r = score_result(mtf, btc, frames, pid)
            symbol = pid.replace("-USD", "")
            if r["score"] >= 600 or r.get("early", {}).get("stage") in ("SQUEEZE", "READY", "MOVE", "ALARM"):
                results.append((symbol, r)); ok, reason = should_alert(symbol, r)
                if ok: alerts.append((symbol, r, reason))
        except Exception as e: print("Analyze error", pid, e)
    results.sort(key=lambda x: x[1]["score"], reverse=True)
    for symbol, r, reason in alerts[:20]:
        if r["score"] >= ALARM: send_message(chat_id, build_alert(symbol, r, reason))
        else: send_message(chat_id, build_watch(symbol, r, reason))
    top = results[:15]
    lines = ["🚀 CRYPTO JET V14.0", "━━━━━━━━━━━━━━━━", f"🪙 Analiz: {max(0,len(products)-1)} Coinbase USD market",
             f"🟡 600+: {sum(r['score'] >= 600 for _,r in results)}", f"🚨 700+: {sum(r['score'] >= 700 for _,r in results)}",
             f"🔥 800+: {sum(r['score'] >= 800 for _,r in results)}", "", "🏆 TOP SİNYALLER"]
    if not top: lines.append("Şu anda güçlü sinyal yok.")
    else:
        for s, r in top:
            icon = "🟢" if r["direction"] == "LONG" else "🔴"; st = r.get("early", {}).get("stage", "")
            lines.append(f"{s:<10} {icon} {r['direction']:<5} {r['score']}/1000 — {level(r['score'])} {st}")
    send_message(chat_id, "\n".join(lines)); return results


def live_loop():
    global last_scan_time
    while not ws_stop.is_set():
        chat = active_chat_id
        if chat and ws_connected:
            try:
                products = get_products(); btc = btc_context(); alerts = []
                # Only evaluate coins that have a live tick; historical MTF is cached.
                for p in products:
                    pid = p.get("id")
                    if not pid or pid == "BTC-USD": continue
                    snap = live_snapshot(pid)
                    if not snap or snap["age"] > 45: continue
                    frames = get_timeframe_candles(pid)
                    if len(frames["1H"]) < MIN_CANDLES or len(frames["1D"]) < MIN_CANDLES: continue
                    mtf = analyze_mtf(frames); r = score_result(mtf, btc, frames, pid)
                    if r["score"] >= 600 or r.get("early", {}).get("stage") in ("SQUEEZE", "READY", "MOVE", "ALARM"):
                        symbol = pid.replace("-USD", ""); ok, reason = should_alert(symbol, r)
                        if ok: alerts.append((symbol, r, reason))
                for symbol, r, reason in sorted(alerts, key=lambda x: x[1]["score"], reverse=True)[:10]:
                    if r["score"] >= ALARM: send_message(chat, build_alert(symbol, r, reason))
                    else: send_message(chat, build_watch(symbol, r, reason))
            except Exception as e: print("Live engine error:", e)
        time.sleep(LIVE_LOOP_INTERVAL)


def ws_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("type") != "ticker": return
        pid = data.get("product_id"); price = safe_float(data.get("price")); size = safe_float(data.get("last_size"))
        if not pid or price <= 0: return
        ts = time.time()
        with live_lock:
            st = live_state[pid]; st["ticks"].append((ts, price, size)); st["last"] = price; st["last_time"] = ts
    except Exception as e: print("WS message error:", e)


def ws_open(ws):
    global ws_connected
    ws_connected = True
    products = [p["id"] for p in get_products()]
    if not products: return
    payload = {"type": "subscribe", "product_ids": products, "channels": ["ticker", "heartbeat"]}
    try: ws.send(json.dumps(payload)); print(f"WebSocket subscribed: {len(products)} products")
    except Exception as e: print("WS subscribe error:", e)


def ws_close(ws, code, msg):
    global ws_connected
    ws_connected = False; print("WebSocket closed:", code, msg)


def ws_error(ws, error):
    global ws_connected
    ws_connected = False; print("WebSocket error:", error)


def websocket_loop():
    global ws_thread, ws_products_signature
    try:
        import websocket
    except ImportError:
        print("websocket-client missing. Install requirements.txt")
        return
    while not ws_stop.is_set():
        try:
            products = get_products(force=True); sig = ",".join(sorted(p["id"] for p in products))
            ws_products_signature = sig
            ws = websocket.WebSocketApp(COINBASE_WS, on_open=ws_open, on_message=ws_message, on_error=ws_error, on_close=ws_close)
            ws.run_forever(ping_interval=25, ping_timeout=10)
        except Exception as e:
            print("WS reconnect error:", e)
        if not ws_stop.is_set(): time.sleep(5)


def start_live_engine():
    global ws_thread
    if ws_thread and ws_thread.is_alive(): return
    ws_stop.clear(); ws_thread = threading.Thread(target=websocket_loop, name="crypto-jet-ws", daemon=True); ws_thread.start()
    threading.Thread(target=live_loop, name="crypto-jet-live", daemon=True).start()


def command_jet(chat_id):
    send_message(chat_id, "🚀 Crypto Jet V14.0 tarama başladı...\nLive WebSocket + Early Move + Sıkışma + MTF + BTC + BTC.D + Breakout + Risk aktif.")
    market_scan(chat_id)


def command_btc(chat_id):
    btc = btc_context(force=True); m = btc["mtf"]; s = m["signals"]
    send_message(chat_id, "₿ CRYPTO JET — BTC MARKET\n━━━━━━━━━━━━━━━━\n"
                 f"Yön: {m['direction']}\nMTF uyumu: {m['agree']}/4\n"
                 f"1H: {s['1H']['direction']} | 2H: {s['2H']['direction']} | 4H: {s['4H']['direction']} | 1D: {s['1D']['direction']}\n"
                 f"Market Regime: {btc['regime']}\nBTC.D: {btc['dominance']:.2f}%\nBTC.D proxy: {btc['dom_dir']}\n\n"
                 f"LIVE: {'🟢 AKTİF' if ws_connected else '🔴 BAĞLI DEĞİL'}")


def handle_command(chat_id, text):
    global active_chat_id
    active_chat_id = chat_id; cmd = text.strip().split()[0].lower() if text.strip() else ""
    if cmd == "/start":
        send_message(chat_id, "🚀 Crypto Jet V14.0 çalışıyor!\n\n/jet — tam tarama\n/btc — BTC + piyasa\n/scan — tarama\n/status — durum\n/stop — otomatik taramayı durdur")
    elif cmd == "/jet": command_jet(chat_id)
    elif cmd in ("/scan", "/sc"): command_jet(chat_id)
    elif cmd == "/btc": command_btc(chat_id)
    elif cmd == "/status":
        send_message(chat_id, "🟢 CRYPTO JET V14.0 AKTİF\n\n🟡 600+ WATCH\n🚨 700+ ALARM\n🔥 800+ STRONG\n💎 900+ ELITE\n🚀 950+ EXTREME\n⚡ 990+ ULTRA\n👑 1000 MAXIMUM\n\nLive WebSocket + Early Move + Sıkışma + MTF + BTC + BTC.D + Relative + Momentum + Volume + Breakout + Risk aktif.")
    elif cmd == "/stop":
        active_chat_id = None; send_message(chat_id, "🛑 Otomatik/live alarm akışı durduruldu. /jet ile tekrar başlatabilirsin.")


def telegram_updates(offset=None):
    if not TELEGRAM_BOT_TOKEN: return []
    try:
        params = {"timeout": 25}
        if offset is not None: params["offset"] = offset
        r = session.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35)
        if r.ok: return r.json().get("result", [])
    except Exception as e: print("Telegram polling error:", e)
    return []


def main():
    if not TELEGRAM_BOT_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN eksik.")
    print("CRYPTO JET V14.0 başlatıldı")
    get_products(force=True)
    start_live_engine()
    offset = None; last_auto = 0
    while True:
        updates = telegram_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1; msg = u.get("message") or {}; chat = msg.get("chat", {}).get("id"); text = msg.get("text", "")
            if chat and text.startswith("/"): handle_command(chat, text)
        if active_chat_id and time.time() - last_auto >= SCAN_INTERVAL:
            try: market_scan(active_chat_id)
            except Exception as e: print("Automatic scan error:", e)
            last_auto = time.time()
        time.sleep(1)


if __name__ == "__main__":
    main()
