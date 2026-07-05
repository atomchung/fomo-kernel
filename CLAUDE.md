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

## 隱私鐵律的技術防線(不要弱化)

`.gitignore` 已經用 `*.csv` + `!skills/fomo-kernel/mock/*.csv` 擋住真實交易資料進 git,只留 mock 假資料例外。這是機制防線,不是靠自律——**任何改動都不要移除或繞過這條規則**,包括新增測試 fixture 時也只能用 mock 資料。

## Commit / PR 慣例(從既有 git log 觀察到的模式)

`<type>(<scope>): <description> (closes #NN) (#PR)` 或 `<type>: <description>`。例:
```
fix(engine): last_px covers all fetched tickers, not just round-trips (closes #79) (#83)
fix(engine): candidate_rules 補 3 維規矩生成 + 分散維度門檻對齊 (#100)
```
這個 repo 走**issue → PR → close issue** 的正規流程,延續這個格式,不要另創一套。開 PR/issue 前**先 `gh issue list` / `git log --grep` 查一下有沒有已經修過**——這個 repo 修 bug 的節奏很快,容易撞到已經處理過的東西。

## 公開 repo 的品質門檻

這個 repo 會被外部使用者 clone 使用,合併標準比純內部工具高:
- 不要在任何 commit、測試 fixture、文件範例裡混入真實交易明細(只用 mock)
- README/AGENTS.md 面向外部讀者,改動措辭要考慮「沒有這段對話上下文的人看得懂嗎」
- README 是**分檔雙語**:`README.md`(英文,GitHub 首頁預設顯示、對外主入口)+ `README.zh-TW.md`(繁體中文完整版),兩檔頂部用 `English · 繁體中文` 語言連結互指。改動主要內容時**兩檔都要同步更新**,不要只改一邊(英文是主入口,尤其別讓它 drift 落後中文)
