#!/usr/bin/env python3
"""
SCALP JET — video kuralı (sinyal + kâğıt pozisyon)

Kaynak short: anlık tarama, yükselişte AL,
10-15 dk içinde +%3 SAT, hedefe gelmeden düşerse
zirve kârın yarısında çık, başka coine geç.

OKX spot emir (opsiyonel). Cekim yok.
Yatirim tavsiyesi degildir.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

import hmac
import hashlib
import base64
import json
import requests

VERSION = "1.2"
DRY_RUN = os.getenv("CRYPTOJET_DRYRUN", "").strip().lower() in {"1", "true", "yes"}
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN and not DRY_RUN:
    raise ValueError("TELEGRAM_BOT_TOKEN yok. Test: CRYPTOJET_DRYRUN=1")

TG = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
CB = "https://api.coinbase.com/api/v3/brokerage"

session = requests.Session()
session.headers.update({"User-Agent": f"ScalpJet/{VERSION}", "Accept": "application/json"})

SCAN_SEC = int(os.getenv("SCAN_SEC", "45"))
TARGET_PCT = float(os.getenv("TARGET_PCT", "3.0"))
MAX_HOLD_SEC = int(os.getenv("MAX_HOLD_SEC", "900"))
ENTRY_MOVE = float(os.getenv("ENTRY_MOVE", "0.45"))
RVOL_MIN = float(os.getenv("RVOL_MIN", "1.35"))
STOP_PCT = float(os.getenv("STOP_PCT", "1.20"))
FEE_PCT = float(os.getenv("FEE_PCT", "0.20"))
MAX_OPEN = int(os.getenv("MAX_OPEN", "3"))
WATCH = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
         "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "NEAR-USD"]


OKX_KEY = os.getenv("OKX_API_KEY", "").strip()
OKX_SEC = os.getenv("OKX_SECRET", "").strip()
OKX_PASS = os.getenv("OKX_PASSPHRASE", "").strip()
OKX_LIVE = os.getenv("OKX_LIVE", "0").strip() in {"1", "true", "yes"}
OKX_DEMO = not OKX_LIVE
ORDER_USDT = min(float(os.getenv("ORDER_USDT", "10")), float(os.getenv("ORDER_USDT_MAX", "50")))
OKX_BASE = "https://www.okx.com"

chat_id: Optional[int] = None
last_scan = 0.0
products: List[Dict] = []
paper: Dict[str, Dict] = {}
cooldown: Dict[str, float] = {}
trade_armed = False


def safe(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def tg(method: str, data=None) -> bool:
    if DRY_RUN:
        if data and data.get("text"):
            print("\n===== MSG =====\n" + str(data["text"])[:1200] + "\n===============\n")
        else:
            print("[DRYRUN]", method)
        return True
    if not TOKEN:
        return False
    try:
        r = session.post(f"{TG}/{method}", data=data, timeout=20)
        return r.status_code == 200
    except Exception as e:
        print("tg", e)
        return False


def send(cid, text: str):
    if not cid and not DRY_RUN:
        return
    tg("sendMessage", {"chat_id": cid or 0, "text": text[:3900], "disable_web_page_preview": True})


def adv(path: str, params=None):
    try:
        r = session.get(CB + path, params=params or {}, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("cb", e)
    return None


def spot(pid: str) -> Optional[float]:
    data = adv(f"/market/products/{pid}")
    if isinstance(data, dict):
        px = safe(data.get("price"))
        return px if px > 0 else None
    return None


def candles(pid: str, gran: str = "FIFTEEN_MINUTE", n: int = 40) -> List[Dict]:
    now = int(time.time())
    data = adv(
        f"/market/products/{pid}/candles",
        {"start": str(now - n * 900), "end": str(now), "granularity": gran},
    )
    raw = data.get("candles") if isinstance(data, dict) else None
    if not raw:
        return []
    out = []
    for c in raw:
        out.append({
            "ts": int(safe(c.get("start"))),
            "open": safe(c.get("open")),
            "high": safe(c.get("high")),
            "low": safe(c.get("low")),
            "close": safe(c.get("close")),
            "volume": safe(c.get("volume")),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def rvol(cs: List[Dict], n: int = 16) -> float:
    if len(cs) < 6:
        return 1.0
    last = cs[-1]["volume"]
    base = [c["volume"] for c in cs[-n-1:-1] if c["volume"] > 0]
    if not base:
        return 1.0
    avg = sum(base) / len(base)
    return last / avg if avg > 0 else 1.0



def okx_ready() -> bool:
    return bool(OKX_KEY and OKX_SEC and OKX_PASS)


def okx_sign(ts: str, method: str, path: str, body: str = "") -> str:
    msg = f"{ts}{method}{path}{body}"
    digest = hmac.new(OKX_SEC.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def okx_req(method: str, path: str, body=None):
    if not okx_ready():
        return {"code": "no-key"}
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    raw = json.dumps(body) if body is not None else ""
    headers = {
        "OK-ACCESS-KEY": OKX_KEY,
        "OK-ACCESS-SIGN": okx_sign(ts, method, path, raw),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": OKX_PASS,
        "Content-Type": "application/json",
    }
    if OKX_DEMO:
        headers["x-simulated-trading"] = "1"
    try:
        r = session.request(method, OKX_BASE + path, headers=headers, data=raw or None, timeout=15)
        return r.json()
    except Exception as e:
        return {"code": "err", "msg": str(e)}


def okx_inst(base: str) -> str:
    return f"{base}-USDT"


def okx_place(base: str, side: str, usdt: float) -> Dict:
    """Spot market. side buy|sell. buy size = USDT, sell size = base qty caller must pass via usdt as qty if side sell? 
    For buy: tgtCcy=quote_ccy sz=USDT
    For sell: we sell all paper qty stored.
    """
    inst = okx_inst(base)
    body = {
        "instId": inst,
        "tdMode": "cash",
        "side": side,
        "ordType": "market",
    }
    if side == "buy":
        body["sz"] = str(round(usdt, 4))
        body["tgtCcy"] = "quote_ccy"
    else:
        body["sz"] = str(usdt)
        body["tgtCcy"] = "base_ccy"
    res = okx_req("POST", "/api/v5/trade/order", body)
    return res if isinstance(res, dict) else {"code": "bad"}




def okx_ok(res: Dict) -> bool:
    return isinstance(res, dict) and str(res.get("code")) == "0"


def okx_err(res: Dict) -> str:
    if not isinstance(res, dict):
        return "yanit yok"
    if res.get("data"):
        d = res["data"][0] if isinstance(res["data"], list) and res["data"] else res["data"]
        if isinstance(d, dict):
            return f"{d.get('sCode','')} {d.get('sMsg','')}"[:200]
    return str(res.get("msg") or res.get("code"))[:200]


def okx_usdt() -> float:
    res = okx_req("GET", "/api/v5/account/balance?ccy=USDT")
    if not okx_ok(res):
        res = okx_req("GET", "/api/v5/asset/balances?ccy=USDT")
    try:
        rows = res.get("data") or []
        if rows and isinstance(rows[0], dict):
            det = rows[0].get("details") or rows
            if isinstance(det, list) and det:
                return safe(det[0].get("availBal") or det[0].get("availEq") or det[0].get("availBal"))
            return safe(rows[0].get("availBal") or rows[0].get("bal"))
    except Exception:
        pass
    return 0.0


def trading_now() -> bool:
    return bool(okx_ready() and trade_armed)


def load_products() -> List[Dict]:
    global products
    out = []
    data = adv("/market/products", {"product_type": "SPOT"})
    rows = data.get("products") if isinstance(data, dict) else None
    if rows:
        for p in rows:
            if p.get("quote_currency_id") != "USD":
                continue
            if p.get("trading_disabled"):
                continue
            base = p.get("base_currency_id") or ""
            if base in {"USD", "USDT", "USDC", "EUR"}:
                continue
            vol = safe(p.get("volume_24h")) * safe(p.get("price"))
            if vol < 2_000_000:
                continue
            out.append({
                "id": p.get("product_id") or f"{base}-USD",
                "base": base,
                "chg": safe(p.get("price_percentage_change_24h")),
                "price": safe(p.get("price")),
            })
        out.sort(key=lambda x: abs(x["chg"]), reverse=True)
        products = out[:18]
        return products
    products = [{"id": x, "base": x.replace("-USD", ""), "chg": 0, "price": 0} for x in WATCH]
    return products


def net_pct(entry: float, now: float) -> float:
    if entry <= 0:
        return 0.0
    return (now - entry) / entry * 100.0 - FEE_PCT


def maybe_enter(p: Dict, cid: int):
    base = p["base"]
    if base in paper or len(paper) >= MAX_OPEN:
        return
    if time.time() - cooldown.get(base, 0) < 20 * 60:
        return
    cs = candles(p["id"])
    if len(cs) < 20:
        return
    last = cs[-1]
    move = (last["close"] - last["open"]) / last["open"] * 100 if last["open"] else 0
    rv = rvol(cs)
    body = abs(last["close"] - last["open"]) / max(last["high"] - last["low"], 1e-9)
    if move < ENTRY_MOVE or rv < RVOL_MIN or body < 0.35:
        return
    px = spot(p["id"]) or last["close"]
    qty = ORDER_USDT / px if px else 0
    live_txt = "kagit (borsa emri yok)"
    filled = True
    if trading_now():
        bal = okx_usdt()
        if bal < ORDER_USDT:
            send(cid, f"AL iptal {base}: USDT yetersiz ({bal:.2f} < {ORDER_USDT})")
            return
        res = okx_place(base, "buy", ORDER_USDT)
        if not okx_ok(res):
            send(cid, f"OKX AL hata {base}: {okx_err(res)}")
            return
        live_txt = f"OKX {'CANLI' if OKX_LIVE else 'DEMO'} emir tamam {ORDER_USDT} USDT"
    paper[base] = {"id": p["id"], "entry": px, "t": time.time(), "peak": px, "peak_pct": 0.0, "qty": qty}
    send(cid, (
        f"AL  {base}\n"
        f"Fiyat {px:.6g}\n"
        f"15m +{move:.2f}%  rVol {rv:.2f}x\n"
        f"Hedef +{TARGET_PCT:.1f}%   sure {MAX_HOLD_SEC//60} dk\n"
        f"Stop -{STOP_PCT:.1f}%   trailing = zirve karin yarisi\n"
        f"Komisyon tahmini %{FEE_PCT:.2f}\n"
        f"{live_txt}"
    ))


def manage(cid: int):
    dead = []
    for base, pos in list(paper.items()):
        px = spot(pos["id"])
        if not px:
            continue
        if px > pos["peak"]:
            pos["peak"] = px
            pos["peak_pct"] = net_pct(pos["entry"], px)
        pnl = net_pct(pos["entry"], px)
        age = time.time() - pos["t"]
        reason = None
        if pnl >= TARGET_PCT:
            reason = f"hedef +{TARGET_PCT:.1f}%"
        elif pnl <= -STOP_PCT:
            reason = f"stop -{STOP_PCT:.1f}%"
        elif pos["peak_pct"] >= 0.8 and pnl <= pos["peak_pct"] * 0.5:
            reason = f"trailing (zirve %{pos['peak_pct']:.2f} -> simdi %{pnl:.2f})"
        elif age >= MAX_HOLD_SEC:
            reason = f"sure doldu {MAX_HOLD_SEC//60} dk  pnl %{pnl:.2f}"
        if not reason:
            continue
        dead.append(base)
        cooldown[base] = time.time()
        live_txt = "kagit"
        if trading_now() and pos.get("qty"):
            res = okx_place(base, "sell", pos["qty"])
            live_txt = ("OKX SAT tamam" if okx_ok(res) else f"OKX SAT hata {okx_err(res)}")
        send(cid, (
            f"SAT  {base}\n"
            f"Giris {pos['entry']:.6g}  cikis {px:.6g}\n"
            f"Net %{pnl:.2f}  (komisyon dusuldu)\n"
            f"Neden: {reason}\n"
            f"{live_txt}"
        ))
    for b in dead:
        paper.pop(b, None)


def scan(cid: int):
    global last_scan
    last_scan = time.time()
    if not products:
        load_products()
    manage(cid)
    btc = spot("BTC-USD")
    send(cid, (
        f"SCALP JET V{VERSION}\n"
        f"BTC {btc if btc else '-'}\n"
        f"Hedef +{TARGET_PCT:.0f}% / {MAX_HOLD_SEC//60} dk\n"
        f"Acik kagit {len(paper)}/{MAX_OPEN}\n"
        f"{', '.join(paper.keys()) or '-'}"
    ))
    for p in products[:12]:
        try:
            maybe_enter(p, cid)
        except Exception as e:
            print("enter", p.get("base"), e)
        time.sleep(0.15)


def handle(cid: int, text: str):
    global chat_id, trade_armed
    t = (text or "").strip().lower()
    if t in {"/start", "/help"}:
        send(cid, (
            f"SCALP JET V{VERSION}\n\n"
            "Video kurali (sinyal + kagit):\n"
            f"- 15m yukselis >= %{ENTRY_MOVE} ve hacim\n"
            f"- AL bildir\n"
            f"- +%{TARGET_PCT:.0f} olursa SAT\n"
            "- zirveden donunce karin yarısında SAT\n"
            f"- {MAX_HOLD_SEC//60} dk dolunca SAT\n\n"
            "/on  taramayi ac\n"
            "/off kapat\n"
            "/now bir tarama\n"
            "/pos acik kagitlar\n\n"
            "OKX: demo varsayilan. Canli icin OKX_LIVE=1\nKey'leri sohbete yazma, ortam degiskeni kullan."
        ))
    elif t == "/on":
        chat_id = cid
        load_products()
        send(cid, "Tarama acik.")
        scan(cid)
    elif t == "/off":
        chat_id = None
        send(cid, "Kapandi.")
    elif t == "/now":
        load_products()
        scan(cid)
    elif t == "/okx":
        if not okx_ready():
            send(cid, "OKX key yok. Ortam: OKX_API_KEY OKX_SECRET OKX_PASSPHRASE")
        else:
            send(cid, f"OKX bagli\nmod {'CANLI' if OKX_LIVE else 'DEMO'}\nemir {'ACIK' if trade_armed else 'KAPALI'}\nUSDT ~{okx_usdt():.2f}\nislem {ORDER_USDT} USDT")
    elif t == "/tradeon":
        if not okx_ready():
            send(cid, "Once key ortamina yaz.")
        else:
            trade_armed = True
            send(cid, f"Borsa emri ACIK ({'CANLI' if OKX_LIVE else 'DEMO'}). Her AL ~{ORDER_USDT} USDT. /tradeoff ile kapat.")
    elif t == "/tradeoff":
        trade_armed = False
        send(cid, "Borsa emri kapandi. Sadece sinyal.")
    elif t == "/pos":
        if not paper:
            send(cid, "Acik kagit yok.")
            return
        lines = ["KAGIT"]
        for b, pos in paper.items():
            px = spot(pos["id"]) or pos["entry"]
            lines.append(f"{b}  giris {pos['entry']:.6g}  simdi {px:.6g}  net %{net_pct(pos['entry'], px):.2f}")
        send(cid, "\n".join(lines))
    else:
        send(cid, "/on /off /now /pos /start")


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
            r = session.get(f"{TG}/getUpdates", params={"offset": offset, "timeout": 10}, timeout=15)
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
