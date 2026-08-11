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

# Seconds a bar must age PAST its close before FMP has finished aggregating it. Measured
# lags were 168 / 192 / 214s (2026-08-11), so this is one full 5m bar — the newest usable
# bar is always exactly one behind, which is both safe and easy to reason about. See
# _closed_bars for the measurement. Override with `live.bar_settle_seconds`.
DEFAULT_SETTLE_S = 300


def _event_key(e) -> str:
    return f"{pd.Timestamp(e.ts).isoformat()}|{e.type}|{e.direction}"


def _closed_bars(df: pd.DataFrame, tf_min: int, now: pd.Timestamp,
                 settle_s: int = DEFAULT_SETTLE_S) -> pd.DataFrame:
    """Keep only bars that are closed AND settled — drop the forming and the fresh ones.

    `close_time <= now` alone is NOT enough, and getting this wrong is silent and expensive.
    FMP keeps aggregating a 5m bar for a couple of minutes AFTER its nominal close, and
    serves the partial in the meantime. Measured live on 2026-08-11 by polling
    `/stable/historical-chart/5min` every 10s: the 14:50 bar (close 14:55:00) still read
    ``c=331.89 v=90,790`` at close+137s and only reached its final ``c=331.85 v=115,049``
    by close+168s. Three bars watched from forming settled at close **+168s, +192s and
    +214s** — hence a one-full-bar (300s) default rather than a tight fit to the maximum.

    Acting on the partial made the runner alert on prices that never existed: an audit of
    the first 11 live sessions found **52 of 87 events priced off a non-final bar close**,
    every one of them inside the final bar's high/low range (the partial-snapshot
    signature), worst case 2026-08-03 primary_entry logged 314.81 against a real close of
    318.00. It also produced phantom signals that a later poll silently replaced — three
    `primary_entry` alerts on 2026-08-10 when only the 10:00 one was real.

    So a bar is usable only once ``close_time + settle_s <= now``. The cost is alert
    latency: at the 300s default a 09:35 signal is mailed at ~09:40:30 rather than
    09:35:30. That is the price of the alert being TRUE, and it is a live-only concern —
    historical bars are already final, so backtests are unaffected either way.
    """
    if df.empty:
        return df
    close_time = df.index + pd.Timedelta(minutes=tf_min) + pd.Timedelta(seconds=max(settle_s, 0))
    return df[close_time <= now]


def _superseded_by(seen: set[str], e) -> str | None:
    """If this event replaces one already alerted for the same session, return its timestamp.

    Belt-and-braces behind the settle margin. Should FMP ever revise a bar beyond
    `bar_settle_seconds`, the engine's replay can move an event's timestamp — and because
    the re-alert guard keys on the exact (ts, type, direction), a moved timestamp reads as
    a brand-new signal and gets mailed as one. That is how 2026-08-10 produced three
    "enter now" alerts for a single trade. Same session + same event type + same direction
    is not a legitimate repeat for any event this system emits, so flag it loudly rather
    than letting the correction masquerade as a fresh signal.
    """
    day = pd.Timestamp(e.ts).date()
    for key in seen:
        ts_s, typ, direction = key.split("|", 2)
        if typ == e.type and direction == e.direction and pd.Timestamp(ts_s).date() == day:
            return ts_s
    return None


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
    settle_s = int(cfg.get("live", {}).get("bar_settle_seconds", DEFAULT_SETTLE_S))

    now = pd.Timestamp.now(tz=_TZ)
    start = (now - pd.Timedelta(days=lookback)).date()
    end = now.date()

    bars = fetch_5min(symbol, start, end, cache_dir=cache_dir(cfg), use_cache=False)
    bars = rth_only(bars)
    bars = _closed_bars(bars, tf, now, settle_s)
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
        is_today = pd.Timestamp(e.ts).date() == today
        prior = _superseded_by(seen, e) if is_today else None
        seen.add(_event_key(e))
        if is_today:
            if prior:
                # Never let a correction go out looking like a second trade.
                was = pd.Timestamp(prior).strftime("%H:%M")
                e.note = (f"{e.note} " if e.note else "") + (
                    f"*** CORRECTION: replaces the {e.type} alerted at {was} ET — "
                    f"that one came off a bar FMP had not finished aggregating. "
                    f"This supersedes it; do not treat it as a second signal.")
                print(f"[{tag}] !! SUPERSEDED {e.type} {was} -> {pd.Timestamp(e.ts):%H:%M} "
                      f"@ {e.price:.2f} (raise live.bar_settle_seconds if this recurs)")
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
