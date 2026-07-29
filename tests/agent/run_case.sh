#!/usr/bin/env bash
# run_case.sh — #60 harness 的 headless 驅動 + checker 編排(docs/eval-design.md §2)。
#
# 兩種用法:
#   1) 對【已產出】的卡 + 狀態跑機檢(確定性、無網路、可離線;這是 CI-verified 的核心):
#        run_case.sh --check <card.md> <state_dir>
#      state_dir = 一個 ~/.trade-coach 目錄(含 log.jsonl / theses.jsonl)。
#
#   2) headless 產卡再機檢(需 claude CLI + API key,opt-in、有成本、非確定性,不進 CI):
#        run_case.sh --headless <case.yaml>
#      隔離 HOME → claude -p 餵 CSV + persona prompt → 收卡/狀態 → 機檢。
#
# ⚠️ (c) 層限制(EVALS.md 2026-07-04 實測、issue #159):headless claude -p 環境【沒有
#    AskUserQuestion】,Step 2 只會走 fallback 對話路徑——「該問有沒有問」的【工具主路徑】
#    headless 測不到,要在互動 session 驗(case.yaml run_mode: interactive 標的即此)。
#    這支自動化的是 fallback 路徑 + checker 編排,不是內心層的完整 adherence。
#
# checker 本身的驗活(斷言是活的)走 tests/test_checkers_offline.py(進 run_all.py);
# 這支只負責「產卡 → 餵 checker」的編排,不重造斷言。
#
# #542:B-1(check_ticker_diagnosis)需要 case 宣告洗白標的 + 標籤集合,是 case 資料
# 不是這支腳本的常數,所以只在 --headless 分支(手上有 case.yaml 可讀)才轉成
# check_card.py 的 --ticker/--forbidden-tags/--allowed-tags;--check 分支只拿到
# 一張卡 + 一個 state_dir、沒有 case 可查,行為不變。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

run_checkers() {                       # $1=card 檔  $2=state_dir  其餘($3+)=check_card.py 額外參數(可空,#542 B-1 用)
  local card="$1" state_dir="$2" rc=0
  shift 2
  echo "── check_card: $card ──"
  python3 "$HERE/check_card.py" "$card" "$@" || rc=1
  echo "── check_state: $state_dir ──"
  python3 "$HERE/check_state.py" "$state_dir" || rc=1
  return $rc
}

case "${1:-}" in
  --check)
    [ $# -eq 3 ] || { echo "用法: run_case.sh --check <card.md> <state_dir>" >&2; exit 2; }
    run_checkers "$2" "$3"
    ;;

  --headless)
    [ $# -eq 2 ] || { echo "用法: run_case.sh --headless <case.yaml>" >&2; exit 2; }
    CASE="$2"
    command -v claude >/dev/null 2>&1 || {
      echo "找不到 claude CLI —— headless 模式需要它 + API key。" >&2
      echo "(只想跑機檢?用 --check <card> <state_dir>,無需網路。)" >&2
      exit 3
    }
    # 極簡 yaml 取值(csv: 那行);複雜解析不值得為兩個欄位引依賴。
    CSV="$(grep -E '^csv:' "$CASE" | head -1 | sed -E 's/^csv:[[:space:]]*//')"
    NAME="$(grep -E '^name:' "$CASE" | head -1 | sed -E 's/^name:[[:space:]]*//')"
    [ -n "$CSV" ] || { echo "case.yaml 缺 csv: 欄位" >&2; exit 2; }
    # #542:B-1 的洗白標的 + 標籤集合,同款極簡取值——空值合法(不是每個 case 都要
    # 宣告)。每條都補 `|| true`:CSV/NAME 從不缺欄所以從沒踩過,但這三個選填欄在
    # 大多數 case(如 clean.yaml)本來就沒有,grep 找不到會以 exit 1 收尾,`set -e
    # -o pipefail` 下會讓整支腳本在還沒印出任何東西前就當掉。
    SUBJECT_TICKER="$(grep -E '^subject_ticker:' "$CASE" | head -1 | sed -E 's/^subject_ticker:[[:space:]]*//' || true)"
    SUBJECT_FORBIDDEN="$(grep -E '^subject_forbidden_tags:' "$CASE" | head -1 | sed -E 's/^subject_forbidden_tags:[[:space:]]*//' || true)"
    SUBJECT_ALLOWED="$(grep -E '^subject_allowed_tags:' "$CASE" | head -1 | sed -E 's/^subject_allowed_tags:[[:space:]]*//' || true)"

    HOME_TMP="$(mktemp -d)"             # 隔離 HOME:受測 session 的 ~/.trade-coach 不碰真資料(§8 反模式 3)
    trap 'rm -rf "$HOME_TMP"' EXIT
    echo "隔離 HOME=$HOME_TMP  case=$NAME  csv=$CSV"
    echo "⚠️  headless 只跑 fallback 路徑;AskUserQuestion 主路徑要互動驗(見檔頭)。"

    PROMPT="用 /fomo-kernel 復盤這份交易 CSV:$ROOT/skills/fomo-kernel/$CSV。這是自動化測試,persona=$NAME(答題腳本見 tests/agent/personas.md),照該 persona 回答 Step 2。"
    HOME="$HOME_TMP" claude -p "$PROMPT" --output-format stream-json > "$HOME_TMP/trajectory.jsonl" 2>&1 || {
      echo "claude -p 非零退出;trajectory 存 $HOME_TMP/trajectory.jsonl 供查。" >&2
      exit 1
    }

    STATE_DIR="$HOME_TMP/.trade-coach"
    CARD="$(ls -t "$STATE_DIR"/cards/*.md 2>/dev/null | head -1 || true)"
    [ -n "$CARD" ] || { echo "沒找到產出的卡(cards/*.md);skill 可能沒跑到收尾。" >&2; exit 1; }
    EXTRA_ARGS=()
    if [ -n "$SUBJECT_TICKER" ]; then
      EXTRA_ARGS=(--ticker "$SUBJECT_TICKER")
      [ -n "$SUBJECT_FORBIDDEN" ] && EXTRA_ARGS+=(--forbidden-tags "$SUBJECT_FORBIDDEN")
      [ -n "$SUBJECT_ALLOWED" ] && EXTRA_ARGS+=(--allowed-tags "$SUBJECT_ALLOWED")
    fi
    run_checkers "$CARD" "$STATE_DIR" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
    ;;

  *)
    echo "用法:" >&2
    echo "  run_case.sh --check <card.md> <state_dir>   # 機檢已產出的卡/狀態(離線、CI 核心)" >&2
    echo "  run_case.sh --headless <case.yaml>          # headless 產卡再機檢(opt-in、需 claude CLI)" >&2
    exit 2
    ;;
esac
