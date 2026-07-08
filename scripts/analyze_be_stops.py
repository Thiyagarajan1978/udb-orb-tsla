#!/usr/bin/env python
"""Deep-dive on BE-Stop *failure* trades for a run.

For each BE-Stop leg we reconstruct the day from stored bars and report:
  - modeled_loss   : what the ledger booked (entry->entry minus slippage)  ~ tiny
  - risk_taken     : |entry - OR boundary| * qty  = the base-SL loss you were exposed to
  - mfe / mae      : max favourable / adverse excursion per unit AFTER entry (to EOD)
  - hold_eod       : P&L per unit if you had just held the ORIGINAL direction to 15:50
  - opp_eod        : P&L per unit if you had taken the OPPOSITE direction to 15:50
  - reversal_pnl   : P&L of the reversal leg the system already took that day (if any)
  - day_net        : net of ALL legs that day (did the day recover?)

Then it aggregates: how BE-stop days actually ended, and which simple alternatives would
have improved them. Writes be_stop_analysis.csv next to the other run exports.

Usage:  python scripts/analyze_be_stops.py --run 8
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.data.fmp_client import rth_only  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402
from udb_orb.engine.params import Params  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", type=int)
    g.add_argument("--latest", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    p = Params.from_config(cfg)
    symbol = cfg["symbol"]

    with Database(db_path(cfg)) as db:
        runs = db.list_runs()
        run_id = int(runs["id"].iloc[0]) if args.latest else args.run
        trades = db.trades_df(run_id)
        bars = db.load_bars(symbol)

    bars = rth_only(bars)
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True).dt.tz_convert("America/New_York")
    day_net = trades.groupby("day")["pnl_total"].sum().to_dict()

    be = trades[trades["reason"].str.contains("BE Stop", na=False)].copy()
    if be.empty:
        print("No BE-Stop trades in this run.")
        return

    recs = []
    for _, t in be.iterrows():
        day = t["day"]
        dbars = bars[[d.isoformat() == day for d in bars.index.date]].sort_index()
        if dbars.empty:
            continue
        or_bar = dbars[[x == p.market_open for x in dbars.index.time]]
        if or_bar.empty:
            continue
        or_high = float(or_bar["high"].iloc[0]); or_low = float(or_bar["low"].iloc[0])
        entry = float(t["entry_price"]); qty = float(t["qty"])
        is_long = t["direction"].startswith("L")

        # bars strictly after the entry bar, through EOD
        after = dbars[dbars.index > t["entry_ts"]]
        eod_close = float(dbars["close"].iloc[-1])

        if is_long:
            base_sl = or_low
            risk = (entry - base_sl) * qty
            mfe = (after["high"].max() - entry) if not after.empty else 0.0
            mae = (after["low"].min() - entry) if not after.empty else 0.0
            hold_eod = eod_close - entry
        else:
            base_sl = or_high
            risk = (base_sl - entry) * qty
            mfe = (entry - after["low"].min()) if not after.empty else 0.0
            mae = (entry - after["high"].max()) if not after.empty else 0.0
            hold_eod = entry - eod_close
        opp_eod = -hold_eod

        # reversal leg that day (if any)
        day_legs = trades[trades["day"] == day]
        rev = day_legs[day_legs["is_reversal"] == 1]
        rev_pnl = float(rev["pnl_total"].sum()) if not rev.empty else None

        recs.append({
            "day": day, "dir": t["direction"], "entry_time": t["entry_ts"].strftime("%H:%M"),
            "entry": round(entry, 2), "or_width": round(or_high - or_low, 2),
            "modeled_loss": round(float(t["pnl_total"]), 2),
            "risk_taken": round(risk, 2),
            "mfe": round(float(mfe), 2), "mae": round(float(mae), 2),
            "hold_eod": round(float(hold_eod), 2), "opp_eod": round(float(opp_eod), 2),
            "reversal_pnl": None if rev_pnl is None else round(rev_pnl, 2),
            "day_net": round(float(day_net.get(day, 0.0)), 2),
        })

    df = pd.DataFrame(recs)
    outdir = ROOT / "data" / "results" / f"run_{run_id}"
    outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir / "be_stop_analysis.csv", index=False)

    n = len(df)
    print(f"=== BE-Stop failure analysis · run #{run_id} · {n} trades ===\n")
    print(df.to_string(index=False))

    # ---- aggregates ----
    modeled = df["modeled_loss"].sum()
    risk_avg = df["risk_taken"].mean()
    risk_max = df["risk_taken"].max()
    saved_by_rev = df[(df["reversal_pnl"].notna()) & (df["day_net"] > 0)]
    day_pos = (df["day_net"] > 0).sum()
    hold_better = (df["hold_eod"] > df["modeled_loss"]).sum()
    hold_eod_sum = df["hold_eod"].sum()
    opp_pos = (df["opp_eod"] > 0).sum()

    print("\n=== Summary ===")
    print(f"  Modeled loss booked on these trades      : {modeled:+.2f}  (just slippage)")
    print(f"  Real risk taken (base-SL) avg / max      : ${risk_avg:.2f} / ${risk_max:.2f} per trade")
    print(f"  Days that still ended NET POSITIVE       : {day_pos}/{n}  (reversal/other legs rescued them)")
    print(f"  Of those, rescued by the reversal leg    : {len(saved_by_rev)}")
    print(f"  'Hold original dir to EOD' beats BE-stop  : {hold_better}/{n}  (sum {hold_eod_sum:+.2f}/unit)")
    print(f"  'Opposite dir to EOD' would be positive  : {opp_pos}/{n}")
    print(f"\nCSV: {outdir / 'be_stop_analysis.csv'}")


if __name__ == "__main__":
    main()
