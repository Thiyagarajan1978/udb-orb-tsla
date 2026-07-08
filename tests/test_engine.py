"""Behavioural tests for the faithful ORB port (Adaptive TP + Reversal @ 5m)."""
import pandas as pd
from conftest import base_config, build_bars

from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params


def _run(rows_by_day, slippage=0.0):
    cfg = base_config()
    cfg["profile"]["slippage_per_unit"] = slippage   # geometry tests use 0; realism test sets >0
    p = Params.from_config(cfg)
    frames = [build_bars(rows, day=day) for day, rows in rows_by_day.items()]
    bars = pd.concat(frames).sort_index()
    return run_engine(bars, p, cfg.get("enhancements", {}))


def test_long_partial_then_eod():
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),      # OR: H101 L99 -> width 2, long_brk 101.2
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),     # entry long @101.5, SL 99, TP 103.64
        (9, 40, 101.5, 103.7, 101.4, 103.5, 1000),   # TP hit -> 25% partial (+0.535)
        (15, 50, 103.9, 104.1, 103.9, 104.0, 1000),  # EOD close 75% @104 (+1.875)
    ]
    res = _run({"2024-06-03": rows})
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "L"
    assert t.reason == "EOD"
    assert abs(t.pnl_total - 2.41) < 1e-6
    types = [e.type for e in res.events]
    assert "primary_entry" in types
    assert "partial_exit" in types
    assert "eod_exit" in types
    # entry and exit are on different bars
    assert t.duration_bars >= 1


def test_be_stop_protects_then_reversal_short():
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),     # OR width 2, short_brk 98.8
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),    # primary long @101.5
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),      # retrace: BE fires, stop->entry -> BE Stop @0
        (9, 45, 99, 99.0, 97.9, 98.0, 1000),        # opposite break -> reversal SHORT @98 (2x)
        (9, 50, 98, 98.1, 92.8, 93.0, 1000),        # reversal TP -> 25% partial (+2.5)
        (15, 50, 93, 93.1, 92.9, 93.0, 1000),       # EOD close 1.5 units @93 (+7.5)
    ]
    res = _run({"2024-06-03": rows})
    assert len(res.trades) == 2

    primary = res.trades[0]
    assert primary.direction == "L"
    assert primary.reason == "BE Stop"
    assert abs(primary.pnl_total) < 1e-9          # geometry only (no slippage) -> exactly break-even
    assert primary.outcome == "failure"           # BE Stop is a FAILURE, never a $0 win

    rev = res.trades[1]
    assert rev.is_reversal is True
    assert rev.direction == "S (Rev)"
    assert rev.reason == "Rev EOD"
    assert rev.qty == 2.0
    assert abs(rev.pnl_total - 10.0) < 1e-6


def test_be_stop_is_failure_and_costs_slippage():
    """With a realistic exit cost, a BE Stop is a small real LOSS, classified as failure."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),   # primary long @101.5
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # BE fires, stop->entry -> BE Stop
    ]
    res = _run({"2024-06-03": rows}, slippage=0.02)
    t = res.trades[0]
    assert t.reason == "BE Stop"
    assert t.outcome == "failure"
    assert t.pnl_total < 0                          # not $0 — slippage makes it a real loss
    assert abs(t.pnl_total - (-0.02)) < 1e-9        # 1 unit * $0.02


def test_summary_counts_be_stop_as_failure():
    from udb_orb.engine.metrics import summarize
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # BE Stop
        (9, 45, 99, 99.0, 97.9, 98.0, 1000),       # reversal short
        (9, 50, 98, 98.1, 92.8, 93.0, 1000),       # partial
        (15, 50, 93, 93.1, 92.9, 93.0, 1000),      # reversal EOD win
    ]
    res = _run({"2024-06-03": rows}, slippage=0.02)
    s = summarize(res)
    assert s.be_stop_failures == 1
    assert s.failures >= 1
    assert s.successes >= 1
    assert s.trades == s.successes + s.failures


def test_no_base_sl_when_be_on():
    """With BE retrace on and be_level above the base stop, a hard Base SL cannot fire."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),
        (9, 40, 101, 101.6, 98.0, 98.5, 1000),   # deep drop still becomes BE Stop, not Base SL
    ]
    res = _run({"2024-06-03": rows})
    assert res.trades[0].reason in ("BE Stop", "BE Trail")


def test_no_trade_day_reason_no_setup():
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 100, 100.5, 99.5, 100.0, 1000),   # never breaks the OR
        (15, 50, 100, 100.2, 99.8, 100.0, 1000),
    ]
    res = _run({"2024-06-03": rows})
    assert len(res.trades) == 0
    nts = res.no_trade_days()
    assert len(nts) == 1
    assert nts[0].no_trade_reason == "No Setup"


def test_reversal_only_once_per_day():
    """Primary + at most one reversal = 2 legs max in a day."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),   # primary long
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # BE stop
        (9, 45, 99, 99.0, 97.9, 98.0, 1000),       # reversal short
        (9, 50, 98, 98.1, 97.9, 98.0, 1000),       # chop
        (15, 50, 98, 98.1, 97.9, 98.0, 1000),      # EOD
    ]
    res = _run({"2024-06-03": rows})
    assert len(res.trades) <= 2
