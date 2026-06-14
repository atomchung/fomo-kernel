# trade-review

> 一個 Claude Code skill:用 **Vincent Yu(余鎮文)的交易鏡片**,把你的真實交易復盤成**一張卡**——
> 你做對的一件事 + 一個最大的洞(用你自己的數字)+ 一條下次要守的規矩 + 一句大師的話。

不是又一份統計報表。它做的是報表做不到的事:**先算出你看不見的行為漏洞,再問出你不願承認的動機,最後逼你下次只改一件事。**

---

## 它跟「貼對帳單給 ChatGPT」差在哪

ChatGPT 算不出 FIFO 配對的真實 α/β、分不清你是「定投」還是「凹單」、也沒有你的歷史。這個 skill 三層遞進:

1. **機械層(Python,確定性精算)** — 算 ChatGPT 估不準的東西:
   - 5 維行為診斷:部位 sizing / 加碼攤平 / 出場 / 分散 / 持有一致性
   - **標的層診斷**:按**金額**排序每檔(小倉不糾結),主從分類器分「疑似定投 vs 疑似凹單 vs 待確認」
   - **報酬歸因**:把「贏大盤」拆成「押對賽道(運氣/方向)」vs「選股(技巧)」——讓你看清賺的是本事還是膽子
2. **鏡片層(VY × 對話)** — 機械分不出的「為什麼」,出卡前問你:
   - 持股假設:「MSTR 一路加碼還虧,是還相信 thesis,還是不想認賠在凹單?」
   - 動機:「賣掉賺錢的賣太早,是 thesis 到價,還是怕回吐?」
   - **機械挑該問的少數標的,你的答案定性**——機械永遠在猜,你一句話定案
3. **處方層** — 從「你哪裡爛」進到「下一步換什麼做法」:揚長(放大你的 edge)/ 砍損耗(機械規則,下次可驗)

→ 最後收斂成**一張卡**,一個洞、一條下次能驗的規矩。第二次來,先對帳「上次那條守了沒」。

## 🔒 隱私:你的交易不離開你的電腦

- skill 在**你自己的機器**上跑你的 CSV,資料不上傳、不外傳、不寫進記憶。
- 作者拿不到你的交易明細。唯一(自願)回收的是一句「這張卡有沒有用」,不含交易內容。
- `.gitignore` 已設:**任何 `.csv` 都不會被 commit**,只有 mock/sample 假資料例外。

## 安裝

需要 Python 3.11+:
```bash
pip install -r requirements.txt           # yfinance + pandas
```
把 skill 掛進 Claude Code(二選一):
```bash
ln -s "$(pwd)/skills/trade-review" ~/.claude/skills/trade-review   # A. symlink(推薦)
cp -r skills/trade-review ~/.claude/skills/                         # B. 複製(給別人用)
```

## 用法

在 Claude Code 裡:
```
/trade-review                      # 沒給資料 → 跑內建 mock,看一次完整 demo
/trade-review ~/Downloads/my.csv   # 復盤你自己的交易
```
你的 CSV 來自**任何券商**都行——Claude 會自動讀懂、轉成引擎要的欄位(`Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate`),不必你手動整理。

**會發生什麼**:① 引擎跑診斷 → ② Claude 在對話裡問你 1–3 個持股假設/動機問題(逢低還是凹單?)→ ③ 拿到你的答案,出一張定論卡。卡的版型見 [`card-template.html`](skills/trade-review/card-template.html)(完整四層的 HTML 範例)。

## 三組風格 sample(直接可跑,看不同風格照出不同洞)

`mock/` 下有四組**虛構**交易,各觸發一種典型洞。完整設計見 [`mock/SAMPLES.md`](skills/trade-review/mock/SAMPLES.md):

```bash
cd skills/trade-review
TR_DRIVER_MAP=mock/sample_fundamental.driver_map.json python3 engine/trade_recap.py mock/sample_fundamental.csv
TR_DRIVER_MAP=mock/sample_momentum.driver_map.json    python3 engine/trade_recap.py mock/sample_momentum.csv
TR_DRIVER_MAP=mock/sample_value.driver_map.json       python3 engine/trade_recap.py mock/sample_value.csv
python3 engine/trade_recap.py                          # 不帶參數 = mock_trades.csv
```

| sample | 風格 | 該照出的頭號洞 |
|---|---|---|
| `sample_fundamental` | 基本面選股 | 出場紀律(賺錢抱 120 天就跑、賠錢抱 378 天等回本) |
| `sample_momentum` | 動能衝衝衝 | 部位梭哈 + 假分散(把 beta 當 alpha) |
| `sample_value` | 只買便宜 | 加碼攤平(越跌越凹,把 INTC 凹成 43% 重倉) |
| `mock_trades` | 方法論建立期 | FOMO 全 AI 假分散 + PLTR 攤平 |

> ⚠️ 引擎用 yfinance 抓真實歷史價算 α/β、市值、套牢,**重跑時絕對數字會隨當前股價漂移**;但每組設計觸發的頭號洞是穩定的(由交易行為決定,不靠特定股價)。

## 結構

```
skills/trade-review/
  SKILL.md                  ← skill 本體(四步流程:格式 → 引擎 → 出卡前確認 → 定論卡)
  engine/trade_recap.py     ← 機械層:5 維 + 標的層主從分類 + 歸因(純函式,無真實路徑)
  rubric/
    vincent-yu.md           ← VY 鏡片原文蒸餾(逐條標出處)
    vincent-yu.lens.json    ← 鏡片的「可換大師層」:規矩/引言/動機問句(換大師 = 換這檔)
  behavior-diagnosis.md     ← 診斷哲學:對事不對人、行為多標籤(why 的設計記錄)
  card-template.html        ← 復盤卡 HTML 版型範例
  mock/                     ← 四組 sample 假資料 + 各自 driver map + SAMPLES.md
```

## 免責

鏡片來自 VY 公開文章的原則蒸餾(逐條標來源),屬引用非轉載、非經本人背書。
本工具定位 **research / coaching support**,所有輸出僅為交易行為回顧與紀律建議,**不構成投資建議、不涉及任何標的買賣推薦**;最終投資決策與結果由使用者自負。
