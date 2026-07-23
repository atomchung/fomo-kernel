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
   `data_source`, and `human_involvement`. Honest default for human
   involvement is `agent_simulated`; only `owner_live` counts as user-experience
   ground truth.
6. **Privacy gate** — if the session touched real trade data, every piece of
   text destined for a public surface passed `tools/privacy_lint.py` first
   (see below).

Each rule doubles as early detection, and each is honest about where it is
machine-enforced versus procedural:

| Gate | Machine-enforced by | Procedural part |
|---|---|---|
| 1. Version | — | record the sha yourself before starting (the owner's `/fomo-qa` skill automates this) |
| 2. Isolation | engine CLIs + `ux_receipt.py` honor `TRADE_COACH_HOME` | exporting it, and not overriding it per-command |
| 3. Receipt | `verify` fails on a missing/duplicated/out-of-order **card presentation sequence**, an undeclared mode, a silent widget degrade, a missing weekly opener, or a missing/duplicated/misordered `cash_anchor_checked` on a first review or full-tier weekly review (#357); it machine-reports timing plausibility separately | recording every event honestly, right after the user sees it |
| 4. Verdict | `verify --require-owner-verdict --require-timing-integrity` fails without a passing verdict or credible timestamp sequence | running both flags on human-graded runs; auditing or re-running suspect timing |
| 5. Manifest | the owner's `/fomo-qa` archive step refuses a non-verifying receipt | on other clients, writing the manifest fields by hand |
| 6. Privacy | `privacy_lint.py` exits non-zero on reference matches | running it on every public-bound draft, and de-identifying what it cannot see (below) |
| 3b. Grounding fidelity | `verify` fails when a `rule_choice_presented` event is missing its grounding-fidelity evidence, or reports a non-verbatim match, with no legacy exemption (#293) | authoring `--grounding-check-file` honestly (candidates + exact presented text) before recording the event |

A drifted run therefore surfaces *before* its results are trusted or posted,
not in a post-hoc audit — but only the checks in the middle column are
self-executing; the right column is on the runner.

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
git fetch origin main
git log -1 --format='%h %s' origin/main   # record this sha — it is what you are testing
# test on a checkout of exactly origin/main (a detached worktree is ideal);
# if your checkout is behind, update it before continuing.
```

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
python3 tools/ux_receipt.py event --event question_presented ...   # after each question is shown
python3 tools/ux_receipt.py event --event answers_received          # right after the final required answer
python3 tools/ux_receipt.py event --event card_presented ...        # after the user actually sees a card
python3 tools/ux_receipt.py event --event rule_choice_presented ... # when the rule choice is shown
```

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

### 4. Wrap up

```bash
python3 tools/ux_receipt.py event --event owner_verdict --controls ... --card ... --memory ... \
  [--question-specificity ... --answer-fit ...]
python3 tools/ux_receipt.py verify --session-id <ID> \
  --require-owner-verdict --require-timing-integrity
```

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
involvement). Claude Code sessions on the owner's machine use the local
`/fomo-qa` skill's `qa_env.sh archive-receipt` for this; other clients record
the same manifest fields alongside the receipt file. Report the tested
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

## Per-client notes

- **Claude Code (owner's machine)** — the local `/fomo-qa` skill automates the
  version gate, dogfood worktree, isolated root, and receipt archiving. The
  skill is the convenience wrapper; *this runbook is the contract it wraps.*
- **Codex, Antigravity, Cursor, others** — no wrapper exists: follow the flow
  above manually. Set `--client` truthfully in the receipt so cross-client
  runs stay attributable (#273). Begin unknown hosts in the complete text
  route; absence of rich cards or native options is not a reason to skip the
  receipt. Promote an adapter only after a separate real-host dogfood pass.
- **Any client**: if you realize mid-run that a gate was violated (wrong root,
  stale checkout, missing receipt), stop, note it, and restart the run — do
  not retrofit compliance onto a drifted session.
