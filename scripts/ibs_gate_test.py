#!/usr/bin/env python
"""Does the ORB+IBS/CLV close-location gate add anything to C1? (handover doc, 2026-07-30)

  python scripts/ibs_gate_test.py --start 2023-08-01 --end 2026-07-29 --years
  python scripts/ibs_gate_test.py --start 2021-08-01 --end 2026-07-29 --years   # 5yr

Runs the doc's OWN §13 experiment matrix — each layer independently against the plain C1
baseline, then stacked — because that is the right way to ask whether IBS adds *incremental*
value rather than whether one combined backtest happens to make money (doc §12).

WHAT IS HELD FIXED
  Only the entry gate changes. C1's stop, ATR target, partial, BE, VWAP runner, close-
  triggered fills, slippage, EOD and day filters are untouched, and the gate applies to the
  PRIMARY only (the doc's system has no reversal leg; --gate-reversal also gates it).

The doc's thresholds are its defaults: prev-day IBS >=0.75 long / <=0.25 short, breakout-bar
IBS >=0.75, body/range >=0.50, adverse wick/range <=0.25, 0.20<=range/ATR<=0.80, and breakout
close beyond the prior daily close.

NOTE the doc's §7 wick guard is NOT independent of its §2 IBS guard: for a bar closing above
its open, wick/range<=0.25 and IBS>=0.75 are the same condition. They agree on 705/705 C1
entry bars, so 'wick' and 'ibs' rows below are expected to be near-identical by construction.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# The doc's §13 matrix: name -> the ibs_entry_gate sub-filters that layer switches on.
LAYERS = [
    ("plain C1 (baseline)",      None),
    ("+ prev-day IBS",           {"prev_day_ibs": 0.75}),
    ("+ breakout-bar IBS",       {"breakout_ibs": 0.75}),
    ("+ both IBS filters",       {"prev_day_ibs": 0.75, "breakout_ibs": 0.75}),
    ("+ range/ATR",              {"prev_day_ibs": 0.75, "breakout_ibs": 0.75,
                                  "range_atr": [0.20, 0.80]}),
    ("+ prior-close progress",   {"prev_day_ibs": 0.75, "breakout_ibs": 0.75,
                                  "range_atr": [0.20, 0.80], "prior_close_progress": True}),
    ("+ body/wick quality",      {"prev_day_ibs": 0.75, "breakout_ibs": 0.75,
                                  "range_atr": [0.20, 0.80], "prior_close_progress": True,
                                  "body_min": 0.50, "wick_max": 0.25}),
]
# Each doc filter ALONE on top of C1 — isolates which one carries the damage.
SOLO = [
    ("breakout IBS only",        {"breakout_ibs": 0.75}),
    ("prev-day IBS only",        {"prev_day_ibs": 0.75}),
    ("body/range only",          {"body_min": 0.50}),
    ("wick/range only",          {"wick_max": 0.25}),
    ("range/ATR only",           {"range_atr": [0.20, 0.80]}),
    ("prior-close prog. only",   {"prior_close_progress": True}),
]


def run(cfg, bars, gate: dict | None, gate_reversal: bool = False):
    from udb_orb.engine.orb_engine import run_engine
    from udb_orb.engine.params import Params
    from udb_orb.engine.metrics import summarize
    enh = dict(cfg.get("enhancements", {}))
    if gate is not None:
        enh["ibs_entry_gate"] = {"enabled": True, "apply_to_reversal": gate_reversal, **gate}
    res = run_engine(bars, Params.from_config(cfg), enh)
    return res, summarize(res)


def line(tag: str, s, base=None) -> str:
    pf = "  n/a" if s.profit_factor is None else f"{s.profit_factor:5.2f}"
    delta = "" if base is None else f"  ({s.net_pnl - base.net_pnl:+8.2f})"
    return (f"{tag:<26} {s.trades:>4}tr  WR {s.win_rate:5.1f}%  net {s.net_pnl:>9.2f}  "
            f"PF {pf}  exp {s.expectancy:>6.2f}  worst {s.worst_day or 0:>7.2f}{delta}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="ORB+IBS/CLV entry gate vs plain C1")
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2026-07-29")
    ap.add_argument("--config", default="config/tsla_config_C1.yaml")
    ap.add_argument("--years", action="store_true", help="per-year table (the house rule)")
    ap.add_argument("--solo", action="store_true", help="also run each filter on its own")
    ap.add_argument("--gate-reversal", action="store_true",
                    help="apply the gate to the reversal leg too (doc has no reversal)")
    args = ap.parse_args(argv)

    from udb_orb.config import load_config
    from udb_orb.backtest.runner import load_bars

    cfg = load_config(ROOT / args.config)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    bars = load_bars(cfg, start, end)
    print(f"{len(bars)} 5m bars  {bars.index.min()} .. {bars.index.max()}\n")

    _, base = run(cfg, bars, None)
    print("=" * 112)
    print("§13 INCREMENTAL MATRIX — each layer added on top of the previous")
    print("=" * 112)
    for name, gate in LAYERS:
        _, s = run(cfg, bars, gate, args.gate_reversal)
        print(line(name, s, None if gate is None else base))

    if args.solo:
        print("\n" + "=" * 112)
        print("EACH FILTER ALONE on top of C1")
        print("=" * 112)
        print(line("plain C1 (baseline)", base))
        for name, gate in SOLO:
            _, s = run(cfg, bars, gate, args.gate_reversal)
            print(line(name, s, base))

    if args.years:
        print("\n" + "=" * 112)
        print("PER YEAR — a multi-year total can hide a regime trap (BE_STOP_ANALYSIS §31)")
        print("=" * 112)
        for yr in sorted({d.year for d in bars.index.date}):
            b = bars[bars.index.year == yr]
            if b.empty:
                continue
            _, yb = run(cfg, b, None)
            print(f"\n  {yr}")
            print("    " + line("plain C1 (baseline)", yb))
            for name, gate in LAYERS[1:]:
                _, s = run(cfg, b, gate, args.gate_reversal)
                print("    " + line(name, s, yb))


if __name__ == "__main__":
    main()
