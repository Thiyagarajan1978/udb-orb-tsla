import glob, re
import pandas as pd, numpy as np
CACHE = r"data/cache/spx"
OUT   = r"exports/spx"
# ---- params (managed-early, her described system) ----
BUF=0.0005
L_TGT, L_STP = 1.50, 0.50
S_OTM=0.0030
S_TGT_FRAC=0.50
S_STP_MULT=2.0
W30, W60 = 5, 10
TS_MAIN=30                       # the requested time stop (minutes)
TS_ALTS=[60,90]                  # context variants
# ---- SPX 5m ----
spx=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(CACHE+r"/spx_5m*.parquet"))]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]
spx.index=spx.index.tz_localize(None) if spx.index.tz is None else spx.index
spx["day"]=spx.index.strftime("%Y-%m-%d"); spx["mod"]=spx.index.hour*60+spx.index.minute
# ---- option quotes ----
q=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(CACHE+r"/chunk*.parquet"))],ignore_index=True)
q=q[q["ts_event"].notna()].copy()
sc=1e9 if q["ask_px_00"].abs().median()>1e6 else 1.0
q["ask"]=q["ask_px_00"]/sc; q["bid"]=q["bid_px_00"]/sc
t=pd.to_datetime(q["ts_event"],utc=True).dt.tz_convert("America/New_York")
q["day"]=t.dt.strftime("%Y-%m-%d"); q["mod"]=(t.dt.hour*60+t.dt.minute).astype(int)
pat=re.compile(r'([CP])(\d{8})$')
cp=[]; K=[]
for s in q["symbol"].values:
    m=pat.search(s); cp.append(m.group(1) if m else None); K.append(int(m.group(2))/1000 if m else np.nan)
q["cp"]=cp; q["K"]=K
q=q[q["bid"].notna() & (q["ask"]>0)]
book={}
for (d,c),g in q.groupby(["day","cp"]):
    dd={}
    for k,gg in g.groupby("K"):
        gg=gg.sort_values("mod")
        dd[k]=(gg["mod"].values, gg["bid"].values, gg["ask"].values)
    book[(d,c)]=dd
def strikes(day,c):
    dd=book.get((day,c)); return (dd, sorted(dd.keys())) if dd else (None,[])
def qat(day,c,k,mod,col):
    dd=book.get((day,c))
    if not dd or k not in dd: return None
    mods,bid,ask=dd[k]; i=np.searchsorted(mods,mod,side="right")-1
    if i<0: return None
    v=(bid if col=="bid" else ask)[i]; return float(v) if v>0 else None
def scan_long(day,c,k,mod0,entry_ask,maxhold=None):
    """brackets +50/-50; if maxhold set, flatten at first quote past it. Returns (pnl/share, held_min)."""
    dd=book.get((day,c))
    if not dd or k not in dd: return (None,None)
    mods,bid,ask=dd[k]; mask=mods>mod0
    for m,b in zip(mods[mask],bid[mask]):
        if maxhold is not None and m-mod0>maxhold:
            return (b-entry_ask, int(m-mod0))
        if b>=L_TGT*entry_ask: return (b-entry_ask, int(m-mod0))
        if b<=L_STP*entry_ask: return (b-entry_ask, int(m-mod0))
    lb=bid[-1]; return (float(lb)-entry_ask, int(mods[-1]-mod0))
def scan_spread(day,c,ks,kl,width,mod0,credit,close,maxhold=None):
    ddc=book.get((day,c))
    if not ddc or ks not in ddc or kl not in ddc: return None
    ms,sb,sa=ddc[ks]; ml,lb,la=ddc[kl]
    mins=sorted(set(ms[ms>mod0]).union(set(ml[ml>mod0])))
    for m in mins:
        sask=qat(day,c,ks,m,"ask"); lbid=qat(day,c,kl,m,"bid")
        if sask is None or lbid is None: continue
        cost=sask-lbid
        if maxhold is not None and m-mod0>maxhold:
            return credit-min(max(cost,0.0),width)          # time-stop buy-back
        if cost<=S_TGT_FRAC*credit: return credit-cost
        if cost>=S_STP_MULT*credit or cost>=width: return credit-min(cost,width)
    if c=="P": intr=max(0.0,ks-close)-max(0.0,kl-close)
    else:      intr=max(0.0,close-ks)-max(0.0,close-kl)
    intr=min(max(intr,0.0),width)
    return credit-intr
# ---- run per day ----
rows=[]
for day,g in spx.groupby("day"):
    g=g.sort_values("mod")
    if len(g)<15: continue
    close=float(g["close"].iloc[-1])
    def orb(nbars):
        w=g.iloc[:nbars]; hi=w["high"].max(); lo=w["low"].min()
        post=g.iloc[nbars:]
        for _,r in post.iterrows():
            if r["close"]>hi*(1+BUF): return ("up",int(r["mod"]),float(r["close"]))
            if r["close"]<lo*(1-BUF): return ("dn",int(r["mod"]),float(r["close"]))
        return None
    rec={"day":day}
    b=orb(3)
    if b:
        d,mod,px=b; c="C" if d=="up" else "P"; dd,ks=strikes(day,c)
        if ks:
            k=min(ks,key=lambda x:abs(x-px)); ea=qat(day,c,k,mod,"ask")
            if ea and ea>0.05:
                r0,h0=scan_long(day,c,k,mod,ea)                 # baseline (EOD fallback)
                if r0 is not None:
                    rec["bot1_base"]=r0*100; rec["b1_dir"]=d; rec["b1_prem"]=ea; rec["b1_held_base"]=h0
                r1,h1=scan_long(day,c,k,mod,ea,maxhold=TS_MAIN)
                if r1 is not None: rec["bot1_ts30"]=r1*100; rec["b1_held_ts30"]=h1
                for a in TS_ALTS:
                    ra,_=scan_long(day,c,k,mod,ea,maxhold=a)
                    if ra is not None: rec[f"bot1_ts{a}"]=ra*100
    for nb,width,name in [(6,W30,"bot2"),(12,W60,"bot3")]:
        b=orb(nb)
        if b:
            d,mod,px=b
            if d=="up": c="P"; short=max([x for x in strikes(day,"P")[1] if x<=px*(1-S_OTM)],default=None); longk=short-width if short else None
            else:       c="C"; short=min([x for x in strikes(day,"C")[1] if x>=px*(1+S_OTM)],default=None); longk=short+width if short else None
            if short and longk:
                sb=qat(day,c,short,mod,"bid"); la=qat(day,c,longk,mod,"ask")
                if sb and la is not None:
                    credit=sb-la
                    if credit>0.05:
                        r0=scan_spread(day,c,short,longk,width,mod,credit,close)
                        if r0 is not None: rec[name]=r0*100
                        r1=scan_spread(day,c,short,longk,width,mod,credit,close,maxhold=TS_MAIN)
                        if r1 is not None: rec[name+"_ts30"]=r1*100
    rows.append(rec)
r=pd.DataFrame(rows); r["yr"]=r.day.str[:4]; r["month"]=r.day.str[:7]
for col in ["bot1_base","bot1_ts30","bot1_ts60","bot1_ts90","bot2","bot3","bot2_ts30","bot3_ts30"]:
    if col not in r: r[col]=np.nan
r["combo_base"]=r[["bot1_base","bot2","bot3"]].sum(axis=1,min_count=1)
r["combo_ts30"]=r[["bot1_ts30","bot2","bot3"]].sum(axis=1,min_count=1)   # time stop on Bot1 only
r["combo_all30"]=r[["bot1_ts30","bot2_ts30","bot3_ts30"]].sum(axis=1,min_count=1)  # time stop on all bots
r.to_csv(OUT+r"/hersystem_ts30_trades.csv",index=False)
def line(name,s):
    s=s.dropna()
    if not len(s): return f"{name:<16} (no data)"
    return f"{name:<16}{len(s):>6}{100*(s>0).mean():>6.0f}%{s.mean():>+9.1f}{s.sum():>+12,.0f}{s.min():>+9,.0f}"
print("=== 30-MIN TIME STOP vs BASELINE, SPX 0DTE, real quotes ===")
print(f"{'':<16}{'#days':>6}{'WR':>7}{'avg/ct$':>9}{'net@1ct':>12}{'worst$':>9}")
for y in ["2024","2025","2026","ALL"]:
    sub=r if y=="ALL" else r[r.yr==y]
    print(f"\n-- {y} --")
    for col,nm in [("bot1_base","B1 baseline"),("bot1_ts30","B1 ts30"),("bot1_ts60","B1 ts60"),("bot1_ts90","B1 ts90"),
                   ("combo_base","COMBO base"),("combo_ts30","COMBO B1ts30"),("combo_all30","COMBO all-ts30")]:
        print(line(nm,sub[col]))
print("\n=== MONTHLY (per 1 contract) — Bot1 with 30-min stop, spreads unchanged ===")
print(f"{'month':<9}{'days':>5}{'bot1_ts30':>11}{'bot2':>9}{'bot3':>9}{'COMBO_ts30':>12}{'COMBO_base':>12}{'delta':>10}")
for mo,g in r.groupby("month"):
    print(f"{mo:<9}{g.combo_ts30.notna().sum():>5}{g.bot1_ts30.sum():>+11,.0f}{g.bot2.sum():>+9,.0f}{g.bot3.sum():>+9,.0f}"
          f"{g.combo_ts30.sum():>+12,.0f}{g.combo_base.sum():>+12,.0f}{g.combo_ts30.sum()-g.combo_base.sum():>+10,.0f}")
mm=r.groupby("month")["combo_ts30"].sum()
print(f"\nlosing months (B1ts30 combo): {(mm<0).sum()} of {len(mm)}  | worst month {mm.min():+,.0f}  best {mm.max():+,.0f}  median {mm.median():+,.0f}")
hh=r["b1_held_ts30"].dropna()
print(f"B1 ts30 hold: median {hh.median():.0f} min, max {hh.max():.0f} min")
print("DONE")
