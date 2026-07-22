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


def main():
    for fn in (test_parse_contract, test_parse_fails_closed, test_adapters,
               test_engine_without_prices_stays_observable,
               test_engine_with_supplied_close_restores_pnl,
               test_engine_with_series_unlocks_benchmarks,
               test_engine_does_not_claim_supply_that_covered_nothing,
               test_engine_fails_closed_on_bad_feed,
               test_prepare_surfaces_and_consumes_the_manifest,
               test_card_names_price_availability_as_the_blocker):
        print(f"\n── {fn.__name__} ──")
        fn()
    failed = [row for row in _RESULTS if not row[0]]
    print(f"\n{'✅ 供給式價格 fallback 測試全過' if not failed else '❌ 失敗'}"
          f"（{len(_RESULTS) - len(failed)}/{len(_RESULTS)} 項）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
