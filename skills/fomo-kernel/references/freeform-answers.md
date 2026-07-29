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

**Empty.** No chart is defined for the freeform surface today. The only
chart the product has is the review card's own P&L sparkline, scoped to the
card's own delivery contract (`card-delivery.md`) — it is not reachable from,
and not a precedent for, a freeform answer. A question that seems to call for
a picture still gets numbers in text until an owner-approved entry is added
here, by name.

Adding one means writing its name, its trigger, and its exact shape into this
section — the same "chosen once, reused" discipline the sparkline already
follows — not composing a one-off visualization inline and calling it the
obvious choice.

## What this does not cover

This is an effort/scope ceiling: how much production an answer costs, never
which facts it must state. Whether a freeform answer's disclosures (a stale
price, a partial book, an unresolved evidence delta) are as rigorously
enforced as the review card's `honesty_ledger` is a separate, open question
(#525) — a short, text-only answer that omits a material disclosure is not
made honest by being short. Read `trade-consequence.md`'s disclosure table
for what a `consider` answer already owes on that axis; this file does not
change it.
