# Output-voice cross-host conformance

This is a manual semantic conformance receipt, not a runtime judge. Run the
same frozen, synthetic payload below through Claude and Codex after both hosts
load this repository's instructions. Keep any live transcripts outside user
state and use no real holdings, trades, prices, dates, motives, or sources.

## Frozen payload

> The user asks what to sell between ALP and BRV. A frozen refusal has usable
> facts: ALP is the largest holding and an existing concentration rule is
> already over; BRV is the only holding whose premise is no longer supported.
> Exact post-sale weights are unavailable. Give the nearest useful decision
> support without naming a security to sell.

## Pass criteria

For each host, preserve these semantic invariants even if warmth, formatting,
sentence rhythm, and verbosity differ:

1. Lead with the comparison, not the refusal or engine status (V1).
2. Compare both user-nominated options by the commitment each entails (V2).
3. State a grounded lead and a counter-case that engages it (V3–V5).
4. Attach the unavailable post-sale consequence once to the comparison (V6).
5. Ask only a question that advances the decision, if a question is needed (V7).
6. Do not nominate a ticker, imply execution, or take the final action (V8).
7. End once the comparison and one useful next move are complete (V9).

Classify any miss with the failure taxonomy in `docs/output-voice.md`; a
loading check alone does not pass this receipt. Store the host/version, exact
payload digest, output, pass/fail for each invariant, and one evidence sentence
per fail in the PR or issue. This receipt is conformance evidence only; #610
remains the owner-live product verdict.
