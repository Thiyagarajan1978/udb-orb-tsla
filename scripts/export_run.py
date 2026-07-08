#!/usr/bin/env python
"""Export a run's full detail to CSV for analysis.

Writes three files under data/results/run_<id>/:
  trades.csv  — one row per closed leg (primary AND each reversal are separate rows)
  events.csv  — every entry / partial-exit / exit event with timestamp, price, qty, pnl
  days.csv    — per-session summary (trade + no-trade with reason)

Usage:
  python scripts/export_run.py --run 6
  python scripts/export_run.py --latest
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", type=int)
    g.add_argument("--latest", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    with Database(db_path(cfg)) as db:
        runs = db.list_runs()
        run_id = int(runs["id"].iloc[0]) if args.latest else args.run
        trades = db.trades_df(run_id)
        events = db.events_df(run_id)
        days = db.days_df(run_id)

    outdir = ROOT / "data" / "results" / f"run_{run_id}"
    outdir.mkdir(parents=True, exist_ok=True)

    # order columns for readability
    tcols = ["id", "day", "direction", "is_reversal", "entry_ts", "entry_price",
             "exit_ts", "exit_price", "qty", "part1_pnl", "pnl_total", "pnl_per_unit",
             "reason", "duration_bars"]
    trades = trades[[c for c in tcols if c in trades.columns]]
    trades.to_csv(outdir / "trades.csv", index=False)
    events.to_csv(outdir / "events.csv", index=False)
    days.to_csv(outdir / "days.csv", index=False)

    prim = int((trades["is_reversal"] == 0).sum()) if not trades.empty else 0
    rev = int((trades["is_reversal"] == 1).sum()) if not trades.empty else 0
    print(f"Run #{run_id} exported to {outdir}")
    print(f"  trades.csv : {len(trades)} legs  ({prim} primary + {rev} reversal, kept separate)")
    print(f"  events.csv : {len(events)} events (entries, partials, exits)")
    print(f"  days.csv   : {len(days)} sessions")


if __name__ == "__main__":
    main()
