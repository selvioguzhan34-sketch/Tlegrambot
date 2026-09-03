import os
import time
import math
import requests
from collections import defaultdict

# =========================================================
# CRYPTO JET V13.2.1 — SMART MTF ENGINE
#
# 1H / 2H / 4H / 1D
# BTC + BTC.D proxy + Market Regime
# Relative Strength + Momentum Acceleration
# Breakout + Volume + Fake-breakout filter
# Risk / Quality layer
# SMART ALERTS
# SCORE: 0-1000
# 700 ALARM / 800 STRONG / 900 ELITE / 950 EXTREME
# 990 ULTRA / 1000 MAXIMUM
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

SCAN_INTERVAL = 600
PRODUCT_REFRESH = 1800
CANDLE_TTL = 300
BTC_CONTEXT_TTL = 300
ALERT_COOLDOWN = 3600
MIN_CANDLES = 55
BTC_D_PROXY_COINS = 30

ALARM = 700
STRONG = 800
ELITE = 900
EXTREME = 950
ULTRA = 990
MAXIMUM = 1000

TF_WEIGHTS = {"1H": 160, "2H": 190, "4H": 250, "1D": 250}
BASE_GRANULARITY = 3600

active_chat_id = None
last_scan_time = 0
last_product_refresh = 0
products_cache = []
candle_cache = {}
btc_context_cache = {"time": 0, "data": None}
alert_state = {}

session = requests.Session()
session.headers.update({"User-Agent": "Crypto-Jet/13.2.1"})


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
        r = session.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=20,
        )
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
    # Coinbase: [time, low, high, open, close, volume]
    out = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            out.append({
                "time": int(row[0]),
                "low": safe_float(row[1]),
                "high": safe_float(row[2]),
                "open": safe_float(row[3]),
                "close": safe_float(row[4]),
                "volume": safe_float(row[5]),
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

    # Coinbase returns up to 300 candles. A recent window is enough for this engine.
    seconds = granularity * min(max(limit, 60), 290)
    end = int(time.time())
    start = end - seconds
    data = coinbase_get(
        f"/products/{product_id}/candles",
        {"granularity": granularity, "start": start, "end": end},
    )
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
        bucket = (c["time"] // step) * step
        groups[bucket].append(c)
    out = []
    for bucket in sorted(groups):
        g = sorted(groups[bucket], key=lambda x: x["time"])
        if not g:
            continue
        # Conservative: only use a full block.
        expected = set(range(bucket, bucket + step, 3600))
        actual = {int(x["time"]) for x in g}
        if not expected.issubset(actual):
            continue
        out.append({
            "time": bucket,
            "open": g[0]["open"],
            "high": max(x["high"] for x in g),
            "low": min(x["low"] for x in g),
            "close": g[-1]["close"],
            "volume": sum(x["volume"] for x in g),
        })
    return out


def get_timeframe_candles(product_id, force=False):
    h1 = get_candles(product_id, 3600, 250, force=force)
    # 1D is fetched directly for a cleaner daily signal.
    d1 = get_candles(product_id, 86400, 120, force=force)
    return {
        "1H": h1,
        "2H": aggregate_candles(h1, 2),
        "4H": aggregate_candles(h1, 4),
        "1D": d1,
    }


def closes(c): return [x["close"] for x in c]
def highs(c): return [x["high"] for x in c]
def lows(c): return [x["low"] for x in c]
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
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
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
    # Align by using the common tail.
    line = []
    start_offset = 26 - 12
    for i in range(len(e26)):
        j = i + start_offset
        if j < len(e12):
            line.append(e12[j] - e26[i])
    sig = ema(line, 9)
    if not sig:
        return line, [], []
    hist = []
    off = len(line) - len(sig)
    for i, s in enumerate(sig):
        hist.append(line[i + off] - s)
    return line, sig, hist


def atr(c, period=14):
    if len(c) <= period:
        return []
    tr = []
    for i, x in enumerate(c):
        if i == 0:
            tr.append(x["high"] - x["low"])
        else:
            prev = c[i - 1]["close"]
            tr.append(max(x["high"] - x["low"], abs(x["high"] - prev), abs(x["low"] - prev)))
    a = sum(tr[:period]) / period
    out = [a]
    for x in tr[period:]:
        a = (a * (period - 1) + x) / period
        out.append(a)
    return out


def adx(c, period=14):
    if len(c) < period * 2 + 2:
        return 0.0
    trs, plus, minus = [], [], []
    for i in range(1, len(c)):
        up = c[i]["high"] - c[i - 1]["high"]
        down = c[i - 1]["low"] - c[i]["low"]
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)
        prev = c[i - 1]["close"]
        trs.append(max(c[i]["high"] - c[i]["low"], abs(c[i]["high"] - prev), abs(c[i]["low"] - prev)))
    tr = sum(trs[-period:]) / period
    p = sum(plus[-period:]) / period
    m = sum(minus[-period:]) / period
    if tr <= 0:
        return 0.0
    pdi, mdi = 100 * p / tr, 100 * m / tr
    den = pdi + mdi
    return 0.0 if den == 0 else 100 * abs(pdi - mdi) / den


def bollinger(values, period=20, mult=2):
    if len(values) < period:
        return 0, 0, 0
    w = values[-period:]
    mean = sum(w) / period
    sd = math.sqrt(sum((x - mean) ** 2 for x in w) / period)
    return mean, mean + mult * sd, mean - mult * sd


def vwap(c, period=30):
    w = c[-period:]
    den = sum(x["volume"] for x in w)
    return sum(((x["high"] + x["low"] + x["close"]) / 3) * x["volume"] for x in w) / den if den else w[-1]["close"]


def volume_ratio(c, period=20):
    if len(c) < period + 1:
        return 1.0
    avg = sum(volumes(c[-period-1:-1])) / period
    return c[-1]["volume"] / avg if avg else 1.0


def obv_delta(c, period=10):
    if len(c) < period + 1:
        return 0
    obv = 0
    vals = []
    for i in range(1, len(c)):
        if c[i]["close"] > c[i-1]["close"]: obv += c[i]["volume"]
        elif c[i]["close"] < c[i-1]["close"]: obv -= c[i]["volume"]
        vals.append(obv)
    return vals[-1] - vals[-1-period]


def timeframe_signal(c):
    if len(c) < MIN_CANDLES:
        return {"direction": "NEUTRAL", "quality": 0, "adx": 0, "rsi": 50, "vr": 1}
    x = closes(c)
    price = x[-1]
    e20, e50 = ema(x, 20)[-1], ema(x, 50)[-1]
    rs = rsi(x, 14)
    rv = rs[-1] if rs else 50
    ml, ms, mh = macd(x)
    mac = ml[-1] if ml else 0
    hist = mh[-1] if mh else 0
    ad = adx(c)
    vr = volume_ratio(c)
    vw = vwap(c)
    mid, upper, lower = bollinger(x)
    ob = obv_delta(c)

    long_pts = short_pts = 0.0
    long_pts += 40 if e20 > e50 else 0
    short_pts += 40 if e20 < e50 else 0
    long_pts += 20 * clamp((rv - 50) / 25)
    short_pts += 20 * clamp((50 - rv) / 25)
    long_pts += 20 if mac > 0 and hist >= 0 else 0
    short_pts += 20 if mac < 0 and hist <= 0 else 0
    long_pts += 15 if vr >= 1 else 0
    short_pts += 15 if vr >= 1 else 0
    if ad >= 20:
        if e20 > e50: long_pts += 5
        elif e20 < e50: short_pts += 5
    long_pts += 5 if price > vw else 0
    short_pts += 5 if price < vw else 0
    long_pts += 3 if price > mid else 0
    short_pts += 3 if price < mid else 0
    long_pts += 2 if rv > 55 and rv < 75 else 0
    short_pts += 2 if rv < 45 and rv > 25 else 0
    long_pts += 2 if ob > 0 else 0
    short_pts += 2 if ob < 0 else 0

    total = 112.0
    if abs(long_pts - short_pts) < 12:
        direction = "NEUTRAL"
        quality = int(max(long_pts, short_pts) / total * 100)
    elif long_pts > short_pts:
        direction = "LONG"
        quality = int(clamp(long_pts / total) * 100)
    else:
        direction = "SHORT"
        quality = int(clamp(short_pts / total) * 100)
    return {"direction": direction, "quality": quality, "adx": ad, "rsi": rv, "vr": vr,
            "price": price, "hist": hist, "ema20": e20, "ema50": e50, "vwap": vw,
            "upper": upper, "lower": lower, "obv": ob}


def analyze_mtf(frames):
    sig = {tf: timeframe_signal(c) for tf, c in frames.items()}
    dirs = [v["direction"] for v in sig.values() if v["direction"] in ("LONG", "SHORT")]
    longs, shorts = dirs.count("LONG"), dirs.count("SHORT")
    if longs == 0 and shorts == 0:
        direction = "NEUTRAL"
    elif longs > shorts:
        direction = "LONG"
    elif shorts > longs:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"
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
            if r.ok:
                return safe_float(r.json()["data"]["btc_dominance"])
        except Exception:
            pass
    try:
        r = session.get(COINGECKO_API + "/global", timeout=15)
        if r.ok:
            return safe_float(r.json()["data"]["market_cap_percentage"]["btc"])
    except Exception:
        pass
    return 0.0


def btc_d_proxy_direction():
    products = [p["id"] for p in get_products() if p["id"] != "BTC-USD"][:BTC_D_PROXY_COINS]
    if not products:
        return "FLAT", 0.0
    btc = get_candles("BTC-USD", 3600, 30)
    if len(btc) < 25:
        return "FLAT", 0.0
    br = pct(btc[-1]["close"], btc[-25]["close"])
    alt_returns = []
    for pid in products:
        c = get_candles(pid, 3600, 30)
        if len(c) >= 25:
            alt_returns.append(pct(c[-1]["close"], c[-25]["close"]))
    if not alt_returns:
        return "FLAT", 0.0
    ar = sum(alt_returns) / len(alt_returns)
    diff = br - ar
    if diff > 1.0: return "RISING", diff
    if diff < -1.0: return "FALLING", diff
    return "FLAT", diff


def market_regime(btc):
    s = btc["signals"]
    bullish = sum(s[tf]["direction"] == "LONG" for tf in ("1H", "4H", "1D"))
    bearish = sum(s[tf]["direction"] == "SHORT" for tf in ("1H", "4H", "1D"))
    avg_adx = sum(s[tf]["adx"] for tf in ("1H", "4H", "1D")) / 3
    if bullish >= 2 and avg_adx >= 22: return "BULL"
    if bearish >= 2 and avg_adx >= 22: return "BEAR"
    if avg_adx < 18: return "CHOPPY"
    return "NEUTRAL"


def btc_context(force=False):
    now = time.time()
    if btc_context_cache["data"] and not force and now - btc_context_cache["time"] < BTC_CONTEXT_TTL:
        return btc_context_cache["data"]
    frames = get_timeframe_candles("BTC-USD", force=force)
    mtf = analyze_mtf(frames)
    dom = get_btc_dominance()
    dom_dir, dom_diff = btc_d_proxy_direction()
    data = {"mtf": mtf, "dominance": dom, "dom_dir": dom_dir, "dom_diff": dom_diff}
    data["regime"] = market_regime(mtf)
    btc_context_cache.update({"time": now, "data": data})
    return data


def relative_strength(mtf, btc, frames):
    # Coin-vs-BTC 24h relative strength, direction-aware.
    coin_c = frames.get("1H", [])
    btc_c = get_timeframe_candles("BTC-USD").get("1H", [])
    if len(coin_c) >= 25 and len(btc_c) >= 25:
        coin_ret = pct(coin_c[-1]["close"], coin_c[-25]["close"])
        btc_ret = pct(btc_c[-1]["close"], btc_c[-25]["close"])
        edge = coin_ret - btc_ret
        if mtf["direction"] == "SHORT":
            edge = -edge
        score = 50 + max(-20, min(45, edge * 4.0))
    else:
        cs = mtf["signals"]["1H"]
        bs = btc["mtf"]["signals"]["1H"]
        score = 50 + max(0, cs["quality"] - bs["quality"]) * 0.45
        edge = 0.0
        coin_ret = btc_ret = 0.0
    score = int(clamp(score / 100) * 100)
    label = "🔥 BTC'DEN GÜÇLÜ" if score >= 75 else "POZİTİF" if score >= 55 else "ZAYIF"
    return {"score": score, "label": label, "edge": edge, "coin_ret": coin_ret, "btc_ret": btc_ret}


def momentum_acceleration(mtf):
    s1 = mtf["signals"]["1H"]
    s2 = mtf["signals"]["2H"]
    s4 = mtf["signals"]["4H"]
    score = 50.0
    # Current MACD histogram and RSI structure.
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
    score = int(clamp(score / 100) * 100)
    return {"score": score, "label": "🔥 HIZLANIYOR" if score >= 75 else "POZİTİF" if score >= 55 else "ZAYIF"}


def breakout_analysis(frames, direction):
    c = frames.get("1H", [])
    if len(c) < 25:
        return {"score": 20, "label": "YOK", "confirmed": False, "fake_risk": False}
    last = c[-1]
    prior = c[-21:-1]
    resistance = max(x["high"] for x in prior)
    support = min(x["low"] for x in prior)
    vr = volume_ratio(c)
    rng = max(last["high"] - last["low"], 1e-12)
    body = abs(last["close"] - last["open"])
    close_pos = (last["close"] - last["low"]) / rng
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    fake = False
    confirmed = False
    score = 15
    label = "YOK"
    if direction == "LONG" and last["close"] > resistance:
        confirmed = vr >= 1.15 and close_pos >= 0.60 and upper_wick <= body * 1.8
        fake = not confirmed
        score = 75 if vr >= 1.5 and confirmed else 60 if confirmed else 30
        label = "KIRILIM + HACİM" if vr >= 1.5 and confirmed else "KIRILIM" if confirmed else "SAHTE KIRILIM RİSKİ"
    elif direction == "SHORT" and last["close"] < support:
        confirmed = vr >= 1.15 and close_pos <= 0.40 and lower_wick <= body * 1.8
        fake = not confirmed
        score = 75 if vr >= 1.5 and confirmed else 60 if confirmed else 30
        label = "KIRILIM + HACİM" if vr >= 1.5 and confirmed else "KIRILIM" if confirmed else "SAHTE KIRILIM RİSKİ"
    return {"score": score, "label": label, "confirmed": confirmed, "fake_risk": fake, "vr": vr,
            "resistance": resistance, "support": support}


def risk_quality(mtf):
    d = mtf["direction"]
    if d == "NEUTRAL": return {"score": 20, "label": "ZAYIF"}
    s = mtf["signals"]["1H"]
    score = 75.0
    if d == "LONG" and s["rsi"] > 78: score -= 25
    if d == "SHORT" and s["rsi"] < 22: score -= 25
    if s["adx"] < 15: score -= 20
    if s["vr"] < 0.65: score -= 10
    if mtf["agree"] < 3: score -= 12
    return {"score": int(clamp(score / 75) * 75), "label": "DÜŞÜK RİSK" if score >= 60 else "ORTA RİSK" if score >= 40 else "YÜKSEK RİSK"}


def score_result(mtf, btc, frames):
    direction = mtf["direction"]
    if direction == "NEUTRAL":
        return {"score": 0, "direction": direction}

    # 350 — coin MTF. Agreement is rewarded but not an all-TF hard gate.
    mtf_score = (mtf["quality"] / 100) * 240
    mtf_score += {2: 45, 3: 80, 4: 110}.get(mtf["agree"], 0)
    mtf_score = min(350, mtf_score)

    # 150 — BTC confirmation.
    bdir = btc["mtf"]["direction"]
    btc_score = 75
    if bdir == direction: btc_score += 55
    elif bdir != "NEUTRAL": btc_score -= 30
    if btc["mtf"]["aligned"] and bdir == direction: btc_score += 20
    btc_score = clamp(btc_score / 150) * 150

    # 100 — market regime + BTC dominance proxy.
    regime = btc["regime"]
    market_score = 45
    if (direction == "LONG" and regime == "BULL") or (direction == "SHORT" and regime == "BEAR"):
        market_score += 35
    elif regime == "CHOPPY":
        market_score -= 20
    dom_dir = btc["dom_dir"]
    if direction == "LONG":
        if dom_dir == "FALLING": market_score += 20
        elif dom_dir == "RISING": market_score -= 10
    else:
        if dom_dir == "RISING": market_score += 20
        elif dom_dir == "FALLING": market_score -= 10
    market_score = clamp(market_score / 100) * 100

    rel = relative_strength(mtf, btc, frames)
    mom = momentum_acceleration(mtf)
    brk = breakout_analysis(frames, direction)
    risk = risk_quality(mtf)

    total = round(mtf_score + btc_score + market_score + rel["score"] + mom["score"] + brk["score"] + risk["score"])
    total = max(0, min(1000, total))
    return {"score": total, "direction": direction, "mtf": mtf_score, "btc": btc_score,
            "market": market_score, "relative": rel, "momentum": mom, "breakout": brk,
            "risk": risk, "regime": regime, "dom_dir": dom_dir, "dominance": btc["dominance"],
            "mtf_agree": mtf["agree"], "mtf_aligned": mtf["aligned"],
            "mtf_signals": mtf["signals"], "btc_direction": btc["mtf"]["direction"]}


def level(score):
    if score >= MAXIMUM: return "MAXIMUM"
    if score >= ULTRA: return "ULTRA"
    if score >= EXTREME: return "EXTREME"
    if score >= ELITE: return "ELITE"
    if score >= STRONG: return "STRONG"
    if score >= ALARM: return "ALARM"
    return "NORMAL"


def should_alert(symbol, result):
    score, direction = result["score"], result["direction"]
    if score < ALARM or direction == "NEUTRAL":
        return False, ""
    now = time.time()
    prev = alert_state.get(symbol)
    bucket = score // 25
    if not prev:
        alert_state[symbol] = {"direction": direction, "bucket": bucket, "score": score, "time": now}
        return True, "NEW"

    reason = ""
    if direction != prev["direction"]:
        reason = "REVERSAL"
    elif bucket > prev["bucket"]:
        reason = "SİNYAL GÜÇLENDİ"
    elif now - prev["time"] >= ALERT_COOLDOWN:
        reason = "YENİLEME"

    if reason:
        alert_state[symbol] = {"direction": direction, "bucket": bucket, "score": score, "time": now}
        return True, reason
    return False, ""


def dominance_label(direction, dom_dir):
    if direction == "LONG" and dom_dir == "FALLING":
        return "🟢 ALT LEHİNE"
    if direction == "SHORT" and dom_dir == "RISING":
        return "🔴 BTC LEHİNE"
    if dom_dir == "RISING":
        return "🟣 BTC GÜÇLENİYOR"
    if dom_dir == "FALLING":
        return "🟣 ALT GÜÇLENİYOR"
    return "🟣 NÖTR"


def build_alert(symbol, r, reason):
    icon = "🟢" if r["direction"] == "LONG" else "🔴"
    sig = r.get("mtf_signals", {})
    prev = alert_state.get(symbol, {})
    prev_score = prev.get("score")
    # alert_state already contains current score after should_alert; use reason context separately.
    change = ""
    if reason == "SİNYAL GÜÇLENDİ" and prev_score is not None and prev_score != r["score"]:
        change = f"\n📈 Skor: {prev_score} → {r['score']}"
    elif reason == "REVERSAL":
        change = "\n🚨 YÖN DEĞİŞİMİ"

    mtf_lines = []
    for tf in ("1H", "2H", "4H", "1D"):
        d = sig.get(tf, {}).get("direction", "NEUTRAL")
        i = "🟢" if d == "LONG" else "🔴" if d == "SHORT" else "⚪"
        mtf_lines.append(f"{tf:<3} {i}")

    return (
        f"🚀 JET ALARM — {level(r['score'])}\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"🪙 {symbol}\n"
        f"{icon} {r['direction']}\n\n"
        f"JET SCORE: {r['score']}/1000{change}\n\n"
        f"⏱ MTF\n"
        f"{mtf_lines[0]}   {mtf_lines[1]}   {mtf_lines[2]}   {mtf_lines[3]}\n"
        f"🎯 Uyum: {r.get('mtf_agree', 0)}/4\n\n"
        f"₿ BTC       {('🟢 LONG' if r['btc_direction']=='LONG' else '🔴 SHORT' if r['btc_direction']=='SHORT' else '⚪ NEUTRAL')}\n"
        f"🟣 BTC.D    {dominance_label(r['direction'], r['dom_dir'])}\n"
        f"📈 MARKET   {'🟢 BULL' if r['regime']=='BULL' else '🔴 BEAR' if r['regime']=='BEAR' else '🟡 '+r['regime']}\n\n"
        f"💪 Relative Strength  {'🔥' if r['relative']['score'] >= 75 else '✅'}\n"
        f"⚡ Momentum             {'🔥' if r['momentum']['score'] >= 75 else '✅'}\n"
        f"📈 Breakout             {'🔥' if r['breakout']['score'] >= 70 else '✅' if r['breakout']['confirmed'] else '⚠️'}\n"
        f"📊 Volume               {'🔥' if r['breakout'].get('vr', 0) >= 1.5 else '✅'}\n"
        f"🛡 Risk                 {'✅' if r['risk']['score'] >= 60 else '⚠️'}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"MTF          {r['mtf']:.0f}/350\n"
        f"BTC          {r['btc']:.0f}/150\n"
        f"MARKET        {r['market']:.0f}/100\n"
        f"RELATIVE      {r['relative']['score']}/100\n"
        f"MOMENTUM      {r['momentum']['score']}/100\n"
        f"BREAKOUT      {r['breakout']['score']}/75\n"
        f"RISK          {r['risk']['score']}/75\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"TOTAL        {r['score']}/1000\n\n"
        f"⚡ OLAY: {reason}"
    )


def market_scan(chat_id):
    global last_scan_time
    last_scan_time = time.time()
    btc = btc_context()
    products = get_products()
    results = []
    alerts = []

    for p in products:
        pid = p.get("id")
        if not pid or pid == "BTC-USD":
            continue
        try:
            frames = get_timeframe_candles(pid)
            if len(frames["1H"]) < MIN_CANDLES or len(frames["1D"]) < MIN_CANDLES:
                continue
            mtf = analyze_mtf(frames)
            r = score_result(mtf, btc, frames)
            if r["score"] >= ALARM:
                symbol = pid.replace("-USD", "")
                results.append((symbol, r))
                ok, reason = should_alert(symbol, r)
                if ok:
                    alerts.append((symbol, r, reason))
        except Exception as e:
            print("Analyze error", pid, e)

    results.sort(key=lambda x: x[1]["score"], reverse=True)
    for symbol, r, reason in alerts[:20]:
        send_message(chat_id, build_alert(symbol, r, reason))

    top = results[:15]
    lines = [
        "🚀 CRYPTO JET V13.2.1",
        "━━━━━━━━━━━━━━━━",
        f"🪙 Analiz: {len(products)-1} Coinbase USD market",
        f"🔥 700+: {sum(r['score'] >= 700 for _,r in results)}",
        f"💥 800+: {sum(r['score'] >= 800 for _,r in results)}",
        f"🚨 900+: {sum(r['score'] >= 900 for _,r in results)}",
        "",
        "🏆 TOP SİNYALLER"
    ]
    if not top:
        lines.append("Şu anda 700+ güçlü sinyal yok.")
    else:
        for s, r in top:
            icon = "🟢" if r["direction"] == "LONG" else "🔴"
            lines.append(f"{s:<10} {icon} {r['direction']:<5} {r['score']}/1000 — {level(r['score'])}")
    send_message(chat_id, "\n".join(lines))
    return results


def command_jet(chat_id):
    send_message(chat_id, "🚀 Crypto Jet V13.2.1 tarama başladı...\nMTF + BTC + BTC.D + Regime + Relative Strength + Momentum + Breakout + Risk aktif.")
    market_scan(chat_id)


def command_btc(chat_id):
    btc = btc_context(force=True)
    m = btc["mtf"]
    s = m["signals"]
    text = (
        "₿ CRYPTO JET — BTC MARKET\n"
        "━━━━━━━━━━━━━━━━\n"
        f"Yön: {m['direction']}\n"
        f"MTF uyumu: {m['agree']}/4\n"
        f"1H: {s['1H']['direction']} | 2H: {s['2H']['direction']} | 4H: {s['4H']['direction']} | 1D: {s['1D']['direction']}\n"
        f"Market Regime: {btc['regime']}\n"
        f"BTC.D: {btc['dominance']:.2f}%\n"
        f"BTC.D proxy: {btc['dom_dir']}"
    )
    send_message(chat_id, text)


def handle_command(chat_id, text):
    global active_chat_id
    active_chat_id = chat_id
    cmd = text.strip().split()[0].lower() if text.strip() else ""
    if cmd == "/start":
        send_message(chat_id, "🚀 Crypto Jet V13.2.1 çalışıyor!\n\n/jet — tam tarama\n/btc — BTC + piyasa\n/scan — tarama\n/status — durum\n/stop — otomatik taramayı durdur")
    elif cmd == "/jet":
        command_jet(chat_id)
    elif cmd in ("/scan", "/sc"):
        command_jet(chat_id)
    elif cmd == "/btc":
        command_btc(chat_id)
    elif cmd == "/status":
        send_message(chat_id, "🟢 CRYPTO JET V13.2.1 AKTİF\n\n700+ ALARM\n800+ STRONG\n900+ ELITE\n950+ EXTREME\n990+ ULTRA\n1000 MAXIMUM\n\nSmart MTF + BTC + BTC.D + Regime + Relative Strength + Momentum + Breakout + Risk aktif.")
    elif cmd == "/stop":
        active_chat_id = None
        send_message(chat_id, "🛑 Otomatik tarama durduruldu. /jet ile manuel tarama başlatabilirsin.")


def telegram_updates(offset=None):
    if not TELEGRAM_BOT_TOKEN:
        return []
    try:
        params = {"timeout": 25}
        if offset is not None: params["offset"] = offset
        r = session.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35)
        if r.ok:
            return r.json().get("result", [])
    except Exception as e:
        print("Telegram polling error:", e)
    return []


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN eksik.")
    print("CRYPTO JET V13.2 başlatıldı")
    offset = None
    last_auto = 0
    while True:
        updates = telegram_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            if chat and text.startswith("/"):
                handle_command(chat, text)
        if active_chat_id and time.time() - last_auto >= SCAN_INTERVAL:
            try:
                market_scan(active_chat_id)
            except Exception as e:
                print("Automatic scan error:", e)
            last_auto = time.time()
        time.sleep(1)


if __name__ == "__main__":
    main()
