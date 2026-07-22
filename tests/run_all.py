#!/usr/bin/env python3
"""Run every offline deterministic fomo-kernel regression suite.

The runner uses only the Python standard library and does not require pytest.
It executes every entry in ``SUITES`` sequentially and returns a non-zero exit
code if any suite fails, making it suitable for CI and local commit gates.

Usage:
  python3 tests/run_all.py
  TR_TEST_NETWORK=1 python3 tests/run_all.py  # optional network smoke coverage
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUITES = [
    ("Engine unit tests", "tests/test_engine_units.py"),
    ("TR_JSON and state contract", "tests/test_tr_json_contract.py"),
    ("Synthetic price paths", "tests/test_price_paths.py"),
    ("Agent-supplied price fallback", "tests/test_price_feed.py"),
    ("Snapshot-anchored ledger", "tests/test_ledger.py"),
    ("Exit revisit and swap", "tests/test_revisit.py"),
    ("Market context", "tests/test_market_context.py"),
    ("Problem ledger", "tests/test_problems.py"),
    ("Narrative digit-ban", "tests/test_digit_ban.py"),
    ("Persona end-to-end", "tests/test_sample_styles.py"),
    ("State-loop end-to-end", os.path.join("skills", "fomo-kernel", "engine", "test_state_loop.py")),
    ("Card and state checker probes", "tests/test_checkers_offline.py"),
    ("Local data-control CLI", "tests/test_coach_data_cli.py"),
    ("Skill dependency preflight (doctor)", "tests/test_deps_doctor.py"),
    ("Session finalization idempotency", "tests/test_coach_session_idempotency.py"),
    ("Skill v2 session, ETF, and E2E", "tests/test_review_v2.py"),
    ("Validated private question surfaces", "tests/test_question_surfaces.py"),
    ("Card HTML and delivery contract", "tests/test_card_html.py"),
    ("Cross-client interaction trajectory", "tests/test_interaction_trajectory.py"),
    ("Public-text privacy lint", "tests/test_privacy_lint.py"),
    ("Documentation and agent workflow boundaries", "tests/test_doc_language.py"),
]


def main():
    results = []
    for label, rel in SUITES:
        path = os.path.join(ROOT, rel)
        print(f"\n{'=' * 64}\n> {label}  ({rel})\n{'=' * 64}", flush=True)
        if not os.path.exists(path):
            print(f"FAIL: missing test file: {path}")
            results.append((label, rel, 127))
            continue
        result = subprocess.run([sys.executable, path], cwd=ROOT)
        results.append((label, rel, result.returncode))

    print(f"\n{'=' * 64}\n  Summary\n{'=' * 64}")
    failed = sum(1 for *_, return_code in results if return_code != 0)
    for label, rel, return_code in results:
        status = "PASS" if return_code == 0 else "FAIL"
        print(f"  {status:4}  {label}  ({rel})")
    print()
    if failed:
        print(f"FAIL: {failed}/{len(results)} suites failed. Do not merge or push.")
    else:
        print(f"PASS: all {len(results)} suites passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
