import sys, datetime as dt, os, glob
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src"); os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd, numpy as np
SC=r"data/cache/spx"
spx=pd.concat([pd.read_parquet(SC+r"\spx_5m_2024_2025.parquet"),pd.read_parquet(SC+r"\spx_5m.parquet")]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]; spx["day"]=spx.index.strftime("%Y-%m-%d")
def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
tmap=[]
for day,g in spx.groupby("day"):
    g=g.reset_index(); tcol=g.columns[0]
    if len(g)<5: continue
    orb=g.iloc[:3]; oh=orb["high"].max(); ol=orb["low"].min(); w=oh-ol
    if w<=0: continue
    lt=oh+0.1*w; st=ol-0.1*w; ent=None
    for i in range(3,len(g)-1):
        c=g.iloc[i]["close"]
        if c>lt: ent=(g.iloc[i][tcol],c,1); break
        if c<st: ent=(g.iloc[i][tcol],c,-1); break
    if ent is None: continue
    ets,E,d=ent; exp=dt.date.fromisoformat(day); cp="C" if d==1 else "P"
    cands=[(round(E*(1+pct/100*d)/5)*5, osi(exp,cp,round(E*(1+pct/100*d)/5)*5)) for pct in np.arange(0.20,0.75,0.05)]
    tmap.append((day,ets,E,d,g.iloc[-1]["close"],cands))
q=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(SC+r"\chunk*.parquet"))],ignore_index=True)
sc=1e9 if q["ask_px_00"].abs().median()>1e6 else 1.0
q["ask"]=q["ask_px_00"]/sc; q["bid"]=q["bid_px_00"]/sc; q["t"]=pd.to_datetime(q["ts_event"],utc=True)
byS={s:g.sort_values("t").reset_index(drop=True) for s,g in q[q.ask>0].groupby("symbol")}
TARGET=0.40; res=[]
for day,ets,E,d,eod,cands in tmap:
    et=pd.Timestamp(ets).tz_convert("UTC"); best=None
    for k,s in cands:
        g=byS.get(s)
        if g is None: continue
        pre=g[g.t<=et]
        if not len(pre): continue
        a=pre["ask"].iloc[-1]
        if a and a>0 and (best is None or abs(a-0.65)<abs(best[2]-0.65)): best=(k,s,a)
    if best is None or not (0.30<=best[2]<=1.20): continue
    k,s,ea=best; post=byS[s][byS[s].t>et]; exitp=None
    for _,r in post.iterrows():
        if r["bid"]>=ea+TARGET: exitp=ea+TARGET; break
    if exitp is None: exitp=max(0.0,(eod-k) if d==1 else (k-eod))
    res.append((day,ea,exitp,exitp-ea))
r=pd.DataFrame(res,columns=["day","entry","exit","pnl"]); r["yr"]=r.day.str[:4]
r.to_csv(SC+r"\spx_cheap0dte_trades.csv",index=False)
print(f"priced {len(r)} trades\n=== CHEAP SPX 0DTE (buy ~$0.65 OTM on ORB breakout, exit +$0.40 or expire) ===")
print(f"{'Year':<6}{'#tr':>5}{'WR':>6}{'net/ct':>9}{'perContract$':>13}{'avgWin':>8}{'avgLoss':>9}{'if 10ct(like her)':>18}")
for y in ["2024","2025","2026","ALL"]:
    s=r if y=="ALL" else r[r.yr==y]
    if not len(s): continue
    w=s[s.pnl>0]; l=s[s.pnl<=0]
    print(f"{y:<6}{len(s):>5}{100*len(w)/len(s):>5.0f}%{s.pnl.mean():>+9.2f}{s.pnl.sum()*100:>+13,.0f}{(w.pnl.mean() if len(w) else 0):>+8.2f}{(l.pnl.mean() if len(l) else 0):>+9.2f}{s.pnl.sum()*100*10:>+18,.0f}")
print("\nnet/ct = per-share; perContract = x100; last col = total $ at 10 contracts (her screenshot size)")
