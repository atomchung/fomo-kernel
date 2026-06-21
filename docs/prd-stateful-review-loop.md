# PRD · 有狀態的復盤迴圈(跨 session 對帳)

> 狀態:草案 / 設計中(暫存 worktree,未進 BACKLOG、未拍板)
> 日期:2026-06-20
> 來源:2026-06-20「三段執行演示 + 狀態層設計」session
> 一句話:讓 trade-review 從「無狀態的孤立快照」變成「跨 session 對帳的連續迴圈」——第二次做能基於第一次 revisit。

---

## 1. 背景與問題

trade-review 目前(v0)是**無狀態**的:`engine/trade_recap.py` 每次跑都是「吃 CSV → 算一次 → 出卡」,不記得上次。

**實證(2026-06-20 三段執行演示)**：把 mock 19 筆交易按時間切三段、累積跑三次：
- 頭號洞三次都是「部位 sizing」,處方前兩次一字不差(上限 20%)——**每次被罵同一句**,而 engine 不知道自己在重複。
- 數字大幅跳動(盈虧比 0→3.5、α 16%→33%)是**假演進**：樣本變多 + 市場指標飄移,不是行為改善。
- 第二名的洞會換(分散→出場→攤平)是真的,但那是「新行為進入樣本」的**橫切面**變化,不是縱貫追蹤。
- **沒有任何一次在對帳上次的承諾。**

**核心缺陷**：系統偵測得到「**新行為出現**」,偵測不到「**同一條行為有沒有改善**」——因為沒有「上次的你」當對照點。而 review 的價值正是這條縱貫線(你上次 sizing 76% → 這次 48% 的進步)。

對應評分：處方與留存閉環 4/10。對應 BACKLOG 演進路徑：本 PRD = `v0 無狀態卡 → v1 守則檔+gate+對帳` 裡的**對帳**那塊。

## 2. 目標 / 非目標

**目標**
- 第二次 review 能基於第一次的結果 revisit：對帳上次承諾、引用上次動機問答,把孤立快照串成連續 context。
- 從「橫切面快照」進化成「縱貫進度線」：看得到同一條行為有沒有改善。
- 守隱私鐵律：狀態全程留本機,不外傳。
- 守架構鐵律：engine 維持純算;狀態讀寫 + 對帳敘事在 Claude(runtime)層。

**非目標(明確不做)**
- 不做 pre-trade gate(下單前攔截)——之後的事,本 PRD 只做「事後對帳」。
- 不改 engine 算法/輸出格式(MVP engine 零改動)。
- 不做雲端同步 / 帳號系統(留本機)。
- 不碰選股建議(IP / 法規紅線不變)。

## 3. 核心洞察(為什麼這個設計成立)

> **狀態層不是 engine 的事,是 runtime 的事。Claude Code 本身就是 runtime,有檔案系統存取。**

對帳 = 拿「上次存的數字」比「這次 engine 算的數字」;這個「比」的動作在 Claude 層,engine 繼續當純函式。因此 **MVP engine 零改動**,只改 `SKILL.md` 對話流程 + 讀寫一個本機檔。

## 4. 狀態模型

每次 review 結束 append 一筆,存三個東西：
- **snapshot 快照**：那次關鍵 metrics(headline dim、max_pos_pct、avgdown 次數、盈虧比、α/β…)。
- **commitment 承諾**：「下次只改這一件」+ 可驗 metric + 目標值。
- **motives 動機問答**：Step 2 問到的(ticker → 逢低/凹單、thesis)。下次同標的不重問 + 可對帳。

## 5. 儲存

`~/.trade-coach/log.jsonl`(BACKLOG 願景層已命名的本機狀態目錄),一次 review 一行 JSON：

```jsonl
{"date":"2026-03-20","headline":"sizing","metrics":{"max_pos_pct":0.76,"avgdown":2,"pnl_ratio":3.5},"commitment":{"text":"最大單一部位壓到 20% 以下","metric":"max_pos_pct","target":0.20},"motives":{"PLTR":"逢低加碼"}}
{"date":"2026-06-20","headline":"avg_down","metrics":{"max_pos_pct":0.48,"avgdown":2,"pnl_ratio":2.9},"checkin":{"metric":"max_pos_pct","prev":0.76,"now":0.48,"target":0.20,"verdict":"改善但未達標"},"commitment":{"text":"虧損部位一律不加碼","metric":"avgdown","target":0}}
```

第二行的 `checkin` 就是 §1 缺的那個對照點。(可選人可讀層 `~/.trade-coach/rules.md` 累積規矩清單，之後做。)

## 6. 流程

**初診模式(第一次 / log 為空)**：照現行四步 → 出卡 → **新增：append snapshot + commitment + motives**。

**對帳模式(log 非空)**：
1. `/trade-review` 啟動,Claude **先讀 log 最後一筆**。
2. engine 重算當前快照(吃新 CSV)。
3. Claude 對帳：上次 `commitment.metric` 的 prev vs now vs target。
4. 出**對帳卡**(見 §7)。
5. append 這次。

模式判斷：讀 `~/.trade-coach/log.jsonl` 有無上次紀錄。

## 7. 對帳卡(第二次的輸出)

從「你最大的洞是…」變成「上次承諾 X → 這次實況 Y」：
- 守住 → 肯定 + 給新洞。
- 沒守住 → 咬住那條,不換題。
- 引用上次動機問答。

範例：
```
歡迎回來,距上次 92 天、新增 7 筆。
上次你鎖定「最大倉壓到 20%」——當時 76%,現在 48%：有在降,但還沒到。
別的先不談,這條收掉再說。
(順帶：上次你說 PLTR 是「逢低」,這次它又往下加了兩次——還算逢低嗎?)
```

## 8. 分期

**MVP(今天就能做,engine 零改動)**
- `SKILL.md`：開場讀 log → 判初診/對帳;收尾寫 log。
- Claude 從 engine 輸出抓關鍵數字寫 log。
- 對帳敘事由 Claude 做。

**之後(不在本 PRD 拍板)**
- `rules.md` 人可讀規矩清單。
- thesis 原文存檔(接 ISSUE-3 證據門檻 = 把 motives 從「自我定性」升級成「舉證」)。
- pre-trade gate(下單前讀 rules 攔一次)。
- sizing% 隨時間下降的趨勢線。

## 9. 與現有架構相容

- **engine**：純算不變(§3),MVP 零改動。
- **隱私**：`~/.trade-coach/` 本機、不外傳,符合 SKILL 隱私鐵律。
- **BACKLOG**：= 願景 v1「守則檔+gate+對帳」的對帳塊;`~/.trade-coach/` 已在願景層命名;`behavior-diagnosis.md` 的「問一次存本機、下次同標的復用」= 本 PRD 的 `motives`。
- **ISSUE-3**：thesis 存檔是證據門檻的狀態化(列在 §8 之後)。

## 10. 驗收

- 跑第一次 → `~/.trade-coach/log.jsonl` 出現一筆含 `commitment`。
- 同資料 + 新增交易跑第二次 → 卡開頭是「上次承諾 X → 這次 Y」的對帳,不是重新初診。
- 上次 sizing 76%、這次 48% → 卡明確說「降了但沒達標」,而非當新洞重講同一件。

## 11. 開放問題

- 觸發「該深照」用日曆(季)還是樣本量(新增 N 筆 round-trip)?(討論傾向**樣本量**——交易頻率差異大,日曆是壞代理。)
- 多份對帳單 / 多帳戶怎麼 key?
- `log.jsonl` 損毀 / 用戶手動編輯的容錯?
- 規矩守住後的「畢業」機制(BACKLOG 6 步弧線的「升級畢業」)?
- 第一次樣本太短時(見三段演示：第一次連未實現都算成 +0),要不要延後給 commitment、先標「資料不足」?(接 ISSUE-1 α 雙閘門同向。)
