#!/usr/bin/env bash
# fomo-kernel commit gate (project-scoped, committed)
#
# Registered as PreToolUse:Bash. Self-filters to `git commit` commands — the
# settings `if: Bash(git commit*)` filter is NOT relied upon (some Claude Code
# versions ignore it, in which case the matcher fires on every Bash call). This
# self-filter is what guarantees the gate only ever runs at commit time, never
# on an unrelated `ls`/`git status`.
#
# WHAT THIS GATES, AND WHAT IT DELIBERATELY STOPPED GATING (#492)
#
# Until #492 this ran the entire suite registry whenever
# `skills/fomo-kernel/engine/` or `tests/` had changed. Two things were wrong
# with that. It made every small engine checkpoint pay the whole QA/eval cost,
# so a receipt, campaign, synthetic-operator, judge or episode-bank failure
# could pressure an implementer into changing product code merely to get a
# reversible local commit through. And it did not actually gate: the registry
# measures ~190s, this hook's `.claude/settings.json` timeout was 60s, so on any
# real engine commit the gate had already been failing open — a mechanism the
# repository believed it had and did not.
#
# A local commit is a reversible development receipt, not a release claim.
# Behaviour is gated by the blocking `product-contract` CI job before merge; see
# docs/maintainer-guide.md for the full ladder.
#
# What stays here is the one class CI catches too late to be worth anything:
# repository integrity that changes no behaviour and that no behavioural suite
# can see — a conflict marker committed inside a docstring (#575), a suite that
# reaches the network because it never declared its market posture (#620), a CI
# trigger that quietly lost its branch filter (#637), a registry entry that
# never named its owner (#492). Those ship silently and a reviewer catches them
# only if the diff hunk happens to include the line.
#
# BUDGET: `tests/test_repo_hygiene.py` measured 0.12s on the maintainer machine
# at #492, against a 20s timeout in `.claude/settings.json`. If this file ever
# grows past that budget, shrink it or move work to CI — do not raise the
# timeout. A gate that can time out is a gate that fails open, which is the
# exact failure #492 found.
#
# Because the gate is repo-wide and cheap, it no longer narrows to
# engine/tests changes: a conflict marker in a Markdown contract is the same
# defect as one in a docstring.
#
# Green -> allow. Red -> DENY the commit and hand the failure tail back.
#
# What "never hard-fails the harness" does and does not cover. It allows (exit
# 0) when the *harness* cannot run the gate: no project directory, no gate
# file, no python3. It does NOT allow when the gate itself runs and returns
# non-zero -- an ImportError or a syntax error in the gate is a broken gate,
# and a broken gate that waves commits through is the failure this whole file
# exists to prevent.

input="$(cat)"

# Only act on git commit commands (critical — see header). The cheap substring
# test just avoids spawning python on every unrelated Bash call; the regex
# below is the real filter.
case "$input" in
  *"git"*"commit"*) : ;;
  *) exit 0 ;;
esac
is_commit="$(printf '%s' "$input" | python3 -c '
import json, sys, re
try:
    cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
except Exception:
    cmd = ""
# `git commit`, and also `git -C <path> commit` / `git --no-pager commit`:
# git accepts global options before the subcommand, and a filter that only
# knew the bare form let `git -C . commit` past the gate entirely.
pattern = r"(^|&&|;|\||\n)\s*git\s+((-[^\s]+|--[^\s]+)(\s+[^\s-][^\s]*)?\s+)*commit\b"
print("1" if re.search(pattern, cmd) else "0")
' 2>/dev/null)"
[ "$is_commit" = "1" ] || exit 0

command -v python3 >/dev/null 2>&1 || exit 0   # no interpreter -> cannot gate

dir="${CLAUDE_PROJECT_DIR:-.}"
cd "$dir" 2>/dev/null || exit 0

gate="tests/test_repo_hygiene.py"
[ -f "$gate" ] || exit 0   # safety: no gate present -> never block

if out="$(python3 "$gate" 2>&1)"; then
  exit 0   # repository integrity intact -> allow the commit
fi

# Red -> deny. Feed the tail of the failure back so the agent knows what broke.
fail_tail="$(printf '%s' "$out" | tail -n 15)"
reason="$(printf '⛔ commit blocked — tests/test_repo_hygiene.py 未過:repository integrity 有問題(衝突標記 / 未宣告 market posture / CI trigger 形狀)。修好再 commit。\n\n行為面不由這個 hook 擋:那是 merge 前的 product-contract CI(docs/maintainer-guide.md)。\n\n%s' "$fail_tail")"
esc="$(printf '%s' "$reason" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$esc"
exit 0
