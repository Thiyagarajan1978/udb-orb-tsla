#!/usr/bin/env python
"""Full decision trail for a month: OR levels, trigger, entry, exit — one block per trade.

Shows WHERE each ORB decision was made so trigger-logic tweaks can be reasoned about.
Usage:  python scripts/july_detail.py 2026-07
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.data.fmp_client import rth_only  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402
from udb_orb.engine.orb_engine import run_engine  # noqa: E402
from udb_orb.engine.params import Params  # noqa: E402


def main():
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-07"
    cfg = load_config()
    if "--confirm" in sys.argv:
        cfg["enhancements"]["confirm_breakout"] = {"enabled": True, "require_trend_candle": True}
    p = Params.from_config(cfg)
    with Database(db_path(cfg)) as db:
        bars = rth_only(db.load_bars(cfg["symbol"]))
    seg = bars[[str(d).startswith(month) for d in bars.index.date]]
    if seg.empty:
        print(f"No bars for {month}"); return
    res = run_engine(seg, p, cfg["enhancements"])

    # index trades + events by day
    ev_by_day: dict[str, list] = {}
    for e in res.events:
        ev_by_day.setdefault(str(e.ts.date()), []).append(e)
    tr_by_day: dict[str, list] = {}
    for t in res.trades:
        tr_by_day.setdefault(t.day, []).append(t)

    buf = p.buffer_pct_or / 100.0
    print(f"=== {cfg['symbol']} {month} — full decision trail (buffer {p.buffer_pct_or:.0f}% OR, "
          f"BE trig {p.be_retrace_trigger}, TP x{p.adaptive_tp_scale}) ===")

    for day, dbars in seg.groupby(seg.index.date):
        dstr = str(day)
        dbars = dbars.sort_index()
        orow = dbars[[t == p.market_open for t in dbars.index.time]]
        if orow.empty:
            continue
        oh, ol = float(orow["high"].iloc[0]), float(orow["low"].iloc[0])
        w = oh - ol
        lbrk, sbrk = oh + w * buf, ol - w * buf
        tp_dist = max(p.adaptive_tp_min, w * p.adaptive_tp_scale)
        dayname = pd.Timestamp(day).strftime("%a")
        trades = tr_by_day.get(dstr, [])
        header = f"\n{'-'*100}\n{dstr} {dayname}   OR {ol:.2f}-{oh:.2f} (w {w:.2f})   Long>{lbrk:.2f}  Short<{sbrk:.2f}  TPdist {tp_dist:.2f}"
        if not trades:
            nt = [d for d in res.days if d.date == dstr]
            reason = nt[0].no_trade_reason if nt else "?"
            print(header + f"   ==> NO TRADE ({reason})")
            continue
        print(header)
        for e in ev_by_day.get(dstr, []):
            ttime = e.ts.strftime("%H:%M")
            if e.type in ("primary_entry", "reversal_entry"):
                kind = "PRIMARY" if e.type == "primary_entry" else "REVERSAL"
                print(f"   {ttime}  ENTER {kind:<8} {e.direction:<8} @ {e.price:7.2f}  qty {e.qty:.2f}")
            elif e.type == "be_retrace_fired":
                print(f"   {ttime}    be-retrace fired  -> stop moved to entry")
            elif e.type == "partial_exit":
                print(f"   {ttime}    PARTIAL 25%      {e.direction:<8} @ {e.price:7.2f}  +{e.pnl:.2f}")
            else:
                lab = e.type.replace("_", " ")
                pnl = f"{e.pnl:+.2f}" if e.pnl is not None else ""
                print(f"   {ttime}  EXIT  {lab:<14} {e.direction:<8} @ {e.price:7.2f}  {pnl}  ({e.reason})")
        daynet = sum(t.pnl_total for t in trades)
        print(f"   day net {daynet:+.2f}")

    net = res.net_pnl()
    print(f"\n{'='*100}\nMONTH: {len(res.trades)} trades, net {net:+.2f}")


if __name__ == "__main__":
    main()
