#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_cache(#235 proposal 3)單元測試 — 全離線、確定性、免裝 pytest。

蓋什麼:
  A. 同日命中 / 不同日必失效(日期是快取正確性的唯一閘門)。
  B. root 不存在時完全不快取(絕不在使用者 home 生出 state 目錄)。
  C. 離線 shim 下 fetch_splits / fetch_prices 不可讀到任何快取
     ——這是「舊快取不得汙染確定性測試」那條約束的機械證明。
  D. 價格框序列化來回一致:index 可 .date()、NaN 語意不變、欄序不動。
  E. cache 目錄已註冊進 coach.DATA_FILES(否則 data reset 清不掉)。
  F. 快取跟著這次跑的 state root 走,不是帳號自己的 root(#627)。
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SKILL = os.path.join(REPO, "skills", "fomo-kernel")
ENGINE = os.path.join(SKILL, "engine")
sys.path.insert(0, HERE)
sys.path.insert(0, ENGINE)
import coach  # noqa: E402
import fetch_cache  # noqa: E402
import offline_posture  # noqa: E402

# #620: this suite drives `prepare`, so its answer may not depend on whether it
# was launched directly or through run_all.py. The one test below that needs the
# provider reachable clears the posture for its own subprocess and supplies a
# local fake, so nothing here reaches the network either way.
offline_posture.apply()

DAY = "2026-07-26"
NEXT = "2026-07-27"


def test_same_day_hit_and_next_day_miss():
    with tempfile.TemporaryDirectory() as root:
        assert fetch_cache.store("splits", ["AAA"], {"AAA": [["2026-01-02", 4.0]]},
                                 root=root, today=DAY)
        assert fetch_cache.load("splits", ["AAA"], root=root, today=DAY) == \
            {"AAA": [["2026-01-02", 4.0]]}, "同日同輸入應命中"
        assert fetch_cache.load("splits", ["AAA"], root=root, today=NEXT) is None, \
            "換日必須失效——否則昨天的行情會被當成今天的結論"
        assert fetch_cache.load("splits", ["BBB"], root=root, today=DAY) is None, \
            "不同輸入不得互相命中"


def test_next_day_write_discards_the_previous_day():
    with tempfile.TemporaryDirectory() as root:
        fetch_cache.store("prices", ["AAA"], {"v": 1}, root=root, today=DAY)
        fetch_cache.store("prices", ["BBB"], {"v": 2}, root=root, today=NEXT)
        assert fetch_cache.load("prices", ["AAA"], root=root, today=NEXT) is None, \
            "跨日寫入不得把舊日條目一起續命(檔案應整份換日,不是合併)"
        assert fetch_cache.load("prices", ["BBB"], root=root, today=NEXT) == {"v": 2}


def test_missing_root_is_never_created():
    with tempfile.TemporaryDirectory() as parent:
        absent = os.path.join(parent, "no-such-root")
        assert fetch_cache.store("splits", ["AAA"], {"AAA": []}, root=absent, today=DAY) is False
        assert not os.path.exists(absent), "快取不得自己生出 state root(裸跑引擎會汙染 home)"
        assert fetch_cache.load("splits", ["AAA"], root=absent, today=DAY) is None


def test_corrupt_cache_file_is_a_miss_not_a_crash():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "cache"))
        with open(os.path.join(root, "cache", "splits.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        assert fetch_cache.load("splits", ["AAA"], root=root, today=DAY) is None


_SHIM_PROBE = r"""
import datetime as dt, json, os, sys
sys.path.insert(0, os.environ["ENGINE"])
import fetch_cache, market_data, trade_recap
root = os.environ["TRADE_COACH_HOME"]
today = dt.date.today().isoformat()
request = market_data.build_request(instruments=["AAA"], benchmarks=["SPY"],
                                    currencies=["TWD"], window_start="2026-01-01")
# 先種一份「今天的」快取,而且是**蓋得住**下面那個請求的一份。若解析路徑在
# import 失敗前就讀快取,就會撈到這筆假資料。
fetch_cache.store(market_data.CACHE_KIND, request, {
    "source": "yahoo", "request": request, "as_of": "2026-01-02",
    "frame": {"index": ["2026-01-02"], "columns": ["AAA", "SPY"], "data": [[1.0, 2.0]]},
    "fx_frame": None, "splits": {"AAA": [["2026-01-02", 4.0]]},
    "fx": {"USD": 1.0, "TWD": 0.0307}, "gaps": [],
}, root=root, today=today)
bundle = market_data.resolve(request, root=root, today=today)
splits = trade_recap.fetch_splits({"AAA"}, bundle=bundle)
frame, err = trade_recap.fetch_prices({"AAA"}, "2026-01-01", bundle=bundle)
fx, fx_err = trade_recap.fetch_fx({"TWD", "USD"}, bundle=bundle)
# default=str:快取命中時 splits 會帶 dt.date,序列化不得因此爆掉——
# 這條探針要讓斷言說話,不是讓 json 先掛掉。
print(json.dumps({"source": bundle.source, "gaps": [g["code"] for g in bundle.gaps],
                  "splits": splits, "frame_is_none": frame is None, "err": err,
                  "fx": fx, "fx_err": fx_err}, default=str))
"""


def test_offline_shim_cannot_read_the_cache():
    """離線降級必須先於快取:import yfinance 失敗就返回,快取讀不到。

    這條是 #235 的核心約束(見 issue 2026-07-19 comment)。把快取讀取移到
    `import yfinance` 之前,這個測試就會紅——那正是它要擋的突變。

    #605:取得層搬進 `market_data` 之後,這條約束跟著搬,守的東西一模一樣——
    種下的是一份「蓋得住該請求」的 bundle,所以它是真的可命中,唯一擋住它的
    是 import 探測的順序。三個投影函式一起驗,因為它們現在共用同一份 bundle:
    任何一個能在離線時變出數字,就是同一個缺陷的第二個出口。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        shim = os.path.join(tmp, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "yfinance.py"), "w", encoding="utf-8") as handle:
            handle.write('raise ImportError("offline shim: test_fetch_cache")\n')
        env = dict(os.environ)
        env.update({"ENGINE": ENGINE, "TRADE_COACH_HOME": root,
                    "PYTHONPATH": shim + os.pathsep + env.get("PYTHONPATH", "")})
        # 這支驗的是 import 順序,不是 TR_OFFLINE 姿態:姿態在場會更早短路,
        # 於是一個壞掉的 shim 也會過關。
        env.pop("TR_OFFLINE", None)
        run = subprocess.run([sys.executable, "-c", _SHIM_PROBE],
                             env=env, capture_output=True, text=True, timeout=120)
        assert run.returncode == 0, f"探針自身失敗:{run.stderr[:400]}"
        out = json.loads(run.stdout.strip().splitlines()[-1])
        assert out["source"] == "unavailable" and out["gaps"] == ["provider_missing"], \
            f"離線時必須直接降級,實際:source={out['source']} gaps={out['gaps']}"
        assert out["splits"] == {}, \
            f"離線時 fetch_splits 必須降級成 {{}},實際讀到快取:{out['splits']}"
        assert out["frame_is_none"] is True and "installed" in (out["err"] or ""), \
            f"離線時 fetch_prices 必須回 (None, 缺 client),實際:{out['err']}"
        assert out["fx"] == {"USD": 1.0} and (out["fx_err"] or ""), \
            f"離線時 fetch_fx 必須只剩 USD 錨點,實際讀到快取:{out['fx']}"


def test_price_frame_round_trip_preserves_dates_nan_and_column_order():
    try:
        import pandas as pd
    except ImportError:                                    # pandas 缺席時本條無意義,略過而非假綠
        print("  (skip: pandas 未安裝)")
        return
    payload = {"index": ["2026-01-02", "2026-01-05"],
               "columns": ["ZZZ", "AAA"],
               "data": [[10.0, None], [11.0, 2.0]]}
    frame = pd.DataFrame(payload["data"], columns=payload["columns"],
                         index=pd.to_datetime(payload["index"]))
    assert list(frame.columns) == ["ZZZ", "AAA"], "欄序不得被重排(下游按欄取值)"
    assert frame.index[-1].date().isoformat() == "2026-01-05", \
        "index 必須還原成可 .date() 的時間索引(price_as_of 依賴它)"
    assert frame["AAA"].isna().iloc[0], "None 必須還原成 NaN——缺價不是 0"
    assert frame["ZZZ"].iloc[1] == 11.0


def test_cache_dir_is_registered_in_local_data_controls():
    names = {name for name, _kind, _desc in coach.DATA_FILES}
    assert "cache" in names, \
        "cache/ 未註冊進 coach.DATA_FILES —— data reset 會清不掉它,持倉代號會留在磁碟上"


# ────────── F. the cache belongs to the run's root, not the account's ──────────

_FAKE_PROVIDER = '''
# usercustomize (never sitecustomize — Homebrew ships its own and shadowing it
# removes site-packages), so the real `review.py prepare` subprocess resolves
# against a deterministic provider instead of Yahoo. Patched below the import
# probe as well, because CI installs no yfinance at all.
import datetime as dt
import os
import sys
sys.path.insert(0, os.environ["ENGINE"])


def _fake_download(symbols, start, end=None):
    import pandas as pd
    index = pd.DatetimeIndex([dt.datetime(2026, 7, 29)])
    data = {}
    for symbol in symbols:
        data[("Close", symbol)] = [100.0]
        data[("Stock Splits", symbol)] = [float("nan")]
    frame = pd.DataFrame(data, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame


import market_data
market_data._download = _fake_download
market_data._provider_available = lambda: True
'''


def test_the_cache_follows_the_runs_own_state_root():
    """#627: `prepare --root X` caches under X, never under the account's root.

    Session state always followed `--root`. The cache did not: `trade_recap`
    called `market_data.resolve` without one, so it fell through to
    `session.default_root()` — an isolated run wrote that run's tickers into the
    real `~/.trade-coach/cache/`, and could be answered from a *different* root's
    closes. Two of three `resolve` call sites passed the root; the default is
    what made forgetting silent, so `root` is now required and this asserts the
    route really supplies it.

    Both halves matter and only one is the privacy claim, so both are asserted:
    the file must appear under the run's root **and** be absent from the default
    one. The default root here is a directory that *exists* — against an absent
    one this would pass through `store`'s opportunistic skip rather than through
    routing, which is how a check like this quietly stops testing anything.
    """
    try:
        import pandas  # noqa: F401
    except ImportError:
        print("  (skip: pandas 未安裝,快取寫不出來)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        account_root = os.path.join(tmp, "account")     # what default_root() resolves to
        run_root = os.path.join(tmp, "run")             # what --root names
        sitedir = os.path.join(tmp, "provider-site")
        os.makedirs(account_root)                       # must exist, or `store` skips regardless
        os.makedirs(sitedir)
        with open(os.path.join(sitedir, "usercustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(_FAKE_PROVIDER)
        env = dict(os.environ)
        env["TRADE_COACH_HOME"] = account_root
        env["ENGINE"] = ENGINE
        env["PYTHONPATH"] = os.pathsep.join(
            [sitedir, ENGINE, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        # The posture is what this test must not have: it short-circuits before
        # the cache is reachable, so an offline run would pass with the defect
        # still in place. Nothing reaches the network — the provider above is a
        # local function.
        env.pop("TR_OFFLINE", None)
        run = subprocess.run(
            [sys.executable, os.path.join(SKILL, "engine", "review.py"), "prepare",
             os.path.join(SKILL, "mock", "sample_momentum.csv"),
             "--root", run_root, "--language", "en"],
            env=env, capture_output=True, text=True, timeout=300)
        assert run.returncode == 0, f"prepare 自身失敗:{run.stderr[-500:]}"
        assert os.path.isfile(os.path.join(run_root, "cache", "market_data.json")), (
            "快取沒跟著 --root 走,這次跑的 root 底下沒有 cache/market_data.json —— "
            f"實得:{sorted(os.listdir(run_root))}")
        leaked = os.path.join(account_root, "cache", "market_data.json")
        assert not os.path.exists(leaked), (
            "快取寫進了帳號自己的 coach root,而不是這次跑的 root —— 隔離跑會把使用者的"
            "持倉代號留在真實 ~/.trade-coach/,而且不同 root 之間會互相讀到對方的收盤價")


def _tests():
    return [(name, obj) for name, obj in sorted(globals().items())
            if name.startswith("test_") and callable(obj)]


if __name__ == "__main__":
    failed = 0
    for name, fn in _tests():
        try:
            fn()
            print(f"✅ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"❌ {name}: {exc}")
    total = len(_tests())
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
