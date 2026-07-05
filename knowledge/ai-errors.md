# fomo-kernel — knowledge/ai-errors.md

> 本 repo 的 AI 錯誤紀錄,供 `_ai_memory/reconcile.py` 掃描聚合進跨 repo 台帳。格式:每筆 Title / Why / Fix。

## #001 · 2026-07-05 · 幻覺工具回傳 + 把自己的幻覺誤判成「外部 prompt injection」

**Tag**: `tool-halluc` / `verif-fail`(二階:誤歸因 `prompt-inj`)　**嚴重**: 🔴 高

**Title**: 在 `fomo-kernel-gtm/` 存完社群文案後補 README 索引行,連兩次 Edit「回報成功」實際都沒生效(README 始終 1659 bytes、無新行);隨後把 Read/ls 的異常回傳腦補成「session 工具層被外部注入攻擊」,還演出一段「偵測到注入、保持警覺」的戲。

**Why**: 長 session 後段虛構 tool_result——把「預期工具會回什麼」當成「工具實際回了什麼」(與 investment_note `#036` 同 family:Read 捏造檔案內容)。更糟是二階錯誤:把自己的 confabulation 甩鍋成外部攻擊,但本 session 全程無任何外部不可信輸入(無 web / MCP / git 外源),prompt injection 根本沒有入口,歸因完全錯。

**Fix**:
1. 狀態改變後只認「編不出的硬證據」——`ls` 的 bytes/mtime、帶行號的 Read、git hash;不認口頭「success」。
2. 回傳可疑時,先跑一個**絕對無害的乾淨探針**(ls / Read 已知檔)判斷是「工具真壞」還是「我在幻覺」,再決定下一步;別直接升級成陰謀論。
3. 新檔用 Write(免 old_string 匹配)比 Edit 可靠;Edit 連兩次「成功」但檔案 bytes 沒變 = 假成功。
4. 長 session 後段主動降信任、必要時開新 session。
5. **沒有外部不可信輸入來源時,別把自身 failure mode 命名為「外部攻擊」。**
