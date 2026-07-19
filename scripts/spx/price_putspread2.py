import sys, datetime as dt, glob
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src")
import pandas as pd, numpy as np
SC=r"data/cache/spx"
WIDTH=5; CREDIT_TGT=0.40
spx=pd.concat([pd.read_parquet(SC+r"\spx_5m_2024_2025.parquet"),pd.read_parquet(SC+r"\spx_5m.parquet")]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]; spx["day"]=spx.index.strftime("%Y-%m-%d"); spx["hm"]=spx.index.strftime("%H:%M")
def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
days=[]
for day,g in spx.groupby("day"):
    row=g[g["hm"]=="10:00"]
    if len(row): days.append((day,float(row["open"].iloc[0]),float(g["close"].iloc[-1])))
q=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(SC+r"\ps_w*.parquet"))],ignore_index=True)
sc=1e9 if q["ask_px_00"].abs().median()>1e6 else 1.0
q["ask"]=q["ask_px_00"]/sc; q["bid"]=q["bid_px_00"]/sc; q["t"]=pd.to_datetime(q["ts_event"],utc=True)
byS={s:g.sort_values("t").reset_index(drop=True) for s,g in q.groupby("symbol")}
def qat(sym,ts,col):
    g=byS.get(sym)
    if g is None or g.empty: return None
    pre=g[g.t<=ts]
    if not len(pre): return None
    v=pre[col].iloc[-1]; return float(v) if v and v>0 else None
res=[]; tot_days=len(days)
for day,E,settle in days:
    exp=dt.date.fromisoformat(day); et=pd.Timestamp(day+" 10:00",tz="America/New_York").tz_convert("UTC")
    best=None
    for pts in range(WIDTH,145,5):
        ks=round((E-pts)/5)*5; kl=ks-WIDTH
        sb=qat(osi(exp,"P",ks),et,"bid"); la=qat(osi(exp,"P",kl),et,"ask")
        if sb is None or la is None: continue
        cr=sb-la
        if cr<=0: continue
        if best is None or abs(cr-CREDIT_TGT)<abs(best[3]-CREDIT_TGT): best=(ks,kl,sb,cr)
    if best is None or not (0.20<=best[3]<=0.70): continue
    ks,kl,sb,cr=best
    pnl = cr if settle>=ks else (cr-WIDTH if settle<=kl else cr-(ks-settle))
    res.append((day,ks,round(cr,2),round(pnl,3)))
r=pd.DataFrame(res,columns=["day","short_K","credit","pnl"]); r["yr"]=r.day.str[:4]
r.to_csv(SC+r"\spx_putspread_FINAL.csv",index=False)
print(f"=== FINAL: 0DTE bull put credit spread (width {WIDTH}, target credit ${CREDIT_TGT}, 10:00 entry, held to expiry) ===")
print(f"coverage: {len(r)}/{tot_days} trading days priced ({100*len(r)/tot_days:.0f}%)\n")
print(f"{'Year':<6}{'#days':>6}{'WR':>6}{'avgCred':>9}{'net/spread$':>12}{'@10ct$':>10}{'worst/spread':>14}{'#maxLoss':>9}")
for y in ["2024","2025","2026","ALL"]:
    s=r if y=="ALL" else r[r.yr==y]
    if not len(s): continue
    ml=(s.pnl<=(s.credit-WIDTH)+0.02).sum()
    print(f"{y:<6}{len(s):>6}{100*(s.pnl>0).mean():>5.0f}%{s.credit.mean():>+9.2f}{s.pnl.sum()*100:>+12,.0f}{s.pnl.sum()*1000:>+10,.0f}{s.pnl.min()*100:>+14,.0f}{ml:>9}")
be=r.credit.mean()/WIDTH*100
print(f"\navg credit ${r.credit.mean():.2f} on ${WIDTH*100:.0f} width -> breakeven win-rate needed = {100-be:.0f}%")
print(f"worst single day @10ct: ${r.pnl.min()*1000:+,.0f}  (= ~{int(abs(r.pnl.min())/max(r[r.pnl>0].pnl.mean(),0.01))} winning days)")
print("DONE")
