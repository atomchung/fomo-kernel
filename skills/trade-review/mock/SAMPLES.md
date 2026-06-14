# 交易風格測試用例(sample fixtures)

三組**虛構**交易紀錄,各自模擬一種常見散戶風格,用來測 engine 的 5 維診斷能不能把每種風格**最該被照出的洞**排到復盤卡最前面。和 `mock_trades.csv`(方法論建立期那個人)並列,都是假資料、可入 git。

## 怎麼跑

每組附一個 `driver_map.json`(SKILL Step 0.5:讓 engine 對實際持倉用正確 sector/主題分類,冷門股不失準),用環境變數餵進去:

```bash
cd skills/trade-review
TR_DRIVER_MAP=mock/sample_fundamental.driver_map.json python3 engine/trade_recap.py mock/sample_fundamental.csv
TR_DRIVER_MAP=mock/sample_momentum.driver_map.json    python3 engine/trade_recap.py mock/sample_momentum.csv
TR_DRIVER_MAP=mock/sample_value.driver_map.json       python3 engine/trade_recap.py mock/sample_value.csv
```

> ⚠️ 數字會漂移:engine 用 yfinance 抓**真實歷史價 + 最新收盤**算 α/β、市值權重、套牢。標的代碼與日期都真實(2023–2024),所以重跑時絕對數字會隨當前股價變,但**每組設計觸發的「頭號洞」是穩定的**(由交易行為決定,不靠特定股價)。

## 三組設計意圖

| 檔案 | 模擬風格 | 行為設計 | engine 應排第一的洞 | 對應 VY 動機問句 |
|---|---|---|---|---|
| `sample_fundamental.csv` | **基本面選股** | 跨 6 產業真分散(醫療/消費/金融/能源/科技/工業)、單筆 ≤18% 不梭哈、賺一點就賣好公司、賠錢的基本面股死抱等回本 | **出場紀律**(處置缺口 +258 天:賺錢抱 ~120 天就跑、賠錢抱 ~378 天)、β≈0.6 低波動 | winner 賣太早 / 賠錢死抱 → D1 時間軸、G1 焦慮 vs 判斷 |
| `sample_momentum.csv` | **動能衝衝衝** | 全押 AI/半導體、單檔梭哈、4~18 天短進短出、追熱門題材 | **部位 sizing**(單檔 >40%)+ **假分散**(AI 暴險 100%、同一 driver)、β≈2.2 把 beta 當 alpha | 梭哈 → B1 賠率/A1 sizing;假分散 → B2 driver;贏大盤靠賽道 → E2 beta vs alpha |
| `sample_value.csv` | **只買便宜估值** | 越跌越攤平(INTC 49→20、CVS、PYPL)、套牢死抱不認賠、只實現小賺(CVX/F) | **加碼攤平**(6 次虧損加碼、5 次破 25% 上限)+ **部位 sizing**(凹單把 INTC 養成 43% 重倉) | 虧損中加碼 → A2 試探≠加碼、G 不想認賠:「INTC 從 45 一路加到 20,是看好還是不想認賠?」 |

## 設計重點(為什麼這樣造)

- **每組只讓「一種洞」壓倒性勝出**,其餘維度刻意守住,確保 engine 的「抓大放小」排序選對。例:基本面組部位/分散/攤平全綠,只有出場紀律 sev=1.00。
- **真實標的 + 真實日期**:這樣 yfinance 才抓得到價,出場紀律(賣出後續漲)、α/β 歸因、套牢才算得出來——這幾維是引擎的價值核心,不能用假代碼跳過。
- **避開拆股/退市的失真標的**:早期版本用過 SMCI(2024 拆股)、WBA(2024 退市)會讓「套牢 -96%」「404」這種假訊號污染診斷,已換成 AMAT/MRVL、CVS。
- **估值組的連貫敘事**:凹單(加碼攤平)直接導致 INTC 變成 43% 重倉(部位失控)——一條因果線串起兩個洞,正是 value trap 的死亡螺旋,不是兩個獨立缺陷。

## 預期 VY 復盤卡一句話(人話版)

- **基本面**:「你買得好(α 正、低 β、真分散),但賺錢的抱 120 天就跑、賠錢的抱 378 天等回本——處置效應在替你做決定。」
- **動能**:「你贏大盤 +119pp,但 β 2.2、AI 暴險 100%、單檔 41%——你押對的是賽道不是選股,而且一次回檔 30% 就 -$18k。」
- **估值**:「你 6 次往虧損倉加碼、把 INTC 凹成 43% 重倉——便宜不是買進理由,『不想認賠想攤低等回本』才是。」
