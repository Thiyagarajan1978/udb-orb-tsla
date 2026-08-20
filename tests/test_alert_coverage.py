"""Delivery-path tests: an event the engine fires but never delivers.

This system is ALERTS-ONLY, so an exit that does not reach you is an exit you do not take.
`alerts.events` is an explicit ALLOW-LIST and `Notifier._wants` drops anything absent from it
in silence -- no error, no log line, correct backtest numbers. Two adopted exits sat in that
blind spot until 2026-08-19:

  * `pd_level_exit`     -- default ON, worth +$3,932 / +29% over 365d on C1
  * `runner_trail_exit` -- the runner peak-trail close

Both fired in the engine and were persisted to the DB with `alerted=0` for their whole life.
A backtest cannot see this class of bug; only these assertions can.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.alerts.notifier import _EVENT_META, Notifier          # noqa: E402
from udb_orb.config import load_config                             # noqa: E402
from udb_orb.engine.orb_engine import ALL_EVENT_TYPES              # noqa: E402

CONFIGS = sorted(glob.glob(str(ROOT / "config" / "*.yaml")))


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: os.path.basename(p))
def test_every_shipped_config_alerts_on_every_engine_event(path):
    """No shipped config may omit an event type the engine can emit."""
    events = load_config(path).get("alerts", {}).get("events", [])
    if not events:
        pytest.skip("no explicit allow-list -> Notifier permits everything")
    missing = [t for t in ALL_EVENT_TYPES if t not in events]
    assert not missing, (
        f"{os.path.basename(path)} would NEVER alert on {missing}. This system is alerts-only, "
        f"so those exits are simply not taken. Add them to alerts.events."
    )


def test_every_engine_event_has_alert_text():
    """A type missing from _EVENT_META still mails, but as a bare 'EVENT' with no action."""
    missing = [t for t in ALL_EVENT_TYPES if t not in _EVENT_META]
    assert not missing, f"_EVENT_META has no tag/action for {missing}"


def test_notifier_reports_an_incomplete_allow_list(capsys):
    """A config that forgets a type must say so loudly instead of failing silently."""
    cfg = {"alerts": {"enabled": True, "channels": [], "events": ["primary_entry"]}}
    n = Notifier(cfg, "TSLA", 5, label="B1")
    out = capsys.readouterr().out
    assert "WARNING" in out and "NEVER alert" in out and "[B1]" in out
    assert "pd_level_exit" in n.missing_events
    assert not n._wants("pd_level_exit")      # the actual defect: silently dropped
    assert n._wants("primary_entry")


def test_empty_allow_list_still_permits_everything():
    """The permissive branch must survive: no `events` key -> alert on all of them."""
    n = Notifier({"alerts": {"enabled": True, "channels": []}}, "TSLA", 5)
    assert n.missing_events == []
    assert all(n._wants(t) for t in ALL_EVENT_TYPES)


def test_traded_configs_alert_on_the_pd_level_exit():
    """The specific 2026-08-19 regression, pinned for B1 and C1."""
    for path in ("config/tsla_best_B.yaml", "config/tsla_config_C1.yaml"):
        cfg = load_config(str(ROOT / path))
        assert "pd_level_exit" in cfg["alerts"]["events"], path
        assert Notifier(cfg, "TSLA", 5, label="B1")._wants("pd_level_exit"), path
