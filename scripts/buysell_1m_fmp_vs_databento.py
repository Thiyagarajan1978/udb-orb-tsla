#!/usr/bin/env python
"""1-minute price / volume / buy-volume / sell-volume: FMP vs Databento.

  python scripts/buysell_1m_fmp_vs_databento.py 2026-07-29

IMPORTANT — what each provider can actually give you:
  * FMP publishes NO buy/sell split at any interval. Its intraday chart API returns
    OHLCV only, so the `*_fmp` buy/sell columns are empty by necessity, not by omission.
  * Databento has no buy/sell field either — it is DERIVED here from `tbbo` (every trade
    plus the BBO in force at that trade) using the standard Lee-Ready quote rule:
        price > mid -> buy-initiated, price < mid -> sell-initiated,
        price == mid -> tick rule (vs the previous differing trade price).
    The raw `side` field is NOT used: on Nasdaq ITCH ~32% of trades carry side='N'
    (non-displayed liquidity), which would silently drop a third of the volume. The
    script cross-tabs the quote rule against `side` so the convention is verifiable.
  * Databento equity data here is XNAS.ITCH = Nasdaq only, ~18% of TSLA's consolidated
    tape. So `volume_db` is NOT comparable in level to FMP's consolidated `volume_fmp`;
    the buy/sell SPLIT (a ratio) is the meaningful figure, not the share counts.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytz  # noqa: E402

ET = pytz.timezone("America/New_York")


def classify_trades(day: date, dataset: str) -> pd.DataFrame:
    """Every trade of the session, tagged buy/sell-initiated by the quote rule."""
    import databento as db
    from forward_test import get_db_key

    d = db.Historical(get_db_key()).timeseries.get_range(
        dataset=dataset, symbols=["TSLA"], stype_in="raw_symbol", schema="tbbo",
        start=f"{day}T13:30", end=f"{day}T20:00").to_df()
    # index on ts_event (exchange time) so bars line up with the OHLCV schema, and note
    # that trade timestamps repeat — all work below is positional, never index-aligned.
    idx = pd.DatetimeIndex(pd.to_datetime(d["ts_event"], utc=True)).tz_convert(ET)
    d = d.set_index(idx).sort_index()

    px = d.price.to_numpy(float)
    mid = ((d["bid_px_00"].to_numpy(float) + d["ask_px_00"].to_numpy(float)) / 2.0)
    lab = np.where(px > mid, "buy", np.where(px < mid, "sell", "mid"))

    # tick rule for at-the-mid prints: compare with the last differing trade price
    at_mid = lab == "mid"
    if at_mid.any():
        s = pd.Series(px)
        prev = s.where(s.diff() != 0).ffill().shift(1).to_numpy(float)
        tick = np.where(px > prev, "buy", np.where(px < prev, "sell", "unclassified"))
        lab = np.where(at_mid, tick, lab)

    return pd.DataFrame({"price": px, "size": d["size"].to_numpy(),
                         "label": lab, "side": d.side.to_numpy(), "mid": mid},
                        index=d.index)


def main(argv=None):
    ap = argparse.ArgumentParser(description="1m price/volume/buy/sell, FMP vs Databento")
    ap.add_argument("day")
    ap.add_argument("--dataset", default="XNAS.ITCH")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    from scripts.compare_fmp_databento import fmp_bars

    day = date.fromisoformat(args.day)
    tr = classify_trades(day, args.dataset)

    print(f"=== {day}: {len(tr):,} {args.dataset} trades classified (quote rule) ===")
    print(tr.label.value_counts().to_string())
    print("\n-- quote rule vs the raw `side` field (establishes its convention) --")
    print(pd.crosstab(pd.Index(tr.side.to_numpy(), name="side"),
                      pd.Index(tr.label.to_numpy(), name="quote rule")))
    unc = (tr.label == "unclassified").sum()
    print(f"\nunclassified: {unc} trades ({100 * tr['size'][tr.label == 'unclassified'].sum() / tr['size'].sum():.2f}% of volume)")

    g = tr.groupby([pd.Grouper(freq="1min"), "label"])["size"].sum().unstack(fill_value=0)
    for c in ("buy", "sell", "unclassified"):
        if c not in g:
            g[c] = 0
    db1 = pd.DataFrame({
        "open_db": tr.price.resample("1min").first(),
        "high_db": tr.price.resample("1min").max(),
        "low_db": tr.price.resample("1min").min(),
        "close_db": tr.price.resample("1min").last(),
        "volume_db": tr["size"].resample("1min").sum(),
        "buy_volume_db": g["buy"], "sell_volume_db": g["sell"],
        "unclassified_volume_db": g["unclassified"],
    }).dropna(subset=["close_db"])

    fmp = fmp_bars(day, "1min")
    # intersection can drop the tz; force ET so the CSV matches the other export files
    idx = pd.DatetimeIndex(fmp.index.intersection(db1.index)).tz_convert(ET)
    fmp, db1 = fmp.loc[idx], db1.loc[idx]

    j = pd.DataFrame(index=idx)
    for c in ("open", "high", "low", "close", "volume"):
        j[f"{c}_fmp"] = fmp[c].values
    j["buy_volume_fmp"] = np.nan       # FMP does not publish an aggressor split
    j["sell_volume_fmp"] = np.nan
    for c in db1.columns:
        j[c] = db1[c].values
    j["buy_pct_db"] = (100 * j.buy_volume_db / (j.buy_volume_db + j.sell_volume_db)).round(2)
    j["d_close"] = (j.close_fmp - j.close_db).round(4)
    j["vol_ratio_fmp_db"] = (j.volume_fmp / j.volume_db).round(3)

    tot_b, tot_s = j.buy_volume_db.sum(), j.sell_volume_db.sum()
    print(f"\n-- session ({len(j)} 1m bars) --")
    print(f"  FMP volume (consolidated)  {int(j.volume_fmp.sum()):>12,}   buy/sell split: NOT PUBLISHED")
    print(f"  {args.dataset} volume         {int(j.volume_db.sum()):>12,}"
          f"   buy {int(tot_b):>11,} ({100 * tot_b / (tot_b + tot_s):.1f}%)"
          f"  sell {int(tot_s):>11,} ({100 * tot_s / (tot_b + tot_s):.1f}%)")
    print(f"  close price agreement: mean abs ${j.d_close.abs().mean():.4f}, max ${j.d_close.abs().max():.3f}")

    out = Path(args.out) if args.out else ROOT / "exports" / f"TSLA_1min_{day}_buysell_fmp_vs_databento.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    j.to_csv(out, index_label="datetime_et")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
