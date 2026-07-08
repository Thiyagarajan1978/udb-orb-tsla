"""FMP (Financial Modeling Prep) 5-minute data loader — the sole provider.

Uses the FMP **stable** API (legacy /api/v3 is disabled post-Aug-2025):
  - 5-minute intraday: /stable/historical-chart/5min

FMP intraday `date` strings are naive wall-clock US/Eastern; we localize (not convert).
Paged in ~5-day chunks under the ~450-row cap; error payloads arrive as a dict; fetched
5m is cached to `data/cache/`. Bars are filtered to regular trading hours [09:30, 16:00).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..config import get_fmp_key

_BASE = "https://financialmodelingprep.com/stable"


def _parse_chart(data, tz: str = "America/New_York") -> pd.DataFrame | None:
    if not data:
        return None
    df = pd.DataFrame(data)
    if df.empty or "date" not in df.columns:
        return None
    df = df.rename(columns=str.lower)
    idx = pd.to_datetime(df["date"]).dt.tz_localize(tz, nonexistent="shift_forward", ambiguous=False)
    df = df.set_index(idx)
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[cols].astype(float).sort_index()


def fetch_5min(symbol: str, start: date, end: date, *, key: str | None = None,
               cache_dir: Path | None = None, use_cache: bool = True,
               chunk_days: int = 5, tz: str = "America/New_York") -> pd.DataFrame:
    """Paged 5-minute history for [start, end], deduped and sorted (tz-aware ET index)."""
    key = key or get_fmp_key()
    if not key:
        raise RuntimeError("FMP_API_KEY not found (env or .env)")

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / f"{symbol}_5min_{start}_{end}.parquet"
        if use_cache and cache.exists():
            return pd.read_parquet(cache)
    else:
        cache = None

    import requests

    base = f"{_BASE}/historical-chart/5min?symbol={symbol}&apikey={key}"
    frames = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        url = f"{base}&from={cur.isoformat()}&to={nxt.isoformat()}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            raise RuntimeError(f"FMP error for {symbol} 5min: {str(data)[:200]}")
        parsed = _parse_chart(data, tz)
        if parsed is not None and not parsed.empty:
            frames.append(parsed)
        cur = nxt + timedelta(days=1)

    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if cache is not None and use_cache:
        out.to_parquet(cache)
    return out


def rth_only(df: pd.DataFrame, rth_start=(9, 30), rth_end=(16, 0)) -> pd.DataFrame:
    """Keep only regular-session bars [09:30, 16:00). Index must be tz-aware ET."""
    if df.empty:
        return df
    from datetime import time as _time
    rs, re_ = _time(*rth_start), _time(*rth_end)
    t = df.index.time
    return df[(t >= rs) & (t < re_)]
