"""Options-chain flow pilot (2026): does PRIOR-DAY chain volume predict ORB day quality?

Pulls TSLA near-dated (exp <= +7d) near-money (strike within +-8% of month range)
per-contract DAILY volume (OPRA ohlcv-1d) for 2026-01..2026-07, caches it, builds
prior-day features, and tests them against B1's actual day P&L:
  pc_ratio    prior-day put/call volume ratio (near-dated)
  skew        vol-weighted strike center vs prior close (above=call-tilted)
  wall_dist   distance from prior close to the max-volume strike (%)
  wall_side   is the wall above or below prior close (break toward/away test)
  dte0_share  share of prior-day volume in contracts expiring THAT prior day (pin proxy)
All features use ONLY day D-1 data for day D — no lookahead.
"""
import os, re, sys, datetime as dt
from pathlib import Path

REPO = Path(r"C:\Users\TT\udb-orb-tsla")
sys.path.insert(0, str(REPO / "src"))
import numpy as np
import pandas as pd
from udb_orb.config import load_config
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

CACHE = REPO / "data" / "cache" / "opra" / "chain_vol_2026.parquet"


def get_key():
    k = os.getenv("DATABENTO_API_KEY")
    if k:
        return k
    for p in (REPO / ".env", Path(r"C:\Users\TT\gap_analyzer\.env")):
        try:
            for line in open(p):
                m = re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
                if m:
                    return m.group(1)
        except FileNotFoundError:
            pass
    sys.exit("no key")


def parse_osi(sym):
    b = sym[-15:]
    return dt.date(2000 + int(b[0:2]), int(b[2:4]), int(b[4:6])), b[6], int(b[7:15]) / 1000


# ---- pull (cached) ----
if CACHE.exists():
    chain = pd.read_parquet(CACHE)
    print(f"chain volume from cache: {len(chain)} rows", flush=True)
else:
    defs = pd.concat([pd.read_parquet(REPO / "data/cache/opra/defs_full.parquet"),
                      pd.read_parquet(REPO / "data/cache/opra/defs_july.parquet")],
                     ignore_index=True).drop_duplicates("symbol")
    defs["exp"] = pd.to_datetime(defs["exp"]).dt.date
    bars_px = rth_only(fetch_5min("TSLA", dt.date(2025, 12, 1), dt.date(2026, 7, 17),
                                  cache_dir=REPO / "data" / "cache"))
    daily_px = bars_px.groupby(bars_px.index.date)["close"].last()

    import databento as dbnt
    cl = dbnt.Historical(get_key())
    frames = []
    spent = 0.0
    for mon in pd.period_range("2026-01", "2026-07", freq="M"):
        ms, me = mon.start_time.date(), min(mon.end_time.date(), dt.date(2026, 7, 17))
        idx = pd.to_datetime(pd.Series(daily_px.index.astype(str))).dt.date
        px = daily_px[((idx >= ms) & (idx <= me)).values]
        if px.empty:
            continue
        lo, hi = px.min() * 0.92, px.max() * 1.08
        sub = defs[(defs.exp >= ms) & (defs.exp <= me + dt.timedelta(days=7))
                   & (defs.strike >= lo) & (defs.strike <= hi)]
        syms = sorted(sub.symbol.unique())
        for i in range(0, len(syms), 1900):
            batch = syms[i:i + 1900]
            c = cl.metadata.get_cost(dataset="OPRA.PILLAR", symbols=batch, stype_in="raw_symbol",
                                     schema="ohlcv-1d", start=ms.isoformat(),
                                     end=(me + dt.timedelta(days=1)).isoformat())
            spent += c
            df = cl.timeseries.get_range(dataset="OPRA.PILLAR", symbols=batch, stype_in="raw_symbol",
                                         schema="ohlcv-1d", start=ms.isoformat(),
                                         end=(me + dt.timedelta(days=1)).isoformat()).to_df().reset_index()
            frames.append(df[["ts_event", "symbol", "volume"]])
            print(f"  {mon} batch {i//1900}: {len(batch)} syms, {len(df)} rows, ${c:.2f}", flush=True)
    chain = pd.concat(frames, ignore_index=True)
    chain.to_parquet(CACHE)
    print(f"pulled {len(chain)} rows, total cost ${spent:.2f} -> {CACHE}", flush=True)

# ---- features per day ----
chain["t"] = pd.to_datetime(chain["ts_event"], utc=True)
# ohlcv-1d bars are stamped 00:00 UTC of the SESSION date — use the UTC date directly.
# (An ET conversion shifts the label a day back and silently turns "prior-day" features
# into same-day lookahead — verified 2026-07-20, it fabricated a huge fake edge.)
chain["day"] = chain["t"].dt.date
osi = chain["symbol"].map(lambda s: parse_osi(s))
chain["exp"] = osi.map(lambda x: x[0])
chain["cp"] = osi.map(lambda x: x[1])
chain["strike"] = osi.map(lambda x: x[2])

bars = rth_only(fetch_5min("TSLA", dt.date(2025, 10, 1), dt.date(2026, 7, 17),
                           cache_dir=REPO / "data" / "cache"))
daily_close = bars.groupby(bars.index.date)["close"].last()

feat = {}
for day, g in chain.groupby("day"):
    close = daily_close.get(day)
    if close is None:
        continue
    cv = g[g.cp == "C"]["volume"].sum()
    pv = g[g.cp == "P"]["volume"].sum()
    tot = cv + pv
    if tot == 0:
        continue
    vws = (g["strike"] * g["volume"]).sum() / g["volume"].sum()
    bystrike = g.groupby("strike")["volume"].sum()
    wall = bystrike.idxmax()
    dte0 = g[g.exp == day]["volume"].sum()
    feat[str(day)] = dict(pc=pv / max(cv, 1), skew=(vws - close) / close * 100,
                          wall=(wall - close) / close * 100, dte0_share=dte0 / tot)
fdf = pd.DataFrame(feat).T.sort_index()
print(f"\nfeature days: {len(fdf)}", flush=True)

# ---- B1 trades ----
cfg = load_config(REPO / "config" / "tsla_best_B.yaml")
tr = [t for t in run_engine(bars.copy(), Params.from_config(cfg), cfg["enhancements"]).trades
      if t.day >= "2026-01-05"]
days = pd.DataFrame([dict(day=t.day, dirn=1 if t.direction.startswith("L") else -1,
                          is_rev=t.is_reversal, pnl=t.pnl_total * 100) for t in tr])
prim = days[~days.is_rev].groupby("day").agg(dirn=("dirn", "first")).reset_index()
dpnl = days.groupby("day")["pnl"].sum().reset_index()
D = prim.merge(dpnl, on="day")

# prior-day features -> trade day
fdf_shift = fdf.copy()
fdf_shift.index = pd.to_datetime(fdf_shift.index)
trade_days = pd.to_datetime(D["day"])
prev_map = {}
fidx = fdf_shift.index
for td in trade_days:
    pos = fidx.searchsorted(td) - 1
    if pos >= 0:
        prev_map[td.strftime("%Y-%m-%d")] = fdf_shift.iloc[pos]
F = pd.DataFrame(prev_map).T
D = D.set_index("day").join(F).dropna()
print(f"joined trade-days: {len(D)}", flush=True)

# ---- tests ----
def bucket_report(col, q=3):
    D["_b"] = pd.qcut(D[col], q, labels=False, duplicates="drop")
    g = D.groupby("_b")["pnl"].agg(["count", "mean", "sum"])
    print(f"\n{col} terciles (prior-day):\n{g.round(1)}", flush=True)
    print(f"  corr({col}, day_pnl) = {D[col].corr(D['pnl']):.3f}", flush=True)

for col in ("pc", "skew", "wall", "dte0_share"):
    bucket_report(col)

# direction-agreement tests
D["skew_agree"] = np.sign(D["skew"]) == D["dirn"]
D["wall_toward"] = np.sign(D["wall"]) == D["dirn"]
D["pc_agree"] = np.where(D["pc"] > 1.0, -1, 1) == D["dirn"]
for col in ("skew_agree", "wall_toward", "pc_agree"):
    g = D.groupby(col)["pnl"].agg(["count", "mean", "sum"])
    print(f"\n{col}:\n{g.round(1)}", flush=True)

# split-half check (Jan-Apr vs May-Jul)
D["half"] = np.where(D.index < "2026-05-01", "JanApr", "MayJul")
for col in ("skew_agree", "wall_toward", "pc_agree"):
    g = D.groupby(["half", col])["pnl"].mean()
    print(f"\n{col} by half:\n{g.round(1)}", flush=True)
