# Weighing a trade the user has not placed yet

A user mid-decision asks something like *"I'm thinking of buying NVDA — what does that do to my book?"* They are not in a review and will not hand over a CSV. `consider` answers from what the product already stores: the local ledger, or transaction files if you have them in hand.

This is Layer 2 (docs/decision-fomo-kernel-shape.md §3-4): deterministic arithmetic over a hypothetical trade. The engine computes the consequence; it never recommends. Owner ruling 2026-07-27: build the case for and against from `consider`'s output, and take no position yourself.

`consider`'s answer is plain conversation, not a card — which means it is a freeform surface and `freeform-answers.md`'s default applies: a quick, direct, textual answer, with no chart or multi-tool production unless the user asks for more.

## When this applies

Any pre-trade question about a single hypothetical trade against the user's current book — "should I buy this," "am I chasing," "should I add here," "does this break my own rule." Not for a review (use `prepare`), and not for a question about several trades at once or a portfolio redesign — `consider` prices exactly one hypothetical trade.

## Running it

```bash
python3 engine/review.py consider --premise '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 20}'
```

No CSV path is required. Without one, the book is reconstructed from the local ledger; with one or more, they are read the same way a review reads them. Both paths are described under [Which book answered](#which-book-answered) below.

`--premise` takes either form, whichever is more natural for you to produce: a path to a JSON file, or the JSON object inline as shown above.

## The premise

Validated against [../schemas/trade-premise.schema.json](../schemas/trade-premise.schema.json).

```json
{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 20}
```

- `ticker`, `side` (`buy` or `sell`), and `price` are required. `price` is the hypothetical execution price, in the instrument's own trading currency — required even for a `qty` trade, because it is also what a `notional` trade divides through.
- Exactly one of `qty` or `notional` (cash terms in the instrument's currency, converted to `qty` by dividing through `price`). Send whichever one you actually have in mind; do not send both.
- `date` is optional and defaults to the day after the book's last row. A date earlier than that is refused — this computes a forward consequence, not a rewrite of history.
- `currency` is optional and defaults to the ticker's own currency if already held, or USD for a new position.
- A `sell` is checked against what is currently held. Selling an unheld ticker, or more shares than are held, is refused rather than read as a short or silently clamped.

A rejected premise returns a `ReviewError` naming the field. Fix it and rerun; nothing is recorded until the call succeeds.

## Reading the consequence

The response carries `before` and `after` — the book's own state without and with the hypothetical trade — plus `delta` (only the readings that actually moved) and `disclosures` (see below). Weight, concentration (`ai_pct`, `max_sector_pct`, `top3`), whether the position-size cap or the concentration line is triggered, cash balance and weight, and how many holdings the book would carry are all in both snapshots, so a before/after comparison never has to be recomputed by hand.

`disclosures` is a list of machine-readable keys, not prose — read them as gaps in the numbers, not failures:

| Key | What it means |
|---|---|
| `cost_basis` | No current price was supplied, so weights are computed on cost rather than market value. |
| `cash_unreliable` | The cash balance has no anchor and is a running sum from cash flows alone. |
| `unmapped_driver` | The premise's ticker has no sector/AI classification, so it cannot be accounted for in concentration. |
| `mixed_currency_no_fx` | The book holds more than one currency and at least one lacks an FX rate, so aggregate figures are incomplete. |
| `partial_book` | At least one held position could not be valued at all and is outside the denominator every percentage here is measured against. `excluded_holdings` names which, and why. |

`partial_book` obliges the answer, not just the payload. When it is present, state the denominator in the same breath as the number — *"this would become 23% of the priced part of your book; ACME has no cost on record and is excluded"* — and name the excluded holdings wherever a derived percentage appears. A partial denominator presented as a whole one is worse than the refusal this replaced, because the user cannot tell it happened. The engine will not answer at all when *nothing* can be valued: that is an empty denominator, not a bounded one.

The same applies to a rule collision. Each row carries `partial_book: true` when the book it was judged against was bounded, and that qualifier belongs in what you say: the user wrote their cap against their whole book, an excluded position reads as weight zero, and a cap that a hidden position is breaching comes back `clear`. Report the state and say which book it was measured on.

Pass `--prices` (an envelope in the shape [price-feed.md](price-feed.md) describes) to price the book on current market value instead of cost, and `--cash` (a `{as_of, amount, currency}` object, or a list of them for a multi-currency book) to anchor the cash balance. `--driver-map` and `--instrument-map` carry the same local classification files a review accepts.

## Reading a rule collision

For every rule currently in the user's rotation (a muted rule is excluded, matching the rotation it opted out of), the response states whether this one hypothetical trade would collide with it right now — never whether the book is generally fine.

| `state` | Meaning | Say it as |
|---|---|---|
| `would_breach` | This trade is what freshly crosses the line. | "This would push you over your own [X] rule." |
| `already_over` | The book is already over the line, with or without this trade being the cause. | Name that the line is already crossed — and read `worsens` before saying anything about direction. |
| `clear` | Not over the line after this trade. | No caveat needed. |
| `unjudged` | The rule's metric (exit or holding-period discipline) describes realized behavior over history; one hypothetical trade cannot settle it. | Say plainly that this rule cannot be evaluated from a single hypothetical trade — never silently drop it or imply it is fine. |
| `unmapped` | The rule's metric has no mechanical mapping at all. | Same as `unjudged`: named as unevaluated, never as a pass. |

**`unjudged` and `unmapped` are not passes.** A rule that is not evaluated must be named as not evaluated. Presenting either as "no issue" tells the user something the engine never checked.

**`already_over` with `worsens: false` means the trade improves an already-broken line.** A book already oversized in a position reads `already_over` for a trade that sells half of it and materially improves the weight, exactly as it does for one that makes the position bigger — the two are told apart only by `worsens`. Reporting an `already_over` reduction as "this breaks your rule" tells the user the opposite of what happened. `worsens` is only meaningful when `state` is `already_over`; it is `null` everywhere else.

## Which book answered

`basis` names what the consequence was computed against and how current it was:

- `source: "transactions"` — you supplied one or more CSV paths, read the same way a review reads them.
- `source: "ledger"` — no CSV was supplied, so the book was reconstructed from the local ledger: the latest complete snapshot anchor, plus every trade after it.
- `as_of` — the date of the record's own latest row.
- `stale_days` — how many days between `as_of` and today. This is disclosed, never gated on: a large value does not block the answer, because no threshold for interrupting the user over it has been set. State it rather than act on it.

The CSV/FIFO path a review uses and the ledger reconstruction `consider` falls back to can legitimately disagree about a position's weight — they are answering different questions from different completeness requirements. Say which basis was used rather than presenting either as the only number.

## The case for and against

The engine states the consequence and the rule collisions; it never recommends. Build the case for and against directly from that output, and take no position on which side wins — that call belongs to the user.

Every claim you add carries its own label: state your record says (drawn straight from `before`/`after`/`delta`/`rule_collisions`), a public fact (something you looked up, sourced), or your own judgment. Do not blend them into one unlabeled sentence.

Name what nobody checked, every time. `consider` measures weight, concentration, driver overlap, cash, and rule collisions — nothing else. Liquidity, valuation, tax consequences, and whether the position still fits this person are all real risks the engine does not measure. Silence about a risk it did not check reads as a clean bill of health it never gave. Record staleness (above) belongs on this list too whenever `stale_days` is more than trivial.

You may optionally structure this case with `--agent-case`, a path to a JSON file:

```json
{
  "for": [
    {"claim": "Your last three adds to this position each preceded a >10% move.", "provenance": "engine_fact"}
  ],
  "against": [
    {"claim": "This trade would push NVDA to 64% of the book, above your own 25% guideline.", "provenance": "engine_fact"},
    {"claim": "The stock trades at a much higher multiple than when you first bought it.", "provenance": "public_fact"},
    {"claim": "You have added to this name three times in two months, which reads more like averaging into a story than reacting to new evidence.", "provenance": "agent_judgment"}
  ]
}
```

Structured claims only, never a free prose blob. If you send it, both `for` and `against` are required — a one-sided submission is refused, matching the owner ruling above. `provenance` is one of `engine_fact` (drawn from `consider`'s own output), `public_fact` (something you looked up), or `agent_judgment` (your own reasoning) — docs/decision-fomo-kernel-shape.md §3's Layer 3 vocabulary. This flag is entirely optional; a plain `--premise` call is a complete, valid use of `consider`.

## Recording what the user did

Every call is recorded in a local, append-only log — nothing about it is presented back automatically, and nothing about it is required. Once the user has decided, tell the engine with `--resolve`:

```bash
python3 engine/review.py consider --resolve <evaluation_id> --decision acted
```

`--decision` is one of `acted`, `declined`, or `modified`. `--resolve` takes no premise and no other consideration flags — the evaluation it names already carries all of that, frozen from when it was asked. A resolution never rewrites the original record; it appends a new entry that supersedes the old by id, so what the engine actually said at the time is never lost.

There is no obligation to call `--resolve`, and no review step depends on it. Do it when it is natural in the conversation, not as a checklist item.

## What a later review does with an unresolved one

An evaluation left at `decision: "open"` does not go silent. The next `prepare` reconciles it against the transaction record and carries the result in the Review Plan's `evaluation_reconciliation` — a `matched` entry names the date and quantity of a trade found for that ticker and side between the evaluation's `created` day and the review's own close; `unmatched` means none was found. This is a fact about the record, never a claim about cause: `matched` is evidence a qualifying trade happened, not evidence the user made it *because of* the evaluation, and a review never writes `decision` — that stays the user's own word, set only through `--resolve` above. Raise a surfaced evaluation the same way any other supplied fact earns a turn: judge whether it is the relevant thing to say in this scene, not an automatic prompt.
