#!/usr/bin/env python3
"""Run the offline deterministic fomo-kernel regression suites.

The runner uses only the Python standard library and does not require pytest.
It executes the selected entries of ``SUITES`` sequentially and returns a
non-zero exit code if any suite fails.

Every entry declares an owner, because a red result answers a different
question depending on which one it is (#492):

``product``
    A red result means product runtime, canonical state, privacy, recovery,
    replay, compatibility, or an explicitly supported user surface may be
    wrong. Someone using this product could get a wrong number or lose state.
``qa-eval``
    A red result means the *maintainer's own evidence system* is wrong: the
    synthetic operator, the receipt and archive workflow, campaign lineage,
    the judge harness, the question-episode bank, or QA-only tooling and its
    documentation. No end user is affected; what broke is our ability to
    gather evidence about the product.

When it is not obvious which one a suite is, it stays in ``product``. Being
slow is never a reason to move one: the split is about who a failure
implicates, not about cost. And a suite belongs to exactly one group — copying
its assertions into both is what this replaced.

``all`` remains the default, so every existing caller keeps its behaviour.

Usage:
  python3 tests/run_all.py                     # the whole registry (--group all)
  python3 tests/run_all.py --group product     # what the blocking CI job runs
  python3 tests/run_all.py --group qa-eval     # the maintainer evidence system
  TR_TEST_NETWORK=1 python3 tests/run_all.py   # optional network smoke coverage
  git diff --name-only main | python3 tests/run_all.py --scope
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GROUPS = ("product", "qa-eval")

# Directories the maintainer evidence system owns outright. A pull request
# touching one of these is inside QA/eval's own scope, so `qa-eval-tooling`
# blocks on it rather than reporting advisory -- see `scope_of`. Kept here,
# beside the registry, because the alternative is a second path list in the
# workflow that drifts from this one, and the drift is silent in the direction
# that matters: QA quietly stops blocking its own changes.
# `tests/agent/` is in the list because its own README opens by calling itself
# the agent-level evaluation harness, with `docs/eval-design.md` as its
# authority: the checkers there grade fixtures and judge output, and nothing a
# user runs reaches them.
QA_EVAL_OWNED_PREFIXES = ("qa/", "evals/", "tests/agent/")

SUITES = [
    ("Engine unit tests", "tests/test_engine_units.py", "product"),
    ("TR_JSON and state contract", "tests/test_tr_json_contract.py", "product"),
    ("Synthetic price paths", "tests/test_price_paths.py", "product"),
    ("Agent-supplied price fallback", "tests/test_price_feed.py", "product"),
    ("Same-day fetch cache (#235)", "tests/test_fetch_cache.py", "product"),
    ("Market-data resolver contract (#605)", "tests/test_market_data.py", "product"),
    ("Condition slots (#412)", "tests/test_conditions.py", "product"),
    ("Hypothetical-trade consequence (Layer 2)", "tests/test_consequence.py", "product"),
    ("Pre-trade evaluation CLI (Layer 2 entry point)", "tests/test_consider.py", "product"),
    ("Synthetic consider QA walkthrough (#718)", "tests/test_synthetic_walk.py", "qa-eval"),
    ("Read-only current-book outlet (#561)", "tests/test_positions.py", "product"),
    ("Answer provenance gate (#414, Wave A)", "tests/test_answer_provenance.py", "product"),
    ("Visible evaluation challenge (#479 Wave B)", "tests/test_evaluation_challenge.py", "product"),
    ("Behavior verdicts (#446)", "tests/test_verdicts.py", "product"),
    ("Snapshot-anchored ledger", "tests/test_ledger.py", "product"),
    ("PortfolioBasis current-book contract (#484)", "tests/test_portfolio_basis.py", "product"),
    ("Exit revisit and swap", "tests/test_revisit.py", "product"),
    ("Confirmed disappearance without a fill (#485 Slice C)", "tests/test_position_absence.py", "product"),
    ("Split basis across every share-count reader (#558, #559)", "tests/test_split_basis.py", "product"),
    ("Independent book-refresh lane (#485 Slice C)", "tests/test_book_refresh.py", "product"),
    ("Market context", "tests/test_market_context.py", "product"),
    ("Weekly market-read prototype (#683)", "tests/test_weekly_market_read.py", "product"),
    ("Weekly market-read locale regression (#683)", "tests/test_weekly_market_read_locales.py", "product"),
    ("Problem ledger", "tests/test_problems.py", "product"),
    ("Narrative digit-ban", "tests/test_digit_ban.py", "product"),
    ("Position-sizing literal gate (#477)", "tests/test_sizing_literal_gate.py", "product"),
    ("Persona end-to-end", "tests/test_sample_styles.py", "product"),
    ("State-loop end-to-end", os.path.join("skills", "fomo-kernel", "engine", "test_state_loop.py"), "product"),
    # `qa-eval` because its subject is `tests/agent/check_card.py` and
    # `check_state.py` -- the harness that grades fixtures and judge output.
    # A red here means a checker went blind, not that a card is wrong.
    ("Card and state checker probes", "tests/test_checkers_offline.py", "qa-eval"),
    ("Global output-voice contract (#676)", "tests/test_output_voice.py", "product"),
    # The narrative judge itself stays out of this gate -- it is opt-in,
    # billable and non-deterministic. What belongs here is its pure logic: the
    # manifest gate that refuses a fixture set which could not catch a
    # rubber-stamp judge, the refusal branch that must run before any content
    # block is read, and the request/schema shape. Same rule
    # `evals/judge_episodes.py` states for its own interlocks -- one only
    # reachable by someone holding an API key is one nobody re-verifies.
    ("Judge harness offline interlocks", "tests/test_judge_harness_offline.py", "qa-eval"),
    ("Local data-control CLI", "tests/test_coach_data_cli.py", "product"),
    ("Skill dependency preflight (doctor)", "tests/test_deps_doctor.py", "product"),
    ("Session finalization idempotency", "tests/test_coach_session_idempotency.py", "product"),
    # #771: the snapshot adapter's own card/state field contract -- which of
    # the trade lane's fields a position snapshot can honestly support,
    # asserted directly on `prepare()`'s return value rather than through the
    # full CLI lifecycle `test_review_v2.py` covers.
    ("Snapshot adapter card/state field contract (#771)", "tests/test_snapshot_adapter.py", "product"),
    ("Skill v2 session, ETF, and E2E", "tests/test_review_v2.py", "product"),
    ("Validated private question surfaces", "tests/test_question_surfaces.py", "product"),
    # #628: the preview precondition on finalize. It lives in its own suite
    # because every other suite here reaches finalize as a means to something
    # else, and a gate whose only coverage is incidental is one nobody notices
    # going quiet.
    ("Preview precondition on finalize (#628)", "tests/test_preview_gate.py", "product"),
    ("Card HTML and delivery contract", "tests/test_card_html.py", "product"),
    ("Design-bundle generator gate (#442)", "tests/test_design_bundle.py", "product"),
    ("Cross-client interaction trajectory", "tests/test_interaction_trajectory.py", "product"),
    ("Automated QA preflight boundary", "tests/test_qa_preflight.py", "qa-eval"),
    # #520: the maintainer's own QA tooling under `qa/` is registered here, in
    # this one registry, because a suite that lives outside it is enforced by
    # whoever remembers to type its path -- which is how
    # `qa/tests/test_receipts.py` shipped and then went unrun, and how
    # `qa/SKILL.md`'s commands drifted from `ux_receipt.py` until an operator
    # discovered it at the end of a walkthrough, with the whole append-only
    # trace already spent. #492 changed who a red here implicates, never
    # whether it runs: these are `qa-eval`, they run on `--group qa-eval` and
    # on the default `all`, and CI blocks on them when a pull request changes
    # QA/eval-owned files. What they stopped doing is standing between an
    # implementer and a reversible product commit.
    ("QA skill command drift (#520)", "qa/tests/test_skill_commands.py", "qa-eval"),
    ("QA receipt manifest and campaign lineage (#520)", "qa/tests/test_receipts.py", "qa-eval"),
    # #557: isolation stopped being an instruction. The harness refuses every
    # command until the account's own coach root is out of reach, and this is
    # the suite that watches the refusal rather than the export.
    ("QA run isolation gate (#557)", "qa/tests/test_isolation_gate.py", "qa-eval"),
    ("Public-text privacy lint", "tests/test_privacy_lint.py", "product"),
    # #575: repository integrity that changes no behaviour and therefore no
    # behavioural suite can see. A lone `<<<<<<< HEAD` lived inside a docstring
    # in `revisit.py` from #562 until #574 — valid Python, nothing raised, 44
    # green suites over it on every run.
    ("Repository hygiene (#575)", "tests/test_repo_hygiene.py", "product"),
    ("Documentation and agent workflow boundaries", "tests/test_doc_language.py", "product"),
    ("Research-aware no-book framing", "tests/test_research_priors.py", "product"),
    ("Copy ratchet (#368 Phase 1)", "tests/test_copy_ratchet.py", "product"),
    # #402 knife 5: the copy branches no persona reaches, rendered on all three
    # delivery surfaces and compared against a generated golden. Unlike the
    # sweep's --baseline half this one needs no second checkout, so it does
    # belong in the default gate: the golden is committed, and an intended
    # wording change regenerates it with `--update`.
    ("Copy corpus golden (#402 knife 5)", "tests/copy_corpus.py", "product"),
    # #787: names which card-reachable copy keys neither the golden above nor
    # a fresh persona render can see, instead of a maintainer discovering the
    # gap one shipped defect at a time (#733, #766, #779 were three in two
    # days). Builds its own persona bundles rather than reusing #402's
    # rendered corpus text, so it costs roughly one more persona-sweep run.
    ("Copy key coverage (#787)", "tests/copy_key_coverage.py", "product"),
    ("Episode checker probes (#417)", "tests/test_episode_checkers.py", "qa-eval"),
    # #590: only the pure eligibility, blind-prompt, vote-report and durable
    # receipt interlocks run here. The TradeEvaluation model calls stay opt-in
    # exactly like the question-episode judge above.
    ("TradeEvaluation answer judge interlocks (#590)",
     "tests/test_trade_answer_judge.py", "qa-eval"),
    # #417: the mechanical half of the question-episode bank. It belongs in the
    # default suite because it is offline, deterministic and free -- what
    # eval-design keeps out of CI is the non-deterministic, billable judge half,
    # which lives nowhere near this entry. A bank nobody replays rots, and the
    # loop this operationalizes had accumulated zero records while it depended
    # on whoever remembered (the #368 lesson, applied one layer up).
    ("Question-episode bank, mechanical half (#417)", "evals/run_episodes.py", "qa-eval"),
    # `qa-eval` because it probes `evals/triggers/run_triggers.py`, the scorer
    # for a billable measurement run the owner schedules separately. It never
    # runs a host and asserts nothing about how a real session is routed --
    # what is under test is the instrument, not the thing it measures.
    ("Trigger matrix corpus and scorer (#458)", "tests/test_triggers.py", "qa-eval"),
    # #368: the sweep used to be a prose maintainer rule ("if the engine
    # changed, run the persona sweep") — i.e. enforced by whoever remembered.
    # It renders every persona x locale x decision variant in about the time
    # one fixture suite takes, so there is no cost argument for leaving the
    # repo's widest renderer gate to self-discipline. The byte-parity half
    # (--baseline) stays a manual tool by design: it needs a second engine
    # checkout, and requiring byte-identity on a PR would fail every
    # legitimate copy change.
    ("Persona sweep (personas x locales x reviews x variants)", "tests/persona_sweep.py", "product"),
]


def suites_for(group):
    """``(label, path)`` for one group, in registry order.

    ``all`` is not a third owner -- it is the whole registry, which is why it
    is spelled here rather than stored on an entry. An entry that could
    declare itself ``all`` would be an entry in both groups.
    """
    if group not in GROUPS + ("all",):
        raise ValueError(f"unknown group {group!r}; expected one of {GROUPS + ('all',)}")
    return [(label, rel) for label, rel, owner in SUITES
            if group == "all" or owner == group]


def qa_eval_owns(path):
    """Whether ``path`` belongs to the maintainer evidence system.

    Two ways in, because the system is not one directory: the QA and eval
    trees, and the individual suites registered as ``qa-eval`` that live under
    ``tests/`` beside product suites. Reading the second out of ``SUITES``
    rather than restating it is the point -- a suite reclassified in the
    registry moves the blocking decision with it, in the same commit.
    """
    norm = str(path).replace(os.sep, "/")
    # Not `lstrip("./")`: that strips a *set* of characters, so a dotfile
    # directory like `.claude/` would come back as `claude/`.
    if norm.startswith("./"):
        norm = norm[2:]
    if any(norm.startswith(prefix) for prefix in QA_EVAL_OWNED_PREFIXES):
        return True
    return norm in {rel.replace(os.sep, "/") for _, rel in suites_for("qa-eval")}


def scope_of(paths):
    """Whether a set of changed paths puts a run inside QA/eval's own scope.

    One owned path is enough: a diff that touches the receipt workflow *and*
    the engine is a diff whose QA/eval verdict has to block, or the blocking
    rule is decided by whatever else happened to be in the same branch.
    """
    return "qa-eval-owned" if any(qa_eval_owns(p) for p in paths) else "product-only"


def _scope_from_stdin():
    paths = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    print(scope_of(paths))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the offline deterministic fomo-kernel regression suites.")
    parser.add_argument(
        "--group", choices=GROUPS + ("all",), default="all",
        help="which owner's suites to run (default: all -- the whole registry)")
    parser.add_argument(
        "--scope", action="store_true",
        help="read changed paths on stdin and print `qa-eval-owned` or "
             "`product-only`; runs no suites")
    args = parser.parse_args(argv)

    if args.scope:
        return _scope_from_stdin()

    # #605: the offline posture is set once, here, rather than remembered by
    # each suite. CI never installs yfinance so it is offline by construction,
    # but a maintainer's machine has it — and once `consider` resolves market
    # data automatically, every one of its ninety-odd `--prices`-free tests would
    # reach the network, go non-deterministic, and move the literal
    # `evaluation_id` digest `tests/test_consider.py` pins. Subprocesses inherit
    # this, so a suite added to SUITES gets the posture without opting in, which
    # is the difference between a rule in a primitive and a rule at a call site.
    # `TR_TEST_NETWORK=1` is the documented opt-in and must still work.
    #
    # #620 moved the decision itself into `offline_posture`, unchanged, because
    # setting it *here* covered only suites launched through this runner. A file
    # run directly — the normal inner loop when iterating on one suite — bypassed
    # it and resolved live prices, so `python3 tests/test_consider.py` reported
    # nine failures this command did not. Both entry points now read one
    # declaration instead of this one owning it.
    offline_posture.apply()

    selected = suites_for(args.group)
    results = []
    for label, rel in selected:
        path = os.path.join(ROOT, rel)
        print(f"\n{'=' * 64}\n> {label}  ({rel})\n{'=' * 64}", flush=True)
        if not os.path.exists(path):
            print(f"FAIL: missing test file: {path}")
            results.append((label, rel, 127))
            continue
        result = subprocess.run([sys.executable, path], cwd=ROOT)
        results.append((label, rel, result.returncode))

    print(f"\n{'=' * 64}\n  Summary (group: {args.group})\n{'=' * 64}")
    failed = sum(1 for *_, return_code in results if return_code != 0)
    for label, rel, return_code in results:
        status = "PASS" if return_code == 0 else "FAIL"
        print(f"  {status:4}  {label}  ({rel})")
    print()
    # The group is named in both verdicts, not only on failure. A pasted tail
    # is evidence for one question and #492 exists because those two questions
    # were indistinguishable: a `qa-eval` pass is not product correctness, and
    # a `product` pass is not formal QA acceptance.
    if failed:
        print(f"FAIL: {failed}/{len(results)} suites failed "
              f"(group: {args.group}). Do not merge or push.")
    else:
        print(f"PASS: all {len(results)} suites passed (group: {args.group}).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
