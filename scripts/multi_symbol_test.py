"""Multi-symbol test of the FROZEN traded profiles (B1 / C1).

The question is NOT "can I find a symbol where this wins" -- with enough symbols something always
does. The question is whether the ORB edge is a PROPERTY OF THE PATTERN (so it shows up broadly on
symbols with the right character) or a property of TSLA (so it doesn't). Four earlier systems here
failed exactly this test, so "TSLA is special" is the null.

Three things this has to get right or the comparison is meaningless:

1. **Frozen configs.** No per-symbol tuning, ever. A per-symbol fit would guarantee winners and
   prove nothing.
2. **Scale-free sizing.** `trade_qty: 1.0` means P&L comes out per SHARE, and a $900 stock moves
   more dollars per share than a $100 one for the same % move. Everything is therefore reported at
   a FIXED $10k notional (shares = 10_000 / price), which is also how the user actually sizes.
3. **Scale-free costs.** `slippage_per_unit: $0.10` was calibrated on a ~$300 TSLA. Held fixed it
   is a trivial cost on a $900 stock and a heavy one on a $50 stock. Default here is
   price-proportional (the same 3.3 bps), with fixed-$0.10 available as a sensitivity.

The dollar params `atr_normalize` does NOT cover (adaptive_tp_min $2.14, reversal_target $5.00,
partial_activation $1.00, be_trail_amount $0.25) are why the universe is restricted to a TSLA-like
price band rather than re-tuned -- re-tuning them per symbol is fitting under another name.

Usage:
  python scripts/multi_symbol_test.py                     # cached symbols, ATR-normalized
  python scripts/multi_symbol_test.py --no-atr-norm       # control: fixed dollar params
  python scripts/multi_symbol_test.py --fixed-slippage    # sensitivity: flat $0.10/share
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.config import load_config                      # noqa: E402
from udb_orb.data.fmp_client import rth_only                # noqa: E402
from udb_orb.engine.metrics import summarize                # noqa: E402
from udb_orb.engine.orb_engine import run_engine            # noqa: E402
from udb_orb.engine.params import Params                    # noqa: E402

CACHE = ROOT / "data" / "cache"
PROFILES = {"B1": "config/tsla_best_B.yaml", "C1": "config/tsla_config_C1.yaml"}
NOTIONAL = 10_000.0
TSLA_REF_PRICE = 300.0        # slippage calibration reference
TSLA_REF_SLIP = 0.10

# ATR-normalization multiples: the values already in the engine's defaults, NOT re-fit here.
ATR_NORM = {"enabled": True, "stop": True, "gate": True, "rev": True,
            "stop_mult": 0.40, "gate_mult": 0.55, "rev_mult": 0.40}


def load_symbol(sym: str, start: str, end: str) -> pd.DataFrame:
    """Read the widest cached 5m parquet for `sym` and slice it to the common window."""
    files = sorted(CACHE.glob(f"{sym}_5min_*.parquet"), key=lambda p: p.stat().st_size)
    if not files:
        return pd.DataFrame()
    df = pd.read_parquet(files[-1])          # widest coverage
    df = rth_only(df)
    return df[(df.index >= start) & (df.index <= end + " 23:59:59")]


def adr_pct(bars: pd.DataFrame) -> float:
    """Average daily range as a % of close — the 'does this symbol move' measure ORB needs."""
    d = bars.groupby(bars.index.date).agg(high=("high", "max"), low=("low", "min"),
                                          close=("close", "last"))
    return float((100 * (d["high"] - d["low"]) / d["close"]).mean())


def run_symbol(sym: str, bars: pd.DataFrame, profile: str, *, atr_norm: bool,
               fixed_slippage: bool) -> dict:
    cfg = load_config(ROOT / PROFILES[profile])
    price = float(bars["close"].mean())

    cfg["profile"]["slippage_per_unit"] = (
        TSLA_REF_SLIP if fixed_slippage else TSLA_REF_SLIP * price / TSLA_REF_PRICE)

    enh = dict(cfg.get("enhancements", {}))
    if atr_norm:
        enh["atr_normalize"] = ATR_NORM

    res = run_engine(bars, Params.from_config(cfg), enh)
    s = summarize(res)
    shares = NOTIONAL / price                     # fixed $10k notional -> comparable across symbols

    return {
        "symbol": sym, "profile": profile, "price": price, "adr_pct": adr_pct(bars),
        "trades": s.trades, "win_rate": s.win_rate,
        "pf": s.profit_factor if s.profit_factor else float("nan"),
        "net_per_share": s.net_pnl, "net_10k": s.net_pnl * shares,
        "ret_pct": 100 * s.net_pnl * shares / NOTIONAL,
        "expectancy_bps": 1e4 * s.expectancy / price if s.trades else 0.0,
        "reversals": s.reversal_entries,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="TSLA,NVDA,AMD,META,AMZN,GOOGL,MSFT,NFLX,SPY,QQQ")
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2026-07-09")     # common window of the cached set
    ap.add_argument("--no-atr-norm", action="store_true")
    ap.add_argument("--fixed-slippage", action="store_true")
    ap.add_argument("--out", default="exports/multi_symbol.csv")
    args = ap.parse_args()

    rows = []
    for sym in [s.strip().upper() for s in args.symbols.split(",")]:
        bars = load_symbol(sym, args.start, args.end)
        if bars.empty:
            print(f"  {sym:<6} no cached 5m data — skipped")
            continue
        days = len(set(bars.index.date))
        for prof in PROFILES:
            rows.append(run_symbol(sym, bars, prof, atr_norm=not args.no_atr_norm,
                                   fixed_slippage=args.fixed_slippage))
        print(f"  {sym:<6} {days} sessions  ${bars['close'].mean():7.2f}  "
              f"ADR {rows[-1]['adr_pct']:.2f}%")

    df = pd.DataFrame(rows)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    mode = ("fixed $0.10" if args.fixed_slippage else "price-scaled") + \
           (" | ATR-norm OFF" if args.no_atr_norm else " | ATR-norm ON")
    for prof in PROFILES:
        d = df[df["profile"] == prof].sort_values("ret_pct", ascending=False)
        print(f"\n{'='*82}\n=== {prof} — {args.start}..{args.end} | costs {mode} | $10k notional\n{'='*82}")
        print(f"  {'sym':<6}{'ADR%':>6}{'price':>9}{'trades':>8}{'WR%':>7}{'PF':>7}"
              f"{'net $10k':>11}{'ret%':>8}{'exp bps':>9}")
        for _, r in d.iterrows():
            print(f"  {r['symbol']:<6}{r['adr_pct']:>6.2f}{r['price']:>9.2f}{r['trades']:>8.0f}"
                  f"{r['win_rate']:>7.1f}{r['pf']:>7.2f}{r['net_10k']:>11,.0f}"
                  f"{r['ret_pct']:>8.1f}{r['expectancy_bps']:>9.2f}")
        wins = int((d["ret_pct"] > 0).sum())
        print(f"  --> profitable on {wins}/{len(d)} symbols; "
              f"median return {d['ret_pct'].median():+.1f}%, mean {d['ret_pct'].mean():+.1f}%")
        ex_tsla = d[d["symbol"] != "TSLA"]
        print(f"  --> EXCLUDING TSLA: {int((ex_tsla['ret_pct']>0).sum())}/{len(ex_tsla)} profitable, "
              f"median {ex_tsla['ret_pct'].median():+.1f}%")
        if len(d) > 2:
            print(f"  --> corr(ADR%, ret%) = {d['adr_pct'].corr(d['ret_pct']):+.2f}   "
                  f"corr(ADR%, expectancy_bps) = {d['adr_pct'].corr(d['expectancy_bps']):+.2f}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
