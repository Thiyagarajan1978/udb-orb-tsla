#!/usr/bin/env python
"""UDB-ORB-TSLA command line.

Commands
  init-db     Create the SQLite schema.
  fetch       Pull TSLA 5m bars from FMP into the DB/cache for a date range.
  backtest    Run the faithful engine over a range, persist a run, print the summary.
  live        Start the alerts-only live loop (or a single --once poll).
  tune        Walk-forward parameter search over stored/fetched history.

Examples
  python cli.py backtest --start 2024-01-02 --end 2024-12-31
  python cli.py live --once
  python cli.py tune --start 2022-01-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

# make `udb_orb` importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from udb_orb.config import db_path, load_config  # noqa: E402


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _print_summary(run_id, summ) -> None:
    s = summ
    print(f"\n=== Backtest summary (run_id={run_id}) ===")
    print(f"  Trades        : {s.trades}  (success {s.successes} / failure {s.failures})")
    print(f"  Win rate      : {s.win_rate:.1f}%   [success = net > 0; BE Stop counts as failure]")
    print(f"  BE-Stop fails : {s.be_stop_failures}")
    print(f"  Net P&L       : {s.net_pnl:+.2f}")
    print(f"  Profit factor : {('%.2f' % s.profit_factor) if s.profit_factor else 'n/a'}")
    print(f"  Expectancy    : {s.expectancy:+.3f} / trade")
    print(f"  Avg W / L     : +{s.avg_win:.2f} / -{s.avg_loss_abs:.2f}")
    print(f"  Best / Worst d: {('%+.2f' % s.best_day) if s.best_day is not None else 'n/a'} / "
          f"{('%+.2f' % s.worst_day) if s.worst_day is not None else 'n/a'}")
    print(f"  Trade days    : {s.trade_days}/{s.total_days} ({s.trade_day_pct:.1f}%)")
    print(f"  Reversals     : {s.reversal_entries}")
    print(f"  Exits  TP {s.tp_exits} · BaseSL {s.base_sl_exits} · BE-Trail {s.be_trail_exits} · "
          f"BE-Stop {s.be_stop_exits} · Part {s.partial_exits} · VWAP {s.vwap_trail_exits} · EOD {s.eod_exits}")


def cmd_init_db(args, cfg):
    from udb_orb.db.database import Database
    with Database(db_path(cfg)) as db:
        print(f"DB ready at {db.path}")


def cmd_fetch(args, cfg):
    from udb_orb.config import cache_dir
    from udb_orb.data.fmp_client import fetch_5min, rth_only
    from udb_orb.db.database import Database
    bars = rth_only(fetch_5min(cfg["symbol"], _d(args.start), _d(args.end),
                               cache_dir=cache_dir(cfg), use_cache=not args.no_cache))
    with Database(db_path(cfg)) as db:
        n = db.upsert_bars(cfg["symbol"], bars)
    print(f"Fetched & stored {n} RTH 5m bars for {cfg['symbol']} {args.start}..{args.end}")


def cmd_backtest(args, cfg):
    from udb_orb.backtest.runner import run_backtest
    run_id, summ = run_backtest(cfg, _d(args.start), _d(args.end),
                                use_cache=not args.no_cache, from_db=args.from_db, notes=args.notes)
    _print_summary(run_id, summ)


def cmd_live(args, cfg):
    from udb_orb.live.runner import run_live
    run_live(cfg, once=args.once)


def cmd_tune(args, cfg):
    from udb_orb.backtest.runner import load_bars
    from udb_orb.tuning.walk_forward import summarize_folds, walk_forward
    bars = load_bars(cfg, _d(args.start), _d(args.end), use_cache=not args.no_cache, from_db=args.from_db)
    folds = walk_forward(cfg, bars, is_days=args.is_days, oos_days=args.oos_days)
    print(f"\n=== Walk-forward ({len(folds)} folds) ===")
    for f in folds:
        print(f"  fold {f.fold}: OOS {f.oos_start}..{f.oos_end} "
              f"net {f.oos_net:+.2f} ({f.oos_trades} tr, WR {f.oos_win_rate:.0f}%) best={f.best_params}")
    print("\nConsensus:", summarize_folds(folds))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="udb-orb", description="TSLA 5m ORB — Adaptive TP + Reversal")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")

    pf = sub.add_parser("fetch")
    pf.add_argument("--start", required=True)
    pf.add_argument("--end", required=True)
    pf.add_argument("--no-cache", action="store_true")

    pb = sub.add_parser("backtest")
    pb.add_argument("--start", required=True)
    pb.add_argument("--end", required=True)
    pb.add_argument("--no-cache", action="store_true")
    pb.add_argument("--from-db", action="store_true", help="use bars already in the DB")
    pb.add_argument("--notes", default="")

    pl = sub.add_parser("live")
    pl.add_argument("--once", action="store_true", help="single poll then exit")

    pt = sub.add_parser("tune")
    pt.add_argument("--start", required=True)
    pt.add_argument("--end", required=True)
    pt.add_argument("--no-cache", action="store_true")
    pt.add_argument("--from-db", action="store_true")
    pt.add_argument("--is-days", type=int, default=120)
    pt.add_argument("--oos-days", type=int, default=30)

    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    {
        "init-db": cmd_init_db, "fetch": cmd_fetch, "backtest": cmd_backtest,
        "live": cmd_live, "tune": cmd_tune,
    }[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
