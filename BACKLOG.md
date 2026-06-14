# trade-review · Backlog

> 從 2026-06-14 的 user-story × engine review 拍出來的待辦。
> 背景脈絡：受眾已收窄為「會用 AI 工具的人（含交易上仍憑感覺的散戶）」；skill 是第一期本體（資料留本機解隱私），不是探針。卡的唯一賣點 = 用你自己的數字，誠實說出你「知道卻沒做到」的事。

> ⚠️ **兩條線同步註記（2026-06-14）**：本檔的「願景層 + ISSUE-1」來自下午的 user-story / 願景 review session；另有一條「engine 實作線」今早 10:34–11:53 並行推進（commit c6cb138→dccf9c4），已 pivot 到 `behavior-diagnosis.md`（對事不對人、行為多標籤）並實作標的層診斷。動 engine 前先跟那條線對齊，別重工。

---

## ISSUE-1 ·〔小–中〕α 雙閘門：資料不夠厚時，不准用「真本事」語氣出 alpha

**問題**
`engine/trade_recap.py` 的 alpha 在樣本不足時，仍以「真本事 α」的*能力*語氣輸出 → 會在看得懂統計的受眾面前砸掉第一張卡的信任。

- `_regress()` (trade_recap.py:231)：門檻只有 `len(df) < 60`（≈3 個月）就吐 α 數字。
- overview (trade_recap.py:634-636)：措辭「真本事 α 年化 X%」，<252 天只加一句小警語，**數字照印**。
- 統計真相：α 作為「能力證據」的顯著性 ≈ Information Ratio × √年數；散戶尺度（1–2 年、幾十筆）**結構上到不了顯著**。而 mock 失真的真因*不是*天數（有 605 天），是**橫截面太窄**（4 檔、98% 同一 driver）→ 算出的是賽道不是選股。
> 行號已更新至 721 行版 engine（dccf9c4 後）。那批 commit 動的是攤平/出場，**α 邏輯未被動，本 issue 仍有效**。

**要改**
1. 新增 `alpha_credible(ab, held, rts)` 雙閘門：(a) 天數夠（建議 ≥252，且仍標「個人 α 偏 noisy」）**AND** (b) 橫截面夠寬（持倉檔數 ≥ N 且最大單一 driver 暴險 < ~50%，否則 α = 賽道紅利非選股）。
2. overview α 印法 (:634-636)：credible 才出「α 年化」；否則只出 β + 「贏大盤 +Xpp（拆帳見下）」，**拿掉「真本事」**。
3. `print_alpha_beta()` (約 :264-289)：不 credible 時整段降級成「報酬拆帳」語氣，不出 `alpha_ann` 數字，只留「贏大盤多少 / 多少來自賽道」的描述性分解（這層不需顯著性、誠實）。
4. lens 同步：`rubric/vincent-yu.lens.json` 的 `dims."alpha/beta".motive_q` + SKILL.md，不 credible 時不要問「這報酬算你選股本事還是敢押高波動」（前提已不成立）。

**驗收**：跑 mock（4 檔 / 98% AI）→ 不再出現「真本事 α +33%」大字，改為「樣本/持倉不足以判定選股能力，先看行為層」之類。
**範圍**：單檔 ~30–50 行，3 處輸出 gate + 1 判斷函式 + 1 處 lens/SKILL 同步。低風險（純輸出層）。約半天。
**原則來源**：跟 `behavior-diagnosis.md` 同向——`prescribe()`（:525-527）對「選股 edge」已經誠實標「資料還判不出、別急著外包也別自滿」；α overview 還沒跟上這個誠實標準，本 issue 就是讓它跟上。

---

## ISSUE-2 ·〔已作廢校正〕原「落地分型」→ 改為「補 behavior-diagnosis 還缺的純損耗標籤」

**校正記錄（2026-06-14）**
本 issue 原寫「落地 trader-types.md 先判型再評分」。**作廢**——`trader-types.md` 已於今早 10:34（c6cb138）被否決移除，正解是 `behavior-diagnosis.md`（對事不對人 · 行為多標籤）。理由比分型強：散戶是風格縫合怪、硬分型會錯判；整個開發照出的真洞全是「對事」算的，分型是多餘中間層；「同訊號不同風格意義相反」用「標的層脈絡」就能解、無單點誤判風險。engine 的 `ticker_diagnosis()`（標的層多標籤）已實作此方向。

**真正還缺的（= behavior-diagnosis.md §下一步2 的 ⏳）**
第一層「跨型純損耗」還差兩個標籤未落到 engine：
- `revenge_trade`：連敗後 / 短時間內報復性加碼同標的 → 處方「連敗 N 次強制冷靜期」。
- `overtrading` 強化：高頻進出且淨輸大盤（Barber-Odean），目前只有頻率 + α/β 部分覆蓋。

**範圍**：engine 內加 2 個偵測 + 對應可驗處方。中等。跟 ISSUE-1 獨立。
**注意**：這條屬「engine 實作線」，動工前先跟該線對齊，別重工。

---

## 願景層（2026-06-14 下午 session 拍板）

**終局形態 = 投資教練 agent（process coach，不是 stock advisor）。** 從「事後一張卡」進化成「事前承諾 → 事後對帳」的閉環。四條紅線必守：
1. **過程教練 ≠ 選股顧問**：教怎麼決策（紀律/守則/對帳動機），絕不碰買哪支（IP/法規/北極星）。
2. **會拒絕 ≠ 有求必應**：有求必應的投資 agent = 「焦慮買答案」的溫床 = 產品要解的病本身。克制是 feature。
3. **有哲學但不寫死**：哲學寫死=拿錯尺、無哲學=yes-man。靠 behavior-diagnosis 的「風格當脈絡」解。
4. **記得你 → skill 要長出本機狀態**（`~/.trade-coach/`）才成 agent；Claude Code/Agent SDK 已是 runtime，不必從零造。

**6 步課程弧線**（每步都有現成零件）：初診(卡) → 訂計畫(守則檔) → 賽前提醒(pre-trade gate) → 賽後對帳(驗規矩) → 升級畢業(守則清單) → 哲學演進(behavior-diagnosis 風格脈絡)。
**演進路徑**：v0 無狀態卡 → v1 守則檔+gate+對帳 → v2 風格脈絡+多鏡片 → v3 全 context 對帳。
**守北極星**：vision 是 agent，但 next action 仍是「那張卡戳中一個真人」（Stage 0 仍未跑 = 最大未驗風險）。別讓大願景偷走當下該驗的小東西。

---

## 候選（未拍板）

- **pre-trade gate**：把「下次只改一件」沉澱成本機 `my-rules.md`，下單前 `/trade-review check <ticker>` 攔一次。市場空白（無人把復盤回灌下一筆）+ 解 Epic D 留存 + 正中北極星。守則檔就是 skill 的「狀態/記憶」。
- **thesis 對帳**：⭐ engine 已起頭（`4599f4f` infer-thesis-from-behavior）。可升級成「拿用戶*寫過的*進場 thesis 原文對帳」——從推斷動機 → 核對原文。skill 能吃用戶全 context，SaaS 結構上做不到。
- **行為 counterfactual 重放**：「賣太早那批多抱 30 天會怎樣」——`fwd_from_px` 已有資料，低成本高回報。
- **多哲學對照**：部分已被 behavior-diagnosis「風格當脈絡」吸收；若要做成「多鏡片顯示分歧」，守住收斂——只呈現分歧最大那一點，別變第二份報告。
