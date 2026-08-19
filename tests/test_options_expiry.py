"""The expiry guard that would have caught the 2026-08-19 OPRA merge bug.

Regression cover for the defect described in udb_orb.options.expiry: a quote book keyed
without an expiry let a 0DTE exit scan fill on a later-expiry quote.
"""

import datetime as dt

import pytest

from udb_orb.options import ExpiryMismatch, assert_expiry, osi_expiry, parse_osi, quote_book

# The real contaminated case, straight from the OPRA cache.
ENTRY_0DTE = "SPXW  230302C03945000"   # true 0DTE on 2023-03-02, entry ask $10.10
LATER_EXP = "SPXW  230324C03945000"   # same strike, 3/24 expiry, $81.40 bid


def test_parse_osi_round_trip():
    root, exp, cp, strike = parse_osi(ENTRY_0DTE)
    assert (root, exp, cp, strike) == ("SPXW", dt.date(2023, 3, 2), "C", 3945.0)


def test_parse_osi_handles_no_padding():
    assert parse_osi("TSLA250815C00300000")[1] == dt.date(2025, 8, 15)


@pytest.mark.parametrize("bad", ["", "SPXW", "SPXW 230302C394500", "not a symbol"])
def test_parse_osi_rejects_malformed(bad):
    # Must raise, never fall through to a permissive default -- that is how an expiry
    # stops being checked in the first place.
    with pytest.raises(ValueError):
        parse_osi(bad)


def test_assert_expiry_accepts_the_true_0dte():
    assert_expiry(ENTRY_0DTE, dt.date(2023, 3, 2))
    assert_expiry(ENTRY_0DTE, "2023-03-02")          # ISO string form


def test_assert_expiry_rejects_the_contaminating_contract():
    """The exact fill that fabricated +$7,060."""
    with pytest.raises(ExpiryMismatch) as e:
        assert_expiry(LATER_EXP, "2023-03-02", context="exit scan")
    msg = str(e.value)
    assert "2023-03-24" in msg and "2023-03-02" in msg and "exit scan" in msg


def test_quote_book_keeps_expiries_apart():
    """Two expiries at the SAME strike must land in different series, not one."""
    rows = [
        {"symbol": ENTRY_0DTE, "mod": 585, "bid": 9.9, "ask": 10.1},
        {"symbol": LATER_EXP, "mod": 602, "bid": 81.4, "ask": 82.0},
        {"symbol": ENTRY_0DTE, "mod": 610, "bid": 6.2, "ask": 6.4},
    ]
    book = quote_book(rows)
    assert set(book) == {ENTRY_0DTE, LATER_EXP}
    # The 0DTE series must NOT contain the $81.40 print that satisfied the +50% target.
    assert [r["bid"] for r in book[ENTRY_0DTE]] == [9.9, 6.2]


def test_quote_book_flags_a_too_wide_pull_window():
    rows = [{"symbol": ENTRY_0DTE}, {"symbol": LATER_EXP}]
    with pytest.raises(ExpiryMismatch):
        quote_book(rows, expected_expiry=dt.date(2023, 3, 2), context="pull window")


def test_quote_book_accepts_a_per_row_expiry_callable():
    rows = [
        {"symbol": ENTRY_0DTE, "day": "2023-03-02"},
        {"symbol": "SPXW  230303C03945000", "day": "2023-03-03"},
    ]
    book = quote_book(rows, expected_expiry=lambda r: r["day"])
    assert len(book) == 2


def test_osi_expiry_shorthand():
    assert osi_expiry(LATER_EXP) == dt.date(2023, 3, 24)
