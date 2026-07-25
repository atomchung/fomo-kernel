# Weekly review flow

Use when the Review Plan has `route=weekly_review`.

**Cash anchor, full-tier only.** After step 1's tier check, resolve it per `references/data-contract.md` — read from source, ask once, or accept a skip. A light week follows `flows/light-capture.md` and is never asked: it keeps its single-question promise, and with no card there is no account figure for an anchor to ground. If the anchor was not on the first `prepare` call, rerun `prepare --cash <json>` and continue on the session it returns — cash participates in the session fingerprint, so the rerun opens a fresh session carrying the anchor instead of silently resuming the cash-less one. Record `cash_anchor_checked` before the first question or card (`references/ux-receipt.md`).

**0. Recover prices first.** If `review_plan.input.price_feed.request` is present, look the requested closes up yourself, transcribe them into the envelope, and rerun `prepare --prices <path>` before surfacing the gap or delivering a degraded card (`references/price-feed.md`). Never stall the review over it.

**1. Read `state_snapshot` from the Review Plan** — not the `~/.trade-coach` directory. If `state_snapshot.cadence.tier == "light"`, stop here and follow `flows/light-capture.md`.

**2. Open by reconciling against `prior_commitment`;** when it is null, say the previous review ended with no committed rule. Record that the memory was actually presented (`references/ux-receipt.md`). Continuity is the reason this product exists, so it has to be visible before anything new. The renderer opens the card with the committed rule and that metric's then/now values verbatim — interpret the movement in the mirror rather than computing a delta yourself.

**3. Ask only what is in `question_queue`, once,** following `references/interaction-delivery.md`. Prepare already deduplicated against active theses and add counts, so do not ask raw engine `thesis_questions` again. For `add_thesis`, `headline_motive`, and `exit_consistency`, bind a grounded private surface through `review.py resume --question-surfaces` and present exactly what comes back; on failure keep the engine fallback. `due_revisit`, `rule_breach`, and recent-exit capture stay engine-rendered. When the queue carries `prior_exit_reason` or a matured revisit, record the matching memory presentation after showing that context.

The queue is the engine's ranking, so do not change route, kind, priority, required status, canonical choices, payload gates, facts, or identities. A quoted thesis keeps its engine-declared voice, and one fact-grounded observation may enter `observations` without changing the queue.

**4. One to three engine-ranked questions.** A recent-exit question captures the user's reason without judging the outcome, and its `skip` is saved so it is not asked again.

**5. A `due_revisit` question replays the user's own recorded exit reason verbatim** from the payload — do not soften or reinterpret it. Present the swap comparison only from `compare`; missing prices stay listed in `needs_prices` and are never estimated. Its `skip` is *not* saved, because the checkpoint legitimately returns next review.

**6. `state_snapshot.market_context` and `horizon_markers` are frozen engine facts.** Do not fetch prices again, compute a new comparison, or turn a horizon marker into a motive verdict the user was never asked about.

**7. When `state_snapshot.exit_backlog` is present,** mention the aggregate pattern in at most one sentence. It is history for context, not a weekly interrogation list.

**8. When `state_snapshot.problem_stats` is present,** weave the top recurring problems into the mirror. A `held_streak >= 2` stays silent; `skipped` periods are never claimed as held. Call a rule broken only after a `rule_breach` question has qualified the exact-period evidence.

**9. A `rule_breach` question always offers `keep_tracking` and `exception`,** plus `revise_rule` only when an engine metric can track a replacement for that same problem key. `exception` and `revise_rule` require a short note. If the user chooses `revise_rule`, use the one final commitment as the replacement and set `commitment.revises_rule_id` to the question's `rule_id`. Prepare asks only the first unqualified breach or a later worsening.

**10. Treat every `missing_thesis_positions` item** as a new cycle or a historical gap and fill it with the inference-first contract from the first-review flow.

**11. Classify each losing-position add** as `planned_tranche`, `new_evidence`, `valuation_change`, `price_only`, or `skip`. A `new_evidence` choice must carry an evidence delta so the next review can examine it as a thesis event.

**12. Focus the narrative on movement against the previous rule and the largest new leak.** Cover every `card_plan.required_honesty_keys` entry in `narrative.honesty`. Frame coverage gaps the engine never asked about as neutral facts per `authoring_contract.narrative.unprompted_gaps`. Optionally add `synthesis` — a closing block connecting facts across sections into one point of view; omit it if it only restates a number. Do not produce a full dashboard.

**13. After preview, show the card inline** (`references/card-delivery.md`), record the presentation (`references/ux-receipt.md`), and only then let the user choose one rule. Present each candidate's engine-authored `grounding` verbatim when the payload carries one, and never invent one. When `card_plan.candidate_comparison` is present, show that sentence once alongside the candidates — it explains why the others ranked lower this period, not which rule is objectively right, so do not rephrase it into an endorsement. If the user states their own single-position cap, record it with `review.py set-cap`. Finalize atomically; update legacy state only through projections.

Do not ask for an already confirmed motive every week. Prepare requeues one only for a new cycle, new behavior, or an inferred answer that remains the largest contradiction.
