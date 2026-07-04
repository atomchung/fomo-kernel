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


# ─────────────── C. prescribe:α 四分支(卡面「怎麼優化」主文案)───────────────

def _ab(port_tot, qqq_tot, soxx_tot, credible, excess=0.02):
    return {"credible": credible, "benchmarks": {
        "SPY": dict(beta=1.2, alpha_ann=0.01, port_tot=port_tot, bench_tot=port_tot - excess,
                    excess=excess, n=300),
        "QQQ": dict(beta=1.1, alpha_ann=0.0, port_tot=port_tot, bench_tot=qqq_tot,
                    excess=port_tot - qqq_tot, n=300),
        "SOXX": dict(beta=1.3, alpha_ann=0.0, port_tot=port_tot, bench_tot=soxx_tot,
                     excess=port_tot - soxx_tot, n=300),
    }}


def _kinds(rx):
    return [r["kind"] for r in rx]


def test_prescribe_not_credible_branch():
    rx = tr.prescribe(_ab(0.30, 0.20, 0.22, credible=False), [], {})
    assert "選股:資料不足以判定" in _kinds(rx), f"不 credible 必須誠實說判不出:{_kinds(rx)}"
    assert "外包短板(漸進)" not in _kinds(rx) and "揚長" not in _kinds(rx), \
        "不 credible 不准下外包/真 edge 定論"


def test_prescribe_outsource_branch():
    rx = tr.prescribe(_ab(0.10, 0.20, 0.20, credible=True), [], {})
    assert "外包短板(漸進)" in _kinds(rx), f"連中性 QQQ 都輸 −10pp 應給外包處方:{_kinds(rx)}"


def test_prescribe_true_edge_branch():
    rx = tr.prescribe(_ab(0.30, 0.20, 0.22, credible=True), [], {})
    assert "揚長" in _kinds(rx), f"贏 QQQ +10pp 且贏 SOXX +8pp 是真 edge:{_kinds(rx)}"


def test_prescribe_no_verdict_branch():
    rx = tr.prescribe(_ab(0.30, 0.20, 0.40, credible=True), [], {})
    assert "選股:目前無定論" in _kinds(rx), f"一正一負(vs QQQ +10 / vs SOXX −10)應無定論:{_kinds(rx)}"


def test_prescribe_excess_hypothesis_branch():
    rx = tr.prescribe(_ab(0.30, 0.20, 0.40, credible=True, excess=0.15), [], {})
    assert "揚長(假設,待驗證)" in _kinds(rx), f"贏大盤 +15pp 應給『押賽道假設』處方:{_kinds(rx)}"


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
