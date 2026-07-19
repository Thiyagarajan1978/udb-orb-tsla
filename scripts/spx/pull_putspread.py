import sys, re, datetime as dt, os
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src"); os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd, numpy as np, databento as db
SC=r"data/cache/spx"
MONTHS=set(sys.argv[1].split(",")); OUT=sys.argv[2]
if os.path.exists(OUT): print("skip",OUT); sys.exit()
spx=pd.concat([pd.read_parquet(SC+r"\spx_5m_2024_2025.parquet"),pd.read_parquet(SC+r"\spx_5m.parquet")]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]; spx["day"]=spx.index.strftime("%Y-%m-%d"); spx["hm"]=spx.index.strftime("%H:%M")
def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
needm={}
for day,g in spx.groupby("day"):
    if day[:7] not in MONTHS: continue
    row=g[g["hm"]=="10:00"]
    if not len(row): continue
    E=float(row["open"].iloc[0]); exp=dt.date.fromisoformat(day)
    for pts in range(5,140,5):                   # OTM PUT strikes 5..135 pts below spot (wide: covers $1 short at SPX 7500)
        needm.setdefault(day[:7],set()).add(osi(exp,"P",round((E-pts)/5)*5))
key=[re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)',l).group(1) for l in open(r"C:\Users\TT\gap_analyzer\.env") if l.startswith("DATABENTO_API_KEY")][0]
cl=db.Historical(key); frames=[]
for m in sorted(needm):
    y,mm=m.split("-"); nm=(dt.date(int(y),int(mm),1)+dt.timedelta(days=32)).replace(day=1); end=min(nm,dt.date(2026,7,17))
    try:
        qq=cl.timeseries.get_range(dataset="OPRA.PILLAR",symbols=sorted(needm[m]),stype_in="raw_symbol",schema="cbbo-1m",start=f"{m}-01",end=end.isoformat()).to_df().reset_index()
        frames.append(qq[["ts_event","symbol","bid_px_00","ask_px_00"]]); print("q",m,len(qq),flush=True)
    except Exception as e: print("q err",m,str(e)[:45],flush=True)
pd.concat(frames,ignore_index=True).to_parquet(OUT); print("SAVED",OUT,flush=True)
