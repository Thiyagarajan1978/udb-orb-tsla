# Trading Configs — quick reference (for live / paper trading)

## ✅ FINAL CHOSEN SETUP (2026-07-13): **Config A + Config B, 5-minute, OR start 09:30, 100 shares**
Run both A and B in parallel (paper) and pick by preference: **A = steadier** (peak-trail runner),
**B = higher net** (VWAP runner, +$574 vs +$469/unit over 3yr). All the range/timeframe/start-time
exploration converged here — see decisions at the bottom.

TSLA, **5-minute** ORB, Regular Trading Hours. Two configs (A & B), each as indicator + strategy, each
with a **09:30 / 09:45 Opening-Range start** selector. All numbers are **per 1 unit**; at **100 shares**
multiply by 100.

## The 4 Pine files
| File | Config | Runner exit |
|------|--------|-------------|
| `pine/UDB_ORB_TSLA_v2_A.pine` / `_A_strategy.pine` | **A** | Peak-Trail (0.75×OR below peak) |
| `pine/UDB_ORB_TSLA_v2_B.pine` / `_B_strategy.pine` | **B** | VWAP-Cross (ride to close-thru-VWAP) |

Everything else is identical: resting stop (touch), Max-Cap $5, confirmation OFF, vol gate, reversal
risk-parity, $0.10 slippage. Python parity: A = `config/config.yaml`; B = `config/tsla_best_B.yaml`.

## Config comparison
| | Config A | Config B | Config C |
|--|--------:|--------:|--------:|
| Runner / TP | Peak-trail runner | VWAP runner | **fixed $2 TP, full exit** |
| 2.5-yr net (24-26, /unit) | +$469 | **+$574** | +$298 |
| Profit factor | 1.65 | 1.79 | 1.59 |
| Win rate (2026) | 53.6% | 51.7% | **67.6%** |
| Style | steadier | higher net | **highest win-rate / consistency** |

### Config C-ATR — volatility-adaptive take-profit (the "increase both" answer)
`config/tsla_config_C_atr.yaml` — TP = **0.25 × 14-day ATR** (auto-widens in high-vol regimes, tightens
in calm ones), $6 BE-stop, full exit. A new `tp_mode: "ATR"` in the engine (Fixed/Adaptive unchanged).
**It shifts the whole profit frontier outward ~$5k/100sh at any given win rate** and generalizes
out-of-sample (walk-forward, mult fit on 2022-24 → OOS 2025-26 **+$40,578** vs flat-$2 C's +$21,811).
2022-2026 net @100sh: 2022 +20,022 · 2023 +17,823 · 2024 +12,520 · 2025 +15,618 · 2026 +21,612 =
**+$87,595 (+33% vs flat-$2 C)**, all years positive, worst day −9.4. **Cost:** WR ~50% (flat-C was 57%)
— it trades win-rate for net. **Honest limit:** no single-target config (fixed *or* ATR) raises BOTH
win% and profit above flat-C at once; the WR↔net trade-off is intrinsic. ATR just gives a better menu.
Pine C/C-ATR files: still TODO. Run: `python cli.py --config config/tsla_config_C_atr.yaml backtest`.

**Config C** (`config/tsla_config_C.yaml`) — fixed **$2 take-profit** (full exit, no partial/runner) +
**$5 BE-stop**, long+short+reversal, resting stop. Trades many small $2 wins; caps upside so it makes
less total net than A/B, but it's the smoothest and **positive in EVERY year 2022-2026 (100 shares):**
2022 **+$17,244** (bear market!) · 2023 +$18,962 · 2024 +$7,975 · 2025 +$8,119 · 2026 +$13,692 =
**+$65,992** over 4.5 yr. PF 1.4-2.4 every year; worst day bounded ~−$8. Pine C files: TODO (fixed-TP
full-exit exit engine — not yet ported; run in Python via `--config config/tsla_config_C.yaml`).

## Opening-Range start (input "Opening Range start bar")
- **09:30** (default): the classic first-bar OR. **Principled, robust.**
- **09:45**: skip the noisy opening 15 min, use the 09:45 bar. Better in the 2024/2025 backtest but on a
  NOISY surface and **worse in 2026 (incl. recent Jun/Jul)**. Provided to A/B test; not the default.

## Trade labels shown (both indicator and strategy)
Entry: **LONG / SHORT** (and **REV LONG / REV SHORT**). Partial: **PARTIAL @ TP** (25% at the target).
Exits: **Base SL**, **BE Stop**, **BE Trail**, **Trail** (A), **VWAP Cross** (B), **EOD**. Indicator
labels are ON by default; on the **strategy**, turn "Show Labels" ON to see them on a single month
(the built-in trade markers always show the same comments).

## Validation CSVs — `exports/`
Full June+July 2026 trade lists at **100 shares** (date, dir, entry/stop/exit prices+times, reason, $):
- `exports/junjul2026_ConfigA_OR0930.csv` · `_ConfigA_OR0945.csv`
- `exports/junjul2026_ConfigB_OR0930.csv` · `_ConfigB_OR0945.csv`
- `exports/junjul2026_ALL4.csv` (all four combined, with `config` + `OR_start` columns)

### June + July 2026 summary @ 100 shares
| Combo | OR | Jun $ | Jul $ | Jun+Jul $ |
|-------|----|------:|------:|----------:|
| Config A | 09:30 | +9,698 | +2,467 | **+12,166** |
| Config A | 09:45 | +6,015 | −347 | +5,668 |
| **Config B** | **09:30** | +9,392 | +3,768 | **+13,160** |
| Config B | 09:45 | +5,903 | +856 | +6,759 |

**@09:30 beats @09:45 in these recent months** (09:45's edge is 2024/2025-only). **Config B @09:30** is
the best of the four here. Note: June was an unusually strong month; July is closer to typical.

## More reference CSVs in `exports/` (2026, @100 shares, both configs @09:30)
- `failuredays_2026_Config{A,B}_OR0930.csv` — **failure DAYS** (day net < 0 *after* the reversal;
  reversal-recovered days excluded). 2026: A 48/122 days (−$13,403); B 51/122 (−$13,761).
- `reversal_saved_days_2026_Config{A,B}_OR0930.csv` — days the reversal **rescued** (primary red →
  day green). 2026: 9 days (identical A & B) turning −$1,645 into +$6,800.

## Decisions / rejected alternatives (why this is the setup)
- **Timeframe = 5-min** (best vs 1m/3m too noisy, 15m/30m too few trades). §29/§30.
- **OR start = 09:30** (principled; 09:45 better 2024/2025 but noisy surface + worse 2026 incl Jun/Jul).
  Selectable input either way. §30.
- **Confirmation OFF** (net-negative under the resting stop; +57% net). §24.
- **Resting stop (touch) + $0.10 slippage** (conservative stop-fill/sweep allowance). §22-23.
- **Long+short with reversal KEPT** (long-only leaves most of the money on the table).
- **REJECTED "faithful long-only (10, 3.0×OR, gate 10) + 20-SMA gate"**: its headline (+$3,996/2026,
  PF 66) is a FILL-MODEL ARTIFACT (fill-at-stop, ~$0 losses). Under honest fills it's +$1,969 (resting
  stop) to −$1,303 (close-fill) — far below Config A/B, and only ~23 trades concentrated in May. Not adopted.

## Python parity (reconcile the Pine against these)
`python cli.py backtest --start 2024-01-02 --end 2026-07-09 --from-db` → Config A (config.yaml).
`python cli.py --config config/tsla_best_B.yaml backtest --start 2024-01-02 --end 2026-07-09 --from-db` → Config B.
For 09:45: set `session.market_open: "09:45"` in the config.
