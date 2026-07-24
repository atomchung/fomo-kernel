# FOMO Kernel

**English** · [繁體中文](README.zh-TW.md)

> A local, agent-assisted trade-review skill for Claude Code, Codex, Cursor, and compatible coding agents. It turns your real trades into **one review card** through deterministic diagnosis and a short judgment dialogue —
> the one thing you did right + your biggest leak (in your own numbers) + one checkable rule you choose. On the next review, it starts by reconciling whether you kept that rule.

Not another stats report. It does what a report can't: **first it computes the behavioral leaks you can't see, then it asks the motive you won't admit, then it converges on one checkable change you choose and checks it next review.**

> 📝 **Language.** The same review contract renders in Traditional Chinese, Simplified Chinese, or English (`--language zh-TW|zh-CN|en`); any other language tag falls back to English. Translation changes the questions and card copy, not the engine facts or analysis policy.

## Quick start

**The full flow (this is the actual product) — inside Claude Code:**
```
/fomo-kernel ~/Downloads/my.csv   # review your own trades (CSV from any broker)
/fomo-kernel <attached position table or statement screenshot>   # opening portfolio check
/fomo-kernel                      # no file → asks you for one, or offers a "test drive" on built-in fake data (nothing written to coach memory)
```
The card's value is in step ② — the engine flags a suspicious position and asks *"averaging down on conviction, or refusing to cut a loser?"*; your one-sentence answer is what turns the raw diagnosis into a verdict. **You can't see that layer from the engine's raw output alone.** Install steps under [Install](#install).

A position table or screenshot takes the narrower snapshot route. It creates an opening portfolio check from cost or value weights, single-position risk, driver concentration, ETF structure, and data-integrity limits. It does not pretend that one snapshot reveals prior averaging down, exits, holding behavior, win rate, payoff, alpha, or historical motives. Add transaction history later to unlock supported historical diagnostics; it does not by itself reconcile a fresh broker view, so ledger-derived current holdings remain canonical.

**Want zero-install, just to see the stable flow start:**
```bash
git clone https://github.com/atomchung/fomo-kernel && cd fomo-kernel
pip install -r requirements.txt      # if it errors with externally-managed-environment → see the venv steps under Install
cd skills/fomo-kernel && python3 engine/review.py prepare --test-drive --language en
# emits a resumable Review Plan; required motive questions come before preview/finalize
```

## What it looks like

Running the built-in mock, the **illustrative card** looks like this (below is a simplified quick-view; the finished review card is rendered only after the required motive questions and one-rule choice):

```text
Review card · mock sample
On paper you're up +$138k, but almost all of it is "held and never sold";
your active trades are what need discipline, not luck.

  Total P&L             +$138,058    (realized $19k + unrealized $119k)
  Active win/loss ratio  2.9         (avg win $2,851 vs avg loss $1,000)
  Beat the market +247pp · β 2.04 · AI exposure 98% (30% drawdown = −$50k)
      └ splitting "beat the market" into luck vs skill: right sector +67pp + picking within the sector +181pp

  Data notes:
  - the α interval is still wide — can't yet tell skill from luck; don't take the demo literally.

Per-position diagnosis (sorted by size; small lots not nitpicked):
  PLTR  +$74,058   [v] likely DCA (buys up and down, not averaging a loser) · [!] too heavy 50%
  NVDA  +$56,412   [v] likely DCA · [!] too heavy 46%
  ORCL   +$1,658   [v] disciplined hold: +22%
  AMD    -$1,000   --  roughly neutral

[v] What you did right: you averaged down twice, but both times stayed within your position cap — no ticker got averaged into an oversized position
[X] Biggest leak: position sizing — largest single lot PLTR is 50%, the rest average 17%
[*] Change only this next time: hard-cap any single position at 20% — trim if it goes over
```

A synchronized dark-card mock is available as [English HTML](docs/demo-card-en.html) and [Traditional Chinese HTML](docs/demo-card.html).

![fomo-kernel review card demo](docs/demo-card-en.png)

> In real use, the engine also flags positions that are "large + being averaged down while underwater" and asks you, *before* the card is issued, "is this a dip-buy or refusing to cut a loser?" — the motive a machine can't tell apart, settled by your one sentence, is what lets the card reach a verdict.
> ⚠️ The mock's α numbers are distorted (too concentrated, too narrow a cross-section) — don't take them literally; real α needs a genuinely diversified book.

---

## How it differs from "pasting your statement into ChatGPT"

ChatGPT can't compute the real FIFO-matched α/β, can't tell "DCA" from "averaging a loser," and has none of your history. This skill layers three passes:

1. **Mechanical layer (Python, deterministic)** — computes what ChatGPT only estimates:
   - 5-dimension behavioral diagnosis: position sizing / averaging down / exit / diversification / holding consistency
   - **Per-position diagnosis**: every ticker ranked **by dollar size** (small lots not nitpicked), with a classifier splitting "likely DCA vs likely averaging-a-loser vs unclear"
   - **Return attribution**: splits "beat the market" into "right sector (luck / direction)" vs "stock picking (skill)" — so you see whether the gains were edge or nerve
2. **Judgment-dialogue layer (engine signals × your intent)** — the "why" a machine can't infer, asked before the card is issued:
   - Thesis check: "MSTR — you kept adding and it's still down. Do you still believe the thesis, or just won't book the loss?"
   - Motive: "You sold a winner early — was the thesis at target, or were you afraid of giving back the gain?"
   - **The engine picks the few positions worth asking about; your answer sets the read** — the machine is always guessing, your one sentence decides
3. **One-rule layer** — turns the verdict into a small candidate set. You choose one candidate, write a custom rule, or skip; the next review checks the same rule instead of starting from zero.

→ It all converges into **one card**: one leak, one checkable rule for next time. Come back a second time and it first reconciles "did you keep that rule?"

## 🔒 Privacy: no backend upload, the author can't see it

- The skill runs your CSV or normalized position snapshot **on your own machine** — **no upload to any backend, no storage anywhere else, nothing sent to the author**. For weekly reconciliation it does save review-derived state **locally** under `~/.trade-coach/` (never sent anywhere) — see the next section for exactly what that is and how to inspect, export, or wipe it.
- The author can't see your trade detail. The only (voluntary) thing collected back is a single "was this card useful?" — no trade content — via the [card feedback form](https://github.com/atomchung/fomo-kernel/issues/new?template=card-feedback.yml), 30 seconds if you're willing.
- `.gitignore` is set so **no `.csv` is ever committed**, with only the mock/sample fixtures excepted.
- Precisely: the local Python engine reads a normalized trade CSV or snapshot JSON envelope. The coding agent you invoke may read a source table or screenshot locally to transcribe broker-declared facts, but it does not use engine OCR or a cloud OCR/upload path. It keeps the temporary normalized JSON outside the repository (for example under `/tmp`) and never calculates weights or hand-builds card/state artifacts. Nothing is sent to the author. This differs from handing a statement to a SaaS whose retained data you cannot inspect.

## 📁 Where your coach memory lives / how to maintain it

On your second visit, the card first reconciles "did you keep last time's rule?" The canonical record is one immutable directory per review:

```bash
ls ~/.trade-coach/sessions/       # bundle + state + answers + cards + hash manifest
```

Legacy tools remain compatible through rebuildable projections:

```bash
cat ~/.trade-coach/log.jsonl       # one line per review (thin metrics + the rule you committed to); empty = first time
cat ~/.trade-coach/theses.jsonl    # per-position "why I hold + what would prove me wrong" (append-only, never overwritten)
cat ~/.trade-coach/profile.md      # your trading goals + 3 personal principles (the baseline each review compares against)
cat ~/.trade-coach/last_state.json # the thin state the engine last computed (per-position shares/cost, for reconciliation; overwritten each run)
```

The engine keeps a handful of other derived files there too (a trade ledger, an exit-tracking queue, a problem/rule log, your saved review cards) — rather than trusting a prose list to stay complete, the CLI below is the single source of truth for exactly what's on disk right now:

```bash
python3 skills/fomo-kernel/engine/coach.py data-status               # every known path: exists? size? line count? (never prints your trade content)
python3 skills/fomo-kernel/engine/coach.py data-export --out backup.zip   # bundle everything that exists into one zip (contains sensitive trade-derived data — treat it like a brokerage statement)
python3 skills/fomo-kernel/engine/coach.py data-reset --dry-run      # preview what a reset would delete
python3 skills/fomo-kernel/engine/coach.py data-reset --confirm      # actually delete it all (not reversible)
```

- **Coming back next week — which CSV do I import?** Just export your **full history** again and hand it over — you never track increments by hand. Rows that overlap with earlier imports are auto-deduplicated (that's exactly what the dedup is for), so **dumping the whole statement every week is safe**; the engine uses last review's cutoff to tell what's new, and the card opens by reconciling against the rule you committed to last time.
- **What does a snapshot anchor?** A complete initial snapshot may establish the ledger's accounting anchor. An incomplete snapshot can still produce a bounded review but is not written as an anchor. Later transaction files can unlock supported historical analysis while ledger-derived holdings stay canonical. Comparing a second or subsequent snapshot with that ledger, showing the diff, and writing an adjustment is explicitly deferred P1 work; current-view claims that need that reconciliation stay unavailable.
- **See past reviews** → `cat ~/.trade-coach/log.jsonl`.
- **Start over / reset the reconciliation baseline** → `coach.py data-reset --confirm` (or delete/rename `~/.trade-coach/` by hand — either way, next time is a fresh first visit).
- **Wrote a thesis wrong** → correct it in the next review; the new event points to the earlier thesis. Do not hand-edit `theses.jsonl`: it is now a rebuildable projection of canonical sessions.
- **Privacy, self-verifiable**: coach memory is just the files `data-status` lists above, all on your machine; there isn't a single row on the author's side.
- **Want to preview the multi-week loop first** (runs entirely in a temp directory, **never touches** your real `~/.trade-coach/`) → `python3 skills/fomo-kernel/engine/demo_weeks.py`: slices the built-in mock into 3 time windows to simulate "first visit → reconcile → reconcile", so you can watch the second card cite last week's commitment and `log.jsonl` grow line by line.

> 💡 **Want to share with a community?** Each committed review creates `card-public.md`, a separately rendered artifact. A transaction-history review may keep a de-identified behavior pattern plus engine-owned beta and benchmark-excess percentage points; a snapshot review keeps only fixed structural-baseline copy and does not imply historical behavior. Both remove amounts, dates, tickers, exact weights, and agent free text. The review card remains the default response. This is a local file for manual sharing; there is no upload or publishing feature yet.

## Install

**Prerequisite:** Python 3.11+. Durable session finalization currently requires POSIX `flock` and directory `fsync` (macOS/Linux); Windows fails closed with a controlled CLI error before canonical session storage is mutated. Claude Code users can install the slash-command skill below; Codex, Cursor, and other agents can use the repo directly through `AGENTS.md` and `engine/review.py` without a Claude subscription.

Needs Python 3.11+. **On recent macOS (Homebrew / system Python) a bare `pip install` is blocked by PEP 668** (`externally-managed-environment`); use a venv:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                            # yfinance + pandas + rich
python3 -c "import yfinance, pandas, rich; print('ok')"    # verify: only 'ok' means it installed
```
Hook the skill into Claude Code (pick one):
```bash
ln -s "$(pwd)/skills/fomo-kernel" ~/.claude/skills/fomo-kernel   # A. symlink (recommended)
cp -r skills/fomo-kernel ~/.claude/skills/                         # B. copy (to hand to someone)
```
> ⚠️ If you installed via venv, Claude Code needs those dependencies on its path when it runs the engine: launch `claude` from a **terminal with the venv activated**, or when the engine reports `ModuleNotFoundError`, swap `python3` for `.venv/bin/python3` and re-run (SKILL.md has this fallback built in).

## Usage

Inside Claude Code:
```
/fomo-kernel ~/Downloads/my.csv   # transaction-history review
/fomo-kernel <attached position table or statement screenshot>   # opening portfolio check
/fomo-kernel                      # no file → asks you for one, or a "test drive" through all four steps on built-in fake data (labeled as demo, nothing written to coach memory)
```
Your CSV can come from **any broker** — Claude reads and maps it into the columns the engine needs (`Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate`, plus optional `Market / Currency` for non-US stocks — e.g. `2330.TW / TW / TWD`; omitted = US/USD); you don't hand-clean anything.

For a position table or screenshot, the agent locally transcribes only the displayed facts into a normalized JSON envelope (`as_of`, `positions`, and optional cash, FX, and completeness facts), keeps that temporary file outside the repository, and sends it through `review.py`. The engine, not the agent, calculates weights, cycle IDs, risk metrics, and ETF treatment. The opening snapshot initializes inferred theses for uncovered positions; only a complete initial snapshot may establish an accounting anchor. Later transaction history can unlock supported behavioral diagnostics, but does not claim that ledger holdings match a newer broker view.

> 🏷️ For **obscure tickers**, the agent may propose a local driver map for sector/theme exposure. For obscure ETFs it may also propose an instrument map, but the code grants an allocation exemption only to explicit broad-market, regional, bond, or commodity classifications; unknowns remain concentrated by default.

**What happens**: ① `prepare` runs the deterministic diagnosis and builds a question queue → ② the agent asks every returned question and creates any required inferred theses (a snapshot may return no motive questions) → ③ `preview` validates the artifacts and renders a card → ④ you choose at most one rule, or skip, and `finalize` commits the session atomically.

## Using it from other coding agents

You don't need Claude Code's skill system. Codex, Cursor, and other agents use the same orchestration contract:

```bash
cd skills/fomo-kernel
python3 engine/review.py prepare ~/Downloads/my.csv --language en
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json --language en
# follow review_plan.flow_path, answer question_queue, then call preview and finalize
```

Point the agent at [`AGENTS.md`](AGENTS.md). `SKILL.md` is now a thin entry; mode-specific flows, JSON schemas, deterministic validators, and renderers hold the detailed contract.

## Style samples (runnable — see how different styles surface different leaks)

`mock/` holds **12 sample sets** (3 retail-style baselines + 4 investor-persona extensions + 5 engine edge-case fixtures) plus `mock_trades`, all **fictional**, each triggering one archetypal leak or engine boundary. Four representatives below; the full twelve and their design intent are in [`mock/SAMPLES.md`](skills/fomo-kernel/mock/SAMPLES.md):

```bash
cd skills/fomo-kernel
TR_DRIVER_MAP=mock/sample_fundamental.driver_map.json python3 engine/trade_recap.py mock/sample_fundamental.csv
TR_DRIVER_MAP=mock/sample_momentum.driver_map.json    python3 engine/trade_recap.py mock/sample_momentum.csv
TR_DRIVER_MAP=mock/sample_value.driver_map.json       python3 engine/trade_recap.py mock/sample_value.csv
python3 engine/trade_recap.py                          # no args = mock_trades.csv
```

| sample | style | the leak it should surface |
|---|---|---|
| `sample_fundamental` | fundamental stock-picking | exit discipline (rides a winner 120 days then bails, holds a loser 378 days waiting to break even) |
| `sample_momentum` | chase-the-momentum | position all-in + fake diversification (mistaking beta for alpha) |
| `sample_value` | only-buy-cheap | averaging down (the lower it goes the more you add, averaging INTC into a single dominant position) |
| `mock_trades` | methodology-building phase | FOMO all-AI fake diversification + PLTR averaging down |

> Four more investor-persona extensions (`sample_ai_holder` / `sample_oldecon` / `sample_swing` / `sample_day_trader` — from an AI believer holding for a year and a half to a same-day in-and-out day trader) — how to run them and their design intent in [`mock/SAMPLES.md`](skills/fomo-kernel/mock/SAMPLES.md).
> ⚠️ The engine uses yfinance to pull real historical prices for α/β, market cap, and how far each position is underwater — so **absolute numbers drift with the current share price on each rerun**; but the headline leak each set is designed to trigger is stable (it's set by the trading behavior, not by any particular price).

## Layout

```
skills/fomo-kernel/
  SKILL.md                  ← thin public entry and invariants
  flows/                    ← first / weekly / snapshot / test-drive contracts
  references/               ← agent boundaries, thesis, card, and recovery policies
  schemas/                  ← Review Plan / answers / narrative / canonical bundle
  copy/                     ← Traditional Chinese and English product copy
  engine/review.py          ← prepare / preview / finalize / resume orchestration
  engine/session.py         ← atomic canonical bundle + legacy projections
  engine/card_renderer.py   ← deterministic private/public Markdown + HTML
  engine/instruments.py     ← ETF allocation-vs-concentration policy
  card-spec.md              ← Step 3 card spec (blocklist / redact / narrative rules; read only after Step 2 questions)
  engine/trade_recap.py     ← mechanical layer: 5-dim + per-position DCA/loser classifier + attribution (pure functions, no real paths)
  rubric/
    vincent-yu.md           ← post-release research notes: paraphrased principles + source list; not loaded by current v2
    vincent-yu.lens.json    ← post-release research schema asset; not connected to current v2 questions or cards
  behavior-diagnosis.md     ← diagnostic philosophy: on the act not the person, multi-label behavior (the "why" design record)
  card-template.html        ← review-card HTML layout example
  mock/                     ← 12 sample sets + mock_trades + each one's driver map + SAMPLES.md
```

## Disclaimer

The files under `rubric/` are post-release research assets distilled from public writing. They use paraphrased summaries with source lists, not verbatim quotation, reproduction, or endorsement, and current v2 does not load them as a runtime persona.
This tool is positioned as **research / coaching support**; all output is trade-behavior review and discipline suggestions only — **not investment advice, and no buy/sell recommendation on any instrument**; final investment decisions and outcomes are your own.
The code is licensed under the [MIT License](LICENSE); the paraphrased research content in `rubric/` has source lists and is not relicensed under MIT.
