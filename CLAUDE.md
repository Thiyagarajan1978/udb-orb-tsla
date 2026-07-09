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
This is an **alerts-only** system: signals fire on the 5-minute **bar close**. `config.yaml`
sets `execution.exit_on_close: true`, so BE Stop / BE Trail / Base SL / runner-trail exits
trigger when a bar **closes** beyond the level and fill at that **close** — a BE stop is a real
~$2–4 loss, not a $0 scratch. This roughly **halves** reported P&L vs the optimistic
fill-at-stop model (6-month 2026: +$214 realistic vs +$522 optimistic) and widens the worst day
(−$22 vs −$3). The single biggest execution improvement is to place a **resting stop order** at
the BE level (broker OCO) instead of exiting manually on the close alert — that recovers the
fill-at-stop behaviour and caps the tail. `faithful_be035.yaml` keeps `exit_on_close: false` to
reproduce the Pine numbers.

## Defaults vs the faithful port
`config/config.yaml` is the **production default** and now carries three *validated* tunings on
top of the port (all cleared train 2024-25 + holdout 2026 — see `docs/BE_STOP_ANALYSIS.md`):
1. **BE trigger 0.55** (port was 0.35) — cuts premature BE-Stop failures.
2. **`reversal_capture` ON** (`trigger_on_be_stop` + `trail_to_eod`) — captures false-breakout
   reversal days in full.
3. **`adaptive_tp_scale` 1.25** (port was 1.0) — wider primary TP lets winners run (more net,
   slightly lower win rate).
4. **`runner_trail` ON @ 1.0×OR** — after the 25% partial, the remaining 75% trails 1×OR width
   below its peak (it previously had no trail until a BE retrace, so trend days gave the whole
   fade back). Banks more of the runner's peak; +net, +win rate, worst day unchanged.

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

## Run
- Backtest:  `python cli.py backtest --start 2024-01-02 --end 2024-12-31`
- Live:      `python cli.py live`  (alerts-only loop)
- Dashboard: `streamlit run ui/app.py --server.port 8080`  (or `scripts/run_ui.bat`)
- Tests:     `python -m pytest -q`

## Conventions
- Pure engine/indicator functions are network-free and unit-tested. Config-driven — no
  hardcoded params in the engine. Results in `data/` are gitignored.
