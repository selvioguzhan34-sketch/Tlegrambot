import os
import time
import requests
from datetime import datetime

# =========================================================
# CRYPTO JET V7.5
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"
BINANCE_FAPI = "https://fapi.binance.com"

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

# Coinbase coin adı -> Binance Futures sembolü
BINANCE_SYMBOLS = {
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
last_alerts = {}


# =========================================================
# HTTP
# =========================================================

def get_json(url, params=None, timeout=15):

    try:

        response = requests.get(
            url,
            params=params,
            timeout=timeout
        )

        if response.status_code != 200:

            print(
                "❌ HTTP HATA:",
                response.status_code,
                response.text[:500]
            )

            return None

        return response.json()

    except Exception as e:

        print(
            "❌ HTTP BAĞLANTI HATASI:",
            e
        )

        return None


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, data=None):

    try:

        response = requests.post(
            f"{TELEGRAM_API}/{method}",
            data=data or {},
            timeout=15
        )

        return response.json()

    except Exception as e:

        print(
            "❌ TELEGRAM HATASI:",
            e
        )

        return None


def send_message(chat_id, text):

    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:

        result = (
            (price - result)
            * multiplier
        ) + result

    return result


# =========================================================
# RSI
# =========================================================

def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i]
            - values[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(abs(change))

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# =========================================================
# MACD
# =========================================================

def macd(values):

    if len(values) < 35:
        return None, None

    ema12 = ema(values, 12)
    ema26 = ema(values, 26)

    if (
        ema12 is None
        or ema26 is None
    ):
        return None, None

    macd_line = (
        ema12 - ema26
    )

    return macd_line, None


# =========================================================
# ATR
# =========================================================

def atr(
    highs,
    lows,
    closes,
    period=14
):

    if len(closes) < period + 1:
        return None

    trs = []

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

        trs.append(tr)

    if len(trs) < period:
        return None

    return (
        sum(trs[-period:])
        / period
    )


# =========================================================
# ADX
# =========================================================

def adx(
    highs,
    lows,
    closes,
    period=14
):

    if len(closes) < period * 2:
        return None

    plus_dm = []
    minus_dm = []
    trs = []

    for i in range(1, len(closes)):

        up_move = (
            highs[i]
            - highs[i - 1]
        )

        down_move = (
            lows[i - 1]
            - lows[i]
        )

        plus = 0
        minus = 0

        if (
            up_move > down_move
            and up_move > 0
        ):
            plus = up_move

        if (
            down_move > up_move
            and down_move > 0
        ):
            minus = down_move

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

        plus_dm.append(plus)
        minus_dm.append(minus)
        trs.append(tr)

    if len(trs) < period:
        return None

    tr_sum = sum(
        trs[-period:]
    )

    plus_sum = sum(
        plus_dm[-period:]
    )

    minus_sum = sum(
        minus_dm[-period:]
    )

    if tr_sum == 0:
        return None

    plus_di = (
        100
        * plus_sum
        / tr_sum
    )

    minus_di = (
        100
        * minus_sum
        / tr_sum
    )

    denominator = (
        plus_di + minus_di
    )

    if denominator == 0:
        return None

    dx = (
        abs(
            plus_di - minus_di
        )
        / denominator
    ) * 100

    return dx


# =========================================================
# COINBASE CANDLES
# =========================================================

def get_klines(symbol):

    url = (
        f"{COINBASE_API}"
        f"/products/{symbol}/candles"
    )

    data = get_json(
        url,
        params={
            "granularity": 3600
        }
    )

    if not data:
        return None

    data = sorted(
        data,
        key=lambda x: x[0]
    )

    highs = [
        float(x[2])
        for x in data
    ]

    lows = [
        float(x[1])
        for x in data
    ]

    closes = [
        float(x[4])
        for x in data
    ]

    volumes = [
        float(x[5])
        for x in data
    ]

    return {
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes
    }


# =========================================================
# BINANCE LONG / SHORT
# =========================================================

def get_long_short(coin):

    symbol = BINANCE_SYMBOLS.get(
        coin
    )

    if not symbol:

        print(
            "❌ Binance sembol yok:",
            coin
        )

        return None

    url = (
        f"{BINANCE_FAPI}"
        "/futures/data/"
        "globalLongShortAccountRatio"
    )

    params = {
        "symbol": symbol,
        "period": "1h",
        "limit": 1
    }

    print(
        f"🔎 LONG/SHORT İSTEK: "
        f"{coin} -> {symbol}"
    )

    data = get_json(
        url,
        params=params
    )

    # KRİTİK DEBUG
    print(
        f"🔎 LONG/SHORT CEVAP "
        f"{coin}:",
        data
    )

    if not data:

        print(
            f"❌ LONG/SHORT VERİ YOK: "
            f"{coin}"
        )

        return None

    if not isinstance(
        data,
        list
    ):

        print(
            f"❌ LONG/SHORT BEKLENMEYEN "
            f"VERİ: {coin}"
        )

        return None

    try:

        row = data[-1]

        print(
            f"📊 {coin} RAW:",
            row
        )

        long_account = float(
            row["longAccount"]
        )

        short_account = float(
            row["shortAccount"]
        )

        ratio = float(
            row["longShortRatio"]
        )

        total = (
            long_account
            + short_account
        )

        if total <= 0:
            return None

        long_percent = (
            long_account
            / total
        ) * 100

        short_percent = (
            short_account
            / total
        ) * 100

        print(
            f"✅ {coin} "
            f"LONG=%{long_percent:.2f} "
            f"SHORT=%{short_percent:.2f} "
            f"RATIO={ratio:.4f}"
        )

        return {
            "long": long_percent,
            "short": short_percent,
            "ratio": ratio
        }

    except Exception as e:

        print(
            f"❌ LONG/SHORT PARSE "
            f"HATASI {coin}:",
            e
        )

        return None


# =========================================================
# SİNYAL
# =========================================================

def calculate_signal(
    closes,
    ema20,
    ema50,
    rsi_value,
    macd_value,
    adx_value,
    volume_now,
    volume_avg
):

    long_score = 0
    short_score = 0

    price = closes[-1]

    # EMA
    if (
        ema20 is not None
        and ema50 is not None
    ):

        if price > ema20:
            long_score += 15

        elif price < ema20:
            short_score += 15

        if ema20 > ema50:
            long_score += 15

        elif ema20 < ema50:
            short_score += 15

    # RSI
    if rsi_value is not None:

        if rsi_value > 55:
            long_score += 15

        elif rsi_value < 45:
            short_score += 15

    # MACD
    if macd_value is not None:

        if macd_value > 0:
            long_score += 15

        elif macd_value < 0:
            short_score += 15

    # ADX
    if adx_value is not None:

        if adx_value >= 25:

            if long_score > short_score:
                long_score += 10

            elif short_score > long_score:
                short_score += 10

    # Volume
    if (
        volume_avg
        and volume_now
    ):

        if volume_now > volume_avg:

            if long_score > short_score:
                long_score += 10

            elif short_score > long_score:
                short_score += 10

    strength = max(
        long_score,
        short_score
    )

    strength = min(
        strength,
        100
    )

    if long_score > short_score:
        direction = "LONG"

    elif short_score > long_score:
        direction = "SHORT"

    else:
        direction = "NEUTRAL"

    return direction, strength


# =========================================================
# COIN ANALİZİ
# =========================================================

def analyze_coin(
    name,
    symbol
):

    candles = get_klines(
        symbol
    )

    if not candles:
        return None

    highs = candles["highs"]
    lows = candles["lows"]
    closes = candles["closes"]
    volumes = candles["volumes"]

    if len(closes) < 60:
        return None

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
        closes,
        14
    )

    macd_value, _ = macd(
        closes
    )

    atr_value = atr(
        highs,
        lows,
        closes,
        14
    )

    adx_value = adx(
        highs,
        lows,
        closes,
        14
    )

    volume_now = volumes[-1]

    volume_avg = (
        sum(volumes[-20:])
        / len(volumes[-20:])
    )

    direction, strength = (
        calculate_signal(
            closes,
            ema20,
            ema50,
            rsi_value,
            macd_value,
            adx_value,
            volume_now,
            volume_avg
        )
    )

    # Binance Long/Short
    long_short = get_long_short(
        name
    )

    if long_short:

        long_percent = (
            long_short["long"]
        )

        short_percent = (
            long_short["short"]
        )

        ls_ratio = (
            long_short["ratio"]
        )

    else:

        long_percent = None
        short_percent = None
        ls_ratio = None

    return {

        "name": name,

        "symbol": symbol,

        "price": price,

        "ema20": ema20,

        "ema50": ema50,

        "rsi": rsi_value,

        "macd": macd_value,

        "atr": atr_value,

        "adx": adx_value,

        "volume": volume_now,

        "volume_avg": volume_avg,

        "long": long_percent,

        "short": short_percent,

        "ls_ratio": ls_ratio,

        "direction": direction,

        "strength": strength
    }


# =========================================================
# NORMAL RAPOR
# =========================================================

def format_coin(result):

    if not result:
        return "❌ Analiz alınamadı."

    long_value = result["long"]
    short_value = result["short"]
    ratio = result["ls_ratio"]

    if long_value is None:

        long_text = "N/A"
        short_text = "N/A"
        ratio_text = "N/A"

    else:

        long_text = (
            f"%{long_value:.1f}"
        )

        short_text = (
            f"%{short_value:.1f}"
        )

        ratio_text = (
            f"{ratio:.2f}"
        )

    direction = result[
        "direction"
    ]

    if direction == "LONG":
        signal_icon = "🟢"

    elif direction == "SHORT":
        signal_icon = "🔴"

    else:
        signal_icon = "⚪"

    return (
        "🚀 CRYPTO JET V7.5\n"
        "━━━━━━━━━━━━━━━━\n\n"

        f"₿ {result['name']}\n"
        "⏱ Zaman dilimi: 1 Saat\n\n"

        f"💰 Fiyat: "
        f"${result['price']:,.2f}\n\n"

        "📊 TEKNİK ANALİZ\n"
        f"EMA20: {result['ema20']:.2f}\n"
        f"EMA50: {result['ema50']:.2f}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"MACD: {result['macd']:.4f}\n"
        f"ADX: {result['adx']:.2f}\n\n"

        "⚔️ LONG / SHORT\n"
        f"🟢 Long: {long_text}\n"
        f"🔴 Short: {short_text}\n"
        f"📐 Oran: {ratio_text}\n\n"

        f"{signal_icon} SİNYAL: "
        f"{direction}\n"

        f"💪 Sinyal Gücü: "
        f"%{result['strength']}"
    )


# =========================================================
# JET ALERT
# =========================================================

def get_alert_level(
    strength
):

    if strength >= 95:
        return "🚀 JET ALERT — EXTREME"

    if strength >= 90:
        return "🔥 JET ALERT — ÇOK GÜÇLÜ"

    if strength >= 85:
        return "🛩️ JET ALERT — GÜÇLÜ"

    return None


def send_jet_alert(result):

    if not result:
        return

    direction = result[
        "direction"
    ]

    strength = result[
        "strength"
    ]

    name = result[
        "name"
    ]

    if direction not in (
        "LONG",
        "SHORT"
    ):
        return

    if strength < 85:
        return

    level = get_alert_level(
        strength
    )

    if not level:
        return

    previous = last_alerts.get(
        name
    )

    if previous:

        previous_direction = (
            previous["direction"]
        )

        previous_strength = (
            previous["strength"]
        )

        if (
            previous_direction == direction
            and strength
            < previous_strength + 3
        ):

            return

    last_alerts[name] = {
        "direction": direction,
        "strength": strength
    }

    if direction == "LONG":

        emoji = "🟢"
        action = "LONG / YÜKSELİŞ"

    else:

        emoji = "🔴"
        action = "SHORT / DÜŞÜŞ"

    if result["long"] is None:

        long_text = "N/A"
        short_text = "N/A"

    else:

        long_text = (
            f"%{result['long']:.1f}"
        )

        short_text = (
            f"%{result['short']:.1f}"
        )

    message = (
        f"{level}\n"
        "━━━━━━━━━━━━━━━━\n\n"

        f"⚡ {name}\n\n"

        f"{emoji} SİNYAL: "
        f"{action}\n"

        f"💪 Güç: "
        f"%{strength}\n\n"

        "⚔️ LONG / SHORT\n"

        f"🟢 Long: "
        f"{long_text}\n"

        f"🔴 Short: "
        f"{short_text}\n\n"

        "⏱ Zaman dilimi: 1 Saat\n"

        "🤖 Crypto Jet otomatik taraması"
    )

    print(
        "🚨 JET ALERT:",
        name,
        direction,
        strength
    )

    for chat_id in list(
        subscribers
    ):

        send_message(
            chat_id,
            message
        )


# =========================================================
# SİNYAL KONTROL
# =========================================================

def check_new_signals(
    results
):

    for result in results:

        if not result:
            continue

        name = result[
            "name"
        ]

        strength = result[
            "strength"
        ]

        direction = result[
            "direction"
        ]

        if (
            strength < 85
            or direction
            not in (
                "LONG",
                "SHORT"
            )
        ):

            if name in last_alerts:
                del last_alerts[name]

            continue

        send_jet_alert(
            result
        )


# =========================================================
# PAPER TRADING
# =========================================================

def update_paper_trades(
    results
):

    for result in results:

        if not result:
            continue

        name = result[
            "name"
        ]

        direction = result[
            "direction"
        ]

        strength = result[
            "strength"
        ]

        price = result[
            "price"
        ]

        if strength < 85:
            continue

        if direction not in (
            "LONG",
            "SHORT"
        ):
            continue

        existing = None

        for trade in paper_trades:

            if (
                trade["coin"] == name
                and trade["open"]
            ):

                existing = trade
                break

        if existing:

            if (
                existing["direction"]
                != direction
            ):

                entry = existing[
                    "entry"
                ]

                if (
                    existing["direction"]
                    == "LONG"
                ):

                    pnl = (
                        (
                            price
                            - entry
                        )
                        / entry
                    ) * 100

                else:

                    pnl = (
                        (
                            entry
                            - price
                        )
                        / entry
                    ) * 100

                existing[
                    "open"
                ] = False

                existing[
                    "exit"
                ] = price

                existing[
                    "pnl"
                ] = pnl

                print(
                    "📕 Paper trade kapandı:",
                    name,
                    pnl
                )

        else:

            paper_trades.append(
                {
                    "coin": name,
                    "direction": direction,
                    "entry": price,
                    "exit": None,
                    "pnl": 0,
                    "open": True,
                    "time":
                        datetime.now().isoformat()
                }
            )

            print(
                "📗 Paper trade açıldı:",
                name,
                direction,
                price
            )


# =========================================================
# TÜM COİNLERİ TARA
# =========================================================

def scan_all():

    print(
        "\n"
        "================================"
    )

    print(
        "🚀 CRYPTO JET TARAMA"
    )

    print(
        "================================"
    )

    results = []

    for name, symbol in (
        COINS.items()
    ):

        try:

            print(
                f"\n🔍 Taranıyor: {name}"
            )

            result = analyze_coin(
                name,
                symbol
            )

            if result:

                results.append(
                    result
                )

                print(
                    f"📈 {name} "
                    f"{result['direction']} "
                    f"%{result['strength']} "
                    f"L/S: "
                    f"{result['long']} / "
                    f"{result['short']}"
                )

        except Exception as e:

            print(
                f"❌ {name} ANALİZ HATASI:",
                e
            )

    return results


# =========================================================
# RAPOR / ALARM
# =========================================================

def send_report():

    results = scan_all()

    if not results:

        print(
            "❌ Sonuç bulunamadı."
        )

        return

    update_paper_trades(
        results
    )

    # Otomatik normal rapor YOK.
    # Sadece güçlü sinyal alarmı.

    check_new_signals(
        results
    )


# =========================================================
# BTC
# =========================================================

def send_btc(chat_id):

    result = analyze_coin(
        "BTC",
        COINS["BTC"]
    )

    if result:

        send_message(
            chat_id,
            format_coin(result)
        )

    else:

        send_message(
            chat_id,
            "❌ BTC analizi alınamadı."
        )


# =========================================================
# DURUM
# =========================================================

def send_status(chat_id):

    open_trades = sum(
        1
        for trade in paper_trades
        if trade["open"]
    )

    text = (
        "🛩️ CRYPTO JET DURUM\n"
        "━━━━━━━━━━━━━━━━\n\n"

        "🟢 Bot: AKTİF\n"
        "⏱ Tarama: 10 dakikada 1\n"
        "📊 Zaman dilimi: 1 Saat\n"
        "🪙 Coin sayısı: 10\n\n"

        "🔔 JET ALERT\n"
        "85%+ güçlü\n"
        "90%+ çok güçlü\n"
        "95%+ extreme\n\n"

        f"📈 Açık paper trade: "
        f"{open_trades}"
    )

    send_message(
        chat_id,
        text
    )


# =========================================================
# PERFORMANS
# =========================================================

def send_performance(
    chat_id
):

    closed = [
        t
        for t in paper_trades
        if not t["open"]
    ]

    if not closed:

        send_message(
            chat_id,
            "📊 Henüz kapanmış "
            "paper trade yok."
        )

        return

    total = len(
        closed
    )

    wins = sum(
        1
        for t in closed
        if t["pnl"] > 0
    )

    losses = sum(
        1
        for t in closed
        if t["pnl"] < 0
    )

    total_pnl = sum(
        t["pnl"]
        for t in closed
    )

    winrate = (
        wins / total * 100
        if total
        else 0
    )

    text = (
        "📊 CRYPTO JET PERFORMANS\n"
        "━━━━━━━━━━━━━━━━\n\n"

        f"📈 İşlem: {total}\n"
        f"🟢 Kazanç: {wins}\n"
        f"🔴 Kayıp: {losses}\n"
        f"🎯 Başarı: %{winrate:.1f}\n"
        f"💰 Toplam: %{total_pnl:.2f}"
    )

    send_message(
        chat_id,
        text
    )


# =========================================================
# COİNLER
# =========================================================

def send_coins(chat_id):

    text = (
        "🪙 CRYPTO JET COINLER\n"
        "━━━━━━━━━━━━━━━━\n\n"
    )

    for coin in COINS:

        text += (
            f"• {coin}\n"
        )

    send_message(
        chat_id,
        text
    )


# =========================================================
# HELP
# =========================================================

def send_help(chat_id):

    text = (
        "🛩️ CRYPTO JET KOMUTLARI\n"
        "━━━━━━━━━━━━━━━━\n\n"

        "/start — Botu başlat\n"
        "/stop — Bildirimleri durdur\n"
        "/btc — BTC analizi\n"
        "/durum — Bot durumu\n"
        "/performans — Paper trade\n"
        "/coinler — İzlenen coinler\n"
        "/help — Yardım\n\n"

        "🔔 Güçlü sinyaller otomatik alarm.\n"
        "85%+ güçlü\n"
        "90%+ çok güçlü\n"
        "95%+ extreme"
    )

    send_message(
        chat_id,
        text
    )


# =========================================================
# TELEGRAM UPDATE
# =========================================================

def process_updates():

    global subscribers

    offset = 0

    print(
        "🚀 Crypto Jet Telegram botu başladı!"
    )

    last_scan = 0

    while True:

        try:

            response = telegram(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30
                }
            )

            if not response:

                time.sleep(2)

                continue

            updates = response.get(
                "result",
                []
            )

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

                chat_id = message[
                    "chat"
                ]["id"]

                text = message.get(
                    "text",
                    ""
                ).strip().lower()

                # -----------------------------------------
                # START
                # -----------------------------------------

                if text == "/start":

                    subscribers.add(
                        chat_id
                    )

                    send_message(
                        chat_id,
                        "🚀 Crypto Jet çalışıyor!\n\n"
                        "🔔 JET ALERT aktif.\n"
                        "💪 %85 ve üzeri güçlü "
                        "sinyaller bildirim olarak "
                        "gönderilecek.\n\n"
                        "⚔️ Long/Short analizi aktif."
                    )

                    send_report()

                # -----------------------------------------
                # STOP
                # -----------------------------------------

                elif text == "/stop":

                    subscribers.discard(
                        chat_id
                    )

                    send_message(
                        chat_id,
                        "🛑 Crypto Jet "
                        "bildirimleri durduruldu."
                    )

                # -----------------------------------------
                # BTC
                # -----------------------------------------

                elif text == "/btc":

                    send_btc(
                        chat_id
                    )

                # -----------------------------------------
                # DURUM
                # -----------------------------------------

                elif text == "/durum":

                    send_status(
                        chat_id
                    )

                # -----------------------------------------
                # PERFORMANS
                # -----------------------------------------

                elif text == "/performans":

                    send_performance(
                        chat_id
                    )

                # -----------------------------------------
                # COINLER
                # -----------------------------------------

                elif text == "/coinler":

                    send_coins(
                        chat_id
                    )

                # -----------------------------------------
                # HELP
                # -----------------------------------------

                elif text == "/help":

                    send_help(
                        chat_id
                    )

            # =================================================
            # OTOMATİK 10 DAKİKA TARAMA
            # =================================================

            now = time.time()

            if (
                subscribers
                and now - last_scan
                >= AUTO_INTERVAL
            ):

                last_scan = now

                print(
                    "⏱ Otomatik tarama..."
                )

                send_report()

        except Exception as e:

            print(
                "❌ ANA DÖNGÜ HATASI:",
                e
            )

            time.sleep(5)


# =========================================================
# BAŞLAT
# =========================================================

if __name__ == "__main__":

    print(
        "================================"
    )

    print(
        "🚀 CRYPTO JET V7.5"
    )

    print(
        "⚔️ LONG / SHORT DEBUG AKTİF"
    )

    print(
        "🔔 JET ALERT AKTİF"
    )

    print(
        "================================"
    )

    process_updates()
