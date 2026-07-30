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
    ("Same-day fetch cache (#235)", "tests/test_fetch_cache.py"),
    ("Market-data resolver contract (#605)", "tests/test_market_data.py"),
    ("Condition slots (#412)", "tests/test_conditions.py"),
    ("Hypothetical-trade consequence (Layer 2)", "tests/test_consequence.py"),
    ("Pre-trade evaluation CLI (Layer 2 entry point)", "tests/test_consider.py"),
    ("Read-only current-book outlet (#561)", "tests/test_positions.py"),
    ("Answer provenance gate (#414, Wave A)", "tests/test_answer_provenance.py"),
    ("Visible evaluation challenge (#479 Wave B)", "tests/test_evaluation_challenge.py"),
    ("Behavior verdicts (#446)", "tests/test_verdicts.py"),
    ("Snapshot-anchored ledger", "tests/test_ledger.py"),
    ("PortfolioBasis current-book contract (#484)", "tests/test_portfolio_basis.py"),
    ("Exit revisit and swap", "tests/test_revisit.py"),
    ("Confirmed disappearance without a fill (#485 Slice C)", "tests/test_position_absence.py"),
    ("Split basis across every share-count reader (#558, #559)", "tests/test_split_basis.py"),
    ("Independent book-refresh lane (#485 Slice C)", "tests/test_book_refresh.py"),
    ("Market context", "tests/test_market_context.py"),
    ("Problem ledger", "tests/test_problems.py"),
    ("Narrative digit-ban", "tests/test_digit_ban.py"),
    ("Position-sizing literal gate (#477)", "tests/test_sizing_literal_gate.py"),
    ("Persona end-to-end", "tests/test_sample_styles.py"),
    ("State-loop end-to-end", os.path.join("skills", "fomo-kernel", "engine", "test_state_loop.py")),
    ("Card and state checker probes", "tests/test_checkers_offline.py"),
    # The narrative judge itself stays out of this gate -- it is opt-in,
    # billable and non-deterministic. What belongs here is its pure logic: the
    # manifest gate that refuses a fixture set which could not catch a
    # rubber-stamp judge, the refusal branch that must run before any content
    # block is read, and the request/schema shape. Same rule
    # `evals/judge_episodes.py` states for its own interlocks -- one only
    # reachable by someone holding an API key is one nobody re-verifies.
    ("Judge harness offline interlocks", "tests/test_judge_harness_offline.py"),
    ("Local data-control CLI", "tests/test_coach_data_cli.py"),
    ("Skill dependency preflight (doctor)", "tests/test_deps_doctor.py"),
    ("Session finalization idempotency", "tests/test_coach_session_idempotency.py"),
    ("Skill v2 session, ETF, and E2E", "tests/test_review_v2.py"),
    ("Validated private question surfaces", "tests/test_question_surfaces.py"),
    ("Card HTML and delivery contract", "tests/test_card_html.py"),
    ("Design-bundle generator gate (#442)", "tests/test_design_bundle.py"),
    ("Cross-client interaction trajectory", "tests/test_interaction_trajectory.py"),
    ("Automated QA preflight boundary", "tests/test_qa_preflight.py"),
    # #520: the maintainer's own QA tooling under `qa/` is gated here, with the
    # product, because this runner is what actually runs before a commit. A
    # suite that lives outside it is enforced by whoever remembers to type its
    # path -- which is how `qa/tests/test_receipts.py` shipped and then went
    # unrun, and how `qa/SKILL.md`'s commands drifted from `ux_receipt.py`
    # until an operator discovered it at the end of a walkthrough, with the
    # whole append-only trace already spent.
    ("QA skill command drift (#520)", "qa/tests/test_skill_commands.py"),
    ("QA receipt manifest and campaign lineage (#520)", "qa/tests/test_receipts.py"),
    # #557: isolation stopped being an instruction. The harness refuses every
    # command until the account's own coach root is out of reach, and this is
    # the suite that watches the refusal rather than the export.
    ("QA run isolation gate (#557)", "qa/tests/test_isolation_gate.py"),
    ("Public-text privacy lint", "tests/test_privacy_lint.py"),
    # #575: repository integrity that changes no behaviour and therefore no
    # behavioural suite can see. A lone `<<<<<<< HEAD` lived inside a docstring
    # in `revisit.py` from #562 until #574 — valid Python, nothing raised, 44
    # green suites over it on every run.
    ("Repository hygiene (#575)", "tests/test_repo_hygiene.py"),
    ("Documentation and agent workflow boundaries", "tests/test_doc_language.py"),
    ("Copy ratchet (#368 Phase 1)", "tests/test_copy_ratchet.py"),
    # #402 knife 5: the copy branches no persona reaches, rendered on all three
    # delivery surfaces and compared against a generated golden. Unlike the
    # sweep's --baseline half this one needs no second checkout, so it does
    # belong in the default gate: the golden is committed, and an intended
    # wording change regenerates it with `--update`.
    ("Copy corpus golden (#402 knife 5)", "tests/copy_corpus.py"),
    ("Episode checker probes (#417)", "tests/test_episode_checkers.py"),
    # #417: the mechanical half of the question-episode bank. It belongs in the
    # default suite because it is offline, deterministic and free -- what
    # eval-design keeps out of CI is the non-deterministic, billable judge half,
    # which lives nowhere near this entry. A bank nobody replays rots, and the
    # loop this operationalizes had accumulated zero records while it depended
    # on whoever remembered (the #368 lesson, applied one layer up).
    ("Question-episode bank, mechanical half (#417)", "evals/run_episodes.py"),
    ("Trigger matrix corpus and scorer (#458)", "tests/test_triggers.py"),
    # #368: the sweep used to be a prose maintainer rule ("if the engine
    # changed, run the persona sweep") — i.e. enforced by whoever remembered.
    # It renders every persona x locale x decision variant in about the time
    # one fixture suite takes, so there is no cost argument for leaving the
    # repo's widest renderer gate to self-discipline. The byte-parity half
    # (--baseline) stays a manual tool by design: it needs a second engine
    # checkout, and requiring byte-identity on a PR would fail every
    # legitimate copy change.
    ("Persona sweep (personas x locales x reviews x variants)", "tests/persona_sweep.py"),
]


def main():
    # #605: the offline posture is set once, here, rather than remembered by
    # each suite. CI never installs yfinance so it is offline by construction,
    # but a maintainer's machine has it — and once `consider` resolves market
    # data automatically, every one of its ninety-odd `--prices`-free tests would
    # reach the network, go non-deterministic, and move the literal
    # `evaluation_id` digest `tests/test_consider.py` pins. Subprocesses inherit
    # this, so a suite added to SUITES gets the posture without opting in, which
    # is the difference between a rule in a primitive and a rule at a call site.
    # `TR_TEST_NETWORK=1` is the documented opt-in and must still work.
    # Assigned, not setdefault: an inherited `TR_OFFLINE=0` would otherwise
    # survive and let the default suite reach the network without anyone asking
    # for it (external review, finding 9). `TR_TEST_NETWORK=1` stays the one
    # documented opt-in, and it clears the posture rather than competing with it.
    if os.environ.get("TR_TEST_NETWORK") == "1":
        os.environ.pop("TR_OFFLINE", None)
    else:
        os.environ["TR_OFFLINE"] = "1"

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
