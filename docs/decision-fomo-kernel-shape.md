# Decision: the shape of fomo-kernel

Status: architectural direction. Decision date: 2026-07-25.

This document consolidates three things that were previously scattered: the
2026-07 conclusion about how many skills this product should be, a boundary
model for what the engine computes versus what the model decides, and a change
in product positioning that reorders the roadmap. It supersedes specific
non-goals in [roadmap.md](roadmap.md) and [prd-investment-os.md](prd-investment-os.md);
those files keep their original text and are annotated rather than rewritten.

## 1. What was already decided

From [research-skill-vs-agent-loop.md](research-skill-vs-agent-loop.md)
(2026-07-07) and the owner ruling of 2026-07-09:

- **One public entry point.** A single `/fomo-kernel` with intent routing inside
  the agent boundary. Modules stay below that boundary; users get no module
  switches.
- **"Split into several skills" was named an anti-pattern** — but the reason
  matters more than the verdict. The anti-pattern is *a flat collection of
  overlapping skills that each writes its own state*. The research placed the
  real design question at the **mode and trigger layer**, not at skill count.
- **The stated target was never "one skill forever."** The exact phrasing:
  *"The right design is multiple thin capabilities over one state machine."*
  One skill was the correct implementation because only one need had been
  validated, not because plurality was rejected.
- **Stage 2 (thin additional entry points): "Add only after a real need is
  validated."**

Three places independently reserved a slot for the entry point this document
now activates:

| Source | Reserved slot |
|---|---|
| Architecture question ② (2026-07-09) | pre-trade gate as a second entry point; prerequisite #146 already shipped |
| [roadmap.md](roadmap.md) v3 | "optionally expose a pre-trade process check" |
| roadmap dependency graph | `v2 stable lifecycle --> pre-trade process check` |

The dependency graph is the operative detail: the pre-trade check depends only
on the v2 lifecycle. It does not depend on lens selection or the snapshot
adapter. It has been buildable since 2026-07; the blocker was validation of
need, not capability.

## 2. What changed: the product's center of gravity

Previous framing: fomo-kernel is a periodic review tool. A user brings a
statement, receives one card, commits one rule.

New framing (owner, 2026-07-25): **the product exists for the moment of the
trade — "am I chasing this? should I buy?"** Review is not the product; review
is the fuel that makes that moment answerable.

The consequence is a reordering, not a replacement:

- Without review history, the pre-trade moment can only produce generic advice.
  Any general-purpose assistant does that.
- With review history, the pre-trade moment can say *"you said the same thing
  in March; that position cost you money and here is the number."*

The three parts form a loop rather than a pipeline:

```text
      ┌───────────────────────────────────────────────┐
      ↓                                               │
  ① pre-trade check ──── writes a decision ────→ ② review
     "should I buy this"                          "how did that go"
      ↑                                               │
      └──────── ③ rule backtest ←─────────────────────┘
              "is this rule of mine even sound"
```

③ is where a user learns whether their own strategy holds up, which is why it
outranks the other Layer 2 tools despite being the least visible.

## 3. Boundary model: engine versus model

The previous boundary in `references/agent-boundaries.md` divides on
**numeric versus qualitative** — the agent may not compute any number. That
axis works for backward-looking review, where every number has exactly one
source of truth in the user's own file. It breaks for forward-looking work,
because a future state has no ledger to be faithful to.

Replace it with this test:

> **Given the same data, should two different agents produce the identical
> answer?**

| Answer | Owner |
|---|---|
| Yes — one correct value, derived from the user's own record | **Engine only** |
| Yes *once a premise is supplied*, but the premise is proposed rather than recorded | **Engine computes, model interprets** |
| No — a second thoughtful person would reasonably disagree | **Model, with provenance labelling** |

### Layer 1 — facts (engine only)

FIFO-matched P&L, position weights, concentration, beta, cycle identity, which
rule was committed and when, what the user wrote as an exit trigger.

The reason to keep this in code is not that the model would hallucinate. It is
that **determinism is what makes cross-week memory trustworthy**: this week's
12% and next week's 12% must be the same 12%, or reconciliation is meaningless.
State the reason this way rather than as a prohibition — a rule with a
understood purpose survives edge cases that a ban does not.

### Layer 2 — consequences (engine computes, model interprets)

Deterministic arithmetic over a premise the model or user supplies. The engine
today ingests only history, so this layer barely exists: `prepare` accepts
transactions, snapshots, prices, cash, and maps — no hypothesis input.

Two Layer 2 primitives already ship, which proves the shape is sound rather
than speculative: the stress scenario in `trade_recap.py` ("this driver falls
30%, the account loses $X") and the realized-drag `counterfactual`. Both are
what-ifs. The limitation is that the engine can only answer what-ifs it
invented itself.

**Layer 2 is what makes an opinion honest.** The model may say "I would buy
this"; the engine states what that does to the user's actual book beside it.
The advantages and disadvantages of an action should be computed, not written
as prose.

### Layer 3 — judgment (model, with provenance)

Whether a thesis holds, whether a valuation is attractive, whether the user's
stated reason is actually a reason, what to buy, whether to sell. No single
correct answer; the model has full latitude.

The constraint is not a ban but a label: mark each claim as *your record says*,
*public fact*, or *my judgment*. Per-sentence provenance is more useful than a
blanket disclaimer, and it is the mechanism that replaces prohibition.

## 4. Layer 2 tool inventory

Most of this is extension of shipped assets rather than new construction.

| Family | Tool | Status |
|---|---|---|
| **What happens if I do this** | Position consequence of a hypothetical trade (weight, concentration, driver exposure, cash level) | New, moderate |
| | Rule collision: does this trade break a rule the user committed? | **Small extension** — `rules.jsonl` is already structured with `metric_key`, `rule_id`, append-only `revises` |
| | Remaining capital if every stop triggers | Small extension |
| **Where do I actually stand** | ETF look-through exposure (how much NVDA is inside the funds) | New; needs an external constituent source. `driver_map` is flat `ticker → [driver, weight]`, so an ETF carries one label today |
| | Effective position count / are these one bet | Small extension |
| | Thesis maturity check against a stated horizon | **Small extension** — `revisit.py` already runs 30/60/90 checkpoints, but only for exited positions; the same machinery applies to open ones |
| **What if** | Arbitrary scenario stress (model proposes scenario → driver mapping → loss) | Small extension of the existing stress computation |
| **Is this rule sound** | **Rule backtest**: replay a candidate rule over the user's own history and report what it would have cost and saved | New entry point over shipped assets — the FIFO engine and the full ledger are already there |

The rule backtest is ranked first for build order. It converts the product's
weakest moment into its strongest: choosing a rule today is a sermon ("you
should size positions better"), and after backtesting it is a computed decision
("this rule would have saved $15k net over 14 months"). It needs no external
data, no market view, and no relaxation of any existing boundary.

## 5. Capabilities are tools, not skills

The 2026-07 research asked for "multiple thin capabilities over one state
machine" without specifying what a capability is. The answer is a **tool**, and
that strengthens the original conclusion:

| | Several skills | One skill plus tools |
|---|---|---|
| Load timing | whole instruction set enters context on trigger | loaded only when called — progressive disclosure enforced mechanically |
| State | each writes its own (the named anti-pattern) | all transitions through one kernel |
| Boundaries | restated per skill | the tool interface *is* the boundary |
| Cost to add | new description competing for triggers | one tool; the entry point is untouched |

**One open risk.** A single `description` must now trigger reliably on two very
different utterances: "review this statement" and "should I buy NVDA right
now." Description text is the primary triggering mechanism and models tend to
under-trigger skills. This cannot be settled by reasoning — it needs a measured
trigger rate over a realistic query set. If one description cannot hold both
contexts, the correct response is a second *entry point* over the same kernel,
which is what the original research already permitted.

## 6. Rules as falsifiable strategy hypotheses (deferred)

Recorded now, deliberately not implemented yet.

If trading is an iterative search for a strategy that fits the person, then a
committed rule is not a discipline device — it is **a falsifiable hypothesis
about that person's own edge**, and it deserves the lifecycle of one:

| Stage | Evidence | Existing asset |
|---|---|---|
| Proposed | backtest over the user's own history | FIFO engine and ledger exist; no "apply a hypothetical rule and replay" entry point |
| Committed | in-sample evidence stored with the rule | `rules.jsonl` carries `metric_key`, `rule_id`, append-only |
| Tested | out-of-sample result at the next review | `problems.py` already reconciles rules against periods |
| Revised | corrected rather than discarded, lineage preserved | `revises` chain exists |

**Convergence with KOL Lens.** This is the same design as the master-lens work
in the parent `kol_collector` repository (`rubrics/*.lens.json`,
`engine/compare_lenses.py`). That design already separates the layers the same
way — its own note states that the engine computes *what the hole is* while the
lens decides *how a given school talks about it*, so swapping masters swaps the
lens file and leaves the engine untouched. Its distilled pillars already
include writing down, before entering, what evidence would mean you were wrong.
The `rubric/` directory in this repository is that design's descendant, held as
a P1 research asset.

Deferred by owner decision on 2026-07-25: keep the current rule contract as is;
revisit as a strategy discussion rather than a schema change.

## 7. Non-goal revision

The recommendation boundary is being reconsidered. An investing agent cannot
avoid the question, so the honest response is to disclose the trade-offs of an
action and the direction one believes, rather than to refuse.

Split the current non-goal in two:

- **Security recommendations — to be lifted.** Conditional on Layer 2 existing.
  A recommendation is admissible when it carries the engine-computed
  consequence for that user's book, a falsifier stating what would prove it
  wrong, and per-claim provenance. Because a recommendation is written into the
  same append-only ledger with the same falsifier field as a user's own thesis,
  the next review reconciles the agent's own calls with the same machinery it
  uses on the user's. The agent may advise, but it may not advise and forget.
- **Market forecasts — kept as a non-goal.** "This position takes your
  semiconductor exposure to 48%" is anchored in the user's record and checkable
  now. "NVDA reaches $250" has no engine support and no falsification date.
  Keeping this boundary is what makes lifting the other one credible.

**Sequencing.** Lifting the recommendation ban before Layer 2 exists would be
worse than keeping it: the model would fill the consequence gap with invented
prose, losing the discipline without gaining precision. Order: slim the
instruction set, build rule backtest and rule collision, build hypothetical
trade consequence, then lift the ban.

Affected text elsewhere: the "Security recommendations or market forecasts"
non-goal in [roadmap.md](roadmap.md) and "never answers what security should be
bought" in [prd-investment-os.md](prd-investment-os.md). Both are superseded in
part by this document as of 2026-07-25 and must not be treated as current
without reading this section.

## 8. Open questions

- **Reach at the decision moment.** A user feeling FOMO does not open a coding
  agent. The pre-trade entry point succeeds or fails on whether it is present
  when the decision happens, not on answer quality. This is the one direction
  that would genuinely outgrow the skill form, and it matches a build trigger
  already listed in the 2026-07 research ("users need proactive reminders
  rather than return-time memory"). It is a Stage 3 problem: first make the
  answer good enough to be worth reaching for.
- **Dual-trigger reliability** for a single description (§5).
- **ETF look-through data source**, required before exposure claims can be
  honest for fund-heavy portfolios.
