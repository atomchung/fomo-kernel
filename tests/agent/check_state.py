#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_state.py — 本機狀態檔 trajectory 層 adherence 機檢(離線、確定性)。

受測面 = 一次 skill run 收尾後的 ~/.trade-coach/ 狀態檔(eval-design.md §0 第 2 面)。

**刻意不重造 coach.py / test_tr_json_contract.py 已擁有的**:cycle_id 三段格式、
commitment schema、maturity/emotion enum,都由 coach.py 在**寫入時**驗、契約測試在
engine 出口驗(單一事實源)。本檔只管那兩層管不到的**收尾產物層 adherence**:

  S-1  每行 parse 得動(append-only 檔沒被寫成半行 / 非 JSON)       ← eval-design A-7
  S-2  log.jsonl 每筆有 {date_end, headline_dim, commitment, metrics_snapshot}
  S-3  theses.jsonl 每筆有 ticker + cycle_id,且 maturity 或 exit_narrative 二選一
  S-4  收尾沒被跳過:log.jsonl 與 theses.jsonl 都存在且 ≥1 行(#60 原始抱怨:
       「theses.jsonl 沒被建立」)。試駕模式零寫入是例外,由 case 宣告豁免。

跨 run 的兩條(單一 dir 看不出,給 case runner 呼叫):
  append_only(before_n, after_n)          第二週檔行數只增不減(A-7)
  differential(log_a, log_b)              換答案→最新 commitment.metric_key 必不同(B-3,
                                          eval-design §1 判定哲學②「差分測聽沒聽」的機檢)

跑法:
  python3 tests/agent/check_state.py <state_dir>       # 檢一個 ~/.trade-coach 目錄
可 import:
  from check_state import check_state, differential, append_only
"""
import json
import pathlib
import sys
from dataclasses import dataclass


@dataclass
class Finding:
    assertion: str
    passed: bool
    label: str
    evidence: str = ""

    def __str__(self) -> str:
        mark = "✅" if self.passed else "❌"
        tail = f"  → {self.evidence}" if (self.evidence and not self.passed) else ""
        return f"{mark} {self.assertion:<4} {self.label}{tail}"


_LOG_KEYS = {"date_end", "headline_dim", "commitment", "metrics_snapshot"}


def _read_jsonl(path: pathlib.Path):
    """回 (rows, error)。任一行壞 → rows=None, error=訊息(S-1 用)。"""
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            return None, f"{path.name} 第 {i+1} 行不是合法 JSON:{e}"
    return rows, None


def check_state(state_dir) -> list[Finding]:
    """檢一個狀態目錄的收尾產物。回 list[Finding](含通過項)。"""
    d = pathlib.Path(state_dir)
    log_p, theses_p = d / "log.jsonl", d / "theses.jsonl"
    findings: list[Finding] = []

    # S-4 收尾沒被跳過(先檢存在性,後面才有東西可 parse)
    log_ok = log_p.exists() and log_p.stat().st_size > 0
    theses_ok = theses_p.exists() and theses_p.stat().st_size > 0
    missing = [n for n, ok in [("log.jsonl", log_ok), ("theses.jsonl", theses_ok)] if not ok]
    findings.append(Finding("S-4", not missing,
                            "收尾沒跳過:log.jsonl + theses.jsonl 都存在且非空",
                            f"缺 / 空:{', '.join(missing)}" if missing else ""))

    # S-1 每行 parse 得動
    log_rows = theses_rows = None
    for p, ok in [(log_p, log_ok), (theses_p, theses_ok)]:
        if not ok:
            continue
        rows, err = _read_jsonl(p)
        if err:
            findings.append(Finding("S-1", False, "append-only 檔每行都是合法 JSON", err))
        if p == log_p:
            log_rows = rows
        else:
            theses_rows = rows
    if not any(f.assertion == "S-1" for f in findings):
        findings.append(Finding("S-1", True, "append-only 檔每行都是合法 JSON"))

    # S-2 log.jsonl 欄位齊(commitment 值可為 null,但 key 要在)
    if log_rows is not None:
        bad = next((r for r in log_rows if not _LOG_KEYS <= set(r)), None)
        findings.append(Finding("S-2", bad is None,
                                "log 每筆有 date_end/headline_dim/commitment/metrics_snapshot",
                                f"缺欄:{sorted(_LOG_KEYS - set(bad))}" if bad else ""))

    # S-3 theses.jsonl 最小結構(不驗 cycle_id 格式/enum——那是 coach 的事)
    if theses_rows is not None:
        def _bad_thesis(r):
            if not r.get("ticker") or not r.get("cycle_id"):
                return "缺 ticker / cycle_id"
            if r.get("event") != "exit_narrative" and not r.get("maturity"):
                return "非 exit_narrative 卻缺 maturity"
            return None
        bad_reason = next((m for r in theses_rows if (m := _bad_thesis(r))), None)
        findings.append(Finding("S-3", bad_reason is None,
                                "theses 每筆有 ticker+cycle_id,且 maturity | exit_narrative",
                                bad_reason or ""))

    return findings


# ─────────────── 跨 run helpers(case runner 呼叫,單一 dir 看不出)───────────────

def _latest_commitment_metric_key(log_path):
    """log.jsonl 最後一筆有 commitment 的 metric_key(對帳錨點)。無 → None。"""
    rows, err = _read_jsonl(pathlib.Path(log_path))
    if err or not rows:
        return None
    for r in reversed(rows):
        cm = r.get("commitment")
        if cm:
            return cm.get("metric_key")
    return None


def differential(log_a, log_b) -> Finding:
    """B-3:同 CSV、兩種用戶答案 → 兩份 log 的最新 commitment.metric_key 必不同。
    相同 = Step 2 是儀式(答什麼都一樣),這條測的是產品靈魂。"""
    ka, kb = _latest_commitment_metric_key(log_a), _latest_commitment_metric_key(log_b)
    differ = (ka != kb) and not (ka is None and kb is None)
    return Finding("B-3", differ,
                   "換答案→commitment.metric_key 不同(Step 2 不是儀式)",
                   f"兩份都是 {ka!r}" if not differ else "")


def append_only(before_n: int, after_n: int) -> Finding:
    """A-7:第二週的檔行數只增不減(狀態是 append-only 記憶,不是覆寫)。"""
    return Finding("A-7", after_n >= before_n,
                   "跨 run 行數只增不減(append-only)",
                   f"{before_n} → {after_n}(縮了)" if after_n < before_n else "")


def _main() -> int:
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <state_dir>", file=sys.stderr)
        return 2
    findings = check_state(sys.argv[1])
    for f in findings:
        print(f)
    failed = [f for f in findings if not f.passed]
    print()
    if failed:
        print(f"❌ {len(failed)}/{len(findings)} 條狀態鐵律被踩:"
              f"{', '.join(f.assertion for f in failed)}")
        return 1
    print(f"✅ 全部 {len(findings)} 條狀態鐵律通過。")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
