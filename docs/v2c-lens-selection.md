# P1 design: lens selection, integrity gate, and comparison

Status: planned after the 2026-07-19 P0 release.

## Core separation

Lens selection and integrity are different systems:

- **Selection** chooses which philosophical frame the user wants to consult.
- **Integrity** decides whether a lens is allowed to change the interpretation of a detected behavior.

Universal behavioral loss mechanisms cannot be excused by a selected philosophy. Only a style-dependent, confidence-bearing signal may produce a divergent lens interpretation.

## Shared pipeline

```text
engine facts
  -> universal behavior checks
  -> optional style observations with confidence
  -> selected lens stance and lean
  -> motive question or comparison
  -> same validator, lifecycle, and renderer
```

Lens selection does not create separate engines, schemas, sessions, or card implementations.

## Selection

- Offer a small verified set rather than the entire draft library.
- Store the chosen lens as a preference, not a permanent identity.
- Allow the user to change or disable it.
- Use GTM to explain the difference between lenses, but keep implementation contracts English-only.

## Integrity gate

For each behavior dimension:

1. Determine whether the engine signal is universal or style-dependent.
2. Require enough data and confidence for a style observation.
3. Read the selected lens's explicit `stance` and `lean`.
4. If the lens has no stance, fail closed to the universal interpretation rather than silently disabling the check.
5. If several lenses agree, show one interpretation rather than artificial debate.
6. If they genuinely disagree, present the smallest useful fork and ask the user to defend the intended strategy.

## Lens schema

Each dimension should define:

- `stance`: `aligned`, `conditional`, `inverted`, or `unconditional`
- `lean`: a machine-readable direction such as `strength`, `weakness`, `barbell`, or `evidence`
- grounded principle and source status
- motive question template
- candidate rule framing

Source status must distinguish verified quotation, paraphrase, interpretation, and cross-domain analogy.

## Edge cases

- A momentum user who buys strength should not be diagnosed as chasing solely because entry is near a high.
- A value user who adds after price weakness still fails if size escalates without evidence or falsifier.
- Intentional concentration remains subject to drawdown and driver-risk facts.
- Unknown or low-confidence style stays an observation and cannot alter the top conclusion.

## Rollout

1. Finish source verification for a small set of contrasting lenses.
2. Implement one style axis, initially entry relative position.
3. Add deterministic stance/lean contract tests.
4. Run differential user cases where the same facts receive legitimately different questions.
5. Only then expose multi-lens selection in GTM.

## P1 acceptance

- Lens choice changes only contextual question and prose surfaces.
- Every changed interpretation cites a confident style observation.
- Universal risk findings remain visible.
- One-card convergence remains intact.
- Public quotations are source-verified.
- The complete P0 lifecycle and recovery suite remains green.
