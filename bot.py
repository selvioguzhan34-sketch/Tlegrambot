import os, time, math, requests

# =========================================================
# CRYPTO JET V13.0
#
# BTC + BTC.D + 1H / 2H / 4H / 1D
# STRICT MULTI-TIMEFRAME CONFIRMATION
#
# 700+  ALARM
# 800+  STRONG
# 900+  ELITE
# 950+  EXTREME
# 990+  ULTRA
# 1000   MAXIMUM
#
# 900+ SINYAL İÇİN:
# - Coin 1H/2H/4H/1D aynı yönde
# - BTC  1H/2H/4H/1D aynı yönde
# - Coin yönü BTC yönüyle aynı
# - BTC.D LONG için düşüyor
# - BTC.D SHORT için yükseliyor
# =========================================================

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN GitHub Secret bulunamadı.")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

CMC_API_KEY = os.environ.get("COINMARKETCAP_API_KEY", "").strip()

session = requests.Session()
session.headers.update({
    "User-Agent": "CryptoJet/13.0"
})

# =========================================================
# AYARLAR
# =========================================================

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

TIMEFRAMES = {
    "1H": 3600,
    "2H": 7200,
    "4H": 14400,
    "1D": 86400
}

TF_WEIGHTS = {
    "1H": 160,
    "2H": 190,
    "4H": 250,
    "1D": 250
}

active_chat_id = None
last_scan_time = 0
last_product_refresh = 0
products_cache = []
alert_state = {}


# =========================================================
# YARDIMCI FONKSİYONLAR
# =========================================================

def safe_float(v, d=0.0):
    try:
        return float(v)
    except:
        return d


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def score_percent(s):
    return clamp(s / 10, 0, 100)


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
                    "disable_notification": disable_notification
                },
                timeout=20
            )

            if r.status_code != 200:
                return False

        return True

    except Exception as e:

        print("Telegram gönderme hatası:", e)
        return False


# =========================================================
# COINBASE API
# =========================================================

def coinbase_get(path, params=None):

    for attempt in range(4):

        try:

            r = session.get(
                COINBASE_API + path,
                params=params,
                timeout=20
            )

            if r.status_code == 200:
                return r.json()

            if r.status_code == 429:

                time.sleep(2 ** attempt)
                continue

            print(
                "Coinbase hata:",
                r.status_code,
                r.text[:150]
            )

        except Exception as e:

            print(
                "Coinbase bağlantı hatası:",
                e
            )

            time.sleep(2)

    return None


# =========================================================
# COIN LİSTESİ
# =========================================================

def get_products(force=False):

    global products_cache
    global last_product_refresh

    now = time.time()

    if (
        products_cache
        and not force
        and now - last_product_refresh < PRODUCT_REFRESH
    ):
        return products_cache

    data = coinbase_get("/products")

    if not isinstance(data, list):
        return products_cache

    out = []

    for p in data:

        try:

            if p.get("quote_currency") != "USD":
                continue

            if p.get("base_currency") in {
                "USD",
                "USDC",
                "USDT"
            }:
                continue

            if p.get("status") != "online":
                continue

            if p.get("trading_disabled"):
                continue

            if p.get("cancel_only"):
                continue

            out.append({
                "id": p.get("id"),
                "base": p.get("base_currency")
            })

        except:
            pass

    products_cache = out
    last_product_refresh = now

    print(
        "Coinbase aktif USD coin sayısı:",
        len(out)
    )

    return out


# =========================================================
# CANDLE VERİSİ
# =========================================================

def get_candles(product_id, granularity=3600):

    data = coinbase_get(
        f"/products/{product_id}/candles",
        {
            "granularity": granularity
        }
    )

    if not isinstance(data, list):
        return []

    now = time.time()
    out = []

    for row in data:

        if not isinstance(row, list):
            continue

        if len(row) < 6:
            continue

        ts, lo, hi, op, cl, vol = map(
            safe_float,
            row[:6]
        )

        if ts <= 0:
            continue

        if ts + granularity > now:
            continue

        if min(op, cl, lo) <= 0:
            continue

        if hi < max(op, cl, lo):
            continue

        if vol < 0:
            continue

        out.append({
            "time": ts,
            "low": lo,
            "high": hi,
            "open": op,
            "close": cl,
            "volume": vol
        })

    d = {
        int(x["time"]): x
        for x in out
    }

    return [
        d[k]
        for k in sorted(d)
    ][-300:]


# =========================================================
# EMA
# =========================================================

def ema(a, n):

    if len(a) < n:
        return None

    k = 2 / (n + 1)

    x = sum(a[:n]) / n

    for v in a[n:]:

        x = (v - x) * k + x

    return x


# =========================================================
# RSI
# =========================================================

def rsi(a, n=14):

    if len(a) <= n:
        return None

    gains = []
    losses = []

    for i in range(1, len(a)):

        d = a[i] - a[i - 1]

        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    for i in range(n, len(gains)):

        avg_gain = (
            avg_gain * (n - 1) + gains[i]
        ) / n

        avg_loss = (
            avg_loss * (n - 1) + losses[i]
        ) / n

    if avg_loss == 0:
        return 100

    return 100 - 100 / (
        1 + avg_gain / avg_loss
    )


# =========================================================
# MACD
# =========================================================

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

    return (
        current,
        signal,
        current - signal
    )


# =========================================================
# ATR
# =========================================================

def atr(c, n=14):

    if len(c) <= n:
        return None

    tr = []

    for i in range(1, len(c)):

        x = c[i]
        y = c[i - 1]

        tr.append(
            max(
                x["high"] - x["low"],
                abs(x["high"] - y["close"]),
                abs(x["low"] - y["close"])
            )
        )

    return (
        sum(tr[-n:]) / n
        if len(tr) >= n
        else None
    )


# =========================================================
# ADX
# =========================================================

def adx(c, n=14):

    if len(c) < n + 2:
        return None

    tr = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(c)):

        x = c[i]
        y = c[i - 1]

        up = x["high"] - y["high"]
        down = y["low"] - x["low"]

        plus_dm.append(
            up if up > down and up > 0 else 0
        )

        minus_dm.append(
            down if down > up and down > 0 else 0
        )

        tr.append(
            max(
                x["high"] - x["low"],
                abs(x["high"] - y["close"]),
                abs(x["low"] - y["close"])
            )
        )

    t = sum(tr[-n:]) / n

    if t <= 0:
        return 0

    plus_di = (
        100 * (sum(plus_dm[-n:]) / n) / t
    )

    minus_di = (
        100 * (sum(minus_dm[-n:]) / n) / t
    )

    if plus_di + minus_di == 0:
        return 0

    return (
        100
        * abs(plus_di - minus_di)
        / (plus_di + minus_di)
    )


# =========================================================
# HACİM YÖNÜ
# =========================================================

def volume_direction(c, n=20):

    if len(c) < n:
        return 50, 50, "NÖTR"

    long_volume = 0
    short_volume = 0

    for x in c[-n:]:

        rng = x["high"] - x["low"]

        body = abs(
            x["close"] - x["open"]
        )

        q = (
            0.5
            if rng <= 0
            else clamp(body / rng, 0, 1)
        )

        if x["close"] > x["open"]:

            long_volume += (
                x["volume"]
                * (0.5 + 0.5 * q)
            )

            short_volume += (
                x["volume"]
                * (0.5 - 0.5 * q)
            )

        elif x["close"] < x["open"]:

            short_volume += (
                x["volume"]
                * (0.5 + 0.5 * q)
            )

            long_volume += (
                x["volume"]
                * (0.5 - 0.5 * q)
            )

        else:

            long_volume += x["volume"] * 0.5
            short_volume += x["volume"] * 0.5

    total = long_volume + short_volume

    if total <= 0:
        return 50, 50, "NÖTR"

    long_percent = (
        long_volume / total * 100
    )

    short_percent = (
        short_volume / total * 100
    )

    if long_percent - short_percent >= 8:
        direction = "LONG"

    elif short_percent - long_percent >= 8:
        direction = "SHORT"

    else:
        direction = "NÖTR"

    return (
        long_percent,
        short_percent,
        direction
    )


# =========================================================
# VOLUME RATIO
# =========================================================

def volume_ratio(c, n=20):

    if len(c) < n + 1:
        return 1

    avg = (
        sum(
            x["volume"]
            for x in c[-n - 1:-1]
        ) / n
    )

    if avg <= 0:
        return 1

    return c[-1]["volume"] / avg


# =========================================================
# 4H AGGREGATION — V12 UYUMLULUĞU
# =========================================================

def aggregate_4h(candles):

    if len(candles) < 4:
        return []

    buckets = {}

    for x in candles:

        bt = (
            int(x["time"]) // 14400
        ) * 14400

        buckets.setdefault(
            bt,
            []
        ).append(x)

    result = []

    for bt in sorted(buckets):

        b = sorted(
            buckets[bt],
            key=lambda x: x["time"]
        )

        if len({
            int(x["time"])
            for x in b
        }) != 4:
            continue

        result.append({
            "time": bt,
            "open": b[0]["open"],
            "high": max(
                x["high"] for x in b
            ),
            "low": min(
                x["low"] for x in b
            ),
            "close": b[-1]["close"],
            "volume": sum(
                x["volume"] for x in b
            )
        })

    return result


# =========================================================
# STOCH RSI
# =========================================================

def stoch_rsi(values, period=14):

    if len(values) < period * 2:
        return None

    rsi_values = []

    for i in range(period, len(values)):

        value = rsi(
            values[:i + 1],
            period
        )

        if value is not None:
            rsi_values.append(value)

    if len(rsi_values) < period:
        return None

    recent = rsi_values[-period:]

    lo = min(recent)
    hi = max(recent)

    if hi == lo:
        return 50

    return (
        (rsi_values[-1] - lo)
        / (hi - lo)
        * 100
    )


# =========================================================
# BOLLINGER
# =========================================================

def bollinger(values, period=20):

    if len(values) < period:
        return None, None, None

    recent = values[-period:]

    middle = sum(recent) / period

    dev = math.sqrt(
        sum(
            (x - middle) ** 2
            for x in recent
        ) / period
    )

    return (
        middle + 2 * dev,
        middle,
        middle - 2 * dev
    )


# =========================================================
# VWAP
# =========================================================

def vwap(candles, period=20):

    if len(candles) < period:
        return None

    recent = candles[-period:]

    total_volume = sum(
        x["volume"]
        for x in recent
    )

    if total_volume <= 0:
        return None

    return sum(
        (
            (
                x["high"]
                + x["low"]
                + x["close"]
            ) / 3
        ) * x["volume"]
        for x in recent
    ) / total_volume


# =========================================================
# OBV
# =========================================================

def obv_delta(candles, period=20):

    if len(candles) < period + 1:
        return 0

    value = 0
    values = []

    for i in range(1, len(candles)):

        if (
            candles[i]["close"]
            > candles[i - 1]["close"]
        ):
            value += candles[i]["volume"]

        elif (
            candles[i]["close"]
            < candles[i - 1]["close"]
        ):
            value -= candles[i]["volume"]

        values.append(value)

    if len(values) < period:
        return 0

    return (
        values[-1]
        - values[-period]
    )


# =========================================================
# TEK ZAMAN DİLİMİ ANALİZİ
#
# EMA
# RSI
# MACD
# ADX
# ATR
# STOCH RSI
# BOLLINGER
# VWAP
# OBV
# VOLUME
# =========================================================

def timeframe_signal(c):

    if len(c) < MIN_CANDLES:
        return "UNKNOWN", 0.0, {}

    closes = [
        x["close"]
        for x in c
    ]

    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    rsi_value = rsi(
        closes,
        14
    )

    macd_value, macd_signal, macd_hist = macd(
        closes
    )

    adx_value = adx(
        c,
        14
    )

    atr_value = atr(
        c,
        14
    )

    stoch = stoch_rsi(
        closes,
        14
    )

    upper, middle, lower = bollinger(
        closes,
        20
    )

    vwap_value = vwap(
        c,
        20
    )

    obv = obv_delta(
        c,
        20
    )

    long_volume, short_volume, volume_dir = volume_direction(
        c,
        20
    )

    vol_ratio = volume_ratio(
        c,
        20
    )

    long_score = 0.0
    short_score = 0.0

    reasons_long = []
    reasons_short = []

    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------

    if ema20 is not None and ema50 is not None:

        if price > ema20 > ema50:

            long_score += 40
            reasons_long.append("EMA trend")

        elif price < ema20 < ema50:

            short_score += 40
            reasons_short.append("EMA trend")

        elif price > ema50:

            long_score += 20

        elif price < ema50:

            short_score += 20

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    if macd_hist is not None:

        if macd_hist > 0:

            long_score += 20
            reasons_long.append("MACD")

        elif macd_hist < 0:

            short_score += 20
            reasons_short.append("MACD")

    # -----------------------------------------------------
    # VOLUME
    # -----------------------------------------------------

    if volume_dir == "LONG":

        long_score += 15
        reasons_long.append("Hacim")

    elif volume_dir == "SHORT":

        short_score += 15
        reasons_short.append("Hacim")

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    if adx_value is not None and adx_value >= 25:

        if long_score > short_score:

            long_score += 5
            reasons_long.append("ADX")

        elif short_score > long_score:

            short_score += 5
            reasons_short.append("ADX")

    # -----------------------------------------------------
    # VWAP
    # -----------------------------------------------------

    if vwap_value is not None:

        if price > vwap_value:

            long_score += 5
            reasons_long.append("VWAP")

        elif price < vwap_value:

            short_score += 5
            reasons_short.append("VWAP")

    # -----------------------------------------------------
    # BOLLINGER
    # -----------------------------------------------------

    if (
        upper is not None
        and lower is not None
        and upper > lower
    ):

        position = (
            (price - lower)
            / (upper - lower)
        )

        if position >= 0.65:
            long_score += 3

        elif position <= 0.35:
            short_score += 3

    # -----------------------------------------------------
    # STOCH RSI
    # -----------------------------------------------------

    if stoch is not None:

        if stoch >= 60:
            long_score += 2

        elif stoch <= 40:
            short_score += 2

    # -----------------------------------------------------
    # OBV
    # -----------------------------------------------------

    if obv > 0 and long_score >= short_score:

        long_score += 2

    elif obv < 0 and short_score >= long_score:

        short_score += 2

    # -----------------------------------------------------
    # YÖN
    # -----------------------------------------------------

    if (
        long_score > short_score
        and long_score - short_score >= 15
    ):

        direction = "LONG"

    elif (
        short_score > long_score
        and short_score - long_score >= 15
    ):

        direction = "SHORT"

    else:

        direction = "NEUTRAL"

    quality = min(
        max(
            long_score,
            short_score
        ) / 112.0,
        1.0
    )

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
        "short_score": round(short_score)
    }

    return (
        direction,
        quality,
        metadata
    )


# =========================================================
# MULTI TIMEFRAME ANALİZ
# =========================================================

def analyze_mtf(product):

    frames = {}

    for name, granularity in TIMEFRAMES.items():

        candles = get_candles(
            product["id"],
            granularity
        )

        direction, quality, metadata = timeframe_signal(
            candles
        )

        frames[name] = {
            "direction": direction,
            "quality": quality,
            "meta": metadata,
            "candles": candles
        }

        time.sleep(0.03)

    directions = [
        frames[x]["direction"]
        for x in TIMEFRAMES
    ]

    aligned = (
        directions[0] in ("LONG", "SHORT")
        and all(
            x == directions[0]
            for x in directions
        )
    )

    price = None

    for name in TIMEFRAMES:

        if frames[name]["candles"]:

            price = frames[name]["candles"][-1]["close"]
            break

    if not aligned:

        return {
            "product": product,
            "frames": frames,
            "direction": "NEUTRAL",
            "quality": 0,
            "aligned": False,
            "price": price
        }

    quality = (
        sum(
            frames[x]["quality"]
            * TF_WEIGHTS[x]
            for x in TIMEFRAMES
        )
        / 850
    )

    return {
        "product": product,
        "frames": frames,
        "direction": directions[0],
        "quality": clamp(
            quality,
            0,
            1
        ),
        "aligned": True,
        "price": price
    }


# =========================================================
# BTC DOMINANCE
# =========================================================

def get_btc_dominance():

    # -----------------------------------------------------
    # COINMARKETCAP
    # -----------------------------------------------------

    try:

        if CMC_API_KEY:

            r = session.get(
                "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest",
                headers={
                    "X-CMC_PRO_API_KEY": CMC_API_KEY
                },
                timeout=20
            )

            if r.status_code == 200:

                data = r.json()["data"]

                return (
                    safe_float(
                        data.get("btc_dominance")
                    ),
                    "CMC"
                )

    except Exception as e:

        print(
            "CMC BTC.D:",
            e
        )

    # -----------------------------------------------------
    # COINGECKO FALLBACK
    # -----------------------------------------------------

    try:

        r = session.get(
            COINGECKO_API + "/global",
            timeout=20
        )

        if r.status_code == 200:

            data = r.json()["data"]

            return (
                safe_float(
                    data[
                        "market_cap_percentage"
                    ].get("btc")
                ),
                "CoinGecko"
            )

    except Exception as e:

        print(
            "CoinGecko BTC.D:",
            e
        )

    return None, "N/A"


# =========================================================
# BTC.D YÖNÜ
#
# Not:
# CMC/CoinGecko burada mevcut BTC.D değerini verir.
# Tarihsel BTC.D serisi olmadığı durumda yön,
# BTC ile altcoin sepeti momentum farkından tahmin edilir.
# =========================================================

def btc_d_direction():

    btcdom, source = get_btc_dominance()

    products = get_products()

    btc = next(
        (
            p for p in products
            if p["base"] == "BTC"
        ),
        None
    )

    if not btc or btcdom is None:

        return (
            btcdom,
            "UNKNOWN",
            source
        )

    btc_candles = get_candles(
        btc["id"],
        3600
    )

    if len(btc_candles) < 25:

        return (
            btcdom,
            "UNKNOWN",
            source
        )

    btc_return = (
        btc_candles[-1]["close"]
        / btc_candles[-25]["close"]
        - 1
    ) * 100

    returns = []

    for p in products[:80]:

        if p["base"] == "BTC":
            continue

        candles = get_candles(
            p["id"],
            3600
        )

        if (
            len(candles) >= 25
            and candles[-25]["close"] > 0
        ):

            coin_return = (
                candles[-1]["close"]
                / candles[-25]["close"]
                - 1
            ) * 100

            returns.append(
                coin_return
            )

        if len(returns) >= 30:
            break

    if not returns:

        return (
            btcdom,
            "UNKNOWN",
            source
        )

    alt_average = sum(returns) / len(returns)

    difference = (
        btc_return
        - alt_average
    )

    if difference >= 1.0:

        direction = "RISING"

    elif difference <= -1.0:

        direction = "FALLING"

    else:

        direction = "FLAT"

    return (
        btcdom,
        direction,
        source
    )


# =========================================================
# BTC CONTEXT
# =========================================================

def btc_context():

    products = get_products()

    btc = next(
        (
            p for p in products
            if p["base"] == "BTC"
        ),
        None
    )

    if not btc:
        return None

    result = analyze_mtf(btc)

    dominance, dominance_direction, source = btc_d_direction()

    result["btc_dominance"] = dominance
    result["btc_d_direction"] = dominance_direction
    result["btc_d_source"] = source

    return result


# =========================================================
# SCORE
#
# ÖNCE MTF KALİTESİ
# SONRA STRICT GATE
#
# 900+ yalnızca tam teyit
# =========================================================

def score_result(mtf, btc_context_result):

    direction = mtf["direction"]

    score = int(
        round(
            mtf["quality"] * 850
        )
    )

    btc_direction = (
        btc_context_result["direction"]
    )

    strict = (
        mtf["aligned"]
        and btc_context_result["aligned"]
        and direction == btc_direction
        and direction in ("LONG", "SHORT")
    )

    # -----------------------------------------------------
    # BTC.D FİLTRESİ
    #
    # ALTCOIN LONG
    # BTC.D FALLING
    #
    # ALTCOIN SHORT
    # BTC.D RISING
    # -----------------------------------------------------

    dominance_ok = (
        (
            direction == "LONG"
            and btc_context_result[
                "btc_d_direction"
            ] == "FALLING"
        )
        or
        (
            direction == "SHORT"
            and btc_context_result[
                "btc_d_direction"
            ] == "RISING"
        )
    )

    # -----------------------------------------------------
    # STRICT 900+
    # -----------------------------------------------------

    if strict and dominance_ok:

        score = max(
            score,
            900
        )

    else:

        score = min(
            score,
            899
        )

    return (
        score,
        strict and dominance_ok,
        dominance_ok
    )


# =========================================================
# ALERT
# =========================================================

def build_alert(x, btc_context_result):

    coin = x["product"]["base"]

    score = x["score"]
    direction = x["direction"]

    dominance = (
        btc_context_result[
            "btc_dominance"
        ]
    )

    if score < 950:
        level = "ELITE"

    elif score < 990:
        level = "EXTREME"

    elif score < 1000:
        level = "ULTRA"

    else:
        level = "MAXIMUM"

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return f"""🚀 JET ALARM — {level}
━━━━━━━━━━━━━━━━

🪙 {coin}

{emoji} Yön: {direction}
💪 Güç: %{score / 10:.0f}
📊 JET SCORE: {score}/1000

₿ BTC 1H/2H/4H/1D: {btc_context_result['direction']} ✅
🪙 Coin 1H/2H/4H/1D: {direction} ✅

₿ BTC.D: {dominance:.2f}% ({btc_context_result['btc_d_direction']}) ✅

💰 Fiyat: {fmt_price(x['price'])}

🔥 STRICT MTF TEYİT
🔥 BTC TEYİDİ
🔥 BTC.D TEYİDİ

⚠️ Bu alarm garanti kâr anlamına gelmez."""


# =========================================================
# ALERT COOLDOWN
# =========================================================

def should_alert(x):

    if x["score"] < 900:
        return False

    key = (
        x["direction"],
        x["score"] // 2
    )

    now = time.time()

    old = alert_state.get(
        x["product"]["id"]
    )

    if (
        old
        and old["key"] == key
        and now - old["time"] < ALERT_COOLDOWN
    ):
        return False

    alert_state[
        x["product"]["id"]
    ] = {
        "key": key,
        "time": now
    }

    return True


# =========================================================
# MARKET SCAN
# =========================================================

def market_scan(
    chat_id=None,
    send_alerts=False
):

    products = get_products()

    btc_context_result = btc_context()

    if not products or not btc_context_result:

        if chat_id:

            send_message(
                chat_id,
                "❌ Piyasa/BTC verisi alınamadı."
            )

        return []

    results = []
    strong = []

    print(
        "🚀 V13 MTF tarama başladı:",
        len(products)
    )

    for i, product in enumerate(
        products,
        1
    ):

        if product["base"] == "BTC":
            continue

        try:

            mtf = analyze_mtf(
                product
            )

            score, eligible, dominance_ok = score_result(
                mtf,
                btc_context_result
            )

            mtf.update({
                "score": score,
                "eligible": eligible,
                "dominance_ok": dominance_ok
            })

            results.append(mtf)

            if score >= 900:
                strong.append(mtf)

            print(
                f"[{i}/{len(products)}]"
                f" {product['base']}"
                f" {mtf['direction']}"
                f" score={score}"
                f" eligible={eligible}"
            )

        except Exception as e:

            print(
                product["id"],
                "analiz hatası:",
                e
            )

    strong.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # -----------------------------------------------------
    # ELITE ALARMLAR
    # -----------------------------------------------------

    if chat_id and send_alerts:

        for x in strong:

            if should_alert(x):

                send_message(
                    chat_id,
                    build_alert(
                        x,
                        btc_context_result
                    )
                )

    # -----------------------------------------------------
    # SCAN ÖZETİ
    # -----------------------------------------------------

    if chat_id:

        text = f"""🚀 CRYPTO JET V13.0
━━━━━━━━━━━━━━━━

🪙 Analiz: {len(results)} coin
🚨 900+ tam teyit: {len(strong)}

"""

        for x in strong[:20]:

            emoji = (
                "🟢"
                if x["direction"] == "LONG"
                else "🔴"
            )

            text += (
                f"{x['product']['base']:<10}"
                f" {emoji}"
                f" {x['direction']}"
                f" {x['score']}/1000\n"
            )

        if not strong:

            text += (
                "Şu an 900+ tam teyit yok.\n"
            )

        send_message(
            chat_id,
            text
        )

    return results


# =========================================================
# BTC ANALİZİ
# =========================================================

def btc_analysis(chat_id):

    btc = btc_context()

    if not btc:

        send_message(
            chat_id,
            "❌ BTC analizi alınamadı."
        )

        return

    dominance = btc[
        "btc_dominance"
    ]

    if dominance is not None:

        dominance_text = (
            f"{dominance:.2f}%"
        )

    else:

        dominance_text = "N/A"

    lines = "\n".join(
        f"{name}: {data['direction']}"
        for name, data
        in btc["frames"].items()
    )

    send_message(
        chat_id,
        f"""₿ CRYPTO JET V13.0 — BTC
━━━━━━━━━━━━━━━━

Yön: {btc['direction']}

Tam 4 zaman dilimi:
{'EVET' if btc['aligned'] else 'HAYIR'}

{lines}

BTC.D: {dominance_text}
BTC.D yönü: {btc['btc_d_direction']}
Kaynak: {btc['btc_d_source']}

💰 Fiyat: {fmt_price(btc['price'])}"""
    )


# =========================================================
# STATUS
# =========================================================

def send_status(chat_id):

    send_message(
        chat_id,
        f"""🚀 CRYPTO JET V13.0
━━━━━━━━━━━━━━━━

Durum:
{'🟢 AKTİF' if active_chat_id else '🟡 BEKLEMEDE'}

Coin: {len(get_products())}

₿ BTC:
1H + 2H + 4H + 1D

📊 Coin:
1H + 2H + 4H + 1D

🟣 BTC.D:
ANA FİLTRE

🚨 900+:
SADECE TAM TEYİT

⏱ Tarama:
10 dakika

⚠️ Sinyal garanti kâr değildir."""
    )


# =========================================================
# TELEGRAM KOMUTLARI
# =========================================================

def handle_command(chat_id, text):

    global active_chat_id
    global last_scan_time

    command = text.strip().lower()

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if command == "/start":

        send_message(
            chat_id,
            """🚀 CRYPTO JET V13.0

/jet → Otomatik sistemi başlat
/btc → BTC + BTC.D + 1H/2H/4H/1D
/scan → Tam tarama
/status → Sistem durumu
/stop → Durdur

🚨 900+ yalnızca tam teyitte."""
        )

    # -----------------------------------------------------
    # JET
    # -----------------------------------------------------

    elif command == "/jet":

        active_chat_id = chat_id

        send_message(
            chat_id,
            """🚀 CRYPTO JET V13.0 AKTİF

₿ BTC
🟣 BTC.D
⏱ 1H
⏱ 2H
⏱ 4H
⏱ 1D

🚨 900+ STRICT TAM TEYİT

İlk tarama başlıyor..."""
        )

        market_scan(
            chat_id,
            True
        )

        last_scan_time = time.time()

    # -----------------------------------------------------
    # BTC
    # -----------------------------------------------------

    elif command == "/btc":

        btc_analysis(
            chat_id
        )

    # -----------------------------------------------------
    # SCAN
    # -----------------------------------------------------

    elif command == "/scan":

        send_message(
            chat_id,
            """🔎 BTC + BTC.D + 1H/2H/4H/1D
tam tarama başlıyor..."""
        )

        market_scan(
            chat_id,
            True
        )

        last_scan_time = time.time()

        send_message(
            chat_id,
            "✅ Tarama tamamlandı."
        )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    elif command == "/status":

        send_status(
            chat_id
        )

    # -----------------------------------------------------
    # STOP
    # -----------------------------------------------------

    elif command == "/stop":

        active_chat_id = None

        send_message(
            chat_id,
            """🛑 CRYPTO JET DURDURULDU

/jet ile tekrar başlatabilirsin."""
        )

    # -----------------------------------------------------
    # UNKNOWN
    # -----------------------------------------------------

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
# OTOMATİK TARAMA
# =========================================================

def automatic_scan():

    global last_scan_time

    if not active_chat_id:
        return

    if (
        time.time() - last_scan_time
        < SCAN_INTERVAL
    ):
        return

    market_scan(
        active_chat_id,
        True
    )

    last_scan_time = time.time()


# =========================================================
# ANA DÖNGÜ
# =========================================================

def main():

    print(
        "🚀 CRYPTO JET V13.0 BAŞLADI"
    )

    offset = 0

    while True:

        try:

            response = session.get(
                f"{TELEGRAM_API}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 10
                },
                timeout=15
            )

            if response.status_code != 200:

                time.sleep(5)
                continue

            data = response.json()

            for update in data.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"]
                    + 1
                )

                message = update.get(
                    "message"
                )

                if (
                    message
                    and message.get("text")
                ):

                    handle_command(
                        message["chat"]["id"],
                        message["text"]
                    )

            automatic_scan()

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "🛑 CRYPTO JET KAPATILDI."
            )

            break

        except Exception as e:

            print(
                "ANA DÖNGÜ HATASI:",
                e
            )

            time.sleep(5)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
