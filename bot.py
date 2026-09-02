import os
import time
import math
import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE = "https://api.exchange.coinbase.com"

session = requests.Session()
session.headers.update({
    "User-Agent": "CryptoJet/10.2"
})

SCAN_INTERVAL = 600          # 10 dakika
PRODUCT_REFRESH = 1800       # 30 dakika
ALERT_COOLDOWN = 3600        # 1 saat

active_chat_id = None
last_scan_time = 0
last_product_refresh = 0

products_cache = []
alert_state = {}


# =========================================================
# YARDIMCI FONKSİYONLAR
# =========================================================

def safe_float(value, default=0.0):
    try:
        return float(value)
    except:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def fmt_price(price):
    price = safe_float(price)

    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:,.4f}"
    else:
        return f"${price:,.8f}"


def send_message(chat_id, text):
    if not chat_id:
        return

    try:
        if len(text) <= 3900:
            session.post(
                f"{API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text
                },
                timeout=20
            )
            return

        parts = []

        while text:
            if len(text) <= 3900:
                parts.append(text)
                break

            cut = text.rfind("\n", 0, 3900)

            if cut <= 0:
                cut = 3900

            parts.append(text[:cut])
            text = text[cut:]

        for part in parts:
            session.post(
                f"{API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": part
                },
                timeout=20
            )

    except Exception as e:
        print("Telegram gönderme hatası:", e)


def coinbase_get(path, params=None):
    url = COINBASE + path

    for attempt in range(4):
        try:
            response = session.get(
                url,
                params=params,
                timeout=20
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                wait = 2 ** attempt
                print(f"Coinbase rate limit. {wait}s bekleniyor...")
                time.sleep(wait)
                continue

            print(
                "Coinbase hata:",
                response.status_code,
                response.text[:200]
            )

        except Exception as e:
            print("Coinbase bağlantı hatası:", e)
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

    products = []

    for p in data:

        try:
            product_id = p.get("id", "")
            base = p.get("base_currency", "")
            quote = p.get("quote_currency", "")
            status = p.get("status", "")
            trading_disabled = p.get("trading_disabled", False)
            cancel_only = p.get("cancel_only", False)

            if not product_id:
                continue

            if quote != "USD":
                continue

            if base in ["USD", "USDC", "USDT"]:
                continue

            if status != "online":
                continue

            if trading_disabled:
                continue

            if cancel_only:
                continue

            products.append({
                "id": product_id,
                "base": base
            })

        except:
            continue

    products_cache = products
    last_product_refresh = now

    print(f"Coinbase aktif USD coin sayısı: {len(products)}")

    return products


# =========================================================
# CANDLE VERİSİ
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

        candles.append({
            "time": safe_float(row[0]),
            "low": safe_float(row[1]),
            "high": safe_float(row[2]),
            "open": safe_float(row[3]),
            "close": safe_float(row[4]),
            "volume": safe_float(row[5])
        })

    candles.sort(key=lambda x: x["time"])

    return candles


# =========================================================
# 4 SAATLİK CANDLE OLUŞTUR
# =========================================================

def aggregate_4h(candles):

    if len(candles) < 4:
        return []

    result = []
    bucket = []

    current_bucket = None

    for candle in candles:

        bucket_id = int(candle["time"] // 14400)

        if current_bucket is None:
            current_bucket = bucket_id

        if bucket_id != current_bucket:

            if len(bucket) >= 4:

                result.append({
                    "time": bucket[0]["time"],
                    "open": bucket[0]["open"],
                    "high": max(x["high"] for x in bucket),
                    "low": min(x["low"] for x in bucket),
                    "close": bucket[-1]["close"],
                    "volume": sum(x["volume"] for x in bucket)
                })

            bucket = []
            current_bucket = bucket_id

        bucket.append(candle)

    if len(bucket) >= 4:

        result.append({
            "time": bucket[0]["time"],
            "open": bucket[0]["open"],
            "high": max(x["high"] for x in bucket),
            "low": min(x["low"] for x in bucket),
            "close": bucket[-1]["close"],
            "volume": sum(x["volume"] for x in bucket)
        })

    return result


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (
            (value - result) * multiplier
            + result
        )

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
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# =========================================================
# MACD
# =========================================================

def macd(values):

    if len(values) < 40:
        return None, None, None

    macd_values = []

    for i in range(26, len(values) + 1):

        subset = values[:i]

        e12 = ema(subset, 12)
        e26 = ema(subset, 26)

        if e12 is not None and e26 is not None:
            macd_values.append(e12 - e26)

    if len(macd_values) < 9:
        return None, None, None

    signal = ema(macd_values, 9)

    current = macd_values[-1]

    histogram = current - signal

    return current, signal, histogram


# =========================================================
# ATR
# =========================================================

def atr(candles, period=14):

    if len(candles) <= period:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    return sum(trs[-period:]) / period


# =========================================================
# ADX
# =========================================================

def adx(candles, period=14):

    if len(candles) < period + 2:
        return None

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        up_move = current["high"] - previous["high"]
        down_move = previous["low"] - current["low"]

        if up_move > down_move and up_move > 0:
            plus = up_move
        else:
            plus = 0

        if down_move > up_move and down_move > 0:
            minus = down_move
        else:
            minus = 0

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        trs.append(tr)
        plus_dm.append(plus)
        minus_dm.append(minus)

    tr_avg = sum(trs[-period:]) / period
    plus_avg = sum(plus_dm[-period:]) / period
    minus_avg = sum(minus_dm[-period:]) / period

    if tr_avg == 0:
        return 0

    plus_di = 100 * plus_avg / tr_avg
    minus_di = 100 * minus_avg / tr_avg

    denominator = plus_di + minus_di

    if denominator == 0:
        return 0

    return 100 * abs(plus_di - minus_di) / denominator


# =========================================================
# STOCH RSI
# =========================================================

def stoch_rsi(values, period=14):

    if len(values) < period * 2:
        return None

    rsi_values = []

    for i in range(period, len(values)):

        current = values[:i + 1]

        value = rsi(current, period)

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
        (rsi_values[-1] - lowest)
        / (highest - lowest)
    ) * 100


# =========================================================
# BOLLINGER
# =========================================================

def bollinger(values, period=20):

    if len(values) < period:
        return None, None, None

    recent = values[-period:]

    middle = sum(recent) / period

    variance = sum(
        (x - middle) ** 2
        for x in recent
    ) / period

    deviation = math.sqrt(variance)

    upper = middle + 2 * deviation
    lower = middle - 2 * deviation

    return upper, middle, lower


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

    if total_volume == 0:
        return None

    total = 0

    for x in recent:

        typical = (
            x["high"]
            + x["low"]
            + x["close"]
        ) / 3

        total += typical * x["volume"]

    return total / total_volume


# =========================================================
# OBV
# =========================================================

def obv_delta(candles, period=20):

    if len(candles) < period + 1:
        return 0

    obv_values = []
    value = 0

    for i in range(1, len(candles)):

        if candles[i]["close"] > candles[i - 1]["close"]:
            value += candles[i]["volume"]

        elif candles[i]["close"] < candles[i - 1]["close"]:
            value -= candles[i]["volume"]

        obv_values.append(value)

    if len(obv_values) < period:
        return 0

    return obv_values[-1] - obv_values[-period]


# =========================================================
# LONG / SHORT HACİM TAHMİNİ
# =========================================================

def volume_direction(candles, period=20):

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
            long_volume += volume * 0.5
            short_volume += volume * 0.5
            continue

        body_ratio = clamp(
            body / candle_range,
            0,
            1
        )

        if candle["close"] > candle["open"]:

            long_volume += (
                volume
                * (0.5 + body_ratio * 0.5)
            )

            short_volume += (
                volume
                * (0.5 - body_ratio * 0.5)
            )

        elif candle["close"] < candle["open"]:

            short_volume += (
                volume
                * (0.5 + body_ratio * 0.5)
            )

            long_volume += (
                volume
                * (0.5 - body_ratio * 0.5)
            )

        else:

            long_volume += volume * 0.5
            short_volume += volume * 0.5

    total = long_volume + short_volume

    if total <= 0:
        return 50, 50, "NÖTR"

    long_pct = (
        long_volume / total
    ) * 100

    short_pct = (
        short_volume / total
    ) * 100

    difference = long_pct - short_pct

    if difference >= 8:
        direction = "LONG"

    elif difference <= -8:
        direction = "SHORT"

    else:
        direction = "NÖTR"

    return long_pct, short_pct, direction


def volume_ratio(candles, period=20):

    if len(candles) < period + 1:
        return 1

    current_volume = candles[-1]["volume"]

    previous = candles[-period - 1:-1]

    average = sum(
        x["volume"]
        for x in previous
    ) / len(previous)

    if average <= 0:
        return 1

    return current_volume / average


# =========================================================
# ANA ANALİZ
# =========================================================

def analyze_coin(product, candles):

    if len(candles) < 60:
        return None

    closes = [
        x["close"]
        for x in candles
    ]

    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    rsi_value = rsi(closes, 14)

    macd_value, macd_signal, macd_hist = macd(closes)

    adx_value = adx(candles, 14)

    atr_value = atr(candles, 14)

    stoch_value = stoch_rsi(closes, 14)

    upper_bb, middle_bb, lower_bb = bollinger(
        closes,
        20
    )

    vwap_value = vwap(candles, 20)

    obv_value = obv_delta(candles, 20)

    vol_ratio = volume_ratio(candles, 20)

    long_vol, short_vol, vol_direction = volume_direction(
        candles,
        20
    )

    candles_4h = aggregate_4h(candles)

    if len(candles_4h) >= 60:

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

    else:

        ema20_4h = None
        ema50_4h = None

    # -----------------------------------------------------
    # SKORLAR
    # -----------------------------------------------------

    long_score = 0
    short_score = 0

    reasons_long = []
    reasons_short = []

    # TREND
    if ema20 is not None and ema50 is not None:

        if price > ema20 > ema50:

            long_score += 30
            reasons_long.append("1H trend yukarı")

        elif price < ema20 < ema50:

            short_score += 30
            reasons_short.append("1H trend aşağı")

        elif price > ema50:

            long_score += 15
            reasons_long.append("Fiyat EMA50 üzerinde")

        elif price < ema50:

            short_score += 15
            reasons_short.append("Fiyat EMA50 altında")

    # 4H TREND
    htf_long = False
    htf_short = False

    if ema20_4h is not None and ema50_4h is not None:

        if price > ema20_4h > ema50_4h:

            long_score += 20
            htf_long = True
            reasons_long.append("4H trend yukarı")

        elif price < ema20_4h < ema50_4h:

            short_score += 20
            htf_short = True
            reasons_short.append("4H trend aşağı")

        elif price > ema50_4h:

            long_score += 10

        elif price < ema50_4h:

            short_score += 10

    # RSI
    if rsi_value is not None:

        if rsi_value >= 55:

            long_score += 12
            reasons_long.append(
                f"RSI güçlü ({rsi_value:.1f})"
            )

        elif rsi_value <= 45:

            short_score += 12
            reasons_short.append(
                f"RSI zayıf ({rsi_value:.1f})"
            )

    # MACD
    if macd_hist is not None:

        if macd_hist > 0:

            long_score += 13
            reasons_long.append(
                "MACD pozitif"
            )

        elif macd_hist < 0:

            short_score += 13
            reasons_short.append(
                "MACD negatif"
            )

    # ADX
    adx_strong = False

    if adx_value is not None:

        if adx_value >= 25:

            adx_strong = True

            if long_score >= short_score:
                long_score += 10
            else:
                short_score += 10

    # STOCH RSI
    if stoch_value is not None:

        if stoch_value >= 60:

            long_score += 5

        elif stoch_value <= 40:

            short_score += 5

    # VWAP
    vwap_support_long = False
    vwap_support_short = False

    if vwap_value is not None:

        if price > vwap_value:

            long_score += 5
            vwap_support_long = True

        elif price < vwap_value:

            short_score += 5
            vwap_support_short = True

    # OBV
    if obv_value > 0:

        long_score += 5

    elif obv_value < 0:

        short_score += 5

    # HACİM YÖNÜ
    if vol_direction == "LONG":

        long_score += 10

    elif vol_direction == "SHORT":

        short_score += 10

    # VOLATİLİTE / BOLLINGER
    if (
        upper_bb is not None
        and lower_bb is not None
    ):

        band_width = upper_bb - lower_bb

        if band_width > 0:

            position = (
                price - lower_bb
            ) / band_width

            if position >= 0.65:

                long_score += 5

            elif position <= 0.35:

                short_score += 5

    # -----------------------------------------------------
    # KARAR
    # -----------------------------------------------------

    difference = abs(
        long_score - short_score
    )

    if difference < 15:

        decision = "BEKLE"

    elif long_score > short_score:

        decision = "LONG"

    else:

        decision = "SHORT"

    raw_score = max(
        long_score,
        short_score
    )

    # Skoru 0-100 arasına getir
    strength = int(
        clamp(
            raw_score,
            0,
            100
        )
    )

    # -----------------------------------------------------
    # GÜÇLÜ SİNYAL FİLTRESİ
    # -----------------------------------------------------

    confirmation_count = 0

    if decision == "LONG":

        if price > ema20:
            confirmation_count += 1

        if htf_long:
            confirmation_count += 1

        if adx_strong:
            confirmation_count += 1

        if vol_direction == "LONG":
            confirmation_count += 1

        if vwap_support_long:
            confirmation_count += 1

    elif decision == "SHORT":

        if price < ema20:
            confirmation_count += 1

        if htf_short:
            confirmation_count += 1

        if adx_strong:
            confirmation_count += 1

        if vol_direction == "SHORT":
            confirmation_count += 1

        if vwap_support_short:
            confirmation_count += 1

    # 90+ için en az 4/5 onay
    if (
        decision in ["LONG", "SHORT"]
        and strength >= 90
        and confirmation_count < 4
    ):
        strength = 89

    # Düşük hacimde güçlü alarm verme
    if vol_ratio < 0.70:
        strength = min(strength, 84)

    # Hacim kararın tersiyse 90+ engelle
    if decision == "LONG" and vol_direction == "SHORT":
        strength = min(strength, 89)

    if decision == "SHORT" and vol_direction == "LONG":
        strength = min(strength, 89)

    # -----------------------------------------------------
    # TEYİT
    # -----------------------------------------------------

    if strength >= 90:

        confirmation = "🟢 GÜÇLÜ TEYİT"

    elif strength >= 70:

        confirmation = "🟡 TEYİT BEKLE"

    else:

        confirmation = "⚪ ZAYIF"

    # -----------------------------------------------------
    # NEDENLER
    # -----------------------------------------------------

    if decision == "LONG":

        reasons = reasons_long[:]

        if macd_hist is not None and macd_hist <= 0:
            reasons.append("MACD henüz pozitif değil")

        if rsi_value is not None and rsi_value < 55:
            reasons.append("RSI tam güçlü değil")

        if vol_direction != "LONG":
            reasons.append(
                "Hacim yönü LONG'u tam desteklemiyor"
            )

    elif decision == "SHORT":

        reasons = reasons_short[:]

        if macd_hist is not None and macd_hist >= 0:
            reasons.append("MACD henüz negatif değil")

        if rsi_value is not None and rsi_value > 45:
            reasons.append("RSI tam zayıf değil")

        if vol_direction != "SHORT":
            reasons.append(
                "Hacim yönü SHORT'u tam desteklemiyor"
            )

    else:

        reasons = [
            "LONG ve SHORT skorları birbirine yakın",
            "Net yön için teyit bekleniyor"
        ]

    # -----------------------------------------------------
    # TRADE PLAN
    # -----------------------------------------------------

    stop = None
    tp1 = None
    tp2 = None

    if atr_value and atr_value > 0:

        if decision == "LONG":

            stop = price - (
                atr_value * 1.2
            )

            tp1 = price + (
                atr_value * 1.5
            )

            tp2 = price + (
                atr_value * 2.5
            )

        elif decision == "SHORT":

            stop = price + (
                atr_value * 1.2
            )

            tp1 = price - (
                atr_value * 1.5
            )

            tp2 = price - (
                atr_value * 2.5
            )

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
        "volume_direction": vol_direction,

        "long_score": long_score,
        "short_score": short_score,

        "decision": decision,
        "strength": strength,

        "confirmation": confirmation,
        "confirmation_count": confirmation_count,

        "reasons": reasons,

        "stop": stop,
        "tp1": tp1,
        "tp2": tp2
    }


# =========================================================
# RAPOR
# =========================================================

def build_report(result):

    p = result["product"]
    base = p["base"]

    decision = result["decision"]
    strength = result["strength"]

    if decision == "LONG":
        direction = "🟢 LONG İÇİN UYGUN"

    elif decision == "SHORT":
        direction = "🔴 SHORT İÇİN UYGUN"

    else:
        direction = "🟡 BEKLE"

    bars = int(
        clamp(
            round(strength / 10),
            0,
            10
        )
    )

    strength_bar = (
        "█" * bars
        + "░" * (10 - bars)
    )

    reasons = "\n".join(
        f"• {x}"
        for x in result["reasons"][:6]
    )

    volume_ratio_percent = (
        result["volume_ratio"] * 100
    )

    if volume_ratio_percent >= 130:
        volume_level = "🔥 YÜKSEK"

    elif volume_ratio_percent >= 90:
        volume_level = "🟢 NORMAL"

    else:
        volume_level = "🔵 DÜŞÜK"

    text = f"""
🚀 CRYPTO JET V10.2
━━━━━━━━━━━━━━━━

🪙 {base}
⏱ Zaman dilimi: 1 Saat

💰 Fiyat: {fmt_price(result["price"])}

{direction}

{result["confirmation"]}

💪 SİNYAL GÜCÜ
{strength_bar} %{strength}

📊 SKOR
🟢 LONG: {result["long_score"]}
🔴 SHORT: {result["short_score"]}

📈 TEKNİK VERİLER

EMA20: {fmt_price(result["ema20"])}
EMA50: {fmt_price(result["ema50"])}

4H EMA20: {fmt_price(result["ema20_4h"])}
4H EMA50: {fmt_price(result["ema50_4h"])}

RSI: {result["rsi"]:.2f}
MACD: {result["macd"]:.4f}
MACD Histogram: {result["macd_hist"]:.4f}
ADX: {result["adx"]:.2f}
Stoch RSI: {result["stoch"]:.1f}

VWAP: {fmt_price(result["vwap"])}

📊 HACİM ANALİZİ

🟢 LONG Hacmi: %{result["long_volume"]:.1f}
🔴 SHORT Hacmi: %{result["short_volume"]:.1f}

💡 Hacim Yönü: {result["volume_direction"]}

🔥 Hacim Gücü:
%{volume_ratio_percent:.0f}
{volume_level}

🧠 NEDEN?

{reasons}

🎯 İŞLEM PLANI
"""

    if decision in ["LONG", "SHORT"]:

        if result["stop"] is not None:

            text += f"""
Giriş: {fmt_price(result["price"])}
Stop: {fmt_price(result["stop"])}
TP1: {fmt_price(result["tp1"])}
TP2: {fmt_price(result["tp2"])}
"""

        text += f"""
Teyit: {result["confirmation_count"]}/5
"""

    else:

        text += """
Şu an net işlem yönü yok.
"""

    text += """
━━━━━━━━━━━━━━━━
⚠️ Sinyal garanti kâr değildir.
"""

    return text


# =========================================================
# KISA TARAMA SATIRI
# =========================================================

def scan_line(result):

    base = result["product"]["base"]
    decision = result["decision"]
    strength = result["strength"]

    if decision == "LONG":
        icon = "🟢"

    elif decision == "SHORT":
        icon = "🔴"

    else:
        icon = "🟡"

    return (
        f"{base:<10} "
        f"{icon} "
        f"{decision:<5} "
        f"%{strength}"
    )


# =========================================================
# ALARM KONTROLÜ
# =========================================================

def should_alert(result):

    decision = result["decision"]
    strength = result["strength"]

    if decision not in ["LONG", "SHORT"]:
        return False

    if strength < 90:
        return False

    product_id = result["product"]["id"]

    key = (
        decision,
        strength // 2
    )

    now = time.time()

    previous = alert_state.get(
        product_id
    )

    if previous:

        previous_key = previous["key"]
        previous_time = previous["time"]

        if (
            previous_key == key
            and now - previous_time < ALERT_COOLDOWN
        ):
            return False

    alert_state[product_id] = {
        "key": key,
        "time": now
    }

    return True


# =========================================================
# TÜM COİNLERİ TARA
# =========================================================

def market_scan(chat_id=None):

    products = get_products()

    if not products:
        print("Coin listesi alınamadı.")
        return []

    results = []

    strong = []

    print(
        f"🚀 Tarama başladı: "
        f"{len(products)} coin"
    )

    for index, product in enumerate(products, 1):

        try:

            candles = get_candles(
                product["id"]
            )

            if len(candles) < 60:
                continue

            result = analyze_coin(
                product,
                candles
            )

            if result is None:
                continue

            results.append(result)

            if should_alert(result):

                strong.append(result)

            print(
                f"[{index}/{len(products)}] "
                f"{product['base']} "
                f"{result['decision']} "
                f"%{result['strength']}"
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

    strong.sort(
        key=lambda x: x["strength"],
        reverse=True
    )

    if chat_id:

        summary = (
            "🚀 CRYPTO JET V10.2\n"
            "━━━━━━━━━━━━━━━━\n\n"
            f"🪙 Analiz edilen: {len(results)} coin\n"
            f"🔥 %90+ teyit: {len(strong)}\n\n"
        )

        if strong:

            summary += "🚨 GÜÇLÜ SİNYALLER\n\n"

            for result in strong[:30]:

                base = result["product"]["base"]

                if result["decision"] == "LONG":
                    icon = "🟢"
                else:
                    icon = "🔴"

                summary += (
                    f"{base:<10} "
                    f"{icon} "
                    f"{result['decision']} "
                    f"%{result['strength']}\n"
                )

        else:

            summary += (
                "Şu an %90+ güçlü sinyal yok.\n"
            )

        summary += "\n📋 TÜM COİNLER\n\n"

        for result in results:

            summary += (
                scan_line(result)
                + "\n"
            )

        send_message(
            chat_id,
            summary
        )

    return results


# =========================================================
# TELEGRAM KOMUTLARI
# =========================================================

def check_updates():

    global active_chat_id

    offset = 0

    print(
        "Crypto Jet Telegram botu başladı!"
    )

    while True:

        try:

            response = session.get(
                f"{API}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 30
                },
                timeout=35
            )

            data = response.json()

            if not data.get("ok"):
                time.sleep(3)
                continue

            for update in data.get(
                "result",
                []
            ):

                offset = update["update_id"] + 1

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                chat_id = message["chat"]["id"]

                active_chat_id = chat_id

                text = (
                    message
                    .get("text", "")
                    .strip()
                    .lower()
                )

                print(
                    "Telegram komutu:",
                    text
                )

                # =========================================
                # /START
                # =========================================

                if text == "/start":

                    send_message(
                        chat_id,
                        """
🚀 CRYPTO JET V10.2

Bot aktif. ✅

Komutlar:

/jet - Tüm sistemi başlat
/btc - BTC analizi
/scan - Tüm coinleri tara

🔥 Jet Alarm: %90+
⏱ Otomatik tarama: 10 dakika

⚠️ Sinyaller garanti kâr değildir.
"""
                    )

                # =========================================
                # /JET
                # =========================================

                elif text == "/jet":

                    send_message(
                        chat_id,
                        """
🚀 CRYPTO JET AKTİF

━━━━━━━━━━━━━━━━

₿ BTC analizi       ✅
🪙 Tüm coin taraması ✅
📊 Teknik analiz     ✅
📈 LONG/SHORT hacim  ✅
🚨 %90+ Jet Alarm    ✅
⏱ 10 dk otomatik     ✅

🔥 Sistem başlatılıyor...

İlk tarama şimdi yapılıyor.
"""
                    )

                    # İlk taramayı hemen yap
                    market_scan(chat_id)

                # =========================================
                # /BTC
                # =========================================

                elif text == "/btc":

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

                        continue

                    send_message(
                        chat_id,
                        "₿ BTC analiz ediliyor..."
                    )

                    candles = get_candles(
                        btc["id"]
                    )

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

                # =========================================
                # /SCAN
                # =========================================

                elif text == "/scan":

                    send_message(
                        chat_id,
                        """
🔎 CRYPTO JET

Tüm uygun Coinbase coinleri
taranıyor...

⏱ Biraz sürebilir.
"""
                    )

                    market_scan(
                        chat_id
                    )

                # =========================================
                # BİLİNMEYEN KOMUT
                # =========================================

                elif text.startswith("/"):

                    send_message(
                        chat_id,
                        """
❓ Bilinmeyen komut.

Kullan:

/jet
/btc
/scan
"""
                    )

        except Exception as e:

            print(
                "Telegram hata:",
                e
            )

            time.sleep(5)


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
        "⏱ Otomatik 10 dakikalık tarama başladı."
    )

    results = market_scan(
        None
    )

    if not results:
        return

    strong_results = [
        x
        for x in results
        if (
            x["decision"]
            in ["LONG", "SHORT"]
            and x["strength"] >= 90
        )
    ]

    for result in strong_results:

        if should_alert(result):

            base = result["product"]["base"]

            if result["strength"] >= 98:

                title = "💥 JET ALERT — EXTREME"

            elif result["strength"] >= 95:

                title = "🚀 JET ALERT — ÇOK GÜÇLÜ"

            else:

                title = "🔥 JET ALERT — GÜÇLÜ"

            message = (
                f"{title}\n"
                f"━━━━━━━━━━━━━━━━\n\n"
                f"🪙 {base}\n"
                f"📊 Yön: {result['decision']}\n"
                f"💪 Güç: %{result['strength']}\n\n"
                f"{build_report(result)}"
            )

            send_message(
                active_chat_id,
                message
            )


# =========================================================
# ANA PROGRAM
# =========================================================

if __name__ == "__main__":

    print(
        "🚀 CRYPTO JET V10.2 BAŞLADI"
    )

    # Telegram güncellemelerini ayrı thread olmadan
    # çalıştırmak için polling + otomatik tarama
    # aynı döngüde yönetiliyor.

    offset = 0

    while True:

        try:

            response = session.get(
                f"{API}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 10
                },
                timeout=15
            )

            data = response.json()

            if data.get("ok"):

                for update in data.get(
                    "result",
                    []
                ):

                    offset = (
                        update["update_id"] + 1
                    )

                    message = update.get(
                        "message"
                    )

                    if not message:
                        continue

                    chat_id = message["chat"]["id"]

                    active_chat_id = chat_id

                    text = (
                        message
                        .get("text", "")
                        .strip()
                        .lower()
                    )

                    print(
                        "Komut:",
                        text
                    )

                    if text == "/start":

                        send_message(
                            chat_id,
                            """
🚀 CRYPTO JET V10.2

Bot aktif. ✅

/jet → Sistemi başlat
/btc → BTC analizi
/scan → Tüm coinleri tara

🚨 Jet Alarm: %90+
⏱ Otomatik tarama: 10 dk
"""
                        )

                    elif text == "/jet":

                        send_message(
                            chat_id,
                            """
🚀 CRYPTO JET AKTİF

━━━━━━━━━━━━━━━━

₿ BTC                 ✅
🪙 TÜM COİNLER         ✅
📊 TEKNİK ANALİZ       ✅
📈 LONG/SHORT HACİM    ✅
🚨 JET ALARM %90+      ✅
⏱ 10 DAKİKA OTOMATİK  ✅

🔥 İlk tarama başlıyor...
"""
                        )

                        results = market_scan(
                            chat_id
                        )

                        last_scan_time = time.time()

                    elif text == "/btc":

                        products = get_products()

                        btc = None

                        for product in products:

                            if product["base"] == "BTC":

                                btc = product
                                break

                        if btc:

                            send_message(
                                chat_id,
                                "₿ BTC analiz ediliyor..."
                            )

                            candles = get_candles(
                                btc["id"]
                            )

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

                        else:

                            send_message(
                                chat_id,
                                "❌ BTC bulunamadı."
                            )

                    elif text == "/scan":

                        send_message(
                            chat_id,
                            """
🔎 CRYPTO JET

Tüm coinler taranıyor...

⏱ Bu işlem biraz sürebilir.
"""
                        )

                        results = market_scan(
                            chat_id
                        )

                        last_scan_time = time.time()

                    elif text.startswith("/"):

                        send_message(
                            chat_id,
                            """
❓ Komut bulunamadı.

Kullan:

/jet
/btc
/scan
"""
                        )

            # Otomatik 10 dakika kontrolü
            automatic_scan()

            time.sleep(1)

        except Exception as e:

            print(
                "ANA DÖNGÜ HATASI:",
                e
            )

            time.sleep(5)
