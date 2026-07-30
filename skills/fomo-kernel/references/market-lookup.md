# Looking up market context for a trade the user is deciding on

`consider` computes what a trade does to the user's own book. It cannot say what the user is walking into: "am I chasing" is a question about where the price stands today, and a same-day filing can be the entire reason a decision feels urgent. That context is the host agent's to gather — the engine performs no lookup, and nothing gathered here changes what it computes. This file is the contract for that gathering: when to look, what to ask, when to stop, and what a found fact is allowed to become.

Every fact retrieved under this contract enters the answer as a `public_fact` with `source` and `as_of` ([trade-consequence.md](trade-consequence.md) documents the claim envelope). A lookup result never becomes the user's motive by itself — see [What a found fact may become](#what-a-found-fact-may-become).

## Three tiers

### L0 — position context, standing on every `consider` call

Before answering, fetch the small position packet for the premise ticker:

- the current price, beside the premise price;
- the source's own recent-move readings — the day move, and whatever week, month, or 52-week change figures the source itself publishes;
- the 52-week high and low.

Transcribe the source's ready-made readings; do not derive new ones. Stating both prices and saying in words which side of the range the trade sits on is judgment and belongs in the answer; computing a new percentage is not transcription. Use a recognized market-data source — the same standard as [price-feed.md](price-feed.md); yfinance's ready-made fields are the default when the host can run it.

L0 is standing input because this route's own headline questions are position-in-time questions. It is fetched even when the user's question is pure book arithmetic — but like every other fact, it appears in the visible answer only where it earns a place. It is not a search: no L0 reading triggers further lookup by itself. If the host cannot reach a price source, say so — "current price unavailable, reading on cost basis" — and answer from the book; the deterministic answer is never blocked.

### L1 — event lookup, on trigger

Look up the current event record only when at least one of these holds and the answer could materially change the question or the judgment:

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

## When an event or dimension lookup does not happen

L0 stands on its own; L1 and L2 are skipped when:

- the question is fully answered by the recorded book ("what does adding this do to my weights?");
- the stated reason is clear and stable, and no current fact is needed to understand the decision;
- the fact could not alter the lead judgment, the counter-case, or one necessary question to the user;
- the packet is already answered — do not keep searching past it;
- the user asked not to browse, or the host has no browse capability: state the gap and ask for the source or the reason instead. Never invent, and never read "nothing found" as "no risk".

Generic company research, news recaps, sentiment collection, market-wide discovery, price targets and forecasts are out of scope on this route regardless of trigger.

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

Two limits, and the lookup ends at whichever binds first:

- **Count ceiling:** one primary source, at most one baseline or countercheck. A third retrieval is justified only to resolve a named contradiction. This is the hard latency line on this segment (#603 owns route-level time budgets); it is a budget, not a proof standard.
- **Sufficiency floor:** stop when the packet's four cells are honestly filled. If the ceiling arrives first, name the empty cells in the answer — "I could not establish the prior baseline" is an honest state, exactly as an `unmapped` condition is. An empty cell is never padded with an inference.

## What a found fact may become

A lookup result can occupy exactly one of three roles, and the transitions are never automatic:

1. **`public_fact`** — what an identified source stated, with `source` and `as_of`.
2. **`agent_judgment`** — your read of whether it is new, material, or decision-relevant, labelled as yours.
3. **the user's `why_now`** — only after the user confirms it in their own words.

A company announcing guidance today does not prove the user is acting because of it. When `why_now` was missing and L1 found a candidate event, ask one grounded question and accept "not this" without steering:

> The closest public change I can find to this decision is [event, dated]. Is that the main reason you want to act now — and if not, what actually changed your read?

The wording is illustrative, not a template. If nothing material was found, say so and ask for the real trigger.

Retrieved context is material for judgment, not script. It enters the visible answer only where it earns a place in the lead, the counter-case, or a necessary question — the same salience selection engine facts pass through. A lookup that ends as a pasted news summary has replaced a disclaimer dump with a news dump, and both fail the same way.
