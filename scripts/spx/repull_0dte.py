"""Re-pull and re-price SPX BOT1 on TRUE 0DTE quotes, month by month.

WHY: scripts/spx/pull_hersystem.py fetched each day's 0DTE symbols over a WHOLE-MONTH
window and scripts/spx/price_hersystem_ts30.py keyed its book on (day, cp, strike) with
no expiry, so 2-8 expiries merged into one series and the exit scan filled on
later-expiry quotes. Every SPX historical dollar figure derived from that pair is void
-- see udb_orb.options.expiry and memory spx-opra-expiry-merge-bug.

THIS SCRIPT IS THE REPLACEMENT. Three structural guarantees, not conventions:
  1. Only symbols whose OSI expiry IS a trade day are ever requested, and a trade may
     only ever be priced from the ONE symbol expiring on its own day.
  2. Every quote row is filtered to the trade day before it can be used, so a contract
     cannot be priced off a different session's quotes.
  3. Every returned symbol is run through udb_orb.options.assert_expiry, and the book is
     keyed on the full OSI symbol, so two expiries can never land in one series.
(1)+(2) are independent: either alone blocks the merge. The ORIGINAL bug was NOT the
month-wide pull window -- it was the (day, cp, strike) book key, which dropped the expiry
and let a $10.10 0DTE entry exit against an $81.40 quote from the 3/24 contract. A wide
window is safe precisely as long as the book is symbol-keyed and rows are day-filtered,
which is what forward_test.py has always done.

Signals come from forward_test_spx.day_entries, i.e. the SAME function the live forward
ledger uses, so the signal leg cannot drift from production.

Days with no true 0DTE contract are recorded as unpriceable, NEVER silently priced on a
later expiry -- that substitution is the whole bug. SPX had no Tue/Thu 0DTE before
2022-04-18 / 2022-05-11, so early-2022 gaps are expected and real (and on holiday weeks
the Monday expiry shifts to Tuesday -- 2022-01-18 is a real 220118 contract).

PULLED PER MONTH, not per day. A per-day pull spends 5-23s server-side on every session
with no 0DTE contract and ran at ~90s/session => 28 hours for 2022-2026. One request per
month is ~55 requests instead of 1,137. Month parquet cache (m_YYYY-MM.parquet) =>
resumable and idempotent; a re-price costs nothing.
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

from udb_orb.options import ExpiryMismatch, assert_expiry, osi_expiry  # noqa: E402

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


def symbols_for(day, cp, atm):
    """The ATM ladder expiring ON `day`. No other expiry is ever requested."""
    exp = dt.date.fromisoformat(day)
    return sorted({osi(exp, cp, atm + d) for d in LADDER})


def fetch_month(cl, month, plan_days):
    """One request for a whole month. `plan_days` maps day -> (cp, atm, ...).

    Only symbols expiring on one of this month's trade days are requested, and every
    symbol that comes back is asserted to expire on one of them.
    """
    syms, exps = set(), set()
    for day, p in plan_days.items():
        syms.update(symbols_for(day, p["cp"], p["atm"]))
        exps.add(dt.date.fromisoformat(day))
    lo, hi = min(exps), max(exps)
    try:
        q = cl.timeseries.get_range(
            dataset="OPRA.PILLAR", symbols=sorted(syms), stype_in="raw_symbol",
            schema="cbbo-1m", start=lo.isoformat(),
            end=(hi + dt.timedelta(days=1)).isoformat(),
        ).to_df().reset_index()
    except Exception as e:
        if "symbology" in str(e).lower() or "422" in str(e):
            return None                  # nothing in this month resolved -- a real gap
        raise
    if not len(q):
        return None
    # GUARD: every symbol must expire on one of this month's trade days. A symbol whose
    # expiry is not a requested trade day means the request leaked -- fail loudly.
    for s in q["symbol"].unique():
        if osi_expiry(s) not in exps:
            raise ExpiryMismatch(
                f"{s} expires {osi_expiry(s)}, not a requested {month} trade day"
            )
    return q


def prep(q):
    """Attach ET day + minute-of-day once per month, so per-day slicing is cheap."""
    # OPRA serves occasional rows with a null ts_event; the minute-of-day cast below is
    # int, so one NaT takes the whole day down (IntCastingNaNError). Drop them first --
    # price_hersystem.py hit this years ago and filtered for the same reason.
    q = q[q["ts_event"].notna()]
    if not len(q):
        return q.assign(ask=[], bid=[], qday=[], mod=[])
    sc = 1e9 if q["ask_px_00"].abs().median() > 1e6 else 1.0
    q = q.assign(ask=q["ask_px_00"] / sc, bid=q["bid_px_00"] / sc)
    t = pd.to_datetime(q["ts_event"], utc=True).dt.tz_convert("America/New_York")
    q = q.assign(qday=t.dt.strftime("%Y-%m-%d"),
                 mod=(t.dt.hour * 60 + t.dt.minute).astype(int))
    return q[q["bid"].notna() & (q["ask"] > 0)]


def book_of(q, day):
    """Book for ONE session, keyed on the full OSI symbol.

    Two independent guards: rows are filtered to `day` (so a contract cannot be priced
    off another session), and only symbols expiring on `day` are admitted (so a later
    expiry cannot supply a quote -- the exact substitution that fabricated +$7,060).
    """
    exp = dt.date.fromisoformat(day)
    q = q[q["qday"] == day]
    bk = {}
    for s, g in q.groupby("symbol"):
        assert_expiry(s, exp, context="repull " + day)
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
    ap.add_argument("--workers", type=int, default=4,
                    help="concurrent session fetches (a request is ~20s of latency "
                         "whatever its size, so count is the wall clock)")
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

    # ---- plan every session's BOT1 signal first, then pull month by month ----
    plan, rows, nogap = {}, [], 0
    for day in days:
        g = bars[bars["day"] == day].sort_values("mod")
        if len(g) < 15:
            continue
        ents = day_entries(g)
        if "bot1" not in ents:
            nogap += 1
            rows.append(dict(day=day, status="no OR break"))
            continue
        d, mod, px = ents["bot1"]
        plan[day] = dict(dir=d, cp="C" if d == "up" else "P",
                         atm=round(px / 5) * 5, mod=mod, px=px)

    print("%d sessions with a BOT1 signal; %d no-OR-break"
          % (len(plan), nogap), flush=True)

    def dpath(day):
        return os.path.join(QCACHE, day + ".parquet")

    # ---- PHASE 1: fetch each session's ATM ladder, in parallel ----
    # A Databento request costs ~20s of latency almost regardless of size, so wall clock is
    # request COUNT. Month batching looked like the fix (55 requests, not 1,128) but is a
    # trap: the request window spans the whole month, so every symbol is served for all ~20
    # days it was listed, not just its expiry day -- ~20x the bytes, and no month completed
    # in 12 minutes. One request per DAY is small and bounded; parallelism supplies the
    # throughput. Conservative worker count -- fetch_universe.py once ate an HTTP 429 at 8.
    todo = [d for d in sorted(plan) if not os.path.exists(dpath(d))]
    cached = len(plan) - len(todo)
    pulled = failed = 0
    if todo and not a.price_only:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print("fetching %d session(s) with %d workers (%d already cached)"
              % (len(todo), a.workers, cached), flush=True)

        def job(day):
            q = fetch_month(cl, day, {day: plan[day]})   # lo==hi -> a one-day window
            if q is not None:
                q.to_parquet(dpath(day), index=False)
            return day, (0 if q is None else len(q))

        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(job, d): d for d in todo}
            done = 0
            for fu in as_completed(futs):
                d = futs[fu]
                done += 1
                try:
                    _, n = fu.result()
                    pulled += 1
                    if not n:
                        print("  %s  no 0DTE contract" % d, flush=True)
                except ExpiryMismatch as e:
                    failed += 1
                    print("  %s  GUARD TRIPPED -- %s" % (d, e), flush=True)
                except Exception as e:
                    failed += 1
                    print("  %s  ERROR %s" % (d, str(e)[:70]), flush=True)
                if done % 50 == 0:
                    print("  [%4d/%d] fetched" % (done, len(todo)), flush=True)

    # ---- PHASE 2: price from cache (no API, so this is free to re-run) ----
    skipped = bad = 0
    for day, p in sorted(plan.items()):
        f = dpath(day)
        if not os.path.exists(f):
            rows.append(dict(day=day, dir=p["dir"], status="no 0DTE contract"))
            skipped += 1
            continue
        try:
            r = price_bot1(book_of(prep(pd.read_parquet(f)), day), p["mod"], p["px"])
        except ExpiryMismatch as e:
            print("  %s: GUARD TRIPPED -- %s" % (day, e), flush=True)
            rows.append(dict(day=day, dir=p["dir"], status="expiry guard tripped"))
            bad += 1
            continue
        if r is None:
            rows.append(dict(day=day, dir=p["dir"], status="no 0DTE contract"))
            skipped += 1
            continue
        rows.append(dict(day=day, dir=p["dir"], entry_mod=p["mod"],
                         spot=round(p["px"], 2), status="priced", **r))

    r = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    r.to_csv(OUT, index=False)
    P = r[r["status"] == "priced"] if "status" in r else r

    print("\nsessions %d | priced %d | no OR break %d | unpriceable %d | guard trips %d"
          % (len(days), len(P), nogap, skipped, bad))
    print("pulled %d new session(s), %d from cache, %d fetch failure(s) -> %s"
          % (pulled, cached, failed, OUT))
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
