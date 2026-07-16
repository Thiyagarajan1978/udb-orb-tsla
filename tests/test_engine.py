"""Behavioural tests for the faithful ORB port (Adaptive TP + Reversal @ 5m)."""
import pandas as pd
from conftest import base_config, build_bars

from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params


def _run(rows_by_day, slippage=0.0, enh_overrides=None, be_trigger=0.35, tp_scale=1.0,
         exit_on_close=False, protective_stop=False, profile_overrides=None,
         daily_loss_limit=0.0, execution_overrides=None):
    cfg = base_config()
    cfg.setdefault("execution", {})["daily_loss_limit"] = daily_loss_limit
    # geometry tests predate stop_fill_mode; force the legacy exit_on_close path unless overridden
    cfg["execution"].pop("stop_fill_mode", None)
    if execution_overrides:
        cfg["execution"].update(execution_overrides)
    cfg["profile"]["slippage_per_unit"] = slippage   # geometry tests use 0; realism test sets >0
    cfg["profile"]["be_retrace_trigger"] = be_trigger  # pin to the port value for stable geometry
    cfg["profile"]["adaptive_tp_scale"] = tp_scale     # pin for stable geometry
    cfg["profile"]["reversal_risk_cap"] = 0.0          # geometry: uncapped 2x unless opted in
    cfg["profile"]["sl_mode"] = "Candle High/Low"      # geometry: plain OR-boundary stop
    if profile_overrides:
        cfg["profile"].update(profile_overrides)
    ex = cfg.setdefault("execution", {})
    ex["exit_on_close"] = exit_on_close   # geometry uses intrabar fills
    ex["protective_stop"] = protective_stop
    enh = cfg.get("enhancements", {})
    # geometry tests are enhancement-independent unless a test opts in
    enh.setdefault("reversal_capture", {})["enabled"] = False
    enh.setdefault("runner_trail", {})["enabled"] = False
    enh.setdefault("volatility_regime", {})["enabled"] = False
    enh.setdefault("confirm_breakout", {})["enabled"] = False
    enh.setdefault("max_entry_ext", {})["enabled"] = False
    if enh_overrides:
        for k, v in enh_overrides.items():
            enh.setdefault(k, {}).update(v)
    p = Params.from_config(cfg)
    frames = [build_bars(rows, day=day) for day, rows in rows_by_day.items()]
    bars = pd.concat(frames).sort_index()
    return run_engine(bars, p, enh)


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
    # "proper" failure size = what was risked without BE: entry 101.5 - base SL (OR low 99.0)
    assert abs(t.risk_amount - 2.5) < 1e-9


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


def test_partial_then_be_stop_is_net_win_not_failure():
    """A partial banked at TP, then a BE-stop on the remainder, is a NET WIN — not a
    BE-stop failure."""
    from udb_orb.engine.metrics import summarize
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),      # OR width 2 -> TP 103.64, BE lvl 100.3
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),     # long @101.5
        (9, 40, 101.5, 103.7, 101.4, 103.5, 1000),   # TP -> 25% partial (+0.535)
        (9, 45, 101, 101.6, 99.0, 100.0, 1000),      # retrace: BE fires, remainder BE-stops @entry
    ]
    res = _run({"2024-06-03": rows})
    t = res.trades[0]
    assert t.reason == "BE Stop"
    assert t.pnl_total > 0                         # partial made it a net win
    assert t.outcome == "success"
    s = summarize(res)
    assert s.be_stop_exits == 1
    assert s.be_stop_failures == 0                 # a winning BE-stop is not a failure
    assert s.successes == 1 and s.failures == 0


def test_exit_on_close_makes_be_stop_a_real_loss():
    """Alerts-only fill model: a BE stop fills at the bar CLOSE (a real loss), not at entry."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),   # long @101.5
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # retrace; BE fires, bar CLOSES at 99.0
    ]
    intrabar = _run({"2024-06-03": rows})                     # fills at entry -> ~$0
    on_close = _run({"2024-06-03": rows}, exit_on_close=True)  # fills at close 99.0 -> real loss
    assert abs(intrabar.trades[0].pnl_total) < 1e-9
    assert on_close.trades[0].reason == "BE Stop"
    assert abs(on_close.trades[0].pnl_total - (99.0 - 101.5)) < 1e-9   # -$2.50, filled at close
    assert on_close.trades[0].outcome == "failure"


def test_close_trigger_skips_wick_that_closes_back_above_stop():
    """CLOSE-triggered stop (adopted default for B1/C1): a bar whose WICK pierces the base SL but
    which CLOSES back above it must NOT stop out — the wick fakeout is skipped. The wick/touch model
    stops on that same bar. This is the mechanism behind the +42-46% net / 2024-flips-green edge."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),      # OR: low 99 -> base SL at 99
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),     # long @101.5 (risk = 101.5-99 = 2.5)
        (9, 40, 101, 101.2, 98.8, 100.5, 1000),      # WICK to 98.8 (< SL) but CLOSES 100.5 (> SL)
        (15, 50, 102, 102.1, 101.9, 102.0, 1000),    # EOD close @102
    ]
    ovr = {"use_be_retrace": False}   # isolate the base SL (no BE retrace moving the stop)
    wick = _run({"2024-06-03": rows}, profile_overrides=ovr,
                execution_overrides={"stop_fill_mode": "touch"})
    close = _run({"2024-06-03": rows}, profile_overrides=ovr,
                 execution_overrides={"stop_fill_mode": "close"})
    # wick/touch: the 98.8 wick hits the resting stop at 99 -> stopped out (-$2.50)
    assert wick.trades[0].reason == "Base SL"
    assert abs(wick.trades[0].pnl_total - (99.0 - 101.5)) < 1e-9
    # close: the bar closes at 100.5 (above 99) -> survives; rides to the EOD close 102 (+$0.50)
    assert close.trades[0].reason == "EOD"
    assert abs(close.trades[0].pnl_total - (102.0 - 101.5)) < 1e-9
    assert close.trades[0].pnl_total > wick.trades[0].pnl_total


def test_protective_stop_caps_a_crash_bar():
    """A resting protective stop at the OR boundary caps a crash bar at base-SL risk, not the
    (much lower) close."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),      # OR low 99 -> protective stop at 99
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),     # long @101.5 (risk = 101.5-99 = 2.5)
        (9, 40, 101, 101.6, 95.0, 95.5, 1000),       # crash: low 95 pierces 99, closes 95.5
    ]
    close_only = _run({"2024-06-03": rows}, exit_on_close=True, protective_stop=False)
    hybrid = _run({"2024-06-03": rows}, exit_on_close=True, protective_stop=True)
    # pure close-based: fills at 95.5 -> -$6.00 loss
    assert abs(close_only.trades[0].pnl_total - (95.5 - 101.5)) < 1e-9
    # protective: resting stop at OR boundary 99 -> capped at -$2.50
    assert abs(hybrid.trades[0].pnl_total - (99.0 - 101.5)) < 1e-9
    assert hybrid.trades[0].pnl_total > close_only.trades[0].pnl_total   # smaller loss


def test_stop_fill_touch_is_gap_aware():
    """Touch mode fills a BE stop at ~entry, but a gap-through bar fills at the worse open."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),      # long @101.5, BE stop = entry after BE fires
        (9, 40, 101.4, 101.6, 98.5, 99.0, 1000),      # opens 101.4 (below stop 101.5): gap-through
    ]
    # touch mode: bar opens at 101.4 < stop 101.5 -> fill at min(101.5, 101.4) = 101.4
    touch = _run({"2024-06-03": rows},
                 profile_overrides={}, exit_on_close=False,
                 execution_overrides={"stop_fill_mode": "touch"})
    assert abs(touch.trades[0].exit_price - 101.4) < 1e-9
    # pure "stop" mode fills exactly at the stop level (Pine parity)
    stop = _run({"2024-06-03": rows}, exit_on_close=False)   # -> stop mode
    assert abs(stop.trades[0].exit_price - 101.5) < 1e-9


def test_no_base_sl_when_be_on():
    """With BE retrace on and be_level above the base stop, a hard Base SL cannot fire."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),
        (9, 40, 101, 101.6, 98.0, 98.5, 1000),   # deep drop still becomes BE Stop, not Base SL
    ]
    res = _run({"2024-06-03": rows})
    assert res.trades[0].reason in ("BE Stop", "BE Trail")


def test_pdh_pdl_filter_requires_close_beyond_level():
    """When PDH sits near the long break level, entry waits for a close ABOVE PDH."""
    prior = [  # 2024-06-02: sets PDH = 101.3
        (9, 30, 100.5, 101.3, 100.5, 101.0, 1000),
        (9, 35, 101.0, 101.2, 100.5, 100.8, 1000),
        (15, 50, 100.8, 101.0, 100.5, 100.9, 1000),
    ]
    day = [  # 2024-06-03: OR high 101 low 99 -> long_brk 101.2; PDH 101.3 is within 14% band
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.3, 100.8, 101.25, 1000),   # closes above buffer(101.2) but below PDH(101.3)
        (9, 40, 101.25, 101.6, 101.2, 101.5, 1000),  # closes above PDH -> entry here
        (15, 50, 101.5, 101.7, 101.3, 101.5, 1000),
    ]
    rows = {"2024-06-02": prior, "2024-06-03": day}

    off = _run(rows)                                   # filter OFF -> enters 09:35 @101.25
    on = _run(rows, enh_overrides={"pdh_pdl_filter": {"enabled": True, "proximity_pct": 14.0}})
    day_trades_off = [t for t in off.trades if t.day == "2024-06-03"]
    day_trades_on = [t for t in on.trades if t.day == "2024-06-03"]
    assert abs(day_trades_off[0].entry_price - 101.25) < 1e-9   # entered on the buffer break
    assert abs(day_trades_on[0].entry_price - 101.5) < 1e-9     # waited for close above PDH 101.3


def test_runner_trail_banks_the_peak():
    """After the partial, the runner exits on a 1xOR retrace from its peak (not held to EOD)."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),        # OR width 2 -> trail dist 2.0, TP 103.64
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),       # long @101.5
        (9, 40, 101.5, 103.7, 101.4, 103.5, 1000),     # TP -> 25% partial; peak so far 103.7
        (9, 45, 103.5, 106.0, 103.0, 105.5, 1000),     # runs to 106 (peak); trail = 106-2 = 104
        (9, 50, 105.5, 105.6, 103.5, 103.8, 1000),     # low 103.5 <= 104 -> runner trail exits @104
        (15, 50, 103.8, 104.0, 103.0, 103.5, 1000),    # (would-be EOD, lower)
    ]
    on = _run({"2024-06-03": rows},
              enh_overrides={"runner_trail": {"enabled": True, "or_mult": 1.0}})
    t = on.trades[0]
    assert t.reason == "Trail"
    assert abs(t.exit_price - 104.0) < 1e-9            # exited at peak(106) - 1xOR(2)
    # the trail beat holding the runner to EOD (103.5)
    off = _run({"2024-06-03": rows})                  # runner_trail off -> rides to EOD
    assert off.trades[0].reason == "EOD"
    assert on.trades[0].pnl_total > off.trades[0].pnl_total


def test_volatility_regime_skips_high_vol_days():
    """A day is skipped entirely when prior realised volatility exceeds the threshold."""
    # 25 prior days of wildly alternating closes -> very high realised vol, then a breakout day
    prior = {}
    for i in range(25):
        day = f"2024-05-{i+1:02d}"
        c = 100.0 if i % 2 == 0 else 130.0          # ~26% daily swings -> huge rvol
        prior[day] = [(9, 30, c, c + 1, c - 1, c, 1000), (15, 50, c, c + 1, c - 1, c, 1000)]
    breakout = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),    # would enter long
        (15, 50, 101.5, 102.0, 101.0, 101.8, 1000),
    ]
    rows = {**prior, "2024-06-03": breakout}

    off = _run(rows)
    on = _run(rows, enh_overrides={"volatility_regime": {"enabled": True, "lookback": 20,
                                                          "max_rvol_pct": 4.92}})
    assert any(t.day == "2024-06-03" for t in off.trades)        # traded normally
    assert not any(t.day == "2024-06-03" for t in on.trades)     # skipped by vol regime
    skipped = [d for d in on.days if d.date == "2024-06-03"]
    assert skipped[0].no_trade_reason == "Vol Regime Skip"


def test_max_cap_stop_tightens_the_or_boundary_stop():
    """'Candle High/Low + Max Cap' never places the stop further than fixed_sl from entry."""
    rows = [  # wide OR 95-101 (width 6) -> long trigger = 101 + 0.6 = 101.6
        (9, 30, 100, 101.0, 95.0, 100.0, 1000),
        (9, 35, 101, 102.2, 100.5, 102.0, 1000),     # long @102.0 (raw risk = 102.0 - 95.0 = 7.0)
        (15, 50, 102.0, 102.5, 101.5, 102.2, 1000),
    ]
    plain = _run({"2024-06-03": rows})
    capped = _run({"2024-06-03": rows},
                  profile_overrides={"sl_mode": "Candle High/Low + Max Cap", "fixed_sl": 3.0})
    # raw OR stop = 95.0 -> risk 7.00 ; capped stop = 102.0 - 3.0 = 99.0 -> risk 3.00
    assert abs(plain.trades[0].risk_amount - 7.0) < 1e-9
    assert abs(capped.trades[0].risk_amount - 3.0) < 1e-9


def test_atr_cap_falls_back_to_fixed_when_atr_unavailable():
    """'Candle High/Low + ATR Cap' uses atr_mult*ATR; with no ATR history it falls back to fixed_sl."""
    rows = [  # single day -> ATR unavailable -> cap = fixed_sl (3.0). Wide OR 95-101, long @102.
        (9, 30, 100, 101.0, 95.0, 100.0, 1000),
        (9, 35, 101, 102.2, 100.5, 102.0, 1000),
        (15, 50, 102.0, 102.5, 101.5, 102.2, 1000),
    ]
    atr = _run({"2024-06-03": rows},
               profile_overrides={"sl_mode": "Candle High/Low + ATR Cap", "fixed_sl": 3.0, "atr_mult": 0.35})
    assert abs(atr.trades[0].risk_amount - 3.0) < 1e-9   # fell back to the fixed cap


def test_or_midpoint_stop_sits_at_the_range_middle():
    """'OR Midpoint' places the base stop at (or_high+or_low)/2 — tighter than the boundary."""
    rows = [  # OR 99-101 -> midpoint 100; long trigger 101.2, enters 101.5, rides to EOD
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 101.0, 101.5, 1000),
        (9, 40, 101.5, 102.5, 101.4, 102.3, 1000),
        (15, 50, 102.3, 102.6, 102.2, 102.4, 1000),
    ]
    mid = _run({"2024-06-03": rows}, profile_overrides={"sl_mode": "OR Midpoint"})
    bnd = _run({"2024-06-03": rows}, profile_overrides={"sl_mode": "Candle High/Low"})
    # midpoint stop = 100 -> risk = 101.5 - 100 = 1.5 ; boundary stop = 99 -> risk = 2.5
    assert abs(mid.trades[0].risk_amount - 1.5) < 1e-9
    assert mid.trades[0].risk_amount < bnd.trades[0].risk_amount


def test_two_close_confirmation_delays_entry():
    """confirm_two_closes requires the PREVIOUS bar to also close beyond the trigger."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),      # OR -> long trigger 101.2
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),     # 1st close above trigger
        (9, 40, 101.5, 102.0, 101.3, 101.9, 1000),   # 2nd close above -> confirmed entry here
        (15, 50, 101.9, 102.2, 101.5, 102.0, 1000),
    ]
    off = _run({"2024-06-03": rows})
    on = _run({"2024-06-03": rows}, enh_overrides={"confirm_two_closes": {"enabled": True}})
    assert abs(off.trades[0].entry_price - 101.5) < 1e-9   # enters on the first close break
    assert abs(on.trades[0].entry_price - 101.9) < 1e-9    # waits for the second


def test_confirm_breakout_waits_for_next_candle_to_hold():
    """confirm_breakout: the first close-break must be HELD by the next candle, else no entry."""
    # 09:35 breaks above 101.2 but 09:40 snaps back inside -> rejected; 09:45+09:50 hold -> enter 09:50
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),      # break (close 101.5 > 101.2)
        (9, 40, 101.5, 101.6, 100.5, 100.9, 1000),    # snaps back inside (100.9 < 101.2) -> reject
        (9, 45, 100.9, 101.6, 100.8, 101.4, 1000),    # fresh break
        (9, 50, 101.4, 101.9, 101.3, 101.8, 1000),    # holds + green -> ENTER here
        (15, 50, 101.8, 102.0, 101.5, 101.9, 1000),
    ]
    off = _run({"2024-06-03": rows})
    on = _run({"2024-06-03": rows}, enh_overrides={"confirm_breakout": {"enabled": True, "require_trend_candle": True}})
    assert abs(off.trades[0].entry_price - 101.5) < 1e-9     # baseline enters on the first break
    assert abs(on.trades[0].entry_price - 101.8) < 1e-9      # confirmed enters on the held candle


def test_confirm_breakout_hold_bars_two_needs_three_consecutive_closes():
    """hold_bars=2 (3-candle): needs the break bar + TWO holds; enters on the 3rd close beyond."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),      # break (close 101.5 > 101.2)
        (9, 40, 101.5, 101.9, 101.3, 101.7, 1000),    # 1st hold  (2-candle would ENTER here)
        (9, 45, 101.7, 102.1, 101.5, 101.9, 1000),    # 2nd hold -> 3-candle ENTERS here (101.9)
        (15, 50, 101.9, 102.2, 101.7, 102.0, 1000),
    ]
    two = _run({"2024-06-03": rows},
               enh_overrides={"confirm_breakout": {"enabled": True, "require_trend_candle": True, "hold_bars": 1}})
    three = _run({"2024-06-03": rows},
                 enh_overrides={"confirm_breakout": {"enabled": True, "require_trend_candle": True, "hold_bars": 2}})
    assert abs(two.trades[0].entry_price - 101.7) < 1e-9     # 2-candle enters on the 1st hold
    assert abs(three.trades[0].entry_price - 101.9) < 1e-9   # 3-candle waits for the 2nd hold


def test_max_entry_ext_skips_over_extended_breakout():
    """max_entry_ext blocks a breakout whose close is > or_mult*OR from the OR-boundary stop."""
    rows = [  # OR 99-101 (w 2); a long that closes 104 is 5.0 from the stop (99) = 2.5x OR
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 104.5, 100.8, 104.0, 1000),   # huge break: close 104 -> 5.0 from stop 99
        (15, 50, 104.0, 104.5, 103.5, 104.0, 1000),
    ]
    took = _run({"2024-06-03": rows})
    skipped = _run({"2024-06-03": rows},
                   enh_overrides={"max_entry_ext": {"enabled": True, "or_mult": 1.5}})  # band 3.0
    assert len(took.trades) == 1
    assert len(skipped.trades) == 0     # 5.0 > 3.0 -> blocked


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


def test_reversal_trail_to_eod_takes_no_partial():
    """With reversal_capture trail_to_eod, the reversal rides full size to EOD (no partial)."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),   # primary long
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # BE Stop
        (9, 45, 99, 99.0, 97.9, 98.0, 1000),       # reversal short (raw OR break)
        (9, 50, 98, 98.1, 92.8, 93.0, 1000),       # would have partialed at $5 TP — now suppressed
        (15, 50, 93, 93.1, 92.9, 93.0, 1000),      # rides to EOD
    ]
    res = _run({"2024-06-03": rows},
               enh_overrides={"reversal_capture": {"enabled": True, "trigger_on_be_stop": True,
                                                    "trail_to_eod": True}})
    rev = [t for t in res.trades if t.is_reversal][0]
    assert rev.reason == "Rev EOD"
    assert rev.part1_pnl == 0.0                     # no partial leg
    assert "partial_exit" not in [e.type for e in res.events]
    # full 2 units captured entry(98)->EOD(93) = $5 * 2
    assert abs(rev.pnl_total - 10.0) < 1e-9


def test_reversal_trigger_on_be_stop_uses_raw_break():
    """trigger_on_be_stop enters the reversal on a raw OR break (no buffer needed)."""
    # closes sit between the buffered trigger (98.8) and the raw OR low (99.0): a raw break
    # fires, a buffered break never does.
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # BE Stop
        (9, 45, 99, 99.2, 98.85, 98.90, 1000),     # 98.90 < OR low 99.0 (raw) but > buffered 98.8
        (10, 0, 98.9, 99.0, 98.85, 98.95, 1000),   # stays above buffered 98.8
        (15, 50, 98.9, 99.0, 98.85, 98.95, 1000),  # EOD, still above buffered 98.8
    ]
    res_raw = _run({"2024-06-03": rows},
                   enh_overrides={"reversal_capture": {"enabled": True, "trigger_on_be_stop": True}})
    res_buf = _run({"2024-06-03": rows})            # rc off -> needs buffered break
    assert any(t.is_reversal for t in res_raw.trades)      # raw break entered
    assert not any(t.is_reversal for t in res_buf.trades)  # buffered break did NOT


def test_whipsaw_reenter_fires_once():
    """After primary AND reversal both stop, a re-entry in the ORIGINAL direction fires once."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),      # OR
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),     # primary long
        (9, 40, 101, 101.6, 100.0, 100.5, 1000),     # primary BE-stops
        (9, 45, 99.5, 99.5, 98.0, 98.5, 1000),       # reversal short (raw break)
        (9, 50, 98.5, 100.0, 98.0, 99.5, 1000),      # reversal stops out
        (9, 55, 100, 101.6, 100.5, 101.5, 1000),     # ORIGINAL dir breaks again -> RE-ENTRY long
        (15, 50, 101.5, 102.0, 101.0, 101.8, 1000),  # EOD
    ]
    res = _run({"2024-06-03": rows},
               enh_overrides={"reversal_capture": {"enabled": True, "trigger_on_be_stop": True,
                                                    "trail_to_eod": False, "reenter_after_whipsaw": True}})
    reentries = [t for t in res.trades if "(Re)" in t.direction]
    assert len(reentries) == 1                       # exactly one re-entry
    assert reentries[0].direction.startswith("L")    # original (long) direction
    # off by default: same day with the flag off produces no re-entry
    res_off = _run({"2024-06-03": rows},
                   enh_overrides={"reversal_capture": {"enabled": True, "trigger_on_be_stop": True}})
    assert not any("(Re)" in t.direction for t in res_off.trades)


def test_reversal_risk_cap_scales_qty():
    """The reversal qty shrinks so its dollar risk <= cap (risk parity), instead of blind 2x."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),   # primary long
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # BE stop -> prim_stopped
        (9, 45, 99, 99.0, 97.9, 98.0, 1000),       # reversal short @98.0, stop = OR high 101.0
        (15, 50, 98, 98.1, 97.9, 98.0, 1000),
    ]
    # risk per unit = |98.0 - 101.0| = 3.0 ; uncapped qty = 2.0 -> risk 6.0
    uncapped = _run({"2024-06-03": rows})
    rev = [t for t in uncapped.trades if t.is_reversal][0]
    assert rev.qty == 2.0

    # cap $3 -> qty = 3.0/3.0 = 1.0
    cfg_rows = {"2024-06-03": rows}
    capped = _run(cfg_rows, profile_overrides={"reversal_risk_cap": 3.0, "reversal_risk_mode": "scale"})
    rev2 = [t for t in capped.trades if t.is_reversal][0]
    assert abs(rev2.qty - 1.0) < 1e-9
    assert abs(rev2.risk_amount - 3.0) < 1e-9      # risk capped

    # skip mode: risk 6.0 > cap 3.0 -> no reversal at all
    skipped = _run(cfg_rows, profile_overrides={"reversal_risk_cap": 3.0, "reversal_risk_mode": "skip"})
    assert not any(t.is_reversal for t in skipped.trades)


def test_daily_loss_breaker_blocks_new_entries():
    """Once the day's realised loss breaches the limit, no NEW entries (blocks the reversal)."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),   # long @101.5
        (9, 40, 101, 101.6, 98.5, 99.0, 1000),     # BE stop fills at close 99.0 -> -$2.50
        (9, 45, 99, 99.0, 97.9, 98.0, 1000),       # reversal short would enter here
        (15, 50, 98, 98.1, 97.9, 98.0, 1000),
    ]
    no_breaker = _run({"2024-06-03": rows}, exit_on_close=True)
    assert any(t.is_reversal for t in no_breaker.trades)          # reversal taken

    breaker = _run({"2024-06-03": rows}, exit_on_close=True, daily_loss_limit=2.0)
    assert not any(t.is_reversal for t in breaker.trades)          # blocked after -$2.50
    assert len(breaker.trades) == 1


def test_resume_reentry_takes_same_direction_and_disarms_reversal():
    """After the primary stops, a close back beyond the break level re-enters the SAME direction."""
    rows = [
        (9, 30, 100, 101.0, 99.0, 100.0, 1000),      # OR 99-101, long_brk 101.2
        (9, 35, 101, 101.6, 100.5, 101.5, 1000),     # primary long @101.5
        (9, 40, 101, 101.6, 100.0, 100.5, 1000),     # BE stop (arms reversal + resume)
        (9, 45, 100.5, 101.9, 100.4, 101.8, 1000),   # closes back above 101.2 -> RESUME long
        (15, 50, 101.8, 102.2, 101.5, 102.0, 1000),
    ]
    off = _run({"2024-06-03": rows})
    on = _run({"2024-06-03": rows},
              enh_overrides={"resume_reentry": {"enabled": True, "trigger": "buffered",
                                                 "risk_cap": 0.0, "disarm_other": True},
                             "reversal_capture": {"enabled": True, "trigger_on_be_stop": True}})
    assert not any("(Re)" in t.direction for t in off.trades)
    res = [t for t in on.trades if "(Re)" in t.direction]
    assert len(res) == 1
    assert res[0].direction.startswith("L")          # same direction as the primary
    assert not any(t.is_reversal for t in on.trades)  # resume disarmed the reversal


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
