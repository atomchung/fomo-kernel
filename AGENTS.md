# AGENTS.md — fomo-kernel

> The always-on instruction floor every coding agent receives: what this product does, the rules that cannot bend, and where to go for anything that is not the core lane. Human-facing documentation lives in [README.md](README.md). Host adapters ([CLAUDE.md](CLAUDE.md), Codex configuration) add tool mechanics and override nothing here.

## The product

A user is making or revisiting a live trading decision. FOMO Kernel answers it against their own recorded book: what the trade would do to their weights, concentration, drivers, and cash, and which of their own rules it collides with.

That lane is the product, and [skills/fomo-kernel/SKILL.md](skills/fomo-kernel/SKILL.md) is its contract. Read it. Nothing else is required to answer a decision.

## Non-negotiable boundaries

Six rules. Each exists because a specific failure is otherwise unrecoverable.

1. **Reach product state only through the `engine/review.py` CLI** — `prepare`, `resume`, `preview`, `finalize`, `capture`, `consider`, `refresh`, `positions`, `render`, `weekly-market-read`, `repair-projections`, `set-cap`, `mute-rule`, `add-cash`, `resolve-market-data`, `doctor`. Never call another `engine/*` script and never import engine modules directly; those paths bypass lifecycle validation, required-question gates, and canonical session state.
2. **The engine owns every number, the portfolio basis, every identity, every `rule_effect`, and every state transition.** Read them out of its response. Never calculate, adjust, interpolate, or recall one — a market price included. You may transcribe broker-declared facts; derived analysis is the engine's. A figure you computed is not reproducible next week, and the whole loop rests on this week's number and next week's number meaning the same thing.
3. **Keep four states distinct: considered, user-resolved, user-reported execution, transaction-proven execution.** Never promote one to the next. Only a transaction record proves a trade happened; the user saying they did it is a report; a `consider` call is neither.
4. **Private data and durable state stay local.** Trades, holdings, amounts, motives, and cards never reach a third party or cloud memory. The review card is private to the user — local files, terminal output, and private-by-default in-client rendering are fine; publishing is not. Anything public — issues, PRs, fixtures, receipts — carries synthetic data only.
5. **Lead with the useful judgment, and ask at most one question** — only when its answers would branch to different advice. No process narration, no engine or schema vocabulary, no manufactured concern once the evidence supports a stop. Price targets and market forecasts stay out, and the decision stays the user's.
6. **Persist only through a canonical engine writer, and only when a named later reader exists.** No hand-assembled state, no field written for a reader nobody built.

Two consequences of rule 2 that a host cannot infer from an engine response alone. Every accepted source **records the book at the time it arrives**, so never ask whether a holdings view **covers the user's whole account** — that is an external account this product does not model. A newer holdings view reaches **the recorded book** through `refresh`, which shows the narrow diff and asks only what the user can settle. And a decision brought with no book is **framed, not refused**: `consider` fails closed, then `skills/fomo-kernel/references/decision-framing.md` owns the answer that follows. It carries **no computed or placeholder portfolio number** anywhere in it, every limitation that matters is **shaped as a question the user can answer rather than a gap narrated back at them**, and what earns the next transaction file is **naming the specific answer the next piece of evidence would buy**.

## Anything that is not a live decision

Read the detailed document when the task is actually that task — not before.

| Task | Authority |
|---|---|
| Changing this repository | [docs/issue-lifecycle.md](docs/issue-lifecycle.md), then [docs/maintainer-guide.md](docs/maintainer-guide.md) |
| QA or dogfooding this repository | [docs/qa-runbook.md](docs/qa-runbook.md) |
| Loading or refreshing a book from broker data | `skills/fomo-kernel/references/data-contract.md` |
| A price the engine could not retrieve | `skills/fomo-kernel/references/price-feed.md` |
| A decision with no recorded book | `skills/fomo-kernel/references/decision-framing.md` |
| A periodic deep review, its card and theses | the routing table in `SKILL.md`, then the flow `prepare` names |

Neither the maintainer guide nor the QA runbook is auto-loaded on any client, deliberately. Before editing repository code, read [docs/maintainer-guide.md](docs/maintainer-guide.md): it holds the development discipline, the tests, the privacy boundary, the commit and PR conventions, and the mirrored-surfaces map naming every set of files that must change together — changing one surface of a mirrored set without its partners is the most frequent defect this repository ships. `python3 tests/run_all.py` must be green before a commit. CI runs the same suite on every push, but the only gate that blocks is a Claude Code hook, so on every other client run the suite yourself rather than letting CI find it (#592).

## Instruction authority

Instruction discovery differs per client, and file order is a loading mechanism, not a licence to change what the product does. This file is the only one every supported client is guaranteed to receive, which is why the floor lives here and everything else is routed rather than copied.

When instructions disagree:

1. Deterministic code, schema, validator, and test-enforced contract outrank prose descriptions of them, and the owning issue body or owner ruling outranks historical comments and superseded issue text. Both lines are summaries — [docs/development-guide.md](docs/development-guide.md) and [docs/issue-lifecycle.md](docs/issue-lifecycle.md) own them in full, and where this summary and one of those disagree, the summary is the thing that is wrong.
2. A nearer directory instruction may specialize a root invariant only for that directory's implementation mechanics.
3. Host adapters — `CLAUDE.md`, Codex configuration, editor rules — may change tool mechanics only: never privacy, arithmetic, canonical state, product scope, acceptance, or runtime semantics.
4. A genuine contradiction between a shared and a host-specific instruction is a repository defect. Stop, record it on the owning issue, and resolve the authority. Do not silently follow whichever file loaded last.

Root `AGENTS.override.md` is not available as a host adapter here: Codex loads an override *instead of* the `AGENTS.md` at that directory level, so a root override would drop this floor entirely. Put client-specific mechanics in that client's own configuration, or in a nested `AGENTS.md` beside the code it governs.
