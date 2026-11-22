"""Paper trader for Version 4.0 Trend-Pullback. No orders are ever placed; public data only."""
import json, os, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

SYMS = ["BTCUSDT", "SOLUSDT"]
ARMS = [1.5, 2.0, 3.0]  # target in R; each is its own virtual account
START_EQ, RISK, MAXLEV = 10000.0, 0.01, 3
FEE, SLIP = 0.0005, 0.0002  # per side
MIN_STOP, MAX_DAY, MAX_HOLD = 0.005, 2, 144  # 144 x 5m = 12h time exit
SRC = ["https://fapi.binance.com/fapi/v1/klines", "https://data-api.binance.vision/api/v3/klines"]
IV = {"5m": 300000, "15m": 900000, "1h": 3600000}


def kl(sym, iv, n):
    err = None
    for u in SRC:
        try:
            q = urllib.parse.urlencode({"symbol": sym, "interval": iv, "limit": n})
            req = urllib.request.Request(u + "?" + q, headers={"User-Agent": "paper/1"})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
            now = time.time() * 1000
            return [[int(x[0])] + [float(v) for v in x[1:6]] for x in d if int(x[0]) + IV[iv] <= now], u
        except Exception as e:
            err = e
    raise RuntimeError(err)


def ema(v, n):
    if len(v) < n:
        return None
    k = 2 / (n + 1)
    e = sum(v[:n]) / n
    for x in v[n:]:
        e = x * k + e * (1 - k)
    return e


def signal(c5, c15, c1, i):
    """Candles are [t,o,h,l,c,v]. Uses only data closed at or before candle i."""
    if i < 30:
        return None
    t = c5[i][0] + 300000
    e20 = ema([x[4] for x in c1 if x[0] + 3600000 <= t], 20)
    e50 = ema([x[4] for x in c1 if x[0] + 3600000 <= t], 50)
    e15 = ema([x[4] for x in c15 if x[0] + 900000 <= t], 20)
    if None in (e20, e50, e15):
        return None
    day = c5[i][0] // 86400000
    d = [x for x in c5[: i + 1] if x[0] // 86400000 == day]
    vw = sum((x[2] + x[3] + x[4]) / 3 * x[5] for x in d) / max(sum(x[5] for x in d), 1e-9)
    c, p, w = c5[i], c5[i - 1], c5[i - 12 : i + 1]
    atr = sum(max(c5[j][2] - c5[j][3], abs(c5[j][2] - c5[j - 1][4]), abs(c5[j][3] - c5[j - 1][4])) for j in range(i - 13, i + 1)) / 14
    if e20 > e50 and c[4] > vw:  # long only
        sw = max(x[2] for x in c5[i - 6 : i])
        if min(x[3] for x in w) <= max(vw, e15) * 1.001 and c[4] > sw and p[4] <= sw:
            return (1, min(x[3] for x in w) - 0.25 * atr)
    if e20 < e50 and c[4] < vw:  # short only
        sw = min(x[3] for x in c5[i - 6 : i])
        if max(x[2] for x in w) >= min(vw, e15) * 0.999 and c[4] < sw and p[4] >= sw:
            return (-1, max(x[2] for x in w) + 0.25 * atr)
    return None


def step(a, sym, r, c, sig):
    day = c[0] // 86400000
    if a["day"] != day:
        a["day"], a["n"] = day, 0
    p, a["pending"] = a["pending"], None
    if p and not a["open"] and a["n"] < MAX_DAY:  # enter at this candle's open
        s = p["s"]
        ent = c[1] * (1 + SLIP * s)
        rk = abs(ent - p["stop"])
        if rk / ent >= MIN_STOP:
            q = min(a["eq"] * RISK / rk, a["eq"] * MAXLEV / ent)
            a["open"] = {"s": s, "ent": ent, "stop": p["stop"], "tgt": ent + s * rk * r, "q": q, "rk": q * rk, "t0": c[0], "k": 0}
            a["n"] += 1
    o = a["open"]
    if o:
        o["k"] += 1
        s, ex = o["s"], None
        if (c[3] <= o["stop"]) if s > 0 else (c[2] >= o["stop"]):
            ex = (o["stop"], "stop")  # stop wins if both hit in one candle
        elif (c[2] >= o["tgt"]) if s > 0 else (c[3] <= o["tgt"]):
            ex = (o["tgt"], "target")
        elif o["k"] >= MAX_HOLD:
            ex = (c[4], "time")
        if ex:
            px = ex[0] * (1 - SLIP * s)
            pnl = (px - o["ent"]) * s * o["q"] - FEE * o["q"] * (o["ent"] + px)
            a["eq"] += pnl
            a["log"].append({"sym": sym, "arm": r, "side": "L" if s > 0 else "S", "t0": o["t0"], "t1": c[0], "ent": round(o["ent"], 4), "px": round(px, 4), "r": round(pnl / o["rk"], 3), "why": ex[1]})
            a["log"] = a["log"][-500:]
            a["open"] = None
    if sig and not a["open"] and a["n"] < MAX_DAY:
        a["pending"] = {"s": sig[0], "stop": sig[1]}


def page(st):
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M PHT")
    cards, allt = "", []
    for k, a in sorted(st["acc"].items()):
        L = a["log"]
        allt += L
        n, w, nr = len(L), sum(1 for x in L if x["r"] > 0), sum(x["r"] for x in L)
        pts = [0]
        for x in L:
            pts.append(pts[-1] + x["r"])
        lo, span = min(pts), (max(pts) - min(pts)) or 1
        poly = " ".join(f"{i * 100 / max(len(pts) - 1, 1):.1f},{28 - (v - lo) * 26 / span:.1f}" for i, v in enumerate(pts))
        o = a["open"]
        op = f"OPEN {'long' if o['s'] > 0 else 'short'} @ {o['ent']:.2f}" if o else "flat"
        sym, r = k.split("|")
        cards += f"<div class=c><b>{sym} · {r}R target</b><small>{op}</small><p>${a['eq']:,.0f} · {n} trades · win {100 * w / n if n else 0:.0f}% · net {nr:+.1f}R · avg {nr / n if n else 0:+.2f}R</p><svg viewBox='0 0 100 30' preserveAspectRatio=none><polyline points='{poly}' fill=none stroke=#4a90d9 stroke-width=1.5 vector-effect=non-scaling-stroke /></svg></div>"
    rows = "".join(
        f"<tr><td>{datetime.fromtimestamp(x['t1'] / 1000, timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')}</td><td>{x['sym'][:3]} {x['side']}</td><td>{x['arm']}R</td><td>{x['r']:+.2f}R</td><td>{x['why']}</td></tr>"
        for x in sorted(allt, key=lambda x: -x["t1"])[:20]
    )
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><meta http-equiv=refresh content=300><title>Paper trader</title>
<style>body{{font:15px system-ui;margin:12px;background:#fff;color:#111}}.c{{border:1px solid #8884;border-radius:10px;padding:10px;margin:8px 0}}small{{float:right;opacity:.7}}p{{margin:6px 0}}svg{{width:100%;height:40px}}table{{width:100%;border-collapse:collapse;font-size:13px}}td{{padding:4px;border-bottom:1px solid #8883}}@media(prefers-color-scheme:dark){{body{{background:#111;color:#eee}}}}</style></head><body>
<h3>Paper trader v4.0</h3><small>Updated {now} · data: {st.get('src', '-').split('/')[2]}</small>{cards}<h4>Last 20 closed trades</h4><table>{rows or '<tr><td>No closed trades yet</td></tr>'}</table>
<p><small>Simulation only. $10k virtual per account, fees 0.05% + slippage 0.02% per side, funding not included.</small></p></body></html>"""


def main():
    st = json.load(open("state.json")) if os.path.exists("state.json") else {"last": {}, "acc": {}}
    for sym in SYMS:
        for r in ARMS:
            st["acc"].setdefault(f"{sym}|{r}", {"eq": START_EQ, "day": 0, "n": 0, "open": None, "pending": None, "log": []})
        c5, st["src"] = kl(sym, "5m", 600)
        c15, _ = kl(sym, "15m", 200)
        c1, _ = kl(sym, "1h", 150)
        last = st["last"].get(sym)
        if last is not None:
            for i, c in enumerate(c5):
                if c[0] > last:
                    sig = signal(c5, c15, c1, i)
                    for r in ARMS:
                        step(st["acc"][f"{sym}|{r}"], sym, r, c, sig)
        st["last"][sym] = c5[-1][0]
    json.dump(st, open("state.json", "w"))
    open("index.html", "w").write(page(st))


if __name__ == "__main__":
    main()
