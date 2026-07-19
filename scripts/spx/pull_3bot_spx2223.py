"""Pull SPXW 0DTE cbbo-1m quotes for the 3-bot backtest, 2022-2023 (the missing bear years).
Targeted: per day, only the strikes the 3 bots can touch (ATM band + spread legs + safety).
Usage: python pull_3bot_spx2223.py 2022-01,2022-02,... data/cache/spx/chunk2223_1.parquet
Note: Tue/Thu SPXW 0DTE only exists from Apr/May 2022 — missing symbols simply return no rows.
"""
import sys, re, os, datetime as dt
os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd, numpy as np, databento as db
BUF=0.0005; OTM=0.0030
MONTHS=set(sys.argv[1].split(",")); OUT=sys.argv[2]
if os.path.exists(OUT): print("skip (exists)",OUT); sys.exit()
spx=pd.read_parquet(r"data/cache/spx/spx_5m_2022_2023.parquet").sort_index()
spx=spx[~spx.index.duplicated(keep="last")]
spx["day"]=spx.index.strftime("%Y-%m-%d"); spx["mod"]=spx.index.hour*60+spx.index.minute
def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
def rnd(x): return round(x/5)*5
needm={}
for day,g in spx.groupby("day"):
    if day[:7] not in MONTHS: continue
    g=g.sort_values("mod")
    if len(g)<15: continue
    exp=dt.date.fromisoformat(day); syms=set()
    def orb(nbars):
        w=g.iloc[:nbars]; hi=w["high"].max(); lo=w["low"].min()
        for _,r in g.iloc[nbars:].iterrows():
            if r["close"]>hi*(1+BUF): return ("up",float(r["close"]))
            if r["close"]<lo*(1-BUF): return ("dn",float(r["close"]))
        return None
    b=orb(3)
    if b:
        d,px=b; cp="C" if d=="up" else "P"; atm=rnd(px)
        for k in (atm-10,atm-5,atm,atm+5,atm+10): syms.add(osi(exp,cp,k))
    for nbars,width in [(6,5),(12,10)]:
        b=orb(nbars)
        if b:
            d,px=b
            if d=="up":
                s=np.floor(px*(1-OTM)/5)*5
                for k in (s+5,s,s-5,s-width,s-width-5): syms.add(osi(exp,"P",k))
            else:
                s=np.ceil(px*(1+OTM)/5)*5
                for k in (s-5,s,s+5,s+width,s+width+5): syms.add(osi(exp,"C",k))
    if syms: needm.setdefault(day[:7],set()).update(syms)
key=os.environ.get("DATABENTO_API_KEY") or [re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)',l).group(1) for l in open(r"C:\Users\TT\gap_analyzer\.env") if l.startswith("DATABENTO_API_KEY")][0]
cl=db.Historical(key); frames=[]
for m in sorted(needm):
    y,mm=m.split("-"); nm=(dt.date(int(y),int(mm),1)+dt.timedelta(days=32)).replace(day=1)
    try:
        qq=cl.timeseries.get_range(dataset="OPRA.PILLAR",symbols=sorted(needm[m]),stype_in="raw_symbol",
                                   schema="cbbo-1m",start=f"{m}-01",end=nm.isoformat()).to_df().reset_index()
        frames.append(qq[["ts_event","symbol","bid_px_00","ask_px_00"]]); print("q",m,len(qq),flush=True)
    except Exception as e: print("q err",m,str(e)[:80],flush=True)
if frames: pd.concat(frames,ignore_index=True).to_parquet(OUT); print("SAVED",OUT,flush=True)
else: print("NO DATA",flush=True)
