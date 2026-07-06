#!/usr/bin/env python3
"""
出場 30/60/90 追蹤 + swap 機會成本的行為契約(#32/#33;#129 PR-3,設計 docs/prd-ledger.md §4)。

跟其他測試檔的分工:
- test_ledger.py → 帳本事實層(雙輸入推導/reconcile)。
- 本檔 → engine/revisit.py:出場偵測(錨點語意)、佇列 append-only 去重、
  due 排程(30 未答不跳 60)、swap framing 對比數學、缺價誠實。

斷言先對真實輸出探測過(2026-07-06)再鎖定。鎖住的核心契約:
  1. 出場偵測 = 清倉(full)或單筆賣 ≥50% 賣前持倉(reduce);小額賣不觸發。
  2. 錨點語意與 ledger 一致:最近 snapshot 之前的交易不進出場偵測。
  3. swap 配對 = 賣後 14 天內、不同 ticker 的買入;無 → idle_cash。
  4. swap framing(#33):swap_net = 換入加權報酬 − 原標的繼續持有報酬;缺價 → None + needs_prices。
  5. resolution append-only:答過的 checkpoint 不再 due;30 未答不跳 60。

跑法:
  python3 tests/test_revisit.py
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import ledger as lg  # noqa: E402
import revisit as rv  # noqa: E402


def _tr(date, ticker, action, qty, price):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price}


def _snap(as_of, positions):
    return {"type": "snapshot", "as_of": as_of, "source": "user_declared",
            "positions": positions}


def _approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ─────────────── A. 出場偵測 ───────────────

def test_detect_full_and_reduce_exits():
    events = [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),    # 清倉 → full
        _tr("2026-05-01", "MU", "buy", 20, 100.0),
        _tr("2026-06-10", "MU", "sell", 11, 110.0),      # 55% → reduce
        _tr("2026-05-01", "AAPL", "buy", 100, 200.0),
        _tr("2026-06-01", "AAPL", "sell", 10, 210.0),    # 10% → 不觸發
    ]
    exits = rv.detect_exits(events)
    kinds = {x["ticker"]: x["kind"] for x in exits}
    assert kinds == {"NVDA": "full", "MU": "reduce"}, f"出場偵測錯:{kinds}"
    nvda = next(x for x in exits if x["ticker"] == "NVDA")
    assert nvda["cycle_id"] == "NVDA#2026-05-01#1" and _approx(nvda["exit_price"], 120.5)


def test_detect_respects_snapshot_anchor():
    """錨點之前的清倉是歷史,不進 revisit(與 ledger 推導同語意);錨點持倉之後清倉才算。"""
    events = [
        _tr("2026-04-01", "OLD", "buy", 10, 50.0),
        _tr("2026-04-20", "OLD", "sell", 10, 55.0),      # 錨點前 → 不算
        _snap("2026-05-01", [{"ticker": "NVDA", "shares": 10, "avg_cost": 120.0}]),
        _tr("2026-06-15", "NVDA", "sell", 10, 150.0),    # 錨點持倉清倉 → 算
    ]
    exits = rv.detect_exits(events)
    assert [x["ticker"] for x in exits] == ["NVDA"], f"錨點語意破了:{exits}"
    assert exits[0]["cycle_id"] == "NVDA#2026-05-01#1", "錨點持倉 cycle 以 as_of 起算"


def test_oversell_not_enqueued():
    exits = rv.detect_exits([_tr("2026-06-01", "MSTR", "sell", 5, 300.0)])
    assert exits == [], "無倉賣(ledger integrity 已記)不進 revisit"


# ─────────────── B. swap 配對 ───────────────

def test_infer_swaps_window_and_idle():
    events = [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),
        _tr("2026-06-15", "AVGO", "buy", 3, 300.0),      # 同日換股(賣早買午)→ 必須配(review 2026-07-06)
        _tr("2026-06-20", "ORCL", "buy", 5, 180.0),      # 窗內(5 天)→ 配
        _tr("2026-07-10", "META", "buy", 2, 700.0),      # 窗外(25 天)→ 不配
        _tr("2026-06-16", "NVDA", "buy", 1, 118.0),      # 同 ticker → 不配(那是回補不是 swap)
    ]
    x = {"ticker": "NVDA", "exit_date": "2026-06-15"}
    swaps = rv.infer_swaps(events, x)
    assert sorted(s["ticker"] for s in swaps) == ["AVGO", "ORCL"], f"swap 窗/同檔/同日規則錯:{swaps}"
    lonely = rv.infer_swaps([_tr("2026-06-15", "NVDA", "sell", 10, 120.5)],
                            {"ticker": "NVDA", "exit_date": "2026-06-15"})
    assert lonely == [], "無買入 → 之後 enqueue 標 idle_cash"


# ─────────────── C. 佇列:去重 + due 排程 + resolution ───────────────

def _mk_paths():
    d = tempfile.mkdtemp()
    return os.path.join(d, "ledger.jsonl"), os.path.join(d, "revisit.jsonl")


def test_enqueue_dedup_and_due_schedule():
    led, q = _mk_paths()
    lg.append_events(led, [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),
    ])
    n1, _ = rv.enqueue_from_ledger(led, q)
    n2, _ = rv.enqueue_from_ledger(led, q)               # 重跑 → 全去重
    assert (n1, n2) == (1, 0), f"enqueue 去重錯:{(n1, n2)}"
    revisits, _, _ = rv.load_queue(q)
    item = list(revisits.values())[0]
    assert item["due"] == {"30": "2026-07-15", "60": "2026-08-14", "90": "2026-09-13"}
    assert item["idle_cash"] is True


def test_scan_due_progression_and_resolution():
    """30 到期未答 → 只出 30(不跳 60);答完 30 → 60 到期才出 60;全答 → 不再出。"""
    led, q = _mk_paths()
    lg.append_events(led, [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),
    ])
    rv.enqueue_from_ledger(led, q)
    revisits, resolutions, _ = rv.load_queue(q)
    rid = list(revisits)[0]
    # 90 天後才 scan,但 30 還沒答 → 只出 30
    due = rv.scan_due(revisits, resolutions, dt.date(2026, 9, 20))
    assert [(d["revisit_id"], d["checkpoint"]) for d in due] == [(rid, "30")]
    lg.append_events(q, [{"type": "resolution", "revisit_id": rid, "checkpoint": "30",
                          "status": "still_valid", "date": "2026-09-20"}])
    revisits, resolutions, _ = rv.load_queue(q)
    due = rv.scan_due(revisits, resolutions, dt.date(2026, 9, 20))
    assert [d["checkpoint"] for d in due] == ["60"], "答完 30 → 下一關 60"
    for cp in ("60", "90"):
        lg.append_events(q, [{"type": "resolution", "revisit_id": rid, "checkpoint": cp,
                              "status": "modified", "date": "2026-09-21"}])
    revisits, resolutions, _ = rv.load_queue(q)
    assert rv.scan_due(revisits, resolutions, dt.date(2027, 1, 1)) == [], "三關全答 → 靜默"
    # 未到期 → 不催(zero-event 誠實)
    assert rv.scan_due(revisits, {}, dt.date(2026, 7, 1)) == []


# ─────────────── D. swap framing 對比數學(#33 核心)───────────────

def test_compare_swap_framing_math():
    """賣飛 +32.78% 但換入 −5% → swap_net −37.78pp = 真正的決策錯誤(探測值鎖定)。"""
    item = {"ticker": "NVDA", "exit_price": 120.5, "idle_cash": False,
            "swaps": [{"ticker": "ORCL", "price": 180.0, "qty": 5}]}
    c = rv.compare(item, {"NVDA": 160.0, "ORCL": 171.0})
    assert _approx(c["orig_ret"], 160.0 / 120.5 - 1, 1e-6)
    assert _approx(c["swap_ret"], -0.05, 1e-9)
    assert _approx(c["swap_net_pp"], c["swap_ret"] - c["orig_ret"], 1e-9)
    assert c["needs_prices"] == []


def test_compare_weighted_multi_swap():
    """分批換多標的 → 金額加權(#33 情境三):1800 元 +10%、200 元 −10% → 加權 +8%。"""
    item = {"ticker": "A", "exit_price": 100.0, "idle_cash": False,
            "swaps": [{"ticker": "B", "price": 180.0, "qty": 10},    # 1800
                      {"ticker": "C", "price": 100.0, "qty": 2}]}    # 200
    c = rv.compare(item, {"A": 100.0, "B": 198.0, "C": 90.0})
    assert _approx(c["swap_ret"], (1800 * 0.10 + 200 * (-0.10)) / 2000, 1e-9)


def test_compare_missing_price_honest_none():
    item = {"ticker": "NVDA", "exit_price": 120.5, "idle_cash": False,
            "swaps": [{"ticker": "ORCL", "price": 180.0, "qty": 5}]}
    c = rv.compare(item, {"ORCL": 171.0})                # 缺 NVDA 現價
    assert c["orig_ret"] is None and c["swap_net_pp"] is None
    assert c["needs_prices"] == ["NVDA"], "缺什麼列什麼,不硬編"
    c2 = rv.compare(item, {"NVDA": 160.0})               # 缺 swap 價
    assert c2["swap_ret"] is None and c2["needs_prices"] == ["ORCL"]


def test_compare_idle_cash():
    item = {"ticker": "NVDA", "exit_price": 120.5, "idle_cash": True, "swaps": []}
    c = rv.compare(item, {"NVDA": 160.0})
    assert c["idle_cash"] is True and c["swap_ret"] is None
    assert _approx(c["orig_ret"], 160.0 / 120.5 - 1), "閒置 → 機會成本 = 原標的繼續持有報酬"


# ─────────────── E. CLI 介面(SKILL 消費形狀)───────────────

def test_cli_roundtrip():
    led, q = _mk_paths()
    lg.append_events(led, [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),
    ])
    ex = os.path.join(ENGINE, "revisit.py")
    r1 = subprocess.run([sys.executable, ex, "--queue", q, "enqueue-from-ledger",
                         "--ledger", led], capture_output=True, text=True)
    assert r1.returncode == 0 and json.loads(r1.stdout)["enqueued"] == 1, r1.stderr
    r2 = subprocess.run([sys.executable, ex, "--queue", q, "scan", "--today", "2026-07-16",
                         "--prices", '{"NVDA": 160.0}'], capture_output=True, text=True)
    out = json.loads(r2.stdout)                          # stdout 必須純 JSON
    assert len(out["due"]) == 1 and out["due"][0]["checkpoint"] == "30"
    assert _approx(out["due"][0]["compare"]["orig_ret"], 160.0 / 120.5 - 1, 1e-6)
    rid = out["due"][0]["revisit_id"]
    r3 = subprocess.run([sys.executable, ex, "--queue", q, "resolve", rid, "30",
                         "falsified", "--note", "panic sell", "--date", "2026-07-16"],
                        capture_output=True, text=True)
    assert r3.returncode == 0, r3.stderr
    r4 = subprocess.run([sys.executable, ex, "--queue", q, "scan", "--today", "2026-07-16"],
                        capture_output=True, text=True)
    assert json.loads(r4.stdout)["due"] == [], "答完 30、60 未到 → 靜默"
    bad = subprocess.run([sys.executable, ex, "--queue", q, "resolve", "GHOST#x#1", "30",
                          "modified"], capture_output=True, text=True)
    assert bad.returncode == 1, "不存在的 revisit_id 要報錯,不靜默寫入"


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
