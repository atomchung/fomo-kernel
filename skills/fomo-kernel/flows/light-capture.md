# Light-capture flow

Use when the Review Plan has `state_snapshot.cadence.tier == "light"` (issue #237 #4). A short span since the last review means this is not a review — it is one light capture of the trade(s) that triggered it. No card, no rule, no full question queue.

1. Identify the trade(s) newer than the previous review's `date_end` (the batch that made this review fire). For each distinct position cycle among them, ask at most one plain question about the motive or emotion behind that trade — deliberate plan, emotional reaction, external constraint, or new evidence — the same qualitative judgment `references/agent-boundaries.md` already permits, just for one trade instead of a whole review. Do not ask about anything else: no thesis update, no rule reconciliation, no exit follow-up.
2. Look up each cycle's `cycle_id` in `state_snapshot.active_theses` and (plan-top-level) `missing_thesis_positions`.
   - If it is in `active_theses`, the cycle already has a thesis. Build a capture entry with `cycle_id` and `note` (a short honest summary of the answer), plus whichever of `emotion`, `emotion_inferred`, `confidence`, `confidence_inferred`, `source_type`, `source_name`, `source_confidence` actually apply — never invent one that wasn't answered.
   - If it is only in `missing_thesis_positions`, the cycle has no thesis yet. The entry must also include `why` and `exit_trigger`, written the same honest, non-fabricated way as a first-review inferred thesis — mark unknowns as unknown rather than guessing. A capture for a brand-new cycle without these two fields is rejected, not silently dropped.
3. Write the entries to a JSON array matching `schemas/capture.schema.json` and call:

   ```bash
   python3 engine/review.py capture --session-id <ID> --root <state_root> --entries /tmp/capture-entries.json
   ```

4. Tell the user in one or two sentences what was captured. There is no card to show and no rule to choose — this is deferred to the next full-tier review, which will fold every capture since then into its normal reconciliation.

`ux_receipt.py` does not apply to this flow: nothing is presented (no question surface beyond plain text, no card), so there is no presentation trace to declare or verify. See `references/interaction-delivery.md`.
