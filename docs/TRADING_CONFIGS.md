# Trading Configs — quick reference (for live / paper trading)

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

## Config comparison (2.5-yr, per unit)
| | Config A | Config B |
|--|--------:|--------:|
| Net | +$469 | **+$574** |
| Profit factor | 1.65 | 1.79 |
| Worst day | −$8.85 | −$8.85 |

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
