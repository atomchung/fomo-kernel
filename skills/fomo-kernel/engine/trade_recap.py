#!/usr/bin/env python3
"""
fomo-kernel · trade-recap engine v0.2
實作 5 維行為診斷算法 → 一張 VY 鏡片復盤卡的「機械層」(抓大放小)。
純函式：trades CSV → 5 維 metrics → 卡片(選 top 1-2)。動機那層由 SKILL.md 的對話流程補。

用法：python3 trade_recap.py [trades.csv ...]   (預設吃 ../mock/mock_trades.csv)
隱私：本檔不含任何真實帳戶路徑;預設只跑 mock 資料。用戶自己的 CSV 由參數傳入,留在本機。
"""
import csv, os, re, sys, statistics, datetime as dt
from collections import defaultdict, deque
try:
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text
    from rich.rule import Rule
    from rich.table import Table
    from rich.padding import Padding
    _HAS_RICH = True
except ImportError:                  # 引擎核心(純函式 / TR_JSON 路徑)不需 rich;缺 rich 時仍可 import,別硬依賴(對齊 #26)
    _HAS_RICH = False

# 卡片固定寬度（含邊框），與 ccstory 對齊；中英混排靠 Rich East Asian Width
CARD_WIDTH = 84
_console = Console(width=CARD_WIDTH, highlight=False) if _HAS_RICH else None

def _no_rich_notice(what="復盤卡"):
    """缺 rich 時的優雅降級:純函式 / TR_JSON 不受影響,只有人話卡需要 rich 渲染。"""
    print(f"（{what}需要 rich 才能渲染:pip install rich;或用 TR_JSON=1 取得免 rich 的完整結構化輸出）")

DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "..", "mock", "mock_trades.csv")

N_FWD = 30          # 賣出後 N 交易日看續漲（tunable）
MIN_SPAN_DAYS = 84  # 樣本不足 gate:60 交易日 ≈ 84 日曆日(×7/5);交易跨度短於此 → insufficient(§4.4, #21.4)

# cycle_id 契約(單一事實源,#61):SKILL 對帳、theses.jsonl 綁定、測試斷言都以這兩條為準。
# 正常 = 3 段「ticker#開倉日#序號」;CSV 缺期初持倉算不出開倉 → 2 段「ticker#unknown」。
# 改格式 = schema 變更:必須同步 SKILL.md(開場路由/收尾 theses 註解)並讓 tests/test_tr_json_contract.py 紅燈把關。
CYCLE_ID_RE = re.compile(r"^[^#\s]+#\d{4}-\d{2}-\d{2}#\d+$")
CYCLE_ID_UNKNOWN_RE = re.compile(r"^[^#\s]+#unknown$")
SELL_EARLY_TH = 0.10
SECTOR_MAX_TH = 0.40       # #87/#95:跟 dim_diversify() severity 的 40% 起算點對齊,triggered/severity 不再各吹各的號
RF_ANNUAL = 0.043   # 無風險利率(年)：美國短期國庫券約 4.3%，Jensen's Alpha 用（tunable）
RESIDUAL_POS_TH = 0.001    # 殘倉閾值:市值佔全持倉 <0.1% = 噪音(股息零頭/1 股尾倉),不計入分散度/what-if/per-ticker 診斷/未分類計數(#172,owner 2026-07-12 拍板;相對佔比自適應帳戶規模,非絕對股數/金額)

# ── ticker → (sector, thematic?)  thematic=1 代表同屬一個跨產業主題(如 AI capex)= VY B2 的「driver」──
# 這張表只是「常見股 fallback」。主路徑:SKILL 指引 Claude 對『實際持倉』用世界知識生成 driver map
# (含冷門股 + 跨 sector 主題),寫成 JSON 餵進來覆寫 → 冷門股不再變「未分類」、分散維不失準。
DRIVER_FALLBACK = {
    # 半導體
    "NVDA":("半導體",1),"AMD":("半導體",1),"MU":("半導體",1),"AVGO":("半導體",1),"ARM":("半導體",1),
    "TSM":("半導體",1),"MRVL":("半導體",1),"ALAB":("半導體",1),"CRDO":("半導體",1),"ASML":("半導體",1),
    "LRCX":("半導體",1),"AMAT":("半導體",1),"KLAC":("半導體",1),"INTC":("半導體",1),"DRAM":("半導體",1),
    # 軟體 / 雲 / AI 應用
    "ORCL":("軟體雲",1),"PLTR":("軟體雲",1),"GOOG":("軟體雲",1),"GOOGL":("軟體雲",1),"META":("軟體雲",1),
    "MSFT":("軟體雲",1),"NOW":("軟體雲",1),"CRWD":("軟體雲",1),"NET":("軟體雲",1),"DDOG":("軟體雲",1),
    "RBRK":("軟體雲",1),"SNOW":("軟體雲",1),"PANW":("軟體雲",1),
    # AI 資料中心電力 / 核能 / 儲能
    "VRT":("資料中心電力",1),"NUKZ":("資料中心電力",1),"SMR":("資料中心電力",1),"OKLO":("資料中心電力",1),
    "EOSE":("資料中心電力",1),"GEV":("資料中心電力",1),"CEG":("資料中心電力",1),
    "TSLA":("電動車AI",1),
    # 非 AI driver
    "MSTR":("加密",0),"COIN":("加密",0),"MARA":("加密",0),
    "HOOD":("金融科技",0),"SOFI":("金融科技",0),"GRAB":("金融科技",0),"NU":("金融科技",0),
    "CAVA":("消費",0),"CELH":("消費",0),"HIMS":("消費",0),
    "MP":("稀土材料",0),"ONDS":("無人機國防",0),"NOK":("電信",0),
    # ETF / 大盤 / 商品 / 債
    "SPY":("大盤ETF",0),"VT":("大盤ETF",0),"VOO":("大盤ETF",0),"EWY":("區域ETF",0),
    "IAU":("商品",0),"IEF":("債券",0),
}
_DRIVER_MAP = dict(DRIVER_FALLBACK)
_DM_SKIPPED = 0                                    # 上次載入跳過的壞 entry 數(給 main 顯示)
# 上次 load() 的計數(給 main 顯示,對齊 _DM_SKIPPED 範式,#50):輸入層每個靜默丟棄面都留痕,
# 卡面數字少了幾筆要看得見,不然「你的數字」跟券商 app 對不上時用戶不可察覺。
_LOAD_STATS = {}
def driver(t): return _DRIVER_MAP.get(t, ("未分類", 0))
def load_driver_map(path):
    """載入 Claude 生成的 {ticker: [sector, thematic]} JSON 覆寫 fallback。讓冷門股也有正確 driver。
    逐筆容錯:一筆格式壞只跳過那一筆、其餘照收(舊版整包 try → 一顆老鼠屎丟掉整張 map)。"""
    import json
    global _DM_SKIPPED
    _DM_SKIPPED = 0
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError) as e:
        # 靜默 fallback 會讓用戶分不出「沒設 driver map」vs「設錯路徑/壞 JSON」(#22.5)
        print(f"⚠️  driver map 載入失敗 ({path}): {e} — 改用 fallback,冷門股 driver 可能失準", file=sys.stderr)
        return 0
    if not isinstance(m, dict):
        print(f"⚠️  driver map 格式應為 dict ({path}),實得 {type(m).__name__} — 改用 fallback", file=sys.stderr)
        return 0
    ok = 0
    for t, v in m.items():
        try:
            _DRIVER_MAP[t] = (v[0], int(v[1]))
            ok += 1
        except (KeyError, IndexError, TypeError, ValueError):
            _DM_SKIPPED += 1                       # 壞的跳過,不連累好的
    return ok

# ─────────────────────────── 1. 解析 ───────────────────────────
def load(paths):
    global _LOAD_STATS
    rows = []
    seen = set()        # 跨檔(完整歷史 + 最新增量)重疊期去重 → 合併到最新日期
    # #50:輸入層靜默丟棄面全部計數,進 meta 行讓用戶看得見資料被動過。
    # #14 後 skip_dup 只計「真跨檔重疊」(同檔同日同價的獨立成交已靠 occ 序號保留,不再併殺)。
    stats = dict(loaded=0, skip_non_trade=0, skip_non_bs=0, skip_no_sym=0,
                 skip_parse=0, skip_zero=0, skip_dup=0)
    for p in paths:
        # #14:per-檔「同鍵出現序號」。同一份對帳單裡不會把同一筆成交列兩次,所以同檔同日同價的
        # 兩筆 = 兩筆獨立成交(大單被拆成同價多筆 / 同日分批),各給遞增 occ → 是不同 rec、都保留。
        # 跨檔重疊時,增量檔的同一筆 occ 從 0 起算 → 與完整檔的 occ 0 撞上 seen → 去重。兩者兼得。
        occ = defaultdict(int)
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("RecordType") or "").strip() != "Trade":
                    stats["skip_non_trade"] += 1
                    continue
                act = (r.get("Action") or "").strip().upper()
                if act not in ("BUY", "SELL"):
                    stats["skip_non_bs"] += 1
                    continue
                sym = (r.get("Symbol") or "").strip()
                if not sym:
                    stats["skip_no_sym"] += 1
                    continue
                try:
                    qty = abs(float(r["Quantity"])); px = float(r["Price"])
                    # TradeDate 解析納入同一 try(triad/Codex):壞/缺日期 → 這列歸 skip_parse,
                    # 不讓「一列日期爛掉」拋 ValueError 炸掉整份復盤(違反 #50 的可觀測誠實)。
                    d = dt.date.fromisoformat((r.get("TradeDate") or "").strip())
                except (ValueError, KeyError):
                    stats["skip_parse"] += 1
                    continue
                if px <= 0 or qty <= 0:   # 濾掉 split/free-share/journal 等 price=0 的偽交易
                    stats["skip_zero"] += 1
                    continue
                key = (sym, act.lower(), round(qty, 2), round(px, 4), d)
                k = occ[key]; occ[key] += 1   # #14:同檔內第 k 筆同鍵成交,序號進 dedup key → 獨立成交不互殺
                rec = key + (k,)
                if rec in seen:           # 跨檔完全相同(同序號)= 重疊期重複,跳過
                    stats["skip_dup"] += 1
                    continue
                seen.add(rec)
                # 多市場欄位(#51/#129 PR-2a):可選 Market/Currency 欄,缺 = 美股 USD(向後相容)。
                # 原幣記帳鐵律(prd-ledger §2):price 永遠是原幣,換算只發生在聚合視圖(usd_view)與呈現層。
                rows.append(dict(ticker=sym, side=act.lower(), qty=qty, price=px, date=d,
                                 market=(r.get("Market") or "US").strip() or "US",
                                 currency=(r.get("Currency") or "USD").strip().upper() or "USD"))
    stats["loaded"] = len(rows)
    _LOAD_STATS = stats
    rows.sort(key=lambda x: x["date"])
    return rows


def load_cash_flows(paths):
    """讀出所有影響現金餘額的 row（含買賣 + deposit/withdrawal/dividend/interest/fee）。
    load() 只留 BUY/SELL 給行為分析；現金餘額要的是**每一筆現金增減**，靠 Amount 欄
    （券商流水裡 Amount = 現金帳戶實際變動：買=負、賣/存/息=正、費=負，已含手續費淨額）。
    #171 PR-1（B 路線帳戶級現金地基）。去重骨架同 load()（跨檔重疊期同一筆只算一次）。
    回傳 [{date, amount, kind, currency}]，依日期排序。"""
    KIND = {"BUY": "trade", "SELL": "trade", "REINVEST": "trade",
            "DEPOSIT": "deposit", "WITHDRAWAL": "withdrawal",
            "DIVIDEND": "dividend", "INTEREST": "interest", "FEE": "fee"}
    flows = []
    seen = set()
    for p in paths:
        occ = defaultdict(int)
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                raw = (r.get("Amount") or "").strip()
                if not raw:                          # 無 Amount（如純 split/journal）→ 不影響現金
                    continue
                try:
                    amt = float(raw)
                    d = dt.date.fromisoformat((r.get("TradeDate") or "").strip())
                except (ValueError, KeyError):
                    continue
                if abs(amt) < 1e-9:
                    continue
                act = (r.get("Action") or "").strip().upper()
                rectype = (r.get("RecordType") or "").strip().upper()
                # kind：先看 Action，退而看 RecordType（有些券商現金流只填 RecordType）
                kind = KIND.get(act) or (rectype.lower() if rectype in
                       ("DEPOSIT", "WITHDRAWAL", "DIVIDEND", "INTEREST", "FEE") else "other")
                cur = (r.get("Currency") or "USD").strip().upper() or "USD"
                key = (d, round(amt, 2), kind, cur)
                k = occ[key]; occ[key] += 1          # 同檔同鍵多筆（如同日兩筆股息）各給序號，不互殺
                rec = key + (k,)
                if rec in seen:                       # 跨檔重疊 = 同序號重複，跳過
                    continue
                seen.add(rec)
                flows.append(dict(date=d, amount=amt, kind=kind, currency=cur))
    flows.sort(key=lambda x: x["date"])
    return flows


def cash_position(cash_flows, held_mv, anchor=None, prev_end=None):
    """帳戶現金地基（#171 PR-1）。
    - cash_balance：有現金餘額錨點 → 錨點 + 其後現金流（正確，對付 is_complete=False 的不完整 CSV）；
      無錨點 → 全期 Σamount 並標不可信（假設開戶現金 0，CSV 漏一筆 deposit 就偏，honesty_ledger 揭露）。
    - cash_weight = 現金 /（持倉市值 + 現金）；分母 ≤0 → None。
    - recent_net_deposit = 本期（prev_end 後）外部淨流入（deposit − withdrawal），給「這筆錢該不該部署」判讀。
    幣別：假設 cash_flows.amount 已在聚合幣別（混幣換算由 main 接線層負責）。"""
    if isinstance(prev_end, str):
        prev_end = dt.date.fromisoformat(prev_end) if prev_end else None
    a_date = None
    if anchor and anchor.get("as_of") and anchor.get("amount") is not None:
        a_date = anchor["as_of"]
        if isinstance(a_date, str):
            a_date = dt.date.fromisoformat(a_date)
        balance = float(anchor["amount"]) + sum(cf["amount"] for cf in cash_flows if cf["date"] > a_date)
        source, reliable = "anchored", True
    else:
        balance = sum(cf["amount"] for cf in cash_flows)
        source, reliable = "csv_sum", False
    denom = held_mv + balance
    # 無錨點的負現金 = csv_sum 假設破裂（買入為主、入金沒記全），weight 是垃圾 → 不報；denom≤0 亦不報。
    # 有錨點的負現金 = 真融資（margin debit），weight 負有意義（槓桿曝險），照報。
    if denom <= 1e-9 or (not reliable and balance < 0):
        weight = None
    else:
        weight = balance / denom
    recent = sum(cf["amount"] for cf in cash_flows
                 if cf["kind"] in ("deposit", "withdrawal")
                 and (prev_end is None or cf["date"] > prev_end))
    return dict(balance=round(balance, 2), weight=weight, source=source,
                reliable=reliable, recent_net_deposit=round(recent, 2))


def _load_skip_note():
    """#50:把 load() 的靜默丟棄計數組成人話短語(進 meta 行)。全零 → 空字串(不吵)。"""
    s = _LOAD_STATS
    if not s:
        return ""
    labels = [("skip_non_trade", "非Trade"), ("skip_dup", "重複"), ("skip_parse", "無法解析"),
              ("skip_zero", "零值濾除"), ("skip_non_bs", "非買賣"), ("skip_no_sym", "無代號")]
    parts = [f"{name} {s[k]}" for k, name in labels if s.get(k)]
    return f"（跳過:{' / '.join(parts)}）" if parts else ""

# ───────────────────── 2. FIFO round-trip 配對 ─────────────────────
def round_trips(rows):
    lots = defaultdict(deque)   # ticker -> deque[(qty, price, date)]
    rts = []
    for r in rows:
        t = r["ticker"]
        if r["side"] == "buy":
            lots[t].append([r["qty"], r["price"], r["date"]])
        else:
            q = r["qty"]
            while q > 1e-9 and lots[t]:
                lot = lots[t][0]
                take = min(q, lot[0])
                ret = (r["price"] - lot[1]) / lot[1]
                hold = (r["date"] - lot[2]).days
                rts.append(dict(ticker=t, entry=lot[2], exit=r["date"], qty=take,
                                buy_px=lot[1], sell_px=r["price"], ret=ret, hold=hold))
                lot[0] -= take; q -= take
                if lot[0] <= 1e-9:
                    lots[t].popleft()
    return rts, lots

def fifo_held(open_lots):
    """FIFO 剩餘 lots → {ticker: (shares, cost_total)}。
    #162:realized 走 FIFO 配對,未實現的剩餘成本必須同一基礎,realized+unrealized
    加總才等於經濟真值(混用 avg cost 在「攤平後部分賣出」時加總會偏離,方向不定)。
    positions() 的 avg cost 語意保留給 avgdown 偵測(行為層:買價 vs 平均持倉成本)
    與 ledger 對帳(用戶宣告錨點=券商 app 的均價視圖),兩者都不與 realized 相加。"""
    held = {}
    for t, lots in open_lots.items():
        sh = sum(l[0] for l in lots)
        if sh > 1e-6:
            held[t] = (sh, sum(l[0] * l[1] for l in lots))
    return held

# ───────────────────── 3. 持倉重建（成本基礎）─────────────────────
def positions(rows):
    pos = defaultdict(lambda: [0.0, 0.0])   # ticker -> [shares, cost_total]
    avg_down = []                            # 虧損中加碼事件
    for r in rows:
        t = r["ticker"]; sh, cost = pos[t]
        if r["side"] == "buy":
            if sh > 1e-9 and r["price"] < (cost / sh) * 0.90:  # 買價比 avg_cost 低 >10% = 有意義攤平（濾掉微幅 DCA）
                # #94:point-in-time 成本權重 —— 用「加碼這個決定做出之前」的 pos 快照(此刻 cost/sh 尚未套用本筆買入)
                # 算這檔在當下佔全部持倉成本基礎的比例,不回推今日市值(分母排除已清倉 s2<=1e-9,對齊下面 held 的過濾閾值)。
                total_cost_then = sum(c for s2, c in pos.values() if s2 > 1e-9)
                weight_then = (cost / total_cost_then) if total_cost_then > 1e-9 else 0.0
                avg_down.append(dict(ticker=t, date=r["date"], px=r["price"], avg=cost/sh, weight_then=weight_then))
            pos[t][0] += r["qty"]; pos[t][1] += r["qty"] * r["price"]
        else:
            if sh > 1e-9:
                ac = cost / sh
                pos[t][1] -= min(r["qty"], sh) * ac
                pos[t][0] -= min(r["qty"], sh)   # clamp:賣量超過持有不讓股數變負(對帳單可能漏最早建倉)
    held = {t: (sh, c) for t, (sh, c) in pos.items() if sh > 1e-6}
    return held, avg_down

def current_cycles(rows):
    """每個當前持倉 ticker 的 position cycle 起始日 + 序號(第幾次建倉)。與 positions() 同累加邏輯
    (sell 只在有倉時減 → 不跌負),所以 cycle 判定跟持倉一致(雙審 gemini#1:修負數誤判 + codex#4:單一 ledger)。
    序號讓同 ticker 清倉後重建不撞 id(雙審 codex#3)。CSV 缺期初持倉 → 該 ticker 不在 return,呼叫端標 #unknown。"""
    sh = defaultdict(float); seq = defaultdict(int); start = {}
    for r in rows:
        t = r["ticker"]
        if r["side"] == "buy":
            if sh[t] <= 1e-6:                        # 從 0/清倉後 建倉 = 新 cycle（閾值對齊 positions held）
                seq[t] += 1; start[t] = r["date"].isoformat()
            sh[t] += r["qty"]
        elif r["side"] == "sell" and sh[t] > 1e-6:  # 明確 sell（防 deposit 等誤觸，雙審）；只在有倉時減
            sh[t] = max(0.0, sh[t] - r["qty"])      # 截斷防跌負（oversell 賣超持倉）→ 否則後續 buy 被負數污染（雙審 blocker）
            if sh[t] <= 1e-6: start.pop(t, None)    # 清倉 → cycle 結束
    return {t: {"start": start[t], "seq": seq[t]} for t in start}

def orphan_sells(rows):
    """偵測『賣超』:某檔賣量超過已知買量。多半是對帳單沒涵蓋最早的建倉,
    或先賣後買(做空)。只計數+列名,當報告最後的資料完整性提示,不進盈虧/洞的計算。
    現階段範圍只處理『先買後賣』;先賣後買(做空)另開 issue 討論。"""
    pos = defaultdict(float); orphan = defaultdict(float)
    for r in rows:
        t = r["ticker"]
        if r["side"] == "buy":
            pos[t] += r["qty"]
        else:
            if r["qty"] > pos[t] + 1e-9:
                orphan[t] += r["qty"] - max(pos[t], 0.0)
            pos[t] = max(pos[t] - r["qty"], 0.0)
    return {t: q for t, q in orphan.items() if q > 1e-6}

def classify_adds(rows, min_adds=2):   # min_adds 指「加碼」筆數(排除首筆);原「≥3 買入」≈「≥2 加碼」,改口徑後對齊(#41 review)
    """主從分類每個標的的加碼:疑似定投(漲跌都買/規律) vs 疑似凹單(只虧損買+金額加速) vs 待確認。
    codex+gemini review 定稿:主從不投票;『價格無關 + 時間規律』為主、金額一致性最易誤判只當輔助;
    機械只下『疑似』,最終靠用戶確認 thesis / 標交易意圖(進場打標 = v2 更根本解)。"""
    pos = defaultdict(lambda: [0.0, 0.0])
    buys = defaultdict(list)                       # ticker -> [(date, amount, in_loss)]
    for r in rows:
        t = r["ticker"]; sh, cost = pos[t]
        if r["side"] == "buy":
            is_add = sh > 1e-9                          # 已有倉才算「加碼」;首筆建倉 / 清倉後重建不算(#41 G1)
            avg = cost / sh if is_add else r["price"]
            buys[t].append((r["date"], r["qty"] * r["price"], is_add and r["price"] < avg, is_add))
            pos[t][0] += r["qty"]; pos[t][1] += r["qty"] * r["price"]
        elif sh > 1e-9:
            pos[t][1] -= min(r["qty"], sh) * (cost / sh); pos[t][0] -= min(r["qty"], sh)   # #41 G2:clamp 股數不跌負(對齊 positions)
    out = {}
    for t, bs in buys.items():
        adds = [b for b in bs if b[3]]                              # 只看「加碼」(sh>0 的買),排除首筆建倉/清倉後重建(#41 G1)
        if len(adds) < min_adds: continue                           # gate 用加碼數,與分類同口徑;擋掉 0/1 筆加碼的無意義分類(#41 review 共識)
        n = len(adds)
        loss_ratio = sum(1 for _, _, il, _ in adds if il) / n       # 主訊號:加碼中虧損買的比例(價格無關性)
        loss_amts = [amt for _, amt, il, _ in adds if il]
        accel = len(loss_amts) >= 3 and statistics.mean(loss_amts[-2:]) > statistics.mean(loss_amts[:2]) * 1.5
        gaps = [(adds[i + 1][0] - adds[i][0]).days for i in range(n - 1)]
        mg = statistics.mean(gaps) if gaps else 0
        regular = len(gaps) >= 2 and mg > 0 and statistics.pstdev(gaps) < mg * 0.6   # 輔:加碼時間規律(間隔 CV 低);需≥2 間隔(≥3 加碼)—— 單一 gap 的 pstdev 恆=0 會誤判規律(#41 review)
        if loss_ratio < 0.6 or regular:                             # 漲跌都買 或 時間規律 → 定投
            cls = "疑似定投"
        elif loss_ratio > 0.8 and accel:                            # 只虧損買 + 金額加速 → 凹單
            cls = "疑似凹單"
        else:
            cls = "待確認"
        out[t] = dict(cls=cls, n_adds=n, loss_ratio=loss_ratio)
    return out

# ───────────── 3.5 多市場幣別(#51/#129 PR-2a;設計 docs/prd-ledger.md §2)─────────────
# 原則:資料層原幣記帳;跨 ticker「聚合」(sizing 權重/分散權重/金額總覽)才需要共同幣別。
# 聚合基準幣 = USD(engine 內部);display currency(語言→幣別)是 SKILL 呈現層的事。
# 單一幣別組合(含純台股)聚合天然自洽 → 不抓匯率、零行為變化。

def currency_map(rows):
    """per-ticker 幣別。回 (cur_map, currencies, conflicts):同一檔出現多幣別=資料錯,
    記 conflicts(取最後一筆的幣別),下游放進 data_integrity 讓卡面誠實。"""
    cur_map, conflicts = {}, set()
    for r in rows:
        c = r.get("currency", "USD")
        t = r["ticker"]
        if t in cur_map and cur_map[t] != c:
            conflicts.add(t)
        cur_map[t] = c
    return cur_map, sorted(set(cur_map.values())), sorted(conflicts)


def fetch_fx(currencies):
    """非 USD 幣別 → yfinance '{CUR}=X'(1 USD 兌多少 CUR)→ {cur: usd_per_unit};USD 恆 1.0。
    離線/失敗 → 缺誰記誰,不 crash(呼叫端把缺口寫進 data_integrity,聚合以 1.0 近似並明示)。"""
    fx = {"USD": 1.0}
    todo = sorted(c for c in set(currencies) if c and c != "USD")
    if not todo:
        return fx, None
    try:
        import yfinance as yf
    except ImportError:
        return fx, "yfinance 未安裝(匯率缺,多幣別聚合按原幣近似)"
    err = None
    for c in todo:
        try:
            h = yf.Ticker(f"{c}=X").history(period="5d")["Close"].dropna()
            if len(h):
                usd_per_cur = 1.0 / float(h.iloc[-1])       # '{CUR}=X' 報 1 USD 兌 CUR → 反轉成 CUR→USD
                if usd_per_cur > 0:
                    fx[c] = round(usd_per_cur, 6)
        except Exception as e:                               # 單一幣別抓不到不連累其他
            err = f"匯率下載失敗({c}): {e}"
    missing = [c for c in todo if c not in fx]
    if missing and not err:
        err = f"匯率無資料: {','.join(missing)}"
    return fx, err


def usd_view(rts, held, last_px, cur_map, fx):
    """聚合前置換算視圖:把 rts 金額欄 / held 成本 / last_px 換到 USD,
    讓 dim_size / dim_diversify / overview_stats / payoff_attribution 零改動地在共同幣別上聚合。
    ret(比率)與 hold(天數)不受等比縮放影響;fx 缺的幣別因子 = 1.0(呼叫端已記警告)。
    ⚠️ 只給「聚合」消費;per-ticker 呈現(ticker_diagnosis / best_worst / 卡上單檔數字)一律用原幣原物件。"""
    f = lambda t: fx.get(cur_map.get(t, "USD"), 1.0)
    rts_u = [dict(r, buy_px=r["buy_px"] * f(r["ticker"]), sell_px=r["sell_px"] * f(r["ticker"]))
             for r in rts]
    held_u = {t: (sh, c * f(t)) for t, (sh, c) in held.items()}
    lastpx_u = {t: px * f(t) for t, px in (last_px or {}).items()}
    return rts_u, held_u, lastpx_u


def pnl_by_currency(rts, held, last_px, cur_map):
    """已實現/未實現按原幣分桶(呈現層拿去換 display currency;會計事實,不摻換算)。"""
    out = {}
    for r in rts:
        if not (r.get("sell_px") and r.get("buy_px")):
            continue
        c = cur_map.get(r["ticker"], "USD")
        out.setdefault(c, {"realized": 0.0, "unrealized": 0.0})
        out[c]["realized"] += r["qty"] * (r["sell_px"] - r["buy_px"])
    for t, (sh, cost) in (held or {}).items():
        px = (last_px or {}).get(t)
        if px is None:
            continue                                        # 無現價者不入未實現(對齊 unrealized_coverage 語意)
        c = cur_map.get(t, "USD")
        out.setdefault(c, {"realized": 0.0, "unrealized": 0.0})
        out[c]["unrealized"] += sh * px - cost
    return {c: {k: round(v, 2) for k, v in d.items()} for c, d in sorted(out.items())}


# ───────────────────── 4. yfinance 補價（賣太早）─────────────────────
def fetch_prices(tickers, start):
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance 未安裝"
    try:
        data = yf.download(sorted(set(tickers) | {"SPY", "QQQ", "SOXX"}), start=start,
                           progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        return None, f"yfinance 下載失敗: {e}"
    if data is None or data.empty:
        return None, "yfinance 無資料"
    if data.ndim == 1:
        data = data.to_frame()
    return data, None

def fetch_splits(tickers):
    """抓每檔的分割事件 {ticker: [(date, ratio), ...]}。抓不到/離線 → 回 {}(不調整,降級)。"""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    out = {}
    for t in sorted(set(tickers)):
        try:
            s = yf.Ticker(t).splits           # pandas Series:index=日期, value=分割比率(10:1 → 10.0)
            if s is not None and len(s):
                out[t] = [(d.date(), float(r)) for d, r in s.items() if r and r > 0]
        except Exception:                     # 單檔抓不到不影響其他檔
            continue
    return {t: v for t, v in out.items() if v}

def adjust_for_splits(rows, splits):
    """把每筆成交換算到『分割後(今日)』基礎,與 yfinance auto_adjust 的價格對齊。
    因子 = 成交日『之後』發生的所有分割比率連乘;股數×因子、價格÷因子 → 成交金額不變。
    這同時修好三件事:① 跨分割 round-trip 的 ret/fwd ② 持倉市值(股數×今日價) ③ 分割造成的假 orphan。
    splits={} (離線/抓不到) → 原地不動,沿用名目價(現狀行為)。回傳實際調整的成交筆數。"""
    n = 0
    for r in rows:
        evs = splits.get(r["ticker"])
        if not evs:
            continue
        factor = 1.0
        for d, ratio in evs:
            if d > r["date"]:                 # 只有成交日之後的分割才影響這筆
                factor *= ratio
        if abs(factor - 1.0) > 1e-9:
            r["qty"] = r["qty"] * factor
            r["price"] = r["price"] / factor
            n += 1
    return n

def adaptive_n_fwd(rows):
    """賣出後觀察窗隨資料長度自適應:資料短就用短窗,讓半年資料的近端賣出也算得到 winner_early。"""
    span = (rows[-1]["date"] - rows[0]["date"]).days
    return 30 if span >= 365 else 20 if span >= 120 else 10   # ≥1年→30d,半年→20d,更短→10d

def last_prices(data, max_stale_days=10):
    """全價格 universe 的最新收盤價 {ticker: px}。
    #79:last_px 不能依附在只掃 round-trip 的迴圈裡——持有中、從未平倉的標的
    (往往是最大倉位)會被結構性排除,未實現損益/套牢診斷/what-if 全部靜默漏算。
    抓價清單本來就含 held(main:tickers = rts | held),這裡把已抓到的價全放出來。
    staleness gate:最後有效價距價格框末日 > max_stale_days(日曆日)→ 不給價,
    下游自然降級成本基礎。下市/長期停牌的殭屍持倉,殘價當現價餵進未實現損益
    比「判不出」更糟;10 天罩得住連假與單日資料延遲,擋得住下市數月的殘價。"""
    if data is None:
        return {}
    out = {}
    latest = data.index[-1]
    for t in data.columns:
        col = data[t].dropna()
        if col.empty:
            continue
        if (latest - col.index[-1]).days > max_stale_days:
            continue
        out[t] = float(col.iloc[-1])
    return out

def fwd_from_px(rts, data, n_fwd=N_FWD):
    if data is None:
        return None, None
    import pandas as pd  # 只有真有價格資料才需要 pandas;無價(乾淨環境/未裝)時提早 return,別硬依賴
    fwds, last_px = [], last_prices(data)
    for r in rts:
        t = r["ticker"]
        if t not in data.columns: continue
        col = data[t].dropna()
        if col.empty: continue
        after = col[col.index > pd.Timestamp(r["exit"])]
        if len(after) == 0: continue
        target = after.iloc[min(n_fwd-1, len(after)-1)]
        r["fwd"] = (float(target) - r["sell_px"]) / r["sell_px"]
        r["fwd_trunc"] = len(after) < n_fwd
        fwds.append(r["fwd"])
    return fwds, last_px

def _port_daily_returns(rows, px, proxy=None, fallback_bench="SPY"):
    """重建投組日報酬序列(昨日持股 × 今日價,排除當日買賣現金流)。
    proxy={ticker: 板塊基準 ticker} 給定時,同步重建「板塊配置混合」mimic 投組
    (同權重、每檔換成其板塊基準)→ 回傳 (port, mimic, fb_share, unproxied):
    fb_share = 無板塊對照、按 fallback_bench 計的平均權重(拆帳 coverage 用),unproxied = 這些 ticker。
    fallback_bench = 該市場主基準(#129 PR-2b:台股子組合按 ^TWII 計,別把 SPY 硬塞給台股當「大盤」)。
    mimic 與 port 定義在完全相同的日集(缺對照時逐層降級 主基準 → 股票自身),恆等式才守得住。
    不給 proxy → 只回 port Series(相容舊呼叫)。"""
    import pandas as pd
    from collections import defaultdict
    days = list(px.index)
    ev = defaultdict(list)
    for r in rows:
        ev[pd.Timestamp(r["date"])].append(r)
    shares = defaultdict(float); prev = {}; port_rets = {}
    mimic_rets = {}; fb_w = 0.0; fb_days = 0; unproxied = set()
    spy = px[fallback_bench] if (proxy is not None and fallback_bench in px.columns) else None
    for i, day in enumerate(days):
        if i > 0 and prev:
            num = den = num_m = fb = 0.0
            for t, sh in prev.items():
                if sh == 0 or t not in px.columns: continue
                p1 = px[t].iloc[i]; p0 = px[t].iloc[i-1]
                if pd.isna(p1) or pd.isna(p0): continue
                num += sh * p1; den += sh * p0
                if proxy is None: continue
                pt = proxy.get(t)
                if pt and pt not in px.columns:       # 有對照但整條沒抓到價(離線/下市)→ 視同無對照
                    unproxied.add(t); pt = None
                if not pt:
                    unproxied.add(t)
                m1 = px[pt].iloc[i] if pt else None
                m0 = px[pt].iloc[i-1] if pt else None
                if pt and not (pd.isna(m1) or pd.isna(m0)):
                    num_m += sh * p0 * (float(m1) / float(m0))
                elif spy is not None and not (pd.isna(spy.iloc[i]) or pd.isna(spy.iloc[i-1])):
                    num_m += sh * p0 * (float(spy.iloc[i]) / float(spy.iloc[i-1]))  # 按 SPY 計:配置 0,全歸選股
                    fb += sh * p0; unproxied.add(t)    # 對照欄位存在但這天缺值(如新上市 ETF)→ 仍記為 fallback,誠實揭露
                else:
                    num_m += sh * p1                  # 連 SPY 都沒價(序列頭)→ 用股票自身,該檔當日選股=0
                    fb += sh * p0; unproxied.add(t)
            if den > 0:
                port_rets[day] = num / den - 1
                if proxy is not None:
                    mimic_rets[day] = num_m / den - 1
                    fb_w += fb / den; fb_days += 1
        for r in ev.get(day, []):
            shares[r["ticker"]] += r["qty"] if r["side"] == "buy" else -r["qty"]
        prev = dict(shares)
    port = pd.Series(port_rets)
    if proxy is None:
        return port
    return port, pd.Series(mimic_rets), (fb_w / fb_days if fb_days else 0.0), unproxied

def _aligned(port, bench_px):
    """對齊投組/基準日報酬:去 NaN + 去離群(split/資料錯)。回歸與拆帳共用同一天集,數字才對得上。"""
    import pandas as pd
    df = pd.DataFrame({"p": port, "s": bench_px.pct_change()}).dropna()
    return df[df["p"].abs() < 0.5]

def _regress(port, bench_px, rf_annual):
    """對單一 benchmark 回歸 → β / Jensen α + 標準誤/t/95% 區間。
    對 excess(扣 rf)回歸,截距即 Jensen α(值與舊式恆等);SE 用 OLS 截距公式——
    持倉越集中、個股雜訊越大 → 殘差越大 → SE 越寬:「集中=判不準」由統計直接量,
    不再用持倉檔數當代理(#80)。se=0(完美複製品)→ t=None,別除零。
    回傳的 days = 對齊後實際回歸用的天索引,給 dim_alpha_beta 的拆帳重用,
    別再對同一組序列重跑一次 _aligned(不然兩處門檻一旦分岔,拆帳恆等式會悄悄壞掉)。"""
    df = _aligned(port, bench_px)
    n = len(df)
    if n < 60: return None
    rf_d = rf_annual / 252.0
    xe = df["s"] - rf_d; ye = df["p"] - rf_d
    var_x = float(xe.var())
    if var_x <= 1e-12:            # 基準幾乎零波動(整段停牌/假期資料)→ β/α 無定義,別除出 NaN 漏進 JSON
        return None
    beta = xe.cov(ye) / var_x
    alpha_d = ye.mean() - beta * xe.mean()
    resid = ye - alpha_d - beta * xe
    s2 = float((resid ** 2).sum()) / (n - 2)
    sxx = var_x * (n - 1)
    se_d = (s2 * (1.0 / n + float(xe.mean()) ** 2 / sxx)) ** 0.5 if sxx > 0 else 0.0
    alpha_ann = alpha_d * 252.0
    se_ann = se_d * 252.0
    # 地板 1e-10(日尺度):真實資料 se 遠大於此;只有「完美複製品」的浮點噪音殘差(~1e-17)
    # 會落在其下 → t 無意義(會算出 1e13 這種天文數字),判 None,分級保守歸 noise。
    t = (float(alpha_d) / se_d) if se_d > 1e-10 else None
    port_tot = (1 + df["p"]).prod() - 1
    bench_tot = (1 + df["s"]).prod() - 1
    return dict(beta=beta, alpha_ann=alpha_ann, alpha_se_ann=se_ann, alpha_t=t,
                alpha_ci95=[alpha_ann - 1.96 * se_ann, alpha_ann + 1.96 * se_ann],
                port_tot=port_tot, bench_tot=bench_tot, excess=port_tot - bench_tot, n=n,
                days=df.index)

# ── 賽道/選股拆帳(Brinson 式兩層)的板塊基準 ──
# sector(driver)標籤 → 板塊 ETF。mimic 投組 = 同權重、每檔換成其板塊基準 →
# 「押對賽道」= mimic − SPY、「板塊內選股」= 你 − mimic;兩項相加恆等於贏大盤 pp。
# 這就是卡面一直說的『真正公平的對照 = 你當時板塊配置的混合』——現在真的算它。
# 標籤來源 = DRIVER_FALLBACK + Claude driver_map(GICS 式中文,含常見同義變體);
# 沒對照的標籤 → 該檔按 SPY 計(配置效果 0,超額全歸選股)+ 記入 unproxied,卡上要誠實提。
SECTOR_BENCH = {
    "半導體": "SOXX", "軟體雲": "QQQ", "科技": "XLK",
    "能源": "XLE", "金融": "XLF", "金融科技": "XLF",
    "醫療": "XLV", "醫療保健": "XLV", "生技": "XLV",
    "必需消費": "XLP", "消費": "XLY", "非必需消費": "XLY", "汽車": "XLY", "電動車AI": "XLY",
    "工業": "XLI", "無人機國防": "XLI", "國防": "XLI",
    "公用事業": "XLU", "公用": "XLU", "資料中心電力": "XLU",
    "電信": "XLC", "通訊": "XLC", "媒體": "XLC",
    "原物料": "XLB", "材料": "XLB", "稀土材料": "XLB",
    "房地產": "XLRE",
}
BENCH_SELF = {"大盤ETF", "區域ETF", "商品", "債券"}  # 持有 ETF 本身=配置決策:基準=它自己(選股恆 0,超額全歸賽道)
ALPHA_MIN_DAYS, ALPHA_T_TH = 252, 1.96  # 能力語氣門檻:≥1 年(業界慣例)+ 95% 顯著;統計錨,非拍腦袋(#63)

# per-market 主基準(#129 PR-2b,prd-ledger §2.4):各市場子組合對各自的大盤,不合成總 α。
# TW 用 ^TWII(加權指數,不含息 → 台股 α 略被高估,卡上明標);未知市場 fallback SPY + 誠實註記。
MARKET_BENCH = {"US": "SPY", "TW": "^TWII"}

def _sector_proxy(t, market="US"):
    """ticker → 拆帳基準:ETF 類=自己;美股有板塊對照=板塊 ETF;非美股市場的板塊表未建
    (SECTOR_BENCH 全是美股 ETF,拿來對台股 = 市場/幣別都錯)→ None(按該市場大盤計,
    coverage 誠實反映「無板塊對照」,對齊 prd-ledger §2.4 台股第一版)。"""
    sec, _ = driver(t)
    if sec in BENCH_SELF:
        return t
    if market != "US":
        return None
    return SECTOR_BENCH.get(sec)

def _alpha_grade(r):
    """α 分級:數字永遠出,語氣看統計(#4 誠實鐵律 v2:「不夠厚不出數」→「出數 + 說得清多不確定」)。
    significant = 樣本 ≥252 交易日 且 |t|≥1.96 → 可用能力語氣(正負皆然,顯著的負 α 也是定論);
    suggestive = 1≤|t|<1.96 → 有跡象未達顯著;noise = 其餘 → 分不出本事還是運氣。
    gate 記「為何到不了 significant」(#80/#82:機械欄位,卡片講原因不靠 Claude 記性)。"""
    t, n = r.get("alpha_t"), r.get("n", 0)
    if n >= ALPHA_MIN_DAYS and t is not None and abs(t) >= ALPHA_T_TH:
        return dict(grade="significant", gate=None)
    grade = "suggestive" if (t is not None and abs(t) >= 1) else "noise"   # 單一算式,門檻只在一處調
    reason = "sample_short" if n < ALPHA_MIN_DAYS else "not_significant"
    gate = (dict(reason="sample_short", n_days=n, need=ALPHA_MIN_DAYS) if reason == "sample_short"
            else dict(reason="not_significant", t=t, need=ALPHA_T_TH))
    return dict(grade=grade, gate=gate)

def _alpha_beta_for_market(rows_m, px, rf_annual, market):
    """單一市場子組合的 β/α + 拆帳(原 dim_alpha_beta 主體,基準改按市場查表)。
    回傳與舊頂層同名的欄位 dict(無 dim/tier),或 note dict。"""
    bench_main = MARKET_BENCH.get(market, "SPY")
    if bench_main not in list(getattr(px, "columns", [])):
        return dict(note=f"無價格/{bench_main}")
    proxy = {t: _sector_proxy(t, market) for t in {r["ticker"] for r in rows_m}}
    port, mimic, fb_share, unproxied = _port_daily_returns(rows_m, px, proxy=proxy,
                                                           fallback_bench=bench_main)
    aux = ["QQQ", "SOXX"] if market == "US" else []        # 參考基準只對美股有意義
    benchmarks = {}
    for b in [bench_main] + aux:
        if b in px.columns:
            r = _regress(port, px[b], rf_annual)
            if r: benchmarks[b] = r
    if bench_main not in benchmarks:
        return dict(note="樣本不足")
    main_r = benchmarks[bench_main]
    days = main_r["days"]                                  # 重用主基準回歸已對齊的天集(別再跑一次 _aligned,兩處才不會分岔)
    mimic_tot = float((1 + mimic.reindex(days)).prod() - 1)
    split = dict(excess=main_r["excess"],
                 allocation=mimic_tot - main_r["bench_tot"],  # 押對賽道:你的板塊配置混合 − 大盤
                 selection=main_r["port_tot"] - mimic_tot,    # 板塊內選股:你 − 板塊配置混合
                 mimic_tot=mimic_tot,
                 coverage=round(1.0 - fb_share, 4),        # 有板塊對照的平均權重;非美股市場第一版恆 0 = 全按大盤計
                 unproxied=sorted(unproxied),
                 # 只列「全程沒 fallback 過」的對照(t not in unproxied)——曾 fallback 的 ticker
                 # 已在 unproxied 誠實揭露,proxy 不該同時宣稱「配置的對照」聽起來像全程有效
                 proxy={t: p for t, p in sorted(proxy.items()) if p and t != p and t not in unproxied})
    g = _alpha_grade(main_r)
    stat = dict(alpha_ann=main_r["alpha_ann"], se_ann=main_r["alpha_se_ann"], t=main_r["alpha_t"],
                ci95=main_r["alpha_ci95"], n_days=main_r["n"], grade=g["grade"], gate=g["gate"])
    return dict(benchmarks=benchmarks, n=main_r["n"], bench=bench_main,
                beta=main_r["beta"], alpha_ann=main_r["alpha_ann"],
                alpha_stat=stat, excess_split=split,
                port_tot=main_r["port_tot"], spy_tot=main_r["bench_tot"],  # 鍵名沿舊契約:值=主基準 tot(TW=^TWII)
                excess_vs_spy=main_r["excess"])            # 同上:鍵名歷史遺留,語意=「贏該市場大盤」


def dim_alpha_beta(rows, data, rf_annual=RF_ANNUAL, market_weights=None):
    """E2:投組日報酬 vs 基準回歸 + 贏大盤拆帳(押對賽道 vs 板塊內選股)。
    per-market(#129 PR-2b,prd-ledger §2.4):各市場子組合對各自大盤(US→SPY、TW→^TWII),
    **不合成總 α**(混市場對單一基準 = 假精確)。單一市場 → 輸出 schema 與舊版恆等(scope/by_market
    為 None);混市場 → 頂層欄位 = 資金佔比最大市場的值 + scope 標明範圍,by_market 給全部。
    α 永遠出數、帶 SE/t/95% 區間;excess_split 配置+選股 ≡ 贏大盤 pp(會計恆等,#80)。"""
    try:
        import pandas as pd  # noqa
    except ImportError:
        return dict(dim="alpha/beta", note="無 pandas")
    if data is None or not len(list(getattr(data, "columns", []))):
        return dict(dim="alpha/beta", note="無價格/SPY")
    px = data.ffill()
    markets = sorted({r.get("market", "US") for r in rows})
    if len(markets) <= 1:
        m = markets[0] if markets else "US"
        r = _alpha_beta_for_market(rows, px, rf_annual, m)
        if r.get("note"):
            return dict(dim="alpha/beta", note=r["note"])
        return dict(dim="alpha/beta", tier=3, scope=None, by_market=None, **r)
    # 混市場:各跑各的;頂層代表值 = 資金佔比最大且回歸有效的市場(卡上仍應兩行並列,讀 by_market)
    by_market, weights = {}, {}
    for m in markets:
        rows_m = [r for r in rows if r.get("market", "US") == m]
        res = _alpha_beta_for_market(rows_m, px, rf_annual, m)
        by_market[m] = res
        weights[m] = (market_weights or {}).get(m)
        if weights[m] is None:                             # 無外部權重(直呼/測試)→ 按買入額原幣近似
            weights[m] = sum(r["qty"] * r["price"] for r in rows_m if r["side"] == "buy")
    valid = [m for m in markets if not by_market[m].get("note")]
    if not valid:
        return dict(dim="alpha/beta", note="樣本不足",
                    by_market=by_market, scope=None)
    top = max(valid, key=lambda m: weights.get(m) or 0)
    out = dict(dim="alpha/beta", tier=3, scope=top, by_market=by_market)
    out.update(by_market[top])                             # 頂層 = scope 市場的欄位(消費者相容;卡上必標「僅含 {scope} 部位」)
    return out

def alpha_credible(ab):
    """α 可用「能力語氣」嗎:樣本 ≥1 年 且 |t|≥1.96(alpha_stat.grade == significant)。
    v2(#80):持倉檔數/集中度閘門退役——兩個舊閘門的意圖各自有了直接測量:
      「集中 → 雜訊大」由回歸 SE 直接量(集中 → 殘差大 → t 低,自然過不了);
      「押賽道 ≠ 選股」由 excess_split 拆帳正面回答(描述性,永遠可出)。
    無 alpha_stat / 有 note → fail-closed。"""
    if not isinstance(ab, dict) or ab.get("note"):
        return False
    return (ab.get("alpha_stat") or {}).get("grade") == "significant"

def print_alpha_beta(d):
    """獨立 Panel:把報酬拆成「運氣(大盤+賽道)」vs「技巧(選股)」。"""
    if not _HAS_RICH:
        return
    if d.get("note"):
        _console.print()
        _console.print(Panel(
            Text(d['note'], style="dim"),
            title="[bold]你的報酬怎麼來的[/]  [dim]· 運氣 vs 技巧[/]",
            title_align="left", border_style="cyan", padding=(1, 2), width=CARD_WIDTH,
        ))
        return
    bench = d.get("bench") or "SPY"                     # per-market(#129 PR-2b):主基準隨市場,別硬編 SPY(純台股=^TWII)
    bs = d["benchmarks"]; spy = bs[bench]
    port = spy["port_tot"]; vs_spy = spy["excess"]
    st = d.get("alpha_stat") or {}; sp = d.get("excess_split") or {}
    t = Text()
    if d.get("scope"):                                  # 混市場:人話卡只展示 scope 市場,要標明範圍(完整 per-market 在 TR_JSON)
        t.append(f"(僅含 {d['scope']} 部位;其他市場見 TR_JSON by_market)\n", style="dim")
    t.append(f"過去 {d['n']} 個交易日:投組 ")
    t.append(f"{port*100:+.0f}%", style="bold green" if port >= 0 else "bold red")
    t.append(f"、大盤 {bench} ")
    t.append(f"{spy['bench_tot']*100:+.0f}%", style="green" if spy['bench_tot'] >= 0 else "red")
    t.append(" → 你贏大盤 " if vs_spy >= 0 else " → 你輸大盤 ")
    t.append(f"{vs_spy*100:+.0f}pp", style="bold green" if vs_spy >= 0 else "bold red")
    t.append("\n\n① 這 ", style="bold")
    t.append(f"{vs_spy*100:+.0f}pp", style="bold")
    t.append(" 從哪來(對照=你當時的板塊配置混合,兩項相加=贏大盤):", style="bold")
    if sp:
        alloc, sel = sp["allocation"], sp["selection"]
        t.append("\n   押對賽道(板塊配置):  ")
        t.append(f"{alloc*100:+.0f}pp", style="bold green" if alloc >= 0 else "bold red")
        t.append("\n   板塊內選股:          ")
        t.append(f"{sel*100:+.0f}pp", style="bold green" if sel >= 0 else "bold red")
        if sp.get("coverage", 1.0) < 0.995 and sp.get("unproxied"):
            miss = "、".join(sp["unproxied"][:4])
            t.append(f"\n   (板塊對照覆蓋 {sp['coverage']*100:.0f}% 市值;{miss} 無板塊 ETF → 按大盤計、歸入選股)",
                     style="dim")
    else:
        t.append("\n   拆帳算不出(缺板塊價格)", style="dim")
    t.append("\n\n② α(vs 通用大盤,調風險後):  ", style="bold")
    if st:
        ci = st.get("ci95") or [None, None]
        t.append(f"年化 {st['alpha_ann']*100:+.0f}%", style="bold cyan")
        if st.get("se_ann") is not None:              # se_ann==0(完美複製品)是合法值,別被 truthy 檢查漏掉
            t.append(f"  (95% 區間 {ci[0]*100:+.0f}%~{ci[1]*100:+.0f}%)", style="dim")
        t.append(f"   β {spy['beta']:.2f} (波動是大盤 {spy['beta']:.1f} 倍)\n   ")
        if st.get("grade") == "significant":
            t.append("樣本 ≥1 年且統計顯著——這塊可以當能力談(正負都算數)。", style="bold")
        elif st.get("grade") == "suggestive":
            t.append("有跡象但未達顯著——傾向有,還不能下定論。", style="yellow")
        else:
            gate = st.get("gate") or {}
            why = "樣本不到 1 年" if gate.get("reason") == "sample_short" else "區間太寬(常見原因:持倉集中、個股雜訊大)"
            t.append(f"統計上分不出是本事還是運氣({why})——工具的侷限,不是說你沒本事;拆帳與行為層照樣能看。",
                     style="dim")
    else:
        t.append(f"β {spy['beta']:.2f} (波動是大盤 {spy['beta']:.1f} 倍)——α 統計量缺(樣本不足)", style="dim")
    t.append(f"\n\n(持倉法日報酬近似;α 基準={bench};拆帳=Brinson 式兩層,配置+選股=贏大盤)", style="dim italic")
    _console.print()
    _console.print(Panel(
        t,
        title="[bold]你的報酬怎麼來的[/]  [dim]· 把運氣(大盤+賽道)和技巧(選股)分開[/]",
        title_align="left", border_style="cyan", padding=(1, 2), width=CARD_WIDTH,
    ))

# ─────────────────────────── 5. 五維 metrics ───────────────────────────
MIN_WINNERS = 5     # winner_early 至少要這麼多「賣掉的贏家」才算可信(半年資料通常達得到)

def dim_exit(rts, fwds, n_fwd=N_FWD):
    # rts 已在 main 預先濾成「決策賣出」（排除大盤/債/商品 ETF 再平衡）
    early_rate = avg_forgone = winner_early = None
    n_winners = 0
    scored = [r for r in rts if "fwd" in r]
    n_trunc = sum(1 for r in scored if r.get("fwd_trunc"))
    if scored:
        early_rate = sum(1 for r in scored if r["fwd"] > SELL_EARLY_TH) / len(scored)
        avg_forgone = statistics.mean(r["fwd"] for r in scored)
        winners = [r for r in scored if r["ret"] > 0]                 # 賣掉賺錢的
        n_winners = len(winners)
        if winners:
            winner_early = sum(1 for r in winners if r["fwd"] > SELL_EARLY_TH) / len(winners)
    low_conf = n_winners < MIN_WINNERS    # 樣本太少 → 仍給數字但標「低信賴」,不直接降權消失
    win_holds = [r["hold"] for r in rts if r["ret"] > 0]
    lose_holds = [r["hold"] for r in rts if r["ret"] < 0]
    hw = statistics.median(win_holds) if win_holds else 0
    hl = statistics.median(lose_holds) if lose_holds else 0
    disp = hl - hw
    win_rate = sum(1 for r in rts if r["ret"] > 0) / len(rts) if rts else 0
    sev = max((early_rate or 0), (winner_early or 0), (avg_forgone or 0)/0.2, disp/60)
    if low_conf: sev *= 0.7              # 低樣本只打折,不歸零——半年資料也看得到方向
    trig = (early_rate is not None and early_rate > 0.5) or (winner_early is not None and winner_early > 0.5) \
           or (avg_forgone is not None and avg_forgone > 0.08) or disp > 20
    return dict(dim="出場紀律", tier=1, triggered=trig, severity=min(max(sev,0),1),
                early_rate=early_rate, avg_forgone=avg_forgone, winner_early=winner_early,
                disp_gap=disp, hold_win=hw, hold_lose=hl, sell_win_rate=win_rate,
                n_rt=len(rts), n_scored=len(scored), n_trunc=n_trunc, n_fwd=n_fwd,
                n_winners=n_winners, low_conf=low_conf)

def dim_size(rows, held, last_px):
    # 用市值（有 yf）或成本算當前權重（rows 參數保留簽名相容;entry-size 序列已移除:
    # 從未進輸出,且混幣下 cum 會跨幣別亂加 —— 兩輪 review 均判 dead code,2026-07-06 刪）
    vals = {}
    for t, (sh, cost) in held.items():
        px = (last_px or {}).get(t)
        vals[t] = sh * px if px else cost
    tot = sum(vals.values()) or 1
    weights = {t: v / tot for t, v in vals.items()}
    max_t = max(weights, key=weights.get) if weights else None
    max_pct = weights.get(max_t, 0)
    sev = min(max((max_pct - 0.20) / 0.30, 0), 1)
    others = [w for t, w in weights.items() if t != max_t]      # 「其餘平均」要排除最大那檔,否則 mean(全部)=1/檔數、跟集中度無關,還會跟「最大佔 X%」自相矛盾
    return dict(dim="部位 sizing", tier=1, triggered=max_pct > 0.25,
                severity=sev, max_ticker=max_t, max_pct=max_pct,
                avg_pct=statistics.mean(others) if others else 0.0, weights=weights)

def meaningful_tickers(held, last_px, floor=RESIDUAL_POS_TH):
    """回傳「非殘倉」的 ticker set:市值佔全持倉 ≥ floor(預設 0.1%)。市值缺價用成本近似。
    殘倉(股息零頭/賣到剩 1 股的尾倉)不該灌 n_holdings/分散度/what-if/per-ticker 診斷/未分類計數(#172)。
    **只給診斷用,不動 overview/P&L**:重倉崩 99% 的部位市值雖小、在此不進集中度診斷,但它的未實現虧損仍留在總覽(不藏虧損)。
    相對佔比自適應帳戶規模(owner 排除絕對股數/金額門檻)。全零市值(理論邊界)→ 全保留,不誤殺。"""
    vals = {}
    for t, (sh, cost) in (held or {}).items():
        px = (last_px or {}).get(t); vals[t] = sh * px if px else cost
    tot = sum(vals.values())
    if tot <= 1e-9:
        return set(vals)
    return {t for t, v in vals.items() if v / tot >= floor}

def dim_diversify(held, last_px):
    vals = {}
    for t, (sh, cost) in held.items():
        px = (last_px or {}).get(t); vals[t] = sh * px if px else cost
    tot = sum(vals.values()) or 1
    w = {t: v / tot for t, v in vals.items()}
    sec = defaultdict(float); ai = 0.0
    for t, wt in w.items():
        s, is_ai = driver(t); sec[s] += wt; ai += wt * is_ai
    classified_sec = {s: v for s, v in sec.items() if s != "未分類"}   # 排除未分類桶,避免 driver_map 沒建好冒充集中度訊號(對齊 what_if() 既有作法)
    max_sec = max(classified_sec, key=classified_sec.get) if classified_sec else None
    max_sec_pct = classified_sec.get(max_sec, 0)
    top3 = sum(sorted(w.values(), reverse=True)[:3])
    sev = min(max((max(max_sec_pct, ai) - 0.40) / 0.40, 0), 1)
    trig = (len(w) >= 8 and max_sec_pct > SECTOR_MAX_TH) or top3 > 0.60 or ai > 0.60
    return dict(dim="分散", tier=2, triggered=trig, severity=sev, n=len(w),
                max_sector=max_sec, max_sector_pct=max_sec_pct, ai_pct=ai,
                top3=top3, sectors=dict(sec))

def dim_hold(rts):
    # B.4 修(2026-06-13)：改判「同一檔內的時間框架一致性」，不再用整組合 IQR。
    # 理由：長線+短線混合本來就跨度大（owner 中位 127 天卻被舊版誤判 sev 1.0）。
    # 真問題是「同一檔 ticker 又當沖又長抱」= 沒有一致框架；不同檔用不同框架是合理的兩套策略。
    hs = [r["hold"] for r in rts]
    if not hs:
        # 無已實現 round-trip(全買未賣)→ keys 補空,讓下游 number_line 不 KeyError
        return dict(dim="持有時間", tier=2, triggered=False, severity=0,
                    median_hold=0, iqr=0, min=0, max=0,
                    incon_rate=0, n_incon=0, n_multi=0, incon_tickers=[],
                    no_data=True)
    med = statistics.median(hs)
    q = sorted(hs); iqr = (q[int(.75*len(q))-1] - q[int(.25*len(q))]) if len(q) > 3 else 0
    overtrading = (5 - med) / 5 if med < 5 else 0          # 中位過短 = 疑似無 edge 的過度交易
    by_ticker = defaultdict(list)
    for r in rts:
        by_ticker[r["ticker"]].append(r["hold"])
    multi = {t: hl for t, hl in by_ticker.items() if len(hl) >= 3}   # 只看有 ≥3 次 round-trip 的檔
    incon = {t: hl for t, hl in multi.items() if any(h < 5 for h in hl) and any(h > 30 for h in hl)}
    incon_rate = len(incon) / len(multi) if multi else 0  # 同檔又當沖(<5d)又長抱(>30d)的比例
    sev = min(max(max(overtrading, incon_rate), 0), 1)
    return dict(dim="持有時間", tier=2, triggered=(med < 5 or incon_rate > 0.3),
                severity=sev, median_hold=med, iqr=iqr, min=min(hs), max=max(hs),
                incon_rate=incon_rate, n_incon=len(incon), n_multi=len(multi),
                incon_tickers=sorted(incon.keys()))

def dim_avgdown(avg_down, held, last_px, size_dim):
    # #94:breach 判準改用加碼「當下」的成本權重(positions() 算好放在 weight_then),
    # 不再查 size_dim["weights"]——那是用「今天」市值回推的單一快照,會把今天的重倉/輕倉
    # 誤套到三年前或上週的每一筆歷史加碼決定上,方向可能整個判反(見 issue #94)。
    cnt = len(avg_down)
    breach = 0
    for e in avg_down:
        w = e.get("weight_then", 0)
        if w > 0.25: breach += 1
    # breach（攤平破 size 上限）才是危險訊號；原始攤平次數對常買 dip 的人不可靠 → 降權 + 封頂 0.8
    # （spec C-limit：無法從交易區分計畫性建倉 vs 恐慌攤平 → 此維以「問句」呈現，不當高信心判決）
    # B.5 修(2026-06-13)：觸發改為 breach 主導 —— 攤平到「破自己 size 上限」才是洞；
    # 純次數高（常買 dip 的 DCA）只當資訊，不入卡（owner 143 次/0 breach 被舊版誤排第三）。
    sev = min(0.5*breach + cnt/600, 0.8)
    tickers = sorted({e["ticker"] for e in avg_down})
    return dict(dim="加碼攤平", tier=1, triggered=(breach >= 1),
                severity=sev, count=cnt, breach=breach, tickers=tickers)

# ── 【風格】維雛形(v2a,解鎖 v2c 誠實閥)──────────────────────────────────────
# 與普世維不同:這維不是「洞」,是『風格軸』——各派對它給相反 stance(動能派稱讚追高、
# 價值派斥責追高)。研究依據與設計見 docs/style-detection-research.md。先不接 lens/閥,
# 只算訊號 + 印讀數,驗算得準不準。
MIN_ENTRY_BUYS = 15        # 研究:n≳15 才分得開 0.65(動能) vs 0.50(中性);不足 → 低信賴
ENTRY_LOOKBACK = 252       # 52 週 = 一年交易日(George-Hwang 錨)

def dim_entry_style(rows, data, lookback=ENTRY_LOOKBACK):
    """進場相對位置(追高 vs 抄底):每筆 BUY 在過去一年價格區間的位置。
    range_pct≈1 = 買在區間頂(追高/動能, lean=strength);≈0 = 買在區間底(抄底/逆勢, lean=weakness)。
    紀律:① point-in-time(只用進場前的價,防 look-ahead)② 只報方向不報精確值(漂移容錯)
    ③ 樣本不足回低信賴 ④ 對事不對人——這是風格不是洞。
    依賴 adjust_for_splits 已把成交價對齊今日基礎,否則跨分割的區間會錯 10 倍。"""
    if data is None:
        return dict(dim="進場", axis="style", tier=2, triggered=False, severity=0,
                    lean=None, n=0, low_conf=True, note="無價格(需 yfinance)")
    import pandas as pd
    cols = set(getattr(data, "columns", []))
    pcts, forms = [], []
    for r in rows:
        if r["side"] != "buy" or r["ticker"] not in cols:
            continue
        col = data[r["ticker"]].dropna()
        prior = col[col.index < pd.Timestamp(r["date"])].tail(lookback)   # 只用進場前的價
        if len(prior) < 30:                                               # 歷史太短不定位
            continue
        lo, hi = float(prior.min()), float(prior.max())
        if hi - lo < 1e-9:
            continue
        pcts.append(min(max((r["price"] - lo) / (hi - lo), 0.0), 1.0))
        forms.append(r["price"] / float(prior.iloc[0]) - 1)               # 形成期報酬(窗起→進場)
    n = len(pcts)
    if n == 0:
        return dict(dim="進場", axis="style", tier=2, triggered=False, severity=0,
                    lean=None, n=0, low_conf=True, note="無可定位的買入")
    med = statistics.median(pcts)
    low_conf = n < MIN_ENTRY_BUYS
    lean = "strength" if med > 0.70 else "weakness" if med < 0.30 else None
    sev = min(max(abs(med - 0.5) * 2, 0), 1)
    if low_conf:
        sev *= 0.7
    return dict(dim="進場", axis="style", tier=2, triggered=(lean is not None and not low_conf),
                severity=sev, lean=lean, median_pct=med,
                median_form=statistics.median(forms), n=n, low_conf=low_conf)

# ─────────────────────────── 6. 卡片選擇 + 渲染 ───────────────────────────
# ── 鏡片層(可換大師):洞的「規矩 + 引言」來自 lens 檔,engine 不 hardcode VY ──
_LENS = None
DEFAULT_LENS = os.path.join(os.path.dirname(__file__), "..", "rubric", "vincent-yu.lens.json")
def load_lens(path=DEFAULT_LENS):
    """載入鏡片檔(規矩/引言/找動機問句)。換大師 = 換這個檔,engine 不動。"""
    global _LENS
    import json
    try:
        with open(path, encoding="utf-8") as f: _LENS = json.load(f)
        return _LENS.get("philosophy")
    except (OSError, ValueError): return None

CARD_LIB_FALLBACK = {
 "出場紀律": ("賣出前先寫一句『我賣的理由是 thesis 破了，還是手癢/想換現金?』",
              "在清醒時先把出場規則寫好，把判斷從『當下』移到『事前』。"),
 "部位 sizing": ("下單前先決定『這筆最多佔幾 %、為什麼是這個數而不是兩倍』。",
                 "門檻低只配小部位，門檻高才配得起大部位。"),
 "分散": ("加新倉前先問『它跟我最大那塊是不是同一個 driver?』是 → 不加。",
          "分散不是檔數多，是讓持有的標的來自不同的 underlying drivers。"),
 "持有時間": ("每筆進場先標『短線/波段/長線』，出場只准用同框架的理由。",
              "先想清楚你的時間軸，讓所有後續分析匹配它。"),
 "加碼攤平": ("往下加碼前必須寫出『一個進場時不知道的新證據』；寫不出 → 不加。",
              "不要出現『再加碼就能回本』就破線。"),
}
def card_for(dim):
    """(rule, quote):優先用 lens 檔(可換大師),載入失敗用 fallback。"""
    if _LENS and dim in _LENS.get("dims", {}):
        d = _LENS["dims"][dim]; m = _LENS.get("philosophy", "鏡片")
        return d.get("rule", ""), f"{d.get('quote', '')}（{m}）"
    return CARD_LIB_FALLBACK.get(dim, ("", ""))

def dim_strength(exit_dim, size_dim, avgdown_dim, div_dim, hold_dim, rts=None):
    """負回饋循環解藥:卡片先給『你做對的最強一件事』再給洞。白話 + 附具體案例,不講黑話。
    (demand-side 研究:看虧損=ego受傷;先肯定 → 降低防衛 → 才聽得進那一刀。)"""
    c = []
    we = exit_dim.get("winner_early")
    if we is not None and we < 0.35 and not exit_dim.get("low_conf"):
        eg = ""
        if rts:   # 找一筆「賣了賺錢、賣完幾乎沒再漲」的具體案例佐證
            cand = [r for r in rts if r.get("ret", 0) > 0.10 and r.get("fwd") is not None and r["fwd"] < 0.03]
            if cand:
                b = max(cand, key=lambda r: r["ret"])
                eg = f"（例:{b['ticker']} 你賺 {b['ret']*100:.0f}% 出場,賣完它只動 {b['fwd']*100:+.0f}%——沒賣早）"
        c.append((0.7+(0.35-we), f"該獲利了結時你不手軟:賣掉的賺錢單只有 {we*100:.0f}% 事後繼續漲,代表你不會「抱著賺錢的捨不得賣、結果回吐」{eg}"))
    mp = size_dim.get("max_pct", 1)
    if mp < 0.22:
        c.append((1-mp, f"單筆部位有控制:押最重的一檔也只佔 {mp*100:.0f}%,沒把身家壓在一檔上"))
    if avgdown_dim.get("breach", 1) == 0 and avgdown_dim.get("count", 0) >= 2:
        _egt = (avgdown_dim.get("tickers") or [""])[0]         # 帶一個具體標的當案例;「爆倉」是黑話,改人話「越攤越重」
        c.append((0.65, f"你往下加碼 {avgdown_dim['count']} 次,卻都守在自己的部位上限內、沒讓任何一檔越攤越重"
                        + (f"(例:{_egt})" if _egt else "")))
    if not div_dim.get("triggered") and div_dim.get("n", 0) >= 5:
        c.append((0.6, f"{div_dim['n']} 檔分布在不同 driver,沒有全押在同一個故事上"))
    if not hold_dim.get("triggered") and hold_dim.get("median_hold"):
        c.append((0.5, f"進出有一致的節奏:中位持有 {hold_dim['median_hold']:.0f} 天,不是隨機亂買亂賣"))
    if not c: return None
    c.sort(reverse=True); return c[0][1]

def best_worst(rts):
    """結果最好 / 最差的具體 round-trip,給卡片當案例(點:列出做得最好跟最不好的決策)。"""
    closed = [r for r in rts if r.get("ret") is not None]
    if not closed: return None, None
    return max(closed, key=lambda r: r["ret"]), min(closed, key=lambda r: r["ret"])

def overview_stats(rts, ab, held=None, last_px=None):
    """金額導向總覽:已實現(賣掉落袋)+ 未實現(還抱著)都要算,只報一個會失真。"""
    pnls = [r["qty"] * (r["sell_px"] - r["buy_px"]) for r in rts
            if r.get("sell_px") and r.get("buy_px")]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
    win_sum, loss_sum = sum(wins), sum(losses)      # loss_sum 為負
    avg_win = win_sum / len(wins) if wins else 0
    avg_loss = loss_sum / len(losses) if losses else 0
    payoff = avg_win / abs(avg_loss) if avg_loss else None       # 無已實現虧損 → None(非 0,別跟真 0 混淆);#21.2 補完
    pf = win_sum / abs(loss_sum) if loss_sum else 0              # 賺總額 / 賠總額(獲利因子)
    realized = win_sum + loss_sum
    held = held or {}
    unpriced = sorted(t for t in held if not (last_px and t in last_px))  # 沒抓到現價的持倉(#82:結構化揭露,別讓未實現靜默漏算)
    unrealized = sum(sh * last_px[t] - c for t, (sh, c) in held.items()
                     if last_px and t in last_px)                 # 還抱著的帳面盈虧(僅涵蓋有現價者)
    unrealized_coverage = dict(priced_n=len(held) - len(unpriced), held_n=len(held), unpriced=unpriced)
    return dict(n_rt=len(rts), realized=realized, unrealized=unrealized,
                unrealized_coverage=unrealized_coverage,
                total_pnl=realized + unrealized, win_sum=win_sum, loss_sum=loss_sum,
                n_wins=len(wins), n_losses=len(losses), avg_win=avg_win, avg_loss=avg_loss,
                payoff=payoff, pf=pf, ab=ab)

def payoff_attribution(rts, top_n=4):
    """盈虧比拆解(每次復盤都算『幾個重點交易的貢獻度』):把已實現 round-trip 的賺/賠
    歸到標的——誰在撐 win_sum、誰在拖 loss_sum,各佔該池多少 %;再算【拿掉最大拖累標的後
    payoff 變多少】(反事實)。只看已實現(payoff 的定義),抱著的浮盈贏家不在此池。"""
    pnl = lambda r: r["qty"] * (r["sell_px"] - r["buy_px"])
    closed = [r for r in rts if r.get("sell_px") and r.get("buy_px")]
    if not closed:
        return None
    pnls = [pnl(r) for r in closed]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
    win_sum, loss_sum = sum(wins), sum(losses)                  # loss_sum < 0
    avg_win = win_sum / len(wins) if wins else 0.0
    avg_loss = loss_sum / len(losses) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss else None    # 無已實現虧損 → None(∞),不報 0(codex)
    by = defaultdict(lambda: {"win": 0.0, "loss": 0.0, "net": 0.0, "n": 0})
    for r in closed:
        p = pnl(r); a = by[r["ticker"]]; a["n"] += 1; a["net"] += p
        a["win" if p > 0 else "loss"] += p
    carriers = sorted(((t, a["win"], a["win"] / win_sum if win_sum else 0)        # 撐盤者(佔總賺 %)
                       for t, a in by.items() if a["win"] > 0), key=lambda x: -x[1])[:top_n]
    draggers = sorted(((t, a["loss"], a["loss"] / loss_sum if loss_sum else 0)    # 拖累者(佔總賠 %)
                       for t, a in by.items() if a["loss"] < 0), key=lambda x: x[1])[:top_n]
    cf = None                                                   # 反事實:拿掉最大拖累標的後的 payoff
    if draggers:
        worst = draggers[0][0]
        rest = [r for r in closed if r["ticker"] != worst]
        w = [pnl(r) for r in rest if pnl(r) > 0]; l = [pnl(r) for r in rest if pnl(r) < 0]
        aw = sum(w) / len(w) if w else 0.0; al = sum(l) / len(l) if l else 0.0
        cf = dict(ticker=worst, drag=by[worst]["net"],
                  payoff=(aw / abs(al)) if al else None)    # 拿掉後若再無虧損 → None(∞),非 0(codex)
    return dict(payoff=payoff, avg_win=avg_win, avg_loss=avg_loss,
                win_sum=win_sum, loss_sum=loss_sum, n=len(closed),
                carriers=carriers, draggers=draggers, counterfactual=cf)

def print_payoff_attr(pa):
    """獨立 Panel:已實現交易的貢獻度,誰在撐 vs 誰在拖,加反事實。"""
    if not _HAS_RICH:
        return
    if not pa:
        return
    fmt = lambda v: "—" if v is None else f"{v:.1f}"        # None=無虧損可比,別印 ∞(#21.2);人話在下方補
    t = Text()
    t.append("盈虧比 ")
    if pa["payoff"] is None:                                # 沒有任何已實現虧損 → 比率無意義,不印 ∞
        t.append("—", style="bold cyan")
        t.append(f"   {pa['n']} 筆已實現全是賺的,沒有虧損可拿來比\n")
    else:
        t.append(f"{fmt(pa['payoff'])}", style="bold cyan")
        t.append(f"   平均賺 ${pa['avg_win']:,.0f}  /  賠 ${abs(pa['avg_loss']):,.0f}  ({pa['n']} 筆已實現)\n")
    t.append("\n撐盤 ", style="bold green")
    t.append("(佔總賺):  ", style="dim green")
    t.append("、".join(f"{tk} ${w:,.0f}({p*100:.0f}%)" for tk, w, p in pa["carriers"]) or "(無已實現獲利)")
    t.append("\n拖累 ", style="bold red")
    t.append("(佔總賠):  ", style="dim red")
    t.append("、".join(f"{tk} ${l:,.0f}({p*100:.0f}%)" for tk, l, p in pa["draggers"]) or "(無已實現虧損)")
    cf = pa["counterfactual"]
    if cf:
        if cf["payoff"] is None:                            # 拿掉最大拖累後就沒有虧損了 → 它是唯一拖累
            t.append(f"\n\n→ 它是你唯一的已實現虧損:拿掉 {cf['ticker']} (淨 ${cf['drag']:,.0f}) 後,"
                     f"已實現就只剩賺的、沒有虧損可比了", style="dim")
        else:
            t.append(f"\n\n→ 拿掉最大拖累 {cf['ticker']} (淨 ${cf['drag']:,.0f}) 後,盈虧比 ", style="dim")
            t.append(f"{fmt(pa['payoff'])}", style="dim")
            t.append(" → ", style="dim")
            t.append(f"{fmt(cf['payoff'])}", style="bold cyan")
    _console.print()
    _console.print(Panel(
        t,
        title="[bold]盈虧比拆解[/]  [dim]· 誰在撐、誰在拖(已實現交易的貢獻度)[/]",
        title_align="left", border_style="cyan", padding=(1, 2), width=CARD_WIDTH,
    ))

def what_if(held, last_px, threshold=0.25):
    """基於用戶實際持倉動態挑「最大集中暴險」做壓測,而非寫死 AI。
    三個候選:① AI thematic(跨多 sector,driver[1]==1)② 最大 sector ③ 最大個股。
    取佔比最高 且 ≥ threshold 那個當壓測標的;若三者都 < threshold(真正分散)→ return None。
    壓測情境:回檔 30%(一般修正)/ 50%(深熊)。"""
    if not last_px: return None
    mv = {t: sh * last_px[t] for t, (sh, c) in held.items() if t in last_px}
    tot = sum(mv.values())
    if tot <= 0: return None

    # 候選 1:AI thematic 全集(跨 sector)
    ai_mv = sum(v for t, v in mv.items() if driver(t)[1] == 1)
    ai_pct = ai_mv / tot

    # 候選 2:最大 sector(排除「未分類」,避免 driver map 沒載入時誤觸發)
    sector_mv = defaultdict(float)
    for t, v in mv.items():
        sec = driver(t)[0]
        if sec != "未分類":
            sector_mv[sec] += v
    if sector_mv:
        max_sec, max_sec_mv = max(sector_mv.items(), key=lambda x: x[1])
        max_sec_pct = max_sec_mv / tot
    else:
        max_sec, max_sec_mv, max_sec_pct = None, 0.0, 0.0

    # 候選 3:最大個股
    max_t, max_t_mv = max(mv.items(), key=lambda x: x[1])
    max_t_pct = max_t_mv / tot

    # 候選清單(只收 ≥ threshold 的;label/mval/pct)
    cands = []
    if ai_pct >= threshold:
        cands.append(("AI 概念股(跨板塊)", ai_mv, ai_pct, "AI 概念股回檔"))
    if max_sec and max_sec_pct >= threshold:
        # 若 AI thematic 已是候選且涵蓋了這個 sector,避免重複(AI 通常 ≥ max_sec)
        if not (ai_pct >= threshold and ai_pct >= max_sec_pct):
            cands.append((f"「{max_sec}」板塊", max_sec_mv, max_sec_pct, f"{max_sec}回檔"))
    if max_t_pct >= threshold:
        # 若 AI 或 sector 已涵蓋且佔比更高,個股不再重複(避免「單檔 NVDA 50%」與「半導體 50%」並列)
        if max_t_pct > max(ai_pct, max_sec_pct):
            cands.append((f"單檔 {max_t}", max_t_mv, max_t_pct, f"{max_t} 個股回檔"))

    if not cands: return None
    label, mval, pct, scenario_prefix = max(cands, key=lambda x: x[2])  # 取佔比最高那個
    return dict(label=label, mval=mval, pct=pct, scenario_prefix=scenario_prefix,
                drop30=mval * 0.30, drop50=mval * 0.50)

def time_trend(rts, avg_down):
    """時間維度:按年看關鍵行為指標,讓用戶看到自己『有沒有在進步』(復盤的留存核心)。"""
    from collections import defaultdict
    yr = defaultdict(lambda: {"pnl": 0.0, "w": 0, "l": 0, "we_w": 0, "we_n": 0, "ad": 0})
    for r in rts:
        y = r["exit"].year
        yr[y]["pnl"] += r["qty"] * (r["sell_px"] - r["buy_px"])
        yr[y]["w" if r["ret"] > 0 else "l"] += 1
        if r.get("ret", 0) > 0 and r.get("fwd") is not None:
            yr[y]["we_n"] += 1
            if r["fwd"] > SELL_EARLY_TH: yr[y]["we_w"] += 1
    for e in avg_down:
        yr[e["date"].year]["ad"] += 1
    return dict(sorted(yr.items()))

def prescribe(ab, dims, overview):
    """處方層:從歸因 + 診斷生成優化路徑——揚長 edge / 外包短板 / 砍損耗。每條盡量附可驗 metric。
    關鍵:處方是『放大你強的 + 外包你弱的』,不是通用避短。力量來自歸因精確(ChatGPT 沒你的數字不敢這樣說)。"""
    dd = {d["dim"]: d for d in dims}
    rx = []
    bs = (ab or {}).get("benchmarks", {})
    sp = (ab or {}).get("excess_split") or {}
    st = (ab or {}).get("alpha_stat") or {}
    bench_main = (ab or {}).get("bench") or "SPY"            # per-market(#129 PR-2b):純台股=^TWII,硬編 SPY 會整段跳過
    if bench_main in bs and sp:                              # 拆帳(配置 vs 選股)取代舊「換基準看敏感度」:恆等式,不會翻
        spy = bs[bench_main]
        alloc, sel = sp["allocation"], sp["selection"]
        if spy["excess"] > 0.10 and alloc >= max(sel, 0.0):  # 贏大盤且賽道佔大頭 → 揚長是「假設」不是「定論」
            rx.append(dict(kind="揚長(假設,待驗證)", verify="記錄『下一個賽道』判斷,事後對帳",
                           text=(f"你贏大盤 {spy['excess']*100:+.0f}pp 裡,押對賽道佔 {alloc*100:+.0f}pp(拆帳)。"
                                 "但這只是『假設你有方向判斷力』,不是已證實的 edge——押對 AI 也可能只是站到風口。"
                                 "壓測它:寫下你『下一個看好的賽道』、記時間,看未來兩三次準不準,對了才叫 edge。")))
        if not ab.get("credible"):                           # t 不顯著 → 不下選股能力定論(外包/真 edge)
            t_note = (f"α 的 95% 區間還太寬(t={st['t']:.1f})" if st.get("t") is not None
                      else "α 統計量還算不穩")
            rx.append(dict(kind="選股:資料不足以判定", text=(
                f"拆帳看,板塊內選股貢獻 {sel*100:+.0f}pp(描述性、這數字站得住);但 {t_note},"
                f"統計上還分不出選股是本事還是運氣——別急著外包、也別自滿。")))
        elif sel < -0.05 or st.get("alpha_ann", 0) < 0:      # 統計站得住且選股在虧 → 外包
            rx.append(dict(kind="外包短板(漸進)", verify="被動部位佔比(升→好)",
                           text=(f"扣掉賽道,你板塊內選股貢獻 {sel*100:+.0f}pp、α 統計上站得住地差——"
                                 "優化不是『別選股』(你享受它、ETF 也會錯過妖股),"
                                 "是『撥一部分資金被動化托底』,選股當衛星。(流程建議,非標的建議)")))
        elif sel > 0.05:                                      # 統計站得住且選股在賺 → 真 edge
            rx.append(dict(kind="揚長", text=(
                f"扣掉賽道紅利,你板塊內選股仍貢獻 {sel*100:+.0f}pp、α 統計顯著——"
                "這是真 edge,別讓 sizing/紀律稀釋它。")))
    ad = dd.get("加碼攤平", {})
    if ad.get("count", 0) >= 10 or ad.get("breach", 0) >= 1:
        rx.append(dict(kind="砍損耗", dim="加碼攤平", verify="虧損加碼次數(降→好)",
                       rule="虧損部位一律不加碼;真想加,先整筆賣掉隔天重買(逼你重新面對『現在還會買它嗎』)",
                       text=f"虧損中加碼 {ad.get('count', 0)} 次是你操盤損耗的大宗——這是最該先砍的純扣分動作。"))
    sz = dd.get("部位 sizing", {})
    if sz.get("max_pct", 0) > 0.30:                          # #29:解開互斥 gate,攤平與 sizing 可同時成候選(讓 candidate_rules 能 2-3 條)
        rx.append(dict(kind="砍損耗", dim="部位 sizing", verify="單筆最大佔比(降→好)",
                       rule=f"單筆部位上限定死 20%,超過就減",
                       text=f"最大一筆 {sz.get('max_ticker')} 佔 {sz.get('max_pct', 0)*100:.0f}%,單一押注過重。"))
    return rx

def ticker_diagnosis(rts, adds_class, held, last_px, top_n=7):
    """標的層診斷(對事不對人):每檔金額影響(已實現+未實現)+ 行為標籤,按 |金額| 排序只取 top。
    加碼用主從分類器(classify_adds)分疑似定投/凹單/待確認,不再用純結果判(避 outcome bias);
    出場叫『賣後機會成本』不叫『賣太早』(去事後諸葛審判語氣)。"""
    last_px = last_px or {}                # 無 yfinance/下載失敗 → last_px=None,降級成只用已實現,不 crash
    agg = defaultdict(lambda: dict(realized=0.0, unreal=0.0, win_n=0, win_early=0,
                                   cur_ret=None, mval=0.0))
    for r in rts:
        a = agg[r["ticker"]]
        a["realized"] += r["qty"] * (r["sell_px"] - r["buy_px"])
        if r.get("ret", 0) > 0 and r.get("fwd") is not None:
            a["win_n"] += 1
            if r["fwd"] > SELL_EARLY_TH: a["win_early"] += 1
    tot_mval = sum(sh * last_px[t] for t, (sh, c) in held.items() if t in last_px) or 1.0
    for t, (sh, cost) in held.items():
        px = last_px.get(t)
        if px:
            a = agg[t]
            a["unreal"] = sh * px - cost
            a["cur_ret"] = (px - cost / sh) / (cost / sh) if sh else 0
            a["mval"] = sh * px
    out = []
    for t, a in agg.items():
        impact = a["realized"] + a["unreal"]
        if abs(impact) < 1: continue
        cur, wpct, tags = a["cur_ret"], a["mval"] / tot_mval, []
        ac = adds_class.get(t) or {}
        cls, n_adds = ac.get("cls"), ac.get("n_adds", 0)
        if cls == "疑似凹單":                         # 主從分類:只在虧損買 + 金額加速
            if cur is not None and cur < -0.10:
                tags.append(f"✗疑似凹單:只在虧損加碼 {n_adds} 次、現虧 {cur*100:.0f}%(待你確認 thesis)")
            else:
                tags.append(f"⚠疑似凹單(現賺):只在虧損加碼 {n_adds} 次——賺回來像運氣,不是紀律")
        elif cls == "待確認" and n_adds >= 4:
            tags.append(f"？加碼 {n_adds} 次待確認:是定投還是凹單,要你定")
        elif cls == "疑似定投":
            tags.append(f"✓疑似定投:漲跌都買/規律 {n_adds} 次,不是凹單")
        if cls != "疑似凹單" and cur is not None and cur < -0.40:
            tags.append(f"✗套牢:{cur*100:.0f}% 還抱著沒處理")
        if a["win_n"] >= 2 and a["win_early"] / a["win_n"] > 0.5:
            tags.append(f"賣後機會成本:{a['win_early']}/{a['win_n']} 筆賣完它還漲(非審判,看你出場規則一致嗎)")
        if wpct > 0.25:
            tags.append(f"⚠押太重:佔組合 {wpct*100:.0f}%")
        if cur is not None and cur > 0.20 and cls not in ("疑似凹單", "待確認"):
            tags.append(f"✓紀律持有:賺 {cur*100:.0f}%")
        if not tags:
            tags.append("— 大致中性")
        thesis_q = None                              # 只對疑似凹單/待確認問 thesis(定投不問)
        if cls in ("疑似凹單", "待確認") and n_adds >= 4 and cur is not None:
            if cur < 0:
                thesis_q = (f"虧損中加碼 {n_adds} 次、現在還虧 {cur*100:.0f}%——"
                            f"你還相信當初買它的理由嗎,還是只是不想認賠、想攤低等回本?")
            else:
                thesis_q = (f"虧損中加碼 {n_adds} 次、現在賺 {cur*100:.0f}%——"
                            f"這是進場前定好的『定期買、長期持有』,還是套牢後才合理化、剛好漲回?")
        out.append(dict(ticker=t, impact=impact, tags=tags, thesis_q=thesis_q))
    out.sort(key=lambda x: abs(x["impact"]), reverse=True)
    return out[:top_n]
def number_line(d):
    n = d["dim"]
    if n == "出場紀律":
        s = []
        if d["early_rate"] is not None:
            we = f"；賣掉賺錢的有 {d['winner_early']*100:.0f}% 續漲（賣太早）" if d.get("winner_early") is not None else ""
            s.append(f"{d['n_rt']} 筆決策賣出（{d['n_scored']} 有 fwd、{d['n_trunc']} 截斷）中 {d['early_rate']*100:.0f}% 在 {d.get('n_fwd', N_FWD)} 天後更高、平均續漲 {d['avg_forgone']*100:+.1f}%{we}")
        s.append(f"賺錢抱 {d['hold_win']:.0f} 天 / 賠錢抱 {d['hold_lose']:.0f} 天（處置缺口 {d['disp_gap']:+.0f}）")
        return "；".join(s)
    if n == "部位 sizing":
        return f"你最大一筆 {d['max_ticker']} 佔 {d['max_pct']*100:.0f}%，其餘平均 {d['avg_pct']*100:.0f}%"
    if n == "分散":
        return f"你持有 {d['n']} 檔看似分散，但 AI capex 暴險 {d['ai_pct']*100:.0f}%、最大板塊「{d['max_sector']}」{d['max_sector_pct']*100:.0f}%、top3 {d['top3']*100:.0f}%——同一個 driver"
    if n == "持有時間":
        if d.get("no_data"):
            return "暫無已實現 round-trip,持有時間統計待生成（只看買進尚未賣出的不納入）"
        base = f"你持有時間 {d['min']}~{d['max']} 天、中位 {d['median_hold']:.0f} 天"
        if d.get("n_incon", 0) > 0:
            return base + f"；其中 {d['n_incon']}/{d['n_multi']} 檔同一檔又當沖又長抱（{', '.join(d['incon_tickers'][:5])}）——同檔沒有一致框架"
        return base + f"（中位 {d['median_hold']:.0f} 天 = 你的主框架；同檔框架大致一致）"
    if n == "加碼攤平":
        return f"你有 {d['count']} 次在虧損倉往下加碼（{', '.join(d['tickers'][:6])}），其中 {d['breach']} 次加到 >25%"
    return ""

def _money(v, with_sign=True):
    """金額帶 +/- 並上色（綠正紅負）；with_sign=False 時不強制正號。"""
    s = f"{v:+,.0f}" if with_sign else f"{v:,.0f}"
    return Text(s, style="bold green" if v >= 0 else "bold red")

def _pct(v, unit="%", bold=False):
    """unit='pp' 用在「超額報酬」對比,'%' 用在「個股/組合報酬率」。"""
    s = f"{v*100:+.0f}{unit}"
    style = ("bold " if bold else "") + ("green" if v >= 0 else "red")
    return Text(s, style=style)

# ── headline 選卡的唯一事實源(#63)──────────────────────────────────────────
# render / build_state / build_card_data 一律走這裡,不各自複製 tier 權重與排序 →
# 測試斷言 _pick_headline() 就是斷言引擎真正的選卡:tier 權重翻轉、severity 錨動,一定紅。
HEADLINE_TIER_W = {1: 1.0, 2: 0.7}   # tier1(出場/sizing/攤平)壓 tier2(分散/持有/風格)

def _rank_holes(dims):
    """triggered 維按 severity × tier 權重(高→低)= 頭號洞優先序。空 = 無 triggered。"""
    return sorted((d for d in dims if d.get("triggered")),
                  key=lambda d: d.get("severity", 0) * HEADLINE_TIER_W.get(d.get("tier", 2), 0.7),
                  reverse=True)

def _pick_headline(dims):
    """頭號洞 = 排序後第一個;無 triggered → None。"""
    ranked = _rank_holes(dims)
    return ranked[0] if ranked else None

def render(dims, strength=None, overview=None, best=None, worst=None, wi=None, trend=None, rx=None, tdiag=None, cash=None):
    """把復盤卡渲染成一張 Rich Panel（cyan 邊框，ANSI color，中英對齊）。
    架構：一張外框大 Panel，內部按段用 Rule(───) 分節；五維行為診斷用 bar chart 取代內部加權公式。"""
    if not _HAS_RICH:
        _no_rich_notice(); return
    trig = _rank_holes(dims)                                # #63:單一事實源,不再複製 tier 權重
    master = (_LENS or {}).get("philosophy", "交易哲學鏡片")
    parts = []

    # 〔總覽 · 金額〕
    if overview:
        o = overview; ab = o.get("ab") or {}
        ov = Text()
        ov.append("帳面總損益  ", style="bold")
        ov.append_text(_money(o['total_pnl']))
        ov.append("\n  = 已實現 ")
        ov.append_text(_money(o['realized']))
        ov.append("   未實現 ")
        ov.append_text(_money(o['unrealized']))
        cov = o.get("unrealized_coverage") or {}
        if cov.get("unpriced"):                                  # 未實現非全覆蓋 → 明講缺誰(#82:別讓省略靜默發生)
            ov.append(f"\n  ⚠ 未實現僅反映 {cov['priced_n']}/{cov['held_n']} 檔持倉,"
                      f"缺現價:{'、'.join(cov['unpriced'])}", style="dim yellow")
        ov.append("\n盈虧比 ")
        if o['payoff'] is None:                                # 無已實現虧損 → 比率無意義,不印 0/∞(#21.2 補完)
            ov.append("—", style="bold")
            ov.append("   沒有已實現虧損可比,全賺")
        else:
            ov.append(f"{o['payoff']:.1f}", style="bold")
            ov.append(f"   平均賺 ${o['avg_win']:,.0f}  vs  平均賠 ${abs(o['avg_loss']):,.0f}")
        if ab and not ab.get("note"):
            ov.append("\n贏大盤 ")
            ov.append_text(_pct(ab['excess_vs_spy'], unit="pp", bold=True))
            sp = ab.get("excess_split") or {}
            if sp:                                             # 拆帳恆等式:賽道 + 選股 = 贏大盤(永遠可出)
                ov.append("  = 押對賽道 ")
                ov.append(f"{sp['allocation']*100:+.0f}pp", style="bold")
                ov.append(" + 板塊內選股 ")
                ov.append(f"{sp['selection']*100:+.0f}pp", style="bold")
            ov.append(f"\nβ {ab['beta']:.2f}  (漲跌是大盤 {ab['beta']:.1f} 倍)")
            st = ab.get("alpha_stat") or {}
            if st:                                             # α 永遠出數,語氣看統計(#80)
                ov.append("   α ")
                ov.append(f"年化 {st['alpha_ann']*100:+.0f}%", style="bold cyan")
                if ab.get("credible"):
                    ov.append(" (≥1 年 + 統計顯著,可當能力談)", style="bold")
                else:
                    ov.append(" (區間寬,分不出本事還是運氣 → 見下方)", style="dim")
        elif ab and ab.get("note"):
            ov.append(f"\nα/β:{ab['note']}", style="dim")
        # 帳戶現金(#171):只在有現金餘額錨點(reliable)時報 weight/入金;無錨點靠 csv_sum 盲算不上卡,交 honesty 揭露
        if cash and cash.get("reliable") and cash.get("weight") is not None:
            ov.append("\n帳戶現金 ")
            ov.append(f"${cash['balance']:,.0f}", style="bold")
            ov.append(f"（佔帳戶 {cash['weight']*100:.0f}%）")
            rnd = cash.get("recent_net_deposit") or 0
            if rnd:                                            # 本期外部淨流入/出:入金判讀鉤子(該不該部署)
                ov.append(f"  本期淨{'入' if rnd > 0 else '提'}金 ")
                ov.append(f"${abs(rnd):,.0f}", style="bold")
        parts.append(Padding(ov, (0, 1)))

    # 〔做得最好 / 最差的一筆〕
    if best and worst:
        parts.append(Rule(style="dim cyan"))
        # 明標這是「已賣出 round-trip」報酬,跟下方標的層的「仍持有 cost→現價」cur_ret 區隔(#21.1)
        parts.append(Padding(Text("做得最好 / 最差的一筆  ·  已賣出 round-trip(買→賣)", style="bold"), (0, 1)))
        bw = Text()
        bw.append("✓ 最賺  ", style="bold green")
        bw.append(f"{best['ticker']:<5} ")
        bw.append_text(_pct(best['ret'], bold=True))
        bw.append(f"   {best['buy_px']:.0f} → {best['sell_px']:.0f}   抱 {best['hold']} 天")
        bw.append("\n✗ 最虧  ", style="bold red")
        bw.append(f"{worst['ticker']:<5} ")
        bw.append_text(_pct(worst['ret'], bold=True))
        bw.append(f"   {worst['buy_px']:.0f} → {worst['sell_px']:.0f}   抱 {worst['hold']} 天")
        parts.append(Padding(bw, (0, 1)))

    # 〔what if〕— 動態挑「最大集中暴險」(AI thematic / 最大 sector / 最大個股 取最高)
    # 都低於 25% 門檻(真分散)→ wi 為 None,整段省略
    if wi:
        parts.append(Rule(style="dim cyan"))
        wif = Text()
        wif.append("what if · 最大集中暴險壓測", style="bold yellow")
        wif.append(f"\n你 {wi['label']} 暴險約 ${wi['mval']:,.0f}  (佔 ")
        wif.append(f"{wi['pct']*100:.0f}%", style="bold")
        wif.append(")")
        wif.append("\n  回檔 30% (一般修正)  → 帳面 ")
        wif.append(f"-${wi['drop30']:,.0f}", style="red")
        wif.append("\n  回檔 50% (深熊)       → 帳面 ")
        wif.append(f"-${wi['drop50']:,.0f}", style="bold red")
        wif.append("   撐得住嗎?", style="italic dim")
        parts.append(Padding(wif, (0, 1)))

    # 〔標的層診斷〕
    if tdiag:
        parts.append(Rule(style="dim cyan"))
        parts.append(Padding(Text("標的層診斷  ·  按金額排序,只看影響大的", style="bold"), (0, 1)))
        tbl = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False, expand=False)
        tbl.add_column(width=6, no_wrap=True)
        tbl.add_column(justify="right", width=11, no_wrap=True)
        tbl.add_column(overflow="fold")
        for d in tdiag:
            tbl.add_row(
                Text(d['ticker'], style="bold"),
                _money(d['impact']),
                '  '.join(d['tags'])
            )
        parts.append(Padding(tbl, (0, 1)))
        # thesis_q 不印在卡上 → Step 2 對話用(SKILL L77-79「確認在出卡之前」);
        # 留在 tdiag dict 給 SKILL 取用,卡上只放用戶答完的定論(規格鐵律 issue #20)。

    # 5 維行為診斷 — 用 bar 取代「sev=0.80 ×tier1」內部加權公式
    parts.append(Rule(style="dim cyan"))
    parts.append(Padding(Text("5 維行為診斷  ·  bar 越長代表這項對你影響越大,紅色 = 已觸發", style="bold"), (0, 1)))
    dim_tbl = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False, expand=False)
    dim_tbl.add_column(width=1, no_wrap=True)            # ● ○
    dim_tbl.add_column(width=11, no_wrap=True)           # 維度名（夠塞「部位 sizing」）
    dim_tbl.add_column(width=14, no_wrap=True)           # bar
    dim_tbl.add_column(overflow="fold")                  # 描述
    for d in sorted(dims, key=lambda d: d["severity"]*HEADLINE_TIER_W[d["tier"]], reverse=True):
        triggered = d["triggered"]
        sev_w = d["severity"] * HEADLINE_TIER_W[d["tier"]]
        filled = max(0, min(14, int(round(sev_w * 14))))
        bar = "█" * filled + "░" * (14 - filled)
        if not triggered:
            flag, dot_style, bar_style = "○", "dim", "dim"
        elif sev_w >= 0.7:
            flag, dot_style, bar_style = "●", "bold red", "red"
        elif sev_w >= 0.4:
            flag, dot_style, bar_style = "●", "yellow", "yellow"
        else:
            flag, dot_style, bar_style = "●", "dim yellow", "dim yellow"
        dim_tbl.add_row(
            Text(flag, style=dot_style),
            Text(d['dim'], style="bold" if triggered else "dim"),
            Text(bar, style=bar_style),
            Text(number_line(d), style="" if triggered else "dim"),
        )
    parts.append(Padding(dim_tbl, (0, 1)))

    # 先肯定 + 復盤卡（top 1-2 最高代價的洞）
    parts.append(Rule(style="dim cyan"))
    if strength:
        intro = (_LENS or {}).get("strength_intro", "先說你做對的一件事:")
        st = Text("✓ ", style="bold green")
        st.append(intro, style="bold green")
        st.append(f"\n  {strength}")
        parts.append(Padding(st, (0, 1)))
    if trig:
        parts.append(Padding(Text("\n復盤卡  ·  top 1-2 最高代價的洞", style="bold"), (0, 1)))
        for d in trig[:2]:
            # lens quote 不當段尾結語(SKILL L192「鏡片引言別當結語」);
            # 留 card_for 給 build_card_data/SKILL 融入敘事,卡上只放數字白話。
            block = Table(show_header=False, box=None, padding=(0, 0), pad_edge=False, expand=False)
            block.add_column(width=2, no_wrap=True)
            block.add_column(overflow="fold")
            block.add_row(Text("▍", style="bold red"),
                          Text(f"最大漏洞 · {d['dim']}", style="bold red"))
            block.add_row("", Text(number_line(d)))
            parts.append(Padding(block, (0, 1)))
    else:
        parts.append(Padding(Text("這幾個地基你目前都守住了。", style="green"), (0, 1)))

    # 處方層
    if rx:
        parts.append(Rule(style="dim cyan"))
        parts.append(Padding(Text("怎麼優化  ·  放大你強的 + 外包你弱的 + 砍掉純損耗", style="bold"), (0, 1)))
        rx_tbl = Table(show_header=False, box=None, padding=(0, 0), pad_edge=False, expand=False)
        rx_tbl.add_column(width=2, no_wrap=True)
        rx_tbl.add_column(overflow="fold")
        for r in rx:
            cell = Text()
            cell.append(f"{r['kind']}:", style="bold")
            cell.append(r['text'])
            if r.get("verify"):
                cell.append(f"  〔下次驗:{r['verify']}〕", style="dim italic")
            rx_tbl.add_row(Text("▸", style="bold"), cell)
        parts.append(Padding(rx_tbl, (0, 1)))
        actionable = [r for r in rx if r.get("rule")]
        if actionable:
            n = min(len(actionable), 3)
            if n == 1:                                  # 只 1 條 → 單行(避免「從這 1 條候選挑」語意怪)
                star_hdr = Text("\n★ 下次只改這一件 ", style="bold yellow")
                star_hdr.append("(可立即執行 + 可驗)", style="dim yellow")
                parts.append(Padding(star_hdr, (0, 1)))
                parts.append(Padding(Text(actionable[0]['rule'], style="bold"), (0, 3)))
            else:                                       # 2-3 條候選讓用戶挑/改一條(#29:prescribe 已能產多條)
                star_hdr = Text("\n★ 下次只改這一件 ", style="bold yellow")
                star_hdr.append(f"(從這 {n} 條候選挑/改一條)", style="dim yellow")
                parts.append(Padding(star_hdr, (0, 1)))
                cand_tbl = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False, expand=False)
                cand_tbl.add_column(width=2, no_wrap=True)
                cand_tbl.add_column(overflow="fold")
                for i, r in enumerate(actionable[:3], 1):
                    cand_tbl.add_row(Text(f"{i}.", style="bold yellow"), Text(r['rule'], style="bold"))
                parts.append(Padding(cand_tbl, (0, 3)))

    _console.print()
    _console.print(Panel(
        Group(*parts),
        title=f"[bold]trade-recap  ·  鏡片 {master}[/]",
        title_align="left",
        border_style="cyan",
        padding=(1, 2),
        width=CARD_WIDTH,
    ))

# ─────────────────── 結構化 state(跨次對帳用)───────────────────
# ─────────────── 問題帳事件規約(#137 三層:統計層是本體,規矩/卡面都消費它)───────────────
AVGDOWN_BREACH_W = 0.25    # 與 dim_avgdown 的 breach 判準同值:攤平「當下」該檔 ≥25% 成本權重才算破線


def build_problem_events(dims, rts, avg_down, held, last_px, date_end, prev_end=None, rows=None):
    """把本次診斷規約成問題事件流(#137):被統計的是「發生過的問題」,不是規矩。

    兩型事件:
      behavior(每筆交易=一個決定):事件日=交易日。prev_end 給定(weekly 增量)→
        只取其後的新交易;不給(初診)→ 全期一次補齊,統計冷啟動就有完整履歷。
      state(倉位結構的持續選擇):每次 review 一筆、事件日=date_end——超線的倉你
        每週都在「選擇不動它」,每週一筆才是對的口徑。
    opportunities = 各 key「本期有沒有機會犯」(Opportunity Check,#137 blocker 級):
    規矩對位時,無事件+有機會才算守住;沒機會=Skipped 不累計(零事件偏差防護)。
    """
    dd = {d["dim"]: d for d in dims}
    events = []

    def _new(d):
        return prev_end is None or d.isoformat() > prev_end

    for e in avg_down or []:                               # behavior:破 size 上限的攤平(breach 才是洞)
        if e.get("weight_then", 0) > AVGDOWN_BREACH_W and _new(e["date"]):
            events.append({"key": "avgdown_breach", "kind": "behavior",
                           "week": e["date"].isoformat(), "ticker": e["ticker"], "amount": None,
                           "note": f"攤平@{e['px']:g},當下佔成本 {e['weight_then']:.0%}"})
    for r in rts or []:                                    # behavior:賣掉賺錢的、後續大漲(放掉的錢可算)
        fwd = r.get("fwd")
        if fwd is not None and fwd > SELL_EARLY_TH and r["ret"] > 0 and _new(r["exit"]):
            events.append({"key": "sell_winner_early", "kind": "behavior",
                           "week": r["exit"].isoformat(), "ticker": r["ticker"],
                           "amount": round(fwd * r["qty"] * r["sell_px"], 2),
                           "note": f"賣後續漲 {fwd:+.0%}"})
    d_size = dd.get("部位 sizing", {})
    if d_size.get("triggered"):                            # state:單注過重
        events.append({"key": "oversize", "kind": "state", "week": date_end,
                       "ticker": d_size.get("max_ticker"), "amount": None,
                       "note": f"最大單注 {d_size.get('max_pct', 0):.0%}"})
    d_div = dd.get("分散", {})
    if d_div.get("triggered"):                             # state:同 driver 集中
        events.append({"key": "concentration", "kind": "state", "week": date_end,
                       "ticker": None, "amount": None,
                       "note": f"top3 {d_div.get('top3', 0):.0%}/同賽道 {d_div.get('ai_pct', 0):.0%}"})
    d_hold = dd.get("持有時間", {})
    if d_hold.get("triggered"):                            # state:同檔多框架混用
        events.append({"key": "hold_inconsistency", "kind": "state", "week": date_end,
                       "ticker": None, "amount": None,
                       "note": "同檔又短打又長抱:" + "/".join((d_hold.get("incon_tickers") or [])[:3])})

    def _px(t):
        return (last_px or {}).get(t)

    has_loss_pos = any(_px(t) and _px(t) < c / s
                       for t, (s, c) in (held or {}).items() if s > 1e-9)
    # 「賣早」的機會要可觀測才算(review):缺 fwd(離線/缺價)的獲利賣出,犯沒犯根本看不到,
    # 標 True 會讓 check_rules 把「沒算出來」冒充「守住」——寧可 skipped 不假 held。
    has_gain = any(_px(t) and _px(t) > c / s
                   for t, (s, c) in (held or {}).items() if s > 1e-9) \
        or any(r.get("fwd") is not None and r["ret"] > 0 and _new(r["exit"]) for r in rts or [])
    has_pos = bool(held)
    has_new_exit = any(_new(r["exit"]) for r in rts or [])
    has_new_entry = any(r["side"] == "buy" and _new(r["date"]) for r in rows or [])
    opportunities = {
        "avgdown_breach": has_loss_pos,        # 有浮虧持倉才有機會攤平
        "sell_winner_early": has_gain,         # 有獲利倉(可賣)或本期有 fwd 可觀測的獲利賣出
        "oversize": has_pos, "concentration": has_pos, "hold_inconsistency": has_pos,
        # 動機類(事件由 SKILL 收尾補,但機會的「事實面」engine 就能判——缺這幾個 key,
        # 綁它們的規矩會永遠 skipped、held_streak 永不累計(review):
        "exit_anxiety": has_new_exit,          # 本期有賣出,才有機會恐慌落袋
        "fomo_entry": has_new_entry,           # 本期有新買入,才有機會追高
        # horizon_break 的機會=「有帶 horizon 的 active thesis」——engine 不讀動機庫,
        # 由 SKILL 收尾 part 5 補進 mark.opportunities。
    }
    return events, opportunities


def build_state(rows, rts, held, dims, overview, ab, rx, currency_meta=None,
                avg_down=None, last_px=None, prev_end=None, cash=None):
    """把這次復盤收斂成一張薄 JSON 狀態,給「下次對帳上次規矩」用(非給人看的卡)。
    只在 main() 偵測 TR_STATE_OUT 時呼叫並寫出;不設 → 完全不執行,引擎行為零變。
    設計依 requirements §4/§10:
      - schema_version 跟欄位語意走:半年後 skill 更新不可損毀舊狀態(§4.6)。
      - 誠實鐵律 v2(#80):α 永遠出數 + 出 t(數字+不確定性,比封殺更誠實);
        credible=統計顯著(≥1 年且 |t|≥1.96)才可用能力語氣;賽道紅利由 excess_split 拆帳現形。
      - metrics 永遠同時帶 sizing/攤平 兩個指標:對帳時要查「上次承諾那一維」的數字,
        不是查這次新 headline(否則第二張卡只是重新初診,不是復盤)。
    """
    dd = {d["dim"]: d for d in dims}
    d_size = dd.get("部位 sizing", {})
    d_avg = dd.get("加碼攤平", {})
    d_div = dd.get("分散", {})
    ab = ab if isinstance(ab, dict) else {}
    has_ab = not ab.get("note")                             # ab 帶 note = 無 pandas/價格/樣本 → 無 α/β
    credible = bool(ab.get("credible"))                     # v2(#80):統計顯著(≥1 年 + |t|≥1.96)才算,檔數閘退役
    headline = _pick_headline(dims)                         # #63:與 render/build_card_data 同一事實源
    headline_dim = headline["dim"] if headline else None
    # headline_metric:sizing→max_pos_pct、攤平→avgdown_count、其餘維→該維 severity
    HKEY = {"部位 sizing": ("max_pos_pct", d_size.get("max_pct")),
            "加碼攤平":   ("avgdown_count", d_avg.get("count")),
            "分散":       ("ai_pct", d_div.get("ai_pct"))}      # 集中押注 → 追 AI 暴險 %
    if headline_dim in HKEY:
        hk, hv = HKEY[headline_dim]
    elif headline_dim:
        hk, hv = "severity", headline.get("severity")
    else:
        hk, hv = None, None
    # rule = render() 的「下次只改這一件」= rx 第一個帶 rule 的處方。
    # 注意:rule 來自 prescribe,不必然 = headline 維(實例:headline=sizing 但 rule=攤平,
    # 因 prescribe 攤平處方排在 sizing 前、且 sizing 規矩被 not any(砍損耗) 擋掉)。
    actionable = [r for r in (rx or []) if r.get("rule")]
    rule = actionable[0]["rule"] if actionable else None
    # 樣本不足(§4.4):round-trip < 3,或交易跨度 < 60 交易日 → 行為訊號太薄,不硬出 commitment。
    # 跨度 gate 堵「≥3 round-trip 但全擠在一兩週」的假承諾(SKILL.md:80,316;#21.4);
    # 60 交易日 ≈ 84 日曆日(×7/5),用日曆跨度當 proxy,免維護市場行事曆。
    # 不綁 α 樣本(ab.n):離線/無價格時 ab.n=0,但行為維(sizing/攤平/分散)仍可承諾;
    # α 是否可信另由 alpha_credible 表示,別讓「沒價格」誤殺行為層的 commitment(codex review)。
    span_days = (rows[-1]["date"] - rows[0]["date"]).days if rows else 0
    insufficient = len(rts) < 3 or span_days < MIN_SPAN_DAYS
    # commitment = 下次要對帳的「規矩 + 它對應的可追蹤 metric」。對帳必須查這一維(用戶真承諾的),
    # 不是查 headline(否則第二張卡拿 sizing 比、用戶卻承諾攤平 = 對錯帳)。rule 關鍵字 → metric。
    commitment = None
    if rule and not insufficient:                          # §4.4:樣本不足不硬出 commitment
        RULE_METRIC = {"不加碼": ("avgdown_count", d_avg.get("count")),   # 攤平:追蹤累計加碼次數(增=退步)
                       "部位上限": ("max_pos_pct", d_size.get("max_pct"))}  # sizing:追蹤最大持倉佔比(降=進步)
        mk, mv = next(((k, v) for kw, (k, v) in RULE_METRIC.items() if kw in rule), (None, None))
        commitment = {"rule": rule, "metric_key": mk, "metric_value": mv, "goal": "down"}
    # holdings snapshot（目標3 持倉變化）：per-ticker 絕對值 + position cycle（給 thesis 綁 cycle）。
    # 只存 shares/cost/avg_cost（確定性）；per-position weight 仍不存（需即時價，跨期不穩）。
    # 帳戶級 cash_weight 改存頂層 cash 欄位（#171 PR-1：有現金錨點才可信，取代原「沒現金算不準」）。
    cyc = current_cycles(rows)                              # 雙審修：與 positions() 同邏輯（不跌負）+ cycle 序號
    holdings = {t: {"shares": round(sh, 4), "cost": round(c, 2),
                    "avg_cost": round(c / sh, 4) if sh > 1e-9 else None,
                    "cycle_start": cyc.get(t, {}).get("start"),
                    # 算不出開倉（CSV 缺期初持倉）→ 標 #unknown，不 fallback 裸 ticker（雙審 codex#4）
                    # ⚠️ 格式契約 = 頂部 CYCLE_ID_RE / CYCLE_ID_UNKNOWN_RE(#61):改這裡必先改常數,契約測試會抓
                    "cycle_id": f"{t}#{cyc[t]['start']}#{cyc[t]['seq']}" if t in cyc else f"{t}#unknown"}
                for t, (sh, c) in held.items()}
    p_events, p_opps = build_problem_events(
        dims, rts, avg_down, held, last_px,
        rows[-1]["date"].isoformat() if rows else None, prev_end, rows=rows)
    return {
        "schema_version": 2,                               # currency_meta 為 optional 附加欄,舊讀者 .get 不受影響
        "currency_meta": currency_meta,                    # #51/#129 PR-2a:聚合幣別/fx/分幣桶(單幣 USD → 大多為 None)
        "date_start": rows[0]["date"].isoformat() if rows else None,
        "date_end": rows[-1]["date"].isoformat() if rows else None,
        "n_trades": len(rows),
        "n_round_trips": len(rts),
        "n_held": len(held),
        "headline_dim": headline_dim,                      # 這次最大的洞(給「新增診斷」用)
        "headline_metric": {"key": hk, "value": hv},
        "commitment": commitment,                          # 下次對帳的錨點(規矩 + 追蹤 metric)
        "metrics": {
            "max_pos_pct": d_size.get("max_pct"),
            "max_pos_ticker": d_size.get("max_ticker"),
            "avgdown_count": d_avg.get("count"),
            "avgdown_breach": d_avg.get("breach"),
            "payoff": (overview or {}).get("payoff"),
            "ai_pct": d_div.get("ai_pct"),                  # 同一 driver(AI capex)暴險佔比
            "max_sector_pct": d_div.get("max_sector_pct"),
            "top3_pct": d_div.get("top3"),
            "n_holdings": d_div.get("n"),
            "beta": ab.get("beta"),
            "alpha_ann": ab.get("alpha_ann"),               # v2(#80):永遠出數;能力語氣由 alpha_credible 管
            "alpha_t": (ab.get("alpha_stat") or {}).get("t"),   # 不確定性一起存,對帳時才知道數字多可信
            "alpha_credible": credible if has_ab else None,
        },
        "rule": rule,
        "insufficient_data": insufficient,
        "holdings": {                                       # 目標3：持倉 snapshot（絕對值，跨期 diff 用）
            "as_of": rows[-1]["date"].isoformat() if rows else None,
            "derived_from": "trades_csv",                   # 從交易推算，可能漏期初持倉
            "is_complete": False,                           # CSV 無法自證完整（雙審 codex#3）：不宣稱完整持倉真相
            "positions": holdings,
        },
        "cash": cash,                                       # #171 PR-1:帳戶現金地基(balance/weight/source/reliable/recent_net_deposit)。None=未提供;source=csv_sum+reliable=False=無錨點靠 Σamount 近似(honesty 揭露)
        "problem_events": p_events,                         # #137 問題帳:本次規約出的事件(SKILL 收尾 append 進 problems.jsonl)
        "problem_opportunities": p_opps,                    # 各 key 本期有無機會犯(規矩對位的 Opportunity Check)
    }

# ─────────────────── 結構化 card data(給 Claude 寫敘事卡用)───────────────────
def build_honesty_ledger(overview, ab, data_integrity, currency_meta, cash=None):
    """聚合「卡面必須交代的誠實點」成一張清單(#82:機械強制取代 self-check 自律)。

    只收『觸發的』揭露項 → 空 list = 這張卡沒有誠實缺口。判定條件對齊預設人話卡的
    資料完整性 notes(main() 的 if 鏈),補上 JSON 模式(Claude 寫卡)本來缺的那套聚合——
    病根就是「該揭露什麼」在 JSON 模式被留給 Claude 自律,人話卡模式 engine 卻自己做了。
    每項 {key, status, data}:key=哪類誠實點、status=哪種缺陷、data=Claude 寫人話要的數字。
    engine 只判定『該講什麼』,文案『怎麼講』留 Claude(card-spec 說話原則);ledger 不上卡,
    是 SKILL Step 3 出卡前 gate 的對照源(每項卡面沒交代到 → 不出卡)。
    不含 market_context(無數據只是整行不出、非會誤導的缺口),不含 widget 呈現(執行層事實,engine 標不到)。
    """
    L = []
    di = data_integrity or {}
    # α 可信度:統計不顯著/樣本不足 → 卡面 α 必帶不確定性語氣,不能用能力語氣(gate 非 None 即觸發)
    if isinstance(ab, dict):
        stat = ab.get("alpha_stat") or {}
        gate = stat.get("gate")
        if gate:
            data = {"need": gate.get("need")}
            for k in ("t", "ci95", "n_days"):
                if stat.get(k) is not None:
                    data[k] = stat[k]
            L.append({"key": "alpha_credibility", "status": gate.get("reason"), "data": data})
    # 板塊歸因不全:有 driver 但查無板塊 ETF → 超額被全歸選股、押賽道功勞漏記(#92)
    if di.get("unproxied_sectors"):
        sp = (ab.get("excess_split") if isinstance(ab, dict) else None) or {}
        L.append({"key": "sector_attribution", "status": "partial",
                  "data": {"coverage": sp.get("coverage"), "unproxied": list(di["unproxied_sectors"])}})
    # 未分類 driver:分散維可能偏樂觀(假分散抓不準)
    if di.get("unclassified_drivers"):
        L.append({"key": "unclassified_drivers", "status": "present",
                  "data": {"tickers": list(di["unclassified_drivers"])}})
    # 未實現非全覆蓋:部分持倉沒抓到現價,帳面看似完整實則漏算(#82 原症)
    cov = (overview or {}).get("unrealized_coverage") or {}
    if cov.get("unpriced"):
        L.append({"key": "unrealized_coverage", "status": "partial",
                  "data": {"priced_n": cov.get("priced_n"), "held_n": cov.get("held_n"),
                           "unpriced": list(cov["unpriced"])}})
    # 賣超:賣量 > 已知買量,該檔盈虧已被忽略(對帳單沒涵蓋最早建倉)
    if di.get("orphan_sells"):
        L.append({"key": "orphan_sells", "status": "present",
                  "data": {"tickers": sorted(di["orphan_sells"])}})
    # 幣別:混幣聚合換 USD / 缺匯率近似 / 同檔多幣別衝突 → 金額幣別要標清楚
    cm = currency_meta or {}
    if cm.get("mixed") or di.get("fx_gaps") or di.get("currency_conflicts"):
        st = "conflict" if di.get("currency_conflicts") else ("fx_gap" if di.get("fx_gaps") else "mixed")
        L.append({"key": "currency_mix", "status": st,
                  "data": {"currencies": cm.get("currencies"),
                           "aggregate_currency": cm.get("aggregate_currency"),
                           "fx_gaps": di.get("fx_gaps")}})
    # 現金無錨點:cash weight 是靠交易流水盲算(假設開戶 $0),可能偏差 → 若上卡必揭露近似、邀用戶補現金餘額(#171)。
    # 只在「有得上卡的 weight 但不可信」時觸發(reliable=False + weight 非 None);weight=None(算不出,不上卡)沒有可誤導的數字,不進 ledger。
    if isinstance(cash, dict) and cash.get("weight") is not None and not cash.get("reliable"):
        L.append({"key": "cash_reliability", "status": "no_anchor",
                  "data": {"balance": cash.get("balance"), "source": cash.get("source")}})
    return L


def build_card_data(dims, strength, overview, best, worst, wi, rx, tdiag,
                    ab, pa, master, data_integrity=None, currency_meta=None, cash=None):
    """組裝 SKILL Step 3「定論卡」要用的結構化資料(JSON,非給人看的卡)。

    Claude 拿這 dict 用敘事方式寫成一段連貫卡(SKILL.md Step 3 鐵律:連貫敘事 ≠ dashboard 拼接);
    不准照搬欄位、不准印 5 維 raw、不准把 thesis_q 印在卡上(Step 2 對話用)。
    跟 build_state() 平行:build_state 給「對帳記憶」,build_card_data 給「Step 3 渲染」。

    對應 issue #20 七條規格鐵律破洞 — 把渲染責任從 engine 移到 Claude:
    - thesis_questions 包出來但標明只在 Step 2 對話用(SKILL L77-79「確認在出卡之前」)
    - top_holes 帶 lens_quote 但別當結語用(SKILL L192)
    - candidate_rules:2-3 條候選規矩(Step 3 讓用戶挑/改一條,別只給第一條;#29 已讓 prescribe 能產多條)
    - dims_raw 5 維給結構化資料,讓 Claude「一句人話帶過其餘維」(SKILL L158-159)
    """
    trig = _rank_holes(dims)                                # #63:單一事實源(同 render / build_state)

    # top 1-2 漏洞:結構化,含 lens 規矩/引言(融入敘事用,別當結語)
    top_holes = []
    for d in trig[:2]:
        rule, quote = card_for(d["dim"])
        top_holes.append({
            "dim": d["dim"],
            "severity": round(d["severity"], 2),
            "tier_weight": HEADLINE_TIER_W[d["tier"]],
            "number_line": number_line(d),                  # 數字白話(可直接用)
            "lens_rule": rule,                              # 鏡片這維的規矩
            "lens_quote": quote,                            # ⚠️ 融入敘事用,別當結語(SKILL L192)
            "raw": d,
        })

    # 候選規矩:2-3 條候選(#29 解開互斥 gate 後可多條),Step 3 跟用戶挑/改一條
    candidate_rules = [r for r in (rx or []) if r.get("rule")][:3]
    covered_dims = {r.get("dim") for r in candidate_rules if r.get("dim")}
    for h in top_holes:                    # #87/#95:出場紀律/分散/持有時間三維在 prescribe() 沒有 rule 生成路徑,candidate_rules 可能全空;用已算好的 lens_rule 補滿(headline dim 優先,因 top_holes 已按 severity 排序)
        if len(candidate_rules) >= 3:
            break
        if h["dim"] in covered_dims or not h.get("lens_rule"):
            continue
        candidate_rules.append({"kind": h["dim"], "dim": h["dim"], "rule": h["lens_rule"], "text": h.get("lens_quote", "")})
        covered_dims.add(h["dim"])

    # ⚠️ thesis_questions 給 Step 2 對話用,絕不准印在卡上(SKILL L77-79「確認在出卡之前」)
    thesis_questions = [{"ticker": d["ticker"], "question": d["thesis_q"]}
                        for d in (tdiag or []) if d.get("thesis_q")]

    return {
        "schema_version": 1,
        "philosophy": master,
        "strength": strength,
        "overview": overview,
        "best_trade": {**best, "pnl": best["qty"] * (best["sell_px"] - best["buy_px"])} if best else None,   # 補 $ 損益,卡上 %和$ 都要
        "worst_trade": {**worst, "pnl": worst["qty"] * (worst["sell_px"] - worst["buy_px"])} if worst else None,
        "what_if": wi,
        "ticker_diagnosis": tdiag,                          # tags 已是人話
        "thesis_questions": thesis_questions,               # ⚠️ Step 2 對話用,不准印卡上
        "top_holes": top_holes,                             # top 1-2,Claude 寫敘事用
        "candidate_rules": candidate_rules,                 # 2-3 條候選,讓用戶挑/改一條
        "prescriptions": rx,                                # 完整處方層
        "alpha_beta_breakdown": ab,
        "payoff_attribution": pa,
        "dims_raw": dims,                                   # 5 維 raw,Claude 用「一句人話」帶過其餘維
        "data_integrity": data_integrity or {},             # 賣超/未分類 driver — 影響數據可信度,Claude 該主動提
        "currency_meta": currency_meta,                     # #51/#129 PR-2a:聚合幣別/fx/分幣桶;None=單幣 USD 舊行為
        "cash": cash,                                        # #171 PR-1:帳戶現金(balance/weight/source/reliable/recent_net_deposit);reliable 才上 weight/入金判讀,無錨點靠 honesty 揭露
        "honesty_ledger": build_honesty_ledger(overview, ab, data_integrity, currency_meta, cash),  # #82:卡面必講的誠實點清單(空=無缺口);出卡前 gate 對照源
    }

# ─────────────────────────── main ───────────────────────────
def main():
    paths = sys.argv[1:] or [DEFAULT_CSV]
    rows = load(paths)
    if not rows:                                          # 空 / 全過濾 CSV → 別在下游 rows[0] crash(#41 F),給人話
        note = _load_skip_note()                           # #50:全滅時把丟棄計數也印出,別讓用戶對著 0 筆猜原因
        hint = ("——大量列因 RecordType≠'Trade' 被跳過,請確認 Step 0 標準化有把成交列填成 RecordType=Trade"
                if _LOAD_STATS.get("skip_non_trade") else "")
        print(f"沒有可解析的交易{note}:CSV 為空,或沒有任何 BUY/SELL 的 Trade 列{hint}。"
              "請確認欄位 Symbol / Action / Quantity / Price / TradeDate 都在。", file=sys.stderr)
        sys.exit(1)
    master = load_lens()                                  # 顯示用哲學名(去名,可換大師/哲學檔)
    dm = os.environ.get("TR_DRIVER_MAP")                  # Claude 生成的 driver map(冷門股分類)
    n_dm = load_driver_map(dm) if dm else 0
    n_adj = adjust_for_splits(rows, fetch_splits({r["ticker"] for r in rows}))  # 分割調整,對齊今日價
    rts, open_lots = round_trips(rows)
    _, avg_down = positions(rows)      # avgdown 偵測留 avg cost(行為語意:買價 vs 平均持倉成本)
    held = fifo_held(open_lots)        # #162:未實現改 FIFO 剩餘,與 realized 同基礎,加總=真值
    tickers = {r["ticker"] for r in rts} | set(held.keys())
    start = (min((r["entry"] for r in rts), default=rows[0]["date"]) - dt.timedelta(days=10)).isoformat()
    t_market = {r["ticker"]: r.get("market", "US") for r in rows}   # ticker→market(per-market 基準/拆帳用)
    bench = {p for p in (_sector_proxy(t, t_market.get(t, "US")) for t in tickers) if p}   # 拆帳要的板塊 ETF(押賽道 vs 選股)
    bench |= {MARKET_BENCH.get(m, "SPY") for m in set(t_market.values())}   # 各市場主基準(TW→^TWII)一起抓
    px, yf_err = fetch_prices(tickers | bench, start)
    n_fwd = adaptive_n_fwd(rows)                           # 觀察窗隨資料長度自適應
    fwds, last_px = fwd_from_px(rts, px, n_fwd)
    last_px = last_px or {}                                # 離線/無價格 → {} 而非 None,讓下游(ticker_diagnosis 等)不 crash
    decision_rts = [r for r in rts if driver(r["ticker"])[0] not in BENCH_SELF]   # 再平衡/現金管理,非選股決策(=配置類,同 BENCH_SELF)
    # 多市場幣別(#51/#129 PR-2a):跨 ticker 聚合必須在共同幣別(USD)上做,否則台股 985 元 + 美股 985 美元
    # 直接相加 = 靜默算錯。單一幣別組合(含純台股)聚合自洽 → 不抓匯率、路徑零變化。
    cur_map, currencies, cur_conflicts = currency_map(rows)
    mixed_ccy = len(currencies) > 1
    fx, fx_err = fetch_fx(currencies) if mixed_ccy else ({"USD": 1.0}, None)
    if mixed_ccy:
        rts_u, held_u, lastpx_u = usd_view(rts, held, last_px, cur_map, fx)
        decision_rts_u = [r for r in rts_u if driver(r["ticker"])[0] not in BENCH_SELF]
    else:
        rts_u, held_u, lastpx_u, decision_rts_u = rts, held, last_px, decision_rts
    # 殘倉過濾(#172):市值<0.1% 的部位不進分散度/what-if/per-ticker 診斷/未分類計數;
    # overview(P&L)/dim_size(單筆過重本就只看大倉)/n_held(對帳全量)不動 → 不藏虧損、對帳一致。
    keep_dx = meaningful_tickers(held_u, lastpx_u)
    held_dx = {t: v for t, v in held_u.items() if t in keep_dx}
    d_size = dim_size(rows, held_u, lastpx_u)
    d_exit = dim_exit(decision_rts, fwds, n_fwd); d_div = dim_diversify(held_dx, lastpx_u)
    d_hold = dim_hold(rts); d_avgdown = dim_avgdown(avg_down, held_u, lastpx_u, d_size)
    dims = [d_exit, d_size, d_div, d_hold, d_avgdown]
    strength = dim_strength(d_exit, d_size, d_avgdown, d_div, d_hold, decision_rts)  # 先給做對的(附案例)
    # per-market α/β(#129 PR-2b):混市場時頂層代表值 = 資金佔比最大市場;佔比在 USD 視圖上算(跨市場比較)
    mkt_weights = defaultdict(float)
    for t, (sh, c) in held_u.items():
        pxv = lastpx_u.get(t)
        mkt_weights[t_market.get(t, "US")] += sh * pxv if pxv else c
    ab = dim_alpha_beta(rows, px, market_weights=dict(mkt_weights))
    if isinstance(ab, dict): ab["credible"] = alpha_credible(ab)   # α 能力語氣閘 v2(#80):統計顯著才算,檔數閘退役(混市場=scope 市場的顯著性)
    overview = overview_stats(decision_rts_u, ab, held_u, lastpx_u)   # 已實現 + 未實現都報(聚合幣別上)
    pa = payoff_attribution(decision_rts_u)                # 盈虧比拆解:重點交易的貢獻度(聚合幣別上)
    best, worst = best_worst(decision_rts)                 # 做得最好/最差的一筆(ret%,無因次 → 原幣)
    wi = what_if(held_dx, lastpx_u)                        # 可量化的 what-if(聚合幣別上,#172 殘倉不計)
    trend = time_trend(decision_rts, avg_down)             # (engine 保留,卡片暫不顯示)
    rx = prescribe(ab, dims, overview)                     # 處方層:揚長/外包/砍損耗
    adds_class = classify_adds(rows)                       # 主從分類:疑似定投 vs 凹單 vs 待確認
    # 標的層:按金額排序,對事不對人。排序/佔比是跨 ticker 比較 → 混幣必須在聚合幣別(USD 視圖)上做,
    # 否則 TWD 名目大數霸榜(review 2026-07-06);比率欄(cur_ret/fwd)無因次不受縮放影響。
    tdiag = ticker_diagnosis(rts_u, adds_class, held_dx, lastpx_u)   # #172 殘倉不列 per-ticker 診斷

    # 資料完整性(賣超 / 未分類 driver)— 影響數據可信度,JSON 與人話卡共用同一份
    orphans = orphan_sells(rows)
    # 未分類 driver 計數排除殘倉(#172):核能小倉 LEU 之類 <0.1% 的未分類尾倉不該冒充「連歸類都做不到」的誠實缺口
    unclassified = sorted(t for t in held if t in keep_dx and driver(t)[0] == "未分類")
    data_integrity = {
        "orphan_sells": {t: round(q, 2) for t, q in sorted(orphans.items())},
        "unclassified_drivers": unclassified,
    }
    fx_gaps = [c for c in currencies if c not in fx] if mixed_ccy else []
    if fx_gaps:
        data_integrity["fx_gaps"] = fx_gaps                # 混幣但缺匯率:聚合按原幣近似(因子=1),卡面必須明示
    if cur_conflicts:
        data_integrity["currency_conflicts"] = cur_conflicts   # 同一檔多幣別 = 輸入資料錯,取最後一筆
    # #92:有 driver 標籤但 SECTOR_BENCH 查無板塊 ETF 對照 → 該檔超額被全歸「選股」、賽道效應漏記。
    # 原本只在 α 面板(需 SPY + ≥60 交易日對齊 + coverage<0.995 那行 if)才揭露 → 併入永遠顯示的
    # data_integrity,與「未分類 driver」同語意(板塊歸因不可靠)的第二個揭露缺口,不再只靠自律。
    _sp = ab.get("excess_split") if isinstance(ab, dict) else None
    _unproxied = (_sp or {}).get("unproxied") or []
    if _unproxied:
        data_integrity["unproxied_sectors"] = list(_unproxied)
    currency_meta = {
        "currencies": currencies,
        "mixed": mixed_ccy,
        # 聚合數字(overview/payoff/what-if/sizing 權重)的幣別:混幣 → USD;單幣 → 該原幣。
        # display currency(語言→幣別)換算是 SKILL 呈現層的責任,engine 只提供 fx。
        "aggregate_currency": "USD" if mixed_ccy else (currencies[0] if currencies else "USD"),
        "fx": ({c: r for c, r in fx.items() if c != "USD"} or None) if mixed_ccy else None,  # {cur: 兌 USD}
        "fx_error": fx_err,
        # ⚠️ 分桶刻意吃「原幣」物件(decision_rts/held,非 _u 版):桶的意義就是原幣會計事實,換成 _u = 全桶變 USD 廢掉
        "pnl_by_currency": pnl_by_currency(decision_rts, held, last_px, cur_map) if mixed_ccy else None,
        "alpha_beta_note": (
            f"α/β 已 per-market 分算:頂層數字僅含 {ab.get('scope')} 部位(對 {ab.get('bench')}),"
            f"其他市場見 by_market——不合成總 α"
            if isinstance(ab, dict) and ab.get("scope")
            else ("多幣別組合(單一市場)的 α/β 按該市場大盤計" if mixed_ccy else None)),
    }

    # 帳戶現金地基（#171 PR-1）：現金流 + 現金餘額錨點 → cash_position。
    # held_mv = 持倉市值（聚合幣別，無現價用成本近似，同 dim_diversify）= cash_weight 分母。
    import json
    cash_flows = load_cash_flows(paths)
    if mixed_ccy:                                        # 混幣：現金流各幣別 → USD（對齊聚合視圖）
        cash_flows = [dict(cf, amount=cf["amount"] * fx.get(cf["currency"], 1.0)) for cf in cash_flows]
    held_mv = sum((sh * lastpx_u[t]) if lastpx_u.get(t) else c for t, (sh, c) in held_u.items())
    _ca = os.environ.get("TR_CASH")                     # SKILL Step 0 抓對帳單現金餘額 → JSON {as_of, amount, currency}
    try:
        cash_anchor = json.loads(_ca) if _ca else None
    except (ValueError, TypeError):
        cash_anchor = None
    cash_data = cash_position(cash_flows, held_mv, anchor=cash_anchor,
                              prev_end=os.environ.get("TR_PREV_END") or None)

    dm_skip = f"({_DM_SKIPPED} 筆格式錯跳過)" if _DM_SKIPPED else ""
    split_note = f"｜分割調整: {n_adj} 筆" if n_adj else ""
    # JSON 模式(SKILL Step 3 走這條):stdout 純 JSON 給 Claude 寫敘事卡;meta 走 stderr 不污染
    if os.environ.get("TR_JSON"):
        import json
        meta = (f"# 載入 {len(rows)} 筆交易{_load_skip_note()}（{rows[0]['date']} ~ {rows[-1]['date']}），"
                f"{len(rts)} round-trip,持倉 {len(held)}｜yfinance: {'OK' if not yf_err else yf_err}"
                f"｜鏡片: {master or 'fallback'}｜driver map: {n_dm} 檔{dm_skip}{split_note}"
                + (" (純 fallback,冷門股可能失準)" if not n_dm else ""))
        print(meta, file=sys.stderr)
        card = build_card_data(dims, strength, overview, best, worst, wi, rx, tdiag,
                               ab, pa, master, data_integrity=data_integrity,
                               currency_meta=currency_meta, cash=cash_data)
        print(json.dumps(card, ensure_ascii=False, indent=2, default=str))
    else:
        # 預設:乾淨人話卡(quickstart / fallback 用,#20 違規條目已砍)
        print(f"# 載入 {len(rows)} 筆交易{_load_skip_note()}（{rows[0]['date']} ~ {rows[-1]['date']}），"
              f"{len(rts)} 個 round-trip，當前持倉 {len(held)} 檔。", end="")
        print(f" yfinance: {'OK' if not yf_err else yf_err}｜鏡片: {master or 'fallback'}"
              f"｜driver map: {n_dm} 檔{dm_skip}{split_note}" + (" (純 fallback,冷門股可能失準)" if not n_dm else ""))
        print(f"# 出場紀律只看「決策賣出」：{len(decision_rts)}/{len(rts)} round-trip"
              f"（排除 {len(rts)-len(decision_rts)} 筆大盤/債/商品 ETF 再平衡）")
        render(dims, strength, overview, best, worst, wi, trend, rx, tdiag, cash=cash_data)
        print_alpha_beta(ab)
        print_payoff_attr(pa)                             # 盈虧比拆解(誰在撐/拖,反事實)
        d_entry = dim_entry_style(rows, px)               # 【風格】維雛形(不進洞排序,先驗訊號)
        print("\n" + "─"*60)
        print("  〔風格雛形 · 進場相對位置(對事不對人,只報方向;閥未接)〕")
        if d_entry.get("note"):
            print(f"    {d_entry['note']}——這維要 yfinance 日線才算得出")
        else:
            zh = {"strength": "偏追高/順勢——買在區間高位(動能派視為策略、價值派視為追高)",
                  "weakness": "偏抄底/逆勢——買在區間低位(價值派視為紀律、動能派視為接刀)",
                  None: "無明顯方向(中性)"}
            conf = "樣本足" if not d_entry["low_conf"] else f"低信賴:可定位買入僅 {d_entry['n']} 筆(<{MIN_ENTRY_BUYS})"
            print(f"    {zh[d_entry['lean']]}")
            print(f"    進場區間位置中位 {d_entry['median_pct']*100:.0f}%（{d_entry['n']} 筆 · {conf}）"
                  f" lean={d_entry['lean'] or '—'}")
        notes = []                                        # 資料完整性提示統一收在最後(orphans/unclassified 已於上方算好)
        if mixed_ccy:                                     # #51:混幣時聚合金額已換 USD,人話卡也要標清楚幣別
            fxs = "、".join(f"1 {c}≈{r} USD" for c, r in sorted(fx.items()) if c != "USD") or "無可用匯率"
            gap_note = f";{','.join(fx_gaps)} 缺匯率,以原幣近似" if fx_gaps else ""
            notes.append(f"多幣別組合({'+'.join(currencies)}):總覽/權重類金額已換算為 USD({fxs}){gap_note};"
                         f"單檔價格仍為原幣")
        if orphans:
            names = ", ".join(f"{t}({q:.0f} 股)" for t, q in sorted(orphans.items()))
            notes.append(f"{len(orphans)} 檔『賣超』(賣量 > 已知買量):{names}"
                         f"——多半是對帳單沒涵蓋最早建倉,已從盈虧忽略(做空支援另議)")
        if unclassified:
            more = f" 等 {len(unclassified)} 檔" if len(unclassified) > 8 else ""
            notes.append(f"{', '.join(unclassified[:8])}{more} 未分類 driver——分散維可能偏樂觀,"
                         f"可補 driver map(見 SKILL Step 0.5)讓假分散抓得準")
        if data_integrity.get("unproxied_sectors"):        # #92:板塊歸因不可靠的第二種(有標籤但查無 ETF)
            up = data_integrity["unproxied_sectors"]
            more = f" 等 {len(up)} 檔" if len(up) > 8 else ""
            notes.append(f"{', '.join(up[:8])}{more} 有 driver 標籤但無板塊 ETF 對照——"
                         f"賽道/選股拆帳把這些歸入『選股』,押對賽道的功勞可能被誤記成選股能力")
        if notes:
            print("\n" + "─"*60)
            print("  ⓘ 資料完整性(不影響上面的洞,只是提醒數字有多硬):")
            for nt in notes:
                print(f"    · {nt}")
    if os.environ.get("TR_STATE_OUT"):                    # 設了才寫薄 state;不設 → 卡片 stdout 零變
        import json, tempfile
        path = os.environ["TR_STATE_OUT"]
        state = build_state(rows, rts, held, dims, overview, ab, rx,
                            currency_meta=currency_meta,
                            avg_down=avg_down, last_px=last_px,
                            prev_end=os.environ.get("TR_PREV_END") or None,
                            cash=cash_data)
        # TR_PREV_END=上次 review 的 date_end(SKILL 對帳模式傳入)→ behavior 型問題事件
        # 只取其後的新交易(weekly 增量);不設 = 初診全期補齊,問題帳統計冷啟動。
        outdir = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=outdir, suffix=".tmp")  # 原子寫:tmp→replace,不留半寫髒狀態(§4.6)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        print(f"# [state] {path}", file=sys.stderr)        # 訊息走 stderr,不污染卡片

if __name__ == "__main__":
    main()
