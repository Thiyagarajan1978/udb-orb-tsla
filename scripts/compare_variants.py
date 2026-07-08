#!/usr/bin/env python
"""Compare config variants over a run's stored bars (no re-fetch).

Answers "can we avoid the BE-Stop failures?" empirically: run the same YTD bars through
the baseline and several candidate rule changes, and print net / win-rate / failures /
BE-stops / reversals side by side. Nothing here is adopted automatically.

Usage:  python scripts/compare_variants.py --run 8
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.data.fmp_client import rth_only  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402
from udb_orb.engine.metrics import summarize  # noqa: E402
from udb_orb.engine.orb_engine import run_engine  # noqa: E402
from udb_orb.engine.params import Params  # noqa: E402


def variant(cfg, *, profile=None, enh=None):
    c = copy.deepcopy(cfg)
    if profile:
        c["profile"].update(profile)
    if enh:
        for k, v in enh.items():
            c["enhancements"].setdefault(k, {}).update(v)
    return c


def run(cfg, bars):
    p = Params.from_config(cfg)
    return summarize(run_engine(bars, p, cfg["enhancements"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, required=True)
    args = ap.parse_args()
    cfg = load_config()
    with Database(db_path(cfg)) as db:
        bars = rth_only(db.load_bars(cfg["symbol"]))

    variants = {
        "baseline": cfg,
        "rvol>=1.2": variant(cfg, enh={"rvol_filter": {"enabled": True, "min_rvol": 1.2}}),
        "rvol>=1.3": variant(cfg, enh={"rvol_filter": {"enabled": True, "min_rvol": 1.3}}),
        "close-based BE": variant(cfg, profile={"be_retrace_use_close": True}),
        "BE trig 0.50": variant(cfg, profile={"be_retrace_trigger": 0.50}),
        "BE trig 0.20": variant(cfg, profile={"be_retrace_trigger": 0.20}),
        "skip 09:35-09:45": variant(cfg, enh={"time_window": {"enabled": True, "start": "09:45", "end": "16:00"}}),
        "rvol1.3+closeBE": variant(cfg, profile={"be_retrace_use_close": True},
                                   enh={"rvol_filter": {"enabled": True, "min_rvol": 1.3}}),
    }

    hdr = f"{'variant':<18}{'trades':>7}{'succ':>6}{'fail':>6}{'WR%':>7}{'net':>10}{'BEstop':>8}{'revs':>6}{'worst':>8}"
    print(hdr)
    print("-" * len(hdr))
    for name, c in variants.items():
        s = run(c, bars)
        worst = f"{s.worst_day:+.2f}" if s.worst_day is not None else "n/a"
        print(f"{name:<18}{s.trades:>7}{s.successes:>6}{s.failures:>6}{s.win_rate:>7.1f}"
              f"{s.net_pnl:>+10.2f}{s.be_stop_failures:>8}{s.reversal_entries:>6}{worst:>8}")


if __name__ == "__main__":
    main()
