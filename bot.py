import os
import time
import math
import requests

# =========================================================
# CRYPTO JET V12.1 — STRICT 1000 SCORE
#
# 1H ANA ZAMAN DİLİMİ
# 4H TREND TEYİDİ
#
# JET SCORE: 1 - 1000
#
# 700+   JET ALARM
# 800+   STRONG
# 900+   ELITE
# 950+   EXTREME
# 990+   ULTRA
# 1000   MAXIMUM ALIGNMENT
#
# ÖNEMLİ:
# SCORE gerçek kazanma olasılığı değildir.
# Teknik uyum / model güven skorudur.
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"

session = requests.Session()
session.headers.update({
    "User-Agent": "CryptoJet/12.1"
})


# =========================================================
# AYARLAR
# =========================================================

SCAN_INTERVAL = 600          # 10 dakika
PRODUCT_REFRESH = 1800       # 30 dakika
ALERT_COOLDOWN = 3600        # 1 saat

MIN_CANDLES = 60

# 1000 SCORE sistemindeki alarm seviyeleri
ALARM_THRESHOLD = 700
STRONG_THRESHOLD = 800
ELITE_THRESHOLD = 900
EXTREME_THRESHOLD = 950
ULTRA_THRESHOLD = 990
MAX_THRESHOLD = 1000

active_chat_id = None
last_scan_time = 0
last_product_refresh = 0

products_cache = []
alert_state = {}


# =========================================================
# YARDIMCI
# =========================================================

def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def fmt_price(price):
    if price is None:
        return "N/A"

    price = safe_float(price)

    if price <= 0:
        return "N/A"

    if price >= 1000:
        return f"${price:,.2f}"

    if price >= 1:
        return f"${price:,.4f}"

    return f"${price:,.8f}"


# =========================================================
# TELEGRAM
# =========================================================

def send_message(chat_id, text):

    if not chat_id:
        return False

    try:

        if len(text) <= 3900:

            response = session.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text
                },
                timeout=20
            )

            return response.status_code == 200

        parts = []

        while text:

            if len(text) <= 3900:
                parts.append(text)
                break

            cut = text.rfind("\n", 0, 3900)

            if cut <= 0:
                cut = 3900

            parts.append(text[:cut])
            text = text[cut:].lstrip("\n")

        success = True

        for part in parts:

            response = session.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": part
                },
                timeout=20
            )

            if response.status_code != 200:
                success = False

        return success

    except Exception as e:

        print("Telegram gönderme hatası:", e)

        return False


# =========================================================
# COINBASE
# =========================================================

def coinbase_get(path, params=None):

    url = COINBASE_API + path

    for attempt in range(4):

        try:

            response = session.get(
                url,
                params=params,
                timeout=20
            )

            if response.status_code == 200:

                try:
                    return response.json()

                except Exception:

                    print(
                        "Coinbase JSON okunamadı."
                    )

                    return None

            if response.status_code == 429:

                wait = 2 ** attempt

                print(
                    f"Coinbase rate limit. "
                    f"{wait}s bekleniyor..."
                )

                time.sleep(wait)

                continue

            print(
                "Coinbase hata:",
                response.status_code,
                response.text[:200]
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

    print(
        "Coinbase coin listesi yenileniyor..."
    )

    data = coinbase_get("/products")

    if not isinstance(data, list):

        print(
            "Coinbase ürün listesi alınamadı."
        )

        return products_cache

    products = []

    for p in data:

        try:

            product_id = p.get("id", "")
            base = p.get("base_currency", "")
            quote = p.get("quote_currency", "")
            status = p.get("status", "")

            if not product_id:
                continue

            if quote != "USD":
                continue

            if base in {
                "USD",
                "USDC",
                "USDT"
            }:
                continue

            if status != "online":
                continue

            if p.get("trading_disabled", False):
                continue

            if p.get("cancel_only", False):
                continue

            products.append({
                "id": product_id,
                "base": base
            })

        except Exception:
            continue

    products_cache = products
    last_product_refresh = now

    print(
        f"Coinbase aktif USD coin sayısı: "
        f"{len(products)}"
    )

    return products


# =========================================================
# 1H CANDLE
# =========================================================

def get_candles(product_id):

    data = coinbase_get(
        f"/products/{product_id}/candles",
        {
            "granularity": 3600
        }
    )

    if not isinstance(data, list):
        return []

    candles = []

    for row in data:

        if not isinstance(row, list):
            continue

        if len(row) < 6:
            continue

        candle = {
            "time": safe_float(row[0]),
            "low": safe_float(row[1]),
            "high": safe_float(row[2]),
            "open": safe_float(row[3]),
            "close": safe_float(row[4]),
            "volume": safe_float(row[5])
        }

        if candle["close"] <= 0:
            continue

        candles.append(candle)

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


# =========================================================
# 4H
# =========================================================

def aggregate_4h(candles):

    if len(candles) < 4:
        return []

    buckets = {}

    for candle in candles:

        timestamp = int(candle["time"])

        bucket_time = (
            timestamp // 14400
        ) * 14400

        buckets.setdefault(
            bucket_time,
            []
        ).append(candle)

    result = []

    for bucket_time in sorted(buckets):

        bucket = buckets[bucket_time]

        bucket.sort(
            key=lambda x: x["time"]
        )

        if len(bucket) < 4:
            continue

        result.append({
            "time": bucket_time,
            "open": bucket[0]["open"],
            "high": max(
                x["high"] for x in bucket
            ),
            "low": min(
                x["low"] for x in bucket
            ),
            "close": bucket[-1]["close"],
            "volume": sum(
                x["volume"]
                for x in bucket
            )
        })

    return result


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = (
        sum(values[:period])
        / period
    )

    for value in values[period:]:

        result = (
            (value - result)
            * multiplier
        ) + result

    return result


# =========================================================
# RSI
# =========================================================

def rsi(values, period=14):

    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i]
            - values[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(abs(change))

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain *
                (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss *
                (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# =========================================================
# MACD
# =========================================================

def macd(values):

    if len(values) < 40:
        return None, None, None

    macd_values = []

    for i in range(
        26,
        len(values) + 1
    ):

        subset = values[:i]

        e12 = ema(
            subset,
            12
        )

        e26 = ema(
            subset,
            26
        )

        if (
            e12 is not None
            and e26 is not None
        ):

            macd_values.append(
                e12 - e26
            )

    if len(macd_values) < 9:
        return None, None, None

    signal = ema(
        macd_values,
        9
    )

    if signal is None:
        return None, None, None

    current = macd_values[-1]

    histogram = (
        current - signal
    )

    return (
        current,
        signal,
        histogram
    )


# =========================================================
# ATR
# =========================================================

def atr(candles, period=14):

    if len(candles) <= period:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            )
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return (
        sum(trs[-period:])
        / period
    )


# =========================================================
# ADX
# =========================================================

def adx(candles, period=14):

    if len(candles) < period + 2:
        return None

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        up_move = (
            current["high"]
            - previous["high"]
        )

        down_move = (
            previous["low"]
            - current["low"]
        )

        plus = (
            up_move
            if (
                up_move > down_move
                and up_move > 0
            )
            else 0
        )

        minus = (
            down_move
            if (
                down_move > up_move
                and down_move > 0
            )
            else 0
        )

        tr = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            )
        )

        trs.append(tr)
        plus_dm.append(plus)
        minus_dm.append(minus)

    if len(trs) < period:
        return None

    tr_avg = (
        sum(trs[-period:])
        / period
    )

    plus_avg = (
        sum(plus_dm[-period:])
        / period
    )

    minus_avg = (
        sum(minus_dm[-period:])
        / period
    )

    if tr_avg == 0:
        return 0

    plus_di = (
        100 *
        plus_avg /
        tr_avg
    )

    minus_di = (
        100 *
        minus_avg /
        tr_avg
    )

    denominator = (
        plus_di +
        minus_di
    )

    if denominator == 0:
        return 0

    return (
        100 *
        abs(
            plus_di -
            minus_di
        )
        /
        denominator
    )


# =========================================================
# STOCH RSI
# =========================================================

def stoch_rsi(values, period=14):

    if len(values) < period * 2:
        return None

    rsi_values = []

    for i in range(
        period,
        len(values)
    ):

        value = rsi(
            values[:i + 1],
            period
        )

        if value is not None:
            rsi_values.append(value)

    if len(rsi_values) < period:
        return None

    recent = rsi_values[-period:]

    lowest = min(recent)
    highest = max(recent)

    if highest == lowest:
        return 50

    return (
        (
            rsi_values[-1]
            - lowest
        )
        /
        (
            highest
            - lowest
        )
    ) * 100


# =========================================================
# BOLLINGER
# =========================================================

def bollinger(values, period=20):

    if len(values) < period:
        return None, None, None

    recent = values[-period:]

    middle = (
        sum(recent)
        / period
    )

    variance = sum(
        (x - middle) ** 2
        for x in recent
    ) / period

    deviation = math.sqrt(
        variance
    )

    upper = (
        middle +
        2 * deviation
    )

    lower = (
        middle -
        2 * deviation
    )

    return (
        upper,
        middle,
        lower
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

    total = 0

    for x in recent:

        typical = (
            x["high"]
            + x["low"]
            + x["close"]
        ) / 3

        total += (
            typical *
            x["volume"]
        )

    return (
        total /
        total_volume
    )


# =========================================================
# OBV
# =========================================================

def obv_delta(candles, period=20):

    if len(candles) < period + 1:
        return 0

    value = 0
    obv_values = []

    for i in range(
        1,
        len(candles)
    ):

        if (
            candles[i]["close"]
            >
            candles[i - 1]["close"]
        ):

            value += candles[i]["volume"]

        elif (
            candles[i]["close"]
            <
            candles[i - 1]["close"]
        ):

            value -= candles[i]["volume"]

        obv_values.append(value)

    if len(obv_values) < period:
        return 0

    return (
        obv_values[-1]
        - obv_values[-period]
    )


# =========================================================
# HACİM YÖNÜ
# =========================================================

def volume_direction(
    candles,
    period=20
):

    if len(candles) < period:

        return 50, 50, "NÖTR"

    recent = candles[-period:]

    long_volume = 0
    short_volume = 0

    for candle in recent:

        volume = candle["volume"]

        candle_range = (
            candle["high"]
            - candle["low"]
        )

        body = abs(
            candle["close"]
            - candle["open"]
        )

        if candle_range <= 0:

            long_volume += (
                volume * 0.5
            )

            short_volume += (
                volume * 0.5
            )

            continue

        body_ratio = clamp(
            body / candle_range,
            0,
            1
        )

        if candle["close"] > candle["open"]:

            long_volume += (
                volume *
                (
                    0.5 +
                    body_ratio * 0.5
                )
            )

            short_volume += (
                volume *
                (
                    0.5 -
                    body_ratio * 0.5
                )
            )

        elif candle["close"] < candle["open"]:

            short_volume += (
                volume *
                (
                    0.5 +
                    body_ratio * 0.5
                )
            )

            long_volume += (
                volume *
                (
                    0.5 -
                    body_ratio * 0.5
                )
            )

        else:

            long_volume += (
                volume * 0.5
            )

            short_volume += (
                volume * 0.5
            )

    total = (
        long_volume +
        short_volume
    )

    if total <= 0:
        return 50, 50, "NÖTR"

    long_pct = (
        long_volume /
        total
    ) * 100

    short_pct = (
        short_volume /
        total
    ) * 100

    difference = (
        long_pct -
        short_pct
    )

    if difference >= 8:

        direction = "LONG"

    elif difference <= -8:

        direction = "SHORT"

    else:

        direction = "NÖTR"

    return (
        long_pct,
        short_pct,
        direction
    )


# =========================================================
# HACİM ORANI
# =========================================================

def volume_ratio(
    candles,
    period=20
):

    if len(candles) < period + 1:
        return 1

    current_volume = (
        candles[-1]["volume"]
    )

    previous = candles[
        -period - 1:-1
    ]

    if not previous:
        return 1

    average = (
        sum(
            x["volume"]
            for x in previous
        )
        /
        len(previous)
    )

    if average <= 0:
        return 1

    return (
        current_volume /
        average
    )


# =========================================================
# GİRİŞ KALİTESİ
# =========================================================

def calculate_entry_quality(
    decision,
    price,
    ema20,
    atr_value,
    rsi_value,
    adx_value,
    volume_ratio_value,
    confirmation_count,
    vwap_support,
    bollinger_support,
    stoch_support
):

    if decision not in ["LONG", "SHORT"]:

        return (
            "C",
            "🟡 NET GİRİŞ YOK",
            0
        )

    quality = 0

    # -----------------------------------------------------
    # ANA TEYİT
    # -----------------------------------------------------

    quality += confirmation_count * 10

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    if adx_value is not None:

        if adx_value >= 30:
            quality += 15

        elif adx_value >= 25:
            quality += 10

        elif adx_value >= 20:
            quality += 5

    # -----------------------------------------------------
    # HACİM
    # -----------------------------------------------------

    if volume_ratio_value >= 1.50:
        quality += 15

    elif volume_ratio_value >= 1.20:
        quality += 10

    elif volume_ratio_value >= 1.00:
        quality += 5

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    if rsi_value is not None:

        if decision == "LONG":

            if 58 <= rsi_value <= 72:
                quality += 10

            elif 55 <= rsi_value < 58:
                quality += 6

            elif rsi_value > 78:
                quality -= 10

        else:

            if 28 <= rsi_value <= 42:
                quality += 10

            elif 42 < rsi_value <= 45:
                quality += 6

            elif rsi_value < 22:
                quality -= 10

    # -----------------------------------------------------
    # VWAP
    # -----------------------------------------------------

    if vwap_support:
        quality += 5

    # -----------------------------------------------------
    # BOLLINGER
    # -----------------------------------------------------

    if bollinger_support:
        quality += 5

    # -----------------------------------------------------
    # STOCH RSI
    # -----------------------------------------------------

    if stoch_support:
        quality += 5

    # -----------------------------------------------------
    # AŞIRI UZAK GİRİŞ KONTROLÜ
    # -----------------------------------------------------

    if (
        ema20 is not None
        and atr_value is not None
        and atr_value > 0
    ):

        distance = abs(
            price - ema20
        )

        atr_distance = (
            distance /
            atr_value
        )

        # Fiyat trendden çok uzaksa
        # giriş kalitesi düşürülür.

        if atr_distance <= 1.0:

            quality += 10

        elif atr_distance <= 1.8:

            quality += 5

        elif atr_distance >= 3.0:

            quality -= 20

        elif atr_distance >= 2.3:

            quality -= 10

    quality = int(
        clamp(
            quality,
            0,
            100
        )
    )

    if quality >= 90:

        return (
            "A+",
            "🔥 A+ MÜKEMMEL",
            quality
        )

    if quality >= 80:

        return (
            "A",
            "🟢 A ÇOK İYİ",
            quality
        )

    if quality >= 65:

        return (
            "B",
            "🟡 B İYİ",
            quality
        )

    if quality >= 50:

        return (
            "C",
            "🟠 C ORTA",
            quality
        )

    return (
        "D",
        "🔴 D ZAYIF",
        quality
    )


# =========================================================
# 1000 SCORE SEVİYESİ
# =========================================================

def score_label(score):

    if score >= 1000:

        return "👑 MAXIMUM ALIGNMENT"

    if score >= 990:

        return "☢️ JET ULTRA"

    if score >= 950:

        return "💥 JET EXTREME"

    if score >= 900:

        return "🚀 JET ELITE"

    if score >= 800:

        return "🔥 JET STRONG"

    if score >= 700:

        return "🟢 JET ALARM"

    if score >= 600:

        return "🟠 GÜÇLENİYOR"

    if score >= 400:

        return "🟡 İZLE"

    return "⚪ ZAYIF"


# =========================================================
# ANA ANALİZ
# =========================================================

def analyze_coin(
    product,
    candles
):

    if len(candles) < MIN_CANDLES:
        return None

    closes = [
        x["close"]
        for x in candles
    ]

    price = closes[-1]

    # =====================================================
    # 1H
    # =====================================================

    ema20 = ema(
        closes,
        20
    )

    ema50 = ema(
        closes,
        50
    )

    rsi_value = rsi(
        closes,
        14
    )

    (
        macd_value,
        macd_signal,
        macd_hist
    ) = macd(closes)

    adx_value = adx(
        candles,
        14
    )

    atr_value = atr(
        candles,
        14
    )

    stoch_value = stoch_rsi(
        closes,
        14
    )

    (
        upper_bb,
        middle_bb,
        lower_bb
    ) = bollinger(
        closes,
        20
    )

    vwap_value = vwap(
        candles,
        20
    )

    obv_value = obv_delta(
        candles,
        20
    )

    vol_ratio = volume_ratio(
        candles,
        20
    )

    (
        long_vol,
        short_vol,
        vol_direction
    ) = volume_direction(
        candles,
        20
    )

    # =====================================================
    # 4H
    # =====================================================

    candles_4h = aggregate_4h(
        candles
    )

    ema20_4h = None
    ema50_4h = None

    htf_long = False
    htf_short = False

    if len(candles_4h) >= 50:

        closes_4h = [
            x["close"]
            for x in candles_4h
        ]

        ema20_4h = ema(
            closes_4h,
            20
        )

        ema50_4h = ema(
            closes_4h,
            50
        )

        if (
            ema20_4h is not None
            and ema50_4h is not None
        ):

            if (
                price >
                ema20_4h >
                ema50_4h
            ):

                htf_long = True

            elif (
                price <
                ema20_4h <
                ema50_4h
            ):

                htf_short = True

    # =====================================================
    # HAM SKOR
    # =====================================================

    long_score = 0.0
    short_score = 0.0

    reasons_long = []
    reasons_short = []

    # =====================================================
    # 1H EMA — 250 / 1000
    # =====================================================

    if (
        ema20 is not None
        and ema50 is not None
    ):

        if price > ema20 > ema50:

            long_score += 250

            reasons_long.append(
                "1H EMA trend güçlü yukarı"
            )

        elif price < ema20 < ema50:

            short_score += 250

            reasons_short.append(
                "1H EMA trend güçlü aşağı"
            )

        elif price > ema50:

            long_score += 125

            reasons_long.append(
                "Fiyat 1H EMA50 üzerinde"
            )

        elif price < ema50:

            short_score += 125

            reasons_short.append(
                "Fiyat 1H EMA50 altında"
            )

    # =====================================================
    # 4H TREND — 200
    # =====================================================

    if htf_long:

        long_score += 200

        reasons_long.append(
            "4H trend LONG teyitli"
        )

    elif htf_short:

        short_score += 200

        reasons_short.append(
            "4H trend SHORT teyitli"
        )

    elif (
        ema50_4h is not None
        and price > ema50_4h
    ):

        long_score += 100

        reasons_long.append(
            "Fiyat 4H EMA50 üzerinde"
        )

    elif (
        ema50_4h is not None
        and price < ema50_4h
    ):

        short_score += 100

        reasons_short.append(
            "Fiyat 4H EMA50 altında"
        )

    # =====================================================
    # RSI — 150
    # =====================================================

    if rsi_value is not None:

        if rsi_value >= 60:

            long_score += 150

            reasons_long.append(
                f"RSI güçlü ({rsi_value:.1f})"
            )

        elif rsi_value >= 55:

            long_score += 80

            reasons_long.append(
                f"RSI pozitif ({rsi_value:.1f})"
            )

        elif rsi_value <= 40:

            short_score += 150

            reasons_short.append(
                f"RSI zayıf ({rsi_value:.1f})"
            )

        elif rsi_value <= 45:

            short_score += 80

            reasons_short.append(
                f"RSI negatif ({rsi_value:.1f})"
            )

    # =====================================================
    # MACD — 100
    # =====================================================

    if macd_hist is not None:

        if macd_hist > 0:

            long_score += 100

            reasons_long.append(
                "MACD pozitif"
            )

        elif macd_hist < 0:

            short_score += 100

            reasons_short.append(
                "MACD negatif"
            )

    # =====================================================
    # HACİM — 100
    # =====================================================

    if vol_direction == "LONG":

        long_score += 100

        reasons_long.append(
            "Hacim LONG'u destekliyor"
        )

    elif vol_direction == "SHORT":

        short_score += 100

        reasons_short.append(
            "Hacim SHORT'u destekliyor"
        )

    # =====================================================
    # ADX — 50
    # =====================================================

    adx_long_support = False
    adx_short_support = False

    if (
        adx_value is not None
        and adx_value >= 25
    ):

        if long_score > short_score:

            long_score += 50
            adx_long_support = True

        elif short_score > long_score:

            short_score += 50
            adx_short_support = True

    # =====================================================
    # VWAP — 50
    # =====================================================

    vwap_support_long = False
    vwap_support_short = False

    if vwap_value is not None:

        if price > vwap_value:

            long_score += 50
            vwap_support_long = True

        elif price < vwap_value:

            short_score += 50
            vwap_support_short = True

    # =====================================================
    # BOLLINGER — 50
    # =====================================================

    bollinger_long = False
    bollinger_short = False

    if (
        upper_bb is not None
        and lower_bb is not None
    ):

        band_width = (
            upper_bb -
            lower_bb
        )

        if band_width > 0:

            position = (
                price -
                lower_bb
            ) / band_width

            if position >= 0.65:

                long_score += 50
                bollinger_long = True

            elif position <= 0.35:

                short_score += 50
                bollinger_short = True

    # =====================================================
    # STOCH RSI — 50
    # =====================================================

    stoch_long = False
    stoch_short = False

    if stoch_value is not None:

        if stoch_value >= 60:

            long_score += 50
            stoch_long = True

        elif stoch_value <= 40:

            short_score += 50
            stoch_short = True

    # =====================================================
    # HAM SKOR 1000'E SIĞDIR
    # =====================================================

    long_score = clamp(
        long_score,
        0,
        1000
    )

    short_score = clamp(
        short_score,
        0,
        1000
    )

    # =====================================================
    # KARAR
    # =====================================================

    difference = abs(
        long_score -
        short_score
    )

    if difference < 150:

        decision = "BEKLE"

    elif long_score > short_score:

        decision = "LONG"

    else:

        decision = "SHORT"

    raw_strength = int(
        round(
            max(
                long_score,
                short_score
            )
        )
    )

    # =====================================================
    # ANA TEYİT SAYISI
    # =====================================================

    confirmation_count = 0

    if decision == "LONG":

        if (
            ema20 is not None
            and price > ema20
        ):
            confirmation_count += 1

        if htf_long:
            confirmation_count += 1

        if (
            rsi_value is not None
            and rsi_value >= 55
        ):
            confirmation_count += 1

        if (
            macd_hist is not None
            and macd_hist > 0
        ):
            confirmation_count += 1

        if vol_direction == "LONG":
            confirmation_count += 1

    elif decision == "SHORT":

        if (
            ema20 is not None
            and price < ema20
        ):
            confirmation_count += 1

        if htf_short:
            confirmation_count += 1

        if (
            rsi_value is not None
            and rsi_value <= 45
        ):
            confirmation_count += 1

        if (
            macd_hist is not None
            and macd_hist < 0
        ):
            confirmation_count += 1

        if vol_direction == "SHORT":
            confirmation_count += 1

    # =====================================================
    # ÇELİŞKİLER
    # =====================================================

    conflict = False
    conflict_reasons = []

    if decision == "LONG":

        if htf_short:

            conflict = True

            conflict_reasons.append(
                "4H trend ters"
            )

        if vol_direction == "SHORT":

            conflict = True

            conflict_reasons.append(
                "Hacim ters"
            )

    elif decision == "SHORT":

        if htf_long:

            conflict = True

            conflict_reasons.append(
                "4H trend ters"
            )

        if vol_direction == "LONG":

            conflict = True

            conflict_reasons.append(
                "Hacim ters"
            )

    if vol_ratio < 0.70:

        conflict = True

        conflict_reasons.append(
            "Hacim çok düşük"
        )

    if (
        adx_value is not None
        and adx_value < 18
    ):

        conflict = True

        conflict_reasons.append(
            "Trend gücü düşük"
        )

    # =====================================================
    # STRICT 1000 SCORE
    # =====================================================

    strength = raw_strength

    # -----------------------------------------------------
    # NET YÖN YOK
    # -----------------------------------------------------

    if decision == "BEKLE":

        strength = min(
            strength,
            599
        )

    # -----------------------------------------------------
    # 5/5 ANA TEYİT YOKSA 900 YOK
    # -----------------------------------------------------

    elif confirmation_count < 4:

        strength = min(
            strength,
            699
        )

    elif confirmation_count == 4:

        strength = min(
            strength,
            899
        )

    # -----------------------------------------------------
    # 5/5 ANA TEYİT
    # -----------------------------------------------------

    elif confirmation_count == 5:

        strength = min(
            strength,
            949
        )

    # =====================================================
    # YARDIMCI TEYİT SAYISI
    # =====================================================

    auxiliary_count = 0

    if decision == "LONG":

        if adx_long_support:
            auxiliary_count += 1

        if vwap_support_long:
            auxiliary_count += 1

        if bollinger_long:
            auxiliary_count += 1

        if stoch_long:
            auxiliary_count += 1

    elif decision == "SHORT":

        if adx_short_support:
            auxiliary_count += 1

        if vwap_support_short:
            auxiliary_count += 1

        if bollinger_short:
            auxiliary_count += 1

        if stoch_short:
            auxiliary_count += 1

    # =====================================================
    # 950+ STRICT
    # =====================================================

    if (
        decision in ["LONG", "SHORT"]
        and confirmation_count == 5
        and not conflict
        and auxiliary_count >= 2
    ):

        strength = max(
            strength,
            950
        )

    # =====================================================
    # 990+ ULTRA
    # =====================================================

    ultra_conditions = (

        decision in ["LONG", "SHORT"]

        and confirmation_count == 5

        and auxiliary_count == 4

        and not conflict

        and adx_value is not None
        and adx_value >= 30

        and vol_ratio >= 1.30

        and rsi_value is not None

        and (
            (
                decision == "LONG"
                and rsi_value >= 62
                and rsi_value <= 72
            )
            or
            (
                decision == "SHORT"
                and rsi_value >= 28
                and rsi_value <= 38
            )
        )
    )

    if ultra_conditions:

        strength = max(
            strength,
            990
        )

    # =====================================================
    # 1000 MAXIMUM ALIGNMENT
    # =====================================================

    maximum_conditions = (

        decision in ["LONG", "SHORT"]

        and confirmation_count == 5

        and auxiliary_count == 4

        and not conflict

        and htf_long == (
            decision == "LONG"
        )

        and htf_short == (
            decision == "SHORT"
        )

        and adx_value is not None
        and adx_value >= 30

        and vol_ratio >= 1.50

        and vol_direction == decision

        and rsi_value is not None

        and (
            (
                decision == "LONG"
                and 65 <= rsi_value <= 72
            )
            or
            (
                decision == "SHORT"
                and 28 <= rsi_value <= 35
            )
        )

        and vwap_value is not None

        and (
            (
                decision == "LONG"
                and price > vwap_value
            )
            or
            (
                decision == "SHORT"
                and price < vwap_value
            )
        )

        and ema20 is not None
        and ema50 is not None

        and (
            (
                decision == "LONG"
                and price > ema20 > ema50
            )
            or
            (
                decision == "SHORT"
                and price < ema20 < ema50
            )
        )
    )

    if maximum_conditions:

        strength = 1000

    strength = int(
        clamp(
            strength,
            1,
            1000
        )
    )

    # =====================================================
    # GİRİŞ KALİTESİ
    # =====================================================

    entry_grade, entry_text, entry_score = (
        calculate_entry_quality(
            decision,
            price,
            ema20,
            atr_value,
            rsi_value,
            adx_value,
            vol_ratio,
            confirmation_count,
            (
                vwap_support_long
                if decision == "LONG"
                else vwap_support_short
            ),
            (
                bollinger_long
                if decision == "LONG"
                else bollinger_short
            ),
            (
                stoch_long
                if decision == "LONG"
                else stoch_short
            )
        )
    )

    # =====================================================
    # AŞIRI UZAK GİRİŞ UYARISI
    # =====================================================

    late_entry = False
    atr_distance = None

    if (
        decision in ["LONG", "SHORT"]
        and ema20 is not None
        and atr_value is not None
        and atr_value > 0
    ):

        atr_distance = (
            abs(price - ema20)
            / atr_value
        )

        if atr_distance >= 2.3:

            late_entry = True

    # =====================================================
    # SEVİYE
    # =====================================================

    confirmation = score_label(
        strength
    )

    # =====================================================
    # NEDENLER
    # =====================================================

    if decision == "LONG":

        reasons = reasons_long[:]

        if macd_hist is not None and macd_hist <= 0:

            reasons.append(
                "MACD LONG'u desteklemiyor"
            )

        if rsi_value is not None and rsi_value < 55:

            reasons.append(
                "RSI tam güçlü değil"
            )

        if vol_direction != "LONG":

            reasons.append(
                "Hacim LONG'u desteklemiyor"
            )

        if htf_short:

            reasons.append(
                "4H trend ters"
            )

    elif decision == "SHORT":

        reasons = reasons_short[:]

        if macd_hist is not None and macd_hist >= 0:

            reasons.append(
                "MACD SHORT'u desteklemiyor"
            )

        if rsi_value is not None and rsi_value > 45:

            reasons.append(
                "RSI tam zayıf değil"
            )

        if vol_direction != "SHORT":

            reasons.append(
                "Hacim SHORT'u desteklemiyor"
            )

        if htf_long:

            reasons.append(
                "4H trend ters"
            )

    else:

        reasons = [
            "LONG ve SHORT yeterince ayrışmadı",
            "Net yön için teyit bekleniyor"
        ]

    # Çelişkileri ekle

    for item in conflict_reasons:

        if item not in reasons:

            reasons.append(
                f"⚠️ {item}"
            )

    if late_entry:

        reasons.append(
            "⚠️ Fiyat trendden uzak — "
            "geç giriş riski"
        )

    # =====================================================
    # İŞLEM PLANI
    # =====================================================

    stop = None
    tp1 = None
    tp2 = None

    if (
        atr_value is not None
        and atr_value > 0
    ):

        if decision == "LONG":

            stop = (
                price -
                atr_value * 1.2
            )

            tp1 = (
                price +
                atr_value * 1.5
            )

            tp2 = (
                price +
                atr_value * 2.5
            )

        elif decision == "SHORT":

            stop = (
                price +
                atr_value * 1.2
            )

            tp1 = (
                price -
                atr_value * 1.5
            )

            tp2 = (
                price -
                atr_value * 2.5
            )

    # =====================================================
    # RETURN
    # =====================================================

    return {

        "product": product,

        "price": price,

        "ema20": ema20,
        "ema50": ema50,

        "ema20_4h": ema20_4h,
        "ema50_4h": ema50_4h,

        "rsi": rsi_value,

        "macd": macd_value,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,

        "adx": adx_value,
        "atr": atr_value,

        "stoch": stoch_value,

        "upper_bb": upper_bb,
        "middle_bb": middle_bb,
        "lower_bb": lower_bb,

        "vwap": vwap_value,

        "obv": obv_value,

        "volume_ratio": vol_ratio,

        "long_volume": long_vol,
        "short_volume": short_vol,

        "volume_direction":
            vol_direction,

        "htf_long":
            htf_long,

        "htf_short":
            htf_short,

        "long_score":
            int(round(long_score)),

        "short_score":
            int(round(short_score)),

        "raw_strength":
            raw_strength,

        "decision":
            decision,

        "strength":
            strength,

        "confirmation":
            confirmation,

        "confirmation_count":
            confirmation_count,

        "auxiliary_count":
            auxiliary_count,

        "entry_grade":
            entry_grade,

        "entry_text":
            entry_text,

        "entry_score":
            entry_score,

        "late_entry":
            late_entry,

        "atr_distance":
            atr_distance,

        "conflict":
            conflict,

        "reasons":
            reasons,

        "stop":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2
    }


# =========================================================
# RAPOR
# =========================================================

def build_report(result):

    base = result["product"]["base"]

    decision = result["decision"]
    strength = result["strength"]

    if decision == "LONG":

        direction = "🟢 LONG"

    elif decision == "SHORT":

        direction = "🔴 SHORT"

    else:

        direction = "🟡 BEKLE"

    bars = int(
        clamp(
            round(strength / 100),
            0,
            10
        )
    )

    strength_bar = (
        "█" * bars +
        "░" * (10 - bars)
    )

    reasons = "\n".join(
        f"• {x}"
        for x in result["reasons"][:8]
    )

    volume_percent = (
        result["volume_ratio"] * 100
    )

    if volume_percent >= 150:

        volume_level = "🔥 ÇOK YÜKSEK"

    elif volume_percent >= 120:

        volume_level = "🟢 YÜKSEK"

    elif volume_percent >= 90:

        volume_level = "🟡 NORMAL"

    else:

        volume_level = "🔵 DÜŞÜK"

    rsi_text = (
        f'{result["rsi"]:.2f}'
        if result["rsi"] is not None
        else "N/A"
    )

    macd_text = (
        f'{result["macd"]:.6f}'
        if result["macd"] is not None
        else "N/A"
    )

    macd_hist_text = (
        f'{result["macd_hist"]:.6f}'
        if result["macd_hist"] is not None
        else "N/A"
    )

    adx_text = (
        f'{result["adx"]:.2f}'
        if result["adx"] is not None
        else "N/A"
    )

    stoch_text = (
        f'{result["stoch"]:.1f}'
        if result["stoch"] is not None
        else "N/A"
    )

    if result["htf_long"]:

        htf_text = "🟢 LONG TEYİTLİ"

    elif result["htf_short"]:

        htf_text = "🔴 SHORT TEYİTLİ"

    else:

        htf_text = "🟡 NET TEYİT YOK"

    if result["late_entry"]:

        entry_warning = (
            "⚠️ GEÇ GİRİŞ RİSKİ"
        )

    else:

        entry_warning = (
            "🟢 GİRİŞ MESAFESİ UYGUN"
        )

    text = f"""
🚀 CRYPTO JET V12.1
━━━━━━━━━━━━━━━━

🪙 {base}

⏱ Ana TF: 1H
🔎 Trend teyidi: 4H

💰 Fiyat:
{fmt_price(result["price"])}

📊 YÖN:
{direction}

{result["confirmation"]}

💪 JET SCORE
{strength_bar} {strength}/1000

⭐ GİRİŞ KALİTESİ
{result["entry_text"]}
Skor: {result["entry_score"]}/100

{entry_warning}

━━━━━━━━━━━━━━━━

🧠 ANA TEYİTLER

1H EMA:
{fmt_price(result["ema20"])}

EMA50:
{fmt_price(result["ema50"])}

4H:
{htf_text}

RSI:
{rsi_text}

MACD:
{macd_text}

MACD Histogram:
{macd_hist_text}

HACİM:
{result["volume_direction"]}

Ana teyit:
{result["confirmation_count"]}/5

━━━━━━━━━━━━━━━━

🧠 YARDIMCI TEYİTLER

ADX:
{adx_text}

VWAP:
{fmt_price(result["vwap"])}

Stoch RSI:
{stoch_text}

Bollinger:
{"🟢 DESTEK" if (
    result["decision"] == "LONG"
    and result["upper_bb"] is not None
) or (
    result["decision"] == "SHORT"
    and result["lower_bb"] is not None
) else "🟡"}

Yardımcı teyit:
{result["auxiliary_count"]}/4

📊 Hacim oranı:
%{volume_percent:.0f}

{volume_level}

━━━━━━━━━━━━━━━━

🧠 NEDEN?

{reasons}

━━━━━━━━━━━━━━━━

🎯 İŞLEM PLANI
"""

    if decision in ["LONG", "SHORT"]:

        if result["stop"] is not None:

            text += (
                f"\nGiriş: "
                f"{fmt_price(result['price'])}"

                f"\nStop: "
                f"{fmt_price(result['stop'])}"

                f"\nTP1: "
                f"{fmt_price(result['tp1'])}"

                f"\nTP2: "
                f"{fmt_price(result['tp2'])}"
            )

    else:

        text += (
            "\nŞu an net işlem yönü yok."
        )

    text += (
        "\n\n━━━━━━━━━━━━━━━━\n"
        "⚠️ JET SCORE garanti kâr değildir.\n"
        "📊 1-1000 teknik uyum skorudur.\n"
        "🧠 Giriş kalitesi ayrıca hesaplanır."
    )

    return text


# =========================================================
# ALARM KONTROLÜ
# =========================================================

def should_alert(result):

    decision = result["decision"]
    strength = result["strength"]

    if decision not in [
        "LONG",
        "SHORT"
    ]:
        return False

    if strength < ALARM_THRESHOLD:
        return False

    product_id = result["product"]["id"]

    # Seviye

    if strength >= ULTRA_THRESHOLD:

        level = "ULTRA"

    elif strength >= EXTREME_THRESHOLD:

        level = "EXTREME"

    elif strength >= ELITE_THRESHOLD:

        level = "ELITE"

    elif strength >= STRONG_THRESHOLD:

        level = "STRONG"

    else:

        level = "ALARM"

    # 10 puanlık gruplar
    key = (
        decision,
        level,
        strength // 10
    )

    now = time.time()

    previous = alert_state.get(
        product_id
    )

    if previous:

        if (
            previous["key"] == key
            and
            now - previous["time"]
            < ALERT_COOLDOWN
        ):

            return False

    alert_state[product_id] = {
        "key": key,
        "time": now
    }

    return True


# =========================================================
# JET ALARM
# =========================================================

def build_alert(result):

    base = result["product"]["base"]

    strength = result["strength"]

    if strength >= MAX_THRESHOLD:

        title = (
            "👑 JET ALARM — MAXIMUM"
        )

    elif strength >= ULTRA_THRESHOLD:

        title = (
            "☢️ JET ALARM — ULTRA"
        )

    elif strength >= EXTREME_THRESHOLD:

        title = (
            "💥 JET ALARM — EXTREME"
        )

    elif strength >= ELITE_THRESHOLD:

        title = (
            "🚀 JET ALARM — ELITE"
        )

    elif strength >= STRONG_THRESHOLD:

        title = (
            "🔥 JET ALARM — STRONG"
        )

    else:

        title = (
            "🟢 JET ALARM"
        )

    if result["htf_long"]:

        htf = "🟢 LONG"

    elif result["htf_short"]:

        htf = "🔴 SHORT"

    else:

        htf = "🟡 NET DEĞİL"

    return (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━\n\n"

        f"🪙 {base}\n"

        f"📊 Yön: "
        f"{result['decision']}\n"

        f"💪 JET SCORE: "
        f"{strength}/1000\n"

        f"⭐ Giriş: "
        f"{result['entry_grade']}\n"

        f"⏱ Ana TF: 1H\n"

        f"🔎 4H: "
        f"{htf}\n"

        f"📊 Ana teyit: "
        f"{result['confirmation_count']}/5\n\n"

        f"{build_report(result)}"
    )


# =========================================================
# MARKET SCAN
# =========================================================

def market_scan(
    chat_id=None,
    send_alerts=False
):

    products = get_products()

    if not products:

        print(
            "Coin listesi alınamadı."
        )

        if chat_id:

            send_message(
                chat_id,
                "❌ Coinbase coin listesi alınamadı."
            )

        return []

    results = []
    strong_results = []

    print(
        f"🚀 V12.1 STRICT tarama başladı: "
        f"{len(products)} coin"
    )

    for index, product in enumerate(
        products,
        1
    ):

        try:

            candles = get_candles(
                product["id"]
            )

            if len(candles) < MIN_CANDLES:
                continue

            result = analyze_coin(
                product,
                candles
            )

            if result is None:
                continue

            results.append(result)

            if (
                result["decision"]
                in ["LONG", "SHORT"]
                and
                result["strength"]
                >= ALARM_THRESHOLD
            ):

                strong_results.append(
                    result
                )

            print(
                f"[{index}/{len(products)}] "
                f"{product['base']} "
                f"{result['decision']} "
                f"{result['strength']}/1000"
            )

            time.sleep(0.08)

        except Exception as e:

            print(
                f"{product['id']} analiz hatası:",
                e
            )

    results.sort(
        key=lambda x: x["strength"],
        reverse=True
    )

    strong_results.sort(
        key=lambda x: x["strength"],
        reverse=True
    )

    # =====================================================
    # ALARMLAR
    # =====================================================

    if chat_id and send_alerts:

        for result in strong_results:

            if should_alert(result):

                send_message(
                    chat_id,
                    build_alert(result)
                )

    # =====================================================
    # ÖZET
    # =====================================================

    if chat_id:

        summary = (
            "🚀 CRYPTO JET V12.1\n"
            "━━━━━━━━━━━━━━━━\n\n"

            f"🪙 Analiz edilen: "
            f"{len(results)} coin\n"

            f"🔥 700+: "
            f"{len(strong_results)}\n\n"
        )

        if strong_results:

            summary += (
                "🚨 STRONG SIGNALS\n\n"
            )

            for result in strong_results[:20]:

                base = result["product"]["base"]

                icon = (
                    "🟢"
                    if result["decision"] == "LONG"
                    else "🔴"
                )

                score = result["strength"]

                if score >= 1000:

                    level = "👑"

                elif score >= 990:

                    level = "☢️"

                elif score >= 950:

                    level = "💥"

                elif score >= 900:

                    level = "🚀"

                elif score >= 800:

                    level = "🔥"

                else:

                    level = "🟢"

                summary += (
                    f"{base:<10} "
                    f"{icon} "
                    f"{result['decision']} "
                    f"{score}/1000 "
                    f"{level}\n"
                )

        else:

            summary += (
                "Şu an 700+ güçlü sinyal yok.\n"
            )

        send_message(
            chat_id,
            summary
        )

    return results


# =========================================================
# BTC
# =========================================================

def btc_analysis(chat_id):

    products = get_products()

    btc = None

    for product in products:

        if product["base"] == "BTC":

            btc = product
            break

    if btc is None:

        send_message(
            chat_id,
            "❌ BTC bulunamadı."
        )

        return

    send_message(
        chat_id,
        "₿ BTC V12.1 STRICT "
        "1H + 4H analiz ediliyor..."
    )

    candles = get_candles(
        btc["id"]
    )

    if len(candles) < MIN_CANDLES:

        send_message(
            chat_id,
            "❌ BTC için yeterli mum "
            "verisi alınamadı."
        )

        return

    result = analyze_coin(
        btc,
        candles
    )

    if result:

        send_message(
            chat_id,
            build_report(result)
        )

    else:

        send_message(
            chat_id,
            "❌ BTC analizi alınamadı."
        )


# =========================================================
# STATUS
# =========================================================

def send_status(chat_id):

    status = (
        "🟢 AKTİF"
        if active_chat_id
        else
        "🟡 BEKLEMEDE"
    )

    product_count = len(
        get_products()
    )

    send_message(
        chat_id,
        f"""
🚀 CRYPTO JET V12.1
━━━━━━━━━━━━━━━━

Durum: {status}

🪙 Coinbase coinleri:
{product_count}

⏱ Tarama:
10 dakika

🧠 Sistem:
STRICT 1000 SCORE

🟢 Alarm:
700+

🔥 Strong:
800+

🚀 Elite:
900+

💥 Extreme:
950+

☢️ Ultra:
990+

👑 Maximum:
1000

⭐ Giriş Kalitesi:
A+ / A / B / C / D

⏱ Alarm cooldown:
1 saat

━━━━━━━━━━━━━━━━

📊 SCORE SİSTEMİ

1H EMA: 250
4H Trend: 200
RSI: 150
MACD: 100
Hacim: 100

ADX: 50
VWAP: 50
Bollinger: 50
Stoch RSI: 50

━━━━━━━━━━━━━━━━

📌 Sadece 700+ sinyaller
alarm olarak gönderilir.

⚠️ Score gerçek kazanma
olasılığı değildir.
"""
    )


# =========================================================
# KOMUTLAR
# =========================================================

def handle_command(
    chat_id,
    text
):

    global active_chat_id
    global last_scan_time

    text = (
        text
        .strip()
        .lower()
    )

    print(
        "Telegram komutu:",
        text
    )

    # =====================================================
    # START
    # =====================================================

    if text == "/start":

        send_message(
            chat_id,
            """
🚀 CRYPTO JET V12.1

STRICT 1000 SCORE sistemi aktif. ✅

Komutlar:

/jet → Otomatik sistemi başlat
/btc → BTC 1H + 4H analiz
/scan → Tüm coinleri tara
/status → Sistem durumu
/stop → Jet Alarm'ı durdur

🟢 700+ → JET ALARM
🔥 800+ → STRONG
🚀 900+ → ELITE
💥 950+ → EXTREME
☢️ 990+ → ULTRA
👑 1000 → MAXIMUM

⭐ Giriş kalitesi ayrıca hesaplanır.

⏱ Otomatik tarama:
10 dakika

⚠️ Score garanti kâr değildir.
"""
        )

    # =====================================================
    # JET
    # =====================================================

    elif text == "/jet":

        active_chat_id = chat_id

        send_message(
            chat_id,
            """
🚀 CRYPTO JET V12.1 AKTİF

━━━━━━━━━━━━━━━━

₿ BTC                 ✅
🪙 TÜM COİNLER         ✅

📊 1H ANA ANALİZ       ✅
🔎 4H TREND TEYİDİ     ✅

🧠 STRICT 1000 SCORE   ✅

📈 EMA                 ✅
📊 RSI                 ✅
📉 MACD                ✅
📦 HACİM               ✅

ADX                    ✅
VWAP                   ✅
BOLLINGER              ✅
STOCH RSI              ✅

⭐ GİRİŞ KALİTESİ       ✅
⚠️ GEÇ GİRİŞ KONTROLÜ  ✅

━━━━━━━━━━━━━━━━

🟢 700+ JET ALARM
🔥 800+ STRONG
🚀 900+ ELITE
💥 950+ EXTREME
☢️ 990+ ULTRA
👑 1000 MAXIMUM

⏱ 10 DAKİKA TARAMA

🔥 İlk tarama başlıyor...
"""
        )

        results = market_scan(
            chat_id,
            send_alerts=True
        )

        last_scan_time = time.time()

        if results:

            send_message(
                chat_id,
                """
✅ İlk V12.1 tarama tamamlandı.

⏱ Jet 10 dakikada bir
otomatik tarama yapacak.

🟢 Sadece 700+ sinyaller
alarm olarak gönderilecek.
"""
            )

    # =====================================================
    # BTC
    # =====================================================

    elif text == "/btc":

        btc_analysis(
            chat_id
        )

    # =====================================================
    # SCAN
    # =====================================================

    elif text == "/scan":

        send_message(
            chat_id,
            """
🔎 CRYPTO JET V12.1

Tüm uygun Coinbase coinleri
1H + 4H STRICT sistemle taranıyor...

🟢 700+ güçlü sinyaller
Telegram'a gönderilecek.

⏱ Bu işlem biraz sürebilir.
"""
        )

        results = market_scan(
            chat_id,
            send_alerts=True
        )

        last_scan_time = time.time()

        if results:

            send_message(
                chat_id,
                "✅ V12.1 tarama tamamlandı."
            )

    # =====================================================
    # STATUS
    # =====================================================

    elif text == "/status":

        send_status(
            chat_id
        )

    # =====================================================
    # STOP
    # =====================================================

    elif text == "/stop":

        active_chat_id = None

        send_message(
            chat_id,
            """
🛑 CRYPTO JET DURDURULDU

Otomatik tarama ve
Jet Alarm devre dışı.

Tekrar başlat:

/jet
"""
        )

    # =====================================================
    # UNKNOWN
    # =====================================================

    elif text.startswith("/"):

        send_message(
            chat_id,
            """
❓ Bilinmeyen komut.

Kullan:

/jet
/btc
/scan
/status
/stop
"""
        )


# =========================================================
# OTOMATİK TARAMA
# =========================================================

def automatic_scan():

    global last_scan_time

    if not active_chat_id:
        return

    now = time.time()

    if (
        now - last_scan_time
        < SCAN_INTERVAL
    ):
        return

    last_scan_time = now

    print(
        "⏱ Otomatik V12.1 tarama başladı."
    )

    results = market_scan(
        None,
        send_alerts=False
    )

    if not results:

        print(
            "Otomatik tarama sonuç vermedi."
        )

        return

    strong_results = [

        x for x in results

        if (
            x["decision"]
            in ["LONG", "SHORT"]

            and
            x["strength"]
            >= ALARM_THRESHOLD
        )
    ]

    print(
        f"🔥 700+ güçlü sinyal: "
        f"{len(strong_results)}"
    )

    for result in strong_results:

        if should_alert(result):

            send_message(
                active_chat_id,
                build_alert(result)
            )


# =========================================================
# ANA DÖNGÜ
# =========================================================

def main():

    global active_chat_id

    print(
        "🚀 CRYPTO JET V12.1 "
        "STRICT 1000 SCORE BAŞLADI"
    )

    print(
        "Telegram polling başlatılıyor..."
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

                print(
                    "Telegram HTTP hata:",
                    response.status_code
                )

                time.sleep(5)

                continue

            data = response.json()

            if not data.get("ok"):

                print(
                    "Telegram API hata:",
                    data
                )

                time.sleep(5)

                continue

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

                if not message:
                    continue

                chat_id = message[
                    "chat"
                ]["id"]

                text = message.get(
                    "text",
                    ""
                )

                if not text:
                    continue

                handle_command(
                    chat_id,
                    text
                )

            automatic_scan()

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "Crypto Jet kapatıldı."
            )

            break

        except Exception as e:

            print(
                "ANA DÖNGÜ HATASI:",
                e
            )

            time.sleep(5)


# =========================================================
# BAŞLAT
# =========================================================

if __name__ == "__main__":
    main()
