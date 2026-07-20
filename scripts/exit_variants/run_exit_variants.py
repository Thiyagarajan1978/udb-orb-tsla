"""Runner-exit variant experiment on the B1 profile (config/tsla_best_B.yaml).

Tests three alternative runner exits (added 2026-07-20 as default-OFF engine toggles;
nothing in the adopted configs changed) against the B1 VWAP-cross baseline:

  V1 hybrid      : runner_trail.hybrid_vwap — peak-trail AND VWAP cross both armed,
                   the runner leaves on whichever fires first
  V2 confirmed   : vwap_exit.confirm_bars / min_cross_frac — the VWAP cross needs N
                   consecutive closes beyond VWAP (a decisive cross >= frac x OR
                   overrides the wait)
  V3 chandelier  : runner_trail.mode "atr" — trail atr_mult x daily ATR14 below the
                   runner peak instead of 0.75 x OR width

Windows: TRAIN 2024-25, HOLDOUT 2026-01-01..2026-07-17, plus per-year 2022-2026.
Per 1 unit, $0.10 slippage, close-triggered stops (B1 execution model).

Run: python scripts/exit_variants/run_exit_variants.py
Findings 2026-07-20: V2 no edge; V1 converges to plain trail (VWAP almost never fires
first); V3 chandelier is the only family >= baseline on BOTH train and holdout
(train-best 0.35xATR; 0.25xATR has the best full-history total and year balance,
rescuing 2024, but gives back some 2022/2025). Not adopted — decision pending.
"""
import copy
from collections import Counter
from datetime import date
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from udb_orb.config import load_config
from udb_orb.backtest.runner import load_bars
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

TRAIN = (date(2024, 1, 1), date(2025, 12, 31))
HOLD = (date(2026, 1, 1), date(2026, 7, 17))
FULL_START = date(2022, 1, 1)

BASE_CFG = load_config(REPO / "config" / "tsla_best_B.yaml")


def variant(name, **enh_over):
    cfg = copy.deepcopy(BASE_CFG)
    for block, kv in enh_over.items():
        cfg["enhancements"].setdefault(block, {}).update(kv)
    return name, cfg


VARIANTS = [
    variant("B1 baseline (VWAP)"),
    variant("A1-ref peak-trail 0.75xOR", runner_trail={"enabled": True, "or_mult": 0.75}),
    variant("V1 HYBRID trail+VWAP", runner_trail={"enabled": True, "or_mult": 0.75, "hybrid_vwap": True}),
    variant("V2a VWAP 2-close +0.25xOR override", vwap_exit={"confirm_bars": 2, "min_cross_frac": 0.25}),
    variant("V2b VWAP 2-close pure", vwap_exit={"confirm_bars": 2}),
    variant("V2c VWAP 3-close +0.25xOR override", vwap_exit={"confirm_bars": 3, "min_cross_frac": 0.25}),
    variant("V3 chandelier 0.15xATR", runner_trail={"enabled": True, "mode": "atr", "atr_mult": 0.15}),
    variant("V3 chandelier 0.25xATR", runner_trail={"enabled": True, "mode": "atr", "atr_mult": 0.25}),
    variant("V3 chandelier 0.35xATR", runner_trail={"enabled": True, "mode": "atr", "atr_mult": 0.35}),
    variant("V1b HYBRID chand0.25 + VWAP", runner_trail={"enabled": True, "mode": "atr", "atr_mult": 0.25, "hybrid_vwap": True}),
]


def metrics(result):
    tr = result.trades
    if not tr:
        return dict(n=0, net=0.0, wr=0.0, pf=0.0, worst=0.0, exits=Counter())
    net = sum(t.pnl_total for t in tr)
    wins = [t.pnl_total for t in tr if t.pnl_total > 0]
    losses = [t.pnl_total for t in tr if t.pnl_total <= 0]
    gw, gl = sum(wins), -sum(losses)
    day_pnl = Counter()
    for t in tr:
        day_pnl[t.day] += t.pnl_total
    return dict(n=len(tr), net=net, wr=100 * len(wins) / len(tr),
                pf=(gw / gl) if gl > 0 else float("inf"),
                worst=min(day_pnl.values()), exits=Counter(t.reason for t in tr))


def main():
    bars = load_bars(BASE_CFG, FULL_START, HOLD[1])
    print(f"{len(bars)} bars {bars.index.min().date()} .. {bars.index.max().date()}")

    windows = {
        "TRAIN 2024-25": bars[(bars.index.date >= TRAIN[0]) & (bars.index.date <= TRAIN[1])],
        "HOLDOUT 2026": bars[(bars.index.date >= HOLD[0]) & (bars.index.date <= HOLD[1])],
    }

    results = {}
    for name, cfg in VARIANTS:
        params = Params.from_config(cfg)
        enh = cfg.get("enhancements", {})
        results[name] = {w: metrics(run_engine(wdf.copy(), params, enh)) for w, wdf in windows.items()}
        full = run_engine(bars.copy(), params, enh)
        ynet = Counter()
        for t in full.trades:
            ynet[int(t.day[:4])] += t.pnl_total
        results[name]["years"] = ynet

    for w in windows:
        print(f"\n=== {w} (per 1 unit; x100 for 100 sh) ===")
        print(f"{'variant':<38}{'n':>5}{'net':>10}{'WR%':>7}{'PF':>7}{'worst d':>9}")
        for name, _ in VARIANTS:
            m = results[name][w]
            print(f"{name:<38}{m['n']:>5}{m['net']:>10.2f}{m['wr']:>7.1f}{m['pf']:>7.2f}{m['worst']:>9.2f}")

    years = [2022, 2023, 2024, 2025, 2026]
    print(f"\n=== per-year net 2022-2026 ===\n{'variant':<38}" + "".join(f"{y:>9}" for y in years) + f"{'TOTAL':>10}")
    for name, _ in VARIANTS:
        yn = results[name]["years"]
        print(f"{name:<38}" + "".join(f"{yn.get(y, 0):>9.1f}" for y in years) + f"{sum(yn.values()):>10.1f}")


if __name__ == "__main__":
    main()
