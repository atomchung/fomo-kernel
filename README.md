# FOMO Kernel

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2.svg)](skills/fomo-kernel)
[![Engine: Deterministic](https://img.shields.io/badge/Engine-Deterministic-green.svg)](skills/fomo-kernel/engine)

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> **A local, evidence-bound trading decision partner.** Bring the trade you are considering or the trades you already made. FOMO Kernel reduces your decision burden without making the decision for you.

It is built for two moments:

- **Before a trade:** challenge what you are about to do against your actual recorded book.
- **After trades:** review what your behavior says, then choose one rule worth checking next time.

Numbers, rankings, portfolio effects, and state transitions come from a deterministic Python engine. The agent handles the bounded work code cannot settle: your motive, the strongest counter-case, and a direct explanation of what matters.

## Start from the moment you are in

| Your moment | Minimum input | First useful outcome |
|---|---|---|
| **“Should I buy, add, trim, or wait?”** | The contemplated action plus your current reason and what changed now | With a recorded book: exact post-trade weight, concentration/driver overlap, cash effect, rule collisions, one lead tension, and a real rebuttal. |
| **Same decision, but no book recorded yet** | Your decision, reason, and why now | A bounded decision framing instead of a refusal: strongest case, strongest counter-case, the key question your decision depends on, and an explicit statement of what was not checked. No invented portfolio numbers and nothing persisted by default. |
| **“Review my recent trades.”** | A broker CSV or transaction export | One behavior-review card: what you did right, your largest supported leak, the motive question that changes the read, and at most one rule you choose. |
| **“I only have a holdings screenshot.”** | A position table or statement screenshot | An opening structural check: weights, single-position risk, driver concentration, ETF structure, and data-integrity limits. It does not invent transaction history. |
| **“Show me the experience first.”** | No personal data | An isolated test drive using fictional data. It never writes to your real coach memory. |

## Fastest path to value

### 1. Install

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

Launch Claude Code from a terminal where the virtual environment is active.

### 2. Bring one real decision or one real record

Inside Claude Code:

```text
/fomo-kernel I'm considering adding 20 shares of NVDA.
My current reason is ..., and what changed now is ...

/fomo-kernel ~/Downloads/trades.csv

/fomo-kernel
Then attach a holdings table or statement screenshot.

/fomo-kernel
With no file, choose the fictional test drive.
```

You do not need to clean a broker export by hand. The agent maps it locally into the engine contract.

## What the product actually does

### Before a trade: challenge the decision, not the ticker

When a recorded book exists, FOMO Kernel computes the contemplated trade's consequence before the agent argues either side:

- resulting position weight;
- concentration and driver overlap;
- cash effect;
- collisions with rules you already track;
- the portfolio basis and limitations behind those facts.

The answer then leads with one supported decision tension, gives the strongest counter-case, attaches limitations to the claims they qualify, and leaves the action to you.

When no book exists, the conversation still advances. FOMO Kernel asks only the few questions that change the framing, states which portfolio facts were not checked, and names the next piece of evidence that would buy a more specific answer. It does not fill missing numbers with generic investment advice.

### After trades: turn behavior into one checkable change

A transaction-history review runs a deterministic diagnosis, asks the small number of motive questions the engine cannot answer, and renders one focused card.

The card converges on:

1. one thing you did right;
2. the largest supported behavioral leak;
3. at most one rule you choose, write yourself, or skip.

On the next review, FOMO Kernel starts by reconciling that prior rule instead of treating you like a new user.

### Starting from a snapshot

A position table or screenshot is a lighter onboarding route. The agent transcribes only broker-declared facts; the engine calculates weights, risk, cycle identity, and ETF treatment.

A snapshot can support an opening structural check. It cannot honestly reveal prior averaging down, exit discipline, holding behavior, win rate, payoff, alpha, or historical motive. Add transaction history later to unlock those claims.

## What a review card looks like

The committed demo uses fictional data:

![fomo-kernel review card demo](docs/demo-card-en.png)

Open the synchronized [English HTML demo](docs/demo-card-en.html) or [Traditional Chinese HTML demo](docs/demo-card.html).

The image is the review route. Pre-trade answers stay brief and textual unless you explicitly ask for more.

## Why this is different from ordinary chat

A general chat can discuss a thesis. FOMO Kernel adds an enforced decision contract:

| Layer | Owner |
|---|---|
| Portfolio math, rankings, rules, identities, and state transitions | Deterministic engine |
| Motive questions, bounded interpretation, strongest counter-case, plain-language explanation | Agent |
| Final action, confirmation, and whether a rule should be kept | User |
| Durable history and replay | Local canonical session bundles |

That separation prevents the agent from quietly becoming a second source of portfolio truth.

## Privacy and truth boundaries

- **No FOMO Kernel backend.** The repository has no account service or upload endpoint, and nothing is sent to the author.
- **Local files and state.** Source files, normalized snapshots, canonical sessions, private cards, and projections live on the machine running the skill.
- **Your AI host still matters.** The model/client you choose may process content you explicitly provide under that host's own terms. FOMO Kernel does not add another server or silently publish the data.
- **No cloud OCR path.** A screenshot is transcribed by the coding agent from the local attachment; the engine does not upload it to an OCR service.
- **Private by default.** `card-private.*` is the normal output. Ask for a share-safe version to receive `card-public.md`, which removes amounts, dates, tickers, exact weights, session IDs, and agent free text. Nothing is published automatically.
- **Public repository evidence is synthetic only.** Never post real trades, holdings, motives, or cards to a public issue or PR.

## Local memory, repeat use, and recovery

Completed reviews are stored as immutable canonical sessions:

```bash
ls ~/.trade-coach/sessions/
```

Useful controls:

```bash
python3 skills/fomo-kernel/engine/coach.py data-status
python3 skills/fomo-kernel/engine/coach.py data-export --out backup.zip
python3 skills/fomo-kernel/engine/coach.py data-reset --dry-run
python3 skills/fomo-kernel/engine/coach.py data-reset --confirm
```

Treat an exported backup like a brokerage statement.

Practical rules:

- Re-exporting full transaction history next week is safe; overlapping rows are deduplicated.
- A newer holdings view is reconciled against the recorded book instead of silently replacing it.
- After interruption, the agent resumes the pending session instead of refetching live facts.
- If a canonical session committed but a projection failed, the projection can be rebuilt without re-questioning you.
- Inferred theses remain marked as inferred until you confirm or revise them.

## Other coding agents

Claude Code provides the simplest slash-command installation. Codex, Cursor, and compatible coding agents can use the same host-neutral contract by opening the repository and following [`AGENTS.md`](AGENTS.md), which routes to [`skills/fomo-kernel/SKILL.md`](skills/fomo-kernel/SKILL.md).

Current owner-live acceptance focuses on Claude Code and Codex. A compatible client is not automatically an accepted client.

## Platform support

- Python 3.11+.
- macOS and Linux support durable session finalization.
- Windows can run the non-mutating preparation/preview path, but durable `finalize` currently fails closed before canonical state is changed because the implementation requires POSIX locking and directory `fsync`.

## What FOMO Kernel is not

FOMO Kernel does not:

- issue price targets or market forecasts;
- select stocks for you;
- make or execute the final buy/sell decision;
- become a broker, wealth manager, or full investment operating system;
- crawl or mirror your private research repository;
- replace missing portfolio facts with generic advice.

It is research and decision-coaching support, not investment advice. You remain responsible for every investment decision and outcome.

## For contributors and maintainers

Start with:

- [`AGENTS.md`](AGENTS.md) — routing and non-negotiable boundaries;
- [`docs/issue-lifecycle.md`](docs/issue-lifecycle.md) — current-context loading and issue ownership;
- [`docs/maintainer-guide.md`](docs/maintainer-guide.md) — development, privacy, tests, mirrored surfaces, and PR conventions.

Before committing repository changes:

```bash
python3 tests/run_all.py
```

The public examples and fixtures must remain synthetic. See the [MIT License](LICENSE).
