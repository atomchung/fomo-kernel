#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
問題帳(#137)測試 — 全離線、確定性、免裝 pytest。

蓋什麼:
  A. trade_recap.build_problem_events:行為/狀態兩型口徑、prev_end 增量過濾、
     Opportunity Check 快照。
  B. problems.py:append 去重、統計窗口/趨勢/金額優先排序、規矩 revises/muted、
     broke/held/skipped 三分與 held_streak(skipped 透明)。
  C. CLI roundtrip(stdout 純 JSON)。
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import problems as pb  # noqa: E402
import trade_recap as tr  # noqa: E402


def _d(s):
    return dt.date.fromisoformat(s)


# ─────────────── A. build_problem_events(engine 規約)───────────────

def _dims(size_trig=False, div_trig=False, hold_trig=False):
    return [
        {"dim": "出場紀律", "triggered": False},
        {"dim": "部位 sizing", "triggered": size_trig, "max_ticker": "NVDA", "max_pct": 0.34},
        {"dim": "分散", "triggered": div_trig, "top3": 0.7, "ai_pct": 0.65},
        {"dim": "持有時間", "triggered": hold_trig, "incon_tickers": ["TSLA"]},
        {"dim": "加碼攤平", "triggered": False},
    ]


def test_behavior_events_and_prev_end_filter():
    avg_down = [
        {"ticker": "PLTR", "date": _d("2026-06-01"), "px": 15.0, "avg": 20.0, "weight_then": 0.30},
        {"ticker": "PLTR", "date": _d("2026-06-20"), "px": 13.0, "avg": 19.0, "weight_then": 0.31},
        {"ticker": "MU", "date": _d("2026-06-21"), "px": 90.0, "avg": 95.0, "weight_then": 0.10},
    ]
    rts = [{"ticker": "ARM", "exit": _d("2026-06-22"), "entry": _d("2026-05-01"),
            "qty": 10, "sell_px": 100.0, "buy_px": 80.0, "ret": 0.25, "hold": 52, "fwd": 0.20}]
    ev, opp = tr.build_problem_events(_dims(), rts, avg_down, {}, {}, "2026-06-27", None)
    keys = [(e["key"], e["week"]) for e in ev]
    assert ("avgdown_breach", "2026-06-01") in keys and ("avgdown_breach", "2026-06-20") in keys
    assert ("avgdown_breach", "2026-06-21") not in keys, "weight_then 10% 未破線,不是事件"
    assert ("sell_winner_early", "2026-06-22") in keys
    amt = [e["amount"] for e in ev if e["key"] == "sell_winner_early"][0]
    assert abs(amt - 0.20 * 10 * 100.0) < 1e-6, "放掉的錢 = fwd × 賣出市值"
    # 增量模式:prev_end 之後的才算新事件(初診不給 = 全期補齊)
    ev2, _ = tr.build_problem_events(_dims(), rts, avg_down, {}, {}, "2026-06-27", "2026-06-15")
    keys2 = [(e["key"], e["week"]) for e in ev2]
    assert ("avgdown_breach", "2026-06-01") not in keys2, "prev_end 前的舊事件不重複入帳"
    assert ("avgdown_breach", "2026-06-20") in keys2


def test_state_events_weekly_cadence():
    ev, _ = tr.build_problem_events(_dims(size_trig=True, div_trig=True), [], [], {}, {},
                                    "2026-06-27", "2026-06-20")
    state_ev = {e["key"]: e for e in ev if e["kind"] == "state"}
    assert set(state_ev) == {"oversize", "concentration"}
    assert all(e["week"] == "2026-06-27" for e in state_ev.values()), \
        "狀態型每次 review 一筆,事件日=date_end(每週都在選擇不動它)"


def test_opportunity_check_snapshot():
    held = {"PLTR": (10.0, 200.0), "NVDA": (5.0, 500.0)}      # avg 20 / 100
    ev, opp = tr.build_problem_events(_dims(), [], [], held, {"PLTR": 15.0, "NVDA": 110.0},
                                      "2026-06-27", None)
    assert opp["avgdown_breach"] is True, "有浮虧持倉(PLTR)→ 有機會攤平"
    assert opp["sell_winner_early"] is True, "有獲利持倉(NVDA)→ 有機會賣早"
    _, opp2 = tr.build_problem_events(_dims(), [], [], {"NVDA": (5.0, 500.0)}, {"NVDA": 110.0},
                                      "2026-06-27", None)
    assert opp2["avgdown_breach"] is False, "全部賺錢 → 沒機會攤平 = 該週守住不算數(Skipped)"


# ─────────────── B. problems.py(帳本 + 統計 + 規矩)───────────────

def _mk():
    d = tempfile.mkdtemp()
    return os.path.join(d, "problems.jsonl"), os.path.join(d, "rules.jsonl")


def _ev(key, week, ticker=None, amount=None):
    return {"key": key, "kind": "behavior", "week": week, "ticker": ticker, "amount": amount}


def test_append_dedup_rerun_safe():
    book, _ = _mk()
    evs = [_ev("avgdown_breach", "2026-06-20", "PLTR"), _ev("oversize", "2026-06-27", "NVDA")]
    mark = {"week": "2026-06-27", "opportunities": {"avgdown_breach": True}}
    n1 = pb.append_book(book, evs, mark)
    n2 = pb.append_book(book, evs, mark)                     # 重跑全去重
    assert n1 == (2, 1) and n2 == (0, 0), f"{n1} / {n2}"
    events, marks, skipped = pb.load_book(book)
    assert len(events) == 2 and len(marks) == 1 and skipped == 0


def test_stats_amount_first_and_trend_weight():
    book, _ = _mk()
    # sell_winner_early:近期 1 次但金額大;avgdown:近期 3 次無金額且惡化
    evs = [_ev("sell_winner_early", "2026-07-01", "ARM", 2000.0),
           _ev("avgdown_breach", "2026-06-25", "PLTR"),
           _ev("avgdown_breach", "2026-06-28", "PLTR"),
           _ev("avgdown_breach", "2026-07-03", "MSTR"),
           _ev("avgdown_breach", "2026-05-20", "PLTR")]      # 前期 1 次 → 近期 3 次 = worse
    pb.append_book(book, evs, None)
    events, _, _ = pb.load_book(book)
    per, top = pb.compute_stats(events, "2026-07-07", 4)
    assert per["avgdown_breach"]["trend"] == "worse"
    assert per["avgdown_breach"]["recent_count"] == 3 and per["avgdown_breach"]["prev_count"] == 1
    assert top[0] == "sell_winner_early", "金額傷害優先(產品鐵律:看金額不看筆數)"
    assert top[1] == "avgdown_breach"
    per2, top2 = pb.compute_stats(events, "2026-09-30", 4)   # 兩個月後:全部退出近期窗
    assert top2 == [], "近期無事件的 key 不進 top(靜默統計中)"
    assert per2["avgdown_breach"]["total"] == 4, "總帳仍在"


def test_rules_revises_and_muted():
    _, rules = _mk()
    rows = [
        {"rule_id": "r1", "text": "虧損不加碼", "problem_key": "avgdown_breach", "status": "tracking"},
        {"rule_id": "r2", "text": "單注 25% 上限", "problem_key": "oversize", "status": "muted"},
        {"rule_id": "r3", "text": "虧損加碼前隔一天", "problem_key": "avgdown_breach",
         "status": "tracking", "revises": "r1"},
    ]
    with open(rules, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tracking, muted = pb.load_rules(rules)
    assert [r["rule_id"] for r in tracking] == ["r3"], "revises 演變線:舊版 superseded,取 latest"
    assert [r["rule_id"] for r in muted] == ["r2"], "muted 不進對位但列出(靜默統計中)"


def test_check_rules_three_way_and_streak():
    events = [{"type": "event", "key": "avgdown_breach", "week": "2026-06-10", "ticker": "PLTR"}]
    marks = [{"week": "2026-06-13", "opportunities": {"avgdown_breach": True}},   # 期1:有事件 → broke
             {"week": "2026-06-20", "opportunities": {"avgdown_breach": True}},   # 期2:無事件+有機會 → held
             {"week": "2026-06-27", "opportunities": {"avgdown_breach": False}},  # 期3:沒機會 → skipped
             {"week": "2026-07-04", "opportunities": {"avgdown_breach": True}}]   # 期4:held
    tracking = [{"rule_id": "r1", "text": "虧損不加碼", "problem_key": "avgdown_breach"}]
    out = pb.check_rules(tracking, events, marks)
    assert out[0]["verdict"] == "held"
    assert out[0]["held_streak"] == 2, "skipped 透明(不中斷不累計):held×2,不是 3 也不是 1"
    marks2 = marks + [{"week": "2026-07-11", "opportunities": {"avgdown_breach": True}}]
    events2 = events + [{"type": "event", "key": "avgdown_breach", "week": "2026-07-08"}]
    out2 = pb.check_rules(tracking, events2, marks2)
    assert out2[0]["verdict"] == "broke" and out2[0]["held_streak"] == 0, "破戒中斷 streak"


# ─────────────── C. CLI roundtrip ───────────────

def test_cli_roundtrip():
    book, rules = _mk()
    d = os.path.dirname(book)
    ev_f = os.path.join(d, "ev.json")
    with open(ev_f, "w", encoding="utf-8") as f:
        json.dump([_ev("oversize", "2026-06-27", "NVDA")], f)
    with open(rules, "w", encoding="utf-8") as f:
        f.write(json.dumps({"rule_id": "r1", "text": "單注 25% 上限",
                            "problem_key": "oversize", "status": "tracking"}) + "\n")
    ex = os.path.join(ENGINE, "problems.py")
    r1 = subprocess.run([sys.executable, ex, "--book", book, "append", ev_f,
                         "--mark", '{"week": "2026-06-27", "opportunities": {"oversize": true}}'],
                        capture_output=True, text=True)
    out1 = json.loads(r1.stdout)                             # stdout 必須純 JSON
    assert r1.returncode == 0 and out1 == {"appended_events": 1, "appended_marks": 1}, r1.stderr
    r2 = subprocess.run([sys.executable, ex, "--book", book, "stats",
                         "--today", "2026-07-07", "--rules", rules],
                        capture_output=True, text=True)
    out2 = json.loads(r2.stdout)
    assert out2["top"] == ["oversize"] and out2["per_key"]["oversize"]["recent_count"] == 1
    assert out2["rules_check"][0]["verdict"] == "broke", "本期有事件 → 該規矩破"
    bad = subprocess.run([sys.executable, ex, "--book", book, "append", "not-json"],
                         capture_output=True, text=True)
    assert bad.returncode == 1, "壞 JSON 要報錯,不吞"


# ─────────────────────────── runner ───────────────────────────

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _main() else 0)
