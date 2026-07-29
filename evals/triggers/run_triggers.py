#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Corpus loader, plan builder, and scorer for the trigger-reliability matrix (#458).

#458 exists because "the pre-trade capability is not shipped on a host/language
where an explicit request silently receives a generic assistant answer" is
otherwise undetectable -- a host either routes an opening message into the
fomo-kernel skill or it does not, and nothing today records which. This module
is the instrument, not the measurement: it owns the corpus format, validates
it, builds the attempt plan, and scores a result file against the release
thresholds. It never runs a host and never will -- see "Why there is no --live
flag" below.

## The corpus is per locale, not per (host, locale)

The issue's Matrix section names "host x locale cells", and it is tempting to
read that as one corpus per cell. It is not: a host is an *execution* surface,
not a *content* dimension. The same `zh-TW` prompt is sent, unmodified, to
Claude Code, Codex, and Antigravity -- what varies per host is whether it
routes correctly, never what was asked. Building 9 copies of the same 60
prompts would not measure anything a 3-locale corpus does not, and it would
triple the chance of an accidental calibration/holdout leak. `evals/triggers/
corpus/<locale>/<split>.json` is therefore the whole corpus; a "cell" is
produced at *scoring* time by crossing every host in a result file against
every locale in the corpus (`build_report` below), never by adding files.

## Three prompt classes, two splits, one fixed denominator

Every `<locale>/<split>.json` carries exactly 20 `review_positive`, 20
`pre_trade_positive`, and 20 `adjacent_negative` prompts (`PROMPTS_PER_CLASS`).
`load_corpus()` proves this by count rather than trusting the author, and
proves calibration/holdout disjointness by both prompt id and normalized text
-- "the runner must prove it rather than trusting the author" from the issue,
taken literally. The three release thresholds (`MIN_CORRECT`) are all
expressed as "at least N of the fixed 20 are correct", which is why a partial
sample is never scored: recall against a 20-prompt corpus is not knowable from
15 attempts, so `evaluate_class` reports `incomplete` rather than computing a
rate against a smaller denominator and calling it done.

## The result format is an append-only per-attempt log, not a summary file

`record` appends one JSON object per line to a `--result-file` (JSONL, the
same shape ``skills/fomo-kernel/tools/ux_receipt.py`` already uses for a
cross-client presentation trace, for the same reason: append is the only
operation that can never race or corrupt a concurrent writer). Every line
carries exactly what the issue asks for -- host, host version, model, the
skill population installed on that host at invocation time, locale, expected
route, actual route, and the raw outcome -- plus an auto-stamped `ts`.
`score` folds the log (`fold_attempts`: last write per (host, locale, split,
prompt_id) wins, file order is the ordering authority, matching
`ux_receipt.py`'s own stated precedent that row order rather than `ts` decides
current-ness) and computes the three rates per (host, locale, split) cell.
There is deliberately no second, mutable "summary" file this could drift from:
`score --out` writes a computed report on demand, the way `ux_receipt.py
verify` computes a report from the raw trace rather than maintaining one.

## Why there is no --live flag

Claude Code, Codex, and Antigravity are interactive products, not HTTP
endpoints this repository can script -- there is no automation anywhere in
this codebase that opens one of those clients, types a prompt, and reads back
whether a skill fired, and building one is far outside a corpus-and-runner
change. Wave C execution is therefore a human or agent operator manually
running each planned prompt inside a real host session and then calling
`record` once per observed attempt -- the same shape `docs/qa-runbook.md`
already uses for dogfood QA. That manual act, which has already happened by
the time `record` is invoked, is the opt-in, billable step; `dry-run` is
everything that can honestly run before it, and it never leaves the local
filesystem or imports anything that could reach a network (`test_triggers.py`
greps this file for exactly that).

## What this module does not decide

`score`'s gate tells you whether the *measured* surface meets the release
thresholds. It does not, and must not, decide the two product questions #458
reserves for the M1 integration owner after the holdout evidence exists: what
`skills/fomo-kernel/SKILL.md`'s trigger description should say, and whether a
second thin entry point is warranted. Both require editing runtime files this
module's tests refuse to touch.

Usage:
  python3 evals/triggers/run_triggers.py validate
  python3 evals/triggers/run_triggers.py dry-run [--hosts h1,h2] [--locales en,zh-TW] [--splits holdout]
  python3 evals/triggers/run_triggers.py record --result-file out.jsonl \\
      --host codex --host-version "0.45.0" --model "GPT-5.6 Codex" \\
      --skill-population "fomo-kernel" --locale zh-TW --split holdout \\
      --prompt-id zh-TW-hold-pre-03 --actual-route pre_trade --raw-outcome "..."
  python3 evals/triggers/run_triggers.py score --result-file out.jsonl
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import sys

TRIGGERS_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_CORPUS_DIR = TRIGGERS_DIR / "corpus"

# #458's Matrix section. Locales are the corpus dimension (see module
# docstring); hosts are an execution dimension carried only in the result log,
# so `record` accepts any non-empty host string -- HOSTS is the M1-scoped
# default matrix for `dry-run`/`score`, not a closed enum enforced on input
# (`--client` in ux_receipt.py makes the same choice, for the same reason: the
# tool should not need editing every time a new host shows up).
LOCALES = ("en", "zh-TW", "zh-CN")
SPLITS = ("calibration", "holdout")
CLASSES = ("review_positive", "pre_trade_positive", "adjacent_negative")
PROMPTS_PER_CLASS = 20
HOSTS = ("claude_code", "codex", "antigravity")
ROUTES = ("review", "pre_trade", "no_trigger")
# What an adjacent_negative prompt sits near, for coverage reporting: pure
# market/research/education content that is not closer to either positive
# class reads as "general" (see test_triggers.py's boundary-coverage check).
NEAR_MISS_TARGETS = ("review_positive", "pre_trade_positive", "general")

EXPECTED_ROUTE_BY_CLASS = {
    "review_positive": "review",
    "pre_trade_positive": "pre_trade",
    "adjacent_negative": "no_trigger",
}

# The three release thresholds (#458), expressed uniformly as "at least N of
# 20 are correct" so one comparison implements both framings in the issue
# text: review/pre_trade read this as recall; adjacent_negative reads it as
# 20 minus the false-trigger count (at most 1/20 false triggers == at least
# 19/20 correctly not triggering).
MIN_CORRECT = {
    "review_positive": 18,
    "pre_trade_positive": 19,
    "adjacent_negative": 19,
}

PROMPT_FIELDS = {"id", "class", "text", "note", "near_miss_of"}
REQUIRED_ATTEMPT_FIELDS = {
    "host", "host_version", "model", "installed_skill_population", "locale",
    "split", "prompt_id", "class", "expected_route", "actual_route", "raw_outcome",
}


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_text(text):
    """Casefold + collapse whitespace. The operative, mechanically-checkable
    definition of "overlap" between calibration and holdout text (module
    docstring); it deliberately does not attempt fuzzy/paraphrase similarity,
    which is not deterministic-friendly -- authoring genuinely distinct
    holdout scenarios is a human authoring discipline this can only backstop,
    not replace (see evals/triggers/README.md)."""
    return " ".join(text.strip().casefold().split())


# ─────────────────────────────── corpus loading ──────────────────────────────

def corpus_path(corpus_dir, locale, split):
    return pathlib.Path(corpus_dir) / locale / f"{split}.json"


def validate_prompt(raw, path, index):
    """Structural validation for one prompt entry. Fail-closed: a typo must
    surface as a problem, never as a silently-dropped or silently-miscounted
    prompt (which would corrupt the exact-20 count check downstream)."""
    problems = []

    def require(condition, message):
        if not condition:
            problems.append(f"{path}: prompts[{index}]: {message}")

    if not isinstance(raw, dict):
        return [f"{path}: prompts[{index}]: prompt must be an object"]
    unknown = set(raw) - PROMPT_FIELDS
    require(not unknown, f"unknown field(s): {sorted(unknown)}")
    require(isinstance(raw.get("id"), str) and raw.get("id").strip(), "id must be a non-empty string")
    require(raw.get("class") in CLASSES, f"class must be one of {CLASSES}, got {raw.get('class')!r}")
    require(isinstance(raw.get("text"), str) and raw.get("text").strip(),
            "text must be a non-empty string")
    require(isinstance(raw.get("note"), str) and raw.get("note").strip(),
            "note must be a non-empty string (maintainer-facing design rationale)")
    near_miss = raw.get("near_miss_of")
    if raw.get("class") == "adjacent_negative":
        require(near_miss in NEAR_MISS_TARGETS,
                f"adjacent_negative prompts must declare near_miss_of in {NEAR_MISS_TARGETS}")
    else:
        require(near_miss is None, "near_miss_of only applies to adjacent_negative prompts")
    return problems


def load_corpus_file(corpus_dir, locale, split):
    """Returns ``(prompts, problems)``. ``prompts`` is ``[]`` whenever
    ``problems`` is non-empty -- a caller must never combine a partially-valid
    file's prompts with a downstream count check, or a structural error could
    misreport as a wrong-count error instead of the real problem."""
    path = corpus_path(corpus_dir, locale, split)
    if not path.is_file():
        return [], [f"{path}: missing corpus file"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], [f"{path}: invalid JSON: {exc}"]
    if not isinstance(raw, dict):
        return [], [f"{path}: corpus file must be a JSON object"]
    problems = []
    unknown = set(raw) - {"locale", "split", "prompts"}
    if unknown:
        problems.append(f"{path}: unknown top-level field(s): {sorted(unknown)}")
    if raw.get("locale") != locale:
        problems.append(f"{path}: locale field {raw.get('locale')!r} does not match its own directory ({locale!r})")
    if raw.get("split") != split:
        problems.append(f"{path}: split field {raw.get('split')!r} does not match its own filename ({split!r})")
    prompts = raw.get("prompts")
    if not isinstance(prompts, list):
        problems.append(f"{path}: prompts must be a list")
        return [], problems

    seen_ids = set()
    for index, item in enumerate(prompts):
        item_problems = validate_prompt(item, path, index)
        problems.extend(item_problems)
        pid = item.get("id") if isinstance(item, dict) else None
        if isinstance(pid, str) and pid:
            if pid in seen_ids:
                problems.append(f"{path}: duplicate id within file: {pid!r}")
            seen_ids.add(pid)
    if problems:
        return [], problems

    counts = collections.Counter(item["class"] for item in prompts)
    stray = set(counts) - set(CLASSES)
    if stray:
        problems.append(f"{path}: unknown class value(s) in counts: {sorted(stray)}")
    for class_name in CLASSES:
        found = counts.get(class_name, 0)
        if found != PROMPTS_PER_CLASS:
            problems.append(
                f"{path}: class {class_name!r} has {found} prompt(s), expected exactly {PROMPTS_PER_CLASS}")
    if problems:
        return [], problems
    return prompts, []


def load_corpus(corpus_dir=DEFAULT_CORPUS_DIR):
    """Returns ``(corpus, problems)``. ``corpus[locale][split]`` is a list of
    prompt dicts. Every mechanical proof #458 asks for happens here: exact
    20/20/20 counts per file (``load_corpus_file``), calibration/holdout
    disjointness by id *and* by normalized text, and id uniqueness across the
    *entire* corpus (catches a copy-paste across a locale or split boundary
    that a per-file check cannot see). A non-empty ``problems`` means the
    corpus must not be used for a plan, a record lookup, or a score -- callers
    check this before doing anything else.
    """
    corpus_dir = pathlib.Path(corpus_dir)
    corpus = {locale: {} for locale in LOCALES}
    problems = []
    for locale in LOCALES:
        for split in SPLITS:
            prompts, file_problems = load_corpus_file(corpus_dir, locale, split)
            problems.extend(file_problems)
            corpus[locale][split] = prompts

    for locale in LOCALES:
        cal = corpus[locale].get("calibration") or []
        hold = corpus[locale].get("holdout") or []
        if not cal or not hold:
            continue  # already reported above; do not cascade a second error
        cal_ids = {p["id"] for p in cal}
        hold_ids = {p["id"] for p in hold}
        id_overlap = cal_ids & hold_ids
        if id_overlap:
            problems.append(f"{locale}: calibration and holdout share id(s): {sorted(id_overlap)}")
        cal_by_text = {}
        for p in cal:
            cal_by_text.setdefault(_normalize_text(p["text"]), []).append(p["id"])
        hold_by_text = {}
        for p in hold:
            hold_by_text.setdefault(_normalize_text(p["text"]), []).append(p["id"])
        text_overlap = set(cal_by_text) & set(hold_by_text)
        if text_overlap:
            pairs = sorted(
                f"{cal_by_text[t]}=={hold_by_text[t]}" for t in text_overlap
            )
            problems.append(f"{locale}: calibration and holdout share normalized prompt text: {pairs}")

    all_ids = collections.defaultdict(list)
    for locale in LOCALES:
        for split in SPLITS:
            for p in corpus[locale][split]:
                all_ids[p["id"]].append(f"{locale}/{split}")
    for pid, locations in sorted(all_ids.items()):
        if len(locations) > 1:
            problems.append(f"duplicate id {pid!r} across the corpus: {locations}")

    return corpus, problems


def find_prompt(corpus, locale, split, prompt_id):
    for prompt in corpus.get(locale, {}).get(split, []) or []:
        if prompt["id"] == prompt_id:
            return prompt
    return None


def corpus_totals(corpus):
    return sum(len(corpus[locale][split]) for locale in LOCALES for split in SPLITS if corpus[locale].get(split))


# ─────────────────────────────── attempt plan ────────────────────────────────

def build_plan(corpus, hosts, locales, splits):
    """The dry-run plan: every (host, locale, split, prompt) combination that
    Wave C would need to attempt. Pure and read-only -- building this touches
    nothing but the in-memory corpus."""
    attempts = []
    for host in hosts:
        for locale in locales:
            for split in splits:
                for prompt in corpus[locale][split]:
                    attempts.append({
                        "host": host,
                        "locale": locale,
                        "split": split,
                        "prompt_id": prompt["id"],
                        "class": prompt["class"],
                        "expected_route": EXPECTED_ROUTE_BY_CLASS[prompt["class"]],
                        "text": prompt["text"],
                    })
    return attempts


# ─────────────────────────── attempt record building ─────────────────────────

class AttemptError(ValueError):
    """A record request that cannot be honored: unknown prompt, empty
    required field, or an actual_route outside the known vocabulary. Raised
    rather than silently coerced -- fail-closed applies to what gets appended
    to a result log exactly as much as to what gets scored from one."""


def build_attempt(corpus, *, locale, split, prompt_id, host, host_version, model,
                   skill_population, actual_route, raw_outcome, ts=None):
    """Pure construction of one attempt record. ``expected_route`` is always
    derived from the corpus's own class for ``prompt_id`` -- never accepted as
    an input -- so an operator cannot record a self-contradictory expectation
    by mistyping a flag. Separated from ``cmd_record`` so tests can exercise
    it directly without a subprocess per case."""
    if locale not in LOCALES:
        raise AttemptError(f"unknown locale {locale!r}, expected one of {LOCALES}")
    if split not in SPLITS:
        raise AttemptError(f"unknown split {split!r}, expected one of {SPLITS}")
    prompt = find_prompt(corpus, locale, split, prompt_id)
    if prompt is None:
        raise AttemptError(f"no prompt {prompt_id!r} in {locale}/{split}")
    if actual_route not in ROUTES:
        raise AttemptError(f"unknown actual_route {actual_route!r}, expected one of {ROUTES}")
    host = (host or "").strip()
    host_version = (host_version or "").strip()
    model = (model or "").strip()
    raw_outcome = (raw_outcome or "").strip()
    if not host:
        raise AttemptError("host must not be empty")
    if not host_version:
        raise AttemptError("host_version must not be empty")
    if not model:
        raise AttemptError("model must not be empty")
    if not raw_outcome:
        raise AttemptError("raw_outcome must not be empty -- record what actually happened")
    population = skill_population if isinstance(skill_population, list) else \
        [part.strip() for part in (skill_population or "").split(",") if part.strip()]
    return {
        "schema_version": 1,
        "ts": ts or _now_iso(),
        "host": host,
        "host_version": host_version,
        "model": model,
        "installed_skill_population": population,
        "locale": locale,
        "split": split,
        "prompt_id": prompt_id,
        "class": prompt["class"],
        "expected_route": EXPECTED_ROUTE_BY_CLASS[prompt["class"]],
        "actual_route": actual_route,
        "raw_outcome": raw_outcome,
    }


def append_attempt(result_file, attempt):
    """The only mutation this module ever performs: append one JSON line to a
    result file the caller named explicitly. JSONL, not read-modify-write JSON
    -- an append can never race or corrupt a concurrent writer, the same
    reason ``skills/fomo-kernel/tools/ux_receipt.py`` picked it for its own
    append-only trace."""
    path = pathlib.Path(result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(attempt, ensure_ascii=False, sort_keys=True) + "\n")


# ─────────────────────────────── result reading ───────────────────────────────

def read_result_file(path):
    """Returns ``(attempts, problems)``. A malformed line is reported, never
    silently skipped -- but does not abort reading the rest of the file, so a
    single bad line cannot hide every attempt recorded around it. Score's gate
    verdict stays fail-closed regardless: a dropped line just becomes an
    attempt that never happened, which the completeness check already treats
    as ``incomplete``/``not_run`` rather than a pass."""
    path = pathlib.Path(path)
    if not path.is_file():
        return [], [f"{path}: result file not found"]
    attempts, problems = [], []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{path}:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(row, dict):
            problems.append(f"{path}:{line_number}: attempt must be a JSON object")
            continue
        missing = REQUIRED_ATTEMPT_FIELDS - set(row)
        if missing:
            problems.append(f"{path}:{line_number}: missing field(s): {sorted(missing)}")
            continue
        if row["actual_route"] not in ROUTES:
            problems.append(f"{path}:{line_number}: actual_route {row['actual_route']!r} not in {ROUTES}")
            continue
        if row["locale"] not in LOCALES or row["split"] not in SPLITS:
            problems.append(f"{path}:{line_number}: locale/split outside the known corpus dimensions")
            continue
        attempts.append(row)
    return attempts, problems


def fold_attempts(attempts):
    """Last-write-wins per (host, locale, split, prompt_id). File order is the
    ordering authority, not ``ts`` -- an append-only log can be re-recorded
    (a repeat trial, or a correction) and the newest line always describes the
    attempt's current status, exactly the precedent
    ``skills/fomo-kernel/tools/ux_receipt.py`` states for its own trace."""
    folded = {}
    for attempt in attempts:
        key = (attempt["host"], attempt["locale"], attempt["split"], attempt["prompt_id"])
        folded[key] = attempt
    return folded


# ─────────────────────────────── scoring ──────────────────────────────────────

def evaluate_class(expected_ids, cell_attempts, class_name):
    """The threshold arithmetic (#458's three release thresholds), and the
    not-run/incomplete/complete status a cell can be in. Pure and
    side-effect-free so it is directly unit-testable against synthetic
    fixtures, including the exact boundary the task specifies (18/20 passes
    review_positive, 17/20 fails it).

    ``expected_ids``: the corpus's fixed ``PROMPTS_PER_CLASS`` prompt ids for
    this (locale, split, class). ``cell_attempts``: prompt_id -> folded
    attempt, already scoped to one (host, locale, split) cell.

    A cell is:
      not_run     zero of the expected prompts have a recorded attempt.
      incomplete  some but not all 20 have one -- recall against a 20-prompt
                  corpus is not knowable from fewer, so this is reported
                  distinctly and never scored, extending "not run never reads
                  as a pass" to a partial sample rather than only to zero.
      complete    all 20 attempted -- ``verdict`` is the only state that may
                  be "pass".
    """
    total = len(expected_ids)
    attempted_ids = [pid for pid in expected_ids if pid in cell_attempts]
    attempted = len(attempted_ids)
    if attempted == 0:
        return {"status": "not_run", "attempted": 0, "total": total, "correct": None, "verdict": None}
    correct = sum(
        1 for pid in attempted_ids
        if cell_attempts[pid]["actual_route"] == EXPECTED_ROUTE_BY_CLASS[class_name]
    )
    if attempted < total:
        return {"status": "incomplete", "attempted": attempted, "total": total,
                "correct": correct, "verdict": None}
    passed = correct >= MIN_CORRECT[class_name]
    return {"status": "complete", "attempted": attempted, "total": total,
            "correct": correct, "verdict": "pass" if passed else "fail"}


def build_report(corpus, attempts, hosts, locales, splits):
    """Per (host, locale, split) row, each carrying all three classes'
    independent ``evaluate_class`` results. Never pooled: nothing here sums or
    averages across classes, hosts, or locales -- that is precisely how one
    failing surface would hide behind two passing ones (#458's "do not pool
    cells"). ``hosts`` is unioned with every host actually present in
    ``attempts`` so an unexpected host's data is never silently dropped from
    the report, and every requested (host, locale, split) combination gets a
    row even with zero attempts, so an unreachable cell is visible as
    ``not_run`` rather than simply absent.
    """
    folded = fold_attempts(attempts)
    by_cell = collections.defaultdict(dict)
    for (host, locale, split, prompt_id), attempt in folded.items():
        by_cell[(host, locale, split)][prompt_id] = attempt
    observed_hosts = {attempt["host"] for attempt in attempts}
    report_hosts = sorted(set(hosts) | observed_hosts)

    rows = []
    for host in report_hosts:
        for locale in locales:
            for split in splits:
                cell_attempts = by_cell.get((host, locale, split), {})
                classes_report = {}
                for class_name in CLASSES:
                    expected_ids = [p["id"] for p in corpus[locale][split] if p["class"] == class_name]
                    classes_report[class_name] = evaluate_class(expected_ids, cell_attempts, class_name)
                rows.append({"host": host, "locale": locale, "split": split, "classes": classes_report})
    return rows


def cell_gate_pass(row):
    """A cell passes the release gate only when every one of the three
    classes independently reports ``verdict == "pass"``. This is a per-row AND
    of three named numbers the caller can still see, not an aggregate score --
    it never replaces printing the three rates."""
    return all(row["classes"][class_name]["verdict"] == "pass" for class_name in CLASSES)


# ─────────────────────────────────── CLI ──────────────────────────────────────

def _split_csv(value):
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _print_problems(problems):
    for problem in problems:
        print(f"FAIL  {problem}")


def _format_cell(cell):
    if cell["status"] in ("not_run", "incomplete"):
        return f"{cell['status']}({cell['attempted']}/{cell['total']})"
    return f"{cell['correct']}/{cell['total']}:{cell['verdict']}"


def cmd_validate(args):
    corpus, problems = load_corpus(args.corpus_dir)
    if problems:
        _print_problems(problems)
        print(f"\nFAIL: corpus is not valid ({len(problems)} problem(s)).")
        return 1
    total = corpus_totals(corpus)
    print(f"PASS: corpus valid -- {total} prompts across {len(LOCALES)} locale(s) x {len(SPLITS)} split(s), "
          f"exactly {PROMPTS_PER_CLASS} per class per file, calibration/holdout disjoint by id and "
          f"normalized text in every locale, no duplicate id anywhere in the corpus.")
    return 0


def cmd_dry_run(args):
    corpus, problems = load_corpus(args.corpus_dir)
    if problems:
        _print_problems(problems)
        print("\nFAIL: corpus is not valid; refusing to build a plan from it.")
        return 2
    hosts = _split_csv(args.hosts) or list(HOSTS)
    locales = _split_csv(args.locales) or list(LOCALES)
    splits = _split_csv(args.splits) or list(SPLITS)
    bad_locales = [value for value in locales if value not in LOCALES]
    bad_splits = [value for value in splits if value not in SPLITS]
    if bad_locales or bad_splits:
        if bad_locales:
            print(f"ERROR: unknown locale(s) {bad_locales}, expected one of {LOCALES}", file=sys.stderr)
        if bad_splits:
            print(f"ERROR: unknown split(s) {bad_splits}, expected one of {SPLITS}", file=sys.stderr)
        return 2

    attempts = build_plan(corpus, hosts, locales, splits)

    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps({"generated": _now_iso(), "attempts": attempts}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    if args.json:
        print(json.dumps(attempts, ensure_ascii=False))
        return 0

    print("DRY RUN -- no host was called, no network request was made, nothing was sent anywhere.")
    print(f"corpus: {corpus_totals(corpus)} prompts across {len(LOCALES)} locale(s) x {len(SPLITS)} "
          f"split(s), validated clean.")
    per_locale_split = PROMPTS_PER_CLASS * len(CLASSES)
    print(f"plan: {len(attempts)} attempt(s) = {len(hosts)} host(s) x {len(locales)} locale(s) x "
          f"{len(splits)} split(s) x {per_locale_split} prompts/locale-split")
    breakdown = collections.Counter((attempt["host"], attempt["locale"], attempt["split"]) for attempt in attempts)
    for (host, locale, split), count in sorted(breakdown.items()):
        print(f"  {host:<14} {locale:<6} {split:<11} {count} prompt(s) to send")
    if args.out:
        print(f"wrote full plan to {args.out}")
    print("\nNext step for Wave C: run each planned prompt inside the real host, one host/locale/split "
          "at a time, then call `record` once per observed attempt.")
    return 0


def cmd_record(args):
    corpus, problems = load_corpus(args.corpus_dir)
    if problems:
        _print_problems(problems)
        print("\nFAIL: corpus is not valid; refusing to record against it.")
        return 2
    try:
        attempt = build_attempt(
            corpus, locale=args.locale, split=args.split, prompt_id=args.prompt_id,
            host=args.host, host_version=args.host_version, model=args.model,
            skill_population=args.skill_population, actual_route=args.actual_route,
            raw_outcome=args.raw_outcome,
        )
    except AttemptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    append_attempt(args.result_file, attempt)
    print(f"recorded 1 attempt -> {args.result_file} "
          f"({attempt['host']}/{attempt['locale']}/{attempt['split']}/{attempt['prompt_id']}: "
          f"expected={attempt['expected_route']} actual={attempt['actual_route']})")
    return 0


def cmd_score(args):
    # In --json mode, stdout carries the JSON payload and nothing else, so a
    # caller can pipe it straight into json.loads(); every diagnostic and the
    # human-readable trailer moves to stderr instead of interleaving with it.
    emit = (lambda *a, **k: print(*a, file=sys.stderr, **k)) if args.json else print

    corpus, problems = load_corpus(args.corpus_dir)
    if problems:
        for problem in problems:
            emit(f"FAIL  {problem}")
        emit("\nFAIL: corpus is not valid; refusing to score against it.")
        return 2
    attempts, read_problems = read_result_file(args.result_file)
    if read_problems:
        for problem in read_problems:
            emit(f"FAIL  {problem}")
        emit(f"NOTE  {len(read_problems)} line(s) in the result file could not be read; they are "
             "excluded from scoring, not treated as passing attempts.\n")

    hosts = _split_csv(args.hosts) or list(HOSTS)
    locales = _split_csv(args.locales) or list(LOCALES)
    splits = _split_csv(args.splits) or list(SPLITS)
    bad_locales = [value for value in locales if value not in LOCALES]
    bad_splits = [value for value in splits if value not in SPLITS]
    if bad_locales or bad_splits:
        if bad_locales:
            print(f"ERROR: unknown locale(s) {bad_locales}, expected one of {LOCALES}", file=sys.stderr)
        if bad_splits:
            print(f"ERROR: unknown split(s) {bad_splits}, expected one of {SPLITS}", file=sys.stderr)
        return 2

    rows = build_report(corpus, attempts, hosts, locales, splits)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        header = f"{'host':<14} {'locale':<6} {'split':<11} " + "  ".join(
            f"{name}" for name in CLASSES)
        print(header)
        for row in rows:
            cells = row["classes"]
            print(f"{row['host']:<14} {row['locale']:<6} {row['split']:<11} " + "  ".join(
                f"{class_name}={_format_cell(cells[class_name])}" for class_name in CLASSES))

    gate_rows = [row for row in rows if row["split"] == args.gate_split]
    gate_pass = bool(gate_rows) and all(cell_gate_pass(row) for row in gate_rows)

    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps({"generated": _now_iso(), "gate_split": args.gate_split,
                        "gate_pass": gate_pass, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        emit(f"wrote report to {args.out}")

    if gate_pass:
        emit(f"\nPASS: every requested {args.gate_split} cell is complete and meets all three thresholds.")
    else:
        emit(f"\nFAIL: not every requested {args.gate_split} cell is complete and passing -- see rows "
             "above. A not_run or incomplete cell never reads as a pass.")
    return 0 if gate_pass else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", default=str(DEFAULT_CORPUS_DIR),
                         help=f"default: {DEFAULT_CORPUS_DIR}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="validate the corpus only (counts, disjointness, schema); no network, writes nothing")
    validate.set_defaults(handler=cmd_validate)

    dry_run = subparsers.add_parser(
        "dry-run",
        help="validate the corpus and print/write the attempt plan; never sends anything to a host")
    dry_run.add_argument("--hosts", help=f"comma-separated, default: {','.join(HOSTS)}")
    dry_run.add_argument("--locales", help=f"comma-separated, default: {','.join(LOCALES)}")
    dry_run.add_argument("--splits", help=f"comma-separated, default: {','.join(SPLITS)}")
    dry_run.add_argument("--out", help="optional path to write the full plan as JSON")
    dry_run.add_argument("--json", action="store_true", help="print the plan as JSON instead of a summary")
    dry_run.set_defaults(handler=cmd_dry_run)

    record = subparsers.add_parser(
        "record",
        help="append one already-observed attempt outcome to a result file; the opt-in step, "
             "and the only subcommand that writes to a result file")
    record.add_argument("--result-file", required=True)
    record.add_argument("--host", required=True, help="free-form host id, e.g. claude_code, codex, antigravity")
    record.add_argument("--host-version", required=True)
    record.add_argument("--model", required=True)
    record.add_argument("--skill-population", default="",
                         help="comma-separated skill ids visible to the host at invocation time")
    record.add_argument("--locale", required=True, choices=LOCALES)
    record.add_argument("--split", required=True, choices=SPLITS)
    record.add_argument("--prompt-id", required=True)
    record.add_argument("--actual-route", required=True, choices=ROUTES)
    record.add_argument("--raw-outcome", required=True, help="what actually happened, in the operator's own words")
    record.set_defaults(handler=cmd_record)

    score = subparsers.add_parser(
        "score", help="compute per-cell review/pre_trade/adjacent_negative rates from a result file")
    score.add_argument("--result-file", required=True)
    score.add_argument("--hosts", help=f"comma-separated, default: {','.join(HOSTS)} plus any host actually observed")
    score.add_argument("--locales", help=f"comma-separated, default: {','.join(LOCALES)}")
    score.add_argument("--splits", help=f"comma-separated, default: {','.join(SPLITS)}")
    score.add_argument("--gate-split", default="holdout", choices=SPLITS,
                        help="which split's cells decide the exit code (default: holdout, per #458's "
                             "release-decision scope)")
    score.add_argument("--out", help="optional path to write the computed report as JSON")
    score.add_argument("--json", action="store_true", help="print rows as JSON instead of a summary table")
    score.set_defaults(handler=cmd_score)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
