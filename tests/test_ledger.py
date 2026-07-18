#!/usr/bin/env python3
"""
snapshot-anchored ledger 的行為契約(Phase B PR-1;設計 docs/prd-ledger.md,#31 修訂版)。

跟其他測試檔的分工:
- test_engine_units.py → trade_recap 純函式(load/positions/round_trips…)。
- 本檔 → engine/ledger.py:雙輸入推導(snapshot 錨點+trades 疊加)、reconcile、
  去重匯入、integrity 誠實層、CLI JSON 介面。

設計原則:全部離線、確定性、純標準庫;斷言先對真實輸出探測過(2026-07-06)再鎖定。
鎖住的核心契約:
  1. snapshot = as_of 收盤後狀態 → date == as_of 的交易不疊加、> 才疊加。
  2. avg_cost 疊加語意對齊 trade_recap.positions():BUY 加權、SELL 減股不動均價。
  3. 錨點均價未宣告 → None 傳播(同 cycle 續買不硬編);清倉重建的新 cycle 可知。
  4. cycle 語意對齊 current_cycles():歸零重建 seq+1;cycle_id 三段格式。
  5. oversell → clamp 0 + integrity 記錄(誠實不靜默)。
  6. 無 snapshot → 純 replay(向後相容)。
  7. adjustment 事件純留痕,不進推導(修正由新 snapshot 錨點承擔,防雙重套用)。

跑法:
  python3 tests/test_ledger.py
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
import session as session_engine  # noqa: E402


# ─────────────────────────── 小工具 ───────────────────────────

def _snap(as_of, positions, source="user_declared", **kw):
    ev = {"type": "snapshot", "as_of": as_of, "source": source, "positions": positions}
    ev.update(kw)
    return ev


def _tr(date, ticker, action, qty, price, **kw):
    ev = {"type": "trade", "date": date, "ticker": ticker, "action": action,
          "qty": qty, "price": price}
    ev.update(kw)
    return ev


def _approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def _tmpfile(text, suffix):
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _snapshot_bundle(session_id, ticker, *, is_complete=True):
    anchor = _snap(
        "2026-07-18",
        [{"ticker": ticker, "shares": 1, "avg_cost": 10,
          "market": "US", "currency": "USD"}],
        is_complete=is_complete,
    )
    return {
        "schema_version": 2,
        "session_id": session_id,
        "route": "snapshot_review",
        "language": "en",
        "review_plan": {},
        "engine_state": {"date_end": "2026-07-18", "snapshot_anchor": anchor,
                         "metrics": {}, "problem_events": []},
        "engine_card": {},
        "answers": {},
        "narrative": {},
        "thesis_updates": [],
        "thesis_decisions": [],
        "exit_narratives": [],
        "commitment": None,
        "observations": [],
    }


def _finalize_snapshot(root, bundle):
    with session_engine.finalize_transaction(root, bundle["session_id"]) as transaction:
        return transaction.commit_bundle(bundle, "private\n", "public\n")


# ─────────────── A. 推導:錨點 + 疊加(核心契約)───────────────

def test_empty_ledger():
    out = lg.derive_holdings([])
    assert out["anchor"] is None, "無事件 anchor 應為 None"
    assert out["holdings"] == {}, "無事件 holdings 應為空"
    assert out["integrity"] == []


def test_snapshot_only():
    out = lg.derive_holdings([_snap("2026-07-01", [
        {"ticker": "NVDA", "shares": 40, "avg_cost": 152.3},
        {"ticker": "PLTR", "shares": 30},                       # 均價未宣告
    ])])
    h = out["holdings"]
    assert out["anchor"]["as_of"] == "2026-07-01"
    assert set(h) == {"NVDA", "PLTR"}
    n = h["NVDA"]
    assert _approx(n["shares"], 40) and _approx(n["avg_cost"], 152.3)
    assert n["origin"] == "snapshot" and n["cycle_id"] == "NVDA#2026-07-01#1"
    assert n["add_count"] == 0 and n["decision_cursor"] is None
    p = h["PLTR"]
    assert p["avg_cost"] is None and p["cost_total"] is None, "均價未宣告 → None,不硬編"
    assert p["shares"] == 30


def test_same_day_trade_not_stacked():
    """snapshot = as_of 收盤後狀態:date == as_of 不疊加,> 才疊加(PRD §1.3 釘死的時點語意)。"""
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "NVDA", "shares": 40, "avg_cost": 152.3}]),
        _tr("2026-07-01", "NVDA", "buy", 10, 170.5),   # 同日:已反映在宣告內
        _tr("2026-07-03", "NVDA", "buy", 10, 171.0),   # 之後:疊加
    ])
    n = out["holdings"]["NVDA"]
    assert _approx(n["shares"], 50), f"同日不疊、後日疊 → 50,得 {n['shares']}"
    # 加權:(40×152.3 + 10×171.0)/50 = 156.04(探測值)
    assert _approx(n["avg_cost"], 156.04, 1e-2), f"avg_cost 加權應 156.04,得 {n['avg_cost']}"
    assert out["counts"]["trades_applied"] == 1, "同日那筆不計入 applied"
    assert n["add_count"] == 1
    assert n["decision_cursor"] == "NVDA#2026-07-01#1#add#1"


def test_avg_cost_none_propagates_on_buy():
    """錨點均價未宣告 + 同 cycle 續買 → 總成本不可知,None 傳播(誠實分級,不假精確)。"""
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "PLTR", "shares": 30}]),
        _tr("2026-07-02", "PLTR", "buy", 10, 140.0),
    ])
    p = out["holdings"]["PLTR"]
    assert _approx(p["shares"], 40)
    assert p["avg_cost"] is None and p["cost_total"] is None


def test_sell_keeps_avg_cost():
    """SELL 減股不動均價(對齊 trade_recap.positions() 的成本會計)。"""
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "NVDA", "shares": 40, "avg_cost": 100.0}]),
        _tr("2026-07-02", "NVDA", "sell", 10, 200.0),
    ])
    n = out["holdings"]["NVDA"]
    assert _approx(n["shares"], 30) and _approx(n["avg_cost"], 100.0)
    assert _approx(n["cost_total"], 3000.0)


def test_cycle_rebuild_bumps_seq():
    """清倉後重建 = 新 cycle:seq+1、origin=trades、since=重建日(對齊 current_cycles 語意)。"""
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "PLTR", "shares": 30, "avg_cost": 120.0}]),
        _tr("2026-07-02", "PLTR", "sell", 30, 140.0),
        _tr("2026-07-04", "PLTR", "buy", 5, 141.0),
    ])
    p = out["holdings"]["PLTR"]
    assert p["cycle_id"] == "PLTR#2026-07-04#2", f"重建應 seq=2,得 {p['cycle_id']}"
    assert p["origin"] == "trades"
    assert _approx(p["avg_cost"], 141.0), "新 cycle 從零建,均價可知(不受舊 None/舊均價影響)"
    assert p["add_count"] == 0 and p["decision_cursor"] is None, "重建先是 entry,不是 add"


def test_add_cursor_counts_only_buys_inside_current_cycle():
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "PLTR", "shares": 30, "avg_cost": 120.0}]),
        _tr("2026-07-02", "PLTR", "buy", 5, 121.0),
        _tr("2026-07-03", "PLTR", "sell", 10, 122.0),  # partial sell: same cycle/cursor
        _tr("2026-07-04", "PLTR", "buy", 5, 123.0),
    ])
    p = out["holdings"]["PLTR"]
    assert p["cycle_id"] == "PLTR#2026-07-01#1"
    assert p["add_count"] == 2
    assert p["decision_cursor"] == "PLTR#2026-07-01#1#add#2"

    reopened = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "PLTR", "shares": 30, "avg_cost": 120.0}]),
        _tr("2026-07-02", "PLTR", "buy", 5, 121.0),
        _tr("2026-07-03", "PLTR", "sell", 35, 122.0),
        _tr("2026-07-04", "PLTR", "buy", 5, 123.0),
        _tr("2026-07-05", "PLTR", "buy", 2, 124.0),
    ])["holdings"]["PLTR"]
    assert reopened["cycle_id"] == "PLTR#2026-07-04#2"
    assert reopened["add_count"] == 1
    assert reopened["decision_cursor"] == "PLTR#2026-07-04#2#add#1"


def test_pure_replay_without_snapshot():
    """無 snapshot → 全 trades replay(向後相容;avg_cost 語意同 positions())。"""
    out = lg.derive_holdings([
        _tr("2026-06-01", "MU", "buy", 10, 100.0),
        _tr("2026-06-05", "MU", "buy", 10, 120.0),
        _tr("2026-06-10", "MU", "sell", 5, 130.0),
    ])
    m = out["holdings"]["MU"]
    assert out["anchor"] is None
    assert _approx(m["shares"], 15)
    assert _approx(m["avg_cost"], 110.0), f"(10×100+10×120)/20=110 賣後不變,得 {m['avg_cost']}"
    assert m["cycle_id"] == "MU#2026-06-01#1"


def test_oversell_clamped_and_reported():
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "NOK", "shares": 5, "avg_cost": 10.0}]),
        _tr("2026-07-02", "NOK", "sell", 8, 12.0),      # 持 5 賣 8:賣超 3
        _tr("2026-07-03", "MSTR", "sell", 5, 300.0),    # 無持倉直接賣
    ])
    assert "NOK" not in out["holdings"], "賣超 clamp 到 0 = 清倉"
    issues = {(i["ticker"], i["issue"]) for i in out["integrity"] if "ticker" in i}
    assert ("NOK", "oversell") in issues and ("MSTR", "oversell") in issues
    nok = next(i for i in out["integrity"] if i.get("ticker") == "NOK")
    assert _approx(nok["qty"], 3.0), f"賣超量應 3,得 {nok['qty']}"


def test_latest_anchor_wins():
    """多 snapshot 取 as_of 最新;同 as_of 取檔案序較後(較新宣告)。舊錨點前的交易不進推導。"""
    out = lg.derive_holdings([
        _snap("2026-06-01", [{"ticker": "NVDA", "shares": 999, "avg_cost": 1.0}]),
        _tr("2026-06-15", "NVDA", "buy", 100, 150.0),
        _snap("2026-07-01", [{"ticker": "NVDA", "shares": 40, "avg_cost": 152.3}]),
    ])
    assert out["anchor"]["as_of"] == "2026-07-01"
    assert _approx(out["holdings"]["NVDA"]["shares"], 40), "新錨點覆蓋一切歷史"
    dup = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "A", "shares": 1}]),
        _snap("2026-07-01", [{"ticker": "B", "shares": 2}]),
    ])
    assert set(dup["holdings"]) == {"B"}, "同 as_of 取後宣告者"


def test_latest_anchor_sequence_beats_repair_file_order_but_legacy_stays_compatible():
    newer = _snap("2026-07-01", [{"ticker": "NEW", "shares": 1}],
                  projection_sequence=2)
    older = _snap("2026-07-01", [{"ticker": "OLD", "shares": 1}],
                  projection_sequence=1)
    assert lg.latest_anchor([newer, older]) is newer, \
        "sequence-aware repair may append an older canonical bundle later"

    first = _snap("2026-07-01", [{"ticker": "FIRST", "shares": 1}])
    second = _snap("2026-07-01", [{"ticker": "SECOND", "shares": 1}])
    assert lg.latest_anchor([first, second]) is second, \
        "pre-sequence same-day snapshots retain their historical file-order rule"
    assert lg.latest_anchor([newer, second]) is newer, \
        "repairing a legacy row later must not override a finalized sequence-aware anchor"
    incomplete = _snap("2026-07-02", [{"ticker": "PARTIAL", "shares": 1}],
                       is_complete=False, projection_sequence=3)
    assert lg.latest_anchor([newer, incomplete]) is newer, \
        "an explicitly incomplete declaration can never replace the accounting anchor"


def test_adjustment_is_provenance_only():
    """adjustment 純留痕不進推導(修正由新 snapshot 承擔,防雙重套用)。"""
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "NVDA", "shares": 40, "avg_cost": 100.0}]),
        {"type": "adjustment", "date": "2026-07-02", "ticker": "NVDA",
         "delta_shares": -5, "reason": "reconcile: user snapshot 35 vs derived 40"},
    ])
    assert _approx(out["holdings"]["NVDA"]["shares"], 40), "adjustment 不改推導"


def test_micro_share_rounds_to_zero_not_listed():
    """review finding(2026-07-06):qty=1e-5 建倉 round(4) 後 = 0.0 → 不准以幽靈持倉出現。"""
    out = lg.derive_holdings([_tr("2026-07-02", "DUST", "buy", 0.00001, 100.0)])
    assert "DUST" not in out["holdings"], "round 後歸零的微量殘股不列 holdings"


def test_csv_filter_parity_with_trade_recap():
    """drift 鎖(review altitude finding):trades_from_csv 的收/拒語意 + 去重鍵精度 +
    #14 同日同價多筆的 multiplicity「對齊 trade_recap.load()」不能只活在註解裡——同一份 CSV
    餵兩邊,斷言行為一致。**比 multiset(sorted list)不只 set**:否則同日同價「一邊保留一邊併殺」
    的分歧會靜默漏測(set 兩邊都塌成 1)。之後任一邊改過濾/精度/去重規則,這條紅燈逼兩邊一起改。"""
    sys.path.insert(0, ENGINE)
    import trade_recap as tr
    csv_text = (
        "Symbol,Action,Quantity,Price,TradeDate,RecordType\n"
        "NVDA,BUY,10,170.5,2026-07-01,Trade\n"          # 正常
        "NVDA,BUY,10,170.5,2026-07-01,Trade\n"          # #14:同檔同日同價 → 兩邊都該保留成 2 筆(非去重)
        "PLTR,SELL,5,140.123456,2026-07-02,Trade\n"     # 高精度價
        "JUNK,BUY,0,10,2026-07-02,Trade\n"              # qty=0(兩邊都拒)
        "FREE,BUY,3,0,2026-07-02,Trade\n"               # px=0(兩邊都拒)
        "AAPL,BUY,3,200.0,2026-07-02,Dividend\n"        # 非 Trade(兩邊都拒)
        "NOK,HOLD,3,10.0,2026-07-02,Trade\n"            # 非 BUY/SELL(兩邊都拒)
    )
    p = _tmpfile(csv_text, ".csv")
    recap_rows = tr.load([p])
    recap_keys = sorted((r["ticker"], r["side"], round(r["qty"], 2), round(r["price"], 4),
                         r["date"].isoformat()) for r in recap_rows)
    led_events, _, _ = lg.trades_from_csv(p, today=dt.date(2026, 7, 2))
    led_fresh, _ = lg.dedupe_against([], led_events)
    led_keys = sorted(lg._trade_key(ev) for ev in led_fresh)
    assert led_keys == recap_keys, (
        f"ledger 與 trade_recap 對同一 CSV 的收/拒/去重/multiplicity 不一致:\n"
        f"ledger: {led_keys}\nrecap: {recap_keys}")
    assert sum(1 for k in recap_keys if k[0] == "NVDA") == 2, \
        "#14:同日同價兩筆 NVDA 應都在(multiset,非去重成 1)"


# ─────────────── B. reconcile ───────────────

def test_reconcile_kinds():
    events = [
        _snap("2026-07-01", [{"ticker": "NVDA", "shares": 50, "avg_cost": 156.0},
                             {"ticker": "PLTR", "shares": 5, "avg_cost": 141.0},
                             {"ticker": "2330.TW", "shares": 100, "avg_cost": 985.0}]),
    ]
    diff = lg.reconcile(events, [
        {"ticker": "NVDA", "shares": 55},       # 對不上
        {"ticker": "2330.TW", "shares": 100},   # 符合
        {"ticker": "GOOG", "shares": 5},        # 推導沒有
    ])                                           # PLTR 宣告沒有
    assert diff["clean"] is False
    assert diff["match"] == ["2330.TW"]
    kinds = {m["ticker"]: m["kind"] for m in diff["mismatch"]}
    assert kinds == {"NVDA": "shares_mismatch", "GOOG": "only_declared", "PLTR": "only_derived"}
    clean = lg.reconcile(events, [{"ticker": "NVDA", "shares": 50},
                                  {"ticker": "PLTR", "shares": 5},
                                  {"ticker": "2330.TW", "shares": 100}])
    assert clean["clean"] is True and clean["mismatch"] == []


# ─────────────── C. 匯入:去重 + 過濾計數 ───────────────

def test_dedupe_against_existing():
    """#14:跨期重疊(既有 ledger 已記那筆)才去重;同批內同日同價的第 2 筆 = 真獨立成交,保留
    (與 trade_recap.load() 同語意——一份匯入不會把同一筆成交列兩次)。"""
    existing = [_tr("2026-07-03", "NVDA", "buy", 10, 171.0)]
    fresh, dup = lg.dedupe_against(existing, [
        _tr("2026-07-03", "NVDA", "buy", 10, 171.0),    # 與既有重疊(重疊期再匯)→ 去重
        _tr("2026-07-05", "NVDA", "buy", 3, 175.0),     # 新
        _tr("2026-07-05", "NVDA", "buy", 3, 175.0),     # 同批同日同價第 2 筆 = 獨立成交,保留(#14)
    ])
    assert dup == 1 and len(fresh) == 2, f"只有跨期重疊那筆去重、同批同日同價兩筆都留,得 dup={dup} fresh={len(fresh)}"
    assert all(ev["date"] == "2026-07-05" for ev in fresh), "留下的是兩筆 07-05"


def test_trades_from_csv_filters_and_counts():
    p = _tmpfile(
        "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
        "NVDA,BUY,10,170.5,2026-07-01,Trade,US,USD\n"
        "2330.TW,BUY,100,985,2026-07-02,Trade,TW,TWD\n"
        "JUNK,BUY,0,10,2026-07-02,Trade,,\n"            # qty=0 → bad
        "AAPL,BUY,3,200.0,2026-07-02,Dividend,,\n"       # 非 Trade → bad
        "NOK,HOLD,3,10.0,2026-07-02,Trade,,\n",          # 非 BUY/SELL → bad
        ".csv")
    evs, skipped, future_dated = lg.trades_from_csv(p, today=dt.date(2026, 7, 2))
    assert len(evs) == 2 and skipped == 3, f"收 2 跳 3,得 {len(evs)}/{skipped}"
    assert future_dated == 0, "這批日期都在過去,不該誤觸未來日期防線"
    tw = next(e for e in evs if e["ticker"] == "2330.TW")
    assert tw["market"] == "TW" and tw["currency"] == "TWD"
    us = next(e for e in evs if e["ticker"] == "NVDA")
    assert us["market"] == "US" and us["currency"] == "USD"


def test_trades_from_csv_rejects_future_dated_trades():
    """#169:格式合法但日期在未來的交易(疑似 Step 0 MM/DD↔DD/MM 誤判)——拒收不寫進帳,
    獨立計數(不跟格式錯誤的 skipped 混在一起),零假陽性(沒有交易真的會晚於「今天」成交)。"""
    p = _tmpfile(
        "Symbol,Action,Quantity,Price,TradeDate,RecordType\n"
        "NVDA,BUY,10,170.5,2026-07-01,Trade\n"          # 過去,正常收
        "PLTR,BUY,5,140.0,2026-07-10,Trade\n"           # 就是「今天」,不算未來,正常收
        "ORCL,BUY,3,120.0,2026-10-07,Trade\n",          # 晚於今天 → 疑似 07/10 誤判成 10/07,拒收
        ".csv")
    evs, skipped, future_dated = lg.trades_from_csv(p, today=dt.date(2026, 7, 10))
    assert len(evs) == 2 and future_dated == 1 and skipped == 0, \
        f"應收 2(含當天)、擋 1 筆未來日期、0 筆格式錯,得 evs={len(evs)}/future={future_dated}/skipped={skipped}"
    assert {e["ticker"] for e in evs} == {"NVDA", "PLTR"}, "ORCL(未來日期)不該出現在收下的事件裡"


def test_trades_from_csv_today_defaults_to_real_today():
    """不傳 today 時預設用真實今天——用一個保證早於任何測試執行時刻的固定過去日期驗證預設值有生效,
    不寫死成某個未來會失效的日期常數。"""
    p = _tmpfile(
        "Symbol,Action,Quantity,Price,TradeDate,RecordType\n"
        "NVDA,BUY,10,170.5,2020-01-01,Trade\n",
        ".csv")
    evs, skipped, future_dated = lg.trades_from_csv(p)
    assert len(evs) == 1 and future_dated == 0, "2020 年的交易對任何現實中的『今天』都不是未來"


def test_load_ledger_skips_bad_lines():
    p = _tmpfile(
        json.dumps(_snap("2026-07-01", [{"ticker": "A", "shares": 1}])) + "\n"
        + "not json at all\n"
        + json.dumps({"type": "alien", "x": 1}) + "\n"
        + json.dumps(_tr("2026-07-02", "A", "buy", 1, 10.0)) + "\n",
        ".jsonl")
    events, skipped = lg.load_ledger(p)
    assert len(events) == 2 and skipped == 2


# ─────────────── D. 寫入 + CLI 介面(SKILL 消費的形狀)───────────────

def test_append_roundtrip_and_cli():
    d = tempfile.mkdtemp()
    led = os.path.join(d, "ledger.jsonl")
    n = lg.append_events(led, [_snap("2026-07-01", [{"ticker": "NVDA", "shares": 40,
                                                     "avg_cost": 152.3}])])
    assert n == 1
    events, skipped = lg.load_ledger(led)
    assert skipped == 0 and events[0]["v"] == lg.SCHEMA_V, "寫入補 schema version"

    r = subprocess.run([sys.executable, os.path.join(ENGINE, "ledger.py"),
                        "--ledger", led, "holdings"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)                     # stdout 必須是純 JSON(SKILL 消費契約)
    assert out["holdings"]["NVDA"]["cycle_id"] == "NVDA#2026-07-01#1"
    assert out["counts"]["skipped_lines"] == 0

    pos2 = os.path.join(d, "pos2.json")
    with open(pos2, "w", encoding="utf-8") as f:
        json.dump([{"ticker": "NVDA", "shares": 40}], f)
    r2 = subprocess.run([sys.executable, os.path.join(ENGINE, "ledger.py"),
                         "--ledger", led, "reconcile", pos2],
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    assert json.loads(r2.stdout)["clean"] is True


def test_cli_append_snapshot_source_reconciled():
    d = tempfile.mkdtemp()
    led = os.path.join(d, "ledger.jsonl")
    pos = os.path.join(d, "pos.json")
    with open(pos, "w", encoding="utf-8") as f:
        json.dump({"as_of": "2026-07-06",
                   "positions": [{"ticker": "NVDA", "shares": 35, "avg_cost": 156.0}],
                   "cash": {"USD": 9000}}, f)
    r = subprocess.run([sys.executable, os.path.join(ENGINE, "ledger.py"),
                        "--ledger", led, "append-snapshot", pos, "--source", "reconciled"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["anchor"]["source"] == "reconciled"
    assert _approx(out["holdings"]["NVDA"]["shares"], 35)
    events, _ = lg.load_ledger(led)
    assert events[0]["cash"] == {"USD": 9000}, "cash 欄要落盤(閒置 cash 偵測的料,#33)"


def test_cli_append_trades_rejects_future_dated():
    """#169 CLI 層 wiring:append-trades 對未來日期的交易要拒收、獨立計數、不寫進帳——
    只測純函式不夠,SKILL 實際呼叫的是這條 CLI 路徑。用 2020/2099 這種年份級的過去/未來
    (而非相對「今天」的日期),測試不管在哪一天跑都不會誤判,不需要幫 CLI 加一個沒人用得到的
    --today 覆寫旗標。"""
    d = tempfile.mkdtemp()
    led = os.path.join(d, "ledger.jsonl")
    csv_path = os.path.join(d, "trades.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("Symbol,Action,Quantity,Price,TradeDate,RecordType\n"
                "NVDA,BUY,10,170.5,2020-01-01,Trade\n"
                "PLTR,BUY,5,140.0,2099-01-01,Trade\n")
    r = subprocess.run([sys.executable, os.path.join(ENGINE, "ledger.py"),
                        "--ledger", led, "append-trades", csv_path],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["appended"] == 1 and out["skipped_future_dated"] == 1 and out["skipped_bad"] == 0, \
        f"應收 1(2020)、擋 1(2099)未來日期,得 {out}"
    assert "PLTR" not in out["holdings_after"], "未來日期那筆(PLTR)不該進 holdings"
    assert "future-dated" in r.stderr and "2099" not in r.stderr, \
        "stderr 要提示未來日期筆數,不需要逐筆印出可能敏感的細節"
    events, _ = lg.load_ledger(led)
    assert all(e.get("ticker") != "PLTR" for e in events), "PLTR 不該被寫進 ledger.jsonl(拒收非只是不算數)"


# ─────────────── E. snapshot session projection ordering ───────────────

def test_snapshot_identity_normalizes_completeness_and_excludes_sequence():
    base = _snap("2026-07-18", [{"ticker": "A", "shares": 1}])
    explicit = {**base, "is_complete": True, "projection_sequence": 99}
    incomplete = {**base, "is_complete": False, "projection_sequence": 99}
    assert session_engine._snapshot_payload(base) == session_engine._snapshot_payload(explicit), \
        "legacy missing completeness means complete, and sequence is projection metadata"
    assert session_engine._snapshot_payload(base) != session_engine._snapshot_payload(incomplete), \
        "an incomplete declaration must have a distinct accounting identity"


def test_incomplete_snapshot_commits_but_projection_is_visibly_skipped():
    root = tempfile.mkdtemp()
    bundle = _snapshot_bundle("2026-07-18__incomplete", "PARTIAL", is_complete=False)
    result, projection, projection_error = _finalize_snapshot(root, bundle)
    assert result["status"] == "committed" and projection_error is None
    snapshot_row = projection["rows"][0]
    assert snapshot_row["status"] == "skipped_incomplete" and snapshot_row["appended"] == 0
    assert not os.path.exists(os.path.join(root, "ledger.jsonl")), \
        "explicitly incomplete holdings are review evidence, never a ledger anchor"
    canonical = session_engine.load_committed(root, bundle["session_id"])
    assert canonical["engine_state"]["snapshot_anchor"]["is_complete"] is False
    assert "projection_sequence" not in canonical["engine_state"], \
        "a skipped accounting fact must not consume ordering"
    report = session_engine.read_json(
        os.path.join(root, "projections", bundle["session_id"] + ".json"))
    assert report["rows"][0]["status"] == "skipped_incomplete", \
        "the durable projection report must make the skip visible"


def test_snapshot_projection_sequence_is_reserved_reused_and_repairs_deterministically():
    root = tempfile.mkdtemp()
    # Lexical repair order is intentionally the reverse of finalize order.
    old = _snapshot_bundle("2026-07-18__z-old", "OLD")
    new = _snapshot_bundle("2026-07-18__a-new", "NEW")
    first = _finalize_snapshot(root, old)
    second = _finalize_snapshot(root, new)
    assert first[2] is None and second[2] is None

    old_canonical = session_engine.load_committed(root, old["session_id"])
    new_canonical = session_engine.load_committed(root, new["session_id"])
    assert old_canonical["engine_state"]["projection_sequence"] == 1
    assert new_canonical["engine_state"]["projection_sequence"] == 2
    rows = session_engine._read_jsonl(os.path.join(root, "ledger.jsonl"))
    assert [row["projection_sequence"] for row in rows] == [1, 2]
    assert lg.latest_anchor(rows)["positions"][0]["ticker"] == "NEW"
    last_state_path = os.path.join(root, "last_state.json")
    assert session_engine.read_json(last_state_path)["snapshot_anchor"]["positions"][0]["ticker"] == "NEW"

    # Finalize rebuilds the draft from plan/answers in production, so retry
    # without a caller-supplied sequence must recover the canonical reservation.
    retry = _finalize_snapshot(root, _snapshot_bundle(old["session_id"], "OLD"))
    assert retry[0]["status"] == "no-op" and retry[2] is None
    assert session_engine.load_committed(root, old["session_id"])["engine_state"]["projection_sequence"] == 1
    assert len(session_engine._read_jsonl(os.path.join(root, "ledger.jsonl"))) == 2

    os.unlink(os.path.join(root, "ledger.jsonl"))
    repaired = session_engine.repair_projections(root)
    assert repaired["errors"] == []
    repaired_rows = session_engine._read_jsonl(os.path.join(root, "ledger.jsonl"))
    assert [row["projection_sequence"] for row in repaired_rows] == [2, 1], \
        "repair order follows stable session discovery, not historical append order"
    assert lg.latest_anchor(repaired_rows)["positions"][0]["ticker"] == "NEW", \
        "higher sequence remains the latest same-day declaration after repair"
    assert session_engine.read_json(last_state_path)["snapshot_anchor"]["positions"][0]["ticker"] == "NEW", \
        "repairing a lower same-day sequence later must not regress last_state"
    assert [report["last_state"] for report in repaired["reports"]] == ["written", "kept_newer"]
    before = list(repaired_rows)
    repaired_again = session_engine.repair_projections(root)
    assert repaired_again["errors"] == []
    assert session_engine._read_jsonl(os.path.join(root, "ledger.jsonl")) == before, \
        "repair must be idempotent"


def test_legacy_same_day_last_state_keeps_projection_order_behavior():
    root = tempfile.mkdtemp()
    first = _snapshot_bundle("2026-07-18__legacy-first", "FIRST")
    second = _snapshot_bundle("2026-07-18__legacy-second", "SECOND")
    for bundle in (first, second):
        bundle["route"] = "weekly_review"
        bundle["engine_state"].pop("snapshot_anchor")
        bundle["engine_state"]["legacy_marker"] = bundle["session_id"]

    _finalize_snapshot(root, first)
    _finalize_snapshot(root, second)
    last_state = session_engine.read_json(os.path.join(root, "last_state.json"))
    assert last_state["legacy_marker"] == second["session_id"], \
        "same-day states without snapshot sequences retain the historical last-write rule"


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
