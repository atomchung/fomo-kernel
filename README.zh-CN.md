# FOMO Kernel

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2.svg)](skills/fomo-kernel)
[![Engine: Deterministic](https://img.shields.io/badge/Engine-Deterministic-green.svg)](skills/fomo-kernel/engine)

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> **一个本地、以证据为边界的交易决策伙伴。** 把你正在考虑的交易，或已经做过的交易带进来。FOMO Kernel 会降低你的决策负担，但不会替你做最后决定。

它服务两个核心时刻：

- **交易前：** 先看这笔交易会如何改变当前记录的持仓，再挑战你现在出手的理由。
- **交易后：** 从行为中找出最值得处理的一件事，亲自选择一条下次可验证的规则。

数字、排名、组合影响与状态转换由确定性 Python 引擎负责。Agent 只处理程序无法替你决定的有限判断：你的动机、最强反方论点，以及直接说明“现在真正重要的是什么”。

## 从你现在的时刻开始

| 你现在遇到的事 | 最少需要提供 | 第一个有用结果 |
|---|---|---|
| **“我该买、加仓、减仓，还是先不动？”** | 计划动作、当前理由，以及现在发生了什么变化 | 已有记录持仓时：精确的交易后权重、持仓之间隐藏的集中／重叠、现金影响、规则冲突、最关键的取舍与最强反方。 |
| **同一个决策，但还没有记录持仓** | 决策、理由与为什么是现在 | 不会直接拒绝，而是给出一个有边界的决策框架：最强正方、最强反方、真正需要回答的关键问题，以及哪些组合事实尚未检查。没有虚构数字，默认也不持久化。 |
| **“帮我复盘最近的交易。”** | 券商 CSV 或交易导出 | 一张聚焦的行为复盘卡：做对的一件事、最大且有证据的漏洞、会改变判断的动机问题，以及最多一条你亲自选择的规则。 |
| **“我只有持仓截图。”** | 持仓表或券商对账单截图 | 开场结构检查：权重、单一持仓风险、驱动集中、ETF 结构与数据完整性限制。不虚构交易历史。 |
| **“先让我看看体验。”** | 不需要私人数据 | 使用虚构数据的隔离 test drive，不会写入你的真实教练记忆。 |

## 最快获得价值的路径

### 1. 安装

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

问题与卡片支持英文、繁体中文与简体中文。切换语言只改变文案，不改变引擎事实。

## 产品实际会做什么

### 交易前：挑战决策，而不是评论 ticker

当已有记录持仓时，FOMO Kernel 会在 Agent 论证前先计算这笔计划交易的后果：

- 交易后持仓权重；
- 集中度，以及依赖同一驱动因素的持仓重叠；
- 现金影响；
- 与已有个人规则的冲突；
- 上述事实使用的组合基础与限制。

回答会先说明最关键、且有证据的取舍，再给出最强反方，并把限制放在它真正限制的主张旁边。最后动作仍由你决定。

当没有记录持仓时，对话也不会停在拒绝。FOMO Kernel 只询问会改变判断的少数问题，明确说明哪些组合事实尚未检查，并指出下一份数据具体能换来什么更精确的答案。它不会用泛化投资建议填补缺失数字。

### 交易后：把行为收敛成一个可验证改变

交易历史复盘会先运行确定性诊断，再询问少数引擎无法知道的动机问题，最后渲染一张聚焦卡片。

卡片收敛为：

1. 你做对的一件事；
2. 最大且有证据的行为漏洞；
3. 最多一条由你选择、自定义，或跳过的规则。

下次复盘会先对账上次那条规则，而不是再次把你当成新用户。

### 从持仓快照开始

持仓表或截图是更轻量的 onboarding。Agent 只抄录券商显示的事实；权重、风险、cycle identity 与 ETF 处理都由引擎计算。

快照可以支持开场结构检查，但不能诚实推断过去是否摊平、出场纪律、持有行为、胜率、payoff、alpha 或历史动机。之后加入交易历史，才会解锁这些有数据基础的判断。

## 复盘卡长什么样

以下 committed demo 使用完全虚构的数据：

![fomo-kernel review card demo](docs/demo-card-en.png)

可打开同步的[英文 HTML demo](docs/demo-card-en.html)或[繁体中文 HTML demo](docs/demo-card.html)。

图片只展示结果卡；真实复盘流程会先询问动机问题，而你的回答可能改变最终判断。Mock 刻意高度集中，其中的 alpha 数字不是可泛化的绩效主张。

交易前回答默认保持简短文字，除非你明确要求更多。

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

实际使用规则：

- 下周重新导出完整交易历史是安全的；重叠数据会自动去重。
- 更新的持仓视图会先与现有记录对账，不会静默覆盖。
- 中断后会恢复 pending session，而不是重新抓取已经回答过的 live facts。
- canonical session 已成功 commit、但 projection 失败时，可以重建 projection，不必重新提问。
- 推断出的 thesis 会持续标记为 inferred，直到你确认或修正。

## 其他 coding agent

Claude Code 提供最直接的 slash-command 安装。Codex、Cursor 与兼容的 coding agent 可以打开 repository，按照 [`AGENTS.md`](AGENTS.md) 进入同一份 host-neutral contract，再由它路由到 [`skills/fomo-kernel/SKILL.md`](skills/fomo-kernel/SKILL.md)。

目前 owner-live acceptance 聚焦 Claude Code 与 Codex。能够兼容运行，不代表已经完成产品验收。

## 平台支持

- Python 3.11+。
- macOS 与 Linux 支持 durable session finalization。
- Windows 可以运行 `prepare` 与 `preview`；但 durable `finalize` 目前会在改动已提交的 canonical state 前 fail closed，因为实现依赖 POSIX locking 与 directory `fsync`。

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

提交 repository 改动前：

```bash
python3 tests/run_all.py
```

公开示例与 fixture 必须保持 synthetic。授权见 [MIT License](LICENSE)。
