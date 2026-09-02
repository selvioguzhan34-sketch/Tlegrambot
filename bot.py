import os
import time
import math
import requests

# =========================================================
# CRYPTO JET V10
# Coinbase Only
# 1H + 4H
# All suitable Coinbase USD spot pairs
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE = "https://api.exchange.coinbase.com"

session = requests.Session()
session.headers.update({
    "User-Agent": "CryptoJet/10.0"
})

offset = 0
active_chat_id = None

last_scan_time = 0
scan_interval = 600

products = []
products_last_update = 0
products_refresh = 1800

alert_state = {}
alert_cooldown = 3600


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, payload=None):

    try:

        response = session.post(
            f"{API}/{method}",
            json=payload or {},
            timeout=15
        )

        return response.json()

    except Exception as e:

        print("Telegram hata:", e)

        return None


def send_message(chat_id, text):

    if len(text) <= 3900:

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text
            }
        )

        return

    buffer = ""

    for line in text.splitlines(True):

        if len(buffer) + len(line) > 3900:

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": buffer.rstrip()
                }
            )

            time.sleep(0.25)

            buffer = ""

        buffer += line

    if buffer.strip():

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": buffer.rstrip()
            }
        )


# =========================================================
# COINBASE REQUEST
# =========================================================

def coinbase_get(path, params=None):

    for attempt in range(3):

        try:

            response = session.get(
                COINBASE + path,
                params=params,
                timeout=15
            )

            if response.status_code == 429:

                time.sleep(
                    1.5 * (attempt + 1)
                )

                continue

            if response.status_code != 200:

                print(
                    "Coinbase:",
                    response.status_code,
                    path
                )

                return None

            return response.json()

        except Exception as e:

            print(
                "Coinbase bağlantı:",
                e
            )

            time.sleep(
                attempt + 1
            )

    return None


# =========================================================
# COINBASE COINLERİ BUL
# =========================================================

def discover_products(force=False):

    global products
    global products_last_update

    if (
        products
        and not force
        and time.time() - products_last_update
        < products_refresh
    ):

        return products

    data = coinbase_get(
        "/products"
    )

    if not isinstance(data, list):

        return products

    found = []

    for product in data:

        product_id = product.get(
            "id",
            ""
        )

        if not product_id.endswith(
            "-USD"
        ):

            continue

        if product.get(
            "quote_currency"
        ) != "USD":

            continue

        base = product.get(
            "base_currency"
        )

        if base in {
            "USD",
            "USDC",
            "USDT"
        }:

            continue

        if product.get(
            "status"
        ) != "online":

            continue

        if product.get(
            "trading_disabled",
            False
        ):

            continue

        if product.get(
            "cancel_only",
            False
        ):

            continue

        found.append(
            product_id
        )

    products = sorted(
        set(found)
    )

    products_last_update = time.time()

    print(
        "Coinbase aktif USD coin:",
        len(products)
    )

    return products


# =========================================================
# 1 SAAT MUM VERİSİ
# =========================================================

def get_candles(
    product_id,
    limit=300
):

    data = coinbase_get(
        f"/products/{product_id}/candles",
        {
            "granularity": 3600
        }
    )

    if not isinstance(
        data,
        list
    ):

        return []

    result = []

    for item in data[-limit:]:

        if len(item) < 6:

            continue

        try:

            result.append(
                (
                    int(item[0]),
                    float(item[1]),
                    float(item[2]),
                    float(item[3]),
                    float(item[4]),
                    float(item[5])
                )
            )

        except Exception:

            continue

    return sorted(
        result,
        key=lambda x: x[0]
    )


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:

        return None

    multiplier = 2 / (
        period + 1
    )

    result = (
        sum(values[:period])
        / period
    )

    for value in values[period:]:

        result = (
            (value - result)
            * multiplier
            + result
        )

    return result


# =========================================================
# RSI
# =========================================================

def rsi(values, period=14):

    if len(values) < period + 1:

        return None

    gains = []
    losses = []

    start = -(period + 1)

    window = values[start:]

    for i in range(
        1,
        len(window)
    ):

        change = (
            window[i]
            - window[i - 1]
        )

        if change >= 0:

            gains.append(change)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(
                abs(change)
            )

    avg_gain = (
        sum(gains)
        / period
    )

    avg_loss = (
        sum(losses)
        / period
    )

    if avg_loss == 0:

        return 100

    rs_value = (
        avg_gain
        / avg_loss
    )

    return (
        100
        - (
            100
            / (1 + rs_value)
        )
    )


# =========================================================
# RSI SERIES
# =========================================================

def rsi_series(
    values,
    period=14
):

    result = []

    if len(values) < period + 1:

        return result

    for i in range(
        period,
        len(values)
    ):

        value = rsi(
            values[:i + 1],
            period
        )

        if value is not None:

            result.append(value)

    return result


# =========================================================
# STOCH RSI
# =========================================================

def stochastic_rsi(
    values,
    period=14
):

    series = rsi_series(
        values,
        period
    )

    if len(series) < period:

        return None

    window = series[-period:]

    lowest = min(window)
    highest = max(window)

    if highest == lowest:

        return 50

    return (
        (
            series[-1]
            - lowest
        )
        / (
            highest
            - lowest
        )
    ) * 100


# =========================================================
# MACD
# =========================================================

def macd(values):

    if len(values) < 35:

        return None, None

    macd_values = []

    start = 26

    for i in range(
        start,
        len(values) + 1
    ):

        fast = ema(
            values[:i],
            12
        )

        slow = ema(
            values[:i],
            26
        )

        if (
            fast is not None
            and slow is not None
        ):

            macd_values.append(
                fast - slow
            )

    if not macd_values:

        return None, None

    current = macd_values[-1]

    signal = ema(
        macd_values,
        9
    )

    return current, signal


# =========================================================
# ATR
# =========================================================

def atr(
    candles,
    period=14
):

    if len(candles) < period + 1:

        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        previous_close = (
            candles[i - 1][4]
        )

        low = candles[i][1]
        high = candles[i][2]

        tr = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            )
        )

        true_ranges.append(tr)

    return (
        sum(true_ranges[-period:])
        / period
    )


# =========================================================
# ADX
# =========================================================

def adx(
    candles,
    period=14
):

    if len(candles) < 40:

        return None

    true_ranges = []
    plus_dm = []
    minus_dm = []

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i][2]
        low = candles[i][1]

        previous_high = (
            candles[i - 1][2]
        )

        previous_low = (
            candles[i - 1][1]
        )

        previous_close = (
            candles[i - 1][4]
        )

        tr = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            )
        )

        up_move = (
            high
            - previous_high
        )

        down_move = (
            previous_low
            - low
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

        true_ranges.append(tr)
        plus_dm.append(plus)
        minus_dm.append(minus)

    if len(true_ranges) < period * 2:

        return None

    atr_value = (
        sum(
            true_ranges[:period]
        )
        / period
    )

    plus_value = (
        sum(
            plus_dm[:period]
        )
        / period
    )

    minus_value = (
        sum(
            minus_dm[:period]
        )
        / period
    )

    dx_values = []

    for i in range(
        period,
        len(true_ranges)
    ):

        atr_value = (
            (
                atr_value
                * (period - 1)
            )
            + true_ranges[i]
        ) / period

        plus_value = (
            (
                plus_value
                * (period - 1)
            )
            + plus_dm[i]
        ) / period

        minus_value = (
            (
                minus_value
                * (period - 1)
            )
            + minus_dm[i]
        ) / period

        if atr_value == 0:

            continue

        plus_di = (
            100
            * plus_value
            / atr_value
        )

        minus_di = (
            100
            * minus_value
            / atr_value
        )

        denominator = (
            plus_di
            + minus_di
        )

        if denominator == 0:

            continue

        dx = (
            100
            * abs(
                plus_di
                - minus_di
            )
            / denominator
        )

        dx_values.append(dx)

    if not dx_values:

        return None

    return (
        sum(dx_values[-period:])
        / min(
            period,
            len(dx_values)
        )
    )


# =========================================================
# BOLLINGER
# =========================================================

def bollinger(
    values,
    period=20,
    multiplier=2
):

    if len(values) < period:

        return None, None, None

    window = values[-period:]

    middle = (
        sum(window)
        / period
    )

    variance = (
        sum(
            (
                value
                - middle
            ) ** 2
            for value in window
        )
        / period
    )

    deviation = math.sqrt(
        variance
    )

    upper = (
        middle
        + multiplier
        * deviation
    )

    lower = (
        middle
        - multiplier
        * deviation
    )

    return (
        middle,
        upper,
        lower
    )


# =========================================================
# VWAP
# =========================================================

def vwap(
    candles,
    period=20
):

    if len(candles) < period:

        return None

    window = candles[-period:]

    total_value = 0
    total_volume = 0

    for candle in window:

        low = candle[1]
        high = candle[2]
        close = candle[4]
        volume = candle[5]

        typical_price = (
            low
            + high
            + close
        ) / 3

        total_value += (
            typical_price
            * volume
        )

        total_volume += volume

    if total_volume == 0:

        return None

    return (
        total_value
        / total_volume
    )


# =========================================================
# OBV
# =========================================================

def obv_delta(
    candles,
    period=20
):

    if len(candles) < period + 1:

        return None

    obv_values = []
    current = 0

    for i in range(
        1,
        len(candles)
    ):

        current_close = (
            candles[i][4]
        )

        previous_close = (
            candles[i - 1][4]
        )

        volume = candles[i][5]

        if current_close > previous_close:

            current += volume

        elif current_close < previous_close:

            current -= volume

        obv_values.append(
            current
        )

    if len(obv_values) < period:

        return None

    return (
        obv_values[-1]
        - obv_values[-period]
    )


# =========================================================
# 4 SAATLİK MUM OLUŞTUR
# =========================================================

def aggregate_4h(candles):

    result = []

    bucket = []
    current_bucket = None

    for candle in candles:

        bucket_id = (
            candle[0]
            // 14400
        )

        if (
            current_bucket is None
            or bucket_id
            == current_bucket
        ):

            bucket.append(candle)
            current_bucket = bucket_id

        else:

            if bucket:

                result.append(
                    (
                        bucket[0][0],

                        min(
                            x[1]
                            for x in bucket
                        ),

                        max(
                            x[2]
                            for x in bucket
                        ),

                        bucket[0][3],

                        bucket[-1][4],

                        sum(
                            x[5]
                            for x in bucket
                        )
                    )
                )

            bucket = [candle]

            current_bucket = (
                bucket_id
            )

    if bucket:

        result.append(
            (
                bucket[0][0],

                min(
                    x[1]
                    for x in bucket
                ),

                max(
                    x[2]
                    for x in bucket
                ),

                bucket[0][3],

                bucket[-1][4],

                sum(
                    x[5]
                    for x in bucket
                )
            )
        )

    return result


# =========================================================
# ANALİZ MOTORU
# =========================================================

def analyze(candles):

    if len(candles) < 80:

        return None

    closes = [
        candle[4]
        for candle in candles
    ]

    price = closes[-1]

    ema20 = ema(
        closes,
        20
    )

    ema50 = ema(
        closes,
        50
    )

    rsi_value = rsi(
        closes
    )

    macd_value, macd_signal = macd(
        closes
    )

    adx_value = adx(
        candles
    )

    atr_value = atr(
        candles
    )

    stoch = stochastic_rsi(
        closes
    )

    bb_middle, bb_upper, bb_lower = (
        bollinger(closes)
    )

    vwap_value = vwap(
        candles
    )

    obv_value = obv_delta(
        candles
    )

    volumes = [
        candle[5]
        for candle in candles
    ]

    average_volume = (
        sum(
            volumes[-21:-1]
        )
        / 20
    )

    volume_ratio = (
        volumes[-1]
        / average_volume
        if average_volume > 0
        else 0
    )

    # -----------------------------------------------------
    # 4H
    # -----------------------------------------------------

    candles_4h = aggregate_4h(
        candles
    )

    closes_4h = [
        candle[4]
        for candle in candles_4h
    ]

    ema4_20 = ema(
        closes_4h,
        20
    )

    ema4_50 = ema(
        closes_4h,
        50
    )

    if None in (
        ema20,
        ema50,
        rsi_value,
        macd_value,
        adx_value,
        atr_value,
        stoch,
        bb_middle,
        bb_upper,
        bb_lower,
        vwap_value,
        obv_value,
        ema4_20,
        ema4_50
    ):

        return None

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    # =====================================================
    # TREND — 30 PUAN
    # =====================================================

    if ema20 > ema50:

        long_score += 15
        long_reasons.append(
            "1H EMA trend yukarı"
        )

    else:

        short_score += 15
        short_reasons.append(
            "1H EMA trend aşağı"
        )

    if price > ema20:

        long_score += 5
        long_reasons.append(
            "Fiyat EMA20 üzerinde"
        )

    else:

        short_score += 5
        short_reasons.append(
            "Fiyat EMA20 altında"
        )

    if ema4_20 > ema4_50:

        long_score += 10
        long_reasons.append(
            "4H trend destekliyor"
        )

    else:

        short_score += 10
        short_reasons.append(
            "4H trend aşağı"
        )

    # =====================================================
    # MOMENTUM — 25 PUAN
    # =====================================================

    if rsi_value >= 55:

        long_score += 10
        long_reasons.append(
            "RSI güçlü"
        )

    elif rsi_value <= 45:

        short_score += 10
        short_reasons.append(
            "RSI zayıf"
        )

    if macd_value > macd_signal:

        long_score += 10
        long_reasons.append(
            "MACD pozitif"
        )

    else:

        short_score += 10
        short_reasons.append(
            "MACD negatif"
        )

    if stoch >= 60:

        long_score += 5
        long_reasons.append(
            "Stoch RSI yukarı"
        )

    elif stoch <= 40:

        short_score += 5
        short_reasons.append(
            "Stoch RSI aşağı"
        )

    # =====================================================
    # HACİM + FİYAT — 20 PUAN
    # =====================================================

    previous_close = candles[-2][4]

    if volume_ratio >= 1.30:

        if price > previous_close:

            long_score += 10
            long_reasons.append(
                "Hacim güçlü"
            )

        elif price < previous_close:

            short_score += 10
            short_reasons.append(
                "Hacim güçlü"
            )

    elif volume_ratio >= 1.00:

        if price > previous_close:

            long_score += 5
            long_reasons.append(
                "Hacim yeterli"
            )

        elif price < previous_close:

            short_score += 5
            short_reasons.append(
                "Hacim yeterli"
            )

    if obv_value > 0:

        long_score += 5
        long_reasons.append(
            "OBV alıcı hacmini destekliyor"
        )

    elif obv_value < 0:

        short_score += 5
        short_reasons.append(
            "OBV satıcı hacmini destekliyor"
        )

    if price > vwap_value:

        long_score += 5
        long_reasons.append(
            "VWAP üzerinde"
        )

    else:

        short_score += 5
        short_reasons.append(
            "VWAP altında"
        )

    # =====================================================
    # VOLATİLİTE — 10 PUAN
    # =====================================================

    if adx_value >= 25:

        if long_score > short_score:

            long_score += 5
            long_reasons.append(
                "ADX trendi güçlü"
            )

        elif short_score > long_score:

            short_score += 5
            short_reasons.append(
                "ADX trendi güçlü"
            )

    if price < bb_lower:

        long_score += 5
        long_reasons.append(
            "Bollinger alt bandı"
        )

    elif price > bb_upper:

        short_score += 5
        short_reasons.append(
            "Bollinger üst bandı"
        )

    elif price > bb_middle:

        long_score += 2

    elif price < bb_middle:

        short_score += 2

    # =====================================================
    # MUM + YAPI — 15 PUAN
    # =====================================================

    current_open = candles[-1][3]
    current_high = candles[-1][2]
    current_low = candles[-1][1]
    current_close = candles[-1][4]

    candle_range = max(
        current_high
        - current_low,
        1e-12
    )

    body = abs(
        current_close
        - current_open
    )

    body_ratio = (
        body
        / candle_range
    )

    if (
        current_close
        > current_open
        and body_ratio >= 0.50
    ):

        long_score += 5
        long_reasons.append(
            "Güçlü alıcı mum"
        )

    elif (
        current_close
        < current_open
        and body_ratio >= 0.50
    ):

        short_score += 5
        short_reasons.append(
            "Güçlü satıcı mum"
        )

    if current_close > closes[-5]:

        long_score += 5
        long_reasons.append(
            "Kısa yapı yukarı"
        )

    elif current_close < closes[-5]:

        short_score += 5
        short_reasons.append(
            "Kısa yapı aşağı"
        )

    if (
        candles_4h[-1][4]
        > ema4_20
    ):

        long_score += 5
        long_reasons.append(
            "4H fiyat EMA20 üzerinde"
        )

    else:

        short_score += 5
        short_reasons.append(
            "4H fiyat EMA20 altında"
        )

    # =====================================================
    # KARAR
    # =====================================================

    difference = abs(
        long_score
        - short_score
    )

    if (
        difference < 15
    ):

        decision = "BEKLE"

        strength = min(
            69,
            45 + difference
        )

        reasons = [
            "Long ve Short tarafı yeterince ayrışmadı"
        ]

    elif long_score > short_score:

        decision = "LONG"

        strength = int(
            min(
                100,
                45
                + long_score * 0.55
                - short_score * 0.15
            )
        )

        reasons = long_reasons[:6]

    else:

        decision = "SHORT"

        strength = int(
            min(
                100,
                45
                + short_score * 0.55
                - long_score * 0.15
            )
        )

        reasons = short_reasons[:6]

    # =====================================================
    # %90 TEYİT KAPISI
    # =====================================================

    if decision != "BEKLE":

        higher_tf_agrees = (
            (
                decision == "LONG"
                and ema4_20 > ema4_50
            )
            or
            (
                decision == "SHORT"
                and ema4_20 < ema4_50
            )
        )

        strong_volume = (
            volume_ratio >= 1.00
        )

        strong_trend = (
            adx_value >= 25
        )

        if not (
            higher_tf_agrees
            and strong_volume
            and strong_trend
        ):

            strength = min(
                strength,
                89
            )

        if volume_ratio < 0.70:

            strength = min(
                strength,
                79
            )

    # =====================================================
    # TEYİT
    # =====================================================

    if decision == "BEKLE":

        confirmation = "⚪ BEKLE"

    elif strength >= 90:

        confirmation = (
            "🟢 GÜÇLÜ TEYİT"
        )

    elif strength >= 70:

        confirmation = (
            "🟡 TEYİT BEKLE"
        )

    else:

        confirmation = "⚪ ZAYIF"

    # =====================================================
    # İŞLEM PLANI
    # =====================================================

    if decision == "LONG":

        stop = (
            price
            - atr_value * 1.20
        )

        tp1 = (
            price
            + atr_value * 1.50
        )

        tp2 = (
            price
            + atr_value * 2.50
        )

    elif decision == "SHORT":

        stop = (
            price
            + atr_value * 1.20
        )

        tp1 = (
            price
            - atr_value * 1.50
        )

        tp2 = (
            price
            - atr_value * 2.50
        )

    else:

        stop = None
        tp1 = None
        tp2 = None

    return {

        "price": price,

        "ema20": ema20,
        "ema50": ema50,

        "ema4_20": ema4_20,
        "ema4_50": ema4_50,

        "rsi": rsi_value,

        "macd": macd_value,
        "macd_signal": macd_signal,

        "adx": adx_value,

        "atr": atr_value,

        "stoch": stoch,

        "vwap": vwap_value,

        "bb_middle": bb_middle,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,

        "volume_ratio": volume_ratio,

        "volume_strength": min(
            200,
            int(
                volume_ratio * 100
            )
        ),

        "decision": decision,

        "strength": strength,

        "confirmation": confirmation,

        "reasons": reasons,

        "entry": price,

        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,

        "long_score": long_score,
        "short_score": short_score,

        "candle":
            "🟢 ALICI MUM"
            if current_close > current_open
            else "🔴 SATICI MUM"
    }


# =========================================================
# TEK COIN ANALİZİ
# =========================================================

def analyze_coin(product_id):

    candles = get_candles(
        product_id
    )

    if not candles:

        return None

    return analyze(
        candles
    )


# =========================================================
# SAYI FORMAT
# =========================================================

def number(value):

    if value is None:

        return "-"

    if abs(value) < 1:

        return (
            f"{value:,.8f}"
            .rstrip("0")
            .rstrip(".")
        )

    return f"{value:,.2f}"


# =========================================================
# BTC / DETAYLI RAPOR
# =========================================================

def format_report(
    product_id,
    result
):

    coin = product_id.split(
        "-"
    )[0]

    if result["decision"] == "LONG":

        decision = (
            "🟢 LONG İÇİN UYGUN"
        )

    elif result["decision"] == "SHORT":

        decision = (
            "🔴 SHORT İÇİN UYGUN"
        )

    else:

        decision = "⚪ BEKLE"

    blocks = int(
        result["strength"]
        / 10
    )

    bar = (
        "█" * blocks
        + "░" * (10 - blocks)
    )

    reasons = "\n".join(
        "• " + x
        for x in result["reasons"]
    )

    if result["volume_ratio"] >= 1.50:

        volume_status = (
            "🔥 ÇOK GÜÇLÜ"
        )

    elif result["volume_ratio"] >= 1.30:

        volume_status = (
            "🟢 GÜÇLÜ"
        )

    elif result["volume_ratio"] >= 1.00:

        volume_status = (
            "🟡 NORMAL"
        )

    else:

        volume_status = (
            "🔵 DÜŞÜK"
        )

    if result["decision"] == "BEKLE":

        trade_plan = (
            "❌ ŞU AN POZİSYON YOK\n"
            "Yeni teyit bekleniyor."
        )

    else:

        trade_plan = f"""
Giriş:  ${number(result["entry"])}
🛑 Stop: ${number(result["stop"])}
✅ TP1: ${number(result["tp1"])}
🚀 TP2: ${number(result["tp2"])}
"""

    return f"""
🚀 CRYPTO JET V10
━━━━━━━━━━━━━━━━━━━━

₿ {coin} / 1 SAAT

💰 FİYAT
${number(result["price"])}

━━━━━━━━━━━━━━━━━━━━
🎯 POZİSYON KARARI
━━━━━━━━━━━━━━━━━━━━

{decision}

{result["confirmation"]}

💪 SİNYAL GÜCÜ
{bar} %{result["strength"]}

━━━━━━━━━━━━━━━━━━━━
🧠 NEDEN?
━━━━━━━━━━━━━━━━━━━━

{reasons}

━━━━━━━━━━━━━━━━━━━━
📊 TREND
━━━━━━━━━━━━━━━━━━━━

EMA20: {number(result["ema20"])}
EMA50: {number(result["ema50"])}

4H EMA20:
{number(result["ema4_20"])}

4H EMA50:
{number(result["ema4_50"])}

━━━━━━━━━━━━━━━━━━━━
📈 MOMENTUM
━━━━━━━━━━━━━━━━━━━━

RSI: {result["rsi"]:.2f}

MACD:
{result["macd"]:.6f}

ADX:
{result["adx"]:.2f}

Stoch RSI:
{result["stoch"]:.1f}

━━━━━━━━━━━━━━━━━━━━
📊 HACİM / FİYAT
━━━━━━━━━━━━━━━━━━━━

🔥 Hacim Gücü:
%{result["volume_strength"]}

{volume_status}

VWAP:
${number(result["vwap"])}

━━━━━━━━━━━━━━━━━━━━
🎯 İŞLEM PLANI
━━━━━━━━━━━━━━━━━━━━

{trade_plan}

━━━━━━━━━━━━━━━━━━━━

⚠️ Sinyal skoru garanti değildir.
Risk yönetimi kullan.

⏱ Analiz: 1 Saat
🕓 Trend teyidi: 4 Saat
🔄 Tarama: 10 Dakika
"""


# =========================================================
# ALARM
# =========================================================

def format_alert(
    product_id,
    result
):

    if (
        result["strength"] < 90
        or result["decision"]
        == "BEKLE"
    ):

        return None

    if result["strength"] >= 98:

        level = (
            "💥 JET ALERT — EXTREME"
        )

    elif result["strength"] >= 95:

        level = (
            "🚀 JET ALERT — ÇOK GÜÇLÜ"
        )

    else:

        level = (
            "🔥 JET ALERT — GÜÇLÜ"
        )

    side = (
        "🟢 LONG"
        if result["decision"] == "LONG"
        else "🔴 SHORT"
    )

    reasons = "\n".join(
        "✓ " + x
        for x in result["reasons"][:5]
    )

    return f"""
{level}
━━━━━━━━━━━━━━━━━━━━

₿ {product_id.split("-")[0]}

{side}

💪 Sinyal Gücü:
%{result["strength"]}

🟢 GÜÇLÜ TEYİT

━━━━━━━━━━━━━━━━━━━━
🧠 NEDEN?

{reasons}

━━━━━━━━━━━━━━━━━━━━

💰 Fiyat:
${number(result["price"])}

📊 Hacim:
%{result["volume_strength"]}

📈 ADX:
{result["adx"]:.2f}

📉 RSI:
{result["rsi"]:.2f}

━━━━━━━━━━━━━━━━━━━━
🎯 İŞLEM PLANI

Giriş:
${number(result["entry"])}

🛑 Stop:
${number(result["stop"])}

✅ TP1:
${number(result["tp1"])}

🚀 TP2:
${number(result["tp2"])}

━━━━━━━━━━━━━━━━━━━━

⚠️ Teyit skoru garanti değildir.
"""


# =========================================================
# TÜM PİYASA
# =========================================================

def scan_market():

    market = []

    current_products = (
        discover_products()
    )

    print(
        "Toplam coin:",
        len(current_products)
    )

    for index, product_id in enumerate(
        current_products,
        start=1
    ):

        try:

            result = analyze_coin(
                product_id
            )

            if result:

                market.append(
                    (
                        product_id,
                        result
                    )
                )

            print(
                f"[{index}/{len(current_products)}]",
                product_id
            )

        except Exception as e:

            print(
                product_id,
                "analiz hatası:",
                e
            )

        # API'ye yük bindirmemek için
        if index % 8 == 0:

            time.sleep(
                0.15
            )

    market.sort(
        key=lambda x:
        x[1]["strength"],
        reverse=True
    )

    return market


# =========================================================
# /SCAN
# =========================================================

def format_market_scan(
    market
):

    strong = [
        item
        for item in market
        if (
            item[1]["strength"] >= 90
            and item[1]["decision"]
            != "BEKLE"
        )
    ]

    lines = []

    lines.append(
        "🚀 CRYPTO JET V10"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append("")

    lines.append(
        f"🪙 Analiz edilen: {len(market)} coin"
    )

    lines.append(
        f"🔥 %90+ teyit: {len(strong)}"
    )

    lines.append("")

    if strong:

        lines.append(
            "🚨 GÜÇLÜ SİNYALLER"
        )

        lines.append("")

        for product_id, result in strong:

            coin = product_id.split(
                "-"
            )[0]

            icon = (
                "🟢"
                if result["decision"]
                == "LONG"
                else "🔴"
            )

            lines.append(
                f"{coin:<10}"
                f"{icon} "
                f"{result['decision']:<6}"
                f"%{result['strength']}"
            )

        lines.append("")

    lines.append(
        "📋 TÜM COİNLER"
    )

    lines.append("")

    for product_id, result in market:

        coin = product_id.split(
            "-"
        )[0]

        if result["decision"] == "LONG":

            icon = "🟢"

        elif result["decision"] == "SHORT":

            icon = "🔴"

        else:

            icon = "⚪"

        lines.append(
            f"{coin:<10}"
            f"{icon} "
            f"{result['decision']:<6}"
            f"%{result['strength']}"
        )

    return "\n".join(
        lines
    )


# =========================================================
# KOMUTLAR
# =========================================================

def handle_command(
    chat_id,
    text
):

    global active_chat_id

    active_chat_id = chat_id

    command = (
        text
        .strip()
        .lower()
    )

    if command == "/start":

        send_message(
            chat_id,
            """
🚀 CRYPTO JET V10 AKTİF
━━━━━━━━━━━━━━━━

🟢 Coinbase spot market
🧠 Çoklu indikatör sistemi
📊 1H + 4H teyit
📈 Trend + momentum
📊 Hacim + VWAP + OBV
🎯 LONG / SHORT / BEKLE

🚨 %90+ güçlü teyit alarmı

🪙 Coinbase'deki uygun
USD altcoinleri otomatik taranır.

Komutlar:

/btc
BTC detaylı analiz

/scan
Tüm coinleri tara

━━━━━━━━━━━━━━━━

🔄 Otomatik tarama:
10 dakika

⚠️ Crypto Jet karar destek
sistemidir.
"""
        )

    elif command == "/btc":

        result = analyze_coin(
            "BTC-USD"
        )

        if result:

            send_message(
                chat_id,
                format_report(
                    "BTC-USD",
                    result
                )
            )

        else:

            send_message(
                chat_id,
                "❌ BTC verisi alınamadı."
            )

    elif command == "/scan":

        send_message(
            chat_id,
            """
🔎 CRYPTO JET V10
MARKET SCAN

Coinbase piyasası taranıyor...

🪙 Coinler bulunuyor
📊 Teknik veriler hesaplanıyor
🧠 1H + 4H teyit yapılıyor
🚨 %90+ sinyaller kontrol ediliyor
"""
        )

        market = scan_market()

        if market:

            send_message(
                chat_id,
                format_market_scan(
                    market
                )
            )

        else:

            send_message(
                chat_id,
                "❌ Market taraması sonuç vermedi."
            )


# =========================================================
# TELEGRAM UPDATES
# =========================================================

def check_updates():

    global offset

    data = telegram(
        "getUpdates",
        {
            "offset": offset,
            "timeout": 2
        }
    )

    if not data:

        return

    if not data.get(
        "ok"
    ):

        return

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

        chat = message.get(
            "chat"
        )

        text = message.get(
            "text"
        )

        if not chat or not text:

            continue

        handle_command(
            chat["id"],
            text
        )


# =========================================================
# OTOMATİK ALARM
# =========================================================

def automatic_scan():

    if active_chat_id is None:

        return

    print(
        "🚀 Otomatik market taraması..."
    )

    market = scan_market()

    now = time.time()

    for product_id, result in market:

        if (
            result["strength"] < 90
            or result["decision"]
            == "BEKLE"
        ):

            continue

        state = (
            result["decision"],
            result["strength"] // 2
        )

        previous = alert_state.get(
            product_id
        )

        if previous:

            old_state, old_time = previous

            if (
                old_state == state
                and now - old_time
                < alert_cooldown
            ):

                continue

        message = format_alert(
            product_id,
            result
        )

        if message:

            send_message(
                active_chat_id,
                message
            )

            alert_state[
                product_id
            ] = (
                state,
                now
            )

    print(
        "✅ Otomatik tarama tamamlandı."
    )


# =========================================================
# ANA DÖNGÜ
# =========================================================

print(
    "🚀 CRYPTO JET V10 BAŞLADI"
)

while True:

    try:

        check_updates()

        now = time.time()

        if (
            now - last_scan_time
            >= scan_interval
        ):

            automatic_scan()

            last_scan_time = now

        time.sleep(1)

    except Exception as e:

        print(
            "Ana döngü hata:",
            e
        )

        time.sleep(5)
