---
name: fomo-kernel
description: 用一面交易哲學鏡片(預設「存活紀律派」,可換),把你的真實交易復盤成一張卡——一個最大的洞 + 一條下次要守的規矩 + 一句鏡片原則。先用機械算抓出最大的行為漏洞(假分散 / 梭哈 / 攤平 / 賣太早 / 把beta當alpha),再用鏡片的思路問出每筆交易背後的「動機」(焦慮還是判斷、看好還是不想認賠)。用戶說 /fomo-kernel、復盤我的交易、看我的交易紀錄、幫我 review 這份對帳單、trade review 時使用。不用於個股研究、選股建議、大盤預測或財經新聞問答——那些不是復盤,不要觸發。資料全程留在用戶本機,不外傳。
---

# FOMO Kernel · 用哲學鏡片復盤你的交易

> 把一份交易紀錄,變成一張「逼你下次只改一件事」的復盤卡。
> 機械層(Python)負責**抓大放小**——只挑最大的行為漏洞;哲學鏡片負責**找動機**——問出那筆交易背後你不願承認的原因。

## 何時用

用戶想復盤自己的交易、想知道「我反覆犯的錯是什麼」、丟給你一份券商 CSV / 對帳單、或直接說 `/fomo-kernel`。沒有資料時,請他提供券商 CSV / 對帳單(截圖也行,Step 0 讀得懂),**並同時給「試駕」選項**(見下節);只想看靜態長相 → README 的範例卡。

## 🧪 試駕模式(沒資料也能體驗流程;三個防護缺一不可)

用戶沒資料或想先體驗 → 用 AskUserQuestion 給兩選項:「**提供我的 CSV** / **先用內建假資料試駕一遍**」。選試駕 → 拿 `mock/mock_trades.csv` 走完整四步流程,但:

1. **狀態一律不落盤**:`TR_STATE_OUT` 指到臨時目錄(如 `mktemp -d` 下);`coach.py`/`ledger.py`/`revisit.py`/`problems.py` 的 `--state`/`--log`/`--theses`/`--rules`/`--cards-dir`/`--ledger`/`--queue`/`--book` 全部覆寫指到同一個臨時目錄。`~/.trade-coach/` 的 log.jsonl / theses.jsonl / profile.md / rules.jsonl / problems.jsonl / ledger.jsonl / revisit.jsonl / cards/ **一個字都不寫**——假資料的承諾進了教練記憶,下次真復盤的對帳基準就是髒的。收尾改成一句講解:「真實使用時,這條規矩會存進你本機的教練記憶,下週回來先對帳」。試駕結束想親自確認沒弄髒正式狀態 → `python3 engine/coach.py data-status` 是單一事實源(#165),列出 `~/.trade-coach/` 下每個檔案的存在/大小/筆數,跑前跑後比對就知道有沒有意外落盤。
2. **Step 2 照問,但標明是演練**:動機問題照走 AskUserQuestion——試駕就是要讓他體驗「我的答案會改變卡」這個差異化;但問句裡標明「示範資料,隨便選一個,看卡怎麼跟著變」,不逼他為不是他的交易編動機(#53 的尷尬就消了)。
3. **卡標示範**:卡頭標「示範 · 假資料,非真實成績」;α/β 附一句「示範資料失真,別當真」——失真警告是**呈現層(你)的責任**,引擎對任何輸入一致、沒有 demo 分支(#89)。

卡尾必收一句引導:「想復盤自己的交易 → `/fomo-kernel your.csv`」。

## 🔒 隱私第一(每次都要遵守)

- **用戶的交易 CSV 全程留在他本機**。你只在他的環境裡跑 `engine/trade_recap.py`,不上傳、不複製到別處、不寫進任何雲端。
- **不要把用戶的交易內容寫進記憶、不要外傳給任何人**(包括 skill 作者)。
- **誠實邊界(隱私話術別過度承諾)**:資料**不上傳後端、不落地儲存到別處、作者永遠拿不到**;但你(Claude)為了復盤**必須讀** CSV/JSON,交易內容自然進你的 context —— 這跟用戶平常用 Claude 一樣,不是「完全不經過任何伺服器」。README / 卡上的隱私話術照這個精度寫,別講成「絕對不離開你的電腦」。
- 要回給作者的只有一件事:**「這張卡有沒有用」的文字反饋**(用戶自願)——不含任何交易明細。
- 用戶沒給資料時,**請他提供或走試駕模式**(內建假資料、不落盤);絕不要主動去翻他機器上的真實對帳單。
- 用戶問「我電腦上到底存了什麼/怎麼備份/怎麼砍掉重來」(#165)→ 指到 `python3 engine/coach.py data-status`(列存在/大小/筆數,不印交易內容)/ `data-export --out FILE.zip`(打包備份)/ `data-reset --dry-run`(先預覽)再 `--confirm`(真的刪);別自己用 `rm -rf` 或手動列檔案湊答案,這三個命令是唯一事實源。

## 🌐 Output language (apply every time)

Everything the user sees — your dialogue, the `AskUserQuestion` options, and the final card — must be in **one resolved output language**. Do not hardcode a language. Resolve it per session, first match wins:

1. **Explicit request this session** — the user says "give it to me in English" / "用中文" / passes `lang=en`.
2. **Saved preference** — `output_lang:` in `~/.trade-coach/profile.md`, if present.
3. **Conversation language** — the language the user is speaking to you in right now. This is the default; follow it, don't impose a language.
4. **Fallback** — Traditional Chinese (`zh-TW`).

Once resolved:
- Run the whole flow (dialogue, questions, card) in that language. Lens files (`rubric/*.lens.json`) currently carry Traditional-Chinese quotes/prompts — translate them faithfully on the fly into the resolved language when you write the card.
- Pass it to the engine each run as `TR_LANG=<code>` (e.g. `TR_LANG=en`) for forward-compatibility. The engine does not consume it yet — its own printed CLI card and lens strings stay Chinese for now; full engine/lens localization keyed on `TR_LANG` (a strings table) is tracked separately as internationalization work. This still governs what the user sees **today**, because the card is one **you** write from `build_card_data()`'s structured JSON, not the engine's printed card.
- Persist it: on first run, or whenever the user switches, write `output_lang: <code>` into `~/.trade-coach/profile.md` (alongside the profile principles) so the next session resolves to their preference at step 2.

> The Traditional-Chinese phrasings, question templates, and card examples throughout the rest of this SKILL are **illustrative of intent**, not literal strings to copy — express their meaning in the resolved language.

## 💱 Display currency(幣別呈現,#51/#129;apply every time)

引擎原幣記帳,**換算只發生在你寫卡這層**。規則:

1. **display currency 跟 resolved output language**:en→USD、zh-TW→TWD、zh-CN→CNY;用戶指定(「用美元」)或 `profile.md` 的 `display_currency:` 優先,並照 Output language 同款方式持久化。
2. **例外:持倉單一市場 → 直接用該市場幣別**(`currency_meta.mixed=false` 時就用 `aggregate_currency`,美股 only 的繁中用戶不該看到滿卡無謂的台幣換算)。
3. **合計換算、分項原幣**:卡上總覽(已實現/未實現/總損益)可換算成 display currency;**單檔數字一律原幣**、必要時附換算(「NVDA +$1,200(≈NT$38,400)」)——用戶要對得上券商 app。
4. **換算匯率來源** = `currency_meta.fx`(engine live 抓的兌 USD 匯率);要換成非 USD 的 display currency,用交叉率(例 TWD 顯示:USD 金額 ÷ fx.TWD)。**離線/缺匯率**(`fx_error` 或 `data_integrity.fx_gaps` 非空):讀上次 `last_state.json` 的 `currency_meta.fx` 當快取,卡上標「匯率截至上次對帳」;連快取都沒有 → **只出原幣、分幣別列,不猜匯率**。
5. **混幣組合**(`mixed=true`):聚合數字(overview/盈虧比/what-if/sizing 權重)已是 USD 基準;`pnl_by_currency` 有原幣分桶供呈現;`fx_gaps` 非空時聚合是原幣近似——**必須在卡上明示**「X 幣別缺匯率,佔比為近似值」。`alpha_beta_note` 非 null 時 α/β 段落照抄該註記(通常=提醒頂層 α/β 僅含 scope 市場;完整 per-market 呈現規則見 Step 1 的 alpha/beta 段)。

## 工作流程(四步)

> 分工原則:**engine 做純算(確定性),Claude 做世界知識(格式 / 分類 / 動機)。** 需要認得世界的事都交給 Claude,engine 不 hardcode。

### 開場 · 讀本機狀態 + 偵測這次要處理什麼（weekly loop 入口）

**投資不是復盤一次就結束。** 這個 skill 是一條**每週迴圈**:`匯入 CSV → 偵測新交易 / 新倉 → 只問缺的動機 → 寫本週 review → 出卡`。目標是**取代你每週的交易紀錄**,而不是每次重算同一個洞。動 CSV 前先讀本機狀態(都在 `~/.trade-coach/`,純本機、不外傳):

```bash
mkdir -p ~/.trade-coach
cat ~/.trade-coach/log.jsonl    2>/dev/null   # 每行一次 review session(薄 metric + 承諾);空 = 第一次
cat ~/.trade-coach/theses.jsonl 2>/dev/null   # 每行一筆 thesis 或 exit_narrative event(append-only);持股+出場動機庫
cat ~/.trade-coach/profile.md   2>/dev/null   # 你的交易目標 + 3 條個人原則(復盤對照基準);空 = 第一次幫你建
python3 engine/ledger.py holdings 2>/dev/null # 帳本推導的當前持倉(snapshot 錨點+交易疊加);讀不到=還沒開帳
python3 engine/revisit.py scan 2>/dev/null    # 出場追蹤:到期的 30/60/90 revisit(#32);空 due=本週不問
python3 engine/problems.py stats --today <今天> --rules ~/.trade-coach/rules.jsonl 2>/dev/null  # 問題帳(#137):top 1–3 + 規矩對位;空=還沒開帳
```

**路由(讀完上面兩檔 + 跑完 Step 1 engine 後判定):**
- **log 空 → 初診**:跑完整 Step 0→4,收尾寫第一筆 session + 為值得問的持倉建 thesis。
- **log 非空 → 對帳(每週迴圈)**,依序:
  1. **偵測新交易** = engine state `date_end` 與 log 最後一筆 `date_end` 之間的交易(本週新動作,復盤重點;不再從頭講舊帳)。
  2. **偵測缺 thesis 的持倉** = engine state `holdings.positions` 每個 `cycle_id` 比對 `theses.jsonl`(**只比 thesis 行,`event:"exit_narrative"` 的行不算**——減倉出場的 narrative 帶同一個 cycle_id,誤匹配會讓沒 thesis 的持倉永遠不被補)。**新建倉(新 cycle_id)或從沒寫過 thesis 的持倉 = 缺**。
  3. **先對帳**(Step 2.5):上次 `commitment` 的 metric 新舊值 + 上次每筆 active thesis 的 `exit_trigger` 有沒有觸發。
  4. **補缺的 thesis**(Step 2):缺 thesis 的持倉由 AI **猜**(標 `inferred`、零提問),只對「行為矛盾、金額最大的 1 檔」問一句;已有 thesis 的不碰(除非 trigger 觸發)。
  5. **問新出場的賣出理由**(Step 2(d)):scan 的 `recent_exits` 有還沒問過的 → 對近 14 天的清倉/大減倉問「當時為什麼賣」(窗口過了就永久缺這筆)。

> 兩個狀態檔都是**用戶自己的**本機教練記憶,永不外傳、不回作者(隱私第一)。`log.jsonl` 存聚合 metric + 承諾(`max_pos_pct=0.48`、「虧損不加碼」);`theses.jsonl` 存 per-position 的五要素持股假設(why 判斷 / horizon 時間軸 / triggers / stop・target_size / driver 同注辨識,#136)。**append-only**:修正 thesis = 補一筆新 event(帶 `revises` 指回舊的),**不蓋舊的** —— 才能跨期看你當初怎麼想、後來怎麼變(蓋掉 = 跨期對帳失效 + 鼓勵事後合理化)。

### Step 0 · 把任意券商格式變成引擎吃得下的(用讀檔者自己的 Claude)

用戶的 CSV 可能來自任何券商、欄位名各異,甚至是一張對帳單截圖。**不要寫死 parser**——你(Claude)直接讀它,轉成標準欄位存暫存 CSV:`Symbol,Action(BUY|SELL),Quantity,Price,TradeDate(YYYY-MM-DD),RecordType(填 Trade)`。這步用的是用戶自己的 Claude 額度,零後端成本,且天生吃得下所有券商——不必為每家券商寫轉換器。

- **🌏 多市場(#173):非美股一律標 `Market`/`Currency` 兩欄(缺 = 美股 USD,向後相容)**。台股尤其要點:`Symbol` 填**完整 yfinance 代號**——上市掛 `.TW`(台積電 `2330.TW`)、上櫃掛 `.TWO`(如 `5483.TWO`);上市/上櫃是**你的世界知識,引擎不查表**。`Market=TW`、`Currency=TWD`,日期若是民國年(`113/07/10`)先換成西元。港股 `.HK`+`HKD`、日股 `.T`+`JPY` 同理。這樣引擎才抓得到台股報價、α/β 才對得上加權指數(`^TWII`)、combined 最大單點依賴/賽道曝險分母才含台股——**否則台積電從引擎世界消失,最大依賴會誤報成某支美股**(這就是 #173 的病灶)。混幣聚合入 USD、缺匯率時明示「近似」都由引擎處理(見 Step 1 `currency_meta`/`honesty_ledger`),你只負責把格式標對。

**📒 帳本雙輸入(snapshot-anchored;#31 修訂版,設計見 `docs/prd-ledger.md`)**:用戶丟的可能不是交易流水,而是**持倉截圖/持倉頁**——多數人拿不出完整交易紀錄,這是常態不是錯誤。兩種輸入進同一本帳(`~/.trade-coach/ledger.jsonl`,append-only、純本機、不外傳):

- **持倉快照** → 你讀圖/表轉成 positions JSON(`[{"ticker","shares","avg_cost"?,"market"?,"currency"?}]`,**均價不知道就留空,別編**),存暫存檔後:
  `python3 engine/ledger.py append-snapshot /tmp/pos.json --as-of <宣告日,通常今天> --cash '{"USD":8200}'`
  (snapshot 語意 = 該日**收盤後**狀態,同日交易視為已含在宣告數字內。**`--cash` 把下方 💵 收到的現金餘額一起存成錨點**(flat dict,多幣別 `{"USD":..,"TWD":..}`)——多週累積 ≥2 個錨點後,引擎自動逐段 rollforward 對帳、量化漏記金流,見 Step 1 `data_integrity.cash_residuals`;#180。)
- **已有帳本、又丟來新快照 → 先 `reconcile`,不要直接 append**:`python3 engine/ledger.py reconcile /tmp/pos.json` 會列宣告 vs 推導的差異——一致 = 對帳通過(卡上可標「帳本已對帳 ✓」);不一致 = 把差異講給用戶聽(「我推 NVDA 40 股,你說 35——中間可能有我沒看到的交易」),他確認後以**他的宣告為準**:`append-snapshot --source reconciled`。這是「數據準確」的機制:每丟一次快照 = 帳本自我修復一次。
- **交易 CSV**(標準化後)→ 除了餵 `trade_recap.py`,同時記帳:`python3 engine/ledger.py append-trades <標準化CSV>`(自動去重,每週增量匯入、重疊期重複匯入都安全)。輸出的 `skipped_future_dated` 非 0(#169:TradeDate 晚於今天,疑似 Step 0 把 MM/DD 誤判成 DD/MM)→ 那幾筆已被拒收、沒寫進帳,回頭跟用戶核對原始對帳單那幾筆的日期,別自己猜著改;記完帳接著排出場追蹤:`python3 engine/revisit.py enqueue-from-ledger`(掃清倉/大減倉 → 30/60/90 佇列,去重、重跑安全)。enqueue 完**再跑一次 `revisit.py scan`**,讀輸出的 **`recent_exits`**(出場 ≤14 天、金額大者先)——這是 Step 2(d) 賣出理由 capture 的候選集(#136:「為什麼賣」只有出場後兩週內問得到,不可回補;空 = 該段靜默跳過)。enqueue 輸出的 `new` 只是「本次新排入」的參考訊號,**capture 候選一律以 `recent_exits` 為準**——上週中斷沒問到的、當週超過限額的,窗口內這裡還會再出現。
- **💵 現金餘額錨點(#171,讓「這筆入金該不該部署」通電)**:交易 CSV 只記部位、記不到帳戶閒置現金——沒有它,`cash_weight` 算不出(引擎降級標不可信,見 Step 1 `honesty_ledger`)。所以 Step 0 順手抓一次**當前現金餘額**:對帳單/持倉頁多半有一行「Cash / 現金 / 可用餘額」——你直接讀出來;讀不到就用 `AskUserQuestion` 問一句「對帳單上的現金餘額大約多少?(想看帳戶層現金比重/入金判讀才需要;略過也能出卡)」。拿到就組 JSON 餵引擎:單一帳戶 `TR_CASH='{"as_of":"<對帳單日期>","amount":<數字>,"currency":"USD"}'`;**台美等多帳戶/多幣別各給一個錨點,用 list**:`TR_CASH='[{"as_of":..,"amount":..,"currency":"USD"},{"as_of":..,"amount":..,"currency":"TWD"}]'`(引擎 per-currency 各算餘額再按匯率聚合;台股帳戶用 TWD)——引擎以錨點為準(對付 CSV 非從開戶完整),其後現金流才疊加。只給部分帳戶的錨點也行:沒給的幣別引擎標盲算,`honesty_ledger` 只揭露缺的那個、邀你補。**入金判讀(`recent_net_deposit`)要看得到存提款流水**:標準化 CSV 時若來源有 deposit/withdrawal/股息/利息/費用列,連同 `Amount` 欄一起留著(格式見 `mock/sample_noisy_broker.csv`),引擎才算得出本期外部淨流入;來源沒有就只靠錨點給比重,判讀那句靜默跳過。
- **snapshot-only(只有快照、還沒有交易紀錄)**:行為診斷跑不了(那需要交易紀錄——誠實講,別硬掰),但出**開帳體檢卡**:用 `holdings` JSON 的成本權重 + 你的世界知識 driver map 講持倉結構(集中度/賽道/sizing,標明「成本基礎」),AI 猜 thesis(Step 2(c))照走,記憶迴圈當場啟動;`integrity` 非空(oversell/壞行)一律如實帶上卡。收尾邀請:「之後把交易紀錄丟給我,攤平/出場/盈虧比這些行為診斷就會解鎖」。
- **帳本誠實檢查**:`holdings` 輸出的 `counts.skipped_lines > 0` = 帳本檔有壞行(可能是中斷寫入)——**如實告訴用戶**、別當帳本完整;修復法就是請他丟一張最新持倉截圖走 reconcile(新錨點蓋過可疑歷史)。(`ledger.py` 純標準庫,不需要 venv——跟 `trade_recap.py` 的 ModuleNotFoundError 提示無關。)
- ⚠️ **過渡期規則**:錨點帶入的持倉 engine 看不到(CSV 無該檔交易),所以 ledger 的 cycle_id 與 engine state 的 cycle_id 可能不同——**theses.jsonl 綁定一律仍照抄 engine state 的 cycle_id**(收尾 part 2 的既有規則,CLI 會驗格式),ledger 的 cycle_id 只供帳本自身追蹤。

### Step 0.5 · 生成 driver map(讓冷門股不失準)

引擎 sector 表只認常見股,冷門股會變「未分類」→ 分散維失真。**你(Claude)對用戶實際持倉用世界知識分類**:每檔 → `[sector, thematic]`,thematic=1 表示跟別檔同屬一個跨產業主題(AI capex / 減重藥 / 太空…)。寫成 JSON `{"PLTR":["軟體雲",1],"CEG":["核電",1],"XOM":["能源",0]}`,用環境變數餵進去:`TR_DRIVER_MAP=/path/driver_map.json python3 engine/trade_recap.py <csv>`。

### Step 1 · 跑引擎,抓大放小

**SKILL 走 JSON 模式拿結構化資料,Step 3 你自己寫卡 ——** 不要照搬 engine 預設輸出(那是 README quickstart 用的乾淨人話卡 / fallback,不是 SKILL 規格那張定論卡):

```bash
mkdir -p ~/.trade-coach
TR_JSON=1 TR_STATE_OUT=~/.trade-coach/last_state.json python3 engine/trade_recap.py <標準化後的CSV>
# TR_JSON=1   → stdout 純 JSON(build_card_data,給你在 Step 3 寫敘事卡用);meta 走 stderr
# TR_STATE_OUT → 寫一份薄 state(對帳用),跟 TR_JSON 平行,可同時設
# TR_PREV_END=<log 最後一筆 date_end> → 對帳模式必帶(#137):問題帳的行為型事件只取其後的
#   新交易(不會把三個月前的舊攤平每週重複入帳);初診不設 = 全期補齊,問題帳統計冷啟動
# TR_CASH='{"as_of":..,"amount":..,"currency":..}'(單帳戶)或 '[{..},{..}]'(台美多帳戶各一錨點) → 現金餘額錨點(#171,Step 0 抓的);設了 cash_weight 才可信,不設引擎降級標不可信
# 都不設 → 印預設人話卡(README quickstart 用)
# TR_DEBUG=1 → 在預設輸出補回 5 維 severity raw 表(開發/驗證用,絕不上卡)
```

> 🔧 **引擎報 `ModuleNotFoundError`(如 pandas / yfinance)**:依賴多半裝在 venv / pyenv 的另一個 python 裡。找到裝了依賴的直譯器路徑重跑一次即可,常見是 repo 根的 `.venv/bin/python3`(README 安裝節的 venv 三行裝出來的)——把上面指令的 `python3` 換成那個路徑;別急著全域 pip(新 macOS 會被 PEP 668 擋)。
引擎吃標準欄位(Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate),`TR_JSON=1` 吐的結構含:
- **`top_holes`**:已選好的 top 1–2 機械洞 + 對應鏡片 quote(融入敘事,**別當結語**)。
- **`candidate_rules`**:2–3 條候選規矩(卡上列候選,**Step 3.5** 讓用戶挑/改一條,**別只給第一條**;引擎只給一條時就用那條)。
- **`thesis_questions`**:per-ticker 持股假設問句 — **這是給 Step 2 對話用的,絕不准印在卡上**(SKILL 鐵律:確認在出卡之前)。
- **`alpha_beta_breakdown` / `payoff_attribution` / `ticker_diagnosis`**:完整數字,你拿去組敘事。
- **`dims_raw`**:5 維行為診斷(每維 severity 0–1)— **別整張攤出來**,用「一句人話」帶過非 headline 的維度(SKILL 鐵律:不放 5 維小數表)。
- **`overview.unrealized_coverage`**:未實現只加總抓得到現價的持倉(`priced_n`/`held_n`/`unpriced`)——讀這欄拿數字,**該不該揭露交給 `honesty_ledger` 統管**(不用自己記何時補)。
- **`cash`**(#171 帳戶現金):`{balance, weight, source, reliable, recent_net_deposit, by_currency}`。`balance`=聚合 USD、`by_currency`=per-幣別原幣明細。`reliable=true`(所有有現金流的幣別都給了 `TR_CASH` 錨點)才把 `weight`+ `recent_net_deposit`(判「這筆錢部署了沒/解不解集中度」)講進卡;`source=partial`(部分帳戶給了、部分沒)或 `csv_sum`(全無錨點)= 靠流水盲算,`weight` 多半 `null`,該不該揭露交 `honesty_ledger`(`cash_reliability.unanchored_currencies` 標哪個幣別缺)。講法照 card-spec「現金與入金判讀」段。
- **`acct_perf`**(#171 帳戶級績效):`{acct_twr, hold_twr, cash_drag, drag_dollar_approx, avg_cash_weight, irr_annual, window, basis, note}`,全部 engine 算好——**只准照抄,不准自己算**(#154 拍板)。`acct_twr` 非 null 才講帳戶級;`{note}` 單鍵或 `acct_twr=null` = gate 掉(現金錨點不可信),只剩 `hold_twr` 持倉柱可用、`note` 說了為什麼。三數字講法與 drag 正負翻譯照 card-spec「帳戶級績效」段;地基缺口該不該揭露交 `honesty_ledger`(`acct_perf_basis`)。
- **`currency_meta`**:聚合幣別與匯率(💱 Display currency 段的資料源)——`aggregate_currency`(overview / what_if / `ticker_diagnosis` 金額等聚合數字的幣別)、`mixed`、`fx`(兌 USD)、`pnl_by_currency`(原幣分桶)、`fx_error`/`alpha_beta_note`。台股/混幣組合寫卡前**先讀這欄**,金額才不會標錯幣;混幣時單檔原幣金額用 `pnl_by_currency` 對照、或由你按 `fx` 反換算。
- **`honesty_ledger`**(#82:誠實點的單一事實源):engine 已聚合好這張卡**必須交代**的誠實缺口清單(空 list = 無缺口),每項 `{key, status, data}`,涵蓋 α 不可信 / 板塊歸因不全 / 未實現缺價 / 未分類 driver / 賣超 / 混幣 / 現金無錨點。**engine 判定「該講什麼」,你只管照 card-spec 的講法融入敘事「怎麼講」**;出卡前逐項核對(Step 3 gate)——取代了散在各欄位「自己記得哪些該揭露」的自律。
- **`pnl_curve`**(#166:總損益從一個點延伸成一張圖):復盤期間累積損益曲線,`{points:[{date,cum_ret}...]}`(起點 `cum_ret` 恆 0、終點對齊 `overview.total_pnl` 那個數)或 `{note:...}`(無價格/樣本不足/混市場尚未支援 → 誠實跳過)。**只在 widget 模式畫成 sparkline**(單色細線,不逐點染色,別重回「多色格子熱力圖」);純文字卡沒有視覺化,`note` 不必轉成一句文字硬補——畫法規格見 card-spec。
- **alpha/beta**:贏大盤多少、其中多少只是「膽子大(高 beta)」、真本事(Jensen's α)剩多少。`excess_split` 把「贏大盤」機械拆成 **押對賽道(allocation)+ 板塊內選股(selection)**,兩項相加恆等於贏大盤 pp——這兩個數是會計恆等式、不需統計顯著,**永遠可講**;`alpha_stat` 給 α 的 95% 區間 / t 值 / 分級(顯著與否),語氣照它走。
  **per-market(混市場組合必讀,#129)**:`alpha_beta_breakdown.scope` 非 null = 組合跨市場,α/β 已按市場分算(US→SPY、TW→台股加權指數),**頂層數字僅含 `scope` 那個市場的部位**——卡上 α/β 段要**兩行並列**(每市場一行,各含資金佔比、各對各的大盤、各自的顯著性語氣),讀 `by_market`;**絕不把兩個市場的 α 加總或平均**(不合成總 α)。台股部位的拆帳 `coverage=0`(無板塊對照、按大盤計)→ 只講「贏/輸台股大盤 X pp」,不拆賽道/選股;`by_market` 內某市場帶 `note`(如 `^TWII` 沒抓到價)→ 該行誠實寫「對照基準抓不到價,本期不判」。單一市場組合 `scope=null`,一切照舊。
- **結構化 state(`TR_STATE_OUT`)**:給對帳用的薄 JSON,讀這幾個欄位 ——
  - `headline_dim` / `headline_metric`:這次最大的洞 +(key, value)。
  - `commitment`:`{rule, metric_key, metric_value, goal}` = **引擎的機械預設承諾**(下次只改這一件 + 追蹤哪個 metric)。**Step 2 動機問完可能推翻它**(實例:engine 給「別加碼」,用戶答「計畫內定投」→ 改盯 `ai_pct`)→ 收尾要存**卡上最終那條**,不是這個預設。對帳比 `metric_key`,別比 headline(規矩維 ≠ headline 維才不對錯帳)。
  - `metrics`:全 metric 快照(`max_pos_pct / avgdown_count / ai_pct / max_sector_pct / top3_pct / payoff / beta / alpha_ann …`),對帳時拿承諾的 `metric_key` 反查新值(集中度承諾就追 `ai_pct`)。
  - `alpha_ann` / `alpha_t` / `alpha_credible`:α **永遠有數,語氣看統計**。`alpha_credible=true`(樣本 ≥1 年且 |t|≥1.96)才可用「真本事」語氣(顯著的負 α 也是可講的定論);`false` → 數字照講但**必帶不確定性**:「α 年化 +X%,但 95% 區間 −Y%~+Z%——統計上還分不出是本事還是運氣」。**卡在哪要講清楚**,引 `alpha_beta_breakdown.alpha_stat.gate.reason`:`sample_short`=不到 1 年 → 才是**樣本不足**;`not_significant`=區間太寬 → 常見原因是**持倉集中、個股雜訊大**(這條跟『最大的洞=集中度』是同一件事,要串起來講——但這是工具的侷限,不是他沒本事)。**贏大盤幾 pp 必配拆帳**:押對賽道 vs 板塊內選股(`excess_split`),`coverage<1` 時補一句「X 檔無板塊對照、按大盤計」。
  - `insufficient_data`:`true`(round-trip<3 或交易跨度<~84 日曆日≈60 交易日)→ **只做體檢、不硬出 commitment**(見開場/收尾)。
  - `problem_events` / `problem_opportunities`(#137 問題帳):本次規約出的問題事件(behavior 型帶交易日與金額;state 型=倉位結構的每週選擇)+ 各類問題「本期有沒有機會犯」快照。**收尾 part 5 原樣 append 進 problems.jsonl**,你只補動機類事件,不改機械類。

**市場背景(#37,跑完主引擎順跑;離線缺席不擋流程)**:

```bash
python3 engine/market_context.py --start <窗口起> --end <state.date_end>
# 窗口:對帳模式 = 上次 log 的 date_end → 這次 state.date_end;初診 = date_end 往前 7 天
```

- 輸出 `benchmarks`:SPY / QQQ 的 `window_ret`(窗口漲跌)+ `ytd_ret`,VIX 的 `last / prev / delta`(水平值,情緒溫度計)。這是**語境,不是診斷**——用在:① 卡開頭的市場背景一行(格式見 card-spec)② 歸因語境:他的動作放進大盤同期的背景講(「你這週砍在 SPY -4% 的恐慌週」)③ Step 2 動機輔助訊號:大漲週進場 = FOMO 候選、大跌週砍倉 = 恐慌候選——**只是輔助你選問誰,不是定性**(定性永遠來自他的回答)。
- **`error` 非 null(離線/未裝)→ 卡上市場背景整段不出**,需要提的話一句「本週缺市場背景(離線)」帶過;**絕不用記憶編大盤數字**。**`missing` 非空(部分家數沒抓到,`error` 可能仍是 null)→ 有什麼講什麼**,缺的那家直接略過、不硬掰——別假設 SPY/QQQ/VIX 三家永遠都在。
- **「你 vs 大盤」只有一個合法數字源 = `alpha_beta_breakdown`** 的 `port_tot`(你的持倉)/`spy_tot`(大盤,US=SPY、TW=^TWII)/`excess_vs_spy`(差 pp):engine 已算好、**只准照抄,不准自己重算**——這一行回答用戶最直白的「我自己選股該不該乾脆無腦買指數」(#164 柱2),講法見 card-spec「該不該買指數」段。市場背景(上面 market_context)的大盤漲跌是語境、**不是對比源**:引擎不算帳戶每週市值序列,別拿它的大盤 window_ret 去心算一個「帳戶那週 +X% vs SPY」(那才是 #37 原本要擋的幻覺)。

**抓大放小鐵律**:只看引擎排在最前面的 1–2 個洞,**其餘忽略**。不要把 5 維全攤給用戶——那就變成另一份報表了。引擎已經幫你收斂,你不要再展開。

### Step 2 · 出卡前的對話確認(持股假設 + 動機)——這層才是鏡片,不可省

**流程鐵律:確認在出卡之前,不在卡上。** 機械算得出「你做了什麼」(what),算不出「你為什麼這樣做」(why)。所以**先在對話裡問完所有需要你定性的問題、拿到答案,Step 3 才出最終卡**——卡是確認後的定論,不是帶問號的待辦。**別把問題做成卡上的按鈕**(那是把 Step 2/3 混在一起)。

**問法鐵律(#55):動機/定性問題一律用 `AskUserQuestion` 工具問,不要寫成文字段落等用戶打字。** 每題二選一(選項裡把兩個動機都寫成人話)+ 用戶可跳過,一次最多 2–3 題,5 秒可點完。自由打字 = 摩擦:用戶會直接略過 Step 2,卡就只能標「待確認」半成品,教練迴圈斷在第一環。只有執行環境沒有 AskUserQuestion 工具(非 Claude Code 的 agent)才退回對話問。

**消重鐵律:答過的不重問(每週被問同一題 = 教練失憶,用戶會走)。** engine 只讀 CSV,不讀記憶——`thesis_questions` 每次都會對同一批標的重新生成,**消重是你(Claude)的責任**:問任何一題之前,先比對 `theses.jsonl`(Step 2.5 重建出的 active thesis)與 `log.jsonl` 最近一筆的動機定性。同一 `cycle_id` 用戶已答過(thesis `maturity=testable`、或上次卡已標凹單/逢低定論)→ **這題不再問**,直接引用舊答案入卡(「上次你說 MSTR 是凹單」);要更新認知走 Step 2.5 對帳的「順手改」,不是重新問卷。**只有三種情況同一標的可以再問**:① 新 cycle(清倉後重建倉)② 行為顯著變了(上次答逢低、這次又深虧加碼 N 次 → 對帳語氣問「還是當初那個理由嗎」,引用他上次的答案)③ 用戶上次跳過(`inferred` 不算答過,但也只在它仍是「金額最大 + 行為矛盾」時才重問一次)。

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

**(c) 建立 / 更新 thesis(AI 猜為主、問為輔 —— 取代週記錄的核心)**

> **鐵律:降摩擦 + 克制。** 這產品的命是「不變成你想逃的重系統」。thesis **絕不逼用戶坐下來填** —— 由你(Claude)從交易行為 + ticker 世界知識**猜**,預設落盤標 `inferred`,用戶不爽再改。讓用戶**冷啟動就有完整 thesis 庫、零填寫成本**。

**主路徑:AI 推測,不問用戶。** 對每個缺 thesis 的持倉,用 engine 行為訊號(`ticker_diagnosis` 的 定投/凹單/押太重/紀律持有 + 加碼次數 + `cur_ret` + 持有天數)+ ticker 是什麼公司 / 賽道,**按五要素結構猜**一筆 thesis(#136:VY 式判斷缺一要素就不算完整;結構是猜的骨架,**不是逼用戶填的表單**——AI 照樣全猜、用戶照樣順手改,摩擦不變):
- **why(判斷)**:猜「**他可能知道什麼還沒被 price in**」,不是複述行為——❌「定投型核心倉」(那是行為,不是理由);✅「賭 AI 推論需求外溢到電力缺口,市場還在按舊供需定價(推測自:規律加碼+長抱核電)」。行為是證據,判斷才是 thesis;**真猜不出判斷(如疑似凹單)→ 誠實寫「攤平等回本(待確認)」且 `horizon` 落 `null`**——編不出判斷就沒有時間軸,別給假 thesis 配假 horizon 讓後續對帳拿去當真。
- **horizon(時間軸,D1)**:這個判斷是**幾週 / 幾季 / 幾年**的事?從行為猜:規律定投 + 長抱 → `年`;押財報 / 事件 → `季`;短進短出 → `週`。沒有時間軸的理由無法對帳——之後「說是三年的事、40 天就跑」這種自相矛盾才抓得到。
- **三 trigger(可證偽退出 + 情境→action,D2/D3;其對賠率的影響接 B1)**:從 ticker 類別猜常見的 —— 成長股 → 營收 / 用戶增速失速;週期股 → 週期反轉;AI 概念 → capex 轉弱。`reduce` 從當前 sizing 猜(已超標 → 該檔減碼線)。
- **stop / target_size(賠率 + 信念→倉位,B1/A1)**:既有欄照猜——最壞情況虧多少、這個理由值多大注。
- **driver(這是不是同一注,B2)**:對照 Step 0.5 你生成的 driver map——與現有持倉同 driver / 同 thematic 的,**why 裡必須點名**(「與 NVDA 同屬 AI capex 一注」),別讓五檔各自漂亮的 thesis 合起來是一注梭哈。
- 每條標來源 `(推測自:規律加碼+長抱)`,讓用戶一眼看出是猜的、好校正。
- **maturity = `inferred`**,全部**直接落盤、零提問**。
- **順猜想法來源(#38 薄版,同樣零提問)**:每筆 thesis 帶 `source_type`(`kol` | `research` | `self` | `other`)+ `source_name` + `source_confidence`。**對話或歷史上下文有明確訊號才標 `kol`/`research`**(用戶這次或先前提過「股癌說」「看了某篇研究」→ `source_name` 填來源名);毫無訊號 → `self` + `source_confidence:"candidate"`(誠實標猜的,別編一個 KOL 出來)。用戶親口確認過才升 `confirmed`。這欄現在只累積、不上卡——等樣本夠了(#38 完整版)才做「自己研究 vs 跟單」勝率分組;**但欄位不可回補,從今天開始收**。
- **順猜進場情緒/信心(#36 薄版,同樣零提問、選填)**:每筆 thesis 順帶猜 `emotion`(`fomo` | `composed` | `forced` | `planned`)+ `confidence`(`high` | `medium` | `low`),各配 `_inferred:true`(AI 猜的、未經用戶確認)。**猜法**:emotion 結合行為訊號 + 進場時機(市場大漲後才追買 / 同賽道已重押還加 = `fomo` 候選;規律定投 / 事件前布局 = `planned`;深虧狂加碼 = `forced`;其餘 = `composed`),confidence 從 why 的語氣推(「基於 Q2 財報」等具體依據 = `high`;「我覺得/賭一把」= `low`;之間 = `medium`)。**若 Step 2 那一問用戶親口透露了情緒/把握(如答「就是怕錯過」)→ 對應欄升 `_inferred:false`**。跟 source_type 一樣**現在只累積、不上卡**(#36 完整版才做「FOMO 進場勝率 vs composed」分組)——**欄位不可回補,從今天開始收**;真的一點訊號都猜不出就整欄留空(null),別硬填。

**只在一種情況問用戶一句**(抓大放小,別審問):
- **行為矛盾、金額最大的那 1 檔**(疑似凹單 / 深虧還加碼)—— 機械分不出「逢低 vs 凹單」,差別只在 why(算不出)。問一句(同樣走 AskUserQuestion,三個選項直接給他點):「{ticker} 加碼 N 次還虧 X%,我猜是不想認賠(凹單)—— 對 / 有新理由(逢低)/ 跳過」。
- **同一次 AskUserQuestion 順帶第二題收來源**(不多一次互動):「{ticker} 這筆的想法最初從哪來?—— 自己研究 / 別人推薦(KOL、朋友——用 Other 填是誰)/ 忘了」。答了 → `source_confidence:"confirmed"`;「忘了」/跳過 → 維持猜的 `candidate`,不追問。
- **一次最多問 1 檔**;其他全用猜的,不打擾。用戶跳過 → 留 `inferred`,不追問。

**校正走「對帳時順手改」,不是「坐下來填」**:對帳(Step 2.5)呈現猜的 thesis + trigger 觸發,用戶看到猜歪的**順手改一條** → 該 thesis 升 `testable`(用戶確認過)。明說「投機跟風沒 thesis」→ 標 `draft`。thesis 越用越準,但從不逼填。

**鐵律不變:`exit_trigger`(看錯了,事實)≠ `stop`(跌多少賣,價格)。** 猜的時候 exit 也猜「thesis 失效的事實」,不是猜停損價。寧可 `inferred` 也不要假的 `testable`。

**(d) 賣出理由 capture(#136)—— 出場當週唯一的收集窗口,錯過不可回補**

買入的 why 有 thesis 承接,**賣出的 why 目前只活在對話裡**——這段把它落盤,30/60/90 出場追蹤(Step 2.5)才有「你當時自己說的理由」可對答案。

- **觸發**:Step 0 enqueue 後那次 `revisit.py scan` 輸出的 **`recent_exits`**(引擎已按出場 ≤14 天過濾 + 金額排序;初診匯入的更早歷史出場天然不在裡面,不會冒出十筆舊出場拷問)。空 → 整段靜默跳過,不提。
- **消重(重跑安全)**:對每筆候選,先比對 `theses.jsonl` 既有的 `event:"exit_narrative"` 行——同 `revisit_id` 已有記錄(含 `capture:"skipped"`)→ 不重問。**同一 ticker 同日多筆出場**(先減倉後清倉 → 佇列兩筆)→ 只問最終那筆(`full` 優先),另一筆落 `capture:"skipped"` 消重,別對同檔同天問兩次。
- **一次最多問 2 筆**(候選已金額大者先)。**沒問到的不落盤**——它們留在 `recent_exits`,窗口內下次 session 補問;窗口過了就自然消失(誠實缺資料,不編)。只有「問了但用戶跳過」才落 `capture:"skipped"`(跳過=他選擇不答,窗口內重問=追問,違反不逼填)。
- **問法(AskUserQuestion,一筆一題,四分法選項寫成人話 + 帶他的真實數字)**。盈虧數字從 engine state 的 `ticker_diagnosis` 拿,拿不到就省略,**別自己算**;減倉比例 = `shares_sold / shares_before`。
  - `kind:"full"`(清倉):「{ticker} 你 {exit_date} 在 {exit_price} 全部出清。當時賣的理由是——**到價了**(當初設的目標走完)/ **看錯了**(thesis 的失效條件發生)/ **換更好的**(把錢挪去 {swaps 的 ticker,無 swap 則寫「別的標的」})/ **想落袋**(怕回吐、想鎖住獲利)」+ 可跳過。
  - `kind:"reduce"`(減倉 ≥50%):同四分法但措辭對齊「還留著一半」的事實——「**到了減碼點**(計畫內的部位調整)/ **信心動搖**(thesis 部分失效,先降風險)/ **換更好的**(騰資金去 {…})/ **想落袋**(鎖住一部分,怕回吐)」。落盤的 `exit_reason` 仍用同一組值(`price_target`/`thesis_broken`/`swap`/`anxiety`)——風控降倉、再平衡這類「都不是」→ 用戶點 Other 寫原話,`exit_reason` 落 `null` + `note` 存他的話。
  - 前二=紀律,後一=焦慮訊號——但**問的當下不說教**,這是 capture 不是審判,定性留給 30/60/90 對答案。
  - **時間軸自相矛盾必帶(#136)**:對 `horizon.py scan` 標 `exit_too_fast` 的該 cycle(門檻 deterministic 住 engine——見 Step 2.5 重建段的 scan;你不再自己算天數 / 比閾值)→ 問句補一句鏡子:「你當初說這是{horizon}的事,{marker 的 `holding_days`}天就走——是判斷變了,還是心態動了?」(thesis `inferred` 時措辭改「我當時猜這是{horizon}的事」)。engine 已對 `horizon` 缺欄 / `null` 自動跳過——別回頭從舊 why 腦補一個 horizon 出來。這正是 horizon 欄存在的理由:沒有時間軸,理由無法對帳。
- **賣出動機只有一種情況可以猜**:`swaps` 非空 → 猜 `swap`(標 `capture:"inferred"`,對答案時措辭用「我當時猜你是換標的」)。其餘(到價 vs 落袋、證偽 vs 恐慌)全是內心狀態、機械分不出——**用戶沒答就落 `exit_reason: null` + `capture:"skipped"`,絕不編**(有 swap 交易事實撐的才敢猜,沒有事實的猜測=替用戶編賣出動機,比不記還糟)。
- **落盤**:跟 thesis 一起在收尾 part 2 統一 append 進 `theses.jsonl`(格式見該段 exit_narrative 範例),`exit_reason` ∈ `price_target` | `thesis_broken` | `swap` | `anxiety` | `null`,`note` 存他用 Other 補的原話(若有)。

### Step 2.5 · 對帳上次的 thesis 與承諾(只在對帳模式 / log 非空)

**先重建「目前有效的 thesis」(append-only 讀取必做,否則 active 名單會爆掉)**:`theses.jsonl` 是 append-only,同一 thesis 有多筆 revision。讀取時按 `thesis_id` 建 event log,**每個 cycle 只取 latest 未被 supersede 的**:
- 後出現的 `revises: <舊 id>` → 把舊 id 標 superseded、排除。
- cycle 已清倉(該 `cycle_id` 不在 engine `holdings.positions`)→ 該 thesis 標 closed、不進對帳(歷史保留)。
- **`event:"exit_narrative"` 的行不是 thesis revision**——跳過、不進 active 重建;它是出場敘事(Step 2(d) 落的「當時為什麼賣」),只在出場追蹤對答案時按 `revisit_id` 撈。
- 結果 = 每個 active cycle 恰一筆有效 thesis。
- **重建完跑 `python3 engine/horizon.py scan <active_theses.json> --as-of <date_end>`** 取時間軸觸線標記:active_theses 每筆帶 `cycle_id` + `horizon`,清倉的那筆另帶 `exit_date`(= 該 cycle 在 `recent_exits` 的出場日)。engine 回 `exit_too_fast`(清倉太快)/ `held_too_long`(抱太久),各帶 `holding_days`。**門檻(deterministic)住 engine,你不再自己算持有天數、不眼球比閾值;`horizon` 缺欄 / `null` / 非三值 engine 自動跳過**。這批標記供 Step 2(d) 賣出 capture(`exit_too_fast`)與下面 trigger 檢查(`held_too_long`)共用——同一次 scan,別各算一遍。

出新卡先回看上次:
1. **承諾 metric**:上次 `commitment.metric_key` 舊值 → 這次 engine state 新值(「上次說壓到 20%,當時 51% → 現在 48%:在降、沒達標」)。
2. **trigger 檢查 —— 只查三類,別逐檔掃(逐檔掃 = 把復盤變研究任務 = 回到高級拖延)**:
   - 只查:**本週有交易的 ticker** + **上次承諾關聯的 ticker** + **最大風險 1 檔**。其餘 active thesis 標「本週未檢查」。**外部新聞 / 基本面查是 opt-in**(用戶說要才查,不每週必跑)。
   - 對這幾檔看 trigger 觸發,**措辭依 maturity 分(最關鍵 —— 別把 AI 猜的當你的承諾)**:
     - **`testable`(你確認過的)** → 才用定論:`exit_trigger` 觸發 = 🔴「你定的『{exit}』發生了 —— thesis broken,該走」。
     - **`inferred`(AI 猜的)** → **只能用問句,絕不說「該走」**:🟡「我**猜**的失效條件『{exit}』似乎發生了 —— 這符合你當初買的邏輯嗎?符合 → 考慮出場;不符 → 順手改成你真正的 exit」。`inferred` 一律帶 `[⚠️ AI 猜測待校正]` 標。
   - `review_trigger` 觸發 → 提示重看,不催賣。
   - **順帶看 horizon 反向矛盾(只對這三類 ticker,零額外掃描)**:對 `horizon.py scan` 標 `held_too_long` 的 cycle(門檻 deterministic 住 engine,同一次 scan 的輸出;engine 已對 `horizon` 缺欄 / `null` 自動跳過)→ 一句鏡子:「當初說是{horizon}的事,現在持有 {marker 的 `holding_days`}天——是判斷升級成長線了(順手改 horizon),還是不想認賠變長抱?」措辭同樣依 maturity 分(`inferred` 用「我猜」)。**這題受消重鐵律管**:答完立刻把結論落盤(revises——改 horizon,或 why 標凹單定性)→ 矛盾要嘛消失、要嘛已定性,**同一 cycle 不重問**;**跳過也視同答過**(本 cycle 不再追,別把鏡子變成每週催告);只有行為又顯著變了(定性凹單後又加碼)才照消重鐵律的例外重開。
3. **出場追蹤(#32/#33,開場 `revisit.py scan` 的 `due` 非空才有這段;空 = 靜默跳過,不催)**:
   - **問之前先撈當時的賣出理由**:比對 `theses.jsonl` 的 `event:"exit_narrative"`(同 `revisit_id`)。**有記錄且 `exit_reason` 非空 → 問句必須引用他自己的話對答案**(#136 閉環,這比泛用問句锋利十倍),按 `exit_reason` 客製:`thesis_broken`→「你賣時說是**看錯了**——{orig_ret:+pp} 之後,當時說的失效條件真的發生了嗎?」;`price_target`→「你賣時說**到價了**——它之後又走了 {orig_ret:+pp},是目標定低,還是紀律就該這樣?」;`anxiety`→「你賣時說**想落袋**(怕回吐)——回頭看,那個回吐{發生了嗎}?」;`swap`→ 直接用下面的 swap framing;`capture:"inferred"`(當時是猜的)→ 措辭改「我當時猜你是{理由}」。**無記錄或 `exit_reason` 為空**(舊出場/當時跳過)→ 泛用問句如下。
   - 每筆 due 用 AskUserQuestion 問一題:「{ticker} 你 {exit_date} 在 {exit_price} 賣掉,現在 {現價}(賣後 {orig_ret:+pp})。當時賣的理由現在看——**還成立**(賣早也是紀律)/ **部分對,要調**/ **看錯了**(真錯,進教訓)?」三選項對應 `still_valid / modified / falsified`,可跳過(下次 due 再問)。
   - **swap framing 必講(#33 鐵律)**:`compare.swap_net_pp` 非 null → 賣飛必對位換入——「賣飛 +X pp,但你換進 {swap ticker} 同期 {swap_ret:+pp} → swap 淨 {net:+pp}」;**只有換入輸給原標的才算真錯,別只算賣早多少**。`idle_cash=true` → 「賣後 cash 閒置,機會成本 = 原標的續漲 X pp」。`needs_prices` 非空 → 把缺的 ticker 現價補進 `--prices` 再算(用 engine state 的 last_px,都缺就標「本週缺價,不判」)。
   - 用戶答完立刻落盤:`python3 engine/revisit.py resolve <revisit_id> <30|60|90> <status> --note "<他的一句話>"`;`falsified` 的當下把那句話帶進卡的教訓段(這就是 mistakes log 的最小形)。
   - 卡上的「出場追蹤」小節**只在有 due 時出現**,一筆一行,不攤成報表。
4. **問題帳對位(#137,開場 `problems.py stats` 的輸出;還沒開帳 = 整段跳過)**:
   - `rules_check` 有 `verdict:"broke"` 的規矩 → **破戒定性問句**(AskUserQuestion 一鍵,這是規矩層唯一的主觀判斷入口):「『{規矩人話}』這次破了({事件證據})——**守不住**(記一筆,繼續追)/ **這條定得不合理**(該修的是規矩不是你)/ **這次是例外**(有正當理由)?」三個出口:守不住 = 事件照記(預設);定得不合理 = 當場請他改一句 → 收尾寫 `revises` 進 rules.jsonl(演變線,同 thesis);例外 = 把他的理由寫進該事件的 `note`(事件仍在帳上,呈現時帶語境)。**一次最多問 2 條**(broke 的照 top 排序);同一規矩連續多週 broke,只在第一次和趨勢惡化時問,其餘一行帶過——別把定性變成每週審判。
   - `held_streak ≥ 2` 的規矩 → **靜默**(注意力調度:連兩期守住就退出卡面,再犯自動回來;這不是畢業,統計一直在跑)。`verdict:"skipped"`(本期沒機會犯)→ 不提也不算守住。
   - `muted_rules` → 完全不提(用戶說過別追了;統計仍在,他哪天要看隨時有)。
5. 對帳完才講本週新洞(headline)。**只收斂一個洞 + 一條規矩**,別把每筆 thesis 攤成報表。

### Step 3 · 出一張卡(收斂鐵律)——拿到 Step 2 答案後才出

**🚦 出卡前 self-check(沒過一律不准出卡)**:
1. **engine 用 `TR_JSON=1` 跑過了嗎?** 拿到的是 `build_card_data()` 結構化 JSON,不是預設那張人話卡。
2. **Step 2 對話完成了嗎?** — `thesis_questions` 至少對「金額最大 + 行為矛盾」的 1 檔問過 + 拿到答案;主要動機鏡片(對應 headline_dim 的)問過 1 句。沒問完就出卡 = 退化成「engine + 套版」,失去 SKILL 的價值。
3. **你打算自己用敘事寫卡,不是照搬 JSON 欄位?** 把 JSON 當資料源,自己組句子,不要列 `〔X〕內容` 的 dashboard 拼接。
4. **`honesty_ledger` 每項都在卡面交代到了嗎?**(#82) 非空清單裡每個誠實缺口,卡面敘事都要有對應人話(講法見 card-spec);漏一項 → 補上再出。**ledger 本身不上卡、不列成表**,它只是你出卡前的核對源。
5. **圖形環境試過 `show_widget` 了嗎?**(engine 標不到的執行層事實)沒實際呼叫過就別直接寫文字卡當唯一交付——先試渲染,失敗才降級文字(判斷見 card-spec 呈現方式段)。

**三項都過了,才讀 [card-spec.md](card-spec.md),照裡面的規格出卡**——卡的結構、禁止清單、private/public 兩種卡與 redact 規則、敘事鐵律、處方層全在那份檔裡,這裡不重複。
**Step 2 還沒問完,不要提前打開它**:在那之前,你唯一的目標是把動機問完、拿到答案。

### Step 3.5 · 規矩收斂:讓用戶挑一條,存進記憶(不可省——這是下次對帳的入口)

卡上列的 2–3 條候選規矩**不是結局**。出完卡立刻用 AskUserQuestion(選項 = 各候選 + **「這週不承諾」**,Other 可改寫)問一句:**「選一條當下週對帳的承諾?會存進本機 log,下次開場第一句就對它:說到有沒有做到。」**(#56:你不准代選,他點了哪條才存哪條。)

- **選項標籤 = 規矩短語**,description 寫「下週看哪個數 + 現在的值」——**一律人話,內部 metric key 不准出現在任何用戶看得到的文字裡**(真人反饋:「追蹤 max_pos_pct,本週基線 42%」= 拗口)。✅「下週就看:最大單注佔比,現在 42%」/ ❌「追蹤 max_pos_pct,基線 42%」。用戶要能 5 秒選完。
- **metric_key 對映(log 存內部名,顯示用人話)**:單一標的佔比 → `max_pos_pct`(人話「最大單注佔比」);虧損加碼 → `avgdown_count`(「虧損加碼次數」);賽道集中 → `ai_pct`(「同賽道佔比」);板塊 → `max_sector_pct`(「最大板塊佔比」);盈虧比 → `payoff`。對帳比 metric,不比 headline(規矩維 ≠ headline 維才不對錯帳)。
- **用戶挑完 → 立刻走下面收尾 CLI 落盤**(`coach.py close`),`--rule` / `--metric` 填他選(或改寫)的那條。
- **`insufficient_data=true` 時的分工**:機械預設 commitment 照舊**不出**(引擎已設 null,別把缺資料的猜測當承諾);但**用戶自己選的規矩照存**——行為承諾是他的意志,跟樣本夠不夠無關;樣本不足影響的只是 metric 基線的解讀。落盤時標 `source: "user_chosen"` + `baseline_note: "short-sample baseline"`,下次對帳措辭看**方向**(在降/沒動/變糟),不判達標。
- 用戶選「這週不承諾」/ 跳過 → 收尾 `coach.py close --rule SKIP`:log 照存本週 metrics(供趨勢對帳),commitment=null,下週不拿規矩對他。

### Step 4 · 收一句反饋(驗證用)

出完卡,問一句:**「這張卡,有戳中你嗎?還是哪裡不對?」** 這句反饋(純文字、不含交易明細)是這個 skill 唯一要回收的東西,用來驗證「這面鏡片產出的卡對別人有沒有用」。

收到反饋後,給他**一個可點的回收入口**(自願、只提一次、不推銷):

> 願意的話,把這句感想貼給作者(30 秒):
> https://github.com/atomchung/fomo-kernel/issues/new?template=card-feedback.yml
> ⚠️ 只貼感想,**別貼任何交易明細**——表單也會再提醒一次。

用戶不想貼就算了,照常走收尾;反饋本身已經進了他自己的教練迴圈,回收只是 bonus。

**收尾埋回訪鉤子(#52 · 每週迴圈的接續點,別讓卡收完就斷)**:反饋收完、狀態落盤後,用**最後一句**把下週的錨點交回用戶手上——引用他這次**真實選的規矩**(Step 3.5 的 commitment 人話),不是模板話:
> 「下週帶新的對帳單回來(**整份全歷史直接丟就好,重疊期會自動去重**),我第一句先對『{這次承諾的規矩人話}』守了沒。」

- **這句同時解掉「第二週該匯什麼」的困惑**(留存生死關):明確講「全歷史直接丟」,用戶才不會卡在「要不要只匯增量」。措辭與 README「每週怎麼用」一致。
- 選「這週不承諾」(SKIP)→ 改一句不綁規矩的:「下週把新的對帳單丟回來(全歷史直接丟),我接著看趨勢往哪走。」
- **test-drive 模式**:這句改講「真實使用時,下週回來我就先對帳這條規矩」(對齊 Step 1 鐵律——假資料不落盤,不能假裝有記憶)。

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
3. 卡**第一句**就對帳:`上次說要{rule 白話},當時{metric 人話}={舊值} → 現在 {新值}:{在降/沒動/變糟}{達標沒}`(例:「上次說逢低加碼要有頂,當時最大單注 42% → 現在 31%:在降」)。用戶的數字、白話、**metric key 內部名不上卡**(人話對映見 Step 3.5)。commitment 帶 `source:"user_chosen"` → 措辭用「**你上次自己選的規矩**」(這是他的承諾,不是系統派的);帶 `baseline_note:"short-sample baseline"` → 只講方向(在降/沒動/變糟),不判達標。
4. **變化摘要(log ≥2 筆時,對帳行之後補一小段「跟上週比」)**:取 log 最近兩筆 `metrics_snapshot`,挑**變化最大的 3 個** metric 用人話講(對映表同 Step 3.5):「AI 暴險 78%→71% 在降;最大單注 42%→45% 反而變重;攤平 +0 次」。**只講 3 個、一行帶過,不攤全表**(那就變 dashboard 了);缺值(None)的 metric 跳過。這一小段是「它記得我」的第二個證據——第二張卡的價值在進度,不在再算一次。
5. **再**講新一輪的洞(headline_dim)——若跟上次同維,直說「這條還沒過關,先別開新戰場」;若是新維,才開新洞。永遠只收斂一個洞 + 一條規矩。

**規矩承諾:用戶主動選,你不准代選(#56)。** 挑規矩的互動走 **Step 3.5**(AskUserQuestion:候選各一 + 「這週不承諾」,Other 可改寫)。**用戶沒點選之前,任何規矩都不准寫進 log** —— 承諾是下週對帳的錨點,錨不是他自己下的,對帳時他只會一頭霧水、迴圈失效。選「這週不承諾」→ 收尾 CLI `--rule SKIP`(照存本週 metrics 供趨勢對帳,但 commitment 為空、下週不拿規矩對他)。

**收尾(出完卡 + Step 3.5 用戶挑完規矩 + Step 4 收完反饋,append 一行)**:
```bash
# commitment 存【用戶在 Step 3.5 親選的那條】(#56)——不是引擎機械預設、更不是你代選
# (Step 2 動機問完常推翻引擎預設:engine 給「虧損別加碼」,用戶答「計畫內定投」→ 他改挑集中度那條)。
# 用戶選「這週不承諾」→ --rule SKIP。gate 規則在 CLI 內(#148):SKIP 一律不存 commitment;
# insufficient_data 只擋機械預設、不擋親選(親選自動補 short-sample 基線註記);--metric 填錯 key 直接拒收。
python3 engine/coach.py close --rule "AI 暴險封頂 70%:要加 AI 新倉先問新賽道還是同一注往上疊" --metric ai_pct
```

**收尾 part 2 · 把本週建立 / 更新的 thesis append 到 `theses.jsonl`(append-only)**:
thesis 是對話 articulate 出來的(engine 不碰)。把本週「新建倉 / 缺 thesis / trigger 觸發後更新」的 thesis 與 Step 2(d) 的賣出敘事寫成**一個陣列**存暫存 JSON(如 `/tmp/theses.json`,空週傳 `[]` 也行),交給 CLI 落盤——cycle_id 格式、必填欄驗證、`thesis_id`/`narrative_id` 生成、`session_date` 注入全在 CLI 內(#148),格式不合**整批拒收(0 筆落盤)**,照 stderr 修完重跑:

```bash
python3 engine/coach.py append-theses /tmp/theses.json --session-date <state.date_end>
```

兩種行的格式(⚠️ `cycle_id` 必須【照抄】engine state `holdings.positions[ticker].cycle_id` 的 3 段格式如 `"NVDA#2024-01-12#1"`——自己拼 2 段會被 CLI 拒收;當初的坑 = 2 段讓對帳永不匹配、每週把寫過 thesis 的持倉當缺 thesis 重問):

```json
[
  {"ticker":"NVDA","cycle_id":"NVDA#2024-01-12#1",
   "why":"一句:還沒被 price in 的判斷(不是行為描述;driver=B2 嵌這裡,同注要點名,不另立欄)",
   "horizon":"年",
   "triggers":{"review":"什麼消息/數字該重看","reduce":"什麼情況減碼","exit":"什麼代表看錯(非股價跌)"},
   "maturity":"inferred",
   "stop":"", "target_size":"20%",
   "source_type":"self", "source_name":null, "source_confidence":"candidate",
   "emotion":"composed", "emotion_inferred":true, "confidence":"high", "confidence_inferred":true,
   "revises":null},
  {"event":"exit_narrative","ticker":"NVDA","cycle_id":"NVDA#2024-01-12#1",
   "revisit_id":"NVDA#2026-07-01#40.0",
   "exit_date":"2026-07-01", "exit_reason":"thesis_broken",
   "capture":"user", "note":null}
]
```

欄位語意——thesis 行:`horizon` 週|季|年(這個判斷是多長的事,#136 五要素 D1);`maturity` `inferred`(AI 猜,預設)|`testable`(用戶確認過)|`draft`(投機跟風沒 thesis);`source_type` kol|research|self|other(#38 薄版;`source_name` 只在 kol/research 填,`source_confidence` candidate|confirmed);`emotion` fomo|composed|forced|planned + `confidence` high|medium|low(#36,**選填**,inference-first,各配 `_inferred` 旗標;**現在只累積、不上卡**,同 source_type——樣本夠了才做「FOMO 進場勝率 vs composed」分組,但欄位不可回補、從今天開始收);更新既有 thesis 用 `revises` 指回舊 `thesis_id`,不蓋舊的。exit_narrative 行:`revisit_id` 照抄 enqueue-from-ledger 輸出 `new[]` 的 key(對答案用);`exit_reason` price_target|thesis_broken|swap|anxiety|null(跳過);`capture` user(親答)|inferred(僅 swap 可猜)|skipped(跳過,消重用);`note` 存用戶 Other 補的原話;**絕不帶 why/triggers**。
> `theses.jsonl` 是 append-only 動機庫:**只追加、不改不刪**。清倉**不刪** thesis(留著當歷史);下次同 ticker 重建倉 = 新 `cycle_id` = 新 thesis。`exit_narrative` 事件(賣出理由)也住這個檔——買入的 why 和賣出的 why 同一本帳,30/60/90 對答案按 `revisit_id` 撈。Step 2.5 對帳讀每筆 active thesis 的 trigger 檢查觸發 + horizon 時間軸矛盾。**隱私同 log:純本機、不外傳、不回作者。**

**收尾 part 3 · 個人 profile(只第一次建,當復盤對照基準)**:`~/.trade-coach/profile.md` 不存在 → 第一次從交易行為**猜** 3 條個人原則寫進去(同 inference-first:不逼填,用戶可改):持有風格(長抱 / 短打)、集中度傾向、紀律缺口(出場 / 加碼)。例:`1. 長期持有型(中位 X 天)　2. 易重押單一賽道(AI X%)　3. 弱點在出場擇時(賣完常續漲)`。之後每週對帳順帶一句「這批交易符合你定的原則嗎」,用戶要改直接改檔。

**收尾 part 4 · 卡片落盤(歷史卡片庫,#129 PR-4)**:出完卡把**最終卡全文**(private review 版)寫進 `~/.trade-coach/cards/<date_end>.md`,頂部 YAML frontmatter:

```markdown
---
date: <state.date_end>
headline_dim: <這次的洞>
commitment: <Step 3.5 用戶親選的規矩原文;沒選填 null>
metric_key: <對應追蹤 metric;null>
feedback: <Step 4 用戶那句反饋;null>
---
<卡全文照貼>
```

卡全文(含 frontmatter)寫進暫存檔後交 CLI 落盤——同日重跑的檔名遞增(`<date>-2.md`,**不蓋舊卡**)由 CLI 管(#148),別自己算檔名:

```bash
python3 engine/coach.py save-card /tmp/card.md --date <state.date_end>
```

這個資料夾 = 你的復盤語料庫——歷史可回看,也是日後「蒸餾你自己的鏡片」的原料。同隱私鐵律:純本機、不外傳、不回作者。

**收尾 part 5 · 問題帳 + 規矩庫落盤(#137)**:

```bash
# (a) 問題事件入帳:engine 規約的機械類原樣進、你只補動機類;去重靠 problems.py,重跑安全。
python3 - <<'PY'
import json, os, subprocess, sys
st = json.load(open(os.path.expanduser("~/.trade-coach/last_state.json")))
events = list(st.get("problem_events") or [])
# 動機類事件(engine 看不到動機——Step 2 拿到答案的才補,沒有就留空;絕不猜):
#   exit_anxiety  — Step 2(d) 答「想落袋」的每筆:{"key":"exit_anxiety","kind":"behavior",
#                   "week":"<exit_date>","ticker":"NVDA","amount":None,"note":"賣出理由=想落袋"}
#   horizon_break — horizon 矛盾且答「心態動了/不想認賠」:week=本次 date_end
#   fomo_entry    — market_context 大漲週(如 SPY 週漲 >3%)新建倉、動機答「怕錯過」:week=建倉日
mark = {"week": st["date_end"], "opportunities": dict(st.get("problem_opportunities") or {})}
# horizon_break 的機會 engine 判不了(它不讀動機庫)——由你補:有帶 horizon 的 active thesis = True
# mark["opportunities"]["horizon_break"] = True
import tempfile
fd, tmp = tempfile.mkstemp(suffix=".json")            # unique 暫存,別用固定路徑(並行 session 會互蓋)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(events, f, ensure_ascii=False)
r = subprocess.run([sys.executable, "engine/problems.py", "append", tmp,
                    "--mark", json.dumps(mark, ensure_ascii=False)], capture_output=True, text=True)
os.unlink(tmp)
print((r.stdout or r.stderr).strip())
PY

# (b) 規矩庫沉澱(只有「新規矩 / 修訂 / 靜音」才 append;同一條繼續守 = 不寫,庫裡已有):
# 寫成陣列存暫存 JSON 後交 CLI(#148)——metric_key→problem_key 對映、rule_id 生成、status/created
# 預設都在 CLI 內。行格式:
#   {"text":"<Step 3.5 選定的規矩人話>","metric_key":"ai_pct","source":"user_chosen","revises":null}
#   · source ∈ user_chosen | imported(冷啟動匯入,見下)
#   · 破戒定性答「定得不合理」→ 填舊 rule_id 進 revises + 改後文字;「這條別追了」→ 補一筆 status:"muted" + revises 舊 id
#   · 無 metric 對位的問題(hold_inconsistency / exit_anxiety / horizon_break / fomo_entry)→ 手填 problem_key
python3 engine/coach.py append-rules /tmp/rules.json --created <state.date_end>
```

> **冷啟動匯入**:`rules.jsonl` 不存在、且用戶自己維護過規矩清單(如他的 RULES.md)→ 邀請一次:「把你現有的規矩貼給我,我翻成可對位的格式」——你逐條翻成 `{text, problem_key}`(對不上機械 key 的 `problem_key: None`,只當人話清單陳列)、**他過目確認後**才落盤(`source:"imported"`)。匯入的是**他本機的資料**,照隱私鐵律留在本機。
> 規矩庫是未來 pre-trade gate 的守則檔:**全部 tracking 的規矩都是守則**(沒有「畢業」門檻)——還在犯的那條,恰恰是下單前最該擋你的。

**第一次樣本不足(`insufficient_data=true`)**:round-trip<3 或交易跨度<~84 日曆日(≈60 交易日),引擎已把 `commitment` 設成 `null`。**機械層只做體檢、不硬出規矩**(否則下次把缺資料的猜測當成已確認的承諾來對帳)。但 **Step 3.5 照走**:用戶自己挑的規矩照存(`source:"user_chosen"` + `baseline_note`,gate 在 `coach.py close` 內)——體檢卡也要留下記憶入口,否則第二週還是初診。卡收尾講一句「資料還太短,基線先存個底,累積多幾筆 round-trip 後對帳才看達標」;用戶跳過不選 → log append(commitment=null),下次來就接得上。

> 驗收這套有沒有真的「記憶」:`engine/test_state_loop.py` 把一份 CSV 按時間切兩段,累積跑「初診→對帳」,驗第二張卡有沒有真的對帳第一張承諾的那一維(而非重新初診)。改完 engine 或這段流程都先跑它。
