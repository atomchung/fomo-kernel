# Agent boundaries

Keep agent flexibility in high-value contextual judgment. Constrain repeatable facts and workflow mechanics in code.

The agent may:

- Understand brokerage-specific fields and normalize them locally.
- Transcribe broker-declared position facts from a table or screenshot into the normalized snapshot envelope, including date and symbol-format normalization, and keep the temporary envelope outside the repository.
- Use world knowledge to propose a driver map or instrument map. Mark uncertainty as unknown rather than pretending certainty.
- Interpret motive answers and evidence deltas.
- Customize a grounded private question surface only for an engine-selected `add_thesis`, `headline_motive`, `initial_thesis`, or `exit_consistency` opportunity, then map it back to the unchanged canonical choices through `references/interaction-delivery.md`.
- Write an inferred hypothesis for a position without a thesis.
- Write the headline, mirror, counterfactual, and rule rationale.
- Add observations that do not silently replace the engine's top conclusion.

The agent may not:

- Calculate or alter numbers, rankings, weights, P&L, cycle IDs, metrics, driver concentration, or ETF allocation exemptions. Transcription is allowed; derived analysis is not.
- Skip required questions, answer for the user, or represent an inference as confirmed.
- Change a question's route, kind, trigger, priority, required status, queue position, canonical choices, payload requirements, numeric facts, identity, or validation; add a surface to an engine-rendered question kind; or ask more than one clarification.
- use polished prose to bypass a missing claim or source for `new_evidence`.
- Assemble engine card/state artifacts by hand, append several JSONL files directly, and claim an atomic completion.
- Upload a statement or screenshot for OCR. Snapshot transcription stays local; the engine accepts only the normalized JSON envelope through `review.py`.
- Treat an incomplete snapshot as an accounting anchor, or claim that later transaction import reconciles a fresh broker view. Ledger-derived current holdings remain canonical until an explicit snapshot reconciliation succeeds.
- Put private data into a public card.
- Call another `engine/*` script or import engine modules directly. Invoke the engine through the `engine/review.py` CLI (`prepare`, `resume`, `preview`, `finalize`, `capture`, `render`, `repair-projections`, `set-cap`, or `doctor`) so lifecycle validation, required-question gates, and canonical session state remain authoritative. `capture` is the one exception to "assemble artifacts by hand, append JSONL directly, and claim an atomic completion" above — it is itself the sanctioned append path for a light-tier (`cadence.tier == "light"`) review; see `flows/light-capture.md`.

If a new observation could overturn the top behavioral leak, add it to `observations` and rerun preview. Do not mutate the engine artifact. This preserves analytical flexibility while keeping conclusion changes inside the same validator and renderer path.
