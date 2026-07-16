# Weekly review flow

Use when the Review Plan has `route=weekly_review`.

1. Read `state_snapshot` from the Review Plan. Do not scan the entire `~/.trade-coach` directory.
2. Begin the interpretation by reconciling against `prior_commitment`. The renderer opens the card with the committed rule and that metric's then/now values verbatim; interpret the movement in the mirror, and do not compute a delta yourself.
3. Ask only items in `question_queue`. Prepare already deduplicated them against active theses and add counts; do not ask raw engine `thesis_questions` again.
4. The queue contains at most three engine-ranked questions. A recent-exit question captures the user's reason without judging the outcome; a `skip` answer is saved and must not be asked again.
5. A `due_revisit` question replays the user's own recorded exit reason from the question payload verbatim; do not soften or reinterpret it. Present the swap comparison only from `compare` (missing prices stay listed in `needs_prices`; never estimate a return). Its `skip` is not saved — the same checkpoint legitimately returns next review.
6. When `state_snapshot.exit_backlog` is present, mention the aggregate pattern in one sentence at most; it is history for context, not a weekly interrogation list.
7. When `state_snapshot.problem_stats` is present, weave the top recurring problems and any `rules_check` verdicts into the mirror. `held_streak >= 2` stays silent; `skipped` periods are never claimed as held.
8. Treat every `missing_thesis_positions` item as a new cycle or a historical thesis gap and fill it using the inference-first contract from the first-review flow.
9. Classify each losing-position add as `planned_tranche`, `new_evidence`, `valuation_change`, `price_only`, or `skip`. A `new_evidence` choice must include an evidence delta so the next review can examine it as a thesis event.
10. Focus the narrative on movement against the previous rule and the largest new behavioral leak. Cover every `card_plan.required_honesty_keys` entry with one sentence in `narrative.honesty`. Do not produce a complete dashboard.
11. After preview, let the user choose only one rule. Finalize atomically; update legacy state only through projections.

Do not ask for an already confirmed motive every week. Prepare should requeue it only for a new cycle, new behavior, or an inferred answer that remains the largest contradiction.
