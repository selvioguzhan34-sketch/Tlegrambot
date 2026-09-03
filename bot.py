import os
import time
import math
import requests
from collections import defaultdict

# =========================================================
# CRYPTO JET V13.1 — STABLE MTF ENGINE
#
# 1H ana veri
# 2H / 4H Python aggregation
# 1D Coinbase
# BTC + BTC.D + Coin MTF confirmation
#
# 700+ ALARM
# 800+ STRONG
# 900+ ELITE
# 950+ EXTREME
# 990+ ULTRA
# 1000 MAXIMUM
# =========================================================

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN GitHub Secret bulunamadı.")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

CMC_API_KEY = os.environ.get("COINMARKETCAP_API_KEY", "").strip()

session = requests.Session()
session.headers.update({"User-Agent": "CryptoJet/13.1"})

SCAN_INTERVAL = 600
PRODUCT_REFRESH = 1800
ALERT_COOLDOWN = 3600
MIN_CANDLES = 55

VERY_STRONG_SIGNAL = 700
STRONG_SIGNAL = 800
ELITE_SIGNAL = 900
EXTREME_SIGNAL = 950
ULTRA_SIGNAL = 990
MAXIMUM_SIGNAL = 1000

TF_WEIGHTS = {"1H": 160, "2H": 190, "4H": 250, "1D": 250}
BASE_GRANULARITY = 3600
ONE_DAY = 86400

active_chat_id = None
last_scan_time = 0
last_product_refresh = 0
products_cache = []
candle_cache = {}
alert_state = {}


def safe_float(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fmt_price(p):
    if p is None or safe_float(p) <= 0:
        return "N/A"
    p = safe_float(p)
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    return f"${p:,.8f}"


# =========================================================
# TELEGRAM
# =========================================================

def send_message(chat_id, text, disable_notification=False):
    if not chat_id:
        return False
    try:
        chunks = []
        while text:
            if len(text) <= 3900:
                chunks.append(text)
                break
            cut = text.rfind("\n", 0, 3900)
            if cut <= 0:
                cut = 3900
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")

        for part in chunks:
            r = session.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": part,
                    "disable_notification": disable_notification,
                },
                timeout=20,
            )
            if r.status_code != 200:
                print("Telegram hata:", r.status_code, r.text[:200])
                return False
        return True
    except Exception as e:
        print("Telegram gönderme hatası:", e)
        return False


# =========================================================
# HTTP / COINBASE
# =========================================================

def coinbase_get(path, params=None):
    for attempt in range(5):
        try:
            r = session.get(
                COINBASE_API + path,
                params=params,
                timeout=20,
            )
            if r.status_code == 200:
                return r.json()

            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(16, 2 ** attempt)
                print("Coinbase geçici hata:", r.status_code, "bekleme:", wait)
                time.sleep(wait)
                continue

            print("Coinbase hata:", r.status_code, r.text[:200])
            return None

        except requests.RequestException as e:
            print("Coinbase bağlantı hatası:", e)
            time.sleep(min(16, 2 ** attempt))

    return None


def get_products(force=False):
    global products_cache, last_product_refresh

    now = time.time()
    if products_cache and not force and now - last_product_refresh < PRODUCT_REFRESH:
        return products_cache

    data = coinbase_get("/products")
    if not isinstance(data, list):
        return products_cache

    out = []
    for p in data:
        try:
            if p.get("quote_currency") != "USD":
                continue
            if p.get("base_currency") in {"USD", "USDC", "USDT"}:
                continue
            if p.get("status") != "online":
                continue
            if p.get("trading_disabled") or p.get("cancel_only"):
                continue
            product_id = p.get("id")
            base = p.get("base_currency")
            if product_id and base:
                out.append({"id": product_id, "base": base})
        except Exception:
            continue

    products_cache = out
    last_product_refresh = now
    print("Coinbase aktif USD coin sayısı:", len(out))
    return out


# =========================================================
# CANDLES
# =========================================================

def parse_candles(data, granularity):
    if not isinstance(data, list):
        return []

    now = time.time()
    out = []

    for row in data:
        try:
            if not isinstance(row, list) or len(row) < 6:
                continue

            ts, lo, hi, op, cl, vol = map(safe_float, row[:6])

            if ts <= 0 or ts + granularity > now:
                continue
            if min(op, cl, lo) <= 0 or hi < max(op, cl, lo) or vol < 0:
                continue

            out.append({
                "time": ts,
                "low": lo,
                "high": hi,
                "open": op,
                "close": cl,
                "volume": vol,
            })
        except Exception:
            continue

    dedup = {int(x["time"]): x for x in out}
    return [dedup[k] for k in sorted(dedup)]


def get_candles(product_id, granularity=3600, limit=300, cache_ttl=300):
    key = (product_id, granularity)
    now = time.time()

    cached = candle_cache.get(key)
    if cached and now - cached["time"] < cache_ttl:
        return cached["data"]

    data = coinbase_get(
        f"/products/{product_id}/candles",
        {"granularity": granularity},
    )
    candles = parse_candles(data, granularity)

    candle_cache[key] = {"time": now, "data": candles[-limit:]}
    return candle_cache[key]["data"]


def aggregate_candles(candles, target_seconds):
    """Aggregate exact consecutive base candles into target timeframe."""
    if not candles or target_seconds % BASE_GRANULARITY != 0:
        return []

    required = target_seconds // BASE_GRANULARITY
    buckets = defaultdict(list)

    for x in candles:
        bt = (int(x["time"]) // target_seconds) * target_seconds
        buckets[bt].append(x)

    result = []
    for bt in sorted(buckets):
        b = sorted(buckets[bt], key=lambda x: x["time"])

        # Exact contiguous hourly candles only.
        if len(b) != required:
            continue

        times = [int(x["time"]) for x in b]
        if len(set(times)) != required:
            continue
        if any(times[i] - times[i - 1] != BASE_GRANULARITY for i in range(1, len(times))):
            continue

        result.append({
            "time": bt,
            "open": b[0]["open"],
            "high": max(x["high"] for x in b),
            "low": min(x["low"] for x in b),
            "close": b[-1]["close"],
            "volume": sum(x["volume"] for x in b),
        })

    return result


def get_timeframe_candles(product_id, name):
    if name == "1H":
        return get_candles(product_id, 3600, 300)

    if name == "2H":
        base = get_candles(product_id, 3600, 300)
        return aggregate_candles(base, 7200)[-150:]

    if name == "4H":
        base = get_candles(product_id, 3600, 300)
        return aggregate_candles(base, 14400)[-100:]

    if name == "1D":
        return get_candles(product_id, 86400, 120)

    return []


# =========================================================
# INDICATORS
# =========================================================

def ema(a, n):
    if len(a) < n:
        return None
    k = 2 / (n + 1)
    x = sum(a[:n]) / n
    for v in a[n:]:
        x = (v - x) * k + x
    return x


def rsi(a, n=14):
    if len(a) <= n:
        return None

    gains, losses = [], []
    for i in range(1, len(a)):
        d = a[i] - a[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n

    if avg_loss == 0:
        return 100.0

    return 100 - 100 / (1 + avg_gain / avg_loss)


def macd(a):
    if len(a) < 40:
        return None, None, None

    values = []
    for i in range(26, len(a) + 1):
        e12 = ema(a[:i], 12)
        e26 = ema(a[:i], 26)
        if e12 is not None and e26 is not None:
            values.append(e12 - e26)

    if len(values) < 9:
        return None, None, None

    signal = ema(values, 9)
    current = values[-1]
    return current, signal, current - signal


def atr(c, n=14):
    if len(c) <= n:
        return None

    tr = []
    for i in range(1, len(c)):
        x, y = c[i], c[i - 1]
        tr.append(max(
            x["high"] - x["low"],
            abs(x["high"] - y["close"]),
            abs(x["low"] - y["close"]),
        ))

    return sum(tr[-n:]) / n if len(tr) >= n else None


def adx(c, n=14):
    if len(c) < n + 2:
        return None

    tr, plus_dm, minus_dm = [], [], []

    for i in range(1, len(c)):
        x, y = c[i], c[i - 1]
        up = x["high"] - y["high"]
        down = y["low"] - x["low"]

        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        tr.append(max(
            x["high"] - x["low"],
            abs(x["high"] - y["close"]),
            abs(x["low"] - y["close"]),
        ))

    t = sum(tr[-n:]) / n
    if t <= 0:
        return 0.0

    plus_di = 100 * (sum(plus_dm[-n:]) / n) / t
    minus_di = 100 * (sum(minus_dm[-n:]) / n) / t

    if plus_di + minus_di == 0:
        return 0.0

    return 100 * abs(plus_di - minus_di) / (plus_di + minus_di)


def volume_direction(c, n=20):
    if len(c) < n:
        return 50.0, 50.0, "NÖTR"

    long_volume = short_volume = 0.0

    for x in c[-n:]:
        rng = x["high"] - x["low"]
        body = abs(x["close"] - x["open"])
        q = 0.5 if rng <= 0 else clamp(body / rng, 0, 1)

        if x["close"] > x["open"]:
            long_volume += x["volume"] * (0.5 + 0.5 * q)
            short_volume += x["volume"] * (0.5 - 0.5 * q)
        elif x["close"] < x["open"]:
            short_volume += x["volume"] * (0.5 + 0.5 * q)
            long_volume += x["volume"] * (0.5 - 0.5 * q)
        else:
            long_volume += x["volume"] * 0.5
            short_volume += x["volume"] * 0.5

    total = long_volume + short_volume
    if total <= 0:
        return 50.0, 50.0, "NÖTR"

    lp = long_volume / total * 100
    sp = short_volume / total * 100

    if lp - sp >= 8:
        direction = "LONG"
    elif sp - lp >= 8:
        direction = "SHORT"
    else:
        direction = "NÖTR"

    return lp, sp, direction


def volume_ratio(c, n=20):
    if len(c) < n + 1:
        return 1.0

    avg = sum(x["volume"] for x in c[-n - 1:-1]) / n
    return c[-1]["volume"] / avg if avg > 0 else 1.0


def stoch_rsi(values, period=14):
    if len(values) < period * 2:
        return None

    rsi_values = []
    for i in range(period, len(values)):
        v = rsi(values[:i + 1], period)
        if v is not None:
            rsi_values.append(v)

    if len(rsi_values) < period:
        return None

    recent = rsi_values[-period:]
    lo, hi = min(recent), max(recent)
    if hi == lo:
        return 50.0

    return (rsi_values[-1] - lo) / (hi - lo) * 100


def bollinger(values, period=20):
    if len(values) < period:
        return None, None, None

    recent = values[-period:]
    middle = sum(recent) / period
    dev = math.sqrt(sum((x - middle) ** 2 for x in recent) / period)

    return middle + 2 * dev, middle, middle - 2 * dev


def vwap(candles, period=20):
    if len(candles) < period:
        return None

    recent = candles[-period:]
    total_volume = sum(x["volume"] for x in recent)
    if total_volume <= 0:
        return None

    return sum(
        ((x["high"] + x["low"] + x["close"]) / 3) * x["volume"]
        for x in recent
    ) / total_volume


def obv_delta(candles, period=20):
    if len(candles) < period + 1:
        return 0.0

    value = 0.0
    values = []

    for i in range(1, len(candles)):
        if candles[i]["close"] > candles[i - 1]["close"]:
            value += candles[i]["volume"]
        elif candles[i]["close"] < candles[i - 1]["close"]:
            value -= candles[i]["volume"]
        values.append(value)

    return values[-1] - values[-period] if len(values) >= period else 0.0


# =========================================================
# TIMEFRAME SIGNAL
# =========================================================

def timeframe_signal(c):
    if len(c) < MIN_CANDLES:
        return "UNKNOWN", 0.0, {}

    closes = [x["close"] for x in c]
    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi_value = rsi(closes, 14)
    macd_value, macd_signal, macd_hist = macd(closes)
    adx_value = adx(c, 14)
    atr_value = atr(c, 14)
    stoch = stoch_rsi(closes, 14)
    upper, middle, lower = bollinger(closes, 20)
    vwap_value = vwap(c, 20)
    obv = obv_delta(c, 20)
    long_volume, short_volume, volume_dir = volume_direction(c, 20)
    vol_ratio = volume_ratio(c, 20)

    long_score = short_score = 0.0
    reasons_long, reasons_short = [], []

    if ema20 is not None and ema50 is not None:
        if price > ema20 > ema50:
            long_score += 40
            reasons_long.append("EMA")
        elif price < ema20 < ema50:
            short_score += 40
            reasons_short.append("EMA")
        elif price > ema50:
            long_score += 20
        elif price < ema50:
            short_score += 20

    if rsi_value is not None:
        if rsi_value >= 60:
            long_score += 20
            reasons_long.append("RSI")
        elif rsi_value >= 55:
            long_score += 10
        elif rsi_value <= 40:
            short_score += 20
            reasons_short.append("RSI")
        elif rsi_value <= 45:
            short_score += 10

    if macd_hist is not None:
        if macd_hist > 0:
            long_score += 20
            reasons_long.append("MACD")
        elif macd_hist < 0:
            short_score += 20
            reasons_short.append("MACD")

    if volume_dir == "LONG":
        long_score += 15
        reasons_long.append("Hacim")
    elif volume_dir == "SHORT":
        short_score += 15
        reasons_short.append("Hacim")

    if adx_value is not None and adx_value >= 25:
        if long_score > short_score:
            long_score += 5
            reasons_long.append("ADX")
        elif short_score > long_score:
            short_score += 5
            reasons_short.append("ADX")

    if vwap_value is not None:
        if price > vwap_value:
            long_score += 5
            reasons_long.append("VWAP")
        elif price < vwap_value:
            short_score += 5
            reasons_short.append("VWAP")

    if upper is not None and lower is not None and upper > lower:
        position = (price - lower) / (upper - lower)
        if position >= 0.65:
            long_score += 3
        elif position <= 0.35:
            short_score += 3

    if stoch is not None:
        if stoch >= 60:
            long_score += 2
        elif stoch <= 40:
            short_score += 2

    if obv > 0 and long_score >= short_score:
        long_score += 2
    elif obv < 0 and short_score >= long_score:
        short_score += 2

    if long_score > short_score and long_score - short_score >= 15:
        direction = "LONG"
    elif short_score > long_score and short_score - long_score >= 15:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    max_tf_score = 112.0
    quality = clamp(max(long_score, short_score) / max_tf_score, 0, 1)

    metadata = {
        "rsi": rsi_value,
        "adx": adx_value,
        "atr": atr_value,
        "macd": macd_value,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "stoch": stoch,
        "ema20": ema20,
        "ema50": ema50,
        "bollinger_upper": upper,
        "bollinger_middle": middle,
        "bollinger_lower": lower,
        "vwap": vwap_value,
        "obv": obv,
        "volume_direction": volume_dir,
        "volume_ratio": vol_ratio,
        "long_volume": long_volume,
        "short_volume": short_volume,
        "reasons_long": reasons_long,
        "reasons_short": reasons_short,
        "long_score": round(long_score),
        "short_score": round(short_score),
    }

    return direction, quality, metadata


# =========================================================
# MTF
# =========================================================

def analyze_mtf(product):
    frames = {}

    for name in ("1H", "2H", "4H", "1D"):
        candles = get_timeframe_candles(product["id"], name)
        direction, quality, metadata = timeframe_signal(candles)

        frames[name] = {
            "direction": direction,
            "quality": quality,
            "meta": metadata,
            "candles": candles,
        }

    directions = [frames[x]["direction"] for x in ("1H", "2H", "4H", "1D")]

    aligned = (
        directions[0] in ("LONG", "SHORT")
        and all(x == directions[0] for x in directions)
    )

    price = None
    for name in ("1H", "2H", "4H", "1D"):
        if frames[name]["candles"]:
            price = frames[name]["candles"][-1]["close"]
            break

    if not aligned:
        return {
            "product": product,
            "frames": frames,
            "direction": "NEUTRAL",
            "quality": 0.0,
            "aligned": False,
            "price": price,
        }

    weighted = sum(
        frames[x]["quality"] * TF_WEIGHTS[x]
        for x in ("1H", "2H", "4H", "1D")
    )
    quality = clamp(weighted / sum(TF_WEIGHTS.values()), 0, 1)

    return {
        "product": product,
        "frames": frames,
        "direction": directions[0],
        "quality": quality,
        "aligned": True,
        "price": price,
    }


# =========================================================
# BTC DOMINANCE
# =========================================================

def get_btc_dominance():
    if CMC_API_KEY:
        try:
            r = session.get(
                "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest",
                headers={"X-CMC_PRO_API_KEY": CMC_API_KEY},
                timeout=20,
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                value = safe_float(data.get("btc_dominance"), 0)
                if value > 0:
                    return value, "CMC"
        except Exception as e:
            print("CMC BTC.D:", e)

    try:
        r = session.get(f"{COINGECKO_API}/global", timeout=20)
        if r.status_code == 200:
            data = r.json().get("data", {})
            value = safe_float(
                data.get("market_cap_percentage", {}).get("btc"),
                0,
            )
            if value > 0:
                return value, "CoinGecko"
    except Exception as e:
        print("CoinGecko BTC.D:", e)

    return None, "N/A"


def btc_d_direction():
    dominance, source = get_btc_dominance()
    products = get_products()

    btc = next((p for p in products if p["base"] == "BTC"), None)
    if not btc or dominance is None:
        return dominance, "UNKNOWN", source

    btc_candles = get_candles(btc["id"], 3600, 120)
    if len(btc_candles) < 25:
        return dominance, "UNKNOWN", source

    btc_return = (
        btc_candles[-1]["close"] / btc_candles[-25]["close"] - 1
    ) * 100

    returns = []
    # Proxy only: this is not historical BTC.D itself.
    for p in products:
        if p["base"] == "BTC":
            continue

        candles = get_candles(p["id"], 3600, 30)
        if len(candles) >= 25 and candles[-25]["close"] > 0:
            coin_return = (
                candles[-1]["close"] / candles[-25]["close"] - 1
            ) * 100
            returns.append(coin_return)

        if len(returns) >= 30:
            break

    if not returns:
        return dominance, "UNKNOWN", source

    alt_average = sum(returns) / len(returns)
    difference = btc_return - alt_average

    if difference >= 1.0:
        direction = "RISING"
    elif difference <= -1.0:
        direction = "FALLING"
    else:
        direction = "FLAT"

    return dominance, direction, source


def btc_context():
    products = get_products()
    btc = next((p for p in products if p["base"] == "BTC"), None)
    if not btc:
        return None

    result = analyze_mtf(btc)
    dominance, dom_direction, source = btc_d_direction()

    result["btc_dominance"] = dominance
    result["btc_d_direction"] = dom_direction
    result["btc_d_source"] = source
    return result


# =========================================================
# REAL 0-1000 SCORE
# =========================================================

def score_result(mtf, btc_result):
    direction = mtf["direction"]
    if direction not in ("LONG", "SHORT"):
        return 0, False, False

    # Base MTF component: max 500.
    mtf_component = mtf["quality"] * 500

    # Directional BTC confirmation: max 200.
    btc_component = 0
    if btc_result["aligned"] and direction == btc_result["direction"]:
        btc_component = 200
    elif btc_result["aligned"]:
        btc_component = 0

    # Dominance component: max 150.
    dom_direction = btc_result["btc_d_direction"]
    dominance_ok = (
        (direction == "LONG" and dom_direction == "FALLING")
        or (direction == "SHORT" and dom_direction == "RISING")
    )
    dominance_component = 150 if dominance_ok else 0

    # Momentum/volume component: max 75.
    one_h = mtf["frames"]["1H"]["meta"]
    momentum_component = 0

    rsi_value = one_h.get("rsi")
    adx_value = one_h.get("adx")
    vol_ratio = one_h.get("volume_ratio", 1)

    if direction == "LONG":
        if rsi_value is not None and 55 <= rsi_value <= 75:
            momentum_component += 30
    else:
        if rsi_value is not None and 25 <= rsi_value <= 45:
            momentum_component += 30

    if adx_value is not None:
        momentum_component += min(25, max(0, (adx_value - 20) * 1.25))

    if vol_ratio >= 1.2:
        momentum_component += 20

    momentum_component = min(75, momentum_component)

    # Cross-timeframe agreement bonus: max 75.
    confirmation_component = 0
    if mtf["aligned"]:
        confirmation_component += 50
    if btc_result["aligned"]:
        confirmation_component += 25

    raw = (
        mtf_component
        + btc_component
        + dominance_component
        + momentum_component
        + confirmation_component
    )

    score = int(round(clamp(raw, 0, 1000)))

    strict = (
        mtf["aligned"]
        and btc_result["aligned"]
        and direction == btc_result["direction"]
        and dominance_ok
    )

    # 900+ is a strict gate.
    if not strict:
        score = min(score, 899)

    return score, strict, dominance_ok


# =========================================================
# ALERTS
# =========================================================

def build_alert(x, btc_result):
    coin = x["product"]["base"]
    score = x["score"]
    direction = x["direction"]
    dominance = btc_result["btc_dominance"]

    if score >= 1000:
        level = "MAXIMUM"
    elif score >= 990:
        level = "ULTRA"
    elif score >= 950:
        level = "EXTREME"
    else:
        level = "ELITE"

    emoji = "🟢" if direction == "LONG" else "🔴"
    dom_text = f"{dominance:.2f}%" if dominance is not None else "N/A"

    return f"""🚀 JET ALARM — {level}
━━━━━━━━━━━━━━━━

🪙 {coin}

{emoji} Yön: {direction}
💪 Güç: %{score / 10:.0f}
📊 JET SCORE: {score}/1000

₿ BTC 1H/2H/4H/1D: {btc_result['direction']} ✅
🪙 Coin 1H/2H/4H/1D: {direction} ✅

₿ BTC.D: {dom_text}
📈 BTC.D proxy trend: {btc_result['btc_d_direction']} ✅

💰 Fiyat: {fmt_price(x['price'])}

🔥 STRICT MTF TEYİT
🔥 BTC TEYİDİ
🔥 BTC.D TEYİDİ

⚠️ Bu alarm garanti kâr anlamına gelmez."""


def should_alert(x):
    if x["score"] < 900:
        return False

    key = (x["direction"], x["score"] // 10)
    now = time.time()
    old = alert_state.get(x["product"]["id"])

    if old and old["key"] == key and now - old["time"] < ALERT_COOLDOWN:
        return False

    alert_state[x["product"]["id"]] = {"key": key, "time": now}
    return True


# =========================================================
# MARKET SCAN
# =========================================================

def market_scan(chat_id=None, send_alerts=False):
    products = get_products()
    btc_result = btc_context()

    if not products or not btc_result:
        if chat_id:
            send_message(chat_id, "❌ Piyasa/BTC verisi alınamadı.")
        return []

    results = []
    strong = []

    print("🚀 V13.1 MTF tarama başladı:", len(products))

    for i, product in enumerate(products, 1):
        if product["base"] == "BTC":
            continue

        try:
            mtf = analyze_mtf(product)
            score, eligible, dominance_ok = score_result(mtf, btc_result)

            mtf.update({
                "score": score,
                "eligible": eligible,
                "dominance_ok": dominance_ok,
            })
            results.append(mtf)

            if score >= 900 and eligible:
                strong.append(mtf)

            print(
                f"[{i}/{len(products)}] "
                f"{product['base']} {mtf['direction']} "
                f"score={score} eligible={eligible}"
            )

        except Exception as e:
            print(product["id"], "analiz hatası:", e)

    strong.sort(key=lambda x: x["score"], reverse=True)

    if chat_id and send_alerts:
        for x in strong:
            if should_alert(x):
                send_message(chat_id, build_alert(x, btc_result))

    if chat_id:
        dom = btc_result["btc_dominance"]
        dom_text = f"{dom:.2f}%" if dom is not None else "N/A"

        text = f"""🚀 CRYPTO JET V13.1
━━━━━━━━━━━━━━━━

🪙 Analiz: {len(results)} coin
🚨 900+ tam teyit: {len(strong)}

₿ BTC: {btc_result['direction']}
🟣 BTC.D: {dom_text}
📈 BTC.D proxy: {btc_result['btc_d_direction']}

"""

        for x in strong[:20]:
            emoji = "🟢" if x["direction"] == "LONG" else "🔴"
            text += (
                f"{x['product']['base']:<10} "
                f"{emoji} {x['direction']} "
                f"{x['score']}/1000\n"
            )

        if not strong:
            text += "Şu an 900+ strict teyit yok.\n"

        send_message(chat_id, text)

    return results


# =========================================================
# BTC
# =========================================================

def btc_analysis(chat_id):
    btc = btc_context()

    if not btc:
        send_message(chat_id, "❌ BTC analizi alınamadı.")
        return

    dominance = btc["btc_dominance"]
    dominance_text = f"{dominance:.2f}%" if dominance is not None else "N/A"

    lines = "\n".join(
        f"{name}: {data['direction']}"
        for name, data in btc["frames"].items()
    )

    send_message(
        chat_id,
        f"""₿ CRYPTO JET V13.1 — BTC
━━━━━━━━━━━━━━━━

Yön: {btc['direction']}

Tam 4 zaman dilimi:
{'EVET' if btc['aligned'] else 'HAYIR'}

{lines}

BTC.D: {dominance_text}
BTC.D proxy yönü: {btc['btc_d_direction']}
Kaynak: {btc['btc_d_source']}

💰 Fiyat: {fmt_price(btc['price'])}"""
    )


def send_status(chat_id):
    send_message(
        chat_id,
        f"""🚀 CRYPTO JET V13.1
━━━━━━━━━━━━━━━━

Durum:
{'🟢 AKTİF' if active_chat_id else '🟡 BEKLEMEDE'}

Coin: {len(get_products())}

₿ BTC:
1H + 2H + 4H + 1D

📊 Coin:
1H + 2H + 4H + 1D

🟣 BTC.D:
DEĞER + PROXY TREND

🚨 900+:
SADECE STRICT TEYİT

⏱ Tarama:
10 dakika

⚠️ Sinyal garanti kâr değildir."""
    )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_command(chat_id, text):
    global active_chat_id, last_scan_time

    command = text.strip().lower()

    if command == "/start":
        send_message(
            chat_id,
            """🚀 CRYPTO JET V13.1

/jet → Otomatik sistemi başlat
/btc → BTC + BTC.D + 1H/2H/4H/1D
/scan → Tam tarama
/status → Sistem durumu
/stop → Durdur

🚨 900+ yalnızca strict tam teyitte."""
        )

    elif command == "/jet":
        active_chat_id = chat_id
        send_message(
            chat_id,
            """🚀 CRYPTO JET V13.1 AKTİF

₿ BTC
🟣 BTC.D
⏱ 1H
⏱ 2H
⏱ 4H
⏱ 1D

🚨 900+ STRICT TAM TEYİT

İlk tarama başlıyor..."""
        )
        market_scan(chat_id, True)
        last_scan_time = time.time()

    elif command == "/btc":
        btc_analysis(chat_id)

    elif command == "/scan":
        send_message(
            chat_id,
            """🔎 BTC + BTC.D + 1H/2H/4H/1D
tam tarama başlıyor..."""
        )
        market_scan(chat_id, True)
        last_scan_time = time.time()
        send_message(chat_id, "✅ Tarama tamamlandı.")

    elif command == "/status":
        send_status(chat_id)

    elif command == "/stop":
        active_chat_id = None
        send_message(
            chat_id,
            """🛑 CRYPTO JET DURDURULDU

/jet ile tekrar başlatabilirsin."""
        )

    elif command.startswith("/"):
        send_message(
            chat_id,
            """❓ Bilinmeyen komut.

/jet
/btc
/scan
/status
/stop"""
        )


# =========================================================
# AUTOMATIC SCAN
# =========================================================

def automatic_scan():
    global last_scan_time

    if not active_chat_id:
        return

    if time.time() - last_scan_time < SCAN_INTERVAL:
        return

    market_scan(active_chat_id, True)
    last_scan_time = time.time()


# =========================================================
# MAIN
# =========================================================

def main():
    print("🚀 CRYPTO JET V13.1 BAŞLADI")
    offset = 0

    while True:
        try:
            response = session.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"offset": offset, "timeout": 10},
                timeout=15,
            )

            if response.status_code != 200:
                time.sleep(5)
                continue

            data = response.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")

                if message and message.get("text"):
                    handle_command(
                        message["chat"]["id"],
                        message["text"],
                    )

            automatic_scan()
            time.sleep(1)

        except KeyboardInterrupt:
            print("🛑 CRYPTO JET KAPATILDI.")
            break

        except Exception as e:
            print("ANA DÖNGÜ HATASI:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
