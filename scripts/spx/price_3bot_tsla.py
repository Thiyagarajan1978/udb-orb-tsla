"""3-bot managed-early concept (from SPX 'her system') applied to TSLA, real OPRA quotes.

Bot1: 15m OR break -> long ATM nearest-expiry, +50%/-50%, 30-min time stop (ADOPTED variant)
Bot2: 30m OR break -> 1-strike ($2.50) credit spread against the move
Bot3: 60m OR break -> 2-strike ($5.00) credit spread against the move
Spread short-leg OTM distance tested at 0.30% (literal SPX param) AND 1.0% (vol-scaled for TSLA).

Data: quotes_2223 (2022-09..2023-12, includes the bear) + quotes_weekly + quotes_july
(2025-01..2026-07). 2024 has no cached TSLA option quotes -> not testable offline.
TSLA expiries are weekly Fridays -> DTE 0-4, NOT daily 0DTE like SPX.
"""
import sys, json, glob, re, datetime as dt
from pathlib import Path
import pandas as pd, numpy as np
sys.path.insert(0, "src")
CACHE = Path("data/cache/opra")
BUF=0.0005; L_TGT, L_STP = 1.50, 0.50
S_TGT_FRAC=0.50; S_STP_MULT=2.0
TS_MAIN=30
STEP=2.5; W2, W3 = 2.5, 5.0
OTM_VARIANTS={"otm0.3%":0.0030, "otm1.0%":0.0100}
# ---- TSLA 5m bars ----
j=json.load(open(CACHE/"tsla_5m_2022_2023.json"))
b1=pd.DataFrame(j); b1["date"]=pd.to_datetime(b1["date"]); b1=b1.set_index("date").sort_index()
b1.index=b1.index.tz_localize("America/New_York")
frames=[b1[["open","high","low","close"]]]
from udb_orb.data.fmp_client import fetch_5min, rth_only
from dotenv import load_dotenv; load_dotenv()
b2=fetch_5min("TSLA", dt.date(2025,1,2), dt.date(2026,7,16))
frames.append(b2[["open","high","low","close"]])
b3=pd.read_parquet("data/cache/TSLA_5min_2024-01-02_2024-12-31.parquet")
frames.append(b3[["open","high","low","close"]])
bars=pd.concat(frames).sort_index()
bars=bars[~bars.index.duplicated(keep="last")]
bars=bars[(bars.index.hour*60+bars.index.minute>=570)&(bars.index.hour*60+bars.index.minute<960)]
bars["day"]=bars.index.strftime("%Y-%m-%d"); bars["mod"]=bars.index.hour*60+bars.index.minute
# ---- option quotes ----
qs=[]
for f in ["quotes_2223","quotes_weekly","quotes_july","quotes_2024_1","quotes_2024_2","quotes_2024_3"]:
    p=CACHE/f"{f}.parquet"
    if p.exists(): qs.append(pd.read_parquet(p)[["ts_event","symbol","bid_px_00","ask_px_00"]])
q=pd.concat(qs,ignore_index=True).drop_duplicates(["ts_event","symbol"])
q=q[q["ts_event"].notna()]
sc=1e9 if q["ask_px_00"].abs().median()>1e6 else 1.0
q["ask"]=q["ask_px_00"]/sc; q["bid"]=q["bid_px_00"]/sc
t=pd.to_datetime(q["ts_event"],utc=True).dt.tz_convert("America/New_York")
q["day"]=t.dt.strftime("%Y-%m-%d"); q["mod"]=(t.dt.hour*60+t.dt.minute).astype(int)
pat=re.compile(r'(\d{6})([CP])(\d{8})$')
m=q["symbol"].str.extract(pat)
q["exp"]=m[0]; q["cp"]=m[1]; q["K"]=m[2].astype(float)/1000
q=q[q["bid"].notna() & (q["ask"]>0) & q["exp"].notna()]
# nearest expiry per day: min exp >= trade day
q["expd"]=pd.to_datetime("20"+q["exp"],format="%Y%m%d").dt.strftime("%Y-%m-%d")
q=q[q["expd"]>=q["day"]]
near=q.groupby("day")["expd"].min().rename("near")
q=q.merge(near,on="day"); q=q[q["expd"]==q["near"]]
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
    dd=book.get((day,c))
    if not dd or k not in dd: return (None,None)
    mods,bid,ask=dd[k]; mask=mods>mod0
    for m,b in zip(mods[mask],bid[mask]):
        if maxhold is not None and m-mod0>maxhold: return (b-entry_ask,int(m-mod0))
        if b>=L_TGT*entry_ask: return (b-entry_ask,int(m-mod0))
        if b<=L_STP*entry_ask: return (b-entry_ask,int(m-mod0))
    lb=bid[-1]; return (float(lb)-entry_ask,int(mods[-1]-mod0))
def scan_spread(day,c,ks,kl,width,mod0,credit,close):
    ddc=book.get((day,c))
    if not ddc or ks not in ddc or kl not in ddc: return None
    ms=ddc[ks][0]; ml=ddc[kl][0]
    mins=sorted(set(ms[ms>mod0]).union(set(ml[ml>mod0])))
    for m in mins:
        sask=qat(day,c,ks,m,"ask"); lbid=qat(day,c,kl,m,"bid")
        if sask is None or lbid is None: continue
        cost=sask-lbid
        if cost<=S_TGT_FRAC*credit: return credit-cost
        if cost>=S_STP_MULT*credit or cost>=width: return credit-min(cost,width)
    # DTE>0 most days: settle at last-quote buy-back, NOT intrinsic
    sask=qat(day,c,ks,955,"ask"); lbid=qat(day,c,kl,955,"bid")
    if sask is None or lbid is None: return None
    return credit-min(max(sask-lbid,0.0),width)
rows=[]
for day,g in bars.groupby("day"):
    g=g.sort_values("mod")
    if len(g)<15: continue
    close=float(g["close"].iloc[-1])
    def orb(nbars):
        w=g.iloc[:nbars]; hi=w["high"].max(); lo=w["low"].min()
        for _,r in g.iloc[nbars:].iterrows():
            if r["close"]>hi*(1+BUF): return ("up",int(r["mod"]),float(r["close"]))
            if r["close"]<lo*(1-BUF): return ("dn",int(r["mod"]),float(r["close"]))
        return None
    rec={"day":day}
    b=orb(3)
    if b:
        d,mod,px=b; c="C" if d=="up" else "P"; dd,ks=strikes(day,c)
        if ks:
            k=min(ks,key=lambda x:abs(x-px))
            if abs(k-px)<=5:
                ea=qat(day,c,k,mod,"ask")
                if ea and ea>0.05:
                    r1,h1=scan_long(day,c,k,mod,ea,maxhold=TS_MAIN)
                    if r1 is not None:
                        rec["bot1"]=r1*100; rec["b1_dir"]=d; rec["b1_prem"]=ea; rec["b1_held"]=h1
    for nb,width,name in [(6,W2,"bot2"),(12,W3,"bot3")]:
        b=orb(nb)
        if b:
            d,mod,px=b
            for vn,otm in OTM_VARIANTS.items():
                if d=="up": c="P"; cand=[x for x in strikes(day,"P")[1] if x<=px*(1-otm)]; short=max(cand,default=None); longk=short-width if short else None
                else:       c="C"; cand=[x for x in strikes(day,"C")[1] if x>=px*(1+otm)]; short=min(cand,default=None); longk=short+width if short else None
                if short and longk and longk in strikes(day,c)[0]:
                    sb=qat(day,c,short,mod,"bid"); la=qat(day,c,longk,mod,"ask")
                    if sb and la is not None:
                        credit=sb-la
                        if credit>0.05:
                            r=scan_spread(day,c,short,longk,width,mod,credit,close)
                            if r is not None: rec[f"{name}_{vn}"]=r*100
    rows.append(rec)
r=pd.DataFrame(rows); r["yr"]=r.day.str[:4]; r["month"]=r.day.str[:7]
cols=["bot1"]+[f"{n}_{v}" for n in ["bot2","bot3"] for v in OTM_VARIANTS]
for c in cols:
    if c not in r: r[c]=np.nan
r["combo_0.3"]=r[["bot1","bot2_otm0.3%","bot3_otm0.3%"]].sum(axis=1,min_count=1)
r["combo_1.0"]=r[["bot1","bot2_otm1.0%","bot3_otm1.0%"]].sum(axis=1,min_count=1)
r.to_csv("exports/spx/tsla_3bot_trades.csv",index=False)
def line(name,s):
    s=s.dropna()
    if not len(s): return f"{name:<16} (no data)"
    return f"{name:<16}{len(s):>6}{100*(s>0).mean():>6.0f}%{s.mean():>+9.1f}{s.sum():>+12,.0f}{s.min():>+9,.0f}"
print("=== 3-BOT CONCEPT ON TSLA, real OPRA quotes (nearest weekly expiry, DTE 0-4) ===")
print("periods: 2022-09..2023-12 (incl. bear) + 2025-01..2026-07  |  2024: no cached quotes")
print(f"{'':<16}{'#days':>6}{'WR':>7}{'avg/ct$':>9}{'net@1ct':>12}{'worst$':>9}")
for y in sorted(r.yr.unique())+["ALL"]:
    sub=r if y=="ALL" else r[r.yr==y]
    print(f"\n-- {y} --")
    for c,nm in [("bot1","B1 15m ts30"),("bot2_otm0.3%","B2 0.3%OTM"),("bot2_otm1.0%","B2 1.0%OTM"),
                 ("bot3_otm0.3%","B3 0.3%OTM"),("bot3_otm1.0%","B3 1.0%OTM"),
                 ("combo_0.3","COMBO 0.3%"),("combo_1.0","COMBO 1.0%")]:
        print(line(nm,sub[c]))
print("\n=== MONTHLY combo (bot1 + 1.0%OTM spreads), per 1 contract ===")
mm=r.groupby("month").agg(days=("combo_1.0",lambda s:s.notna().sum()),net=("combo_1.0","sum"))
for mo,row in mm.iterrows():
    print(f"  {mo}  {int(row['days']):>3}d  {row['net']:>+10,.0f}")
print(f"\nlosing months: {(mm.net<0).sum()} of {len(mm)} | worst {mm.net.min():+,.0f} best {mm.net.max():+,.0f} median {mm.net.median():+,.0f}")
h=r["b1_held"].dropna()
if len(h): print(f"B1 hold: median {h.median():.0f}m | B1 entry prem median ${r.b1_prem.median()*100:,.0f}/ct")
print("DONE")
