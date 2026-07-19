import sys, re, datetime as dt, os
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src"); os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd, numpy as np
import databento as db
SC=r"data/cache/spx"
spx=pd.concat([pd.read_parquet(SC+r"\spx_5m_2024_2025.parquet"),pd.read_parquet(SC+r"\spx_5m.parquet")]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]; spx["day"]=spx.index.strftime("%Y-%m-%d")
# 1) ORB 15m breakouts (direction, entry ts, entry level) + EOD close
trades=[]
for day,g in spx.groupby("day"):
    g=g.reset_index(); tcol=g.columns[0]
    if len(g)<5: continue
    orb=g.iloc[:3]; oh=orb["high"].max(); ol=orb["low"].min(); w=oh-ol
    if w<=0: continue
    lt=oh+0.1*w; st=ol-0.1*w
    for i in range(3,len(g)-1):
        c=g.iloc[i]["close"]
        if c>lt: trades.append((day,g.iloc[i][tcol],c,1,g.iloc[-1]["close"])); break
        if c<st: trades.append((day,g.iloc[i][tcol],c,-1,g.iloc[-1]["close"])); break
print(f"ORB breakouts 2024-2026: {len(trades)}",flush=True)
# 2) construct OTM-band SPXW 0DTE symbols per trade (~$0.65 lives ~0.2-0.7% OTM)
def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
need=set(); tmap=[]
for day,ets,E,d,eod in trades:
    exp=dt.date.fromisoformat(day); cp="C" if d==1 else "P"
    cands=[]
    for pct in np.arange(0.20,0.75,0.05):
        k=round(E*(1+pct/100*d)/5)*5; s=osi(exp,cp,k); need.add(s); cands.append((k,s))
    tmap.append((day,ets,E,d,eod,cp,cands))
print(f"constructed {len(need)} SPXW symbols",flush=True)
key=[re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)',l).group(1) for l in open(r"C:\Users\TT\gap_analyzer\.env") if l.startswith("DATABENTO_API_KEY")][0]
cl=db.Historical(key)
QF=SC+r"\spxw_cheap_quotes.parquet"
if os.path.exists(QF):
    q=pd.read_parquet(QF); print("quotes cached",len(q),flush=True)
else:
    frames=[]
    for m in [f"{y}-{i:02d}" for y in (2024,2025) for i in range(1,13)]+[f"2026-{i:02d}" for i in range(1,8)]:
        y,mm=m.split("-"); nm=(dt.date(int(y),int(mm),1)+dt.timedelta(days=32)).replace(day=1)
        end=min(nm, dt.date(2026,7,17))
        msyms=sorted({s for (day,ets,E,d,eod,cp,cands) in tmap if day[:7]==m for k,s in cands})
        if not msyms: continue
        try:
            qq=cl.timeseries.get_range(dataset="OPRA.PILLAR",symbols=msyms,stype_in="raw_symbol",schema="cbbo-1m",start=f"{m}-01",end=end.isoformat()).to_df().reset_index()
            frames.append(qq[["ts_event","symbol","bid_px_00","ask_px_00"]]); print("q",m,len(qq),flush=True)
        except Exception as e: print("q err",m,str(e)[:45],flush=True)
    q=pd.concat(frames,ignore_index=True); q.to_parquet(QF); print("saved",len(q),flush=True)
sc=1e9 if q["ask_px_00"].abs().median()>1e6 else 1.0
q["ask"]=q["ask_px_00"]/sc; q["bid"]=q["bid_px_00"]/sc; q["t"]=pd.to_datetime(q["ts_event"],utc=True)
byS={s:g.sort_values("t").reset_index(drop=True) for s,g in q[(q.ask>0)].groupby("symbol")}
def series(s): return byS.get(s)
# 3) price each trade: pick ask~0.65 at entry, exit +$0.40 target else EOD bid (or 0)
TARGET=0.40; res=[]
for day,ets,E,d,eod,cp,cands in tmap:
    et=pd.Timestamp(ets).tz_convert("UTC")
    best=None
    for k,s in cands:
        g=series(s)
        if g is None: continue
        pre=g[g.t<=et]
        if not len(pre): continue
        a=pre["ask"].iloc[-1]
        if a is None or a<=0: continue
        if best is None or abs(a-0.65)<abs(best[2]-0.65): best=(k,s,a)
    if best is None or not (0.30<=best[2]<=1.20): continue   # must be a 'cheap' ticket
    k,s,ea=best; g=series(s); post=g[g.t>et]
    exitp=None
    for _,r in post.iterrows():
        if r["bid"]>=ea+TARGET: exitp=ea+TARGET; break   # small-profit target hit
    if exitp is None:   # EOD: settle at intrinsic (cash), OTM->0
        intr=max(0.0,(eod-k) if d==1 else (k-eod))
        exitp=intr
    res.append((day,ea,exitp,exitp-ea))
r=pd.DataFrame(res,columns=["day","entry","exit","pnl"]); r["yr"]=r.day.str[:4]
print(f"\n=== CHEAP SPX 0DTE (buy ~$0.65 OTM on ORB breakout, exit +$0.40 or expire) ===",flush=True)
print(f"{'Year':<6}{'#tr':>5}{'WR':>6}{'net/ct$':>10}{'x100':>10}{'avgWin':>9}{'avgLoss':>9}{'total x100 x10ct':>18}",flush=True)
for y in ["2024","2025","2026","ALL"]:
    s=r if y=="ALL" else r[r.yr==y]
    if not len(s): continue
    w=s[s.pnl>0]; l=s[s.pnl<=0]
    print(f"{y:<6}{len(s):>5}{100*len(w)/len(s):>5.0f}%{s.pnl.mean():>+10.2f}{s.pnl.sum()*100:>+10.0f}{(w.pnl.mean() if len(w) else 0):>+9.2f}{(l.pnl.mean() if len(l) else 0):>+9.2f}{s.pnl.sum()*100*10:>+18,.0f}",flush=True)
print("\n(net/ct = per-share option P&L; x100 = per contract; last col = total if 10 contracts like her screenshot)",flush=True)
print("DONE",flush=True)
