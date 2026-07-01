# 交易風格測試用例(sample fixtures)

**虛構**交易紀錄,各自模擬一種投資者畫像(風格 × 持有長度),用來測 engine 的 5 維診斷能不能把每種風格**最該被照出的洞**排到復盤卡最前面。和 `mock_trades.csv`(方法論建立期那個人)並列,都是假資料、可入 git。

目前共 **7 組**:三組散戶風格基準(fundamental / momentum / value)+ 四組投資者畫像擴充(ai_holder / oldecon / swing / day_trader,2026-06-30 經 Claude+Codex+Gemini 三方 review 定稿,見下方「投資者畫像擴充」)。

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
```

> ⚠️ 數字會漂移:engine 用 yfinance 抓**真實歷史價 + 最新收盤**算 α/β、市值權重、套牢。標的代碼與日期都真實(2023–2024),所以重跑時絕對數字會隨當前股價變,但**每組設計觸發的「頭號洞」是穩定的**(由交易行為決定,不靠特定股價)。

## 三組設計意圖

| 檔案 | 模擬風格 | 行為設計 | engine 應排第一的洞 | 對應鏡片動機問句 |
|---|---|---|---|---|
| `sample_fundamental.csv` | **基本面選股** | 跨 6 產業真分散(醫療/消費/金融/能源/科技/工業)、單筆 ≤18% 不梭哈、賺一點就賣好公司、賠錢的基本面股死抱等回本 | **出場紀律**(處置缺口 +258 天:賺錢抱 ~120 天就跑、賠錢抱 ~378 天)、β≈0.6 低波動 | winner 賣太早 / 賠錢死抱 → D1 時間軸、G1 焦慮 vs 判斷 |
| `sample_momentum.csv` | **動能衝衝衝** | 全押 AI/半導體、單檔梭哈、4~18 天短進短出、追熱門題材 | **部位 sizing**(單檔 >40%)+ **假分散**(AI 暴險 100%、同一 driver)、β≈2.2 把 beta 當 alpha | 梭哈 → B1 賠率/A1 sizing;假分散 → B2 driver;贏大盤靠賽道 → E2 beta vs alpha |
| `sample_value.csv` | **只買便宜估值** | 越跌越攤平(INTC 49→20、CVS、PYPL)、套牢死抱不認賠、只實現小賺(CVX/F) | **加碼攤平**(6 次虧損加碼、5 次破 25% 上限)+ **部位 sizing**(凹單把 INTC 養成 43% 重倉) | 虧損中加碼 → A2 試探≠加碼、G 不想認賠:「INTC 從 45 一路加到 20,是看好還是不想認賠?」 |

## 設計重點(為什麼這樣造)

- **每組只讓「一種洞」壓倒性勝出**,其餘維度刻意守住,確保 engine 的「抓大放小」排序選對。例:基本面組部位/分散/攤平全綠,只有出場紀律 sev=1.00。
- **真實標的 + 真實日期**:這樣 yfinance 才抓得到價,出場紀律(賣出後續漲)、α/β 歸因、套牢才算得出來——這幾維是引擎的價值核心,不能用假代碼跳過。
- **避開拆股/退市的失真標的**:早期版本用過 SMCI(2024 拆股)、WBA(2024 退市)會讓「套牢 -96%」「404」這種假訊號污染診斷,已換成 AMAT/MRVL、CVS。
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
