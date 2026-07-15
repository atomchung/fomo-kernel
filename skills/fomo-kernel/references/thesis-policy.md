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

The agent authors thesis content and user-answer payloads, not identities or cursors. `exit_trigger` is a fact that would falsify the thesis; `stop` is a price or sizing action. Keep them distinct.
