import glob, re, datetime as dt
import pandas as pd, numpy as np
SC=r"data/cache/spx"
# ---- params (managed-early, her described system) ----
BUF=0.0005                      # 0.05% close-break buffer on the OR
L_TGT, L_STP = 1.50, 0.50       # 15m long: +50% target / -50% stop
S_OTM=0.0030                    # credit spread short leg ~0.30% OTM against the move
S_TGT_FRAC=0.50                 # close spread at 50% of credit kept
S_STP_MULT=2.0                  # stop if buy-back cost >= 2x credit (lose ~1x credit)
W30, W60 = 5, 10                # 30m spread 5-wide, 60m spread 10-wide (she said "10 wide")
# ---- SPX 5m ----
spx=pd.concat([pd.read_parquet(SC+r"\spx_5m_2024_2025.parquet"),pd.read_parquet(SC+r"\spx_5m.parquet")]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]
spx.index=spx.index.tz_localize(None) if spx.index.tz is None else spx.index
spx["day"]=spx.index.strftime("%Y-%m-%d"); spx["mod"]=spx.index.hour*60+spx.index.minute
# ---- option quotes (chunk = both C+P, full intraday, 636 days) ----
q=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(SC+r"\chunk*.parquet"))],ignore_index=True)
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
# per (day,cp) -> {K: (mods, bid, ask)} sorted by mod
book={}
for (d,c),g in q.groupby(["day","cp"]):
    dd={}
    for k,gg in g.groupby("K"):
        gg=gg.sort_values("mod")
        dd[k]=(gg["mod"].values, gg["bid"].values, gg["ask"].values)
    book[(d,c)]=dd
def strikes(day,c):
    dd=book.get((day,c)); return (dd, sorted(dd.keys())) if dd else (None,[])
def qat(day,c,k,mod,col):   # last quote at/just before mod
    dd=book.get((day,c))
    if not dd or k not in dd: return None
    mods,bid,ask=dd[k]; i=np.searchsorted(mods,mod,side="right")-1
    if i<0: return None
    v=(bid if col=="bid" else ask)[i]; return float(v) if v>0 else None
def scan_long(day,c,k,mod0,entry_ask):
    dd=book.get((day,c))
    if not dd or k not in dd: return (None,None)
    mods,bid,ask=dd[k]; mask=mods>mod0
    for m,b in zip(mods[mask],bid[mask]):
        if b>=L_TGT*entry_ask: return (b-entry_ask, int(m-mod0))
        if b<=L_STP*entry_ask: return (b-entry_ask, int(m-mod0))
    lb=bid[-1]; return (float(lb)-entry_ask, int(mods[-1]-mod0))
def scan_spread(day,c,ks,kl,width,mod0,credit,close):
    ddc=book.get((day,c))
    if not ddc or ks not in ddc or kl not in ddc: return None
    ms,sb,sa=ddc[ks]; ml,lb,la=ddc[kl]
    # union of minutes after entry
    mins=sorted(set(ms[ms>mod0]).union(set(ml[ml>mod0])))
    for m in mins:
        sask=qat(day,c,ks,m,"ask"); lbid=qat(day,c,kl,m,"bid")
        if sask is None or lbid is None: continue
        cost=sask-lbid              # buy-back cost of the spread
        if cost<=S_TGT_FRAC*credit: return credit-cost         # take profit
        if cost>=S_STP_MULT*credit or cost>=width: return credit-min(cost,width)  # stop
    # EOD intrinsic settle
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
    # Bot1: 15m long ATM
    b=orb(3)
    if b:
        d,mod,px=b; c="C" if d=="up" else "P"; dd,ks=strikes(day,c)
        if ks:
            k=min(ks,key=lambda x:abs(x-px)); ea=qat(day,c,k,mod,"ask")
            if ea and ea>0.05:
                r,held=scan_long(day,c,k,mod,ea)
                if r is not None:
                    rec["bot1"]=r*100; rec["b1_dir"]=d; rec["b1_prem"]=ea; rec["b1_held"]=held
    # NULL control: buy ATM CALL at fixed 10:00 (no signal), same +50/-50 managed exit
    g10=g[g["mod"]==600]
    if len(g10):
        px=float(g10["close"].iloc[0]); dd,ks=strikes(day,"C")
        if ks:
            k=min(ks,key=lambda x:abs(x-px)); ea=qat(day,"C",k,600,"ask")
            if ea and ea>0.05:
                r,held=scan_long(day,"C",k,600,ea)
                if r is not None: rec["null_call"]=r*100
    # Bot2: 30m credit spread against move (5-wide)
    b=orb(6)
    if b:
        d,mod,px=b
        if d=="up": c="P"; short=max([x for x in strikes(day,"P")[1] if x<=px*(1-S_OTM)],default=None); longk=short-W30 if short else None
        else:       c="C"; short=min([x for x in strikes(day,"C")[1] if x>=px*(1+S_OTM)],default=None); longk=short+W30 if short else None
        if short and longk:
            sb=qat(day,c,short,mod,"bid"); la=qat(day,c,longk,mod,"ask")
            if sb and la is not None:
                credit=sb-la
                if credit>0.05:
                    r=scan_spread(day,c,short,longk,W30,mod,credit,close)
                    if r is not None: rec["bot2"]=r*100
    # Bot3: 60m credit spread against move (10-wide)
    b=orb(12)
    if b:
        d,mod,px=b
        if d=="up": c="P"; short=max([x for x in strikes(day,"P")[1] if x<=px*(1-S_OTM)],default=None); longk=short-W60 if short else None
        else:       c="C"; short=min([x for x in strikes(day,"C")[1] if x>=px*(1+S_OTM)],default=None); longk=short+W60 if short else None
        if short and longk:
            sb=qat(day,c,short,mod,"bid"); la=qat(day,c,longk,mod,"ask")
            if sb and la is not None:
                credit=sb-la
                if credit>0.05:
                    r=scan_spread(day,c,short,longk,W60,mod,credit,close)
                    if r is not None: rec["bot3"]=r*100
    rows.append(rec)
r=pd.DataFrame(rows); r["yr"]=r.day.str[:4]
for b in ["bot1","bot2","bot3"]:
    if b not in r: r[b]=np.nan
r["combo"]=r[["bot1","bot2","bot3"]].sum(axis=1,min_count=1)
r.to_csv(SC+r"\hersystem_trades.csv",index=False)
def line(name,s):
    s=s.dropna()
    if not len(s): return f"{name:<10} (no data)"
    return f"{name:<10}{len(s):>6}{100*(s>0).mean():>6.0f}%{s.mean():>+9.1f}{s.sum():>+11,.0f}{s.sum()*10:>+12,.0f}{s.min():>+9,.0f}"
print("=== HER SYSTEM (managed-early), SPX 2024-01 -> 2026-07, real 0DTE quotes ===")
print("15m long ATM (+50%/-50%) | 30m 5-wide credit spread | 60m 10-wide credit spread | short ~0.30% OTM, close@50% credit")
print(f"{'':<10}{'#days':>6}{'WR':>7}{'avg/ct$':>9}{'net@1ct':>11}{'net@10ct':>12}{'worst$':>9}")
for y in ["2024","2025","2026","ALL"]:
    sub=r if y=="ALL" else r[r.yr==y]
    print(f"\n-- {y} --")
    for b,nm in [("bot1","15m Call"),("bot2","30m Spd"),("bot3","60m Spd"),("combo","COMBINED")]:
        print(line(nm,sub[b]))
print("\n(net@10ct = her stated 10-contract size. worst$ = worst single day per 1 contract.)")
# ---- DIAGNOSTICS ----
print("\n=== DIAGNOSTICS ===")
if "b1_prem" in r:
    p=r["b1_prem"].dropna()
    print(f"15m-long entry premium ($/contract): median ${p.median()*100:,.0f}  p10 ${p.quantile(.1)*100:,.0f}  p90 ${p.quantile(.9)*100:,.0f}")
    h=r["b1_held"].dropna()
    print(f"15m-long time held (min): median {h.median():.0f}  |  exited <=1min: {100*(h<=1).mean():.0f}%  <=3min: {100*(h<=3).mean():.0f}%")
    for d in ["up","dn"]:
        s=r[r.b1_dir==d]["bot1"].dropna()
        if len(s): print(f"15m-long dir={d}: {len(s)}d  WR {100*(s>0).mean():.0f}%  avg ${s.mean():+.0f}  net ${s.sum():+,.0f}")
print("\n--- NULL CONTROL: ATM call bought at fixed 10:00 every day (no ORB signal) ---")
if "null_call" in r:
    for y in ["2024","2025","2026","ALL"]:
        s=(r if y=="ALL" else r[r.yr==y])["null_call"].dropna()
        if len(s): print(f"  {y}: {len(s)}d  WR {100*(s>0).mean():.0f}%  avg ${s.mean():+.0f}  net@1ct ${s.sum():+,.0f}")
    print("  -> if this is also hugely positive, the 'edge' is buy-calls-in-a-bull, NOT the ORB signal.")
print("DONE")
