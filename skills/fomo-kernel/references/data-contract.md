# Data contract and recovery

Authority order:

1. `sessions/<session_id>/bundle.json`: complete immutable session.
2. State, plan, answers, narrative, private/public cards, and manifest in the same directory: manifest-locked artifacts.
3. `last_state.json`, `log.jsonl`, `theses.jsonl`, `thesis_decisions.jsonl`, `rules.jsonl`, `problems.jsonl`, and `cards/`: rebuildable compatibility projections.

If prepare is interrupted, read `.pending/<session_id>` through `review.py resume`; do not refetch live prices.

If finalize fails before the atomic rename, no session is committed. The pending session remains available for correction and retry.

If finalize fails after the rename while writing projections, the session is complete. Run `review.py repair-projections`; do not delete the bundle or ask the user again.

Retrying the same session with identical content is a no-op. Retrying the same session with different content fails closed. To review identical state as a distinct session, pass an explicit `--session-nonce` to prepare.

Schemas:

- Review Plan: `schemas/review-plan.schema.json`
- Answers: `schemas/answers.schema.json`
- Prose narrative: `schemas/narrative.schema.json`
- Canonical bundle: `schemas/session-bundle.schema.json`

ETF policy: broad-market, regional, bond, and commodity ETFs are diversified allocations. Sector, thematic, and leveraged ETFs remain concentrated risk. Treat an unknown ticker conservatively as equity. Missing expense ratio or tracking error belongs in the honesty ledger and must never be filled with zero.
