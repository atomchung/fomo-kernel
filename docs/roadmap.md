# FOMO Kernel · Roadmap v0→v3(規矩軸 × 鏡片軸)

> 🗄️ **設計史快照(2026-06-17,非當前規格)**:本文是改名前的 v0→v3 設計藍圖,記錄當時思路與 owner 拍板的排序決策。**部分已被 main 實作超越**——當前的本機狀態格式、engine 是否已有結構化輸出等,一律以 `skills/fomo-kernel/SKILL.md` 與 `engine/trade_recap.py` 為準(例:state 已是 `log.jsonl`/`theses.jsonl` + `TR_JSON`/`TR_STATE_OUT`,engine 已輸出結構化 card JSON)。文中 `trade_recap.py:NNN` 行號為當時版本、可能已位移。保留作設計脈絡與決策史。

> 狀態:規劃中(2026-06-17),待 codex + gemini 審。把 `BACKLOG.md` 願景層、`docs/v1-weekly-coach.md`、`docs/v2c-lens-selection.md` 整合成一張 v0→v3 總圖,標清楚**兩條演化軸**的依賴與排序。
> 北極星:卡是鏡子不是法官;一張卡一個洞;克制 = feature;對事不對人。
> 紅線:① 保持薄(別變回 owner 那套 447 檔案重系統)② 一張卡一個洞 ③ 過程教練 ≠ 選股顧問 ④ 有哲學但不寫死 ⑤ **Stage 0(卡戳中一個真人)未過前,別讓大願景偷走當下**。

---

## 1. 兩個系統(輸入/邊界,不是版本)

「trade review」是兩個容易混淆的系統,本 roadmap 只規劃 **B**:

| | **A · `/record-trade`**(`investment_note/`) | **B · `/fomo-kernel`**(本 repo) |
|---|---|---|
| 角色 | 記帳 + 決策 + revisit(管**真相**) | 復盤卡 / 教練(管**行為改變**) |
| revisit 對象 | 「買賣決策事後對不對」 | 「反覆犯的行為洞補了沒」 |
| 狀態 | 重系統,已每週在做 | 輕、可分發,本 roadmap 要長出迴圈 |

**不合併**。B dogfood 可讀 A 的 CSV,但 B 的狀態層獨立(`~/.trade-coach/`,薄)。詳見 [`v1-weekly-coach.md`](v1-weekly-coach.md) §0。

---

## 2. 兩條演化軸(本 roadmap 的組織主軸)

| 軸 | 演化什麼 | 三階段(初次→持續→優化) | 成熟期 |
|---|---|---|---|
| **規矩軸** | 哪個洞、哪條 if-then | 初診一條規矩 → 對帳守了沒 → 畢業換下個洞 | **早**(v1 全到位) |
| **鏡片軸** | 用什麼**思路**判(philosophy) | 借鑑幾家大師 → 縫成你自己的尺 → 每次復盤磨利 | **晚**(v2→v3) |

**核心排序主張**:先讓「規矩軸」的每週迴圈跑起來(v1),才有「每次復盤」這個動作可以累積;鏡片軸是疊在這個迴圈上的外圈。

---

## 3. v0→v3 總圖(逐版)

每版標:規矩軸狀態 / 鏡片軸狀態 / 依賴 / engine 改動 / 出場條件(DoD) / 對應 BACKLOG。

### v0 · 無狀態卡 ✅ 已出貨
- **規矩軸**:一次性出卡,一個洞一條規矩,不記得。
- **鏡片軸**:單一 pinned 鏡片(去名「存活紀律派」)。
- **狀態**:已 ship,全面去名,測試通過。
- BACKLOG:`v0 無狀態卡`。

### v0.5 · gate:卡可信化 + Stage 0 真人測 ⚑ owner 2026-06-19 拍板「先 gate」

**主線前必過(不是版本,是 gate)。** codex blocker:第一張卡若印假 α、攤 5 維,給真人看會為錯的理由失敗。
- **α 語氣雙閘門**(GitHub #4):資料不夠厚不准用「真本事 α」語氣(已驗 engine 仍印,`trade_recap.py:633-636`)。
- **收斂卡面**:落實「一卡一洞」,5 維降為安靜供參(對齊 `SKILL.md:44`)。
- **Stage 0 真人測**(GitHub #3 P0):3-5 個**真人**跑第一張卡,收「有沒有戳中」。
- **出場條件**:多數真人說「戳中」+ 卡面無假 α → 才開 v1 主線。

### v1 · 每週迴圈 + 薄狀態 ◐ 已規格化(`v1-weekly-coach.md`),未實作
- **規矩軸**:✅ 三模式(初次/持續 review/持續優化)+ `~/.trade-coach/profile.json` 薄狀態 + 對帳上週規矩 + 畢業換洞。
- **鏡片軸**:單一 pinned + **誠實標尺**(卡上標「這把尺偏存活紀律,動能交易者僅供參考」);雙鏡選擇 = v1.1(owner 2026-06-19 拍板:先單鏡低成本驗迴圈)。
- **依賴**:v0。
- **engine 改動**:中等(雙審修正)——建 JSON/metric contract + stable dim id + `active_rule` checker + Opportunity Check;診斷數學重用。見 `v1-weekly-coach.md §6`。
- **DoD**:第二次跑先對帳(守住/破 X 次,含 Opportunity Check:沒交易→Skipped)再找新洞;規矩連 N 週守住畢業換洞;狀態零交易明細、守薄契約(`v1-weekly-coach.md §2`)。
- BACKLOG:`v1 守則檔+gate+對帳` 的**後對帳半邊**(pre-trade gate 拆成可選平行軌,見 §3 尾)。

### v1.1 · 鏡片選擇(2-3 面選一 pin)○ 待動能鏡校對

- **鏡片軸**:【借鑑】起點——動能派鏡片過 verbatim 校對後,首次 onboarding 給 2-3 面選一 pin。
- **依賴**:v1 + `feat/multi-master-lens-library` 引言校對。
- **選擇 ≠ 演化**:此步只是「選你認同的尺」;縫自己的尺仍在 v3a。

### v2a · 建【風格】機械維 ○ v2c 已釘前置,未建
- **規矩軸**:多一個【風格】維可選(追高/順勢、ride-vs-cut)。
- **鏡片軸**:為誠實閥準備「可觸發的對象」。
- **依賴**:可與 v1 **並行**(engine spike,先驗風格維能否從 CSV 穩定算出);只有誠實閥才真依賴它(codex:不必嚴格串在 v1 後)。
- **為何先做**:v2c §5 釘死——現有機械維 100% 是【普世】,誠實閥**結構上無對象可觸發**;必須先建【風格】維 → 才補 stance → 閥才能動。**順序不可顛倒**。
- BACKLOG / v2c:v2c §5 關鍵路徑前置。

### v2c · selection + 誠實閥 + 多鏡片 compare ○ 藍圖(PR #9),未實作

> 編號:本機狀態已上移到 v1(supersede `v2c-lens-selection.md §8` 的「v2b=狀態」);本 roadmap 故跳過 v2b、用 v2c 對應該藍圖,免撞名。
- **鏡片軸**:【借鑑】階段——幾面大師鏡片(`feat/multi-master-lens-library`),用戶可選/pin;誠實閥上線(普世洞免疫;風格洞被選的尺 `inverted` 才端岔路)。
- **依賴**:v2a(閥需風格維)+ 多鏡片庫引言過 verbatim 校對。
- BACKLOG:`v2 風格脈絡+多鏡片`;細節見 [`v2c-lens-selection.md`](v2c-lens-selection.md)。

### v3a · 形成自己的鏡片 ○ 概念(`v1-weekly-coach.md` §11)
- **鏡片軸**:【綜合】階段——跨復盤累積「哪些原則一直打中你、哪些洞你真的在修」,縫成 `personal.lens.json`(human-in-the-loop)。= distill-KOL→lens 同機制,套到 distill 你自己的復盤。
- **依賴**:v2c(先借鑑過幾家才知道認同啥)+ v1 迴圈(累積過幾次復盤)。
- **紅線**:personal lens 仍是薄 lens.json(非 manifesto);誠實閥仍守(不准自製尺放過普世洞)。

### v3b · 優化鏡片 + 全 context 對帳 ○ 概念
- **鏡片軸**:【優化】——每次復盤磨利 personal lens(砍死從不咬人的原則、強化一直抓到真洞的)。
- **規矩軸頂**:thesis 對帳——拿用戶**寫過的**進場原文對帳(不只推斷動機)。
- **依賴**:v3a +(thesis 對帳需接 context 源,如 `investment_note/wiki/`)。
- BACKLOG:`v3 全 context 對帳` + 哲學演進;candidate「thesis 對帳」。

### v1b · pre-trade gate(Stage 0 後)
- 把 `active_rule` 沉澱成 `my-rules.md`,下單前 `/fomo-kernel check <ticker>` 攔一次。
- **依賴**:v1 的 `active_rule`;定位 **Stage 0 後的 v1b**(codex:不留模糊平行軌)。
- 註:BACKLOG 把 gate 併進 v1;本 roadmap 拆成 v1b(v1 先把事後對帳閉環跑順,事前攔截是另一個 UX 面)。

---

## 4. 依賴與關鍵路徑

```
v0 ─▶ v0.5 gate ─▶ v1 ─▶ v1.1 ─▶ v2c ─▶ v3a ─▶ v3b
      α+真人測      迴圈   鏡片選擇  閥+compare 形成自己  優化+thesis
                     └─▶ v1b pre-trade gate(Stage 0 後)
   v2a 風格維 ∥ 與 v1 並行(engine spike)─────▶ 餵 v2c 誠實閥
```

- **gate-first**:`v0.5`(α 誠實化 + Stage 0 真人測)是主線前 gate,沒過不開 v1。
- **關鍵路徑**:`v1 → v1.1 → v2c → v3a → v3b`。
- **可並行**:`v2a 風格維`(engine spike)與 v1 同時驗可算性;多鏡片庫 verbatim 校對在 v1 期間並行(`feat/multi-master-lens-library` DRAFT)。
- **不可顛倒**:`v2a 風格維` 必在 `v2c 誠實閥` 之前——`v2c-lens-selection.md §5/§10` 已證閥無風格維則無觸發對象。

---

## 5. Stage 0 與排序原則

- **Stage 0 ≠ owner 每週 dogfood**(codex 裁決):owner 每週用 = 驗「迴圈機制好不好用」;**Stage 0 = 3-5 個真人驗「第一張卡有沒有戳中」**(GitHub #3 P0,需求側仍 0)。兩者別劃等號;Stage 0 住在 `v0.5` gate。
- **gate-first**(owner 2026-06-19 拍板):先把第一張卡修到能戳中真人(α 誠實 + 收斂),再投資每週迴圈——別在未驗的卡上蓋迴圈。
- **規矩 > 鏡片演化**:鏡片軸的「形成自己/優化」吃多次復盤累積,沒 v1 迴圈就沒資料來源;但鏡片「選擇」(v1.1)可早。
- **別讓外圈偷走當下**(BACKLOG 原話):Stage 0 未過前,克制衝 v2c 多鏡片那種「看起來很完整」的功能。

---

## 6. 待拍板(open decisions + 提議,送審重點)

**規矩軸(v1)**
1. **N / M**:規矩連守幾週算畢業(提議 N=3)、連破幾週降級(提議 M=3)?
2. **baseline 固定 vs 滾動**:總進度對「初診固定 baseline」、本期退步對「上週滾動」——兩個都留還是簡化?
3. **嚴格單一 active rule**:守「一次一條」(提議是),不開多條並行?
4. **輸入耦合**:dogfood 直接讀 `investment_note/trades/raw/`(最低摩擦)還是每週手動丟 CSV(保持產品乾淨)?提議前者、狀態層仍解耦。

**鏡片軸(v2→v3)**
5. **personal.lens 怎麼累積**:系統自動推「你認同的原則」(每次復盤投票),還是每次手選沉澱?提議「自動推草稿 + 用戶確認」(沿用 Step 2 的 human-in-the-loop)。
6. **誠實閥觸發後 UX**:端岔路問「你想反駁哪邊」後,怎麼把答案沉澱進 personal lens 而不變嘮叨?
7. **pre-trade gate 歸屬**:維持 BACKLOG 的「併進 v1」,還是本 roadmap 提議的「拆成可選平行軌」?

---

## 7. 給審查者的問題(codex / gemini)

1. **排序**:規矩軸(v1)先於鏡片軸(v2+)對嗎?還是「借鑑多家」該更早(第一張卡就多鏡片照)?
2. **拆分**:把 pre-trade gate 從 BACKLOG 的 v1 拆出、降為可選平行軌,合理嗎?
3. **哲學一致性**:「形成自己的鏡片」(v3a)會不會撞「對事不對人」/「鏡片不寫死」?用「有意識自縫 ≠ 風格縫合怪 + 誠實閥(普世洞免疫)」化解,夠嗎?
4. **紅線張力**:「保持薄」vs「持續累積 personal lens + history」會不會矛盾?薄狀態的界線該畫在哪?
5. **engine 估計**:v1 宣稱「幾乎不動 engine」(metric binding 重用 5 維)——有沒有低估改動量?
6. **依賴鏈**:§4 的關鍵路徑有沒有錯把可平行的畫成序列、或漏掉前置?
7. **可分發性**:dogfood 讀 `investment_note` CSV,會不會讓產品悄悄依賴 owner 私人系統、傷害「別人 clone 也能用」?
8. **遺漏**:有沒有缺的 version / 步驟 / 紅線?

---

## 8. 銜接文件
- [`v1-weekly-coach.md`](v1-weekly-coach.md) — 規矩軸 v1 細節規格 + §11 鏡片軸概念。
- [`v2c-lens-selection.md`](v2c-lens-selection.md) — 鏡片軸 v2a/v2b(selection × 誠實閥)細節。
- [`../BACKLOG.md`](../BACKLOG.md) — 願景層原文 + candidates + ISSUE-1。
- GitHub issues:#3(Stage 0 P0)、#4(α 雙閘門)、#5(損耗標籤)、#6(user-stories 校準)、#8(chat 引流版)。

## 9. 雙審整合(codex + gemini,2026-06-17)

> 兩方各自對抗性審本 roadmap。codex 總評「有結構問題」、gemini「需修正後推進」。共識修正已部分回寫 `v1-weekly-coach.md`(§2 薄契約 / §4 Opportunity Check + 回退陷阱 / §6 engine 改動量 / §11 fail-closed)。

**共識修正(兩方一致 + 已驗證)**
1. **engine「幾乎不動」嚴重低估**(已驗 `skills/fomo-kernel/engine/trade_recap.py:684-718` 全 stdout、dim 用中文字串):v1 對帳要建 JSON/metric contract + stable dim id + checker。
2. **鏡片「選擇」可在 v1**:SKILL 換鏡片只換 lens 檔、engine 不動;只有**誠實閥**要等 v2a 風格維。→ v1 提供 2-3 面選一 pin,別硬鎖單一(否則動能交易者 Stage 0 就流失)。我原把「選擇」也推到後面是過頭。
3. **薄狀態硬契約**:retention cap + schema budget + 豐富語料改存 `~/.trade-coach/cards/*.md`。
4. **v3a fail-closed**:personal lens 不准動軸;普世維 missing stance 視為一律判(非 v2c §8 現行的閥 OFF)。
5. **統計洞**:Opportunity Check(沒交易/沒觸發場景 → `Skipped`)+ 絕對門檻判畢業(回退陷阱)。
6. **可分發**:產品吃 standard schema;owner CSV 走本機 adapter,不進預設入口。

**裁決(兩方分歧 / 補判)**
- **Stage 0 ≠ owner 每週 dogfood**(codex blocker,採):§5 原文把兩者劃等號**作廢**——owner 每週=驗機制;Stage 0 = 3-5 個**真人**驗第一張卡有沒有戳中。
- **alpha 語氣 gate**(codex blocker,採):已驗 engine 仍印「真本事 α」(`trade_recap.py:633-636`);α 雙閘門([#4](https://github.com/atomchung/fomo-kernel/issues/4))設為主線前 gate。
- **版本標籤漂移**(codex):本 roadmap 為準——本機狀態歸 v1(supersede `v2c-lens-selection.md §8` 的 rollout 編號);v2a 風格維是 engine spike,**可與 v1 並行**驗可算性,不必嚴格串在 v1 後。
- **pre-trade gate**:採 codex,定成「Stage 0 後的 v1b」,不留模糊平行軌。
- **(pre-existing,非 roadmap 鍋)**:codex 指「一卡一洞」與實作矛盾(`SKILL.md:44` 別攤 5 維 vs `:79`+engine 仍印 5 維)——屬 SKILL/engine 線,另案。

**owner 拍板(2026-06-19,已重排 §3–§5 主線)**
1. ✅ **先插 v0.5 gate**(α 誠實化 + 收斂卡面 + 3-5 真人 Stage 0)再動每週迴圈。
2. ✅ **v1 先單鏡 + 誠實標尺**,雙鏡選擇拆成 v1.1。
> 已據此重排:v0.5 gate、v1.1 鏡片選擇、pre-trade → v1b、v2b 改 v2c(本機狀態歸 v1)。

---

## 修訂紀錄
- 2026-06-17 · 初版。整合 BACKLOG/v1-weekly-coach/v2c 成 v0→v3 總圖,以「規矩軸 × 鏡片軸」為組織主軸。待 codex + gemini 審。
- 2026-06-17 (b) · 加 §9 雙審整合。codex+gemini 對抗性審:6 條共識修正(已回寫 v1-weekly-coach)+ 4 條裁決;2 個 sequencing(v0.5 gate、v1 鏡片選擇成本)待 owner 拍板再重排主線。
- 2026-06-19 · owner 拍板兩個 sequencing → 重排主線:gate-first(v0.5)、v1 單鏡 + v1.1 選擇、pre-trade → v1b、版本去 v2b(狀態歸 v1)。§3/§4/§5/§9 更新。
