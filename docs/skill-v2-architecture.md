# Skill v2 architecture

Goal: preserve agent judgment where context matters while turning reliable completion, recovery, and card production into code contracts.

## Before and after

| Concern | v1: long skill prompt | v2: thin entry point plus orchestration |
|---|---|---|
| Workflow authority | The agent remembers the order from more than four hundred lines of prose. | `review.py` owns the lifecycle; route-specific flow files supply only contextual guidance. |
| Numbers | The engine computes them, but the agent copies them into a card. | The renderer reads numeric values only from engine artifacts. |
| Motive questions | The agent decides what to ask and whether it was already answered. | The Review Plan emits and deduplicates a required question queue. |
| Thesis history | The agent appends JSONL after a conversation. | Validators produce append-only thesis decision events. |
| Evidence for adds | Evidence exists as card prose and is difficult to revisit. | `new_evidence` requires a claim and source that can be reconciled later. |
| Persistence | Several commands write files independently and can leave partial state. | A staging directory is atomically renamed into one canonical session bundle. |
| Interruption | Recovery often reruns the engine and may observe different prices. | `.pending` plus `resume` preserves the original facts and questions. |
| Legacy files | JSONL files act as authority. | JSONL and card folders are repairable compatibility projections. |
| Sharing | The agent manually redacts a private card. | A public renderer creates an independent structured view. |
| Language | Runtime instructions and product copy are mixed. | Runtime contracts are English-only; localized user copy renders the same facts. |
| ETFs | Every ticker behaves like a single stock. | Diversified allocation ETFs are exempt; thematic, sector, and leveraged ETFs remain concentrated. |

## Data flow

```mermaid
flowchart LR
    A["Broker CSV or snapshot"] --> B["Mechanical engine"]
    B --> C["Review Plan"]
    C --> D["Agent interpretation and user answers"]
    D --> E["Validators"]
    E --> F["Private and public preview"]
    F --> G["User chooses one rule"]
    G --> H["Atomic canonical session"]
    H --> I["Legacy projections"]
    I -. "failure is recoverable" .-> H
```

## Where agent flexibility remains

The agent still decides how to normalize broker fields, interpret motive answers, write an inferred thesis, frame a counterfactual, and surface qualitative observations. These tasks depend on context and benefit from flexible reasoning.

The agent no longer controls numbers, rankings, required-question gates, cycle IDs, evidence completeness, ETF exemptions, public-card privacy, or persistence order. Variance in these areas creates failures rather than insight.

The architecture therefore fixes facts and workflow while preserving interpretation and narrative. The no-digits narrative rule prevents two competing numeric truth sources without forcing every agent to produce identical qualitative analysis.

## User cases

### Losing-position add

The engine detects a large position with adds while underwater and puts a required question in the Review Plan. Choosing `new_evidence` requires a claim and source; a vague statement such as increased confidence fails preview. The next review can examine whether that evidence still holds instead of restarting with a generic averaging-down question.

### Core ETF allocation

A portfolio contains mostly a broad-market ETF plus a small stock position. The allocation ETF is excluded from single-name sizing risk, risk top-three concentration, and single-name what-if stress. Sector, thematic, and leveraged ETFs receive no such exemption.

### Interrupted conversation

The engine completed, but the user did not answer. The Review Plan remains under `.pending/<session_id>`, so another agent can resume with the same facts and questions without refetching prices. If the canonical session committed and only a projection failed, `repair-projections` rebuilds it without asking the user again.

### English GTM demonstration

`--language en` changes user-visible questions, rule copy, and rendering without creating a second analysis prompt. English and Traditional Chinese sessions share the same engine card and state structures, preventing market-specific forks of the product contract.

## Release boundary

P0 includes workflow stability, canonical sessions, thesis evidence, ETF policy, private/public rendering, English developer contracts, and localized GTM surfaces.

P1 includes multi-lens selection. It may add lens selection and narrative or rule copy, but it must not duplicate lifecycle, state, or rendering infrastructure.
