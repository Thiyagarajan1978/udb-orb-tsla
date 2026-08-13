"""Fail loudly when a forward-test ledger stops advancing.

WHY THIS EXISTS. Both forward-test scripts treat "OPRA has not released that session yet" as a
clean no-op and exit 0 -- which is correct, because a release lag is not a fault. The failure
mode that creates is that Task Scheduler reports success indefinitely while the ledger silently
falls behind, and nothing anywhere says so.

That is not hypothetical. On 2026-08-12 the 09:00 task showed `rc=0` and the run log said
"No new priceable days", while the TSLA ledger had been stuck on session 2026-08-10 for two
days; the real cause was only visible in the SPX log, as a Databento
`403 license_not_found_unauthorized / A live data license is required` -- OPRA historical
availability had started lagging past the T+1 the 09:00 schedule assumes. `forward_test_FAILED.txt`
was three sessions stale from an unrelated issue and looked reassuring.

So: this checks the OUTCOME (did the ledger move?) rather than the exit code. A gap inside the
normal T+1-plus-a-weekend rhythm is fine; beyond that the run is not doing its job.

    python scripts/check_ledger_freshness.py            # warn-only, always exits 0
    python scripts/check_ledger_freshness.py --strict   # exit 1 when stale (use in the .bat)
    python scripts/check_ledger_freshness.py --max-lag 5

Trading days here are weekdays only -- market holidays are not modelled, so a holiday week
loosens the check by a day rather than crying wolf. That is the right direction to be wrong in.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGERS = {
    "TSLA": (os.path.join(ROOT, "exports", "forward_options_ledger.csv"), "trade_day"),
    "SPX": (os.path.join(ROOT, "exports", "forward_spx_ledger.csv"), None),
}
# Healthy steady state is lag 0: the 09:00 run prices yesterday's session under OPRA's T+1
# release. A lag of 1 is a KNOWN-NORMAL slip -- the 09:00 schedule sometimes beats the release,
# and it self-heals the next morning -- so alarming on it would train the alarm to be ignored.
# A lag that survives into a second day has stopped self-healing and is the real signal, so the
# default trips at 2. (2026-08-12 sat at exactly 1 and was already broken; 08-13 would trip it.)
DEFAULT_MAX_LAG = 1


def _weekdays_between(a: date, b: date) -> int:
    """Weekdays strictly after `a`, up to and including `b`."""
    n, cur = 0, a + timedelta(days=1)
    while cur <= b:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def _last_session(today: date) -> date:
    """The most recent weekday that has already closed (today counts only after the close)."""
    cur = today - timedelta(days=1)
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


def _session_column(df: pd.DataFrame, preferred: str | None) -> str | None:
    if preferred and preferred in df.columns:
        return preferred
    for c in ("trade_day", "session", "date", "day"):
        if c in df.columns:
            return c
    return None


def check(name: str, path: str, col: str | None, max_lag: int, today: date) -> tuple[bool, str]:
    if not os.path.exists(path):
        return False, f"{name}: MISSING {os.path.relpath(path, ROOT)}"
    try:
        df = pd.read_csv(path)
    except Exception as exc:                                  # pragma: no cover - IO shape
        return False, f"{name}: UNREADABLE ({exc})"
    if df.empty:
        return False, f"{name}: EMPTY"
    col = _session_column(df, col)
    if col is None:
        return False, f"{name}: no session-date column in {list(df.columns)[:6]}"

    newest = pd.to_datetime(df[col]).max().date()
    target = _last_session(today)
    lag = _weekdays_between(newest, target)
    rel = os.path.relpath(path, ROOT)
    if lag > max_lag:
        return False, (f"{name}: STALE - newest session {newest}, last closed session {target}, "
                       f"{lag} trading day(s) behind (limit {max_lag}). "
                       f"Check the run log for a Databento 403/503, not the exit code. [{rel}]")
    return True, f"{name}: ok - newest session {newest}, {lag} trading day(s) behind [{rel}]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-lag", type=int, default=DEFAULT_MAX_LAG,
                    help=f"trading days a ledger may lag before it is stale (default "
                         f"{DEFAULT_MAX_LAG})")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any ledger is stale (default: report only)")
    ap.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD), for tests")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    stale = 0
    for name, (path, col) in LEDGERS.items():
        ok, msg = check(name, path, col, args.max_lag, today)
        print(("  " if ok else "!! ") + msg)
        stale += not ok
    if stale:
        print(f"\n{stale} ledger(s) not advancing - the forward test is exiting 0 without "
              f"doing its job.")
    return 1 if (stale and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
