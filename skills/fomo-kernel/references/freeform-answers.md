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
`card-private.*` per SKILL.md rule 5 / AGENTS.md boundary 5: asking for the
card in freeform conversation does not loosen that default, and only
`card-public.md` is share-safe, on request.

**Positions view.** Trigger: the user asks, in freeform conversation, to see
their current holdings or positions. Shape, chosen once: a plain table, one
row per held ticker, columns `Ticker | Shares | Avg cost | Weight of book`,
sorted by weight descending (largest position first). No bars, no color
coding, no sparkline, no second panel — being in this named set never
requires a picture, and the honest shape here is a compact text table and
nothing else. Cash and any other disclosure (a stale price, an unreliable
cash balance, a partial book) still ride Rule 1's existing disclosure
boundary rather than a rule this entry restates.

Every field in the Positions view must come from an engine-computed
current-book snapshot obtained through `engine/review.py` — the same
numbers-from-engine and CLI-only boundary (SKILL.md rules 1 and 2; AGENTS.md
boundaries 1 and 7) every other number in this product already obeys, never
a value the agent recomputes from a CSV, and never one read by importing an
engine module directly. No dedicated read-only
`engine/review.py` outlet returns that snapshot on its own today: `consider`
computes the equivalent snapshot as `before` context for a hypothetical
trade, but only together with a real premise, and every call durably records
a `trade_evaluations.jsonl` row (`trade-consequence.md`) — a passive lookup
dressed as a considered trade would be a misuse of that surface, not a
shortcut through it. `refresh`'s read-only mode computes a comparable
snapshot too, but only as one side of a diff against a newly supplied broker
view, not as a way to reprint the book already recorded. Until a dedicated
outlet exists, say that plainly instead of forcing the Positions view
through either path — the same fail-closed posture SKILL.md rule 4 already
takes for a missing price. Naming the shape now is what lets a future outlet
fill it in without a shape invented inline at that point either.

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
(SKILL.md rule 1 / AGENTS.md boundary 1), trade data still stays local
(SKILL.md rule 5 / AGENTS.md boundary 5), and a market price is still never
invented (SKILL.md rule 4; AGENTS.md states the same prohibition in its
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
