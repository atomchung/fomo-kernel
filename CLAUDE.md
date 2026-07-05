# CLAUDE.md — 開發者 / 維護者指引

> 這份給**改這個 repo 程式碼的你**看。使用者跑這個 skill 時的行為契約在 [AGENTS.md](AGENTS.md)(給非 Claude Code 的 agent,如 Codex,執行時看;Claude Code 自己會自動載入 SKILL.md,不需要 AGENTS.md);兩者角色不同,**不要互相搬內容**——AGENTS.md 講「怎麼用這個 skill」,這份講「怎麼改這個 codebase」。

## 這個 repo 是什麼(維護者角度)

`fomo-kernel`(對外 `/fomo-kernel` skill)的**公開** git repo(GitHub `atomchung/fomo-kernel`),會被外部使用者 clone/安裝。核心是 `skills/fomo-kernel/engine/` 的純 Python 確定性引擎,`skills/fomo-kernel/SKILL.md` 定義 Claude Code 執行時的四步流程,`AGENTS.md` 是給非 Claude Code agent 的路由指南。

## 改動前必讀:契約同步

- **`skills/fomo-kernel/SKILL.md` 是行為契約的唯一權威**。如果你改的 engine 邏輯會影響使用者看到的行為(例如 `[ASK]` 的判定條件、卡片欄位、四步流程順序),**同一個 commit 裡要同步更新 SKILL.md**(必要時也更新 `AGENTS.md` 的摘要),不要讓兩者 drift。
- `AGENTS.md` 只放「路由 + 鐵律摘要」,細節仍指回 `SKILL.md`——不要把完整流程複製進 `AGENTS.md`。

## 測試(改 engine/ 前後必跑)

```bash
python3 tests/run_all.py                       # 一鍵跑全部五套測試,離線、確定性、免裝 pytest
TR_TEST_NETWORK=1 python3 tests/run_all.py     # 額外加跑 β 方向 network smoke
```

五套分工:機械層純函式單元(`tests/test_engine_units.py`)、TR_JSON/state 契約(`tests/test_tr_json_contract.py`)、價格路徑合成單元(`tests/test_price_paths.py`)、三風格端到端(`tests/test_sample_styles.py`)、狀態迴圈端到端(`skills/fomo-kernel/engine/test_state_loop.py`)。**改 engine 輸出格式、last_px 邏輯或排序邏輯後,這五套沒全過就不要 commit。**

## `.claude/` hooks(committed 的 agent 護欄)

這個 repo committed 了 Claude Code hooks(`.claude/settings.json` + `.claude/hooks/`),把上面「五套沒全過就不要 commit」從自律變成機制:`pre_commit_test_gate.sh` 是 `PreToolUse:Bash` gate,當 `skills/fomo-kernel/engine/` 或 `tests/` 有未提交改動時跑 `tests/run_all.py`,紅了就 deny 掉 commit。

⚠️ **改或加任何 hook 前必讀**:實測目前這版 Claude Code **忽略 hook 的 `if:` filter**——matcher(如 `Bash`)會對**每一個**符合的 tool call 觸發,不是只有 `if` 指定的那種。所以**一律在腳本裡自己讀 stdin `tool_input.command` 判斷、非目標指令立即 `exit 0`,永遠別依賴 `if:`**。少了這道自我過濾,commit-gate 會在 engine dirty 時對每個 Bash 指令各跑一次整套測試(~11.5s)。照 `pre_commit_test_gate.sh` 開頭的 self-filter 範式抄。

## 隱私鐵律的技術防線(不要弱化)

`.gitignore` 已經用 `*.csv` + `!skills/fomo-kernel/mock/*.csv` 擋住真實交易資料進 git,只留 mock 假資料例外。這是機制防線,不是靠自律——**任何改動都不要移除或繞過這條規則**,包括新增測試 fixture 時也只能用 mock 資料。

## Commit / PR 慣例(從既有 git log 觀察到的模式)

`<type>(<scope>): <description> (closes #NN) (#PR)` 或 `<type>: <description>`。例:
```
fix(engine): last_px covers all fetched tickers, not just round-trips (closes #79) (#83)
fix(engine): candidate_rules 補 3 維規矩生成 + 分散維度門檻對齊 (#100)
```
這個 repo 走**issue → PR → close issue** 的正規流程,延續這個格式,不要另創一套。開 PR/issue 前**先 `gh issue list` / `git log --grep` 查一下有沒有已經修過**——這個 repo 修 bug 的節奏很快,容易撞到已經處理過的東西。

## 並行開發慣例(多 session / 多 agent 同時動這個 repo 是常態)

- **認領再修**:動手修某個 issue 前,先 assign 自己或在 issue 下留言認領;開修復 PR 前 `gh pr list` 查同一 issue / 同一函式區域有沒有 open PR。前例:同一個 bug 被獨立診斷兩次(#87/#95,互不引用),`render()` 被兩個 PR 並行大改產生 4 個規格 regression(#23/#24)。
- **開新 branch 先 fetch、從最新 `origin/main` 開**;merge 完手上的 PR 後再 `gh pr list` 一次,查剛冒出的新 PR、以及與剛 merge 內容的**語意重疊**(git 只擋文字衝突,不擋語意衝突)。
- **修 bug 不只修發現的那個實例**:同一根因常住在多處,動手前先 grep fixtures / docs / tests 掃其他實例,PR body 寫「掃過的範圍與結果」。前例:拆股 fixture 的同款定價 bug 分三批被動發現(#93 → #98 → #108)。
- **批次 merge(一次合 ≥2 個 PR)前做一輪 zoom-out**,不只逐 diff 看正確性:①同主題 issue 第二次出現=同一設計缺陷的第二個症狀,先問「這條線該不該存在」再修單點 ②文檔/測試出現「繞過/避開/先…再跑」措辭=系統在教人繞過自己 ③ engine 靠檔名/環境變數等隱性訊號做行為分支=違反 data-agnostic(#89 前例)。含 engine 改動時,對全部 mock persona CSV 跑一輪產卡並核對數字——#93/#94/#95 三個正確性 bug 全是這樣現形的,任何 diff review 都看不到。
- **批次 merge 收尾**:這一輪產生的 agent worktree / 本地 branch,PR 全 merge 後,驗證 commit 已可從 main 達到、且 `git worktree list` 確認沒有其他 session 在用,才清掉。

## 鏡像檔案對照表(同一份事實住在多處,改一處要連動)

漏同步的 drift 反覆發生過(#68、#96、cycle_id 對帳失效),改下列任何一處,照表連動其餘:

| 事實 | 住在哪些檔案 |
|---|---|
| 行為契約 | engine ↔ `skills/fomo-kernel/SKILL.md`(權威)↔ `docs/eval-design.md` ↔ `evals/EVALS.md` |
| demo 卡示意數字 | README 文字卡 ↔ `docs/demo-card.html`(改後重截 `demo-card.png`) |
| README 雙語 | 繁中主文 ↔ 英文 TL;DR,語意要對齊,不要只改一邊 |

引用**產品假設**(誰是用戶、當前卡點是什麼)做優先級決策時,帶上判定日期;判定已隔數週或出現矛盾訊號,先跟 maintainer 對帳再據以行動——過時結論被跨 session 複讀的案例見 #112。

## 公開 repo 的品質門檻

這個 repo 會被外部使用者 clone 使用,合併標準比純內部工具高:
- 不要在任何 commit、測試 fixture、文件範例裡混入真實交易明細(只用 mock)
- README/AGENTS.md 面向外部讀者,改動措辭要考慮「沒有這段對話上下文的人看得懂嗎」
