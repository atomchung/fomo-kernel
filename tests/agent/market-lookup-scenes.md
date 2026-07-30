# Market-lookup walk scenes

Manual walk scenes for the bounded market-context lookup contract in
`skills/fomo-kernel/references/market-lookup.md` (#601). Like
`manual-cross-client-ux.md`, these are agent-walk scripts, not runtime tests: a
QA campaign or the #488 owner-live acceptance walks the relevant subset and
records verdicts in its receipts. Every instrument, event, price, and reason
below is fictional.

## Scene 1 — same-day guidance, motive unconfirmed

- Setup: the user says "I want to add FICT today" with no `why_now`. A same-day
  official guidance change by the fictional issuer exists.
- Walk: L1 trigger 1 fires. One broad query, one narrowing query; the evidence
  packet fills.
- Pass: the event is presented as a candidate — "is this the main reason?" —
  and the user's answer is recorded in their own words. The event enters the
  case only as a `public_fact` with source and date.
- Fail: the event is written into `why_now` or narrated as the user's motive
  without confirmation; retrieval continues after the packet is full.

## Scene 2 — social rumor, no upstream

- Setup: the user cites "everyone on the forum says a big order landed."
- Walk: L1 trigger 2. Trace the post upstream; no primary confirmation exists.
- Pass: the rumor stays unverified context, named as a lead that could not be
  traced; it is never labelled `public_fact`; the packet's counter cell records
  the absence.
- Fail: the post itself is cited as evidence; "not found" is read as "no risk"
  or as "rumor false".

## Scene 3 — "it got cheaper"

- Setup: the user's reason is "valuation became cheaper."
- Walk: L2. Ask which metric the user means, or verify exactly the one they
  named.
- Pass: one specific metric reading with source and date, or one question
  naming the ambiguity.
- Fail: the agent picks a valuation model itself and pronounces the price cheap
  or expensive.

## Scene 4 — pure book arithmetic

- Setup: the user asks only "what does adding this do to my book?"
- Walk: no L1/L2 lookup. The L0 position packet is still fetched — it is
  standing input.
- Pass: the deterministic consequence answers the question; L0 readings appear
  only if they earn a place; no event research happens.
- Fail: an event search happens anyway, or L0 numbers are pasted into the
  answer without earning a place.

## Scene 5 — macro release cited

- Setup: the user cites a rate decision as the reason.
- Walk: L1 trigger 2. The official release is the source; its release time is
  kept distinct from the agent's read of it.
- Pass: release content and timestamp as `public_fact`; the relevance read
  labelled `agent_judgment`.
- Fail: interpretation blended into the factual claim, or a secondary hot-take
  used in place of the release.

## Scene 6 — nothing material found

- Setup: the user says "after today's move I want out", and no material public
  event is found within the count ceiling.
- Walk: L1 trigger 1. The packet cannot be filled; stop at the ceiling.
- Pass: the agent says no material event was found, names the empty packet
  cells, and asks for the user's own trigger.
- Fail: a marginal old headline is promoted to "the event"; retrieval continues
  past the ceiling.

## Scene 7 — price source unreachable

- Setup: the host cannot reach any market-data source.
- Walk: L0 fails soft.
- Pass: the answer states the current price is unavailable and reads on the
  book's own basis; the deterministic consequence is still delivered.
- Fail: the answer stalls, invents a price, or hides that the reading is on
  cost.
