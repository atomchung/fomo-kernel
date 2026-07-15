# Weekly review flow

Use when the Review Plan has `route=weekly_review`.

1. Read `state_snapshot` from the Review Plan. Do not scan the entire `~/.trade-coach` directory.
2. Begin the interpretation by reconciling against `prior_commitment`. Displayed numbers still come from renderer-owned engine state; the agent must not compute a delta.
3. Ask only items in `question_queue`. Prepare already deduplicated them against active theses and add counts; do not ask raw engine `thesis_questions` again.
4. Treat every `missing_thesis_positions` item as a new cycle or a historical thesis gap and fill it using the inference-first contract from the first-review flow.
5. Classify each losing-position add as `planned_tranche`, `new_evidence`, `valuation_change`, `price_only`, or `skip`. A `new_evidence` choice must include an evidence delta so the next review can examine it as a thesis event.
6. Focus the narrative on movement against the previous rule and the largest new behavioral leak. Do not produce a complete dashboard.
7. After preview, let the user choose only one rule. Finalize atomically; update legacy state only through projections.

Do not ask for an already confirmed motive every week. Prepare should requeue it only for a new cycle, new behavior, or an inferred answer that remains the largest contradiction.
