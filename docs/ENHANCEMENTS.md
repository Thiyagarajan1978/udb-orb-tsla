# Enhancement proposal — Adaptive TP + Reversal (TSLA 5m)

The faithful port is the baseline (`config/profile:` — do not change it; the tests assert
it). Everything below is an **opt-in** improvement you enable in `config/enhancements:` (or
a small code addition), then **A/B against the baseline** on the same date range before
adopting. All shipped enhancements default **OFF**, so the baseline is always reproducible.

## How to A/B any enhancement
1. Run the baseline once and note the run id:
   `python cli.py backtest --start 2022-01-03 --end 2025-12-31`
2. Toggle one enhancement in `config/enhancements:`, run again with `--from-db`
   (reuses stored bars → identical data, only the rule changes):
   `python cli.py backtest --start 2022-01-03 --end 2025-12-31 --from-db --notes "rvol 1.3"`
3. Compare the two runs in the dashboard (Equity + Daily review tabs). Keep it only if it
   improves net **and** doesn't wreck win rate / worst-day.

Judge on **out-of-sample** data — the `tune` command already does rolling walk-forward so a
change that only helps in-sample gets caught.

---

## Ranked proposals

### 1. RVOL breakout confirmation  *(shipped: `rvol_filter`)*
- **Hypothesis:** Low-volume "breakouts" are the ones that fail and become BE/EOD scratches.
  Requiring the breakout bar to carry above-average volume should raise average win and
  cut dead trades.
- **Mechanism:** enter only if `breakout_bar_volume ≥ min_rvol × avg(prior N bars)`.
- **Config:** `rvol_filter.enabled: true`, tune `min_rvol` (1.1–1.5) and `lookback_bars`.
- **Watch-out:** too high a threshold starves the reversal leg (also volume-gated) — sweep
  it. Baseline June-2024: 28 trades / +$28.17 → `min_rvol 2.0`: 23 trades / +$15.69 (too
  strict); the sweet spot is lower. **Refinement:** compare against the *same time-of-day*
  average rather than a trailing window (opening bars are naturally heavier).

### 2. Time-of-day entry window  *(shipped: `time_window`)*
- **Hypothesis:** The cleanest ORB continuation happens mid-morning; late-afternoon breaks
  are chop that the EOD close scratches.
- **Mechanism:** only allow entries within `[start, end]` ET (e.g. 09:35–14:00).
- **Config:** `time_window.enabled: true`. Start with `09:35`–`14:00` and walk the end time.
- **Watch-out:** the reversal often triggers later in the day — don't clip it off.

### 3. OR-width regime gate  *(shipped: `or_width_regime`)*
- **Hypothesis:** The Pine docs attribute most of the (small) remaining losses to
  extreme-OR-width days. Since BE already caps the worst day near −$2, the bigger lever is
  *sizing/selection*: skip the widest days (whipsaw) and/or the narrowest (no follow-through).
- **Mechanism:** skip the day if `OR_width < skip_below` or `> skip_above`.
- **Config:** `or_width_regime.enabled: true`. Bucket OR width from stored `days.or_width`
  first (dashboard Daily-review tab), then set thresholds where expectancy turns negative.
- **Watch-out:** wide-OR days also produce the biggest winners — verify net, not just WR.

### 4. Walk-forward self-tuning of TP / BE / reversal  *(shipped: `tuning/walk_forward.py`)*
- **Hypothesis:** The single fixed `adaptive_tp_scale = 1.0`, `be_trigger = 0.35`,
  `reversal_target = 5.0` are not optimal across volatility regimes. Re-fitting on a rolling
  in-sample window and applying to the next out-of-sample window should add net P&L that
  survives regime change.
- **Mechanism:** `python cli.py tune --start 2021-01-01 --end 2025-12-31`. Grid over
  `adaptive_tp_scale`, `be_retrace_trigger`, `reversal_target`; objective = OOS net (PF
  tie-break); reports per-fold picks + a consensus.
- **Adopt only** the consensus params if OOS total is clearly positive and stable fold to
  fold; then set them in `config/profile:` as a *new tuned variant* (keep the original for
  comparison).

---

## Further ideas (not yet coded — propose before building)

| # | Idea | Rationale | Effort |
|---|------|-----------|-------|
| 5 | **ATR-scaled BE trail** (replace fixed `$0.25`) | 5m ATR varies 3–5× across regimes; a fixed trail gives back too much on high-vol days and chops out early on quiet days. | S |
| 6 | **Dynamic reversal target** = `k × OR_width` instead of fixed `$5` | Same adaptivity that helps the primary TP should help the reversal. | S |
| 7 | **Second scale-out** (e.g. 25% at 1×OR, 25% at 2×OR, 50% trails) | Locks more on partial-fail days without capping trend days. | M |
| 8 | **Realistic cost model for live** — set `slippage_per_unit: 0.02`, add commission | The backtest is gross; live P&L needs costs to be honest. | S |
| 9 | **Half-day / holiday calendar + data-integrity guard** | Early-close days have a 13:00 EOD; missing-bar days should be flagged, not silently traded. | M |
| 10 | **VWAP-side confirmation for the reversal only** | Reversals into VWAP resistance fail; requiring the reversal to be on the right side of VWAP may raise its hit rate. | S |
| 11 | **Portfolio/monthly risk report + Sharpe/expectancy tracking in DB** | Turn the ledger into a performance dashboard (per-month, per-weekday). | M |

## Guardrails
- Never change the engine math to make an enhancement look better — enhancements are gates
  and parameters layered *around* the verified core.
- Prefer fewer, higher-conviction rules. Every added filter cuts sample size; validate on
  OOS and watch that the reversal leg still has enough trades to matter.
