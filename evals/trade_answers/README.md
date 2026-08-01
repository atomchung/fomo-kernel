# TradeEvaluation answer witnesses

This directory holds bounded, synthetic answer-level evidence for #590. It is
maintainer tooling, not a runtime dependency and not product acceptance.

## Two gates, two separate lanes

The deterministic lane runs the real production gates. It first calls
`answer_provenance.validate_agent_case`. It resolves each witness answer's
`agent_case_ref` against that fixture's frozen `basis`, `consequence`, and
`rule_collisions`, and supplies the frozen context's exact `reason` and
`why_now` as user statements. Unsupported anchors, missing required coverage,
one-sided cases, and a user statement promoted to `public_fact` fail closed
before any model call. `expect_eligible` is a fixture expectation checked
against that result; it is not a trusted eligibility switch. It then applies
the same challenge-delivery fidelity used by the `consider` UX receipt and the
shared answer-number provenance checker to the exact presented prose. A
candidate's captured challenge must also equal the challenge derived from the
frozen fixture byte-for-JSON-value. Missing machine-checkable facts, missing
verbatim user statements, invented numbers or dates, and a mismatched challenge
fail before any model call. Candidate artifacts add one stricter presentation
binding over the character-for-character complete answer. Ordered character-offset
`segments` must partition all of `presented_text`: every validated `agent_case`
claim appears exactly once as an exact `claim_ref`; limitations bind only to
the fixture challenge's existing basis/disclosure/unchecked obligations; the
connective lane is explicitly `agent_judgment`; the single final resolution
declares the existing open/declined/modified workflow options and visibly names
each option marker; and paragraph separators contain whitespace only. An
unlabelled appended or overlapping
sentence therefore cannot borrow a valid structured case's provenance result.
This does not pretend to solve general NLI: whether a labelled limitation or
connective faithfully realizes its declared role remains semantic evidence for
the relevant judge axis, while whether a resolution honestly offers its named
choices remains an owner-review boundary outside this four-axis judge.

The semantic lane runs only for deterministically eligible answers. The judge
sees one answer, the question, and the frozen evaluation facts. It does not see
sibling answers, witness IDs, expected failures, `agent_case_ref`, eligibility
expectations, fixture titles, or maintainer notes. The deterministic and
semantic lanes are separate and may run concurrently; neither lane's verdict
is used as the other lane's input.

## Two run kinds

`fixture_witness` is the calibration/mutation lane. It grades the committed,
orthogonal synthetic answers against their declared labels. It proves whether
the judge reproduces those labels; it does not run today's product generator.

`candidate_output` is the product-regression input lane. A host or generator
captures the exact answer currently shown for the same frozen synthetic
TradeEvaluation, together with the structured case and the emitted challenge,
in a local JSON artifact. `--answer-file` grades that candidate without editing
`TA-001`; all semantic axes target `pass`. Host/model execution remains outside
this first slice, so the generator and revision belong in optional metadata and
the receipt never implies that this script generated the answer.

## What the semantic judge evaluates

| LLM axis | Voice mapping | Relationship to the independent #610 human lane |
|---|---|---|
| `decision_focus` | V1, V4 | Additional #590 diagnostic; #610 has no standalone focus check |
| `internal_consistency` | No direct V-ID | Additional #590 diagnostic; not V5 and not a counter-case verdict |
| `decision_synthesis` | V3 plus the global decision-value posture | Partial evidence for #610's owner check that the answer adds a new connection; structured grounding remains deterministic |
| `caveat_discipline` | V6 | Partial evidence for #610's owner check that the answer still stands after limitation sentences are covered |

The production gate validates the structured `agent_case`; it does not
mechanically prove every sentence in the free prose. The semantic judge is also
not asked to reproduce that gate.

These axes specialize the global voice contract in `docs/output-voice.md`; they
do not create a second product voice contract.

## Run

```bash
python3 evals/judge_trade_answers.py --plan
python3 evals/judge_trade_answers.py
TR_JUDGE_RUNS=5 python3 evals/judge_trade_answers.py TA-001
python3 evals/judge_trade_answers.py --answer-file /tmp/candidate.json --plan
python3 evals/judge_trade_answers.py --answer-file /tmp/candidate.json
python3 evals/judge_trade_answers.py --history 20
```

Candidate artifact shape:

```json
{
  "schema_version": 3,
  "fixture_id": "TA-001",
  "answer_id": "current-consider-output",
  "agent_case": {
    "for": [{"claim": "First exact surfaced claim.", "provenance": "agent_judgment"}],
    "against": [{"claim": "Second exact surfaced claim.", "provenance": "engine_fact", "anchor": "rule_collisions.rule-fixture-cap.state"}]
  },
  "challenge": {"...": "the complete challenge emitted for this frozen evaluation"},
  "segments": [
    {"kind": "claim_ref", "side": "for", "index": 0, "start": 0, "end": 27},
    {"kind": "separator", "start": 27, "end": 28},
    {"kind": "claim_ref", "side": "against", "index": 0, "start": 28, "end": 56},
    {"kind": "separator", "start": 56, "end": 57},
    {
      "kind": "connective",
      "provenance": "agent_judgment",
      "start": 57,
      "end": 125
    },
    {"kind": "separator", "start": 125, "end": 127},
    {
      "kind": "limitation",
      "obligation_refs": [
        "must_state[0]", "must_state[1]", "unchecked.liquidity",
        "unchecked.valuation", "unchecked.tax", "unchecked.position_fit"
      ],
      "start": 127,
      "end": 162
    },
    {"kind": "separator", "start": 162, "end": 164},
    {
      "kind": "resolution",
      "workflow_options": ["open", "declined", "modified"],
      "start": 164,
      "end": 214
    }
  ],
  "presented_text": "First exact surfaced claim. Second exact surfaced claim. That makes the choice depend on whether the exception is deliberate.\n\nThis is a recorded-book limitation.\n\nYour call: keep it open, decline it, or modify it.",
  "generator": {"host": "optional", "model": "optional", "revision": "optional"}
}
```

Offsets are Python/Unicode character indexes into the exact `presented_text`,
not UTF-8 byte indexes. The example abbreviates the challenge. A real
limitation segment may cite only obligations that exist in the complete derived
challenge; overall answer coverage remains the production delivery gate. The loader
rejects gaps, overlaps, empty/out-of-range spans, unknown fields or segment
kinds, repeated/omitted claims, unknown limitation references, blank substantive
segments, non-whitespace separators, missing/duplicate/non-final resolution
segments, resolution metadata outside open/declined/modified, and resolution
text that does not visibly name those three option markers. Marker presence is
mechanical evidence, not proof that arbitrary prose offers the choices honestly.

The artifact must stay outside the repository (for example under `/tmp`). It
must contain synthetic fixture output only: `--answer-file` sends its text to
the selected external judge, so it is not a route for a private real-decision
answer. The segmented format is deliberate: this first slice can replay a
normal two-paragraph-plus-resolution answer character-for-character while
refusing unlabelled extra text. A capture author can still misclassify an
unsupported sentence as `connective`, claim that a limitation realizes the
wrong obligation, or negate the choices inside a resolution span even though
the required option words are present. That role laundering is not mechanically
decidable without turning every sentence back into a provenance claim or adding
an NLI gate. The relevant limitation/connective fit belongs to the semantic
axes; resolution meaning remains outside those four axes and requires owner
review.

Backend, model, repeated-vote, fail-closed parsing, and ambiguity behavior are
inherited from `evals/judge_episodes.py`. A tied vote is `ambiguous`; an
unreadable sample is unusable and fails the run even when the remaining votes
agree. Results remain per axis rather than being folded into an aggregate
quality score. The receipt and console include, for every axis, agreement,
disagreement, and ambiguous numerators, denominator, and rates. An answer-axis
is `ambiguous` when its majority ties or any sample for that axis is unreadable
or partial; such a cell is never also counted as agreement. Candidate agreement
means agreement with the explicit all-pass target, not owner calibration.

Each durable receipt records separate source-fixture, candidate, judge-contract,
and per-answer exact judge-input digests as applicable. It also keeps the exact
candidate artifact for `candidate_output` and a content-addressed copy of every
canonical judge call specification. The latter includes the complete prompt or
structured messages plus model, effort, token budget, tool schema, forced tool
choice, CLI argv, retry count, and timeout where applicable. Deleting the
temporary candidate file therefore does not erase what the deterministic gate
or model saw, and a transport-option change cannot masquerade as the same run.
These copies remain in the private local coach root; they are not committed.
Receipt JSON uses reversible ASCII escapes so even malformed Unicode returned
by an untrusted judge cannot erase an otherwise completed run.
The receipt is appended and synced before any result text is written to stdout,
so a closed pipe cannot erase evidence for model calls that already happened.
Before real model calls, a locked store preflight also rejects torn, malformed,
or already-unwritable history. It cannot predict a later filesystem failure;
if final append or sync still fails, the run is explicitly non-evidence.
History prints calibration state and compact per-axis counts rather than showing
an uncalibrated `PASS` without its trust boundary.

## Trust and acceptance boundary

- Fixtures contain synthetic facts only. Real holdings, trades, amounts,
  dates, reasons, or private-repository content never belong here.
- `declared_by: agent` means uncalibrated. Only owner review may change it to
  `owner`; this bank carries no owner ratification.
- #610 still owns its four human checks. In particular, a real V5 counter-case
  and a more-concrete next step have no equivalent LLM axis in this slice.
- An LLM pass is not #610 acceptance and is not product acceptance. It is one
  bounded diagnostic result over one frozen synthetic answer. A candidate pass
  says the captured output cleared this uncalibrated rubric; it does not say the
  harness executed or accepted the product workflow that generated it.

## Adding a witness

Add a new failure shape only after an observed product miss cannot be diagnosed
by the existing orthogonal witnesses. A passing answer shows that the named
invariants can coexist; it is not a reference answer or wording template.
