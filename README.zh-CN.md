# FOMO Kernel

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2.svg)](skills/fomo-kernel)
[![Engine: Deterministic](https://img.shields.io/badge/Engine-Deterministic-green.svg)](skills/fomo-kernel/engine)

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 一个专为 Claude Code、Codex、Cursor 等 Coding Agent 设计的本地交易复盘 Skill：先通过确定性诊断找出行为漏洞，再经过一段简短的判断对话收敛成**一张复盘卡**——
> 你做对的一件事 + 一个最大的漏洞（用你自己的数据）+ 一条你亲选、下次可验证的纪律规则。下次复盘时，首先对账这条规则是否得到遵守。

这不是又一份简单的统计报表。它完成了普通报表无法实现的功能：**先算出你未察觉的行为漏洞，再问出你不愿承认的交易动机，最终收敛至你亲选的一项可验证改变，并在下次复盘时进行对账。**

> 📝 **语言与语系。** 同一套复盘契约可渲染为繁体中文、简体中文或英文（`--language zh-CN|zh-TW|en`）。切换语言仅改变问题与卡片文案，不改变引擎计算事实与分析策略。


## Quick start

**完整流程（这才是产品本体）—— 在 Claude Code 中：**
```
/fomo-kernel ~/Downloads/my.csv   # 复盘你自己的交易（支持任何券商 CSV）
/fomo-kernel <附上的持仓表或对账单截图>   # 首次持仓健康检查
/fomo-kernel                      # 未提供数据 → 提示你提供，或使用内置样例数据进行“试驾”（不写入教练记忆）
```
复盘卡的价值体现在第 ② 步的对话中 —— 引擎挑选出可疑标的并询问：“逢低补仓还是死扛亏损？”；你的一句话回答定案后，复盘卡才得出最终结论。**仅查看引擎原始输出无法获得这一层深度。** 安装说明请见下方 [安装](#安装)。

持仓表或截图将走更窄的 snapshot 路线：仅进行首次持仓体检，分析成本或市值权重、单一持仓风险、驱动因素集中度、ETF 结构及数据完整性。单张快照无法揭示过往补仓、平仓时机、持有行为、胜率、盈亏比、Alpha 或历史动机；后续补充交易历史后，方可解锁有证据支持的历史诊断，但不会据此宣称已对齐最新券商界面，当前持仓仍以账本（Ledger）推导结果为准。

**希望零安装先体验稳定流程：**
```bash
git clone https://github.com/atomchung/fomo-kernel && cd fomo-kernel
pip install -r requirements.txt      # 若提示 externally-managed-environment → 请参阅下方“安装”部分的 venv 说明
cd skills/fomo-kernel && python3 engine/review.py prepare --test-drive --language zh-CN
# 首先生成可恢复的 Review Plan；回答完必需的动机问题后方可进行 preview/finalize
```

## 跑出来长什么样

运行内置 Mock 生成的**示意复盘卡**如下（以下为简化速览版；实际引擎输出为彩色终端卡，另含 What-if 回撤压测、5 维行为柱状图及收益归因专区 —— 真正的定论卡则是由 Claude 在第 ② 步对话完成动机询问后收敛生成的）：

```text
复盘卡 · mock 范例
你帐面赚 +$138k,但幾乎全是「抱著沒賣」賺的;真正進出操作,要靠紀律不靠運氣。

  帳面總損益      +$138,058    (已實現 $19k + 未實現 $119k)
  主動買賣盈虧比   2.9          (平均賺 $2,851 vs 賠 $1,000)
  贏大盤 +247pp · β 2.04 · AI 暴險 98%(回檔 30% = −$50k)
      └ 把「贏大盤」拆成運氣和技巧:押對賽道 +67pp + 板塊內選股 +181pp
        (α 區間仍寬,還分不出選股是本事還是運氣 —— demo 別當真)

標的層診斷(按金額排序,小倉不糾結):
  PLTR  +$74,058   [v] 疑似定投(漲跌都買,不是凹單) · [!] 押太重 50%
  NVDA  +$56,412   [v] 疑似定投 · [!] 押太重 46%
  ORCL   +$1,658   [v] 紀律持有:賺 +22%
  AMD    -$1,000   --  大致中性

[v] 你做對的:往下加碼 2 次,但都守在部位上限內,沒有任何一檔越攤越重
[X] 最大的洞:部位 sizing — 最大一筆 PLTR 佔 50%,其餘平均 17%
[*] 下次只改:單筆部位上限定死 20%,超過就減
```

同步的深色卡片示意可见 [English HTML](docs/demo-card-en.html) 与 [繁體中文 HTML](docs/demo-card.html)。

![fomo-kernel 復盤卡 demo](docs/demo-card.png)

> 在实际使用中，引擎还会挑选出“金额巨大 + 亏损中持续加仓”的标的，在**出卡前**询问你：“这是逢低吸纳还是死扛不认赔？” —— 机器无法分辨的动机，由你的一句话定案，复盘卡方能给出结论。
> ⚠️ Mock 中的 Alpha 数字存在失真（持仓过于集中、横截面过窄），请勿当真；真实的多元化持仓方具备 Alpha 参考意义。

---

## 它跟「貼對帳單給 ChatGPT」差在哪

ChatGPT 无法计算基于 FIFO 匹配的真实 Alpha/Beta、无法区分“分批定投”与“死扛补仓”，也不具备你的历史交易记忆。本 Skill 分三层递进：

1. **机械层（Python，确定性精算）** — 计算 ChatGPT 无法精确评估的数据：
   - 5 维行为诊断：持仓 Size / 加仓补仓 / 平仓时机 / 分散度 / 持有一致性
   - **标的层诊断**：按**金额**排序各标的（小仓位不予纠结），通过主从分类器区分“疑似定投 vs 疑似死扛 vs 待确认”
   - **收益归因**：将“超越大盘”拆解为“押对赛道（运气/方向）”与“选股能力（技巧）” —— 让你看清盈利源于实力还是胆量
2. **判断对话层（引擎信号 × 你的意图）** — 机器无法推断的“为什么”，在出卡前向你确认：
   - 持仓假设：“MSTR 持续加仓却依然亏损，是依然相信投资逻辑，还是不肯认赔在死扛？”
   - 交易动机：“过早卖出盈利标的，是因为到达目标价，还是害怕利润回吐？”
   - **由机械层挑选值得询问的少数标的，由你的回答完成定性** —— 机器永远在猜测，你的一句话决定事实
3. **单一规则层** — 将定性收敛为少数候选规则。你可以选择一条、自定义一条或跳过；下次复盘时将沿用同一规则进行对账，无需从零开始。

→ 最终收敛为**一张卡**，一个最核心漏洞，以及一条下次可验证的纪律规则。第二次使用时，首先对账“上次承诺的规则是否遵守”。

## 🔒 隱私:不上傳後端、作者拿不到

- 本 Skill 在**你自己的本地机器**上运行 CSV 或标准化持仓快照，**绝不上传至任何后端、不落盘至第三方存储、不回传给作者**。为了实现每周对账，系统会将复盘衍生状态保存在**你本地**的 `~/.trade-coach/` 目录中（绝不外传） —— 下一节将说明其具体内容及查看、导出与清除方法。
- 作者无法获取你的交易明细。唯一（自愿）收集的反饋仅为“本张复盘卡是否有帮助”，不包含任何交易内容 —— 如愿提供可通过 [card feedback 表单](https://github.com/atomchung/fomo-kernel/issues/new?template=card-feedback.yml) 提交，耗时约 30 秒。
- `.gitignore` 已配置：**任何 `.csv` 文件均不会被 Commit**，仅 Mock/Sample 样例数据例外。
- 准确而言：本地 Python Engine 读取标准化后的交易 CSV 或 Snapshot JSON 包。你所使用的 Coding Agent 可在本地读取持仓表或截图，将券商显示的客观事实逐栏转录；不经过 Engine OCR，亦无云端 OCR/上传路径。临时 JSON 保存在 Repository 外部（如 `/tmp`），Agent 不会自行计算权重或手动组装 Card/State。数据绝不回传作者。这与将对账单提交给会保留数据且不可审查的 SaaS 服务存在本质区别。

## 📁 你的教練記憶在哪 / 怎麼維護

第二次使用时，复盘卡将首先对账“上次承诺的规则是否遵守”。每次正式复盤的权威记录均为一个不可变（Immutable）的 Canonical Session：

```bash
ls ~/.trade-coach/sessions/       # bundle、state、answers、cards、hash manifest
```

原本的本地文件依然保留，但它们已转变为可重建的兼容 Projection：

```bash
cat ~/.trade-coach/log.jsonl       # 每行一次复盘记录（精简 Metric + 你承诺的规则）；为空表示首次使用
cat ~/.trade-coach/theses.jsonl    # 每笔持仓的“持仓理由 + 证伪条件”（Append-only，绝不覆盖）
cat ~/.trade-coach/profile.md      # 你的交易目标 + 3 条个人交易原则（复盘对比基准）
cat ~/.trade-coach/last_state.json # 引擎最近一次计算的精简状态（含各持仓 Shares/Cost，用于对账；每次运行覆盖）
```

引擎还在该目录存放了若干衍生文件（交易账本、平仓追踪队列、问题/规则日志、保存的复盘卡） —— 为避免依赖散文清单保证完整性，以下 CLI 工具为查看“本地存储内容”的唯一事实来源：

```bash
python3 skills/fomo-kernel/engine/coach.py data-status               # 所有已知路径：是否存在？大小？行数？（绝不打印交易具体内容）
python3 skills/fomo-kernel/engine/coach.py data-export --out backup.zip   # 将现有数据打包为 ZIP 文件（含敏感交易衍生数据，请妥善保存）
python3 skills/fomo-kernel/engine/coach.py data-reset --dry-run      # 预览 Reset 将删除的内容
python3 skills/fomo-kernel/engine/coach.py data-reset --confirm      # 确认执行彻底删除（不可撤销）
```

- **下周回来复盘需要导入哪份 CSV？** 直接导出**全量历史交易记录**提交即可 —— 无需手动追踪增量。与历史记录重叠的行会自动去重（去重逻辑即为此设计），因此**每周提交完整对账单完全安全**；引擎利用上次复盘的截止点识别新交易，复盘卡首句即对账上次承诺的规则。
- **Snapshot 锚定什么？** 首次完整的 Snapshot 可作为账本（Ledger）的会计锚点；不完整的 Snapshot 仍可生成有边界的体检报告，但不会写入锚点。后续补充交易记录可解锁有证据支持的历史分析，当前持仓仍以 Ledger 推导结果为准。第二次或后续 Snapshot 的差异对比、对账与 Adjustment Event 明确归入 P1 阶段；需要该层对账方可成立的当前画面断言维持不可用状态。
- **查看历史复盘记录** → `cat ~/.trade-coach/log.jsonl`。
- **重新开始 / 清空对账基准** → `coach.py data-reset --confirm`（或手动删除/重命名 `~/.trade-coach/`，效果相同：下次使用将被视为首次拜访）。
- **Thesis 记录有误** → 在下次复盘时新增修订 Event 并指向旧 Thesis；请勿手动修改 `theses.jsonl`，该文件现为 Canonical Session 的可重建 Projection。
- **隐私自证**：教练记忆即为 `data-status` 列出的本地文件，全部存放在你的机器上，作者端无任何记录。
- **希望先体验“多周循环”流程**（全程在 Temp 目录运行，**绝不触碰**正式的 `~/.trade-coach/`） → `python3 skills/fomo-kernel/engine/demo_weeks.py`：将内置 Mock 按时间切分为 3 段，模拟“初诊 → 对账 → 对账”，直观观察第二张卡如何引用上周承诺以及 `log.jsonl` 如何逐行生成。

> 💡 **希望分享至社区？** 每次 Committed Review 均会额外生成 `card-public.md`。它并非脱敏版的复盘卡，而是重新渲染的产物：交易记录复盘保留脱敏后的行为模式、引擎计算的 Beta 及相对大盘超额百分点；Snapshot Review 仅保留固定的结构基线表述，不暗示历史行为。两者均剔除具体金额、日期、Ticker、精确权重及 Agent 自由文本；默认回复依然为复盘卡。目前仅生成本地文件，尚未提供上传或发布功能。

## 安裝

**前置要求：** Python 3.11+。持久化 Session Finalize 目前需要 POSIX `flock` 与目录 `fsync`（macOS/Linux）；Windows 环境将在写入 Canonical Session Storage 前通过受控 CLI 错误 Fail Closed。Claude Code 用户可安装下方的 Slash-command Skill；Codex、Cursor 等 Agent 可直接依据 `AGENTS.md` 与 `engine/review.py` 使用本 Repo，无需 Claude 订阅。

需要 Python 3.11+。**在较新的 macOS（Homebrew / 系统 Python）上直接运行 `pip install` 会被 PEP 668 拦截**（`externally-managed-environment`），请使用 venv 进行安装：
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                            # yfinance + pandas + rich
python3 -c "import yfinance, pandas, rich; print('ok')"    # 验证：仅打印 ok 表示安装成功
```
将 Skill 加载至 Claude Code（二选一）：
```bash
ln -s "$(pwd)/skills/fomo-kernel" ~/.claude/skills/fomo-kernel   # A. 软链接（推荐）
cp -r skills/fomo-kernel ~/.claude/skills/                         # B. 复制（用于分发）
```
> ⚠️ 若通过 venv 安装，后续 Claude Code 运行引擎时需确保能够获取相关依赖：在**已激活 venv 的终端**中启动 `claude`，或在引擎提示 `ModuleNotFoundError` 时将 `python3` 替换为 `.venv/bin/python3` 后重新运行（SKILL 内部已集成该补救指引）。

## 用法

在 Claude Code 中：
```
/fomo-kernel ~/Downloads/my.csv   # 交易历史复盘
/fomo-kernel <附上的持仓表或对账单截图>   # 首次持仓健康检查
/fomo-kernel                      # 未提供数据 → 提示你提供，或使用内置样例数据进行“试驾”走完四步（标注示范、不写入教练记忆）
```
你的 CSV 可来自**任何券商** —— Claude 会自动解析并转换为引擎所需的字段（`Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate`，非美股标的可选择性添加 `Market / Currency` 字段，如 `2330.TW / TW / TWD`；未填写默认为美股 USD），无需手动清洗数据。

对于持仓表或截图，Agent 将在本地将界面显示的客观事实转录为标准 JSON Envelope（包含 `as_of`、`positions` 以及选填的现金、汇率与完整性事实），将临时文件保存在 Repo 外部，再提交给 `review.py`。权重、Cycle ID、风险 Metric 及 ETF 定性均由引擎计算，无需 Agent 手动计算。首次 Snapshot 将为未覆盖的持仓建立 Inferred Thesis；仅有完整的首次 Snapshot 方可作为会计锚点。后续补充交易记录可解锁有证据支持的历史行为诊断，但不会宣称 Ledger 持仓已对齐最新的券商界面。

> 🏷️ **冷门标的**可由 Agent 在本地提供 Driver Map；冷门 ETF 另可提供 Instrument Map。但仅有明确分类为大盘、区域、债券或商品 ETF 的标的方可获得配置豁免；未知标的默认仍按集中风险计算。

**执行流程**：① `prepare` 运行确定性诊断并构建 Question Queue → ② Agent 询问所有返回的问题并建立必要的 Inferred Thesis（Snapshot 模式可无动机问题）→ ③ `preview` 验证 Artifacts 并生成复盘卡 → ④ 你最多选择一条规则（亦可跳过），最后由 `finalize` 原子提交整个 Session。

## 其他 coding agent 怎么用

无需 Claude Code 的 Skill 系统同样可以使用。Codex、Cursor 等 Agent 遵循同一份 Orchestration Contract：

```bash
cd skills/fomo-kernel
python3 engine/review.py prepare ~/Downloads/my.csv --language zh-CN
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json --language zh-CN
# 遵循 review_plan.flow_path 执行，回答 question_queue，随后调用 preview / finalize
```

提示 Agent 首先阅读 [`AGENTS.md`](AGENTS.md)。`SKILL.md` 现为精简入口；各 Mode 的 Flow、JSON Schema、Validator 与 Renderer 构成了详细契约。

## 風格 sample(直接可跑,看不同風格照出不同洞)

`mock/` 目录下包含 **12 组 Sample**（3 组散户风格基准 + 4 组投资者画像扩展 + 5 组 Engine 边界场景）以及 `mock_trades`，各自触发一种典型漏洞或 Engine 边界。下方列出 4 个代表，完整 12 组及其设计意图请参见 [`mock/SAMPLES.md`](skills/fomo-kernel/mock/SAMPLES.md)：

```bash
cd skills/fomo-kernel
TR_DRIVER_MAP=mock/sample_fundamental.driver_map.json python3 engine/trade_recap.py mock/sample_fundamental.csv
TR_DRIVER_MAP=mock/sample_momentum.driver_map.json    python3 engine/trade_recap.py mock/sample_momentum.csv
TR_DRIVER_MAP=mock/sample_value.driver_map.json       python3 engine/trade_recap.py mock/sample_value.csv
python3 engine/trade_recap.py                          # 不带参数 = mock_trades.csv
```

| sample | 风格 | 应揭示的核心漏洞 |
|---|---|---|
| `sample_fundamental` | 基本面选股 | 出场纪律（盈利持仓 120 天即平仓、亏损持仓死扛 378 天等待回本） |
| `sample_momentum` | 追涨动能 | 仓位全押 + 假分散（将 Beta 误认为 Alpha） |
| `sample_value` | 只买便宜货 | 越跌越买/越摊越平（持续加仓将 INTC 摊成单一重仓） |
| `mock_trades` | 方法论建立期 | FOMO 纯 AI 假分散 + PLTR 加仓死扛 |

> 另有 4 组投资者画像扩展（`sample_ai_holder` / `sample_oldecon` / `sample_swing` / `sample_day_trader`，从长抱一年半的 AI 信仰者到同日进出的日内交易员） —— 运行方式与设计意图详见 [`mock/SAMPLES.md`](skills/fomo-kernel/mock/SAMPLES.md)。
> ⚠️ 引擎利用 yfinance 获取真实历史价格计算 Alpha/Beta、市值及套牢程度，**重新运行时的绝对数值会随当前股价发生漂移**；但每组样例设计的核心漏洞判定是稳定的（由交易行为决定，不依赖特定股价）。

## 結構

```
skills/fomo-kernel/
  SKILL.md                  ← 精简入口与不可违反的 Invariants
  flows/                    ← first / weekly / snapshot / test-drive 路由契约
  references/               ← Agent 边界、Thesis、卡片与 Recovery Policy
  schemas/                  ← Review Plan / answers / narrative / canonical bundle
  copy/                     ← 繁中、简中与英文产品 Copy
  engine/review.py          ← prepare / preview / finalize / resume
  engine/session.py         ← atomic canonical bundle + legacy projections
  engine/card_renderer.py   ← deterministic private/public Markdown + HTML
  engine/instruments.py     ← ETF 配置／集中风险 Policy
  card-spec.md              ← Step 3 卡规格（禁止清单 / redact / 叙事铁律；Step 2 问完后方可阅读）
  engine/trade_recap.py     ← 机械层：5 维 + 标的层主从分类 + 归因（纯函数，无真实路径）
  rubric/
    vincent-yu.md           ← Release 后研究笔记：意译原则 + 来源清单；现行 v2 不读取
    vincent-yu.lens.json    ← Release 后研究用 Schema 资产；未接入现行 v2 问题或卡片
  behavior-diagnosis.md     ← 诊断哲学：对事不对人、行为多标签（Why 的设计记录）
  card-template.html        ← 复盘卡 HTML 版面样例
  mock/                     ← 12 组 Sample + mock_trades + 各自 Driver Map + SAMPLES.md
```

## 免責

`rubric/` 内部是从公开文章蒸馏出的 Release 后研究资产。内容采用意译摘要并附带来源清单，非逐字引述、非转载、未经本人背书；现行 v2 亦不会将其加载为 Runtime Persona。
本工具定位为 **Research / Coaching Support**，所有输出仅作为交易行为回顾与纪律建议，**不构成投资建议，亦不涉及任何标的的买卖推荐**；最终投资决策与后果由使用者自行承担。
代码采用 [MIT License](LICENSE) 授权；`rubric/` 内部的意译研究内容附有来源清单，不随 MIT 许可证转授权。
