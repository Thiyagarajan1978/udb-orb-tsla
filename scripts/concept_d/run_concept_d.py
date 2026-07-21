"""Concept D initial test — July 2026 (2026-07-01..17), TSLA @100sh.

D = C1 entries filtered by 15m Supertrend-Fakeout direction (EmreKb port: ATR14 RMA,
mult 3.0, fakeout index limit 5, fakeout ATR mult 1.5, High/Low type), C1 ATR exits.
At each 5m signal-bar close the gate uses the last COMPLETED 15m bar's trend.
Strict skip semantics; reversal legs re-check the gate at their own trigger.
Baseline C1 side by side. Also prints the July 15m trend segments for TV eyeballing.
"""
import sys, datetime as dt
from collections import Counter
from pathlib import Path

REPO = Path(r"C:\Users\TT\udb-orb-tsla")
sys.path.insert(0, str(REPO / "src"))
import numpy as np
import pandas as pd
from udb_orb.config import load_config
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

QTY = 100
JUL = ("2026-07-01", "2026-07-17")


def supertrend_fakeout(df15, atr_len=14, mult=3.0, idx_limit=5, fk_mult=1.5):
    """Faithful port of [EmreKb] Supertrend Fakeout (High/Low type)."""
    h, l, c = df15["high"].values, df15["low"].values, df15["close"].values
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr, index=df15.index).ewm(alpha=1 / atr_len, adjust=False).mean().values

    n = len(df15)
    trend = np.ones(n, dtype=int)
    dn_prev = up_prev = np.nan
    tr_prev = 1
    fk = None                      # (bar_index, st_line)
    for i in range(n):
        dn = c[i] - atr[i] * mult
        up = c[i] + atr[i] * mult
        if i > 0:
            if tr_prev == 1 and dn_prev > dn:
                dn = dn_prev
            if tr_prev == -1 and up_prev < up:
                up = up_prev
        t = tr_prev
        # block 1: uptrend breach below dnLine
        if t == 1 and l[i] < dn:
            if fk is None:
                fk = (i, up)
            elif i - fk[0] > idx_limit or (dn - l[i]) > atr[i] * fk_mult:
                t = -1
                up = fk[1]
                fk = None
        # block 2: downtrend breach above upLine
        if t == -1 and h[i] > up:
            if fk is None:
                fk = (i, dn)
            elif i - fk[0] > idx_limit or (h[i] - up) > atr[i] * fk_mult:
                t = 1
                dn = fk[1]
                fk = None
        # cancel a pending fakeout once price is back on the right side
        if fk is not None:
            if t == 1 and l[i] >= dn:
                fk = None
            if t == -1 and h[i] <= up:
                fk = None
        trend[i] = t
        tr_prev, dn_prev, up_prev = t, dn, up
    return pd.Series(trend, index=df15.index)


# ---- bars, 15m supertrend, 5m mapping ----
bars = rth_only(fetch_5min("TSLA", dt.date(2025, 10, 1), dt.date(2026, 7, 17),
                           cache_dir=REPO / "data" / "cache"))
df15 = bars.resample("15min").agg(open=("open", "first"), high=("high", "max"),
                                  low=("low", "min"), close=("close", "last")).dropna()
st15 = supertrend_fakeout(df15)

# gate value for a 5m bar (index = bar START): last 15m bar whose CLOSE (start+15m)
# <= this 5m bar's close (start+5m)  <=>  15m start <= 5m start - 10min
starts15 = df15.index
pos = starts15.searchsorted(bars.index - pd.Timedelta(minutes=10), side="right") - 1
htf = pd.Series(np.where(pos >= 0, st15.values[np.clip(pos, 0, None)], 0), index=bars.index)

# July 15m trend segments (for eyeballing vs TradingView)
jul15 = st15[(st15.index >= JUL[0]) & (st15.index <= JUL[1] + " 23:59")]
print("=== 15m Supertrend-Fakeout trend segments, July 2026 ===")
chg = jul15[jul15.ne(jul15.shift())]
for ts, v in chg.items():
    print(f"  {ts:%m-%d %H:%M} -> {'UP' if v == 1 else 'DOWN'}")

# ---- run C1 and D ----
cfg = load_config(REPO / "config" / "tsla_config_C1.yaml")
params = Params.from_config(cfg)
runs = {}
runs["C1"] = run_engine(bars.copy(), params, cfg["enhancements"])
enh_d = dict(cfg["enhancements"])
enh_d["htf_trend_filter"] = {"enabled": True, "series": htf, "mode": "strict"}
runs["D"] = run_engine(bars.copy(), params, enh_d)

print(f"\n=== July 2026 ({JUL[0]}..{JUL[1]}) @ {QTY} shares ===")
print(f"{'':<4}{'n':>4}{'net':>10}{'win %':>8}{'PF':>7}{'worst d':>10}{'best d':>10}")
res = {}
for name, r in runs.items():
    T = [t for t in r.trades if JUL[0] <= t.day <= JUL[1]]
    res[name] = T
    w = [t.pnl_total for t in T if t.pnl_total > 0]
    lo = [t.pnl_total for t in T if t.pnl_total <= 0]
    dp = Counter()
    for t in T:
        dp[t.day] += t.pnl_total
    pf = (sum(w) / -sum(lo)) if lo and sum(lo) < 0 else float("inf")
    wr = 100 * len(w) / len(T) if T else 0
    print(f"{name:<4}{len(T):>4}{sum(t.pnl_total for t in T) * QTY:>+10,.0f}{wr:>8.1f}{pf:>7.2f}"
          f"{min(dp.values()) * QTY if dp else 0:>+10,.0f}{max(dp.values()) * QTY if dp else 0:>+10,.0f}")

print("\n=== trade-by-trade (July) ===")
c1map = {(t.day, t.direction, t.entry_ts): t for t in res["C1"]}
dmap = {(t.day, t.direction, t.entry_ts): t for t in res["D"]}
alldays = sorted({t.day for t in res["C1"]} | {t.day for t in res["D"]})
print(f"{'day':<12}{'C1 trade':<34}{'C1 $':>8}   {'D trade':<34}{'D $':>8}")
for day in alldays:
    c1t = [t for t in res["C1"] if t.day == day]
    dt_ = [t for t in res["D"] if t.day == day]
    rows = max(len(c1t), len(dt_), 1)
    for i in range(rows):
        a = c1t[i] if i < len(c1t) else None
        b = dt_[i] if i < len(dt_) else None
        fa = f"{a.direction:<8}{a.entry_ts:%H:%M}->{a.exit_ts:%H:%M} {a.reason:<11}" if a else "(no trade)"
        fb = f"{b.direction:<8}{b.entry_ts:%H:%M}->{b.exit_ts:%H:%M} {b.reason:<11}" if b else "(skipped)"
        va = f"{a.pnl_total * QTY:>+8,.0f}" if a else f"{'':>8}"
        vb = f"{b.pnl_total * QTY:>+8,.0f}" if b else f"{'':>8}"
        print(f"{day if i == 0 else '':<12}{fa:<34}{va}   {fb:<34}{vb}")
