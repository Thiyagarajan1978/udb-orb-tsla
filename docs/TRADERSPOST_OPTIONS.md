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

| Profile | **weekly** 1 ct, 2025‑01→2026‑07‑17 — realistic bar‑close fills | (old bar‑start figure) | why |
|---|---|---|---|
| **A1 — ADOPTED** | **+$12,895** (~$33/trade) | ~~+$72,896~~ | peak‑trail rides trend furthest |
| **D1** | **+$11,284** | — | ATR‑trail runner (added 2026‑07‑25) |
| **B1** | **+$11,241** (~$28/trade) | ~~+$67,748~~ | VWAP runner — but **worst on options in 2026 Jan‑May** |
| **C1** | +$10,059 | ~~+$66,343~~ | ATR target + VWAP runner |
| C2 | +$4,246 under the v3.5 OR‑width TP (~~−$967~~ pre‑v3.5) | ~~+$25,722~~ | $2 scalp capped the winners |

> **These are WEEKLY figures, not 0DTE** (corrected 2026‑08‑09 — TSLA listed no Mon/Wed contracts for
> ~85% of this window; see the expiry box below). **Defaults since Pine v3.9.2: profile `A1`,
> `Option expiration = Friday (weekly)`.** And read the concentration warning before sizing — 91% of
> 2026's options P&L came from a six‑week stretch in June–July.

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
| `Option expiration` | **`Friday (weekly)`** (default since v3.9.2) | **dropdown**: **`Friday (weekly)`** (dynamic days‑to‑Friday — **adopted**), `+0 days` (nearest listed), `+1/+2/+3/+7 days` (fixed offsets, manual experiments only) |
| `Strikes away from ATM` | `0` | 0 = ATM; 1 = one strike OTM, etc. |

> ### 0DTE vs weekly — SETTLED 2026‑08‑09: use **weekly**
>
> **Both earlier verdicts in this doc were wrong, for the same hidden reason.** Checking the OPRA definition
> files directly: **TSLA listed FRIDAY‑ONLY expiries until 2026‑02‑02** (first Mon/Wed contract appears in the
> 2026‑01‑26 snapshot). Cached definitions — 2025: **49 Fri + 2 Thu** (holiday weeks) and **zero Mon/Wed**;
> 2026: 24 Mon / 23 Wed / 36 Fri.
>
> So across 2025‑01→2026‑07 the "0DTE" and "weekly" legs were **picking the same contract for ~85% of the
> sample**. Tell‑tale: D1 2025 scored 0DTE **+$3,560** vs weekly **+$3,570** — a 0.3% gap that is nothing but
> two holiday weeks. Both the original "weekly beats 0DTE by 3‑5%" *and* the 2026‑07‑20 "they're within noise,
> 0DTE marginally ahead" were comparing a contract to itself. **Every "0DTE" figure in this doc is really a
> weekly/1‑4DTE figure.**
>
> On 2026 data, where the two legs are finally distinct, **weekly wins on 5 of 5 profiles in 3 independent
> windows**: 2026 Jan‑May, Jun 1‑Jul 17, and the forward ledger's 70 genuinely‑same‑day trades (~30% less
> loss there). Cost is ~22% more premium per contract. `Friday (weekly)` also **always** resolves to a listed
> contract on any weekday, whereas `+0 days` silently rolls to 1DTE on Tue/Thu.

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

A **long** signal buys a CALL, a **short** signal buys a PUT, and **any** exit closes. With the v3.9.2
defaults (2 contracts, **weekly**, ATM) the `{{strategy.order.alert_message}}` resolves to — note the
`expiration` offset is computed per bar (Mon `+4` … Fri `+0`), so it always lands on a listed Friday:

**Long entry (open CALL):**
```json
{"ticker":"TSLA", "action":"buy", "quantity":2, "expiration":"+4 days", "optionType":"call", "strikesAway":0}
```

**Short entry (open PUT):**
```json
{"ticker":"TSLA", "action":"buy", "quantity":2, "expiration":"+4 days", "optionType":"put", "strikesAway":0}
```

> ⚠️ **CORRECTED 2026-08-09** — this example previously showed `"action":"sell"`, which is **wrong and
> dangerous**. Per the TradersPost options docs, `action:sell` + `optionType:put` is **SELL-TO-OPEN a short
> (naked) put**, not "buy a put". The Pine has always emitted **`"action":"buy"` for both sides** — direction
> is carried by `optionType` (`call`/`put`), never by the action — so the live script was never wrong; only
> this doc was. Leave **"Invert puts" UNCHECKED** in TradersPost or a short signal will still open a short put.

**Any exit — TP, close‑stop (Base SL / BE Stop / BE Trail), EOD, or a reversal flip (closes the old side):**
```json
{"ticker":"TSLA", "action":"exit"}
```

`{{ticker}}` resolves to the chart symbol at alert time. On a reversal day the primary's `exit` closes the
option, then the reversal entry opens the opposite option — **buy CALL ↔ buy PUT** (both `action:buy`).

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
| C2 (scalp $2 — **pre-v3.5**) | ~~−$967~~ → **+$4,246** under the v3.5 OR-width TP | ~~+$25,722~~ |

> **Read this table as WEEKLY, not 0DTE** (see the expiry box above — TSLA had no Mon/Wed contracts for
> ~85% of this window). The 2026-07-25 update adds D1 **+$11,284** (2nd place) and re-scores C2 **+$4,246**
> under the v3.5 OR-width target — so the "C2 options are negative, don't trade them" verdict below is
> **superseded**; C2 is positive but still last.

### ⚠️ The edge is regime-concentrated, not a steady drip (measured 2026-08-09)

Re-pricing 2026 month by month on the weekly leg, 1 contract, bar-close fills:

| Window | trades | A1 | D1 | C1 | C2 | B1 |
|---|---|---|---|---|---|---|
| **Jan 2 – May 31** (5 months) | 109 | **+$785** | +$462 | −$316 | −$387 | **−$1,116** |
| **Jun 1 – Jul 17** (6.5 weeks) | 34 | **+$7,743** | +$6,298 | +$5,073 | +$2,656 | +$6,613 |

June alone gave A1 +$6,635. **91% of 2026's options P&L came from 34 of 143 trades**, and the window right
after it (the forward ledger, 2026-07-10→08-07) is negative on every profile. Over the flat five months the
best profile earned **~$7/trade on ~$656 of premium** — indistinguishable from zero, with 3 of 5 profiles
losing. Size for a Jan-May stretch, not for June.

**Note the rankings differ by asset.** A1 leads on options in both windows and is the only profile positive
in Jan-May — hence the v3.9.2 default. **B1 is the *worst* on options** despite being one of the two
live-traded shares profiles. Do not assume the shares pick transfers.

> Corrected 2026-07-17: an earlier version mapped reversal-longs to PUTs instead of CALLs in the pricing
> script (analysis only — the live Pine strategy always mapped call/put from the actual order side, so it
> was never wrong). Net effect ~1-6% per window; 2025-26 dipped slightly, 2022-23 rose. Conclusion unchanged.
> NOTE: the Sep22-Dec23 window (+$33-37k/profile) and the per-month/day-of-week/premium stats quoted around
> this doc were computed under bar-start fills and are NOT yet re-validated — treat them as upper bounds.

**The corrected picture:** the options edge is real but modest — ~$28-33/trade for A1/B1, roughly on par
with (not 10-16× above) the share P&L at 25 sh. The mechanism still holds directionally: the tight BE stop
caps option losses while winners ride trend/gamma — but most of the previously-claimed fat tail was the
entry lookahead. **A1/D1/B1/C1 (held to the runner exit) beat C2** (C2 was net negative pre-v3.5; the
OR-width TP flipped it positive but still last). **A1 is the adopted options profile.** The strategy
handles the runner-hold automatically in Options mode (it suppresses the
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
- **Verify the `"+N days"` string parses.** The TradersPost docs only ever show same‑day as `"0 days"` —
  without the plus — while the Pine emits `"+0 days"` (which `Friday (weekly)` produces on a Friday). If
  their parser is strict, a Friday entry would be rejected. Fire **one paper alert on a Friday** and read
  the TradersPost webhook log before trusting it live. This is the last unverified item in the chain.
- Confirm **"Invert puts" is UNCHECKED** — with it on, a short signal opens a short put instead of a long one.
- Expect one **rejected/malformed webhook per winning trade**: in Options mode the 25% partial deliberately
  sends plain English (`"UDB-ORB INFO: … NO OPTIONS ACTION …"`) so the contract holds to the runner exit.
  That log line is by design, not a failure.
- **One options position per underlying per account** (TradersPost limitation, single‑leg/directional only).
  Two profiles on TSLA — or the A1+D1 pair — cannot both route options into the same account.
- Watch fills vs the backtest: the Strategy Tester models **no slippage** and prices the **underlying**,
  so live option fills will differ. Size small until the mapping is proven.
