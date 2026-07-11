# Pine port — two scripts

Both mirror the Python engine's **current default config** (`config/config.yaml`) on TSLA 5m.

| File | Type | Use it for |
|------|------|-----------|
| `UDB_ORB_TSLA_v2_strategy.pine` | `strategy()` | **Strategy Tester** → List of Trades + Performance Summary |
| `UDB_ORB_TSLA_v2.pine` | `indicator()` | Chart + alerts for paper trading, custom summary table |

## Install
1. TradingView → **Pine Editor** → paste the file → **Add to chart**.
2. Chart must be **TSLA, 5-minute, Regular Trading Hours**.
3. Strategy version: open the **Strategy Tester** tab for the trade list.
   Indicator version: the **Summary Table** (top-right) reports trades, win rate, net P&L, PF and
   worst day — the same definitions the Python engine uses (**a BE Stop counts as a failure**).

## Execution model — RESTING STOP (adopted 2026-07-11)
Stop-type exits are now **real resting stop orders**: `strategy.exit(stop=...)` fills **intrabar at
the stop level**, or at the **open** on a gap. This is *not* the zero-slippage fantasy — it is the
honest model of a broker OCO stop, and it requires you to actually **place resting stops**. It lifts
3-year net **+18% (+$295 → +$349/unit)** and roughly **halves the worst day (−12.3 → −7.8)**, because
a BE stop now fills at ~entry instead of a bar close far below it.

| Event | Order type | Fills at |
|-------|-----------|----------|
| Entry / reversal entry | market | signal bar's **close** |
| 25% partial at TP (`P1`) | **limit** | the **TP level** |
| BE/Base stop (`SL`) · runner-trail (`TR`) | **resting stop** | the **level**, or the **open** on a gap |
| EOD | market | the flatten bar's **close** |

Two labelled resting stops (`SL` = BE/base, `TR` = runner peak-trail) let the reversal arm correctly:
it arms only when the primary exits via `SL`, never via `TR` or EOD.

**Residual divergence to expect:** a resting order can only move *after* a bar completes, so TradingView
applies a newly-armed BE stop one bar later than the engine. On a bar that both arms BE *and* stops out,
TV fills at the base SL while Python fills at ~entry — TV is slightly more conservative there. Set
"Stop exits fill at bar CLOSE" **ON** to reproduce the old alerts-only close-fill numbers.

## Alerts (for paper trading)
Right-click chart → **Add Alert** → Condition: this indicator → **"Any alert() function call"**.
One subscription delivers every event, each message self-identifying:
`[ENTRY]`, `[ENTRY-REV]`, `[STATE]`, `[EXIT-PARTIAL]`, `[EXIT-WIN]`, `[EXIT-LOSS]`, `[EXIT-EOD]`.

## Parity with the Python engine (verified value-by-value)

| Setting | Value |
|---|---|
| EOD exit | 15:50 ET |
| Breakout buffer | 10% of OR width |
| Stop | OR low (long) / OR high (short) |
| Adaptive TP | `max($2.14, OR × 1.0)` |
| BE retrace | trigger 0.55 × OR (**wick**-based), trail $0.25 |
| Partial | 25% at TP, 75% runs |
| Runner peak-trail | 0.75 × OR below the post-partial peak |
| Reversal | raw opposite OR break, **trails to EOD** (no TP, no partial) |
| Reversal size | `min(2×, $6 / risk_per_unit)` — **risk parity** |
| Max OR width | skip day if > $8 |
| Volatility gate | skip day if prior-20d realised daily vol > 4.92% |
| Stop fills | **resting stop, intrabar at the level / open-on-gap** (`stop_fill_mode: touch`) |
| Slippage | **$0.10 per unit**, on every exit leg (conservative stop-fill/sweep allowance) |

Not ported (all disabled in the Python default): `protective_stop`, `daily_loss_limit`,
`reenter_after_whipsaw`, `pdh_pdl_filter`, `rvol_filter`, `time_window`, `or_width_regime`.

## The two changes that matter most
1. **Resting stop (`stop_fill_mode: touch`)** — stop exits fill intrabar at the level (gap-aware),
   as a real broker OCO order. This is the single biggest lever: +18% net and ~half the worst day
   vs the prior close-fill model, because BE stops fill at ~entry instead of a bar close far below.
   (The *earlier* project default was close-fill precisely because we had not yet committed to
   placing resting stops; now that we do, touch is the honest and better model.)
2. **Volatility gate** — this is a *low-vol* breakout system. A high-vol bar closes further past
   the stop, so BE-stop cost scales with volatility. Skipping the top vol quintile rescues 2024
   (PF 1.07 → 1.19) and never fires in 2026 (which had ~no high-vol days — that's *why* 2026
   looked so good).

Set "Stop exits fill at bar CLOSE" **ON** in the inputs to see the old close-fill numbers.

## Benchmarks for your 30 / 90 / 365-day Strategy Tester runs
Python engine, **per 1 unit**, realistic fills, $0.10/exit slippage, all ending **2026-07-09**:

Default = **2-candle confirmation + Max-Cap $5 + RESTING STOP** (touch fills, $0.10 slippage):

| Window | From | Trades | WR | Net | PF | Worst day | Reversals |
|--------|------|-------:|---:|----:|---:|----------:|----------:|
| 30d | 2026-06-09 | 23 | 65.2% | +$74.98 | 4.92 | −$5.18 | 3 |
| 90d | 2026-04-10 | 71 | 52.1% | +$108.99 | 2.18 | −$6.28 | 12 |
| **365d** | 2025-07-10 | **269** | **49.8%** | **+$171.42** | **1.46** | **−$7.75** | 42 |

Slippage is now **$0.10/share** (was $0.02) — a conservative allowance for stop-fill slippage and
liquidity sweeps (a resting stop becomes a market order and can fill worse than the level; the 5m
backtest fills *at* the level and can't see sub-bar sweep wicks). This haircuts net ~15%. Set the
Strategy Tester slippage to ~10 ticks to match. To see the old close-fill numbers, set "Stop exits
fill at bar CLOSE" ON.

At `Shares per unit = 100`, Strategy Tester Net P&L should read **~100×** these
(365d ≈ **+$21,300**). It will land slightly *higher*, because Python subtracts $0.02/unit on
every exit leg while the strategy runs `slippage=0`.

**v2.1 fix — no new entries at/after the EOD cutoff.** TradingView will not honour a
`strategy.close()` issued on the *same bar* as the entry, so a **15:55 entry rode overnight**
(and over weekends). A 365-day run had **12** such trades plus a **half-session carry** (Christmas
Eve closes 13:00, so no 15:50 bar ever exists) — roughly **+$58/unit of pure artifact**. Entries are
now blocked at/after the cutoff and the position is always flattened on the session's **last bar**.
The Python engine carries the identical guards.

### TradingView vs Python reconciliation — new default (2-candle + Max-Cap $5), as run 2026-07-10
| Window | From | TV ÷100 | Python | Net diff | Worst day |
|--------|------|--------:|-------:|-----:|---------:|
| 30d | 2026-06-10 | +61.96 | +61.26 | **+0.70** | −8.29 (both) |
| 90d | 2026-04-13 | +114.92 | +117.31 | −2.39 | −10.42 (both) |
| 365d | 2025-07-10 | +176.62 | +188.38 | −11.76 | −12.25 (both) |

**Worst day matches to the cent in all three windows** — the Max-Cap and confirmation logic are
byte-for-byte aligned across the two engines. Trade count reconciles exactly once you account for
TV listing each 25% partial as its own row: **30d = 22 Python trades + 11 partials = 33 TV rows**;
**365d = 269 + 94 = 363**. Both new features are visible in the fills: every `Base SL` loss caps
near $5/share (30d trade 4 = −5.19, trade 7 = −5.65 — the cap plus a few cents of close-fill
overshoot), and the 90d net (+114.92) matches the 2-candle number, not the old immediate-entry
+150.22 — confirming the confirmation gate is active. (A `BE Stop` can still exceed $5, e.g.
−$9.01 on 2025-10-07: once BE is armed the stop sits at entry and fills at the bar close, so a
violent bar eats the full move — intended, and Python models it identically.)

The 30-day net matches within slippage (TV runs `slippage=0`, so it sits +$0.70 above Python's
$0.02/leg). The 365-day −6% gap is the known full-year divergence: FMP-vs-TradingView 5-minute
feed drift, plus the volatility gate reading the daily close differently (Pine `request.security`
vs Python's RTH-rebuilt close) across 250+ days — each flipped marginal day adds/removes a whole
trade. Not a logic bug; the cent-exact worst days rule that out.

(Historical note: the pre-v2.1 365-day run read **+39.80/unit HIGHER** on TV — that was the
overnight-entry bug, since fixed in both engines.)

**Read the 365-day row, not the 30-day row.** PF collapses 3.16 → 2.13 → 1.43 as the window
widens — the 30/90-day windows sit entirely inside the calm 2026 regime this system is built for.
PF 1.43 is the honest number, and 2024 (a high-volatility year) was **PF 1.19**. Size off the weak
year, not the flattering one.

## Full-year results — current default (2-candle confirmation + Max-Cap $5)
Python, 1 unit, realistic fills, $0.10 slippage:

| Year | Trades | WR | Net | PF | Worst day | Reversals |
|---|---:|---:|---:|---:|---:|---:|
| 2024 | 229 | 47.6% | +$74.51 | 1.30 | −$7.90 | 49 |
| 2025 | 221 | 51.1% | +$100.60 | 1.31 | −$7.91 | 38 |
| 2026 H1 | 140 | 48.6% | +$122.49 | 1.65 | −$6.38 | 20 |
| **2024→26 H1** | **590** | **49.2%** | **+$297.60** | **1.39** | **−$7.91** | 107 |

The last row is the full 2.5-year ledger (Run #57, resting stop + $0.10 slippage). At $0.02 slippage
it was +$348.72; the conservative $0.10 stop-slip allowance haircuts it ~15% to +$297.60. **Size off 2024, not 2026** — 2026 was the
friendly low-vol regime. For reference, the OLD immediate-entry default was 2024 253tr/45.1%/+$65.67,
2025 236tr/47.5%/+$82.71, 2026 H1 152tr/50.7%/+$192.31 (higher 2026 net, lower win rate, bigger
worst day) — reproduce it by turning confirmation OFF and Max Stop Distance to 0.

## Set your buffer to 10%
If your chart plots a **Long Trigger of 396.19** on 2026-07-09, your buffer is **14%**, not 10%
(10% gives 396.01). 14% was swept across all three years and rejected — the curve is noise, the
worst day is identical at every buffer, and 14%'s only edge comes from the friendly 2026 regime
(it is *worse* in 2024). Nothing will reconcile against the Python engine unless both use 10%.

## Reading the reversal (why 2026-07-09 didn't fire)
The reversal is **always the opposite direction of the primary**, and the primary stopping only
**arms** it — it fires only when price **closes beyond the raw opposite OR boundary**. That level
is now plotted as **purple circles** whenever a reversal is armed, and the stop label prints
`REV armed: need close < 390.86`.

On 2026-07-09 the primary was **long**, so the reversal could only be a **short** needing a close
below 390.86. The lowest close after the stop was 392.98, and the day rallied to 406.55. Forcing
that short at its best available price would have **lost −$3.12**. Not firing was correct.

## Known sources of small divergence
These are expected; they should shift totals modestly, not change the character.

- **Data feed**: TradingView vs FMP 5-minute bars differ slightly (ticks, consolidation).
- **Volatility gate**: Pine reads the daily bar's close via `request.security`; Python rebuilds the
  daily close from RTH 5m bars. Days near the 4.92% threshold may be classified differently.
- **Intrabar order**: Python evaluates entry → exit → EOD per bar; the Pine port matches this,
  but a trade entered exactly on the 15:50 bar is closed by EOD on that same bar in both.
- **`ta.stdev(..., biased=false)`** is used to match pandas' `ddof=1`.

The volatility gate uses the no-repaint idiom (`lookahead_on` + `[1]`), so it reads only the last
**completed** daily bar — no future leak.
