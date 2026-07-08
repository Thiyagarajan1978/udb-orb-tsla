#!/usr/bin/env python
"""Print every trade of a run in a readable, per-day table for analysis.

Columns: entry time/price, exit time/price, qty, partial banked, net P&L, $ at risk
(base-SL), outcome, exit reason. Multiple entries per day (primary + reversal) are grouped
so you can see how each day played out.

Usage:  python scripts/show_trades.py --run 13
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


def _t(ts):
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "  -  "
    return pd.Timestamp(ts).strftime("%H:%M")


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

    print(f"Run #{run_id} · {rr['profile']} · {rr['start_date']}..{rr['end_date']}")
    print(f"{'Day':<11}{'Dir':<9}{'In':>6}{'Entry':>9}{'Out':>7}{'Exit':>9}{'Qty':>5}"
          f"{'Part$':>8}{'NetPnL':>9}{'Risk$':>8}{'Outcome':>9}  Reason")
    print("-" * 104)

    day_net = {}
    for _, r in t.iterrows():
        day_net[r["day"]] = day_net.get(r["day"], 0.0) + r["pnl_total"]
        print(f"{r['day']:<11}{r['direction']:<9}{_t(r['entry_ts']):>6}{r['entry_price']:>9.2f}"
              f"{_t(r['exit_ts']):>7}{r['exit_price']:>9.2f}{r['qty']:>5.1f}"
              f"{r['part1_pnl']:>+8.2f}{r['pnl_total']:>+9.2f}{(r['risk_amount'] or 0):>8.2f}"
              f"{r['outcome']:>9}  {r['reason']}")

    # totals + no-trade days
    net = t["pnl_total"].sum() if not t.empty else 0.0
    succ = int((t["pnl_total"] > 0).sum()) if not t.empty else 0
    fail = int((t["pnl_total"] <= 0).sum()) if not t.empty else 0
    print("-" * 104)
    print(f"TOTAL  {len(t)} trades · {succ} success / {fail} failure · net {net:+.2f} · "
          f"risk exposed {t['risk_amount'].sum():.2f}")

    nt = d[d["has_trades"] == 0]
    if not nt.empty:
        print("\nNo-trade days:")
        for _, r in nt.iterrows():
            print(f"  {r['date']} ({r['day_name']}) — {r['no_trade_reason']}")


if __name__ == "__main__":
    main()
