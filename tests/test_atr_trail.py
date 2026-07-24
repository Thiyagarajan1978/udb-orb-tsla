"""TRADE TASTIC ATR-trail (atr_trail_exit runner enhancement) — indicator unit tests.

The script has ONE line for both directions: TS = HHV(high - mult*ATR, hhv), moving only
on a closing, extending bar. Long exits close <= TS (green->red flip); short exits
close > TS (red->green flip). The 2026-07-23 fix removed an invented mirrored short line.
"""
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
    ts = indicators.trade_tastic_trail(bars)
    for i in range(16):
        assert ts.iloc[i] == closes[i]


def test_trade_tastic_line_rises_below_price_and_holds_on_stall():
    # steady +1/bar uptrend, then two stalled bars (close not > prev close)
    closes = [100.0 + i for i in range(28)] + [127.0, 127.0]
    bars = _bars_from_closes(closes)
    ts = indicators.trade_tastic_trail(bars)
    # trending: the line ratchets up each bar and stays below the close (color green)
    for i in range(20, 28):
        assert ts.iloc[i] > ts.iloc[i - 1]
        assert ts.iloc[i] < closes[i]
    # stalled bars fail the close>prev condition -> the line HOLDS
    assert ts.iloc[28] == ts.iloc[27]
    assert ts.iloc[29] == ts.iloc[28]


def test_trade_tastic_short_exit_is_the_red_to_green_flip_of_the_same_line():
    # uptrend (line accepted below price), then a waterfall long enough for the 10-bar HHV
    # to roll fully into fallen values, then a bounce bar.
    up = [100.0 + i for i in range(20)]           # line ends ~just below 119
    down = [115.0 - 3 * i for i in range(15)]     # 115 .. 73: color red, line must HOLD
    closes = up + down + [92.0]                   # bounce: extends up AND clears the fallen HHV
    bars = _bars_from_closes(closes)
    ts = indicators.trade_tastic_trail(bars)
    n_up = len(up)
    # during the fall no bar closes above the HHV -> the line holds its last accepted value
    for i in range(n_up, n_up + len(down)):
        assert ts.iloc[i] == ts.iloc[n_up - 1]
        assert closes[i] < ts.iloc[i]             # color red = short stays open
    # the bounce bar satisfies close > HHV and close > prev close -> TS drops to the HHV,
    # putting the line UNDER the close: red->green flip = the short's exit bar
    i = len(closes) - 1
    assert ts.iloc[i] < ts.iloc[i - 1]            # the HHV dragged the accepted level down
    assert closes[i] > ts.iloc[i]                 # color green -> short exits (close > TS)
