"""Replay the Pine v1.3.1 BOT1 state machine on the cached SPX 5m bars.

Purpose: produce the EXPECTED trade list so a TradingView Strategy Tester re-run can be
reconciled trade-by-trade instead of judged on a headline number. Mirrors the Pine exactly:
  rthIx 0..2  -> 15m opening range (09:30/09:35/09:40 bars, OR closes 09:45)
  entry       -> first bar with rthIx > 2 and rthIx < 77 whose CLOSE breaks OR +/- 0.05%
  exit        -> bar_index >= entry_bar + 6 (30-min time stop), or rthIx >= 76 (15:50 EOD)
  one trade per session (b1Dir latches)
Timestamps are the BAR OPEN, which is what TradingView prints.
"""
import pandas as pd, sys

BUF = 0.0005     # bufPct: input 0.05 * 0.01
TS_BARS = 6      # round(30 / 5)
EOD_IX = 76      # rthIx >= 76 -> 15:50 bar onward
LAST_ENTRY_IX = 77  # rthIx < 77 blocks the 15:55 entry (v1.2.1 guard)

frames = []
for f in ["data/cache/spx/spx_5m_2022_2023.parquet",
          "data/cache/spx/spx_5m_2024_2025.parquet",
          "data/cache/spx/spx_5m.parquet"]:
    frames.append(pd.read_parquet(f))
b = pd.concat(frames)
b = b[~b.index.duplicated(keep="last")].sort_index()
b = b.between_time("09:30", "15:55")
b["day"] = b.index.date

rows = []
for day, g in b.groupby("day"):
    g = g.sort_index()
    if len(g) < 4:
        continue
    hi15 = g["high"].iloc[0:3].max()
    lo15 = g["low"].iloc[0:3].min()
    up_lvl, dn_lvl = hi15 * (1 + BUF), lo15 * (1 - BUF)

    ent_ix = None
    for ix in range(3, min(len(g), LAST_ENTRY_IX)):
        c = g["close"].iloc[ix]
        if c > up_lvl:
            ent_ix, d = ix, 1
            break
        if c < dn_lvl:
            ent_ix, d = ix, -1
            break
    if ent_ix is None:
        continue

    ts_ix = ent_ix + TS_BARS
    eod_ix = next((i for i in range(ent_ix + 1, len(g)) if i >= EOD_IX), None)
    cand = [i for i in (ts_ix, eod_ix) if i is not None and i < len(g)]
    ex_ix = min(cand) if cand else len(g) - 1
    reason = "Time stop 30m" if ex_ix == ts_ix else "EOD"

    ep, xp = g["close"].iloc[ent_ix], g["close"].iloc[ex_ix]
    rows.append(dict(day=str(day),
                     dir="up" if d == 1 else "dn",
                     entry_ts=g.index[ent_ix].strftime("%Y-%m-%d %H:%M"),
                     entry_px=round(ep, 2),
                     exit_ts=g.index[ex_ix].strftime("%Y-%m-%d %H:%M"),
                     exit_px=round(xp, 2),
                     reason=reason,
                     bars_held=ex_ix - ent_ix,
                     idx_pts=round((xp - ep) * d, 2),
                     or_hi=round(hi15, 2), or_lo=round(lo15, 2),
                     skip_usd_070=round(g["close"].iloc[ent_ix] * 0.70, 0)))

t = pd.DataFrame(rows)
t["yr"] = pd.to_datetime(t["day"]).dt.year
out = "exports/spx/EXPECTED_bot1_trades_v131.csv"
t.to_csv(out, index=False)

print("wrote %s  (%d trades, %s -> %s)" % (out, len(t), t["day"].min(), t["day"].max()))
print("\n=== expected trades per year ===")
print(t.groupby("yr").agg(trades=("idx_pts", "size"),
                          idx_pts=("idx_pts", "sum"),
                          time_stop=("reason", lambda s: (s == "Time stop 30m").sum()),
                          eod=("reason", lambda s: (s == "EOD").sum())).to_string())

print("\n=== the window your export covered (2025-08-18 .. 2026-04-13) ===")
w = t[(t["day"] >= "2025-08-18") & (t["day"] <= "2026-04-13")]
print("EXPECTED trades in that window : %d      (your export showed 95)" % len(w))
print("entry-time range               : %s .. %s" %
      (w["entry_ts"].str[-5:].min(), w["entry_ts"].str[-5:].max()))
print("longs / shorts                 : %d / %d" % ((w["dir"] == "up").sum(), (w["dir"] == "dn").sum()))

# cross-check direction against the OPRA backtest
bt = pd.read_csv("exports/spx/hersystem_ts30_trades.csv")
bt["day"] = pd.to_datetime(bt["day"]).dt.strftime("%Y-%m-%d")
bt = bt[bt["b1_dir"].notna() & (bt["b1_dir"] != 0)][["day", "b1_dir"]]
m = t.merge(bt, on="day", how="inner")
agree = (m["dir"] == m["b1_dir"]).mean() * 100
print("\n=== cross-check vs OPRA backtest directions ===")
print("days in both  : %d" % len(m))
print("direction agreement : %.2f%%" % agree)
bad = m[m["dir"] != m["b1_dir"]]
if len(bad):
    print("disagreements:")
    print(bad[["day", "dir", "b1_dir", "entry_ts"]].to_string(index=False))

# ==================== TEST CARD ====================
t = pd.read_csv("exports/spx/EXPECTED_bot1_trades_v131.csv")
t["yr"] = pd.to_datetime(t["day"]).dt.year

print("=== A. TRADES THE OLD RUN DROPPED — these MUST now appear ===")
print("   (your export's last entry was 2026-04-13; everything below was missing)")
g = t[t["day"] > "2026-04-13"]
print("   count after 2026-04-13, through the data I hold (2026-07-16): %d\n" % len(g))
print(g.head(12)[["day", "dir", "entry_ts", "entry_px", "exit_ts", "reason", "idx_pts"]].to_string(index=False))

print("\n=== B. THE 48-DAY HOLE — 2025-12-19 .. 2026-02-05 ===")
h = t[(t["day"] > "2025-12-19") & (t["day"] < "2026-02-05")]
print("   expected trades inside that hole: %d  (old run showed 0)\n" % len(h))
print(h.head(8)[["day", "dir", "entry_ts", "entry_px", "reason", "idx_pts"]].to_string(index=False))

print("\n=== C. THE 3 MARGIN-CALLED SESSIONS — must now be clean full-size exits ===")
for d in ["2025-11-11", "2026-02-17", "2026-03-10"]:
    r = t[t["day"] == d]
    if len(r):
        r = r.iloc[0]
        print("   %s  %s  entry %s @ %.2f  ->  exit %s @ %.2f  %s  (%+.2f pts)"
              % (d, r["dir"], r["entry_ts"][-5:], r["entry_px"], r["exit_ts"][-5:],
                 r["exit_px"], r["reason"], r["idx_pts"]))

print("\n=== D. WHAT THE TESTER SHOULD SHOW (index points, NOT dollars of edge) ===")
s = t.groupby("yr")["idx_pts"].agg(["size", "sum"])
s.columns = ["trades", "index_points"]
print(s.to_string())
print("   TOTAL 2022-2026: %d trades, %+.1f index points" % (len(t), t["idx_pts"].sum()))
print("   -> at 1 unit that is %+.0f USD on the tester, over 4.5 years." % (t["idx_pts"].sum() * 1))
print("   The SAME trades on real 0DTE options = +$205,025 @1ct (no skip),")
print("   +$321,815 with the 0.70%% skip. THE TESTER CURVE IS NOT THE EDGE.")

print("\n=== E. STRUCTURAL CHECKS (must all hold) ===")
print("   entry times            : %s .. %s   (never 15:55 - the rthIx<77 guard)"
      % (t["entry_ts"].str[-5:].min(), t["entry_ts"].str[-5:].max()))
print("   bars held              : %s" % t["bars_held"].value_counts().to_dict())
print("   exits by reason        : %s" % t["reason"].value_counts().to_dict())
print("   longs / shorts         : %d / %d" % ((t["dir"] == "up").sum(), (t["dir"] == "dn").sum()))
print("   max 1 trade per session: %s" % (t["day"].duplicated().sum() == 0))
