#!/usr/bin/env python3
"""Deterministic probes for the cross-client surface adapter receipt."""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills" / "fomo-kernel" / "tools" / "ux_receipt.py"
SPEC = ROOT / "skills" / "fomo-kernel" / "references" / "interaction-delivery.md"

module_spec = importlib.util.spec_from_file_location("ux_receipt", TOOL)
ux_receipt = importlib.util.module_from_spec(module_spec)
assert module_spec.loader is not None
module_spec.loader.exec_module(ux_receipt)


def declaration(**overrides):
    value = {
        "version": 1,
        "event": "capabilities_declared",
        "session_id": "session-230",
        "client": "codex-desktop",
        "route": "first_review",
        "question_modes": ["plain_text"],
        "card_modes": ["markdown_inline"],
        "required_question_ids": ["q-1"],
        "expected_memory": [],
    }
    value.update(overrides)
    return value


def row(event, **values):
    return {"version": 1, "event": event, "session_id": "session-230", **values}


def good_markdown_rows():
    return [
        declaration(),
        row("question_presented", question_id="q-1", mode="plain_text"),
        row("question_answered", question_id="q-1"),
        row("artifact_generated", stage="preview", artifact_path="/tmp/card-private-preview.md"),
        row("card_presented", stage="preview", mode="markdown_inline"),
        row("commitment_answered"),
        row("artifact_generated", stage="final", artifact_path="/tmp/card-private.md"),
        row("card_presented", stage="final", mode="markdown_inline"),
    ]


def assert_has(errors, fragment):
    assert any(fragment in error for error in errors), errors


def test_known_good_text_fallback_passes():
    assert ux_receipt.verify_rows(good_markdown_rows()) == []


def test_native_controls_and_widget_pass():
    rows = good_markdown_rows()
    rows[0] = declaration(
        question_modes=["plain_text", "native_options"],
        card_modes=["markdown_inline", "widget"],
    )
    rows[1]["mode"] = "native_options"
    rows[4]["mode"] = "widget"
    rows[7]["mode"] = "widget"
    assert ux_receipt.verify_rows(rows) == []


def test_artifact_generation_does_not_count_as_card_delivery():
    rows = good_markdown_rows()
    del rows[4]
    assert_has(ux_receipt.verify_rows(rows), "preview card_presented must appear exactly once")


def test_duplicate_question_interaction_fails():
    rows = good_markdown_rows()
    rows.insert(2, row("question_presented", question_id="q-1", mode="plain_text"))
    assert_has(ux_receipt.verify_rows(rows), "must be presented exactly once")


def test_receipt_rejects_private_payload_fields():
    rows = good_markdown_rows()
    rows[2]["answer"] = "price_only"
    assert_has(ux_receipt.verify_rows(rows), "forbidden receipt fields")


def test_widget_capability_requires_explicit_failure_before_markdown_fallback():
    rows = good_markdown_rows()
    rows[0] = declaration(card_modes=["markdown_inline", "widget"])
    assert_has(ux_receipt.verify_rows(rows), "without recording a failed widget attempt")
    rows.insert(4, row("widget_attempt_failed", stage="preview"))
    assert ux_receipt.verify_rows(rows) == []


def test_weekly_opening_memory_is_observable():
    rows = good_markdown_rows()
    rows[0] = declaration(
        route="weekly_review",
        expected_memory=["prior_commitment", "exit_reason", "due_revisit"],
    )
    errors = ux_receipt.verify_rows(rows)
    assert_has(errors, "expected memory 'prior_commitment'")
    assert_has(errors, "expected memory 'exit_reason'")
    assert_has(errors, "expected memory 'due_revisit'")

    rows[1:1] = [
        row("memory_presented", memory_kind="prior_commitment"),
        row("memory_presented", memory_kind="exit_reason"),
        row("memory_presented", memory_kind="due_revisit"),
    ]
    assert ux_receipt.verify_rows(rows) == []


def test_manual_verification_requires_owner_experience_verdict():
    rows = good_markdown_rows()
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "requires exactly one owner_verdict",
    )
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True) == []


def test_manual_weekly_verification_requires_memory_to_feel_present():
    rows = good_markdown_rows()
    rows[0] = declaration(route="weekly_review", expected_memory=["prior_skip"])
    rows.insert(1, row("memory_presented", memory_kind="prior_skip"))
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="fail"))
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "requires a passing memory verdict",
    )
    rows[-1]["memory"] = "pass"
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True) == []


def test_cli_writes_only_bounded_receipt_fields():
    with tempfile.TemporaryDirectory() as tmp:
        receipt = pathlib.Path(tmp) / "ux.jsonl"
        start = subprocess.run(
            [
                sys.executable, str(TOOL), "start", "--path", str(receipt),
                "--session-id", "session-230", "--client", "codex-desktop",
                "--route", "first_review", "--question-mode", "plain_text",
                "--card-mode", "markdown_inline", "--required-question", "q-1",
            ],
            capture_output=True,
            text=True,
        )
        assert start.returncode == 0, start.stderr
        event = subprocess.run(
            [
                sys.executable, str(TOOL), "event", "--path", str(receipt),
                "--event", "question_presented", "--question-id", "q-1",
                "--mode", "plain_text",
            ],
            capture_output=True,
            text=True,
        )
        assert event.returncode == 0, event.stderr
        lines = [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
        assert set(lines[1]) == {"version", "event", "session_id", "question_id", "mode"}

        rejected = subprocess.run(
            [
                sys.executable, str(TOOL), "event", "--path", str(receipt),
                "--event", "question_answered", "--question-id", "my answer has spaces",
            ],
            capture_output=True,
            text=True,
        )
        assert rejected.returncode == 2
        assert "not free text" in rejected.stderr


def test_runtime_contract_contains_fixed_fallback_and_no_file_only_success():
    text = SPEC.read_text(encoding="utf-8")
    for fragment in (
        "A. <label> — <description>",
        "Reply with one option label: A, B, ...",
        "Artifact generation is not presentation",
        "A file path or attachment without inline card content is not presentation",
        "--require-owner-verdict",
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


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} interaction trajectory tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
