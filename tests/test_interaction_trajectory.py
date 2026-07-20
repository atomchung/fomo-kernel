#!/usr/bin/env python3
"""Deterministic probes for the slim cross-client presentation trace.

Scope mirrors ux_receipt.py: prove that each engine-rendered card actually
reached the user (and the weekly opening memory), and nothing more. Answer and
commitment completeness are the engine's job (test_review_v2 / thesis) and are
deliberately not re-tested here.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills" / "fomo-kernel" / "tools" / "ux_receipt.py"
SPEC = ROOT / "skills" / "fomo-kernel" / "references" / "interaction-delivery.md"
SURFACE_DIGEST = "a" * 64

module_spec = importlib.util.spec_from_file_location("ux_receipt", TOOL)
ux_receipt = importlib.util.module_from_spec(module_spec)
assert module_spec.loader is not None
module_spec.loader.exec_module(ux_receipt)


def declaration(**overrides):
    value = {
        "version": 2,
        "event": "capabilities_declared",
        "session_id": "session-230",
        "client": "codex-desktop",
        "route": "first_review",
        "question_modes": ["plain_text"],
        "card_modes": ["markdown_inline"],
    }
    value.update(overrides)
    return value


def row(event, **values):
    return {"version": 2, "event": event, "session_id": "session-230", **values}


def good_markdown_rows():
    return [
        declaration(),
        row("artifact_generated", stage="preview", artifact_path="/tmp/card-private-preview.md"),
        row("card_presented", stage="preview", mode="markdown_inline"),
        row("artifact_generated", stage="final", artifact_path="/tmp/card-private.md"),
        row("card_presented", stage="final", mode="markdown_inline"),
    ]


def weekly_rows():
    rows = good_markdown_rows()
    rows[0] = declaration(route="weekly_review")
    rows.insert(1, row("memory_presented", memory_kind="prior_commitment"))
    return rows


def assert_has(errors, fragment):
    assert any(fragment in error for error in errors), errors


# --- Happy paths --------------------------------------------------------------

def test_known_good_text_fallback_passes():
    assert ux_receipt.verify_rows(good_markdown_rows()) == []


def test_native_controls_and_widget_pass():
    rows = good_markdown_rows()
    rows[0] = declaration(
        question_modes=["plain_text", "native_options"],
        card_modes=["markdown_inline", "widget"],
    )
    rows.insert(1, row("question_presented", mode="native_options",
                       surface_source="validated_dynamic", surface_digest=SURFACE_DIGEST))
    rows[3]["mode"] = "widget"   # preview card
    rows[5]["mode"] = "widget"   # final card
    assert ux_receipt.verify_rows(rows) == []


def test_question_surface_trace_is_content_free():
    rows = good_markdown_rows()
    rows.insert(1, row("question_presented", mode="plain_text",
                       surface_source="engine_fallback", surface_digest=SURFACE_DIGEST))
    assert ux_receipt.verify_rows(rows) == []

    leaked = [dict(value) for value in rows]
    leaked[1]["stem"] = "private trade wording"
    assert_has(ux_receipt.verify_rows(leaked), "question trace contains content fields")

    missing = [dict(value) for value in rows]
    missing[1].pop("surface_digest")
    assert_has(ux_receipt.verify_rows(missing), "source and digest must appear together")

    invalid = [dict(value) for value in rows]
    invalid[1]["surface_digest"] = "not-a-digest"
    assert_has(ux_receipt.verify_rows(invalid), "invalid surface digest")


def test_weekly_opening_memory_passes():
    assert ux_receipt.verify_rows(weekly_rows()) == []


# --- Presentation is not artifact generation ---------------------------------

def test_generated_without_presented_fails():
    rows = good_markdown_rows()
    del rows[2]  # drop the preview card_presented, keep its artifact
    assert_has(ux_receipt.verify_rows(rows), "preview card_presented must appear exactly once")


def test_card_marked_presented_before_artifact_fails():
    rows = good_markdown_rows()
    rows[1], rows[2] = rows[2], rows[1]  # card before its artifact
    assert_has(ux_receipt.verify_rows(rows), "before its artifact existed")


def test_final_card_before_preview_card_fails():
    rows = [good_markdown_rows()[0]] + good_markdown_rows()[3:5] + good_markdown_rows()[1:3]
    assert_has(ux_receipt.verify_rows(rows), "final card presentation must follow the preview card")


# --- Widget degradation must be explicit -------------------------------------

def test_declared_widget_silent_markdown_fails():
    rows = good_markdown_rows()
    rows[0] = declaration(card_modes=["markdown_inline", "widget"])
    assert_has(ux_receipt.verify_rows(rows), "without recording a failed widget attempt")


def test_declared_widget_with_recorded_failure_passes():
    rows = good_markdown_rows()
    rows[0] = declaration(card_modes=["markdown_inline", "widget"])
    rows.insert(1, row("widget_attempt_failed", stage="preview"))
    assert ux_receipt.verify_rows(rows) == []


def test_widget_failure_without_capability_fails():
    rows = good_markdown_rows()
    rows.insert(1, row("widget_attempt_failed", stage="preview"))
    assert_has(ux_receipt.verify_rows(rows), "without declared widget capability")


# --- Capability / mode declarations ------------------------------------------

def test_missing_universal_fallbacks_fail():
    rows = good_markdown_rows()
    rows[0] = declaration(question_modes=["native_options"], card_modes=["widget"])
    errors = ux_receipt.verify_rows(rows)
    assert_has(errors, "plain_text as the universal question fallback")
    assert_has(errors, "markdown_inline as the universal card fallback")


def test_undeclared_card_mode_fails():
    rows = good_markdown_rows()
    rows[2]["mode"] = "widget"  # not declared
    assert_has(ux_receipt.verify_rows(rows), "undeclared mode")


def test_undeclared_question_mode_fails():
    rows = good_markdown_rows()
    rows.insert(1, row("question_presented", mode="native_options"))  # not declared
    assert_has(ux_receipt.verify_rows(rows), "question used undeclared mode")


# --- Weekly opening memory ordering ------------------------------------------

def test_weekly_missing_opener_fails():
    rows = good_markdown_rows()
    rows[0] = declaration(route="weekly_review")
    assert_has(ux_receipt.verify_rows(rows), "exactly one prior commitment or skip opener")


def test_weekly_opener_after_first_card_fails():
    rows = good_markdown_rows()
    rows[0] = declaration(route="weekly_review")
    rows.append(row("memory_presented", memory_kind="prior_skip"))  # after both cards
    assert_has(ux_receipt.verify_rows(rows), "after the first question or card")


# --- Declaration integrity ---------------------------------------------------

def test_session_id_must_be_consistent():
    rows = good_markdown_rows()
    rows[2]["session_id"] = "another-session"
    assert_has(ux_receipt.verify_rows(rows), "declared session_id")


def test_unknown_route_fails():
    rows = good_markdown_rows()
    rows[0] = declaration(route="bogus_route")
    assert_has(ux_receipt.verify_rows(rows), "unsupported route")


def test_version_mismatch_fails():
    rows = good_markdown_rows()
    rows[2]["version"] = 1
    assert_has(ux_receipt.verify_rows(rows), "unsupported version")


def test_missing_declaration_first_fails():
    rows = good_markdown_rows()[1:]  # no capabilities_declared row
    assert_has(ux_receipt.verify_rows(rows), "capabilities_declared event as its first row")


# --- Owner verdict / manual gate ---------------------------------------------

def test_owner_verdict_must_follow_final_card():
    rows = good_markdown_rows()
    rows.insert(4, row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    assert_has(ux_receipt.verify_rows(rows), "must follow the final card presentation")


def test_manual_verification_requires_owner_verdict():
    rows = good_markdown_rows()
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "requires exactly one owner_verdict",
    )
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True) == []


def test_dynamic_surface_manual_verdict_requires_specificity_and_answer_fit():
    rows = good_markdown_rows()
    rows.insert(1, row("question_presented", mode="plain_text",
                       surface_source="validated_dynamic", surface_digest=SURFACE_DIGEST))
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "requires passing question specificity and answer fit verdicts",
    )
    rows[-1].update(question_specificity="pass", answer_fit="fail")
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "requires passing question specificity and answer fit verdicts",
    )
    rows[-1]["answer_fit"] = "pass"
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True) == []


def test_manual_weekly_requires_memory_verdict():
    rows = weekly_rows()
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="fail"))
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "requires a passing memory verdict",
    )
    rows[-1]["memory"] = "pass"
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True) == []


# --- CLI end to end ----------------------------------------------------------

def test_cli_writes_trace_into_protected_state_root():
    with tempfile.TemporaryDirectory() as tmp:
        common = ["--session-id", "session-230", "--state-root", tmp]
        start = subprocess.run(
            [sys.executable, str(TOOL), "start", *common,
             "--client", "codex-desktop", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True,
        )
        assert start.returncode == 0, start.stderr
        receipt = pathlib.Path(tmp) / "ux" / "session-230.jsonl"
        assert receipt.is_file(), "trace must live under <state-root>/ux/"

        # start refuses to overwrite an existing trace
        again = subprocess.run(
            [sys.executable, str(TOOL), "start", *common,
             "--client", "codex-desktop", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True,
        )
        assert again.returncode == 2 and "refusing to overwrite" in again.stderr

        for args in (
            ["--event", "question_presented", "--mode", "plain_text",
             "--surface-source", "validated_dynamic", "--surface-digest", SURFACE_DIGEST],
            ["--event", "artifact_generated", "--stage", "preview", "--artifact-path", "/tmp/p.md"],
            ["--event", "card_presented", "--stage", "preview", "--mode", "markdown_inline"],
            ["--event", "artifact_generated", "--stage", "final", "--artifact-path", "/tmp/f.md"],
            ["--event", "card_presented", "--stage", "final", "--mode", "markdown_inline"],
        ):
            done = subprocess.run(
                [sys.executable, str(TOOL), "event", *common, *args],
                capture_output=True, text=True,
            )
            assert done.returncode == 0, done.stderr

        verified = subprocess.run(
            [sys.executable, str(TOOL), "verify", *common],
            capture_output=True, text=True,
        )
        assert verified.returncode == 0, verified.stderr
        assert json.loads(verified.stdout)["status"] == "pass"


def test_cli_rejects_undeclared_stage_choice():
    # argparse choices constrains stage/mode/route/memory-kind at the CLI edge.
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [sys.executable, str(TOOL), "start", "--session-id", "s", "--state-root", tmp,
             "--client", "c", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True, check=True,
        )
        bad = subprocess.run(
            [sys.executable, str(TOOL), "event", "--session-id", "s", "--state-root", tmp,
             "--event", "card_presented", "--stage", "AAPL 100@150", "--mode", "markdown_inline"],
            capture_output=True, text=True,
        )
        assert bad.returncode != 0 and "invalid choice" in bad.stderr


# --- Contract mirror ---------------------------------------------------------

def test_runtime_contract_contains_fixed_fallback_and_no_file_only_success():
    text = SPEC.read_text(encoding="utf-8")
    for fragment in (
        "A. <label> — <description>",
        "Reply with one option label: A, B, ...",
        "Artifact generation is not presentation",
        "A file path or attachment without inline card content is not presentation",
        "--require-owner-verdict",
        "protected state directory",
        "--surface-source",
        "--surface-digest",
        "--question-specificity",
        "--answer-fit",
    ):
        assert fragment in text, fragment


def test_every_runtime_adapter_routes_to_one_shared_contract():
    surfaces = [
        ROOT / "AGENTS.md",
        ROOT / "skills" / "fomo-kernel" / "SKILL.md",
        *sorted((ROOT / "skills" / "fomo-kernel" / "flows").glob("*.md")),
    ]
    missing = [str(path.relative_to(ROOT)) for path in surfaces
               if "references/interaction-delivery.md" not in path.read_text(encoding="utf-8")]
    assert not missing, f"runtime surfaces bypass the shared adapter contract: {missing}"


def test_final_artifact_before_preview_card_fails():
    # #239 review (Codex): the final stage must not begin before the preview is visible.
    rows = [declaration(),
            row("artifact_generated", stage="preview", artifact_path="/tmp/p.md"),
            row("artifact_generated", stage="final", artifact_path="/tmp/f.md"),
            row("card_presented", stage="preview", mode="markdown_inline"),
            row("card_presented", stage="final", mode="markdown_inline")]
    assert_has(ux_receipt.verify_rows(rows), "final artifact was generated before the preview card")


def test_weekly_opener_after_first_question_fails():
    # #239 review (Codex): the opener must precede the first QUESTION, not merely the first card.
    rows = good_markdown_rows()
    rows[0] = declaration(route="weekly_review")
    rows.insert(1, row("question_presented", mode="plain_text"))
    rows.insert(2, row("memory_presented", memory_kind="prior_commitment"))
    assert_has(ux_receipt.verify_rows(rows), "after the first question or card")


def test_unknown_stage_row_fails_closed():
    # #239 review (Codex): a structurally invalid stage must fail, not be silently ignored.
    rows = good_markdown_rows()
    rows.append(row("card_presented", stage="bogus", mode="markdown_inline"))
    assert_has(ux_receipt.verify_rows(rows), "unsupported stage")


def test_cli_rejects_session_id_path_traversal():
    # #239 review (Codex): session_id must not escape <state-root>/ux/ via path separators.
    with tempfile.TemporaryDirectory() as tmp:
        bad = subprocess.run(
            [sys.executable, str(TOOL), "start", "--session-id", "../escape", "--state-root", tmp,
             "--client", "c", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True,
        )
        assert bad.returncode == 2 and "not a path" in bad.stderr


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} interaction trajectory tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
