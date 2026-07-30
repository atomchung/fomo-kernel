# Trigger-reliability matrix (#458)

FOMO Kernel ships as a skill: a user types an opening message into Claude
Code, Codex, or Antigravity, and either the skill triggers or the host
answers as a generic assistant with no idea the capability exists. Nothing in
the repository measured that before #458. This directory is the instrument
that decides it, per host x locale, for the two capabilities that matter --
review and the pre-trade evaluation M1 adds -- while proving that a plainly
adjacent, off-topic request does not fire the skill by accident.

**Scope of this directory (Wave A, per #458's frozen-parity comment):**
corpus, runner, result format. It never edits `skills/fomo-kernel/SKILL.md`'s
trigger description, never calls a host, and never spends a token. Wave C
(the owner-scheduled, billable holdout run against the integrated surface)
and Wave B (the single M1 integration owner's trigger-description iteration)
both consume what lives here; neither happens inside it.

```bash
python3 evals/triggers/run_triggers.py validate       # corpus only, no network
python3 evals/triggers/run_triggers.py dry-run         # + prints the attempt plan; still no network
python3 evals/triggers/run_triggers.py record ...      # append one already-observed attempt
python3 evals/triggers/run_triggers.py score ...       # compute the per-cell rates from a result file
```

## Files

| Path | Role |
|---|---|
| `corpus/<locale>/<split>.json` | the corpus: 6 files, 60 prompts each, 360 total |
| `schema/prompt-corpus.schema.json` | readable contract for one corpus file |
| `schema/trigger-attempt.schema.json` | readable contract for one result-file line |
| `run_triggers.py` | the loader, plan builder, and scorer -- the whole enforcement |
| `../../tests/test_triggers.py` | offline, deterministic tests, plus the mutation probes for the checks below |

The schema files are documentation, the same posture
`evals/episodes/episode.schema.json` takes: the offline suite carries no
`jsonschema` dependency, so `run_triggers.py`'s own loader (`load_corpus`,
`load_corpus_file`, `read_result_file`, `build_attempt`) is the actual
enforcement, and a field the loader accepts must be a documented property.

## Why the corpus is organized per locale, not per (host, locale)

#458's Matrix section names "host x locale cells", which invites building one
corpus per cell -- nine copies of sixty prompts. That would be the wrong
shape. A host is an *execution* surface: it decides whether a given opening
message routes correctly, but it has no say in what the message *is*. The
same `zh-TW` prompt is sent, byte-for-byte, into Claude Code, Codex, and
Antigravity -- what should legitimately vary across hosts is the outcome,
never the input. The corpus therefore has exactly one dimension for content
(locale, three values) and one dimension carried only in the *result log*
(host, free-form, see below); a host x locale cell is produced at scoring
time by crossing every host that appears in a result file against every
locale in the corpus, not by adding files.

This also directly serves the disjointness requirement: nine near-identical
copies of the same sixty prompts would multiply, by nine, the chance of an
accidental calibration/holdout leak introduced while hand-maintaining
duplicates -- exactly the "mirror tax" this repository's own
`docs/development-guide.md` names as its largest measured cost. One corpus,
read by every host, is the generating source; nothing here is hand-mirrored.

## The three prompt classes

- **`review_positive`** (20 per split per locale): the user is asking for a
  retrospective review of trades they already made -- a CSV/statement
  upload, a specific-trade postmortem, a behavioral-pattern question over
  real history, a returning weekly check-in.
- **`pre_trade_positive`** (20 per split per locale): the user is weighing a
  trade they have not made yet, against their own book -- an add, a trim, a
  stop-loss decision, a rule-collision check. This is M1's capability
  (`review.py consider`, reached through plain conversation per #479's
  visible-challenge contract).
- **`adjacent_negative`** (20 per split per locale): market, research,
  education, or general-advice content that must **not** trigger the skill.
  These are the hard cases and the entire point of the corpus -- a negative
  set built from obviously off-topic prompts ("what's the weather") would
  measure nothing, because nothing resembling that ever reaches a trading
  skill's trigger surface by accident. Every prompt here is deliberately
  aimed at the boundary of one of the two positive classes above, or at the
  general shape of "this is trading-adjacent language with no personal
  decision or personal history attached to it."

Every `adjacent_negative` prompt declares `near_miss_of`
(`review_positive`, `pre_trade_positive`, or `general`) plus an English
`note` explaining exactly what makes it a near-miss rather than an obviously
unrelated prompt. That declaration is not decorative: `tests/test_triggers.py`
checks that every locale/split's twenty `adjacent_negative` prompts cover all
three values, so the corpus cannot quietly drift toward only the easy,
obviously-off-topic cases.

Three representative pairs (full set, with the reasoning for every single
one, is in the corpus files themselves -- `note` on every prompt):

1. **Near `pre_trade_positive`.** *"Is NVDA a good buy right now?"*
   (`en-cal-adj-01`) reads like a buy decision, but there is no reference to
   the user's own holdings, reason, or book -- it is the trigger-case table's
   "stock recommendation" exclusion, not a weighed trade against a real
   portfolio. The genuine positive counterpart in the same corpus,
   `en-cal-pre-01`, opens with "I already own some NVDA... am I chasing if I
   add more here" -- the book reference is exactly what is missing from the
   negative.
2. **Near `review_positive`.** *"What is averaging down, and why do traders
   do it?"* (`en-cal-adj-06`) uses review-adjacent vocabulary but is purely
   definitional, with no reference to any trade the user actually made. Its
   positive counterpart, `en-cal-rev-03`, asks the skill to check *my own*
   trade history for exactly that pattern ("a pattern of averaging down on
   losers instead of admitting I was wrong").
3. **`general`.** *"Explain covered calls to me"* and *"What's the S&P 500
   doing today"* (`en-cal-adj-11`, `en-hold-adj-12`) are the issue's own
   canonical examples: options education and a market-data lookup, neither
   tied to a personal decision or a personal trade history at all. Every
   locale carries its own equivalents rather than a translation of the
   English pair -- see "Locale authenticity" below.

The same taxonomy repeats in `zh-TW` and `zh-CN`, with locale-native
scenarios rather than translations of the English set (a Taiwan-specific
example: `zh-TW-cal-adj-01` asks a generic buy-verdict question about a
TW-listed name, paired against `zh-TW-cal-pre-01`'s book-referencing add
decision on the same kind of holding; a Mainland-specific example:
`zh-CN-cal-adj-01` does the same with an A-share name and Mainland retail
vocabulary).

## Locale authenticity: zh-TW and zh-CN are not each other's transliteration

`docs/language-policy.md` and #458's own text are explicit that Traditional
and Simplified Chinese retail-trading prompts are not produced by converting
one script to the other -- vocabulary and phrasing genuinely differ in how
people actually talk about trading in each market, and a corpus built by
machine conversion would measure a dialect nobody speaks. This corpus was
authored independently per locale rather than translated:

- **zh-TW** prompts use Taiwan retail vernacular (e.g. stop-loss/take-profit
  as a matched pair distinct from the Mainland pair, averaging-down phrased
  with the term Taiwan traders actually use, "review" phrased as a plain
  verb rather than the Mainland's chess/go-derived noun for a post-game
  review) and mix Taiwan-listed instruments with US names, reflecting that
  Taiwanese retail investors commonly trade both markets through the same
  account.
- **zh-CN** prompts use Mainland retail vernacular (a different
  stop-loss/take-profit pair, a different averaging-down phrase, the
  chess/go-derived review term that is standard among Mainland retail
  traders but not typically used in Taiwan) and mix A-share and other
  Mainland-accessible instruments with US ADR names.
- The two locale corpora deliberately use **different example instruments**
  from each other and from the English set, for the same reason: matching
  tickers across all three locales would make the zh-CN file look like a
  respelling of the zh-TW file even where the wording itself is independent.

Every prompt's English `note` field documents the design intent regardless of
the prompt's own locale, so the corpus stays maintainer-legible without
requiring a second, mirrored English paraphrase of every prompt (which would
itself be exactly the kind of hand-mirror `docs/development-guide.md` warns
against). This README cannot quote the zh-TW/zh-CN prompt text directly:
`tests/test_doc_language.py` enforces implementation Markdown as English-only
across the whole `evals/` tree, so every non-English example above is given
by prompt id plus an English gloss rather than an inline quotation.

## Calibration vs. holdout

Every locale has two disjoint sixty-prompt sets. #458's release rule: *"Only
one bounded description iteration may use the calibration set. If any holdout
cell still fails, the eval produces a second-thin-entrypoint recommendation
rather than weakening intent separation."* That means:

- Calibration exists to be looked at while iterating on
  `skills/fomo-kernel/SKILL.md`'s trigger description (Wave B, after this
  Wave A corpus exists and after Wave C's first holdout run, by the single
  M1 integration owner with explicit approval -- never by this directory).
- Holdout must never be read, quoted, or reasoned about while writing or
  adjusting that description. It exists to answer the release question once,
  honestly.

**The runner proves disjointness instead of trusting the author.**
`load_corpus()` checks, per locale: no prompt id appears in both splits, and
no two prompts anywhere in the pair have the same *normalized* text
(casefolded, whitespace-collapsed). That is a real, mechanical guarantee
against copy-paste or a near-duplicate paraphrase slipping in. It is **not**
a guarantee that holdout is *semantically* distinct from calibration -- two
differently-worded prompts that test the identical scenario would pass this
check and still under-test generalization. Authoring genuinely different
scenarios per split (different specific situations, not just different
phrasing of the same one) is a human discipline this corpus followed and the
mechanical check can only backstop, never replace. Every calibration/holdout
pair in this corpus targets a different concrete scenario -- different
ticker, different rule, different situational detail -- and never merely
reworded the calibration prompt.

## Every prompt is generic and synthetic

No real ticker the owner holds, no real amount, no private content, per this
repository's privacy boundary (`docs/maintainer-guide.md`, `tools/privacy_lint.py`). This
corpus contains only well-known public company names and instruments used
generically -- exactly the convention `skills/fomo-kernel/mock/*.csv` already
uses for its own synthetic fixtures (NVDA, AMD, CVX, and similar large caps
appear there as ordinary example data, not as anyone's real holding). Nothing
here needed `tools/privacy_lint.py` run against it, because nothing here was
converted from a real trade record; the lint's job starts where this
directory's does not.

## The result format

A result file is JSONL (one JSON object per line), append-only -- the same
shape `skills/fomo-kernel/tools/ux_receipt.py` already uses for its own
cross-client presentation trace, and for the same reason: an append can never
race or corrupt a concurrent writer, unlike a read-modify-write JSON array
would. `record` is the only subcommand that writes one; `score` folds and
scores whatever a result file contains, and never mutates it.

Every line (`schema/trigger-attempt.schema.json`) carries exactly what #458
asks a runner to record per attempt: `host`, `host_version`, `model`,
`installed_skill_population`, `locale`, `split`, `prompt_id`, `class`,
`expected_route`, `actual_route`, and `raw_outcome`, plus an auto-stamped
`ts` and a `schema_version`. `expected_route` and `class` are always derived
from `prompt_id` against the corpus -- `record` looks the prompt up itself,
so an operator cannot record a self-contradictory expectation by mistyping a
flag. `actual_route` is one of `review` / `pre_trade` / `no_trigger`; a host
error, refusal, or otherwise broken response is recorded as `no_trigger`
(the fomo-kernel capability was not delivered either way) with the specifics
kept in `raw_outcome`, rather than adding a fourth, harder-to-score outcome
value.

A **not-run cell is never a pass.** `score` reports, for every (host,
locale, split, class) it looks at, one of three statuses: `not_run` (zero of
the twenty prompts attempted), `incomplete` (some but not all twenty --
recall against a fixed 20-prompt denominator is not knowable from fewer, so
this is reported distinctly and never scored, extending "not run never reads
as a pass" to a partial sample rather than only to a completely untouched
cell), or `complete` (all twenty attempted, and only this status may carry a
`pass`/`fail` verdict). `score`'s report always lists the full requested
(host x locale x split) matrix, including cells with zero attempts, so an
unreached host is visible as `not_run` rather than silently absent from the
table.

**Cells are never pooled.** Every row prints all three class rates
independently; nothing sums or averages review/pre_trade/adjacent_negative
into one number, and nothing sums or averages across hosts or locales. A row
"passes the gate" only when all three of its own classes independently
report `verdict == "pass"` -- a per-row boolean **alongside** the three
numbers that produced it, never a replacement for printing them.

## Why there is no `--live` flag

Claude Code, Codex, and Antigravity are interactive products, not HTTP
endpoints this repository can script. There is no automation anywhere in
this codebase -- and building one is far outside a corpus-and-runner change
-- that opens one of those clients, types a prompt, and reads back whether a
skill fired. Wave C execution is therefore a human or agent operator manually
running each planned prompt inside a real host session and then calling
`record` once per observed attempt, the same shape `docs/qa-runbook.md`
already uses for dogfood QA. That manual act, which has already happened by
the time `record` runs, is the opt-in, billable step. `dry-run` is everything
that can honestly run before it: it validates the corpus and prints (or
writes, with `--out`) exactly what would be sent to which host, locale, and
split, and it never imports anything that could reach a network --
`tests/test_triggers.py` greps `run_triggers.py`'s source for exactly that
and fails if a network-capable import or call ever appears.

## What this corpus does not prove

Stated plainly, so a green `validate`/`dry-run` is never over-trusted:

- **It does not measure trigger reliability by itself.** Everything in this
  directory is inert until Wave C's owner-scheduled holdout run happens
  against the real, integrated hosts. `validate` and `dry-run` prove the
  instrument is sound; they prove nothing about the product.
- **Disjointness is mechanical on text, not on meaning.** See "Calibration
  vs. holdout" above -- the check catches copy-paste and near-duplicate
  phrasing, not two differently-worded prompts that happen to test the same
  underlying scenario.
- **`installed_skill_population` is exactly as honest as the operator makes
  it.** There is no way for this offline tool to independently verify what a
  live host actually had installed at the moment of one attempt; the field
  exists so that fact is recorded at all; whether it is recorded accurately
  is procedural, the same limitation `docs/qa-runbook.md` states plainly
  for its own `--client` and `human_involvement` fields.
- **A `pass` on this matrix is not a claim about answer quality.** This
  measures only whether the correct *capability* engaged (review vs.
  pre-trade vs. neither) -- never whether the resulting review or
  evaluation was any good once it did.

## Extending the corpus

1. Pick the class and locale, and write the prompt in that locale's own
   voice -- never translate an existing prompt from another locale.
2. For `adjacent_negative`, name a genuine near-miss: which positive class's
   boundary it sits near (or `general`), and why a reasonable trigger
   description might wrongly catch it. If you cannot state the near-miss
   reason, the prompt likely belongs in the "obviously off-topic" pile this
   corpus deliberately does not need more of.
3. Add it to the correct `corpus/<locale>/<split>.json` file, keeping each
   class at exactly 20 entries — `python3 evals/triggers/run_triggers.py
   validate` fails loudly on any other count, on an id collision anywhere in
   the corpus, or on any calibration/holdout text overlap.
4. Never add to `holdout` once a Wave C run has scored against it for a
   release decision -- that would silently invalidate the "untouched
   holdout" guarantee #458's release rule depends on. Grow calibration, or
   open a new holdout generation as an explicit, named event instead.
5. Run `python3 tests/test_triggers.py`.

## Handoff: what stays out of this directory

- **Editing `skills/fomo-kernel/SKILL.md`'s trigger description** (the
  bounded calibration-informed iteration, and the eventual second
  thin-entry-point name/description if holdout still fails after it) is
  reserved for the single M1 integration owner, after Wave C's evidence
  exists, with explicit owner approval (#458). Nothing here should ever be
  read as a recommendation for specific wording.
- **Running Wave C itself** — the actual host calls — is the owner-scheduled
  billable step this directory only prepares for.
- **#488's bounded owner-live acceptance walk** is a separate, smaller,
  end-to-end sample on the hosts/locales this matrix already declared
  supported, not a rerun of the full corpus.
