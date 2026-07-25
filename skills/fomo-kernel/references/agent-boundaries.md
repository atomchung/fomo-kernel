# Agent boundaries

The split is not "code is trustworthy, the agent is not." It is that facts must be **reproducible across weeks** for reconciliation to mean anything, while judgment is exactly what a deterministic engine cannot supply. Keep repeatable facts and workflow mechanics in code; spend the agent's latitude on contextual judgment.

The agent may:

- Understand brokerage-specific fields and normalize them locally.
- Transcribe broker-declared position facts from a table or screenshot into the snapshot envelope, including date and symbol normalization, keeping the temporary file outside the repository.
- Use world knowledge to propose a driver map or instrument map, marking uncertainty as unknown rather than pretending certainty.
- Interpret motive answers and evidence deltas.
- Author a grounded private question surface for an engine-selected `add_thesis`, `headline_motive`, `initial_thesis`, or `exit_consistency` opportunity, then map it back to the unchanged canonical choices through `references/interaction-delivery.md`.
- Write an inferred hypothesis for a position without a thesis.
- Write the headline, mirror, counterfactual, rule rationale, and the optional closing synthesis.
- Add observations that do not silently replace the engine's top conclusion.

The agent may not:

- Calculate or alter numbers, rankings, weights, P&L, cycle IDs, metrics, driver concentration, or ETF allocation exemptions. Transcription is allowed; derived analysis is not. An agent-computed figure is not reproducible next week, and the whole memory loop rests on this week's number and next week's number meaning the same thing.
- Skip a required question, answer on the user's behalf, or present an inference as confirmed. The engine can tell that an answer is missing; it cannot tell that an answer was invented.
- Change a question's route, kind, trigger, priority, required status, queue position, canonical choices, payload requirements, numeric facts, identity, or validation; add a surface to an engine-rendered question kind; or ask more than one clarification.
- Show, echo, or append an internal engine value or schema field name in a question, option, or rule prompt — a canonical choice key (`planned_entry`, `anxiety`) beside its label, or the `commitment` field surfaced as "Commitment Rule". User-facing surfaces use domain language only; see `references/interaction-delivery.md`.
- Assemble engine card or state artifacts by hand, append several JSONL files directly, and claim an atomic completion.
- Upload a statement or screenshot for OCR. Snapshot transcription stays local; the engine accepts only the normalized JSON envelope through `review.py`.
- Treat an incomplete snapshot as an accounting anchor, or claim that a later transaction import reconciles a fresh broker view. Ledger-derived current holdings stay canonical until an explicit snapshot reconciliation succeeds.
- Put private data into the public card.
- Call another `engine/*` script or import engine modules directly. Invoke the engine through the `engine/review.py` CLI (`prepare`, `resume`, `preview`, `finalize`, `capture`, `render`, `repair-projections`, `set-cap`, `doctor`) so lifecycle validation, required-question gates, and canonical session state stay authoritative. `capture` is the one sanctioned append path outside that lifecycle, and only for a light-tier review — see `flows/light-capture.md`.

If a new observation could overturn the top behavioral leak, add it to `observations` and rerun preview. Do not edit the engine artifact. That keeps analytical flexibility while every conclusion change still passes the same validator and renderer.
