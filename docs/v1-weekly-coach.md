# v1 藍圖:每週復盤迴圈(初次 × 持續 review × 持續優化)+ 薄本機狀態

> 🗄️ **設計史快照(2026-06-17,非當前規格)**:本文是改名前的 v1 每週迴圈設計藍圖。**部分已被 main 實作超越**——尤其 §2 的 `~/.trade-coach/profile.json` 狀態格式、與 §6「engine 尚無結構化輸出、需建 JSON contract」的前提,當前已不同(SKILL.md 用 `log.jsonl`/`theses.jsonl`、engine 已有 `TR_JSON`/`TR_STATE_OUT` 結構化輸出)。當前規格一律以 `skills/fomo-kernel/SKILL.md` 與 `engine/trade_recap.py` 為準;本文保留作設計脈絡與決策史。

> 狀態:設計中(v1)。把 `BACKLOG.md` 願景層 6 步弧線中的 `初診(卡) → 賽後對帳(驗規矩) → 升級畢業` 三步,落成可實作的最小規格。
> 目的:讓 `/fomo-kernel` 從「一次性 demo」變成「每週重跑、記得上次那條規矩」的迴圈,且**絕不長回成重系統**。
> 北極星:一張卡、一個洞;第二張卡的價值在**進度**(規矩守了沒、洞有沒有縮),不在再算一次。
> 範圍外(本 v1 不做):pre-trade gate、多鏡片對照/誠實閥(v2c,需先建【風格】維)、thesis 對帳(v3)、revenge/overtrade 標籤(engine 線)。見 §8。

---

## 0. 先釘死:跟既有兩個系統的關係(免混淆)

「trade review」在 owner 的環境裡是**兩個容易混淆的系統**,本 v1 只動 B:

| | **A · `/record-trade`** | **B · `/fomo-kernel`(本 repo)** |
|---|---|---|
| 在哪 | `investment_note/`(owner 私人系統) | 本 repo(對外可分發產品) |
| 角色 | 記帳 + 記決策 + revisit(管**真相**) | 出一張復盤卡(鏡片/教練,管**行為改變**) |
| revisit 對象 | 「這筆**買賣決策**事後看對不對」(30/60/90) | 「你那個**反覆犯的行為洞**補了沒」(本 v1) |
| 重量 | 重(4 寫入車道 + 6 protocol) | 輕(刻意做 447 系統的相反) |
| 狀態 | 已經每週在做 | 本 v1 才開始有記憶 |

**分工**:A 管真相,B 管行為改變;兩個 revisit 是不同對象,**不合併**。
**耦合**:B 的 dogfood 輸入可直接讀 A 已維護的 `investment_note/trades/raw/` CSV(owner `/record-trade` 更新完帳,接著 `/fomo-kernel` 讀同一份出教練卡);但 **B 的狀態層永遠是自己的**(`~/.trade-coach/`,薄、可分發),跟 A 解耦——別人 clone 也能用,owner 只是剛好把輸入指向 investment_note。

---

## 1. 三件事 = 兩個入口狀態 + 一個輪替邏輯

```
/fomo-kernel
   └─ 看 ~/.trade-coach/profile.json 在不在
        ├─ 不在 →【初次】初診出卡 + 落狀態
        └─ 在   →【持續 review】每跑必先對帳上週規矩 → 再找新洞
                    └─ 內含【持續優化】規矩畢業 / 輪替 / 降級(非另一個指令)
```

- **初次** 與 **持續 review** 是兩個**入口狀態**(看 state 自動分支,使用者不必記參數)。
- **持續優化** 不是第三個指令,是 持續 review 跑完後的「規矩輪替」邏輯(§5)。

> ⚠️ **兩條演化軸,別混**:本 v1 演化的是**規矩**(哪個洞、哪條 if-then),**鏡片(交易思路)維持單一 pinned**。「一開始借鑑幾種風格 → 慢慢形成自己的思路 → 每次復盤優化」那條**鏡片演化軸**是外圈(v2→v3),見 §11——它需要 v1 先把「每次復盤」這個迴圈跑起來,才有東西可累積。

---

## 2. 薄本機狀態 `~/.trade-coach/profile.json`

```json
{
  "lens": "vincent-yu",
  "baseline": {
    "as_of": "2026-06-08",
    "AI_exposure": 0.92,
    "avgdown_count": 143,
    "max_position": 0.48,
    "winner_early_pct": 0.71,
    "realized_pnl": -1234
  },
  "active_rule": {
    "id": "no-avgdown",
    "text": "虧損部位一律不加碼，要加先整筆賣掉隔天重買",
    "check": { "metric": "avgdown_count", "op": "<=", "target": 0, "window": "this_period" },
    "set_on": "2026-06-08",
    "held_weeks": 0,
    "broke_weeks": 0
  },
  "graduated_rules": [],
  "history": [
    { "week": "2026-06-08", "hole": "虧損加碼", "rule": "no-avgdown", "held": null }
  ]
}
```

**隱私鐵律(延續 SKILL.md 隱私段)**:`profile.json` **只存聚合 metric 數字 + 規矩文字**,零交易明細(沒有逐筆 buy/sell)。留本機、不上雲、不進 skill 作者收集的反饋。規矩文字可能含 owner 自己寫的 ticker(本機便利,不外傳)。

**薄狀態硬契約(codex + gemini 審 2026-06-17,「保持薄」不能只是口號)**:
- `profile.json` 只存:`active_rule`(1 條)+ 少量聚合 `baseline` + **固定長度的 `history` summary**(retention cap,例:近 12 週,更舊的滾成一行統計)。設 schema budget 上限,超過就摘要,不無限長。
- **禁(那是 A `/record-trade` 的責任)**:重記帳、逐筆 PnL、每日淨值、重建 portfolio。
- **豐富語料另存**:v3a 蒸餾「你自己的鏡片」需要動機/反駁脈絡,薄 JSON 給不了 → 每次出卡把卡片 Markdown(含 YAML frontmatter:`top_flaw` / `motive_q` 的答 / 用戶反駁)寫進 `~/.trade-coach/cards/*.md`。**歷史卡片夾 = B 的語料庫**(留本機、可唯讀掃描),profile.json 維持薄。

**與 v2c 和諧**:`v2c-lens-selection.md` §3 已假設 `~/.trade-coach/profile.json` 存 `active_lens`(selection 預設)。本 v1 先用單一字串 `lens`(永遠 pinned,= v2c 的「v1 相容:單一 lens 永遠 pinned」);v2c 復活時把 `lens` 擴成 `active_lens` 物件(加 stance/lean),**同一個檔、向後相容**,不另開檔。

---

## 3.【初次】初診(= 現行四步 + 落狀態)

完全沿用 `SKILL.md` Step 0–4(格式 → driver map → 引擎 → 出卡前對話確認 → 出卡 → 收反饋),**只在末尾加一步**:

- **Step 5(新)· 落狀態**:出完卡後,把「pin 的鏡片 + 使用者剛挑定的那條 if-then 規矩 + 本次引擎的 key metric 當 baseline」寫進 `profile.json`。`active_rule.check` 由「那個洞對應的維」決定 metric(§6 對照表)。

其餘流程、收斂鐵律、隱私一律不變。

---

## 4.【持續 review】賽後對帳(每跑必做的順序)

state 存在時,**每次跑都先對帳,再找新洞**(落地 SKILL「第二次以後:驗規矩,不要再照同一個洞」):

1. 讀 `profile.json` → 取 `active_rule` + `baseline`。
2. 重跑 `engine/trade_recap.py` on **本週/本期 CSV**。
3. **對帳**:用 `active_rule.check.metric` 在本期重算,判定「守住 / 破 X 次」。
   - **先做 Opportunity Check(codex/gemini blocker)**:只有「**本期存在會觸發破戒的場景**」才算數。例:規矩=虧損不加碼 → 要本期**有浮虧部位**、卻沒往下加,才算「守住」;本期根本沒浮虧或沒交易 → 對帳標 `Skipped`,**不累計 `held_weeks`、不推進畢業**(否則「沒遇到考驗」會被誤判成「克制了」= 零事件偏差)。
   - **守住判定一律用絕對目標**(回退陷阱 fix):`held_weeks`/畢業/降級**只認 `active_rule.check` 的絕對門檻**(如 `avgdown_count <= 0`)。「比上週好」只能當卡上的鼓勵語,**不准影響**畢業邏輯(上週破 5、本週破 4,對 baseline=0 仍是破戒)。
   - **小樣本防噪**:本期樣本過少時,比率型 metric(如早賣率,1 筆=100%、0 筆=NaN)不下判定,標「樣本不足、僅記錄」。
   - 對 **baseline** 比 → 看**總進度**(例:AI 暴險 92% → 78%,敘事用)。
4. **卡頂端先出一行對帳**(進度錨點),再走原本的「找最大洞」——但 `graduated_rules` 與當前 `active_rule` 對應的洞要**跳過**,別每次照同一個分散。
5. 更新 `history` 追加本週一筆;依結果調 `held_weeks` / `broke_weeks`。

卡的結構、收斂鐵律(一個洞 + 一條規矩)、「先承認本事再打」、金額>勝率 等,**全部不變**;只是多了頂端那行「上週那條:守住了 / 破了幾次」。

---

## 5.【持續優化】畢業 / 輪替 / 降級

在 §4 跑完後執行:

- **畢業**:`held_weeks ≥ N`(建議 **N=3**,待 dogfood 校)→ `active_rule` 移入 `graduated_rules`、標日期;新 `active_rule` = 引擎當前排序的**下一個洞**(經 SKILL Step 2 對話確認動機後定稿)。
- **降級(規矩一直破)**:`broke_weeks ≥ M`(建議 **M=3**)→ 不只嘮叨。兩條路:① 把規矩換成**更小步**的 if-then(門檻放寬、先求做得到);② 誠實標「這條沒 land」,讓使用者換一條——鏡子不是法官。
- **永遠只有一條 `active_rule`**(守收斂)。同時存在多個洞時,引擎照 `severity × tier` 排,一次只工作一條。

---

## 6. 對帳要重算什麼(metric binding · engine 改動量已修正)

每條規矩綁一個 **engine 既有**輸出 metric;對帳 = 重跑同一個引擎、讀同一個 metric。**診斷數學可重用**,但「讓對帳讀得到 metric」需要的不是輸出層小補丁(見下方修正):

| 規矩(例) | 綁定維(engine) | metric | check |
|---|---|---|---|
| 虧損不加碼 | `dim_avgdown`(攤平) | 本期攤平破線次數 | `<= 0` 或 `<= 上週` |
| AI 部位砍到 70% | `dim_dispersion`(分散) | 最大單一 driver 暴險 | `<= 0.70` |
| winner 不賣太早 | `dim_exit`(出場) | winner 賣後續漲比 / 早賣率 | 對 baseline 改善 |
| 單一部位 < 25% | `dim_size`(sizing) | 最大單一部位佔比 | `<= 0.25` |
| 持有時間一致 | `dim_holding`(持有) | 同檔內時間框架一致性 | 對 baseline 改善 |

> ⚠️ **修正(codex 審 2026-06-17,已驗 `skills/fomo-kernel/engine/trade_recap.py:684-718`)**:engine 目前**全程 `print()` 到 stdout、無結構化回傳、dim 以中文字串識別**(`dims=[d_exit,d_size,d_div,d_hold,d_avgdown]`)。所以 v1 對帳**不是**「數十行純輸出層」,要先建:① JSON/結構化輸出模式 ② **stable dim id**(現為顯示字串)③ metric binding 表 ④ `active_rule` checker(含 §4 的 Opportunity Check)⑤ 排序時跳過 current/graduated 洞。診斷數學可重用,但這個 **contract 層是新功能、屬中等工作量**(非低風險小改)。

---

## 7. 紅線(別讓它變回 447 系統)

1. **薄狀態 ≠ 第二本帳**:`profile.json` 只存「一條 active 規矩 + pin 鏡片 + 薄歷史 + key metric baseline」。帳的真相留在 A,B 不重記逐筆交易。
2. **一張卡永遠一個洞**:「持續優化」= 規矩**畢業/換掉**,**不是疊維度**。第二份十維報表 = 失敗。
3. **卡是故事不是 dashboard**:沿用 SKILL Step 3 全部鐵律。
4. **排序紀律**(BACKLOG 原話「別讓大願景偷走當下該驗的小東西」):先把 v1(對帳那塊)做到 owner 每週真的會用,**別一次跳到 v2/v3**。

---

## 8. 範圍外 + 銜接點

| 項目 | 屬於 | 為何不在 v1 |
|---|---|---|
| pre-trade gate(`/fomo-kernel check <ticker>` 下單前攔) | BACKLOG 候選 | 是「事前」端;v1 先把「事後對帳」閉環跑順。守則檔(`active_rule`)正好是 gate 的料。 |
| 多鏡片對照 / 誠實閥 | v2c | v2c §5 已釘:閥要能觸發需**先建【風格】機械維**(v2a);v1 維持單一 pinned lens。 |
| 鏡片演化軸(借鑑多家 → 形成自己 → 優化) | v2→v3(§11) | 需先有 v1 規矩迴圈(才有東西可演化)+ v2a【風格】維 + 多鏡片庫(verbatim 校對後)。 |
| thesis 對帳(核對使用者寫過的進場原文) | v3 | 需吃全 context;v1 只對帳行為 metric。 |
| `revenge_trade` / `overtrading` 標籤 | engine 線(BACKLOG ISSUE-2 校正後) | 屬另一條 engine 實作線,動工前先對齊,別重工。 |

---

## 9. 驗收標準(讓之後可實作、可驗)

- [ ] 初次跑完 → `~/.trade-coach/profile.json` 生成,含 `active_rule`(有 `check`)+ `baseline`。
- [ ] 第二次跑(換 CSV、state 已存在)→ 卡**頂端先出對帳行**「上週那條:守住 / 破 X 次」,**再**出新洞;不重複同一個已處理的洞。
- [ ] `held_weeks` 連續達 N → `active_rule` 進 `graduated_rules`,新 `active_rule` = 引擎次洞。
- [ ] `broke_weeks` 連續達 M → 規矩降級或標「沒 land」,不只重複嘮叨。
- [ ] `profile.json` **不含任何逐筆交易明細**(只聚合 metric + 規矩文字)。
- [ ] engine 程式碼改動限「輸出被追蹤 metric 的結構化鍵」一處;診斷邏輯零改動。

---

## 10. 待拍板(實作前)

1. **N / M 值**:畢業要連守幾週(建議 3)、降級要連破幾週(建議 3)?owner dogfood 後校。
2. **baseline 固定 vs 滾動**:總進度對「初診那次」固定 baseline,本期退步對「上週」滾動——本 v1 兩個都留(§4.3),確認要不要簡化成一個。
3. **嚴格單一 active rule**:確認守「一次一條」(本 v1 預設),不開多條並行。
4. **輸入耦合程度**:dogfood 直接讀 `investment_note/trades/raw/`(最低摩擦)還是每週手動丟 CSV(保持產品乾淨)?預設前者,狀態層仍解耦。

---

## 11. 鏡片演化軸(哲學演進)— 外圈 v2→v3

> 來源:owner 2026-06-17「一開始借鑑幾種交易風格 → 慢慢形成自己的思路 → 基於每次復盤持續優化」。這是 BACKLOG 6 步弧線最後一步「哲學演進」的具體化,也是**把「去名」走完**的關鍵——尺從「某位大師的」變成「你自己的」。

**兩條演化軸,同一個 初次 → 持續 → 優化 結構:**

| 軸 | 演化的東西 | 三階段 | 在哪 |
|---|---|---|---|
| 規矩軸 | 哪個洞、哪條 if-then | 初診 → 對帳 → 畢業換洞 | **v1**(§3–5) |
| 鏡片軸 | 用什麼思路判(philosophy) | 借鑑多家 → 縫成自己 → 每次磨利 | v2→v3(本節) |

**鏡片軸三階段:**

- **借鑑(borrow)**:出卡時用幾面大師鏡片照**同一筆交易**,看不同派別怎麼讀(存活紀律 / 動能順勢 / 安全邊際 / 交易心理)。= v2c 的多鏡片,但當**學習/探索**用,不只 selection。
- **形成自己(synthesize)**:跨復盤累積「哪些原則一直打中你、哪些洞你真的在修」,縫成一面 `personal.lens.json`——你自己的尺(human-in-the-loop,沿用 Step 2 確認)。**這是 distill-KOL→lens 的同一機制,套到「distill 你自己的復盤」**:閉環,正中 apply-to-self 護城河(背景見母專案記憶 `project-kol-collect-vs-collector-overlap`)。
- **持續優化(refine)**:每次復盤磨利——砍掉從不咬人的原則、強化一直抓到真洞的、把動機問句調成你真實的 pattern。鏡片跟你共同演化。

**化解「對事不對人」的關鍵分辨:**

- 「**你有意識地縫自己的尺**」(承諾自己要守的原則)≠「**風格縫合怪**」(無意識亂縫、被 `behavior-diagnosis.md` 否決的那種)。前者是**自我承諾**,正是教練 agent「事前承諾 → 事後對帳」要的東西;後者是病。
- **誠實閥(v2c)是讓「形成自己」不淪為自我安慰的護欄**:你能作者你的哲學,但**普世的洞免疫**——不准用自製的尺把最大的漏定義成「我的風格」。本節(形成自己,給自由)和 v2c(誠實閥,守底線)互補。
  - **硬化(codex 審 blocker,2026-06-17)**:① personal lens **不得修改 universal/style 軸**(軸以 `rubric/vincent-yu.md` 為 source-of-truth)② 自製 lens 在**普世維 missing stance 必須 fail-CLOSED(視為一律判)**,不可沿用 `v2c-lens-selection.md §8` 現行的「missing → 閥 OFF」——否則漏填 stance 就能繞過閥放過最大的洞。誠實閥對「自製鏡片」要比對「大師鏡片」更嚴,不是更鬆。

**排序(別讓外圈偷走 v1):** 鏡片軸要能跑,前提是 ① v1 規矩迴圈先在(才有「每次復盤」可累積)② v2a 先建【風格】機械維(誠實閥才有對象,見 v2c §5)③ 多鏡片庫引言先過 verbatim 校對(`feat/multi-master-lens-library`)。**所以這是最外圈,先把 v1 跑順。**

---

## 修訂紀錄

- 2026-06-17 · 初版。回應「能不能把 fomo-kernel 變成每週做的(初次/持續 review/持續優化)」。對齊 BACKLOG 願景層 v0→v3、SKILL「第二次以後」段、v2c `~/.trade-coach/` 假設。狀態:設計中,未實作。
- 2026-06-17 (b) · 加 §11 鏡片演化軸(借鑑 → 形成自己 → 優化),回應 owner「借鑑幾種風格→形成自己思路→持續優化」。釐清「規矩軸(v1)vs 鏡片軸(v2→v3)」兩條演化軸,並以「有意識自縫 ≠ 風格縫合怪」+ 誠實閥化解「對事不對人」張力。
