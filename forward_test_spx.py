"""Out-of-sample SPX 3-BOT forward test (Databento shadow, no broker, no money).

Prices the FROZEN SPX 3-bot system's new-session signals against REAL SPXW 0DTE quotes
(OPRA cbbo-1m, buy-ask / sell-bid) and APPENDS to exports/forward_spx_ledger.csv.
Bot1 = 15m long ATM +50%/-50% with the ADOPTED 30-min time stop; Bot2/3 = 5/10-wide
credit spreads ~0.30% OTM against the move, close @50% credit, stop @2x.
Idempotent; OPRA releases T+1. Underlying: ^GSPC 5m via FMP.

Usage:
    python forward_test_spx.py                              # new sessions since ledger
    python forward_test_spx.py --start 2026-07-17 --end 2026-07-17
"""
import argparse, os, re, sys, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import pandas as pd, numpy as np
from udb_orb.data.fmp_client import fetch_5min, rth_only

ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(ROOT, "exports", "forward_spx_ledger.csv")
BUF=0.0005; L_TGT,L_STP=1.50,0.50; S_OTM=0.0030; S_TGT_FRAC=0.50; S_STP_MULT=2.0
TS_MAIN=30; W30,W60=5,10

def get_db_key():
    k = os.getenv("DATABENTO_API_KEY")
    if k: return k
    for path in (os.path.join(ROOT, ".env"), r"C:\Users\TT\gap_analyzer\.env"):
        try:
            for line in open(path):
                m = re.match(r'\s*DATABENTO_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
                if m: return m.group(1)
        except FileNotFoundError: pass
    sys.exit("DATABENTO_API_KEY not found")

def osi(exp,cp,strike): return f"SPXW  {exp:%y%m%d}{cp}{int(round(strike*1000)):08d}"

def day_entries(g):
    """3-bot first-break entries from one day's 5m bars."""
    out={}
    for name,nb in [("bot1",3),("bot2",6),("bot3",12)]:
        w=g.iloc[:nb]; hi=w["high"].max(); lo=w["low"].min(); e=None
        for _,r in g.iloc[nb:].iterrows():
            if r["close"]>hi*(1+BUF): e=("up",int(r["mod"]),float(r["close"])); break
            if r["close"]<lo*(1-BUF): e=("dn",int(r["mod"]),float(r["close"])); break
        if e: out[name]=e
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end")
    a = ap.parse_args()
    done = set()
    if os.path.exists(LEDGER):
        done = set(pd.read_csv(LEDGER)["day"].astype(str))
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today() - dt.timedelta(days=1)
    if a.start:
        start = dt.date.fromisoformat(a.start)
    elif done:
        start = dt.date.fromisoformat(max(done)) + dt.timedelta(days=1)
    else:
        start = end
    if start > end:
        print("Nothing new to price."); return
    bars = rth_only(fetch_5min("^GSPC", start - dt.timedelta(days=1), end))
    bars = bars[(bars.index.date >= start) & (bars.index.date <= end)].copy()
    if not len(bars): print("No new sessions."); return
    bars["day"]=bars.index.strftime("%Y-%m-%d"); bars["mod"]=bars.index.hour*60+bars.index.minute
    import databento as db
    cl = db.Historical(get_db_key())
    rows=[]; run_ts=dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    for day, g in bars.groupby("day"):
        if day in done: continue
        g=g.sort_values("mod")
        if len(g)<15: continue
        ents=day_entries(g)
        if not ents: rows.append({"run_ts":run_ts,"day":day,"bot":"none","dir":"","detail":"no OR break","entry_hm":"","prem":0,"pnl_1ct":0,"note":""}); continue
        exp=dt.date.fromisoformat(day); close=float(g["close"].iloc[-1]); syms=set()
        if "bot1" in ents:
            d,_,px=ents["bot1"]; cp="C" if d=="up" else "P"; atm=round(px/5)*5
            for k in (atm-10,atm-5,atm,atm+5,atm+10): syms.add(osi(exp,cp,k))
        for name,width in [("bot2",W30),("bot3",W60)]:
            if name in ents:
                d,_,px=ents[name]
                if d=="up":
                    s=np.floor(px*(1-S_OTM)/5)*5
                    for k in (s+5,s,s-5,s-width,s-width-5): syms.add(osi(exp,"P",k))
                else:
                    s=np.ceil(px*(1+S_OTM)/5)*5
                    for k in (s-5,s,s+5,s+width,s+width+5): syms.add(osi(exp,"C",k))
        try:
            q=cl.timeseries.get_range(dataset="OPRA.PILLAR",symbols=sorted(syms),stype_in="raw_symbol",
                                      schema="cbbo-1m",start=day,end=(exp+dt.timedelta(days=1)).isoformat()).to_df().reset_index()
        except Exception as e:
            print(f"{day}: quotes not available yet ({str(e)[:60]}) — stopping."); break
        if not len(q): print(f"{day}: no quotes returned — OPRA likely not published yet; stopping."); break
        sc=1e9 if q["ask_px_00"].abs().median()>1e6 else 1.0
        q["ask"]=q["ask_px_00"]/sc; q["bid"]=q["bid_px_00"]/sc
        t=pd.to_datetime(q["ts_event"],utc=True).dt.tz_convert("America/New_York")
        q["mod"]=(t.dt.hour*60+t.dt.minute).astype(int)
        pat=re.compile(r'([CP])(\d{8})$')
        q["cp"]=[pat.search(s).group(1) for s in q["symbol"]]
        q["K"]=[int(pat.search(s).group(2))/1000 for s in q["symbol"]]
        q=q[q["bid"].notna() & (q["ask"]>0)]
        book={}
        for (c,k),gg in q.groupby(["cp","K"]):
            gg=gg.sort_values("mod"); book[(c,k)]=(gg["mod"].values,gg["bid"].values,gg["ask"].values)
        def qat(c,k,mod,col):
            if (c,k) not in book: return None
            mods,bid,ask=book[(c,k)]; i=np.searchsorted(mods,mod,side="right")-1
            if i<0: return None
            v=(bid if col=="bid" else ask)[i]; return float(v) if v>0 else None
        if "bot1" in ents:
            d,mod,px=ents["bot1"]; cp="C" if d=="up" else "P"
            ks=[k for (c,k) in book if c==cp]
            if ks:
                k=min(ks,key=lambda x:abs(x-px)); ea=qat(cp,k,mod,"ask")
                if ea and ea>0.05:
                    mods,bid,_=book[(cp,k)]; mask=mods>mod; r=None;held=None
                    for m,b in zip(mods[mask],bid[mask]):
                        if m-mod>TS_MAIN: r=b-ea; held=int(m-mod); break
                        if b>=L_TGT*ea or b<=L_STP*ea: r=b-ea; held=int(m-mod); break
                    if r is None and mask.any(): r=float(bid[-1])-ea; held=int(mods[-1]-mod)
                    if r is not None:
                        rows.append({"run_ts":run_ts,"day":day,"bot":"bot1_ts30","dir":d,"detail":osi(exp,cp,k),
                                     "entry_hm":f"{mod//60:02d}:{mod%60:02d}","prem":round(ea,2),"pnl_1ct":round(r*100,1),"note":f"held {held}m"})
        for name,width in [("bot2",W30),("bot3",W60)]:
            if name not in ents: continue
            d,mod,px=ents[name]
            cp="P" if d=="up" else "C"
            ks=sorted(k for (c,k) in book if c==cp)
            short=(max([k for k in ks if k<=px*(1-S_OTM)],default=None) if d=="up"
                   else min([k for k in ks if k>=px*(1+S_OTM)],default=None))
            longk=(short-width if d=="up" else short+width) if short else None
            if not short or longk not in ks: continue
            sb=qat(cp,short,mod,"bid"); la=qat(cp,longk,mod,"ask")
            if not sb or la is None: continue
            credit=sb-la
            if credit<=0.05: continue
            ms=book[(cp,short)][0]; ml=book[(cp,longk)][0]
            mins=sorted(set(ms[ms>mod]).union(set(ml[ml>mod]))); r=None; why=""
            for m in mins:
                sask=qat(cp,short,m,"ask"); lbid=qat(cp,longk,m,"bid")
                if sask is None or lbid is None: continue
                cost=sask-lbid
                if cost<=S_TGT_FRAC*credit: r=credit-cost; why="tp50"; break
                if cost>=S_STP_MULT*credit or cost>=width: r=credit-min(cost,width); why="stop"; break
            if r is None:
                intr=(max(0.0,short-close)-max(0.0,longk-close)) if cp=="P" else (max(0.0,close-short)-max(0.0,close-longk))
                r=credit-min(max(intr,0.0),width); why="eod"
            rows.append({"run_ts":run_ts,"day":day,"bot":name,"dir":d,"detail":f"{cp}spread {short:g}/{longk:g}",
                         "entry_hm":f"{mod//60:02d}:{mod%60:02d}","prem":round(credit,2),"pnl_1ct":round(r*100,1),"note":why})
    if not rows:
        print("No rows to append."); return
    new=pd.DataFrame(rows)
    hdr=not os.path.exists(LEDGER)
    new.to_csv(LEDGER, mode="a", header=hdr, index=False)
    per=new.groupby("day")["pnl_1ct"].sum()
    print(f"Appended {len(new)} rows for {new['day'].nunique()} session(s).")
    for d,v in per.items(): print(f"  {d}: {v:+,.1f} $/1ct-set")

if __name__ == "__main__":
    main()
