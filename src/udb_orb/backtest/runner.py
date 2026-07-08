"""Backtest runner: pull/reuse bars, run the engine, persist to the DB, return summary."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import cache_dir, db_path
from ..data.fmp_client import fetch_5min, rth_only
from ..db.database import Database
from ..engine.metrics import Summary, summarize
from ..engine.orb_engine import run_engine
from ..engine.params import Params


def load_bars(cfg: dict[str, Any], start: date, end: date, *, use_cache: bool = True,
              from_db: bool = False) -> pd.DataFrame:
    """Load TSLA 5m RTH bars either from FMP (default) or the local DB (--from-db)."""
    symbol = cfg["symbol"]
    if from_db:
        with Database(db_path(cfg)) as db:
            df = db.load_bars(symbol, str(start), str(end) + "T23:59:59")
        return rth_only(df)
    df = fetch_5min(symbol, start, end, cache_dir=cache_dir(cfg), use_cache=use_cache)
    return rth_only(df)


def run_backtest(cfg: dict[str, Any], start: date, end: date, *, use_cache: bool = True,
                 from_db: bool = False, notes: str = "") -> tuple[int, Summary]:
    symbol = cfg["symbol"]
    params = Params.from_config(cfg)
    enh = cfg.get("enhancements", {})

    bars = load_bars(cfg, start, end, use_cache=use_cache, from_db=from_db)
    if bars.empty:
        raise RuntimeError("No bars loaded for the requested range.")

    result = run_engine(bars, params, enh)
    summ = summarize(result)

    with Database(db_path(cfg)) as db:
        db.upsert_bars(symbol, bars)
        run_id = db.create_run(
            kind="backtest", symbol=symbol, profile=cfg["profile"]["name"],
            start_date=str(start), end_date=str(end), config=cfg, enhancements=enh, notes=notes,
        )
        db.write_result(run_id, symbol, result)
    return run_id, summ
