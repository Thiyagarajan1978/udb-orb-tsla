"""Live loop (ALERTS-ONLY).

Each poll: pull the last few days of TSLA 5m bars from FMP, drop any still-forming bar,
run the faithful engine over them, diff the event stream against what we've already seen,
then alert + persist the new events. No broker orders are ever placed.

Restart-safe: on startup it seeds the "seen" set from events already stored across the same
lookback window the engine replays, so a restart neither re-alerts today's events nor
re-appends prior sessions' to the DB.
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


def profile_label(cfg: dict[str, Any]) -> str:
    """Short id for this profile (B1 / C1). Falls back to the long profile name.

    B1 and C1 share a profile NAME and a db_path, so the label is what keeps their runs,
    events and alerts apart. Without it they cross-suppress each other — see _seed_seen.
    """
    prof = cfg.get("profile", {})
    return str(prof.get("label") or prof.get("name", "default"))


def _seed_seen(db: Database, symbol: str, day: date, label: str,
               lookback_days: int = 0) -> set[str]:
    """Re-dupe guard: events THIS profile has already recorded in the replay window.

    Two things this must get right:

    - **Scoped by label** (via the runs table). Filtering on symbol+day alone would let a B1
      event suppress the identical C1 event — the two profiles trade the same symbol off the
      same bars and routinely fire the same event type at the same timestamp.
    - **Covers the whole lookback window, not just today.** The engine replays `lookback_days`
      of bars, so its event stream always includes prior sessions. Seeding only from today
      would leave those unseen, and they would be re-appended to `events` on every restart.
    """
    seen: set[str] = set()
    first = day - timedelta(days=max(lookback_days, 0))
    q = ("SELECT e.ts, e.type, e.direction FROM events e "
         "JOIN runs r ON r.id = e.run_id "
         "WHERE e.symbol=? AND r.profile=? AND e.ts >= ?")
    for row in db.conn.execute(q, (symbol, label, first.isoformat())).fetchall():
        seen.add(f"{pd.Timestamp(row['ts']).isoformat()}|{row['type']}|{row['direction']}")
    return seen


def poll_once(cfg: dict[str, Any], db: Database, run_id: int, notifier: Notifier,
              seen: set[str], *, verbose: bool = True, label: str | None = None) -> int:
    """One refresh. Returns the count of new events alerted."""
    tag = f"live {label}" if label else "live"
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
            print(f"[{tag}] {now:%H:%M:%S} no closed bars yet")
        return 0

    db.upsert_bars(symbol, bars)
    # today's session is still open -> its newest bar is NOT a flatten bar (see OrbEngine.run)
    result = run_engine(bars, params, enh, open_session=now.date())

    new_events = [e for e in result.events if _event_key(e) not in seen]
    if not new_events:
        if verbose:
            print(f"[{tag}] {now:%H:%M:%S} up to date ({len(result.events)} events, 0 new)")
        return 0

    # The engine needs a multi-day lookback for context, so its event stream always includes
    # prior sessions. Those are worth PERSISTING but must never be alerted: on the first run
    # under a new profile label the seen-set is empty, and without this guard the very first
    # poll would mail every event from the last `lookback_days` days as if it just happened.
    today = now.date()
    alerted_ids = []
    ids = db.append_events(run_id, symbol, new_events)
    n_alerted = n_backfill = 0
    for e, eid in zip(new_events, ids):
        seen.add(_event_key(e))
        is_today = pd.Timestamp(e.ts).date() == today
        if is_today:
            if notifier.notify(e):
                alerted_ids.append(eid)
            n_alerted += 1
            if verbose:
                print(f"[{tag}] NEW {e.type} {e.direction} @ {e.price:.2f} ({e.ts})")
        else:
            n_backfill += 1
    if alerted_ids:
        db.mark_alerted(alerted_ids)
    if n_backfill and verbose:
        print(f"[{tag}] recorded {n_backfill} prior-session event(s) silently (no alert)")
    return n_alerted


def run_live(cfg: dict[str, Any], *, once: bool = False, verbose: bool = True) -> None:
    run_live_multi([cfg], once=once, verbose=verbose)


def run_live_multi(cfgs: list[dict[str, Any]], *, once: bool = False,
                   verbose: bool = True) -> None:
    """Poll several profiles in ONE process, one after another per cycle.

    The traded set is B1 + C1 (see CLAUDE.md), and they must run together: same symbol, same
    bars, different levels. Running them as one process means one Task Scheduler entry, one
    shared bar fetch per cycle instead of N, and no chance of one being silently dead while
    the other runs. Each profile keeps its own run_id, notifier and seen-set, keyed on its
    label, so their alerts never merge or suppress one another.
    """
    if not cfgs:
        raise ValueError("run_live_multi needs at least one config")

    poll_s = int(cfgs[0].get("live", {}).get("poll_seconds", 30))
    labels = [profile_label(c) for c in cfgs]
    if len(set(labels)) != len(labels):
        raise ValueError(f"profile labels must be unique, got {labels} — "
                         "set a distinct `profile.label` in each config")

    # All profiles share one DB file; open it once.
    with Database(db_path(cfgs[0])) as db:
        ctxs = []
        for cfg, label in zip(cfgs, labels):
            symbol, tf = cfg["symbol"], int(cfg["timeframe_minutes"])
            lookback = int(cfg.get("live", {}).get("lookback_days", 3))
            run_id = db.create_run(
                kind="live", symbol=symbol, profile=label,
                start_date=str(date.today()), end_date=None, config=cfg,
                enhancements=cfg.get("enhancements", {}),
                notes=f"live session ({label})",
            )
            ctxs.append({
                "cfg": cfg, "label": label, "run_id": run_id,
                "notifier": Notifier(cfg, symbol, tf, label=label),
                "seen": _seed_seen(db, symbol, date.today(), label, lookback),
            })
            if verbose:
                print(f"[live] {label}: run_id={run_id} symbol={symbol} "
                      f"seeded {len(ctxs[-1]['seen'])} prior event(s) "
                      f"({lookback}d window)")
        if verbose:
            chans = ctxs[0]["notifier"].channels or "console"
            print(f"[live] profiles={labels} alerts={chans} poll={poll_s}s once={once}")

        def cycle() -> None:
            for c in ctxs:
                try:
                    poll_once(c["cfg"], db, c["run_id"], c["notifier"], c["seen"],
                              verbose=verbose, label=c["label"])
                except Exception as exc:
                    # One profile failing (or one bad FMP response) must not take the other
                    # down -- a half-dead runner that looks alive is the worst outcome here.
                    print(f"[live] {c['label']} poll FAILED: {exc!r}")

        if once:
            cycle()
            return

        try:
            while True:
                cycle()
                _time.sleep(poll_s)
        except KeyboardInterrupt:
            print("\n[live] stopped by user")
