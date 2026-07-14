#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ledger.py — snapshot-anchored 本機帳本(Phase B PR-1;設計 docs/prd-ledger.md,tracking #129,#31 修訂版)

兩種輸入進同一本帳(~/.trade-coach/ledger.jsonl,append-only 事件流,純本機):
  snapshot  持倉宣告(券商 app 截圖/持倉頁,SKILL Step 0 標準化) — 多數用戶拿得出這個
  trade     交易流水(標準化 CSV) — 不假設完整;缺漏是常態不是錯誤

推導 = 最近 snapshot 當錨點 + 「date > as_of」的 trades 依序疊加(snapshot 語意 =
as_of 日收盤後狀態,同日交易視為已反映在宣告數字內);沒有任何 snapshot → 純 replay
(向後相容現行 engine 行為)。avg_cost 疊加語意對齊 trade_recap.positions()
(BUY 加權平均、SELL 減股不動均價),cycle 語意對齊 current_cycles()(歸零重建 seq+1)。

與 trade_recap.py 的邊界(PR-1 過渡期,別誤會兩者已統一):
  - 本模組是「帳本事實層」:純標準庫、離線、確定性;價格/匯率一律不在這裡。
  - 行為診斷(5 維)仍由 trade_recap 直接吃 CSV(樣本優先,含錨點前交易);
    帳本推導只信錨點之後(準確優先)。兩個消費者、兩種完整性要求,刻意分離。
  - 錨點帶入的持倉 trade_recap 看不到 → 兩邊 cycle_id 可能不同;theses.jsonl 綁定
    仍以 engine state 的 cycle_id 為準(SKILL.md 現行規則),ledger cycle_id 供帳本自身追蹤。

adjustment 事件是 reconcile 的差異留痕(給人回看),不進推導 —— 差異的實際修正由
reconcile 後追加的新 snapshot(新錨點)承擔,避免雙重套用。

CLI(SKILL 消費;JSON 走 stdout、人話訊息走 stderr,對齊 TR_JSON 模式):
  python3 ledger.py holdings        [--ledger P]                      # 推導當前持倉+integrity
  python3 ledger.py append-snapshot POS.json [--as-of D] [--source S] [--cash JSON] [--ledger P]
  python3 ledger.py append-trades   STD.csv  [--ledger P]             # 自動去重(重疊期重複匯入安全)
  python3 ledger.py reconcile       POS.json [--ledger P]             # 宣告 vs 推導 diff(唯讀)
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict

SCHEMA_V = 1
DEFAULT_LEDGER = os.path.expanduser("~/.trade-coach/ledger.jsonl")
EPS = 1e-6
SHARES_TOL = 1e-4          # reconcile 股數容差(對齊事件 round 精度:qty round4)
EVENT_TYPES = ("snapshot", "trade", "adjustment")


# ─────────────────────────── 讀寫 ───────────────────────────

def load_ledger(path):
    """讀 ledger.jsonl → (events, skipped)。逐行容錯:壞 JSON / 未知 type 只跳過該行並計數
    (#50 精神:讀入/跳過要可見,不靜默)。"""
    events, skipped = [], 0
    if not os.path.exists(path):
        return events, skipped
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
            if not isinstance(ev, dict) or ev.get("type") not in EVENT_TYPES:
                skipped += 1
                continue
            events.append(ev)
    return events, skipped


def append_events(path, events):
    """append-only 寫入;每 event 補 schema version。回傳寫入筆數。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ev in events:
            ev = dict(ev)
            ev.setdefault("v", SCHEMA_V)
            f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
    return len(events)


# ─────────────────────────── 推導 ───────────────────────────

def latest_anchor(events):
    """最近一筆 as_of 合法的 snapshot(同 as_of 取檔案序較後者=較新宣告)。無 → None。"""
    best, best_key = None, None
    for i, ev in enumerate(events):
        if ev.get("type") != "snapshot":
            continue
        try:
            d = dt.date.fromisoformat(str(ev.get("as_of")))
        except (TypeError, ValueError):
            continue
        key = (d, i)
        if best_key is None or key > best_key:
            best, best_key = ev, key
    return best


def _norm_trade(ev):
    """trade 事件 → (date, ticker, action, qty, px) 或 None(壞事件)。"""
    try:
        d = dt.date.fromisoformat(str(ev.get("date")))
        t = ev["ticker"]
        act = str(ev.get("action", "")).lower()
        qty = float(ev["qty"])
        px = float(ev["price"])
    except (KeyError, TypeError, ValueError):
        return None
    if not t or act not in ("buy", "sell") or qty <= 0 or px <= 0:
        return None
    return d, t, act, qty, px


def derive_holdings(events):
    """錨點推導當前持倉。回傳 {anchor, holdings, integrity, counts}。

    holdings: {ticker: {shares, avg_cost(None=未宣告且不可知), cost_total, currency,
                        market, origin(snapshot|trades), since, cycle_id}}
    integrity: 壞事件 / oversell(賣超,clamp 後照走)清單 —— 資料誠實層,呈現端要帶出。
    """
    anchor = latest_anchor(events)
    integrity = []
    pos = {}
    seq_base = defaultdict(int)      # ticker → 最後用過的 cycle 序號(清倉後保留,重建 +1)
    anchor_date = None

    if anchor is not None:
        anchor_date = dt.date.fromisoformat(str(anchor["as_of"]))
        for p in anchor.get("positions", []):
            t = p.get("ticker") if isinstance(p, dict) else None
            try:
                sh = float(p.get("shares"))
            except (AttributeError, TypeError, ValueError):
                sh = None
            if not t or sh is None:
                integrity.append({"issue": "bad_snapshot_position",
                                  "detail": json.dumps(p, ensure_ascii=False)[:120]})
                continue
            if sh <= EPS:
                continue
            ac = p.get("avg_cost")
            try:
                cost_total = float(ac) * sh if ac is not None else None
            except (TypeError, ValueError):
                cost_total = None
                integrity.append({"issue": "bad_avg_cost", "ticker": t})
            pos[t] = {"shares": sh, "cost_total": cost_total,
                      "currency": p.get("currency", "USD"), "market": p.get("market", "US"),
                      "origin": "snapshot", "since": anchor_date.isoformat()}
            seq_base[t] = 1                # cycle 序號單一事實源:seq_base(清倉後仍保留,重建 +1)

    trades = []
    for ev in events:
        if ev.get("type") != "trade":
            continue
        n = _norm_trade(ev)
        if n is None:
            integrity.append({"issue": "bad_trade_event",
                              "detail": json.dumps(ev, ensure_ascii=False)[:120]})
            continue
        d, t, act, qty, px = n
        if anchor_date is not None and d <= anchor_date:
            continue                      # snapshot = as_of 收盤後狀態;同日/更早的交易已反映在宣告內
        trades.append((d, t, act, qty, px, ev))
    trades.sort(key=lambda x: x[0])       # stable:同日保持匯入序

    for d, t, act, qty, px, ev in trades:
        cur = pos.get(t)
        if act == "buy":
            if cur is None or cur["shares"] <= EPS:
                seq_base[t] += 1
                pos[t] = {"shares": qty, "cost_total": qty * px,
                          "currency": ev.get("currency", "USD"), "market": ev.get("market", "US"),
                          "origin": "trades", "since": d.isoformat()}
            else:
                cur["shares"] += qty
                if cur["cost_total"] is not None:    # 錨點均價未宣告 → 總成本不可知,None 傳播
                    cur["cost_total"] += qty * px
        else:  # sell
            if cur is None or cur["shares"] <= EPS:
                integrity.append({"issue": "oversell", "ticker": t,
                                  "date": d.isoformat(), "qty": round(qty, 4)})
                continue
            if qty > cur["shares"] + EPS:
                integrity.append({"issue": "oversell", "ticker": t, "date": d.isoformat(),
                                  "qty": round(qty - cur["shares"], 4)})
            take = min(qty, cur["shares"])
            if cur["cost_total"] is not None:
                cur["cost_total"] -= take * (cur["cost_total"] / cur["shares"])
            cur["shares"] -= take
            if cur["shares"] <= EPS:
                pos.pop(t)                # 清倉;seq_base 留著給重建 +1

    holdings = {}
    for t in sorted(pos):
        p = pos[t]
        if round(p["shares"], 4) <= 0:     # 微量殘股 round 後歸零 → 不列(避免 shares=0.0 的幽靈持倉)
            continue
        ac = (p["cost_total"] / p["shares"]) if (p["cost_total"] is not None and p["shares"] > EPS) else None
        holdings[t] = {"shares": round(p["shares"], 4),
                       "avg_cost": round(ac, 4) if ac is not None else None,
                       "cost_total": round(p["cost_total"], 2) if p["cost_total"] is not None else None,
                       "currency": p["currency"], "market": p["market"],
                       "origin": p["origin"], "since": p["since"],
                       "cycle_id": f"{t}#{p['since']}#{seq_base[t]}"}
    return {"anchor": ({"as_of": anchor.get("as_of"), "source": anchor.get("source", "user_declared")}
                       if anchor is not None else None),
            "holdings": holdings,
            "integrity": integrity,
            "counts": {"events": len(events),
                       "trades_applied": len(trades),
                       "positions": len(holdings)}}


# ─────────────────────────── 對帳 ───────────────────────────

def reconcile(events, declared_positions):
    """宣告持倉 vs 推導持倉 diff(唯讀,不寫任何東西)。
    declared_positions: [{ticker, shares, ...}];回傳 {match, mismatch, clean}。
    mismatch.kind: shares_mismatch | only_declared(推導漏=中間有沒看到的交易) | only_derived(宣告漏=可能已清倉)。"""
    derived = derive_holdings(events)["holdings"]
    dec = {}
    for p in declared_positions:
        t = p.get("ticker") if isinstance(p, dict) else None
        if not t:
            continue
        try:
            dec[t] = float(p.get("shares", 0))
        except (TypeError, ValueError):
            continue
    match, mismatch = [], []
    for t in sorted(set(dec) | set(derived)):
        ds = derived.get(t, {}).get("shares")
        cs = dec.get(t)
        if ds is None:
            mismatch.append({"ticker": t, "derived_shares": 0.0, "declared_shares": cs,
                             "kind": "only_declared"})
        elif cs is None:
            mismatch.append({"ticker": t, "derived_shares": ds, "declared_shares": 0.0,
                             "kind": "only_derived"})
        elif abs(ds - cs) <= SHARES_TOL:
            match.append(t)
        else:
            mismatch.append({"ticker": t, "derived_shares": ds, "declared_shares": cs,
                             "kind": "shares_mismatch"})
    return {"match": match, "mismatch": mismatch, "clean": not mismatch}


# ─────────────────────────── 交易匯入 ───────────────────────────

def trades_from_csv(path, today=None):
    """標準欄位 CSV(Symbol/Action/Quantity/Price/TradeDate[/Market/Currency/Fee])→ trade 事件。
    過濾語意對齊 trade_recap.load()(RecordType=Trade、BUY/SELL、qty/px>0),但跳過要計數。

    #169:TradeDate 只驗格式合法(fromisoformat 不報錯)不夠——Step 0 把「格式合法但值錯」的日期
    (如把美式 MM/DD 誤判成 DD/MM)寫進來,append-only 帳本就永久帶著一筆看似正常、實則日期
    錯的交易,且沒有任何計數器提示(#50 只擋得住格式本身不合法的情形)。未來日期是唯一能零假陽性
    偵測的子情形(沒有交易會晚於今天成交)——擋在這裡不寫進帳,獨立計數,不跟既有格式錯誤的
    `skipped` 混在一起(那是另一種失敗模式,別把兩種訊號合成一種讓人分不清）。"""
    today = today or dt.date.today()
    out, skipped, future_dated = [], 0, 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("RecordType") or "").strip() != "Trade":
                skipped += 1
                continue
            act = (r.get("Action") or "").strip().upper()
            sym = (r.get("Symbol") or "").strip()
            if act not in ("BUY", "SELL") or not sym:
                skipped += 1
                continue
            try:
                qty = abs(float(r["Quantity"]))
                px = float(r["Price"])
                d = dt.date.fromisoformat(r["TradeDate"].strip())
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if qty <= 0 or px <= 0:
                skipped += 1
                continue
            if d > today:
                future_dated += 1
                continue
            ev = {"type": "trade", "date": d.isoformat(), "ticker": sym,
                  "action": act.lower(), "qty": round(qty, 4), "price": round(px, 6),
                  "market": (r.get("Market") or "US").strip() or "US",
                  "currency": (r.get("Currency") or "USD").strip().upper() or "USD",
                  "source_file": os.path.basename(path)}
            fee = (r.get("Fee") or "").strip()
            if fee:
                try:
                    ev["fee"] = float(fee)
                except ValueError:
                    pass
            out.append(ev)
    return out, skipped, future_dated


def _trade_key(ev):
    """去重鍵,對齊 trade_recap.load() 的 seen tuple 精度(qty round2 / px round4)。"""
    return (ev.get("ticker"), str(ev.get("action", "")).lower(),
            round(float(ev.get("qty", 0)), 2), round(float(ev.get("price", 0)), 4),
            str(ev.get("date")))


def dedupe_against(events, new_trades):
    """新交易對既有 ledger 去重(每週增量匯入、重疊期重複匯入都安全)。→ (fresh, dup_count)。
    #14:同日同價的獨立成交靠「出現序號」區分,與 trade_recap.load() 同語意——同一份匯入不會把
    一筆成交列兩次,故同批同日同價的第 2 筆 = 真獨立成交(保留);只有「超出既有 ledger 已記次數」
    才算真跨期重疊(跳過)。既有事件先按序號建 seen,新交易各自從 0 起算比對。"""
    seen = set()
    occ_seen = defaultdict(int)
    for ev in events:
        if ev.get("type") == "trade":
            try:
                key = _trade_key(ev)
            except (TypeError, ValueError):
                continue
            seen.add(key + (occ_seen[key],)); occ_seen[key] += 1
    fresh, dup = [], 0
    occ_new = defaultdict(int)
    for ev in new_trades:
        key = _trade_key(ev)                  # 呼叫端已標準化;壞 key 仍拋(保持原行為)
        rec = key + (occ_new[key],); occ_new[key] += 1
        if rec in seen:
            dup += 1
            continue
        seen.add(rec)
        fresh.append(ev)
    return fresh, dup


# ───────────────────── 共用工具(#166:coach.py/problems.py 收尾原子化)─────────────────────

def atomic_write_text(path, text):
    """原子寫入:tmp→replace,不留半寫髒狀態(抽自 trade_recap.py TR_STATE_OUT 既有寫法)。"""
    outdir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(outdir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=outdir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def session_id_from_state(state, nonce=""):
    """從 engine state 內容算穩定 session 身分(#166):同一份 state 重新算永遠得到同一個
    id,跨 Claude Code 對話中斷恢復免費,不用額外持久化 pending marker。別用 time.time()
    ——每次呼叫都不同,同一 session 的重試會被誤判成新 session(coach.py 改動前,
    append-theses/append-rules 的 sid 就是踩這個坑)。nonce 是逃生艙口:同日兩個內容
    恰巧相同、但邏輯上是不同 session 時,呼叫端可明確指定不同 nonce 拆開。

    已知限制(刻意不做,不在 #166 範圍內):state 內容包含即時抓的市場數據(alpha_ann/beta/
    payoff/cash 等,由 trade_recap.py 當次執行抓現價/匯率算出)。若中斷恢復時選擇整個
    重跑一次引擎(而非直接重讀既有的 last_state.json),重新抓到的現價大概率不同 byte,
    這裡算出的 session_id 就會跟著變、原本該被判定為「同 session」的收尾會被當成新 session。
    這條路徑對「Step 1 已寫出 last_state.json、之後只是繼續讀既有檔案」的正常 SKILL 流程
    沒有影響,只在使用者/Claude 選擇從頭重跑整個引擎當恢復手段時才會出現。"""
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256((canonical + "\x00" + nonce).encode("utf-8")).hexdigest()[:12]
    return f"{state.get('date_end')}__{digest}"


# ─────────────────────────── CLI ───────────────────────────

def _load_positions_file(path):
    """positions JSON:接受 [{...}] 或 {"as_of":..,"positions":[...],"cash":..}。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"positions": data}
    if isinstance(data, dict) and isinstance(data.get("positions"), list):
        return data
    raise ValueError("positions JSON 應為 [{ticker,shares,...}] 或 {positions:[...]}")


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    ap = argparse.ArgumentParser(description="fomo-kernel snapshot-anchored ledger(見 docs/prd-ledger.md)")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER, help=f"ledger 路徑(預設 {DEFAULT_LEDGER})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("holdings", help="推導當前持倉(JSON)")

    p_snap = sub.add_parser("append-snapshot", help="追加持倉宣告(新錨點)")
    p_snap.add_argument("positions_json")
    p_snap.add_argument("--as-of", default=None, help="宣告基準日 YYYY-MM-DD(預設今天;語意=該日收盤後)")
    p_snap.add_argument("--source", default="user_declared", choices=["user_declared", "reconciled"])
    p_snap.add_argument("--cash", default=None, help='現金 JSON,如 \'{"USD":8200,"TWD":120000}\'')

    p_tr = sub.add_parser("append-trades", help="標準化 CSV 匯入交易(自動去重)")
    p_tr.add_argument("std_csv")

    p_rec = sub.add_parser("reconcile", help="宣告 vs 推導 diff(唯讀)")
    p_rec.add_argument("positions_json")

    a = ap.parse_args(argv)
    events, skipped = load_ledger(a.ledger)
    if skipped:
        print(f"⚠️  ledger 有 {skipped} 行壞事件被跳過({a.ledger})", file=sys.stderr)

    if a.cmd == "holdings":
        out = derive_holdings(events)
        out["counts"]["skipped_lines"] = skipped
        _emit(out)
        return 0

    if a.cmd == "append-snapshot":
        data = _load_positions_file(a.positions_json)
        as_of = a.as_of or data.get("as_of") or dt.date.today().isoformat()
        dt.date.fromisoformat(as_of)                      # 早爆:壞日期別寫進帳
        ev = {"type": "snapshot", "as_of": as_of, "source": a.source,
              "positions": data["positions"]}
        cash = a.cash or data.get("cash")
        if cash:
            ev["cash"] = json.loads(cash) if isinstance(cash, str) else cash
        append_events(a.ledger, [ev])
        out = derive_holdings(events + [ev])
        print(f"appended snapshot as_of={as_of} source={a.source} "
              f"positions={len(data['positions'])}", file=sys.stderr)
        _emit(out)
        return 0

    if a.cmd == "append-trades":
        new_trades, bad, future_dated = trades_from_csv(a.std_csv)
        fresh, dup = dedupe_against(events, new_trades)
        append_events(a.ledger, fresh)
        if future_dated:                                  # #169:獨立示警,別跟 bad rows 混在一起
            print(f"⚠️  {future_dated} 筆交易的 TradeDate 晚於今天,疑似 Step 0 日期轉換錯誤"
                 f"(如 MM/DD 誤判成 DD/MM),已拒收不寫進帳——回頭核對原始對帳單的這幾筆日期",
                 file=sys.stderr)
        print(f"appended {len(fresh)} trades(dup skipped {dup}, bad rows {bad}, "
             f"future-dated skipped {future_dated})", file=sys.stderr)
        _emit({"appended": len(fresh), "skipped_dup": dup, "skipped_bad": bad,
               "skipped_future_dated": future_dated,
               "holdings_after": derive_holdings(events + fresh)["holdings"]})
        return 0

    if a.cmd == "reconcile":
        data = _load_positions_file(a.positions_json)
        _emit(reconcile(events, data["positions"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
