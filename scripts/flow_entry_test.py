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

ENTRY TIMING — E1 (09:31) vs E2 (09:35)
  E2 (default) takes the trade at the CLOSE of the 09:30 bar — 09:35:00, the first executable
  price at which the 5m opening range, and hence C1's stop and BE level, are known. It costs
  four minutes of drift; `--drift` measures that.
  E1 (`--e1`) removes the delay: it enters at 09:31:00, the instant the signal completes, and
  sizes the stop and BE from the 09:30-09:31 CANDLE — the only range that exists at that
  moment — so it still looks ahead at nothing. Two consequences of entering mid-bar, both
  reported by `--e1`:
    * the 5m max-OR-width day filter cannot be applied (that width is unknown at 09:31), so
      E1 runs with `or_gate` OFF;
    * the exit engine's first bar is 09:40, because letting it act on the 09:30-09:35 bar
      would let a wick printed BEFORE the entry fill the TP. That skips one close-stop check
      at 09:35, so the harness counts and prices those days as an explicit leakage line.

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
E1_ENTRIES = ROOT / "data" / "reference" / "tsla_0931_entry.csv"


def load_e1_entries(path: Path) -> tuple[dict, dict]:
    """{day: 09:31 price}, {day: (09:30 candle high, low)} — build with scripts/entry_0931.py."""
    if not path.exists():
        raise SystemExit(f"{path} missing — run: python scripts/entry_0931.py")
    d = pd.read_csv(path).dropna(subset=["px_0931", "hi_0930", "lo_0930"])
    return (dict(zip(d.date, d.px_0931.astype(float))),
            {k: (float(h), float(lo)) for k, h, lo in zip(d.date, d.hi_0930, d.lo_0930)})


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


def run(cfg, bars, signals: dict | None, allow_reversal: bool = True, e1: tuple | None = None):
    from udb_orb.engine.orb_engine import run_engine
    from udb_orb.engine.params import Params
    from udb_orb.engine.metrics import summarize
    enh = dict(cfg.get("enhancements", {}))
    if signals is not None:
        fe = {"enabled": True, "signals": signals, "allow_reversal": allow_reversal}
        if e1 is not None:
            px, rng = e1
            fe.update(entry_price=px, entry_range=rng, or_gate=False)
        enh["flow_entry"] = fe
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
    ap.add_argument("--e1", action="store_true",
                    help="also run E1: enter at 09:31, stop sized off the 09:30-09:31 candle")
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

    e1 = load_e1_entries(E1_ENTRIES) if args.e1 else None

    base_res, base = run(cfg, bars, None)
    fol_res, fol = run(cfg, bars, follow)
    fad_res, fad = run(cfg, bars, fade)
    fol_nr_res, fol_nr = run(cfg, bars, follow, allow_reversal=False)

    print("=" * 104)
    print(line("C1 baseline (ORB)", base))
    if e1:
        e1_res, e1s = run(cfg, bars, follow, e1=e1)
        e1n_res, e1n = run(cfg, bars, follow, allow_reversal=False, e1=e1)
        e1f_res, e1f = run(cfg, bars, fade, e1=e1)
        print(line("E1 follow  (09:31)", e1s))
        print(line("E1 follow, no rev", e1n))
        print(line("E1 fade (inverse)", e1f))
    print(line("E2 follow  (09:35)", fol))
    print(line("E2 follow, no rev", fol_nr))
    print(line("E2 fade (inverse)", fad))
    print("=" * 104)

    if e1:
        # Leakage: E1 holds from 09:31 but the exit engine's first bar is 09:40, so a
        # close-triggered stop that would have fired at the 09:35 close is missed. Count those
        # days and price the miss as (realized P&L) - (exit at the 09:35 close on full size).
        _, rng_map = e1
        prim = [t for t in e1_res.trades if not t.is_reversal]
        worse = 0.0
        cnt = 0
        for t in prim:
            day = str(t.day)
            g = bars[bars.index.date == date.fromisoformat(day)]
            if g.empty or day not in rng_map:
                continue
            or_close = float(g.iloc[0]["close"])
            ent = float(t.entry_price)
            sgn = 1 if str(t.direction).upper().startswith(("1", "L")) else -1
            hi, lo = rng_map[day]
            stop = max(lo, ent - 6.0) if sgn == 1 else min(hi, ent + 6.0)
            if (or_close - stop) * sgn < 0:
                cnt += 1
                worse += (or_close - ent) * sgn * t.qty - t.pnl_total
        print(f"E1 leakage check: {cnt}/{len(prim)} primaries closed the 09:35 bar already "
              f"beyond their stop; forcing them out there instead would move net by {worse:+.2f}")

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
        print("\n-- by year --")
        for yr in sorted({date.fromisoformat(k).year for k in follow}):
            b = bars[bars.index.year == yr]
            if b.empty:
                continue
            s_f = {k: v for k, v in follow.items() if date.fromisoformat(k).year == yr}
            _, sb = run(cfg, b, None)
            _, sf = run(cfg, b, s_f)
            if e1:
                _, se = run(cfg, b, s_f, e1=e1)
                print(f"  {yr}  " + line("E1 (09:31)", se))
                print("        " + line("E2 (09:35)", sf))
            else:
                print(f"  {yr}  " + line("FLOW", sf))
            print("        " + line("C1", sb))

    print("\nexit mix (FLOW follow): "
          f"TP {fol.tp_exits}  BaseSL {fol.base_sl_exits}  BEstop {fol.be_stop_exits}  "
          f"BEtrail {fol.be_trail_exits}  VWAP {fol.vwap_trail_exits}  EOD {fol.eod_exits}")


if __name__ == "__main__":
    main()
