#!/usr/bin/env python
"""Bar-by-bar inspection of a single day: OR levels, triggers, and the price path.

Shows why a trade did / didn't happen so BE-stop and reversal behaviour can be validated
by eye. Uses stored bars.

Usage:  python scripts/inspect_day.py 2026-06-15 [2026-06-17 ...]
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
from udb_orb.engine.params import Params  # noqa: E402


def inspect(day: str, bars, p: Params):
    dbars = bars[[d.isoformat() == day for d in bars.index.date]].sort_index()
    if dbars.empty:
        print(f"{day}: no bars"); return
    orb = dbars[[t == p.market_open for t in dbars.index.time]]
    oh, ol = float(orb["high"].iloc[0]), float(orb["low"].iloc[0])
    w = oh - ol
    buf = w * p.buffer_pct_or / 100.0
    long_brk, short_brk = oh + buf, ol - buf
    mid = (oh + ol) / 2
    be_long = oh - p.be_retrace_trigger * w    # long BE trigger level
    be_short = ol + p.be_retrace_trigger * w   # short BE trigger level
    print(f"\n===== {day} =====")
    print(f"OR: high {oh:.2f} low {ol:.2f} width {w:.2f} mid {mid:.2f}")
    print(f"Long break {long_brk:.2f} (raw {oh:.2f}) | Short break {short_brk:.2f} (raw {ol:.2f})")
    print(f"BE trigger: long<= {be_long:.2f}  short>= {be_short:.2f}")
    print(f"{'time':<6}{'open':>8}{'high':>8}{'low':>8}{'close':>8}  notes")
    for ts, r in dbars.iterrows():
        o, h, l, c = r["open"], r["high"], r["low"], r["close"]
        notes = []
        if c > long_brk: notes.append("close>Lbrk")
        if c < short_brk: notes.append("close<Sbrk")
        if h > oh: notes.append("H>ORhi")
        if l < ol: notes.append("L<ORlo")
        print(f"{ts.strftime('%H:%M'):<6}{o:>8.2f}{h:>8.2f}{l:>8.2f}{c:>8.2f}  {' '.join(notes)}")


def main():
    days = sys.argv[1:]
    if not days:
        print("usage: python scripts/inspect_day.py YYYY-MM-DD [...]"); return
    cfg = load_config()
    p = Params.from_config(cfg)
    with Database(db_path(cfg)) as db:
        bars = rth_only(db.load_bars(cfg["symbol"]))
    for d in days:
        inspect(d, bars, p)


if __name__ == "__main__":
    main()
