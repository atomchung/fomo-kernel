# AGENTS.md — fomo-kernel

> The shared always-on instruction floor for Codex, Cursor, Claude Code, and other coding agents: route selection, non-negotiable boundaries, and the instruction-authority policy — nothing more. Human-facing product documentation lives in [README.md](README.md). The only cross-agent workflow entry point is [skills/fomo-kernel/SKILL.md](skills/fomo-kernel/SKILL.md). Host adapters ([CLAUDE.md](CLAUDE.md), Codex configuration) add tool mechanics and never override anything here.

## When to trigger

Trigger when a user asks for a trade review, transaction postmortem, brokerage-statement review, or provides a trade CSV or position snapshot.

**Maintainer QA is a different route.** If the task is dogfooding or QA-verifying this repository itself (rather than reviewing a real user's trades), follow [docs/qa-runbook.md](docs/qa-runbook.md): latest-main version gate, an isolated state root the account's own `~/.trade-coach` is not reachable from — `TRADE_COACH_HOME` alone routes writers and never bounded a reader (#557), so the runbook's step 1 replaces `HOME` too and `tools/qa_preflight.py isolate-check` refuses the run until it took — a `ux_receipt` through the walk, an archived manifest, and `tools/privacy_lint.py` on real-data findings before posting them publicly. A run that skips those gates does not count as a QA run.

`python3 skills/fomo-kernel/tools/qa_preflight.py run` is an optional automated
contract preflight, not a substitute for that QA route. Its report deliberately
contains no presentation receipt or owner verdict, so it may never be described
as a user-experience pass.

**Changing this repository is a third route.** If the task is maintenance rather than a review or a QA walk, follow [docs/issue-lifecycle.md](docs/issue-lifecycle.md) before loading context. Its one non-negotiable: an open issue is not by that fact active work. Load latest `main` and the current contract, the roadmap guard, the context index, then only the owning implementation and acceptance issues and what they directly reference — never every open issue. Read an issue's `Status` header before acting on its title or body.

[docs/maintainer-guide.md](docs/maintainer-guide.md) is the detailed host-neutral contract for that route, and it is not auto-loaded on any client: read it before editing repository code. It holds the development discipline, the tests, the privacy boundary, the commit and PR conventions, and the mirrored-surfaces map naming every set of files that must change together — changing one surface of a mirrored set without its partners is the most frequent defect this repository ships. `python3 tests/run_all.py` must be green before a commit. CI runs the same suite on every push, but the only gate that blocks is a Claude Code hook, so on Codex, Cursor, or any other client run the suite yourself rather than letting CI find it (#592).

## Workflow

1. Read `skills/fomo-kernel/SKILL.md` completely.
2. Normalize brokerage data locally. Do not require the user to reformat it. For a position table or screenshot, transcribe only the broker-declared facts into the snapshot JSON envelope documented in `references/data-contract.md`; keep this temporary JSON outside the repository (for example under `/tmp`), and do not calculate weights, P&L, cycle IDs, or classifications. Screenshot transcription stays local and does not use a cloud OCR service.
3. Start from the single orchestration entry point. Preflight once after install — the engine fail-soft degrades (silently dropping current prices, P&L, alpha/beta, and market context) when its optional runtime dependencies are missing. Use the trade command for transaction history or the snapshot command for declared positions:

   ```bash
   cd skills/fomo-kernel
   pip install -r requirements.txt   # runtime deps: yfinance + pandas + rich
   python3 engine/review.py doctor   # verify; lists what each unlocks, non-zero if a full-experience dep is missing
   python3 engine/review.py prepare <CSV...> --language en
   python3 engine/review.py prepare --route snapshot_review \
     --snapshot-json /tmp/fomo-kernel-positions.json --language en
   ```

4. Read the returned `review_plan.flow_path` and shared references. Follow `references/interaction-delivery.md`: declare the host capabilities for the local presentation trace, validate and freeze private surfaces only for `add_thesis` and `headline_motive` through `review.py resume`, use the unchanged engine fallback otherwise, and ask only questions in `question_queue` with `required:true`.
5. Produce schema-valid answers and a narrative with no digits, then call `preview`. Fix rejected artifacts instead of bypassing the validator.
6. Show the review-card preview (`card-private-preview.md`) inline and record the actual delivery mode before asking the user to choose one candidate rule, supply a custom rule, or skip. Card delivery — preview and final — follows `references/card-delivery.md`; a generated file is not evidence that the user saw it. Write that choice to `answers.commitment` and call `finalize`.
7. Deliver the review card at `sessions/<id>/card-private.md`. Deliver `card-public.md` only when the user asks for a share-safe artifact; there is no publishing feature yet.

After an interruption, use `review.py resume`; do not refetch live prices. If a projection fails, use `review.py repair-projections`. An existing canonical session is not data loss.

If the host blocks the engine's own price retrieval, `prepare` still completes and reports the gap in `review_plan.input.price_feed`, including a manifest of what is unpriced. You may transcribe those closes from a recognized market-data source into the envelope in `references/price-feed.md` and rerun `prepare --prices <path>`. Never invent, interpolate, or recall a price, and never read a missing price as a delisting or a zero return.

Test drive (`prepare --test-drive`) runs in an isolated root: pass `--root <review_plan.state_root>` to every later command of that session.

## Non-negotiable boundaries

1. Numbers, rankings, cycle IDs, metrics, weights, and ETF exemptions come from code. The agent may transcribe broker-declared position facts, but must not calculate, invent, or alter derived values.
2. A trade the user is deciding on gets `consider`'s computed consequence, and the case for and against is built from that output — never from prose. Mark every claim you add as your own judgment, cite an engine fact through the anchor `references/trade-consequence.md` documents, and state everything the response's own `challenge` block says the answer owes — including the user's exact words, the rules this trade collides with, and what nobody checked. A case that misquotes the record, leaves an owed fact uncovered, or relabels the user's own words as an outside source is refused, not stored. Lead with one supported decision tension and attach each limitation to the claim it qualifies — the decision-first plan in `skills/fomo-kernel/references/trade-consequence.md` governs the answer's shape. Leave the decision to the user. Current market context enters only through the bounded lookup contract in `skills/fomo-kernel/references/market-lookup.md`, and a found public event never becomes the user's motive without their confirmation. Price targets and market forecasts stay out, and a review card's prescription still never names what to buy or sell.
3. Required motive questions cannot be skipped. A `new_evidence` decision requires both a claim and a source.
4. Each card has at most one final rule, chosen by the user. Skipping is valid.
5. Keep trade data and engine state local and out of cloud memory. The review card itself is private to the user, not public: local files, terminal output, and private-by-default in-client rendering (for example, a claude.ai Artifact) are permitted, but never publish, post, or send it to a third party. Never mix private-card content into a public card.
6. Every accepted source records the book at the time it arrives, and which kind of source it was never decides whether it may anchor or be analyzed. Never ask whether a holdings view covers the user's whole account: that is an external account this product does not model, and whatever the user handed over is what gets recorded. A newer holdings view reaches the recorded book only through `review.py refresh`, which shows the narrow diff and asks about the differences only the user can settle. Later transaction files may unlock history-dependent diagnostics; ledger-derived current holdings stay canonical. Unreadable input, a missing or incompatible valuation or FX rate, a zero denominator, an unsettled reconciliation, and claims about an unreconciled current broker view all still fail closed.
7. Invoke the engine only through the `engine/review.py` CLI (`prepare`, `resume`, `preview`, `finalize`, `capture`, `consider`, `refresh`, `render`, `repair-projections`, `set-cap`, `mute-rule`, `resolve-market-data`, or `doctor`). Never call another `engine/*` script or import engine modules directly; those paths bypass lifecycle validation, required-question gates, and canonical session state.
8. An ad hoc informational question — including a `consider` call — gets a quick, direct, textual answer: no chart, no rendered artifact, no multi-tool production, unless the user asks for more. A chart is never invented on the spot; it matches a name in the small pre-defined set `references/freeform-answers.md` declares, or it does not exist — that set bounds what the agent decides to produce on its own initiative, never what the user explicitly asks for. Brevity bounds what an answer produces, never which facts it owes: a surface with its own disclosure contract still states all of them.
9. A decision the user brings with no recorded book and no snapshot is framed, not refused. `consider` still fails closed for want of a book — boundary 6 is unchanged — and the answer that follows is a separate bounded outcome under `skills/fomo-kernel/references/decision-framing.md`: at most three questions, no computed or placeholder portfolio number anywhere in it, a user-declared size treated as an input and never as a record, nothing written to durable state, and every limitation that matters shaped as a question the user can answer rather than a gap narrated back at them. Refusing is not what earns a transaction history — naming the specific answer the next piece of evidence would buy is.

## Instruction authority

Instruction discovery differs per client, and file order is a loading mechanism, not a licence to change what the product does. This file is the only one every supported client is guaranteed to receive, which is why the shared floor lives here and the detailed maintainer contract is routed rather than duplicated. [docs/maintainer-guide.md](docs/maintainer-guide.md) holds the per-client mechanics and how to check that a client really loaded this file.

When instructions disagree:

1. Deterministic code, schema, validator, and test-enforced contract outrank prose descriptions of them, and the owning issue body or owner ruling outranks historical comments and superseded issue text. Both lines are summaries — [docs/development-guide.md](docs/development-guide.md) and [docs/issue-lifecycle.md](docs/issue-lifecycle.md) own them in full, and where this summary and one of those disagree, the summary is the thing that is wrong.
2. A nearer directory instruction may specialize a root invariant only for that directory's implementation mechanics.
3. Host adapters — `CLAUDE.md`, Codex configuration, editor rules — may change tool mechanics only: never privacy, arithmetic, canonical state, product scope, acceptance, or runtime semantics.
4. A genuine contradiction between a shared and a host-specific instruction is a repository defect. Stop, record it on the owning issue, and resolve the authority. Do not silently follow whichever file loaded last.

Root `AGENTS.override.md` is not available as a host adapter here: Codex loads an override *instead of* the `AGENTS.md` at that directory level, so a root override would drop this floor entirely. Put client-specific mechanics in that client's own configuration, or in a nested `AGENTS.md` beside the code it governs.

## Why this bridge stays thin

Claude, Codex, and Cursor perform the same small set of high-value judgments. Mode flows, schemas, validators, session commits, and renderers are shared repository code. A thin bridge prevents each agent from maintaining a separate long prompt that drifts over time.
