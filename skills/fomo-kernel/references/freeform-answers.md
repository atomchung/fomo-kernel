# Freeform informational answers

## The exemplars

Two ends of one rule live on this surface. A simple lookup should still look
like the compact `freeform_cash_question` or `freeform_positions_view` scenes
in the corpus; do not inflate it. The fenced scene below is the **upper
witness**: when a live decision genuinely turns on several sourced increments,
the increment gate earns several blocks. It is not a minimum length, a default
answer size, or permission to narrate research that does not change the call.

It is copied verbatim from `tests/agent/expression-witnesses.json`, scene
`freeform_research_depth` — the corpus that
§3.5 of the repository's expression contract (`docs/expression-contract.md`) makes the
binding statement of this shape. Every issuer and source in it is invented,
and `tests/test_expression_contract.py` fails if the two copies disagree.

```exemplar freeform_research_depth
先不要加 WDGT：這筆會把部位從 28.6% 推到 32.4%，先跨過你自己寫的 30% 上限，而收購的價值仍取決於尚未驗證的融資與整合。

交易不是免費增長：WDGT 為 Fabrion 報價 $4.2B，預計 60% 舉債、40% 發股，交割還要過監管（WDGT 8-K，2026-08-12）。新增的是 18% 產能，交割時只帶來 6% EBITDA；管理層把 70% 協同效益放在十八個月後（WDGT 投資人簡報，2026-08-12），所以營運好處和資產負債表壓力不是同時兌現。

形式上淨負債／EBITDA 會從 2.1 倍升到 3.9 倍，Northstar Ratings 已列入負向觀察（2026-08-13）。這不是賣出理由：現有部位的核心 thesis 還沒有被公告本身推翻；但在證據到位前，把超過上限的部位繼續放大，等於同時押融資、監管與整合三件事。

會讓我改口的證據有兩個：最終融資把舉債比例壓到 40% 以下，或交割後兩季的協同效益 run-rate 達到管理層路徑。在那之前，若仍要增加曝險，就把這筆縮到買後仍不超過 30%；不要先跨過自己的線，再等交易替你證明自己。

（帳本 8/14、價格 8/14 收盤；來源：WDGT 8-K／投資人簡報 8/12、Northstar Ratings 8/13；未評反壟斷通過機率與交易後估值）
```

The user does not only meet this product through `prepare → preview →
finalize`. They ask ad hoc questions mid-conversation — "what's my portfolio
worth right now," "how much cash do I have," "what if I add to this" — and
`consider` itself answers in plain conversation, not a card
(`trade-consequence.md`). Owner ruling, 2026-08-19 (#827): simple questions
default to a quick, direct text answer. That is a latency preference, not a
semantic ceiling on relevant research, tools, or presentation.

## Why this exists

A real dogfood session asked one ad hoc question after a review had already
finalized, entirely outside the lifecycle. The answer took roughly 34 LLM
turns: recomputing holdings from the trade CSV by hand, cross-referencing two
unrelated historical issues to explain an apparent inconsistency in the
numbers, and producing a full chart artifact through a rendering tool, before
finally summarizing in text. Nothing asked for any of that — not `SKILL.md`,
not a flow, not the user. The review card's own P&L visual is a deliberately
minimal 32px sparkline with no axes (`card-delivery.md`) — a cheap, fast
precedent already existed; the freeform surface simply had no rule pointing
at it.

## Rule 1 — the default is a quick, direct answer

An informational question asked outside the card defaults to a direct answer
in text. Do not turn a simple question into an unrelated production detour.
Use relevant research, multiple tools, or a visual when the user asks or when
it materially improves the decision. Stop when the marginal decision value of
more work is lower than its cost or latency, and disclose material coverage
limits.

This covers any ad hoc question, and explicitly a `consider` call:
`consider`'s answer is plain conversation by design (`trade-consequence.md`),
so it is a freeform surface like any other, not a card-lifecycle exemption
from this rule.

### Recovering a price completes the deterministic input

Owner ruling, 2026-07-30 (#629). The engine keeps one retrieval source. When
it fails, **the agent recovers the prices** — a search may find the
publisher's page, the close is read off that page and never off a
search-result snippet — and hands them back through the existing `--prices`
envelope. Recovering a price the engine could not retrieve is completing the
input. The specific integrity rules below apply when `consider` returned a
`price_feed` recovery kit naming missing closes and/or currency conversion;
they do not decide what other relevant research or presentation the answer may
use.

**Why the carve-out is worth its cost.** `consider` exists to answer what a
trade does to the user's concentration. Computed without current prices, every
weight is a share of *cost*: on this repository's own momentum fixture the
largest position reads more than thirteen points higher on cost than at
market, and the second and third positions by size swap places. The user is
not lied to — the answer discloses `cost_basis` — but knowing the basis is not
knowing that the ranking flipped. A forward-looking decision measured on cost
describes a book that no longer exists.

**The boundary.** The task is **transcription, not analysis**: the output is an
envelope, and the result is mechanically checked downstream, so this is
input completion step rather than judgment.

- **Coverage:** the instruments `price_feed.request.tickers` names and
  nothing else — no benchmark, no index, no integrity exclusion, and no
  instrument the book does not hold. The engine already scopes that manifest
  to the held book plus the premise's own ticker. When `tickers` is empty and
  `request.currencies` is non-empty, look up only those FX rates.
- **Stop:** retry or change sources only while the marginal coverage is worth
  its cost and latency. Supply whatever you found — partial coverage is
  accepted, and the answer names what it could not value and which sources
  were checked.
- **Delegation:** the work is bounded and parallelizable and **may be
  delegated to whatever faster tier the host has**. Which tier, and whether the
  host has one at all, is the host's own configuration and never this
  product's.
- **Degradation:** when the sources genuinely publish nothing, say so with
  `consider --prices-unavailable '<the sources you checked>'`. The question is
  then **refused rather than answered on cost basis**. Never invent, never
  interpolate, never fall back to the cost-basis figure.

That refusal is the opposite of what the review-card lane does with the same
declaration, and both are right. [price-feed.md](price-feed.md), "Two lanes,
two opposite rules", is the one statement of why.

## Rule 2 — presentation follows decision value

Use the smallest presentation that makes the important relationship easier to
understand. A visual may be generated when the user requests it or when it
materially improves the decision. Reuse the established surfaces below when
they fit; they are cheap defaults, not a closed list or a refusal boundary.

### Reusable existing surfaces

These two surfaces already have deterministic readers and privacy contracts,
so prefer them when they answer the request.

**Review card.** Trigger: the user asks, in freeform conversation, to see
their review card — the current one, or a specific past review's. This is
not a new rendering: it is the same engine-rendered artifact
`card-delivery.md` already governs, delivered through that existing contract
rather than a freshly composed one. The card's own P&L sparkline stays
exactly as scoped before — part of the card's own rendering, not a
detachable chart reachable on its own — and privacy still defaults to
`card-private.*` — the card never reaches a third party or cloud memory
(SKILL.md, "Private data stays local"): asking for the card in freeform
conversation does not loosen that default, and only `card-public.md` is
share-safe, on request.

**Positions view.** Trigger: the user asks, in freeform conversation, to see
their current holdings or positions. Shape, revised by owner ruling
2026-07-30 (#561) from the original four-column table into the richer,
already-demoed one: one row per held ticker — ticker, shares, avg cost,
current value, $ P&L, and the sizing / averaging-down / exit-discipline /
hold-consistency diagnosis tags — sorted by size (largest |$ P&L impact|
first), exactly the "Per-position diagnosis" section README.md's "What it
looks like" demonstrates. A ticker held below the meaningful-position floor
(#172's residual filter — dust too small to diagnose, such as a dividend
odd lot) is still named with its shares/cost/value, just without a
diagnosis, matching the demo's own "small lots not nitpicked" framing —
  never silently dropped from the book. The existing default is a compact
  text table; a different user-requested or materially useful presentation
  still reads the same engine facts. Cash and any other disclosure (a stale price, an unreliable cash
balance, a partial book) still ride Rule 1's existing disclosure boundary
rather than a rule this entry restates.

Every field in the Positions view must come from an engine-computed
current-book snapshot obtained through `engine/review.py` — the same
numbers-from-engine and CLI-only boundary (SKILL.md and
`references/agent-boundaries.md`) every other number in this product already obeys, never
a value the agent recomputes from a CSV, and never one read by importing an
engine module directly. The dedicated read-only outlet is
`engine/review.py positions` (#561): no CSV, no premise, no supplied
snapshot — it reconstructs the book from `<root>/ledger.jsonl` alone, asks
no question, creates no session, and appends nothing to
`trade_evaluations.jsonl` or any other durable file, unlike a `consider`
call answering the same question would. Read its JSON output (`positions`,
`residual_positions`, and their `tags`/`impact` fields) rather than
re-deriving any of it, the same discipline this file already applies to
every other engine number.

Read its `sizing` block too, and relay what it says (#737). It carries the
engine's own verdict on the weights it just emitted: whether a weight could
be computed at all, the `aggregate_currency` those weights are measured in,
and — when one could not — every holding that has none, each with the
engine's own reason, plus the missing prices or FX rates that explain it.
Two things follow. A mixed-currency book's `value` is stated in each
holding's *native* currency while its `weight` is measured in the aggregate,
so presenting the two as one basis misreads the book. And a null `weight` is
never to be shown, or silently skipped, as though it were simply a small
position: say that the weight is unavailable and why, since the whole point
of the sizing tags is a comparison that has not been made. A missing FX rate
is repairable the same way `references/price-feed.md` describes — transcribe
the rate and ask again — never by inventing one or treating it as identity.

These defaults never authorize a second source of portfolio truth. Numbers
still come only from the engine, trade data stays local, and a market price is
never invented. A new visual may reorganize supported facts; it may not
calculate, interpolate, or silently widen them.

## Rule 4 — limitations follow relevance, not a template

Use the expression contract's D1–D7 (`docs/expression-contract.md`) for
every limitation. Keep truth-critical denominator, unit, or pricing-set
qualifiers inline. Include other limitations only when they materially qualify
the answer; no marker or numeric line cap is required.

**This route's derivation from §3 is empty, and that is the expected case**
(#832). A freeform answer takes the pyramid exactly as it stands — one sentence
on top, an increment-gated middle, the rest of the inventory behind one offer,
one caliber block at the end — and adds no parameter of its own. Rule 1's
text-first default is a **latency** preference about how much work to do before
answering; it is not a shape, it never was, and reading it as one is how a
surface acquires a second answer-shape rule.

Since #830, *where* they go is a rule rather than a free choice. A fact lives
on exactly one floor (D7): the facts that decide the call open the body, a
truth-critical qualifier stays beside its number, and everything else material
— sources and as-of, which book, which session, a degraded price, a gap that
could change the recommendation — collects into **one compact end block, one
line each, non-narrative**. Nothing is said in the body and again below it.

Three consequences worth stating in this file's own terms, because this is the
surface where they were being got wrong:

- **A short answer is not an honest one by virtue of being short.** Text-first
  is a default, not a ceiling on what an answer *produces* or which facts it
  *owes*.
- **A complete answer is not a useful one by virtue of being complete.** An
  obligation list has one cheapest discharge — a sentence per item — and a
  correct answer nobody finishes reading has taxed its own correctness away.
  Say what this decision needs.
- **Nothing material means no disclaimer.** An answer with no material
  limitation ends at its last judgment. A standing "as always, this
  is not advice" tail is a manufactured disclosure (D4), which is the same
  defect as a manufactured concern.

Where a disclosure is *truth-critical* to a number in the body — a partial
denominator, a weight priced on cost, a value stated in one currency while
its weight is measured in another — it stays in that sentence and does **not**
repeat elsewhere (D2/D6). That is the same rule `trade-consequence.md`
already applies to `partial_book`, now stated once for every surface.

**Machine anchors are never rendered.** A content hash, a state version, or
validator detail belongs to the payload; there is no register in which a
person wants one. `tests/agent/check_expression.py`'s E-6 and the `consider`
receipt's delivery evidence both fail an answer that carries one.

## What this does not cover

This file gives cost-aware defaults and integrity boundaries. It does not cap
what reasoning, research, or presentation a useful answer may employ.

**Relevance is governed; placement and form are free (#825).** Every freeform
surface uses the same materiality and truth-critical qualifier rules. The
remaining deterministic checker (`tests/agent/check_expression.py`) protects
C4 against engine-vocabulary leaks; it does not impose a marker, position, or
line count. The review card's footnote remains that surface's own layout.

**Obligation selection is still per-route.** What the card owes comes from
`build_honesty_ledger()`; what a `consider` answer owes comes from its
`challenge` block (`trade-consequence.md`, "What the answer owes"), computed
per call — and since #830 that block separates what an answer owes from what
it merely *has*, so "available in the payload" is no longer a reason to say
something. Every *other* ad hoc question in this file's opening paragraph —
"what's my portfolio worth", "how much cash do I have" — has no engine-computed
obligation list of its own, and #823 did not build one. Those answers inherit
the placement rules above and select their own disclosures from what the engine
response they read actually carried.

## The research baseline is available here too

Owner ruling ([#716](https://github.com/atomchung/fomo-kernel/issues/716)). A
freeform question is often a decision wearing a lookup's clothes — whether to
keep holding, whether to add, what to do with cash sitting in the account. When
it is, the baseline in [research-priors.md](research-priors.md) is available on
this route exactly as it is on the one with no recorded book. Having the book is
not a reason to lose it: an answer that reads the engine's computed weights and
drops the baseline the same user would have been given with no book at all is
the defect that ruling names, where more evidence bought a narrower answer.

The boundary lives once, in that file's own **"An engine fact dominates a
prior"**, and this route quotes it rather than paraphrasing it: **A prior may
interpret a deterministic result. It may never replace one, substitute for one,
or fill a gap in one.** It therefore supplies no cap, no allocation, and no
threshold the user has no rule for and the engine did not compute.

**This adds no shape parameter, and Rule 4's empty derivation stands.** A claim
authority says what a block may be backed by; it does not say which floor a
block sits on or create one. A prior that does not change the recommendation is
not said, the same as any other block that fails the increment gate — and a
lookup that really was a lookup stays the quick, direct answer Rule 1 defaults
to.
