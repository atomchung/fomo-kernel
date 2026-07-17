# Agent boundaries

Keep agent flexibility in high-value contextual judgment. Constrain repeatable facts and workflow mechanics in code.

The agent may:

- Understand brokerage-specific fields and normalize them locally.
- Use world knowledge to propose a driver map or instrument map. Mark uncertainty as unknown rather than pretending certainty.
- Interpret motive answers and evidence deltas.
- Write an inferred hypothesis for a position without a thesis.
- Write the headline, mirror, counterfactual, and rule rationale.
- Add observations that do not silently replace the engine's top conclusion.

The agent may not:

- Calculate or alter numbers, rankings, cycle IDs, metrics, or ETF allocation exemptions.
- Skip required questions, answer for the user, or represent an inference as confirmed.
- use polished prose to bypass a missing claim or source for `new_evidence`.
- Assemble state by hand, append several JSONL files directly, and claim an atomic completion.
- Put private data into a public card.
- Call another `engine/*` script or import engine modules directly. Invoke the engine through the `engine/review.py` CLI (`prepare`, `resume`, `preview`, `finalize`, or `repair-projections`) so lifecycle validation, required-question gates, and canonical session state remain authoritative.

If a new observation could overturn the top behavioral leak, add it to `observations` and rerun preview. Do not mutate the engine artifact. This preserves analytical flexibility while keeping conclusion changes inside the same validator and renderer path.
