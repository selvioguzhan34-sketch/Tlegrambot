import os
import time
import math
import requests
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# =========================================================
# CRYPTO JET V13 — HİBRİT EARLY MOVE
# =========================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN bulunamadı! export TELEGRAM_BOT_TOKEN='token'")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
COINBASE_API = "https://api.exchange.coinbase.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

session = requests.Session()
session.headers.update({"User-Agent": "CryptoJet/13.1"})

# =========================================================
# AYARLAR
# =========================================================
SCAN_INTERVAL = 300          # 5 dakika
PRODUCT_REFRESH = 1800
ALERT_COOLDOWN = 2400        # 40 dakika
MIN_CANDLES = 45

MICRO_MIN = 0.006            # %0.6
MICRO_MAX = 0.013            # %1.3

WATCH_MIN = 550
ALARM_MIN = 700
STRONG_MIN = 800
ELITE_MIN = 900
EXTREME_MIN = 950

active_chat_id: Optional[int] = None
last_scan_time = 0.0
last_product_refresh = 0.0
products_cache: List[Dict] = []
alert_state: Dict[str, Dict] = {}


# =========================================================
# YARDIMCILAR
# =========================================================
def safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def fmt_price(p) -> str:
    if p is None or p <= 0:
        return "N/A"
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    return f"${p:,.8f}"


# =========================================================
# TELEGRAM
# =========================================================
def send_message(chat_id: Optional[int], text: str) -> bool:
    if not chat_id:
        return False
    try:
        if len(text) <= 3900:
            r = session.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
            return r.status_code == 200

        parts = []
        while text:
            if len(text) <= 3900:
                parts.append(text)
                break
            cut = text.rfind("\n", 0, 3900)
            if cut <= 0:
                cut = 3900
            parts.append(text[:cut])
            text = text[cut:].lstrip("\n")

        ok = True
        for part in parts:
            r = session.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": part}, timeout=20)
            if r.status_code != 200:
                ok = False
            time.sleep(0.3)
        return ok
    except Exception as e:
        print("Telegram hata:", e)
        return False


# =========================================================
# DATA LAYER
# =========================================================
def coinbase_get(path: str, params: dict = None):
    for attempt in range(3):
        try:
            r = session.get(COINBASE_API + path, params=params, timeout=14)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(1.8 * (attempt + 1))
        except Exception as e:
            print("Coinbase:", e)
            time.sleep(1.2)
    return None


def get_products(force: bool = False) -> List[Dict]:
    global products_cache, last_product_refresh
    now = time.time()
    if products_cache and not force and now - last_product_refresh < PRODUCT_REFRESH:
        return products_cache

    data = coinbase_get("/products")
    if not isinstance(data, list):
        return products_cache

    products = []
    for p in data:
        try:
            if (p.get("quote_currency") == "USD" and
                p.get("status") == "online" and
                not p.get("trading_disabled") and
                not p.get("cancel_only") and
                p.get("base_currency") not in {"USD", "USDC", "USDT"}):
                products.append({"id": p["id"], "base": p["base_currency"]})
        except Exception:
            continue

    products_cache = products
    last_product_refresh = now
    print(f"Coin listesi: {len(products)} adet")
    return products


def get_candles(product_id: str, granularity: int, limit: int = 90) -> List[Dict]:
    data = coinbase_get(f"/products/{product_id}/candles", {"granularity": granularity})
    if not isinstance(data, list):
        return []

    candles = []
    for row in data:
        if isinstance(row, list) and len(row) >= 6:
            c = {
                "time": safe_float(row[0]),
                "low": safe_float(row[1]),
                "high": safe_float(row[2]),
                "open": safe_float(row[3]),
                "close": safe_float(row[4]),
                "volume": safe_float(row[5])
            }
            if c["close"] > 0:
                candles.append(c)
    candles.sort(key=lambda x: x["time"])
    return candles[-limit:]


# =========================================================
# MARKET CONTEXT
# =========================================================
def get_market_context() -> Dict:
    btc_price = 0.0
    btc_change = 0.0
    regime = "NEUTRAL"
    dominance = 52.0

    try:
        ticker = coinbase_get("/products/BTC-USD/ticker")
        stats = coinbase_get("/products/BTC-USD/stats")
        if ticker and stats:
            btc_price = safe_float(ticker.get("price"))
            open_24 = safe_float(stats.get("open"))
            if open_24 > 0:
                btc_change = (btc_price - open_24) / open_24 * 100

        if btc_change > 2.2:
            regime = "RISK_ON"
        elif btc_change < -2.2:
            regime = "RISK_OFF"
    except Exception as e:
        print("BTC context:", e)

    try:
        r = session.get(f"{COINGECKO_API}/global", timeout=8)
        if r.status_code == 200:
            dominance = safe_float(r.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 52))
    except Exception:
        pass

    if dominance >= 56:
        dom_bias = "BEARISH_ALT"
    elif dominance <= 47:
        dom_bias = "BULLISH_ALT"
    else:
        dom_bias = "NEUTRAL"

    return {
        "btc_price": btc_price,
        "btc_change": btc_change,
        "regime": regime,
        "dominance": dominance,
        "dom_bias": dom_bias
    }


# =========================================================
# GÖSTERGELER
# =========================================================
def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = (v - e) * k + e
    return e


def atr(candles: List[Dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i-1]
        tr = max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
        trs.append(tr)
    return sum(trs[-period:]) / period


def bollinger_width(candles: List[Dict], period: int = 20) -> Optional[float]:
    if len(candles) < period:
        return None
    closes = [c["close"] for c in candles[-period:]]
    mid = sum(closes) / period
    var = sum((x - mid) ** 2 for x in closes) / period
    std = math.sqrt(var)
    return (2 * std) / mid if mid > 0 else None


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# =========================================================
# COMPRESSION + EARLY MOVE
# =========================================================
def calc_compression(candles: List[Dict]) -> Dict:
    if len(candles) < 32:
        return {"score": 0.0, "bb_width": None, "atr_ratio": None, "range_pct": None}

    bb_width = bollinger_width(candles, 20)
    atr_now = atr(candles[-16:], 14)
    atr_prev = atr(candles[-30:-16], 14)
    atr_ratio = (atr_now / atr_prev) if (atr_prev and atr_prev > 0) else 1.0

    recent = candles[-12:]
    high = max(c["high"] for c in recent)
    low = min(c["low"] for c in recent)
    mid = (high + low) / 2
    range_pct = (high - low) / mid if mid > 0 else 1.0

    score = 0.0
    if bb_width is not None:
        if bb_width < 0.022: score += 42
        elif bb_width < 0.032: score += 32
        elif bb_width < 0.042: score += 20
        elif bb_width < 0.055: score += 10

    if atr_ratio < 0.72: score += 32
    elif atr_ratio < 0.82: score += 22
    elif atr_ratio < 0.92: score += 12

    if range_pct < 0.016: score += 26
    elif range_pct < 0.025: score += 18
    elif range_pct < 0.035: score += 10

    return {
        "score": clamp(score, 0, 100),
        "bb_width": bb_width,
        "atr_ratio": atr_ratio,
        "range_pct": range_pct
    }


def detect_micro_breakout(candles: List[Dict]) -> Dict:
    if len(candles) < 6:
        return {"active": False, "direction": None, "pct": 0.0, "volume_spike": False}

    last = candles[-1]
    prev_close = candles[-2]["close"]
    move = (last["close"] - prev_close) / prev_close

    direction = None
    if MICRO_MIN <= abs(move) <= MICRO_MAX:
        direction = "LONG" if move > 0 else "SHORT"

    vols = [c["volume"] for c in candles[-9:-1]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    volume_spike = last["volume"] > avg_vol * 1.4 if avg_vol > 0 else False

    return {
        "active": direction is not None,
        "direction": direction,
        "pct": abs(move) * 100,
        "volume_spike": volume_spike
    }


# =========================================================
# MULTI TIMEFRAME BASİT TREND
# =========================================================
def get_tf_bias(candles: List[Dict]) -> str:
    if len(candles) < 30:
        return "NEUTRAL"
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50) if len(closes) >= 50 else None
    price = closes[-1]

    if e20 is None:
        return "NEUTRAL"
    if e50 is not None:
        if price > e20 > e50:
            return "LONG"
        if price < e20 < e50:
            return "SHORT"
    if price > e20:
        return "LONG"
    if price < e20:
        return "SHORT"
    return "NEUTRAL"


# =========================================================
# ANA ANALİZ
# =========================================================
@dataclass
class Result:
    product: Dict
    price: float
    decision: str
    score: int
    level: str
    compression: Dict
    micro: Dict
    tf_bias: Dict
    market: Dict
    reasons: List[str] = field(default_factory=list)


def analyze(product: Dict, market: Dict) -> Optional[Result]:
    c1h = get_candles(product["id"], 3600, 80)
    if len(c1h) < MIN_CANDLES:
        return None

    c4h = get_candles(product["id"], 14400, 60)
    c1d = get_candles(product["id"], 86400, 40)

    price = c1h[-1]["close"]
    compression = calc_compression(c1h)
    micro = detect_micro_breakout(c1h)

    bias_1h = get_tf_bias(c1h)
    bias_4h = get_tf_bias(c4h) if len(c4h) >= 30 else "NEUTRAL"
    bias_1d = get_tf_bias(c1d) if len(c1d) >= 25 else "NEUTRAL"

    # ----------------- SKOR -----------------
    score = 0.0
    reasons = []

    # 1. Compression (en önemli erken sinyal)
    comp = compression["score"]
    score += comp * 0.38
    if comp >= 75:
        reasons.append(f"Güçlü sıkışma ({comp:.0f})")
    elif comp >= 55:
        reasons.append(f"Sıkışma var ({comp:.0f})")

    # 2. Micro Breakout
    if micro["active"]:
        score += 28
        reasons.append(f"Micro breakout %{micro['pct']:.2f} → {micro['direction']}")
        if micro["volume_spike"]:
            score += 14
            reasons.append("Hacim artışı onaylı")

    # 3. Multi-TF uyumu
    tf_score = 0
    if bias_1h == bias_4h and bias_1h != "NEUTRAL":
        tf_score += 18
    if bias_4h == bias_1d and bias_4h != "NEUTRAL":
        tf_score += 12
    if bias_1h == bias_4h == bias_1d and bias_1h != "NEUTRAL":
        tf_score += 10
    score += tf_score

    if tf_score >= 25:
        reasons.append("Güçlü multi-TF uyumu")
    elif tf_score >= 15:
        reasons.append("TF uyumu mevcut")

    # 4. Market Context çarpanı
    multiplier = 1.0
    direction = micro["direction"] if micro["active"] else None

    if direction == "LONG":
        if market["regime"] == "RISK_OFF" or market["btc_change"] < -1.8:
            multiplier *= 0.62
            reasons.append("BTC zayıf → Long cezalı")
        if market["dom_bias"] == "BEARISH_ALT":
            multiplier *= 0.78
            reasons.append("BTC.D yüksek → Alt long zayıf")
        if market["regime"] == "RISK_ON" and market["dom_bias"] == "BULLISH_ALT":
            multiplier *= 1.12
            reasons.append("Market Context LONG destekliyor")

    elif direction == "SHORT":
        if market["regime"] == "RISK_ON" or market["btc_change"] > 1.8:
            multiplier *= 0.68
            reasons.append("BTC güçlü → Short cezalı")

    score *= multiplier
    score = int(clamp(score, 0, 1000))

    # Karar
    if micro["active"]:
        decision = micro["direction"]
    elif comp >= 78:
        decision = "WATCH"
    else:
        decision = "BEKLE"

    # Seviye
    if score >= EXTREME_MIN:
        level = "EXTREME"
    elif score >= ELITE_MIN:
        level = "ELITE"
    elif score >= STRONG_MIN:
        level = "STRONG"
    elif score >= ALARM_MIN:
        level = "ALARM"
    elif score >= WATCH_MIN or decision == "WATCH":
        level = "WATCH"
    else:
        level = "NONE"

    return Result(
        product=product,
        price=price,
        decision=decision,
        score=score,
        level=level,
        compression=compression,
        micro=micro,
        tf_bias={"1H": bias_1h, "4H": bias_4h, "1D": bias_1d},
        market=market,
        reasons=reasons
    )


# =========================================================
# RAPOR
# =========================================================
def build_report(r: Result) -> str:
    emoji = {"LONG": "🟢", "SHORT": "🔴", "WATCH": "🟡", "BEKLE": "⚪"}.get(r.decision, "⚪")
    level_emoji = {
        "EXTREME": "💥", "ELITE": "🚀", "STRONG": "🔥",
        "ALARM": "🟢", "WATCH": "👀", "NONE": "⚪"
    }.get(r.level, "⚪")

    reasons_text = "\n".join(f"• {x}" for x in r.reasons[:7]) or "• Belirgin neden yok"

    text = f"""
🚀 CRYPTO JET V13 — HİBRİT
━━━━━━━━━━━━━━━━━━━━

🪙 {r.product['base']}
💰 {fmt_price(r.price)}

{emoji} Yön: {r.decision}
{level_emoji} Seviye: {r.level}
💪 Skor: {r.score}/1000

📊 Compression: {r.compression['score']:.0f}/100
📈 Micro: {"Var → " + r.micro['direction'] + f" (%{r.micro['pct']:.2f})" if r.micro['active'] else "Yok"}

🕰 TF Bias:
1H: {r.tf_bias['1H']} | 4H: {r.tf_bias['4H']} | 1D: {r.tf_bias['1D']}

🌐 Market:
BTC: {r.market['btc_change']:+.2f}% | Dom: {r.market['dominance']:.1f}%
Rejim: {r.market['regime']}

━━━━━━━━━━━━━━━━━━━━
🧠 Nedenler:
{reasons_text}

⚠️ Erken sinyal sistemidir. Garanti değildir.
"""
    return text.strip()


def should_alert(r: Result) -> bool:
    if r.level in ("NONE",):
        return False
    if r.score < WATCH_MIN and r.level != "WATCH":
        return False

    key = (r.decision, r.level, r.score // 20)
    now = time.time()
    prev = alert_state.get(r.product["id"])
    if prev and prev["key"] == key and now - prev["time"] < ALERT_COOLDOWN:
        return False

    alert_state[r.product["id"]] = {"key": key, "time": now}
    return True


# =========================================================
# TARAMA
# =========================================================
def market_scan(chat_id: Optional[int] = None, send_alerts: bool = False) -> List[Result]:
    products = get_products()
    if not products:
        if chat_id:
            send_message(chat_id, "❌ Coin listesi alınamadı.")
        return []

    market = get_market_context()
    results = []
    strong = []

    print(f"\n🚀 Tarama başladı — {len(products)} coin | BTC {market['btc_change']:+.2f}% | Dom {market['dominance']:.1f}%")

    for i, product in enumerate(products, 1):
        try:
            r = analyze(product, market)
            if r is None:
                continue
            results.append(r)
            if r.level in ("WATCH", "ALARM", "STRONG", "ELITE", "EXTREME"):
                strong.append(r)

            print(f"[{i}/{len(products)}] {product['base']:<8} {r.decision:<6} {r.score:4d} {r.level}")
            time.sleep(0.07)
        except Exception as e:
            print(f"Hata {product['base']}:", e)

    results.sort(key=lambda x: x.score, reverse=True)
    strong.sort(key=lambda x: x.score, reverse=True)

    if chat_id and send_alerts:
        for r in strong:
            if should_alert(r):
                send_message(chat_id, build_report(r))

    if chat_id:
        summary = f"🚀 CRYPTO JET V13\n━━━━━━━━━━━━━━━━\n\nAnaliz: {len(results)} coin\nSinyal: {len(strong)}\n\n"
        if strong:
            summary += "En güçlüler:\n"
            for r in strong[:15]:
                icon = {"LONG": "🟢", "SHORT": "🔴", "WATCH": "👀"}.get(r.decision, "⚪")
                summary += f"{r.product['base']:<8} {icon} {r.score}/1000 {r.level}\n"
        else:
            summary += "Şu an güçlü sinyal yok.\n"
        send_message(chat_id, summary)

    return results


# =========================================================
# KOMUTLAR
# =========================================================
def handle_command(chat_id: int, text: str):
    global active_chat_id, last_scan_time
    text = text.strip().lower()

    if text == "/start":
        send_message(chat_id, """
🚀 CRYPTO JET V13 — HİBRİT EARLY MOVE

Komutlar:
/jet     → Sistemi başlat (otomatik tarama)
/scan    → Tek seferlik tarama
/btc     → BTC durumu
/status  → Sistem durumu
/stop    → Durdur

Seviyeler:
👀 WATCH → Sıkışma
🟢 ALARM → İlk kıpırdanma
🔥 STRONG
🚀 ELITE
💥 EXTREME
""")
    elif text == "/jet":
        active_chat_id = chat_id
        send_message(chat_id, "🚀 Jet V13 aktif.\n5 dakikada bir tarama yapılacak.\nİlk tarama başlıyor...")
        market_scan(chat_id, send_alerts=True)
        last_scan_time = time.time()
    elif text == "/scan":
        send_message(chat_id, "🔎 Tarama başlatıldı, biraz sürebilir...")
        market_scan(chat_id, send_alerts=True)
        last_scan_time = time.time()
    elif text == "/btc":
        m = get_market_context()
        send_message(chat_id, f"""
₿ BTC Context
Fiyat: {fmt_price(m['btc_price'])}
24s: {m['btc_change']:+.2f}%
Rejim: {m['regime']}
Dominance: {m['dominance']:.2f}%
Bias: {m['dom_bias']}
""")
    elif text == "/status":
        status = "🟢 AKTİF" if active_chat_id else "🟡 KAPALI"
        send_message(chat_id, f"Durum: {status}\nCoin: {len(get_products())}\nSon tarama aralığı: 5 dk")
    elif text == "/stop":
        active_chat_id = None
        send_message(chat_id, "🛑 Jet durduruldu.\nTekrar başlatmak için /jet")
    elif text.startswith("/"):
        send_message(chat_id, "Bilinmeyen komut.\n/jet /scan /btc /status /stop")


def automatic_scan():
    global last_scan_time
    if not active_chat_id:
        return
    now = time.time()
    if now - last_scan_time < SCAN_INTERVAL:
        return
    last_scan_time = now
    print("⏱ Otomatik tarama...")
    results = market_scan(None, send_alerts=False)
    strong = [r for r in results if r.level in ("WATCH", "ALARM", "STRONG", "ELITE", "EXTREME")]
    for r in strong:
        if should_alert(r):
            send_message(active_chat_id, build_report(r))


# =========================================================
# MAIN
# =========================================================
def main():
    print("🚀 CRYPTO JET V13 — HİBRİT EARLY MOVE başlatıldı")
    offset = 0
    while True:
        try:
            r = session.get(f"{TELEGRAM_API}/getUpdates", params={"offset": offset, "timeout": 10}, timeout=15)
            if r.status_code != 200:
                time.sleep(4)
                continue
            data = r.json()
            if not data.get("ok"):
                time.sleep(4)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg.get("text", "")
                if text:
                    handle_command(chat_id, text)

            automatic_scan()
            time.sleep(1)
        except KeyboardInterrupt:
            print("Kapatıldı.")
            break
        except Exception as e:
            print("Ana döngü hata:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
