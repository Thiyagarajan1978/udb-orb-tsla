"""A1 vs B1, 2024-2026: stocks @100sh and options @2ct (ATM 0DTE, barclose fills).

2025-26 options come from the existing barclose ledger. 2024 contracts are
reconstructed: expiry per day from the cached 2024 quote symbols (3-bot pulls,
fallback = next Mon/Wed/Fri), strike = nearest $2.50 to entry; missing quotes
pulled from Databento in monthly batches and cached.
"""
import os, re, sys, datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(r"C:\Users\TT\udb-orb-tsla")
sys.path.insert(0, str(REPO / "src"))
import pandas as pd
from udb_orb.config import load_config
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

CACHE = REPO / "data" / "cache" / "opra"
GAP24 = CACHE / "quotes_2024_udb_gaps.parquet"
SCRATCH = Path(r"C:\Users\TT\AppData\Local\Temp\claude\c--Users-TT-udb-orb-tsla\8586d0e4-ef89-405e-a887-ca05ac5468af\scratchpad")
QTY_SH, QTY_CT = 100, 2


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
    sys.exit("no key")


def parse_osi(sym):
    b = sym[-15:]
    return dt.date(2000 + int(b[0:2]), int(b[2:4]), int(b[4:6])), b[6], int(b[7:15]) / 1000


# ---- signals ----
bars = rth_only(fetch_5min("TSLA", dt.date(2023, 10, 1), dt.date(2026, 7, 17),
                           cache_dir=REPO / "data" / "cache"))
print(f"bars {bars.index.min().date()}..{bars.index.max().date()}", flush=True)
sig = {}
for prof, cfgp in (("A1", "config/tsla_best_A.yaml"), ("B1", "config/tsla_best_B.yaml")):
    cfg = load_config(REPO / cfgp)
    tr = run_engine(bars.copy(), Params.from_config(cfg), cfg["enhancements"]).trades
    sig[prof] = [t for t in tr if "2024-01-01" <= t.day <= "2026-07-17"]
    print(f"{prof}: {len(sig[prof])} trades", flush=True)

# ---- 2024 expiry calendar from cached quote symbols ----
q24 = pd.concat([pd.read_parquet(CACHE / f"quotes_2024_{i}.parquet") for i in (1, 2, 3)],
                ignore_index=True)
day_exp = defaultdict(set)
for s in q24["symbol"].unique():
    e, _, _ = parse_osi(s)
    day_exp_key = None  # symbol quotes appear on days <= exp; map via quote dates below
sym_days = q24.assign(t=pd.to_datetime(q24.ts_event, utc=True, errors="coerce")).dropna(subset=["t"])
sym_days["d"] = sym_days["t"].dt.tz_convert("America/New_York").dt.date
for (s, d) in sym_days[["symbol", "d"]].drop_duplicates().itertuples(index=False):
    if pd.isna(d):
        continue
    e, _, _ = parse_osi(s)
    if e >= d:
        day_exp[str(d)].add(e)

def exp_for(day):
    D = dt.date.fromisoformat(day)
    if day in day_exp:
        return min(e for e in day_exp[day] if e >= D)
    d = D  # fallback: next Mon/Wed/Fri
    while d.weekday() not in (0, 2, 4):
        d += dt.timedelta(days=1)
    return d

def osi24(day, cp, price):
    e = exp_for(day)
    strike = round(price / 2.5) * 2.5
    return f"TSLA  {e:%y%m%d}{cp}{int(round(strike * 1000)):08d}"

# ---- quote store: all caches ----
parts = [q24]
for f in ("quotes_weekly.parquet", "quotes_full.parquet", "quotes_july.parquet",
          "quotes_barclose_fill_gaps.parquet"):
    p = CACHE / f
    if p.exists():
        parts.append(pd.read_parquet(p))
if GAP24.exists():
    parts.append(pd.read_parquet(GAP24))
q = pd.concat(parts, ignore_index=True).drop_duplicates(["ts_event", "symbol"])
scale = 1e9 if q["ask_px_00"].abs().median() > 1e6 else 1.0
q["ask"] = q["ask_px_00"] / scale
q["bid"] = q["bid_px_00"] / scale
q["t"] = pd.to_datetime(q["ts_event"], utc=True)
q = q[(q.ask > 0) & (q.bid > 0)]

# ---- 2024 needs + gap pull (entries shared A1/B1 -> use union) ----
need = defaultdict(set)
for prof in sig:
    for t in sig[prof]:
        if t.day >= "2025-01-01":
            continue
        cp = "C" if t.direction.startswith("L") else "P"
        need[osi24(t.day, cp, t.entry_price)].add(t.day)
have_days = {s: set(g["t"].dt.tz_convert("America/New_York").dt.date.astype(str).unique())
             for s, g in q.groupby("symbol")}
missing = defaultdict(set)
for s, ds in need.items():
    for d in ds:
        if d not in have_days.get(s, set()):
            missing[d[:7]].add(s)
n_missing = sum(len(v) for v in missing.values())
print(f"2024 contract-days needed {sum(len(v) for v in need.values())}, missing {n_missing}", flush=True)

if n_missing:
    import databento as dbnt
    cl = dbnt.Historical(get_db_key())
    pulled = []
    for mon in sorted(missing):
        start = f"{mon}-01"
        endd = (dt.date.fromisoformat(start) + dt.timedelta(days=40)).replace(day=1).isoformat()
        try:
            df = cl.timeseries.get_range(dataset="OPRA.PILLAR", symbols=sorted(missing[mon]),
                                         stype_in="raw_symbol", schema="cbbo-1m",
                                         start=start, end=endd).to_df().reset_index()
            pulled.append(df[["ts_event", "symbol", "bid_px_00", "ask_px_00"]])
            print(f"  pulled {mon}: {len(df)} rows / {len(missing[mon])} contracts", flush=True)
        except Exception as e:
            print(f"  PULL FAILED {mon}: {e}", flush=True)
    if pulled:
        nd = pd.concat(pulled, ignore_index=True)
        allnew = pd.concat([pd.read_parquet(GAP24), nd]) if GAP24.exists() else nd
        allnew.drop_duplicates(["ts_event", "symbol"]).to_parquet(GAP24)
        s2 = 1e9 if nd["ask_px_00"].abs().median() > 1e6 else 1.0
        nd = nd.assign(ask=nd["ask_px_00"] / s2, bid=nd["bid_px_00"] / s2,
                       t=pd.to_datetime(nd["ts_event"], utc=True))
        q = pd.concat([q, nd[(nd.ask > 0) & (nd.bid > 0)]], ignore_index=True)

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

# ---- option pnl per trade: 2024 priced here; 2025-26 from the barclose ledger ----
led = pd.read_csv(SCRATCH / "tsla_barclose_ledger.csv")
BC = pd.Timedelta(minutes=5)
opt = {p: {} for p in sig}   # profile -> {(day, entry_hhmm, dir): pnl_1ct}
n_unpriced = Counter()
for prof in sig:
    for t in sig[prof]:
        key = (t.day, t.entry_ts.strftime("%H:%M"), t.direction)
        if t.day >= "2025-01-01":
            continue
        cp = "C" if t.direction.startswith("L") else "P"
        s = osi24(t.day, cp, t.entry_price)
        a = qv(s, t.entry_ts + BC, "ask")
        b = qv(s, t.exit_ts + BC, "bid")
        if a is None or b is None:
            n_unpriced[prof] += 1
            continue
        opt[prof][key] = (b - a) * 100
print(f"2024 unpriced: {dict(n_unpriced)}", flush=True)

# ---- aggregate per year ----
def yr_stats_stocks(trs, year):
    T = [t for t in trs if t.day[:4] == year]
    w = [t.pnl_total for t in T if t.pnl_total > 0]
    l = [t.pnl_total for t in T if t.pnl_total <= 0]
    pf = (sum(w) / -sum(l)) if l and sum(l) < 0 else float("inf")
    return sum(w + l) * QTY_SH, pf, (100 * len(w) / len(T)) if T else 0, len(T)

def yr_stats_opt(prof, year):
    vals = []
    if year == "2024":
        vals = [v for (d, _, _), v in opt[prof].items() if d[:4] == year]
    else:
        L = led[(led.profile == prof) & (led.day.str[:4] == year)]
        vals = list(L["dte0_bc"].dropna())
    vals = [v * QTY_CT for v in vals]
    w = [v for v in vals if v > 0]
    l = [v for v in vals if v <= 0]
    pf = (sum(w) / -sum(l)) if l and sum(l) < 0 else float("inf")
    return sum(vals), pf, (100 * len(w) / len(vals)) if vals else 0, len(vals)

print("\n=== A1 vs B1 — stocks @100sh | options ATM 0DTE @2ct barclose ===", flush=True)
print(f"{'year':<6}" + "".join(f"{p+' stk net':>12}{'PF':>6}{p+' opt net':>12}{'PF':>6}" for p in ("A1", "B1")))
for year in ("2024", "2025", "2026"):
    row = f"{year:<6}"
    for prof in ("A1", "B1"):
        sn, spf, swr, s_n = yr_stats_stocks(sig[prof], year)
        on, opf, owr, o_n = yr_stats_opt(prof, year)
        row += f"{sn:>+12,.0f}{spf:>6.2f}{on:>+12,.0f}{opf:>6.2f}"
    print(row, flush=True)
for prof in ("A1", "B1"):
    for year in ("2024", "2025", "2026"):
        sn, spf, swr, s_n = yr_stats_stocks(sig[prof], year)
        on, opf, owr, o_n = yr_stats_opt(prof, year)
        print(f"{prof} {year}: stk n={s_n} wr={swr:.1f}  opt n={o_n} wr={owr:.1f}", flush=True)
