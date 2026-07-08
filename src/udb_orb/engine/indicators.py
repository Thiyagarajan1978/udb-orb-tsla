"""Pure indicator helpers used by the engine and the UI.

All functions are network-free and operate on a tz-aware (ET) OHLCV DataFrame whose
index timestamps are bar-START times. The 5-minute bar at 09:30 is the opening-range bar.
"""
from __future__ import annotations

import pandas as pd


def hlc3(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP, reset each calendar day, **including the current bar**.

    Matches Pine's `ta.vwap(hlc3, newDay)`: cumulative sum(hlc3*vol)/sum(vol) from the
    day's first (09:30) bar through the current bar.
    """
    price = hlc3(df)
    vol = df["volume"].astype(float)
    day = pd.Series(df.index.date, index=df.index)
    pv = (price * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum()
    out = pv / cv.where(cv > 0)
    return out


def trailing_avg_volume(df: pd.DataFrame, lookback: int) -> pd.Series:
    """Mean volume of the prior `lookback` bars (excludes the current bar).

    Used for the RVOL breakout filter. NaN at the very start of the series.
    """
    return df["volume"].astype(float).shift(1).rolling(lookback, min_periods=1).mean()


def relative_volume(df: pd.DataFrame, lookback: int) -> pd.Series:
    """current bar volume / trailing average volume (NaN-safe: NaN where avg is NaN/0)."""
    avg = trailing_avg_volume(df, lookback)
    return df["volume"].astype(float) / avg.where(avg > 0)


def opening_range(day_df: pd.DataFrame, market_open) -> tuple[float, float] | None:
    """(high, low) of the opening-range bar for a single day's RTH DataFrame."""
    mask = [t == market_open for t in day_df.index.time]
    bar = day_df[mask]
    if bar.empty:
        return None
    return float(bar["high"].iloc[0]), float(bar["low"].iloc[0])
