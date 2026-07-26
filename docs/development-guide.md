# Development guide — failure modes and the discipline that prevents them

Status: living document. Born from the 2026-07-26 architecture review of the
2026-07-11 → 07-26 window (127 commits, 43 of them fixes). Owner direction,
2026-07-26: this file is the development knowledge we maintain — new lessons
land here with receipts, instead of accumulating as scattered session notes.

Scope: how changes to this repository go wrong, and the small rule set that
prevents the expensive reruns. It complements, and never repeats:

- [CLAUDE.md](../CLAUDE.md) — maintainer mechanics: tests, hooks, privacy
  boundary, PR conventions, the mirrored-surfaces table
- [decision-fomo-kernel-shape.md](decision-fomo-kernel-shape.md) — product
  shape and the engine/model boundary (Layers 1–3)
- [eval-design.md](eval-design.md) — evaluation methodology

Update bar: a pattern earns a place here only after it shipped a real mistake,
and it arrives with its receipts (issue/PR numbers). Prefer tightening an
existing section over appending a new one; if this file passes ~200 lines,
something should be deleted or promoted into a mechanical gate.

## 0. The discriminant every other rule hangs on

Every rule in this repository is one of two kinds. Most calibration mistakes
are treating one kind as the other.

| | Invariant — "must never happen" | Behavior — "say it like this" |
|---|---|---|
| Examples | one numeric source of truth; append-only state; fail-closed identity; privacy exits; frozen question surfaces | wording, templates, ordering, tone, which observation matters right now |
| Enforce in | the mechanical layer: engine `raise`, schema, validator, deterministic test | model latitude, steered by evals; hard-coded only while models are too weak to hold it |
| As models strengthen | appreciates — a more capable agent has more exits to guard | depreciates — enumerated sentences block improvement that would otherwise arrive for free |

The agents running this skill are user-supplied (Claude Code, Codex,
Antigravity — strong and weak models, mixed, indefinitely). A prompt-layer
prohibition is unreliable for the weak model and wasted context for the strong
one; the only layer that binds both ends is the mechanical one. Corollary,
proven by the 2026-07 instruction slimming (#399, #407): a prohibition the
engine already fail-closes is wasted prompt. Only what the engine cannot catch
belongs in instructions, and it is written as *why*, not as a bare ban.

**Loosening guard.** Before relaxing a rule you have classified as behavior,
prove it is not a load-bearing invariant: the slimming removed the only
protection for required-question visibility because it read like style
(caught in the PR #399 review). Locked fragments in the test suite force
exactly this check — run `python3 tests/run_all.py` before editing
instruction text, and treat a broken lock as a question to answer, not an
obstacle to rephrase around.

## 1. Mirror tax — the largest measured cost

**Signature.** One fact stored in N files, kept aligned by hand or by an
equality test. The mirrored-surfaces table in CLAUDE.md is the census.

**Measured (#402 baseline).** Median change fans out to 4 surfaces; a wording
change had a 0.96 probability of touching engine code; tests are touched in
93% of changes. In the review window, `card_renderer.py` churned 1.8× its own
line count.

**Rules.**

- Before adding a surface that must stay synchronized, generate it from the
  single source at build time. `skills/fomo-kernel/tools/design_bundle.py` is
  the precedent; #401 applies the same move to `card-template.html` and #402
  knife 5 applies it to test expectations (`tests/copy_corpus.py`). A hand
  mirror guarded by an equality test is the fallback, not the default —
  including inside a test function, where a literal copied out of
  `copy/*.json` is the same mirror wearing an assertion.
- Work the #402 knives in order and re-run
  `skills/fomo-kernel/tools/change_surface.py` after each one; a knife that
  does not move its number stops the line. Check the number is one the tool
  can see first: fan-out and coupling measure how many surfaces a change
  crosses, so a knife that leaves a surface in place and only stops it being
  hand-written is invisible to them. That is what the authored-churn reading
  exists for, and adding a reading is the right response to a blind spot —
  declaring the knife unmeasurable is not.
- Transition rule while #390 (single canonical language, generated catalogs)
  is pending, adopted 2026-07-26: new copy keys land in `en` only; other
  locales take the documented fallback. Strike this line when #390 ships.
  Three hand-maintained catalogs are this tax at its purest.

## 2. The fake-green family — five ways a check passes without looking

A fake green is a passing check that never observed the thing you changed.
Five types have shipped here; they share one root: **the verification surface
was not the delivery surface.**

1. **Structure, not content** — asserts the block exists, not what it says
   (#363).
2. **No pinned extremes** — "measure at least two viewport widths" was
   satisfied with two widths wide enough to hide the truncation at 360px, and
   fixtures used 3-digit amounts for a product whose own README shows 6
   digits (#382). Comply-and-hide is the failure mode of every "at least N"
   gate.
3. **Claimed-run, not run** — a verification asserted in prose with no
   artifact behind it.
4. **Harness surface ≠ delivery surface** — byte-parity compared Markdown
   only, so a KPI refactor moved 112 of 156 HTML cards under a green sweep
   (#394 fixed the blindness).
5. **Right assertion, wrong layer** — an argparse-level test for a
   handler-level `raise`; only the mutation run exposed it (PR #399 review).

**Rules.**

- A new checker lands with proof its matching mutation fails.
  [eval-design.md](eval-design.md) already states it; the PR template now
  asks for the evidence.
- Before touching a renderer or output path: list which surfaces the changed
  code path reaches (grep the callers), and name the oracle that covers each
  one. This exact move caught two live blindspots (#363, #394).
- Measurement gates pin their extremes explicitly: narrowest supported width,
  longest unbreakable token, largest realistic magnitude, fewest cells — and
  check overflow on both axes (`scrollWidth` caught what `scrollHeight`
  passed, PR #384). "At least N" invites compliant sampling of the safe
  region.

## 3. Static-rule-ification — the upstream generator of complexity

**Signature.** A runtime judgment hard-coded as an enum, table, or priority
list. Every exception then becomes a branch plus a test plus, often, a
mirror — this is where fan-out is born.

**Receipts.** Two condition templates meant 8 of 12 personas received the
identical sentence (#400). `metric_key not in metrics → ReviewError` threw
away exactly the most informative input a user can give — the condition our
defaults did not anticipate (#412). `candidate_rules[].verify` was written
for display and read by no mechanical consumer: a dead field that repeatedly
invited misreading.

**Rules.**

- The enum-gated-surface standard (the #412 pattern), for any surface an
  agent or user will stretch: an enum for what is mechanically decidable;
  `unmapped` as a first-class, honest state for what is not; never silently
  drop, never guess.
- Situational priority belongs to the agent at runtime. The owner ratifies
  facts, budgets, and red lines — not orderings. Phrase design questions as
  "which facts must the engine expose so the agent can judge well," not
  "which static ordering do we pick."
- Every stored field needs a mechanical consumer, or it goes. A
  written-never-read field is schema debt (the `verify` lesson).
- The second recurrence of a symptom is a design issue, not a second patch
  (#357: the cash-anchor gap recurred three times as one-off patches before
  it was named a design problem).

## 4. Order discipline

- **Tax removal before features.** After the 2026-07-18 review named the
  refactors, feature PRs cut the line and the hot files grew 1.8–2.8× in a
  week (#216 → #402). The knives are scheduled work, not background wishes.
- **Gates before loosening.** The recommendation ban lifts only after Layer 2
  exists ([decision-fomo-kernel-shape.md](decision-fomo-kernel-shape.md) §7)
  *and* free-form answers have a mechanical provenance gate (#414). The
  honesty ledger gates cards only; an answer surface without its own gate
  would be the loosest point in the system.
- **Eval shape ships in the same PR as the capability.** The researched tier
  (#412) lands together with its adversarial episodes: a criterion restated
  as a yes/no query must be caught, an uncheckable condition must fall to
  `unmapped`, and two-sided reasoning must actually carry both sides.

## 5. The never-loosen list

Loosening any of these is a product regression, not a style change:

- Numbers come from engine artifacts only; agent prose derives nothing.
- Frozen question surfaces replay byte-identically on resume.
- Append-only state, engine-assigned content-addressed identity, idempotent
  finalize, fail-closed on same-ID conflict.
- Privacy exits: the public-card whitelist, local-only trade data, and
  `skills/fomo-kernel/tools/privacy_lint.py` on any real-data text bound for
  this public repository.
- Artifact generation is not presentation.
- `derived`/`fetched` reconciliation stays deterministic; `researched`
  verdicts never enter `held_streak` or graduation statistics (#412).

## 6. Changing instruction surfaces

- Guaranteed-delivery entry points are `SKILL.md` and `AGENTS.md`; every
  other document is soft-routed and may never be read. A rule that must
  always land is either repeated at those entry points — a test-enforced
  contract, see `test_review_py_is_a_non_negotiable_boundary` — or enforced
  mechanically. Repetition there is a delivery contract, not redundancy;
  find the enforcing test before cleaning it up.
- Do not trust pattern counts of prohibitions ("N occurrences of *never*").
  On inspection, most hits describe engine behavior the agent relies on to do
  *less* work; deleting them creates work. Read and classify before
  concluding.
