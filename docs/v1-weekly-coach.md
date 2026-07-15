# v1 design: recurring review and thin local state

Status: historical design whose core has been superseded by the v2 canonical-session architecture.

## Product model

The recurring coach has three behaviors:

1. First review: identify one costly behavior and let the user choose one rule.
2. Returning review: reconcile that rule against new evidence before discussing a new leak.
3. Improvement: graduate or replace a rule only after actual opportunities to violate it.

## Thin-state principle

State exists to support future review, not to become a second portfolio wiki. Preserve:

- metric snapshot and data coverage
- active and prior commitments
- thesis and motive history by cycle
- review status and immutable session artifacts

Do not store raw trade data in memory documents or create a growing narrative profile.

The current implementation uses `sessions/<session_id>/bundle.json` as canonical state and legacy JSONL files as projections. This replaces the original proposal for one mutable `profile.json`.

## First review

- Run the deterministic engine.
- Ask every required motive question.
- Create inferred theses where history is absent.
- Show one private preview.
- Let the user choose, rewrite, or skip one rule.
- Commit atomically.

## Returning review

- Load bounded prior state through the Review Plan.
- Reconcile the prior commitment first.
- Distinguish `passed`, `failed`, and `skipped/no opportunity`.
- Do not ask confirmed motives again unless a new cycle or new contradiction justifies it.
- Preserve the same rule when it remains the largest unresolved leak.

## Graduation

Naive consecutive-review counts are insufficient. A rule should become a graduation candidate only when:

- the relevant opportunity occurred enough times
- the metric met an absolute threshold, not merely improved from a worse baseline
- no hidden regression is masked by inactivity
- the user confirms graduation

## Metric binding

Every rule binds to an engine metric and target. The engine or validator owns the value; the agent does not calculate it. Baselines must be explicit, and short samples must remain labeled as such.

## Boundaries

- One active rule at a time.
- Event-driven review, not calendar nagging.
- No security recommendations.
- Local-only state.
- No duplicate lifecycle for different agents or lenses.
- Multi-lens selection may affect questions and prose but not facts, state, or persistence.

## Acceptance

- A second review references the prior commitment before a new leak.
- A week with no relevant opportunity is skipped rather than counted as success.
- A user-selected rule, not an engine default, is the stored commitment.
- Recovery never requires rebuilding a committed session from chat memory.
