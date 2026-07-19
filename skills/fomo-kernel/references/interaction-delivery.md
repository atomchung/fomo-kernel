# Surface adapter contract

Claude, Codex, Cursor, and future hosts use the same Review Plan, schemas, and rendered artifacts. A host adapter may choose controls and rendering channels; it may not duplicate or reinterpret review logic.

The engine already fail-closes on review content: `preview`/`finalize` reject a missing required answer or an absent commitment. This contract covers only what the engine cannot see — whether the host actually *presented* each card and the weekly opening memory to the user. That evidence is recorded in a local presentation trace.

## Declare capabilities once

Immediately after `prepare`, declare what the current host can do. `plain_text` and `markdown_inline` are universal fallbacks and must always be declared; add `native_options` or `widget` only when the current surface exposes them.

```bash
python3 tools/ux_receipt.py start \
  --session-id <session_id> --client <client> --route <route> \
  --question-mode plain_text --card-mode markdown_inline
```

The tool writes the trace inside the protected state directory (`~/.trade-coach/ux/<session_id>.jsonl`) — the same trust boundary as the canonical ledger. It is never committed and never published; placement, not scrubbing, is what keeps trade content safe, so the trace carries only session and capability/delivery identifiers and needs no per-field content review. After an interruption, append to the existing trace with the same `--session-id`; do not start a new one. (Tests and inspection pass `--state-root` to redirect it.)

## Present each required question once

Ask one required question at a time, in queue order. With `native_options`, use one single-choice control whose options preserve the engine's label, description, value, and order. When native controls are unavailable, use this exact structural fallback without merging, reordering, or rewriting the options:

```text
<question>

A. <label> — <description>
B. <label> — <description>
...

Reply with one option label: A, B, ...
```

Letters are a presentation mapping only; write the mapped engine `value` to `answers.json`. An invalid or ambiguous reply is not an answer: clarify inside the same question without displaying a second option set. The engine validates answer completeness and choice validity at `preview`; this contract does not re-check them. Record how a question was surfaced so a silent mode downgrade stays visible:

```bash
python3 tools/ux_receipt.py event --session-id <session_id> \
  --event question_presented --mode plain_text
```

On `weekly_review`, surface the opening memory before the first question or card — `prior_commitment` when a prior rule exists, otherwise `prior_skip` — and record it. Record `exit_reason` or `due_revisit` too when the Review Plan carries them:

```bash
python3 tools/ux_receipt.py event --session-id <session_id> \
  --event memory_presented --memory-kind prior_commitment
```

## Artifact generation is not presentation

After a successful `preview`, record `artifact_generated`, then present the complete card inline following `card-delivery.md`, and only then record `card_presented`. A file path or attachment without inline card content is not presentation. Ask for the one commitment only after the preview card is visible. Apply the same generated-versus-presented distinction to the final card.

If `widget` was declared, attempt it. When an attempt fails, record `widget_attempt_failed` and paste the canonical Markdown verbatim inline; do not stop at a file link and do not paraphrase the card. A recorded failure authorizes the Markdown fallback for the rest of the session (widget capability is fixed per host, so one failure need not be repeated per stage); presenting Markdown under a declared widget capability with no recorded failure fails verification.

```bash
python3 tools/ux_receipt.py event --session-id <id> --event artifact_generated --stage preview --artifact-path <preview_markdown_path>
python3 tools/ux_receipt.py event --session-id <id> --event card_presented --stage preview --mode markdown_inline
python3 tools/ux_receipt.py event --session-id <id> --event artifact_generated --stage final --artifact-path <final_markdown_path>
python3 tools/ux_receipt.py event --session-id <id> --event card_presented --stage final --mode markdown_inline
python3 tools/ux_receipt.py verify --session-id <id>
```

`verify` fails when a stage's card was not presented after its artifact, when the final card precedes the preview card, when a declared widget silently degraded to Markdown without a recorded failure, or when a weekly opening memory was not surfaced before the first card. It does not re-verify answered questions or the commitment — the engine owns those. For owner dogfood, append `owner_verdict` after the final card and verify with `--require-owner-verdict`; a weekly pass requires controls, card visibility, and remembered context all to pass.
