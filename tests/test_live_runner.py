"""Live-runner safety tests.

These cover the two failure modes that are invisible until they hit production:

1. B1 and C1 share a profile NAME and a db_path. If the re-alert guard is not scoped by
   profile label, one silently suppresses the other's alerts and you simply never hear about
   half your trades.
2. The engine replays a multi-day lookback for context, so its event stream always contains
   prior sessions. On the first poll under a new label the seen-set is empty -- without a
   same-day guard the runner mails every event from the last `lookback_days` days at once.

Both are alert-path bugs: the numbers stay right and nothing raises, so only a test catches them.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from udb_orb.alerts.notifier import format_event                      # noqa: E402
from udb_orb.db.database import Database                              # noqa: E402
from udb_orb.live.runner import _seed_seen, profile_label             # noqa: E402

_TZ = "America/New_York"


def ev(ts, type_="primary_entry", direction="L", price=100.0):
    t = pd.Timestamp(ts)
    t = t.tz_localize(_TZ) if t.tzinfo is None else t.tz_convert(_TZ)
    return SimpleNamespace(ts=t, type=type_, direction=direction,
                           price=price, qty=1.0, pnl=None, reason=None, note=None)


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "t.db") as d:
        yield d


def _run(db, label):
    return db.create_run(kind="live", symbol="TSLA", profile=label, start_date="2026-07-31",
                         end_date=None, config={}, enhancements={}, notes=label)


def test_profile_label_prefers_label_over_name():
    assert profile_label({"profile": {"name": "Adaptive TP", "label": "B1"}}) == "B1"
    assert profile_label({"profile": {"name": "Adaptive TP"}}) == "Adaptive TP"


def test_seen_set_is_scoped_by_profile_so_B1_cannot_suppress_C1(db):
    """The regression that matters: identical event, two profiles, same day."""
    day = date(2026, 7, 31)
    b1, c1 = _run(db, "B1"), _run(db, "C1")
    db.append_events(b1, "TSLA", [ev("2026-07-31 09:45")])

    assert len(_seed_seen(db, "TSLA", day, "B1")) == 1, "B1 must see its own event"
    assert _seed_seen(db, "TSLA", day, "C1") == set(), \
        "C1 must NOT inherit B1's event — that would suppress a real C1 alert"

    db.append_events(c1, "TSLA", [ev("2026-07-31 09:45")])
    assert len(_seed_seen(db, "TSLA", day, "C1")) == 1


def test_seen_set_covers_the_replay_lookback_not_just_today(db):
    """Prior sessions must seed too, or every restart re-appends them to `events`.

    The engine replays `lookback_days` of bars, so its event stream always includes them.
    """
    b1 = _run(db, "B1")
    db.append_events(b1, "TSLA", [ev("2026-07-29 09:45"), ev("2026-07-31 09:45")])
    day = date(2026, 7, 31)

    assert len(_seed_seen(db, "TSLA", day, "B1", 3)) == 2, \
        "a 3-day lookback must seed the 07-29 event — else it is re-inserted on restart"
    assert len(_seed_seen(db, "TSLA", day, "B1", 0)) == 1, "window is honoured, not ignored"
    assert _seed_seen(db, "TSLA", date(2026, 8, 5), "B1", 1) == set(), \
        "events outside the window stay out"


def test_alerts_carry_the_profile_label():
    """Two profiles trade the same symbol off the same bars; unlabelled alerts are ambiguous."""
    msg = format_event("TSLA", 5, ev("2026-07-31 09:45"), "C1")
    assert "UDB-ORB C1" in msg
    assert format_event("TSLA", 5, ev("2026-07-31 09:45")).startswith("[ENTRY] UDB-ORB |")


def _one_session_breakout():
    """OR bar 09:30-09:35 (299-303), a buffered break up, then a slow drift — no exit trigger."""
    idx = pd.date_range("2026-07-31 09:30", periods=40, freq="5min", tz=_TZ)
    rows = []
    for i in range(len(idx)):
        if i == 0:
            row = (300, 303, 299, 301)
        elif i == 1:
            row = (301, 304.5, 301, 304.4)          # closes above or_high + 10% buffer
        else:
            b = 304.4 + 0.05 * (i - 1)
            row = (b, b + 0.3, b - 0.3, b)
        rows.append((*row, 100_000))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def test_open_session_suppresses_the_false_last_bar_flatten():
    """An in-progress session's newest bar must not be treated as the EOD flatten bar.

    `last_ts_by_date` comes from the DATA. In a backtest every session is complete, so its last
    bar legitimately flattens (that is how half days close). Live, "the last bar" is just the
    newest bar that has closed — so without `open_session` the engine closes any open position
    on every poll and fires a fresh eod_exit at a NEW timestamp each time: ~30 false "close your
    position" alerts per trade per day. Invisible to any backtest.
    """
    from udb_orb.config import load_config
    from udb_orb.engine.orb_engine import run_engine
    from udb_orb.engine.params import Params

    df = _one_session_breakout()
    root = Path(__file__).resolve().parents[1]
    p = Params.from_config(load_config(root / "config" / "tsla_best_B.yaml"))

    done = run_engine(df, p, {})                                    # complete session
    live = run_engine(df, p, {}, open_session=df.index[0].date())   # session still open

    def types(r):
        return [e.type for e in r.events]

    assert types(done) == ["primary_entry", "eod_exit"], \
        "a COMPLETE session must still flatten on its last bar (half-day handling)"
    assert types(live) == ["primary_entry"], \
        "an OPEN session must not flatten early — only the 15:50 clock may close it"
    assert df.index[-1].time() < p.eod_exit, "fixture must end before the real EOD cutoff"


def test_only_same_day_events_are_alerted(monkeypatch, db, tmp_path):
    """Prior-session events must be persisted silently, never mailed.

    Drives poll_once with a stubbed fetch/engine so the guard is tested without the network.
    """
    from udb_orb.live import runner as R

    today = pd.Timestamp.now(tz=_TZ).normalize()
    yday = today - pd.Timedelta(days=1)
    events = [ev(yday + pd.Timedelta(hours=10)),                       # prior session
              ev(today + pd.Timedelta(hours=10), direction="S")]       # today

    bars = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                         "volume": [1.0]}, index=[today + pd.Timedelta(hours=9, minutes=35)])
    monkeypatch.setattr(R, "fetch_5min", lambda *a, **k: bars)
    monkeypatch.setattr(R, "rth_only", lambda d: d)
    monkeypatch.setattr(R, "_closed_bars", lambda d, tf, now: d)
    monkeypatch.setattr(R, "run_engine", lambda *a, **k: SimpleNamespace(events=events))
    monkeypatch.setattr(R.Params, "from_config", staticmethod(lambda cfg: None))

    sent: list = []
    notifier = SimpleNamespace(notify=lambda e: (sent.append(e), True)[1], channels=set())
    run_id = _run(db, "B1")
    cfg = {"symbol": "TSLA", "timeframe_minutes": 5, "data": {}, "live": {}}

    n = R.poll_once(cfg, db, run_id, notifier, set(), verbose=False, label="B1")

    assert n == 1, "only today's event counts as alerted"
    assert len(sent) == 1 and sent[0].direction == "S", "yesterday's event must not be mailed"
    stored = db.conn.execute("SELECT COUNT(*) c FROM events WHERE run_id=?",
                             (run_id,)).fetchone()["c"]
    assert stored == 2, "but BOTH events must still be persisted"
