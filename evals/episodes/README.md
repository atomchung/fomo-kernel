# Question-episode bank

An **episode** is one miss, frozen as replayable data: a synthetic state
fixture, the question that was asked, and the answers that must pass or fail.
The unit is **one question, answered** — the thing the product now has to get
right at the moment of the trade
([decision-fomo-kernel-shape.md](../../docs/decision-fomo-kernel-shape.md)) —
not one whole review session.

Why the bank exists (#417): dogfood output used to convert entirely into
issues. Useful, but nothing was ever replayed against a later engine, so no
miss could tell us whether it had actually been fixed or had merely stopped
being noticed. An episode is the replayable form of a miss.

```bash
python3 evals/run_episodes.py            # replay the bank (offline, deterministic)
python3 evals/run_episodes.py --list
python3 evals/run_episodes.py EP-002
```

The runner also executes inside `python3 tests/run_all.py`. It is offline and
free; the rubric judge is neither, and stays out (see **The two halves** below).

## Files

| Path | Role |
|---|---|
| `EP-NNN-*.json` | the episodes; ids are sequential, not issue numbers, because one issue can yield several episodes |
| `episode.schema.json` | the readable field contract |
| `../run_episodes.py` | the enforcement: fixture replay, the six checks, the interlocks |
| `../../tests/test_episode_checkers.py` | per-checker mutation probes, and the bank's structural gates |

The schema is documentation; the loader in `run_episodes.py` enforces the same
shape, because the offline suite carries no `jsonschema` dependency. A field the
loader accepts must be a documented property — `test_committed_bank_is_
structurally_valid_and_schema_fields_match` fails if the two drift apart.

## The two halves

`docs/eval-design.md` ranks evidence: deterministic assertion, differential
fixture, LLM judge, human review — strongest cheap layer first.

- **Mechanical half (this runner).** Deterministic, offline, free. Runs in the
  default suite. Answers only questions code can settle: does this number exist,
  is this grounding verbatim, is this limitation disclosed, does this token trace
  to the fixture.
- **Rubric judge (not built).** Whether the answer was *good*: did it answer the
  question asked, was the reasoning genuinely two-sided, could the user overrule
  it on evidence. Non-deterministic and billable, so it stays opt-in and out of
  default CI, and it must be calibrated against owner ratings before its score
  means anything.

The split is not a staging convenience — it is the evidence hierarchy. Do not
ask a judge whether a number reconciles.

## The fixture is replayed, not frozen

Each episode names a mock CSV and a locale; every run calls the real
`review.py prepare` against it, offline (yfinance stubbed, isolated
`TRADE_COACH_HOME`). Nothing about the engine's output is committed here.

That is deliberate. A committed artifact dump would be a hand mirror of engine
output, and the mirror tax is this repository's largest measured cost
([development-guide.md](../../docs/development-guide.md) section 1). Replaying
means an engine change that stops triggering a limitation, stops queueing a
question kind, or renames a candidate rule makes the episode **fail loudly**
instead of grading nothing. A failing episode after an engine change is a
finding to read, not harness flake.

## The checks

Every one is an **invariant** — something the product must never do. None
compares an answer against the wording the product ships today, because the
question layer is being reshaped (#395, #396, #412) and a test that pinned
current phrasing would report the improvement as a regression.

| Check | What it settles | Receipt |
|---|---|---|
| `number_provenance` | every number on a user-facing surface traces to a number the engine emitted | never-loosen rule 1: agent prose derives nothing |
| `honesty_coverage` | each in-scope honesty key is still triggered by the fixture **and** disclosed, in a digit-free sentence | #82, #357 |
| `privacy_trace` | every ticker-shaped token traces to the synthetic fixture or the engine artifacts; no internal position-id format | #274 |
| `surface_hygiene` | no snake_case engine identifier reached a user-facing surface | #262 |
| `locale_purity` | an `en` surface carries no CJK; a localized surface carries no English metric label that locale translates | #262, #356 |
| `condition_integrity` | a user-authored condition survives the engine's own slot validator, reaches the user in their own words, and is described only as far as the evidence goes | #412 |
| `condition_check_integrity` | a per-period result survives the engine's own check validator, every figure in the prose traces to the record, and a lookup that failed is spoken as one | #412 second half, #434 |

Every ban list is derived at run time from an engine source — the copy
catalogs' dimension keys, the plan's own canonical choice values,
`check_card._INTERNAL_KEYS`, `persona_sweep.CJK`, `privacy_lint.POSITION_ID`,
`card_renderer.numeric_claim`. Adding a dimension or a question kind extends the
checks without editing them. Nothing here is hand-mirrored.

**A digit inside an engine-authored span is not a violation.** Frozen surfaces
replay verbatim, and a recorded thesis may itself carry a quantity the user
wrote, so `number_provenance` asks whether the engine emitted the figure — not
whether prose contains one. Quietly adjusting an engine figure is the violation:
EP-005 pins both halves, because a check that punished the quantity there would
forbid faithful replay.

**A passing answer is a witness, not a target.** The first cut of this bank
copied the product's own v1 phrasing into the passing half of three episodes,
which quietly declared "the way it asks today is the correct answer" — while
#395 says one of those questions must be replaced outright and #396 says fixed
options are going away. Worse, it shipped a `grounding_fidelity` check requiring
every word of a candidate-rule description to be a verbatim substring of an
engine field: a behavior oracle in an invariant's clothing, which would have
failed exactly the agent-authored two-sided reasoning #412 is building. The
check is gone and those passing answers are now deliberately *not* the engine's
copy — freely worded, and still passing, which is the demonstration. Owner
direction, 2026-07-26: expect answers to keep getting freer, more varied and
more restrained. If you find yourself writing a check that only the current
template can satisfy, you are pinning behavior; put it in front of the judge
instead.

## The other question: did asking buy anything

An answer check asks "did it lie". The second gate asks the more expensive
question — **what did this question buy** — because a question costs the user
attention, capped at three to five per review by #291, where a dead field only
costs schema clarity.

`QUESTION_CONSUMERS` in the runner declares, for every question kind the engine
can ask, where its answer goes. The list of kinds is read from the Review Plan
schema's own `kind` enum, so a new question that ships without a declaration
fails the gate rather than slipping in. Two proof modes, both read from real
artifacts: `card` (the sink key is referenced by the renderer that delivers the
card) and `state:<key>` (that key exists in a prepared plan's `state_snapshot`,
which is what the next review reads).

**There is deliberately no `reflection_only` state.** Owner ruling,
2026-07-26: "pure reflective value, zero mechanical consumption" is not a
legitimate reason for a question to exist here. The agent reads the Review Plan
every review, so any stored answer *can* be replayed — `headline_motive_events`
already is. A missing consumer means nobody wired it, not that it cannot be
wired. The current shape of an unwired question is the worst of both: the answer
is persisted, paying the storage and privacy cost, and never read, earning
nothing. If an answer genuinely should influence nothing, the honest design is
ask-and-do-not-store.

`KNOWN_UNWIRED` is the one escape, and it is a ratchet, not an exemption: each
entry is pinned to the issue that owns its disposition, a newly unwired kind
cannot join without one, and a kind that becomes wired must leave the list or
the run says so. Today it holds `initial_thesis` and `exit_consistency`, both
tracked in #429 — a user can answer "I chased the momentum" on their three
largest positions and get a byte-identical card.

## The fixture can carry a standing condition

A `condition_crossing` question exists only when the user already committed to
a condition and this period's lookup came back — state no mock CSV can produce,
because a condition is something a person writes. The runner seeds it from the
episode's own answers rather than from a second declaration: any answer carrying
both `condition` and `condition_check` makes the fixture write that slot into
the isolated root and rerun `prepare --condition-checks`. Episodes are minimal
pairs, so the seed is unambiguous, and the state the engine is asked about is
exactly the state the answers are about.

## What the run says it did not grade

Each replay prints an `unmapped` note per answer: how many sentences passed the
hygiene checks and nothing else, because they carry no number, quote no engine
span, and disclose no honesty key. Their substance waits on the rubric judge,
and on #414 for the product-side provenance gate.

This is #412's enum-gated-surface standard turned on the harness — decide what
is decidable, keep `unmapped` as a first-class honest state, never silently drop
— and it is a report, never a failure. Printing nothing would be the
over-trust: it would let "bank replayed clean" read as "the answers were good"
when four of five sentences in an answer may be ungraded prose.

## Why a green run means something

Three interlocks. Without them a bank of passing episodes would be the fifth
kind of fake green — a check that never observed the thing it names
([development-guide.md](../../docs/development-guide.md) section 2).

1. **Every episode carries an answer that must fail, and names the checks it
   must fail on.** A checker decaying into a no-op turns those answers green and
   the runner red. The recorded miss is the mutation, permanently.
2. **Failing the wrong check is a failure.** An episode that fails on some
   other check has stopped grading what it was written to grade. This is the
   interlock that actually catches most no-op mutations — "expected fail, got
   fail" alone would have hidden five of the six.
3. **A declared check with nothing to inspect fails.** `honesty_coverage` in
   front of an episode that puts no key in scope has abstained, not passed.
   Abstentions also do not count toward coverage.

Plus a bank-level coverage report: every declared check must be observed both
passing and failing somewhere, or the run says so — `tests/persona_sweep.py`
learned that lesson the expensive way when its first answer policy lit none of
the surfaces it gated and still reported success.

## What the mechanical half does not prove

Stated plainly, because a check whose limits are unwritten gets over-trusted:

- **`number_provenance` is strong on distinctive magnitudes, weak on small
  integers.** The allow-set is every number the engine emitted, so a derived
  `63%` is caught while a derived `3` probably exists somewhere already.
- **`honesty_coverage` is coverage, not adequacy.** It proves a disclosure
  exists, is digit-free, and is not copy-pasted across keys. Whether it reads
  honestly is judge work. Its scope is the episode's `must_disclose`, because
  an answer to one question legitimately touches part of the ledger; the card
  surface keeps its own equality gate inside `_draft_bundle`.
- **A fabricated *claim* is still ungraded.** #293's miss — an invented
  sentence in the slot where measured facts live — has no mechanical test today,
  so EP-002 is recorded and replayed but ungraded, `blocked_on` #414. The first
  cut of this bank pretended otherwise with a byte rule; see below.
- **`condition_integrity` gets the numeric leak, not neutrality.** A query that
  restates the criterion while dropping the number ("did growth fall below the
  threshold?") passes it. That is the judge's half; the mechanical half catches
  the form the failure actually takes. Its episodes are also deliberate minimal
  pairs — an answer carrying two defects stays red when the gate under test is
  neutered, and grades nothing.
- **`condition_check_integrity` cannot see whether reasoning is genuinely
  two-sided.** What it can settle is where a one-sided push usually breaks: the
  argument needs a fact the record does not have, so it states a figure the
  lookup never returned (EP-008). An answer that argues one direction using only
  real numbers passes it, and waits on the judge. Its blind half is an allow-set
  of words for "not checked", never a required sentence — the wording will keep
  changing, and a check only the current template satisfies is a behavior
  oracle.
- **Presentation is out of reach.** Whether the card actually reached the
  screen is a presentation-trace question (`tools/ux_receipt.py`,
  `card_presented`). #262's third complaint lives there, and no episode can
  grade it.
- **An episode grades an answer, not an agent.** It cannot say whether a live
  agent would produce the repaired answer — only whether a given answer holds.

## Adding an episode

Convert on the spot, while the miss is still in front of you — a miss that only
becomes an issue is a miss nobody replays. Step 6 of
[qa-runbook.md](../../docs/qa-runbook.md) is where a QA run is told to do this;
this section is the how. Step 6 of
[qa-runbook.md](../../docs/qa-runbook.md) is where a QA run is told to do this;
this section is the how.

1. **Find the fixture that reproduces the structure.** Not the data — the
   shape: a candidate rule without a grounding, a triggered `cash_reliability`,
   a queued `headline_motive`. `python3 evals/run_episodes.py --list` shows what
   the bank already leans on; the mock personas differ widely in what they
   trigger.
2. **Keep only the failure structure.** Real-use misses are de-identified
   before conversion (`tools/privacy_lint.py` is the tool for the draft text).
   `privacy_trace` is the mechanical backstop: a symbol or amount that survived
   from a real account will not trace to a synthetic fixture, and the episode
   fails. Never commit a real CSV, ticker, amount, or date.
3. **Write the recorded miss first, then the repaired answer.** Both are
   required. Name in `fails` exactly the checks the miss must trip — run it and
   read the findings rather than guessing.
4. **Run it.** `python3 evals/run_episodes.py EP-NNN`, then
   `python3 tests/run_all.py`.
5. **A new check needs its mutation probe** in
   `tests/test_episode_checkers.py`, clean half and mutated half. A checker that
   stays green under its own mutation is not evidence.

## Growth slots, deliberately empty

Named in #417 and left unbuilt, so the shape does not have to be renegotiated
later:

- **An over-stepping temptation set for model upgrades.** The user asks the
  agent to compute a number, predict a price, or recommend a security before
  Layer 2 exists.
- **A client x model matrix**, once #273 adds those fields to the receipts.
