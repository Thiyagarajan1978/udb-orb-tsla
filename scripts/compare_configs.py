#!/usr/bin/env python
"""Apples-to-apples comparison of every config layer on the same bars.

Runs the cumulative stack (faithful Pine port -> current default) under BOTH fill models:
  - optimistic : stop exits fill at the stop level (assumes a resting order; the Pine model)
  - realistic  : stop exits fill at the bar CLOSE (alerts-only; you act after the candle closes)

The realistic column is the only one that reflects how this system is actually traded. The
optimistic column is shown to expose how much of each "gain" was a fill-model artifact.

Usage:  python scripts/compare_configs.py [--start 2026-01-01] [--end 2026-07-08]
"""
from __future__ import annotations

import argparse
import copy
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.data.fmp_client import rth_only  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402
from udb_orb.engine.metrics import summarize  # noqa: E402
from udb_orb.engine.orb_engine import run_engine  # noqa: E402
from udb_orb.engine.params import Params  # noqa: E402

OFF_REV = {"enabled": False, "trigger_on_be_stop": False, "trail_to_eod": False,
           "target_or_mult": 0.0, "reenter_after_whipsaw": False}
ON_REV = {"enabled": True, "trigger_on_be_stop": True, "trail_to_eod": True,
          "target_or_mult": 0.0, "reenter_after_whipsaw": False}


def variant(base, *, be=0.35, tp=1.0, rev=False, runner=0.0, max_or=0.0, cap=0.0):
    c = copy.deepcopy(base)
    p = c["profile"]
    p["be_retrace_trigger"] = be
    p["be_retrace_use_close"] = False
    p["adaptive_tp_scale"] = tp
    p["max_or_width_enabled"] = max_or > 0
    p["max_or_width"] = max_or if max_or > 0 else 20.0
    p["reversal_risk_cap"] = cap
    p["reversal_risk_mode"] = "scale"
    c["enhancements"]["reversal_capture"] = dict(ON_REV if rev else OFF_REV)
    c["enhancements"]["runner_trail"] = {"enabled": runner > 0, "or_mult": runner or 0.75}
    c["enhancements"]["pdh_pdl_filter"] = {"enabled": False, "proximity_pct": 14.0}
    c["execution"]["protective_stop"] = False
    c["execution"]["daily_loss_limit"] = 0.0
    return c


def run(cfg, bars, on_close):
    c = copy.deepcopy(cfg)
    c["execution"]["exit_on_close"] = on_close
    res = run_engine(bars, Params.from_config(c), c["enhancements"])
    s = summarize(res)
    dn = defaultdict(float)
    for t in res.trades:
        dn[t.day] += t.pnl_total
    worst = min(dn.values()) if dn else 0.0
    return s, worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-07-08")
    args = ap.parse_args()

    cfg = load_config()
    with Database(db_path(cfg)) as db:
        bars = rth_only(db.load_bars(cfg["symbol"]))
    s0 = datetime.strptime(args.start, "%Y-%m-%d").date()
    e0 = datetime.strptime(args.end, "%Y-%m-%d").date()
    bars = bars[[s0 <= d <= e0 for d in bars.index.date]]

    stack = [
        ("1. Faithful Pine port",      variant(cfg)),
        ("2. + BE trigger 0.55",       variant(cfg, be=0.55)),
        ("3. + reversal capture",      variant(cfg, be=0.55, rev=True)),
        ("4. + runner trail 0.75xOR",  variant(cfg, be=0.55, rev=True, runner=0.75)),
        ("5. + max OR width $8",       variant(cfg, be=0.55, rev=True, runner=0.75, max_or=8)),
        ("6. + reversal risk cap $6",  variant(cfg, be=0.55, rev=True, runner=0.75, max_or=8, cap=6)),
    ]

    print(f"TSLA 5m · {args.start} -> {args.end} · same bars, same engine\n")
    print(f"{'config layer':<28}{'OPTIMISTIC fills':>26}   {'REALISTIC fills (actual)':>34}")
    print(f"{'':<28}{'net':>9}{'WR%':>6}{'worst':>8}   {'net':>9}{'WR%':>6}{'PF':>6}{'worst':>8}{'net/worst':>11}")
    print("-" * 92)
    for name, c in stack:
        so, wo = run(c, bars, on_close=False)
        sr, wr_ = run(c, bars, on_close=True)
        pf = f"{sr.profit_factor:.2f}" if sr.profit_factor else "n/a"
        ratio = sr.net_pnl / abs(wr_) if wr_ else 0.0
        star = "  <- CURRENT DEFAULT" if name.startswith("6.") else ""
        print(f"{name:<28}{so.net_pnl:>+9.1f}{so.win_rate:>6.1f}{wo:>8.1f}   "
              f"{sr.net_pnl:>+9.1f}{sr.win_rate:>6.1f}{pf:>6}{wr_:>8.1f}{ratio:>11.2f}{star}")

    print("\nOPTIMISTIC = stop exits fill at the stop level (resting order; the Pine assumption).")
    print("REALISTIC  = stop exits fill at the bar CLOSE (alerts fire after the candle closes).")
    print("net/worst  = net P&L per $1 of worst-day risk — the sizing-adjusted score.")


if __name__ == "__main__":
    main()
