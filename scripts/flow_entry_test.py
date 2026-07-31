#!/usr/bin/env python
"""Does the 09:30-09:31 order-flow imbalance work as an ORB entry signal?

  python scripts/flow_entry_test.py --start 2026-07-01 --end 2026-07-29
  python scripts/flow_entry_test.py --start 2023-08-01 --end 2026-07-29 --years

THE IDEA UNDER TEST (user, 2026-07-30): if the first RTH minute traded more BUY volume than
sell volume, go long; if more sell than buy, go short. Then manage the position with the C1
profile's exits, unchanged.

WHAT IS HELD FIXED vs REAL C1
  Only the ENTRY TRIGGER changes. Stop (OR boundary capped at $6), ATR take-profit, 25%
  partial, 0.55 BE retrace, $0.25 BE trail, VWAP-cross runner, close-triggered stop fills,
  $0.10 slippage, 15:50 EOD, the volatility-regime and max-OR-width day filters, and the
  reversal leg all come straight from config/tsla_config_C1.yaml.

ENTRY TIMING — why 09:35 and not 09:31
  The signal is complete at 09:31:00, but the C1 exit engine runs on 5-minute bars and its
  stop and BE level are defined off the 09:30-09:35 opening range. Entering at 09:31 while
  sizing the stop off a range that has not finished forming would be lookahead. So the trade
  is taken at the CLOSE of the 09:30 bar — 09:35:00, the first executable price at which the
  stop is actually knowable. `--drift` reports what those four minutes cost.

SIGNAL COLUMN
  Default `buy_pct_ex_cross_db` from data/reference/tsla_first_minute_*.csv. The opening
  auction is EXCLUDED: it averages 65% of that bar's volume, has no aggressor, and inverts
  the naive split (see that file's README). `--column buy_pct_db` reproduces the naive
  version for comparison.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

DEFAULT_SIGNALS = ROOT / "data" / "reference" / "tsla_first_minute_2023-07-31_2026-07-29.csv"


def load_signals(path: Path, column: str, threshold: float, fade: bool) -> pd.DataFrame:
    d = pd.read_csv(path, parse_dates=["date"])
    if column not in d.columns:
        raise SystemExit(f"no column {column!r}; have {[c for c in d.columns if 'buy' in c]}")
    d = d.dropna(subset=[column]).copy()
    d["signal"] = (d[column] > threshold).map({True: 1, False: -1})
    if fade:
        d["signal"] = -d["signal"]
    d["day"] = d["date"].dt.strftime("%Y-%m-%d")
    return d


def run(cfg, bars, signals: dict | None, allow_reversal: bool = True):
    from udb_orb.engine.orb_engine import run_engine
    from udb_orb.engine.params import Params
    from udb_orb.engine.metrics import summarize
    enh = dict(cfg.get("enhancements", {}))
    if signals is not None:
        enh["flow_entry"] = {"enabled": True, "signals": signals,
                             "allow_reversal": allow_reversal}
    res = run_engine(bars, Params.from_config(cfg), enh)
    return res, summarize(res)


def line(tag: str, s) -> str:
    pf = "  n/a" if s.profit_factor is None else f"{s.profit_factor:5.2f}"
    return (f"{tag:<22} {s.trades:>4}tr  WR {s.win_rate:5.1f}%  net {s.net_pnl:>9.2f}  "
            f"PF {pf}  exp {s.expectancy:>6.2f}  worst day {s.worst_day or 0:>7.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="09:30-09:31 order-flow entry, C1 exits")
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--end", default="2026-07-29")
    ap.add_argument("--config", default="config/tsla_config_C1.yaml")
    ap.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    ap.add_argument("--column", default="buy_pct_ex_cross_db")
    ap.add_argument("--threshold", type=float, default=50.0)
    ap.add_argument("--years", action="store_true", help="also break results down by year")
    ap.add_argument("--trades", action="store_true", help="print the per-trade table")
    ap.add_argument("--drift", action="store_true", help="what the 09:31->09:35 delay costs")
    args = ap.parse_args(argv)

    from udb_orb.config import load_config
    from udb_orb.backtest.runner import load_bars

    cfg = load_config(ROOT / args.config)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    bars = load_bars(cfg, start, end)
    print(f"{len(bars)} 5m bars  {bars.index.min()} .. {bars.index.max()}")

    sig = load_signals(Path(args.signals), args.column, args.threshold, fade=False)
    sig = sig[(sig.date.dt.date >= start) & (sig.date.dt.date <= end)]
    follow = dict(zip(sig.day, sig.signal))
    fade = {k: -v for k, v in follow.items()}
    nl = sum(1 for v in follow.values() if v == 1)
    print(f"{len(follow)} signal days from {args.column} (>{args.threshold} = long): "
          f"{nl} long, {len(follow) - nl} short\n")

    base_res, base = run(cfg, bars, None)
    fol_res, fol = run(cfg, bars, follow)
    fad_res, fad = run(cfg, bars, fade)
    fol_nr_res, fol_nr = run(cfg, bars, follow, allow_reversal=False)

    print("=" * 104)
    print(line("C1 baseline (ORB)", base))
    print(line("FLOW follow", fol))
    print(line("FLOW follow, no rev", fol_nr))
    print(line("FLOW fade (inverse)", fad))
    print("=" * 104)

    # Direction accuracy of the raw signal, independent of any exit logic: did the day close
    # in the signalled direction? This separates "the signal is wrong" from "the exits ate it".
    day_ohlc = bars.groupby(bars.index.date).agg(o=("open", "first"), c=("close", "last"))
    hits = tot = 0
    for k, v in follow.items():
        d0 = date.fromisoformat(k)
        if d0 in day_ohlc.index:
            r = day_ohlc.loc[d0]
            if (r.c - r.o) * v > 0:
                hits += 1
            tot += 1
    if tot:
        print(f"\nraw direction check: signal matched the day's open->close sign on "
              f"{hits}/{tot} days ({100 * hits / tot:.1f}%)")

    if args.drift:
        rows = []
        for k in follow:
            d0 = date.fromisoformat(k)
            g = bars[bars.index.date == d0]
            if len(g) >= 2:
                rows.append({"day": k, "sig": follow[k],
                             "px_0930_open": g.iloc[0]["open"], "px_0935": g.iloc[0]["close"]})
        dr = pd.DataFrame(rows)
        if len(dr):
            dr["drift"] = (dr.px_0935 - dr.px_0930_open) * dr.sig
            print(f"\n09:30 open -> 09:35 entry drift IN THE SIGNAL'S FAVOUR: "
                  f"mean {dr.drift.mean():+.3f}  median {dr.drift.median():+.3f}  "
                  f"({(dr.drift > 0).sum()}/{len(dr)} favourable)")

    if args.trades:
        rows = [{"day": t.day, "dir": t.direction, "entry": round(t.entry_price, 2),
                 "exit": round(t.exit_price, 2), "qty": round(t.qty, 2),
                 "reason": t.reason, "pnl": round(t.pnl_total, 2)} for t in fol_res.trades]
        print("\n-- FLOW follow trades --")
        print(pd.DataFrame(rows).to_string(index=False) if rows else "  (none)")

    if args.years:
        print("\n-- by year (FLOW follow vs C1 baseline) --")
        for yr in sorted({date.fromisoformat(k).year for k in follow}):
            b = bars[bars.index.year == yr]
            if b.empty:
                continue
            s_f = {k: v for k, v in follow.items() if date.fromisoformat(k).year == yr}
            _, sb = run(cfg, b, None)
            _, sf = run(cfg, b, s_f)
            print(f"  {yr}  " + line("FLOW", sf))
            print(f"        " + line("C1", sb))

    print("\nexit mix (FLOW follow): "
          f"TP {fol.tp_exits}  BaseSL {fol.base_sl_exits}  BEstop {fol.be_stop_exits}  "
          f"BEtrail {fol.be_trail_exits}  VWAP {fol.vwap_trail_exits}  EOD {fol.eod_exits}")


if __name__ == "__main__":
    main()
