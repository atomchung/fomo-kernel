# FOMO Kernel

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2.svg)](skills/fomo-kernel)
[![Engine: Deterministic](https://img.shields.io/badge/Engine-Deterministic-green.svg)](skills/fomo-kernel/engine)

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> **一個直接、以證據為界、在本機執行的交易決策夥伴。** 把你正在考慮的交易，或已經做過的交易帶進來。FOMO Kernel 會降低你的決策負擔，但不替你做最後決定。

它服務兩個核心時刻：

- **交易前：** 先看這筆交易會如何改變目前記錄的持倉，再挑戰你現在出手的理由。
- **交易後：** 從行為找出最值得處理的一件事，親自選一條下次可驗的規則。

數字、排名、組合影響與狀態轉換由確定性 Python 引擎負責。Agent 只處理程式無法替你決定的有限判斷：你的動機、最強反方論點，以及直接說清楚「現在真正重要的是什麼」。

## 從你現在的時刻開始

| 你現在遇到的事 | 最少需要提供 | 第一個有用結果 |
|---|---|---|
| **「我該買、加碼、減碼，還是先不動？」** | 預計動作、目前理由，以及現在有什麼改變 | 已有記錄持倉時：精確的交易後權重、持倉之間隱藏的集中／重疊、現金影響、規則衝突、最關鍵的取捨與最強反方。 |
| **同一個決策，但還沒有記錄持倉** | 決策、理由與為什麼是現在 | 不會直接拒絕，而是給一個有邊界的決策框架：最強正方、最強反方、真正決定這筆交易的關鍵問題，以及哪些組合事實尚未檢查。沒有虛構數字，也不會持久化。 |
| **「幫我復盤最近的交易。」** | 券商 CSV 或交易匯出 | 一張聚焦的行為復盤卡：做對的一件事、最大且有證據的漏洞、會改變判讀的動機問題，以及最多一條你親自選的規則。 |
| **「我只有持倉截圖。」** | 持倉表或券商對帳單截圖 | 開場結構檢查：權重、單一持倉風險、驅動集中、ETF 結構與資料完整性限制。不虛構交易歷史。 |
| **「先讓我看看體驗。」** | 不需要私人資料 | 使用虛構資料的隔離 test drive，不會寫進你的真實教練記憶。 |

## 實際使用 FOMO Kernel 的體驗

### 1. 直接用人話開始

你不需要先選內部模式，也不需要先學流程。說出正在面對的決策、附上手邊的記錄，或要求 test drive 即可。

FOMO Kernel 會針對當下使用最窄、但仍有價值的路徑。即時交易決策維持簡短對話；交易歷史值得一張完整復盤卡；只有持倉截圖時，就做結構檢查，不虛構歷史判斷。

### 2. 引擎先建立事實，Agent 才開始解讀

組合數學、排名、規則衝突、identity 與持久狀態都由引擎負責。Agent 不能偷偷補一個缺失價格、重算權重，或發明交易歷史。

因此對話有穩定的地基：記錄實際說了什麼、Agent 認為這代表什麼、還有哪些事情不知道，三者保持可區分。

### 3. 只問程式無法知道的事

一筆可疑加碼可能是信念，也可能是不願停損；提早賣出獲利部位可能是紀律，也可能是害怕回吐。程式可以找出張力，但只有你能說明動機。

問題因此聚焦在：為什麼是現在、什麼真的改變、什麼會證明 thesis 錯了、當時到底是什麼驅動行動。它不是一份泛用投資人格問卷。

### 4. 先看到有用結果，再要求下一個承諾

交易前回答會先講真正影響決策的張力與最強反方，不會先塞滿工具流程或 caveat。

復盤會先把完整卡片顯示在對話裡，再請你選擇規則。檔案生成不等於交付；結果必須真的到你面前。

### 5. 最終動作仍由你負責

對一筆正在考慮的交易，FOMO Kernel 可以記錄「曾經考慮過什麼」，但不會把它叫做已執行。它不給目標價，也不替你選擇要買賣哪一檔。

復盤時，你可以選一條候選規則、自訂一條，或跳過。產品不會為了完成流程而捏造承諾。

### 6. 下次對話從上次開始

下次復盤時，FOMO Kernel 會先檢查上次選的規則，並沿用已確認的 thesis 與記錄持倉。重新上傳完整交易歷史是安全的，重疊資料會自動去重。新的持倉視圖會先和記錄持倉比較，不會靜默取代。

價值不在於建立更大的資料庫，而在於連續性：**當時相信什麼 → 實際做了什麼 → 後來改變什麼 → 哪條規則值得留下。**

## 復盤卡長什麼樣

以下 committed demo 使用完全虛構的資料：

![fomo-kernel review card demo](docs/demo-card.png)

可開啟同步的[繁體中文 HTML demo](docs/demo-card.html)或[英文 HTML demo](docs/demo-card-en.html)。

圖片只展示結果卡；真實復盤流程會先問動機問題，而你的回答可能改變最終判讀。Mock 刻意高度集中，其中的 alpha 數字不是可泛化的績效主張。

交易前回答預設保持簡短文字，除非你明確要求更多。

## 最快得到價值的路徑

### 1. 安裝

```bash
git clone https://github.com/atomchung/fomo-kernel
cd fomo-kernel

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 skills/fomo-kernel/engine/review.py doctor
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/fomo-kernel" ~/.claude/skills/fomo-kernel
```

請從已啟用虛擬環境的終端機啟動 Claude Code。

### 2. 帶進一個真實決策，或一份真實記錄

在 Claude Code 裡：

```text
/fomo-kernel 我正在考慮加碼 20 股 NVDA。
我現在的理由是……，而這次真正改變的是……

/fomo-kernel ~/Downloads/trades.csv

/fomo-kernel
接著附上持倉表或券商對帳單截圖。

/fomo-kernel
沒有檔案時，選擇虛構資料 test drive。
```

你不需要自己清理券商匯出。Agent 會在本機把它映射成引擎需要的資料契約。

問題與卡片支援英文、繁體中文與簡體中文。切換語言只改文案，不改引擎事實。

## 不同輸入能解鎖什麼

### 一筆正在考慮的交易 + 已有記錄持倉

FOMO Kernel 會計算交易後權重、集中度、依賴同一驅動因素的持倉重疊、現金影響、規則衝突，以及這些事實使用的組合基礎。回答再從凍結結果出發，提出最強正方與最強反方。

### 一筆正在考慮的交易 + 沒有記錄持倉

對話仍會往前，但不假裝知道權重、集中度、現金或規則衝突。它只問會改變判讀的少數問題，並說清楚下一份證據能換來什麼更具體的答案。這條路徑不會持久化任何內容。

### 交易歷史

交易匯出可以支持跨時間的行為判讀：倉位大小、攤平、出場、分散、持有一致性、逐標的診斷，以及資料足夠時的績效歸因。引擎只挑少數值得追問的動機；最終復盤收斂成一張卡與最多一條由使用者選的規則。

### 持倉快照

持倉表或截圖可以支持開場結構檢查，但不能誠實推論過去是否攤平、出場紀律、持有行為、勝率、payoff、alpha 或歷史動機。之後加入交易歷史，才會解鎖這些判讀。

## 它和一般聊天有什麼不同

一般聊天可以討論 thesis；FOMO Kernel 多了一個可執行、可稽核的決策契約：

| 層 | 誰負責 |
|---|---|
| 組合數學、排名、規則、identity 與狀態轉換 | 確定性引擎 |
| 動機追問、有邊界的解讀、最強反方、白話說明 | Agent |
| 最後動作、確認，以及規則是否保留 | 使用者 |
| 持久歷史與 replay | 本機 canonical session bundle |

這個分工避免 Agent 悄悄變成第二套組合事實來源。

## 隱私與真實性邊界

- **沒有 FOMO Kernel backend。** Repository 沒有帳號服務或上傳端點，也不會把任何內容送給作者。
- **檔案與狀態留在本機。** 來源檔案、正規化快照、canonical session、私人卡片與 projection 都存在執行 skill 的機器上。
- **公開市場資料。** 為計算支援的價格與報酬，引擎可能向市場資料供應商查詢公開 ticker 與日期；不會傳送券商交易列、數量、成本、動機或卡片。
- **你選擇的 AI host 仍然重要。** 你明確交給模型／client 的內容，仍依該 host 自己的條款處理；FOMO Kernel 不會再加一個伺服器，也不會暗中公開資料。
- **沒有 cloud OCR 路徑。** 截圖由 coding agent 從本機附件抄錄；引擎不會上傳到 OCR 服務。
- **預設私人。** 正常輸出是 `card-private.*`。你可以要求分享安全版 `card-public.md`；它會移除金額、日期、ticker、精確權重、session ID 與 Agent 自由文字，而且不會自動發布。
- **公開 repository 只允許 synthetic evidence。** 不要把真實交易、持倉、動機或卡片貼到公開 issue 或 PR。

## 本機記憶、重複使用與恢復

完成的復盤會存成 immutable canonical session：

```bash
ls ~/.trade-coach/sessions/
```

常用控制：

```bash
python3 skills/fomo-kernel/engine/coach.py data-status
python3 skills/fomo-kernel/engine/coach.py data-export --out backup.zip
python3 skills/fomo-kernel/engine/coach.py data-reset --dry-run
python3 skills/fomo-kernel/engine/coach.py data-reset --confirm
```

`data-status` 只列檔案狀態與 metadata，不印出交易內容。匯出的備份應視同券商對帳單保管；`data-reset --confirm` 不可逆。

中斷後，Agent 會恢復 pending session，而不是重新抓取你已經回答過的事實。canonical session 已成功 commit、但衍生 projection 失敗時，可以重建 projection，不必再問一次。

## 其他 coding agent

Claude Code 提供最直接的 slash-command 安裝。Codex、Cursor 與相容的 coding agent 可以開啟 repository，依照 [`AGENTS.md`](AGENTS.md) 進入同一份 host-neutral contract，再由它路由到 [`skills/fomo-kernel/SKILL.md`](skills/fomo-kernel/SKILL.md)。

若要用 host-neutral CLI 啟動 test-drive plan：

```bash
python3 skills/fomo-kernel/engine/review.py prepare --test-drive --language zh-TW
```

這個命令會回傳 Review Plan；Agent 再依它選出的 flow 呈現並完成體驗。

目前 owner-live acceptance 聚焦 Claude Code 與 Codex。能相容執行，不代表已完成產品驗收。

## 平台支援

- Python 3.11+。
- macOS 與 Linux 支援 durable session finalization。
- Windows 可以執行 `prepare` 與 `preview`；但 durable `finalize` 目前會在改動已提交的 canonical state 前 fail closed，因為實作依賴 POSIX locking 與 directory `fsync`。

## FOMO Kernel 不做什麼

FOMO Kernel 不會：

- 提供目標價或市場預測；
- 替你選股；
- 替你做或執行最後買賣決定；
- 變成券商、財富管理或完整 investment OS；
- 爬取或鏡像你的私人研究 repository；
- 用泛用建議取代缺失的組合事實。

它是研究與決策教練工具，不是投資建議。所有投資決定與結果仍由你負責。

## 給 contributor 與 maintainer

請先讀：

- [`AGENTS.md`](AGENTS.md) — 路由與不可妥協的邊界；
- [`docs/issue-lifecycle.md`](docs/issue-lifecycle.md) — context 載入與 issue owner；
- [`docs/maintainer-guide.md`](docs/maintainer-guide.md) — 開發、隱私、測試、mirrored surfaces 與 PR 慣例。

提交 repository 改動前：

```bash
python3 tests/run_all.py
```

公開範例與 fixture 必須維持 synthetic。授權見 [MIT License](LICENSE)。