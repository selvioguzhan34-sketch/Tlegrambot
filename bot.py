import os
import time
import requests
from datetime import datetime

# =========================================================
# CRYPTO JET V9
# Coinbase Only
# 1 Saatlik Analiz
# 10 Dakikada Bir Tarama
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

offset = 0
active_chat_id = None

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

last_alerts = {}

# =========================================================
# TELEGRAM
# =========================================================

def send_message(chat_id, text):
    try:
        requests.post(
            f"{API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=15
        )
    except Exception as e:
        print("Telegram hata:", e)


# =========================================================
# COINBASE DATA
# =========================================================

def get_candles(symbol, limit=120):

    url = f"https://api.exchange.coinbase.com/products/{symbol}/candles"

    try:
        response = requests.get(
            url,
            params={
                "granularity": 3600
            },
            timeout=15
        )

        if response.status_code != 200:
            print("Coinbase hata:", response.status_code)
            return []

        data = response.json()

        if not isinstance(data, list):
            return []

        data = sorted(data, key=lambda x: x[0])

        return data[-limit:]

    except Exception as e:
        print("Candle hata:", e)
        return []


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (price - result) * multiplier + result

    return result


# =========================================================
# RSI
# =========================================================

def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# =========================================================
# MACD
# =========================================================

def calculate_macd(closes):

    if len(closes) < 35:
        return None

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    if ema12 is None or ema26 is None:
        return None

    return ema12 - ema26


# =========================================================
# ADX
# =========================================================

def calculate_adx(candles, period=14):

    if len(candles) < period + 2:
        return None

    highs = [float(x[2]) for x in candles]
    lows = [float(x[1]) for x in candles]
    closes = [float(x[4]) for x in candles]

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(candles)):

        high = highs[i]
        low = lows[i]

        prev_high = highs[i - 1]
        prev_low = lows[i - 1]
        prev_close = closes[i - 1]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        up_move = high - prev_high
        down_move = prev_low - low

        if up_move > down_move and up_move > 0:
            pdm = up_move
        else:
            pdm = 0

        if down_move > up_move and down_move > 0:
            mdm = down_move
        else:
            mdm = 0

        trs.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    if len(trs) < period:
        return None

    atr = sum(trs[:period]) / period
    p_dm = sum(plus_dm[:period]) / period
    m_dm = sum(minus_dm[:period]) / period

    dx_values = []

    for i in range(period, len(trs)):

        atr = ((atr * (period - 1)) + trs[i]) / period
        p_dm = ((p_dm * (period - 1)) + plus_dm[i]) / period
        m_dm = ((m_dm * (period - 1)) + minus_dm[i]) / period

        if atr == 0:
            continue

        plus_di = 100 * (p_dm / atr)
        minus_di = 100 * (m_dm / atr)

        denominator = plus_di + minus_di

        if denominator == 0:
            continue

        dx = 100 * abs(plus_di - minus_di) / denominator

        dx_values.append(dx)

    if not dx_values:
        return None

    return sum(dx_values[-14:]) / min(14, len(dx_values))


# =========================================================
# ATR
# =========================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = float(candles[i][2])
        low = float(candles[i][1])
        prev_close = float(candles[i - 1][4])

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


# =========================================================
# ANALİZ
# =========================================================

def analyze_coin(name, symbol):

    candles = get_candles(symbol)

    if len(candles) < 60:
        return None

    closes = [float(x[4]) for x in candles]
    volumes = [float(x[5]) for x in candles]

    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    rsi = calculate_rsi(closes)
    macd = calculate_macd(closes)
    adx = calculate_adx(candles)
    atr = calculate_atr(candles)

    if None in (ema20, ema50, rsi, macd, adx, atr):
        return None

    # -----------------------------------------------------
    # SKORLAR
    # -----------------------------------------------------

    long_score = 0
    short_score = 0

    reasons_long = []
    reasons_short = []

    # TREND
    if ema20 > ema50:
        long_score += 25
        reasons_long.append("Trend yukarı")
    else:
        short_score += 25
        reasons_short.append("Trend aşağı")

    # FİYAT / EMA20
    if price > ema20:
        long_score += 10
        reasons_long.append("Fiyat EMA20 üzerinde")
    else:
        short_score += 10
        reasons_short.append("Fiyat EMA20 altında")

    # RSI
    if rsi < 30:
        long_score += 8
        reasons_long.append("RSI aşırı satım bölgesinde")
    elif rsi < 45:
        short_score += 10
        reasons_short.append("RSI zayıf")
    elif rsi > 70:
        short_score += 8
        reasons_short.append("RSI aşırı alım bölgesinde")
    elif rsi > 55:
        long_score += 10
        reasons_long.append("RSI güçlü")

    # MACD
    if macd > 0:
        long_score += 20
        reasons_long.append("MACD pozitif")
    else:
        short_score += 20
        reasons_short.append("MACD negatif")

    # ADX
    if adx >= 25:

        if long_score > short_score:
            long_score += 15
            reasons_long.append("ADX trendi güçlü gösteriyor")

        elif short_score > long_score:
            short_score += 15
            reasons_short.append("ADX trendi güçlü gösteriyor")

    # SON MUM
    last_open = float(candles[-1][3])
    last_high = float(candles[-1][2])
    last_low = float(candles[-1][1])
    last_close = float(candles[-1][4])

    if last_close > last_open:
        long_score += 5
        reasons_long.append("Son mum alıcı")
        candle_direction = "ALICI MUM"
    else:
        short_score += 5
        reasons_short.append("Son mum satıcı")
        candle_direction = "SATICI MUM"

    # HACİM
    current_volume = volumes[-1]

    avg_volume = sum(volumes[-21:-1]) / 20

    if avg_volume > 0:
        volume_ratio = current_volume / avg_volume
    else:
        volume_ratio = 0

    volume_strength = int(min(200, volume_ratio * 100))

    # Hacim sadece yeterliyse skora katkı verir
    if volume_ratio >= 1.30:

        if long_score > short_score:
            long_score += 10
            reasons_long.append("Hacim güçlü")
        elif short_score > long_score:
            short_score += 10
            reasons_short.append("Hacim güçlü")

    # -----------------------------------------------------
    # YÖN
    # -----------------------------------------------------

    difference = abs(long_score - short_score)

    if difference < 15:

        decision = "BEKLE"
        base_strength = 45
        reasons = [
            "Long ve Short tarafı birbirine yakın",
            "Net yön oluşmadı",
            "Ek teyit bekleniyor"
        ]

    elif long_score > short_score:

        decision = "LONG"
        reasons = reasons_long[:5]

        base_strength = 55 + difference

    else:

        decision = "SHORT"
        reasons = reasons_short[:5]

        base_strength = 55 + difference

    # -----------------------------------------------------
    # HACİM FRENİ
    # -----------------------------------------------------

    # Düşük hacimde güçlü sinyal oluşmasını engeller.
    if volume_ratio < 0.50:

        base_strength -= 20

    elif volume_ratio < 0.80:

        base_strength -= 10

    elif volume_ratio >= 1.30:

        base_strength += 5

    # ADX zayıfsa güçlü sinyali frenle
    if adx < 20:

        base_strength -= 10

    elif adx >= 30:

        base_strength += 5

    strength = int(max(0, min(100, base_strength)))

    # -----------------------------------------------------
    # TEYİT SEVİYESİ
    # -----------------------------------------------------

    if decision == "BEKLE":

        confirmation = "⚪ BEKLE"

    elif strength >= 85 and volume_ratio >= 1.00 and adx >= 25:

        confirmation = "🟢 GÜÇLÜ TEYİT"

    elif strength >= 60:

        confirmation = "🟡 TEYİT BEKLE"

    else:

        confirmation = "⚪ ZAYIF"

    # Düşük hacimde %85 üstünü kesinlikle engelle
    if volume_ratio < 1.00 and strength >= 85:

        strength = 84

        confirmation = "🟡 TEYİT BEKLE"

    # -----------------------------------------------------
    # İŞLEM PLANI
    # -----------------------------------------------------

    entry = price

    if decision == "LONG":

        stop = entry - (atr * 1.20)
        tp1 = entry + (atr * 1.50)
        tp2 = entry + (atr * 2.50)

    elif decision == "SHORT":

        stop = entry + (atr * 1.20)
        tp1 = entry - (atr * 1.50)
        tp2 = entry - (atr * 2.50)

    else:

        stop = None
        tp1 = None
        tp2 = None

    return {
        "name": name,
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "macd": macd,
        "adx": adx,
        "current_volume": current_volume,
        "avg_volume": avg_volume,
        "volume_strength": volume_strength,
        "volume_ratio": volume_ratio,
        "decision": decision,
        "strength": strength,
        "confirmation": confirmation,
        "reasons": reasons,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "candle_direction": candle_direction,
        "open": last_open,
        "high": last_high,
        "low": last_low,
        "close": last_close
    }


# =========================================================
# FORMAT
# =========================================================

def format_report(result):

    if not result:
        return "❌ BTC verisi alınamadı."

    name = result["name"]

    decision = result["decision"]
    strength = result["strength"]

    if decision == "LONG":
        decision_text = "🟢 LONG İÇİN UYGUN"
    elif decision == "SHORT":
        decision_text = "🔴 SHORT İÇİN UYGUN"
    else:
        decision_text = "⚪ BEKLE"

    # Güç barı
    blocks = int(strength / 10)

    if blocks > 10:
        blocks = 10

    bar = "█" * blocks + "░" * (10 - blocks)

    reasons_text = "\n".join(
        f"• {reason}" for reason in result["reasons"]
    )

    volume_ratio = result["volume_ratio"]

    if volume_ratio >= 1.50:
        volume_status = "🔥 ÇOK GÜÇLÜ"
    elif volume_ratio >= 1.30:
        volume_status = "🟢 GÜÇLÜ"
    elif volume_ratio >= 1.00:
        volume_status = "🟡 NORMAL"
    else:
        volume_status = "🔵 DÜŞÜK"

    text = f"""
🚀 CRYPTO JET V9
━━━━━━━━━━━━━━━━━━━━

₿ {name} / 1 SAAT

💰 FİYAT
${result["price"]:,.2f}

━━━━━━━━━━━━━━━━━━━━
🎯 POZİSYON KARARI
━━━━━━━━━━━━━━━━━━━━

{decision_text}

{result["confirmation"]}

💪 SİNYAL GÜCÜ
{bar} %{strength}

━━━━━━━━━━━━━━━━━━━━
🧠 NEDEN?
━━━━━━━━━━━━━━━━━━━━

{reasons_text}

━━━━━━━━━━━━━━━━━━━━
📊 TREND
━━━━━━━━━━━━━━━━━━━━

EMA20: {result["ema20"]:.2f}
EMA50: {result["ema50"]:.2f}

━━━━━━━━━━━━━━━━━━━━
📈 MOMENTUM
━━━━━━━━━━━━━━━━━━━━

RSI:  {result["rsi"]:.2f}
MACD: {result["macd"]:.2f}
ADX:  {result["adx"]:.2f}

━━━━━━━━━━━━━━━━━━━━
📊 HACİM
━━━━━━━━━━━━━━━━━━━━

Mevcut:  {result["current_volume"]:.2f}
Ortalama: {result["avg_volume"]:.2f}

🔥 Hacim Gücü: %{result["volume_strength"]}
{volume_status}

━━━━━━━━━━━━━━━━━━━━
🕯️ SON MUM
━━━━━━━━━━━━━━━━━━━━

Açılış: ${result["open"]:,.2f}
Yüksek: ${result["high"]:,.2f}
Düşük:  ${result["low"]:,.2f}
Kapanış: ${result["close"]:,.2f}

{"🟢" if result["close"] > result["open"] else "🔴"} {result["candle_direction"]}

━━━━━━━━━━━━━━━━━━━━
🎯 İŞLEM PLANI
━━━━━━━━━━━━━━━━━━━━
"""

    if decision == "BEKLE":

        text += """
❌ ŞU AN POZİSYON YOK

Yeni teyit bekleniyor.
"""

    else:

        text += f"""
Giriş:  ${result["entry"]:,.2f}
🛑 Stop: ${result["stop"]:,.2f}
✅ TP1: ${result["tp1"]:,.2f}
🚀 TP2: ${result["tp2"]:,.2f}
"""

        if strength >= 85 and volume_ratio >= 1.00 and result["adx"] >= 25:

            text += """
━━━━━━━━━━━━━━━━━━━━

🟢 GÜÇLÜ TEYİT VAR
🔔 Alarm seviyesi aktif
"""

        elif strength >= 60:

            text += """
━━━━━━━━━━━━━━━━━━━━

🟡 TEYİT BEKLENİYOR
❌ Acele pozisyon açma
"""

        else:

            text += """
━━━━━━━━━━━━━━━━━━━━

⚪ SİNYAL ZAYIF
❌ BEKLE
"""

    text += """
━━━━━━━━━━━━━━━━━━━━

⚠️ Bu sistem garanti kâr vermez.
Risk yönetimi kullan.

⏱ Analiz: 1 Saat
🔄 Tarama: 10 Dakika
"""

    return text


# =========================================================
# ALERT
# =========================================================

def format_alert(result):

    decision = result["decision"]
    strength = result["strength"]

    if strength >= 95:

        level = "🚀 JET ALERT — EXTREME"

    elif strength >= 90:

        level = "🔥 JET ALERT — ÇOK GÜÇLÜ"

    elif strength >= 85:

        level = "🛩️ JET ALERT — GÜÇLÜ"

    else:

        return None

    return f"""
{level}
━━━━━━━━━━━━━━━━━━━━

₿ {result["name"]}

{("🟢 LONG" if decision == "LONG" else "🔴 SHORT")}

💪 Sinyal Gücü: %{strength}

{result["confirmation"]}

💰 Fiyat: ${result["price"]:,.2f}

📊 Hacim: %{result["volume_strength"]}
📈 ADX: {result["adx"]:.2f}
📉 RSI: {result["rsi"]:.2f}

🎯 Giriş: ${result["entry"]:,.2f}
🛑 Stop: ${result["stop"]:,.2f}
✅ TP1: ${result["tp1"]:,.2f}
🚀 TP2: ${result["tp2"]:,.2f}

⚠️ Güçlü sinyal teyitlidir,
garanti değildir.
"""


# =========================================================
# KOMUTLAR
# =========================================================

def handle_command(chat_id, text):

    global active_chat_id

    active_chat_id = chat_id

    text = text.strip().lower()

    if text == "/start":

        send_message(
            chat_id,
            """
🚀 CRYPTO JET V9 AKTİF
━━━━━━━━━━━━━━━━

🟢 Coinbase veri sistemi
🧠 Akıllı karar sistemi
📊 Hacim teyidi
📈 Trend + momentum
🎯 LONG / SHORT / BEKLE

🔔 %85+ güçlü ve teyitli sinyaller alarm verir.

⚠️ Düşük hacimde sistem güçlü alarmı frenler.

Komut:

/btc
BTC analizini gösterir.

━━━━━━━━━━━━━━━━
⚠️ Crypto Jet karar destek sistemidir.
"""
        )

    elif text == "/btc":

        result = analyze_coin("BTC", "BTC-USD")

        if result:

            send_message(
                chat_id,
                format_report(result)
            )

        else:

            send_message(
                chat_id,
                "❌ BTC analizi için Coinbase verisi alınamadı."
            )


# =========================================================
# TELEGRAM UPDATE
# =========================================================

def check_updates():

    global offset

    try:

        response = requests.get(
            f"{API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 5
            },
            timeout=10
        )

        data = response.json()

        if not data.get("ok"):
            return

        for update in data.get("result", []):

            offset = update["update_id"] + 1

            message = update.get("message")

            if not message:
                continue

            chat_id = message["chat"]["id"]

            text = message.get("text", "")

            if text:

                handle_command(
                    chat_id,
                    text
                )

    except Exception as e:

        print("Update hata:", e)


# =========================================================
# MARKET SCAN
# =========================================================

def scan_market():

    global active_chat_id

    if active_chat_id is None:
        return

    print("🚀 Market taraması başladı...")

    for name, symbol in COINS.items():

        try:

            result = analyze_coin(
                name,
                symbol
            )

            if not result:
                continue

            print(
                name,
                result["decision"],
                result["strength"],
                "%",
                result["volume_strength"],
                "volume"
            )

            strength = result["strength"]
            decision = result["decision"]

            # Sadece 85+ ve teyitli sinyaller alarm
            if (
                strength >= 85
                and decision in ["LONG", "SHORT"]
                and result["volume_ratio"] >= 1.00
                and result["adx"] >= 25
            ):

                key = name

                current_state = (
                    decision,
                    strength // 5
                )

                previous_state = last_alerts.get(key)

                # Aynı sinyali sürekli gönderme
                if previous_state != current_state:

                    alert = format_alert(result)

                    if alert:

                        send_message(
                            active_chat_id,
                            alert
                        )

                        last_alerts[key] = current_state

            else:

                # Güçlü bölgeden çıkınca tekrar alarm verebilmesi için
                # eski kaydı temizle
                if name in last_alerts:

                    if strength < 80:

                        del last_alerts[name]

        except Exception as e:

            print(
                f"{name} analiz hatası:",
                e
            )

    print("✅ Market taraması tamamlandı.")


# =========================================================
# ANA DÖNGÜ
# =========================================================

print("🚀 CRYPTO JET V9 BAŞLADI")

last_scan_time = 0

while True:

    try:

        check_updates()

        current_time = time.time()

        # 10 dakikada bir tarama
        if current_time - last_scan_time >= 600:

            scan_market()

            last_scan_time = current_time

        time.sleep(2)

    except Exception as e:

        print("Ana döngü hata:", e)

        time.sleep(5)
