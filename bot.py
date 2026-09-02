import os
import time
import requests
from datetime import datetime

# =========================================================
# 🚀 CRYPTO JET V8
# Coinbase tabanlı karar destek sistemi
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

offset = 0
active_chat_id = None

# ---------------------------------------------------------
# COINS
# ---------------------------------------------------------

COINS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "BNB": "BNB-USD",
    "ADA": "ADA-USD",
    "DOGE": "DOGE-USD",
    "AVAX": "AVAX-USD",
    "LINK": "LINK-USD",
    "LTC": "LTC-USD",
}

# Güçlü alarm
ALERT_MIN = 85

# Son gönderilen alarmlar
last_alerts = {}

# =========================================================
# TELEGRAM
# =========================================================

def send_message(chat_id, text):
    try:
        requests.post(
            f"{API}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text
            },
            timeout=15
        )
    except Exception as e:
        print("Telegram hata:", e)


# =========================================================
# COINBASE CANDLES
# =========================================================

def get_candles(symbol):

    try:

        url = (
            f"https://api.exchange.coinbase.com/"
            f"products/{symbol}/candles"
        )

        response = requests.get(
            url,
            params={
                "granularity": 3600
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        if not data:
            return []

        data = sorted(
            data,
            key=lambda x: x[0]
        )

        return data

    except Exception as e:

        print(
            f"Coinbase candles hata {symbol}:",
            e
        )

        return []


# =========================================================
# EMA
# =========================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (
            (price - result) * multiplier
        ) + result

    return result


# =========================================================
# RSI
# =========================================================

def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = values[i] - values[i - 1]

        if change >= 0:
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

def calculate_macd(values):

    if len(values) < 35:
        return None, None

    ema12 = calculate_ema(values, 12)
    ema26 = calculate_ema(values, 26)

    if ema12 is None or ema26 is None:
        return None, None

    macd = ema12 - ema26

    return macd, ema12


# =========================================================
# ADX
# =========================================================

def calculate_adx(candles, period=14):

    if len(candles) < period + 2:
        return None

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(candles)):

        prev = candles[i - 1]
        curr = candles[i]

        prev_high = float(prev[2])
        prev_low = float(prev[1])
        prev_close = float(prev[4])

        high = float(curr[2])
        low = float(curr[1])

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        up = high - prev_high
        down = prev_low - low

        pdm = up if up > down and up > 0 else 0
        mdm = down if down > up and down > 0 else 0

        trs.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    if len(trs) < period:
        return None

    atr = sum(trs[:period]) / period
    plus = sum(plus_dm[:period]) / period
    minus = sum(minus_dm[:period]) / period

    dx_values = []

    for i in range(period, len(trs)):

        atr = (
            (atr * (period - 1))
            + trs[i]
        ) / period

        plus = (
            (plus * (period - 1))
            + plus_dm[i]
        ) / period

        minus = (
            (minus * (period - 1))
            + minus_dm[i]
        ) / period

        if atr == 0:
            continue

        plus_di = 100 * plus / atr
        minus_di = 100 * minus / atr

        total = plus_di + minus_di

        if total == 0:
            continue

        dx = (
            100
            * abs(plus_di - minus_di)
            / total
        )

        dx_values.append(dx)

    if not dx_values:
        return None

    return sum(
        dx_values[-period:]
    ) / min(
        period,
        len(dx_values)
    )


# =========================================================
# HACİM ANALİZİ
# =========================================================

def volume_analysis(volumes):

    if len(volumes) < 21:
        return None, None, None

    current = volumes[-1]

    previous = volumes[-21:-1]

    average = sum(previous) / len(previous)

    if average == 0:
        return current, average, None

    strength = (
        current / average
    ) * 100

    return current, average, strength


# =========================================================
# SON MUM
# =========================================================

def candle_analysis(candle):

    timestamp = candle[0]
    low = float(candle[1])
    high = float(candle[2])
    open_price = float(candle[3])
    close = float(candle[4])
    volume = float(candle[5])

    if close > open_price:
        direction = "ALICI"
    elif close < open_price:
        direction = "SATICI"
    else:
        direction = "NÖTR"

    return {
        "time": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "direction": direction
    }


# =========================================================
# POZİSYON KARARI
# =========================================================

def make_decision(
    price,
    ema20,
    ema50,
    rsi,
    macd,
    adx,
    volume_strength,
    candle_direction
):

    long_score = 0
    short_score = 0

    reasons_long = []
    reasons_short = []

    # -----------------------------------------------------
    # EMA / TREND
    # -----------------------------------------------------

    if ema20 is not None and ema50 is not None:

        if ema20 > ema50:

            long_score += 25
            reasons_long.append(
                "Trend yukarı"
            )

        else:

            short_score += 25
            reasons_short.append(
                "Trend aşağı"
            )

        if price > ema20:

            long_score += 10

        else:

            short_score += 10

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    if rsi is not None:

        if rsi < 30:

            # Çok düşük RSI tek başına SHORT değildir.
            # Aşırı satım olduğu için LONG'a küçük destek.
            long_score += 8
            reasons_long.append(
                "RSI aşırı satım"
            )

        elif rsi < 45:

            short_score += 10
            reasons_short.append(
                "RSI zayıf"
            )

        elif rsi > 70:

            short_score += 8
            reasons_short.append(
                "RSI aşırı alım"
            )

        elif rsi > 55:

            long_score += 10
            reasons_long.append(
                "RSI güçlü"
            )

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    if macd is not None:

        if macd > 0:

            long_score += 20
            reasons_long.append(
                "MACD pozitif"
            )

        else:

            short_score += 20
            reasons_short.append(
                "MACD negatif"
            )

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    if adx is not None and adx >= 25:

        if long_score > short_score:

            long_score += 15

        else:

            short_score += 15

    # -----------------------------------------------------
    # HACİM
    # -----------------------------------------------------

    if volume_strength is not None:

        if volume_strength >= 130:

            if long_score > short_score:

                long_score += 10
                reasons_long.append(
                    "Hacim güçlü"
                )

            elif short_score > long_score:

                short_score += 10
                reasons_short.append(
                    "Hacim güçlü"
                )

    # -----------------------------------------------------
    # SON MUM
    # -----------------------------------------------------

    if candle_direction == "ALICI":

        long_score += 5

    elif candle_direction == "SATICI":

        short_score += 5

    # -----------------------------------------------------
    # KARAR
    # -----------------------------------------------------

    total = max(
        long_score,
        short_score
    )

    difference = abs(
        long_score - short_score
    )

    # Çok yakınsa BEKLE
    if difference < 15:

        return {
            "decision": "BEKLE",
            "strength": 0,
            "long_score": long_score,
            "short_score": short_score,
            "reasons": [
                "LONG ve SHORT birbirine yakın"
            ]
        }

    if long_score > short_score:

        strength = min(
            100,
            int(
                55
                + difference
                + (total * 0.15)
            )
        )

        return {
            "decision": "LONG",
            "strength": strength,
            "long_score": long_score,
            "short_score": short_score,
            "reasons": reasons_long
        }

    strength = min(
        100,
        int(
            55
            + difference
            + (total * 0.15)
        )
    )

    return {
        "decision": "SHORT",
        "strength": strength,
        "long_score": long_score,
        "short_score": short_score,
        "reasons": reasons_short
    }


# =========================================================
# STOP / TAKE PROFIT
# =========================================================

def calculate_levels(
    price,
    decision,
    candles
):

    recent = candles[-14:]

    highs = [
        float(x[2])
        for x in recent
    ]

    lows = [
        float(x[1])
        for x in recent
    ]

    recent_high = max(highs)
    recent_low = min(lows)

    risk_range = recent_high - recent_low

    if risk_range <= 0:
        risk_range = price * 0.01

    if decision == "LONG":

        stop = price - (
            risk_range * 0.35
        )

        tp1 = price + (
            risk_range * 0.50
        )

        tp2 = price + (
            risk_range * 0.90
        )

    elif decision == "SHORT":

        stop = price + (
            risk_range * 0.35
        )

        tp1 = price - (
            risk_range * 0.50
        )

        tp2 = price - (
            risk_range * 0.90
        )

    else:

        stop = None
        tp1 = None
        tp2 = None

    return stop, tp1, tp2


# =========================================================
# ANALİZ
# =========================================================

def analyze_coin(coin):

    symbol = COINS[coin]

    candles = get_candles(symbol)

    if len(candles) < 60:
        return None

    closes = [
        float(x[4])
        for x in candles
    ]

    volumes = [
        float(x[5])
        for x in candles
    ]

    price = closes[-1]

    ema20 = calculate_ema(
        closes,
        20
    )

    ema50 = calculate_ema(
        closes,
        50
    )

    rsi = calculate_rsi(
        closes
    )

    macd, _ = calculate_macd(
        closes
    )

    adx = calculate_adx(
        candles
    )

    current_volume, average_volume, volume_strength = (
        volume_analysis(volumes)
    )

    last_candle = candle_analysis(
        candles[-1]
    )

    decision = make_decision(
        price,
        ema20,
        ema50,
        rsi,
        macd,
        adx,
        volume_strength,
        last_candle["direction"]
    )

    stop, tp1, tp2 = calculate_levels(
        price,
        decision["decision"],
        candles
    )

    return {
        "coin": coin,
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "macd": macd,
        "adx": adx,
        "volume": current_volume,
        "average_volume": average_volume,
        "volume_strength": volume_strength,
        "candle": last_candle,
        "decision": decision["decision"],
        "strength": decision["strength"],
        "long_score": decision["long_score"],
        "short_score": decision["short_score"],
        "reasons": decision["reasons"],
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2
    }


# =========================================================
# YARDIMCI FORMAT
# =========================================================

def money(value):

    if value is None:
        return "N/A"

    return f"${value:,.2f}"


def num(value):

    if value is None:
        return "N/A"

    return f"{value:.2f}"


def volume_text(value):

    if value is None:
        return "N/A"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:.2f}"


def strength_bar(value):

    if value <= 0:
        return "░░░░░░░░░░"

    blocks = int(
        value / 10
    )

    blocks = max(
        1,
        min(10, blocks)
    )

    return (
        "█" * blocks
        + "░" * (10 - blocks)
    )


# =========================================================
# RAPOR
# =========================================================

def format_report(result):

    decision = result["decision"]
    strength = result["strength"]

    if decision == "LONG":

        decision_text = (
            "🟢 LONG İÇİN UYGUN"
        )

    elif decision == "SHORT":

        decision_text = (
            "🔴 SHORT İÇİN UYGUN"
        )

    else:

        decision_text = (
            "⚪ BEKLE"
        )

    if result["volume_strength"] is not None:

        volume_strength_text = (
            f"%{result['volume_strength']:.0f}"
        )

        if result["volume_strength"] >= 150:
            volume_status = "🚀 ÇOK GÜÇLÜ"

        elif result["volume_strength"] >= 120:
            volume_status = "🔥 GÜÇLÜ"

        elif result["volume_strength"] >= 80:
            volume_status = "🟡 NORMAL"

        else:
            volume_status = "🔵 DÜŞÜK"

    else:

        volume_strength_text = "N/A"
        volume_status = "N/A"

    if result["candle"]["direction"] == "ALICI":
        candle_text = "🟢 ALICI MUM"

    elif result["candle"]["direction"] == "SATICI":
        candle_text = "🔴 SATICI MUM"

    else:
        candle_text = "⚪ NÖTR MUM"

    reasons = result["reasons"]

    if reasons:

        reason_text = "\n".join(
            f"• {x}"
            for x in reasons[:5]
        )

    else:

        reason_text = (
            "• Yeterli yön desteği yok"
        )

    if decision == "BEKLE":

        trade_plan = """
⚪ POZİSYON AÇMA
Şimdilik bekle.
Yön netleşince yeni sinyal gelecek.
"""

    else:

        trade_plan = f"""
🎯 İŞLEM PLANI

Giriş:  {money(result["price"])}
🛑 Stop: {money(result["stop"])}
✅ TP1:  {money(result["tp1"])}
🚀 TP2:  {money(result["tp2"])}
"""

    text = f"""
🚀 CRYPTO JET V8
━━━━━━━━━━━━━━━━━━━━

₿ {result["coin"]} / 1 SAAT

💰 FİYAT
{money(result["price"])}

━━━━━━━━━━━━━━━━━━━━
🎯 POZİSYON KARARI
━━━━━━━━━━━━━━━━━━━━

{decision_text}

💪 SİNYAL GÜCÜ
{strength_bar(strength)} %{strength}

━━━━━━━━━━━━━━━━━━━━
🧠 NEDEN?
━━━━━━━━━━━━━━━━━━━━

{reason_text}

━━━━━━━━━━━━━━━━━━━━
📊 TREND
━━━━━━━━━━━━━━━━━━━━

EMA20: {num(result["ema20"])}
EMA50: {num(result["ema50"])}

━━━━━━━━━━━━━━━━━━━━
📈 MOMENTUM
━━━━━━━━━━━━━━━━━━━━

RSI:  {num(result["rsi"])}
MACD: {num(result["macd"])}
ADX:  {num(result["adx"])}

━━━━━━━━━━━━━━━━━━━━
📊 HACİM
━━━━━━━━━━━━━━━━━━━━

Mevcut:  {volume_text(result["volume"])}
Ortalama: {volume_text(result["average_volume"])}

🔥 Hacim Gücü: {volume_strength_text}
{volume_status}

━━━━━━━━━━━━━━━━━━━━
🕯️ SON MUM
━━━━━━━━━━━━━━━━━━━━

Açılış: {money(result["candle"]["open"])}
Yüksek: {money(result["candle"]["high"])}
Düşük:  {money(result["candle"]["low"])}
Kapanış: {money(result["candle"]["close"])}

{candle_text}

━━━━━━━━━━━━━━━━━━━━
{trade_plan}
━━━━━━━━━━━━━━━━━━━━

⚠️ Bu sinyal garanti değildir.
Risk yönetimi kullan.

⏱ Analiz: 1 Saat
🔄 Tarama: 10 Dakika
"""

    return text.strip()


# =========================================================
# ALARM
# =========================================================

def alert_level(strength):

    if strength >= 95:
        return "🚀 JET ALERT — EXTREME"

    if strength >= 90:
        return "🔥 JET ALERT — ÇOK GÜÇLÜ"

    if strength >= 85:
        return "🛩️ JET ALERT — GÜÇLÜ"

    return None


def should_alert(coin, decision, strength):

    if decision == "BEKLE":
        return False

    if strength < ALERT_MIN:
        return False

    previous = last_alerts.get(coin)

    if previous is None:

        last_alerts[coin] = (
            decision,
            strength
        )

        return True

    old_decision, old_strength = previous

    if decision != old_decision:

        last_alerts[coin] = (
            decision,
            strength
        )

        return True

    if strength >= old_strength + 5:

        last_alerts[coin] = (
            decision,
            strength
        )

        return True

    return False


def format_alert(result):

    level = alert_level(
        result["strength"]
    )

    if not level:
        return None

    if result["decision"] == "LONG":

        direction = (
            "🟢 LONG İÇİN UYGUN"
        )

    else:

        direction = (
            "🔴 SHORT İÇİN UYGUN"
        )

    return f"""
{level}
━━━━━━━━━━━━━━━━

⚡ {result["coin"]}

{direction}

💰 {money(result["price"])}

💪 GÜÇ
{strength_bar(result["strength"])}
%{result["strength"]}

📊 Hacim Gücü:
%{result["volume_strength"]:.0f}
"""

    # unreachable
# =========================================================
# TELEGRAM COMMAND
# =========================================================

def handle_command(chat_id, text):

    global active_chat_id

    active_chat_id = chat_id

    command = text.strip().lower()

    if command == "/start":

        send_message(
            chat_id,
            """
🚀 CRYPTO JET V8 AKTİF
━━━━━━━━━━━━━━━━

🟢 Coinbase veri sistemi
🧠 Akıllı karar sistemi
📊 Hacim analizi
📈 Trend + momentum
🎯 LONG / SHORT / BEKLE

🔔 %85+ güçlü sinyaller alarm verir.

Komut:

/btc
BTC analizini gösterir.

━━━━━━━━━━━━━━━━
⚠️ Crypto Jet karar destek sistemidir.
"""
        )

        return

    if command == "/btc":

        result = analyze_coin("BTC")

        if result is None:

            send_message(
                chat_id,
                "❌ Coinbase'den BTC verisi alınamadı."
            )

            return

        send_message(
            chat_id,
            format_report(result)
        )

        return


# =========================================================
# TELEGRAM UPDATES
# =========================================================

def check_updates():

    global offset

    try:

        response = requests.get(
            f"{API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 30
            },
            timeout=35
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            return

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

            chat = message.get(
                "chat"
            )

            if not chat:
                continue

            chat_id = chat["id"]

            text = message.get(
                "text",
                ""
            )

            if text:

                handle_command(
                    chat_id,
                    text
                )

    except Exception as e:

        print(
            "Telegram update hata:",
            e
        )


# =========================================================
# OTOMATİK TARAMA
# =========================================================

def scan_market():

    if active_chat_id is None:
        return

    print(
        f"[{datetime.now()}] "
        "Piyasa taraması başladı."
    )

    for coin in COINS:

        try:

            result = analyze_coin(
                coin
            )

            if result is None:
                continue

            print(
                coin,
                result["decision"],
                result["strength"]
            )

            if should_alert(
                coin,
                result["decision"],
                result["strength"]
            ):

                alert = format_alert(
                    result
                )

                if alert:

                    send_message(
                        active_chat_id,
                        alert
                    )

        except Exception as e:

            print(
                f"{coin} tarama hata:",
                e
            )


# =========================================================
# MAIN
# =========================================================

print(
    "🚀 CRYPTO JET V8 BAŞLADI!"
)

last_scan = 0

while True:

    try:

        check_updates()

        now = time.time()

        if (
            active_chat_id is not None
            and now - last_scan >= 600
        ):

            scan_market()

            last_scan = now

        time.sleep(2)

    except Exception as e:

        print(
            "Ana döngü hata:",
            e
        )

        time.sleep(5)
