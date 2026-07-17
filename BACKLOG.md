# fomo-kernel backlog

Last refreshed: 2026-07-17. The target is a promotion-ready release on 2026-07-19.

## North star (owner correction 2026-07-05, #112 — do not regress this)

The vision is an agent, but the next action is still one card striking one real person. **The constraint is card quality — whether the card is useful enough and at a publishable standard — not distribution or finding users.** The card has always been tested and always been shown to people; the real bottleneck is that the card is not yet good enough. The promotion release below is a vehicle for validating card quality, not a distribution goal in itself. Do not let the big vision steal the small thing that needs validating now.

## P0 for the promotion release

### Stable workflow and card production

Status: implemented, pending manual release gates.

- Keep `SKILL.md` as a thin entry point.
- Use the fixed `prepare -> preview -> finalize` lifecycle.
- Resume interrupted sessions without refetching live data.
- Commit one canonical immutable session and rebuild projections from it.
- Render private and public cards deterministically.
- Keep required questions and evidence completeness as code gates.

Release evidence: `docs/release-2026-07-19.md`, `tests/test_review_v2.py`, and the complete offline suite.

### Thesis evolution and add evidence

Status: implemented in the v2 lifecycle.

- Classify losing-position adds as planned tranche, new evidence, valuation change, price only, or skip.
- Require a claim and source for `new_evidence`.
- Store append-only thesis decision events tied to active cycle IDs.
- Reconcile the evidence in future reviews rather than asking a generic averaging-down question again.

### Weekly reconciliation and memory presentation

Status: implemented in the v2 lifecycle and private-card renderer, pending second-review dogfood and the manual card-quality gate.

- Open a later review by reconciling the user's prior commitment rather than starting from zero.
- Put due exit revisits into the required question queue, preserve the earlier exit reason, and frame the checkpoint against the actual swap outcome.
- Keep cold-start historical exits in a summarized backlog instead of flooding required questions.
- Surface recurring four-week problem counts and ask for a qualitative judgment when a chosen rule appears to have been broken again.
- Mirror frozen market context and holding-horizon contradictions from the review plan onto the private card.
- Leave skipped due checkpoints unresolved so they return; commit answered checkpoints and review marks through canonical session projections.

### ETF policy

Status: implemented with conservative fallback.

- Exempt only broad-market, regional, bond, and commodity allocation ETFs from single-name concentration.
- Keep sector, thematic, and leveraged ETFs in concentration and stress diagnostics.
- Give unknown instruments no exemption.
- Disclose missing expense ratio and tracking error rather than assuming zero.
- Add a live metadata source later without changing the policy contract.

### English implementation and bilingual GTM

Status: implemented in this change, pending full verification.

- Keep developer documentation and skill instructions in English.
- Keep English and Traditional Chinese GTM artifacts synchronized as separate files.
- Keep user-visible localized product copy in separate locale resources.
- Prevent mixed-language implementation docs with a deterministic regression test.

## Manual release gates

- Complete one full Traditional Chinese run with an anonymized publishable CSV.
- Complete the same flow in English.
- Inspect the public card manually for amounts, dates, tickers, exact weights, session IDs, evidence text, and free-form narrative.
- Demonstrate evidence-gate rejection and recovery.
- Demonstrate broad ETF exemption versus thematic ETF concentration.
- Complete a second review against the same local state and inspect prior-rule reconciliation, due checks with frozen-price swap framing, exit backlog, recurring-problem presentation, market context, and any horizon contradiction.

## Shipped after the P0 gate scope was frozen

### Initial snapshot onboarding adapter

Status: implemented as the v2.1 onboarding slice; validate separately from the 2026-07-19 CSV-focused manual gates.

- [x] Accept a normalized position JSON envelope produced locally from a table or screenshot through `review.py`.
- [x] Keep screenshot transcription local and the temporary normalized JSON outside the repository; there is no engine OCR or cloud-upload path.
- [x] Normalize the declared facts into the snapshot review card/state contract. A complete initial snapshot may establish the accounting anchor; an incomplete snapshot produces a bounded review without becoming an anchor.
- [x] Limit conclusions to cost or value weights, single-position risk, driver concentration, ETF structure, and data integrity.
- [x] Initialize inferred theses for open cycles without claiming historical motives.
- [x] Allow later transaction history to unlock supported history-dependent behavioral dimensions while ledger-derived current holdings remain canonical and unreconciled current-view claims fail closed.

## P1

### Post-release lens research and possible selection

- Keep the existing `rubric/` files as research assets; do not load them into current v2 questions or cards.
- Select from a small verified lens set.
- Apply style-specific divergence only where mechanical evidence supports a style axis.
- Omit lens-specific interpretation when the selected lens has no explicit stance for that dimension.
- Keep universal risk facts independent of the lens and never attribute them to a persona.
- Do not duplicate lifecycle, state, schemas, or renderers.
- Keep public wording as source-linked paraphrase, never a quotation or persona endorsement.

### Snapshot reconciliation and repeated snapshots

- Compare a second or subsequent declared snapshot with ledger-derived current holdings.
- Present a narrow diff, fail closed on ambiguous causes, and write an explicit adjustment event only through the reconciliation contract.
- Adopt a newer declared accounting anchor only after that reconciliation succeeds; do not let transaction-history import silently assert a fresh broker view.

### ETF metadata enrichment

- Add a maintained instrument source for classification, expense ratio, and tracking error.
- Preserve the local override and conservative unknown fallback.
- Cache data for offline and repeatable reviews.

## Later candidates

- Event-driven pre-trade check against the active process rule.
- Personal lens distilled from repeated confirmed review patterns.
- Richer source attribution for owner-only research workflows.
- Automated GTM asset generation and publishing.
- More behavior detectors only when they are measurable and bind to a testable rule.

## Product boundaries

- Process coaching is not security selection.
- One card converges on one behavioral leak and at most one rule.
- Trade data remains local.
- A clean strengths card is valid when no costly leak is supported.
- Real-user usefulness remains the final validation layer; passing automated tests is necessary but not sufficient.
