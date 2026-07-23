"""TRADE TASTIC ATR-trail (atr_trail_exit runner enhancement) — indicator unit tests."""
from conftest import build_bars

from udb_orb.engine import indicators


def _bars_from_closes(closes):
    rows = []
    minutes = 0
    for c in closes:
        hh = 9 + (30 + minutes) // 60
        mm = (30 + minutes) % 60
        rows.append((hh, mm, c, c + 0.5, c - 0.5, c, 1000))
        minutes += 5
    return build_bars(rows)


def test_trade_tastic_init_pins_to_close():
    closes = [100.0 + i for i in range(20)]
    bars = _bars_from_closes(closes)
    ts_long, ts_short = indicators.trade_tastic_trail(bars)
    for i in range(16):
        assert ts_long.iloc[i] == closes[i]
        assert ts_short.iloc[i] == closes[i]


def test_trade_tastic_long_line_rises_below_price_and_holds_on_stall():
    # steady +1/bar uptrend, then two stalled bars (close not > prev close)
    closes = [100.0 + i for i in range(28)] + [127.0, 127.0]
    bars = _bars_from_closes(closes)
    ts_long, _ = indicators.trade_tastic_trail(bars)
    # trending: the line ratchets up each bar and stays below the close
    for i in range(20, 28):
        assert ts_long.iloc[i] > ts_long.iloc[i - 1]
        assert ts_long.iloc[i] < closes[i]
    # stalled bars fail the close>prev condition -> the line HOLDS
    assert ts_long.iloc[28] == ts_long.iloc[27]
    assert ts_long.iloc[29] == ts_long.iloc[28]


def test_trade_tastic_short_line_mirrors():
    closes = [200.0 - i for i in range(28)] + [173.0, 173.0]
    bars = _bars_from_closes(closes)
    _, ts_short = indicators.trade_tastic_trail(bars)
    for i in range(20, 28):
        assert ts_short.iloc[i] < ts_short.iloc[i - 1]
        assert ts_short.iloc[i] > closes[i]
    assert ts_short.iloc[28] == ts_short.iloc[27]
    assert ts_short.iloc[29] == ts_short.iloc[28]
