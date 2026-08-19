"""Out-of-sample SPX 3-BOT forward test (Databento shadow, no broker, no money).

Prices the FROZEN SPX 3-bot system's new-session signals against REAL SPXW 0DTE quotes
(OPRA cbbo-1m, buy-ask / sell-bid) and APPENDS to exports/forward_spx_ledger.csv.
Bot1 = 15m long ATM +50%/-50% with the ADOPTED 30-min time stop; Bot2/3 = 5/10-wide
credit spreads ~0.30% OTM against the move, close @50% credit, stop @2x.
Bot3-LONG (added 2026-08-07, the second AUTOMATED leg) = the 60m OR traded as a long ATM
option with a 50-min time stop; rows carry a SKIPPED flag when the premium exceeds 0.45%
of spot, so the skip rule stays measurable rather than baked in.
Idempotent; OPRA releases T+1. Underlying: ^GSPC 5m via FMP.

NOTE the ledger prices bot3 BOTH ways each day — as a spread (bot=bot3) and as a long option
(bot=bot3_long_ts50). Only ONE of them is the production leg; summing every row overstates.

Usage:
    python forward_test_spx.py                              # new sessions since ledger
    python forward_test_spx.py --start 2026-07-17 --end 2026-07-17
"""
import argparse, os, re, sys, time, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import pandas as pd, numpy as np
from udb_orb.data.fmp_client import fetch_5min, rth_only
from udb_orb.options import ExpiryMismatch, assert_expiry

ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(ROOT, "exports", "forward_spx_ledger.csv")
BUF=0.0005; L_TGT,L_STP=1.50,0.50; S_OTM=0.0030; S_TGT_FRAC=0.50; S_STP_MULT=2.0
TS_MAIN=30; W30,W60=5,10
# BOT3-LONG (added 2026-08-07): the 60m OR traded as a single-leg long ATM option instead of the
# un-automatable 10-wide spread. Its two parameters were validated INDEPENDENTLY of BOT1 and do
# NOT match it — 50-min time stop (beats 30m by +$41,970, t=2.48, wins 5/5 years) and a RELATIVE
# premium skip at 0.45% of spot (a fixed-$ cap silently tightens as SPX rises). See
# pine/SPX_ORB_BOT3L_60M_v1_strategy.pine and docs/SPX_ANALYSIS.md.
# The ledger records the trade EITHER WAY and flags the skip, so the skip rule stays measurable
# forward instead of being baked in unobservably.
TS_B3L=50; B3L_CAP_PCT=0.0045

def wait_for_network(timeout=120, host="hist.databento.com"):
    """Block until DNS resolves, or timeout. Smart App Control's verdict on an unsigned binary
    comes from a CLOUD reputation lookup, so starting offline is an automatic block — and the
    9 AM task fires seconds after the machine wakes. See forward_test.py for the full note."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.getaddrinfo(host, 443)
            return True
        except OSError:
            time.sleep(5)
    print(f"network still unreachable after {timeout}s — importing anyway.", flush=True)
    return False

def import_databento(attempts=20, delay=15, max_delay=60):
    """Import databento, retrying Windows Smart App Control blocks of the native extension.

    ROOT CAUSE 2026-08-06: databento_dbn's UNSIGNED _lib .pyd is refused by Smart App Control
    (CodeIntegrity events 3077 + 3118) whenever its cloud reputation lookup doesn't come back
    clean. Through 07-31 the old 4x15s window cleared it on attempt 2; on 08-04..08-06 it never
    cleared and three runs died. Now: wait for network, then back off over ~12 min.
    Full explanation and the durable fallback (drop the SDK for the HTTP API) in forward_test.py.
    """
    wait_for_network()
    for i in range(1, attempts + 1):
        try:
            import databento as db
            if i > 1: print(f"databento imported on attempt {i}.", flush=True)
            return db
        except ImportError as e:
            for mod in [m for m in list(sys.modules) if m.split(".")[0] in ("databento", "databento_dbn")]:
                del sys.modules[mod]
            if i == attempts:
                sys.exit(f"databento import failed after {attempts} attempts — likely a Smart App "
                         f"Control block; check CodeIntegrity/Operational event 3077: {e}")
            wait = min(delay * (1 + i // 5), max_delay)
            print(f"databento import blocked (attempt {i}/{attempts}): {e}\n  retrying in {wait}s...", flush=True)
            time.sleep(wait)

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
    """3-bot first-break entries from one day's 5m bars.

    BARCLOSE FIX 2026-07-24: the recorded minute is the signal bar's CLOSE (index mod + 5),
    not its start — the breakout only exists once the bar closes, so pricing the entry at the
    bar-start quote was a 5-minute LOOKAHEAD (the same bug fixed in the TSLA forward test on
    2026-07-20). All downstream anchors (entry ask, time stop, spread management, held-minutes)
    key off this minute, so they are all fill-relative now. Ledger rebuilt same day.
    """
    out={}
    for name,nb in [("bot1",3),("bot2",6),("bot3",12)]:
        w=g.iloc[:nb]; hi=w["high"].max(); lo=w["low"].min(); e=None
        for _,r in g.iloc[nb:].iterrows():
            if r["close"]>hi*(1+BUF): e=("up",int(r["mod"])+5,float(r["close"])); break
            if r["close"]<lo*(1-BUF): e=("dn",int(r["mod"])+5,float(r["close"])); break
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
    db = import_databento()
    cl = db.Historical(get_db_key())
    rows=[]; run_ts=dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    for day, g in bars.groupby("day"):
        if day in done: continue
        g=g.sort_values("mod")
        if len(g)<15: continue
        ents=day_entries(g)
        if not ents: rows.append({"run_ts":run_ts,"day":day,"bot":"none","dir":"","detail":"no OR break","entry_hm":"","prem":0,"pnl_1ct":0,"note":""}); continue
        exp=dt.date.fromisoformat(day); close=float(g["close"].iloc[-1]); syms=set()
        for _b in ("bot1","bot3"):            # bot3 ATM strikes = the new BOT3-LONG leg
            if _b in ents:
                d,_,px=ents[_b]; cp="C" if d=="up" else "P"; atm=round(px/5)*5
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
        # EXPIRY GUARD (2026-08-19). This ledger has always been clean -- it pulls a ONE-SESSION
        # window and only ever requests symbols expiring `exp` -- but nothing asserted it, which is
        # exactly how scripts/spx/price_hersystem_ts30.py merged 2-8 expiries into one series for
        # years without anyone noticing. Assert it, so "clean by construction" stays true if the
        # construction is ever edited. Skip the day rather than kill a scheduled run.
        try:
            for _s in q["symbol"].unique(): assert_expiry(_s, exp, context=f"forward_spx {day}")
        except ExpiryMismatch as _e:
            print(f"{day}: EXPIRY GUARD TRIPPED, day NOT priced -- {_e}"); continue
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
        # ---- long-ATM legs: BOT1 (15m OR, 30-min stop) and BOT3-LONG (60m OR, 50-min stop) ----
        for name, ts, tag in [("bot1", TS_MAIN, "bot1_ts30"), ("bot3", TS_B3L, "bot3_long_ts50")]:
            if name not in ents: continue
            d,mod,px=ents[name]; cp="C" if d=="up" else "P"
            ks=[k for (c,k) in book if c==cp]
            if not ks: continue
            k=min(ks,key=lambda x:abs(x-px)); ea=qat(cp,k,mod,"ask")
            if not ea or ea<=0.05: continue
            mods,bid,_=book[(cp,k)]; mask=mods>mod; r=None;held=None
            for m,b in zip(mods[mask],bid[mask]):
                if m-mod>ts: r=b-ea; held=int(m-mod); break
                if b>=L_TGT*ea or b<=L_STP*ea: r=b-ea; held=int(m-mod); break
            if r is None and mask.any(): r=float(bid[-1])-ea; held=int(mods[-1]-mod)
            if r is None: continue
            note=f"held {held}m"
            if name=="bot3":
                # Record the trade EITHER WAY and flag it, so the relative premium skip stays
                # measurable forward. Judge BOT3-LONG on rows WITHOUT "SKIPPED".
                skip_usd = px * B3L_CAP_PCT * 100
                if ea*100 > skip_usd:
                    note += f" | SKIPPED prem ${ea*100:,.0f} > ${skip_usd:,.0f} (0.45% spot)"
            rows.append({"run_ts":run_ts,"day":day,"bot":tag,"dir":d,"detail":osi(exp,cp,k),
                         "entry_hm":f"{mod//60:02d}:{mod%60:02d}","prem":round(ea,2),
                         "pnl_1ct":round(r*100,1),"note":note})
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
