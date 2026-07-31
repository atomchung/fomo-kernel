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
- Answer on the user's behalf, or present an inference as confirmed. The engine cannot tell that an answer was invented.
- Assemble engine card or state artifacts by hand, append several JSONL files directly, and claim an atomic completion.
- Upload a statement or screenshot for OCR. Snapshot transcription stays local; the engine accepts only the normalized JSON envelope through `review.py`.
- Ask whether a holdings view covers the user's whole account, treat which kind of source recorded the book as deciding whether it may anchor, or claim that a later transaction import reconciles a fresh broker view. Every accepted source records the book at the time it arrives; ledger-derived current holdings stay canonical, and a newer holdings view reaches the recorded book through `refresh`.
- Put private data into the public card.
- Call another `engine/*` script or import engine modules directly. Invoke the engine through the `engine/review.py` CLI (`prepare`, `resume`, `preview`, `finalize`, `capture`, `consider`, `refresh`, `positions`, `record-rationale`, `render`, `repair-projections`, `set-cap`, `mute-rule`, `add-cash`, `resolve-market-data`, `doctor`) so lifecycle validation, required-question gates, and canonical session state stay authoritative. `add-cash` stays inside the lifecycle: it recomputes one prepared session with the anchor the user supplied at the card beat and refuses if anything but the anchor moved (`references/data-contract.md`). `capture` and `consider` are the two sanctioned append paths outside that lifecycle — `capture` only for a light-tier review (`flows/light-capture.md`), `consider` only for a trade the user has not placed yet (`references/trade-consequence.md`). `resolve-market-data` and `positions` are neither: `resolve-market-data` retrieves current market facts into a `references/price-feed.md` envelope, and `positions` prints the per-position diagnosis of the recorded book (#561, `references/freeform-answers.md`) — both write no session, ledger, or evaluation row. `prepare` and `consider` resolve their own facts, so reach for `resolve-market-data` only to inspect what retrieval produced, or to capture an envelope for a machine that cannot reach the provider.
- Argue a considered trade's case from anything but `consider`'s output. The engine states the consequence; the agent may add judgment about the thesis, the valuation, or the timing, but every such claim carries its own label, and the risks the engine does not measure are named rather than passed over. Silence about what was not checked reads as a clean bill of health. What this particular answer owes is not a list to remember: the response's own `challenge` block computes it per call (`references/trade-consequence.md`, "What the answer owes").

If a new observation could overturn the top behavioral leak, add it to `observations` and rerun preview. Do not edit the engine artifact. That keeps analytical flexibility while every conclusion change still passes the same validator and renderer.
