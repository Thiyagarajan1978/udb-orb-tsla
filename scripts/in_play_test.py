"""Is the ORB edge a property of TSLA, or of whichever symbol is IN PLAY that day?

`multi_symbol_test.py` showed the frozen profiles lose on 8 of 9 non-TSLA symbols, with every one
of them landing at PF 0.84-1.04 (breakeven noise) while TSLA sits at 1.23-1.28. Two readings fit:

  (a) TSLA is genuinely special -> nothing transfers, trade TSLA only.
  (b) ORB needs a stock that is IN PLAY (gapping, unusual opening volume), and TSLA is simply the
      in-play name most days -> the edge transfers to a SCANNER, not to a fixed second symbol.

(b) is testable with the data already cached. For each session, score every symbol on information
known by 09:35 -- the overnight gap and the opening-range bar's relative volume -- then trade only
the top-ranked symbol that day. If (b) holds, daily selection should beat both the average fixed
symbol and a random pick, and should approach or beat always-TSLA.

NO-LOOKAHEAD: both scores are known once the 09:30-09:35 OR bar closes. The earliest possible entry
is the NEXT bar (a buffered close-break of the OR), so nothing here can see its own outcome. The
selection is also made per DAY, never per trade.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.config import load_config                      # noqa: E402
from udb_orb.data.fmp_client import rth_only                # noqa: E402
from udb_orb.engine.orb_engine import run_engine            # noqa: E402
from udb_orb.engine.params import Params                    # noqa: E402

CACHE = ROOT / "data" / "cache"
PROFILES = {"B1": "config/tsla_best_B.yaml", "C1": "config/tsla_config_C1.yaml"}
NOTIONAL = 10_000.0
ATR_NORM = {"enabled": True, "stop": True, "gate": True, "rev": True,
            "stop_mult": 0.40, "gate_mult": 0.55, "rev_mult": 0.40}


def load_symbol(sym, start, end):
    files = sorted(CACHE.glob(f"{sym}_5min_*.parquet"), key=lambda p: p.stat().st_size)
    if not files:
        return pd.DataFrame()
    df = rth_only(pd.read_parquet(files[-1]))
    return df[(df.index >= start) & (df.index <= end + " 23:59:59")]


def daily_scores(bars: pd.DataFrame) -> pd.DataFrame:
    """Per-session in-play scores, all known by 09:35 (the OR bar close)."""
    g = bars.groupby(bars.index.date)
    d = pd.DataFrame({
        "open": g["open"].first(),          # 09:30 open
        "close": g["close"].last(),
        "or_vol": g["volume"].first(),      # 09:30-09:35 bar volume
        "or_rng": g["high"].first() - g["low"].first(),
    })
    d["prev_close"] = d["close"].shift(1)
    d["gap_pct"] = 100 * (d["open"] - d["prev_close"]).abs() / d["prev_close"]
    # relative opening volume vs this symbol's own trailing norm -> comparable ACROSS symbols
    d["or_rvol"] = d["or_vol"] / d["or_vol"].shift(1).rolling(20, min_periods=5).median()
    d["or_rng_pct"] = 100 * d["or_rng"] / d["open"]
    return d


def daily_pnl(sym, bars, profile) -> pd.DataFrame:
    """Per-session return at a fixed $10k notional, sized off that day's open."""
    cfg = load_config(ROOT / PROFILES[profile])
    price = float(bars["close"].mean())
    cfg["profile"]["slippage_per_unit"] = 0.10 * price / 300.0     # price-scaled, as in pass 1
    enh = dict(cfg.get("enhancements", {}))
    enh["atr_normalize"] = ATR_NORM
    res = run_engine(bars, Params.from_config(cfg), enh)

    opens = bars.groupby(bars.index.date)["open"].first()
    rows = {}
    for t in res.trades:
        day = pd.Timestamp(t.day).date() if not isinstance(t.day, str) else pd.Timestamp(t.day).date()
        rows[day] = rows.get(day, 0.0) + t.pnl_total
    s = pd.Series(rows, dtype=float)
    if s.empty:
        return pd.DataFrame(columns=["ret_pct"])
    shares = NOTIONAL / opens.reindex(s.index)
    return pd.DataFrame({"ret_pct": 100 * s * shares / NOTIONAL})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="TSLA,NVDA,AMD,META,AMZN,GOOGL,MSFT,NFLX")
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2026-07-09")
    ap.add_argument("--top", type=int, default=1, help="how many in-play symbols to trade per day")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",")]
    bars = {s: load_symbol(s, args.start, args.end) for s in syms}
    bars = {s: b for s, b in bars.items() if not b.empty}
    scores = {s: daily_scores(b) for s, b in bars.items()}

    for prof in PROFILES:
        pnl = {s: daily_pnl(s, b, prof) for s, b in bars.items()}
        ret = pd.DataFrame({s: p["ret_pct"] for s, p in pnl.items()}).sort_index()

        print(f"\n{'='*78}\n=== {prof} — in-play selection vs fixed symbol ({args.start}..{args.end})\n{'='*78}")

        # --- benchmarks -------------------------------------------------
        fixed = ret.sum().sort_values(ascending=False)
        non_tsla = fixed.drop("TSLA", errors="ignore")
        print(f"  always-TSLA            : {fixed.get('TSLA', float('nan')):+8.1f}%")
        print(f"  best fixed non-TSLA    : {non_tsla.idxmax():<6} {non_tsla.max():+8.1f}%")
        print(f"  mean fixed non-TSLA    : {non_tsla.mean():+8.1f}%")
        print(f"  equal-weight all 8     : {ret.mean(axis=1).sum():+8.1f}%   "
              f"(diversified, no selection)")

        # --- selection rules --------------------------------------------
        for name, col in [("gap %", "gap_pct"), ("OR rvol", "or_rvol"), ("OR range %", "or_rng_pct")]:
            sc = pd.DataFrame({s: scores[s][col] for s in bars}).reindex(ret.index)
            # rank each day, take the top-N symbols, average their returns
            picks = sc.rank(axis=1, ascending=False, method="first") <= args.top
            sel = (ret.where(picks)).mean(axis=1)
            hit = ret.where(picks).notna().sum(axis=1).gt(0)
            tsla_share = 100 * picks["TSLA"].sum() / picks.any(axis=1).sum() if "TSLA" in picks else 0
            print(f"  top-{args.top} by {name:<11}: {sel.sum():+8.1f}%   "
                  f"({int(hit.sum())} traded days, TSLA picked {tsla_share:.0f}% of days)")

        # --- random-selection null --------------------------------------
        rng = np.random.default_rng(7)
        nulls = []
        cols = list(ret.columns)
        for _ in range(500):
            pick = rng.integers(0, len(cols), size=len(ret))
            nulls.append(np.nansum(ret.to_numpy()[np.arange(len(ret)), pick]))
        nulls = np.array(nulls)
        print(f"  random pick (500 draws): {nulls.mean():+8.1f}%  "
              f"[5th {np.percentile(nulls,5):+.1f}, 95th {np.percentile(nulls,95):+.1f}]")


if __name__ == "__main__":
    main()
