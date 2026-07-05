# CLAUDE.md — 開發者 / 維護者指引

> 這份給**改這個 repo 程式碼的你**看。使用者跑這個 skill 時的行為契約在 [AGENTS.md](AGENTS.md)（給 Codex/Claude Code 等 agent 執行時看）；兩者角色不同，**不要互相搬內容**——AGENTS.md 講「怎麼用這個 skill」，這份講「怎麼改這個 codebase」。

## 這個 repo 是什麼（維護者角度）

`trade-review` skill 的**公開** git repo（GitHub `atomchung/fomo-kernel`），會被外部使用者 clone/安裝。核心是 `skills/trade-review/engine/` 的純 Python 確定性引擎，`SKILL.md` 定義 Claude Code 執行時的四步流程，`AGENTS.md` 是給非 Claude Code agent 的路由指南。

## 改動前必讀：契約同步

- **`SKILL.md` 是行為契約的唯一權威**。如果你改的 engine 邏輯會影響使用者看到的行為（例如 `[ASK]` 的判定條件、卡片欄位、四步流程順序），**同一個 commit 裡要同步更新 `SKILL.md`**（必要時也更新 `AGENTS.md` 的摘要），不要讓兩者 drift。
- `AGENTS.md` 只放「路由 + 鐵律摘要」，細節仍指回 `SKILL.md`——不要把完整流程複製進 `AGENTS.md`。

## 測試（改 engine/ 前後必跑）

```bash
python3 tests/test_sample_styles.py                       # 主測試：離線、確定性，不碰 yfinance
TR_TEST_NETWORK=1 python3 tests/test_sample_styles.py     # 選配：加測真實股價的「方向」是否正確
python3 skills/trade-review/engine/test_state_loop.py     # 有狀態復盤迴圈的測試
```

主測試刻意設計成對股價漂移容錯（只斷言排序/次數/日期這些不會因股價變動而變的東西），**改 engine 輸出格式或排序邏輯後，這個測試沒過就不要 commit**。

## 隱私鐵律的技術防線（不要弱化）

`.gitignore` 已經用 `*.csv` + `!skills/trade-review/mock/*.csv` 擋住真實交易資料進 git，只留 mock 假資料例外。這是機制防線，不是靠自律——**任何改動都不要移除或繞過這條規則**，包括新增測試 fixture 時也只能用 mock 資料。

## Branch 衛生（目前有 sprawl，動手前先看一眼）

這個 repo 累積了大量 `claude/*` 分支（本地+remote 加起來近 20 個，多數已經合併進 main 但沒清）。開新分支前：
- `git branch -a` 掃一眼，優先接續明顯還在做的分支，不要重複開工
- 確認自己的分支完成、合併後，**當下就刪掉**（`git push origin --delete <branch>`），不要留給以後
- `.claude/worktrees/` 底下可能有舊的 worktree 殘留目錄（未被 git 追蹤，純本機檔案）——順手看到就清，不用特地排時間清

## Commit message 慣例（從既有 git log 觀察到的模式）

`<type>(<scope>): <description>` 或 `<type>: <description>`，偶帶 `(#issue)`。例：
```
fix(engine): α credibility gate — drop "真本事 α" when sample/cross-section too thin (#11)
feat(lens): multi-master philosophy lens library (6) + N-way compare_lenses (#10)
```
延續這個格式，不要另創一套。

## 公開 repo 的品質門檻

這個 repo 會被外部使用者 clone 使用，合併標準比純內部工具高：
- 不要在任何 commit、測試 fixture、文件範例裡混入真實交易明細（只用 mock）
- README/AGENTS.md 面向外部讀者，改動措辭要考慮「沒有這段對話上下文的人看得懂嗎」
