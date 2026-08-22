# Framing a decision when there is no recorded book

## The exemplar

One whole no-book answer. Every book-derived claim is refused and the answer
still lands a stance, names the gap that decides it, and leaves the user a
falsifier they could write down themselves. Read it before writing one.

It is copied verbatim from `tests/agent/expression-witnesses.json`, scene
`no_book_single_name` — the corpus that
[expression-contract.md](../../../docs/expression-contract.md) §3.5 makes the
binding statement of this shape. Every issuer in it is invented, and
`tests/test_expression_contract.py` fails if the two copies disagree.

```exemplar no_book_single_name
WDGT 這家公司的證據支持買，但「現在進場」我不背書——缺的是估值，不是基本面。上季營收 +24%、EPS 超預期、同日上修全年（公司新聞稿，2026-07-24），這是硬的；但這些已公開三週，而我查不到現在的估值倍數，「好消息是否已在價格裡」這一半我答不了。

$5,000 試水溫，真正的洞在出場：「故事變了就賣」跟你的進場理由是同一個變數，等於沒有獨立的認錯線。可檢查的替代就用它自己簽的支票：全年營收財測或毛利率轉向，擇一寫下來，就可以進場。

（價 8/14 收盤 $188.20；你未提供持倉，部位佔比與重疊無法評）
```

`review.py consider` answers what a trade does to the user's own book, and it fails closed when there is no book to answer against. That refusal is correct — it protects the arithmetic — but it is not the end of the conversation. A user who has recorded nothing still arrives with a live decision, and refusing is not what earns their transaction history. Guidance is.

This file is the contract for decisions made without a recorded book. The user
may bring one security, several candidates, constraints, or an explicit request
for discovery. It runs entirely host-side unless a later selected candidate is
evaluated against a recorded book. There is no session or durable write from
this framing itself — the answer lives in the conversation and ends with it.

Confidence rises with a book; it does not fall to zero without one. This route exists to be useful now and to make the next piece of evidence worth handing over, not to stand in for [trade-consequence.md](trade-consequence.md)'s computed answer.

## Recorded book, but no safe consequence

This is not the no-book route when `consider` has a recorded book but cannot
safely compute its consequence. The route-specific refusal contract in
[trade-consequence.md](trade-consequence.md) owns any portfolio claim. Frozen
`usable_facts` may support that claim; sourced public facts and clearly labelled
judgment may still support a recommendation that does not pretend the missing
portfolio consequence was computed. Do not treat the recorded book as absent
or ask the user to repeat context already supplied. Name a missing input only
when it could change the recommendation or unlock the portfolio claim.

## Voice and expression authority

Apply the global [expression contract](../../../docs/expression-contract.md):
the answer's shape through its §3 mother chapter, voice through the
[output-voice contract](../../../docs/output-voice.md) (V1–V9), disclosure
relevance and placement through D1–D7, provenance labelling through C1–C4.
They own universal output semantics; this reference owns the no-book facts,
questions, and route order below.

**This route's derivation from §3, and nothing more** (#832): with no book,
the pyramid's top sentence is a *research-backed baseline* rather than a
computed consequence, and the strategy-class map below is a middle-floor block
set. Both are stated once, in "Research-aware strategy framing". Everything
else about the shape — that the top is one sentence, that every block must add
a new decision-relevant fact, that the rest of the inventory waits behind one
offer — is §3's, and this file no longer says it a second time.

## What the answer is

A useful framing may carry:

- a direct recommendation at the confidence the available evidence supports;
- the support for it, and a counter-case only when material;
- the decision's key tension when one remains;
- the user's own stated exit condition, in their words;
- whichever portfolio fact this decision actually turns on, as a question the user can answer themselves.

**It is never a thin `TradeEvaluation`.** No weight, no concentration figure, no cash consequence, no rule collision, no post-trade percentage — not as a zero, not as a placeholder, not as an empty section. A number that would have to be computed from a book is absent, and its absence is not narrated as a field.

## Research-aware strategy framing

When a user asks for a strategy before they have a book, do not make them
invent an exit philosophy before supplying the bounded value available now.
This route's block order — the parameter it adds to the pyramid, whose top
floor is already the answer:

```text
research-backed baseline          (the top sentence, when no book exists)
→ applicable strategy-class map   (middle floor)
→ any question whose answer could change the recommendation
```

The baseline is the narrowly scoped authority in
[research-priors.md](research-priors.md). Apply only the prior whose
applicable decision class fits the user's own description; state any material
exception beside that baseline. A prior may support a strategy recommendation,
but never invents a fund choice, allocation, suitability finding, or forecast.

For genuinely long-horizon risk capital with no supplied concentration edge,
the available baseline is broad diversification and lower discretionary
turnover. State the important limitation with it: a long label does not make
equity safe, and an ETF or index wrapper alone does not prove genuinely broad
exposure. Do not ask about liquidity or a stop before the user sees the
baseline and map.

Then provide only the strategy classes that fit the user moment; this is a
bounded map, not a fixed questionnaire:

- **Long-horizon broad-market policy.** Governance follows goal/liquidity
  facts, an explicit multi-asset target when one exists, or a material change
  to the vehicle/exposure — not an arbitrary price stop.
- **Recurring future-savings policy.** This is primarily a savings/execution
  policy, not a choice to leave already-available cash out of the market.
- **Predetermined staging of already-available cash.** This exchanges more
  immediate exposure for less timing regret or a plan the user can adhere to;
  it is not a mechanically superior return or risk rule.
- **Tactical or learning trade.** This is a different policy. Its governance
  must be an observable condition the user defines; do not supply a percentage,
  moving average, deadline, or earnings exit.
- **Defer.** Valid when the liquidity horizon, intended exposure, or policy
  class is still unknown.

For a thematic, sector, leveraged, narrow-country, or concentrated large-cap
index, do not apply the broad-market branch merely because it is an ETF or
tracks an index. Explain the remaining exposure concentration and identify the
index-composition fact that would settle classification. For a standing
long-horizon policy, do not leak a tactical percentage stop into its governance;
changed liquidity, goal, explicit allocation target, or vehicle facts are the
relevant alternatives.

Ask only questions that separate remaining live branches. Usually the baseline
and map should reach the user before an intake detour, but natural dialogue and
decision value determine placement; there is no universal count or last-slot rule.
For example: “Is this capital separated from a known spending need, or
are you deliberately making a tactical learning trade?” Do not ask “what is
your stop?” for a standing long-horizon policy.

## Question heuristics

For an ordinary single-trade framing, these are useful candidate questions,
not a required sequence or count. Never re-ask a known answer. Ask only what
could change the recommendation, and offer concrete options plus `not sure /
depends` when that makes the branch easier to answer. A question whose answers
produce the same visible output is a defect, not a reflection exercise.

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

A limitation must reach the user in a form they can act on. A question can be
more useful than narration when the user can answer it directly:

> Are your three largest positions already the same bet?

over the narration:

> I did not check your concentration.

Both are honest; only the first gives the user something to answer. The discriminator: **does this sentence hand the user something to decide, or does it only report what the product lacks?** The second is filler, and filler is what this product has already been caught producing around a perfectly good fact.

Three rules follow, and the third is the one that keeps the first two honest:

1. Select material portfolio facts by salience, not as a checklist. State them
   as limitations or ask about them according to whichever form best advances
   the recommendation.
2. A limitation that cannot be turned into a question is stated plainly and once — "I have secondary reporting, not the filing" — when it could change the framing or prevent a false impression of coverage. Put a truth-critical denominator, unit, or pricing set beside its number; place other material limitations where they make the answer clearest ([expression contract](../../../docs/expression-contract.md) D1–D2).
3. A material limitation may never simply disappear. Dropping the narration is a change of shape, not permission to leave a decision-relevant gap unsaid.

> **History:** this rule once required per-claim placement, then #823 replaced
> it with a universal tail block. Issue #825 removed both formatting mandates:
> the durable rule is relevance plus truth-critical inline qualification, not
> a required position or marker.

## Earning the next piece of evidence

The invitation names the question the evidence would answer, never the data being requested. It is generic because it is keyed on the small closed set of answers a book can buy that no book can, not on which question the user answered.

1. **What fraction this actually becomes.** Replaces a declared size with a computed weight.
2. **Whether this is a bet already held.** Driver overlap against the existing positions — the one most users cannot answer from memory.
3. **Whether it can be paid for.** The cash consequence.
4. **Whether it breaks a rule already set.** Collision with the user's own recorded rules.
5. **What happened the last time this reason was given.** The only one a positions snapshot cannot buy — it needs transaction history.

Choose invitations by salience — whichever of the five the user's own answers
made central to this decision. There is no numeric cap, but every invitation
must be capable of changing the recommendation; a list that does not earn its
space is still a disclosure dump. When none is decision-central, say nothing.

Placement is conversational rather than fixed: ask at the point where the answer naturally branches the recommendation.
A useful answer is never withheld until data arrives.

A holdings view buys the first four; transaction history alone buys the fifth, and nothing else does — name the evidence that would settle the question, never data in general. The wording is illustrative, not a template:

> You said you want this to be a core holding. Hand me a holdings screenshot and I can tell you what it actually becomes — and whether your top three are already the same bet.

Not "provide your portfolio for a more accurate analysis".

## Red lines, unchanged and hardest to hold here

- **Targets and forecasts remain judgment.** A recommendation is allowed, but
  its confidence must reflect that portfolio fit, concentration, cash, and
  rule collisions were not computed. State a target or forecast only when it
  is decision-relevant, with assumptions and uncertainty; an analyst target
  never becomes an engine fact or certainty merely because it was found.
- **A missing number is never replaced by a general rule.** A single-position cap is a fact measured against a computed weight and overridable by the user's own `set-cap`. Stated as this user's remaining capacity with no book, the identical sentence becomes fortune telling — the user may already be far past it, and nothing here knows that. A staged-entry, size, or leverage heuristic may still be recommended as labelled judgment; it must not impersonate a computed fact about this book.
- **"So should I buy it?"** gets the best bounded answer available: recommend, delay, or decline based on the stated premise and evidence, then name the portfolio fact most likely to reverse that judgment. Do not hide behind “the decision is yours,” and do not manufacture portfolio precision.
- **Brevity is not a licence to drop a fact.** Text-first is a default, never a
  limit on which claims the answer owes.

## Nothing is persisted

No answer, chosen principle, or working rule from this route is written to durable state. If saving a principle is ever justified, its owner is the distillation contract, not a file created beside it — and a condition only becomes checkable through [condition-slots.md](condition-slots.md).
