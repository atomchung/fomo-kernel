# PRD · 投資 OS:吸收 record-trade,雙前端(自己用 + 別人用)

> 狀態:草案(worktree,未進 issue;codex + gemini review 中)
> 日期:2026-06-20
> 來源:本 session — 讀 issue #12 完整本文 + 完整讀 record-trade(investment_note) + owner 拍板最終目標
> 定位:issue #12 的 **Phase B 設計** + 回答其開放問題 **#1 / #2 / #5**;昨天的 `prd-stateful-review-loop.md` 收編為本檔 Layer 3

---

## 0. Owner 最終目標(2026-06-20 拍板)

> 把 record-trade 的功能抽離出來、結合進 fomo-kernel,設計一個系統,**同時給別人用 + 給自己用**。

這一句直接定了 issue #12 的三個開放問題:

| issue #12 開放問題 | 本 PRD 拍板 |
|---|---|
| #1 定位(個人工具 vs 可分發 vs 兩前端) | **雙前端**:自己用=個人工具、別人用=可分發產品 |
| #2 跟既有系統的關係 | **吸收功能**:fomo-kernel 當 OS 本體,抽 record-trade 的功能、砍其形態 |
| #5 一個 OS vs 同引擎多前端 | **同一薄引擎 + 雙前端** |

---

## 1. 核心架構:一個薄核心,兩個前端

```
   ┌─ 前端 A:自己用(個人工具)────────────────┐
   │   全 Phase:找資訊→選股→交易→update→recap   │
   │   可選讀外部 context(KOL/wiki/research,唯讀) │
   │   用自己的 RULES.md 當尺                      │
   └──────────────────────────────────────────┘
            │  (掛在同一核心上)
   ┌─────── 共用核心(兩前端完全一樣)───────────┐
   │   ① 機械引擎:5 維行為診斷 + α/β + 賣後機會成本 │
   │   ② 薄狀態 ~/.trade-coach/:thesis 一行 + 規矩 cadence │
   │   ③ 輸出永遠收斂成一張卡                       │
   └──────────────────────────────────────────┘
            │  (掛在同一核心上)
   ┌─ 前端 B:別人用(可分發產品)──────────────┐
   │   只開 recap(+ 薄 update);選股/找資訊關閉    │
   │   純本機、無外部連接、隱私留本機              │
   │   用可換大師鏡片(VY 等)當尺                  │
   └──────────────────────────────────────────┘
```

---

## 2. 第一原則:別人用 = 自己用的子集

**不是兩個系統,是一個系統關掉幾個開關。**

```
別人用  =  自己用  −  Phase C/D(選股/找資訊)  −  外部資料連接  ⊕  換成可換鏡片
```

→ 「同一引擎」因此成立:引擎不分叉,兩前端只差「開放哪些 Phase + 資料邊界 + 用哪把尺」。維護一套核心,不維護兩套系統。

---

## 3. 紅線 = 前端開關,不是引擎分叉

引擎只算「**你做了什麼 / 你的 thesis 證偽了沒**」,永遠不碰「**該買什麼**」。

- 選股 / 找資訊(Phase C/D)**只在「自己用」前端開放**,且即使開放也是**過程支援**(檢查你自己的 thesis 還成不成立),不 recommend。
- 「別人用」前端把 Phase C/D **關掉**。

→ issue #12 的北極星紅線(過程教練 ≠ 選股顧問)由**前端開關**守住,不需要兩個引擎、不需要兩份 codebase。

---

## 4. record-trade 功能抽離清單(過 issue #12 三道閘)

> 三道閘:① 改下一筆測試 ② 硬性 form budget ③ 盡量自動。

| record-trade 組件 | 功能 / 形態 | 過閘? | 處置 |
|---|---|---|---|
| update(CSV→holdings/mark) | 功能 | ✅ | **吸收 → Layer 0 薄記帳** |
| 決策 narrative(11 欄) | 功能 | ⚠️ 欄爆 form budget | **吸收功能、砍欄 → Layer 2 thesis 一行**(why/證偽/停損/size) |
| revisit cadence(30/60/90,still-believe/falsified) | 功能 | ✅ falsified→停損,強改動作 | **吸收 → Layer 3 教練迴圈對帳**(最該吸收的) |
| swap analysis(賣後機會成本) | 功能 | ✅ | issue #12 Layer 1 已列「已實作」→ **已在核心** |
| source attribution(KOL→決策歸因) | 功能(重) | ⚠️ | **延到 Phase C**(屬「找資訊」價值驗證,非 Phase B) |
| portfolio.md 治理(Health/Actions/$5k 閾值) | 形態 | ❌ form budget 爆 | **砍** |
| weekly-review.md 逐週長文 journal | 形態 | ❌ 長文 wiki | **砍 → 收斂成卡** |
| 多 protocol(fact-check/concurrency/sync) | 形態(源自真事故) | ⚠️ recap 用不到 | Phase B **砍**;fact-check 精簡版留 **Phase C/D**(那時要 web search) |

**結論:record-trade 真正該被吸收的功能只有 4 個**(薄記帳 / thesis 一行 / cadence 對帳 / 賣後機會成本),其餘大半是 governance 形態 → 砍。

---

## 5. 4 層架構(issue #12)× 雙前端

| Layer | 共用? | 自己用 | 別人用 |
|---|---|---|---|
| **L0 記帳(薄)** | 共用 | 同 | 同(或更薄:見開放 #2) |
| **L1 機械引擎** | 共用 | 同 | 同 |
| **L2 thesis 一行** | 共用 schema | 可連 wiki 預填 | 純手寫(可留白) |
| **L3 教練迴圈**(gate/週/對帳) | 共用 | 同 | 同 |
| **記憶 ~/.trade-coach/**(薄·自動) | 共用 schema | 多「外部 context」可選欄 | 純本機欄,無外部 |
| **輸出一張卡** | 共用鐵律 | 同 | 同 |

---

## 6. 雙前端差異矩陣

| 維度 | 自己用(個人工具) | 別人用(可分發) |
|---|---|---|
| Phase 範圍 | 全(A→D) | A recap + 薄 B update |
| 選股/找資訊 | 開(過程支援,不 recommend) | **關** |
| 資料 | 本機 + 可選讀外部(KOL/wiki,唯讀) | 純本機,零外部 |
| thesis 來源 | wiki 預填 + 手寫 | 純手寫 |
| 用的尺 | 自己的 RULES.md | 可換大師鏡片(VY…) |
| 隱私 | 自己的 repo | 不外傳、不入記憶(現有鐵律) |
| 發布 | 不發布 | 可分發 skill |

---

## 7. 分階段(issue #12 Phase A–D × 雙前端何時可發布)

| Phase | 內容 | 雙前端 |
|---|---|---|
| **A recap**(現況) | 行為教練卡 | 兩前端共用,已有 |
| **B 交易+update** | 吸收 record-trade 的薄記帳 + thesis 一行 + cadence 對帳 | 做完 **別人用即可發布**(recap + 薄 update) |
| **C 選股** | 研究/KOL 蒸餾接進來當過程支援 | **只自己用** |
| **D 找資訊** | 研究收集鏈 | **只自己用** |

→ 「別人用」的最小可發布版 = Phase A + B 核心。Phase C/D 是「自己用」專屬的外擴。

---

## 8. 開放問題(給 codex / gemini review)

1. **雙前端怎麼在一個 codebase 共存?** feature flag / engine 抽成共用 package + 兩個薄 skill / build-time 分支?哪個最不會讓核心變胖?
2. **「別人用」要不要 update(記帳)?** 還是別人用只做**無狀態 recap**(丟 CSV 出卡、不維護 portfolio)= 最薄、最易發布、最像現在的 v0?「薄 update」對陌生用戶是價值還是摩擦?
3. **source attribution 延到 Phase C** —— 但「哪個資訊源讓我賺錢」正是「自己用」的主要動機之一。延後會不會閹掉自己用的核心價值?還是它本來就該等 Phase C?
4. **thesis 一行的雙向壓力**:對「自己用」(有 wiki 深 thesis)會不會太薄、逼他降級?對「別人用」會不會太重、逼陌生人寫他不會寫的東西?
5. **統一 ~/.trade-coach/ schema** 怎麼同時:薄、餵雙前端(別人用無外部欄/自己用有)、又不讓「自己用」的外部 context 洩漏進「別人用」的隱私邊界?
6. **紅線靠前端開關守**夠不夠硬?「自己用」開了選股過程支援,會不會在共用引擎裡留下「給別人用也能被誘導 recommend」的後門?

---

## 9. 收編 / 取代

- 昨天 `docs/prd-stateful-review-loop.md` → **收編為本檔 L3 教練迴圈 + 開放問題 #5**(它的 `~/.trade-coach/log.jsonl` = 本檔記憶層的 recap 切片)。
- issue #12 → 本檔是它 **Phase B + 開放問題 #1/#2/#5** 的設計落地;Phase C/D 與開放問題 #3/#4/#6 仍掛 issue #12。
