"""Pull TSLA cbbo-1m quotes for the 3-bot backtest, 2024 (the missing year).
Nearest weekly Friday expiry, targeted strikes only (ATM band + spread legs at 0.3% and 1.0% OTM).
Usage: python pull_3bot_tsla2024.py 2024-01,2024-02,... data/cache/opra/quotes_2024_1.parquet
Note: holiday-Friday weeks (e.g. Good Friday) shift expiry to Thursday; we request both symbols.
"""
import sys, re, os, datetime as dt
os.chdir(r"C:\Users\TT\udb-orb-tsla")
import pandas as pd, numpy as np, databento as db
BUF=0.0005; STEP=2.5
MONTHS=set(sys.argv[1].split(",")); OUT=sys.argv[2]
if os.path.exists(OUT): print("skip (exists)",OUT); sys.exit()
bars=pd.read_parquet(r"data/cache/TSLA_5min_2024-01-02_2024-12-31.parquet").sort_index()
bars["day"]=bars.index.strftime("%Y-%m-%d"); bars["mod"]=bars.index.hour*60+bars.index.minute
def osi(exp,cp,strike): return f"TSLA  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
def rnd(x): return round(x/STEP)*STEP
needm={}
for day,g in bars.groupby("day"):
    if day[:7] not in MONTHS: continue
    g=g.sort_values("mod")
    if len(g)<15: continue
    d0=dt.date.fromisoformat(day)
    fri=d0+dt.timedelta(days=(4-d0.weekday())%7)
    exps=[fri, fri-dt.timedelta(days=1)] if fri!=d0 else [fri]   # Thursday fallback for holiday Fridays
    syms=set()
    def orb(nbars):
        w=g.iloc[:nbars]; hi=w["high"].max(); lo=w["low"].min()
        for _,r in g.iloc[nbars:].iterrows():
            if r["close"]>hi*(1+BUF): return ("up",float(r["close"]))
            if r["close"]<lo*(1-BUF): return ("dn",float(r["close"]))
        return None
    b=orb(3)
    if b:
        d,px=b; cp="C" if d=="up" else "P"; atm=rnd(px)
        for e in exps:
            for k in (atm-STEP*2,atm-STEP,atm,atm+STEP,atm+STEP*2): syms.add(osi(e,cp,k))
    for nbars,width in [(6,STEP),(12,STEP*2)]:
        b=orb(nbars)
        if b:
            d,px=b
            for otm in (0.003,0.010):
                if d=="up":
                    s=np.floor(px*(1-otm)/STEP)*STEP
                    ks=(s+STEP,s,s-STEP,s-width,s-width-STEP)
                    cp="P"
                else:
                    s=np.ceil(px*(1+otm)/STEP)*STEP
                    ks=(s-STEP,s,s+STEP,s+width,s+width+STEP)
                    cp="C"
                for e in exps:
                    for k in ks: syms.add(osi(e,cp,k))
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
