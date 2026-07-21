# Card policy

> Section order and section set are governed by
> [docs/output-contract.md](../../../docs/output-contract.md) (keynote + four
> blocks). Where this file implies a different order, the contract wins. This
> file keeps wording, redaction, and narrative rules.

A card is a story, not a dashboard.

The private card follows this order when data exists: mirror, frozen market/timeline context, primary account numbers, one strength, largest leak, qualitative motive or thesis, exit follow-up and recurring problem memory, ETF structure, and one rule. Honesty sentences are not a section: each triggered ledger key renders inside the section it qualifies, using the agent-authored wording from `narrative.honesty`. Omit unavailable sections instead of filling them with generic prose.

Agent narrative may not contain digits. The renderer obtains every amount, percentage, date, ticker, and metric from engine card and state artifacts. This deliberately strict boundary prevents the engine and prose from becoming competing numeric truth sources.

For a single-currency portfolio, private-card amounts stay in that portfolio currency regardless of locale. For a mixed-currency portfolio, aggregate amounts render in USD for English and TWD for Traditional Chinese, using the rate frozen during `prepare`; an unavailable live display rate may use the prior local state's cached rate with its reconciliation date shown. If neither rate exists, show original-currency P&L buckets and omit aggregate monetary conversions instead of guessing. Best/worst trade amounts always retain their brokerage currency. These display rules never change engine calculations, relative performance, or the public card, which contains no absolute amounts.

The public card does not reuse agent narrative. It renders a separate structured view and removes session IDs, dates, tickers, amounts, exact weights, and evidence text. It may retain fixed behavior-pattern copy plus engine-owned beta and benchmark-excess percentage points; mixed-market public lines name only the market and never expose benchmark symbols. It is not a regular-expression mask over the private card.

Do not provide buy or sell recommendations, shame the user, or list several action items. A commitment may be skipped. Code labels short samples as baselines rather than pretending they passed a mature threshold.
