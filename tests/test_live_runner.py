"""Live-runner safety tests.

These cover the failure modes that are invisible until they hit production:

1. B1 and C1 share a profile NAME and a db_path. If the re-alert guard is not scoped by
   profile label, one silently suppresses the other's alerts and you simply never hear about
   half your trades.
2. The engine replays a multi-day lookback for context, so its event stream always contains
   prior sessions. On the first poll under a new label the seen-set is empty -- without a
   same-day guard the runner mails every event from the last `lookback_days` days at once.

3. FMP keeps aggregating a 5m bar for ~2-3 minutes past its nominal close and serves the
   PARTIAL in the meantime, so `close_time <= now` hands the engine a bar whose OHLC is
   still moving. Measured 2026-08-11; it had priced 52 of the first 87 live events off a
   bar that never existed and fired phantom entry alerts a later poll silently replaced.

These are alert-path bugs: the numbers stay right and nothing raises, so only a test catches
them. A backtest cannot catch #3 at all -- historical bars are already final.
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
from udb_orb.live.runner import (                                     # noqa: E402
    DEFAULT_SETTLE_S, _closed_bars, _event_key, _seed_seen, _superseded_by, profile_label,
)

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
    monkeypatch.setattr(R, "_closed_bars", lambda d, tf, now, settle=0: d)
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


# --------------------------------------------------------------------------------------
# 3. FMP serves a PARTIAL 5m bar for minutes after its nominal close
# --------------------------------------------------------------------------------------

def _bars(*ts):
    idx = [pd.Timestamp(t).tz_localize(_TZ) for t in ts]
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                        index=idx)


def test_closed_bars_waits_out_the_settle_window():
    """A bar is usable only once close_time + settle has passed, not at close_time.

    This is THE fix for the 2026-08-11 partial-bar defect. Without the margin the runner
    reads a bar FMP is still aggregating: measured, the 14:50 bar was still moving at
    close+137s and only settled by close+168s.
    """
    bars = _bars("2026-08-11 09:30", "2026-08-11 09:35", "2026-08-11 09:40")
    now = pd.Timestamp("2026-08-11 09:45:30").tz_localize(_TZ)   # 09:40 bar closed 30s ago

    naive = _closed_bars(bars, 5, now, settle_s=0)
    assert list(naive.index) == list(bars.index), "sanity: no margin admits the fresh bar"

    safe = _closed_bars(bars, 5, now, settle_s=DEFAULT_SETTLE_S)
    assert pd.Timestamp("2026-08-11 09:40").tz_localize(_TZ) not in safe.index, (
        "the just-closed bar is still being aggregated by FMP and must be held back")
    assert pd.Timestamp("2026-08-11 09:35").tz_localize(_TZ) in safe.index, (
        "a bar older than the settle window is final and must be used")


def test_settle_window_releases_the_bar_once_it_is_old_enough():
    bars = _bars("2026-08-11 09:35", "2026-08-11 09:40")
    close_t = pd.Timestamp("2026-08-11 09:45").tz_localize(_TZ)     # 09:40 bar's close
    just_before = _closed_bars(bars, 5, close_t + pd.Timedelta(seconds=DEFAULT_SETTLE_S - 1),
                               settle_s=DEFAULT_SETTLE_S)
    just_after = _closed_bars(bars, 5, close_t + pd.Timedelta(seconds=DEFAULT_SETTLE_S),
                              settle_s=DEFAULT_SETTLE_S)
    assert len(just_before) == 1 and len(just_after) == 2


def test_default_settle_clears_the_measured_worst_case():
    """168s was the observed settle lag on 2026-08-11; the default must clear it with room."""
    assert DEFAULT_SETTLE_S >= 200, (
        "measured settle lag was 137-201s -- a default below ~200s reintroduces the defect")


def test_superseded_event_is_flagged_not_mailed_as_a_new_signal():
    """A moved timestamp is a CORRECTION, not a second trade.

    On 2026-08-10 the runner mailed three `primary_entry` alerts for one trade because the
    re-alert guard keys on the exact (ts, type, direction) and a revision moves the ts.
    """
    seen = {_event_key(ev("2026-08-10 09:45"))}
    later = ev("2026-08-10 10:00")
    assert _superseded_by(seen, later) == pd.Timestamp("2026-08-10 09:45").tz_localize(_TZ).isoformat()

    # different direction, different type and different session are all legitimate
    assert _superseded_by(seen, ev("2026-08-10 10:00", direction="S")) is None
    assert _superseded_by(seen, ev("2026-08-10 10:00", type_="partial_exit")) is None
    assert _superseded_by(seen, ev("2026-08-11 10:00")) is None


def test_poll_once_marks_a_superseded_alert(db, monkeypatch):
    """End-to-end: the replacement alert must carry a CORRECTION note."""
    from udb_orb.live import runner as R

    today = pd.Timestamp.now(tz=_TZ).normalize()
    first = ev(today + pd.Timedelta(hours=9, minutes=45), price=329.61)
    second = ev(today + pd.Timedelta(hours=10), price=330.16)

    bars = _bars("2026-08-11 09:30")
    monkeypatch.setattr(R, "fetch_5min", lambda *a, **k: bars)
    monkeypatch.setattr(R, "rth_only", lambda d: d)
    monkeypatch.setattr(R, "_closed_bars", lambda d, tf, now, settle=0: d)
    monkeypatch.setattr(R.Params, "from_config", staticmethod(lambda cfg: None))

    sent: list = []
    notifier = SimpleNamespace(notify=lambda e: (sent.append(e), True)[1], channels=set())
    run_id = _run(db, "B1")
    cfg = {"symbol": "TSLA", "timeframe_minutes": 5, "data": {}, "live": {}}
    seen: set = set()

    monkeypatch.setattr(R, "run_engine", lambda *a, **k: SimpleNamespace(events=[first]))
    R.poll_once(cfg, db, run_id, notifier, seen, verbose=False, label="B1")
    assert "CORRECTION" not in (sent[0].note or ""), "the first signal is not a correction"

    # FMP revises the bar; the engine now places the entry 15 minutes later
    monkeypatch.setattr(R, "run_engine", lambda *a, **k: SimpleNamespace(events=[first, second]))
    R.poll_once(cfg, db, run_id, notifier, seen, verbose=False, label="B1")

    assert len(sent) == 2, "the replacement must still be delivered"
    assert "CORRECTION" in sent[1].note and "09:45" in sent[1].note, (
        "the replacement must say which alert it supersedes, or it reads as a second entry")
