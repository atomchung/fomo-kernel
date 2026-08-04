# CLAUDE.md — Claude Code adapter

@AGENTS.md

The line above imports the shared always-on instruction floor. `AGENTS.md` is
the same floor Codex and other clients read natively, and it carries the route
selection, the non-negotiable boundaries, and the instruction-authority policy
this file is subordinate to. The detailed host-neutral contract for changing
this repository — development discipline, tests, the privacy boundary, commit
and PR conventions, and the mirrored-surfaces map — is
[docs/maintainer-guide.md](docs/maintainer-guide.md). Read it before editing
repository code; it is not auto-loaded, deliberately.

## Edit boundary for this file

A change here must name the Claude-only mechanism it supports. Shared product,
architecture, test, privacy, issue, PR, or runtime guidance belongs in the
shared authority.

This file is a host adapter, so the authority policy in `AGENTS.md` applies to
it: it may change tool mechanics only. Keep it short —
`tests/test_doc_language.py` fails the suite if it outgrows its line budget or
if a shared section heading comes back into it.

## Claude Code hooks

Committed hooks in `.claude/` enforce the test gate. Hook `if:` filters have
been observed to be unreliable in the supported Claude Code setup. Every hook
script must inspect `tool_input.command` from stdin and exit immediately for
unrelated commands. Follow the self-filtering pattern in
`pre_commit_test_gate.sh`.

That gate is the mechanical half of a shared rule and it exists only here.
Since #492 it covers repository integrity (`tests/test_repo_hygiene.py`), not
the whole registry: behaviour is gated by the blocking `product-contract` CI
job before merge. The rule itself is stated in `AGENTS.md` for every client
precisely because no other client enforces it.
