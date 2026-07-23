# First review — structural (thin first file)

Use when the Review Plan has `route=first_review` and
`state_snapshot.review_tier.tier` is `structural` or `empty`. The engine has
decided that this first file does not yet carry enough realized history
(fewer than `review_tier.min_round_trips` closed round trips) for a full
behavioral review, so this is an opening **structural check**, not a behavioral
diagnosis — and never a string of onboarding questions.

The engine owns this decision. Do not re-derive it, do not try to "unlock" more
by asking the user questions, and do not apologize for what the data cannot yet
support. Present a coherent opening check and one clear next step.

1. In one or two sentences, say plainly that this is an **opening structural
   check** of the current portfolio, computed locally, and that a full
   behavioral review (exit discipline, holding period, win/loss, alpha)
   unlocks once the user provides complete buy **and** sell history — about
   three months, i.e. at least `review_tier.min_round_trips` completed round
   trips. Frame it as "here is what today's file already shows," not as a
   downgrade.
2. **Ask no questions.** `question_queue` is empty by contract on this tier;
   never fabricate a motive, thesis, or exit question to fill it. A single
   fact-grounded observation may still enter `observations` without adding to
   the queue.
3. Present only what the engine emitted and the data supports. Every number
   comes from engine artifacts (non-negotiable rule 1); do not compute or
   estimate. What a thin first file legitimately supports:
   - position concentration / single-position sizing,
   - diversification and shared-driver concentration,
   - averaging-down, when the buy sequence shows adds into a position
     (no sells required),
   - and any realized or unrealized figures the engine already emitted, shown
     with the engine's own counts and low-confidence markers — never inflated
     into a confident verdict.
   For anything the engine withheld because the underlying event does not exist
   yet (no sells → no exit discipline, no realized holding period, no win rate),
   integrate the engine-owned honesty sentence as a neutral fact, and state the
   one concrete thing that would unlock it. Do not present these gaps as the
   central judgment of the card.
4. Create an inferred thesis for every entry in `missing_thesis_positions`,
   following `review_plan.authoring_contract`, exactly as in `flows/first-review.md`
   step 3 (submit the unchanged `cycle_id` plus the qualitative fields; the
   engine prefills the mechanical fields). This preserves thesis continuity for
   later reviews. Label every inferred thesis as inferred; never present it as
   user-confirmed, and do not ask a question to confirm it now.
5. Keep the narrative qualitative and structural. Cover every
   `card_plan.required_honesty_keys` entry with one sentence in
   `narrative.honesty` (wording guidance in `card-spec.md`). Do not include
   digits.
6. Run preview. If validation fails, fix the artifact the error names; do not
   bypass the gate.
7. Show the review-card preview inline following `references/card-delivery.md`
   and record the actual presentation following `references/interaction-delivery.md`.
   A commitment is **not** forced on this tier: offer a candidate rule only if
   the engine surfaced one, and make clear the user may skip it and simply keep
   this structural check as the baseline. When more than one candidate is
   offered and `card_plan.candidate_comparison` is present, present that one
   sentence too — it explains why the others ranked lower on this period's
   severity ranking, not which rule is objectively right for the user. Write
   the choice (or `skip`) to `answers.commitment`, then finalize.

**Empty edge (`review_tier.tier == "empty"`):** the file carried no current
holdings and no closed round trips. Do not manufacture a card. Tell the user
exactly what to provide — a broker export with `Symbol / Action(BUY|SELL) /
Quantity / Price / TradeDate`, or at least the current positions — and stop.

Success means the user sees one coherent opening check (or, on the empty edge, a
clear next step), with no interrogation and no forced commitment. A later file
with complete buy/sell history routes to `flows/first-review.md` and unlocks the
full behavioral review.
