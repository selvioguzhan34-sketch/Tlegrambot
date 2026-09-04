#!/usr/bin/env python3
"""
SCALP JET V3
Binance long/short + funding + hacim skor karti.
Renkli funding, 3 kademeli hedef, stop, pozisyon boyutu, cooldown.
Sadece sinyal. Borsa emri yok. Yatirim tavsiyesi degildir.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

import requests

VERSION = "3.0"
DRY_RUN = os.getenv("CRYPTOJET_DRYRUN", "").strip().lower() in {"1", "true", "yes"}
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN and not DRY_RUN:
    raise ValueError("TELEGRAM_BOT_TOKEN yok. Test icin: CRYPTOJET_DRYRUN=1")

TG = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
BN = "https://fapi.binance.com"

session = requests.Session()
session.headers.update({
    "User-Agent": f"ScalpJet/{VERSION}",
    "Accept": "application/json",
})

SCAN_SEC = int(os.getenv("SCAN_SEC", "60"))
TOP_N = int(os.getenv("TOP_N", "50"))
MIN_VOL = float(os.getenv("MIN_VOL", "5000000"))
LONG_HEAVY = float(os.getenv("LONG_HEAVY", "60"))
SHORT_HEAVY = float(os.getenv("SHORT_HEAVY", "60"))
FUND_EXTREME = float(os.getenv("FUND_EXTREME", "0.05"))
ENTRY_MOVE = float(os.getenv("ENTRY_MOVE", "0.5"))
RVOL_MIN = float(os.getenv("RVOL_MIN", "1.3"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "1200"))
RISK_PCT = float(os.getenv("RISK_PCT", "1.5"))
PP1_PCT = float(os.getenv("PP1_PCT", "1.5"))
PP2_PCT = float(os.getenv("PP2_PCT", "3.0"))
PP3_PCT = float(os.getenv("PP3_PCT", "5.0"))
STOP_PCT = float(os.getenv("STOP_PCT", "1.5"))
PAPER_BALANCE = float(os.getenv("PAPER_BALANCE", "1000"))

chat_id: Optional[int] = None
last_scan = 0.0
products: List[Dict] = []
cooldown: Dict[str, float] = {}
balance = PAPER_BALANCE


def safe(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def tg(method, data=None) -> bool:
    if DRY_RUN:
        if data and data.get("text"):
            print("\n===== MSG =====\n" + str(data["text"])[:1500] + "\n===============\n")
        return True
    try:
        r = session.post(f"{TG}/{method}", data=data, timeout=20)
        return r.status_code == 200
    except Exception as e:
        print("tg", e)
        return False


def send(cid, text):
    if not cid and not DRY_RUN:
        return
    tg("sendMessage", {
        "chat_id": cid or 0,
        "text": text[:3900],
        "disable_web_page_preview": True,
    })


def bn(path, params=None):
    try:
        r = session.get(BN + path, params=params or {}, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("bn", e)
    return None


def ticker_price(sym):
    d = bn("/fapi/v1/ticker/price", {"symbol": sym})
    if isinstance(d, dict):
        p = safe(d.get("price"))
        return p if p > 0 else None
    return None


def candles(sym, interval="15m", n=40):
    d = bn("/fapi/v1/klines", {"symbol": sym, "interval": interval, "limit": n})
    if not isinstance(d, list):
        return []
    out = []
    for c in d:
        if not isinstance(c, list) or len(c) < 6:
            continue
        out.append({
            "ts": int(safe(c[0])),
            "open": safe(c[1]),
            "high": safe(c[2]),
            "low": safe(c[3]),
            "close": safe(c[4]),
            "volume": safe(c[5]),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def rvol(cs, n=16):
    if len(cs) < 6:
        return 1.0
    last = cs[-1]["volume"]
    base = [c["volume"] for c in cs[-n - 1:-1] if c["volume"] > 0]
    if not base:
        return 1.0
    avg = sum(base) / len(base)
    return last / avg if avg > 0 else 1.0


def ls_ratio(sym):
    d = bn("/futures/data/globalLongShortAccountRatio", {
        "symbol": sym,
        "period": "15m",
        "limit": 1,
    })
    return d[0] if isinstance(d, list) and d else None


def funding(sym):
    d = bn("/fapi/v1/premiumIndex", {"symbol": sym})
    return d if isinstance(d, dict) else None


def oi(sym):
    d = bn("/fapi/v1/openInterest", {"symbol": sym})
    return d if isinstance(d, dict) else None


def score_card(sym):
    ls = ls_ratio(sym)
    if not ls:
        return None
    long_pct = safe(ls.get("longAccount")) * 100
    short_pct = safe(ls.get("shortAccount")) * 100
    ratio = safe(ls.get("longShortRatio"))
    fr = funding(sym)
    fund = safe(fr.get("lastFundingRate")) * 100 if fr else 0.0
    o = oi(sym)
    oi_amt = safe(o.get("openInterest")) if o else 0.0
    return {
        "long": long_pct,
        "short": short_pct,
        "ratio": ratio,
        "funding": fund,
        "oi": oi_amt,
    }


def load_products():
    global products
    out = []
    d = bn("/fapi/v1/ticker/24hr")
    if not isinstance(d, list):
        return products
    for p in d:
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol") or ""
        if not sym.endswith("USDT"):
            continue
        if any(x in sym for x in ("_", "USDC", "BUSD")):
            continue
        vol = safe(p.get("quoteVolume"))
        if vol < MIN_VOL:
            continue
        base = sym[:-4]
        out.append({
            "id": sym,
            "base": base,
            "chg": safe(p.get("priceChangePercent")),
            "price": safe(p.get("lastPrice")),
            "vol": vol,
        })
    out.sort(key=lambda x: x["vol"], reverse=True)
    products = out[:TOP_N]
    return products


def targets(entry, bias):
    if bias == "LONG":
        return {
            "pp1": entry * (1 + PP1_PCT / 100),
            "pp2": entry * (1 + PP2_PCT / 100),
            "pp3": entry * (1 + PP3_PCT / 100),
            "stop": entry * (1 - STOP_PCT / 100),
        }
    return {
        "pp1": entry * (1 - PP1_PCT / 100),
        "pp2": entry * (1 - PP2_PCT / 100),
        "pp3": entry * (1 - PP3_PCT / 100),
        "stop": entry * (1 + STOP_PCT / 100),
    }


def position_size(entry, stop):
    risk_amount = balance * RISK_PCT / 100
    per_unit = abs(entry - stop)
    if per_unit <= 0:
        return 0.0
    return risk_amount / per_unit


def signal(sc, move, rv):
    reasons = []
    bias = None
    if sc["long"] >= LONG_HEAVY:
        bias = "LONG"
        reasons.append(f"long %{sc['long']:.0f}")
        if sc["funding"] >= FUND_EXTREME:
            reasons.append("funding yuksek")
    elif sc["short"] >= SHORT_HEAVY:
        bias = "SHORT"
        reasons.append(f"short %{sc['short']:.0f}")
        if sc["funding"] <= -FUND_EXTREME:
            reasons.append("funding dusuk")
    if move >= ENTRY_MOVE and rv >= RVOL_MIN:
        reasons.append(f"15m +{move:.2f}% rVol {rv:.2f}x")
    elif move <= -ENTRY_MOVE and rv >= RVOL_MIN:
        reasons.append(f"15m {move:.2f}% rVol {rv:.2f}x")
        if bias == "LONG":
            bias = None
    if bias and len(reasons) >= 2:
        return bias, reasons
    return None, reasons


def funding_label(fund):
    if fund >= FUND_EXTREME:
        return "🔴 LONG ÖDÜYOR"
    if fund <= -FUND_EXTREME:
        return "🟢 SHORT ÖDÜYOR"
    return "⚪ NÖTR"


def scan(cid):
    global last_scan
    last_scan = time.time()
    if not products:
        load_products()
    btc = ticker_price("BTCUSDT")
    send(
        cid,
        f"SCALP JET V{VERSION}\n"
        f"BTC {btc or '-'}\n"
        f"Tarama: {len(products)} coin | Bakiye ${balance:.0f}",
    )
    for p in products:
        base = p["base"]
        if time.time() - cooldown.get(base, 0) < COOLDOWN_SEC:
            continue
        sym = p["id"]
        sc = score_card(sym)
        if not sc:
            continue
        cs = candles(sym)
        if len(cs) < 20:
            continue
        last = cs[-1]
        move = (last["close"] - last["open"]) / last["open"] * 100 if last["open"] else 0.0
        rv = rvol(cs)
        bias, reasons = signal(sc, move, rv)
        if not bias:
            continue
        px = ticker_price(sym) or last["close"]
        t = targets(px, bias)
        qty = position_size(px, t["stop"])
        cooldown[base] = time.time()
        tag = "🟢 YUKSELIS" if bias == "LONG" else "🔴 DUSUS"
        fl = funding_label(sc["funding"])
        send(cid, (
            f"{tag}  {base}\n"
            f"Long %{sc['long']:.0f}  Short %{sc['short']:.0f}  oran {sc['ratio']:.2f}\n"
            f"{fl}  (funding %{sc['funding']:.4f})\n"
            f"OI {sc['oi']:.0f} | Fiyat {px:.6g} | 24s {p['chg']:+.1f}% | Hacim ${p['vol']/1e6:.1f}M\n"
            f"15m {move:+.2f}%  rVol {rv:.2f}x\n"
            f"🎯 PP1 {t['pp1']:.4g} · PP2 {t['pp2']:.4g} · PP3 {t['pp3']:.4g}\n"
            f"🛑 Stop {t['stop']:.4g} | Boyut {qty:.4g} {base} (~${qty*px:.1f})\n"
            f"Sebep: {', '.join(reasons)}"
        ))
        time.sleep(0.15)


def handle(cid, text):
    global chat_id
    t = (text or "").strip().lower()
    if t in {"/start", "/help"}:
        send(cid, (
            f"SCALP JET V{VERSION}\n\n"
            "Binance long/short + funding + hacim skor karti.\n"
            "Renkli funding, 3 kademeli hedef, stop, pozisyon boyutu.\n"
            "Sadece sinyal, emir yok.\n\n"
            "/on  tarama ac\n"
            "/off kapat\n"
            "/now bir tarama\n"
            "/top en yuksek hacimli\n"
            "/balance bakiye goster"
        ))
    elif t == "/on":
        chat_id = cid
        load_products()
        send(cid, f"Tarama acik. {len(products)} coin.")
        scan(cid)
    elif t == "/off":
        chat_id = None
        send(cid, "Kapandi.")
    elif t == "/now":
        load_products()
        scan(cid)
    elif t == "/top":
        load_products()
        lines = [
            f"{i+1}. {p['base']}  ${p['vol']/1e6:.1f}M  {p['chg']:+.1f}%"
            for i, p in enumerate(products[:15])
        ]
        send(cid, "EN YUKSEK HACIM\n" + "\n".join(lines))
    elif t == "/balance":
        send(cid, f"Bakiye: ${balance:.2f} | Risk/islem: %{RISK_PCT}")
    else:
        send(cid, "/on /off /now /top /balance")


def main():
    print(f"SCALP JET V{VERSION}")
    if DRY_RUN:
        load_products()
        print("urun", len(products), [p["base"] for p in products[:8]])
        scan(0)
        return
    offset = 0
    while True:
        try:
            r = session.get(
                f"{TG}/getUpdates",
                params={"offset": offset, "timeout": 10},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                for u in data.get("result", []):
                    offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if msg.get("text"):
                        handle(msg["chat"]["id"], msg["text"])
            if chat_id and time.time() - last_scan >= SCAN_SEC:
                scan(chat_id)
            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("loop", e)
            time.sleep(4)


if __name__ == "__main__":
    main()
