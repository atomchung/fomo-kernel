#!/usr/bin/env python3
"""LLM-as-judge:卡片「敘事品質」評分(docs/eval-design.md §4「唯一的 LLM-judge 項」)。

只評一件事——卡是不是連貫故事、不是報表拼接,rubric 直接抄
skills/fomo-kernel/card-spec.md 的敘事鐵律(先肯定再打 / 數字要髒有案例 /
不講黑話 / 引言不當結語 / 連貫敘事不准標籤拼接)。judge 只看 rubric,不看
範本答案 —— 改 card-spec.md 的敘事鐵律時,同步改這裡的 RUBRIC 常數。

兩個後端,自動選(#511):Antigravity CLI(`agy`,吃訂閱、不用 API key)或 Anthropic
SDK(可攜、強制 tool use 所以形狀有保證)。**選哪個後端這件事不在這裡決定**——
evals/judge_episodes.py 的 resolve_backend() 已經擁有它,這支直接 import 那個決定。
同一件事有兩個地方決定,就是兩個地方要維護 agy 契約。這支只提供它自己不同的部分:
rubric、schema,以及 CLI 沒有形狀保證時的 fail-closed 解析。

跑法:
  python3 tests/agent/judge_narrative.py tests/agent/fixtures/card_good.txt
  cat some_card.txt | python3 tests/agent/judge_narrative.py -
  TR_JUDGE_BACKEND=anthropic python3 tests/agent/judge_narrative.py ...   # 釘後端
  TR_JUDGE_MODEL=... TR_JUDGE_EFFORT=low python3 tests/agent/judge_narrative.py ...

走 Anthropic 後端時要 `pip install anthropic` + ANTHROPIC_API_KEY(或放 .env,
python-dotenv 會自動讀);走 agy 後端兩者都不需要。

輸出:JSON(每軸 0–5 分 + 一句理由 + overall)。
"""
import json
import os
import pathlib
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 後端解析的單一來源。judge_episodes 只在 main() 內才 import anthropic,所以這行
# 不會把 SDK 依賴帶進離線測試;它自己 import 的 run_episodes 也是純 stdlib、不連網。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "evals"))
import judge_episodes as BACKENDS      # noqa: E402

resolve_backend = BACKENDS.resolve_backend
DEFAULT_MODELS = BACKENDS.DEFAULT_MODELS
EFFORT = BACKENDS.EFFORT

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


def _is_score(value) -> bool:
    """0–5 的整數,而且真的是整數。

    Anthropic 那條路的分數形狀是 API 用 strict enum 保證的;CLI 這條路沒有保證,
    所以這裡的嚴格程度必須跟那個 enum 一樣——替代品比被替代的鬆,等於換了後端就
    悄悄降低了標準。bool 在 Python 是 int 的子類,`True` 會被當成 1,要擋掉。
    """
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 5


def _parse_scores(raw, axes=_AXES):
    """從自由文字讀出評分,讀不出來就回 None(不猜)。

    對照 evals/judge_episodes.py 的 _parse_verdicts:少一軸、分數不在 0–5、理由是
    空字串——任何一項不合格就整份丟掉,不是留下半份。半份評分會變成「這一軸沒被
    評到卻有分數」,那正是 judge harness 開始跟自己同意的起點。
    """
    parsed = BACKENDS.extract_json_object(raw)
    if parsed is None:
        return None
    scored = {}
    for axis in axes:
        entry = parsed.get(axis)
        if not isinstance(entry, dict):
            return None
        reason, score = entry.get("reason"), entry.get("score")
        if not _is_score(score) or not isinstance(reason, str) or not reason.strip():
            return None
        scored[axis] = {"score": score, "reason": reason.strip()}
    if not _is_score(parsed.get("overall")):
        return None
    scored["overall"] = parsed["overall"]
    return scored


def _agy_prompt(card_text: str) -> str:
    shape = {axis: {"score": "0-5 integer", "reason": "one sentence"}
             for axis in _AXES} | {"overall": "0-5 integer"}
    return (RUBRIC + "\n\n" + "─" * 60 + f"\n\n待審的卡:\n\n{card_text}"
            + "\n\nReturn ONLY a JSON object — no prose, no code fence — "
              "shaped exactly:\n" + json.dumps(shape, indent=2, ensure_ascii=False))


def _judge_agy(card_text: str, model: str) -> dict:
    scored = BACKENDS.run_cli_sample(
        BACKENDS.cli_call_spec(model, _agy_prompt(card_text)),
        _AXES, parse=_parse_scores)
    if scored is None:
        raise RuntimeError(
            "agy 這次沒回出讀得懂的評分(逾時、非 JSON、缺軸,或分數不是 0–5 整數)"
            "—— 沒評到不等於通過,別拿這次結果當判決。")
    return scored


def _judge_anthropic(card_text: str, model: str) -> dict:
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
            model=model,
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


def judge(card_text: str, *, backend=None, model=None) -> dict:
    """評一張卡。評不到就拋 RuntimeError——沒評到不等於通過。

    後端不在 import 時解析:CI 兩個後端都沒裝,提前解析會讓「這台機器沒有可用模型」
    變成整份離線測試載不進來。resolve_backend() 自己就是為了能被注入而寫的
    (見 evals/judge_episodes.py 那支的 docstring)。
    """
    if backend is None or model is None:
        backend, model = resolve_backend()
    return _judge_agy(card_text, model) if backend == "agy" else \
        _judge_anthropic(card_text, model)


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
