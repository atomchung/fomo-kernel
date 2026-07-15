# fomo-kernel evaluation design

This specification evaluates the layer between deterministic engine math and real-user value: whether an agent follows the review contract, creates correct artifacts, and produces a useful card.

Engine formulas are tested in the standard unit suites. Real-user usefulness is measured separately through actual reviews. This document owns automated workflow and artifact assertions; `evals/EVALS.md` is the compact manual checklist.

## Evaluation surfaces

One run has four observable surfaces:

1. Review Plan and conversation trajectory.
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
- Emits a deduplicated required question queue.
- Creates a pending session with a stable fingerprint.
- Repeated prepare or resume does not refetch prices for the same pending review.

### Agent artifacts

- Every required question has an explicit answer before preview.
- Every uncovered cycle has a thesis update tied to the unchanged engine cycle ID.
- Inferred theses use `maturity:"inferred"` and never claim user confirmation.
- Narrative is qualitative and contains no digits.
- A `new_evidence` choice requires claim and source.
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
