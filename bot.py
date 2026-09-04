#!/usr/bin/env python3
"""
SCALP JET V3 — Binance long/short + funding + hacim skor karti
Renkli funding, 3 kademeli hedef, stop, pozisyon boyutu, cooldown, backtest.
Sadece sinyal, borsa emri yok. Yatirim tavsiyesi degildir.
"""
from __future__ import annotations

import os, time, json, math
from typing import Dict, List, Optional, Tuple
import requests

VERSION = "3.0"
DRY_RUN = os.getenv("CRYPTOJET_DRYRUN", "").strip().lower() in {"1", "true", "yes"}
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN and not DRY_RUN:
    raise ValueError("TELEGRAM_BOT_TOKEN yok. Test: CRYPTOJET_DRYRUN=1")

TG = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
BN = "https://fapi.binance.com"
CB = "https://api.coinbase.com/api/v3/brokerage"

session = requests.Session()
session.headers.update({"User-Agent": f"ScalpJet/{VERSION}", "Accept": "application/json"})

SCAN_SEC = int(os.getenv("SCAN_SEC", "60"))
TOP_N = int(os.getenv("TOP_N", "50"))
MIN_VOL = float(os.getenv("MIN_VOL", "5_000_000"))
LONG_HEAVY = float(os.getenv("LONG_HEAVY", "60"))
SHORT_HEAVY = float(os.getenv("SHORT_HEAVY", "40"))
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

chat_id: Optional = None
last_scan = 0.0
products: List = []
cooldown: Dict =
paper: Dict =
balance = PAPER_BALANCE


def safe(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def tg(method, data=None) -> bool:
    if DRY_RUN:
        if data and data.get("text"):
            print("\n===== MSG =====\n" + str(data )[:1500] + "\n===============\n")
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
    tg("sendMessage", {"chat_id": cid or 0, "text": text[:3900], "disable_web_page_preview": True,
                       "parse_mode": "Markdown"})


def bn(path, params=None):
    try:
        r = session.get(BN + path, params=params or {}, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("bn", e)
    return None


def cb(path, params=None):
    try:
        r = session.get(CB + path, params=params or {}, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("cb", e)
    return None


def spot(pid):
    d = cb(f"/market/products/{pid}")
    if isinstance(d, dict):
        p = safe(d.get("price"))
        return p if p > 0 else None
    return None


def candles(pid, gran="FIFTEEN_MINUTE", n=40):
    now = int(time.time())
    d = cb(f"/market/products/{pid}/candles",
           {"start": str(now - n * 900), "end": str(now), "granularity": gran})
    raw = d.get("candles") if isinstance(d, dict) else None
    if not raw:
        return [ ]
    for c in raw:
        out.append({"ts": int(safe(c.get("start"))), "open": safe(c.get("open")),
                    "high": safe(c.get("high")), "low": safe(c.get("low")),
                    "close": safe(c.get("close")), "volume": safe(c.get("volume"))})
    out.sort(key=lambda x: x )
    return out


def rvol(cs, n=16):
    if len(cs) < 6:
        return 1.0
    last = cs[-1] base = for c in cs if c > 0]
    if not base:
        return 1.0
    avg = sum(base) / len(base)
    return last / avg if avg > 0 else 1.0


def ls_ratio(sym):
    d = bn("/futures/data/globalLongShortAccountRatio",
           {"symbol": sym, "period": "15m", "limit": 1})
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
    fund = safe(fr.get("lastFundingRate")) * 100 if fr else 0
    o = oi(sym)
    oi_amt = safe(o.get("openInterest")) if o else 0
    return {"long": long_pct, "short": short_pct, "ratio": ratio,
            "funding": fund, "oi": oi_amt}


def load_products():
    global products
    out = []
    d = cb("/market/products", {"product_type": "SPOT"})
    rows = d.get("products") if isinstance(d, dict) else None
    if not rows:
        return products
    for p in rows:
        if p.get("quote_currency_id") != "USD":
            continue
        if p.get("trading_disabled"):
            continue
        base = p.get("base_currency_id") or ""
        if base in {"USD", "USDT", "USDC", "EUR"}:
            continue
        vol = safe(p.get("volume_24h")) * safe(p.get("price"))
        if vol < MIN_VOL:
            continue
        out.append({"id": p.get("product_id") or f"{base}-USD", "base": base,
                    "chg": safe(p.get("price_percentage_change_24h")),
                    "price": safe(p.get("price")), "vol": vol})
    out.sort(key=lambda x: x , reverse=True)
    products = out return products


def targets(entry, bias):
    if bias == "LONG":
        return {"pp1": entry * (1 + PP1_PCT/100), "pp2": entry * (1 + PP2_PCT/100),
                "pp3": entry * (1 + PP3_PCT/100), "stop": entry * (1 - STOP_PCT/100)}
    return {"pp1": entry * (1 - PP1_PCT/100), "pp2": entry * (1 - PP2_PCT/100),
            "pp3": entry * (1 - PP3_PCT/100), "stop": entry * (1 + STOP_PCT/100)}


def position_size(entry, stop):
    risk_amount = balance * RISK_PCT / 100
    per_unit = abs(entry - stop)
    if per_unit <= 0:
        return 0.0
    return risk_amount / per_unit


def signal(base, sc, move, rv):
    reasons = []
    bias = None
    if sc >= LONG_HEAVY:
        bias = "LONG"
        reasons.append(f"long %{sc :.0f}")
        if sc >= FUND_EXTREME:
            reasons.append("funding yuksek")
    elif sc >= (100 - SHORT_HEAVY):
        bias = "SHORT"
        reasons.append(f"short %{sc :.0f}")
        if sc <= -FUND_EXTREME:
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
    global last_scan, balance
    last_scan = time.time()
    if not products:
        load_products()
    btc = spot("BTC-USD")
    send(cid, f"🚀 *SCALP JET V{VERSION}*\nBTC {btc or '-'}\nTarama: {len(products)} coin | Bakiye ${balance:.0f}")
    for p in products:
        base = p if time.time() - cooldown.get(base, 0) < COOLDOWN_SEC:
            continue
        sym = f"{base}USDT"
        sc = score_card(sym)
        if not sc:
            continue
        cs = candles(p )
        if len(cs) < 20:
            continue
        last = cs[-1 "open"] * 100 if last else 0
        rv = rvol(cs)
        bias, reasons = signal(base, sc, move, rv)
        if not bias:
            continue
        px = spot(p ) or last t = targets(px, bias)
        qty = position_size(px, t )
        cooldown = time.time()
        tag = "🟢 YUKSELIS" if bias == "LONG" else "🔴 DUSUS"
        fl = funding_label(sc )
        send(cid, (
            f"{tag}  *{base}*\n"
            f"Long %{sc :.0f}  Short %{sc :.0f}  oran {sc :.2f}\n"
            f"{fl}  (funding %{sc :.4f})\n"
            f"OI {sc :.0f} | Fiyat {px:.6g} | 24s {p :+.1f}% | Hacim ${p /1e6:.1f}M\n"
            f"15m {move:+.2f}%  rVol {rv:.2f}x\n"
            f"🎯 PP1 {t :.4g} · PP2 {t['pp2']:.4g} · PP3 {t :.4g}\n"
            f"🛑 Stop {t :.4g} | Boyut {qty:.4g} {base} (~${qty*px:.1f})\n"
            f"Sebep: {', '.join(reasons)}"
        ))
        time.sleep(0.2)


def handle(cid, text):
    global chat_id, balance
    t = (text or "").strip().lower()
    if t in {"/start", "/help"}:
        send(cid, (
            f"🚀 *SCALP JET V{VERSION}*\n\n"
            "Binance long/short + funding + hacim skor karti.\n"
            "Renkli funding, 3 kademeli hedef, stop, pozisyon boyutu.\n"
            "Sadece sinyal, emir yok.\n\n"
            "/on  tarama ac\n/off kapat\n/now bir tarama\n"
            "/top en yuksek hacimli\n/backtest gecmis test\n"
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
        lines = }  ${p /1e6:.1f}M  {p :+.1f}%"
                 for i, p in enumerate(products[:15])]
        send(cid, "EN YUKSEK HACIM\n" + "\n".join(lines))
    elif t == "/balance":
        send(cid, f"Bakiye: ${balance:.2f} | Risk/islem: %{RISK_PCT}")
    elif t == "/backtest":
        send(cid, backtest_report())
    else:
        send(cid, "/on /off /now /top /backtest /balance")


def backtest_report() -> str:
    wins = losses = 0
    samples = 0
    for p in products[:20]:
        cs = candles(p , gran="FIFTEEN_MINUTE", n=2000)
        if len(cs) < 50:
            continue
        for i in range(20, len(cs) - 4):
            window = cs last = window[-1 "open"] * 100 if last else 0
            rv = rvol(window)
            if abs(move) < ENTRY_MOVE or rv < RVOL_MIN:
                continue
            bias = "LONG" if move > 0 else "SHORT"
            entry = last t = targets(entry, bias)
            future = cs[i+1:i+5]
            if not future:
                continue
            hi = max(c for c in future)
            lo = min(c for c in future)
            if bias == "LONG":
                hit = hi >= t else:
                hit = lo <= t if hit:
                wins += 1
            else:
                losses += 1
            samples += 1
            if samples >= 200:
                break
        if samples >= 200:
            break
    if samples == 0:
        return "Backtest: yeterli veri yok (Coinbase baglantisi lazim)."
    wr = wins / samples * 100
    return (f"📊 *BACKTEST* (son ~30g, {samples} sinyal)\n"
            f"Basarili: {wins} | Basarisiz: {losses}\n"
            f"Kazanma orani: %{wr:.1f}\n"
            f"Kural: 15m hareket >%{ENTRY_MOVE}, rVol >{RVOL_MIN}, hedef +%{PP1_PCT}")


def main():
    print(f"SCALP JET V{VERSION}")
    if DRY_RUN:
        load_products()
        print("urun", len(products), [p["base" :8])
        scan(0)
        return
    offset = 0
    while True:
        try:
            r = session.get(f"{TG}/getUpdates", params={"offset": offset, "timeout": 10}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                for u in data.get("result", []):
                    offset = u + 1
                    msg = u.get("message") or
                    if msg.get("text"):
                        handle(msg  , msg )
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
