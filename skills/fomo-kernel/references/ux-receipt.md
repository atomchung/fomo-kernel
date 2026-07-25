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
python3 tools/ux_receipt.py event --session-id <id> --event widget_attempt_failed
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
