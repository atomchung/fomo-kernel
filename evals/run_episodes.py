#!/usr/bin/env python3
"""Replay the question-episode bank's mechanical validators.

    python3 evals/run_episodes.py                 # every episode
    python3 evals/run_episodes.py --id 357-...    # one
    python3 evals/run_episodes.py --list          # inventory
    python3 evals/run_episodes.py --coverage      # which validators each episode exercises

#417. Before this, every dogfood miss converted into an issue and nothing
else: two triage rounds closed fifteen of them, the dogfood root held
eighteen `ux_receipt` traces, and the miss-verdict file
`docs/eval-design.md` has specified since 2026-07-14 had **zero** records.
Issues describe a failure; they cannot re-run it. An episode can.

An episode is `(state fixture, user question, graded candidate answers)`.
The product's unit of value moved to answering a question at the moment of
the trade (`docs/decision-fomo-kernel-shape.md`), so the unit under
evaluation is **one question, answered** — not one review session, which is
what `tests/test_review_v2.py` and `tests/persona_sweep.py` already own.

**Two candidates per episode, not one.** Each carries the recorded miss and
at least one answer that must pass. A validator asserted only against the
miss is unfalsifiable in the direction that matters: a check that fails
everything would look perfect. The corrected answer is the control, and the
runner fails when either side lands wrong. `expect_findings` names which
validators must fire, because "it failed" is satisfied by an unrelated
regression — fake green type 1, docs/development-guide.md §2.

**What this half can and cannot decide.** Mechanical validators are cheap,
deterministic, and run first; they answer "is this answer *allowed*". They
do not answer "is this answer *good*" — whether it addressed the question,
whether the reasoning carries both sides, whether a user could overrule it
on evidence. That is the rubric-judge half (#417 stage 2), which is
non-deterministic and billable and stays out of default CI per
`docs/eval-design.md`. Episodes carry their `rubric` today and this runner
ignores it; a validator that cannot decide reports `unmapped` rather than
passing quietly (the #412 enum-gated-surface standard).

Deterministic, offline, stdlib only.
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EPISODES = ROOT / "evals" / "episodes"
SKILL = ROOT / "skills" / "fomo-kernel"
sys.path.insert(0, str(SKILL / "engine"))
sys.path.insert(0, str(SKILL / "tools"))
import card_renderer  # noqa: E402
import privacy_lint  # noqa: E402


# --------------------------------------------------------------------------
# Validators
# --------------------------------------------------------------------------
# Each returns a list of human-readable findings; empty means it passed.
# `None` means the validator cannot decide this episode and says so.

_NUMBER = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?")


def _numeric_forms(value):
    """Every rendering of one engine number an answer may legitimately use."""
    forms = set()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return forms
    forms.add(f"{value:g}")
    forms.add(f"{value:.2f}".rstrip("0").rstrip("."))
    for scaled in (value, value * 100):
        for digits in (0, 1, 2):
            rendered = f"{scaled:.{digits}f}"
            forms.add(rendered)
            forms.add(rendered.lstrip("-"))
            if abs(scaled) >= 1000:
                forms.add(f"{scaled:,.{digits}f}")
    return {form.rstrip("0").rstrip(".") if "." in form else form for form in forms} | forms


def _fixture_numbers(node, out=None):
    out = set() if out is None else out
    if isinstance(node, dict):
        for value in node.values():
            _fixture_numbers(value, out)
    elif isinstance(node, list):
        for value in node:
            _fixture_numbers(value, out)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out |= _numeric_forms(node)
    elif isinstance(node, str):
        # A figure already inside an engine-authored string is engine-owned.
        for match in _NUMBER.finditer(node):
            out.add(match.group().lstrip("+$").rstrip("%").replace(",", ""))
    return out


def grounded_numbers(episode, answer):
    """Every figure in the answer recomputes from the fixture's artifacts.

    The never-loosen rule this enforces: numbers come from engine artifacts
    only, agent prose derives nothing (docs/development-guide.md §5).

    The question's own figures count as given rather than derived — a
    "30-day check" the user was asked about is not a claim about their record
    — and that is the whole exemption. An earlier draft skipped every one- and
    two-digit number as "structural", which would have waved through the
    invented `61%` that episode `293-...` exists to catch: the widest class of
    ungrounded claim is a plausible-looking percentage.
    """
    known = _fixture_numbers(episode.get("fixture") or {})
    given = {match.group().lstrip("+-$").rstrip("%").replace(",", "")
             for match in _NUMBER.finditer(episode.get("question") or "")}
    # A leaked position id is four ungrounded figures wearing one defect.
    # `privacy_clean` owns it; reporting it here as well would bury the
    # finding that matters under its own side effects.
    scan = privacy_lint.POSITION_ID.sub(" ", answer)
    findings = []
    for match in _NUMBER.finditer(scan):
        raw = match.group()
        token = raw.lstrip("+-$").rstrip("%").replace(",", "")
        stripped = token.rstrip("0").rstrip(".") if "." in token else token
        if not token or {token, stripped} & (known | given):
            continue
        findings.append(f"{raw!r} appears in no engine artifact")
    return findings


# A quoted span is the user's own words replayed, not the agent's claim. The
# answer surface has this problem the card narrative does not: `add_thesis`
# and exit-capture stems are *required* to replay a recorded thesis verbatim,
# and a user who wrote "我願意等兩季" has written a quantity the agent may not
# edit out. Digits inside the quote are still checked — `grounded_numbers`
# reads the whole answer — so this exempts spelled-out forms only, and the
# quotation itself is held byte-exact by `required_mentions`.
_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|\"[^\"]*\"|“[^”]*”")


def spelled_out_numbers(episode, answer):
    """A quantity written as a word is the same claim without the digits.

    Reuses the engine's own digit-ban detector, so the answer surface and the
    card narrative are held to one definition of "a number".
    """
    reason = card_renderer.numeric_claim(_QUOTED.sub(" ", answer))
    return [reason] if reason else []


# Names that exist for the engine's benefit. #262's first complaint was an
# option that read `position sizing` — a `dims_raw` identifier printed at a
# user. The register is the engine's own vocabulary, so it grows with the
# engine rather than by opinion.
INTERNAL_NAMES = tuple(sorted(
    set(card_renderer.DIMENSION_ID_BY_LEGACY_LABEL.values())
    | {"dims_raw", "top_holes", "engine_card", "engine_state", "honesty_ledger",
       "candidate_rules", "thesis_id", "cycle_id", "decision_cursor", "problem_stats",
       "max_pos_pct", "avgdown_count", "acct_twr", "hold_twr", "alpha_ann",
       "question_opportunity", "required_honesty_keys"}))


def no_internal_field_names(episode, answer):
    lowered = answer.lower()
    return [f"internal identifier {name!r} on a user-facing surface"
            for name in INTERNAL_NAMES
            if re.search(r"(?<![a-z_])" + re.escape(name.replace("_", " ")) + r"(?![a-z_])", lowered)
            or re.search(r"(?<![a-z_])" + re.escape(name) + r"(?![a-z_])", lowered)]


_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
# Latin that belongs in a Traditional Chinese answer: instrument symbols, the
# units the card itself prints, and the Greek letters the engine uses.
_ZH_LATIN_ALLOWED = re.compile(
    r"^(?:[A-Z0-9.\-]{1,8}|pp|bps|SPY|QQQ|SOXX|ETF|AI|IRR|TWR|FIFO|OK)$")


def language_purity(episode, answer):
    """#262's second complaint: an answer that mixed the two locales.

    English answers carry no CJK (the #356 rule the persona sweep applies to
    cards). Traditional Chinese answers may carry symbols and units but not
    English sentences.
    """
    language = episode.get("language", "en")
    if language == "en":
        found = sorted(set(_CJK.findall(answer)))
        return [f"CJK on an English answer: {''.join(found)}"] if found else []
    intruders = [word for word in re.findall(r"[A-Za-z][A-Za-z'\-]*", answer)
                 if not _ZH_LATIN_ALLOWED.match(word)]
    return ([f"English words on a {language} answer: {', '.join(sorted(set(intruders)))}"]
            if intruders else [])


def privacy_clean(episode, answer):
    """The internal position-id format never belongs in text a user reads.

    Scoped deliberately: `tools/privacy_lint.py`'s ticker and amount channels
    need the real CSV as a reference set, which by construction cannot exist
    in this repository. What survives without one is the format check, which
    is the channel that leaks the ledger's shape rather than its contents.
    """
    return [f"position identifier {hit!r} in the answer"
            for hit in privacy_lint.POSITION_ID.findall(answer)]


def required_mentions(episode, answer):
    findings = []
    for entry in episode.get("required_mentions") or []:
        if not any(phrase in answer for phrase in entry["any_of"]):
            findings.append(f"missing {entry['label']}")
    return findings


def forbidden_mentions(episode, answer):
    findings = []
    for entry in episode.get("forbidden_mentions") or []:
        for phrase in entry["none_of"]:
            if phrase in answer:
                findings.append(f"{entry['label']}: {phrase!r}")
    return findings


def provenance_labels(episode, answer):
    """Pending #414. Reported as unmapped, never as a pass.

    A free-form answer is supposed to mark each claim as *your record says*,
    *public fact*, or *my judgment* (decision-fomo-kernel-shape.md §3). No
    mechanical gate for that exists yet, and #402 §4 makes it a prerequisite
    for lifting the recommendation ban. Returning `None` keeps the gap on the
    report instead of letting an unchecked surface read as a clean one.
    """
    return None


VALIDATORS = {
    "grounded_numbers": grounded_numbers,
    "spelled_out_numbers": spelled_out_numbers,
    "no_internal_field_names": no_internal_field_names,
    "language_purity": language_purity,
    "privacy_clean": privacy_clean,
    "required_mentions": required_mentions,
    "forbidden_mentions": forbidden_mentions,
    "provenance_labels": provenance_labels,
}


# --------------------------------------------------------------------------
# Episode loading
# --------------------------------------------------------------------------
class EpisodeError(Exception):
    pass


def _require(condition, message):
    if not condition:
        raise EpisodeError(message)


def load_episode(path):
    """Structural validation against `episode.schema.json`'s vocabulary.

    Hand-rolled: the offline suite ships no jsonschema validator, and the
    same pinning approach `tests/test_review_v2.py` uses for the plan and
    bundle schemas applies here.
    """
    episode = json.loads(path.read_text(encoding="utf-8"))
    name = path.name
    _require(episode.get("schema_version") == 1, f"{name}: schema_version must be 1")
    _require(episode.get("id") == path.stem, f"{name}: id must equal the filename stem")
    for field in ("title", "question"):
        _require(isinstance(episode.get(field), str) and episode[field].strip(),
                 f"{name}: {field} is required")
    source = episode.get("source") or {}
    _require(source.get("kind") in
             {"dogfood_miss", "real_use_miss", "back_converted_issue", "adversarial"},
             f"{name}: source.kind is not one of the declared kinds")
    _require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(source.get("date", ""))),
             f"{name}: source.date must be ISO")
    _require(isinstance(episode.get("fixture"), dict), f"{name}: fixture must be an object")
    candidates = episode.get("candidates") or []
    _require(len(candidates) >= 2, f"{name}: needs a recorded miss and a control")
    expectations = {candidate.get("expect") for candidate in candidates}
    _require({"pass", "fail"} <= expectations,
             f"{name}: needs at least one passing and one failing candidate")
    for candidate in candidates:
        label = candidate.get("label", "?")
        _require(isinstance(candidate.get("answer"), str) and candidate["answer"].strip(),
                 f"{name}/{label}: answer is required")
        if candidate["expect"] == "fail":
            _require(candidate.get("expect_findings"),
                     f"{name}/{label}: a failing candidate must name the validators that fire")
            unknown = set(candidate["expect_findings"]) - set(VALIDATORS)
            _require(not unknown, f"{name}/{label}: unknown validator(s) {sorted(unknown)}")
    return episode


def load_all():
    return [load_episode(path) for path in sorted(EPISODES.glob("*.json"))
            if path.name != "episode.schema.json"]


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------
def run_candidate(episode, candidate):
    """Return (fired, unmapped, detail) for one candidate answer."""
    fired, unmapped, detail = [], [], {}
    for name, validator in VALIDATORS.items():
        findings = validator(episode, candidate["answer"])
        if findings is None:
            unmapped.append(name)
        elif findings:
            fired.append(name)
            detail[name] = findings
    return fired, unmapped, detail


def replay(episode):
    """Return a list of failure strings; empty means the episode held."""
    problems = []
    for candidate in episode["candidates"]:
        fired, _unmapped, detail = run_candidate(episode, candidate)
        label = f"{episode['id']}/{candidate['label']}"
        if candidate["expect"] == "pass":
            if fired:
                problems.append(f"{label}: expected clean, got {sorted(fired)}")
                for name in sorted(fired):
                    problems.extend(f"    {name}: {line}" for line in detail[name])
        else:
            expected = set(candidate["expect_findings"])
            if set(fired) != expected:
                problems.append(
                    f"{label}: expected {sorted(expected)}, got {sorted(fired)}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--id", help="replay one episode by id")
    ap.add_argument("--list", action="store_true", help="print the inventory and exit")
    ap.add_argument("--coverage", action="store_true",
                    help="print which validators each episode exercises")
    args = ap.parse_args(argv)

    try:
        episodes = load_all()
    except (EpisodeError, json.JSONDecodeError) as error:
        print(f"FAIL invalid episode: {error}", file=sys.stderr)
        return 1
    if args.id:
        episodes = [e for e in episodes if e["id"] == args.id]
        if not episodes:
            print(f"FAIL no episode with id {args.id!r}", file=sys.stderr)
            return 1
    if not episodes:
        print("FAIL the episode bank is empty", file=sys.stderr)
        return 1

    if args.list:
        for episode in episodes:
            source = episode["source"]
            origin = f"#{source['issue']}" if source.get("issue") else source["kind"]
            print(f"{episode['id']:<44} {origin:<8} {episode['title']}")
        return 0

    if args.coverage:
        exercised = set()
        for episode in episodes:
            for candidate in episode["candidates"]:
                exercised |= set(candidate.get("expect_findings") or [])
        unmapped = [name for name, fn in VALIDATORS.items() if fn(episodes[0], "x") is None]
        for name in VALIDATORS:
            state = ("unmapped" if name in unmapped else
                     "exercised" if name in exercised else "never fires")
            print(f"  {name:<26} {state}")
        return 0

    problems = []
    for episode in episodes:
        problems.extend(replay(episode))

    if not args.id:
        # A validator no episode makes fire is an unproven checker, and this
        # repository does not accept those: "a checker that stays green under
        # its matching mutation is not evidence" (docs/eval-design.md). The
        # fix is an episode, not a suppression — which is the difference
        # between a data-driven bank and a checker that accretes rules.
        exercised = {name for episode in episodes for candidate in episode["candidates"]
                     for name in candidate.get("expect_findings") or []}
        unproven = [name for name, validator in VALIDATORS.items()
                    if name not in exercised and validator(episodes[0], "x") is not None]
        problems.extend(f"validator {name!r} fires on no episode — add one that it catches"
                        for name in unproven)

    if problems:
        print("FAIL episode bank", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    candidates = sum(len(e["candidates"]) for e in episodes)
    unmapped = sorted(name for name, fn in VALIDATORS.items() if fn(episodes[0], "x") is None)
    print(f"PASS episode bank: {len(episodes)} episodes, {candidates} graded answers")
    if unmapped:
        print(f"     unmapped validators (no mechanical gate yet): {', '.join(unmapped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
