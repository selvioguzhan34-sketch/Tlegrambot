import os
import time
import requests
from datetime import datetime, timezone

# ============================================================
# 🚀 CRYPTO JET V7.1
# 1 SAAT ANALİZ
# 10 DAKİKADA BİR TARAMA
# PAPER TRADE
# GERÇEK İŞLEM YOK
# ============================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
BINANCE_API = "https://fapi.binance.com"

TIMEFRAME = "1h"
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
last_signal = {}


# ============================================================
# HTTP
# ============================================================

def get_json(url, params=None):
    try:
        response = requests.get(
            url,
            params=params,
            timeout=15,
            headers={
                "User-Agent": "CryptoJet/7.1"
            }
        )

        if response.status_code != 200:
            print(
                f"API HATA {response.status_code}: "
                f"{url} | {response.text[:300]}"
            )
            return None

        return response.json()

    except Exception as e:
        print(f"HTTP HATA: {url} -> {e}")
        return None


def f(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


def price_text(price):
    if price >= 1000:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:,.4f}"
    return f"${price:,.6f}"


def money_text(value):
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

def EMA(values, period):

    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    result = [
        sum(values[:period]) / period
    ]

    for value in values[period:]:
        result.append(
            (value - result[-1]) * multiplier
            + result[-1]
        )

    return result


# ============================================================
# RSI
# ============================================================

def RSI(values, period=14):

    if len(values) < period + 1:
        return 50

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ============================================================
# MACD
# ============================================================

def MACD(values):

    ema12 = EMA(values, 12)
    ema26 = EMA(values, 26)

    if not ema12 or not ema26:
        return 0, 0, 0

    length = min(
        len(ema12),
        len(ema26)
    )

    ema12 = ema12[-length:]
    ema26 = ema26[-length:]

    macd_line = []

    for a, b in zip(ema12, ema26):
        macd_line.append(a - b)

    if len(macd_line) < 9:
        return 0, 0, 0

    signal_line = EMA(
        macd_line,
        9
    )

    if not signal_line:
        return 0, 0, 0

    macd_value = macd_line[-1]
    signal_value = signal_line[-1]

    histogram = (
        macd_value - signal_value
    )

    return (
        macd_value,
        signal_value,
        histogram
    )


# ============================================================
# ATR
# ============================================================

def ATR(highs, lows, closes, period=14):

    if len(closes) < period + 1:
        return 0

    true_ranges = []

    for i in range(1, len(closes)):

        tr = max(
            highs[i] - lows[i],
            abs(
                highs[i]
                - closes[i - 1]
            ),
            abs(
                lows[i]
                - closes[i - 1]
            )
        )

        true_ranges.append(tr)

    return sum(
        true_ranges[-period:]
    ) / period


# ============================================================
# ADX
# ============================================================

def ADX(highs, lows, closes, period=14):

    if len(closes) < period * 2:
        return 20, 0, 0

    tr_list = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(closes)):

        high_move = (
            highs[i] - highs[i - 1]
        )

        low_move = (
            lows[i - 1] - lows[i]
        )

        tr = max(
            highs[i] - lows[i],
            abs(
                highs[i]
                - closes[i - 1]
            ),
            abs(
                lows[i]
                - closes[i - 1]
            )
        )

        tr_list.append(tr)

        plus_dm.append(
            high_move
            if (
                high_move > low_move
                and high_move > 0
            )
            else 0
        )

        minus_dm.append(
            low_move
            if (
                low_move > high_move
                and low_move > 0
            )
            else 0
        )

    tr_sum = sum(
        tr_list[-period:]
    )

    if tr_sum == 0:
        return 20, 0, 0

    plus_di = (
        100
        * sum(plus_dm[-period:])
        / tr_sum
    )

    minus_di = (
        100
        * sum(minus_dm[-period:])
        / tr_sum
    )

    total = plus_di + minus_di

    if total == 0:
        return 20, plus_di, minus_di

    dx = (
        100
        * abs(plus_di - minus_di)
        / total
    )

    return dx, plus_di, minus_di


# ============================================================
# BINANCE 1H KLINE
# ============================================================

def get_klines(symbol):

    data = get_json(
        f"{BINANCE_API}/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": TIMEFRAME,
            "limit": 250
        }
    )

    if not data or not isinstance(data, list):
        return None

    try:

        return {
            "open": [
                f(x[1])
                for x in data
            ],

            "high": [
                f(x[2])
                for x in data
            ],

            "low": [
                f(x[3])
                for x in data
            ],

            "close": [
                f(x[4])
                for x in data
            ],

            "volume": [
                f(x[5])
                for x in data
            ],
        }

    except Exception as e:

        print(
            f"KLINE PARSE HATASI "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# LONG / SHORT
# ============================================================

def get_long_short(symbol):

    data = get_json(
        f"{BINANCE_API}/futures/data/globalLongShortAccountRatio",
        {
            "symbol": symbol,
            "period": "1h",
            "limit": 1
        }
    )

    if not data or not isinstance(data, list):

        print(
            f"{symbol}: Long/Short verisi yok"
        )

        return None, None

    row = data[-1]

    ratio = f(
        row.get(
            "longShortRatio"
        ),
        0
    )

    if ratio <= 0:
        return None, None

    long_pct = (
        ratio
        / (1 + ratio)
        * 100
    )

    short_pct = (
        100 - long_pct
    )

    return (
        long_pct,
        short_pct
    )


# ============================================================
# OPEN INTEREST
# ============================================================

def get_open_interest(symbol):

    data = get_json(
        f"{BINANCE_API}/fapi/v1/openInterest",
        {
            "symbol": symbol
        }
    )

    if not data:
        return 0

    return f(
        data.get(
            "openInterest"
        )
    )


# ============================================================
# COIN ANALİZİ
# ============================================================

def analyze_coin(name, symbol):

    candles = get_klines(symbol)

    if not candles:

        print(
            f"{name}: Kline alınamadı"
        )

        return None

    closes = candles["close"]
    highs = candles["high"]
    lows = candles["low"]
    volumes = candles["volume"]

    if len(closes) < 200:

        print(
            f"{name}: Yeterli mum yok"
        )

        return None

    current_price = closes[-1]

    # --------------------------------------------------------
    # İNDİKATÖRLER
    # --------------------------------------------------------

    ema20 = EMA(
        closes,
        20
    )[-1]

    ema50 = EMA(
        closes,
        50
    )[-1]

    ema200 = EMA(
        closes,
        200
    )[-1]

    rsi = RSI(
        closes,
        14
    )

    macd_value, macd_signal, macd_hist = MACD(
        closes
    )

    atr = ATR(
        highs,
        lows,
        closes
    )

    adx, plus_di, minus_di = ADX(
        highs,
        lows,
        closes
    )

    # --------------------------------------------------------
    # HACİM
    # --------------------------------------------------------

    avg_volume = (
        sum(volumes[-20:])
        / 20
    )

    current_volume = volumes[-1]

    if avg_volume > 0:
        volume_ratio = (
            current_volume
            / avg_volume
        )
    else:
        volume_ratio = 1

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum = (
        (
            current_price
            - closes[-5]
        )
        / closes[-5]
        * 100
    )

    # --------------------------------------------------------
    # DESTEK / DİRENÇ
    # --------------------------------------------------------

    support = min(
        lows[-20:]
    )

    resistance = max(
        highs[-20:]
    )

    # --------------------------------------------------------
    # FUTURES
    # --------------------------------------------------------

    long_pct, short_pct = get_long_short(
        symbol
    )

    open_interest = get_open_interest(
        symbol
    )

    if (
        long_pct is not None
        and short_pct is not None
    ):

        oi_value = (
            open_interest
            * current_price
        )

        estimated_long = (
            oi_value
            * long_pct
            / 100
        )

        estimated_short = (
            oi_value
            * short_pct
            / 100
        )

        long_short_text = True

    else:

        estimated_long = 0
        estimated_short = 0
        long_short_text = False

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    # EMA TREND
    if ema20 > ema50 > ema200:

        score += 2
        ema_icon = "🟢"

    elif ema20 < ema50 < ema200:

        score -= 2
        ema_icon = "🔴"

    else:

        ema_icon = "⚪"

    # EMA200
    if current_price > ema200:
        score += 1

    elif current_price < ema200:
        score -= 1

    # RSI
    if 55 <= rsi <= 68:

        score += 1
        rsi_icon = "🟢"

    elif 32 <= rsi <= 45:

        score -= 1
        rsi_icon = "🔴"

    else:

        rsi_icon = "⚪"

    # MACD
    if macd_hist > 0:

        score += 1
        macd_icon = "🟢"

    elif macd_hist < 0:

        score -= 1
        macd_icon = "🔴"

    else:

        macd_icon = "⚪"

    # ADX
    if adx >= 25:

        if plus_di > minus_di:

            score += 2
            adx_icon = "🟢"

        elif minus_di > plus_di:

            score -= 2
            adx_icon = "🔴"

        else:

            adx_icon = "⚪"

    else:

        adx_icon = "⚪"

    # HACİM
    if volume_ratio >= 1.20:

        if momentum > 0:

            score += 1
            volume_icon = "🟢"

        elif momentum < 0:

            score -= 1
            volume_icon = "🔴"

        else:

            volume_icon = "⚪"

    else:

        volume_icon = "⚪"

    # MOMENTUM
    if momentum > 0.50:

        score += 1

    elif momentum < -0.50:

        score -= 1

    # LONG / SHORT
    if long_pct is not None:

        if long_pct >= 57:

            score += 1

        elif short_pct >= 57:

            score -= 1

    # --------------------------------------------------------
    # DESTEK / DİRENÇ FİLTRESİ
    # --------------------------------------------------------

    near_resistance = (
        (
            resistance
            - current_price
        )
        / current_price
        < 0.008
    )

    near_support = (
        (
            current_price
            - support
        )
        / current_price
        < 0.008
    )

    if near_resistance and score > 0:
        score -= 1

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
    # PAPER TRADE
    # --------------------------------------------------------

    if atr <= 0:
        atr = current_price * 0.01

    if direction == "LONG":

        stop = (
            current_price
            - atr * 1.2
        )

        target = (
            current_price
            + atr * 2
        )

    elif direction == "SHORT":

        stop = (
            current_price
            + atr * 1.2
        )

        target = (
            current_price
            - atr * 2
        )

    else:

        stop = 0
        target = 0

    return {

        "name": name,
        "symbol": symbol,

        "price": current_price,

        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,

        "rsi": rsi,

        "macd_hist": macd_hist,

        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,

        "atr": atr,

        "volume_ratio": volume_ratio,

        "momentum": momentum,

        "support": support,
        "resistance": resistance,

        "long_pct": long_pct,
        "short_pct": short_pct,

        "open_interest": open_interest,

        "estimated_long": estimated_long,
        "estimated_short": estimated_short,

        "long_short_available": long_short_text,

        "score": score,
        "direction": direction,
        "signal": signal,
        "strength": strength,

        "stop": stop,
        "target": target,

        "ema_icon": ema_icon,
        "rsi_icon": rsi_icon,
        "macd_icon": macd_icon,
        "adx_icon": adx_icon,
        "volume_icon": volume_icon,
    }


# ============================================================
# TÜM COİNLER
# ============================================================

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

            print(
                f"{name} ANALİZ HATASI: {e}"
            )

    print(
        f"Toplam başarılı analiz: "
        f"{len(results)}/{len(COINS)}"
    )

    return results


# ============================================================
# PAPER TRADE
# ============================================================

def open_trade(result):

    if result["direction"] not in (
        "LONG",
        "SHORT"
    ):
        return

    for trade in paper_trades:

        if (
            trade["symbol"]
            == result["symbol"]
            and trade["status"]
            == "OPEN"
        ):
            return

    paper_trades.append({

        "symbol": result["symbol"],
        "name": result["name"],

        "direction":
            result["direction"],

        "entry":
            result["price"],

        "stop":
            result["stop"],

        "target":
            result["target"],

        "status": "OPEN",

        "opened":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "exit": 0,
        "pnl": 0
    })


def update_trades(results):

    prices = {
        r["symbol"]: r["price"]
        for r in results
    }

    for trade in paper_trades:

        if trade["status"] != "OPEN":
            continue

        symbol = trade["symbol"]

        if symbol not in prices:
            continue

        price = prices[symbol]

        # LONG
        if trade["direction"] == "LONG":

            if price <= trade["stop"]:

                trade["status"] = "LOSS"
                trade["exit"] = price

                trade["pnl"] = (
                    (
                        price
                        - trade["entry"]
                    )
                    / trade["entry"]
                    * 100
                )

            elif price >= trade["target"]:

                trade["status"] = "WIN"
                trade["exit"] = price

                trade["pnl"] = (
                    (
                        price
                        - trade["entry"]
                    )
                    / trade["entry"]
                    * 100
                )

        # SHORT
        elif trade["direction"] == "SHORT":

            if price >= trade["stop"]:

                trade["status"] = "LOSS"
                trade["exit"] = price

                trade["pnl"] = (
                    (
                        trade["entry"]
                        - price
                    )
                    / trade["entry"]
                    * 100
                )

            elif price <= trade["target"]:

                trade["status"] = "WIN"
                trade["exit"] = price

                trade["pnl"] = (
                    (
                        trade["entry"]
                        - price
                    )
                    / trade["entry"]
                    * 100
                )


# ============================================================
# COIN FORMAT
# ============================================================

def format_coin(r):

    if r["long_pct"] is not None:

        long_text = (
            f"%{r['long_pct']:.1f}"
        )

        short_text = (
            f"%{r['short_pct']:.1f}"
        )

        long_value = money_text(
            r["estimated_long"]
        )

        short_value = money_text(
            r["estimated_short"]
        )

    else:

        long_text = "N/A"
        short_text = "N/A"
        long_value = "N/A"
        short_value = "N/A"

    return (
        f"🪙 {r['name']}\n"
        f"💰 Fiyat: "
        f"{price_text(r['price'])}\n"
        f"🟢 Long: {long_text}\n"
        f"🔴 Short: {short_text}\n"
        f"⚖️ L/S: "
        f"{long_text} / {short_text}\n"
        f"💵 Tahmini Long: {long_value}\n"
        f"💵 Tahmini Short: {short_value}\n"
        f"📊 EMA {r['ema_icon']} | "
        f"RSI {r['rsi_icon']} | "
        f"MACD {r['macd_icon']} | "
        f"ADX {r['adx_icon']} | "
        f"Hacim {r['volume_icon']}\n"
        f"📈 RSI: {r['rsi']:.1f} | "
        f"ADX: {r['adx']:.1f}\n"
        f"⚡ Momentum: "
        f"{r['momentum']:+.2f}%\n"
        f"🎯 {r['signal']}\n"
        f"💪 Güç: "
        f"%{r['strength']:.0f}\n"
        f"🧠 Skor: "
        f"{r['score']:+d}"
    )


# ============================================================
# RAPOR
# ============================================================

def create_report(results):

    now = datetime.now().strftime(
        "%d.%m.%Y %H:%M"
    )

    text = (
        "🚀 CRYPTO JET V7.1\n"
        "━━━━━━━━━━━━━━━━\n"
        "⏱ Zaman dilimi: 1 Saat\n"
        "🔄 Tarama: 10 Dakika\n"
        f"🕐 {now}\n"
        "🧪 Paper Trade: AKTİF\n"
        "━━━━━━━━━━━━━━━━\n\n"
    )

    for result in results:

        text += (
            format_coin(result)
            + "\n"
            + "━━━━━━━━━━━━━━━━\n"
        )

    return text


# ============================================================
# TELEGRAM
# ============================================================

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

        print(
            f"Telegram HATASI: {e}"
        )


# ============================================================
# PERFORMANS
# ============================================================

def performance():

    closed = [
        t
        for t in paper_trades
        if t["status"]
        in ("WIN", "LOSS")
    ]

    wins = sum(
        1
        for t in closed
        if t["status"] == "WIN"
    )

    losses = sum(
        1
        for t in closed
        if t["status"] == "LOSS"
    )

    total_pnl = sum(
        t["pnl"]
        for t in closed
    )

    if closed:

        win_rate = (
            wins
            / len(closed)
            * 100
        )

    else:

        win_rate = 0

    open_count = sum(
        1
        for t in paper_trades
        if t["status"] == "OPEN"
    )

    return (
        "📊 CRYPTO JET V7.1\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "🧪 PAPER TEST\n\n"
        f"📌 Kapanan: {len(closed)}\n"
        f"🟢 Kazanç: {wins}\n"
        f"🔴 Kayıp: {losses}\n"
        f"📈 Başarı: %{win_rate:.1f}\n"
        f"💰 Paper P/L: %{total_pnl:.2f}\n"
        f"🔵 Açık: {open_count}"
    )


# ============================================================
# GÜÇLÜ SİNYAL
# ============================================================

def check_new_signals(results):

    alerts = []

    for r in results:

        direction = r["direction"]

        if direction not in (
            "LONG",
            "SHORT"
        ):
            continue

        old = last_signal.get(
            r["symbol"]
        )

        if old != direction:

            last_signal[
                r["symbol"]
            ] = direction

            alerts.append(r)

            open_trade(r)

    return alerts


# ============================================================
# OTOMATİK RAPOR
# ============================================================

def send_report(results):

    report = create_report(
        results
    )

    for chat_id in list(
        subscribers
    ):

        send_message(
            chat_id,
            report
        )

    alerts = check_new_signals(
        results
    )

    for r in alerts:

        alert = (
            "🚨 YENİ GÜÇLÜ SİNYAL\n"
            "━━━━━━━━━━━━━━━━\n"
            f"🪙 {r['name']}\n"
            f"🎯 {r['signal']}\n"
            f"💪 Güç: "
            f"%{r['strength']:.0f}\n"
            f"💰 Giriş: "
            f"{price_text(r['price'])}\n"
            f"🛑 Paper SL: "
            f"{price_text(r['stop'])}\n"
            f"🎯 Paper TP: "
            f"{price_text(r['target'])}\n"
            f"📊 RSI: "
            f"{r['rsi']:.1f}\n"
            f"📊 ADX: "
            f"{r['adx']:.1f}\n"
        )

        if r["long_pct"] is not None:

            alert += (
                f"⚖️ Long/Short: "
                f"%{r['long_pct']:.1f} / "
                f"%{r['short_pct']:.1f}\n"
            )

        alert += (
            "\n🧪 Gerçek işlem yok.\n"
            "Paper Trade olarak takip ediliyor."
        )

        for chat_id in list(
            subscribers
        ):

            send_message(
                chat_id,
                alert
            )


# ============================================================
# KOMUTLAR
# ============================================================

def handle_command(
    chat_id,
    text
):

    command = (
        text.strip()
        .lower()
        .split()[0]
    )

    # START
    if command == "/start":

        subscribers.add(
            chat_id
        )

        send_message(
            chat_id,
            "🚀 CRYPTO JET V7.1 AKTİF!\n\n"
            "⏱ 1 saatlik analiz\n"
            "🔄 10 dakikada otomatik tarama\n"
            "🧠 Güçlü sinyal sistemi\n"
            "🧪 Paper Trade aktif\n\n"
            "İlk piyasa taraması yapılıyor..."
        )

        results = analyze_all()

        if results:

            update_trades(
                results
            )

            send_report(
                results
            )

        else:

            send_message(
                chat_id,
                "❌ Veri alınamadı.\n\n"
                "GitHub Actions logunda "
                "hangi Binance API isteğinin "
                "hata verdiğini görebilirsin."
            )

    # STOP
    elif command == "/stop":

        subscribers.discard(
            chat_id
        )

        send_message(
            chat_id,
            "🛑 Otomatik bildirimler durduruldu."
        )

    # BTC
    elif command == "/btc":

        result = analyze_coin(
            "BTC",
            "BTCUSDT"
        )

        if result:

            send_message(
                chat_id,
                "🚀 CRYPTO JET V7.1\n"
                "━━━━━━━━━━━━━━━━\n"
                + format_coin(result)
            )

        else:

            send_message(
                chat_id,
                "❌ BTC verisi alınamadı."
            )

    # COINLER
    elif command == "/coinler":

        results = analyze_all()

        if not results:

            send_message(
                chat_id,
                "❌ Coin verileri alınamadı."
            )

            return

        text = (
            "🪙 CRYPTO JET V7.1\n"
            "━━━━━━━━━━━━━━━━\n\n"
        )

        for r in results:

            text += (
                f"{r['name']}: "
                f"{r['signal']} "
                f"%{r['strength']:.0f}\n"
            )

        send_message(
            chat_id,
            text
        )

    # DURUM
    elif command == "/durum":

        send_message(
            chat_id,
            "🟢 CRYPTO JET V7.1 ÇALIŞIYOR\n\n"
            f"👥 Abone: "
            f"{len(subscribers)}\n"
            f"🧪 Paper işlemler: "
            f"{len(paper_trades)}\n"
            "⏱ Analiz: 1 Saat\n"
            "🔄 Tarama: 10 Dakika"
        )

    # PERFORMANS
    elif command == "/performans":

        send_message(
            chat_id,
            performance()
        )

    # HELP
    elif command == "/help":

        send_message(
            chat_id,
            "🚀 CRYPTO JET V7.1\n\n"
            "/start - Başlat\n"
            "/stop - Bildirimleri durdur\n"
            "/btc - BTC analizi\n"
            "/coinler - Coin özeti\n"
            "/durum - Bot durumu\n"
            "/performans - Paper sonuçları\n"
            "/help - Yardım"
        )


# ============================================================
# TELEGRAM UPDATES
# ============================================================

def get_updates():

    global offset

    data = get_json(
        f"{TELEGRAM_API}/getUpdates",
        {
            "offset": offset,
            "timeout": 10
        }
    )

    if not data:

        return []

    return data.get(
        "result",
        []
    )


# ============================================================
# 🚀 ANA MOTOR
# ============================================================

print(
    "🚀 CRYPTO JET V7.1 BAŞLADI!"
)

while True:

    try:

        # Telegram mesajları
        updates = get_updates()

        for update in updates:

            offset = (
                update["update_id"]
                + 1
            )

            message = update.get(
                "message"
            )

            if not message:
                continue

            chat_id = message["chat"]["id"]

            text = message.get(
                "text",
                ""
            )

            if text.startswith("/"):

                handle_command(
                    chat_id,
                    text
                )

        # Otomatik tarama
        now = time.time()

        if (
            subscribers
            and
            now - last_auto_report
            >= AUTO_INTERVAL
        ):

            print(
                "🔎 10 dakikalık tarama başladı..."
            )

            results = analyze_all()

            if results:

                update_trades(
                    results
                )

                send_report(
                    results
                )

                print(
                    "✅ Rapor gönderildi."
                )

            else:

                print(
                    "❌ Hiçbir coin analiz edilemedi."
                )

            last_auto_report = now

        time.sleep(1)

    except KeyboardInterrupt:

        print(
            "🛑 Bot kapatıldı."
        )

        break

    except Exception as e:

        print(
            f"ANA MOTOR HATASI: {e}"
        )

        time.sleep(5)
