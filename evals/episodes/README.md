# Question episodes

One episode is `(state fixture, user question, graded candidate answers)`. It is the smallest thing this repository can *re-run* about a miss.

Run them with:

```bash
python3 evals/run_episodes.py
```

## Why this directory exists

Until #417, a dogfood miss had exactly one destination: an issue. Two triage rounds closed fifteen of them, the dogfood root held eighteen `ux_receipt` traces and one archived manifest, and the miss-verdict record `docs/eval-design.md` has specified since 2026-07-14 had **zero** entries. An issue describes a failure well and cannot reproduce it, so every miss was paid for once and never again.

The evaluated unit is deliberately **one question, answered** — not one review session. `tests/test_review_v2.py` owns the lifecycle and `tests/persona_sweep.py` owns the rendered card; neither can say whether the sentence a user actually read was allowed to be written. The product's centre of gravity moved to the moment of the trade ([`docs/decision-fomo-kernel-shape.md`](../../docs/decision-fomo-kernel-shape.md)), and a question is what happens there.

## Two halves, only one of them here

| Half | What it decides | Where it runs |
|---|---|---|
| **Mechanical validators** | Is this answer *allowed*: every figure traceable to an engine artifact, no internal identifiers, one locale, no ledger identifiers, the facts the episode requires present | `evals/run_episodes.py`, offline and deterministic, in the default suite |
| **Rubric judge** | Is this answer *good*: did it answer the question, is the reasoning two-sided, could a user overrule it on evidence | Not built (#417 stage 2). Episodes carry their `rubric` today and the runner ignores it. |

A validator that cannot decide reports `unmapped` rather than passing quietly — `provenance_labels` is the current one, waiting on #414. Silence would read as a clean surface.

## Adding one

The moment to write an episode is when the miss is in front of you, not later. Converting takes about as long as writing the issue body.

1. **Name the failure, not the incident.** `357-account-return-claimed-while-the-cash-gate-is-closed`, not `qa-run-3-bug-2`. The filename stem is the `id` and the runner enforces that.
2. **Cut the fixture down** to the artifacts the answer had to be faithful to. A whole session bundle makes the episode unreadable and pins facts the miss never depended on.
3. **Record the miss verbatim.** Its exact words are the asset; a cleaned-up version grades a failure nobody had.
4. **Write the control.** Every episode needs at least one answer that must pass. A validator asserted only against the miss is unfalsifiable in the direction that matters — a check that failed everything would look perfect.
5. **Name the validators in `expect_findings`.** "It failed" is satisfied by an unrelated regression.
6. **Run it, and expect surprises.** Both `293-...` and the adversarial episode below fired validators their author had not predicted; the honest response was to record the extra finding, not to loosen the check.

The runner fails when any mapped validator fires on no episode. A checker that never fires is not evidence (`docs/eval-design.md`, *Mutation testing*), and the fix is another episode rather than a suppression — which is what keeps this a bank of data rather than a checker that accretes rules (#368's owner ruling).

## Real-use misses

`source.kind: "real_use_miss"` carries an obligation. Only the **failure structure** survives: invent the fixture, invent the instrument, invent the wording, keep the shape of what went wrong. `tools/privacy_lint.py` is no help here — it needs the real CSV as its reference set and that file is, correctly, not in this repository — so de-identification is the author's own work. Never a real ticker, amount, date, or sentence.

## Slots left open

Named in #417 and deliberately not built:

- **Researched-tier adversarial episodes** (#412) — a criterion restated as a yes/no query must be caught; an uncheckable condition must fall to `unmapped`.
- **Over-stepping temptations** — the user asks the agent to compute a number, predict a price, or recommend before Layer 2 exists.
- **A client × model matrix** — once #273 adds those fields to the receipt.
