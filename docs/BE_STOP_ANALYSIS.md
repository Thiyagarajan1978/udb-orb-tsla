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

## 22. System-improvement study — execution, sizing, generalization (2026-07-11)

Three levers tested after entry-filtering was exhausted (edge is in management, not entry).

**(1) Resting stop — REAL WIN, recommended.** Compared the three fill models across 3 years:
| fill model | 3yr net | WR | worst day | worst trade |
|-----------|--------:|---:|----------:|------------:|
| close (current, alerts-only) | +295.3 | 50.0% | −12.3 | −9.03 |
| **touch (realistic OCO, gap-aware)** | **+348.7** | 49.5% | **−7.8** | **−5.02** |
| stop (fill exactly @ stop, 0 slip) | +801.7 | 50.5% | −6.1 | −5.02 |
A real broker resting stop (touch model) is **+18% net AND ~halves the worst day** (−12.3→−7.8)
and worst trade (−9.03→−5.02). The +801 "stop" figure is the zero-slippage fantasy (the ~$450 gap
to touch = gap-through-stop cost). Adopting = moving from alerts-only to placing OCO stops.

**ADOPTED 2026-07-11.** `stop_fill_mode: close → touch`. New default full years (Run #56): 2024
229tr/47.6%/+95.67/PF1.41/worst-7.76 · 2025 221tr/51.1%/+119.02/PF1.38/worst-7.77 · 2026H1
140tr/50.0%/+134.04/PF1.73/worst-6.24 · combined 590tr/49.5%/+348.72/PF1.48/worst-7.77. Benchmarks:
30d +76.80/PF5.15/-5.04 · 90d +114.82/PF2.29/-6.11 · 365d +193.80/PF1.54/-7.60. BOTH Pine scripts
rewritten to native resting stops (strategy: two labelled `strategy.exit(stop=)` orders SL/TR so the
reversal arms only on SL; indicator: `exitOnClose` default OFF + gap-aware fill). Residual Pine-vs-
engine gap: a resting order moves only after a bar completes, so TV applies a newly-armed BE stop one
bar later than the engine (bar that arms BE *and* stops → TV fills base SL, Python ~entry; TV more
conservative). Needs TradingView re-validation via the CSV loop.

**(2) Vol-normalized sizing — REDUNDANT on TSLA.** Sizing each primary to a constant $5 risk vs
flat 1 unit: net +295→+297, worst day unchanged. The Max-Cap $5 already bounds per-trade risk, so
sizing only nudges the few sub-$5-stop trades. No benefit single-symbol. (Code reverted; its real
use is ATR-scaling levels for multi-symbol — see below.)

**(3) Multi-symbol generalization — EDGE IS TSLA-SPECIFIC at these settings.** Ran the default
engine on 7 liquid names, 2024-01→2026-07 (avgR = pnl/risk is price-neutral):
| sym | WR% | avgR | | sym | WR% | avgR |
|-----|----:|-----:|-|-----|----:|-----:|
| **TSLA** | **50.0** | **+0.10** | | NVDA | 43.2 | −0.00 |
| AMD | 47.8 | +0.02 | | GOOGL | 44.5 | +0.02 |
| MSFT | 46.0 | +0.01 | | META | 47.5 | −0.04 |
| AMZN | 46.7 | +0.01 | | (NFLX bad FMP data, excluded) |
TSLA's +0.10 avgR is 5x the best other name; everything else is ~0 with sub-50% WR. **The edge does
not generalize.** CAVEAT: dollar params (Max-Cap $5, TP floor $2.14, OR-gate $8) are TSLA-price-
scaled — $5 is 0.8% on META ($631, punishingly tight) vs 2.5% on NVDA ($203). So "no edge" is
confounded with "levels mis-scaled." The clean re-test = express Max-Cap/TP/OR-gate as %/×ATR, then
re-run the basket. Until then: KEEP single-symbol TSLA; do not deploy on other names.

## 23. Liquidity sweeps / stop-fill slippage — slippage bumped 0.02 -> 0.10 (conservative)

A resting stop becomes a MARKET order when triggered, so it can fill WORSE than the level (a "sweep"
wicks through the stops and reverses). The 5m backtest fills AT the level and CANNOT see sub-bar
sweep wicks (no tick/1m data on this FMP plan), so the touch model is optimistic on stop fills.
Every trade's main leg exits via a stop/market fill (only the 25% TP partial is a protected limit),
so slippage hits nearly the whole book. Stress (extra $/share on all stop/market exits, on top of base):

| extra $/sh | 3yr net | worst day |
|-----------:|--------:|----------:|
| 0.00 | +348.7 | -7.8 |
| 0.10 | +289.8 | -7.9 |
| 0.25 | +201.4 | -8.2 |
| 0.50 | +54.1  | -8.7 |
| 1.00 | -240.6 | -9.7 |

~$59/unit per $0.10/share; edge survives to ~$0.50, breaks even ~$0.60. TSLA is very liquid so real
avg slip is likely $0.05-0.15, not $0.50 — but the $0.02 default was too light for stop fills.
ADOPTED `slippage_per_unit 0.10` as the conservative working baseline. New default (Run #57):
2024 +74.51/PF1.30 | 2025 +100.60/PF1.31 | 2026H1 +122.49/PF1.65 | combined 590tr/49.2%/+297.60/
PF1.39/worst-7.91. Benchmarks 30/90/365: +74.98(PF4.92)/+108.99(PF2.18)/+171.42(PF1.46). The one
number the backtest CANNOT give is your true stop-slip — paper-trading must MEASURE it. stop-market
= guaranteed exit + slip; stop-limit = no slip but risks not filling (bigger loss). Use stop-market.

## 24. Re-tune under the resting stop — confirmation DROPPED ("Config A" adopted as default)

Ablating the current default (all at touch + $0.10) showed features tuned under CLOSE-FILL are now
suboptimal — the resting stop caps whipsaws at $5, so "avoid-whipsaw" filters lost their benefit but
kept their cost:

| variant (touch+0.10) | 3yr net | PF | worst |
|----------------------|--------:|---:|------:|
| CURRENT (confirm ON) | +298 | 1.39 | -7.9 |
| **Config A: confirm OFF** | **+469** | **1.65** | -8.9 |
| Config B: confirm+runner OFF | +574 | 1.79 | -8.9 |
| (net-max but strips risk control) no-max-cap | +352 | 1.48 | **-13.7** |

**ADOPTED Config A** (confirm OFF, keep runner-trail + ALL risk controls). Better every year:
2024 251tr/45.0%/+137.9/PF1.58 | 2025 236tr/47.0%/+103.7/PF1.32 | 2026H1 151tr/53.6%/+227.8/PF2.46 |
combined 638tr/47.8%/+469.4/PF1.65/worst-8.85/143rev. Benchmarks 30/90/365: 27tr/+79.27/PF3.93 |
78tr/+160.79/PF3.01 | 290tr/+278.95/PF1.81. Trade-off: WR 49->48% (-2pt) for +57% net. Config A =
`config.yaml` default now; Config B saved as `config/tsla_best_B.yaml`. NOTE: net-max ablations that
strip risk controls (max-cap, vol gate) were NOT chosen — they add net but remove tail protection
(no-max-cap worst -13.7). Re-enable confirmation ONLY if reverting to close-fill.

## 25. OR-midpoint stop (LuxAlgo "moderate 1:1.5") — TESTED, REJECTED

Idea (LuxAlgo ORB page + user): stop at the OR MIDPOINT — cancel the breakout if price pulls back
through the middle of the opening range. Added sl_mode "OR Midpoint" / "OR Midpoint + Max Cap".
Tested vs Config A (all at touch + $0.10):

| stop mode | 2024 | 2025 | 2026 | 3yr | avg risk |
|-----------|-----:|-----:|-----:|----:|---------:|
| Max-Cap $5 (Config A) | +138 | +104 | +228 | **+469** | $4.5 |
| OR Midpoint | +125 | +101 | +210 | +436 | $3.7 |
| OR Midpoint + Max-Cap | +129 | +100 | +211 | +440 | $3.6 |
| OR boundary (no cap) | +135 | +131 | +236 | +501 | $5.4 |

Midpoint is WORSE in ALL 3 years (+436 vs +469). The tighter stop (avg $3.7 vs $4.5) causes
premature exits: the OR midpoint acts as an early-session CHOP MAGNET (the page even calls it a
"magnet"), so price routinely dips through it before the real move, stopping us out. The resting
stop already caps losses cleanly at the boundary/cap, so a tighter invalidation costs more in lost
runners than it saves. KEPT as an opt-in sl_mode; NOT adopted.

Bonus finding: OR boundary with NO cap = +501 (> Config A +469) under the resting stop — but it
raises per-trade risk (avg $5.4, worst ~$8.8) for marginal net; KEPT the Max-Cap for tail safety
(same "don't strip a risk control for net" rule as §24). Other LuxAlgo techniques were already
present or already rejected: candle-close entry (have), RVOL/volume filter (built, off — entry
filters all fail), trend/gap alignment (§ pre-market: real but unexploitable), runner-trail (have),
vol gate (have, but ours skips HIGH vol — a low-vol system), EOD/time exit (have).

## 26. ATR-scaled stop cap — TESTED, IMPROVES TSLA + unlocks multi-symbol

The fixed dollar params were fitted to TSLA ~$400. But TSLA's ATR(14) ranged **4.6x** over 2024-2026
($6.2 -> $28.8), so the fixed $5 stop silently meant **0.24x to 0.80x ATR** at different times (tight
in high-vol 2025, loose in calm 2024). Added sl_mode "Candle High/Low + ATR Cap" (cap = atr_mult*ATR,
ATR shifted = no lookahead). Test vs fixed $5 (Config A, TSLA):

| stop cap | 2024 | 2025 | 2026 | 3yr | avg risk |
|----------|-----:|-----:|-----:|----:|---------:|
| Fixed $5 (current) | +138 | +104 | +228 | +469 | $3.7->$4.9 |
| ATR 0.35x | +137 | +108 | +232 | +477 | $3.3->$5.3 |
| ATR 0.40x | +136 | +107 | +236 | **+479** | $3.5->$5.6 |
| ATR 0.50x | +135 | +107 | +236 | +478 | $3.8->$5.8 |

ATR cap is +~2% net (+469 -> +479), modestly better (neutral 2024, small gains 2025/2026). Plateau
0.35-0.50x (robust, not a fit peak); mechanism sound (constant vol-normalized risk); worst day
~unchanged; avgRisk now ADAPTS ($3.5 calm -> $5.6 volatile). (NOTE: an earlier run showed +503/+7%
but that was a NaN-fallback BUG — the first ~14 days had no ATR and ran UNCAPPED; fixed, clean = +479.)
So on TSLA-alone the win is small. The REAL prize is MULTI-SYMBOL (§22): with ATR-scaled levels the
fixed dollar params (max-cap/TP-floor/OR-gate) stop being TSLA-price-specific, so the edge can scale
across symbols. Kept as opt-in sl_mode; the decisive next test = ATR-normalize ALL fixed levels + re-run
the basket. (OR-based TP already adapts — OR/ATR ~0.31 stable — only the FIXED params need scaling.)

## 27. Full ATR-normalization sweep — the OR-GATE is the winner, not the stop

Extended ATR-scaling to ALL fixed-$ params via `atr_normalize` enh (per-param stop/gate/rev toggles +
mults), isolating each (Config A, TSLA):

| ATR-normalized | 2024 | 2025 | 2026 | 3yr | worst |
|----------------|-----:|-----:|-----:|----:|------:|
| Baseline (fixed $) | +138 | +104 | +228 | +469 | -8.8 |
| stop only 0.40x | +138 | +103 | +228 | +468 | -8.5 |
| **gate only 0.55x** | +121 | **+139** | +223 | **+483** | -8.8 |
| reversal only 0.40x | +138 | +104 | +229 | +471 | -10.1 |
| all (0.40/0.55/0.40) | +118 | +141 | +222 | +481 | -9.4 |

**The OR-WIDTH GATE is the fixed param that benefits from ATR (+3%, +469->+483), all from high-vol
2025 (+104->+139).** The fixed $8 gate got too tight as TSLA's ATR grew (skipping wide-OR days that
were only "wide" in dollars, not in vol) — ATR-scaling the gate lets those good 2025 days back in, with
the SAME worst day (-8.8). The STOP is neutral (§26) and REVERSAL is neutral-with-worse-tail (-10.1).
Aggressive multi-param tunes hit +503 (0.35/0.50/0.35) to +535 (looser) but worsen the worst day (-9 to
-11) and are overfit-prone — NOT chosen. Clean takeaway: **ATR-normalize the OR-gate** (single-param,
mechanistically clear, robust); leave stop/reversal fixed. `atr_normalize` kept opt-in (default off).
Still the key to MULTI-SYMBOL (§22).

## 28. ATR gate adoption REJECTED (per-year) + multi-symbol RE-TEST (edge now generalizes!)

**Task 1 — ATR OR-gate per-year (TSLA), for adoption:** the +3% (§27) is a 2025-ONLY effect:
| year | fixed $8 | ATR-gate | delta |
|------|---------:|---------:|------:|
| 2024 | +137.9 | +121.1 | **-16.8** |
| 2025 | +103.7 | +138.7 | +35.0 |
| 2026 | +227.8 | +223.0 | -4.8 |
Worse in 2024 AND 2026, better only in high-vol 2025. Fails the "robust across all years" bar (the
usual single-year trap). NOT adopted — kept fixed $8. `atr_normalize` stays opt-in.

**Task 2 — multi-symbol RE-TEST under Config A, FIXED vs ATR-normalized (avgR = price-neutral):**
| sym | FIXED avgR | ATR avgR | | sym | FIXED avgR | ATR avgR |
|-----|-----------:|---------:|-|-----|-----------:|---------:|
| TSLA | +0.149 | +0.140 | | AMZN | +0.077 | +0.083 |
| NVDA | +0.044 | +0.048 | | GOOGL | +0.056 | +0.064 |
| AMD | +0.073 | +0.081 | | MSFT | +0.087 | +0.077 |
| META | +0.070 | +0.066 | | | | |

TWO findings: (1) **ATR-normalization barely moves generalization** (±0.01, mixed) — it is NOT the key
to multi-symbol after all. (2) **THE EDGE NOW GENERALIZES:** under Config A ALL 7 symbols have POSITIVE
avgR (+0.04..+0.15), even with fixed $ params — vs §22's "TSLA-only (+0.10, others ~0.00)". What changed
was NOT ATR but **Config A itself** (resting stop + confirmation OFF): §22's multi-symbol ran on the OLD
close-fill/confirmation config. So the earlier "edge is TSLA-specific" was a config artifact. TSLA is
still strongest (+0.149) but AMD/MSFT/AMZN are viable (+0.07-0.09) → a diversified basket is now real.
ATR gives nothing here; the generalization came free with the Config A upgrades.

## 29. ORB timeframe — 5-minute is best (15m/30m tested, REJECTED)

Resampled 5m -> 15m/30m (OR = first bar at each resolution), ran Config A and B, 2.5yr TSLA:

| OR TF | trades | WR | Config A net | Config B net | worst |
|-------|-------:|---:|-------------:|-------------:|------:|
| **5m** | 638 | ~47% | **+469** | **+574** | -8.8 |
| 15m | 438 | ~48% | +222 | +217 | -7.3 |
| 30m | 288 | ~53% | +176 | +178 | -7.3 |

5m makes **2-3x more net** in both configs. 15m/30m have HIGHER win rate (up to 53%) and SMALLER worst
day (-7.3) but far FEWER trades -> much less total profit; per-trade quality (PF) is similar (30m 1.68
~= 5m 1.65), so 5m wins by sheer number of shots at the same edge. Config B > A at 5m (+574 vs +469);
at 15m/30m they tie (fewer trends for the VWAP runner to ride). CAVEAT: 15m/30m use 5m-calibrated $
params; re-tuning might narrow the gap but wouldn't beat 5m on net. DECISION: stay 5-minute, run A + B.

## 30. OR start-time + timeframe + hybrid decision-resolution (all explored)

**Breakout timeframe (2026, OR = first bar @ 9:30):** 1m +218/40%WR · 3m +110/46% · **5m +229/53%** ·
15m +222 · 30m +176. 5-MINUTE is the clean peak — finer (1m/3m) adds noise (low WR), coarser (15m/30m)
loses shots. SETTLED at 5m.

**OR start-time (3-year, both configs):** shifting market_open later. 09:45 wins the 3yr TOTAL and is
better in 2024+2025 (A: 09:45 +554 vs 09:30 +469; B: +621 vs +574) — but the NEIGHBORHOOD is NOISY, not
a plateau: Config A 09:40 +511 / 09:45 +554 but 09:35 +431 / 09:50 +426; B 09:35 +628 / 09:45 +621 but
09:50 +539. 09:45 is worse in 2026. VERDICT: suggestive (later start skips opening noise, helps size-off
years) but the bumpy surface = overfitting risk. KEPT 09:30 as the principled/robust default; 09:45 is a
documented alternative worth paper-trading (market_open is a 1-line config change). NOT hardcoded.

**Hybrid (wider OR + 1m decision) — REJECTED.** Added `or_bars` (OR spans N bars from open) + tested on
1m data: 15m OR @9:45 with 1m decision = +64 (A) vs current +229; ALL 1m-decision variants (+64/+73/+86)
crushed by 5m-decision (+229). The 1-minute breakout is too NOISY — price crosses the level constantly
on 1m -> false signals, WR 35-44%. The 5-minute CLOSE is the right breakout-decision resolution. `or_bars`
kept opt-in (default 1). DECISION RESOLUTION matters as much as OR width: 5m close >> 1m close for entries.

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

## 31. Immediate reversal on the BE Stop bar — BUILT, TESTED, REJECTED (2026-07-28)

**Question (from the chart):** when the primary BE-stops, why wait for a close back through the
opposite OR boundary? Why not flip *right there*, on the bar that stopped us?

Built as `enhancements.reversal_capture.immediate_on_be_stop` (default **OFF**): the flip enters at
the same 5m close the BE stop filled at. Two stop placements were tested, because a BE-stop fill
frequently lands *inside* the OR (or, after a short primary, still below the OR low) — parking the
flip's stop at the usual OR boundary then puts it on the **wrong side of entry**, or a few cents
away, which the risk-parity cap turns into an absurd size:
- `swing` (default) — stop at the primary's failed extreme.
- `or_boundary` — the standard reversal stop, falling back to `swing` when invalid.
Sizing reuses the `reversal_risk_cap` parity, but may only **shrink** the 2× base, never inflate it
off a tight stop. `immediate_min_risk_or_mult` (0.15×OR) drops flips with a noise-tight stop.

C1, per unit, close-mode stops, `scripts/immediate_reversal_test.py`:

| window | base (wait for OR break) | imm-swing | imm-orb | no reversal |
|---|---:|---:|---:|---:|
| 2026-05-27→07-24 (41 sess) | **+85.7** (PF 2.60) | +72.1 | +82.3 | +60.9 |
| 1 year → 2026-07-24 | +224.6 (PF 1.53) | +227.3 | **+250.0** | +204.3 |
| 2024-2026 | **+265.7** (PF 1.28) | +237.2 | +271.5 | +237.1 |
| 2022-2026 | **+435.9** (PF 1.25) | +370.1 | +422.3 | +290.3 |

**The wait is a filter, not latency.** Over 2022-26 the immediate flip roughly **doubles** the
reversal count (198 → 374) while total reversal P&L **falls** (+145.6 → +79.8) — the ~176 extra
flips are collectively net-negative. Reversal WR drops 38.9% → 35.3%. `imm-orb` looks close to base
only because its wider stop declines the worst flips; it still never beats base on 2024-26 or
2022-26 and carries a worse worst-day (−13.8 vs −13.3) and worse maxDD.

Per-day detail over the 2-month window makes the mechanism visible: **12** days had a primary BE
Stop. Base entered only **3** flips (2 of them the EOD trend winners, +10.26 and +8.43). Immediate
entered all **12**; 7 lost outright and 2 more were ~breakeven, and most died within 1-4 bars (enter
10:15, BE-stopped 10:25) — the flip is entering into chop *inside* the opening range. The day was
**worse with immediate on 8 of the 12**. Net on those 12 days: **−23.62 base vs −37.27 immediate**. Requiring price to travel the full OR is what distinguishes a genuine failed breakout
from range noise; the reversal's own stop is only meaningful once price has crossed the range.

Also tested `immediate_reasons` = all primary stops (Base SL / BE Trail / BE Stop) — identical to
BE-Stop-only, no additional edge.

### All five profiles (2026-07-28) — same verdict, and it is a REGIME TRAP

`--profiles all`. On multi-year *window* totals `imm-orb` looks like a winner: it beat base on
2024-2026 and on the trailing year in **every** profile (A1 +17.6, B1 +17.6, C1 +5.7, C2 +28.0,
D1 +23.4). Per-year (`--years`) shows that is one year wearing a disguise:

| profile | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
|---|---:|---:|---:|---:|---:|
| A1 | −17.1 | −30.8 | −11.0 | **+24.8** | +3.8 |
| B1 | −17.1 | −26.4 | −11.0 | **+24.8** | +3.8 |
| C1 | −3.1 | −22.9 | −7.5 | **+2.3** | +11.0 |
| C2 | −28.6 | −40.9 | −17.3 | **+28.8** | +16.5 |
| D1 | −13.1 | −30.8 | −9.1 | **+28.8** | +3.8 |
| **sum(5)** | **−78.8** | **−151.7** | **−56.0** | **+109.5** | **+38.9** |

(delta = imm-orb − base, per unit; + = the immediate flip helped)

**It loses 2022, 2023 AND 2024 in all five profiles**, and the two worst losing years are the true
OOS ones. The entire "edge" is **2025 plus a thin 2026** — and even inside 2026, the trailing
2-month slice is negative for all five (A1 −2.8, B1 −2.8, C1 −3.5, C2 −0.2, D1 −2.8). D1's maxDD
also degrades badly (−56.1 → −66.6 over 5yr) while `imm-swing` loses across the board everywhere.

That per-year signature — **wins 2025, loses 2022/23/24** — is the same one that killed the midline
trigger (§ 2026-07-25, "wins 24/25, loses 22/23/26"). Early reversals pay in mean-reverting years
and bleed in trending ones; the profile you pick does not change that, because all five profiles
share one entry engine and only differ in how the runner exits. Every attempt to make the reversal
enter *earlier and more often* has now cost money on TSLA: midline trigger, mid-price entry limits,
immediate flip. **The OR-break wait stays.**

## 32. Prior-day levels (PDH/PDL/PDC/PDO) — BUILT, MEASURED, REJECTED (2026-08-10)

Triggered by 7 losing-trade screenshots that visually looked "blocked by a prior-day line" after
Pine v3.9.3 put the levels on the chart. Trade table with all four levels + 20 derived features:
`exports/trades_with_pd_levels_3yr.csv` (710 trades, 2023-08-01..2026-07-30). Engine run ONCE, sliced
by `exit_ts.year` throughout.

**The screenshots do not survive measurement.** 3 of the 4 matched BE-stop failures had CLEAR headroom
(no level between entry and target); the 4th had no level ahead at all. With four lines drawn, price is
always near one.

**"It died at the line" is a confound.** Losers peak a mean 0.84x TP from the nearest level, winners
1.63x — but only because winners TRAVEL further (mean MFE 1.84x TP vs 0.43x). Control for distance run
and peaking ON a level is equal or BETTER than peaking nowhere near one, in all five MFE bands.

**Loss anatomy (3yr):** BE Stop 73.6% + Rev BE Stop 16.0% = **89.6% of all loss dollars**. BE-stop
losers peak a median **5 minutes** after entry at 0.30x TP; winners peak at a median 105 minutes. The
loser is dead on the entry bar, before any level is in play.

Rejected, all per-year:
- **9 entry filters** (inside-PDR, headroom <0.75/<1.0, level density >=3, OR-straddles-pdC / ->=2,
  thin base, gap -1..-0.3%, obstacle=pdC). Only "skip obstacle=pdC" is positive over the window (+500)
  and it loses $1,398 in 2026 — the live regime.
- **OR straddles the prior-day OPEN** looks clean (43.1% win vs 49.1%) but runs +999/+680/-129/-686/+841
  over five years and is net POSITIVE — skipping it costs money.
- **Cap the TP at the level**: -$5,769, negative all 5 years. **Bank the 25% partial there**: -$1,479,
  negative all 4 years (50%: -$2,958). **Bail when a bar closes back through the level**: -$1,182..-$2,498
  at every threshold.
- **No-progress TIME-STOP** (the level-free version of the same instinct — the losers are visibly dead
  in one bar), 18 settings (exit at bar 2/3/4/6/9/12 if MFE < 0.20/0.35/0.50 x TP): all 18 lose,
  -$460 to -$8,933, negative in 2023 + 2024 + 2025 + 2026.

### 32b. `pd_level_exit` — the tag-and-reject exit, wired into the engine

User's framing: not "the level predicts failure" but "a REVERSAL happens at one of the 4 areas — exit
there instead of riding to the BE stop." Distinct from `pdh_pdl_filter` (an entry gate) and from a plain
take-profit-at-the-level (no reversal required). Built as enhancement **`pd_level_exit`, default OFF**:
a bar that tags a level (within `zone` x the adaptive TP distance) and CLOSES rejected off it flattens
the remaining position at that close. Last in the exit precedence chain, so stop / TP / runner-trail /
VWAP-cross still own the bar. Verified inert when off (A1/B1/C1/D1 byte-identical, 44 tests pass).

**Real-engine sweep, 4 profiles x 7 configs x 2022-2026: 0 of 28 cells improve all five years, and
every single cell is net NEGATIVE.**

| profile | best config | n fired | baseline | net | delta | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| C1 | PDC only, ahead, zone 0.05 | 125 | 13,219 | 12,607 | **-612** | +622 | -1,263 | +1,154 | 0 | -1,125 |
| A1 | PDC only, ahead, zone 0.05 | 117 | 13,517 | 12,537 | -980 | +428 | -1,349 | +1,011 | +256 | -1,326 |
| D1 | PDC only, ahead, zone 0.05 | 111 | 12,641 | 11,313 | -1,328 | +112 | -1,485 | +969 | +337 | -1,261 |
| B1 | PDC only, ahead, zone 0.05 | 122 | 13,897 | 12,369 | -1,528 | +185 | -1,311 | +1,154 | -239 | -1,317 |

Widening it makes it worse monotonically: all-4-levels/ahead/zone 0.05 = -3,796..-5,466; any-direction
= -4,455..-6,138. The PDC-only variant wins 2024 on all four profiles and loses 2023 AND 2026 on all
four — a regime, not an edge.

**Mechanism (A1, zone 0.05, ahead, reject candle):** fires on 321 of 1,122 trades (29%) and is right
100 times (31%). It saves **+$9,546** on the losing cohort (BE Stop +5,792 / Rev BE Stop +3,425 /
Base SL +329) and gives back **-$13,824** of winners (Trail -5,298 / EOD -4,509 / Rev EOD -3,355).

**A tag-and-reject at a prior-day level is followed by CONTINUATION ~69% of the time** — the same law
as the rejected liquidity-sweep gate (sweeps fuel continuation). The levels genuinely mark where price
hesitates; hesitation carries no information about which way the day resolves.

**Durable rule:** the losing trade is identifiable within 5 minutes but **not separable** — flat-at-5-min
is equally the opening of a winner. Every rule that acts on the observation, at any threshold, has now
lost money. Keep the v3.9.3 lines as chart context only.

> **Superseded in part — see §32c.** One filter on the tag-and-reject exit does survive: a *small-bodied*
> rejection candle (`max_body_frac` < 0.25). It flips the rule positive on all four profiles and passes a
> 30-shift placebo test at p = 0.032. Still default OFF pending out-of-sample validation.

---

## §32c — Separating the good fires: `max_body_frac`, the one survivor (2026-08-10)

§32b closed the tag-and-reject exit as a net loser. But the mechanism line hid a question nobody had
asked: the rule fires on 321 trades and is **right 165 times (51%)** — it saves $12,367 on the trades
that were going to lose and gives back $16,645 on the trades that were going to win. If anything
observable **at the fire bar** separates those two halves, the rule flips positive.

`pd_exit_separate.py` (scratchpad) measured every candidate. Open P&L at the fire, minutes since entry,
clock time, which level, VWAP side, MFE-so-far, bar range, direction, primary-vs-reversal — **all of them
sit at 43-57% good.** Nothing.

**One feature grades monotonically: the body of the rejection candle** (`|close-open| / (high-low)`).

| body / range | n | good % | delta | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| **< 0.25** | 78 | **62%** | **+1,660** | +286 | +191 | +211 | +403 | +570 |
| 0.25-0.50 | 89 | 43% | -3,643 | -347 | -690 | -465 | -1,136 | -1,005 |
| 0.50-0.75 | 102 | 54% | -1,109 | +21 | -216 | +146 | +34 | -1,094 |
| > 0.75 | 52 | 46% | -1,186 | -494 | -430 | -153 | +342 | -452 |

**This is the opposite of the intuitive read.** A large decisive bar closing back off the level is
worthless — it is a trend bar that happens to touch a line, and price continues (the same continuation
law as the liquidity-sweep and tag-and-reject results). What predicts a genuine rejection is a **small
body: a doji or pin that runs into the level and cannot close through it.**

Wired into the engine as `pd_level_exit.max_body_frac` (default **1.0 = no filter**, so the shipped
behaviour is unchanged). Real engine, ONE run per profile sliced by `exit_ts.year`, at `max_body_frac:
0.25` with `levels: all 4 | ahead_only | zone 0.05 | require_reject_candle` (`ahead_only` was later
turned OFF by user call — see §32d):

| profile | baseline | delta | yrs up |
|---|---|---|---|
| A1 | 13,517 | **+1,248** | 4/5 |
| B1 | 13,897 | **+2,983** | **5/5** |
| C1 | 13,219 | **+3,487** | **5/5** |
| D1 | 12,641 | **+755** | 4/5 |

+21% (B1) / +26% (C1) of net on the two traded profiles. **4 of 24 configs improve all five years**,
against 0 of 28 for the unfiltered rule in §32b.

Threshold shape is a **plateau, not a spike** — the failure mode that killed the R-multiple TP work:
`<0.10` +825 (5/5), `<0.15` +1,302 (5/5), `<0.25` +1,660 (5/5), then it breaks — `<0.35` +207,
`<0.40` -874, `<0.50` -1,888.

### Placebo test — PASSES (p = 0.032)

The attribution worry: is this the **prior-day levels**, or merely "exit on any small-bodied bar"?
Re-ran the identical rule with the levels taken from session **t-2 … t-31** instead of t-1 (30 placebos):

```
REAL (t-1)      : +1,338    (2022 +86, 2023 +109, 2024 -50, 2025 +945, 2026 +247)
placebo mean    : -1,128    sd 1,037
placebo min/max : -3,426 / +938     positive: 6 of 30
z-score of real : 2.38
rank of real    : 1 of 31   ->  empirical p = 0.032
```

No placebo beat the real levels. This clears the p<0.05 bar the in-play scanner failed at p=0.116.

### Caveats — why it stays OFF for now

1. **The 0.25 threshold was chosen in-sample over all 5 years. There is no holdout.** This is exactly
   the sin that produced the retracted R-multiple result.
2. The optimum is **profile-dependent** (B1/C1 favour 0.25, D1 favours 0.15) — one shared number is
   already a compromise.
3. Adjacent thresholds swing ~2x and the effect is dead by 0.35-0.40. The plateau is narrow.
4. A1 is negative in 2023 (-151); D1 is negative in 2023 (-578) at 0.25.
5. 71% of A1's post-hoc gain is 2025 alone (less concentrated in the engine runs — 2025 is 46% of B1's).

**Disposition: default OFF. Validate forward in the options/shares forward test alongside the frozen
strategy; revisit for adoption only if it holds on unseen sessions.** §32's "not separable" conclusion
stands for every feature except this one — the losing trade is still not identifiable at entry, but the
*fire* is gradeable after the fact by the shape of the bar that triggers it.

### Cross-market check: SPX says NO (2026-08-10)

> **CORRECTION 2026-08-19:** the SPX dollar figures in this section are INVALID -- the
> baselines it reconciles to (BOT1 +$205,025 / BOT3L +$269,300) came from a quote book that
> merged expiries. Correctly priced, BOT1 is **-$33,515** over 2022-2026. The *conclusion*
> here is unaffected: this section rejects the SPX small-body exit, and it is rejected on
> the corrected data too -- every variant is negative before and after. See
> `docs/SPX_ANALYSIS.md` for the corrected table.

The same protocol was re-run on the SPX system — real 5m SPX bars 2022-01-03..2026-07-16, real OPRA
quotes, both automated legs (BOT1 15m-OR ts30, BOT3-LONG 60m-OR ts50) — with the exit priced at the bid
on the fire bar's close minute. Harness `spx_pd_body_test.py` (scratchpad); it reconciles **exactly** to
the documented baselines (BOT1 +$205,025 / 1,107 trades / WR 48%; BOT3L +$269,300 / 656 / WR 54%), so
the trade stream is right and only the exit rule is under test.

```
BOT1  (base +205,025)        fires  delta   2022  2023  2024  2025  2026  yrs_up
  body<0.15                     16  -2,350  +850  -800  -270 -1,940  -190     1
  body<0.25                     27  -3,610   +80  -940  -100 -2,120  -530     1
  body<0.35                     41  -7,950   +80  -400  -100 -2,530 -5,000    1
  no body filter               110 -23,790 -3,185  -210 -8,825 -4,900 -6,670   0

BOT3L (base +269,300)        fires  delta   2022  2023  2024  2025  2026  yrs_up
  body<0.15                     17  -4,350  +260 -1,420  +870 -4,190  +130    3
  body<0.25                     22  -4,140  +260 -1,420 +1,170 -3,250  -900   2
  no body filter                63 -18,785 -1,670 -1,690 -5,040 -9,735  -650   0

PLACEBO (levels from t-2..t-31, body<0.25)
  BOT1 : REAL -3,610 | placebo mean  -495 sd 1,595 | pos 12/30 | z -1.95 | rank 30/31 -> p=0.968
  BOT3L: REAL -4,140 | placebo mean -2,181 sd 3,732 | pos 10/30 | z -0.52 | rank 24/31 -> p=0.774
```

Every threshold is negative on both legs, and the placebo verdict is the **mirror image** of TSLA's: on
TSLA the real levels ranked 1st of 31 (p=0.032, better than every shuffle); on SPX they rank 30th and
24th — the *real* prior-day levels are worse than most random ones. The body filter still orders the
damage correctly (tighter = less bad, -2,350 vs -23,790), i.e. the "small body = real rejection" reading
of the candle is not wrong; there is simply no exit edge at prior-day levels on the index to harvest.

**Conclusion: `max_body_frac` is TSLA-specific.** Consistent with the standing finding that the ORB edge
itself is TSLA-specific (SPY: no edge; 8 of 9 liquid names: no edge). Do not port it to the SPX bots.

---

## §32d — The "buffer" question, and `ahead_only: false` ADOPTED BY USER CALL (2026-08-11)

Prompted by three losing sessions the user flagged — 2026-08-04, 08-05, 08-11 — with the request to add
a **buffer** to the PD rule so it exits failures earlier and lifts the win rate.

### The buffer cannot reach those days

`pd_level_exit` only looked at levels **ahead** of the entry. Geometry of the four legs (B1, final bars):

| day | leg | nearest level AHEAD | closest approach | `zone` required |
|---|---|---|---|---|
| 08-04 | S 09:40 @321.77 → −2.45 | pdO 311.05, **10.72 away** | short $9.75 | 2.17 = **43× shipped** |
| 08-05 | L 09:35 @324.72 → −1.91 | pdC 327.45, 2.73 away | short $0.31 | 0.094 = 2× shipped |
| 08-11 | S 09:35 @330.51 → −0.90 | pdO 326.76, 3.75 away | short $2.77 | 1.18 = 24× shipped |
| 08-11 | L rev 10:00 @334.86 → −2.50 | **none — all four behind** | — | **impossible at any size** |

Three of four legs are structurally out of reach, and the 08-11 reversal (74% of that day's loss) was in
blue sky above pdH/pdC/pdO/pdL, where no prior-day rule can ever act. 08-05's near miss also fails on a
second count: the 10:05 bar that came within $0.31 of pdC **closed up**, so there was no reject candle.
Confirmed by running the days — `zone` 0.05 → 0.10 → 0.20 leaves all three **bit-identical at −7.76/unit**.

### `zone` sweep — 0.05 is a true peak, and a wider buffer is a 2026 regime trap

B1, one engine run per config split by `exit_ts.year`, `max_body_frac` held at 0.25:

```
 zone   n   PDx   WR%    net    PF     2022    2023    2024    2025    2026  pre-fit
 0.00 1128  114  46.3  562.86  1.35  128.32   44.28   41.70  205.34  143.22  293.56
 0.05 1126  138  46.2  575.93  1.36  141.34   51.92   37.74  199.74  145.19  309.85 <- shipped
 0.10 1124  157  46.7  561.62  1.36  139.43   39.50   27.25  192.46  162.99  286.43
 0.15 1123  171  46.7  557.16  1.36  128.45   28.59   30.07  200.62  169.43  274.23
 0.20 1121  184  46.0  513.42  1.33  108.22   24.62   27.69  184.10  168.79  235.83
 0.50 1115  230  44.8  454.64  1.31   94.04   11.46    6.24  176.62  166.28  169.27
```

Same shape on A1/C1/D1. Worse at 0.00, monotone decay above, and `pre-fit` (everything before the knob's
2025-08-11 fitting window) decays monotonically too — so 0.05 is a real optimum, not a fitting artifact.
Note the win-rate column: **0.10-0.15 does raise WR (46.2 → 46.7) while net falls $14-19** — the buffer
delivers the requested statistic by clipping winners. And **2026 is the only year that improves, in every
variant** — the §31 signature, and the only year the motivating days come from.

### What was actually in those days: `ahead_only: false` ("both sides")

08-04 shorted **$0.33 below pdC**; 08-11 shorted **$0.34 below pdC**. Both broke a prior-day close by a
third of a dollar and were immediately reclaimed — a level *behind* the entry, invisible to `ahead_only`.
Dropping that guard makes a reclaim of a broken level an exit too.

| B1 | n | PDx | WR% | net | 2022 | 2023 | 2024 | 2025 | 2026 | pre-fit |
|---|---|---|---|---|---|---|---|---|---|---|
| ahead_only (was shipped) | 1126 | 138 | 46.2 | **575.93** | 141.34 | 51.92 | 37.74 | 199.74 | 145.19 | **309.85** |
| both sides, zone 0.05 | 1120 | 191 | 44.2 | 558.32 | 120.11 | 46.82 | 32.91 | 196.32 | 162.16 | 278.79 |
| both sides, zone 0.20 | 1112 | 245 | 44.1 | 517.08 | 96.62 | 24.36 | 15.49 | 193.29 | 187.31 | 214.11 |

All four profiles, per unit 2022-01-03..2026-08-10, both-sides @ 0.05 vs ahead-only:
**A1 485.6 / 502.0 · B1 558.3 / 575.9 · C1 556.4 / 573.2 · D1 480.3 / 483.5.**

It does fire on the motivating days — 08-04 exits 09:50 for −1.12 instead of 10:05 for −2.45, 08-05 exits
11:05 for −1.29 instead of 11:15 for −1.91, 3-day total −7.76 → **−5.81**. 08-11 is untouched (still −3.40).

**Verdict: the measurement says NO** — 4 of 5 years worse on B1, WR 46.2 → 44.2, pre-fit slice −31, and
only 2026 gains. It buys $1.95 on three self-selected 2026 days for ~$17.6/unit across the history.

**ADOPTED ANYWAY 2026-08-11 on the user's explicit call**, after the above was presented. Shipped in all
five yamls, the engine fallback (`ahead_only` now defaults False) and **Pine v3.9.8** (new input "Levels
AHEAD of entry only", default UNTICKED). `zone` deliberately stays **0.05** — widening it adds nothing to
the motivating days and costs a further ~$41/unit. Revert = `ahead_only: true` + tick the Pine box.

### TV A/B on 16 years — the measurement CONFIRMED, ~2.5× worse (2026-08-12)

The user exported the v3.9.8 strategy twice, 45 seconds apart, changing **only** the new checkbox
(`UDB-ORB_v3.9.8_S_NASDAQ_TSLA_2026-08-11_1f33f.csv` = UNTICKED/both-sides, 1202 `PD Level` exits;
`…_e7521.csv` = TICKED/ahead-only, 963). NASDAQ:TSLA 5m, **2010-06-29 → 2026-08-11, 4,522 trades**,
zero slippage, 20-share base. Harness `scratchpad/tv_pair.py`.

```
 year   both-sides    ahead-only        diff        year   both-sides    ahead-only        diff
 2020       542.67        372.87     +169.80        2024      1147.75       1336.16     -188.41
 2021      5076.23       5133.27      -57.04        2025      3374.99       3494.29     -119.30
 2022      2862.53       3310.83     -448.30        2026      3758.56       3523.24     +235.32
 2023      1496.27       1874.41     -378.14       TOTAL     18196.20      18983.67     -787.47
```

**2010-2019 is unreadable and must be excluded** — TSLA traded $1.18-$24 split-adjusted there against a
$2.14 minimum TP and a $0.25 BE trail, so the dollar params have no meaning; those ten years total ±$60
of noise. (General rule for any long TV export on this system: the usable window starts ~2020.)

On the real window it loses **5 of 6 years** and wins only 2026 — the §31 signature, independently
reproduced. 2022-2026 = **−$44.9/unit** vs the Python sandbox's −$17.6/unit: same sign, same per-year
shape, ~2.5× the magnitude, and TV charges no slippage so it is the flattering estimate.

**Of the three motivating days the change moves exactly one.** 08-04 −23.20 (PD Level 09:50) vs −49.00
(BE Stop 10:05) — matches Python's −1.16 vs −2.45/unit to the cent. **08-05 and 08-11 are bit-identical
in both exports.** 08-11 was predicted untouched; **08-05 was not** — Python tags pdO (behind by $0.72)
on the 11:05 retrace and exits −1.29, TV holds to the 11:15 BE Stop. One leg of four, inside the known
~91% PD concordance band, but it is the only open Python/TV divergence on this feature.

So the feature bought one of three days at a measured cost of $787 across the history.
**Recommend revert** (`ahead_only: true` + tick the Pine box); left in place pending the user's call.

### Half-day flatten still leaks (Pine-only, found in the same export)

Both exports carry two `EOD flat` rows and neither is what v3.9.7 intended:

```
 #2190  Entry short 2018-11-23 10:10 @22.00  ->  Exit 2018-11-26 15:50  EOD flat  -40.40  (130 bars)
 #585   Entry short 2012-11-23 09:40 @2.13   ->  Exit 2012-11-26 15:50  EOD flat   -0.40  (118 bars)
```

Both entries are on the **Friday after Thanksgiving, a 13:00 half day**. v3.9.7 was supposed to flatten
on that session's own last bar (~12:55); instead the position carried 2.5 days and the flatten fired on
Monday's regular 15:50 — the fix catches the carry a session late rather than preventing it. Two
occurrences in 16 years, and Pine-only (Python handles half days via `last_ts_by_date`), so low priority,
but **open, not closed**.

Clean in the same export: **0 entries after 12:00** (17-18 exactly at 12:00, matching the Python inclusive
boundary), and both `PD Level` and `REV PD Level` present. The export does not record the profile; the
per-year shape (strong 2022, weak 2024, strong 2025/26) says **B1 or C1** and rules out A1 and D1 — it
does not affect the verdict, since the A/B is same-profile against itself.
