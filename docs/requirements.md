# fomo-kernel requirements

Status: living requirements source. Original decision date: 2026-06-20. Implementation status was refreshed for the v2 architecture on 2026-07-14.

## Product outcome

Combine the useful decision-review capabilities of the earlier record-trade workflow into fomo-kernel so the same core can support a distributable product and a richer owner workflow.

The product is a process coach, not a stock advisor. Its defining output is a card grounded in the user's own account data that identifies a costly repeated behavior, preserves thesis evolution, and creates one rule that can be checked later.

## Requirements

### Diagnosis

| ID | Requirement |
|---|---|
| R1 | Identify repeated behavioral leaks from real user numbers: sizing, losing-position adds, exits, holding horizon, and true diversification. |
| R2 | Deterministically calculate facts the user should not have to calculate: FIFO P&L, payoff, benchmark comparison, and allocation versus selection. |
| R3 | Converge on one progress card with one largest leak and at most one rule. |
| R4 | Allow philosophy lenses to change motive questions without changing mechanical facts. |
| R5 | Fail honestly when evidence, sample size, prices, benchmark coverage, or metadata is insufficient. |

### Memory and continuity

| ID | Requirement |
|---|---|
| R6 | Remember the prior leak, rule, motive answers, and active theses. |
| R7 | Reconcile the next review against the prior commitment before opening a new topic. |
| R8 | Track decision and thesis evolution across months and years. |
| R9 | Show whether the behavior improved, worsened, or remained unresolved. |

### Constraints

| ID | Requirement |
|---|---|
| R10 | Keep trade data and derived state local. |
| R11 | Coach decision process without recommending a security. |
| R12 | Accept broker data without requiring the user to normalize it. |
| R13 | Refuse unsupported conclusions and allow the user to skip a commitment. |

### Product shape and validation

| ID | Requirement |
|---|---|
| R14 | Support a distributable surface and an owner surface from one core contract. |
| R15 | Treat the distributable surface as a constrained subset of the owner surface. |
| R16 | Absorb useful functions from record-trade without importing its governance-heavy document shape. |
| R17 | Validate that a real user finds the card specific and useful. |
| R18 | Make the second review materially better because the system remembers the first. |

## First-principles decision: continuity is core

Investment review is longitudinal. A stateless card repeats the same diagnosis without knowing whether the user followed the previous rule. Therefore memory is not a retention add-on; it is required for the product to qualify as a review loop.

Continuity is event-driven, not reminder-driven. The system should remember when the user returns, not create a calendar nag. A new review is justified by sufficient new decisions, a due thesis check, or an explicit user request.

## State model

Model transitions before file formats:

- Thesis or decision: `open -> still | modified | falsified | closed`, with `due`, `skipped`, and `insufficient` side states.
- Rule: `active -> candidate -> graduated`, where eligibility requires actual opportunities to violate the rule.
- Source attribution: `captured -> confirmed -> evaluated`.

Important constraints:

- Capture thesis and source evidence when the decision is made; it cannot be reconstructed reliably months later.
- Preserve holdings and decision cycles even when a closed ticker disappears from the latest CSV.
- A short first sample or skipped motive may produce no commitment.
- Use schema versions, atomic writes, immutable session bundles, and rebuildable projections.
- Graduation should combine code-computed eligibility with explicit user confirmation.

## Shared-core product architecture

The distributable surface uses recap and a thin update loop with local state. The owner surface may later add research or source context, but both use the same mechanical engine, thesis schema, session lifecycle, and card contract.

Selection and research features remain outside the public coaching boundary. Feature flags may restrict a surface, but no shared engine path should emit a security recommendation.

## Absorbed capabilities

Keep:

- thin transaction and position ledger
- compact thesis record with falsifier and sizing intent
- due revisit cadence
- post-exit and swap opportunity-cost analysis

Do not import:

- a large portfolio-governance document
- weekly long-form journal requirements
- protocol complexity unrelated to the current lifecycle

## Current implementation

As of 2026-07-16, v2 implements the core review lifecycle, canonical atomic sessions, append-only thesis decisions, prior-commitment reconciliation, frozen market/timeline context, exit and problem-ledger follow-up on the private card, public/private rendering, ETF policy, and projection repair. The snapshot route still needs a complete adapter, and multi-lens selection remains P1.

The canonical implementation references are `skills/fomo-kernel/SKILL.md`, its routed flows and references, and `docs/skill-v2-architecture.md`.
