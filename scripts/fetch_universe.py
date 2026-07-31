"""Fetch 5m history for the scanner universe, in parallel, into data/cache/.

Universe is PRE-REGISTERED on character, not on returns: liquid US equities that actually gap
(semis, high-beta software, crypto-adjacent, EV, travel, biotech, high-short-interest names) plus
index controls. Picking names because they backtest well is the failure mode this whole exercise
is trying to avoid.

Price band is applied LATER (see scan_universe.py): the frozen profiles still carry dollar params
that `atr_normalize` does not cover (adaptive_tp_min $2.14, reversal_target $5.00,
partial_activation $1.00), so a $10 stock would get a TP floor it can never reach. Fetching is
cheap, so we pull everything and filter structurally afterwards.

  python scripts/fetch_universe.py --workers 8
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from udb_orb.data.fmp_client import fetch_5min          # noqa: E402

CACHE = ROOT / "data" / "cache"

UNIVERSE = [
    # semis
    "NVDA", "AMD", "MU", "MRVL", "AVGO", "ARM", "SMCI", "INTC", "QCOM", "LRCX", "AMAT", "ON",
    # high-beta software / growth
    "PLTR", "CRWD", "SNOW", "NET", "DDOG", "MDB", "ZS", "TWLO", "SHOP", "RBLX", "PATH", "U",
    # crypto-adjacent
    "COIN", "MSTR", "MARA", "RIOT", "HOOD",
    # EV / auto
    "TSLA", "RIVN", "GM", "F",
    # consumer / travel / discretionary
    "ABNB", "UBER", "DKNG", "LYFT", "CHWY", "ETSY", "RH", "LULU", "NKE", "SBUX", "CVNA",
    # biotech / pharma
    "MRNA", "BNTX", "VRTX", "REGN", "LLY",
    # mega / industrials / energy / materials
    "META", "AMZN", "GOOGL", "MSFT", "AAPL", "NFLX", "DIS", "BA", "GE", "CAT", "OXY", "FCX", "X",
    # high-short-interest / retail-flow
    "GME", "SOFI", "AFRM", "UPST",
    # index controls (expected NEGATIVE - they anchor the cross-section)
    "SPY", "QQQ", "IWM",
]

START, END = date(2024, 1, 2), date(2026, 7, 9)     # matches the already-cached mega-cap window


def pull(sym: str) -> tuple[str, int, str]:
    target = CACHE / f"{sym}_5min_{START}_{END}.parquet"
    if target.exists():
        return sym, -1, "cached"
    try:
        t0 = time.time()
        df = fetch_5min(sym, START, END, cache_dir=CACHE, use_cache=True)
        return sym, len(df), f"{time.time()-t0:.0f}s"
    except Exception as exc:
        return sym, 0, f"FAILED {exc!r}"[:90]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--symbols", default=None)
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else UNIVERSE
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"fetching {len(syms)} symbols {START}..{END} with {args.workers} workers")

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(pull, s): s for s in syms}
        for f in as_completed(futs):
            sym, n, note = f.result()
            done += 1
            flag = "" if n != 0 else "  <-- CHECK"
            print(f"  [{done:>2}/{len(syms)}] {sym:<6} {n:>7} bars  {note}{flag}", flush=True)
    print("done")


if __name__ == "__main__":
    main()
