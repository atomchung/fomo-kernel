---
name: fomo-qa
description: Prepares a clean, consistent QA environment for dogfooding fomo-kernel and walks the real product routes — review, refresh, and what follows — as one continuous campaign in a single conversation. Use when the user says /fomo-qa, "dogfood fomo-kernel", 跑一次 fomo QA, 走一次復盤驗收, 準備乾淨的測試環境, 幫我 QA fomo-kernel. Its purpose is to kill the rework that comes from every session testing a different environment: a fixed version gate (test only on the latest origin/main, refuse when behind) + a clean detached dogfood worktree (never used for development) + a simulated new user (a dogfood-only coach root, cleared with qa_env.sh reset, never the real ~/.trade-coach) + standardized data sources (real trades / mock persona / test-drive). This is the acceptance tool used while developing and maintaining fomo-kernel, not the product itself; to review a real user's trades, use the product skill fomo-kernel instead. Never touches the real records in investment_note.
---

# fomo-qa

This makes "prepare a clean, consistent fomo-kernel dogfood environment" a procedure. **It is a QA tool for development and maintenance**, answering "I changed the engine — walking through as a real user would, is the experience right?", and it guarantees every run tests **the same thing**: the latest `origin/main`.

**This is the mandatory standard path for every fomo-kernel dogfood (v1, fixed 2026-07-20)** — every dogfood starts here. Do not improvise an environment on the spot.

**Cross-client contract source (since 2026-07-21)**: `docs/qa-runbook.md` in the `kol_collector/fomo-kernel` repository (PR #275) defines what counts as a compliant QA run — **seven** gates (version gate / isolated root / receipt throughout / verdict+verify / archived manifest / privacy lint / **findings disposition**). A session missing any of them is not a QA run and its conclusions may not be cited. The seventh was added on 2026-07-27 (#417): a session passing the first six while leaving behind no replayable asset is exactly what this loop actually looked like for a year. That `docs/qa-runbook.md`, together with the repo-root `AGENTS.md`, **must stay independent of this skill and of any personal registry** — fomo-kernel is a public product a stranger clones on any machine, and cannot assume they have this skill. When the two disagree, the runbook wins.

**What this skill is**: the runbook's seven gates automated into a repeatable procedure plus the `qa_env.sh` tool, for ting's own maintenance of fomo-kernel. Since 2026-07-21 it is reachable through `ai-harness`'s discovery registry, so Claude, Codex and Antigravity all call the same canonical content under the same name `fomo-qa` (see `ai-harness/inventory/fomo-qa.json`). **It is not for anyone else** — an external user has no such skill and needs none; they follow the public `docs/qa-runbook.md` above.

> Why it exists: a 2026-07-19 audit found 17 of 18 worktrees behind main (the worst by 28 commits). Dogfood had been running on development worktrees each pinned to its own base — testing some past slice of the product, with no way to learn afterwards which slice. "It runs" is not "it tested the latest version". This skill blocks that mechanically (issue #250).

> **Its place in the eval system**: this is also the execution procedure that `docs/eval-design.md`'s evidence level **4 (human review)** and observation surface 1 (content-free interaction receipts) had always lacked. A walkthrough ends by producing a `ux_receipt` owner verdict, turning the layer eval currently marks "pending owner dogfood" into a machine-readable annotation that accumulates. **Since 2026-07-27 it produces one more thing**: step 6 converts every miss into a replayable episode under `evals/episodes/`, so a dogfood produces a permanent regression asset rather than a one-off observation — that is what "what we get is stable enough to keep" means.

## When to use it

- The user says `/fomo-qa`, "dogfood fomo-kernel", "跑一次 fomo QA", "走一次復盤驗收", "準備乾淨的測試環境"
- After changing the engine, `SKILL.md`, or card rendering, to judge the experience from the user's side
- To confirm a change really runs on the **latest** version and that the card really comes out

**Not** for reviewing a real user's trades — that is the product skill `fomo-kernel`. This skill only makes the acceptance environment clean and consistent; the walkthrough itself still follows the product's `SKILL.md`.

## Coverage (v1, fixed — do not claim beyond it)

This round verifies **L1: environment consistency + engine CLI contract + agent walkthrough behavior**. Completing `/fomo-qa` means "the engine and agent behavior were verified on the latest version". It does **not** mean "the user's experience was verified on every client". The following are **known follow-ups, out of this procedure**; do not claim the experience is green after a run (that is precisely the false-pass trap in [#230](https://github.com/atomchung/fomo-kernel/issues/230)):

- **L2 card visuals** (next round): the card HTML is never actually rendered in a browser and compared by screenshot (today even `test_card_html.py` makes string assertions and takes zero screenshots). Visual consistency, layout and dark mode can only be eyeballed.
- **L3 interaction delivery** (partly an inherent ceiling): "the option buttons really appeared and the user could really click them" is a client-layer fact (Claude native options vs typing by hand in Codex) and cannot be verified headlessly. Here we verify only the contract layer — the card text was pasted into the conversation, the questions were presented — which `tools/ux_receipt.py` can carry. The native-interaction half on Codex is #230's ceiling and rests on the owner's manual verdict.
- **A general HTML interaction document + the fastest plain-text completion experience**: next round.

## Hard isolation guardrails (read first, non-negotiable)

1. **Never touch the real records**: `~/Side_project/investment_note/` holds ting's real investment records. Only the "real trades" data source reads **one** CSV there, read-only; nothing else is read or written.
2. **Coach state is isolated to a dogfood-only root, and the real one is not reachable**: dogfood always uses a separate `~/.trade-coach-dogfood`, fully apart from the `~/.trade-coach` you use with the real product. Clear it with `qa_env.sh reset` (which backs up first and fail-closed refuses to touch the real root or investment_note). The real `~/.trade-coach` is managed only by `reset-fomo-coach.sh`; the dogfood procedure never touches it. Never hand-write an `rm` against any coach root. **`TRADE_COACH_HOME` routes writers only** — it never stopped anything reading the real root, and in #557 an agent that judged the isolated root unfamiliar and empty read the real ledger on its own initiative. Step 0's `isolate` closes that path by replacing `HOME`, and is a gate rather than a request. A dogfood root that looks empty is a fresh dogfood root; never go looking for a fuller one, and never compose an absolute path to the real one — that is the one hole this lane cannot close.
3. **Work only in the dogfood worktree**: every engine command runs inside the detached worktree created by `qa_env.sh up`. `qa_env.sh` is itself fail-closed and only operates on a worktree whose path contains `dogfood`, so a slip cannot discard another session's uncommitted work.
4. **Do not change product code**: QA reads, it does not edit. If the walkthrough finds a bug, write it down and open an issue; do not fix it in the dogfood worktree (it is detached and exists to be tested, not developed).
5. **Public text passes the privacy lint first (bought by the #274 incident)**: the repository is public, and real tickers, specific amounts, or `TICKER#date#seq` position ids **must never** appear in an issue, PR, comment or commit message — text channels count, not just files. If this QA session used real trade data, run every draft destined for GitHub through the lint first (from `skills/fomo-kernel/` inside the dogfood worktree):

   ```bash
   python3 tools/privacy_lint.py --against ~/Side_project/investment_note/trades/fomo/trades.csv /tmp/draft.md
   ```

   Only exit 0 may be posted. On a hit, rewrite as a de-identified description ("N individual stocks", "concentration is high") and re-scan until clean. The output is masked, so the lint result itself is safe to show. Real values stay local (memory, local notes).

`qa_env.sh` lives in this skill's directory. Every `qa_env.sh` path below is written for Claude (`~/.claude/skills/fomo-qa/qa_env.sh`); on Codex use `~/.agents/skills/fomo-qa/qa_env.sh`, and on Antigravity `~/.gemini/config/skills/fomo-qa/qa_env.sh`. All three are symlinks to the same canonical `qa_env.sh` with identical content — pure bash, independent of the client calling it.

**Cross-client execution gaps** (being in the discovery registry only guarantees the skill is found, not that every step runs correctly; see the `ai-harness` task record):
- `qa_env.sh`, `tools/ux_receipt.py`, `docs/qa-runbook.md`'s seven gates, and the Step 0–6 skeleton run as-is on all three clients, with no rewriting.
- If Step 4 presents questions through Claude's native option tool (for example `AskUserQuestion`), that is a Claude-only capability. Codex and Antigravity have no equivalent and must degrade to fixed-format plain-text options, recorded in `ux_receipt.py` as `plain_text` mode, not `native_options`.
- The rendering-pipeline test mentioned in walkthrough rule 1's "try the widget once" (for example some Artifact-style publishing tool on Claude) is a Claude-only MCP tool. Codex and Antigravity must test with whatever equivalent rendering they have, or — having none — record `widget_attempt_failed` and degrade to Markdown. Do not copy the Claude-side tool name.
- `qa_env.sh`'s assumptions about the current working directory and worktree have not been tested against Codex's or Antigravity's own working-directory models. On a first run there, use `status` (read-only) to confirm the behavior matches expectations before going further.

## The fixed procedure

### Step 0 — Isolate this shell, then the version gate (read-only)

`qa_env.sh` **refuses every command** until the shell it runs in has the account's own coach root out of reach (#557), so this line comes before everything else — including `status` — and has to be repeated in every later shell of the campaign:

```bash
eval "$(~/.claude/skills/fomo-qa/qa_env.sh isolate)"
```

It exports the dogfood `TRADE_COACH_HOME` **and** replaces `HOME` with a throwaway directory, so that `~/.trade-coach` — the path `review.py`, `coach.py`, `tools/ux_receipt.py` and any improvised shell command all compose by default — names nothing. Every value in the block is resolved against the account's real home *before* the override, which is why the repo, the dogfood worktree, the receipt archive, `git`'s configuration and Python's user-installed packages keep working after it. Export any `FOMO_DOGFOOD_*` overrides for a concurrent session **before** this line, while `$HOME` is still the account's own.

It is a bounded guarantee, and reporting it as more than that is the failure it was written against: an absolute path typed on purpose still reaches the real root, and only running the campaign in a container would change that. Do not type one.

```bash
~/.claude/skills/fomo-qa/qa_env.sh status
```

At a glance: the latest `origin/main` sha, how far behind the dogfood worktree is, and whether the **dogfood coach state** (the isolated root, not the real `~/.trade-coach`) is a clean new user. **If it is behind, do not go on** — update via Step 1 first. Report `main@<sha>` to the user explicitly: that is the version this QA tested.

`status` also reports one extra line, **this skill's own freshness** — checking the checkout actually running `qa_env.sh` (wherever the symlink points, which is not necessarily the dogfood worktree) against `origin/main`. It is report-only: it never fetches and never blocks `status`, and when behind it prints a copy-pasteable fix.

### Step 1 — Clean worktree (pinned to the latest main)

```bash
~/.claude/skills/fomo-qa/qa_env.sh up
```

Creates (or refreshes) `~/Side_project/kol_collector/fomo-kernel-dogfood` at `--detach origin/main`. This worktree is dedicated to QA and is never used for development. The working directory for every later command:

```bash
cd ~/Side_project/kol_collector/fomo-kernel-dogfood/skills/fomo-kernel
```

### Step 2 — Open the campaign (once per conversation, not once per route)

Steps 0–3 are the **campaign** setup: one worktree, one isolated root, one client/model/effort identity, one acceptance campaign. They happen **once**. Everything after them is a **route run** — one `first_review`, one `refresh`, one `weekly_review` — and a conversation may contain several, each with its own receipt and its own archived `run_id`.

Three identities that are deliberately not one-to-one (#544):

| | What it is | How many |
|---|---|---|
| **Campaign** | this conversation, this worktree, this isolated root | one |
| **Route run** | one product command lifecycle: `prepare → preview → finalize`, or a `refresh` | as many as the session walks |
| **Receipt** | one route-specific, append-only evidence trace | exactly one per route run |

**Never merge route runs into one receipt.** A `first_review` owes two cards and a cash anchor, a `refresh` owes a card-free change surface and no cards at all — one mixed trace could satisfy neither verifier. The reusable unit is the campaign, not the receipt.

**Step 0's `isolate` already routed the whole toolchain into the dogfood-only coach root** — `review.py`, `coach.py` and `tools/ux_receipt.py` **all three** honor `TRADE_COACH_HOME` (ux_receipt since the #269 fix, merged in PR #275), so that one export keeps `prepare`/`preview`/`finalize`/`data-status` and the receipt consistent throughout. If this is a new shell, re-run it before anything else:

```bash
eval "$(~/.claude/skills/fomo-qa/qa_env.sh isolate)"
```

- **Simulate a brand-new user** (the default; runs first-review):

  ```bash
  ~/.claude/skills/fomo-qa/qa_env.sh reset   # back up, then clear the dogfood root to a fresh new user
  ```

- **Simulate a returning user** (runs weekly-review / due-revisit): do **not** reset. Keep the dogfood coach state left by a previous campaign and go straight to Step 3. **Lesson from 2026-07-20: a freshly reset session can never test memory continuity or problem-ledger continuity** (memory is `not_applicable`, and an empty prior problem ledger will not "catch" you). Verifying "did last time's problem follow up?" requires a book that already has a finalized review behind it — do not mistake its absence for a fix working.

Confirm with the user which one to simulate; default to "brand-new user" when unsure. This is a **campaign-level** choice, made once. It is not re-asked before each route run: a campaign that opened fresh and then finalized a `first_review` **is** a returning user for everything that follows, without a reset and without leaving the conversation. That is the cheapest way to reach the returning-user routes, and since #544 it is the documented one.

**This isolation must survive into every later shell** — if commands each start a new shell, re-run the `isolate` line in every one, for every route run in the campaign. `qa_env.sh` refuses when it has not been; the engine and receipt commands do not, because they are product commands a real user runs against their own root, so a shell that quietly lost it writes the dogfood run into the real book. Establish it first, every time.

### Step 3 — Choose a data source (one of three, standardized)

| Data source | Path used in commands | Fits |
|---|---|---|
| **Real trades** (read-only) | `~/Side_project/investment_note/trades/fomo/trades.csv` | True acceptance: it asks about ting's own motives, which exposes the most |
| **Mock persona** | `mock/<persona>.csv` inside the worktree (see `mock/SAMPLES.md`, e.g. `sample_ai_holder`, `sample_tw_mixed`) | Fast, zero privacy risk, reproducible |
| **Test-drive** | `--test-drive` (no CSV) | Demonstration only; `persist:false`, zero writes, isolated root |

The real trade file currently holds roughly 1,125 rows across 76 symbols, mixing Taiwanese and US markets and mixed date formats — good stress-test material.

### Step 4 — Walk through (follow the product's fixed lifecycle; do not rewrite it)

After `cd .../fomo-kernel-dogfood/skills/fomo-kernel` (and confirming Step 2's `export TRADE_COACH_HOME` is live in this shell, so `prepare`/`preview`/`finalize` all land in the isolated dogfood root), follow the **product** `SKILL.md`'s fixed lifecycle. Summary — for detail and edge cases the product's `SKILL.md` / `flows/*` / `references/*` are always authoritative:

```bash
# 1. prepare —— read review_plan.flow_path to decide which flow to follow
python3 engine/review.py prepare <CSV or --test-drive> --language zh-TW
#    for test-drive, note review_plan.state_root; every later preview/finalize/resume needs --root <state_root>

# 2. agent work —— declare host capability, make the qualitative judgments, ask every required
#    question in question_queue, build an inferred thesis for uncovered positions, and write the
#    "no numbers" narrative (answers.json / narrative.json must pass their schemas)

# 3. preview —— validate, then render the private / public previews
python3 engine/review.py preview --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json

# 4. embed the full card preview → ask the user to pick a rule / write their own / skip

# 5. finalize —— atomically commit the canonical bundle
python3 engine/review.py finalize --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json
```

**The UX receipt runs through the whole walkthrough (mandatory — this is what connects QA to eval)**: a walkthrough is not "the engine finished". Every user-visible step goes into the receipt (the product's `tools/ux_receipt.py`). That content-free receipt — only session id, capabilities, pass/fail, no trade content — is the machine-readable annotation feeding eval's evidence level 4.

Below is a **complete, directly copyable `first_review` trace**. The order is a contract, not a suggestion: `verify` hard-checks which events must appear, exactly how many times, and which precedes which. Get it wrong and the whole session is void with no way to repair it (the trace is append-only). Record each line **immediately after the user actually sees that thing**; never save it all for the end.

```bash
# qa-trace: first_review
# 0) Declare host capability right after prepare. --adapter must match the capability
#    set you declare (plain_text / native_options / validated_widget); start adds the
#    universal plain_text and markdown_inline fallbacks itself — do not pass them again.
python3 tools/ux_receipt.py start --session-id <ID> --client claude --route first_review \
  --adapter validated_widget --question-mode native_options --card-mode widget

# 1) Cash anchor (#357): exactly once on first_review / weekly_review, and it must come
#    before the first question and the first card. It happens during prepare, so recording
#    it later is judged out of order.
python3 tools/ux_receipt.py event --session-id <ID> --event cash_anchor_checked \
  --cash-outcome found_in_source

# 2) One row per question asked. Question text never enters the trace: a question from a
#    validated dynamic surface records "source + sha256 of the presented text" instead, and
#    the two must appear together (this replaces the removed --question-id).
python3 tools/ux_receipt.py event --session-id <ID> --event question_presented \
  --mode native_options --surface-source validated_dynamic --surface-digest <64-hex-digest>

# 3) Record this the instant the user answers the last required question, before running
#    preview — it is #236's measurement start for "answered → card".
python3 tools/ux_receipt.py event --session-id <ID> --event answers_received

# 4) Cards are always "artifact first, presented second", and both rows need --stage
python3 tools/ux_receipt.py event --session-id <ID> --event artifact_generated \
  --stage preview --artifact-path <preview-card.html>
python3 tools/ux_receipt.py event --session-id <ID> --event card_presented \
  --stage preview --mode widget

# 5) Record when the "pick a rule / write your own / skip" choice is shown.
#    --grounding-check-file is required (#293)
python3 tools/ux_receipt.py event --session-id <ID> --event rule_choice_presented \
  --mode native_options --grounding-check-file <grounding-check.json>

# 6) The final card after finalize — again artifact first, then presented
python3 tools/ux_receipt.py event --session-id <ID> --event artifact_generated \
  --stage final --artifact-path <final-card.html>
python3 tools/ux_receipt.py event --session-id <ID> --event card_presented \
  --stage final --mode widget
```

> That leading `# qa-trace: <route>` line is not decoration. `qa/tests/test_skill_commands.py` uses it to **actually replay** this file's commands into a trace and send it through `verify` — so an example here that drifts from the CLI or from the event order goes red at commit time, rather than halfway through the next QA run. When adding a block containing `ux_receipt.py` commands, carry this marker; the test blocks a fence that lacks it.

`--grounding-check-file` points at a **transient JSON that never enters the trace** (same nature as `--question-surfaces`: keep it in `/tmp`, out of the repository). The tool performs the verbatim-containment comparison itself and writes only the boolean result and a hash into the receipt; the raw text never lands:

```json
{
  "candidates": [
    {"id": "candidate_0", "grounding": "the engine's own candidate_rules[].grounding sentence"},
    {"id": "candidate_1"}
  ],
  "presented_text": "the exact block of text you showed the user"
}
```

A candidate with no `grounding` omits the field entirely (like `candidate_1`) — **do not invent a sentence to fill it**. That is precisely the half #293 cannot catch and only a human can hold.

**The `weekly_review` route carries one extra opener, and `verify` enforces it** (the trace above is `first_review`; do not copy the opener into it). When `prepare` selects `weekly_review`, show the user the rule agreed last time **before the first question and the first card**. The complete trace differs from the one above only in `start` and this row; everything else — cash anchor, questions, answers received, both card stages, rule choice — is copied verbatim:

```bash
# qa-trace: weekly_review
python3 tools/ux_receipt.py start --session-id <ID> --client claude --route weekly_review \
  --adapter validated_widget --question-mode native_options --card-mode widget
# Opener: exactly one row. One more or one fewer both fail. If last time was a skip with no
# rule set, use --memory-kind prior_skip instead.
python3 tools/ux_receipt.py event --session-id <ID> --event memory_presented \
  --memory-kind prior_commitment
# If the plan also returned exit_reason / due_revisit, record one row each (same
# --memory-kind flag; these do not count as the opener):
#   python3 tools/ux_receipt.py event --session-id <ID> --event memory_presented --memory-kind due_revisit
python3 tools/ux_receipt.py event --session-id <ID> --event cash_anchor_checked \
  --cash-outcome asked_user
python3 tools/ux_receipt.py event --session-id <ID> --event question_presented --mode native_options
python3 tools/ux_receipt.py event --session-id <ID> --event answers_received
python3 tools/ux_receipt.py event --session-id <ID> --event artifact_generated \
  --stage preview --artifact-path <preview-card.html>
python3 tools/ux_receipt.py event --session-id <ID> --event card_presented --stage preview --mode widget
python3 tools/ux_receipt.py event --session-id <ID> --event rule_choice_presented \
  --mode native_options --grounding-check-file <grounding-check.json>
python3 tools/ux_receipt.py event --session-id <ID> --event artifact_generated \
  --stage final --artifact-path <final-card.html>
python3 tools/ux_receipt.py event --session-id <ID> --event card_presented --stage final --mode widget
```

The wrap-up has the same shape as Step 5, except **`--memory` must be `pass` or `fail`**: a weekly session does not accept `not_applicable`, and `verify --require-owner-verdict` refuses it — memory continuity is the entire reason this route exists, so it may not be waived as inapplicable. This block is written out in full rather than pointing back at Step 5 precisely because that difference is the part copying would miss, and it only bites at the moment of archiving:

```bash
# qa-trace: weekly_review
python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded \
  --finding episode:EP-0NN
python3 tools/ux_receipt.py event --session-id <ID> --event owner_verdict \
  --controls pass --card pass --memory pass
python3 tools/ux_receipt.py verify --session-id <ID> \
  --require-owner-verdict --require-timing-integrity --require-findings
```

**That opener is memory continuity itself** — was the rule agreed last time actually brought back and reconciled? For `route == "weekly_review"`, `ux_receipt.py` hard-checks two things: exactly one opener, positioned before the first `question_presented` / `card_presented`. Wrong order still fails, because a row backfilled afterwards cannot prove the user saw it at the time.

### The `snapshot_review` route: a declared book, no trade history

Selected when the user has a position table or screenshot and no transaction history. Transcribe it into the envelope in `references/data-contract.md` (`/tmp/fomo-kernel-positions.json`, never inside the repository), then walk `flows/snapshot-review.md`:

```bash
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json --language en
python3 engine/review.py preview  --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json
python3 engine/review.py finalize --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json
```

Three differences from `first_review` decide the trace, and all three were read off a real run rather than assumed:

- **No cash anchor row.** The route's contract does not carry the #357 pre-flight, because the snapshot envelope declares `cash` inline (`references/ux-receipt.md` says so, and `verify` demands `cash_anchor_checked` only on `first_review` / `weekly_review`). Recording one here proves nothing that the envelope did not already state.
- **No question rows.** The observed plan came back with `question_queue: []` and `card_plan.question_policy` of `{"min": 0, "max": 0, "route": "snapshot_review"}` — the budget is structurally zero, because a snapshot holds no action history to ask about. Do not manufacture a question or a dynamic surface to fill the gap; the flow says so too.
- **The rule choice is still reached.** `preview` returned `candidate_rules` with a `grounding` sentence on each candidate, so `--grounding-check-file` is required here exactly as on `first_review`. This is the route's one real control, and it is what `--controls` judges.

Everything else matches `first_review`: both card stages, artifact before card, `--memory not_applicable` (a snapshot review has no prior period to carry, and unlike `weekly_review` this route accepts that value).

```bash
# qa-trace: snapshot_review
# 0) Declare capability the moment `prepare` returns. The trace's session id is
#    the plan's own session_id.
python3 tools/ux_receipt.py start --session-id <ID> --client claude --route snapshot_review \
  --adapter validated_widget --question-mode native_options --card-mode widget

# 1) No cash_anchor_checked and no question_presented on this route — see above.
#    The latency marker still belongs here, immediately before `preview`: it is
#    what makes the wait until the card appears measurable (#236), and on a route
#    that asks nothing it times the authored thesis_updates/narrative going in.
python3 tools/ux_receipt.py event --session-id <ID> --event answers_received

# 2) The preview card, artifact first. The path is preview's own
#    `private_card_html_path`.
python3 tools/ux_receipt.py event --session-id <ID> --event artifact_generated \
  --stage preview --artifact-path <preview-card.html>
python3 tools/ux_receipt.py event --session-id <ID> --event card_presented \
  --stage preview --mode widget

# 3) The rule choice, with its #293 grounding evidence.
python3 tools/ux_receipt.py event --session-id <ID> --event rule_choice_presented \
  --mode native_options --grounding-check-file <grounding-check.json>

# 4) The final card after `finalize`. The path is its `private_card_html`.
python3 tools/ux_receipt.py event --session-id <ID> --event artifact_generated \
  --stage final --artifact-path <final-card.html>
python3 tools/ux_receipt.py event --session-id <ID> --event card_presented \
  --stage final --mode widget

# 5) Wrap up: findings first, verdict last.
python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded \
  --finding episode:EP-0NN
#   this run genuinely found nothing (a declaration, not an omission):
#   python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded --no-findings
python3 tools/ux_receipt.py event --session-id <ID> --event owner_verdict \
  --controls pass --card pass --memory not_applicable
python3 tools/ux_receipt.py verify --session-id <ID> \
  --require-owner-verdict --require-timing-integrity --require-findings
```

### The `refresh` route: recording the book comes before reviewing it

Once a book exists, a newer holdings view is **not** a review. `prepare --route snapshot_review` refuses any declaration the book-update lane would raise a confirmation for, and names the lane (#530). Its `{"status": "error"}` payload carries this message, observed verbatim on exit code 2:

```text
this holdings view has changes only you can settle before the recorded book can
catch up; run `review.py refresh --snapshot-json ...` first, then review it
```

So the real journey is composed — record, then review — and it produces **two receipts on two routes**, never one. Walk `flows/book-refresh.md` first:

```bash
# step 1: read-only. Writes nothing; returns the frozen diff, a summary, and
# pending_confirmations, keyed by a refresh_id.
python3 engine/review.py refresh --snapshot-json /tmp/fomo-kernel-positions.json

# step 3: adopt, passing that refresh_id back verbatim.
python3 engine/review.py refresh --snapshot-json /tmp/fomo-kernel-positions.json \
  --answers /tmp/refresh-answers.json
```

Then come back to the review with the same declaration; the observed rerun of `prepare --route snapshot_review` succeeded and carried `engine_state.snapshot_reconciliation` with `status: reconciled`, because the refresh had already brought the book up to date.

What makes this route's trace different:

- **A refresh creates no session**, so the trace is keyed by the engine's own `refresh_id` (`references/ux-receipt.md`). Declare capabilities only after step 1 has returned one.
- **No card events at all.** `verify` refuses `artifact_generated`, `card_presented`, `widget_attempt_failed` and `rule_choice_presented` on this route — a card delivery that structurally cannot have happened. What the trace owes instead is a **change surface**.
- **The question row depends on what the engine raised, and so does the verdict.** Step 1 returns `status: pending_confirmation` with a non-empty `pending_confirmations` (the observed run raised a disappearance and an appearance), or `status: ready` with `pending_confirmations: []` when only small, cash, market or currency differences moved. The first shape presents **one** question covering every raised item — never one per ticker — and its verdict carries `--controls pass|fail`; the second presents no question and its verdict must carry `--controls not_applicable`.
- **That `--controls` choice is not recoverable.** Recording `--controls pass` on a refresh that raised nothing is accepted at write time and then rejected by `verify` with `owner controls verdict must be not_applicable on a refresh trace`. The trace is append-only, so the run is void and has to be walked again. Decide it from step 1's `pending_confirmations`, before the verdict, not after.

```bash
# qa-trace: refresh
# 0) Session id = the refresh_id returned by step 1 of the flow above. This lane
#    renders no card, so `widget` has nothing to declare and `--adapter
#    validated_widget` would be a claim about a surface that does not exist here;
#    `native_options` is the honest ceiling on a host with real controls, and a
#    host without them declares `--adapter plain_text` and records the question
#    below as `--mode plain_text` (the form references/ux-receipt.md shows).
python3 tools/ux_receipt.py start --session-id <refresh_id> --client claude --route refresh \
  --adapter native_options --question-mode native_options

# 1) The engine's difference, as you narrated it. change_presented carries only
#    its kind: the diff itself holds tickers and share counts, and no trace ever
#    holds those.
python3 tools/ux_receipt.py event --session-id <refresh_id> --event change_presented \
  --change-kind diff

# 2) Only when step 1 came back `pending_confirmation`: the one question covering
#    every raised item. Omit this row entirely on a `ready` plan, and read the
#    --controls note above before the verdict.
python3 tools/ux_receipt.py event --session-id <refresh_id> --event question_presented \
  --mode native_options

# 3) After the adopt call returns: what was recorded, or that nothing was.
python3 tools/ux_receipt.py event --session-id <refresh_id> --event change_presented \
  --change-kind result

# 4) Wrap up. --card not_applicable is required rather than optional: it is the
#    positive claim that no card was owed. --change is the load-bearing axis —
#    did what the receipt showed match what actually happened to the book.
python3 tools/ux_receipt.py event --session-id <refresh_id> --event findings_recorded \
  --finding episode:EP-0NN
python3 tools/ux_receipt.py event --session-id <refresh_id> --event owner_verdict \
  --controls pass --card not_applicable --memory not_applicable --change pass
python3 tools/ux_receipt.py verify --session-id <refresh_id> \
  --require-owner-verdict --require-timing-integrity --require-findings
```

Archive the two receipts separately. The refresh and the review that follows it are one journey and one `--case-id`, so the second archive is `--state-mode continued --parent-run-id <the first run_id>` — a fresh review archived beside a refresh it actually continued would lose exactly the lineage #520 added.

**Three walkthrough rules (from the 2026-07-20 owner_live audit correction; breaking any one voids that QA session)**:
1. **Declare capability honestly, and try the widget once per session — with the right tool**: the single walkthrough deviation on 2026-07-20 was under-declaring `card_modes` with zero widget attempts. #249's rich HTML card was generated, but the owner saw flat Markdown throughout (the main reason card=fail). A graphical surface must declare `widget`: try the widget first, and on failure record `widget_attempt_failed` before degrading to Markdown — do not let "the artifact was green" stand in for "it was delivered" again.

   **New lesson, 2026-07-21 (see the #230 comments)**: trying the widget does not mean grabbing whatever tool sounds like a renderer. Generic chart/dashboard visualization tools (some MCP `show_widget`/`visualize` tools, for instance) usually carry their own design system and will normalize or strip a large third-party `<style>` block. That is not "the host cannot render rich HTML" — it is the tool behaving as designed, and feeding it the card is using the wrong tool. To verify widget delivery, pick a pipeline that **preserves the supplied `<style>` and HTML as-is without design-system normalization** (for example Claude Code's Artifact-style publishing tools: a page of their own, with no external design system applied). Do not treat a tool as equivalent to what `references/card-delivery.md` calls "graphical surface: render a widget from the engine HTML artifact" merely because its name sounds like "widget". This was mis-diagnosed once — "picked the wrong tool" was reported as "the host has no rendering capability", posted to GitHub as a wrong diagnosis, and corrected only afterwards. Confirm the tool's contract (does it preserve the original CSS?) before concluding.

   **This tool-selection detail belongs here and must not be promoted into fomo-kernel's `docs/qa-runbook.md`**: that document ships with the public product for any stranger who clones it, and deliberately uses host-agnostic language naming no tool ("if the host can render rich content, try it; on failure degrade to canonical Markdown"). That is correct, because an external user's client has none of these Claude MCP tools and a hardcoded tool name means nothing to them. The operational knowledge of "how to tell whether a tool will normalize your CSS" is a Claude-specific MCP-ecosystem detail and belongs only in this skill (`fomo-qa`, for ting's own maintenance of fomo-kernel, not a public contract). Do not let it slip across while editing the runbook.
2. **`--language` follows the conversation language**: a Chinese conversation always uses `--language zh-TW` (stated in the product `SKILL.md`'s Language section; forcing `en` in the 2026-07-20 mock session caused the mixed-language output in #262).
3. **Measure "answered → card"**: the timestamp gap from `answers_received` to the preview `card_presented` is the machine wait in seconds, and it must be reported at wrap-up (#236's re-measurement instrument; `tools/ux_receipt.py --help` is authoritative for events and timestamps).

The complete event sequence lives in the product's `references/interaction-delivery.md` (**arguments per `tools/ux_receipt.py --help`** — the docs drift occasionally; `start --required-question`, for instance, no longer exists in the code). **What fomo-qa changes is promoting this step from "the product recommends it" to "QA cannot skip it"**, because without a receipt this dogfood leaves no machine-readable evidence and cannot enter eval.

**Known `ux_receipt.py` CLI traps** (2026-07-21; two consecutive walkthroughs hit one each, recorded so they are not repeated):
- `artifact_generated` must come **before** the `card_presented` of the same stage, and be recorded the moment the action happens. Backfilling it later — even with correct content — is still judged by `verify` as "card was marked presented before its artifact existed", and an append-only trace has no way back: the session can only be voided and redone. Do not defer receipt-writing to the end of the walkthrough.
- `start --question-mode`/`--card-mode` declare only the capabilities this client has **in addition** (`native_options`/`widget`). The two universal fallbacks `plain_text`/`markdown_inline` are added by `start` itself ([PR #298](https://github.com/atomchung/fomo-kernel/pull/298), merged), and passing them by hand collides with the `--adapter plain_text` check that only the universal fallbacks may be declared.
- **`--adapter` defaults to `plain_text`**, and the `plain_text` adapter may declare **only** the universal fallbacks. So "declared `native_options`/`widget` but passed no `--adapter`" fails at the very first `start`. Pass `--adapter validated_widget` for a graphical surface, or `--adapter native_options` for native options without a widget.
- **The enum cannot express "widget cards but plain-text questions"** ([#337](https://github.com/atomchung/fomo-kernel/issues/337)): `--adapter` binds question capability and card capability into one three-tier enum, and `validated_widget` requires `native_options` as well. On such a host — able to embed HTML but with no native interactive controls — the honest move is to declare `--adapter plain_text` without `widget`, and record "card delivery capability was under-declared" as a finding for that session. **Do not misreport `native_options` to satisfy the enum**: that would make the receipt claim an interactive capability the user never got, which is exactly what #230 exists to prevent.
- `findings_recorded` must come **before** `owner_verdict`: the verdict is the session's last event, and a disposition recorded after it is judged a backfill. This is also why Step 6's episodes are converted during the walkthrough rather than at wrap-up.
- `response_mode`/`response_provenance` apply only to question kinds that support a private surface, such as `headline_motive`/`add_thesis`. Engine-rendered kinds like `due_revisit`/`rule_breach` must not carry those two fields in their answer object at all; doing so reports "own-words mapping is not enabled for this kind".

The QA mindset — watch for these while walking (record what you find; do not fix it here):
- Are the questions on target? Anything irrelevant asked, or a key motive missed? (#238's line of inquiry)
- How long did the machine take from "answered" to "card"? Too long? (#236's 5–10 minute wait; watch how many times preview was rejected and rewritten.) Measure it with rule 3's receipt timestamp gap, not by feel.
- Does the card copy hallucinate numbers? Is the honest disclosure right? Is the rule connected to actual holdings?
- Do Taiwanese stocks / mixed markets / cash / date formats hit any edge-case error?
- **When presenting the candidate rule choice, did the agent quietly reword or invent a `grounding`?** (2026-07-21 lesson, see #293.) `flows/*.md` states that a candidate rule's `grounding` must be quoted verbatim and that a candidate without one may not have a sentence invented for it. **The mechanical check covers only half**: `verify` catches "the engine supplied a `grounding` but the presented text does not contain it verbatim" (#293), comparing against the `--grounding-check-file` you supplied yourself. It **cannot** catch "the candidate had no `grounding` and the agent invented one" — there is no engine text to compare against, and `ux_receipt.py`'s `_grounding_fidelity()` documents that half as an accepted limitation. So the human check remains necessary, aimed at exactly that half: before presenting, read `card_plan.candidate_rules` yourself and confirm each candidate's `grounding` came from the engine and was not written by you.

### Step 5 — Close out this route run (the campaign stays open)

This step ends **one route run**, not the conversation. Archive is not a stop signal: after it, the worktree, the isolated root and the campaign identity are all still live, and the next natural request starts the next route run against the same book. Only an explicit stop from the maintainer reaches this step's own item 4, the cleanup — see "Continuing in the same campaign" below.

1. **Owner verdict + archive the receipt (the core output of QA; do not skip it)**: once the final card is out, give a verdict — could the options be clicked (controls), did the card appear readably (card), did the weekly memory carry over (memory), were the questions specific enough (question-specificity), did the answers map correctly (answer-fit). This is exactly the human-review annotation eval has always lacked.

   **The order is hard**: `findings_recorded` (gate 7) first, `owner_verdict` second. The verdict is the session's last event; a disposition recorded after it is a backfill and `verify` refuses it. Where that row's content comes from is Step 6 — episodes are converted **on the spot** during the walkthrough.

   ```bash
   # qa-trace: first_review
   python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded \
     --finding episode:EP-0NN \
     --finding 'not-episodable:#NN:why this one cannot be replayed'
   # This session genuinely found nothing (omission is not a declaration of none — say it):
   #   python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded --no-findings

   python3 tools/ux_receipt.py event --session-id <ID> --event owner_verdict \
     --controls pass --card pass --memory not_applicable \
     --question-specificity pass --answer-fit pass

   # archive re-runs the same flags; hitting a failure here first is cheaper to fix (must be green)
   python3 tools/ux_receipt.py verify --session-id <ID> \
     --require-owner-verdict --require-timing-integrity --require-findings
   ```

   ```bash
   # Archive: copy the model and effort verbatim from the current host's settings.
   # Never guess, and never fill in unknown/default.
   ~/.claude/skills/fomo-qa/qa_env.sh archive-receipt <receipt-path> mock:sample_ai_holder owner_live \
     --agent-model '<exact-host-model-label>' --effort '<exact-host-effort>' \
     --campaign 'issue:#486' --case-id M0-F1 --state-mode fresh
   ```

   The `--case-id` values are defined by the acceptance issue, never invented here: #486's are `M0-F1`…`M0-F6` (one per user flow) since its 2026-07-29 ruling, plus `M0-T01`…`M0-T10` for the contract lane. Read the issue before the first archive of a campaign — an id that looks plausible but names nothing leaves a manifest that cannot be checked off against anything.

   Archiving produces a **run manifest** (`<run_id>.manifest.json`) recording this dogfood's full provenance: `engine_version` (`main-<sha>`), `agent.client`, `agent.model`, `agent.effort`, `data_source`, `human_involvement`, `owner_verdict`, `receipt_sha256`, plus **which acceptance case this session actually tested**: `campaign` / `case_id` / `state_mode` / `parent_run_id`. Model and effort are host labels supplied explicitly at archive time; the script never infers them from the client name, the commit, the chat context, or anything downstream. Missing any of them, or filling `unknown`/`default`, makes archive fail closed. This lets the report compare models and efforts separately rather than averaging them into one pass rate.

   **Case and state lineage (#520)**: a receipt that verifies proves only that this session's presentation was real. It cannot say which case was tested or what state it started from. Without that, two individually valid manifests still cannot prove #486's matrix was walked rather than the same easy case run five times. So archive now enforces three things:

   | Argument | Value | Rule |
   |---|---|---|
   | `--campaign` | `issue:#486` throughout M0 | Owned by that acceptance issue |
   | `--case-id` | e.g. `M0-U01`, `M0-U02` | Stable identifiers defined in #486; do not invent one on the spot |
   | `--state-mode` | `fresh` \| `continued` | Did this session start from a clean root, or continue an archived earlier run |
   | `--parent-run-id` | the previous session's `run_id` | **Only** for `continued`; supplying it on `fresh` is refused |

   `--parent-run-id` must name a manifest that **actually exists** in the receipt directory, or archive fails closed — lineage pointing at nothing is a claim, not evidence. Conversely it is only an evidence chain, not proof that every byte of state is correct (that belongs to #492, if operational evidence ever justifies it). Old manifests are never backfilled or retro-attributed; `report` labels them `legacy-unattributed`.

2. **Report which version was tested**: `main@<sha>` + data source + the simulated user state + the "answered → card" seconds (the receipt timestamp gap).
3. **What you found**: write each one down. If it is genuinely a bug or a gap, check `gh issue list` for duplicates and open an issue (do not fix it in the dogfood worktree). **If this session used real trade data, every issue or comment draft must pass guardrail 5's `privacy_lint.py` with exit 0 before posting.** Add a row for significant conclusions following `EVALS.md`'s "Regression record" convention (the receipt is the machine-readable ledger, `EVALS.md` the human-readable one).

   **But opening an issue is not the end — Step 6 must be finished before `owner_verdict` is recorded.** An issue records that something went wrong; it does not make that failure replayable, so next time nobody knows whether it was fixed or merely not stepped on again.
4. **Cleanup — only when the maintainer says the campaign is over.** Do not offer it as the natural next step after an archive, and never run it to "tidy up" between route runs: the isolated root *is* the campaign's state, and clearing it ends every lineage the following runs would have continued. Confirm Step 6 is done, then:
   - Staying in the campaign, or keeping the book for a later one → leave it alone. **This is the default.**
   - Explicitly asked for a clean slate → `~/.claude/skills/fomo-qa/qa_env.sh reset` (clears the isolated dogfood root).
   - Explicitly done with the worktree → `~/.claude/skills/fomo-qa/qa_env.sh down` (removes the worktree only, leaving state alone).

### Step 6 — Convert every miss into a replayable episode (gate 7, the last step before the verdict)

**This step was added to this skill only on 2026-07-27, and it patched a hole that had really happened**: the repo's `docs/qa-runbook.md` added step 6 on 2026-07-26, but this skill stopped at Step 5 and still wrapped up with "open an issue + add a row to EVALS.md" — exactly the old behavior #417 was meant to replace. The word "episode" appeared zero times anywhere in this skill directory. The rule was written in three repo documents and reached none of the buttons you actually press, so it had never once been executed. **Do not let it fall back to "do it if you remember" — `archive-receipt` now enforces it.**

Convert each miss into an episode **on the spot**, as you find it, rather than batching them at wrap-up: the agent's exact wording is the asset, and it is gone by the next session.

```bash
# From the repository root of the dogfood worktree
python3 evals/run_episodes.py --list        # see which fixtures the bank already leans on
# Follow evals/episodes/README.md's intake steps to write EP-NNN-*.json
# (write the recorded miss first, then the repaired answer)
python3 evals/run_episodes.py EP-NNN        # read which checks it actually trips; do not guess
python3 tests/run_all.py
```

Then record on the receipt where each miss went — the runbook's seventh gate, enforced by `verify --require-findings`. **That `findings_recorded` command lives in Step 5's wrap-up, immediately above `owner_verdict`**, because it must precede the verdict: the verdict is the session's last event, and a disposition recorded after it is a backfill that `verify` fails outright. This step's job is only to give you something real to record.

Three things not to get wrong:
- **`episode:EP-NNN` is reconciled against `evals/episodes/`** — claiming a conversion that never happened fails outright rather than relying on self-discipline.
- **"This session found nothing" must be said explicitly** (`--no-findings`); leaving the row out is not a substitute. Omission is not a declaration of none.
- **Not every miss can become an episode.** Use `not-episodable:#NN:<why>` for those. Whether the card ever reached the screen, for example, is a receipt-layer question (Step 4), not an answer-layer one — saying so is more honest than forcing a fake episode.

**Sessions using real data**: an episode keeps only the failure's *structure*. De-identify every real ticker, amount and date before writing the fixture — `privacy_trace` is the mechanical backstop (a real value that cannot be traced to the synthetic fixture goes red), but it is a necessary condition, not a sufficient one.

## Continuing in the same campaign

After a route run is archived, **stay here**. The maintainer is now a user with a book, and the next thing they say is the next route run's trigger. Do not re-run Steps 0–3, do not reset, and do not treat the archive as the end of testing (#544: walking four routes used to cost four full ceremonies in four sessions, which is why routes went unwalked).

Route the request to the **real command**. Answering it by hand — recomputing holdings, tallying a position, estimating what an addition would do — produces zero evidence and is the exact failure #543 recorded: an ad hoc portfolio question answered outside any lifecycle, 34 turns and a chart nobody asked for.

| The maintainer says | Route run to start | Lineage |
|---|---|---|
| "my holdings changed", "I bought/sold something", hands over a newer holdings view | `refresh` — record before reviewing (`flows/book-refresh.md`) | `continued` |
| "review it again", "run the weekly one" on a book with a finalized review behind it | `weekly_review` | `continued` |
| hands over a position table with no trade history, on a fresh root | `snapshot_review` | `fresh` |
| hands over a transaction CSV, on a fresh root | `first_review` | `fresh` |
| "what if I add to X", "should I buy Y" | `consider` — the pre-trade evaluation, walked below | `continued` |

Every continued run archives with `--state-mode continued --parent-run-id <the preceding accepted run_id>`, and each keeps its **own** receipt under its own route contract. The campaign identity (`--campaign`) stays the same across all of them; the `--case-id` is whichever acceptance case that particular run walks.

### The `consider` route: a receipted pre-trade evaluation

`review.py consider` (#544 Slice B, on #479's TradeEvaluation contract) renders no card and mutates no book. Run the real command against the campaign's own book, so the engine computes the consequence rather than the agent estimating it:

```bash
python3 engine/review.py consider --premise /tmp/fomo-kernel-premise.json \
  [--decision-context /tmp/fomo-kernel-context.json]
```

The response's `challenge` block is printed on that call's own stdout and **never stored** — capture it from there, together with the exact answer text you actually showed the user, into a transient check file. The engine states what the answer owes; the receipt proves it was delivered.

```bash
# qa-trace: consider
# 0) Session id = the engine's own evaluation_id (shape eval-<16 hex>);
#    consider creates no session, the way a refresh trace uses its refresh_id.
python3 tools/ux_receipt.py start --session-id <evaluation_id> --client claude --route consider \
  --adapter native_options --question-mode native_options

# 1) Only when a bounded context question was actually asked (for example,
#    the engine asking for a missing why_now). Omit this row entirely on a
#    run that asked nothing, and read the --controls note below before
#    recording the verdict.
python3 tools/ux_receipt.py event --session-id <evaluation_id> --event question_presented \
  --mode native_options

# 2) The moment the user's last context answer arrived. On a run that asked
#    nothing, omit this row too.
python3 tools/ux_receipt.py event --session-id <evaluation_id> --event answers_received

# 3) The challenge delivery: record immediately after the answer was shown
#    inline. The tool computes coverage/fidelity itself from your check file —
#    a failing result is stored as computed and voids the run at verify, so
#    read the check file before recording. The trace is append-only.
python3 tools/ux_receipt.py event --session-id <evaluation_id> --event evaluation_presented \
  --challenge-check-file <challenge-check.json>

# 4) The resolution invitation, exactly once, after the evaluation. open = the
#    invitation was shown and nothing settled; acted/declined/modified only
#    after the user's word was recorded via `consider --resolve` — never a
#    claim of broker execution.
python3 tools/ux_receipt.py event --session-id <evaluation_id> --event resolution_presented \
  --workflow-state open

# 5) Wrap up: findings first, verdict last, then verify.
python3 tools/ux_receipt.py event --session-id <evaluation_id> --event findings_recorded \
  --finding episode:EP-0NN

python3 tools/ux_receipt.py event --session-id <evaluation_id> --event owner_verdict \
  --controls pass --card not_applicable --memory not_applicable \
  --comprehension pass --usefulness pass --friction pass --resolution pass

python3 tools/ux_receipt.py verify --session-id <evaluation_id> \
  --require-owner-verdict --require-timing-integrity --require-findings
```

`--challenge-check-file` points at a transient JSON that never enters the trace, the same nature as `--grounding-check-file`. It pairs the `challenge` block **from the `consider` call's own stdout** with the exact answer text shown:

```json
{
  "challenge": {
    "must_state": [
      {"topic": "basis", "value": "ledger", "anchor": "basis.source"},
      {"topic": "position", "value": 0.2731, "anchor": "consequence.after.weights.SYNTH"},
      {"topic": "cash", "value": 8400.0, "anchor": "consequence.after.cash.balance"},
      {"topic": "rule_collision", "value": "would_breach", "anchor": "rule_collisions.rule-1.state",
       "detail": {"rule_id": "rule-1", "text": "One name never above a quarter of the book", "worsens": null}},
      {"topic": "disclosure", "value": "cost_basis", "anchor": "consequence.disclosures.0"}
    ],
    "quote_verbatim": [{"field": "reason", "text": "Best setup I have seen this year."}],
    "unchecked": ["liquidity", "valuation", "tax", "position_fit", "evidence_delta"],
    "case_required": {"for": 1, "against": 1},
    "required_coverage": [{"path": "consequence.disclosures.0", "owes": "disclosure", "key": "cost_basis"}]
  },
  "presented_text": "On your recorded book this takes SYNTH to 27.3% and leaves 8,400 in cash. It would break your own rule: \"One name never above a quarter of the book\". Weights are on cost, not live prices. You said: \"Best setup I have seen this year.\" Liquidity, valuation, tax, whether the position still fits you, and whether this is genuinely new information were not checked."
}
```

Paste the challenge verbatim from stdout — a truncated paste is refused rather than read as a smaller obligation — and the recorded `challenge_hash` stays auditable afterward: the block is a pure function of the persisted evaluation row, so anyone holding the root can recompute it.

What the machine half checks, and what stays with the owner's `comprehension` verdict: load-bearing numbers as digits at any display precision, and rule-collision texts / user quotes / excluded-holding tickers verbatim, are machine-decidable; engine-vocabulary strings, boolean triggers, and `unchecked` keys reach the user only as prose in the conversation's language, which no offline comparison can judge — that half is what `comprehension` is for. See `references/ux-receipt.md` ("The second card-free route") for the complete split.

Archive like any other continued route run in this campaign: `--state-mode continued --parent-run-id <the preceding accepted run_id>`, same `--campaign`. A `consider` receipt is now formal evidence, and the old "exploratory only" caveat is gone — but a run whose trace fails `verify` is still void and has to be walked again with a fresh receipt; the trace is append-only, so there is no repair.

The resolution boundary: `acted` records the user's own word via `consider --resolve`, never a fill. The invitation only ever asks whether the user says they acted, declined, or modified — do not read a recorded `acted` as proof a broker order executed.

### Ending the campaign

The campaign ends only when the maintainer explicitly says so ("that's enough", "we're done", "reset it"). Then, and only then, run Step 5's cleanup. An interrupted campaign resumes by reusing the same isolated root and starting a **new** receipt for the next route run — receipts are append-only and one is never reopened, rewritten, or repurposed for a route it did not trace.

## The dogfood ledger: separating human involvement, aggregating across versions

Archive's `human` argument decides whether a run counts as real experience evidence — this is what the whole ledger's credibility rests on:

| Level | Meaning | Counts as experience ground truth |
|---|---|---|
| `owner_live` | You personally throughout (answering and judging) | ✅ **Real UX ground truth** |
| `agent_with_owner_verdict` | The AI walked the flow; you gave only the final verdict | Partly human |
| `agent_simulated` (**default**) | Fully AI-simulated, no human | ❌ Contract only, **not an experience signal** |

Unmarked means `agent_simulated` — better to understate credibility than to quietly pass off "the AI attested to itself" as "the user said it was good" (#230's core lesson).

To see pass-rate trends across versions and levels of human involvement:

```bash
~/.claude/skills/fomo-qa/qa_env.sh report
```

The report keeps `owner_live` and `agent_simulated` **strictly separate**, and buckets further by client, agent model and effort. Old manifests are labeled `legacy-unattributed` and do not mix into new model/effort comparisons. Its closing note states that only the former is ground truth — no number of green `agent_simulated` runs licenses a claim that the experience is good.

## One-off quick self-check

To confirm the environment is healthy without walking the full procedure:

```bash
~/.claude/skills/fomo-qa/qa_env.sh status
```
