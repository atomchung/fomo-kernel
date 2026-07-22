#!/usr/bin/env python3
"""Deterministic probes for the slim cross-client presentation trace.

Scope mirrors ux_receipt.py: prove that each engine-rendered card actually
reached the user (and the weekly opening memory), and nothing more. Answer and
commitment completeness are the engine's job (test_review_v2 / thesis) and are
deliberately not re-tested here.
"""

import hashlib
import importlib.util
import json
import os
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


# --- Latency markers: answers_received / rule_choice_presented (#236, #230) ---

def test_latency_marker_events_pass_without_ordering_rules():
    rows = good_markdown_rows()
    rows.insert(1, row("answers_received"))  # before the preview artifact
    rows.insert(4, row("rule_choice_presented", mode="plain_text",
                       grounding_expected=False, grounding_verbatim=True))  # after the preview card
    assert ux_receipt.verify_rows(rows) == []
    # Deliberately no ordering rules for the markers: they verify wherever they
    # appear, including before the weekly opener.
    weekly = weekly_rows()
    weekly.insert(1, row("answers_received"))
    assert ux_receipt.verify_rows(weekly) == []


def test_answers_received_rejects_extra_fields():
    rows = good_markdown_rows()
    rows.insert(1, row("answers_received", note="private wording"))
    assert_has(ux_receipt.verify_rows(rows), "answers_received contains unsupported fields")


def test_rule_choice_rejects_extra_fields():
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="plain_text", options="A/B/C"))
    assert_has(ux_receipt.verify_rows(rows), "rule_choice_presented contains unsupported fields")


def test_rule_choice_undeclared_mode_fails():
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="native_options"))  # only plain_text declared
    assert_has(ux_receipt.verify_rows(rows), "rule choice used undeclared mode")
    missing = good_markdown_rows()
    missing.insert(3, row("rule_choice_presented"))  # no mode at all fails closed
    assert_has(ux_receipt.verify_rows(missing), "rule choice used undeclared mode")


# --- Rule-choice grounding fidelity is machine-checked, not self-attested (#293) --

def test_rule_choice_faithful_grounding_passes():
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="plain_text",
                       grounding_expected=True, grounding_hash=SURFACE_DIGEST,
                       grounding_verbatim=True))
    assert ux_receipt.verify_rows(rows) == []


def test_rule_choice_no_grounding_expected_passes():
    # A candidate list where no candidate carried an engine grounding: nothing
    # to be verbatim about, so the trivial state must still pass.
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="plain_text",
                       grounding_expected=False, grounding_verbatim=True))
    assert ux_receipt.verify_rows(rows) == []


def test_rule_choice_missing_grounding_evidence_fails_closed():
    # This is the #293 bug itself: an agent that records rule_choice_presented
    # without ever running the fidelity check. Unlike optional `ts`, there is
    # no legacy grandfather here — absence must fail exactly like a false
    # result, or an agent could silently keep doing what caused the issue.
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="plain_text"))
    errors = ux_receipt.verify_rows(rows)
    assert_has(errors, "missing grounding-fidelity evidence")
    assert_has(errors, "did not prove its candidates' grounding was presented verbatim")


def test_rule_choice_paraphrased_grounding_fails():
    # Reproduces the reported failure mode: engine grounding existed but the
    # presented text did not contain it verbatim (paraphrased/rewritten).
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="plain_text",
                       grounding_expected=True, grounding_hash=SURFACE_DIGEST,
                       grounding_verbatim=False))
    assert_has(
        ux_receipt.verify_rows(rows),
        "did not prove its candidates' grounding was presented verbatim",
    )


def test_rule_choice_expected_without_hash_fails():
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="plain_text",
                       grounding_expected=True, grounding_verbatim=True))
    assert_has(ux_receipt.verify_rows(rows), "invalid or missing grounding_hash")


def test_rule_choice_hash_without_expectation_fails():
    # Defensive/consistency check: a hash with no expected grounding is a
    # contradictory row (hand-edited or corrupted), not a legitimate state.
    rows = good_markdown_rows()
    rows.insert(3, row("rule_choice_presented", mode="plain_text",
                       grounding_expected=False, grounding_hash=SURFACE_DIGEST,
                       grounding_verbatim=True))
    assert_has(ux_receipt.verify_rows(rows), "grounding_hash but no grounding was expected")


def test_grounding_fidelity_helper_matches_verbatim_containment():
    with tempfile.TemporaryDirectory() as tmp:
        check_path = pathlib.Path(tmp) / "grounding-check.json"
        check_path.write_text(json.dumps({
            "candidates": [
                {"id": "candidate_0", "grounding": "This period's actual position: largest single holding ZZZZ at 48%."},
                {"id": "candidate_1"},
            ],
            "presented_text": (
                "A. Cap position size before adding — "
                "This period's actual position: largest single holding ZZZZ at 48%.\n"
                "B. Sell when the thesis is confirmed false or complete."
            ),
        }), encoding="utf-8")
        result = ux_receipt._grounding_fidelity(str(check_path))
        assert result["grounding_expected"] is True
        assert result["grounding_verbatim"] is True
        assert ux_receipt.SURFACE_DIGEST.fullmatch(result["grounding_hash"])
        # Deterministic: the hash is over the grounding text only, matching
        # the algorithm documented in _grounding_fidelity.
        expected_hash = hashlib.sha256(
            "This period's actual position: largest single holding ZZZZ at 48%.".encode("utf-8")
        ).hexdigest()
        assert result["grounding_hash"] == expected_hash
        # Never persist the raw strings themselves.
        assert set(result) == {"grounding_expected", "grounding_hash", "grounding_verbatim"}


def test_grounding_fidelity_helper_detects_paraphrase():
    with tempfile.TemporaryDirectory() as tmp:
        check_path = pathlib.Path(tmp) / "grounding-check.json"
        check_path.write_text(json.dumps({
            "candidates": [
                {"id": "candidate_0", "grounding": "This period's actual position: largest single holding ZZZZ at 48%."},
            ],
            "presented_text": "A. Cap position size before adding — you're overweight ZZZZ right now.",
        }), encoding="utf-8")
        result = ux_receipt._grounding_fidelity(str(check_path))
        assert result == {
            "grounding_expected": True,
            "grounding_hash": hashlib.sha256(
                "This period's actual position: largest single holding ZZZZ at 48%.".encode("utf-8")
            ).hexdigest(),
            "grounding_verbatim": False,
        }


def test_grounding_fidelity_helper_no_candidates_trivially_passes():
    with tempfile.TemporaryDirectory() as tmp:
        check_path = pathlib.Path(tmp) / "grounding-check.json"
        check_path.write_text(json.dumps({
            "candidates": [{"id": "candidate_0"}, {"id": "candidate_1"}],
            "presented_text": "A. Type your own rule.\nB. Skip for now.",
        }), encoding="utf-8")
        assert ux_receipt._grounding_fidelity(str(check_path)) == {
            "grounding_expected": False,
            "grounding_verbatim": True,
        }


def test_grounding_fidelity_helper_requires_file():
    try:
        ux_receipt._grounding_fidelity(None)
    except ux_receipt.ReceiptError as exc:
        assert "requires --grounding-check-file" in str(exc)
    else:
        raise AssertionError("expected a ReceiptError")


def test_grounding_fidelity_helper_rejects_malformed_json():
    with tempfile.TemporaryDirectory() as tmp:
        check_path = pathlib.Path(tmp) / "grounding-check.json"
        check_path.write_text("not json", encoding="utf-8")
        try:
            ux_receipt._grounding_fidelity(str(check_path))
        except ux_receipt.ReceiptError as exc:
            assert "not valid JSON" in str(exc)
        else:
            raise AssertionError("expected a ReceiptError")


def test_grounding_fidelity_helper_requires_presented_text():
    with tempfile.TemporaryDirectory() as tmp:
        check_path = pathlib.Path(tmp) / "grounding-check.json"
        check_path.write_text(json.dumps({"candidates": []}), encoding="utf-8")
        try:
            ux_receipt._grounding_fidelity(str(check_path))
        except ux_receipt.ReceiptError as exc:
            assert "presented_text must be a non-empty string" in str(exc)
        else:
            raise AssertionError("expected a ReceiptError")


def test_cli_rule_choice_presented_requires_grounding_check_file():
    with tempfile.TemporaryDirectory() as tmp:
        common = ["--session-id", "session-293", "--state-root", tmp]
        subprocess.run(
            [sys.executable, str(TOOL), "start", *common,
             "--client", "codex-desktop", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True, check=True,
        )
        missing = subprocess.run(
            [sys.executable, str(TOOL), "event", *common,
             "--event", "rule_choice_presented", "--mode", "plain_text"],
            capture_output=True, text=True,
        )
        assert missing.returncode == 2
        assert "requires --grounding-check-file" in missing.stderr


def test_cli_rule_choice_presented_persists_only_hash_never_raw_grounding():
    fake_grounding = "This period's actual position: largest single holding ZZZZ at 61%."
    with tempfile.TemporaryDirectory() as tmp:
        common = ["--session-id", "session-293b", "--state-root", tmp]
        subprocess.run(
            [sys.executable, str(TOOL), "start", *common,
             "--client", "codex-desktop", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True, check=True,
        )
        check_path = pathlib.Path(tmp) / "grounding-check.json"
        check_path.write_text(json.dumps({
            "candidates": [{"id": "candidate_0", "grounding": fake_grounding}],
            "presented_text": f"A. Cap position size before adding — {fake_grounding}",
        }), encoding="utf-8")

        done = subprocess.run(
            [sys.executable, str(TOOL), "event", *common,
             "--event", "rule_choice_presented", "--mode", "plain_text",
             "--grounding-check-file", str(check_path)],
            capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr

        receipt = pathlib.Path(tmp) / "ux" / "session-293b.jsonl"
        raw_bytes = receipt.read_bytes()
        assert b"ZZZZ" not in raw_bytes
        assert b"largest single holding" not in raw_bytes
        assert fake_grounding.encode("utf-8") not in raw_bytes

        rows = [json.loads(line) for line in raw_bytes.decode("utf-8").splitlines()]
        presented = [r for r in rows if r["event"] == "rule_choice_presented"][0]
        assert presented["grounding_expected"] is True
        assert presented["grounding_verbatim"] is True
        assert ux_receipt.SURFACE_DIGEST.fullmatch(presented["grounding_hash"])
        assert set(presented) == {
            "version", "event", "session_id", "ts", "mode",
            "grounding_expected", "grounding_hash", "grounding_verbatim",
        }


# --- Timestamps are optional metadata, validated when present (#236) ----------

def test_legacy_trace_without_ts_still_passes():
    assert ux_receipt.verify_rows(good_markdown_rows()) == []
    mixed = good_markdown_rows()
    mixed[2]["ts"] = "2026-07-20T13:46:02Z"  # partially stamped traces pass too
    assert ux_receipt.verify_rows(mixed) == []


def test_fully_stamped_trace_passes():
    rows = good_markdown_rows()
    for value in rows:
        value["ts"] = "2026-07-20T13:46:02Z"
    assert ux_receipt.verify_rows(rows) == []


def test_malformed_ts_fails():
    for bad in ("2026-07-20 13:46:02", "2026-07-20T13:46:02", "not-a-time",
                "2026-13-45T99:99:99Z", 1752934962, None):
        rows = good_markdown_rows()
        rows[1]["ts"] = bad
        assert_has(ux_receipt.verify_rows(rows), "invalid ts")


def test_cli_verifies_legacy_trace_without_ts():
    # A receipt written before ts existed must keep verifying end to end.
    with tempfile.TemporaryDirectory() as tmp:
        receipt = pathlib.Path(tmp) / "ux" / "legacy.jsonl"
        receipt.parent.mkdir(parents=True)
        rows = good_markdown_rows()
        for value in rows:
            value["session_id"] = "legacy"
        receipt.write_text("".join(json.dumps(value) + "\n" for value in rows), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(TOOL), "verify", "--session-id", "legacy", "--state-root", tmp],
            capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr


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

        no_grounding_check = pathlib.Path(tmp) / "grounding-check.json"
        no_grounding_check.write_text(json.dumps({
            "candidates": [{"id": "candidate_0"}],
            "presented_text": "A. Type your own rule.\nB. Skip for now.",
        }), encoding="utf-8")

        for args in (
            ["--event", "question_presented", "--mode", "plain_text",
             "--surface-source", "validated_dynamic", "--surface-digest", SURFACE_DIGEST],
            ["--event", "answers_received"],
            ["--event", "artifact_generated", "--stage", "preview", "--artifact-path", "/tmp/p.md"],
            ["--event", "card_presented", "--stage", "preview", "--mode", "markdown_inline"],
            ["--event", "rule_choice_presented", "--mode", "plain_text",
             "--grounding-check-file", str(no_grounding_check)],
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

        # Every persisted row is stamped with a UTC ts at write time (#236).
        written = [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
        assert all(ux_receipt.TS_PATTERN.fullmatch(value.get("ts", "")) for value in written), written

        nomode = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "rule_choice_presented"],
            capture_output=True, text=True,
        )
        assert nomode.returncode == 2 and "requires --mode" in nomode.stderr


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
        "answers_received",
        "rule_choice_presented",
        "stamped with a UTC ISO-8601 `ts`",
        "--grounding-check-file",
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


def test_cli_state_root_defaults_to_trade_coach_home():
    # #269: one `export TRADE_COACH_HOME=...` must route this tool too —
    # omitting --state-root must not fall through to the real ~/.trade-coach.
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "TRADE_COACH_HOME": tmp}
        start = subprocess.run(
            [sys.executable, str(TOOL), "start", "--session-id", "isolated",
             "--client", "c", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True, env=env,
        )
        assert start.returncode == 0, start.stderr
        assert (pathlib.Path(tmp) / "ux" / "isolated.jsonl").is_file(), \
            "trace must land in TRADE_COACH_HOME when --state-root is omitted"


def test_cli_explicit_state_root_overrides_trade_coach_home():
    # Resolution order matches the engine CLIs: --state-root > TRADE_COACH_HOME.
    with tempfile.TemporaryDirectory() as explicit, tempfile.TemporaryDirectory() as env_root:
        env = {**os.environ, "TRADE_COACH_HOME": env_root}
        start = subprocess.run(
            [sys.executable, str(TOOL), "start", "--session-id", "explicit-wins",
             "--state-root", explicit,
             "--client", "c", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True, env=env,
        )
        assert start.returncode == 0, start.stderr
        assert (pathlib.Path(explicit) / "ux" / "explicit-wins.jsonl").is_file()
        assert not (pathlib.Path(env_root) / "ux" / "explicit-wins.jsonl").exists()


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} interaction trajectory tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
