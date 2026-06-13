# trade-review

> 一個 Claude Code skill:用 **Vincent Yu(余鎮文)的交易鏡片**,把你的真實交易復盤成一張卡——
> **一個最大的洞 + 一條下次要守的規矩 + 一句大師的話。**

機械算負責**抓大放小**(只挑最大的行為漏洞),VY 鏡片負責**找動機**(問出那筆交易背後你不願承認的原因)。

---

## 為什麼這樣設計

復盤工具最大的問題是:**算得出「你做了什麼」(賣太早、攤平、梭哈),算不出「你為什麼這樣做」**——而後者才是重複犯錯的根源。

所以這個 skill 分兩層:
- **機械層(Python)**:純算 5 維行為診斷(假分散 / 梭哈 / 攤平 / 賣太早 / 把 beta 當 alpha),收斂成 1–2 個最大的洞。這層是普世行為金融,誰來都一樣。
- **鏡片層(VY + 對話)**:對最該問的那幾筆交易,用 VY 的思路問你一個二選一的「動機問題」(看好還是不想認賠?判斷還是手癢?)。**這層才是 Vincent Yu**,不是貼個名字。

## 🔒 隱私:你的交易不離開你的電腦

- skill 在**你自己的機器**上跑你的 CSV,資料不上傳、不外傳。
- 作者(或任何人)拿不到你的交易明細。要回收的只有一句:**「這張卡有沒有用」**——你自願給,不含任何交易內容。
- `.gitignore` 已設定:**任何 `.csv` 都不會被 commit**,只有 mock 假資料例外。

## 安裝

需要 Python 3.11+。先裝依賴:
```bash
pip install -r requirements.txt
```

把 skill 掛進 Claude Code(二選一):
```bash
# A. symlink(推薦,改了會即時生效)
ln -s "$(pwd)/skills/trade-review" ~/.claude/skills/trade-review

# B. 複製(給別人用)
cp -r skills/trade-review ~/.claude/skills/
```

## 用法

在 Claude Code 裡:
```
/trade-review                      # 沒給資料 → 跑內建 mock,看一次 demo
/trade-review ~/Downloads/my.csv   # 復盤你自己的交易
```

CSV 只需要通用欄位:`Symbol` / `Action`(BUY|SELL)/ `Quantity` / `Price` / `TradeDate`。
(目前對齊 Firstrade 匯出格式;其他券商欄位名不同的話,先轉成這幾欄。)

## Demo:mock 裡那個人是誰

`mock/mock_trades.csv` 是一個虛構的「方法論建立期散戶」,2024 年的典型故事:
- FOMO 進 AI,4 檔 98% 全是 AI(**以為自己分散**)
- PLTR 從 24 套到 15 一路加碼(**攤平、佔到 48%**)
- 賺錢的賣太早(71% 賣完繼續漲)

跑 `/trade-review` 會照出這幾個洞,然後問他:「PLTR 一路加,是看好還是不想認賠?」——這就是 VY 找動機那層。
> mock 的 alpha/beta 數字會失真(資料太少),demo 時忽略,真實資料才看。

## 結構

```
skills/trade-review/
  SKILL.md                  ← skill 本體(四步工作流程 + VY 動機單元對照表)
  engine/trade_recap.py     ← 機械層:5 維行為診斷 + alpha/beta(純函式,無真實路徑)
  rubric/vincent-yu.md      ← VY 鏡片:公開文章原則蒸餾,逐條標出處
  mock/mock_trades.csv      ← demo 假資料
```

## 免責

鏡片來自 VY 公開文章的原則蒸餾(逐條標來源),屬引用非轉載、非經本人背書。
本工具定位 research / coaching support,所有輸出僅為交易行為回顧,不構成投資建議;買賣決策與結果由使用者自負。
