import sys, os, copy, datetime as dt
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src")
os.chdir(r"C:\Users\TT\udb-orb-tsla")
from collections import defaultdict
from pathlib import Path
from udb_orb.config import db_path, load_config
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.db.database import Database
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

cfg = load_config()
SYMS = ["TSLA", "NVDA", "AMD", "META", "AMZN", "GOOGL", "MSFT", "NFLX"]
start, end = dt.date(2024, 1, 2), dt.date(2026, 7, 9)
cache = Path("data/cache")

def get_bars(sym):
    if sym == "TSLA":
        with Database(db_path(cfg)) as db:
            return rth_only(db.load_bars(sym))
    df = rth_only(fetch_5min(sym, start, end, cache_dir=cache))
    return df

def stats(seg, sym):
    c = copy.deepcopy(cfg); c["symbol"] = sym
    r = run_engine(seg, Params.from_config(c), c["enhancements"])
    T = r.trades
    if not T: return None
    wins = [t for t in T if t.pnl_total > 0]
    dn = defaultdict(float)
    for t in T: dn[t.day] += t.pnl_total
    rs = [t.pnl_total / t.risk_amount for t in T if t.risk_amount]
    net = sum(t.pnl_total for t in T)
    return dict(tr=len(T), wr=100*len(wins)/len(T), net=net,
                avgR=sum(rs)/len(rs) if rs else 0, totR=sum(rs),
                worst=min(dn.values()), px=float(seg["close"].iloc[-1]))

print(f"{'sym':<7}{'avgPx':>7}{'trades':>7}{'WR%':>6}{'avgR':>7}{'totR':>8}{'net$/u':>9}{'worstDay':>9}", flush=True)
print("-"*62, flush=True)
for sym in SYMS:
    try:
        b = get_bars(sym)
        seg = b[[(start <= d <= end) for d in b.index.date]]
        s = stats(seg, sym)
        if s is None:
            print(f"{sym:<7} no trades", flush=True); continue
        print(f"{sym:<7}{s['px']:>7.0f}{s['tr']:>7}{s['wr']:>6.1f}{s['avgR']:>+7.2f}{s['totR']:>+8.1f}{s['net']:>+9.1f}{s['worst']:>+9.1f}", flush=True)
    except Exception as e:
        print(f"{sym:<7} ERROR {str(e)[:80]}", flush=True)
print("\nNOTE: dollar params (TP floor, max-cap, reversal cap, OR-gate $8) are TSLA-scaled;", flush=True)
print("avgR and WR are the price-neutral generalization metrics.", flush=True)
