# fomo-kernel backlog

Last refreshed: 2026-07-14. The target is a promotion-ready release on 2026-07-19.

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

## P1

### Multi-lens selection and comparison

- Select from a small verified lens set.
- Apply style-specific divergence only where mechanical evidence supports a style axis.
- Keep universal risk failures outside the lens override.
- Do not duplicate lifecycle, state, schemas, or renderers.
- Verify every public quotation against a primary source before promotion.

### Complete snapshot adapter

- Accept a position table or screenshot directly.
- Normalize into the snapshot review card/state contract.
- Limit conclusions to facts supported by a snapshot.
- Allow later transaction history to unlock behavioral dimensions.

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
