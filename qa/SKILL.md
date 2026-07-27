---
name: fomo-qa
description: 開發者 dogfood fomo-kernel 時，用來準備一個乾淨、一致的 QA 環境並引導走查一次完整復盤流程。用戶說 /fomo-qa、dogfood fomo-kernel、跑一次 fomo QA、走一次復盤驗收、準備乾淨的測試環境、幫我 QA fomo-kernel 時使用。核心目的是消滅「每個 session 測試環境不一樣導致重工」：固定版本閘（只在最新 origin/main 上測，落後就擋）+ 乾淨 detached dogfood worktree（永不拿來開發）+ 模擬新用戶（dogfood 專屬 coach root，用 qa_env.sh reset 清，絕不碰真實 ~/.trade-coach）+ 標準化資料源（真實交易 / mock persona / test-drive）。這是「開發維護 fomo-kernel 時用的驗收工具」，不是產品本身；要幫真實用戶復盤交易時用產品 skill fomo-kernel，不要用這個。絕不碰 investment_note 真實紀錄。
---

# fomo-qa

把「準備一個乾淨、一致的 fomo-kernel dogfood 環境」規範化。**這是開發/維護時的 QA 工具**，回答的是「我改完引擎後，站在真實用戶的角度走一次，體驗對不對」，並保證每次都測到**同一個東西**：最新的 `origin/main`。

**這是 fomo-kernel dogfood 的標準必經流程（v1 已固化，2026-07-20）**——以後所有 dogfood 都從這裡開始，不要臨場自己準備環境。

**跨 client 契約源（2026-07-21 起）**：`kol_collector/fomo-kernel` repo 的 `docs/qa-runbook.md`（PR #275）定義「什麼才算一次合規 QA run」——**七**道 gate（版本閘／隔離 root／receipt 全程／verdict+verify／archive manifest／隱私 lint／**findings disposition**），缺任一該場就不算 QA、結論不可引用。第七道是 2026-07-27 補的（#417）：前六道全過、卻沒留下任何可重播資產的場，正是這條迴圈一年來的實際樣子。這份 fomo-kernel 自己的 `docs/qa-runbook.md` + repo-root `AGENTS.md` **必須維持獨立於本 skill、獨立於任何個人 registry**——fomo-kernel 是給陌生人在任何機器 clone 的公開產品，不能預期對方有這份 skill；兩者不一致時以 runbook 為準。

**本 skill 是什麼**：把 runbook 的六道 gate 自動化成一套可重複執行的流程 + `qa_env.sh` 工具，供 ting 自己維護 fomo-kernel 時用（2026-07-21 起透過 `ai-harness` 的 discovery registry，在 Claude / Codex / Antigravity 三邊用同一個名字 `fomo-qa` 呼叫同一份 canonical 內容，見 `ai-harness/inventory/fomo-qa.json`）。**這不是給其他人用的**——一般外部用戶沒有這個 skill，也不需要，他們只會走上面那份公開的 `docs/qa-runbook.md`。

> 為什麼存在：2026-07-19 盤點發現 18 個 worktree 裡 17 個落後 main（最多落後 28 個 commit），dogfood 一直被跑在各自釘死 base 的開發 worktree 上——測到的是過去某個切片，事後還無從得知是哪一片。「跑得起來」不等於「測到最新版」。這個 skill 用機制擋掉它（對應 issue #250）。

> **在 eval 體系的位置**：這同時是 `docs/eval-design.md` 證據層級**第 4 層（Human review）**、第 1 觀測面（content-free interaction receipts）一直缺的執行流程。走查收尾產出一份 `ux_receipt` owner verdict，把 eval 現在標「pending owner dogfood」的那層變成可累積的機讀標註。**2026-07-27 起還多產一樣東西**：Step 6 把每個 miss 轉成 `evals/episodes/` 裡可重播的 episode，所以一場 dogfood 產的是永久回歸資產，不是一次性觀察——這才是「拿到的東西足夠穩定持續」的意思。

## 什麼時候用

- 用戶說 `/fomo-qa`、「dogfood fomo-kernel」、「跑一次 fomo QA」、「走一次復盤驗收」、「準備乾淨的測試環境」
- 改完 engine / SKILL.md / 卡片渲染後，想站在用戶角度驗收體驗
- 想確認某個改動在**最新版**上真的能跑、卡片真的出得來

**不是**用來幫真實用戶復盤交易——那是產品 skill `fomo-kernel` 的事。這個 skill 只負責「把驗收環境弄乾淨、弄一致」，走查本身仍照產品的 `SKILL.md`。

## 覆蓋範圍（v1 已固化，別越界宣稱）

這一期驗的是 **L1：環境一致性 + 引擎 CLI 契約 + agent 走查行為**。走完 `/fomo-qa` = 「引擎和 agent 行為在最新版上驗過了」，**不等於**「用戶在每個 client 上的體驗都驗過了」。以下是**已知後續、不在本流程**，走完別宣稱體驗全綠（那正是 [#230](https://github.com/atomchung/fomo-kernel/issues/230) 的假通過陷阱）：

- **L2 卡片視覺**（下一期）：卡片 HTML 沒有真的用瀏覽器渲染 + 截圖比對（現狀連 `test_card_html.py` 都只做字串斷言、零截圖）。畫面一致 / 佈局 / dark-mode 目前只能人眼看。
- **L3 互動交付**（部分固有天花板）：「選項按鈕真的出現、用戶真的點得到」是 client 層事實（Claude native options vs Codex 手打），headless 驗不到。這裡只驗到「卡片原文有貼進對話、問題有呈現」的契約層（可接 `tools/ux_receipt.py`）；Codex 原生互動那半是 #230 的天花板，靠 owner 人工 verdict。
- **通用 HTML 互動文檔 + 純文字最快完成體驗**：下一期。

## 硬隔離護欄（先讀，不可違反）

1. **絕不碰真實紀錄**：`~/Side_project/investment_note/` 是 ting 的真實投資紀錄，只有「真實交易」資料源會**唯讀**取用其中一個 CSV，其餘一律不讀不寫。
2. **coach state 隔離到 dogfood 專屬 root**：dogfood 一律用獨立的 `~/.trade-coach-dogfood`（與你真實用產品的 `~/.trade-coach` 完全分離）。清除用 `qa_env.sh reset`（先備份再清，且 fail-closed 拒絕碰真實 root / investment_note）。真實 `~/.trade-coach` 只由 `reset-fomo-coach.sh` 管，dogfood 流程永不碰它。不要自己寫 `rm` 清任何 coach root。
3. **只在 dogfood worktree 動手**：所有引擎指令都在 `qa_env.sh up` 建出的 detached worktree 裡跑。`qa_env.sh` 本身 fail-closed，只操作路徑含 `dogfood` 的 worktree，避免手滑丟掉別的 session 的未提交工作。
4. **不改產品碼**：QA 過程只讀不改。若走查中發現 bug，記下來、開 issue，不要在 dogfood worktree 裡順手改（它是 detached、拿來測的，不是開發線）。
5. **公開文字先過 privacy lint（#274 事故換來的）**：repo 是公開的，真實 ticker／具體金額／`TICKER#日期#序號` 識別碼**絕不得**出現在 issue/PR/留言/commit message——不只檔案，文字管道也算。凡這場 QA 用了真實交易資料，任何要貼上 GitHub 的草稿先跑（在 dogfood worktree 的 `skills/fomo-kernel/` 下）：

   ```bash
   python3 tools/privacy_lint.py --against ~/Side_project/investment_note/trades/fomo/trades.csv /tmp/draft.md
   ```

   exit 0 才准貼；有 hit 就改寫成去識別化描述（「N 檔個股」「集中度偏高」）再掃，直到乾淨。輸出是遮罩過的，lint 結果本身可安全展示。真實值只准留在本機（memory / 本機筆記）。

`qa_env.sh` 就在本 skill 目錄下。下面每個 `qa_env.sh` 指令的路徑以 Claude 端為例（`~/.claude/skills/fomo-qa/qa_env.sh`）；在 Codex 上換成 `~/.agents/skills/fomo-qa/qa_env.sh`，在 Antigravity 上換成 `~/.gemini/config/skills/fomo-qa/qa_env.sh`——三個都是 symlink，指向同一份 canonical `qa_env.sh`，內容完全一樣，純 bash、跟呼叫它的 client 無關。

**跨 client 執行落差（登記進 discovery registry 只保證找得到，不保證每一步都跑得對，見 `ai-harness` task 紀錄）**：
- `qa_env.sh`、`tools/ux_receipt.py`、`docs/qa-runbook.md` 六道 gate、Step 0–5 的流程骨架——三個 client 都能原樣執行，無需改寫。
- Step 4 走查裡「問題呈現」若用 Claude 的原生選項工具（例如 `AskUserQuestion`），這是 Claude 專屬能力；Codex/Antigravity 沒有等價工具，要退化成固定格式的純文字選項呈現，並在 `ux_receipt.py` 記 `plain_text` 模式（不是 `native_options`）。
- Step 4「試 widget 一次」的規矩鐵則裡提到的渲染管道測試（例如 Claude 端可能用到的某個 Artifact 類發布工具）是 Claude 專屬 MCP 工具；Codex/Antigravity 要用它們自己有的等價渲染能力測，或者根本沒有就直接記 `widget_attempt_failed` 降級成 markdown，不要照抄 Claude 端提到的工具名字。
- `qa_env.sh` 對「目前工作目錄／worktree」的假設，尚未在 Codex/Antigravity 自己的工作目錄模型下實測過——第一次在那邊跑建議先用 `status`（唯讀）確認行為符合預期，再往下走。

## 固定流程

### Step 0 — 版本閘（唯讀，先看全景）

```bash
~/.claude/skills/fomo-qa/qa_env.sh status
```

一眼看到：`origin/main` 最新 sha、dogfood worktree 落後幾個、**dogfood coach state**（隔離 root，不是真實 `~/.trade-coach`）是不是乾淨的 new-user。**落後就不要往下走**，先 Step 1 更新。把 `main@<sha>` 明確回報給用戶——這次 QA 測的就是這個版本。

### Step 1 — 乾淨 worktree（釘最新 main）

```bash
~/.claude/skills/fomo-qa/qa_env.sh up
```

建立（或把既有的刷新到）`~/Side_project/kol_collector/fomo-kernel-dogfood`，`--detach origin/main`。這個 worktree 專職 QA，永不拿來開發。之後所有指令的工作目錄：

```bash
cd ~/Side_project/kol_collector/fomo-kernel-dogfood/skills/fomo-kernel
```

### Step 2 — 模擬用戶狀態

**先把整個工具鏈路由到 dogfood 專屬 coach root**（與真實 `~/.trade-coach` 隔離；review.py / coach.py / `tools/ux_receipt.py` **三者都認 `TRADE_COACH_HOME`**（ux_receipt 自 #269 修復、PR #275 已 merge），一次 export，`prepare`/`preview`/`finalize`/`data-status`/receipt 全程一致）：

```bash
export TRADE_COACH_HOME="$(~/.claude/skills/fomo-qa/qa_env.sh coach-root)"
```

- **模擬全新用戶**（預設，走 first-review）：

  ```bash
  ~/.claude/skills/fomo-qa/qa_env.sh reset   # 備份後清 dogfood root 到 fresh new-user
  ```

- **模擬回訪用戶**（走 weekly-review / due-revisit）：**不要** reset。保留上一次 QA 留下的 dogfood coach state，直接進 Step 3。若要精準造一個「第二週」狀態，先以新用戶跑完一次完整流程（含 finalize），再用新資料跑第二次。**0720 教訓：fresh reset 場永遠測不到記憶承接與問題帳連續性（memory=not_applicable、先前問題帳是空的不會「卡住」你）——要驗「上次的問題有沒有追上來」必須走這條，別誤判成修復生效。**

先跟用戶確認要模擬哪一種；不確定就預設「全新用戶」。**這個 `export` 必須在 Step 4 走查的同一個 shell 維持**——若後續指令各自起新 shell，每個都要重跑這行。

### Step 3 — 選資料源（三選一，標準化）

| 資料源 | 指令用的路徑 | 適用 |
|---|---|---|
| **真實交易**（唯讀） | `~/Side_project/investment_note/trades/fomo/trades.csv` | 真驗收：問的是 ting 自己的動機，最能暴露問題 |
| **mock persona** | worktree 內 `mock/<persona>.csv`（見 `mock/SAMPLES.md`，如 `sample_ai_holder`、`sample_tw_mixed`） | 快、隱私零風險、可重現 |
| **test-drive** | `--test-drive`（無 CSV） | 純展示，`persist:false` 零寫入、隔離 root |

真實交易目前約 1125 筆、76 檔標的、含台美混市場與日期格式混用——是很好的壓力測試素材。

### Step 4 — 走查（照產品 fixed lifecycle，不重寫）

在 `cd .../fomo-kernel-dogfood/skills/fomo-kernel` 之後（並確認 Step 2 的 `export TRADE_COACH_HOME` 在這個 shell 生效——`prepare`/`preview`/`finalize` 才會全部落在 dogfood 隔離 root），照**產品** `SKILL.md` 的 fixed lifecycle 走。摘要（細節與邊界一律以產品 `SKILL.md` / `flows/*` / `references/*` 為準）：

```bash
# 1. prepare —— 讀 review_plan.flow_path 決定讀哪個 flow
python3 engine/review.py prepare <CSV 或 --test-drive> --language zh-TW
#    test-drive 要記下 review_plan.state_root，之後每個 preview/finalize/resume 都要 --root <state_root>

# 2. agent work —— 宣告 host capability、做質性判斷、問 question_queue 裡每個 required 問題、
#    為未覆蓋部位建 inferred thesis、寫「無數字」narrative（answers.json / narrative.json 要過 schema）

# 3. preview —— 驗證 + 渲染 private / public 預覽
python3 engine/review.py preview --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json

# 4. 內嵌完整卡片預覽 → 請用戶選一條規矩 / 自訂 / skip

# 5. finalize —— 原子提交 canonical bundle
python3 engine/review.py finalize --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json
```

**UX receipt 貫穿走查（強制——這是 QA 接進 eval 的載體）**：走查不是「引擎跑完」就算，每一步用戶可見的動作都要進 receipt（產品的 `tools/ux_receipt.py`）。這份 content-free receipt（只有 session id、能力、pass/fail，無 trade 內容）就是餵給 eval 第 4 證據層的機讀標註：

```bash
# prepare 後立刻宣告當前 client 真正能做什麼——如實宣告、別低報：
# 互動式 Claude 介面通常是 native_options+plain_text ＋ widget+markdown_inline
python3 tools/ux_receipt.py start --session-id <ID> --client claude --route first_review \
  --question-mode native_options --question-mode plain_text \
  --card-mode widget --card-mode markdown_inline
# 每問一題、每出一次卡，都在用戶真的看到「之後」記一筆
python3 tools/ux_receipt.py event --event question_presented --question-id <qid> --mode plain_text
python3 tools/ux_receipt.py event --event card_presented --stage preview --mode widget
# 用戶答完最後一題必答「立刻」先記這筆、再去跑 preview——這是 #236「答完→出卡」量測起點
python3 tools/ux_receipt.py event --event answers_received
# 「選一條規矩/自訂/skip」呈現給用戶時記一筆
python3 tools/ux_receipt.py event --event rule_choice_presented --mode plain_text
```

**三條走查鐵則（2026-07-20 owner_live 稽核修正，違反其一＝該場 QA 作廢）**：
1. **能力宣告如實＋widget 每 session 必試一次，但要挑對的工具**：0720 唯一走查偏差＝低報 `card_modes`、零 widget 嘗試，#249 富 HTML 卡生成了但 owner 全程只看到扁平 md（card=fail 主因）。圖形介面必宣告 `widget`，先試 widget、失敗記 `widget_attempt_failed` 再降級 markdown——別讓「artifact 綠≠交付」重演。

   **2026-07-21 新教訓（見 #230 留言）**：試 widget 不能隨便抓一個看起來像渲染工具的 tool 就用——generic 的圖表/dashboard 類視覺化工具（例如某些 MCP `show_widget`/`visualize` 工具）通常有自己一套獨立設計系統，會把第三方提供的大型自訂 `<style>` 區塊正規化或直接剝除，這不是「host 不能渲染 rich HTML」，是工具本身的預期行為（拿去餵它等於用錯地方）。驗 widget 交付要挑「能原樣保留提供的 `<style>`／HTML、不做設計系統正規化」的管道（例如 Claude Code 的 Artifact 類發布工具：自成一頁、不套用外部設計系統）；不要只因為工具名字聽起來像「widget」就當作等價於 `references/card-delivery.md` 講的「graphical surface: render a widget from the engine HTML artifact」。踩過一次沒分辨清楚，把「選錯工具」誤診成「host 沒有渲染能力」，回頭在 GitHub 上發了錯誤診斷、事後才修正——先確認工具契約（會不會保留原始 CSS），別急著下結論。

   **這段工具挑選細節只寫在這裡，不要往上搬進 fomo-kernel 的 `docs/qa-runbook.md`**：那份是 fomo-kernel 公開產品自帶、給任何陌生人 clone 都能讀的跨 client 契約源，目前用的是刻意不點名任何工具的 host-agnostic 語言（「有 rich render 能力就試、失敗就退化成 canonical Markdown」）——這樣才對，因為外部使用者的 client 上根本不會有 Claude 這幾個 MCP 工具，寫死工具名字對他們無意義。這段「怎麼分辨工具會不會正規化 CSS」的具體操作知識，是 Claude 特有的 MCP 工具生態細節，只在本 skill（`fomo-qa`，僅供 ting 自己維護 fomo-kernel 用，不是給外部用戶的公開契約）這裡展開就好，別在改 fomo-kernel 的 runbook 時手滑帶過去。
2. **`--language` 跟對話語言走**：中文對話一律 `--language zh-TW`（產品 SKILL.md Language 節已明文；0720 mock 場硬帶 en 造成 #262 中英夾雜）。
3. **量測「答完→出卡」**：`answers_received` → preview `card_presented` 的 ts 差就是機器等待秒數，收尾必回報（#236 複量儀器；事件與 ts 以 `tools/ux_receipt.py --help` 為準）。

完整事件序列見產品 `references/interaction-delivery.md`（**參數以 `tools/ux_receipt.py --help` 為準**——doc 偶有 drift，例如 `start --required-question` 已不存在於 code）。**fomo-qa 的差別 = 把這步從「產品建議」升級成「QA 不可跳過」**，因為沒有 receipt 這次 dogfood 就沒有機讀證據、無法進 eval。

**已知 `ux_receipt.py` CLI 坑（2026-07-21，連續兩場走查各踩一個，記下避免重犯）**：
- `artifact_generated` 一定要在對應 stage 的 `card_presented` **之前**、且是動作發生的當下就記——事後補記（即使內容正確）一樣會被 `verify` 判定「card was marked presented before its artifact existed」，append-only trace 沒有回頭修的辦法，該場只能作廢重來。別把記 receipt 這件事拖到走查後段一次補。
- `start --question-mode`/`--card-mode` 只需要宣告這個 client **額外**有的能力（`native_options`/`widget`）——`plain_text`/`markdown_inline` 這兩個通用 fallback，[PR #298](https://github.com/atomchung/fomo-kernel/pull/298) 之後 `start` 會自動幫你補上，不用再手動重複宣告（PR 未 merge 前，仍要照舊手動兩個都傳，否則 `verify` 會在收尾才報「capabilities must declare plain_text/markdown_inline」）。
- `response_mode`/`response_provenance` 只對 `headline_motive`/`add_thesis` 這類支援私有 surface 的題型有效；`due_revisit`/`rule_breach` 等engine-rendered 題型的 answer 物件裡完全不要帶這兩個欄位，帶了會報「own-words mapping is not enabled for this kind」。

QA 心態，走的時候盯這些（發現就記，別在這改）：
- 問題問得準不準？有沒有問到不相干 / 漏問關鍵動機？（呼應 #238 提問方向）
- 「答完 → 出卡」機器等了多久？久不久？（呼應 #236 5–10 分鐘等待；可留意 preview 被 reject 重寫幾次）——用鐵則 3 的 receipt ts 差直接量，別再靠體感
- 卡片文案有沒有數字幻覺、誠實揭露對不對、規矩有沒有連到實際持倉？
- 台股 / 混市場 / 現金 / 日期格式這些邊界有沒有出錯？
- **呈現候選規矩選擇時，agent 自己有沒有偷懶改寫/瞎編 `grounding`？**（2026-07-21 教訓，見 #293）`flows/*.md` 明講候選規矩的 `grounding` 要逐字引用、沒有 `grounding` 的候選不准編一句上去；這步完全沒有機械檢查，agent 求快時很容易犯——呈現前自己對照一次 `card_plan.candidate_rules` 的原始欄位。

### Step 5 — 收尾

1. **owner 判決 + 封存 receipt（QA 的核心產出，別跳過）**：final 卡出來後，你給一個 verdict——選項能不能點（controls）、卡片有沒有可讀地出現（card）、weekly 記憶有沒有承接（memory）、問題夠不夠具體（question-specificity）、答案映射對不對（answer-fit）。這正是 eval 一直缺的 Human-review 標註：

   ```bash
   python3 tools/ux_receipt.py event --event owner_verdict --controls pass --card pass --memory not_applicable --question-specificity pass --answer-fit pass
   python3 tools/ux_receipt.py verify --require-owner-verdict   # 必須綠
   # 封存：模型與 effort 必須從當前 host 的設定逐字抄錄；不可猜測或填 unknown/default
   ~/.claude/skills/fomo-qa/qa_env.sh archive-receipt <receipt-path> mock:sample_ai_holder owner_live \
     --agent-model '<exact-host-model-label>' --effort '<exact-host-effort>'
   ```

   封存會產出一份 **run manifest**（`<run_id>.manifest.json`），記全這次 dogfood 的來歷：`engine_version`（`main-<sha>`）、`agent.client`、`agent.model`、`agent.effort`、`data_source`、`human_involvement`、`owner_verdict`。模型與 effort 都是 archive 時明確提供的 host label；腳本不從 client 名稱、commit、聊天上下文或後續推論補值。缺任一、或填 `unknown`／`default`，archive 會 fail closed。這讓 report 能把不同模型和 effort 分開比較，而不把它們平均成同一個通過率。

2. **回報測了哪個版本**：`main@<sha>` + 資料源 + 模擬的用戶狀態 + 「答完→出卡」秒數（receipt ts 差）。
3. **發現的問題**：逐條記下。真的是 bug / 缺口就 `gh issue list` 查重後開 issue（別在 dogfood worktree 順手改）。**這場若用了真實交易資料，issue／留言草稿必先過護欄 5 的 `privacy_lint.py`，exit 0 才貼**。重大結論照 `EVALS.md` 的「Regression record」慣例補一列（receipt 是機讀帳，`EVALS.md` 是人讀帳）。

   **但開 issue 不是終點——先做完 Step 6 再回來封存**。issue 只記錄「出過事」，不會讓那次失敗可重播，下次沒人知道它是修好了還是只是沒人再踩到。
4. **清理**（可選）：（先確認 Step 6 做完了）
   - 想留著 state 供下次「第二週」測 → 不動。
   - 想回到全新 → 再跑一次 `~/.claude/skills/fomo-qa/qa_env.sh reset`（清 dogfood 隔離 root）。
   - 不再需要 worktree → `~/.claude/skills/fomo-qa/qa_env.sh down`（只移 worktree，不碰 state）。

### Step 6 — 把每個 miss 轉成可重播的 episode（gate 7，封存前的最後一步）

**這一步 2026-07-27 才補進本 skill，補的是一個實際發生過的洞**：repo 的 `docs/qa-runbook.md` 在 2026-07-26 就加了 step 6，但本 skill 停在 Step 5，收尾寫的還是「開 issue + 補 EVALS.md 一列」——正是 #417 要取代的舊行為。整個 skill 目錄裡「episode」出現 0 次。規矩寫在 repo 三份文件裡、一份都沒進到你真正會按的按鈕，所以一次都沒被執行過。**別再讓它退回成「記得就做」——現在 `archive-receipt` 會擋。**

走查中每發現一個 miss，**當場**轉成 episode，不要等收尾一起補：agent 產出的那段原文才是資產，下個 session 就沒了。

```bash
# 在 dogfood worktree 的 repo 根目錄
python3 evals/run_episodes.py --list        # 看 bank 已經靠哪些 fixture
# 照 evals/episodes/README.md 的 intake 步驟寫 EP-NNN-*.json（先寫 recorded miss，再寫修好的答案）
python3 evals/run_episodes.py EP-NNN        # 讀它到底踩了哪些 check，別用猜的
python3 tests/run_all.py
```

然後在 receipt 上記一筆「每個 miss 去哪了」——這是 runbook 的第七道 gate，`verify --require-findings` 會擋：

```bash
# 在 skills/fomo-kernel/ 下
python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded \
  --finding episode:EP-0NN \
  --finding 'not-episodable:#NN:為什麼這個沒辦法重播'
# 這場真的沒發現問題：
python3 tools/ux_receipt.py event --session-id <ID> --event findings_recorded --no-findings
```

三件事別搞錯：
- **`episode:EP-NNN` 會被拿去 `evals/episodes/` 對帳**——沒真的轉成 episode 就寫「已轉」會直接失敗，不是靠自律。
- **「這場沒發現問題」必須明講**（`--no-findings`），不能用「不記這筆」代替。省略不等於沒有。
- **不是每個 miss 都能 episode 化**，那就用 `not-episodable:#NN:<理由>`。例如「卡片到底有沒有出現在螢幕上」是 receipt 層的問題（Step 4），不是答案層的，寫清楚比硬塞一個假 episode 誠實。

**用真實資料的場**：episode 只留失敗的「結構」，真實 ticker / 金額 / 日期一律去識別化後再寫進 fixture——`privacy_trace` 是機械兜底（真值 trace 不到 synthetic fixture 就會紅），但它只是必要條件，不是充分條件。

## dogfood 帳：區分人為介入 + 跨版本聚合

archive 的 `human` 參數決定這筆算不算真體驗證據——這是整個帳的可信度關鍵：

| 等級 | 意思 | 算不算體驗 ground truth |
|---|---|---|
| `owner_live` | 你本人全程（答題 + 判 verdict） | ✅ **真 UX ground truth** |
| `agent_with_owner_verdict` | AI 走流程、你只給最終 verdict | 半人為 |
| `agent_simulated`（**預設**） | 全 AI 模擬，無真人 | ❌ 只驗契約，**不算體驗信號** |

沒主動標就是 `agent_simulated`——寧可低估可信度，也不默默把「AI 自證」當成「用戶說好」（#230 的核心教訓）。

看跨版本、跨人為介入的通過率趨勢：

```bash
~/.claude/skills/fomo-qa/qa_env.sh report
```

報告嚴格把 `owner_live` 和 `agent_simulated` **分開統計**，並再依 client、agent model、effort 分桶；舊 manifest 會標為 `legacy-unattributed`，不混進新的模型／effort 比較。末尾標明只有前者算 ground truth——`agent_simulated` 再多次全綠也不能宣稱「體驗好」。

## 一次性快速自檢

只想確認環境健康、不走完整流程時：

```bash
~/.claude/skills/fomo-qa/qa_env.sh status
```
