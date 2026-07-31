# data/reference/

Curated datasets that are **committed on purpose** (unlike `data/cache/`, `data/results/` and
`exports/`, which are gitignored and regenerable). They are inputs to research, not outputs of
the trading engine.

---

## `tsla_first_minute_*.csv` — the 09:30–09:31 ET opening minute, 3 years

One row per trading session. The first RTH minute is the leading edge of the opening range,
so this is the raw material for asking whether opening-minute order flow predicts the day's
ORB trade.

Rebuild / extend (resumable, skips cached sessions):

```
python scripts/first_minute_history.py --start 2023-07-31 --end 2026-07-29
```

### Sources and their scope

| columns | source | scope |
|---|---|---|
| `*_fmp` | FMP `/stable/historical-chart/1min`, the 09:30 bar | consolidated (all venues) |
| `*_db` | Databento `XNAS.ITCH` `tbbo` (trade + BBO at execution) | **Nasdaq only, ~17–19% of TSLA's tape** |

Because the two have different scope, **compare prices in level and volume only in ratio or
split**. `volume_db` is *not* meant to equal `volume_fmp`.

### Buy/sell volume is derived, not published

No vendor publishes an aggressor split — FMP has none at any interval, and Databento has no
buy/sell field. It is computed here with the standard **Lee-Ready quote rule**: price above the
mid = buy-initiated, below = sell-initiated, at the mid = tick rule vs the last differing trade
price. The raw `side` field is deliberately *not* used for grouping (≈32% of Nasdaq trades carry
`side='N'`, non-displayed, and would silently vanish); the quote rule was validated at 99.85%
agreement with `side` where it is populated, and trade-derived volume ties exactly to the
`ohlcv-1m` schema total. See `docs/`-adjacent notes and `scripts/buysell_1m_fmp_vs_databento.py`.

### The opening cross — read `buy_pct_ex_cross_db`, not `buy_pct_db`

The 09:30 minute contains the **Nasdaq opening auction**, which prints as one huge non-displayed
trade in the first seconds. An auction has no aggressor (both sides are matched), but the quote
rule labels it anyway — and it is typically **58–78% of the minute's volume**, so it swamps the
raw split. The script isolates it (largest print in the first 2 seconds that is ≥5% of the bar)
into `cross_size_db` / `cross_price_db`, and the `*_ex_cross_db` columns give the split across
**continuous trading only**. That is the meaningful order-flow number.

How much it matters: on 2023-08-01 the raw split reads **88.0% buy**; excluding the cross it is
**41.7% buy** — the opposite sign. The cross is detected on all 752 sessions and on every one of
them it is also the largest print of the entire minute, so the identification is unambiguous.
It usually lands within 1s of the open, but 2025-05-02..09 ran 2.2–2.4s late, hence the 10s
window rather than a tight one.

`cross_price_db` is Nasdaq's official opening price, which is usually the better "open" than
`open_db` (the first print, which can precede the cross).

### Columns

| column | meaning |
|---|---|
| `date`, `weekday` | session |
| `open/high/low/close_fmp`, `volume_fmp` | FMP's 09:30 one-minute bar |
| `open/high/low/close_db` | from Nasdaq trades in the window (`open_db` = first print) |
| `range_db` | `high_db - low_db` |
| `vwap_db` | volume-weighted trade price of the minute |
| `d_close` | `close_fmp - close_db` (venue vs consolidated last trade) |
| `volume_db` | Nasdaq shares traded in the minute, cross included |
| `buy/sell/unclassified_volume_db` | quote-rule split of `volume_db` (cross included) |
| `buy_pct_db` | buy ÷ (buy+sell), **cross-polluted — prefer the ex-cross column** |
| `cross_price_db`, `cross_size_db`, `cross_pct_of_bar_db` | the isolated opening auction |
| `volume_ex_cross_db`, `buy/sell_volume_ex_cross_db` | continuous trading only |
| `buy_pct_ex_cross_db`, `net_volume_ex_cross_db` | **the headline order-flow figures** |
| `trades_db`, `largest_print_db` | trade count, biggest single print |
| `error_db`, `error_fmp` | present only if a session failed to fetch |

### Known data caveats

* FMP's intraday feed is a **partial tape** — it omits the auction, so `volume_fmp` on this bar
  can sit *below* Nasdaq-only `volume_db`. Do not read `volume_fmp` as the true opening-minute
  volume. (FMP's *daily* endpoint is exact; its 5-minute endpoint drops whole bars.)
* Prices agree closely (`d_close` typically within a few cents), but occasional FMP 09:30 bars
  are visibly wrong in level — cross-check against `cross_price_db` before trusting one.
* **FMP is missing the 09:30 bar entirely on 4 of 752 sessions** (2025-11-28, 2026-02-12,
  2026-02-25, 2026-04-16 — its data starts at 09:31 or 09:32 those days). Those rows have blank
  `*_fmp` columns. Databento covers all 752.
* Databento equities are **T+1**, so the newest available session is always yesterday's.

### Integrity checks that pass on the committed file

* 752 sessions, 2023-07-31 → 2026-07-29, zero fetch errors.
* `buy_volume_db + sell_volume_db + unclassified_volume_db == volume_db` exactly, every row.
* `volume_ex_cross_db + cross_size_db == volume_db` exactly, every row.
* Opening cross identified on 752/752, and equal to the minute's largest print on 752/752.
