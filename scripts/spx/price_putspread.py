import sys, datetime as dt, glob, os
sys.path.insert(0, r"C:\Users\TT\udb-orb-tsla\src")
import pandas as pd, numpy as np
SC=r"data/cache/spx"
WIDTH=5      # 5-pt bull put spread (matches screenshot ~$0.40 credit / ~$4.60 max loss)
spx=pd.concat([pd.read_parquet(SC+r"\spx_5m_2024_2025.parquet"),pd.read_parquet(SC+r"\spx_5m.parquet")]).sort_index()
spx=spx[~spx.index.duplicated(keep="last")]; spx["day"]=spx.index.strftime("%Y-%m-%d"); spx["hm"]=spx.index.strftime("%H:%M")
def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"
days=[]
for day,g in spx.groupby("day"):
    row=g[g["hm"]=="10:00"]
    if not len(row): continue
    days.append((day, float(row["open"].iloc[0]), float(g["close"].iloc[-1])))   # day, E@10:00, SPX settle
q=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(SC+r"\ps*.parquet"))],ignore_index=True)
sc=1e9 if q["ask_px_00"].abs().median()>1e6 else 1.0
q["ask"]=q["ask_px_00"]/sc; q["bid"]=q["bid_px_00"]/sc; q["t"]=pd.to_datetime(q["ts_event"],utc=True)
byS={s:g.sort_values("t").reset_index(drop=True) for s,g in q.groupby("symbol")}
def qat(sym, ts_utc, col):
    g=byS.get(sym)
    if g is None or g.empty: return None
    pre=g[g.t<=ts_utc]
    if not len(pre): return None
    v=pre[col].iloc[-1]; return float(v) if v and v>0 else None
res=[]
for day,E,settle in days:
    exp=dt.date.fromisoformat(day)
    et=pd.Timestamp(day+" 10:00", tz="America/New_York").tz_convert("UTC")
    # find short put strike whose 10:00 bid ~ $1.00 (the leg she sells)
    best=None
    for pts in range(5,65,5):
        ks=round((E-pts)/5)*5; sb=qat(osi(exp,"P",ks), et, "bid")
        if sb is None: continue
        if best is None or abs(sb-1.00)<abs(best[1]-1.00): best=(ks,sb)
    if best is None or not (0.60<=best[1]<=1.60): continue
    ks,sb=best; kl=ks-WIDTH; la=qat(osi(exp,"P",kl), et, "ask")
    if la is None: continue
    credit=sb-la
    if credit<=0.05: continue
    # expiry P&L (cash settle)
    if settle>=ks: pnl=credit
    elif settle<=kl: pnl=credit-WIDTH
    else: pnl=credit-(ks-settle)
    res.append((day,ks,kl,round(credit,2),round(settle-ks,1),round(pnl,3)))
r=pd.DataFrame(res,columns=["day","short_K","long_K","credit","settle_vs_short","pnl"]); r["yr"]=r.day.str[:4]
r.to_csv(SC+r"\spx_putspread_trades.csv",index=False)
print(f"BULL PUT CREDIT SPREAD 0DTE (width {WIDTH}pt, short ~$1.00, held to expiry), {len(r)} days\n")
print(f"{'Year':<6}{'#days':>6}{'WR':>6}{'avgCred':>9}{'net/spread$':>12}{'x10ct$':>10}{'worstDay/spread':>17}{'#maxLoss':>9}")
for y in ["2024","2025","2026","ALL"]:
    s=r if y=="ALL" else r[r.yr==y]
    if not len(s): continue
    maxloss=(s.pnl<=(s.credit-WIDTH)+0.01).sum()
    print(f"{y:<6}{len(s):>6}{100*(s.pnl>0).mean():>5.0f}%{s.credit.mean():>+9.2f}{s.pnl.sum()*100:>+12,.0f}{s.pnl.sum()*100*10:>+10,.0f}{s.pnl.min()*100:>+15,.0f}${maxloss:>8}")
print(f"\nnet/spread = per-spread $ (x100); x10ct = her size. Max loss/spread = -${(WIDTH):.0f}00 + credit ~ -$460.")
print(f"worst single day @10ct: ${r.pnl.min()*100*10:+,.0f} (one bad day wipes ~{int(abs(r.pnl.min())/r[r.pnl>0].pnl.mean())} winning days)")
