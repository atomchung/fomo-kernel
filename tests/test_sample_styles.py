#!/usr/bin/env python3
"""
回歸測試:三種交易風格 sample(mock/sample_*.csv)→ engine 應排出的「頭號洞」。

設計原則 = 對股價漂移容錯(engine 會抓 yfinance 即時價,精確數字明天就變,不能斷言精確值):
- 主測試(deterministic,離線):只斷言與「最新股價」無關的核心 —— 頭號洞排序、攤平/破倉次數、
  處置缺口(純日期)、AI driver 旗標(來自 driver_map)。權重一律用『成本基礎』(last_px=None)算,
  完全不碰 yfinance → 離線、快、永遠不會因為今天股價漲跌而 flaky。
- 選配 smoke(network):TR_TEST_NETWORK=1 才跑,用真實 yfinance 驗 β『方向』(動能高 / 基本面低),
  抓不到價就 skip,不當紅燈 —— 這就是「股價相關的只斷言方向、不斷言精確值」。

跑法:
  python3 tests/test_sample_styles.py                       # 主測試(離線、確定性)
  TR_TEST_NETWORK=1 python3 tests/test_sample_styles.py     # 加跑 network smoke
  pytest tests/test_sample_styles.py                        # 若已裝 pytest 亦可被發現
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "skills", "fomo-kernel")
MOCK = os.path.join(SKILL, "mock")
sys.path.insert(0, os.path.join(SKILL, "engine"))
import trade_recap as tr  # noqa: E402

_SKIP = "__skip__"               # 標準庫 runner 用的 skip 哨兵(pytest 下會被當 pass)


def _dims(style):
    """讀 CSV + driver_map,用『成本基礎』(last_px=None,不碰 yfinance)算 5 維 → 確定性結果。"""
    tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)            # 每次重置,避免風格間 driver 污染
    tr.load_driver_map(os.path.join(MOCK, f"sample_{style}.driver_map.json"))
    rows = tr.load([os.path.join(MOCK, f"sample_{style}.csv")])
    rts, _ = tr.round_trips(rows)
    held, avg_down = tr.positions(rows)
    d_size = tr.dim_size(rows, held, None)              # last_px=None → 成本基礎權重
    d_exit = tr.dim_exit(rts, None)                     # 無 fwd → 只算日期型(處置缺口)
    d_div = tr.dim_diversify(held, None)
    d_hold = tr.dim_hold(rts)
    d_avg = tr.dim_avgdown(avg_down, held, None, d_size)
    return dict(rows=rows, rts=rts, dims=[d_exit, d_size, d_div, d_hold, d_avg],
                exit=d_exit, size=d_size, div=d_div, hold=d_hold, avg=d_avg)


def _top_hole(dims):
    """#63:直接呼叫引擎的**單一事實源** _pick_headline(不再自帶 tier 權重/選卡副本)——
    這樣斷言的是引擎真正的選卡,engine 的 tier 權重翻轉、severity 錨動都會讓這裡紅。"""
    h = tr._pick_headline(dims)
    return h["dim"] if h else None


def test_headline_tier_weight_ordering():
    """#63 合成排序單元:鎖住 tier 權重語意(不靠特定 persona 的資料湊巧)。
    tier1 sev 0.5 → 0.5×1.0=0.50 壓過 tier2 sev 0.6 → 0.6×0.7=0.42;M1 突變(權重翻成
    {1:0.7,2:1.0})→ tier2 勝(0.60 vs 0.35)→ 這條紅。反向再驗排序真的在算、不是永遠 tier1。"""
    t1_low = [{"dim": "sizing", "tier": 1, "triggered": True, "severity": 0.5},
              {"dim": "分散", "tier": 2, "triggered": True, "severity": 0.6}]
    assert tr._pick_headline(t1_low)["dim"] == "sizing", "tier1 sev0.5(0.50)應壓過 tier2 sev0.6(0.42)"
    t2_high = [{"dim": "sizing", "tier": 1, "triggered": True, "severity": 0.5},
               {"dim": "分散", "tier": 2, "triggered": True, "severity": 0.8}]
    assert tr._pick_headline(t2_high)["dim"] == "分散", "tier2 sev0.8(0.56)應壓過 tier1 sev0.5(0.50)"
    assert tr._pick_headline([{"dim": "x", "tier": 1, "triggered": False, "severity": 1.0}]) is None, \
        "無 triggered → 無頭號洞(不能拿沒觸發的維當洞)"


# ─────────────────────────── 主測試(離線、確定性)───────────────────────────

def test_fundamental_top_hole_is_exit_discipline():
    """基本面選股 → 頭號洞 = 出場紀律(處置效應:賺錢早走、賠錢死抱)。"""
    s = _dims("fundamental")
    assert _top_hole(s["dims"]) == "出場紀律"
    # 處置效應:賠錢的抱得比賺錢的久很多(純日期,與股價無關)
    assert s["exit"]["disp_gap"] > 20
    assert s["exit"]["hold_lose"] > s["exit"]["hold_win"]
    # 真分散 + 不梭哈 + 不攤平(成本基礎 / CSV,與股價無關)
    assert s["div"]["ai_pct"] == 0
    assert s["size"]["max_pct"] < 0.25
    assert s["avg"]["count"] == 0


def test_momentum_top_hole_is_sizing():
    """動能衝衝衝 → 頭號洞 = 梭哈(部位 sizing)。
    #63:釘死精確值(不再『sizing 或 分散』二選一)——sizing sev=1.0×tier1 壓過 分散 sev×tier2,
    這個排序結果就是產品核心決策,tier 權重被翻轉必須讓這裡紅(M1 突變:momentum 會變『分散』)。"""
    s = _dims("momentum")
    assert _top_hole(s["dims"]) == "部位 sizing"
    # #63 severity 錨:sizing severity 錨在滿格附近(M7 突變把除數 0.30→3.0 → sev 1.0→0.14,這條會紅)
    assert s["size"]["severity"] >= 0.9, f"梭哈 severity 應接近滿格,實得 {s['size']['severity']}"
    # 假分散:100% 同一 driver(thematic 旗標,與股價無關)
    assert s["div"]["ai_pct"] == 1.0
    assert s["div"]["triggered"]
    # 梭哈:單檔過重
    assert s["size"]["triggered"] and s["size"]["max_pct"] > 0.25
    # 短進短出
    assert s["hold"]["median_hold"] < 15


def test_value_top_hole_is_averaging_down():
    """只買便宜估值 → 頭號洞 = 加碼攤平(凹單),且凹單把單檔養成重倉。"""
    s = _dims("value")
    assert _top_hole(s["dims"]) == "加碼攤平"
    # 凹單:虧損加碼多次 + 破自己部位上限(CSV 成交價 / 成本基礎,與最新股價無關)
    assert s["avg"]["count"] >= 5
    assert s["avg"]["breach"] >= 1
    assert s["avg"]["triggered"]
    assert "INTC" in s["avg"]["tickers"]
    # 凹單導致部位失控
    assert s["size"]["max_pct"] > 0.25


def test_three_styles_have_distinct_top_holes():
    """三種風格的頭號洞應彼此不同 —— 證明 sample 真的把風格區分開了。"""
    holes = {st: _top_hole(_dims(st)["dims"])
             for st in ("fundamental", "momentum", "value")}
    assert holes["fundamental"] == "出場紀律"
    assert holes["value"] == "加碼攤平"
    assert holes["fundamental"] != holes["value"] != holes["momentum"]


# ───────────── 投資者畫像 fixture(風格 × 持有長度,2026-06-30 擴充)─────────────
# 設計與三方 review 見 mock/SAMPLES.md。每型頭號洞都由「與最新股價無關」的訊號決定
# (driver flag / 成本權重 / 純日期)→ 離線確定性。

def test_ai_holder_top_hole_is_diversify():
    """AI 長期投資者 → 頭號洞 = 分散(假分散:整副身家押同一 AI 敘事)。"""
    s = _dims("ai_holder")
    assert _top_hole(s["dims"]) == "分散"
    assert s["div"]["ai_pct"] == 1.0 and s["div"]["triggered"]   # AI 暴險 100%
    # 龍頭重倉是「次要洞」:sizing 有 triggered 但分數低於分散(max_pct 控在 0.41 臨界下)
    assert s["size"]["triggered"] and s["size"]["max_pct"] < 0.41
    assert s["size"]["severity"] * tr.HEADLINE_TIER_W[1] < s["div"]["severity"] * tr.HEADLINE_TIER_W[2]
    # 長抱:中位持有遠超短線,且有 >=3 筆已實現 round trip(否則 engine 標 insufficient、不出 commitment)
    assert len(s["rts"]) >= 3
    assert s["hold"]["median_hold"] > 200 and not s["hold"]["triggered"]
    # 漲著加碼 != 攤平
    assert s["avg"]["count"] == 0


def test_oldecon_is_clean_strength_baseline():
    """傳統產業投資者 → 紀律乾淨基準:無任何洞觸發,改走『揚長(strength)』路徑。

    補上既有 fixture 全缺的覆蓋:當組合沒有洞時,卡片該如何呈現。
    """
    s = _dims("oldecon")
    # 五維全綠 → 無頭號洞
    assert _top_hole(s["dims"]) is None
    assert not any(d["triggered"] for d in s["dims"])
    # 老經濟:無 AI 暴險、真分散、不梭哈、不攤平、賺賠持有期相近(無處置缺口)
    assert s["div"]["ai_pct"] == 0
    assert s["size"]["max_pct"] < 0.25
    assert s["avg"]["count"] == 0
    assert abs(s["exit"]["disp_gap"]) < 20
    # 揚長卡必須有東西可講(否則乾淨組合會印空白)
    strengths = tr.dim_strength(s["exit"], s["size"], s["avg"], s["div"], s["hold"], s["rts"])
    assert strengths, "乾淨基準應產出至少一張揚長卡"


def test_swing_top_hole_is_inconsistent_timeframe():
    """快進快出 → 頭號洞 = 持有時間(框架不一致:同檔又當沖又凹單)。"""
    s = _dims("swing")
    assert _top_hole(s["dims"]) == "持有時間"
    # incon:同一檔既有 <5 天也有 >30 天的 round trip
    assert s["hold"]["incon_rate"] > 0.3 and s["hold"]["triggered"]
    assert s["hold"]["min"] < 5 and s["hold"]["max"] > 30
    # 與當沖區隔:median 不是 0(不是純當沖)
    assert s["hold"]["median_hold"] >= 5


def test_day_trader_top_hole_is_overtrading():
    """當沖 → 頭號洞 = 持有時間(過度交易:同日進出,中位持有 0 天)。"""
    s = _dims("day_trader")
    assert _top_hole(s["dims"]) == "持有時間"
    # 同日進出 → 中位持有 0 天(overtrading),且全平倉 → 無持倉
    assert s["hold"]["median_hold"] < 5
    assert s["hold"]["max"] == 0          # 全部同日 round trip
    # 當天全平 → 無持倉 → sizing/分散/攤平 全失效(max_pct=0、無持倉檔數)
    assert s["size"]["max_pct"] == 0 and s["div"]["n"] == 0
    assert not s["size"]["triggered"] and not s["div"]["triggered"]


def test_swing_and_day_trader_distinct_by_mechanism():
    """swing 與 day_trader 同走『持有時間』維,但機制不同 → 可穩定區分。"""
    sw, dy = _dims("swing")["hold"], _dims("day_trader")["hold"]
    # day_trader 靠 overtrading(median 0),swing 靠 incon(median>=5、incon_rate 高)
    assert dy["median_hold"] == 0 and dy["incon_rate"] == 0
    assert sw["median_hold"] >= 5 and sw["incon_rate"] > 0.3


def test_personas_have_distinct_headlines():
    """五型畫像 + 既有 3 組:頭號洞分配應如設計(撞維者靠 sub-signal 區分)。"""
    holes = {st: _top_hole(_dims(st)["dims"]) for st in
             ("ai_holder", "oldecon", "swing", "day_trader", "momentum", "fundamental", "value")}
    assert holes["ai_holder"] == "分散"
    assert holes["oldecon"] is None              # 乾淨基準,無洞
    assert holes["swing"] == "持有時間"
    assert holes["day_trader"] == "持有時間"
    assert holes["momentum"] == "部位 sizing"    # #63:釘死精確值,不再二選一


def test_pyramid_top_hole_is_sizing_not_avgdown():
    """金字塔加碼者(只在浮盈時加碼,非攤平)→ 頭號洞 = 部位 sizing;加碼攤平維必須是 0。

    區隔 sample_value(虧損中加碼):兩者都會把單一標的養成重倉,但成因相反——
    這裡測 engine 不會把『越漲越加碼』誤判成攤平(dim_avgdown 只認買價 < 均價*0.9)。
    """
    s = _dims("pyramid")
    assert _top_hole(s["dims"]) == "部位 sizing"
    assert s["avg"]["count"] == 0 and s["avg"]["breach"] == 0
    assert s["size"]["triggered"] and s["size"]["max_pct"] > 0.25
    assert len(s["rts"]) >= 3   # 足夠樣本,不落 insufficient_data 分支
    # 加碼分類:應為「疑似定投」(漲跌都買/規律)而非「疑似凹單」(只虧損買)
    rows = tr.load([os.path.join(MOCK, "sample_pyramid.csv")])
    adds = tr.classify_adds(rows)
    assert adds and all(v["cls"] != "疑似凹單" for v in adds.values())


def test_insufficient_sample_blocks_commitment():
    """樣本不足者(2 個 round-trip、跨度 <84 天)→ engine 不得硬出 commitment(A-10)。

    對應 eval-design.md A-10:round-trip < 3 或交易跨度 < MIN_SPAN_DAYS → insufficient_data=True、
    commitment 必為 None(除非用戶親選,那是 SKILL 層 Step 3.5 的例外,engine 本身不做這件事)。
    """
    tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)
    tr.load_driver_map(os.path.join(MOCK, "sample_insufficient.driver_map.json"))
    rows = tr.load([os.path.join(MOCK, "sample_insufficient.csv")])
    rts, _ = tr.round_trips(rows)
    span_days = (rows[-1]["date"] - rows[0]["date"]).days
    assert len(rts) < 3
    assert span_days < tr.MIN_SPAN_DAYS
    # 直接跑 build_state 驗證 engine 真的擋下 commitment(不只是靠 gate 條件成立就假設)
    held, avg_down = tr.positions(rows)
    d_size = tr.dim_size(rows, held, None)
    d_exit = tr.dim_exit(rts, None)
    d_div = tr.dim_diversify(held, None)
    d_hold = tr.dim_hold(rts)
    d_avg = tr.dim_avgdown(avg_down, held, None, d_size)
    dims = [d_exit, d_size, d_div, d_hold, d_avg]
    overview = tr.overview_stats(rts, {}, held, None)
    ab = {"credible": False, "note": "樣本不足"}
    rx = tr.prescribe(ab, dims, overview)
    state = tr.build_state(rows, rts, held, dims, overview, ab, rx)
    assert state["insufficient_data"] is True
    assert state["commitment"] is None


def test_noisy_broker_csv_matches_clean_baseline():
    """混入股息/轉帳/利息/再投資等非 Trade 雜訊列 → 解析結果須與乾淨版(oldecon)完全一致。

    測 CSV 健壯性:load() 的 RecordType!='Trade' 與 Action not in (BUY,SELL) 兩道過濾
    要把雜訊列全部濾掉,不能讓雜訊污染任何一維的 severity。
    """
    clean, noisy = _dims("oldecon"), _dims("noisy_broker")
    assert len(clean["rows"]) == len(noisy["rows"])
    assert len(clean["rts"]) == len(noisy["rts"])
    for cd, nd in zip(clean["dims"], noisy["dims"]):
        assert cd["dim"] == nd["dim"]
        assert cd["triggered"] == nd["triggered"]
        assert abs(cd["severity"] - nd["severity"]) < 1e-9


def test_rotator_top_hole_is_sizing_via_theme_churn():
    """輪動追熱點者(依序全倉重壓不同賽道,每次都清倉才換下一個)→ 頭號洞 = 部位 sizing。

    區隔 sample_momentum(全程同一賽道全押):rotator 的每個 round-trip 標的 driver 全部不同
    (沒有重複賽道),而 momentum 是同一 driver 反覆押注——同樣落在『部位 sizing/分散』頭號洞,
    但成因不同,靠 driver 序列的『churn』(全不重複)區分,不是靠頭號洞本身。
    """
    s = _dims("rotator")
    assert _top_hole(s["dims"]) == "部位 sizing"
    assert len(s["rts"]) >= 4
    # 每次持有長度落在「波段」區間(介於 momentum 的 <15 天與 ai_holder 的 >200 天之間)
    assert 20 < s["hold"]["median_hold"] < 60
    assert not s["hold"]["triggered"]           # 每檔只交易一次,無框架不一致
    # 賽道全換過一輪,不重複(驅動因子churn):與 momentum 的『同一 driver 反覆押注』相反
    rows = tr.load([os.path.join(MOCK, "sample_rotator.csv")])
    rts, _ = tr.round_trips(rows)
    sectors = [tr.driver(r["ticker"])[0] for r in rts]
    assert len(set(sectors)) == len(sectors), f"賽道應該逐輪全換不重複,實得 {sectors}"


def test_panic_seller_extreme_disposition_and_chase_back():
    """恐慌全出者(長抱虧損倉到極限,某週同時全數認賠出清,幾個月後又追高買回)→ 頭號洞 = 出場紀律。

    比 sample_fundamental 的處置缺口(+258 天)更極端(+526 天),且多兩個 fundamental 沒有的訊號:
    ① 多檔虧損倉在同一週內同步出清(恐慌,非個股別的紀律賣出);② 賣飛之後追高買回同一檔——
    『賣在恐慌低點、買回追高點』雙重行為錯置。
    """
    s = _dims("panic_seller")
    assert _top_hole(s["dims"]) == "出場紀律"
    assert s["exit"]["disp_gap"] > 300, "處置缺口應遠比 fundamental(+258 天)更極端"
    assert s["exit"]["hold_lose"] > s["exit"]["hold_win"]
    # 恐慌訊號①:多檔虧損倉在極短窗口內同步出清(而不是分散在不同時間點各自出場)
    rows = tr.load([os.path.join(MOCK, "sample_panic_seller.csv")])
    loss_tickers = {"BA", "NKE", "LOW"}
    loss_sells = sorted(r["date"] for r in rows if r["side"] == "sell" and r["ticker"] in loss_tickers)
    assert (loss_sells[-1] - loss_sells[0]).days <= 5, "三檔虧損倉應在同一個恐慌窗口內同步出清"
    # 恐慌訊號②:賣飛之後,同一檔用更高的價格追買回來(追高買回)
    ba_rows = [r for r in rows if r["ticker"] == "BA"]
    ba_sell_px = next(r["price"] for r in ba_rows if r["side"] == "sell")
    ba_rebuy_px = next(r["price"] for r in ba_rows if r["side"] == "buy" and r["date"] > ba_rows[0]["date"])
    assert ba_rebuy_px > ba_sell_px * 1.05, "追高買回應明顯高於恐慌賣出價"


def test_offline_pipeline_no_crash():
    """離線(無 yfinance,last_px=None)時,卡片層全鏈路不得 crash。

    回歸守門:ticker_diagnosis / overview_stats / what_if 都吃 last_px,
    只要 yfinance 沒裝或下載失敗 last_px 就是 None。先前 ticker_diagnosis
    沒 guard None → main() 在最後一步崩潰,而本檔測試只跑 dim_* 沒跑這層,
    所以紅燈被掩蓋。這個測試把『成本基礎(last_px=None)』全鏈路跑一遍。
    """
    s = _dims("momentum")
    rows, rts = s["rows"], s["rts"]
    held, avg_down = tr.positions(rows)
    adds = tr.classify_adds(rows)
    # last_px=None 不得 crash(降級成只用已實現/成本基礎)
    tdiag = tr.ticker_diagnosis(rts, adds, held, None)
    assert isinstance(tdiag, list)
    ov = tr.overview_stats(rts, {}, held, None)
    assert ov["unrealized"] == 0          # 無價格 → 未實現視為 0,不爆
    assert tr.what_if(held, None) is None  # 無價格 → what-if 直接 None,不爆


def test_split_adjustment_dollar_invariant():
    """分割調整:股數×因子、價格÷因子 → 成交金額不變,且跨分割 round-trip 不再假 orphan。

    確定性(無網路):用合成 splits dict 驗算術。NVDA 2024/6 做 10:1,分割前買、分割後賣。
    """
    import datetime as dt
    rows = [dict(ticker="NVDA", side="buy", qty=10, price=1200.0, date=dt.date(2024, 1, 1)),
            dict(ticker="NVDA", side="sell", qty=100, price=120.0, date=dt.date(2024, 7, 1))]
    splits = {"NVDA": [(dt.date(2024, 6, 10), 10.0)]}     # 10:1
    n = tr.adjust_for_splits(rows, splits)
    assert n == 1                                          # 只有分割前那筆被調整
    # 分割前的買:股數 10→100、價 1200→120,金額 12000 不變
    assert abs(rows[0]["qty"] - 100) < 1e-6
    assert abs(rows[0]["price"] - 120.0) < 1e-6
    assert abs(rows[0]["qty"] * rows[0]["price"] - 12000) < 1e-6
    # 分割後的賣:不動
    assert abs(rows[1]["qty"] - 100) < 1e-6 and abs(rows[1]["price"] - 120.0) < 1e-6
    # 調整後:1 個乾淨 round-trip、ret≈0(其實打平)、無假 orphan
    rts, _ = tr.round_trips(rows)
    assert len(rts) == 1 and abs(rts[0]["ret"]) < 1e-6
    assert tr.orphan_sells(rows) == {}


def test_driver_map_partial_tolerance():
    """driver map 逐筆容錯:一筆格式壞不該丟掉整張 map。"""
    import json, tempfile, os as _os
    tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)
    bad = {"AAA": ["核電", 1], "BBB": "壞格式", "CCC": ["能源", 0]}   # BBB 缺 thematic
    fd, p = tempfile.mkstemp(suffix=".json")
    with _os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    ok = tr.load_driver_map(p)
    _os.unlink(p)
    assert ok == 2 and tr._DM_SKIPPED == 1                # 好的 2 筆收下、壞的 1 筆跳過
    assert tr.driver("AAA") == ("核電", 1) and tr.driver("CCC") == ("能源", 0)
    tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)             # 還原,免污染其他測試


def test_entry_style_chase_vs_dip():
    """【風格】維雛形:追高 vs 抄底,用合成價格 fixture 驗(確定性、離線、不碰 yfinance)。

    chase:單調上升、買在新高 → range_pct≈1 → lean=strength。
    dip:先在 150~250 震盪建出區間、後段壓低,買在低檔 → range_pct 低 → lean=weakness。
    """
    import math, datetime as dt
    try:
        import pandas as pd
    except ImportError:
        return _SKIP                                      # 無 pandas → 跳過(這維本來就需要它)
    idx = pd.bdate_range("2023-01-01", periods=300)

    # ── chase:單調上升,後段每兩天買一筆(此時價格史已 >252)──
    rising = pd.Series([100 + i * 0.3 for i in range(300)], index=idx)
    px_up = pd.DataFrame({"AAA": rising})
    buys_up = [dict(ticker="AAA", side="buy", qty=1, price=float(rising.iloc[i]),
                    date=idx[i].date()) for i in range(260, 300, 2)]
    d = tr.dim_entry_style(buys_up, px_up)
    assert d["lean"] == "strength", f"追高應判 strength,實得 {d['lean']}（pct={d['median_pct']:.2f}）"
    assert d["median_pct"] > 0.70

    # ── dip:前段 150~250 震盪(撐出區間),後段壓在 165,買在低檔 ──
    vals = [200 + 50 * math.sin(i / 10.0) if i < 252 else 165.0 for i in range(300)]
    s = pd.Series(vals, index=idx)
    px_dn = pd.DataFrame({"BBB": s})
    buys_dn = [dict(ticker="BBB", side="buy", qty=1, price=165.0, date=idx[i].date())
               for i in range(260, 300)]
    d2 = tr.dim_entry_style(buys_dn, px_dn)
    assert d2["lean"] == "weakness", f"抄底應判 weakness,實得 {d2['lean']}（pct={d2['median_pct']:.2f}）"
    assert d2["median_pct"] < 0.30

    # ── 樣本不足 → 低信賴、不 triggered(但 lean 仍可算)──
    d3 = tr.dim_entry_style(buys_up[:3], px_up)
    assert d3["low_conf"] and not d3["triggered"]

    # ── 無價格 → 優雅降級,不 crash ──
    d4 = tr.dim_entry_style(buys_up, None)
    assert d4["lean"] is None and d4["low_conf"]


def test_tw_mixed_markets_currency_offline():
    """[deterministic] 台股+美股混市場 fixture:load 認得 Market/Currency 欄、driver map 用
    完整 yahoo ticker(2330.TW/.TWO)當 key。台股報價/α-β 對 ^TWII/combined 依賴走 network smoke。"""
    tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)
    tr.load_driver_map(os.path.join(MOCK, "sample_tw_mixed.driver_map.json"))
    rows = tr.load([os.path.join(MOCK, "sample_tw_mixed.csv")])
    assert {r["market"] for r in rows} == {"TW", "US"}
    cur_map, currencies, conflicts = tr.currency_map(rows)
    assert currencies == ["TWD", "USD"] and not conflicts
    assert cur_map["2330.TW"] == "TWD" and cur_map["AAPL"] == "USD"      # per-ticker 幣別對
    assert tr.driver("2330.TW")[0] == "半導體" and tr.driver("6488.TWO")[0] == "半導體"  # .TW/.TWO 當 key
    rts, _ = tr.round_trips(rows)
    held, _ = tr.positions(rows)
    assert len(rts) == 2                                    # AAPL(部分賣)、6488.TWO(清倉)
    assert held.get("2330.TW", (0,))[0] == 2000            # 台積電 2000 股大倉(三筆買入累積)
    flows = tr.load_cash_flows([os.path.join(MOCK, "sample_tw_mixed.csv")])
    twd_dep = [f for f in flows if f["currency"] == "TWD" and f["kind"] == "deposit"]
    assert twd_dep and abs(twd_dep[0]["amount"] - 1500000.0) < 1e-3     # TWD 入金各記原幣別


# ─────────────────────── 選配:network smoke(β 方向)───────────────────────

def test_beta_direction_network():
    """[network] 真實 yfinance 驗 β 方向:動能高槓桿、基本面低波動。TR_TEST_NETWORK=1 才跑。

    這示範另一種容錯:股價相關的數字(β)只斷言『方向/門檻』,不斷言精確值,抓不到價就 skip。
    """
    if os.environ.get("TR_TEST_NETWORK") != "1":
        return _SKIP  # 預設離線跳過

    def _beta(style):
        tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)
        tr.load_driver_map(os.path.join(MOCK, f"sample_{style}.driver_map.json"))
        rows = tr.load([os.path.join(MOCK, f"sample_{style}.csv")])
        tickers = {r["ticker"] for r in rows}
        start = min(r["date"] for r in rows).isoformat()
        px, err = tr.fetch_prices(tickers, start)
        if err or px is None:
            return None
        return tr.dim_alpha_beta(rows, px).get("beta")

    b_mom, b_fun = _beta("momentum"), _beta("fundamental")
    if b_mom is None or b_fun is None:
        return _SKIP  # yfinance 抓不到 → skip,不當失敗
    assert b_mom > 1.5, f"動能 β 應 >1.5(高槓桿),實得 {b_mom:.2f}"
    assert b_fun < 1.0, f"基本面 β 應 <1.0(低波動),實得 {b_fun:.2f}"
    assert b_mom > b_fun


def test_tw_mixed_combined_exposure_network():
    """[network] 台股報價(2330.TW/.TWO)抓得到 + 混市場 combined 口徑正確:最大單點依賴含台股
    (TSM 不從 engine 世界消失)、α/β 台股對 ^TWII、混幣現金錨點換算聚合幣。TR_TEST_NETWORK=1 才跑。"""
    if os.environ.get("TR_TEST_NETWORK") != "1":
        return _SKIP
    import subprocess
    import json as _json
    csv = os.path.join(MOCK, "sample_tw_mixed.csv")
    dm = os.path.join(MOCK, "sample_tw_mixed.driver_map.json")
    env = dict(os.environ, TR_JSON="1", TR_DRIVER_MAP=dm,
               TR_CASH=_json.dumps({"as_of": "2024-03-15", "amount": 250000, "currency": "TWD"}))
    engine = os.path.join(SKILL, "engine", "trade_recap.py")
    r = subprocess.run([sys.executable, engine, csv], capture_output=True, text=True, env=env)
    if r.returncode != 0 or not r.stdout.strip():
        return _SKIP                                       # 離線/抓不到 → skip,不當紅燈
    card = _json.loads(r.stdout)
    cov = card["overview"]["unrealized_coverage"]
    if cov["priced_n"] < cov["held_n"]:
        return _SKIP                                       # 台股價抓不齊(假日/延遲)→ skip 不當失敗
    # combined 最大單點依賴 = 台股 2330.TW(聚合 USD;若只看美股 CSV 它根本不在 engine 世界)
    assert card["ticker_diagnosis"][0]["ticker"] == "2330.TW", card["ticker_diagnosis"][0]["ticker"]
    # α/β per-market:頂層 scope=TW(資金佔比最大),對 ^TWII,不合成總 α
    ab = card["alpha_beta_breakdown"]
    assert ab["scope"] == "TW" and ab["bench"] == "^TWII", ab.get("scope")
    assert {"TW", "US"} <= set(ab["by_market"])
    # 混幣現金錨點換算聚合幣:250000 TWD ≈ 7-8.5k USD(非當 250000 USD 的 latent gap)
    assert 6000 < card["cash"]["balance"] < 9000, card["cash"]["balance"]
    # AI/半導體 combined 曝險分母含台股(what_if 板塊集中)
    assert "半導體" in (card["what_if"] or {}).get("label", "") and card["what_if"]["pct"] > 0.5


# ─────────────────────── 標準庫 runner(免 pytest 即可跑)───────────────────────

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = skipped = 0
    for name, fn in tests:
        try:
            if fn() == _SKIP:
                skipped += 1
                print(f"SKIP  {name}  (設 TR_TEST_NETWORK=1 才跑)")
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
