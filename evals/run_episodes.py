#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay the question-episode bank's mechanical half (#417).

An **episode** is one dogfood or real-use miss, frozen as data:
``(state fixture, question, answers with expected verdicts)``. The bank lives in
``evals/episodes/``; this runner executes the deterministic, offline half of the
grading and nothing else. The rubric judge (#417's second half) is a separate,
opt-in, billable harness and never runs here — ``docs/eval-design.md`` keeps
non-deterministic runs out of the default suite.

Why a bank at all: before #417 every dogfood miss converted into an issue and
nothing else, so no miss was ever replayed against a later engine. An episode
is the replayable form. Because the fixture is a mock CSV rather than a frozen
artifact dump, each replay re-derives the engine's own facts through
``review.py prepare`` — the bank cannot drift out of sync with the engine the
way a committed artifact mirror would (development-guide section 1).

What the mechanical half proves, per answer under test:

``number_provenance``   every number on a user-facing surface traces to a number
                        the engine actually emitted (never-loosen rule 1: agent
                        prose derives nothing)
``grounding_fidelity``  a presented candidate rule quotes the engine's own
                        ``grounding`` verbatim, and a candidate that has none is
                        presented without one (#293)
``honesty_coverage``    every honesty key this episode puts in scope is both
                        still triggered by the fixture and disclosed by the
                        answer, in a digit-free sentence
``privacy_trace``       every ticker-shaped token traces to the synthetic
                        fixture or the engine artifacts, and no internal
                        position-id format appears (#274's text channel)
``surface_hygiene``     no snake_case engine identifier reached a user-facing
                        surface (#262: raw option values and metric keys)
``locale_purity``       an ``en`` surface carries no CJK; a non-``en`` surface
                        carries no untranslated English metric label (#262)

Three interlocks keep a green run meaningful (development-guide section 2 —
"a checker that stays green under its mutation is not evidence"):

1. Every episode declares at least one answer that MUST fail, and names the
   checks it must fail on. A checker that decays into a no-op turns those
   answers green and this runner red.
2. A declared check that finds nothing to look at is a failure, not a pass. An
   answer with no options cannot quietly satisfy ``grounding_fidelity``.
3. Bank coverage: every check must be observed both passing and failing at
   least once across the bank, or the run reports the gap.

Usage:
  python3 evals/run_episodes.py                 # replay the whole bank
  python3 evals/run_episodes.py EP-002 EP-003   # replay selected episodes
  python3 evals/run_episodes.py --list
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "fomo-kernel"
EPISODE_DIR = REPO / "evals" / "episodes"
COPY_DIR = SKILL_DIR / "copy"
TESTS_DIR = REPO / "tests"


def _load_module(name, path):
    """Import a repo script by path (persona_sweep's helper, same contract)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Borrowed rather than restated, so there is one definition of each rule:
#   check_card._INTERNAL_KEYS  the internal-field-name ban already enforced on
#                              the card face (A-12)
#   persona_sweep.CJK          the #356 CJK ranges
#   privacy_lint.POSITION_ID   the internal position-id format (#274)
#   card_renderer.numeric_claim the narrative digit ban, spelled-out forms
#                              included (#194)
_check_card = _load_module("episodes_check_card", TESTS_DIR / "agent" / "check_card.py")
_persona_sweep = _load_module("episodes_persona_sweep", TESTS_DIR / "persona_sweep.py")
_privacy_lint = _load_module("episodes_privacy_lint", SKILL_DIR / "tools" / "privacy_lint.py")
_card_renderer = _load_module("episodes_card_renderer", SKILL_DIR / "engine" / "card_renderer.py")

INTERNAL_KEYS = _check_card._INTERNAL_KEYS
CJK = _persona_sweep.CJK
POSITION_ID = _privacy_lint.POSITION_ID
numeric_claim = _card_renderer.numeric_claim

# A bare number, thousands separators included. Unsigned on purpose: a leading
# "-" in real copy is a hyphen or an ISO date separator far more often than a
# negative sign, and DATE below consumes date spans before this pattern runs.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Ticker-shaped: all-caps/digits with at least one letter, optional market or
# class suffix. Two characters minimum so English "I" and "A" never match.
UPPER_TOKEN = re.compile(r"\b(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,7}(?:[.-][A-Z0-9]{1,4})?\b")
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
# Plan values that carry no engine fact: absolute paths, content hashes, and
# session fingerprints would otherwise donate their digits to the provenance
# allow-set and quietly widen it.
NOISE_KEYS = {"state_root", "engine_meta", "path", "fingerprint", "session_id",
              "engine_version", "cycle_id", "id", "thesis_id", "event_id"}
HEX_TOKEN = re.compile(r"^[0-9a-f]{12,}$")

CHECK_NAMES = ("number_provenance", "grounding_fidelity", "honesty_coverage",
               "privacy_trace", "surface_hygiene", "locale_purity")
ANSWER_PART_KEYS = ("prose", "presented_options", "discloses")


# ─────────────────────────── episode loading ────────────────────────────────

def validate_episode(raw, rel):
    """Structural validation, fail-closed. A typo must not silently skip work.

    ``evals/episodes/episode.schema.json`` is the readable contract; this is the
    enforcement, because the offline suite carries no jsonschema dependency.
    """
    problems = []

    def require(condition, message):
        if not condition:
            problems.append(f"{rel}: {message}")

    require(isinstance(raw, dict), "episode must be a JSON object")
    if problems:
        return problems
    unknown = set(raw) - {"id", "title", "source", "moment", "fixture", "question",
                          "checks", "must_disclose", "answers"}
    require(not unknown, f"unknown top-level field(s): {sorted(unknown)}")
    for field in ("id", "title", "moment"):
        require(isinstance(raw.get(field), str) and raw.get(field), f"{field} must be a non-empty string")
    source = raw.get("source") or {}
    require(isinstance(source, dict), "source must be an object")
    require(isinstance(source.get("refs"), list) and source.get("refs"),
            "source.refs must list the issue(s) or run this episode was converted from")
    require(isinstance(source.get("date"), str) and source.get("date"),
            "source.date must record when the miss was observed")

    fixture = raw.get("fixture") or {}
    require(isinstance(fixture, dict), "fixture must be an object")
    trades = fixture.get("trades")
    require(isinstance(trades, str) and trades.startswith("skills/fomo-kernel/mock/"),
            "fixture.trades must be a synthetic CSV under skills/fomo-kernel/mock/")
    if isinstance(trades, str) and not (REPO / trades).is_file():
        problems.append(f"{rel}: fixture.trades does not exist: {trades}")
    require(fixture.get("route") in {"first_review", "weekly_review"},
            "fixture.route must be first_review or weekly_review")
    require(isinstance(fixture.get("locale"), str) and (COPY_DIR / f"{fixture.get('locale')}.json").is_file(),
            "fixture.locale must name a shipped copy catalog")

    question = raw.get("question") or {}
    require(isinstance(question, dict), "question must be an object")
    require(question.get("asked_by") in {"engine", "user"}, "question.asked_by must be engine or user")
    require(isinstance(question.get("kind"), str) and question.get("kind"),
            "question.kind must be an engine question kind, commitment_choice, or free_form")
    require(isinstance(question.get("text"), str) and question.get("text"),
            "question.text must record what the user was actually asked")

    checks = raw.get("checks")
    require(isinstance(checks, list) and checks, "checks must list at least one check")
    if isinstance(checks, list):
        bad = [name for name in checks if name not in CHECK_NAMES]
        require(not bad, f"unknown check(s): {bad}")
    require(isinstance(raw.get("must_disclose", []), list), "must_disclose must be a list")
    if raw.get("must_disclose") and "honesty_coverage" not in (checks or []):
        problems.append(f"{rel}: must_disclose is set but honesty_coverage is not in checks")
    if "honesty_coverage" in (checks or []) and not raw.get("must_disclose"):
        problems.append(f"{rel}: honesty_coverage is declared but must_disclose is empty — the "
                        "check would have no key to look for, and an abstention is not a pass")

    answers = raw.get("answers")
    require(isinstance(answers, list) and len(answers) >= 2,
            "answers must hold at least the recorded miss and one repaired answer")
    if not isinstance(answers, list):
        return problems
    seen_ids, failing = set(), 0
    for index, answer in enumerate(answers):
        tag = f"{rel}: answers[{index}]"
        if not isinstance(answer, dict):
            problems.append(f"{tag} must be an object")
            continue
        unknown = set(answer) - {"id", "expect", "fails", "note", *ANSWER_PART_KEYS}
        if unknown:
            problems.append(f"{tag} unknown field(s): {sorted(unknown)}")
        if not (isinstance(answer.get("id"), str) and answer.get("id")):
            problems.append(f"{tag} needs a non-empty id")
        elif answer["id"] in seen_ids:
            problems.append(f"{tag} duplicate answer id {answer['id']!r}")
        else:
            seen_ids.add(answer["id"])
        if answer.get("expect") not in {"pass", "fail"}:
            problems.append(f"{tag} expect must be pass or fail")
        if not any(answer.get(key) for key in ANSWER_PART_KEYS):
            problems.append(f"{tag} carries no answer surface ({', '.join(ANSWER_PART_KEYS)})")
        if answer.get("expect") == "fail":
            failing += 1
            fails = answer.get("fails")
            if not (isinstance(fails, list) and fails):
                problems.append(f"{tag} expects failure but does not name the check(s) it must fail")
            else:
                outside = [name for name in fails if name not in (checks or [])]
                if outside:
                    problems.append(f"{tag} fails names check(s) the episode does not declare: {outside}")
        elif answer.get("fails"):
            problems.append(f"{tag} expects a pass but names fails")
        for option in answer.get("presented_options") or []:
            if not isinstance(option, dict) or not option.get("maps_to"):
                problems.append(f"{tag} every presented option needs maps_to")
        if not isinstance(answer.get("discloses", {}), dict):
            problems.append(f"{tag} discloses must be an object of key -> sentence")
    if not failing:
        problems.append(f"{rel}: no answer expects failure — an episode whose every answer "
                        "passes cannot prove its checks still look at anything")
    return problems


def load_bank(selected=None):
    """Return ``(episodes, problems)`` for every ``EP-*.json`` in the bank."""
    episodes, problems = [], []
    paths = sorted(EPISODE_DIR.glob("EP-*.json"))
    if not paths:
        return [], [f"{EPISODE_DIR.relative_to(REPO)}: no episodes found"]
    for path in paths:
        rel = path.relative_to(REPO)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{rel}: invalid JSON: {exc}")
            continue
        found = validate_episode(raw, rel)
        if found:
            problems.extend(found)
            continue
        raw["_path"] = rel
        episodes.append(raw)
    # Duplicate ids are checked across every valid file, before any selection:
    # a collision one id-filter away from being visible is still a collision.
    ids = [episode["id"] for episode in episodes]
    for duplicate in sorted({name for name in ids if ids.count(name) > 1}):
        problems.append(f"duplicate episode id across files: {duplicate}")
    if selected:
        for name in sorted(selected - set(ids)):
            problems.append(f"no episode with id {name}")
        episodes = [episode for episode in episodes if episode["id"] in selected]
    return episodes, problems


# ────────────────────────────── engine facts ─────────────────────────────────

def prepare_fixture(episode, workdir):
    """Run the real engine offline over the episode's synthetic CSV.

    Same offline contract as ``tests/persona_sweep.py``: a PYTHONPATH-injected
    yfinance ImportError stub, so an open-position persona degrades
    deterministically instead of reaching the network, and an isolated
    ``TRADE_COACH_HOME`` so no episode can see production coach state.
    """
    fixture = episode["fixture"]
    root = workdir / "root"
    stubs = workdir / "stubs"
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / "yfinance.py").write_text('raise ImportError("offline stub")\n', encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(stubs), env.get("PYTHONPATH")) if part)
    env["TRADE_COACH_HOME"] = str(root)
    relative = pathlib.Path(fixture["trades"]).relative_to("skills/fomo-kernel")
    done = subprocess.run(
        [sys.executable, "engine/review.py", "prepare", str(relative),
         "--language", fixture["locale"], "--route", fixture["route"]],
        cwd=SKILL_DIR, capture_output=True, text=True, env=env)
    plans = list(root.glob(".pending/*/plan.json"))
    if done.returncode != 0 or len(plans) != 1:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or [""]
        return None, f"prepare failed (rc={done.returncode}, pending={len(plans)}): {tail[0]}"
    return json.loads(plans[0].read_text(encoding="utf-8")), None


def _leaves(node, key=None):
    """Yield ``(key, value)`` for every scalar leaf, dropping noise subtrees."""
    if isinstance(node, dict):
        for child_key, child in node.items():
            if child_key in NOISE_KEYS:
                continue
            yield from _leaves(child, child_key)
    elif isinstance(node, list):
        for child in node:
            yield from _leaves(child, key)
    else:
        yield key, node


def _is_opaque(text):
    return "/" in text or "\\" in text or bool(HEX_TOKEN.match(text))


def engine_facts(plan, episode):
    """Everything the checks are allowed to treat as engine-authored truth."""
    locale = episode["fixture"]["locale"]
    numbers, strings = set(), []
    for _key, value in _leaves(plan):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            numbers.add(float(value))
        elif isinstance(value, str) and not _is_opaque(value):
            strings.append(value)
            for match in NUMBER.finditer(value):
                numbers.add(float(match.group(0).replace(",", "")))

    copy_locale = json.loads((COPY_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    copy_en = json.loads((COPY_DIR / "en.json").read_text(encoding="utf-8"))
    fixture_text = (REPO / episode["fixture"]["trades"]).read_text(encoding="utf-8")

    # Traceable tokens: the synthetic ledger's own symbols, plus every all-caps
    # token the engine or its copy catalog already uses (USD, ETF, benchmark
    # names). Anything else on an answer surface came from outside the fixture,
    # which is exactly the #274 leak shape.
    tokens = set(UPPER_TOKEN.findall(fixture_text))
    for text in strings:
        tokens.update(UPPER_TOKEN.findall(text))
    for catalog in (copy_locale, copy_en):
        for _key, value in _leaves(catalog):
            if isinstance(value, str):
                tokens.update(UPPER_TOKEN.findall(value))

    # Engine identifiers that must never reach a user-facing surface: stable
    # snake_case codes (docs/language-policy.md) drawn from the plan itself, so
    # a new question kind or choice value is covered without editing this file.
    identifiers = {key for key in copy_en.get("dimensions", {})}
    identifiers.update(rule.get("id") for rule in
                       ((plan.get("card_plan") or {}).get("candidate_rules") or []))
    for question in plan.get("question_queue") or []:
        identifiers.add(question.get("id"))
        identifiers.add(question.get("kind"))
        for option in question.get("options") or []:
            identifiers.add(option.get("value"))
        contract = ((question.get("question_opportunity") or {}).get("answer_contract") or {})
        identifiers.update(contract.get("canonical_choices") or [])
    identifiers = {name for name in identifiers
                   if isinstance(name, str) and SNAKE_CASE.match(name)}

    # #262's mixed-language leak: an English metric label on a localized
    # surface. Only labels this locale actually translates count — zh-TW's own
    # "部位 sizing" keeps an English word on purpose.
    local_labels = {str(value) for value in copy_locale.get("dimensions", {}).values()}
    foreign_labels = {str(value) for value in copy_en.get("dimensions", {}).values()
                      if str(value) not in local_labels} if locale != "en" else set()

    return {
        "locale": locale,
        "engine_text": "\n".join(strings),
        "numbers": numbers,
        "dates": {match for text in strings for match in DATE.findall(text)},
        "tokens": tokens,
        "identifiers": identifiers,
        "foreign_labels": foreign_labels,
        "candidates": {rule.get("id"): rule for rule in
                       ((plan.get("card_plan") or {}).get("candidate_rules") or [])},
        "honesty_keys": set((plan.get("card_plan") or {}).get("required_honesty_keys") or []),
        "question_kinds": {question.get("kind") for question in (plan.get("question_queue") or [])},
    }


# ──────────────────────────────── the checks ─────────────────────────────────

def _surfaces(answer):
    """Every string the user would have read, tagged with where it sat."""
    out = [("prose", answer.get("prose") or "")]
    for index, option in enumerate(answer.get("presented_options") or []):
        out.append((f"option[{index}].label", option.get("label") or ""))
        out.append((f"option[{index}].description", option.get("description") or ""))
    for key, sentence in sorted((answer.get("discloses") or {}).items()):
        out.append((f"discloses[{key}]", sentence or ""))
    return [(role, text) for role, text in out if text]


def _number_matches(value, allowed):
    """True when ``value`` is an engine number, or that number rounded."""
    for candidate in allowed:
        if abs(candidate - value) <= 1e-9:
            return True
        for places in (0, 1, 2):
            if abs(round(candidate, places) - value) <= 1e-9:
                return True
    return False


def check_number_provenance(answer, facts):
    """Never-loosen rule 1, applied to an answer surface instead of a card.

    #414 will gate free-form answers in the product; this is the same question
    asked of a recorded answer: could the engine have produced this figure?
    """
    findings = []
    for role, text in _surfaces(answer):
        remainder = text
        for date in DATE.findall(text):
            if date not in facts["dates"]:
                findings.append(f"{role}: date {date} appears in no engine artifact")
            remainder = remainder.replace(date, " ")
        for match in NUMBER.finditer(remainder):
            raw = match.group(0)
            value = float(raw.replace(",", ""))
            if not _number_matches(value, facts["numbers"]):
                findings.append(f"{role}: {raw} traces to no engine number — "
                                "the answer derived or invented it")
    return findings


def check_grounding_fidelity(answer, facts):
    """#293: the presented candidate rule must be the engine's, word for word.

    Two halves, both shipped as one miss: a rule whose real ``grounding`` was
    paraphrased, and a rule with no ``grounding`` that received an invented one.
    Both reduce to the same mechanical question — is every word of this
    description engine-authored?
    """
    findings = []
    for index, option in enumerate(answer.get("presented_options") or []):
        role = f"option[{index}]"
        candidate = facts["candidates"].get(option.get("maps_to"))
        if candidate is None:
            findings.append(f"{role}: maps_to {option.get('maps_to')!r} is not a candidate "
                            "rule this fixture emits")
            continue
        description = (option.get("description") or "").strip()
        grounding = (candidate.get("grounding") or "").strip()
        if grounding and grounding not in description:
            findings.append(f"{role}: engine grounding is not quoted verbatim "
                            f"(engine: {grounding!r})")
        engine_text = sorted((str(candidate.get(field)) for field in ("grounding", "rule", "text")
                              if candidate.get(field)), key=len, reverse=True)
        residue = description
        for piece in engine_text:
            residue = residue.replace(piece, " ")
        residue = residue.strip(" \t\n·—–-:;,.。、，；：（）()「」〈〉\"'")
        if residue:
            findings.append(f"{role}: description carries wording no engine field authored: "
                            f"{residue!r}" + ("" if grounding else " — this candidate has no "
                                              "grounding, so it must be presented without one"))
    return findings


def check_honesty_coverage(episode, answer, facts):
    """Coverage, not adequacy: a triggered limitation must be said out loud.

    Scope is the episode's ``must_disclose``, verified to still be a subset of
    what the fixture triggers — a card carries the engine's own equality gate in
    ``_draft_bundle``, but an answer to one question legitimately touches only
    part of the ledger. Whether the sentence is *good* is judge work; that it
    exists, is digit-free, and is not copy-pasted across keys is mechanical.
    """
    findings = []
    scope = set(episode.get("must_disclose") or [])
    stale = sorted(scope - facts["honesty_keys"])
    if stale:
        findings.append(f"episode puts {stale} in scope but the fixture no longer triggers "
                        "them — the episode or the engine moved, and this can no longer "
                        "grade what it was written to grade")
    disclosed = answer.get("discloses") or {}
    for key in sorted(scope & facts["honesty_keys"]):
        sentence = (disclosed.get(key) or "").strip()
        if not sentence:
            findings.append(f"discloses[{key}]: triggered by the fixture, disclosed nowhere "
                            "in the answer")
        elif numeric_claim(sentence):
            findings.append(f"discloses[{key}]: makes a numeric claim; a disclosure carries "
                            "the limitation, the engine carries the magnitude")
    for key in sorted(set(disclosed) - facts["honesty_keys"]):
        findings.append(f"discloses[{key}]: this fixture does not trigger that limitation")
    seen = {}
    for key, sentence in sorted(disclosed.items()):
        sentence = (sentence or "").strip()
        if sentence and sentence in seen:
            findings.append(f"discloses[{key}]: byte-identical to discloses[{seen[sentence]}] — "
                            "one sentence cannot disclose two different limitations")
        seen[sentence] = key
    return findings


def check_privacy_trace(answer, facts):
    """#274's text channel, inverted into a positive test.

    The public repository can never hold real trade data, so an episode's every
    ticker-shaped token must trace to the synthetic fixture or the engine's own
    artifacts. A back-converted real-use miss that kept a real symbol fails
    here, which is what "keep only the failure structure" means mechanically.
    """
    findings = []
    for role, text in _surfaces(answer):
        for match in POSITION_ID.finditer(text):
            findings.append(f"{role}: internal position-id format on a user-facing surface")
        for match in UPPER_TOKEN.finditer(text):
            token = match.group(0)
            if token not in facts["tokens"]:
                findings.append(f"{role}: {token} traces to neither the synthetic fixture nor "
                                "the engine artifacts")
    return findings


def check_surface_hygiene(answer, facts):
    """#262, first half: internal identifiers are not user-facing copy.

    Stable snake_case codes are the engine's vocabulary
    (docs/language-policy.md); the user reads the copy catalog's words.
    """
    findings = []
    for role, text in _surfaces(answer):
        for identifier in sorted(facts["identifiers"]):
            if identifier in text:
                findings.append(f"{role}: engine identifier {identifier!r} reached a "
                                "user-facing surface")
        internal = INTERNAL_KEYS.search(text)
        if internal:
            findings.append(f"{role}: internal field name {internal.group(0)!r} reached a "
                            "user-facing surface")
    return findings


def check_locale_purity(answer, facts):
    """#262, second half, plus #356's direction.

    ``en`` surface: no CJK, the rule ``persona_sweep`` already holds cards to.
    Localized surface: no English metric label this locale translates. There is
    deliberately no blanket Latin ban — a zh card legitimately carries tickers,
    currency codes, and benchmark names.
    """
    findings = []
    for role, text in _surfaces(answer):
        if facts["locale"] == "en":
            if CJK.search(text):
                findings.append(f"{role}: CJK on an en surface")
            continue
        for label in sorted(facts["foreign_labels"]):
            if label.lower() in text.lower():
                findings.append(f"{role}: untranslated metric label {label!r} on a "
                                f"{facts['locale']} surface")
    return findings


SENTENCE = re.compile(r"[^.。!！?？\n]+")


def unmapped_claims(answer, facts):
    """What the mechanical half did not grade — reported, never inferred.

    #412's enum-gated-surface standard, turned on this harness: an enum for what
    is mechanically decidable, `unmapped` as a first-class honest state for what
    is not, and never a silent drop. A sentence carrying no number, quoting no
    engine span, and disclosing no honesty key has passed the hygiene checks and
    nothing else; its substance waits on the rubric judge, and on #414 for the
    product-side provenance gate. Never a failure — the over-trust would be
    printing nothing and letting a green run read as "the answer is good".
    """
    out = []
    for role, text in _surfaces(answer):
        if role.startswith("discloses["):
            continue
        for match in SENTENCE.finditer(text):
            sentence = match.group(0).strip()
            if len(sentence) < 12 or NUMBER.search(sentence):
                continue
            if sentence in facts["engine_text"]:
                continue
            out.append((role, sentence))
    return out


def run_check(name, episode, answer, facts):
    """Return ``(findings, looked_at_something)``.

    The second value is interlock 2: a check with nothing in front of it has not
    passed, it has abstained, and an abstention on a declared check is a
    failure. Otherwise an answer could satisfy ``grounding_fidelity`` by
    presenting no options at all.
    """
    if name == "number_provenance":
        surfaces = _surfaces(answer)
        return check_number_provenance(answer, facts), bool(surfaces)
    if name == "grounding_fidelity":
        options = answer.get("presented_options") or []
        return check_grounding_fidelity(answer, facts), bool(options)
    if name == "honesty_coverage":
        scope = set(episode.get("must_disclose") or [])
        return check_honesty_coverage(episode, answer, facts), bool(scope)
    if name == "privacy_trace":
        return check_privacy_trace(answer, facts), bool(_surfaces(answer))
    if name == "surface_hygiene":
        return check_surface_hygiene(answer, facts), bool(_surfaces(answer))
    if name == "locale_purity":
        return check_locale_purity(answer, facts), bool(_surfaces(answer))
    raise AssertionError(f"unknown check {name}")


# ──────────────────────────────── the runner ─────────────────────────────────

def replay(episode, facts):
    """Grade every answer in one episode. Returns ``(failures, observed)``.

    ``observed`` maps check -> set of outcomes seen ("pass"/"fail"), which the
    bank-level coverage report consumes.
    """
    failures, unmapped = [], []
    observed = {name: set() for name in episode["checks"]}
    tag = episode["id"]

    question = episode["question"]
    if question["asked_by"] == "engine":
        if question["kind"] == "commitment_choice":
            if not facts["candidates"]:
                failures.append(f"{tag}: the fixture emits no candidate rule, so the "
                                "commitment question this episode replays no longer exists")
        elif question["kind"] not in facts["question_kinds"]:
            failures.append(f"{tag}: the fixture no longer queues a {question['kind']!r} "
                            f"question (queued: {sorted(facts['question_kinds'])})")

    for answer in episode["answers"]:
        ungraded = unmapped_claims(answer, facts)
        if ungraded:
            role, sentence = ungraded[0]
            unmapped.append(f"unmapped: {tag}/{answer['id']} — {len(ungraded)} sentence(s) "
                            f"graded on hygiene only, substance waits on the judge "
                            f"(first, {role}: {sentence[:60]!r})")
        found = {}
        for name in episode["checks"]:
            findings, looked = run_check(name, episode, answer, facts)
            if not looked:
                failures.append(f"{tag}/{answer['id']}: declared check {name!r} had nothing "
                                "to inspect — a check that abstains is not a check that passed")
                continue
            observed[name].add("fail" if findings else "pass")
            if findings:
                found[name] = findings
        verdict = "fail" if found else "pass"
        if verdict != answer["expect"]:
            detail = ("; ".join(f"{name}: {message}" for name, messages in sorted(found.items())
                                for message in messages) or "no findings")
            failures.append(f"{tag}/{answer['id']}: expected {answer['expect']}, got "
                            f"{verdict} — {detail}")
            continue
        if verdict == "fail":
            expected = set(answer["fails"])
            actual = set(found)
            if actual != expected:
                detail = "; ".join(f"{name}: {message}" for name, messages in sorted(found.items())
                                   for message in messages)
                failures.append(f"{tag}/{answer['id']}: failed on {sorted(actual)} but the "
                                f"episode records {sorted(expected)} — {detail}")
    return failures, observed, unmapped


def coverage_report(observed_total, declared):
    """Interlock 3: a check nobody exercises both ways is not evidence.

    ``tests/persona_sweep.py`` learned this the expensive way — its first answer
    policy lit none of the surfaces it gated and still reported success.
    """
    failures, notes = [], []
    for name in CHECK_NAMES:
        outcomes = observed_total.get(name, set())
        if name not in declared:
            notes.append(f"coverage: {name} — no episode declares it yet")
            continue
        if "fail" not in outcomes:
            failures.append(f"coverage: no episode makes {name} fail — the bank cannot tell "
                            "whether it still looks at anything")
        if "pass" not in outcomes:
            failures.append(f"coverage: no episode makes {name} pass — it may be failing "
                            "everything for a reason unrelated to the miss it grades")
    return failures, notes


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episode", nargs="*", help="episode id(s) to replay; default all")
    parser.add_argument("--list", action="store_true", help="list the bank and exit")
    args = parser.parse_args()

    episodes, problems = load_bank(set(args.episode) or None)
    if args.list:
        for episode in episodes:
            print(f"{episode['id']}  {episode['title']}")
            print(f"          fixture={episode['fixture']['trades']} "
                  f"locale={episode['fixture']['locale']} "
                  f"refs={','.join(str(ref) for ref in episode['source']['refs'])}")
        for line in problems:
            print(f"FAIL  {line}")
        return 1 if problems else 0

    failures = list(problems)
    observed_total, declared, unmapped = {}, set(), []
    for episode in episodes:
        declared.update(episode["checks"])
        with tempfile.TemporaryDirectory() as tmp:
            plan, error = prepare_fixture(episode, pathlib.Path(tmp))
            if error:
                failures.append(f"{episode['id']}: {error}")
                continue
            facts = engine_facts(plan, episode)
        episode_failures, observed, episode_unmapped = replay(episode, facts)
        failures.extend(episode_failures)
        unmapped.extend(episode_unmapped)
        for name, outcomes in observed.items():
            observed_total.setdefault(name, set()).update(outcomes)
        if not episode_failures:
            print(f"PASS  {episode['id']}  {len(episode['answers'])} answer(s) graded on "
                  f"{', '.join(sorted(episode['checks']))}")

    if args.episode:
        # Coverage is a property of the whole bank: on a filtered run, a check
        # whose failing episode was not selected would report a false gap. Say
        # out loud that the interlock did not run rather than skipping quietly.
        notes = ["coverage: not evaluated — a filtered run cannot judge bank coverage; "
                 "run without arguments before trusting a green result"]
    else:
        coverage_failures, notes = coverage_report(observed_total, declared)
        failures.extend(coverage_failures)
    for line in unmapped + notes:
        print(f"NOTE  {line}")
    for line in failures:
        print(f"FAIL  {line}")
    verdict = f"FAIL: {len(failures)} failure(s)" if failures else "PASS: bank replayed clean"
    print(f"\nepisode bank: {len(episodes)} episode(s) — {verdict}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
