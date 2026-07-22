# Manual cross-client UX gate

Use this owner-only gate for Claude, Codex, or another interactive host. It tests the experience a headless agent cannot prove. Keep real trade files, Review Plans, traces, cards, and state roots outside the repository.

## First-review pass

1. Prepare one local case through `engine/review.py`. For a same-input cross-client comparison, give each host the same prepared Review Plan for presentation inspection. Do not let two hosts preview or finalize the same pending session; use separate isolated state roots for full lifecycle runs.
2. Start one presentation trace per host with `ux_receipt.py start` (it is written under `~/.trade-coach/ux/`, outside the repository). Begin every unknown host with `--adapter plain_text`; it declares the universal text and Markdown fallbacks. Declare `native_options` or `validated_widget` only when the current host has actually proven that profile.
3. When the plan contains `add_thesis` or `headline_motive`, bind a private surface through `review.py resume --question-surfaces` before display. Confirm the returned source and digest are recorded without copying any question content into the trace. Exercise the unchanged engine fallback in a separate isolated session.
4. Confirm each required question appears once, in order. Native controls must be single-choice. A host without them must use the exact lettered fallback from `references/interaction-delivery.md`; that is a supported mode, not a failed review. For the text route, verify the complete question survives every interruption, the option set is not duplicated, and the next question is complete after a valid reply.
5. For a validated dynamic surface, rate whether the stem felt specific to the supplied context and whether one of the available answers fit. Confirm a valid choice is captured once. Ambiguous input may use only the frozen clarification inside the same question and must not cause the option set to be displayed again.
6. Confirm the full private preview appears inline before any commitment prompt. A path, attachment, or message saying that a card exists fails this gate.
7. Choose one candidate, provide one custom rule, or skip. Confirm the final private card appears inline and contains at most that one user choice.
8. Append `owner_verdict` with controls and card set from the owner's direct experience. For a dynamic surface also pass `--question-specificity` and `--answer-fit`, then run trace verification with `--require-owner-verdict`.

Run this gate once for the universal `plain_text` route on an unknown or
plugin-absent host. Run it again for each proposed `validated_widget` adapter;
the latter does not promote the adapter until the owner observes direct choice
submission and card rendering in the actual host.

## Weekly-memory pass

1. Reuse the same isolated state root from the completed first review and prepare the next review. Do not substitute a fresh root or a separately authored memory summary.
2. Plan to surface `prior_commitment` (or `prior_skip` when no prior rule exists) as the opening memory, plus `exit_reason` and `due_revisit` when those fields are present in the Review Plan, recording each with `memory_presented`.
3. Before the first question, confirm the host explicitly surfaces the prior rule or says that the prior review ended with no rule. When present, confirm the old exit reason and due revisit appear as the engine supplied them rather than as a generic claim that the system remembers.
4. Complete the same control, preview, commitment, and final-card checks as the first review.
5. Append an owner verdict. Mark memory `pass` only if the returning experience made the specific prior context recognizable; state existing on disk is insufficient. Verify with `--require-owner-verdict`.

## Failure handling

- Keep the raw failed trace local.
- Record whether the failure was controls, card visibility, or remembered context.
- Convert only the failure structure into a synthetic mutation test.
- Do not change engine facts or numeric logic to repair a host presentation failure.
