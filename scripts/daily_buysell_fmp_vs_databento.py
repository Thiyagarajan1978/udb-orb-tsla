#!/usr/bin/env python
"""Daily price / volume / buy-volume / sell-volume: FMP vs Databento, last N sessions.

  python scripts/daily_buysell_fmp_vs_databento.py --days 5

Sources and their scope — these are NOT interchangeable, hence the explicit column names:
  * FMP `/stable/historical-price-eod/full` — consolidated OHLCV. No buy/sell split exists
    at FMP, at any interval, so `*_fmp` buy/sell columns are empty by necessity.
  * Databento EQUS.SUMMARY `ohlcv-1d` — official consolidated (all-venue) daily bar. This
    is the like-for-like comparator for the FMP daily line.
  * Databento XNAS.ITCH `tbbo` — Nasdaq-only RTH trades, each tagged buy/sell-initiated by
    the Lee-Ready quote rule (see buysell_1m_fmp_vs_databento.py). Nasdaq is ~18% of TSLA's
    tape, so buy+sell+unclassified reconciles to `volume_nasdaq_rth_db`, NOT to either
    consolidated volume column. Read the SPLIT (buy_pct_db), not the share counts.

Databento equities are T+1 on the historical API, so the most recent session available is
yesterday's; today is skipped rather than emitted with blank Databento columns.

Cost: ~$0.07 per session (tbbo) + a fraction of a cent for the daily bars.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402


def fmp_daily(start: date, end: date) -> pd.DataFrame:
    import requests
    from udb_orb.config import get_fmp_key
    url = (f"https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol=TSLA&from={start}&to={end}&apikey={get_fmp_key()}")
    data = requests.get(url, timeout=30).json()
    if isinstance(data, dict):
        raise RuntimeError(f"FMP error: {str(data)[:200]}")
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.set_index("date").sort_index()


def databento_daily(start: date, end: date) -> pd.DataFrame:
    """Consolidated all-venue daily bars."""
    import databento as db
    from forward_test import get_db_key
    d = db.Historical(get_db_key()).timeseries.get_range(
        dataset="EQUS.SUMMARY", symbols=["TSLA"], stype_in="raw_symbol", schema="ohlcv-1d",
        start=str(start), end=str(end + timedelta(days=1))).to_df()
    d.index = pd.DatetimeIndex(pd.to_datetime(d.index, utc=True)).date
    return d[["open", "high", "low", "close", "volume"]].sort_index()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily FMP vs Databento with buy/sell split")
    ap.add_argument("--days", type=int, default=5, help="number of sessions (default 5)")
    ap.add_argument("--end", default=None, help="last session (default: newest Databento has)")
    ap.add_argument("--dataset", default="XNAS.ITCH")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    from scripts.buysell_1m_fmp_vs_databento import classify_trades

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days * 2 + 7)          # slack for weekends/holidays
    con = databento_daily(start, end)
    sessions = list(con.index)[-args.days:]
    print(f"sessions: {sessions[0]} .. {sessions[-1]} ({len(sessions)})")
    if date.today() not in con.index:
        print(f"NOTE: {date.today()} excluded - Databento equities are T+1, no data yet.")

    fmp = fmp_daily(sessions[0], sessions[-1])
    rows = []
    for d in sessions:
        tr = classify_trades(d, args.dataset)
        vol = tr.groupby(tr.label.to_numpy())["size"].sum()
        f = fmp.loc[d] if d in fmp.index else None
        rows.append({
            "date": d,
            "open_fmp": f.open if f is not None else None,
            "high_fmp": f.high if f is not None else None,
            "low_fmp": f.low if f is not None else None,
            "close_fmp": f.close if f is not None else None,
            "volume_fmp": f.volume if f is not None else None,
            "buy_volume_fmp": None, "sell_volume_fmp": None,   # FMP publishes no split
            "open_db": con.loc[d, "open"], "high_db": con.loc[d, "high"],
            "low_db": con.loc[d, "low"], "close_db": con.loc[d, "close"],
            "volume_db": con.loc[d, "volume"],
            "volume_nasdaq_rth_db": int(tr["size"].sum()),
            "buy_volume_db": int(vol.get("buy", 0)),
            "sell_volume_db": int(vol.get("sell", 0)),
            "unclassified_volume_db": int(vol.get("unclassified", 0)),
        })
        print(f"  {d} done ({len(tr):,} trades)")

    j = pd.DataFrame(rows).set_index("date")
    j["buy_pct_db"] = (100 * j.buy_volume_db / (j.buy_volume_db + j.sell_volume_db)).round(2)
    j["d_close"] = (j.close_fmp - j.close_db).round(4)
    j["d_volume"] = (j.volume_fmp - j.volume_db).astype("Int64")

    pd.set_option("display.width", 250)
    print("\n=== price / volume ===")
    print(j[["close_fmp", "close_db", "d_close", "volume_fmp", "volume_db", "d_volume"]].to_string())
    print("\n=== buy/sell (Nasdaq RTH, quote rule) ===")
    print(j[["volume_nasdaq_rth_db", "buy_volume_db", "sell_volume_db",
             "unclassified_volume_db", "buy_pct_db"]].to_string())

    out = Path(args.out) if args.out else ROOT / "exports" / f"TSLA_1day_{sessions[0]}_{sessions[-1]}_buysell_fmp_vs_databento.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    j.to_csv(out, index_label="date")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
