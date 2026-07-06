# FOMO Kernel

**English** · [繁體中文](README.zh-TW.md)

> A [Claude Code](https://claude.com/claude-code) skill that reviews your real trades through **one master's lens** (a swappable distillation of a trading philosophy) and hands you back **a single card** —
> the one thing you did right + your biggest leak (in your own numbers) + one rule to keep next time + one line from the master.

Not another stats report. It does what a report can't: **first it computes the behavioral leaks you can't see, then it asks the motive you won't admit, then it forces you to change exactly one thing next time.**

> 📝 **Note on language.** This README is in English, but the skill's runtime output, the master lens, `SKILL.md`, and `AGENTS.md` are currently in Traditional Chinese — FOMO Kernel is Chinese-first today. This page covers setup and concepts so you can decide whether to install; the card you'll actually get renders in Chinese.

## Quick start

**The full flow (this is the actual product) — inside Claude Code:**
```
/fomo-kernel ~/Downloads/my.csv   # review your own trades (CSV from any broker)
/fomo-kernel                      # no file → asks you for one, or offers a "test drive" on built-in fake data (nothing written to coach memory)
```
The card's value is in step ② — the engine flags a suspicious position and asks *"averaging down on conviction, or refusing to cut a loser?"*; your one-sentence answer is what turns the raw diagnosis into a verdict. **You can't see that layer from the engine's raw output alone.** Install steps under [Install](#install).

**Want zero-install, just to see what the engine computes** (the *raw diagnosis* from the mechanical layer — not yet the finished card):
```bash
git clone https://github.com/atomchung/fomo-kernel && cd fomo-kernel
pip install -r requirements.txt      # if it errors with externally-managed-environment → see the venv steps under Install
cd skills/fomo-kernel && python3 engine/trade_recap.py   # runs the built-in mock, prints the raw diagnosis
```

## What it looks like

Running the built-in mock, the **illustrative card** looks like this (below is the simplified quick-view; the actual engine output is a full-color terminal card that also includes a what-if drawdown stress test, 5-dimension behavior bars, and a return-attribution section — the finished verdict card is what Claude converges on *after* asking about motive in Step ②). *Translated below to show the shape — the engine currently renders in Traditional Chinese:*

```text
Review card · Master lens · mock sample
On paper you're up +$138k, but almost all of it is "held and never sold";
your active trades are what need discipline, not luck.

  Total P&L             +$138,058    (realized $19k + unrealized $119k)
  Active win/loss ratio  2.9         (avg win $2,851 vs avg loss $1,000)
  Beat the market +247pp · β 2.04 · AI exposure 98% (30% drawdown = −$50k)
      └ splitting "beat the market" into luck vs skill: right sector +67pp + picking within the sector +181pp
        (the α interval is still wide — can't yet tell skill from luck; don't take the demo literally)

Per-position diagnosis (sorted by size; small lots not nitpicked):
  PLTR  +$74,058   [v] likely DCA (buys up and down, not averaging a loser) · [!] too heavy 50%
  NVDA  +$56,412   [v] likely DCA · [!] too heavy 46%
  ORCL   +$1,658   [v] disciplined hold: +22%
  AMD    -$1,000   --  roughly neutral

[v] What you did right: you averaged down twice, but both times stayed within your position cap — no ticker got averaged into an oversized position
[X] Biggest leak: position sizing — largest single lot PLTR is 50%, the rest average 17%
[*] Change only this next time: hard-cap any single position at 20% — trim if it goes over
 >  Lens principle: a cheap one-time probe is allowed — it doesn't count as completing long-term validation of trust
```

The same card, rendered as a dark visual card:

![fomo-kernel review card demo](docs/demo-card.png)

> In real use, the engine also flags positions that are "large + being averaged down while underwater" and asks you, *before* the card is issued, "is this a dip-buy or refusing to cut a loser?" — the motive a machine can't tell apart, settled by your one sentence, is what lets the card reach a verdict.
> ⚠️ The mock's α numbers are distorted (too concentrated, too narrow a cross-section) — don't take them literally; real α needs a genuinely diversified book.

---

## How it differs from "pasting your statement into ChatGPT"

ChatGPT can't compute the real FIFO-matched α/β, can't tell "DCA" from "averaging a loser," and has none of your history. This skill layers three passes:

1. **Mechanical layer (Python, deterministic)** — computes what ChatGPT only estimates:
   - 5-dimension behavioral diagnosis: position sizing / averaging down / exit / diversification / holding consistency
   - **Per-position diagnosis**: every ticker ranked **by dollar size** (small lots not nitpicked), with a classifier splitting "likely DCA vs likely averaging-a-loser vs unclear"
   - **Return attribution**: splits "beat the market" into "right sector (luck / direction)" vs "stock picking (skill)" — so you see whether the gains were edge or nerve
2. **Lens layer (a master's principles × dialogue)** — the "why" a machine can't infer, asked before the card is issued:
   - Thesis check: "MSTR — you kept adding and it's still down. Do you still believe the thesis, or just won't book the loss?"
   - Motive: "You sold a winner early — was the thesis at target, or were you afraid of giving back the gain?"
   - **The engine picks the few positions worth asking about; your answer sets the read** — the machine is always guessing, your one sentence decides
3. **Prescription layer** — from "where you're leaking" to "what to do differently next time": amplify (scale your edge) / cut waste (a mechanical rule you can check next time)

→ It all converges into **one card**: one leak, one checkable rule for next time. Come back a second time and it first reconciles "did you keep that rule?"

## 🔒 Privacy: no backend upload, the author can't see it

- The skill runs your CSV **on your own machine** — **no upload to any backend, no storage anywhere else, nothing written to any memory**.
- The author can't see your trade detail. The only (voluntary) thing collected back is a single "was this card useful?" — no trade content — via the [card feedback form](https://github.com/atomchung/fomo-kernel/issues/new?template=card-feedback.yml), 30 seconds if you're willing.
- `.gitignore` is set so **no `.csv` is ever committed**, with only the mock/sample fixtures excepted.
- Precisely: the only thing that reads your trades is **the Claude you're already using** — it has to read the CSV to review it for you, exactly like any other time you use Claude. That's a different thing from handing your statement to a SaaS that stores it, that you can't see into, that the author can query. (So it's not "never touches any server" — it's "not persisted, not sent to the author, not through a third party.")

## 📁 Where your coach memory lives / how to maintain it

On your second visit, the card first reconciles "did you keep last time's rule?" — backed by three **local-only** files (all under `~/.trade-coach/`, never sent anywhere, never to the author):

```bash
cat ~/.trade-coach/log.jsonl       # one line per review (thin metrics + the rule you committed to); empty = first time
cat ~/.trade-coach/theses.jsonl    # per-position "why I hold + what would prove me wrong" (append-only, never overwritten)
cat ~/.trade-coach/profile.md      # your trading goals + 3 personal principles (the baseline each review compares against)
cat ~/.trade-coach/last_state.json # the thin state the engine last computed (per-position shares/cost, for reconciliation; overwritten each run)
```

- **See past reviews** → `cat ~/.trade-coach/log.jsonl`.
- **Switch philosophy lens / reset the reconciliation baseline** → delete or rename `~/.trade-coach/` (deleting makes next time a fresh first visit).
- **Wrote a thesis wrong** → edit `theses.jsonl`; it's append-only, so a correction = append a new event (don't overwrite the old one — that's how you see, across time, how you first reasoned and how it later changed).
- **Privacy, self-verifiable**: coach memory is just these files under `~/.trade-coach/`, all on your machine; there isn't a single row on the author's side.
- **Want to preview the multi-week loop first** (runs entirely in a temp directory, **never touches** your real `~/.trade-coach/`) → `python3 skills/fomo-kernel/engine/demo_weeks.py`: slices the built-in mock into 3 time windows to simulate "first visit → reconcile → reconcile", so you can watch the second card cite last week's commitment and `log.jsonl` grow line by line.

> 💡 **Want to share with a community?** By default the card is the full private version only. Tell me "give me the shareable version" and it outputs a **de-sensitized** plain-text version (hides amounts / share counts / exact ratios, keeps only the behavior pattern + relative performance β / beat-the-market pp) — ready to paste to X / Threads.

## Install

**Prerequisite: this is a skill for [Claude Code](https://claude.com/claude-code)** — Anthropic's terminal / desktop AI tool (needs a Claude subscription). If you haven't used it, spend 5 minutes to [install and log in](https://docs.claude.com/en/docs/claude-code/setup) first, then come back for the three steps below.

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
/fomo-kernel ~/Downloads/my.csv   # review your own trades (a statement screenshot works too)
/fomo-kernel                      # no file → asks you for one, or a "test drive" through all four steps on built-in fake data (labeled as demo, nothing written to coach memory)
```
Your CSV can come from **any broker** — Claude reads and maps it into the columns the engine needs (`Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate`, plus optional `Market / Currency` for non-US stocks — e.g. `2330.TW / TW / TWD`; omitted = US/USD); you don't hand-clean anything.

> 🏷️ For **obscure tickers** the engine's sector table doesn't recognize, Claude **auto-generates a driver map** for you to confirm (each tagged `[sector, theme]`), so the "diversification" dimension doesn't count same-theme names as real diversification — you don't do this by hand, and don't let it fall back to the `driver map: 0 tickers` case (diversification goes off). Details in SKILL Step 0.5.

**What happens**: ① the engine runs the diagnosis → ② Claude asks you 1–3 thesis/motive questions in dialogue (dip-buy or averaging a loser?) → ③ with your answers, it issues one verdict card. Card layout is in [`card-template.html`](skills/fomo-kernel/card-template.html) (a full four-layer HTML example).

## Using it from other coding agents

You don't need Claude Code's skill system — the core engine is plain Python and depends on no agent machinery:

```bash
cd skills/fomo-kernel && python3 engine/trade_recap.py ~/Downloads/my.csv
```

If you use Codex / Cursor or another coding agent, point it at [`AGENTS.md`](AGENTS.md) and have it follow along — that file is the routing guide for non-Claude-Code agents, telling it how to run the engine, ask about motive, and issue the card.

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
  SKILL.md                  ← the skill itself (four-step flow: format → engine → pre-card confirm → verdict card)
  card-spec.md              ← Step 3 card spec (blocklist / redact / narrative rules; read only after Step 2 questions)
  engine/trade_recap.py     ← mechanical layer: 5-dim + per-position DCA/loser classifier + attribution (pure functions, no real paths)
  rubric/
    vincent-yu.md           ← the default lens's principle distillation (each cited to source; swappable for another master)
    vincent-yu.lens.json    ← the lens's "swappable master layer": rules / quotes / motive prompts (swap master = swap this file)
  behavior-diagnosis.md     ← diagnostic philosophy: on the act not the person, multi-label behavior (the "why" design record)
  card-template.html        ← review-card HTML layout example
  mock/                     ← 12 sample sets + mock_trades + each one's driver map + SAMPLES.md
```

## Disclaimer

The default lens is a principle distillation from one investor's public writing (sources cited line-by-line in `rubric/`) — quoted, not reproduced, and not endorsed by that person; the lens is swappable, with more to come.
This tool is positioned as **research / coaching support**; all output is trade-behavior review and discipline suggestions only — **not investment advice, and no buy/sell recommendation on any instrument**; final investment decisions and outcomes are your own.
The code is licensed under the [MIT License](LICENSE); the lens content in `rubric/` is principle quotation with sources cited line-by-line, and is not relicensed under MIT.
