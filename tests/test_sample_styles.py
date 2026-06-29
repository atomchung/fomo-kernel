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

TIER_W = {1: 1.0, 2: 0.7}        # 與 engine render() 的排序權重一致
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
    """複製 engine render() 的選卡邏輯:triggered 中按 severity × tier 權重取第一。"""
    trig = sorted((d for d in dims if d["triggered"]),
                  key=lambda d: d["severity"] * TIER_W[d["tier"]], reverse=True)
    return trig[0]["dim"] if trig else None


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


def test_momentum_top_hole_is_sizing_or_concentration():
    """動能衝衝衝 → 頭號洞 = 梭哈(部位 sizing)或假分散(同一 driver)。"""
    s = _dims("momentum")
    assert _top_hole(s["dims"]) in ("部位 sizing", "分散")
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
