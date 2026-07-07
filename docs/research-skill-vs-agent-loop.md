# Research · Skill vs Agent Loop:整個產品做完之後,乘載形態要不要換?

> 狀態:研究筆記(worktree 草稿,未進 issue)
> 日期:2026-07-07
> 來源:owner 提問「整個產品做完了,skills 還適合乘載嗎?是不是該是一個 agent 裡有很多 skills/工具?假想 Claude for Trading,還缺什麼 harness」+ 現況體檢(SKILL.md/engine/issue #12/prd-investment-os.md)+ Claude Agent SDK 官方文件查證(2026-07-07)
> 定位:issue #12 開放問題 **#5(一個 OS vs 同引擎多前端)** 的 harness 層延伸;`prd-investment-os.md` 拍板的「同一薄引擎 + 雙前端」在本檔升級為「同一引擎 + 可換 harness」

---

## 0. Reframe:「skill vs agent loop」是假對立

「是不是應該是一個 agent 裡面有很多 skills or 工具」——**今天已經是了**:

```
Claude Code            = agent loop(harness:loop/工具/權限/記憶/UI/排程)
/fomo-kernel SKILL.md  = 行為契約(能力單元)
engine/*.py (3165 行)  = 確定性工具
~/.trade-coach/        = domain 狀態(5+ 檔案)
AskUserQuestion, hooks = UI 原語與護欄
```

Skill 從來不是 agent 的對立面——skill 是**搭在別人 agent loop 上的能力單元**。所以真正的問題是兩個:

- **Q1(harness 所有權)**:繼續借 Claude Code 的通用 loop,還是自建專用 loop?
- **Q2(控制權分佈)**:哪些行為由 prompt(軟約束)保證,哪些由 code(硬約束)保證?

Q2 比 Q1 急迫,而且 **Q2 的答案與 Q1 的選擇無關**——不管殼換不換,確定性都該往 code 搬。

## 1. 現況體檢:SKILL.md 已經在用 prose 實作 harness

SKILL.md 現在 399 行(~25k tokens,官方建議 Level 2 <5k)。其中至少四類內容不是「行為契約」,是 **harness 職責用 prose 寫**:

| 類別 | 現在的位置 | 本質 |
|---|---|---|
| 路由 dispatch(初診/對帳/試駕/snapshot-only) | 開場 prose | agent loop 的 dispatcher |
| 狀態演算法(Step 2.5 active-thesis 重建:revises/superseded/closed/exit_narrative 排除) | prose 演算法,每週讓 LLM 重新執行 | 純函式(錯一次 = 錯帳) |
| 消重/記憶管理(答過不重問三例外、exit capture 消重) | 鐵律 prose | state query |
| 收尾落盤腳本(~50 行 python heredoc) | 內嵌在 SKILL.md | 已是 code,住錯地方 |

第五類是**防禦性鐵律**(絕不編動機/絕不印 thesis_questions/不准代選/別攤 5 維表)——每條都是一次「Claude 走歪」的 patch,本質是 prompt-space 的 assert。

**膨脹規律**:每加一個功能(賣出 capture、horizon 對帳、幣別)= 一段流程 + 幾條鐵律 + 幾條消重規則。線性增長不封頂,而 prompt 遵循度隨長度遞減。**Skill 形態的天花板不是「裝不裝得下」,是「遵循度 × 每次執行的變異」**。

但今天這個稅還付得起:低頻(週一次)、owner = 維護者、且 **迭代速度是最大紅利**(改 prose 就 ship)——當前卡點是「卡不夠好用」(owner 2026-07-05 判定),這正需要最快的迭代殼。

## 2. Skill 形態的結構性缺口(prompt 怎麼寫都補不了)

對照「Claude for Trading」終局,四個缺口:

1. **觸發權(initiative)**:skill 只在用戶開 session 時活。教練價值高峰在**決策前**(pre-trade gate)與**事件時**(exit_trigger 燒到、持倉異動)——現在連「週日提醒你復盤」都做不到。緩解:Claude Code 已有 desktop scheduled tasks(本機、app 開著就跑)與 cloud Routines(全無人值守、可 webhook 觸發),可廉價原型主動性,不必換 harness。
2. **執行保證**:「絕不」寫在 prompt = 每次執行重擲骰子。金融場景的絕不清單(不下單/不外傳/不編數字)要求 code-grade。repo 已有正確前例——`.gitignore` 擋真實 CSV、hooks 擋未測試 commit——但**行為層**(卡上不出現 X、問過的不重問)還沒有等價物。
3. **狀態 schema 所有權**:jsonl + prose 讀寫規則 = schema 活在 prompt 裡。engine 已在收編(ledger.py/revisit.py 有 CLI 子命令)——方向正確,還沒收完。
4. **分發/計費形態**:skill 分發 = 用戶要有 terminal + Claude 訂閱(用戶額度買單、開發者零邊際成本);「Claude for Trading」的目標用戶(散戶交易者)不在 terminal 裡。App 化 = Agent SDK + API 計費(**開發者買單**)+ 自有 UI——這是商業決策驅動 harness 決策,不是技術偏好。

## 3. Agent SDK 查證結果(2026-07-07,官方文件)

| 事實 | 對本 repo 的意義 |
|---|---|
| SDK 提供完整 harness 原語:loop、內建工具、MCP、hooks(7 型)、subagents、sessions/resume、permissions、AskUserQuestion | 自建 loop 的成本大半被 library 化 |
| **SKILL.md 可無痛搬進 SDK**(`setting_sources` + `skills` 參數,三層漸進披露保留;唯 `allowed-tools` frontmatter 改由 query 的 allowedTools 控) | **skill 投資不沉沒**——現在寫的行為契約就是未來 app 的行為契約 |
| SDK 不內建排程(外部 cron/launchd/Lambda);Claude Code 有 desktop scheduled tasks + cloud Routines | 主動性可以在 skill 形態先原型 |
| 官方分工:skills = 跨 surface 可攜能力;subagents = context 隔離工人;SDK = 需要 programmatic lifecycle / 嵌入自有 app 時 | 與本檔三階段路徑一致 |
| 計費:skill = 用戶訂閱額度;SDK 自建 = 開發者 API 帳單(2026-06 的計費分離改革延期中,勿依賴現狀) | 換 harness 的那天 = 商業模式要先想好 |

來源:code.claude.com/docs/en/agent-sdk/overview、/agent-sdk/skills、/scheduled-tasks、/routines、anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills

## 4. Claude for Trading 的 harness bill of materials(還缺什麼)

| 組件 | 現在(skill on Claude Code) | 終局需要 | 缺口 |
|---|---|---|---|
| Agent loop | Claude Code 免費給 | 專用 loop(復盤/對帳/gate 多模式) | 小——SDK 可承接 |
| 確定性計算 | engine 3165 行,pure Python、tool-agnostic | 同,或再加 API 化 | **零——最大資產** |
| 行為契約 | SKILL.md + card-spec + AGENTS.md | 同,拆 per-phase | 小——可攜(已查證) |
| 狀態存儲 | jsonl + prose 讀寫規則 | schema 化 state store + migration | 中——engine 收編中 |
| 觸發/排程 | 用戶開 session | 盤後 cron + 事件 watcher + 推播 | **大(缺口一)** |
| 資料饋送 | 每週手動 CSV/截圖 | 券商 API(SnapTrade/Plaid 類)或持續手動 | **大(缺口二,且與「資料留本機」隱私鐵律有張力)** |
| 護欄 | prompt 鐵律 + gitignore + hooks | policy engine(絕不清單 code 化、render assert) | 中 |
| 對話 UI | terminal + AskUserQuestion | 卡片 UI + 一鍵回答 + 通知(demo-card.html 已是雛形) | 大(產品化時才付) |
| 記憶消重 | prose 消重鐵律 | engine query(「這週該問誰」) | 中——可先 engine 化 |
| 評測 | tests 八套 + evals/ 雛形 | 卡質量行為迴歸 | 中 |
| 計費/分發 | 用戶自己的 Claude | API 計費 or BYO-key or 訂閱 | 商業決策 |

**關鍵洞察:兩個最大缺口(觸發權、資料饋送)與「skill vs agent loop」之爭正交**——就算今天自建 loop,還是得解券商資料與推播通道。所以「換 harness」不是解鎖終局的關鍵路徑;**把 engine 長成完整狀態機 + 原型主動性**才是。

## 5. 建議路徑:三階段,各有升級觸發訊號

### Stage 1(現在):skill 形態不動,開始降 prompt-space 稅

- 判斷:當前卡點是產品問題(卡不夠好用),換 harness 不解決,反而凍結迭代速度。
- 動作:**確定性內容持續下沉 engine**——
  - 收尾落盤腳本 → engine 子命令(如 `engine/session.py close`)
  - active-thesis 重建演算法 → engine 輸出(SKILL 只讀結果)
  - 消重判定 → engine 輸出「這週該問誰」清單(dedup 已套用)
  - 開場路由判定 → engine 輸出 route(初診/對帳/snapshot-only)
- **分界原則:SKILL.md 只留「怎麼跟人說話」(語氣/敘事/問法/鏡片),所有「怎麼算/怎麼記/問誰」下沉 engine。**
- 判準儀表板:SKILL.md 每條鐵律 = 未來一個 assert;每段流程演算法 = 未來一個函式。**鐵律的增長速度 = 該搬家的訊號強度。**
- 副作用即收益:SKILL.md 從 ~25k tokens 往 <10k 收,遵循度回升,今天就賺。

### Stage 2(訊號:主動性需求被驗證):同 engine 多入口

- 訊號:owner 自己想要「它來找我」;或週迴圈黏著驗證成立(連續 N 週用)。
- 動作:scheduled task / launchd 盤後跑 engine scan(trigger 燒到 → 通知);skill 仍是對話前端;engine 長出 daemon 半邊。**零 harness 遷移。**
- 這是「Claude for Trading」最有辨識度的能力(主動教練)的最便宜驗證法。

### Stage 3(訊號:商業化拍板):Agent SDK 產品化

- 訊號:付費意願驗證 + 目標用戶明確不在 terminal + 願意從「用戶額度買單」翻轉成「開發者 API 帳單」。
- 動作:SDK 承接 loop;SKILL.md 直接搬(已查證可攜);engine 原封不動;新建 UI/推播/券商連接/計費;**絕不清單全部 code 化**。

## 6. 終局圖像:對,「一個 agent 裡很多 skills」,但不是平鋪

五個生命週期階段(找資訊→選股→交易→update→recap)不是五個 skill 平鋪,而是——

```
一個 dispatcher(路由到階段;漸進披露:對帳 session 不載入選股 prompt)
+ 共用狀態(engine 管,schema 化)
+ 各階段薄行為契約(現在的 SKILL.md 拆成 per-phase)
+ 少數 subagents(Phase C/D 研究類長任務,context 隔離)
+ 確定性全在 engine,護欄全在 code
```

Claude Code 自己就是這個圖像的參考實作(skills 平時只佔 name+description,觸發才載入)。

**一句話:engine 是產品,harness 是殼。** Skill 是第一個殼(迭代最快),daemon 是第二個半殼,SDK app 是第三個殼。確定性持續下沉 engine,殼就能隨商業形態換而不重寫產品——issue #12 開放問題 #5 的「同一薄引擎 + 多前端」在 harness 層依然成立,且查證後(SKILL.md 可攜)更成立。

## 7. 開放問題(下次討論)

1. Stage 1 下沉的第一刀切哪裡?(建議:收尾腳本 → engine 子命令,最小、最無爭議)
2. 資料饋送的隱私張力:券商 API 進來後「資料留本機」鐵律怎麼守?(本機 daemon 拉、雲端不落地?)
3. pre-trade gate 的形態:skill 內的一個模式,還是獨立輕入口?(它的觸發時機在「下單前」,天生不在復盤 session 裡)
4. Stage 2 的通知通道選型:launchd + 本機通知 / cloud Routine / 手機推播,各自與隱私鐵律的相容性。

---

# Part 2 · 終局藍圖:一個獨立 agent 乘載完整決策鏈

> 2026-07-07 owner 追問的正面版:假設做一個獨立 agent,乘載 ① 買啥 ② 多少買 ③ 賣嗎 ④ 交易做得好不好、是否修改決策 ⑤ 績效/資金分布/風報比。架構長什麼樣、skills 與 loop 各放哪、harness 還缺哪幾塊。

## 8. 出發點:五項功能的計算本質不一樣

架構不該從「功能清單」出發,該從**每項的計算性質與時間性**出發:

| # | 功能 | 判斷 vs 計算 | 時間性 | 風險 |
|---|---|---|---|---|
| ① 買啥 | **判斷為主**(檢索+合成,唯一需要多步 agent 推理的) | 非同步長任務(研究可跑很久) | 最高(建議責任、紅線) |
| ② 多少買 | **幾乎純計算**(風險預算/波動率/相關性),輸入才是判斷(conviction) | 決策當下,秒級 | 中 |
| ③ 賣嗎 | 一半計算(trigger 監控)+ 一半判斷(thesis 還成立嗎) | **事件驅動**(盤中/盤後) | 高 |
| ④ 好不好、改決策 | 混合(行為診斷計算 + 動機對話)= 現有 fomo-kernel | 週期性(週/月) | 低 |
| ⑤ 績效/分布/風報比 | **純計算**(α/β/exposure/drawdown/R) | 隨查 + 定期 | 低 |

三個直接的架構推論:
- **② 和 ⑤ 根本不該是 LLM 的工作**——是 engine 的自然擴張,LLM 只負責人話↔參數的翻譯。
- **① 是唯一真正需要 agent loop 多步推理的**——research 是長任務,必須 subagent 隔離(研究一支股票吃 50k+ tokens 的 filings/新聞,不能污染主對話)。
- **③ 的形狀是 watcher daemon + 觸發後對話**——事件驅動,跟「用戶開 session」的互動形狀根本不同;這是終局必須自有 loop 的第一個硬理由。

## 9. 藍圖

```
                 ┌────────────────────────────────┐
                 │  Orchestrator(唯一對話面)       │ ← 自有 loop(可以是 Agent SDK 的)
                 └────────────────────────────────┘
        路由到五個模式;skills = 各模式的薄行為契約,漸進載入
     │① research  │② sizing    │③ exit      │④ review    │⑤ analytics
     ▼            ▼            ▼            ▼            ▼
 [research    [sizing      [watcher     [現有        [analytics
  subagent]    engine]      daemon]      engine]      engine]
     └────────────┴────────────┴────────────┴────────────┘
                          │
              ┌───────────────────────┐
              │ Shared State(脊椎)    │ positions / trades / theses /
              │ schema 由 code 管      │ rules / **decisions log**
              └───────────────────────┘
                          │
              ┌───────────────────────┐
              │ Policy layer(code)   │ 絕不自動下單 / 數字必來自 engine /
              │                       │ 建議必附 bear case / 不外傳
              └───────────────────────┘
```

**分工的最終答案(skills vs agent loop)**:
- **Loop 自有**(事件驅動 + 背景研究撐不進 session 形狀),但用 Agent SDK 的 loop,不從零寫。
- **Skills 不被淘汰,但降級**:從「執行單位」變成 **prompt 資產的組織單位**——五模式五份薄行為契約,平時只佔 name+description,進模式才載入(Claude Code 的漸進披露機制照搬)。執行保證全部上移到 code(policy/engine/state)。
- **LLM 的職責收斂成三件事**:翻譯(人話↔參數)、合成(research)、對話(動機、決策呈現)。計算、監控、記憶、護欄,全部 code。

## 10. 五項的具體形態(與現有資產的接點)

**① 買啥 → 做成「thesis builder」,不是「screener」。** 用戶帶 idea 進來(自己的想法、KOL 訊號——接 kol_collector),research subagent 跑結構化研究:bull/bear case、可證偽條件、driver 歸類(接現有 driver map)、與現有持倉的相關性檢查(「這是不是同一注」= B2 已有)。**輸出不是『買』,是一筆五要素 thesis 草稿——格式就是現有 theses.jsonl 的 entry(#136)。** 不生成「今天該買什麼」的 universe 掃描,只深化你自己帶進來的 idea:這同時是紅線內化(教練≠顧問)與產品差異化(市面 screener 一堆,深化+反面+同注檢查沒有)。

**② 多少買 → 規矩系統的前向應用。** 輸入:conviction(對話收)、風險預算與規矩(profile/log 已有)、既有 exposure(ledger 已有)、波動率(市場資料)。輸出:size 區間 + 理由 + **違反哪條你自己的規矩**(「你的規矩說最大單注 20%,這筆買滿會到 27%」)。pre-trade gate 就是這個模組的 UI——把復盤教練變成事前教練,fomo-kernel 哲學(拿你自己的話照你)零損耗前移。

**③ 賣嗎 → watcher daemon(計算)+ exit 對話(判斷)。** theses.jsonl 已有 exit_trigger/stop/review_trigger;現在是每週對帳人肉查三類,終局是 daemon 盤後掃:價格類 trigger 直接比,事實類 trigger(「營收失速」)派 subagent 查證。燒到 → 推播 → exit 對話:「你當初說的失效條件 vs 現在的事實」,用戶決策,落 decisions log。**絕不自動賣**(policy 第一條,是硬體結構不是 prompt)。

**④ 好不好、改決策 → 現有 engine + 一個閉環升級:決策審計。** 現在的復盤只看得到**交易(結果)**,看不到**決策(當時的輸入)**。①②③ 一旦落盤決策,④ 就第一次能做「決策品質 vs 結果品質」分離(好決策壞結果 ≠ 壞決策):當初 sizing 對嗎、research 的 bear case 料中了嗎、賣的理由 30/60/90 之後對帳(revisit 已有,擴到全決策鏈)。**這是五項合一的真正紅利——不是功能加總,是 ④ 的質變。**

**⑤ 績效/分布/風報比 → 純 engine 擴張。** 已有:α/β、payoff、集中度、pp 拆帳。要加:時間加權報酬、drawdown、Sharpe/Sortino、exposure 矩陣(driver × market × currency,driver map 已有)、**per-thesis R multiple**(進場時 stop/target 定義了 1R,實際走出幾 R)——注意 R 的可算性依賴 ②③ 的資料紀律,這是五項互相咬合的典型例子。

## 11. Harness 缺口重算(這個假想下,按建造依賴排序)

**直接繼承(零或低成本)**:engine 確定性核心(④⑤ 的一半)、五要素 thesis schema(① 的輸出格式、③ 的輸入)、規矩系統(② 的約束源)、SKILL.md 行為契約(可攜)、本機隱私架構。

**缺的,按依賴序**:

1. **Decisions log(決策事件流)——第一塊,且今天在 skill 形態就能開始補。** 現有 theses.jsonl + exit_narrative 已是雛形;缺:沒買的決策(研究過放棄的)、sizing 當時的理由、決策時的 context snapshot。沒有它,④ 永遠只能復盤結果。它是五項的共同脊椎。
2. **NAV/市值序列 daemon。** SKILL.md 自己註明「帳戶 vs 大盤數字級對比給不了(需要每週市值序列)」——⑤ 的 drawdown/風報比全部需要 equity curve,而 equity curve 需要**持續的 mark-to-market 快照**(每日收盤存一行 NAV)。這是 daemon 的第二個硬理由(第一個是 ③ 的 trigger 掃描),兩者可以是同一支程式。
3. **Policy engine——做 ①②③ 之前必須先有**(它們是高風險模組):不下單、數字必來自 engine(render assert)、建議必含 bear_case 欄位(schema 驗證,缺=不出)、同注檢查必跑。形狀:PreToolUse hook + 輸出 schema 驗證 + render diff。
4. **推播通道**(③ 的觸發後半):本機通知 / 手機,與隱私鐵律的相容性待選型。
5. **市場資料層升級**:yfinance 夠復盤;①③ 需要財報日曆、事件流、基本面——**成本中心**(數據授權費),也是「free skill → 付費產品」的天然分界線。
6. **Research harness**(① 專用):subagent 定義 + 來源工具 + 反面強制。
7. **Eval harness**:① 的研究質量、③ 的 trigger 誤報率——**敢自動化的前提**。誤報率高的 watcher 比沒有 watcher 更糟(狼來了→通知全關)。
8. **合規/免責層**:①②③ 上線 = 從「復盤工具」變「決策支援」;自己用 vs 分發給別人的法律定位差異巨大(prd-investment-os.md 的雙前端開關在這裡從產品設計變成合規結構)。

## 12. 建造順序:逆著決策鏈走

直覺順序是 ①→⑤(從買啥開始),**正確順序幾乎相反:⑤ → ② → ③ → ④閉環 → ①**:

- **⑤ 最便宜**(純 engine 擴張,今天可做),且立刻提升現有卡的可信度。
- **② 是差異化最強的**:市面 stock picker 一堆,「拿你自己的規矩在你下單前照你」沒有;而且它只是規矩系統前移,幾乎零新 harness。
- **③ 帶入第一塊新 harness(daemon)**,但資料(trigger)已經在收。
- **④ 的決策審計隨 decisions log 自然出現。**
- **① 最後**:最貴(數據+eval+合規)、紅線最險、市場最擁擠。

深層理由:**① 是唯一「無中生有」的判斷,②③④⑤ 全是「拿你自己的話對你」**(你的規矩、你的 thesis、你的 trigger、你的決策)。fomo-kernel 的靈魂(鏡子不是審判、教練不是顧問)在 ②-⑤ 零損耗保留,只在 ① 真正踩線——所以 ① 做成 thesis builder 而非 screener,把紅線內化成產品形狀,而不是靠 prompt 守。

## 13. 一句話收斂

終局架構 = **一個自有 loop 的 orchestrator + 五份薄 skill(行為契約)+ 一個持續長大的確定性 engine 家族 + 一條 code 化的 policy 層 + 一根 decisions log 脊椎**。缺的 harness 不是「更聰明的 agent」,是 agent 周圍的機械:decisions log(記憶脊椎)、daemon(事件+NAV 的半邊)、policy engine(絕不清單)、市場資料層(成本中心)、eval(敢自動化的前提)。而其中兩塊(decisions log、⑤ 的 engine 擴張)今天在 skill 形態就能動工,做了就直接變成終局的地基。

---

# Part 3 · 用 investment_note 校準:需求的 ground truth

> 2026-07-07 第三輪:owner 要求「基於 investment_note 的設計,整體探索方向和需求」。investment_note(owner 私有 repo,獨立系統)是**已經在跑的全生命週期投資系統**——它的每個組件是一次「真的需要」的證據,每條 protocol 是一次事故換來的 harness 需求,每個變重的角落是一次「這形態不行」的反面教訓。本節只引其**系統設計與方法論**,不含任何持倉/交易內容。

## 14. 第一個發現:終局不是假想,activo 原型已經在跑

investment_note 現況 = **24 個能力單元掛在 Claude Code 上**(13 個自家 skill + equity-research 8 命令 + financial-analysis 3 命令),加 4 條 protocol、7+ 個確定性工具、evals/ 雛形、以及固定 cadence(daily 5min mobile / after-trade / around-earnings / weekly 30min / monthly 1hr / quarterly 2hr)。對照 Part 2 的五項:

| Part 2 的五項 | investment_note 現有實作 | 狀態判讀 |
|---|---|---|
| ① 買啥 | `/screen`、`/initiate`(全套 initiation:research/model/valuation/report)、`/thesis`、`/sector`、`/13f`、`/verify-kol-claim`、BACKLOG 72hr 冷卻、三 Gate | **在跑,工具最多**;痛點是驗證鏈路被持倉 narrative 污染(見 §16) |
| ② 多少買 | RULES.md 按市值分級的部位上限(量化)+ Pre-Trade Checklist + cooling periods(新標的 72hr/加碼虧損 1 週/獲利再進 2 週) | **規格全有、量化完畢,缺 enforcement**——全靠自律照 checklist |
| ③ 賣嗎 | Rule #5(單一持倉 -20% → 強制書面復盤)、Rule #6(主題 ETF 連 3 月跑輸 SPY >10% → 強制 review,對照表已定)、`/weekly-watch`(falsification trigger)、catalyst 日曆 | **watcher 的規則引擎規格已經寫好,缺 daemon**——現在靠每週人肉跑 |
| ④ 好不好 | `/record-trade`(update/decision/revisit 三模式)、`/review-mistakes`、`/ai-scorecard`、mistakes.md(數百筆交易的人類錯誤庫,與 ai-errors.md 嚴格分開) | **最成熟**,fomo-kernel 已在吸收(prd-investment-os.md 第 4 節) |
| ⑤ 績效/風報 | portfolio.md health check、`mark_portfolio.py`(唯一寫 derived 欄位者) | **最薄**——fomo-kernel 的 α/β/payoff 反而更強,回流方向成立 |

**推論:五項的「需求存在嗎」不用再驗證——全部有活實作。剩下的只有形態遷移問題。**

## 15. 紅線已經演化:「絕不建議」→「結構化雙面」(2026-07-04)

Part 2 說「① 做成 thesis builder 不是 screener」——**要修正**。investment_note 2026-07-04 已把「Never give buy/sell advice」正式改成「**AI 可給方向性建議,但決策型輸出必須結構化雙面**」:(1) bull+bear 同口徑、bear 須引 base rate/歷史反證、篇幅相當、禁稻草人 (2) falsifiers 量化可證偽 (3) options ≥2(sizing 只能寫成「路徑+各自代價」,不可單向指令)(4) 口徑一致 (5) 可表態,但**傾向擺在雙面之後** (6) **RULES #3 凌駕:thesis broken → 只能減碼/出場,禁任何加碼傾向**。

這改寫終局 ① 的設計:紅線不是「不給答案」,是「**答案的結構被強制**」——這比 prompt 鐵律高一級,因為它是 **schema 可驗證的**(缺 bear case / 缺 falsifier / options<2 → policy 層直接擋下不出)。「結構化雙面」正是 policy engine 最該 enforce 的輸出契約,也是「自己用開選股」與「分發版關選股」之間的第三條路:**分發版的 ① 可以開,但輸出契約鎖死在結構化雙面 + 過程支援**。

## 16. 最值錢的資產:事故史 = harness 需求的實證清單

investment_note 的每條 protocol 都「源自 #NN 事故」(ai-errors.md 編號),直接翻譯成終局 harness 需求——**這份清單不是設計出來的,是失血換來的**:

| 事故(實證) | protocol(prompt 層防線) | 終局 harness 需求(code 層) |
|---|---|---|
| 憑記憶斷言 ticker 事實,#027 family **復發計數持續增加** | **Provenance 不變量**(default-deny:記憶不是出處,事實必須來自 repo 或本回合 search) | **policy 層第一公民**——比 Part 2 的「數字必來自 engine」更廣:所有事實斷言的出處驗證,做成輸出 gate 而非提醒 |
| 下市股 stale cache 當 live → 全表 audit **24% error rate** | Stock Data Hygiene(listing status/corporate action/baseline trading day/cross-source/reverse-compute) | **市場資料層不是「接個 API」,是 hygiene pipeline**——終局最被低估的重活;fomo-kernel 的 yfinance 直取在 ①③⑤ 的精度要求下不夠 |
| 並行寫入靜默改寫 portfolio 真相(#023,毀滅性) | Write Lanes(state/canonical/generated/governance)+ 單一寫入者鐵律 | **shared state 的 lane 治理**:哪個模組能寫哪段狀態,code enforce(fomo-kernel 的 append-only jsonl + engine 單一寫入路徑是同一思想的正確起點) |
| 時序幻覺幾乎污染交易(#001) | Fact Check(日期絕對化/來源 URL/時間軸定位/狀態標記) | 研究輸出的 **schema 驗證**(每個事件必附 YYYY-MM-DD + URL,缺 = 不出) |
| ≥3 次「在持倉 narrative 下驗錯被打臉」 | (SUBAGENT_PROPOSAL 的解法,見 §17) | **認知隔離**:position-blind 驗證鏈路 |

還有一條 meta 級發現:investment_note 自己已寫下「**觸發詞一律降級為示例,真正閘門 = 不變量**」+「**同 root cause 失效記為復發計數,計數器 = 該動結構、禁止再加觸發詞的訊號**」——這與 Part 1 §5 的「鐵律增速 = 搬家訊號」是同一個發現的兩個獨立實證。**owner 已經有 prompt 防線極限的一手數據。**

## 17. SUBAGENT_PROPOSAL:owner 自己的架構判準,直接繼承進藍圖

investment_note 的 SUBAGENT_PROPOSAL.md(經 Codex+Gemini 雙審到 v2)給了四個可直接繼承的裁決:

1. **subagent 的價值點只有 4 個**:隔離 context、工具硬限制、角色一致性跨 session、並行多視角——不滿足任一,維持 skill。這比 Part 2 的「research 才 subagent」更精確:**subagent 化的理由是認知結構,不是任務大小**。
2. **position-blind 硬規定**:claim-verifier 不讀 portfolio、不讀 ticker wiki,只收單句 claim——「一旦讀了持倉就不是乾淨 context,等於自欺」。**這是 Part 2 藍圖漏掉的關鍵架構需求**:多 agent 的第一個理由不是 context 大小,是**驗證者與持倉者的認知隔離**(對應行為金融的 confirmation bias,有 ≥3 次實證)。終局藍圖修正:③ 的「事實類 trigger 查證」與 ① 的 bear case 研究,都必須走 position-blind 通道。
3. **claim-type 分級證據門檻**:raw-fact(1 個官方一手)/ derived-metric(公式可重算)/ market-claim(一手+高品質二手交叉)/ **causal-claim(最高只能 supported,永不 confirmed)**——研究輸出的置信度分級,直接是 ① research harness 的輸出 schema。
4. **eval-gated 部署 + 校準前禁 action**:先用歷史錯誤案例回測、量化 false-confirm/false-refute rate,達標才放行;未校準前 thesis-adversary 禁止輸出 hold/review/cut,只給 broken_pillars。**「敢自動化的前提是 eval」在 owner 方法論裡已經成文**——Part 2 §11 第 7 項有了本地實證與現成做法(evals/golden-cases.md 起步中)。

## 18. 形態的反面教訓(terminal 訊號,別重蹈)

- **重系統自診**:11 欄決策 narrative、portfolio.md 治理、weekly-review 長文 journal——owner 自己診斷「用分析取代行動」,prd-investment-os.md 已判砍。終局 agent 的 form budget(一張卡)是對這個教訓的制度化,不是美學偏好。
- **24 個能力單元的觸發路由已經出現實測痛點**:momentum 三合一提案被 Gemini 以「觸發誤判」反對(裁決:入口不合併、後端抽共用 lib)。**skills 平鋪的路由問題有實證**——Part 2 藍圖的 orchestrator/dispatcher 不是過度設計,是已發生的需求。
- **protocol 層 ~180 行全靠 prompt 遵循 + hook 提醒**:UserPromptSubmit hook 注入提醒是「唯一能在純對話端生效的防線」——owner 已經摸到 prompt 防線的天花板,policy code 化是自然下一步。

## 19. 兩 repo 關係的重定位:雙前端已經是現在式

prd-investment-os.md 把「雙前端」寫成未來設計——**實際上它已經以兩個 repo 的形式存在**:

```
investment_note  = 「自己用」前端的現在式:全生命週期、外部資料、自己的 RULES 當尺、重、私有
fomo-kernel      = 「別人用」前端的現在式:recap only、純本機、可換鏡片、輕、公開、有確定性 engine
缺的一塊         = 共用核心:兩邊的確定性工具是平行的兩套
                   (investment_note: mark_portfolio/scanner/audit ↔ fomo-kernel: trade_recap/ledger/revisit)
```

終局 agent 的公式:**investment_note 的功能(需求已全部實證)× fomo-kernel 的形態紀律(三道閘、一張卡、engine 化、隱私鐵律)× 一套共用 harness(policy/state lanes/daemon/eval)**。

## 20. 校準後的方向與需求(整合三個 Part)

**Harness 缺口重排**(依 investment_note 實證的痛度,取代 Part 2 §11 的依賴序):

1. **Provenance/輸出 gate(policy engine 第一條)**——不是「不下單」(題目未至),是「記憶不是出處」(#027 family 復發計數還在漲,天天失血)。含:結構化雙面 schema 驗證、事實必附出處、causal 永不 confirmed。
2. **資料衛生 pipeline**——24% error rate 的教訓;①③⑤ 的精度全部依賴它;成本中心與護城河同體。
3. **Enforcement 缺口(pre-trade gate)**——② 的規格量化完畢(分級上限/三 Gate/cooling periods)但全靠自律;把 checklist 變閘門是所有缺口裡**規格最完備、離可做最近**的。
4. **Watcher daemon**——③ 的規則(-20% 強制復盤/ETF 對照輪動偵測/catalyst 日曆)已成文,缺執行體;與 NAV 序列(Part 2 §11-2)同一支 daemon。
5. **認知隔離架構(position-blind subagents)**——claim-verifier 已設計完(v2),實施順序第一;≥3 次實證痛點。
6. **State lanes 治理**——#023 級風險;fomo-kernel 的 append-only + 單一寫入路徑起點正確,擴到多模組時 lane 表 code 化。
7. **Eval harness**——golden-cases 起步 + 回測 gate 方法論已成文;每個要自動化的模組先過它。
8. **Mobile 介面**——workflow cadence 的 daily 5min 是 mobile(claude.ai/code 實際在用):終局 UI 需求不是猜的,晨間場景已存在。

**建造順序修正**(Part 2 §12 的 ⑤→②→③→④→① 之上,加雙前端分岔):
- 「自己用」前端:① 不用等——它已經在跑(screen/initiate/13f),該做的是給它套上 §15 的輸出契約 + §17 的 position-blind 驗證鏈。
- 「分發」前端:順序不變(⑤→②→③→④),① 最後且鎖結構化雙面;fomo-kernel 現在的 recap(④)繼續當灘頭堡。
- 兩邊共同的第一步不變:**decisions log + engine 下沉**——investment_note 的 `/record-trade decision`(11 欄,太重)與 fomo-kernel 的 theses.jsonl(五要素,較薄)本來就是同一個東西的兩個形態,收斂它們 = 共用核心的第一塊磚。

## 21. 開放問題(接續 §7,新增)

5. 共用核心的物理形態:fomo-kernel engine 抽成獨立 package 給兩 repo 用?還是 investment_note 逐步改 import fomo-kernel?(牽動公開/私有邊界)→ **Part 4 回答:不做共用 package,直接單一產品替代**
6. 結構化雙面的 schema 落在哪:fomo-kernel engine(公開、可分發)還是 investment_note tools(私有)?它是分發版 ① 的前提。→ **Part 4 回答:落 fomo-kernel(機制屬產品)**
7. mistakes.md(人類錯誤庫)與 fomo-kernel 的規矩/教訓迴圈是否同一資料模型?(畢業機制 #137 已有 STRATEGIES→RULES 的 6 個月/3 次觸發原型可借)
8. daily morning note(generated lane、disposable)是否屬於終局 agent?它是黏著度最高的 cadence,但也最接近「資訊消費」而非「決策支援」。

---

# Part 4 · 融合路線:單一產品替代,不是雙前端並存

> 2026-07-07 第四輪,owner 方向拍板:「investment_note 是我自己長出來的東西,希望用一個對外產品來替代——build for public 但我自己用得很開心。」這修正 prd-investment-os.md 的「雙前端並存」表述。本節評估可行性、給融合的正確形狀與風險。

## 22. 直接回答:可以融合,而且已經在發生——但「直接」的形狀是三分法,不是合併 repo

**第一個證據:record-trade 的替代已經進行中。** Phase B(ledger/thesis/revisit/賣出 capture)就是把 `/record-trade` 的功能吸收進公開產品,驗收標準已定:「owner 連續兩週不開 /record-trade」。**「用公開產品替代自用系統」不是待驗證的假設,是有第一個案例、有可測量驗收的既成路徑**——問題只剩「能走多遠、按什麼順序」。

把 investment_note 的組件按融合性質分三類:

**A 類 · 機制直接進產品(功能已驗證、形態可收斂、無隱私/紅線障礙)**
- record-trade 全家 → Phase B 進行中
- RULES/STRATEGIES 規矩系統 → 產品的規矩 + 畢業機制(#137 直接借 STRATEGIES→RULES 的「≥6 個月驗證 + ≥3 次觸發都對」原型);**個人參數(分級部位上限、cooling 時長)= profile 設定,產品給機制、用戶填自己的尺**
- Pre-Trade Checklist / 三 Gate / cooling periods → pre-trade gate 模組(②)
- watcher 規則(-20% 強制復盤、ETF 對照輪動偵測)→ daemon(③),對照表做成預設可改
- Thesis Quality Checklist 五維 → 五要素 thesis 的品質檢查
- mistakes 錯誤庫的資料模型 → 教訓迴圈(revisit falsified → 教訓段)
- claim-verifier / thesis-adversary(position-blind)→ 產品的驗證鏈
- protocols(Provenance / Fact Check / Data Hygiene)→ **升維,不是搬運**:180 行 prose 變 policy 層 code,這是它們的正確歸宿(§16)

**B 類 · 容器公開、內容永遠私有**
- wiki/{TICKER} 的 thesis/falsification → 產品的 thesis 庫(theses.jsonl 富化);機制公開,你的內容留 `~/.trade-coach/`
- portfolio → ledger 已承接
- 個人投資觀 / RULES 參數 → profile
- 歷史資產(數百筆交易、錯誤庫、wiki 存量)→ **一次性 import 工程,別低估**

**C 類 · 不融合(裝進去會把產品搞死)**
- equity-research / financial-analysis plugins(initiate/dcf/comps/sector):SUBAGENT_PROPOSAL 自己判過——「plugin 全是 sell-side 機構流程,自家 skill 是 buy-side 散戶紀律,重疊度低」。它們是通用研究工具,不是產品差異化;**且不需要融合——終局 agent 跑在同一 harness 上(Claude Code / Agent SDK),plugin 生態天然共存,你照裝照用**
- morning-note 等資訊消費類:黏著高但離「決策支援」最遠(§21-8),可選模組或不做

## 23. 關鍵反轉:從「減法雙前端」到「加法單一產品」

prd-investment-os.md 的公式是**減法**:`別人用 = 自己用 − 選股/找資訊 − 外部連接`——從重往輕閹割。單一產品把它反轉成**加法**:

```
產品核心(所有人一樣)   = 卡、對帳、規矩、thesis 迴圈(現在的 fomo-kernel)
+ opt-in 模組(逐個解鎖) = pre-trade gate、watcher、研究驗證(position-blind)、績效深化
owner                    = 全模組開啟的第一個 power user
```

**加法才守得住形態**:每個模組進產品要過三道閘(改下一筆 / form budget / 盡量自動),而閹割永遠閹不乾淨。這也治 investment_note 的病——「用分析取代行動」的重形態,遷移到 form budget 由產品定義強制執行的殼上,你自己也被它保護。

紅線不再需要功能開關:§15 的「結構化雙面」讓選股支援可以進公開產品——不是「自己用開、別人用關」,是**輸出契約鎖死**(缺 bear case / 缺量化 falsifier / options<2 → policy 層不放行)。

## 24. 遷移方法論:逐模組,驗收 =「你不再回去用」

把 record-trade 的驗收判準推廣成整個融合的方法論:**每個模組融合完成的定義 = owner 在 investment_note 不再使用該功能**(連續兩週為觀察窗)。逐模組、可測量、不搞大爆炸遷移。順序沿 §20:

1. Phase B 收尾(record-trade 替代,驗收中)
2. ~~規矩系統參數化 + 畢業機制(#137)~~ → **已完成(2026-07-07 PR #146,main `347c14c`):問題帳三層架構 supersede 畢業設計**——rules.jsonl 多條規矩綁 problem_key、revises 演變線、broke/held/skipped 對位、held_streak≥2 靜默調度;個人規則庫已落地,且 pre-trade gate 查全集的前置已就緒(roadmap v1b)
3. pre-trade gate(規格最完備、你自己最缺 enforcement、產品差異化最強;**#146 後前置已備,離可做更近**)
4. watcher daemon(規則已成文)
5. position-blind 驗證鏈(claim-verifier v2 設計已完)
6. thesis 庫富化 + 歷史 import

investment_note 的終態:**archive(歷史檔案,唯讀)+ C 類長尾(plugin 研究工具照用)**——不是刪除,是退役。

## 25. 誠實的風險清單(這條路的真實代價)

1. **雙用戶撕裂**(此模式的著名死法):為你加的深度嚇跑陌生用戶,為陌生用戶做的簡化讓你回頭用 investment_note、dogfood 斷。§23 的加法分層是解法,但每次「為自己加模組」都要問:它是 opt-in 還是改變了核心體驗?
2. **隱私鐵律要重新表述**:你自己用需要外部呼叫(行情、web 驗證、13F),「純本機零外部」會被 opt-in 模組打破。新表述:**交易資料永遠本機不外傳;opt-in 模組的外部呼叫只查公開市場資料,不攜帶你的持倉**(position-blind 剛好同時是認知需求與隱私需求)。market_context.py 已有先例(yfinance 查公開行情 ≠ 交易資料外傳)。
3. **import 工程的包袱**:數百筆交易、wiki 存量、錯誤庫——一次性但不小;做不好,你的「用得開心」從第一天就折損(教練失憶)。
4. **dogfood 偏誤是雙向的**:你用得開心 ≠ 別人用得開心(你有 24 個工具的肌肉記憶與完整 context);反饋管道(#42)與 Stage 0 的真人驗證要持續,別讓「自己爽」遮住「卡不夠好用」。
5. **節奏風險**:融合是多季工程,期間兩系統並存、狀態雙寫的窗口最容易出 #023 型事故——每個模組切換時明確「單一寫入者換邊」的時點,寫進遷移 checklist。

## 26-a. 形態答案:吸收功能,不吸收形態——「一堆 skill」是沉積,不是設計

> owner 追問:「investment 本質是一堆 skill + 流程 + 規範,和 fomo-kernel 差異較大——抽過來時,產品或落地形態該變嗎?」

**答案:對外形態不變(單入口 + 一張卡),內部結構演化。** 理由:

1. **investment_note 的「13 skill + 4 protocol + cadence」不是被設計成這樣,是沒有 form budget 的自然沉積**——每個需求加一個 skill、每次事故加一條 protocol、每個節奏寫一段 cadence 靠自律。owner 自診「用分析取代行動」+ 觸發路由誤判(§18)就是這個形態的代價。issue #12 的第一原則「吸收功能,不吸收形態」正是為這一刻定的。
2. **兩者形態差異的本質是「單位」不同**:investment_note 的單位是**工具**(skill = 一個動作,用戶自己編排流程)= 工具箱;fomo-kernel 的單位是**迴圈**(狀態讀→診斷→問→卡→承諾→下週對帳,產品編排流程)= 教練。**抽功能的正確動作是把「工具」熔成「迴圈裡的站點」,不是把工具箱搬家**:
   - watcher 規則 ≠ 新增 `/check-rotation` 指令 → = 對帳開場的一段(「你的持倉觸發了輪動檢視線」)+ 未來 daemon 主動通知
   - pre-trade checklist ≠ 用戶自己記得跑的 `/pre-trade-check`(現在 RULES checklist 靠自律 = 失效中)→ = 「買之前來說一聲」的教練入口,gate 過程對話完成
   - claim-verifier ≠ 用戶指令 → = 迴圈內部零件(subagent),被 gate/對帳在需要時呼叫
3. **對外入口收斂規則**:主入口一個(`/fomo-kernel` 週迴圈);pre-trade gate 是唯一值得考慮的第二入口(觸發時機在「下單前」,天生不在復盤 session 裡,§7-3);daemon 通知是第三種觸達但非指令。內部模式/模組可長(SKILL.md 拆子檔案漸進載入,card-spec.md 已是先例),**對外面永遠收斂**——issue #12「內部層可長,外部面收斂」從輸出推廣到入口。
4. **規範(protocols)升維不搬運**(§16):可機械判定的進 engine/policy code,SKILL.md 只留少數需要 LLM 遵循的鐵律——不會出現「fomo-kernel 版的 180 行 protocol 章」。
5. **形態真正變的兩個時點**(即 Part 1 的 Stage 2/3):daemon 半邊加進來(中期,加背景執行體,對話面不變)與 SDK 換殼(遠期,商業決策)。在那之前,任何「要不要多開一個 skill/入口」的衝動,先過三問:**它進哪個迴圈?輸出上不上卡?真的需要新入口嗎(預設 no)?**

## 26. 收斂:三個 Part 的答案疊起來

- Part 1:**engine 是產品,harness 是殼**——skill 殼迭代最快,現在不用換。
- Part 2:終局 = orchestrator + 薄 skills + engine 家族 + policy 層 + decisions log 脊椎;缺的是 agent 周圍的機械。
- Part 3:investment_note 證明需求全部真實,並貢獻事故換來的 harness 清單與架構判準。
- **Part 4:融合方向 = 單一公開產品,加法分層,逐模組替代,「你不再回去用」為驗收**——fomo-kernel 不是 investment_note 的閹割版出口,是它的**下一個形態**;investment_note 是需求探礦場,採完的礦進產品,採不完的(sell-side plugin 生態)共存不融。

---

# Part 5 · 從零抽象:investment_note 作為一個產品的 pseudo code,與 skill 任務明確性檢驗

> 2026-07-07 第五輪,owner:「跳脫 fomo-kernel 框架,把 investment_note 抽象成一個產品會怎麼做?給簡單 pseudo code,對照 best practice——我預期一個 skill 給這麼多任務會違反任務明確原則,確認我的理解。」

## 27. Pseudo code:不從 skill 出發,從「它什麼時候必須存在」出發

```python
# ================= 抽象:一個投資流程 OS =================

state = {
    "portfolio": ...,     # 持倉真相(每段唯一寫入者)
    "theses":    [...],   # 每檔:why / falsifiers / horizon / size 理由
    "rules":     [...],   # 你的紀律:caps / cooling / gates(量化、可判定)
    "journal":   [...],   # decisions + mistakes,append-only
}

# ---------- 觸發面:三種,缺一不可 ----------

on user_intent(intent):              # 「我想買 X」「幫我復盤」「查證這個說法」
    match intent:
        case pre_trade(action):
            violations = check(action, state.rules)        # 確定性
            dialogue   = two_sided(action, state.theses)   # LLM:結構化雙面
            state.journal += decision(action, 用戶答案)
        case review():
            reconcile(state.journal.last_commitment)       # 對帳先行
            card = converge(diagnose(state), ask_motive()) # 收斂一張卡
        case research(idea):
            draft = blind_verify(idea)                     # position-blind 子代理
            state.theses += draft                          # 輸出=thesis 草稿,非「買」

on schedule(cadence):                # daily / weekly / monthly / quarterly
    due = scan_revisits(state) + scan_rules(state)         # 全部確定性
    if due: notify(due)                                    # 只召喚,不出卡

on market_event(tick):               # 盤後 daemon
    for t in state.theses:
        if burning(t.falsifiers, tick): notify(t)          # -20% / 輪動 / catalyst

# ---------- policy 面:跨全部路徑的硬約束(code,非 prompt)----------
policies = [
    provenance,            # 任何事實斷言必有出處(state 或本次檢索)
    no_execution,          # 絕不下單
    structured_two_sided,  # 決策型輸出缺 bear / falsifier 不量化 / options<2 → 不放行
    single_writer,         # state 每段唯一寫入者
    form_budget,           # 輸出永遠一張卡 / 一則通知
]
```

這個抽象揭示 investment_note 的本質 = **三種觸發 × 一份 state × 一層 policy**。對照現況:24 個 skill 只是 `user_intent` 的 match arms 被攤平成頂層指令;`on schedule` 沒有執行體(cadence 寫在 CLAUDE.md 靠自律);`on market_event` 完全缺席;policies 用 ~180 行 prompt 寫。**它不是「一堆 skill 的系統」,是「只有一種觸發面可用的系統」——形態是被 Claude Code 當年只有 user_intent 觸發這個限制塑形的,不是需求長這樣。**

## 28. Best practice 檢驗:「一個 skill 這麼多任務」違反任務明確嗎?

**owner 的直覺對一半——問題是真的,但切割維度要修正。**

官方 Agent Skills 的判準不是「一個 skill 只能一個 task」,而是三條:
1. **觸發明確**:用戶意圖 → skill 的映射無歧義(靠 description)。
2. **載入明確**:漸進披露——Level 1(name+description,~100 tokens,常駐)/ Level 2(SKILL.md body,**<5k tokens**,觸發才載)/ Level 3(子檔案資源,按需才載)。官方 docx/pptx skill 就是「一個 domain、多個 task、子檔案按需」的參照實作。
3. **執行契約明確**:每個任務有可測試的輸入/輸出。

用這三條照兩個系統,結論反直覺:

- **skill 顆粒度上,「多」才是病**:investment_note 13 個平鋪 skill 在第 1 條失分——momentum 三入口路由誤判是實測(§18),SUBAGENT_PROPOSAL 的 Gemini 裁決已確認「入口不合併」是為了保觸發明確,但那是在平鋪前提下的局部最優。一個 domain 一個入口(`/fomo-kernel`)在第 1 條反而是滿分:「復盤交易→這裡」無歧義。
- **fomo-kernel 的真病在第 2 條**:SKILL.md 399 行 ~25k tokens **全量載入**——對帳模式也載著試駕、幣別、初診、收尾腳本的全文,超官方建議 5 倍。owner 感覺到的「任務明確被違反」,病根不是「任務多」,是「**所有任務的 prose 同時在場**」(遵循度隨長度衰減,Part 1 §1 的稅)。
- **另一半病根:兩種非對話觸發被硬塞進對話形態**——cadence 與 market_event 本來就不屬於 skill 的職責(§27 的觸發面分離),寫進 SKILL.md 靠 LLM 記得「開場先 scan」才真正違反任務明確。

**修正後的理解**:
```
錯誤解法:拆成多個 skill                → investment_note 的老路(觸發歧義)
正確解法:skill 層 = domain(一個入口)
         mode 層  = task(SKILL.md 瘦成 dispatcher <2k:路由+鐵律;
                    每 mode 一份子檔案 gate.md / review.md / research.md,
                    觸發才載——card-spec.md 已是先例)
         cadence / market_event = 不進 skill,進 harness 觸發面(scheduled / daemon)
```

即:**任務明確原則成立,但它的落點是 mode 層與觸發面,不是 skill 數量**。這與 Part 2 §9(dispatcher + 五份薄契約)、§26-a(單位是迴圈不是工具)互為印證——三條路徑推到同一個形狀。

## 29. 狀態更正(並行機制,2026-07-07)

- **#137 已 CLOSED,設計 superseded**:PR #146(main `347c14c`)落地「問題帳三層架構」——統計層(`build_problem_events` + `engine/problems.py`)/ 規矩層(`rules.jsonl` 多條綁 problem_key、revises 演變線、broke/held/skipped 對位)/ 呈現層(卡面問題帳 top 1-3);held_streak≥2 靜默調度**取代**畢業概念。§20 曾建議「借 STRATEGIES→RULES 原型」——已被更好的設計 supersede,勿複讀。
- 個人規則庫(Part 3 §14 的 ② 約束源)因此已落地;**pre-trade gate 查全集的前置已就緒**(#146 comment、roadmap v1b)——§24 順序裡的第 3 項離可做更近。
