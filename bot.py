import os
import time
import requests
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
offset = 0
BINANCE = "https://api.binance.com"
print("Crypto Jet V1 başladı!")
def get_json(url, params=None, timeout=15):
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()
def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for price in values[period:]:
        result = (price - result) * multiplier + result
    return result
def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
def macd(values):
    if len(values) < 35:
        return None, None, None
    def build_ema_series(data, period):
        multiplier = 2 / (period + 1)
        current = sum(data[:period]) / period
        series = [current]
        for price in data[period:]:
            current = (price - current) * multiplier + current
            series.append(current)
        return series
    e12 = build_ema_series(values, 12)
    e26 = build_ema_series(values, 26)
    macd_series = []
    start = len(values) - len(e12)
    for i, value in enumerate(e12):
        absolute_index = start + i
        e26_index = absolute_index - (len(values) - len(e26))
        if 0 <= e26_index < len(e26):
            macd_series.append(value - e26[e26_index])
    if len(macd_series) < 9:
        return None, None, None
    signal = ema(macd_series, 9)
    line = macd_series[-1]
    histogram = line - signal if signal is not None else None
    return line, signal, histogram
def get_btc_analysis():
    symbol = "BTCUSDT"
    interval = "1h"
    candles = get_json(
        f"{BINANCE}/api/v3/klines",
        params={
            "symbol": symbol,
            "interval": interval,
            "limit": 250
        }
    )
    if not candles:
        raise ValueError("BTC mum verisi alınamadı.")
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]
    price = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi_value = rsi(closes, 14)
    macd_line, signal_line, histogram = macd(closes)
    avg_volume = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / avg_volume if avg_volume else 0
    score = 0
    reasons = []
    # EMA20
    if price > ema20:
        score += 1
        reasons.append("Fiyat EMA20 üzerinde")
    else:
        score -= 1
        reasons.append("Fiyat EMA20 altında")
    # EMA20 / EMA50
    if ema20 > ema50:
        score += 1
        reasons.append("EMA20 > EMA50")
    else:
        score -= 1
        reasons.append("EMA20 < EMA50")
    # EMA200
    if price > ema200:
        score += 1
        reasons.append("Fiyat EMA200 üzerinde")
    else:
        score -= 1
        reasons.append("Fiyat EMA200 altında")
    # RSI
    if rsi_value >= 55 and rsi_value < 70:
        score += 1
        reasons.append("RSI pozitif bölgede")
    elif rsi_value <= 45 and rsi_value > 30:
        score -= 1
        reasons.append("RSI negatif bölgede")
    elif rsi_value >= 70:
        reasons.append("RSI aşırı alım bölgesinde")
    elif rsi_value <= 30:
        reasons.append("RSI aşırı satım bölgesinde")
    # MACD
    if histogram is not None:
        if histogram > 0:
            score += 1
            reasons.append("MACD pozitif")
        else:
            score -= 1
            reasons.append("MACD negatif")
    # Volume
    if volume_ratio >= 1.20:
        reasons.append(
            f"Hacim ortalamanın {volume_ratio:.1f}x'i"
        )
    # Signal
    if score >= 3:
        signal = "🟢 LONG"
        strength = min(95, 55 + score * 7)
    elif score <= -3:
        signal = "🔴 SHORT"
        strength = min(95, 55 + abs(score) * 7)
    else:
        signal = "🟡 BEKLE"
        strength = 50 + abs(score) * 4
    return {
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi_value,
        "macd": macd_line,
        "signal": signal,
        "strength": strength,
        "volume_ratio": volume_ratio,
        "reasons": reasons
    }
def format_btc_analysis(data):
    macd_text = (
        f"{data['macd']:.4f}"
        if data["macd"] is not None
        else "N/A"
    )
    reasons = "\n".join(
        f"• {reason}"
        for reason in data["reasons"]
    )
    return (
        f"🚀 <b>Crypto Jet V1</b>\n\n"
        f"₿ <b>BTCUSDT</b> | 1H\n"
        f"💰 Fiyat: <b>${data['price']:,.2f}</b>\n\n"
        f"📊 EMA20: {data['ema20']:,.2f}\n"
        f"📊 EMA50: {data['ema50']:,.2f}\n"
        f"📊 EMA200: {data['ema200']:,.2f}\n"
        f"📈 RSI14: {data['rsi']:.2f}\n"
        f"〽️ MACD: {macd_text}\n"
        f"🔊 Hacim: {data['volume_ratio']:.2f}x "
        f"(20 mum ort.)\n\n"
        f"🎯 <b>Sinyal: {data['signal']}</b>\n"
        f"💪 Sinyal gücü: "
        f"<b>%{data['strength']}</b>\n\n"
        f"<b>Nedenler:</b>\n"
        f"{reasons}\n\n"
        f"⚠️ Bu bir teknik sinyaldir; "
        f"kesin kazanç garantisi değildir."
    )
def send_message(chat_id, text):
    requests.post(
        f"{API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        },
        timeout=15
    )
while True:
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
        for update in data.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message:
                continue
            chat_id = message["chat"]["id"]
            text = message.get("text", "").strip()
            if text == "/start":
                reply = (
                    "🚀 <b>Crypto Jet V1 çalışıyor!</b>\n\n"
                    "/btc — BTC 1 saatlik analiz"
                )
            elif text == "/btc":
                try:
                    btc_data = get_btc_analysis()
                    reply = format_btc_analysis(btc_data)
                except Exception as e:
                    reply = (
                        f"❌ BTC analizi alınamadı:\n{e}"
                    )
            else:
                reply = (
                    "📌 Kullanabileceğin komut:\n\n"
                    "/btc — Bitcoin 1 saatlik analiz"
                )
            send_message(chat_id, reply)
    except Exception as e:
        print("Hata:", e)
        time.sleep(5)
