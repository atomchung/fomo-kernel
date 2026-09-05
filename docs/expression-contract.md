# Expression contract — how this product speaks, on every surface

> Status: **v1**, encoding the owner ruling recorded in
> [#823](https://github.com/atomchung/fomo-kernel/issues/823) (2026-08-14):
> *the way FOMO Kernel expresses itself must not be attached to any one
> output; abstract it out, and hold it across every output scene.*

## 1. What this file is, and what it is not

Expression is **how an answer is said**: its voice, where its disclosures go,
and how a claim states where it came from. Layout is **what a given surface
renders**: which blocks, in which order, with which fields.

This file owns expression, for every user-visible surface at once. Each
surface document keeps layout, and references the rules below rather than
restating them. A surface that carries its own expression rule is the defect
this file exists to remove: before #823 the review card was governed
(one collapsed footnote, one number one home, a net-line-count acceptance,
a mechanical placement check) and `consider`, freeform answers and the no-book
route were not — and `decision-framing.md` mandated the exact opposite
placement rule with no scope note, so two contradictory disclosure policies
coexisted silently.

§3 is the **mother chapter**: the one shape every answer takes. It is not a
registry entry and has no ID, because it is the law the registries are
parameters of. Read it before anything below it.

Three registries live under it:

| Series | Owns | Where the rules are |
|---|---|---|
| **V1–V9** | Voice: what an answer leads with, what it may not manufacture, when it stops. | [output-voice.md](output-voice.md) — routed, never copied. |
| **D1–D7** | Disclosure relevance and placement: what materially qualifies a claim, and which floor it lives on. | §4 below. |
| **C1–C4** | Citation and provenance: how a claim says where it came from. | §5 below. |

Voice stays in its own file rather than being folded in here because V1–V9 are
a stable, ID-addressed registry with their own oracle
(`tests/agent/check_voice.py`) and their own cross-host protocol
(`tests/agent/voice-cross-host.md`). Copying them would create a second
authority, which is the failure this file is against. Routing them is what
`AGENTS.md`'s own instruction-authority policy already asks for.

## 2. What is not expression

Out of scope here, and unchanged by this file:

- **Which facts an answer owes.** That is computed per route — the card's
  `build_honesty_ledger()`, `consider`'s `challenge` block. Whether a fact is
  owed is decided upstream and expression never overrules it.

  What expression *does* own, and did not before #830, is the **distribution**
  of what gets said: which floor each fact lives on — opening body,
  parenthetical beside its number, one line in the end block, or not rendered
  at all — and therefore how much of the answer any one obligation is entitled
  to. The pre-#830 wording ("expression governs where an owed fact is said,
  never whether it is owed") was read as a disclaimer of volume, and nothing
  else claimed it: no rule anywhere governed how much an answer said. So every
  owed fact landed in body prose as its own sentence, and a fifteen-item
  obligation list became a fifteen-sentence answer. D7 is the rule that was
  missing. It cannot license dropping an owed fact — a fact whose floor is the
  end block is still said — and the route's own obligation list is still the
  authority on which facts those are.
- **What a surface renders.** Block order, module prerequisites, field sets:
  [output-contract.md](output-contract.md) for the card, the route reference
  for every other surface.
- **Which language it is said in.** [output-language.md](output-language.md).
- **Text-first as a default.** `freeform-answers.md` starts simple questions
  cheaply without limiting relevant research, tools, or presentation. Nothing
  here licenses dropping a fact to satisfy a style preference; see D5.

## 3. The answer pyramid — the mother law

Owner ruling, 2026-08-21
([#832](https://github.com/atomchung/fomo-kernel/issues/832)). **Every
user-visible answer this product gives has one shape.** It is mandatory, it is
small, and every surface below derives from it rather than restating it. D1–D7,
C1–C4, and every surface document are parameters of this shape.

**Why one shape, and why now.** #830 deleted the obligation whitelist. The
post-merge rerun of the same four frozen scenes (receipts on
[PR #831](https://github.com/atomchung/fomo-kernel/pull/831)) shows composition
fixed — no basis recital, no rendered machine anchor, disclosures collapsed,
answers opening on stance — and **first-answer length inside ±5% of the
pre-trim arm in all four scenes**. Deleting obligations was necessary and not
sufficient: nothing positive said what an answer *is*, so the space the
deleted obligations vacated refilled with discretionary elaboration. Worse,
the one norm that could have said it already existed **five times, written
five different ways**: the card's keynote (output-contract.md §2), V1
(output-voice.md), the reader-order paragraph (SKILL.md), "lead with the
bounded value" (decision-framing.md), "the host shows value first"
(weekly-market-read.md). Five local phrasings of one idea drift by
construction. #823 unified where the *rules* live and classified answer shape
as per-surface layout; that classification is the seam this chapter closes.

Four external traditions converge on the same answer, and each contributed one
piece: the **consulting pyramid** (answer on top, supports under it, evidence
at the bottom, and the reader descends only as far as they need); the
**sell-side research note** (the first page is the rating, the thesis and the
numbers that carry it, and the anatomy is a house template rather than an
author's preference); **Anthropic's own prompting guidance** (worked examples
steer format more reliably than a list of constraints, and a small mandatory
base plus opt-in specialization beats one long rule set); **design-system token
layering** (a mandatory core, an additive component layer, a fall-through
default, and a different owner and cadence per layer).

### 3.1 The four floors

| Floor | What it carries | How much of it |
|---|---|---|
| **Top** | The answer: the stance, and the reason that decides it. | One sentence. Always present. |
| **Middle** | Only support that changes the action, the confidence in it, or the comparison against the alternative in play. | As many blocks as pass the increment gate, and no more. |
| **Bottom** | Evidence, alternatives, and the rest of the computed inventory. | Not in the answer. It lives in the data layer and surfaces on request. |
| **End** | Caliber: sources, as-of, which book, which session, material gaps. | One compact block, one line each — D7. |

**The top is an answer, not a topic sentence.** It names the stance —
proceed, resize, delay, collect evidence, pick this candidate, no trade, or
"this is what your book now looks like" for a question with no action in it —
and the single reason that decides it. A first sentence that describes what
the answer is about, restates the question, or narrates what was computed has
spent the only position the reader is guaranteed to read.

**The middle is gated by increment.** Every block must add a NEW
decision-relevant fact or judgment. The test is not "is this true" and not "is
this owed" — under the whitelist era everything printed was both. The test is:
**delete this block; can the reader still take the call, judge how far to
trust it, and see why it beat the alternative they were weighing?** If nothing
is lost on any of those three, the block is not shortened, it is deleted.
Blocks that survive the gate have no cap, and this is deliberately not a length
rule: an answer that genuinely needs six increments gets six.

Owner amendment, 2026-09-05 (§8, and [#827](https://github.com/atomchung/fomo-kernel/issues/827)).
The gate read **"does the decision change?"** until this ruling, which is one
clause narrower than the shape it was written for. A block that lets the reader
check the call, or that says what makes this pick better than the one they came
in holding, does not flip the action — so the literal test deleted it, and the
answer kept its stance while losing the reason anyone could weigh. The three
failures the amendment names are observed, not hypothetical: a stance the
reader cannot check, an answer that never engages the reason they were buying,
and a better candidate named with nothing said about what makes it better.

The bans below did not move, and the amendment is not a volume licence.
Support that restates, hedges, explains a system default, or invents a scenario
adds nothing on any of the three and is still deleted. **Confidence is not a
hedge** — it is stated as the threshold that would change the call (§3.2), so a
block earning its place on the second clause carries a number or a condition,
never an adverb. **Comparison is not a survey** — it is the one alternative the
user is actually choosing against, not a tour of the field.

**Four named bans.** Each was observed live in the #827 or #830 runs, each
survived every rule then on the books, and each is a way for a block to add
volume without adding an increment. The slug after each name is what the
exemplar corpus references it by (§3.5):

- **A manufactured hypothetical scenario nobody asked for**
  (`manufactured_scenario`). Inventing a comparison, a simulation, or a
  what-if to carry facts that had nowhere else to go. The owner's verdict on
  the observed instance — an all-in-one-name simulation nobody requested — was
  that it meant nothing. The user's own question already bounds what is being
  decided.
- **A system default restated as insight** (`default_as_insight`). The
  engine's own threshold fired, and the answer explains the threshold as though
  the reader had learned something about their book. Both arms of the #827 A/B
  independently wrote the same sentence about the same default, which is what
  obligation discharge looks like from the outside. The user's *own* rule is
  the opposite case and is never optional (see §4's D-series and
  `rule_effects`).
- **A hedging couplet** (`hedging_couplet`). "This is not a reason to wait,
  but…" — a sentence that states a position and withdraws it in the same
  breath, leaving the reader with the work of deciding which half was meant.
  Say the half you mean. A real uncertainty is a falsifier with a threshold,
  not a hedge.
- **The same point in a second form** (`restated_point`). A judgment made in
  prose and then again as a bullet, a table row, or a summary line. Restating
  is not emphasis; it is the reader paying twice for one increment. This is
  D6/D7 seen from the shape side.

**A stance to wait names what it is waiting for.** Owner amendment, 2026-09-05
(§8). *Delay* and *collect evidence* are answers like any other, and they owe
what a directional call owes in its falsifier: the specific evidence that would
settle the question, and the next point at which it can actually be checked. A
scheduled date is not that reason — an earnings date exists on every name every
quarter, and naming one without saying which figure in it decides the call is a
wait with no end. Where no verified checkpoint exists, say the trigger is a
condition rather than a date, and say which condition. An answer that
recommends waiting and names neither has withheld the decision rather than made
one.

**The bottom floor is a door, not a section.** The route still computes
everything, and the user may ask for any of it at any moment. The answer says
**once** that expansion is available — one short offer, in whatever register
the surface speaks — and does not preview, summarize, or partially deliver the
expansion in advance. Not saying a number this decision does not turn on is
not hiding it; that is the whole distinction #830's deletion rests on.

**The end block is D7's one floor**, unchanged by this chapter: sources,
as-of, which book, which session, and the material gaps, one line each,
non-narrative, collected once. An answer with nothing on that floor ends at
its judgment (D4).

### 3.2 Voice

Write as a senior analyst speaking to their own principal. Direct, concrete,
and already inside the decision: the reader owns the money, has the context,
and asked a real question. Name the call and the number it turns on. Do not
teach the concept behind the number, do not narrate the process that produced
it, do not soften a judgment into a menu, and do not close with a summary the
reader just read. Confidence is expressed as a threshold that would change the
call, never as an adverb and never as a disclaimer.

### 3.3 Derivation is additive, and empty derivation is the default

A surface may **add parameters** to this shape: which blocks its middle floor
is allowed to hold, which question set it may ask from, what its end block
must name, what it may not compute at all. A surface may **not** restate,
narrow, re-order, or contradict the shape itself. A surface with no special
need declares nothing and falls through to the default — that is the expected
case, not a gap.

The one document incarnation is the review card: its keynote plus four blocks
*is* this pyramid rendered as a document, and `output-contract.md` §2 keeps
that structure exactly as it is. Its derivation is recorded there; nothing
about the card changes.

### 3.4 Registry freeze for shape and length

**V, D, and C take no new IDs for a shape or length concern.** A style fix has
exactly two lanes now:

1. **Amend this chapter** — requires an owner ruling, and lands in §8's log.
2. **Add an exemplar or a counter-exemplar** — the day-to-day lane, no ruling
   required, and the one that carries most fixes.

A new V/D/C ID for "answers are too long", "lead with X", or "stop repeating
Y" is refused: five independent phrasings of the answer-first principle is
what this chapter exists to end, and a sixth with an ID on it is still a sixth.
The registries keep their existing IDs and their existing subjects — a
disclosure's *materiality* (D), a citation's *provenance* (C), a lead's
*failure class* (V) — and the historical definitions stay readable where they
are. Where a local answer-shape phrasing was superseded by this chapter, the
surface document says so rather than deleting its own history.

### 3.5 Exemplars are the spec

The binding statement of this shape is not the prose above; it is the exemplar
set in `tests/agent/expression-witnesses.json`. Three to five canonical
exemplars per conversational surface, each declaring the one-sentence answer it
leads with and the increment each of its blocks adds, plus counter-exemplars
for the named bans. Every issuer in them is fictional (Widgetron WDGT,
Gridcore GRDC, Fabrion FABR, ACME) and nothing is derived from a real user
record — the notes some scenes quote are invented with the issuers.

Since #834 the corpus is not the only place they live. Each conversational
surface's reference file **opens with its own copy of one of them**, so the
example is met while an answer is being written rather than only while one is
being graded. The two copies are one text, not two:
`tests/test_expression_contract.py` compares each reference block against the
scene its fence names and fails on drift.

`tests/agent/check_expression.py` derives E-7 and E-8 from that corpus (§7).
What they decide is exactly two things — that the declared answer really leads,
and that every declared block really adds a distinct increment — and the
corpus records, as an asserted fact, that two of the four bans are
**invisible** to it. A manufactured scenario declares no increment and a
restated point declares one twice, so E-8 reaches both. A system default
explained as insight and a hedging couplet are well-formed blocks carrying
true content; deciding that one is a sermon and the other withdraws its own
position requires reading what the answer means, which is the boundary
`docs/development-guide.md` already draws between a code check and a judge.
This chapter states that boundary rather than implying a gate it does not have.

## 4. Disclosure relevance and placement (D1–D7)

A disclosure is a sentence about the *limits* of what was just said: the book
it was measured on, the session it was priced at, the part of the denominator
that was not read, the risk nobody checked. It is not a judgment, and it is not
a fact the user asked for.

| ID | Rule | Verification class | Named oracle |
|---|---|---|---|
| D1 | Put a material limitation where it is clearest | instruction only | — |
| D2 | Truth-critical qualifiers stay inline, and only those | instruction only | — |
| D3 | No mandatory marker or block syntax | instruction only | — |
| D4 | Relevance-based: only what could change or qualify the recommendation | instruction only | — |
| D5 | Brevity follows salience, not a numeric line cap | instruction only | — |
| D6 | State each material limitation once | instruction only | — |
| D7 | A fact lives on exactly one floor | deterministic fixture on the machine-anchor floor; instruction elsewhere | `expression_oracle` (E-6) |

D1–D6 carry no mechanical oracle and the table says so rather than implying
one. Deciding whether a limitation is material, where it reads most clearly,
and whether two sentences duplicate the same concern requires reading what the
answer means, which is the boundary
`docs/development-guide.md` already draws between a code check and a judge.
The retired E-1–E-4 checker proved only block position, prefix, line count, and
literal duplication. It could not prove relevance or clarity, so Issue #825
deleted it rather than treating answer formatting as product integrity.

D7 declares the same split honestly: only its bottom floor — a machine anchor
rendered at a person — is a thing a regex can decide, and that is the only half
E-6 claims.

### D1 — put a material limitation where it is clearest

A material limitation may stay beside the claim it changes, or several may be
collected when that reads better. It does not have to open or close the answer,
and conversational surfaces do not inherit the review card's footnote layout.
The test is comprehension, not a fixed position.

### D2 — truth-critical qualifiers stay inline, and only those

One exception, and it is an **enumeration, not a judgment call about
importance**. A qualifier stays inline when it names one of exactly three
properties of the number beside it:

1. **Denominator** — which holdings the percentage was measured over, when that
   is less than the whole book (`partial_book`, `unclassified_book`,
   `etf_not_decomposed`).
2. **Unit** — cost share versus market share, native currency versus the
   aggregate a weight is measured in, % versus pp.
3. **Pricing set** — which market session valued *this* instrument, where that
   differs from the frame every other number came from.

Those three change what the number *refers to*, so a sentence without them
states something false rather than something incomplete.

Other limitations — staleness, reliability, liquidity, valuation, tax, and
position fit — are not automatically owed. Include one when it could reverse
the recommendation, materially lower confidence, or prevent the answer from
implying it was checked.

### D3 — no mandatory marker or block syntax

Conversational answers need no `[i]` prefix, dedicated tail block, heading, or
bullet form. Use ordinary prose unless a compact list genuinely improves
readability. A rendered review card may keep its own footnote layout; that is a
surface layout decision, not a universal expression rule.

### D4 — relevance-based

A limitation appears because it matters on this call, never as standing
boilerplate. An available `unchecked` dimension is not, by itself, a reason to
surface it. When nothing material remains, the answer ends at its judgment.

### D5 — brevity follows salience

There is no line cap. Merge related limits when that clarifies them; keep them
separate when merging would hide distinct consequences. Brevity is evaluated
by decision value, not by satisfying a formatter.

### D6 — each limitation appears exactly once

Across the whole answer, state a material limitation once. This is a semantic
instruction, not a literal-string deduplication gate.

### D7 — a fact lives on exactly one floor

Owner ruling, 2026-08-20 ([#830](https://github.com/atomchung/fomo-kernel/issues/830)).
D1–D6 say *whether* a limitation is worth saying and leave every one of them
free to land in body prose. That is how a fifteen-item obligation list became
a fifteen-sentence answer while every rule above stayed satisfied. D7 is the
missing half: each fact has one floor, and appearing on two is a bug.

| Floor | What lives there | Form |
|---|---|---|
| **Opening body** | The facts that decide this call: the stance, the reason it wins, the one or two numbers that would flip it. | Ordinary prose. |
| **Parenthetical beside its number** | A truth-critical qualifier — denominator, unit, or pricing set, exactly D2's enumeration. | Inside the sentence that carries the number. |
| **One end block** | Sources, caliber, and material gaps: which book, which session, what was not checked. | One line each, non-narrative, collected once at the end. |
| **Not rendered** | Machine anchors: content hashes, state versions, validator detail. | Payload only. |

Four consequences the surfaces below inherit:

- **The end block is one block, not a running commentary.** Related caliber
  lines merge into it; they do not each earn a paragraph beside the claim they
  qualify. This does not reinstate the review card's footnote as a universal
  layout — a conversational answer with nothing on that floor ends at its
  judgment (D4), and the card keeps its own footnote as local layout.
- **A pre-written disclosure sentence is an end-block line.** Where a route
  hands the answer localized disclosure copy (`consider`'s
  `disclosures_display`), it is material for that block, not a paragraph to
  paste into the body.
- **Nothing appears on two floors.** A qualifier that rode a body sentence is
  finished; repeating it below is D6's duplication seen from the placement
  side.
- **Completeness lives in the data layer.** The route still computes
  everything, and the user can ask for any of it at any time. Not saying a
  number this decision does not turn on is not hiding it.

## 5. Citation and provenance (C1–C4)

| ID | Rule | Verification class | Named oracle |
|---|---|---|---|
| C1 | Four provenances, always distinguishable | deterministic fixture on a structured case; instruction elsewhere | `answer_provenance` |
| C2 | A public fact carries source and date, inline and minimal | deterministic fixture on a structured case | `answer_provenance` |
| C3 | Common knowledge is not sourced | instruction only | — |
| C4 | Never the engine's own vocabulary | deterministic fixture | `expression_oracle` (E-5), `answer_provenance` |

### C1 — four provenances, always distinguishable

Every claim in an answer is one of four things, and the reader can always tell
which: **what the engine computed** from the user's recorded book, **the user's
own written record** — a note they wrote, quoted as theirs with its date (#844) —
**a public fact** the agent looked up, or **the agent's own judgment**. Do not
blend them into one unlabeled sentence, and never hand the user's own words
back to them under another of the four labels.

This is the default, on every surface — not a property of the optional
`--agent-case` envelope. `schemas/answer-provenance.schema.json` is the
*mechanical projection* of this rule onto the one surface that can be checked
offline (a structured case submitted to `consider`), which is why that schema
exists and why it is not the rule itself. Reading the flag as the rule is what
left the default `consider` path with no provenance discipline at all — the
#823 diagnosis.

The label is a register, not a syntax. "Your record says", "you wrote on the
30th that", "that is my read", "per the filing" all satisfy C1; a taxonomy printed at the user is C4.

### C2 — a public fact carries its source and its date, inline and minimal

A looked-up fact states where it came from and when, in a few words beside the
claim — the Reuters house form ("Reuters data"), not a citation apparatus.
Full provenance fields belong in structured payloads, never in prose.

Use the minimum sufficient sources for the claim and its material coverage.
Additional sources earn their place by resolving a contradiction or widening a
stated search universe, not by citation volume alone.

### C3 — common knowledge is not sourced

A statement of the obvious carries no attribution. Sourcing density tracks
consequence: a claim the decision turns on is sourced, and background is not.
Exhaustive source listing is its own failure.

### C4 — never the engine's own vocabulary

A payload token — `already_over`, `cost_basis`, `partial_book`,
`unusable_shares`, `rule_effect`, a schema name, a CLI flag — never reaches the
user. Say what it means for this decision: "computed on cost rather than on
current prices", not "cost_basis".

Already enforced on the question surface and on `--agent-case` claims
(`answer_provenance._assert_no_internal_leak`); C4 is that rule stated once for
every surface, and `check_expression.py` E-5 is its check on the conversational
ones.

## 6. Surface map

Each surface document keeps its own layout, derives its answer shape from §3,
and references this file. None of them may restate, narrow, or contradict the
pyramid or V/D/C. "Derivation" is what that surface **adds** to §3; an empty
derivation is valid and is the default.

**A shared claim authority is not a derivation.** Three of the conversational
surfaces read `references/research-priors.md` for the same research baseline
(#716), and it adds a shape parameter to none of them: it says what a block may
be *backed by*, never which floor a block sits on or that a block must exist.
The derivation column below is unchanged by that ruling; the file appears in the
last column, where a surface's other keeps live. Its own "An engine fact
dominates a prior" section is the single statement of how far a prior may travel
beside a computed number; the two book-bearing routes carry that sentence
**verbatim** rather than a paraphrase of it, and `tests/test_research_priors.py`
fails on drift — the same mechanism #834 used to put one exemplar on each
surface's generation path.

| Surface | Layout authority | Derivation it adds to §3 | Everything else it keeps |
|---|---|---|---|
| Review card | [output-contract.md](output-contract.md) | The **document incarnation**: keynote + four fixed blocks, in that order, on every committed card. | Module prerequisites, which block the footnote ends. |
| `consider` | `references/trade-consequence.md` | Lead-selection salience order; the answer slots the middle floor may hold; `rule_effects` is never traded away. | What the payload means, the obligation floor, the research baseline a computed number may be interpreted with. |
| Freeform answers | `references/freeform-answers.md` | None on shape — text-first is a latency default, not a shape. | Proportionate production, reusable engine-backed views, the same research baseline when the question is a decision. |
| No recorded book | `references/decision-framing.md` | The top sentence is a research-backed baseline when no book exists; the strategy-class map is a middle-floor block set. | Claim boundaries, question heuristics, the invitation set; the baseline catalogue it shares with the two routes above. |
| Weekly market read | `references/weekly-market-read.md` | Its one optional question comes after the complete brief, never before it. | What the prototype reads, and what it may not invoke. |

## 7. Enforcement

| Check | What it observes — and what it does not | Where |
|---|---|---|
| `check_card.py` S-3 | Review-card layout only: no consecutive caveat paragraphs, none before Block 1, none inside Block 1. It does not govern conversational placement. | `tests/agent/check_card.py` |
| `check_expression.py` E-5 | C4 only: no engine payload token reaches a conversational answer. E-1–E-4 were retired by #825 because formatting is not evidence of relevance or clarity. | `tests/agent/check_expression.py` |
| `check_expression.py` E-6 | D7's bottom floor only: no machine anchor is rendered. Matched by shape (a long hex run), not by vocabulary, so it survives a prefix rename without importing the engine. It says nothing about which floor an owed fact landed on. | `tests/agent/check_expression.py` |
| `check_expression.py` E-7 | §3's top floor, on an exemplar: the scene's declared one-sentence answer must actually appear in the answer's opening block. It decides *placement of a declared core*, never whether that core is the right call. | `tests/agent/check_expression.py` |
| `check_expression.py` E-8 | §3's increment gate, on an exemplar: every declared block's text occurs in the answer in declared order, and every block declares a distinct, non-empty increment. It decides *distinctness*, never whether an increment was worth having. | `tests/agent/check_expression.py` |
| `ux_receipt` delivery evidence | The same rule where the frozen value and the presented text are both in hand: a declared `machine_state` value appearing in the answer refuses the evidence rather than counting it. Silent on a challenge block that predates the key. | `skills/fomo-kernel/tools/ux_receipt.py` |
| `check_voice.py` | V1–V9 witness classification. | `tests/agent/check_voice.py` |
| `answer_provenance` | C1/C2/C4 on a structured `--agent-case`, and the coverage a case may not leave uncited — including the extent of an illegible book, not only that it is one. | `skills/fomo-kernel/engine/answer_provenance.py` |
| `test_expression_contract.py` | That both registries are complete, every surface routes here **and declares its §3 derivation**, that no surface still carries a local answer-first phrasing, D1–D6 honestly declare instruction-only verification, the C4 blacklist remains schema-derived, and the `consider` obligation floor stays smaller than the whole computed inventory. | `tests/test_expression_contract.py` |

**None of these runs against a live answer.** `check_expression.py` proves only
the exact C4, D7-floor, and exemplar-corpus properties it can decide. D1–D6,
D7's other three floors, and two of §3's four named bans — a system default
restated as insight, and the hedging couplet — are evaluated by reading the
answer in context; pretending a regex covered them was the constraint failure
#825 removed. The corpus asserts that gap rather than hiding it: a
counter-exemplar for either of those two bans must **pass** every mechanical
assertion, so the day someone builds a real oracle for one of them, that
assertion is what tells them the coverage boundary moved. Nothing sits between the model and the user.

The delivery half is observed the same way every other instruction-tier rule
in this repository is: by owner-live dogfood and by the QA receipt
(`references/ux-receipt.md`'s `consider` route). A document, a fixture, or a
passing loader is implementation evidence and this file says so rather than
letting a green suite read as a governed output.

## 8. Ruling log

| Date | Ruling |
|---|---|
| 2026-07-22 | Card disclosures collapse into one footnote, reversing per-number placement, after real high-density data produced a wall of per-number interruptions ([#276](https://github.com/atomchung/fomo-kernel/issues/276)). Scope: the review card. |
| 2026-08-14 | Owner ruling ([#823](https://github.com/atomchung/fomo-kernel/issues/823)): expression is abstracted out of every output surface and held across all of them. The 2026-07-22 placement ruling generalizes product-wide as D1; `decision-framing.md`'s contradicting per-claim rule is unified into D1/D2 rather than kept as a scope exception, because the rule it stated ("attached to the claim it qualifies, never grouped into a disclosure block") was written about *which limitations deserve saying at all*, and its placement clause was never the ruling anyone made. Provenance labelling is stated as the default (C1) with `--agent-case` demoted to its mechanical projection. |
| 2026-08-14 | Line cap set at five (D5) from a measured four-line worst case, with merging — never dropping — as the remedy, so a cap can never become an argument for omitting an owed fact. |
| 2026-08-19 | Issue #825 retires the product-wide block, prefix, and line-cap template plus E-1–E-4. Those checks proved formatting, not whether a limitation mattered. The review card keeps its footnote as local layout; conversational surfaces use relevance-driven placement. |
| 2026-08-20 | Owner ruling ([#830](https://github.com/atomchung/fomo-kernel/issues/830)): the product is too verbose, and the fix is deletion rather than a reading budget — anything whose must-have reason cannot be stated is cut. `consider`'s obligation list splits into owed / available / never-rendered, and D7 makes volume *distribution* expression's business, which §2 had disclaimed and nothing else had claimed. The reading-budget rule proposed as V10 is demoted to a backstop and is not adopted here: a length cap is what #827 had just deleted, and re-adding one would have priced the symptom instead of removing the cause. |
| 2026-08-21 | Owner ruling ([#832](https://github.com/atomchung/fomo-kernel/issues/832)): **one communication method.** §3 becomes the mother law — one pyramid, mandatory on every surface — and the five independent local phrasings of answer-first (the card keynote, V1, SKILL.md's reader-order paragraph, `decision-framing.md`'s bounded-value lead, `weekly-market-read.md`'s value-first, plus `trade-consequence.md`'s reader-question-chain section, the sixth the audit found) are replaced by derivation references. Derivation is **additive only** and an empty derivation is the default. The increment gate and four named bans are encoded; V/D/C take no new IDs for a shape or length concern, so a future style fix is either an owner amendment to §3 or an exemplar — never a sixth phrasing with an ID on it. The card's structure is unchanged: it is the document incarnation of the same pyramid. Deliberately **not** adopted, again: any character-count cap (#543's ceiling, deleted by #827, stays deleted) — the shape is positive, and length is its consequence rather than its rule. Integrity gates (engine-owned numbers, provenance, canonical writes, execution truth, privacy) are untouched, and the #829 unselected-write finding stays open and out of scope. |
| 2026-09-05 | Owner amendment ([#827](https://github.com/atomchung/fomo-kernel/issues/827), through §3.4's first lane): §3's increment gate widens from *does the decision change* to **the action, the confidence in it, or the comparison against the alternative in play**, and a *wait* stance owes what it is waiting for plus the next checkable point. Both are amendments to the mother chapter rather than new IDs, which is what §3.4 requires of a shape fix. The narrow gate deleted support that let the reader check a call or see why one candidate beat another — neither flips the action, and both are what the owner's blind cases asked for. The four bans, D1–D7, C1–C4, the registry freeze and the standing refusal of any character-count ceiling are unchanged, and neither amendment licenses a fact the route did not owe: a block still has to earn one of the three. |
