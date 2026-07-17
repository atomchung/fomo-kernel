# P1 design: lens selection, integrity gate, and comparison

Status: post-release research, not current v2 behavior. Current v2 does not load `rubric/` or expose a runtime lens persona.

## Core separation

Lens selection and integrity are different systems:

- **Selection** chooses which philosophical frame the user wants to consult.
- **Integrity** decides whether a lens is allowed to change the interpretation of a detected behavior.

Universal engine facts stand on their own: they remain visible, are never attributed to a lens, and cannot be excused by a selected philosophy. Only a style-dependent, confidence-bearing signal with an explicit lens stance may produce a lens-specific interpretation.

## Shared pipeline

```text
engine facts
  -> universal behavior checks, rendered independently
  -> optional style observations with confidence
  -> explicit selected-lens stance and lean, or omit the lens-specific branch
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
2. Render any universal fact independently. Do not attach it to, label it as, or derive it from a lens.
3. Require enough data and confidence for a separate style observation.
4. Read the selected lens's explicit `stance` and `lean` for that style dimension.
5. If the lens has no stance, omit the lens-specific interpretation for that dimension. Do not synthesize a fallback or relabel the universal fact as the lens's view.
6. If several lenses agree, show one interpretation rather than artificial debate.
7. If they genuinely disagree, present the smallest useful fork and ask the user to defend the intended strategy.

## Lens schema

Each dimension should define:

- `stance`: `aligned`, `conditional`, `inverted`, or `unconditional`
- `lean`: a machine-readable direction such as `strength`, `weakness`, `barbell`, or `evidence`
- grounded principle and source status
- motive question template
- candidate rule framing

Source status must distinguish paraphrase, interpretation, and cross-domain analogy. Paraphrased material keeps source provenance but must not be presented as a quotation or a runtime persona.

## Edge cases

- A momentum user who buys strength should not be diagnosed as chasing solely because entry is near a high.
- A value user who adds after price weakness still fails if size escalates without evidence or falsifier.
- Intentional concentration remains subject to drawdown and driver-risk facts.
- Unknown or low-confidence style stays an observation and cannot alter the top conclusion.

## Rollout

1. Finish source-provenance and licensing review for a small set of contrasting paraphrased principle sets.
2. Implement one style axis, initially entry relative position.
3. Add deterministic stance/lean contract tests.
4. Run differential user cases where the same facts receive legitimately different questions.
5. Only then expose multi-lens selection in GTM.

## P1 acceptance

- Lens choice changes only contextual question and prose surfaces, never engine facts.
- Every changed interpretation cites a confident style observation.
- A missing `stance` omits the lens-specific interpretation for that dimension.
- Universal risk findings remain visible independently and are never attributed to a lens.
- One-card convergence remains intact.
- Public wording is source-linked paraphrase and is never presented as a quotation or persona endorsement.
- The complete P0 lifecycle and recovery suite remains green.
