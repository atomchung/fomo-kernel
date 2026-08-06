# Freeform informational answers

The user does not only meet this product through `prepare → preview →
finalize`. They ask ad hoc questions mid-conversation — "what's my portfolio
worth right now," "how much cash do I have," "what if I add to this" — and
`consider` itself answers in plain conversation, not a card
(`trade-consequence.md`). Owner ruling, 2026-07-29 (#543): unless the user
explicitly asks for a chart, every one of these gets a quick, direct answer.

## Why this exists

A real dogfood session asked one ad hoc question after a review had already
finalized, entirely outside the lifecycle. The answer took roughly 34 LLM
turns: recomputing holdings from the trade CSV by hand, cross-referencing two
unrelated historical issues to explain an apparent inconsistency in the
numbers, and producing a full chart artifact through a rendering tool, before
finally summarizing in text. Nothing asked for any of that — not `SKILL.md`,
not a flow, not the user. The review card's own P&L visual is a deliberately
minimal 32px sparkline with no axes (`card-delivery.md`) — a cheap, fast
precedent already existed; the freeform surface simply had no rule pointing
at it.

## Rule 1 — the default is a quick, direct answer

An informational question asked outside the card gets a direct answer in
text. No chart, no rendered artifact, no multi-tool production — a claude.ai
Artifact, an HTML file, an image, reconstructing a book from a CSV by hand,
cross-referencing unrelated issues or sessions, or generating any supporting
file nobody asked for. Depth is available on request; it is never the
opening move. Answer, then stop — the next turn is the user's, not another
chance to keep producing.

This covers any ad hoc question, and explicitly a `consider` call:
`consider`'s answer is plain conversation by design (`trade-consequence.md`),
so it is a freeform surface like any other, not a card-lifecycle exemption
from this rule.

### The one exception: recovering a price is completing the input

Owner ruling, 2026-07-30 (#629). The engine keeps one retrieval source. When
it fails, **the agent recovers the prices** — a search may find the
publisher's page, the close is read off that page and never off a
search-result snippet — and hands them back through the existing `--prices`
envelope. That is a second tool used on an ad hoc question, so this rule has
to say plainly that it is allowed: **recovering a price the engine could not
retrieve is completing the input, not production.** Nothing else about the
answer changes; it is still brief, still text.

It is not a general loosening. It licenses no chart, artifact, or other
multi-tool work, on this call or any other, and it applies only when
`consider` actually returned a `price_feed` recovery kit naming missing closes
and/or currency conversion.

**Why the carve-out is worth its cost.** `consider` exists to answer what a
trade does to the user's concentration. Computed without current prices, every
weight is a share of *cost*: on this repository's own momentum fixture the
largest position reads more than thirteen points higher on cost than at
market, and the second and third positions by size swap places. The user is
not lied to — the answer discloses `cost_basis` — but knowing the basis is not
knowing that the ranking flipped. A forward-looking decision measured on cost
describes a book that no longer exists.

**The bound.** The task is **transcription, not analysis**: the output is an
envelope, and the result is mechanically checked downstream, so this is
bounded work rather than judgment.

- **Count ceiling:** the instruments `price_feed.request.tickers` names and
  nothing else — no benchmark, no index, no integrity exclusion, and no
  instrument the book does not hold. **At most twenty instruments**, one
  attempt each. The engine already scopes that manifest to the held book plus
  the premise's own ticker. When `tickers` is empty and
  `request.currencies` is non-empty, look up only those FX rates.
- **Timeout:** an instrument whose publisher page does not resolve is left out
  rather than retried. Supply whatever you found — partial coverage is
  accepted, and the answer names what it could not value.
- **Delegation:** the work is bounded and parallelizable and **may be
  delegated to whatever faster tier the host has**. Which tier, and whether the
  host has one at all, is the host's own configuration and never this
  product's.
- **Degradation:** when the sources genuinely publish nothing, say so with
  `consider --prices-unavailable '<the sources you checked>'`. The question is
  then **refused rather than answered on cost basis**. Never invent, never
  interpolate, never fall back to the cost-basis figure.

That refusal is the opposite of what the review-card lane does with the same
declaration, and both are right. [price-feed.md](price-feed.md), "Two lanes,
two opposite rules", is the one statement of why.

## Rule 2 — a chart is named in advance, never improvised

The expensive part was never "a picture exists." It is inventing a new
visual shape on the spot, at whatever cost the moment suggests, every time. A
chart is legitimate only when it matches a name in the set below. Anything
else — a new layout, a chart-library call, a one-off rendering-tool
invocation — is out of contract for a freeform answer, however reasonable it
seems in the moment.

### The named set

Owner ruling, 2026-07-29: two entries. Each was chosen once and is reused
verbatim, never recomposed per question — the same discipline the review
card's own sparkline already follows.

**Review card.** Trigger: the user asks, in freeform conversation, to see
their review card — the current one, or a specific past review's. This is
not a new rendering: it is the same engine-rendered artifact
`card-delivery.md` already governs, delivered through that existing contract
rather than a freshly composed one. The card's own P&L sparkline stays
exactly as scoped before — part of the card's own rendering, not a
detachable chart reachable on its own — and privacy still defaults to
`card-private.*` per AGENTS.md invariant 4 / AGENTS.md invariant 4: asking for the
card in freeform conversation does not loosen that default, and only
`card-public.md` is share-safe, on request.

**Positions view.** Trigger: the user asks, in freeform conversation, to see
their current holdings or positions. Shape, revised by owner ruling
2026-07-30 (#561) from the original four-column table into the richer,
already-demoed one: one row per held ticker — ticker, shares, avg cost,
current value, $ P&L, and the sizing / averaging-down / exit-discipline /
hold-consistency diagnosis tags — sorted by size (largest |$ P&L impact|
first), exactly the "Per-position diagnosis" section README.md's "What it
looks like" demonstrates. A ticker held below the meaningful-position floor
(#172's residual filter — dust too small to diagnose, such as a dividend
odd lot) is still named with its shares/cost/value, just without a
diagnosis, matching the demo's own "small lots not nitpicked" framing —
never silently dropped from the book. No bars, no color coding, no
sparkline, no second panel — being in this named set never requires a
picture, and the honest shape here is a compact text table and nothing
else. Cash and any other disclosure (a stale price, an unreliable cash
balance, a partial book) still ride Rule 1's existing disclosure boundary
rather than a rule this entry restates.

Every field in the Positions view must come from an engine-computed
current-book snapshot obtained through `engine/review.py` — the same
numbers-from-engine and CLI-only boundary (SKILL.md rules 1 and 2; AGENTS.md
invariants 2 and 1) every other number in this product already obeys, never
a value the agent recomputes from a CSV, and never one read by importing an
engine module directly. The dedicated read-only outlet is
`engine/review.py positions` (#561): no CSV, no premise, no supplied
snapshot — it reconstructs the book from `<root>/ledger.jsonl` alone, asks
no question, creates no session, and appends nothing to
`trade_evaluations.jsonl` or any other durable file, unlike a `consider`
call answering the same question would. Read its JSON output (`positions`,
`residual_positions`, and their `tags`/`impact` fields) rather than
re-deriving any of it, the same discipline this file already applies to
every other engine number.

Read its `sizing` block too, and relay what it says (#737). It carries the
engine's own verdict on the weights it just emitted: whether a weight could
be computed at all, the `aggregate_currency` those weights are measured in,
and — when one could not — every holding that has none, each with the
engine's own reason, plus the missing prices or FX rates that explain it.
Two things follow. A mixed-currency book's `value` is stated in each
holding's *native* currency while its `weight` is measured in the aggregate,
so presenting the two as one basis misreads the book. And a null `weight` is
never to be shown, or silently skipped, as though it were simply a small
position: say that the weight is unavailable and why, since the whole point
of the sizing tags is a comparison that has not been made. A missing FX rate
is repairable the same way `references/price-feed.md` describes — transcribe
the rate and ask again — never by inventing one or treating it as identity.

Adding a third entry means writing its name, its trigger, and its exact
shape into this section — the same "chosen once, reused" discipline above —
not composing a one-off visualization inline and calling it the obvious
choice.

## Rule 3 — the set bounds the agent, not the user

Rules 1 and 2 constrain what the agent decides to produce on its own
initiative — an unprompted chart, artifact, or multi-tool detour is what the
34-turn failure actually was. Neither rule is a ceiling on what the user may
explicitly ask for. When the user names something outside the two entries
above — a different chart, a different table, more detail than Rule 1's
default — meet that request instead of declining it on this file's
authority. Every other non-negotiable rule still applies in full to however
that request gets answered: numbers still come only from the engine
(AGENTS.md invariant 2 / AGENTS.md invariant 2), trade data still stays local
(AGENTS.md invariant 4 / AGENTS.md invariant 4), and a market price is still never
invented (AGENTS.md invariant 2; AGENTS.md states the same prohibition in its
Workflow section rather than as a numbered boundary). This file bounds what
the agent reaches for unasked; it was never written to tell a user no.

## What this does not cover

This is an effort/scope ceiling: how much production an answer costs, never
which facts it must state. Whether a freeform answer's disclosures (a stale
price, a partial book, an unresolved evidence delta) are as rigorously
enforced as the review card's `honesty_ledger` is a separate, open question
(#525) — a short, text-only answer that omits a material disclosure is not
made honest by being short. Read `trade-consequence.md`'s disclosure table
for what a `consider` answer already owes on that axis; this file does not
change it.

One surface has since been closed on that axis and the rest have not.
`consider` now returns a `challenge` block computing what its own answer
owes — the facts, the user's exact words, the rules the trade collides
with, and what nobody checked (`trade-consequence.md`, "What the answer
owes"). That is the `honesty_ledger` treatment applied to one freeform
route, not to freeform answers in general: every other ad hoc question in
this file's opening paragraph still has no equivalent, which is precisely
what #525 remains open about.
