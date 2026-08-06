# FOMO Kernel

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2.svg)](skills/fomo-kernel)
[![Engine: Deterministic](https://img.shields.io/badge/Engine-Deterministic-green.svg)](skills/fomo-kernel/engine)

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> **一个直接、以证据为边界、在本地运行的交易决策伙伴。** 把你正在考虑的交易，或已经做过的交易带进来。FOMO Kernel 会降低你的决策负担，但不会替你做最后决定。

它服务两个核心时刻：

- **交易前：** 先看这笔交易会如何改变当前记录的持仓，再挑战你现在出手的理由。
- **交易后：** 从行为中找出最值得处理的一件事，亲自选择一条下次可验证的规则。

数字、排名、组合影响与状态转换由确定性 Python 引擎负责。Agent 只处理程序无法替你决定的有限判断：你的动机、最强反方论点，以及直接说明“现在真正重要的是什么”。

## 从你现在的时刻开始

| 你现在遇到的事 | 最少需要提供 | 第一个有用结果 |
|---|---|---|
| **“我该买、加仓、减仓，还是先不动？”** | 计划动作、当前理由，以及现在发生了什么变化 | 已有记录持仓时：精确的交易后权重、持仓之间隐藏的集中／重叠、现金影响、规则冲突、最关键的取舍与最强反方。 |
| **同一个决策，但还没有记录持仓** | 决策、理由与为什么是现在 | 不会直接拒绝，而是给出一个有边界的决策框架：最强正方、最强反方、真正决定这笔交易的关键问题，以及哪些组合事实尚未检查。没有虚构数字，也不会持久化。 |
| **“帮我复盘最近的交易。”** | 券商 CSV 或交易导出 | 一张聚焦的行为复盘卡：做对的一件事、最大且有证据的漏洞、会改变判断的动机问题，以及最多一条你亲自选择的规则。 |
| **“我只有持仓截图。”** | 持仓表或券商对账单截图 | 开场结构检查：权重、单一持仓风险、驱动集中、ETF 结构与数据完整性限制。不虚构交易历史。 |
| **“先让我看看体验。”** | 不需要私人数据 | 使用虚构数据的隔离 test drive，不会写入你的真实教练记忆。 |

## 实际使用 FOMO Kernel 的体验

### 1. 直接用自然语言开始

你不需要先选择内部模式，也不需要先学习流程。说出正在面对的决策、附上手边的记录，或要求 test drive 即可。

FOMO Kernel 会针对当下使用最窄、但仍有价值的路径。即时交易决策保持简短对话；交易历史值得一张完整复盘卡；只有持仓截图时，就进行结构检查，不虚构历史判断。

### 2. 引擎先建立事实，Agent 才开始解读

组合数学、排名、规则冲突、identity 与持久状态都由引擎负责。Agent 不能偷偷补一个缺失价格、重新计算权重，或发明交易历史。

因此对话有稳定的基础：记录实际说明了什么、Agent 认为这代表什么、还有哪些事情不知道，三者保持可区分。

### 3. 只问程序无法知道的事

一笔可疑加仓可能是信念，也可能是不愿止损；提前卖出盈利仓位可能是纪律，也可能是害怕回吐。程序可以找出张力，但只有你能说明动机。

问题因此聚焦在：为什么是现在、什么真的发生变化、什么会证明 thesis 错了、当时到底是什么驱动行动。它不是一份泛化投资人格问卷。

### 4. 先看到有用结果，再要求下一项承诺

交易前回答会先说明真正影响决策的张力与最强反方，不会先塞满工具流程或 caveat。

复盘的设计是先把完整卡片显示在对话中，再请你选择规则 — 单纯生成文件不算交付；结果必须真正到达你面前。这个交付契约目前在 Claude Code 上已完整验证。在没有原生选项控件与对话内丰富渲染的 host 上，交付还没有验证到相同标准；详见下方“版本与已知限制”。

### 5. 最终动作仍由你负责

对一笔正在考虑的交易，FOMO Kernel 可以记录“曾经考虑过什么”，但不会把它称为已经执行。它不提供目标价，也不替你选择要买卖哪一只股票。

复盘时，你可以选择一条候选规则、自定义一条，或跳过。产品不会为了完成流程而捏造承诺。

### 6. 下一次对话从上一次开始

下次复盘时，FOMO Kernel 会先检查上次选择的规则，并沿用已经确认的 thesis 与记录持仓。重新上传完整交易历史是安全的，重叠数据会自动去重。新的持仓视图会先与记录持仓比较，不会静默替换。

价值不在于建立更大的数据库，而在于连续性：**当时相信什么 → 实际做了什么 → 后来改变什么 → 哪条规则值得保留。**

## 复盘卡长什么样

以下 committed demo 使用完全虚构的数据：

![fomo-kernel review card demo](docs/demo-card-en.png)

可打开同步的[英文 HTML demo](docs/demo-card-en.html)或[繁体中文 HTML demo](docs/demo-card.html)。

图片只展示结果卡；真实复盘流程会先询问动机问题，而你的回答可能改变最终判断。Mock 刻意高度集中，其中的 alpha 数字不是可泛化的绩效主张。

交易前回答默认保持简短文字，除非你明确要求更多。

## 最快获得价值的路径

### 1. 安装

```bash
git clone https://github.com/atomchung/fomo-kernel
cd fomo-kernel

python3 -m venv .venv
source .venv/bin/activate
pip install -r skills/fomo-kernel/requirements.txt

python3 skills/fomo-kernel/engine/review.py doctor
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/fomo-kernel" ~/.claude/skills/fomo-kernel
```

请从已启用虚拟环境的终端启动 Claude Code。

### 2. 带入一个真实决策，或一份真实记录

在 Claude Code 中：

```text
/fomo-kernel 我正在考虑加仓 20 股 NVDA。
我现在的理由是……，而这次真正发生变化的是……

/fomo-kernel ~/Downloads/trades.csv

/fomo-kernel
接着附上持仓表或券商对账单截图。

/fomo-kernel
没有文件时，选择虚构数据 test drive。
```

你不需要自己清理券商导出。Agent 会在本地将其映射为引擎需要的数据契约。

问题与卡片支持英文、繁体中文与简体中文（`--language en|zh-TW|zh-CN`）。切换语言只改变文案，不改变引擎事实。

## 不同输入能解锁什么

### 一笔正在考虑的交易 + 已有记录持仓

FOMO Kernel 会计算交易后权重、集中度、依赖同一驱动因素的持仓重叠、现金影响、规则冲突，以及这些事实使用的组合基础。回答再从冻结结果出发，提出最强正方与最强反方。

### 一笔正在考虑的交易 + 没有记录持仓

对话仍会继续，但不会假装知道权重、集中度、现金或规则冲突。它只询问会改变判断的少数问题，并说明下一份证据能换来什么更具体的答案。这条路径不会持久化任何内容。

### 交易历史

交易导出可以支持跨时间的行为判断：仓位大小、摊平、出场、分散、持有一致性、逐标的诊断，以及数据足够时的绩效归因。引擎只挑选少数值得追问的动机；最终复盘收敛成一张卡与最多一条由用户选择的规则。

### 持仓快照

持仓表或截图可以支持开场结构检查，但不能诚实推断过去是否摊平、出场纪律、持有行为、胜率、payoff、alpha 或历史动机。之后加入交易历史，才会解锁这些判断。

## 它和一般聊天有什么不同

一般聊天可以讨论 thesis；FOMO Kernel 多了一个可执行、可审计的决策契约：

| 层 | 谁负责 |
|---|---|
| 组合数学、排名、规则、identity 与状态转换 | 确定性引擎 |
| 动机追问、有边界的解读、最强反方、白话说明 | Agent |
| 最后动作、确认，以及规则是否保留 | 用户 |
| 持久历史与 replay | 本地 canonical session bundle |

这个分工避免 Agent 悄悄变成第二套组合事实来源。

## 隐私与真实性边界

- **没有 FOMO Kernel backend。** Repository 没有账号服务或上传端点，也不会把任何内容发送给作者。
- **文件与状态留在本地。** 来源文件、正规化快照、canonical session、私人卡片与 projection 都保存在运行 skill 的机器上。
- **公开市场数据。** 为计算支持的价格与收益，引擎可能向市场数据供应商查询公开 ticker 与日期；不会发送券商交易行、数量、成本、动机或卡片。
- **你选择的 AI host 仍然重要。** 你明确交给模型／client 的内容，仍依该 host 自己的条款处理；FOMO Kernel 不会再增加一个服务器，也不会暗中公开数据。
- **没有 cloud OCR 路径。** 截图由 coding agent 从本地附件抄录；引擎不会上传到 OCR 服务。
- **默认私人。** 正常输出是 `card-private.*`。你可以要求分享安全版 `card-public.md`；它会移除金额、日期、ticker、精确权重、session ID 与 Agent 自由文本，而且不会自动发布。
- **公开 repository 只允许 synthetic evidence。** 不要把真实交易、持仓、动机或卡片贴到公开 issue 或 PR。

## 本地记忆、重复使用与恢复

完成的复盘会保存为 immutable canonical session：

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

`data-status` 只列出文件状态与 metadata，不打印交易内容。导出的备份应像券商对账单一样保管；`data-reset --confirm` 不可逆。

中断后，Agent 会恢复 pending session，而不是重新抓取你已经回答过的事实。canonical session 已成功 commit、但衍生 projection 失败时，可以重建 projection，不必重新提问。

## 其他 coding agent

Claude Code 提供最直接的 slash-command 安装。Codex、Cursor 与兼容的 coding agent 可以打开 repository，按照 [`AGENTS.md`](AGENTS.md) 进入同一份 host-neutral contract，再由它路由到 [`skills/fomo-kernel/SKILL.md`](skills/fomo-kernel/SKILL.md)。

若要通过 host-neutral CLI 启动 test-drive plan：

```bash
python3 skills/fomo-kernel/engine/review.py prepare --test-drive --language zh-CN
```

这个命令会返回 Review Plan；Agent 再按照它选择的 flow 呈现并完成体验。

引擎与离线测试套件已完成机械验证；owner-live acceptance — 也就是在真实决策中确认实用性、交付与延迟 — 不论在 Claude Code、Codex 还是其他 host 上都还没有发生。能够兼容运行，不代表已经完成产品验收。

## 平台支持

- Python 3.11+。
- macOS 与 Linux 支持 durable session finalization。
- Windows 可以运行 `prepare` 与 `preview`；但 durable `finalize` 目前会在改动已提交的 canonical state 前 fail closed，因为实现依赖 POSIX locking 与 directory `fsync`。

## 版本与已知限制

这是 **v0.1.0**，FOMO Kernel 第一个打 tag 的版本。这是早期软件：引擎契约与离线测试套件是稳定的部分，实时对话体验还没有经过 owner-live acceptance。

host 支持分级：

- **Claude Code** — 交付机制已验证的路径：slash-command 安装、原生选项控件、对话内卡片交付都能完整运作。这是机制层的陈述，不是“答案有用”的裁决。
- **Codex、Cursor 与其他兼容的 coding agent** — 引擎、CLI 与 [`AGENTS.md`](AGENTS.md) 里的 host-neutral contract 都能运作。交互式选项控件与对话内卡片交付在这些 host 上**还没有**验证；见 [issue #230](https://github.com/atomchung/fomo-kernel/issues/230)。本地卡片文件无论如何都会写入。

已知缺陷请查 [GitHub issue tracker](https://github.com/atomchung/fomo-kernel/issues)，不在这里列清单 — 清单会随每次修复变动。

## FOMO Kernel 不做什么

FOMO Kernel 不会：

- 提供目标价或市场预测；
- 替你选股；
- 替你做出或执行最后买卖决定；
- 变成券商、财富管理或完整 investment OS；
- 爬取或镜像你的私人研究 repository；
- 用泛化建议替代缺失的组合事实。

它是研究与决策教练工具，不是投资建议。所有投资决定与结果仍由你负责。

## 给 contributor 与 maintainer

请先阅读：

- [`AGENTS.md`](AGENTS.md) — 路由与不可妥协的边界；
- [`docs/issue-lifecycle.md`](docs/issue-lifecycle.md) — context 加载与 issue owner；
- [`docs/maintainer-guide.md`](docs/maintainer-guide.md) — 开发、隐私、测试、mirrored surfaces 与 PR 规范。

开 pull request 前：

```bash
python3 tests/run_all.py --group product
```

公开示例与 fixture 必须保持 synthetic。授权见 [MIT License](LICENSE)。