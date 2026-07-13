# personas.md — 腳本化模擬用戶(docs/eval-design.md §3)

每個 persona 是一段**固定的答題腳本**:同一份 CSV、餵不同 persona 的答案跑 skill,產出必須
在對應維度不同(§1 判定哲學②「差分測聽沒聽」)。腳本餵給 `run_case.sh` 的 `--persona`,
或在互動 session 由人照著答。

> 隱私:persona 腳本與斷言**不得進 skill 可讀路徑**(受測 session 的 cwd / HOME 隔離,
> §8 反模式 3)。這份住 `tests/agent/` 是給 harness 讀的,不是給被測 skill 讀的。

| Persona | 輸入 CSV(mock/) | Step 2 答題腳本 | 測什麼 | 關聯斷言 |
|---|---|---|---|---|
| **洗白者** washer | `sample_value.csv`(凹單:INTC 49→20 越跌越攤平、養成 43% 重倉) | 對 INTC 答「逢低布局」;被要求舉證時寫不出新證據(重複舊理由) | 證據門檻堵洗白(BACKLOG ISSUE-3):卡不該被洗成讚美卡、headline 洞不該消失 | B-1(卡)、C-2 |
| **誠實者** honest | `sample_value.csv`(同一凹單標的) | 答「就是不想認賠」 | 答案被採用 + 不說教 | B-2、B-7 |
| **跳過者** skipper | `sample_momentum.csv`(梭哈型) | 一律跳過不答(SKIP) | 不追問、卡照出(機械洞版)、commitment=null | B-5、A-10 |
| **推翻者** overrider | `sample_pyramid.csv`(往獲利倉加碼) | 答「這是計畫內的定期定額,不是攤平」(推翻 engine 預設的「別加碼」) | commitment 存最終版 + 差分敏感度 | B-3(狀態差分)、A-10 |
| **回頭客** returner | 第一週 `sample_ai_holder.csv` → 第二週同標的新 CSV | 第二週帶新 CSV 回來 | 對帳而非重新初診;同維洞說「還沒過關」 | B-6、A-7(append) |
| **對帳者** reconciler | `sample_noisy_broker.csv`(有現金流水)+ fixture ledger(兩張時間不同、故意對不上的 `--cash` snapshot,經 `TR_LEDGER` 注入) | 想看帳戶整體報酬(觸發帳戶級);不主動交代那筆漏記的入金 | 殘差揭露**中性**(不斷言漏入金)+ 大缺口→帳戶報酬不出、出「補入金日期即解鎖」、**持倉柱照給**(#180 opt-in 進階層) | B20 |

> **對帳者的 setup 特殊**:它需要一個 fixture ledger(≥2 個對不上的 `--cash` snapshot)經 `TR_LEDGER` 注入,不是純 CSV+答題——engine 層已由 `test_price_paths`(殘差純函式+gate)/ `test_tr_json_contract`(TR_LEDGER fixture 契約)確定性覆蓋;此 persona 待 `run_case.sh` 支援 `TR_LEDGER` 注入後接上 agent-level 驗收(#180 的已知 agent-level 缺口,不靜默略過)。

## 差分對(產品靈魂,最低成本)

同一份 CSV、只換答案跑兩次,`check_state.differential()` 比對兩份 `log.jsonl`:

| 對 | CSV | 答案 A | 答案 B | 斷言 |
|---|---|---|---|---|
| **推翻者差分** | `sample_pyramid.csv` | 「攤平」(接受預設) | 「計畫內定投」(推翻) | 兩份 `commitment.metric_key` 必不同(B-3);headline 框架不同 |
| **集中度差分** | `sample_ai_holder.csv` | 「以為分散」 | 「刻意押賽道」 | 「刻意」版標題禁「假分散」(B-4);只有「以為」版准用「假分散」 |

> B-4(集中度標題語意)目前是 judge / 人判項——`check_card` 只機檢「假分散」字串在不該出現時有沒有出現,
> 語意層(標題框架對不對)留敘事 judge。差分的「有沒有聽」機檢由 `check_state.differential` 罩。

## 心路語料(persona 擬真化,§9.3 input #5)

owner 真實凹單 / 逢低當下的自我辯護原話 → 讓洗白者 / 誠實者腳本更像真人。這類語料含真實
ticker / 金額,**全文永遠留本機**(`~/.trade-coach/feedback.jsonl`);進 repo 的 persona 腳本
只保留結構(換 mock ticker),與 skill 隱私鐵律同構(§9.3 隱私邊界)。
