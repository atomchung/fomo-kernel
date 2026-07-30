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

## What the user said

The premise is the trade. `--decision-context` is optionally the *reason* — what the user tells you they are doing and why today, frozen in their own words beside what the engine computed. Validated against [../schemas/decision-context.schema.json](../schemas/decision-context.schema.json), and accepted as a file path or inline JSON like `--premise`:

```json
{
  "reason": "It is still my highest-conviction name and the build-out has room to run.",
  "why_now": "Their main supplier raised capacity guidance this morning.",
  "evidence_refs": ["Supplier capacity guidance, this morning"]
}
```

Entirely optional. A plain `--premise` call is a complete use of `consider` and behaves exactly as it always has, down to the `evaluation_id` it returns.

- `reason` and `why_now` are the user's exact words, quoted, not your summary of them and never translated. Send them together or not at all: telling new evidence apart from a price move is the question this envelope exists to make askable, and a reason with no why-now is the half that lets it pass unasked. If the user has not said why today, ask them — that question is the product working.
- `evidence_refs` is what they pointed at: a filing, a release, a headline, a note of their own. Zero to five, and only what actually moved the decision. The engine does not fetch, date or believe any of them; this records what was cited.
- Anything over a limit is refused with the limit named, never shortened. A truncated reason or a clipped evidence list reads back as something the user said, which they did not.

Sending a context changes what the call *is*, not what it computes. The consequence and the rule collisions come from the premise and the book alone — identical, byte for byte, whatever the user's reason. What it does change is identity: the same trade asked twice on the same day with two different why-nows is two evaluations, not one silently overwriting the other. That is the point. A user who re-asks after the price moved has told you something, and the record keeps both askings.

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

### Stock splits

Both books add up share counts, and a quantity recorded before a split is not comparable to one recorded after it. Ninety shares bought before a ten-for-one, minus a hundred sold after it, is zero — so a position the user still holds can be missing from the book this answer reasons about, with nothing said about it.

`consider` never fetches split data. It reads, in order:

1. the `splits` field on a `--prices` row ([price-feed.md](price-feed.md)) — supply it whenever you supply prices for a ticker the user has held across one;
2. otherwise, the map the last review in this coach root froze.

A root that has never been reviewed and a `--prices` envelope that says nothing about splits leave the answer on as-transacted quantities. That is the pre-existing behaviour and it is silent, which is the reason to fill in the envelope rather than rely on the fallback.

## What the answer owes

Every `consider` response carries a `challenge` block beside the evaluation: the engine's own statement of what *this* answer has to put in front of *this* user. It exists because the obligations used to live only in this file, to be re-derived by hand on every call from a payload with roughly forty fields in it — and because `consider`'s answer is plain conversation, so nothing between the frozen result and a user told half of it.

Read it as the floor of the answer, and read `SKILL.md` rule 8 with it. That rule says a freeform answer is brief, and in the same breath that brevity bounds what an answer *produces*, never which facts it *owes*. This block is the second clause made computable. A short answer carrying every entry is exactly what rule 8 asks for; a long one that drops a rule collision still fails.

| Key | What it is |
|---|---|
| `must_state` | Ordered owed facts, each `{topic, value}` plus `anchor` when the fact is addressable. |
| `quote_verbatim` | The user's own words, to be reproduced rather than summarized. Empty when no `--decision-context` was supplied. |
| `unchecked` | What the engine did not look at on this call. |
| `case_required` | The floor for a two-sided case: at least one claim on each side. |
| `required_coverage` | The mechanically enforced subset — what an `--agent-case` submission is *refused* for leaving out. |

`must_state` entries are facts, not sentences. Several belong in one sentence: the `basis` entries are one clause — *"computed on your recorded book as of the 20th, nine days old, and never reconciled against a broker view"* — not a bullet each. The order is the order the facts depend on each other (the basis first because every number after it is measured against that book, the disclosures last because they qualify what precedes them), not a script to read aloud. `basis.state_version` is the exception worth naming: it is the book's exact identity, present so a QA run can compare what the user saw against the frozen payload mechanically. Carry it, but do not recite a hash at someone mid-decision — *"this is your book as of the 20th"* is the same fact in the register the rest of the answer is in.

`anchor` is present on most entries and absent on a few. A dot-separated path cannot address a ticker that itself contains a dot, so `2330.TW`'s own weight arrives with its value and no `anchor`: the fact is still owed and still stated, it simply cannot be cited by path. Every anchor that *is* offered has already been resolved against the frozen record, so an anchor from this block is always one the case validator accepts.

`unchecked` names risks the engine never went near — distinct from `disclosures`, which are gaps in numbers it did compute. Silence about a risk nobody checked reads as a clean bill of health the engine never gave.

| Key | What it means |
|---|---|
| `liquidity` | Nothing here measures whether the position can be exited at these prices. |
| `valuation` | No view is taken on whether the price is reasonable. |
| `tax` | Tax consequences of the trade are not computed. |
| `position_fit` | Whether the position still suits this person is outside what the engine measures. |
| `evidence_delta` | Present when a decision context was supplied. Whether the stated why-now is genuinely new information or a price move that feels like one is a call the engine cannot make; label your own read of it as judgment. |
| `evidence_refs_unverified` | Present when the user cited something. The engine did not fetch, date or believe any reference; it recorded that one was cited. |

`required_coverage` is deliberately a subset of `must_state`. It names every disclosure, the basis whenever it is stale or not a declared-complete book, and every rule this trade `would_breach` or is `already_over` on. It does **not** include `unjudged`/`unmapped` collisions: those must be *named in the answer* — an unevaluated rule presented as no issue tells the user something the engine never checked — but a book with eight behavioral rules would otherwise need eight claims saying nothing was measured, and an answer padded to satisfy a checker is worse than a short honest one. Its `path` is matched by prefix, so `basis` accepts any `basis.*` citation and `rule_collisions.<id>` accepts either `.state` or `.worsens`.

The block is emitted, never stored: it is a pure function of the premise, basis, consequence, collisions and context the evaluation row already freezes, and it takes no part in the `evaluation_id`. A `--resolve` call carries none, because nothing new is being answered there.

Under maintainer QA, delivery of these obligations is proven rather than assumed. The receipt tool's card-free `consider` route ([ux-receipt.md](ux-receipt.md)) captures the challenge emitted on the call's own stdout into a transient comparison file, paired with the exact answer text shown to the user. The tool computes the coverage and verbatim fidelity itself rather than trusting a self-report, and persists only booleans, counts, and a hash — never the challenge or the presented text. The same trace also records one resolution invitation after it, whose recorded workflow state is the user's own word on what happened next, never proof that a trade was executed.

## The case for and against

The engine states the consequence and the rule collisions; it never recommends. Build the case for and against directly from that output, and take no position on which side wins — that call belongs to the user.

Every claim you add carries its own label: state your record says (drawn straight from `before`/`after`/`delta`/`rule_collisions`), a public fact (something you looked up, sourced), or your own judgment. Do not blend them into one unlabeled sentence.

Name what nobody checked, every time. `consider` measures weight, concentration, driver overlap, cash, and rule collisions — nothing else. Liquidity, valuation, tax consequences, and whether the position still fits this person are all real risks the engine does not measure. Silence about a risk it did not check reads as a clean bill of health it never gave. Record staleness (above) belongs on this list too whenever `stale_days` is more than trivial.

You may optionally structure this case with `--agent-case`, a path to a JSON file, checked by `engine/answer_provenance.py::validate_agent_case` (#414) before anything is stored or returned:

```json
{
  "for": [
    {"claim": "You have historically held through drawdowns of this size in this name without selling.", "provenance": "agent_judgment"}
  ],
  "against": [
    {"claim": "This grows NVDA to 64% of the book.", "provenance": "engine_fact", "anchor": "consequence.after.max_pct"},
    {"claim": "This is priced on cost, not a live market value, so the weight above may be off.", "provenance": "engine_fact", "anchor": "consequence.disclosures.0"},
    {"claim": "The record is several days stale.", "provenance": "engine_fact", "anchor": "basis.stale_days"},
    {"claim": "The stock trades at a much higher earnings multiple than when you first bought it.", "provenance": "public_fact", "source": "Market data provider", "as_of": "2026-07-20"}
  ]
}
```

Structured claims only, never a free prose blob. If you send it, both `for` and `against` are required and neither may be empty — a one-sided or empty-sided submission is refused, matching the owner ruling above. `provenance` is one of `engine_fact` (drawn from `consider`'s own output), `public_fact` (something you looked up), or `agent_judgment` (your own reasoning) — docs/decision-fomo-kernel-shape.md §3's Layer 3 vocabulary. This flag is entirely optional; a plain `--premise` call is a complete, valid use of `consider`.

**A claim's provenance decides what else it must carry**, per `schemas/answer-provenance.schema.json`:

- `engine_fact` must carry `anchor`: a dot-separated path into exactly this call's own frozen `basis`, `consequence`, or `rule_collisions` (`rule_collisions` is addressed by `rule_id`, e.g. `rule_collisions.rule-1.worsens`, never by list position). The path must resolve to one fact, never a container, and copy it verbatim from the JSON `consider` already handed you rather than retyping it by hand. When the resolved fact is a number, quote it in the claim's own prose within half a display unit, at whichever scale the record itself uses — a fraction-shaped value (weights, `max_pct`, `ai_pct`, …) is written ×100 as a percent, everything else (`stale_days`, share counts, dollar balances) as-is. A claim anchored at a `rule_collisions[...].state` or `.worsens` field whose frozen state is `already_over` with `worsens` not null must also carry its own `worsens` boolean, matching the frozen one exactly (see [Reading a rule collision](#reading-a-rule-collision) above) — and `worsens` is forbidden on every other engine_fact claim.
- `public_fact` must carry `source` (the named external source) and `as_of` (an ISO date). It must never restate what the user themselves said through `--decision-context`'s `reason`/`why_now` — copying the user's own words and relabelling them as an outside fact is refused, not stored.
- `agent_judgment` carries nothing beyond `claim` and `provenance`.

**Everything on `required_coverage` must be covered, or the whole case is refused.** That list arrives in the same response ([What the answer owes](#what-the-answer-owes) above) — you do not have to derive it. For each entry, at least one `engine_fact` claim must anchor at or under its `path`: every key in the frozen `consequence.disclosures`, the `basis` whenever it is stale (`stale_days > 0`) or not a declared-complete snapshot, and every rule this trade `would_breach` or is `already_over` on. This is why the example above anchors `consequence.disclosures.0` and `basis.stale_days` even though neither reads as dramatic on its own — leaving one out is refused the same as a wrong number, and silence about a rule the trade breaks reads to the user as a rule that held.

A rejected case is refused before it is stored or shown: the caller gets the validator's own error, naming the exact claim and the exact rule it failed, and `consider` persists and returns nothing for that attempt. Fix the claim and resend, or drop `--agent-case` and present the case in plain prose instead.

## Recording what the user did

Every call is recorded in a local, append-only log — nothing about it is presented back automatically, and nothing about it is required. Once the user has decided, tell the engine with `--resolve`:

```bash
python3 engine/review.py consider --resolve <evaluation_id> --decision acted
```

`--decision` is one of `acted`, `declined`, or `modified`. `--resolve` takes no premise and no other consideration flags — the evaluation it names already carries all of that, frozen from when it was asked, including any decision context. A resolution never rewrites the original record; it appends a new entry that supersedes the old by id, carrying the frozen premise, basis, consequence and the user's own words forward unchanged, so what the engine actually said at the time — and what the user said it was for — is never lost.

There is no obligation to call `--resolve`, and no review step depends on it. Do it when it is natural in the conversation, not as a checklist item.

## What a later review does with an unresolved one

An evaluation left at `decision: "open"` does not go silent. The next `prepare` reconciles it against the transaction record and carries the result in the Review Plan's `evaluation_reconciliation` — a `matched` entry names the date and quantity of a trade found for that ticker and side between the evaluation's `created` day and the review's own close; `unmatched` means none was found. This is a fact about the record, never a claim about cause: `matched` is evidence a qualifying trade happened, not evidence the user made it *because of* the evaluation, and a review never writes `decision` — that stays the user's own word, set only through `--resolve` above. Raise a surfaced evaluation the same way any other supplied fact earns a turn: judge whether it is the relevant thing to say in this scene, not an automatic prompt.
