"""Quality levers #2 and #3 (shares, A1 + B1, train 2024-25 / holdout 2026, @100sh).

#2 ATR-scaled stop cap: sl_mode "Candle High/Low + ATR Cap", atr_mult swept, vs the
   adopted fixed $5 cap. Train picks the mult, holdout judges it.
#3 Vol-tiered sizing: rvol20 quintiles from TRAIN days only; Q4 (60-80th pct) at half
   size (Q5 is already skipped by the adopted vol gate = its threshold IS the 80th pct).
   Applied as exact post-hoc day-P&L scaling (all legs scale proportionally).
"""
import copy, sys, datetime as dt
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
bars = rth_only(fetch_5min("TSLA", dt.date(2023, 10, 1), dt.date(2026, 7, 17),
                           cache_dir=REPO / "data" / "cache"))
PROFILES = [("A1", "config/tsla_best_A.yaml"), ("B1", "config/tsla_best_B.yaml")]


def stats(trades, y0, y1, scale=None):
    T = [t for t in trades if y0 <= t.day[:4] <= y1]
    if not T:
        return dict(n=0, net=0, pf=0, wr=0, worst=0)
    mult = (lambda d: scale.get(d, 1.0)) if scale else (lambda d: 1.0)
    pnl = [t.pnl_total * mult(t.day) for t in T]
    w = [x for x in pnl if x > 0]
    lo = [x for x in pnl if x <= 0]
    dp = Counter()
    for t, x in zip(T, pnl):
        dp[t.day] += x
    return dict(n=len(T), net=sum(pnl) * QTY, pf=(sum(w) / -sum(lo)) if lo else float("inf"),
                wr=100 * len(w) / len(T), worst=min(dp.values()) * QTY)


def prow(tag, s):
    print(f"  {tag:<26} n={s['n']:>4} net={s['net']:>+9,.0f} wr={s['wr']:>5.1f} pf={s['pf']:>5.2f} worst={s['worst']:>+8,.0f}", flush=True)


# ---- #2 ATR stop cap ----
print("=== #2 ATR-scaled stop cap (train 2024-25 -> holdout 2026) ===", flush=True)
for prof, cfgp in PROFILES:
    base_cfg = load_config(REPO / cfgp)
    print(f"\n{prof} (fixed cap ${base_cfg['profile']['fixed_sl']}):", flush=True)
    base = run_engine(bars.copy(), Params.from_config(base_cfg), base_cfg["enhancements"]).trades
    prow("fixed (adopted)  TRAIN", stats(base, "2024", "2025"))
    prow("fixed (adopted)  HOLD", stats(base, "2026", "2026"))
    for m in (0.30, 0.35, 0.40, 0.45, 0.50):
        cfg = copy.deepcopy(base_cfg)
        cfg["profile"]["sl_mode"] = "Candle High/Low + ATR Cap"
        cfg["profile"]["atr_mult"] = m
        tr = run_engine(bars.copy(), Params.from_config(cfg), cfg["enhancements"]).trades
        st_t, st_h = stats(tr, "2024", "2025"), stats(tr, "2026", "2026")
        prow(f"ATR {m:.2f}x  TRAIN", st_t)
        prow(f"ATR {m:.2f}x  HOLD", st_h)

# ---- #3 vol-tiered sizing ----
print("\n=== #3 Vol-tiered sizing (Q4 half size; quintiles fit on TRAIN only) ===", flush=True)
daily = bars.groupby(bars.index.date).agg(cl=("close", "last"))
rvol = (daily["cl"].pct_change().rolling(20).std() * 100.0).shift(1)
rvol.index = [str(d) for d in rvol.index]
train_days = {d: v for d, v in rvol.items() if "2024-01-01" <= d <= "2025-12-31" and v == v}
qs = np.percentile(list(train_days.values()), [20, 40, 60, 80])
print(f"train rvol20 quintile edges: {np.round(qs, 2)} (adopted gate=4.92 ~= Q80 {qs[3]:.2f})", flush=True)

def tier_scale(q4_mult):
    return {d: (q4_mult if qs[2] < v <= qs[3] else 1.0) for d, v in rvol.items() if v == v}

for prof, cfgp in PROFILES:
    cfg = load_config(REPO / cfgp)
    tr = run_engine(bars.copy(), Params.from_config(cfg), cfg["enhancements"]).trades
    print(f"\n{prof}:", flush=True)
    prow("flat size   TRAIN", stats(tr, "2024", "2025"))
    prow("flat size   HOLD", stats(tr, "2026", "2026"))
    for q4 in (0.5, 0.75):
        sc = tier_scale(q4)
        prow(f"Q4 x{q4}   TRAIN", stats(tr, "2024", "2025", scale=sc))
        prow(f"Q4 x{q4}   HOLD", stats(tr, "2026", "2026", scale=sc))
