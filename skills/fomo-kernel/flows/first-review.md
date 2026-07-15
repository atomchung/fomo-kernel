# First review flow

Use when the Review Plan has `route=first_review`.

1. Explain in one or two sentences that the engine computed the numbers locally and that the review needs motive confirmation before it can produce a conclusion.
2. Ask every required question in `question_queue` in order. Use a native option UI when available; otherwise present the same options as text. Do not merge questions or replace their meaning.
3. Create an inferred thesis for every entry in `missing_thesis_positions`. Include at least:
   - `ticker` and the unchanged `cycle_id`
   - `why`: the fact or expectation that may not be priced in, or an honest placeholder such as "averaging down while waiting to recover; confirmation needed"
   - `horizon`: weeks, quarters, or years; use null when no reasonable inference is possible
   - `exit_trigger`: a factual condition that would falsify the thesis, not a stop-loss price
   - `stop`, `target_size`, and `driver`
   - `maturity:"inferred"` plus the inference source; never present it as user-confirmed
4. Keep the narrative qualitative. Write `headline` and `mirror`; optionally add `counterfactual`, `strength`, and `rule_rationale`. Cover every `card_plan.required_honesty_keys` entry with one sentence in `narrative.honesty` (wording guidance in `card-spec.md`). Do not include digits.
5. Run preview. If validation fails, fix the artifact described by the error; do not bypass the gate.
6. Show the private preview and ask the user to choose one candidate rule, provide a custom rule, or skip.
7. Write the choice to `answers.commitment`, then finalize. Return the private card. Return the public card only when the user asks to share it.

Success means that a canonical session is committed and the user sees one card. Projection errors are repairable and must not be described as session loss.
