#!/usr/bin/env python3
"""線上半邊(價格路徑)的合成確定性測試(#62)—— 全離線,不碰 yfinance。

背景:SELL_EARLY_TH 改成 -9.99 三套測試全綠存活(突變證據)。「賣太早」是產品名字(FOMO)的
核心訊號,但所有「有價格才活」的路徑(fwd_from_px / dim_exit fwd 計分 / _regress / prescribe
α 分支 / what_if / thesis_q)之前只在 TR_TEST_NETWORK=1 才被碰到,而 CI 從不設它。

方法:合成 300 交易日的價格 DataFrame(pd.bdate_range,確定性),已知 round-trip 餵進去,
精確斷言 fwd 值 / 閾值邊界 / β / Jensen α ——「線上路徑」與「線上資料」解耦:路徑該測,資料才 flaky。

跟其他測試檔的分工:
- test_engine_units.py    → 無價格的純函式(load / FIFO / 攤平 / 誠實鐵律)。
- test_tr_json_contract.py→ SKILL 消費介面 key/型別契約。
- 本檔                    → 「有價格才活」的計分語意:賣太早閾值、β/α 回歸、α 處方分支、
                             what_if 集中度、thesis_q 生成 —— 每一條都是離線可斷言的合成路徑。

跑法:
  python3 tests/test_price_paths.py        # 標準庫 runner(免 pytest;需 pandas)
"""
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "skills", "fomo-kernel")
sys.path.insert(0, os.path.join(SKILL, "engine"))
import trade_recap as tr  # noqa: E402

import pandas as pd  # noqa: E402  # CI 本來就裝 pandas(dim_alpha_beta 需要);本檔不需 yfinance


# ─────────────────────────── 合成器 ───────────────────────────

IDX = pd.bdate_range("2024-01-02", periods=300)     # 300 個交易日,確定性

def _px_frame(cols):
    """cols = {ticker: list/Series of 300 prices} → 引擎 fetch_prices 同形狀的 DataFrame。"""
    return pd.DataFrame(cols, index=IDX)


def _rt(ticker, exit_i, sell_px=100.0, ret=0.10):
    """一筆已配對 round-trip,exit 落在合成索引第 exit_i 天(fwd_from_px 只讀這三個欄位+回寫)。"""
    return dict(ticker=ticker, exit=IDX[exit_i].date(), sell_px=sell_px, buy_px=sell_px / (1 + ret),
                qty=10, ret=ret, hold=30, entry=IDX[max(exit_i - 30, 0)].date())


def _spy_returns():
    """確定性、非退化的日報酬序列(給 β/α 回歸用;無 RNG,重跑永遠同值)。"""
    cyc = [0.012, -0.008, 0.015, -0.005, 0.010, -0.010]
    return [cyc[i % len(cyc)] for i in range(len(IDX) - 1)]


def _prices_from_returns(base, rets):
    out = [float(base)]
    for r in rets:
        out.append(out[-1] * (1 + r))
    return out


# ─────────────── A. fwd_from_px:賣出後 n_fwd 日報酬的精確語意 ───────────────

def test_fwd_from_px_exact_values_and_trunc():
    """fwd = (賣出後第 n_fwd 個交易日的價 − sell_px)/sell_px;資料不足 n_fwd 日 → 取最後一天 + fwd_trunc。"""
    aaa = [100.0] * 300
    aaa[80] = 105.0    # exit=IDX[50] → after 從 IDX[51] 起,第 30 天 = IDX[80]
    aaa[130] = 115.0   # exit=IDX[100] → IDX[130]
    aaa[180] = 109.0   # exit=IDX[150] → IDX[180]
    aaa[230] = 111.0   # exit=IDX[200] → IDX[230]
    aaa[299] = 120.0   # 末日:last_px + trunc 案例的 target
    px = _px_frame({"AAA": aaa, "SPY": [400.0] * 300})
    rts = [_rt("AAA", 50), _rt("AAA", 100), _rt("AAA", 150), _rt("AAA", 200),
           _rt("AAA", 290)]                                  # 290:後面只剩 9 天 → trunc
    fwds, last_px = tr.fwd_from_px(rts, px, n_fwd=30)
    got = [round(r["fwd"], 9) for r in rts]
    assert got[:4] == [0.05, 0.15, 0.09, 0.11], f"fwd 精確值不對:{got[:4]}"
    assert all(not r["fwd_trunc"] for r in rts[:4]), "滿窗的 4 筆不該標 trunc"
    assert rts[4]["fwd_trunc"] is True and abs(rts[4]["fwd"] - 0.20) < 1e-9, \
        f"窗不滿應取最後一天(120→fwd 0.20)+trunc,實得 {rts[4]['fwd']}, {rts[4]['fwd_trunc']}"
    assert abs(last_px["AAA"] - 120.0) < 1e-9, "last_px 應取該檔最後一個有效價"
    assert len(fwds) == 5


def test_dim_exit_early_rate_and_threshold_boundary():
    """issue #62 的招牌案例:fwd=(0.05,0.15,0.09,0.11) → early_rate 恰 0.5。
    SELL_EARLY_TH 被改成 -9.99 → 這裡變 1.0 → 紅燈(原本三套測試看不到)。"""
    aaa = [100.0] * 300
    aaa[80], aaa[130], aaa[180], aaa[230] = 105.0, 115.0, 109.0, 111.0
    px = _px_frame({"AAA": aaa})
    rts = [_rt("AAA", 50), _rt("AAA", 100), _rt("AAA", 150), _rt("AAA", 200)]
    tr.fwd_from_px(rts, px, n_fwd=30)
    d = tr.dim_exit(rts, None, n_fwd=30)
    assert abs(d["early_rate"] - 0.5) < 1e-9, f"early_rate 應 0.5(2/4 > TH),實得 {d['early_rate']}"
    assert abs(d["winner_early"] - 0.5) < 1e-9, "4 筆全是 winner → winner_early 同 0.5"
    assert abs(d["avg_forgone"] - 0.10) < 1e-9, "平均放掉 = mean(0.05,0.15,0.09,0.11) = 0.10"
    assert d["n_scored"] == 4 and d["n_trunc"] == 0
    assert d["low_conf"] is True, "winners=4 < MIN_WINNERS=5 → 低信賴要標出來"

    # 邊界語意:嚴格大於 —— 0.099 不算、0.101 算、恰 0.10 不算
    def scored(fwd):
        r = _rt("BBB", 50, ret=0.2)
        r["fwd"] = fwd
        return r
    d2 = tr.dim_exit([scored(0.099), scored(0.101)], None)
    assert abs(d2["early_rate"] - 0.5) < 1e-9, f"0.099 不算/0.101 算 → 0.5,實得 {d2['early_rate']}"
    d3 = tr.dim_exit([scored(0.10)], None)
    assert d3["early_rate"] == 0.0, f"恰等於 SELL_EARLY_TH(0.10)不算賣太早(嚴格 >),實得 {d3['early_rate']}"


def test_last_px_covers_held_only_tickers():
    """issue #79:持有中、從未平倉的標的也要有 last_px。
    修前 last_px 只從 round-trip 迴圈填 → held-only 標的(往往是最大倉位)的
    未實現損益/套牢診斷/what-if 全部靜默漏算(sample_value 實測漏 $46,202)。"""
    px = _px_frame({"AAA": [100.0] * 300,
                    "BBB": [50.0] * 299 + [80.0],      # held-only:從沒有任何 round-trip
                    "CCC": [30.0] * 240 + [float("nan")] * 60,   # 下市殭屍:尾端 60 交易日無價
                    "SPY": [400.0] * 300})
    rts = [_rt("AAA", 50)]                             # 只有 AAA 曾平倉
    _, last_px = tr.fwd_from_px(rts, px, n_fwd=30)
    assert abs(last_px.get("BBB", 0.0) - 80.0) < 1e-9, \
        f"held-only 的 BBB 應拿到最新價 80,實得 {last_px.get('BBB')}"
    assert abs(last_px.get("AAA", 0.0) - 100.0) < 1e-9, "曾平倉的 AAA 行為不變"
    assert "CCC" not in last_px, \
        f"staleness gate:殘價距末日 >10 天不可當現價(下游降級成本基礎),實得 {last_px.get('CCC')}"

    # 下游①:未實現損益要把 held-only 倉位算進去(10 股、成本 $500 → 10×80−500 = +$300)
    held = {"BBB": (10.0, 500.0)}
    ov = tr.overview_stats(rts, {"note": "x"}, held, last_px)
    assert abs(ov["unrealized"] - 300.0) < 1e-9, \
        f"unrealized 應為 +300(修前 BBB 無現價 → 0),實得 {ov.get('unrealized')}"

    # 下游②:what_if 壓測候選要看得到 held-only 的最大倉位
    wi = tr.what_if(held, last_px)
    assert wi and "BBB" in wi["label"], f"what_if 應鎖定 BBB,實得 {wi}"

    # 下游③:ticker_diagnosis 對 held-only 倉位給得出現價相關診斷
    #(impact=未實現 +300;tags 的「押太重 wpct」「賺 60%」都得靠 last_px 才算得出)
    td = tr.ticker_diagnosis(rts, {}, held, last_px)
    bbb = next((d for d in td if d["ticker"] == "BBB"), None)
    assert bbb is not None and abs(bbb["impact"] - 300.0) < 1e-9, \
        f"BBB 的 impact 應為未實現 +300,實得 {bbb}"
    assert any("押太重" in t for t in bbb["tags"]) and any("60%" in t for t in bbb["tags"]), \
        f"BBB 應有現價相關 tags(押太重/賺 60%),實得 {bbb['tags']}"


# ─────────────── B. β / Jensen α:合成序列的回歸恆等式 ───────────────

def test_beta_two_alpha_rf_for_leveraged_clone():
    """port 日報酬恆 = 2×SPY → β=2;Jensen α 恆等式給 α_ann = rf_annual(數學上精確,非近似)。"""
    rets = _spy_returns()
    spy = _prices_from_returns(400.0, rets)
    lev = _prices_from_returns(100.0, [2 * r for r in rets])
    px = _px_frame({"LEV": lev, "SPY": spy})
    rows = [dict(ticker="LEV", side="buy", qty=10, price=100.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    assert "benchmarks" in ab and "SPY" in ab["benchmarks"], f"應有 SPY 回歸:{ab.get('note')}"
    assert ab["n"] >= 290, f"300 天合成資料回歸點應近乎全收,實得 n={ab['n']}"
    assert abs(ab["beta"] - 2.0) < 1e-9, f"β 應精確 2.0,實得 {ab['beta']}"
    assert abs(ab["alpha_ann"] - tr.RF_ANNUAL) < 1e-9, \
        f"2×SPY 的 Jensen α 恆 = rf_annual({tr.RF_ANNUAL}),實得 {ab['alpha_ann']}(cov/var 次序或 rf 日化寫錯會炸這裡)"
    st = ab["alpha_stat"]
    assert st["t"] is None and st["grade"] == "noise", \
        f"完美複製品殘差=0 → se=0 → t=None(除零守衛),保守歸 noise:{st}"


def test_beta_one_alpha_zero_for_spy_clone():
    """port = SPY 本身 → β=1、α=0:回歸的『零點』。×252 年化/日化任何一處寫錯都藏不住。"""
    rets = _spy_returns()
    spy = _prices_from_returns(400.0, rets)
    clone = _prices_from_returns(100.0, rets)
    px = _px_frame({"CLONE": clone, "SPY": spy})
    rows = [dict(ticker="CLONE", side="buy", qty=10, price=100.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    assert abs(ab["beta"] - 1.0) < 1e-9, f"β 應精確 1.0,實得 {ab['beta']}"
    assert abs(ab["alpha_ann"]) < 1e-9, f"α 應精確 0,實得 {ab['alpha_ann']}"
    assert abs(ab["excess_vs_spy"]) < 1e-9, "同報酬序列 → 超額 = 0"


# ─────────────── B2. excess_split:賽道/選股拆帳(Brinson 式恆等)───────────────

def test_excess_split_identity_semi_vs_soxx():
    """拆帳恆等式:配置 + 選股 = 贏大盤(同天集同複利,1e-9 級,不是近似)。
    半導體檔對照 SOXX:配置 = SOXX−SPY、選股 = 你−SOXX——把「押對賽道」從「選股」裡剝出來。"""
    rets = _spy_returns()
    spy = _prices_from_returns(400.0, rets)
    soxx = _prices_from_returns(200.0, [r * 1.5 for r in rets])              # 板塊比大盤兇
    nvda = _prices_from_returns(100.0, [r * 1.5 + 0.001 for r in rets])      # 板塊內再多一點選股
    px = _px_frame({"NVDA": nvda, "SOXX": soxx, "SPY": spy})
    rows = [dict(ticker="NVDA", side="buy", qty=10, price=100.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    sp = ab["excess_split"]
    assert abs(sp["allocation"] + sp["selection"] - sp["excess"]) < 1e-9, \
        f"恆等式破了:{sp['allocation']}+{sp['selection']} ≠ {sp['excess']}"
    assert abs(sp["excess"] - ab["excess_vs_spy"]) < 1e-9, "拆帳與回歸同天集,excess 必相等"
    soxx_tot = ab["benchmarks"]["SOXX"]["bench_tot"]
    spy_tot = ab["benchmarks"]["SPY"]["bench_tot"]
    assert abs(sp["allocation"] - (soxx_tot - spy_tot)) < 1e-9, "唯一持倉=半導體 → mimic 就是 SOXX"
    assert sp["selection"] > 0, "NVDA 每日比 SOXX 多 +10bp,選股應為正"
    assert sp["coverage"] > 0.999 and sp["unproxied"] == []


def test_excess_split_unmapped_ticker_goes_to_selection():
    """無板塊對照 → 按 SPY 計:配置恆 0、超額全歸選股;unproxied / coverage 誠實入帳(卡上要提)。"""
    rets = _spy_returns()
    spy = _prices_from_returns(400.0, rets)
    zzz = _prices_from_returns(100.0, [r + 0.002 for r in rets])
    px = _px_frame({"ZZZZ": zzz, "SPY": spy})
    rows = [dict(ticker="ZZZZ", side="buy", qty=10, price=100.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    sp = ab["excess_split"]
    assert abs(sp["allocation"]) < 1e-9, f"mimic=SPY → 配置恆 0,實得 {sp['allocation']}"
    assert abs(sp["selection"] - sp["excess"]) < 1e-9
    assert sp["unproxied"] == ["ZZZZ"] and sp["coverage"] < 0.01, f"覆蓋要誠實:{sp}"
    # #92:unproxied 是 data_integrity["unproxied_sectors"] 的來源(併入永遠顯示的揭露入口,不再只在 α 面板)


def test_sector_labeled_but_unmapped_is_unproxied_not_unclassified():
    """#92:有 driver 標籤但 SECTOR_BENCH 查無對照的檔 = 板塊歸因不可靠的第二種——它不是「未分類」
    (有標籤,躲過 unclassified_drivers),但拆帳靜默按 SPY 計、賽道功勞被誤記成選股 → 必須也揭露。
    重現 issue #92 的實測案例(Claude 生成合理標籤『電力』,SECTOR_BENCH 未收錄)。"""
    tr._DRIVER_MAP["FAKE_POWER_CO"] = ("電力", 0)
    try:
        assert tr.driver("FAKE_POWER_CO")[0] == "電力", "有標籤 → 不會進 unclassified_drivers(躲過既有揭露)"
        assert "電力" not in tr.SECTOR_BENCH, "前提:此標籤確實不在封閉對照表(bare『電力』≠『資料中心電力』)"
        assert tr._sector_proxy("FAKE_POWER_CO") is None, "查無 ETF → 靜默降級按 SPY 計(#92 的洞)"
    finally:
        del tr._DRIVER_MAP["FAKE_POWER_CO"]


def test_excess_split_broad_etf_is_allocation():
    """持有大盤/區域 ETF = 配置決策:基準=它自己 → 選股恆 0,超額全歸賽道(買 EWY 是押韓國,不是選股)。"""
    rets = _spy_returns()
    spy = _prices_from_returns(400.0, rets)
    ewy = _prices_from_returns(60.0, [r * 0.8 + 0.0005 for r in rets])
    px = _px_frame({"EWY": ewy, "SPY": spy})
    rows = [dict(ticker="EWY", side="buy", qty=100, price=60.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    sp = ab["excess_split"]
    assert abs(sp["selection"]) < 1e-9, f"ETF 對照自己 → 選股恆 0,實得 {sp['selection']}"
    assert abs(sp["allocation"] - sp["excess"]) < 1e-9
    assert sp["unproxied"] == [] and sp["coverage"] > 0.999


def test_flat_benchmark_returns_none_not_nan():
    """#90 review 修:基準變異數=0(整段停牌/假期資料)→ _regress 回 None,不除出 NaN。
    改前 beta=xe.cov(ye)/xe.var() 在 var=0 時算出 NaN,會漏進 TR_JSON 變成非法 token。"""
    rets = _spy_returns()
    flat_spy = [400.0] * len(IDX)                      # 變異數恆 0
    stk = _prices_from_returns(100.0, rets)
    px = _px_frame({"ZZZZ": stk, "SPY": flat_spy})
    rows = [dict(ticker="ZZZZ", side="buy", qty=10, price=100.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    assert ab.get("note") == "樣本不足", f"SPY 零波動應直接判樣本不足,不該算出 NaN:{ab}"


def test_partial_nan_proxy_recorded_as_unproxied():
    """#90 review 修(Codex):板塊 ETF 有欄但開頭 NaN(如新上市 ETF)→ 那幾天降級用 SPY,
    coverage 該 <1;該檔必須進 unproxied(誠實揭露),且不該同時出現在 proxy 字典裡
    (否則卡片一邊說『NVDA 對照 SOXX』一邊 coverage 又 <1,自相矛盾)。"""
    rets = _spy_returns()
    spy = _prices_from_returns(400.0, rets)
    soxx = _prices_from_returns(200.0, rets)
    soxx[:30] = [float("nan")] * 30                     # 開頭 30 天無價(新上市 ETF 的常見情況)
    nvda = _prices_from_returns(100.0, [r + 0.001 for r in rets])
    px = _px_frame({"NVDA": nvda, "SOXX": soxx, "SPY": spy})
    rows = [dict(ticker="NVDA", side="buy", qty=10, price=100.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    sp = ab["excess_split"]
    assert sp["coverage"] < 1.0, f"開頭 30 天 fallback 應反映在 coverage:{sp}"
    assert "NVDA" in sp["unproxied"], f"部分日 fallback 也要誠實記入 unproxied:{sp}"
    assert "NVDA" not in sp["proxy"], \
        f"曾 fallback 的 ticker 不該同時宣稱『對照 SOXX』(誤導 coverage<1 卻像全程有效):{sp}"


def test_alpha_se_widens_with_idiosyncratic_noise():
    """#80 的核心測量:集中(個股雜訊大)→ 殘差大 → SE 寬 → t 低。
    同一 α、雜訊 ×4 → SE 應近乎 ×4——「判不準」由統計直接量,不再靠持倉檔數代理。"""
    def ab_for(scale):
        rets = _spy_returns()
        noise = [(0.004 if i % 2 == 0 else -0.004) * scale for i in range(len(rets))]
        spy = _prices_from_returns(400.0, rets)
        stk = _prices_from_returns(100.0, [r + n for r, n in zip(rets, noise)])
        px = _px_frame({"ZZZZ": stk, "SPY": spy})
        rows = [dict(ticker="ZZZZ", side="buy", qty=10, price=100.0, date=IDX[0].date())]
        return tr.dim_alpha_beta(rows, px)["alpha_stat"]
    lo, hi = ab_for(1.0), ab_for(4.0)
    assert lo["se_ann"] > 0 and hi["se_ann"] > 0
    assert hi["se_ann"] > lo["se_ann"] * 3.5, \
        f"雜訊 ×4 → SE 應近乎 ×4,實得 {lo['se_ann']:.4f} → {hi['se_ann']:.4f}"
    assert abs(lo["t"] - lo["alpha_ann"] / lo["se_ann"]) < 1e-9, "t = α/SE(年化互相抵銷)要自洽"
    ci = lo["ci95"]
    assert abs((ci[1] - ci[0]) - 2 * 1.96 * lo["se_ann"]) < 1e-9, "CI 寬 = 2×1.96×SE"


# ─────────────── B3. per-market α/β(#129 PR-2b,prd-ledger §2.4)───────────────

def test_single_market_schema_unchanged():
    """回歸鎖:純美股輸出與 per-market 重構前恆等 —— scope/by_market 補 None,其餘欄照舊。"""
    rets = _spy_returns()
    px = _px_frame({"CLONE": _prices_from_returns(100.0, rets),
                    "SPY": _prices_from_returns(400.0, rets)})
    rows = [dict(ticker="CLONE", side="buy", qty=10, price=100.0, date=IDX[0].date())]
    ab = tr.dim_alpha_beta(rows, px)
    assert ab["scope"] is None and ab["by_market"] is None, "單一市場不啟用 per-market 分組"
    for k in ("benchmarks", "n", "beta", "alpha_ann", "alpha_stat", "excess_split",
              "port_tot", "spy_tot", "excess_vs_spy"):
        assert k in ab, f"舊契約欄位 {k} 不見了"
    assert ab["bench"] == "SPY"


def test_per_market_split_each_vs_own_bench():
    """混市場:美股部位對 SPY(β=2)、台股部位對 ^TWII(β=1)——各算各的,互不污染;
    頂層 = market_weights 較大的市場 + scope 標明;台股 excess_split coverage=0(無板塊對照)。"""
    rets = _spy_returns()
    twii_rets = [0.5 * r for r in rets]                     # 台股大盤 = 獨立(等比)序列
    px = _px_frame({
        "SPY": _prices_from_returns(400.0, rets),
        "^TWII": _prices_from_returns(18000.0, twii_rets),
        "USLEV": _prices_from_returns(100.0, [2 * r for r in rets]),      # β=2 vs SPY
        "2330.TW": _prices_from_returns(900.0, twii_rets),                # β=1 vs ^TWII(複製品)
    })
    rows = [dict(ticker="USLEV", side="buy", qty=10, price=100.0, date=IDX[0].date(),
                 market="US", currency="USD"),
            dict(ticker="2330.TW", side="buy", qty=100, price=900.0, date=IDX[0].date(),
                 market="TW", currency="TWD")]
    ab = tr.dim_alpha_beta(rows, px, market_weights={"US": 9.0, "TW": 1.0})
    assert ab["scope"] == "US", f"權重大的市場當頂層 scope,得 {ab.get('scope')}"
    bm = ab["by_market"]
    assert set(bm) == {"US", "TW"}
    us, tw = bm["US"], bm["TW"]
    assert us["bench"] == "SPY" and abs(us["beta"] - 2.0) < 1e-9, f"美股部位 β 應 2.0:{us.get('beta')}"
    assert tw["bench"] == "^TWII" and abs(tw["beta"] - 1.0) < 1e-9, \
        f"台股部位對 ^TWII β 應 1.0(對 SPY 混算就不會是 1):{tw.get('beta')}"
    assert abs(tw["excess_vs_spy"]) < 1e-9, "複製品贏自家大盤 = 0(鍵名歷史遺留,語意=該市場大盤)"
    assert tw["excess_split"]["coverage"] == 0.0, "台股第一版無板塊對照 → coverage 0(按大盤計)"
    assert abs(tw["excess_split"]["allocation"]) < 1e-9 and \
           abs(tw["excess_split"]["selection"] - tw["excess_vs_spy"]) < 1e-9, \
        "無對照 → 配置 0、超額全歸選股(恆等式仍成立)"
    assert "QQQ" not in tw["benchmarks"], "參考基準(QQQ/SOXX)只掛美股子組合"
    # 頂層 = US 欄位展開(消費者相容)
    assert abs(ab["beta"] - 2.0) < 1e-9 and ab["bench"] == "SPY"
    # 權重反轉 → 頂層變 TW
    ab2 = tr.dim_alpha_beta(rows, px, market_weights={"US": 1.0, "TW": 9.0})
    assert ab2["scope"] == "TW" and abs(ab2["beta"] - 1.0) < 1e-9


def test_per_market_missing_bench_degrades_honestly():
    """混市場但 ^TWII 沒抓到價(離線/抓不到):台股子組合 note、美股照算,頂層 scope=US。"""
    rets = _spy_returns()
    px = _px_frame({"SPY": _prices_from_returns(400.0, rets),
                    "USLEV": _prices_from_returns(100.0, [2 * r for r in rets]),
                    "2330.TW": _prices_from_returns(900.0, rets)})
    rows = [dict(ticker="USLEV", side="buy", qty=10, price=100.0, date=IDX[0].date(),
                 market="US", currency="USD"),
            dict(ticker="2330.TW", side="buy", qty=1, price=900.0, date=IDX[0].date(),
                 market="TW", currency="TWD")]
    ab = tr.dim_alpha_beta(rows, px, market_weights={"US": 1.0, "TW": 9.0})
    assert ab["scope"] == "US", "TW 無基準價 → 有效市場只剩 US(權重再大也輪不到壞資料)"
    assert ab["by_market"]["TW"].get("note") == "無價格/^TWII"
    assert abs(ab["beta"] - 2.0) < 1e-9


def test_tw_only_consumers_not_spy_hardcoded():
    """review 抓的消費者 bug:純台股組合 benchmarks 只有 ^TWII——
    prescribe 的拆帳分支不准因硬編 'SPY' 整段跳過;人話卡渲染同理(bench 動態)。"""
    twii_rets = _spy_returns()
    px = _px_frame({"^TWII": _prices_from_returns(18000.0, twii_rets),
                    "TWLEV": _prices_from_returns(900.0, [2 * r for r in twii_rets])})
    rows = [dict(ticker="TWLEV", side="buy", qty=100, price=900.0, date=IDX[0].date(),
                 market="TW", currency="TWD")]
    ab = tr.dim_alpha_beta(rows, px)
    assert ab.get("note") is None and ab["bench"] == "^TWII" and "SPY" not in ab["benchmarks"]
    assert abs(ab["beta"] - 2.0) < 1e-9, "純台股 β 對 ^TWII 應 2.0"
    dims_min = [dict(dim="出場紀律", triggered=False, severity=0, tier=1),
                dict(dim="部位 sizing", triggered=False, severity=0, tier=1, max_pct=0.1),
                dict(dim="分散", triggered=False, severity=0, tier=2, ai_pct=0.1),
                dict(dim="持有時間", triggered=False, severity=0, tier=2),
                dict(dim="加碼攤平", triggered=False, severity=0, tier=1, count=0)]
    ov = dict(payoff=1.5, realized=100.0, pf=2.0)
    rx = tr.prescribe(ab, dims_min, ov)
    assert any(("pp" in (r.get("text") or "")) or r.get("kind") for r in rx), \
        "prescribe 對純台股不得因 'SPY' 硬編而整包空手(至少 α/拆帳分支要活)"


def test_sector_proxy_market_aware():
    """台股 ticker 即使 driver 命中美股板塊表(半導體→SOXX),也不准拿美股 ETF 當對照。"""
    tr._DRIVER_MAP["2330.TW"] = ("半導體", 1)
    try:
        assert tr._sector_proxy("2330.TW", "US") == "SOXX", "美股市場照舊查表"
        assert tr._sector_proxy("2330.TW", "TW") is None, "非美股市場 → None(按大盤計)"
        tr._DRIVER_MAP["0050.TW"] = ("大盤ETF", 0)
        assert tr._sector_proxy("0050.TW", "TW") == "0050.TW", "ETF=自己(BENCH_SELF 跨市場通用)"
    finally:
        tr._DRIVER_MAP = dict(tr.DRIVER_FALLBACK)


# ─────────────── C. prescribe:α 分支(卡面「怎麼優化」主文案)───────────────

def _ab(excess, alloc, sel, credible, t=0.8, alpha_ann=0.01):
    """組 prescribe 需要的最小 ab:拆帳(excess_split)+ 統計(alpha_stat)+ SPY 回歸摘要。"""
    return {"credible": credible,
            "excess_split": {"excess": excess, "allocation": alloc, "selection": sel,
                             "mimic_tot": 0.0, "coverage": 1.0, "unproxied": [], "proxy": {}},
            "alpha_stat": {"alpha_ann": alpha_ann, "se_ann": 0.05, "t": t,
                           "ci95": [alpha_ann - 0.1, alpha_ann + 0.1], "n_days": 300,
                           "grade": "significant" if credible else "noise",
                           "gate": None if credible else {"reason": "not_significant", "t": t, "need": 1.96}},
            "benchmarks": {"SPY": dict(beta=1.2, alpha_ann=alpha_ann, port_tot=0.30,
                                       bench_tot=0.30 - excess, excess=excess, n=300)}}


def _kinds(rx):
    return [r["kind"] for r in rx]


def test_prescribe_not_credible_branch():
    rx = tr.prescribe(_ab(0.02, 0.01, 0.01, credible=False), [], {})
    assert "選股:資料不足以判定" in _kinds(rx), f"不 credible 必須誠實說判不出:{_kinds(rx)}"
    assert "外包短板(漸進)" not in _kinds(rx) and "揚長" not in _kinds(rx), \
        "不 credible 不准下外包/真 edge 定論"


def test_prescribe_outsource_branch():
    rx = tr.prescribe(_ab(0.02, 0.10, -0.08, credible=True, t=2.5, alpha_ann=-0.05), [], {})
    assert "外包短板(漸進)" in _kinds(rx), f"統計站得住且板塊內選股 −8pp 應給外包處方:{_kinds(rx)}"


def test_prescribe_true_edge_branch():
    rx = tr.prescribe(_ab(0.10, 0.02, 0.08, credible=True, t=2.5, alpha_ann=0.06), [], {})
    assert "揚長" in _kinds(rx), f"統計顯著且扣掉賽道仍 +8pp 是真 edge:{_kinds(rx)}"


def test_prescribe_excess_hypothesis_branch():
    rx = tr.prescribe(_ab(0.15, 0.12, 0.03, credible=True, t=2.5, alpha_ann=0.02), [], {})
    assert "揚長(假設,待驗證)" in _kinds(rx), f"贏大盤 +15pp 且賽道佔大頭應給『押賽道假設』:{_kinds(rx)}"


def test_prescribe_small_selection_no_forced_verdict():
    """統計顯著但選股影響小(|sel|≤5pp 且 α≥0)→ 不硬下選股處方(揚長/外包都不出,誠實留白)。"""
    rx = tr.prescribe(_ab(0.06, 0.04, 0.02, credible=True, t=2.2, alpha_ann=0.03), [], {})
    ks = _kinds(rx)
    assert "外包短板(漸進)" not in ks and "揚長" not in ks and "選股:資料不足以判定" not in ks, \
        f"選股影響小不該硬下定論:{ks}"


# ─────────────── D. what_if / thesis_q:離線也該活的正例 ───────────────

def test_what_if_concentration_positive_and_negative():
    """單一 AI 標的 100% → AI 集中度情境(drop30 = 市值×0.3);五檔未分類 20% → None。"""
    wi = tr.what_if({"NVDA": (10.0, 1000.0)}, {"NVDA": 200.0})
    assert wi is not None and wi["label"].startswith("AI"), f"NVDA 100% 應觸發 AI 集中度:{wi}"
    assert abs(wi["pct"] - 1.0) < 1e-9 and abs(wi["drop30"] - 600.0) < 1e-9, \
        f"mval=2000 → drop30=600,實得 {wi}"
    held = {t: (10.0, 1000.0) for t in ("ZZA", "ZZB", "ZZC", "ZZD", "ZZE")}
    px = {t: 100.0 for t in held}
    assert tr.what_if(held, px) is None, "五檔各 20%、無 AI/板塊集中 → 不該硬掰情境"


def test_thesis_q_generated_for_suspect_positions():
    """thesis_q(SKILL Step 2 對話素材)的兩個生成分支:現虧問『還信嗎』、現賺問『合理化?』。"""
    td = tr.ticker_diagnosis(
        [], {"TTT": dict(cls="疑似凹單", n_adds=5, loss_ratio=1.0)},
        {"TTT": (10.0, 2000.0)}, {"TTT": 100.0})              # cur = −50%
    q = next((d["thesis_q"] for d in td if d["ticker"] == "TTT"), None)
    assert q and "還相信當初買它的理由" in q, f"現虧的疑似凹單應問『還信嗎』:{q}"

    td2 = tr.ticker_diagnosis(
        [], {"UUU": dict(cls="待確認", n_adds=4, loss_ratio=0.6)},
        {"UUU": (10.0, 500.0)}, {"UUU": 100.0})               # cur = +100%
    q2 = next((d["thesis_q"] for d in td2 if d["ticker"] == "UUU"), None)
    assert q2 and "合理化" in q2, f"現賺的待確認應問『定投還是合理化』:{q2}"

    td3 = tr.ticker_diagnosis(
        [], {"VVV": dict(cls="疑似定投", n_adds=6, loss_ratio=0.2)},
        {"VVV": (10.0, 1000.0)}, {"VVV": 150.0})
    assert all(d["thesis_q"] is None for d in td3), "疑似定投不問 thesis(別審問)"


# ─────────────── H. account_perf(#171 B 路線):daily 鏈式 TWR / cash drag / 帳戶 IRR ───────────────
# 全合成、確定性:V_t = 持倉(復權價語意)+ 現金(錨點回滾);外部流 = deposit/withdrawal/other。

import perf as pf  # noqa: E402  # 同目錄 engine 已在 sys.path


def _lin(a, b):
    """300 天線性價格路徑 a → b(確定性)。"""
    n = len(IDX)
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _row(i, ticker="X", side="buy", qty=10.0, px=100.0):
    return dict(date=IDX[i].date(), ticker=ticker, side=side, qty=qty, price=px)


def _cf(i, amount, kind, ccy="USD"):
    return dict(date=IDX[i].date(), amount=float(amount), kind=kind, currency=ccy)


def _cashd(source, by):
    """cash_position 輸出的最小替身(account_perf 只讀 source / by_currency)。"""
    return {"source": source, "reliable": source == "anchored", "by_currency": by}


def _anch(balance, reliable=True):
    return {"balance": float(balance), "source": "anchored" if reliable else "csv_sum",
            "reliable": reliable}


def test_acct_all_in_zero_cash_drag_zero():
    """全倉零現金:acct == hold,drag 恆等於 0(鏈式歸因的恆等錨)。IRR ≈ TWR 年化。"""
    px = _px_frame({"X": _lin(100, 150)})
    a = pf.account_perf([_row(0)], px, [_cf(0, -1000, "trade")],
                        _cashd("anchored", {"USD": _anch(0)}), {"X": "USD"})
    assert abs(a["acct_twr"] - 0.5) < 1e-9, a["acct_twr"]
    assert abs(a["hold_twr"] - 0.5) < 1e-9, a["hold_twr"]
    assert abs(a["cash_drag"]) < 1e-12, a["cash_drag"]
    assert a["avg_cash_weight"] == 0.0, a["avg_cash_weight"]
    days = (IDX[-1].date() - IDX[0].date()).days
    expect_irr = 1.5 ** (365.0 / days) - 1
    assert a["irr_annual"] is not None and abs(a["irr_annual"] - expect_irr) < 1e-4, \
        (a["irr_annual"], expect_irr)


def test_acct_deposit_does_not_distort_twr():
    """平價路徑 + 中途大額入金:TWR/IRR 全 0(BOD 剝離;錢進來≠賺到)。"""
    px = _px_frame({"X": [100.0] * len(IDX)})
    a = pf.account_perf([_row(0)], px,
                        [_cf(0, -1000, "trade"), _cf(100, 5000, "deposit")],
                        _cashd("anchored", {"USD": _anch(5000)}), {"X": "USD"})
    assert abs(a["acct_twr"]) < 1e-9, a["acct_twr"]
    assert abs(a["hold_twr"]) < 1e-9, a["hold_twr"]
    assert a["irr_annual"] is not None and abs(a["irr_annual"]) < 1e-6, a["irr_annual"]


def test_acct_other_kind_is_external_flow():
    """拍板四:kind=other(ACH/Transfer)計入外部流——漏計會把入金當「賺的」,此測殺那個突變。"""
    px = _px_frame({"X": [100.0] * len(IDX)})
    a = pf.account_perf([_row(0)], px,
                        [_cf(0, -1000, "trade"), _cf(100, 5000, "other")],
                        _cashd("anchored", {"USD": _anch(5000)}), {"X": "USD"})
    assert abs(a["acct_twr"]) < 1e-9, f"other 沒進外部流,入金被算成報酬:{a['acct_twr']}"
    assert a["irr_annual"] is not None and abs(a["irr_annual"]) < 1e-6, a["irr_annual"]


def test_acct_half_cash_dilutes_and_dollar_approx():
    """持倉 +50% 但一半錢躺現金:acct = +25%,drag = −25pp,機會成本 ≈ 平均現金 × hold_twr。"""
    px = _px_frame({"X": _lin(100, 150)})
    a = pf.account_perf([_row(0)], px, [_cf(0, -1000, "trade")],
                        _cashd("anchored", {"USD": _anch(1000)}), {"X": "USD"})
    assert abs(a["acct_twr"] - 0.25) < 0.02, a["acct_twr"]      # 現金權重逐日漂移,約 25%
    assert a["cash_drag"] < -0.20, a["cash_drag"]
    assert abs(a["drag_dollar_approx"] - 1000 * a["hold_twr"]) < 1.0, a["drag_dollar_approx"]
    assert 0.35 < a["avg_cash_weight"] < 0.5, a["avg_cash_weight"]  # 1000/2000 起、1000/2500 終


def test_acct_dividend_not_double_counted():
    """復權價語意:價已含息(平線)+ 股息現金入帳 → 沖銷後 acct/hold 都 = 0(不沖會虛 +5%)。"""
    px = _px_frame({"X": [100.0] * len(IDX)})
    a = pf.account_perf([_row(0)], px,
                        [_cf(0, -1000, "trade"), _cf(150, 50, "dividend")],
                        _cashd("anchored", {"USD": _anch(50)}), {"X": "USD"})
    assert abs(a["acct_twr"]) < 1e-9, f"股息被雙計:{a['acct_twr']}"
    assert abs(a["hold_twr"]) < 1e-9, a["hold_twr"]


def test_acct_exit_then_idle_covers_crash():
    """出場後空倉期照走(#164 縫 A 在帳戶級閉合):出場後股價崩,acct 鎖住出場報酬。"""
    path = ([100 + 20 * i / 100 for i in range(101)] +          # day 0–100:100 → 120
            [120 - 60 * i / 199 for i in range(1, 200)])        # day 101–299:崩到 60
    px = _px_frame({"X": path})
    a = pf.account_perf([_row(0), _row(100, side="sell", qty=10.0, px=120.0)], px,
                        [_cf(0, -1000, "trade"), _cf(100, 1200, "trade")],
                        _cashd("anchored", {"USD": _anch(1200)}), {"X": "USD"})
    assert abs(a["acct_twr"] - 0.2) < 1e-6, a["acct_twr"]       # 躲掉的跌不會回頭咬
    assert abs(a["hold_twr"] - 0.2) < 1e-6, a["hold_twr"]       # 持倉柱只涵蓋持倉日
    assert abs(a["cash_drag"]) < 1e-6, a["cash_drag"]


def test_acct_csv_sum_gated_hold_only():
    """三態 gate:csv_sum(全無錨點)→ 帳戶柱不出、持倉柱照出、note 指路 TR_CASH。"""
    px = _px_frame({"X": _lin(100, 150)})
    a = pf.account_perf([_row(0)], px, [_cf(0, -1000, "trade")],
                        _cashd("csv_sum", {"USD": _anch(-1000, reliable=False)}), {"X": "USD"})
    assert a["acct_twr"] is None and a["irr_annual"] is None and a["cash_drag"] is None, a
    assert a["hold_twr"] is not None and abs(a["hold_twr"] - 0.5) < 1e-9, a["hold_twr"]
    assert a["note"] and "csv_sum" in a["note"], a["note"]


def test_acct_partial_broken_rollback_gated():
    """partial 但盲算幣別回滾出負現金 = 假設破裂 → 帳戶柱降 None(不出污染數字)。"""
    px = _px_frame({"X": _lin(100, 150)})
    flows = [_cf(0, -1000, "trade"), _cf(50, -10000, "withdrawal", ccy="TWD")]
    cd = {"source": "partial", "reliable": False,
          "by_currency": {"USD": _anch(0), "TWD": _anch(-10000, reliable=False)}}
    a = pf.account_perf([_row(0)], px, flows, cd, {"X": "USD"})
    assert a["acct_twr"] is None and "破裂" in (a["note"] or ""), a
    assert a["hold_twr"] is not None, a["hold_twr"]


def test_acct_partial_ok_discloses_unanchored():
    """partial 且盲算桶不負 → 帳戶柱照出,basis.unanchored 記缺錨點幣別(honesty 揭露源)。"""
    px = _px_frame({"X": _lin(100, 150)})
    flows = [_cf(0, -1000, "trade"), _cf(50, 10000, "deposit", ccy="TWD")]
    cd = {"source": "partial", "reliable": False,
          "by_currency": {"USD": _anch(0), "TWD": _anch(10000, reliable=False)}}
    a = pf.account_perf([_row(0)], px, flows, cd, {"X": "USD"})
    assert a["acct_twr"] is not None, a
    assert a["basis"]["unanchored"] == ["TWD"], a["basis"]


def test_acct_fx_daily_series_captures_currency_gain():
    """混幣拍板默認:每日 fx 序列 → TWD 現金升值計入帳戶報酬(匯損益不歸零)。"""
    px = _px_frame({"X": [100.0] * len(IDX)})
    fxs = pd.DataFrame({"TWD": _lin(0.030, 0.033)}, index=IDX)
    a = pf.account_perf([_row(0)], px, [_cf(0, -100, "trade")],
                        _cashd("anchored", {"USD": _anch(0), "TWD": _anch(300000)}),
                        {"X": "USD"}, fx_spot={"TWD": 0.033, "USD": 1.0}, fx_series=fxs)
    assert a["acct_twr"] > 0.05, a["acct_twr"]                  # 9000 → 9900 純匯升
    assert a["basis"]["fx_approx"] is False, a["basis"]
    assert a["cash_drag"] > 0.05, a["cash_drag"]                # 現金桶貢獻為正(drag 翻號語意)


def test_acct_fx_series_missing_falls_back_spot_flagged():
    """fx 序列缺 → 退回即期常數(匯損益歸零的近似)並標 fx_approx(honesty 揭露源)。"""
    px = _px_frame({"X": [100.0] * len(IDX)})
    a = pf.account_perf([_row(0)], px, [_cf(0, -100, "trade")],
                        _cashd("anchored", {"USD": _anch(0), "TWD": _anch(300000)}),
                        {"X": "USD"}, fx_spot={"TWD": 0.033, "USD": 1.0}, fx_series=None)
    assert a["acct_twr"] is not None and abs(a["acct_twr"]) < 1e-9, a["acct_twr"]
    assert a["basis"]["fx_approx"] is True, a["basis"]


def test_acct_irr_short_window_gated():
    """窗 <90 天:年化 IRR 無意義 → None + note(同 #164 gate);TWR(全期,非年化)照出。"""
    sub = pd.DataFrame({"X": [100.0 + i for i in range(40)]}, index=IDX[:40])
    a = pf.account_perf([_row(0)], sub, [_cf(0, -1000, "trade")],
                        _cashd("anchored", {"USD": _anch(0)}), {"X": "USD"})
    assert a["irr_annual"] is None and "年化" in (a["note"] or ""), a
    assert a["acct_twr"] is not None, a


def test_acct_negative_equity_days_skipped_not_chained():
    """深 margin 淨值翻負的日子:因子 ≤0 不入鏈 + skipped 計數(TWR 無法穿越破產點)——
    硬乘會讓整條鏈翻號(×負數)且永久失真。"""
    px = _px_frame({"X": [100.0] * 10 + [5.0] * (len(IDX) - 10)})   # day10 崩 95%
    a = pf.account_perf([_row(0)], px, [_cf(0, -1000, "trade")],
                        _cashd("anchored", {"USD": _anch(-950)}), {"X": "USD"})  # 真融資 margin
    assert a["basis"]["skipped_days"] > 0, a["basis"]
    assert a["note"] and "未入鏈" in a["note"], a["note"]
    assert a["acct_twr"] is not None and abs(a["acct_twr"]) < 0.01, a["acct_twr"]  # 崩前平盤段,不被負因子翻爆


def test_acct_offline_fail_closed():
    """px=None(離線)→ {note} 單鍵 fail-closed,不硬湊(同 pnl_curve 慣例)。"""
    a = pf.account_perf([_row(0)], None, [], _cashd("anchored", {"USD": _anch(0)}), {})
    assert set(a.keys()) == {"note"} and a["note"], a


def test_acct_unpriced_ticker_carried_at_cost():
    """抓不到價的檔(限流/下市)以成本平線入桶:零報酬、不撕裂桶會計——
    整檔剔除會讓「錢出了現金桶、資產憑空消失」→ 假 −100% + V 被打凹(第三輪 sweep 實測)。"""
    px = _px_frame({"X": _lin(100, 150)})               # Y 不在 px → at-cost
    rows = [_row(0), _row(10, ticker="Y", qty=5.0, px=200.0)]
    flows = [_cf(0, -1000, "trade"), _cf(10, -1000, "trade")]
    a = pf.account_perf(rows, px, flows,
                        _cashd("anchored", {"USD": _anch(0)}), {"X": "USD", "Y": "USD"})
    assert a["basis"]["at_cost_tickers"] == ["Y"], a["basis"]
    assert a["hold_twr"] is not None and a["hold_twr"] > 0.2, a["hold_twr"]   # X 的 +50% 沒被 Y 拖成 −100%
    # 平線=零報酬:Y 的 1000 成本在 V 裡但不產生報酬 → hold 介於 X 純報酬與 0 之間
    assert a["hold_twr"] < 0.5, a["hold_twr"]
    # V0 = X 1000 + 現金 1000(day10 買 Y 的錢回滾後 day0–9 還在現金桶)→ V_end = 1500 + Y 平線 1000
    assert abs(a["acct_twr"] - 0.25) < 1e-9, a["acct_twr"]
    assert abs(a["cash_drag"]) < 0.02, a["cash_drag"]     # 僅前 10 天的閒置現金,效應微小不失控

    # 變體:Z 在 px.columns 但首個有效價(day20)晚於首筆交易(day10)→ 同樣 at-cost
    # (yfinance 限流常見形態:列在、頭半段 NaN;買入落在 NaN 段=估不出,整檔平線)
    pz = _px_frame({"X": _lin(100, 150),
                    "Z": [float("nan")] * 20 + [200.0] * (len(IDX) - 20)})
    az = pf.account_perf([_row(0), _row(10, ticker="Z", qty=5.0, px=200.0)], pz,
                         [_cf(0, -1000, "trade"), _cf(10, -1000, "trade")],
                         _cashd("anchored", {"USD": _anch(0)}), {"X": "USD", "Z": "USD"})
    assert az["basis"]["at_cost_tickers"] == ["Z"], az["basis"]
    assert abs(az["acct_twr"] - 0.25) < 1e-9, az["acct_twr"]


def test_acct_no_trade_footprint_gated():
    """CSV 缺 Amount(交易無現金足跡)→ 現金史回滾必錯,帳戶柱誠實不出;hold 柱照出
    (它只吃 rows + 價格)。錨點在也救不了——這正是 ai_holder sweep 抓到的 8663% 假暴漲根因。"""
    px = _px_frame({"X": _lin(100, 150)})
    a = pf.account_perf([_row(0)], px, [_cf(100, 5000, "deposit")],
                        _cashd("anchored", {"USD": _anch(4000)}), {"X": "USD"})
    assert a["acct_twr"] is None and a["irr_annual"] is None, a
    assert "Amount" in (a["note"] or ""), a["note"]
    assert a["hold_twr"] is not None and abs(a["hold_twr"] - 0.5) < 1e-9, a["hold_twr"]


def test_acct_big_residual_gated_unlock_invite():
    """#180 大缺口 gate:殘差大到污染每天淨值(相對帳戶規模)→ 帳戶柱不出、hold 柱照出、
    note 給解鎖邀請。比照 broken_ccys 降 None 家族:算不出就不硬給、導向補齊。"""
    px = _px_frame({"X": _lin(100, 150)})
    resid = [{"currency": "USD", "start": "2024-01-02", "end": "2024-06-01",
              "prev_balance": 0.0, "next_balance": 500000.0,
              "flows_sum": 0.0, "residual": 500000.0}]          # 殘差 50 萬 >> 帳戶規模(~1500)
    a = pf.account_perf([_row(0)], px, [_cf(0, -1000, "trade")],
                        _cashd("anchored", {"USD": _anch(0)}), {"X": "USD"},
                        cash_residuals=resid)
    assert a["acct_twr"] is None and a["irr_annual"] is None, a
    assert a["hold_twr"] is not None and abs(a["hold_twr"] - 0.5) < 1e-9, a["hold_twr"]
    assert a["note"] and "解鎖" in a["note"], a["note"]


def test_acct_small_residual_not_gated():
    """小殘差(相對帳戶規模 < 閾值)→ 帳戶柱照出(不為小缺口撤整個帳戶視圖);殘差揭露走 honesty。"""
    px = _px_frame({"X": _lin(100, 150)})
    resid = [{"currency": "USD", "start": "2024-01-02", "end": "2024-06-01",
              "prev_balance": 1000.0, "next_balance": 1050.0,
              "flows_sum": 0.0, "residual": 50.0}]              # $50 對 ~$1500 帳戶 < 10%
    a = pf.account_perf([_row(0)], px, [_cf(0, -1000, "trade")],
                        _cashd("anchored", {"USD": _anch(0)}), {"X": "USD"},
                        cash_residuals=resid)
    assert a["acct_twr"] is not None, ("小缺口不該撤數字", a)


# ─────────────── H2. cash_reconcile_residuals(#180 多錨點對帳殘差純函式)───────────────
def _snap(as_of, **cash):
    return {"as_of": as_of, "cash": cash}


def _flow(d, amt, kind="trade", ccy="USD"):
    return dict(date=dt.date.fromisoformat(d), amount=float(amt), kind=kind, currency=ccy)


def test_residuals_reconciled_no_bark():
    """殘差=0(金流記全)→ 空清單。最重要的假陽性防線:記全的帳不能被誤報成漏記。"""
    snaps = [_snap("2026-06-01", USD=10000), _snap("2026-06-30", USD=40000)]
    flows = [_flow("2026-06-05", -2000), _flow("2026-06-10", 1000),
             _flow("2026-06-15", 31000, "deposit")]
    assert pf.cash_reconcile_residuals(snaps, flows) == []


def test_residuals_missing_deposit_quantified():
    """漏記入金 → 殘差 == 漏記額(#180 核心:漏記金額可精確量化)。"""
    snaps = [_snap("2026-06-01", USD=10000), _snap("2026-06-30", USD=40000)]
    flows = [_flow("2026-06-05", -2000), _flow("2026-06-10", 1000)]
    out = pf.cash_reconcile_residuals(snaps, flows)
    assert len(out) == 1 and abs(out[0]["residual"] - 31000) < 1e-9, out


def test_residuals_single_anchor_silent():
    """單錨點無相鄰對 → 空清單(不吠)。"""
    assert pf.cash_reconcile_residuals([_snap("2026-06-01", USD=10000)], []) == []


def test_residuals_missing_withdrawal_negative_abs():
    """漏記提款 → 負殘差;|殘差| 比較(殺『去掉 abs』突變:負殘差不能漏報)。"""
    snaps = [_snap("2026-06-01", USD=40000), _snap("2026-06-30", USD=8000)]
    out = pf.cash_reconcile_residuals(snaps, [])
    assert len(out) == 1 and abs(out[0]["residual"] + 32000) < 1e-9, out


def test_residuals_adjacent_only_not_all_pairs():
    """三錨點漏一筆 → 只報中段一筆(殺『相鄰改全對 → 同筆重複歸因報多筆』突變)。"""
    snaps = [_snap("2026-06-01", USD=10000), _snap("2026-06-30", USD=40000),
             _snap("2026-07-31", USD=40000)]
    out = pf.cash_reconcile_residuals(snaps, [])
    assert len(out) == 1 and out[0]["start"] == "2026-06-01", out


def test_residuals_absent_currency_not_zero():
    """缺席幣別≠0:TWD 只在第二個 snapshot 宣告 → 無相鄰對、不誤報(殺『缺席=0』突變)。"""
    snaps = [_snap("2026-06-01", USD=8200), _snap("2026-06-30", USD=8200, TWD=120000)]
    assert pf.cash_reconcile_residuals(snaps, []) == []


def test_residuals_gross_threshold_not_net():
    """閾值分母用 Σ|flow| 非淨流:大額入金+提款(淨$0)撐高閾值,$500 尾差在雜訊內不吠
    (殺『用淨流當分母 → 閾值趨零爆假殘差』突變:淨流版閾值=$50 會誤吠)。"""
    snaps = [_snap("2026-06-01", USD=10000), _snap("2026-06-30", USD=10500)]  # 殘差 500
    flows = [_flow("2026-06-10", 50000, "deposit"), _flow("2026-06-20", -50000, "withdrawal")]
    assert pf.cash_reconcile_residuals(snaps, flows) == []


def test_residuals_tie_break_latest_declaration():
    """同 as_of 多筆 → 取檔案序較後(較新宣告),對齊 ledger.latest_anchor;此例後者對得上 → 不吠。"""
    snaps = [_snap("2026-06-01", USD=10000), _snap("2026-06-01", USD=12000),
             _snap("2026-06-30", USD=12000)]
    assert pf.cash_reconcile_residuals(snaps, []) == []


# ─────────────────── 標準庫 runner(與 test_engine_units 一致)───────────────────

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
