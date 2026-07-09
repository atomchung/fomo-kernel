#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_card.py — 卡面鐵律機檢(離線、確定性;docs/eval-design.md §4 A/B 系列可機檢部分)。

判定哲學(eval-design §1「code-check > LLM-judge > 人工」):能 regex / 子字串斷言的
絕不用 judge。本檔管「卡犯規沒」(純文字機檢),judge_narrative.py 管「敘事好不好」
(需 LLM)——兩支分工,別把機檢項丟給 judge(judge 非確定性、有成本)。

**斷言的單一權威 = docs/eval-design.md 的 A/B 編號**(鐵律文本住 skills/fomo-kernel/
card-spec.md)。改那兩份的對應鐵律時,同步這裡的 CHECKS——漂移防線見 eval-design §5。

每條 check 回一個 Finding(assertion 編號 / passed / 證據原句)。任一 FAIL → CLI exit 1。
只驗**卡面輸出**,不驗 repo 文檔(A-13 同款限定)。

跑法:
  python3 tests/agent/check_card.py <card.md|->      # 檔案或 stdin(-)
  cat card.md | python3 tests/agent/check_card.py -
可 import:
  from check_card import check_card
  findings = check_card(card_text)                    # list[Finding];[f for f in findings if not f.passed]
"""
import re
import sys
from dataclasses import dataclass


@dataclass
class Finding:
    assertion: str          # eval-design 編號,如 "A-2"
    passed: bool
    label: str              # 這條在檢查什麼(人話)
    evidence: str = ""      # 扣分時引卡上原句;通過時留空

    def __str__(self) -> str:
        mark = "✅" if self.passed else "❌"
        tail = f"  → {self.evidence}" if (self.evidence and not self.passed) else ""
        return f"{mark} {self.assertion:<4} {self.label}{tail}"


# ── 內部 metric key 黑名單(A-12;card-spec 說話原則:工程內部名不上卡)──────────
_INTERNAL_KEYS = re.compile(
    r"max_pos_pct|ai_pct|avgdown_count|max_sector_pct|top3_pct|metric_key|baseline")

# ── 5 維 severity 小數表(A-2;card-spec 🚫「卡是故事不是 dashboard」)────────────
_SEVERITY_TABLE = re.compile(r"0?\.\d+ *[🔴🟡]")

# ── 標籤拼接 / 內部註記(A-3;同上)──────────────────────────────────────────────
_BRACKET_TAG = re.compile(r"〔.+〕")
_POINT_TAG = re.compile(r"← *點\d")
_LITERAL_TAGS = ("(引擎產出)", "(供參)", "（引擎產出）", "（供參）")

# ── 首段以勝率當主數字(A-6;card-spec 數字鐵律「金額 > 筆數勝率」)──────────────
_WINRATE = re.compile(r"勝率 *\d+ *%|\d+ *勝 *\d+ *負")

# ── 卡面半形標點夾在中日文字之間(A-13;全形統一,數字格式除外)────────────────
#    兩側都要 CJK,所以「$1,850,平均」(digit,CJK)不會誤判——數字格式天然被排除。
_HALFWIDTH_PUNCT = re.compile(r"[一-鿿][,:;][一-鿿]")

# ── 抽象規矩黑名單(B-7;「這 ChatGPT 也會講」的空泛句)──────────────────────────
#    與 judge_narrative.py RUBRIC 第 3 條同源;改一邊要同步另一邊。
_ABSTRACT_RULES = ("注意分散", "加碼前想清楚", "控制風險")

# ── 具體數字(B-9 proxy;金額 / 百分比 / 小數 / 千分位)────────────────────────────
#    B-9 原文是「最大的洞段落須含 ticker + 數字」;本機檢取全卡層的保守 proxy:
#    「卡須含 ≥1 具體數字」。純形容詞堆砌卡(零數字)必掛;section 級 ticker 定位與
#    黑話翻譯鄰近度留 case-spec / judge(見 README「這裡沒做的」)。
_CONCRETE_NUM = re.compile(r"\$[\d,]+|\d+ *%|\d+\.\d+|\d{1,3}(?:,\d{3})+")


def _first_paragraph(text: str) -> str:
    """首段 = 第一個非空白區塊(到第一個空行為止)。A-6 只看首段。"""
    for block in re.split(r"\n\s*\n", text.strip()):
        if block.strip():
            return block.strip()
    return ""


def check_card(text: str) -> list[Finding]:
    """對一張卡跑全部離線鐵律機檢。回 list[Finding](含通過項,方便呈現全貌)。"""
    findings: list[Finding] = []

    # A-2 5 維 severity 小數表
    m = _SEVERITY_TABLE.search(text)
    findings.append(Finding("A-2", m is None,
                            "無 5 維 severity 小數表(0.71 🔴 這類)",
                            m.group(0) if m else ""))

    # A-3 標籤拼接 / 內部註記
    tag_hit = (_BRACKET_TAG.search(text) or _POINT_TAG.search(text)
               or next((t for t in _LITERAL_TAGS if t in text), None))
    ev = tag_hit if isinstance(tag_hit, str) else (tag_hit.group(0) if tag_hit else "")
    findings.append(Finding("A-3", not tag_hit,
                            "無標籤拼接〔…〕/ ←點n /(引擎產出)/(供參)", ev))

    # A-6 首段以勝率當主數字
    m = _WINRATE.search(_first_paragraph(text))
    findings.append(Finding("A-6", m is None,
                            "首段不以勝率 / 勝負筆數當主數字",
                            m.group(0) if m else ""))

    # A-12 內部 metric key 外洩
    m = _INTERNAL_KEYS.search(text)
    findings.append(Finding("A-12", m is None,
                            "卡面無工程內部 metric key(max_pos_pct 這類)",
                            m.group(0) if m else ""))

    # A-13 半形標點夾中日文字
    m = _HALFWIDTH_PUNCT.search(text)
    findings.append(Finding("A-13", m is None,
                            "中文字間標點全形統一(數字格式除外)",
                            m.group(0) if m else ""))

    # B-7 抽象規矩黑名單
    hit = next((p for p in _ABSTRACT_RULES if p in text), None)
    findings.append(Finding("B-7", hit is None,
                            "規矩不是空泛黑名單句(注意分散 / 控制風險 這類)",
                            hit or ""))

    # B-9(proxy)卡須含具體數字
    has_num = bool(_CONCRETE_NUM.search(text))
    findings.append(Finding("B-9", has_num,
                            "卡含 ≥1 具體數字(非純形容詞堆砌)",
                            "" if has_num else "全卡查無金額 / % / 小數"))

    return findings


def _main() -> int:
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <card.md|->", file=sys.stderr)
        return 2
    if sys.argv[1] == "-":
        text = sys.stdin.read()
    else:
        with open(sys.argv[1], encoding="utf-8") as f:
            text = f.read()

    findings = check_card(text)
    for f in findings:
        print(f)
    failed = [f for f in findings if not f.passed]
    print()
    if failed:
        print(f"❌ {len(failed)}/{len(findings)} 條鐵律被踩:"
              f"{', '.join(f.assertion for f in failed)}")
        return 1
    print(f"✅ 全部 {len(findings)} 條卡面鐵律通過。")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
