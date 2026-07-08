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

**Recommendation:** adopt **BE trigger 0.55, trail $0.25** as a tuned variant. It is provided
as `config/tuned_be055.yaml` so the verified 0.35 baseline stays intact for comparison:

```
python cli.py backtest --config config/tuned_be055.yaml --start 2026-01-01 --end 2026-07-08
```

## 4. Better alternative-trade capture (next step, not yet built)

The false-breakout days close opposite 74% of the time, yet the reversal only rescues 13. The
reversal often BE-stops itself or never triggers (needs a fresh opposite *buffered* close-break).
Candidate improvements to test next:
- Let the reversal **trail to EOD** (no self-BE choke) on trend-reversal days.
- Trigger the reversal off the **BE-stop event** directly rather than a second buffer break.
- Size the reversal target to OR width instead of a fixed $5.

These are proposals — each must clear the same train/holdout bar before adoption.
