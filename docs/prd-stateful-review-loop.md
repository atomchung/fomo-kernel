# PRD: stateful review loop

Status: implemented in v2 with canonical sessions and compatibility projections. Original concept date: 2026-06-20.

## Problem

A stateless review can detect new behavior but cannot determine whether an earlier behavior improved. Repeated runs may produce the same rule while changing numbers simply because the sample grew or market prices moved.

The core product question is longitudinal: the previous review identified a metric and the user chose a rule; what changed when the user returned?

## Goals

- Reconcile every returning review against the prior commitment.
- Reuse confirmed motive and thesis history without asking the same question again.
- Preserve a longitudinal metric and decision trail.
- Keep state local and recoverable.
- Keep the mechanical engine responsible for facts and the orchestration layer responsible for lifecycle.

## Non-goals

- No pre-trade execution gate in this phase.
- No cloud account or synchronization system.
- No security recommendations.
- No calendar-driven reminder system.

## Canonical state

Each completed review stores an immutable session bundle containing:

- engine card and state snapshot
- Review Plan
- user answers
- qualitative narrative
- thesis and add-decision events
- user-chosen commitment
- private and public card artifacts
- manifest hashes

`sessions/<session_id>/bundle.json` is canonical. Legacy JSONL files and card folders are rebuildable projections.

## Lifecycle

### First review

1. Prepare engine facts and required questions.
2. Obtain motive answers and create inferred theses for uncovered cycles.
3. Preview one card.
4. Let the user choose, rewrite, or skip one rule.
5. Atomically commit the canonical session.

### Returning review

1. Load the Review Plan's bounded `state_snapshot` rather than scanning all local files.
2. Recompute current facts from new input.
3. Reconcile the prior commitment metric with the current engine state.
4. Ask only deduplicated questions caused by new cycles, new adds, or unresolved high-cost contradictions.
5. Produce a progress card and atomically commit the new session.

## Progress-card behavior

- If the prior rule improved but is still outside target, say so and keep the same topic when it remains the largest leak.
- If the prior rule held, acknowledge it before selecting a new leak.
- If the sample was too short or the user skipped, preserve a baseline without pretending there was a commitment.
- Use engine-owned values; the agent must not calculate the delta.

## Recovery

- Interrupted prepare or unanswered conversation: resume from `.pending/<session_id>` without refetching prices.
- Failure before canonical rename: retry the pending session.
- Failure after canonical rename: repair projections from the bundle without questioning the user again.
- Identical retry: no-op. Conflicting retry under the same session ID: fail closed.

## Acceptance criteria

- The first completed run creates one canonical session and one private card.
- A second run starts with the prior commitment context.
- Required questions are not repeated when active thesis state already answers them.
- Projection failure cannot destroy or invalidate a committed session.
- Private data never enters the public card or a cloud memory system.
