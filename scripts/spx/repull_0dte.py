"""Re-pull and re-price SPX BOT1 on TRUE 0DTE quotes, one day at a time.

WHY: scripts/spx/pull_hersystem.py fetched each day's 0DTE symbols over a WHOLE-MONTH
window and scripts/spx/price_hersystem_ts30.py keyed its book on (day, cp, strike) with
no expiry, so 2-8 expiries merged into one series and the exit scan filled on
later-expiry quotes. Every SPX historical dollar figure derived from that pair is void
-- see udb_orb.options.expiry and memory spx-opra-expiry-merge-bug.

THIS SCRIPT IS THE REPLACEMENT. Three structural guarantees, not conventions:
  1. The pull window is ONE SESSION (start=day, end=day+1) -- a contract cannot return
     quotes from a later session even if it wanted to.
  2. Only symbols whose OSI expiry IS the trade day are ever requested.
  3. Every returned symbol is run through udb_orb.options.assert_expiry before it can
     reach the book, and the book is keyed on the full OSI symbol.
Signals come from forward_test_spx.day_entries, i.e. the SAME function the live forward
ledger uses, so the signal leg cannot drift from production.

Days with no true 0DTE contract are recorded as unpriceable, NEVER silently priced on a
later expiry -- that substitution is the whole bug. SPX had no Tue/Thu 0DTE before
2022-04-18 / 2022-05-11, so early-2022 gaps are expected and real.

Per-day parquet cache => resumable and idempotent; a re-price costs nothing.
Cost is trivial (a metadata.get_cost probe put the full 2022-2026 pull at ~$0.66).

    python scripts/spx/repull_0dte.py                    # all cached sessions
    python scripts/spx/repull_0dte.py --start 2026-01-02 # a slice
    python scripts/spx/repull_0dte.py --price-only       # re-price from cache, no API
"""

import argparse
import datetime as dt
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from udb_orb.options import ExpiryMismatch, assert_expiry  # noqa: E402

from forward_test_spx import (  # noqa: E402
    L_STP,
    L_TGT,
    TS_MAIN,
    day_entries,
    get_db_key,
    import_databento,
    osi,
)

BARS = os.path.join(ROOT, "data", "cache", "spx")
QCACHE = os.path.join(BARS, "opra_0dte")
OUT = os.path.join(ROOT, "exports", "spx_bot1_0dte_clean.csv")
LADDER = (-10, -5, 0, 5, 10)          # ATM +/- 2 strikes, same ladder as forward_test_spx
_STRIKE = re.compile(r"([CP])(\d{8})$")


def sessions():
    """Cached SPX 5m RTH bars, one frame, deduped."""
    fs = sorted(glob.glob(os.path.join(BARS, "spx_5m*.parquet")))
    if not fs:
        sys.exit("no SPX bar cache under " + BARS)
    b = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    b = b[~b.index.duplicated(keep="last")].between_time("09:30", "15:55").copy()
    b["day"] = b.index.strftime("%Y-%m-%d")
    b["mod"] = b.index.hour * 60 + b.index.minute
    return b


def fetch_day(cl, day, cp, atm):
    """One session, one expiry. Returns a quote frame or None if no 0DTE contract exists."""
    exp = dt.date.fromisoformat(day)
    syms = sorted({osi(exp, cp, atm + d) for d in LADDER})
    try:
        q = cl.timeseries.get_range(
            dataset="OPRA.PILLAR", symbols=syms, stype_in="raw_symbol", schema="cbbo-1m",
            start=day, end=(exp + dt.timedelta(days=1)).isoformat(),
        ).to_df().reset_index()
    except Exception as e:
        if "symbology" in str(e).lower() or "422" in str(e):
            return None                      # no contract expiring this day -- a real gap
        raise
    if not len(q):
        return None
    # GUARD: nothing that is not a true 0DTE may reach the book.
    for s in q["symbol"].unique():
        assert_expiry(s, exp, context="repull " + day)
    return q


def book_of(q):
    """Book keyed on the full OSI symbol, so expiries cannot merge."""
    sc = 1e9 if q["ask_px_00"].abs().median() > 1e6 else 1.0
    q = q.assign(ask=q["ask_px_00"] / sc, bid=q["bid_px_00"] / sc)
    t = pd.to_datetime(q["ts_event"], utc=True).dt.tz_convert("America/New_York")
    q = q.assign(mod=(t.dt.hour * 60 + t.dt.minute).astype(int))
    q = q[q["bid"].notna() & (q["ask"] > 0)]
    bk = {}
    for s, g in q.groupby("symbol"):
        g = g.sort_values("mod")
        bk[s] = (g["mod"].values, g["bid"].values, g["ask"].values,
                 int(_STRIKE.search(s).group(2)) / 1000)
    return bk


def price_bot1(bk, mod, px, ts=TS_MAIN):
    """BOT1 ts30 on one contract: +50% target / -50% stop on the bid, else time stop.

    Identical rule to forward_test_spx.py -- only the quote source is fixed.
    """
    if not bk:
        return None
    sym = min(bk, key=lambda s: abs(bk[s][3] - px))
    mods, bid, ask, k = bk[sym]
    i = np.searchsorted(mods, mod, side="right") - 1
    if i < 0:
        return None
    ea = float(ask[i])
    if ea <= 0.05:
        return None
    m2 = mods > mod
    if not m2.any():
        return None
    r = held = why = None
    for m, b in zip(mods[m2], bid[m2]):
        if m - mod > ts:
            r, held, why = b - ea, int(m - mod), "time stop"
            break
        if b >= L_TGT * ea:
            r, held, why = b - ea, int(m - mod), "target +50%"
            break
        if b <= L_STP * ea:
            r, held, why = b - ea, int(m - mod), "stop -50%"
            break
    if r is None:
        r, held, why = float(bid[-1]) - ea, int(mods[-1] - mod), "eod"
    return dict(symbol=sym, strike=k, prem=round(ea, 2), pnl_1ct=round(r * 100, 1),
                held_min=held, exit_reason=why)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--price-only", action="store_true",
                    help="re-price from cache, no API calls")
    a = ap.parse_args()
    os.makedirs(QCACHE, exist_ok=True)

    bars = sessions()
    days = sorted(bars["day"].unique())
    if a.start:
        days = [d for d in days if d >= a.start]
    if a.end:
        days = [d for d in days if d <= a.end]
    print("%d sessions: %s .. %s" % (len(days), days[0], days[-1]), flush=True)

    cl = None
    if not a.price_only:
        db = import_databento()
        cl = db.Historical(get_db_key())

    rows = []
    nogap = pulled = cached = skipped = bad = 0
    for n, day in enumerate(days, 1):
        g = bars[bars["day"] == day].sort_values("mod")
        if len(g) < 15:
            continue
        ents = day_entries(g)
        if "bot1" not in ents:
            nogap += 1
            rows.append(dict(day=day, status="no OR break"))
            continue
        d, mod, px = ents["bot1"]
        cp = "C" if d == "up" else "P"
        atm = round(px / 5) * 5
        f = os.path.join(QCACHE, day + ".parquet")

        if os.path.exists(f):
            q = pd.read_parquet(f)
            cached += 1
        elif a.price_only:
            rows.append(dict(day=day, dir=d, status="not cached"))
            skipped += 1
            continue
        else:
            try:
                q = fetch_day(cl, day, cp, atm)
            except ExpiryMismatch as e:
                print("  %s: GUARD TRIPPED -- %s" % (day, e), flush=True)
                rows.append(dict(day=day, dir=d, status="expiry guard tripped"))
                bad += 1
                continue
            if q is None:
                rows.append(dict(day=day, dir=d, status="no 0DTE contract"))
                skipped += 1
                continue
            q.to_parquet(f, index=False)
            pulled += 1

        p = price_bot1(book_of(q), mod, px)
        if p is None:
            rows.append(dict(day=day, dir=d, status="no usable quote"))
            skipped += 1
            continue
        rows.append(dict(day=day, dir=d, entry_mod=mod, spot=round(px, 2),
                         status="priced", **p))
        if n % 100 == 0:
            print("  %d/%d  %s  pulled=%d cached=%d skipped=%d"
                  % (n, len(days), day, pulled, cached, skipped), flush=True)

    r = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    r.to_csv(OUT, index=False)
    P = r[r["status"] == "priced"] if "status" in r else r

    print("\nsessions %d | priced %d | no OR break %d | unpriceable %d | guard trips %d"
          % (len(days), len(P), nogap, skipped, bad))
    print("pulled %d new day(s), %d from cache -> %s" % (pulled, cached, OUT))
    if len(P):
        P = P.copy()
        P["year"] = P["day"].str[:4]
        print("\n=== BOT1 ts30 on TRUE 0DTE, @1 contract ===")
        print("%-6s %5s %11s %7s %9s %6s" % ("year", "n", "net $", "WR", "avg $", "PF"))
        for y, gg in P.groupby("year"):
            w = gg[gg.pnl_1ct > 0]
            l = gg[gg.pnl_1ct < 0]
            pf = w.pnl_1ct.sum() / abs(l.pnl_1ct.sum()) if len(l) else float("nan")
            print("%-6s %5d %11s %6.1f%% %9s %6.2f"
                  % (y, len(gg), format(gg.pnl_1ct.sum(), ",.0f"), len(w) / len(gg) * 100,
                     format(gg.pnl_1ct.mean(), ",.0f"), pf))
        w = P[P.pnl_1ct > 0]
        l = P[P.pnl_1ct < 0]
        pf = w.pnl_1ct.sum() / abs(l.pnl_1ct.sum()) if len(l) else float("nan")
        print("%-6s %5d %11s %6.1f%% %9s %6.2f"
              % ("ALL", len(P), format(P.pnl_1ct.sum(), ",.0f"), len(w) / len(P) * 100,
                 format(P.pnl_1ct.mean(), ",.0f"), pf))
        print("\nexit reasons:", dict(P.exit_reason.value_counts()))


if __name__ == "__main__":
    main()
