# What changed

<!-- One paragraph. Link the issue (closes #NN). -->

## Tests

- [ ] `python3 tests/run_all.py` complete offline suite passes (paste the tail below).

```text
(suite tail here)
```

## If this adds or changes a checker, gate, or test

- [ ] Mutation evidence: the matching intentional mutation fails. A checker that stays green under its mutation is not evidence ([docs/development-guide.md](../docs/development-guide.md) §2).

## If this touches a renderer or output path

- [ ] Surfaces reached by the changed code path are listed below, with the oracle covering each (guide §2 — the harness surface must be the delivery surface).

<!-- surfaces / oracles -->
