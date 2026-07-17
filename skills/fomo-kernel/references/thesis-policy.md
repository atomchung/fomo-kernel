# Thesis and add-decision policy

A thesis is an append-only history of investment judgment, not a weekly memo that overwrites the past.

Classify every add to a losing position as:

- `planned_tranche`: the staged plan existed before entry; the note describes that original plan.
- `new_evidence`: a relevant fact was unknown at entry; require `evidence_delta.claim` and `source`, with optional `observed_at` and `falsifier`.
- `valuation_change`: the core facts are unchanged, but price changed the odds or margin of safety; the note states which assumption remains unchanged.
- `price_only`: no new fact exists; the main motive is a lower price, lower average cost, or waiting to recover.
- `skip`: the user does not want to classify it yet. Preserve the uncertainty in the card and memory; the agent must not answer for the user.

`new_evidence` is not a synonym for positive news. It must change a falsifiable part of the prior thesis. "It is cheaper" is a valuation change; "the market agrees" without a new fact is price only.

Identity and continuity are engine-owned:

- One position cycle keeps one stable `thesis_id`. A fully exited ticker followed by a new position starts a new cycle and a new thesis.
- Every thesis update, decision, or linked exit has its own `event_id`; `revises` points to the prior event. Never overwrite an old event.
- Each active cycle has its own add-decision cursor. Only another add inside that cycle advances it; a portfolio-wide count must not re-open another ticker's question.
- A full exit closes the position side. `thesis_broken` records a `falsified` outcome; every other confirmed or skipped full-exit explanation records `closed`, with the explanation state preserved separately. A reduction does not close the thesis.

Evidence provenance follows `captured -> confirmed -> evaluated`:

- `captured` means a legacy or imported event contains a claim and source, but the newer confirmation contract was not present.
- `confirmed` means the user confirmed that this claim and source were part of the decision. It does not mean the external claim is objectively true.
- `evaluated` is reserved for a later reconciliation that compares an observation with the claim or falsifier. P0 provenance capture must not promote evidence to this state automatically.

The engine assigns a stable `evidence_id`, preserves the stated source, and keeps `observed_at` null when the user did not provide it. Review time is capture provenance, not a fabricated observation date.

The agent authors thesis content and user-answer payloads, not identities or cursors. `exit_trigger` is a fact that would falsify the thesis; `stop` is a price or sizing action. Keep them distinct.

New thesis updates use locale-neutral horizon ids: `weeks`, `quarters`, or `years`. Legacy localized aliases remain readable so old sessions and identical retries do not break. A missing or unknown horizon stays null and produces no timeline marker.

Inference-only accumulation fields (`source_type`/`source_name`/`source_confidence`, `emotion`/`confidence` with their `_inferred` flags) ride on thesis updates without their own questions. They accumulate for later analysis and never appear on the card. Guess only from real signals in the conversation or the trade pattern; a field with no signal stays null — an invented source or emotion is worse than a missing one. Only the user's own words upgrade `source_confidence` to `confirmed` or flip an `_inferred` flag to false. These fields cannot be backfilled: a week that stores nothing loses that week's signal permanently.
