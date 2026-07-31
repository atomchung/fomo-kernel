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

- `reason` and `why_now` are the user's exact words, quoted, not your summary of them and never translated. Send them together or not at all: telling new evidence apart from a price move is the question this envelope exists to make askable, and a reason with no why-now is the half that lets it pass unasked. If the user has not said why today, ask them — that question is the product working. [market-lookup.md](market-lookup.md) allows one bounded event lookup first, so the question can name what actually happened today instead of being asked cold.
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
| `unclassified_book` | At least one *held* position has no sector/AI classification. It contributes zero to `ai_pct` and is dropped from `max_sector_pct`'s numerator, so both figures are measured over less than the whole book. `unclassified_holdings` names which, and at what weight. |
| `etf_not_decomposed` | At least one held position is a fund whose constituents nothing here inspects. `undecomposed_etfs` names which, at what weight, and whether it was exempted from concentration wholesale or counted as one opaque ticker. |
| `partial_book` | At least one position is outside the usable book every percentage here is measured against — it could not be valued at all, or the ledger's integrity record names it. `excluded_holdings` names which, and `reason` says which of the two. |

`partial_book` obliges the answer, not just the payload. When it is present, state the denominator in the same breath as the number — *"this would become 23% of the priced part of your book; ACME has no cost on record and is excluded"* — and name the excluded holdings wherever a derived percentage appears. A partial denominator presented as a whole one is worse than the refusal this replaced, because the user cannot tell it happened. The engine will not answer at all when *nothing* is usable: that is an empty denominator, not a bounded one.

Say the `reason` too, because the two lead somewhere different. `unusable_shares` and `unavailable_cost` are facts missing from the holding, and the repair is to supply them. `integrity_oversell` is not a missing fact: the history carries a sell of that ticker with no matching prior buy — ordinary for an export that starts mid-account — so the replay clamped it and the recorded share count is not what the history can account for. The repair is the earlier transactions, and telling that user their position "has no cost on record" sends them after a number they already gave you. An `integrity_oversell` entry can also name a ticker the book records *no* position in; that is not a stray, it is the point, because a recorded zero is exactly what an unmatched sell puts in doubt. Never state "you don't hold X" from a book that excluded X for this reason.

A premise about an `integrity_oversell` holding is refused rather than answered, and the refusal names the ticker and the reason. That refusal is narrow on purpose: it is one position, the rest of the book still answers, and the user is told what would make the question answerable. Do not read it as the account being unusable — that whole-book refusal was #673's defect.

The same applies to a rule collision. Each row carries `partial_book: true` when the book it was judged against was bounded, and that qualifier belongs in what you say: the user wrote their cap against their whole book, an excluded position reads as weight zero, and a cap that a hidden position is breaching comes back `clear`. Report the state and say which book it was measured on.

`unclassified_book` and `etf_not_decomposed` oblige the answer the same way, and they are the two reasons a concentration figure can be honest arithmetic and still not mean what it looks like. Say the number and the reach of the number in one breath — *"semiconductors would be 31% of your book; two positions worth 22% of it have no sector classification and are not counted in that figure"* — and never present `ai_pct: 0` as evidence of no AI exposure on a book carrying either key. The weights are real; the composition is what was not read. Both lists also appear inside `before` and `after` themselves, each describing its own book — the top-level pair is `after`'s, the one every number in the response is measured against.

Both are fixable in one round trip, and saying so is part of the answer. `--driver-map` supplies sector/AI labels for names the built-in table has no entry for — it is an explicitly partial common-stock fallback, with no entry for any foreign listing at all, so a company under its primary listing abroad and the same company's US ADR classify differently. `--instrument-map` declares that a ticker is a fund, which is what moves a position out of `unclassified_holdings` and into `undecomposed_etfs`, where the limitation stated is the true one. Use world knowledge to build either, mark what you are unsure of as unknown rather than guessing, and re-ask; per [agent-boundaries.md](agent-boundaries.md) that classification is yours to propose, and the engine's numbers are still the engine's.

There is no look-through inside either key. A fund's constituents are never prorated across sector or AI buckets, so a concentrated single-sector fund and a broad world index fund of the same size are equally invisible to `ai_pct` and `max_sector_pct`; `allocation_exempt` says which of the two ways a given holding is invisible. Do not narrate a fund's likely composition as though the engine measured it — that is your own judgment and is marked as such, like any other claim you add.

There is no disclosure key for a book whose currencies could not be converted. `consider` refuses that book outright and names the missing rate: summing an unconverted currency at a 1:1 factor does not make the aggregate incomplete, it can invert which holding is the largest, and a disclosure the reader takes as "some data is missing" would understate it by an order of magnitude. Supply the rate in `--prices`' `fx` block and ask again.

Pass `--prices` (an envelope in the shape [price-feed.md](price-feed.md) describes) to price the book on current market value instead of cost, and `--cash` (a `{as_of, amount, currency}` object, or a list of them for a multi-currency book) to anchor the cash balance. `--driver-map` and `--instrument-map` carry the same local classification files a review accepts.

## When the whole book refuses (#674)

Everything above is the recoverable case: some holding is excluded, the rest of the book still answers. Sometimes nothing is left to compute a consequence against at all, and that refusal is a different shape, not a bigger version of the one above. It fires for exactly three reasons, all genuinely non-recoverable — no corrected premise and no different ticker fixes any of them:

- the canonical basis itself will not build (structural corruption a malformed ledger row leaves behind);
- an integrity warning names a ticker but this route has no reason it can disclose for it, so it cannot be scoped to one holding the way an `oversell` warning is above;
- every holding was excluded, leaving no usable row to size anything against.

Contrast this against the paragraph above rather than reading it as a licence to widen it: a book where *one* holding is unusable is the recoverable case, and answers about the rest of the book exactly as documented above. This section is what happens when *nothing* is left.

The response is still `{"status": "error", "error": "<message>"}`, and it carries one more field:

```json
{"status": "error", "error": "canonical PortfolioBasis has no usable holding: ...",
 "usable_facts": {
   "as_of": "2026-07-14",
   "concentration": {"max_pos_pct": 0.42, "max_pos_ticker": "PLTR", "ai_pct": 0.61,
                     "max_sector_pct": 0.55, "top3_pct": 0.78},
   "commitment": {"rule": "Cap any single position at 20%.", "metric_key": "max_pos_pct",
                  "metric_value": 0.42, "goal": "down"}}}
```

`usable_facts` is never something this call computed. It is copied, unchanged, from whatever the *last finalized review* already froze — the same `last_state.json` a later `refresh` and split resolution already read forward — filtered to two bounded pieces: `concentration` (the whole-book weight and concentration reading: `max_pos_pct`/`max_pos_ticker`/`ai_pct`/`max_sector_pct`/`top3_pct`) and `commitment` (the rule the user is actually tracking: its own words, the metric it watches, the value frozen when it was chosen, and the direction that counts as a breach). Either half is omitted when that review never froze it; the whole field is `null` when no review has ever been finalized in this root, or when one was and froze neither. Never treat `null` as a smaller version of this contract — it means no computed fact exists, and the answer that follows is [decision-framing.md](decision-framing.md)'s no-book contract instead, with no computed or frozen portfolio number anywhere in it.

**What the answer owes here is framing, not narration.** The bare refusal — "supply a source," "review your exit backlog," restate the error, ask the user to fix the book — is exactly the failure this leaf exists to close; declining to compute a consequence is not declining to help the user decide.

- The first visible sentence is a decision tension — what the trade-off actually is — never the engine's error message and never a request to restart the review.
- Frame at least two of the user's own nominated options — the specific holdings *they* are weighing, gathered from the conversation, never invented. `usable_facts` carries no opinion on which tickers are on the table; that is the user's context, not the engine's.
- For each option, state what selling (or keeping) it would commit the user to believing, and which fact in `usable_facts` it trades off — cite only fields the payload actually carries. Nothing here licenses recomputing a weight, a rule collision, or any other arithmetic the refusal could not produce; a fact absent from `usable_facts` is a fact this answer does not have, not one to estimate.
- Never name which security to sell. This file's opening ruling — the engine computes, it never recommends — holds exactly as hard on a refusal as it does on a priced answer.
- Say once, attached to the claim it qualifies, that the consequence itself — the exact post-trade weight, the cash impact, whether it would collide with the rule — is unavailable. That is the one thing this route could not compute; everything in `usable_facts` is offered instead of it, not as proof it does not matter.

This is a different posture from a declared price dead end (`--prices-unavailable`, [below](#which-market-session-priced-it)): that one refuses the question outright rather than answer on cost basis. This one is a bounded framing that still answers, built from facts already on record before this call was ever made.

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

### When the engine could not price the book (#629)

`valuation_basis` says whether current prices reached this answer. `"priced"` needs nothing from you. `"unpriced"` means every weight above is a share of *cost*, not of market value — and for a trade the user has not placed yet, that is a different book's answer, not a rougher version of this one.

### Which market session priced it

A `"priced"` basis also carries `price_observations`: `as_of`, the newest close this answer used, and `by_ticker`, the session each instrument's own close came from. It is absent — not null, not a placeholder, not today's date — whenever the basis is `unpriced`.

This is a different fact from `basis.as_of`, which is the last row of the *record*. A user who asks the same question twice needs to tell a number that moved because the market moved from one that moved because their book changed, and only these two dates together answer that. Say the price day; [What the answer owes](#what-the-answer-owes) below makes it an owed fact rather than a habit.

`by_ticker` exists because a single frame date cannot say which session a given instrument's number came from — one fresh close otherwise makes every stale one look same-day. Where an instrument's own session matches `as_of`, the summary already covers it; where it does not, say so beside the number it qualifies.

So the response also carries a `price_feed` block beside the evaluation, built by the same engine helper `prepare` uses:

- `provenance` — where the prices came from, and the stable reason code for why retrieval failed.
- `request` — present only when coverage is incomplete: exactly which instruments still need a close. Scoped to the held book plus the premise's own ticker, so it never sends you after a closed position or a benchmark.
- `recovery` — whether recovery was attempted at all, on the same three states [price-feed.md](price-feed.md) documents.
- `next_action` — what to do, ending in `consider --prices <path>`.

Recover the prices before you answer. That lookup is the one carve-out from `SKILL.md` rule 8's ban on multi-tool production — it is completing the input, not producing anything — and [freeform-answers.md](freeform-answers.md) states its bound: transcription only, a count ceiling, and what happens when a source does not resolve.

If the sources genuinely publish nothing, run `consider --prices-unavailable '<the sources you checked>'`. The call then **refuses** instead of returning a cost-basis answer. That is the opposite of what the same declaration does on the review-card lane, on purpose; [price-feed.md](price-feed.md), "Two lanes, two opposite rules", is the single statement of why.

### Stock splits

Both books add up share counts, and a quantity recorded before a split is not comparable to one recorded after it. Ninety shares bought before a ten-for-one, minus a hundred sold after it, is zero — so a position the user still holds can be missing from the book this answer reasons about, with nothing said about it.

`consider` never fetches split data. It resolves one map, per ticker:

1. the `splits` field on a `--prices` row ([price-feed.md](price-feed.md)) governs its own ticker — supply it whenever you supply prices for a ticker the user has held across one;
2. every other ticker keeps the entry the last review in this coach root froze. Declaring a split for one ticker never erases another's recorded one: an envelope legitimately omits `splits` for a close already past its split, and that omission is not a statement that the split never happened.

A root that has never been reviewed and a `--prices` envelope that says nothing about splits leave the answer on as-transacted quantities. That is the pre-existing behaviour and it is silent, which is the reason to fill in the envelope rather than rely on the frozen entries.

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

`must_state` entries are facts, not sentences. Several belong in one sentence: the `basis` and `price_basis` entries are one clause — *"computed on your recorded book as of the 20th, nine days old and never reconciled against a broker view, priced at Tuesday's closes"* — not a bullet each. The order is the order the facts depend on each other (the basis first because every number after it is measured against that book, the price session next because those numbers are measured at one, the disclosures last because they qualify what precedes them), not a script to read aloud. `basis.state_version` is the exception worth naming: it is the book's exact identity, present so a QA run can compare what the user saw against the frozen payload mechanically. Carry it, but do not recite a hash at someone mid-decision — *"this is your book as of the 20th"* is the same fact in the register the rest of the answer is in.

`price_basis` is present only on a priced answer, and is normally one entry: the frame date every number came from. A per-instrument entry appears beside it only where that instrument's own session differs from the frame, and it carries the ticker in `detail` — say that one aloud, because it is the case a single date would have hidden. An unpriced answer carries no `price_basis` entry at all; the `cost_basis` disclosure is what speaks for it there.

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

`required_coverage` is deliberately a subset of `must_state`. It names every disclosure, the basis whenever it is stale or not a declared-complete book, the price session whenever the book was priced, and every rule this trade `would_breach` or is `already_over` on. It does **not** include `unjudged`/`unmapped` collisions: those must be *named in the answer* — an unevaluated rule presented as no issue tells the user something the engine never checked — but a book with eight behavioral rules would otherwise need eight claims saying nothing was measured, and an answer padded to satisfy a checker is worse than a short honest one. Its `path` is matched by prefix, so `basis` accepts any `basis.*` citation and `rule_collisions.<id>` accepts either `.state` or `.worsens`. Where two paths nest, one claim pays the narrower one only: `basis.price_observations` sits under `basis`, so citing the price day covers the price day and leaves the staleness obligation still to be cited.

The block is emitted, never stored: it is a pure function of the premise, basis, consequence, collisions and context the evaluation row already freezes, and it takes no part in the `evaluation_id`. A `--resolve` call carries none, because nothing new is being answered there.

Under maintainer QA, delivery of these obligations is proven rather than assumed. The receipt tool's card-free `consider` route ([ux-receipt.md](ux-receipt.md)) captures the challenge emitted on the call's own stdout into a transient comparison file, paired with the exact answer text shown to the user. The tool computes the coverage and verbatim fidelity itself rather than trusting a self-report, and persists only booleans, counts, and a hash — never the challenge or the presented text. The same trace also records one resolution invitation after it, whose recorded workflow state is the user's own word on what happened next, never proof that a trade was executed.

## The decision-first answer

The challenge block is the floor — what must be present. This section is the shape — what leads, and what attaches to what. On a representative book the block carries roughly seventeen owed facts and half a dozen unchecked items; stated as equal-weight bullets they are complete and communicate no judgment. Before writing the visible answer, make five bounded decisions, privately — this is a drafting discipline, not an engine object, a schema, or anything persisted:

1. choose one **lead** — the single most decision-relevant tension;
2. choose the smallest set of engine facts that supports it;
3. state the strongest genuine counter-case;
4. attach each limitation to the claim it qualifies, compressed;
5. close by inviting the existing resolution without implying execution.

### What leads

Unless a truth-critical disclosure changes how an earlier item can be understood, salience runs:

1. **A user-authored rule collision** — `would_breach`, or `already_over` with `worsens: true`. The user wrote that line themselves; this trade crossing it outranks everything else.
2. **The largest non-obvious portfolio consequence** — weight, concentration or driver overlap, cash. *Non-obvious* is load-bearing: the user already knows they hold the position and that the price fell. What they cannot see from where they sit is what the trade does to the whole book's shape.
3. **The decision-context read** — whether `why_now` looks like a real evidence delta or a price move wearing one, labelled as your judgment. [market-lookup.md](market-lookup.md) governs verifying it.
4. **Routine basis and unchecked boundaries** — present, compressed, attached to what they qualify; they displace the decision only when they materially undermine it.

Special cases: `already_over` with `worsens: false` is an improvement to an already-broken line, never framed as a new breach. A `partial_book` or missing-FX denominator qualifies every affected percentage in the same sentence, not in a footer. A stale or cost-basis book attaches to the conclusion it weakens, and leads only when it makes the apparent consequence unreliable enough to change the decision. With no collision, lead with the largest changed consequence; with no material change, say that the supported dimensions show little change and name what stays unchecked — never convert "not measured" into "no risk".

### The visible shape

Default to two compact paragraphs plus one resolution sentence — a shape, not a template; more or fewer sentences are allowed when the challenge requires them, and there is no word-count target.

- **Paragraph 1 — answer first:** the lead, its key engine support, and any qualifier that changes how it reads.
- **Paragraph 2 — the real trade-off:** the strongest case the other way, the user's exact `reason` / `why_now` where owed, and one grouped limitation clause.
- **Resolution sentence:** keep it open, decline it, or modify it — the user's call, and never imply a broker action occurred.

### Compression

- Every owed fact appears once, not once per section.
- Basis metadata is one clause; group unchecked items by the claim they qualify — no standalone disclaimer list by default, and no generic financial-advice warnings the challenge does not carry.
- A caveat that can overturn the lead sits beside the lead, not at the end.
- The counter-case must be a genuine rebuttal: at least one claim in it engages the lead's strongest support directly. A softened restatement — or a parallel list of unrelated facts that never touches the lead — does not count as the second side.

### Self-check before sending

Four questions, the same four the owner-live acceptance applies (#488; opt-in semantic evaluation is #590 — there is no runtime judge):

1. Cover the limitation sentences — does the answer still stand on its own?
2. Does it say at least one thing the user did not already know, or knew but had not connected?
3. Does the counter-case directly engage the lead's strongest support?
4. Is the user's next step more concrete than before reading?

### One payload, two renderings

A fictional book, run through the real engine: six US holdings at $100,000 total cost, NVDA at 30%, three semiconductor names summing to 60% `ai_pct`; the premise buys 100 more NVDA at $127.50 — below cost, dated after the book's last row — with `reason` *"NVDA is still my highest-conviction name in the book."* and `why_now` *"It dropped hard this week and the discount feels too good to pass up."* The engine returns: NVDA 30% → 37.9%, `ai_pct` 60% → 64.5%, `oversize_triggered` already true *before* the trade, cash running to −$112,750 with `cash_unreliable`, a 45-day-stale unverified cost-basis record, and `unclassified_book` naming three holdings. Seventeen owed facts, five unchecked items, no rule collision on file. Both renderings below are complete against that challenge. They are witnesses to the shape, not reference answers or test oracles.

**Complete but flat — every fact true and anchored, no judgment:**

> The engine computed the following for buying 100 NVDA at $127.50. Basis: transactions as of 2026-06-15, 45 days stale, completeness unverified. NVDA weight before: 30.0%; after: 37.9%. Top-3 concentration after: 64.5%. AI exposure after: 64.5%. Max sector (semiconductors) after: 64.5%. Oversize triggered: true. Concentration triggered: true. Cash balance after: −$112,750. Disclosures: cost basis; cash unreliable; unclassified book (JNJ, PG, KO). Your reason: "NVDA is still my highest-conviction name in the book." Your why-now: "It dropped hard this week and the discount feels too good to pass up." For: you already hold NVDA and it dropped this week. Against: the record is stale; weights are on cost; cash is unreliable; part of the book is unclassified. Not checked: liquidity, valuation, tax, position fit, evidence delta. The decision is yours.

Every number is anchored and every owed fact is present, and it fails all four self-checks: the for-side states what the user already knows, the against-side is the disclosure list wearing a new heading and never touches the for-side, and nothing says what the decision actually trades off.

**Decision-first — same payload, same floor:**

> What this buy mainly changes is not your NVDA entry price — it is the size of a bet that is already past its limit. On your recorded book (cost-priced, June 15, 45 days old, never reconciled), NVDA goes from 30% to about 38% and semiconductors as a group from 60% to about 65%; the position-size line was already triggered before this trade, so this widens an existing breach rather than creating one. The recorded cash line would read −$112,750, but that balance is an unanchored running sum — read it as "no deposit has ever been recorded here", not as a real overdraft.
>
> The strongest case the other way attacks the lead's own numbers: those weights are cost weights on a stale, unverified record — if this week's drop hit your semiconductor names hardest, market-value concentration is lower than the record shows — and your largest position being your highest-conviction name is a choice, not an accident. Against that, your own words — "it dropped hard this week and the discount feels too good to pass up" — describe a price move, not new evidence, and whether anything about the business actually changed is exactly what nothing here checked (nor liquidity, valuation, tax, or fit; JNJ, PG and KO carry no classification, so the 65% is measured against less than the whole book).
>
> Your call: keep this open, decline it, or modify the size — nothing has been executed.

The floor is identical — same numbers, same disclosures, same verbatim quotes, same unchecked list. What changed: one lead was chosen (the second salience tier, since no user rule is on file), the basis caveat sits beside the number it weakens, the counter-case's first claim directly attacks the lead's supporting numbers, the user's own words are read against the evidence-delta question and labelled as judgment, and the cash oddity is interpreted instead of recited.

## The case for and against

The engine states the consequence and the rule collisions; it never recommends. Build the case for and against directly from that output, and take no position on which side wins — that call belongs to the user.

Every claim you add carries its own label: state your record says (drawn straight from `before`/`after`/`delta`/`rule_collisions`), a public fact (something you looked up, sourced), or your own judgment. Do not blend them into one unlabeled sentence. When and how to look something up at all — the standing position packet, the event-lookup triggers, the neutral query, the stop discipline — is [market-lookup.md](market-lookup.md)'s contract.

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
