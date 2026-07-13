#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coach.py / problems.py 收尾 session idempotency 測試(#166)— 全離線、確定性、免裝 pytest。

逐條對應 issue #166 的 8 條驗收標準。本路線(最小延伸,#166 issue comment 拍板)刻意不做
跨指令 all-or-nothing——第 3/6 點的測試明確標註「誠實記錄的落差」,不是測試沒寫全:close
成功但後續指令失敗時,已寫入的部分不會被回滾,這是相對統一 bundle 路線刻意接受的取捨
(見該 issue 討論與 PR 說明)。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "skills", "fomo-kernel", "engine")
COACH = os.path.join(ENGINE, "coach.py")
PROBLEMS = os.path.join(ENGINE, "problems.py")
sys.path.insert(0, ENGINE)
import ledger as lg  # noqa: E402  # 算 session_id 用,同 SKILL.md heredoc 慣例


def _run(script, *args):
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True)


def _write_state(path, **overrides):
    st = {"date_end": "2026-07-13", "headline_dim": "concentration",
          "insufficient_data": False, "commitment": None,
          "metrics": {"max_pos_pct": 0.42, "ai_pct": 0.6}}
    st.update(overrides)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    return st


def _paths(tmp):
    return {"state": os.path.join(tmp, "state.json"), "log": os.path.join(tmp, "log.jsonl"),
            "theses": os.path.join(tmp, "theses.jsonl"), "rules": os.path.join(tmp, "rules.jsonl"),
            "cards": os.path.join(tmp, "cards"), "problems": os.path.join(tmp, "problems.jsonl")}


def _theses_payload(tmp, why="w"):
    path = os.path.join(tmp, "theses.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"ticker": "NVDA", "cycle_id": "NVDA#2024-01-12#1", "why": why,
                    "maturity": "inferred"}], f)
    return path


def _rules_payload(tmp, text="AI 暴險封頂 70%"):
    path = os.path.join(tmp, "rules.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"text": text, "metric_key": "ai_pct", "source": "user_chosen"}], f)
    return path


def _card_payload(tmp, body="卡片內容"):
    path = os.path.join(tmp, "card.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\ndate: 2026-07-13\n---\n{body}")
    return path


def _events_payload(tmp):
    path = os.path.join(tmp, "events.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"key": "avgdown_breach", "kind": "state", "week": "2026-07-13",
                    "ticker": "PLTR", "amount": None, "note": None}], f)
    return path


def _run_all_five(tmp, p, state, rule="測試規矩", nonce=None):
    """跑一次完整收尾五動作(close/append-theses/append-rules/save-card/problems append),
    回傳各步驟 subprocess 結果的 dict。"""
    n = ["--session-nonce", nonce] if nonce else []
    r = {}
    r["close"] = _run(COACH, "close", "--rule", rule, "--metric", "max_pos_pct",
                       "--state", p["state"], "--log", p["log"], *n)
    r["theses"] = _run(COACH, "append-theses", _theses_payload(tmp),
                        "--session-date", state["date_end"],
                        "--theses", p["theses"], "--state", p["state"], *n)
    r["rules"] = _run(COACH, "append-rules", _rules_payload(tmp), "--created", state["date_end"],
                       "--rules", p["rules"], "--state", p["state"], *n)
    r["card"] = _run(COACH, "save-card", _card_payload(tmp), "--date", state["date_end"],
                      "--cards-dir", p["cards"], "--state", p["state"], *n)
    sid = lg.session_id_from_state(state, nonce or "")
    mark = json.dumps({"week": state["date_end"], "opportunities": {"avgdown_breach": True}})
    r["problems"] = _run(PROBLEMS, "--book", p["problems"], "append", _events_payload(tmp),
                          "--mark", mark, "--session-id", sid)
    return r


def _snapshot_outputs(p):
    """只快照『真正的收尾輸出』(log/theses/rules/cards/problems),不含測試自己寫的暫存
    payload 檔案——那些檔案本來就會被每次呼叫刻意改寫成不同內容(用來製造衝突情境),
    跟「磁碟上的收尾成果有沒有被動到」是兩回事,混在一起快照會產生假陽性。"""
    out = {}
    for key in ("log", "theses", "rules", "problems"):
        path = p[key]
        out[key] = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if os.path.isdir(p["cards"]):
        out["cards"] = {name: open(os.path.join(p["cards"], name), encoding="utf-8").read()
                        for name in sorted(os.listdir(p["cards"]))}
    else:
        out["cards"] = {}
    return out


# ─────────────── 驗收標準 1:同一 session finalize 兩次 → 只存在一個 session ───────────────

def test_1_rerun_same_session_no_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        st = _write_state(p["state"])
        r1 = _run_all_five(tmp, p, st)
        assert all(x.returncode == 0 for x in r1.values()), {k: v.stderr for k, v in r1.items()}
        r2 = _run_all_five(tmp, p, st)             # 完全重跑一次:同 state、同 payload
        assert all(x.returncode == 0 for x in r2.values()), {k: v.stderr for k, v in r2.items()}
        with open(p["log"], encoding="utf-8") as f:
            assert len(f.readlines()) == 1, "log.jsonl 重跑後仍只 1 行"
        with open(p["theses"], encoding="utf-8") as f:
            assert len(f.readlines()) == 1, "theses.jsonl 重跑後仍只 1 行"
        with open(p["rules"], encoding="utf-8") as f:
            assert len(f.readlines()) == 1, "rules.jsonl 重跑後仍只 1 行"
        assert len(os.listdir(p["cards"])) == 1, "cards/ 重跑後仍只 1 個檔案"
        with open(p["problems"], encoding="utf-8") as f:
            assert len(f.readlines()) == 2, "problems.jsonl 重跑後仍是 1 事件+1 mark = 2 行"


# ─────────────── 驗收標準 2:同 session id、不同 payload → fail closed ───────────────

def test_2_conflicting_retry_fails_closed_and_leaves_files_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        st = _write_state(p["state"])
        r1 = _run_all_five(tmp, p, st)
        assert all(x.returncode == 0 for x in r1.values())
        before = _snapshot_outputs(p)

        r_close = _run(COACH, "close", "--rule", "不同規矩", "--metric", "max_pos_pct",
                        "--state", p["state"], "--log", p["log"])
        assert r_close.returncode != 0

        r_theses = _run(COACH, "append-theses", _theses_payload(tmp, why="改過的理由"),
                         "--session-date", st["date_end"],
                         "--theses", p["theses"], "--state", p["state"])
        assert r_theses.returncode != 0

        r_rules = _run(COACH, "append-rules", _rules_payload(tmp, text="不同規矩文字"),
                        "--created", st["date_end"], "--rules", p["rules"], "--state", p["state"])
        assert r_rules.returncode != 0

        r_card = _run(COACH, "save-card", _card_payload(tmp, body="改過的卡片內容"),
                       "--date", st["date_end"], "--cards-dir", p["cards"], "--state", p["state"])
        assert r_card.returncode != 0

        sid = lg.session_id_from_state(st)
        mark_conflict = json.dumps({"week": st["date_end"],
                                    "opportunities": {"avgdown_breach": False}})  # 跟 r1 不同
        r_problems = _run(PROBLEMS, "--book", p["problems"], "append", _events_payload(tmp),
                          "--mark", mark_conflict, "--session-id", sid)
        assert r_problems.returncode != 0

        after = _snapshot_outputs(p)
        assert before == after, "任何一個拒收都不該改動磁碟上任何檔案"

        # 逃生艙口:帶 --session-nonce 明確拆開,應該成功(正面案例)
        r_ok = _run(COACH, "close", "--rule", "不同規矩", "--metric", "max_pos_pct",
                    "--state", p["state"], "--log", p["log"], "--session-nonce", "second-review")
        assert r_ok.returncode == 0, r_ok.stderr
        with open(p["log"], encoding="utf-8") as f:
            assert len(f.readlines()) == 2, "帶 nonce 的正面案例應該新增第 2 行"


# ─────────────── 驗收標準 3:誠實分兩層 ───────────────

def test_3a_single_command_validation_still_all_or_nothing():
    """單一指令內的驗證失敗 = 100% all-or-nothing(今天就已如此,重新確認不退步)。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        st = _write_state(p["state"])
        bad_theses = os.path.join(tmp, "bad.json")
        with open(bad_theses, "w", encoding="utf-8") as f:
            json.dump([{"ticker": "NVDA", "cycle_id": "NVDA#2024-01-12",  # 2 段,格式不合契約
                        "maturity": "inferred"}], f)
        r = _run(COACH, "append-theses", bad_theses, "--session-date", st["date_end"],
                 "--theses", p["theses"], "--state", p["state"])
        assert r.returncode != 0
        assert not os.path.exists(p["theses"]), "驗證失敗時 0 筆落盤"


def test_3b_cross_command_gap_is_documented_limitation():
    """誠實記錄的落差(不是遺漏):close 成功但 append-theses 因格式錯誤被拒時,log.jsonl
    那一行不會被回滾——這是本路線(最小延伸)相對統一 bundle 路線刻意接受的取捨。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        st = _write_state(p["state"])
        r_close = _run(COACH, "close", "--rule", "測試規矩", "--metric", "max_pos_pct",
                       "--state", p["state"], "--log", p["log"])
        assert r_close.returncode == 0
        bad_theses = os.path.join(tmp, "bad.json")
        with open(bad_theses, "w", encoding="utf-8") as f:
            json.dump([{"ticker": "NVDA", "cycle_id": "bad", "maturity": "inferred"}], f)
        r_theses = _run(COACH, "append-theses", bad_theses, "--session-date", st["date_end"],
                        "--theses", p["theses"], "--state", p["state"])
        assert r_theses.returncode != 0
        assert os.path.exists(p["log"]), "誠實揭露的落差:close 已成功的部分不因後續失敗回滾"
        with open(p["log"], encoding="utf-8") as f:
            assert len(f.readlines()) == 1


# ─────────────── 驗收標準 4:failure injection 後重試 = 一次成功 ───────────────

def test_4_partial_then_full_retry_converges_to_golden_reference():
    """模擬「使用者只做了部分收尾就放棄、之後重新從頭跑一次完整五步」——最終狀態應與
    golden reference(一次成功跑完)逐檔案內容相同。這證明的是「收斂到相同終態」,不是
    「過程中不會觀察到中間不一致」——第 3 點已誠實點出這兩者的差距。"""
    with tempfile.TemporaryDirectory() as golden_tmp:
        p_g = _paths(golden_tmp)
        st_g = _write_state(p_g["state"])
        r_golden = _run_all_five(golden_tmp, p_g, st_g)
        assert all(x.returncode == 0 for x in r_golden.values()), \
            {k: v.stderr for k, v in r_golden.items()}
        golden = _snapshot_outputs(p_g)

    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        st = _write_state(p["state"])
        r_partial = _run(COACH, "close", "--rule", "測試規矩", "--metric", "max_pos_pct",
                         "--state", p["state"], "--log", p["log"])
        assert r_partial.returncode == 0                # 模擬中途放棄:只做了第一步
        r_retry = _run_all_five(tmp, p, st)              # 重新從頭跑完整五步
        assert all(x.returncode == 0 for x in r_retry.values()), \
            {k: v.stderr for k, v in r_retry.items()}
        final = _snapshot_outputs(p)
        assert golden == final, "部分收尾後完整重跑,最終狀態應與一次成功完全相同"


# ─────────────── 驗收標準 5:同日兩個不同 session 正確保存與對帳 ───────────────

def test_5_two_different_sessions_same_day_both_saved():
    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        _write_state(p["state"], metrics={"max_pos_pct": 0.42, "ai_pct": 0.6})
        r1 = _run(COACH, "close", "--rule", "規矩A", "--metric", "max_pos_pct",
                  "--state", p["state"], "--log", p["log"])
        assert r1.returncode == 0
        r1_card = _run(COACH, "save-card", _card_payload(tmp, body="卡片A"), "--date", "2026-07-13",
                       "--cards-dir", p["cards"], "--state", p["state"])
        assert r1_card.returncode == 0

        state2_path = os.path.join(tmp, "state2.json")
        _write_state(state2_path, metrics={"max_pos_pct": 0.55, "ai_pct": 0.6})  # 內容不同 → 不同 session
        r2 = _run(COACH, "close", "--rule", "規矩B", "--metric", "max_pos_pct",
                  "--state", state2_path, "--log", p["log"])
        assert r2.returncode == 0
        r2_card = _run(COACH, "save-card", _card_payload(tmp, body="卡片B"), "--date", "2026-07-13",
                       "--cards-dir", p["cards"], "--state", state2_path)
        assert r2_card.returncode == 0

        with open(p["log"], encoding="utf-8") as f:
            rows = [json.loads(x) for x in f.read().splitlines()]
        assert len(rows) == 2 and rows[0]["session_id"] != rows[1]["session_id"]
        assert rows[0]["commitment"]["rule"] == "規矩A" and rows[1]["commitment"]["rule"] == "規矩B"

        cards = sorted(os.listdir(p["cards"]))
        assert cards == ["2026-07-13-2.md", "2026-07-13.md"]


# ─────────────── 驗收標準 6:誠實標註未達成(下一週只看到已 committed session)───────────────

def test_6_incomplete_session_is_still_readable_documented_gap():
    """誠實標註未達成:只跑 close(其餘四步未做),log.jsonl 那一行仍可讀、仍會被下週對帳
    消費——沒有機制擋下它。這是相對統一 bundle 路線(才會做跨檔案 commit gate)刻意不做的
    部分,不在本次範圍內解決。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        _write_state(p["state"])
        r_close = _run(COACH, "close", "--rule", "測試規矩", "--metric", "max_pos_pct",
                       "--state", p["state"], "--log", p["log"])
        assert r_close.returncode == 0
        assert not os.path.exists(p["theses"]), "其餘四步確實沒做(這是本測試的前提)"
        with open(p["log"], encoding="utf-8") as f:
            rows = [json.loads(x) for x in f.read().splitlines()]
        assert len(rows) == 1 and rows[-1]["commitment"]["rule"] == "測試規矩", \
            "下週對帳讀 log 最後一行時,仍會讀到這個『其實沒收尾完整』的 session——已知落差"


# ─────────────── 驗收標準 7:legacy fixture(無 session_id)向後相容 ───────────────

def test_7_legacy_rows_without_session_id_still_readable():
    with tempfile.TemporaryDirectory() as tmp:
        p = _paths(tmp)
        _write_state(p["state"])
        legacy_log = {"date_end": "2026-06-01", "headline_dim": "avg_down",
                      "commitment": {"rule": "舊規矩", "metric_key": "max_pos_pct",
                                    "metric_value": 0.5, "goal": "down", "source": "user_chosen"},
                      "metrics_snapshot": {"max_pos_pct": 0.5}}
        with open(p["log"], "w", encoding="utf-8") as f:
            f.write(json.dumps(legacy_log, ensure_ascii=False) + "\n")

        r = _run(COACH, "close", "--rule", "新規矩", "--metric", "max_pos_pct",
                 "--state", p["state"], "--log", p["log"])
        assert r.returncode == 0, r.stderr
        with open(p["log"], encoding="utf-8") as f:
            rows = [json.loads(x) for x in f.read().splitlines()]
        assert len(rows) == 2
        assert rows[0] == legacy_log, "舊行原封不動(沒有 session_id 欄位也不受影響)"
        assert "session_id" in rows[1] and rows[0].get("session_id") is None


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
