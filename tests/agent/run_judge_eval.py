#!/usr/bin/env python3
"""敘事品質 judge 的 mutation 驗活(docs/eval-design.md §6)。

judge_narrative.py 本身也是一段 prompt,和 SKILL.md 一樣可能是「死斷言」——
不會因為卡片真的踩雷就亮紅。這支腳本跑 fixtures/manifest.json 裡的固定卡
(一張乾淨、兩張刻意違反敘事鐵律),驗證 judge 真的能分辨好壞:
乾淨卡 overall 要 ≥ PASS_THRESHOLD,壞卡要 < FAIL_THRESHOLD。

任何一條不符預期 = judge 是活的還是死的先打問號,不能拿去當真卡的把關。
非確定性:預設跑 N_RUNS 次取多數決,不是單次判生死。

跑法: python3 tests/agent/run_judge_eval.py
"""
import json
import os
import pathlib
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from judge_narrative import judge  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
PASS_THRESHOLD = 4
FAIL_THRESHOLD = 3
N_RUNS = int(os.environ.get("TR_JUDGE_N_RUNS", "2"))
if N_RUNS < 1:
    sys.exit(f"TR_JUDGE_N_RUNS 必須 >= 1(拿到 {N_RUNS})—— 0 次跑不出多數決,別設成 0。")


def _verdict(overall: int) -> str:
    if overall >= PASS_THRESHOLD:
        return "pass"
    if overall < FAIL_THRESHOLD:
        return "fail"
    return "ambiguous"


def _run_case(case: dict) -> bool:
    text = (FIXTURES / case["file"]).read_text(encoding="utf-8")
    verdicts = []
    for _ in range(N_RUNS):
        result = judge(text)
        v = _verdict(result["overall"])
        verdicts.append((v, result["overall"]))
    tally = Counter(v for v, _ in verdicts).most_common()
    top_count = tally[0][1]
    tied = sum(1 for _, c in tally if c == top_count) > 1
    majority = "ambiguous(tie)" if tied else tally[0][0]  # 平票不准偷偷選一邊當多數
    ok = majority == case["expect"]
    scores = ", ".join(str(o) for _, o in verdicts)
    status = "✅" if ok else "❌"
    print(f"{status} {case['file']:<28} expect={case['expect']:<5} "
          f"got={majority}({top_count}/{N_RUNS}, scores={scores})  {case['label']}")
    return ok


def _main():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    results = [_run_case(c) for c in manifest["cases"]]
    n_ok, n_total = sum(results), len(results)
    print(f"\n{n_ok}/{n_total} mutation case 符合預期")
    if n_ok < n_total:
        print("judge 沒能分辨出至少一個刻意壞掉的 fixture —— 先修 rubric/prompt,"
              "再拿它去判真卡(docs/eval-design.md §6)。")
    return 0 if n_ok == n_total else 1


if __name__ == "__main__":
    sys.exit(_main())
