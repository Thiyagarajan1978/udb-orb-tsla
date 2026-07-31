#!/usr/bin/env python
"""Build the 09:30-09:31 ET opening-minute history for TSLA: OHLC, volume, buy/sell volume.

  python scripts/first_minute_history.py --start 2023-07-31 --end 2026-07-29
  python scripts/first_minute_history.py --start ... --end ... --workers 8   # resumes

The first RTH minute is the leading edge of the opening range, so this is the raw material
for asking whether opening-minute order flow says anything about the day's ORB trade.

Sources
  * Databento XNAS.ITCH `tbbo` (Nasdaq only, ~17-19% of the tape) — trades plus the BBO in
    force at each trade, classified buy/sell-initiated by the Lee-Ready quote rule. This is
    the ONLY buy/sell source: FMP publishes no aggressor split at any interval.
  * FMP `/historical-chart/1min` — the 09:30 bar, for a consolidated price cross-check.
    Its VOLUME is included but is known-unreliable intraday (see docs/DATA_PROVIDERS.md);
    use `volume_db` semantics knowingly.

CAVEAT that matters for this particular minute: 09:30-09:31 contains the opening cross.
Auction prints have no meaningful aggressor side, so `buy_pct_db` for this bar is polluted
by the cross. `largest_print_db` is included so cross-dominated sessions are identifiable.

Resumable: each session is cached under data/cache/first_minute/ and skipped on re-run.
Cost ~$0.0032/session of Databento (~$2.40 for 3 years).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytz  # noqa: E402

ET = pytz.timezone("America/New_York")
CACHE = ROOT / "data" / "cache" / "first_minute"
_local = threading.local()


def _client():
    """One Databento client per worker thread."""
    if not hasattr(_local, "c"):
        import databento as db
        from forward_test import get_db_key
        _local.c = db.Historical(get_db_key())
    return _local.c


def window_utc(day: date) -> tuple[str, str]:
    """09:30-09:31 ET for this date, in UTC. Localize per-date so DST is handled."""
    a = ET.localize(datetime(day.year, day.month, day.day, 9, 30))
    b = a + timedelta(minutes=1)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return a.astimezone(pytz.UTC).strftime(fmt), b.astimezone(pytz.UTC).strftime(fmt)


def sessions(start: date, end: date) -> list[date]:
    """Actual trading days, from the venue's own daily bars.

    ohlcv-1d is stamped at 00:00 UTC ON the trading date — converting it to ET would roll
    every session back to the previous calendar day, so take the UTC date as-is.
    """
    import databento as db
    from forward_test import get_db_key
    d = db.Historical(get_db_key()).timeseries.get_range(
        dataset="XNAS.ITCH", symbols=["TSLA"], stype_in="raw_symbol", schema="ohlcv-1d",
        start=str(start), end=str(end + timedelta(days=1))).to_df()
    return sorted({pd.Timestamp(t).date() for t in pd.to_datetime(d.index, utc=True)})


def databento_minute(day: date) -> dict:
    """Classified trades for the opening minute (quote rule, tick-rule fallback)."""
    s, e = window_utc(day)
    d = _client().timeseries.get_range(
        dataset="XNAS.ITCH", symbols=["TSLA"], stype_in="raw_symbol", schema="tbbo",
        start=s, end=e).to_df()
    if len(d) == 0:
        return {}
    d = d.sort_values("ts_event")
    px = d.price.to_numpy(float)
    sz = d["size"].to_numpy(float)
    mid = (d["bid_px_00"].to_numpy(float) + d["ask_px_00"].to_numpy(float)) / 2.0

    lab = np.where(px > mid, "buy", np.where(px < mid, "sell", "mid"))
    at_mid = lab == "mid"
    if at_mid.any():
        ps = pd.Series(px)
        prev = ps.where(ps.diff() != 0).ffill().shift(1).to_numpy(float)
        lab = np.where(at_mid, np.where(px > prev, "buy",
                       np.where(px < prev, "sell", "unclassified")), lab)

    # The opening cross prints as one huge non-displayed trade in the first seconds. It has
    # no aggressor — both sides are matched by the auction — but the quote rule happily
    # labels it, and it can be 60-80% of the minute, so it must be isolated rather than
    # left to dominate buy_pct. Identify it as the largest print in the first 10 seconds
    # that is at least 5% of the minute's volume. The cross usually lands inside 1s, but
    # 2025-05-02..09 ran 2.2-2.4s late, so the window has slack; it costs nothing because
    # the cross is also the largest print of the whole minute on every session here.
    t = pd.DatetimeIndex(pd.to_datetime(d["ts_event"], utc=True)).to_numpy()
    early = t <= (t[0] + np.timedelta64(10, "s"))
    cross = np.zeros(len(d), bool)
    cand = np.where(early & (sz >= 0.05 * sz.sum()))[0]
    if len(cand):
        cross[cand[np.argmax(sz[cand])]] = True

    keep = ~cross
    row = {
        "open_db": px[0], "high_db": px.max(), "low_db": px.min(), "close_db": px[-1],
        "volume_db": int(sz.sum()),
        "buy_volume_db": int(sz[lab == "buy"].sum()),
        "sell_volume_db": int(sz[lab == "sell"].sum()),
        "unclassified_volume_db": int(sz[lab == "unclassified"].sum()),
        "trades_db": int(len(d)),
        "vwap_db": round(float((px * sz).sum() / sz.sum()), 4),
        "largest_print_db": int(sz.max()),
        "cross_size_db": int(sz[cross].sum()),
        "cross_price_db": float(px[cross][0]) if cross.any() else None,
        "volume_ex_cross_db": int(sz[keep].sum()),
        "buy_volume_ex_cross_db": int(sz[keep & (lab == "buy")].sum()),
        "sell_volume_ex_cross_db": int(sz[keep & (lab == "sell")].sum()),
    }
    return row


def fmp_minute(day: date) -> dict:
    """FMP's own 09:30 one-minute bar (consolidated price cross-check)."""
    import requests
    from udb_orb.config import get_fmp_key
    url = (f"https://financialmodelingprep.com/stable/historical-chart/1min"
           f"?symbol=TSLA&from={day}&to={day}&apikey={get_fmp_key()}")
    data = requests.get(url, timeout=30).json()
    if not isinstance(data, list) or not data:
        return {}
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    row = df[df["date"].dt.strftime("%H:%M") == "09:30"]
    if row.empty:
        return {}
    r = row.iloc[0]
    return {"open_fmp": float(r.open), "high_fmp": float(r.high), "low_fmp": float(r.low),
            "close_fmp": float(r.close), "volume_fmp": int(r.volume)}


def one_session(day: date) -> dict:
    f = CACHE / f"{day}.json"
    if f.exists():
        return json.loads(f.read_text())
    row = {"date": str(day)}
    try:
        row.update(databento_minute(day))
    except Exception as e:
        row["error_db"] = str(e)[:150]
    try:
        row.update(fmp_minute(day))
    except Exception as e:
        row["error_fmp"] = str(e)[:150]
    f.write_text(json.dumps(row))
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description="TSLA 09:30-09:31 history with buy/sell volume")
    ap.add_argument("--start", default=None, help="default: 3 years before --end")
    ap.add_argument("--end", default=None, help="default: yesterday (Databento is T+1)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=365 * 3)
    CACHE.mkdir(parents=True, exist_ok=True)

    days = sessions(start, end)
    todo = [d for d in days if not (CACHE / f"{d}.json").exists()]
    print(f"{len(days)} sessions {days[0]}..{days[-1]}   cached {len(days) - len(todo)}   to fetch {len(todo)}")
    print(f"estimated Databento cost ~${0.0032 * len(todo):.2f}", flush=True)

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one_session, d): d for d in todo}
        for fut in as_completed(futs):
            done += 1
            if done % 25 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)}", flush=True)

    rows = [json.loads((CACHE / f"{d}.json").read_text()) for d in days]
    df = pd.DataFrame(rows).set_index("date").sort_index()
    df["weekday"] = pd.to_datetime(df.index).day_name().str[:3]
    df["buy_pct_db"] = (100 * df.buy_volume_db /
                        (df.buy_volume_db + df.sell_volume_db)).round(2)
    # the headline split: auction volume removed, so it reflects continuous-trade aggression
    df["buy_pct_ex_cross_db"] = (100 * df.buy_volume_ex_cross_db /
                                 (df.buy_volume_ex_cross_db + df.sell_volume_ex_cross_db)).round(2)
    df["net_volume_ex_cross_db"] = df.buy_volume_ex_cross_db - df.sell_volume_ex_cross_db
    df["cross_pct_of_bar_db"] = (100 * df.cross_size_db / df.volume_db).round(2)
    df["range_db"] = (df.high_db - df.low_db).round(4)
    df["d_close"] = (df.close_fmp - df.close_db).round(4)
    cols = ["weekday",
            "open_fmp", "high_fmp", "low_fmp", "close_fmp", "volume_fmp",
            "open_db", "high_db", "low_db", "close_db", "range_db", "vwap_db", "d_close",
            "volume_db", "buy_volume_db", "sell_volume_db", "unclassified_volume_db",
            "buy_pct_db",
            "cross_price_db", "cross_size_db", "cross_pct_of_bar_db",
            "volume_ex_cross_db", "buy_volume_ex_cross_db", "sell_volume_ex_cross_db",
            "buy_pct_ex_cross_db", "net_volume_ex_cross_db",
            "trades_db", "largest_print_db"]
    df = df[[c for c in cols if c in df.columns] +
            [c for c in df.columns if c.startswith("error")]]

    out = Path(args.out) if args.out else ROOT / "data" / "reference" / f"tsla_first_minute_{days[0]}_{days[-1]}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index_label="date")
    print(f"\nwrote {out}  ({len(df)} sessions)")
    bad = df.filter(like="error").notna().any(axis=1).sum() if df.filter(like="error").shape[1] else 0
    print(f"sessions with a fetch error: {bad}")


if __name__ == "__main__":
    main()
