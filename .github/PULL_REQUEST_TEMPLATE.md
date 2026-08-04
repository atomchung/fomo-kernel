# What changed

<!-- One paragraph. Link the issue (closes #NN). -->

## Tests

- [ ] `python3 tests/run_all.py --group product` passes (paste the tail below). This is the group the blocking `product-contract` CI job runs.

```text
(suite tail here)
```

- [ ] If this PR changes QA/eval-owned files (`qa/`, `evals/`, or a suite registered as `qa-eval`): `python3 tests/run_all.py --group qa-eval` passes too, and its tail is below. A `qa-eval` result that was skipped or red is never described as a product pass, and a green `product` result is never described as formal QA acceptance.
- [ ] If this is release or formal-QA preparation, or it changes shared test-runner infrastructure: `python3 tests/run_all.py --group all` passes.

## If this adds or changes a checker, gate, or test

- [ ] Mutation evidence: the matching intentional mutation fails. A checker that stays green under its mutation is not evidence ([docs/development-guide.md](../docs/development-guide.md) §2).

## If this touches a renderer or output path

- [ ] Surfaces reached by the changed code path are listed below, with the oracle covering each (guide §2 — the harness surface must be the delivery surface).

<!-- surfaces / oracles -->
