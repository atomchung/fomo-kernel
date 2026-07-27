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
  TR_JUDGE_MODEL=... TR_JUDGE_EFFORT=low python3 tests/agent/judge_narrative.py ...

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

MODEL = os.environ.get("TR_JUDGE_MODEL", "claude-opus-5")
EFFORT = os.environ.get("TR_JUDGE_EFFORT", "high")

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

# 0–5 分寫成 enum 而不是 minimum/maximum:strict 模式不吃數值區間約束,enum 吃,
# 語意一樣而且是 API 保證的(不是拿到分數後自己再驗一次)。
_SCORE = {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]}
_AXIS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "reason"],
    "properties": {"score": _SCORE, "reason": {"type": "string"}},
}
_AXES = ("coherent_story", "strength_first", "concrete_evidence",
         "plain_language", "quote_not_lecture")

SCORE_TOOL = {
    "name": "score_narrative",
    "description": "回報敘事品質五軸評分 + overall",
    "strict": True,  # 回傳保證符合 schema,少一軸/多一軸都不會靜默通過
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [*_AXES, "overall"],
        "properties": {**{axis: _AXIS for axis in _AXES}, "overall": _SCORE},
    },
}


def judge(card_text: str) -> dict:
    # anthropic 延遲 import(對照 evals/judge_episodes.py):這支是 opt-in、要付費的
    # 工具,離線測試套件刻意不依賴 SDK。import 留在函式內,SCORE_TOOL 這些純邏輯才
    # 能被 tests/test_judge_harness_offline.py 免費驗活——只有拿得到 API key 的人
    # 才跑得到的閘門,等於沒人在複驗。
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "敘事 judge 需要 anthropic 套件(pip install anthropic);"
            "離線測試套件刻意不依賴它。") from None
    client = anthropic.Anthropic()  # 讀 ANTHROPIC_API_KEY
    try:
        resp = client.messages.create(
            model=MODEL,
            # max_tokens 是「思考 + 回覆」的總上限,不是回覆的上限。這個模型預設會思考,
            # 舊的 1024 會在還沒吐出 tool_use 就被截斷(症狀是 stop_reason=max_tokens、
            # 沒有 tool_use 區塊),所以留足額度。
            max_tokens=16000,
            system=RUBRIC,
            output_config={"effort": EFFORT},
            tools=[SCORE_TOOL],
            tool_choice={"type": "tool", "name": "score_narrative"},
            messages=[{"role": "user", "content": f"待審的卡:\n\n{card_text}"}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"judge() 呼叫 Anthropic API 失敗:{e}") from e
    # 被安全分類器擋下時回的是 HTTP 200 + 空的(或半截的)content,所以要在讀 content
    # 之前先判,不能等 StopIteration 才發現。拒答＝這張卡沒被評到,不是通過。
    if resp.stop_reason == "refusal":
        raise RuntimeError(
            "judge 模型拒答了這張卡"
            f"(category={getattr(resp.stop_details, 'category', None)!r})—— "
            "沒評到不等於通過,別拿這次結果當判決。")
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
