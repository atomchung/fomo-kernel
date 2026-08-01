# Framing a decision when there is no recorded book

`review.py consider` answers what a trade does to the user's own book, and it fails closed when there is no book to answer against. That refusal is correct — it protects the arithmetic — but it is not the end of the conversation. A user who has recorded nothing still arrives with a live decision, and refusing is not what earns their transaction history. Guidance is.

This file is the contract for that case: the user is deciding on one trade, has no transaction export and no holdings snapshot, and wants the decision framed anyway. It runs entirely host-side. There is no engine call, no session, no route value, and nothing is written to disk — the answer lives in the conversation and ends with it.

Confidence rises with a book; it does not fall to zero without one. This route exists to be useful now and to make the next piece of evidence worth handing over, not to stand in for [trade-consequence.md](trade-consequence.md)'s computed answer.

## Recorded book, but no safe consequence

This is not the no-book route when `consider` has a recorded book but cannot
safely compute its consequence. The route-specific refusal contract in
[trade-consequence.md](trade-consequence.md) owns that result: if frozen facts
support a tension, use them; if they do not, use its stable two-sentence
unavailable result. Do not ask the three no-book questions again or treat the
recorded book as absent. The user already supplied the premise, reason and
why-now; preserve them and name only the exact missing system fact or next
check.

## Voice authority

Apply the global [output-voice contract](../../../docs/output-voice.md). It
owns universal output semantics; this reference owns the no-book facts,
questions, and route order below.

## What the answer is

One bounded framing carrying four things:

- the strongest case for the decision, and the strongest case against;
- the decision's key tension — one, leading;
- the user's own stated exit condition, in their words;
- whichever portfolio fact this decision actually turns on, as a question the user can answer themselves.

**It is never a thin `TradeEvaluation`.** No weight, no concentration figure, no cash consequence, no rule collision, no post-trade percentage — not as a zero, not as a placeholder, not as an empty section. A number that would have to be computed from a book is absent, and its absence is not narrated as a field.

## The three questions

Ask at most three, and fewer when the user's opening message already answered one. Never re-ask a known answer. Offer concrete options plus `not sure / depends`; reserve free text for the user's own reason or exit condition. The design principle behind every branch below: **a different answer does not change the wording, it changes which challenge the answer is about.** A question whose answers produce the same visible output is a defect, not a reflection exercise.

### Q1 — how important is this position, and what size is intended

| Answer | The challenge becomes |
|---|---|
| Core holding, intended to be large | **Single point of failure.** This position's outcome largely decides the result. Counter-case: if the premise is wrong, how long before you find out? |
| Toe in the water, small | **What is actually being bought.** A small position's common failure is not losing money, it is being bought and then never judged again. Counter-case: under what condition would you size it up — and if there is none, is this a position or an insurance payment against missing out? |
| Not sure | Fork to something answerable: **would being wrong here cost you sleep?** Easier than a percentage, and it maps directly onto size. |

### Q2 — what changed today

| Answer | The challenge becomes |
|---|---|
| A new public fact | Verify it through [market-lookup.md](market-lookup.md): the claim, its timing, and the strongest narrow counter-reading. Axis: **is this actually new.** |
| Only the price moved | **Chasing or waiting.** Counter-case: which direction of the move made you want in? Down is averaging into weakness; up is chasing strength. |
| Someone recommended it | **Can you restate the premise without them?** Counter-case: if they change their mind tomorrow, what do you do? |
| Nothing in particular | The most valuable answer, not a missing one. Axis: **why today** — did the position get better, or did you get impatient? |

### Q3 — what would make you exit

| Answer | The challenge becomes |
|---|---|
| A checkable condition — a price, a reported figure, a date | **Will it actually trigger.** Counter-case: if it never arrives, how long do you hold; when it does, will you really sell? |
| "If the thesis breaks," with no stated break condition | **The premise is currently unfalsifiable.** The strongest single observation on this route, and it needs no book at all. |
| A dimension with no observable — "when demand peaks", "if the story changes" | More specific than an unfalsifiable premise, less checkable than a date. Ask what evidence would count as that dimension moving, and name the nearest instance the user has already dismissed. |
| Has not thought about it | Do not force it. One question: **how far down before you start doubting yourself?** |

## Combinations that change the answer qualitatively

Reading each question alone misses these, and they are where the route earns its place.

- **Only the price moved, and no falsifiable condition** — the one case that deserves a blunt statement: with no new fact and no break condition, this decision cannot be judged afterwards. Zero book facts required to say it.
- **A reason and an exit that name the same variable** — the exit is not an independent test; it will be resolved by the same disputed reading the user is already committed to. Ask what evidence would count, given the instance they have already dismissed.
- **Core or large, with no stated ending** — position size and stated conviction are mismatched.
- **A verified new fact and a checkable condition** — the healthy combination, and the instruction is **do not manufacture a problem.** Confirm one thing: are the evidence and the exit condition connected to each other? For many users they are entirely independent.

## A declared size is an input, never a record

With no recorded book, intended size is a user-declared target or an importance signal — never a computed weight. It is used to pick Q1's branch and to aim the closing question, and that is the whole of its job. It is stored nowhere, and it is never described with the vocabulary reserved for engine facts. When a book later arrives, the computed weight is simply the answer; the declaration is not shown beside it, where the user could walk away remembering the wrong number.

## What the answer owes, and the shape it owes it in

A limitation must reach the user in a form they can act on. Prefer the question:

> Are your three largest positions already the same bet?

over the narration:

> I did not check your concentration.

Both are honest; only the first gives the user something to answer. The discriminator: **does this sentence hand the user something to decide, or does it only report what the product lacks?** The second is filler, and filler is what this product has already been caught producing around a perfectly good fact.

Three rules follow, and the third is the one that keeps the first two honest:

1. Pick the one portfolio fact this decision actually turns on and ask about it. Weight, concentration, cash and rule collisions are not a checklist to recite; salience selects, exactly as it does for engine facts elsewhere.
2. A limitation that cannot be turned into a question is stated plainly and once — "I have secondary reporting, not the filing" — attached to the claim it qualifies, never grouped into a disclosure block.
3. A material limitation may never simply disappear. Dropping the narration is a change of shape, not permission to leave a decision-relevant gap unsaid.

## Earning the next piece of evidence

The invitation names the question the evidence would answer, never the data being requested. It is generic because it is keyed on the small closed set of answers a book can buy that no book can, not on which question the user answered.

1. **What fraction this actually becomes.** Replaces a declared size with a computed weight.
2. **Whether this is a bet already held.** Driver overlap against the existing positions — the one most users cannot answer from memory.
3. **Whether it can be paid for.** The cash consequence.
4. **Whether it breaks a rule already set.** Collision with the user's own recorded rules.
5. **What happened the last time this reason was given.** The only one a positions snapshot cannot buy — it needs transaction history.

At most one per answer, chosen by salience — whichever of the five the user's own answers made central to this decision. Two invitations in one answer is the disclosure-dump failure the shape rules above already forbid. When none of the five is decision-central, the honest move is to say nothing; a manufactured invitation is the same defect as a manufactured disclosure.

Placement governs a second, distinct question — not which, but where and how many: **one invitation per answer, and it goes last.** It is the closing move of the answer — appended once the framing is complete, never interleaved with the case for and against, and never attached to an individual claim. One round of the conversation, one question answered, at most one invitation, appended at the end. A useful answer is never withheld until data arrives, and an invitation placed mid-answer reads as exactly that precondition on the sentence it interrupts; placed last, after the answer is already complete, it cannot read that way.

A holdings view buys the first four; transaction history alone buys the fifth, and nothing else does — name the evidence that would settle the question, never data in general. The wording is illustrative, not a template:

> You said you want this to be a core holding. Hand me a holdings screenshot and I can tell you what it actually becomes — and whether your top three are already the same bet.

Not "provide your portfolio for a more accurate analysis".

## Red lines, unchanged and hardest to hold here

- **No price target, no forecast, no buy-or-sell verdict.** The discriminator is whether the sentence states a sourced fact or issues a price or direction verdict. An analyst target found during lookup is a verdict and does not enter the answer.
- **A missing number is never replaced by a general rule.** A single-position cap is a fact measured against a computed weight and overridable by the user's own `set-cap`. Stated with no book, the identical sentence becomes fortune telling — the user may already be far past it, and nothing here knows that. The same bar rules out prescribing staged entry, a size ceiling, or a leverage rule.
- **"So should I buy it?"** happens every time, and the answer is that the decision is theirs, followed by the strongest case on each side and the one observation their own answers earned. When there are no numbers to state, the cheapest way to sound useful is to state an opinion — which is precisely when this product has the least standing to.
- **Brevity is not a licence to drop a fact.** It bounds what the answer produces, never what it owes, and the shape rules above are how both hold at once.

## Nothing is persisted

No answer, chosen principle, or working rule from this route is written to durable state. If saving a principle is ever justified, its owner is the distillation contract, not a file created beside it — and a condition only becomes checkable through [condition-slots.md](condition-slots.md).
