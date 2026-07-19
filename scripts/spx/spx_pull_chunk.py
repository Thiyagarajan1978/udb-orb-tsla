import sys, re, datetime as dt, os
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src"); os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd, numpy as np, databento as db
SC=r"data/cache/spx"
MONTHS=set(sys.argv[1].split(",")); OUT=sys.argv[2]
if os.path.exists(OUT): print("skip (exists)",OUT); sys.exit()
spx=pd.concat([pd.read_parquet(SC+r"\spx_5m_2024_2025.parquet"),pd.read_parquet(SC+r"\spx_5m.parquet")]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]; spx["day"]=spx.index.strftime("%Y-%m-%d")
def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
needm={}
for day,g in spx.groupby("day"):
    if day[:7] not in MONTHS: continue
    g=g.reset_index(); tcol=g.columns[0]
    if len(g)<5: continue
    orb=g.iloc[:3]; oh=orb["high"].max(); ol=orb["low"].min(); w=oh-ol
    if w<=0: continue
    lt=oh+0.1*w; st=ol-0.1*w; ent=None
    for i in range(3,len(g)-1):
        c=g.iloc[i]["close"]
        if c>lt: ent=(c,1); break
        if c<st: ent=(c,-1); break
    if ent is None: continue
    E,d=ent; exp=dt.date.fromisoformat(day); cp="C" if d==1 else "P"
    for pct in np.arange(0.20,0.75,0.05):
        needm.setdefault(day[:7],set()).add(osi(exp,cp,round(E*(1+pct/100*d)/5)*5))
key=[re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)',l).group(1) for l in open(r"C:\Users\TT\gap_analyzer\.env") if l.startswith("DATABENTO_API_KEY")][0]
cl=db.Historical(key); frames=[]
for m in sorted(needm):
    y,mm=m.split("-"); nm=(dt.date(int(y),int(mm),1)+dt.timedelta(days=32)).replace(day=1); end=min(nm,dt.date(2026,7,17))
    try:
        qq=cl.timeseries.get_range(dataset="OPRA.PILLAR",symbols=sorted(needm[m]),stype_in="raw_symbol",schema="cbbo-1m",start=f"{m}-01",end=end.isoformat()).to_df().reset_index()
        frames.append(qq[["ts_event","symbol","bid_px_00","ask_px_00"]]); print("q",m,len(qq),flush=True)
    except Exception as e: print("q err",m,str(e)[:45],flush=True)
pd.concat(frames,ignore_index=True).to_parquet(OUT); print("SAVED",OUT,flush=True)
