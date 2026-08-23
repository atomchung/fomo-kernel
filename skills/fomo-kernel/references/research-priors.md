# Research priors — the decision baseline, on every route

This is the small, host-side research authority behind a normative baseline
this product may state directly. It is not portfolio arithmetic, a suitability
assessment, or a product selector: no route reading it chooses a fund,
geography, allocation, trade, or final action.

Recheck the primary sources before widening a claim or applying it to a new
decision class. These priors are stable research syntheses, not runtime web
lookups, engine facts, or user rules. Cite one the way any looked-up fact is
cited — the claim and where it comes from — and never as something computed
from the user's own record.

## One catalogue, every route

The catalogue was reachable only from the route that has no book, which made
the product answer a user *worse* the more evidence they handed over: with
nothing recorded they heard that broad diversification is the baseline and that
an index label alone does not establish breadth, and once they supplied a book
they got weights and concentration and could no longer reach that baseline at
all. Owner ruling
([#716](https://github.com/atomchung/fomo-kernel/issues/716)): the same
catalogue is the baseline on every route that answers a decision.

| Route | Where the baseline enters |
|---|---|
| No recorded book ([decision-framing.md](decision-framing.md)) | The answer's top sentence, ahead of the strategy-class map. |
| `consider` ([trade-consequence.md](trade-consequence.md)) | A middle-floor block, interpreting a fact the engine computed. |
| Freeform answers ([freeform-answers.md](freeform-answers.md)) | The same, whenever the question asked is a decision rather than a lookup. |

Only the *entry point* differs. The claims, their applicable decision classes,
their material exceptions and their forbidden overclaims are identical on all
three, and a route may not hold a narrower or a wider version of a prior than
this file states.

A prior is a claim authority, not a block, a question, or a standing paragraph:
it adds no shape parameter to any surface, so the increment gate in
the repository's expression contract (`docs/expression-contract.md`) §3 governs it
like every other block. One that changes nothing about what the user should do
is deleted, not shortened.

## An engine fact dominates a prior

**A prior may interpret a deterministic result. It may never replace one,
substitute for one, or fill a gap in one.** Where the engine computed a number,
that number is the fact and the prior is at most the reading of it. Where the
engine computed nothing, the prior does not get to supply the missing number in
words.

Three consequences, in the order they get broken:

1. **A prior invents no threshold.** It may not name a position cap, an
   allocation, a concentration limit, or an exposure ceiling that this user has
   no rule for and the engine did not compute. A cap is a fact measured against
   a computed weight and overridable by the user's own `set-cap`; offered as a
   general rule it is fortune telling about a book nobody read.
2. **A prior interprets, then points.** It may say broad diversification is the
   baseline and then point at the concentration the engine computed for this
   book. It may not run that inference backwards and describe the book from the
   baseline.
3. **A prior judges no particular act without the record.** It may say low
   discretionary turnover is generally supported for a standing long-horizon
   policy; it may not call this user's sale excessive without the transaction
   record or a rule they stated themselves.

The dominance is one-directional. An engine fact, a user rule, or a stated
personal fact narrows or disqualifies a prior's **applicability**; a prior never
edits, softens, excuses, or outranks an engine fact. When the two look like they
disagree, the engine fact is the answer and the prior was inapplicable — that is
the whole of the conflict rule. The two book-bearing routes quote the bold
sentence above **verbatim** rather than wording it themselves, and
`tests/test_research_priors.py` fails if the copies drift apart; no surface
writes a version of its own.

## RP-001 — broad diversification baseline

**Applicable decision class:** a genuinely broad-market, long-horizon equity
policy, when the user has not supplied a material reason to concentrate in one
company, sector, theme, country, or factor sleeve.

**Bounded directional claim:** broad, diversified market exposure is the
baseline over concentrated security or theme selection. An ETF or index label
alone does not establish breadth: companies, sectors, geographies,
capitalization range, and concentration still matter.

**Material exceptions:** a near-term liquidity need, a deliberately bounded
tactical or learning trade, an already-defined multi-asset policy, or exposure
that is not genuinely broad. Broad exposure still carries equity, geography,
currency, valuation, and drawdown risk.

**Forbidden overclaims:** do not call a broad index safe, choose an index or
geography, claim it will outperform, or infer a suitable allocation.

**Reviewed date:** 2026-08-01.

**Primary sources:** Bessembinder et al., *Long-Term Shareholder Returns:
Evidence from 64,000 Global Stocks* (2023),
https://doi.org/10.1080/0015198X.2023.2188870; Jorion and Goetzmann, *Global
Stock Markets in the Twentieth Century* (1999),
https://doi.org/10.1111/0022-1082.00133.

## RP-002 — low discretionary turnover

**Applicable decision class:** a standing long-horizon market policy, rather
than a consciously tactical trade.

**Bounded directional claim:** lower discretionary turnover is the baseline
over reactive trading for that policy. An arbitrary price stop is not the
normal governance mechanism for a long-horizon market policy.

**Material exceptions:** liquidity or goal facts change; an explicit target
allocation requires a rebalance; or the vehicle no longer delivers the
intended exposure or changes materially. A tactical policy can instead use its
own user-defined observable condition.

**Forbidden overclaims:** do not say never sell, invent a stop, moving average,
deadline, or rebalance cadence, or treat a long horizon as a guarantee.

**Reviewed date:** 2026-08-01.

**Primary sources:** Sharpe, *The Arithmetic of Active Management* (1991),
https://web.stanford.edu/~wfsharpe/art/active/active.htm; Barber and Odean,
*Trading Is Hazardous to Your Wealth* (2000),
https://doi.org/10.1111/0022-1082.00226.

## RP-003 — liquidity horizon is an exception gate

**Applicable decision class:** a user calls an equity policy “long term” or is
deciding whether capital belongs in equity exposure.

**Bounded directional claim:** a long label does not make equity safe or
guarantee recovery before the money is needed. A known goal or liquidity date
is a material exception gate before applying the broad-market baseline.

**Material exceptions:** the money is genuinely separated from foreseeable
spending and the user can bear a severe drawdown without changing the policy.

**Forbidden overclaims:** do not infer risk tolerance, set an emergency-fund
amount, choose stock/bond percentages, or promise that time removes risk.

**Reviewed date:** 2026-08-01.

**Primary source:** Pástor and Stambaugh, *Are Stocks Really Less Volatile in
the Long Run?* (2009), https://www.nber.org/papers/w14757.

## RP-004 — recurring savings is not staged existing cash

**Applicable decision class:** a user mentions periodic investing, staging, or
a lump sum.

**Bounded directional claim:** recurring future savings and staging
already-available cash are different decisions. Recurring savings is mainly an
execution policy. Staging existing cash exchanges immediate market exposure for
regret/adherence management; it is not a free expected-return improvement.

**Material exceptions:** the capital's real availability, the user's ability to
adhere to a stated policy, and any near-term goal or liquidity need.

**Forbidden overclaims:** do not say periodic investing always improves returns
or reduces risk, predict the market, prescribe a schedule, or treat future
savings as idle cash.

**Reviewed date:** 2026-08-01.

**Primary sources:** Constantinides, *A Note on the Suboptimality of
Dollar-Cost Averaging as an Investment Policy* (1979),
https://doi.org/10.2307/2330513; Statman, *A Behavioral Framework for
Dollar-Cost Averaging* (1995), https://doi.org/10.3905/jpm.1995.409537.
