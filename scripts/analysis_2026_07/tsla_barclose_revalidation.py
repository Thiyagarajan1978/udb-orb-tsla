"""TSLA options barclose re-validation (2025-01 .. 2026-07-17, all 4 profiles).

Reprices the frozen profiles' signals against real OPRA cbbo-1m quotes under BOTH fill
conventions:
  barstart — ask/bid at the signal bar's START ts (forward_test.py convention: LOOKAHEAD,
             the alert only exists at the bar close)
  barclose — ask/bid at ts+5min = the bar CLOSE, when the alert actually fires (realistic)
Both expiries: 0DTE (nearest) and WEEKLY (nearest Friday). Uses the local OPRA parquet
cache; pulls only missing contracts from Databento and appends them to a new cache file.
"""
import os, re, sys, datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(r"C:\Users\TT\udb-orb-tsla")
sys.path.insert(0, str(REPO / "src"))
import pandas as pd
from udb_orb.config import load_config, db_path
from udb_orb.data.fmp_client import rth_only, fetch_5min
from udb_orb.db.database import Database
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

CACHE = REPO / "data" / "cache" / "opra"
NEWQ = CACHE / "quotes_barclose_fill_gaps.parquet"
OUT = Path(r"C:\Users\TT\AppData\Local\Temp\claude\c--Users-TT-udb-orb-tsla\8586d0e4-ef89-405e-a887-ca05ac5468af\scratchpad\tsla_barclose_ledger.csv")
PROFILES = [("A1", "config/tsla_best_A.yaml"), ("B1", "config/tsla_best_B.yaml"),
            ("C1", "config/tsla_config_C1.yaml"), ("C2", "config/tsla_config_C.yaml")]
START_DAY = "2025-01-02"


def get_db_key():
    k = os.getenv("DATABENTO_API_KEY")
    if k:
        return k
    for path in (REPO / ".env", Path(r"C:\Users\TT\gap_analyzer\.env")):
        try:
            for line in open(path):
                m = re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
                if m:
                    return m.group(1)
        except FileNotFoundError:
            pass
    sys.exit("no DATABENTO_API_KEY")


# ---- bars + signals ----
with Database(db_path(load_config())) as dbx:
    old = rth_only(dbx.load_bars("TSLA"))
fresh = rth_only(fetch_5min("TSLA", old.index[-1].date() - dt.timedelta(days=3), dt.date(2026, 7, 17),
                            cache_dir=REPO / "data" / "cache"))
bars = pd.concat([old, fresh]).sort_index()
bars = bars[~bars.index.duplicated(keep="last")]
print(f"bars {bars.index.min().date()}..{bars.index.max().date()}", flush=True)

sig = {}
for name, path in PROFILES:
    cfg = load_config(REPO / path)
    tr = run_engine(bars.copy(), Params.from_config(cfg), cfg["enhancements"]).trades
    sig[name] = [t for t in tr if START_DAY <= t.day <= "2026-07-17"]
    print(f"{name}: {len(sig[name])} trades {sig[name][0].day}..{sig[name][-1].day}", flush=True)

# ---- contract picker from cached definitions ----
defs = pd.concat([pd.read_parquet(CACHE / "defs_full.parquet"),
                  pd.read_parquet(CACHE / "defs_july.parquet")], ignore_index=True)
defs["exp"] = pd.to_datetime(defs["exp"]).dt.date
defs["snap"] = pd.to_datetime(defs["snap"]).dt.date
defs = defs.drop_duplicates("symbol")

def pick(day, price, cp, weekly):
    D = dt.date.fromisoformat(day)
    sub = defs[(defs.cp == cp) & (defs.exp >= D) & (defs.snap <= D)]
    if weekly:
        sub = sub[sub.exp.apply(lambda e: e.weekday() == 4)]
    if sub.empty:
        return None, None
    e = sub.exp.min()
    sub = sub[sub.exp == e]
    r = sub.iloc[(sub.strike - price).abs().values.argmin()]
    return r.symbol, (e - D).days

# ---- quotes: local cache + gap pull ----
qparts = [pd.read_parquet(CACHE / f) for f in
          ("quotes_weekly.parquet", "quotes_full.parquet", "quotes_july.parquet")]
if NEWQ.exists():
    qparts.append(pd.read_parquet(NEWQ))
q = pd.concat(qparts, ignore_index=True).drop_duplicates(["ts_event", "symbol"])
scale = 1e9 if q["ask_px_00"].abs().median() > 1e6 else 1.0
q["ask"] = q["ask_px_00"] / scale
q["bid"] = q["bid_px_00"] / scale
q["t"] = pd.to_datetime(q["ts_event"], utc=True)
q = q[(q.ask > 0) & (q.bid > 0)]

# what (symbol, day) do we need?
need = defaultdict(set)   # symbol -> set of days
for name in sig:
    for t in sig[name]:
        cp = "C" if t.direction.startswith("L") else "P"
        for w in (False, True):
            s, _ = pick(t.day, t.entry_price, cp, w)
            if s:
                need[s].add(t.day)

have = set()
for s, g in q.groupby("symbol"):
    days_present = set(pd.to_datetime(g["t"]).dt.tz_convert("America/New_York").dt.date.astype(str))
    for d in need.get(s, ()):
        if d in days_present:
            have.add((s, d))
missing = [(s, d) for s in need for d in need[s] if (s, d) not in have]
print(f"contract-days needed {sum(len(v) for v in need.values())}, cached {len(have)}, missing {len(missing)}", flush=True)

if missing:
    import databento as dbnt
    cl = dbnt.Historical(get_db_key())
    by_day = defaultdict(set)
    for s, d in missing:
        by_day[d].add(s)
    pulled = []
    for d in sorted(by_day):
        end = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
        try:
            df = cl.timeseries.get_range(dataset="OPRA.PILLAR", symbols=sorted(by_day[d]),
                                         stype_in="raw_symbol", schema="cbbo-1m",
                                         start=d, end=end).to_df().reset_index()
            pulled.append(df[["ts_event", "symbol", "bid_px_00", "ask_px_00"]])
            print(f"  pulled {d}: {len(df)} rows / {len(by_day[d])} contracts", flush=True)
        except Exception as e:
            print(f"  PULL FAILED {d}: {e}", flush=True)
    if pulled:
        newdf = pd.concat(pulled, ignore_index=True)
        allnew = pd.concat([pd.read_parquet(NEWQ), newdf]) if NEWQ.exists() else newdf
        allnew.drop_duplicates(["ts_event", "symbol"]).to_parquet(NEWQ)
        s2 = 1e9 if newdf["ask_px_00"].abs().median() > 1e6 else 1.0
        newdf = newdf.assign(ask=newdf["ask_px_00"] / s2, bid=newdf["bid_px_00"] / s2,
                             t=pd.to_datetime(newdf["ts_event"], utc=True))
        q = pd.concat([q, newdf[(newdf.ask > 0) & (newdf.bid > 0)]], ignore_index=True)

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

# ---- price ----
SHIFT = pd.Timedelta(minutes=5)
rows = []
for name in sig:
    for t in sig[name]:
        cp = "C" if t.direction.startswith("L") else "P"
        rec = dict(profile=name, day=t.day, dir=t.direction, reason=t.reason,
                   share_pnl_u=round(t.pnl_total, 3))
        for tag, w in (("dte0", False), ("wk", True)):
            s, dte = pick(t.day, t.entry_price, cp, w)
            rec[f"{tag}_sym"], rec[f"{tag}_dte"] = s, dte
            for conv, off in (("bs", pd.Timedelta(0)), ("bc", SHIFT)):
                a = qv(s, t.entry_ts + off, "ask") if s else None
                b = qv(s, t.exit_ts + off, "bid") if s else None
                rec[f"{tag}_{conv}"] = round((b - a) * 100, 2) if (a is not None and b is not None) else None
        rows.append(rec)

led = pd.DataFrame(rows)
led.to_csv(OUT, index=False)
led["year"] = led.day.str[:4]

print("\n=== TSLA options 1 ct — barstart (lookahead, as published) vs barclose (realistic) ===", flush=True)
for tag, lab in (("dte0", "0DTE"), ("wk", "WEEKLY")):
    print(f"\n--- {lab} ---")
    print(f"{'profile':<5}{'n':>5}{'miss':>6}{'2025 bs':>10}{'2025 bc':>10}{'2026 bs':>10}{'2026 bc':>10}{'TOT bs':>10}{'TOT bc':>10}")
    for name in sig:
        L = led[led.profile == name]
        miss = int(L[f"{tag}_bc"].isna().sum())
        f = lambda yr, c: L[(L.year == yr)][f"{tag}_{c}"].sum()
        print(f"{name:<5}{len(L):>5}{miss:>6}{f('2025','bs'):>10.0f}{f('2025','bc'):>10.0f}"
              f"{f('2026','bs'):>10.0f}{f('2026','bc'):>10.0f}"
              f"{L[f'{tag}_bs'].sum():>10.0f}{L[f'{tag}_bc'].sum():>10.0f}", flush=True)
print(f"\nledger -> {OUT}", flush=True)
