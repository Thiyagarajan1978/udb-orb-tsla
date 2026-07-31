#!/usr/bin/env python
"""Build the 09:31:00 executable entry price for every session (E1's entry).

  python scripts/entry_0931.py --start 2023-08-01 --end 2026-07-29

E1 enters the instant the 09:30-09:31 order-flow signal is complete. The FMP 1-minute bar
labelled 09:31 covers 09:31:00-09:31:59, so its OPEN is the first trade at/after 09:31:00 —
the price you could actually get. The 09:30 bar's high/low is emitted alongside it: that
candle is the only range in existence at 09:31, so it is what E1 must size its stop from.

Reuses data/cache/TSLA_1min_*.parquet where it covers the date and only calls FMP for the
gap, then caches the result so this is a one-time cost.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

OUT = ROOT / "data" / "reference" / "tsla_0931_entry.csv"


def from_cache(start: date, end: date) -> pd.DataFrame:
    rows = []
    for f in sorted((ROOT / "data" / "cache").glob("TSLA_1min_*.parquet")):
        d = pd.read_parquet(f)
        for day, g in d.groupby(d.index.date):
            if not (start <= day <= end):
                continue
            b30 = g[g.index.strftime("%H:%M") == "09:30"]
            b31 = g[g.index.strftime("%H:%M") == "09:31"]
            if b30.empty or b31.empty:
                continue
            rows.append({"date": str(day), "px_0931": float(b31.iloc[0].open),
                         "hi_0930": float(b30.iloc[0].high), "lo_0930": float(b30.iloc[0].low),
                         "src": "cache"})
    return pd.DataFrame(rows)


def from_fmp(day: str) -> dict | None:
    import requests
    from udb_orb.config import get_fmp_key
    url = (f"https://financialmodelingprep.com/stable/historical-chart/1min"
           f"?symbol=TSLA&from={day}&to={day}&apikey={get_fmp_key()}")
    try:
        data = requests.get(url, timeout=30).json()
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    hhmm = df["date"].dt.strftime("%H:%M")
    b30, b31 = df[hhmm == "09:30"], df[hhmm == "09:31"]
    if b30.empty or b31.empty:
        return None
    return {"date": day, "px_0931": float(b31.iloc[0].open),
            "hi_0930": float(b30.iloc[0].high), "lo_0930": float(b30.iloc[0].low), "src": "fmp"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="09:31:00 entry price per session")
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2026-07-29")
    ap.add_argument("--sessions", default=str(ROOT / "data" / "reference" /
                                              "tsla_first_minute_2023-07-31_2026-07-29.csv"))
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    want = pd.read_csv(args.sessions, parse_dates=["date"])
    want = want[(want.date.dt.date >= start) & (want.date.dt.date <= end)]
    days = [d.strftime("%Y-%m-%d") for d in want.date]

    have = pd.DataFrame()
    if OUT.exists():
        have = pd.read_csv(OUT)
    cached = from_cache(start, end)
    have = pd.concat([have, cached]).drop_duplicates(subset="date", keep="first")
    todo = sorted(set(days) - set(have.get("date", pd.Series(dtype=str))))
    print(f"{len(days)} sessions   have {len(have)}   fetching {len(todo)} from FMP")

    rows = []
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(from_fmp, d) for d in todo]
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                if r:
                    rows.append(r)
                if i % 50 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}", flush=True)

    out = pd.concat([have, pd.DataFrame(rows)]).drop_duplicates(subset="date", keep="first")
    out = out[out.date.isin(days)].sort_values("date")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    miss = sorted(set(days) - set(out.date))
    print(f"\nwrote {OUT}  ({len(out)}/{len(days)} sessions)  by source: "
          f"{out.src.value_counts().to_dict()}")
    if miss:
        print(f"missing {len(miss)}: {miss[:10]}")


if __name__ == "__main__":
    main()
