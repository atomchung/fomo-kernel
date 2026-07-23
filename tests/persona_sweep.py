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
2. Render the pre-commitment card (``render_private`` + ``render_html``) from
   the frozen plan, with a fixed digit-free synthetic narrative: headline,
   mirror, and exactly the plan's ``required_honesty_keys``.
3. Gate the output: ``tests/agent/check_card.py`` (S-1..S-4) must pass on the
   Markdown card, the HTML card must hold the layout invariants that no
   fixture-based test can cover across real engine output — the KPI grid's
   ``data-n`` equals the number of cells that actually lit, and exactly one
   ``.sec.keystep`` emphasis ground exists (R3/R4, docs/design-guidelines.md)
   — and an English card must carry no CJK on either surface (#356).
4. With ``--baseline``: the Markdown card must be byte-identical to the
   baseline engine's render of the same plan. HTML may differ by design;
   silent Markdown drift is the red flag, because that surface is the only
   carrier of every figure on clients without widget rendering.

Exit code 0 only when every persona passes every gate.

Scope: every card here is the pre-commitment **first-review** render — each
persona prepares into a fresh empty root, so ``route=auto`` resolves to
``first_review``. Weekly-only surfaces (the prior-commitment mirror,
due-revisit swap panels, rule-breach blocks) are exercised by the fixture
suites (``tests/test_card_html.py``, ``tests/test_review_v2.py``), not by
this sweep.
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

TESTS_DIR = pathlib.Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
SKILL_DIR = REPO / "skills" / "fomo-kernel"
LOCALES = ("zh-TW", "en")

NARRATIVE = {
    "zh-TW": {
        "headline": "這期最值得記住的是紀律的變化。",
        "mirror": "你上期在意的事，這期有了對照。",
        "honesty": "這項資料有已標註的侷限，判讀時保守以對。",
    },
    "en": {
        "headline": "The discipline shift is what this period should be remembered for.",
        "mirror": "What you cared about last period now has its mirror.",
        "honesty": "This figure carries a known limitation; read it conservatively.",
    },
}


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare_plans(out):
    """Run review.py prepare for every persona x locale; return the plans dir."""
    plans = out / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    stub_dir = out / "stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)
    # The repo's _offline_engine_env pattern (tests/test_review_v2.py): the
    # real engine subprocess imports this stub instead of yfinance, so the
    # sweep stays offline and open-position personas degrade deterministically.
    (stub_dir / "yfinance.py").write_text('raise ImportError("offline stub")\n', encoding="utf-8")
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(stub_dir), base_env.get("PYTHONPATH")) if part)
    failures = []
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
            (plans / f"{persona}-{locale}.plan.json").write_text(plan_files[0].read_text())
    return plans, failures


def build_bundle(plan):
    """Mirror review.py's preview bundle shape for a pre-commitment render."""
    locale = "zh-TW" if str(plan.get("language", "zh-TW")).lower().startswith("zh") else "en"
    text = NARRATIVE[locale]
    required = sorted(set((plan.get("card_plan") or {}).get("required_honesty_keys") or []))
    narrative = {"headline": text["headline"], "mirror": text["mirror"]}
    if required:
        narrative["honesty"] = {key: text["honesty"] for key in required}
    return {
        "schema_version": 2,
        "engine_version": plan.get("engine_version"),
        "session_id": plan["session_id"],
        "route": plan["route"],
        "language": plan["language"],
        "review_plan": plan,
        "engine_state": plan["engine_state"],
        "engine_card": plan["engine_card"],
        "answers": {"session_id": plan["session_id"]},
        "narrative": narrative,
        "thesis_updates": [],
        "thesis_decisions": [],
        "exit_narratives": [],
        "commitment": None,
        "observations": [],
    }


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
CJK = re.compile("[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


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


def render_all(engine_dir, plans_dir, render_dir, gate):
    """Render every plan with one engine version; optionally run the gates."""
    card_renderer = _load_module("sweep_card_renderer", pathlib.Path(engine_dir) / "card_renderer.py")
    check_card = _load_module("sweep_check_card", TESTS_DIR / "agent" / "check_card.py") if gate else None
    render_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for plan_file in sorted(pathlib.Path(plans_dir).glob("*.plan.json")):
        name = plan_file.name.replace(".plan.json", "")
        plan = json.loads(plan_file.read_text())
        bundle = build_bundle(plan)
        try:
            markdown = card_renderer.render_private(bundle)
            html_card = card_renderer.render_html(bundle)
        except Exception as exc:  # a crash on any persona is exactly what the sweep exists to catch
            failures.append(f"render {name}: {type(exc).__name__}: {exc}")
            continue
        (render_dir / f"{name}.md").write_text(markdown)
        (render_dir / f"{name}.html").write_text(html_card)
        if not gate:
            continue
        # The plan itself is a valid S-2 context: check_card's _context_card
        # reads its engine_card key, so module lighting is actually checked
        # rather than degraded to a skip.
        broken = [f.assertion for f in check_card.check_card(markdown, plan) if not f.passed]
        if broken:
            failures.append(f"check_card {name}: {', '.join(broken)}")
        failures.extend(f"html {name}: {p}" for p in html_invariants(html_card))
        locale = "en" if str(plan.get("language", "")).lower() == "en" else "zh-TW"
        failures.extend(f"markdown {name}: {p}" for p in locale_purity(markdown, locale))
        failures.extend(f"html {name}: {p}" for p in locale_purity(html_card, locale))
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default=str(SKILL_DIR / "engine"),
                        help="engine directory whose card_renderer renders the cards")
    parser.add_argument("--baseline", default=None,
                        help="second engine directory; Markdown must be byte-identical to it")
    parser.add_argument("--out", default=None, help="working directory (default: temp dir)")
    parser.add_argument("--plans", default=None,
                        help="reuse an existing plans dir instead of running prepare")
    parser.add_argument("--render-only", action="store_true",
                        help="internal: render --plans with --engine, no prepare, no gates")
    args = parser.parse_args()

    out = pathlib.Path(args.out or tempfile.mkdtemp(prefix="persona-sweep-"))
    failures = []
    if args.plans:
        plans = pathlib.Path(args.plans)
    else:
        plans, failures = prepare_plans(out)

    if args.render_only:
        failures += render_all(args.engine, plans, out / "render", gate=False)
        for line in failures:
            print(f"FAIL  {line}")
        return 1 if failures else 0

    failures += render_all(args.engine, plans, out / "render", gate=True)

    if args.baseline:
        proc = subprocess.run(
            [sys.executable, __file__, "--render-only", "--engine", args.baseline,
             "--plans", str(plans), "--out", str(out / "baseline")],
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

    plan_count = len(list(plans.glob("*.plan.json")))
    for line in failures:
        print(f"FAIL  {line}")
    verdict = f"FAIL: {len(failures)} failure(s)" if failures else "PASS: all gates pass"
    print(f"\npersona sweep: {plan_count} cards rendered to {out} — {verdict}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
