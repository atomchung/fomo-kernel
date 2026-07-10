#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_card.py / check_state.py 的離線驗活(#60 最小驗收 harness 的確定性核心)。

eval-design.md §6:斷言本身也可能是「死的」(不會因為卡真的踩雷就亮紅)。這支證明
兩支 checker 是活的——**乾淨輸入全過、刻意壞掉的輸入必掛對應條**,無網路、確定性,
所以進得了 tests/run_all.py(headless 產卡那段非確定性 + 有成本,不進 CI,見 §7)。

分工:
  check_card  對三張 judge fixture 跑機檢 + 逐條 micro 驗活(每條各一 trip / 一 clean)
  check_state 用 coach.py【真實寫入】當 known-good oracle(§6)+ 手工壞檔驗紅 + 差分/append

跑法:python3 tests/test_checkers_offline.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "tests" / "agent"
FIXTURES = AGENT_DIR / "fixtures"
COACH = str(ROOT / "skills" / "fomo-kernel" / "engine" / "coach.py")

sys.path.insert(0, str(AGENT_DIR))
from check_card import check_card                       # noqa: E402
from check_state import check_state, differential, append_only  # noqa: E402

_fails = []


def ok(cond, msg, extra=""):
    print(("✅" if cond else "❌") + f" {msg}" + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        _fails.append(msg)


def _card_fail_ids(text):
    return {f.assertion for f in check_card(text) if not f.passed}


# ─────────────────────── check_card ───────────────────────

def test_card_fixtures():
    """三張 judge fixture:乾淨卡全過;兩張壞卡各自踩到預期集合(⊆,容忍附帶違規)。"""
    good = (FIXTURES / "card_good.txt").read_text(encoding="utf-8")
    ok(_card_fail_ids(good) == set(),
       "card_good 過全部卡面鐵律(修全形後成真乾淨參照)", str(_card_fail_ids(good)))

    dash = (FIXTURES / "card_bad_dashboard.txt").read_text(encoding="utf-8")
    ok({"A-2", "A-3", "A-12", "B-7"} <= _card_fail_ids(dash),
       "card_bad_dashboard 踩 A-2/A-3/A-12/B-7(dashboard 化的機檢子集)", str(_card_fail_ids(dash)))

    vague = (FIXTURES / "card_bad_vague.txt").read_text(encoding="utf-8")
    ok({"B-7", "B-9"} <= _card_fail_ids(vague),
       "card_bad_vague 踩 B-7/B-9(空泛黑名單 + 無具體數字)", str(_card_fail_ids(vague)))


def test_card_each_assertion_alive():
    """逐條:一個會踩、一個乾淨——證明每條斷言活著且不誤判(eval-design §6)。"""
    cases = [
        ("A-2", "部位 sizing: 0.71 🔴", "部位押得有點重"),
        ("A-3", "〔這次成績〕還可以", "這次成績還可以"),
        ("A-6", "勝率 62% 是你的主數字", "盈虧比 0.24 是你的主數字"),
        ("A-12", "你的 max_pos_pct 到 31%", "你的最大單注到 31%"),
        ("A-13", "想分散,結果沒有", "想分散，結果沒有"),
        ("B-7", "下次規矩:控制風險", "下次規矩:INTC 虧損不再加碼"),
        ("B-9", "紀律不佳、風險偏高、需要注意", "INTC 這半年虧 $1,240"),
    ]
    for aid, bad, clean in cases:
        ok(aid in _card_fail_ids(bad), f"{aid} 抓得到違規案例", bad)
        ok(aid not in _card_fail_ids(clean), f"{aid} 乾淨案例不誤判", clean)


# ─────────────────────── check_state ───────────────────────

_MIN_STATE = {
    "date_end": "2026-07-01", "headline_dim": "avgdown",
    "commitment": None, "insufficient_data": False,
    "metrics": {"max_pos_pct": 0.31, "avgdown_count": 4, "ai_pct": 0.55},
}
_THESIS = [{"ticker": "INTC", "cycle_id": "INTC#2026-01-05#1", "why": "w", "maturity": "inferred"}]


def _write_state_via_coach(home, rule, metric):
    """用 coach.py 真實寫入一個 ~/.trade-coach(known-good oracle,§6):close + append-theses。"""
    tc = home / ".trade-coach"
    tc.mkdir(parents=True, exist_ok=True)
    (tc / "last_state.json").write_text(json.dumps(_MIN_STATE), encoding="utf-8")
    env = dict(os.environ, HOME=str(home))
    tj = home / "theses.json"
    tj.write_text(json.dumps(_THESIS), encoding="utf-8")
    r1 = subprocess.run([sys.executable, COACH, "close", "--rule", rule, "--metric", metric],
                        env=env, capture_output=True, text=True, timeout=60)
    r2 = subprocess.run([sys.executable, COACH, "append-theses", str(tj),
                         "--session-date", _MIN_STATE["date_end"]],
                        env=env, capture_output=True, text=True, timeout=60)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    return tc


def test_state_oracle_good():
    """coach 真實寫出的狀態 = known-good:check_state 必須全過(否則 checker 比 writer 還嚴,是死斷言)。"""
    with tempfile.TemporaryDirectory() as tmp:
        tc = _write_state_via_coach(pathlib.Path(tmp), "單筆上限 20%", "max_pos_pct")
        bad = [f for f in check_state(tc) if not f.passed]
        ok(not bad, "coach 真實輸出過 check_state 全部條(known-good oracle)",
           ", ".join(f.assertion for f in bad))


def test_state_each_assertion_alive():
    """手工弄壞 known-good 的各面向,對應條必紅(§6 known-bad)。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = _write_state_via_coach(pathlib.Path(tmp), "單筆上限 20%", "max_pos_pct")

        # S-4 缺 theses.jsonl → 收尾跳過
        d4 = pathlib.Path(tmp) / "s4"
        d4.mkdir()
        (d4 / "log.jsonl").write_text((base / "log.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        ok("S-4" in {f.assertion for f in check_state(d4) if not f.passed},
           "S-4 抓到收尾跳過(theses.jsonl 沒建)")

        # S-1 log.jsonl 有壞行
        d1 = pathlib.Path(tmp) / "s1"
        d1.mkdir()
        (d1 / "log.jsonl").write_text('{"date_end":"x"}\n這不是 JSON\n', encoding="utf-8")
        (d1 / "theses.jsonl").write_text((base / "theses.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        ok("S-1" in {f.assertion for f in check_state(d1) if not f.passed},
           "S-1 抓到非 JSON 壞行")

        # S-2 log 缺欄
        d2 = pathlib.Path(tmp) / "s2"
        d2.mkdir()
        (d2 / "log.jsonl").write_text('{"date_end":"2026-07-01","commitment":null}\n', encoding="utf-8")
        (d2 / "theses.jsonl").write_text((base / "theses.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        ok("S-2" in {f.assertion for f in check_state(d2) if not f.passed},
           "S-2 抓到 log 缺 headline_dim/metrics_snapshot")

        # S-3 theses 缺 cycle_id
        d3 = pathlib.Path(tmp) / "s3"
        d3.mkdir()
        (d3 / "log.jsonl").write_text((base / "log.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        (d3 / "theses.jsonl").write_text('{"ticker":"INTC","maturity":"inferred"}\n', encoding="utf-8")
        ok("S-3" in {f.assertion for f in check_state(d3) if not f.passed},
           "S-3 抓到 theses 缺 cycle_id")


def test_state_differential_and_append():
    """B-3 差分(換答案→commitment.metric_key 不同)+ A-7 append-only,各驗一 pass 一 fail。"""
    with tempfile.TemporaryDirectory() as tmp:
        a = _write_state_via_coach(pathlib.Path(tmp) / "a", "單筆上限 20%", "max_pos_pct")
        b = _write_state_via_coach(pathlib.Path(tmp) / "b", "AI 曝險封頂 40%", "ai_pct")
        la, lb = a / "log.jsonl", b / "log.jsonl"
        ok(differential(la, lb).passed,
           "B-3 兩種答案 → commitment.metric_key 不同(Step 2 不是儀式)")
        ok(not differential(la, la).passed,
           "B-3 同一份 log 對自己 → 不算差分(斷言活著,不會永遠綠)")

    ok(append_only(3, 5).passed, "A-7 行數增(3→5)判 append-only 通過")
    ok(not append_only(5, 3).passed, "A-7 行數縮(5→3)判 append-only 失敗(斷言活著)")


def main():
    test_card_fixtures()
    test_card_each_assertion_alive()
    test_state_oracle_good()
    test_state_each_assertion_alive()
    test_state_differential_and_append()
    print()
    if _fails:
        print(f"❌ {len(_fails)} 條驗活失敗 —— checker 或 fixture 有問題,先修再用。")
        return 1
    print("✅ check_card / check_state 全部驗活通過(斷言證明是活的)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
