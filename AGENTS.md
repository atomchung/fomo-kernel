# AGENTS.md — fomo-kernel

> 給 AI coding agent(**Codex**、Cursor、Claude Code 等)的操作指引。
> 人類使用說明見 [README.md](README.md);**完整流程權威見 [skills/fomo-kernel/SKILL.md](skills/fomo-kernel/SKILL.md)**。

## 這個 repo 是什麼

一個交易復盤工具:把使用者的交易 CSV 復盤成**一張卡** —— 一個最大的行為漏洞 + 一條下次守則 + 一句大師原則。三層遞進:機械層(Python 確定性精算)→ 鏡片層(大師原則問動機)→ 收斂成一張卡。

## 何時觸發

使用者說「復盤」「trade review」「檢討交易」「看我的對帳單」,或丟出一份交易 CSV 時。

## 怎麼做(照這個順序)

1. **先讀完整流程**:`skills/fomo-kernel/SKILL.md` 是四步流程的權威,照它走。
2. **跑 engine(機械層)**:
   ```bash
   cd skills/fomo-kernel
   python3 engine/trade_recap.py <使用者的 CSV 路徑>
   # 不帶參數 = 跑內建 mock,看一次完整 demo
   ```
   依賴:Python 3.11+、`yfinance`、`pandas`、`rich`(見 `requirements.txt`)。沒網路時 engine 會自動退成行為層診斷,不會中斷。
   CSV 來自任何券商都行 —— 你負責讀懂、轉成引擎要的欄位(`Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate`),不必要求使用者手動整理。
3. **出卡前問動機(鏡片層)**:engine 標 `[ASK]` 的標的(金額大 + 虧損中加碼),逐一問使用者「逢低布局還是凹單?」,拿到答案才出定論卡。
4. **出卡(收斂層)**:engine 輸出 + 使用者動機答案 → 收斂成一張卡(版型見 `skills/fomo-kernel/card-template.html`)。

## 鐵律(不可違反;完整版見 SKILL.md)

1. **數字全部來自 engine,一個都不准自己算或編。** 你的工作是解讀 + 追問動機,不是當計算機。engine 沒輸出的數字 → 不要寫。
2. **不給投資建議。** 不 recommend 買賣標的;只復盤「行為」、問「動機」、給「下次守則」。
3. **動機問句必出。** engine 標 `[ASK]` 的標的,出卡前必問,不可跳過。
4. **一次只逼一件事。** 卡上「下次只改」永遠只有一條,不給清單。
5. **隱私不外傳。** 使用者交易資料只在本機跑,不上傳、不外傳、不寫進任何雲端記憶。

## 為什麼有這個檔

`SKILL.md` 是 Claude Code 的 skill 格式,**會被 Claude Code 自動偵測載入**。其他 agent(如 Codex)沒有「自動載入 skill」機制,但**能照常把 SKILL.md 當指令讀 + 跑 engine** —— 本檔就是給這些 agent 的指路牌。核心引擎是純 Python,**與工具無關**:Codex / Claude Code / 甚至使用者自己在終端,都跑得出同一份診斷。
