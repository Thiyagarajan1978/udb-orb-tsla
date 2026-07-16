# C2 Options via TradersPost — setup & webhook payloads

Trade **TSLA options** off the ORB signal by routing the **v3 strategy** through a TradersPost
webhook. The strategy fires the signals on the **underlying** (TSLA 5m); TradersPost translates each
alert into an option order. This is wired onto the **adopted close‑triggered stop** — a stop fires
only when a 5m bar *closes* beyond the level, and that close sends the `exit` JSON.

## Use profile C2 only

Set the strategy to **profile C2** (Fixed $2 target, **FULL exit, no partial/runner**) before switching
`Order asset` to **Options**. Why C2 and not B1/C1:

- C2 is **one open → one close**. The entry opens a single option; the *first* exit (TP, close‑stop, or
  EOD) closes it. Clean, and it maps 1:1 to a TradersPost open/close.
- B1/C1 take a **25% partial**. In options mode that partial sends an `exit`, and TradersPost's `exit`
  closes the **whole** contract — so you'd be flat after the partial and miss the 75% runner. Do **not**
  run options on B1/C1.

> The Strategy Tester P&L is always the **share‑signal** P&L. Real option P&L (delta / theta / IV) is
> **not** backtestable here — the underlying's stop/TP fire the entries and exits, nothing more. 0DTE
> theta is severe; treat the backtest as *signal timing*, not option P&L.

## Strategy inputs (group "Options via TradersPost")

| Input | Default | Meaning |
|---|---|---|
| `Order asset` | `Shares` → set to **`Options`** | switches the JSON from shares to options |
| `Option contracts (quantity)` | `1` | contracts per order |
| `Option expiration` | `+0 days` | 0DTE. Weeklies: `+2 days` / `+3 days`, or a date `2026-07-18` |
| `Strikes away from ATM` | `0` | 0 = ATM; 1 = one strike OTM, etc. |

## Webhook payloads (exactly what the alert sends)

A **long** signal buys a CALL, a **short** signal buys a PUT, and **any** exit closes. With the defaults
(1 contract, 0DTE, ATM) the `{{strategy.order.alert_message}}` resolves to:

**Long entry (open CALL):**
```json
{"ticker":"TSLA", "action":"buy", "quantity":1, "expiration":"+0 days", "optionType":"call", "strikesAway":0}
```

**Short entry (open PUT):**
```json
{"ticker":"TSLA", "action":"sell", "quantity":1, "expiration":"+0 days", "optionType":"put", "strikesAway":0}
```

**Any exit — TP, close‑stop (Base SL / BE Stop / BE Trail), EOD, or a reversal flip (closes the old side):**
```json
{"ticker":"TSLA", "action":"exit"}
```

`{{ticker}}` resolves to the chart symbol at alert time. On a reversal day the primary's `exit` closes the
option, then the reversal entry opens the opposite option (buy CALL ↔ sell PUT).

## TradingView alert setup

1. On the chart, select **profile C2** and set **`Order asset = Options`** (adjust contracts / expiration
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

## Before going live

- **Paper first.** Confirm the open fires the right side (long→call, short→put) and every exit type
  (TP, close‑stop, EOD, reversal) closes the contract.
- Verify `expiration` resolves to a **real listed** expiry for TSLA on the day (0DTE only exists on days
  TSLA lists same‑day options; otherwise use `+1`/`+2 days`).
- Watch fills vs the backtest: the Strategy Tester models **no slippage** and prices the **underlying**,
  so live option fills will differ. Size small until the mapping is proven.
