"""Export TSLA 5-minute RTH candles as monthly CSV feed files.

Range: 2026-01-01 -> today (last completed session). Source: FMP stable
/historical-chart/5min via the project's fmp_client (naive ET -> localized).

Writes one CSV per month plus a combined file to exports/tsla_5m_monthly_2026/.
Columns: datetime,date,time,open,high,low,close,volume  (datetime is ET, tz-aware)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from udb_orb.data.fmp_client import fetch_5min, rth_only  # noqa: E402

START = date(2026, 1, 1)
END = date.today()
OUT = ROOT / "exports" / "tsla_5m_monthly_2026"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = fetch_5min("TSLA", START, END, cache_dir=ROOT / "data" / "cache", use_cache=False)
    df = rth_only(df)

    out = df.copy()
    out.insert(0, "datetime", out.index.strftime("%Y-%m-%d %H:%M:%S%z"))
    out.insert(1, "date", out.index.strftime("%Y-%m-%d"))
    out.insert(2, "time", out.index.strftime("%H:%M"))
    out["volume"] = out["volume"].astype("int64")

    rows = []
    for period, g in out.groupby(out.index.to_period("M")):
        path = OUT / f"TSLA_5m_{period}.csv"
        g.to_csv(path, index=False)
        rows.append((str(period), len(g), g["date"].nunique(),
                     g["date"].iloc[0], g["date"].iloc[-1], path.name))

    combined = OUT / "TSLA_5m_2026_ALL.csv"
    out.to_csv(combined, index=False)

    print(f"{'month':8} {'bars':>6} {'days':>5}  {'first':10} {'last':10}  file")
    for m, n, d, f, l, name in rows:
        print(f"{m:8} {n:6d} {d:5d}  {f:10} {l:10}  {name}")
    print(f"\nTOTAL    {len(out):6d} {out['date'].nunique():5d}  "
          f"{out['date'].iloc[0]} {out['date'].iloc[-1]}  {combined.name}")
    print(f"\nOutput dir: {OUT}")


if __name__ == "__main__":
    main()
