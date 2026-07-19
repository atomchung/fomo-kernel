# Surface adapter contract

Claude, Codex, Cursor, and future hosts use the same Review Plan, schemas, and rendered artifacts. A host adapter may choose controls and rendering channels; it may not duplicate or reinterpret review logic.

## Declare capabilities once

Immediately after `prepare`, create a privacy-safe UX receipt outside the repository. Declare what the current host can actually do and include every required question id from the Review Plan. `plain_text` and `markdown_inline` are universal fallbacks and must always be declared; add `native_options` or `widget` only when the current surface exposes them.

```bash
python3 tools/ux_receipt.py start \
  --path /tmp/fomo-kernel-ux-<session_id>.jsonl \
  --session-id <session_id> --client <client> --route <route> \
  --question-mode plain_text --card-mode markdown_inline \
  --required-question <question_id>
```

On `weekly_review`, also declare exactly one opening memory expectation: `prior_commitment` when the prior rule exists, otherwise `prior_skip`. Add `exit_reason` and `due_revisit` only when the Review Plan contains those memories. After interruption, append to the existing receipt; do not replace it.

The receipt stores only session and question identifiers, capability/delivery modes, artifact paths, and pass/fail verdicts. Never put question text, answers, card text, tickers, amounts, or other trade content in it. It is execution evidence, not canonical investment state.

## Present each required question once

Ask one required question at a time, in queue order. With `native_options`, use one single-choice control whose options preserve the engine's label, description, value, and order. Record `question_presented` only after the control is visible, and record `question_answered` only after the user's response maps to one schema-valid option plus any required note.

When native controls are unavailable, use this exact structural fallback without merging, reordering, or rewriting the options:

```text
<question>

A. <label> — <description>
B. <label> — <description>
...

Reply with one option label: A, B, ...
```

Letters are a presentation mapping only; write the mapped engine `value` to `answers.json`. An invalid or ambiguous reply is not `question_answered`: clarify inside the same question without displaying a second option set.

```bash
python3 tools/ux_receipt.py event --path <receipt> \
  --event question_presented --question-id <question_id> --mode plain_text
python3 tools/ux_receipt.py event --path <receipt> \
  --event question_answered --question-id <question_id>
```

## Artifact generation is not presentation

After a successful `preview`, record `artifact_generated`, then present the complete preview inline following `card-delivery.md`, and only then record `card_presented`. A file path or attachment without inline card content is not presentation. Ask for the one commitment only after the preview is visible. Apply the same generated-versus-presented distinction to the final card.

If `widget` was declared, attempt it once per session. When that attempt fails, record `widget_attempt_failed` and paste the canonical Markdown verbatim inline; do not stop at a file link and do not paraphrase the card.

```bash
python3 tools/ux_receipt.py event --path <receipt> \
  --event artifact_generated --stage preview --artifact-path <preview_markdown_path>
python3 tools/ux_receipt.py event --path <receipt> \
  --event card_presented --stage preview --mode markdown_inline
python3 tools/ux_receipt.py event --path <receipt> --event commitment_answered
python3 tools/ux_receipt.py event --path <receipt> \
  --event artifact_generated --stage final --artifact-path <final_markdown_path>
python3 tools/ux_receipt.py event --path <receipt> \
  --event card_presented --stage final --mode markdown_inline
python3 tools/ux_receipt.py verify --path <receipt>
```

`verify` fails when an artifact exists but its card was not presented, when a question is missing or duplicated, when the commitment precedes the preview, when a declared widget silently degrades without a failed attempt, or when weekly opening memory was not surfaced. For owner dogfood, append `owner_verdict` after the final card and verify with `--require-owner-verdict`; a weekly pass requires controls, card visibility, and remembered context all to pass.
