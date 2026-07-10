# tests/agent/ — SKILL 行為層 eval harness

實作範圍:[`docs/eval-design.md`](../../docs/eval-design.md) §2 harness + §4 A/B 斷言。
分兩塊,判定哲學(§1「code-check > LLM-judge > 人工」)決定各走哪條:

- **離線機檢(確定性,進 `tests/run_all.py`)** — 能 regex / JSON diff 斷言的卡面/狀態鐵律。
- **LLM-judge(非確定性,需 API key,不進 CI)** — 只有「敘事品質」一項,判斷「好不好」而非「犯規沒」。
- **headless 產卡(非確定性 + 有成本,opt-in)** — 真跑 skill 產卡,再餵上面兩者。

## 檔案

**離線機檢(#60,`python3 tests/test_checkers_offline.py` 或併進 run_all.py 第 10 套)**
- `check_card.py` — 卡面鐵律機檢(A-2/A-3/A-6/A-12/A-13/B-7/B-9)。import 或 CLI。斷言權威 =
  eval-design §4 + card-spec.md;改鐵律要同步(見各檔頭)。
- `check_state.py` — 狀態檔 trajectory adherence(S-1..S-4 收尾產物 + 差分 / append-only helper)。
  **刻意不重造** coach.py / `test_tr_json_contract.py` 已擁有的 cycle_id 格式 / enum / commitment
  schema,只管那兩層管不到的收尾產物層。
- `../test_checkers_offline.py` — 上兩支的**驗活**(eval-design §6):乾淨輸入全過、刻意壞掉必掛
  對應條;check_state 用 coach.py【真實寫入】當 known-good oracle。無網路、確定性。

**LLM-judge(敘事品質,需 `ANTHROPIC_API_KEY`)**
- `judge_narrative.py` — judge 本體(五軸 + overall,rubric 抄 card-spec.md 敘事鐵律)。
- `run_judge_eval.py` — judge 的 mutation 驗活,跑 `fixtures/manifest.json`。
- `fixtures/` — 一張乾淨卡(`card_good.txt`,同時是 check_card 的乾淨參照)+ 兩張刻意壞卡。

**harness 編排(headless,opt-in)**
- `personas.md` — 5 個腳本化模擬用戶 + 差分對(eval-design §3)。
- `cases/*.yaml` — case 宣告(輸入 CSV / persona / 該套哪些斷言)。`washer` = §落地順序 step 1。
- `run_case.sh` — `--check <card> <state_dir>` 對已產出的卡/狀態機檢(離線核心,CI-verified);
  `--headless <case.yaml>` 隔離 HOME 跑 `claude -p` 產卡再機檢(需 claude CLI + API key)。

## 跑法

```bash
# 離線機檢(無需網路 / API key)——已併進 run_all.py
python3 tests/test_checkers_offline.py
python3 tests/agent/check_card.py tests/agent/fixtures/card_good.txt
python3 tests/agent/run_case.sh --check my_card.md ~/.trade-coach

# LLM-judge(需 API key;放 .env 或 export)
export ANTHROPIC_API_KEY=sk-...
python3 tests/agent/run_judge_eval.py

# headless 產卡(opt-in,需 claude CLI + API key,有成本、非確定性)
tests/agent/run_case.sh --headless tests/agent/cases/washer.yaml
```

## ⚠️ (c) 內心層的 headless 天花板(issue #60/#159)

Step 2「該問有沒有問」的**工具主路徑**(AskUserQuestion)headless 測不到——headless `claude -p`
沒有該工具,只會走 fallback 對話路徑(EVALS.md 2026-07-04 回歸紀錄實測)。所以:
- `check_card` / `check_state` 機檢的是**產出物**(卡/狀態),不管卡怎麼產的,離線可跑。
- 主路徑 adherence(工具問答的順序 / 差分敏感度)要**互動 session** 驗(case.yaml `run_mode: interactive`),
  或靠 Step 4 線上反饋——這是內心層無 ground truth 的固有天花板(#159 三層框架的 (c) 層)。

## 仍待辦(#60 較大本體)

- B-1 洗白者 eval-first 的 red→green 全流程(§落地順序 step 1):需互動 / headless 真跑一輪。
- check_card 的 case 特定斷言(B-1 標籤定位、B-9 section 級 ticker-in-洞):現只做卡層 invariants。
- grader 校準(§6):首批 transcript 人工全判對比機檢,量 FP/FN。

## 維護鐵律

`card-spec.md` 敘事鐵律改 → `judge_narrative.py` 的 `RUBRIC` 跟著改;
eval-design §4 的 A/B regex 改 → `check_card.py` / `check_state.py` 的對應 CHECK 跟著改。
同源判準漂移防線見 `docs/eval-design.md` §5。
