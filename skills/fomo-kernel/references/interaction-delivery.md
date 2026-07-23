# Surface adapter contract

Claude, Codex, Cursor, and future hosts use the same Review Plan, schemas, validated question presentations, and rendered artifacts. A host adapter may choose controls and rendering channels; it may not duplicate or reinterpret review logic.

The engine already fail-closes on review content: `preview`/`finalize` reject a missing required answer, invalid private motive provenance, or an absent commitment. This contract also covers what the engine cannot see — whether the host actually *presented* each question, card, and weekly opening memory to the user. That evidence is recorded in a local presentation trace.

**Scope: full-tier reviews only.** A light-tier capture (`state_snapshot.cadence.tier == "light"`, `flows/light-capture.md`) never renders a card and never asks more than one plain-text question, so there is nothing for this contract to verify. Do not call `ux_receipt.py start`/`event`/`verify` for a light-tier session — its `ROUTES` and `verify_rows` checks are defined for `first_review`/`weekly_review`/`snapshot_review`/`test_drive` presenting at least one card, which a capture-only action by design never does.

## Capability resolution

Immediately after `prepare`, resolve exactly one adapter from what the current
host can prove *in this task*. A Skill may name these profiles, but it must not
infer one from the coding-agent name, a plugin manifest, or a previous task.
For example, a host named Codex does not prove that an AppBridge plugin is
installed, loaded, permitted, or able to submit a choice directly.

| Resolved adapter | Use only when | Required delivery |
|---|---|---|
| `plain_text` | Default for Unknown hosts, missing plugins, and unproven capabilities. | Complete lettered questions and inline canonical Markdown card. |
| `native_options` | The host can show one real single-choice control and return its canonical value. | Native question/rule controls and inline canonical Markdown card. |
| `validated_widget` | A host-specific adapter has passed real-host dogfood for direct structured choice submission and rich-card rendering. AppBridge may qualify only after that gate. | Native controls plus widget card, with the same universal fallbacks declared. |

The engine owns the Review Plan, resolved presentation, answer schema,
lifecycle, and card artifacts. An adapter only renders those canonical payloads
and submits their canonical values. It must not invent, paraphrase, reorder,
or omit an option; it must not receive private session/card data merely to
perform this capability probe.

`plain_text` and `markdown_inline` are universal fallbacks. The receipt CLI
defaults to `plain_text`, so an unknown host needs no optional capability flag:

```bash
python3 tools/ux_receipt.py start \
  --session-id <session_id> --client <client> --route <route> \
  --adapter plain_text
```

When a known adapter is actually available, declare its profile and the extra
capabilities it proves:

```bash
python3 tools/ux_receipt.py start \
  --session-id <session_id> --client <client> --route <route> \
  --adapter validated_widget \
  --question-mode native_options --card-mode widget
```

The CLI writes the universal fallbacks into both declarations. It rejects a
`plain_text` adapter that claims optional controls, or a `validated_widget`
adapter missing either native controls or a widget. Old traces without an
adapter remain verifiable for historical evidence, but every new CLI trace
records the resolved route.

The tool writes the trace inside the protected state directory (`~/.trade-coach/ux/<session_id>.jsonl`) — the same trust boundary as the canonical ledger. It is never committed and never published. Question events are also mechanically restricted to mode, surface source, and an opaque digest; they cannot carry the stem, options, ticker, thesis, user statement, or interpretation. After an interruption, append to the existing trace with the same `--session-id`; do not start a new one. (Tests and inspection pass `--state-root` to redirect it.)

Every row the tool writes is stamped with a UTC ISO-8601 `ts` (seconds, e.g. `2026-07-20T13:46:02Z`) at write time. `verify` accepts legacy traces written before `ts` existed, but fails on a malformed value; row order, not `ts`, remains the ordering authority. For a complete multi-stage trace ending in `owner_verdict`, `verify` also emits a `timing_integrity` object. Its status is `credible`, `suspect`, or `not_assessed`; `owner_live_eligible` is `false` for suspect timing and `null` when legacy or incomplete timestamps cannot be assessed.

## Bind a private surface or the engine fallback

The Review Plan keeps display-ready `question` and `options` for every queue row. `add_thesis`, `headline_motive`, `initial_thesis`, and `exit_consistency` may additionally carry an engine-owned `question_opportunity`; `due_revisit`, `rule_breach`, and recent-exit `revisit` remain engine-rendered.

For an eligible opportunity, the agent may author only the private stem, surface labels/descriptions, allowed grounding references, `none_of_above` copy, and one optional clarification in `schemas/question-surface.schema.json`. Keep that candidate outside the repository and bind it through the existing lifecycle entry point before showing it:

```bash
python3 engine/review.py resume --session-id <session_id> \
  --question-surfaces /tmp/fomo-kernel-question-surfaces.json
```

A successful bind returns `question_presentations` with `source=validated_dynamic` and freezes the exact artifact in pending state. Every later `resume` returns the same bytes; a different candidate for that session fails closed. If generation, parsing, grounding, one-to-one mapping, order, or payload-semantics validation fails, use the returned `source=engine_fallback` presentation. A host that cannot author dynamic copy may call `resume --session-id` and use the unchanged fallback directly. The route, kind, trigger, priority, required status, route-varying queue budget, canonical values, payload requirements, numeric facts, and identities never enter the authored surface.

## Domain language, not internal keys

Every surface this contract governs — a dynamic or engine-fallback question, a native control, and the later rule/commitment choice — shows the user clean domain language only (#305). An option's engine `value` (the canonical answer key written to `answers.json`, for example `planned_entry`, `anxiety`, or `swap`) is a machine identifier, not display copy: never show it, echo it, or append it — in parentheses or any other form — next to its `label`, inside a question stem, or anywhere else in rendered text, in either the dynamic surface or the fixed fallback template below. The same boundary covers internal schema and field names: `commitment` is the `answers.json` field the final rule choice is written to, and presenting that field name to the user as "Commitment Rule" (or any other schema-derived term) is exactly the leak this boundary forbids. Ask about a rule, an entry motive, or an exit reason in the domain language the flows and copy already use; never the internal name of the field or enum that stores the answer.

## Present each required question once

Ask one resolved `question_presentations` item at a time, in queue order. With `native_options`, use one single-choice control whose options preserve the resolved label, description, engine-owned semantic anchor, non-empty requirement text, canonical value, and order. When native controls are unavailable, use this exact structural fallback without merging, reordering, or rewriting the resolved presentation:

```text
<question>

A. <label> — <description> — <semantic anchor> — <requirement text when non-empty>
B. <label> — <description> — <semantic anchor> — <requirement text when non-empty>
...

<none_of_above label and description when present>

Reply with one option label: A, B, ...
```

Letters are a presentation mapping only; write the mapped engine `value` to `answers.json`. `semantic_anchor`, `payload_requirements`, and `requirement_text` are engine-owned and cannot be replaced by custom wording. An invalid or ambiguous reply is not an answer. If the resolved presentation includes a clarification, the agent may use that exact frozen wording once inside the same `question_id`, without displaying a second option set; it may not improvise another follow-up.

The text route is a first-class experience: show the full question and one
complete option set; accept a bounded letter reply; confirm the mapped choice
briefly; then show the next complete question. Never hide a required question
in a prior message, replace it with a bare “reply now” prompt, duplicate its
options, or add a host-specific greeting/preamble. An interruption resumes the
same unresolved `question_id` with its complete canonical fallback.

`none_of_above` is not a new canonical choice. Preserve the user's exact words in `response_provenance.user_statement`. A mapped interpretation requires `summary_author=ai_interpretation`, mapping confidence, and explicit user confirmation. If the mapping remains ambiguous, write `choice=skip`, preserve the exact statement in `note`, and mark the private provenance low-confidence and unresolved. Existing gates still apply: `new_evidence` requires claim and source, while planned-tranche and valuation-change answers require their short note.

The engine validates answer completeness, choice validity, provenance, and the clarification limit at `preview`; the local trace does not duplicate those checks. Record only how the frozen surface was presented so mode or copy drift stays visible:

```bash
python3 tools/ux_receipt.py event --session-id <session_id> \
  --event question_presented --mode plain_text \
  --surface-source validated_dynamic --surface-digest <surface_digest>
```

`native_options` and `plain_text` use the same `surface_digest` and write the same canonical answer. The trace rejects extra question-content fields.

On `weekly_review`, surface the opening memory before the first question or card — `prior_commitment` when a prior rule exists, otherwise `prior_skip` — and record it. Record `exit_reason` or `due_revisit` too when the Review Plan carries them:

```bash
python3 tools/ux_receipt.py event --session-id <session_id> \
  --event memory_presented --memory-kind prior_commitment
```

On `first_review` and full-tier `weekly_review`, record `cash_anchor_checked` before the first question or card (#357); a light-tier week follows `flows/light-capture.md`, is never asked, and writes no receipt at all. The cash anchor (`references/data-contract.md`) is resolved before the first surface — on `first_review` before `prepare` even runs, on `weekly_review` after the cadence-tier check (#357 owner ruling 2026-07-23) — read from the source, asked as one short question, or explicitly skipped — so this event is retrospective evidence that the check happened at all rather than a self-reported claim made after the fact; recording it late fails the same way a backfilled weekly opener would. `--cash-outcome` names which of the three happened:

```bash
python3 tools/ux_receipt.py event --session-id <session_id> \
  --event cash_anchor_checked --cash-outcome found_in_source
```

`found_in_source` (the trade statement carried a cash balance row, used directly), `asked_user` (none appeared anywhere, so the user was asked once and answered or declined), or `skipped` (the user explicitly declined to provide one) are the only valid values; the card degrades to the holdings-only pillar exactly as before when no anchor was ultimately supplied. `snapshot_review` (its own envelope states `cash` inline, or omits it) and `test_drive` (never persists an accounting anchor) do not carry this requirement.

When the user's final required answer has arrived in the conversation, record `answers_received` immediately, before calling `preview`:

```bash
python3 tools/ux_receipt.py event --session-id <session_id> --event answers_received
```

This content-free marker makes the "answered → card" machine wait (#236) measurable from the trace as `card_presented(stage=preview).ts - answers_received.ts`.

Every event must be appended immediately after the action it records. After an interruption, continue the existing trace; never replace it or reconstruct earlier events at wrap-up time. Timestamp reversal and a complete owner-verdict walk stamped in less than three seconds are reported as suspect timing because they cannot support interaction-latency or owner-live UX claims without an audit or re-run.

## Artifact generation is not presentation

After a successful `preview`, record `artifact_generated`, then present the complete card inline following `card-delivery.md`, and only then record `card_presented`. A file path or attachment without inline card content is not presentation. Ask for the one commitment only after the preview card is visible. Apply the same generated-versus-presented distinction to the final card.

When the rule choice — the candidate rules, a custom rule, or skip — is shown to the user after the preview card, record `rule_choice_presented` with the presentation mode used (it must be a declared question mode). This event also machine-checks grounding fidelity (#293): whether each candidate's engine-authored `card_plan.candidate_rules[].grounding`, when present, was shown to the user verbatim, so "present each candidate's grounding verbatim... never invent a grounding for a candidate that has none" (the flow instruction above) is no longer enforced by self-discipline alone. Write a transient `--grounding-check-file` (never committed, never persisted, deleted like any other scratch file once the event is recorded) pairing each presented candidate's `id` and engine `grounding` (omitted when the candidate has none) with the exact `presented_text` shown to the user:

```bash
cat > /tmp/fomo-kernel-rule-choice-grounding.json <<'JSON'
{"candidates": [{"id": "candidate_0", "grounding": "<engine grounding text, verbatim from card_plan>"},
                {"id": "candidate_1"}],
 "presented_text": "<the exact text shown to the user for this rule choice>"}
JSON
python3 tools/ux_receipt.py event --session-id <id> --event rule_choice_presented --mode native_options \
  --grounding-check-file /tmp/fomo-kernel-rule-choice-grounding.json
```

The tool performs the verbatim-containment comparison itself and persists only `grounding_expected` (bool), `grounding_verbatim` (bool), and `grounding_hash` (sha256 of the grounding text, only when one was expected) — never the raw grounding or presented text. `verify` fails closed when this evidence is absent or `grounding_verbatim` is not `true`; there is no legacy exemption for this field. This cannot detect a candidate that had no grounding but was presented with a fabricated one — the check only proves fidelity where the engine gave something to be faithful to.

If `widget` was declared, attempt it. When an attempt fails, record `widget_attempt_failed` and paste the canonical Markdown verbatim inline; do not stop at a file link and do not paraphrase the card. A recorded failure authorizes the Markdown fallback for the rest of the session (widget capability is fixed per host, so one failure need not be repeated per stage); presenting Markdown under a declared widget capability with no recorded failure fails verification.

```bash
python3 tools/ux_receipt.py event --session-id <id> --event artifact_generated --stage preview --artifact-path <preview_markdown_path>
python3 tools/ux_receipt.py event --session-id <id> --event card_presented --stage preview --mode markdown_inline
python3 tools/ux_receipt.py event --session-id <id> --event artifact_generated --stage final --artifact-path <final_markdown_path>
python3 tools/ux_receipt.py event --session-id <id> --event card_presented --stage final --mode markdown_inline
python3 tools/ux_receipt.py verify --session-id <id>
```

`verify` fails when a stage's card was not presented after its artifact, when the final card precedes the preview card, when a declared widget silently degraded to Markdown without a recorded failure, or when a weekly opening memory was not surfaced before the first card. It does not re-verify answered questions or the commitment — the engine owns those. `answers_received` and `rule_choice_presented` are content-free latency markers: `verify` enforces their field whitelists; row order remains the presentation authority.

Timing plausibility is a separate integrity signal. Default verification remains compatible with legacy receipts and exits successfully with a `WARN` plus `timing_integrity.status=suspect` when fully stamped rows reverse or the entire owner-verdict trace was recorded in a sub-three-second burst. A suspect result has `owner_live_eligible=false` and must not be cited directly as owner-live UX ground truth. Audit contemporaneous evidence or re-run the walkthrough. Human-graded QA must add `--require-timing-integrity`, which accepts only `credible` timing; legacy no-`ts` receipts still pass ordinary verification but are `not_assessed`, not fresh owner-live evidence.

For owner dogfood, append `owner_verdict` after the final card and verify with both `--require-owner-verdict` and `--require-timing-integrity`; a weekly pass requires controls, card visibility, and remembered context all to pass. When a validated dynamic surface was presented, the verdict must also record whether the question felt specific and whether an available answer fit, using `--question-specificity` and `--answer-fit`. These owner judgments are product gates, not schema-derived claims: a timing warning limits how the receipt may be used but does not erase the owner's stated verdict.
