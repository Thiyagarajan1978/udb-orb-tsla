import sys, datetime as dt, os
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src"); os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd, numpy as np
from udb_orb.config import load_config
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.engine.orb_engine import run_engine
from udb_orb.engine.params import Params
SC=r"data/cache/spx"
H=SC+r"\spx_5m_2024_2025.parquet"
if os.path.exists(H):
    hist=pd.read_parquet(H); print("hist cached",len(hist),flush=True)
else:
    print("fetching ^GSPC 2024-01-01 -> 2025-11-01 ...",flush=True)
    hist=rth_only(fetch_5min("^GSPC", dt.date(2024,1,1), dt.date(2025,11,1))); hist.to_parquet(H)
    print("fetched",len(hist),flush=True)
recent=pd.read_parquet(SC+r"\spx_5m.parquet")
spx=pd.concat([hist,recent]).sort_index(); spx=spx[~spx.index.duplicated(keep="last")]
print("SPX full frame:",spx.index[0],"->",spx.index[-1],len(spx),flush=True)
def orw(df,nb,lo,hi):
    d=df[(df.index.strftime("%Y-%m-%d")>=lo)&(df.index.strftime("%Y-%m-%d")<=hi)].copy(); d["day"]=d.index.strftime("%Y-%m-%d")
    return d.groupby("day").apply(lambda g: g.head(nb)["high"].max()-g.head(nb)["low"].min()).median()
TSLA_OR=4.07
SCALE=["fixed_sl","adaptive_tp_min","fixed_tp","reversal_target","reversal_risk_cap","be_trail_amount","atr_tp_min","min_or_width","max_or_width"]
def scfg(base,or_bars,ratio):
    c=load_config(base); p=c["profile"]
    for k in SCALE:
        if k in p and p[k]: p[k]=round(p[k]*ratio,2)
    c["enhancements"]["volatility_regime"]["max_rvol_pct"]=99.0
    c.setdefault("session",{})["or_bars"]=or_bars
    return c
def stat(TT):
    if not TT: return "     0 tr"
    net=sum(t.pnl_total for t in TT); w=sum(1 for t in TT if t.pnl_total>0)
    gw=sum(t.pnl_total for t in TT if t.pnl_total>0); gl=sum(t.pnl_total for t in TT if t.pnl_total<=0)
    return f"{len(TT):>4}tr PF{gw/abs(gl) if gl else 9:>5.2f} net{net:>+8.0f}pt WR{100*w/len(TT):>3.0f}%"
print("\n=== SPX 30m-OR: TRAIN (2024-2025) vs HOLDOUT (2026), scale from TRAIN OR-width ===",flush=True)
for base,n in [("config/tsla_best_B.yaml","B1"),("config/tsla_config_C.yaml","C2"),("config/tsla_best_A.yaml","A1")]:
    print(f"\n{n}:  {'':<16}{'2024 (OOS)':<28}{'2025 (OOS)':<28}{'2026 (disc.)'}",flush=True)
    for tag,ob,nb in [("5m",1,1),("15m",3,3),("30m",6,6)]:
        ratio=orw(spx,nb,"2024-01-01","2025-12-31")/TSLA_OR   # scale from train only
        T=run_engine(spx,Params.from_config(scfg(base,ob,ratio)),scfg(base,ob,ratio)["enhancements"]).trades
        y24=[t for t in T if t.day[:4]=="2024"]; y25=[t for t in T if t.day[:4]=="2025"]; y26=[t for t in T if t.day[:4]=="2026"]
        print(f"   {tag:<4}(x{ratio:>4.1f}) {stat(y24):<28}{stat(y25):<28}{stat(y26)}",flush=True)
print("\nDONE",flush=True)
