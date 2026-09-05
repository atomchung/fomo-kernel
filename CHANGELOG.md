# Changelog

Notable changes to FOMO Kernel. Versions follow semantic versioning; while the
major version is `0`, a minor bump may change a contract.

## [Unreleased]

### Limits moved onto the claim they protect (#827)

- The always-on boundary file forbade "rankings" and "derived analysis" without
  saying whose, and forbade arguing a considered trade from anything but
  `consider`'s output — while the entry beside it mandates candidate ranking,
  labelled judgment, and a deciding reason drawn from the user's own record.
  Each prohibition is now scoped to the fact it protects: a *portfolio-derived*
  number or ranking, a *portfolio* figure the engine did not emit, and a
  considered trade's *portfolio consequence*. Comparing and ranking candidates
  the user asked about is judgment, carries its label, and enters no canonical
  state.
- A risk the engine did not measure is named where the answer's own wording
  would imply it was checked, or where it could change the recommendation.
  That is a materiality test, never the enumeration #830 deleted.

### An answer may support the reader's confidence, not only their action (#827)

- The expression contract's increment gate asked whether deleting a block
  changed the decision, which deleted the support that lets a reader check a
  call or see why one candidate beat another. It now asks whether the reader
  can still take the call, judge how far to trust it, and see why it beat the
  alternative they were weighing. The four named bans are unchanged: restating,
  hedging, explaining a default and inventing a scenario still add nothing.
- A recommendation to wait now owes what a directional call owes in its
  falsifier — the evidence that settles it and the next point it can be
  checked. A scheduled date is not itself a reason to wait.
- No character-count ceiling and no new registry ID: both amendments land in
  the mother chapter, which is where the freeze says a shape fix goes.

### The record read reaches standing rules, and keeps what bounds a quote (#844)

- What is read now names the user's **standing decision rules** alongside the
  per-name thesis, falsifiers, open questions, prior decisions and stances, and
  says where to start: the names in play and those rules, then what they name —
  not a sweep of the whole folder and not a filename convention.
- A quote carries what bounds it. A condition keeps its threshold, a thesis its
  falsifier, a stance whether it authorized acting yet; quoting a line the user
  marked not-yet-actionable as an action basis is a misquote. Prefer their
  newest statement, name a real conflict instead of silently picking a winner,
  and argue any departure from their recorded preference from the evidence that
  changed.

### An unsettled consideration reaches the next one (#827)

- `consider` may now return `unresolved_prior`: at most one earlier
  consideration of the same ticker that was never settled — the direction, the
  day it was asked, the user's own stored words when both were supplied, and
  the `evaluation_id` that closes it. It carries no `decision` and no
  `decided_on`, because an open row is a question that was asked, never a
  decision and never proof of one.
- Its one use is to ask what the user did, once, and only when they have not
  already said. Their answer goes back through `consider --resolve`, which is
  what makes it `prior_decision` next time. Until this the identifier
  `--resolve` needs was emitted only in the response that minted it, so a
  consideration left open on this route could never be closed on it.
- Emitted beside the row, never stored on it, and absent rather than null. No
  number, identity or obligation changes.

### The user's own record comes first (#844)

- The skill is installed inside the user's investing folder and reads what they
  already wrote about the names in play — thesis, falsifiers, open questions,
  prior decisions, stated stances — before it recommends. It quotes that record
  as theirs, with the note and its date; an AI-maintained status field is a
  tool's note about the user, not their belief. The deciding reason may come
  from the record; the engine's consequence and rule collisions check the pick.
- `--agent-case` gains a fourth claim class, `user_record`, carrying `source`
  and `as_of` like a public fact and nothing else, so a quoted note no longer
  has to wear another label. The expression contract's C1 now names four
  provenances.
- `references/trade-consequence.md`'s opening comparison is revised in place:
  its deciding reason is now the user's own recorded condition, with the
  engine's consequence as the check; the pre-#844 text is in git history.
- The README install puts the skill under `<your investing folder>/.claude/skills/`
  and says what is read there. A quoted note is stored only inside a
  consideration's own recorded case, on the user's machine, never elsewhere.

## [0.1.0] — 2026-08-06

The first tagged release. Everything before it was untagged `main`.

### Two decision routes

- **Before a trade.** `consider` takes one contemplated trade against the
  recorded book and returns the deterministic consequence: post-trade weight,
  concentration and driver overlap, cash effect, which of the user's own
  recorded rules it collides with, and the portfolio basis behind each figure.
  The answer that follows owes a two-sided case and must state what it could not
  check. A decision brought with no recorded book is framed rather than refused,
  and nothing from that path is persisted.
- **After trades.** A broker export or transaction history becomes one behavior
  review: per-position diagnosis, sizing and averaging-down and exit patterns,
  supported performance attribution, and at most one rule the user chooses. The
  next review opens by reconciling that rule.

### The engine contract

- `skills/fomo-kernel/engine/review.py` is the only entry point, with sixteen
  subcommands. Every other path bypasses lifecycle validation, required-question
  gates, and canonical session state.
- The engine owns every number, identity, `rule_effect`, and state transition.
  The agent contributes what code cannot settle — motive, the strongest
  counter-case, plain-language explanation — and cannot become a second source
  of portfolio truth.
- Completed reviews commit as immutable canonical session bundles through an
  atomic staging rename. Identical retries are no-ops; conflicts fail closed. An
  interrupted session resumes without re-asking what was already answered, and a
  failed projection rebuilds from the committed session rather than from the
  user.
- Theses are an append-only ledger keyed to cycle identity. A durable `cycle_id`
  is minted from the canonical ticker, so the review lane, the ledger, and the
  exit queue all name the same cycle.
- The recorded book is snapshot-anchored and replayed from trade events. Share
  counts and prices are rebased onto a declared split basis, and a price whose
  split basis disagrees with the share count it would be multiplied by is
  refused rather than silently mixed.
- Mixed markets are supported without inventing a combined benchmark: TW renders
  against `^TWII` and US against `SPY`, and no total alpha is synthesized across
  them.
- Missing facts enter an honesty ledger instead of defaulting to zero. A price
  the engine cannot retrieve is a disclosed gap, not an interpolation.
- Questions and cards render in English, Traditional Chinese, and Simplified
  Chinese. Locale changes the copy, never an engine fact.

### Privacy

- No backend. The repository has no account service or upload endpoint, and
  nothing is sent to the author.
- Source files, normalized snapshots, canonical sessions, cards, and projections
  stay on the machine running the skill.
- The engine may query public symbols and dates from market-data providers to
  price a book. It never sends broker rows, quantities, costs, motives, or
  cards.
- `card-private.*` is the default output. The share-safe `card-public.md` is
  produced only on request and strips amounts, dates, tickers, exact weights,
  session IDs, and agent free text. Nothing is published automatically.
- Screenshots are transcribed locally by the coding agent; there is no cloud OCR
  path.

### Verification at this tag

The complete offline registry — 58 suites — passes on Python 3.11 and 3.12, and
CI's blocking `product-contract` job runs the product group before merge.

### Known limitations

- **The interactive experience follows the host.** Claude Code provides native
  option controls and inline card rendering. On a host without them, required
  questions can degrade to hand-typed codes and the preview card may not reach
  the conversation — the engine still completes and the local card files are
  still written. See
  [issue #230](https://github.com/atomchung/fomo-kernel/issues/230).
- **Windows cannot finalize.** `prepare` and `preview` run, but durable
  `finalize` fails closed before committed state changes, because the
  implementation requires POSIX locking and directory `fsync`. macOS and Linux
  are unaffected.
- **Long-only.** Short positions and sell-before-buy sequences are out of scope
  and disclosed rather than approximated.
- **A holdings snapshot buys less than a transaction history.** It supports an
  opening structural check; it cannot honestly reveal prior averaging down, exit
  discipline, win rate, payoff, or alpha.

Open defects are tracked in the
[issue tracker](https://github.com/atomchung/fomo-kernel/issues) rather than
enumerated here.

[0.1.0]: https://github.com/atomchung/fomo-kernel/releases/tag/v0.1.0
