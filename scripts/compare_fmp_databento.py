#!/usr/bin/env python
"""Cross-check FMP 5m bars against Databento for one session.

FMP is the sole provider for signals; this audits it against an independent feed.
Databento's equity datasets are **T+1** on the historical API and this account has no
live licence, so the newest checkable session is yesterday's.

  python scripts/compare_fmp_databento.py 2026-07-29
  python scripts/compare_fmp_databento.py 2026-07-29 --dataset DBEQ.BASIC

XNAS.ITCH is Nasdaq-only (TSLA's primary listing) so its VOLUME is a subset of the
consolidated tape FMP reports — compare volume shape/ratio, not absolute levels. The
EQUS.SUMMARY daily line printed at the end is the official consolidated total.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytz  # noqa: E402

ET = pytz.timezone("America/New_York")


def databento_bars(day: date, dataset: str, interval: str) -> pd.DataFrame:
    """1-minute OHLCV from Databento, resampled onto the FMP bar grid (no-op for 1min)."""
    import databento as db
    from forward_test import get_db_key

    client = db.Historical(get_db_key())
    d = client.timeseries.get_range(
        dataset=dataset, symbols=["TSLA"], stype_in="raw_symbol", schema="ohlcv-1m",
        start=f"{day}T13:30", end=f"{day}T20:00").to_df()
    d.index = pd.DatetimeIndex(pd.to_datetime(d.index, utc=True)).tz_convert(ET)
    out = d[["open", "high", "low", "close", "volume"]].sort_index()
    if interval == "1min":
        return out
    return (out.resample("5min", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"}).dropna())


def fmp_bars(day: date, interval: str) -> pd.DataFrame:
    """FMP intraday bars for one session. `fmp_client` only wraps 5min, so 1min goes direct."""
    from udb_orb.data.fmp_client import _parse_chart, fetch_5min, rth_only
    if interval == "5min":
        return rth_only(fetch_5min("TSLA", day, day, use_cache=True))

    import requests
    from udb_orb.config import get_fmp_key
    url = (f"https://financialmodelingprep.com/stable/historical-chart/1min"
           f"?symbol=TSLA&from={day}&to={day}&apikey={get_fmp_key()}")
    data = requests.get(url, timeout=30).json()
    if isinstance(data, dict):
        raise RuntimeError(f"FMP error: {str(data)[:200]}")
    return rth_only(_parse_chart(data))


def consolidated_daily(day: date) -> pd.Series | None:
    """Official consolidated (all-venue) daily bar — the volume ground truth."""
    import databento as db
    from forward_test import get_db_key
    try:
        d = db.Historical(get_db_key()).timeseries.get_range(
            dataset="EQUS.SUMMARY", symbols=["TSLA"], stype_in="raw_symbol",
            schema="ohlcv-1d", start=str(day),
            end=str(day + timedelta(days=1))).to_df()
        return d.iloc[0] if len(d) else None
    except Exception as e:  # dataset not entitled / not yet published
        print(f"  (EQUS.SUMMARY unavailable: {str(e)[:80]})")
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="FMP vs Databento intraday cross-check")
    ap.add_argument("day")
    ap.add_argument("--interval", default="5min", choices=["5min", "1min"])
    ap.add_argument("--dataset", default="XNAS.ITCH")
    ap.add_argument("--out", default=None, help="CSV path (default exports/)")
    args = ap.parse_args(argv)

    day = date.fromisoformat(args.day)
    fmp = fmp_bars(day, args.interval)
    dbx = databento_bars(day, args.dataset, args.interval)
    common = fmp.index.intersection(dbx.index)
    if len(fmp) != len(dbx) or len(common) != len(fmp):
        print(f"GRID MISMATCH  FMP {len(fmp)}  {args.dataset} {len(dbx)}  common {len(common)}")
        missing_f = dbx.index.difference(fmp.index)
        missing_d = fmp.index.difference(dbx.index)
        if len(missing_f):
            print(f"  absent from FMP      ({len(missing_f)}): {[str(t.time())[:5] for t in missing_f][:12]}")
        if len(missing_d):
            print(f"  absent from Databento({len(missing_d)}): {[str(t.time())[:5] for t in missing_d][:12]}")
    fmp, dbx = fmp.loc[common], dbx.loc[common]
    n = len(fmp)

    j = pd.DataFrame(index=fmp.index)
    for c in ("open", "high", "low", "close", "volume"):
        j[f"{c}_fmp"] = fmp[c].values
        j[f"{c}_db"] = dbx[c].values
        j[f"d_{c}"] = (fmp[c].values - dbx[c].values).round(4)

    print(f"\n=== TSLA {day}: FMP vs {args.dataset} ({n} {args.interval} bars) ===")
    print("\n-- price deltas (FMP - Databento, $) --")
    px = [f"d_{c}" for c in ("open", "high", "low", "close")]
    print(j[px].describe().round(4).T[["mean", "std", "min", "max"]])
    for c in ("open", "high", "low", "close"):
        within = (j[f"d_{c}"].abs() < 0.005).sum()
        print(f"  {c:<6} within $0.005: {within}/{n}   max abs {j[f'd_{c}'].abs().max():.3f}")
    print(f"  close correlation: {np.corrcoef(j.close_fmp, j.close_db)[0, 1]:.6f}")

    orb_f, orb_d = fmp.iloc[0], dbx.iloc[0]
    print(f"\n-- first bar of the session (09:30, {args.interval}) --")
    print(f"  FMP        H {orb_f.high:.4f}  L {orb_f.low:.4f}  width {orb_f.high - orb_f.low:.4f}")
    print(f"  {args.dataset:<10} H {orb_d.high:.4f}  L {orb_d.low:.4f}  width {orb_d.high - orb_d.low:.4f}")

    print("\n-- volume --")
    imposs = j.index[j.volume_fmp < j.volume_db]
    print(f"  FMP RTH total   {int(j.volume_fmp.sum()):>12,}")
    print(f"  {args.dataset} RTH total {int(j.volume_db.sum()):>12,}"
          f"   ratio {j.volume_fmp.sum() / j.volume_db.sum():.2f}")
    print(f"  per-bar correlation: {np.corrcoef(j.volume_fmp, j.volume_db)[0, 1]:.3f}")
    print(f"  bars where FMP < single-venue volume (impossible for a consolidated tape): "
          f"{len(imposs)}/{n} {[str(t.time())[:5] for t in imposs][:15]}")

    # FMP's failure mode is sporadic per-bar DROPOUTS, not a scale error: the healthy bars
    # hold a tight ratio to the venue feed, so re-state the stats with the dropouts removed.
    ratio = j.volume_fmp / j.volume_db
    ok = ratio >= 1.5
    if ok.sum() > 2 and (~ok).any():
        print(f"  ratio: median {ratio.median():.2f} (all) vs {ratio[ok].median():.2f} "
              f"+/- {ratio[ok].std():.2f} excluding {int((~ok).sum())} dropout bars")
        print(f"  correlation excluding dropouts: "
              f"{np.corrcoef(j.volume_fmp[ok], j.volume_db[ok])[0, 1]:.3f}")
        lost = j.volume_db[~ok] * ratio[ok].median() - j.volume_fmp[~ok]
        print(f"  volume missing from the dropout bars: ~{int(lost.sum()):,} shares "
              f"({100 * lost.sum() / j.volume_fmp.sum():.1f}% of the FMP session total)")
        print("    NOTE: the last bar's 'dropout' is usually the closing auction on the "
              "primary venue, a real structural difference rather than an FMP fault.")
    cd = consolidated_daily(day)
    if cd is not None:
        print(f"  EQUS.SUMMARY consolidated FULL-day volume {int(cd.volume):>12,}"
              f"  -> FMP RTH is {100 * j.volume_fmp.sum() / cd.volume:.1f}% of it")

    out = (Path(args.out) if args.out else
           ROOT / "exports" / f"TSLA_{args.interval}_{day}_fmp_vs_databento.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    j.to_csv(out, index_label="datetime_et")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
