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
| **D1–D6** | Disclosure placement: one block, where it sits, how it is prefixed, how long it may be. | §3 below. |
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
  `build_honesty_ledger()`, `consider`'s `challenge` block. Expression governs
  where an owed fact is said, never whether it is owed.
- **What a surface renders.** Block order, module prerequisites, field sets:
  [output-contract.md](output-contract.md) for the card, the route reference
  for every other surface.
- **Which language it is said in.** [output-language.md](output-language.md).
- **Brevity as an effort ceiling.** `freeform-answers.md` rule 1 bounds what an
  answer *produces*. Nothing here licenses dropping a fact to satisfy a line
  cap; see D5.

## 3. Disclosure placement (D1–D6)

A disclosure is a sentence about the *limits* of what was just said: the book
it was measured on, the session it was priced at, the part of the denominator
that was not read, the risk nobody checked. It is not a judgment, and it is not
a fact the user asked for.

| ID | Rule | Verification class | Named oracle |
|---|---|---|---|
| D1 | One block, after what it qualifies | deterministic fixture | `expression_oracle` (E-1) |
| D2 | Truth-critical qualifiers stay inline, and only those | instruction only | — |
| D3 | Fixed line prefix, from the registry | deterministic fixture | `expression_oracle` (E-2) |
| D4 | Need-based: only what actually fired | instruction only | — |
| D5 | Line cap, with merging as the remedy | deterministic fixture | `expression_oracle` (E-3) |
| D6 | Each limitation appears exactly once | deterministic fixture | `expression_oracle` (E-4) |

D2 and D4 carry no mechanical oracle and the table says so rather than
implying one. Deciding whether a qualifier names a denominator, a unit or a
pricing set — and whether a disclosure's condition actually fired — requires
reading what the sentence is about, which is the boundary
`docs/development-guide.md` already draws between a code check and a judge.
Claiming otherwise would be the structural-gate failure this repository has
shipped before: a check that proves a prefix is present is not a check that
proves the right thing sits behind it.

### D1 — one block, after what it qualifies

Every disclosure an answer owes **collapses into one block, placed after the
content it qualifies**. Never interleaved with that content, never split across
two places, never opening the answer.

Which "after" is a layout question the surface owns: on the review card the
block is the footnote at the end of Block 1 (`output-contract.md` §4), because
that is where the numbers it qualifies end; on a conversational answer it is
the end of the answer.

This rule was paid for once already. The 2026-07-22 ruling reversed a
per-number placement policy after real high-density data fragmented the card's
indicator list into a wall of one-caveat-per-number interruptions
([#276](https://github.com/atomchung/fomo-kernel/issues/276)). #823 generalizes
that ruling product-wide instead of leaving it as one surface's local history.

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

Everything else goes to the block, however serious it is — including staleness
("that book is 45 days old"), reliability ("that cash balance has no anchor"),
and absence ("nobody checked liquidity"). Each of those qualifies how much the
number is *worth*, not what it *is*, and the enumeration is what keeps D2 from
swallowing D1: an "importance" test would readmit every disclosure to the body
one at a time, which is exactly how the wall this contract removes was built.

An inline qualifier is **not repeated** in the tail block (D6). The block is
where a disclosure goes, not a second copy of everything.

### D3 — fixed line prefix, from the registry

Each line of the block starts with its surface's registered prefix, and that
prefix marks disclosures and nothing else on that surface. This is what makes
the block greppable — by a mechanical check, by a QA receipt, and by a reader
skipping it.

| Surface class | Prefix | Why this one |
|---|---|---|
| Review card footnote | `- ` (the footnote's existing bullet) | Already shipped and already checked (`check_card.py` S-3); the card pipeline is a finished precedent and #823's constraint is not to reopen it. |
| Every conversational surface — `consider`, freeform answers, no-book framing, weekly market read | `[i] ` | The card's own `[v]`/`[X]`/`[?]`/`[*]` tag vocabulary, extended by one, so the prefix is language-neutral, never occurs in ordinary prose, and reads as a tag rather than as punctuation. |

Adding a surface class means adding a row here. Inventing a prefix inline does
not make one.

### D4 — need-based: only what actually fired

A disclosure appears because its condition is true on this call, never as
standing boilerplate. A manufactured disclosure is the same defect as a
manufactured invitation (`decision-framing.md`) and a manufactured concern
(V9) — all three fill space the evidence did not earn.

The corollary bites in the other direction too: dropping the block entirely
when nothing fired is correct, not an omission. An answer with no triggered
limitation ends at its last judgment.

### D5 — a line cap, and merging is the remedy

**At most five lines.** One line per independent limitation, not one per
payload key: the basis and the price session are one line (they describe the
same "which book, valued when"); every unchecked dimension is one line
together; two disclosures qualifying the same number are one line.

When the cap binds, **merge — never drop**. Brevity bounds what an answer
produces, never which facts it owes (`SKILL.md` rule 8, and the same clause in
`freeform-answers.md`). A limitation that cannot fit is a signal that two lines
describe the same thing, not a licence to leave a decision-relevant gap unsaid.

Five is the measured ceiling, not a preference. On a representative
high-density book — a stale unreconciled basis, a defaulted price session,
cost-priced weights, an unanchored cash balance, three unclassified holdings,
five unchecked dimensions — the merged block is four lines. The cap leaves one
line of headroom above the worst case this repository has measured, and a book
that needs six is evidence to re-measure rather than to write six.

### D6 — each limitation appears exactly once

Across the whole answer: not inline *and* in the block, not twice in the block
under two wordings, not restated in a closing sentence. This is V6's "state each
material limitation once", with the placement half now owned here.

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

At most three sources for one statement; past that, the statement is doing too
much work. (ALCE measured no gain beyond three.)

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
| Freeform answers | `references/freeform-answers.md` | The effort ceiling, the named chart set, the positions view's shape. |
| No recorded book | `references/decision-framing.md` | The three questions, the strategy-class map, the invitation set. |
| Weekly market read | `references/weekly-market-read.md` | What the prototype reads, and what it may not invoke. |

## 6. Enforcement

| Check | What it observes | Where |
|---|---|---|
| `check_card.py` S-3 | D1/D2 on the review card: no consecutive caveat paragraphs, none before Block 1, none inside Block 1. | `tests/agent/check_card.py` |
| `check_expression.py` E-1…E-5 | D1/D3/D5/D6 and C4 on a conversational answer, against synthetic witnesses. | `tests/agent/check_expression.py` |
| `check_voice.py` | V1–V9 witness classification. | `tests/agent/check_voice.py` |
| `answer_provenance` | C1/C2/C4 on a structured `--agent-case`, and the coverage a case may not leave uncited. | `skills/fomo-kernel/engine/answer_provenance.py` |
| `test_expression_contract.py` | That this registry is complete, that every surface document routes here, and that no surface carries its own placement rule. | `tests/test_expression_contract.py` |

What none of them observes is whether a model actually said it. A document, a
fixture, or a passing loader is implementation evidence; the delivery half is
observed by owner-live dogfood and by the QA receipt, exactly as
`output-voice.md` already states about its own registry.

## 7. Ruling log

| Date | Ruling |
|---|---|
| 2026-07-22 | Card disclosures collapse into one footnote, reversing per-number placement, after real high-density data produced a wall of per-number interruptions ([#276](https://github.com/atomchung/fomo-kernel/issues/276)). Scope: the review card. |
| 2026-08-14 | Owner ruling ([#823](https://github.com/atomchung/fomo-kernel/issues/823)): expression is abstracted out of every output surface and held across all of them. The 2026-07-22 placement ruling generalizes product-wide as D1; `decision-framing.md`'s contradicting per-claim rule is unified into D1/D2 rather than kept as a scope exception, because the rule it stated ("attached to the claim it qualifies, never grouped into a disclosure block") was written about *which limitations deserve saying at all*, and its placement clause was never the ruling anyone made. Provenance labelling is stated as the default (C1) with `--agent-case` demoted to its mechanical projection. |
| 2026-08-14 | Line cap set at five (D5) from a measured four-line worst case, with merging — never dropping — as the remedy, so a cap can never become an argument for omitting an owed fact. |
