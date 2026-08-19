"""Expiry guards for OPRA quote books.

WHY THIS EXISTS (2026-08-19). ``scripts/spx/price_hersystem_ts30.py`` keyed its quote
book on ``(day, cp, strike)`` with no expiry, while ``scripts/spx/pull_hersystem.py``
fetched each day's 0DTE symbols over a WHOLE MONTH. Two to eight expiries therefore
landed at the same strike and were sorted into ONE time series, so the exit scan walked
off a 0DTE entry onto a later-expiry quote. Verified on 2023-03-02 CALL 3945: the 0DTE
entry ask is $10.10 and the next quote in the merged series is an $81.40 bid belonging
to the 3/24 contract, instantly satisfying the +50% target for a fabricated +$7,060.
Re-priced correctly, 2022-23 BOT1 ts30 is -$32,310 against the +$116,955 published for
the same 465 sessions.

The defect survived years of analysis because nothing ever checked. Every pricer should
now route its book through :func:`quote_book` (or call :func:`assert_expiry` per fill) so
a wrong-expiry quote fails loudly instead of paying out.

Network-free and unit-tested, per the project convention.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

# OSI: root, 6-digit expiry (YYMMDD), C/P, 8-digit strike in thousandths.
_OSI = re.compile(r"^(?P<root>[A-Z0-9]{1,6})\s*(?P<exp>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


class ExpiryMismatch(ValueError):
    """A quote was about to be used for a contract it does not belong to."""


def parse_osi(symbol: str) -> tuple[str, dt.date, str, float]:
    """Split an OSI symbol into ``(root, expiry, cp, strike)``.

    Raises ``ValueError`` on anything that is not a well-formed OSI symbol -- an
    unparseable symbol must never fall through to a permissive default, because that is
    precisely how an expiry stops being checked.
    """
    m = _OSI.match(symbol.strip().replace(" ", ""))
    if not m:
        raise ValueError(f"not an OSI option symbol: {symbol!r}")
    e = m.group("exp")
    return (
        m.group("root"),
        dt.date(2000 + int(e[:2]), int(e[2:4]), int(e[4:6])),
        m.group("cp"),
        int(m.group("strike")) / 1000.0,
    )


def osi_expiry(symbol: str) -> dt.date:
    """The expiry encoded in an OSI symbol."""
    return parse_osi(symbol)[1]


def assert_expiry(symbol: str, expected: dt.date | str, *, context: str = "") -> None:
    """Raise :class:`ExpiryMismatch` unless ``symbol`` expires on ``expected``.

    Call this on every fill -- entry AND exit. The 2026-08-19 bug produced a correct
    entry and a wrong-expiry exit, so checking only the entry would have missed it.
    """
    want = dt.date.fromisoformat(expected) if isinstance(expected, str) else expected
    got = osi_expiry(symbol)
    if got != want:
        where = f" [{context}]" if context else ""
        raise ExpiryMismatch(
            f"{symbol} expires {got}, expected {want}{where}. A quote book keyed without "
            f"expiry will silently price this contract against another one -- see "
            f"udb_orb.options.expiry for the 2023-03-02 CALL 3945 case."
        )


def quote_book(rows: Iterable[dict], *, expected_expiry=None, context: str = "") -> dict:
    """Build a quote book keyed on ``(symbol, ...)`` so expiries can never merge.

    ``rows`` are mappings carrying at least ``symbol``. The returned dict maps the full
    OSI symbol to the list of its rows, in input order. Keying on the symbol -- rather
    than on ``(day, cp, strike)`` -- is what makes the merge structurally impossible;
    ``forward_test.py`` has always done this and was never affected.

    If ``expected_expiry`` is given (a date, or a callable taking the row and returning
    one), every row is checked against it, which additionally catches a pull whose date
    window was too wide.
    """
    book: dict[str, list[dict]] = {}
    for r in rows:
        sym = r["symbol"]
        if expected_expiry is not None:
            want = expected_expiry(r) if callable(expected_expiry) else expected_expiry
            assert_expiry(sym, want, context=context)
        book.setdefault(sym, []).append(r)
    return book
