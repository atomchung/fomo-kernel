# Surface adapter contract

Claude, Codex, Cursor, and future hosts share one Review Plan, schemas, question presentations, and rendered artifacts. A host adapter chooses controls and rendering channels. It does not duplicate or reinterpret review logic.

The engine already fail-closes on review content at `preview`/`finalize`: a missing required answer, invalid motive provenance, or an absent commitment cannot get through. This file covers the part the engine cannot see — whether the host actually *presented* each question and card to a human. Recording that evidence is described in [`ux-receipt.md`](ux-receipt.md); read it when you start a session, not while deciding how to word a question.

**Scope: full-tier reviews only.** A light-tier capture (`flows/light-capture.md`) renders no card and asks at most one plain-text question, so nothing here applies to it. Neither does it apply to a book refresh (`flows/book-refresh.md`), which is not a review at all: it renders no card, holds no session to attach a receipt to, and asks one plain question. Both flows still owe the user that question as its own visible turn — that rule is about the human, not about the review lifecycle, and each states it in its own file.

## Capability resolution

Immediately after `prepare`, resolve exactly one adapter from what this host can prove *in this task*. Do not infer a capability from the agent's name, a plugin manifest, or a previous session — a host named Codex does not prove an AppBridge plugin is installed, permitted, and able to submit a choice.

| Adapter | Use when | Delivery |
|---|---|---|
| `plain_text` | Default for unknown hosts, missing plugins, and unproven capabilities. | Lettered questions and inline canonical Markdown card. |
| `native_options` | The host can show one real single-choice control and return its canonical value. | Native controls, inline canonical Markdown card. |
| `validated_widget` | A host-specific adapter has passed real-host dogfood for structured choice submission and rich-card rendering. | Native controls plus widget card, universal fallbacks still declared. |

`plain_text` and `markdown_inline` are universal fallbacks, so an unknown host needs no capability flag. The text route is a first-class experience, not a degraded one.

## Author a question surface, or use the engine's

Every queue row arrives with display-ready `question` and `options`. `add_thesis`, `headline_motive`, `initial_thesis`, `exit_consistency`, `condition_crossing`, and `condition_basis` may also carry a `question_opportunity`, which invites you to write a grounded, specific stem instead of the generic one. `due_revisit`, `rule_breach`, and recent-exit `revisit` stay engine-rendered.

A `condition_crossing` is the one kind where the engine's own fallback is deliberately the flattest sentence it can write, because the question needs something a template cannot produce: **one sentence for acting on the line and one for not**. Ground both in `context.condition.criterion` and `context.condition.evidence` — the user's own words and what the lookup returned — and nothing else. The engine's comparison is already made; what the question buys is the chance for a user who knows the figure is misleading to say so, which only exists if the stem gives that side real weight.

For an eligible opportunity you may author the private stem, surface labels and descriptions, allowed grounding references, `none_of_above` copy, and one optional clarification (`schemas/question-surface.schema.json`). Keep the candidate outside the repository and bind it before showing it:

```bash
python3 engine/review.py resume --session-id <session_id> \
  --question-surfaces /tmp/fomo-kernel-question-surfaces.json
```

A successful bind returns `question_presentations` with `source=validated_dynamic` and freezes those exact bytes: every later `resume` returns them, and a different candidate for the same session fails closed. This freeze is what lets an interrupted session resume mid-question without the user being asked something subtly different the second time. If generation, grounding, mapping, order, or payload validation fails, present the returned `source=engine_fallback` instead — a slightly generic question that is answerable beats a specific one that misrepresents the queue.

What you author is wording. The route, kind, trigger, priority, required status, queue budget, canonical values, payload requirements, numeric facts, and identities all stay as the engine set them, because the answer you collect is written against those identities.

## Speak domain language, not schema

An option's engine `value` (`planned_entry`, `anxiety`, `swap`) is a machine identifier for `answers.json`. Field names like `commitment` are equally internal. Neither belongs in anything the user reads — not beside a label, not in parentheses, not as a heading such as "Commitment Rule". Ask about a rule, an entry motive, or an exit reason the way the flows and copy already talk about them. A user who sees an enum key learns that they are filling in a form, which is the opposite of the conversation this product is trying to have.

## Present each question once

Ask one resolved `question_presentations` item at a time, in queue order. With `native_options`, use a single-choice control preserving the resolved label, description, semantic anchor, requirement text, canonical value, and order. Otherwise use this structure without merging, reordering, or rewriting:

```text
<question>

A. <label> — <description> — <semantic anchor> — <requirement text when non-empty>
B. <label> — <description> — <semantic anchor> — <requirement text when non-empty>
...

<none_of_above label and description when present>

Reply with one option label: A, B, ...
```

Letters are presentation only; write the mapped engine `value` to `answers.json`. `semantic_anchor`, `payload_requirements`, and `requirement_text` are engine-owned. An ambiguous reply is not an answer. If the resolved presentation carries a clarification, you may use that exact frozen wording once within the same `question_id`; improvising a second follow-up turns a fixed queue into an interview of unpredictable length.

Show the full question and one complete option set, accept the reply, confirm the mapped choice briefly, then move to the next question. An interruption resumes the same unresolved `question_id` with its complete canonical presentation.

Each question needs its own visible turn. Do not leave a required question buried in an earlier message and follow it with a bare "reply now" prompt, and do not repeat an option set the user is already looking at. The engine can see that an answer is missing; it cannot see that the question was hard to find, and a user who scrolls back to work out what is being asked is answering a different question than the one the queue ranked.

`native_options` and `plain_text` present the same frozen surface and write the same canonical answer, so both record the same `surface_digest` — that digest is how copy drift stays visible without the trace ever holding the question text itself.

`none_of_above` is not a new canonical choice. Preserve the user's exact words in `response_provenance.user_statement`. A mapped interpretation requires `summary_author=ai_interpretation`, a confidence, and explicit user confirmation. If the mapping stays ambiguous, write `choice=skip`, keep the exact statement in `note`, and mark the provenance low-confidence and unresolved — an honest gap is recoverable next review, a forced classification is not.

## Surface memory before the first question

On `weekly_review`, open with the memory the engine carries: `prior_commitment` when a prior rule exists, otherwise `prior_skip`. Surface `exit_reason` or `due_revisit` context too when the Review Plan carries them. Continuity is the reason this product exists, and it has to be visible before the first new question, not summarized afterward.

On `first_review` and full-tier `weekly_review`, the cash anchor (`data-contract.md`) is resolved before the first surface — read from the source, asked once, or explicitly skipped.

## Artifact generation is not presentation

After a successful `preview`, present the complete card inline following `card-delivery.md`. A file path or attachment without inline card content is not presentation; the user has not seen the card until its content is in the conversation. Ask for the one commitment only after the preview card is visible, and apply the same distinction to the final card.

When you show the rule choice, present each candidate's engine-authored `grounding` sentence verbatim if the payload carries one — that sentence is what ties a generic rule to this user's actual positions. Never invent a grounding for a candidate that has none.

If `widget` was declared, attempt it. When the attempt fails, paste the canonical Markdown verbatim inline rather than stopping at a file link or paraphrasing the card; one recorded failure authorizes Markdown for the rest of the session.

Record what was actually presented as you go, following [`ux-receipt.md`](ux-receipt.md).
