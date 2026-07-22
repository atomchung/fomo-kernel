# Supplying prices when the host cannot retrieve them

The engine retrieves its own prices. Some hosts block that retrieval — a sandbox with no outbound DNS returns errors such as `could not resolve host`, and every price-dependent number degrades at once: unrealized P&L, total P&L, position weights, concentration, benchmark comparison, and account-level return.

This is a data-availability failure, not a verdict. A missing price never means a security is delisted and never means a zero return.

When the host blocks the engine's own retrieval, recovering the prices is your first move, not an optional extra: look the closes up yourself from a recognized market-data source and hand them back through the envelope below **before** you surface the gap to the user or deliver a degraded card. The division of labour is the same as the position snapshot: the agent transcribes declared facts from a market-data source, and the engine keeps every calculation. Do not compute a return, a weight, a P&L figure, or an average cost.

## When this applies

`prepare` reports price availability in `review_plan.input.price_feed`:

- `provenance.mode`: `engine_fetch` (the engine retrieved the prices itself), `agent_feed` (an envelope was applied), or `unavailable` (retrieval failed and no envelope was supplied).
- `provenance.coverage`: how many instruments were requested, how many were priced, and which are missing.
- `request`: present only when coverage is incomplete. It lists the exact symbols, benchmarks, currencies, and window still needed.

Only act on this when `request` is present. Unpriced instruments in `request.tickers` remove P&L itself; unpriced symbols in `request.benchmarks` only remove the benchmark comparison.

Attempt recovery yourself first; the degraded card is the fallback for when recovery genuinely fails, not the default. A degraded review still completes, so never stall the review waiting for a price you cannot find — if the source does not publish it, omit that instrument and deliver the degraded card rather than blocking.

## Sources

Use a recognized market-data source that publishes closing prices for the listing venue: the exchange itself, the exchange's official market-data site, or an established financial-data provider. Record the one you actually read in `source`. A search-result snippet is not a source; open the page that publishes the price.

- The close must be the instrument's own trading currency: `2330.TW` in TWD, `NVDA` in USD. A currency contradicting the trade rows fails the run closed rather than pricing the position wrongly.
- Match the symbol exactly to what the trade rows use, including the `.TW` and `.TWO` suffixes described in [data-contract.md](data-contract.md).
- Never estimate, interpolate, or carry a price forward from memory. Omit an instrument you cannot find; the card discloses it as unpriced.
- Save the envelope outside this repository, for example `/tmp/fomo-kernel-prices.json`.

## Envelope

Validated against [../schemas/price-feed.schema.json](../schemas/price-feed.schema.json). A malformed envelope is rejected before any engine work, with the offending field named.

```json
{
  "as_of": "2026-07-21",
  "source": "Nasdaq official closing prices",
  "prices": [
    {
      "ticker": "NVDA",
      "close": 178.52,
      "date": "2026-07-21",
      "currency": "USD",
      "source": "https://www.nasdaq.com/market-activity/stocks/nvda"
    },
    {
      "ticker": "2330.TW",
      "close": 1090.0,
      "date": "2026-07-21",
      "currency": "TWD",
      "history": [["2026-07-18", 1075.0], ["2026-07-21", 1090.0]]
    }
  ],
  "fx": [
    {"currency": "TWD", "usd_per_unit": 0.0307, "date": "2026-07-21", "source": "..."}
  ]
}
```

Field rules:

- `as_of` is the non-future end-of-day date the feed represents. No row may be dated after it.
- `source` is the feed-level provenance shown on the card. A per-row `source` overrides it for that instrument.
- Each row needs `ticker`, `close` (positive, trading currency), `date`, and `currency`. One row per instrument.
- `history` is optional: `[date, close]` pairs. When present it must agree with `close` on the shared date.
- `splits` is optional: `[date, ratio]` pairs, where a ten-for-one split is `10`. Supply it only when the source shows it; omitted means no split adjustment is applied.
- `fx` is optional and only matters for a mixed-currency portfolio. Rates are USD per one unit of the currency. `USD` is fixed at 1.0 and must not be supplied. Omit a rate you cannot find.

## Coverage tiers

Both are accepted; supply what the source actually gives you.

| Supplied | Restores |
|---|---|
| `close` only | market value, unrealized P&L, total P&L, position weights, concentration, what-if |
| `close` plus `history` | additionally: benchmark window comparison, beta and alpha, the P&L curve, account-level time-weighted return |

For the second tier, include the benchmark symbols listed in `request.benchmarks` alongside the holdings, over the window in `request.window` (or from `request.history_from` for the full series).

## Running it

```bash
python3 engine/review.py prepare <CSV...> --language en --prices /tmp/fomo-kernel-prices.json
```

The supplied envelope changes the session fingerprint, so this is a new prepare rather than a resume of the degraded run. When an envelope is supplied the engine performs no retrieval of its own, including FX and split lookups.

## What the card says

An applied envelope triggers the `price_source` honesty key, and the card names the external source and its as-of date. An unrecovered failure triggers the same key with the unavailable status, and the performance block states that price retrieval — not the cash anchor — is what blocks the portfolio-level return. Neither form silently drops a number that could not be computed.
