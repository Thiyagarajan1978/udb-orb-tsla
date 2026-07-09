"""Faithful bar-by-bar port of the Pine v12.4.3 ORB state machine for the
"Adaptive TP + Reversal (Best Combined)" profile on TSLA 5m.

The engine consumes a tz-aware (ET) OHLCV DataFrame (bar-START index, RTH only) and
produces closed trade legs, an ordered event stream (for alerts), day summaries, and
no-trade-day reasons. It is network-free and deterministic — the same bars always yield
the same output — so it is the single source of truth for both backtest and live.

Processing order per bar mirrors Pine exactly:
  new-day finalize/reset -> capture OR bar -> VWAP/RVOL (precomputed) -> no-trade tracking
  -> primary entry -> reversal entry -> LONG exit engine -> SHORT exit engine -> EOD close.
A trade entered on a bar cannot exit on the same bar (exit engine requires a later bar),
matching Pine's `bar_index > entryBarIndex` guard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any, Optional

import pandas as pd

from . import indicators
from .params import Params

# ---- Event / trade type tags (align with config alerts.events) ----------
EV_PRIMARY_ENTRY = "primary_entry"
EV_REVERSAL_ENTRY = "reversal_entry"
EV_BE_RETRACE_FIRED = "be_retrace_fired"
EV_TP_FULL = "tp_full"
EV_PARTIAL_EXIT = "partial_exit"
EV_BE_TRAIL_EXIT = "be_trail_exit"
EV_BE_STOP_EXIT = "be_stop_exit"
EV_VWAP_CROSS_EXIT = "vwap_cross_exit"
EV_TRAIL_EXIT = "runner_trail_exit"
EV_BASE_SL_EXIT = "base_sl_exit"
EV_EOD_EXIT = "eod_exit"

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class TradeLeg:
    day: str
    direction: str            # "L", "S", "L (Rev)", "S (Rev)"
    is_reversal: bool
    entry_ts: pd.Timestamp
    entry_price: float
    exit_ts: pd.Timestamp
    exit_price: float
    qty: float
    part1_pnl: float
    pnl_total: float
    pnl_per_unit: float
    reason: str               # "TP", "BE Trail", "BE Stop", "Base SL", "VWAP Cross", "EOD" (+ "Rev ")
    duration_bars: int
    outcome: str = "failure"  # "success" if pnl_total > 0 else "failure" (BE Stop @ ~$0 = failure)
    risk_amount: float = 0.0  # |entry - initial base SL| * qty = the $ you were exposed to
                              # (for BE Stops, the loss you'd have taken without BE protection)


@dataclass
class Event:
    ts: pd.Timestamp
    type: str
    direction: str
    price: float
    qty: float
    pnl: Optional[float]
    reason: str
    note: str = ""


@dataclass
class DayRecord:
    date: str
    day_name: str
    has_trades: bool
    t1: str = ""
    entry_ts: Optional[pd.Timestamp] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    duration_bars: Optional[int] = None
    day_net: float = 0.0
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_width: Optional[float] = None
    no_trade_reason: str = ""


@dataclass
class Result:
    trades: list[TradeLeg] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    days: list[DayRecord] = field(default_factory=list)

    def no_trade_days(self) -> list[DayRecord]:
        return [d for d in self.days if not d.has_trades]

    def trade_days(self) -> list[DayRecord]:
        return [d for d in self.days if d.has_trades]

    def net_pnl(self) -> float:
        return sum(t.pnl_total for t in self.trades)


class _DayState:
    """Mutable per-day trade/exit state (reset on each new day)."""

    def __init__(self) -> None:
        self.or_high: Optional[float] = None
        self.or_low: Optional[float] = None
        self.or_width: Optional[float] = None
        self.skipped_by_regime = False
        self.skipped_by_vol = False

        # active trade
        self.active = False
        self.dir = 0                 # 1 long, -1 short
        self.entry_price: Optional[float] = None
        self.stop: Optional[float] = None
        self.tp: Optional[float] = None
        self.entry_bar: Optional[int] = None
        self.entry_ts: Optional[pd.Timestamp] = None
        self.first_taken = False
        self.in_reversal = False

        # BE / partial state
        self.be_triggered = False
        self.be_level: Optional[float] = None
        self.init_stop: Optional[float] = None   # base SL at entry (risk basis)
        self.part1_closed = False
        self.eff_qty: Optional[float] = None
        self.trail_active = False
        self.part1_pnl = 0.0
        self.suppress_partial = False   # reversal trail_to_eod: no partial, ride full move
        self.runner_peak: Optional[float] = None   # peak favorable price after the partial

        # reversal state machine
        self.prim_dir = 0
        self.prim_stopped = False
        self.qty_total: Optional[float] = None

        # whipsaw re-entry (original direction after BOTH primary and reversal stop)
        self.is_reenter = False
        self.reenter_armed = False
        self.reenter_taken = False

        # no-trade reason tracking
        self.has_or = False
        self.post_raw_break = False
        self.post_buffered_touch = False
        self.post_buffered_close_break = False
        self.or_too_narrow = False
        self.or_too_wide = False
        self.vwap_blocked = False
        self.pdhpdl_blocked = False

        # day review accumulation
        self.has_trades = False
        self.t1 = ""
        self.entry_ts_day: Optional[pd.Timestamp] = None
        self.entry_price_day: Optional[float] = None
        self.exit_price_day: Optional[float] = None
        self.day_pnl = 0.0
        self.exit_reason_day = ""
        self.duration_bars_day: Optional[int] = None


class OrbEngine:
    def __init__(self, params: Params, enhancements: Optional[dict[str, Any]] = None) -> None:
        self.p = params
        self.enh = enhancements or {}
        self.result = Result()

    # ---- enhancement gates (no-ops unless enabled in config) -------------
    def _rvol_ok(self, rvol: float) -> bool:
        cfg = self.enh.get("rvol_filter", {})
        if not cfg.get("enabled", False):
            return True
        if rvol is None or pd.isna(rvol):
            return True   # not enough history -> don't block
        return rvol >= float(cfg.get("min_rvol", 1.2))

    def _time_window_ok(self, ts: pd.Timestamp) -> bool:
        cfg = self.enh.get("time_window", {})
        if not cfg.get("enabled", False):
            return True
        t = ts.time()
        sh, sm = map(int, str(cfg.get("start", "09:35")).split(":"))
        eh, em = map(int, str(cfg.get("end", "16:00")).split(":"))
        return time(sh, sm) <= t <= time(eh, em)

    # ---- reversal-capture enhancement (default OFF) ---------------------
    @property
    def _rev_cfg(self) -> dict[str, Any]:
        return self.enh.get("reversal_capture", {})

    @property
    def _rev_on(self) -> bool:
        return bool(self._rev_cfg.get("enabled", False))

    def _rev_trigger_raw(self) -> bool:
        return self._rev_on and bool(self._rev_cfg.get("trigger_on_be_stop", False))

    @property
    def _reenter_on(self) -> bool:
        return self._rev_on and bool(self._rev_cfg.get("reenter_after_whipsaw", False))

    # ---- PDH/PDL confirmation filter (default OFF) ----------------------
    @property
    def _pdhpdl_cfg(self) -> dict[str, Any]:
        return self.enh.get("pdh_pdl_filter", {})

    @property
    def _pdhpdl_on(self) -> bool:
        return bool(self._pdhpdl_cfg.get("enabled", False))

    # ---- runner peak-trail (default OFF) -------------------------------
    @property
    def _runner_trail_on(self) -> bool:
        return bool(self.enh.get("runner_trail", {}).get("enabled", False))

    def _runner_trail_dist(self, st: _DayState) -> float:
        mult = float(self.enh.get("runner_trail", {}).get("or_mult", 0.75))
        return mult * (st.or_width or 0.0)

    def _effective_triggers(self, st: _DayState, long_brk, short_brk, pdh, pdl):
        """When PDH/PDL sits within proximity_pct of the OR width of the break level, raise the
        long trigger to PDH (require close ABOVE it) / lower the short trigger to PDL."""
        long_trig, short_trig = long_brk, short_brk
        if not self._pdhpdl_on or not st.or_width:
            return long_trig, short_trig
        band = st.or_width * float(self._pdhpdl_cfg.get("proximity_pct", 14.0)) / 100.0
        if long_brk is not None and pdh is not None and abs(pdh - long_brk) <= band:
            long_trig = max(long_brk, pdh)
        if short_brk is not None and pdl is not None and abs(pdl - short_brk) <= band:
            short_trig = min(short_brk, pdl)
        return long_trig, short_trig

    def _reversal_qty(self, st: _DayState, c: float, direction: int) -> Optional[float]:
        """Reversal size after the dollar-risk cap. None => skip the reversal entirely.

        The reversal enters after price crossed the whole OR, so its stop (the opposite OR
        boundary) is far away; at 2x size that risk drives the worst days. `scale` shrinks the
        qty so risk <= cap; `skip` declines the trade.
        """
        p = self.p
        stop = (st.or_high + p.sl_offset) if direction == -1 else (st.or_low - p.sl_offset)
        risk_per_unit = abs(c - stop)
        qty = p.trade_qty * p.reversal_qty_mult
        cap = p.reversal_risk_cap
        if cap and cap > 0 and risk_per_unit > 0:
            if p.reversal_risk_mode == "skip":
                if risk_per_unit * qty > cap:
                    return None
            else:  # scale
                qty = min(qty, cap / risk_per_unit)
        return qty

    def _reversal_tp_dist(self, or_size: float) -> Optional[float]:
        """Distance for the reversal TP, or None to disable TP (trail to EOD)."""
        if self._rev_on and self._rev_cfg.get("trail_to_eod", False):
            return None
        if self._rev_on and float(self._rev_cfg.get("target_or_mult", 0.0) or 0.0) > 0:
            return float(self._rev_cfg["target_or_mult"]) * or_size
        return self.p.reversal_target

    def _vol_regime_skip(self, rvol: Optional[float]) -> bool:
        """Skip the whole day when prior realised volatility exceeds the threshold."""
        cfg = self.enh.get("volatility_regime", {})
        if not cfg.get("enabled", False):
            return False
        if rvol is None or pd.isna(rvol):
            return False   # not enough history -> don't block
        return float(rvol) > float(cfg.get("max_rvol_pct", 4.92))

    def _regime_skip(self, or_width: float) -> bool:
        cfg = self.enh.get("or_width_regime", {})
        if not cfg.get("enabled", False):
            return False
        below = float(cfg.get("skip_below", 0.0) or 0.0)
        above = float(cfg.get("skip_above", 0.0) or 0.0)
        if below > 0 and or_width < below:
            return True
        if above > 0 and or_width > above:
            return True
        return False

    # ---- main -----------------------------------------------------------
    def run(self, df: pd.DataFrame) -> Result:
        if df.empty:
            return self.result
        df = df.sort_index()
        vwap = indicators.session_vwap(df)
        rvol = indicators.relative_volume(df, int(self.enh.get("rvol_filter", {}).get("lookback_bars", 20)))

        # Prior-day high/low (PDH/PDL) per date, from the RTH session data itself.
        _daily = df.groupby(df.index.date).agg(hi=("high", "max"), lo=("low", "min"),
                                               cl=("close", "last"))
        pdh_map = _daily["hi"].shift(1).to_dict()
        pdl_map = _daily["lo"].shift(1).to_dict()

        # Prior-N-day realised daily volatility (%) — known at the open, so usable pre-entry.
        _vcfg = self.enh.get("volatility_regime", {})
        _lb = int(_vcfg.get("lookback", 20))
        _rvol = (_daily["cl"].pct_change().rolling(_lb).std() * 100.0).shift(1)
        rvol_map = _rvol.to_dict()

        st = _DayState()
        cur_date = None
        bar_i = -1
        p = self.p

        for ts, row in df.iterrows():
            bar_i += 1
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            v = float(row.get("volume", 0.0) or 0.0)
            vw = vwap.loc[ts]
            vw = None if pd.isna(vw) else float(vw)
            rv = rvol.loc[ts]

            d = ts.date()
            if cur_date is None:
                cur_date = d
                st.skipped_by_vol = self._vol_regime_skip(rvol_map.get(d))
            elif d != cur_date:
                self._finalize_day(cur_date, st)
                st = _DayState()
                cur_date = d
                st.skipped_by_vol = self._vol_regime_skip(rvol_map.get(d))

            t = ts.time()

            # ---- capture opening-range bar ----
            if t == p.market_open:
                st.or_high, st.or_low = h, l
                st.or_width = abs(h - l)
                st.has_or = True
                if p.min_or_width_enabled and st.or_width < p.min_or_width:
                    st.or_too_narrow = True
                if p.max_or_width_enabled and st.or_width > p.max_or_width:
                    st.or_too_wide = True
                st.skipped_by_regime = self._regime_skip(st.or_width)

            # buffer levels
            if st.or_high is not None:
                or_size = st.or_width or 0.0
                buf = or_size * (p.buffer_pct_or * 0.01) if p.use_breakout_buffer else 0.0
                long_brk = st.or_high + buf
                short_brk = st.or_low - buf
            else:
                long_brk = short_brk = None

            # PDH/PDL confirmation: raise/lower the effective trigger to PDH/PDL when near
            _pdh = pdh_map.get(d)
            _pdl = pdl_map.get(d)
            pdh = None if _pdh is None or pd.isna(_pdh) else float(_pdh)
            pdl = None if _pdl is None or pd.isna(_pdl) else float(_pdl)
            long_trig, short_trig = self._effective_triggers(st, long_brk, short_brk, pdh, pdl)

            # ---- no-trade reason tracking (bars after OR bar) ----
            if t > p.market_open and st.has_or:
                raw_long = h > st.or_high
                raw_short = l < st.or_low
                if raw_long or raw_short:
                    st.post_raw_break = True
                if (long_brk is not None and h > long_brk) or (short_brk is not None and l < short_brk):
                    st.post_buffered_touch = True
                blc = long_brk is not None and c > long_brk
                bsc = short_brk is not None and c < short_brk
                if blc or bsc:
                    st.post_buffered_close_break = True
                if p.use_vwap_filter:
                    if blc and not (vw is not None and c > vw):
                        st.vwap_blocked = True
                    if bsc and not (vw is not None and c < vw):
                        st.vwap_blocked = True
                # PDH/PDL guard: a buffered close break happened but did not clear PDH/PDL
                if self._pdhpdl_on:
                    if blc and long_trig > long_brk and c <= long_trig:
                        st.pdhpdl_blocked = True
                    if bsc and short_trig < short_brk and c >= short_trig:
                        st.pdhpdl_blocked = True

            # daily loss circuit-breaker: once the day's realised P&L breaches the limit, take no
            # NEW entries (an already-open position still manages to its own exit).
            breaker_ok = (p.daily_loss_limit <= 0) or (st.day_pnl > -p.daily_loss_limit)

            entry_ok_common = (
                p and st.has_or and st.or_high is not None and t > p.market_open
                and not st.skipped_by_regime and not st.skipped_by_vol
                and self._time_window_ok(ts) and breaker_ok
            )

            # filter gates for entry direction
            min_ok = (not p.min_or_width_enabled) or (st.or_width is not None and st.or_width >= p.min_or_width)
            max_ok = (not p.max_or_width_enabled) or (st.or_width is not None and st.or_width <= p.max_or_width)
            vwap_long_ok = (not p.use_vwap_filter) or (vw is not None and c > vw)
            vwap_short_ok = (not p.use_vwap_filter) or (vw is not None and c < vw)

            can_long = (
                p.allow_longs and long_trig is not None and c > long_trig
                and min_ok and max_ok and vwap_long_ok and self._rvol_ok(rv)
            )
            can_short = (
                p.allow_shorts and short_trig is not None and c < short_trig
                and min_ok and max_ok and vwap_short_ok and self._rvol_ok(rv)
            )

            # ---- PRIMARY ENTRY ----
            if entry_ok_common and not st.active and not st.first_taken:
                if can_long:
                    self._enter(st, ts, bar_i, direction=1, c=c, reversal=False)
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "L", c, st.qty_total, None, "entry"))
                elif can_short:
                    self._enter(st, ts, bar_i, direction=-1, c=c, reversal=False)
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "S", c, st.qty_total, None, "entry"))

            # ---- REVERSAL ENTRY ----
            # trigger_on_be_stop uses the RAW OR boundary (earlier/more frequent) instead of
            # requiring a fresh *buffered* close-break past the opposite trigger.
            rev_long_level = st.or_high if self._rev_trigger_raw() else long_brk
            rev_short_level = st.or_low if self._rev_trigger_raw() else short_brk
            if (p.use_reversal and st.prim_stopped and not st.active and entry_ok_common):
                if st.prim_dir == 1 and rev_short_level is not None and c < rev_short_level and max_ok and vwap_short_ok and self._rvol_ok(rv):
                    rqty = self._reversal_qty(st, c, -1)
                    if rqty is not None:
                        self._enter(st, ts, bar_i, direction=-1, c=c, reversal=True, qty=rqty)
                        st.prim_stopped = False
                        self.result.events.append(Event(ts, EV_REVERSAL_ENTRY, "S (Rev)", c, st.qty_total, None, "reversal entry"))
                elif st.prim_dir == -1 and rev_long_level is not None and c > rev_long_level and max_ok and vwap_long_ok and self._rvol_ok(rv):
                    rqty = self._reversal_qty(st, c, 1)
                    if rqty is not None:
                        self._enter(st, ts, bar_i, direction=1, c=c, reversal=True, qty=rqty)
                        st.prim_stopped = False
                        self.result.events.append(Event(ts, EV_REVERSAL_ENTRY, "L (Rev)", c, st.qty_total, None, "reversal entry"))

            # ---- WHIPSAW RE-ENTRY (original direction, once, after primary+reversal both stopped) ----
            if (self._reenter_on and st.reenter_armed and not st.reenter_taken and not st.active
                    and entry_ok_common):
                if st.prim_dir == 1 and st.or_high is not None and c > st.or_high and max_ok and vwap_long_ok and self._rvol_ok(rv):
                    self._enter(st, ts, bar_i, direction=1, c=c, reversal=False, reenter=True)
                    st.reenter_armed = False
                    st.reenter_taken = True
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "L (Re)", c, st.qty_total, None, "whipsaw re-entry"))
                elif st.prim_dir == -1 and st.or_low is not None and c < st.or_low and max_ok and vwap_short_ok and self._rvol_ok(rv):
                    self._enter(st, ts, bar_i, direction=-1, c=c, reversal=False, reenter=True)
                    st.reenter_armed = False
                    st.reenter_taken = True
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "S (Re)", c, st.qty_total, None, "whipsaw re-entry"))

            # ---- EXIT ENGINE ----
            if st.active and st.entry_bar is not None and bar_i > st.entry_bar:
                if st.dir == 1:
                    self._exit_long(st, ts, bar_i, o, h, l, c, vw)
                elif st.dir == -1:
                    self._exit_short(st, ts, bar_i, o, h, l, c, vw)

            # ---- EOD forced close ----
            if p.eod_exit is not None and t >= p.eod_exit and st.active and st.dir != 0:
                self._eod_close(st, ts, bar_i, c)

        # finalize last day
        if cur_date is not None:
            self._finalize_day(cur_date, st)
        return self.result

    # ---- entry helper ---------------------------------------------------
    def _enter(self, st: _DayState, ts, bar_i, direction, c, reversal, reenter=False, qty=None):
        p = self.p
        st.active = True
        st.dir = direction
        st.in_reversal = reversal
        st.is_reenter = reenter
        st.entry_price = c
        st.entry_bar = bar_i
        st.entry_ts = ts
        or_size = st.or_width or 0.0

        if not reversal:
            # primary AND whipsaw re-entry share the same management (adaptive TP, 1x, partial,
            # BE, VWAP trail). A re-entry must NOT reset the day's primary/first-trade state.
            if not reenter:
                st.prim_dir = direction
                st.first_taken = True
            if direction == 1:
                st.stop = self._long_sl(c, st.or_low)
                tp_dist = max(p.adaptive_tp_min, or_size * p.adaptive_tp_scale) if p.use_adaptive_tp else p.fixed_tp
                st.tp = c + tp_dist
                st.be_level = st.or_high - (p.be_retrace_trigger * or_size) if or_size else None
            else:
                st.stop = self._short_sl(c, st.or_high)
                tp_dist = max(p.adaptive_tp_min, or_size * p.adaptive_tp_scale) if p.use_adaptive_tp else p.fixed_tp
                st.tp = c - tp_dist
                st.be_level = st.or_low + (p.be_retrace_trigger * or_size) if or_size else None
            st.qty_total = p.trade_qty
        else:
            # reversal: TP per _reversal_tp_dist (fixed $5, OR-scaled, or None=trail to EOD),
            # SL at the OR boundary opposite the reversal direction.
            tp_dist = self._reversal_tp_dist(or_size)
            if direction == -1:  # short reversal after long primary
                st.stop = st.or_high + p.sl_offset
                st.tp = None if tp_dist is None else c - tp_dist
                st.be_level = st.or_low + (p.be_retrace_trigger * or_size) if or_size else None
            else:                # long reversal after short primary
                st.stop = st.or_low - p.sl_offset
                st.tp = None if tp_dist is None else c + tp_dist
                st.be_level = st.or_high - (p.be_retrace_trigger * or_size) if or_size else None
            st.qty_total = qty if qty is not None else (p.trade_qty * p.reversal_qty_mult)

        st.init_stop = st.stop   # record base SL as the risk basis (before any BE ratchet)
        st.be_triggered = False
        st.part1_closed = False
        st.eff_qty = st.qty_total
        st.trail_active = False
        st.part1_pnl = 0.0
        st.runner_peak = None
        # reversal 'trail_to_eod' rides the full move: no partial scale-out
        st.suppress_partial = bool(reversal and self._rev_on and self._rev_cfg.get("trail_to_eod", False))
        if st.entry_ts_day is None:
            st.entry_ts_day = ts

    def _long_sl(self, entry, or_low):
        p = self.p
        if p.sl_mode == "Candle High/Low":
            return or_low - p.sl_offset
        return entry - p.fixed_sl

    def _short_sl(self, entry, or_high):
        p = self.p
        if p.sl_mode == "Candle High/Low":
            return or_high + p.sl_offset
        return entry + p.fixed_sl

    # ---- LONG exit engine ----------------------------------------------
    def _exit_long(self, st: _DayState, ts, bar_i, o, h, l, c, vw):
        p = self.p
        apply_be = p.use_be_retrace and (not st.in_reversal or p.apply_be_to_reversal)
        sl_at_bar_start = st.stop
        be_before = st.be_triggered
        be_fired_now = False

        # Step 1: BE Retrace
        fire = (c <= st.be_level) if p.be_retrace_use_close else (l <= st.be_level)
        if apply_be and not st.be_triggered and st.be_level is not None and fire:
            st.stop = max(st.stop, st.entry_price)
            st.be_triggered = True
            be_fired_now = True
            self.result.events.append(Event(ts, EV_BE_RETRACE_FIRED, "L", c, st.eff_qty, None, "SL->entry"))

        # Step 2: BE Trail
        if apply_be and st.be_triggered and p.be_trail_amount > 0:
            st.stop = max(st.stop, h - p.be_trail_amount)

        # Step 3: runner exit for the post-partial remainder — either a peak-trail (when
        # runner_trail is on) or the default VWAP cross.
        long_vwap_cross = False
        long_runner_trail = False
        runner_trail_px = None
        if p.use_partial_exit and st.part1_closed:
            if self._runner_trail_on:
                st.runner_peak = h if st.runner_peak is None else max(st.runner_peak, h)
                trail_level = st.runner_peak - self._runner_trail_dist(st)
                # alerts-only: trigger on close beyond the trail and fill at the close
                if (c <= trail_level) if p.exit_on_close else (l <= trail_level):
                    long_runner_trail = True
                    runner_trail_px = c if p.exit_on_close else trail_level
            else:
                if (c - st.entry_price) >= p.partial_activation:
                    st.trail_active = True
                if st.trail_active and vw is not None and c <= vw:
                    long_vwap_cross = True

        # alerts-only fill model: stop exits trigger on the bar CLOSE and fill at the close.
        # Hybrid: a resting protective stop at the OR boundary (init_stop) fills intrabar first,
        # capping a crash bar at the base-SL risk instead of the (much lower) close.
        if p.exit_on_close and p.protective_stop and st.init_stop is not None and l <= st.init_stop:
            sl_hit = True
            sl_fill = st.init_stop
        elif p.exit_on_close:
            sl_hit = c <= st.stop
            sl_fill = c
        else:
            sl_hit = l <= st.stop
            sl_fill = st.stop
        tp_hit = st.tp is not None and h >= st.tp
        anach = p.be_retrace_use_close and be_fired_now and (l <= sl_at_bar_start)

        if sl_hit:
            if anach:
                exit_px = sl_at_bar_start
                st.be_triggered = False
                # cancel the be_retrace_fired event we just appended
                if self.result.events and self.result.events[-1].type == EV_BE_RETRACE_FIRED:
                    self.result.events.pop()
            else:
                exit_px = sl_fill
            reason = "BE Trail" if (st.be_triggered and exit_px > st.entry_price) else ("BE Stop" if st.be_triggered else "Base SL")
            self._close_leg(st, ts, bar_i, exit_px, reason, long=True)
        elif tp_hit:
            if p.use_partial_exit and not st.suppress_partial and not st.part1_closed:
                self._partial(st, ts, long=True)
            else:
                self._close_leg(st, ts, bar_i, st.tp, "TP", long=True)
        elif long_runner_trail:
            self._close_leg(st, ts, bar_i, runner_trail_px, "Trail", long=True)
        elif long_vwap_cross:
            self._close_leg(st, ts, bar_i, c, "VWAP Cross", long=True)

    # ---- SHORT exit engine ---------------------------------------------
    def _exit_short(self, st: _DayState, ts, bar_i, o, h, l, c, vw):
        p = self.p
        apply_be = p.use_be_retrace and (not st.in_reversal or p.apply_be_to_reversal)
        sl_at_bar_start = st.stop
        be_before = st.be_triggered
        be_fired_now = False

        fire = (c >= st.be_level) if p.be_retrace_use_close else (h >= st.be_level)
        if apply_be and not st.be_triggered and st.be_level is not None and fire:
            st.stop = min(st.stop, st.entry_price)
            st.be_triggered = True
            be_fired_now = True
            self.result.events.append(Event(ts, EV_BE_RETRACE_FIRED, "S", c, st.eff_qty, None, "SL->entry"))

        if apply_be and st.be_triggered and p.be_trail_amount > 0:
            st.stop = min(st.stop, l + p.be_trail_amount)

        short_vwap_cross = False
        short_runner_trail = False
        runner_trail_px = None
        if p.use_partial_exit and st.part1_closed:
            if self._runner_trail_on:
                st.runner_peak = l if st.runner_peak is None else min(st.runner_peak, l)
                trail_level = st.runner_peak + self._runner_trail_dist(st)
                if (c >= trail_level) if p.exit_on_close else (h >= trail_level):
                    short_runner_trail = True
                    runner_trail_px = c if p.exit_on_close else trail_level
            else:
                if (st.entry_price - c) >= p.partial_activation:
                    st.trail_active = True
                if st.trail_active and vw is not None and c >= vw:
                    short_vwap_cross = True

        # hybrid: resting protective stop at the OR boundary fills intrabar first (caps crashes)
        if p.exit_on_close and p.protective_stop and st.init_stop is not None and h >= st.init_stop:
            sl_hit = True
            sl_fill = st.init_stop
        elif p.exit_on_close:
            sl_hit = c >= st.stop
            sl_fill = c
        else:
            sl_hit = h >= st.stop
            sl_fill = st.stop
        tp_hit = st.tp is not None and l <= st.tp
        anach = p.be_retrace_use_close and be_fired_now and (h >= sl_at_bar_start)

        if sl_hit:
            if anach:
                exit_px = sl_at_bar_start
                st.be_triggered = False
                if self.result.events and self.result.events[-1].type == EV_BE_RETRACE_FIRED:
                    self.result.events.pop()
            else:
                exit_px = sl_fill
            reason = "BE Trail" if (st.be_triggered and exit_px < st.entry_price) else ("BE Stop" if st.be_triggered else "Base SL")
            self._close_leg(st, ts, bar_i, exit_px, reason, long=False)
        elif tp_hit:
            if p.use_partial_exit and not st.suppress_partial and not st.part1_closed:
                self._partial(st, ts, long=False)
            else:
                self._close_leg(st, ts, bar_i, st.tp, "TP", long=False)
        elif short_runner_trail:
            self._close_leg(st, ts, bar_i, runner_trail_px, "Trail", long=False)
        elif short_vwap_cross:
            self._close_leg(st, ts, bar_i, c, "VWAP Cross", long=False)

    # ---- partial close --------------------------------------------------
    def _partial(self, st: _DayState, ts, long: bool):
        p = self.p
        fill_px = st.tp   # the partial closes AT the TP level (record it before disabling TP)
        part1_qty = st.qty_total * (p.partial_qty_pct / 100.0)
        per_unit = (fill_px - st.entry_price - p.slippage_per_unit) if long else (st.entry_price - fill_px - p.slippage_per_unit)
        st.part1_pnl = per_unit * part1_qty
        st.eff_qty = st.qty_total - part1_qty
        st.part1_closed = True
        st.tp = None  # disable TP; trail takes over
        base_dir = "L" if long else "S"
        d = base_dir + (" (Rev)" if st.in_reversal else "")
        self.result.events.append(Event(ts, EV_PARTIAL_EXIT, d, fill_px, part1_qty, st.part1_pnl,
                                        f"partial {p.partial_qty_pct:.0f}%", note=f"{100 - p.partial_qty_pct:.0f}% trails"))

    # ---- final close of a leg ------------------------------------------
    def _close_leg(self, st: _DayState, ts, bar_i, exit_px, reason, long: bool):
        p = self.p
        per_unit = (exit_px - st.entry_price - p.slippage_per_unit) if long else (st.entry_price - exit_px - p.slippage_per_unit)
        leg_total = per_unit * st.eff_qty
        pnl_total = leg_total + st.part1_pnl
        base_dir = ("L" if long else "S")
        suffix = " (Rev)" if st.in_reversal else (" (Re)" if st.is_reenter else "")
        dir_label = base_dir + suffix
        prefix = "Rev " if st.in_reversal else ("Re " if st.is_reenter else "")
        reason_out = prefix + reason

        leg = TradeLeg(
            day=str(ts.date()), direction=dir_label, is_reversal=st.in_reversal,
            entry_ts=st.entry_ts, entry_price=st.entry_price, exit_ts=ts, exit_price=exit_px,
            qty=st.qty_total, part1_pnl=st.part1_pnl, pnl_total=pnl_total, pnl_per_unit=per_unit,
            reason=reason_out, duration_bars=bar_i - st.entry_bar,
            outcome=("success" if pnl_total > 0 else "failure"),
            risk_amount=(abs(st.entry_price - st.init_stop) * st.qty_total) if st.init_stop is not None else 0.0,
        )
        self.result.trades.append(leg)

        # event tag
        ev_type = {
            "TP": EV_TP_FULL, "BE Trail": EV_BE_TRAIL_EXIT, "BE Stop": EV_BE_STOP_EXIT,
            "Base SL": EV_BASE_SL_EXIT, "VWAP Cross": EV_VWAP_CROSS_EXIT, "EOD": EV_EOD_EXIT,
            "Trail": EV_TRAIL_EXIT,
        }[reason]
        self.result.events.append(Event(ts, ev_type, dir_label, exit_px, st.eff_qty, pnl_total, reason_out))

        # day accumulation
        st.has_trades = True
        st.day_pnl += pnl_total
        st.t1 = dir_label
        st.entry_price_day = st.entry_price
        st.exit_price_day = exit_px
        st.exit_reason_day = reason_out
        st.duration_bars_day = bar_i - st.entry_bar

        # reversal transition: primary SL with reversal enabled -> await reversal
        was_primary_sl = (not st.in_reversal) and (not st.is_reenter) and reason in ("Base SL", "BE Stop", "BE Trail")
        if was_primary_sl and p.use_reversal:
            st.prim_stopped = True
        # whipsaw arming: a REVERSAL leg that stops out -> allow one original-direction re-entry
        if st.in_reversal and self._reenter_on and reason in ("Base SL", "BE Stop", "BE Trail"):
            st.reenter_armed = True

        # clear active-trade fields
        st.active = False
        st.dir = 0
        st.entry_price = None
        st.stop = None
        st.tp = None
        st.entry_bar = None
        st.in_reversal = False
        st.is_reenter = False

    # ---- EOD close ------------------------------------------------------
    def _eod_close(self, st: _DayState, ts, bar_i, c):
        p = self.p
        per_unit = ((c - st.entry_price) if st.dir == 1 else (st.entry_price - c)) - p.slippage_per_unit
        eod_qty = st.eff_qty if st.eff_qty is not None else (st.qty_total if st.qty_total is not None else p.trade_qty)
        pnl_total = per_unit * eod_qty + st.part1_pnl
        base_dir = ("L" if st.dir == 1 else "S")
        suffix = " (Rev)" if st.in_reversal else (" (Re)" if st.is_reenter else "")
        dir_label = base_dir + suffix
        reason_out = ("Rev EOD" if st.in_reversal else ("Re EOD" if st.is_reenter else "EOD"))

        leg = TradeLeg(
            day=str(ts.date()), direction=dir_label, is_reversal=st.in_reversal,
            entry_ts=st.entry_ts, entry_price=st.entry_price, exit_ts=ts, exit_price=c,
            qty=st.qty_total, part1_pnl=st.part1_pnl, pnl_total=pnl_total, pnl_per_unit=per_unit,
            reason=reason_out, duration_bars=bar_i - st.entry_bar,
            outcome=("success" if pnl_total > 0 else "failure"),
            risk_amount=(abs(st.entry_price - st.init_stop) * st.qty_total) if st.init_stop is not None else 0.0,
        )
        self.result.trades.append(leg)
        self.result.events.append(Event(ts, EV_EOD_EXIT, dir_label, c, eod_qty, pnl_total, reason_out))

        st.has_trades = True
        st.day_pnl += pnl_total
        st.t1 = dir_label
        st.entry_price_day = st.entry_price
        st.exit_price_day = c
        st.exit_reason_day = reason_out
        st.duration_bars_day = bar_i - st.entry_bar

        st.active = False
        st.dir = 0
        st.entry_price = None
        st.stop = None
        st.tp = None
        st.entry_bar = None
        st.in_reversal = False
        st.is_reenter = False

    # ---- day finalize ---------------------------------------------------
    def _finalize_day(self, d, st: _DayState):
        day_name = _DAY_NAMES[pd.Timestamp(d).dayofweek]
        rec = DayRecord(
            date=str(d), day_name=day_name, has_trades=st.has_trades,
            or_high=st.or_high, or_low=st.or_low, or_width=st.or_width,
        )
        if st.has_trades:
            rec.t1 = st.t1
            rec.entry_ts = st.entry_ts_day
            rec.entry_price = st.entry_price_day
            rec.exit_price = st.exit_price_day
            rec.exit_reason = st.exit_reason_day
            rec.duration_bars = st.duration_bars_day
            rec.day_net = st.day_pnl
        else:
            rec.no_trade_reason = self._no_trade_reason(st)
        self.result.days.append(rec)

    def _no_trade_reason(self, st: _DayState) -> str:
        if st.skipped_by_vol:
            return "Vol Regime Skip"
        if not st.has_or:
            return "Open Range Missing"
        if st.skipped_by_regime:
            return "OR Regime Skip"
        if st.or_too_narrow:
            return "OR Too Narrow"
        if st.or_too_wide:
            return "OR Too Wide"
        if not st.post_raw_break:
            return "No Setup"
        if st.post_raw_break and not st.post_buffered_touch:
            return "Buffer Not Reached"
        if st.post_buffered_touch and not st.post_buffered_close_break:
            return "No Close Break"
        if st.pdhpdl_blocked:
            return "PDH/PDL Guard"
        if st.vwap_blocked:
            return "VWAP Blocked"
        return "No Close Break"


def run_engine(df: pd.DataFrame, params: Params, enhancements: Optional[dict[str, Any]] = None) -> Result:
    """Convenience wrapper: build an engine and run it over `df`."""
    return OrbEngine(params, enhancements).run(df)
