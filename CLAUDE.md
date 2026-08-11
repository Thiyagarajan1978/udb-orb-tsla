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

**Do not re-propose making the reversal enter earlier.** Three variants are now built, swept and
rejected — `midline_trigger`, mid-price entry limits, and `immediate_on_be_stop` (flip on the
BE-Stop bar instead of waiting for the opposite OR break; swept on all 5 profiles 2026-07-28, §31).
All three share one signature: they win 2025 and lose 2022/2023/2024. The OR-break wait is a
**filter**, not latency. **Method rule from §31: multi-year WINDOW totals hid a 3-losing-year
regime trap — always run the per-year table** (`scripts/immediate_reversal_test.py --profiles all
--years`) before believing any entry-timing "win".

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
3. **Time-of-day window** (default **ON** since 2026-07-26, end 12:00) — no NEW entries (primary
   or reversal) after noon; open positions manage to their normal exits. Post-noon entries were a
   net-negative cohort over 5 years (the 12:00-13:00 lunch hour is the toxic pocket). WR up in
   every tested year-cell on A1/B1/C1/D1 incl 2022-23 OOS, net +3-4%, worst day never worse.
   Enabled in config.yaml + the A1/B1/C1/D1 yamls; Pine v3.9 "Entry cutoff" input is the twin.
4. **Reversal capture** (default **ON** — adopted) — `trigger_on_be_stop` + `trail_to_eod`.
5. **Walk-forward tuning** (`tuning/`) — re-fit `adaptive_tp_scale` etc. from stored trades.

Enable one at a time and compare to the baseline before trusting it.

## Data — FMP only
- Provider: Financial Modeling Prep **stable** API. Key in `.env` as `FMP_API_KEY`.
  - 5-minute intraday: `/stable/historical-chart/5min` (signal + fill resolution).
- FMP intraday timestamps are naive wall-clock ET → **localize, don't convert**. Paged in
  ~5-day chunks (≈450-row cap); error payloads arrive as a dict; 5m cached to `data/cache/`.
- The 5m bars drive the intrabar fill model. Fill priority is **stop-first** on any bar
  (matches Pine's SL-over-TP tie-break). NOTE 2026-07-20: the plan NOW serves **1-minute**
  bars (probe returned a full session) and **extended hours** (`extended=true`, 04:00–19:55)
  — the old "no 1-minute" limitation is gone; a 1m-resolution fill model is now possible.
  Extended-hours 5m 2025-26 cached at `data/cache/TSLA_5min_ext_2025_2026.parquet`.

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
(Databento OPRA `cbbo-1m`, buy-ask/sell-bid), for all 5 profiles (A1/B1/C1/C2 + experimental D1
ATR-trail runner, added 2026-07-23) at BOTH expiries (0DTE nearest + weekly Friday), and APPENDS to
`exports/forward_options_ledger.csv` (gitignored — it's data; forward-only, seeded 2026-07-10 — the
long-history options numbers below come from separate analysis scripts, NOT this ledger).
`--profiles X` prices a subset with per-profile idempotency (how D1 was aligned to 2026-07-10). **Judge it on the
`*_opt_1ct_bc` columns** (bar-CLOSE fills = when the alert actually fires). The legacy `*_opt_1ct` columns
price at the bar-START quote — a 5-min lookahead found 2026-07-20 that inflated the published options
figures ~6x (corrected: A1 +$12.9k / B1 +$11.2k / C1 +$10.1k @1ct 2025-01→2026-07; C2 −$1k pre-v3.5,
+$4.2k under the OR-width TP). SPY cross-check: NO edge
(shares and options both negative) — the ORB edge is TSLA-specific. Idempotent; OPRA
releases T+1 so it prices up to the last fully-available session. Needs `DATABENTO_API_KEY` (env or .env).
- Run:      `python forward_test.py`                 (prices new sessions since the ledger)
- Backfill: `python forward_test.py --start 2026-07-10 --end 2026-07-16`
- Schedule: `run_forward_test.bat` via Task Scheduler ~9:00 AM ET (T+1 after close). ~$0.05/day.
This validates the SIGNAL edge going forward; it still assumes fills at the quote — TradersPost paper
trading is the complementary test for real fill quality.

**EXPIRY — settled 2026-08-09, `Friday (weekly)` ADOPTED (Pine v3.9.2, options profile A1).**
`TSLA listed FRIDAY-ONLY expiries until 2026-02-02` (first Mon/Wed contract in the 2026-01-26 OPRA
definition snapshot; cached defs: 2025 = 49 Fri + 2 Thu, ZERO Mon/Wed; 2026 = 24 Mon / 23 Wed / 36 Fri).
So **every options figure published for 2025-01→2026-07 is really a WEEKLY (1-4 DTE) number** — the
"0DTE" label is a misnomer, and both prior expiry verdicts compared a contract to ITSELF (tell-tale:
D1 2025 dte0 +$3,560 vs wk +$3,570). On 2026 data, where the legs finally differ, **weekly beats
true-0DTE on 5 of 5 profiles across 3 independent windows**, at ~22% more premium.
**The options edge is REGIME-CONCENTRATED, not a steady drip:** weekly @1ct, Jan 2-May 31 2026
(109 trades, 5 months) = A1 **+$785** / D1 +$462 / C1 −$316 / C2 −$387 / B1 −$1,116, vs Jun 1-Jul 17
(34 trades) = A1 **+$7,743**. **91% of 2026's options P&L is 34 of 143 trades.** Size for five flat
months. NOTE the options ranking ≠ the shares ranking — A1 leads on options, **B1 is the worst**
despite being a traded shares profile. Harness: `weekly_2026_jan_may.py` (scratchpad, argv start/end);
it cross-validates to the cent against `forward_test.py` — mismatches are pre-noon-cutoff ledger rows
(16 of 26 forward reversals entered after 12:00, priced before the cutoff was adopted 2026-07-26).

## Paper runner (LIVE, alerts-only) — started 2026-07-31
`python cli.py live --profiles B1,C1` runs **both traded profiles in one process** (`scripts/run_live.bat`,
Task Scheduler **"UDB-ORB-TSLA Paper Runner"**, Mon-Fri 9:25 AM ET, `-StartWhenAvailable`). One process =
one FMP fetch per cycle and no chance of one profile being silently dead. `--dry-run` forces console-only
alerts (still writes the DB) — always use it when testing. `TRADED_PROFILES` in `cli.py` is the allow-list;
A1/C2 were dropped and must not be added without a fresh validation run.

Five live-only invariants, none of which a backtest can catch (see `tests/test_live_runner.py`):
1. **`profile.label` (B1/C1) scopes everything.** The two share a profile NAME and a db_path, so without
   the label the re-alert guard lets one suppress the other's identical event — you simply never hear
   about half your trades. It is also on every alert message and in the webhook payload.
2. **The seen-set seeds over the whole `lookback_days` window, not just today.** The engine replays a
   multi-day lookback, so its event stream always contains prior sessions; seeding only from today
   re-appends them to `events` on every restart.
3. **Prior-session events are persisted but never alerted.** On the first poll under a new label the
   seen-set is empty; without the same-day guard the runner mails 3 days of history at once.
4. **`open_session=today` must be passed to `run_engine`.** `last_ts_by_date` comes from the DATA — in a
   backtest every session is complete so its last bar legitimately flattens (this is the half-day
   handling), but live "the last bar" is just the newest closed bar. Without the flag the engine closes
   every open position on every poll and fires a fresh `eod_exit` at a NEW timestamp each time (~30 false
   "close your position" alerts per trade per day).
5. **A bar is not usable the moment it closes — FMP is still aggregating it** (fixed 2026-08-11,
   `live.bar_settle_seconds`, default **300s** = one full bar). FMP serves the PARTIAL 5m bar for
   ~2-3 minutes past its nominal close; measured by polling every 10s, three bars settled at close
   **+168s / +192s / +214s** (the 14:50 bar read `c=331.89 v=90,790` at +137s vs a final
   `c=331.85 v=115,049`). The old `close_time <= now` test therefore fed the engine half-formed bars:
   an audit of the first 11 live sessions found **52 of 87 events priced off a non-final close**, every
   one inside the final bar's H/L range (the partial-snapshot signature), worst 2026-08-03
   `primary_entry` logged 314.81 vs a real 318.00. Worse, a later poll's corrected bar moves the
   event's TIMESTAMP, and the re-alert guard keys on exact (ts, type, direction) — so the correction
   mails as a brand-new signal (three `primary_entry` alerts on 2026-08-10 for one trade; duplicates on
   4 of 11 sessions). `_superseded_by` now tags any same-session repeat of a (type, direction) as
   `*** CORRECTION` in the alert and logs `!! SUPERSEDED`. Cost: a 09:35 signal mails ~09:40:30.
   Live-only — historical bars are final, so backtests and the TV reconciliation are unaffected.

Corollary: **do not backtest through the current session** — the partial day's last bar produces an
artificial `eod_exit`. Reconciled 2026-07-31 over 07-28..07-30: live and backtest event streams are
identical (11 events per profile).

## Multi-symbol research — NO-GO for a second symbol (2026-07-31)
`scripts/multi_symbol_test.py` ran the FROZEN B1/C1 (no per-symbol tuning) over 2024-01-02..2026-07-09
on TSLA + 9 liquid names, at a fixed **$10k notional** (`trade_qty: 1.0` yields per-SHARE P&L, so raw
net is not comparable across prices) and **price-scaled slippage** ($0.10 was calibrated on a ~$300
TSLA). ATR-normalization of the dollar params (`atr_normalize`: stop 0.40 / gate 0.55 / rev 0.40) was
tested ON and OFF.

**Result: 1 of 9 non-TSLA symbols profitable; mean −15%.** Every non-TSLA symbol lands at PF 0.84-1.04
(breakeven noise) while TSLA sits at 1.23-1.28, and the two "winners" swap identity between the
norm-ON and norm-OFF variants — i.e. noise, not a second edge. **Trade TSLA only.** Note the profiles
still carry dollar params `atr_normalize` does NOT cover (`adaptive_tp_min` $2.14, `reversal_target`
$5.00, `partial_activation` $1.00, `be_trail_amount` $0.25) — which is why the universe must stay in a
TSLA-like price band rather than be re-tuned. Re-tuning per symbol is fitting under another name.

**Refuted hypothesis:** "it needs a high-ADR symbol." corr(ADR%, ret%) = **+0.53** full-sample looks
supportive, but TSLA is the single dominant point — remove it and the correlation collapses to **+0.08**
(norm ON) / **−0.25 to −0.32** (norm OFF). Not evidence.

**Open, unproven:** `scripts/in_play_test.py` tests the alternative reading — that ORB needs whichever
symbol is IN PLAY that day, not a fixed second symbol. Selecting the top-1 daily by |gap %| (known at
the 09:35 OR close, no lookahead) returns **+21.3% (B1) / +25.2% (C1)** vs a mean fixed non-TSLA of
−19.4%/−22.1% and a 500-draw random-pick null of −9.0%/−11.4%, picking TSLA only 26% of days. But
**p = 0.116 / 0.072 → fails p<0.05**, 2024 is negative (−20.2%/−17.2%), and the median selected gap is
only 2.09% — an 8-symbol universe rarely contains a genuinely gapping name. `scripts/fetch_universe.py`
pulls a 69-symbol character-selected universe to power this properly; as of 2026-07-31 only **15** are
cached — FMP returned **HTTP 429** on the rest at `--workers 8`. Re-run it (it skips cached files) with
fewer workers before re-testing.

## Run
- Backtest:  `python cli.py backtest --start 2024-01-02 --end 2024-12-31`
- Live:      `python cli.py live --profiles B1,C1`  (alerts-only; add `--dry-run` to test)
- Dashboard: `streamlit run ui/app.py --server.port 8080`  (or `scripts/run_ui.bat`)
- Tests:     `python -m pytest -q`

## Conventions
- Pure engine/indicator functions are network-free and unit-tested. Config-driven — no
  hardcoded params in the engine. Results in `data/` are gitignored.
