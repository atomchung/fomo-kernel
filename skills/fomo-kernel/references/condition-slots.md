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

- **`kind`** *(optional, defaults to `numeric`)* — `numeric` for a line a quantity crosses; `event` for a yes/no occurrence with no line ("sell if the CEO leaves"). An event's verdict is the user's to give — you bring the evidence, they answer — so send `event` rather than inventing a threshold for it. Both kinds are watched from the moment they are committed.
- **`criterion`** — the user's sentence, verbatim. Never tidied into the product's phrasing, never translated. If you also send `rule`, it must be the same string.
- **`query`** — a neutral factual lookup. **Never the criterion restated as a question.** A yes/no lookup (*"did growth fall below 30%?"*) steers retrieval toward confirmation: the search returns the nearest matching real event and attaches it to the timeframe your question supplied, so you get a real figure, a real source, and a wrong date — indistinguishable from a correct answer at the point of use. The engine refuses a query carrying the threshold value. Written once and frozen, so a later check re-reads the same question rather than re-deriving it.
- **`threshold`** — the comparison, structured, so the engine performs it. `direction` is which side means the condition is met.
- **`near_line`** *(optional)* — margin in threshold units, inside which a not-yet-crossed line still deserves a question. Defaults to 10% of the threshold and is frozen at creation. **Required when the threshold is zero** ("sell if free cash flow goes negative" has no magnitude to take a tenth of).
- **`observation`** *(optional)* — what you found, with `source` and `as_of` (the date the figure describes, not the date you read it). `period` and `document` are what a later check uses to tell new information from the same quarter re-worded.

The engine assigns the rest — identity, tier, and the comparison at commit time. Do not send them.

## What the engine does with it

| What you sent | Result | What to tell the user |
|---|---|---|
| threshold + observation | `researched` — stored, anchored, with the commit-time comparison recorded | the value you found, and whether it is already past their line |
| `kind: "event"` | `researched` — stored and watched; the user adjudicates | the watch has started, and the call stays theirs |
| no numeric threshold | `unmapped` / `no_threshold` | it is in their record, and there is no measurable line in it to check |
| threshold, no observation | `unmapped` / `no_baseline` | you could not find the figure, so this one is watchable in principle but currently blind |

`unmapped` is an honest state, not a failure: it says the user committed to something real that we could not check this period. Say so plainly, and use the reason — "there is no measurable line in this" and "I could not find the figure" are different things to hear. Never present an unmapped condition as watched, and never state a figure for one — nothing was found, so any number would be yours rather than a source's.

Whatever you found is kept on the row even when the condition is not watchable, so a later review can read what was known at the time.

The card carries one engine-authored line when there is something to say — a line already crossed, or a condition that is not being watched — and stays silent when the condition is simply being watched and is nowhere near its line. You do not write that sentence; do not add your own.

A rejected envelope returns a `ReviewError` naming the field. Fix the envelope and rerun; nothing is committed until finalize succeeds.

## Every review after: check what is due

`review_plan.state_snapshot.condition_slots_due` is this review's lookup request — the live row of each standing condition, plus what its last check found. It is **bounded at eight** and ordered oldest-last-checked first, so the list rotates instead of growing without limit; `condition_slots_summary` states the total, how many were sent, and how many were held back. Never present the due list as the user's whole record.

For each due condition: run its **frozen `query`** — not a fresh question of your own, and never a yes/no restatement — and submit what came back. Then rerun prepare:

```bash
python3 engine/review.py prepare <trades.csv> --condition-checks /tmp/fomo-kernel-condition-checks.json
```

```json
{"condition_checks": [
  {"slot_id": "slot-...-0",
   "check": {"lookup_status": "ok",
             "observation": {"value": 21.0, "as_of": "2026-08-20",
                             "source": "Q2 FY2027 results release",
                             "period": "FY2027Q2", "document": "10-Q 2026-08-20"}}}
]}
```

Send the same array again in `answers.condition_checks` so the result is recorded. It must be identical, and it must be **complete**: a reading ingested at `prepare` that goes missing from the answers would be rewritten into a "nobody looked" row, so the engine refuses that as well as a reading that changed between the question and the answer.

One entry per condition, and **per condition means per line**: a re-stated criterion keeps its line, so a superseded `slot_id` and the live head are two names for the same thing and submitting both is refused.

**`user_response` and `basis_resolution` are not yours to send.** They record what the *user* said, and they reach a row only from their answer to a question that was actually shown. An envelope carrying either is refused by name.

**Never assert a verdict in prose.** You supply evidence; the engine performs the comparison from the frozen threshold, and for an event the *user's answer is the verdict of record* — the engine only stores it. Saying "the line was crossed" in your own words puts an assertion where a computation belongs.

### The six states of a check

| `lookup_status` | When | What the record says |
|---|---|---|
| `ok` | the query returned a usable figure or fact | the observation, plus the engine's own comparison |
| `failed` | you looked and could not get a usable answer | blind this period, with your `reason` |
| `not_checked` | no lookup was attempted | engine-written for every due condition you did not submit |

and, on an `ok` check, the `information_state` axis: `new_period` (a period nobody has seen), `restated` (same period, different document or figure), `no_new_data` (the same result, re-worded — which cannot spoof newness).

An unfindable figure is a legitimate outcome. Send `{"lookup_status": "failed", "reason": "..."}` and say so plainly — never carry the previous quarter's number forward as though it were this one.

### The two things you may raise

Neither is a verdict; each raises exactly one question and decides nothing.

- **`event_alert: true`** *(event conditions, `ok` lookups only)* — the evidence suggests the occurrence may have happened. A routine event check that found no sign carries its `observation.summary`, no alert, and stays silent.
- **`basis_alert: {note, source?, as_of?}`** — you believe the *measurement* changed underneath the frozen threshold (a restated segment, a shifted fiscal calendar, a metric published differently). **When in doubt, raise it.** A false alarm costs one question; silence about a broken basis poisons every verdict after it.

### What the engine asks the user

- **`condition_crossing`** — at most **one per review**, from a crossing (`met`/`near_line`) or an alerted event. An alerted event outranks any number; among numbers the deepest breach wins. A crossing goes quiet on the card only once the user has actually **answered** it. One that lost the budget, or was asked and skipped, or was never delivered, states its figure and says which of those happened, and the summary counts it as unresolved — a crossed line is never silent. Author the stem yourself (`question_opportunity`, `references/interaction-delivery.md`): it needs **one sentence for acting and one for not**, both built only from the criterion, the line, and what the lookup returned. `overridden` requires a short note — rejecting the engine's own reading is a claim, and it goes into the record.
- **`condition_basis`** — from your alert. It stays visible on the card until it is settled: an alert the user never answered keeps its concern printed and counts as unresolved, exactly like an unanswered crossing. `keep` records that the user declined the doubt. `revise_threshold` / `revise_metric` require `answers.condition_revision` (`{of_line_id, condition}`) carrying the re-stated criterion in the user's own words; the engine writes it as a new row on the same line, never an edit. A line answered as a crossing this review cannot also be re-stated in it, and a condition revision is never a `revise_rule` replacement.

A skip records nothing, and the reading is still stored with the engine's own verdict.

### On the card

One engine-authored line reconciles the prior commitment's condition then-and-now, plus any crossing still unanswered, any basis concern still open, this period's readings for conditions clear of their line, any lookup that failed, and — whenever anything is unsettled **or** the card is showing fewer readings than it took — one sentence saying so. A condition goes quiet only when it is genuinely settled. You do not write those lines; do not add your own.
