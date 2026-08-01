# Weekly Market Read prototype

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

The host shows value first. The first response has `optional_question.selected`
as `null`, then may ask its one optional question after the complete brief.
When the user skips, stop: the shown brief is already complete. Only on an
answer, rerun the same read-only command with its offered `--focus` value;
that second response has the selected value and a visibly different
current-session watch, without another question. Persistence is outside this
prototype.
