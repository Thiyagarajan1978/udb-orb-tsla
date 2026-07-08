# UDB-ORB-TSLA

A Python live/backtest system that ports the TradingView Pine Script **"Unified Daily
Breakout Suite v12.4.3"** — restricted to its best profile, **Adaptive TP + Reversal
(Best Combined)** — to **TSLA, 5-minute** bars, driven by **Financial Modeling Prep (FMP)**
data and backed by a **SQLite** ledger with a **Streamlit** dashboard.

> **Alerts-only.** The system computes signals and logs every event; it never places
> broker orders.

## What it does

Opening Range Breakout (ORB):
1. The first regular-session 5-minute bar (**09:30–09:35 ET**) sets the opening range (OR).
2. A **buffered close-break** (10% of OR width beyond the OR edge) enters long/short at the
   bar close.
3. **Adaptive take-profit** distance = `max($2.14, OR_width × 1.0)`. At TP, **25%** is
   closed and the remaining **75% trails** on a break-even ratchet (`high − $0.25` for
   longs); after profit ≥ $1.00 it exits on a **VWAP cross**.
4. **BE Retrace** moves the stop to entry once price retraces to `OR_high − 0.35×OR` — this
   is why hard stop-losses are almost never hit.
5. After a primary stop, on the **opposite** buffered close-break, a **2× reversal** fires
   with a fixed $5 target (BE applies). Max one reversal/day.
6. Everything still open is **force-closed at 15:50 ET**.

The engine (`src/udb_orb/engine/orb_engine.py`) reproduces the Pine bar-by-bar state
machine exactly for this profile; `tests/` locks the behavior down.

### Validation (real FMP data, TSLA 5m)
| Range | Trades | Win rate | Net P&L | PF | Worst day | Reversals |
|-------|-------:|---------:|--------:|---:|----------:|----------:|
| 2024 full year | 317 | 96.8% | +$413.73 | 59.5 | −$1.70 | 74 |

Exit mix (BE 190 · Partial 87 · VWAP 47 · EOD 80 · Base-SL 0 · full-TP 0) matches the
profile's mechanics — BE protection means near-zero hard stops.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # then paste your FMP_API_KEY

python cli.py init-db
python cli.py backtest --start 2024-01-02 --end 2024-12-31
python cli.py live --once     # single alerts poll (or `live` for the loop)
streamlit run ui/app.py --server.port 8080   # dashboard  (or scripts\run_ui.bat)
python -m pytest -q
```

## CLI

| Command | Purpose |
|---------|---------|
| `init-db` | Create the SQLite schema. |
| `fetch --start --end` | Pull TSLA 5m bars into cache/DB. |
| `backtest --start --end [--from-db] [--no-cache]` | Run the engine, persist a run, print a summary. |
| `live [--once]` | Alerts-only live loop. |
| `tune --start --end [--is-days N] [--oos-days N]` | Walk-forward parameter search. |

## Configuration

Everything lives in `config/config.yaml`. The `profile:` block is the verified port —
changing it diverges from Pine (tests assert the defaults). The `enhancements:` block adds
optional, **default-OFF** features you can A/B against the baseline:

- **`rvol_filter`** — require breakout-bar volume ≥ `min_rvol × trailing average`.
- **`or_width_regime`** — skip days whose OR width is below/above thresholds.
- **`time_window`** — only enter within `[start, end]` ET.

See [docs/ENHANCEMENTS.md](docs/ENHANCEMENTS.md) for the ranked improvement proposal and how
to test each one.

## Data & alerts

- **FMP stable API** (`/stable/historical-chart/5min`). Key in `.env` as `FMP_API_KEY`.
  Timestamps are naive ET (localized, not converted); 5m bars also drive the intrabar fill
  model (stop-first). Bars cache to `data/cache/`.
- **Alerts** (optional): Resend email (`RESEND_API_KEY`, `ALERT_FROM`, `ALERT_TO`) and/or a
  generic webhook (`ALERT_WEBHOOK_URL`). With none configured, events print to the console.

## Layout

```
src/udb_orb/    config · data/fmp_client · engine/{params,indicators,orb_engine,metrics}
                db/database · alerts/notifier · backtest/runner · live/runner · tuning/walk_forward
cli.py          command line
ui/app.py       Streamlit dashboard (:8080)
tests/          faithful-port behavior tests
```

## Disclaimer
For research and personal use. Not investment advice. Backtest results are not guarantees;
5-minute intrabar fills are approximate. Trade at your own risk.
