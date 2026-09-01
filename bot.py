import os
import time
import requests
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
BINANCE = "https://api.binance.com"
offset = 0
print("🚀 Crypto Jet V2 başladı!")
def get_json(url, params=None, timeout=15):
    response = requests.get(
        url,
        params=params,
        timeout=timeout
    )
    response.raise_for_status()
    return response.json()
def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for price in values[period:]:
        result = ((price - result) * multiplier) + result
    return result
def rsi(values, period=14):
    if len(values) < period + 1:
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
            (avg_gain * (period - 1)) + gains[i]
        ) / period
        avg_loss = (
            (avg_loss * (period - 1)) + losses[i]
        ) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
def build_ema_series(values, period):
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period
    result = [current]
    for price in values[period:]:
        current = (
            (price - current) * multiplier
        ) + current
        result.append(current)
    return result
def macd(values):
    if len(values) < 50:
        return None, None, None
    ema12 = build_ema_series(values, 12)
    ema26 = build_ema_series(values, 26)
    macd_values = []
    start_index = len(values) - len(ema12)
    for i, value in enumerate(ema12):
        absolute_index = start_index + i
        ema26_start = len(values) - len(ema26)
        ema26_index = absolute_index - ema26_start
        if 0 <= ema26_index < len(ema26):
            macd_values.append(
                value - ema26[ema26_index]
            )
    if len(macd_values) < 9:
        return None, None, None
    signal = ema(macd_values, 9)
    if signal is None:
        return None, None, None
    line = macd_values[-1]
    histogram = line - signal
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
        raise ValueError(
            "BTC verisi alınamadı."
        )
    closes = [
        float(candle[4])
        for candle in candles
    ]
    volumes = [
        float(candle[5])
        for candle in candles
    ]
    price = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi_value = rsi(closes, 14)
    macd_line, macd_signal, macd_histogram = macd(
        closes
    )
    # Son mum hacmi / önceki 20 mum ortalaması
    previous_volumes = volumes[-21:-1]
    average_volume = (
        sum(previous_volumes) / len(previous_volumes)
        if previous_volumes
        else 0
    )
    volume_ratio = (
        volumes[-1] / average_volume
        if average_volume > 0
        else 0
    )
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
    if 55 <= rsi_value < 70:
        score += 1
        reasons.append("RSI pozitif bölgede")
    elif 30 < rsi_value <= 45:
        score -= 1
        reasons.append("RSI negatif bölgede")
    elif rsi_value >= 70:
        reasons.append("RSI aşırı alım bölgesinde")
    elif rsi_value <= 30:
        reasons.append("RSI aşırı satım bölgesinde")
    else:
        reasons.append("RSI nötr bölgede")
    # MACD
    if macd_histogram is not None:
        if macd_histogram > 0:
            score += 1
            reasons.append("MACD pozitif")
        else:
            score -= 1
            reasons.append("MACD negatif")
    # Hacim
    if volume_ratio >= 1.20:
        reasons.append(
            f"Hacim güçlü: ortalamanın "
            f"{volume_ratio:.2f}x'i"
        )
    elif volume_ratio < 0.80:
        reasons.append(
            f"Hacim düşük: ortalamanın "
            f"{volume_ratio:.2f}x'i"
        )
    else:
        reasons.append(
            f"Hacim normal: ortalamanın "
            f"{volume_ratio:.2f}x'i"
        )
    # Sinyal
    if score >= 3:
        signal = "🟢 LONG"
        strength = min(
            95,
            55 + (score * 7)
        )
    elif score <= -3:
        signal = "🔴 SHORT"
        strength = min(
            95,
            55 + (abs(score) * 7)
        )
    else:
        signal = "🟡 BEKLE"
        strength = 50 + (abs(score) * 4)
    return {
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi_value,
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "volume_ratio": volume_ratio,
        "signal": signal,
        "strength": strength,
        "score": score,
        "reasons": reasons
    }
def format_btc_analysis(data):
    if data["macd"] is not None:
        macd_text = f"{data['macd']:.4f}"
    else:
        macd_text = "N/A"
    reasons = "\n".join(
        f"• {reason}"
        for reason in data["reasons"]
    )
    return (
        "🚀 <b>CRYPTO JET V2</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "₿ <b>BITCOIN</b>\n"
        "⏱ Zaman dilimi: <b>1 Saat</b>\n\n"
        f"💰 Fiyat: "
        f"<b>${data['price']:,.2f}</b>\n\n"
        "📊 <b>TEKNİK GÖSTERGELER</b>\n"
        f"EMA20: {data['ema20']:,.2f}\n"
        f"EMA50: {data['ema50']:,.2f}\n"
        f"EMA200: {data['ema200']:,.2f}\n"
        f"RSI14: {data['rsi']:.2f}\n"
        f"MACD: {macd_text}\n"
        f"Hacim: {data['volume_ratio']:.2f}x\n\n"
        "🎯 <b>SONUÇ</b>\n"
        f"Sinyal: <b>{data['signal']}</b>\n"
        f"Sinyal gücü: "
        f"<b>%{data['strength']}</b>\n"
        f"Skor: <b>{data['score']}</b>\n\n"
        "📋 <b>ANALİZ NEDENLERİ</b>\n"
        f"{reasons}\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "⚠️ Teknik analizdir. "
        "Kesin kazanç garantisi değildir."
    )
def send_message(chat_id, text):
    response = requests.post(
        f"{API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        },
        timeout=15
    )
    response.raise_for_status()
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
            text = message.get(
                "text",
                ""
            ).strip()
            if text == "/start":
                reply = (
                    "🚀 <b>Crypto Jet V2 çalışıyor!</b>\n\n"
                    "₿ <b>/btc</b>\n"
                    "Bitcoin 1 saatlik teknik analiz."
                )
            elif text == "/btc":
                try:
                    analysis = get_btc_analysis()
                    reply = format_btc_analysis(
                        analysis
                    )
                except Exception as e:
                    print(
                        "BTC analiz hatası:",
                        e
                    )
                    reply = (
                        "❌ <b>BTC analizi alınamadı.</b>\n\n"
                        f"Hata: {e}"
                    )
            else:
                reply = (
                    "📌 <b>Crypto Jet V2</b>\n\n"
                    "Kullanabileceğin komut:\n\n"
                    "₿ /btc — BTC 1 saatlik analiz"
                )
            send_message(
                chat_id,
                reply
            )
    except Exception as e:
        print(
            "Bot hatası:",
            e
        )
        time.sleep(5)
