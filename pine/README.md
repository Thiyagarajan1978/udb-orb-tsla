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

## Why the strategy never uses `strategy.exit(stop=...)`
A resting stop order fills **intrabar at the stop price** — that is the optimistic fantasy the
original v12.4.3 script assumed, and it inflated the 6-month P&L from **+$214 to +$522**. This is
an alerts-only system: the signal fires on the 5-minute **bar close**. So every stop-type exit is a
**market order** and, with `process_orders_on_close=true`, fills at **that bar's close**.

| Event | Order type | Fills at |
|-------|-----------|----------|
| Entry / reversal entry | market | signal bar's **close** |
| 25% partial at TP | **limit** | the **TP level** |
| BE Stop / BE Trail / Base SL / runner-trail / EOD | market | that bar's **close** |

Strategy Tester will show slightly **more** than Python, because Python charges $0.02/unit slippage
on every exit leg and the strategy sets `slippage=0`. Set `slippage` to **2 ticks** to compare like
for like (TradingView charges it on entries *and* exits, so it will be marginally harsher).

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
| Stop fills | **at the bar CLOSE** (`exit_on_close`) |
| Slippage | $0.02 per unit, on every exit leg |

Not ported (all disabled in the Python default): `protective_stop`, `daily_loss_limit`,
`reenter_after_whipsaw`, `pdh_pdl_filter`, `rvol_filter`, `time_window`, `or_width_regime`.

## The two changes that matter most
1. **`exit_on_close`** — stop exits trigger on the bar *close* and fill there. The old script
   filled at the stop level, as if a resting order existed. That single assumption inflated the
   6-month P&L from **+$214 → +$522**. A BE stop is a real ~$2–4 loss, not a $0 scratch.
2. **Volatility gate** — this is a *low-vol* breakout system. A high-vol bar closes further past
   the stop, so BE-stop cost scales with volatility. Skipping the top vol quintile rescues 2024
   (PF 1.07 → 1.19) and never fires in 2026 (which had ~no high-vol days — that's *why* 2026
   looked so good).

Toggle `exit_on_close` **off** in the inputs to see the old, optimistic numbers for comparison.

## Benchmarks for your 30 / 90 / 365-day Strategy Tester runs
Python engine, **per 1 unit**, realistic fills, $0.02/exit slippage, all ending **2026-07-09**:

Default = **2-candle confirmation + Max-Cap $5 stop** (higher win rate, smaller worst day):

| Window | From | Trades | WR | Net | PF | Worst day | Reversals |
|--------|------|-------:|---:|----:|---:|----------:|----------:|
| 30d | 2026-06-09 | 23 | 65.2% | +$76.41 | 3.84 | −$8.29 | 3 |
| 90d | 2026-04-10 | 71 | 53.5% | +$113.08 | 1.92 | −$10.42 | 12 |
| **365d** | 2025-07-10 | **269** | **50.9%** | **+$188.38** | **1.41** | **−$12.25** | 42 |

Reproduce the old immediate-entry numbers by turning OFF "Require confirmation candle" and setting
"Max Stop Distance from Entry" to 0 (→ 365d 289 tr, 49.5%, +$212.95).

At `Shares per unit = 100`, Strategy Tester Net P&L should read **~100×** these
(365d ≈ **+$21,300**). It will land slightly *higher*, because Python subtracts $0.02/unit on
every exit leg while the strategy runs `slippage=0`.

**v2.1 fix — no new entries at/after the EOD cutoff.** TradingView will not honour a
`strategy.close()` issued on the *same bar* as the entry, so a **15:55 entry rode overnight**
(and over weekends). A 365-day run had **12** such trades plus a **half-session carry** (Christmas
Eve closes 13:00, so no 15:50 bar ever exists) — roughly **+$58/unit of pure artifact**. Entries are
now blocked at/after the cutoff and the position is always flattened on the session's **last bar**.
The Python engine carries the identical guards.

### TradingView vs Python reconciliation (as run)
| Window | TV ÷100 | Python | Diff |
|--------|--------:|-------:|-----:|
| 30d | +94.08 | +88.56 | +5.52 |
| 90d | +155.00 | +150.22 | +4.78 |
| 365d (pre-fix) | +255.77 | +215.97 | **+39.80** (the overnight bug) |

The 30-day match is structurally exact: TV's 39 rows = 26 trades + 13 partials; reversals 5,
Trail 11, EOD 6, BE-Stop 9 — all identical. Residual $4–5 gaps are slippage plus a few
cents of feed difference on marginal triggers.

**Read the 365-day row, not the 30-day row.** PF collapses 3.16 → 2.13 → 1.43 as the window
widens — the 30/90-day windows sit entirely inside the calm 2026 regime this system is built for.
PF 1.43 is the honest number, and 2024 (a high-volatility year) was **PF 1.19**. Size off the weak
year, not the flattering one.

## Full-year results (Python, 1 unit, realistic fills, $0.02 slippage)

| Year | Trades | WR | Net | PF | Worst day | Reversals |
|---|---:|---:|---:|---:|---:|---:|
| 2024 | 253 | 45.1% | +$65.67 | 1.19 | −$9.72 | 65 |
| 2025 | 236 | 47.5% | +$82.71 | 1.19 | −$13.12 | 52 |
| 2026 H1 | 152 | 50.7% | +$192.31 | 1.78 | −$9.68 | 30 |

**Size off 2024, not 2026.** 2026 was the friendly regime.

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
