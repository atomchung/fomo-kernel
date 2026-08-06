# Changelog

Notable changes to FOMO Kernel. Versions follow semantic versioning; while the
major version is `0`, a minor bump may change a contract.

## [0.1.0] — 2026-08-06

The first tagged release. Everything before it was untagged `main`.

What is stable here is the engine: its arithmetic, its identities, its state
transitions, and the offline suite that gates them. What is not yet established
is that the live conversation built on top is more useful than a capable general
agent — that has not been through owner acceptance on real decisions, and this
release does not claim it.

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

- The complete offline registry — 58 suites — passes on Python 3.11 and 3.12,
  and CI's blocking `product-contract` job runs the product group before merge.
- That is mechanical verification of declared contracts. It is not evidence that
  the product is useful on a real decision, that a card reached a user's screen,
  or that response latency is acceptable.

### Known limitations

- **Interactive delivery is verified on Claude Code only.** On hosts without
  native option controls and inline rich rendering, required questions can
  degrade to hand-typed codes and the preview card may not reach the
  conversation, even though the engine completes and the local card files are
  written. See
  [issue #230](https://github.com/atomchung/fomo-kernel/issues/230). The
  cross-client delivery adapter is deliberately deferred past this release.
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
