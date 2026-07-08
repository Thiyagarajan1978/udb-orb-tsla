"""Live loop (ALERTS-ONLY).

Each poll: pull the last few days of TSLA 5m bars from FMP, drop any still-forming bar,
run the faithful engine over them, diff the event stream against what we've already seen,
then alert + persist the new events. No broker orders are ever placed.

Restart-safe: on startup it seeds the "seen" set from events already stored for today, so
a restart mid-session does not re-alert past events.
"""
from __future__ import annotations

import time as _time
from datetime import date, timedelta
from typing import Any

import pandas as pd

from ..alerts.notifier import Notifier
from ..config import cache_dir, db_path
from ..data.fmp_client import fetch_5min, rth_only
from ..db.database import Database
from ..engine.orb_engine import run_engine
from ..engine.params import Params

_TZ = "America/New_York"


def _event_key(e) -> str:
    return f"{pd.Timestamp(e.ts).isoformat()}|{e.type}|{e.direction}"


def _closed_bars(df: pd.DataFrame, tf_min: int, now: pd.Timestamp) -> pd.DataFrame:
    """Keep only bars whose close time (ts + tf) has passed — drop the forming bar."""
    if df.empty:
        return df
    close_time = df.index + pd.Timedelta(minutes=tf_min)
    return df[close_time <= now]


def _seed_seen(db: Database, symbol: str, day: date) -> set[str]:
    seen: set[str] = set()
    q = "SELECT ts, type, direction FROM events WHERE symbol=? AND ts LIKE ?"
    for row in db.conn.execute(q, (symbol, f"{day.isoformat()}%")).fetchall():
        seen.add(f"{pd.Timestamp(row['ts']).isoformat()}|{row['type']}|{row['direction']}")
    return seen


def poll_once(cfg: dict[str, Any], db: Database, run_id: int, notifier: Notifier,
              seen: set[str], *, verbose: bool = True) -> int:
    """One refresh. Returns the count of new events alerted."""
    symbol = cfg["symbol"]
    tf = int(cfg["timeframe_minutes"])
    params = Params.from_config(cfg)
    enh = cfg.get("enhancements", {})
    lookback = int(cfg.get("live", {}).get("lookback_days", 3))

    now = pd.Timestamp.now(tz=_TZ)
    start = (now - pd.Timedelta(days=lookback)).date()
    end = now.date()

    bars = fetch_5min(symbol, start, end, cache_dir=cache_dir(cfg), use_cache=False)
    bars = rth_only(bars)
    bars = _closed_bars(bars, tf, now)
    if bars.empty:
        if verbose:
            print(f"[live] {now:%H:%M:%S} no closed bars yet")
        return 0

    db.upsert_bars(symbol, bars)
    result = run_engine(bars, params, enh)

    new_events = [e for e in result.events if _event_key(e) not in seen]
    if not new_events:
        if verbose:
            print(f"[live] {now:%H:%M:%S} up to date ({len(result.events)} events, 0 new)")
        return 0

    ids = db.append_events(run_id, symbol, new_events)
    alerted_ids = []
    for e, eid in zip(new_events, ids):
        if notifier.notify(e):
            alerted_ids.append(eid)
        seen.add(_event_key(e))
        if verbose:
            print(f"[live] NEW {e.type} {e.direction} @ {e.price:.2f} ({e.ts})")
    if alerted_ids:
        db.mark_alerted(alerted_ids)
    return len(new_events)


def run_live(cfg: dict[str, Any], *, once: bool = False, verbose: bool = True) -> None:
    symbol = cfg["symbol"]
    tf = int(cfg["timeframe_minutes"])
    poll_s = int(cfg.get("live", {}).get("poll_seconds", 30))

    with Database(db_path(cfg)) as db:
        run_id = db.create_run(
            kind="live", symbol=symbol, profile=cfg["profile"]["name"],
            start_date=str(date.today()), end_date=None, config=cfg,
            enhancements=cfg.get("enhancements", {}), notes="live session",
        )
        notifier = Notifier(cfg, symbol, tf)
        seen = _seed_seen(db, symbol, date.today())
        if verbose:
            print(f"[live] run_id={run_id} symbol={symbol} profile={cfg['profile']['name']}")
            print(f"[live] alerts channels={notifier.channels or 'console'} poll={poll_s}s once={once}")

        if once:
            poll_once(cfg, db, run_id, notifier, seen, verbose=verbose)
            return

        try:
            while True:
                poll_once(cfg, db, run_id, notifier, seen, verbose=verbose)
                _time.sleep(poll_s)
        except KeyboardInterrupt:
            print("\n[live] stopped by user")
