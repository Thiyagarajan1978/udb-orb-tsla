# CLAUDE.md — UDB-ORB-TSLA

## What this is
A Python live/backtest port of the TradingView Pine Script **"Unified Daily Breakout
Suite v12.4.3"**, restricted to the **Adaptive TP + Reversal (Best Combined)** profile on
**TSLA, 5-minute** bars. Opening Range Breakout (ORB): the first RTH bar (09:30–09:35 ET)
defines the range; a buffered close-break triggers the trade; adaptive TP + 25% partial +
75% BE-trail manage it; a 2× reversal fires after the primary stop.

**This system is ALERTS-ONLY.** It computes signals and logs every event to SQLite; it
never places broker orders.

## Golden rule — faithful port first
`src/udb_orb/engine/orb_engine.py` reproduces the Pine bar-by-bar state machine *exactly*
for this profile, including:
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

## Enhancements (default OFF, config `enhancements:`)
1. **RVOL filter** — breakout bar volume ≥ `min_rvol × avg`.
2. **OR-width regime gate** — skip days by opening-range width buckets.
3. **Time-of-day window** — only enter within `[start, end]` ET.
4. **Walk-forward tuning** (`tuning/`) — re-fit `adaptive_tp_scale` etc. from stored trades.

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
