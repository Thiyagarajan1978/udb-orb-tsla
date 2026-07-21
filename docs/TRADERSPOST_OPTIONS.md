# TSLA Options via TradersPost — setup & webhook payloads

Trade **TSLA options** off the ORB signal by routing the **v3 strategy** through a TradersPost
webhook. The strategy fires the signals on the **underlying** (TSLA 5m); TradersPost translates each
alert into an option order, wired onto the **close‑triggered stop**.

## Works on ALL profiles — A1/B1/C1 recommended (updated 2026‑07‑16)

Set `Order asset = Options` on **any** profile. The old "C2‑only" limitation is **fixed**: in Options
mode the strategy **auto‑suppresses the 25% partial's webhook**, so a single contract **holds through the
partial and closes only at the runner's final exit** (VWAP / trail / BE / close‑stop / EOD). That's exactly
what the real 0DTE option backtest priced — and **A1/B1/C1 beat C2 decisively on options**:

> **⚠ LOOKAHEAD CORRECTION (2026‑07‑20).** The originally published figures priced entries at the signal
> bar's **START** quote — 5 minutes before the alert can fire at the bar **close**, i.e. before the breakout
> bar's premium move. Repricing every trade at the **bar‑close quote (realistic alert fill)** removed ~83% of
> the apparent edge. The corrected numbers below are the ones to size against. (Same finding killed the
> apparent SPY options edge entirely — SPY ORB is negative on shares AND options; TSLA's edge is real but small.)

| Profile | 0DTE 1 ct, 2025‑01→2026‑07‑17 — **realistic bar‑close fills** | (old bar‑start figure) | why |
|---|---|---|---|
| **A1** | **+$12,895** (~$33/trade) | ~~+$72,896~~ | peak‑trail rides trend furthest |
| **B1** | **+$11,241** (~$28/trade) | ~~+$67,748~~ | VWAP runner |
| **C1** | +$10,059 | ~~+$66,343~~ | ATR target + VWAP runner |
| C2 | **−$967 (negative — do NOT trade C2 options)** | ~~+$25,722~~ | $2 scalp caps the winners |

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
| `Option expiration` | `+0 days` | **dropdown**: `+0 days` (nearest listed), **`Friday (weekly)`** (dynamic days‑to‑Friday — the correct weekly setting), `+1/+2/+3/+7 days` (fixed offsets, manual experiments only) |
| `Strikes away from ATM` | `0` | 0 = ATM; 1 = one strike OTM, etc. |

> **0DTE vs weekly — REVISED 2026‑07‑20:** the earlier claim that "weekly beat 0DTE by ~3‑5%" was itself a
> **lookahead artifact** (it only held under bar‑start fills). Under realistic bar‑close fills the two are
> within noise of each other — 0DTE marginally ahead (A1 +$12,895 vs +$12,477; B1 +$11,241 vs +$11,013 over
> 2025‑01→2026‑07). Weekly premium is still ~22% higher, so 0DTE gives slightly better return on premium at
> risk. Pick by premium‑at‑risk preference; the expiry choice is no longer a performance lever.

> **Expiry calendars differ per underlying (verified 2026-07-20 against broker chains):** **TSLA lists only
> ~Mon/Wed/Fri weekly expiries — there are NO Tue/Thu dailies** (SPX, by contrast, lists **every trading day**,
> which is why the SPX 3‑bot strategy can hardcode `+0 days` as true 0DTE). TradersPost resolves `+N days` to the
> *nearest listed expiry on/after* today+N, so on TSLA: `+0 days` on Tue/Thu actually fills the next Wed/Fri
> (1DTE — fine, just not literal 0DTE), and a **fixed `+2`/`+3 days` only lands on Friday early in the week — from
> Wed/Thu/Fri it rolls into NEXT week's Monday**. Use **`Friday (weekly)`**, which computes the day offset
> dynamically (Mon +4 … Fri +0) and matches what `forward_test.py` prices as the weekly leg.

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

## Real backtest evidence (Databento OPRA; barclose-corrected 2026-07-20)

We priced the signals against **actual TSLA option quotes** (Databento OPRA `cbbo-1m`, ATM nearest-expiry ≈
0DTE, filled **buy-at-ask / sell-at-bid** = conservative on the spread). 1 contract per signal.
**All figures below are the realistic bar-close fills (2026-07-20 lookahead correction)** unless struck out:

| Profile | 2025-01→2026-07-17 realistic | (old bar-start figure) |
|---|---|---|
| A1 (runner) | **+$12,895** | ~~+$72,896~~ |
| B1 (runner) | **+$11,241** | ~~+$67,748~~ |
| C1 (runner) | +$10,059 | ~~+$66,343~~ |
| C2 (scalp $2) | **−$967** | ~~+$25,722~~ |

> Corrected 2026-07-17: an earlier version mapped reversal-longs to PUTs instead of CALLs in the pricing
> script (analysis only — the live Pine strategy always mapped call/put from the actual order side, so it
> was never wrong). Net effect ~1-6% per window; 2025-26 dipped slightly, 2022-23 rose. Conclusion unchanged.
> NOTE: the Sep22-Dec23 window (+$33-37k/profile) and the per-month/day-of-week/premium stats quoted around
> this doc were computed under bar-start fills and are NOT yet re-validated — treat them as upper bounds.

**The corrected picture:** the options edge is real but modest — ~$28-33/trade for A1/B1, roughly on par
with (not 10-16× above) the share P&L at 25 sh. The mechanism still holds directionally: the tight BE stop
caps option losses while winners ride trend/gamma — but most of the previously-claimed fat tail was the
entry lookahead. **A1/B1/C1 (held to the runner exit) beat C2, and C2 options are net NEGATIVE — don't
trade C2 options.** The strategy handles the runner-hold automatically in Options mode (it suppresses the
partial's webhook). Cross-symbol check (2026-07-20): the same pipeline on SPY 2026 is negative on shares
AND options — the edge is TSLA-specific; do not port to index underlyings.

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
