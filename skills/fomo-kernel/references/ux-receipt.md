# Presentation trace (ux_receipt)

How to record that questions and cards actually reached the user. The behavior being recorded is defined in [`interaction-delivery.md`](interaction-delivery.md); this file is the command reference. Full-tier reviews only — a light-tier capture presents nothing, so it writes no trace at all.

The trace lives in the protected state directory (`~/.trade-coach/ux/<session_id>.jsonl`), the same trust boundary as the canonical ledger. It is never committed and never published. Question events are mechanically restricted to mode, surface source, and an opaque digest: they cannot carry the stem, options, ticker, thesis, user statement, or interpretation. Tests and inspection redirect it with `--state-root`.

Append each event immediately after the action it records. After an interruption, continue the existing trace with the same `--session-id` — never replace it or reconstruct earlier events at wrap-up. Rows are stamped with a UTC ISO-8601 `ts` at write time, and row order — not `ts` — is the ordering authority.

## Start

```bash
python3 tools/ux_receipt.py start \
  --session-id <session_id> --client <client> --route <route> \
  --adapter plain_text
```

The CLI defaults to `plain_text`, so an unknown host needs no capability flag. When a known adapter is genuinely available, declare it with the capabilities it proves:

```bash
python3 tools/ux_receipt.py start \
  --session-id <session_id> --client <client> --route <route> \
  --adapter validated_widget \
  --question-mode native_options --card-mode widget
```

Universal fallbacks are written into both declarations automatically. The CLI rejects a `plain_text` adapter claiming optional controls, and a `validated_widget` adapter missing either native controls or a widget.

## Events

```bash
# each question, as it is shown
python3 tools/ux_receipt.py event --session-id <id> \
  --event question_presented --mode plain_text \
  --surface-source validated_dynamic --surface-digest <surface_digest>

# weekly opening memory, before the first question or card
python3 tools/ux_receipt.py event --session-id <id> \
  --event memory_presented --memory-kind prior_commitment

# cash anchor, before the first question or card (first_review, full-tier weekly)
python3 tools/ux_receipt.py event --session-id <id> \
  --event cash_anchor_checked --cash-outcome found_in_source

# the moment the last required answer arrives, before calling preview
python3 tools/ux_receipt.py event --session-id <id> --event answers_received

# each card stage: generated, then presented
python3 tools/ux_receipt.py event --session-id <id> --event artifact_generated --stage preview --artifact-path <path>
python3 tools/ux_receipt.py event --session-id <id> --event card_presented --stage preview --mode markdown_inline
python3 tools/ux_receipt.py event --session-id <id> --event artifact_generated --stage final --artifact-path <path>
python3 tools/ux_receipt.py event --session-id <id> --event card_presented --stage final --mode markdown_inline
```

`native_options` and `plain_text` share the same `surface_digest` and write the same canonical answer. The trace rejects extra question-content fields.

`--cash-outcome` takes exactly one of `found_in_source` (the statement carried a balance row), `asked_user` (none appeared anywhere, so the user was asked once and answered or declined), or `skipped` (the user explicitly declined). Recording it late fails the same way a backfilled weekly opener does, because the event exists to be retrospective evidence rather than a self-report. `snapshot_review` states cash inline in its own envelope and `test_drive` persists no anchor, so neither carries this requirement.

`answers_received` is a content-free latency marker. It makes the answered-to-card wait measurable from the trace as `card_presented(stage=preview).ts - answers_received.ts`.

When a declared widget attempt fails, record it before falling back to Markdown; one recorded failure covers the rest of the session, since widget capability is fixed per host:

```bash
python3 tools/ux_receipt.py event --session-id <id> --event widget_attempt_failed --stage preview
```

## Rule choice and grounding fidelity

When the rule choice is shown after the preview card, record it with the mode used. This event also machine-checks that each candidate's engine-authored `grounding` reached the user verbatim, so that fidelity is not left to self-discipline. Write a transient check file pairing each presented candidate's `id` and engine `grounding` (omit the key when the candidate has none) with the exact text shown:

```bash
cat > /tmp/fomo-kernel-rule-choice-grounding.json <<'JSON'
{"candidates": [{"id": "candidate_0", "grounding": "<engine grounding text, verbatim from card_plan>"},
                {"id": "candidate_1"}],
 "presented_text": "<the exact text shown to the user for this rule choice>"}
JSON
python3 tools/ux_receipt.py event --session-id <id> --event rule_choice_presented --mode native_options \
  --grounding-check-file /tmp/fomo-kernel-rule-choice-grounding.json
```

Delete the check file afterward like any other scratch file. The tool performs the containment comparison itself and persists only `grounding_expected`, `grounding_verbatim`, and `grounding_hash` — never the raw grounding or presented text. `verify` fails closed when this evidence is absent or `grounding_verbatim` is not `true`, with no legacy exemption. It cannot detect a candidate that had no grounding but was presented with a fabricated one; the check proves fidelity only where the engine supplied something to be faithful to.

## Verify

```bash
python3 tools/ux_receipt.py verify --session-id <id>
```

`verify` fails when a stage's card was not presented after its artifact, when the final card precedes the preview card, when a declared widget degraded to Markdown with no recorded failure, or when a weekly opening memory did not precede the first card. It does not re-check answered questions or the commitment — the engine owns those.

Timing plausibility is a separate signal. Verification stays compatible with legacy receipts and exits successfully with a `WARN` and `timing_integrity.status=suspect` when stamped rows reverse or an entire owner-verdict trace was recorded in a sub-three-second burst. A suspect result sets `owner_live_eligible=false` and cannot be cited as owner-live UX ground truth; audit contemporaneous evidence or re-run the walkthrough. Legacy receipts without `ts` pass ordinary verification but are `not_assessed` rather than fresh evidence.

Human-graded QA adds `--require-timing-integrity`, which accepts only `credible` timing.

For owner dogfood, append an `owner_verdict` event after the final card, then verify with both `--require-owner-verdict` and `--require-timing-integrity`. The verdict carries the owner's own three judgments — `--controls` (could they answer through real controls), `--card` (did the card actually appear), and `--memory` (did it remember the right context, or `not_applicable` on a first review) — and a weekly pass needs all three. When a validated dynamic surface was presented, add `--question-specificity` and `--answer-fit` for whether the question felt specific and whether an offered answer fit.

```bash
python3 tools/ux_receipt.py event --session-id <id> --event owner_verdict \
  --controls pass --card pass --memory pass
python3 tools/ux_receipt.py verify --session-id <id> \
  --require-owner-verdict --require-timing-integrity
```

These owner judgments are product gates, not schema-derived claims: a timing warning limits how the receipt may be used but does not erase the owner's stated verdict.

## Where the misses went

A run's last act is archiving, and for a long time nothing at that moment asked what it had found. #417 measured the result: eighteen receipts, one archived manifest, and zero replayable assets. `findings_recorded` is the moment that asks, and `verify --require-findings` is what makes it unskippable.

Record it once, and before the owner verdict — `verify` enforces that ordering for the same reason it enforces the card sequence: the verdict is the last act, so a disposition recorded after it was reconstructed rather than observed. Each `--finding` is one miss and its disposition, and there are exactly two honest ones: `episode:EP-NNN` for a miss converted into a replayable episode ([evals/episodes/README.md](../../../evals/episodes/README.md)), or `not-episodable:#NN:<why it cannot be replayed>` for one that only an issue can hold — whether the card reached the screen, for instance, is a receipt question, not an answer question.

A converted id is resolved against the bank in this checkout — on the write path *and* again on `verify`, so a hand-authored or later-edited receipt cannot carry a conversion that never happened. Resolution reads each episode file's declared `id`; a file that cannot be read or parsed backs no claim, and cannot stop the gate from running either. Two limits worth knowing: it proves an episode with that id exists, not that the episode is a good one (that is `evals/run_episodes.py`'s job), and where the bank is not reachable at all — a skill directory vendored without `evals/` beside it — the id is checked for shape only. That is the one place gate 7 is on the runner rather than on the tool. The event also rejects any field beyond the dispositions, because this is the row a maintainer is most tempted to paste miss text into and it sits inside the state directory's trust boundary.

A run that observed nothing declares `--no-findings`. That is a real and common outcome; what the gate forbids is inferring it from an absent event.

```bash
python3 tools/ux_receipt.py event --session-id <id> --event findings_recorded \
  --finding episode:EP-009 \
  --finding 'not-episodable:#230:the card never reached the screen — a presentation trace question'
python3 tools/ux_receipt.py verify --session-id <id> \
  --require-owner-verdict --require-timing-integrity --require-findings
```
