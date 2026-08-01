# Weekly Market Read prototype

The #683 prototype is a read-only companion to a prepared `weekly_review`.
Run it once after the complete private Review Card and before the existing rule
choice:

```bash
python3 engine/review.py weekly-market-read --session-id <id>
```

It reads the review's frozen `market_context` and existing `ticker_diagnosis`.
It must not invoke Yahoo, another provider, `market_data.resolve`, `prepare`,
or a second price computation. It selects no more than three already diagnosed
held names; no result means the whole block is omitted, never replaced by a
generic market recap.

The output is a session-local `WeeklyMarketRead`: frozen engine facts (with
source/as-of), an explicit engine/book connection, one labelled judgment risk,
one or two `next_week_watch` checks, and at most one optional focus question.
The answer is not stored, does not change card bytes, diagnosis, rule ranking,
commitment, or canonical state. A public L1 event, if a host later adds one,
must follow `market-lookup.md`: one triggered packet maximum, source/as-of on
every public fact, and never infer the user's motive.

The host shows value first. It may ask the optional question only after the
brief, and must show the same complete brief when the user skips. Different
answers must select visibly different current-session watches; persistence is
outside this prototype.
