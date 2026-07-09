#!/usr/bin/env python3
"""
機械層純函式的回歸防護網(避免重構引擎時改壞地基)。

跟另外兩個測試檔的分工:
- test_sample_styles.py  → 端到端:三風格 CSV 的「頭號洞」排序(對股價漂移容錯)。
- test_state_loop.py     → 端到端:初診→對帳的有狀態迴圈 + current_cycles 邊界。
- test_engine_units.py   → 本檔:單一純函式的行為契約(load / round_trips / positions /
                           classify_adds / overview_stats / payoff_attribution /
                           alpha_credible / dim_avgdown / build_state)。

設計原則(同 test_sample_styles):全部離線、確定性,完全不碰 yfinance —— 權重一律用
『成本基礎』(last_px=None),報酬/盈虧用合成 round-trip。斷言鎖在「與最新股價無關」的
核心契約上:配對數量、FIFO 順序、攤平門檻、分類規則、誠實鐵律(α 不可信→不報數)。

跑法:
  python3 tests/test_engine_units.py        # 標準庫 runner(免 pytest)
  pytest tests/test_engine_units.py         # 若已裝 pytest 亦可被發現
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "skills", "fomo-kernel")
MOCK = os.path.join(SKILL, "mock")
sys.path.insert(0, os.path.join(SKILL, "engine"))
import trade_recap as tr  # noqa: E402
import horizon as hz  # noqa: E402  # #148 item5:horizon 時間軸矛盾(純狀態側,閾值下沉)

_SKIP = "__skip__"        # 與 test_sample_styles 一致的 skip 哨兵(本檔暫無 network 測試)


# ─────────────────────────── 小工具 ───────────────────────────

def _approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


def _R(t, side, qty, px, d):
    """一筆 raw 交易(load 後的格式),給 round_trips / positions / classify_adds 用。"""
    return dict(ticker=t, side=side, qty=qty, price=px, date=dt.date.fromisoformat(d))


def _RT(t, buy, sell, qty=10):
    """一筆已配對 round-trip(給 overview_stats / payoff_attribution 用)。"""
    return dict(ticker=t, buy_px=buy, sell_px=sell, qty=qty, ret=(sell - buy) / buy,
                hold=10, entry=dt.date(2024, 1, 1), exit=dt.date(2024, 2, 1))


def _write_csv(text):
    fd, p = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# ─────────────────────── A. load():CSV 解析地基 ───────────────────────

def test_load_within_file_keeps_fills_and_filters():
    """#14:同一份對帳單裡「同日同價的兩筆」= 兩筆獨立成交(大單被拆成同價多筆 / 同日分批進場)——
    券商不會把同一筆成交在同一份檔裡列兩次,所以同檔重複列不是「重疊」,必須都保留。
    (跨檔重疊去重見 test_load_dedup_cross_file_overlap;這兩條互為正反面。)
    順帶守其餘濾規則:sell 取絕對值 + Action 大小寫不敏感 / price<=0 偽交易濾除 / 非 Trade 列濾除。"""
    p = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "NVDA,150,50.00,BUY,2024-01-12,Trade\n"
        "NVDA,150,50.00,BUY,2024-01-12,Trade\n"      # #14:同檔同日同價 → 兩筆獨立成交,都保留(舊行為誤砍成 1)
        "AMD,-50,160.00,sell,2024-01-22,Trade\n"     # 小寫 sell + 負數量
        "FREE,10,0.00,BUY,2024-02-01,Trade\n"        # price=0 split/free-share → 濾掉
        "DIV,5,1.00,BUY,2024-02-02,Dividend\n"       # 非 Trade → 濾掉
    )
    try:
        rows = tr.load([p])
    finally:
        os.unlink(p)
    nvda = [r for r in rows if r["ticker"] == "NVDA"]
    amd = [r for r in rows if r["ticker"] == "AMD"]
    assert len(nvda) == 2, f"同檔同日同價的兩筆獨立成交應都保留(#14),實得 {len(nvda)}"
    assert len(rows) == 3, f"2 NVDA + 1 AMD(FREE/DIV 濾掉),實得 {len(rows)}"
    assert amd and amd[0]["side"] == "sell" and amd[0]["qty"] == 50.0, "sell 大小寫不敏感 + qty 取絕對值"
    assert all(r["ticker"] not in ("FREE", "DIV") for r in rows), "price=0 / 非 Trade 應被濾除"
    assert tr._LOAD_STATS["skip_dup"] == 0, "#14:同檔不再併殺 → skip_dup 只計真跨檔重疊,此處應 0"


def test_load_dedup_cross_file_overlap():
    """#14:去重的真正對象 = 跨對帳單重疊期的同一筆(完整歷史 + 增量檔各出現一次)→ 只算 1。
    各檔內獨有的、以及同檔同日同價的獨立成交,不受影響。這是「放心丟全歷史」建議(#52)成立的機制。"""
    full = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "NVDA,10,100.00,BUY,2024-01-02,Trade\n"      # 與 incr 重疊的那筆
        "NVDA,10,100.00,BUY,2024-01-02,Trade\n"      # 同檔同日同價第二筆(獨立成交,保留)
        "AMD,5,90.00,BUY,2024-01-05,Trade\n"
    )
    incr = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "NVDA,10,100.00,BUY,2024-01-02,Trade\n"      # 與 full 第一筆重疊 → 去重(occ 0 撞 occ 0)
        "ORCL,3,180.00,BUY,2024-01-20,Trade\n"       # 增量新交易
    )
    try:
        rows = tr.load([full, incr])
    finally:
        os.unlink(full); os.unlink(incr)
    assert sorted(r["ticker"] for r in rows) == ["AMD", "NVDA", "NVDA", "ORCL"], \
        f"跨檔重疊去 1、其餘全留(含同檔第二筆),實得 {sorted(r['ticker'] for r in rows)}"
    assert tr._LOAD_STATS["skip_dup"] == 1, f"只有 incr 的重疊筆算 dup,實得 {tr._LOAD_STATS['skip_dup']}"


def test_load_sorts_by_date():
    """load() 必須把多列按日期排序 —— round_trips 的 FIFO 與 cycle 判定都假設時間有序。"""
    p = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "A,10,10,BUY,2024-03-01,Trade\n"
        "A,10,10,BUY,2024-01-01,Trade\n"
        "A,10,10,BUY,2024-02-01,Trade\n"
    )
    try:
        rows = tr.load([p])
    finally:
        os.unlink(p)
    # #64:先前這裡沒鎖 len,若 dedup 鍵漏掉日期把 3 筆「同規格不同日」誤砍成 1 筆,
    # 排序斷言仍空洞通過(單一元素必是已排序)——len 斷言才是這條測試真正要守的東西。
    assert len(rows) == 3, f"3 筆同規格不同日應全保留(非去重對象),實得 {len(rows)} 筆"
    dates = [r["date"].isoformat() for r in rows]
    assert dates == sorted(dates), f"load 應按日期排序,實得 {dates}"


def test_load_dedup_keeps_same_spec_different_date():
    """#64/#14:dedup 鍵必須含日期 —— 同(ticker, action, qty, price)但不同日,是 3 筆獨立交易
    (例:每月固定日期定期定額買同股數同價位),不是重疊對帳單造成的重複列,不該被去重。
    跟 test_load_dedup_cross_file_overlap 的『跨檔同一筆才去重』互為正反面。"""
    p = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "DCA,10,50.00,BUY,2024-01-01,Trade\n"
        "DCA,10,50.00,BUY,2024-02-01,Trade\n"
        "DCA,10,50.00,BUY,2024-03-01,Trade\n"
    )
    try:
        rows = tr.load([p])
    finally:
        os.unlink(p)
    assert len(rows) == 3, \
        f"同規格不同日的 3 筆定期定額交易應全保留,實得 {len(rows)} 筆(dedup 鍵漏了日期才會誤砍)"
    assert sorted(r["date"].isoformat() for r in rows) == ["2024-01-01", "2024-02-01", "2024-03-01"]


def test_load_stats_surfaces_silent_skips():
    """#50:load() 每個靜默丟棄面都要計數,卡面 meta 才誠實(少算了幾筆看得見,不然跟券商 app 對不上也不知道)。
    混料兩檔:非Trade / 零價 / 解析失敗(檔內)+ 跨檔重疊(dup,#14 後同檔重複不再算 dup)→ 各歸各的桶。"""
    a = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "NVDA,10,100.00,BUY,2024-01-02,Trade\n"        # 收
        "DIV,5,1.00,BUY,2024-02-02,Dividend\n"         # 非 Trade → skip_non_trade
        "FREE,10,0.00,BUY,2024-02-01,Trade\n"          # 零價偽交易 → skip_zero
        "BAD,x,100,BUY,2024-03-01,Trade\n"             # qty 解析失敗 → skip_parse
    )
    b = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "NVDA,10,100.00,BUY,2024-01-02,Trade\n"        # 與 a 跨檔重疊 → skip_dup
    )
    try:
        tr.load([a, b])
    finally:
        os.unlink(a); os.unlink(b)
    s = tr._LOAD_STATS
    assert s["loaded"] == 1, f"只有 1 筆真交易(NVDA,跨檔重疊去 1),實得 {s['loaded']}"
    assert (s["skip_dup"], s["skip_non_trade"], s["skip_zero"], s["skip_parse"]) == (1, 1, 1, 1), \
        f"各跳過桶計數錯:{s}"
    note = tr._load_skip_note()
    for frag in ("重複 1", "非Trade 1", "零值濾除 1", "無法解析 1"):
        assert frag in note, f"meta 跳過提示缺「{frag}」:{note!r}"


def test_load_skip_note_silent_when_clean():
    """全乾淨 CSV → skip note 為空(不無病呻吟,只有真丟資料才吵)。"""
    p = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "NVDA,10,100.00,BUY,2024-01-02,Trade\n"
        "NVDA,10,120.00,SELL,2024-02-02,Trade\n"
    )
    try:
        tr.load([p])
    finally:
        os.unlink(p)
    assert tr._LOAD_STATS["loaded"] == 2
    assert tr._load_skip_note() == "", f"乾淨 CSV 不該有跳過提示,實得 {tr._load_skip_note()!r}"


def test_load_bad_date_counted_not_crash():
    """#50/triad(Codex 反例):壞/缺 TradeDate 不可硬 crash 整份復盤,應歸 skip_parse 桶
    ——一列日期爛掉不連累全 CSV(這正是 load() 誠實化要擋的靜默/爆炸邊界)。"""
    p = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "OK,1,10,BUY,2024-01-01,Trade\n"
        "BADDATE,1,10,BUY,not-a-date,Trade\n"      # 壞日期 → skip_parse(不拋例外)
        "NODATE,1,10,BUY,,Trade\n"                 # 空日期 → skip_parse
    )
    try:
        rows = tr.load([p])                         # 不可拋例外
    finally:
        os.unlink(p)
    assert len(rows) == 1 and tr._LOAD_STATS["loaded"] == 1, f"只有 OK 有效,實得 {tr._LOAD_STATS}"
    assert tr._LOAD_STATS["skip_parse"] == 2, f"壞日期+空日期都歸 skip_parse,實得 {tr._LOAD_STATS['skip_parse']}"


# ─────────────────────── B. round_trips():FIFO 配對 ───────────────────────

def test_round_trips_fifo_partial():
    """FIFO 必須先吃最早的 lot,跨 lot 部分配對,且 ret/hold 各自對應自己的進場 lot。
    改壞配對順序 → 出場紀律(處置缺口)與盈虧比全錯。"""
    rows = [_R("A", "buy", 100, 10, "2024-01-01"),
            _R("A", "buy", 100, 20, "2024-01-10"),
            _R("A", "sell", 150, 30, "2024-02-01")]   # 賣 150 = 吃光 lot1(100) + lot2(50)
    rts, lots = tr.round_trips(rows)
    assert len(rts) == 2, f"跨 lot 應配出 2 筆,實得 {len(rts)}"
    assert rts[0]["buy_px"] == 10 and rts[0]["qty"] == 100, "FIFO:先配最早 @10 那 lot 全量"
    assert _approx(rts[0]["ret"], 2.0), "ret=(30-10)/10=2.0"
    assert rts[0]["hold"] == 31, "hold = 2/1 - 1/1 = 31 天"
    assert rts[1]["buy_px"] == 20 and rts[1]["qty"] == 50, "再配 @20 那 lot 的 50 股"
    assert _approx(rts[1]["ret"], 0.5), "ret=(30-20)/20=0.5"
    assert len(lots["A"]) == 1 and lots["A"][0][0] == 50, "@20 那 lot 應剩 50 股未配"


def test_round_trips_oversell_no_crash():
    """賣超持倉(CSV 缺期初部位)只配得出可配的量,不爆例外、不產生負數 lot。"""
    rows = [_R("B", "buy", 50, 10, "2024-01-01"),
            _R("B", "sell", 80, 12, "2024-01-05")]    # 賣 80 但只有 50
    rts, lots = tr.round_trips(rows)
    assert len(rts) == 1 and _approx(rts[0]["ret"], 0.2), "只配出已持有的 50 股,ret=0.2"
    assert len(lots["B"]) == 0, "lot 應被吃光,不留負數殘量"


# ─────────────────────── C. positions():持倉 + 攤平偵測 ───────────────────────

def test_positions_avgdown_threshold():
    """攤平偵測門檻:買價須 < 均價 * 0.90 才算『有意義攤平』,微幅 DCA(95>90)不算。
    這道門檻是『加碼攤平』維不把常買 dip 的人誤判成凹單的關鍵。"""
    rows = [_R("C", "buy", 100, 100, "2024-01-01"),
            _R("C", "buy", 100, 95, "2024-01-10"),    # 95 > 100*0.9 → 不算攤平
            _R("C", "buy", 100, 80, "2024-01-20")]    # 80 < 0.9*avg → 算攤平
    held, avg_down = tr.positions(rows)
    assert held["C"] == (300.0, 27500.0), f"淨持倉=(300, 27500),實得 {held.get('C')}"
    assert len(avg_down) == 1, f"只有 @80 那筆算攤平,實得 {len(avg_down)} 件"
    assert avg_down[0]["ticker"] == "C" and avg_down[0]["px"] == 80


def test_positions_cleared_not_held():
    """清倉(賣光)後該 ticker 不應留在 held —— 否則 sizing/分散 的分母被幽靈持倉污染。"""
    rows = [_R("D", "buy", 10, 100, "2024-01-01"),
            _R("D", "sell", 10, 120, "2024-02-01")]
    held, _ = tr.positions(rows)
    assert "D" not in held, "清倉後不應在 held"


# ─────────────────────── D. classify_adds():主從分類 ───────────────────────

def test_classify_adds_dca():
    """漲跌都買 + 時間規律 → 疑似定投(不該被冤枉成凹單)。"""
    rows = [_R("DCA", "buy", 10, 100, "2024-01-01"),
            _R("DCA", "buy", 10, 110, "2024-02-01"),
            _R("DCA", "buy", 10, 95, "2024-03-01"),
            _R("DCA", "buy", 10, 120, "2024-04-01"),
            _R("DCA", "buy", 10, 105, "2024-05-01")]
    out = tr.classify_adds(rows).get("DCA")
    assert out and out["cls"] == "疑似定投", f"規律漲跌都買應為定投,實得 {out}"
    assert out["n_adds"] == 4    # 5 筆買入 = 首筆建倉 + 4 筆加碼;n_adds 只計加碼、不含首筆(#41 G1)


def test_classify_adds_averaging_down():
    """只在虧損加碼 + 間隔不規律 + 金額加速 → 疑似凹單。
    (注意:loss_ratio 須嚴格 >0.8 且非規律間隔,故用 6 筆不等距——這正是容易改壞的邊界)"""
    rows = [_R("Z", "buy", 10, 100, "2024-01-01"),    # 建倉(sh=0,不計虧損買)
            _R("Z", "buy", 10, 90, "2024-01-06"),     # gap 5
            _R("Z", "buy", 10, 80, "2024-02-15"),     # gap 40
            _R("Z", "buy", 20, 70, "2024-02-23"),     # gap 8
            _R("Z", "buy", 40, 55, "2024-04-13"),     # gap 50,金額加速
            _R("Z", "buy", 80, 40, "2024-04-23")]     # gap 10,金額加速
    out = tr.classify_adds(rows).get("Z")
    assert out and out["cls"] == "疑似凹單", f"只虧損買+不規律+加速應為凹單,實得 {out}"
    assert out["n_adds"] == 5 and out["loss_ratio"] > 0.8    # 6 筆買入 = 首筆 + 5 筆加碼;n_adds 只計加碼(#41 G1)


def test_classify_adds_below_min():
    """加碼次數 < min_adds(預設 2)→ 樣本太薄,不分類(回傳不含該 ticker)。
    此例:1 筆建倉 + 1 筆加碼 = 1 加碼 < 2(#41 review:gate 改用加碼數,與分類同口徑)。"""
    rows = [_R("X", "buy", 10, 100, "2024-01-01"),
            _R("X", "buy", 10, 90, "2024-02-01")]
    assert tr.classify_adds(rows).get("X") is None, "1 次加碼(<2)不應分類"


# ─────────────────────── E. overview_stats():金額總覽 ───────────────────────

def test_overview_stats_payoff_and_pf():
    """金額層的恆等式:realized = win_sum + loss_sum;盈虧比=平均賺/|平均賠|;
    獲利因子=總賺/|總賠|。卡片總覽直接引用,算錯 → 用戶被餵錯數字。"""
    rts = [_RT("W1", 10, 20), _RT("W2", 10, 15), _RT("L1", 10, 5)]  # +100,+50,-50
    ov = tr.overview_stats(rts, {})
    assert ov["win_sum"] == 150 and ov["loss_sum"] == -50
    assert ov["realized"] == 100, "realized = 150 + (-50)"
    assert _approx(ov["avg_win"], 75.0) and _approx(ov["avg_loss"], -50.0)
    assert _approx(ov["payoff"], 1.5), "payoff = 75 / 50"
    assert _approx(ov["pf"], 3.0), "profit factor = 150 / 50"


# ─────────────────── F. payoff_attribution():盈虧比拆解 ───────────────────

def test_payoff_attribution_carriers_draggers_counterfactual():
    """誰在撐(carriers)、誰在拖(draggers)+ 反事實『拿掉最大拖累後盈虧比』。
    佔比須對總賺/總賠正規化;反事實拿掉後若再無虧損 → payoff=None(∞)而非 0。"""
    rts = [_RT("WIN", 10, 30), _RT("SMALL", 10, 12), _RT("DRAG", 10, 2)]  # +200,+20,-80
    pa = tr.payoff_attribution(rts)
    assert _approx(pa["payoff"], 1.375), f"payoff=avg_win/|avg_loss|=110/80,實得 {pa['payoff']}"
    car = dict((t, round(p, 2)) for t, _w, p in pa["carriers"])
    assert car["WIN"] == 0.91 and car["SMALL"] == 0.09, f"撐盤佔比應正規化到總賺,實得 {car}"
    assert pa["draggers"][0][0] == "DRAG" and _approx(pa["draggers"][0][2], 1.0)
    cf = pa["counterfactual"]
    assert cf["ticker"] == "DRAG" and cf["payoff"] is None, "拿掉唯一拖累後無虧損 → ∞(None),不報 0"


def test_payoff_attribution_all_wins_is_none():
    """全是賺單(無已實現虧損)→ payoff=None(∞),draggers 空、無反事實。
    這是 #18 codex review 的點:不可把『無虧損』錯報成 payoff=0。"""
    pa = tr.payoff_attribution([_RT("A", 10, 20), _RT("B", 10, 15)])
    assert pa["payoff"] is None, "無虧損 → None,不是 0"
    assert pa["draggers"] == [] and pa["counterfactual"] is None


# ─────────────────────── G. alpha 統計閘 v2(#80) ───────────────────────

def test_alpha_credible_stat_gate():
    """v2(#80):credible = 統計顯著(alpha_stat.grade == significant),檔數/集中度閘退役——
    集中的雜訊由回歸 SE 直接量(集中 → 殘差大 → t 低,自然過不了),
    「押賽道 vs 選股」由 excess_split 拆帳正面回答。
    這道閘若被改鬆,引擎會把噪音 α 冒充成『真本事』——紅線沒變,測量方式變了。"""
    assert tr.alpha_credible(dict(note="無價格")) is False, "有 note(無價格/樣本)→ False"
    assert tr.alpha_credible({}) is False, "無 alpha_stat → fail-closed"
    assert tr.alpha_credible(dict(alpha_stat=dict(grade="noise"))) is False
    assert tr.alpha_credible(dict(alpha_stat=dict(grade="suggestive"))) is False
    assert tr.alpha_credible(dict(alpha_stat=dict(grade="significant"))) is True
    assert tr.alpha_credible(dict(note="樣本不足", alpha_stat=dict(grade="significant"))) is False, \
        "note 優先於 grade(fail-closed)"


def test_alpha_grade_thresholds_anchored():
    """分級門檻有統計錨(#63 教訓):n≥252(1 年,業界慣例)+ |t|≥1.96(95%)。
    #80 回歸驗證:分級只看 t 與樣本天數——5 檔集中投組不再被持倉檔數結構性排除。"""
    g = tr._alpha_grade(dict(alpha_t=2.5, n=300))
    assert g["grade"] == "significant" and g["gate"] is None
    g = tr._alpha_grade(dict(alpha_t=-2.4, n=300))
    assert g["grade"] == "significant", "顯著的負 α 也是可談的定論"
    g = tr._alpha_grade(dict(alpha_t=2.5, n=100))
    assert g["grade"] == "suggestive" and g["gate"]["reason"] == "sample_short", \
        "樣本 <1 年:再顯著也不給能力語氣"
    g = tr._alpha_grade(dict(alpha_t=1.3, n=300))
    assert g["grade"] == "suggestive" and g["gate"]["reason"] == "not_significant"
    g = tr._alpha_grade(dict(alpha_t=0.4, n=300))
    assert g["grade"] == "noise" and g["gate"]["reason"] == "not_significant"
    g = tr._alpha_grade(dict(alpha_t=None, n=300))
    assert g["grade"] == "noise", "t 無定義(se=0 完美複製品)→ 保守歸 noise"


# ─────────────────────── H. dim_avgdown():breach 主導觸發 ───────────────────────

def test_dim_avgdown_breach_drives_trigger():
    """B.5 修:觸發由 breach(攤平到破自己 size 上限)主導,純次數高不觸發。
    若退回『次數高就觸發』,常買 dip 的人會被永遠誤排頭號洞。
    #94 修後:breach 判準改吃逐筆 weight_then(加碼當下的成本權重),不再查 size_dim["weights"]
    (那是「今天」市值的單一快照)——這裡的 fixture 直接手造 weight_then 模擬『當下』的佔比。"""
    # 純次數高、每筆加碼當下都是小倉(weight_then 低)、無 breach → 不觸發
    avg_down = [dict(ticker="DCA", date=dt.date(2024, 1, i + 1), px=10, avg=12, weight_then=0.05)
                for i in range(8)]
    held = {"DCA": (100, 1000), "BIG": (100, 50000)}
    size_dim = tr.dim_size([_R("DCA", "buy", 100, 10, "2024-01-01"),
                            _R("BIG", "buy", 100, 500, "2024-01-01")], held, None)
    d = tr.dim_avgdown(avg_down, held, None, size_dim)
    assert d["count"] == 8 and d["breach"] == 0 and d["triggered"] is False, \
        f"純次數高無 breach 不該觸發,實得 {d['count']}/{d['breach']}/{d['triggered']}"
    # 攤平「當下」破 size 上限(weight_then > 0.25)→ 觸發
    avg_down2 = [dict(ticker="BIG", date=dt.date(2024, 1, 1), px=400, avg=500, weight_then=0.30)]
    held2 = {"BIG": (100, 50000), "SMALL": (10, 1000)}
    size2 = tr.dim_size([_R("BIG", "buy", 100, 500, "2024-01-01"),
                         _R("SMALL", "buy", 10, 100, "2024-01-01")], held2, None)
    d2 = tr.dim_avgdown(avg_down2, held2, None, size2)
    assert d2["breach"] == 1 and d2["triggered"] is True, "攤平當下破倉 → breach 觸發"


def test_dim_avgdown_uses_weight_then_not_todays_snapshot():
    """#94 回歸:同一檔『今天』佔比很高,但這筆加碼『當下』佔比不高 → 不該 breach。
    真實案例(sample_value.csv,issue #94):INTC 第一次加碼當下只佔 ~12.5%(成本基礎)不該破線,
    但 INTC 今天(被後續大量加碼推高後)的市值/成本權重遠超 25%——舊邏輯查 size_dim["weights"]
    (今天快照)會誤判這筆『當下不算重倉』的加碼為 breach。同時驗證反向對照:換一檔今天權重不同
    (CVS)的加碼事件,只要它自己的 weight_then 本身破 25%,一樣正確 breach——不是改吃 weight_then
    後就矯枉過正變成『永遠不觸發』(triad review 2026-07-05:CVS 今天成本權重其實也 >25%,並非
    「今天佔比低」,這裡驗證的重點是判準只看 weight_then、不管今天佔比高或低)。"""
    # INTC 今天(size_dim)佔比刻意做到遠超 25%,但這筆加碼「當下」(weight_then)只有 12.5%
    avg_down = [dict(ticker="INTC", date=dt.date(2024, 4, 1), px=40, avg=49, weight_then=0.125)]
    held = {"INTC": (450, 12460.5), "CVS": (120, 7479.6)}   # 精確對齊下面 buy rows 的 qty*price,INTC 今天成本權重 ≈ 62.5%,遠超 25%
    size_dim = tr.dim_size([_R("INTC", "buy", 450, 27.69, "2024-01-01"),
                            _R("CVS", "buy", 120, 62.33, "2024-01-01")], held, None)
    assert size_dim["weights"]["INTC"] > 0.25, "前提:INTC 今天快照本身確實 > 25%(否則此測試無意義)"
    d = tr.dim_avgdown(avg_down, held, None, size_dim)
    assert d["breach"] == 0 and d["triggered"] is False, \
        f"加碼當下 weight_then=12.5%(<25%)不該因『今天』佔比高被誤判 breach,實得 breach={d['breach']}"
    # 反向對照:當下確實重倉(weight_then > 0.25)的加碼,不管今天佔比是什麼,仍應正確 breach
    avg_down2 = [dict(ticker="CVS", date=dt.date(2024, 5, 1), px=55, avg=67.57, weight_then=0.2623)]
    d2 = tr.dim_avgdown(avg_down2, held, None, size_dim)
    assert d2["breach"] == 1 and d2["triggered"] is True, \
        f"當下確實破 25%(weight_then=26.23%)應正確 breach,實得 breach={d2['breach']}"


def test_positions_computes_weight_then_as_point_in_time_cost_share():
    """#94:直接測 positions() 產生 weight_then 這個計算本身(不是像上面兩個測試手造 weight_then
    餵給 dim_avgdown),鎖住『加碼決定做出之前的成本佔比,且每筆事件現算、不是沿用舊快照』這個語意
    ——這是 #94 真正修的那段程式碼,只測 dim_avgdown 吃現成 weight_then 測不到 positions() 自己
    算錯分子分母或算錯時機的迴歸。"""
    rows = [
        _R("A", "buy", 100, 10, "2024-01-01"),    # A 建倉,成本 1000
        _R("B", "buy", 100, 10, "2024-01-02"),    # B 建倉,成本 1000;此刻 A:B = 50:50
        _R("A", "buy", 100, 8, "2024-01-03"),     # 攤平#1(8 < 10*0.9=9):加碼當下 A 成本 1000,總成本 1000+1000=2000 → weight_then=0.5;之後 A:[200,1800]
        _R("C", "buy", 200, 10, "2024-01-04"),    # C 建倉,成本 2000;此刻 A=1800,B=1000,C=2000,總成本 4800
        _R("A", "buy", 100, 5, "2024-01-05"),     # 攤平#2(5 < 9*0.9=8.1):加碼當下 A 成本仍是 1800(此筆套用前),總成本 4800 → weight_then=1800/4800=0.375
    ]
    held, avg_down = tr.positions(rows)
    a_events = [e for e in avg_down if e["ticker"] == "A"]
    assert len(a_events) == 2, f"應有兩次 A 的攤平事件,實得 {len(a_events)}"
    assert _approx(a_events[0]["weight_then"], 0.5, tol=1e-9), \
        f"攤平#1 當下(套用前)A 佔全部持倉成本應為 1000/2000=0.5,實得 {a_events[0]['weight_then']}"
    assert _approx(a_events[1]["weight_then"], 0.375, tol=1e-9), \
        (f"攤平#2 當下 A 佔全部持倉成本應為 1800/4800=0.375(C 加入後現算,不是沿用攤平#1 的 0.5 快照),"
         f"實得 {a_events[1]['weight_then']}")


# ─────────────────────── H2. dim_size():其餘平均排除最大檔 ───────────────────────

def test_dim_size_avg_pct_excludes_max():
    """「其餘平均」必須排除最大那檔——否則 mean(全部)恆=1/檔數、跟集中度無關,
    還跟卡上「最大佔 X%」自相矛盾(91% + 其餘每檔 25% > 100%)。"""
    held = {"BIG": (100, 10000.0), "S1": (10, 1000.0), "S2": (10, 1000.0)}   # 成本權重 ≈ 0.833 / 0.083 / 0.083
    d = tr.dim_size([], held, None)                                          # last_px=None → 用成本算權重
    assert d["max_ticker"] == "BIG"
    assert abs(d["avg_pct"] - 1000.0 / 12000.0) < 1e-6, \
        f"其餘平均應=排除最大檔後的平均≈0.083(舊碼 mean(全部)=1/3≈0.333),實得 {d['avg_pct']}"


# ─────────────────────── I. build_state():薄狀態 + 誠實鐵律 ───────────────────────

def _state_from(rows, ab):
    """組出 build_state 需要的 dims/overview/rx(離線、成本基礎)。"""
    rts, _ = tr.round_trips(rows)
    held, avg_down = tr.positions(rows)
    d_size = tr.dim_size(rows, held, None)
    dims = [tr.dim_exit(rts, None), d_size, tr.dim_diversify(held, None),
            tr.dim_hold(rts), tr.dim_avgdown(avg_down, held, None, d_size)]
    ov = tr.overview_stats(rts, ab, held, None)
    rx = tr.prescribe(ab, dims, ov)
    return tr.build_state(rows, rts, held, dims, ov, ab, rx)


def test_build_state_alpha_always_reported_with_uncertainty():
    """誠實鐵律 v2(#80):α 永遠出數 + 同存 alpha_t(數字+不確定性,比封殺更誠實);
    能力語氣由 alpha_credible 管。被改回「不 credible 就 None」= 回到檔數封殺時代。"""
    rows = tr.load([os.path.join(MOCK, "mock_trades.csv")])
    ab = dict(dim="alpha/beta", beta=1.5, alpha_ann=0.25, credible=False, n=300,
              alpha_stat=dict(alpha_ann=0.25, t=0.8, grade="noise"))
    st = _state_from(rows, ab)
    assert st["schema_version"] == 2, "schema_version 應為 2"
    assert _approx(st["metrics"]["beta"], 1.5), "β 照常入狀態"
    assert _approx(st["metrics"]["alpha_ann"], 0.25), "v2:不 credible 也出數(語氣另管)"
    assert _approx(st["metrics"]["alpha_t"], 0.8), "不確定性(t)一起入狀態,對帳才知道數字多可信"
    assert st["metrics"]["alpha_credible"] is False
    assert st["holdings"]["is_complete"] is False, "CSV 推算不宣稱完整持倉"


def test_build_state_insufficient_sample_no_commitment():
    """§4.4 樣本不足(round-trip < 3)→ 不硬出 commitment,標 insufficient_data。
    防止『只有一兩筆交易』就煞有介事地給承諾/追蹤錨點。"""
    tiny = [_R("A", "buy", 10, 100, "2024-01-01"),
            _R("A", "sell", 10, 120, "2024-02-01")]   # 1 個 round-trip
    st = _state_from(tiny, dict(note="無價格"))
    assert st["insufficient_data"] is True, "rt<3 應標 insufficient_data"
    assert st["commitment"] is None, "樣本不足不該出 commitment"


# ─────────────────────── J. prescribe():#29 能產 ≥2 候選規矩 ───────────────────────

def test_prescribe_multiple_candidate_rules():
    """#29:攤平(breach≥1)與單筆過重(max_pct>0.30)同時觸發時,prescribe 應給 ≥2 條帶 rule 的候選。
    這釘住『解開互斥 gate』——若退回 sizing 被 `not any(kind=='砍損耗')` 擋掉,只剩 1 條,
    candidate_rules 的『2-3 條候選』又會變成兌現不了的死承諾(原 review finding pr23-f1)。"""
    dims = [
        dict(dim="加碼攤平", count=12, breach=2),                  # 觸發攤平 rule
        dict(dim="部位 sizing", max_pct=0.55, max_ticker="NVDA"),  # 觸發 sizing rule
    ]
    rx = tr.prescribe(None, dims, {})        # ab=None → 跳過 alpha/beta 段,只看處方互斥
    rules = [r for r in rx if r.get("rule")]
    assert len(rules) >= 2, f"#29:兩條都觸發應給 ≥2 條候選 rule,實得 {len(rules)}: {[r.get('kind') for r in rx]}"


def test_prescribe_rx_entries_tagged_with_dim():
    """#87/#95:prescribe() 產出的「加碼攤平」「部位 sizing」兩條 rx 必須帶 dim= 標籤,
    build_card_data() 的 candidate_rules 補滿邏輯靠這個做 dedup(見下面 K 節)。"""
    dims = [
        dict(dim="加碼攤平", count=12, breach=2),
        dict(dim="部位 sizing", max_pct=0.55, max_ticker="NVDA"),
    ]
    rx = tr.prescribe(None, dims, {})
    tagged = {r["dim"]: r for r in rx if r.get("rule")}
    assert tagged.keys() == {"加碼攤平", "部位 sizing"}, f"兩條 rule 應各自帶對應 dim,實得 {list(tagged.keys())}"


# ─────────────────── K. build_card_data():candidate_rules 從 top_holes 補滿(#87/#95) ───────────────────

def _card_from(dims, rx):
    """組出 build_card_data 需要的最小參數(其餘用不到的欄位餵 None/{})。"""
    return tr.build_card_data(dims, None, {}, None, None, None, rx, [], None, None, None)


def test_candidate_rules_backfilled_from_top_holes_when_prescribe_empty():
    """#87/#95 Bug A:出場紀律/分散/持有時間三維在 prescribe() 完全沒有 rule 生成路徑——
    headline 是這三維之一時,candidate_rules 不該是空陣列(否則 SKILL Step 3.5 的記憶迴圈斷鏈)。
    用 top_holes[].lens_rule(headline 優先,因 top_holes 已按 severity 排序)補滿。"""
    dims = [
        dict(dim="持有時間", tier=2, triggered=True, severity=1.0,
             min=1, max=45, median_hold=8.0, n_incon=2, n_multi=5, incon_tickers=["AAA", "BBB"]),
        dict(dim="出場紀律", tier=1, triggered=True, severity=0.69,
             early_rate=0.5, n_rt=4, n_scored=4, n_trunc=0, avg_forgone=0.05,
             winner_early=0.5, n_fwd=30, hold_win=10, hold_lose=3, disp_gap=7),
    ]
    rx = tr.prescribe(None, dims, {})           # 加碼攤平/部位 sizing 都沒觸發 → rx 應為空
    assert rx == [], f"這兩維都不在 prescribe() 覆蓋範圍,rx 應為空,實得 {rx}"
    card = _card_from(dims, rx)
    assert card["candidate_rules"], "#87/#95:candidate_rules 不該是空陣列——headline 維度應補到規矩"
    got_dims = [r["dim"] for r in card["candidate_rules"]]
    assert got_dims[0] == "持有時間", f"headline(severity 最高)應排第一,實得 {got_dims}"
    assert set(got_dims) == {"持有時間", "出場紀律"}
    for r in card["candidate_rules"]:
        assert r.get("rule"), f"補滿的候選必須帶非空 rule,實得 {r}"


def test_candidate_rules_dedup_skips_dim_already_covered_by_rx():
    """#87/#95:若某 dim 已經被 prescribe() 的 rx 貢獻了一條 rule(例如它剛好也是 headline),
    backfill 不該再從 lens_rule 幫同一個 dim 補第二條——否則同一維度出現兩條用詞不同的規矩,
    使用者在 Step 3.5 選規矩時會困惑『到底哪條才是engine真正要的』。"""
    dims = [
        dict(dim="部位 sizing", tier=1, triggered=True, severity=1.0,
             max_pct=0.55, max_ticker="NVDA", avg_pct=0.10),   # headline,且 prescribe() 會給它一條 rule
        dict(dim="分散", tier=2, triggered=True, severity=0.9,
             n=6, ai_pct=0.1, max_sector="半導體", max_sector_pct=0.5, top3=0.7),  # 沒有 rule 生成路徑
    ]
    rx = tr.prescribe(None, dims, {})
    rule_dims = {r["dim"] for r in rx if r.get("rule")}
    assert rule_dims == {"部位 sizing"}, f"只有部位 sizing 觸發 rule,實得 {rule_dims}"
    card = _card_from(dims, rx)
    got_dims = [r["dim"] for r in card["candidate_rules"]]
    assert got_dims.count("部位 sizing") == 1, f"部位 sizing 已被 rx 涵蓋,不該再被 lens_rule 重複補一次,實得 {got_dims}"
    assert "分散" in got_dims, "分散沒有 rule 生成路徑,應由 lens_rule 補上"
    assert len(card["candidate_rules"]) == 2, f"應為『rx 一條 + backfill 一條』共 2 條,實得 {len(card['candidate_rules'])}"


# ─────────────────── L. dim_diversify():未分類桶不冒充集中度(#87/#95 Bug B) ───────────────────

def test_dim_diversify_excludes_unclassified_from_severity():
    """#87/#95 Bug B:全部持倉都落進 driver() 的 fallback 桶「未分類」時,
    這個桶不是真的『同產業集中』,是資料品質缺口(driver_map 沒建好)——
    severity 不該被這個假訊號拉到 1.0(那是跟 data_integrity.unclassified_drivers 打對台的謊報)。"""
    held = {t: (10, 1000.0) for t in ["CVX", "DUK", "HON", "JNJ", "JPM", "PG", "SO"]}  # 全部沒進 driver_map
    d = tr.dim_diversify(held, None)
    assert d["max_sector"] is None, f"沒有任何已分類 sector 時 max_sector 應為 None,實得 {d['max_sector']}"
    assert d["max_sector_pct"] == 0, f"排除未分類後應無真實 sector 集中訊號,實得 {d['max_sector_pct']}"
    assert d["severity"] < 0.1, f"未分類桶不該冒充成高嚴重度,實得 severity={d['severity']}"
    assert _approx(d["sectors"]["未分類"], 1.0, tol=1e-6), \
        f"sectors 原始分布仍應如實記錄未分類佔比(供 data_integrity 揭露用),實得 {d['sectors']['未分類']}"


def test_dim_diversify_triggered_severity_thresholds_aligned():
    """#87/#95 Bug B:triggered 的 sector 門檻(SECTOR_MAX_TH)必須跟 severity 的 40% 起算點對齊,
    否則會出現『severity 拉滿但 triggered=False』或反過來的自相矛盾組合。
    邊界案例:max_sec_pct 卡在 0.40~0.50 之間時,兩者理應一致觸發(對齊後),不該一個算集中一個算不集中。"""
    assert tr.SECTOR_MAX_TH == 0.40, f"SECTOR_MAX_TH 應與 severity 40% 起算點對齊,實得 {tr.SECTOR_MAX_TH}"
    # 8 檔(過 len(w)>=8 閘門),同一 sector 佔 45%(卡在新舊門檻之間:>0.40 但 <0.50)
    held = {"S1": (10, 4500.0)}
    for i in range(7):
        held[f"O{i}"] = (10, (10000.0 - 4500.0) / 7)
    orig_map = tr._DRIVER_MAP                              # 存原始參照(而非重設成固定值)才能真正物歸原位
    try:
        tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)
        tr._DRIVER_MAP["S1"] = ("半導體", 0)
        for i in range(7):
            tr._DRIVER_MAP[f"O{i}"] = (f"產業{i}", 0)      # 其餘 7 檔各自不同 sector,避免湊出另一個大桶
        d = tr.dim_diversify(held, None)
    finally:
        tr._DRIVER_MAP = orig_map                          # 還原成呼叫前的原始參照,別讓別的測試(若曾 load_driver_map)被這裡悄悄清空
    assert abs(d["max_sector_pct"] - 0.45) < 1e-6
    assert d["triggered"] is True, f"45% 過新門檻(40%)應觸發,實得 triggered={d['triggered']}"
    assert d["severity"] > 0, f"45% 也應貢獻正的 severity(同一套 40% 起算點),實得 severity={d['severity']}"


# ─────────────────── H. 多市場幣別(#51/#129 PR-2a)───────────────────

def test_load_currency_columns_defaults_and_normalization():
    p = _write_csv(
        "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
        "2330.TW,BUY,100,900,2025-01-06,Trade,TW,twd\n"     # 小寫 → 正規化 TWD
        "NVDA,BUY,10,100,2025-01-06,Trade,,\n")             # 缺 → US/USD(向後相容)
    rows = tr.load([p])
    tw = next(r for r in rows if r["ticker"] == "2330.TW")
    us = next(r for r in rows if r["ticker"] == "NVDA")
    assert tw["market"] == "TW" and tw["currency"] == "TWD"
    assert us["market"] == "US" and us["currency"] == "USD"


def test_currency_map_detects_mixed_and_conflicts():
    rows = [_R("NVDA", "buy", 1, 10, "2025-01-06"), _R("2330.TW", "buy", 1, 900, "2025-01-06"),
            _R("2330.TW", "sell", 1, 950, "2025-02-03")]
    rows[1]["currency"] = "TWD"
    rows[2]["currency"] = "USD"                              # 同一檔兩種幣別 = 輸入錯
    cur_map, currencies, conflicts = tr.currency_map(rows)
    assert conflicts == ["2330.TW"], f"同檔多幣別要進 conflicts,得 {conflicts}"
    assert cur_map["2330.TW"] == "USD", "衝突取最後一筆"
    assert cur_map["NVDA"] == "USD", "未帶 currency 的 row 預設 USD"
    assert currencies == ["USD"], "currencies = 最終 per-ticker 值域(衝突已另由 conflicts 警告)"
    rows[1]["currency"] = "TWD"; rows[2]["currency"] = "TWD"   # 真混幣:一檔 TWD 一檔 USD
    _, currencies2, conflicts2 = tr.currency_map(rows)
    assert currencies2 == ["TWD", "USD"] and conflicts2 == []


def test_usd_view_fixes_mixed_currency_weights():
    """#51 核心:台股 90 萬(原幣)混美股 4 千(USD)——無換算時台股假性壓垮 sizing,
    換算後權重才是真的。ret(比率)必須不受等比縮放影響。"""
    held = {"2330.TW": (1000, 900_000.0), "AAPL": (20, 4_000.0)}
    cur_map = {"2330.TW": "TWD", "AAPL": "USD"}
    fx = {"USD": 1.0, "TWD": 1.0 / 30.0}
    _, held_u, lastpx_u = tr.usd_view([], held, {}, cur_map, fx)
    d = tr.dim_size([], held_u, lastpx_u)
    want = 30_000.0 / 34_000.0
    assert abs(d["max_pct"] - want) < 1e-9, f"換算後 2330 權重應 {want:.4f},得 {d['max_pct']:.4f}"
    d_raw = tr.dim_size([], held, {})                        # 對照:不換算 = 99.6% 假集中
    assert d_raw["max_pct"] > 0.99


def test_usd_view_ret_and_holdings_shape_invariant():
    rts = [_RT("2330.TW", 900.0, 990.0, qty=10)]             # ret = +10%
    rts_u, held_u, lastpx_u = tr.usd_view(rts, {"2330.TW": (5, 4500.0)}, {"2330.TW": 990.0},
                                          {"2330.TW": "TWD"}, {"USD": 1.0, "TWD": 1.0 / 30.0})
    r = rts_u[0]
    assert _approx(r["buy_px"], 30.0) and _approx(r["sell_px"], 33.0)
    assert _approx((r["sell_px"] - r["buy_px"]) / r["buy_px"], 0.10), "ret 等比不變"
    assert _approx(held_u["2330.TW"][1], 150.0) and _approx(lastpx_u["2330.TW"], 33.0)
    assert rts[0]["buy_px"] == 900.0, "原物件不可被改(per-ticker 呈現要原幣)"


def test_usd_view_missing_fx_factor_is_one():
    _, held_u, _ = tr.usd_view([], {"2330.TW": (1, 900.0)}, {}, {"2330.TW": "TWD"}, {"USD": 1.0})
    assert _approx(held_u["2330.TW"][1], 900.0), "fx 缺 → 因子 1.0 近似(警告由 data_integrity 載)"


def test_fetch_fx_usd_only_is_offline_noop():
    fx, err = tr.fetch_fx(["USD", "USD"])
    assert fx == {"USD": 1.0} and err is None, "純 USD 不碰網路、不報錯"


def test_ticker_diagnosis_ranks_on_aggregate_currency():
    """混幣時標的層排序/佔比要在 USD 視圖上做:2330 名目 +90k(TWD)其實只值 3k USD,
    不准霸榜壓過 NVDA 的 +5k USD(review 2026-07-06 抓的聚合遺漏)。"""
    rts = [_RT("2330.TW", 900.0, 1000.0, qty=900),           # +90,000 TWD = +3,000 USD
           _RT("NVDA", 100.0, 150.0, qty=100)]               # +5,000 USD
    cur_map = {"2330.TW": "TWD", "NVDA": "USD"}
    fx = {"USD": 1.0, "TWD": 1.0 / 30.0}
    rts_u, held_u, lastpx_u = tr.usd_view(rts, {}, {}, cur_map, fx)
    out = tr.ticker_diagnosis(rts_u, {}, held_u, lastpx_u)
    assert out[0]["ticker"] == "NVDA", f"USD 視圖下 NVDA(+5k)應排第一,得 {out[0]['ticker']}"
    raw = tr.ticker_diagnosis(rts, {}, {}, {})               # 對照:原幣名目讓 2330 假性第一
    assert raw[0]["ticker"] == "2330.TW"


def test_pnl_by_currency_buckets():
    rts = [_RT("2330.TW", 900.0, 990.0, qty=10), _RT("NVDA", 100.0, 150.0, qty=10)]
    held = {"AAPL": (20, 4_000.0), "NOPX": (5, 100.0)}       # NOPX 無現價 → 不入未實現
    b = tr.pnl_by_currency(rts, held, {"AAPL": 210.0},
                           {"2330.TW": "TWD", "NVDA": "USD", "AAPL": "USD", "NOPX": "USD"})
    assert _approx(b["TWD"]["realized"], 900.0)              # 10×(990-900)
    assert _approx(b["USD"]["realized"], 500.0)
    assert _approx(b["USD"]["unrealized"], 200.0)            # 20×210 − 4000;NOPX 缺價不硬編


# ─────────────── horizon 時間軸矛盾(#148 item5:SKILL prose 閾值下沉,horizon.py)───────────────

def test_horizon_exit_side_thresholds():
    """清倉太快:年<90 / 季<21 觸 exit_too_fast;邊界(恰 90/21)不觸;週快清=正常。"""
    assert hz.horizon_contradiction("年", 89, exited=True) == "exit_too_fast"
    assert hz.horizon_contradiction("年", 90, exited=True) is None       # 邊界:strict <
    assert hz.horizon_contradiction("季", 20, exited=True) == "exit_too_fast"
    assert hz.horizon_contradiction("季", 21, exited=True) is None
    assert hz.horizon_contradiction("週", 3, exited=True) is None        # 週快清=正常,不標


def test_horizon_held_side_thresholds():
    """抱太久:週>60 / 季>180 觸 held_too_long;邊界(恰 60/180)不觸;年長抱=正常。"""
    assert hz.horizon_contradiction("週", 61, exited=False) == "held_too_long"
    assert hz.horizon_contradiction("週", 60, exited=False) is None      # 邊界:strict >
    assert hz.horizon_contradiction("季", 181, exited=False) == "held_too_long"
    assert hz.horizon_contradiction("季", 180, exited=False) is None
    assert hz.horizon_contradiction("年", 999, exited=False) is None     # 年長抱=正常,不標


def test_horizon_null_and_bad_horizon_skip():
    """horizon 缺欄 / null / 非三值 → 一律 None(靜默跳過,不回補)。"""
    assert hz.horizon_contradiction(None, 5, exited=True) is None
    assert hz.horizon_contradiction("", 5, exited=True) is None
    assert hz.horizon_contradiction("month", 999, exited=False) is None


def test_horizon_scan_computes_days_from_cycle_id():
    """scan 從 cycle_id 內嵌日算 holding_days,exit_date 有無決定 exited 路徑。"""
    theses = [
        {"cycle_id": "NVDA#2026-06-01#1", "horizon": "年", "ticker": "NVDA",
         "exit_date": "2026-07-01", "maturity": "inferred"},          # 清倉 30 天<90 → 太快
        {"cycle_id": "MSFT#2025-06-01#1", "horizon": "季", "ticker": "MSFT"},  # 續抱 >180 → 太久
    ]
    m = hz.scan(theses, as_of="2026-07-01")
    by = {x["cycle_id"]: x for x in m}
    assert by["NVDA#2026-06-01#1"]["kind"] == "exit_too_fast"
    assert by["NVDA#2026-06-01#1"]["holding_days"] == 30 and by["NVDA#2026-06-01#1"]["exited"]
    assert by["MSFT#2025-06-01#1"]["kind"] == "held_too_long"          # 2025-06-01→2026-07-01 ≈395d>180
    assert not by["MSFT#2025-06-01#1"]["exited"]


def test_horizon_scan_skips_unknown_and_missing():
    """#unknown / 無 horizon / 非三值 horizon → 不進 markers(無從算或不判)。"""
    theses = [
        {"cycle_id": "AMD#unknown", "horizon": "年", "ticker": "AMD"},         # 無日期
        {"cycle_id": "F#2026-06-01#1", "ticker": "F"},                        # 無 horizon
        {"cycle_id": "T#2026-06-25#1", "horizon": "週", "exit_date": "2026-07-01"},  # 週清倉=正常
    ]
    assert hz.scan(theses, as_of="2026-07-01") == []


# ─────────────────── 標準庫 runner(免 pytest 即可跑,與 test_sample_styles 一致)───────────────────

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = skipped = 0
    for name, fn in tests:
        try:
            if fn() == _SKIP:
                skipped += 1
                print(f"SKIP  {name}")
            else:
                passed += 1
                print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _main() else 0)
