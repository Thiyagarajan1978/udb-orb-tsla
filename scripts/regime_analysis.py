#!/usr/bin/env python
"""Is the strategy's edge regime-dependent, and is the regime knowable BEFORE entry?

Builds daily features using ONLY information available at 09:35 ET (prior sessions + the
opening-range bar), joins them to each day's realised P&L, and asks:
  1. How do 2024 (PF 1.07) and 2026 (PF 1.81) actually differ?
  2. Does any pre-entry feature separate profitable days from losing ones?

A feature computed from the full session (e.g. close-to-open efficiency) is USELESS as a filter
even if it correlates perfectly — it isn't known when the trade is placed. Only the pre-entry
columns below qualify.

Usage:  python scripts/regime_analysis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.data.fmp_client import rth_only  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402
from udb_orb.engine.orb_engine import run_engine  # noqa: E402
from udb_orb.engine.params import Params  # noqa: E402


def build_features(bars: pd.DataFrame, market_open) -> pd.DataFrame:
    d = bars.groupby(bars.index.date).agg(O=("open", "first"), H=("high", "max"),
                                          L=("low", "min"), C=("close", "last"),
                                          V=("volume", "sum"))
    d.index = pd.to_datetime(d.index)
    # opening-range bar (09:30) high/low/volume
    ob = bars[[t == market_open for t in bars.index.time]]
    orb = pd.DataFrame({
        "or_high": ob["high"].values, "or_low": ob["low"].values, "or_vol": ob["volume"].values,
    }, index=pd.to_datetime([t.date() for t in ob.index]))
    d = d.join(orb, how="left")
    d["or_width"] = d.or_high - d.or_low

    prev_c = d.C.shift(1)
    tr = pd.concat([d.H - d.L, (d.H - prev_c).abs(), (d.L - prev_c).abs()], axis=1).max(axis=1)
    ret = d.C.pct_change()

    f = pd.DataFrame(index=d.index)
    # --- PRE-ENTRY features (known by 09:35) ---
    f["atr14_prior"] = tr.rolling(14).mean().shift(1)
    f["rvol20_prior"] = ret.rolling(20).std().shift(1) * 100          # % daily vol
    f["trend20_prior"] = (prev_c / d.C.shift(21) - 1) * 100           # prior 20d return %
    f["absTrend20_prior"] = f.trend20_prior.abs()
    ma20 = d.C.rolling(20).mean().shift(1)
    f["dist_ma20_prior"] = (prev_c - ma20) / ma20 * 100
    f["gap_pct"] = (d.O - prev_c) / prev_c * 100
    f["absGap_pct"] = f.gap_pct.abs()
    f["or_width"] = d.or_width
    f["or_width_pct"] = d.or_width / d.O * 100
    f["or_over_atr"] = d.or_width / f.atr14_prior
    f["or_rvol"] = d.or_vol / d.or_vol.rolling(20).mean().shift(1)
    # --- POST-HOC (not usable as a filter; shown for diagnosis only) ---
    f["day_range_pct"] = (d.H - d.L) / d.O * 100
    f["efficiency"] = (d.C - d.O).abs() / (d.H - d.L)                 # trend-day-ness
    f["day_move_pct"] = (d.C - d.O) / d.O * 100
    return f


def main():
    cfg = load_config()
    p = Params.from_config(cfg)
    with Database(db_path(cfg)) as db:
        bars = rth_only(db.load_bars(cfg["symbol"]))

    feats = build_features(bars, p.market_open)
    res = run_engine(bars, p, cfg["enhancements"])
    day_net = {pd.Timestamp(d.date): d.day_net for d in res.days if d.has_trades}
    df = feats.copy()
    df["net"] = pd.Series(day_net)
    df["year"] = df.index.year
    df = df.dropna(subset=["net"])

    PRE = ["atr14_prior", "rvol20_prior", "absTrend20_prior", "dist_ma20_prior",
           "absGap_pct", "or_width", "or_width_pct", "or_over_atr", "or_rvol"]
    POST = ["day_range_pct", "efficiency", "day_move_pct"]

    print("=== 1) How do the years differ? (mean per traded day) ===")
    cols = PRE + POST + ["net"]
    print(df.groupby("year")[cols].mean().round(3).to_string())

    print("\n=== 2) Correlation of each feature with that day's P&L ===")
    print("(PRE-ENTRY = usable as a filter | POST-HOC = known only at the close)")
    cor = df[cols].corr()["net"].drop("net").sort_values(key=abs, ascending=False)
    for k, v in cor.items():
        tag = "PRE-ENTRY" if k in PRE else "POST-HOC "
        print(f"  {tag}  {k:<20}{v:+.3f}")

    print("\n=== 3) Do pre-entry features separate good days from bad? (quintiles) ===")
    for c in ["or_over_atr", "rvol20_prior", "absTrend20_prior", "or_rvol", "absGap_pct"]:
        sub = df.dropna(subset=[c]).copy()
        if sub[c].nunique() < 5:
            continue
        sub["q"] = pd.qcut(sub[c], 5, labels=["Q1 low", "Q2", "Q3", "Q4", "Q5 high"], duplicates="drop")
        g = sub.groupby("q", observed=True)["net"].agg(["count", "mean", "sum"])
        print(f"\n  -- {c} --")
        print("   " + g.round(3).to_string().replace("\n", "\n   "))


if __name__ == "__main__":
    main()
