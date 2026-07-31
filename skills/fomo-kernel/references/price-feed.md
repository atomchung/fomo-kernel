# Supplying prices when the host cannot retrieve them

The engine retrieves its own prices. Some hosts block that retrieval — a sandbox with no outbound DNS returns errors such as `could not resolve host`, and every price-dependent number degrades at once: unrealized P&L, total P&L, position weights, concentration, benchmark comparison, and account-level return.

This is a data-availability failure, not a verdict. A missing price never means a security is delisted and never means a zero return.

When the host blocks the engine's own retrieval, recovering the prices is your first move, not an optional extra: look the closes up yourself from a recognized market-data source and hand them back through the envelope below **before** you surface the gap to the user or deliver a degraded card. The division of labour is the same as the position snapshot: the agent transcribes declared facts from a market-data source, and the engine keeps every calculation. Do not compute a return, a weight, a P&L figure, or an average cost.

## When this applies

`prepare` reports price availability in `review_plan.input.price_feed`:

- `provenance.mode`: `engine_fetch` (the engine retrieved the prices itself), `agent_feed` (an envelope priced at least one requested instrument), or `unavailable` (no instrument was priced). An `fx`-only envelope reads `unavailable` here even though it was supplied and applied — `mode` is about instrument closes specifically; `recovery.outcome` below is what shows an envelope arrived at all.
- `provenance.coverage`: how many instruments were requested, how many were priced, and which are missing.
- `request`: present only when coverage is incomplete. It lists the exact symbols, benchmarks, currencies, and window still needed.

Only act on this when `request` is present. Unpriced instruments in `request.tickers` remove P&L itself; unpriced symbols in `request.benchmarks` only remove the benchmark comparison.

Attempt recovery yourself first; the degraded card is the fallback for when recovery genuinely fails, not the default. A degraded review still completes, so never stall the review waiting for a price you cannot find — if the source does not publish it, omit that instrument and deliver the degraded card rather than blocking.

## A dead end is declared, never assumed (#623)

`recovery` sits beside `request` and records which of the two happened, because the card said the same sentence either way and nothing could tell them apart:

- `attempted: true`, `outcome: "supplied"` — an envelope arrived, whatever coverage it reached.
- `attempted: true`, `outcome: "declared_unavailable"` — you looked and the sources publish nothing; `checked` carries the sources you named.
- `attempted: false` — nothing was ever handed back.

The last one is not a disclosure. The user can do nothing about a price *you* were asked to look up, and a card built on no prices at all states every weight from cost basis when it did not have to — so `preview` and `finalize` refuse it. Clear the refusal by doing the step, or, when the dead end is real, by stating it:

```bash
python3 engine/review.py prepare <CSV...> --prices-unavailable "the exchange's own market-data site publishes no close for these"
```

Name the sources you actually checked. It asks the user for nothing and costs one command, which is why the refusal is a gate on a step rather than a stall — and why the honest dead end still delivers its card.

## Two lanes, two opposite rules (#629)

The same declaration means opposite things on the two routes, and both are correct. This is stated here, beside the rules themselves, so nobody later reads the difference as an inconsistency and "fixes" one of them.

| | Review card (`prepare` → `preview` → `finalize`) | A trade the user is deciding on (`consider`) |
|---|---|---|
| Recovery never attempted | Refused — the card would state every weight from cost when it did not have to | Not refused; the answer carries a `price_feed` recovery kit naming what to look up |
| Recovery attempted, sources publish nothing (`--prices-unavailable`) | **The degraded card is delivered.** Never stall a review over a price nobody publishes | **The question is refused**, never answered on cost basis |

The asymmetry is the difference between the two questions, not an oversight:

- A retrospective card's cost weights describe **what the user actually paid**. That is a true, useful fact about a period that has already happened, and the card discloses that prices were unavailable. Withholding the whole review over it would cost the user a real answer.
- A forward concentration decision computed on cost describes **a book that no longer exists**. It is not a weaker answer to "what does this trade do to my concentration" — it is a different book's answer, and on this repository's own momentum fixture the largest position moves by more than thirteen points and the second and third positions by size swap places. A user holding a "no single position over half the book" rule is told they are already in breach when they are not.

So `consider` degrades to a refusal where a card degrades to a disclosure. `references/freeform-answers.md` carries the agent-facing half — the bound on the recovery itself, and why looking a price up is not the "multi-tool production" `SKILL.md` rule 8 otherwise forbids.

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

`prices` and `fx` are each optional; the envelope needs at least one of them, non-empty. The shape above answers a fully unpriced book. A book refused only for a missing rate on a currency its account aggregate spans (`MissingAggregateCurrencyRate`, #612/#649 — a currency that appears only in a cash-flow row counts) needs *only* the rate — the closes are a separate, independent gap, and demanding both to clear a refusal that is purely about the rate would push you toward inventing prices, which is forbidden (below). This is a complete repair for that refusal:

```json
{
  "as_of": "2026-07-21",
  "source": "Taiwan Bank spot rates",
  "fx": [
    {"currency": "TWD", "usd_per_unit": 0.0307, "date": "2026-07-21",
     "source": "https://rate.bot.com.tw/xrt"}
  ]
}
```

`prices` is omitted entirely here (an empty `"prices": []` is equally valid). The book's own closes stay unpriced and the review still discloses that degradation — this envelope clears only the currency-conversion refusal, not the missing-price one.

Field rules:

- `as_of` is the non-future end-of-day date the feed represents. No row may be dated after it.
- `source` is the feed-level provenance shown on the card. A per-row `source` overrides it for that instrument.
- `prices` is optional (#642): omit it, or send an empty list, when all you have is an FX rate. When present, each row needs `ticker`, `close` (positive, trading currency), `date`, and `currency` — one row per instrument.
- `history` is optional: `[date, close]` pairs. When present it must agree with `close` on the shared date.
- `splits` is optional: `[date, ratio]` pairs, where a ten-for-one split is `10`. Supply it whenever the source shows one inside the trade history, and check for one on any position the user has held for years. Omitting it is not cosmetic, and it now costs something on both sides of the multiplication — see the section below.
- `fx` carries USD-per-one-unit spot rates, and it is optional only while the book holds one currency. A **mixed-currency** book needs a rate for every currency it holds: without one there is no denominator its positions can be added into, so `prepare` and `consider` both refuse and name the currency instead of converting it at 1.0 (#612). Still omit a rate you cannot find — the refusal is recoverable and a guessed rate is not. `fx` alone, with `prices` omitted, is a complete envelope (#642): the two gaps fail independently, and the rate is usually the easier of the two to look up — one spot rate on one page, against one close per instrument.

## Prices are raw observations; the engine does the split arithmetic

**Every `close` and every `history` entry is the raw number the source printed on that row's own date. Never adjust a price for a split yourself.** Transcribe the observation, declare the split in `splits`, and stop there — the same division of labour as everywhere else in this file.

The engine then rebases each observation onto the split basis of the share count it is about to be multiplied by, using that row's own date against the splits you declared. A close observed the day before a ten-for-one is divided by ten; a close already dated on or after the split is left exactly as it is, and a row with no splits is untouched. The same happens to a `history` series before any consumer differences it, so a split inside the window is not read as a market move by beta, alpha, the P&L curve, or account-level return.

This is why an omitted split is not merely a missing nicety:

- **On the share side** the ledger records every trade at the count that actually executed, so a sale placed after a split cannot be subtracted from buys placed before it. Without the split, a routine trim of a long-held position reads as a full liquidation — which closes that position's thesis and states on the saved card that the user exited something they still hold (#550).
- **On the price side** the close you supplied stays in its own session's basis while the share count moves into the post-split one. A hundred shares become a thousand and are valued at the pre-split price: a tenfold market value, and with it a tenfold weight, an inverted concentration verdict, and a `consider` consequence that is wrong by the split ratio — all of it stated with valid provenance and no caveat anywhere on the card (#583).

**When the basis cannot be established, the run refuses rather than guessing.** `consider` compares the split divisor your observation received against the one this book's share counts received past that same date. If a split falls between your close's date and the basis the book is on — typically because the envelope declared no splits while a previous review recorded one — the two numbers are not comparable, and no weight or consequence is computed from them. The message names the ticker, the close's date, and the split. Either repair is yours: declare the split in the envelope, or supply a close dated on or after it. The engine will not apply a corporate action your price source never confirmed, and it will not quietly ignore one it already knows about.

`prepare` needs no such check: when you supply an envelope, the engine performs no split retrieval of its own, so the prices and the share counts are adjusted from the same declared events by construction.

## Coverage tiers

Both are accepted; supply what the source actually gives you. Both describe `prices` specifically — the rate-only shape above supplies neither, and is never reported as if it had (#652): omitting `prices` means there is no tier, not the smaller one.

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

## Plausibility cross-check

Structural validation catches a malformed envelope, not a wrong one: a mistranscribed digit, a stale price from the wrong date, or the wrong ticker entirely can still be positive, finite, and currency-matched, and would otherwise be priced and disclosed as if it were genuine. The engine additionally compares every supplied close against that ticker's own last recorded trade price. A deviation beyond a wide band, in either direction, adds a `price_plausibility` honesty caveat naming the affected ticker — it never rejects the run. A genuine multi-bagger or a genuine collapse between the last trade and `as_of` is real and still prices normally; this only asks that you double-check the source when the gap is unusually large.

If the card discloses this caveat, re-open the page you read the close from and confirm the ticker, the date, and the number before treating it as settled.

## What the card says

An applied envelope triggers the `price_source` honesty key, and the card names the external source and its as-of date. An unrecovered failure triggers the same key with the unavailable status, and the performance block states that price retrieval — not the cash anchor — is what blocks the portfolio-level return. Neither form silently drops a number that could not be computed. A supplied close that fails the plausibility cross-check above additionally triggers `price_plausibility`, naming the ticker without changing the price the card actually uses.
