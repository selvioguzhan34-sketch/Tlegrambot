import os
import time
import requests
from datetime import datetime, timezone

# ============================================================
# CRYPTO JET V7
# 1 SAAT ANALİZ + 10 DAKİKA TARAMA
# BINANCE USDⓈ-M FUTURES
# PAPER TRADE - GERÇEK İŞLEM YOK
# ============================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

BINANCE = "https://fapi.binance.com"

INTERVAL = "1h"
AUTO_INTERVAL = 10 * 60

COINS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "BNB": "BNBUSDT",
    "ADA": "ADAUSDT",
    "DOGE": "DOGEUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "LTC": "LTCUSDT",
}

subscribers = set()
paper_trades = []

offset = 0
last_auto_report = 0
last_signal_direction = {}


# ============================================================
# GENEL
# ============================================================

def get_json(url, params=None):
    try:
        r = requests.get(
            url,
            params=params,
            timeout=15,
            headers={"User-Agent": "Crypto-Jet-V7"}
        )

        if r.status_code != 200:
            return None

        return r.json()

    except Exception:
        return None


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def fmt_price(price):
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:,.4f}"
    else:
        return f"${price:,.6f}"


def fmt_money(value):
    value = abs(value)

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:,.0f}"


# ============================================================
# EMA
# ============================================================

def ema(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    result = [sum(values[:period]) / period]

    for price in values[period:]:
        result.append(
            (price - result[-1]) * multiplier + result[-1]
        )

    return result


# ============================================================
# RSI
# ============================================================

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0

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


# ============================================================
# MACD
# ============================================================

def macd(values):
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)

    if not ema12 or not ema26:
        return 0, 0, 0

    # EMA dizilerini aynı son bölgeye getir
    min_len = min(len(ema12), len(ema26))

    ema12 = ema12[-min_len:]
    ema26 = ema26[-min_len:]

    macd_line = [
        a - b for a, b in zip(ema12, ema26)
    ]

    if len(macd_line) < 9:
        return 0, 0, 0

    signal_line = ema(macd_line, 9)

    if not signal_line:
        return 0, 0, 0

    macd_value = macd_line[-1]
    signal_value = signal_line[-1]

    histogram = macd_value - signal_value

    return macd_value, signal_value, histogram


# ============================================================
# ATR
# ============================================================

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0

    trs = []

    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )

        trs.append(tr)

    if len(trs) < period:
        return 0

    return sum(trs[-period:]) / period


# ============================================================
# ADX
# ============================================================

def adx(highs, lows, closes, period=14):
    if len(closes) < period * 2:
        return 20.0, 0.0, 0.0

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(closes)):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )

        trs.append(tr)

        plus_dm.append(
            high_diff if high_diff > low_diff and high_diff > 0 else 0
        )

        minus_dm.append(
            low_diff if low_diff > high_diff and low_diff > 0 else 0
        )

    if len(trs) < period:
        return 20.0, 0.0, 0.0

    tr_sum = sum(trs[-period:])
    plus_sum = sum(plus_dm[-period:])
    minus_sum = sum(minus_dm[-period:])

    if tr_sum == 0:
        return 20.0, 0.0, 0.0

    plus_di = 100 * plus_sum / tr_sum
    minus_di = 100 * minus_sum / tr_sum

    denominator = plus_di + minus_di

    if denominator == 0:
        return 20.0, plus_di, minus_di

    dx = 100 * abs(plus_di - minus_di) / denominator

    return dx, plus_di, minus_di


# ============================================================
# BINANCE KLINE
# ============================================================

def get_klines(symbol):
    data = get_json(
        f"{BINANCE}/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": 250
        }
    )

    if not data or not isinstance(data, list):
        return None

    try:
        opens = [float(x[1]) for x in data]
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        closes = [float(x[4]) for x in data]
        volumes = [float(x[5]) for x in data]

        return {
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": volumes
        }

    except Exception:
        return None


# ============================================================
# PRICE
# ============================================================

def get_price(symbol):
    data = get_json(
        f"{BINANCE}/fapi/v1/ticker/price",
        {"symbol": symbol}
    )

    if not data:
        return 0

    return safe_float(data.get("price"))


# ============================================================
# OPEN INTEREST
# ============================================================

def get_open_interest(symbol):
    data = get_json(
        f"{BINANCE}/fapi/v1/openInterest",
        {"symbol": symbol}
    )

    if not data:
        return 0

    return safe_float(data.get("openInterest"))


# ============================================================
# LONG / SHORT ORANI
# ============================================================

def get_long_short(symbol):
    data = get_json(
        f"{BINANCE}/futures/data/globalLongShortAccountRatio",
        {
            "symbol": symbol,
            "period": "1h",
            "limit": 1
        }
    )

    if not data or not isinstance(data, list):
        return 50.0, 50.0

    row = data[-1]

    ratio = safe_float(row.get("longShortRatio"), 1)

    if ratio <= 0:
        return 50.0, 50.0

    long_pct = (ratio / (1 + ratio)) * 100
    short_pct = 100 - long_pct

    return long_pct, short_pct


# ============================================================
# TOP TRADER POSITION ORANI
# ============================================================

def get_top_position_ratio(symbol):
    data = get_json(
        f"{BINANCE}/futures/data/topLongShortPositionRatio",
        {
            "symbol": symbol,
            "period": "1h",
            "limit": 1
        }
    )

    if not data or not isinstance(data, list):
        return 50.0, 50.0

    row = data[-1]

    ratio = safe_float(row.get("longShortRatio"), 1)

    if ratio <= 0:
        return 50.0, 50.0

    long_pct = (ratio / (1 + ratio)) * 100
    short_pct = 100 - long_pct

    return long_pct, short_pct


# ============================================================
# ANALİZ
# ============================================================

def analyze_coin(name, symbol):
    candles = get_klines(symbol)

    if not candles:
        return None

    closes = candles["closes"]
    highs = candles["highs"]
    lows = candles["lows"]
    volumes = candles["volumes"]

    if len(closes) < 200:
        return None

    price = closes[-1]

    ema20_values = ema(closes, 20)
    ema50_values = ema(closes, 50)
    ema200_values = ema(closes, 200)

    ema20 = ema20_values[-1]
    ema50 = ema50_values[-1]
    ema200 = ema200_values[-1]

    rsi_value = rsi(closes)

    macd_value, macd_signal, macd_hist = macd(closes)

    atr_value = atr(highs, lows, closes)

    adx_value, plus_di, minus_di = adx(
        highs,
        lows,
        closes
    )

    recent_volume = volumes[-1]
    avg_volume = sum(volumes[-20:]) / 20

    volume_ratio = (
        recent_volume / avg_volume
        if avg_volume > 0
        else 1
    )

    momentum = (
        ((price - closes[-5]) / closes[-5]) * 100
        if closes[-5] != 0
        else 0
    )

    # --------------------------------------------------------
    # DESTEK / DİRENÇ
    # --------------------------------------------------------

    resistance = max(highs[-20:])
    support = min(lows[-20:])

    long_pct, short_pct = get_long_short(symbol)
    top_long_pct, top_short_pct = get_top_position_ratio(symbol)

    open_interest = get_open_interest(symbol)

    # Yaklaşık pozisyon değerleri.
    # Bunlar gerçek toplam long/short işlem hacmi değildir.
    oi_notional = open_interest * price

    estimated_long_value = oi_notional * long_pct / 100
    estimated_short_value = oi_notional * short_pct / 100

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    # EMA TREND
    if ema20 > ema50 > ema200:
        score += 2
        ema_signal = "🟢"
    elif ema20 < ema50 < ema200:
        score -= 2
        ema_signal = "🔴"
    else:
        ema_signal = "⚪"

    # PRICE / EMA200
    if price > ema200:
        score += 1
    elif price < ema200:
        score -= 1

    # RSI
    if 55 <= rsi_value <= 68:
        score += 1
        rsi_signal = "🟢"
    elif 32 <= rsi_value <= 45:
        score -= 1
        rsi_signal = "🔴"
    else:
        rsi_signal = "⚪"

    # MACD
    if macd_hist > 0:
        score += 1
        macd_signal_icon = "🟢"
    elif macd_hist < 0:
        score -= 1
        macd_signal_icon = "🔴"
    else:
        macd_signal_icon = "⚪"

    # ADX
    if adx_value >= 25:
        if plus_di > minus_di:
            score += 2
            adx_signal = "🟢"
        elif minus_di > plus_di:
            score -= 2
            adx_signal = "🔴"
        else:
            adx_signal = "⚪"
    else:
        adx_signal = "⚪"

    # HACİM
    if volume_ratio >= 1.20:
        if momentum > 0:
            score += 1
            volume_signal = "🟢"
        elif momentum < 0:
            score -= 1
            volume_signal = "🔴"
        else:
            volume_signal = "⚪"
    else:
        volume_signal = "⚪"

    # MOMENTUM
    if momentum > 0.50:
        score += 1
    elif momentum < -0.50:
        score -= 1

    # LONG / SHORT
    if long_pct >= 57:
        score += 1
    elif short_pct >= 57:
        score -= 1

    # TOP TRADER POZİSYONLARI
    if top_long_pct >= 60:
        score += 1
    elif top_short_pct >= 60:
        score -= 1

    # --------------------------------------------------------
    # DESTEK / DİRENÇ FİLTRESİ
    # --------------------------------------------------------

    near_resistance = (
        resistance > 0 and
        ((resistance - price) / price) < 0.008
    )

    near_support = (
        support > 0 and
        ((price - support) / price) < 0.008
    )

    # Direncin hemen altında LONG'u biraz zorlaştır.
    if near_resistance and score > 0:
        score -= 1

    # Desteğin hemen üstünde SHORT'u biraz zorlaştır.
    if near_support and score < 0:
        score += 1

    # --------------------------------------------------------
    # SİNYAL
    # --------------------------------------------------------

    if score >= 7:
        direction = "LONG"
        signal = "🟢 GÜÇLÜ AL"
    elif score <= -7:
        direction = "SHORT"
        signal = "🔴 GÜÇLÜ SAT"
    else:
        direction = "WAIT"
        signal = "⏸️ BEKLE"

    strength = min(
        95,
        50 + abs(score) * 5
    )

    # --------------------------------------------------------
    # PAPER TRADE SEVİYELERİ
    # --------------------------------------------------------

    if atr_value <= 0:
        atr_value = price * 0.01

    if direction == "LONG":
        stop = price - (atr_value * 1.2)
        target = price + (atr_value * 2.0)

    elif direction == "SHORT":
        stop = price + (atr_value * 1.2)
        target = price - (atr_value * 2.0)

    else:
        stop = 0
        target = 0

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
        "macd_hist": macd_hist,

        "adx": adx_value,
        "plus_di": plus_di,
        "minus_di": minus_di,

        "atr": atr_value,

        "volume_ratio": volume_ratio,
        "momentum": momentum,

        "support": support,
        "resistance": resistance,

        "long_pct": long_pct,
        "short_pct": short_pct,

        "top_long_pct": top_long_pct,
        "top_short_pct": top_short_pct,

        "open_interest": open_interest,

        "estimated_long_value": estimated_long_value,
        "estimated_short_value": estimated_short_value,

        "score": score,
        "direction": direction,
        "signal": signal,
        "strength": strength,

        "stop": stop,
        "target": target,

        "ema_signal": ema_signal,
        "rsi_signal": rsi_signal,
        "macd_signal_icon": macd_signal_icon,
        "adx_signal": adx_signal,
        "volume_signal": volume_signal,
    }


# ============================================================
# TÜM COİNLER
# ============================================================

def analyze_all():
    results = []

    for name, symbol in COINS.items():
        try:
            result = analyze_coin(name, symbol)

            if result:
                results.append(result)

        except Exception as e:
            print(f"{name} analiz hatası: {e}")

    return results


# ============================================================
# PAPER TRADE
# ============================================================

def has_open_trade(symbol):
    for trade in paper_trades:
        if (
            trade["symbol"] == symbol and
            trade["status"] == "OPEN"
        ):
            return True

    return False


def open_paper_trade(result):
    if result["direction"] not in ("LONG", "SHORT"):
        return

    if has_open_trade(result["symbol"]):
        return

    trade = {
        "symbol": result["symbol"],
        "name": result["name"],
        "direction": result["direction"],
        "entry": result["price"],
        "stop": result["stop"],
        "target": result["target"],
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "OPEN",
        "exit": None,
        "pnl_pct": 0
    }

    paper_trades.append(trade)

    print(
        f"PAPER OPEN: "
        f"{result['name']} "
        f"{result['direction']} "
        f"{fmt_price(result['price'])}"
    )


def update_paper_trades(results):
    price_map = {
        r["symbol"]: r["price"]
        for r in results
    }

    for trade in paper_trades:

        if trade["status"] != "OPEN":
            continue

        symbol = trade["symbol"]

        if symbol not in price_map:
            continue

        price = price_map[symbol]

        if trade["direction"] == "LONG":

            if price <= trade["stop"]:
                trade["status"] = "LOSS"
                trade["exit"] = price

                trade["pnl_pct"] = (
                    (price - trade["entry"])
                    / trade["entry"]
                ) * 100

            elif price >= trade["target"]:
                trade["status"] = "WIN"
                trade["exit"] = price

                trade["pnl_pct"] = (
                    (price - trade["entry"])
                    / trade["entry"]
                ) * 100

        elif trade["direction"] == "SHORT":

            if price >= trade["stop"]:
                trade["status"] = "LOSS"
                trade["exit"] = price

                trade["pnl_pct"] = (
                    (trade["entry"] - price)
                    / trade["entry"]
                ) * 100

            elif price <= trade["target"]:
                trade["status"] = "WIN"
                trade["exit"] = price

                trade["pnl_pct"] = (
                    (trade["entry"] - price)
                    / trade["entry"]
                ) * 100


# ============================================================
# PAPER PERFORMANS
# ============================================================

def performance_text():
    closed = [
        x for x in paper_trades
        if x["status"] in ("WIN", "LOSS")
    ]

    wins = sum(
        1 for x in closed
        if x["status"] == "WIN"
    )

    losses = sum(
        1 for x in closed
        if x["status"] == "LOSS"
    )

    total_pnl = sum(
        x["pnl_pct"]
        for x in closed
    )

    if closed:
        win_rate = (wins / len(closed)) * 100
    else:
        win_rate = 0

    open_count = sum(
        1 for x in paper_trades
        if x["status"] == "OPEN"
    )

    text = (
        "📊 CRYPTO JET V7\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "🧪 PAPER TEST PERFORMANSI\n\n"
        f"📌 Toplam kapanan: {len(closed)}\n"
        f"🟢 Kazanç: {wins}\n"
        f"🔴 Kayıp: {losses}\n"
        f"📈 Başarı: %{win_rate:.1f}\n"
        f"💰 Toplam paper P/L: %{total_pnl:.2f}\n"
        f"🔵 Açık işlemler: {open_count}\n"
    )

    if open_count:
        text += "\n📂 AÇIK PAPER İŞLEMLER\n"

        for trade in paper_trades:
            if trade["status"] == "OPEN":
                text += (
                    f"\n{trade['name']} "
                    f"{'🟢 LONG' if trade['direction'] == 'LONG' else '🔴 SHORT'}\n"
                    f"Giriş: {fmt_price(trade['entry'])}\n"
                    f"SL: {fmt_price(trade['stop'])}\n"
                    f"TP: {fmt_price(trade['target'])}\n"
                )

    return text


# ============================================================
# COIN MESAJI
# ============================================================

def format_coin(r):
    return (
        f"{r['name']}\n"
        f"💰 Fiyat: {fmt_price(r['price'])}\n"
        f"🟢 Long pozisyon: %{r['long_pct']:.1f}\n"
        f"🔴 Short pozisyon: %{r['short_pct']:.1f}\n"
        f"⚖️ L/S: %{r['long_pct']:.1f} / %{r['short_pct']:.1f}\n"
        f"💵 Tahmini LONG değeri: {fmt_money(r['estimated_long_value'])}\n"
        f"💵 Tahmini SHORT değeri: {fmt_money(r['estimated_short_value'])}\n"
        f"📊 EMA {r['ema_signal']} | RSI {r['rsi_signal']} | "
        f"MACD {r['macd_signal_icon']} | ADX {r['adx_signal']} | "
        f"Hacim {r['volume_signal']}\n"
        f"📈 RSI: {r['rsi']:.1f} | ADX: {r['adx']:.1f}\n"
        f"⚡ Momentum: {r['momentum']:+.2f}%\n"
        f"🎯 {r['signal']}\n"
        f"💪 Güç: %{r['strength']:.0f}\n"
        f"🧠 Skor: {r['score']:+d}"
    )


# ============================================================
# GENEL RAPOR
# ============================================================

def format_report(results):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    text = (
        "🚀 CRYPTO JET V7\n"
        "━━━━━━━━━━━━━━━━\n"
        "⏱ Zaman dilimi: 1 Saat\n"
        f"🕐 Tarama: {now}\n"
        "🔄 Otomatik tarama: 10 dk\n"
        "🧪 Paper Trade: AKTİF\n"
        "━━━━━━━━━━━━━━━━\n\n"
    )

    for result in results:
        text += format_coin(result)
        text += "\n━━━━━━━━━━━━━━━━\n"

    return text


# ============================================================
# GÜÇLÜ SİNYAL KONTROLÜ
# ============================================================

def get_new_strong_signals(results):
    alerts = []

    for r in results:

        if r["direction"] not in ("LONG", "SHORT"):
            continue

        old_direction = last_signal_direction.get(
            r["symbol"]
        )

        if old_direction != r["direction"]:

            alerts.append(r)

            last_signal_direction[
                r["symbol"]
            ] = r["direction"]

            open_paper_trade(r)

    return alerts


# ============================================================
# TELEGRAM
# ============================================================

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
        print("Telegram gönderim hatası:", e)


def send_auto_report(results):
    text = format_report(results)

    for chat_id in list(subscribers):
        send_message(chat_id, text)

    alerts = get_new_strong_signals(results)

    for r in alerts:

        alert = (
            "🚨 YENİ GÜÇLÜ SİNYAL\n"
            "━━━━━━━━━━━━━━━━\n"
            f"🪙 {r['name']}\n"
            f"🎯 {r['signal']}\n"
            f"💪 Sinyal gücü: %{r['strength']:.0f}\n"
            f"💰 Giriş: {fmt_price(r['price'])}\n"
            f"🛑 Paper SL: {fmt_price(r['stop'])}\n"
            f"🎯 Paper TP: {fmt_price(r['target'])}\n"
            f"📊 RSI: {r['rsi']:.1f}\n"
            f"📊 ADX: {r['adx']:.1f}\n"
            f"⚖️ Long/Short: "
            f"%{r['long_pct']:.1f} / %{r['short_pct']:.1f}\n"
            "\n"
            "🧪 Bu gerçek işlem değildir.\n"
            "Paper test olarak takip ediliyor."
        )

        for chat_id in list(subscribers):
            send_message(chat_id, alert)


# ============================================================
# TELEGRAM KOMUTLARI
# ============================================================

def handle_command(chat_id, text):

    global subscribers

    command = text.strip().lower()

    if command == "/start":

        subscribers.add(chat_id)

        send_message(
            chat_id,
            "🚀 CRYPTO JET V7 AKTİF!\n\n"
            "⏱ 1 saatlik analiz\n"
            "🔄 10 dakikada bir otomatik tarama\n"
            "🧠 Güçlü sinyal sistemi\n"
            "🧪 Paper Trade aktif\n\n"
            "İlk tarama başlatılıyor..."
        )

        results = analyze_all()

        if results:
            update_paper_trades(results)
            send_auto_report(results)
        else:
            send_message(
                chat_id,
                "⚠️ Şu anda piyasa verileri alınamadı."
            )

    elif command == "/stop":

        subscribers.discard(chat_id)

        send_message(
            chat_id,
            "🛑 Otomatik bildirim durduruldu."
        )

    elif command == "/coinler":

        results = analyze_all()

        if not results:
            send_message(
                chat_id,
                "⚠️ Veri alınamadı."
            )
            return

        text = "🪙 CRYPTO JET V7\n━━━━━━━━━━━━━━━━\n\n"

        for r in results:
            text += (
                f"{r['name']}: "
                f"{r['signal']} "
                f"(%{r['strength']:.0f})\n"
            )

        send_message(chat_id, text)

    elif command == "/btc":

        result = analyze_coin(
            "BTC",
            "BTCUSDT"
        )

        if result:
            send_message(
                chat_id,
                "🚀 CRYPTO JET V7\n"
                "━━━━━━━━━━━━━━━━\n" +
                format_coin(result)
            )
        else:
            send_message(
                chat_id,
                "⚠️ BTC verisi alınamadı."
            )

    elif command == "/durum":

        status = (
            "🟢 CRYPTO JET V7 ÇALIŞIYOR\n\n"
            f"👥 Abone: {len(subscribers)}\n"
            f"🧪 Paper işlem: {len(paper_trades)}\n"
            "⏱ Analiz: 1 Saat\n"
            "🔄 Tarama: 10 Dakika"
        )

        send_message(chat_id, status)

    elif command == "/performans":

        send_message(
            chat_id,
            performance_text()
        )

    elif command == "/help":

        send_message(
            chat_id,
            "🚀 CRYPTO JET V7 KOMUTLARI\n\n"
            "/start - Otomatik sistemi başlat\n"
            "/stop - Otomatik bildirimleri durdur\n"
            "/btc - BTC analizi\n"
            "/coinler - 10 coin özeti\n"
            "/durum - Bot durumu\n"
            "/performans - Paper test sonucu\n"
            "/help - Yardım"
        )


# ============================================================
# TELEGRAM UPDATES
# ============================================================

def get_updates():

    global offset

    data = get_json(
        f"{API}/getUpdates",
        {
            "offset": offset,
            "timeout": 10
        }
    )

    if not data:
        return []

    return data.get("result", [])


# ============================================================
# BAŞLANGIÇ
# ============================================================

print("🚀 CRYPTO JET V7 BAŞLADI!")

while True:

    try:

        # ----------------------------------------------------
        # TELEGRAM
        # ----------------------------------------------------

        updates = get_updates()

        for update in updates:

            offset = update["update_id"] + 1

            message = update.get("message")

            if not message:
                continue

            chat_id = message["chat"]["id"]

            text = message.get("text", "")

            if text.startswith("/"):
                handle_command(
                    chat_id,
                    text
                )

        # ----------------------------------------------------
        # OTOMATİK TARAMA
        # ----------------------------------------------------

        now = time.time()

        if (
            subscribers and
            now - last_auto_report >= AUTO_INTERVAL
        ):

            print("🔎 10 dakikalık otomatik tarama...")

            results = analyze_all()

            if results:

                # Önce açık paper işlemleri kontrol et
                update_paper_trades(results)

                # Sonra rapor + yeni sinyaller
                send_auto_report(results)

                print(
                    "✅ Otomatik rapor gönderildi."
                )

            else:

                print(
                    "⚠️ Otomatik taramada veri alınamadı."
                )

            last_auto_report = now

        time.sleep(1)

    except KeyboardInterrupt:

        print("Bot kapatıldı.")
        break

    except Exception as e:

        print("Ana döngü hatası:", e)
        time.sleep(5)
