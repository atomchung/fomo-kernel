# QA runbook — formal dogfood flow for every client

This is the maintainer-facing QA contract for dogfooding fomo-kernel from any
coding agent or IDE: Claude Code, Codex, Antigravity, Cursor, or anything else
that can run a shell. It exists because informal QA runs kept drifting — tested
against stale checkouts, wrote into the real coach root, left no attributable
trace (#273), had no formal entry point on some clients (#271), and in the
worst case leaked real trade data into public issue text (#274).

**Scope boundary**: this runbook is for maintainers verifying the product. A
real user reviewing their own trades follows the product skill
([skills/fomo-kernel/SKILL.md](../skills/fomo-kernel/SKILL.md)) and never needs
this document.

## Choose the evidence lane deliberately

Maintainers may choose the inexpensive automated lane or the owner-live lane,
but the results are intentionally not interchangeable.

| Lane | Command | Result | What it proves |
|---|---|---|---|
| Contract preflight | `python3 skills/fomo-kernel/tools/qa_preflight.py run` | `engine_contract_pass` | The deterministic regression suite and artifact contracts passed. It creates no attributable dogfood session or receipt, and makes no UX claim. |
| Formal dogfood | This runbook's full flow | archived owner/agent receipt | The named host walked the product lifecycle with attributable presentation evidence. Only an `owner_live` run can support a user-experience claim. |

`qa_preflight.py` is deliberately content-free: it accepts no trade CSV, never
records a maintainer `card_presented`, and always reports `formal_qa:false`,
`human_involvement:agent_simulated`, and `ux_evidence:not_assessed`. A green
preflight is a useful cheap gate before dogfood; it is not a QA pass and must
not be archived as one. Its deterministic suite may create isolated temporary
fixtures, but those are test internals rather than attributable QA evidence.
The runner strips `TRADE_COACH_HOME` and inherited `TR_*` input, state, and
network overrides (including `TR_TEST_NETWORK`), then runs under a temporary
`HOME`. Test fixtures therefore cannot write into the caller's real or dogfood
root or become a live-network run. It preserves only the local Python
dependency import path and user-site base needed by this managed runtime.

## What counts as a QA run (fail closed)

A dogfood session is a QA run **only if all of the following hold**. A session
that skips any of them is an informal exploration: its findings may still be
reported, but it must not be counted, archived, or cited as "QA passed", and
issues it produces must say so.

1. **Version gate** — it ran against the latest `origin/main`, and the tested
   sha was recorded before starting. Testing a stale worktree measures a past
   slice of the product and the result cannot be attributed afterwards.
2. **Isolated state root** — `TRADE_COACH_HOME` pointed at a dedicated
   dogfood root for the entire session. The real `~/.trade-coach` and the
   maintainer's private records directory are never read or written. All
   lifecycle tools honor `TRADE_COACH_HOME` (`review.py`, `coach.py`,
   `tools/ux_receipt.py` — the last one since #269).
3. **Presentation receipt** — every user-visible step was recorded through
   `tools/ux_receipt.py` (`start` with an honest `--client`, resolved adapter,
   and capability declaration, then events after each user-visible action). A run without a
   receipt is structurally unattributable — exactly the untracked-run gap
   described in #273. This applies to `--test-drive` runs too. When a rule
   choice is presented, the receipt also proves each candidate's engine
   `grounding` was shown verbatim (or that none was expected) — see
   `rule_choice_presented` in
   [interaction-delivery.md](../skills/fomo-kernel/references/interaction-delivery.md)
   (#293).
4. **Verdict and verification** — the session ended with an `owner_verdict`
   event and `tools/ux_receipt.py verify` passing. Human-graded runs use both
   `--require-owner-verdict` and `--require-timing-integrity`; only
   `timing_integrity.status=credible` is eligible for fresh `owner_live` UX
   ground truth.
5. **Archived manifest** — the receipt was archived together with a manifest
   recording `engine_version` (the tested `main@<sha>`), `client`,
   `data_source`, `human_involvement`, a digest of the archived receipt, and
   the run's campaign attribution: which named acceptance `case_id` it tested,
   and whether it started from fresh state or continued a named earlier run
   (#520). Honest default for human involvement is `agent_simulated`; only
   `owner_live` counts as user-experience ground truth. A verifying receipt
   proves one presentation trace; without the case and state lineage, a set of
   individually valid receipts still cannot prove a required multi-step
   trajectory started where it was supposed to, or that a campaign's cases were
   covered rather than its easiest one repeated. Manifests archived before this
   binding existed stay readable as unattributed evidence and are never
   upgraded after the fact.
6. **Privacy gate** — if the session touched real trade data, every piece of
   text destined for a public surface passed `tools/privacy_lint.py` first
   (see below).
7. **Findings disposition** — the run recorded a `findings_recorded` event
   saying where every miss went: converted into a replayable episode, or tied
   to the issue that owns it with the reason it cannot be replayed. A run that
   observed nothing declares that explicitly. This is gate 7 because the first
   six can all pass on a run that found real problems and left no replayable
   asset behind — which is exactly what #417 measured (eighteen receipts, one
   archived manifest, zero episodes) and what step 6 below is the how of.

Each rule doubles as early detection, and each is honest about where it is
machine-enforced versus procedural:

| Gate | Machine-enforced by | Procedural part |
|---|---|---|
| 1. Version | — | record the sha yourself before starting (the owner's `/fomo-qa` skill automates this) |
| 2. Isolation | engine CLIs + `ux_receipt.py` honor `TRADE_COACH_HOME` | exporting it, and not overriding it per-command |
| 3. Receipt | `verify` fails on a missing/duplicated/out-of-order **card presentation sequence**, an undeclared mode, a silent widget degrade, a missing weekly opener, or a missing/duplicated/misordered `cash_anchor_checked` on a `first_review`/`weekly_review` trace (#357 — the check is tier-blind by design: a light-tier session writes no receipt at all, per the scope rule in `references/interaction-delivery.md`); it machine-reports timing plausibility separately. What a trace owes is read from its declared route, so the card-free `refresh` route (#523) is held to a **change surface** instead — and, symmetrically, `verify` refuses a refresh trace recording any card event, and refuses `change_presented` on a card-producing route | recording every event honestly, right after the user sees it |
| 4. Verdict | `verify --require-owner-verdict --require-timing-integrity` fails without a passing verdict or credible timestamp sequence. Which axes must be affirmative is the route's own contract: `card=pass` on a card route, `memory=pass` additionally on `weekly_review`, and on the card-free `refresh` route `change=pass` with `card=not_applicable` — a verdict that judges the change surface the user actually saw rather than a card nobody rendered | running both flags on human-graded runs; auditing or re-running suspect timing |
| 5. Manifest | the owner's `/fomo-qa` archive step refuses a non-verifying receipt, and refuses a run that names no campaign/`case_id`/state mode, or claims to continue a `parent_run_id` with no archived manifest behind it (#520) | on other clients, writing the manifest fields by hand; on every client, that the `case_id` is the one the run actually walked |
| 6. Privacy | `privacy_lint.py` exits non-zero on reference matches | running it on every public-bound draft, and de-identifying what it cannot see (below) |
| 7. Findings | `verify --require-findings` fails when the trace has no `findings_recorded` (or more than one), when that row sits *after* the owner verdict, when it omits `findings` or gives it a non-list, when it carries an unrecognized disposition or any field beyond the dispositions, or — **wherever `evals/episodes/` is reachable, which excludes a vendored skill directory** — when an `episode:EP-NNN` id is absent from it, a conversion claim with nothing behind it. Resolved on the write path and again on `verify`, so an edited receipt cannot carry one | judging honestly what counts as a miss; converting it while the wording is still in front of you; and, on a checkout with no bank beside it, that the id is real |
| 3b. Grounding fidelity | `verify` fails when a `rule_choice_presented` event is missing its grounding-fidelity evidence, or reports a non-verbatim match, with no legacy exemption (#293) | authoring `--grounding-check-file` honestly (candidates + exact presented text) before recording the event |

A drifted run therefore surfaces *before* its results are trusted or posted,
not in a post-hoc audit — but only the checks in the middle column are
self-executing; the right column is on the runner.

The manifest's `human_involvement` is an evidence label, not an option to
upgrade after the fact. Set `owner_live` only when the owner answered every
required question, made the rule choice (or skip), saw both cards in the named
host, and supplied the verdict. Automated answers are `agent_simulated`; an
agent-run flow with only a final owner verdict is `agent_with_owner_verdict`.

## Hard guardrails

- **Real trade CSVs are read-only, always.** Nothing in a QA run ever writes
  to the maintainer's private records.
- **Never point any tool at the real `~/.trade-coach`.** One
  `export TRADE_COACH_HOME=<dogfood-root>` at the start of the session routes
  `prepare`/`preview`/`finalize`/`resume`, `coach.py`, and `ux_receipt.py`
  consistently. Do not pass per-command `--root`/`--state-root` overrides that
  disagree with it.
- **QA worktrees are read-only for product code.** If you find a bug, record
  it and open an issue; do not patch the checkout you are testing.
- **Public text passes the privacy lint.** This repository is public. Real
  tickers, amounts, dates, and `TICKER#date#seq` position ids must never
  appear in issues, PRs, comments, or commit messages (#274). Local notes may
  keep real values; public text may not.

## Standard flow

The product lifecycle itself (prepare → agent work → preview → rule choice →
finalize) is defined by [skills/fomo-kernel/SKILL.md](../skills/fomo-kernel/SKILL.md)
and its routed flows — this runbook does not restate it, it wraps it.

### 0. Version gate

```bash
python3 skills/fomo-kernel/tools/qa_preflight.py refresh
# test on a checkout of exactly origin/main (a detached worktree is ideal);
# if your checkout is behind, update it before continuing.
```

`qa_preflight.py status` is network-free and labels remote freshness
`unverified`; it cannot make a run QA-eligible. `refresh` performs the fetch.
After it succeeds, record the reported `origin_main` SHA and confirm that the
dogfood worktree's `HEAD` equals that SHA before starting. A feature-branch
preflight remains useful developer evidence, but it is never a formal dogfood
run against latest main. `origin_main` itself can come back `null` in a
checkout whose fetch never populates a `origin/main` remote-tracking ref
(some CI checkouts do this) -- if `refresh` reports `null`, fall back to
`git fetch origin main && git log -1 --format='%h %s' FETCH_HEAD` to get the
SHA by hand.

### 1. Isolate

```bash
export TRADE_COACH_HOME="$HOME/.trade-coach-dogfood"   # dedicated dogfood root
```

Keep this export alive for every later command in the session (re-export in
each new shell). To simulate a brand-new user, clear the dogfood root through
`coach.py data-reset --root "$TRADE_COACH_HOME" --confirm` (backup first via
`data-export` if the prior state matters); to simulate a returning user, keep
the previous state. Never run reset-style commands against the real root.

### 2. Choose a data source

| Source | Notes |
|---|---|
| Real trades CSV | Read-only. Strongest signal, strictest privacy handling (gate 6). |
| Mock persona (`mock/*.csv`, see `mock/SAMPLES.md`) | Reproducible, zero privacy risk. |
| `--test-drive` | No CSV, isolated temp root, `persist:false`. Still gets a receipt. |

### 3. Walk the product flow, receipt everything

Work inside `skills/fomo-kernel/` of the checkout. Follow the product
`SKILL.md`; declare host capabilities honestly and record each user-visible
step (exact arguments: `tools/ux_receipt.py --help`):

```bash
python3 tools/ux_receipt.py start --session-id <ID> --client <your-client> --route <route> \
  --adapter plain_text
python3 tools/ux_receipt.py event --session-id <ID> --event cash_anchor_checked ... # before the first surface
python3 tools/ux_receipt.py event --session-id <ID> --event question_presented ...  # after each question is shown
python3 tools/ux_receipt.py event --session-id <ID> --event answers_received        # right after the final required answer
python3 tools/ux_receipt.py event --session-id <ID> --event artifact_generated ...  # when the card file is written
python3 tools/ux_receipt.py event --session-id <ID> --event card_presented ...      # after the user actually sees it
python3 tools/ux_receipt.py event --session-id <ID> --event rule_choice_presented ... # when the rule choice is shown
```

Every subcommand needs its own `--session-id`; the trace is a file keyed by it,
not an implicit session. `artifact_generated` and `card_presented` both take
`--stage preview|final`, and each stage needs exactly one of each, artifact
first — a card marked presented before its artifact existed fails verification
with no way to repair the row.

Non-negotiables while walking (each has burned a real QA run before):

- Resolve the adapter honestly. An unknown client, missing plugin, or unproven
  bridge starts as `--adapter plain_text`; it is a supported, first-class QA
  route. Declare `validated_widget` only after a real-host probe has shown
  direct structured choice submission and rich-card rendering. If that widget
  then fails in-session, record `widget_attempt_failed` before falling back.
  A generated artifact is not a presented card (#230).
- Append each event when the action happens. Never replace a partial receipt or
  backfill the walk at archive time; after an interruption, append to the same
  trace. A reconstructed receipt is not execution-layer evidence.
- `--language` follows the conversation language.
- The `answers_received → card_presented(preview)` timestamp gap is the
  user-visible machine wait — report it in the wrap-up (#236).
- For a weekly run, reuse the exact isolated root of a finalized first review.
  Do not force a `weekly_review` route: `prepare` must select it. Before the
  first question or card, present and record `prior_commitment` or `prior_skip`,
  plus any `exit_reason` or `due_revisit` returned by the plan.
- `--test-drive` always selects `test_drive` and uses the emitted `state_root`
  for every later engine and receipt command. It is a demo route, not evidence
  for first-review or weekly-memory behavior.

### 4. Wrap up

```bash
# Gate 7 first. The verdict is the run's last act, so a disposition recorded
# after it reads as a backfill and verification rejects it. Step 6 below is how
# you earn this row; record it here, immediately before the verdict.
python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded ...
python3 tools/ux_receipt.py event --session-id <ID> --event owner_verdict --controls ... --card ... --memory ... \
  [--question-specificity ... --answer-fit ...]
python3 tools/ux_receipt.py verify --session-id <ID> \
  --require-owner-verdict --require-timing-integrity --require-findings
```

`--require-findings` is gate 7, and it is why step 6 below happens *before* the
owner verdict rather than whenever someone remembers. A `weekly_review` run
additionally needs `--memory pass` or `--memory fail`: `not_applicable` is
refused under `--require-owner-verdict`, because memory continuity is the whole
reason that route exists.

The JSON result includes `timing_integrity`. Timestamp reversal or a complete
owner-verdict trace spanning less than three seconds is `suspect`; ordinary
verification warns so legacy consumers remain compatible, while the strict QA
command above exits non-zero. A suspect receipt cannot be archived or cited as
`owner_live` ground truth without an audit using contemporaneous evidence or a
fresh walkthrough. If retaining the run for non-UX diagnostics, label its
`human_involvement` as `agent_simulated`. The recorded owner verdict itself may
still be reported as the owner's judgment; timing integrity limits the trace's
evidentiary use, not that judgment. Legacy receipts without `ts` remain valid
under ordinary verification and report `not_assessed`; they are not evidence
for a new `owner_live` claim.

Archive the receipt with its manifest (tested sha, client, data source, human
involvement, campaign and `case_id`, fresh-or-continued state lineage, and the
receipt digest — gate 5). Claude Code sessions on the owner's machine use the
local `/fomo-qa` skill's `qa_env.sh archive-receipt` for this; other clients
record the same manifest fields alongside the receipt file. Report the tested
`main@<sha>`, data source, simulated user state, and the answers→card wait.

### 5. Report findings — through the privacy gate

Search for duplicates first (`gh issue list`), then, **if the session touched
real trade data**, run every draft destined for a public surface through the
lint before posting:

```bash
python3 tools/privacy_lint.py --against <real-trades.csv> draft.md
# or: <paste> | python3 tools/privacy_lint.py --against <real-trades.csv> -
```

Exit 0 is the only pass. Findings are printed masked; fix the draft (replace
tickers/amounts/dates/position ids with de-identified descriptions) and re-run
until clean. The tool fails closed: an unreadable or empty reference CSV is an
error, never a silent pass.

What the lint machine-checks: reference tickers (including bare stems of
suffixed symbols like `2330.TW` / `BRK-B`), reference trade dates (ISO and
slash forms), amounts with a 4+ digit integer part (cell values and per-row
quantity x price products), and the `TICKER#date#seq` position-id format.
What it cannot see — smaller numbers (a bare share price), prose that
describes a position without naming it, percentages — you de-identify by
hand; a passing lint is necessary, not sufficient. Mock-data sessions do not
need the lint, but the `TICKER#date#seq` format should still never be pasted
verbatim.

### 6. Convert each miss into an episode

An issue records that something went wrong; it does not make the failure
replayable, so nothing later checks whether it stayed fixed. #417 measured the
result: eighteen receipts, one archived manifest, and zero replayable assets
from a loop `docs/eval-design.md` had specified all along. File the issue **and**
convert, in the same sitting — the exact wording the agent produced is the
asset, and it is gone by the next session.

For a miss in an answer or a presented surface:

```bash
# read what the checks actually say about the recorded answer, then pin it
python3 evals/run_episodes.py EP-NNN
python3 evals/run_episodes.py            # coverage is judged only on a full run
```

[evals/episodes/README.md](../evals/episodes/README.md) holds the field
contract and the intake steps. Two rules matter more than the rest: the episode
carries **both** the recorded miss and the repaired answer, naming exactly which
checks the miss must trip; and a real-data miss keeps only its failure structure
— run the draft through the privacy gate in step 5, and let `privacy_trace` be
the backstop, since a symbol or amount that survived from a real account cannot
trace to a synthetic fixture.

A miss the mechanical checks cannot express is still worth an episode when a
later judge could grade it, and worth saying out loud when it cannot: whether
the card ever reached the screen is a receipt question (step 3), not an answer
question.

Then record where each one went, which is gate 7 and the last thing before the
owner verdict in step 4:

```bash
python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded \
  --finding episode:EP-0NN \
  --finding 'not-episodable:#NN:why this one cannot be replayed'
# or, for a run that genuinely found nothing:
python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded --no-findings
```

The event resolves each `episode:EP-NNN` against `evals/episodes/` in this
checkout, so "converted" cannot be claimed for a conversion that did not happen.
`--no-findings` is a real and common outcome; what fails closed is leaving it to
be inferred from an absent event.

## Per-client notes

- **Claude Code (owner's machine)** — the local `/fomo-qa` skill automates the
  version gate, dogfood worktree, isolated root, and receipt archiving. The
  skill is the convenience wrapper; *this runbook is the contract it wraps.*
- **Codex, Antigravity, Cursor, others** — no dogfood-lifecycle wrapper exists:
  follow the flow above manually. An optional local preflight tool (see
  "Choose the evidence lane deliberately" above) may offer the same
  version-gate and setup conveniences, but it cannot turn an automated result
  into host UX evidence — set `--client` truthfully in the receipt so
  cross-client runs stay attributable (#273), and record `card_presented`
  only after the owner actually sees the card. Begin unknown hosts in the
  complete text route; absence of rich cards or native options is not a
  reason to skip the receipt. Promote an adapter only after a separate
  real-host dogfood pass.
- **Any client**: if you realize mid-run that a gate was violated (wrong root,
  stale checkout, missing receipt), stop, note it, and restart the run — do
  not retrofit compliance onto a drifted session.
