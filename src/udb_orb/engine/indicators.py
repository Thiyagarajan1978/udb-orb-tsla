"""Pure indicator helpers used by the engine and the UI.

All functions are network-free and operate on a tz-aware (ET) OHLCV DataFrame whose
index timestamps are bar-START times. The 5-minute bar at 09:30 is the opening-range bar.
"""
from __future__ import annotations

import numpy as np
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


def trade_tastic_trail(df: pd.DataFrame, atr_period: int = 5, hhv_period: int = 10,
                       mult: float = 2.5, init_bars: int = 16) -> pd.Series:
    """'TRADE TASTIC' trail line (ceyhun, TradingView) — ONE line for BOTH directions.

    TS = HHV(high - mult*ATR(atr_period), hhv_period), but it only MOVES on a bar that
    closes above the HHV AND extends (close > prev close) — otherwise it holds. It is not
    a strict ratchet: after a deep fall the HHV drags an accepted level DOWN, which is how
    the line comes back under price to catch the short-cover flip. Color = close > TS.
    Exit semantics (the indicator's color flips, state-based): a LONG exits when the bar
    CLOSES <= TS (green->red); a SHORT exits when the bar CLOSES > TS (red->green).
    FIXED 2026-07-23: the first port gave shorts a mirrored LLV(low + mult*ATR) line —
    the original script has NO short line; on 2026-07-23 the mirror exited the short at
    11:55 where the true flip was 10:35 (user-verified against the TV original).
    The first init_bars bars pin the line to the close (the Pine warm-up). ATR is
    Wilder/RMA to match Pine `ta.atr`. Computed on the series as-is — RTH-continuous
    across days, like the TV RTH chart.
    """
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr, index=df.index).ewm(alpha=1.0 / atr_period, adjust=False).mean().to_numpy()
    hhv = pd.Series(h - mult * atr, index=df.index).rolling(hhv_period, min_periods=1).max().to_numpy()
    ts = np.empty(len(c))
    for i in range(len(c)):
        if i < init_bars:
            ts[i] = c[i]
        else:
            ts[i] = hhv[i] if (c[i] > hhv[i] and c[i] > c[i - 1]) else ts[i - 1]
    return pd.Series(ts, index=df.index)


def opening_range(day_df: pd.DataFrame, market_open) -> tuple[float, float] | None:
    """(high, low) of the opening-range bar for a single day's RTH DataFrame."""
    mask = [t == market_open for t in day_df.index.time]
    bar = day_df[mask]
    if bar.empty:
        return None
    return float(bar["high"].iloc[0]), float(bar["low"].iloc[0])
