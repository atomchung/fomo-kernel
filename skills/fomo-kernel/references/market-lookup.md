# Looking up market context for a trade the user is deciding on

`consider` computes what a trade does to the user's own book. It cannot say what the user is walking into: "am I chasing" is a question about where the price stands today, and a same-day filing can be the entire reason a decision feels urgent. That context is the host agent's to gather — the engine performs no lookup, and nothing gathered here changes what it computes. This file is the contract for that gathering: when to look, what to ask, when to stop, and what a found fact is allowed to become.

Every fact retrieved under this contract enters the answer as a `public_fact` with `source` and `as_of` ([trade-consequence.md](trade-consequence.md) documents the claim envelope). A lookup result never becomes the user's motive by itself — see [What a found fact may become](#what-a-found-fact-may-become).

## Relevance gates

### Price and position context — when material

Fetch a small position packet only when the question or recommendation depends
on where the security trades now — for example, “am I chasing?”, an unstated
premise price, or a comparison whose result could change with current value:

- the current price, beside the premise price;
- the source's own recent-move readings — the day move, and whatever week, month, or 52-week change figures the source itself publishes;
- the 52-week high and low.

Transcribe the source's ready-made readings; do not derive new ones. Stating both prices and saying in words which side of the range the trade sits on is judgment and belongs in the answer; computing a new percentage is not transcription. Use a recognized market-data source — the same standard as [price-feed.md](price-feed.md); yfinance's ready-made fields are the default when the host can run it.

Do not fetch this packet for pure book arithmetic or when the engine already
resolved the prices the answer needs. A price reading never triggers more
research by itself. If a material price source is unreachable, state that gap
and answer only to the extent the recorded book supports; never turn an
unpriced basis into a current-market claim.

### L1 — event lookup, on trigger

For a simple single-security decision, look up the current event record when
at least one of these holds and the answer could materially change the question
or the judgment:

1. **`why_now` is missing or vague in a time-sensitive decision** — the user says "today", "now", "after the move", "because of the news" without naming the event. Find the most plausible current event, then *ask* whether it is the actual trigger.
2. **The user cites a specific current claim** — earnings, guidance, a filing, a launch, a headline. Verify the exact claim and its timing rather than accepting a label like "good earnings".
3. **The user's statement and the accessible public record appear to conflict** — check the narrow contradiction before presenting a judgment.

One lookup answers one **evidence packet** for one candidate catalyst:

| Cell | Question |
|---|---|
| change | What exactly changed or was released? |
| known | When did it become public, so it can plausibly belong to `why_now`? |
| baseline | What prior guidance, period, or setting does it update? |
| counter | What is the strongest narrow reading that it was smaller, older, already disclosed, or contradicted? |

Open broad, then narrow: the first query is short and wide ("<ticker> news this week"), and only the follow-up narrows to the candidate event. A first query built around one hypothesis finds that hypothesis.

### L2 — dimension lookup, when the user's reason names one

`unchecked` names what the engine did not look at. When the user's own reason makes one of those dimensions decision-central — "it got cheap" (valuation), "the business improved" (operating evidence), "rates changed" (an official release) — that dimension stops being a disclaimer and becomes the one thing to verify: look up the specific metric or release the user means, or ask which one they mean. The lookup is bounded to the named dimension.

## When lookup does not happen

All lookup is skipped when:

- the question is fully answered by the recorded book ("what does adding this do to my weights?");
- the stated reason is clear and stable, and no current fact is needed to understand the decision;
- the fact could not alter the lead judgment, the counter-case, or one necessary question to the user;
- the packet is already answered — do not keep searching past it;
- the user asked not to browse, or the host has no browse capability: state the gap and ask for the source or the reason instead. Never invent, and never read "nothing found" as "no risk".

Company research is in scope when an operating, valuation, event, or comparative
fact could change the recommendation. When the user explicitly asks for
candidate discovery, search a stated universe with stated filters and an as-of
point. Report material exclusions and coverage limits, and never claim the
search was exhaustive unless the evidence establishes that. A target or
forecast remains model judgment with assumptions and uncertainty, never a
public fact merely because a source published one.

## The neutral query

Separate the hypothesis from the lookup. The query asks what a source stated; whether that supports the trade is your judgment after retrieval, never embedded in the search.

- Bad: "did the new guidance justify buying <ticker>?"
- Good: "what guidance did <company> issue, when, and what was the prior guidance?"

This is the same rule [condition-slots.md](condition-slots.md) freezes into every stored condition: a query carrying the conclusion steers retrieval toward confirmation and returns a real source with a wrong fit.

## Source hierarchy

Prefer the closest source that can answer the packet:

1. company filing, exchange announcement, investor-relations release, official transcript;
2. regulator, central bank, or official statistical release;
3. a recognized market-data source, for price, volume, or named-metric readings;
4. reputable secondary reporting, for context or when the primary document is out of reach — and judge a secondary source laterally: what other independent sources say about it, not what it says about itself;
5. social media and forum content, only as a lead to trace upstream. A post is never itself evidence in the judgment.

## Stop discipline

Stop when the decision-relevant packet or comparison is honestly covered and
another retrieval has lower marginal value than its cost or latency. Broader
candidate searches may need more sources than a simple event check; narrower
ones may need fewer. Report any material empty cells, excluded universe, or
unresolved contradiction. "I could not establish the prior baseline" is an
honest state; an empty cell is never padded with an inference.

## What a found fact may become

A lookup result can occupy exactly one of three roles, and the transitions are never automatic:

1. **`public_fact`** — what an identified source stated, with `source` and `as_of`.
2. **`agent_judgment`** — your read of whether it is new, material, or decision-relevant, labelled as yours.
3. **the user's `why_now`** — only after the user confirms it in their own words.

A company announcing guidance today does not prove the user is acting because of it. When `why_now` was missing and L1 found a candidate event, ask one grounded question and accept "not this" without steering:

> The closest public change I can find to this decision is [event, dated]. Is that the main reason you want to act now — and if not, what actually changed your read?

The wording is illustrative, not a template. If nothing material was found, say so and ask for the real trigger.

Retrieved context is material for judgment, not script. It enters the visible
answer only where it earns a place in the recommendation, its support, a
material counter-case, or a necessary question. A lookup that ends as a pasted
news summary has replaced a disclaimer dump with a news dump, and both fail the
same way.
