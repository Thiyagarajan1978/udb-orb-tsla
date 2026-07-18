# CLAUDE.md — UDB-ORB-TSLA

## What this is
A Python live/backtest port of the TradingView Pine Script **"Unified Daily Breakout
Suite v12.4.3"**, restricted to the **Adaptive TP + Reversal (Best Combined)** profile on
**TSLA, 5-minute** bars. Opening Range Breakout (ORB): the first RTH bar (09:30–09:35 ET)
defines the range; a buffered close-break triggers the trade; adaptive TP + 25% partial +
75% BE-trail manage it; a 2× reversal fires after the primary stop.

**This system is ALERTS-ONLY.** It computes signals and logs every event to SQLite; it
never places broker orders.

## Execution realism (IMPORTANT)
**ADOPTED 2026-07-14 for the TRADED profiles B1/C1 → `stop_fill_mode: close` (CLOSE-triggered stop).**
The stop fires ONLY when a 5m bar *closes* beyond the level (fills at the close); the TP still fills on a
favorable wick. Walk-forward over 2022-2026: **+42-46% net, ~40% smaller drawdown, and 2024 flips from a
loss to a profit** vs the wick/resting stop — OOS-confirmed on 2022-23 (both never part of the discovery).
It skips the wick-fakeout stop-outs that dominate choppy years; the trend year barely notices. **TV-validated
2026-07-15**: the wired v3 Pine strategy (Stop trigger=Close) reconciles to Python close-mode within 1.3%
(C1) / 2.8% (B1) at zero slippage. NOTE: TV's Strategy Tester models NO slippage → haircut TV numbers ~12%
for the realistic $0.10/share fills. `config/tsla_best_B.yaml` + `tsla_config_C1.yaml` carry this default;
the v3 indicator uses `exitOnClose` (default ON), the v3 strategy a `Stop trigger: Close|Wick` input.

Signals fire on the 5-minute **bar close**. **(Prior, 2026-07-11 — now superseded for B1/C1 by close above):**
`config.yaml` sets `execution.stop_fill_mode: touch` — a **real broker resting stop (OCO)**. Stop-type exits
(Base SL / BE Stop / BE Trail / runner peak-trail) fill **intrabar at the level**, gap-aware (a bar
opening beyond the stop fills at the worse open). This lifts 3-year net **+18% (+$295 → +$349/unit)**
and roughly **halves the worst day (−12.3 → −7.8)**, because BE stops fill at ~entry instead of a bar
close far below. **This means the system is no longer purely alerts-only for the stop leg — you must
place resting stops with the broker.** Set `stop_fill_mode: close` for the prior manual/alerts model
(BE stop = a real ~$3.68 close loss). `stop` mode (fill exactly at the stop, zero-slippage) is the
optimistic fantasy — used only by `faithful_be035.yaml` for Pine parity. See BE_STOP_ANALYSIS §22.
`slippage_per_unit` is **0.10** (was 0.02) — a conservative allowance for stop-fill slippage /
liquidity sweeps (a resting stop becomes a market order; the 5m backtest can't see sub-bar sweep
wicks). Haircuts net ~15% (+348.7 → +297.6). Paper-trading must MEASURE true fills. See §23.

## Defaults vs the faithful port
`config/config.yaml` is the **production default** and now carries three *validated* tunings on
top of the port (all cleared train 2024-25 + holdout 2026 — see `docs/BE_STOP_ANALYSIS.md`):
1. **BE trigger 0.55** (port was 0.35) — cuts premature BE-Stop failures.
2. **`reversal_capture` ON** (`trigger_on_be_stop` + `trail_to_eod`) — captures false-breakout
   reversal days in full.
3. **`adaptive_tp_scale` 1.0** — re-tuned under realistic fills (optimistic tuning liked 1.25,
   but a wider TP just rides more trades into a real BE-stop loss). Equals the Pine port value.
4. **`runner_trail` ON @ 0.75×OR** — after the 25% partial, the runner trails 0.75×OR below its
   peak (it previously had no trail until a BE retrace). Re-tuned from 1.0 under realism.
5. **`max_or_width` ≤ $8** — skips wide-OR whipsaw days (the realistic tail driver: primary +
   2× reversal both take real close losses). Cuts worst day (−22→−16) with net flat-to-up.
0. **`volatility_regime` ON (rvol20 ≤ 4.92%)** — this is a **low-vol breakout system**. With
   close-based BE stops, a high-vol bar closes further past the stop, so BE-stop cost scales with
   volatility. The top vol quintile is the only net-negative bucket. Skipping it rescues 2024
   (PF 1.07→1.19) and cannot touch 2026 (no high-vol days there). Threshold set on 2024-25 only.
6. **`reversal_risk_cap` $6 (scale)** — RISK PARITY. The reversal enters after price crossed the
   whole OR, so its stop is far away; at 2× size it carried 1.6× the primary's risk ($10.03 vs
   $6.16) and caused **68% of worst-day damage**. Scaling its qty to equal dollar risk cuts the
   worst day 41% (−16.5→−9.7) for 18% net = **+40% return per $1 of worst-day risk**.
   (`skip` mode was tested and rejected — regime-dependent.)

Built but **NOT adopted** (default OFF, opt-in): `reenter_after_whipsaw` (marginal/regime-dependent
OOS) and `pdh_pdl_filter` (require close beyond prior-day high/low when it's near the break level —
barely triggers on TSLA; see `docs/BE_STOP_ANALYSIS.md` §6).

To reproduce the **exact Pine v12.4.3 numbers**, use `config/faithful_be035.yaml` (BE 0.35,
reversal_capture OFF). `tests/test_params.py` asserts both: the tuned default AND the port.

## Golden rule — faithful port first
`src/udb_orb/engine/orb_engine.py` reproduces the Pine bar-by-bar state machine *exactly*
for this profile (with `faithful_be035.yaml`), including:
- Auto-Tune @ 5m: BE trigger 0.35, BE trail $0.25, partial activation $1.00.
- Adaptive TP distance = `max($2.14, OR_width × 1.0)`, used as a fixed distance from entry.
- Wick-based BE Retrace (this profile is NOT Pure Trail), BE trail = `high − 0.25` (long).
- 25% partial at TP → disable TP → remaining 75% trails; VWAP-cross exit after
  profit ≥ activation.
- Reversal: after primary SL, on opposite buffered close-break, enter 2× size, fixed $5 TP,
  BE applies. Max one reversal/day (primary + reversal = 2 trades max).
- EOD forced close at 15:50 ET.
- v12.1 anti-anachronism guard only bites when `be_retrace_use_close` is true (Pure Trail);
  it is inert here but implemented for parity.

The tests in `tests/` assert the resolved profile params and known single-day trade
outcomes. **Do not change engine math to make an enhancement look good** — enhancements are
separate, toggleable, and default OFF.

## Enhancements (config `enhancements:`)
1. **RVOL filter** (default OFF) — breakout bar volume ≥ `min_rvol × avg`.
2. **OR-width regime gate** (default OFF) — skip days by opening-range width buckets.
3. **Time-of-day window** (default OFF) — only enter within `[start, end]` ET.
4. **Reversal capture** (default **ON** — adopted) — `trigger_on_be_stop` + `trail_to_eod`.
5. **Walk-forward tuning** (`tuning/`) — re-fit `adaptive_tp_scale` etc. from stored trades.

Enable one at a time and compare to the baseline before trusting it.

## Data — FMP only
- Provider: Financial Modeling Prep **stable** API. Key in `.env` as `FMP_API_KEY`.
  - 5-minute intraday: `/stable/historical-chart/5min` (signal + fill resolution).
- FMP intraday timestamps are naive wall-clock ET → **localize, don't convert**. Paged in
  ~5-day chunks (≈450-row cap); error payloads arrive as a dict; 5m cached to `data/cache/`.
- No 1-minute on this plan, so 5m bars also drive the intrabar fill model. Fill priority is
  **stop-first** on any bar (matches Pine's SL-over-TP tie-break).

## Layout
```
src/udb_orb/
  config.py            # yaml + .env
  data/fmp_client.py   # FMP 5m fetch + cache
  engine/params.py     # resolved profile params
  engine/indicators.py # session VWAP, RVOL, OR width
  engine/orb_engine.py # faithful bar-by-bar state machine
  engine/enhancements.py
  db/database.py       # SQLite schema + writers
  alerts/notifier.py   # Resend email + webhook
  backtest/runner.py   # historical run -> DB + summary
  live/runner.py       # poll FMP, feed engine, alert + persist
  tuning/walk_forward.py
cli.py                 # backtest | live | tune | init-db | fetch
ui/app.py              # Streamlit B Square dashboard (:8080)
```

## Options forward test (out-of-sample, Databento shadow — no broker, no money)
`forward_test.py` prices the FROZEN strategy's new-session signals against REAL TSLA option quotes
(Databento OPRA `cbbo-1m`, buy-ask/sell-bid), for all 4 profiles at BOTH expiries (0DTE nearest + weekly
Friday), and APPENDS to `exports/forward_options_ledger.csv` (gitignored — it's data). Idempotent; OPRA
releases T+1 so it prices up to the last fully-available session. Needs `DATABENTO_API_KEY` (env or .env).
- Run:      `python forward_test.py`                 (prices new sessions since the ledger)
- Backfill: `python forward_test.py --start 2026-07-10 --end 2026-07-16`
- Schedule: `run_forward_test.bat` via Task Scheduler ~9:00 AM ET (T+1 after close). ~$0.05/day.
This validates the SIGNAL edge going forward; it still assumes fills at the quote — TradersPost paper
trading is the complementary test for real fill quality.

## Run
- Backtest:  `python cli.py backtest --start 2024-01-02 --end 2024-12-31`
- Live:      `python cli.py live`  (alerts-only loop)
- Dashboard: `streamlit run ui/app.py --server.port 8080`  (or `scripts/run_ui.bat`)
- Tests:     `python -m pytest -q`

## Conventions
- Pure engine/indicator functions are network-free and unit-tested. Config-driven — no
  hardcoded params in the engine. Results in `data/` are gitignored.
