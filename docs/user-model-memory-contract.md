# User decision context — context first, decision second

Status: **research / architecture decision only**. This document does not
activate an implementation front. Issue #27 still owns the current M1 repair
queue.

Decision date: 2026-08-03.

Owners and related records: #446, #475, #403, #450, #650, merged PR #460,
and #718.

## 1. Owner decision

FOMO Kernel should not begin its next stage by separately productizing memory,
style, strategy, improvement focus, and verdict readers.

The first product problem is simpler:

> Establish enough accurate context about the user that a strong agent can
> understand the decision in front of it, then use that context to improve the
> decision.

A complete schema is not the problem. The failure occurs when a complete stored
model is confused with a complete runtime prompt, or when each concept grows its
own store, workflow, and QA harness.

The next validated shape is therefore:

```text
user-supplied report / existing local records
                 ↓
       one versioned UserDecisionContext
                 ↓
current engine-computed portfolio consequence
                 +
one small context view relevant to this decision
                 ↓
       direct personalized challenge
                 ↓
  candidate context update after the decision
```

The immediate private validation source may be a report generated from the
owner's `investment-note`. The public product must treat it only as a generic,
explicitly supplied local report. FOMO Kernel never crawls, mirrors, or copies a
private repository automatically.

## 2. Product thesis: expert augmentation before guided progression

The product must prove two stages in order.

### Stage A — expert augmentation

Start with the owner, who already has unusually rich context: research history,
portfolio state, transaction reviews, explicit rules, mistakes, beliefs, and
multiple investor reports.

The question is not whether the system can summarize those records. It is:

> Given all this context, can FOMO Kernel help the owner make one materially
> better decision than a strong general agent or the owner alone?

The improvement may be:

- identifying the decision's real bottleneck sooner;
- exposing a contradiction between the current action and the user's own model;
- distinguishing new evidence from a price-triggered explanation;
- separating a strong selection thesis from weak sizing or timing;
- recognizing that apparently different positions repeat the same driver;
- recommending a better process action: proceed, reduce, delay, collect
  evidence, revise the reason, or cancel;
- avoiding a repeated question because the answer is already in context.

If rich context cannot produce a useful difference here, no broader memory or
onboarding product is justified.

### Stage B — guided progression

Only after Stage A identifies which context actually changes decisions should
FOMO Kernel help other users build that context more easily.

The goal is not to make other users copy the owner's holdings, beliefs, or risk
appetite. It is to compress the useful parts of the owner's decision process:

- make beliefs explicit;
- connect actions to portfolio consequences;
- distinguish evidence from price movement;
- preserve why a decision was made;
- compare later outcomes with what was known at the time;
- convert repeated mistakes into one testable improvement focus;
- learn which personal rules deserve to survive.

A less mature user should reach a useful decision context through actual
reviews and decisions, not by completing a comprehensive investor-personality
questionnaire.

## 3. Why the current product can feel less intelligent

The engine has become better at truth, replay, recovery, and portfolio
consequence. Those are necessary capabilities.

But a strong model can still appear less useful when its attention is dominated
by:

- route mechanics;
- schema completion;
- required wording and coverage lists;
- QA receipts and recovery chronology;
- many narrowly owned stores with no current read model;
- repeated questions whose answers are already present in the user's broader
  context.

The resulting answer can be mechanically valid while missing the user's actual
investment model. This is not solved by adding another evaluator or another
memory workflow. It is solved by giving the agent a compact, trustworthy user
context and preserving room for judgment.

Harnesses may observe the product. They must not become the product's reasoning
architecture.

## 4. Three layers are sufficient

The first context-first product needs only three conceptual layers.

### A. Source evidence

Evidence may include:

- a user-supplied investor report;
- transaction and position history already accepted by FOMO Kernel;
- prior reviews and consultations;
- the user's exact statements;
- user-adopted rules;
- later outcomes and verdicts.

Sources remain local. Existing canonical event streams stay authoritative for
the events they own.

### B. Current user context

`UserDecisionContext` is a versioned current projection of what is useful to
know about this user when making decisions.

It is not an immutable event log and not a biography. It may be rebuilt or
superseded from its evidence.

### C. Decision episode

A decision episode combines:

- the user's current contemplated action and reason;
- deterministic current-book consequence;
- the few context claims and policies relevant to this decision;
- the agent's challenge and the user's resolution;
- a proposed update to context when something genuinely changed.

No other product layer is required for the first slice.

## 5. One complete but sparse schema

The schema should define the whole shape while allowing almost every field to
remain absent or `unknown`. Completeness of the schema must never imply
completeness of the user model.

Conceptual contract:

```yaml
schema_version: "1"
context_id: ""
revision: 1
as_of: ""

sources:
  - source_id: ""
    source_type: user_report | transaction_history | review | consultation | user_statement
    reference: "local opaque reference"
    observed_at: ""

claims:
  - claim_id: ""
    kind: capability | belief | decision_pattern | risk_pattern | preference | regime_dependency | portfolio_hypothesis
    statement: ""
    scope: ""
    status: unknown | hypothesis | supported | confirmed | superseded
    provenance: user_declared | behavior_derived | agent_synthesis
    evidence_refs: []
    counter_evidence_refs: []
    confidence: low | medium | high
    user_confirmation: unasked | confirmed | corrected
    first_observed_at: ""
    last_updated_at: ""
    supersedes_claim_id: null

policies:
  - policy_id: ""
    decision_class: ""
    applicability: ""
    policy: ""
    source: user_declared | user_adopted
    status: provisional | standing | superseded | retired
    evidence_refs: []

current_focus: null

open_questions:
  - question_id: ""
    question: ""
    why_it_matters: ""
    evidence_needed: ""
```

This one projection can express what earlier discussions called memory, style,
strategy, improvement focus, and current profile. Those remain useful semantic
labels, not requirements for five product objects.

Period-specific events such as an executed trade or a historical verdict stay
in their existing append-only stores and may be cited by the context. They are
not copied into the projection.

## 6. Why a complete schema can still go wrong

A complete schema is useful only with the following protections.

### Sparse, not form-driven

The product must not ask the user to fill every field. Initial context is
created from the evidence already supplied. Missing areas stay missing.

### Provenance before confidence

A user declaration, a behavior-derived observation, and an agent synthesis are
not interchangeable. The schema must preserve which one produced each claim.

### Claims, not rigid personality slots

A fixed field such as `investor_type: momentum` creates false completeness and
makes contradictory behavior hard to represent. A claim collection permits
multiple scoped and even conflicting hypotheses.

### Current projection, not historical truth

The context is a working model. Superseding a claim must not rewrite the source
report, transaction, prior statement, or old verdict.

### Atomic revision

Updates should produce a new revision of the context after validation. Partial
writes, concurrent patches, ambiguous claim identity, and malformed evidence
references fail closed.

### No full runtime load

Storage may be complete. Runtime context must remain selective. The relevant
view is assembled for the current decision and should normally include only:

- up to three relevant claims;
- up to two applicable policies;
- at most one current improvement focus;
- the unresolved question that most changes the decision.

A poorly designed schema can create loading problems, but loading the complete
schema every time would remain a product error even with a well-designed
schema.

## 7. Bootstrap experience

The first context experience should accept the richest material the user
already has instead of forcing an interview.

### User moment

> “Here is my investor report / historical summary. Understand how I invest so
> you can help with later decisions.”

### Product behavior

1. Read the explicitly supplied local report and any allowed FOMO Kernel state.
2. Produce a draft `UserDecisionContext`.
3. Show a short current understanding, not the schema:
   - strongest supported capability;
   - most consequential recurring risk;
   - central belief or portfolio assumption;
   - one contradiction or uncertainty;
   - one candidate improvement focus.
4. Ask at most one high-leverage correction question, such as which conclusion
   is most inaccurate or which current behavior the user most wants to change.
5. Save the corrected context locally as revision 1.
6. Allow the user to start a decision immediately. No full profile ceremony is
   required.

### Minimum useful outcome

The user can say:

> “The system understands the most important way I invest and the main way I
> tend to lose decision quality.”

This is a context outcome, not yet proof that decisions improve.

## 8. Decision-support experience

When the user later brings a trade, the answer should follow this order.

### 1. Current consequence

Engine-owned facts remain first:

- resulting position and concentration;
- driver overlap;
- cash effect;
- applicable user rule collision;
- data limitations.

### 2. Contextual tension

The agent selects only the context that changes this decision. Examples:

- the action repeats a previously identified risk pattern;
- the current reason conflicts with a user-confirmed policy;
- the proposal depends on a belief the user's report marked as unproven;
- this is the class of company or situation in which the user historically had
  stronger or weaker decision quality.

### 3. One discriminating question

Ask the one question that separates the best-supported interpretations. For
example:

> What new evidence exists now that was not already present in the prior
> thesis, and would you still add if the price had not moved?

### 4. Decision options, not an imposed verdict

The product may make the trade-off explicit and identify reasonable process
options such as proceed, reduce, delay, collect evidence, revise the reason, or
cancel. The final action remains the user's.

### 5. Candidate context patch

After the user resolves the decision, the agent may propose a narrow update:

- confirm or counter one claim;
- record a new user policy;
- update the current improvement focus;
- add an open question;
- leave the context unchanged.

Agent synthesis does not silently become confirmed user context.

## 9. Two user stories

### Advanced user: improve an already strong process

The user already has extensive research and decision records.

1. Supply the existing report.
2. Review and correct the draft context once.
3. Bring one real decision.
4. Receive current portfolio consequence plus the most relevant personal
   tension.
5. Resolve the decision.
6. Update only the claim or policy that genuinely changed.

The product promise is:

> Preserve the user's genuine edge while reducing the recurring execution
> leakage that prevents the edge from compounding.

### Developing user: reach useful context with less effort

The user may have only holdings, transactions, and one current decision.

1. Receive immediate current-decision value; no profile is required first.
2. Preserve the reason and decision outcome from normal use.
3. After several eligible decisions, surface one supported capability or risk
   pattern.
4. Ask the user to confirm or correct it.
5. Propose one bounded policy or improvement focus.
6. Reuse it in the next relevant decision.

The product promise is:

> Compress the work of building a disciplined decision history without asking
> the user to maintain a full investment repository.

## 10. Before and after

### Without context

The product computes the current book correctly but often asks from zero and
produces a challenge that a general assistant could also produce.

The user experience is:

> “It knows my portfolio, but it does not understand how I invest.”

### With context but no decision use

The product can generate an impressive investor profile, but it remains a
report generator. The user experience is:

> “It describes me well, but I do not know what this changes.”

### With context used in the decision

The same current consequence is connected to the user's actual capability,
risk pattern, belief, and chosen policy. The user experience becomes:

> “It remembered the part of my history that matters here and asked a harder,
> more relevant question than I would have asked myself.”

That third state is the product outcome.

## 11. What not to build

This decision deliberately avoids:

- separate Memory, Style, Strategy, ImprovementFocus, and Verdict products;
- a generic knowledge graph;
- mandatory profile questionnaires;
- automatic crawling of a private repository;
- a permanent investor personality enum;
- a durable per-decision context packet;
- one prompt containing the entire context schema and all source reports;
- a memory-specific campaign runner, dashboard, scoring framework, or model
  panel;
- new user-facing routes before one existing decision route proves the context
  changes its answer.

Existing #403/#450 rationale work, #460 verdicts, and #650 rules may later enrich
or update `UserDecisionContext`. They are not prerequisites for the initial
report bootstrap.

## 12. Minimal validation without another harness

Validation has two separate questions.

### Context quality

Using one private owner report locally or a synthetic public equivalent:

- are major claims grounded in sources?
- are fact, user statement, and inference separated?
- does the context preserve uncertainty and counter-evidence?
- can the owner identify and correct a wrong conclusion?

This can be reviewed directly. It does not require an LLM judge panel.

### Decision usefulness

Run one real or synthetic decision twice using the same frozen current facts:

- once with no user context;
- once with the relevant context view.

The context earns its place only if it creates a named useful difference, such
as:

- avoids a repeated question;
- exposes a conflict with the user's own model;
- identifies a relevant prior strength or failure pattern;
- changes what evidence is requested;
- changes the user's process action: proceed, reduce, delay, cancel, or collect
  evidence.

Use one focused route fixture and one owner verdict. Do not extend #718 or build
a context-specific harness. If the difference is not useful, revise the context
or decision prompt rather than expanding evaluation infrastructure.

## 13. Smallest sequence

The ordering below is architectural and does not override #27.

### Step 0 — stop adding product surface

Finish the current M1 repair boundary. Do not add another memory route, card,
questionnaire, or QA platform.

### Step 1 — private context prototype

Use the owner's existing reports to produce one `UserDecisionContext` locally.
No public private-data commit and no product integration yet.

Review only:

- what context is genuinely decision-relevant;
- what is unsupported or contradictory;
- which parts are stable enough to carry forward;
- which one current improvement focus is highest leverage.

### Step 2 — apply it to one real decision

Combine the frozen current `TradeEvaluation` with the relevant context view.
Observe whether the answer and the user's process improve.

This is the first product proof. It comes before more memory infrastructure.

### Step 3 — freeze the minimum schema

Remove every field the prototype did not use. Keep one complete sparse schema
for the remaining context.

### Step 4 — add one local bootstrap/update path

Accept an explicitly supplied report, produce the context, show the short
summary, allow one correction, and persist one atomic revision.

### Step 5 — integrate one existing decision route

Use the context in one `consider` / add-or-new-risk flow. Do not expand to every
review route in the same change.

### Step 6 — learn from actual use

Only after repeated use should existing rationale, verdict, and rule streams
automatically propose context updates. Add a new store or object only when this
single context projection cannot represent an observed product need.

### Step 7 — productize the context-building path

After the owner loop repeatedly improves decisions, identify the minimum inputs
that produced the useful context and make those available to developing users.
Do not copy the owner's profile. Productize the learning process.

## 14. Ownership and relation to current work

| Concern | Owner / relation |
|---|---|
| Current M1 implementation queue | #27 |
| `UserDecisionContext` design and distillation projection | #446 |
| Staged decision-memory roadmap and privacy boundary | #475 |
| Later exact rationale evidence | #403 / #450; optional enrichment, not bootstrap prerequisite |
| User-owned policies | #650 |
| Historical verdict evidence | merged PR #460; optional enrichment |
| M1 synthetic QA trace/accounting | #718; not a dependency and must not be extended for this work |

## 15. Acceptance for this decision

Before implementing any context feature, a contributor must answer:

1. What user-supplied evidence initializes the context?
2. Which claims are fact, user declaration, or agent synthesis?
3. What remains unknown or contradicted?
4. Which existing decision route reads the context immediately?
5. Which part of the answer changes because context exists?
6. What can be removed or avoided instead of adding another workflow?
7. How can the owner correct the model?
8. What focused existing test proves identity, provenance, and update safety?
9. Which part of the successful owner workflow can later be offered to a user
   with less context?

If the context does not improve a real decision, the next step is not more
memory or more harness. The next step is to revise what context the agent sees
and how it uses that context.