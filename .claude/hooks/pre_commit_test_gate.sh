#!/usr/bin/env bash
# fomo-kernel commit gate (project-scoped, committed)
#
# Registered as PreToolUse:Bash. Self-filters to `git commit` commands — the
# settings `if: Bash(git commit*)` filter is NOT relied upon (some Claude Code
# versions ignore it, in which case the matcher fires on every Bash call). This
# self-filter is what guarantees the 11.5s test suite only ever runs at commit
# time, never on an unrelated `ls`/`git status`.
#
# Turns the CLAUDE.md rule "改 engine ... 五套沒全過就不要 commit" from a habit
# into a MECHANISM:
#   - Only gates when skills/fomo-kernel/engine/ or tests/ have uncommitted
#     changes vs HEAD (doc-/readme-only commits are NOT gated — instant allow).
#   - Green -> allow. Red -> DENY the commit and hand the failure tail back.
# Never hard-fails the harness: on any internal problem it allows (exit 0).

input="$(cat)"

# Only act on git commit commands (critical — see header).
case "$input" in
  *"git commit"*) : ;;
  *) exit 0 ;;
esac
is_commit="$(printf '%s' "$input" | python3 -c '
import json, sys, re
try:
    cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
except Exception:
    cmd = ""
print("1" if re.search(r"(^|&&|;|\||\n)\s*git\s+commit\b", cmd) else "0")
' 2>/dev/null)"
[ "$is_commit" = "1" ] || exit 0

dir="${CLAUDE_PROJECT_DIR:-.}"
cd "$dir" 2>/dev/null || exit 0
[ -f tests/run_all.py ] || exit 0   # safety: no runner present -> never block

# Gate only when engine/tests actually changed vs HEAD (else instant allow).
if git diff --quiet HEAD -- skills/fomo-kernel/engine tests 2>/dev/null; then
  exit 0
fi

if out="$(python3 tests/run_all.py 2>&1)"; then
  exit 0   # all suites green -> allow the commit
fi

# Red -> deny. Feed the tail of the failure back so the agent knows what broke.
fail_tail="$(printf '%s' "$out" | tail -n 15)"
reason="$(printf '⛔ commit blocked — tests/run_all.py 未全綠(CLAUDE.md:五套沒全過就不要 commit)。修好再 commit。\n\n%s' "$fail_tail")"
esc="$(printf '%s' "$reason" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$esc"
exit 0
