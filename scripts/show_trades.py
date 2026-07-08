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


def _ts(ts):
    """Exact fill timestamp in ET, to the second (bar-close fill — see note)."""
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "        -          "
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", type=int)
    g.add_argument("--latest", action="store_true")
    ap.add_argument("--events", action="store_true",
                    help="also print the full fill-by-fill event log (every entry/partial/exit)")
    args = ap.parse_args()

    cfg = load_config()
    with Database(db_path(cfg)) as db:
        runs = db.list_runs()
        run_id = int(runs["id"].iloc[0]) if args.latest else args.run
        rr = runs.loc[runs["id"] == run_id].iloc[0]
        t = db.trades_df(run_id)
        d = db.days_df(run_id)
        ev = db.events_df(run_id) if args.events else None

    print(f"Run #{run_id} · {rr['profile']} · {rr['start_date']}..{rr['end_date']}")
    print("Times are the 5-minute BAR-START (ET); the fill is that bar's CLOSE price "
          "(so a 09:55 entry fills at the 09:55-10:00 bar close).\n")
    print(f"{'#':>3} {'Dir':<9}{'Entry Time (ET)':<21}{'Entry$':>9}   {'Exit Time (ET)':<21}{'Exit$':>9}"
          f"{'Qty':>5}{'Part$':>8}{'NetPnL':>9}{'Risk$':>8}{'Outcome':>9}  Reason")
    print("-" * 130)

    for i, (_, r) in enumerate(t.iterrows(), 1):
        print(f"{i:>3} {r['direction']:<9}{_ts(r['entry_ts']):<21}{r['entry_price']:>9.2f}   "
              f"{_ts(r['exit_ts']):<21}{r['exit_price']:>9.2f}{r['qty']:>5.1f}"
              f"{r['part1_pnl']:>+8.2f}{r['pnl_total']:>+9.2f}{(r['risk_amount'] or 0):>8.2f}"
              f"{r['outcome']:>9}  {r['reason']}")

    # totals + no-trade days
    net = t["pnl_total"].sum() if not t.empty else 0.0
    succ = int((t["pnl_total"] > 0).sum()) if not t.empty else 0
    fail = int((t["pnl_total"] <= 0).sum()) if not t.empty else 0
    print("-" * 130)
    print(f"TOTAL  {len(t)} trades · {succ} success / {fail} failure · net {net:+.2f} · "
          f"risk exposed {t['risk_amount'].sum():.2f}")

    nt = d[d["has_trades"] == 0]
    if not nt.empty:
        print("\nNo-trade days:")
        for _, r in nt.iterrows():
            print(f"  {r['date']} ({r['day_name']}) — {r['no_trade_reason']}")

    if ev is not None and not ev.empty:
        print("\n=== Full fill-by-fill event log (exact time + price) ===")
        print(f"{'Time (ET)':<21}{'Event':<16}{'Dir':<9}{'Price':>9}{'Qty':>6}{'PnL':>9}  Note")
        print("-" * 92)
        for _, e in ev.iterrows():
            pnl = "" if e["pnl"] is None or pd.isna(e["pnl"]) else f"{e['pnl']:+.2f}"
            note = e["note"] or e["reason"] or ""
            print(f"{_ts(e['ts']):<21}{e['type']:<16}{(e['direction'] or ''):<9}"
                  f"{e['price']:>9.2f}{e['qty']:>7.2f}{pnl:>9}  {note}")


if __name__ == "__main__":
    main()
