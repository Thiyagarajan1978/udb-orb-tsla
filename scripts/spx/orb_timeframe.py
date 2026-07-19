import sys, os, copy
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src")
os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd
from collections import defaultdict
from udb_orb.config import db_path, load_config
from udb_orb.data.fmp_client import rth_only
from udb_orb.db.database import Database
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params

cfg = load_config()
with Database(db_path(cfg)) as db:
    b5 = rth_only(db.load_bars(cfg["symbol"]))

def resample(df, n):   # group n consecutive 5m bars per day -> 15m (n=3), 30m (n=6)
    rows=[]
    for day, g in df.groupby(df.index.date):
        g=g.sort_index()
        for i in range(0, len(g), n):
            c=g.iloc[i:i+n]
            if len(c)==0: continue
            rows.append((c.index[0], c["open"].iloc[0], c["high"].max(), c["low"].min(), c["close"].iloc[-1], c["volume"].sum()))
    return pd.DataFrame({"open":[r[1] for r in rows],"high":[r[2] for r in rows],"low":[r[3] for r in rows],
                         "close":[r[4] for r in rows],"volume":[r[5] for r in rows]},
                        index=pd.DatetimeIndex([r[0] for r in rows]))

frames={"5m (current)":b5, "15m":resample(b5,3), "30m":resample(b5,6)}
def cfgB():
    c=copy.deepcopy(cfg); c["enhancements"]["runner_trail"]["enabled"]=False; return c   # Config B (VWAP runner)

def stats(df,c):
    segs={y:df[[d.year==y for d in df.index.date]] for y in (2024,2025,2026)}
    tot=0; tw=0; tt=0; wst=0; gw=gl=0
    for y in (2024,2025,2026):
        r=run_engine(segs[y],Params.from_config(c),c["enhancements"]); T=r.trades
        if not T: continue
        gw+=sum(t.pnl_total for t in T if t.pnl_total>0); gl+=sum(t.pnl_total for t in T if t.pnl_total<=0)
        dn=defaultdict(float)
        for t in T: dn[t.day]+=t.pnl_total
        tot+=sum(t.pnl_total for t in T); tt+=len(T); tw+=sum(1 for t in T if t.pnl_total>0); wst=min(wst,min(dn.values()))
    return tot, 100*tw/tt if tt else 0, tt, (gw/abs(gl) if gl else 99), wst

print("ORB timeframe test (2.5yr, TSLA) — OR = first bar of the day at each resolution\n")
for label,base in [("CONFIG A (peak-trail)",cfg),("CONFIG B (VWAP runner)",cfgB())]:
    print(f"=== {label} ===")
    print(f"{'OR timeframe':<14}{'trades':>7}{'WR%':>7}{'3yr net':>9}{'PF':>6}{'worst':>8}")
    for tf,df in frames.items():
        net,wr,tr,pf,wst=stats(df, base if label.startswith('CONFIG A') else base)
        print(f"{tf:<14}{tr:>7}{wr:>6.1f}%{net:>+9.0f}{pf:>6.2f}{wst:>+8.1f}")
    print()
