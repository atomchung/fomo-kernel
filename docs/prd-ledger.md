# PRD · 持久帳本（snapshot-anchored ledger）× 多市場幣別 × 記憶差異

> 狀態：設計定稿（owner 已拍板需求 + benchmark 方案），待實作
> 日期：2026-07-06（判定日期，引用產品假設時帶上）
> 來源：2026-07-06 session——「Phase B：完整取代每週 trade view」需求盤點 → owner 五點需求回覆 → 帳本方案討論 → per-market benchmark 拍板
> 定位：[#31](https://github.com/atomchung/fomo-kernel/issues/31) 的範圍**修訂**（replay-only → snapshot-anchored 雙輸入）＋ [#51](https://github.com/atomchung/fomo-kernel/issues/51) 的**升級**（明示邊界 → 真支援）＋ [#32](https://github.com/atomchung/fomo-kernel/issues/32)/[#33](https://github.com/atomchung/fomo-kernel/issues/33) 的掛載點；上游需求層見 `docs/requirements.md`（R1–R18）、`docs/prd-investment-os.md`（雙前端 + record-trade 功能抽離）

---

## 0. 需求源（owner 2026-07-06 拍板，五點）

1. **帳本要支持「持倉＋交易紀錄」雙輸入**——用戶不容易有完整交易紀錄拼湊持倉。這推翻 #31 原設計「從 broker CSV 流水 replay 還原」的單路前提。
2. **數據要準確，同時支持台股和美股**；呈現幣別跟 output language（en→USD、zh-TW→TWD、zh-CN→CNY）。
3. **持續記憶卡片差異**，讓人有持續使用的訴求——包含記憶投資決策、review 決策。
4. **30/60/90 revisit（賣飛了沒）重要**（#32 確認優先）。
5. **Swap 機會成本（賣 A 換 B net 多少）**（#33 確認要做）。

背景：Phase B 目標 =「連續兩週 owner 只跑 `/fomo-kernel`、不再開 investment_note 的 `/record-trade`」。每週 trade view 的功能基準 = record-trade 週報 11 段（本週決策表／市場表現／新聞驗證／決策品質／narrative／source attribution／swap／改進點／規則檢查／事前登記／revisit scan）。#121 那輪把 #31–33 標「非 MVP 範疇」是 **MVP 發布輪**的判定；本輪目標升級後正式解除排除。

**核心一句話：帳本以「持倉宣告」為錨點、交易紀錄做增量疊加——兩種輸入進同一本帳；行為診斷與帳本推導分離，各吃各夠用的資料。**

---

## 1. 帳本：snapshot-anchored ledger

### 1.1 兩種輸入現實

| 輸入 | 形態 | 現況 |
|---|---|---|
| 交易紀錄 | broker CSV / 對帳單截圖（BUY/SELL 流水） | engine 現行唯一輸入；**假設完整**，缺漏會靜默算錯持倉 |
| 持倉宣告 | 券商 app 持倉頁截圖 / 表格（ticker、股數、均價） | **新增**——多數用戶拿得出這個，拿不出完整流水 |

### 1.2 資料模型

`~/.trade-coach/ledger.jsonl`（append-only 事件流，純本機，延續隱私鐵律；schema_version 隨檔）：

```jsonc
// 事件一：持倉宣告（Step 0 由 Claude 讀截圖/表格標準化，零 parser——同現行 CSV 標準化模式）
{"type": "snapshot", "as_of": "2026-07-06", "source": "user_declared",
 "positions": [{"ticker": "NVDA", "market": "US", "currency": "USD",
                "shares": 40, "avg_cost": 152.3}],   // avg_cost 可缺
 "cash": {"USD": 8200, "TWD": 120000}}                // 可缺

// 事件二：交易（現行 CSV 標準化流程，補 market/currency/fee 欄）
{"type": "trade", "date": "2026-07-08", "ticker": "2330.TW", "market": "TW",
 "currency": "TWD", "action": "BUY", "qty": 100, "price": 985, "fee": 42,
 "source_file": "TW_statement_202607.csv"}

// 事件三（reconcile 產物）：調整留痕
{"type": "adjustment", "date": "...", "ticker": "...", "delta_shares": -5,
 "reason": "reconcile: user snapshot 35 vs derived 40"}
```

### 1.3 持倉推導（會計的「期初餘額＋本期異動」）

1. 取**最近一筆 snapshot 當錨點**；
2. 錨點之後（`date > as_of`）的 trades 依序疊加（`old ± trade = new`）；
3. 沒有任何 snapshot → 純 replay（= 現行行為，**向後相容**）。

關鍵性質：**錨點之前的歷史缺失不是錯誤，是常態**——不影響當前帳正確性。replay 中出現負股數（= 交易紀錄不完整的鐵證，現行 engine 靜默）→ 明確提示「補一張持倉快照，或接受行為分析 only 模式」。

### 1.4 準確性機制：re-declare 即對帳（reconcile）

用戶隨時再丟一張持倉截圖 → 與「錨點＋疊加」推導結果 diff：

- **一致** → 驗證通過，卡上標「帳本已對帳 ✓」；
- **不一致** → 列差異（「推導 NVDA 40 股，你宣告 35 股——中間可能有我沒看到的交易」），**預設以用戶新宣告為準**：寫入 `source: "reconciled"` 的新 snapshot ＋ adjustment 事件留痕。

把「數據準確」從一次性假設變成**每次丟截圖就自我修復**的閉環。snapshot 事件序列同時免費送出 #31 想要的「持倉結構時間序列」。

### 1.5 關鍵分離：行為診斷 ≠ 帳本推導

兩個消費者對「資料完整性」要求不同，分開就都滿足：

| 消費者 | 吃什麼 | 完整性要求 |
|---|---|---|
| **帳本推導**（持倉/損益，準確優先） | 只信錨點之後的 trades | 嚴格——錨點保證正確起點 |
| **行為診斷**（5 維/攤平/出場，樣本優先） | 所有看得到的 trades（含錨點之前） | 寬鬆——樣本越多越準，缺漏標記即可 |

### 1.6 誠實分級（延續 α 誠實化精神：缺什麼標什麼，不硬編）

- `avg_cost` 缺 → 市值/佔比/集中度照算（只需 shares×現價），未實現損益標「均價未宣告」；
- 已實現損益標「自 {錨點日} 起算」；
- snapshot 帶入的持倉開倉日不可知 → `cycle_id` 用錨點日＋標 `origin: snapshot`（持有期左截斷，出場/持有維語氣降級）。cycle_id 沿用現行三段格式（`ticker#日期#序號`），theses 對帳迴圈不受影響。

### 1.7 冷啟動紅利

入口從「先整理交易 CSV」降到「**丟一張持倉截圖就能開始**」：

- **Day 0**：截圖 → snapshot → 結構診斷卡（集中度/賽道/sizing 現況三維不需要歷史）＋ AI 照常猜 theses → 記憶迴圈當場啟動；
- **之後每週**：丟增量交易 → 攤平/出場/payoff 等行為維逐步解鎖；
- **隨時**：再丟截圖 = 自動對帳。

對「別人用」前端（`prd-investment-os.md`）是重大摩擦削減。

---

## 2. 多市場多幣別

**原則：資料層永遠原幣記帳，換算只發生在呈現層。**

### 2.1 資料層

- 每筆事件帶 `market` + `currency`；
- 台股 ticker 標準化為 `2330.TW`（yfinance 慣例），Step 0 由 Claude 判市場補後綴；
- `fee` 欄吃台股手續費/證交稅；支援零股。

### 2.2 呈現層：resolved output language → display currency

- 映射：en→USD、zh-TW→TWD、zh-CN→CNY；掛在 SKILL.md 既有的 Output language resolution 段；存 `profile.md` 可 override；
- **合計數字**換算成 display currency；**分項保留原幣**並附換算（「NVDA +$1,200（≈NT$38,400）」）——用戶要對得上券商 app；
- 例外：持倉單一市場時直接用該市場幣別（美股 only 的繁中用戶不該看到滿卡無謂的台幣換算）。

### 2.3 匯率 gate（不准把 #64 剛修好的「離線確定性」弄假回去）

- 匯率走 last_px 同一條 fetch 路徑（yfinance `TWD=X` 等），同受網路 gate；
- 離線 → 用 state 快取的上次匯率，卡上標「匯率截至 MM-DD」；從無匯率 → 只出原幣，不猜；
- 測試沿用 #64 的 offline 強制模式，匯率可 pin。

### 2.4 對標層：per-market benchmark（owner 2026-07-06 拍板）

> 先前提案「只對主市場算＋明標範圍」被否決：owner 部位台美約 50/50，「主市場」不存在。改為 **per-market 分算**。

- **拆分**：按 `market` 分子組合，各對各的基準——US→SPY（現行）；TW→台股大盤（`^TWII` 不含息 vs `0050.TW`，實作時定案並在卡上明標所選基準與含息差異）；
- **呈現**：兩行並列、各含資金佔比，例：
  ```
  美股部位（52% 資金）：贏 SPY +14pp = 押賽道 +9 / 選股 +5；β 1.4
  台股部位（48% 資金）：贏加權指數 +3pp（無板塊對照、按大盤計）；β 0.9
  ```
- **不合成總 α**——混合組合對單一基準的 α 是假精確。會計層（總報酬/未實現/已實現）照 display currency 合計，那是會計事實、可加總；
- **統計檢定力誠實**：拆開後每邊樣本變小，`alpha_credible`（≥1 年 & |t|≥1.96）per-market 各自判，更容易 not_significant——這是誠實不是缺陷（混算的顯著性本來就是假的），語氣 gate 照現行規則走；
- **台股 excess_split 第一版**：SECTOR_BENCH 是美股 ETF 表（#92），台股部位標「無板塊對照、按大盤計」——沿用既有 `coverage<1` 語彙，不硬造假拆帳。

### 2.5 行為層不拆市場（與 2.4 的分界）

5 維行為診斷、driver map（Claude 世界知識天然跨市場：「AI capex」主題可同時含 NVDA 與 2330.TW）、sizing 佔比（用同日匯率換 display currency 算比例，比例對匯率誤差不敏感）——**人只有一個，行為不分國界**。只有 α/β 對標層 per-market。

---

## 3. 記憶與卡片差異（留存的產品機制）

三層記憶，兩層已有、一層新增。目標：**第二次打開的前 10 秒出現三個「它記得我」的證據**。

1. **承諾迴圈**（已有）：`log.jsonl`，開場對帳「上次那條規矩守住了沒」。
2. **決策記憶 × review 決策**（已有＋本輪接上）：`theses.jsonl` 記「當初為什麼買」；revisit（#32）到期把它調出來對答案——owner 需求第 3 點的「記憶投資決策 + review 決策」就是這兩件事的閉環。
3. **卡片庫＋變化摘要**（新增）：
   - 每次出卡落 `~/.trade-coach/cards/YYYY-MM-DD.md`（YAML frontmatter：headline、key metrics、commitment＋卡全文）——歷史可回看；也是 v3a「蒸餾自己的鏡片」的語料庫（`v1-weekly-coach.md` §2 設計過、未實作）；
   - 開場對帳行升級成**變化摘要**：上週 vs 本週最大的 3 個變化（承諾 metric 動了多少／持倉結構怎麼變／上次的洞收斂了沒）。diff 全從 `log.jsonl` 的 metrics 序列算，唯一前置是把 `metrics_snapshot` 從現在只存 4 個 key 擴成全量 metrics（一行改動）。

---

## 4. Revisit（#32）× Swap（#33）：改從帳本事件驅動

兩個 issue 的設計成立，掛載點升級：

- **出場偵測從帳本事件來**（trade 使 shares→0 或減 ≥50% → 自動排入 `revisit.jsonl` 的 30/60/90 queue），不再靠每次全量 CSV 重推——清倉標的從此不會「從宇宙消失」；
- due 檢查進 SKILL 開場路由（與「偵測新交易」並列）；賣飛對比價走 last_px 既有路徑；
- **swap 配對**（賣 A 後 N 天內買 B）從 ledger 事件流算，AI 推＋用戶 confirm（inference-first 不變）；swap net 必對位（賣飛只有在「換入 < 原標的」時才算真錯誤）；閒置 cash 偵測靠 snapshot 的 `cash` 欄更準。

---

## 5. 實作切分

```
PR-1  ledger 資料層：雙事件 + current_holdings() + reconcile diff + Step 0 讀持倉截圖   ← 地基
PR-2  多市場/幣別：market/currency + 台股正規化 + 呈現層換算 + 匯率 gate + per-market α/β（依賴 PR-1 欄位）
PR-3  revisit + swap（#32 + #33，依賴 PR-1 事件流）
PR-4  卡片庫 + 變化摘要（獨立可並行，最小）
```

每個 PR：過五套測試（`python3 tests/run_all.py`）＋ mock fixture 只用假資料＋ **影響用戶可見行為者同 commit 更新 SKILL.md**（契約同步鐵律）。

## 6. 開放問題（實作時定，不阻塞動工）

1. 台股 benchmark：`^TWII`（不含息，台股高股息會低估基準）vs `0050.TW`——選定後卡上明標。
2. swap 配對窗 N 天（#33 預設 14）與 revisit 觸發門檻（#32 預設清倉/減半）——沿 issue 預設，dogfood 後校。
3. reconcile 差異的呈現粒度（逐檔 vs 摘要）。
4. `ledger.jsonl` 與既有 `last_state.json` 的關係：state 是推導快照（可重算、非權威），ledger 是事實層——state 檔角色不變。

## 7. 紅線（沿用，此處只列不重述）

- 隱私：ledger/cards/revisit 全在 `~/.trade-coach/`，純本機、不外傳、不回作者；mock 之外的 CSV 永不進 git（.gitignore 機制防線不動）。
- 薄狀態：ledger 是事實層 append-only，不是第二本 447 系統——**不做** portfolio.md 式治理層、不做每日淨值序列。
- 卡的形態：輸出永遠收斂一張卡，帳本數字是卡的地基不是新報表。
- 離線確定性（#64）：所有網路依賴（含匯率）走同一 gate、測試可 pin。
