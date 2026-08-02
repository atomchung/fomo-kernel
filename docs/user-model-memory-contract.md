# User model and decision-memory contract

Status: **research / architecture decision only**. This document does not
activate an implementation front. The current execution queue remains the M1
repair work named by issue #27.

Decision date: 2026-08-02.

Owners and related records: #446, #403, #450, #16, #650, #475, merged PR #460,
and draft PR #661.

## 1. Why this document exists

FOMO Kernel already stores several kinds of durable history and has separate
research tracks for behavioral style, profile distillation, standing rules,
and cross-period rationale. What is missing is one product contract that says
how those pieces work together to improve the user's next decision.

The goal is not a more elaborate description of the user. It is:

> Use a small, auditable slice of prior decisions to make the next decision
> clearer, reduce repeated mistakes, and learn which personal strategies
> deserve to survive.

The product must answer five different questions without collapsing them into
one profile summary:

1. **Memory** — what happened, and what did the user actually say?
2. **Style** — what repeated behavior is currently supported by evidence?
3. **Strategy** — what has the user chosen as a standing expectation for future decisions?
4. **Improvement focus** — what one behavior is the user deliberately testing now?
5. **Verdict** — did the later decision follow the expectation, and what did the outcome teach?

These concepts have different sources, authority, lifecycles, and readers.
They must not share one free-text `profile.md`-style source of truth.

## 2. Current repository truth

### Current milestone

Issue #27 currently routes only two M1 repair fronts. It explicitly says not to
merge wider M2 memory work without a new owner decision. This document may
land as a reversible design record, but it does not place memory implementation
on the current critical path.

### Memory-related state today

| Capability | Current state | Owner |
|---|---|---|
| Immutable review/session history | Shipped | existing session and ledger contracts |
| Pre-trade consultation persistence and review reconciliation | Shipped for its bounded use | `consider` contract |
| Continuing-position rationale event and bounded query | Designed; draft PR #661 contains the store/query module, but no user-facing consumer or stable integration has landed | #403 / #450 |
| Execution-qualified cross-period self-report reader | Specified; not a complete shared runtime reader yet | #450 |
| Behavior verdict persistence | Shipped by PR #460 as `verdicts.jsonl` | #446 cut 1 |
| Behavior verdict readback | Missing; the store has no user-facing query/consumer | #446 |
| Mechanical style dimension | First dimension exists; not yet a general user-model reader | #16 |
| Standing personal strategy/rule lifecycle | Partially shipped for rules, broader lifecycle still under design | #650 |
| Profile/distillation claim | Not shipped | #446 |

This distinction matters: **stored is not remembered**. A file with no bounded
reader that changes a later interaction is write-only schema debt, not product
memory.

## 3. Object boundaries

### 3.1 Memory

Memory is canonical historical evidence. It has no normative authority.

Examples:

- an executed trade event;
- a contemplated decision and its resolution state;
- the user's exact reason for continuing to hold a position;
- a user-adopted rule and its version;
- the portfolio basis frozen for a decision;
- a later outcome or review-period observation.

Memory preserves source identity and voice:

- `user_verbatim`;
- `engine_fact`;
- `agent_interpretation`;
- legacy or unknown provenance where applicable.

Memory never rewrites the original event to make a newer interpretation look
as if it had been known earlier.

### 3.2 Style

Style is a descriptive inference over repeated, execution-qualified behavior.
It answers:

> In this bounded class of situations, what does the user tend to do?

Style is not a permanent personality and is not a recommendation.

A style claim must include:

- a named behavioral dimension;
- an eligibility rule and sample count;
- an observation window;
- cited events or deterministic aggregates;
- counter-evidence;
- a status such as `candidate | supported | stable`;
- a supersession path.

There are two useful levels:

1. **Recent pattern** — a small number of recent examples. Wording must stay
   bounded: "in the recent eligible cases..."
2. **Observed style** — enough eligible, point-in-time observations to pass the
   dimension's sample and stability gates.

A recent pattern must never be silently promoted into an observed style.

### 3.3 Strategy

Strategy is normative and user-owned. It answers:

> What has the user chosen to do in future eligible situations?

A strategy may originate from:

- a direct user statement;
- a rule the user explicitly adopts;
- an agent proposal the user explicitly accepts;
- a previously tested strategy that the user keeps or revises.

Observed behavior cannot create a strategy. Repeatedly averaging down does not
mean "average down" is the user's strategy. An agent suggestion cannot become
policy merely because it was generated during a review.

The first implementation should reuse #650's standing-rule lifecycle wherever
one rule can express the strategy. A broader `Strategy` object is justified
only after a real reader proves one rule is insufficient.

### 3.4 Improvement focus

An improvement focus is one temporary, user-confirmed experiment. It answers:

> What one behavior are we deliberately trying to improve now, and how will we know?

Conceptual fields:

```yaml
source_claim_ids: []
target_behavior: ""
trigger: ""
intervention: ""
metric: ""
baseline_window: ""
review_after: ""
status: proposed | active | completed | revised | retired
user_confirmed: true
```

The product should allow at most one active focus in the first version. A full
profile may contain many claims; a user cannot effectively improve ten things
at once.

Do not add a new persistence object first. Try representing the initial focus
as a temporary standing rule/experiment under #650. Add a distinct object only
if duration, measurement, or retirement cannot be represented honestly.

### 3.5 Verdict

A verdict is an append-only learning event about a specific period or decision.
It answers questions such as:

- was the strategy applicable?
- did the user follow it?
- was the proposed reason new evidence or only a price move?
- did the memory intervention change the action, size, delay, or evidence collection?
- did the prior profile claim receive support or a counterexample?

A verdict about period N remains true about period N after the current profile
or strategy changes. It therefore cannot live inside a supersedable profile
projection.

## 4. The profile layer should store claims, not a biography

If #446 later introduces a distillation projection, its useful unit is a cited
`ProfileClaim`, not a complete investor-personality paragraph.

Conceptual contract:

```yaml
claim_id: ""
claim_kind: strength | risk_pattern | style | belief_hypothesis | unknown
claim: ""
scope: ""
evidence_refs: []
counter_evidence_refs: []
confidence: candidate | supported | stable
first_observed_at: ""
last_observed_at: ""
supersedes_claim_id: null
status: active | superseded | retired
future_observation: ""
```

Important constraints:

- no citation, no claim;
- a multi-model agreement is not a substitute for evidence;
- `unknown` is a useful claim kind when the record cannot yet distinguish two explanations;
- claims are served by relevance and consequence salience, not loaded globally;
- user-facing wording may be model-authored, but stored identity, scope,
  evidence, and status must remain mechanically checkable.

## 5. Readback: what it means and why it comes first

"Complete readback" does **not** mean reading a private research repository or
running another general summary over all historical files.

Readback means:

> A current product route can retrieve a small, correctly qualified historical
> slice from FOMO Kernel's own canonical state, and that slice causes a named,
> user-visible difference in the current interaction.

A readback is complete only when all of these hold:

1. **Canonical source** — the reader consumes the accepted event/store, not a
   duplicate summary.
2. **Correct subject** — position-cycle, decision, rule, or condition identity
   is proven; ticker-only guesses do not cross sale/re-entry boundaries.
3. **Voice and execution qualification** — user words, agent interpretation,
   considered action, and executed action remain distinct.
4. **Bounded retrieval** — the reader reports counts/truncation and does not
   scan or inject unbounded history.
5. **Real consumer** — a review or pre-trade route changes a question,
   challenge, or reconciliation because of the retrieved item.
6. **Failure safety** — corruption, ambiguity, or stale identity fails closed
   rather than choosing a convenient memory.

### Already-designed readback slices that remain incomplete

#### A. Position-rationale readback

#403 and draft PR #661 define an append-only rationale chain and a bounded
query. The missing product slice is a public action/reader integration where a
later relevant review can quote the prior statement instead of asking from
zero. A future `consider` consumer comes only after the route's identity and
frozen-reference questions are resolved.

#### B. Verdict readback

PR #460 writes replayable behavior verdicts. The first readback should surface
a sentence that cannot be generated from the current period alone, for example
that the same judged mismatch has occurred across multiple eligible periods.
Removing the reader must remove that sentence.

#### C. Shared self-report readback

#450 defines the bounded, provenance-preserving cross-period reader. Its shape
must become the shared foundation before a profile claim combines those events.
Otherwise profile distillation would become another writer over records that
current routes still cannot reliably retrieve.

This is why readback precedes profile generation. Building `ProfileClaim`
first would create another store whose output has no proven user-facing reader.

## 6. DecisionMemoryPacket

A live decision must not load the whole user model. It receives one ephemeral,
bounded packet assembled for that decision.

Conceptual output:

```yaml
relevant_self_reports: []       # max 2
relevant_profile_claims: []     # max 2
applicable_strategies: []       # max 2
active_improvement_focus: null  # max 1
prior_analog: null              # max 1
relevant_verdict: null          # max 1
current_state_refs: []
unknowns: []
truncation: {}
```

Selection priority:

1. same proven subject or decision class;
2. active improvement trigger;
3. current portfolio/rule consequence;
4. recent execution-qualified analog;
5. evidence strength and counter-evidence;
6. strategy applicability.

The packet is a read projection, not canonical state. It may be rebuilt. It may
not rewrite the events it cites.

## 7. How this changes the product

### Before

A user brings a new add decision. FOMO Kernel computes current portfolio
consequence and challenges the current reason, but it may not reliably say:

- how the current reason differs from the user's prior stated reason;
- whether a similar reason was used before;
- whether an unresolved behavior/rule mismatch is repeating;
- which one improvement experiment the user is currently testing.

### After

The same route still begins with deterministic current-book consequence. It
then adds only the relevant historical tension, for example:

> This proposal adds the same driver as the prior eligible decision. The prior
> recorded reason was X; the current reason adds no confirmed evidence beyond
> the price move. Your active experiment is to separate evidence changes from
> price changes, so the unresolved question is Y.

That sentence is valuable only if each clause is backed by the appropriate
source and identity. It must disappear or narrow when the memory is absent,
ambiguous, or contradicted.

## 8. First improvement experiment

The first owner-live experiment should be narrower than a full user model:

> Before an add or new-risk decision, distinguish a real evidence delta from a
> price delta and identify whether the action adds a new thesis or repeats an
> existing driver.

Candidate intervention:

1. What specific evidence is new? If none, state `price_delta_only`.
2. Does this create a new driver/thesis exposure, or add to an existing one?
3. What happened after the most relevant prior reason, and what is materially different now?

Do not turn this into a universal cash, position-size, tier, or stop-loss
threshold. The purpose is to test whether a bounded historical intervention
changes the decision process.

## 9. Validation without a new harness

This feature does not justify a separate memory-evaluation framework. The
validation surface should be the same surface the user will actually consume.

Use, in order:

1. **Focused unit and contract tests** for identity, provenance, bounded ordering,
   truncation, idempotency, corruption, and fail-closed behavior.
2. **One existing synthetic route fixture** proving the current review or
   `consider` output changes because a bounded memory item is present. The
   mutation is removal of that reader or memory row; the named historical
   sentence/question must disappear.
3. **One private owner-live trajectory** after the consumer exists, recording
   only a non-sensitive verdict about whether the readback changed the decision
   process.

Do not build:

- a separate multi-model comparison harness;
- a memory-specific campaign runner, dashboard, or scoring framework;
- a second route simulator beside the existing review/consider fixtures;
- a durable `DecisionMemoryPacket` store merely so a harness can inspect it;
- a generic profile benchmark before one real consumer exists;
- a broad A/B platform for a one-slice product question.

The current #718 QA front owns M1 correction-turn trace and accounting. This
memory work must not extend, depend on, or compete with that harness. Reuse an
existing trace/receipt only if it already fits after landing; otherwise one
focused fixture plus one owner verdict is sufficient.

The only evaluation question is:

> Did correctly qualified history cause one useful, named difference in the
> real route without adding false claims or extra ceremony?

If not, remove or narrow the memory feature rather than expanding the harness.

## 10. What the owner-live comparison actually does

This check happens **after** the required readback consumer exists. It is not a
request to run three models, not a request to copy a private repository, and not
a prerequisite for this docs-only decision.

Use one real, private decision locally and freeze the same current inputs for
the two readings:

- same recorded book and `PortfolioBasis`;
- same contemplated action/premise;
- same prices and public-fact allowance;
- same model/host where practical.

This does not require two automated arms. Read the current route once without
the new memory input, then once with the bounded readback, or compare the
pre-change synthetic fixture with the post-change fixture. Record only whether
the new history caused a named difference.

What matters:

- correctly recalling a prior user statement without turning interpretation into verbatim;
- surfacing one relevant prior outcome or unresolved verdict;
- avoiding a repeated known question;
- changing the key question or challenge;
- changing a concrete decision-process action: proceed, cancel, delay, reduce,
  collect evidence, or revise the stated reason;
- creating a later verdict that can determine whether the intervention helped.

Public GitHub evidence records only a synthetic fixture or a non-sensitive
owner verdict. No real holding, amount, date, motive, or answer enters this
repository.

### Pass / stop rule

Keep a memory unit only when its readback causes a named improvement without
introducing false identity, psychologizing, or extra ceremony.

Revise or remove it when:

- the output is effectively identical without it;
- the same result already comes reliably from current state;
- it adds questions whose answers do not change the response;
- maintenance cost exceeds the observed decision value;
- it turns a contextual pattern into a permanent personality label.

## 11. Ordered rollout

The ordering below is architectural. It does not override #27's current M1
repair queue.

### Cut 0 — this design record

- define the object boundaries and ownership map;
- no schema, engine, route, persistence, or harness change.

### Cut 1 — complete one self-report readback loop

After an owner decision reactivates M2:

- finish the #403/#450 public writer-reader integration;
- prove a prior rationale changes the next relevant review;
- use focused tests on the existing review route;
- keep `consider` unchanged in this cut.

### Cut 2 — give `verdicts.jsonl` its first reader

- bounded query over replayable verdicts;
- one user-visible sentence available only from cross-period history;
- no profile claim and no new harness.

### Cut 3 — one pre-trade DecisionMemoryPacket

- consume the shared readback contracts;
- one synthetic add/new-risk decision class in the existing route fixtures;
- no generic profile onboarding or card section;
- run one private owner-live trajectory and record only a safe verdict.

### Cut 4 — optional `ProfileClaim` projection

Only if Cuts 1–3 show that multiple raw events must be repeatedly synthesized
and direct bounded readback is insufficient:

- add cited, supersedable claims;
- serve at most two relevant claims per decision;
- mutation-test citation and scope failures;
- remove the feature if it does not change a downstream consumer.

### Cut 5 — strategy learning

Build on #650 and #475 only after opportunity denominators and outcome history
exist. Report rule eligibility, adherence, and observed outcomes. Do not invent
a full-portfolio counterfactual return.

## 12. Ownership map

| Concern | Existing owner |
|---|---|
| Continuing-position rationale writer and first consumer | #403 |
| Bounded, provenance-preserving cross-period self-report reader | #450 |
| Mechanical observed-style dimensions | #16 |
| Profile/distillation claims and behavior verdict boundary | #446 |
| Standing user strategy/rule lifecycle | #650 |
| Staged product scope and later rule learning | #475 |
| Current implementation queue | #27 |
| M1 QA trace/accounting harness | #718; not a dependency of this memory design |

Do not open a parallel memory epic or validation platform. New work belongs to
these owners unless a measured failure proves a distinct user outcome has no
home.

## 13. Engine, agent, and user authority

### Engine owns

- canonical event identity and lifecycle;
- execution and subject qualification;
- deterministic metrics and style dimensions;
- rule applicability and adherence where mechanically decidable;
- bounded query ordering/count metadata;
- current portfolio consequences;
- fail-closed validation and replay.

### Agent owns

- selecting which already-eligible memory matters to this decision;
- explaining evidence and counter-evidence;
- asking the one question code cannot settle;
- proposing an improvement experiment;
- translating engine output into direct user language.

The agent may not create an engine fact, upgrade interpretation into user
verbatim, or promote its proposal into user strategy.

### User owns

- the final decision;
- confirmation or correction of motive;
- adoption, revision, or retirement of strategy;
- activation of an improvement focus;
- whether a proposed memory interpretation is accepted as useful context.

## 14. Simplification rules

This contract simplifies the system by preferring composition over new
abstractions:

- reuse canonical events; do not copy them into a profile biography;
- reuse #650 standing rules before adding a Strategy store;
- reuse one temporary rule/experiment before adding an ImprovementFocus store;
- derive `DecisionMemoryPacket` ephemerally; never persist it;
- add `ProfileClaim` only after direct readback proves repeated synthesis is
  required;
- one consumer before another writer;
- one active improvement focus, not a program of concurrent interventions;
- one focused fixture and one owner verdict, not a harness.

Every proposed object or tool must answer:

1. Which current user-visible failure does it solve?
2. Why can the existing event, rule, reader, or route not solve it?
3. Which real consumer reads it immediately?
4. Which existing test surface proves the change?
5. What can be deleted or avoided because this object exists?

If the answer to question 2 or 3 is unclear, do not add the abstraction.

## 15. Non-goals

- no implicit reading, indexing, or mirroring of a private research repository;
- no universal investment ontology or generic memory graph;
- no full investor biography or permanent personality type;
- no runtime multi-model voting requirement;
- no automatic strategy creation from observed behavior;
- no automatic buy/sell verdict, target price, or market forecast;
- no new store before a real reader and consumer are named;
- no memory-specific harness, campaign runner, dashboard, or scoring system;
- no activation of M2 while #27 keeps M1 repair as the only execution queue.

## 16. Acceptance for this design decision

This document is useful when a future contributor can answer, before coding:

1. Which object owns this fact: memory, style, strategy, focus, or verdict?
2. What canonical evidence and subject identity support it?
3. Which existing issue owns the work?
4. Which current route will read it?
5. What user-visible behavior changes when the reader is present?
6. How is the change later reconciled or retired?
7. What existing test surface proves it without a new harness?
8. What privacy-safe evidence proves it worked?

If those questions cannot be answered, the proposed memory feature is not yet
an executable slice.
