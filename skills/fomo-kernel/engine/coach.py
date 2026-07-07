#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coach.py — 收尾落盤(#148 首刀:SKILL 收尾 heredoc 的 code 化,狀態側獨立模組)。

trade_recap.py 維持純函式(CSV → JSON),所有讀寫 ~/.trade-coach/ 的收尾動作住這裡
(同 ledger/revisit/problems 的狀態側慣例)。SKILL 層只負責「跟人對話拿到內容」,
格式驗證 / gate 規則 / id 生成 / append 全在本模組——LLM 每週手抄腳本的變異從此陣亡。

CLI(JSON stdout / 訊息 stderr,同 ledger 慣例;全部 append-only、重跑安全=多跑多 append,由 SKILL 序列執行保證單次):
  python3 coach.py close --rule TEXT --metric KEY [--state P] [--log P]
      # log.jsonl append。gate 規則(#56):--rule SKIP 一律不存 commitment;
      # insufficient_data 只擋 engine 機械預設,不擋用戶親選(親選補 baseline_note)。
      # --metric 必須存在於 state.metrics,否則 exit 2(擋填錯 key 靜默存 None)。
  python3 coach.py append-theses THESES.json --session-date D [--theses P]
      # theses.jsonl append。thesis 行驗 cycle_id 3 段格式(#41 的坑:2 段 → 對帳永不匹配);
      # exit_narrative 行驗必填欄。id 生成(session 戳防同日撞 id)在這裡,LLM 不再手拼。
  python3 coach.py append-rules RULES.json --created D [--rules P]
      # rules.jsonl append。metric_key → problem_key 對映內建(PKEY),rule_id 生成。
  python3 coach.py save-card CARD.md --date D [--cards-dir P]
      # 卡片落盤:cards/<date>.md;同日重跑檔名遞增 <date>-2.md,不蓋舊卡(append-only 精神)。
"""
import argparse
import json
import os
import re
import sys
import time

# 與 trade_recap.CYCLE_ID_RE 同一條契約(#61)。coach 刻意不 import trade_recap(保持純標準庫、
# 免 pandas 依賴——同 ledger.py 慣例);pattern 同步由 tests/test_tr_json_contract.py 機械鎖定。
CYCLE_ID_RE = re.compile(r"^[^#\s]+#\d{4}-\d{2}-\d{2}#\d+$")
CYCLE_ID_UNKNOWN_RE = re.compile(r"^[^#\s]+#unknown$")

EXIT_REASONS = {"price_target", "thesis_broken", "swap", "anxiety", None}
CAPTURES = {"user", "inferred", "skipped"}
MATURITIES = {"inferred", "testable", "draft"}
RULE_SOURCES = {"user_chosen", "imported"}
# metric_key → problem_key 對映(問題帳對位;無對位的問題手填 problem_key,不經這張表)
PKEY = {"max_pos_pct": "oversize", "avgdown_count": "avgdown_breach",
        "ai_pct": "concentration", "max_sector_pct": "concentration", "top3_pct": "concentration"}


def _die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def _load_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        _die(f"{what} 檔不存在:{path}")
    except json.JSONDecodeError as e:
        _die(f"{what} 不是合法 JSON:{e}")


def _append_lines(path, rows):
    if not rows:                                      # 0 筆不開檔:空跑不留空檔
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ─────────────────────────── close(log append)───────────────────────────

def cmd_close(args):
    state_path = args.state or os.path.expanduser("~/.trade-coach/last_state.json")
    log_path = args.log or os.path.expanduser("~/.trade-coach/log.jsonl")
    st = _load_json(state_path, "state")

    dflt = st.get("commitment") or {}
    skip = (args.rule == "SKIP")                      # 用戶明確「這週不承諾」(#56):不硬塞錨點
    user_chose = (not skip) and bool((args.rule or "").strip())
    rule = ("" if skip else (args.rule or "")) or dflt.get("rule")
    mk = (args.metric or "") or dflt.get("metric_key")

    if user_chose and args.metric and args.metric not in st.get("metrics", {}):
        _die(f"--metric '{args.metric}' 不在 state.metrics(填錯 key 會靜默存 None,拒收)。"
             f"可用:{', '.join(sorted(st.get('metrics', {})))}")
    if user_chose and rule and not mk:
        print("warning: 規矩有了但無對應 metric,commitment 不落(下次對帳無錨)——"
              "無 metric 對位的規矩走 append-rules(problem_key 對位)", file=sys.stderr)

    commitment = None
    if not skip and rule and mk and (user_chose or not st["insufficient_data"]):
        # SKIP 一律不存;insufficient_data 只擋 engine 機械預設,不擋用戶親選
        commitment = {"rule": rule, "metric_key": mk,
                      "metric_value": st["metrics"].get(mk), "goal": "down",
                      "source": "user_chosen" if user_chose else "engine_default"}
        if user_chose and st["insufficient_data"]:
            commitment["baseline_note"] = "short-sample baseline"  # 下次對帳看方向,不判達標
    entry = {"date_end": st["date_end"], "headline_dim": st["headline_dim"],
             "commitment": commitment,
             "metrics_snapshot": dict(st["metrics"])}
    _append_lines(log_path, [entry])
    print(json.dumps(entry, ensure_ascii=False))


# ─────────────────────── append-theses(theses append)───────────────────────

def _valid_cycle_id(cid):
    return bool(cid) and bool(CYCLE_ID_RE.match(cid) or CYCLE_ID_UNKNOWN_RE.match(cid))


def cmd_append_theses(args):
    theses_path = args.theses or os.path.expanduser("~/.trade-coach/theses.jsonl")
    rows = _load_json(args.file, "theses")
    if not isinstance(rows, list):
        _die("theses JSON 必須是陣列(可為空 [])")

    errs = []
    sid = int(time.time())                            # session 戳:防同日多次 review 撞 id
    for i, t in enumerate(rows):
        tk, cid = t.get("ticker"), t.get("cycle_id")
        if not tk:
            errs.append(f"[{i}] 缺 ticker")
            continue
        if not _valid_cycle_id(cid):
            errs.append(f"[{i}] {tk} cycle_id 格式不合契約:{cid!r}(必須照抄 engine state 的 3 段 "
                        f"ticker#YYYY-MM-DD#n,別自己拼 2 段——2 段 → 對帳永不匹配)")
        if t.get("event") == "exit_narrative":
            if not t.get("revisit_id") or not t.get("exit_date"):
                errs.append(f"[{i}] {tk} exit_narrative 缺 revisit_id / exit_date")
            if t.get("exit_reason") not in EXIT_REASONS:
                errs.append(f"[{i}] {tk} exit_reason 不在 {sorted(k for k in EXIT_REASONS if k)}+null")
            if t.get("capture") not in CAPTURES:
                errs.append(f"[{i}] {tk} capture 必須是 {sorted(CAPTURES)}")
        else:
            if t.get("maturity") not in MATURITIES:
                errs.append(f"[{i}] {tk} maturity 必須是 {sorted(MATURITIES)}")
    if errs:
        _die("append-theses 拒收(0 筆落盤,修完重跑):\n" + "\n".join(errs))

    for i, t in enumerate(rows):
        t["session_date"] = args.session_date
        if t.get("event") == "exit_narrative":        # 出場敘事:不進 active thesis 重建
            t.setdefault("narrative_id", f"exit-{t['ticker']}-{args.session_date}-{sid}-{i}")
        else:
            t.setdefault("status", "active")
            t["thesis_id"] = f"{t['ticker']}-{args.session_date}-{sid}-{i}"
    _append_lines(theses_path, rows)
    print(json.dumps({"appended": len(rows)}, ensure_ascii=False))


# ─────────────────────── append-rules(rules append)───────────────────────

def cmd_append_rules(args):
    rules_path = args.rules or os.path.expanduser("~/.trade-coach/rules.jsonl")
    rows = _load_json(args.file, "rules")
    if not isinstance(rows, list):
        _die("rules JSON 必須是陣列(可為空 [])")

    errs = []
    for i, r in enumerate(rows):
        if not (r.get("text") or "").strip():
            errs.append(f"[{i}] 缺 text(規矩人話)")
        if r.get("source") not in RULE_SOURCES:
            errs.append(f"[{i}] source 必須是 {sorted(RULE_SOURCES)}")
    if errs:
        _die("append-rules 拒收(0 筆落盤,修完重跑):\n" + "\n".join(errs))

    sid = int(time.time())
    for i, r in enumerate(rows):
        if "problem_key" not in r:                    # 對映內建;metric 無對位 → None(人話清單陳列)
            r["problem_key"] = PKEY.get(r.get("metric_key"))
        r.setdefault("status", "tracking")
        r.setdefault("created", args.created)
        r.setdefault("rule_id", f"rule-{sid}-{i}")
    _append_lines(rules_path, rows)
    print(json.dumps({"appended": len(rows)}, ensure_ascii=False))


# ─────────────────────── save-card(卡片落盤)───────────────────────

def cmd_save_card(args):
    cards_dir = args.cards_dir or os.path.expanduser("~/.trade-coach/cards")
    try:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        _die(f"卡片檔不存在:{args.file}")
    if not text.strip():
        _die("卡片檔是空的,拒收(避免落一張空卡)")
    os.makedirs(cards_dir, exist_ok=True)
    path = os.path.join(cards_dir, f"{args.date}.md")
    n = 2
    while os.path.exists(path):                       # 同日重跑 → 遞增,不蓋舊卡
        path = os.path.join(cards_dir, f"{args.date}-{n}.md")
        n += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(json.dumps({"path": path}, ensure_ascii=False))


# ─────────────────────────── main ───────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="fomo-kernel 收尾落盤(狀態側)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("close", help="log.jsonl append(commitment gate)")
    c.add_argument("--rule", default="", help="Step 3.5 用戶親選的規矩;SKIP=這週不承諾;空=用 engine 預設")
    c.add_argument("--metric", default="", help="規矩對應的 metric key(必須在 state.metrics)")
    c.add_argument("--state", default=None)
    c.add_argument("--log", default=None)
    c.set_defaults(fn=cmd_close)

    t = sub.add_parser("append-theses", help="theses.jsonl append(格式驗證 + id 生成)")
    t.add_argument("file", help="theses 陣列 JSON 檔(thesis 行與 exit_narrative 行混排)")
    t.add_argument("--session-date", required=True, help="本次 review 日(= engine state 的 date_end)")
    t.add_argument("--theses", default=None)
    t.set_defaults(fn=cmd_append_theses)

    r = sub.add_parser("append-rules", help="rules.jsonl append(problem_key 對映 + rule_id 生成)")
    r.add_argument("file", help="rules 陣列 JSON 檔")
    r.add_argument("--created", required=True, help="建立日(= date_end)")
    r.add_argument("--rules", default=None)
    r.set_defaults(fn=cmd_append_rules)

    s = sub.add_parser("save-card", help="卡片落盤(同日遞增檔名,不蓋舊)")
    s.add_argument("file", help="卡全文 markdown 檔(含 frontmatter)")
    s.add_argument("--date", required=True, help="state.date_end")
    s.add_argument("--cards-dir", default=None)
    s.set_defaults(fn=cmd_save_card)

    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
