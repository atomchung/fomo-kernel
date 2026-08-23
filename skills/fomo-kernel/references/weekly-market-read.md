# Weekly Market Read prototype

## The exemplar

One whole weekly brief. The connection between the frozen reading and a
diagnosed holding leads, the bound on what that alert claims follows it, and
the next-week check closes. Read it before writing one.

It is copied verbatim from `tests/agent/expression-witnesses.json`, scene
`weekly_read_connection` — the corpus that
§3.5 of the repository's expression contract (`docs/expression-contract.md`) makes the
binding statement of this shape. Every issuer in it is invented, and
`tests/test_expression_contract.py` fails if the two copies disagree.

```exemplar weekly_read_connection
Volatility rose through the week while your heaviest name was already flagged as too large. Both readings are frozen with the review rather than refreshed today, and valuation was not checked, so this is a concentration alert rather than a claim that the holding is expensive. Watch whether the name's weight and the volatility reading remain elevated next week.
```

The #683 prototype is a read-only companion to a prepared `weekly_review`.
Run its first read only after the complete, current private-card preview and
before the existing rule choice:

```bash
python3 engine/review.py weekly-market-read --session-id <id>
```

It refuses without that preview (including after an `add-cash` recomputation
until its new `preview` has run). It reads the review's frozen `market_context`
and existing `ticker_diagnosis`. It must not invoke Yahoo, another provider,
`market_data.resolve`, `prepare`, or a second price computation. The first
slice has one genuine connection only: a held name already diagnosed as
`too_heavy` and a positive frozen VIX delta in the same review window. It
selects no more than three already diagnosed held names; either missing side
means the whole block is omitted, never replaced by a generic market recap.

The output is a session-local `WeeklyMarketRead`: frozen engine facts (with
source/as-of), an explicit engine/book connection, one labelled judgment risk,
one or two `next_week_watch` checks, and at most one optional focus question.
The answer is not stored, does not change card bytes, diagnosis, rule ranking,
commitment, or canonical state. A public L1 event, if a host later adds one,
must follow `market-lookup.md`: one triggered packet maximum, source/as-of on
every public fact, and never infer the user's motive.

How that brief is said is not this file's to decide: apply the repository's global
expression contract (`docs/expression-contract.md`) — the answer's
shape through its §3 mother chapter, voice V1–V9, disclosure relevance and
placement D1–D7, provenance labelling C1–C4. Source and as-of on a public fact
are C2; the labelled judgment risk is C1. This file owns only what the read may
compute and what it must refuse.

**This route's derivation from §3, and nothing more** (#832): the one optional
question comes *after* the complete brief, never before it. That is a sequencing
parameter on a read whose whole first response is a brief; the reason a brief
leads at all is §3's top floor, which this file no longer restates.

The first response has `optional_question.selected` as `null`, then may ask its
one optional question. When the user skips, stop: the shown brief is already
complete. Only on an answer, rerun the same read-only command with its offered
`--focus` value; that second response has the selected value and a visibly
different current-session watch, without another question. Persistence is
outside this prototype.
