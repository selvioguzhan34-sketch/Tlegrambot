import os
import time
import requests

# ============================================================
# CRYPTO JET V6
# ============================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE = "https://api.exchange.coinbase.com"

offset = 0

# ------------------------------------------------------------
# AYARLAR
# ------------------------------------------------------------

GRANULARITY = 3600          # 1 saat
AUTO_INTERVAL = 10 * 60     # 10 dakika

# İstenen coinler
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

# /start yapan kullanıcılar burada tutulur.
subscribers = set()

# Son otomatik rapor zamanı
last_auto_report = 0

print("🚀 CRYPTO JET V6 başladı!", flush=True)


# ============================================================
# GENEL HTTP
# ============================================================

def get_json(url, params=None, timeout=15):

    response = requests.get(
        url,
        params=params,
        timeout=timeout,
        headers={
            "User-Agent": "Crypto-Jet/6.0"
        }
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    current = sum(values[:period]) / period

    for price in values[period:]:
        current = (
            (price - current) * multiplier
        ) + current

    return current


# ============================================================
# RSI
# ============================================================

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
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ============================================================
# EMA SERIES
# ============================================================

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


# ============================================================
# MACD
# ============================================================

def macd(values):

    if len(values) < 50:
        return None, None, None

    ema12 = build_ema_series(values, 12)
    ema26 = build_ema_series(values, 26)

    macd_values = []

    start_index = len(values) - len(ema12)
    ema26_start = len(values) - len(ema26)

    for i, value in enumerate(ema12):

        absolute_index = start_index + i

        ema26_index = (
            absolute_index - ema26_start
        )

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


# ============================================================
# COIN ANALİZİ
# ============================================================

def get_coin_analysis(symbol, product_id):

    print(
        f"📊 {symbol} taranıyor...",
        flush=True
    )

    candles = get_json(
        f"{COINBASE}/products/{product_id}/candles",
        params={
            "granularity": GRANULARITY
        }
    )

    if not candles:

        raise ValueError(
            f"{symbol} verisi alınamadı."
        )

    candles = sorted(
        candles,
        key=lambda x: x[0]
    )

    closes = [
        float(candle[4])
        for candle in candles
    ]

    volumes = [
        float(candle[5])
        for candle in candles
    ]

    if len(closes) < 200:

        raise ValueError(
            f"{symbol} için yeterli mum yok."
        )

    # --------------------------------------------------------
    # GÖSTERGELER
    # --------------------------------------------------------

    price = closes[-1]

    previous_price = closes[-2]

    price_3_candles_ago = closes[-4]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    rsi_value = rsi(closes, 14)

    macd_line, macd_signal, macd_histogram = macd(
        closes
    )

    # --------------------------------------------------------
    # HACİM
    # --------------------------------------------------------

    previous_volumes = volumes[-21:-1]

    if previous_volumes:

        average_volume = (
            sum(previous_volumes)
            / len(previous_volumes)
        )

    else:

        average_volume = 0

    if average_volume > 0:

        volume_ratio = (
            volumes[-1]
            / average_volume
        )

    else:

        volume_ratio = 0

    # --------------------------------------------------------
    # SKOR
    # --------------------------------------------------------

    score = 0

    reasons = []

    # 1. Fiyat - EMA20
    if price > ema20:

        score += 1

        reasons.append(
            "Fiyat EMA20 üzerinde"
        )

    else:

        score -= 1

        reasons.append(
            "Fiyat EMA20 altında"
        )

    # 2. EMA20 - EMA50
    if ema20 > ema50:

        score += 2

        reasons.append(
            "EMA20 > EMA50"
        )

    else:

        score -= 2

        reasons.append(
            "EMA20 < EMA50"
        )

    # 3. EMA50 - EMA200
    if ema50 > ema200:

        score += 2

        reasons.append(
            "EMA50 > EMA200"
        )

    else:

        score -= 2

        reasons.append(
            "EMA50 < EMA200"
        )

    # 4. Fiyat - EMA200
    if price > ema200:

        score += 2

        reasons.append(
            "Uzun vadeli trend yukarı"
        )

    else:

        score -= 2

        reasons.append(
            "Uzun vadeli trend aşağı"
        )

    # 5. RSI
    if 55 <= rsi_value < 70:

        score += 2

        reasons.append(
            "RSI pozitif"
        )

    elif 30 < rsi_value <= 45:

        score -= 2

        reasons.append(
            "RSI negatif"
        )

    elif rsi_value >= 70:

        reasons.append(
            "RSI aşırı alım"
        )

    elif rsi_value <= 30:

        reasons.append(
            "RSI aşırı satım"
        )

    else:

        reasons.append(
            "RSI nötr"
        )

    # 6. MACD
    if macd_histogram is not None:

        if macd_histogram > 0:

            score += 2

            reasons.append(
                "MACD pozitif"
            )

        else:

            score -= 2

            reasons.append(
                "MACD negatif"
            )

    # 7. Momentum
    momentum = (
        (price - price_3_candles_ago)
        / price_3_candles_ago
    ) * 100

    if momentum > 0.20:

        score += 1

        reasons.append(
            f"Momentum +{momentum:.2f}%"
        )

    elif momentum < -0.20:

        score -= 1

        reasons.append(
            f"Momentum {momentum:.2f}%"
        )

    else:

        reasons.append(
            f"Momentum {momentum:.2f}%"
        )

    # 8. Hacim onayı
    if volume_ratio >= 1.20:

        if score > 0:

            score += 1

            reasons.append(
                f"Hacim güçlü {volume_ratio:.2f}x"
            )

        elif score < 0:

            score -= 1

            reasons.append(
                f"Hacim güçlü {volume_ratio:.2f}x"
            )

        else:

            reasons.append(
                f"Hacim güçlü {volume_ratio:.2f}x"
            )

    elif volume_ratio < 0.80:

        reasons.append(
            f"Hacim düşük {volume_ratio:.2f}x"
        )

    else:

        reasons.append(
            f"Hacim normal {volume_ratio:.2f}x"
        )

    # --------------------------------------------------------
    # SİNYAL
    # --------------------------------------------------------

    if score >= 6:

        signal = "🟢 LONG"

    elif score <= -6:

        signal = "🔴 SHORT"

    else:

        signal = "🟡 BEKLE"

    # --------------------------------------------------------
    # SİNYAL GÜCÜ
    # --------------------------------------------------------

    strength = 50 + (
        abs(score) * 4.5
    )

    strength = min(
        95,
        round(strength)
    )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if ema20 > ema50 > ema200:

        trend = "🟢 Güçlü yükseliş"

    elif ema20 < ema50 < ema200:

        trend = "🔴 Güçlü düşüş"

    elif price > ema200:

        trend = "🟢 Yükseliş"

    elif price < ema200:

        trend = "🔴 Düşüş"

    else:

        trend = "🟡 Kararsız"

    return {

        "symbol": symbol,

        "price": price,

        "previous_price": previous_price,

        "ema20": ema20,

        "ema50": ema50,

        "ema200": ema200,

        "rsi": rsi_value,

        "macd": macd_line,

        "volume_ratio": volume_ratio,

        "momentum": momentum,

        "signal": signal,

        "strength": strength,

        "score": score,

        "trend": trend,

        "reasons": reasons
    }


# ============================================================
# TÜM COİNLERİ TARA
# ============================================================

def get_all_analysis():

    results = []

    for symbol, product_id in COINS.items():

        try:

            data = get_coin_analysis(
                symbol,
                product_id
            )

            results.append(data)

        except Exception as e:

            print(
                f"⚠️ {symbol} analiz edilemedi: {e}",
                flush=True
            )

            results.append({
                "symbol": symbol,
                "error": str(e)
            })

    return results


# ============================================================
# TEK COİN FORMAT
# ============================================================

def format_coin(data):

    if "error" in data:

        return (
            f"❌ {data['symbol']}\n"
            f"Veri alınamadı."
        )

    macd_text = (
        f"{data['macd']:.4f}"
        if data["macd"] is not None
        else "N/A"
    )

    return (
        f"{data['symbol']}\n"
        f"💰 ${data['price']:,.4f}\n"
        f"📈 Trend: {data['trend']}\n"
        f"RSI: {data['rsi']:.2f}\n"
        f"MACD: {macd_text}\n"
        f"Hacim: {data['volume_ratio']:.2f}x\n"
        f"Momentum: {data['momentum']:+.2f}%\n"
        f"🎯 {data['signal']}\n"
        f"💪 Güç: %{data['strength']}\n"
        f"Skor: {data['score']}"
    )


# ============================================================
# TÜM RAPOR
# ============================================================

def format_all_analysis(results):

    lines = []

    lines.append(
        "🚀 CRYPTO JET V6"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "⏱ Zaman dilimi: 1 Saat"
    )

    lines.append(
        "🔄 Otomatik tarama: 10 dakika"
    )

    lines.append("")

    # Güçlü sinyalleri önce göster
    valid_results = [
        x for x in results
        if "error" not in x
    ]

    valid_results.sort(
        key=lambda x: abs(x["score"]),
        reverse=True
    )

    for data in valid_results:

        lines.append(
            f"💠 {data['symbol']}"
        )

        lines.append(
            f"💰 ${data['price']:,.4f}"
        )

        lines.append(
            f"🎯 {data['signal']} | "
            f"Güç %{data['strength']}"
        )

        lines.append(
            f"📊 Skor: {data['score']} | "
            f"RSI: {data['rsi']:.1f}"
        )

        lines.append(
            f"📈 {data['trend']}"
        )

        lines.append("")

    errors = [
        x for x in results
        if "error" in x
    ]

    if errors:

        lines.append(
            "⚠️ Veri alınamayanlar:"
        )

        for data in errors:

            lines.append(
                f"• {data['symbol']}"
            )

        lines.append("")

    # En güçlü sinyal
    strong_long = [
        x for x in valid_results
        if x["signal"] == "🟢 LONG"
    ]

    strong_short = [
        x for x in valid_results
        if x["signal"] == "🔴 SHORT"
    ]

    if strong_long:

        best_long = max(
            strong_long,
            key=lambda x: x["strength"]
        )

        lines.append(
            f"🚀 En güçlü LONG: "
            f"{best_long['symbol']} "
            f"(%{best_long['strength']})"
        )

    if strong_short:

        best_short = max(
            strong_short,
            key=lambda x: x["strength"]
        )

        lines.append(
            f"🔻 En güçlü SHORT: "
            f"{best_short['symbol']} "
            f"(%{best_short['strength']})"
        )

    lines.append("")

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "⚠️ Teknik analizdir. "
        "Kesin kazanç garantisi değildir."
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM MESAJ GÖNDER
# ============================================================

def send_message(chat_id, text):

    response = requests.post(
        f"{API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text
        },
        timeout=15
    )

    response.raise_for_status()


# ============================================================
# OTOMATİK RAPOR
# ============================================================

def send_auto_report():

    global last_auto_report

    if not subscribers:

        return

    print(
        "🤖 Otomatik tarama başlıyor...",
        flush=True
    )

    try:

        results = get_all_analysis()

        report = format_all_analysis(
            results
        )

        for chat_id in list(subscribers):

            try:

                send_message(
                    chat_id,
                    report
                )

                print(
                    f"✅ Otomatik rapor gönderildi: "
                    f"{chat_id}",
                    flush=True
                )

            except Exception as e:

                print(
                    f"⚠️ {chat_id} mesaj gönderilemedi: "
                    f"{e}",
                    flush=True
                )

        last_auto_report = time.time()

    except Exception as e:

        print(
            "❌ Otomatik tarama hatası:",
            repr(e),
            flush=True
        )


# ============================================================
# ESKİ TELEGRAM MESAJLARINI TEMİZLE
# ============================================================

try:

    print(
        "🧹 Eski Telegram mesajları temizleniyor...",
        flush=True
    )

    cleanup = requests.get(
        f"{API}/getUpdates",
        params={
            "offset": -1,
            "timeout": 1
        },
        timeout=5
    )

    cleanup_data = cleanup.json()

    if cleanup_data.get("result"):

        offset = (
            cleanup_data["result"][-1]["update_id"]
            + 1
        )

        print(
            f"✅ Eski mesajlar temizlendi. "
            f"Yeni offset: {offset}",
            flush=True
        )

    else:

        print(
            "✅ Temizlenecek eski mesaj yok.",
            flush=True
        )

except Exception as e:

    print(
        "⚠️ Temizleme hatası:",
        repr(e),
        flush=True
    )


# ============================================================
# ANA BOT DÖNGÜSÜ
# ============================================================

while True:

    try:

        # ----------------------------------------------------
        # TELEGRAM MESAJLARINI AL
        # ----------------------------------------------------

        response = requests.get(
            f"{API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 10
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        for update in data.get("result", []):

            offset = (
                update["update_id"] + 1
            )

            message = update.get("message")

            if not message:
                continue

            chat_id = message["chat"]["id"]

            text = message.get(
                "text",
                ""
            ).strip().lower()

            print(
                f"📩 Gelen mesaj: {text}",
                flush=True
            )

            # ------------------------------------------------
            # START
            # ------------------------------------------------

            if text == "/start":

                subscribers.add(chat_id)

                reply = (
                    "🚀 CRYPTO JET V6 AKTİF!\n\n"

                    "Sen otomatik taramaya eklendin. ✅\n\n"

                    "📊 10 coin taranıyor.\n"
                    "⏱ Zaman dilimi: 1 Saat\n"
                    "🔄 Otomatik rapor: 10 dakikada bir\n\n"

                    "Komutlar:\n"
                    "₿ /btc — BTC analizi\n"
                    "📊 /coinler — Tüm coinleri tara\n"
                    "🤖 /durum — Bot durumu\n"
                    "🛑 /stop — Otomatik mesajları durdur\n\n"

                    "İlk otomatik rapor şimdi hazırlanıyor..."
                )

                send_message(
                    chat_id,
                    reply
                )

                # İlk raporu hemen gönder
                try:

                    results = get_all_analysis()

                    report = format_all_analysis(
                        results
                    )

                    send_message(
                        chat_id,
                        report
                    )

                    last_auto_report = time.time()

                except Exception as e:

                    send_message(
                        chat_id,
                        f"❌ İlk tarama yapılamadı.\n\n"
                        f"Hata: {e}"
                    )

            # ------------------------------------------------
            # BTC
            # ------------------------------------------------

            elif text == "/btc":

                try:

                    data_btc = get_coin_analysis(
                        "BTC",
                        COINS["BTC"]
                    )

                    reply = (
                        "₿ CRYPTO JET — BTC\n"
                        "━━━━━━━━━━━━━━━━\n\n"
                        f"{format_coin(data_btc)}\n\n"
                        "━━━━━━━━━━━━━━━━\n"
                        "⚠️ Teknik analizdir. "
                        "Kesin kazanç garantisi değildir."
                    )

                except Exception as e:

                    reply = (
                        "❌ BTC analizi alınamadı.\n\n"
                        f"Hata: {e}"
                    )

                send_message(
                    chat_id,
                    reply
                )

            # ------------------------------------------------
            # COINLER
            # ------------------------------------------------

            elif text == "/coinler":

                try:

                    results = get_all_analysis()

                    reply = format_all_analysis(
                        results
                    )

                except Exception as e:

                    reply = (
                        "❌ Coin taraması yapılamadı.\n\n"
                        f"Hata: {e}"
                    )

                send_message(
                    chat_id,
                    reply
                )

            # ------------------------------------------------
            # DURUM
            # ------------------------------------------------

            elif text == "/durum":

                aktif = (
                    "🟢 AKTİF"
                    if chat_id in subscribers
                    else "🔴 PASİF"
                )

                reply = (
                    "🤖 CRYPTO JET V6\n"
                    "━━━━━━━━━━━━━━━━\n\n"

                    f"Durum: {aktif}\n"
                    f"Coin sayısı: {len(COINS)}\n"
                    "Zaman dilimi: 1 Saat\n"
                    "Otomatik tarama: 10 dakika\n\n"

                    "📌 Güçlü sinyaller:\n"
                    "🟢 LONG\n"
                    "🔴 SHORT\n"
                    "🟡 BEKLE"
                )

                send_message(
                    chat_id,
                    reply
                )

            # ------------------------------------------------
            # STOP
            # ------------------------------------------------

            elif text == "/stop":

                subscribers.discard(chat_id)

                reply = (
                    "🛑 Otomatik mesajlar durduruldu.\n\n"
                    "Tekrar başlatmak için:\n"
                    "/start"
                )

                send_message(
                    chat_id,
                    reply
                )

            # ------------------------------------------------
            # HELP
            # ------------------------------------------------

            elif text in ["/help", "/yardim"]:

                reply = (
                    "🚀 CRYPTO JET V6\n\n"

                    "/start\n"
                    "Otomatik taramayı başlatır.\n\n"

                    "/btc\n"
                    "BTC analizini gösterir.\n\n"

                    "/coinler\n"
                    "10 coinlik taramayı başlatır.\n\n"

                    "/durum\n"
                    "Bot durumunu gösterir.\n\n"

                    "/stop\n"
                    "Otomatik mesajları durdurur."
                )

                send_message(
                    chat_id,
                    reply
                )

            # ------------------------------------------------
            # BİLİNMEYEN KOMUT
            # ------------------------------------------------

            else:

                reply = (
                    "📌 CRYPTO JET V6\n\n"
                    "₿ /btc\n"
                    "📊 /coinler\n"
                    "🤖 /durum\n"
                    "🛑 /stop\n"
                    "❓ /help"
                )

                send_message(
                    chat_id,
                    reply
                )

        # ----------------------------------------------------
        # 10 DAKİKALIK OTOMATİK TARAMA
        # ----------------------------------------------------

        if subscribers:

            if (
                time.time() - last_auto_report
                >= AUTO_INTERVAL
            ):

                send_auto_report()

    except Exception as e:

        print(
            "❌ Bot hatası:",
            repr(e),
            flush=True
        )

        time.sleep(5)
