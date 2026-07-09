# Pine port — `UDB_ORB_TSLA_v2.pine`

TradingView indicator that mirrors the Python engine's **current default config**
(`config/config.yaml`). Use it to paper-trade / eyeball-validate the tuned logic on TSLA 5m.

## Install
1. TradingView → **Pine Editor** → paste `UDB_ORB_TSLA_v2.pine` → **Add to chart**.
2. Chart must be **TSLA, 5-minute, Regular Trading Hours**.
3. The **Summary Table** (top-right) reports trades, win rate, net P&L, PF, and worst day —
   the same definitions the Python engine uses (**a BE Stop counts as a failure**).

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

## Expected results (Python, 1 unit, realistic fills, $0.02 slippage)

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
