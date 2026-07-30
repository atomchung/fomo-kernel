# Card policy

> Section order and section set are governed by
> [docs/output-contract.md](../../../docs/output-contract.md) (keynote + four
> blocks), which also owns every rule about which figures render as tiles
> versus prose. This file keeps only what the agent decides: wording,
> redaction, and narrative.

A card is a story, not a dashboard. It converges on one behavioral leak and one rule, so a reader finishes it knowing what to change — not holding a list of everything that could be improved.

## A card never names a reward with no way to claim it

A sentence telling the user something is missing owes one of two things, and the rule is [`decision-framing.md`](decision-framing.md)'s "Earning the next piece of evidence" — the same rule, not a second one for this surface (#617, #623):

- **An invitation names the question the evidence would answer, never the data being requested,** and there is at most one on a card. It is the closing block's job — the engine's own unlock copy — and it is absent entirely when nothing further is needed, which is what a complete behavioral review renders. A parenthetical feature list ("win rate, payoff ratio, and more") is the checklist shape this rule exists to forbid: it names a catalogue instead of the one answer this user's book cannot reach.
- **A limitation with no user action attached is a disclosure, stated once, beside the number it explains** — never gathered into a block of its own, and never dressed up as an invitation. Where a product path does exist for it, the path is named inline in the user's own terms rather than as a command.

Do not add an explanatory Note to the card face to carry either one; acceptance is measured on net line count, and a card that grew a paragraph to explain itself has failed.

Omit a section whose data is unavailable rather than filling it with generic prose. The one genuinely optional section is the closing synthesis (`narrative.synthesis`): it renders only when you author it, and it should connect facts that otherwise sit in separate sections into a single point of view. If it only restates a number already on the card, leave it out.

Currency display: a single-currency portfolio stays in that currency regardless of locale. A mixed-currency portfolio renders aggregates in USD for English and TWD for Traditional Chinese, using the rate frozen during `prepare`; if no rate is available, show original-currency P&L buckets and omit aggregate conversion rather than guessing. These are display rules only — they never change engine calculations, relative performance, or the public card.

The public card is independently rendered, not a regular-expression mask over the private one. Independent rendering is what prevents portfolio reconstruction from a redaction that missed something. It excludes session IDs, dates, tickers, amounts, exact weights, and evidence text, and it never reuses agent narrative. It may retain fixed behavior-pattern copy plus engine-owned beta and benchmark-excess percentage points; mixed-market lines name only the market, never the benchmark symbol.

Do not recommend a security to buy or sell, shame the user, or issue several action items at once. A commitment may be skipped. Short samples are labeled as baselines rather than presented as having passed a mature threshold.
