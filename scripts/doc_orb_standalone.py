#!/usr/bin/env python
"""The ORB+IBS/CLV handover doc's system, implemented STANDALONE and run head-to-head vs C1.

  python scripts/doc_orb_standalone.py --start 2022-01-03 --end 2026-07-29 --years

The previous test (scripts/ibs_gate_test.py) bolted the doc's FILTERS onto C1's ORB. This
one instead implements the doc's own §8 rules end to end, so its filters are judged inside
the framework they were designed for. Its ORB differs from C1's in four ways we have already
measured separately as worse on TSLA — 15-minute OR, 09:45-11:00 entry window, a fixed 1.5R
target, and no reversal leg — so this asks two questions at once:

  1. does the doc's system beat C1?                       (compare to the C1 line)
  2. do the IBS filters help INSIDE the doc's own system?  (compare 'ORB only' to the rest)

Question 2 is the fair one for the concept, and it is the reason to run this at all.

FAITHFUL TO THE DOC (§8): 15m opening range, entry only 09:45-11:00, breakout buffer 5% of
OR width, close must be outside the buffered range, stop at the OPPOSITE opening-range side,
target 1.5R, max one trade per day, forced exit 15:50 ET, and the conservative same-candle
rule — when a bar touches both stop and target, the stop is assumed to happen first.

HELD COMMON WITH C1 so the comparison is honest: same 5m FMP bars, same $0.10/unit cost
charged once per round trip, entries filled at the breakout bar's close, 1 unit per trade.

STOP FILL: the doc's touch rule is intrabar/gap-aware ('touch'); C1's adopted realism is a
close-triggered stop ('close'). Both are reported — mixing the two would confound the
comparison, since that choice alone is worth +42-46% on this instrument.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OR_END = time(9, 45)
WIN_START, WIN_END = time(9, 45), time(11, 0)
EOD = time(15, 50)


def ibs_of(o, h, l, c):
    rng = h - l
    if rng <= 0:
        return 0.5, 0.0, 0.0, 0.0, True
    return ((c - l) / rng, abs(c - o) / rng,
            (h - max(o, c)) / rng, (min(o, c) - l) / rng, False)


def backtest(bars: pd.DataFrame, *, use_ibs=True, use_range_atr=True, use_quality=True,
             use_pcp=True, stop_mode="touch", target_r=1.5, buffer_pct=0.05,
             slip=0.10) -> pd.DataFrame:
    b = bars.copy()
    tr = pd.concat([b.high - b.low, (b.high - b.close.shift()).abs(),
                    (b.low - b.close.shift()).abs()], axis=1).max(axis=1)
    b["iatr"] = tr.rolling(14).mean().shift(1)      # shifted: no self-reference (doc §5)
    day = b.groupby(b.index.date)
    daily = pd.DataFrame({"h": day.high.max(), "l": day.low.min(), "c": day.close.last()})
    rng_d = daily.h - daily.l
    daily["ibs"] = ((daily.c - daily.l) / rng_d.where(rng_d > 0)).fillna(0.5)
    prev_ibs, prev_close = daily.ibs.shift(1), daily.c.shift(1)

    out = []
    for d, g in b.groupby(b.index.date):
        orb = g[g.index.time < OR_END]
        if len(orb) < 3 or d not in daily.index:
            continue
        or_hi, or_lo = float(orb.high.max()), float(orb.low.min())
        buf = (or_hi - or_lo) * buffer_pct
        p_ibs, p_cl = prev_ibs.get(d, np.nan), prev_close.get(d, np.nan)

        trade = None
        for ts, r in g[(g.index.time >= WIN_START) & (g.index.time <= WIN_END)].iterrows():
            o, h, l, c = float(r.open), float(r.high), float(r.low), float(r.close)
            direction = 1 if c > or_hi + buf else (-1 if c < or_lo - buf else 0)
            if not direction:
                continue
            ibs, body, up_w, lo_w, zero = ibs_of(o, h, l, c)
            if zero:                                            # doc §4
                continue
            fav = ibs if direction == 1 else 1 - ibs
            if use_ibs:
                if fav < 0.75:
                    continue
                if np.isnan(p_ibs) or (p_ibs < 0.75 if direction == 1 else p_ibs > 0.25):
                    continue
            if use_range_atr:
                ia = r.iatr
                if not (ia == ia and ia) or not (0.20 <= (h - l) / ia <= 0.80):
                    continue
            if use_quality:
                if body < 0.50 or (up_w if direction == 1 else lo_w) > 0.25:
                    continue
            if use_pcp and (np.isnan(p_cl) or (c - p_cl) * direction < 0):
                continue
            stop = or_lo if direction == 1 else or_hi          # opposite OR side (doc §8)
            risk = (c - stop) * direction
            if risk <= 0:
                continue
            trade = dict(day=str(d), dir=direction, entry_ts=ts, entry=c, stop=stop,
                         target=c + direction * target_r * risk, risk=risk)
            break
        if trade is None:
            continue

        after = g[g.index > trade["entry_ts"]]
        exit_px, reason = None, "EOD"
        for ts, r in after.iterrows():
            o, h, l, c = float(r.open), float(r.high), float(r.low), float(r.close)
            dr, stp, tgt = trade["dir"], trade["stop"], trade["target"]
            if stop_mode == "touch":
                # gap-aware, and stop is checked BEFORE target on the same bar (doc §8)
                if (l <= stp if dr == 1 else h >= stp):
                    exit_px = o if ((o <= stp) if dr == 1 else (o >= stp)) else stp
                    reason = "Stop"
                    break
            else:                                               # close-triggered, C1's realism
                if (c <= stp if dr == 1 else c >= stp):
                    exit_px, reason = c, "Stop"
                    break
            if (h >= tgt if dr == 1 else l <= tgt):
                exit_px, reason = tgt, "Target"
                break
            if ts.time() >= EOD:
                exit_px, reason = c, "EOD"
                break
        if exit_px is None:
            exit_px = float(after.iloc[-1].close) if len(after) else trade["entry"]
        pnl = (exit_px - trade["entry"]) * trade["dir"] - slip
        out.append({**trade, "exit": exit_px, "reason": reason, "pnl": pnl,
                    "r_mult": pnl / trade["risk"]})
    return pd.DataFrame(out)


def stats(df: pd.DataFrame) -> str:
    if df.empty:
        return f"{0:>4}tr  (no trades)"
    w, lo = df[df.pnl > 0], df[df.pnl <= 0]
    pf = (w.pnl.sum() / abs(lo.pnl.sum())) if len(lo) and lo.pnl.sum() else float("inf")
    worst = df.groupby("day").pnl.sum().min()
    return (f"{len(df):>4}tr  WR {100*len(w)/len(df):5.1f}%  net {df.pnl.sum():>9.2f}  "
            f"PF {pf:5.2f}  exp {df.pnl.mean():>6.2f}  worst {worst:>7.2f}  "
            f"medR {df.r_mult.median():>5.2f}")


VARIANTS = [
    ("ORB only (no IBS)",   dict(use_ibs=False, use_range_atr=False, use_quality=False,
                                 use_pcp=False)),
    ("+ both IBS filters",  dict(use_ibs=True, use_range_atr=False, use_quality=False,
                                 use_pcp=False)),
    ("+ range/ATR",         dict(use_ibs=True, use_range_atr=True, use_quality=False,
                                 use_pcp=False)),
    ("+ prior-close prog.", dict(use_ibs=True, use_range_atr=True, use_quality=False,
                                 use_pcp=True)),
    ("FULL doc system",     dict(use_ibs=True, use_range_atr=True, use_quality=True,
                                 use_pcp=True)),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="the handover doc's ORB+IBS system, standalone")
    ap.add_argument("--start", default="2022-01-03")
    ap.add_argument("--end", default="2026-07-29")
    ap.add_argument("--config", default="config/tsla_config_C1.yaml")
    ap.add_argument("--years", action="store_true")
    args = ap.parse_args(argv)

    from udb_orb.config import load_config
    from udb_orb.backtest.runner import load_bars
    from udb_orb.engine.orb_engine import run_engine
    from udb_orb.engine.params import Params
    from udb_orb.engine.metrics import summarize

    cfg = load_config(ROOT / args.config)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    bars = load_bars(cfg, start, end)
    print(f"{len(bars)} 5m bars  {bars.index.min()} .. {bars.index.max()}")

    c1 = summarize(run_engine(bars, Params.from_config(cfg), cfg.get("enhancements", {})))
    pf = "  n/a" if c1.profit_factor is None else f"{c1.profit_factor:5.2f}"
    print(f"\n{'C1 (our system)':<24} {c1.trades:>4}tr  WR {c1.win_rate:5.1f}%  "
          f"net {c1.net_pnl:>9.2f}  PF {pf}  exp {c1.expectancy:>6.2f}  "
          f"worst {c1.worst_day or 0:>7.2f}")

    for mode in ("touch", "close"):
        print(f"\n{'=' * 108}\nDOC SYSTEM — stop fill: {mode.upper()}"
              f"{'  (the doc s own rule)' if mode == 'touch' else '  (C1 s adopted realism)'}"
              f"\n{'=' * 108}")
        for name, kw in VARIANTS:
            print(f"{name:<24} " + stats(backtest(bars, stop_mode=mode, **kw)))

    if args.years:
        print(f"\n{'=' * 108}\nPER YEAR — full doc system vs C1\n{'=' * 108}")
        for yr in sorted({d.year for d in bars.index.date}):
            yb = bars[bars.index.year == yr]
            if yb.empty:
                continue
            yc1 = summarize(run_engine(yb, Params.from_config(cfg), cfg.get("enhancements", {})))
            print(f"\n  {yr}")
            print(f"    {'C1 (our system)':<24} {yc1.trades:>4}tr  WR {yc1.win_rate:5.1f}%  "
                  f"net {yc1.net_pnl:>9.2f}  exp {yc1.expectancy:>6.2f}")
            for name, kw in (("ORB only (no IBS)", VARIANTS[0][1]),
                             ("FULL doc system", VARIANTS[-1][1])):
                for mode in ("touch", "close"):
                    print(f"    {name + ' [' + mode + ']':<24} "
                          + stats(backtest(yb, stop_mode=mode, **kw)))


if __name__ == "__main__":
    main()
