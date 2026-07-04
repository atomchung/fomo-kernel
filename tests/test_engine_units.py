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

def test_load_dedup_and_filters():
    """load() 是整條管線的地基,任一濾規則改壞 → 所有上層 metric 連帶歪掉:
    重疊期去重 / sell qty 取絕對值 + Action 大小寫不敏感 / price<=0 偽交易濾除 /
    非 Trade 列濾除。"""
    p = _write_csv(
        "Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
        "NVDA,150,50.00,BUY,2024-01-12,Trade\n"
        "NVDA,150,50.00,BUY,2024-01-12,Trade\n"      # 完全相同 → 去重
        "AMD,-50,160.00,sell,2024-01-22,Trade\n"     # 小寫 sell + 負數量
        "FREE,10,0.00,BUY,2024-02-01,Trade\n"        # price=0 split/free-share → 濾掉
        "DIV,5,1.00,BUY,2024-02-02,Dividend\n"       # 非 Trade → 濾掉
    )
    try:
        rows = tr.load([p])
    finally:
        os.unlink(p)
    assert len(rows) == 2, f"去重+濾偽交易後應剩 2 筆,實得 {len(rows)}"
    nvda = [r for r in rows if r["ticker"] == "NVDA"]
    amd = [r for r in rows if r["ticker"] == "AMD"]
    assert len(nvda) == 1, "完全相同的 NVDA 交易應被去重成 1 筆"
    assert amd and amd[0]["side"] == "sell" and amd[0]["qty"] == 50.0, "sell 大小寫不敏感 + qty 取絕對值"
    assert all(r["ticker"] not in ("FREE", "DIV") for r in rows), "price=0 / 非 Trade 應被濾除"


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
    dates = [r["date"].isoformat() for r in rows]
    assert dates == sorted(dates), f"load 應按日期排序,實得 {dates}"


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
    若退回『次數高就觸發』,常買 dip 的人會被永遠誤排頭號洞。"""
    # 純次數高、小倉、無 breach → 不觸發
    avg_down = [dict(ticker="DCA", date=dt.date(2024, 1, i + 1), px=10, avg=12) for i in range(8)]
    held = {"DCA": (100, 1000), "BIG": (100, 50000)}
    size_dim = tr.dim_size([_R("DCA", "buy", 100, 10, "2024-01-01"),
                            _R("BIG", "buy", 100, 500, "2024-01-01")], held, None)
    d = tr.dim_avgdown(avg_down, held, None, size_dim)
    assert d["count"] == 8 and d["breach"] == 0 and d["triggered"] is False, \
        f"純次數高無 breach 不該觸發,實得 {d['count']}/{d['breach']}/{d['triggered']}"
    # 攤平破 size 上限 → 觸發
    avg_down2 = [dict(ticker="BIG", date=dt.date(2024, 1, 1), px=400, avg=500)]
    held2 = {"BIG": (100, 50000), "SMALL": (10, 1000)}
    size2 = tr.dim_size([_R("BIG", "buy", 100, 500, "2024-01-01"),
                         _R("SMALL", "buy", 10, 100, "2024-01-01")], held2, None)
    d2 = tr.dim_avgdown(avg_down2, held2, None, size2)
    assert d2["breach"] == 1 and d2["triggered"] is True, "攤平破倉 → breach 觸發"


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
