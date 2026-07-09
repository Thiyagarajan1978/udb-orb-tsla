#!/usr/bin/env python
"""Summary report for a run: headline stats, monthly breakdown, exit-reason mix, top/worst days.

Usage:  python scripts/summary_report.py --run 20
        python scripts/summary_report.py --latest
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

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
        rr = runs.loc[runs["id"] == run_id].iloc[0]
        t = db.trades_df(run_id)
        d = db.days_df(run_id)

    t["month"] = pd.to_datetime(t["day"]).dt.to_period("M").astype(str)
    net = t["pnl_total"].sum()
    succ = int((t["pnl_total"] > 0).sum())
    fail = int((t["pnl_total"] <= 0).sum())
    gp = t.loc[t.pnl_total > 0, "pnl_total"].sum()
    gl = -t.loc[t.pnl_total <= 0, "pnl_total"].sum()

    print(f"=== UDB-ORB TSLA · Summary Report · run #{run_id} ===")
    print(f"Profile : {rr['profile']}")
    print(f"Range   : {rr['start_date']} -> {rr['end_date']}")
    print(f"\nTrades {len(t)} | success {succ} / failure {fail} | WR {100*succ/len(t):.1f}%")
    print(f"Net P&L {net:+.2f} | PF {gp/gl if gl else float('inf'):.2f} | "
          f"Expectancy {net/len(t):+.3f}/trade")
    print(f"Avg win +{gp/succ if succ else 0:.2f} | Avg loss -{gl/fail if fail else 0:.2f}")
    print(f"Total risk exposed {t['risk_amount'].sum():.2f} | "
          f"Reversals {int(t['is_reversal'].sum())}")

    # monthly breakdown
    print("\n--- Monthly breakdown ---")
    print(f"{'month':<9}{'trades':>7}{'succ':>6}{'fail':>6}{'WR%':>7}{'net':>10}{'best':>9}{'worst':>9}")
    dnet = t.groupby(["month", "day"])["pnl_total"].sum().reset_index()
    for m, sub in t.groupby("month"):
        s = int((sub.pnl_total > 0).sum()); f = int((sub.pnl_total <= 0).sum())
        dd = dnet[dnet.month == m]["pnl_total"]
        print(f"{m:<9}{len(sub):>7}{s:>6}{f:>6}{100*s/len(sub):>7.1f}{sub.pnl_total.sum():>+10.2f}"
              f"{dd.max():>+9.2f}{dd.min():>+9.2f}")

    # exit reasons
    print("\n--- Exit reason mix ---")
    print(f"{'reason':<16}{'n':>5}{'net':>10}{'avg':>9}")
    for r, sub in t.groupby("reason"):
        print(f"{r:<16}{len(sub):>5}{sub.pnl_total.sum():>+10.2f}{sub.pnl_total.mean():>+9.2f}")

    # day extremes
    dd = t.groupby("day")["pnl_total"].sum().sort_values()
    print("\n--- Worst 5 days ---")
    for day, v in dd.head(5).items():
        print(f"  {day}  {v:+.2f}")
    print("--- Best 5 days ---")
    for day, v in dd.tail(5)[::-1].items():
        print(f"  {day}  {v:+.2f}")

    nt = d[d["has_trades"] == 0]
    if not nt.empty:
        print(f"\n--- No-trade days ({len(nt)}) ---")
        for reason, sub in nt.groupby("no_trade_reason"):
            print(f"  {reason:<22} {len(sub)}")


if __name__ == "__main__":
    main()
