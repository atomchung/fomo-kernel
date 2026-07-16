#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coach.py — 收尾落盤(#148 首刀:SKILL 收尾 heredoc 的 code 化,狀態側獨立模組)。

trade_recap.py 維持純函式(CSV → JSON),所有讀寫 ~/.trade-coach/ 的收尾動作住這裡
(同 ledger/revisit/problems 的狀態側慣例)。SKILL 層只負責「跟人對話拿到內容」,
格式驗證 / gate 規則 / id 生成 / append 全在本模組——LLM 每週手抄腳本的變異從此陣亡。

CLI(JSON stdout / 訊息 stderr,同 ledger 慣例;#166:close/append-theses/append-rules/save-card
四個收尾指令各自 session 級 idempotent——session_id 從 --state 內容雜湊算出(非 time.time(),
同一份 state 重算永遠同一個 id),同 session 重試 = no-op,同 session 但內容不同 = fail closed
不寫入。同日兩個「內容恰巧相同但邏輯上不同」的 session 用 --session-nonce 明確拆開。
跨指令(例如 close 成功但 append-theses 失敗)不保證整批回滾——這是刻意的取捨,見 issue #166):
  python3 coach.py close --rule TEXT --metric KEY [--state P] [--log P] [--session-nonce N]
      # log.jsonl append。gate 規則(#56):--rule SKIP 一律不存 commitment;
      # insufficient_data 只擋 engine 機械預設,不擋用戶親選(親選補 baseline_note)。
      # --metric 必須存在於 state.metrics,否則 exit 2(擋填錯 key 靜默存 None)。
  python3 coach.py append-theses THESES.json --session-date D [--theses P] [--state P] [--session-nonce N]
      # theses.jsonl append。thesis 行驗 cycle_id 3 段格式(#41 的坑:2 段 → 對帳永不匹配);
      # exit_narrative 行驗必填欄。id 由 session_id fingerprint 生成,同 session 重試 id 不變。
      # --state 檔不存在時退化成無 session 級保護(僅供孤立呼叫,正常 SKILL 流程不會觸發)。
  python3 coach.py append-rules RULES.json --created D [--rules P] [--state P] [--session-nonce N]
      # rules.jsonl append。metric_key → problem_key 對映內建(PKEY),rule_id 生成。
  python3 coach.py save-card CARD.md --date D [--cards-dir P] [--state P] [--session-nonce N]
      # 卡片落盤:cards/<date>.md;frontmatter 注入 session_id。同 session 重試 = no-op(不
      # 產生新檔);真正不同的第二個 session 才遞增 <date>-2.md,不蓋舊卡。
  python3 coach.py data-status [--root P]
      # 列出 ~/.trade-coach/ 下每個已知檔案的存在/大小/筆數(#165:單一命令看到完整持久化足跡,
      # 不印交易內容本身)。root 預設 ~/.trade-coach,測試/檢視別的路徑用 --root 覆寫。
  python3 coach.py data-export --out BACKUP.zip [--root P]
      # 把現有檔案打包成 zip 備份;stderr 明確標示內含敏感交易衍生資料。
  python3 coach.py data-reset (--dry-run | --confirm) [--root P]
      # 清空 ~/.trade-coach/。--dry-run 只列會刪什麼,--confirm 才真的刪(不可復原,兩者擇一,
      # 沒帶旗標一律拒收,不給「裸執行就刪除」的預設)。
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger as lg  # noqa: E402  # 共用 atomic_write_text / session_id_from_state(#166)

# 與 trade_recap.CYCLE_ID_RE 同一條契約(#61)。coach 刻意不 import trade_recap(保持純標準庫、
# 免 pandas 依賴——同 ledger.py 慣例);pattern 同步由 tests/test_tr_json_contract.py 機械鎖定。
CYCLE_ID_RE = re.compile(r"^[^#\s]+#\d{4}-\d{2}-\d{2}#\d+$")
CYCLE_ID_UNKNOWN_RE = re.compile(r"^[^#\s]+#unknown$")

EXIT_REASONS = {"price_target", "thesis_broken", "swap", "anxiety", None}
CAPTURES = {"user", "inferred", "skipped"}
MATURITIES = {"inferred", "testable", "draft"}
RULE_SOURCES = {"user_chosen", "imported"}
# #36 進場情緒/信心(inference-first,選填;缺欄=None 合法,legacy thesis 不破)。
# 現在只累積、不上卡(同 source_type #38 薄版)——樣本夠了才做「FOMO 進場勝率 vs composed」分組。
EMOTIONS = {"fomo", "composed", "forced", "planned", None}
CONFIDENCES = {"high", "medium", "low", None}
# metric_key → problem_key 對映(問題帳對位;無對位的問題手填 problem_key,不經這張表)
PKEY = {"max_pos_pct": "oversize", "avgdown_count": "avgdown_breach",
        "ai_pct": "concentration", "max_sector_pct": "concentration", "top3_pct": "concentration",
        "exit_severity": "sell_winner_early", "hold_severity": "hold_inconsistency"}


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


# ─────────────────── session 級 idempotency 共用工具(#166)───────────────────

def _read_jsonl_rows(path):
    """容錯讀 JSONL:壞行跳過,不 crash(同 problems.py load_book 慣例)。"""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _strip_session_id(obj):
    d = dict(obj)
    d.pop("session_id", None)
    return d


def _strip_generated(obj, *extra_keys):
    """比對用:除了 session_id,也拿掉 CLI 自己生成的 id 欄位(thesis_id/narrative_id/rule_id)。
    這些 id 目前由陣列位置(#166 review 抓到:位置一變,id 就變)生成,不是使用者提交的內容,
    納入比對會把「同一份邏輯內容、陣列順序恰巧不同」誤判成衝突——見 cmd_append_theses/rules。"""
    d = dict(obj)
    d.pop("session_id", None)
    for k in extra_keys:
        d.pop(k, None)
    return d


def _optional_session_id(args):
    """append-theses/append-rules/save-card 用:--state 檔存在才算 session_id;不存在不算
    錯誤,退化成不做 session 級去重/衝突偵測(僅供孤立呼叫,正常 SKILL 六步流程裡 Step 1
    早就寫出 last_state.json,不會走到這條退化路徑)。"""
    state_path = args.state or os.path.expanduser("~/.trade-coach/last_state.json")
    if not os.path.exists(state_path):
        print(f"warning: state 檔不存在({state_path}),本次不做 session 級去重/衝突偵測",
              file=sys.stderr)
        return None
    st = _load_json(state_path, "state")
    return lg.session_id_from_state(st, args.session_nonce or "")


# ─────────────────────────── close(log append)───────────────────────────

def cmd_close(args):
    state_path = args.state or os.path.expanduser("~/.trade-coach/last_state.json")
    log_path = args.log or os.path.expanduser("~/.trade-coach/log.jsonl")
    st = _load_json(state_path, "state")
    session_id = lg.session_id_from_state(st, args.session_nonce or "")

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
             "metrics_snapshot": dict(st["metrics"]),
             "session_id": session_id}

    existing = [r for r in _read_jsonl_rows(log_path) if r.get("session_id") == session_id]
    if existing:
        if _canonical(_strip_session_id(existing[-1])) == _canonical(_strip_session_id(entry)):
            print(json.dumps(existing[-1], ensure_ascii=False))    # 同 session 重試:no-op
            return
        _die(f"close 拒收:session {session_id} 已存在內容不同的紀錄(這次跟上次收尾內容不一致)。"
             f"若這確實是新的一次 review,帶 --session-nonce 明確拆開。")
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
            # #36:emotion/confidence 選填(inference-first),但填了就得在 enum 內(擋填錯值靜默存髒)
            if t.get("emotion") not in EMOTIONS:
                errs.append(f"[{i}] {tk} emotion 必須是 {sorted(k for k in EMOTIONS if k)}+null(選填)")
            if t.get("confidence") not in CONFIDENCES:
                errs.append(f"[{i}] {tk} confidence 必須是 {sorted(k for k in CONFIDENCES if k)}+null(選填)")
    if errs:
        _die("append-theses 拒收(0 筆落盤,修完重跑):\n" + "\n".join(errs))

    session_id = _optional_session_id(args)
    # id 尾碼:有 session_id 就用其 fingerprint(同 session 重試 id 不變);沒有(孤立呼叫、
    # state 檔不存在)才退回舊的 time.time() 尾碼,只影響那條退化路徑本身的 id 唯一性。
    sid = session_id.split("__")[1] if session_id else str(int(time.time()))
    for i, t in enumerate(rows):
        t["session_date"] = args.session_date
        if session_id:
            t["session_id"] = session_id
        if t.get("event") == "exit_narrative":        # 出場敘事:不進 active thesis 重建
            t.setdefault("narrative_id", f"exit-{t['ticker']}-{args.session_date}-{sid}-{i}")
        else:
            t.setdefault("status", "active")
            t["thesis_id"] = f"{t['ticker']}-{args.session_date}-{sid}-{i}"

    if session_id:
        existing = [r for r in _read_jsonl_rows(theses_path) if r.get("session_id") == session_id]
        if existing:
            existing_content = {_canonical(_strip_generated(r, "thesis_id", "narrative_id"))
                                 for r in existing}
            new_content = [_canonical(_strip_generated(t, "thesis_id", "narrative_id"))
                           for t in rows]
            new_content_set = set(new_content)
            if existing_content == new_content_set:
                print(json.dumps({"appended": 0, "note": "no-op:同 session 已存在相同內容"},
                                 ensure_ascii=False))
                return
            if existing_content <= new_content_set:      # 既有內容全在這次提交裡 → 合法追加
                delta = [t for t, c in zip(rows, new_content) if c not in existing_content]
                _append_lines(theses_path, delta)
                print(json.dumps({"appended": len(delta),
                                  "note": "同 session 追加:只補新增的部分,已存在的照舊"},
                                 ensure_ascii=False))
                return
            _die(f"append-theses 拒收:session {session_id} 已存在內容不同的紀錄"
                 f"(不是單純追加——有既有內容在這次提交中消失或變了)。"
                 f"若這確實是新的一次 review,帶 --session-nonce 明確拆開。")
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

    session_id = _optional_session_id(args)
    sid = session_id.split("__")[1] if session_id else str(int(time.time()))
    for i, r in enumerate(rows):
        if "problem_key" not in r:                    # 對映內建;metric 無對位 → None(人話清單陳列)
            r["problem_key"] = PKEY.get(r.get("metric_key"))
        r.setdefault("status", "tracking")
        r.setdefault("created", args.created)
        r.setdefault("rule_id", f"rule-{sid}-{i}")
        if session_id:
            r["session_id"] = session_id

    if session_id:
        existing = [x for x in _read_jsonl_rows(rules_path) if x.get("session_id") == session_id]
        if existing:
            existing_content = {_canonical(_strip_generated(x, "rule_id")) for x in existing}
            new_content = [_canonical(_strip_generated(x, "rule_id")) for x in rows]
            new_content_set = set(new_content)
            if existing_content == new_content_set:
                print(json.dumps({"appended": 0, "note": "no-op:同 session 已存在相同內容"},
                                 ensure_ascii=False))
                return
            if existing_content <= new_content_set:      # 既有內容全在這次提交裡 → 合法追加
                delta = [x for x, c in zip(rows, new_content) if c not in existing_content]
                _append_lines(rules_path, delta)
                print(json.dumps({"appended": len(delta),
                                  "note": "同 session 追加:只補新增的部分,已存在的照舊"},
                                 ensure_ascii=False))
                return
            _die(f"append-rules 拒收:session {session_id} 已存在內容不同的紀錄"
                 f"(不是單純追加——有既有內容在這次提交中消失或變了)。"
                 f"若這確實是新的一次 review,帶 --session-nonce 明確拆開。")
    _append_lines(rules_path, rows)
    print(json.dumps({"appended": len(rows)}, ensure_ascii=False))


# ─────────────────────── save-card(卡片落盤)───────────────────────

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _inject_session_id(text, session_id):
    """權威性插入/覆寫卡片 frontmatter 的 session_id 欄位(不管呼叫端有沒有帶,由 CLI 蓋掉)。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:                                          # 理論上不該發生(卡片一律含 frontmatter)
        return f"---\nsession_id: {session_id}\n---\n{text}"
    lines = [ln for ln in m.group(1).splitlines() if not ln.startswith("session_id:")]
    lines.append(f"session_id: {session_id}")
    return "---\n" + "\n".join(lines) + "\n---\n" + text[m.end():]


def _card_session_id(text):
    """從卡片文字的 frontmatter 讀 session_id;找不到回 None。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.startswith("session_id:"):
            return line.split(":", 1)[1].strip()
    return None


def _strip_card_session_id(text):
    """比對內容用:拿掉 session_id 那一行,其餘 frontmatter + 卡體逐字比較。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text
    lines = [ln for ln in m.group(1).splitlines() if not ln.startswith("session_id:")]
    return "---\n" + "\n".join(lines) + "\n---\n" + text[m.end():]


def cmd_save_card(args):
    cards_dir = args.cards_dir or os.path.expanduser("~/.trade-coach/cards")
    try:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        _die(f"卡片檔不存在:{args.file}")
    if not text.strip():
        _die("卡片檔是空的,拒收(避免落一張空卡)")

    session_id = _optional_session_id(args)
    if session_id:
        text = _inject_session_id(text, session_id)

    os.makedirs(cards_dir, exist_ok=True)

    if session_id:
        for p in sorted(glob.glob(os.path.join(cards_dir, f"{args.date}*.md"))):
            with open(p, encoding="utf-8") as f:
                old_text = f.read()
            if _card_session_id(old_text) != session_id:
                continue
            if _strip_card_session_id(old_text) == _strip_card_session_id(text):
                print(json.dumps({"path": p, "note": "no-op:同 session 已存在相同內容"},
                                 ensure_ascii=False))                # 同 session 重試:no-op
                return
            _die(f"save-card 拒收:session {session_id} 已存在內容不同的卡片({p})。"
                 f"若這確實是新的一次 review,帶 --session-nonce 明確拆開。")

    path = os.path.join(cards_dir, f"{args.date}.md")
    n = 2
    while os.path.exists(path):          # 同日真正不同的第二個 session → 遞增,不蓋舊卡
        path = os.path.join(cards_dir, f"{args.date}-{n}.md")
        n += 1
    lg.atomic_write_text(path, text)
    print(json.dumps({"path": path}, ensure_ascii=False))


# ─────────────── data-status / data-export / data-reset(#165)───────────────
# 單一事實源:別的地方(README/SKILL.md)要講「本機存了什麼」,一律指來跑這裡,
# 不要另外維護一份會 drift 的散文清單(同 #82 的判定進 code、文案別四處抄的教訓)。
DATA_FILES = [
    ("last_state.json", "json", "上次引擎算出的薄狀態(對帳用;每次跑覆蓋,非 append-only)"),
    ("log.jsonl", "jsonl", "每次復盤的規矩承諾 + metric 快照"),
    ("theses.jsonl", "jsonl", "每筆持倉的持股假設與出場敘事"),
    ("thesis_decisions.jsonl", "jsonl", "每次加碼的 thesis 決策與 evidence delta"),
    ("profile.md", "text", "交易目標 + 個人原則(第一次復盤時建立,Claude 直接寫檔)"),
    ("rules.jsonl", "jsonl", "累積的規矩庫"),
    ("problems.jsonl", "jsonl", "問題事件記錄(#137)"),
    ("ledger.jsonl", "jsonl", "交易/持倉快照帳本"),
    ("revisit.jsonl", "jsonl", "出場後 30/60/90 天追蹤佇列"),
    ("cards", "dir", "每次復盤的完整復盤卡(含絕對金額/ticker/佔比)"),
    ("sessions", "tree", "v2 canonical session bundles(private/public cards + manifest)"),
    ("projections", "dir", "canonical bundle 投影到舊資料檔的修復紀錄"),
    (".pending", "tree", "尚未 finalize 的可恢復 review plan/answers/preview"),
]


def _coach_root(args):
    return os.path.expanduser(args.root) if args.root else os.path.expanduser("~/.trade-coach")


def _scan_root(root):
    """回傳 DATA_FILES 每個路徑的現況——只算大小/筆數,不讀交易內容本身。"""
    out = []
    for name, kind, desc in DATA_FILES:
        path = os.path.join(root, name)
        entry = {"name": name, "path": path, "kind": kind, "desc": desc,
                 "exists": os.path.exists(path)}
        if entry["exists"]:
            if kind in {"dir", "tree"}:
                if kind == "tree":
                    files = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(path) for f in fs)
                else:
                    files = sorted(os.path.join(path, f) for f in os.listdir(path)
                                   if os.path.isfile(os.path.join(path, f)))
                entry["count"] = len(files)
                entry["size_bytes"] = sum(os.path.getsize(f) for f in files)
            else:
                entry["size_bytes"] = os.path.getsize(path)
                if kind == "jsonl":
                    with open(path, encoding="utf-8") as f:
                        entry["lines"] = sum(1 for _ in f)
        out.append(entry)
    return out


def cmd_data_status(args):
    root = _coach_root(args)
    scan = _scan_root(root)
    present = [e for e in scan if e["exists"]]
    print(json.dumps({"root": root, "files": scan, "present_count": len(present)},
                     ensure_ascii=False, indent=2))


def cmd_data_export(args):
    root = _coach_root(args)
    scan = _scan_root(root)
    present = [e for e in scan if e["exists"]]
    if not present:
        _die(f"{root} 下沒有任何資料可匯出(可能是第一次使用,或 --root 指錯路徑)")
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in present:
            if e["kind"] in {"dir", "tree"}:
                for dp, _, files in os.walk(e["path"]):
                    for f in sorted(files):
                        fp = os.path.join(dp, f)
                        rel = os.path.relpath(fp, e["path"])
                        zf.write(fp, arcname=os.path.join(e["name"], rel))
            else:
                zf.write(e["path"], arcname=e["name"])
    print(f"⚠️  匯出檔含敏感交易衍生資料(部位金額/ticker/規矩承諾),請比照對帳單妥善保存:{args.out}",
         file=sys.stderr)
    print(json.dumps({"out": args.out, "included": [e["name"] for e in present]},
                     ensure_ascii=False))


def cmd_data_reset(args):
    root = _coach_root(args)
    scan = _scan_root(root)
    present = [e for e in scan if e["exists"]]
    if not present:
        print(json.dumps({"root": root, "deleted": [], "note": "沒有資料可清"}, ensure_ascii=False))
        return
    if args.dry_run:
        print(json.dumps({"root": root, "would_delete": [e["path"] for e in present],
                          "note": "dry-run,尚未刪除任何東西;確認後加 --confirm 重跑才會真的刪"},
                         ensure_ascii=False))
        return
    deleted = []
    for e in present:
        if e["kind"] in {"dir", "tree"}:
            shutil.rmtree(e["path"])
        else:
            os.remove(e["path"])
        deleted.append(e["path"])
    print(json.dumps({"root": root, "deleted": deleted}, ensure_ascii=False))


# ─────────────────────────── main ───────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="fomo-kernel 收尾落盤(狀態側)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("close", help="log.jsonl append(commitment gate)")
    c.add_argument("--rule", default="", help="Step 3.5 用戶親選的規矩;SKIP=這週不承諾;空=用 engine 預設")
    c.add_argument("--metric", default="", help="規矩對應的 metric key(必須在 state.metrics)")
    c.add_argument("--state", default=None)
    c.add_argument("--log", default=None)
    c.add_argument("--session-nonce", default=None,
                   help="逃生艙口:同一份 state 內容但邏輯上是不同 session 時,帶不同值明確拆開")
    c.set_defaults(fn=cmd_close)

    t = sub.add_parser("append-theses", help="theses.jsonl append(格式驗證 + id 生成)")
    t.add_argument("file", help="theses 陣列 JSON 檔(thesis 行與 exit_narrative 行混排)")
    t.add_argument("--session-date", required=True, help="本次 review 日(= engine state 的 date_end)")
    t.add_argument("--theses", default=None)
    t.add_argument("--state", default=None, help="算 session_id 用;檔不存在則退化成無 session 級保護")
    t.add_argument("--session-nonce", default=None)
    t.set_defaults(fn=cmd_append_theses)

    r = sub.add_parser("append-rules", help="rules.jsonl append(problem_key 對映 + rule_id 生成)")
    r.add_argument("file", help="rules 陣列 JSON 檔")
    r.add_argument("--created", required=True, help="建立日(= date_end)")
    r.add_argument("--rules", default=None)
    r.add_argument("--state", default=None, help="算 session_id 用;檔不存在則退化成無 session 級保護")
    r.add_argument("--session-nonce", default=None)
    r.set_defaults(fn=cmd_append_rules)

    s = sub.add_parser("save-card", help="卡片落盤(同日遞增檔名,不蓋舊)")
    s.add_argument("file", help="卡全文 markdown 檔(含 frontmatter)")
    s.add_argument("--date", required=True, help="state.date_end")
    s.add_argument("--cards-dir", default=None)
    s.add_argument("--state", default=None, help="算 session_id 用;檔不存在則退化成無 session 級保護")
    s.add_argument("--session-nonce", default=None)
    s.set_defaults(fn=cmd_save_card)

    ds = sub.add_parser("data-status", help="列出本機保存了哪些資料(路徑/大小/筆數,不印交易內容)")
    ds.add_argument("--root", default=None, help="覆寫 ~/.trade-coach/ 路徑(預設值本身)")
    ds.set_defaults(fn=cmd_data_status)

    de = sub.add_parser("data-export", help="把現有資料打包成 zip 備份")
    de.add_argument("--out", required=True, help="輸出 zip 路徑")
    de.add_argument("--root", default=None)
    de.set_defaults(fn=cmd_data_export)

    dr = sub.add_parser("data-reset", help="清空本機資料(dry-run 預覽或 confirm 實際刪除,兩者擇一)")
    dr_grp = dr.add_mutually_exclusive_group(required=True)
    dr_grp.add_argument("--dry-run", action="store_true", help="只列出會刪除什麼,不動手")
    dr_grp.add_argument("--confirm", action="store_true", help="實際刪除,不可復原")
    dr.add_argument("--root", default=None)
    dr.set_defaults(fn=cmd_data_reset)

    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
