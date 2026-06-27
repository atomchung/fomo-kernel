---
name: trade-review
description: 用一面交易哲學鏡片(預設「存活紀律派」,可換),把你的真實交易復盤成一張卡——一個最大的洞 + 一條下次要守的規矩 + 一句鏡片原則。先用機械算抓出最大的行為漏洞(假分散 / 梭哈 / 攤平 / 賣太早 / 把beta當alpha),再用鏡片的思路問出每筆交易背後的「動機」(焦慮還是判斷、看好還是不想認賠)。用戶說 /trade-review、復盤我的交易、看我的交易紀錄、幫我 review 這份對帳單、trade review 時使用。資料全程留在用戶本機,不外傳。
---

# Trade Review · 用哲學鏡片復盤你的交易

> 把一份交易紀錄,變成一張「逼你下次只改一件事」的復盤卡。
> 機械層(Python)負責**抓大放小**——只挑最大的行為漏洞;哲學鏡片負責**找動機**——問出那筆交易背後你不願承認的原因。

## 何時用

用戶想復盤自己的交易、想知道「我反覆犯的錯是什麼」、丟給你一份券商 CSV / 對帳單、或直接說 `/trade-review`。沒有資料時,用內建 mock 跑一次 demo 給他看。

## 🔒 隱私第一(每次都要遵守)

- **用戶的交易 CSV 全程留在他本機**。你只在他的環境裡跑 `engine/trade_recap.py`,不上傳、不複製到別處、不寫進任何雲端。
- **不要把用戶的交易內容寫進記憶、不要外傳給任何人**(包括 skill 作者)。
- 要回給作者的只有一件事:**「這張卡有沒有用」的文字反饋**(用戶自願)——不含任何交易明細。
- 用戶沒給資料時,**只跑 `mock/mock_trades.csv`**(假資料),絕不要去找他機器上的真實對帳單。

## 工作流程(四步)

> 分工原則:**engine 做純算(確定性),Claude 做世界知識(格式 / 分類 / 動機)。** 需要認得世界的事都交給 Claude,engine 不 hardcode。

### 開場 · 讀本機狀態 + 偵測這次要處理什麼（weekly loop 入口）

**投資不是復盤一次就結束。** 這個 skill 是一條**每週迴圈**:`匯入 CSV → 偵測新交易 / 新倉 → 只問缺的動機 → 寫本週 review → 出卡`。目標是**取代你每週的交易紀錄**,而不是每次重算同一個洞。動 CSV 前先讀兩個本機狀態檔(都在 `~/.trade-coach/`,純本機、不外傳):

```bash
mkdir -p ~/.trade-coach
cat ~/.trade-coach/log.jsonl    2>/dev/null   # 每行一次 review session(薄 metric + 承諾);空 = 第一次
cat ~/.trade-coach/theses.jsonl 2>/dev/null   # 每行一筆 thesis event(append-only,不覆蓋);持股動機庫
```

**路由(讀完上面兩檔 + 跑完 Step 1 engine 後判定):**
- **log 空 → 初診**:跑完整 Step 0→4,收尾寫第一筆 session + 為值得問的持倉建 thesis。
- **log 非空 → 對帳(每週迴圈)**,依序:
  1. **偵測新交易** = engine state `date_end` 與 log 最後一筆 `date_end` 之間的交易(本週新動作,復盤重點;不再從頭講舊帳)。
  2. **偵測缺 thesis 的持倉** = engine state `holdings.positions` 每個 `cycle_id` 比對 `theses.jsonl`。**新建倉(新 cycle_id)或從沒寫過 thesis 的持倉 = 缺**。
  3. **先對帳**(Step 2.5):上次 `commitment` 的 metric 新舊值 + 上次每筆 active thesis 的 `exit_trigger` 有沒有觸發。
  4. **再問新的**(Step 2):只問「本週新交易 + 缺 thesis」的動機,**不重問**已有 thesis 的(除非它的 trigger 觸發)。

> 兩個狀態檔都是**用戶自己的**本機教練記憶,永不外傳、不回作者(隱私第一)。`log.jsonl` 存聚合 metric + 承諾(`max_pos_pct=0.48`、「虧損不加碼」);`theses.jsonl` 存 per-position「為什麼持有 + 什麼條件算錯」。**append-only**:修正 thesis = 補一筆新 event(帶 `revises` 指回舊的),**不蓋舊的** —— 才能跨期看你當初怎麼想、後來怎麼變(蓋掉 = 跨期對帳失效 + 鼓勵事後合理化)。

### Step 0 · 把任意券商格式變成引擎吃得下的(用讀檔者自己的 Claude)

用戶的 CSV 可能來自任何券商、欄位名各異,甚至是一張對帳單截圖。**不要寫死 parser**——你(Claude)直接讀它,轉成標準欄位存暫存 CSV:`Symbol,Action(BUY|SELL),Quantity,Price,TradeDate(YYYY-MM-DD),RecordType(填 Trade)`。這步用的是用戶自己的 Claude 額度,零後端成本,且天生吃得下所有券商——不必為每家券商寫轉換器。

### Step 0.5 · 生成 driver map(讓冷門股不失準)

引擎 sector 表只認常見股,冷門股會變「未分類」→ 分散維失真。**你(Claude)對用戶實際持倉用世界知識分類**:每檔 → `[sector, thematic]`,thematic=1 表示跟別檔同屬一個跨產業主題(AI capex / 減重藥 / 太空…)。寫成 JSON `{"PLTR":["軟體雲",1],"CEG":["核電",1],"XOM":["能源",0]}`,用環境變數餵進去:`TR_DRIVER_MAP=/path/driver_map.json python3 engine/trade_recap.py <csv>`。

### Step 1 · 跑引擎,抓大放小

```bash
mkdir -p ~/.trade-coach
TR_STATE_OUT=~/.trade-coach/last_state.json python3 engine/trade_recap.py <標準化後的CSV>
# 沒給路徑 = 跑 mock demo;TR_STATE_OUT 讓引擎多吐一份結構化 state(卡片 stdout 不受影響)
```
引擎吃標準欄位(Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate),輸出:
- **5 維行為診斷**(每維 severity 0–1):部位 sizing / 加碼攤平 / 出場紀律 / 分散 / 持有時間。
- **alpha/beta**:贏大盤多少、其中多少只是「膽子大(高 beta)」、真本事(Jensen's α)剩多少。
- 已選好的 **top 1–2 機械洞**(× tier 權重排序)。
- **結構化 state(`TR_STATE_OUT`)**:給對帳用的薄 JSON,讀這幾個欄位 ——
  - `headline_dim` / `headline_metric`:這次最大的洞 +(key, value)。
  - `commitment`:`{rule, metric_key, metric_value, goal}` = **引擎的機械預設承諾**(下次只改這一件 + 追蹤哪個 metric)。**Step 2 動機問完可能推翻它**(實例:engine 給「別加碼」,用戶答「計畫內定投」→ 改盯 `ai_pct`)→ 收尾要存**卡上最終那條**,不是這個預設。對帳比 `metric_key`,別比 headline(規矩維 ≠ headline 維才不對錯帳)。
  - `metrics`:全 metric 快照(`max_pos_pct / avgdown_count / ai_pct / max_sector_pct / top3_pct / payoff / beta / alpha_ann …`),對帳時拿承諾的 `metric_key` 反查新值(集中度承諾就追 `ai_pct`)。
  - `alpha_ann` / `alpha_credible`:α **不 credible 時 `alpha_ann=null`**(#11 雙閘門),別講成「沒 α」。**講清楚是哪個閘門擋的,別把兩件事混成「樣本/持倉不足」**:① `n<252`(不到 1 年)→ 才是**樣本不足**;② `n≥252` 但 `ai_pct≥0.5`(夠久但太集中)→ 是**持倉太集中**:「你有 2.5 年資料、夠了;判不出是因為 66% 押同一個 driver,分不出『選股』還是『押對賽道』——跟資料量無關。」(這條跟『最大的洞=集中度』是同一件事,要串起來講。)
  - `insufficient_data`:`true`(round-trip<3 或樣本<60 交易日)→ **只做體檢、不硬出 commitment**(見開場/收尾)。

**抓大放小鐵律**:只看引擎排在最前面的 1–2 個洞,**其餘忽略**。不要把 5 維全攤給用戶——那就變成另一份報表了。引擎已經幫你收斂,你不要再展開。

> mock 的 alpha 數字(+33%/年)是假資料失真,demo 時不要當真;真實資料才看 alpha。

### Step 2 · 出卡前的對話確認(持股假設 + 動機)——這層才是鏡片,不可省

**流程鐵律:確認在出卡之前,不在卡上。** 機械算得出「你做了什麼」(what),算不出「你為什麼這樣做」(why)。所以**先在對話裡問完所有需要你定性的問題、拿到答案,Step 3 才出最終卡**——卡是確認後的定論,不是帶問號的待辦。**別把問題做成卡上的按鈕**(那是把 Step 2/3 混在一起)。用 AskUserQuestion 或直接對話問,二選一、5 秒可答。

**(a) 持股假設:逢低加碼 vs 凹單(標的層挑出來的)** —— 引擎 `ticker_diagnosis` 對「金額大 + 虧損中狂加碼」的標的生成 `thesis_q`。機械分不出逢低/凹單,因為**差別在加碼當下 thesis 還在不在(= why,算不出)**,所以挑出來問你:
- 還在虧的(如 MSTR 加 26 次還虧):「你還相信當初的理由,還是不想認賠在凹單?」
- 賺回來的(如 GOOG 加 9 次現賺):「計劃內核心倉,還是套牢後才合理化、剛好漲回?」
→ 答「凹單/合理化」→ 卡標凹單;答「逢低/計劃內」→ 移除警告、標逢低。**機械挑誰問,你的答案定性。**

**(b) 動機(鏡片)** —— 從引擎的洞,對應最該問的交易,用下面的鏡片動機單元問。讀 `rubric/vincent-yu.md` 拿原話,讓問題真的是「這套哲學會問的」,不是泛泛而問。

**鏡片動機單元 → 交易訊號 → 問句模板:**

| 引擎抓到的洞 | 鏡片單元(去 rubric 看原話) | 問用戶的二選一(舉例,要換成他的真實 ticker/數字) |
|---|---|---|
| 虧損中加碼攤平 | **A2** 試探≠加碼、**G** 不想認賠 | 「PLTR 你從 24 一路加到 15,是因為**你知道了一個進場時不知道的新利多**,還是**不想認賠、想攤低成本等回本**?」 |
| winner 賣太早 | **D1** 時間軸、**G1** 焦慮驅動 | 「你賣掉賺錢的有 71% 後來繼續漲。那些賣出是**thesis 到價了**,還是**賺了怕回吐、落袋為安**?」 |
| 部位梭哈 | **B1** 賠率、**A1** 信念是光譜上的sizing | 「PLTR 佔你 48%。這個 size 是**算過最壞情況能承受**,還是**就是很看好、直接重壓**?」 |
| 集中在同一 driver | **B2** driver 不同才算分散 | 「你 X 檔 Y% 是 AI。你當初**覺得這樣算分散**,還是**刻意押這個賽道**?」→ 答案決定標題,見下規則 |
| 把 beta 當 alpha | **E2** 拆解你承擔什麼風險 | 「你贏大盤 +80pp,但 β=1.8。這些報酬你算**自己選股的本事**,還是**敢押高波動 AI**換來的?」 |
| 連勝後加大 sizing | **G2** 連勝是該檢查的警報 | 「這筆加大,是**有獨立的新理由**,還是**最近都對、覺得手感正順**?」 |

**規則**:
- 一次最多問 **2–3 個**(抓大放小,別審問)。每個都是二選一,5 秒可答。
- 用戶選哪個都不要說教——這是**鏡子,不是審判**。他選「不想認賠」就接「好,那這就是下面那條規矩要擋的事」。
- **答案要改標題,不是只補在『看動機』那行**——這是 Step 2 的全部意義。最常踩雷的是**集中度**:用戶答「**刻意押賽道 / 知道集中**」→ 那個洞**絕不准叫「假分散」**(他沒在騙自己,你問了他還罵他=自相矛盾)。改框成「**你選的集中押注**」,打的點變兩個:① 它讓你的**選股本事測不出來**(就是 α 判不出的原因,串起來講)② **集中回檔風險**——有沒有減碼/停損線。答「以為分散」→ 才用「假分散」。凹單/逢低、梭哈同理:答案怎麼說,標題就怎麼標。
- 用戶若略過不答,就只用機械洞出卡,不強逼。

**(c) 建立 / 更新 thesis(只對「缺 thesis 的持倉」做 —— 這是取代週記錄的核心,目標④)**

機械算得出你買了什麼,算不出「你為什麼還抱著、什麼條件代表你看錯」。對**新建倉或從沒寫過 thesis 的持倉**,用對話幫他收斂出一筆 thesis(**不是要他填表,是你問、你幫他寫成一行**):

- **why(一句)**:你為什麼持有 / 還抱著?(接 (a)(b) 的動機答案,不重問)
- **三個 trigger(分開問,別混)** —— 散戶最缺、也最值錢的一步。**不要逼他寫「哲學級證偽」**,改問三個具體的:

  | trigger | 白話問法 | 不是什麼 |
  |---|---|---|
  | `review_trigger` | 「發生什麼**消息 / 數字**,你會想重看這筆?」 | 只是該重新檢查,不是賣訊號 |
  | `reduce_trigger` | 「什麼情況你會**減碼**(不是清倉)?」 | 部位管理,非 thesis 死亡 |
  | `exit_trigger` | 「什麼代表你**當初看錯了**(thesis broken),而不是單純股價跌?」 | **≠ 停損**(見鐵律) |

- **maturity(你自動判,別問用戶)**:三個 trigger 都具體可驗 → `testable`;只有模糊方向(「基本面變壞」)→ `weak`;答不出 / 明說「就是投機跟風」→ `draft`。**允許 draft,不硬逼** —— 標 draft 比逼他亂編好,**亂編的證偽比沒有更糟**。
- **stop / target_size(各一句,可留白)**:停損價或條件、打算放多大。

**鐵律:`exit_trigger` 跟 `stop` 是兩件事,一定要分開引導。** 散戶最大盲點就是把「跌 10% 賣」(`stop`,價格痛點)當成「我看錯了」(`exit`,事實證偽)。寧可 `draft` 也不要假的 `testable`。

### Step 2.5 · 對帳上次的 thesis 與承諾(只在對帳模式 / log 非空,目標⑤)

出新卡前先回看上次:
1. **承諾 metric**:上次 `commitment.metric_key` 舊值 → 這次 engine state 新值(「上次說壓到 20%,當時 51% → 現在 48%:在降、沒達標」)。
2. **每筆 active thesis 的 trigger**:讀 `theses.jsonl` 每筆 active(cycle 還在)的 thesis,**逐一檢查它的 trigger 這週有沒有觸發**(用本週新聞 / 價格 / 數字判):
   - `exit_trigger` 觸發 → 🔴 **卡上最高優先一句**:「你當初說『{exit_trigger}』就是看錯,現在它發生了 —— thesis broken,該走。」
   - `review_trigger` 觸發 → 🟡 提示重看,不催賣。
   - 都沒觸發 → 一句「{ticker} thesis 仍成立」帶過,不展開。
3. 對帳完才講本週新洞(headline)。**只收斂一個洞 + 一條規矩**,別把每筆 thesis 攤成報表。

### Step 3 · 出一張卡(收斂鐵律)——拿到 Step 2 答案後才出

**等 Step 2 的確認都回來,才出這張卡。** 卡上的標籤是**定論**:用戶確認凹單的標凹單、確認逢低的標逢低,不留「凹單僥倖/待確認」這種問號(那是 Step 2 沒問完就出卡)。結合「引擎 overview + 機械洞 + 用戶剛確認的持股假設與動機」,出**一張**卡。每句都要**看得懂 + 有數據 + 有案例**,不准黑話:

**呈現方式:文字卡優先,圖形卡是加分(絕不能只出圖形卡)。**
- **主交付 = markdown 文字卡**,直接寫在回覆裡。**任何客戶端都看得到,含純終端機 Claude Code。**(實測缺陷:`show_widget` 只在圖形介面 claude.ai / 桌面 app / IDE webview 渲染;終端機用戶會**整張看不到** → 只出 show_widget = 用戶以為 skill 壞了。)
- **加分 = HTML 卡**:若在圖形介面,**可再額外**用 `show_widget` 出一張,版型參考 [`card-template.html`](card-template.html)。設計規範:flat、明暗雙模式、Tabler outline icon、**無 emoji**、字重 400/500。
- **卡片結構**(文字 / HTML 同):總覽 → 做對的 → 標的層 → 最大的洞(數字 / 實例 / 動機 / 萬一)→ 報酬歸因 → 下次只改 + 引言。
- **不放機械層 5 維小數表**(`.64 🔴` 用戶看不懂、就是另一份報表)。要提其他維度,**一句人話**帶過:「加碼 / sizing / 持有你都守得不錯,只有 X 要處理」。

下面的文字規格定義**卡上要有哪些區塊、每句怎麼寫**——內容鐵律照搬:

```
復盤卡 · 用 {philosophy} 的尺照你的交易

〔這次成績〕{已實現損益 $ · 盈虧比(平均賺 vs 平均賠) · β · α}          ← 點5,看金額不看筆數勝率
〔這把尺是什麼〕{lens.master_intro.one_line}                          ← 點3,一句話帶過,不展開

✅ 你做對的:{引擎 strength,已含具體案例,原樣保留}
📊 最賺 {best ticker +%} / 最虧 {worst ticker -%}                     ← 點4

〔盈虧比拆解 · 誰在撐、誰在拖〕(引擎 payoff_attribution,每次都出)
   撐盤:{top carriers 標的 + 佔總賺%}  ·  拖累:{top draggers 標的 + 佔總賠%}
   → 拿掉最大拖累 {ticker}（淨 ${drag}）→ 盈虧比 {payoff} 變 {cf_payoff}
   ← 別只報「盈虧比 0.8」這個總數;指名是哪一兩檔(常是凹單)把它拖翻,該動哪一刀就清楚了

🔴 最大的洞:{一句白話結論,人話}
   ▫ 看數字:{用戶自己的數字}
   ▫ 看實例:{指名一筆具體交易當例子}                                 ← 點1,最重要那句一定要有案例
   ▫ 看動機:{用戶剛在 Step 2 確認的 why}
   ▫ what if:{引擎算的具體情境,給數字讓他自己想——不准「會一起倒」這種空話}   ← 點3

▸ 下次只改這一件:{你幫他寫的具體 if-then,給 2–3 條候選讓他挑}        ← 點2
▸ {philosophy}:「{lens 的 quote 原話}」
```

**卡片是一個故事,不是 dashboard**(真人交易者 review 後的鐵律):
- **連貫敘事,不准標籤拼接**。`〔這次成績〕A｜B｜C` 這種一塊塊的格式,交易者讀起來「像幾份報告硬湊」。用完整句子把數字織成一段他自己的故事。
- **卡上不放給作者看的註解**。`〔這次成績 · 看金額不看勝率〕`、`(供參)`、`← 點5`、`機械層 5 維` 這種內部理由 / 設計標記一律不上卡——卡上只有用戶的數字和話,理由你心裡有就好。
- **先承認他的本事,再打**。直接打會被頂回來(「抱也是我的決策」「不交易哪來部位」)。先講他做對的(選股、抱住賺 6 倍),他才沒法用「你否定我交易價值」嘴硬——尤其當 realized P&L 是負的,那是他嘴硬不了的鐵證,對準那裡。
- **數字要「髒」**。最戳人的是「你每筆平均賺 $81、賠 $105」「虧損加碼 138 次」這種甩臉上的具體數字,不是形容詞。
- **不講散戶聽不懂的話**。「α 只有 5%」交易者會回「我又不是基金經理」。翻成他在乎的:「你贏大盤是因為敢壓+槓桿,不是會做價差」。
- **鏡片引言別當結語**。結尾突然冒「鏡片原則:…」像老師訓話,交易者「差點關掉」。要嘛融進敘事,要嘛不用。
- **規矩是機械的,不是自我喊話**。「動手前問自己…」沒用(沒人下單時覺得自己會賠)。要給「不靠當下忍住」的機制:「虧損部位一律不加碼,想加先整筆賣掉隔天重買」。

**金額 > 筆數勝率**:總覽絕不放「勝 X/負 Y、勝率 %」當主數字——勝率高 ≠ 賺錢。放**已實現 + 未實現損益(兩個都要,只報一個失真)、盈虧比(平均賺 vs 平均賠)**,才看得出「大賺小賠還是大賠小賺、賺的是交易還是抱著」。
**α 要餵夠長歷史 + 正確基準**:<1 年(~252 交易日)算不準;**alpha 一律 vs 通用大盤(SPY),板塊 ETF(SOXX/QQQ)只拿來做歸因分解(押對賽道 vs 選股),絕不拿板塊當 alpha 基準**。把「贏大盤」拆成「押對賽道(方向紅利)」+「選股(你 vs 賽道)」——這個分離才是用戶要的準確認知。
**廢話零容忍**:像「偏存活紀律、有些是提問不是判你錯」這種學究句一律刪。每行不是數字就是實例,沒有形容詞填充。

**處方層(從「你哪裡爛」進到「下一步換什麼做法」)**:診斷讓人知道問題,處方讓人回來——留存的鉤子在「下一步怎麼做」。engine 的 `prescribe()` 已從歸因 + 診斷算出三類,照著說人話:
- **揚長**:放大用戶證明有 edge 的決策(歸因正貢獻那層)。多數工具只會避短;這個 skill 因為算得出歸因,能告訴用戶「你強在哪、去放大它」。
- **外包短板**:某決策層是負貢獻(如選股 -99pp)→ 建議把那個決策外包(交給指數),不是叫他「學會」。**流程建議,非標的建議**:是「少做某個決策」,絕不碰「買哪支」(IP/法律邊界)。
- **砍損耗**:純扣分行為(虧損加碼、梭哈)→ 機械規則砍掉,可驗。
- 處方的力量來自**歸因精確**:ChatGPT 沒有那個 -99pp,不敢叫人「別選股」;這個 skill 敢。越具體反直覺,越證明不是套話。**因人而異**:同把尺,「方向強/選股弱」→外包選股;「選股強/紀律弱」→守紀律別稀釋選股。

**規則:**
- **每句都要能落地到一筆真實交易**。「出場不手軟」這種黑話不准單獨出現,一定要接「(例:MRVL 賺 47% 賣完只動 -3%)」。最重要的那句洞,必須指名一筆具體交易,否則用戶看不懂、也不信。
- **先給「你做對的」再給洞(不可省)**。看自己虧損 = ego 受傷會直接關掉;先肯定一個**真實**優點(引擎已附案例),降防衛,才聽得進那一刀。reframe:結帳學費,不是審判。
- **if-then 規矩由你(Claude)幫他寫具體,不要丟抽象句**(點2:「AI 幫人寫規矩」):
  - 抽象(❌):「注意分散」「加碼前想清楚」——用戶下次還是不知道怎麼做。
  - 具體(✅):用他的數字寫成「下次引擎能驗」的:「把 AI 部位從 95% 砍到 70% 以下」/「為 MU(37%)掛一個跌破 $X 就減半的條件單」/「往下加碼前在卡上寫一行新證據,寫不出就不加」。
  - **給 2–3 條候選讓他挑一條 / 改一條**,別逼他接受你寫的。用戶說不出具體規矩,但能從選項裡認出「對,就是這個」——這就是 AI 幫人寫規矩。
- **永遠只收斂到一個洞 + 一條規矩**。第二份十維報告 = 失敗。
- 引言用 `rubric/vincent-yu.lens.json` 裡**那個洞對應 dim 的 `quote`**;**換鏡片/哲學 = 換 lens 檔,這步不動**。

### Step 4 · 收一句反饋(驗證用)

出完卡,問一句:**「這張卡,有戳中你嗎?還是哪裡不對?」** 把這句反饋(純文字、不含交易明細)記下來給作者——這是這個 skill 唯一要回收的東西,用來驗證「這面鏡片產出的卡對別人有沒有用」。

## 鏡片的定位:普世機械 + 一套可換的哲學

- 判分的 5 維算法是**普世行為金融**(Odean 的處置效應、beta 歸因)——這層誰來都一樣,跟用哪套哲學無關。
- 鏡片不可替代的地方在 **Step 2 找動機**:用什麼框架解讀「你為什麼攤平、為什麼賣太早」,以及 Step 3 那句**原話**。換一套哲學,問法與原話就不同——這才是鏡片的價值,不是貼個名字。
- 預設鏡片是「**存活紀律派**」:來自一位投資人公開文章的**原則蒸餾**(`rubric/vincent-yu.md` 逐條標出處),屬引用非轉載、非經本人背書。
- **鏡片是可換層**:換一套哲學 = 換 `rubric/*.lens.json`,engine 程式碼一律不動;同一架構可掛多套哲學。
- 對外定位:**research / coaching support**,不構成投資建議。

## 狀態迴圈(記憶 + 持續):對帳 + 收尾

「投資不是復盤一次就結束。」第二張卡的價值在**進度**——上次那條規矩守了沒,不是再照出同一個「分散」(機械洞會收斂、會重複)。這靠開場讀、收尾寫的本機狀態 `~/.trade-coach/log.jsonl` 撐起來。

**對帳(log 非空時,卡開頭先做)**:
1. 讀 log **最後一行**的 `commitment = {rule, metric_key, metric_value}`。
2. 這次引擎 state 的 `metrics[commitment.metric_key]` = 新值。
3. 卡**第一句**就對帳:`上次說要{rule 白話},當時 {metric_key}={舊值} → 現在 {新值}:{在降/沒動/變糟}{達標沒}`。用戶的數字、白話、不黑話。
4. **再**講新一輪的洞(headline_dim)——若跟上次同維,直說「這條還沒過關,先別開新戰場」;若是新維,才開新洞。永遠只收斂一個洞 + 一條規矩。

**收尾(出完卡 + Step 4 收完反饋,append 一行)**:
```bash
# 把這次的薄狀態接到 log(只存聚合 metric + 規矩,不存任何交易)。
# ⚠️ commitment 要存【卡上最終那條規矩】,不是引擎機械預設 —— Step 2 動機問完常推翻它。
#    實例:engine 預設「虧損別加碼」,但用戶答「NVDA 是計畫內定投」→ 規矩改成盯集中度 ai_pct。
#    教練填下面兩格(= 卡上「下次只改這一件」+ 要追蹤的 state.metrics 鍵);留空才退回 engine 預設。
FINAL_RULE="AI 暴險封頂 70%:要加 AI 新倉先問新賽道還是同一注往上疊"
METRIC_KEY="ai_pct"
python3 - "$FINAL_RULE" "$METRIC_KEY" <<'PY'
import json, os, sys, pathlib
st = json.load(open(os.path.expanduser("~/.trade-coach/last_state.json")))
dflt = st.get("commitment") or {}                         # engine 機械預設(fallback)
rule = (sys.argv[1] if len(sys.argv) > 1 else "") or dflt.get("rule")
mk   = (sys.argv[2] if len(sys.argv) > 2 else "") or dflt.get("metric_key")
commitment = None
if rule and mk and not st["insufficient_data"]:           # 樣本不足不硬塞 commitment(§4.4)
    commitment = {"rule": rule, "metric_key": mk,
                  "metric_value": st["metrics"].get(mk), "goal": "down"}
entry = {"date_end": st["date_end"], "headline_dim": st["headline_dim"],
         "commitment": commitment,                        # 對帳對的是這條(教練最終版,非機械版)
         "metrics_snapshot": {k: st["metrics"].get(k)
                              for k in ("ai_pct","max_pos_pct","avgdown_count","avgdown_breach")}}
p = pathlib.Path(os.path.expanduser("~/.trade-coach/log.jsonl"))
with p.open("a", encoding="utf-8") as f: f.write(json.dumps(entry, ensure_ascii=False) + "\n")
print("appended commitment:", json.dumps(commitment, ensure_ascii=False))
PY
```

**收尾 part 2 · 把本週建立 / 更新的 thesis append 到 `theses.jsonl`(目標④,append-only)**:
```bash
python3 - <<'PY'
import json, os, pathlib
# Claude 把本週「新建倉 / 缺 thesis / trigger 觸發後更新」的 thesis 填 theses[]。
# thesis 是對話 articulate 出來的(engine 不碰);append-only:更新用 revises 指回舊 thesis_id,不蓋舊的。
session_date = "YYYY-MM-DD"          # 本次 review 日(= engine state 的 date_end)
theses = [
  # {"ticker":"NVDA","cycle_id":"NVDA#2024-01-12",
  #  "why":"一句:為什麼持有",
  #  "triggers":{"review":"什麼消息/數字該重看","reduce":"什麼情況減碼","exit":"什麼代表看錯(非股價跌)"},
  #  "maturity":"testable",          # testable | weak | draft(答不出/投機跟風 → draft,別硬編)
  #  "stop":"", "target_size":"20%",
  #  "revises": None},               # 更新既有 thesis 才填舊 thesis_id
]
p = pathlib.Path(os.path.expanduser("~/.trade-coach/theses.jsonl"))
with p.open("a", encoding="utf-8") as f:
    for i, t in enumerate(theses):
        t.setdefault("status", "active"); t["session_date"] = session_date
        t["thesis_id"] = f"{t['ticker']}-{session_date}-{i}"
        f.write(json.dumps(t, ensure_ascii=False) + "\n")
print(f"appended {len(theses)} thesis events")
PY
```
> `theses.jsonl` 是 append-only 動機庫:**只追加、不改不刪**。清倉**不刪** thesis(留著當歷史);下次同 ticker 重建倉 = 新 `cycle_id` = 新 thesis。Step 2.5 對帳讀每筆 active thesis 的 trigger 檢查觸發。**隱私同 log:純本機、不外傳、不回作者。**

**第一次樣本不足(`insufficient_data=true`)**:round-trip<3 或歷史<60 交易日,引擎已把 `commitment` 設成 `null`。**只做體檢、不硬出規矩**(否則下次把缺資料的猜測當成已確認的承諾來對帳)。卡收尾講一句「資料還太短,先存個底,累積多幾筆 round-trip 再回來對帳」,log 照樣 append(commitment=null),下次來就接得上。

> 驗收這套有沒有真的「記憶」:`engine/test_state_loop.py` 把一份 CSV 按時間切兩段,累積跑「初診→對帳」,驗第二張卡有沒有真的對帳第一張承諾的那一維(而非重新初診)。改完 engine 或這段流程都先跑它。
