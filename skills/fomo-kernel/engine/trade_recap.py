#!/usr/bin/env python3
"""
fomo-kernel · trade-recap engine v0.2
實作 5 維行為診斷算法 → 一張 VY 鏡片復盤卡的「機械層」(抓大放小)。
純函式：trades CSV → 5 維 metrics → 卡片(選 top 1-2)。動機那層由 SKILL.md 的對話流程補。

用法：python3 trade_recap.py [trades.csv ...]   (預設吃 ../mock/mock_trades.csv)
隱私：本檔不含任何真實帳戶路徑;預設只跑 mock 資料。用戶自己的 CSV 由參數傳入,留在本機。
"""
import csv, os, re, sys, statistics, datetime as dt
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import instruments as instrument_policy
import market_context as market_context_engine
import price_feed

DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "..", "mock", "mock_trades.csv")

N_FWD = 30          # 賣出後 N 交易日看續漲（tunable）
MIN_SPAN_DAYS = 84  # 樣本不足 gate:60 交易日 ≈ 84 日曆日(×7/5);交易跨度短於此 → insufficient(§4.4, #21.4)
MIN_ROUND_TRIPS = 3  # 完整買賣回合 < 此 → 已實現行為太薄;review._review_tier 的 behavioral 門檻同源(#306)
CURVE_MAX_POINTS = 52  # pnl_curve 逐日序列長於此 → 降成週頻(W-FRI),卡片 sparkline 不需要逐日精度

# cycle_id 契約(單一事實源,#61):SKILL 對帳、theses.jsonl 綁定、測試斷言都以這兩條為準。
# 正常 = 3 段「ticker#開倉日#序號」;CSV 缺期初持倉算不出開倉 → 2 段「ticker#unknown」。
# 改格式 = schema 變更:必須同步 SKILL.md(開場路由/收尾 theses 註解)並讓 tests/test_tr_json_contract.py 紅燈把關。
CYCLE_ID_RE = re.compile(r"^[^#\s]+#\d{4}-\d{2}-\d{2}#\d+$")
CYCLE_ID_UNKNOWN_RE = re.compile(r"^[^#\s]+#unknown$")
SELL_EARLY_TH = 0.10
SECTOR_MAX_TH = 0.40       # #87/#95:跟 dim_diversify() severity 的 40% 起算點對齊,triggered/severity 不再各吹各的號
RF_ANNUAL = 0.043   # 無風險利率(年)：美國短期國庫券約 4.3%，Jensen's Alpha 用（tunable）
RESIDUAL_POS_TH = 0.001    # 殘倉閾值:市值佔全持倉 <0.1% = 噪音(股息零頭/1 股尾倉),不計入分散度/what-if/per-ticker 診斷/未分類計數(#172,owner 2026-07-12 拍板;相對佔比自適應帳戶規模,非絕對股數/金額)
# ── 單一部位 sizing 閾值(#324:四處硬編對齊成單一事實源)────────────────────────
# 對齊前三處口徑各吹各的號:診斷 25% / 處方 30% / severity 起算 20% → 25–30% 的倉被標成
# 洞卻拿不到規矩(診斷與處方脫節)。對齊後:診斷=處方=同一條觸發線 OVERSIZE_TRIGGER,severity
# 亦從該線起算;規矩「建議壓到」的目標 POSITION_CAP 可比觸發線更嚴(那是教練建議,不是觸發門檻)。
# 通用基準,不依用戶歷史分佈個人化——那會把壞習慣正常化(owner 2026-07-22);個人化只來自用戶
# 明確覆寫(profile.json → state.max_position_pct),覆寫時觸發線與規矩目標一起改用其值。
OVERSIZE_TRIGGER = 0.25    # 超過即把 sizing 標成洞 + 開 cut_oversize 處方(診斷=處方同一條線)
OVERSIZE_SEV_SPAN = 0.25   # severity 從觸發線線性升到 1.0 的跨度(觸發線 + span = 滿格;預設 25%→50%)
POSITION_CAP = 0.20        # cut_oversize 規矩建議壓到的上限(教練目標);renderer 端有 stdlib 副本,test_card_html 鎖同步


def valid_position_cap(value):
    """用戶自訂單一部位上限 → (0,1) 的 float,否則 None(fail-closed)。
    拒收 <=0、>=1、NaN、inf、非數字:壞覆寫值靜默退回通用預設,不讓它污染診斷/處方/規矩。
    連鎖比較天然擋 NaN/inf(0 < nan 為 False),不必額外引入 math。"""
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    return pct if 0 < pct < 1 else None


def effective_oversize_trigger(override=None):
    """診斷/處方的觸發線:用戶自訂上限(合法時)否則通用預設 OVERSIZE_TRIGGER。"""
    return valid_position_cap(override) or OVERSIZE_TRIGGER


def effective_position_cap(override=None):
    """規矩文案「建議上限」帶的數字:用戶自訂上限(合法時)否則通用預設 POSITION_CAP。"""
    return valid_position_cap(override) or POSITION_CAP

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


def _cash_balance_one_ccy(flows, anchor, prev_end):
    """單一幣別的現金餘額（原幣）。回 (balance, source, reliable)。
    有錨點 → 錨點餘額 + 其後現金流（對付不完整 CSV，reliable）；
    無 → Σ全部（假設開戶 0，CSV 漏一筆 deposit 就偏，csv_sum/unreliable）。"""
    if anchor and anchor.get("as_of") and anchor.get("amount") is not None:
        a_date = anchor["as_of"]
        if isinstance(a_date, str):
            a_date = dt.date.fromisoformat(a_date)
        bal = float(anchor["amount"]) + sum(cf["amount"] for cf in flows if cf["date"] > a_date)
        return bal, "anchored", True
    return sum(cf["amount"] for cf in flows), "csv_sum", False


def cash_position(cash_flows, held_mv, anchor=None, prev_end=None, fx=None):
    """帳戶現金地基（#171 PR-1；多幣別現金桶）。
    per-currency 各算餘額（錨點+其後現金流 or csv_sum），用 fx 聚合成 USD total——
    台美各帳戶現金各自錨點（一個 TR_CASH 只表一種幣的舊限制在此解除）。
    - anchor：單 dict（向後相容，無 currency 欄 → 對應唯一幣別/單桶）或 list[dict]（per-currency，各帶 currency）。
    - fx：{ccy: usd_per_unit} 多幣別聚合用；None/單幣 → 因子 1.0（原幣即聚合幣）。
    - cash_balance：聚合 USD；cash_weight = 現金 /（持倉市值 + 現金），分母 ≤0 → None。
    - recent_net_deposit：本期（prev_end 後）外部淨流入，per-currency 換 USD 加總。
    - reliable：所有有現金流的幣別都有錨點=True（source=anchored）；部分=partial；全無=csv_sum。
    - by_currency：{ccy: {balance(原幣), source, reliable}} per-currency 明細（呈現層可展開、可只揭露缺錨點的幣別）。"""
    if isinstance(prev_end, str):
        prev_end = dt.date.fromisoformat(prev_end) if prev_end else None
    fx = fx or {}
    anchors = [anchor] if isinstance(anchor, dict) else list(anchor or [])
    currencies = sorted({cf.get("currency", "USD") for cf in cash_flows}) or ["USD"]

    def _anchor_for(c):
        # 帶 currency 的錨點按幣別配對；無 currency 的錨點只在單一幣別時對應（向後相容舊單桶格式）。
        for a in anchors:
            ac = (a.get("currency") or "").strip().upper()
            if ac == c or (not ac and len(currencies) == 1):
                return a
        return None

    by_ccy, total, recent = {}, 0.0, 0.0
    all_reliable, any_reliable = True, False
    for c in currencies:
        flows_c = [cf for cf in cash_flows if cf.get("currency", "USD") == c]
        bal_c, src_c, rel_c = _cash_balance_one_ccy(flows_c, _anchor_for(c), prev_end)
        f = fx.get(c, 1.0)
        by_ccy[c] = dict(balance=round(bal_c, 2), source=src_c, reliable=rel_c)
        total += bal_c * f
        recent += f * sum(cf["amount"] for cf in flows_c
                          if cf["kind"] in ("deposit", "withdrawal")
                          and (prev_end is None or cf["date"] > prev_end))
        all_reliable = all_reliable and rel_c
        any_reliable = any_reliable or rel_c
    source = "anchored" if all_reliable else ("partial" if any_reliable else "csv_sum")
    denom = held_mv + total
    # 不完全可信的負現金 = csv_sum 假設破裂（入金沒記全），weight 垃圾 → None；denom≤0 亦 None。
    # 全可信（全錨點）的負現金 = 真融資 margin，weight 負有意義，照報。
    if denom <= 1e-9 or (not all_reliable and total < 0):
        weight = None
    else:
        weight = total / denom
    return dict(balance=round(total, 2), weight=weight, source=source,
                reliable=all_reliable, recent_net_deposit=round(recent, 2),
                by_currency=by_ccy)


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


def current_cycle_add_cursors(rows):
    """Return an engine-owned decision cursor for each open position cycle.

    The cursor advances only when that same cycle receives another buy. It does
    not depend on the portfolio-wide averaging-down count, so an add in one
    ticker cannot cause another ticker's thesis question to reappear.
    """
    shares = defaultdict(float)
    seq = defaultdict(int)
    start = {}
    add_count = defaultdict(int)
    for row in rows:
        ticker = row["ticker"]
        if row["side"] == "buy":
            if shares[ticker] <= 1e-6:
                seq[ticker] += 1
                start[ticker] = row["date"].isoformat()
                add_count[ticker] = 0
            else:
                add_count[ticker] += 1
            shares[ticker] += row["qty"]
        elif row["side"] == "sell" and shares[ticker] > 1e-6:
            shares[ticker] = max(0.0, shares[ticker] - row["qty"])
            if shares[ticker] <= 1e-6:
                start.pop(ticker, None)
                add_count.pop(ticker, None)
    out = {}
    for ticker in sorted(start):
        cycle_id = f"{ticker}#{start[ticker]}#{seq[ticker]}"
        count = add_count[ticker]
        out[ticker] = {
            "cycle_id": cycle_id,
            "add_count": count,
            "decision_cursor": f"{cycle_id}#add#{count}" if count else None,
        }
    return out

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


def fetch_fx(currencies, feed=None):
    """非 USD 幣別 → yfinance '{CUR}=X'(1 USD 兌多少 CUR)→ {cur: usd_per_unit};USD 恆 1.0。
    離線/失敗 → 缺誰記誰,不 crash(呼叫端把缺口寫進 data_integrity,聚合以 1.0 近似並明示)。
    feed(#289 agent-supplied envelope)在場 → 只讀 feed,不碰網路:餵 feed 的前提就是
    這台機器抓不到,再試一次只是把 prepare 卡在 DNS timeout 上。"""
    fx = {"USD": 1.0}
    todo = sorted(c for c in set(currencies) if c and c != "USD")
    if not todo:
        return fx, None
    if feed is not None:
        fx.update({c: r for c, r in price_feed.fx_rates(feed).items() if c in todo})
        missing = [c for c in todo if c not in fx]
        return fx, (f"匯率無資料(供給的價格檔未含): {','.join(missing)}" if missing else None)
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


def fx_request_currencies(currencies, requested_display=None):
    """Include the renderer's target only for a genuinely mixed portfolio."""
    held = {str(c).strip().upper() for c in currencies if str(c).strip()}
    requested = str(requested_display or "").strip().upper()
    if len(held) > 1 and requested:
        held.add(requested)
    return sorted(held)


def fetch_fx_series(currencies, start, feed=None):
    """非 USD 幣別的每日匯率序列('{CUR}=X' → usd_per_unit DataFrame),帳戶級估值用
    (#171 拍板默認:混幣 V_t 用每日 fx、含匯率損益)。離線/缺 →(None, err),
    呼叫端退回即期 fx 常數近似(perf.basis.fx_approx 會標記、honesty 揭露)。
    feed 在場 → 不碰網路:envelope 只帶即期匯率,序列誠實缺席走既有近似路徑。"""
    todo = sorted({c for c in currencies if c != "USD"})
    if not todo:
        return None, None
    if feed is not None:
        return None, "fx 序列缺(供給的價格檔只帶即期匯率),帳戶級估值退回即期近似"
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance 未安裝(fx 序列缺,帳戶級估值退回即期近似)"
    try:
        data = yf.download([f"{c}=X" for c in todo], start=start,
                           progress=False, auto_adjust=True)["Close"]
        if data is None or data.empty:
            return None, "fx 序列無資料"
        if data.ndim == 1:
            data = data.to_frame(name=f"{todo[0]}=X")
        cols = {}
        for c in todo:
            col = f"{c}=X"
            if col in data.columns:
                s = data[col].dropna()
                if len(s):
                    cols[c] = 1.0 / s                 # '{CUR}=X' 報 1 USD 兌 CUR → 反轉成 CUR→USD
        if not cols:
            return None, "fx 序列全缺"
        import pandas as pd
        return pd.DataFrame(cols), None
    except Exception as e:
        return None, f"fx 序列下載失敗: {e}"


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
def fetch_prices(tickers, start, feed=None):
    """價格框(index=日期, columns=ticker)。失敗一律 (None, 人話原因),絕不 crash。

    feed(#289):沙箱 host 抓不到價時,agent 從公認資料源查回來的 envelope 走這條——
    只讀 envelope、完全不碰網路,單日收盤就足以還原損益,帶日線 history 才解鎖
    基準/β/帳戶柱。envelope 沒涵蓋的 ticker 就是缺價,誠實留白不補洞。"""
    if feed is not None:
        universe = sorted(set(tickers) | set(market_context_engine.SYMBOLS))
        frame, err = price_feed.to_frame(feed, universe)
        return frame, err
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance 未安裝"
    try:
        data = yf.download(sorted(set(tickers) | {"SPY", "QQQ", "SOXX", "^VIX"}), start=start,
                           progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        return None, f"yfinance 下載失敗: {e}"
    if data is None or data.empty:
        return None, "yfinance 無資料"
    if data.ndim == 1:
        data = data.to_frame()
    return data, None


def review_window(date_end, previous_end=None):
    """Freeze the market-context window from engine-owned review dates."""
    end = dt.date.fromisoformat(str(date_end))
    if previous_end:
        try:
            previous = dt.date.fromisoformat(str(previous_end))
            if previous <= end:
                return previous.isoformat(), end.isoformat()
        except (TypeError, ValueError):
            pass
    return (end - dt.timedelta(days=7)).isoformat(), end.isoformat()


def _resolve_prev_end(date_end, prev_end, prev_prev_end):
    """Self-exclusion for a same-week rerun (#270).

    review.py sets TR_PREV_END from last_state.json's date_end *before*
    invoking this engine — before this run's own date_end is knowable, since
    that requires parsing the CSV. Re-running the identical CSV for the same
    week therefore makes TR_PREV_END alias THIS run's own date_end (the prior
    finalize already advanced the anchor to it). Feeding that back in collapses
    every "_new(d)" boundary (build_problem_events) to nothing, which flips
    opportunity flags such as exit_anxiety/fomo_entry between runs of the same
    week and trips the #166 fail-closed mark guard.

    When the candidate would alias this run's own date_end, fall back one rung
    to prev_prev_end — the prev_end that last_state itself was built from,
    i.e. the closest genuinely-earlier review boundary — instead of silently
    treating every trade in the week as "new". Once a review commits with the
    correct prev_end, this stays a stable fixed point: last_state.prev_end
    keeps resolving to the same genuinely-earlier boundary no matter how many
    times the same week is reviewed again."""
    if prev_end and prev_end == date_end:
        return prev_prev_end or None
    return prev_end or None


def shared_price_start(trade_start, context_start, context_end):
    """Widen the shared fetch enough for both trade analytics and context anchors."""
    trade_anchor = dt.date.fromisoformat(str(trade_start))
    context_anchor = dt.date.fromisoformat(str(context_start))
    end = dt.date.fromisoformat(str(context_end))
    context_fetch = min(
        context_anchor - dt.timedelta(days=market_context_engine.FETCH_PAD_DAYS),
        dt.date(end.year - 1, 12, 15),
    )
    return min(trade_anchor, context_fetch).isoformat()


def market_context_from_prices(data, error, start, end):
    """Adapt the shared price frame into market_context's dependency-free contract."""
    prices = {}
    if data is not None:
        for symbol in market_context_engine.SYMBOLS:
            if symbol not in data.columns:
                continue
            series = [(idx.date().isoformat(), float(value))
                      for idx, value in data[symbol].items() if value == value]
            if series:
                prices[symbol] = series
    return market_context_engine.build_output(prices or None, error, start, end)

def fetch_splits(tickers, feed=None):
    """抓每檔的分割事件 {ticker: [(date, ratio), ...]}。抓不到/離線 → 回 {}(不調整,降級)。
    feed(#289)在場 → 只認 envelope 宣告的分割事件,不碰網路;沒宣告 = 不調整(降級同離線)。
    yfinance 沒有批次 splits 端點,只能逐檔;序列逐檔是 prepare 網路時間裡唯一隨持倉數
    線性增長的段(#235 實測 8 執行緒 4.2×),故執行緒並行。結果按排序後的 ticker 合併,
    與序列版逐檔輸出一致;單檔失敗仍不連累其他檔。"""
    if feed is not None:
        return price_feed.splits_map(feed, tickers)
    try:
        import yfinance as yf
    except ImportError:
        return {}

    def one(t):
        try:
            s = yf.Ticker(t).splits           # pandas Series:index=日期, value=分割比率(10:1 → 10.0)
            if s is not None and len(s):
                return [(d.date(), float(r)) for d, r in s.items() if r and r > 0]
        except Exception:                     # 單檔抓不到不影響其他檔
            return None
        return None

    todo = sorted(set(tickers))
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
        fetched = list(pool.map(one, todo))   # map 保持輸入順序 → 輸出決定論
    return {t: v for t, v in zip(todo, fetched) if v}

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

def pnl_curve(rows, data, market=None):
    """E2c:投組期間累積報酬曲線(mark-to-market,非已實現)——卡片畫 sparkline 用,不影響 α/β 判定。
    重用 `_port_daily_returns` 已算好的逐日投組報酬,cumprod 成一條「這次復盤怎麼走到這個數字」的線,
    起點錨定 cum_ret=0(復盤期間起算),終點對齊卡面已有的「帳面總損益」那個點——一個點延伸成一張圖。
    只算單一市場(混市場逐日 FX 換算複雜度先不做);呼叫端未傳 scope market 時直接降級交待,
    不做隱性猜測。data=None/樣本不足 → {'note':...} fail-closed 降級,不是死文案(講不講由 card-spec 決定)。
    序列長於 CURVE_MAX_POINTS 天 → 週頻(W-FRI)降採樣,但強制保留最後一天,終點才對得上帳面數字。"""
    try:
        import pandas as pd
    except ImportError:
        return {"note": "無 pandas"}
    if data is None or not len(list(getattr(data, "columns", []))):
        return {"note": "無價格"}
    if market is not None:
        rows = [r for r in rows if r.get("market", "US") == market]
    if not rows:
        return {"note": "無交易"}
    px = data.ffill()
    port = _port_daily_returns(rows, px).dropna()
    if len(port) < 2:
        return {"note": "樣本不足"}
    cum = (1.0 + port).cumprod() - 1.0
    if len(cum) > CURVE_MAX_POINTS:
        weekly = cum.resample("W-FRI").last().dropna()
        if weekly.index[-1] != cum.index[-1]:
            weekly[cum.index[-1]] = cum.iloc[-1]      # 強制含終點,別讓週頻降採樣漂掉「帳面總損益」對應的那一天
        cum = weekly
    anchor_date = (port.index[0] - pd.Timedelta(days=1)).date().isoformat()
    points = [{"date": anchor_date, "cum_ret": 0.0}]
    points += [{"date": d.date().isoformat(), "cum_ret": round(float(v), 4)} for d, v in cum.items()]
    return {"points": points}

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

def dim_size(rows, held, last_px, max_pos_override=None):
    # 用市值（有 yf）或成本算當前權重（rows 參數保留簽名相容;entry-size 序列已移除:
    # 從未進輸出,且混幣下 cum 會跨幣別亂加 —— 兩輪 review 均判 dead code,2026-07-06 刪）
    vals = {}
    for t, (sh, cost) in held.items():
        px = (last_px or {}).get(t)
        vals[t] = sh * px if px else cost
    tot = sum(vals.values()) or 1
    weights = {t: v / tot for t, v in vals.items()}
    # 配置型 ETF 是一籃子資產,不拿「單一公司部位上限」誤殺。產業/主題/槓桿 ETF
    # 仍是集中風險;未知 ticker 保守視為 equity,不會因猜測而取得豁免。
    risk_weights = {t: w for t, w in weights.items()
                    if not instrument_policy.is_diversified_allocation(t)}
    max_t = max(risk_weights, key=risk_weights.get) if risk_weights else None
    max_pct = risk_weights.get(max_t, 0)
    # #324:診斷觸發線與 severity 起算點對齊到同一條線(可被用戶自訂覆寫);severity 從觸發線
    # 而非舊的 0.20 起算,「被標成洞」與「severity>0」不再各自為政。
    trigger = effective_oversize_trigger(max_pos_override)
    sev = min(max((max_pct - trigger) / OVERSIZE_SEV_SPAN, 0), 1)
    others = [w for t, w in risk_weights.items() if t != max_t]  # 「其餘平均」排除最大風險部位;配置型 ETF 不混入單一標的基準
    return dict(dim="部位 sizing", tier=1, triggered=max_pct > trigger,
                severity=sev, max_ticker=max_t, max_pct=max_pct,
                avg_pct=statistics.mean(others) if others else 0.0, weights=weights,
                risk_weights=risk_weights,
                allocation_etfs={t: w for t, w in weights.items()
                                 if instrument_policy.is_diversified_allocation(t)})

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
    risk_w = {t: wt for t, wt in w.items()
              if not instrument_policy.is_diversified_allocation(t)}
    sec = defaultdict(float); ai = 0.0
    for t, wt in risk_w.items():
        s, is_ai = driver(t); sec[s] += wt; ai += wt * is_ai
    classified_sec = {s: v for s, v in sec.items() if s != "未分類"}   # 排除未分類桶,避免 driver_map 沒建好冒充集中度訊號(對齊 what_if() 既有作法)
    max_sec = max(classified_sec, key=classified_sec.get) if classified_sec else None
    max_sec_pct = classified_sec.get(max_sec, 0)
    top3 = sum(sorted(risk_w.values(), reverse=True)[:3])
    sev = min(max((max(max_sec_pct, ai) - 0.40) / 0.40, 0), 1)
    trig = (len(risk_w) >= 8 and max_sec_pct > SECTOR_MAX_TH) or top3 > 0.60 or ai > 0.60
    return dict(dim="分散", tier=2, triggered=trig, severity=sev, n=len(w),
                n_risk=len(risk_w),
                max_sector=max_sec, max_sector_pct=max_sec_pct, ai_pct=ai,
                top3=top3, sectors=dict(sec),
                allocation_etfs={t: wt for t, wt in w.items()
                                 if instrument_policy.is_diversified_allocation(t)})

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
    # 配置型 ETF 的低價定投是一籃子資產再平衡,不是單一標的凹單——breach/次數全豁免
    # (與 dim_size 的豁免同語意);產業/主題/槓桿 ETF 照舊計入。
    exempt = sorted({e["ticker"] for e in avg_down
                     if instrument_policy.is_diversified_allocation(e.get("ticker"))})
    avg_down = [e for e in avg_down
                if not instrument_policy.is_diversified_allocation(e.get("ticker"))]
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
    # #302:per-ticker 次數讓卡片的規矩能指名「這條本期會攔下誰」,不只給總次數。
    # 純聚合,不帶日期/價格——事件細節留在 problem_events,卡面只要可對照的標的。
    ticker_counts = Counter(e["ticker"] for e in avg_down)
    return dict(dim="加碼攤平", tier=1, triggered=(breach >= 1),
                severity=sev, count=cnt, breach=breach, tickers=tickers,
                ticker_counts=dict(ticker_counts),
                allocation_exempt_tickers=exempt)

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
LENS_DIM_ID = {
 "出場紀律": "exit_discipline", "部位 sizing": "position_sizing",
 "分散": "diversification", "持有時間": "holding_period",
 "加碼攤平": "averaging_down", "alpha/beta": "alpha_beta", "進場": "entry_style",
}
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
    lens_dim = LENS_DIM_ID.get(dim, dim)
    if _LENS and lens_dim in _LENS.get("dims", {}):
        d = _LENS["dims"][lens_dim]; m = _LENS.get("philosophy", "lens")
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
                eg = f"（例:{b['ticker']} 你賺 {b['ret']*100:.0f}% 出場，賣完它只動 {b['fwd']*100:+.0f}%——沒賣早）"
        c.append((0.7+(0.35-we), f"該獲利了結時你不手軟：賣掉的賺錢單只有 {we*100:.0f}% 事後繼續漲，代表你不會「抱著賺錢的捨不得賣、結果回吐」{eg}"))
    mp = size_dim.get("max_pct", 1)
    if mp < 0.22:
        c.append((1-mp, f"單筆部位有控制：押最重的一檔也只佔 {mp*100:.0f}%,沒把身家壓在一檔上"))
    if avgdown_dim.get("breach", 1) == 0 and avgdown_dim.get("count", 0) >= 2:
        _egt = (avgdown_dim.get("tickers") or [""])[0]         # 帶一個具體標的當案例;「爆倉」是黑話,改人話「越攤越重」
        c.append((0.65, f"你往下加碼 {avgdown_dim['count']} 次，卻都守在自己的部位上限內、沒讓任何一檔越攤越重"
                        + (f"(例:{_egt})" if _egt else "")))
    if not div_dim.get("triggered") and div_dim.get("n", 0) >= 5:
        c.append((0.6, f"{div_dim['n']} 檔分布在不同驅動因子，沒有全押在同一個故事上"))
    if not hold_dim.get("triggered") and hold_dim.get("median_hold"):
        c.append((0.5, f"進出有一致的節奏：中位持有 {hold_dim['median_hold']:.0f} 天，不是隨機亂買亂賣"))
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

def what_if(held, last_px, threshold=0.25):
    """基於用戶實際持倉動態挑「最大集中暴險」做壓測,而非寫死 AI。
    三個候選:① AI thematic(跨多 sector,driver[1]==1)② 最大 sector ③ 最大個股。
    取佔比最高 且 ≥ threshold 那個當壓測標的;若三者都 < threshold(真正分散)→ return None。
    壓測情境:回檔 30%(一般修正)/ 50%(深熊)。"""
    if not last_px: return None
    mv = {t: sh * last_px[t] for t, (sh, c) in held.items() if t in last_px}
    tot = sum(mv.values())
    if tot <= 0: return None
    risk_mv = {t: v for t, v in mv.items()
               if not instrument_policy.is_diversified_allocation(t)}
    if not risk_mv:
        return None

    # 候選 1:AI thematic 全集(跨 sector)
    ai_mv = sum(v for t, v in risk_mv.items() if driver(t)[1] == 1)
    ai_pct = ai_mv / tot

    # 候選 2:最大 sector(排除「未分類」,避免 driver map 沒載入時誤觸發)
    sector_mv = defaultdict(float)
    for t, v in risk_mv.items():
        sec = driver(t)[0]
        if sec != "未分類":
            sector_mv[sec] += v
    if sector_mv:
        max_sec, max_sec_mv = max(sector_mv.items(), key=lambda x: x[1])
        max_sec_pct = max_sec_mv / tot
    else:
        max_sec, max_sec_mv, max_sec_pct = None, 0.0, 0.0

    # 候選 3:最大個股
    max_t, max_t_mv = max(risk_mv.items(), key=lambda x: x[1])
    max_t_pct = max_t_mv / tot

    # Candidate list (only >= threshold). #279 i18n phase 1: the engine emits a
    # locale-neutral ``scenario`` (stable kind code + data subject); localized
    # labels live in copy/<locale>.json and are resolved by the renderers.
    # Sector names stay as data — they come from the user's driver map.
    cands = []
    if ai_pct >= threshold:
        cands.append((dict(kind="ai_thematic"), ai_mv, ai_pct))
    if max_sec and max_sec_pct >= threshold:
        # 若 AI thematic 已是候選且涵蓋了這個 sector,避免重複(AI 通常 ≥ max_sec)
        if not (ai_pct >= threshold and ai_pct >= max_sec_pct):
            cands.append((dict(kind="sector", sector=max_sec), max_sec_mv, max_sec_pct))
    if max_t_pct >= threshold:
        # 若 AI 或 sector 已涵蓋且佔比更高,個股不再重複(避免「單檔 NVDA 50%」與「半導體 50%」並列)
        if max_t_pct > max(ai_pct, max_sec_pct):
            cands.append((dict(kind="single_ticker", ticker=max_t), max_t_mv, max_t_pct))

    if not cands: return None
    scenario, mval, pct = max(cands, key=lambda x: x[2])  # 取佔比最高那個
    return dict(scenario=scenario, mval=mval, pct=pct,
                drop30=mval * 0.30, drop50=mval * 0.50)

def prescribe(ab, dims, overview, max_pos_override=None):
    """處方層:從歸因 + 診斷生成優化路徑——揚長 edge / 外包短板 / 砍損耗。每條盡量附可驗 metric。
    關鍵:處方是『放大你強的 + 外包你弱的』,不是通用避短。力量來自歸因精確(ChatGPT 沒你的數字不敢這樣說)。"""
    # #279 i18n phase 1: prescription rows carry stable codes plus raw params.
    # ``code`` selects the sentence template and ``kind`` the category label in
    # copy/<locale>.json; the renderers format the numbers. ``verify``/``rule``
    # remain v1-only zh fields (v2 resolves rule wording via copy "rules").
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
            rx.append(dict(code="amplify_hypothesis", kind="amplify_hypothesis",
                           params={"excess": spy["excess"], "allocation": alloc},
                           verify="記錄『下一個賽道』判斷,事後對帳"))
        if not ab.get("credible"):                           # t 不顯著 → 不下選股能力定論(外包/真 edge)
            rx.append(dict(code="selection_inconclusive", kind="selection_inconclusive",
                           params={"selection": sel, "t": st.get("t")}))
        elif sel < -0.05 or st.get("alpha_ann", 0) < 0:      # 統計站得住且選股在虧 → 外包
            rx.append(dict(code="outsource_selection", kind="outsource",
                           params={"selection": sel},
                           verify="被動部位佔比(升→好)"))
        elif sel > 0.05:                                      # 統計站得住且選股在賺 → 真 edge
            rx.append(dict(code="amplify_selection_edge", kind="amplify",
                           params={"selection": sel}))
    ad = dd.get("加碼攤平", {})
    if ad.get("count", 0) >= 10 or ad.get("breach", 0) >= 1:
        rx.append(dict(code="cut_averaging_down", kind="cut_loss", dim="加碼攤平",
                       params={"count": ad.get("count", 0)},
                       verify="虧損加碼次數(降→好)",
                       rule="虧損部位一律不加碼;真想加,先整筆賣掉隔天重買(逼你重新面對『現在還會買它嗎』)"))
    sz = dd.get("部位 sizing", {})
    # #324:處方觸發線對齊到診斷同一條線(原 0.30 → OVERSIZE_TRIGGER，25–30% 不再被標成洞卻拿不到規矩);
    # 觸發線與規矩文案的上限都吃用戶自訂覆寫。#29:解開互斥 gate,攤平與 sizing 可同時成候選。
    if sz.get("max_pct", 0) > effective_oversize_trigger(max_pos_override):
        rx.append(dict(code="cut_oversize", kind="cut_loss", dim="部位 sizing",
                       params={"ticker": sz.get("max_ticker"), "max_pct": sz.get("max_pct", 0)},
                       verify="單筆最大佔比(降→好)",
                       rule=f"單筆部位上限定死 {effective_position_cap(max_pos_override):.0%},超過就減"))
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
        # #279 i18n phase 1: tags are stable codes + raw params; localized
        # wording lives in copy/<locale>.json and is resolved by the renderers.
        if cls == "疑似凹單":                         # 主從分類:只在虧損買 + 金額加速
            if cur is not None and cur < -0.10:
                tags.append({"code": "suspected_averaging_down_losing",
                             "params": {"n_adds": n_adds, "cur": cur}})
            else:
                tags.append({"code": "suspected_averaging_down_recovered",
                             "params": {"n_adds": n_adds}})
        elif cls == "待確認" and n_adds >= 4:
            tags.append({"code": "adds_pending_confirmation", "params": {"n_adds": n_adds}})
        elif cls == "疑似定投":
            tags.append({"code": "suspected_dca", "params": {"n_adds": n_adds}})
        if cls != "疑似凹單" and cur is not None and cur < -0.40:
            tags.append({"code": "deep_underwater", "params": {"cur": cur}})
        if a["win_n"] >= 2 and a["win_early"] / a["win_n"] > 0.5:
            tags.append({"code": "sold_winner_early",
                         "params": {"win_early": a["win_early"], "win_n": a["win_n"]}})
        if wpct > 0.25 and not instrument_policy.is_diversified_allocation(t):
            tags.append({"code": "too_heavy", "params": {"wpct": wpct}})
        if cur is not None and cur > 0.20 and cls not in ("疑似凹單", "待確認"):
            tags.append({"code": "disciplined_hold", "params": {"cur": cur}})
        if not tags:
            tags.append({"code": "roughly_neutral", "params": {}})
        thesis_q = None                              # 只對疑似凹單/待確認問 thesis(定投不問;配置型 ETF 定投也不問)
        if cls in ("疑似凹單", "待確認") and n_adds >= 4 and cur is not None \
                and not instrument_policy.is_diversified_allocation(t):
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
        return f"你持有 {d['n']} 檔看似分散，但 AI capex 暴險 {d['ai_pct']*100:.0f}%、最大板塊「{d['max_sector']}」{d['max_sector_pct']*100:.0f}%、top3 {d['top3']*100:.0f}%——同一個驅動因子"
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
        if instrument_policy.is_diversified_allocation(e.get("ticker")):
            continue                                       # 配置型 ETF 定投不是單一標的凹單,與 dim_avgdown 豁免同語意
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
                avg_down=None, last_px=None, prev_end=None, cash=None,
                portfolio_structure=None, price_snapshot=None, market_context=None,
                max_pos_override=None, price_provenance=None, price_request=None):
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
    d_exit = dd.get("出場紀律", {})
    d_hold = dd.get("持有時間", {})
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
    # 樣本不足(§4.4, #306):完整買賣回合 < MIN_ROUND_TRIPS → 已實現行為太薄,不硬出 commitment。
    # #306 起「交易跨度」不再進這條硬閘(取代 #21.4 的 span gate):判準是「回合夠不夠完整」,
    # 不是日曆窗長短——免得把高頻短窗檔(回合多但跨度短)誤殺。跨度只當 review._review_tier
    # 的 durability_short 提示;承諾本就可 skip,窗短提示即可,不用 span 硬壓掉整條承諾。
    # 不綁 α 樣本(ab.n):離線/無價格時 ab.n=0,但行為維(sizing/攤平/分散)仍可承諾;
    # α 是否可信另由 alpha_credible 表示,別讓「沒價格」誤殺行為層的 commitment(codex review)。
    insufficient = len(rts) < MIN_ROUND_TRIPS
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
    add_cursors = current_cycle_add_cursors(rows)
    holdings = {t: {"shares": round(sh, 4), "cost": round(c, 2),
                    "avg_cost": round(c / sh, 4) if sh > 1e-9 else None,
                    "cycle_start": cyc.get(t, {}).get("start"),
                    # 算不出開倉（CSV 缺期初持倉）→ 標 #unknown，不 fallback 裸 ticker（雙審 codex#4）
                    # ⚠️ 格式契約 = 頂部 CYCLE_ID_RE / CYCLE_ID_UNKNOWN_RE(#61):改這裡必先改常數,契約測試會抓
                    "cycle_id": f"{t}#{cyc[t]['start']}#{cyc[t]['seq']}" if t in cyc else f"{t}#unknown",
                    "add_count": (add_cursors.get(t) or {}).get("add_count", 0),
                    "decision_cursor": (add_cursors.get(t) or {}).get("decision_cursor")}
                for t, (sh, c) in held.items()}
    p_events, p_opps = build_problem_events(
        dims, rts, avg_down, held, last_px,
        rows[-1]["date"].isoformat() if rows else None, prev_end, rows=rows)
    return {
        "schema_version": 2,                               # currency_meta 為 optional 附加欄,舊讀者 .get 不受影響
        "currency_meta": currency_meta,                    # #51/#129 PR-2a:聚合幣別/fx/分幣桶(單幣 USD → 大多為 None)
        "portfolio_structure": portfolio_structure,        # v2 orchestration P0:ETF 配置/集中語意 + metadata 缺口
        "date_start": rows[0]["date"].isoformat() if rows else None,
        "date_end": rows[-1]["date"].isoformat() if rows else None,
        # #270:這次實際採用的 prev_end(已解過同週重跑自我別名),持久化供下次 review 當
        # TR_PREV_PREV_END 的來源——同一週被重跑任意次都能穩定解回同一個「真正更早」的邊界。
        "prev_end": prev_end,
        "n_trades": len(rows),
        "n_round_trips": len(rts),
        "n_held": len(held),
        "headline_dim": headline_dim,                      # 這次最大的洞(給「新增診斷」用)
        "headline_metric": {"key": hk, "value": hv},
        "commitment": commitment,                          # 下次對帳的錨點(規矩 + 追蹤 metric)
        "max_position_pct": valid_position_cap(max_pos_override),  # #324:本次診斷/規矩文案採用的用戶自訂上限(None=通用預設);renderer 與下次對帳讀它
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
            "exit_severity": d_exit.get("severity"),       # v2 commitment:所有 headline 都有可跨期追蹤錨點
            "hold_severity": d_hold.get("severity"),
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
        "price_snapshot": price_snapshot,                   # #191 PR B:review-time prices for deterministic exit/swap comparison; frozen in the session plan
        "price_provenance": price_provenance,               # #289:價格來源與覆蓋率(engine_fetch / agent_feed / unavailable),degraded 模式必須可觀測
        "price_request": price_request,                     # #289:還缺哪些價的機讀清單,agent 據此去公認資料源補檔再重跑 prepare
        "market_context": market_context,                   # #191 PR B:SPY/QQQ/VIX review window; renderer consumes without refetching
        "problem_events": p_events,                         # #137 問題帳:本次規約出的事件(SKILL 收尾 append 進 problems.jsonl)
        "problem_opportunities": p_opps,                    # 各 key 本期有無機會犯(規矩對位的 Opportunity Check)
    }

# ─────────────────── 結構化 card data(給 Claude 寫敘事卡用)───────────────────
def _attribution_gaps(ab):
    """Return every market whose allocation/selection split is incomplete.

    Mixed-market compatibility fields mirror only ``scope``. Honesty checks
    must inspect ``by_market`` instead, otherwise a complete scope market can
    hide an incomplete split that the renderer shows for another market.
    """
    if not isinstance(ab, dict):
        return {}
    by_market = ab.get("by_market")
    if isinstance(by_market, dict) and by_market:
        rows = [(str(market), row) for market, row in sorted(by_market.items())]
    else:
        rows = [(str(ab.get("scope") or "portfolio"), ab)]
    gaps = {}
    for market, row in rows:
        if not isinstance(row, dict) or row.get("note"):
            continue
        split = row.get("excess_split")
        if not isinstance(split, dict):
            continue
        unproxied = sorted({str(ticker) for ticker in (split.get("unproxied") or []) if ticker})
        coverage = split.get("coverage")
        try:
            partial_coverage = coverage is not None and float(coverage) < 0.995
        except (TypeError, ValueError):
            partial_coverage = False
        if unproxied or partial_coverage:
            gaps[market] = {"coverage": coverage, "unproxied": unproxied}
    return gaps


def _merge_attribution_integrity(data_integrity, ab):
    """Merge per-market unproxied tickers into the existing integrity field."""
    gaps = _attribution_gaps(ab)
    unproxied = {str(ticker) for ticker in (data_integrity.get("unproxied_sectors") or []) if ticker}
    for gap in gaps.values():
        unproxied.update(gap["unproxied"])
    if unproxied:
        data_integrity["unproxied_sectors"] = sorted(unproxied)
    return gaps


def build_honesty_ledger(overview, ab, data_integrity, currency_meta, cash=None, acct_perf=None,
                         portfolio_structure=None, price_provenance=None):
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
    # 板塊歸因不全:逐市場檢查,避免頂層 scope 的完整 coverage 遮掉另一市場的缺口。
    attribution_gaps = _attribution_gaps(ab)
    unproxied = {str(ticker) for ticker in (di.get("unproxied_sectors") or []) if ticker}
    for gap in attribution_gaps.values():
        unproxied.update(gap["unproxied"])
    if attribution_gaps or unproxied:
        coverages = [gap.get("coverage") for gap in attribution_gaps.values()
                     if gap.get("coverage") is not None]
        data = {"coverage": min(coverages) if coverages else None,
                "unproxied": sorted(unproxied)}
        if isinstance(ab, dict) and isinstance(ab.get("by_market"), dict):
            data["by_market"] = attribution_gaps
        L.append({"key": "sector_attribution", "status": "partial",
                  "data": data})
    # 未分類 driver:分散維可能偏樂觀(假分散抓不準)
    if di.get("unclassified_drivers"):
        L.append({"key": "unclassified_drivers", "status": "present",
                  "data": {"tickers": list(di["unclassified_drivers"])}})
    # 價格來源(#289):這一項排在 unrealized_coverage 之前,因為它是「因」不是「果」。
    # 卡面 Block-1 footnote 依 ledger 順序逐句列出誠實點(#276 §4 收斂:不再各自搶指標行的
    # host),所以「因先於果」這個閱讀順序由這裡的 append 次序保證,而不是 renderer 端的配位。
    #   agent_feed  = 現價由 agent 從外部資料源查回、engine 自己沒抓(provenance 必揭露)
    #   unavailable = engine 抓價失敗又沒人補檔(資料可得性故障,不是下市判定、不是零報酬)
    # engine 自己抓到但個別檔缺價 → 不觸發,那是 unrealized_coverage 的敘事,不疊床架屋。
    pp = price_provenance or {}
    if pp.get("mode") in ("agent_feed", "unavailable"):
        cov_pp = pp.get("coverage") or {}
        L.append({"key": "price_source", "status": pp["mode"],
                  "data": {"source": pp.get("source"), "as_of": pp.get("as_of"),
                           "series": pp.get("series"), "error": pp.get("error"),
                           "splits_applied": pp.get("splits_applied"),
                           "priced_n": cov_pp.get("priced_n"),
                           "requested_n": cov_pp.get("requested_n"),
                           "missing": cov_pp.get("missing")}})
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
    # 現金可信度(#171 無/部分錨點盲算 + #180 多錨點對帳殘差):兩種缺陷對用戶都是「現金這塊多準」的
    # 同一段話,共用 cash_reliability key(#82:key=敘事段落非誠實點)。
    #   ① 盲算:weight 非 None 但 reliable=False(假設開戶 $0,可能偏差)→ 邀補現金餘額。weight=None
    #      (算不出、不上卡)沒有可誤導的數字,不進 ledger。
    #   ② 殘差:有錨點但錨點間現金史對不上(data_integrity.cash_residuals)→ 錨點可信時也要揭露,
    #      故不綁 acct_twr 出不出數(有別於 acct_perf_basis)。殘差細節進 data.residuals 讓 Claude 講。
    di_residuals = di.get("cash_residuals")
    cash_blind = isinstance(cash, dict) and cash.get("weight") is not None and not cash.get("reliable")
    if cash_blind or di_residuals:
        cd = cash if isinstance(cash, dict) else {}
        missing = sorted(c for c, v in (cd.get("by_currency") or {}).items() if not v.get("reliable"))
        status = ("partial" if cash_blind and cd.get("source") == "partial"
                  else "no_anchor" if cash_blind else "residual")
        data = {"balance": cd.get("balance"), "source": cd.get("source"),
                "unanchored_currencies": missing}
        if di_residuals:
            data["residuals"] = di_residuals
        L.append({"key": "cash_reliability", "status": status, "data": data})
    # 帳戶級績效地基有洞(#171):帳戶 TWR 有出數、但算在部分錨點 / 缺價檔成本平線 / fx 即期
    # 近似之上 → 數字可用但地基要交代(哪個幣別盲算、哪些檔平線零報酬、匯損益是近似)。
    # 沒出數(gate 掉)不觸發——cash_reliability / note 已各自說明,不疊床架屋。
    if isinstance(acct_perf, dict) and acct_perf.get("acct_twr") is not None:
        b = acct_perf.get("basis") or {}
        if b.get("unanchored") or b.get("at_cost_tickers") or b.get("fx_approx"):
            status = ("partial_anchor" if b.get("unanchored") else
                      "partial_coverage" if b.get("at_cost_tickers") else "fx_approx")
            L.append({"key": "acct_perf_basis", "status": status,
                      "data": {"unanchored": b.get("unanchored"),
                               "at_cost_tickers": b.get("at_cost_tickers"),
                               "fx_approx": b.get("fx_approx"),
                               "cash_source": b.get("cash_source")}})
    # ETF 費用率 / tracking error 沒資料時不猜數字。只要這次有 ETF 且 metadata 不全,
    # renderer 必須明說「尚未納入」；這是 P0 的誠實邊界,不是要把缺值補成 0。
    ps = portfolio_structure or {}
    if ps.get("metadata_gaps"):
        L.append({"key": "etf_metadata", "status": "partial",
                  "data": {"gaps": list(ps["metadata_gaps"])}})
    return L


def build_card_data(dims, strength, overview, best, worst, wi, rx, tdiag,
                    ab, pa, master, data_integrity=None, currency_meta=None, cash=None,
                    acct_perf=None, pnl_curve_data=None, portfolio_structure=None,
                    currency_by_ticker=None, price_provenance=None, price_request=None):
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
        # Per-trade amounts stay in brokerage/original currency.  The v2 renderer
        # must never apply the aggregate portfolio label to these raw values.
        "best_trade": ({**best,
                         "pnl": best["qty"] * (best["sell_px"] - best["buy_px"]),
                         "currency": (currency_by_ticker or {}).get(best["ticker"]) or
                                     (None if (currency_meta or {}).get("mixed") else
                                      (currency_meta or {}).get("aggregate_currency") or "USD")}
                       if best else None),
        "worst_trade": ({**worst,
                          "pnl": worst["qty"] * (worst["sell_px"] - worst["buy_px"]),
                          "currency": (currency_by_ticker or {}).get(worst["ticker"]) or
                                      (None if (currency_meta or {}).get("mixed") else
                                       (currency_meta or {}).get("aggregate_currency") or "USD")}
                        if worst else None),
        "what_if": wi,
        "ticker_diagnosis": tdiag,                          # tags = stable codes + params (#279); renderers resolve via copy
        "thesis_questions": thesis_questions,               # ⚠️ Step 2 對話用,不准印卡上
        "top_holes": top_holes,                             # top 1-2,Claude 寫敘事用
        "candidate_rules": candidate_rules,                 # 2-3 條候選,讓用戶挑/改一條
        "prescriptions": rx,                                # 完整處方層
        "alpha_beta_breakdown": ab,
        "payoff_attribution": pa,
        "dims_raw": dims,                                   # 5 維 raw,Claude 用「一句人話」帶過其餘維
        "data_integrity": data_integrity or {},             # 賣超/未分類 driver — 影響數據可信度,Claude 該主動提
        "currency_meta": currency_meta,                     # #51/#129 PR-2a:聚合幣別/fx/分幣桶;None=單幣 USD 舊行為
        "portfolio_structure": portfolio_structure,         # ETF P0:配置型豁免、集中 ETF 仍計風險、metadata 誠實缺口
        "cash": cash,                                        # #171 PR-1:帳戶現金(balance/weight/source/reliable/recent_net_deposit);reliable 才上 weight/入金判讀,無錨點靠 honesty 揭露
        "acct_perf": acct_perf,                              # #171 B 路線:帳戶級 TWR/cash drag/IRR(daily 鏈式;{note} = 沒算,acct_twr=None+hold_twr 有值 = 現金 gate 只出持倉柱)
        "price_provenance": price_provenance,                # #289:這次的價格從哪來(engine_fetch / agent_feed / unavailable)
        "price_request": price_request,                      # #289:還缺哪些價的機讀清單;None=無缺口
        "honesty_ledger": build_honesty_ledger(overview, ab, data_integrity, currency_meta, cash, acct_perf,
                                                portfolio_structure, price_provenance),  # #82:卡面必講的誠實點清單(空=無缺口);出卡前 gate 對照源
        "pnl_curve": pnl_curve_data or {"note": "無資料"},   # #167:累積損益曲線,卡片畫 sparkline 用(一個點→一張圖);{'note':...} = 誠實降級,不硬畫
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
    # #270:date_end 一旦可知(rows 已排序、非空)就立刻凍結 prev_end,下面所有消費點
    # (review_window / cash_position / build_state)共用同一個解過的值,不再各自現讀
    # os.environ——避免同週重跑時只解出「別名成自己」的 TR_PREV_END。
    date_end = rows[-1]["date"].isoformat()
    prev_end = _resolve_prev_end(date_end, os.environ.get("TR_PREV_END") or None,
                                 os.environ.get("TR_PREV_PREV_END") or None)
    master = load_lens()                                  # 顯示用哲學名(去名,可換大師/哲學檔)
    max_pos_override = valid_position_cap(os.environ.get("TR_MAX_POSITION_PCT"))  # #324:用戶自訂單一部位上限(review.py 從 profile.json 帶入);壞值 fail-closed 退回通用預設
    dm = os.environ.get("TR_DRIVER_MAP")                  # Claude 生成的 driver map(冷門股分類)
    n_dm = load_driver_map(dm) if dm else 0
    im_result = instrument_policy.load_from_env()          # 本機 ETF/instrument 覆寫;未知標的不猜 ETF
    if im_result.get("error"):
        print(f"⚠️  instrument map 載入失敗: {im_result['error']} — 改用保守 fallback", file=sys.stderr)
    # 供給式價格檔(#289):沙箱 host 抓不到 Yahoo 時,agent 從公認資料源查回來的 envelope。
    # 壞檔一律 fail closed:價格是錢,靜默算錯比誠實缺價更糟,agent 修好 envelope 重跑即可。
    try:
        feed = price_feed.load_from_env()
    except price_feed.PriceFeedError as e:
        print(f"❌ 供給的價格檔不合格:{e}", file=sys.stderr)
        sys.exit(1)
    if feed:
        conflicts = price_feed.currency_conflicts(feed, currency_map(rows)[0])
        if conflicts:
            detail = "; ".join(f"{c['ticker']} 價格檔記 {c['feed']}、交易紀錄記 {c['trades']}"
                               for c in conflicts)
            print(f"❌ 供給的價格檔幣別與交易紀錄不符({detail})——修正 envelope 後重跑 prepare",
                  file=sys.stderr)
            sys.exit(1)
    splits = fetch_splits({r["ticker"] for r in rows}, feed=feed)
    n_adj = adjust_for_splits(rows, splits)                # 分割調整,對齊今日價
    rts, open_lots = round_trips(rows)
    _, avg_down = positions(rows)      # avgdown 偵測留 avg cost(行為語意:買價 vs 平均持倉成本)
    held = fifo_held(open_lots)        # #162:未實現改 FIFO 剩餘,與 realized 同基礎,加總=真值
    tickers = {r["ticker"] for r in rts} | set(held.keys())
    trade_price_start = (min((r["entry"] for r in rts), default=rows[0]["date"])
                         - dt.timedelta(days=10)).isoformat()
    context_start, context_end = review_window(date_end, prev_end)
    start = shared_price_start(trade_price_start, context_start, context_end)
    t_market = {r["ticker"]: r.get("market", "US") for r in rows}   # ticker→market(per-market 基準/拆帳用)
    bench = {p for p in (_sector_proxy(t, t_market.get(t, "US")) for t in tickers) if p}   # 拆帳要的板塊 ETF(押賽道 vs 選股)
    bench |= {MARKET_BENCH.get(m, "SPY") for m in set(t_market.values())}   # 各市場主基準(TW→^TWII)一起抓
    px, yf_err = fetch_prices(tickers | bench, start, feed=feed)
    n_fwd = adaptive_n_fwd(rows)                           # 觀察窗隨資料長度自適應
    fwds, last_px = fwd_from_px(rts, px, n_fwd)
    last_px = last_px or {}                                # 離線/無價格 → {} 而非 None,讓下游(ticker_diagnosis 等)不 crash
    review_market = market_context_from_prices(px, yf_err, context_start, context_end)
    price_as_of = (px.index[-1].date().isoformat() if px is not None and len(px.index) else context_end)
    price_snapshot = {"as_of": price_as_of,
                      "prices": {ticker: round(value, 6)
                                 for ticker, value in sorted(last_px.items())}}
    decision_rts = [r for r in rts
                    if driver(r["ticker"])[0] not in BENCH_SELF
                    and not instrument_policy.is_diversified_allocation(r["ticker"])]  # 配置 ETF 再平衡/現金管理,非選股決策
    # 多市場幣別(#51/#129 PR-2a):跨 ticker 聚合必須在共同幣別(USD)上做,否則台股 985 元 + 美股 985 美元
    # 直接相加 = 靜默算錯。單一幣別組合(含純台股)聚合自洽 → 不抓匯率、路徑零變化。
    cur_map, currencies, cur_conflicts = currency_map(rows)
    mixed_ccy = len(currencies) > 1
    # Rendering is deterministic and must not fetch at preview/finalize time.
    # The orchestration layer therefore requests the locale's display currency
    # during prepare, even when that currency is not held in the portfolio.
    requested_display = str(os.environ.get("TR_DISPLAY_CURRENCY") or "").strip().upper()
    fx_currencies = fx_request_currencies(currencies, requested_display)
    fx, fx_err = fetch_fx(fx_currencies, feed=feed) if mixed_ccy else ({"USD": 1.0}, None)
    # 價格可得性(#289):這次的價從哪來、覆蓋到哪、還缺什麼——全部機讀化。
    # 缺價不是「零報酬」也不是「下市」,是資料可得性故障,必須對用戶與 QA 都保持可見。
    priced = {t for t in tickers if t in last_px}
    # 沒有價格框 = unavailable,不管有沒有人餵過檔:餵了一份完全對不上持倉的 envelope
    # (符號寫錯之類)不該對外宣稱「價格已由外部供給」,error 欄會說清楚是哪一種失敗。
    price_provenance = price_feed.provenance(
        mode=("unavailable" if px is None else ("agent_feed" if feed else "engine_fetch")),
        feed=feed, error=yf_err, requested=tickers, priced=priced,
        benchmarks_priced={b for b in bench if b in last_px},
        fx_mode=("not_needed" if not mixed_ccy else
                 ("feed" if feed else ("engine_fetch" if not fx_err else "missing"))),
        splits_applied=bool(splits), as_of=price_as_of)
    price_request = None
    if price_provenance["coverage"]["missing"] or not price_provenance["benchmarks_priced"]:
        price_request = price_feed.build_request(
            tickers=tickers, benchmarks=[b for b in bench if b not in last_px],
            currencies=[c for c in currencies if c not in fx],
            window=(context_start, context_end), as_of=context_end,
            earliest_trade=start, reason=yf_err,
            missing=price_provenance["coverage"]["missing"])
    if mixed_ccy:
        rts_u, held_u, lastpx_u = usd_view(rts, held, last_px, cur_map, fx)
        decision_rts_u = [r for r in rts_u
                          if driver(r["ticker"])[0] not in BENCH_SELF
                          and not instrument_policy.is_diversified_allocation(r["ticker"])]
    else:
        rts_u, held_u, lastpx_u, decision_rts_u = rts, held, last_px, decision_rts
    # 殘倉過濾(#172):市值<0.1% 的部位不進分散度/what-if/per-ticker 診斷/未分類計數;
    # overview(P&L)/dim_size(單筆過重本就只看大倉)/n_held(對帳全量)不動 → 不藏虧損、對帳一致。
    keep_dx = meaningful_tickers(held_u, lastpx_u)
    held_dx = {t: v for t, v in held_u.items() if t in keep_dx}
    d_size = dim_size(rows, held_u, lastpx_u, max_pos_override)
    d_exit = dim_exit(decision_rts, fwds, n_fwd); d_div = dim_diversify(held_dx, lastpx_u)
    portfolio_structure = instrument_policy.portfolio_analysis(d_size.get("weights"))
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
    # 累積損益曲線(卡片 sparkline,#167):單一市場才算;多市場沿用 α/β 已選好的 scope,
    # 混市場又沒選出 scope(樣本不足)就誠實不畫,別隱性猜哪個市場該代表。
    markets_present = sorted(set(t_market.values()))
    curve_market = (ab.get("scope") if isinstance(ab, dict) else None) or (
        markets_present[0] if len(markets_present) == 1 else None)
    pc = pnl_curve(rows, px, market=curve_market) if curve_market else {"note": "混市場尚未支援"}
    overview = overview_stats(decision_rts_u, ab, held_u, lastpx_u)   # 已實現 + 未實現都報(聚合幣別上)
    pa = payoff_attribution(decision_rts_u)                # 盈虧比拆解:重點交易的貢獻度(聚合幣別上)
    best, worst = best_worst(decision_rts)                 # 做得最好/最差的一筆(ret%,無因次 → 原幣)
    wi = what_if(held_dx, lastpx_u)                        # 可量化的 what-if(聚合幣別上,#172 殘倉不計)
    rx = prescribe(ab, dims, overview, max_pos_override)   # 處方層:揚長/外包/砍損耗(sizing 觸發線 + 規矩上限吃自訂覆寫)
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
    _merge_attribution_integrity(data_integrity, ab)
    currency_meta = {
        "currencies": currencies,
        "mixed": mixed_ccy,
        # 聚合數字(overview/payoff/what-if/sizing 權重)的幣別:混幣 → USD;單幣 → 該原幣。
        # display currency(語言→幣別)換算是 SKILL 呈現層的責任,engine 只提供 fx。
        "aggregate_currency": "USD" if mixed_ccy else (currencies[0] if currencies else "USD"),
        "fx": ({c: r for c, r in fx.items() if c != "USD"} or None) if mixed_ccy else None,  # {cur: 兌 USD}
        "fx_error": fx_err,
        "requested_display_currency": requested_display or None,
        # ⚠️ 分桶刻意吃「原幣」物件(decision_rts/held,非 _u 版):桶的意義就是原幣會計事實,換成 _u = 全桶變 USD 廢掉
        "pnl_by_currency": pnl_by_currency(decision_rts, held, last_px, cur_map) if mixed_ccy else None,
        "alpha_beta_note": (
            f"α/β 已 per-market 分算:頂層數字僅含 {ab.get('scope')} 部位(對 {ab.get('bench')}),"
            f"其他市場見 by_market——不合成總 α"
            if isinstance(ab, dict) and ab.get("scope")
            else ("多幣別組合(單一市場)的 α/β 按該市場大盤計" if mixed_ccy else None)),
    }

    # 帳戶現金地基（#171）：現金流 + 現金餘額錨點 → cash_position（多幣別 per-currency 各算再 fx 聚合）。
    # held_mv = 持倉市值（聚合幣別 USD，無現價用成本近似，同 dim_diversify）= cash_weight 分母。
    import json
    cash_flows = load_cash_flows(paths)                 # per-currency（每筆帶 currency）；聚合由 cash_position 內部做
    held_mv = sum((sh * lastpx_u[t]) if lastpx_u.get(t) else c for t, (sh, c) in held_u.items())
    _ca = os.environ.get("TR_CASH")                     # SKILL Step 0 抓對帳單現金餘額 → 單 {as_of,amount,currency} 或多帳戶 list
    try:
        cash_anchor = json.loads(_ca) if _ca else None
    except (ValueError, TypeError):
        cash_anchor = None
    # 多幣別：cash_position 內部 per-currency 各算餘額再用 fx 聚合（台美各帳戶各自錨點）；單幣 fx=None → 因子 1.0。
    cash_data = cash_position(cash_flows, held_mv, anchor=cash_anchor,
                              prev_end=prev_end,
                              fx=fx if mixed_ccy else None)

    # 帳戶級績效(#171 B 路線,拍板 2026-07-12 見該 issue comment):daily 鏈式 TWR + cash drag +
    # 帳戶 IRR。外部流=deposit/withdrawal/other(對帳口徑);可用性繼承 cash reliable 三態;
    # 混幣用每日 fx 序列(含匯損益),序列抓不到 → 即期常數近似(perf 標 fx_approx、honesty 揭露)。
    from perf import account_perf, cash_reconcile_residuals
    # #180 多錨點對帳殘差:讀 ledger 累積的 cash snapshot(多錨點)→ 逐段 rollforward 殘差 →
    # data_integrity(永遠記,觸發式);殘差大到污染每天淨值 → account_perf 內部 gate 掉帳戶柱。
    # 隱私:TR_LEDGER 預設本機真帳本,測試/試駕須釘到空/dev/null——「不落盤 ≠ 不讀盤」:不釘會
    # 把真餘額洩進示範卡、或在 owner 機讀到真 ledger 汙染契約測試(見 SKILL Step 0 / 契約測試)。
    from ledger import load_ledger, DEFAULT_LEDGER
    _levents, _ = load_ledger(os.environ.get("TR_LEDGER") or DEFAULT_LEDGER)
    _cash_snaps = [{"as_of": e["as_of"], "cash": e["cash"]}
                   for e in _levents if e.get("type") == "snapshot" and e.get("cash")]
    cash_residuals = cash_reconcile_residuals(_cash_snaps, cash_flows,
                                              fx=fx if mixed_ccy else None)
    if cash_residuals:
        data_integrity["cash_residuals"] = cash_residuals
    fx_series = None
    if mixed_ccy and px is not None:
        fx_series, _fxs_err = fetch_fx_series(currencies, start, feed=feed)
    acct_perf = account_perf(rows, px, cash_flows, cash_data, cur_map,
                             fx_spot=fx if mixed_ccy else None, fx_series=fx_series,
                             cash_residuals=cash_residuals)

    dm_skip = f"({_DM_SKIPPED} 筆格式錯跳過)" if _DM_SKIPPED else ""
    split_note = f"｜分割調整: {n_adj} 筆" if n_adj else ""
    # 價格狀態一行(#289):兩種輸出模式都印,degraded 與供給式來源都不准靜默。
    px_note = ({"agent_feed": f"供給價格檔 OK（{price_provenance.get('source')}"
                              f"，{price_provenance['coverage']['priced_n']}/"
                              f"{price_provenance['coverage']['requested_n']} 檔）",
                "unavailable": f"價格不可得（{yf_err or '來源無回應'}）——已輸出 price_request 待補",
                }.get(price_provenance["mode"], "OK" if not yf_err else yf_err))
    # JSON 模式(SKILL Step 3 走這條):stdout 純 JSON 給 Claude 寫敘事卡;meta 走 stderr 不污染
    if os.environ.get("TR_JSON"):
        import json
        meta = (f"# 載入 {len(rows)} 筆交易{_load_skip_note()}（{rows[0]['date']} ~ {rows[-1]['date']}），"
                f"{len(rts)} round-trip,持倉 {len(held)}｜價格: {px_note}"
                f"｜鏡片: {master or 'fallback'}｜driver map: {n_dm} 檔{dm_skip}{split_note}"
                + (" (純 fallback,冷門股可能失準)" if not n_dm else ""))
        print(meta, file=sys.stderr)
        card = build_card_data(dims, strength, overview, best, worst, wi, rx, tdiag,
                               ab, pa, master, data_integrity=data_integrity,
                               currency_meta=currency_meta, cash=cash_data,
                               acct_perf=acct_perf, pnl_curve_data=pc,
                               portfolio_structure=portfolio_structure,
                               currency_by_ticker=cur_map,
                               price_provenance=price_provenance, price_request=price_request)
        print(json.dumps(card, ensure_ascii=False, indent=2, default=str))
    else:
        # 預設:乾淨人話卡(quickstart / fallback 用,#20 違規條目已砍)
        print(f"# 載入 {len(rows)} 筆交易{_load_skip_note()}（{rows[0]['date']} ~ {rows[-1]['date']}），"
              f"{len(rts)} 個 round-trip，當前持倉 {len(held)} 檔。", end="")
        print(f" 價格: {px_note}｜鏡片: {master or 'fallback'}"
              f"｜driver map: {n_dm} 檔{dm_skip}{split_note}" + (" (純 fallback,冷門股可能失準)" if not n_dm else ""))
        print(f"# 出場紀律只看「決策賣出」：{len(decision_rts)}/{len(rts)} round-trip"
              f"（排除 {len(rts)-len(decision_rts)} 筆大盤/債/商品 ETF 再平衡）")
        import rich_card                               # 延遲 import 避環(#216 刀2a):純函式引擎頂部不依賴 rich_card
        # lens 必須由 main 傳:直跑 `python3 trade_recap.py` 時本模組是 __main__,rich_card 內
        # `import trade_recap` 會載入「另一個」trade_recap 副本(其模組級 _LENS 恆為 None)——
        # 只有 main 這裡的 bare _LENS 才是 load_lens() 已填好的值。
        rich_card.render(dims, strength, overview, best, worst, wi, rx, tdiag, cash=cash_data,
                         acct=acct_perf, lens=_LENS)
        rich_card.print_alpha_beta(ab)
        rich_card.print_payoff_attr(pa)                   # 盈虧比拆解(誰在撐/拖,反事實)
        d_entry = dim_entry_style(rows, px)               # 【風格】維雛形(不進洞排序,先驗訊號)
        rich_card.print_entry_style(d_entry)
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
                            prev_end=prev_end,
                            cash=cash_data, portfolio_structure=portfolio_structure,
                            price_snapshot=price_snapshot, market_context=review_market,
                            max_pos_override=max_pos_override,
                            price_provenance=price_provenance, price_request=price_request)
        # prev_end(#270 解過的值,上次 review 的 date_end,已排除同週重跑自我別名)→
        # behavior 型問題事件只取其後的新交易(weekly 增量);None = 初診全期補齊,問題帳統計冷啟動。
        outdir = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=outdir, suffix=".tmp")  # 原子寫:tmp→replace,不留半寫髒狀態(§4.6)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        print(f"# [state] {path}", file=sys.stderr)        # 訊息走 stderr,不污染卡片

if __name__ == "__main__":
    main()
