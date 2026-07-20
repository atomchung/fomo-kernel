# fomo-kernel evaluation design

This specification evaluates the layer between deterministic engine math and real-user value: whether an agent follows the review contract, creates correct artifacts, and produces a useful card.

Engine formulas are tested in the standard unit suites. Real-user usefulness is measured separately through actual reviews. This document owns automated workflow and artifact assertions; `evals/EVALS.md` is the compact manual checklist.

## Evaluation surfaces

One run has four observable surfaces:

1. Review Plan, frozen question presentations, content-free interaction receipts, and conversation trajectory.
2. Canonical session bundle and manifest.
3. Private and public cards.
4. Rebuildable compatibility projections.

All four matter. A readable card with corrupt state fails, and correct state with skipped motive questions also fails.

## Evidence hierarchy

1. Deterministic code assertion.
2. Differential fixture assertion.
3. LLM narrative judge.
4. Human review.

Use the strongest cheaper layer that can answer the question. Do not ask a model to judge a schema, hash, privacy field, or required-question gate.

Non-deterministic agent runs should be repeated when measuring adherence. Deterministic lifecycle tests need only one run per case because identical input must produce identical contract behavior.

## Harness

```text
tests/test_review_v2.py       lifecycle, evidence, ETF, language, privacy, recovery
tests/test_doc_language.py    implementation/GTM language boundary
tests/agent/check_card.py     legacy and artifact-level card invariants
tests/agent/check_state.py    projection and trajectory helpers
tests/agent/personas.md       scripted users and differential pairs
tests/agent/cases/*.yaml      optional headless declarations
tests/agent/judge_narrative.py optional prose-quality judge
```

The complete deterministic suite runs through `python3 tests/run_all.py`. Headless agent generation and LLM judging are opt-in because they are non-deterministic and may cost money.

## P0 lifecycle assertions

### Prepare

- Uses `engine/review.py prepare` as the canonical entry point.
- Produces a schema-valid Review Plan.
- Selects one route and a bounded flow path.
- Emits a deduplicated required question queue, ranked by engine-owned amount or P&L impact and capped at three items; recent-exit questions deduplicate against canonical sessions, and a saved capture `skip` is never re-asked.
- Perishable exit-reason captures (at most two per session) outrank matured `due_revisit` checkpoints and add questions regardless of notional; a due question replays the recorded reason from the kind-aware copy table, its non-skip verdict persists as a queue resolution, and its `skip` is not saved so the checkpoint returns.
- Weaves the cycle's recorded thesis into add and exit-capture stems verbatim with voice fidelity (confirmed as the user's words, inferred as a guess) and emits `asked_because`; a cycle without a recorded thesis keeps the plain stem byte-identical.
- Emits an engine-owned `question_opportunity` only for `add_thesis` and `headline_motive`. Its canonical choices, order, payload requirements, required status, identity, queue rank, and single-clarification budget remain deterministic; due revisits, rule breaches, and recent exits stay engine-rendered.
- Accepts only a grounded private stem plus one-to-one surface labels/descriptions, freezes the validated presentation in pending state, and returns it byte-identically on resume. Missing, reordered, duplicate, invented-fact, semantics-changing, or otherwise invalid surfaces fall back to the existing engine stem/options without blocking the review.
- Frozen market context, engine-owned exit/swap prices, and ranked horizon markers enter the plan once; resume and rendering never refetch or recalculate them, and the public card never consumes their private fields.
- Problem-book stats and exact-period rule-breach evidence fold into the plan read-only; events and the weekly Opportunity Check mark append only at finalize through projections and round-trip through `load_book`. A breach decision is canonical, first-or-worsening deduplicated, and a rule replacement can only be the review's one final commitment with `revises` linkage.
- Re-asks a confirmed motive only when the engine-owned per-cycle decision cursor advances (another add in that same cycle); thesis identity (`thesis_id`, `event_id`, `revises`) is engine-assigned, content-addressed, and stable across update order, resume, and projection loss.
- In persist mode, validates every normalized CSV before the first ledger write; cash-flow rows (deposits, dividends, interest, fees, reinvest notices) in the same file are counted and reported, never fatal — only future-dated rows reject the import.
- Creates a pending session with a stable fingerprint.
- Stamps a fail-safe `engine_version` provenance marker (a committed VERSION file, else the git short SHA plus a dirty flag, else `unknown`) onto the plan and carries it into the bundle and the private HTML card `<meta>`. It is pure metadata: it never enters narrative, numeric facts, or the public card.
- Repeated prepare or resume does not refetch prices or re-ingest trades for the same pending review.

### Agent artifacts

- Every required question has an explicit answer before preview.
- Every uncovered cycle has a thesis update tied to the unchanged engine cycle ID.
- Inferred theses use `maturity:"inferred"` and never claim user confirmation.
- Narrative is qualitative and contains no digits.
- A `new_evidence` choice requires claim and source.
- Native and text clients present the same frozen surface digest and write the same canonical value. Their presentation trace contains only delivery mode, surface source, and digest; it contains no question, trade, thesis, answer, or clarification copy.
- An own-words answer preserves the exact private statement and explicitly attributed AI interpretation. A resolved mapping requires user confirmation; an unresolved mapping becomes low-confidence `skip`, and the schema permits at most one clarification.
- The agent never calculates numbers or ETF exemptions.

### Preview

- Validation failures identify the artifact to fix and do not mutate canonical state.
- Private and public previews render from the same engine facts.
- Preview contains at most one proposed commitment surface and does not finalize it automatically.

### Finalize

- Requires a user choice: candidate rule, custom rule, or skip.
- Commits one immutable canonical bundle by atomic directory rename.
- Writes a manifest hash for every artifact.
- Identical retry is a no-op.
- Conflicting content under the same session ID fails closed.
- Projection failure cannot invalidate the canonical session.
- `repair-projections` rebuilds compatibility files without asking the user again.

### Snapshot review

- Claims only structural facts a position snapshot supports — cost or value weights, single-position risk, driver concentration, ETF structure, and data integrity — and never a transaction-history dimension such as averaging down, exit discipline, win rate, payoff, alpha, or historical motives.
- Returns an empty question queue for this route instead of inventing a motive question, because a snapshot carries no action history.
- Produces a bounded review from an incomplete snapshot (`is_complete:false`) without treating it as the local accounting anchor; only a complete initial snapshot may become one.
- Makes ledger-derived current holdings canonical once later transaction history is imported; a disagreement with the raw snapshot view on tickers, shares, market, currency, or cost basis fails closed under the `accounting_reconciliation` honesty key on every current-view claim (sizing, diversification, unrealized P&L, ETF weights) instead of trusting either source silently.
- Renders the public card with only fixed structural-baseline copy for this route, never a behavior-pattern line, so it cannot imply transaction-history behavior the review did not score.

## Thesis and evidence assertions

- Losing-position adds use only the defined decision enum.
- `new_evidence` contains a claim and source and may include observation time or falsifier.
- A cheaper price alone maps to valuation change or price only, not new evidence.
- A revision appends a new event with `revises`; old events remain unchanged.
- New position cycles receive new thesis identities.
- Active-thesis reconstruction ignores unrelated event types.

## ETF assertions

- Broad-market, regional, bond, and commodity ETFs may receive the explicit diversified-allocation exemption.
- Sector, thematic, and leveraged ETFs remain concentrated risk.
- Unknown tickers receive no exemption.
- Allocation exemptions affect sizing, risk concentration, single-name stress, and decision-exit logic consistently.
- Missing expense ratio or tracking error is disclosed, never set to zero.

## Card assertions

### Private card

- Uses only engine-owned numeric facts.
- Shows a strength before the largest leak.
- Converges on one largest leak and at most one rule.
- Integrates every triggered honesty-ledger limitation.
- Contains no raw question queue, internal field names, or five-dimension dashboard.
- Gives no security recommendation.
- Renders both a canonical Markdown artifact and a self-contained HTML artifact from one structured assembly, carrying identical engine numbers; the HTML makes no external request, exposes exactly one `WIDGET-FRAGMENT` block, and draws a P&L sparkline only when the curve has at least two finite points (missing or note-form curves omit it without a new caveat).
- Is delivered, never re-summarized in chat, per `references/card-delivery.md`: graphical surfaces render the widget fragment and fall back to verbatim Markdown; a snapshot route carries no performance section or sparkline.

### Public card

- Is independently rendered rather than redacted from private prose.
- Contains no amount, share count, exact date, ticker, exact weight, session ID, evidence text, or agent-authored free prose.
- Shows a final rule only when the user selected one.

### Test drive

- Uses `persist:false`.
- Never reads or writes production coach state.
- Labels conversation and cards as demo data.
- Follows the same required-question and commitment lifecycle.

## Differential personas

Run the same mechanical facts with different user answers:

- `washer` versus `honest`: vague rationalization must not satisfy the evidence gate.
- planned pyramid versus averaging-down answer: thesis event and rule framing differ.
- intentional theme concentration versus believed diversification: facts stay fixed while motive framing differs.
- returner versus first review: the returning run reconciles the prior commitment.
- skip versus choose: commitment artifacts differ without breaking card production.

Differential tests prove that the conversation affects permitted qualitative state without allowing the user or agent to rewrite mechanical facts.

For question surfaces, hold the event, queue rank, required status, canonical choices, payload gates, and identities fixed while changing only the confirmed prior thesis. The validated stems and contextual surface labels should differ; every engine-owned field and canonical answer remains identical.

## Narrative judge

Use an LLM judge only for prose qualities that deterministic checks cannot settle:

- coherent story rather than report fragments
- specific strength before critique
- direct and non-shaming language
- concrete rule rationale
- no tacked-on philosophical lecture

Calibrate the judge against human ratings. If agreement is poor, improve the rubric rather than treating the score as truth.

## Mutation testing

Every important guard should fail under an intentional mutation at least once. High-value mutations include:

- allow preview before required answers
- accept `new_evidence` without source
- add a digit to agent narrative
- let unknown ETFs receive an allocation exemption
- leak a ticker into the public card
- reorder or duplicate a private surface mapping
- remove a payload requirement or required status through a surface mutation
- add an ungrounded numeric fact or a second clarification
- interrupt projection after canonical commit
- retry one session with conflicting content
- place non-English text in implementation Markdown

A checker that stays green under its matching mutation is not evidence.

## Real-user feedback loop

Automated success does not prove that the card matters. After a real review, record a lightweight local verdict:

```json
{"date":"2026-07-14","verdict":"miss","line":"exact card sentence","why":"one concise reason","tag":"not heard"}
```

Keep raw feedback local because it may contain real tickers or amounts. Convert only the failure structure into a synthetic regression case.

The host interaction itself has a separate local presentation trace. `skills/fomo-kernel/tools/ux_receipt.py` records capability modes, presentation events, and artifact paths inside the protected state directory (`~/.trade-coach/ux/`), the same trust boundary as the ledger, so placement rather than content scrubbing keeps trade data safe. A complete trace proves the preview and final cards were each presented after they were generated, a declared widget that failed degraded to inline canonical Markdown, and the weekly opening memory was surfaced before the first card. It deliberately distinguishes `artifact_generated` from `card_presented`; only the latter is evidence about what the user could see. Answer and commitment completeness remain the engine's job at preview and finalize.

For cross-client owner dogfood, follow `tests/agent/manual-cross-client-ux.md` and require `owner_verdict`. Automated trace checks prove the trajectory shape, while the owner verdict answers the product question: whether the controls felt usable, the card was actually legible, and the weekly review felt remembered.

For the first question-surface slice, owner dogfood must also rate whether the stem felt specific and whether one of the available answers fit. Test success proves containment and lifecycle correctness, not product usefulness.

For each miss:

1. Determine whether the cause is missing instruction, poor adherence, conflicting instructions, or a wrong product rule.
2. Add or update the smallest synthetic case.
3. Change one contract surface.
4. Run the complete deterministic suite and the relevant agent eval.
5. Recheck a real card.

## Run cadence

| Trigger | Required evidence |
|---|---|
| Engine, schema, renderer, or lifecycle change | complete deterministic suite |
| Skill or policy wording change | complete deterministic suite plus relevant scripted cases |
| Model upgrade | repeated agent evals across the scripted set |
| GTM release | both locale demos plus human public-card privacy check |

Do not put non-deterministic, billable agent runs in default CI. Do not weaken assertions merely to reduce flakiness.
