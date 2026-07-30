#!/usr/bin/env python
"""Does flipping IMMEDIATELY on a BE Stop beat waiting for the opposite OR break?

Today every profile arms the reversal when the primary stops, but only ENTERS when price closes
back through the raw opposite OR boundary — often 30-60 min and ~1 OR width later. This script
prices the alternative: enter the flip on the SAME bar close that the BE stop filled at.

Variants
  base            current behaviour (reversal waits for the raw opposite OR break)
  imm-swing       flip at the BE-stop bar close; stop = the primary's failed swing extreme
  imm-orb         flip at the BE-stop bar close; stop = opposite OR boundary (swing fallback)
  imm-all-swing   as imm-swing but flips on ANY primary stop (Base SL / BE Trail / BE Stop)
  no-reversal     reversal off entirely (the floor)

Usage:
  python scripts/immediate_reversal_test.py                      # C1, last 2 months
  python scripts/immediate_reversal_test.py --profiles all --windows
  python scripts/immediate_reversal_test.py --profiles B1 --detail
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

import yaml  # noqa: E402

from udb_orb.config import db_path, load_config  # noqa: E402
from udb_orb.data.fmp_client import rth_only  # noqa: E402
from udb_orb.db.database import Database  # noqa: E402
from udb_orb.engine.metrics import summarize  # noqa: E402
from udb_orb.engine.orb_engine import run_engine  # noqa: E402
from udb_orb.engine.params import Params  # noqa: E402

ALL_STOPS = ["BE Stop", "BE Trail", "Base SL"]

PROFILES = {           # label -> config file
    "A1": "tsla_best_A.yaml",
    "B1": "tsla_best_B.yaml",
    "C1": "tsla_config_C1.yaml",
    "C2": "tsla_config_C.yaml",
    "D1": "tsla_config_D1.yaml",
}


def variants(base, full=True):
    def v(**rev):
        c = copy.deepcopy(base)
        c["enhancements"]["reversal_capture"].update(rev)
        return c

    out = [
        ("base (wait for OR break)", v()),
        ("imm-swing", v(immediate_on_be_stop=True, immediate_stop_mode="swing")),
        ("imm-orb", v(immediate_on_be_stop=True, immediate_stop_mode="or_boundary")),
    ]
    if full:
        off = copy.deepcopy(base)
        off["profile"]["use_reversal"] = False
        out += [
            ("imm-all-swing", v(immediate_on_be_stop=True, immediate_stop_mode="swing",
                                immediate_reasons=ALL_STOPS)),
            ("no-reversal", off),
        ]
    return out


def run(cfg, bars):
    res = run_engine(bars, Params.from_config(cfg), cfg["enhancements"])
    s = summarize(res)
    dn = defaultdict(float)
    for t in res.trades:
        dn[t.day] += t.pnl_total
    worst = min(dn.values()) if dn else 0.0
    eq = peak = dd = 0.0
    for day in sorted(dn):
        eq += dn[day]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    revs = [t for t in res.trades if t.is_reversal]
    rev_net = sum(t.pnl_total for t in revs)
    rev_wr = 100.0 * sum(1 for t in revs if t.pnl_total > 0) / len(revs) if revs else 0.0
    return s, worst, dd, len(revs), rev_net, rev_wr, res


def line(name, s, worst, dd, nrev, rnet, rwr, mark=""):
    pf = f"{s.profit_factor:.2f}" if s.profit_factor else "n/a"
    print(f"{name:<26}{s.net_pnl:>+9.1f}{s.trades:>7}{s.win_rate:>7.1f}{pf:>7}"
          f"{worst:>8.1f}{dd:>8.1f}   {nrev:>5}{rnet:>+9.1f}{rwr:>7.1f}  {mark}")


def header(title):
    print(f"\n{title}")
    print(f"{'variant':<26}{'net':>9}{'trades':>7}{'WR%':>7}{'PF':>7}{'worst':>8}{'maxDD':>8}"
          f"   {'#rev':>5}{'rev net':>9}{'revWR%':>7}")
    print("-" * 96)


def load_profile(name):
    c = yaml.safe_load((ROOT / "config" / PROFILES[name]).read_text(encoding="utf-8-sig"))
    c.setdefault("enhancements", {}).setdefault("reversal_capture", {})
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-27")
    ap.add_argument("--end", default="2026-07-24")
    ap.add_argument("--profiles", default="C1", help="comma list of A1,B1,C1,C2,D1 or 'all'")
    ap.add_argument("--windows", action="store_true", help="also run 1y / 2024-26 / 2022-26")
    ap.add_argument("--years", action="store_true", help="per-YEAR base vs imm-orb, all profiles")
    ap.add_argument("--detail", action="store_true", help="per-day BE-stop flip detail")
    args = ap.parse_args()

    profs = list(PROFILES) if args.profiles.lower() == "all" else \
        [p.strip().upper() for p in args.profiles.split(",")]

    cfg = load_config()
    with Database(db_path(cfg)) as db:
        allbars = rth_only(db.load_bars(cfg["symbol"]))

    def slice_bars(s, e):
        s0 = datetime.strptime(s, "%Y-%m-%d").date()
        e0 = datetime.strptime(e, "%Y-%m-%d").date()
        return allbars[[s0 <= d <= e0 for d in allbars.index.date]]

    if args.years:
        # The window totals hide a regime split; per-year is the decisive view.
        years = [2022, 2023, 2024, 2025, 2026]
        print("Per-year net P&L per unit:  base | imm-orb | delta   (+ = immediate flip better)")
        print(f"{'profile':<9}" + "".join(f"{y:>26}" for y in years))
        print(f"{'':<9}" + "".join(f"{'base':>8}{'imm-orb':>9}{'delta':>9}" for _ in years))
        print("-" * (9 + 26 * len(years)))
        tot = {y: [0.0, 0.0] for y in years}
        for prof in profs:
            b0 = load_profile(prof)
            row = f"{prof:<9}"
            for y in years:
                bars = allbars[[d.year == y for d in allbars.index.date]]
                vs = dict(variants(b0, full=False))
                nb = run(vs["base (wait for OR break)"], bars)[0].net_pnl
                ni = run(vs["imm-orb"], bars)[0].net_pnl
                tot[y][0] += nb
                tot[y][1] += ni
                row += f"{nb:>+8.1f}{ni:>+9.1f}{ni - nb:>+9.1f}"
            print(row)
        print("-" * (9 + 26 * len(years)))
        print(f"{'SUM':<9}" + "".join(
            f"{tot[y][0]:>+8.1f}{tot[y][1]:>+9.1f}{tot[y][1] - tot[y][0]:>+9.1f}" for y in years))
        print("\n(2026 = YTD; 2022-23 were never part of any discovery = true out-of-sample)")
        return

    windows = [(f"{args.start} -> {args.end}  (the 2-month ask)", args.start, args.end)]
    if args.windows:
        windows += [
            ("1 year  2025-07-25 -> 2026-07-24", "2025-07-25", "2026-07-24"),
            ("2024-2026", "2024-01-02", "2026-07-24"),
            ("2022-2026 (full)", "2022-01-03", "2026-07-24"),
        ]

    scoreboard = defaultdict(dict)
    detail_res = {}
    multi = len(profs) > 1
    for prof in profs:
        base_cfg = load_profile(prof)
        for title, s, e in windows:
            bars = slice_bars(s, e)
            ndays = len(set(bars.index.date))
            header(f"{prof} - {title}   [{ndays} sessions]")
            nets = {}
            outs = {}
            for name, c in variants(base_cfg, full=not multi):
                outs[name] = run(c, bars)
                nets[name] = outs[name][0].net_pnl
            best = max(nets, key=nets.get)
            for name in outs:
                sm, worst, dd, nrev, rnet, rwr, res = outs[name]
                line(name, sm, worst, dd, nrev, rnet, rwr, mark="<- best" if name == best else "")
                if s == args.start:
                    detail_res[(prof, name)] = res
            scoreboard[prof][title] = (nets, best)

    if multi:
        print("\n\n=== SCOREBOARD: net P&L per unit, base vs immediate flip ===")
        print(f"{'profile':<8}{'window':<36}{'base':>10}{'imm-swing':>11}{'imm-orb':>10}"
              f"{'best delta':>12}")
        print("-" * 87)
        for prof in profs:
            for title, (nets, best) in scoreboard[prof].items():
                b = nets["base (wait for OR break)"]
                bestimm = max(nets["imm-swing"], nets["imm-orb"])
                flag = "  IMM WINS" if bestimm > b else ""
                print(f"{prof:<8}{title:<36}{b:>+10.1f}{nets['imm-swing']:>+11.1f}"
                      f"{nets['imm-orb']:>+10.1f}{bestimm - b:>+12.1f}{flag}")
            print()

    if args.detail:
        prof = profs[0]
        base_res = detail_res[(prof, "base (wait for OR break)")]
        imm_res = detail_res[(prof, "imm-swing")]

        def by_day(res):
            d = defaultdict(list)
            for t in res.trades:
                d[t.day].append(t)
            return d

        b, i = by_day(base_res), by_day(imm_res)
        print(f"\nPer-day detail ({prof}) on days whose PRIMARY ended in a BE Stop")
        print(f"{'day':<12}{'prim':<16}{'base flip':<34}{'imm flip':<34}{'base':>8}{'imm':>8}{'delta':>8}")
        print("-" * 120)
        tb = ti = 0.0
        for d in sorted(set(b) | set(i)):
            bl, il = b.get(d, []), i.get(d, [])
            prim = next((t for t in bl if not t.is_reversal), None)
            if prim is None or "BE Stop" not in prim.reason:
                continue

            def fmt(legs):
                r = next((t for t in legs if t.is_reversal), None)
                if r is None:
                    return "-- none --"
                return (f"{r.direction} {r.entry_ts:%H:%M}@{r.entry_price:.2f}"
                        f" -> {r.exit_ts:%H:%M}@{r.exit_price:.2f} {r.reason.replace('Rev ','')}"
                        f" x{r.qty:.2f}")
            pb = sum(t.pnl_total for t in bl)
            pi = sum(t.pnl_total for t in il)
            tb += pb
            ti += pi
            print(f"{d:<12}{prim.direction + ' ' + prim.reason:<16}{fmt(bl):<34}{fmt(il):<34}"
                  f"{pb:>+8.2f}{pi:>+8.2f}{pi - pb:>+8.2f}")
        print("-" * 120)
        print(f"{'TOTAL (BE-Stop days only)':<96}{tb:>+8.2f}{ti:>+8.2f}{ti - tb:>+8.2f}")


if __name__ == "__main__":
    main()
