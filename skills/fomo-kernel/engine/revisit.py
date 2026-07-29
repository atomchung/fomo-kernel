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
    出場來源有兩個、只有一個集合(#485 Slice C):trade 走勢偵測 + ledger 的 position_absence
    (使用者確認賣掉、但沒有成交紀錄)。後者 exit_price=None、帶 cost_basis 供排序用;
    「有沒有成交價」的唯一判讀點是 is_priced_exit(),缺價的算式一律不算、不假裝。

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
  python3 revisit.py enqueue-from-ledger [--ledger P] [--queue Q] [--today D] [--splits J]  # 掃出場→排入(去重,蓋 enqueued_at)
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
import splits as split_policy  # noqa: E402  # #550 分割規則單一實作(純標準庫,不連網)

DEFAULT_QUEUE = os.path.expanduser("~/.trade-coach/revisit.jsonl")
REDUCE_TH = 0.5            # 單筆賣出 ≥ 賣前持倉 50% = 大減倉,也排入(#32)
SWAP_WINDOW_DAYS = 14      # 賣出後 N 天內的買入 = 換入候選(#33 預設)
RECENT_WINDOW_DAYS = 14    # 賣出理由 capture 鮮度窗(#136):出場 ≤N 天記憶還在,才值得問「為什麼賣」
CHECKPOINTS = ("30", "60", "90")
STATUSES = ("still_valid", "modified", "falsified")


# ─────────────────────────── 出場偵測 ───────────────────────────

def detect_exits(events, splits=None):
    """從 ledger 事件流偵測出場(錨點語意與 ledger.derive_holdings 一致:
    只看最近 snapshot 之後的交易;錨點持倉當初始 shares)。
    回 [{ticker, cycle_id, exit_date, exit_price, shares_sold, kind}]。
    kind: full=清倉 / reduce=單筆賣 ≥50% 賣前持倉。同一天清倉多筆只記最後一筆(合併語意)。

<<<<<<< HEAD
    ``splits`` (#550) — this ticker's split events, in ``splits.normalize``'s
    accepted shapes. The ledger stores quantities exactly as transacted, which
    is correct and must stay that way; but a running balance accumulated across
    a split is then comparing two different share bases, and 90 bought before a
    ten-for-one split minus 100 sold after it reads as zero. That is a ~10% trim
    reported as a full liquidation — which closes the thesis permanently and
    prints "fully exited" on a saved card.

    The scaling is applied to the *running position*, at the date each split
    happened, so every comparison below is between two quantities in the same
    basis: the basis of the day the sale actually took place. That is also the
    basis the user's own broker statement for that sale is in, so ``shares_sold``
    (and therefore ``revisit_id``) stays exactly what it is today and a future
    split never re-bases an exit that is already recorded.

    This function never retrieves anything. The caller supplies the map that
    the review already fetched (``review._prepare_exit_capture`` passes the one
    frozen into ``state``), keeping this module standard-library and offline.
    Absent split data leaves the pre-existing unadjusted answer rather than a
    guessed one.

    The anchor read is ``declared_only`` (#549). "Same anchor semantics as
    ``derive_holdings``" used to be a sentence in this docstring; it is now the
    mechanism, because a CSV import writes down the book it derived. Without
    the filter this function would take that restatement as its starting
    position and swallow the very trades the row summarizes, so an exit that
    happened before it would stop being detected.
    """
    split_events = split_policy.normalize(splits)
    anchor = lg.latest_anchor(events, declared_only=True)
    shares = {}
    since = {}
    seq = {}
    # Per-ticker date the running balance is currently stated in the basis of;
    # None = nothing held yet, so a split older than the first trade multiplies
    # zero shares and cannot move anything.
    basis_at = {}
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
                # A declared position states the share count as of the anchor
                # day, so a split after it still has to be carried forward.
                basis_at[t] = anchor_date
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
        if split_events.get(t):
            factor = split_policy.factor_between(split_events[t], basis_at.get(t), d)
            if abs(factor - 1.0) > 1e-9:
                cur *= factor
                shares[t] = cur
        basis_at[t] = d
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
    # #485 Slice C:確認消失的出場沒有 sell trade,走不進上面的 trade walk。從同一個
    # detect_exits 出來,是為了讓「出場」只有一個來源集合——enqueue/recent/due/backlog/
    # horizon marker 全部沿用既有路徑,不需要各自再記得多讀一個地方(#461 明確要求)。
    # 既有 trade 出場的相對順序刻意不動(附加在尾端),避免 revisit.jsonl 追加序漂移。
    return exits + absence_exits(events)


def absence_exits(events):
    """Exit rows for confirmed disappearances recorded without a fill (#485 Slice C).

    A ``position_absence`` carries no price and no quantity by construction, so
    everything the exit pipeline needs beyond the date comes from the recorded
    book as it stood immediately before the row was appended — copied from
    ``ledger.derive_holdings``, never invented.  ``exit_price`` stays ``None``:
    the fill is genuinely unknown, and win rate, payoff and exit discipline must
    exclude the row rather than compute from a manufactured number.

    ``cost_basis`` is the recorded cost of the position that left, and it exists
    only so importance ranking has a magnitude.  It is not proceeds and no
    caller may present it as one.
    """
    rows = lg.position_absences(events)
    if not rows:
        return []
    # An absence never enters derivation, so every absence appended in one batch
    # sees the same prior book; key the cache by preceding *deriving* events and
    # the whole batch costs one derivation.
    deriving_before, seen = [], 0
    for ev in events:
        deriving_before.append(seen)
        if isinstance(ev, dict) and ev.get("type") in ("snapshot", "trade"):
            seen += 1
    cache, out = {}, []
    for row in rows:
        # The prefix bounds the rows by FILE order; the date window bounds them
        # by WHEN THEY HAPPENED. Both are needed: a trade imported before the
        # refresh ran but dated after the snapshot sits inside the prefix, and
        # counting it would report an exit larger than the position ever was on
        # the day it left. Cache on both, so one batch still costs one read.
        key = (deriving_before[row["index"]], row["date"])
        if key not in cache:
            cache[key] = lg.holdings_as_of(events[:row["index"]], row["date"])
        prior = cache[key].get(row["ticker"]) or {}
        try:
            shares = round(float(prior.get("shares")), 4)
        except (TypeError, ValueError):
            shares = 0.0
        try:
            cost = abs(float(prior.get("cost_total")))
        except (TypeError, ValueError):
            cost = None
        out.append({"ticker": row["ticker"], "cycle_id": row["cycle_id"],
                    "exit_date": row["date"], "exit_price": None,
                    "shares_sold": shares, "shares_before": shares, "kind": "full",
                    "market": str(prior.get("market") or "US"),
                    "currency": str(prior.get("currency") or "USD").upper(),
                    "cost_basis": round(cost, 2) if cost is not None else None,
                    "absence_id": row.get("absence_id")})
    out.sort(key=lambda item: (item["exit_date"], item["ticker"]))
    return out


def rebased_exit_price(item, splits=None):
    """The stored exit price restated in today's split basis (#559).

    An exit price is what actually executed, in the share basis of its own
    day. A current quote is always in today's basis. Comparing the two across
    a split reports the split as if it were a price move: on a ten-for-one, an
    exit at 950 against a 197 quote reads as -79% when the truth is +107% —
    the sign itself inverts, so "you sold before it fell" is printed about a
    position that doubled.

    Rebasing happens here, at read time, and is never stored: ``revisit_id``
    is derived from what executed and must not churn the next time the ticker
    splits. Returns ``None`` for an exit with no price (#485 Slice C), which
    is a different thing from a missing quote.
    """
    price = (item or {}).get("exit_price")
    if price is None:
        return None
    events = split_policy.normalize(splits).get((item or {}).get("ticker"))
    if not events:
        return float(price)
    try:
        day = dt.date.fromisoformat(str(item.get("exit_date")))
    except (TypeError, ValueError):
        return float(price)
    return float(price) / split_policy.factor_after(events, day)


def is_priced_exit(item):
    """Single reader for "does this exit have a recorded fill price".

    Every downstream branch that would otherwise divide by, multiply with, or
    print ``exit_price`` asks here, so an unpriced exit cannot reach one path as
    a number and another as a blank.
    """
    price = (item or {}).get("exit_price")
    if price is None:
        return False
    try:
        return float(price) > 0
    except (TypeError, ValueError):
        return False


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


def enqueue_from_ledger(ledger_path, queue_path, today=None, splits=None):
    """掃 ledger 出場 → 排入 queue(以 revisit_id 去重,重跑安全)。回 (new_items, skipped_dup)。
    new_items = 本次新排入的完整 revisit 事件——這是 SKILL 賣出理由 capture(#136)的訊號源:
    「為什麼賣」只有出場當週問得到,已在佇列的出場不重報,所以 new 非空 = 本週有新出場要問。
    #170:每筆蓋 enqueued_at(= 開始追蹤這筆的日期,預設今天;today 供測試注入)——scan 用它區分
    「啟用後才到期的 due」與「啟用前就過期的歷史存量(→ backlog,不催)」。
    #550:``splits`` 原樣傳給 detect_exits;呼叫端供給,本函式不取回任何東西。"""
    enqueued_at = (today or dt.date.today()).isoformat()
    events, _ = lg.load_ledger(ledger_path)
    revisits, _, _ = load_queue(queue_path)
    # #143:去重 key 一律用「新格式正規 id」。既有條目(含存量 legacy)先用其 cycle_id 重建 →
    # 同日同股數的不同輪次分得開,遷移時舊出場不重排、真第二輪也不被連坐誤殺。
    seen_ids = {_canonical_id(it) for it in revisits.values()}
    new = []
    dup = 0
    for x in detect_exits(events, splits=splits):
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
    """出場金額(排序備援用):出場價 × 賣出股數。缺欄防禦回 0。

    確認消失的出場沒有成交價(#485 Slice C)——退回這個部位的記錄成本當「規模」,
    否則它在每一個以金額排序的地方都等於 0,永遠排在最後、實質等於看不見。
    這是排序用的量級,不是賣出所得;任何呈現端都不准把它講成出場金額。"""
    if not is_priced_exit(item):
        try:
            return abs(float(item.get("cost_basis")))
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(item.get("exit_price") or 0) * float(item.get("shares_sold") or 0)
    except (TypeError, ValueError):
        return 0.0


def _impact_dollars(item, cmp):
    """#343:這筆出場決策對總報酬的金額影響——notional × 決策淨差,不是淨差的報酬率本身。
    有 swap:淨差 = swap_net_pp(換入 vs 原標的續抱,已含正負號,>0 = 換對了)。
    純閒置現金(無 swap):以「現金 ≈ 0 報酬」當基準,淨差 = -orig_ret——原標的續抱後續漲,
    閒置就是機會成本(負);原標的續抱後續跌,閒置反而是對的(正)。號的方向跟 swap_net_pp
    一致,兩種情況可直接比大小。任一端缺現價(needs_prices 非空)→ None,誠實不猜,
    由呼叫端退回 notional 排序。
    口徑刻意跟 trade_recap.ticker_diagnosis() 的 |impact| 排序一致(金額而非報酬率)——
    #346 已裁決「按報酬率排序」與卡片其餘所有排序邏輯(帳面損益、關鍵交易、加碼次數)
    不一致,不可在這裡重蹈。"""
    net_pp = cmp.get("swap_net_pp")
    if net_pp is None and cmp.get("idle_cash") and cmp.get("orig_ret") is not None:
        net_pp = -cmp["orig_ret"]
    if net_pp is None:
        return None
    return _notional(item) * net_pp


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


def scan_backlog(revisits, resolutions, prices=None, limit=5, splits=None):
    """#170 冷啟動兩層的下半:啟用前的歷史出場不灌 due,改成 on-demand backlog。
    回 (backlog_topN, summary, total)。
      backlog_topN —— #343 排序鍵:每筆先算 compare()(需要價才有 swap_net_pp/閒置機會成本),
        有現價可判斷決策淨差的,按金額影響 |impact_dollars| 大者先(口徑同 trade_recap 的
        |impact| 排序,金額而非報酬率——#346 已裁決報酬率排序與卡片其餘邏輯不一致);
        沒現價、判斷不了決策淨差的,退回 notional(出場金額)排序,墊在有 impact 的後面
        (未知不能假裝比已知的更值得看)。已複核過(有任一 resolution)的排除;
        engine 先收斂到 limit 筆。
      summary —— 對「全部歷史未複核出場」的彙總洞察(選項 4):count/full/reduce/top_tickers/span 免現價必得;
        賣飛傾向(sold_before_rise/avg_hindsight_pp)只對 prices 有的算、覆蓋率(priced)誠實列,缺價不猜。
      total —— 歷史未複核出場總數(backlog 收斂前的真數,SKILL 講「還有 N 筆」用)。"""
    hist = [it for it in revisits.values()
            if _is_historical(it) and not _resolved_any(it.get("revisit_id"), resolutions)]
    # compare() 要對每一筆算(不能只對「notional 前 limit 名」算)否則排在 notional 第 6+ 名、
    # 但 swap 翻轉幅度最大的那筆永遠沒機會被看見——這正是舊版排序看起來「隨便挑幾筆」的病灶。
    scored = [(it, compare(it, prices, splits=splits)) for it in hist]
    scored = [(it, cmp, _impact_dollars(it, cmp)) for it, cmp in scored]
    scored.sort(key=lambda row: (row[2] is None,
                                  -abs(row[2]) if row[2] is not None else 0.0,
                                  -_notional(row[0]), str(row[0].get("revisit_id"))))
    hist = [row[0] for row in scored]
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
        if px and is_priced_exit(it):
            priced += 1
            r = px / rebased_exit_price(it, splits) - 1.0
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
             "notional": round(_notional(it), 2),
             "impact": round(impact, 2) if impact is not None else None,
             "compare": cmp}
            for it, cmp, impact in scored[:limit]]
    return topn, summary, len(hist)


def compare(item, prices, splits=None):
    """賣飛/swap 對比(#33 swap framing)。prices={ticker: 現價};缺價 → None,列 needs_prices。
    orig_ret = 原標的出場價→現價;swap_ret = 各換入標的買入→現價的金額加權;
    swap_net_pp = swap_ret − orig_ret(>0 = 換對了;<0 = 換錯;idle → 機會成本 = orig_ret)。"""
    needs = []
    t = item["ticker"]
    px = (prices or {}).get(t)
    orig_ret = None
    unpriced = not is_priced_exit(item)
    if unpriced:
        # 沒有成交價的出場(#485 Slice C):「續抱會怎樣」的基準點不存在,不是缺現價。
        # 不進 needs_prices——那句話會叫使用者去補一個補了也算不出來的東西。
        pass
    elif px:
        orig_ret = px / rebased_exit_price(item, splits) - 1.0
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
    out = {"orig_ret": round(orig_ret, 6) if orig_ret is not None else None,
           "swap_ret": round(swap_ret, 6) if swap_ret is not None else None,
           "swap_net_pp": round(swap_net, 6) if swap_net is not None else None,
           "idle_cash": bool(item.get("idle_cash")),
           "needs_prices": sorted(set(needs))}
    if unpriced:
        # Only stamped when true: every priced exit's comparison stays the exact
        # object shape the plan, the card and the fixtures already carry.
        out["unpriced_exit"] = True
    return out


# ─────────────────────────── CLI ───────────────────────────

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json_arg(value):
    """CLI JSON argument: a path if one exists, otherwise the literal payload."""
    if not value:
        return None
    if os.path.exists(value):
        with open(value, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(value)


def main(argv=None):
    ap = argparse.ArgumentParser(description="fomo-kernel 出場 30/60/90 追蹤 + swap(#32/#33)")
    ap.add_argument("--queue", default=DEFAULT_QUEUE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_eq = sub.add_parser("enqueue-from-ledger", help="掃 ledger 出場排入 queue(去重,重跑安全)")
    p_eq.add_argument("--ledger", default=lg.DEFAULT_LEDGER)
    p_eq.add_argument("--today", default=None, help="YYYY-MM-DD 排入日(蓋 enqueued_at;預設今天;測試用)")
    p_eq.add_argument("--splits", default=None,
                      help='分割事件 JSON 檔或字串 {"NVDA": [["2024-06-10", 10]]}(#550;'
                           '不給 = 不調整,沿用名目股數)')

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
        try:
            supplied_splits = _load_json_arg(a.splits)
        except (OSError, ValueError) as exc:
            print(f"❌ 分割事件檔讀不了:{exc}", file=sys.stderr)
            return 1
        try:
            new, _ = enqueue_from_ledger(a.ledger, a.queue, today=today,
                                         splits=supplied_splits)
        except split_policy.SplitDataError as exc:
            # 分割比率是股數的乘數:壞值靜默丟掉 = 端出一個自信的錯數字,正是 #550 的病。
            print(f"❌ 分割事件不合格:{exc}", file=sys.stderr)
            return 1
        print(f"enqueued {len(new)} revisit(s)", file=sys.stderr)
        _emit({"enqueued": len(new), "new": new})
        return 0

    revisits, resolutions, skipped = load_queue(a.queue)
    if skipped:
        print(f"⚠️  revisit queue 有 {skipped} 行壞事件被跳過", file=sys.stderr)

    if a.cmd == "scan":
        today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
        prices = _load_json_arg(a.prices) or {}
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
