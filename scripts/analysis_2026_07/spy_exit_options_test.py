"""SPY cross-symbol test of the runner-exit variants, on SHARES and REAL OPTIONS.

B1 config ported to SPY by scaling every dollar param by the 2026 ATR14 ratio
(SPY/TSLA = 0.541). 2026 signals priced against Databento OPRA cbbo-1m quotes
(buy-ask / sell-bid, ATM 0DTE — SPY lists an expiry every trading day, $1 strikes).
Contract symbols constructed directly (no definitions pull needed).
"""
import copy, os, re, sys, datetime as dt
from collections import Counter
from pathlib import Path

REPO = Path(r"C:\Users\TT\udb-orb-tsla")
sys.path.insert(0, str(REPO / "src"))
import pandas as pd
from udb_orb.config import load_config
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

SCALE = 0.541
OUT = Path(r"C:\Users\TT\AppData\Local\Temp\claude\c--Users-TT-udb-orb-tsla\8586d0e4-ef89-405e-a887-ca05ac5468af\scratchpad\spy_exit_options_ledger.csv")


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
    sys.exit("DATABENTO_API_KEY not found")


# ---- SPY config: B1 with ATR-scaled dollar params ----
base = load_config(REPO / "config" / "tsla_best_B.yaml")
base["symbol"] = "SPY"
prof = base["profile"]
for key in ("fixed_sl", "adaptive_tp_min", "fixed_tp", "partial_activation",
            "reversal_target", "reversal_risk_cap", "max_or_width"):
    prof[key] = round(prof[key] * SCALE, 2)
prof["be_trail_amount"] = round(prof["be_trail_amount"] * SCALE, 3)
prof["slippage_per_unit"] = 0.05
# vol gate threshold is TSLA-calibrated (4.92% daily vol) — never binds on SPY (~1%): gate inert.

def variant(name, **enh_over):
    cfg = copy.deepcopy(base)
    for block, kv in enh_over.items():
        cfg["enhancements"].setdefault(block, {}).update(kv)
    return name, cfg

VARIANTS = [
    variant("B1-style VWAP cross"),
    variant("peak-trail 0.75xOR"),
    variant("chandelier 0.25xATR", runner_trail={"enabled": True, "mode": "atr", "atr_mult": 0.25}),
    variant("chandelier 0.35xATR", runner_trail={"enabled": True, "mode": "atr", "atr_mult": 0.35}),
]
VARIANTS[1][1]["enhancements"]["runner_trail"].update({"enabled": True, "or_mult": 0.75})

# ---- bars + signals ----
bars = rth_only(fetch_5min("SPY", dt.date(2025, 10, 1), dt.date(2026, 7, 17), cache_dir=REPO / "data" / "cache"))
print(f"SPY bars {bars.index.min().date()}..{bars.index.max().date()}")

sigs = {}
for name, cfg in VARIANTS:
    tr = run_engine(bars.copy(), Params.from_config(cfg), cfg["enhancements"]).trades
    sigs[name] = [t for t in tr if t.day >= "2026-01-01"]

print("\n=== SPY 2026 SHARES (per 1 unit) ===")
print(f"{'variant':<24}{'n':>4}{'net':>9}{'WR%':>7}{'PF':>6}{'worst d':>9}")
for name, _ in VARIANTS:
    T = sigs[name]
    net = sum(t.pnl_total for t in T)
    wins = [t.pnl_total for t in T if t.pnl_total > 0]
    gl = -sum(t.pnl_total for t in T if t.pnl_total <= 0)
    dp = Counter()
    for t in T:
        dp[t.day] += t.pnl_total
    pf = sum(wins) / gl if gl else float("inf")
    print(f"{name:<24}{len(T):>4}{net:>9.2f}{100*len(wins)/len(T):>7.1f}{pf:>6.2f}{min(dp.values()):>9.2f}")

# ---- options pricing (Databento OPRA, ATM 0DTE, constructed OSI symbols) ----
import databento as dbnt
cl = dbnt.Historical(get_db_key())
rng_end = pd.to_datetime(cl.metadata.get_dataset_range("OPRA.PILLAR")["end"])
last_day = None

def osi(day, cp, strike):
    d = dt.date.fromisoformat(day)
    return f"SPY   {d:%y%m%d}{cp}{int(round(strike * 1000)):08d}"

need = set()
for name, _ in VARIANTS:
    for t in sigs[name]:
        close_utc = pd.Timestamp(t.day + " 16:00", tz="America/New_York").tz_convert("UTC")
        if close_utc > rng_end:
            continue
        cp = "C" if t.direction.startswith("L") else "P"
        need.add(osi(t.day, cp, round(t.entry_price)))
print(f"\nContracts needed: {len(need)} (OPRA available through {rng_end.date()})")

days_span = sorted({s[6:12] for s in need})
start = f"20{days_span[0][:2]}-{days_span[0][2:4]}-{days_span[0][4:6]}"
end = (dt.date(2000 + int(days_span[-1][:2]), int(days_span[-1][2:4]), int(days_span[-1][4:6]))
       + dt.timedelta(days=1)).isoformat()
QCACHE = OUT.parent / "spy_quotes_cache.parquet"
if QCACHE.exists():
    q = pd.read_parquet(QCACHE)
    print(f"quotes from cache: {QCACHE}")
else:
    q = cl.timeseries.get_range(dataset="OPRA.PILLAR", symbols=sorted(need), stype_in="raw_symbol",
                                schema="cbbo-1m", start=start, end=end).to_df().reset_index()
    q.to_parquet(QCACHE)
scale = 1e9 if q["ask_px_00"].abs().median() > 1e6 else 1.0
q["ask"] = q["ask_px_00"] / scale
q["bid"] = q["bid_px_00"] / scale
q["t"] = pd.to_datetime(q["ts_event"], utc=True)
q = q[(q.ask > 0) & (q.bid > 0)]
quotes = {s: g.sort_values("t").reset_index(drop=True) for s, g in q.groupby("symbol")}
print(f"Quotes pulled: {len(q)} rows, {len(quotes)} contracts with data")

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

rows = []
SHIFT = pd.Timedelta(minutes=5)
# barstart = quote at the signal bar's index ts (forward_test.py's convention — LOOKAHEAD:
# the alert only exists once the bar CLOSES). barclose = quote at ts+5m = realistic alert fill.
for conv, off in (("barstart", pd.Timedelta(0)), ("barclose", SHIFT)):
    print(f"\n=== SPY 2026 OPTIONS, ATM 0DTE, 1 ct, buy-ask/sell-bid — {conv} fills ===")
    print(f"{'variant':<24}{'priced':>7}{'unpriced':>9}{'net $':>9}{'win%':>7}{'avg win':>9}{'avg loss':>9}")
    for name, _ in VARIANTS:
        pnl, miss = [], 0
        for t in sigs[name]:
            close_utc = pd.Timestamp(t.day + " 16:00", tz="America/New_York").tz_convert("UTC")
            if close_utc > rng_end:
                continue
            cp = "C" if t.direction.startswith("L") else "P"
            sym = osi(t.day, cp, round(t.entry_price))
            a = qv(sym, t.entry_ts + off, "ask")
            b = qv(sym, t.exit_ts + off, "bid")
            if a is None or b is None:
                miss += 1
                continue
            p = (b - a) * 100
            pnl.append(p)
            rows.append(dict(conv=conv, variant=name, day=t.day, dir=t.direction, reason=t.reason,
                             entry=(t.entry_ts + off).strftime("%H:%M"), exit=(t.exit_ts + off).strftime("%H:%M"),
                             sym=sym, ask_in=a, bid_out=b, opt_pnl_1ct=round(p, 2),
                             share_pnl_u=round(t.pnl_total, 3)))
        w = [x for x in pnl if x > 0]
        lo = [x for x in pnl if x <= 0]
        print(f"{name:<24}{len(pnl):>7}{miss:>9}{sum(pnl):>9.0f}{100*len(w)/len(pnl) if pnl else 0:>7.1f}"
              f"{(sum(w)/len(w)) if w else 0:>9.0f}{(sum(lo)/len(lo)) if lo else 0:>9.0f}")

pd.DataFrame(rows).to_csv(OUT, index=False)
print(f"\nledger -> {OUT}")
