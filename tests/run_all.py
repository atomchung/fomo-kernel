#!/usr/bin/env python3
"""
一鍵跑 fomo-kernel 的全部測試 —— 之後每次迭代引擎/規格,跑這一條就知道有沒有改壞。

零依賴(只用標準庫,免裝 pytest);全程離線、確定性(不碰 yfinance,不需網路)。
subprocess 依序跑三套測試,任一非零退出 → 整體 exit 1(給 CI / pre-push 當紅綠燈)。

六套測試的分工:
  1. 機械層純函式單元    tests/test_engine_units.py
  2. TR_JSON/state 契約  tests/test_tr_json_contract.py(#61:SKILL 消費介面紅綠燈,強制離線)
  3. 價格路徑合成單元    tests/test_price_paths.py(#62:賣太早/β/α/處方的離線確定性覆蓋)
  4. snapshot-anchored 帳本  tests/test_ledger.py(#31 修訂版/#129 PR-1:雙輸入推導/reconcile/去重)
  5. 三風格端到端        tests/test_sample_styles.py
  6. 狀態迴圈端到端      skills/fomo-kernel/engine/test_state_loop.py

跑法:
  python3 tests/run_all.py
  TR_TEST_NETWORK=1 python3 tests/run_all.py   # 額外跑 sample_styles 的 β 方向 network smoke
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUITES = [
    ("機械層純函式單元", "tests/test_engine_units.py"),
    ("TR_JSON/state 契約", "tests/test_tr_json_contract.py"),
    ("價格路徑合成單元", "tests/test_price_paths.py"),
    ("snapshot-anchored 帳本", "tests/test_ledger.py"),
    ("三風格端到端", "tests/test_sample_styles.py"),
    ("狀態迴圈端到端", os.path.join("skills", "fomo-kernel", "engine", "test_state_loop.py")),
]


def main():
    results = []
    for label, rel in SUITES:
        path = os.path.join(ROOT, rel)
        print(f"\n{'='*64}\n▶ {label}  ({rel})\n{'='*64}", flush=True)
        if not os.path.exists(path):                      # 檔案被搬走也算紅燈,不靜默跳過
            print(f"❌ 找不到測試檔:{path}")
            results.append((label, rel, 127))
            continue
        r = subprocess.run([sys.executable, path], cwd=ROOT)
        results.append((label, rel, r.returncode))

    print(f"\n{'='*64}\n  總結\n{'='*64}")
    failed = sum(1 for *_, rc in results if rc != 0)
    for label, rel, rc in results:
        print(f"  {'✅ PASS' if rc == 0 else '❌ FAIL'}  {label}  ({rel})")
    print()
    if failed:
        print(f"❌ {failed}/{len(results)} 套測試失敗 —— 有東西被改壞了,先別 merge/push。")
    else:
        print(f"✅ 全部 {len(results)} 套測試通過。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
