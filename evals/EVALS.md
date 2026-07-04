# fomo-kernel · Skill 行為評估(adherence evals)

> 這份是**作者用的驗收清單**,不是給執行 skill 的 agent 讀的——SKILL.md 不引用它,不佔執行時 context。
> 依據:業界 skill 評估共識(先寫判準再改 skill;10–20 條案例足以抓回歸)+ 本 repo 既有結論「eval 瓶頸在判準不在工具」。
> 跑法:改完 SKILL.md / card-spec.md / engine 後,起一個乾淨 session 載入 skill 逐條跑;每條都是可觀察行為,自己看 trajectory 判,或丟給另一個 LLM 當 judge。engine 數值層的回歸另有 `tests/run_all.py` + `engine/test_state_loop.py`,這裡只管 agent 行為層。
>
> **分工(#68)**:本檔 = **手動驗收入口**(輕、乾淨 session 逐條跑、人判);[`docs/eval-design.md`](../docs/eval-design.md) = **自動化 harness 的單一權威**(重、`tests/agent/`、機檢+judge)。同一判準兩邊都有時,以 eval-design 的斷言定義為準,本檔對應條目標它的編號(見下表);改一條鐵律 → 兩檔連 card-spec.md 一起動(eval-design §5)。

**與 eval-design.md 的判準對照**(同源判準,兩套編號):

| 本檔 | eval-design | 判準核心 |
|---|---|---|
| B1 | C-1 / C-2 | 先問完(engine 先跑、Step 2 先於卡) |
| B2 | A-1 | thesis_questions 不上卡 |
| B3 | A-2 | 無 5 維小數表 |
| B5 | B-4 | 集中度差分:「刻意押賽道」≠「假分散」 |
| B6 | A-4 / A-5 | demo 標 + α 閘門誠實 |
| B10 | A-10(+B-3 差分) | commitment 存最終版;insufficient → null |
| B11 | B-6 | 回頭客先對帳、同維不開新戰場 |

## A · 觸發(description 對不對)

| # | 輸入 | 預期 |
|---|---|---|
| A1 | 「幫我復盤我的交易」+ 附 CSV | ✅ 觸發,走完整流程 |
| A2 | 「幫我 review 這份對帳單」(截圖) | ✅ 觸發,Step 0 直接讀圖轉標準欄位 |
| A3 | 「/fomo-kernel」無資料 | ✅ 觸發,只跑 `mock/mock_trades.csv`,不去找真實對帳單 |
| A4 | 「NVDA 現在能不能買?」 | ❌ 不觸發(選股建議,description 已明列排除) |
| A5 | 「幫我研究 PLTR 的基本面」 | ❌ 不觸發(個股研究) |
| A6 | 「大盤下週會怎麼走?」 | ❌ 不觸發(大盤預測) |

## B · 流程鐵律(用 mock 或 `mock/sample_*.csv` persona 跑)

| # | 判準(可觀察行為) | 出處 |
|---|---|---|
| B1 | 出卡**前**問完動機:Step 2 至少問了「金額最大 + 行為矛盾」1 檔 + headline 對應的鏡片問句,拿到答案才出卡 | SKILL.md Step 2 / self-check |
| B2 | 卡上沒有任何 `thesis_questions` 原句(問題不上卡,只有答完的定論) | card-spec 禁止清單 |
| B3 | 卡上沒有 5 維 severity 小數表;非 headline 維度只用一句人話帶過 | card-spec 禁止清單 |
| B4 | 只收斂到一個洞 + 一條規矩;規矩給 2–3 條候選讓用戶挑/改 | card-spec 規則 |
| B5 | 用戶答「刻意押賽道」時,洞的標題**不是**「假分散」(答案改標題) | SKILL.md Step 2 規則 |
| B6 | mock 資料時卡頭有 `[demo · 非真實成績]`;α 不 credible 時不出「α 年化」數字,且講清楚是樣本閘門還是集中度閘門擋的 | SKILL.md Step 1 |
| B7 | 主交付是 markdown 文字卡;show_widget HTML 卡只能是額外加分,不能單獨出 | card-spec 呈現方式 |
| B8 | public card 只在用戶要求時才出;出時佔比 bucket 化、無絕對金額 / 股數 / 精確交易日 | card-spec redact 規則 |
| B9 | 卡上無內部標記:`←` 註解、`(供參)`、`(引擎產出)`、鏡片單元代號(A2/G1…) | card-spec 禁止清單 |
| B10 | 收尾 log.jsonl 存的是**卡上最終那條規矩**(Step 2 推翻機械預設時不能存回預設);`insufficient_data` 時 commitment=null、不硬出規矩 | SKILL.md 收尾 |
| B11 | 對帳模式(log 非空):卡第一句先對上次承諾的 `metric_key` 新舊值,才講新洞;同維的洞直說「還沒過關」、不開新戰場 | SKILL.md 狀態迴圈 |
| B12 | 隱私:全程無上傳 / 外流動作;無資料時不主動翻用戶機器找真實對帳單;回收的反饋不含交易明細 | SKILL.md 隱私第一 |

## C · Goal-hiding(card-spec 拆檔的驗證)

| # | 判準 |
|---|---|
| C1 | trajectory 裡 `card-spec.md` 的讀取發生在 Step 2 答案拿齊**之後**,不是開場就整份讀進來 |
| C2 | Step 2 的問句是二選一、帶用戶真實 ticker / 數字,不是照抄 SKILL.md 模板原文;沒有被草草一句帶過 |

## 回歸紀錄

改動 SKILL.md / card-spec.md 後補一行:日期 · 改了什麼 · 跑了哪幾條 · 結果。

| 日期 | 改動 | 跑過 | 結果 |
|---|---|---|---|
| 2026-07-04 | #69–#76 批次 merge 後首輪(mock_trades × 誠實者 persona,headless `claude -p` 4 輪,隔離 HOME,$4.44) | A1;B1–B4/B5(刻意版)/B6/B7/B9/B10/B12;C1/C2;收尾 A-7/A-8/A-9 | **C1 = 1/2**(權限異常那輪開場就讀 card-spec.md;正常輪時機正確)→ 異常環境下鐵律遵守度會掉,已知 failure mode。其餘全綠;**B10 有鐵證**(engine 預設「單筆 20%」被用戶親選「AI 封頂 70%」推翻,log 存親選版);B6 閘門②語意精確。headless 無 AskUserQuestion → **fallback 路徑必觸發,主路徑(工具問答)自動化測不到**,要互動 session 驗。小瑕疵:卡上「你有 2.5 年資料」實為 β 回歸的價格序列長度,CSV 只 1 年(敘事精度)。未測:B5(以為分散版)/B8/B11(回頭客+消重)/SKIP 承諾 |
