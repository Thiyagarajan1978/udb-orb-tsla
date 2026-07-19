# SPX ORB Exploration — Full Record (2026-07-18 session)

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
