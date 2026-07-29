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
| `tests/test_receipts.py` | Unit tests for `receipts.py`, including the campaign/case/state-lineage rules an archived run must satisfy. |
| `tests/test_skill_commands.py` | Drift gate: every `ux_receipt.py` command in `SKILL.md` must parse against the real CLI, and the documented event order must replay into a trace that verifies (#520). |
| `tests/test_isolation_gate.py` | Refusal gate: `qa_env.sh` must stop every command while the account's own `~/.trade-coach` is still reachable, and what `isolate` prints must satisfy the check that refused (#557). |

All three test files run inside `tests/run_all.py`, the repository's actual commit gate.
Maintainer tooling is not product code, but a gate that lives outside the runner is
enforced by whoever remembers — which is how `SKILL.md` accumulated commands that
could not be executed as written.

## Relationship to `docs/qa-runbook.md`

`docs/qa-runbook.md` is the authority and must stay independent of this directory.
It defines **what counts as a compliant QA run** — the gates — for anyone who cloned the
public repository, who cannot be assumed to have this skill installed.

`SKILL.md` covers something different: **how to actually execute a run** on this machine —
the commands, the worktree paths, which shell must keep which exported variable, and how
each agent client degrades when it lacks a capability.

When the two disagree, the runbook wins.

> The two documents are maintained by hand and have drifted before — most expensively
> in their command examples, which #520 repaired and now gates mechanically via
> `tests/test_skill_commands.py`. #527 then gated the structural half of the prose,
> across all three enumerations of the gates: the runbook's numbered list and its
> enforcement table must agree on numbering and order, and this skill's stated count
> — in every paragraph that states one — and the number of names in its inline roster
> must both equal that list's length. So a gate cannot be added to or removed from the
> runbook and left stale here.
>
> What is deliberately *not* compared is wording: what each gate means, the names
> themselves, the order this skill happens to list them in, and the per-client
> degradation notes. Each document phrases the same gate for its own reader, and
> pinning that would pin prose. The cost of that choice is real and worth naming —
> renumbering the runbook's gates leaves this skill's roster in the old order with
> nothing to catch it.

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
