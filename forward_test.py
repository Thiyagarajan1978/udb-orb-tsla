"""Out-of-sample OPTIONS forward test (Databento shadow, no broker, no money).

Each run prices the FROZEN strategy's new-session signals against REAL TSLA option
quotes (Databento OPRA cbbo-1m, buy-ask / sell-bid = conservative) and APPENDS to a
running ledger. Prices BOTH expiries per trade: 0DTE (nearest) and WEEKLY (nearest
Friday). Idempotent — never re-prices a day already in the ledger. OPRA releases T+1,
so it prices sessions up to the last fully-available day.

Usage:
    python forward_test.py                 # price all new priceable days since the ledger
    python forward_test.py --start 2026-07-10 --end 2026-07-16   # explicit range
    python forward_test.py --dry-run       # print, do not write the ledger

Requires DATABENTO_API_KEY (env var, or in .env / gap_analyzer .env). FMP key as usual.
"""
import argparse, os, re, sys, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import pandas as pd
from udb_orb.config import load_config, db_path
from udb_orb.data.fmp_client import rth_only, fetch_5min
from udb_orb.db.database import Database
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(ROOT, "exports", "forward_options_ledger.csv")
PROFILES = [("A1", "config/tsla_best_A.yaml"), ("B1", "config/tsla_best_B.yaml"),
            ("C1", "config/tsla_config_C1.yaml"), ("C2", "config/tsla_config_C.yaml")]
DATASET = "OPRA.PILLAR"


def get_db_key():
    k = os.getenv("DATABENTO_API_KEY")
    if k:
        return k
    for path in (os.path.join(ROOT, ".env"), os.path.expanduser("~/gap_analyzer/.env"),
                 r"C:\Users\TT\gap_analyzer\.env"):
        try:
            for line in open(path):
                m = re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
                if m:
                    return m.group(1)
        except FileNotFoundError:
            pass
    sys.exit("DATABENTO_API_KEY not found — set the env var or add it to .env")


def parse_osi(sym):
    b = sym[-15:]
    return dt.date(2000 + int(b[0:2]), int(b[2:4]), int(b[4:6])), b[6], int(b[7:15]) / 1000


def load_underlying():
    """DB history (warmup) + fresh FMP for recent/new sessions."""
    with Database(db_path(load_config())) as dbx:
        old = rth_only(dbx.load_bars("TSLA"))
    last = old.index[-1].date()
    fresh = rth_only(fetch_5min("TSLA", last - dt.timedelta(days=3), dt.date.today()))
    full = pd.concat([old, fresh]).sort_index()
    return full[~full.index.duplicated(keep="last")]


def signals(full):
    """{profile: [trades]} for every session in the frame."""
    out = {}
    for name, path in PROFILES:
        cfg = load_config(path)
        out[name] = run_engine(full, Params.from_config(cfg), cfg["enhancements"]).trades
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import databento as dbnt
    cl = dbnt.Historical(get_db_key())
    rng_end = pd.to_datetime(cl.metadata.get_dataset_range(DATASET)["end"])  # UTC, T+1 release edge

    full = load_underlying()
    sig = signals(full)
    all_days = sorted({t.day for T in sig.values() for t in T})

    done = set()
    if os.path.exists(LEDGER):
        done = set(pd.read_csv(LEDGER, usecols=["trade_day"])["trade_day"].astype(str))

    def priceable(day):
        close_utc = pd.Timestamp(day + " 16:00", tz="America/New_York").tz_convert("UTC")
        return close_utc <= rng_end

    last_logged = max(done) if done else None
    if args.start:
        days = [d for d in all_days if args.start <= d <= (args.end or "9999")]
    elif last_logged:
        days = [d for d in all_days if d > last_logged]          # only sessions after the ledger
    else:
        days = all_days[-1:]                                     # first run, no ledger: seed the latest day only
    days = [d for d in days if priceable(d) and d not in done]
    if not days:
        print("No new priceable days. Ledger current through the last released OPRA session.")
        return
    print(f"Pricing {len(days)} new session(s): {days[0]} .. {days[-1]}  (OPRA available through {rng_end.date()})")

    # ---- definitions per day (nearest + weekly-Friday contract ladder) ----
    defs = {}
    for day in days:
        dd = cl.timeseries.get_range(dataset=DATASET, symbols=["TSLA.OPT"], stype_in="parent",
                                     schema="definition", start=day,
                                     end=(dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
                                     ).to_df()[["raw_symbol"]].drop_duplicates("raw_symbol")
        defs[day] = pd.DataFrame([(s,) + parse_osi(s) for s in dd.raw_symbol],
                                 columns=["symbol", "exp", "cp", "strike"])

    def pick(day, price, cp, weekly):
        df = defs[day]
        D = dt.date.fromisoformat(day)
        sub = df[(df.cp == cp) & (df.exp >= D)]
        if weekly:
            sub = sub[sub.exp.apply(lambda e: e.weekday() == 4)]
        if sub.empty:
            return None, None
        e = sub.exp.min()
        sub = sub[sub.exp == e]
        return sub.iloc[(sub.strike - price).abs().argmin()].symbol, (e - D).days

    # ---- collect contracts, pull quotes ----
    need = set()
    for name, T in sig.items():
        for t in T:
            if t.day not in days:
                continue
            cp = "C" if t.direction.startswith("L") else "P"
            for w in (False, True):
                s, _ = pick(t.day, t.entry_price, cp, w)
                if s:
                    need.add(s)
    q = cl.timeseries.get_range(dataset=DATASET, symbols=sorted(need), stype_in="raw_symbol",
                                schema="cbbo-1m", start=days[0],
                                end=(dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1)).isoformat()
                                ).to_df().reset_index()
    scale = 1e9 if q["ask_px_00"].abs().median() > 1e6 else 1.0
    q["ask"] = q["ask_px_00"] / scale
    q["bid"] = q["bid_px_00"] / scale
    q["t"] = pd.to_datetime(q["ts_event"], utc=True)
    q = q[(q.ask > 0) & (q.bid > 0)]
    quotes = {s: g.sort_values("t").reset_index(drop=True) for s, g in q.groupby("symbol")}

    def qv(sym, ts, col):
        g = quotes.get(sym)
        if g is None or g.empty:
            return None
        tt = ts.tz_convert("UTC")
        i = g["t"].searchsorted(tt)
        c = g.iloc[max(0, i - 1):i + 1]
        if c.empty:
            return None
        r = c.iloc[(c["t"] - tt).abs().values.argmin()]
        return None if abs((r["t"] - tt).total_seconds()) > 900 else float(r[col])

    # ---- price + build rows ----
    run_ts = pd.Timestamp(rng_end).strftime("%Y-%m-%dT%H:%M")  # deterministic stamp = data edge (no Date.now)
    rows = []
    daysum = {}
    for name, T in sig.items():
        for t in T:
            if t.day not in days:
                continue
            cp = "C" if t.direction.startswith("L") else "P"
            rec = dict(run_ts=run_ts, trade_day=t.day, profile=name, direction=t.direction,
                       is_reversal=t.is_reversal, entry_ts=t.entry_ts.strftime("%H:%M"),
                       entry_px=round(t.entry_price, 2), exit_ts=t.exit_ts.strftime("%H:%M"),
                       exit_px=round(t.exit_price, 2), reason=t.reason, dur_min=t.duration_bars * 5,
                       cp=cp, share_pnl_u=round(t.pnl_total, 3), share_pnl_25=round(t.pnl_total * 25, 2))
            for tag, w in (("dte0", False), ("wk", True)):
                s, d = pick(t.day, t.entry_price, cp, w)
                a = qv(s, t.entry_ts, "ask") if s else None
                b = qv(s, t.exit_ts, "bid") if s else None
                rec[f"{tag}_sym"] = s
                rec[f"{tag}_dte"] = d
                rec[f"{tag}_opt_1ct"] = round((b - a) * 100, 2) if (a is not None and b is not None) else None
            rows.append(rec)
            key = (t.day, name)
            daysum.setdefault(key, [0.0, 0.0, 0.0])
            daysum[key][0] += rec["share_pnl_25"]
            daysum[key][1] += rec["dte0_opt_1ct"] or 0
            daysum[key][2] += rec["wk_opt_1ct"] or 0

    new = pd.DataFrame(rows)
    print("\n=== NEW forward-test rows (per day x profile: shares@25 | 0DTE@1ct | weekly@1ct) ===")
    for (day, name) in sorted(daysum):
        sh, o0, ow = daysum[(day, name)]
        print(f"  {day}  {name:<3}  shares ${sh:>+7.0f}   0DTE ${o0:>+7.0f}   weekly ${ow:>+7.0f}")

    if args.dry_run:
        print("\n(dry-run — ledger not written)")
        return
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    header = not os.path.exists(LEDGER)
    new.to_csv(LEDGER, mode="a", header=header, index=False)
    total = pd.read_csv(LEDGER)
    print(f"\nAppended {len(new)} rows -> {LEDGER}  (ledger now {len(total)} rows, "
          f"{total['trade_day'].nunique()} sessions {total['trade_day'].min()}..{total['trade_day'].max()})")


if __name__ == "__main__":
    main()
