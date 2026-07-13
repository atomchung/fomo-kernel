#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
problems.py — 問題帳(#137 三層架構的統計層):持續記錄發生過的問題,定期統計次數/金額/趨勢。

本體是「問題事件」,不是規矩:規矩只是你對某個問題的 opt-in 回應(綁 problem_key),
卡面只挑最嚴重的 top 1–3 講。沒有「畢業」終態——紀律是血壓管理不是考駕照,
看的永遠是「最近控制得好不好」(趨勢),不是「治好了沒」(狀態機)。

資料流(全本機,append-only,同 ledger/revisit 慣例):
  engine state 的 problem_events / problem_opportunities(trade_recap 規約)
    + SKILL 層動機類事件(exit_anxiety / horizon_break / fomo_entry)
    ──append──▶ ~/.trade-coach/problems.jsonl
  行兩種:
    {type:"event", key, kind: behavior|state, week, ticker?, amount?, note?}
    {type:"review_mark", week, opportunities: {key: bool}}   # 每次 review 一筆,定義「期」的邊界
  規矩庫(SKILL 寫,本模組只讀):~/.trade-coach/rules.jsonl
    {rule_id, text, problem_key, status: tracking|muted, source, created, revises?}

判讀鐵律:
  - Opportunity Check:無事件+該期有機會犯才算「守住」;沒機會=skipped 不累計(零事件偏差)。
  - 排序金額傷害優先(產品鐵律「看金額不看筆數」),頻率次之;惡化加權 1.5x、收斂 0.6x
    ——注意這是「加權後金額」優先:小額但惡化中的,**刻意**可以排到大額但持平的前面
    (惡化要先看見);正在變好的降權(別繼續轟),但仍在統計裡(呈現層給正向回饋)。
  - held_streak 供呈現層做注意力調度(連 N 次守住 → 退出卡面,再犯自動回來);
    它不是終態判定,判錯代價只是少提一週,所以門檻可以放心用低值。

已知限制(dogfood 後再調,別提前優化):
  - 統計窗是日曆 4 週,假設 weekly review 節奏(產品設計);不定期 review 時 state 型
    事件數會摻入 review 頻率,trend 解讀要打折。
  - append 走 load→dedupe→append,重跑安全但**非併發安全**(同 ledger/revisit 慣例:
    單用戶本機工具,SKILL 序列執行)。

CLI(JSON stdout / 訊息 stderr,同 ledger 慣例):
  python3 problems.py append EVENTS.json [--mark MARK.json] [--session-id S] [--book P]
      # 去重,重跑安全。#166:同 week 的 mark 內容不同時 fail closed(不再靜默丟棄衝突)。
  python3 problems.py stats [--today D] [--rules R] [--book P] [--recent-weeks 4]
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger as lg  # noqa: E402  # 共用 append_events / 壞行語意

DEFAULT_BOOK = os.path.expanduser("~/.trade-coach/problems.jsonl")
TREND_MULT = {"worse": 1.5, "flat": 1.0, "better": 0.6}


# ─────────────────────────── 讀寫 ───────────────────────────

def load_book(path):
    """讀 problems.jsonl → (events[], marks[], skipped)。壞行跳過計數,不 crash。"""
    events, marks, skipped = [], [], 0
    if not os.path.exists(path):
        return events, marks, skipped
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(ev, dict):
                skipped += 1
                continue
            if ev.get("type") == "event" and ev.get("key") and ev.get("week"):
                events.append(ev)
            elif ev.get("type") == "review_mark" and ev.get("week"):
                marks.append(ev)
            else:
                skipped += 1
    return events, marks, skipped


def _event_id(e):
    # 去重 key 含 amount/note:同日同 ticker 的多筆真實行為(早盤+尾盤各攤一次,px 不同
    # → note 不同)不可誤殺(review);engine 規約是確定性的,同 CSV 重跑 → 同 tuple → 仍去重。
    return (e.get("key"), e.get("week"), e.get("ticker"), e.get("amount"), e.get("note"))


def append_book(book_path, new_events, mark=None, session_id=None):
    """事件以 (key, week, ticker) 去重、mark 以 week 去重 → 重跑/重疊匯入安全。回 (n_ev, n_mark)。

    session_id(#166,選填):非去重鍵(_event_id 不讀這個欄位),只附加在新寫入的 event/mark
    row 供事後追蹤。同 week 已有 mark、且 opportunities 內容跟這次不同 → 拋 ValueError(不再
    靜默丟棄衝突內容);內容完全相同的重跑維持既有 no-op 行為(不報錯,呼叫端不用特別處理)。
    """
    events, marks, _ = load_book(book_path)
    seen = {_event_id(e) for e in events}
    marks_by_week = {m.get("week"): m for m in marks}
    out = []
    for e in new_events or []:
        row = dict(e)
        row["type"] = "event"
        if session_id:
            row["session_id"] = session_id
        if _event_id(row) in seen:
            continue
        seen.add(_event_id(row))
        out.append(row)
    n_mark = 0
    if mark and mark.get("week"):
        want_opps = mark.get("opportunities") or {}
        existing = marks_by_week.get(mark["week"])
        if existing is None:
            row = {"type": "review_mark", "week": mark["week"], "opportunities": want_opps}
            if session_id:
                row["session_id"] = session_id
            out.append(row)
            n_mark = 1
        elif (existing.get("opportunities") or {}) != want_opps:
            raise ValueError(
                f"review_mark week={mark['week']} 已存在內容不同的紀錄"
                f"(既有 opportunities={existing.get('opportunities')},這次={want_opps})——"
                f"不再靜默丟棄衝突內容(#166)")
        # existing 存在且 opportunities 相同 → 既有行為:no-op,不 append 也不報錯
    if out:
        lg.append_events(book_path, out)
    return len(out) - n_mark, n_mark


# ─────────────────────────── 統計 ───────────────────────────

def compute_stats(events, today, recent_weeks=4):
    """per key:總數 / 近期 vs 前期次數 / 近期金額 / 趨勢 / 排序分。純函式,離線可測。
    trend 比的是次數(worse=近期>前期);排序鍵 = (金額×trend 加權, 次數×加權) 雙級。"""
    span = dt.timedelta(days=recent_weeks * 7)
    t0 = dt.date.fromisoformat(today)
    recent_from, prev_from = t0 - span, t0 - span - span
    per = {}
    for e in events:
        try:
            d = dt.date.fromisoformat(str(e["week"]))
        except ValueError:
            continue
        k = e["key"]
        s = per.setdefault(k, {"total": 0, "recent_count": 0, "prev_count": 0,
                               "recent_amount": 0.0, "total_amount": 0.0,
                               "last_week": None, "recent_events": []})
        s["total"] += 1
        amt = e.get("amount")
        if isinstance(amt, (int, float)):
            s["total_amount"] += amt
        if s["last_week"] is None or e["week"] > s["last_week"]:
            s["last_week"] = e["week"]
        if recent_from < d <= t0:
            s["recent_count"] += 1
            if isinstance(amt, (int, float)):
                s["recent_amount"] += amt
            s["recent_events"].append(e)
        elif prev_from < d <= recent_from:
            s["prev_count"] += 1
    for k, s in per.items():
        s["trend"] = ("worse" if s["recent_count"] > s["prev_count"] else
                      "better" if s["recent_count"] < s["prev_count"] else "flat")
        s["recent_amount"] = round(s["recent_amount"], 2)
        s["total_amount"] = round(s["total_amount"], 2)
        mult = TREND_MULT[s["trend"]]
        s["_sort"] = (-s["recent_amount"] * mult, -s["recent_count"] * mult, k)
    top = [k for k, s in sorted(per.items(), key=lambda kv: kv[1]["_sort"])
           if s["recent_count"] > 0][:3]
    for s in per.values():
        del s["_sort"]
    return per, top


def load_rules(path):
    """rules.jsonl → (active_tracking[], muted[])。append-only:revises 指回舊 rule_id →
    舊的 superseded;每條規矩線取 latest;status muted 不進對位但列出(呈現「靜默統計中」)。"""
    rows, superseded = [], set()
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("rule_id"):
                continue
            if r.get("revises"):
                superseded.add(r["revises"])
            rows.append(r)
    latest = [r for r in rows if r["rule_id"] not in superseded]
    tracking = [r for r in latest if r.get("status", "tracking") == "tracking"]
    muted = [r for r in latest if r.get("status") == "muted"]
    return tracking, muted


def check_rules(tracking, events, marks):
    """逐期對位,verdict 依 Opportunity Check 三分:
      broke   = 該期內有綁定 key 的事件
      held    = 無事件,且該期 mark 標「有機會犯」
      skipped = 無事件,也沒機會(零事件不冒充守住)
    回每條 {rule_id, text, problem_key, verdict(最新期), held_streak(尾端連續 held 期數)}。
    held_streak 給呈現層做注意力調度(連 2 次守住退出卡面),不是畢業判定。
    規矩只從 created 之後的期開始對位(review):初診全期補齊的歷史事件是「你過去犯過」
    的統計事實,但規矩生效前的行為不對規矩計破——否則規矩才立(或剛匯入)就滿版 broke。"""
    marks_sorted = sorted(marks, key=lambda m: m["week"])
    out = []
    for r in tracking:
        k = r.get("problem_key")
        created = r.get("created")
        verdicts = []
        prev_week = None
        for m in marks_sorted:
            w = m["week"]
            if created and w < created:                     # 規矩生效前的期:不對位
                prev_week = w
                continue
            in_period = [e for e in events if e["key"] == k
                         and (prev_week is None or e["week"] > prev_week) and e["week"] <= w
                         and (not created or e["week"] >= created)]
            if in_period:
                verdicts.append("broke")
            elif (m.get("opportunities") or {}).get(k):
                verdicts.append("held")
            else:
                verdicts.append("skipped")
            prev_week = w
        streak = 0
        for v in reversed(verdicts):
            if v == "held":
                streak += 1
            elif v == "broke":
                break
            # skipped 不中斷也不累計(沒機會犯的週,對 streak 是透明的)
        out.append({"rule_id": r["rule_id"], "text": r.get("text"),
                    "problem_key": k, "verdict": verdicts[-1] if verdicts else None,
                    "held_streak": streak})
    return out


# ─────────────────────────── CLI ───────────────────────────

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json_arg(v):
    if os.path.exists(v):
        with open(v, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description="fomo-kernel 問題帳(#137):事件入帳 + 統計/趨勢/規矩對位")
    ap.add_argument("--book", default=DEFAULT_BOOK)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ap = sub.add_parser("append", help="事件入帳(去重,重跑安全)")
    p_ap.add_argument("events", help="事件 JSON 檔或字串(list;engine state 的 problem_events 直接餵)")
    p_ap.add_argument("--mark", default=None, help='review_mark JSON:{"week": date_end, "opportunities": {...}}')
    p_ap.add_argument("--session-id", default=None, help="#166:非去重鍵,附加在新寫入 row 供追蹤")

    p_st = sub.add_parser("stats", help="全量統計 + top 1–3 + 規矩對位(JSON)")
    p_st.add_argument("--today", default=None, help="統計錨點 YYYY-MM-DD(預設今天;SKILL 傳 date_end)")
    p_st.add_argument("--rules", default=None, help="rules.jsonl 路徑(給了才做規矩對位)")
    p_st.add_argument("--recent-weeks", type=int, default=4)

    a = ap.parse_args(argv)

    if a.cmd == "append":
        try:
            events = _load_json_arg(a.events)
            mark = _load_json_arg(a.mark) if a.mark else None
        except ValueError as e:
            print(f"❌ JSON 解析失敗:{e}", file=sys.stderr)
            return 1
        if not isinstance(events, list):
            print("❌ events 必須是 list", file=sys.stderr)
            return 1
        try:
            n_ev, n_mark = append_book(a.book, events, mark, session_id=a.session_id)
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
        print(f"appended {n_ev} event(s), {n_mark} mark(s)", file=sys.stderr)
        _emit({"appended_events": n_ev, "appended_marks": n_mark})
        return 0

    if a.cmd == "stats":
        events, marks, skipped = load_book(a.book)
        if skipped:
            print(f"⚠️  problems.jsonl 有 {skipped} 行壞事件被跳過", file=sys.stderr)
        today = a.today or dt.date.today().isoformat()
        try:
            per, top = compute_stats(events, today, a.recent_weeks)
        except ValueError as e:
            print(f"❌ 日期格式錯:{e}", file=sys.stderr)
            return 1
        rules_check = muted = None
        if a.rules:
            tracking, muted_rules = load_rules(a.rules)
            rules_check = check_rules(tracking, events, marks)
            muted = [{"rule_id": r["rule_id"], "text": r.get("text"),
                      "problem_key": r.get("problem_key")} for r in muted_rules]
        _emit({"as_of": today, "recent_weeks": a.recent_weeks,
               "per_key": per, "top": top,
               "rules_check": rules_check, "muted_rules": muted,
               "events_n": len(events), "marks_n": len(marks), "skipped_lines": skipped})
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
