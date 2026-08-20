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

Three registries live here:

| Series | Owns | Where the rules are |
|---|---|---|
| **V1–V9** | Voice: what an answer leads with, what it may not manufacture, when it stops. | [output-voice.md](output-voice.md) — routed, never copied. |
| **D1–D7** | Disclosure relevance and placement: what materially qualifies a claim, and which floor it lives on. | §3 below. |
| **C1–C4** | Citation and provenance: how a claim says where it came from. | §4 below. |

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

## 3. Disclosure relevance and placement (D1–D7)

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

## 4. Citation and provenance (C1–C4)

| ID | Rule | Verification class | Named oracle |
|---|---|---|---|
| C1 | Three provenances, always distinguishable | deterministic fixture on a structured case; instruction elsewhere | `answer_provenance` |
| C2 | A public fact carries source and date, inline and minimal | deterministic fixture on a structured case | `answer_provenance` |
| C3 | Common knowledge is not sourced | instruction only | — |
| C4 | Never the engine's own vocabulary | deterministic fixture | `expression_oracle` (E-5), `answer_provenance` |

### C1 — three provenances, always distinguishable

Every claim in an answer is one of three things, and the reader can always tell
which: **what the engine computed** from the user's own record, **a public fact**
the agent looked up, or **the agent's own judgment**. Do not blend them into one
unlabeled sentence.

This is the default, on every surface — not a property of the optional
`--agent-case` envelope. `schemas/answer-provenance.schema.json` is the
*mechanical projection* of this rule onto the one surface that can be checked
offline (a structured case submitted to `consider`), which is why that schema
exists and why it is not the rule itself. Reading the flag as the rule is what
left the default `consider` path with no provenance discipline at all — the
#823 diagnosis.

The label is a register, not a syntax. "Your record says", "that is my read",
"per the filing" all satisfy C1; a taxonomy printed at the user is C4.

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

## 5. Surface map

Each surface document keeps its own layout and references this file. None of
them may restate, narrow, or contradict V/D/C.

| Surface | Layout authority | What it keeps |
|---|---|---|
| Review card | [output-contract.md](output-contract.md) | Keynote + four blocks, module prerequisites, which block the footnote ends. |
| `consider` | `references/trade-consequence.md` | Salience order, answer slots, what the payload means. |
| Freeform answers | `references/freeform-answers.md` | Text-first defaults, proportionate production, reusable engine-backed views. |
| No recorded book | `references/decision-framing.md` | Claim boundaries, question heuristics, the strategy-class map, the invitation set. |
| Weekly market read | `references/weekly-market-read.md` | What the prototype reads, and what it may not invoke. |

## 6. Enforcement

| Check | What it observes — and what it does not | Where |
|---|---|---|
| `check_card.py` S-3 | Review-card layout only: no consecutive caveat paragraphs, none before Block 1, none inside Block 1. It does not govern conversational placement. | `tests/agent/check_card.py` |
| `check_expression.py` E-5 | C4 only: no engine payload token reaches a conversational answer. E-1–E-4 were retired by #825 because formatting is not evidence of relevance or clarity. | `tests/agent/check_expression.py` |
| `check_expression.py` E-6 | D7's bottom floor only: no machine anchor is rendered. Matched by shape (a long hex run), not by vocabulary, so it survives a prefix rename without importing the engine. It says nothing about which floor an owed fact landed on. | `tests/agent/check_expression.py` |
| `ux_receipt` delivery evidence | The same rule where the frozen value and the presented text are both in hand: a declared `machine_state` value appearing in the answer refuses the evidence rather than counting it. Silent on a challenge block that predates the key. | `skills/fomo-kernel/tools/ux_receipt.py` |
| `check_voice.py` | V1–V9 witness classification. | `tests/agent/check_voice.py` |
| `answer_provenance` | C1/C2/C4 on a structured `--agent-case`, and the coverage a case may not leave uncited — including the extent of an illegible book, not only that it is one. | `skills/fomo-kernel/engine/answer_provenance.py` |
| `test_expression_contract.py` | That both registries are complete, every surface routes here, D1–D6 honestly declare instruction-only verification, the C4 blacklist remains schema-derived, and the `consider` obligation floor stays smaller than the whole computed inventory. | `tests/test_expression_contract.py` |

**None of these runs against a live answer.** `check_expression.py` proves only
the exact C4 and D7-floor properties it can decide. D1–D6, and D7's other three
floors, are evaluated by reading the answer in context; pretending a regex
covered them was the constraint failure #825 removed. Nothing sits between the
model and the user.

The delivery half is observed the same way every other instruction-tier rule
in this repository is: by owner-live dogfood and by the QA receipt
(`references/ux-receipt.md`'s `consider` route). A document, a fixture, or a
passing loader is implementation evidence and this file says so rather than
letting a green suite read as a governed output.

## 7. Ruling log

| Date | Ruling |
|---|---|
| 2026-07-22 | Card disclosures collapse into one footnote, reversing per-number placement, after real high-density data produced a wall of per-number interruptions ([#276](https://github.com/atomchung/fomo-kernel/issues/276)). Scope: the review card. |
| 2026-08-14 | Owner ruling ([#823](https://github.com/atomchung/fomo-kernel/issues/823)): expression is abstracted out of every output surface and held across all of them. The 2026-07-22 placement ruling generalizes product-wide as D1; `decision-framing.md`'s contradicting per-claim rule is unified into D1/D2 rather than kept as a scope exception, because the rule it stated ("attached to the claim it qualifies, never grouped into a disclosure block") was written about *which limitations deserve saying at all*, and its placement clause was never the ruling anyone made. Provenance labelling is stated as the default (C1) with `--agent-case` demoted to its mechanical projection. |
| 2026-08-14 | Line cap set at five (D5) from a measured four-line worst case, with merging — never dropping — as the remedy, so a cap can never become an argument for omitting an owed fact. |
| 2026-08-19 | Issue #825 retires the product-wide block, prefix, and line-cap template plus E-1–E-4. Those checks proved formatting, not whether a limitation mattered. The review card keeps its footnote as local layout; conversational surfaces use relevance-driven placement. |
| 2026-08-20 | Owner ruling ([#830](https://github.com/atomchung/fomo-kernel/issues/830)): the product is too verbose, and the fix is deletion rather than a reading budget — anything whose must-have reason cannot be stated is cut. `consider`'s obligation list splits into owed / available / never-rendered, and D7 makes volume *distribution* expression's business, which §2 had disclaimed and nothing else had claimed. The reading-budget rule proposed as V10 is demoted to a backstop and is not adopted here: a length cap is what #827 had just deleted, and re-adding one would have priced the symptom instead of removing the cause. |
