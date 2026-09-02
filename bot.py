import os
import time
import requests
import math
from datetime import datetime

# =========================================================
# CRYPTO JET V7.2
# Coinbase teknik analiz + Binance Long/Short opsiyonel
# Paper Trading
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"

TIMEFRAME = "1h"
AUTO_INTERVAL = 10 * 60

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

subscribers = set()
paper_trades = []
last_signals = {}


# =========================================================
# GENEL HTTP
# =========================================================

def get_json(url, params=None, timeout=15):
    try:
        r = requests.get(
            url,
            params=params,
            timeout=timeout,
            headers={
                "User-Agent": "Crypto-Jet/7.2"
            }
        )

        r.raise_for_status()
        return r.json()

    except Exception as e:
        print(f"API HATASI: {url}")
        print(e)
        return None


# =========================================================
# TEKNİK İNDİKATÖRLER
# =========================================================

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


def macd(values):
    if len(values) < 35:
        return None, None

    ema12_values = []
    ema26_values = []

    for i in range(26, len(values) + 1):
        e12 = ema(values[:i], 12)
        e26 = ema(values[:i], 26)

        if e12 is not None and e26 is not None:
            ema12_values.append(e12)
            ema26_values.append(e26)

    if not ema12_values:
        return None, None

    macd_values = [
        a - b for a, b in zip(ema12_values, ema26_values)
    ]

    if len(macd_values) < 9:
        return macd_values[-1], None

    signal = ema(macd_values, 9)

    return macd_values[-1], signal


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None

    trs = []

    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )

        trs.append(tr)

    return sum(trs[-period:]) / period


def adx(highs, lows, closes, period=14):
    if len(closes) < period * 2:
        return None

    tr_values = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(closes)):

        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        if high_diff > low_diff and high_diff > 0:
            plus = high_diff
        else:
            plus = 0

        if low_diff > high_diff and low_diff > 0:
            minus = low_diff
        else:
            minus = 0

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )

        tr_values.append(tr)
        plus_dm.append(plus)
        minus_dm.append(minus)

    if len(tr_values) < period:
        return None

    atr_value = sum(tr_values[-period:]) / period

    if atr_value == 0:
        return 0

    plus_di = (
        sum(plus_dm[-period:]) / period
    ) / atr_value * 100

    minus_di = (
        sum(minus_dm[-period:]) / period
    ) / atr_value * 100

    denominator = plus_di + minus_di

    if denominator == 0:
        return 0

    dx = abs(plus_di - minus_di) / denominator * 100

    return dx


# =========================================================
# COINBASE KLINE
# =========================================================

def get_klines(symbol):

    url = f"{COINBASE_API}/products/{symbol}/candles"

    data = get_json(
        url,
        params={
            "granularity": 3600
        }
    )

    if not data or not isinstance(data, list):
        return None

    # Coinbase:
    # [time, low, high, open, close, volume]

    data = sorted(data, key=lambda x: x[0])

    data = data[-200:]

    candles = []

    for row in data:
        if len(row) < 6:
            continue

        candles.append({
            "low": float(row[1]),
            "high": float(row[2]),
            "open": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5])
        })

    return candles


# =========================================================
# BINANCE LONG / SHORT
# =========================================================

def get_long_short(symbol):

    try:

        url = (
            "https://fapi.binance.com/"
            "futures/data/globalLongShortAccountRatio"
        )

        data = get_json(
            url,
            params={
                "symbol": symbol,
                "period": "1h",
                "limit": 1
            }
        )

        if not data:
            return None

        item = data[-1]

        long_ratio = float(item["longAccount"])
        short_ratio = float(item["shortAccount"])

        total = long_ratio + short_ratio

        if total <= 0:
            return None

        long_percent = long_ratio / total * 100
        short_percent = short_ratio / total * 100

        return {
            "long": long_percent,
            "short": short_percent
        }

    except Exception as e:

        print(f"Long/Short alınamadı: {symbol}")
        print(e)

        return None


# =========================================================
# ANALİZ
# =========================================================

def analyze_coin(name, symbol):

    candles = get_klines(symbol)

    if not candles or len(candles) < 60:
        print(f"{name}: Yeterli veri yok.")
        return None

    closes = [x["close"] for x in candles]
    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]
    volumes = [x["volume"] for x in candles]

    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    rsi_value = rsi(closes)

    macd_value, macd_signal = macd(closes)

    atr_value = atr(
        highs,
        lows,
        closes
    )

    adx_value = adx(
        highs,
        lows,
        closes
    )

    average_volume = sum(volumes[-20:]) / 20

    current_volume = volumes[-1]

    volume_ratio = (
        current_volume / average_volume
        if average_volume > 0
        else 1
    )

    momentum = (
        (price - closes[-10]) /
        closes[-10] *
        100
    )

    score = 0

    # EMA
    if ema20 and ema50 and ema200:

        if price > ema20:
            score += 1
        else:
            score -= 1

        if ema20 > ema50:
            score += 1
        else:
            score -= 1

        if price > ema200:
            score += 1
        else:
            score -= 1

    # RSI
    if rsi_value is not None:

        if 52 <= rsi_value <= 70:
            score += 1

        elif 30 <= rsi_value < 45:
            score -= 1

        elif rsi_value > 75:
            score -= 1

        elif rsi_value < 25:
            score += 1

    # MACD
    if macd_value is not None and macd_signal is not None:

        if macd_value > macd_signal:
            score += 1
        else:
            score -= 1

    # ADX
    if adx_value is not None:

        if adx_value >= 25:

            if ema20 and ema50:

                if ema20 > ema50:
                    score += 2
                else:
                    score -= 2

    # Momentum
    if momentum > 1:
        score += 1

    elif momentum < -1:
        score -= 1

    # Volume
    if volume_ratio >= 1.3:

        if momentum > 0:
            score += 1

        elif momentum < 0:
            score -= 1

    # Long / Short
    futures_symbol = symbol.replace("-", "")

    long_short = get_long_short(futures_symbol)

    if long_short:

        if long_short["long"] >= 58:
            score += 1

        elif long_short["short"] >= 58:
            score -= 1

    # Direction
    if score >= 7:
        signal = "🟢 GÜÇLÜ AL"
        direction = "LONG"

    elif score <= -7:
        signal = "🔴 GÜÇLÜ SAT"
        direction = "SHORT"

    else:
        signal = "⏸️ BEKLE"
        direction = "WAIT"

    strength = min(
        95,
        50 + abs(score) * 5
    )

    return {
        "name": name,
        "symbol": symbol,
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi_value,
        "macd": macd_value,
        "macd_signal": macd_signal,
        "adx": adx_value,
        "atr": atr_value,
        "volume_ratio": volume_ratio,
        "momentum": momentum,
        "long_short": long_short,
        "score": score,
        "signal": signal,
        "direction": direction,
        "strength": strength
    }


# =========================================================
# TÜM COİNLER
# =========================================================

def analyze_all():

    results = []

    for name, symbol in COINS.items():

        try:

            result = analyze_coin(
                name,
                symbol
            )

            if result:
                results.append(result)

        except Exception as e:

            print(f"{name} ANALİZ HATASI:")
            print(e)

    return results


# =========================================================
# PAPER TRADING
# =========================================================

def open_trade(result):

    if result["direction"] not in ["LONG", "SHORT"]:
        return

    symbol = result["symbol"]

    for trade in paper_trades:

        if (
            trade["symbol"] == symbol
            and trade["status"] == "OPEN"
        ):
            return

    price = result["price"]
    atr_value = result["atr"]

    if not atr_value or atr_value <= 0:
        return

    if result["direction"] == "LONG":

        stop_loss = price - atr_value * 1.2
        take_profit = price + atr_value * 2

    else:

        stop_loss = price + atr_value * 1.2
        take_profit = price - atr_value * 2

    trade = {
        "symbol": symbol,
        "name": result["name"],
        "direction": result["direction"],
        "entry": price,
        "stop": stop_loss,
        "target": take_profit,
        "status": "OPEN",
        "opened": datetime.utcnow().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "closed": None,
        "exit": None,
        "pnl": 0
    }

    paper_trades.append(trade)

    print(
        f"PAPER TRADE AÇILDI: "
        f"{result['name']} {result['direction']} "
        f"{price}"
    )


def update_trades(results):

    for trade in paper_trades:

        if trade["status"] != "OPEN":
            continue

        result = next(
            (
                r for r in results
                if r["symbol"] == trade["symbol"]
            ),
            None
        )

        if not result:
            continue

        price = result["price"]

        closed = False

        if trade["direction"] == "LONG":

            if price <= trade["stop"]:
                trade["exit"] = price
                trade["pnl"] = (
                    price - trade["entry"]
                ) / trade["entry"] * 100

                closed = True

            elif price >= trade["target"]:
                trade["exit"] = price
                trade["pnl"] = (
                    price - trade["entry"]
                ) / trade["entry"] * 100

                closed = True

        else:

            if price >= trade["stop"]:
                trade["exit"] = price
                trade["pnl"] = (
                    trade["entry"] - price
                ) / trade["entry"] * 100

                closed = True

            elif price <= trade["target"]:
                trade["exit"] = price
                trade["pnl"] = (
                    trade["entry"] - price
                ) / trade["entry"] * 100

                closed = True

        if closed:

            trade["status"] = "CLOSED"

            trade["closed"] = datetime.utcnow().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            print(
                f"PAPER TRADE KAPANDI: "
                f"{trade['name']} "
                f"{trade['pnl']:.2f}%"
            )


# =========================================================
# TELEGRAM MESAJI
# =========================================================

def send_message(chat_id, text):

    try:

        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=15
        )

    except Exception as e:

        print("Telegram mesaj hatası:")
        print(e)


def format_coin(result):

    ls = result["long_short"]

    if ls:

        long_text = f"{ls['long']:.0f}%"
        short_text = f"{ls['short']:.0f}%"

    else:

        long_text = "N/A"
        short_text = "N/A"

    ema_status = "🟢" if (
        result["ema20"]
        and result["ema50"]
        and result["price"] > result["ema20"]
        and result["ema20"] > result["ema50"]
    ) else "🔴"

    rsi_status = "🟢" if (
        result["rsi"] is not None
        and 50 <= result["rsi"] <= 70
    ) else "🔴"

    macd_status = "🟢" if (
        result["macd"] is not None
        and result["macd_signal"] is not None
        and result["macd"] > result["macd_signal"]
    ) else "🔴"

    adx_status = "🟢" if (
        result["adx"] is not None
        and result["adx"] >= 25
    ) else "🟡"

    volume_status = "🟢" if (
        result["volume_ratio"] >= 1.2
    ) else "🟡"

    return (
        f"━━━━━━━━━━━━━━━━\n"
        f"🪙 {result['name']}\n\n"
        f"💰 Fiyat: ${result['price']:,.2f}\n\n"
        f"⚖️ Long / Short: "
        f"{long_text} / {short_text}\n\n"
        f"📊 EMA: {ema_status}\n"
        f"📊 RSI: {rsi_status}\n"
        f"📊 MACD: {macd_status}\n"
        f"📊 ADX: {adx_status}\n"
        f"📊 Hacim: {volume_status}\n"
        f"📈 Momentum: {result['momentum']:+.2f}%\n\n"
        f"🎯 SİNYAL: {result['signal']}\n"
        f"💪 Güç: %{result['strength']}\n"
    )


def create_report(results):

    if not results:
        return "❌ Veri alınamadı."

    text = (
        "🚀 CRYPTO JET V7.2\n"
        "━━━━━━━━━━━━━━━━\n"
        "⏱ Zaman dilimi: 1 Saat\n"
        "🔄 Tarama: 10 dakika\n"
        "🧪 Paper Trading\n\n"
    )

    for result in results:
        text += format_coin(result)
        text += "\n"

    return text


# =========================================================
# PERFORMANS
# =========================================================

def performance():

    closed = [
        x for x in paper_trades
        if x["status"] == "CLOSED"
    ]

    open_count = len([
        x for x in paper_trades
        if x["status"] == "OPEN"
    ])

    wins = len([
        x for x in closed
        if x["pnl"] > 0
    ])

    losses = len([
        x for x in closed
        if x["pnl"] <= 0
    ])

    total_pnl = sum(
        x["pnl"] for x in closed
    )

    if closed:
        win_rate = wins / len(closed) * 100
    else:
        win_rate = 0

    return (
        "📊 CRYPTO JET PERFORMANS\n"
        "━━━━━━━━━━━━━━━━\n\n"
        f"📁 Kapanan işlem: {len(closed)}\n"
        f"🟢 Kazanç: {wins}\n"
        f"🔴 Zarar: {losses}\n"
        f"🎯 Başarı oranı: %{win_rate:.1f}\n"
        f"💰 Toplam P/L: {total_pnl:+.2f}%\n"
        f"🔵 Açık işlem: {open_count}\n"
    )


# =========================================================
# YENİ GÜÇLÜ SİNYAL KONTROLÜ
# =========================================================

def check_new_signals(results):

    for result in results:

        symbol = result["symbol"]

        old_signal = last_signals.get(symbol)

        new_signal = result["direction"]

        last_signals[symbol] = new_signal

        if new_signal in ["LONG", "SHORT"]:

            if old_signal != new_signal:

                open_trade(result)


# =========================================================
# RAPOR
# =========================================================

def send_report():

    results = analyze_all()

    if not results:

        for chat_id in subscribers:
            send_message(
                chat_id,
                "❌ Veri alınamadı.\n\n"
                "Coinbase piyasa verisi alınamadı."
            )

        return

    update_trades(results)

    check_new_signals(results)

    report = create_report(results)

    for chat_id in list(subscribers):

        send_message(
            chat_id,
            report
        )


# =========================================================
# TELEGRAM KOMUTLARI
# =========================================================

def handle_command(chat_id, text):

    text = text.strip().lower()

    if text == "/start":

        subscribers.add(chat_id)

        send_message(
            chat_id,
            "🚀 CRYPTO JET V7.2 AKTİF!\n\n"
            "10 coin taranıyor.\n"
            "⏱ 1 saatlik analiz\n"
            "🔄 10 dakikada bir güncelleme\n"
            "🧪 Paper Trading aktif\n\n"
            "İlk rapor hazırlanıyor..."
        )

        send_report()

    elif text == "/btc":

        result = analyze_coin(
            "BTC",
            "BTC-USD"
        )

        if result:
            send_message(
                chat_id,
                "🚀 CRYPTO JET BTC\n\n"
                + format_coin(result)
            )
        else:
            send_message(
                chat_id,
                "❌ BTC verisi alınamadı."
            )

    elif text == "/coinler":

        text_out = (
            "🪙 TARANAN COİNLER\n"
            "━━━━━━━━━━━━━━━━\n\n"
        )

        for coin in COINS:
            text_out += f"• {coin}\n"

        send_message(
            chat_id,
            text_out
        )

    elif text == "/durum":

        send_message(
            chat_id,
            "🚀 Crypto Jet V7.2 aktif.\n\n"
            f"👥 Abone: {len(subscribers)}\n"
            f"🧪 Paper işlem: {len(paper_trades)}\n"
            "🔄 Tarama: 10 dakika"
        )

    elif text == "/performans":

        send_message(
            chat_id,
            performance()
        )

    elif text == "/stop":

        subscribers.discard(chat_id)

        send_message(
            chat_id,
            "🛑 Otomatik bildirim durduruldu."
        )

    elif text == "/help":

        send_message(
            chat_id,
            "🤖 CRYPTO JET V7.2\n\n"
            "/start - Botu başlat\n"
            "/btc - BTC analizi\n"
            "/coinler - Coin listesi\n"
            "/durum - Bot durumu\n"
            "/performans - Paper trading sonucu\n"
            "/stop - Bildirimleri durdur\n"
            "/help - Yardım"
        )


# =========================================================
# TELEGRAM UPDATES
# =========================================================

def get_updates(offset):

    try:

        r = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 30
            },
            timeout=35
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:

        print("Telegram getUpdates hatası:")
        print(e)

        return None


# =========================================================
# ANA DÖNGÜ
# =========================================================

print("🚀 CRYPTO JET V7.2 BAŞLADI")

offset = 0
last_auto_scan = 0

while True:

    try:

        data = get_updates(offset)

        if data and data.get("ok"):

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

        # =================================================
        # OTOMATİK 10 DAKİKALIK TARAMA
        # =================================================

        current_time = time.time()

        if (
            subscribers
            and current_time - last_auto_scan >= AUTO_INTERVAL
        ):

            print("🔄 Otomatik piyasa taraması...")

            send_report()

            last_auto_scan = current_time

        time.sleep(1)

    except Exception as e:

        print("ANA DÖNGÜ HATASI:")
        print(e)

        time.sleep(5)
