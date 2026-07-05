# 交易風格測試用例(sample fixtures)

**虛構**交易紀錄,各自模擬一種投資者畫像(風格 × 持有長度),用來測 engine 的 5 維診斷能不能把每種風格**最該被照出的洞**排到復盤卡最前面。和 `mock_trades.csv`(方法論建立期那個人)並列,都是假資料、可入 git。

目前共 **12 組**:三組散戶風格基準(fundamental / momentum / value)+ 四組投資者畫像擴充(ai_holder / oldecon / swing / day_trader,2026-06-30 經 Claude+Codex+Gemini 三方 review 定稿,見下方「投資者畫像擴充」)+ 五組 engine 邊界情境擴充(pyramid / insufficient / noisy_broker / rotator / panic_seller,2026-07-04,見下方「engine 邊界情境擴充」)。

範圍限定:全部是**現股 long-only**(BUY/SELL),不含選擇權/賣空——engine 本身只認 `RecordType=="Trade"` 且 `Action in ("BUY","SELL")`([trade_recap.py:105-108](../engine/trade_recap.py#L105)),沒有做空/選擇權的計算邏輯,造這類 fixture 對回歸沒有增益。

## 怎麼跑

每組附一個 `driver_map.json`(SKILL Step 0.5:讓 engine 對實際持倉用正確 sector/主題分類,冷門股不失準),用環境變數餵進去:

```bash
cd skills/fomo-kernel
TR_DRIVER_MAP=mock/sample_fundamental.driver_map.json python3 engine/trade_recap.py mock/sample_fundamental.csv
TR_DRIVER_MAP=mock/sample_momentum.driver_map.json    python3 engine/trade_recap.py mock/sample_momentum.csv
TR_DRIVER_MAP=mock/sample_value.driver_map.json       python3 engine/trade_recap.py mock/sample_value.csv
# 投資者畫像擴充
TR_DRIVER_MAP=mock/sample_ai_holder.driver_map.json   python3 engine/trade_recap.py mock/sample_ai_holder.csv
TR_DRIVER_MAP=mock/sample_oldecon.driver_map.json     python3 engine/trade_recap.py mock/sample_oldecon.csv
TR_DRIVER_MAP=mock/sample_swing.driver_map.json       python3 engine/trade_recap.py mock/sample_swing.csv
TR_DRIVER_MAP=mock/sample_day_trader.driver_map.json  python3 engine/trade_recap.py mock/sample_day_trader.csv
# engine 邊界情境擴充
TR_DRIVER_MAP=mock/sample_pyramid.driver_map.json      python3 engine/trade_recap.py mock/sample_pyramid.csv
TR_DRIVER_MAP=mock/sample_insufficient.driver_map.json python3 engine/trade_recap.py mock/sample_insufficient.csv
TR_DRIVER_MAP=mock/sample_noisy_broker.driver_map.json python3 engine/trade_recap.py mock/sample_noisy_broker.csv
TR_DRIVER_MAP=mock/sample_rotator.driver_map.json      python3 engine/trade_recap.py mock/sample_rotator.csv
TR_DRIVER_MAP=mock/sample_panic_seller.driver_map.json python3 engine/trade_recap.py mock/sample_panic_seller.csv
```

> ⚠️ 數字會漂移:engine 用 yfinance 抓**真實歷史價 + 最新收盤**算 α/β、市值權重、套牢。標的代碼與日期都真實(2023–2024),所以重跑時絕對數字會隨當前股價變,但**每組設計觸發的「頭號洞」是穩定的**(由交易行為決定,不靠特定股價)。

## 這些 CSV 的角色:測試 fixture,不是 demo 模式(#89)

engine 已移除 `is_demo` 檔名嗅探(#89):**輸入路徑含不含 `mock` 都走同一條 call**,同一份輸入任何人跑都得到同一結果,輸出沒有任何 demo 分支。

- **persona 模擬(測「真實用戶會看到什麼」)**:CSV 放哪都行,卡面 = 真實形態。測試元信息(狀態隔離到哪、這是模擬)只留在**對話層**跟作者講,一個字都不准上卡:卡上出現作者視角 = 模擬穿幫,測不到真實體驗。
- **給沒資料的用戶體驗 = 呈現層的事**:靜態長相 → README 範例卡(#46);想走一遍流程 → SKILL「試駕模式」(#53):`mock_trades.csv` 走完整四步,但 Step 2 標明演練、狀態只進 temp 不碰 `~/.trade-coach/`、卡標「示範」——失真警告由呈現層扛,引擎不設任何 demo 分支(#89)。

## 三組設計意圖

| 檔案 | 模擬風格 | 行為設計 | engine 應排第一的洞 | 對應鏡片動機問句 |
|---|---|---|---|---|
| `sample_fundamental.csv` | **基本面選股** | 跨 6 產業真分散(醫療/消費/金融/能源/科技/工業)、單筆 ≤18% 不梭哈、賺一點就賣好公司、賠錢的基本面股死抱等回本 | **出場紀律**(處置缺口 +258 天:賺錢抱 ~120 天就跑、賠錢抱 ~378 天)、β≈0.6 低波動 | winner 賣太早 / 賠錢死抱 → D1 時間軸、G1 焦慮 vs 判斷 |
| `sample_momentum.csv` | **動能衝衝衝** | 全押 AI/半導體、單檔梭哈、4~18 天短進短出、追熱門題材 | **部位 sizing**(單檔 >40%)+ **假分散**(AI 暴險 100%、同一 driver)、β≈2.2 把 beta 當 alpha | 梭哈 → B1 賠率/A1 sizing;假分散 → B2 driver;贏大盤靠賽道 → E2 beta vs alpha |
| `sample_value.csv` | **只買便宜估值** | 越跌越攤平(INTC 49→20、CVS、PYPL)、套牢死抱不認賠、只實現小賺(CVX/F) | **加碼攤平**(6 次虧損加碼、5 次破 25% 上限)+ **部位 sizing**(凹單把 INTC 養成 43% 重倉) | 虧損中加碼 → A2 試探≠加碼、G 不想認賠:「INTC 從 45 一路加到 20,是看好還是不想認賠?」 |

## 設計重點(為什麼這樣造)

- **每組只讓「一種洞」壓倒性勝出**,其餘維度刻意守住,確保 engine 的「抓大放小」排序選對。例:基本面組部位/分散/攤平全綠,只有出場紀律 sev=1.00。
- **真實標的 + 真實日期**:這樣 yfinance 才抓得到價,出場紀律(賣出後續漲)、α/β 歸因、套牢才算得出來——這幾維是引擎的價值核心,不能用假代碼跳過。
- **避開拆股/退市的失真標的**:早期版本用過 SMCI(2024 拆股)、WBA(2024 退市)會讓「套牢 -96%」「404」這種假訊號污染診斷,已換成 AMAT/MRVL、CVS。**NVDA(day_trader/momentum 兩組)是例外保留**:AI 分散度/主題暴險敘事需要它,拆股前(2024-06-10 之前)交易改填當時真實名目價位(如 $830-950 區間),讓 `adjust_for_splits()` 事後調整算對,而非誤填今日拆股後等值價造成雙重縮放(issue #93)。`mock_trades.csv` 的 NVDA 同屬此例外,但更特殊——它是**跨拆股長倉**(拆股前 2024-01-12 / 03-11 建倉、拆股後 08-06 賣、12-03 加碼):兩筆建倉填名目價 `15@$500`、`35@$620`,線上 `adjust_for_splits()` 換回今日等值 `150@$50`、`350@$62` 後正確(#98 只修了 day_trader/momentum、漏此檔,後補)。惟跨拆股長倉在**離線**(無 yfinance / 抓價失敗 → `splits={}` 不調整)時,拆股前名目買價會與拆股後等值賣價尺度錯配,NVDA round-trip 被翻成假虧——注意 engine **無卡級『無價格』旗標**攔截(僅依賴日線的 α/β 等維會各自標無價格),已實現損益、盈虧比、出場紀律維全部照跑、靜默吸收假虧,另生成 150 股假 `orphan_sells`(頭號洞倒仍穩在 sizing+分散、不隨之跑掉)。此為 FIFO 賣出 clamp 對跨拆股長倉在降級定價下的固有性質、非本檔特有 bug,線上 demo 主路徑(10:1 拆股正確套用)不受影響;但**離線跑 mock_trades 的損益與行為診斷不可信、勿據以判讀**。
- **估值組的連貫敘事**:凹單(加碼攤平)直接導致 INTC 變成 43% 重倉(部位失控)——一條因果線串起兩個洞,正是 value trap 的死亡螺旋,不是兩個獨立缺陷。

## 預期鏡片復盤卡一句話(人話版)

- **基本面**:「你買得好(α 正、低 β、真分散),但賺錢的抱 120 天就跑、賠錢的抱 378 天等回本——處置效應在替你做決定。」
- **動能**:「你贏大盤 +119pp,但 β 2.2、AI 暴險 100%、單檔 41%——你押對的是賽道不是選股,而且一次回檔 30% 就 -$18k。」
- **估值**:「你 6 次往虧損倉加碼、把 INTC 凹成 43% 重倉——便宜不是買進理由,『不想認賠想攤低等回本』才是。」

---

# 投資者畫像擴充(2026-06-30)

在三組散戶風格基準之上,再造四組**投資者畫像**——刻意用「風格 × 持有長度」拉開光譜:
從長抱一年半的 AI 信徒,到同日進出的當沖客。設計經 **Claude + Codex + Gemini 三方 review** 兩輪定稿
(第一輪審引擎機制、第二輪審策略真實度),Codex 實際跑 engine 驗算頭號洞排序、標的價格對齊 2024 真實區間。

## 四型畫像

| 檔案 | 投資者畫像 | 持有長度 | 行為設計 | engine 應排第一的洞 | 對應鏡片動機問句 |
|---|---|---|---|---|---|
| `sample_ai_holder.csv` | **AI 長期投資者** | 約 1.5 年(526–623 天) | 2023 起重押 AI 龍頭(NVDA/MSFT/GOOGL/AVGO/PLTR/TSM)、長抱、漲著順勢加碼、偶爾長抱後減碼 | **分散**(假分散:AI 暴險 100%、單一敘事);次要洞=NVDA 重倉 33% | 「你以為買 6 檔很分散,其實都押同一個 AI 敘事——一起漲一起跌,一次 AI 寒冬就是 −50%」 |
| `sample_oldecon.csv` | **傳統產業投資者** | 數月 | 只買老經濟(能源/金融/工業/必需消費/公用/醫療)、跨 6 sector 真分散、低 β、不梭哈、不攤平、賺賠持有期相近 | **(無洞)→ 揚長卡** | 紀律乾淨基準:照不出洞時卡片該怎麼講「你守住了什麼」 |
| `sample_swing.csv` | **快進快出(短波段)** | 不一致(2–45 天) | 同一檔有時 2–3 天快停利(賺就跑)、有時套住就凹成 40+ 天(賠的拖著) | **持有時間**(框架不一致 incon_rate=1.0);次要洞=出場紀律 | 「同一檔 SHOP,你有時抱 3 天有時抱 44 天——你到底是短打還是長線?沒有框架就沒有紀律」 |
| `sample_day_trader.csv` | **當沖交易者** | 0 天(同日進出) | 同日 BUY+SELL、多檔輪動、賺賠當天結(stylized:單日 1–2 筆,示意當沖框架非真實 HFT 頻率) | **持有時間**(過度交易:中位持有 0 天) | 「中位持有 0 天、14 筆同日沖——這個頻率需要的 edge,你的勝率撐得起嗎?還是在繳手續費」 |

> **動能者**已由既有 `sample_momentum.csv` 涵蓋(全押 AI/半導體、單檔梭哈、4–18 天短進短出、頭號洞=部位 sizing),不重造。
> 五型畫像 = ai_holder(長線信徒) + oldecon(保守長抱) + swing(波段) + day_trader(當沖) + momentum(動能)。

## 設計重點(為什麼這樣造 / 三方 review 的關鍵發現)

- **頭號洞必須由「與最新股價無關」的訊號決定**(driver flag / 成本基礎權重 / 純日期),才能離線確定性測試、
  不因 yfinance 即時價漂移而 flaky。各型的回歸斷言見 `tests/test_sample_styles.py`。
- **AI 長期 vs 動能同樣 AI 暴險,但靠持有長度區隔**:AI 長期是長抱(median >200 天、hold 維守綠)+ 主題集中(分散當頭號);
  動能是短進短出(median <15)+ 單檔梭哈(sizing 當頭號)。
- **ai_holder 線上頭號洞會在「分散↔sizing」間漂移,但同屬 AI 過度集中**:離線測試(成本基礎)斷言 **分散**;
  但線上跑時 NVDA 自 2023 大漲 ~8 倍 → 市值佔比膨脹到 ~73% → 卡片可能改以 **部位 sizing** 當頭號。
  兩者講的是同一課(身家壓在單一 AI 敘事),故事不變;回歸測試以確定性的「分散」為準。
- **`top3` 不進 severity**(Codex 跑碼證實):分散維的 severity 只看 `max(max_sector_pct, ai_pct)`。
  所以 ai_holder 必須 **ai_pct≈1.0** 才排得上頭號,光靠 top3 紅燈分數趨近 0。
- **sizing vs 分散 的臨界 = max_pct 0.41**(且平手時 dims 順序讓 sizing 先贏):ai_holder 的龍頭股
  刻意控在 **33%**(<0.41),確保「分散」(0.70 分)穩穩壓過「sizing」(0.43 分)當頭號。
- **AI 長期需 ≥3 筆已實現 round trip**:engine 在 `len(round_trips) < 3` 時標 `insufficient`、不出 commitment。
  所以這型安排了長抱後的減碼(NVDA/TSM/PLTR/AVGO),既講「長線信徒」又讓 engine 完整運作。
- **swing 不能靠「賣太早」當頭號**:`winner_early`/`avg_forgone` 需 yfinance,離線測不出 → 改用
  **「持有時間框架不一致」**(同檔又 <5d 又 >30d)當穩定頭號,離線可斷言。
- **當沖機械路徑**(Codex 驗證):同日 BUY 行排在 SELL 行前 → `round_trips` 配成 hold=0;當天全平倉 →
  `held` 為空 → sizing/分散/攤平全失效,只剩「持有時間 overtrading」當穩定頭號。
- **dedup 陷阱**:`load()` 去重鍵 = `(symbol, side, qty, price, date)`。當沖/高頻同日重複腿必須讓
  qty 或 price 有差異,否則交易會被默默吃掉——本 fixture 已讓每筆買賣價不同。
- **傳統產業 = 乾淨基準**:刻意讓五維全綠,補上既有 fixture 全缺的覆蓋——「沒有洞時卡片走揚長路徑」。
  與既有 `sample_fundamental`(同樣老牌穩健但頭號=出場紀律)互補,避免兩者撞同一頭號洞。
- **第二輪策略審修正**(2026-07-01,三方審策略真實度後):① 標的成交價全部對齊 2024 真實歷史區間
  (yfinance 複查 ≤±12%)——原 TSLA/SHOP/UBER 偏離 20–48% 已修;② `SQ` 已改名退市(Block→XYZ,
  yfinance 抓無資料)→ 換成 `PYPL`,遵守「避開拆股/退市失真標的」原則;③ oldecon 補第 6 個 sector
  (醫療 JNJ)讓「跨 6 sector」名實相符;④ swing 的長抱(40+ 天)全部改為虧損出場,讓「賺的快跑、
  賠的拖著」在資料上成立(原本 SHOP 長抱那筆是賺錢,敘事對不上);⑤ 誠實標注 day_trader 是
  stylized(單日 1–2 筆)、非真實 HFT 頻率。

## 預期鏡片復盤卡一句話(人話版)

- **AI 長期**:「你信 AI 信了一年半也賺了一年半,但你 6 檔全是同一個敘事、AI 暴險 100%——這不是分散,是一注押更大。」
- **傳統產業**:「沒有洞要照——你跨 6 個產業、最重一檔 16%、賺賠都抱得住。守住紀律本身就是答案。」
- **快進快出**:「同一檔 SHOP 你抱過 3 天也抱過 44 天——賺的快跑、賠的拖著,你缺的不是進出點是時間框架。」
- **當沖**:「14 筆同日進出、中位持有 0 天——先問自己:這個頻率需要的 edge,你真的有嗎?」

---

# engine 邊界情境擴充(2026-07-04)

前兩批都在造「畫像」——真人交易者的行為模式。這批不再造新畫像,改造 **engine 判斷分支本身**的邊界情境:
每一支既有鐵律(A-10 樣本不足 / 攤平 vs 加碼贏家的方向判斷 / CSV 雜訊過濾)都該有一組 fixture 專門去戳它,
而不是只能等真人交易者剛好撞上才發現。範圍限定現股 long-only(見上方「範圍限定」)。

| 檔案 | 模擬情境 | 行為設計 | 測的 engine 分支 | 回歸斷言 |
|---|---|---|---|---|
| `sample_pyramid.csv` | **金字塔加碼者** | 只在浮盈時加碼(COST 550→600→650 一路買高、UNH 480→520),從未在虧損時加碼,最終部位轉重倉 | `dim_avgdown` 必須靠「買價 < 均價×0.9」判斷方向,不能把「越漲越加碼」誤判成攤平 | `tests/test_sample_styles.py::test_pyramid_top_hole_is_sizing_not_avgdown` — 頭號洞=部位 sizing、`avgdown.count==0`、`classify_adds` 分類為疑似定投而非疑似凹單 |
| `sample_insufficient.csv` | **樣本不足者** | 只有 2 個 round-trip(AAPL、MSFT 各一組買賣),交易跨度 41 天(<`MIN_SPAN_DAYS`=84) | `build_state()` 的 `insufficient = len(rts) < 3 or span_days < MIN_SPAN_DAYS` gate | `test_insufficient_sample_blocks_commitment` — 直接跑 `build_state()` 驗證 `insufficient_data=True` 且 `commitment=None`(對應 eval-design.md A-10) |
| `sample_noisy_broker.csv` | **CSV 雜訊版** | 複製 `sample_oldecon.csv` 的交易列,插入股息/轉帳/利息/帳戶手續費/股息再投資等非典型列(`RecordType` 非 Trade,或 `Action="REINVEST"`) | `load()` 的兩道過濾:`RecordType!="Trade"` 續行、`Action not in (BUY,SELL)` 續行 | `test_noisy_broker_csv_matches_clean_baseline` — 解析後應與乾淨版 `oldecon` 五維結果逐一比對相同(差分斷言,雜訊必須被完全濾除) |
| `sample_rotator.csv` | **輪動追熱點者** | 依序全倉重壓 4 個不同熱門賽道(AI半導體→生技→能源→電動車),每個都在 30–40 天內清倉才換下一個,最後重壓最新熱點(金融科技) | `dim_size`/`dim_diversify` 在單一持倉快照下天然觸發集中;真正的區隔訊號是 round-trip 標的的 driver **逐輪全換不重複**,對照 momentum 的『同一 driver 反覆押注』 | `test_rotator_top_hole_is_sizing_via_theme_churn` — 頭號洞=部位 sizing、持有天數落在 20–60 天(介於 momentum 的 <15 與 ai_holder 的 >200 之間)、4 個 round-trip 的 driver 兩兩不同 |
| `sample_panic_seller.csv` | **恐慌全出者** | 3 檔虧損倉長抱 500+ 天,某個真實有過大盤重挫的那週(2024-08-05~07)同時全數認賠出清;幾個月後追高買回其中一檔 | `dim_exit` 的處置缺口公式在『多檔同週恐慌出清』下天然放大到極端值;`disp_gap` 沒有專門的『恐慌同步』訊號,靠日期群聚(plain check)佐證 | `test_panic_seller_extreme_disposition_and_chase_back` — 頭號洞=出場紀律、處置缺口 >300 天(比 fundamental 的 +258 天更極端)、3 檔虧損倉出清日期落在 ≤5 天窗口內、同一檔追高買回價格 >恐慌賣出價 105% |

## 設計重點

- **這批不是新畫像,是新故障模式**:三支都對應一條既有鐵律的「反例」——如果沒特意造,這些分支只能等真人資料剛好踩到才會被驗證到,而 A-10 這種 gate 條件本來就很少在既有 7 組畫像的正常交易量下被觸發。
- **金字塔 vs 攤平的方向對照**:`sample_value.csv`(既有)是「往虧損倉加碼」,`sample_pyramid.csv`(新)是「往獲利倉加碼」——同樣是「多次加碼同一檔」的交易表面模式,但 engine 的 `dim_avgdown` 只認買價相對均價的方向,不能靠加碼次數本身判斷,兩組刻意對照確保這個方向判斷沒有被次數污染。
- **`sample_insufficient.csv` 直接跑 `build_state()`,不只斷言 gate 條件成立**:先前 fixture 都只驗 `dim_*` 純函式,這組因為要測的是 `insufficient_data`/`commitment` 這兩個只存在於 `build_state()` 輸出的欄位,測試因此改為直接呼叫 `build_state()` 全鏈路,而非只驗證 `len(rts)<3` 這個中間條件本身。
- **`sample_noisy_broker.csv` 用差分斷言而非獨立斷言**:不另外斷言「頭號洞是什麼」,而是要求跟乾淨版 `oldecon` 逐維 diff 相同——這樣任何未來對雜訊列的誤判(哪怕只影響一維的 severity 小數點),都會在差分裡現形,比各自獨立斷言更敏感。
- **`sample_rotator.csv` 頭號洞跟 momentum 撞維,靠序列訊號(不是 dim 本身)區分**:engine 的 5 維只看「當下持倉快照」,追熱點者清倉重壓下一個賽道後,快照必然是單一新持倉(集中度天然滿分)——跟 momentum 表面同一種頭號洞形狀。真正的行為差異(每次都換賽道 vs 從頭到尾同一賽道)沒有專屬 dim,測試改為直接檢查 round-trip 標的的 driver 序列有沒有重複,這是刻意留在 fixture/測試層的訊號,不是逼 engine 生出新維度。
- **`sample_panic_seller.csv` 是 fundamental 處置效應的極端版,加兩個 engine 沒有專屬 dim 的訊號**:① 多檔虧損倉在同一週同步出清(恐慌的特徵是『同步』,不是『個股別考量後賣出』,但 `dim_exit` 只看賣出後的持有天數分布,量不到『是不是同一週』)——用交易日期直接檢查窗口寬度佐證;② 賣飛之後追高買回同一檔(『賣在恐慌低點、買回追高點』的雙重行為錯置)——用同檔前後兩筆買入價格比對佐證。兩者都刻意寫在測試而非新增 engine 邏輯,對齊本次任務範圍(豐富測試用例,不是擴充 engine 功能)。
- **五支都經 mutation 驗活**:分別故意弄壞 `insufficient` gate、`dim_avgdown` 的 0.90 閾值、`RecordType` 過濾、`dim_exit` 的 severity 公式、`driver()` 分類函式,確認對應測試真的會亮紅,才收進回歸(見 repo 一貫的「鐵則=先探測真實輸出+全綠後跑突變測試證明非假綠燈」)。
