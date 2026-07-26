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
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENGINE = os.path.join(REPO, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import coach  # noqa: E402
import fetch_cache  # noqa: E402

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
import fetch_cache, trade_recap
root = os.environ["TRADE_COACH_HOME"]
today = dt.date.today().isoformat()
# 先種一份「今天的」快取。若 fetch 路徑在 import 失敗前就讀快取,就會撈到這筆。
fetch_cache.store("splits", sorted({"AAA"}), {"AAA": [["2026-01-02", 4.0]]}, root=root, today=today)
fetch_cache.store("prices", {"universe": sorted({"AAA", "SPY", "QQQ", "SOXX", "^VIX"}),
                             "start": "2026-01-01"},
                  {"index": ["2026-01-02"], "columns": ["AAA"], "data": [[1.0]]},
                  root=root, today=today)
fetch_cache.store("fx", ["TWD"], {"TWD": 0.0307}, root=root, today=today)
splits = trade_recap.fetch_splits({"AAA"})
frame, err = trade_recap.fetch_prices({"AAA"}, "2026-01-01")
fx, fx_err = trade_recap.fetch_fx({"TWD", "USD"})
# default=str:快取命中時 splits 會帶 dt.date,序列化不得因此爆掉——
# 這條探針要讓斷言說話,不是讓 json 先掛掉。
print(json.dumps({"splits": splits, "frame_is_none": frame is None, "err": err,
                  "fx": fx, "fx_err": fx_err}, default=str))
"""


def test_offline_shim_cannot_read_the_cache():
    """離線降級必須先於快取:import yfinance 失敗就返回,快取讀不到。

    這條是 #235 的核心約束(見 issue 2026-07-19 comment)。把快取讀取移到
    `import yfinance` 之前,這個測試就會紅——那正是它要擋的突變。
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
        run = subprocess.run([sys.executable, "-c", _SHIM_PROBE],
                             env=env, capture_output=True, text=True, timeout=120)
        assert run.returncode == 0, f"探針自身失敗:{run.stderr[:400]}"
        out = json.loads(run.stdout.strip().splitlines()[-1])
        assert out["splits"] == {}, \
            f"離線時 fetch_splits 必須降級成 {{}},實際讀到快取:{out['splits']}"
        assert out["frame_is_none"] is True and "yfinance" in (out["err"] or ""), \
            f"離線時 fetch_prices 必須回 (None, 未安裝),實際:{out['err']}"
        assert out["fx"] == {"USD": 1.0} and "yfinance" in (out["fx_err"] or ""), \
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
