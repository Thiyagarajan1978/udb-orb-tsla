"""Quality lever #4 (options, A1 + B1, 2025-01..2026-07-17, ATM 0DTE, barclose fills).

a) Premium terciles: is the "cheap premium trades better" repo finding real under barclose?
b) Sizing: fixed 2 ct vs fixed-DOLLAR budget (contracts = floor($B / premium); 0 = skip).
c) Spread cost: entry at ask vs mid, exit at bid vs mid — what limit-order fills are worth.
All quotes from the local OPRA caches (no new pulls).
"""
import os, re, sys, datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(r"C:\Users\TT\udb-orb-tsla")
sys.path.insert(0, str(REPO / "src"))
import numpy as np
import pandas as pd
from udb_orb.config import load_config, db_path
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.db.database import Database
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

CACHE = REPO / "data" / "cache" / "opra"


def parse_osi(sym):
    b = sym[-15:]
    return dt.date(2000 + int(b[0:2]), int(b[2:4]), int(b[4:6])), b[6], int(b[7:15]) / 1000


with Database(db_path(load_config())) as dbx:
    old = rth_only(dbx.load_bars("TSLA"))
fresh = rth_only(fetch_5min("TSLA", old.index[-1].date() - dt.timedelta(days=3), dt.date(2026, 7, 17),
                            cache_dir=REPO / "data" / "cache"))
bars = pd.concat([old, fresh]).sort_index()
bars = bars[~bars.index.duplicated(keep="last")]

defs = pd.concat([pd.read_parquet(CACHE / "defs_full.parquet"),
                  pd.read_parquet(CACHE / "defs_july.parquet")], ignore_index=True).drop_duplicates("symbol")
defs["exp"] = pd.to_datetime(defs["exp"]).dt.date
defs["snap"] = pd.to_datetime(defs["snap"]).dt.date

def pick(day, price, cp):
    D = dt.date.fromisoformat(day)
    sub = defs[(defs.cp == cp) & (defs.exp >= D) & (defs.snap <= D)]
    if sub.empty:
        return None
    e = sub.exp.min()
    sub = sub[sub.exp == e]
    return sub.iloc[(sub.strike - price).abs().values.argmin()].symbol

qparts = []
for f in ("quotes_weekly.parquet", "quotes_full.parquet", "quotes_july.parquet",
          "quotes_barclose_fill_gaps.parquet"):
    p = CACHE / f
    if p.exists():
        qparts.append(pd.read_parquet(p))
q = pd.concat(qparts, ignore_index=True).drop_duplicates(["ts_event", "symbol"])
scale = 1e9 if q["ask_px_00"].abs().median() > 1e6 else 1.0
q["ask"] = q["ask_px_00"] / scale
q["bid"] = q["bid_px_00"] / scale
q["t"] = pd.to_datetime(q["ts_event"], utc=True)
q = q[(q.ask > 0) & (q.bid > 0)]
quotes = {s: g.sort_values("t").reset_index(drop=True) for s, g in q.groupby("symbol")}

def qrow(sym, ts):
    g = quotes.get(sym)
    if g is None or g.empty:
        return None
    tt = ts.tz_convert("UTC")
    i = g["t"].searchsorted(tt)
    c = g.iloc[max(0, i - 1):i + 1]
    if c.empty:
        return None
    r = c.iloc[(c["t"] - tt).abs().values.argmin()]
    return None if abs((r["t"] - tt).total_seconds()) > 900 else r

BC = pd.Timedelta(minutes=5)
rows = []
for prof, cfgp in (("A1", "config/tsla_best_A.yaml"), ("B1", "config/tsla_best_B.yaml")):
    cfg = load_config(REPO / cfgp)
    tr = [t for t in run_engine(bars.copy(), Params.from_config(cfg), cfg["enhancements"]).trades
          if "2025-01-01" <= t.day <= "2026-07-17"]
    for t in tr:
        cp = "C" if t.direction.startswith("L") else "P"
        s = pick(t.day, t.entry_price, cp)
        if not s:
            continue
        ein = qrow(s, t.entry_ts + BC)
        eout = qrow(s, t.exit_ts + BC)
        if ein is None or eout is None:
            continue
        rows.append(dict(prof=prof, day=t.day, year=t.day[:4],
                         ask_in=float(ein["ask"]), mid_in=(float(ein["ask"]) + float(ein["bid"])) / 2,
                         bid_out=float(eout["bid"]), mid_out=(float(eout["ask"]) + float(eout["bid"])) / 2))
L = pd.DataFrame(rows)
L.to_parquet(Path(__file__).parent / "options_quality_trades.parquet")
L["pnl_1ct"] = (L.bid_out - L.ask_in) * 100
L["pnl_mid_entry"] = (L.bid_out - L.mid_in) * 100          # limit fill at mid on entry only
L["pnl_mid_both"] = (L.mid_out - L.mid_in) * 100           # mid both sides (upper bound)
print(f"priced trades: {len(L)} ({dict(L.groupby('prof').size())})", flush=True)

def perf(x):
    w = x[x > 0]; lo = x[x <= 0]
    pf = w.sum() / -lo.sum() if len(lo) and lo.sum() < 0 else float("inf")
    return f"n={len(x):>3} net={x.sum():>+9,.0f} wr={100*len(w)/len(x):>5.1f} pf={pf:>5.2f}"

print("\n=== a) premium terciles (entry ask, per profile, 1 ct barclose) ===", flush=True)
for prof, g in L.groupby("prof"):
    g = g.copy()
    g["bucket"] = pd.qcut(g.ask_in, 3, labels=["cheap", "mid", "rich"])
    print(f"{prof}: premium tercile edges ${g.ask_in.quantile(1/3):.2f} / ${g.ask_in.quantile(2/3):.2f}", flush=True)
    for b, gg in g.groupby("bucket", observed=True):
        print(f"   {b:<6} avg_prem=${gg.ask_in.mean()*100:>4.0f}  {perf(gg.pnl_1ct)}", flush=True)

print("\n=== b) sizing: fixed 2 ct vs fixed-dollar budget (skip if 0 ct) ===", flush=True)
for prof, g in L.groupby("prof"):
    fixed2 = g.pnl_1ct * 2
    dp2 = g.assign(p=fixed2).groupby("day")["p"].sum()
    print(f"{prof} fixed 2ct : net={fixed2.sum():>+9,.0f}  worst_day={dp2.min():>+8,.0f}  "
          f"avg_prem_risk=${(g.ask_in*200).mean():,.0f}  max=${(g.ask_in*200).max():,.0f}", flush=True)
    for B in (700, 900, 1100):
        n_ct = np.floor(B / (g.ask_in * 100)).clip(upper=4)
        pnl = g.pnl_1ct * n_ct
        taken = n_ct > 0
        dpb = g.assign(p=pnl).groupby("day")["p"].sum()
        print(f"{prof} ${B} bdgt: net={pnl.sum():>+9,.0f}  worst_day={dpb.min():>+8,.0f}  "
              f"avg_ct={n_ct[taken].mean():.2f}  skipped={int((~taken).sum())}  "
              f"avg_prem_risk=${(g.ask_in*100*n_ct)[taken].mean():,.0f}", flush=True)

print("\n=== b2) year-split robustness: cheap-tercile PF and $-budget sizing ===", flush=True)
for prof, g in L.groupby("prof"):
    for yr, gy in g.groupby("year"):
        gy = gy.copy()
        gy["bucket"] = pd.qcut(gy.ask_in, 3, labels=["cheap", "mid", "rich"])
        ch = gy[gy.bucket == "cheap"]
        n_ct = np.floor(1100 / (gy.ask_in * 100)).clip(upper=4)
        print(f"{prof} {yr}: cheap-tercile {perf(ch.pnl_1ct)} | fixed2 net={float((gy.pnl_1ct*2).sum()):>+9,.0f}"
              f" | $1100bdgt net={float((gy.pnl_1ct*n_ct).sum()):>+9,.0f}", flush=True)

print("\n=== c) spread cost / limit-fill value (1 ct) ===", flush=True)
for prof, g in L.groupby("prof"):
    spread_in = ((g.ask_in - g.mid_in) * 100)
    print(f"{prof}: ask-vs-mid entry cost avg ${spread_in.mean():.0f}/trade, total ${spread_in.sum():,.0f} "
          f"over {len(g)} trades", flush=True)
    print(f"   market both sides : {perf(g.pnl_1ct)}", flush=True)
    print(f"   mid on ENTRY only : {perf(g.pnl_mid_entry)}", flush=True)
    print(f"   mid both sides    : {perf(g.pnl_mid_both)}", flush=True)
