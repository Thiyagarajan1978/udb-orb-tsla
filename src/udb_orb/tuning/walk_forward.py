"""Walk-forward parameter tuning (enhancement).

Grid-searches a few high-leverage knobs of the Adaptive TP + Reversal profile
(adaptive_tp_scale, be_retrace_trigger, reversal_target) on rolling in-sample windows,
then measures the chosen params on the following out-of-sample window. This is how the
profile can *self-improve* from accumulated data without hand-tuning.

It never mutates the shipped config; it returns a report (and optionally the best params)
for you to review before adopting. Objective = out-of-sample net P&L (ties broken by PF).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from itertools import product
from typing import Any

import pandas as pd

from ..engine.metrics import summarize
from ..engine.orb_engine import run_engine
from ..engine.params import Params

DEFAULT_GRID = {
    "adaptive_tp_scale": [0.75, 1.0, 1.25, 1.5],
    "be_retrace_trigger": [0.25, 0.35, 0.45],
    "reversal_target": [3.0, 5.0, 7.0],
}


@dataclass
class FoldResult:
    fold: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    best_params: dict[str, float]
    is_net: float
    oos_net: float
    oos_trades: int
    oos_win_rate: float


def _with_overrides(cfg: dict, overrides: dict[str, float]) -> Params:
    c = copy.deepcopy(cfg)
    c["profile"].update(overrides)
    return Params.from_config(c)


def _score(bars: pd.DataFrame, params: Params, enh: dict) -> tuple[float, float]:
    res = run_engine(bars, params, enh)
    s = summarize(res)
    pf = s.profit_factor if s.profit_factor is not None else 0.0
    return s.net_pnl, pf


def walk_forward(cfg: dict[str, Any], bars: pd.DataFrame, *, grid: dict | None = None,
                 is_days: int = 120, oos_days: int = 30) -> list[FoldResult]:
    """Rolling walk-forward. `bars` is the full tz-aware RTH 5m history."""
    grid = grid or DEFAULT_GRID
    enh = cfg.get("enhancements", {})
    keys = list(grid.keys())
    combos = [dict(zip(keys, vals)) for vals in product(*(grid[k] for k in keys))]

    days = sorted({d for d in bars.index.date})
    folds: list[FoldResult] = []
    i = 0
    fold_n = 0
    while i + is_days + oos_days <= len(days):
        is_days_set = set(days[i:i + is_days])
        oos_days_set = set(days[i + is_days:i + is_days + oos_days])
        is_bars = bars[[d in is_days_set for d in bars.index.date]]
        oos_bars = bars[[d in oos_days_set for d in bars.index.date]]

        best_combo, best_net, best_pf = None, float("-inf"), float("-inf")
        for combo in combos:
            net, pf = _score(is_bars, _with_overrides(cfg, combo), enh)
            if (net, pf) > (best_net, best_pf):
                best_combo, best_net, best_pf = combo, net, pf

        oos_res = run_engine(oos_bars, _with_overrides(cfg, best_combo), enh)
        oos_s = summarize(oos_res)
        folds.append(FoldResult(
            fold=fold_n,
            is_start=str(min(is_days_set)), is_end=str(max(is_days_set)),
            oos_start=str(min(oos_days_set)), oos_end=str(max(oos_days_set)),
            best_params=best_combo, is_net=best_net,
            oos_net=oos_s.net_pnl, oos_trades=oos_s.trades, oos_win_rate=oos_s.win_rate,
        ))
        fold_n += 1
        i += oos_days
    return folds


def summarize_folds(folds: list[FoldResult]) -> dict[str, Any]:
    if not folds:
        return {"folds": 0, "oos_net_total": 0.0}
    oos_total = sum(f.oos_net for f in folds)
    # most frequently selected value per knob
    consensus: dict[str, Any] = {}
    for k in folds[0].best_params:
        vals = [f.best_params[k] for f in folds]
        consensus[k] = max(set(vals), key=vals.count)
    return {
        "folds": len(folds),
        "oos_net_total": oos_total,
        "oos_net_avg": oos_total / len(folds),
        "consensus_params": consensus,
    }
