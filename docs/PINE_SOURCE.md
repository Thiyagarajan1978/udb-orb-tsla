# Source of truth

This project is a Python port of the TradingView Pine Script:

- **Indicator:** Unified Daily Breakout Suite
- **Version:** v12.4.3
- **Ported profile:** `Adaptive TP + Reversal (Best Combined)` only
- **Instrument / timeframe:** TSLA, 5-minute, buffer 10% of OR

## Resolved profile parameters (what the Pine branch computes)

| Setting | Value | Pine source |
|---------|-------|-------------|
| BE Retrace | ON, trigger 0.35, trail $0.25 | Auto-Tune @ 5m |
| BE trigger mode | wick-based (`beRetraceUseClose = false`) | not Pure Trail |
| Partial exit | ON, 25% closed / 75% trails | `partialQtyPct → 25` |
| Partial activation | $1.00 | Auto-Tune @ 5m |
| TP mode | Adaptive, `max($2.14, OR × 1.0)` | `tpMode → ADAPTIVE` |
| Reversal | ON, 2×, fixed $5 TP, BE applies | `useReversal → true` |
| Buffer | 10% of OR width | default |
| Stop (primary) | OR low (long) / OR high (short) | `sl_mode = Candle High/Low` |
| EOD close | 15:50 ET | v11.20 default |
| Anti-anachronism guard | implemented; inert unless close-based BE | v12.1 |

The full original Pine script is kept by the author outside the repo. If you change any of
the values above you have diverged from the verified port — `tests/test_params.py` will fail,
which is intentional.
