# PRD: investment OS with one core and constrained surfaces

Status: architectural direction. Original decision date: 2026-06-20.

## Product decision

Absorb useful record-trade capabilities into fomo-kernel and serve both a distributable product and a richer owner workflow from one core.

This is not two products and not two engines. The distributable product is a constrained subset of the owner workflow.

## Architecture

```text
Owner surface
  optional personal research context
  future selection and information workflows
            |
Shared core
  deterministic behavior and performance engine
  local ledger and thesis state
  review lifecycle and validators
  one-card renderer
            |
Distributable surface
  recap and thin update only
  local data only
  no selection or research workflow
```

The shared core calculates what happened and whether a recorded thesis or process rule survived evidence. It never answers what security should be bought.

## Surface matrix

| Concern | Owner surface | Distributable surface |
|---|---|---|
| Recap and progress card | shared | shared |
| Thin transaction/position update | shared | shared or simplified |
| Local thesis and rule state | shared schema | shared schema |
| Research context | optional | disabled |
| Selection workflow | future, process support only | disabled |
| External sources | explicit and read-only | none by default |
| Lens | personal rules or selected lens | packaged lenses |
| Card contract | shared | shared |

## Capabilities absorbed from record-trade

| Capability | Decision |
|---|---|
| Broker update into holdings | Keep as a thin ledger capability. |
| Large decision narrative | Reduce to a compact thesis with why, falsifier, horizon, stop, and target size. |
| Revisit cadence | Keep and integrate with the coach loop. |
| Post-exit and swap analysis | Keep in the mechanical layer. |
| Source attribution | Capture early; analyze more deeply only in later owner workflows. |
| Portfolio governance document | Do not import. |
| Weekly long-form journal | Replace with canonical sessions and one card. |
| Multiple operating protocols | Keep only safeguards required by observed failures. |

## Layer model

1. Thin ledger: local facts from snapshots and transactions.
2. Mechanical engine: behavior, accounting, benchmark comparison, and post-exit analysis.
3. Thesis record: compact, append-only, and falsifiable.
4. Coach loop: required questions, prior-rule reconciliation, one commitment, and recovery.
5. Renderer: one private card plus an independent public view.

## Safety boundary

Feature switches may expose more owner context, but they must not create a recommendation path in shared code. Research support can test a user-owned thesis; it cannot quietly become a stock-picking API.

## Release sequence

- Phase A: recap card.
- Phase B: thin update, ledger, thesis, and stateful reconciliation. This is the minimum distributable complete loop.
- Phase C: owner-only selection research support.
- Phase D: owner-only information gathering and source attribution analysis.

## Open decisions

- Whether the distributable surface should expose snapshot updates immediately or begin with recap-only onboarding.
- How much source capture fits the first-session question budget.
- How to keep optional owner context from leaking into shareable or distributable artifacts.
- How to make capability gates structural rather than relying only on prose.
