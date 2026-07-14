"""Effective engine parameters.

Mirrors the Pine v12.4.3 *profile-resolution* block for the
"Adaptive TP + Reversal (Best Combined)" profile with Auto-Tune ON @ 5m.
Building `Params` from config is the single source of truth for the engine;
tests assert the resolved defaults match the Pine profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _resolve_fill_mode(execution: dict) -> dict:
    """Map execution config -> (exit_on_close, stop_fill_touch, be_lag). stop_fill_mode wins if
    present; otherwise fall back to the legacy exit_on_close bool.

    "resting" = the realistic broker model: gap-aware intrabar fill like "touch", PLUS a BE-retrace
    to break-even cannot protect its OWN firing bar (a stop moved on the bar close only rests from
    the next bar). "touch"/"stop"/"close" keep the legacy same-bar BE-retrace protection, which is
    intrabar look-ahead that overstates net (see docs/BE_STOP_ANALYSIS resting-fill note)."""
    mode = execution.get("stop_fill_mode")
    if mode is None:
        mode = "close" if bool(execution.get("exit_on_close", False)) else "stop"
    touch = mode in ("touch", "resting")
    return {"exit_on_close": mode == "close", "stop_fill_touch": touch, "be_lag": mode == "resting"}


@dataclass(frozen=True)
class Params:
    # session
    market_open: time
    eod_exit: time
    rth_start: time
    rth_end: time
    timeframe_minutes: int

    # sizing
    trade_qty: float
    slippage_per_unit: float

    # buffer
    use_breakout_buffer: bool
    buffer_pct_or: float

    # stop
    sl_mode: str
    sl_offset: float
    fixed_sl: float

    # take profit
    tp_mode: str            # "Adaptive" | "Fixed"
    adaptive_tp_scale: float
    adaptive_tp_min: float
    fixed_tp: float

    # BE retrace
    use_be_retrace: bool
    be_retrace_trigger: float
    be_trail_amount: float
    be_retrace_use_close: bool

    # partial + vwap trail
    use_partial_exit: bool
    partial_qty_pct: float
    partial_activation: float

    # reversal
    use_reversal: bool
    reversal_qty_mult: float
    reversal_target: float
    apply_be_to_reversal: bool

    # filters
    min_or_width_enabled: bool
    min_or_width: float
    max_or_width_enabled: bool
    max_or_width: float
    use_vwap_filter: bool

    # side
    trade_side_mode: str    # "Both" | "Long Only" | "Short Only"

    # execution realism. stop_fill_mode selects how a stop exit fills:
    #   "close" : alerts-only — trigger & fill on the bar CLOSE (the default live workflow)
    #   "stop"  : Pine intrabar — trigger on the wick, fill exactly at the stop level
    #   "touch" : broker resting stop — trigger on the wick, gap-aware fill at min(stop, open)
    #   "resting": like "touch" but a BE-retrace to entry cannot protect its OWN firing bar
    #              (no intrabar look-ahead). This is the realistic model; "touch" overstates net.
    exit_on_close: bool = False      # derived: True iff stop_fill_mode == "close"
    stop_fill_touch: bool = False    # derived: True iff stop_fill_mode in ("touch","resting")
    be_lag: bool = False             # derived: True iff stop_fill_mode == "resting"
    # ATR-scaled stop cap ("Candle High/Low + ATR Cap" sl_mode): cap = atr_mult * ATR(atr_period),
    # volatility-normalized instead of a fixed dollar cap. Enables cross-symbol / cross-regime scaling.
    atr_mult: float = 0.35
    atr_period: int = 14
    # ATR-adaptive TP (tp_mode == "ATR"): target distance = atr_tp_mult * ATR(atr_period), floored at
    # atr_tp_min. Volatility-normalized so the same rule fits high- and low-price/vol regimes.
    atr_tp_mult: float = 0.25
    atr_tp_min: float = 1.0
    # Opening range spans this many bars from market_open (1 = the single first bar; on 1m data
    # or_bars=15 = a 15-minute range, then trade breakouts on the base (1m) resolution).
    or_bars: int = 1
    # hybrid: a resting protective stop at the OR boundary fills intrabar (caps crash bars)
    protective_stop: bool = False

    # reversal risk control (the 2x reversal drives the worst days)
    reversal_risk_cap: float = 0.0      # 0 = off; cap the reversal leg's dollar risk
    reversal_risk_mode: str = "scale"   # "scale" (shrink qty) | "skip" (no reversal)

    # daily loss circuit-breaker: block new entries once day P&L <= -limit (0 = off)
    daily_loss_limit: float = 0.0

    @property
    def use_adaptive_tp(self) -> bool:
        return self.tp_mode.lower().startswith("adapt")

    @property
    def allow_longs(self) -> bool:
        return self.trade_side_mode != "Short Only"

    @property
    def allow_shorts(self) -> bool:
        return self.trade_side_mode != "Long Only"

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Params":
        s = cfg["session"]
        p = cfg["profile"]
        return cls(
            market_open=_parse_hhmm(s["market_open"]),
            eod_exit=_parse_hhmm(s["eod_exit"]),
            rth_start=_parse_hhmm(s["rth_start"]),
            rth_end=_parse_hhmm(s["rth_end"]),
            timeframe_minutes=int(cfg.get("timeframe_minutes", 5)),
            trade_qty=float(p["trade_qty"]),
            slippage_per_unit=float(p["slippage_per_unit"]),
            use_breakout_buffer=bool(p["use_breakout_buffer"]),
            buffer_pct_or=float(p["buffer_pct_or"]),
            sl_mode=str(p["sl_mode"]),
            sl_offset=float(p["sl_offset"]),
            fixed_sl=float(p["fixed_sl"]),
            tp_mode=str(p["tp_mode"]),
            adaptive_tp_scale=float(p["adaptive_tp_scale"]),
            adaptive_tp_min=float(p["adaptive_tp_min"]),
            fixed_tp=float(p["fixed_tp"]),
            use_be_retrace=bool(p["use_be_retrace"]),
            be_retrace_trigger=float(p["be_retrace_trigger"]),
            be_trail_amount=float(p["be_trail_amount"]),
            be_retrace_use_close=bool(p["be_retrace_use_close"]),
            use_partial_exit=bool(p["use_partial_exit"]),
            atr_mult=float(p.get("atr_mult", 0.35)),
            atr_period=int(p.get("atr_period", 14)),
            atr_tp_mult=float(p.get("atr_tp_mult", 0.25)),
            atr_tp_min=float(p.get("atr_tp_min", 1.0)),
            or_bars=int(cfg["session"].get("or_bars", 1)),
            partial_qty_pct=float(p["partial_qty_pct"]),
            partial_activation=float(p["partial_activation"]),
            use_reversal=bool(p["use_reversal"]),
            reversal_qty_mult=float(p["reversal_qty_mult"]),
            reversal_target=float(p["reversal_target"]),
            apply_be_to_reversal=bool(p["apply_be_to_reversal"]),
            min_or_width_enabled=bool(p["min_or_width_enabled"]),
            min_or_width=float(p["min_or_width"]),
            max_or_width_enabled=bool(p["max_or_width_enabled"]),
            max_or_width=float(p["max_or_width"]),
            use_vwap_filter=bool(p["use_vwap_filter"]),
            trade_side_mode=str(p["trade_side_mode"]),
            **_resolve_fill_mode(cfg.get("execution", {})),
            protective_stop=bool(cfg.get("execution", {}).get("protective_stop", False)),
            reversal_risk_cap=float(p.get("reversal_risk_cap", 0.0) or 0.0),
            reversal_risk_mode=str(p.get("reversal_risk_mode", "scale")),
            daily_loss_limit=float(cfg.get("execution", {}).get("daily_loss_limit", 0.0) or 0.0),
        )
