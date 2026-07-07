#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_context(#37)單元測試 — 全離線、確定性、免裝 pytest。

蓋什麼:
  A. compute_context 純函式:window_ret / ytd_ret 錨點語意、VIX 水平值特例、
     缺資料誠實缺席(不硬算、不編)。
  B. CLI 參數驗證(不觸網)。
  C. TR_TEST_NETWORK=1 才跑的 network smoke(平時離線紀律不破)。
"""
import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import market_context as mc  # noqa: E402


def _approx(a, b, tol=1e-6):
    # 輸出經 round(x, 6),容差對齊該精度(1e-9 會在循環小數上假紅,如 121/115)
    return a is not None and b is not None and abs(a - b) <= tol


def _spy():
    # 去年末收 100;窗口(06-09~06-13)前收 110;窗口末收 121 → ytd +21%、week +10%
    return [("2025-12-30", 98.0), ("2025-12-31", 100.0),
            ("2026-06-05", 108.0), ("2026-06-06", 110.0),
            ("2026-06-09", 115.0), ("2026-06-13", 121.0)]


def test_window_and_ytd_anchors():
    out = mc.compute_context({"SPY": _spy()}, "2026-06-09", "2026-06-13")
    spy = out["SPY"]
    assert _approx(spy["window_ret"], 121.0 / 110.0 - 1), "週漲跌 = 窗口前收→窗口末收"
    assert _approx(spy["ytd_ret"], 121.0 / 100.0 - 1), "YTD = 去年最後收盤→窗口末收"
    assert spy["last_date"] == "2026-06-13" and spy["last_close"] == 121.0


def test_missing_prev_close_is_null_not_fabricated():
    series = [("2026-06-09", 115.0), ("2026-06-13", 121.0)]   # 資料頭從窗口內才開始
    out = mc.compute_context({"SPY": series}, "2026-06-09", "2026-06-13")
    assert out["SPY"]["window_ret"] is None, "缺前收 → null,不硬算"
    assert out["SPY"]["ytd_ret"] is None, "缺年末錨 → null"


def test_vix_is_level_not_return():
    vix = [("2026-06-06", 19.8), ("2026-06-13", 16.2)]
    out = mc.compute_context({"^VIX": vix}, "2026-06-09", "2026-06-13")
    v = out["VIX"]                                            # ^ 去掉,呈現名 VIX
    assert v["last"] == 16.2 and v["prev"] == 19.8 and _approx(v["delta"], -3.6, 1e-6)
    assert "ytd_ret" not in v and "window_ret" not in v, "VIX 是水平值,不當資產報酬"


def test_no_data_in_window_symbol_absent():
    series = [("2026-01-05", 100.0)]                          # 窗口外才有價
    out = mc.compute_context({"SPY": series, "QQQ": []}, "2026-06-09", "2026-06-13")
    assert out == {}, "窗口內沒價的 symbol 誠實缺席,不編"


def test_unsorted_input_ok():
    shuffled = list(reversed(_spy()))
    out = mc.compute_context({"SPY": shuffled}, "2026-06-09", "2026-06-13")
    assert _approx(out["SPY"]["window_ret"], 121.0 / 110.0 - 1), "輸入亂序也要算對(內部排序)"


def test_single_day_window():
    out = mc.compute_context({"SPY": _spy()}, "2026-06-13", "2026-06-13")
    assert _approx(out["SPY"]["window_ret"], 121.0 / 115.0 - 1), "單日窗口:前收=06-09"


def test_cli_rejects_bad_dates():
    ex = os.path.join(ENGINE, "market_context.py")
    r1 = subprocess.run([sys.executable, ex, "--start", "2026-06-13", "--end", "2026-06-09"],
                        capture_output=True, text=True)
    assert r1.returncode == 1, "start > end 要報錯"
    r2 = subprocess.run([sys.executable, ex, "--start", "not-a-date", "--end", "2026-06-09"],
                        capture_output=True, text=True)
    assert r2.returncode == 1, "壞日期要報錯,不吞"


def test_network_smoke_optional():
    """TR_TEST_NETWORK=1 才跑:真抓一次,驗 CLI 輸出形狀(stdout 純 JSON、鍵齊)。"""
    if os.environ.get("TR_TEST_NETWORK") != "1":
        print("  (skip network smoke;TR_TEST_NETWORK=1 才跑)")
        return
    end = dt.date.today() - dt.timedelta(days=3)
    start = end - dt.timedelta(days=5)
    ex = os.path.join(ENGINE, "market_context.py")
    r = subprocess.run([sys.executable, ex, "--start", start.isoformat(),
                        "--end", end.isoformat()], capture_output=True, text=True)
    out = json.loads(r.stdout)                                # stdout 必須純 JSON
    assert set(out) == {"start", "end", "benchmarks", "error"}
    if out["error"] is None:
        assert "SPY" in out["benchmarks"], f"線上路徑該有 SPY:{out['benchmarks'].keys()}"


# ─────────────────────────── runner ───────────────────────────

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
