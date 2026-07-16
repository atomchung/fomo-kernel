# fomo-kernel agent-behavior acceptance cases

This is a maintainer checklist, not runtime context. Executable prompts live in `skills/fomo-kernel/evals/evals.json`; deterministic P0 assertions live in `tests/test_review_v2.py` and `tests/run_all.py`.

## Trigger cases

| Input | Expected behavior |
|---|---|
| Trade-review request plus CSV | Trigger the complete review lifecycle. |
| Brokerage statement or screenshot | Trigger and normalize locally. |
| Skill invocation with no data | Offer test drive without searching the user's machine for statements. |
| Request for a stock recommendation | Do not use this skill to provide advice. |
| Request for company research | Do not treat it as a trade postmortem. |
| Request for a market forecast | Do not treat it as a trade postmortem. |

## Lifecycle invariants

1. Use `review.py prepare`; do not reconstruct the lifecycle manually.
2. Ask every required motive question before preview.
3. Never put raw questions or unanswered hypotheses on the conclusion card.
4. Display no raw five-dimension severity dashboard.
5. Use only engine-owned numbers and renderer-owned numeric copy.
6. Require claim and source for `new_evidence`.
7. Create inferred theses for uncovered cycles without presenting them as confirmed.
8. Show one private preview, then let the user choose, rewrite, or skip one rule.
9. Store exactly the user's final rule selection. Short samples remain baselines unless the user explicitly chooses a rule.
10. Commit one immutable canonical bundle; rebuild projections from it.
11. Resume pending work without refetching prices.
12. Keep all trade data local.

## Card invariants

- One strength, one largest leak, and at most one commitment.
- No internal field names or author notes.
- No buy/sell recommendation and no personality judgment.
- Every triggered honesty-ledger key appears in plain, narrow language.
- Public card is independently rendered and contains no amounts, dates, tickers, exact weights, session IDs, evidence text, or agent-authored prose.
- Test-drive cards and conversations are visibly labeled as demo data and do not touch production state.

## Important scenario checks

- A vague "buying the dip" answer does not satisfy the `new_evidence` gate.
- Broad-market, regional, bond, and commodity ETFs may receive the explicit allocation exemption; thematic, sector, leveraged, and unknown instruments do not.
- A multi-market portfolio compares each market with its own benchmark and never synthesizes a total alpha. (Renderer support for per-market lines is deferred to #193; until it ships, the card must not fabricate them.)
- Account-level performance appears only when cash and price foundations satisfy engine gates.
- Cash residual wording remains neutral and does not invent a missing deposit or withdrawal.
- The next weekly review reconciles the prior commitment before introducing a new leak.
- A recent exit or large reduction inside the freshness window yields at most three engine-ranked questions (largest exit amount first); an explicit `skip` is durable and the same exit is never asked again, and a confirmed reason appears only on the local review card, never on the public card.
- A confirmed motive question reappears only when that same cycle receives another add (per-cycle decision cursor); activity in a different ticker never re-opens it. The agent never invents `thesis_id`, `event_id`, `revises`, or `decision_cursor` — the engine assigns identity, and a full exit preserves an explicit closed or falsified outcome instead of silently dropping the cycle.
- Confirmed evidence means "the user confirmed this was part of the decision," never external fact verification: legacy evidence stays `captured` without silent promotion, a missing `observed_at` stays null instead of inheriting the review date, and nothing is auto-promoted to `evaluated`.
- A normalized CSV containing cash-flow rows (deposits, dividends, interest, fees, reinvest notices) still prepares: those rows are counted in `ledger_ingest`, and only future-dated rows reject the import.
- Historical exit-review backlog is summarized and prioritized rather than converted into a large interrogation queue. (Prepare now enqueues exits, but due-revisit and backlog consumption remain deferred to #191; this row applies once that lands.)

## Evaluation method

Prefer deterministic checks over an LLM judge, and an LLM judge over manual inspection. Use a judge only for narrative coherence, not for facts that code can assert. Prove each checker with both a known-good artifact and an intentional mutation.

## Regression record

| Date | Change | Evidence | Result |
|---|---|---|---|
| 2026-07-04 | Post-merge agent run over mock data | Interactive and headless cases plus artifact checkers | Core invariants passed; headless option-tool behavior remained untestable. |
| 2026-07-14 | Skill v2 orchestration, atomic sessions, thesis evidence, ETF policy, localization, and private/public renderers | Complete offline suite, nine v2 cases, and a real test-drive prepare smoke | Passed; canonical recovery and projection repair worked. |
| 2026-07-14 | English-only implementation documentation with bilingual GTM/localized copy boundaries | `tests/test_doc_language.py` plus complete offline suite | Pending final verification in this change. |
| 2026-07-15 | Recent-exit reason capture: prepare-time trade ingestion, three-question ranked queue, durable skip, review-card-only rendering (#196) | v2 suite exit-capture cases plus cash-flow-row ingestion regression on the noisy-broker fixture | Passed; review found and fixed a fail-closed gate that rejected legitimate cash-flow rows. |
| 2026-07-16 | Thesis continuity by cycle: stable engine-assigned identity, revises chains, per-cycle add-decision cursor replacing the portfolio-wide count, explicit full-exit outcomes (#200) | v2 suite continuity cases plus a 14-persona engine sweep against main (only the two designed state fields differ; float drift traced to live pricing via a same-version control run) | Passed. |
| 2026-07-16 | Evidence provenance: content-addressed `evidence_id`, captured/confirmed source states without legacy promotion, null `observed_at` preserved, evaluation left pending (#198) | v2 suite provenance assertions inside the continuity and legacy-fold cases | Passed. |
