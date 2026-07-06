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


def test_adjustment_is_provenance_only():
    """adjustment 純留痕不進推導(修正由新 snapshot 承擔,防雙重套用)。"""
    out = lg.derive_holdings([
        _snap("2026-07-01", [{"ticker": "NVDA", "shares": 40, "avg_cost": 100.0}]),
        {"type": "adjustment", "date": "2026-07-02", "ticker": "NVDA",
         "delta_shares": -5, "reason": "reconcile: user snapshot 35 vs derived 40"},
    ])
    assert _approx(out["holdings"]["NVDA"]["shares"], 40), "adjustment 不改推導"


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
    existing = [_tr("2026-07-03", "NVDA", "buy", 10, 171.0)]
    fresh, dup = lg.dedupe_against(existing, [
        _tr("2026-07-03", "NVDA", "buy", 10, 171.0),    # 重複(重疊期再匯)
        _tr("2026-07-05", "NVDA", "buy", 3, 175.0),     # 新
        _tr("2026-07-05", "NVDA", "buy", 3, 175.0),     # 同批內重複也擋
    ])
    assert dup == 2 and len(fresh) == 1
    assert fresh[0]["date"] == "2026-07-05"


def test_trades_from_csv_filters_and_counts():
    p = _tmpfile(
        "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
        "NVDA,BUY,10,170.5,2026-07-01,Trade,US,USD\n"
        "2330.TW,BUY,100,985,2026-07-02,Trade,TW,TWD\n"
        "JUNK,BUY,0,10,2026-07-02,Trade,,\n"            # qty=0 → bad
        "AAPL,BUY,3,200.0,2026-07-02,Dividend,,\n"       # 非 Trade → bad
        "NOK,HOLD,3,10.0,2026-07-02,Trade,,\n",          # 非 BUY/SELL → bad
        ".csv")
    evs, skipped = lg.trades_from_csv(p)
    assert len(evs) == 2 and skipped == 3, f"收 2 跳 3,得 {len(evs)}/{skipped}"
    tw = next(e for e in evs if e["ticker"] == "2330.TW")
    assert tw["market"] == "TW" and tw["currency"] == "TWD"
    us = next(e for e in evs if e["ticker"] == "NVDA")
    assert us["market"] == "US" and us["currency"] == "USD"


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
