# SPX ORB Exploration — Full Record (2026-07-18 session)

> ## ⚠ EXPIRY-MERGE CORRECTION 2026-08-19 - EVERY DOLLAR FIGURE IN THIS FILE IS INVALID
>
> This supersedes the barclose correction below, which fixed a different bug and left this one
> in place. The quote book used throughout this record was keyed `(day, cp, strike)` with **no
> expiry**. The OPRA cache holds 2-8 expiries per strike, so exits filled on later-expiry
> contracts - verified on 2023-03-02 CALL 3945, where a $10.10 0DTE entry "exited" on an $81.40
> bid belonging to the 2023-03-24 contract, a fake +$7,060 on a single trade.
>
> Re-pulled 2022-2026 against contracts asserted to expire on the trade day
> (`scripts/spx/repull_0dte.py`, guard `udb_orb.options.assert_expiry`), 1,096 priced sessions,
> **zero guard trips**:
>
> | year | n | net $ @1ct | WR | PF |
> |---|---|---|---|---|
> | 2022 | 219 | -15,725 | 39.7% | 0.78 |
> | 2023 | 246 | -16,585 | 39.4% | 0.68 |
> | 2024 | 250 | -4,795 | 46.0% | 0.91 |
> | 2025 | 250 | **+5,320** | 48.0% | 1.08 |
> | 2026 | 131 | -1,730 | 44.3% | 0.95 |
> | **ALL** | **1096** | **-33,515** | **43.5%** | **0.88** |
>
> **BOT1 ts30 is -$33,515 @1ct, not +$205,025. Only 2025 is positive** - the "positive every
> year" claim does not survive. At the production 3 contracts that is **-$100,545**.
>
> The **0.70% premium skip is inert** on correct data: p99 of entry premium is 0.78% of spot,
> so it removes ~1.5% of trades (-33,515 -> -31,285), and no threshold from 1.0% down to 0.3%
> makes the system positive. The +$116k it appeared to add (+$205,585 -> +$321,815) was fitting
> the contamination. The *direction* survives - expensive-premium entries are the worse cohort -
> but net improves monotonically as the cap tightens, which is the signature of "this book is
> negative, so trade less of it", not of a threshold with an optimum.
>
> Validation: the 2022-23 total reproduces the earlier independent partial re-pull to the
> dollar (-$32,310). **The SIGNAL logic is unaffected** - 1,127 trades, reconciling to Pine
> exactly. Only the P&L attached to them was wrong.
>
> Corrected 2022-23 spread/COMBO figures do not exist yet: `price_hersystem*.py` are the
> infected pricers and are quarantined, so BOT2/BOT3/COMBO numbers here remain unpriced,
> not merely restated.


> **⚠ BARCLOSE CORRECTION 2026-07-24 — the 3-bot dollar figures below are INFLATED.**
> All option entries in this record were priced at the signal bar's **START** quote — a
> 5-minute lookahead (the breakout only exists at the bar's close; same bug found in the
> TSLA forward test on 2026-07-20). Re-run on the same cached OPRA quotes with bar-CLOSE
> entries (`scripts/spx/price_hersystem_ts30.py`, fixed; 1,137 sessions 2022-01→2026-07):
>
> | @1ct, 2022–2026 | bar-START (this doc) | bar-CLOSE (corrected) | Δ |
> |---|---|---|---|
> | BOT1 ts30 | +$457,265 (WR 63%, PF 2.73) | **+$205,025** (WR 48%, PF 1.50, worst day −$6,980) | −55% |
> | BOT2 | +$289,550 | **+$245,665** | −15% |
> | BOT3 | +$341,936 | **+$236,981** | −31% |
> | COMBO | +$1,088,751 | **+$687,671** (worst day −$5,880) | −37% |
>
> **Every bot remains positive every year 2022–2026** (weakest: BOT1 2024 +$14,715) — the
> system survives the correction at roughly ⅔ scale overall. `forward_test_spx.py`
> was corrected and its ledger rebuilt the same day.
>
> **Secondary findings RE-VERIFIED on corrected fills (2026-07-24):**
> - **ts30 adoption HOLDS, stronger**: BOT1 ts30 +$205,025 vs baseline +$160,485 (+28%, was
>   "+9%"); ts60/ts90 slightly worse; time-stopping the spread bots still hurts (all-ts30
>   COMBO +$626,625 < B1-only-ts30 +$687,671). Median BOT1 hold 28 min.
> - **Premium terciles — SHARPER than before**: cheap PF 4.20 (+$127,830), mid PF 2.29
>   (+$138,505), **rich tercile now NET NEGATIVE** (−$61,310, PF 0.77, WR 43%, hosts the
>   −$6,980 worst day). Fixed-DOLLAR sizing is now near-mandatory: $1k-risk/day on BOT1
>   (skip if 1ct risk > $1k → auto-skips rich days) = +$452,985 over 4.5yr, worst day −$1,610,
>   trades 726/1107 days.
> - **Weekday: every weekday still net-positive** (Mon best +$63,925; Fri weakest +$27,760).
> - **Losing months (COMBO, B1 ts30): 5 of 55** (was 2/55), worst month −$4,235, median +$8,401.
> - **TSLA cross-test COLLAPSED**: the 3-bot on TSLA was +$45,916 under the lookahead → 
>   **−$2,295 barclose** (BOT1, WR 44%; all spread bots negative). 3-bot is SPX-ONLY, full stop.

Recovered from the interrupted 2026-07-18 session. Scripts live in `scripts/spx/`,
final trade CSVs + run logs in `exports/spx/` (gitignored), option-quote caches in
`data/cache/spx/` (gitignored, ~315MB — REAL Databento OPRA SPXW quotes; do not delete,
the Databento account is out of funds and cannot re-pull).

## Phase 1 — ORB on SPX underlying, many param variants: NO EDGE
Tested standard 5m ORB plus variants: 15m/30m opening range, fixed-time exits,
profit-% targets. Verdict across six structurally different angles:
- Plain breakout: **coin flip** (PF ~1.0) — SPX is an efficient index; the
  "breakout continues" premise that works on TSLA fails on SPX.
- 15/30m OR variants that looked good on one year: **curve-fit**, died OOS.
- Fixed-time exit: too thin (~2.5%/yr, futures-grade).
- Buying cheap 0DTE options: bull-market beta (87% calls), not edge.
- Selling credit spreads held to expiry: outright loses (−$42,680 @10ct 2024-26;
  89% day-WR but needs 92% to break even — the 35 max-loss days sink it).

**Verdict: NO-GO on SPX share/underlying-style ORB in any form.**

## Phase 2 — the influencer's actual system ("her system", managed early)
User clarified her real setup: three concurrent ORB bots, all exits managed early
(minutes), NOT held to expiry — invalidating the hold-to-expiry loss above.

Replication params (`scripts/spx/price_hersystem.py`), both directions
(up-break → long call + put credit spreads; down-break → mirrored):
- Buffer 0.05% close-break of the OR.
- **Bot 1**: 15m OR break → long ATM 0DTE, TP +50% / SL −50% of premium.
- **Bot 2**: 30m OR break → 5-wide credit spread, short leg ~0.30% OTM against the
  move, close at 50% of credit, stop at 2× credit.
- **Bot 3**: 60m OR break → 10-wide credit spread, same management.
- Priced against real OPRA cbbo-1m (buy ask / sell bid), 2024-01 → 2026-07 (636 days).

### Results (per 1 contract; ×10 = her stated size)
| Bot | #days | WR | avg/day | net 2024-26 | worst day |
|---|---|---|---|---|---|
| 15m long ATM | 611 | 63% | +$308 | +$188,225 | −$6,480 |
| 30m 5-wide spread | 413 | 57% | +$228 | +$93,940 | −$450 |
| 60m 10-wide spread | 388 | 69% | +$431 | +$167,221 | −$820 |
| **COMBINED** | 628 | 66% | +$716 | **+$449,386** | −$6,740 |

Zero losing months in 31. Survives $0.35/contract slippage stress.

### Validation diagnostics (the step the session closed during — run COMPLETED)
- **Null control** (buy ATM call at fixed 10:00 daily, NO signal): 579d, 50% WR,
  **−$18,465** net. → The profit is NOT buy-calls-in-a-bull beta; the ORB timing
  signal is doing real work.
- **Direction-balanced**: up-breaks 329d WR 63% (+$73,265); down-breaks 282d WR 63%
  (+$114,960). Both sides profitable — down-breaks earn MORE. Not bull beta.
- **Not a quote artifact**: median entry premium $1,240/ct (p10 $340, p90 $5,350);
  median hold 34 min; only 5% exit ≤1 min.

### Reconciling with Phase 1's "coin flip"
The underlying edge IS ~50/50 directionally, but the option structure is convex:
the +50%/−50% bracket on a 0DTE ATM option monetizes post-breakout *movement*
(gamma) within ~34 min, and the early-managed spreads harvest the high base rate of
"doesn't reverse through the whole range in minutes". Structure, not direction,
carries the P&L — which is why the null control (same structure, no timing) loses.

### Remaining caveats before any real money
1. cbbo-1m cannot sequence TP vs SL *within* a minute — bracket fills may be
   optimistic on fast bars (worst day −$6,740 @1ct → −$67k at her 10-lot).
2. 30m spread degraded in 2026 (WR 47%) — weakest component.
3. 2024-26 contains no bear market; the 2022-style stress test is impossible
   (SPXW 0DTE data + the account budget don't reach it).

**Recommendation**: signal survives every falsification test run so far; treat like
TSLA — forward-test it (shadow ledger) before committing capital. TSLA remains the
primary validated system.

## Phase 3 — refinement + ports (2026-07-18, this repo)
**30-min time stop on Bot 1 (ADOPTED for the SPX spec)**: brackets active 30 min, then
flatten. +$17,525 net (+9% on Bot 1), combined +$466,911, WR 66→70%, every year improves,
median hold 31 min, 2025 worst day −23%. 60/90-min stops worse; time-stopping the spreads
kills the gain (leave them credit-managed). `scripts/spx/price_hersystem_ts30.py`,
results `exports/spx/ts30.log`.

**TradingView indicator**: `pine/SPX_ORB_3BOT_v1.pine` — standalone, alerts-only,
deliberately separate from the UDB-ORB TSLA suite. 15/30/60m OR, per-bot alerts,
strike suggestions, 30-min time-stop alert.

**Same concept on TSLA** (`scripts/spx/price_3bot_tsla.py`, real OPRA quotes, nearest
weekly expiry DTE 0-4; 2022-09→2023-12 incl. the bear + 2025-01→2026-07; 2024 uncached):
- **Bot 1 works**: +$36,103 @1ct over 609 days, 65% WR, EVERY year green incl. late-2022
  bear (+$3,983), worst day only −$345, 2 losing months in 43. Concept is real on TSLA
  and far better tail-per-dollar than SPX (avg/worst 0.17 vs 0.05).
- **Bots 2/3 (credit spreads) do NOT translate**: rarely set up ($2.50 strikes, weekly
  expiry, thin credits vs width) and net NEGATIVE at both 0.3% and 1.0% OTM. Skip them.
- **BUT the existing UDB TSLA options system is ~4× better** (~+$65-70k/yr @1ct 2025-26
  vs Bot 1's ~+$16k/yr): the tuned 5m-OR engine with partials/trails beats the simple
  ±50% bracket on the same symbol. So: no change to the TSLA system; the 3-bot concept
  is an SPX-specific play.

## Phase 4 — FULL history 2022-2026 (2026-07-19, after Databento top-up)
Gaps closed: SPX 2022-23 (`pull_3bot_spx2223.py` → `data/cache/spx/chunk2223_*.parquet`,
^GSPC 5m via FMP) and TSLA 2024 (`pull_3bot_tsla2024.py` → `data/cache/opra/quotes_2024_*`).
Targeted pulls: only the strikes each day's 3-bot trades can touch.

**Bot 1 ts30, per 1 contract — the bear-market caveat is now CLOSED:**
| yr | SPX days | WR | net | PF | worst d | TSLA days | WR | net | PF | worst d |
|---|---|---|---|---|---|---|---|---|---|---|
| 2022 | 247 | 63% | +$128,645 | 2.49 | −$6,590 | 80 | 61% | +$3,983 | 3.74 | −$155 |
| 2023 | 247 | 65% | +$122,870 | 2.56 | −$5,540 | 217 | 66% | +$9,797 | 3.41 | −$175 |
| 2024 | 246 | 62% | +$58,650 | 2.77 | −$4,440 | 241 | 61% | +$9,813 | 2.51 | −$255 |
| 2025 | 239 | 62% | +$91,010 | 3.17 | −$2,990 | 190 | 67% | +$15,883 | 3.42 | −$310 |
| 2026 | 126 | 67% | +$56,090 | 3.40 | −$6,480 | 122 | 64% | +$6,440 | 2.54 | −$345 |
| ALL | 1105 | 63% | **+$457,265** | **2.73** | −$6,590 | 850 | 64% | **+$45,916** | **3.02** | −$345 |

SPX 2022 (bear, −19% index year) was Bot 1's BEST dollar year — high IV premiums cut PF
but grow dollars; the timing edge held in every regime. Losing months: SPX 2/55,
TSLA 2/47. SPX 3-bot COMBO full history: **+$1,088,751 @1ct** (ts30 on Bot 1).
2022 spreads printed heavily (high credits); 2024 TSLA spreads (fresh full-coverage
data) confirm the TSLA-spread verdict: B2 negative, B3 ~flat — still skip on TSLA.

**Sizing on full history** ($10k, risk/ct = half premium): 2% fixed → SPX $37.6k /
TSLA $35.2k, maxDD 1.8% both; 10% fixed $1k → SPX $855k (maxDD 8.2%) / TSLA $239k
(3.2%); 10% compound cap-10ct → SPX $4.42M (maxDD 24.2%) / TSLA $462k (4.7%).
Same fill-optimism caveats as ever; forward-test before capital.

---

## Phase 5 — the SECOND AUTOMATED LEG: BOT3-LONG (2026-08-07)

### Why: the spreads were never automatable
BOT2/BOT3 have existed only as `alertcondition()` + chart labels. Neither ever had a
`strategy.entry()`, and the TradersPost payload
(`{"ticker":"SPX","action":"buy","expiration":"+0 days","optionType":"call","strikesAway":0}`)
is a **single-leg construct with no spread syntax**. To automate a second leg it has to be
single-leg. Execution study (2026-08-06) quantified the gap: BOT1's ATM option quotes at a
**1.5%**-of-mid spread vs BOT2's combined 2-leg spread at **26% of the credit received**;
BOT1 crosses 2 legs, BOT2 crosses 4; breakeven slippage $0.93/leg vs $0.76.

### Which OR to convert — NOT the 30m
Single-leg candidates paired with BOT1 (15m long), 2022-01..2026-07:

| partner for BOT1 | corr | same-dir | pair net @1ct | worst day | net/stdev |
|---|---|---|---|---|---|
| 5m long option | +0.49 | 84% | +322,300 | −10,760 | 131.1 |
| 30m long option | +0.55 | 87% | +428,230 | −13,960 | 167.9 |
| **60m long option** | **+0.22** | 75% | +404,795 | −9,400 | **188.6** |
| 30m SPREAD (shipped, manual) | +0.09 | 87% | +450,980 | −5,600 | 255.8 |

*(all long variants at a common 30-min stop for the comparison — BOT3L's own 50-min tuning below.)*

The 30m long is **near-duplicate BOT1**: 87% same direction, median entry gap **0 min**,
correlation +0.55, both lose together 40% of shared days. Converting it doubles one bet.
Waiting the full hour decorrelates it. On identical 30m signals the spread also beats the long
option by −$98,930 (P(long better) = **3%**) — so the diversification in the original design
came from the **instrument** (long premium vs short premium), not the OR length.

**We knowingly give up ~10% of paper edge (+450,980 → +404,795 pre-skip) to gain automation.**
That trade survives fill costs: at $0.10/leg extra, spread-pairing +396,360 vs all-single-leg
+384,370 — closer than the raw numbers suggest, because the spread crosses twice as many legs.

### BOT3-LONG parameters — validated INDEPENDENTLY; they do not match BOT1
**Time stop 50 min, not BOT1's 30.** Paired on the identical 1,010 trades:

| hold | Δ vs 30m | t | P(better) | 95% CI | years won |
|---|---|---|---|---|---|
| 40m | +27,345 | 1.85 | 98% | [+964, +58,138] | 4/5 |
| **50m** | **+41,970** | **2.48** | **100%** | **[+10,838, +77,780]** | **5/5** |
| 60m | +40,060 | 2.19 | 99% | [+5,266, +78,042] | 4/5 |
| 75m | +43,635 | 2.09 | 98% | [+3,440, +86,181] | 4/5 |

Broad plateau (40–75), CI excludes zero, and walk-forward hold selection beat flat-30 in **4/4
live years**. Mechanism: BOT3 enters ~10:40+, past the opening gamma burst, so the move needs
longer. **Contrast BOT1, where the same test rejected a longer hold** — that one was an
in-sample mirage (fitted +24,545 → walk-forward −2,435).

**Premium skip = 0.45% of spot (RELATIVE).** The rich tercile is net negative here exactly as on
BOT1: −$41,270, PF 0.80, WR 41%, and it hosts the −$5,310 worst day. Skipping: **+$269,330**,
PF 3.10, worst −$2,150, walk-forward beat no-skip in **3/4 live years**, per-year
+58,860 / +74,195 / +48,750 / +47,700 / +39,825 — stable, no decay.

*(Figures are from the shipped `forward_test_spx.py` code path, which measures the 0.45%
threshold against the **entry-bar** spot. The exploratory sweep used the session close — a mild
lookahead — and read +268,550 / PF 3.08. The production number above is the lookahead-free one.)*

### ⚠ WARNING — the FIXED $2,000 skip on BOT1 is drifting
A fixed dollar threshold on a premium that scales with the index **silently tightens as SPX
rises**: $2,000 was 0.49% of spot in 2022, only 0.28% in 2026.

| BOT1 rule | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| no skip | +58,425 | +63,415 | +14,775 | +40,945 | +27,755 |
| skip > $2,000 (live rule) | +113,015 | +124,595 | +22,525 | **+6,425** | **+3,965** |
| skip > 0.45% of spot | +95,145 | +123,445 | +25,755 | +19,535 | +17,895 |

It nearly doubles the early years and **guts the recent ones**. The headline **+$452,985 is not
a go-forward expectation.** The relative cap recovers much of it but beat no-skip in only **2/4**
live years on BOT1 — not clean enough to adopt off a sweep. **BOT1's live rule is left UNCHANGED
pending forward evidence.** If using TradersPost sizing, revisit "Amount per position" as SPX
moves rather than leaving it fixed.

### Production portfolio (per 1 contract, 2022-01-04 .. 2026-07-15)

| leg | trades | WR% | net @1ct | PF | worst day | +yrs |
|---|---|---|---|---|---|---|
| BOT1 15m long, ts30, skip>$2k (live rule) | 726 | 51.5 | +270,525 | 2.93 | −1,510 | 5/5 |
| BOT3L 60m long, ts50, skip>0.45% spot | 655 | 53.6 | +269,330 | 3.10 | −2,150 | 5/5 |

| portfolio | net @1ct | stdev/day | worst day | net/stdev |
|---|---|---|---|---|
| BOT1 alone, flat 1ct | +205,315 | 1,468 | −6,980 | 139.9 |
| BOT1 + BOT3L, both flat, no skips | +446,765 | 2,216 | −9,400 | 201.6 |
| **BOT1 + BOT3L with skips (PRODUCTION)** | **+532,525** | 1,801 | **−2,680** | **295.6** |

Per-year production: 2022 +171,875 · 2023 +198,790 · 2024 +70,075 · 2025 +47,595 · 2026 +44,190.
Both legs traded on 523 days, BOT1 only 203, BOT3L only 132; both lost on 28% of days;
leg correlation +0.57 *after* the skips (the skips remove different days from each leg).
Median premium ~$1,080 (BOT1) + ~$1,070 (BOT3L) ⇒ **~$2,150 committed with both legs open**.

All figures reconciled against the shipped `forward_test_spx.py` module (imported, not retyped):
BOT1 reproduces +205,315 / 1,105 trades exactly, confirming the shared-loop refactor is neutral.
Caveat: the +544,495 row inherits BOT1's drifting fixed cap, so treat it as an upper bound —
BOT3L's leg is the one with the stable per-year profile.

### Files
- `pine/SPX_ORB_BOT3L_60M_v1_strategy.pine` — NEW, orders + time stop + EOD failsafe.
  **Separate strategy on its own chart/alert** — TradingView nets positions and the two bots
  disagree on direction 25% of days, so merging would silently flatten BOT1.
- `pine/SPX_ORB_3BOT_v1.pine` v1.2 — "Bot3 instrument" mode (Long ATM / legacy spread),
  50-min stop, relative skip, BOT3-LONG alerts.
- `forward_test_spx.py` — prices `bot3_long_ts50` daily and flags `SKIPPED` rows. NOTE it prices
  bot3 BOTH ways (spread and long); only one is production, so summing every row overstates.
