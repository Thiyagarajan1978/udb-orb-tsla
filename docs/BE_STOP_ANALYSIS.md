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

### Final realistic 6-month 2026 (all adopted, exit_on_close)
BE 0.55 · reversal capture · tp_scale 1.0 · runner_trail 0.75×OR · max_or_width $8:
**150 trades · 50.7% WR · net +$238.06 · PF 1.87 · worst −$16.47** (vs the un-re-tuned realistic
+$214 / PF 1.67 / −$22.06). Reproduce optimistic Pine numbers with `config/faithful_be035.yaml`.
