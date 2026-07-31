#!/usr/bin/env python
"""Does sizing the PRIMARY to constant dollar risk beat sizing it to constant share count?

  python scripts/primary_risk_sizing_test.py --start 2025-01-02 --end 2025-12-31
  python scripts/primary_risk_sizing_test.py --start 2022-01-03 --end 2026-07-29 --years

C1 takes trade_qty=1.0 on every primary whether its stop is $1.20 or the full $6 cap away —
a ~5x unmanaged swing in dollar risk per trade. The same change on the REVERSAL leg (a $6
dollar-risk cap, adopted 2026-07) cut the worst day 41% for 18% of net. This applies it to
the primary: qty = risk_budget / |entry - initial stop|.

APPLES TO APPLES — the part that matters
  Re-sizing alone moves net without adding edge, so raw net is not a fair comparison. The
  risk budget is CALIBRATED from the baseline: it is set to the baseline's own mean dollar
  risk per primary trade, so both runs deploy the same average risk per trade. Results are
  then reported three ways:
    net              — only meaningful because the budget is calibrated
    $ risk deployed  — sum of |entry - stop| * qty; confirms the calibration actually held
    net per $1 risk  — the scale-free metric, and the one that justified the reversal cap
  A qty cap is swept too: an uncapped budget hands enormous size to the tightest stops,
  which is a real execution risk, not a free lunch.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def run(cfg, bars, budget=None, max_qty=None):
    from udb_orb.engine.orb_engine import run_engine
    from udb_orb.engine.params import Params
    from udb_orb.engine.metrics import summarize
    enh = dict(cfg.get("enhancements", {}))
    if budget:
        enh["primary_risk_sizing"] = {"enabled": True, "risk_budget": budget,
                                      "max_qty": max_qty}
    res = run_engine(bars, Params.from_config(cfg), enh)
    return res, summarize(res)


def risk_stats(res):
    """Dollar risk deployed on PRIMARY legs, and the qty range that produced it."""
    prim = [t for t in res.trades if not t.is_reversal]
    risk = sum(t.risk_amount for t in prim)
    qtys = [t.qty for t in prim]
    return prim, risk, (min(qtys) if qtys else 0), (max(qtys) if qtys else 0)


def report(tag, res, s, base_net=None, base_risk=None):
    prim, risk, qmin, qmax = risk_stats(res)
    tot_risk = sum(t.risk_amount for t in res.trades)
    per_risk = s.net_pnl / tot_risk if tot_risk else 0.0
    d_net = f"{s.net_pnl - base_net:+8.2f}" if base_net is not None else "        "
    d_risk = f"{100 * (risk / base_risk - 1):+5.1f}%" if base_risk else "      "
    pf = "  n/a" if s.profit_factor is None else f"{s.profit_factor:5.2f}"
    print(f"{tag:<28} {s.trades:>4}tr  WR {s.win_rate:5.1f}%  net {s.net_pnl:>8.2f} {d_net}  "
          f"PF {pf}  worst {s.worst_day or 0:>7.2f}  |  prim $risk {risk:>8.2f} {d_risk}  "
          f"net/$risk {per_risk:>6.3f}  qty {qmin:.2f}-{qmax:.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="primary risk-parity sizing vs flat qty")
    ap.add_argument("--start", default="2025-01-02")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--config", default="config/tsla_config_C1.yaml")
    ap.add_argument("--caps", default="none,3,2,1.5",
                    help="max_qty values to sweep ('none' = uncapped)")
    ap.add_argument("--years", action="store_true")
    args = ap.parse_args(argv)

    from udb_orb.config import load_config
    from udb_orb.backtest.runner import load_bars

    cfg = load_config(ROOT / args.config)
    bars = load_bars(cfg, date.fromisoformat(args.start), date.fromisoformat(args.end))
    caps = [None if c.strip() == "none" else float(c) for c in args.caps.split(",")]

    def block(b, label):
        base_res, base = run(cfg, b, None)
        prim, base_risk, _, _ = risk_stats(base_res)
        if not prim:
            return
        budget = base_risk / len(prim)      # calibration: baseline's own mean risk per trade
        print(f"\n{label}   calibrated risk budget = ${budget:.2f}/trade "
              f"(baseline mean over {len(prim)} primaries)")
        print("-" * 150)
        report("C1 baseline (flat qty 1.0)", base_res, base)
        for cap in caps:
            r, s = run(cfg, b, budget, cap)
            report(f"risk-parity, cap {cap or 'none'}", r, s, base.net_pnl, base_risk)

    block(bars, f"=== {args.start} .. {args.end} ===")
    if args.years:
        for yr in sorted({d.year for d in bars.index.date}):
            yb = bars[bars.index.year == yr]
            if not yb.empty:
                block(yb, f"=== {yr} ===")


if __name__ == "__main__":
    main()
