#!/usr/bin/env python3
"""敘事品質 judge 的 mutation 驗活(docs/eval-design.md §6)。

judge_narrative.py 本身也是一段 prompt,和 SKILL.md 一樣可能是「死斷言」——
不會因為卡片真的踩雷就亮紅。這支腳本跑 fixtures/manifest.json 裡的固定卡
(一張乾淨、兩張刻意違反敘事鐵律),驗證 judge 真的能分辨好壞:
乾淨卡 overall 要 ≥ PASS_THRESHOLD,壞卡要 < FAIL_THRESHOLD。

任何一條不符預期 = judge 是活的還是死的先打問號,不能拿去當真卡的把關。
非確定性:預設跑 N_RUNS 次取多數決,不是單次判生死。

每次跑會 append 一行到受保護 state 目錄的 judge/narrative-runs.jsonl。單次結果只
告訴你「今天過不過」,但這支要防的是 judge 慢慢變鈍——壞卡從 1 分飄到 3 分,每一次
單看都還是 fail,連續看才看得出來。所以記錄要留,而且要有人讀得到:--history 就是
讀的那一面(不跑、不花錢),verdict 跟上一次不同的會標 ⚠。

跑法:
  python3 tests/agent/run_judge_eval.py              # 跑 + 記一行
  python3 tests/agent/run_judge_eval.py --history    # 只看歷史漂移,不呼叫 API
  TRADE_COACH_HOME=/tmp/x python3 tests/agent/run_judge_eval.py   # 換記錄位置
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from judge_narrative import EFFORT, MODEL, judge  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
PASS_THRESHOLD = 4
FAIL_THRESHOLD = 3
N_RUNS = int(os.environ.get("TR_JUDGE_N_RUNS", "2"))
if N_RUNS < 1:
    sys.exit(f"TR_JUDGE_N_RUNS 必須 >= 1(拿到 {N_RUNS})—— 0 次跑不出多數決,別設成 0。")


# ── 記錄面:一次跑 = 一行 JSONL ──────────────────────────────────────────────
#
# 位置沿用 repo 既有慣例(engine/session.default_root() → ux_receipt.py 也是這樣
# 鏡像的):受保護 state 目錄,不是這個 public repo——判決理由會逐字引用卡片內容,
# 這條界線不能因為「現在跑的是 mock fixture」就鬆掉。TRADE_COACH_HOME 讓 /fomo-qa
# 的隔離 root 自動生效。鏡像的正確性由 tests/test_judge_harness_offline.py 釘住。

def _state_root() -> pathlib.Path:
    """Mirror engine/session.default_root(): TRADE_COACH_HOME,否則 ~/.trade-coach。"""
    return pathlib.Path(os.path.expanduser(
        os.environ.get("TRADE_COACH_HOME", "~/.trade-coach")))


def _history_path() -> pathlib.Path:
    return _state_root() / "judge" / "narrative-runs.jsonl"


def _record(row: dict) -> None:
    """Append 一行。寫不進去只警告,絕不把一次好好的跑變成 crash。"""
    path = _history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as e:
        print(f"⚠️  這次的結果沒記錄成功({path}):{e}")


def _read_history() -> list:
    path = _history_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 壞行不該讓整份歷史讀不出來
    return rows


def _show_history(limit: int) -> int:
    rows = _read_history()
    if not rows:
        print(f"還沒有任何紀錄({_history_path()})—— 先跑一次 "
              f"python3 tests/agent/run_judge_eval.py")
        return 0
    print(f"{_history_path()}({len(rows)} 次,顯示最近 {min(limit, len(rows))} 次)\n")
    previous = {}
    for row in rows[-limit:]:
        if row.get("blocked"):
            print(f"{row.get('run_at', '?')}  —— 沒評到:{row['blocked']}")
            continue
        cells = []
        for case in row.get("cases", []):
            got = case.get("got", "?")
            scores = ",".join(str(s) for s in case.get("scores", []))
            # ⚠ = 這張 fixture 的判決跟上一次記錄不同,漂移就是這樣開始的
            drift = "⚠" if previous.get(case["file"], got) != got else " "
            cells.append(f"{drift}{case['file'].replace('card_', '').replace('.txt', '')}"
                         f"={got}({scores})")
            previous[case["file"]] = got
        print(f"{row.get('run_at', '?')}  {row.get('model', '?')}/{row.get('effort', '?')}  "
              f"{row.get('verified', '?')}/{row.get('total', '?')}  {' '.join(cells)}")
    return 0


def _verdict(overall: int) -> str:
    if overall >= PASS_THRESHOLD:
        return "pass"
    if overall < FAIL_THRESHOLD:
        return "fail"
    return "ambiguous"


def _run_case(case: dict) -> dict:
    """跑一張 fixture,回傳這一格的紀錄(ok 是「符不符合預期」)。"""
    text = (FIXTURES / case["file"]).read_text(encoding="utf-8")
    verdicts = []
    for _ in range(N_RUNS):
        try:
            result = judge(text)
        except RuntimeError as e:
            # 拒答或 API 掛掉 = 這個 fixture 沒被評到。沒被評到不能算通過(也不能讓
            # traceback 吃掉整份總結),記成這條沒過並往下跑。
            print(f"❌ {case['file']:<28} 這次沒評到:{e}")
            return {"file": case["file"], "expect": case["expect"],
                    "got": "ungraded", "scores": [], "ok": False, "error": str(e)}
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
    return {"file": case["file"], "expect": case["expect"], "got": majority,
            "scores": [o for _, o in verdicts], "ok": ok}


def _blocked(cases: list) -> str | None:
    """這批 fixture 有沒有能力證明 judge 是活的?不能的話回傳理由,否則 None。

    對應 evals/judge_episodes.py 的 nothing_to_judge() + coverage_report():這支
    腳本存在的唯一理由是「證明 judge 分得出好卡壞卡」,所以有兩種情況就算全綠也
    不算數,而且兩種都會被 `n_ok == n_total` 判成通過:
      - 一條都沒跑:0/0 是「沒評到」,不是「都對」。
      - 只有單邊期望值:全是 expect=pass 的話,一個「什麼都說 pass」的壞 judge
        也會拿滿分,這就不是 mutation 驗活了。
    在花任何一塊錢之前先擋,所以沒有 API key 也擋得住、也測得到。
    """
    if not cases:
        return "manifest 裡一條 case 都沒有 —— 這次跑沒評到任何東西,0/0 不是通過"
    missing = {"pass", "fail"} - {c.get("expect") for c in cases}
    if missing:
        return (f"manifest 缺少 expect={'/'.join(sorted(missing))} 的 fixture —— "
                "只有單邊期望值時,一個「什麼都說 pass」的 judge 也會全綠,"
                "這份結果證明不了 judge 還活著(docs/eval-design.md §6)")
    return None


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--history", nargs="?", type=int, const=20, default=None,
                        metavar="N", help="只印最近 N 次紀錄(預設 20),不呼叫 API")
    args = parser.parse_args(argv)
    if args.history is not None:
        return _show_history(args.history)

    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    cases = manifest.get("cases") or []
    row = {"run_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "model": MODEL, "effort": EFFORT, "n_runs": N_RUNS}

    blocked = _blocked(cases)
    if blocked:
        print(f"❌ {blocked}")
        # 擋掉的跑也要留一行:歷史裡出現空窗時,才分得出「沒人跑」和「跑了但沒評到」。
        _record({**row, "blocked": blocked, "verified": 0, "total": 0, "cases": []})
        return 1

    # 判決是哪個模型給的要印出來:換模型後這份驗活結果就不能直接沿用。
    print(f"model={MODEL}, effort={EFFORT}, {N_RUNS} run(s) per fixture\n")
    results = [_run_case(c) for c in cases]
    n_ok, n_total = sum(r["ok"] for r in results), len(results)
    _record({**row, "verified": n_ok, "total": n_total, "cases": results})
    print(f"\n{n_ok}/{n_total} mutation case 符合預期")
    if n_ok < n_total:
        print("judge 沒能分辨出至少一個刻意壞掉的 fixture —— 先修 rubric/prompt,"
              "再拿它去判真卡(docs/eval-design.md §6)。")
    print(f"已記錄到 {_history_path()}(看漂移:--history)")
    return 0 if n_ok == n_total else 1


if __name__ == "__main__":
    sys.exit(_main())
