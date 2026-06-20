# trade-review · 需求單一權威(Requirements SSOT)

> 狀態:草案。本檔為**需求**的 single source of truth;PRD/issues 回指本檔。
> 日期:2026-06-20
> 來源:2026-06-20 session(用戶視角 revisit → codex/gemini 三方 review → 八構面評分 → 需求整理 → 狀態模型 review)
>
> **給接手 session 的話**:本檔自包含、可冷讀。搭配讀:GitHub **issue #12**(全生命週期投資 OS 願景錨點)+ `docs/prd-investment-os.md`(雙前端架構)+ `docs/prd-stateful-review-loop.md`(狀態對帳迴圈)。**實作尚未開始**(見 §9)。

---

## 0. 最終目標(owner 2026-06-20 拍板)

> 把 `record-trade` 的功能抽離出來、結合進 `trade-review`,設計一個系統**同時給別人用 + 給自己用**。

這定了 issue #12 三個開放問題:#1 定位 = **雙前端**;#2 既有系統關係 = **吸收功能不吸收形態**(trade-review 當本體);#5 = **同一薄引擎 + 雙前端**。

---

## 1. 需求點 R1–R18

**核心診斷(不變)**
| R | 需求 | 狀態 |
|---|---|---|
| R1 | 用我真實數字照出反覆犯的行為漏洞(sizing/攤平/出場/分散) | 不變 |
| R2 | 機械精算我算不出的(FIFO α/β、賽道 vs 選股) | 不變 |
| R3 | 收斂成一張卡(一個洞+一條規矩) | **要改**:快照→進度卡 |
| R4 | 哲學鏡片找動機,可換大師 | 不變 |
| R5 | 誠實閘門(α 雙閘門 #4 + 證據門檻 ISSUE-3) | 不變(#4 待核對,見 §8) |

**記憶/持續 ← 本 session 從邊緣升為第一性(見 §2)**
| R | 需求 | 狀態 |
|---|---|---|
| R6 | 記憶:記得上次的洞/規矩/動機問答 | **新增·核心** |
| R7 | 對帳:這次基於上次 revisit | **新增·核心** |
| R8 | 長期:跨季/年連續追蹤,不是一次結束 | **新增·核心** |
| R9 | 進度感:看得到行為有沒有改善(縱貫線) | **新增·核心** |

**約束(不變)**
| R | 需求 | 狀態 |
|---|---|---|
| R10 | 隱私(留本機,不外傳) | 不變 |
| R11 | 紅線(過程教練≠選股顧問) | 不變·靠前端開關守 |
| R12 | 低摩擦(輸入零整理,Claude 自動轉券商格式) | 不變 |
| R13 | 克制(會拒絕,不有求必應) | 不變 |

**形態 + 驗證**
| R | 需求 | 狀態 |
|---|---|---|
| R14 | 雙前端(別人用+自己用) | **新增** |
| R15 | 別人用 = 自己用的子集 | **新增** |
| R16 | 吸收 record-trade 功能不吸收形態 | **新增** |
| R17 | Stage 0 真人驗「戳中」(GitHub #3) | 不變·**P0·天花板** |
| R18 | 留存:回來要第二張 | **翻案**:v1+ → 核心 |

---

## 2. 核心升級:記憶+持續 = 第一性需求

owner 作為**首位真實用戶**的反饋:**「投資不是一週復盤就結束。」**

這把「記憶/持續」從原本的**推測**(三段執行演示)、**假設**(#3 判準3 標「留存假設」)、**缺陷**(評分留存 4/10)、**v1+ 願景**(user-stories Epic D「無法觀測」),一次提升成**已確立的第一性需求**。理由不是產品功能選擇,是**投資的本質**(長期持續)。

**推論:無狀態的卡不合格** —— 它只是「一次性體檢」,不是「復盤」(「復」=再一次、對比上次)。三段執行演示已實證:無狀態 → 三次罵同一句、數字假演進、從不對帳。

---

## 3. 新架構下要改的需求(詳細)

- **R3 一張卡**:無狀態快照 → **有記憶的進度卡**。第一張=初診;第二張起=對帳卡(開頭「上次承諾 sizing 壓 20% → 當時 76%、現在 48%:在降、沒達標」)。收斂鐵律(一個洞+一條規矩)不變,語義從「你哪裡爛」→「上次說要改的做到沒 + 新洞」。
- **R6–R9 記憶/持續**(主體):見 §4 狀態模型。**關鍵約束:持續 ≠ 催。** record-week 移除 L2 的教訓 = time-driven 自動推送無效(23 題 0 回答)。要的是「**我回來時它記得我**」(event-driven),不是「它每週推我」。觸發頻率 = **樣本驅動**(累積夠新 round-trip / 一季),不是日曆週。
- **R18 留存翻案**:Epic D 從「無法觀測 v1+」→ 核心可驗;連帶把 #3 Stage 0 判準3(回來要第二張)從「假設」升為核心驗證項。
- **R14–16 雙前端 × 記憶(一個衝突)**:記憶是兩前端都要的核心,但 **#8 chat 引流版(ChatGPT Custom GPT)本質無狀態** → 結構上滿足不了 R6–R9。**解**:chat 版釘死成「一次性體檢的引流漏斗」(明說要記憶就回本機版);「別人用**完整版**」必須有本機薄狀態,不能只是無狀態 chat。

---

## 4. 狀態模型設計結論(codex + gemini review,2026-06-20)

> 兩個外部模型獨立收斂到同一句:**「先設計狀態機,不要先定檔名。」** 原本「該有哪些檔案」是問錯層——檔案是末端。

**該有的狀態(不能薄掉):**
- **decision/thesis**:`open → still / modified / falsified / closed(清倉)`;旁路 `due`(到期 revisit)、`skipped / insufficient`(動機沒捕到/樣本不足)
- **rule**:`active → candidate(達 eligibility)→ graduated(升策略)`
- **source**:`captured(當下)→ confirmed(用戶標哪個影響)→ [Phase C 算 realized alpha]`

**review 該收的刀:**
1. **先狀態機後檔名** —— 上面這些 transition 定義清楚,檔案幾個無所謂。3 檔若沒 transition,第二次仍是「假連續」。
2. **source attribution 捕捉須提前 Phase B**(gemini+codex,2:1 推翻 prd-investment-os 原本延 Phase C):分析可延後,**canonical capture 必須在當下做**(半年後不可補)。
3. **持倉/decision 要落盤**:純「CSV 每次重算」不夠(台股無 API + 清倉標的消失 + 無新交易但 due revisit)。
4. **第一次不必然產生 commitment**:樣本短/用戶跳過 → 寫 `insufficient_data`/`skipped`,否則第二次把缺資料誤當已確認 thesis。
5. **別人用 = log-first**:不逼陌生人維護 thesis/rules;rules 用模板鏡片、theses 選用。
6. **schema version + 原子寫入**:半年狀態不能因 skill 更新損毀;寫入要原子(算完→寫→出卡,不留半寫髒狀態)。

**一個未決分歧(待 owner 拍板):畢業機制**
- gemini:human-in-the-loop(偵測達標→標 candidate→出卡問 owner)。
- codex:自動但要可計算 eligibility,看 **denominator**「有機會違規但沒違規」才算(沒新買入的三次不能讓「不攤平」畢業)。
- 建議合解:codex 的 eligibility 自動偵測 + gemini 的出卡確認。兩家都反對「天真連續 N 次」(會造假畢業)。

---

## 5. 雙前端架構(設計細節見 `prd-investment-os.md`)

- **別人用 = 自己用的子集**:`別人用 = 自己用 − Phase C/D(選股·找資訊) − 外部連接 ⊕ 換可換鏡片`。同一薄引擎,不分叉。
- **紅線靠前端開關守**:引擎只算「你做了什麼/thesis 證偽沒」,不碰「該買什麼」;選股/找資訊只在「自己用」前端開放(且是過程支援、非 recommend)。
- **4 層**:L0 薄記帳 / L1 機械引擎(已實作)/ L2 thesis 一行 / L3 教練迴圈 + 薄狀態記憶。輸出永遠收斂一張卡。

---

## 6. record-trade 功能抽離清單(過 issue #12 三道閘)

- **吸收(4 個)**:薄記帳(L0)/ 決策 narrative 砍成 thesis 一行(L2)/ revisit cadence(L3 對帳)/ 賣後機會成本(L1 已實作)。
- **砍(governance 形態)**:portfolio.md 治理 / weekly-review.md 逐週長文 / 多 protocol(fact-check 精簡版留 Phase C/D)。
- **提前**:source attribution 捕捉提前 Phase B(見 §4.2)。

---

## 7. 與 issue #12 / PRD 的關係

- **issue #12** = 全生命週期投資 OS 願景(Phase A recap→B 交易+update→C 選股→D 找資訊)。本檔 = 它的**需求層**。
- `prd-investment-os.md` = 雙前端架構(答 #12 開放問題 #1/#2/#5)。
- `prd-stateful-review-loop.md` = L3 狀態對帳(需照 §4「狀態機優先」**重寫**,它目前是被 review 打的「3 檔」版)。

---

## 8. 開放問題(未決)

1. 畢業機制:human gate vs 自動 eligibility(§4 有合解建議,owner 未拍板)。
2. 「別人用」要不要薄 update,還是純無狀態 recap(log-first 已定方向,update 程度未定)。
3. source 提前 Phase B,怎麼跟「別人用純本機 + form budget」平衡。
4. 紅線靠前端開關夠不夠硬(prd 開放問題 #6,沒人深 review 過)。
5. **#4 α 謎團(事實層,要核對)**:GitHub #4 標 closed,但實跑 mock(含 9 筆 run1)engine overview 仍印「真本事 α」→ 疑 PR #11 只改卡層、engine `:633-636` 未改。BACKLOG ISSUE-1 還列著。
6. 需求落檔後,user-stories.md(#6)、BACKLOG↔GitHub 不同步(ISSUE-3 未上 GitHub)要對齊。

---

## 9. 實作現況(誠實)

- **engine 乾淨未改**(`git diff` 空,行數 721)。本 session 嘗試加「state JSON 輸出」**未成功**(Edit 未真正套用),從零開始。
- 已落地的是**設計文件**:本檔 + 兩份 PRD + BACKLOG ISSUE-3。**零實作**。
- **天花板仍是 #3 Stage 0**:真人驗「卡有沒有戳中」,`dist/chat/` 已 build = 零工程門檻,**未跑**。owner 自己「要記憶」的反饋已是第一個真人信號,但「真的跑一次卡」還沒發生。

---

## 10. 接手指引(下個 session 從哪開始)

**最小心臟實作(MVP of 新架構,不要一次做整個 OS):**

1. **engine 加結構化 state 輸出**(唯一的 code 改動;設 `TR_STATE_OUT=<path>` 才寫,不設則行為零變):
   - 新增 `build_state(rows, rts, held, dims, overview, ab, rx)` → dict,含:`schema_version`、日期區間、`n_trades/n_round_trips/n_held`、`headline_dim`、`headline_metric`(sizing→`max_pos_pct`、攤平→`avgdown_count`)、`metrics{max_pos_pct, max_pos_ticker, avgdown_count, payoff, beta, alpha_ann}`、`rule`(下次只改那條文字)、`insufficient_data`(`len(rts)<3 or ab.n<60`)。
   - main() 結尾:`if os.environ.get("TR_STATE_OUT"): json.dump(build_state(...), open(path,"w"), ensure_ascii=False, indent=2)`。記得頂部 `import json`。
   - dim key 參考:`d_size["max_pct"]/["max_ticker"]`、`d_avgdown["count"]`、`overview["payoff"]`、`ab["beta"]/["alpha_ann"]`(ab 有 `note` 時這兩個給 None)。
2. **SKILL.md 加初診/對帳雙模式**(prompt 層):開場讀 `~/.trade-coach/log.jsonl` → 空=初診/非空=對帳;對帳=讀上次 commitment.metric 比這次;收尾 append。第一次樣本不足 → 寫 `insufficient_data`,不硬出 commitment。
3. **測試**:用 owner 真實對帳單(或 mock)按時間**切兩段**,跑「初診→對帳」,驗第二張卡有沒有真的基於第一張(不是重新初診)。

**但先想清楚**(本 session 反覆撞到的元問題):這一切實作,在 **#3 Stage 0(真人跑一次卡)** 之前做,有多少是必要、多少是過度設計?owner 自己已是首位用戶,最便宜的驗證是:拿真對帳單跑**現在的卡**一次,看戳不戳中,再決定記憶層怎麼長。
