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
   `tools/ux_receipt.py` (`start` with an honest `--client` and capability
   declaration, then events after each user-visible action). A run without a
   receipt is structurally unattributable — exactly the untracked-run gap
   described in #273. This applies to `--test-drive` runs too.
4. **Verdict and verification** — the session ended with an `owner_verdict`
   event and `tools/ux_receipt.py verify` passing (use
   `--require-owner-verdict` for human-graded runs).
5. **Archived manifest** — the receipt was archived together with a manifest
   recording `engine_version` (the tested `main@<sha>`), `client`,
   `data_source`, and `human_involvement`. Honest default for human
   involvement is `agent_simulated`; only `owner_live` counts as user-experience
   ground truth.
6. **Privacy gate** — if the session touched real trade data, every piece of
   text destined for a public surface passed `tools/privacy_lint.py` first
   (see below).

Each rule doubles as early detection: `verify` fails on missing or out-of-order
events, archiving fails without a receipt, the version gate fails on a stale
checkout, and the privacy lint fails on real identifiers — so a run that
drifted off the standard flow surfaces the drift *before* its results are
trusted or posted, not in a post-hoc audit.

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
  --question-mode ... --card-mode ...
python3 tools/ux_receipt.py event --event question_presented ...   # after each question is shown
python3 tools/ux_receipt.py event --event answers_received          # right after the final required answer
python3 tools/ux_receipt.py event --event card_presented ...        # after the user actually sees a card
python3 tools/ux_receipt.py event --event rule_choice_presented ... # when the rule choice is shown
```

Non-negotiables while walking (each has burned a real QA run before):

- Declare capabilities honestly; if the client can render rich cards, attempt
  the rich path once before degrading and record `widget_attempt_failed` if it
  fails. A generated artifact is not a presented card (#230).
- `--language` follows the conversation language.
- The `answers_received → card_presented(preview)` timestamp gap is the
  user-visible machine wait — report it in the wrap-up (#236).

### 4. Wrap up

```bash
python3 tools/ux_receipt.py event --event owner_verdict --controls ... --card ... --memory ... \
  [--question-specificity ... --answer-fit ...]
python3 tools/ux_receipt.py verify --session-id <ID> [--require-owner-verdict]
```

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
tickers/amounts/position ids with de-identified descriptions) and re-run until
clean. The tool fails closed: an unreadable or empty reference CSV is an
error, never a silent pass. Mock-data sessions do not need the lint, but the
`TICKER#date#seq` format should still never be pasted verbatim.

## Per-client notes

- **Claude Code (owner's machine)** — the local `/fomo-qa` skill automates the
  version gate, dogfood worktree, isolated root, and receipt archiving. The
  skill is the convenience wrapper; *this runbook is the contract it wraps.*
- **Codex, Antigravity, Cursor, others** — no wrapper exists: follow the flow
  above manually. Set `--client` truthfully in the receipt so cross-client
  runs stay attributable (#273). If the client cannot render rich cards or
  native option controls, that is a finding to record (with an honest
  capability declaration and verdict), not a reason to skip the receipt.
- **Any client**: if you realize mid-run that a gate was violated (wrong root,
  stale checkout, missing receipt), stop, note it, and restart the run — do
  not retrofit compliance onto a drifted session.
