#!/usr/bin/env python3
"""Persona sweep: render every mock persona's card and gate the output.

CLAUDE.md's merge discipline requires: "If the engine changed, run the persona
sweep." This is that command — the canonical form of what sessions previously
re-invented ad hoc (#368 records the round that standardized it).

    python3 tests/persona_sweep.py
    python3 tests/persona_sweep.py --baseline <other-checkout>/skills/fomo-kernel/engine

For every ``mock/sample_*.csv`` persona x locale (zh-TW, en):

1. ``review.py prepare`` into an isolated throwaway ``TRADE_COACH_HOME``,
   with a PYTHONPATH-injected yfinance ImportError stub (the repo's
   ``_offline_engine_env`` pattern) so open-position personas degrade
   deterministically instead of fetching live prices.
2. Freeze one session bundle per **decision variant** (see below) by calling
   the engine's own ``review._draft_bundle`` — the same function ``preview``
   and ``finalize`` call — with synthetic answers derived from the plan's
   own question options and a fixed digit-free narrative.
3. Render each frozen bundle on all three surfaces: ``render_private``,
   ``render_html``, ``render_public``.
4. Gate the output: ``tests/agent/check_card.py`` (S-1..S-4) must pass on the
   private Markdown card, the HTML card must hold the layout invariants that
   no fixture-based test can cover across real engine output — the KPI grid's
   ``data-n`` equals the number of cells that actually lit, and exactly one
   ``.sec.keystep`` emphasis ground exists (R3/R4, docs/design-guidelines.md)
   — and an English card must carry no CJK on any surface (#356).
5. With ``--baseline``: every Markdown card must be byte-identical to the
   baseline engine's render of the same bundle. HTML may differ by design;
   silent Markdown drift is the red flag, because that surface is the only
   carrier of every figure on clients without widget rendering.

Exit code 0 only when every persona passes every gate on every variant.

**Decision variants.** A review's card depends on what the user decided, not
only on the trade file, so one render per persona could never be a complete
golden master. Each persona x locale is frozen three ways:

``preview``    the pre-commitment card shown before the user picks a rule
               (``require_commitment=False``, Block 4's standing-rule branch)
``committed``  a rule chosen from ``card_plan.candidate_rules`` — Block 4's
               committed-rule paragraph, its targets line, and its trade-off
               line. Skipped, and reported, for personas the engine gives no
               candidate rule.
``skip``       the user declined to set a rule (Block 4's ``rule_skip``).

**Why ``_draft_bundle`` and not a hand-built dict.** This sweep used to
assemble its own bundle mirroring ``review.py``'s shape. That mirror silently
went stale: it pinned ``thesis_updates``/``thesis_decisions``/
``exit_narratives`` to empty and never carried ``revisit_resolutions``,
``rule_breach_decisions``, ``headline_motive_events``,
``exit_consistency_events`` or ``initial_thesis_events`` at all — so every
answer-derived card surface was dark to the oracle while appearing covered.
A hand-maintained mirror of a product surface is exactly the failure #368
exists to remove, so the sweep now calls the engine's own bundle builder.
``_draft_bundle`` takes ``(plan, answers, narrative)`` and performs no root
I/O, which is what makes this substitution possible.

Scope: every card here is a **first-review** render — each persona prepares
into a fresh empty root, so ``route=auto`` resolves to ``first_review``.
Surfaces that need review history (the prior-commitment mirror, rule-breach
blocks) or elapsed calendar time (due-revisit checkpoints mature 30/60/90 days
out) remain covered by the fixture suites (``tests/test_card_html.py``,
``tests/test_review_v2.py``), as do the ``snapshot_review`` and ``test_drive``
routes, which no mock persona exercises.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

TESTS_DIR = pathlib.Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
SKILL_DIR = REPO / "skills" / "fomo-kernel"
ENGINE_DIR = SKILL_DIR / "engine"
LOCALES = ("zh-TW", "en")

NARRATIVE = {
    "zh-TW": {
        "headline": "這期最值得記住的是紀律的變化。",
        "mirror": "你上期在意的事，這期有了對照。",
        "honesty": "這項資料有已標註的侷限，判讀時保守以對。",
        "thesis_why": "這個部位的理由仍待下次復盤驗證。",
        "thesis_exit": "當初的理由不再成立就出場。",
    },
    "en": {
        "headline": "The discipline shift is what this period should be remembered for.",
        "mirror": "What you cared about last period now has its mirror.",
        "honesty": "This figure carries a known limitation; read it conservatively.",
        "thesis_why": "The reason for holding this position still needs the next review to test it.",
        "thesis_exit": "Exit when the original reason no longer holds.",
    },
}

# Answering is read off each question's own ``options``, so a new question kind
# is answered without teaching this file about it. Two rules shape the pick.
#
# ``skip`` is answerable everywhere and is therefore the tempting default — and
# it is exactly the wrong one here. Skipping a question suppresses the card
# surface that answering it would light: the first draft of this file preferred
# ``skip`` and silently produced zero ``headline_motive_events`` and zero
# ``revisit_resolutions`` while reporting that all gates passed. Skip is now the
# last resort, and the sweep asserts below that the surfaces it is supposed to
# light actually lit.
#
# A few choices impose an authoring duty the sweep has no opinion about, so they
# are avoided rather than satisfied with invented content.
COSTLY_CHOICES = {
    "planned_entry",  # demands a captured (non-inferred) thesis for that cycle
    "new_evidence",   # demands an evidence_delta payload
    "swap",           # the swap panel is a comparison against a replacement
}
# Explicit order, not "first non-costly option": the rendered cards are a golden
# master, so the answer must not drift when the engine reorders an option list.
PREFERRED_CHOICES = ("no_clear_thesis", "deliberate_plan", "thesis_broken",
                     "keep_tracking", "other", "external_call", "momentum_follow")
# Bundle keys that exist only because a question was answered. The sweep is an
# oracle for these surfaces, so producing none of them means the oracle quietly
# stopped covering them — a failure, not a pass.
ANSWER_DERIVED_KEYS = ("commitment", "thesis_updates", "exit_narratives",
                       "initial_thesis_events", "headline_motive_events")
# Answer-derived surfaces a first-review sweep structurally cannot reach, with
# the reason. Printed every run: an accounting of what the oracle does not cover
# has to be visible, not inferred from the absence of an assertion. If one of
# these ever lights, the run says so — that is the signal to promote it into
# ANSWER_DERIVED_KEYS rather than leave new coverage unguarded.
KNOWN_DARK = {
    "revisit_resolutions": "needs a due_revisit question; checkpoints mature 30/60/90 days "
                           "after a prior review's finalize (revisit.CHECKPOINTS)",
    "thesis_decisions": "needs an add_thesis question, which no mock persona triggers (#231)",
    "rule_breach_decisions": "needs a breach logged against a prior review's committed rule",
    "exit_consistency_events": "needs an exit_consistency question",
}

VARIANTS = ("preview", "committed", "skip")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _locale_of(plan):
    return "en" if str(plan.get("language", "")).lower() == "en" else "zh-TW"


def narrative_for(plan):
    """A fixed digit-free narrative covering exactly the plan's required keys.

    The honesty key set is an equality contract in ``_draft_bundle``: missing a
    required key and claiming an unrequired one both fail closed."""
    text = NARRATIVE[_locale_of(plan)]
    required = sorted(set((plan.get("card_plan") or {}).get("required_honesty_keys") or []))
    narrative = {"headline": text["headline"], "mirror": text["mirror"]}
    if required:
        narrative["honesty"] = {key: text["honesty"] for key in required}
    return narrative


def _choice_for(question):
    values = [option.get("value") for option in question.get("options") or []
              if isinstance(option, dict) and option.get("value")]
    for preferred in PREFERRED_CHOICES:
        if preferred in values:
            return preferred
    usable = [value for value in values if value not in COSTLY_CHOICES and value != "skip"]
    if usable:
        return usable[0]
    return "skip" if "skip" in values else (values[0] if values else None)


def answers_for(plan, commitment=None):
    """Synthetic answers that satisfy the engine's own answer contract.

    Returns ``(answers, problems)``; a question whose options the sweep cannot
    answer is reported rather than silently skipped, because an unanswerable
    question means the oracle stopped covering that question's card surface."""
    problems = []
    rows = []
    for question in plan.get("question_queue") or []:
        choice = _choice_for(question)
        if choice is None:
            problems.append(f"question {question.get('id')} ({question.get('kind')}) "
                            "offers no answerable option")
            continue
        rows.append({"question_id": question["id"], "choice": choice})
    text = NARRATIVE[_locale_of(plan)]
    # The engine prefills ticker and maturity for a missing cycle; the agent
    # owes cycle_id plus the two judgment fields (authoring_contract).
    updates = [{"cycle_id": row["cycle_id"], "why": text["thesis_why"],
                "exit_trigger": text["thesis_exit"]}
               for row in plan.get("missing_thesis_positions") or []
               if row.get("cycle_id")]
    answers = {"session_id": plan["session_id"], "answers": rows,
               "thesis_updates": updates, "observations": []}
    if commitment is not None:
        answers["commitment"] = commitment
    return answers, problems


def variant_answers(plan, variant):
    """``(answers, problems, skip_reason)`` for one decision variant."""
    if variant == "preview":
        return (*answers_for(plan), None)
    if variant == "skip":
        return (*answers_for(plan, commitment={"choice": "skip"}), None)
    candidates = (plan.get("card_plan") or {}).get("candidate_rules") or []
    if not candidates:
        return None, [], "engine offered no candidate rule"
    return (*answers_for(plan, commitment={"choice": candidates[0]["id"]}), None)


def build_fixtures(out):
    """prepare every persona x locale, then freeze one bundle per variant."""
    bundles = out / "bundles"
    bundles.mkdir(parents=True, exist_ok=True)
    stub_dir = out / "stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)
    # The repo's _offline_engine_env pattern (tests/test_review_v2.py): the
    # real engine subprocess imports this stub instead of yfinance, so the
    # sweep stays offline and open-position personas degrade deterministically.
    (stub_dir / "yfinance.py").write_text('raise ImportError("offline stub")\n', encoding="utf-8")
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(stub_dir), base_env.get("PYTHONPATH")) if part)
    sys.path.insert(0, str(ENGINE_DIR))
    review = _load_module("sweep_review", ENGINE_DIR / "review.py")
    failures, notes = [], []
    for csv in sorted(SKILL_DIR.glob("mock/sample_*.csv")):
        persona = csv.stem.replace("sample_", "")
        for locale in LOCALES:
            root = out / "roots" / f"{persona}-{locale}"
            proc = subprocess.run(
                [sys.executable, "engine/review.py", "prepare",
                 str(csv.relative_to(SKILL_DIR)), "--language", locale],
                cwd=SKILL_DIR, capture_output=True, text=True,
                env={**base_env, "TRADE_COACH_HOME": str(root)},
            )
            plan_files = list(root.glob(".pending/*/plan.json"))
            if proc.returncode != 0 or not plan_files:
                failures.append(f"prepare {persona}-{locale}: rc={proc.returncode} "
                                f"{proc.stderr.strip().splitlines()[-1:] or ''}")
                continue
            plan = json.loads(plan_files[0].read_text())
            for variant in VARIANTS:
                name = f"{persona}-{locale}.{variant}"
                answers, problems, skip_reason = variant_answers(plan, variant)
                failures.extend(f"answers {name}: {p}" for p in problems)
                if skip_reason:
                    # Never a silent cap: a variant the engine cannot produce is
                    # reported, so "all gates pass" never overstates coverage.
                    notes.append(f"{name}: not rendered — {skip_reason}")
                    continue
                try:
                    bundle = review._draft_bundle(
                        plan, answers, narrative_for(plan),
                        require_commitment=(variant != "preview"))
                except Exception as exc:
                    failures.append(f"draft {name}: {type(exc).__name__}: {exc}")
                    continue
                (bundles / f"{name}.bundle.json").write_text(
                    json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return bundles, failures, notes


def coverage_report(bundles_dir):
    """``(failures, notes)`` on what the frozen bundles actually light.

    Without this the sweep degrades silently: answers that stop producing
    events, or a persona set that stops triggering a question kind, would read
    as "all gates pass" while the oracle quietly shrank. This checks the sweep,
    not the engine — it is the reason the answer-choice policy above can be
    trusted. The first draft of that policy answered every question with
    ``skip`` and lit none of these; the run still reported success."""
    lit = {key: 0 for key in tuple(ANSWER_DERIVED_KEYS) + tuple(KNOWN_DARK)}
    for bundle_file in sorted(pathlib.Path(bundles_dir).glob("*.bundle.json")):
        bundle = json.loads(bundle_file.read_text())
        for key in lit:
            if bundle.get(key):
                lit[key] += 1
    failures = [f"coverage: no bundle carries {key!r} — the sweep no longer covers "
                f"the card surface it gates" for key in ANSWER_DERIVED_KEYS if not lit[key]]
    notes = []
    for key, reason in sorted(KNOWN_DARK.items()):
        if lit[key]:
            notes.append(f"coverage: {key!r} now lights on {lit[key]} bundle(s) — promote it "
                         "into ANSWER_DERIVED_KEYS so the new coverage is guarded")
        else:
            notes.append(f"not covered: {key} — {reason}")
    notes.append("covered: " + ", ".join(f"{key}x{lit[key]}" for key in ANSWER_DERIVED_KEYS))
    return failures, notes


def html_invariants(html_card):
    """Layout invariants from docs/design-guidelines.md that only hold against
    real engine output: R3 (data-n equals lit cells) and R4 (one keystep)."""
    problems = []
    kpi = re.search(r'class="kpi" data-n="(\d+)"', html_card)
    if kpi:
        cells = html_card.count('<div class="m">') + html_card.count('<div class="m curve">')
        if int(kpi.group(1)) != cells:
            problems.append(f"data-n={kpi.group(1)} but {cells} cells rendered (R3)")
    if html_card.count("sec keystep") != 1:
        problems.append(f"expected exactly one .sec.keystep, got {html_card.count('sec keystep')} (R4)")
    return problems


# CJK punctuation, ext A, the main ideograph block, compatibility
# ideographs, and fullwidth forms. Escapes, not literals: a leak can be a
# lone corner bracket or fullwidth semicolon from a zh sentence template as
# easily as a whole clause, and the range bounds must stay readable.
CJK = re.compile("[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")


def locale_purity(card, locale):
    """#356: an English card carries no CJK, anywhere.

    Locale bugs in this engine are interpolation bugs, not translation bugs —
    the wrapper sentence comes from ``copy/en.json`` and reads as English while
    the value dropped into it is a zh literal the engine hardcoded. A
    per-sentence assertion cannot see that class; scanning the whole rendered
    card can, and it holds for every persona and every block at once. The zh
    direction has no counterpart: a zh card legitimately carries English
    tickers, currency codes, and benchmark names."""
    if locale != "en":
        return []
    return [f"CJK on the English card: {line.strip()}"
            for line in card.splitlines() if CJK.search(line)]


def render_all(engine_dir, bundles_dir, render_dir, gate):
    """Render every frozen bundle with one engine version; optionally gate."""
    card_renderer = _load_module("sweep_card_renderer", pathlib.Path(engine_dir) / "card_renderer.py")
    check_card = _load_module("sweep_check_card", TESTS_DIR / "agent" / "check_card.py") if gate else None
    render_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for bundle_file in sorted(pathlib.Path(bundles_dir).glob("*.bundle.json")):
        name = bundle_file.name.replace(".bundle.json", "")
        bundle = json.loads(bundle_file.read_text())
        try:
            private = card_renderer.render_private(bundle)
            public = card_renderer.render_public(bundle)
            html_card = card_renderer.render_html(bundle)
        except Exception as exc:  # a crash on any persona is exactly what the sweep exists to catch
            failures.append(f"render {name}: {type(exc).__name__}: {exc}")
            continue
        (render_dir / f"{name}.private.md").write_text(private)
        (render_dir / f"{name}.public.md").write_text(public)
        (render_dir / f"{name}.html").write_text(html_card)
        if not gate:
            continue
        # The plan travels inside the bundle and is a valid S-2 context:
        # check_card's _context_card reads its engine_card key, so module
        # lighting is actually checked rather than degraded to a skip.
        plan = bundle.get("review_plan") or {}
        broken = [f.assertion for f in check_card.check_card(private, plan) if not f.passed]
        if broken:
            failures.append(f"check_card {name}: {', '.join(broken)}")
        failures.extend(f"html {name}: {p}" for p in html_invariants(html_card))
        locale = _locale_of(bundle)
        for surface, card in (("private", private), ("public", public), ("html", html_card)):
            failures.extend(f"{surface} {name}: {p}" for p in locale_purity(card, locale))
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default=str(ENGINE_DIR),
                        help="engine directory whose card_renderer renders the cards")
    parser.add_argument("--baseline", default=None,
                        help="second engine directory; Markdown must be byte-identical to it")
    parser.add_argument("--out", default=None, help="working directory (default: temp dir)")
    parser.add_argument("--bundles", default=None,
                        help="reuse an existing bundles dir instead of running prepare")
    parser.add_argument("--render-only", action="store_true",
                        help="internal: render --bundles with --engine, no prepare, no gates")
    args = parser.parse_args()

    out = pathlib.Path(args.out or tempfile.mkdtemp(prefix="persona-sweep-"))
    failures, notes = [], []
    if args.bundles:
        bundles = pathlib.Path(args.bundles)
    else:
        bundles, failures, notes = build_fixtures(out)

    if args.render_only:
        failures += render_all(args.engine, bundles, out / "render", gate=False)
        for line in failures:
            print(f"FAIL  {line}")
        return 1 if failures else 0

    failures += render_all(args.engine, bundles, out / "render", gate=True)
    # Runs wherever the gates run, including a --bundles rerun: coverage is a
    # gate on the sweep itself, so it must not be tied to having just built the
    # fixtures.
    gaps, coverage_notes = coverage_report(bundles)
    failures += gaps
    notes += coverage_notes

    if args.baseline:
        proc = subprocess.run(
            [sys.executable, __file__, "--render-only", "--engine", args.baseline,
             "--bundles", str(bundles), "--out", str(out / "baseline")],
            capture_output=True, text=True)
        if proc.returncode != 0:
            failures.append(f"baseline render failed:\n{proc.stdout}{proc.stderr}")
        else:
            for md in sorted((out / "render").glob("*.md")):
                other = out / "baseline" / "render" / md.name
                if not other.exists():
                    failures.append(f"baseline missing {md.name}")
                elif md.read_bytes() != other.read_bytes():
                    failures.append(f"markdown drift vs baseline: {md.name}")

    count = len(list(bundles.glob("*.bundle.json")))
    for line in notes:
        print(f"NOTE  {line}")
    for line in failures:
        print(f"FAIL  {line}")
    verdict = f"FAIL: {len(failures)} failure(s)" if failures else "PASS: all gates pass"
    print(f"\npersona sweep: {count} bundles x 3 surfaces rendered to {out} — {verdict}")
    # Own temp dir, clean run: remove it, since run_all.py calls this on every
    # commit. A failing run keeps everything — the rendered cards are the
    # evidence you need to read next.
    if not args.out and not failures:
        shutil.rmtree(out, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
