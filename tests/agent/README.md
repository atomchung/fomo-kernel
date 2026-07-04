# tests/agent/ — 敘事品質 LLM-judge

實作範圍:[`docs/eval-design.md`](../../docs/eval-design.md) §4「唯一的 LLM-judge 項」
（卡是不是連貫故事、不是報表拼接），以及 §6「Mutation 驗活」對這個 judge 本身的驗活。

**這裡沒做的**（§2 `tests/agent/` 完整 harness的其餘部分，仍是待辦）：
- `cases/*.yaml` + `personas.md` + `run_case.sh`：headless `claude -p` 真的跑 skill 產卡
- `check_card.py` / `check_state.py`：A/B 系列的機檢斷言（regex/JSON diff，不該用 judge）
- 差分 case（B-3 推翻者 / B-4 集中度）

這支 judge 目前吃的是**人工準備或已產出的卡片文字**，不是自動跑 skill 產生的——
接上 §2 的 harness 之後，`run_case.sh` 產出的卡可以直接餵給 `judge_narrative.py`。

## 檔案

- `judge_narrative.py` — judge 本體。rubric 抄自 `card-spec.md` 敘事鐵律，五軸
  （連貫敘事 / 先肯定再打 / 數字要髒有案例 / 白話不裸奔內部名 / 引言不當結語）+ overall，
  透過 forced tool-use 拿結構化 JSON。
- `fixtures/` — mutation 驗活用的固定卡：一張乾淨（`card_good.txt`）、兩張刻意壞
  （`card_bad_dashboard.txt` 格式類違規、`card_bad_vague.txt` 內容空洞類違規），
  `manifest.json` 記每張的預期判定。
- `run_judge_eval.py` — 跑 manifest 裡全部 fixture，每個跑 N 次取多數決（非確定性），
  驗 judge 真的能把乾淨卡判高、壞卡判低。任何一條不符 = judge 是死斷言，先別拿去用。

## 跑法

```bash
pip install -r requirements.txt          # 含 anthropic + python-dotenv
export ANTHROPIC_API_KEY=sk-...          # 或放專案根目錄 .env（已在 .gitignore）
python3 tests/agent/run_judge_eval.py
```

單張卡手動評分：

```bash
python3 tests/agent/judge_narrative.py tests/agent/fixtures/card_good.txt
cat my_card.txt | python3 tests/agent/judge_narrative.py -
```

## 成本與非確定性

`run_judge_eval.py` 預設每個 fixture 跑 2 次（`TR_JUDGE_N_RUNS` 可調），3 個 fixture
= 6 次 API 呼叫，單次幾千 token，成本很低。judge 輸出非確定性，單次判紅判綠不算數——
這也是 eval-design.md 判定哲學第 3 條（「一個 case 跑 n≥2 次報通過率」）的原因。

## 維護鐵律

`card-spec.md` 的敘事鐵律（「卡片是一個故事，不是 dashboard」章節）改了 → 這裡的
`RUBRIC` 常數要跟著改，否則 judge 判的標準和 SKILL 實際要求的標準會漂移
（同源判準見 `docs/eval-design.md` 開頭的分工說明）。
