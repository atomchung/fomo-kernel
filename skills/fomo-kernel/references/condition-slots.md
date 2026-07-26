# When the user commits to something the engine cannot compute

The commitment step offers candidate rules the engine already tracks. A user who instead says *"sell if quarterly revenue growth drops under 30%"* is naming something `state.metrics` has never held — and that is the most informative thing a review receives, because it is the part of their thinking the engine's defaults did not anticipate.

It used to be the one thing thrown away: an unknown `metric_key` failed the whole finalize. It is now stored as a **condition slot** in `conditions.jsonl`, in the user's own words, whether or not anyone can check it. The record is the product; a condition that exists only in the conversation is a promise nobody can hold the user to.

## When this applies

At the commitment moment, when the user writes their own rule and it names a quantity outside the candidate rules. Send it as `commitment.condition` alongside `choice: "custom"`.

Not for: a candidate rule (already tracked mechanically), a rule the engine's own metrics express (`metric_key` is the right anchor — the engine's reconciliation is deterministic and free), or a `revise_rule` replacement (that rule is reconciled against problem events every period, and a slot cannot enter that reconciliation).

## Look the quantity up in the same exchange

Before sending the envelope, look the quantity up and show the value back:

> Latest reported quarter: growth of 38%, measured against the year-ago quarter. The fiscal year is offset by one quarter from the calendar year. Is that the comparison you meant?

Asking with the number is categorically different from asking without it. *"I will compare the latest quarter against the year-ago quarter"* is a method the user has to simulate mentally; *"it is 38% right now"* is a fact they react to instantly, and **a wrong basis exposes itself through the number**. It also means a condition that is already met is visible immediately — that is a decision to make now, not a tripwire to watch — and a quantity nobody can find is known before the user walks away believing it is being watched.

Use a recognized source that publishes the figure, the same standard as [price-feed.md](price-feed.md), and record the one you actually read. Never estimate it, never carry it from memory, and never stall the review over a figure you cannot find: an unfindable quantity is a legitimate outcome with a defined result below.

## Envelope

```json
{
  "choice": "custom",
  "condition": {
    "criterion": "sell if quarterly revenue growth drops under 30%",
    "query": "what was the most recent quarterly revenue, and the year-ago quarter?",
    "threshold": {"value": 30, "unit": "%", "direction": "below"},
    "observation": {"value": 38.0, "as_of": "2026-05-20",
                    "source": "Q1 FY2027 results release",
                    "period": "FY2027Q1", "document": "8-K 2026-05-20"}
  }
}
```

- **`criterion`** — the user's sentence, verbatim. Never tidied into the product's phrasing, never translated. If you also send `rule`, it must be the same string.
- **`query`** — a neutral factual lookup. **Never the criterion restated as a question.** A yes/no lookup (*"did growth fall below 30%?"*) steers retrieval toward confirmation: the search returns the nearest matching real event and attaches it to the timeframe your question supplied, so you get a real figure, a real source, and a wrong date — indistinguishable from a correct answer at the point of use. The engine refuses a query carrying the threshold value. Written once and frozen, so a later check re-reads the same question rather than re-deriving it.
- **`threshold`** — the comparison, structured, so the engine performs it. `direction` is which side means the condition is met.
- **`near_line`** *(optional)* — margin in threshold units. Defaults to 10% of the threshold and is frozen at creation.
- **`observation`** *(optional)* — what you found, with `source` and `as_of` (the date the figure describes, not the date you read it). `period` and `document` are what a later check uses to tell new information from the same quarter re-worded.

The engine assigns the rest — identity, tier, and the comparison at commit time. Do not send them.

## What the engine does with it

| What you sent | Result | What to tell the user |
|---|---|---|
| threshold + observation | `researched` — stored, anchored, with the commit-time comparison recorded | the value you found, and whether it is already past their line |
| no numeric threshold | `unmapped` — stored in full | it is in their record, and it is not something that can be checked for them |
| no observation | `unmapped` — stored in full | you could not find the figure, so this one is currently blind |

`unmapped` is an honest state, not a failure: it says the user committed to something real that we could not check this period. Say so plainly. Never present an unmapped condition as watched, and never state a figure for one — nothing was found, so any number would be yours rather than a source's.

A rejected envelope returns a `ReviewError` naming the field. Fix the envelope and rerun; nothing is committed until finalize succeeds.

## What is not built yet

The per-period check — re-looking-up the quantity, adjudicating a crossing with two-sided reasoning, and the user's override — is not implemented. Standing slots come back in `review_plan.state_snapshot.condition_slots` so the user can see what they committed to; present them as their own standing conditions, never as checked-and-fine.
