#!/usr/bin/env python3
"""LLM-as-judge:卡片「敘事品質」評分(docs/eval-design.md §4「唯一的 LLM-judge 項」)。

只評一件事——卡是不是連貫故事、不是報表拼接,rubric 直接抄
skills/fomo-kernel/card-spec.md 的敘事鐵律(先肯定再打 / 數字要髒有案例 /
不講黑話 / 引言不當結語 / 連貫敘事不准標籤拼接)。judge 只看 rubric,不看
範本答案 —— 改 card-spec.md 的敘事鐵律時,同步改這裡的 RUBRIC 常數。

跑法:
  export ANTHROPIC_API_KEY=sk-...          # 或放 .env(python-dotenv 會自動讀)
  python3 tests/agent/judge_narrative.py tests/agent/fixtures/card_good.txt
  cat some_card.txt | python3 tests/agent/judge_narrative.py -

輸出:單行 JSON(每軸 0–5 分 + 一句理由 + overall)。
"""
import json
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import anthropic

MODEL = os.environ.get("TR_JUDGE_MODEL", "claude-sonnet-4-5-20250929")

# 來源=skills/fomo-kernel/card-spec.md「卡片是一個故事,不是 dashboard」+ 🚫 清單。
# 改那份檔的敘事鐵律時,這裡要跟著動(維護鐵律見該檔第 6 行)。
RUBRIC = """你是 fomo-kernel(交易復盤卡)的敘事品質審核員。只看下面五條鐵律,
不管卡上的分析對不對、不管你自己認不認同建議——這是格式與敘事品質審查,不是內容審查。

1. 連貫敘事,不准標籤拼接:卡不能是「〔這次成績〕A｜B｜C」這種一塊塊格式,也不能有
   5 維 severity 小數表(如「0.71 🔴」)、`(引擎產出)`/`(供參)` 這類內部標記。要讀起來像
   一段連貫的話,不是幾份報告硬湊。
2. 先肯定再打:進入「最大的洞」之前,必須先具體肯定一個真實優點(附案例),不能開頭就是
   批評或說教。
3. 數字要髒、要有案例:「最大的洞」段落必須指名至少一筆具體交易(ticker + 數字),不能只用
   形容詞(「紀律不佳」「風險偏高」)帶過。規矩也必須是具體的 if-then,不能是空泛建議
   (黑名單:「注意分散」「加碼前想清楚」「控制風險」這類抽象句)。
4. 不講黑話、不裸奔工程內部名:`max_pos_pct`、`avgdown_count`、`metric_key`、`baseline_note`
   這類內部變數名絕不能出現;學術詞(α/β/處置效應/夏普)出現時 ±2 句內要有白話翻譯。
5. 引言不當結語:鏡片引用的那句話不能是卡片結尾單獨冒出來的訓話(「這是核心教誨,請謹記在心」
   這種語氣不合格),必須融入敘事、呼應前面講的具體案例。

對每一條給 0–5 分(5=完全遵守,0=嚴重違反),並給整體 overall(0–5,不是五軸平均,
是你綜合判斷這張卡讀起來像不像「一個真人寫給另一個真人看的復盤」)。每軸附一句理由,
理由如果是扣分,必須引用卡上的原句當證據。"""

SCORE_TOOL = {
    "name": "score_narrative",
    "description": "回報敘事品質五軸評分 + overall",
    "input_schema": {
        "type": "object",
        "properties": {
            "coherent_story": {"type": "object", "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 5},
                "reason": {"type": "string"}}, "required": ["score", "reason"]},
            "strength_first": {"type": "object", "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 5},
                "reason": {"type": "string"}}, "required": ["score", "reason"]},
            "concrete_evidence": {"type": "object", "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 5},
                "reason": {"type": "string"}}, "required": ["score", "reason"]},
            "plain_language": {"type": "object", "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 5},
                "reason": {"type": "string"}}, "required": ["score", "reason"]},
            "quote_not_lecture": {"type": "object", "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 5},
                "reason": {"type": "string"}}, "required": ["score", "reason"]},
            "overall": {"type": "integer", "minimum": 0, "maximum": 5},
        },
        "required": ["coherent_story", "strength_first", "concrete_evidence",
                     "plain_language", "quote_not_lecture", "overall"],
    },
}


def judge(card_text: str) -> dict:
    client = anthropic.Anthropic()  # 讀 ANTHROPIC_API_KEY
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=RUBRIC,
            tools=[SCORE_TOOL],
            tool_choice={"type": "tool", "name": "score_narrative"},
            messages=[{"role": "user", "content": f"待審的卡:\n\n{card_text}"}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"judge() 呼叫 Anthropic API 失敗:{e}") from e
    try:
        tool_use = next(b for b in resp.content if b.type == "tool_use")
    except StopIteration:
        raise RuntimeError(
            f"judge() 的回應沒有 tool_use 區塊,即使已強制 tool_choice(stop_reason={resp.stop_reason!r})"
        ) from None
    return tool_use.input


def _main():
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <card.txt|->", file=sys.stderr)
        return 2
    if sys.argv[1] == "-":
        text = sys.stdin.read()
    else:
        with open(sys.argv[1], encoding="utf-8") as f:
            text = f.read()
    print(json.dumps(judge(text), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
