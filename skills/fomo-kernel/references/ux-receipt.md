# Presentation trace (ux_receipt)

How to record that questions and cards actually reached the user. The behavior being recorded is defined in [`interaction-delivery.md`](interaction-delivery.md); this file is the command reference. Full-tier reviews, plus the card-free book-refresh lane described below — a light-tier capture presents nothing, so it writes no trace at all.

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

# cash anchor (first_review, full-tier weekly). found_in_source goes before the
# first question or card; provided/declined go after the card they were asked at
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

`--cash-outcome` takes exactly one of `found_in_source` (the statement carried a balance row, so nothing was asked), `provided` (the user was asked at the card beat and gave one), or `declined` (the user was asked and did not). Every value states what the *user's* data or answer decided; there is no value for "the agent decided not to ask". The retired `skipped` was exactly that, and #357's fifth recurrence recorded it correctly and in order while the user was never offered the question — the gate passed and the experience was identical to forgetting. Now a run that never asked can record nothing, and `verify` refuses a trace with no `cash_anchor_checked` on a route that owes one, so "nobody was asked" and "they declined" are different traces.

Position follows the outcome, and `verify` enforces it both ways. `found_in_source` is read before `prepare` runs, so it must precede the first question or card — retrospective evidence rather than a self-report, the same anti-backfill rule as the weekly opener. `provided` and `declined` record a question asked in the same message as the preview card (`data-contract.md`), so they must come *after* the first `card_presented`; a `declined` recorded earlier is refused, because at that point there was no card the question could have been attached to.

When the user provides an anchor, `review.py add-cash` recomputes the review and returns a new session id. **Keep the original session id for the whole trace.** A receipt records one conversation with a user, not one engine session — the same reason a refresh trace is keyed by `refresh_id` and a `consider` trace by `evaluation_id`.

`snapshot_review` states cash inline in its own envelope and `test_drive` persists no anchor, so neither carries this requirement; `input.cash_anchor.status` says `not_applicable` on both, and on a light-tier week.

`answers_received` is a content-free latency marker. It makes the answered-to-card wait measurable from the trace as `card_presented(stage=preview).ts - answers_received.ts`.

When a declared widget attempt fails, record it before falling back to Markdown; one recorded failure covers the rest of the session, since widget capability is fixed per host:

```bash
python3 tools/ux_receipt.py event --session-id <id> --event widget_attempt_failed --stage preview
```

## Rule choice and grounding fidelity

The rule choice is shown in the same message as the preview card, under it. Record `card_presented` first and `rule_choice_presented` second — append order is what says the card was above the choices — then the cash question's outcome if one was owed. Record it with the mode used. This event also machine-checks that each candidate's engine-authored `grounding` reached the user verbatim, so that fidelity is not left to self-discipline. Write a transient check file pairing each presented candidate's `id` and engine `grounding` (omit the key when the candidate has none) with the exact text shown:

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

## A card-free route: the book refresh

`flows/book-refresh.md` renders no card, so the sequence above has nothing to record there. It is still a real user-visible step, and it has its own route rather than borrowing another one — exempting `snapshot_review` from the card check would disable that check for genuine snapshot reviews, which do present cards.

What a `refresh` trace owes instead of a card is a **change surface**: the engine's difference as you narrated it, the report of what was recorded, or the confirmation question when one was raised. Use the engine's own `refresh_id` as the session id, so the trace names the plan it walked.

```bash
python3 tools/ux_receipt.py start \
  --session-id <session_id> --client <client> --route refresh --adapter plain_text

# step 1: the engine's difference, as narrated to the user
python3 tools/ux_receipt.py event --session-id <id> --event change_presented --change-kind diff

# step 2, only when the refresh raised something: the one confirmation question
python3 tools/ux_receipt.py event --session-id <id> --event question_presented --mode plain_text

# step 3: what was recorded — or that nothing was — as reported back
python3 tools/ux_receipt.py event --session-id <id> --event change_presented --change-kind result
```

`change_presented` states that a surface appeared and nothing about what it said; the trace rejects any other field, because the difference it narrates holds tickers and share counts.

The owner verdict still judges what the user actually saw:

```bash
python3 tools/ux_receipt.py event --session-id <id> --event owner_verdict \
  --controls pass --card not_applicable --memory not_applicable --change pass
```

- `--change` is the load-bearing axis here: did what the receipt showed match what actually happened to the book.
- `--card not_applicable` is required rather than optional. Saying "no card was owed" is a claim the gate can check; leaving the axis free would let a lane with no card record a passing card verdict.
- `--controls` follows the trace. A refresh that raised a confirmation must judge it (`pass`/`fail`); a refresh that raised nothing must record `not_applicable`, because there was no control to judge. `verify` refuses either mismatch.

`verify` refuses a `refresh` trace that records any card event — a delivery that cannot have happened — and refuses one with no change surface at all, since a receipt holding only a start row and a verdict has proven nothing. Card-producing routes are unaffected: they still owe both cards, and `change_presented` does not belong on them.

## The second card-free route: the pre-trade evaluation

`review.py consider` (#544 Slice B) renders no card and mutates no book. Its whole product surface is one inline textual answer that must carry the engine-declared challenge (#479; [`trade-consequence.md`](trade-consequence.md), "What the answer owes"), plus one resolution invitation. What its trace owes instead of a card is that **presentation pair**: `evaluation_presented`, carrying machine-computed challenge-delivery evidence, then `resolution_presented` after it. A stored `trade_evaluations.jsonl` row is not delivery, and `verify` refuses a trace that cannot show the pair.

`consider` creates no session, so use the engine's own `evaluation_id` as the session id, the way a refresh trace uses its `refresh_id`.

```bash
python3 tools/ux_receipt.py start \
  --session-id <id> --client <client> --route consider --adapter plain_text

# bounded context questions, when any were asked — each as it is shown
python3 tools/ux_receipt.py event --session-id <id> --event question_presented --mode plain_text

# the challenge delivery, immediately after the answer is shown to the user
python3 tools/ux_receipt.py event --session-id <id> --event evaluation_presented \
  --challenge-check-file <challenge-check.json>

# the resolution invitation, exactly once, after the evaluation
python3 tools/ux_receipt.py event --session-id <id> --event resolution_presented \
  --workflow-state open
```

`--challenge-check-file` points at a transient JSON that never enters the trace, the same nature as the grounding check file. It pairs the `challenge` block **from the `consider` call's own stdout** with the exact answer text shown:

```json
{
  "challenge": {"...": "the emitted block, verbatim — all five keys, uncut"},
  "presented_text": "the exact answer text you showed the user"
}
```

The challenge is emitted beside the evaluation row and never stored, so it must be captured when the command returns — there is no copy on disk to read back, and a truncated or hollowed paste is refused rather than read as a smaller obligation: the block always owes basis facts, always names at least its four unconditional unchecked risks, and always states the two-sided case floor, so a payload below any of those floors cannot have come from the call this event records. It stays auditable afterwards: the block is a pure function of the persisted row, so the recorded `challenge_hash` (sha256 of the block serialized with sorted keys, compact separators) can be recomputed by anyone holding the root.

The tool machine-checks what containment and digits can honestly decide, and persists only booleans, counts and that hash: the user's `quote_verbatim` sentences must appear verbatim (`quotes_verbatim`), every rule collision's own `detail.text` must appear verbatim, every excluded holding's ticker must appear, and every position/concentration/cash number must appear **as digits** at some display precision — `34.3%`, `34%` and `0.343` all state a frozen `0.34344…`, and a disagreeing number counts as missing (`facts_missing`). An engine-vocabulary string (`cost_basis`, `unverified`), a boolean trigger, or an `unchecked` key reaches the user as prose in the conversation's own language, which no offline comparison can judge — that half belongs to the owner's `comprehension` verdict below, and `must_state_total`/`unchecked_total` are persisted so that judgment is made against a stated obligation size rather than from memory. Like the grounding check, the fidelity result is recorded as computed and judged at `verify`: evidence that is absent, malformed, or failing fails the trace with no legacy exemption, and the trace being append-only means a failed delivery voids the run rather than being patched over.

`--workflow-state` records what the invitation left the evaluation as, in the engine's own vocabulary: `open` (invitation shown, nothing settled yet), or `acted` / `declined` / `modified` once the user's word was recorded through `consider --resolve`. `acted` is the user's own statement, never broker-execution proof — no value shaped like "executed" exists, and describing the resolution as an execution record is exactly what this event's fixed vocabulary forbids.

The owner verdict carries the four route-specific judgments, and `card` stays pinned:

```bash
python3 tools/ux_receipt.py event --session-id <id> --event owner_verdict \
  --controls pass --card not_applicable --memory not_applicable \
  --comprehension pass --usefulness pass --friction pass --resolution pass
```

- `--comprehension` — was the engine's challenge understood as presented: the owed facts, the user's own words, and what was never checked actually landed. This is the human half of the fidelity split above.
- `--usefulness` — specific to this book and this trade, versus generic chat advice.
- `--friction` — cheap enough that the owner would reach for it again mid-decision.
- `--resolution` — the invitation was understood as recording the user's word, not as executing anything.
- `--card not_applicable` is required rather than optional, exactly as on `refresh`: stating "no card was owed" is a positive claim the gate can check.
- `--controls` follows the trace, as on `refresh`: a run that asked a bounded context question judges it `pass`/`fail`; one that asked nothing records `not_applicable`.
- `--memory` is left open: whether the right book answered is already visible through the challenge's own `basis` facts, judged under comprehension rather than gated twice.

All four route axes must be `pass` before `verify --require-owner-verdict` accepts the run. `verify` also refuses a `consider` trace that records any card event or any `change_presented` — deliveries this route cannot have made — refuses the pair out of order or duplicated, and refuses these two events on every other route.

## Verify

```bash
python3 tools/ux_receipt.py verify --session-id <id>
```

`verify` fails when a stage's card was not presented after its artifact, when the final card precedes the preview card, when a declared widget degraded to Markdown with no recorded failure, or when a weekly opening memory did not precede the first card. Which of those a trace owes is decided by its declared route — cards, cash anchor, opening memory, change surface, and verdict axes are declared once per route inside the tool, so a route either carries an obligation or it does not, and none of them can be skipped by wording. It does not re-check answered questions or the commitment — the engine owns those.

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
