# First review flow

Use when the Review Plan has `route=first_review`.

**Before `prepare`:** resolve the cash anchor per `references/data-contract.md` — read it from the source, ask one short question when it appears nowhere, or accept an explicit skip. Once `prepare` returns a session_id, record `cash_anchor_checked` before the first question or card (`references/ux-receipt.md`). The check happens before `prepare` runs, so the receipt is retrospective evidence it happened at all rather than a claim made afterward.

**0. Recover prices first.** If `review_plan.input.price_feed.request` is present, the host could not retrieve prices. Look the requested closes up yourself from a recognized market-data source, transcribe them into the envelope, and rerun `prepare --prices <path>` — before you mention the gap to the user or deliver a degraded card (`references/price-feed.md`). Deliver a degraded card only if recovery genuinely fails, and never stall the review over it.

**1. Set expectations briefly.** One or two sentences: the engine computed these numbers locally, and the review needs the user's motive before it can reach a conclusion.

**2. Ask every required question in `question_queue`, in order, once.** Follow `references/interaction-delivery.md` for presentation. A first review carries three to five highest-information items, possibly including an `initial_thesis` capture asking, for an un-thesised holding, whether the entry followed a plan, chased momentum, came from an external recommendation, or had no clear thesis.

For `add_thesis`, `initial_thesis`, `headline_motive`, and `exit_consistency`, bind a grounded private surface through `review.py resume --question-surfaces` and present exactly what comes back; on any failure keep the engine fallback. An `exit_consistency` question is the answerable form of the Block-3 exit-opportunity-cost observation — it asks whether the early exits it names were a deliberate rule, an emotional reaction, or an external constraint, and the read-only panel yields to it when queued. A recent-exit question stays engine-rendered, captures a reason without judging the outcome, and saves `skip` so it is not asked again.

The queue is the engine's ranking by impact, so do not merge, add, skip, or reorder it, and do not change canonical options or payload gates. A quoted thesis keeps its engine-declared voice. One fact-grounded observation per review may enter `observations` without changing the queue.

**3. Write an inferred thesis for every entry in `missing_thesis_positions`,** using the field vocabulary in `review_plan.authoring_contract`. Submit the unchanged `cycle_id` plus:

- `why` — the fact or expectation that may not be priced in, or an honest placeholder such as "averaging down while waiting to recover; confirmation needed"
- `exit_trigger` — a factual condition that would falsify the thesis, not a stop-loss price
- `horizon` — a stable id from `card_plan.horizon_ids`, or null when no reasonable inference exists

The engine prefills `ticker`, `maturity:"inferred"`, and provenance. Never present an inferred thesis as user-confirmed. Optional fields (`stop`, `target_size`, `driver`, and the inference-only accumulation fields in `authoring_contract`) follow one rule: fill one only when the conversation contains a real signal, leave it null otherwise, and never add a question for it. An invented source or emotion is worse than a missing one, and these cannot be backfilled later. Upgrade `_inferred` to false or `source_confidence` to `"confirmed"` only when the user volunteers it.

**4. Write the narrative, qualitative and digit-free.** `headline` and `mirror` are required; `counterfactual` and `strength` are optional. Cover every `card_plan.required_honesty_keys` entry with one sentence in `narrative.honesty` (wording guidance in `card-spec.md`). Frame coverage gaps the engine never asked about as neutral facts per `authoring_contract.narrative.unprompted_gaps` — they are not the central judgment of the card. Optionally add `synthesis`: a closing block after the rule that connects facts sitting in separate sections into one point of view. If it only restates a number already on the card, leave it out. Do not write `rule_rationale`; the engine derives the rule's trade-off sentence itself and the field survives only so older sessions stay readable.

**5. Run `preview`.** If validation fails, fix the artifact the error names rather than working around the gate.

**6. Show the card preview inline** (`references/card-delivery.md`), record the presentation (`references/ux-receipt.md`), and only then ask the user to choose one candidate rule, write their own, or skip.

Present each candidate's engine-authored `grounding` sentence verbatim when the payload carries one — that is what ties a generic rule to this user's real positions — and never invent a grounding for a candidate that has none. When `card_plan.candidate_comparison` is present, show that one sentence once alongside the candidates: it explains why the others ranked lower on this period's severity ranking, not which rule is objectively right for this user, so do not rephrase it into an endorsement.

If the user states their own single-position cap here ("my limit is 25%"), record it with `review.py set-cap` per `references/data-contract.md` so future reviews reconcile against their number. Do not add a card note explaining the threshold.

**7. Write the choice to `answers.commitment` and `finalize`.** Return the review card (`card-private.md`) per the same delivery contract. Return the share-safe artifact only if the user asks; do not imply the product publishes it.

Success means one canonical session is committed and the user has seen one card. A projection error is repairable and must not be described as session loss.
