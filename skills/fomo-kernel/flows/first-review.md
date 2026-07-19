# First review flow

Use when the Review Plan has `route=first_review`.

1. Explain in one or two sentences that the engine computed the numbers locally and that the review needs motive confirmation before it can produce a conclusion.
2. Ask every required question in `question_queue` in order, once, following the native-control and fixed-text protocols in `references/interaction-delivery.md`. The queue is already limited to the three highest-impact items. A recent-exit question captures a reason without judging the outcome; `skip` is saved and is not asked again. Do not merge questions or replace their meaning. A stem may already quote the user's own recorded thesis; present it as-is and do not re-verify or re-ask the quoted thesis. You may add one short lead-in in your own words and keep conversing within a question until the answer maps to one schema option plus its required note, and you may flag at most one observation per review as context; ground every added sentence in facts already present in the stem, payload, or plan, and keep it direct — no softening preambles or intent disclaimers. Never change a question's meaning or options, invent facts, add required questions, or skip or reorder the queue.
3. Create an inferred thesis for every entry in `missing_thesis_positions`. Include at least:
   - `ticker` and the unchanged `cycle_id`
   - `why`: the fact or expectation that may not be priced in, or an honest placeholder such as "averaging down while waiting to recover; confirmation needed"
   - `horizon`: the stable id `weeks`, `quarters`, or `years`; use null when no reasonable inference is possible (legacy Chinese values remain readable but must not be newly authored)
   - `exit_trigger`: a factual condition that would falsify the thesis, not a stop-loss price
   - `stop`, `target_size`, and `driver`
   - `maturity:"inferred"` plus the inference source; never present it as user-confirmed
   - Inference-only accumulation fields (never ask extra questions for them; they cannot be backfilled later): `source_type` (`kol`|`research`|`self`|`other`, with `source_name` and `source_confidence:"candidate"` only when the conversation contains a real signal), `emotion` (`fomo`|`composed`|`forced`|`planned`) and `confidence` (`high`|`medium`|`low`), each with `emotion_inferred`/`confidence_inferred` set to true. Leave a field null when no signal supports a guess; upgrade `_inferred` to false or `source_confidence` to `"confirmed"` only when the user volunteers it in an existing answer.
4. Keep the narrative qualitative. Write `headline` and `mirror`; optionally add `counterfactual`, `strength`, and `rule_rationale`. Cover every `card_plan.required_honesty_keys` entry with one sentence in `narrative.honesty` (wording guidance in `card-spec.md`). Do not include digits.
5. Run preview. If validation fails, fix the artifact described by the error; do not bypass the gate.
6. Show the review-card preview inline following `references/card-delivery.md`, record the actual presentation following `references/interaction-delivery.md`, and only then ask the user to choose one candidate rule, provide a custom rule, or skip.
7. Write the choice to `answers.commitment`, then finalize. Return the review card (`card-private.md`) per the same delivery contract. Return the share-safe artifact only when the user asks for it; do not imply that the product publishes it.

Success means that a canonical session is committed and the user sees one card. Projection errors are repairable and must not be described as session loss.
