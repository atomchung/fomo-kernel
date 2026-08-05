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
reconcile 後追加的新 snapshot(新錨點)承擔,避免雙重套用。reconciliation 事件是
乾淨對帳的標記(宣告與推導一致,#220),同樣不進推導。position_absence(#485 Slice C)
同樣不進推導:它記的是「使用者確認某日起這檔不在帳上」,沒有成交價與股數可寫,
持倉變更一樣由同批的新錨點承擔;它存在的理由是讓出場管線(revisit)讀得到這次出場。
二次宣告的窄 diff 契約見 snapshot_reconciliation() docstring 與 docs/prd-ledger.md。

CLI(SKILL 消費;JSON 走 stdout、人話訊息走 stderr,對齊 TR_JSON 模式):
  python3 ledger.py holdings        [--ledger P]                      # 推導當前持倉+integrity
  python3 ledger.py append-snapshot POS.json [--as-of D] [--source S] [--cash JSON] [--ledger P]
  python3 ledger.py append-trades   STD.csv  [--ledger P]             # 自動去重(重疊期重複匯入安全)
  python3 ledger.py reconcile       POS.json [--ledger P]             # 宣告 vs 推導 diff(唯讀)
  python3 ledger.py doctor          [--ledger P]                      # 唯讀診斷壞行,絕不推導(#462)

#462:load_ledger() 預設 strict=True——訂單相依的帳本(snapshot 錨點 + 依序疊加的 trades)
缺一行不是缺一筆,是後面每一個推導出的股數/均價都可能錯一個數字且無法從結果本身看出來。
壞行只在 strict=False(唯一消費者是 `doctor` 子指令/讀 legacy 資料的遷移工具)才回報而不拋出;
derive_holdings/latest_anchor 等推導函式因此永遠只吃得到「乾淨或已被拒收」的 events,
不需要各自重複檢查——見 docs/development-guide.md §7(單一 gate,而非多處各自防)。
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import defaultdict

import splits as sp
import symbols

SCHEMA_V = 1
DEFAULT_LEDGER = os.path.expanduser("~/.trade-coach/ledger.jsonl")
EPS = 1e-6
SHARES_TOL = 1e-4          # reconcile 股數容差(對齊事件 round 精度:qty round4)
CASH_TOL = 0.005           # cash / avg-cost absolute tolerance (broker cent rounding)
EVENT_TYPES = ("snapshot", "trade", "adjustment", "reconciliation", "position_absence")

# position_absence(#485 Slice C):使用者確認「這檔賣掉了」但沒有成交紀錄時,唯一能誠實
# 寫下的事實 —— 某日起這檔不在帳上。成交價與股數是未知的,所以這個事件「結構上」就沒有
# 放它們的欄位:build_position_absence() 自己驗證輸出鍵集合,想加價格欄位的人會讓 builder
# 直接爆掉(而不是靠一條註解或一個測試提醒)。這條線跟 condition-check.schema.json 拒收
# 引擎自寫 user_response 是同一條:勝率/盈虧比/出場紀律永遠不准從捏造的數字算出來。
# 它跟 adjustment 一樣不進 derive_holdings 推導——持倉的實際變更由同批寫入的新錨點承擔,
# 這個事件是「出場管線讀得到的訊號」+ 稽核留痕,避免雙重套用。
# 一個 snapshot 持倉列允許的欄位,單一宣告。三個讀者共用同一份:snapshot_adapter
# 驗證 agent 供給的信封、portfolio_basis 兩處判斷「這個事件動不動得了帳本」與契約
# 驗證。曾經是三份手抄白名單——#485 Slice C 加 carried 時只改到其中一份,結果
# refresh 寫出來的錨點讓 portfolio_basis 整本判成不可信,consider 直接拒答,而
# 全套件是綠的。維護端防線要 agent-free:共用程式碼 > 測試鎖 > 文件。
# carried 是來源註記(這一列是從既有紀錄帶過來的,不是這次供給的畫面上讀到的),
# 不影響帳本內容,所以它進得了白名單、但不進 _normalized_anchor 的身分計算。
# since / since_basis(#531)同理:一檔在新宣告裡「冒出來」的持倉沒有來歷,使用者
# 被問了大約持有幾個月,引擎(不是 agent)把月數換算成開倉日並蓋章。兩個鍵成對出現,
# 由 snapshot_adapter 強制:since 一定伴隨 since_basis="user_estimate",所以讀得到日期
# 的人一定同時讀得到「這是估的」——「推算出來的開倉日不得被當成精確日期呈現」在儲存層
# 就成立,不必靠每個 renderer 自律。since_basis="unknown"(使用者說不知道)不帶 since,
# derive_holdings 下面把它變成既有的 ticker#unknown cycle。
# cycle_seq(#539)是 cycle_id 的第三段:同一天內清掉再買回的兩個 cycle 只差這個序號,
# 所以帶了 since 卻不帶序號的採納會把還持有的部位配上「已經賣掉那個 cycle」的 id。
SNAPSHOT_POSITION_KEYS = frozenset({
    "ticker", "shares", "avg_cost", "market_value", "market", "currency", "carried",
    "since", "since_basis", "cycle_seq", "add_count",
})
# 引擎指派、永遠不收 agent 供給的持倉欄位(AGENTS.md 不可協商邊界 2:數字來自引擎產物)。
# snapshot_adapter.normalize_envelope 預設拒收它們;只有 book_refresh 採納自己剛問到的
# 答案時才開鎖,而那一條路上的值是引擎自己算出來的。
ENGINE_ASSIGNED_POSITION_KEYS = frozenset({"since", "since_basis", "cycle_seq", "add_count"})
# since_basis 說的是「這個日期憑什麼被相信」,不是「它是怎麼被搬過來的」
# (#539 owner ruling 2026-07-31)。四個值各是一種證據強度,採納時原樣帶過去,不合併:
#   "trade_event"     = 帳本親眼看著這個 cycle 開的,精確日期。
#   "snapshot_anchor" = 某次宣告第一次把它記進帳本的那天,是下界,不是買進日。
#   "user_estimate"   = 從使用者給的月數換算(近似,±半個月)。
#   "unknown"         = 使用者說不知道,不編日期。
# 後兩個是舊有的;前兩個是 #539 新增。一檔從沒被問過「抱多久」的持倉(第一次宣告裡
# 的每一檔都是)本來沒有任何蓋章可帶,於是每次採納都被退回成最新宣告日,cycle_id 跟著
# 重鑄,使用者寫過的 thesis 被重問一次。但用單一個「從帳本帶過來的」值蓋掉全部會抹掉
# 精確度差異,而且事後補不回來:採納之後 origin 描述的是新的 snapshot 寫入者,不再是
# 這個起點原本的證據。所以帶的是原本那個值本身。
SINCE_BASES = ("user_estimate", "unknown", "trade_event", "snapshot_anchor")

# A snapshot row's ``source`` says how the book it states was learned (#549).
# It is recorded, never used to decide whether the row counts as the recorded
# book: owner ruling 2026-07-29 — "we always take what the user gave us as their
# current positions", so a transaction export and a holdings view are equal on
# that axis and only differ in when each arrived.
DECLARED_BOOK_SOURCE = "user_declared"
DERIVED_BOOK_SOURCE = "trades_derived"

ABSENCE_IDENTITY_KEYS = ("type", "date", "ticker", "cycle_id")
ABSENCE_KEYS = frozenset(ABSENCE_IDENTITY_KEYS) | {"absence_id", "session_id", "v", "recorded_at"}
# 任何帶「成交長什麼樣」語意的欄位名;出現在 ABSENCE_KEYS 或 builder 產出即為契約破口。
ABSENCE_FORBIDDEN_KEYS = frozenset({
    "price", "exit_price", "qty", "quantity", "shares", "shares_sold",
    "amount", "proceeds", "value", "market_value", "avg_cost", "cost", "fee",
})


class LedgerIntegrityError(ValueError):
    """Raised by load_ledger(path) (default strict=True) when the ledger has
    one or more unreadable rows (#462).

    ``ledger.jsonl`` is order-dependent and append-only: a dropped row is not
    a missing fact, it is a wrong downstream number (a different share count
    or average cost) with nothing in the output to signal it happened. So the
    default is to refuse rather than to compute over a gap. ``issues`` carries
    the same per-row detail the ``doctor`` CLI command reports, so a caller
    that wants to show *what* is wrong can read it off the exception instead
    of re-scanning the file.
    """

    def __init__(self, message, issues):
        super().__init__(message)
        self.issues = issues


# ─────────────────────────── 讀寫 ───────────────────────────

def _scan_ledger(path):
    """單一事實源:逐行掃描 ledger.jsonl → (events, issues)。

    issues 元素 = {"line": 行號(1-based), "reason": "invalid_json"|"unknown_type"}。
    load_ledger() 的 strict gate 與 `doctor` 子指令的診斷報告都只讀這個函式的結果,
    兩邊不會對「什麼算壞行」各自表述、彼此漂移(development-guide.md §7)。"""
    events, issues = [], []
    if not os.path.exists(path):
        return events, issues
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                issues.append({"line": lineno, "reason": "invalid_json"})
                continue
            if not isinstance(ev, dict) or ev.get("type") not in EVENT_TYPES:
                issues.append({"line": lineno, "reason": "unknown_type"})
                continue
            events.append(ev)
    return events, issues


def load_ledger(path, *, strict=True):
    """讀 ledger.jsonl → (events, skipped)。

    strict=True(預設):只要 _scan_ledger 回報任何壞行,整批 raise
    LedgerIntegrityError——holdings/reconcile/consider 等每一個推導路徑用的都是
    這個預設值,壞行因此永遠無法無聲流進任何算出來的數字(#462;#50 精神的延伸:
    讀入/跳過要可見,不靜默——不夠,訂單相依的帳本必須連「靜默算錯」都不許發生)。

    strict=False:診斷/遷移專用(`ledger.py doctor`、未來的 legacy 資料檢視工具),
    只回報壞行數、絕不拋出——呼叫端自己決定怎麼呈現,但不准把回傳的 events 拿去
    derive_holdings/latest_anchor:那正是這個旗標存在的唯一理由是「看見問題」,
    不是「繞過問題」。
    """
    events, issues = _scan_ledger(path)
    if strict and issues:
        detail = "; ".join(f"line {row['line']} ({row['reason']})" for row in issues[:5])
        more = f"; +{len(issues) - 5} more" if len(issues) > 5 else ""
        raise LedgerIntegrityError(
            f"{path} has {len(issues)} unreadable row(s) ({detail}{more}). "
            "Refusing to derive holdings or any other number from an incomplete "
            "ledger (#462) — run `python3 ledger.py doctor --ledger "
            f"{path}` to inspect without deriving anything.",
            issues,
        )
    return events, len(issues)


def append_events(path, events, *, recorded_at=None):
    """append-only 寫入;每 event 補 schema version。回傳寫入筆數。

    recorded_at(#472,opt-in,無隱性退回值):此系統得知這筆事實的日期(ISO 字串)
    ——與 event 自帶的 date/as_of(事情何時發生)是刻意分開的兩個日期,同
    thesis.py/review.py `_build_exit_narratives` 既有的 recorded_at 語意(那裡從
    review period 的 date_end 取值)。呼叫端已知這個日期時才傳入(如 review.py
    `_ingest_trades` 傳 review period 的 date_end;ledger.py 自己的 CLI 在呼叫處
    自行組 `dt.date.today().isoformat()` 傳入,因為那是唯一真正沒有 review
    context、wall-clock 才是誠實答案的路徑)。

    這個函式是 problems.py/revisit.py 共用的寫入路徑,不是 ledger.jsonl 專屬——
    這兩個 store 各自可能已有自己的「何時記錄」欄位(revisit.py 的
    enqueued_at,有自己的讀者與 legacy 缺席語意)。這裡若對「沒傳 recorded_at」
    退回 dt.date.today(),等於在它們身上蓋一個同義、卻是猜出來的第二個日期
    欄位——同一個概念兩個名字,正是 #472 本文警告的漂移,只是搬到了另一個檔案
    (曾經發生過一次,已改回 opt-in)。所以:不傳就不蓋欄位,setdefault 只在
    recorded_at 非 None 時才呼叫;沒有 review context 的呼叫端讓事件維持缺席
    =未知,好過蓋一個猜出來的日期。

    舊資料(#472 之前寫入)也沒有這個欄位——讀回時 recorded_at 缺席必須代表
    「未知」,絕不能是猜出來或回填的值(見 load_ledger/_scan_ledger:未知/缺席
    欄位本來就容忍,只有壞行才拒收)。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ev in events:
            ev = dict(ev)
            ev.setdefault("v", SCHEMA_V)
            if recorded_at is not None:
                ev.setdefault("recorded_at", recorded_at)
            f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
    return len(events)


# ───────────────────── 確認消失(position_absence)─────────────────────

def _absence_identity(date, ticker, cycle_id):
    """Validated identity tuple for one confirmed disappearance; fails closed."""
    try:
        day = dt.date.fromisoformat(str(date)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"position_absence has an invalid date: {date!r}") from exc
    if not ticker or not isinstance(ticker, str):
        raise ValueError("position_absence requires a ticker")
    if not cycle_id or not isinstance(cycle_id, str):
        raise ValueError(f"position_absence for {ticker} requires a cycle_id")
    return {"type": "position_absence", "date": day, "ticker": ticker, "cycle_id": cycle_id}


def build_position_absence(*, date, ticker, cycle_id, session_id=None):
    """Build one `position_absence` event (content-addressed, fill-free).

    ``absence_id`` hashes only ABSENCE_IDENTITY_KEYS, so re-running the same
    confirmation is the same row and finalize stays idempotent the way
    ``snapshot_id``/``adjustment_id`` already are.

    The returned key set is checked against ABSENCE_KEYS and
    ABSENCE_FORBIDDEN_KEYS here, in the writer itself, rather than in a test:
    adding a price or share field to this event makes every caller raise
    immediately instead of quietly persisting a manufactured fill that win
    rate, payoff and exit discipline would then compute from.
    """
    identity = _absence_identity(date, ticker, cycle_id)
    event = dict(identity)
    event["absence_id"] = "absence-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    if session_id is not None:
        event["session_id"] = str(session_id)
    unknown = set(event) - ABSENCE_KEYS
    if unknown:
        raise ValueError("position_absence has unknown fields: " + ", ".join(sorted(unknown)))
    forbidden = (set(event) | ABSENCE_KEYS) & ABSENCE_FORBIDDEN_KEYS
    if forbidden:
        raise ValueError(
            "position_absence must not carry fill facts: " + ", ".join(sorted(forbidden)))
    return event


def position_absences(events):
    """Well-formed `position_absence` rows with their index, in ledger order.

    The index is what a reader needs to see the recorded book as it stood
    immediately before the row was appended — the only honest source for the
    shares, currency and cost basis the event itself deliberately omits.
    """
    out = []
    for index, ev in enumerate(events):
        if not isinstance(ev, dict) or ev.get("type") != "position_absence":
            continue
        try:
            identity = _absence_identity(ev.get("date"), ev.get("ticker"), ev.get("cycle_id"))
        except ValueError:
            continue          # 壞行語意跟 bad_trade_event 一致:跳過,不讓它變成錯的推導
        out.append({"index": index, "absence_id": ev.get("absence_id"), **identity})
    return out


# ─────────────────────────── 推導 ───────────────────────────

def latest_anchor(events, *, declared_only=False):
    """Return the latest recorded book row.

    Every ``type: "snapshot"`` row is one, whatever its ``source`` and whoever
    wrote it (#549).  Nothing has to qualify: the book is simply what the
    system last recorded, and a transaction import records it as legitimately
    as a holdings view does.  Before #549 this skipped rows the user had marked
    as covering only part of the account, which made a helpful answer
    disqualify the book from ever updating; #485 had already abolished the
    concept that skip rested on.

    ``declared_only`` answers the one different question in the system: which
    row *re-bases the replay* in :func:`derive_holdings`.  A
    ``DERIVED_BOOK_SOURCE`` row restates a book this ledger's own trades
    already produce, so replaying those trades reproduces it exactly, while
    re-basing on it would discard the cycle starts, cycle sequence and add
    counts the trades carry and the summary row cannot.  Expressed as one
    argument on one function rather than a second selector, so the two
    questions cannot drift apart about ordering or validity
    (development-guide.md §7).  Its callers are exactly the readers that
    reconstruct the book from the same events: ``derive_holdings``,
    ``portfolio_basis.query_current_book``, ``review._rows_from_ledger``, and
    ``revisit.detect_exits`` — each of which already states in its own
    docstring that it follows ``derive_holdings``' anchor semantics, and now
    does so by calling the same function rather than by intention.

    Same-day adapter projections carry a root-wide monotonic
    ``projection_sequence``.  When both candidates have one, the higher
    sequence is the newer declaration even if projection repair appended the
    JSONL rows in a different order.  Old hand-written/CLI rows have no
    sequence, so their long-standing file-order tie-break remains unchanged.
    """
    best, best_date, best_index, best_sequence = None, None, None, None
    for i, ev in enumerate(events):
        if ev.get("type") != "snapshot":
            continue
        if declared_only and ev.get("source") == DERIVED_BOOK_SOURCE:
            continue
        try:
            d = dt.date.fromisoformat(str(ev.get("as_of")))
        except (TypeError, ValueError):
            continue
        raw_sequence = ev.get("projection_sequence")
        sequence = (raw_sequence if isinstance(raw_sequence, int)
                    and not isinstance(raw_sequence, bool) and raw_sequence > 0 else None)
        if best is None or d > best_date:
            best, best_date, best_index, best_sequence = ev, d, i, sequence
            continue
        if d < best_date:
            continue
        if sequence is not None and best_sequence is not None:
            if sequence > best_sequence or (sequence == best_sequence and i > best_index):
                best, best_index, best_sequence = ev, i, sequence
        elif sequence is not None:
            # A sequence-bearing adapter projection was finalized after the
            # root's legacy rows and remains ordered even if repair appends an
            # older legacy row later.
            best, best_index, best_sequence = ev, i, sequence
        elif best_sequence is None and i > best_index:
            # All-legacy same-day rows retain the historical file-order rule.
            best, best_index = ev, i
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


def _anchored_cycle_start(position, anchor_date, ticker, integrity):
    """一列錨點持倉的 cycle 起點,回 (since_iso, cycle_unknown)。

    預設仍是錨點日 —— 一筆宣告出來的持倉,帳本只知道它「至少從這天起在帳上」。
    #531 之後,refresh 問過「大約抱多久」的持倉會帶 since/since_basis 蓋章:
    ``user_estimate`` 用蓋章的日期,``unknown`` 保持錨點日但把 cycle 標成 unknown。
    #539 之後,採納一份新宣告時引擎會把帳本本來就記著的起點連同它原本的證據強度
    (``trade_event`` / ``snapshot_anchor``)一起帶過來。這裡對三個帶日期的值讀法
    完全相同 —— 差別是「這個日期憑什麼被相信」,那屬於帳本的誠實,不屬於這裡的算法。

    本函式對壞值一律降級回錨點日並記 integrity,不 raise:ledger.jsonl 是可被手改的
    append-only 檔案,而 derive_holdings 對壞資料的既有契約是「照走、但看得見」
    (bad_avg_cost / oversell 前例),不是整本拒讀。
    """
    default = anchor_date.isoformat()
    basis = position.get("since_basis")
    if basis is None:
        if position.get("since") is not None:
            # 有日期沒蓋章 = 成對不變式被繞過(手改或某條沒走 snapshot_adapter 的路)。
            # 忽略它:一個沒有「這是估的」旁證的日期,正是規則 2 要擋的假精確。
            integrity.append({"issue": "unstamped_since", "ticker": ticker})
        return default, False
    if basis not in SINCE_BASES:
        integrity.append({"issue": "bad_since_basis", "ticker": ticker})
        return default, False
    if basis == "unknown":
        return default, True
    raw = position.get("since")
    try:
        stated = dt.date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        integrity.append({"issue": "bad_since", "ticker": ticker})
        return default, False
    if stated > anchor_date:
        integrity.append({"issue": "bad_since", "ticker": ticker})
        return default, False
    return stated.isoformat(), False


def cycle_sequence(cycle_id):
    """The sequence segment of a ``cycle_id``, or None when it carries none.

    The inverse of the one composition below, kept beside it so the two cannot
    drift, and the only reader: a second place that split this string would be
    the divergent-derivation shape this repository keeps closing. ``#unknown``
    and anything unparseable answer None — a caller with no sequence to carry
    must be told so rather than handed a guess.
    """
    parts = str(cycle_id or "").split("#")
    if len(parts) != 3:
        return None
    try:
        seq = int(parts[2])
    except (TypeError, ValueError):
        return None
    return seq if seq > 0 else None


def _anchored_cycle_seq(position, ticker, integrity):
    """一列錨點持倉的 cycle 序號,預設 1(#539)。

    ``since`` 只釘住 cycle 的起點;同一天清倉再買回的兩個 cycle 起點相同,只差序號。
    採納一份新宣告時若只帶 since 不帶序號,還持有的那個 cycle 會拿到「已經賣掉那一段」
    的 id,連同它的 thesis、conditions 和結案狀態一起繼承過來 —— 比重鑄更糟。

    與 ``_anchored_cycle_start`` 同一個契約:壞值降級回預設並記 integrity,不 raise。
    """
    raw = position.get("cycle_seq")
    if raw is None:
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        integrity.append({"issue": "bad_cycle_seq", "ticker": ticker})
        return 1
    return raw


def _anchored_add_count(position, ticker, integrity):
    """The engine-known add sequence an adopted anchor may carry (#660).

    A snapshot never declares this number: ``snapshot_adapter`` admits it only
    on ``book_refresh``'s engine-provenance path.  It is still a durable ledger
    row, though, so a hand-edited legacy row must not turn a later derivation
    into a type error or a fabricated cursor.  The established ledger posture
    for a malformed carried field is to keep the book readable, name the
    integrity gap, and fall back to the legacy zero-count behavior.
    """
    raw = position.get("add_count")
    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        integrity.append({"issue": "bad_add_count", "ticker": ticker})
        return 0
    return raw


def _moved_basis(position, events, after, upto):
    """Record the newest split this rebase actually applied to ``position``.

    The book's own quantity basis (#583 §3): a share count that a split moved is
    stated in that split's basis, not in the basis of the last trade. Recorded
    where the rebase happens rather than re-derived afterwards, because a second
    walk over the same events is the divergent-derivation shape.
    """
    moved = sp.last_applied(events, after, upto)
    if moved is not None and (position.get("basis_moved") is None
                              or moved > position["basis_moved"]):
        position["basis_moved"] = moved


def _carry_position(position, events, day):
    """Carry a running position into ``day``'s split basis, in place (#558).

    Shares scale by every split in ``(basis_date, day]``; ``cost_total`` is
    untouched because a split is a zero-dollar event. The position's basis
    moves to ``day``, so the next carry starts where this one left off and no
    split is ever applied twice.
    """
    factor = sp.factor_between(events, position.get("basis_date"), day)
    if abs(factor - 1.0) > 1e-12:
        position["shares"] *= factor
        _moved_basis(position, events, position.get("basis_date"), day)
    position["basis_date"] = day


def derive_holdings(events, splits=None, as_of=None):
    """錨點推導當前持倉。回傳 {anchor, holdings, quantity_basis, integrity, counts}。

    ``quantity_basis`` (#583) — the newest split date that actually moved a
    quantity still held, or ``None``. The single statement of what basis this
    book's share counts are on, for the consumers that have to compare them
    against a price observation with a basis of its own.

    holdings: {ticker: {shares, avg_cost(None=未宣告且不可知), cost_total, currency,
                        market, origin(snapshot|trades), since, cycle_id,
                        add_count, decision_cursor}}
    integrity: 壞事件 / oversell(賣超,clamp 後照走)清單 —— 資料誠實層,呈現端要帶出。

    ``splits`` (#558) — this ticker's split events, in ``splits.normalize``'s
    accepted spellings. Every quantity below is stored as transacted, in the
    basis of its own day, so a running balance accumulated across a split is
    not a balance at all: 90 bought before a ten-for-one split and 100 sold
    after it cannot be subtracted from each other. The rule lives in
    ``engine/splits.py`` and is applied here at three points — the anchor's
    declared shares, each trade as it arrives, and once more at the tail — so
    that every comparison happens in the basis of the moment it describes.
    ``None`` means no split information and reproduces the pre-#558 answer
    exactly; it does not mean "no splits".

    ``as_of`` bounds the tail rebase for a caller reading the book through an
    earlier day (:func:`holdings_as_of`). Omitted, every known split applies,
    which is what "the book as it stands today" means — and is the case with
    no post-split trading at all, the most common one in the wild.

    ``cost_total`` is deliberately untouched by every one of those rebases: a
    split is a zero-dollar event, so preserving cost while shares move is what
    makes the derived ``avg_cost`` fall out correct without a second
    adjustment. ``since`` is likewise untouched — a split does not restart a
    holding period.

    The replay re-bases on the latest *declaration* only (#549). A
    ``DERIVED_BOOK_SOURCE`` row is this function's own output written down, so
    replaying the trades it summarizes returns the identical book — that
    equality is gated in tests/test_ledger.py — while re-basing on it would
    silently drop what a summary row cannot carry: the real cycle start, the
    cycle sequence, and the add count. One definition of what is held, one
    place it is computed.
    """
    split_events = sp.normalize(splits)
    anchor = latest_anchor(events, declared_only=True)
    integrity = []
    pos = {}
    # #803/#814: the book is keyed canonically from here down — a legacy `nvda`
    # row lands on the `NVDA` it names instead of beside it — and `cycle_id` is
    # minted from that same canonical key. One instrument, one identity, one
    # sequence, in every lane. Deriving the id from a *spelling* is what let this
    # lane and the CSV review lane mint two ids for one cycle (#814): the review
    # recorded a thesis under the export's spelling while every ledger-backed
    # reader here wrote the canonical one, so the exit never closed the thesis.
    seq_base = defaultdict(int)      # ticker → 最後用過的 cycle 序號(清倉後保留,重建 +1)
    anchor_date = None

    if anchor is not None:
        anchor_date = dt.date.fromisoformat(str(anchor["as_of"]))
        declared_as = {}                 # canonical → this declaration's own spelling
        for p in anchor.get("positions", []):
            # Canonical before the guard below, never after it (#803): a
            # non-string ticker is exactly the malformed row that guard exists
            # for, and canonicalizing past it would key the book on `None`.
            t = symbols.canonical_ticker(p.get("ticker")) if isinstance(p, dict) else None
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
            # #803. One declaration may not hold the same instrument under two
            # different spellings: which of the two share counts is the position
            # is not derivable from the rows, and collapsing them would silently
            # state a holding the user never declared. `bad_` fails the whole
            # book closed through `portfolio_basis._bad_integrity`, naming both.
            # A ticker declared twice under the *same* spelling is a different,
            # older case and keeps its existing last-one-wins overwrite, which
            # `review._rows_from_ledger` deliberately mirrors.
            declared = p.get("ticker")
            if declared_as.setdefault(t, declared) != declared:
                integrity.append({"issue": "bad_ticker_collision", "ticker": t,
                                  "detail": f"declared as {declared_as[t]!r} and {declared!r}"})
                continue
            ac = p.get("avg_cost")
            try:
                cost_total = float(ac) * sh if ac is not None else None
            except (TypeError, ValueError):
                cost_total = None
                integrity.append({"issue": "bad_avg_cost", "ticker": t})
            since, cycle_unknown = _anchored_cycle_start(p, anchor_date, t, integrity)
            pos[t] = {"shares": sh, "cost_total": cost_total,
                      "currency": p.get("currency", "USD"), "market": p.get("market", "US"),
                      "origin": "snapshot", "since": since,
                      "cycle_unknown": cycle_unknown,
                      "add_count": _anchored_add_count(p, t, integrity),
                      # A declaration states shares in its own as_of basis (#558).
                      "basis_date": anchor_date}
            # cycle 序號單一事實源:seq_base(清倉後仍保留,重建 +1)。錨點列帶得動它時
            # 就沿用,否則 1 —— 一份沒有序號的宣告本來就說不出它是第幾段(#539)。
            seq_base[t] = _anchored_cycle_seq(p, t, integrity)

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
        # #803. Two spellings of one symbol are one instrument's executions, so
        # a legacy `nvda` buy adds to the declared `NVDA` rather than opening a
        # phantom position beside it. Unlike two declarations of one holding,
        # there is nothing ambiguous to refuse here: a trade list is a sequence
        # of fills, and applying them all in order is the only reading.
        executed_as, t = t, symbols.canonical_ticker(t)
        if t is None:
            integrity.append({"issue": "bad_trade_event",
                              "detail": json.dumps(ev, ensure_ascii=False)[:120]})
            continue
        # The spelling travels with the fill rather than being collapsed here:
        # which cycle a spelling belongs to is only decidable in date order,
        # below, and folding it now is exactly the whole-history `setdefault`
        # #807 replaced.
        trades.append((d, t, act, qty, px, ev, executed_as))
    trades.sort(key=lambda x: x[0])       # stable:同日保持匯入序

    for d, t, act, qty, px, ev, executed_as in trades:
        cur = pos.get(t)
        if cur is not None:
            # #558: put the running position in this trade's own basis before
            # adding to or subtracting from it. Without this the two sides of
            # the arithmetic are quantities from different days.
            _carry_position(cur, split_events.get(t), d)
        if act == "buy":
            if cur is None or cur["shares"] <= EPS:
                seq_base[t] += 1
                pos[t] = {"shares": qty, "cost_total": qty * px,
                          "currency": ev.get("currency", "USD"), "market": ev.get("market", "US"),
                          "origin": "trades", "since": d.isoformat(),
                          "add_count": 0, "basis_date": d}
            else:
                cur["shares"] += qty
                cur["add_count"] += 1
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

    # #558 tail: a split after the last trade — or after the anchor, for a book
    # with no trades in that ticker at all — still moves the share count. This
    # is the most common shape in the wild: the user simply has not traded it
    # since. ``as_of`` bounds the window for a caller reading an earlier day;
    # omitted, every known split applies, which is what "as it stands" means.
    for t, p in pos.items():
        events_t = split_events.get(t)
        if not events_t:
            continue
        basis = p.get("basis_date")
        factor = (sp.factor_after(events_t, basis) if as_of is None
                  else sp.factor_between(events_t, basis, as_of))
        if abs(factor - 1.0) > 1e-12:
            p["shares"] *= factor
            _moved_basis(p, events_t, basis, as_of)

    holdings = {}
    for t in sorted(pos):
        p = pos[t]
        if round(p["shares"], 4) <= 0:     # 微量殘股 round 後歸零 → 不列(避免 shares=0.0 的幽靈持倉)
            continue
        ac = (p["cost_total"] / p["shares"]) if (p["cost_total"] is not None and p["shares"] > EPS) else None
        # 使用者說不知道抱多久 → 沿用既有的兩段式 ticker#unknown,horizon._cycle_start
        # 會解成 None,那檔就退出持有期診斷,而不是被編一個日期進去(#531 owner ruling)。
        # `since` 仍留錨點日:它本來就是「這檔何時進到帳本」的記帳事實,而 cycle_id 才是
        # 持有期的量測基準,所以下游每個讀 since 的人拿到的仍是合法日期。
        # #814: minted from the canonical key, the same one every other lane
        # uses. A cycle_id derived from a spelling is what let this lane and the
        # CSV review lane file one cycle under two ids, so the exit recorded here
        # never closed the thesis the review had written.
        cycle_id = (f"{t}#unknown" if p.get("cycle_unknown")
                    else f"{t}#{p['since']}#{seq_base[t]}")
        add_count = p.get("add_count", 0)
        holdings[t] = {"shares": round(p["shares"], 4),
                       "avg_cost": round(ac, 4) if ac is not None else None,
                       "cost_total": round(p["cost_total"], 2) if p["cost_total"] is not None else None,
                       "currency": p["currency"], "market": p["market"],
                       "origin": p["origin"], "since": p["since"],
                       "cycle_id": cycle_id, "add_count": add_count,
                       "decision_cursor": f"{cycle_id}#add#{add_count}" if add_count else None}
    # #583 §3. The newest split that actually moved a quantity still held. A
    # book whose last trade is old but whose share count a split restated last
    # month is not a month-old book in the only sense that matters to a price
    # comparison, and `PortfolioBasis` was calling it one: its freshness date
    # came from the anchor and the trades alone, so a valuation dated between
    # the last trade and the split passed its own `reference_as_of` gate while
    # the holdings had already moved into the later basis. `None` for every book
    # with no split information and every book no split touched — which is what
    # keeps their `state_version` exactly where it was.
    quantity_basis = max((p["basis_moved"] for t, p in pos.items()
                          if t in holdings and p.get("basis_moved")), default=None)
    return {"anchor": ({"as_of": anchor.get("as_of"), "source": anchor.get("source", "user_declared")}
                       if anchor is not None else None),
            "holdings": holdings,
            "quantity_basis": quantity_basis.isoformat() if quantity_basis else None,
            "integrity": integrity,
            "counts": {"events": len(events),
                       "trades_applied": len(trades),
                       "positions": len(holdings)}}


def build_derived_book(events, *, as_of, session_id=None, splits=None):
    """The snapshot row that writes down the book these events already produce.

    Owner ruling on #549: *every source that arrives records the book at its own
    time*.  Before this, a transaction import left trade rows and no statement of
    what the system concluded was held, so the next source to arrive — a holdings
    view the user wanted to correct the book with — had no predecessor to update
    and both doors refused, each pointing at the other.  A holdings view already
    wrote such a row; a CSV import now writes one too, and ``source`` records
    which of the two it was.  That distinction is worth recording and is never
    used to decide whether a row counts: :func:`latest_anchor` reads them alike.

    Returns ``None`` when the events produce no positions.  A root with no
    history at all therefore still records nothing and still opens through
    onboarding — the invariant #549 requires to survive.

    ``as_of`` is when the book is being recorded, and it may not precede the
    newest trade the row summarizes: a book dated before its own facts would
    make :func:`snapshot_reconciliation` refuse a holdings view that is actually
    newer.

    No ``cash``: this lane learns positions, never a balance.  Copying the last
    declared balance forward would state a declaration nobody made, and
    ``perf.cash_reconcile_residuals`` reads every cash-bearing snapshot row as
    one — two rows carrying the same balance across a period with real deposits
    is exactly the shape it reports as an unexplained residual.
    ``snapshot_reconciliation`` keeps reading the last *declared* balance
    instead, so nothing is lost by staying silent here.
    """
    # #558: this row is *written down*, so an unadjusted share count here does
    # not stay a read-time error — it becomes a durable one.
    derived = derive_holdings(events, splits=splits, as_of=as_of)
    holdings = derived["holdings"]
    if not holdings:
        return None
    try:
        day = dt.date.fromisoformat(str(as_of))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"derived book has an invalid as_of: {as_of!r}") from exc
    for ev in events:
        norm = _norm_trade(ev) if ev.get("type") == "trade" else None
        if norm is not None and norm[0] > day:
            raise ValueError(
                f"derived book dated {day.isoformat()} would exclude a trade dated "
                f"{norm[0].isoformat()} it was derived from")
    positions = []
    for ticker in sorted(holdings):
        fact = holdings[ticker]
        row = {"ticker": ticker, "shares": fact["shares"],
               "market": fact.get("market") or "US",
               "currency": fact.get("currency") or "USD"}
        if fact.get("avg_cost") is not None:
            row["avg_cost"] = fact["avg_cost"]
        unknown = set(row) - SNAPSHOT_POSITION_KEYS
        if unknown:
            raise ValueError(
                "derived book position has unknown fields: " + ", ".join(sorted(unknown)))
        positions.append(row)
    event = {"type": "snapshot", "as_of": day.isoformat(),
             "source": DERIVED_BOOK_SOURCE, "positions": positions}
    if session_id is not None:
        event["session_id"] = str(session_id)
    return event


# ─────────────────────────── 對帳 ───────────────────────────

def reconcile(events, declared_positions, splits=None):
    """宣告持倉 vs 推導持倉 diff(唯讀,不寫任何東西)。
    declared_positions: [{ticker, shares, ...}];回傳 {match, mismatch, clean}。
    mismatch.kind: shares_mismatch | only_declared(推導漏=中間有沒看到的交易) | only_derived(宣告漏=可能已清倉)。"""
    derived = derive_holdings(events, splits=splits)["holdings"]
    dec = {}
    for p in declared_positions:
        t = p.get("ticker") if isinstance(p, dict) else None
        if not t:
            continue
        try:
            dec[t] = float(p.get("shares", 0))
        except (TypeError, ValueError):
            continue
    # #803/#805: `derived` is canonical, so comparing it against the declaration's
    # stored spelling reported *both* books as missing the other's position — for
    # a declaration and a ledger that agree, and even when both are written the
    # same non-canonical way. Same rule and same ambiguity carve-out as
    # `review._overlay_ledger_holdings`: two spellings inside one declaration are
    # a real difference this function exists to name, so they keep their keys and
    # fall through to the mismatch rather than one silently winning.
    dec = symbols.by_canonical_identity(dec)
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


def _declared_positions_map(declared):
    """Validate a declared snapshot's positions into {ticker: facts}; fail closed."""
    out = {}
    for index, row in enumerate(declared.get("positions") or []):
        if not isinstance(row, dict):
            raise ValueError(f"declared positions[{index}] must be an object")
        ticker = row.get("ticker")
        if not ticker or not isinstance(ticker, str):
            raise ValueError(f"declared positions[{index}] is missing a ticker")
        if ticker in out:
            raise ValueError(f"declared snapshot repeats ticker {ticker}")
        try:
            shares = float(row.get("shares"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"declared {ticker} has invalid shares") from exc
        if not math.isfinite(shares) or shares <= 0:
            raise ValueError(f"declared {ticker} has invalid shares")
        avg_cost = row.get("avg_cost")
        if avg_cost is not None:
            try:
                avg_cost = float(avg_cost)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"declared {ticker} has invalid avg_cost") from exc
            if not math.isfinite(avg_cost) or avg_cost <= 0:
                raise ValueError(f"declared {ticker} has invalid avg_cost")
        out[ticker] = {"shares": shares, "avg_cost": avg_cost,
                       "market": str(row.get("market") or "US").upper(),
                       "currency": str(row.get("currency") or "USD").upper()}
    if not out:
        raise ValueError("declared snapshot has no positions")
    return out


def _cash_amount(value, label):
    """Original-currency cash balance or None; non-numeric fails closed."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} cash balance must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} cash balance must be finite")
    return value


def holdings_as_of(events, as_of, splits=None):
    """Derived holdings as a declaration dated ``as_of`` sees them.

    A snapshot is an end-of-day view, so a trade dated after ``as_of`` is not
    part of it. Every consumer of ``snapshot_reconciliation``'s diff must read
    the book through this same window: the diff's ``derived`` values are stated
    on this basis, and a caller that separately calls ``derive_holdings`` gets
    the book as of *today* instead. Mixing the two silently attaches today's
    share count, cycle id and cost basis to a difference computed for an
    earlier day — which is only visible when the ledger holds a trade newer
    than the snapshot, exactly the case a user creates by importing a fresh CSV
    alongside an older screenshot.
    """
    day = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of))
    aligned = []
    for ev in events:
        if ev.get("type") == "trade":
            norm = _norm_trade(ev)
            if norm is not None and norm[0] > day:
                continue
        aligned.append(ev)
    return derive_holdings(aligned, splits=splits, as_of=day)["holdings"]


def snapshot_reconciliation(events, declared, splits=None):
    """Fact-only reconciliation between the recorded book and a newer declaration.

    Implements the docs/prd-ledger.md reconciliation contract: compare
    ledger-derived holdings, as of the declared snapshot's end-of-day ``as_of``,
    with the newly declared positions and cash.  Returns ``None`` only when the
    ledger has recorded no book at all, which since #549 means a root with no
    history rather than one whose history happens to be trades — that root keeps
    the initial-onboarding fail-closed boundary.  Otherwise::

        {"schema_version": 1, "status": "reconciled" | "adjusted",
         "as_of": ..., "against": {"as_of": ..., "snapshot_id": ...},
         "diff": {"positions": [...], "cash": [...]}}

    Rules pinned by owner decision (do not weaken):

    - The diff lists facts only (derived vs declared values).  It never infers
      whether a mismatch is a missing trade, transfer, split, fee, or data
      error — those are indistinguishable here.
    - Every value is compared in its original currency; nothing is converted.
    - Shares use ``SHARES_TOL``; cash and avg_cost use ``CASH_TOL`` (avg_cost
      additionally allows 1e-6 relative slack for large prices).  avg_cost is
      compared only when both sides state a number: an omitted or unknown cost
      is missing data, not a disputed fact.
    - Trades dated after the declared ``as_of`` are excluded: the declaration
      is an end-of-day view and later ledger trades are not part of it.
    - Cash is compared only when the declaration carries a cash object; within
      it, a currency present on only one side is itself a listed difference.
      The recorded side is the last balance the user *declared*: a trades
      import records positions and never a balance (#549), so reading cash off
      the newest recorded row would retire the baseline the moment a CSV
      arrived.
    - A declaration older than the current anchor raises ``ValueError``; only
      the newer view can reconcile (same-day is resolved by the existing
      ``projection_sequence`` tie-break at adoption time).
    """
    anchor = latest_anchor(events)
    if anchor is None:
        return None
    declaration = latest_anchor(events, declared_only=True)
    try:
        declared_as_of = dt.date.fromisoformat(str(declared.get("as_of")))
    except (TypeError, ValueError) as exc:
        raise ValueError("declared snapshot has an invalid as_of date") from exc
    anchor_as_of = dt.date.fromisoformat(str(anchor.get("as_of")))
    if declared_as_of < anchor_as_of:
        raise ValueError(
            f"declared snapshot as_of {declared_as_of.isoformat()} is older than the "
            f"current ledger anchor {anchor_as_of.isoformat()}; only a newer or "
            "same-day declaration can reconcile")
    declared_map = _declared_positions_map(declared)

    derived = holdings_as_of(events, declared_as_of, splits=splits)
    # #803/#805: `reconcile`'s sibling, a hundred lines up, and the same
    # one-sided comparison — `derived` is canonical while the declaration keeps
    # its own spelling, so a book and a declaration that agree came back
    # `adjusted`, with the whole position listed as `only_derived` *and*
    # `only_declared`. It needs no unusual input: two views written the same
    # non-canonical way did it. Same ambiguity carve-out too — one declaration
    # naming an instrument twice is a real difference this diff exists to
    # report, so those keep their stored spelling and stay listed.
    declared_map = symbols.by_canonical_identity(declared_map)

    positions = []
    for ticker in sorted(set(derived) | set(declared_map)):
        fact = derived.get(ticker)
        claim = declared_map.get(ticker)
        if fact is None:
            positions.append({"ticker": ticker, "kind": "only_declared",
                              "derived": None, "declared": claim["shares"]})
            continue
        if claim is None:
            positions.append({"ticker": ticker, "kind": "only_derived",
                              "derived": fact["shares"], "declared": None})
            continue
        if abs(float(fact["shares"]) - claim["shares"]) > SHARES_TOL:
            positions.append({"ticker": ticker, "kind": "shares",
                              "derived": fact["shares"], "declared": claim["shares"]})
        if str(fact.get("market") or "US").upper() != claim["market"]:
            positions.append({"ticker": ticker, "kind": "market",
                              "derived": fact.get("market"), "declared": claim["market"]})
        if str(fact.get("currency") or "USD").upper() != claim["currency"]:
            positions.append({"ticker": ticker, "kind": "currency",
                              "derived": fact.get("currency"), "declared": claim["currency"]})
        derived_cost = fact.get("avg_cost")
        if (derived_cost is not None and claim["avg_cost"] is not None
                and not math.isclose(float(derived_cost), claim["avg_cost"],
                                     rel_tol=1e-6, abs_tol=CASH_TOL)):
            positions.append({"ticker": ticker, "kind": "avg_cost",
                              "derived": derived_cost, "declared": claim["avg_cost"]})

    cash = []
    declared_cash = declared.get("cash")
    if isinstance(declared_cash, dict):
        recorded_cash = (declaration or {}).get("cash")
        anchor_cash = recorded_cash if isinstance(recorded_cash, dict) else {}
        derived_cash = {str(key).upper(): value for key, value in anchor_cash.items()}
        claimed_cash = {str(key).upper(): value for key, value in declared_cash.items()}
        for currency in sorted(set(derived_cash) | set(claimed_cash)):
            have = _cash_amount(derived_cash.get(currency), f"ledger {currency}")
            claim_amount = _cash_amount(claimed_cash.get(currency), f"declared {currency}")
            if (have is not None and claim_amount is not None
                    and abs(have - claim_amount) <= CASH_TOL):
                continue
            cash.append({"currency": currency, "derived": have, "declared": claim_amount})

    return {"schema_version": 1,
            "status": "reconciled" if not positions and not cash else "adjusted",
            "as_of": declared_as_of.isoformat(),
            "against": {"as_of": anchor.get("as_of"),
                        "snapshot_id": anchor.get("snapshot_id")},
            "diff": {"positions": positions, "cash": cash}}


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
            # #803: canonical on the way in, so a broker export spelling a
            # symbol in lower case cannot author a second instrument in an
            # append-only file. Rows already written stay exactly as they are —
            # `derive_holdings` projects those at read time instead.
            sym = symbols.canonical_ticker(r.get("Symbol"))
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
    """去重鍵,對齊 trade_recap.load() 的 seen tuple 精度(qty round2 / px round4)。

    Ticker 走 canonical identity(#803)。`trades_from_csv` 把匯入的 symbol 正規化,
    舊帳本裡卻可能存著小寫拼法——只正規化其中一側,同一筆成交的新舊兩份就不再是
    同一個 key,週度重匯入會把它當成新交易再寫一次,持倉靜默翻倍。兩側讀同一條
    規則,舊列的 bytes 不動。"""
    # `or` the stored value: a ticker this rule cannot canonicalize (a
    # hand-edited non-string) must keep its own identity here, or two
    # genuinely different malformed rows both key on None and dedupe
    # silently drops one — the same class of defect this line fixes.
    return (symbols.canonical_ticker(ev.get("ticker")) or ev.get("ticker"),
            str(ev.get("action", "")).lower(),
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


def virtualize(existing, batches):
    """Pure post-import view for one prepare attempt (#501).

    ``existing`` must already be the caller's strict ledger read and every
    batch must already have passed ``trades_from_csv``.  This function has no
    path, lock, or writer: it only preserves the established sequential
    occurrence-aware dedupe semantics for existing rows and earlier batches.
    """
    if not isinstance(existing, list) or not isinstance(batches, (list, tuple)):
        raise ValueError("virtualize requires event list and candidate batches")
    # This helper is also used before a strict ledger read is persisted.  Do
    # not let a caller's arbitrary object reach occurrence dedupe (which would
    # otherwise expose an AttributeError or become part of a virtual book).
    if any(not isinstance(event, dict) or event.get("type") not in EVENT_TYPES
           for event in existing):
        raise ValueError("virtualize existing events must be known event objects")
    for batch in batches:
        if not isinstance(batch, list):
            raise ValueError("virtualize candidate batches must be lists")
        for event in batch:
            if (not isinstance(event, dict) or event.get("type") != "trade"
                    or _norm_trade(event) is None):
                raise ValueError("virtualize candidates must be valid trade events")
    virtual = list(existing)
    fresh_all, skipped_dup = [], 0
    for batch in batches:
        fresh, dup = dedupe_against(virtual, batch)
        virtual.extend(fresh)
        fresh_all.extend(fresh)
        skipped_dup += dup
    return {"events": virtual, "fresh": fresh_all, "skipped_dup": skipped_dup}


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

    sub.add_parser("doctor", help="唯讀診斷壞行,絕不推導(#462;修好/刪掉壞行後再跑其他子指令)")

    a = ap.parse_args(argv)

    if a.cmd == "doctor":
        # #462 的診斷/遷移出口:strict=False 的唯一合法消費者。刻意不呼叫
        # derive_holdings——這條指令的存在理由就是「看見問題」,拿它的 events
        # 去推導會把出口重新變成繞過口。
        events, issues = _scan_ledger(a.ledger)
        _emit({"ledger": a.ledger, "clean": not issues,
               "readable_lines": len(events), "skipped": len(issues), "issues": issues})
        return 0

    try:
        events, skipped = load_ledger(a.ledger)
    except LedgerIntegrityError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

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
        # #472: the standalone CLI has no review period to borrow a date from,
        # so wall-clock is the honest recorded_at here — pass it explicitly
        # rather than lean on a default inside append_events (problems.py and
        # revisit.py share that writer and must not inherit a wall-clock stamp).
        append_events(a.ledger, [ev], recorded_at=dt.date.today().isoformat())
        out = derive_holdings(events + [ev])
        print(f"appended snapshot as_of={as_of} source={a.source} "
              f"positions={len(data['positions'])}", file=sys.stderr)
        _emit(out)
        return 0

    if a.cmd == "append-trades":
        new_trades, bad, future_dated = trades_from_csv(a.std_csv)
        fresh, dup = dedupe_against(events, new_trades)
        # #472: same rationale as append-snapshot above — this standalone CLI
        # path has no review period, so wall-clock is passed explicitly.
        append_events(a.ledger, fresh, recorded_at=dt.date.today().isoformat())
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
