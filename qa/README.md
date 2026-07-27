# qa/ — maintainer dogfood tooling

Everything here exists to **test** fomo-kernel. None of it is part of the product.

A user installs `skills/fomo-kernel/` and never sees this directory. That separation is
the reason `qa/` sits at the repository root rather than under `skills/`: a second entry
under `skills/` would imply there are two things to install, and there is exactly one.

## What is here

| File | Role |
|---|---|
| `SKILL.md` | The maintainer-facing QA skill. Symlinked into the agent's skill directory (see below). |
| `qa_env.sh` | Dogfood environment manager — pinned worktree, isolated coach root, reset. |
| `receipts.py` | Reads and summarizes `ux_receipt` output across dogfood runs. |
| `slice_csv.py` | Cuts a fixture CSV down to a date window, for staging a second review period. |
| `tests/test_receipts.py` | Unit tests for `receipts.py`. |

## Relationship to `docs/qa-runbook.md`

`docs/qa-runbook.md` is the authority and must stay independent of this directory.
It defines **what counts as a compliant QA run** — the gates — for anyone who cloned the
public repository, who cannot be assumed to have this skill installed.

`SKILL.md` covers something different: **how to actually execute a run** on this machine —
the commands, the worktree paths, which shell must keep which exported variable, and how
each agent client degrades when it lacks a capability.

When the two disagree, the runbook wins.

> The two documents are currently maintained by hand and have measurably drifted
> (`SKILL.md` calls the gates "seven" in one paragraph and "six" in the next, because the
> seventh landed on 2026-07-27 and only one sentence was updated). Collapsing the
> duplicated half into a reference is tracked separately; it changes QA behavior and so
> does not belong in the commit that merely relocated these files.

## Install

```bash
ln -s "$(pwd)/qa" ~/.claude/skills/fomo-qa
```

## Current status

These files were **copied** from `ai_harness/skills/fomo-qa/`, not moved. The originals
remain in place and the live symlink still points at them, so nothing about a running QA
session changed when this directory appeared. Switching the symlink and retiring the
originals is a separate, reversible step.

`ai_harness/inventory/fomo-qa.json` stays where it is — cross-project skill governance
belongs to the harness, not to this repository.
