#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent-supplied price fallback (#289) — offline, deterministic.

背景:沙箱 host 抓不到 Yahoo(實測 `could not resolve host guce.yahoo.com`)時,所有
「有價格才活」的數字一起消失,而卡片照樣渲染成一張正常的績效卡——缺的那塊靜默不見。
#289 要的是兩件事:① degraded 必須可觀測(機讀原因 + 待補清單 + 卡面說得出是價格問題)
② 有補救路徑:agent 從公認資料源查回收盤價,按規格餵回來,損益依然算得出。

本檔的分工(對齊 test_price_paths「路徑該測、資料才 flaky」的原則):
- envelope 驗證是純函式 → 直接斷言每一條 fail-closed 邊界。
- 引擎端一律 subprocess + 假 yfinance shim 強制離線,本機裝不裝 yfinance 跑同一條路徑。

跑法:
  python3 tests/test_price_feed.py
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "skills", "fomo-kernel")
ENGINE = os.path.join(SKILL, "engine")
sys.path.insert(0, ENGINE)
import card_renderer  # noqa: E402
import price_feed as pf  # noqa: E402

TRADE_RECAP = os.path.join(ENGINE, "trade_recap.py")
REVIEW = os.path.join(ENGINE, "review.py")

_RESULTS = []


def ok(cond, label, detail=""):
    _RESULTS.append((bool(cond), label, detail))
    print(("  ✅ " if cond else "❌ FAIL: ") + label + ("" if cond else f"  {detail}"))


def raises(fn, fragment, label):
    try:
        fn()
    except pf.PriceFeedError as exc:
        ok(fragment in str(exc), label, f"訊息未含 {fragment!r}: {exc}")
    except Exception as exc:  # noqa: BLE001
        ok(False, label, f"預期 PriceFeedError,實際 {type(exc).__name__}: {exc}")
    else:
        ok(False, label, "預期拒收,實際通過")


# ─────────────────────────── fixtures ───────────────────────────

AS_OF = "2024-03-15"
CSV_ROWS = [
    "Symbol,Quantity,Price,Action,Description,TradeDate,SettledDate,Interest,Amount,Commission,Fee,CUSIP,RecordType",
    "NVDA,10,100.00,BUY,BOUGHT NVIDIA CORP,2024-01-10,2024-01-12,0,-1000.00,0,0,,Trade",
    "AMD,20,50.00,BUY,BOUGHT ADV MICRO DEVICES,2024-02-01,2024-02-05,0,-1000.00,0,0,,Trade",
    "NVDA,10,120.00,SELL,SOLD NVIDIA CORP,2024-03-10,2024-03-12,0,1200.00,0,0,,Trade",
]
# 已實現 = 10 × (120 − 100) = +200;持倉 AMD 20 股、成本 1000。
EXPECTED_REALIZED = 200.0
AMD_CLOSE = 60.0
EXPECTED_UNREALIZED = 20 * AMD_CLOSE - 1000.0        # = +200


def envelope(**overrides):
    base = {
        "as_of": AS_OF,
        "source": "Example Exchange official closing prices",
        "prices": [
            {"ticker": "AMD", "close": AMD_CLOSE, "date": AS_OF, "currency": "USD",
             "source": "https://example.invalid/amd"},
            {"ticker": "NVDA", "close": 130.0, "date": AS_OF, "currency": "USD"},
        ],
    }
    base.update(overrides)
    return base


def _business_days(start, end):
    days, day = [], dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    while day <= stop:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += dt.timedelta(days=1)
    return days


def series_envelope():
    """單日收盤 → 只夠算損益;帶日線 history → 基準/曲線/市場背景一起解鎖。"""
    days = _business_days("2023-12-15", AS_OF)
    def line(base, step):
        return [[day, round(base + step * i, 4)] for i, day in enumerate(days)]
    rows = []
    for ticker, base, step in (("AMD", 50.0, 0.16), ("NVDA", 100.0, 0.5),
                               ("SPY", 400.0, 1.0), ("QQQ", 350.0, 0.9),
                               ("SOXX", 180.0, 0.4), ("^VIX", 15.0, 0.02)):
        history = line(base, step)
        rows.append({"ticker": ticker, "close": history[-1][1], "date": history[-1][0],
                     "currency": "USD", "history": history})
    return {"as_of": AS_OF, "source": "Example Exchange daily closes", "prices": rows}


def write(tmp, name, payload):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def offline_shim(tmp):
    """假 yfinance(import 即 ImportError):不論本機裝沒裝,都走同一條抓不到價的路徑。"""
    shim = os.path.join(tmp, "shim")
    os.makedirs(shim, exist_ok=True)
    with open(os.path.join(shim, "yfinance.py"), "w", encoding="utf-8") as handle:
        handle.write('raise ImportError("offline shim: test_price_feed 強制離線")\n')
    return shim


def run_engine(tmp, csv_path, prices=None, expect=0):
    """TR_JSON 模式跑引擎,回 (returncode, card_or_None, stderr)。"""
    env = dict(os.environ, TR_JSON="1", TR_LEDGER=os.devnull,
               PYTHONPATH=offline_shim(tmp))
    env.pop("TR_PRICES", None)
    if prices:
        env["TR_PRICES"] = prices
    run = subprocess.run([sys.executable, TRADE_RECAP, csv_path], cwd=ENGINE,
                         env=env, capture_output=True, text=True, timeout=180)
    ok(run.returncode == expect, f"引擎 exit={expect}"
       + (" (供給價格檔)" if prices else " (無價格檔)"), run.stderr[-300:])
    card = json.loads(run.stdout) if run.returncode == 0 and run.stdout.strip() else None
    return run.returncode, card, run.stderr


def trades_csv(tmp):
    path = os.path.join(tmp, "trades.csv")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(CSV_ROWS) + "\n")
    return path


# ─────────────────────── 1. envelope 驗證(fail-closed)───────────────────────

def test_parse_contract():
    feed = pf.parse(envelope())
    ok(feed["as_of"] == AS_OF and feed["source"].startswith("Example"),
       "parse 保留 as_of / feed 級 source", repr(feed["as_of"]))
    ok(set(feed["prices"]) == {"AMD", "NVDA"}, "每檔一列", repr(sorted(feed["prices"])))
    ok(feed["prices"]["AMD"]["source"].endswith("/amd")
       and feed["prices"]["NVDA"]["source"].startswith("Example"),
       "per-row source 覆寫,缺省沿用 feed 級", repr(feed["prices"]["NVDA"]["source"]))
    ok(feed["coverage"] == "single_close", "只有收盤 → single_close 層級", feed["coverage"])
    ok(feed["prices"]["AMD"]["history"] == [(AS_OF, AMD_CLOSE)],
       "close 併入 history 成一點序列", repr(feed["prices"]["AMD"]["history"]))
    ok(pf.parse(series_envelope())["coverage"] == "daily_series",
       "帶 history → daily_series 層級")
    ok(pf.parse(envelope(prices=[{"ticker": "^TWII", "close": 23000.0, "date": AS_OF,
                                  "currency": "TWD"}]))["prices"]["^TWII"]["currency"] == "TWD",
       "指數符號(^ 開頭)與非 USD 幣別可用")


def test_parse_fails_closed():
    """價格是錢:寧可拒收整份,也不要靜默用半份算 P&L。"""
    raises(lambda: pf.parse({}), "as_of is required", "缺 as_of 拒收")
    raises(lambda: pf.parse(envelope(source="")), "source", "空 source 拒收(來源必須說得出)")
    raises(lambda: pf.parse(envelope(prices=[])), "non-empty", "空 prices 拒收")
    raises(lambda: pf.parse({**envelope(), "schema_version": 99}),
           "schema_version", "未知 schema_version 拒收")
    tomorrow = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    raises(lambda: pf.parse(envelope(as_of=tomorrow)), "future", "未來 as_of 拒收")
    raises(lambda: pf.parse(envelope(prices=[{"ticker": "AMD", "close": 0, "date": AS_OF,
                                              "currency": "USD"}])),
           "must be positive", "非正收盤價拒收")
    raises(lambda: pf.parse(envelope(prices=[{"ticker": "AMD", "close": 1.0,
                                              "date": "2024-03-20", "currency": "USD"}])),
           "after as_of", "晚於 as_of 的日期拒收")
    raises(lambda: pf.parse(envelope(prices=[{"ticker": "AMD", "close": 1.0, "date": AS_OF,
                                              "currency": "usd$"}])),
           "three-letter", "壞幣別碼拒收")
    raises(lambda: pf.parse(envelope(prices=[
        {"ticker": "AMD", "close": 1.0, "date": AS_OF, "currency": "USD"},
        {"ticker": "AMD", "close": 2.0, "date": AS_OF, "currency": "USD"}])),
        "twice", "同檔重複列拒收(哪個才算?不猜)")
    raises(lambda: pf.parse(envelope(prices=[
        {"ticker": "AMD", "close": 60.0, "date": AS_OF, "currency": "USD",
         "history": [[AS_OF, 61.0]]}])),
        "disagrees with close", "history 與 close 同日不一致拒收")
    raises(lambda: pf.parse({**envelope(), "fx": [{"currency": "USD", "usd_per_unit": 1.0,
                                                   "date": AS_OF}]}),
           "USD", "USD 匯率不得供給(恆 1.0)")
    raises(lambda: pf.parse({**envelope(), "fx": [{"currency": "TWD", "usd_per_unit": -1,
                                                   "date": AS_OF}]}),
           "must be positive", "非正匯率拒收")


def test_adapters():
    feed = pf.parse({**envelope(), "fx": [{"currency": "TWD", "usd_per_unit": 0.0307,
                                           "date": AS_OF}]})
    ok(pf.fx_rates(feed) == {"USD": 1.0, "TWD": 0.0307}, "fx_rates 帶 USD=1.0",
       repr(pf.fx_rates(feed)))
    frame, err = pf.to_frame(feed, ["AMD"])
    ok(err is None and list(frame.columns) == ["AMD"] and len(frame.index) == 1,
       "to_frame 只給要求的欄位", repr(None if frame is None else list(frame.columns)))
    import trade_recap as tr
    ok(tr.last_prices(frame) == {"AMD": AMD_CLOSE}, "單日框可被 last_prices 消費",
       repr(tr.last_prices(frame)))
    _, err = pf.to_frame(feed, ["ZZZZ"])
    ok(err is not None, "要求的標的完全沒被涵蓋 → 誠實回錯誤而非空框", repr(err))
    splits = pf.parse(envelope(prices=[{"ticker": "NVDA", "close": 130.0, "date": AS_OF,
                                        "currency": "USD",
                                        "splits": [["2024-02-15", 10]]}]))
    ok(pf.splits_map(splits) == {"NVDA": [(dt.date(2024, 2, 15), 10.0)]},
       "splits_map 對齊 fetch_splits 形狀", repr(pf.splits_map(splits)))
    raises(lambda: pf.parse(envelope(prices=[{"ticker": "NVDA", "close": 130.0, "date": AS_OF,
                                              "currency": "USD",
                                              "splits": [["2024-06-10", 10]]}])),
           "after as_of", "as_of 之後的分割拒收(還沒發生,不可能已折進收盤價)")
    ok(pf.splits_map(pf.parse(envelope())) == {}, "沒宣告分割 → 空 map(不調整,同離線降級)")
    conflicts = pf.currency_conflicts(pf.parse(envelope()), {"AMD": "TWD", "NVDA": "USD"})
    ok([row["ticker"] for row in conflicts] == ["AMD"], "幣別衝突逐檔指認", repr(conflicts))
    ok(pf.currency_conflicts(pf.parse(envelope()), {"AMD": "USD"}) == [],
       "幣別一致 → 無衝突;交易紀錄沒有的標的(基準)不比對")


def test_plausibility_flags():
    """#330:純函式層——supplied close vs 最近一次真實成交價的軟性合理性複核。
    band=20x(pf.PLAUSIBILITY_BAND),嚴格大於才觸發;方向雙向(暴漲/暴跌都算);
    沒有交易錨點(基準,不在持倉裡)一律跳過,不因缺資料誤判。"""
    feed = pf.parse(envelope())   # AMD close=60.0(AMD_CLOSE),NVDA close=130.0
    ok(pf.PLAUSIBILITY_BAND == 20.0, "預設 band = 20x(#330 選定值)", pf.PLAUSIBILITY_BAND)

    normal = pf.plausibility_flags(feed, {"AMD": (50.0, dt.date(2024, 2, 1)),
                                          "NVDA": (120.0, dt.date(2024, 3, 10))})
    ok(normal == [], "落差在 band 內(含真實漲跌)→ 不揭露", repr(normal))

    feed3 = pf.parse(envelope(prices=[
        {"ticker": "AMD", "close": AMD_CLOSE, "date": AS_OF, "currency": "USD"},
        {"ticker": "NVDA", "close": 130.0, "date": AS_OF, "currency": "USD"},
        {"ticker": "SPY", "close": 4000.0, "date": AS_OF, "currency": "USD"}]))
    skipped = pf.plausibility_flags(feed3, {"AMD": (50.0, dt.date(2024, 2, 1)),
                                            "NVDA": (120.0, dt.date(2024, 3, 10))})
    ok(skipped == [], "SPY 在餵入的價格檔裡但不在交易紀錄裡(基準)→ 沒錨點可比,略過不誤判",
       repr(skipped))

    at_band = pf.plausibility_flags(feed, {"AMD": (3.0, dt.date(2023, 1, 5)),
                                           "NVDA": (120.0, dt.date(2024, 3, 10))})
    ok(60.0 / 3.0 == 20.0 and at_band == [],
       "恰好等於 band(60/3=20.0)不觸發——嚴格大於才算", repr(at_band))

    just_over = pf.plausibility_flags(feed, {"AMD": (2.99, dt.date(2023, 1, 5)),
                                             "NVDA": (120.0, dt.date(2024, 3, 10))})
    ok([row["ticker"] for row in just_over] == ["AMD"],
       "剛超過 band(60/2.99≈20.07)→ 揭露", repr(just_over))
    ok(just_over and just_over[0]["last_trade_price"] == 2.99
       and just_over[0]["feed_close"] == AMD_CLOSE
       and just_over[0]["ratio"] == round(AMD_CLOSE / 2.99, 2),
       "回傳落差比與錨點原值,供 agent 寫話用", repr(just_over))

    crash = pf.plausibility_flags(feed, {"AMD": (50.0, dt.date(2024, 2, 1)),
                                         "NVDA": (3000.0, dt.date(2024, 3, 10))})
    ok([row["ticker"] for row in crash] == ["NVDA"],
       "反向(供給價遠低於最近成交價)一樣揭露,不是只抓暴漲", repr(crash))
    ok(crash and crash[0]["ratio"] == round(3000.0 / 130.0, 2),
       "跌方向 ratio 用倒數,恆 >1", repr(crash))

    both = pf.plausibility_flags(feed, {"AMD": (2.0, dt.date(2023, 1, 5)),
                                        "NVDA": (3000.0, dt.date(2024, 3, 10))})
    ok([row["ticker"] for row in both] == ["AMD", "NVDA"],
       "兩檔同時觸發 → 依 ticker 排序,非插入序", repr(both))

    widened = pf.plausibility_flags(feed, {"AMD": (2.0, dt.date(2023, 1, 5))}, band=100.0)
    ok(widened == [], "band 可由呼叫端調寬,同一落差不再觸發", repr(widened))

    ok(pf.plausibility_flags(feed, {}) == [], "沒有任何錨點 → 全部略過,不拋例外")
    ok(pf.plausibility_flags(None, {"AMD": (50.0, dt.date(2024, 2, 1))}) == [],
       "feed 為 None(未供給價格)→ 空清單,永不拋例外")


# ─────────────────── 2. 引擎:無價格 → degraded 可觀測 ───────────────────

def test_engine_without_prices_stays_observable():
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        _, card, _ = run_engine(tmp, trades_csv(tmp))
        prov = card["price_provenance"]
        ok(prov["mode"] == "unavailable", "抓不到價 → mode=unavailable", repr(prov))
        ok(prov["error"], "保留機讀失敗原因", repr(prov.get("error")))
        ok(prov["coverage"] == {"requested_n": 2, "priced_n": 0, "missing": ["AMD", "NVDA"]},
           "覆蓋率如實:0/2", repr(prov["coverage"]))
        keys = [row["key"] for row in card["honesty_ledger"]]
        ok("price_source" in keys, "honesty 觸發 price_source(缺價不再靜默)", repr(keys))
        ok(keys.index("price_source") < keys.index("unrealized_coverage"),
           "因(價格來源)排在果(未實現覆蓋)之前", repr(keys))
        status = [row["status"] for row in card["honesty_ledger"] if row["key"] == "price_source"]
        ok(status == ["unavailable"], "status 指認資料可得性故障", repr(status))
        request = card["price_request"]
        ok(request and request["tickers"] == ["AMD", "NVDA"],
           "待補清單列出缺價標的", repr(request))
        ok(request["benchmarks"] and request["window"]["end"] and request["history_from"],
           "待補清單含基準、窗口與 history 起點", repr(request))
        ok(card["overview"]["realized"] == EXPECTED_REALIZED,
           "已實現不受影響(不靠現價)", repr(card["overview"]["realized"]))
        ok(card["overview"]["unrealized_coverage"]["priced_n"] == 0,
           "未實現覆蓋率誠實為 0,不用已實現冒充組合報酬")


# ─────────────────── 3. 引擎:供給價格 → 損益回來 ───────────────────

def test_engine_with_supplied_close_restores_pnl():
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        path = write(tmp, "prices.json", envelope())
        _, card, meta = run_engine(tmp, trades_csv(tmp), prices=path)
        overview = card["overview"]
        ok(overview["unrealized"] == EXPECTED_UNREALIZED,
           f"未實現損益回來 = {EXPECTED_UNREALIZED}", repr(overview["unrealized"]))
        ok(overview["total_pnl"] == EXPECTED_REALIZED + EXPECTED_UNREALIZED,
           "總損益 = 已實現 + 未實現", repr(overview["total_pnl"]))
        ok(overview["unrealized_coverage"]["unpriced"] == [], "持倉全被定價")
        prov = card["price_provenance"]
        ok(prov["mode"] == "agent_feed" and prov["as_of"] == AS_OF,
           "mode=agent_feed 且帶 as_of", repr(prov)[:160])
        ok(prov["source"].startswith("Example") and prov["series"] == "single_close",
           "provenance 記來源與覆蓋層級", repr(prov.get("source")))
        ok(prov["sources_by_ticker"]["AMD"].endswith("/amd"),
           "逐檔來源可回溯", repr(prov.get("sources_by_ticker")))
        entry = [row for row in card["honesty_ledger"] if row["key"] == "price_source"]
        ok(entry and entry[0]["status"] == "agent_feed",
           "供給來源仍必須揭露(不是引擎自己抓的)", repr(entry))
        ok("unrealized_coverage" not in [row["key"] for row in card["honesty_ledger"]],
           "全覆蓋 → 不再宣告未實現有洞")
        ok("供給價格檔" in meta, "meta 一行說得出價格來自供給檔", meta[:160])


def test_engine_flags_implausible_supplied_close():
    """#330:supplied close 遠超 band(這裡用 AMD 最近一次真實成交價的 25 倍)→ 加揭露,
    但仍照樣定價,不是第二個 fail-closed 關卡——跟 currency_conflicts(#289 既有的硬擋)
    刻意不同待遇:軟性複核只加話術,不擋復盤跑完。"""
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        implausible_close = 50.0 * 25   # AMD 最近一次真實成交價 50.0 → 25x,band(20x)之外
        path = write(tmp, "prices.json", envelope(prices=[
            {"ticker": "AMD", "close": implausible_close, "date": AS_OF, "currency": "USD"},
            {"ticker": "NVDA", "close": 130.0, "date": AS_OF, "currency": "USD"}]))
        rc, card, stderr = run_engine(tmp, trades_csv(tmp), prices=path)
        ok(rc == 0, "遠超 band 的供給價不擋跑——軟性揭露,非第二道 fail-closed", stderr[-200:])
        prov = card["price_provenance"]
        ok(prov["mode"] == "agent_feed", "價格仍照常視為供給成功", repr(prov)[:160])
        entry = [row for row in card["honesty_ledger"] if row["key"] == "price_plausibility"]
        ok(entry and entry[0]["status"] == "suspect" and entry[0]["data"]["tickers"] == ["AMD"],
           "觸發 price_plausibility,指名 AMD", repr(entry))
        detail = (entry[0]["data"]["details"][0] if entry and entry[0]["data"]["details"] else {})
        ok(detail.get("ticker") == "AMD" and detail.get("feed_close") == implausible_close
           and detail.get("last_trade_price") == 50.0,
           "細節帶供給收盤價與最近成交價原值,供 agent 寫話用", repr(detail))
        ok(card["overview"]["unrealized"] == 20 * implausible_close - 1000.0,
           "揭露之外,這個價照樣被拿去算未實現損益——fail-open,不是第二個 reject",
           repr(card["overview"]["unrealized"]))


def test_engine_does_not_flag_a_plausible_multibagger():
    """#330:band 選 20x 就是為了讓這種真實大漲(15x)照樣通過,不誤判成假資料——
    這正是 issue 點名『不能因為挑到合理的大漲就 false-positive』的案例。"""
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        multibagger_close = 50.0 * 15   # 15x,band(20x)之內——真實的大漲一樣要能定價
        path = write(tmp, "prices.json", envelope(prices=[
            {"ticker": "AMD", "close": multibagger_close, "date": AS_OF, "currency": "USD"},
            {"ticker": "NVDA", "close": 130.0, "date": AS_OF, "currency": "USD"}]))
        rc, card, stderr = run_engine(tmp, trades_csv(tmp), prices=path)
        ok(rc == 0, "正常供給價 exit 0", stderr[-200:])
        keys = [row["key"] for row in card["honesty_ledger"]]
        ok("price_plausibility" not in keys,
           "15x 在 band 內 → 不觸發,不能讓真實的大漲被當成可疑資料", repr(keys))
        ok(card["overview"]["unrealized"] == 20 * multibagger_close - 1000.0,
           "大漲照樣正確定價,不因『看起來誇張』被打折或攔下",
           repr(card["overview"]["unrealized"]))


def test_engine_with_series_unlocks_benchmarks():
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        path = write(tmp, "prices.json", series_envelope())
        _, card, _ = run_engine(tmp, trades_csv(tmp), prices=path)
        prov = card["price_provenance"]
        ok(prov["series"] == "daily_series", "日線層級", repr(prov.get("series")))
        ok("SPY" in prov["benchmarks_priced"], "基準被定價", repr(prov["benchmarks_priced"]))
        ok(card["price_request"] is None, "全部補齊 → 不再有待補清單",
           repr(card["price_request"]))
        points = (card.get("pnl_curve") or {}).get("points")
        ok(points and len(points) > 1, "損益曲線需要序列,單日層級畫不出、日線層級畫得出",
           repr(card.get("pnl_curve"))[:120])


def test_engine_does_not_claim_supply_that_covered_nothing():
    """符號寫錯的 envelope:餵了 ≠ 有價。不准對外宣稱價格已由外部供給。"""
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        path = write(tmp, "wrong.json", envelope(prices=[
            {"ticker": "WRONG", "close": 10.0, "date": AS_OF, "currency": "USD"}]))
        _, card, _ = run_engine(tmp, trades_csv(tmp), prices=path)
        prov = card["price_provenance"]
        ok(prov["mode"] == "unavailable",
           "envelope 一檔都對不上 → 仍是 unavailable,不冒充 agent_feed", repr(prov)[:170])
        ok(prov["error"], "說明是哪一種失敗", repr(prov.get("error")))
        ok(card["price_request"]["tickers"] == ["AMD", "NVDA"], "待補清單照舊列出真正要的檔")


def test_engine_fails_closed_on_bad_feed():
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        csv_path = trades_csv(tmp)
        bad = write(tmp, "bad.json", envelope(prices=[
            {"ticker": "AMD", "close": -1, "date": AS_OF, "currency": "USD"}]))
        _, _, stderr = run_engine(tmp, csv_path, prices=bad, expect=1)
        ok("must be positive" in stderr, "壞 envelope 指名欄位後拒跑", stderr[-200:])
        mismatch = write(tmp, "ccy.json", envelope(prices=[
            {"ticker": "AMD", "close": 60.0, "date": AS_OF, "currency": "TWD"}]))
        _, _, stderr = run_engine(tmp, csv_path, prices=mismatch, expect=1)
        ok("AMD" in stderr and "TWD" in stderr,
           "幣別與交易紀錄不符 → fail closed(不拿 TWD 收盤價算 USD 成本的損益)", stderr[-200:])


# ─────────────────── 4. review.py:manifest 與 fingerprint ───────────────────

def run_prepare(tmp, csv_path, root, prices=None, expect=0):
    env = dict(os.environ, PYTHONPATH=offline_shim(tmp))
    env.pop("TR_PRICES", None)
    argv = [sys.executable, REVIEW, "prepare", csv_path, "--root", root, "--language", "en"]
    if prices:
        argv += ["--prices", prices]
    run = subprocess.run(argv, cwd=ENGINE, env=env, capture_output=True, text=True, timeout=300)
    ok(run.returncode == expect, f"prepare exit={expect}", (run.stdout + run.stderr)[-300:])
    return json.loads(run.stdout) if run.stdout.strip() else {}


def test_prepare_surfaces_and_consumes_the_manifest():
    with tempfile.TemporaryDirectory(prefix="fomo-pf-") as tmp:
        csv_path, root = trades_csv(tmp), os.path.join(tmp, "root")
        degraded = run_prepare(tmp, csv_path, root)
        feed_status = degraded["review_plan"]["input"]["price_feed"]
        ok(feed_status["provenance"]["mode"] == "unavailable",
           "prepare 把價格可得性攤在 input.price_feed", repr(feed_status["provenance"])[:150])
        ok(feed_status["request"]["tickers"] == ["AMD", "NVDA"],
           "manifest 進 Review Plan", repr(feed_status.get("request"))[:150])
        ok("--prices" in degraded["next_action"] and "never invent" in degraded["next_action"].lower(),
           "next_action 指出補救路徑,同時封死編價格這條", degraded["next_action"][:200])
        ok("price_source" in degraded["review_plan"]["card_plan"]["required_honesty_keys"],
           "degraded 時 agent 必須為 price_source 寫一句話")

        supplied = run_prepare(tmp, csv_path, root, prices=write(tmp, "p.json", envelope()))
        ok(supplied["session_id"] != degraded["session_id"],
           "補價後是新 session,不會被 fingerprint 撞回缺價那場",
           f"{degraded['session_id']} vs {supplied['session_id']}")
        ok(supplied["review_plan"]["input"]["price_feed"]["provenance"]["mode"] == "agent_feed",
           "第二次 prepare 真的吃到供給的價")
        ok(supplied["review_plan"]["input"]["fingerprint"]
           != degraded["review_plan"]["input"]["fingerprint"],
           "價格 envelope 進 fingerprint")

        bad = write(tmp, "bad.json", envelope(prices=[
            {"ticker": "AMD", "close": 0, "date": AS_OF, "currency": "USD"}]))
        rejected = run_prepare(tmp, csv_path, root, prices=bad, expect=2)
        ok(rejected.get("status") == "error" and "price feed rejected" in rejected.get("error", ""),
           "壞 envelope 在跑引擎前就被擋下", repr(rejected)[:200])


# ─────────────────── 5. 卡面:缺價要說是缺價 ───────────────────

def test_card_names_price_availability_as_the_blocker():
    blocked = {"price_provenance": {"mode": "unavailable",
                                    "coverage": {"requested_n": 2, "priced_n": 0}}}
    ok(card_renderer.price_retrieval_blocked(blocked), "全缺價 → 判定為價格阻斷")
    ok(not card_renderer.price_retrieval_blocked({}), "舊 bundle 無 provenance → 沿用原措辭")
    ok(not card_renderer.price_retrieval_blocked(
        {"price_provenance": {"mode": "agent_feed",
                              "coverage": {"requested_n": 2, "priced_n": 2}}}),
       "價補回來了就不是價格阻斷")
    # 全缺價時 engine 的 unrealized=0 是「沒量到」不是「沒賺賠」——卡面不准把它印成 0。
    blackout = {"unrealized_coverage": {"held_n": 2, "priced_n": 0, "unpriced": ["AMD", "NVDA"]}}
    ok(not card_renderer.unrealized_is_measured(blackout),
       "持倉全無現價 → 未實現視為未量測")
    ok(card_renderer.unrealized_is_measured(
        {"unrealized_coverage": {"held_n": 2, "priced_n": 1}}),
       "部分有價 → 是真數字(缺口由 unrealized_coverage 揭露)")
    ok(card_renderer.unrealized_is_measured(
        {"unrealized_coverage": {"held_n": 0, "priced_n": 0}}),
       "沒有持倉 → 未實現 0 是真的 0")
    card = {"overview": {"total_pnl": 200.0, "realized": 200.0, "unrealized": 0.0, **blackout},
            "currency_meta": {"aggregate_currency": "USD", "mixed": False}}
    text = " ".join(card_renderer._overview_lines(card, "en"))
    ok("not scored" in text and "unrealized" not in text.split("not scored")[0].lower()
       .replace("current unrealized", ""),
       "缺價卡面說『未評分』,不印 0 也不把已實現貼上 Total 標籤", text)
    ok("Total P&L" not in text, "全缺價時不出現 Total P&L 標籤", text)
    for language in ("en", "zh-TW"):
        copy = card_renderer.load_copy(language)
        missing = copy["block_missing"]
        ok(missing["annualized_prices"] != missing["annualized"],
           f"{language}: 缺價與缺現金錨點是兩句不同的話")
        ok(missing["vs_market_prices"] != missing["vs_market"],
           f"{language}: 缺價與缺基準序列是兩句不同的話")
        ok(copy["honesty"].get("price_source"), f"{language}: price_source 有 fallback 文案")
        ok(copy["honesty"].get("price_plausibility"),
           f"{language}: price_plausibility 有 fallback 文案(#330)")


# ─────────── 6. degraded session_id 決定性:error 存穩定代碼、不存原文 ───────────

def test_degraded_error_normalizes_so_session_id_stays_deterministic():
    """#289 review finding 4:price_provenance['error'] 不可把 yfinance 原文帶進 state。
    原文常含 volatile object repr(記憶體位址、每次重試不同的 host),而 price_provenance
    進 engine state、state 的 sha256 決定 session_id_from_state();同一種失敗的兩次
    degraded 收尾若算出不同 id,already_committed 重偵測就失效。provenance() 只存穩定
    reason code,原文只走 stderr。"""
    import ledger  # noqa: E402  (ENGINE 已在 sys.path)

    # 同一類(HTTP transport 失敗),volatile 細節不同(object repr + host + 狀態碼)。
    err_a = ("yfinance 下載失敗: HTTPError('query1.finance.yahoo.com', 503, "
             "<urllib3.connectionpool.HTTPSConnectionPool object at 0x10a3f9d80>)")
    err_b = ("yfinance 下載失敗: HTTPError('query2.finance.yahoo.com', 502, "
             "<urllib3.connectionpool.HTTPSConnectionPool object at 0x7fb2c1e40>)")
    prov_a = pf.provenance(mode="unavailable", error=err_a, requested=["AMD", "NVDA"])
    prov_b = pf.provenance(mode="unavailable", error=err_b, requested=["AMD", "NVDA"])
    ok(prov_a["error"] == prov_b["error"] == "http_error",
       "同類失敗(不同 volatile 原文)→ 同一穩定代碼", f"{prov_a['error']} vs {prov_b['error']}")
    ok(prov_a == prov_b, "整個 provenance 記錄逐 byte 相同,volatile 原文不入 state")

    def state_with(prov):
        return {"date_end": "2024-03-15", "schema_version": 5, "price_provenance": prov}
    ok(ledger.session_id_from_state(state_with(prov_a))
       == ledger.session_id_from_state(state_with(prov_b)),
       "→ 兩次 degraded 收尾 session_id_from_state 判為同一 session",
       f"{ledger.session_id_from_state(state_with(prov_a))} vs "
       f"{ledger.session_id_from_state(state_with(prov_b))}")
    # 反向:真的不同類的失敗仍算出不同 id(正規化沒有把所有失敗抹成同一個)。
    prov_dns = pf.provenance(mode="unavailable", error="could not resolve host guce.yahoo.com",
                             requested=["AMD", "NVDA"])
    ok(ledger.session_id_from_state(state_with(prov_dns))
       != ledger.session_id_from_state(state_with(prov_a)),
       "不同類失敗(dns vs http)→ 不同 id,代碼有區辨力")

    # 各類 canonical 失敗各自對到自己的代碼;無法辨識的字串是 'unknown',絕不外洩原文。
    ok(pf.classify_error("curl error 6: Could not resolve host guce.yahoo.com") == "dns_failure",
       "could not resolve host → dns_failure")
    ok(pf.classify_error("HTTPSConnectionPool: Read timed out. (read timeout=10)") == "timeout",
       "read timed out → timeout")
    ok(pf.classify_error("yfinance 未安裝") == "client_missing", "缺 client → client_missing")
    ok(pf.classify_error("price feed covers none of the requested instruments") == "no_data",
       "envelope 對不上 → no_data")
    ok(pf.classify_error("some brand-new failure nobody has a rule for") == "unknown",
       "未知字串 → 'unknown'(不外洩原文)")
    ok(pf.classify_error(None) is None, "沒有 error → None")


def main():
    for fn in (test_parse_contract, test_parse_fails_closed, test_adapters,
               test_plausibility_flags,
               test_engine_without_prices_stays_observable,
               test_engine_with_supplied_close_restores_pnl,
               test_engine_flags_implausible_supplied_close,
               test_engine_does_not_flag_a_plausible_multibagger,
               test_engine_with_series_unlocks_benchmarks,
               test_engine_does_not_claim_supply_that_covered_nothing,
               test_engine_fails_closed_on_bad_feed,
               test_prepare_surfaces_and_consumes_the_manifest,
               test_card_names_price_availability_as_the_blocker,
               test_degraded_error_normalizes_so_session_id_stays_deterministic):
        print(f"\n── {fn.__name__} ──")
        fn()
    failed = [row for row in _RESULTS if not row[0]]
    print(f"\n{'✅ 供給式價格 fallback 測試全過' if not failed else '❌ 失敗'}"
          f"（{len(_RESULTS) - len(failed)}/{len(_RESULTS)} 項）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
