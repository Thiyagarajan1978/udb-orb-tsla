"""The 15:55 entry that can never fill.

Pine blocks it with `rthIx < 77` (v1.2.1). The Python signal side did not, so a break on
the last bar of the session produced an entry minute of 16:00 -- after every quote in the
session, so the trade could neither fill nor close. It fired on 1 session in 1,128
(2023-09-05) and priced to nothing, which is why it read as a missing quote rather than a
phantom trade for months. Guard both directions and the boundary.
"""
import sys, os
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from forward_test_spx import day_entries, LAST_ENTRY_IX, BUF


def session(break_ix=None, direction="up", n=78):
    """A flat 4500 session; optionally one bar that breaks the 15m OR at `break_ix`."""
    rows = []
    for i in range(n):
        mod = 570 + 5 * i
        o = h = l = c = 4500.0
        if i == break_ix:
            c = 4500.0 * (1 + 3 * BUF) if direction == "up" else 4500.0 * (1 - 3 * BUF)
            h, l = max(o, c), min(o, c)
        rows.append(dict(mod=mod, open=o, high=h, low=l, close=c))
    return pd.DataFrame(rows)


@pytest.mark.parametrize("direction", ["up", "dn"])
def test_last_bar_break_is_not_an_entry(direction):
    """rthIx 77 is the 15:55 bar -- Pine refuses it, so Python must too."""
    assert "bot1" not in day_entries(session(break_ix=77, direction=direction))


@pytest.mark.parametrize("direction", ["up", "dn"])
def test_bar_before_the_cutoff_still_entries(direction):
    """The guard must not eat rthIx 76 -- that trade is legal and closes at EOD."""
    e = day_entries(session(break_ix=76, direction=direction))
    assert e["bot1"][0] == direction
    assert e["bot1"][1] == 570 + 5 * 76 + 5      # bar close, per the barclose fix


def test_entry_minute_never_exceeds_the_session():
    """The real failure mode: an entry stamped after the last quote of the day."""
    g = session(break_ix=77)
    last = int(g["mod"].iloc[-1])
    for _, (_, mod, _) in day_entries(g).items():
        assert mod <= last, f"entry at {mod} is past the last bar at {last}"


def test_cutoff_matches_pine():
    assert LAST_ENTRY_IX == 77
