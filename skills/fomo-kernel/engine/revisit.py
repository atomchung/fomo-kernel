#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
revisit.py — 出場後 30/60/90 追蹤 + swap 機會成本(#32/#33;#129 PR-3,設計 docs/prd-ledger.md §4)

解的問題:清倉後那檔就從宇宙消失——沒有機制在 30/60/90 天後問「賣飛了沒?當時的理由還成立嗎?
換進去的東西有比較好嗎?」。出場邊緣永遠是盲區,直到這裡把它變成系統性追蹤。

資料流(全本機,append-only,同 ledger 慣例):
  ledger.jsonl(事實層,PR-1) ──enqueue-from-ledger──▶ revisit.jsonl(追蹤佇列)
    事件三種:
      {type:"revisit", revisit_id, ticker, cycle_id, exit_date, exit_price, shares_sold,
       kind: full|reduce, due:{"30":d,"60":d,"90":d}, enqueued_at, swaps:[{ticker,date,price,qty}]|[],
       idle_cash: bool}
      {type:"resolution", revisit_id, checkpoint("30"|"60"|"90"), status, note?, date}
      status ∈ still_valid(理由成立,賣早也是紀律)/ modified(部分對,要調)/ falsified(真錯,進教訓)

冷啟動兩層(#170):既有歷史使用者第一次 enqueue 時,2.5 年舊出場的 30/60/90 全在啟用日之前 →
  若照單進 due 會一次噴近百筆、把復盤變審問。解法用每筆 enqueued_at(= 開始追蹤這筆的日期)分兩層:
    · 到期複核 due —— 只留「啟用後才到期」的 checkpoint(due>enqueued_at);啟用前就過期的不催。
    · 歷史 backlog —— 連 90 都在啟用前過期的「完全歷史存量」不丟、不逐筆逼問,改成 scan 的 backlog
      (金額大者先,抓大放小)+ backlog_summary(彙總洞察:賣飛傾向、清倉/減倉比、最常進出)。
      歷史是復盤依據,但不是每週審問——SKILL 每次選擇性帶最大 1–2 筆,答完 resolve 即退出 backlog。

判讀鐵律(#33 swap framing):賣飛的 hindsight loss 必須對位 swap——
  賣 A 換 B:B 同期報酬 vs A 繼續持有;賣 A 閒置:機會成本 = A 繼續持有報酬。
  只有「換入 < 原標的」才算真正的決策錯誤,不可只算 sell 賣早多少。

離線紀律:本模組純標準庫、零網路;對比要的現價由呼叫端注入(SKILL 拿 engine state 的 last_px
餵 `--prices`),缺價 → 對比欄位 None + needs_prices 誠實列出,不猜。

CLI(JSON stdout / 訊息 stderr,同 ledger 慣例):
  python3 revisit.py enqueue-from-ledger [--ledger P] [--queue Q] [--today D]  # 掃出場→排入(去重,蓋 enqueued_at)
  python3 revisit.py scan [--queue Q] [--today D] [--prices J]        # due + recent_exits + backlog(#170)
  python3 revisit.py resolve ID CHECKPOINT STATUS [--note N] [--queue Q]
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger as lg  # noqa: E402  # 同目錄,共用 load/append 與錨點語意

DEFAULT_QUEUE = os.path.expanduser("~/.trade-coach/revisit.jsonl")
REDUCE_TH = 0.5            # 單筆賣出 ≥ 賣前持倉 50% = 大減倉,也排入(#32)
SWAP_WINDOW_DAYS = 14      # 賣出後 N 天內的買入 = 換入候選(#33 預設)
RECENT_WINDOW_DAYS = 14    # 賣出理由 capture 鮮度窗(#136):出場 ≤N 天記憶還在,才值得問「為什麼賣」
CHECKPOINTS = ("30", "60", "90")
STATUSES = ("still_valid", "modified", "falsified")


# ─────────────────────────── 出場偵測 ───────────────────────────

def detect_exits(events):
    """從 ledger 事件流偵測出場(錨點語意與 ledger.derive_holdings 一致:
    只看最近 snapshot 之後的交易;錨點持倉當初始 shares)。
    回 [{ticker, cycle_id, exit_date, exit_price, shares_sold, kind}]。
    kind: full=清倉 / reduce=單筆賣 ≥50% 賣前持倉。同一天清倉多筆只記最後一筆(合併語意)。"""
    anchor = lg.latest_anchor(events)
    shares = {}
    since = {}
    seq = {}
    anchor_date = None
    if anchor is not None:
        anchor_date = dt.date.fromisoformat(str(anchor["as_of"]))
        for p in anchor.get("positions", []):
            t = p.get("ticker") if isinstance(p, dict) else None
            try:
                sh = float(p.get("shares"))
            except (AttributeError, TypeError, ValueError):
                continue
            if t and sh > lg.EPS:
                shares[t] = sh
                since[t] = anchor_date.isoformat()
                seq[t] = 1
    trades = []
    for ev in events:
        if ev.get("type") != "trade":
            continue
        n = lg._norm_trade(ev)
        if n is None:
            continue
        d, t, act, qty, px = n
        if anchor_date is not None and d <= anchor_date:
            continue
        trades.append((d, t, act, qty, px,
                       str(ev.get("market") or "US"),
                       str(ev.get("currency") or "USD").upper()))
    trades.sort(key=lambda x: x[0])
    exits = []
    for d, t, act, qty, px, market, currency in trades:
        cur = shares.get(t, 0.0)
        if act == "buy":
            if cur <= lg.EPS:
                seq[t] = seq.get(t, 0) + 1
                since[t] = d.isoformat()
            shares[t] = cur + qty
            continue
        if cur <= lg.EPS:
            continue                                  # 賣超/無倉賣:ledger integrity 已記,不進 revisit
        take = min(qty, cur)
        left = cur - take
        if left <= lg.EPS or take >= cur * REDUCE_TH - lg.EPS:
            exits.append({"ticker": t,
                          "cycle_id": f"{t}#{since.get(t, '?')}#{seq.get(t, 1)}",
                          "exit_date": d.isoformat(),
                          "exit_price": round(px, 6),
                          "shares_sold": round(take, 4),
                          "shares_before": round(cur, 4),
                          "kind": "full" if left <= lg.EPS else "reduce",
                          "market": market, "currency": currency})
        shares[t] = left
    return exits


def infer_swaps(events, exit_item, window_days=SWAP_WINDOW_DAYS):
    """賣出「當天起」window 天內、不同 ticker 的買入 = 換入候選(全列,金額大者先);空 = 閒置 cash。
    同日含(review 2026-07-06):賣早買午是最常見的真實換股;日期無盤中順序,同日買入
    可能先於賣出,但那也是同一次資金重配——寬進,由用戶 confirm(inference-first)嚴出。"""
    d0 = dt.date.fromisoformat(exit_item["exit_date"])
    d1 = d0 + dt.timedelta(days=window_days)
    cands = []
    for ev in events:
        if ev.get("type") != "trade":
            continue
        n = lg._norm_trade(ev)
        if n is None:
            continue
        d, t, act, qty, px = n
        if act != "buy" or t == exit_item["ticker"]:
            continue
        if d0 <= d <= d1:
            cands.append({"ticker": t, "date": d.isoformat(),
                          "price": round(px, 6), "qty": round(qty, 4)})
    cands.sort(key=lambda c: -(c["price"] * c["qty"]))
    return cands


# ─────────────────────────── 佇列 ───────────────────────────

def _revisit_id(x):
    # #143:cycle_id(含 ticker+開倉日+序號)天然區分「同 ticker 同日同股數的不同輪次」——
    # 舊 key ticker#exit_date#shares_sold 會把同日兩個 round-trip 算成同一個 id,第二筆被去重誤殺,
    # 出場追蹤永久漏一筆(直接傷 #32 的 30/60/90 賣飛對帳)。detect_exits 早就算好 cycle_id,用它。
    return f"{x['cycle_id']}#{x['exit_date']}#{x['shares_sold']}"


def _canonical_id(item):
    """把佇列既有條目正規化成「新格式 revisit_id」,作為 enqueue 去重 key(#143 遷移)。
    存量 legacy 條目的 revisit_id 是舊 3 段(cycle_id 分不出同日同股數的不同輪次),但它們都存了
    cycle_id 欄(detect_exits 必產、enqueue **x 必存)→ 用 cycle_id 重建新 id,遷移時仍能逐輪次辨識。
    ⚠️ 別退化成「舊 3 段字串 membership」:那會把整個「同 ticker/日/股數」碰撞家族一起誤判 dup,
    只要存量有一筆舊 id,同日的第二輪永遠補不回來(triad/Codex 抓到的反例)。"""
    if item.get("cycle_id") and item.get("exit_date") is not None and item.get("shares_sold") is not None:
        return _revisit_id(item)
    return item.get("revisit_id")            # 極端防禦:壞條目真缺 cycle_id → 退回自身 id,至少不 KeyError


def load_queue(path):
    """讀 revisit.jsonl → (revisits{id: item}, resolutions{(id, checkpoint): status})。壞行跳過計數。"""
    revisits, resolutions, skipped = {}, {}, 0
    if not os.path.exists(path):
        return revisits, resolutions, skipped
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
            if ev.get("type") == "revisit" and ev.get("revisit_id"):
                revisits[ev["revisit_id"]] = ev
            elif ev.get("type") == "resolution" and ev.get("revisit_id"):
                resolutions[(ev["revisit_id"], str(ev.get("checkpoint")))] = ev
            else:
                skipped += 1
    return revisits, resolutions, skipped


def enqueue_from_ledger(ledger_path, queue_path, today=None):
    """掃 ledger 出場 → 排入 queue(以 revisit_id 去重,重跑安全)。回 (new_items, skipped_dup)。
    new_items = 本次新排入的完整 revisit 事件——這是 SKILL 賣出理由 capture(#136)的訊號源:
    「為什麼賣」只有出場當週問得到,已在佇列的出場不重報,所以 new 非空 = 本週有新出場要問。
    #170:每筆蓋 enqueued_at(= 開始追蹤這筆的日期,預設今天;today 供測試注入)——scan 用它區分
    「啟用後才到期的 due」與「啟用前就過期的歷史存量(→ backlog,不催)」。"""
    enqueued_at = (today or dt.date.today()).isoformat()
    events, _ = lg.load_ledger(ledger_path)
    revisits, _, _ = load_queue(queue_path)
    # #143:去重 key 一律用「新格式正規 id」。既有條目(含存量 legacy)先用其 cycle_id 重建 →
    # 同日同股數的不同輪次分得開,遷移時舊出場不重排、真第二輪也不被連坐誤殺。
    seen_ids = {_canonical_id(it) for it in revisits.values()}
    new = []
    dup = 0
    for x in detect_exits(events):
        rid = _revisit_id(x)
        if rid in seen_ids:
            dup += 1
            continue
        d0 = dt.date.fromisoformat(x["exit_date"])
        swaps = infer_swaps(events, x)
        item = dict(type="revisit", revisit_id=rid, **x,
                    due={cp: (d0 + dt.timedelta(days=int(cp))).isoformat() for cp in CHECKPOINTS},
                    enqueued_at=enqueued_at,
                    swaps=swaps, idle_cash=not swaps)
        new.append(item)
        seen_ids.add(rid)                     # 同一輪內去重(detect_exits 若回同 exit 兩次)
    if new:
        lg.append_events(queue_path, new)
    return new, dup


def _backfilled_cp(item, cp):
    """#170:這關的到期日在「開始追蹤此筆(enqueued_at)」當天或更早 → 複核窗在我們看它之前就關了,
    是啟用前的歷史存量,不催(改進 backlog 供 on-demand 複習,不灌 due)。legacy 條目無 enqueued_at →
    不視為 backfill(維持舊行為;存量佇列由 owner rm 後重跑補上 enqueued_at)。"""
    enq = item.get("enqueued_at")
    if not enq:
        return False
    return dt.date.fromisoformat(item["due"][cp]) <= dt.date.fromisoformat(enq)


def scan_due(revisits, resolutions, today):
    """每筆 revisit 取「最早、非 backfill、未 resolve」的 checkpoint → due 清單。
    zero-event 誠實:沒到期就不出現,不催。#170:啟用前就過期的關(backfill)直接跳過、不佔位
    (不擋後面的關浮現)——歷史存量走 scan_backlog,不灌 due。"""
    due = []
    for rid, item in sorted(revisits.items()):
        for cp in CHECKPOINTS:
            if (rid, cp) in resolutions:
                continue                              # 這關已答過
            if _backfilled_cp(item, cp):
                continue                              # 啟用前歷史窗(#170),跳過不佔位
            d = dt.date.fromisoformat(item["due"][cp])
            if d <= today:
                due.append({"revisit_id": rid, "checkpoint": cp, "due_date": item["due"][cp],
                            "item": item})
            break                                     # 只出最早未解的一關(30 沒答不跳 60)
    return due


def _notional(item):
    """出場金額(排序用):出場價 × 賣出股數。缺欄防禦回 0。"""
    try:
        return float(item.get("exit_price") or 0) * float(item.get("shares_sold") or 0)
    except (TypeError, ValueError):
        return 0.0


def scan_recent_exits(revisits, today, window_days=RECENT_WINDOW_DAYS):
    """Return fresh exit-reason candidates, largest exit amount first.

    The review orchestrator owns capture dedup against canonical sessions.  This
    engine helper only owns the deterministic freshness window and amount sort so
    the CLI and review-v2 path cannot drift apart.
    """
    recent = [it for it in (revisits or {}).values()
              if 0 <= (today - dt.date.fromisoformat(it["exit_date"])).days <= window_days]
    recent.sort(key=lambda it: (-_notional(it), str(it.get("revisit_id"))))
    return recent


def _is_historical(item):
    """完全歷史存量(#170)= 連最後一關(90)都 backfill(啟用前就全部過期)→ 永不會進 due,歸 backlog。
    部分 backfill(30 過期但 60/90 還在未來)不算歷史:它的 60/90 之後會自然進 due,不搶進 backlog。"""
    return _backfilled_cp(item, CHECKPOINTS[-1])


def _resolved_any(rid, resolutions):
    return any((rid, cp) in resolutions for cp in CHECKPOINTS)


def scan_backlog(revisits, resolutions, prices=None, limit=5):
    """#170 冷啟動兩層的下半:啟用前的歷史出場不灌 due,改成 on-demand backlog。
    回 (backlog_topN, summary, total)。
      backlog_topN —— 金額大者先(抓大放小),已複核過(有任一 resolution)的排除;engine 先收斂到 limit 筆。
      summary —— 對「全部歷史未複核出場」的彙總洞察(選項 4):count/full/reduce/top_tickers/span 免現價必得;
        賣飛傾向(sold_before_rise/avg_hindsight_pp)只對 prices 有的算、覆蓋率(priced)誠實列,缺價不猜。
      total —— 歷史未複核出場總數(backlog 收斂前的真數,SKILL 講「還有 N 筆」用)。"""
    hist = [it for it in revisits.values()
            if _is_historical(it) and not _resolved_any(it.get("revisit_id"), resolutions)]
    hist.sort(key=lambda it: (-_notional(it), str(it.get("revisit_id"))))
    full = sum(1 for it in hist if it.get("kind") == "full")
    freq = {}
    for it in hist:
        freq[it["ticker"]] = freq.get(it["ticker"], 0) + 1
    top_tickers = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    dates = sorted(it["exit_date"] for it in hist)
    priced = sold_before_rise = 0
    ret_sum = 0.0
    for it in hist:
        px = (prices or {}).get(it["ticker"])
        if px:
            priced += 1
            r = px / it["exit_price"] - 1.0
            ret_sum += r
            if r > 0:
                sold_before_rise += 1                 # 賣掉後續漲 = 賣飛(系統性賣太早的訊號)
    summary = {"count": len(hist), "full": full, "reduce": len(hist) - full,
               "top_tickers": top_tickers,
               "span": {"first": dates[0], "last": dates[-1]} if dates else None,
               "priced": priced, "sold_before_rise": sold_before_rise,
               "avg_hindsight_pp": round(ret_sum / priced, 6) if priced else None}
    topn = [{"revisit_id": it["revisit_id"], "ticker": it["ticker"], "exit_date": it["exit_date"],
             "exit_price": it["exit_price"], "shares_sold": it["shares_sold"], "kind": it.get("kind"),
             "notional": round(_notional(it), 2), "compare": compare(it, prices)}
            for it in hist[:limit]]
    return topn, summary, len(hist)


def compare(item, prices):
    """賣飛/swap 對比(#33 swap framing)。prices={ticker: 現價};缺價 → None,列 needs_prices。
    orig_ret = 原標的出場價→現價;swap_ret = 各換入標的買入→現價的金額加權;
    swap_net_pp = swap_ret − orig_ret(>0 = 換對了;<0 = 換錯;idle → 機會成本 = orig_ret)。"""
    needs = []
    t = item["ticker"]
    px = (prices or {}).get(t)
    orig_ret = None
    if px:
        orig_ret = px / item["exit_price"] - 1.0
    else:
        needs.append(t)
    swap_ret = None
    if item.get("swaps"):
        num = den = 0.0
        complete = True
        for s in item["swaps"]:
            spx = (prices or {}).get(s["ticker"])
            amt = s["price"] * s["qty"]
            if spx is None:
                needs.append(s["ticker"])
                complete = False
                continue
            num += amt * (spx / s["price"] - 1.0)
            den += amt
        if den > 0 and complete:
            swap_ret = num / den
    swap_net = (swap_ret - orig_ret) if (swap_ret is not None and orig_ret is not None) else None
    return {"orig_ret": round(orig_ret, 6) if orig_ret is not None else None,
            "swap_ret": round(swap_ret, 6) if swap_ret is not None else None,
            "swap_net_pp": round(swap_net, 6) if swap_net is not None else None,
            "idle_cash": bool(item.get("idle_cash")),
            "needs_prices": sorted(set(needs))}


# ─────────────────────────── CLI ───────────────────────────

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    ap = argparse.ArgumentParser(description="fomo-kernel 出場 30/60/90 追蹤 + swap(#32/#33)")
    ap.add_argument("--queue", default=DEFAULT_QUEUE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_eq = sub.add_parser("enqueue-from-ledger", help="掃 ledger 出場排入 queue(去重,重跑安全)")
    p_eq.add_argument("--ledger", default=lg.DEFAULT_LEDGER)
    p_eq.add_argument("--today", default=None, help="YYYY-MM-DD 排入日(蓋 enqueued_at;預設今天;測試用)")

    p_sc = sub.add_parser("scan", help="到期的 revisit + swap 對比(JSON)")
    p_sc.add_argument("--today", default=None, help="YYYY-MM-DD(預設今天;測試用)")
    p_sc.add_argument("--prices", default=None, help='現價 JSON 檔或字串 {"NVDA": 160.0, ...}')

    p_rs = sub.add_parser("resolve", help="回答一關:still_valid / modified / falsified")
    p_rs.add_argument("revisit_id")
    p_rs.add_argument("checkpoint", choices=list(CHECKPOINTS))
    p_rs.add_argument("status", choices=list(STATUSES))
    p_rs.add_argument("--note", default=None)
    p_rs.add_argument("--date", default=None, help="回答日(預設今天)")

    a = ap.parse_args(argv)

    if a.cmd == "enqueue-from-ledger":
        today = dt.date.fromisoformat(a.today) if a.today else None
        new, _ = enqueue_from_ledger(a.ledger, a.queue, today=today)
        print(f"enqueued {len(new)} revisit(s)", file=sys.stderr)
        _emit({"enqueued": len(new), "new": new})
        return 0

    revisits, resolutions, skipped = load_queue(a.queue)
    if skipped:
        print(f"⚠️  revisit queue 有 {skipped} 行壞事件被跳過", file=sys.stderr)

    if a.cmd == "scan":
        today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
        prices = {}
        if a.prices:
            if os.path.exists(a.prices):
                with open(a.prices, encoding="utf-8") as f:
                    prices = json.load(f)
            else:
                prices = json.loads(a.prices)
        due = scan_due(revisits, resolutions, today)
        for d in due:
            d["compare"] = compare(d["item"], prices)
        # recent_exits = 賣出理由 capture(#136)的正式候選集:出場 ≤14 天、記憶還新鮮的佇列項。
        # 不能只靠 enqueue 當次的 new——session 中斷或當週限額沒問到的,窗口內下次還要能補問。
        # 金額大者先,SKILL 直接取前 2;「問過沒」由 SKILL 比對 theses.jsonl 的 exit_narrative(engine 不讀動機庫)。
        recent = scan_recent_exits(revisits, today)
        backlog, backlog_summary, backlog_total = scan_backlog(revisits, resolutions, prices)
        _emit({"due": due, "recent_exits": recent,
               "backlog": backlog, "backlog_summary": backlog_summary, "backlog_total": backlog_total,
               "pending_total": len(revisits), "resolved_total": len(resolutions),
               "skipped_lines": skipped})
        return 0

    if a.cmd == "resolve":
        if a.revisit_id not in revisits:
            print(f"❌ 不存在的 revisit_id: {a.revisit_id}", file=sys.stderr)
            return 1
        ev = {"type": "resolution", "revisit_id": a.revisit_id, "checkpoint": a.checkpoint,
              "status": a.status, "date": a.date or dt.date.today().isoformat()}
        if a.note:
            ev["note"] = a.note
        lg.append_events(a.queue, [ev])
        _emit({"resolved": a.revisit_id, "checkpoint": a.checkpoint, "status": a.status})
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
