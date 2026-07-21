"""Pre-market high/low FAILED-RECLAIM fade ("concept E", friend's video strategy).

Rules (5m approximation of the video's 2m chart — FMP plan has no 1-2m data):
  PMH/PML = high/low of the 04:00-09:25 extended session.
  SHORT: a 5m bar CLOSES above PMH (reclaim, >=5 min hold by construction), then a later
         bar CLOSES back below PMH before 12:00 -> short at that close.
         stop = swing high of the reclaim episode; target = PML; else EOD 15:50 close.
  LONG (mirror): close below PML then close back above -> long, stop = swing low,
         target = PMH.
  First signal per day only. 1 unit, $0.10 slippage. Variant B requires the reclaim to
  hold >= 2 consecutive closes (~10 min) before the failure counts.
Data: FMP 5m extended=true, cached to data/cache/TSLA_5min_ext_2025_2026.parquet.
"""
import os, re, sys, datetime as dt
from collections import Counter
from pathlib import Path

REPO = Path(r"C:\Users\TT\udb-orb-tsla")
sys.path.insert(0, str(REPO / "src"))
import pandas as pd
import requests

CACHE = REPO / "data" / "cache" / "TSLA_5min_ext_2025_2026.parquet"
QTY = 100
SLIP = 0.10


def fetch_extended():
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    key = None
    for line in open(REPO / ".env"):
        m = re.match(r'\s*FMP_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
        if m:
            key = m.group(1)
    rows = []
    cur = dt.date(2025, 1, 1)
    end = dt.date(2026, 7, 17)
    while cur <= end:
        nxt = min(cur + dt.timedelta(days=4), end)
        r = requests.get("https://financialmodelingprep.com/stable/historical-chart/5min",
                         params={"symbol": "TSLA", "apikey": key, "extended": "true",
                                 "from": cur.isoformat(), "to": nxt.isoformat()}, timeout=30)
        js = r.json()
        if isinstance(js, list):
            rows.extend(js)
        cur = nxt + dt.timedelta(days=1)
    df = pd.DataFrame(rows).drop_duplicates("date")
    df["ts"] = pd.to_datetime(df["date"]).dt.tz_localize("America/New_York")
    df = df.set_index("ts").sort_index()[["open", "high", "low", "close", "volume"]].astype(float)
    df.to_parquet(CACHE)
    return df


bars = fetch_extended()
print(f"extended bars: {len(bars)}  {bars.index.min()} .. {bars.index.max()}", flush=True)


def run(hold_bars=1):
    trades = []
    for day, g in bars.groupby(bars.index.date):
        pre = g[(g.index.time >= dt.time(4, 0)) & (g.index.time < dt.time(9, 30))]
        rth = g[(g.index.time >= dt.time(9, 30)) & (g.index.time <= dt.time(15, 55))]
        if len(pre) < 6 or len(rth) < 30:
            continue
        pmh, pml = pre["high"].max(), pre["low"].min()
        state = 0          # 0 idle, +1 above-PMH episode, -1 below-PML episode
        held = 0
        swing = None
        trade = None
        for ts, row in rth.iterrows():
            c, h, l = row["close"], row["high"], row["low"]
            if trade is None:
                if ts.time() > dt.time(12, 0):
                    break
                if state == 0:
                    if c > pmh:
                        state, held, swing = 1, 1, h
                    elif c < pml:
                        state, held, swing = -1, 1, l
                elif state == 1:
                    swing = max(swing, h)
                    if c > pmh:
                        held += 1
                    elif c < pmh:
                        if held >= hold_bars:
                            trade = dict(day=str(day), dirn=-1, entry_ts=ts, entry=c,
                                         stop=swing, target=pml)
                        else:
                            state, held, swing = 0, 0, None
                            if c < pml:
                                state, held, swing = -1, 1, l
                elif state == -1:
                    swing = min(swing, l)
                    if c < pml:
                        held += 1
                    elif c > pml:
                        if held >= hold_bars:
                            trade = dict(day=str(day), dirn=1, entry_ts=ts, entry=c,
                                         stop=swing, target=pmh)
                        else:
                            state, held, swing = 0, 0, None
                            if c > pmh:
                                state, held, swing = 1, 1, h
            else:
                d = trade["dirn"]
                # stop first (conservative), fill at stop level; target fills at level
                if (d == -1 and h >= trade["stop"]) or (d == 1 and l <= trade["stop"]):
                    trade.update(exit_ts=ts, exit=trade["stop"], reason="Stop")
                    break
                if (d == -1 and l <= trade["target"]) or (d == 1 and h >= trade["target"]):
                    trade.update(exit_ts=ts, exit=trade["target"], reason="Target")
                    break
                if ts.time() >= dt.time(15, 50):
                    trade.update(exit_ts=ts, exit=c, reason="EOD")
                    break
        if trade and "exit" in trade:
            pnl = (trade["entry"] - trade["exit"]) * trade["dirn"] * -1
            pnl = (trade["exit"] - trade["entry"]) * trade["dirn"]
            trade["pnl"] = pnl - SLIP
            trades.append(trade)
    return trades


for hold in (1, 2):
    tr = run(hold)
    print(f"\n=== hold >= {hold} bar(s) (~{hold*5} min) ===", flush=True)
    for year in ("2025", "2026"):
        T = [t for t in tr if t["day"].startswith(year)]
        if not T:
            continue
        w = [t["pnl"] for t in T if t["pnl"] > 0]
        lo = [t["pnl"] for t in T if t["pnl"] <= 0]
        dp = Counter()
        for t in T:
            dp[t["day"]] += t["pnl"]
        pf = sum(w) / -sum(lo) if lo else float("inf")
        rs = Counter(t["reason"] for t in T)
        print(f"{year}: n={len(T):3d} net={sum(w+lo)*QTY:>+9,.0f} wr={100*len(w)/len(T):4.1f}% "
              f"pf={pf:4.2f} worst_day={min(dp.values())*QTY:>+8,.0f} exits={dict(rs)}", flush=True)

# a peek at recent trades for sanity
tr = run(1)
print("\nlast 8 trades (hold>=1):", flush=True)
for t in tr[-8:]:
    print(f"  {t['day']} {'L' if t['dirn']==1 else 'S'} in {t['entry_ts'].strftime('%H:%M')}@{t['entry']:.2f} "
          f"out {t['exit_ts'].strftime('%H:%M')}@{t['exit']:.2f} {t['reason']:<6} {t['pnl']*QTY:>+8,.0f}", flush=True)
