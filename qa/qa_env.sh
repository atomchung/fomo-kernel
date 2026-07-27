#!/usr/bin/env bash
#
# qa_env.sh — fomo-kernel dogfood QA environment manager
#
# Purpose: make the dogfood QA environment reproducible so every session tests
# the SAME thing — the latest origin/main, on a clean detached worktree that is
# never used for development. This kills the "every session tests a different
# slice" rework (issue #250).
#
# What this script owns:
#   - the version gate (fetch + refuse to test anything behind origin/main)
#   - a single dedicated dogfood worktree, pinned to origin/main (detached)
#
# Coach state isolation:
#   - dogfood coach state lives in its OWN root ($HOME/.trade-coach-dogfood by
#     default), SEPARATE from the real ~/.trade-coach the product uses. review.py
#     and coach.py both honor TRADE_COACH_HOME, so exporting it once routes the
#     whole review lifecycle into this throwaway root. `reset` wipes only the
#     dogfood root (backup first, then the product's coach.py data-reset --root).
#     The real ~/.trade-coach is reset only by the dedicated reset-fomo-coach.sh;
#     this script never touches it.
#
# HARD ISOLATION (fail closed):
#   - only ever operates on a worktree whose path contains "dogfood", so a
#     misfire can never `checkout --detach` (i.e. discard uncommitted work) on a
#     real development worktree belonging to another session.
#   - the dogfood coach root must contain "dogfood", must not be the real
#     ~/.trade-coach, and must not overlap investment_note (guards below).
#   - only ever runs git against the fomo-kernel repo.
#   - never reads or writes ~/Side_project/investment_note.
#
# Usage:
#   ./qa_env.sh status      # read-only: fetch, report main sha, worktree + dogfood state
#   ./qa_env.sh up          # create/refresh the dogfood worktree to latest origin/main
#   ./qa_env.sh path        # print the dogfood worktree path (for a later cd)
#   ./qa_env.sh coach-root  # print the isolated dogfood coach root (for TRADE_COACH_HOME)
#   ./qa_env.sh reset       # reset the dogfood coach root to fresh new-user (backup first)
#   ./qa_env.sh down        # remove the dogfood worktree (coach state left alone)
#   ./qa_env.sh archive-receipt <receipt.jsonl> <data-source> <human> \
#       --agent-model <exact-host-label> --effort <exact-host-setting>
#                           # archive only with explicit agent provenance; never infer it
#   ./qa_env.sh slice-csv <src.csv> <until YYYY-MM-DD> <dst.csv>
#                           # cut a trade CSV at a TradeDate cutoff, for staged
#                           # same-day replay of multiple review cycles (see
#                           # slice_csv.py for why this doesn't need wall-clock time)
#
# Running more than one dogfood session at once:
#   Every path this script owns (dogfood worktree, dogfood coach root, receipt
#   archive dir, coach-reset backup dir) resolves through an env var with a
#   default. Two concurrent QA sessions on the same machine WILL collide on
#   the defaults (same worktree, same coach root) — pick a distinct tag per
#   session and export all four before calling this script:
#     export FOMO_DOGFOOD_WT="$HOME/Side_project/kol_collector/fomo-kernel-dogfood-<tag>"
#     export FOMO_DOGFOOD_COACH="$HOME/.trade-coach-dogfood-<tag>"
#     export FOMO_DOGFOOD_BACKUPS="$HOME/.trade-coach-dogfood-backups-<tag>"
#   FOMO_QA_RECEIPTS can stay shared — archive-receipt's run_id already carries
#   a second-precision timestamp, the tested commit sha, and the session id,
#   so concurrent archives don't collide on a filename.

set -euo pipefail

REPO="${FOMO_REPO:-$HOME/Side_project/kol_collector/fomo-kernel}"
DOGFOOD_WT="${FOMO_DOGFOOD_WT:-$HOME/Side_project/kol_collector/fomo-kernel-dogfood}"
# reset-fomo-coach.sh resets the REAL ~/.trade-coach only (its own guards refuse any
# other root). Dogfood uses its own isolated root below, reset via `qa_env.sh reset`.
RESET_SH="$HOME/Side_project/kol_collector/reset-fomo-coach.sh"
# Isolated dogfood coach root — never the real ~/.trade-coach. review.py and coach.py
# both honor TRADE_COACH_HOME, so one export routes the whole lifecycle here.
DOGFOOD_COACH_ROOT="${FOMO_DOGFOOD_COACH:-$HOME/.trade-coach-dogfood}"
# Where `reset` backs up the dogfood coach root before wiping it. Parameterized (not
# just DOGFOOD_COACH_ROOT-derived) so two concurrent sessions resetting at the same
# wall-clock second cannot land backups in the same shared directory under the same
# guard umbrella as the other dogfood paths.
DOGFOOD_BACKUP_DIR="${FOMO_DOGFOOD_BACKUPS:-$HOME/.trade-coach-dogfood-backups}"
RECEIPT_DIR="${FOMO_QA_RECEIPTS:-$HOME/.fomo-qa-receipts}"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Isolation guards (fail closed) -----------------------------------------
# 1. The dogfood worktree path MUST contain "dogfood". `up` runs
#    `checkout --detach origin/main`, which discards that worktree's uncommitted
#    changes; pointing it at a real dev worktree would silently destroy another
#    session's work. Refuse unless the name proves this is the throwaway one.
case "$DOGFOOD_WT" in
  *dogfood*) : ;;
  *)
    echo "REFUSING: dogfood worktree path '$DOGFOOD_WT' does not contain 'dogfood'." >&2
    echo "This is a guard against discarding another worktree's work. Fix FOMO_DOGFOOD_WT." >&2
    exit 1 ;;
esac
# 2. Never let the dogfood worktree land inside the real records.
case "$DOGFOOD_WT" in
  *investment_note*|*investment-note*)
    echo "REFUSING: dogfood worktree '$DOGFOOD_WT' overlaps real investment records. Aborting." >&2
    exit 1 ;;
esac
# 2c. The dogfood COACH ROOT is deletable by `reset`; a misfire must never wipe the
#     user's real ~/.trade-coach or land in investment_note. Fail closed on all three.
case "$DOGFOOD_COACH_ROOT" in
  *investment_note*|*investment-note*)
    echo "REFUSING: dogfood coach root '$DOGFOOD_COACH_ROOT' overlaps real records." >&2
    exit 1 ;;
esac
if [ "$DOGFOOD_COACH_ROOT" = "$HOME/.trade-coach" ]; then
  echo "REFUSING: dogfood coach root must not be the real ~/.trade-coach." >&2
  echo "The real root is reset only via reset-fomo-coach.sh; dogfood uses its own." >&2
  exit 1
fi
case "$DOGFOOD_COACH_ROOT" in
  *dogfood*) : ;;
  *)
    echo "REFUSING: dogfood coach root '$DOGFOOD_COACH_ROOT' does not contain 'dogfood'." >&2
    exit 1 ;;
esac
# 2d. Same fail-closed treatment for the backup dir: it must not overlap the real
#     records or the real coach root, and must be clearly marked as a dogfood path.
case "$DOGFOOD_BACKUP_DIR" in
  *investment_note*|*investment-note*)
    echo "REFUSING: dogfood backup dir '$DOGFOOD_BACKUP_DIR' overlaps real investment records." >&2
    exit 1 ;;
esac
if [ "$DOGFOOD_BACKUP_DIR" = "$HOME/.trade-coach" ]; then
  echo "REFUSING: dogfood backup dir must not be the real ~/.trade-coach." >&2
  exit 1
fi
case "$DOGFOOD_BACKUP_DIR" in
  *dogfood*) : ;;
  *)
    echo "REFUSING: dogfood backup dir '$DOGFOOD_BACKUP_DIR' does not contain 'dogfood'." >&2
    exit 1 ;;
esac
# 3. The repo must actually be the fomo-kernel git repo.
if [ ! -e "$REPO/.git" ] || [ ! -f "$REPO/skills/fomo-kernel/engine/review.py" ]; then
  echo "ERROR: fomo-kernel repo not found at $REPO (set FOMO_REPO)." >&2
  exit 1
fi

_origin_main_sha() { git -C "$REPO" rev-parse --short origin/main; }
_origin_main_line() { git -C "$REPO" log -1 --format='%h %s' origin/main; }

# Resolve the physical directory containing THIS running script. Normal
# install is a directory symlink (~/.claude/skills/fomo-qa -> <repo>/qa; the
# script FILE itself usually isn't a symlink, only its parent directory is),
# and macOS `readlink` has no `-f` to resolve that in one call. Walk any
# symlink on the file itself first (rare), then let `cd -P` resolve a
# symlinked directory anywhere in what's left of the path (the normal case).
_self_script_dir() {
  local src="${BASH_SOURCE[0]}"
  local target
  while [ -L "$src" ]; do
    target="$(readlink "$src")"
    case "$target" in
      /*) src="$target" ;;
      *)  src="$(cd -P "$(dirname "$src")" && pwd)/$target" ;;
    esac
  done
  (cd -P "$(dirname "$src")" && pwd)
}

# Report whether the checkout THIS script actually runs from — not $REPO and
# not the dogfood worktree; the installed skill can be symlinked from a
# separate checkout that nothing keeps pinned to origin/main — is stale.
# Report-only, like the rest of `status`: never fetches (this reads whatever
# origin/main ref that checkout already has locally) and never aborts the
# script. Every exit from this function is an `echo` followed by `return 0`,
# so a surprise here can only ever produce an honest "cannot determine" line.
_report_skill_freshness() {
  local self_dir self_repo head behind
  self_dir="$(_self_script_dir 2>/dev/null || true)"
  if [ -z "$self_dir" ]; then
    echo "skill HEAD:    cannot determine (could not resolve this script's real path)"
    return 0
  fi
  self_repo="$(git -C "$self_dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -z "$self_repo" ]; then
    echo "skill HEAD:    cannot determine ($self_dir is not inside a git repository)"
    return 0
  fi
  if [ ! -f "$self_repo/skills/fomo-kernel/engine/review.py" ]; then
    echo "skill HEAD:    cannot determine ($self_repo does not look like a fomo-kernel checkout)"
    return 0
  fi
  if ! git -C "$self_repo" remote get-url origin >/dev/null 2>&1; then
    echo "skill HEAD:    cannot determine ($self_repo has no 'origin' remote)"
    return 0
  fi
  if ! git -C "$self_repo" rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
    echo "skill HEAD:    cannot determine (no local origin/main ref in $self_repo; never fetched)"
    return 0
  fi
  head="$(git -C "$self_repo" rev-parse --short HEAD 2>/dev/null || true)"
  behind="$(git -C "$self_repo" rev-list --count HEAD..origin/main 2>/dev/null || true)"
  if [ -z "$head" ] || [ -z "$behind" ]; then
    echo "skill HEAD:    cannot determine ($self_repo git query failed)"
    return 0
  fi
  echo "skill HEAD:    $head  (behind origin/main by $behind)"
  if [ "$behind" = "0" ]; then
    echo "               -> UP TO DATE. This qa skill checkout matches origin/main."
  else
    echo "               -> STALE by $behind commit(s). Fix (safe under detached HEAD):"
    echo "               git -C $self_repo pull --ff-only origin main"
  fi
  return 0
}

cmd_status() {
  echo "== fomo-kernel QA environment =="
  echo "repo:          $REPO"
  echo "dogfood wt:    $DOGFOOD_WT"
  echo ""
  echo "-- fetching origin/main --"
  git -C "$REPO" fetch origin main -q
  echo "origin/main:   $(_origin_main_line)"
  echo ""

  if [ -e "$DOGFOOD_WT/.git" ]; then
    local head behind
    head="$(git -C "$DOGFOOD_WT" rev-parse --short HEAD)"
    behind="$(git -C "$DOGFOOD_WT" rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
    echo "dogfood HEAD:  $head  (behind origin/main by $behind)"
    if [ "$behind" = "0" ]; then
      echo "               -> CLEAN & LATEST. Safe to QA."
    else
      echo "               -> STALE. Run './qa_env.sh up' before testing."
    fi
  else
    echo "dogfood HEAD:  (worktree not created yet)"
    echo "               -> Run './qa_env.sh up' to create it."
  fi
  echo ""
  echo "-- this qa skill's own freshness (checkout running this script; no fetch) --"
  _report_skill_freshness
  echo ""
  echo "-- dogfood coach state (isolated root, NOT the real ~/.trade-coach) --"
  local coach="$REPO/skills/fomo-kernel/engine/coach.py"
  if [ -f "$coach" ]; then
    python3 "$coach" data-status --root "$DOGFOOD_COACH_ROOT" 2>/dev/null | python3 -c "import sys,json;\
d=json.load(sys.stdin);\
print('coach root:  ', d['root']);\
present=[f['name'] for f in d['files'] if f.get('exists')];\
print('state files: ', (', '.join(present) if present else 'NONE -> fresh new-user'))" 2>/dev/null \
      || echo "(could not read dogfood coach state)"
  else
    echo "(coach.py not found at $coach)"
  fi
  echo ""
  echo "-- route the review lifecycle into this isolated root before walking Step 4: --"
  echo "   export TRADE_COACH_HOME=$DOGFOOD_COACH_ROOT"
}

cmd_up() {
  git -C "$REPO" fetch origin main -q
  local sha; sha="$(_origin_main_sha)"
  if [ -e "$DOGFOOD_WT/.git" ]; then
    echo "Refreshing dogfood worktree to origin/main ($sha)..."
    git -C "$DOGFOOD_WT" fetch origin main -q
    git -C "$DOGFOOD_WT" checkout --detach origin/main -q
  else
    echo "Creating dogfood worktree at $DOGFOOD_WT, pinned to origin/main ($sha)..."
    git -C "$REPO" worktree add --detach "$DOGFOOD_WT" origin/main -q
  fi
  local head; head="$(git -C "$DOGFOOD_WT" rev-parse --short HEAD)"
  if [ "$head" != "$sha" ]; then
    echo "ERROR: dogfood HEAD ($head) != origin/main ($sha) after up." >&2
    exit 1
  fi
  echo "OK. dogfood worktree is at origin/main@$head (detached)."
  echo "    cd $DOGFOOD_WT/skills/fomo-kernel"
}

cmd_path() { echo "$DOGFOOD_WT"; }

cmd_coach_root() { echo "$DOGFOOD_COACH_ROOT"; }

cmd_reset_coach() {
  # Reset the DOGFOOD coach root (never the real ~/.trade-coach) to fresh new-user.
  # Backs up first when non-empty, then wipes — via the product's own coach.py,
  # which honors --root. The top-of-script guards already refused if this root is
  # the real coach dir, overlaps investment_note, or lacks "dogfood".
  local coach="$REPO/skills/fomo-kernel/engine/coach.py"
  if [ ! -f "$coach" ]; then
    echo "ERROR: coach.py not found at $coach" >&2; exit 1
  fi
  if [ -d "$DOGFOOD_COACH_ROOT" ] && [ -n "$(ls -A "$DOGFOOD_COACH_ROOT" 2>/dev/null)" ]; then
    mkdir -p "$DOGFOOD_BACKUP_DIR"
    local out="$DOGFOOD_BACKUP_DIR/dogfood-$(date +%Y%m%d-%H%M%S).zip"
    echo "Backing up dogfood coach state -> $out"
    python3 "$coach" data-export --root "$DOGFOOD_COACH_ROOT" --out "$out" || true
  else
    echo "Dogfood coach root already empty; nothing to back up."
  fi
  echo "Resetting dogfood coach root to fresh new-user: $DOGFOOD_COACH_ROOT"
  python3 "$coach" data-reset --root "$DOGFOOD_COACH_ROOT" --confirm
}

cmd_down() {
  if [ -e "$DOGFOOD_WT/.git" ]; then
    echo "Removing dogfood worktree $DOGFOOD_WT (coach state untouched)..."
    git -C "$REPO" worktree remove --force "$DOGFOOD_WT"
    echo "Removed."
  else
    echo "No dogfood worktree at $DOGFOOD_WT; nothing to remove."
  fi
}

cmd_archive_receipt() {
  if [ "$#" -lt 3 ]; then
    echo "usage: $0 archive-receipt <path> <data-source> <human> --agent-model <exact-host-label> --effort <exact-host-setting>" >&2
    exit 2
  fi
  local src="$1"
  local data_source="$2"
  local human="$3"
  shift 3
  local agent_model=""
  local agent_effort=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --agent-model)
        if [ "$#" -lt 2 ]; then
          echo "ERROR: --agent-model requires the exact host label." >&2
          exit 2
        fi
        agent_model="$2"
        shift 2 ;;
      --effort)
        if [ "$#" -lt 2 ]; then
          echo "ERROR: --effort requires the exact host setting." >&2
          exit 2
        fi
        agent_effort="$2"
        shift 2 ;;
      *)
        echo "ERROR: unknown archive-receipt argument '$1'." >&2
        exit 2 ;;
    esac
  done
  _require_declared_agent_value() {
    local field="$1" value="$2" normalized
    normalized="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
    case "$normalized" in
      ""|unknown|unspecified|default|n/a|na|not_available|'?')
        echo "ERROR: $field must be an exact host-declared value, not '$value'." >&2
        exit 1 ;;
    esac
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]] || [ "${#value}" -gt 200 ]; then
      echo "ERROR: $field must be a single line no longer than 200 characters." >&2
      exit 1
    fi
  }
  _require_declared_agent_value "agent model" "$agent_model"
  _require_declared_agent_value "agent effort" "$agent_effort"
  if [ ! -f "$src" ]; then
    echo "ERROR: receipt not found: $src (pass the ux_receipt jsonl path)." >&2
    exit 1
  fi
  case "$human" in
    owner_live|agent_with_owner_verdict|agent_simulated) : ;;
    *)
      echo "ERROR: human must be owner_live|agent_with_owner_verdict|agent_simulated (got '$human')." >&2
      exit 1 ;;
  esac
  if [ ! -e "$DOGFOOD_WT/.git" ]; then
    echo "ERROR: no dogfood worktree at $DOGFOOD_WT; cannot determine which commit was actually tested. Archive before running './qa_env.sh down'." >&2
    exit 1
  fi
  # Runbook gate (docs/qa-runbook.md): a receipt that does not verify is not a
  # QA run — refuse to let it into the ledger. When the run claims any human
  # involvement, the trace must also carry a passing owner_verdict, so a run
  # labeled owner_live without a verdict cannot inflate the ground-truth count.
  local receipt_tool="$DOGFOOD_WT/skills/fomo-kernel/tools/ux_receipt.py"
  if [ ! -f "$receipt_tool" ]; then
    echo "ERROR: ux_receipt.py not found in dogfood worktree; cannot verify receipt before archiving." >&2
    exit 1
  fi
  if [ "$(basename "$(dirname "$src")")" = "ux" ]; then
    local vsid vroot
    vsid="$(basename "$src" .jsonl)"
    vroot="$(dirname "$(dirname "$src")")"
    local verify_args=(--session-id "$vsid" --state-root "$vroot")
    if [ "$human" != "agent_simulated" ]; then
      # Runbook gate 4 names *both* flags for a human-graded run, because only
      # timing_integrity.status=credible is eligible to become fresh owner_live
      # UX ground truth. Archiving used to pass just --require-owner-verdict:
      # verify still computed the timing warning and still printed it, but the
      # archive succeeded anyway, so the public contract's bar was one flag
      # higher than anything actually enforced it.
      verify_args+=(--require-owner-verdict --require-timing-integrity)
    fi
    # Runbook gate 7 (#417). Every level of human involvement carries it: the
    # thing being gated is whether the run's misses became replayable, and an
    # agent-simulated run finds misses too. Passed only when the tested worktree
    # is new enough to know the flag — an older checkout still archives, and
    # says which gate it could not run rather than implying it passed.
    if python3 "$receipt_tool" verify --help 2>/dev/null | grep -q -- --require-findings; then
      verify_args+=(--require-findings)
    else
      echo "WARNING: tested checkout predates --require-findings; gate 7 not enforced for this run." >&2
    fi
    if ! python3 "$receipt_tool" verify "${verify_args[@]}" >/dev/null; then
      echo "REFUSING to archive: receipt fails ux_receipt.py verify (human=$human)." >&2
      echo "Fix the trace (or archive as agent_simulated if no owner verdict exists) — a non-verifying run is not a QA run." >&2
      echo "If it is timing integrity: the trace's timestamps are not credible (missing, same-burst," >&2
      echo "  or out of order), so this run cannot become fresh owner_live UX ground truth — that is" >&2
      echo "  runbook gate 4, and it is newly enforced here. Backfilled receipts hit this. Either" >&2
      echo "  re-run recording each event when it actually happened, or archive as agent_simulated." >&2
      echo "If it is gate 7: record where this run's misses went before archiving —" >&2
      echo "  ux_receipt.py event --event findings_recorded --finding episode:EP-NNN" >&2
      echo "  (or --no-findings, which is a declaration, not an omission). See Step 6." >&2
      exit 1
    fi
  else
    echo "WARNING: receipt is not under <state-root>/ux/, verify gate skipped (archiving anyway)." >&2
  fi
  local sha stamp base run_id dest manifest
  sha="$(git -C "$DOGFOOD_WT" rev-parse --short HEAD)"
  stamp="$(date +%Y%m%d-%H%M%S)"
  base="$(basename "$src" .jsonl)"
  run_id="${stamp}__main-${sha}__${base}"
  mkdir -p "$RECEIPT_DIR"
  dest="$RECEIPT_DIR/${run_id}.jsonl"
  manifest="$RECEIPT_DIR/${run_id}.manifest.json"
  cp "$src" "$dest"
  python3 "$SKILL_DIR/receipts.py" build "$dest" "$sha" "$data_source" "$human" "$agent_model" "$agent_effort" "$run_id" "$stamp" > "$manifest"
  echo "Archived receipt  -> $dest"
  echo "Wrote manifest    -> $manifest"
  echo "(main@$sha | data=$data_source | human=$human | agent=$agent_model | effort=$agent_effort)"
}

cmd_report() {
  python3 "$SKILL_DIR/receipts.py" report "$RECEIPT_DIR"
}

cmd_slice_csv() {
  local src="${1:-}" until_date="${2:-}" dst="${3:-}"
  if [ -z "$src" ] || [ -z "$until_date" ] || [ -z "$dst" ]; then
    echo "usage: $0 slice-csv <src.csv> <until YYYY-MM-DD> <dst.csv>" >&2
    exit 2
  fi
  python3 "$SKILL_DIR/slice_csv.py" "$src" "$until_date" "$dst"
}

case "${1:-status}" in
  status)          cmd_status ;;
  up)              cmd_up ;;
  path)            cmd_path ;;
  coach-root)      cmd_coach_root ;;
  reset)           cmd_reset_coach ;;
  down)            cmd_down ;;
  archive-receipt) shift; cmd_archive_receipt "$@" ;;
  report)          cmd_report ;;
  slice-csv)       cmd_slice_csv "${2:-}" "${3:-}" "${4:-}" ;;
  *)
    echo "usage: $0 {status|up|path|coach-root|reset|down|archive-receipt <path> <data-source> <human> --agent-model <label> --effort <setting>|report|slice-csv <src.csv> <until> <dst.csv>}" >&2
    exit 2 ;;
esac
