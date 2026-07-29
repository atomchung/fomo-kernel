# Development guide — failure modes and the discipline that prevents them

Status: living document from the 2026-07-26 architecture review of the
2026-07-11 → 07-26 window (127 commits, 43 of them fixes). Owner direction:
this is the development knowledge we maintain — lessons land here with
receipts, not as scattered session notes.

Scope: how changes to this repository go wrong, and the small rule set that
prevents the expensive reruns. It complements, and never repeats:

- [CLAUDE.md](../CLAUDE.md) — maintainer mechanics: tests, hooks, privacy
  boundary, PR conventions, the mirrored-surfaces table
- [decision-fomo-kernel-shape.md](decision-fomo-kernel-shape.md) — product
  shape and the engine/model boundary (Layers 1–3)
- [eval-design.md](eval-design.md) — evaluation methodology
- [issue-lifecycle.md](issue-lifecycle.md) — what an open issue must state
  about itself, when to close versus preserve a record, and the context-loading
  order that keeps `is:open` from being read as the execution queue

Update bar: a pattern earns a place here only after it shipped a real mistake,
and it arrives with its receipts (issue/PR numbers). Prefer tightening an
existing section over appending a new one; if this file passes ~200 lines,
something should be deleted or promoted into a mechanical gate.

**Gate labels.** *Gated* — a check catches violations unattended, so the line
is only a pointer. *Partly gated* — a check exists but covers less than the
rule says, and the line names where it stops, because a bare pointer would
promote "one instance is tested" into "this is guaranteed". Unlabelled means
judgment. Label Gated only after reading the assertion: a test's name is not
evidence (2026-07-27 audit — three claims here outran their gates).

## 0. The discriminant every other rule hangs on

Every rule in this repository is one of two kinds. Most calibration mistakes
are treating one kind as the other.

| | Invariant — "must never happen" | Behavior — "say it like this" |
|---|---|---|
| Examples | one numeric source of truth; append-only state; fail-closed identity; privacy exits; frozen question surfaces | wording, templates, ordering, tone, which observation matters right now |
| Enforce in | the mechanical layer: engine `raise`, schema, validator, deterministic test | model latitude, steered by evals; hard-coded only while models are too weak to hold it |
| As models strengthen | appreciates — a more capable agent has more exits to guard | depreciates — enumerated sentences block improvement that would otherwise arrive for free |

The agents running this skill are user-supplied (Claude Code, Codex,
Antigravity — strong and weak, mixed, indefinitely). A prompt-layer prohibition
is unreliable for the weak model and wasted context for the strong one; only
the mechanical layer binds both ends. Corollary from the 2026-07 slimming
(#399, #407): instructions carry only what the engine cannot fail-close,
written as *why* rather than as a bare ban.

**Loosening guard** (partly gated). Before relaxing a rule you classified as
behavior, prove it is not a load-bearing invariant — the slimming removed the
only protection for required-question visibility because it read like style
(PR #399 review). Run `python3 tests/run_all.py` first and treat a broken lock
as a question to answer, not an obstacle to rephrase around. Do not read the
locks as coverage: they are eight fixed strings in
`test_runtime_contract_contains_fixed_fallback_and_no_file_only_success` — the
sentence that motivated this rule was itself added to the set (#442), but
nothing locks the next sentence someone judges load-bearing by eye. The suite
says when you hit a lock, never that you missed one.

## 1. Mirror tax — the largest measured cost

**Signature.** One fact stored in N files, kept aligned by hand or by an
equality test; the mirrored-surfaces table in CLAUDE.md is the census.
**Measured (#402).** The median change fans out to 4 surfaces, and
`card_renderer.py` churned 1.8× its own line count in the review window.

**Rules.**

- Prefer generating a synchronized surface over hand-mirroring it (partly
  gated). `skills/fomo-kernel/tools/design_bundle.py` is the precedent; #401
  applies it to `card-template.html`, #402 knife 5 to test expectations
  (`tests/copy_corpus.py`). A hand mirror guarded by an equality test is the
  fallback, not the default — including inside a test, where a literal copied
  out of `copy/*.json` is the same mirror wearing an assertion. Instances are
  gated (`test_card_template_matches_its_generator`, and — since #442 —
  `tests/test_design_bundle.py`, which runs `design_bundle.py` itself for a
  clean exit and checks its derived output still reflects the current
  runtime CSS; and — since #452 —
  `test_data_files_registry_covers_every_engine_written_path`, which parses
  `engine/*.py` for the coach-root path literals the code actually
  constructs and fails if one is missing from `coach.DATA_FILES` — the
  hand-maintained persistence registry `condition_checks.jsonl` fell out of
  silently before this check existed); nothing detects a new hand mirror
  before one of its instances earns a gate.
- A metric can be blind to your change: `change_surface.py` counts surfaces
  crossed, so a knife that only stops a surface being hand-written moves
  nothing. Add a reading (authored churn) rather than call the knife
  unmeasurable. The tool is manual — no suite runs it.
- A new copy key lands in `en` **and** `zh-TW`; only `zh-CN` takes the
  documented fallback (gated: `test_locale_copy_files_keep_key_parity`, #279)
  — an earlier version of this line said `en` only, which that test had
  already made false. Strike when #390 ships.

## 2. The fake-green family — five ways a check passes without looking

A fake green is a passing check that never observed the thing you changed.
Five types have shipped here, sharing one root: **the verification surface was
not the delivery surface.**

| # | Type | Receipt |
|---|---|---|
| 1 | Structure, not content — asserts the block exists, not what it says | #363 |
| 2 | No pinned extremes — "at least two viewport widths" was satisfied by two wide enough to hide the truncation at 360px, and fixtures used 3-digit amounts where the README shows 6. Comply-and-hide is the failure mode of every "at least N" gate | #382 |
| 3 | Claimed-run, not run — a verification asserted in prose with no artifact behind it | — |
| 4 | Harness surface ≠ delivery surface — byte-parity compared Markdown only, so a KPI refactor moved 112 of 156 HTML cards under a green sweep | #394 |
| 5 | Right assertion, wrong layer — an argparse-level test for a handler-level `raise`; only the mutation run exposed it | PR #399 |

**Rules.**

- A new checker lands with proof its matching mutation fails; a checker that
  stays green under its mutation is not evidence ([eval-design.md](eval-design.md)
  holds the mutation catalogue). The PR template asks for the evidence, but
  nothing runs a mutation or checks that the box is true.
- Before touching a renderer or output path: list which surfaces the changed
  code path reaches (grep the callers), and name the oracle that covers each
  one — the move that caught two live blindspots (#363, #394). No tooling
  verifies the list is complete.
- Measurement gates pin their extremes explicitly: narrowest width, longest
  unbreakable token, largest realistic magnitude, fewest cells, and overflow
  on both axes (`scrollWidth` caught what `scrollHeight` passed, PR #384).
  Partly gated — `test_pnl_and_payoff_tile_subs_fit_the_two_line_cap_at_narrow_widths`
  pins magnitudes as literal strings but measures no viewport, so CSS
  overflowing at 360px still passes.

**A sixth shape, with a different root.** The five above share *the verification
surface was not the delivery surface*. A suite can also observe exactly the
right surface and still pin the wrong answer. `consider`'s rule collision
shipped with 35 passing tests and three precise mutations while telling a user
selling down an oversized position that the trade broke their own rule — the
engine read the resulting state of the book instead of the effect of the trade,
so an action that cut the top weight from 59.75% to 49.74% returned the same
verdict as one that made it worse. Every test asserted "given this book and this
premise, return this state," and every test was right about what the code did.
None asked what the returned state would mean to the person reading it.
Receipt: `2d563b9`.

- For any value an agent or renderer turns into advice, enumerate its states and
  ask of each: **what would the user do if they believed this, and would they end
  up better or worse off?** A state that inverts under a legitimate scenario —
  one value covering both "you are making this worse" and "you are fixing it" —
  is a design defect, not a wording problem, and no amount of mutation testing
  on the existing assertions will surface it. It was found by running the
  command on a real fixture and reading the answer as its reader.

## 3. Static-rule-ification — the upstream generator of complexity

**Signature.** A runtime judgment hard-coded as an enum, table, or priority
list; every exception becomes a branch plus a test plus, often, a mirror, and
that is where fan-out is born. **Receipts.** Two condition templates meant 8
of 12 personas received the identical sentence (#400);
`metric_key not in metrics → ReviewError` discarded the most informative input
a user can give (#412); `candidate_rules[].verify` was written for display,
read by no mechanical consumer, and repeatedly invited misreading.

**Rules.**

- The enum-gated-surface standard (#412), for any surface an agent or user
  will stretch: an enum for what is mechanically decidable, `unmapped` as a
  first-class state for what is not, never a silent drop or guess. Partly
  gated — `condition-slot.schema.json` and `tests/test_conditions.py` hold it
  for `tier` alone; nothing finds the next surface that skips it.
- Situational priority belongs to the agent at runtime. The owner ratifies
  facts, budgets, and red lines — not orderings. Phrase design questions as
  "which facts must the engine expose so the agent can judge well," not "which
  static ordering do we pick."
- Every stored field needs a mechanical consumer, or it goes; a
  written-never-read field is schema debt (the `verify` lesson). Partly gated:
  `QUESTION_CONSUMERS` in `evals/run_episodes.py` reads the kind list live
  from the schema enum, so an undeclared kind fails, and — since #451 —
  `question_field_consumers()` in the same file fails on a field four of
  those sinks construct that the card never reads (the gap that let
  `thesis_decisions.note`/`.evidence_delta` pass as wired). Both stop at
  question kinds; `condition_checks` (read by a dozen-plus functions, not one
  loop — see the section comment above `QUESTION_FIELD_SOURCES`) and `verify`
  itself still slip through.
- The second recurrence of a symptom is a design issue, not a second patch
  (#357: the cash-anchor gap recurred three times as one-off patches before it
  was named a design problem).
- **An availability gate needs a named user scene, not just a true
  proposition.** "A transaction export cannot prove it covers the whole
  account" is true. Modelling it as a `completeness` tier and gating on it made
  the product refuse instead of answer: `declared_complete` is reachable only
  through a snapshot anchor, CSV ingestion writes no snapshot event, so
  `consider` aborted for every user whose book came from trade history — the
  primary input route (#496 added that gate, #506 added a second one on the
  same field, #485 removes both). The tier is also derived from all history,
  so one superseded partial snapshot downgraded a book permanently. Before
  adding an availability tier, write "user X, in scene Y, sees Z instead of W"
  into the acceptance criteria; a gate that cannot produce that sentence is
  refusing on the product's behalf for nothing. This is a different act from
  suppressing a calculation whose *fact* is missing — no price, no FX rate,
  corrupt event, zero denominator — which names what is absent rather than
  judging what the user supplied. Splitting an architecture into small
  reviewable leaves does not preserve this by itself: no leaf-level review
  asked what the gate bought a user, so each acceptance list needs the
  sentence of its own. Ungated, and the near-miss is instructive — all 54
  cases in `tests/test_consider.py` seed a complete snapshot event, so a gate
  that locked the capability out of its main route shipped green twice. The
  closest mechanical proxy is one end-to-end test per capability over the
  primary input route.

## 4. Order discipline

- **Tax removal before features.** After the 2026-07-18 review named the
  refactors, feature PRs cut the line and the hot files grew 1.8–2.8× in a
  week (#216 → #402). The knives are scheduled work, not background wishes.
- **Gates before loosening.** The recommendation ban lifts only after Layer 2
  exists ([decision-fomo-kernel-shape.md](decision-fomo-kernel-shape.md) §7)
  *and* free-form answers have a mechanical provenance gate (#414). The ban is
  itself ungated, resting on `SKILL.md` and `card-policy.md` text — an answer
  surface without its own gate would be the loosest point in the system.
- **Eval shape ships in the same PR as the capability**: the researched tier
  (#412) landed with adversarial episodes for a criterion restated as a yes/no
  query, an uncheckable condition falling to `unmapped`, and two-sided
  reasoning carrying both sides. Weaker than it sounds — a check with no
  exercising episodes prints a `NOTE`, never a failure, so a capability can
  ship uncovered with CI green.

## 5. The never-loosen list

Loosening any of these is a product regression, not a style change. Each row
names what actually holds it; where nothing does, the row says so.

| Invariant | Held by |
|---|---|
| Numbers come from engine artifacts only; agent prose derives nothing | Gated — `tests/test_digit_ban.py` rejects digits *and* spelled-out quantities across every narrative field, so a new field inherits it |
| Frozen question surfaces replay byte-identically on resume | Gated — `test_resume_freezes_validated_surface_and_invalid_generation_falls_back`; even a corrected resubmission is rejected |
| Append-only state, engine-assigned content-addressed identity, idempotent finalize, fail-closed on same-ID conflict | Partly — `tests/test_coach_session_idempotency.py` asserts the last three; append-only itself has no dedicated test |
| Privacy exits: the public-card whitelist, local-only trade data, and `skills/fomo-kernel/tools/privacy_lint.py` on any real-data text bound for this public repository | Partly — `tests/test_privacy_lint.py` proves the lint fails closed and that rendered cards are git-ignored; the original `*.csv` and `ledger.jsonl` patterns have no such test, and *running* the lint before a post is manual |
| Artifact generation is not presentation | Judgment — the `ux_receipt` log is shape-checked but self-reported; #249 is exactly this hole |
| `derived`/`fetched` reconciliation stays deterministic; `researched` verdicts never enter `held_streak` or graduation statistics (#412) | Gated — `test_conditions_module_never_imports_problems` |

## 6. Changing instruction surfaces

- Guaranteed-delivery entry points are `SKILL.md` and `AGENTS.md`; every other
  document is soft-routed and may never be read. A rule that must always land
  is either repeated at those entry points or enforced mechanically.
  Repetition there is a delivery contract, not redundancy; find the enforcing
  test before cleaning it up. Partly gated:
  `test_review_py_is_a_non_negotiable_boundary` derives the CLI whitelist live
  from `review.py`. `test_freeform_answer_shape_is_a_boundary_in_both_entry_points`
  (#543) is a second instance, weaker in kind: it checks a shared literal
  phrase rather than a list derived from source, because there is no engine
  artifact to derive a freeform-answer-shape rule from. Two rules are now
  wired this way; a third must-always-land rule forgotten in one entry point
  is still caught by nothing.
- Do not trust pattern counts of prohibitions ("N occurrences of *never*").
  Most hits describe engine behavior the agent relies on to do *less* work;
  deleting them creates work. Read and classify before concluding.

## 7. Two readers, one fact — divergent derivation

**Signature.** Two pieces of code derive the same fact from the same data
instead of one computing it and the rest reading the answer. They agree on the
common case and split the first time an edge case reaches only one path, and
the disagreement ships silently. **Receipts.** Third recurrence in one cut
(#412); each closed the same way — collapse to one reader.

| Fact derived twice | How the readers split | Collapsed to |
|---|---|---|
| What counts as a number | `condition_integrity`'s own regex saw the `3` in `Q3`, while the criterion's allow-set was read with the engine's `numbers_in`, which does not; the mismatch failed an honest answer that quoted the criterion verbatim | Both sides call `conditions.numbers_in` (#433) |
| Which line a row belongs to | Reconciliation compared `check.slot_id` against `{prior.slot_id, prior.line_id}` rather than routing both sides through one identity function; a `slot_id` names its line's live head, so a second revision matched neither and the then/now comparison went silent with the most history behind it | `conditions.slot_line_id` is the only reader; `review.py` stamps `line_id` on every due entry and on `prior_commitment.condition`, and the renderer compares only stamped values (#437/#438) |
| Whether a row is still open | The card's summary count and its per-condition lines decided independently until round 2 unified them onto `_condition_outcomes` — then round 3 found the same disease inside it, where `_condition_outcome` folded a crossing axis and a basis axis into one value through an if/elif chain, so whichever matched first dropped the other from both surfaces | Return both axes and render each independently (#438, review rounds 2–3) |
| Which thesis cycles are live | Both the plan and the card context routed through the single derivation `_thesis_cycle_index` — and still split, because the two call sites assembled its *input* differently: the plan joined thesis rows with the session's cycle relinks, finalize re-read root history without them, so a condition live only via a relink and beyond the lookup cap fell out of both rosters. Each half had a test; the defect lived only in their intersection | `cycle_relinks` became a required argument — a call site that omits it does not run — and `_plan_thesis_cycles` is the only finalize-side accessor (#444, review rounds 3–4) |

**Rules** — no gate detects a second derivation.

- Before adding a second place that computes X, ask who else derives X and
  from what; import an existing answer rather than restate its logic.
- Collapse to one reader: share the function (`numbers_in`), stamp a
  once-derived value so every other site compares only stamped values
  (`line_id`), or classify once and render every surface from that
  classification (`_condition_outcomes`). Register the pair in CLAUDE.md's
  mirrored-surfaces table when the fix lands.
- A unified reader is not done until it is checked for silently merging
  independent facts into one value — the same shape, one layer down (#438
  round 3).
- A single reader is still two readers when its input is assembled in two
  places: the composition feeding the derivation must be single too. Make a
  missing ingredient unrepresentable — a required parameter with no default —
  rather than remembered at every call site (#444 round 3).
