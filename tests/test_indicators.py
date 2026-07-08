from conftest import build_bars

from udb_orb.engine import indicators


def test_session_vwap_cumulative_equal_volume():
    bars = build_bars([
        (9, 30, 100, 101, 99, 100, 1000),   # hlc3 = 100
        (9, 35, 100, 102, 100, 101, 1000),  # hlc3 = 101
        (9, 40, 101, 104, 101, 103, 1000),  # hlc3 = 102.667
    ])
    vw = indicators.session_vwap(bars)
    # equal volume -> running mean of hlc3
    assert abs(vw.iloc[0] - 100.0) < 1e-9
    assert abs(vw.iloc[1] - 100.5) < 1e-9
    assert abs(vw.iloc[2] - (100 + 101 + 102.6666667) / 3) < 1e-6


def test_vwap_resets_per_day():
    d1 = build_bars([(9, 30, 100, 100, 100, 100, 1000)], day="2024-06-03")
    d2 = build_bars([(9, 30, 200, 200, 200, 200, 1000)], day="2024-06-04")
    import pandas as pd
    bars = pd.concat([d1, d2])
    vw = indicators.session_vwap(bars)
    assert abs(vw.iloc[0] - 100.0) < 1e-9
    assert abs(vw.iloc[1] - 200.0) < 1e-9   # reset, not blended


def test_relative_volume_trailing():
    bars = build_bars([
        (9, 30, 100, 101, 99, 100, 1000),
        (9, 35, 100, 102, 100, 101, 1000),
        (9, 40, 101, 104, 101, 103, 3000),  # 3x the trailing avg
    ])
    rv = indicators.relative_volume(bars, lookback=20)
    assert rv.iloc[2] == 3.0   # 3000 / mean(1000,1000)
