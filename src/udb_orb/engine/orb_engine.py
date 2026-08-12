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
from datetime import date, time
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
EV_PD_LEVEL_EXIT = "pd_level_exit"

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
        self.or_bar_count = 0            # bars accumulated into the opening range (multi-bar OR)
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
        self.vwap_cross_bars = 0        # consecutive closes beyond VWAP (vwap_exit.confirm_bars)

        # reversal state machine
        self.prim_dir = 0
        self.prim_stopped = False
        self.qty_total: Optional[float] = None
        # immediate_on_be_stop: reason string of a primary stop that just closed on THIS bar,
        # so the flip can be taken at that same bar's close instead of waiting for an OR break
        self.pending_immediate_rev: Optional[str] = None
        self.prim_ext: Optional[float] = None   # primary's favorable extreme (swing stop basis)

        # whipsaw re-entry (original direction after BOTH primary and reversal stop)
        self.is_reenter = False
        self.reenter_armed = False
        self.reenter_taken = False

        # resume re-entry (original direction after the PRIMARY stops, price resumes)
        self.resume_armed = False
        self.resume_taken = False

        # HTF-trend gate (strict mode): a primary blocked once stays blocked that day/direction
        self.htf_blocked_long = False
        self.htf_blocked_short = False

        # no-trade reason tracking
        self.has_or = False
        self.post_raw_break = False
        self.post_buffered_touch = False
        self.post_buffered_close_break = False
        self.or_too_narrow = False
        self.or_too_wide = False
        self.vwap_blocked = False
        self.pdhpdl_blocked = False

        # prior-day levels (pdO, pdH, pdL, pdC) for the pd_level_exit enhancement; None = unknown
        self.pd_levels: Optional[tuple] = None

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
        self._open_session: Optional[date] = None   # set per-run; see run()
        # ATR normalization: when on, replace the FIXED dollar params (stop cap, OR-width gate,
        # reversal risk cap) with multiples of the current ATR, so the risk profile is constant
        # across volatility regimes and transferable across symbols/prices. Off => fixed dollars.
        _an = self.enh.get("atr_normalize", {})
        self._atr_norm = bool(_an.get("enabled", False))
        self._atr_stop_mult = float(_an.get("stop_mult", 0.40))
        self._atr_gate_mult = float(_an.get("gate_mult", 0.55))
        self._atr_rev_mult = float(_an.get("rev_mult", 0.40))
        self._atr_stop_on = bool(_an.get("stop", True))    # per-param toggles to isolate each effect
        self._atr_gate_on = bool(_an.get("gate", True))
        self._atr_rev_on = bool(_an.get("rev", True))
        self._cur_atr = None
        # Regime-conditional TP (needs tp_mode == "ATR"): classify the day at entry from the opening-
        # range width relative to daily ATR (OR/ATR ratio). Wide OR = expansion/trend day -> wider
        # target (ride the move); narrow OR = range/chop day -> tighter target (bank the quick win,
        # raising hit rate). The ratio is known once the OR bar closes -> no lookahead.
        _rt = self.enh.get("regime_tp", {})
        self._regime_tp_on = bool(_rt.get("enabled", False))
        self._regime_tp_thresh = float(_rt.get("or_atr_thresh", 0.16))
        self._regime_tp_trend = float(_rt.get("trend_mult", 0.40))
        self._regime_tp_range = float(_rt.get("range_mult", 0.15))
        # ATR-trail runner exit ("TRADE TASTIC", default OFF): replaces the runner's VWAP-cross /
        # peak-trail exit for the post-partial remainder. ONE line for both directions
        # (indicators.trade_tastic_trail); the runner exits on the indicator's color flip —
        # a LONG when the bar CLOSES <= the line, a SHORT when it CLOSES > the line (fill at
        # the close — the flip is close-based by definition).
        # hybrid_vwap: keep the VWAP-cross armed too; the runner leaves on whichever fires first.
        # VWAP distance gate (default OFF): the plain VWAP-side condition is a structural no-op here
        # (the buffered close-break implies it — 652/652 entries 2024-26 already satisfied it), so any
        # binding VWAP entry filter must be a DISTANCE: require the entry close to be at least
        # min_frac x OR width beyond the session VWAP (skips breaks that barely cleared a flat open)
        # and/or at most max_frac x OR width beyond it (skips over-extended breaks). 0 = that side off.
        # Gates primary + reversal + resume + re-entry, like use_vwap_filter.
        _vg = self.enh.get("vwap_distance_gate", {})
        self._vg_on = bool(_vg.get("enabled", False))
        self._vg_min = float(_vg.get("min_frac", 0.0) or 0.0)
        self._vg_max = float(_vg.get("max_frac", 0.0) or 0.0)
        # Liquidity gate (default OFF): skip a primary breakout that runs straight into resting
        # "liquidity" — a recent swing high (longs) / swing low (shorts) from the prior
        # lookback_days RTH sessions sitting within proximity_frac x OR width BEYOND the break
        # trigger. Premise (2026-07-24, user-supplied ORB video): such breaks sweep the swing and
        # reverse. Swing = k=1 fractal (bar high > both neighbors / low < both). Primary only —
        # the reversal/resume legs stay ungated (a blocked direction simply doesn't trade).
        _lq = self.enh.get("liquidity_gate", {})
        self._lq_on = bool(_lq.get("enabled", False))
        self._lq_days = int(_lq.get("lookback_days", 2))
        self._lq_frac = float(_lq.get("proximity_frac", 0.5))
        self._lq_map = {}
        # Flow entry (default OFF): take the primary from an EXTERNAL per-date direction instead
        # of a breakout — e.g. the 09:30-09:31 order-flow imbalance. Entry is the OR bar's CLOSE
        # (09:35:00 on a 5m grid), the first executable price at which or_high/or_low — and hence
        # the stop and BE level — are known, so nothing looks ahead. Everything downstream (SL,
        # adaptive/ATR TP, partial, BE retrace, VWAP-cross runner, EOD) is untouched.
        # `signals` is injected by the caller as {date_str: +1 long / -1 short}; absent or 0 = no
        # trade that day. The engine stays network- and file-free: loading is the script's job.
        _fe = self.enh.get("flow_entry", {})
        self._flow_on = bool(_fe.get("enabled", False))
        self._flow_rev_ok = bool(_fe.get("allow_reversal", True))
        self._flow_sig = {str(k): int(v) for k, v in (_fe.get("signals") or {}).items()}
        # E1 mode: enter at the 09:31 price instead of the OR bar's close. `entry_price` is
        # {date: px} (the first executable print at 09:31:00) and `entry_range` is
        # {date: [hi, lo]} of the 09:30-09:31 candle — the only range that exists at that
        # instant, so the primary's stop/BE must be sized from it, not from the 5m OR. Both
        # apply to the PRIMARY only; the reversal cannot fire before 09:35 and keeps the 5m OR.
        # `or_gate` (default True) applies the min/max-OR-width day filters; E1 turns it OFF
        # because the 5m OR width is not knowable at 09:31.
        self._flow_px = {str(k): float(v) for k, v in (_fe.get("entry_price") or {}).items()}
        self._flow_rng = {str(k): (float(v[0]), float(v[1]))
                          for k, v in (_fe.get("entry_range") or {}).items()}
        self._flow_or_gate = bool(_fe.get("or_gate", True))
        # ---- IBS / close-location entry gate (enhancement, default OFF) ----
        # From the ORB+IBS/CLV handover doc (2026-07-30). Each sub-filter is independently
        # toggleable so the doc's own §13 incremental matrix can be run one layer at a time;
        # a sub-filter set to None is not applied. Gates the PRIMARY only unless
        # apply_to_reversal is set — the doc's system has no reversal leg.
        # ---- PRIMARY risk-parity sizing (enhancement, default OFF) ----
        # The reversal has had a dollar-risk cap since 2026-07 (+40% return per $1 of worst-day
        # risk); the primary has not — it takes trade_qty whether its stop is $1.20 or the full
        # $6 cap away, a ~5x swing in dollar risk. Size instead to a constant risk budget:
        # qty = budget / |entry - initial stop|, clamped. Compare on risk-adjusted terms, not
        # raw net: changing size alone moves net without adding edge.
        _ps = self.enh.get("primary_risk_sizing", {})
        self._prs_on = bool(_ps.get("enabled", False))
        self._prs_budget = float(_ps.get("risk_budget", 0.0) or 0.0)
        self._prs_max = _ps.get("max_qty")
        self._prs_min = _ps.get("min_qty")
        _ig = self.enh.get("ibs_entry_gate", {})
        self._ibs_on = bool(_ig.get("enabled", False))
        self._ibs_atr_bars = int(_ig.get("atr_bars", 14))
        self._ibs_brk = _ig.get("breakout_ibs")          # min IBS in the trade's direction
        self._ibs_prev = _ig.get("prev_day_ibs")         # min prior-session IBS, direction-aware
        self._ibs_body = _ig.get("body_min")             # min |close-open| / range
        self._ibs_wick = _ig.get("wick_max")             # max adverse wick / range
        self._ibs_ratr = _ig.get("range_atr")            # [lo, hi] band on range / intraday ATR
        self._ibs_pcp = bool(_ig.get("prior_close_progress", False))
        self._ibs_zero = bool(_ig.get("block_zero_range", True))
        self._ibs_rev = bool(_ig.get("apply_to_reversal", False))
        _tt = self.enh.get("atr_trail_exit", {})
        self._tt_on = bool(_tt.get("enabled", False))
        self._tt_atr_period = int(_tt.get("atr_period", 5))
        self._tt_hhv_period = int(_tt.get("hhv_period", 10))
        self._tt_mult = float(_tt.get("mult", 2.5))
        self._tt_hybrid_vwap = bool(_tt.get("hybrid_vwap", False))
        self._tt_arr = None

    def _atr_or(self, on: bool, mult: float, fixed: float) -> float:
        """mult*ATR when atr_normalize+this param are on and ATR is available, else the fixed value."""
        a = getattr(self, "_cur_atr", None)
        return mult * a if (self._atr_norm and on and a is not None and a == a) else fixed

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
    def _rev_immediate_on(self) -> bool:
        """immediate_on_be_stop (default OFF): flip on the SAME bar the primary stop closes,
        instead of waiting for a close back through the opposite OR boundary."""
        return self._rev_on and bool(self._rev_cfg.get("immediate_on_be_stop", False))

    @property
    def _rev_immediate_reasons(self) -> tuple:
        return tuple(self._rev_cfg.get("immediate_reasons", ["BE Stop"]) or ["BE Stop"])

    @property
    def _reenter_on(self) -> bool:
        return self._rev_on and bool(self._rev_cfg.get("reenter_after_whipsaw", False))

    # ---- resume re-entry (default OFF) ----------------------------------
    @property
    def _resume_cfg(self) -> dict[str, Any]:
        return self.enh.get("resume_reentry", {})

    @property
    def _resume_on(self) -> bool:
        return bool(self._resume_cfg.get("enabled", False))

    @property
    def _resume_disarms(self) -> bool:
        """True: resume and reversal compete for one slot (max 2 legs/day).
        False: both stay armed — a stopped resume can still be followed by a reversal (3 legs)."""
        return bool(self._resume_cfg.get("disarm_other", True))

    def _resume_qty(self, st: _DayState, c: float, direction: int) -> float:
        p = self.p
        stop = (st.or_low - p.sl_offset) if direction == 1 else (st.or_high + p.sl_offset)
        rpu = abs(c - stop)
        qty = p.trade_qty
        cap = float(self._resume_cfg.get("risk_cap", 0.0) or 0.0)
        if cap > 0 and rpu > 0:
            qty = min(qty, cap / rpu)
        return qty

    # ---- PDH/PDL confirmation filter (default OFF) ----------------------
    @property
    def _pdhpdl_cfg(self) -> dict[str, Any]:
        return self.enh.get("pdh_pdl_filter", {})

    @property
    def _pdhpdl_on(self) -> bool:
        return bool(self._pdhpdl_cfg.get("enabled", False))

    # ---- PD-LEVEL TAG-AND-REJECT EXIT (default OFF) ---------------------
    # "price REVERSES at one of the 4 prior-day areas -> get out there instead of riding to the
    # BE stop." A bar that TAGS a prior-day level (within `zone` x TP distance) and CLOSES back
    # REJECTED off it exits the remaining position at that close. Distinct from pdh_pdl_filter
    # (an entry gate) and from a plain take-profit-at-the-level (no reversal required).
    #   levels: which of pdO/pdH/pdL/pdC may trigger it   (default all four)
    #   ahead_only: only levels the trade is moving TOWARD (default FALSE since 2026-08-11 — see
    #     the "both sides" note below; false also watches the level the trade broke, so LOSING a
    #     level you were on the right side of is an exit too)
    #   zone: tag tolerance as a fraction of the adaptive TP distance (0 = must touch)
    #   require_reject_candle: the bar must also close against the trade direction
    #   in_profit_only: arm only once the bar closes in profit (otherwise the stop owns it)
    #   max_body_frac: the tag bar's |close-open| / (high-low) ceiling  (THE knob — see below)
    # History, because the first verdict here was wrong and the comment outlived it: measured
    # 2026-08-10 over 5 years x 24 configs WITHOUT `max_body_frac`, every config lost (it fired on
    # 29% of trades and was right 31% of the time — a tag-and-reject is followed by CONTINUATION
    # ~69% of the time). Adding the small-body requirement (0.25) is what separated a genuine
    # rejection wick from a bar that merely closed the wrong colour on its way through: it cuts the
    # fire rate to ~13% of trades at a ~70% win rate. That version is ADOPTED and DEFAULT ON
    # (Pine v3.9.6, commit 09a8a7f). 2022-2026 D1, per unit: all four levels 498.4 vs 466.7 off.
    # Do not re-derive the "every config loses" claim without the body filter. Level attribution
    # (149 fires): pdO +107.2 / pdH +75.8 / pdL +54.7 / pdC +30.1 — pdC is the weak one, but a
    # 2026-08-11 subset sweep showed dropping ANY single level is worse than keeping all four
    # (drop_pdH 493.8, drop_pdC 488.2, drop_pdO 487.3, drop_pdL 480.4, all < all4 498.4), and no
    # single level alone beats the baseline. See docs/BE_STOP_ANALYSIS.md.
    #
    # "BOTH SIDES" (ahead_only false) — ADOPTED 2026-08-11 BY USER CALL, AGAINST THE MEASUREMENT.
    # It also treats a level BEHIND the entry as a trigger, i.e. price closing back through a level
    # the trade had already won is a rejection. Motivation: the 08-04 and 08-11 shorts both triggered
    # ~$0.33 below pdC and were immediately reclaimed. It does fire on those days (08-04 exits 09:50
    # -1.12 instead of 10:05 -2.45; 08-05 11:05 -1.29 instead of 11:15 -1.91) but the full-history
    # cost is real and was measured before adopting, per unit 2022-01-03..2026-08-10 vs ahead_only:
    #     A1 485.6 vs 502.0   B1 558.3 vs 575.9   C1 556.4 vs 573.2   D1 480.3 vs 483.5
    # B1 by year: 2022 -21.2 / 2023 -5.1 / 2024 -4.8 / 2025 -3.4 / 2026 +17.0 — FOUR of five years
    # worse, win rate 46.2% -> 44.2%, and the pre-fitting-window slice (before 2025-08-11) falls
    # 309.9 -> 278.8. Only 2026 improves, which is the §31 regime-trap signature and also the only
    # year the motivating days came from. Widening `zone` on top of this adds NOTHING to those days
    # (identical at 0.05/0.10/0.20) and costs a further ~$41/unit, so the zone stays at 0.05.
    # Revert = ahead_only true in the five yamls + the Pine "Levels ahead of entry only" box.
    @property
    def _pdx_cfg(self) -> dict[str, Any]:
        return self.enh.get("pd_level_exit", {})

    @property
    def _pdx_on(self) -> bool:
        return bool(self._pdx_cfg.get("enabled", False))

    def _pd_tag_reject(self, st: _DayState, direction: int, o: float, c: float,
                       h: float, l: float) -> bool:
        """True when this bar tags a prior-day level and closes rejected off it."""
        cfg = self._pdx_cfg
        if not self._pdx_on or st.pd_levels is None or st.entry_price is None:
            return False
        if cfg.get("in_profit_only", False) and (c - st.entry_price) * direction <= 0:
            return False
        if cfg.get("require_reject_candle", True) and (c - o) * direction >= 0:
            return False
        # max_body_frac: only fire on a SMALL-bodied rejection (a doji/pin at the level). The
        # post-hoc split found this is the only fire-bar feature that grades monotonically — but
        # see §32c: it is as likely to be exit-PRICE quality (a big-body bar exits at the bottom
        # of its own move) as it is signal quality.
        mbf = float(cfg.get("max_body_frac", 1.0) or 1.0)
        if mbf < 1.0:
            rng = max(h - l, 1e-9)
            if abs(c - o) / rng >= mbf:
                return False
        names = cfg.get("levels", ["pdO", "pdH", "pdL", "pdC"])
        tp_dist = max(self.p.adaptive_tp_min, (st.or_width or 0.0) * self.p.adaptive_tp_scale)
        tol = float(cfg.get("zone", 0.05)) * tp_dist
        ahead_only = bool(cfg.get("ahead_only", False))
        for name, lvl in zip(("pdO", "pdH", "pdL", "pdC"), st.pd_levels):
            if lvl is None or name not in names:
                continue
            if ahead_only and (lvl - st.entry_price) * direction <= 0:
                continue
            if direction == 1:
                if h >= lvl - tol and c < lvl:      # tagged resistance, closed back under it
                    return True
            else:
                if l <= lvl + tol and c > lvl:      # tagged support, closed back over it
                    return True
        return False

    # ---- HTF trend-direction gate (default OFF; concept D) --------------
    # enhancements["htf_trend_filter"] = {"enabled": True, "series": <pd.Series>, "mode": "strict"}
    # series: +1/-1 per 5m bar index = the direction of the last COMPLETED higher-TF bar at
    # that 5m bar's close (built by the caller — e.g. 15m Supertrend-Fakeout). Entries must
    # AGREE with it. "strict" (default): a primary blocked at its break bar stays blocked for
    # that day+direction ("skip it"); "wait": the entry may fire on a later aligned bar.
    @property
    def _htf_cfg(self) -> dict[str, Any]:
        return self.enh.get("htf_trend_filter", {})

    def _htf_ok(self, ts, direction: int) -> bool:
        cfg = self._htf_cfg
        if not cfg.get("enabled", False):
            return True
        ser = cfg.get("series")
        if ser is None:
            return True
        v = ser.get(ts)
        return v is not None and v == direction

    # ---- runner peak-trail (default OFF) -------------------------------
    @property
    def _runner_trail_on(self) -> bool:
        return bool(self.enh.get("runner_trail", {}).get("enabled", False))

    def _runner_trail_dist(self, st: _DayState) -> float:
        cfg = self.enh.get("runner_trail", {})
        # mode "atr": chandelier — trail atr_mult x daily ATR(14) below the peak (adapts to
        # regime instead of the day's OR). Falls back to OR sizing on ATR-warmup days.
        if str(cfg.get("mode", "or")).lower().startswith("atr"):
            a = getattr(self, "_cur_atr", None)
            if a is not None and a == a:
                return float(cfg.get("atr_mult", 0.25)) * a
        mult = float(cfg.get("or_mult", 0.75))
        return mult * (st.or_width or 0.0)

    @property
    def _runner_hybrid_vwap(self) -> bool:
        """runner_trail.hybrid_vwap: with the trail ON, ALSO keep the VWAP-cross exit armed —
        the runner leaves on whichever fires first (default OFF = trail replaces VWAP)."""
        return bool(self.enh.get("runner_trail", {}).get("hybrid_vwap", False))

    # ---- VWAP-cross exit confirmation (defaults = current behavior) -----
    @property
    def _vwap_confirm_bars(self) -> int:
        """Consecutive closes beyond VWAP required to exit (1 = classic single-close cross)."""
        return int(self.enh.get("vwap_exit", {}).get("confirm_bars", 1))

    @property
    def _vwap_min_cross_frac(self) -> float:
        """If > 0: a single close beyond VWAP by >= frac x OR width exits immediately,
        overriding confirm_bars (a decisive cross needs no confirmation)."""
        return float(self.enh.get("vwap_exit", {}).get("min_cross_frac", 0.0))

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
        cap = self._atr_or(self._atr_rev_on, self._atr_rev_mult, p.reversal_risk_cap)
        if cap and cap > 0 and risk_per_unit > 0:
            if p.reversal_risk_mode == "skip":
                if risk_per_unit * qty > cap:
                    return None
            else:  # scale
                qty = min(qty, cap / risk_per_unit)
        return qty

    def _immediate_rev_stop(self, st: _DayState, c: float, direction: int) -> Optional[float]:
        """Stop for an immediate (same-bar) flip, or None if no sane stop exists.

        A BE-stop exit can land *inside* the opening range — or, for a short primary that
        BE-stops only slightly above entry, still BELOW the OR low. Parking the flip's stop at
        the OR boundary would then put the stop on the WRONG side of entry (or a hair away,
        which the risk-parity cap turns into an absurd position size). So:
          - 'swing'       : stop at the primary's favorable extreme (the failed swing).
          - 'or_boundary' : the standard reversal stop, falling back to 'swing' when invalid.
        Either way the distance must clear `immediate_min_risk_or_mult` x OR width.
        """
        p = self.p
        or_size = st.or_width or 0.0
        mode = str(self._rev_cfg.get("immediate_stop_mode", "swing"))
        cands = []
        if mode == "or_boundary":
            cands.append((st.or_low - p.sl_offset) if direction == 1 else (st.or_high + p.sl_offset))
        if st.prim_ext is not None:
            cands.append((st.prim_ext - p.sl_offset) if direction == 1 else (st.prim_ext + p.sl_offset))
        floor = float(self._rev_cfg.get("immediate_min_risk_or_mult", 0.15)) * or_size
        for stop in cands:
            risk = (c - stop) if direction == 1 else (stop - c)
            if risk > 0 and risk >= floor:
                return stop
        return None

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
    def run(self, df: pd.DataFrame, open_session: Optional[date] = None) -> Result:
        """Replay `df`. `open_session` marks a date whose session is still IN PROGRESS.

        Backtests leave it None: every date in the frame is a complete session, so its last bar
        is a legitimate flatten bar (this is what closes positions on half days). LIVE must set
        it to today, because there "the last bar" is merely the newest bar that has closed —
        without it the engine flattens every open position on the newest bar of every poll and
        emits a fresh, false `eod_exit` alert each time. See `run` -> `last_ts_by_date`.
        """
        if df.empty:
            return self.result
        self._open_session = open_session
        df = df.sort_index()
        vwap = indicators.session_vwap(df)
        rvol = indicators.relative_volume(df, int(self.enh.get("rvol_filter", {}).get("lookback_bars", 20)))
        if self._tt_on:
            self._tt_arr = indicators.trade_tastic_trail(
                df, atr_period=self._tt_atr_period, hhv_period=self._tt_hhv_period,
                mult=self._tt_mult).to_numpy()
        if self._lq_on:
            # per-session k=1 fractal swings, then day -> swings of the prior lookback sessions
            import numpy as np
            sess_dates, sess_hi, sess_lo = [], [], []
            for sd, g in df.groupby(df.index.date):
                hh = g["high"].to_numpy(float)
                ll = g["low"].to_numpy(float)
                sh = [hh[i] for i in range(1, len(hh) - 1) if hh[i] > hh[i - 1] and hh[i] > hh[i + 1]]
                sl = [ll[i] for i in range(1, len(ll) - 1) if ll[i] < ll[i - 1] and ll[i] < ll[i + 1]]
                sess_dates.append(sd)
                sess_hi.append(sh)
                sess_lo.append(sl)
            for i, sd in enumerate(sess_dates):
                lo_i = max(0, i - self._lq_days)
                self._lq_map[sd] = (
                    np.array([x for s in sess_hi[lo_i:i] for x in s], dtype=float),
                    np.array([x for s in sess_lo[lo_i:i] for x in s], dtype=float))

        # Last bar of each session. Half sessions (e.g. Christmas Eve, 13:00 close) never produce
        # a bar at/after eod_exit, so without this a position would be silently dropped at the
        # day rollover instead of being closed.
        last_ts_by_date = {d: g.index.max() for d, g in df.groupby(df.index.date)}
        # ...but an in-progress session has no last bar yet — only the eod_exit clock may flatten
        # it. Dropping it here is what stops the live loop re-firing a false EOD exit every poll.
        if self._open_session is not None:
            last_ts_by_date.pop(self._open_session, None)

        # Prior-day high/low (PDH/PDL) per date, from the RTH session data itself.
        _daily = df.groupby(df.index.date).agg(hi=("high", "max"), lo=("low", "min"),
                                               cl=("close", "last"), op=("open", "first"))
        pdh_map = _daily["hi"].shift(1).to_dict()
        pdl_map = _daily["lo"].shift(1).to_dict()
        pdo_map = _daily["op"].shift(1).to_dict()

        # Prior-N-day realised daily volatility (%) — known at the open, so usable pre-entry.
        _vcfg = self.enh.get("volatility_regime", {})
        _lb = int(_vcfg.get("lookback", 20))
        _rvol = (_daily["cl"].pct_change().rolling(_lb).std() * 100.0).shift(1)
        rvol_map = _rvol.to_dict()

        # 14-day ATR (from daily H/L/C), shifted so it is known at the open (no lookahead). Used by
        # the "ATR Cap" stop mode to size the cap by volatility instead of a fixed dollar amount.
        _pc = _daily["cl"].shift(1)
        _tr = pd.concat([_daily["hi"] - _daily["lo"], (_daily["hi"] - _pc).abs(),
                         (_daily["lo"] - _pc).abs()], axis=1).max(axis=1)
        atr_map = _tr.rolling(int(self.p.atr_period)).mean().shift(1).to_dict()

        # ---- IBS / close-location gate inputs (enhancement, default OFF) ----
        # Prior session's Internal Bar Strength (Close-Low)/(High-Low) and prior close, both
        # shifted so only completed sessions are visible, plus an intraday ATR shifted one bar
        # so the breakout candle cannot inflate its own reference range.
        _rng = (_daily["hi"] - _daily["lo"])
        _dibs = ((_daily["cl"] - _daily["lo"]) / _rng.where(_rng > 0)).fillna(0.5)
        pibs_map = _dibs.shift(1).to_dict()
        pclose_map = _daily["cl"].shift(1).to_dict()
        _itr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(),
                          (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
        iatr = _itr.rolling(int(self._ibs_atr_bars)).mean().shift(1)

        st = _DayState()
        cur_date = None
        bar_i = -1
        p = self.p
        prev_close = None
        prev_date = None
        recent = []   # (date, close) history for multi-bar breakout confirmation

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
                self._cur_atr = atr_map.get(d)
            elif d != cur_date:
                self._finalize_day(cur_date, st)
                st = _DayState()
                cur_date = d
                st.skipped_by_vol = self._vol_regime_skip(rvol_map.get(d))
                self._cur_atr = atr_map.get(d)

            t = ts.time()

            # ---- capture opening-range (spans p.or_bars bars from market_open) ----
            or_completing_now = False
            if t == p.market_open:
                st.or_high, st.or_low = h, l
                st.or_bar_count = 1
            elif st.or_bar_count and not st.has_or and st.or_bar_count < p.or_bars:
                st.or_high = max(st.or_high, h)
                st.or_low = min(st.or_low, l)
                st.or_bar_count += 1
            if st.or_bar_count and not st.has_or and st.or_bar_count >= p.or_bars:
                st.or_width = abs(st.or_high - st.or_low)
                st.has_or = True
                or_completing_now = True   # do NOT enter on the bar that completes the OR
                if p.min_or_width_enabled and st.or_width < p.min_or_width:
                    st.or_too_narrow = True
                if p.max_or_width_enabled and st.or_width > self._atr_or(self._atr_gate_on, self._atr_gate_mult, p.max_or_width):
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

            # prior-day levels for the pd_level_exit enhancement (inert unless enabled)
            if self._pdx_on and st.pd_levels is None:
                def _f(v):
                    return None if v is None or pd.isna(v) else float(v)
                st.pd_levels = (_f(pdo_map.get(d)), pdh, pdl, _f(pclose_map.get(d)))

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

            # Flatten bar = at/after the EOD cutoff, OR the session's last bar (half days).
            is_last_bar = ts == last_ts_by_date.get(d)
            is_flatten_bar = (p.eod_exit is not None and t >= p.eod_exit) or is_last_bar

            entry_ok_common = (
                p and st.has_or and st.or_high is not None and t > p.market_open
                and not or_completing_now  # not on the bar that just completed a multi-bar OR
                and not st.skipped_by_regime and not st.skipped_by_vol
                and self._time_window_ok(ts) and breaker_ok
                and not is_flatten_bar     # no new entries at/after EOD (parity with the Pine port)
            )

            # filter gates for entry direction
            min_ok = (not p.min_or_width_enabled) or (st.or_width is not None and st.or_width >= p.min_or_width)
            max_ok = (not p.max_or_width_enabled) or (st.or_width is not None and st.or_width <= self._atr_or(self._atr_gate_on, self._atr_gate_mult, p.max_or_width))
            vwap_long_ok = (not p.use_vwap_filter) or (vw is not None and c > vw)
            vwap_short_ok = (not p.use_vwap_filter) or (vw is not None and c < vw)
            if self._vg_on and vw is not None and st.or_width:
                dl, ds = c - vw, vw - c   # signed distance beyond VWAP per direction
                if self._vg_min > 0:
                    vwap_long_ok = vwap_long_ok and dl >= self._vg_min * st.or_width
                    vwap_short_ok = vwap_short_ok and ds >= self._vg_min * st.or_width
                if self._vg_max > 0:
                    vwap_long_ok = vwap_long_ok and dl <= self._vg_max * st.or_width
                    vwap_short_ok = vwap_short_ok and ds <= self._vg_max * st.or_width

            # 2-close acceptance: require the PREVIOUS bar to also have closed beyond the trigger
            # (a confirmation filter borrowed from the v1.23 PDH/PDL acceptance concept).
            two_close = bool(self.enh.get("confirm_two_closes", {}).get("enabled", False))
            same_day_prev = prev_close is not None and prev_date == d
            long_confirm = (not two_close) or (same_day_prev and long_trig is not None and prev_close > long_trig)
            short_confirm = (not two_close) or (same_day_prev and short_trig is not None and prev_close < short_trig)

            # Confirmation candle: after a fresh close-break, require the NEXT candle to HOLD beyond
            # the trigger before entering (and optionally not be an against-trend candle). Rejects
            # the extended first-break bar that snaps back inside — e.g. 2026-07-09 10:00 break,
            # 10:05 back inside; only the 11:35 break + 11:40 hold is a valid entry.
            cb = self.enh.get("confirm_breakout", {})
            if cb.get("enabled", False):
                trend = bool(cb.get("require_trend_candle", True))
                # hold_bars = how many PRIOR consecutive same-day bars must have closed beyond the
                # trigger before entering. 1 = the 2-candle rule (break bar + this entry bar);
                # 2 = a 3-candle rule (break bar + 2 holds + entry), etc.
                hold = max(1, int(cb.get("hold_bars", 1)))
                held_long = len(recent) >= hold and all(
                    pd_ == d and long_trig is not None and pc_ > long_trig for pd_, pc_ in recent[-hold:])
                held_short = len(recent) >= hold and all(
                    pd_ == d and short_trig is not None and pc_ < short_trig for pd_, pc_ in recent[-hold:])
                lc = held_long and (c >= o or not trend)
                sc = held_short and (c <= o or not trend)
                long_confirm = long_confirm and lc
                short_confirm = short_confirm and sc

            # Max entry-extension guard: skip a breakout whose entry (bar close) is already more
            # than `or_mult` * OR width from the OR-boundary stop — i.e. price is over-extended and
            # the stop would be too far away. (Pairs well with confirmation, which enters later.)
            mee = self.enh.get("max_entry_ext", {})
            long_ext_ok = short_ext_ok = True
            if mee.get("enabled", False) and st.or_width:
                band = float(mee.get("or_mult", 1.5)) * st.or_width
                long_ext_ok = (c - st.or_low) <= band
                short_ext_ok = (st.or_high - c) <= band

            lq_long_ok = lq_short_ok = True
            if self._lq_on and st.or_width:
                hs, ls = self._lq_map.get(d, (None, None))
                band = self._lq_frac * st.or_width
                if hs is not None and hs.size and long_trig is not None:
                    lq_long_ok = not bool(((hs > long_trig) & (hs <= long_trig + band)).any())
                if ls is not None and ls.size and short_trig is not None:
                    lq_short_ok = not bool(((ls < short_trig) & (ls >= short_trig - band)).any())

            _pibs, _pcl = pibs_map.get(d), pclose_map.get(d)
            _ia = iatr.loc[ts] if self._ibs_on else None
            ibs_long_ok = self._ibs_ok(1, o, h, l, c, _pibs, _pcl, _ia)
            ibs_short_ok = self._ibs_ok(-1, o, h, l, c, _pibs, _pcl, _ia)

            can_long = (
                p.allow_longs and long_trig is not None and c > long_trig and long_confirm and long_ext_ok
                and min_ok and max_ok and vwap_long_ok and lq_long_ok and self._rvol_ok(rv)
                and ibs_long_ok
            )
            can_short = (
                p.allow_shorts and short_trig is not None and c < short_trig and short_confirm and short_ext_ok
                and min_ok and max_ok and vwap_short_ok and lq_short_ok and self._rvol_ok(rv)
                and ibs_short_ok
            )

            # HTF trend gate on the primary. strict: a mismatch at the break bar kills that
            # direction's primary for the day ("skip it"); wait: it may enter later once aligned.
            if self._htf_cfg.get("enabled", False):
                strict = str(self._htf_cfg.get("mode", "strict")) == "strict"
                if can_long and not self._htf_ok(ts, 1):
                    if strict:
                        st.htf_blocked_long = True
                    can_long = False
                if can_short and not self._htf_ok(ts, -1):
                    if strict:
                        st.htf_blocked_short = True
                    can_short = False
                can_long = can_long and not st.htf_blocked_long
                can_short = can_short and not st.htf_blocked_short

            # ---- FLOW ENTRY (enhancement, default OFF) — replaces the breakout primary ----
            # Fires on the OR-completing bar's close. It deliberately bypasses `entry_ok_common`,
            # which requires t > market_open and forbids the OR bar: those guard the *breakout*
            # trigger, which cannot be evaluated until after the OR. The day-level filters that do
            # apply (vol regime, OR-width gates, loss breaker) are re-checked here.
            if self._flow_on and or_completing_now and not st.active and not st.first_taken:
                fdir = self._flow_sig.get(str(d), 0)
                px = self._flow_px.get(str(d))
                rng = self._flow_rng.get(str(d))
                gate_ok = (min_ok and max_ok) if self._flow_or_gate else True
                have_px = (not self._flow_px) or (px is not None and rng is not None)
                if (fdir and have_px and not st.skipped_by_regime and not st.skipped_by_vol
                        and gate_ok and breaker_ok):
                    ep = px if px is not None else c
                    self._enter(st, ts, bar_i, direction=fdir, c=ep, reversal=False,
                                range_override=rng)
                    self.result.events.append(Event(
                        ts, EV_PRIMARY_ENTRY, "L" if fdir == 1 else "S", ep, st.qty_total,
                        None, "flow entry"))

            # ---- PRIMARY ENTRY ----
            if entry_ok_common and not self._flow_on and not st.active and not st.first_taken:
                if can_long:
                    self._enter(st, ts, bar_i, direction=1, c=c, reversal=False)
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "L", c, st.qty_total, None, "entry"))
                elif can_short:
                    self._enter(st, ts, bar_i, direction=-1, c=c, reversal=False)
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "S", c, st.qty_total, None, "entry"))

            # ---- REVERSAL ENTRY ----
            # trigger_on_be_stop uses the RAW OR boundary (earlier/more frequent) instead of
            # requiring a fresh *buffered* close-break past the opposite trigger.
            # reversal_capture.midline_trigger (default OFF, 2026-07-25 ORB-webinar concept): trigger
            # at the OR MIDLINE instead — the reversal enters as soon as price closes back through
            # mid-range after the stop, roughly half an OR earlier. Entry is nearer the reversal's
            # stop (the crossed boundary), so per-unit risk shrinks and the risk-parity cap sizes up.
            rev_long_level = st.or_high if self._rev_trigger_raw() else long_brk
            rev_short_level = st.or_low if self._rev_trigger_raw() else short_brk
            if bool(self._rev_cfg.get("midline_trigger", False)) and st.or_high is not None and st.or_low is not None:
                rev_long_level = rev_short_level = (st.or_high + st.or_low) / 2.0
            if (p.use_reversal and (self._flow_rev_ok or not self._flow_on)
                    and st.prim_stopped and not st.active and entry_ok_common):
                if st.prim_dir == 1 and rev_short_level is not None and c < rev_short_level and max_ok and vwap_short_ok and self._rvol_ok(rv) and self._htf_ok(ts, -1) and (ibs_short_ok or not self._ibs_rev):
                    rqty = self._reversal_qty(st, c, -1)
                    if rqty is not None:
                        self._enter(st, ts, bar_i, direction=-1, c=c, reversal=True, qty=rqty)
                        st.prim_stopped = False
                        if self._resume_disarms:
                            st.resume_armed = False   # reversal wins; disarm the resume leg
                        self.result.events.append(Event(ts, EV_REVERSAL_ENTRY, "S (Rev)", c, st.qty_total, None, "reversal entry"))
                elif st.prim_dir == -1 and rev_long_level is not None and c > rev_long_level and max_ok and vwap_long_ok and self._rvol_ok(rv) and self._htf_ok(ts, 1) and (ibs_long_ok or not self._ibs_rev):
                    rqty = self._reversal_qty(st, c, 1)
                    if rqty is not None:
                        self._enter(st, ts, bar_i, direction=1, c=c, reversal=True, qty=rqty)
                        st.prim_stopped = False
                        if self._resume_disarms:
                            st.resume_armed = False
                        self.result.events.append(Event(ts, EV_REVERSAL_ENTRY, "L (Rev)", c, st.qty_total, None, "reversal entry"))

            # ---- RESUME RE-ENTRY (same direction, after the primary stopped and price resumed) ----
            if (self._resume_on and st.resume_armed and not st.resume_taken and not st.active
                    and entry_ok_common):
                _raw = str(self._resume_cfg.get("trigger", "buffered")) == "raw"
                res_long_level = st.or_high if _raw else long_brk
                res_short_level = st.or_low if _raw else short_brk
                if st.prim_dir == 1 and res_long_level is not None and c > res_long_level and max_ok and vwap_long_ok and self._rvol_ok(rv):
                    self._enter(st, ts, bar_i, direction=1, c=c, reversal=False, reenter=True,
                                qty=self._resume_qty(st, c, 1))
                    st.resume_armed = False
                    st.resume_taken = True
                    if self._resume_disarms:
                        st.prim_stopped = False   # resume wins; disarm the reversal
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "L (Re)", c, st.qty_total, None, "resume re-entry"))
                elif st.prim_dir == -1 and res_short_level is not None and c < res_short_level and max_ok and vwap_short_ok and self._rvol_ok(rv):
                    self._enter(st, ts, bar_i, direction=-1, c=c, reversal=False, reenter=True,
                                qty=self._resume_qty(st, c, -1))
                    st.resume_armed = False
                    st.resume_taken = True
                    if self._resume_disarms:
                        st.prim_stopped = False
                    self.result.events.append(Event(ts, EV_PRIMARY_ENTRY, "S (Re)", c, st.qty_total, None, "resume re-entry"))

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

            # Track the primary's favorable extreme — the structural swing the trade ran to.
            # immediate_stop_mode 'swing' parks the flip's stop there.
            if st.active and not st.in_reversal and st.dir != 0:
                if st.dir == 1:
                    st.prim_ext = h if st.prim_ext is None else max(st.prim_ext, h)
                else:
                    st.prim_ext = l if st.prim_ext is None else min(st.prim_ext, l)

            # ---- EXIT ENGINE ----
            if st.active and st.entry_bar is not None and bar_i > st.entry_bar:
                if st.dir == 1:
                    self._exit_long(st, ts, bar_i, o, h, l, c, vw)
                elif st.dir == -1:
                    self._exit_short(st, ts, bar_i, o, h, l, c, vw)

            # ---- IMMEDIATE REVERSAL (enhancement, default OFF) ----
            # The primary just stopped on THIS bar's close; flip right here rather than waiting
            # for a close back through the opposite OR boundary. Entry is the same close the
            # stop filled at (close-mode stops fill at the bar close, so this is executable).
            # If the direction filters reject it, prim_stopped stays armed and the normal
            # OR-break reversal can still fire later.
            if st.pending_immediate_rev is not None:
                rdir = -st.prim_dir
                if (p.use_reversal and not st.active and entry_ok_common and rdir != 0 and max_ok
                        and (vwap_long_ok if rdir == 1 else vwap_short_ok)
                        and self._rvol_ok(rv) and self._htf_ok(ts, rdir)):
                    rstop = self._immediate_rev_stop(st, c, rdir)
                    rqty = None
                    if rstop is not None:
                        # risk parity, same cap as the standard reversal — but it may only SHRINK
                        # the 2x base size, never inflate it off a tight stop.
                        risk_pu = abs(c - rstop)
                        cap = self._atr_or(self._atr_rev_on, self._atr_rev_mult, p.reversal_risk_cap)
                        rqty = p.trade_qty * p.reversal_qty_mult
                        if cap and cap > 0 and risk_pu > 0:
                            if p.reversal_risk_mode == "skip" and risk_pu * rqty > cap:
                                rqty = None
                            elif p.reversal_risk_mode != "skip":
                                rqty = min(rqty, cap / risk_pu)
                    if rqty is not None and rqty > 0:
                        self._enter(st, ts, bar_i, direction=rdir, c=c, reversal=True, qty=rqty,
                                    stop_override=rstop)
                        st.prim_stopped = False
                        if self._resume_disarms:
                            st.resume_armed = False
                        lbl = "L (Rev)" if rdir == 1 else "S (Rev)"
                        self.result.events.append(
                            Event(ts, EV_REVERSAL_ENTRY, lbl, c, st.qty_total, None, "immediate reversal"))
                st.pending_immediate_rev = None

            # ---- EOD forced close (also on the session's last bar, for half days) ----
            if is_flatten_bar and st.active and st.dir != 0:
                self._eod_close(st, ts, bar_i, c)

            prev_close = c
            prev_date = d
            recent.append((d, c))

        # finalize last day
        if cur_date is not None:
            self._finalize_day(cur_date, st)
        return self.result

    # ---- entry helper ---------------------------------------------------
    def _enter(self, st: _DayState, ts, bar_i, direction, c, reversal, reenter=False, qty=None,
               stop_override=None, range_override=None):
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
            # range_override (flow_entry E1): size the stop / BE off the 09:30-09:31 candle,
            # the only range in existence at that entry. Local to this leg — st.or_* is left
            # alone so the reversal, runner trail and day filters keep the real 5m OR.
            if range_override is not None:
                r_hi, r_lo = range_override
                or_size = abs(r_hi - r_lo)
            else:
                r_hi, r_lo = st.or_high, st.or_low
            if direction == 1:
                st.stop = self._long_sl(c, r_lo, r_hi)
                tp_dist = self._tp_dist(or_size)
                st.tp = c + tp_dist
                st.be_level = r_hi - (p.be_retrace_trigger * or_size) if or_size else None
            else:
                st.stop = self._short_sl(c, r_hi, r_lo)
                tp_dist = self._tp_dist(or_size)
                st.tp = c - tp_dist
                st.be_level = r_lo + (p.be_retrace_trigger * or_size) if or_size else None
            st.qty_total = qty if qty is not None else p.trade_qty
            if (qty is None and self._prs_on and self._prs_budget > 0
                    and st.stop is not None):
                rpu = abs(c - st.stop)
                if rpu > 0:
                    q = self._prs_budget / rpu
                    if self._prs_max is not None:
                        q = min(q, float(self._prs_max))
                    if self._prs_min is not None:
                        q = max(q, float(self._prs_min))
                    st.qty_total = q
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
            if stop_override is not None:
                st.stop = stop_override
            st.qty_total = qty if qty is not None else (p.trade_qty * p.reversal_qty_mult)

        st.init_stop = st.stop   # record base SL as the risk basis (before any BE ratchet)
        st.be_triggered = False
        st.part1_closed = False
        st.eff_qty = st.qty_total
        st.trail_active = False
        st.part1_pnl = 0.0
        st.runner_peak = None
        st.vwap_cross_bars = 0
        # reversal 'trail_to_eod' rides the full move: no partial scale-out
        st.suppress_partial = bool(reversal and self._rev_on and self._rev_cfg.get("trail_to_eod", False))
        if st.entry_ts_day is None:
            st.entry_ts_day = ts

    def _ibs_ok(self, direction, o, h, l, c, prev_ibs, prev_close, iatr) -> bool:
        """ORB+IBS/CLV close-location gate. True = the doc's filters allow this breakout.

        IBS is (close-low)/(high-low), flipped for shorts so it always reads 'closed in the
        trade's favour'. A zero-range bar scores the neutral 0.50 rather than NaN and, by
        default, cannot trigger an entry at all (doc §4).
        """
        if not self._ibs_on:
            return True
        rng = h - l
        if rng <= 0:
            return not self._ibs_zero
        ibs = (c - l) / rng
        fav = ibs if direction == 1 else 1.0 - ibs
        if self._ibs_brk is not None and fav < float(self._ibs_brk):
            return False
        if self._ibs_body is not None and abs(c - o) / rng < float(self._ibs_body):
            return False
        if self._ibs_wick is not None:
            wick = (h - max(o, c)) / rng if direction == 1 else (min(o, c) - l) / rng
            if wick > float(self._ibs_wick):
                return False
        if self._ibs_ratr is not None:
            if iatr is None or iatr != iatr or not iatr:   # !=self excludes NaN warmup
                return False
            lo_b, hi_b = float(self._ibs_ratr[0]), float(self._ibs_ratr[1])
            if not (lo_b <= rng / iatr <= hi_b):
                return False
        if self._ibs_prev is not None:
            if prev_ibs is None or prev_ibs != prev_ibs:
                return False
            thr = float(self._ibs_prev)
            if (prev_ibs < thr) if direction == 1 else (prev_ibs > 1.0 - thr):
                return False
        if self._ibs_pcp:
            if prev_close is None or prev_close != prev_close:
                return False
            if (c - prev_close) * direction < 0:
                return False
        return True

    def _tp_dist(self, or_size: float) -> float:
        """Primary TP distance. Fixed (default), Adaptive (OR-scaled), or ATR (volatility-scaled)."""
        p = self.p
        m = p.tp_mode.lower()
        if m.startswith("adapt"):
            return max(p.adaptive_tp_min, or_size * p.adaptive_tp_scale)
        if m.startswith("atr"):
            _a = getattr(self, "_cur_atr", None)
            if _a is not None and _a == _a:           # _a==_a excludes NaN (early warmup days)
                mult = p.atr_tp_mult
                if self._regime_tp_on and or_size and _a:
                    ratio = or_size / _a               # OR width relative to daily ATR
                    mult = self._regime_tp_trend if ratio >= self._regime_tp_thresh else self._regime_tp_range
                return max(p.atr_tp_min, mult * _a)
            return p.fixed_tp                          # fallback before ATR is available
        return p.fixed_tp

    def _long_sl(self, entry, or_low, or_high=None):
        """OR-boundary stop, optionally capped so it is never further than `fixed_sl` below entry."""
        p = self.p
        raw = or_low - p.sl_offset
        if p.sl_mode == "Candle High/Low":
            return raw
        if p.sl_mode == "Candle High/Low + Max Cap":
            return max(raw, entry - self._atr_or(self._atr_stop_on, self._atr_stop_mult, p.fixed_sl))
        # ATR-scaled cap: like Max-Cap but the cap is atr_mult * ATR (volatility-normalized).
        if p.sl_mode == "Candle High/Low + ATR Cap":
            _a = getattr(self, "_cur_atr", None)
            cap = p.atr_mult * _a if (_a is not None and _a == _a) else p.fixed_sl   # _a==_a excludes NaN
            return max(raw, entry - cap)
        # OR-midpoint stop (LuxAlgo "moderate" 1:1.5): tighter than the boundary; cancel the
        # breakout if price pulls back through the middle of the opening range.
        if p.sl_mode in ("OR Midpoint", "OR Midpoint + Max Cap") and or_high is not None:
            mid = (or_high + or_low) / 2.0
            return max(mid, entry - p.fixed_sl) if p.sl_mode.endswith("Max Cap") else mid
        return entry - p.fixed_sl

    def _short_sl(self, entry, or_high, or_low=None):
        p = self.p
        raw = or_high + p.sl_offset
        if p.sl_mode == "Candle High/Low":
            return raw
        if p.sl_mode == "Candle High/Low + Max Cap":
            return min(raw, entry + p.fixed_sl)
        if p.sl_mode == "Candle High/Low + ATR Cap":
            _a = getattr(self, "_cur_atr", None)
            cap = p.atr_mult * _a if (_a is not None and _a == _a) else p.fixed_sl
            return min(raw, entry + cap)
        if p.sl_mode in ("OR Midpoint", "OR Midpoint + Max Cap") and or_low is not None:
            mid = (or_high + or_low) / 2.0
            return min(mid, entry + p.fixed_sl) if p.sl_mode.endswith("Max Cap") else mid
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

        # Step 3: runner exit for the post-partial remainder — a peak-trail (when runner_trail
        # is on), the VWAP cross (default), or BOTH armed at once (runner_trail.hybrid_vwap).
        long_vwap_cross = False
        long_runner_trail = False
        runner_trail_px = None
        if p.use_partial_exit and st.part1_closed:
            if self._tt_on:
                # TRADE TASTIC: long exits when the bar CLOSES at/below the line (green->red)
                if c <= self._tt_arr[bar_i]:
                    long_runner_trail = True
                    runner_trail_px = c
            elif self._runner_trail_on:
                st.runner_peak = h if st.runner_peak is None else max(st.runner_peak, h)
                trail_level = st.runner_peak - self._runner_trail_dist(st)
                # alerts-only: trigger on close beyond the trail and fill at the close
                if (c <= trail_level) if p.exit_on_close else (l <= trail_level):
                    long_runner_trail = True
                    runner_trail_px = c if p.exit_on_close else trail_level
            if (self._tt_on and self._tt_hybrid_vwap) or \
               (not self._tt_on and ((not self._runner_trail_on) or self._runner_hybrid_vwap)):
                if (c - st.entry_price) >= p.partial_activation:
                    st.trail_active = True
                if st.trail_active and vw is not None:
                    if c <= vw:
                        # confirm_bars consecutive closes beyond VWAP, unless the cross is
                        # decisive (>= min_cross_frac x OR width) — defaults reproduce the
                        # classic single-close cross exactly.
                        st.vwap_cross_bars += 1
                        decisive = (self._vwap_min_cross_frac > 0 and st.or_width
                                    and (vw - c) >= self._vwap_min_cross_frac * st.or_width)
                        if st.vwap_cross_bars >= self._vwap_confirm_bars or decisive:
                            long_vwap_cross = True
                    else:
                        st.vwap_cross_bars = 0

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
            # intrabar fill. "stop" = exactly at the stop (Pine parity). "touch"/"resting" =
            # gap-aware broker stop: a bar opening BELOW the stop fills at the worse open price.
            # "resting" (be_lag): a BE-retrace that fired THIS bar cannot protect it — fill against
            # the pre-retrace stop, since a stop moved at the bar close only rests from the next bar.
            rest = sl_at_bar_start if (p.be_lag and be_fired_now) else st.stop
            sl_hit = l <= rest
            sl_fill = min(rest, o) if p.stop_fill_touch else rest
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
        elif self._pd_tag_reject(st, 1, o, c, h, l):
            self._close_leg(st, ts, bar_i, c, "PD Level", long=True)

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
            if self._tt_on:
                # TRADE TASTIC: short exits when the bar CLOSES ABOVE the SAME line (red->green
                # flip). FIXED 2026-07-23 — no mirrored short line exists in the original script.
                if c > self._tt_arr[bar_i]:
                    short_runner_trail = True
                    runner_trail_px = c
            elif self._runner_trail_on:
                st.runner_peak = l if st.runner_peak is None else min(st.runner_peak, l)
                trail_level = st.runner_peak + self._runner_trail_dist(st)
                if (c >= trail_level) if p.exit_on_close else (h >= trail_level):
                    short_runner_trail = True
                    runner_trail_px = c if p.exit_on_close else trail_level
            if (self._tt_on and self._tt_hybrid_vwap) or \
               (not self._tt_on and ((not self._runner_trail_on) or self._runner_hybrid_vwap)):
                if (st.entry_price - c) >= p.partial_activation:
                    st.trail_active = True
                if st.trail_active and vw is not None:
                    if c >= vw:
                        st.vwap_cross_bars += 1
                        decisive = (self._vwap_min_cross_frac > 0 and st.or_width
                                    and (c - vw) >= self._vwap_min_cross_frac * st.or_width)
                        if st.vwap_cross_bars >= self._vwap_confirm_bars or decisive:
                            short_vwap_cross = True
                    else:
                        st.vwap_cross_bars = 0

        # hybrid: resting protective stop at the OR boundary fills intrabar first (caps crashes)
        if p.exit_on_close and p.protective_stop and st.init_stop is not None and h >= st.init_stop:
            sl_hit = True
            sl_fill = st.init_stop
        elif p.exit_on_close:
            sl_hit = c >= st.stop
            sl_fill = c
        else:
            # "touch"/"resting": gap-aware — a bar opening ABOVE the stop fills the short worse.
            # "resting" (be_lag): a same-bar BE-retrace cannot protect its own firing bar.
            rest = sl_at_bar_start if (p.be_lag and be_fired_now) else st.stop
            sl_hit = h >= rest
            sl_fill = max(rest, o) if p.stop_fill_touch else rest
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
        elif self._pd_tag_reject(st, -1, o, c, h, l):
            self._close_leg(st, ts, bar_i, c, "PD Level", long=False)

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
            "Trail": EV_TRAIL_EXIT, "PD Level": EV_PD_LEVEL_EXIT,
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
            if self._rev_immediate_on and reason in self._rev_immediate_reasons:
                st.pending_immediate_rev = reason
        # resume re-entry arms on the SAME primary stop that arms the reversal
        if was_primary_sl and self._resume_on:
            st.resume_armed = True
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


def run_engine(df: pd.DataFrame, params: Params, enhancements: Optional[dict[str, Any]] = None,
               open_session: Optional[date] = None) -> Result:
    """Convenience wrapper: build an engine and run it over `df`.

    Pass `open_session=<today>` from the live loop — see `OrbEngine.run`.
    """
    return OrbEngine(params, enhancements).run(df, open_session=open_session)
