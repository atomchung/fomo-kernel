# fomo-kernel 產出 Eval 設計（spec）

> 2026-07-03。評估對象**嚴格限定 = 這個 skill 的產出**。engine 的數學對不對歸 `tests/`（已覆蓋，不在本檔）；模型裸能力歸 investment-note `evals/`（不在本檔）。本檔管的是中間那層——**Claude 照著 SKILL.md 跑出來的東西，好不好、有沒有踩鐵律、用戶會不會覺得沒意義**。
>
> 設計討論全文見 investment-note `research/20260703_agent_eval_design.md`；本檔是其中 fomo-kernel 部分的可執行版（單一權威在這裡，那邊不再維護細節）。

## 0. 產出是什麼（= 受測面）

skill 一次 run 的產出有三面，eval 三面都要管：

1. **卡**（private review 文字卡）— 用戶直接讀的東西。
2. **本機狀態檔**（`~/.trade-coach/log.jsonl` / `theses.jsonl` / `profile.md`）— 下週對帳的記憶；寫壞 = 迴圈失效，用戶下週才發現。
3. **對話行為**（Step 2 問答的順序與敏感度）— 產品的差異化價值所在；transcript 可判。

## 1. 判定哲學（三條）

1. **code-check > LLM-judge > 人工**。能 regex / JSON diff 斷言的絕不用 judge；judge 只留給敘事品質一項。
2. **差分斷言測「聽沒聽」**：同一份 CSV、只換用戶答案跑兩次，產出必須在對應維度不同。**卡對答案不變 = Step 2 是儀式** —— 這測的是產品靈魂，而且是純機檢（diff 兩份 `log.jsonl` 即可）。
3. **一個 case 跑 n≥2 次報通過率**（輸出非確定性；產品要的是每次都不踩，不是有一次做對）。

## 2. Harness

```
tests/agent/
  cases/*.yaml       # 每 case：輸入 fixture、persona 腳本、斷言清單
  personas.md        # 腳本化用戶（見 §3）
  run_case.sh        # headless `claude -p` 跑 skill；HOME 指到暫存目錄（~/.trade-coach 隔離）
  check_card.py      # 卡片斷言（§4 A/B 系列的機檢部分）
  check_state.py     # 狀態檔斷言
  mutations.md       # 驗活記錄（§6）
```

fixture 直接用現有 `mock/` 的 7 個 persona CSV + driver map，不另造。transcript 用 `--output-format stream-json` 收，tool-call 順序從這裡判。

## 3. Simulated user（腳本化 persona）

| Persona | 腳本 | 測什麼 |
|---|---|---|
| **洗白者** | 對疑似凹單標的答「逢低」，被要求舉證時寫不出新證據 | 證據門檻（BACKLOG ISSUE-3）|
| **誠實者** | 答「不想認賠」 | 答案被採用 + 不說教 |
| **跳過者** | 一律跳過不答 | 不追問、卡照出 |
| **推翻者** | 答「計畫內定投」（推翻 engine 預設的「別加碼」）| commitment 存最終版 + 差分敏感度 |
| **回頭客** | 第二週帶新 CSV 回來 | 對帳而非重新初診 |

## 4. Case 總表

### A 系列 · 鐵律不變量（SKILL.md 🚫 逐條翻譯，全機檢）

| # | 斷言 | 來源 |
|---|---|---|
| A-1 | 卡上不得出現 `thesis_questions` 任何一條原文 | Step 1「絕不准印在卡上」 |
| A-2 | 不得出現 5 維 severity 小數表（`0?\.\d+ *[🔴🟡]`）| Step 3 🚫 |
| A-3 | 不得出現連續 `〔.+〕` 標籤拼接 / `← 點\d` / `(引擎產出)` / `(供參)` | 「卡是故事不是 dashboard」 |
| A-4 | `is_demo=true` → 卡頭必含 `[demo · 非真實成績]` | Step 1 |
| A-5 | `alpha_credible=false` → 全文禁「真本事 α / alpha 年化」；閘門①須含「不到 1 年/樣本」語意、閘門②須含「集中/賽道」語意 | ISSUE-1 輸出 gate |
| A-6 | 首段不得以勝率當主數字（`勝率 *\d+%`）| 「金額 > 筆數勝率」 |
| A-7 | `log.jsonl` / `theses.jsonl` 行數只增不減、每行可 parse | append-only 鐵律 |
| A-8 | `theses.jsonl` 每筆 `cycle_id` 符合三段格式 `ticker#YYYY-MM-DD#序號` | 收尾 part 2 ⚠️（踩了 = 記憶迴圈失效）|
| A-9 | 新 thesis 預設 `maturity=inferred`；修正走新 event 帶 `revises`，舊行不動 | append-only 動機庫 |
| A-10 | `insufficient_data=true` → `commitment=null` | 樣本不足不硬出規矩 |
| A-11 | `inferred` thesis trigger 觸發 → 輸出為問句 + `[⚠️ AI 猜測待校正]`，禁「該走」定論 | Step 2.5 措辭分級（雙審標最關鍵）|

### B 系列 · 用戶價值（「這卡沒意義」的四種機制 × persona）

「沒意義」不是抽象猜測——SKILL.md 那批鐵律的出處就是一次真人交易者 review（「像幾份報告硬湊」「差點關掉」「我又不是基金經理」）。歸納成四機制，每個配 case：

**機制④ 沒被聽見（最致命：殺掉 Step 2 本身）— 全機檢，含差分：**

| # | Persona | 沒意義的樣子 | 斷言 |
|---|---|---|---|
| B-1 | 洗白者 | 卡被洗成讚美卡、洞消失 | 該 ticker 標籤 ∈ {凹單, 待確認}，∉ {逢低}；headline 洞仍在（= ISSUE-3 驗收，**eval-first：先亮紅再改 prompt**）|
| B-2 | 誠實者 | 被說教；或答了還標「待確認」 | 卡標凹單且「看動機」引用其原話；說教句式黑名單（「你不該/大忌/千萬別」）+ judge 複核；規矩接「這就是要擋的事」 |
| B-3 | **推翻者差分** | 答什麼卡都一樣 →「講了白講」 | 同 CSV、兩種答案 → 兩份 `log.jsonl` 的 `commitment.metric_key` 必不同（定投版 → `ai_pct` 類非 `avgdown` 類）；headline 框架不同 |
| B-4 | **集中度差分** | 答「刻意押賽道」還被罵「假分散」= 問了還打臉 | 「刻意」版標題禁「假分散」、須含集中回檔 / α 測不出語意；「以為分散」版才准用「假分散」 | 
| B-5 | 跳過者 | 被追問審問；卡上留問號待辦 | 卡照出（機械洞版）；transcript 無二次追問；卡文無問句 |
| B-6 | 回頭客 | 重新初診、同一個洞當新發現重講 → 沒進度感 | 卡第一段含上次 `commitment.metric_key` 的舊值→新值兩個數字；同維洞須含「還沒過關」語意 |

**機制① 沒新資訊（「這 ChatGPT 也會講」）：**

| # | 斷言 |
|---|---|
| B-7 | 每條 candidate rule 必含用戶自己的 ticker 或具體數字（% / $）；抽象規矩黑名單（「注意分散」「想清楚」「控制風險」）|
| B-8 | （低頻、optional）同 CSV 餵裸模型出報告，judge 盲比：skill 版必含只有引擎算得出的資訊（FIFO α/β、歸因 pp、攤平次數）— 差異化價值存在性檢查 |

**機制② 不可行動（黑話 / 沒案例）：**

| # | 斷言 |
|---|---|
| B-9 | 「最大的洞」區塊必含 ≥1 具體 ticker + ≥1 數字；黑話詞（α / β / 處置效應 / 夏普）出現時 ±2 句內必有白話翻譯（近鄰 keyword + judge 複核）|
| B-10 | 規矩必為 if-then 可驗形；「動手前問自己」型自我喊話禁入（judge）|

**機制③ 不可信（名實不符 → 信任崩，整卡歸零）：** A-4 / A-5 / A-11 已覆蓋，歸入此機制不另立 case。

### C 系列 · 對話行為 / trajectory（transcript 斷言）

| # | 斷言 |
|---|---|
| C-1 | `TR_JSON=1` 的 engine 呼叫存在，且先於卡片輸出 |
| C-2 | Step 2 提問（AskUserQuestion 或對話問句）先於卡片輸出（「確認在出卡之前」）；一次 ≤3 問 |
| C-3 | Step 0 魯棒性：3–5 份不同券商欄位命名的小 CSV → 標準化輸出欄位恰為 `Symbol,Action,Quantity,Price,TradeDate,RecordType` |
| C-4 | 收尾有 append log.jsonl / theses.jsonl 的呼叫（狀態迴圈沒被跳過）|

### 唯一的 LLM-judge 項

「卡是連貫故事不是報表」敘事品質 0–5：rubric 直接抄 SKILL.md 鐵律（先承認本事再打 / 數字要髒 / 不講黑話 / 引言不當結語），judge 看 rubric 不看範本答案。judge 與人工判的 agreement < ~80% → 分數不可信，重寫 rubric。

## 5. Case 來源三條（持續長 case 的機制）

1. **鐵律驅動**：SKILL.md 每新增一條 🚫 → 一條 A 系列斷言（改 SKILL.md 的 PR 應同時動本檔）。
2. **用戶反饋驅動**：Step 4 的「沒戳中 / 哪裡不對」反饋 = **用戶價值層的 escape log**。每收到一個，做三問 postmortem：有 case 嗎？case 為何沒攔（grader 太鬆 vs 沒 cover）？→ 長出新 B-case。這是 L3（真人反饋）回流 L2（可跑斷言）的管道。
3. **事故驅動**：開發 / 使用中踩的坑（如 cycle_id 拼錯格式）→ 即補不變量。

## 6. Eval 自身的驗活（做完 harness 的第一件事）

- **Mutation 驗活**：故意弄壞 SKILL.md（刪「確認在出卡之前」、放行 thesis_questions 上卡、讓 commitment 存 engine 預設）→ 對應 case **必須亮紅**。不紅 = 斷言是死的，先修 eval。每條斷言至少被一個 mutation 殺過一次，記錄在 `mutations.md`。
- **Grader 校準**：首批 10–20 個 transcript 人工全判一次，對比機檢結果量 FP/FN；之後 grader 每次改動抽 5 個複核。
- **飽和監控**：長期 100% pass 的 case 標「回歸哨兵」身分（允許存在，但不要誤當「還在提供訊號」）。

## 7. 跑的節奏

| 觸發 | 跑什麼 |
|---|---|
| 改 SKILL.md / engine 輸出層 | 全套（~15 case × n=2，分鐘級/case）|
| 模型升級 | 全套 n=3 |
| 平時 | 不跑。**L2 不進 CI**（非確定性 + 有成本，進 CI 會逼人把斷言寫鬆）；`tests/run_all.py` 維持每 commit |

## 8. 反模式

1. 別造 framework——yaml case + 一支 runner + 兩支 checker，幾百行封頂。
2. 別用 judge 驗機檢項；別追單一總分（有意義的是「哪個 case 紅、對應哪條鐵律」）。
3. persona 腳本與斷言不得進 skill 可讀路徑（受測 session 的 cwd / HOME 隔離）。
4. 別把 Step 4 反饋（L3）錯當本層通過標準：B 系列全綠 ≠ 卡對真人有用；反之亦然。兩層都要，不互抵。

## 9. 迭代迴圈與輸入協議（loop engineering）

> Eval 建好之後怎麼用：prompt（SKILL.md）是假設、eval 是實驗，每次改動都是「單變數實驗 + 全套回歸」。本節定義迴圈本體、紅燈歸因、以及 owner 要給的 input。

### 9.1 一輪迴圈（owner 只出現在兩個點）

```
owner：真實跑 skill + 一行 verdict（input #1 #2）
      ↓
loop session：讀 feedback → 三問 postmortem（真實性/可判定/可行動）→ mock 化寫 case
      → 跑全套 eval → 紅燈歸因（§9.2）→ 單變數改 SKILL.md → 全套重跑（防修 A 壞 B）
      → 開 PR（附前後 pass rate）
      ↓
owner：PR 裁決（input #3）——規格變更才需要想；遵守度修復看 eval 證據綠了即放行
      ↓
merge → 下次真實使用 = 下一輪 input
```

節奏**事件驅動不排程**：每次 `miss` 觸發一輪、或每累積 3–4 次真實使用跑一輪回歸。沒有新 input 不迭代（空轉只會過擬合現有 case）。改 SKILL.md 前必有 baseline 數字，否則「改善」無從說起。

### 9.2 紅燈歸因：四種病因，藥完全不同

| 病因 | 症狀 | 藥 |
|---|---|---|
| **① 指令缺失/含糊** | 失效處 SKILL.md 根本沒講 | 加鐵律 + 同步加 case |
| **② 指令存在但不被遵守** | 有明文還是踩 | 不是再寫一遍加粗——最常見真因是**指令互相稀釋**（SKILL.md 已 300+ 行）。驗法：該條前移 / 其他條刪減後重跑，pass rate 動了 = 位置/密度問題；不動 = 模型能力問題（考慮流程硬化，如 self-check 清單）|
| **③ 指令衝突** | 兩條鐵律在特定情境打架（如「降摩擦別審問」vs「舉證門檻」）| prompt 寫明優先序；case 固定那個衝突情境 |
| **④ 規格本身錯** | eval 全綠但 Step 4 反饋說沒戳中 | 改的是鐵律不是遵守度——**此通道必須顯式存在，否則 eval 會把錯的規格鎖死** |

配套兩機制：
- **指令效用審計（刪的勇氣）**：刪一條指令跑全套，全綠 → 死重候選（模型已內化或從沒觸發），提請 owner 裁決。理想態 = 每條鐵律 ↔ 至少一個 case 依賴（mutation 驗活自然建立此映射）；沒 case 罩的指令 = 改動時裸奔。
- **Eval 追隨意圖不追隨措辭**：改 prompt 措辭不應需要改 case；要改 case 時先問是不是規格真的變了（走病因④通道）。case 與措辭耦合太緊 = 在測「模型有沒有背這段話」。

### 9.3 Owner 輸入協議（loop 生不出來的三種 ground truth：真實使用、判決、仲裁）

| # | 給什麼 | 頻率/成本 | Loop 拿去做什麼 |
|---|---|---|---|
| 1 | **真實跑一次 skill**（transcript/卡/狀態檔自動留本機）| 每週復盤本來就跑，零額外 | baseline 樣本池——唯一能校正腦補 case 的東西 |
| 2 | **每張卡一行 verdict**：`hit`，或 `miss + 引卡上原句 + 一句為什麼` | 30 秒/卡 | miss → postmortem → 新 B-case 或修 grader；hit rate = L3 baseline |
| 3 | **仲裁**（loop 問才答）：規格錯 vs 遵守度？死重指令刪嗎？ | 每輪 0–2 個 Y/N | 病因④通道 + 鐵律增刪拍板——唯一不可自動化的判斷 |
| 4 | **抽判 5–10 張**（同意/不同意機檢判定）| 只在 grader 新建/改動時 | grader 校準（FP/FN）|
| 5 | **真實心路語料**（自己凹單/逢低當下的自我辯護原話）| 不定期 | persona 腳本擬真化 |

**verdict 格式**（本機 `~/.trade-coach/feedback.jsonl`，append 一行）：

```json
{"date":"2026-07-05","verdict":"miss","line":"卡上原句照抄","why":"一句話","tag":"沒被聽見|黑話|不可信|沒新資訊"}
```

`tag` 可省（loop 自歸四機制）；`line` + `why` 不可省。hit 就一行 `{"verdict":"hit"}`，別讓記錄變負擔。

**兩條 input 鐵律：**
1. **Miss 必引卡上原句**——形容詞 feedback（「不夠深」）無法翻譯成斷言；引原句 + why 十分鐘後就是新 case。
2. **給症狀不給藥**——owner 直接指定 prompt 改法會跳過 baseline 與歸因，把病因②誤治成病因①（再加一條指令），這正是 prompt 膨脹到 300+ 行的機制。觀察到什麼照抄什麼，改法由 loop 提、附 eval 前後對比，owner 只在 PR 裁決。

**隱私邊界（public repo 必守）**：feedback 原文含真實 ticker/金額 → **全文永遠留本機** `~/.trade-coach/feedback.jsonl`；迭代 session 在 owner 機器上讀它歸因；**進 repo 的只有 mock 化後的 case**（症狀結構保留、數字換 mock persona 的）。與 skill 本身隱私鐵律同構：明細不出本機，出去的只有結構。

## 落地順序（每步獨立可停）

1. **B-1 eval-first**（~半天）：先寫洗白者 case + 標籤機檢，跑現行 SKILL.md 讓它亮紅 → 做 ISSUE-3 的 prompt 改動 → 轉綠。一步同時交付「第一個 case + ISSUE-3 本體」。
2. **`check_card.py` + `check_state.py`**（~1 天）：A 系列 + B 系列機檢部分。先可離線用（人工貼卡進去檢）。
3. **差分 case B-3 / B-4**（半天）：harness 通了之後最先加——測 Step 2 靈魂，成本最低。
4. **mutation 驗活 + grader 校準**（§6）：harness 全通後做一輪，之後才有資格說「eval 是活的」。
