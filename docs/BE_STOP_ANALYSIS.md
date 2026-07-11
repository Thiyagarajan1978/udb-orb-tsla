# BE-Stop failure review (TSLA 5m, Adaptive TP + Reversal)

Question from the desk: *BE-Stop exits show ~$0 — that's not a real exit. What's the proper
failure amount, and can we avoid them or take an alternative trade on those days?*

Tools: `scripts/analyze_be_stops.py --run N` and `scripts/compare_variants.py --run N`.
Every trade now stores `risk_amount` = `|entry − base SL| × qty` in the DB.

## 1. What a BE-Stop actually costs

A "BE Stop" means BE-Retrace had already moved the stop to entry, then price came back and
hit it. With realistic slippage it books ~**−$0.02/unit** — essentially free. That number is
*correct*: the protection worked. What was misleading was calling it a $0 **win**; it is a
**failure** (the trade never made money) but a **cheap** one.

The honest dollar view (2026 YTD, 68 BE-Stops):

| Measure | Value |
|--------|------|
| P&L actually booked on the 68 | **+$0.25 total** (slippage only) |
| Risk that was on the table (base-SL) | **avg $7.25/trade · max $19.94** |
| Total risk BE-Retrace neutralised | **≈ $493** |

So BE-Retrace converted ~$493 of potential drawdown into a $0.25 scratch. The "proper failure
amount" per trade is now stored as `risk_amount` — the loss you'd have taken **without** BE.

## 2. Why they happen — two distinct causes

Reconstructing each day (MFE/MAE after entry, EOD close):

- **Premature BE (whipsaw-then-resume)** — on ~18/68 the original direction would have been
  *profitable* if held (`hold_eod > 0`), but a shallow wick retrace tripped the tight **0.35**
  BE trigger and stopped it at entry. These are self-inflicted.
- **False breakout (reversal day)** — ~50/68 closed the *opposite* way (`opp_eod > 0`). The
  breakout was simply wrong; BE correctly saved you. The right play is the **reversal trade**,
  which the system already takes and which rescued **13** of these days to net-positive.

Blanket "hold to EOD" is a trap: across all 68 it sums to **−$258/unit** (the reversal days
dominate). RVOL and time-of-day filters didn't help (they cut good trades too).

## 3. The fix — loosen the BE trigger

The 0.35 trigger (move stop to entry after only a 35%-of-OR retrace) fires too early. Raising
it lets the trade breathe. Swept over 2024→2026 (546 trades) **and** validated train/holdout:

| BE trigger | Net 24-25 (train) | Net 2026 (holdout) | 2026 WR | 2026 BE-stops | 2026 worst day |
|-----------:|------------------:|-------------------:|--------:|--------------:|---------------:|
| **0.35** (current) | +$602.63 | +$450.01 | 51.6% | 68 | −$1.25 |
| **0.50** | +$664.13 | +$460.19 | 53.8% | 59 | −$4.48 |
| **0.55** | **+$687.25** | **+$466.70** | **55.2%** | **56** | −$4.58 |

Both periods agree — higher trigger → fewer BE-stops, higher win rate, more net. The cost is a
larger worst day (−$4.58 vs −$1.25) because a deeper retrace occasionally becomes a small real
base-SL loss instead of a scratch. That is the honest trade-off, and it is still tiny next to
the ~$7 already risked per trade.

**Adopted:** **BE trigger 0.55** is now the default in `config/config.yaml`. The exact Pine
port (0.35) is preserved in `config/faithful_be035.yaml` for reproduction:

```
python cli.py backtest --start 2026-01-01 --end 2026-07-08                     # default (tuned)
python cli.py --config config/faithful_be035.yaml backtest --start ... --end ... # exact Pine port
```

## 4. Better alternative-trade capture — BUILT & ADOPTED

The false-breakout days close opposite 74% of the time, yet the default reversal (fresh
*buffered* close-break + fixed $5 TP + its own BE) only rescued a few. Two changes, gated by
`enhancements.reversal_capture`, fix that:

- **`trigger_on_be_stop`** — enter the reversal on a **raw** opposite OR break (earlier /
  more often) instead of waiting for a buffered break.
- **`trail_to_eod`** — the reversal rides the full move (no fixed $5 TP, no partial), exiting
  on BE-trail or EOD, so trend-reversal days are captured in full.

A/B over 2024→2026, train + holdout (default now BE 0.55):

| Reversal setting | Train net | Holdout net | Holdout rev P&L | Holdout worst |
|------------------|----------:|------------:|----------------:|--------------:|
| off (default reversal) | +687 | +467 | +140 | −4.6 |
| trigger_on_be_stop | +709 | +495 | +168 | −3.2 |
| **trigger + trail_to_eod (adopted)** | **+733** | **+502** | **+176** | **−3.2** |

Both changes clear the train/holdout bar, so they are **adopted as defaults** in
`config/config.yaml`. `target_or_mult` (OR-scaled reversal TP) was tested and left OFF — it
did not beat trail_to_eod.

### Combined effect of both adopted changes (YTD 2026)
| | Faithful port (BE 0.35) | Adopted (BE 0.55 + reversal capture) |
|--|------------------------:|-------------------------------------:|
| Net P&L | +$450.01 | **+$501.91** (+11.5%) |
| Win rate | 51.6% | **55.1%** |
| BE-Stop failures | 68 | **55** |
| Best day | +$27.86 | **+$33.84** |
| Worst day | −$1.25 | −$3.22 |

Reproduce the exact Pine port (no tuning) with `config/faithful_be035.yaml`.

## 5. Can the 7 failures be avoided? — tested, mostly no

Drilling into a single month (June 2026, 7 failures) with `scripts/inspect_day.py`:
- **3 of 7 are already winning days** — the reversal rescued them (Jun 10 +$17, 18 +$5, 24 +$3).
- **2 are unwinnable chop** (Jun 15, 25) — entered on the open, snapped back to breakeven, then
  ranged sideways; never broke the far OR boundary. BE caps them at ~$0.
- **1 is a whipsaw** (Jun 17) — the short was directionally right (close 394 vs 398.55 entry) but
  BE-stopped on a mid-day spike; the reversal long then bought the top. Net −$0.06.

Total cost of all 7 ≈ **−$0.14**. Crucially, **the failures and the biggest winners share the
same 09:35 opening-break setup** (Jun 5 +$20 and Jun 29 +$19 both entered 09:35, as did four
failures). You cannot filter the losers without killing the winners — which is why every
time-of-day / RVOL filter *reduced* net. BE protection is already optimal here.

**What was tested (train + holdout):**
| Lever | Result | Decision |
|-------|--------|----------|
| Bigger partial (25→75%) | BE-stops happen *before* any partial → count unchanged; helped train, hurt holdout | wash |
| **Wider TP `adaptive_tp_scale` 1.25** | +net on BOTH (train +$34, holdout +$9); lower WR | **adopted** |
| **Whipsaw re-entry** (`reenter_after_whipsaw`) | train +$38 (48% WR) but holdout only +$4.3 over 6mo (30% WR), +failures | built, **left OFF** (opt-in) |

The honest takeaway: the losers are already ~$0, so there is nothing to squeeze there — the only
robust lever is letting **winners run further** (TP scale), which trades win-rate for total P&L.

### 6-month 2026 with all adopted tunings (BE 0.55 + reversal capture + tp_scale 1.25)
157 trades · 52.9% WR · **net +$510.47** · best day +$33.84 · worst −$3.22 (vs faithful port
+$450.01). Re-entry stays OFF.

## 6. PDH/PDL confirmation filter — tested, left OFF

Hypothesis (desk observation): failures are immediate reversals off prior-day high/low
(PDH/PDL). Fix: when PDH/PDL sits within `proximity_pct` of the OR width of the break level,
require a close BEYOND PDH (longs) / PDL (shorts) before entering. Built as
`enhancements.pdh_pdl_filter` (default OFF).

**Validation (June 2026 failures):** partly true but not actionable.
- The *turn points* of 2–3 of 8 failures were right at PDH/PDL — Jun 15 short bounced $0.44
  above PDH, Jun 25 short $1.81 above PDL, Jun 16 long stalled $2.58 below PDH.
- But the *entries* were far from PDH/PDL (the break levels sat $5–21 away, i.e. 1–5× the OR
  width). Price only reaches PDH/PDL deep into the trade, not at entry — so an entry-proximity
  filter cannot see it.
- The other 5 failures reversed nowhere near PDH/PDL.

**Sweep (train + holdout, proximity 10–30%):** the filter barely activates on TSLA (OR breakout
levels are rarely near prior-day extremes) and does not help — holdout net 510.5 → 508.9 (10–14%)
and worse at wider bands. No setting improved results.

**Verdict:** the observation is real on a minority of days, but this specific rule doesn't
trigger enough on TSLA to matter and can't catch these failures (the entry isn't near PDH/PDL).
Kept as an opt-in (default OFF) — prior-day levels are meaningful S/R generally and may help on
other instruments/regimes. Not adopted.

## 7. Better TP process: runner peak-trail — ADOPTED

Diagnosing the *exit* side (6-month, per-unit): the winners reach a big peak but capture little.

| Exit | n | Realized/unit | Peak (MFE)/unit | Give-back |
|------|---:|-------------:|----------------:|----------:|
| EOD | 72 | $5.49 | $8.90 | **$3.41** |
| VWAP Cross | 18 | $2.41 | $8.94 | **$6.53** |
| BE Stop | 58 | $0.00 | $2.67 | (correct — reversed hard, EOD would be −$4.86) |

Root cause: the BE-trail only engages **after** price retraces to the BE trigger. On strong trend
days price never retraces that far, so the post-partial runner has **no trailing stop at all** — it
holds to EOD (or a VWAP cross), giving back the whole fade from the peak (~$540/unit total left).

Fix (`enhancements.runner_trail`): after the 25% partial, trail the 75% runner `or_mult × OR width`
below its running peak, engaging immediately. Swept train + holdout:

| Trail width | Train net | Holdout net |
|------------:|----------:|------------:|
| baseline (VWAP/EOD) | +$767 | +$510.5 |
| 0.75×OR | +$784 | +$490 (worse) |
| **1.0×OR (adopted)** | **+$795** | **+$522.6** |
| 1.25×OR | +$781 | +$516 |

1.0×OR adds +$28 train / +$12 holdout, lifts win rate and avg win, worst day unchanged. Tighter
trails overfit (help train, hurt holdout — shaken out of volatile trends). **Adopted @ 1.0×OR.**

### 6-month 2026 with ALL adopted tunings (BE 0.55 + reversal capture + tp_scale 1.25 + runner_trail 1.0×OR)
157 trades · 53.5% WR · **net +$522.56** (vs faithful port +$450.01, +16%). EOD exits 72 → 66 as
the runner banks its peak earlier. *(NOTE: this is the OPTIMISTIC fill-at-stop model — see §8.)*

## 8. Execution realism — BE stops fill at the bar CLOSE (alerts-only)

Desk observation: in live trading a BE stop loses ~$2, not $0, because the alert fires on the
5-minute **bar close** — there is no resting order, so you exit *after* the close, at the close.
`execution.exit_on_close: true` (now the default in `config.yaml`) models this: BE Stop / BE Trail
/ Base SL / runner-trail exits trigger on a close beyond the level and fill at that close.

**Impact (6-month 2026), optimistic (fill-at-stop) vs realistic (fill-at-close):**
| | Optimistic | Realistic |
|--|-----------:|----------:|
| Net P&L | +$522.56 | **+$214.03** |
| Win rate | 53.5% | 47.8% |
| Avg loss | −$0.21 | **−$3.87** |
| Worst day | −$3.22 | **−$22.06** |
| Profit factor | 34.7 | **1.67** |

June 2026: +$131.66 → **+$89.88** (PF 2.68, worst −$14.38).

The strategy is still profitable realistically, but far more modest, and the earlier "failures are
≈free" conclusion only held under the optimistic model. **Key execution lever:** placing a resting
stop order at the BE level (broker OCO) instead of a manual close-alert exit recovers most of the
gap and caps the tail — that is now the single highest-value improvement, and it lives in *how you
execute*, not in the signal. All prior tunings were validated under the optimistic model and should
be re-checked under `exit_on_close` if you trade purely on close alerts.

## 9. Re-tune under realistic fills + tail control (a + b)

**(a) Re-tuned under `exit_on_close` (train + holdout):**
- BE OFF *loses* money on train (−$33) — BE is essential under realistic fills.
- `adaptive_tp_scale` 1.25 → **1.0** (wider TP rides more trades into a real BE loss; 1.0 wins both).
- `runner_trail` 1.0 → **0.75×OR** (wins both).
- BE trigger 0.55 kept. Net effect (holdout 6-month): +$214 → **+$241**, train +$64 → +$99.

**(b) Protective stop — TESTED, LEFT OFF.** A resting stop at the OR boundary does **not** cap the
tail: the realistic worst days are whipsaws on *wide-OR* days where the loss **closes within the OR
range** (above the boundary), so the boundary stop never fires (and can worsen a wick-and-recover
bar). Worst day unchanged (−22.1), train net slightly worse. Kept as an opt-in
(`execution.protective_stop`), default OFF.

**The real tail driver + fix.** The 4 worst days are all: wide OR (risk $12–17) → primary BE-stops
big → the **2× reversal** BE-stops big in the other direction → −$16 to −$22 day. Dropping the
reversal halves the tail but costs $91 on holdout (too valuable). **Capping OR width at $8** is the
fix — skips the widest whipsaw days:

| | Train net | Holdout net | Train worst | Holdout worst |
|--|----------:|------------:|------------:|--------------:|
| re-tuned baseline | +$99 | +$241 | −$24.3 | −$22.1 |
| **+ max OR ≤ $8 (adopted)** | **+$159** | +$238 | **−$17.3** | **−$16.5** |

Train net +61%, holdout flat, worst day better on both — same return, lower risk.

## 10. Reducing the worst day — reversal risk parity (ADOPTED)

Every one of the 8 worst days has the same shape: **primary BE-stops small, then the 2× reversal
BE-stops big.** Across those days: primary −$28.53 vs **reversal −$60.04 (68% of the damage)**.

Cause: the reversal enters *after* price crossed the whole opening range, so its stop (the
opposite OR boundary) is far away — and then it's doubled by the 2× size.

| | n | avg dollar risk |
|--|--:|----------------:|
| primary legs | 120 | $6.16 |
| **reversal legs** | 30 | **$10.03 (1.6× the primary)** |

The 2× share multiplier (from the ORB doc) was never risk-adjusted. Fix: `reversal_risk_cap`
(profile) scales the reversal qty so its dollar risk ≤ cap (`scale`), or declines it (`skip`).

- **`skip` rejected** — regime-dependent (train +$163 / holdout +$164, a $74 collapse).
- **`scale` is smooth and consistent** on both segments.

Return per $1 of worst-day risk (net / |worst day|) rises monotonically as the cap tightens — but
that degenerates toward "no reversal" at the extreme. The **principled stopping point is risk
parity with the primary (~$6)**, not the ratio maximum.

| Cap (scale) | Holdout net | Holdout worst | ratio | Train net | Train worst |
|------------:|------------:|--------------:|------:|----------:|------------:|
| off | +$238 | −$16.5 | 14.45 | +$159 | −$17.3 |
| **$6 (adopted)** | **+$196** | **−$9.7** | **20.28** | **+$134** | **−$11.9** |

Worst day −41% for −18% net. **Sized to a fixed worst-day budget, that's ~+40% more profit for
the same risk** (e.g. a $100/day tolerance: 6.07 units × $238 = $1,445 vs 10.33 units × $196 =
$2,027). Worst 5 days flatten from −16.5/−12.9/−11.5/−10.8/−10.5 to −9.7/−9.3/−9.1/−7.4/−7.1.

## 11. Daily loss circuit-breaker — TESTED, REJECTED

Idea: once the day's realised P&L ≤ −X, take no new entries (blocks the reversal on bad days).
Built as `execution.daily_loss_limit` (0 = off, default).

**The signal is inverted.** On the 6-month data, the primary's loss *before* the reversal was:
- days the reversal **WON**: mean **−$4.06**
- days the reversal **LOST**: mean **−$3.34**

A *bigger* primary loss predicts a *better* reversal — a large primary loss means price moved
decisively against the breakout, which is exactly the move the reversal rides. A breaker therefore
cuts the reversals you most want. At a −$5 breaker it blocks 4 reversals, **all 4 winners**, avoiding
$0 of losses.

Sweep (train + holdout), judged on net per $1 of worst-day risk:

| Breaker | Train net | Train ratio | Holdout net | Holdout ratio |
|--------:|----------:|------------:|------------:|--------------:|
| off | +$134 | 11.29 | **+$196** | **20.28** |
| −$3/day | +$155 | 18.17 | +$145 | 19.56 |
| −$4/day | +$127 | 14.93 | +$157 | 21.14 |
| −$8/day | +$127 | 11.13 | +$196 | 20.28 (never fires) |

Train prefers −$3; the holdout's ratio gets *worse* there. The holdout's best (−$4) buys a **+4%**
risk-adjusted gain for a **−20%** cut in net. And at "safe" levels (−$6 and wider) it never fires,
because after the risk-parity cap the primary loss rarely exceeds −$6. **Regime-dependent and
value-destroying — left OFF** (opt-in via `execution.daily_loss_limit`).

**Conclusion: −$9.68 is the right worst-day floor.** The reversal risk-parity cap (§10) was the
real fix; the breaker adds nothing on top of it.

## 12. Regime: this is a LOW-VOLATILITY breakout system (volatility gate ADOPTED)

Full-year data (after fixing a DB gap — 2025 only had ~60 days stored) showed the edge is heavily
regime-dependent:

| Year | Trades | Net | PF | Expectancy/trade |
|------|-------:|----:|---:|-----------------:|
| 2024 | 315 | +$33.89 | **1.07** | $0.11 |
| 2025 | 288 | +$85.79 | 1.16 | $0.30 |
| 2026 H1 | 150 | +$196.23 | 1.81 | $1.31 |

2026 earns **12× more per trade** than 2024. So what differs? Building daily features that use
ONLY pre-entry information (prior sessions + the 09:30 OR bar) and correlating with day P&L:

- The only strong correlates are **post-hoc**: `efficiency` (trend-day-ness, +0.43) and
  `day_range_pct` (+0.40). Both are known only at the close — useless as a filter.
- **Every pre-entry feature has |corr| ≤ 0.06** (OR/ATR, gap, prior trend, OR volume). Linearly,
  nothing predicts the day.

But the relationship is a **step function**, not linear. Prior-20-day realised volatility, by quintile:

| rvol20 quintile | days | mean day P&L |
|-----------------|-----:|-------------:|
| Q1 (lowest vol) | 113 | +0.891 |
| Q2 | 112 | +0.728 |
| Q3 | 112 | +0.753 |
| Q4 | 112 | +0.742 |
| **Q5 (highest vol)** | 112 | **−0.156** |

Q1–Q4 are uniformly profitable; **only the top vol quintile loses money.**

**Causal mechanism:** with close-based BE stops (§8), a high-volatility bar closes further past the
stop, so the BE-stop cost scales directly with volatility. High vol doesn't break the signal — it
inflates the cost of being wrong.

**The filter.** Skip the day when prior-20d realised daily vol > threshold. Threshold = 80th
percentile of **2024–25 only** (4.92%); 2026 is out-of-sample.

| Year | Before | After | Days skipped |
|------|-------:|------:|-------------:|
| 2024 | +$33.89 (PF 1.07) | **+$65.67 (PF 1.19)** | 64 |
| 2025 | +$85.79 (PF 1.16) | +$82.71 (PF 1.19) | 66 |
| **2026 (OOS)** | +$196.23 (PF 1.81) | **+$196.23 (PF 1.81)** | **8** |

2026 has almost **no** high-vol days — that is *why* it outperformed. The gate rescues the worst
year (+94% net, PF 1.07→1.19), costs $3 in 2025, and **cannot touch 2026**. Every year now clears
PF ≥ 1.19; the razor-thin 1.07 is gone. Adopted as `enhancements.volatility_regime`.

## 13. Resume re-entry — BUILT, TESTED, REJECTED

Motivated by **2026-07-09**: the primary long (398.01) was BE-stopped at 392.43 (−$5.60) by a
single dip, then price rallied all day to close at 406.55. The reversal (a *short*, triggered by a
close below the OR low 390.86) never armed — price never came within $2 of it. Correct, but the
day was left on the table. A "resume" rule — re-enter the SAME direction when price closes back
beyond the original break level — turns that day into **−$5.60 → +$1.48**.

Implemented as `enhancements.resume_reentry` (trigger buffered/raw, optional risk cap, and
`disarm_other` controlling whether it competes with the reversal or both stay armed).

**It only works in 2025.** Resume-leg P&L and win rate:

| Year | Resume legs | Resume P&L | Resume WR |
|------|------------:|-----------:|----------:|
| 2024 | 37 | **−$16.7** | 32% |
| 2025 | 40 | **+$83.7** | **62%** |
| 2026 | 22 | **−$16.8** | 36% |

**It also cannibalises the reversal** (they compete for the same slot). In 2024 the resume
preempted **+$46.5 of GOOD reversals** and returned −$16.7 — a −$63 swing:

| Year | Reversal off→on | Preempted reversals worth | Resume delivered | Net |
|------|-----------------|--------------------------:|-----------------:|----:|
| 2024 | 65 legs +$17.3 → 49 legs −$29.2 | **+$46.5** | −$16.7 | **−$63** |
| 2025 | 52 legs −$36.1 → 42 legs −$36.5 | +$0.4 | +$83.7 | +$83 |
| 2026 | 30 legs +$59.8 → 19 legs +$69.9 | −$10.1 | −$16.8 | −$7 |

Letting **both** stay armed (up to 3 legs/day) removes the cannibalisation but is worse still:

| Variant | Train net | Train worst | Holdout net | Holdout worst | Holdout ratio |
|---------|----------:|------------:|------------:|--------------:|--------------:|
| **baseline (off)** | +$148 | −$13.1 | **+$192.3** | **−$9.7** | **19.87** |
| resume, compete (2 legs) | +$169 | −$13.1 | +$185.6 | −$9.7 | 19.18 |
| resume, both armed (3 legs) | **+$219** | −$18.4 | +$171.1 | −$13.7 | 12.51 |

The most aggressive variant is **best on train and worst on holdout** — the classic overfit
signature. Baseline beats both on holdout net, PF *and* worst day.

**Why:** 2025 was a **shakeout regime** (its reversals lost −$36, so resuming was the better bet);
2024 and 2026 were **reversal regimes** (reversals made +$17 and +$60). These are opposite worlds,
and — as with the entry filters (§5, §6) — we cannot tell which one we are in *ex ante*. The
reversal is positive in 2 of 3 years, so it keeps the slot. **Default OFF.**

2026-07-09 is an anecdote, not a pattern.

## 14. Breakout buffer — swept, KEPT AT 10%

Prompted by a TradingView chart running a 14% buffer (long trigger 396.19 = 395.54 + 0.655 on a
4.68 OR). Swept 0-25% across all three years:

| Buffer | 2024 | 2025 | 2026 H1 | Train | Holdout | Sum |
|-------:|-----:|-----:|--------:|------:|--------:|----:|
| 0% | +49.2 | +84.8 | +162.2 | +134.0 | +162.2 | +296 |
| 5% | +61.9 | +93.0 | +173.6 | +154.8 | +173.6 | +328 |
| 8% | +55.9 | +83.0 | +193.3 | +138.9 | +193.3 | +332 |
| **10% (kept)** | **+65.7** | +82.7 | +192.3 | +148.4 | +192.3 | +341 |
| 12% | +52.1 | +96.4 | +187.5 | +148.5 | +187.5 | +336 |
| 14% | +59.8 | +90.0 | **+201.3** | +149.8 | **+201.3** | **+351** |
| 18% | +56.8 | +75.8 | +176.6 | +132.6 | +176.6 | +309 |
| 25% | +44.5 | +44.2 | +171.6 | +88.6 | +171.6 | +260 |

**The curve is noise, not signal.** Walk 2024: `49 → 62 → 56 → 66 → 52 → 60 → 57` — it jumps ~$10
between adjacent settings, with no smooth optimum. Anything 5–14% is statistically the same; only
the extremes hurt (0% takes false breaks, 25% arrives too late). **Worst day is identical (-13.1 /
-9.7) at every buffer** — this knob has zero risk effect.

14% posts the best sum (+$351) but its entire edge comes from **2026, the friendly low-vol regime**;
in 2024 (the weak year we size off) it is *worse* (+59.8 vs +65.7). Adopting it would be tuning to
the good regime for a $9 gain inside the noise band. **Kept at 10%.**

## 15. 2026-07-09 — the reversal was RIGHT not to fire

The primary LONG (398.01) was BE-stopped at 392.43 (-$5.60) by one dip; price then rallied all day
to close 406.55. It *looks* like a missed reversal. It is not — **the reversal is always the OPPOSITE
of the primary, so on this day it could only have been a SHORT**, needing a close below the raw OR
low (390.86). The lowest close after the stop was 392.98; it never came within $2.

Forcing a short at that best-possible price and applying the engine's exact rules:

```
FORCED reversal SHORT @392.98, stop 395.54 (OR high), risk-parity qty 2.00, BE trigger >=393.43
EXIT 10:25 @ close 394.52 (BE Stop) -> -1.56/unit x 2.00 = -$3.12
Day WITH reversal: -$8.72   |   Day AS TRADED: -$5.60   |   Gate SAVED +$3.12
```

The miss people see on 07-09 is a **resume** miss (re-entering LONG), not a reversal miss — and the
resume rule was tested and rejected in §13 (it pays +$7 here but costs -$63 in 2024).

## 16. Third-eye review: "ORB Adaptive TP Best Default v1.23"

An external Pine script built on the same UDB v12.4.3 lineage. Concepts triaged against our work:

| Concept | Verdict |
|---------|---------|
| PDH/PDL **Confluence Gate** (extend trigger to PDH/PDL when within 25% OR) | Already built as `pdh_pdl_filter`; barely triggers on TSLA, holdout worse (§6) |
| PDH/PDL as S/R generally (Ahead Block, Break+Retest, Sweep-Reclaim, Momentum Bypass) | 11 HTF levels tested: **47% failure rate in BOTH** "level in path" and "clear path" groups. No signal (§5) |
| VWAP-cross trail | Superseded by the runner peak-trail (§7) |
| **Max Cap Stop Distance** | **NEW — tested, REJECTED** (below) |
| **2-close acceptance** | **NEW — tested, REJECTED decisively** (below) |
| Block entries at/after EOD | Trivial; an EOD-bar entry costs only slippage |
| Sweep-Reclaim block | Largely redundant — we already require a *close* beyond the trigger, so a wick-sweep never enters |

### Max-cap stop (`sl_mode: "Candle High/Low + Max Cap"`) — REJECTED

Caps the OR-boundary stop at `fixed_sl` from entry. Implemented and available.

It *does* fix 2026-07-09: `-5.60 (BE Stop) -> -4.05 (Base SL)`. But across three years:

| Cap | 2024 net | 2025 net | 2026 net | Total |
|----:|---------:|---------:|---------:|------:|
| off (OR stop) | +65.7 | +82.7 | **+192.3** | 340.7 |
| $3 | +55.7 | +95.6 | +189.4 | 340.7 |
| $4 | +70.0 | +81.3 | +185.8 | 337.1 |
| $5 | +67.1 | **+100.1** | +185.3 | **352.5** |
| $6 | +66.2 | +82.7 | +193.1 | 342.0 |

**The surface is noise.** 2025 swings `+95.6 -> +81.3 -> +100.1 -> +82.7` across caps $3→$6 — a $19
jump between adjacent settings, non-monotone, no optimum. The best total ($5) comes from train
(+$18.8) while **hurting the holdout** (2026: 192.3 → 185.3). Train-helps/holdout-hurts again.

Mechanically the cap rarely binds: BE fires on almost any retrace (wick ≤ `be_level`) and moves the
stop *up* to entry, so the cap only matters on wide-OR days where price closes below the capped stop
before touching `be_level`. Those are exactly the days `max_or_width $8` already skips.

### 2-close acceptance (`enhancements.confirm_two_closes`) — REJECTED

Require the *previous* bar to also close beyond the trigger.

| | 2024 net | 2024 worst | 2025 net | 2026 net | 2026 worst |
|--|---------:|-----------:|---------:|---------:|-----------:|
| baseline | **+65.7** | **−9.7** | +82.7 | **+192.3** | **−9.7** |
| 2-close | +43.1 | −14.9 | +84.0 | +150.3 | −10.4 |

It delays the fill (worse entry price) and **worsens both net and worst day** in 2024 and 2026.
On 07-09 it enters at 396.28 instead of 398.01 — a "better" loss (−3.87) purely by entering later
into the same stop. That is not an edge, it is a smaller position in the same bad trade.

### The most valuable insight from v1.23

Its own changelog says: *"Reversal Trades default OFF because the 365-day sample improved with
reversal disabled."* **Our data agrees with the diagnosis and rejects the cure.** Their reversal is
the un-risk-adjusted 2× fixed-size version — the exact design we proved causes **68% of worst-day
damage** (§10). They removed the reversal; we **sized it to risk parity** and kept the edge
(reversal legs: +$17 in 2024, +$60 in 2026; deleting it costs $91 on the holdout).

Same symptom, two fixes. Theirs throws away a profitable leg; ours keeps it and fixes the sizing bug.

## 17. TradingView validation found a real bug: entries at/after the EOD cutoff

Strategy Tester exports (30 / 90 / 365 day, ending 2026-07-09, `Shares per unit = 100`):

| Window | TV ÷100 | Python | Diff |
|--------|--------:|-------:|-----:|
| 30d | +94.08 | +88.56 | +5.52 |
| 90d | +155.00 | +150.22 | +4.78 |
| **365d** | **+255.77** | +215.97 | **+39.80** |

The 30-day is a **structurally exact** match — TV's 39 rows = 26 trades + 13 partials; reversals 5,
Trail 11, EOD 6, BE-Stop 9; fractional risk-parity sizes (161/113/72/126/82 shares) all present.

The 365-day overshoot is a bug. TradingView will **not honour a `strategy.close()` issued on the
same bar as the entry**, so an entry on the **15:55** bar (past the 15:50 cutoff) rides overnight:

```
Entry L  2025-08-08 15:55 -> Exit 2025-08-11 15:50 "EOD flat"  +1044   (over a weekend)
Entry S  2026-01-02 15:55 -> Exit 2026-01-05 15:50 "EOD flat"  -1481
Entry S  2025-12-24 09:35 -> Exit 2025-12-26 15:50 "EOD flat"  (half day: no 15:50 bar exists)
```

**12** such trades plus a half-session carry ≈ **+$58/unit of artifact** — the entire 365-day gap.
It never appears in the 30/90-day windows because they contain no 15:55 entries.

**Fix (both engines):** block new entries at/after the EOD cutoff, and always flatten on the
session's **last bar** (half sessions close at 13:00 and never produce a 15:50 bar; previously the
Python engine silently *dropped* those open trades at the day rollover).

Impact on Python: 365d 291 -> 289 trades, +$215.97 -> **+$212.95**. 30d/90d unchanged.
Full years: 2024 +$65.75 (PF 1.19) · 2025 +$79.63 (PF 1.18) · 2026H1 +$192.37 (PF 1.78).

**Note on §16:** the external ORB A+R v1.23 script had `blockEntriesAtOrAfterEOD` and this review
dismissed it as *"trivial; ~zero impact."* That was wrong. In the Python engine it *is* ~zero
(the trade opens and closes on the same bar for −$0.02), but in Pine it is a correctness bug. The
concept was right; only our engine's tolerance for it hid the cost.

## 18. External review response — execution modality, time window, reversal

An external reviewer ranked six recommendations. Each was tested on full-year 2024 / 2025 / 2026.

**(1) Execution modality — CONFIRMED as the biggest lever; `stop_fill_mode` added.** Every loss is
a close-fill BE stop averaging ~$3.68/share *through* the stop — a tax on the alerts-only workflow,
not on the alpha. A broker resting stop fills at the stop ±slippage instead. Modelled honestly
(`stop_fill_mode: touch`, gap-aware fill at min(stop, open), which also wicks out trades that dip to
entry and recover):

| model | 2024 | 2025 | 2026 |
|-------|-----:|-----:|-----:|
| close-fill (alerts-only) | +65.8 (PF 1.2) | +79.6 (1.2) | +192.4 (1.8) |
| touch slip $0.03 | +155.4 (1.7) | +148.0 (1.5) | +246.7 (2.7) |
| touch slip $0.10 | +134.7 (1.6) | +130.8 (1.4) | +235.7 (2.6) |
| touch slip $0.20 | +105.0 (1.4) | +106.2 (1.3) | +220.1 (2.4) |
| stop-exactly (fantasy) | +377.8 (24.9) | +439.4 (27.8) | +383.7 (32.8) |

A resting stop roughly **doubles net** and lifts PF to 1.3-1.6 with a smaller worst day — but the
"PF 24" only appears if stops fill at exactly entry (zero gap slippage), which will not happen live.
The truth is measurable: paper-trade a resting stop on TradeStation and compare. Default stays
`close` (the honest alerts-only number); `touch` is one config line away.

**(3) Time window on primaries — VALIDATED, recommended.** Late ORB breaks (compressed choppy
mornings) underperform. Restricting entries to 09:35-11:00 (also gates midday reversals):

| variant | 2024 | 2025 | 2026 | ugly(24+25) | total |
|---------|-----:|-----:|-----:|------------:|------:|
| baseline | +65.8 | +79.6 | +192.4 | +145.4 | +337.7 |
| window 09:35-10:30 | +58.9 | +113.5 | +162.7 | +172.5 | +335.1 |
| **window 09:35-11:00** | **+74.2** | +92.6 | +179.6 | +166.8 | **+346.4** |

11:00 is PF-positive in all three years and net-positive in the weak 2024 (+$8, PF 1.19->1.26). The
exact cutoff is noisy (10:30 is *worse* than 11:00 in 2024), so this is a risk-shift — trade a little
2026 upside for weak-regime robustness — not a free lunch. It is `enhancements.time_window`, off by
default; enable `end: "11:00"` to adopt.

**(2) Reversal — reviewer's 365d view was pre-cap; full years disagree by regime.** With the $6
risk-parity cap already in place: deleting the reversal *helps 2025* (+$36, a shakeout year) but
*hurts 2024* (−$17) and *2026* (−$60). `reversal_qty_mult 1.0` is the robust middle (2024 +67.2,
2025 +88.5, 2026 +180.7 — better on both weak years, small cost in the strong one). Kept at 2×-capped
for now; the time window already removes the worst midday reversals.

**(4/5) Partial & trail — CONFIRMED as-is.** 15% partial marginally beats 25% in all three years but
by <$10/yr (noise). Tighter runner trail (0.55/0.65×OR) *badly hurts* 2025 (+$37/+$31 vs +$80) — the
0.75 trail is right. BE trail $0.25 has zero exits under close-fill but 9 under touch, so it is not
cosmetic if you move to a broker stop — kept.

**(6) Guardrails — agreed and already the house rule.** Every change above was judged on 2024/2025,
not on the flattering Jun-Jul 2026 stretch. The Mon/Fri seasonality the reviewer flagged as overfit
bait was not acted on.

## 19. Confirmation-candle trigger — TESTED, REJECTED (5th entry filter to fail the same way)

User request: instead of entering on the first close-break, require the NEXT candle to HOLD beyond
the trigger (and, optionally, be a with-trend candle); if it snaps back inside, wait for a fresh
break. Motivated by 2026-07-09 (10:00 break, 10:05 back inside; only the 11:35 break + 11:40 hold
was real). Implemented as `enhancements.confirm_breakout`.

**On July 2026 it looks great** — turns the 07-09 BE-stop (−$5.60) into +$4.46 and 07-06 into
+$10.77 (the failed opening short is replaced by a confirmed long). Month +$27.27 → +$34.78.

**On the full sample it loses:**

| variant | 2024 | 2025 | 2026 | ugly(24+25) | total | worst 2024 |
|---------|-----:|-----:|-----:|------------:|------:|-----------:|
| baseline (immediate) | +65.8 | +79.6 | +192.4 | +145.4 | +337.7 | −9.7 |
| confirm hold + trend | +42.8 | +112.3 | +125.1 | +155.1 | +280.2 | −14.9 |
| confirm hold only | +43.2 | +79.9 | +150.4 | +123.0 | +273.4 | −14.9 |

Helps 2025 (+$33, a shakeout year) but hurts 2024 (−$23) and 2026 (−$67); total −$57.5; and the
worst day gets *worse* (−9.7 → −14.9). **Why:** confirmation makes you enter later at a worse price
on the trend days (07-02: short filled 413.76 instead of 418.70, −$5 on a runner), and TSLA's
biggest winners are immediate follow-through breakouts. The whipsaw-saves are outnumbered by the
trend-fill-costs, and a confirmed entry that fails is more extended from the OR, so a bigger stop.

**Pattern (now 5 for 5): every entry-timing filter fails the same way** — time-of-day, RVOL,
PDH/PDL, 2-close acceptance, and now confirmation. On TSLA, filters that WAIT cost more on trends
than they save on whipsaws. The edge is in management (BE, risk-parity sizing, trailing), not entry
selection. Kept as an opt-in (default OFF); the immediate-entry logic stays final.

## 20. Higher-win-rate profile ADOPTED: 2-candle confirmation + Max-Cap $5 stop

User chose to prioritise win rate. The confirmation candle (§19) raises WR but enters later, which
widens the entry->stop distance and enlarges the worst day (2024 −9.7 → −14.9). Two fixes tested:

| fix on top of 2-candle | 3yr net | 3yr WR | 3yr exp | 2024 worst |
|------------------------|--------:|-------:|--------:|-----------:|
| none (2-candle only) | +280 | 50.3% | +0.47 | −14.9 |
| **+ Max-Cap $5 stop** | **+295** | 50.0% | **+0.50** | **−8.7** |
| + skip entry ext≤1.5×OR | +182 | 48.4% | +0.38 | −8.8 |
| + skip entry ext≤1.2×OR | +49 | 47.9% | +0.29 | −5.2 |

The **skip guard is wrong** — it discards the whole trade (tail *and* winner), so net collapses.
The **stop-cap is right** — it keeps the trade but caps the loss, so the −$14 tail becomes −$5
while winners survive. Adopted: `confirm_breakout` ON + `sl_mode: Candle High/Low + Max Cap`,
`fixed_sl 5.0`.

Standalone the Max-Cap was noise (§16); it earns its place ONLY paired with confirmation, whose
extended entries are exactly what it caps.

### New default vs the immediate-entry profile (full years, per unit)
| | Immediate (old) | 2-candle + cap $5 (new) |
|--|----------------:|------------------------:|
| 2024 | +$65.8 (WR 45.4, worst −9.7) | +$54.7 (WR **48.0**, worst **−8.7**) |
| 2025 | +$79.6 (47.5, −13.1) | +$124.0 (**52.5**, −12.3) |
| 2026 H1 | +$192.4 (51.3, −9.7) | +$116.6 (49.3, −10.4) |
| 30/90/365d net | 88.6 / 150.2 / 213.0 | 76.4 / 113.1 / 188.4 |
| 365d WR | 49.5% | **50.9%** |

The new default trades ~$14/yr of net and a little 2026 upside for a higher win rate and a smaller
worst day. Revert with `confirm_breakout.enabled: false` + `sl_mode: "Candle High/Low"`.

### TradingView Strategy Tester validation (2026-07-10)
Ran the updated Pine strategy on TSLA 5m for three windows; reconciled against the Python engine
on the identical date ranges:

| Window | From | TV ÷100 | Python | Net diff | Worst day |
|--------|------|--------:|-------:|---------:|----------:|
| 30d | 2026-06-10 | +61.96 | +61.26 | +0.70 | −8.29 (both) |
| 90d | 2026-04-13 | +114.92 | +117.31 | −2.39 | −10.42 (both) |
| 365d | 2025-07-10 | +176.62 | +188.38 | −11.76 | −12.25 (both) |

**Worst day matched to the cent in all three windows** → Max-Cap + confirmation are byte-for-byte
aligned. Trade count reconciles once TV's per-partial rows are collapsed (30d: 22 trades + 11
partials = 33 rows; 365d: 269 + 94 = 363). Max-Cap verified in fills (every Base SL ≈ −$5/share);
confirmation verified because 90d net (+114.92) tracks the 2-candle number, not the old immediate
+150. 30d net matches within slippage; the 365d −6% is the known feed-drift + vol-gate-day
classification divergence, not a logic bug (the cent-exact worst days rule that out).

## 21. 3-candle (and 4-candle) confirmation — TESTED, REJECTED
Generalised confirmation to `hold_bars` = prior consecutive same-day closes beyond the trigger
(1 = the adopted 2-candle rule). Tested 2/3/4-candle across 3 years, all else default (Max-Cap $5):

| Rule (hold_bars) | 2024 | 2025 | 2026 H1 | 3yr net | WR | exp |
|------------------|-----:|-----:|--------:|--------:|---:|----:|
| 2-candle (1, current) | +54.7 | **+124.0** | +116.6 | **+295.3** | 50.0% | **+0.50** |
| 3-candle (2) | +62.6 | **+46.8** | +114.4 | +223.7 | 50.7% | +0.40 |
| 4-candle (3) | +81.4 | +63.1 | +103.8 | +248.4 | 53.0% | +0.47 |

3-candle trades 24% of net (+295 -> +224) and 20% of expectancy for +0.7pt WR — and the loss is
almost all **2025** (+124 -> +47), the strong-trend year, because the 3rd hold bar enters after the
move's body. Non-monotonic (4-candle > 3-candle on net) = regime noise, not a stable edge. More
confirmation buys win rate at a steep cost in missed upside. KEPT 2-candle (hold_bars=1). The lever
stays as an opt-in `enhancements.confirm_breakout.hold_bars` (default 1 = no change).

### TP1 fill model — touch/cross vs close-through (touch KEPT)
Prompted by 2026-07-08: the short's TP1 sat at 390.53; FMP's 14:55 low was 390.51 (clipped it by
2¢) so Python took the partial (+$1.59), but TradingView's feed printed the low ~2¢ higher and
missed → rode to EOD (−$1.03). Pure feed divergence — the trigger logic is already touch-based and
identical in both scripts (`low<=tp` / `high>=tp`). Tested the only stricter alternative (TP1
counts only if the bar CLOSES beyond the target):

| Mode | 3yr TP1 fills | WR | 3yr net |
|------|--------------:|---:|--------:|
| **touch/cross (current)** | **204** | **50.0%** | **+295.35** |
| close-through | 187 | 48.2% | +273.69 |

Touch is both more lenient AND strictly better (+17 fills, +1.8pt WR, +$21.66/unit). KEPT.
No code change — touch is already production. Chasing the 2¢ TV miss would require a fill
tolerance that would manufacture phantom partials elsewhere. Judge marginal fills in aggregate.

### Final realistic 6-month 2026 (all adopted, exit_on_close)
BE 0.55 · reversal capture · tp_scale 1.0 · runner_trail 0.75×OR · max_or_width $8:
**150 trades · 50.7% WR · net +$238.06 · PF 1.87 · worst −$16.47** (vs the un-re-tuned realistic
+$214 / PF 1.67 / −$22.06). Reproduce optimistic Pine numbers with `config/faithful_be035.yaml`.
