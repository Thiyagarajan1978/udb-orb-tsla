# TSLA Options via TradersPost — setup & webhook payloads

Trade **TSLA options** off the ORB signal by routing the **v3 strategy** through a TradersPost
webhook. The strategy fires the signals on the **underlying** (TSLA 5m); TradersPost translates each
alert into an option order, wired onto the **close‑triggered stop**.

## Works on ALL profiles — A1/B1/C1 recommended (updated 2026‑07‑16)

Set `Order asset = Options` on **any** profile. The old "C2‑only" limitation is **fixed**: in Options
mode the strategy **auto‑suppresses the 25% partial's webhook**, so a single contract **holds through the
partial and closes only at the runner's final exit** (VWAP / trail / BE / close‑stop / EOD). That's exactly
what the real 0DTE option backtest priced — and **A1/B1/C1 crush C2 on options**:

| Profile | real 0DTE options, 1 ct, 2025→7/16 (18 mo) | why |
|---|---|---|
| **A1** | **+$70,460** | peak‑trail rides trend furthest |
| **B1** | +$65,372 | VWAP runner |
| **C1** | +$64,360 | ATR target + VWAP runner |
| C2 | +$25,508 | $2 scalp caps the winners (single open→close, no partial) |

- **Entry** → open CALL (long) / PUT (short). **Partial** → *no webhook* (contract holds). **Runner/stop/
  VWAP/EOD full‑flatten** → close. **Reversal** → close the old option, open the opposite.
- The suppressed partial fires an **empty webhook that TradersPost ignores** (harmless log entry).
- Keep **`Stop trigger = Close`** (default) so stops flatten cleanly and fire the option close.

> The Strategy Tester P&L is always the **share‑signal** P&L. Real option P&L uses actual OPRA quotes
> (see the "Real backtest evidence" section) — the Tester can't price options. Trade the **close stop**.

## Strategy inputs (group "Options via TradersPost")

| Input | Default | Meaning |
|---|---|---|
| `Order asset` | `Shares` → set to **`Options`** | switches the JSON from shares to options |
| `Option contracts (quantity)` | **`2`** | contracts per order — **default 2** (P&L *and* premium at risk both scale ×2 vs the per‑contract backtest) |
| `Option expiration` | `+0 days` | **dropdown**: `+0 days` (0DTE/nearest), `+1/+2/+3/+7 days`. `+2`/`+3` ≈ the nearest weekly (Friday) |
| `Strikes away from ATM` | `0` | 0 = ATM; 1 = one strike OTM, etc. |

> **0DTE vs weekly (tested 2026-07-17):** the trades hold **2‑3 hours on average**, so an extra day of DTE
> bleeds less theta. **Weekly (+2/+3 days) beat 0DTE by ~3‑5% net** on every profile (A1/B1/C1 +$2‑3k over
> 2025‑26, same win rate — the gain is keeping more of each win). Catch: weekly premium is ~22% higher
> ($6.37 vs $5.24/ct), so for *equal dollar risk* you'd size slightly fewer contracts. Both are selectable in
> the `Option expiration` dropdown — default `+0 days`; try both in paper trading.

> **Sizing note:** 2 contracts ≈ **~$930 premium at risk per trade** on TSLA (~9% of a $10k account).
> Losses are capped small (~−$97/contract → ~−$194/trade). Raise to 3–4 for more, drop to 1 for less —
> the scaling is linear. All the backtest figures below are **per 1 contract**; multiply by 2 for the default.

## Webhook payloads (exactly what the alert sends)

A **long** signal buys a CALL, a **short** signal buys a PUT, and **any** exit closes. With the defaults
(2 contracts, 0DTE, ATM) the `{{strategy.order.alert_message}}` resolves to:

**Long entry (open CALL):**
```json
{"ticker":"TSLA", "action":"buy", "quantity":2, "expiration":"+0 days", "optionType":"call", "strikesAway":0}
```

**Short entry (open PUT):**
```json
{"ticker":"TSLA", "action":"sell", "quantity":2, "expiration":"+0 days", "optionType":"put", "strikesAway":0}
```

**Any exit — TP, close‑stop (Base SL / BE Stop / BE Trail), EOD, or a reversal flip (closes the old side):**
```json
{"ticker":"TSLA", "action":"exit"}
```

`{{ticker}}` resolves to the chart symbol at alert time. On a reversal day the primary's `exit` closes the
option, then the reversal entry opens the opposite option (buy CALL ↔ sell PUT).

## TradingView alert setup

1. On the chart, select your profile (**A1/B1/C1** recommended) and set **`Order asset = Options`** (adjust contracts / expiration
   / strikes as desired). Leave **`Stop trigger = Close`** (the adopted default).
2. Create an alert on the **strategy** → **Condition: the strategy**, **Order fills only**.
3. Set the alert **Message** to exactly:
   ```
   {{strategy.order.alert_message}}
   ```
   Do **not** use `{{strategy.order.action}}` — it only ever prints `buy`/`sell`, never `exit`, so your
   closes would never fire.
4. **Webhook URL**: your TradersPost strategy's webhook endpoint.
5. In TradersPost, connect the strategy to your **options‑enabled broker** and confirm the symbol/quantity
   mapping.

## Real backtest evidence (Databento OPRA, 2026-07-16)

We priced the signals against **actual TSLA option quotes** (Databento OPRA `cbbo-1m`, ATM nearest-expiry ≈
0DTE, filled **buy-at-ask / sell-at-bid** = conservative on the spread). 1 contract per signal:

| Profile | 2025-26 (bull) | Sep22-Dec23 (crash+chop) | shares @25 | months positive |
|---|---|---|---|---|
| A1 (runner) | +$70,460 | +$37,130 | ~$2-6k | 16/16 & 17/17 |
| B1 (runner) | +$65,372 | +$33,841 | ~$1.6-6k | 16/16 & 17/17 |
| C1 (runner) | +$64,360 | +$34,537 | ~$2-6k | 16/16 & 17/17 |
| C2 (scalp $2)            | +$25,508 | +$19,291 | ~$2.2k   | 15/16 & 17/17 |

> Corrected 2026-07-17: an earlier version mapped reversal-longs to PUTs instead of CALLs in the pricing
> script (analysis only — the live Pine strategy always mapped call/put from the actual order side, so it
> was never wrong). Net effect ~1-6% per window; 2025-26 dipped slightly, 2022-23 rose. Conclusion unchanged.

**Confirmed robust in BOTH bull and bear/chop regimes** — options beat shares 10-16×, nearly every month
green, max drawdown only ~-$868 (2025-26). Why it works: the strategy's tight BE/base-SL stop **caps each
option loss small** (early stop-out, avg loss ~-$97) while winners fat-tail on trend/gamma (avg win ~+$364),
and TSLA's high intraday vol pushes 0DTE ATM ITM often enough. **A1/B1/C1 (held to the runner exit) beat
C2** — and the strategy now does this automatically in Options mode (it suppresses the partial's webhook, so
the contract rides to the runner exit). **Just pick A1, B1, or C1 and set `Order asset = Options`** — no need
to touch `use_partial_exit`. C2 still works, just smaller.

**Load-bearing caveats:** (1) **regime/VOL-dependent** — TSLA's 3-4%/day vol is essential; a low-vol
underlying may not clear theta+spread; (2) real fills haircut the `cbbo` figures ~10-30% (slippage beyond
the quote, worst near EOD/expiry); (3) needs **automation** (~1.5 trades/day); (4) ~$465 premium at risk per
trade (~4.6% of a $10k account) — losses are capped and small, but a chop cluster can exceed the benign
drawdown seen here. Treat the backtest as *strong evidence*, size small, and paper-trade first.

## Before going live

- **Paper first.** Confirm the open fires the right side (long→call, short→put) and every exit type
  (TP, close‑stop, EOD, reversal) closes the contract.
- Verify `expiration` resolves to a **real listed** expiry for TSLA on the day (0DTE only exists on days
  TSLA lists same‑day options; otherwise use `+1`/`+2 days`).
- Watch fills vs the backtest: the Strategy Tester models **no slippage** and prices the **underlying**,
  so live option fills will differ. Size small until the mapping is proven.
