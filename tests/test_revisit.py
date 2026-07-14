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
    new1, dup1 = rv.enqueue_from_ledger(led, q, today=dt.date(2026, 6, 15))
    new2, dup2 = rv.enqueue_from_ledger(led, q, today=dt.date(2026, 6, 15))   # 重跑 → 全去重
    assert (len(new1), len(new2)) == (1, 0), f"enqueue 去重錯:{(new1, new2)}"
    assert (dup1, dup2) == (0, 1), f"dup 計數錯:{(dup1, dup2)}"
    # new = 賣出理由 capture 的訊號源(#136):首跑要帶完整事件,重跑不重報
    assert new1[0]["ticker"] == "NVDA" and new1[0]["revisit_id"], "new 要含完整 revisit 事件"
    revisits, _, _ = rv.load_queue(q)
    item = list(revisits.values())[0]
    assert item["due"] == {"30": "2026-07-15", "60": "2026-08-14", "90": "2026-09-13"}
    assert item["enqueued_at"] == "2026-06-15", "#170:每筆蓋 enqueued_at(= 開始追蹤日)"
    assert item["idle_cash"] is True
    assert item["shares_before"] == 10, "減倉比例要靠 shares_before,capture 問句用"


def test_revisit_id_includes_cycle_id():
    """#143:revisit_id 必須含 cycle_id 段,否則「同 ticker 同日同股數的不同輪次」撞成同一 id,
    第二筆被去重誤殺 → 出場追蹤永久漏一筆(直接傷 #32 的賣飛對帳)。"""
    a = {"ticker": "NVDA", "cycle_id": "NVDA#2026-01-05#1", "exit_date": "2026-03-20", "shares_sold": 100}
    b = {"ticker": "NVDA", "cycle_id": "NVDA#2026-02-10#2", "exit_date": "2026-03-20", "shares_sold": 100}
    assert rv._revisit_id(a) == "NVDA#2026-01-05#1#2026-03-20#100", "新 id = cycle_id#exit_date#shares"
    assert rv._revisit_id(a) != rv._revisit_id(b), "不同輪次(同 ticker/日/股數)必須是不同 id"
    # 遷移:_canonical_id 用既有條目的 cycle_id 重建新 id → 存量 legacy 條目(3 段 revisit_id)也能逐輪次辨識
    legacy_item = {"revisit_id": "NVDA#2026-03-20#100", "ticker": "NVDA",
                   "cycle_id": "NVDA#2026-01-05#1", "exit_date": "2026-03-20", "shares_sold": 100}
    assert rv._canonical_id(legacy_item) == "NVDA#2026-01-05#1#2026-03-20#100", \
        "存量 legacy 條目用它自己的 cycle_id 重建成新 id(不靠會碰撞的 3 段字串)"


def test_enqueue_legacy_id_migration_compat():
    """#143 遷移:存量 revisit.jsonl 用舊 3 段 id。改格式後 enqueue 不可把舊出場當「新出場」重排,
    否則同一筆會以新舊兩種 id 各排一次 → 佇列虛胖、對帳重複。"""
    led, q = _mk_paths()
    lg.append_events(led, [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),      # cycle NVDA#2026-05-01#1,exit 06-15,10 股
    ])
    # 模擬遷移前就以舊 3 段 id 排入的存量條目。舊 _revisit_id 用 f"{shares_sold}",shares 是 float →
    # 真實舊 id 是 「…#10.0」(非 #10);_revisit_id_legacy 用同一個 f-string 重現,兩者天然對得上。
    lg.append_events(q, [{"type": "revisit", "revisit_id": "NVDA#2026-06-15#10.0",
                          "ticker": "NVDA", "cycle_id": "NVDA#2026-05-01#1",
                          "exit_date": "2026-06-15", "shares_sold": 10.0,
                          "due": {"30": "2026-07-15", "60": "2026-08-14", "90": "2026-09-13"},
                          "swaps": [], "idle_cash": True}])
    new, dup = rv.enqueue_from_ledger(led, q, today=dt.date(2026, 6, 16))
    assert (len(new), dup) == (0, 1), f"舊 3 段 id 的存量出場應被認出、不重排,實得 new={new} dup={dup}"


def test_enqueue_fresh_uses_new_five_segment_id():
    """乾淨佇列首排 → 用新 5 段 id(含 cycle_id),與 legacy 3 段可區分。"""
    led, q = _mk_paths()
    lg.append_events(led, [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),
    ])
    new, dup = rv.enqueue_from_ledger(led, q, today=dt.date(2026, 6, 16))
    assert (len(new), dup) == (1, 0)
    assert new[0]["revisit_id"] == "NVDA#2026-05-01#1#2026-06-15#10.0", \
        f"新排入應用 5 段 id(cycle_id + exit_date + shares 浮點),實得 {new[0]['revisit_id']}"


def test_enqueue_migration_keeps_same_day_second_round():
    """#143 遷移邊界(triad/Codex 反例):存量有一筆 legacy id,同日同股數的第二輪(不同 cycle)
    不可被「legacy 碰撞家族」連坐誤判 dup。只用 3 段字串 membership 會漏掉第二輪;用 cycle_id 重建才分得開。"""
    led, q = _mk_paths()
    lg.append_events(led, [
        _tr("2026-01-01", "NVDA", "buy", 10, 100.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 110.0),      # cycle1 NVDA#2026-01-01#1 → exit 06-15,10 股
        _tr("2026-06-15", "NVDA", "buy", 10, 111.0),       # 同日清倉後重建 → cycle2 NVDA#2026-06-15#2
        _tr("2026-06-15", "NVDA", "sell", 10, 112.0),      # cycle2 → exit 06-15,10 股(與 cycle1 同日同股數)
    ])
    # 存量:只有 cycle1 以舊 3 段 id 排入(cycle2 尚未追蹤)
    lg.append_events(q, [{"type": "revisit", "revisit_id": "NVDA#2026-06-15#10.0",
                          "ticker": "NVDA", "cycle_id": "NVDA#2026-01-01#1",
                          "exit_date": "2026-06-15", "shares_sold": 10.0,
                          "due": {"30": "2026-07-15", "60": "2026-08-14", "90": "2026-09-13"},
                          "swaps": [], "idle_cash": True}])
    new, dup = rv.enqueue_from_ledger(led, q, today=dt.date(2026, 6, 16))
    ids = sorted(n["revisit_id"] for n in new)
    assert ids == ["NVDA#2026-06-15#2#2026-06-15#10.0"], \
        f"cycle1 應認 dup、cycle2 應新排(不被碰撞家族連坐),實得 new ids={ids} dup={dup}"
    assert dup == 1, f"cycle1(存量已排)應計 1 dup,實得 {dup}"


def test_scan_due_progression_and_resolution():
    """30 到期未答 → 只出 30(不跳 60);答完 30 → 60 到期才出 60;全答 → 不再出。"""
    led, q = _mk_paths()
    lg.append_events(led, [
        _tr("2026-05-01", "NVDA", "buy", 10, 120.0),
        _tr("2026-06-15", "NVDA", "sell", 10, 120.5),
    ])
    rv.enqueue_from_ledger(led, q, today=dt.date(2026, 6, 15))    # 出場當日排入 → 30/60/90 全在未來,非 backfill
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


# ─────────────── C2. 冷啟動兩層:due 不灌爆 + 歷史 backlog(#170)───────────────

def _hist_ledger(led, n=8, base=dt.date(2024, 1, 15)):
    """n 檔季度性 買進→45 天後清倉,全落在 2024–2025(對 2026 啟用日 = 純歷史存量)。"""
    evs = []
    for i in range(n):
        buy = base + dt.timedelta(days=i * 90)
        sell = buy + dt.timedelta(days=45)
        evs += [_tr(buy.isoformat(), f"T{i}", "buy", 10, 100.0),
                _tr(sell.isoformat(), f"T{i}", "sell", 10, 110.0 + i)]   # 出場金額 T7 > … > T0
    lg.append_events(led, evs)


def test_cold_start_flood_suppressed_into_backlog():
    """既有歷史使用者第一次 enqueue:2.5 年舊出場的 30/60/90 全在啟用日之前 →
    due 一筆都不灌(不把復盤變審問),全數落 backlog。這是 #170 的核心迴歸。"""
    led, q = _mk_paths()
    _hist_ledger(led, n=8)
    enq = dt.date(2026, 7, 14)
    new, _ = rv.enqueue_from_ledger(led, q, today=enq)
    assert len(new) == 8 and all(n["enqueued_at"] == "2026-07-14" for n in new)
    revisits, resolutions, _ = rv.load_queue(q)
    assert rv.scan_due(revisits, resolutions, enq) == [], "啟用前歷史出場不該灌 due(#170 病灶)"
    backlog, summary, total = rv.scan_backlog(revisits, resolutions)
    assert total == 8 and summary["count"] == 8, "8 筆歷史存量全進 backlog 統計"
    assert summary["full"] == 8 and summary["reduce"] == 0
    assert len(backlog) == 5, "backlog engine 收斂到 top-5(抓大放小),真數看 backlog_total"
    assert backlog[0]["ticker"] == "T7", "金額大者先(T7 出場價最高)"


def test_partial_backfill_surfaces_later_checkpoints():
    """啟用當下已過 30 但未過 60/90 的出場:30 不催(啟用前窗),60 到期才正常浮現(不是被跳過的 30);
    且它非『完全歷史』→ 不進 backlog。"""
    led, q = _mk_paths()
    lg.append_events(led, [_tr("2026-05-01", "NVDA", "buy", 10, 120.0),
                           _tr("2026-05-30", "NVDA", "sell", 10, 130.0)])  # due30=06-29/60=07-29/90=08-28
    rv.enqueue_from_ledger(led, q, today=dt.date(2026, 7, 14))              # 30 已過、60/90 未過
    revisits, resolutions, _ = rv.load_queue(q)
    assert rv.scan_due(revisits, resolutions, dt.date(2026, 7, 14)) == [], "30 是啟用前窗、60 未到 → 靜默"
    due = rv.scan_due(revisits, resolutions, dt.date(2026, 7, 29))
    assert [d["checkpoint"] for d in due] == ["60"], f"應浮現 60 而非被跳過的 30,實得 {due}"
    _, summary, total = rv.scan_backlog(revisits, resolutions)
    assert total == 0 and summary["count"] == 0, "部分 backfill(90 未過)不算歷史存量,不進 backlog"


def test_backlog_summary_hindsight_honest_coverage():
    """選項 4 賣飛傾向要現價:有價的才算進分母,覆蓋率(priced)誠實列;缺價不猜(avg=None)。"""
    led, q = _mk_paths()
    lg.append_events(led, [_tr("2024-01-01", "UP", "buy", 10, 100.0),
                           _tr("2024-02-10", "UP", "sell", 10, 100.0),     # 賣後漲(150 → +50%)
                           _tr("2024-03-01", "DOWN", "buy", 10, 100.0),
                           _tr("2024-04-10", "DOWN", "sell", 10, 100.0)])  # 賣後跌(不給價)
    rv.enqueue_from_ledger(led, q, today=dt.date(2026, 7, 14))
    revisits, resolutions, _ = rv.load_queue(q)
    _, s1, _ = rv.scan_backlog(revisits, resolutions, prices={"UP": 150.0})
    assert s1["count"] == 2 and s1["priced"] == 1, "只有 UP 有現價 → 分母 1,不硬湊"
    assert s1["sold_before_rise"] == 1 and _approx(s1["avg_hindsight_pp"], 0.5, 1e-9)
    _, s2, _ = rv.scan_backlog(revisits, resolutions, prices={})
    assert s2["priced"] == 0 and s2["avg_hindsight_pp"] is None, "全缺價 → 不算賣飛傾向,不猜"


def test_backlog_excludes_resolved_and_ranks_by_amount():
    """backlog 金額大者先;複核過(落 resolution)的退出 backlog,summary.count 同步 -1(答完不再纏)。"""
    led, q = _mk_paths()
    lg.append_events(led, [_tr("2024-01-01", "AAA", "buy", 10, 100.0),
                           _tr("2024-02-10", "AAA", "sell", 10, 110.0),    # notional 1100
                           _tr("2024-03-01", "BBB", "buy", 5, 200.0),
                           _tr("2024-04-10", "BBB", "sell", 5, 300.0)])    # notional 1500 > AAA
    rv.enqueue_from_ledger(led, q, today=dt.date(2026, 7, 14))
    revisits, resolutions, _ = rv.load_queue(q)
    backlog0, _, total0 = rv.scan_backlog(revisits, resolutions)
    assert total0 == 2 and backlog0[0]["ticker"] == "BBB", "金額大者(BBB 1500)先"
    lg.append_events(q, [{"type": "resolution", "revisit_id": backlog0[0]["revisit_id"],
                          "checkpoint": "90", "status": "still_valid", "date": "2026-07-14"}])
    revisits, resolutions, _ = rv.load_queue(q)
    backlog1, summary1, total1 = rv.scan_backlog(revisits, resolutions)
    assert total1 == 1 and summary1["count"] == 1
    assert [b["ticker"] for b in backlog1] == ["AAA"], "複核過的 BBB 退出 backlog"


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
                         "--ledger", led, "--today", "2026-06-16"], capture_output=True, text=True)
    out1 = json.loads(r1.stdout)
    assert r1.returncode == 0 and out1["enqueued"] == 1, r1.stderr
    assert out1["new"][0]["ticker"] == "NVDA", "new = 本週新出場清單(SKILL 賣出 capture 訊號)"
    assert out1["new"][0]["enqueued_at"] == "2026-06-16", "#170:CLI 也蓋 enqueued_at"
    r1b = subprocess.run([sys.executable, ex, "--queue", q, "enqueue-from-ledger",
                          "--ledger", led, "--today", "2026-06-16"], capture_output=True, text=True)
    assert json.loads(r1b.stdout)["new"] == [], "重跑 new 必空,否則同一出場每週重問"
    r2 = subprocess.run([sys.executable, ex, "--queue", q, "scan", "--today", "2026-07-16",
                         "--prices", '{"NVDA": 160.0}'], capture_output=True, text=True)
    out = json.loads(r2.stdout)                          # stdout 必須純 JSON
    assert len(out["due"]) == 1 and out["due"][0]["checkpoint"] == "30"
    assert _approx(out["due"][0]["compare"]["orig_ret"], 160.0 / 120.5 - 1, 1e-6)
    assert out["recent_exits"] == [], "出場 31 天,超過 capture 鮮度窗 → 不再列為候選"
    assert out["backlog"] == [] and out["backlog_total"] == 0, \
        "#170:出場當日排入(enqueued_at 06-16),90 關在未來 → 非歷史存量,不進 backlog"
    r2b = subprocess.run([sys.executable, ex, "--queue", q, "scan", "--today", "2026-06-20"],
                         capture_output=True, text=True)
    recent = json.loads(r2b.stdout)["recent_exits"]
    assert len(recent) == 1 and recent[0]["ticker"] == "NVDA", \
        "窗口內(5 天)→ capture 候選,session 中斷/限額沒問到的下次還在"
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
