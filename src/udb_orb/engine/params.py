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

    # execution realism: alerts-only -> stop exits fill at the bar close, not the stop level
    exit_on_close: bool = False
    # hybrid: a resting protective stop at the OR boundary fills intrabar (caps crash bars)
    protective_stop: bool = False

    # reversal risk control (the 2x reversal drives the worst days)
    reversal_risk_cap: float = 0.0      # 0 = off; cap the reversal leg's dollar risk
    reversal_risk_mode: str = "scale"   # "scale" (shrink qty) | "skip" (no reversal)

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
            exit_on_close=bool(cfg.get("execution", {}).get("exit_on_close", False)),
            protective_stop=bool(cfg.get("execution", {}).get("protective_stop", False)),
            reversal_risk_cap=float(p.get("reversal_risk_cap", 0.0) or 0.0),
            reversal_risk_mode=str(p.get("reversal_risk_mode", "scale")),
        )
