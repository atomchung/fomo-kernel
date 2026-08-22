# Weighing a trade the user has not placed yet

## The exemplar

One whole `consider` answer, over a three-candidate comparison: the stance and
its deciding reason on top, the counter-side present only as the line that could
overturn the pick, and the book date, the price session and the unevaluated
valuation gap as one end block. Read it before writing one.

It is copied verbatim from `tests/agent/expression-witnesses.json`, scene
`consider_three_way_comparison` — the corpus that
[expression-contract.md](../../../docs/expression-contract.md) §3.5 makes the
binding statement of this shape. Every issuer in it is invented, and
`tests/test_expression_contract.py` fails if the two copies disagree.

```exemplar consider_three_way_comparison
三個裡我會選 GRDC 加 15 股。決定性理由：三案對組合的影響都在一個百分點內——誰都不改變你的集中度——真正有差的只有事件風險：WDGT 六天後出財報、預期已拉滿（公司財報行事曆，2026-08-14），這時把最大倉再加大，是三案裡波動最大的；FABR 18 股只佔 1.2%，公司再好這個大小也改變不了結果。GRDC 下次財報在十月底，中間乾淨，上季主業 +82%（公司財報，2026-07-30）撐著。

反面就一條：前三大會從 51.3% 升到 51.9%（GRDC 本來就是第二大）——嫌集中的話這是三案共同的問題，答案是減碼不是選誰。
會讓我改口：你本來就想賭財報超預期——那 WDGT 反而是最直接的表達，排序整個反過來。

（帳本 8/14、價格 8/14 收盤；三案動用 $4.4K／$5.1K／$4.9K；估值未評）
```

A user mid-decision asks something like *"I'm thinking of buying NVDA — what does that do to my book?"* They are not in a review and will not hand over a CSV. `consider` answers from what the product already stores: the local ledger, or transaction files if you have them in hand.

This is Layer 2 (docs/decision-fomo-kernel-shape.md §3-4): deterministic arithmetic over a hypothetical trade. The engine computes the consequence; the agent turns it and any relevant sourced evidence into an explicit recommendation. The recommendation is `agent_judgment`, never a disguised engine output or execution claim.

`consider`'s answer is plain conversation, not a card — which means it is a
freeform surface and `freeform-answers.md`'s default applies: simple questions
start with a quick, direct text answer; relevant research, tools, and visuals
remain available when they materially improve the decision.

## When this applies

Any pre-trade question about a hypothetical trade against the user's current
book — "should I buy this," "am I chasing," "should I add here," "does this
break my own rule." Not for a review (use `prepare`). `consider` prices one
hypothetical trade per call; comparison uses one ephemeral call per candidate
whose portfolio consequence matters. Candidate discovery itself is host-side
under `market-lookup.md`'s universe, coverage, and provenance contract.

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

- `ticker` and `side` (`buy` or `sell`) are required. `price` — the hypothetical execution price, in the instrument's own trading currency — is optional (#777, owner ruling 2026-08-02): state it when you have one in mind, and omit it to let the engine default to its own observed close for that ticker, the same close every other number in the answer is priced from. A `notional` trade divides through whichever price this ends up being, stated or defaulted. The stored premise carries which of the two happened as `price_basis` (`user_stated` or `observed`), and a defaulted price is a `must_state` fact under the `position` topic in `challenge` — say "priced at the last close" when it fired, so a system default is never read as a number the user chose. Refused, naming the ticker, when price is omitted and the engine has no observed close for it either — a premise this route cannot price is never answered on an invented number.
- Exactly one of `qty` or `notional` (cash terms in the instrument's currency, converted to `qty` by dividing through `price`, stated or defaulted). Send whichever one you actually have in mind; do not send both.
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

- `reason` and `why_now` are the user's exact words, quoted, not your summary of them and never translated. Send them together or not at all. Ask for why-now only when its answers could change the recommendation, settle provenance classification, or make a context-bearing evaluation the user chose to record complete. Otherwise omit the optional context and answer the plain premise. [market-lookup.md](market-lookup.md) allows a bounded event lookup when it would make a decision-changing question more concrete.
- `evidence_refs` is what they pointed at: a filing, a release, a headline, a note of their own. Zero to five, and only what actually moved the decision. The engine does not fetch, date or believe any of them; this records what was cited.
- Anything over a limit is refused with the limit named, never shortened. A truncated reason or a clipped evidence list reads back as something the user said, which they did not.

Sending a context changes what the call *is*, not what it computes. The consequence and the rule collisions come from the premise and the book alone — identical, byte for byte, whatever the user's reason. What it does change is identity: the same trade asked twice on the same day with two different why-nows is two evaluations, not one silently overwriting the other. That is the point. A user who re-asks after the price moved has told you something, and the record keeps both askings.

## Reading the consequence

The response carries `before` and `after` — the book's own state without and with the hypothetical trade — plus `delta` (only the readings that actually moved) and `disclosures` (see below). Weight, concentration (`ai_pct`, `max_sector_pct`, `top3`), whether the position-size cap or the concentration line is triggered, cash balance and weight, and how many holdings the book would carry are all in both snapshots, so a before/after comparison never has to be recomputed by hand.

`max_sector` inside those snapshots is the engine's own canonical label, and it stays that way whatever language you asked in — it is part of the stored evaluation, and the same trade evaluated in two languages has to be one evaluation. **Name the sector from `sector_display`, never from `consequence.*.max_sector`.** `sector_display` arrives beside the evaluation, carries `before` and `after` — they genuinely differ when the trade is what changes which sector leads — and is already in the language you asked for. A category the user invented in their own driver map has nothing to translate to and comes back unchanged. The key is absent when the book has no largest sector to name; absent means there is none, never that one went untranslated.

`disclosures` is a list of machine-readable keys, not prose — read them as gaps in the numbers, not failures:

| Key | What it means |
|---|---|
| `cost_basis` | No current price was supplied, so weights are computed on cost rather than market value. |
| `cash_unreliable` | The cash balance has no anchor and is a running sum from cash flows alone. |
| `unmapped_driver` | The premise's ticker has no sector/AI classification, so it cannot be accounted for in concentration. |
| `unclassified_book` | At least one *held* position has no sector/AI classification. It contributes zero to `ai_pct` and is dropped from `max_sector_pct`'s numerator, so both figures are measured over less than the whole book. `unclassified_holdings` names which, and at what weight. |
| `etf_not_decomposed` | At least one held position is a fund whose constituents nothing here inspects. `undecomposed_etfs` names which, at what weight, and whether it was exempted from concentration wholesale or counted as one opaque ticker. |
| `partial_book` | At least one position is outside the usable book every percentage here is measured against — it could not be valued at all, or the ledger's integrity record names it. `excluded_holdings` names which, and `reason` says which of the two. |

The keys themselves stay this exact stable English vocabulary whatever language you asked in — the same rule `max_sector` follows above, and for the same reason: `required_coverage` and an `--agent-case` engine_fact anchor address this array by position (`consequence.disclosures.0`), so translating the array itself would break every existing citation the moment `--language` changed. **Say the meaning in the reader's own language from `disclosures_display`, never by translating the key yourself.** `disclosures_display` arrives beside the evaluation (and beside a `--resolve` response re-emitting the same row), keyed by the same strings this table names, already resolved through `copy/<locale>.json`'s own `disclosures` table for the language you asked in (#739). It is present only when `disclosures` is non-empty; an unrecognized or retired key (`consequence.RETIRED_DISCLOSURES`) reads back as itself rather than raising.

`partial_book` obliges the answer, not just the payload. When it is present, state the denominator in the same breath as the number — *"this would become 23% of the priced part of your book; ACME has no cost on record and is excluded"* — and name the excluded holdings wherever a derived percentage appears. A partial denominator presented as a whole one is worse than the refusal this replaced, because the user cannot tell it happened. The engine will not answer at all when *nothing* is usable: that is an empty denominator, not a bounded one.

Say the `reason` too, because the two lead somewhere different. `unusable_shares` and `unavailable_cost` are facts missing from the holding, and the repair is to supply them. `integrity_oversell` is not a missing fact: the history carries a sell of that ticker with no matching prior buy — ordinary for an export that starts mid-account — so the replay clamped it and the recorded share count is not what the history can account for. The repair is the earlier transactions, and telling that user their position "has no cost on record" sends them after a number they already gave you. An `integrity_oversell` entry can also name a ticker the book records *no* position in; that is not a stray, it is the point, because a recorded zero is exactly what an unmatched sell puts in doubt. Never state "you don't hold X" from a book that excluded X for this reason.

A premise about an `integrity_oversell` holding is refused rather than answered, and the refusal names the ticker and the reason. That refusal is narrow on purpose: it is one position, the rest of the book still answers, and the user is told what would make the question answerable. Do not read it as the account being unusable — that whole-book refusal was #673's defect.

The same applies to a rule collision. Each row carries `partial_book: true` when the book it was judged against was bounded, and that qualifier belongs in what you say: the user wrote their cap against their whole book, an excluded position reads as weight zero, and a cap that a hidden position is breaching comes back `clear`. Report the state and say which book it was measured on.

`unclassified_book` and `etf_not_decomposed` oblige the answer the same way, and they are the two reasons a concentration figure can be honest arithmetic and still not mean what it looks like. Say the number and the reach of the number in one breath — *"semiconductors would be 31% of your book; two positions worth 22% of it have no sector classification and are not counted in that figure"* — and never present `ai_pct: 0` as evidence of no AI exposure on a book carrying either key. The weights are real; the composition is what was not read. Both lists also appear inside `before` and `after` themselves, each describing its own book — the top-level pair is `after`'s, the one every number in the response is measured against.

Both are fixable in one round trip, and saying so is part of the answer. `--driver-map` supplies sector/AI labels for names the built-in table has no entry for — it is an explicitly partial common-stock fallback. A handful of major Taiwan dual-listings and local benchmark ETFs are built in (#741: `2330.TW` classifies the same as its ADR `TSM`), but foreign-listing coverage stops there, so most companies under their primary listing abroad and the same company's US ADR still classify differently. `--instrument-map` declares that a ticker is a fund, which is what moves a position out of `unclassified_holdings` and into `undecomposed_etfs`, where the limitation stated is the true one. Use world knowledge to build either, mark what you are unsure of as unknown rather than guessing, and re-ask; per [agent-boundaries.md](agent-boundaries.md) that classification is yours to propose, and the engine's numbers are still the engine's.

There is no look-through inside either key. A fund's constituents are never prorated across sector or AI buckets, so a concentrated single-sector fund and a broad world index fund of the same size are equally invisible to `ai_pct` and `max_sector_pct`; `allocation_exempt` says which of the two ways a given holding is invisible. Do not narrate a fund's likely composition as though the engine measured it — that is your own judgment and is marked as such, like any other claim you add.

There is no disclosure key for a book whose currencies could not be converted. `consider` refuses that book outright and names the missing rate: summing an unconverted currency at a 1:1 factor does not make the aggregate incomplete, it can invert which holding is the largest, and a disclosure the reader takes as "some data is missing" would understate it by an order of magnitude. Supply the rate in `--prices`' `fx` block and ask again.

Pass `--prices` (an envelope in the shape [price-feed.md](price-feed.md) describes) to price the book on current market value instead of cost, and `--cash` (a `{as_of, amount, currency}` object, or a list of them for a multi-currency book) to anchor the cash balance. `--driver-map` and `--instrument-map` carry the same local classification files a review accepts.

Omitting `--cash` is not the same as declaring no anchor exists (#756). When the last finalized review in this root anchored its own cash reading, that balance is reused automatically — restated as of the review's own `date_end`, which is exact rather than approximate, because every cash flow up to that date is already folded into the number the review froze. An explicit `--cash` on this call always overrides it. The fallback is offered only when the last review's own cash was itself genuinely anchored; a last review that fell back to `csv_sum` leaves nothing firmer to reuse, and this call computes its own unanchored running sum exactly as before, still disclosed through `cash_unreliable`.

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

`usable_facts` is never something this call computed. It is copied, unchanged, from whatever the *last finalized review* already froze — the same `last_state.json` a later `refresh` and split resolution already read forward — filtered to two bounded pieces: `concentration` (the whole-book weight and concentration reading: `max_pos_pct`/`max_pos_ticker`/`ai_pct`/`max_sector_pct`/`top3_pct`) and `commitment` (the rule the user is actually tracking: its own words, the metric it watches, the value frozen when it was chosen, and the direction that counts as a breach). Either half is omitted when that review never froze it; the whole field is `null` when no review has ever been finalized in this root, or when one was and froze neither. Never treat `null` as a smaller version of this contract — it means no frozen fact is safe to use. The recorded book is still present but its consequence is not computable, so use [No safe decision value](#no-safe-decision-value) below: no computed or frozen portfolio number, no no-book question sequence, and no process narration.

**What the answer owes here is framing, not narration.** The bare refusal — "supply a source," "review your exit backlog," restate the error, ask the user to fix the book — is exactly the failure this leaf exists to close; declining to compute a consequence is not declining to help the user decide.

#### Multiple user-nominated alternatives

- Compare whichever alternatives remain live in the user's decision. Do not
  invent an option merely to create symmetry.
- Use only fields `usable_facts` actually carries for portfolio claims. A fact
  absent from it is unavailable, not one to estimate. Relevant sourced company
  or market evidence may still distinguish the candidates when portfolio
  arithmetic cannot.
- Recommend or rank when the supported evidence distinguishes the options;
  otherwise name the discriminator that remains unresolved.
- State the material reach of the refusal — exact post-trade weight, cash
  impact, or rule collision was not computed — without turning it into the
  lead or a process narrative.

#### One proposed trade

This is not a comparison with an invented candidate. Give the strongest direct
recommendation the user's premise, relevant sourced evidence, and any safe
frozen facts support. A counter-case or additional question appears only when
it could change that recommendation. Say the material portfolio limitation
without turning it into the lead or repeating it as filler.

Keep the premise and context intact. Name the precise next system fact or check
that would let the same proposal answer — for example, a usable current-book
basis — without asking the user to repeat any known reason, timing, premise, or
candidate. Do not expose commands, gates, provider attempts, recovery/retry
chronology, QA steps, payload/schema names, unrelated symbols, or maintainer
work. Do not add arithmetic, claim execution, or
promise background work.

#### No safe decision value

When no supported recommendation is possible, state what remains unknown and
the exact fact that would change the decision. Use the shortest clear form for
the situation; no fixed sentence count or template applies. Do not narrate the
refusal process.

This is a different posture from a declared price dead end (`--prices-unavailable`, [below](#which-market-session-priced-it)): that one refuses only the current-value portfolio consequence rather than answer that claim on cost basis. The host still gives any supported non-portfolio judgment the question warrants. This one is a bounded framing built from facts already on record before this call was ever made.

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

**`state` is diagnostic. `rule_effect` is the verdict.** `state` answers *where the book stands*; the question the user asked is *what this trade does*, and those are different questions. Composing the answer out of `state` and `worsens` is what failed owner-live acceptance on 2026-08-02 — a sell taking a position from 80% to 75% against a self-authored 20% cap was described back as breaking the rule — so the engine now derives one verdict and every decision-facing surface reads it (#579). Never re-derive it, and never let `state` stand in for it.

| `rule_effect` | What this trade did | The statement it owes |
|---|---|---|
| `new_breach` | The line was not crossed before this trade and is after it. | "This would push you over your own [X] rule." |
| `worsened_existing_breach` | Already over, and this trade moves further over. | Both: already over, *and* this trade increases it. |
| `improved_but_still_over` | Already over, and this trade moves back toward the line without reaching it. | Both truths: it reduces the position, *and* it remains above the line. Never a breach this trade caused. |
| `resolved_existing_breach` | Was over the line; is under it after this trade. | That this trade brings the position back inside the rule. |
| `unchanged_existing_breach` | Already over, and this trade does not move the reading. | Already over, and this trade neither helps nor hurts it. |
| `compliant` | Not over the line, before or after. | No caveat needed — the only effect that may pass in silence. |
| `unjudged` / `unmapped` | Not evaluated at all. | Named as unevaluated, never as a pass. |

The `challenge` block's `rule_effects` array is the product-safe projection of this: per rule, the effect, the line it was judged against (`limit`), whose line it is (`limit_source`), and two lists of semantic slots — `must_convey` and `must_not_convey`. Realize every `must_convey` slot; assert nothing on `must_not_convey`. They are meanings, not wording: several slots commonly belong in one clause.

**`limit_source` decides whether you may call it *their* rule.** `user_cap` means the user set that position cap themselves, so "your 20% rule" is true. `engine_default` means this engine picked the threshold — describe it as the line the engine measures against, never as a rule the user wrote.

**Two things `state`/`worsens` cannot say at all**, which is why this is a field rather than a guideline: `improved_but_still_over` and `unchanged_existing_breach` are *both* `already_over` with `worsens: false` (the direction test is a strict `>`, so "reduced" and "did not move" are the same `false`), and `resolved_existing_breach` is indistinguishable from `compliant` under `state` alone. `state` and `worsens` remain on the row as machine diagnostics for QA and replay; they are not what the answer states.

## Which book answered

`basis` names what the consequence was computed against and how current it was:

- `source: "transactions"` — you supplied one or more CSV paths, read the same way a review reads them.
- `source: "snapshot_anchor"` — no CSV was supplied, so the book was reconstructed from the local ledger: the latest declared holdings snapshot, plus every trade after it.
- `source: "transaction_replay"` — no CSV was supplied and no snapshot anchor exists, so the book was replayed purely from the ledger's trade history.
- `source: "empty"` — no CSV was supplied and the ledger has neither a snapshot anchor nor any trades.
- `as_of` — the date of the record's own latest row.
- `stale_days` — how many days between `as_of` and today. This is disclosed, never gated on: a large value does not block the answer, because no threshold for interrupting the user over it has been set. State it rather than act on it.

The CSV/FIFO path a review uses and the ledger reconstruction `consider` falls back to can legitimately disagree about a position's weight — they are answering different questions from different completeness requirements. Say which basis was used rather than presenting either as the only number.

### When the engine could not price the book (#629)

`valuation_basis` says whether current prices reached this answer. `"priced"` needs nothing from you. `"unpriced"` means every weight above is a share of *cost*, not of market value — and for a trade the user has not placed yet, that is a different book's answer, not a rougher version of this one.

### Which market session priced it

A `"priced"` basis also carries `price_observations`: `as_of`, the newest close this answer used, and `by_ticker`, the session each instrument's own close came from. It is absent — not null, not a placeholder, not today's date — whenever the basis is `unpriced`.

This is a different fact from `basis.as_of`, which is the last row of the *record*. A user who asks the same question twice needs to tell a number that moved because the market moved from one that moved because their book changed, and only these two dates together answer that. Say the price day; [What the answer owes](#what-the-answer-owes) below makes it an owed fact rather than a habit.

`by_ticker` exists because a single frame date cannot say which session a given instrument's number came from — one fresh close otherwise makes every stale one look same-day. Where an instrument's own session matches `as_of`, the summary already covers it; where it does not, say so beside the number it qualifies.

So the response carries a `price_feed` block beside the evaluation — or beside
the recoverable refusal when no market-value denominator can yet be built —
using the same engine helper `prepare` uses:

- `provenance` — where the prices came from, and the stable reason code for why retrieval failed.
- `request` — present only when coverage is incomplete: exactly which instruments still need a close and which currencies still need FX. Scoped to usable canonical current holdings plus the premise's own ticker; integrity, quantity and unmatched-history exclusions stay in disclosure/provenance and never become market-data work. An empty `tickers` list with a non-empty `currencies` list is an FX-only repair.
- `recovery` — whether recovery was attempted at all, on the same three states [price-feed.md](price-feed.md) documents.
- `next_action` — what to do, ending in `consider --prices <path>`.

Recover the prices before making a portfolio-consequence claim. The lookup is
completing the deterministic input, and [freeform-answers.md](freeform-answers.md)
states its boundary: transcription only, adaptive coverage/cost stopping, and
what happens when a source does not resolve.

If the sources genuinely publish nothing, run `consider --prices-unavailable '<the sources you checked>'`. The call then **refuses** instead of returning a cost-basis answer. That is the opposite of what the same declaration does on the review-card lane, on purpose; [price-feed.md](price-feed.md), "Two lanes, two opposite rules", is the single statement of why.

### Stock splits

Both books add up share counts, and a quantity recorded before a split is not comparable to one recorded after it. Ninety shares bought before a ten-for-one, minus a hundred sold after it, is zero — so a position the user still holds can be missing from the book this answer reasons about, with nothing said about it.

`consider` never fetches split data. It resolves one map, per ticker:

1. the `splits` field on a `--prices` row ([price-feed.md](price-feed.md)) governs its own ticker — supply it whenever you supply prices for a ticker the user has held across one;
2. every other ticker keeps the entry the last review in this coach root froze. Declaring a split for one ticker never erases another's recorded one: an envelope legitimately omits `splits` for a close already past its split, and that omission is not a statement that the split never happened.

A root that has never been reviewed and a `--prices` envelope that says nothing about splits leave the answer on as-transacted quantities. That is the pre-existing behaviour and it is silent, which is the reason to fill in the envelope rather than rely on the frozen entries.

## What the answer owes

Every `consider` response carries a `challenge` block beside the evaluation: the engine's own statement of what *this* answer has to put in front of *this* user. It exists because the obligations used to live only in this file, to be re-derived by hand on every call from a payload with roughly forty fields in it — and because `consider`'s answer is plain conversation, so nothing between the frozen result and a user told half of it.

Read it as the floor of the answer, and read `SKILL.md` with it. A simple answer
defaults to brevity, but no presentation choice bounds which facts the answer
*owes*. This block makes the claim floor computable: an answer of any length
that drops a required rule collision still fails.

| Key | What it is |
|---|---|
| `must_state` | Ordered owed facts, each `{topic, value}` plus `anchor` when the fact is addressable. The trailing two topics name holdings: `excluded_holding` for what fell out of the book entirely, `out_of_scope` for what stayed in it with its composition unread. |
| `may_state` | Computed and owed on no call (#830): the concentration family and cash. State one when this decision turns on it. |
| `machine_state` | Anchors for machines. Never rendered, in any register. |
| `rule_effects` | The product-safe projection of what this trade does to each of the user's own rules: `effect`, the `limit` it was judged against and its `limit_source`, and the `must_convey` / `must_not_convey` slots. Empty when no rule speaks. See [Reading a rule collision](#reading-a-rule-collision). |
| `quote_verbatim` | The user's own words, to be reproduced rather than summarized. Empty when no `--decision-context` was supplied. |
| `unchecked` | Research dimensions the engine did not look at. An availability list the answer never enumerates; a material gap earns one end-block line. |
| `case_required` | The preferred positive case: one recommendation, at least one supporting claim, and a counter-case when material. |
| `required_coverage` | The mechanically enforced subset — what an `--agent-case` submission is *refused* for leaving out. |

### What "state" means

**Stated = the fact appears with its correct anchor.** An inline number, a
table cell, and a line in the answer's end block all qualify; so does a clause
that carries two or three facts at once. What does not qualify is a number
whose denominator, unit, or session has been left behind, because that is a
different fact wearing the same digits.

Fifteen obligations are not fifteen sentences, and the difference is the whole
reason this block lists *facts*. The `basis` and `price_basis` entries are one
clause — *"on your book as of the 20th, priced at Tuesday's closes"* — not a
bullet each. `must_state`'s order is the order the facts depend on each other
(the basis first because every number after it is measured against that book,
the price session next because those numbers are measured at one, the
disclosures last because they qualify what precedes them). It is a dependency
order and never a reading order: [the answer's own order](#answer-shape) is the
pyramid's, and the fact that decides the call opens it.

### The three lists, and why each fact is on the one it is on (#830)

Owner ruling, 2026-08-20. The block used to be one list of roughly fifteen owed
facts, and an obligation list has one cheapest discharge: a sentence per item.
The audit asked, of each entry, what its must-have reason was, and deleted the
ones that had none — starting with `basis.state_version`, a content hash that
had been sitting on the list of facts a human answer owes.

**Kept, with the reason each keep is a keep:**

| Kept | Why it may not be dropped |
|---|---|
| Stance and the deciding reason (`case_required`) | It is the product's point. An answer without it is a briefing, not a decision. |
| Every non-empty `rule_effects` entry | Silence about a line the user wrote themselves is help breaking it. Owner-live incident, 2026-08-02. |
| The one or two consequence numbers that would flip *this* decision | That is the answer, not a disclosure. |
| A falsifier on any directional recommendation | The one element the owner singled out as praise in the #827 blind A/B. |
| "Recorded a consideration, not executed", when a canonical write happened | The user cannot see the difference from where they sit, and the difference is real money. |
| Source and as-of on a public fact, compact and inline | A looked-up fact with no provenance is indistinguishable from an invented one (C2). |
| One warning line when pricing is degraded | Cost weights can invert which position is the largest — the fixture evidence in [freeform-answers.md](freeform-answers.md). |

**Deleted, and what replaced each:**

| Deleted | What it is now |
|---|---|
| `basis.state_version` in the answer | `machine_state`. Never rendered; *"this is your book as of the 20th"* is the same fact in the register the rest of the answer is in. |
| The basis four-piece recital every answer | Two facts, `as_of` and `stale_days`, and staleness is *said* only when it could change the decision. Otherwise it is one end-block line. |
| The concentration family every answer | `may_state`. It surfaces when it is the deciding fact or when it touches the user's own cap — and the second case is not judgment, because `rule_effects` carries it and `required_coverage` enforces it. |
| Cash balance and weight every answer | `may_state`. State it when cash is the question or a floor is being crossed. |
| Enumerating `unchecked` | Nothing. A material gap earns one end-block line; four lines saying nothing was measured is the purest form of discharging a list. |
| A standalone counter-case section | The falsifier line. `counter_case: when_material` stays in the payload; it stops becoming a section. |
| Basis and caliber narrated in the body *and* the footer | One compact end block. `disclosures_display` lines land there. |

The data layer did not shrink. Every number is still computed, still anchored,
still citable, and the user can ask for any of it — which is what makes not
saying a number this decision does not turn on different from hiding it. What
moved to judgment is *selection*, and the named risk is omitting a consequence
that mattered; the two silences that could help a user break their own rule are
the two that stayed machine-enforced.

`price_basis` is present only on a priced answer, and is normally one entry: the frame date every number came from. A per-instrument entry appears beside it only where that instrument's own session differs from the frame, and it carries the ticker in `detail` — say that one aloud, because it is the case a single date would have hidden. An unpriced answer carries no `price_basis` entry at all; the `cost_basis` disclosure is what speaks for it there.

`anchor` is present on most entries and absent on a few. A dot-separated path cannot address a ticker that itself contains a dot, so `2330.TW`'s own weight arrives with its value and no `anchor`: the fact is still owed and still stated, it simply cannot be cited by path. Every anchor that *is* offered has already been resolved against the frozen record, so an anchor from this block is always one the case validator accepts.

`unchecked` names available research dimensions the engine never went near — distinct from `disclosures`, which are gaps in numbers it did compute. It is an availability list the answer **never enumerates**. One of these earns one end-block line when it could change the recommendation, or when the answer's own wording would otherwise imply it was checked; the rest stay silent. In the #827 A/B the same *"I did not check valuation"* appeared twice in one answer, which is what a recital quota produces when the model is trying to be honest.

| Key | What it means |
|---|---|
| `liquidity` | Nothing here measures whether the position can be exited at these prices. |
| `valuation` | No view is taken on whether the price is reasonable. |
| `tax` | Tax consequences of the trade are not computed. |
| `position_fit` | Whether the position still suits this person is outside what the engine measures. |
| `evidence_delta` | Present when a decision context was supplied. Whether the stated why-now is genuinely new information or a price move that feels like one is a call the engine cannot make; label your own read of it as judgment. |
| `evidence_refs_unverified` | Present when the user cited something. The engine did not fetch, date or believe any reference; it recorded that one was cited. |

`required_coverage` names every disclosure, the basis whenever it is stale or not a declared-complete book, the price session whenever the book was priced, and every rule whose `rule_effect` is `new_breach`, `worsened_existing_breach`, `improved_but_still_over` or `unchanged_existing_breach` — the four where the line is still crossed after this trade and silence would read as approval. It does **not** include `unjudged`/`unmapped` collisions: those must be *named in the answer* — an unevaluated rule presented as no issue tells the user something the engine never checked — but a book with eight behavioral rules would otherwise need eight claims saying nothing was measured, and an answer padded to satisfy a checker is worse than a short honest one. `resolved_existing_breach` is likewise stated but not required to be cited: it is good news, and no missing claim can hide a risk behind it. Its `path` is matched by prefix, so `basis` accepts any `basis.*` citation and `rule_collisions.<id>` accepts `.rule_effect`, `.state` or `.worsens`. Where two paths nest, one claim pays the narrower one only: `basis.price_observations` sits under `basis`, so citing the price day covers the price day and leaves the staleness obligation still to be cited.

**A limitation whose gap is an extent owes the extent too** (#823). Three disclosures say the numbers were measured over less than the whole book — `partial_book`, `unclassified_book`, `etf_not_decomposed` — and each carries its own list of the holdings that explain it (`excluded_holdings`, `unclassified_holdings`, `undecomposed_etfs`). Those holdings ride `must_state` under the `excluded_holding` and `out_of_scope` topics, and `required_coverage` carries a second entry per key, `owes: "out_of_scope"`, pointing at the list. Saying "part of your book carries no classification" and stopping used to clear the gate, so a case could report complete coverage while the reader learned nothing they could act on — which is what "name the excluded holdings wherever a derived percentage appears" has asked for since #515, now enforced rather than remembered. The two topics are not the same fact: an `excluded_holding` is outside the book the numbers were measured over at all, while an `out_of_scope` holding is inside it, carries a real weight, and had its *composition* go unread — which is also why their repairs differ (`--driver-map` for an unclassified name, `--instrument-map` for a fund).

The block is emitted, never stored: it is a pure function of the premise, basis, consequence, collisions and context the evaluation row already freezes, and it takes no part in the `evaluation_id`. A `--resolve` call carries none, because nothing new is being answered there.

Under maintainer QA, delivery of these obligations is proven rather than assumed. The receipt tool's card-free `consider` route ([ux-receipt.md](ux-receipt.md)) captures the challenge emitted on the call's own stdout into a transient comparison file, paired with the exact answer text shown to the user. The tool computes the coverage and verbatim fidelity itself rather than trusting a self-report, and persists only booleans, counts, and a hash — never the challenge or the presented text. If the conversation actually presents a later resolution invitation, the trace may record it once; it is not required for every answer.

## Route-specific synthesis

Apply the global [expression contract](../../../docs/expression-contract.md):
the answer's shape through its §3 mother chapter, voice through the
[output-voice contract](../../../docs/output-voice.md) (V1–V9), disclosure
relevance and placement through D1–D7, provenance labelling through C1–C4.
Those own how this answer speaks; this section owns only the `consider` route's
salience facts and answer slots.

The challenge block is the factual floor. The shape below selects the
decision-relevant facts without turning available ones into standing copy.

<a id="answer-shape"></a>
### Derivation from the pyramid

The answer's shape is [expression contract §3](../../../docs/expression-contract.md)'s
— one sentence on top, an increment-gated middle, the rest of the inventory
behind a single offer, one caliber block at the end — and this file states it
nowhere else (#832; before that, the reader's-question-chain paragraph here was
one of six independent phrasings of the same idea).

Two parameters this route adds, and only these two: **which fact wins the top
sentence** (lead selection, below) and **which blocks the middle floor may
hold** (answer slots, below). Payload order is a dependency order computed for
a machine; reading it aloud is how an answer comes to open on the book's
provenance and reach the recommendation in its last paragraph, which is what
the pyramid exists to prevent.

### Lead selection

Unless a truth-critical disclosure changes how an earlier item can be understood, salience runs:

1. **A funding shortfall** — a negative post-trade cash balance (#778). It outranks everything else when it occurs, because it is not a portfolio consequence at all: it says this trade cannot be done out of the recorded book, so the user is either funding it from somewhere the engine cannot see or selling something to do it. Lead with **that decision**, not with the balance and its weight. Those two numbers are owed and they are support, not the answer — an answer that recites them and then explains what the engine can and cannot see has led with the boundary, which is the exact defect #778 recorded. The boundary is still stated, once, beside the claim it qualifies (D2/D6): the engine sees the recorded book and no other account. Never assume the user has other cash, and never assume they do not.
2. **A user-authored rule collision** — `rule_effect` of `new_breach` or `worsened_existing_breach`. The user wrote that line themselves; this trade crossing it or digging further into it outranks everything else below.
3. **The largest non-obvious portfolio consequence** — weight, concentration or driver overlap, cash. *Non-obvious* is load-bearing: the user already knows they hold the position and that the price fell. What they cannot see from where they sit is what the trade does to the whole book's shape.
4. **The decision-context read** — whether `why_now` looks like a real evidence delta or a price move wearing one, labelled as your judgment. [market-lookup.md](market-lookup.md) governs verifying it.
5. **Routine basis and unchecked boundaries** — include them when they materially qualify or could reverse the recommendation. Do not append a standard tail merely because a field exists.

Special cases: `improved_but_still_over` and `resolved_existing_breach` are improvements to an already-broken line, never framed as a new breach — an improvement that leaves the line crossed still leads with both truths, and one that clears it is worth saying out loud rather than passing in silence. A `partial_book` or missing-FX denominator qualifies every affected percentage in the same sentence — it is the textbook truth-critical qualifier (expression contract D2), because the number means something different without it. A stale or cost-basis book leads only when it makes the apparent consequence unreliable enough to change the decision; otherwise include it only when it materially qualifies the recommendation. With no collision, lead with the largest changed consequence; with no material change, say that the supported dimensions show little change and name only what stays materially unchecked — never convert "not measured" into "no risk".

### Answer slots

Default to one compact recommendation body. There is no word-count target or
mandatory paragraph count — and no obligation to say a fact because it exists.

- **Opening body:** the stance, the reason that decides it, the one or two
  numbers that would flip it, and any *truth-critical* qualifier (D2) without
  which one of those numbers would be misread. Every non-empty `rule_effects`
  entry belongs here too; it is never traded away for brevity.
- **The falsifier:** what would change your mind, attached to any directional
  call. That is where the counter-side lives. A standalone counter-case
  section is what `counter_case: when_material` produced every time, and it
  stops being one.
- **One end block:** sources and as-of, which book, which market session, a
  degraded price, and at most the material gaps that could change this
  recommendation. One line each, non-narrative, collected once.
  `disclosures_display` sentences are material for this block.

Nothing appears on two floors (D6/D7): a qualifier that rode a body sentence is
finished, and a caliber line in the end block is not also narrated above it.
This freedom is never a discount on the factual floor: every `must_state` entry
is still owed, and every `may_state` entry is still there to reach for the
moment the decision turns on it.

### One payload, three renderings

A fictional book, run through the real engine: six US holdings at $100,000 total cost, NVDA at 30%, three semiconductor names summing to 60% `ai_pct`; the premise buys 100 more NVDA at $127.50 — below cost, dated after the book's last row — with `reason` *"NVDA is still my highest-conviction name in the book."* and `why_now` *"It dropped hard this week and the discount feels too good to pass up."* The engine returns: NVDA 30% → 37.9%, `ai_pct` 60% → 64.5%, `oversize_triggered` already true *before* the trade, cash running to −$112,750 with `cash_unreliable`, a 45-day-stale unverified cost-basis record, and `unclassified_book` naming three holdings. Under the pre-#830 block that was seventeen owed facts and five unchecked items; under the current one it is nine owed, six available, one never rendered, and the unchecked list is not a recital at all. No rule collision is on file. All three renderings below are complete against the floor. They are witnesses to the shape, not reference answers or test oracles.

**Complete but flat — every fact true and anchored, no judgment, no shape:**

> The engine computed the following for buying 100 NVDA at $127.50. Basis: transactions as of 2026-06-15, 45 days stale, completeness unverified. NVDA weight before: 30.0%; after: 37.9%. Top-3 concentration after: 64.5%. AI exposure after: 64.5%. Max sector (semiconductors) after: 64.5%. Oversize triggered: true. Concentration triggered: true. Cash balance after: −$112,750. Disclosures: cost basis; cash unreliable; unclassified book (JNJ, PG, KO). Your reason: "NVDA is still my highest-conviction name in the book." Your why-now: "It dropped hard this week and the discount feels too good to pass up." For: you already hold NVDA and it dropped this week. Against: the record is stale; weights are on cost; cash is unreliable; part of the book is unclassified. Not checked: liquidity, valuation, tax, position fit, evidence delta. The decision is yours.

Every number is anchored and every owed fact is present, and it fails on both axes this section governs. On judgment: the for-side states what the user already knows, the against-side is the disclosure list wearing a new heading and never touches the for-side, and nothing says what the decision actually trades off. On expression: there is no body/block split at all — the limitations are inline sentences in the same list as the numbers, which is D1's wall of per-number interruptions in its purest form.

**Decision-first, whitelist era — same payload, every owed fact discharged:**

> Do not add NVDA at this size. Priced on cost rather than on current market value, NVDA goes from 30% to about 38% and semiconductors as a group from 60% to about 65% — and that 65% is measured over the classified part of the book, which excludes JNJ, PG and KO. The position-size line was already triggered before this trade, so this widens an existing breach rather than creating one.
>
> The material case the other way is that these are cost weights: if this week's drop hit your semiconductor names hardest, market-value concentration could be lower than the record shows. But your own words — "it dropped hard this week and the discount feels too good to pass up" — describe a price move, not new business evidence. That is my read, not an engine fact.
>
> The recorded book is 45 days old and has never been reconciled against a broker view. Its −$112,750 cash line is an unanchored running sum, not evidence of a real overdraft. Nothing has been executed.

The factual floor is identical — same numbers, disclosures, and verbatim quotes. The unchecked list is filtered by materiality. One lead is chosen, and any counter-case directly attacks its support rather than satisfying symmetry.

What changed on expression, and which rule decides each: the cost-versus-market basis and the incomplete classification denominator stay in the body, because they are the *unit* and the *denominator* of the numbers in that sentence (D2) — without them those percentages refer to something else. Staleness and cash reliability are collected because both materially qualify confidence in the recommendation. No marker, tail position, or numeric cap decides that placement (D1/D3/D5), and nothing appears twice (D6).

**Same payload, after the deletion (#830):**

> Do not add NVDA at this size — the position-size line was already over before this trade, so this widens a breach rather than opening one. On cost rather than market value it goes from 30% to about 38%.
>
> What would change my mind: market-value weights. These are cost weights, and if this week's drop hit your semiconductor names hardest, the real concentration could be lower than the record shows. Your own words — "it dropped hard this week and the discount feels too good to pass up" — describe a price move, not new business evidence; that is my read, not an engine fact. Hand me current closes and I will re-run it.
>
> (Book 2026-06-15, 45 days old, never reconciled; weights on cost; the cash line is an unanchored running sum, not a real overdraft; sector shares exclude JNJ, PG and KO. Nothing has been executed.)

Three paragraphs where the second rendering has three and the first has one wall, and the difference is not compression. The second rendering states the AI/sector concentration, the cash number, and the classification denominator in body prose because the whitelist owed all three; here the concentration family and the cash balance are `may_state`, so the answer reaches for them only where they carry the argument — the sector figure is gone because nothing about the recommendation turns on it, and the cash line survives only as the end-block caveat that stops a −$112,750 from reading as a real overdraft. The counter-case became the falsifier, which is the same content with a decision attached to it. Every caliber line moved to the end block and appears once. `basis.state_version` appears nowhere, in any of the three.

The floor did not move: the same rule effect, the same verbatim quote, the same disclosures. Ask *"what were the other numbers?"* and every one of them is still there.

## The recommendation case

The engine states the consequence and rule collisions. Recommend what to do, then support it from that output and relevant sourced evidence. Include a counter-case only when it could change the action.

Every claim you add carries its own label: state your record says (drawn straight from `before`/`after`/`delta`/`rule_collisions`), a public fact (something you looked up, sourced), or your own judgment. Do not blend them into one unlabeled sentence. When and how to look something up at all — the standing position packet, the event-lookup triggers, the neutral query, the stop discipline — is [market-lookup.md](market-lookup.md)'s contract.

`consider` measures weight, concentration, driver overlap, cash, and rule collisions. Liquidity, valuation, tax consequences, and position fit are available unchecked dimensions, not mandatory boilerplate. Name the ones that bear on the recommendation or prevent a false impression of coverage.

You may optionally structure this case with `--agent-case`, a path to a JSON file, checked by `engine/answer_provenance.py::validate_agent_case` (#414) before anything is stored or returned:

```json
{
  "recommendation": {
    "claim": "Do not add at this size.", "provenance": "agent_judgment"
  },
  "support": [
    {"claim": "This grows NVDA to 64% of the book.", "provenance": "engine_fact", "anchor": "consequence.after.max_pct"},
    {"claim": "This is priced on cost, not a live market value, so the weight above may be off.", "provenance": "engine_fact", "anchor": "consequence.disclosures.0"},
    {"claim": "The record is several days stale.", "provenance": "engine_fact", "anchor": "basis.stale_days"},
    {"claim": "The stock trades at a much higher earnings multiple than when you first bought it.", "provenance": "public_fact", "source": "Market data provider", "as_of": "2026-07-20"}
  ],
  "counter_case": [
    {"claim": "You have historically held through drawdowns of this size in this name without selling.", "provenance": "agent_judgment"}
  ]
}
```

Structured claims only, never a free prose blob. New submissions require one `recommendation` (always `agent_judgment`) and non-empty `support`; `counter_case` is optional. The legacy `for`/`against` shape remains readable so stored history replays. Claim `provenance` is one of `engine_fact`, `public_fact`, or `agent_judgment`. This flag is optional; a plain `--premise` call is complete.

**A claim's provenance decides what else it must carry**, per `schemas/answer-provenance.schema.json`:

- `engine_fact` must carry `anchor`: a dot-separated path into exactly this call's own frozen `basis`, `consequence`, or `rule_collisions` (`rule_collisions` is addressed by `rule_id`, e.g. `rule_collisions.rule-1.worsens`, never by list position). The path must resolve to one fact, never a container, and copy it verbatim from the JSON `consider` already handed you rather than retyping it by hand. When the resolved fact is a number, quote it in the claim's own prose within half a display unit, at whichever scale the record itself uses — a fraction-shaped value (weights, `max_pct`, `ai_pct`, …) is written ×100 as a percent, everything else (`stale_days`, share counts, dollar balances) as-is. A claim anchored at a `rule_collisions[...].rule_effect`, `.state` or `.worsens` field must also carry its own `rule_effect` string, matching the frozen one exactly, whenever that frozen effect describes a real transition (`new_breach`, `worsened_existing_breach`, `improved_but_still_over`, `resolved_existing_breach`, `unchanged_existing_breach`). Nothing offline can read your prose for direction, so the transition rides beside it and is checked exactly; a case built on the wrong one is refused rather than stored. `rule_effect` is forbidden on every other engine_fact claim, and `worsens` is the pre-#579 two-way version of the same declaration — still required on a stored row that predates `rule_effect`, optional and still checked beside it, forbidden anywhere else. See [Reading a rule collision](#reading-a-rule-collision) above.
- `public_fact` must carry `source` (the named external source) and `as_of` (an ISO date). It must never restate what the user themselves said through `--decision-context`'s `reason`/`why_now` — copying the user's own words and relabelling them as an outside fact is refused, not stored.
- `agent_judgment` carries nothing beyond `claim` and `provenance`.

**Everything on `required_coverage` must be covered, or the whole case is refused.** That list arrives in the same response ([What the answer owes](#what-the-answer-owes) above) — you do not have to derive it. For each entry, at least one `engine_fact` claim must anchor at or under its `path`: every key in the frozen `consequence.disclosures`, the `basis` whenever it is stale (`stale_days > 0`) or not a declared-complete snapshot, and every rule still over its line after this trade. This is why the example above anchors `consequence.disclosures.0` and `basis.stale_days` even though neither reads as dramatic on its own — leaving one out is refused the same as a wrong number, and silence about a rule the trade breaks reads to the user as a rule that held.

**A claim may not narrate the engine's own vocabulary.** A snake_case payload token — `already_over`, `improved_but_still_over`, `cost_basis`, `partial_book`, `unusable_shares`, … — is an internal identifier, and a case that puts one in front of the user is refused before it is stored. Say what the token *means for this decision* instead: "computed on cost rather than on current prices", not "cost_basis". This is the same rule the question surface already enforces, applied to the answer surface it was missing from.

A rejected case is refused before it is stored or shown: the caller gets the validator's own error, naming the exact claim and the exact rule it failed, and `consider` persists and returns nothing for that attempt. Fix the claim and resend, or drop `--agent-case` and present the case in plain prose instead.

## Recording what the user did

Persistent calls are recorded in a local, append-only log. Candidate fan-out uses `--ephemeral`, which computes against the existing recorded book and writes no evaluation; rerun only the selected or live candidate without the flag.

**When a persistent call happens, say once that the record is a consideration and not an execution.** This is one of the answer's keeps, and it is owed exactly when a canonical write occurred — never on an `--ephemeral` fan-out, which recorded nothing to be confused about. The user cannot see the difference between "stored what you were weighing" and "placed the order" from where they sit, and the difference is real money.

Once the user has decided, tell the engine with `--resolve`:

```bash
python3 engine/review.py consider --resolve <evaluation_id> --decision acted
```

`--decision` is one of `acted`, `declined`, or `modified`. `--resolve` takes no premise and no other consideration flags — the evaluation it names already carries all of that, frozen from when it was asked, including any decision context. A resolution never rewrites the original record; it appends a new entry that supersedes the old by id, carrying the frozen premise, basis, consequence and the user's own words forward unchanged, so what the engine actually said at the time — and what the user said it was for — is never lost.

There is no obligation to call `--resolve`, and no review step depends on it. Do it when it is natural in the conversation, not as a checklist item.

## What a later review does with an unresolved one

An evaluation left at `decision: "open"` does not go silent. The next `prepare` reconciles it against the transaction record and carries the result in the Review Plan's `evaluation_reconciliation` — a `matched` entry names the date and quantity of a trade found for that ticker and side between the evaluation's `created` day and the review's own close; `unmatched` means none was found. This is a fact about the record, never a claim about cause: `matched` is evidence a qualifying trade happened, not evidence the user made it *because of* the evaluation, and a review never writes `decision` — that stays the user's own word, set only through `--resolve` above. Raise a surfaced evaluation the same way any other supplied fact earns a turn: judge whether it is the relevant thing to say in this scene, not an automatic prompt.

## What a later consideration does with a resolved one (#609)

The mirror image of the section above. When the user asks about a ticker they have already consulted you on **and settled**, the response may carry one optional `prior_decision` beside the evaluation:

```json
{
  "evaluation_id": "eval-...",
  "ticker": "NVDA",
  "side": "buy",
  "reason": "It is still my highest-conviction name and the build-out has room to run.",
  "why_now": "Their main supplier raised capacity guidance this morning.",
  "evidence_refs": ["Supplier capacity guidance, this morning"],
  "decision": "declined",
  "decided_on": "2026-03-11"
}
```

At most one reaches you, and only when all of these hold: the same ticker, a different evaluation than this one, a complete stored `reason` and `why_now`, a resolved `decision`, and a canonical `decided_on`. The newest eligible same-side prior wins; only when there is none does the newest eligible opposite-side one take its place. An `open` evaluation is unresolved and never appears here — that one goes through the reconciliation above instead. When nothing is eligible the field is simply absent, which is why there is no empty case to write around.

It is a read projection of another stored row, not a new record. It changes no number, no `rule_effect`, no `evaluation_id`, and nothing is written because of it.

**Use it only when it changes the current lead judgment, evidence requirement, process action, or a decision-changing question.** Memory that does not change the answer stays silent: there is no history paragraph to write, and "you have asked about this before" is not worth a sentence on its own. What is worth one is a question their own record has earned — *last time the reason was the price move and you passed; what is different now besides the price?*

Two things it is not. `decision: "acted"` is what the user **reported** doing about that consultation, exactly as `--resolve` records it — never that a trade executed, filled, or reached the ledger. And any comparison you draw from it — the same reason, a genuinely different one, a rationalization — is your judgment right now, offered as yours and labelled that way. The engine stores no pattern, no motive, and no verdict about the user, and neither should the answer imply one.
