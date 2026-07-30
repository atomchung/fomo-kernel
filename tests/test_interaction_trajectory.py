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
import re
import shlex
import subprocess
import sys
import tempfile

# The market must not be an input to these assertions (#620). Declared in
# tests/offline_posture.py so a direct `python3 tests/<this file>` run and a
# `run_all.py` run reach the same answer; TR_TEST_NETWORK=1 still opts in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills" / "fomo-kernel" / "tools" / "ux_receipt.py"
SPEC = ROOT / "skills" / "fomo-kernel" / "references" / "interaction-delivery.md"
# The runtime contract is two files: interaction-delivery.md states what must be
# presented, ux-receipt.md states how to record that it was. The mirror check
# below spans both, so splitting the file cannot drop a clause. Keep them listed
# in test_doc_language.AGENT_RUNTIME_SURFACES so neither can vanish silently.
RECEIPT_SPEC = ROOT / "skills" / "fomo-kernel" / "references" / "ux-receipt.md"
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
        "adapter": "plain_text",
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
        row("cash_anchor_checked", cash_outcome="found_in_source"),
        row("artifact_generated", stage="preview", artifact_path="/tmp/card-private-preview.md"),
        row("card_presented", stage="preview", mode="markdown_inline"),
        row("artifact_generated", stage="final", artifact_path="/tmp/card-private.md"),
        row("card_presented", stage="final", mode="markdown_inline"),
    ]


# --- Semantic access to a trace (#360) ----------------------------------------
#
# Tests below reach into a fixture by *what a row is*, never by where it
# happens to sit. #357 is the receipt: adding one `cash_anchor_checked` row to
# `good_markdown_rows()` shifted every downstream index and broke roughly ten
# tests in this file plus one in `test_question_surfaces.py` — none of them
# about cash. Raw integers keep the fixture's shape in the reader's head and in
# trailing comments, which is where it drifts: the latency-marker test carried
# `# after the preview card` on an insertion that put the row *before* it.
#
# `locate` raises on an ambiguous or absent anchor rather than picking one.
# Silently mutating the wrong row is the failure this whole change exists to
# stop, and a fixture that has grown a second matching row should say so.

DECLARATION = ("capabilities_declared", {})
CASH_ANCHOR = ("cash_anchor_checked", {})
PREVIEW_ARTIFACT = ("artifact_generated", {"stage": "preview"})
PREVIEW_CARD = ("card_presented", {"stage": "preview"})
FINAL_ARTIFACT = ("artifact_generated", {"stage": "final"})
FINAL_CARD = ("card_presented", {"stage": "final"})
WEEKLY_OPENER = ("memory_presented", {})
QUESTION = ("question_presented", {})
RULE_CHOICE = ("rule_choice_presented", {})
CHANGE_DIFF = ("change_presented", {"change_kind": "diff"})
CHANGE_RESULT = ("change_presented", {"change_kind": "result"})
EVALUATION = ("evaluation_presented", {})
RESOLUTION = ("resolution_presented", {})
VERDICT = ("owner_verdict", {})


def locate(rows, anchor):
    """Index of the one row an anchor names."""
    event, match = anchor
    hits = [index for index, value in enumerate(rows)
            if value.get("event") == event
            and all(value.get(key) == want for key, want in match.items())]
    assert len(hits) == 1, f"anchor {anchor} matched {len(hits)} rows, expected exactly one"
    return hits[0]


def at(rows, anchor):
    """The row itself, for in-place field edits."""
    return rows[locate(rows, anchor)]


def before(rows, anchor, new_row):
    rows.insert(locate(rows, anchor), new_row)
    return rows


def after(rows, anchor, new_row):
    rows.insert(locate(rows, anchor) + 1, new_row)
    return rows


def drop(rows, anchor):
    del rows[locate(rows, anchor)]
    return rows


def swap(rows, first, second):
    a, b = locate(rows, first), locate(rows, second)
    rows[a], rows[b] = rows[b], rows[a]
    return rows


def redeclare(rows, **overrides):
    """Replace the capability declaration, keeping its position."""
    rows[locate(rows, DECLARATION)] = declaration(**overrides)
    return rows


def weekly_rows():
    rows = redeclare(good_markdown_rows(), route="weekly_review")
    return after(rows, DECLARATION, row("memory_presented", memory_kind="prior_commitment"))


def owner_rows():
    rows = good_markdown_rows()
    before(rows, PREVIEW_ARTIFACT, row("question_presented", mode="plain_text"))
    before(rows, PREVIEW_ARTIFACT, row("answers_received"))
    # #293 (merged after this fixture was authored): rule_choice_presented now
    # requires machine-checked grounding-fidelity evidence. No candidate in
    # this synthetic fixture carries an engine grounding, so the trivially
    # satisfied state applies (mirrors _grounding_fidelity's no-candidates path).
    after(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text",
                                  grounding_expected=False, grounding_verbatim=True))
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    return rows


def refresh_rows():
    """A book refresh walked honestly: what differed, what was asked, what was
    recorded, and a verdict on the surface the user actually saw.

    No card and no preview/final staging appear anywhere in it, because the
    flow renders none (`flows/book-refresh.md`).
    """
    return [
        declaration(route="refresh"),
        row("change_presented", change_kind="diff"),
        row("question_presented", mode="plain_text"),
        row("answers_received"),
        row("change_presented", change_kind="result"),
        row("findings_recorded", findings=[]),
        row("owner_verdict", controls="pass", card="not_applicable",
            memory="not_applicable", change="pass"),
    ]


# Entirely synthetic, but shaped exactly like a live `consider` emission
# (topics, value types, a rule collision carrying its own text, a
# dot-carrying ticker excluded from the denominator, the user's own words).
# Fields the machine half checks are all represented, so the fidelity tests
# below exercise every branch of `_challenge_fidelity` against one payload.
SYNTHETIC_CHALLENGE = {
    "must_state": [
        {"topic": "basis", "value": "ledger", "anchor": "basis.source"},
        {"topic": "basis", "value": 9, "anchor": "basis.stale_days"},
        {"topic": "position", "value": 0.34344827586206894,
         "anchor": "consequence.after.weights.SYNTH"},
        {"topic": "concentration", "value": 1.0, "anchor": "consequence.after.ai_pct"},
        {"topic": "concentration", "value": True,
         "anchor": "consequence.after.oversize_triggered"},
        {"topic": "cash", "value": -39200.0, "anchor": "consequence.after.cash.balance"},
        {"topic": "rule_collision", "value": "would_breach",
         "anchor": "rule_collisions.rule-1.state",
         "detail": {"rule_id": "rule-1",
                    "text": "Single position must stay under a fifth of the book",
                    "worsens": None}},
        {"topic": "disclosure", "value": "cost_basis",
         "anchor": "consequence.disclosures.0"},
        {"topic": "excluded_holding", "value": "9999.TT",
         "detail": {"reason": "unavailable_cost"}},
    ],
    "quote_verbatim": [
        {"field": "reason", "text": "It is still my highest-conviction synthetic name."},
        {"field": "why_now", "text": "The synthetic supplier raised guidance this morning."},
    ],
    "unchecked": ["liquidity", "valuation", "tax", "position_fit", "evidence_delta"],
    "case_required": {"for": 1, "against": 1},
    "required_coverage": [
        {"path": "consequence.disclosures.0", "owes": "disclosure", "key": "cost_basis"},
    ],
}

# An answer that carries everything the machine half can check: the weight at
# display precision, the ×100 percent form, the magnitude of the negative cash
# balance with a thousands separator, the rule's own text, the dotted ticker,
# and the user's sentences verbatim.
SYNTHETIC_ANSWER = (
    "On your recorded book this takes SYNTH to 34.3% and AI exposure to 100%, "
    "leaving cash 39,200 overdrawn. It would break your own rule — \"Single "
    "position must stay under a fifth of the book\". 9999.TT has no usable "
    "cost and sits outside every percentage here. You said: \"It is still my "
    "highest-conviction synthetic name.\" and \"The synthetic supplier raised "
    "guidance this morning.\" Liquidity, valuation, tax, whether the position "
    "still fits you, and whether that is genuinely new information were not "
    "checked."
)


def evaluation_row(**overrides):
    """An `evaluation_presented` row as `_challenge_fidelity` would emit it
    for SYNTHETIC_CHALLENGE/SYNTHETIC_ANSWER: five machine-checkable facts
    (two numeric, the cash magnitude, the rule text, the ticker), all found.
    """
    value = {
        "challenge_hash": "b" * 64,
        "quotes_expected": True,
        "quotes_verbatim": True,
        "facts_checked": 5,
        "facts_missing": 0,
        "must_state_total": 9,
        "unchecked_total": 5,
    }
    value.update(overrides)
    return row("evaluation_presented", **value)


def consider_rows():
    """A pre-trade evaluation walked honestly (#544 Slice B): one bounded
    context question, the inline challenge delivery with its fidelity
    evidence, the resolution invitation, and the four route-specific owner
    judgments. No card, no change surface — the route has neither.
    """
    return [
        declaration(route="consider"),
        row("question_presented", mode="plain_text"),
        row("answers_received"),
        evaluation_row(),
        row("resolution_presented", workflow_state="open"),
        row("findings_recorded", findings=[]),
        row("owner_verdict", controls="pass", card="not_applicable",
            memory="not_applicable", comprehension="pass", usefulness="pass",
            friction="pass", resolution="pass"),
    ]


def stamp(rows, timestamps):
    """Positional on purpose — the timing tests assert on the *sequence*.

    This is the one coupling to trace length that #360 leaves in place: a
    timing test's meaning is the gaps between consecutive stamps, which no
    anchor can express. When a fixture grows a row, four tests fail here with
    the message below rather than silently stamping the wrong events.
    """
    assert len(rows) == len(timestamps), (
        f"trace has {len(rows)} rows and {len(timestamps)} timestamps — a fixture "
        "grew a row; extend this test's timestamp list to match")
    for value, timestamp in zip(rows, timestamps):
        value["ts"] = timestamp
    return rows


def assert_has(errors, fragment):
    assert any(fragment in error for error in errors), errors


# --- Happy paths --------------------------------------------------------------

def test_unknown_host_defaults_to_a_first_class_text_fallback():
    # No optional adapter is declared or failed: plain text is the normal
    # unknown-host route, rather than a degraded widget delivery.
    assert at(good_markdown_rows(), DECLARATION)["adapter"] == "plain_text"
    assert ux_receipt.verify_rows(good_markdown_rows()) == []


def test_native_controls_and_widget_pass():
    rows = good_markdown_rows()
    redeclare(rows, adapter="validated_widget",
              question_modes=["plain_text", "native_options"],
              card_modes=["markdown_inline", "widget"])
    before(rows, PREVIEW_ARTIFACT, row("question_presented", mode="native_options",
                                       surface_source="validated_dynamic",
                                       surface_digest=SURFACE_DIGEST))
    at(rows, PREVIEW_CARD)["mode"] = "widget"
    at(rows, FINAL_CARD)["mode"] = "widget"
    assert ux_receipt.verify_rows(rows) == []


def test_question_surface_trace_is_content_free():
    rows = good_markdown_rows()
    before(rows, PREVIEW_ARTIFACT, row("question_presented", mode="plain_text",
                                       surface_source="engine_fallback",
                                       surface_digest=SURFACE_DIGEST))
    assert ux_receipt.verify_rows(rows) == []

    leaked = [dict(value) for value in rows]
    at(leaked, QUESTION)["stem"] = "private trade wording"
    assert_has(ux_receipt.verify_rows(leaked), "question trace contains content fields")

    missing = [dict(value) for value in rows]
    at(missing, QUESTION).pop("surface_digest")
    assert_has(ux_receipt.verify_rows(missing), "source and digest must appear together")

    invalid = [dict(value) for value in rows]
    at(invalid, QUESTION)["surface_digest"] = "not-a-digest"
    assert_has(ux_receipt.verify_rows(invalid), "invalid surface digest")


def test_weekly_opening_memory_passes():
    assert ux_receipt.verify_rows(weekly_rows()) == []


# --- Presentation is not artifact generation ---------------------------------

def test_generated_without_presented_fails():
    rows = good_markdown_rows()
    drop(rows, PREVIEW_CARD)  # keep its artifact
    assert_has(ux_receipt.verify_rows(rows), "preview card_presented must appear exactly once")


def test_card_marked_presented_before_artifact_fails():
    rows = good_markdown_rows()
    swap(rows, PREVIEW_ARTIFACT, PREVIEW_CARD)  # card before its artifact
    assert_has(ux_receipt.verify_rows(rows), "before its artifact existed")


def test_final_card_before_preview_card_fails():
    # Move the whole final pair ahead of the preview pair, artifacts included,
    # so the only defect under test is the order the user saw the cards in.
    rows = good_markdown_rows()
    final = [at(rows, FINAL_ARTIFACT), at(rows, FINAL_CARD)]
    drop(rows, FINAL_CARD)
    drop(rows, FINAL_ARTIFACT)
    for value in reversed(final):
        before(rows, PREVIEW_ARTIFACT, value)
    assert_has(ux_receipt.verify_rows(rows), "final card presentation must follow the preview card")


# --- Widget degradation must be explicit -------------------------------------

def test_declared_widget_silent_markdown_fails():
    rows = good_markdown_rows()
    redeclare(rows, adapter="validated_widget",
              question_modes=["plain_text", "native_options"],
              card_modes=["markdown_inline", "widget"])
    assert_has(ux_receipt.verify_rows(rows), "without recording a failed widget attempt")


def test_declared_widget_with_recorded_failure_passes():
    rows = good_markdown_rows()
    redeclare(rows, adapter="validated_widget",
              question_modes=["plain_text", "native_options"],
              card_modes=["markdown_inline", "widget"])
    after(rows, DECLARATION, row("widget_attempt_failed", stage="preview"))
    assert ux_receipt.verify_rows(rows) == []


def test_widget_failure_without_capability_fails():
    rows = good_markdown_rows()
    after(rows, DECLARATION, row("widget_attempt_failed", stage="preview"))
    assert_has(ux_receipt.verify_rows(rows), "without declared widget capability")


# --- Capability / mode declarations ------------------------------------------

def test_missing_universal_fallbacks_fail():
    rows = good_markdown_rows()
    redeclare(rows, adapter="validated_widget",
              question_modes=["native_options"], card_modes=["widget"])
    errors = ux_receipt.verify_rows(rows)
    assert_has(errors, "plain_text as the universal question fallback")
    assert_has(errors, "markdown_inline as the universal card fallback")


def test_undeclared_card_mode_fails():
    rows = good_markdown_rows()
    at(rows, PREVIEW_CARD)["mode"] = "widget"  # not declared
    assert_has(ux_receipt.verify_rows(rows), "undeclared mode")


def test_undeclared_question_mode_fails():
    rows = good_markdown_rows()
    after(rows, DECLARATION, row("question_presented", mode="native_options"))  # not declared
    assert_has(ux_receipt.verify_rows(rows), "question used undeclared mode")


def test_adapter_profile_rejects_unverified_capability_claims():
    rows = good_markdown_rows()
    redeclare(rows, adapter="plain_text",
              question_modes=["plain_text", "native_options"],
              card_modes=["markdown_inline"])
    assert_has(ux_receipt.verify_rows(rows), "plain_text adapter may declare only")

    rows = good_markdown_rows()
    redeclare(rows, adapter="validated_widget",
              question_modes=["plain_text", "native_options"],
              card_modes=["markdown_inline"])
    assert_has(ux_receipt.verify_rows(rows), "requires card modes")


def test_legacy_trace_without_adapter_still_verifies():
    rows = good_markdown_rows()
    at(rows, DECLARATION).pop("adapter")
    assert ux_receipt.verify_rows(rows) == []


# --- Latency markers: answers_received / rule_choice_presented (#236, #230) ---

def test_latency_marker_events_pass_without_ordering_rules():
    rows = good_markdown_rows()
    after(rows, DECLARATION, row("answers_received"))
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text",
                                   grounding_expected=False, grounding_verbatim=True))
    assert ux_receipt.verify_rows(rows) == []
    # Deliberately no ordering rules for the markers: they verify wherever they
    # appear, including before the weekly opener.
    weekly = weekly_rows()
    after(weekly, DECLARATION, row("answers_received"))
    assert ux_receipt.verify_rows(weekly) == []


def test_answers_received_rejects_extra_fields():
    rows = good_markdown_rows()
    after(rows, DECLARATION, row("answers_received", note="private wording"))
    assert_has(ux_receipt.verify_rows(rows), "answers_received contains unsupported fields")


def test_rule_choice_rejects_extra_fields():
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text", options="A/B/C"))
    assert_has(ux_receipt.verify_rows(rows), "rule_choice_presented contains unsupported fields")


def test_rule_choice_undeclared_mode_fails():
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD,
           row("rule_choice_presented", mode="native_options"))  # only plain_text declared
    assert_has(ux_receipt.verify_rows(rows), "rule choice used undeclared mode")
    missing = good_markdown_rows()
    before(missing, PREVIEW_CARD,
           row("rule_choice_presented"))  # no mode at all fails closed
    assert_has(ux_receipt.verify_rows(missing), "rule choice used undeclared mode")


# --- Rule-choice grounding fidelity is machine-checked, not self-attested (#293) --

def test_rule_choice_faithful_grounding_passes():
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text",
                                   grounding_expected=True, grounding_hash=SURFACE_DIGEST,
                                   grounding_verbatim=True))
    assert ux_receipt.verify_rows(rows) == []


def test_rule_choice_no_grounding_expected_passes():
    # A candidate list where no candidate carried an engine grounding: nothing
    # to be verbatim about, so the trivial state must still pass.
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text",
                                   grounding_expected=False, grounding_verbatim=True))
    assert ux_receipt.verify_rows(rows) == []


def test_rule_choice_missing_grounding_evidence_fails_closed():
    # This is the #293 bug itself: an agent that records rule_choice_presented
    # without ever running the fidelity check. Unlike optional `ts`, there is
    # no legacy grandfather here — absence must fail exactly like a false
    # result, or an agent could silently keep doing what caused the issue.
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text"))
    errors = ux_receipt.verify_rows(rows)
    assert_has(errors, "missing grounding-fidelity evidence")
    assert_has(errors, "did not prove its candidates' grounding was presented verbatim")


def test_rule_choice_paraphrased_grounding_fails():
    # Reproduces the reported failure mode: engine grounding existed but the
    # presented text did not contain it verbatim (paraphrased/rewritten).
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text",
                                   grounding_expected=True, grounding_hash=SURFACE_DIGEST,
                                   grounding_verbatim=False))
    assert_has(
        ux_receipt.verify_rows(rows),
        "did not prove its candidates' grounding was presented verbatim",
    )


def test_rule_choice_expected_without_hash_fails():
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text",
                                   grounding_expected=True, grounding_verbatim=True))
    assert_has(ux_receipt.verify_rows(rows), "invalid or missing grounding_hash")


def test_rule_choice_hash_without_expectation_fails():
    # Defensive/consistency check: a hash with no expected grounding is a
    # contradictory row (hand-edited or corrupted), not a legitimate state.
    rows = good_markdown_rows()
    before(rows, PREVIEW_CARD, row("rule_choice_presented", mode="plain_text",
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
    at(mixed, PREVIEW_ARTIFACT)["ts"] = "2026-07-20T13:46:02Z"  # partially stamped passes too
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
        at(rows, CASH_ANCHOR)["ts"] = bad
        assert_has(ux_receipt.verify_rows(rows), "invalid ts")


def test_normal_owner_trace_has_credible_timing_integrity():
    rows = stamp(owner_rows(), [
        "2026-07-20T13:46:00Z",
        "2026-07-20T13:46:02Z",  # cash_anchor_checked
        "2026-07-20T13:46:05Z",
        "2026-07-20T13:46:12Z",
        "2026-07-20T13:46:15Z",
        "2026-07-20T13:46:17Z",
        "2026-07-20T13:46:22Z",
        "2026-07-20T13:46:31Z",
        "2026-07-20T13:46:34Z",
        "2026-07-20T13:46:40Z",
    ])
    integrity = ux_receipt.timing_integrity(rows)
    assert integrity["status"] == "credible"
    assert integrity["owner_live_eligible"] is True
    assert integrity["span_seconds"] == 40
    assert integrity["findings"] == []


def test_same_second_owner_trace_is_suspect_one_burst_backfill():
    rows = stamp(owner_rows(), ["2026-07-20T13:46:02Z"] * 10)
    integrity = ux_receipt.timing_integrity(rows)
    assert integrity["status"] == "suspect"
    assert integrity["owner_live_eligible"] is False
    assert integrity["span_seconds"] == 0
    assert [finding["code"] for finding in integrity["findings"]] == [
        "implausible_one_burst_backfill"
    ]


def test_reversed_owner_trace_timestamp_is_suspect():
    rows = stamp(owner_rows(), [
        "2026-07-20T13:46:00Z",
        "2026-07-20T13:46:02Z",  # cash_anchor_checked
        "2026-07-20T13:46:05Z",
        "2026-07-20T13:46:12Z",
        "2026-07-20T13:46:11Z",  # preview artifact timestamp reverses
        "2026-07-20T13:46:17Z",
        "2026-07-20T13:46:22Z",
        "2026-07-20T13:46:31Z",
        "2026-07-20T13:46:34Z",
        "2026-07-20T13:46:40Z",
    ])
    integrity = ux_receipt.timing_integrity(rows)
    assert integrity["status"] == "suspect"
    assert integrity["owner_live_eligible"] is False
    assert [finding["code"] for finding in integrity["findings"]] == [
        "timestamp_reversal"
    ]
    assert integrity["findings"][0] == {
        "code": "timestamp_reversal",
        "row": 5,
        "previous_row": 4,
    }


def test_legacy_owner_trace_without_ts_remains_compatible_and_not_assessed():
    rows = owner_rows()
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True) == []
    integrity = ux_receipt.timing_integrity(rows)
    assert integrity["status"] == "not_assessed"
    assert integrity["owner_live_eligible"] is None
    assert "legacy receipt remains compatible" in integrity["reason"]


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


def test_cli_warns_by_default_and_strict_timing_gate_fails_suspect_trace():
    with tempfile.TemporaryDirectory() as tmp:
        receipt = pathlib.Path(tmp) / "ux" / "burst.jsonl"
        receipt.parent.mkdir(parents=True)
        rows = stamp(owner_rows(), ["2026-07-20T13:46:02Z"] * 10)
        for value in rows:
            value["session_id"] = "burst"
        receipt.write_text("".join(json.dumps(value) + "\n" for value in rows), encoding="utf-8")

        common = ["--session-id", "burst", "--state-root", tmp, "--require-owner-verdict"]
        warned = subprocess.run(
            [sys.executable, str(TOOL), "verify", *common],
            capture_output=True, text=True,
        )
        assert warned.returncode == 0, warned.stderr
        assert "WARN: timing_integrity implausible_one_burst_backfill" in warned.stderr
        payload = json.loads(warned.stdout)
        assert payload["timing_integrity"]["status"] == "suspect"
        assert payload["timing_integrity"]["owner_live_eligible"] is False

        strict = subprocess.run(
            [sys.executable, str(TOOL), "verify", *common, "--require-timing-integrity"],
            capture_output=True, text=True,
        )
        assert strict.returncode == 1
        assert "cannot be used as owner_live UX ground truth" in strict.stderr


# --- Weekly opening memory ordering ------------------------------------------

def test_weekly_missing_opener_fails():
    rows = good_markdown_rows()
    redeclare(rows, route="weekly_review")
    assert_has(ux_receipt.verify_rows(rows), "exactly one prior commitment or skip opener")


def test_weekly_opener_after_first_card_fails():
    rows = good_markdown_rows()
    redeclare(rows, route="weekly_review")
    rows.append(row("memory_presented", memory_kind="prior_skip"))  # after both cards
    assert_has(ux_receipt.verify_rows(rows), "after the first question or card")


# --- Cash anchor pre-flight (#357) --------------------------------------------
# The cash anchor is resolved before the first surface (on first_review before
# `prepare` even runs; on weekly_review after the cadence-tier gate, since a
# light week is never asked and writes no receipt at all -- #357 owner ruling
# 2026-07-23), so this event is retrospective evidence the check happened at
# all -- an agent that forgot to check (the failure mode #357 was filed for)
# cannot fabricate it after the fact without also getting the ordering wrong.

def test_cash_anchor_checked_missing_fails_for_first_review():
    rows = good_markdown_rows()
    drop(rows, CASH_ANCHOR)
    assert_has(ux_receipt.verify_rows(rows), "first_review must record exactly one cash_anchor_checked event")


def test_cash_anchor_checked_missing_fails_for_weekly_review():
    rows = weekly_rows()
    drop(rows, CASH_ANCHOR)
    assert_has(ux_receipt.verify_rows(rows), "weekly_review must record exactly one cash_anchor_checked event")


def test_cash_anchor_checked_duplicate_fails():
    rows = good_markdown_rows()
    after(rows, CASH_ANCHOR, row("cash_anchor_checked", cash_outcome="asked_user"))
    assert_has(ux_receipt.verify_rows(rows), "must record exactly one cash_anchor_checked event")


def test_cash_anchor_checked_after_first_question_fails():
    rows = good_markdown_rows()
    drop(rows, CASH_ANCHOR)  # remove the pre-flight cash check...
    after(rows, DECLARATION, row("question_presented", mode="plain_text"))
    after(rows, QUESTION,
          row("cash_anchor_checked", cash_outcome="found_in_source"))  # ...and backfill it late
    assert_has(ux_receipt.verify_rows(rows), "cash_anchor_checked was recorded after the first question or card")


def test_cash_anchor_checked_invalid_outcome_fails():
    rows = good_markdown_rows()
    at(rows, CASH_ANCHOR)["cash_outcome"] = "assumed_zero"
    assert_has(ux_receipt.verify_rows(rows), "unsupported cash outcome")


def test_cash_anchor_checked_valid_outcomes_pass():
    for outcome in ux_receipt.CASH_OUTCOMES:
        rows = good_markdown_rows()
        at(rows, CASH_ANCHOR)["cash_outcome"] = outcome
        assert ux_receipt.verify_rows(rows) == []


def test_cash_anchor_checked_not_required_outside_trade_history_routes():
    # snapshot_review's own envelope states `cash` inline (or omits it) and
    # test_drive never persists an accounting anchor at all (references/
    # data-contract.md) -- neither route carries this requirement.
    for route in ("snapshot_review", "test_drive"):
        rows = good_markdown_rows()
        drop(rows, CASH_ANCHOR)
        redeclare(rows, route=route)
        assert ux_receipt.verify_rows(rows) == []


def test_cli_cash_anchor_checked_requires_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        common = ["--session-id", "session-357", "--state-root", tmp]
        subprocess.run(
            [sys.executable, str(TOOL), "start", *common,
             "--client", "codex-desktop", "--route", "first_review",
             "--question-mode", "plain_text", "--card-mode", "markdown_inline"],
            capture_output=True, text=True, check=True,
        )
        missing = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "cash_anchor_checked"],
            capture_output=True, text=True,
        )
        assert missing.returncode == 2
        assert "requires --cash-outcome" in missing.stderr

        done = subprocess.run(
            [sys.executable, str(TOOL), "event", *common,
             "--event", "cash_anchor_checked", "--cash-outcome", "skipped"],
            capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr


# --- The card-free lane: book refresh (#523) ----------------------------------
#
# A refresh renders no card by design, so before this route existed the card
# check could only ever fail it and the only way to pass was to fabricate card
# events — the exact dishonesty this tool exists to prevent. What each route
# owes now lives in one table (ux_receipt.ROUTE_CONTRACTS), and these probes
# cover both directions of it: the card-free lane must prove what it *did*
# show, and the card-producing routes must not have been quietly exempted along
# the way.

def test_a_refresh_trace_verifies_without_any_card():
    assert ux_receipt.verify_rows(refresh_rows()) == []
    assert ux_receipt.verify_rows(refresh_rows(), require_owner_verdict=True,
                                  require_findings=True) == []


def test_a_quiet_refresh_that_raised_nothing_still_has_a_change_surface():
    # Not every refresh raises a confirmation: an additive change is adopted
    # without ceremony. The diff and the result are still shown, and the
    # controls verdict must then say `not_applicable` rather than judge a
    # control nobody saw.
    rows = drop(drop(refresh_rows(), QUESTION), ("answers_received", {}))
    at(rows, VERDICT)["controls"] = "not_applicable"
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True,
                                  require_findings=True) == []


def test_a_refresh_claiming_a_card_fails():
    """The exemption forbids, it does not merely stop requiring.

    A route that only stopped *demanding* cards would accept a receipt claiming
    a delivery that structurally cannot have happened — the same fabricated
    evidence, volunteered instead of forced.
    """
    for claim in (row("artifact_generated", stage="final", artifact_path="/tmp/card.md"),
                  row("card_presented", stage="final", mode="markdown_inline"),
                  row("widget_attempt_failed", stage="preview"),
                  row("rule_choice_presented", mode="plain_text",
                      grounding_expected=False, grounding_verbatim=True)):
        rows = refresh_rows()
        before(rows, VERDICT, claim)
        assert_has(ux_receipt.verify_rows(rows), "record a card delivery that cannot have happened")


def test_a_refresh_with_no_visible_change_surface_fails():
    """A start row and a verdict prove nothing about the lane they claim."""
    bare = refresh_rows()
    for anchor in (CHANGE_DIFF, CHANGE_RESULT, QUESTION, ("answers_received", {})):
        drop(bare, anchor)
    at(bare, VERDICT)["controls"] = "not_applicable"
    assert_has(ux_receipt.verify_rows(bare), "must show at least one visible change surface")
    # Any one of the three surfaces is enough, per #523's stated contract: the
    # narrated diff, the recorded result, or the confirmation question. (The
    # question also brings a control back, so it brings its verdict with it.)
    for restored, controls in (
            (row("change_presented", change_kind="diff"), "not_applicable"),
            (row("change_presented", change_kind="result"), "not_applicable"),
            (row("question_presented", mode="plain_text"), "pass")):
        one = before([dict(value) for value in bare], VERDICT, restored)
        at(one, VERDICT)["controls"] = controls
        assert ux_receipt.verify_rows(one) == [], restored


def test_change_presented_is_content_free_and_typed():
    rows = refresh_rows()
    at(rows, CHANGE_DIFF)["change_kind"] = "guessed_a_cause"
    assert_has(ux_receipt.verify_rows(rows), "unsupported change kind")
    rows = refresh_rows()
    at(rows, CHANGE_DIFF)["disappeared"] = "ACME 120 shares"
    assert_has(ux_receipt.verify_rows(rows),
               "change_presented contains unsupported fields: disappeared")


def test_a_card_route_may_not_borrow_the_change_surface():
    # Symmetry with the rule above: `change_presented` on a route that renders
    # cards would let a review skip proving its card by pointing at something
    # else it showed.
    rows = good_markdown_rows()
    before(rows, PREVIEW_ARTIFACT, row("change_presented", change_kind="diff"))
    assert_has(ux_receipt.verify_rows(rows), "change_presented does not belong on this route")


def test_the_refresh_verdict_judges_the_change_and_not_a_card():
    rows = refresh_rows()
    at(rows, VERDICT)["card"] = "pass"
    assert_has(ux_receipt.verify_rows(rows), "owner card verdict must be not_applicable")

    rows = refresh_rows()
    del at(rows, VERDICT)["change"]
    assert_has(ux_receipt.verify_rows(rows), "owner change verdict must be pass or fail")

    rows = refresh_rows()
    at(rows, VERDICT)["change"] = "fail"
    assert_has(ux_receipt.verify_rows(rows, require_owner_verdict=True), "requires change=pass")

    # And the axis does not exist on a route that renders a card.
    rows = good_markdown_rows()
    rows.append(row("owner_verdict", controls="pass", card="pass",
                    memory="not_applicable", change="pass"))
    assert_has(ux_receipt.verify_rows(rows), "owner change verdict does not apply")


def test_the_controls_verdict_follows_what_the_refresh_actually_asked():
    # Both directions, because both are fabrications: judging a control that
    # never appeared, and waving away one that did.
    rows = refresh_rows()
    at(rows, VERDICT)["controls"] = "not_applicable"
    assert_has(ux_receipt.verify_rows(rows), "owner controls verdict must be pass or fail")

    rows = drop(drop(refresh_rows(), QUESTION), ("answers_received", {}))
    assert_has(ux_receipt.verify_rows(rows), "owner controls verdict must be not_applicable")


def test_a_refresh_verdict_recorded_before_the_change_surface_fails():
    # The same anti-backfill rule the final card carries: the verdict is the
    # last act, so one recorded before the surface it judges judged nothing.
    # Only the result moves, so the findings-ordering rule stays satisfied and
    # this probe fails for exactly one reason.
    rows = refresh_rows()
    result = at(rows, CHANGE_RESULT)
    drop(rows, CHANGE_RESULT)
    rows.append(result)
    assert_has(ux_receipt.verify_rows(rows), "must follow the change surface it judges")


def test_a_refresh_trace_can_reach_credible_timing_integrity():
    """Without this, `--require-timing-integrity` would refuse the lane forever.

    `qa_env.sh` archives human-graded runs with that flag, so a route whose
    structural completeness is unreachable is a route that cannot be archived
    at all — which is what #486's refresh rows were blocked on.
    """
    # Structural completeness for this lane is its change surface, not a card
    # walk it can never have.
    surfaceless = [value for value in refresh_rows()
                   if value.get("event") not in ("change_presented", "question_presented")]
    incomplete = ux_receipt.timing_integrity(surfaceless)
    assert incomplete["status"] == "not_assessed"
    assert "change surface" in incomplete["reason"], incomplete

    # Unstamped but complete stays compatible, exactly like a legacy receipt.
    stale = ux_receipt.timing_integrity(refresh_rows())
    assert stale["status"] == "not_assessed"
    assert "legacy receipt remains compatible" in stale["reason"], stale

    integrity = ux_receipt.timing_integrity(stamp(refresh_rows(), [
        "2026-07-28T09:15:00Z",
        "2026-07-28T09:15:08Z",
        "2026-07-28T09:15:20Z",
        "2026-07-28T09:16:04Z",
        "2026-07-28T09:16:11Z",
        "2026-07-28T09:16:30Z",
        "2026-07-28T09:16:44Z",
    ]))
    assert integrity["status"] == "credible", integrity
    assert integrity["owner_live_eligible"] is True
    assert integrity["span_seconds"] == 104


def test_cli_archive_gates_accept_a_refresh_receipt_end_to_end():
    """The gate set `qa/qa_env.sh` applies at archive time, run for real."""
    with tempfile.TemporaryDirectory() as tmp:
        receipt = pathlib.Path(tmp) / "ux" / "refresh-9f2c.jsonl"
        receipt.parent.mkdir(parents=True)
        rows = stamp(refresh_rows(), [
            "2026-07-28T09:15:00Z", "2026-07-28T09:15:08Z", "2026-07-28T09:15:20Z",
            "2026-07-28T09:16:04Z", "2026-07-28T09:16:11Z", "2026-07-28T09:16:30Z",
            "2026-07-28T09:16:44Z",
        ])
        for value in rows:
            value["session_id"] = "refresh-9f2c"
        receipt.write_text("".join(json.dumps(value) + "\n" for value in rows), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(TOOL), "verify", "--session-id", "refresh-9f2c",
             "--state-root", tmp, "--require-owner-verdict", "--require-timing-integrity",
             "--require-findings"],
            capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout)["timing_integrity"]["status"] == "credible"


def test_cli_writes_and_verifies_a_refresh_trace():
    with tempfile.TemporaryDirectory() as tmp:
        common = ["--session-id", "refresh-cli", "--state-root", tmp]
        start = subprocess.run(
            [sys.executable, str(TOOL), "start", *common, "--client", "codex-desktop",
             "--route", "refresh"],
            capture_output=True, text=True)
        assert start.returncode == 0, start.stderr
        missing = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "change_presented"],
            capture_output=True, text=True)
        assert missing.returncode == 2 and "requires --change-kind" in missing.stderr
        for kind in ux_receipt.CHANGE_KINDS:
            done = subprocess.run(
                [sys.executable, str(TOOL), "event", *common, "--event", "change_presented",
                 "--change-kind", kind],
                capture_output=True, text=True)
            assert done.returncode == 0, done.stderr
        verdict = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "owner_verdict",
             "--controls", "not_applicable", "--card", "not_applicable",
             "--memory", "not_applicable", "--change", "pass"],
            capture_output=True, text=True)
        assert verdict.returncode == 0, verdict.stderr
        verified = subprocess.run(
            [sys.executable, str(TOOL), "verify", *common, "--require-owner-verdict"],
            capture_output=True, text=True)
        assert verified.returncode == 0, verified.stderr


def test_card_routes_still_owe_their_cards_after_the_refresh_exemption():
    """The regression guard: the exemption must not have leaked one route over.

    This is why the refresh lane could not reuse `snapshot_review` — exempting
    that route from the card check would have disabled it for genuine snapshot
    reviews, which do present cards (#523 owner ruling). So every route the
    table says renders cards must still fail without them.
    """
    carded = sorted(route for route, contract in ux_receipt.ROUTE_CONTRACTS.items()
                    if contract["cards"])
    assert carded == ["first_review", "snapshot_review", "test_drive", "weekly_review"]
    for route in carded:
        rows = redeclare(good_markdown_rows(), route=route)
        if route == "weekly_review":
            after(rows, DECLARATION, row("memory_presented", memory_kind="prior_commitment"))
        assert ux_receipt.verify_rows(rows) == [], route
        stripped = [value for value in rows
                    if value.get("event") not in ("artifact_generated", "card_presented")]
        errors = ux_receipt.verify_rows(stripped)
        for stage in ux_receipt.STAGES:
            assert_has(errors, f"{stage} card_presented must appear exactly once")
            assert_has(errors, f"{stage} artifact_generated must appear exactly once")


# --- The pre-trade lane: consider (#544 Slice B) -------------------------------
#
# `review.py consider` renders no card and mutates no book; its entire product
# surface is one inline textual answer that must carry the engine-declared
# challenge (#479), plus one resolution invitation. Before this route existed a
# successful consider call was exploratory evidence only — a stored JSON row
# could be mistaken for delivery, and an agent could drop `must_state` or
# `unchecked` entries after the engine produced them with nothing going red.
# These probes hold both halves: the trace must prove the presentation pair
# happened in order with machine-computed fidelity evidence, and every other
# route must be refused the same events.

def test_a_consider_trace_verifies_without_any_card():
    assert ux_receipt.verify_rows(consider_rows()) == []
    assert ux_receipt.verify_rows(consider_rows(), require_owner_verdict=True,
                                  require_findings=True) == []


def test_a_quiet_consider_that_asked_nothing_still_verifies():
    # Context questions are bounded, not owed: a premise the user stated fully
    # needs none. The controls verdict must then say `not_applicable` rather
    # than judge a control nobody saw.
    rows = drop(drop(consider_rows(), QUESTION), ("answers_received", {}))
    at(rows, VERDICT)["controls"] = "not_applicable"
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True,
                                  require_findings=True) == []


def test_a_consider_claiming_a_card_fails():
    """The #523 rule again: a card-free lane forbids, it does not exempt."""
    for claim in (row("artifact_generated", stage="final", artifact_path="/tmp/card.md"),
                  row("card_presented", stage="final", mode="markdown_inline"),
                  row("widget_attempt_failed", stage="preview"),
                  row("rule_choice_presented", mode="plain_text",
                      grounding_expected=False, grounding_verbatim=True)):
        rows = consider_rows()
        before(rows, VERDICT, claim)
        assert_has(ux_receipt.verify_rows(rows), "record a card delivery that cannot have happened")


def test_a_consider_may_not_borrow_the_change_surface():
    # `consider` never mutates the book, so a change surface here would claim
    # a recording that cannot have happened.
    rows = consider_rows()
    before(rows, VERDICT, row("change_presented", change_kind="result"))
    assert_has(ux_receipt.verify_rows(rows), "change_presented does not belong on this route")


def test_a_consider_without_its_presentation_pair_fails():
    """A declaration and a verdict prove nothing about the answer's delivery."""
    rows = drop(consider_rows(), EVALUATION)
    assert_has(ux_receipt.verify_rows(rows), "must record exactly one evaluation_presented")

    rows = drop(consider_rows(), RESOLUTION)
    assert_has(ux_receipt.verify_rows(rows), "must record exactly one resolution_presented")

    doubled = consider_rows()
    after(doubled, EVALUATION, evaluation_row())
    assert_has(ux_receipt.verify_rows(doubled), "exactly one evaluation_presented")


def test_the_resolution_invitation_must_follow_the_evaluation():
    rows = swap(consider_rows(), EVALUATION, RESOLUTION)
    assert_has(ux_receipt.verify_rows(rows),
               "resolution invitation must follow the evaluation presentation")


def test_no_other_route_may_claim_an_evaluation_delivery():
    # Symmetry: an evaluation claimed on a route that never presents one is
    # the same fabricated evidence a card claim on a refresh is.
    rows = good_markdown_rows()
    before(rows, PREVIEW_ARTIFACT, evaluation_row())
    assert_has(ux_receipt.verify_rows(rows),
               "presents no trade evaluation, so rows")

    rows = refresh_rows()
    before(rows, VERDICT, row("resolution_presented", workflow_state="open"))
    assert_has(ux_receipt.verify_rows(rows),
               "presents no trade evaluation, so rows")


def test_evaluation_presented_is_fail_closed_on_its_evidence():
    """No legacy state: the route and the event were born together (#544),
    so absent, malformed, or failing fidelity evidence all refuse the trace —
    the same no-grandfathering rule #293's grounding evidence holds."""
    rows = consider_rows()
    at(rows, EVALUATION)["presented_answer"] = "the answer text itself"
    assert_has(ux_receipt.verify_rows(rows),
               "evaluation_presented contains unsupported fields: presented_answer")

    rows = consider_rows()
    del at(rows, EVALUATION)["challenge_hash"]
    assert_has(ux_receipt.verify_rows(rows), "invalid or missing challenge_hash")

    rows = consider_rows()
    at(rows, EVALUATION)["challenge_hash"] = "not-a-sha"
    assert_has(ux_receipt.verify_rows(rows), "invalid or missing challenge_hash")

    rows = consider_rows()
    del at(rows, EVALUATION)["quotes_expected"]
    assert_has(ux_receipt.verify_rows(rows), "missing quote-fidelity evidence")

    rows = consider_rows()
    at(rows, EVALUATION)["quotes_verbatim"] = False
    assert_has(ux_receipt.verify_rows(rows),
               "did not prove the user's own words were reproduced verbatim")

    rows = consider_rows()
    at(rows, EVALUATION).update(facts_missing=2)
    assert_has(ux_receipt.verify_rows(rows),
               "machine-checkable fact(s) the presented answer did not carry")

    rows = consider_rows()
    at(rows, EVALUATION)["facts_checked"] = -1
    assert_has(ux_receipt.verify_rows(rows), "facts_checked must be a non-negative integer")


def test_resolution_presented_is_typed_and_content_free():
    rows = consider_rows()
    at(rows, RESOLUTION)["workflow_state"] = "executed"
    assert_has(ux_receipt.verify_rows(rows), "unsupported workflow state")

    rows = consider_rows()
    at(rows, RESOLUTION)["decision_reason"] = "took profits"
    assert_has(ux_receipt.verify_rows(rows),
               "resolution_presented contains unsupported fields: decision_reason")

    for state in ux_receipt.WORKFLOW_STATES:
        rows = consider_rows()
        at(rows, RESOLUTION)["workflow_state"] = state
        assert ux_receipt.verify_rows(rows) == [], state


def test_the_consider_verdict_judges_the_evaluation_and_not_a_card():
    rows = consider_rows()
    at(rows, VERDICT)["card"] = "pass"
    assert_has(ux_receipt.verify_rows(rows), "owner card verdict must be not_applicable")

    # Each of the four route-specific axes is owed, and `not_applicable` is
    # not on their scale — an unjudged axis is an unjudged M1 question.
    for axis in ("comprehension", "usefulness", "friction", "resolution"):
        rows = consider_rows()
        del at(rows, VERDICT)[axis]
        assert_has(ux_receipt.verify_rows(rows),
                   f"owner {axis} verdict must be pass or fail")

        rows = consider_rows()
        at(rows, VERDICT)[axis] = "fail"
        assert_has(ux_receipt.verify_rows(rows, require_owner_verdict=True),
                   f"requires {axis}=pass")

    # The change axis does not exist here, and the consider axes do not exist
    # on a route with a card to judge instead.
    rows = consider_rows()
    at(rows, VERDICT)["change"] = "pass"
    assert_has(ux_receipt.verify_rows(rows), "owner change verdict does not apply")

    rows = good_markdown_rows()
    rows.append(row("owner_verdict", controls="pass", card="pass",
                    memory="not_applicable", comprehension="pass"))
    assert_has(ux_receipt.verify_rows(rows), "owner comprehension verdict does not apply")


def test_the_controls_verdict_follows_what_the_consider_actually_asked():
    rows = consider_rows()
    at(rows, VERDICT)["controls"] = "not_applicable"
    assert_has(ux_receipt.verify_rows(rows), "owner controls verdict must be pass or fail")

    rows = drop(drop(consider_rows(), QUESTION), ("answers_received", {}))
    assert_has(ux_receipt.verify_rows(rows), "owner controls verdict must be not_applicable")


def test_a_consider_verdict_recorded_before_its_surfaces_fails():
    # The verdict is the last act. Only the resolution moves, so the
    # findings-ordering rule stays satisfied and this fails for one reason.
    rows = consider_rows()
    invitation = at(rows, RESOLUTION)
    drop(rows, RESOLUTION)
    rows.append(invitation)
    assert_has(ux_receipt.verify_rows(rows),
               "owner_verdict must follow the evaluation surface it judges")


def test_a_consider_trace_can_reach_credible_timing_integrity():
    """Same stake as the refresh probe: a lane whose structural completeness
    is unreachable can never be archived under --require-timing-integrity."""
    pairless = [value for value in consider_rows()
                if value.get("event") not in ("evaluation_presented",
                                              "resolution_presented")]
    incomplete = ux_receipt.timing_integrity(pairless)
    assert incomplete["status"] == "not_assessed"
    assert "evaluation presentation" in incomplete["reason"], incomplete

    stale = ux_receipt.timing_integrity(consider_rows())
    assert stale["status"] == "not_assessed"
    assert "legacy receipt remains compatible" in stale["reason"], stale

    integrity = ux_receipt.timing_integrity(stamp(consider_rows(), [
        "2026-07-30T10:02:00Z",
        "2026-07-30T10:02:12Z",
        "2026-07-30T10:02:41Z",
        "2026-07-30T10:03:05Z",
        "2026-07-30T10:03:30Z",
        "2026-07-30T10:03:52Z",
        "2026-07-30T10:04:06Z",
    ]))
    assert integrity["status"] == "credible", integrity
    assert integrity["owner_live_eligible"] is True
    assert integrity["span_seconds"] == 126


def challenge_check_payload(**overrides):
    payload = {"challenge": json.loads(json.dumps(SYNTHETIC_CHALLENGE)),
               "presented_text": SYNTHETIC_ANSWER}
    payload.update(overrides)
    return payload


def fidelity_of(payload):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        path = handle.name
    try:
        return ux_receipt._challenge_fidelity(path)
    finally:
        os.unlink(path)


def test_challenge_fidelity_finds_every_machine_checkable_fact():
    evidence = fidelity_of(challenge_check_payload())
    assert evidence["quotes_expected"] is True
    assert evidence["quotes_verbatim"] is True
    # Two fraction-shaped numbers, the cash magnitude, the rule's own text,
    # and the dotted ticker — the boolean trigger, the basis day count and the
    # engine-vocabulary strings are deliberately not machine-checkable.
    assert evidence["facts_checked"] == 5
    assert evidence["facts_missing"] == 0
    assert evidence["must_state_total"] == 9
    assert evidence["unchecked_total"] == 5
    assert ux_receipt.SURFACE_DIGEST.fullmatch(evidence["challenge_hash"])


def test_challenge_fidelity_accepts_any_display_precision():
    # "0.343", "34.3%" and "34%" all state the frozen 0.34344…; so does a
    # rounded-up percent for a value whose next digit carries.
    for wording in ("weight becomes 0.343 of the book",
                    "takes it to 34.3%", "takes it to 34%"):
        payload = challenge_check_payload(presented_text=SYNTHETIC_ANSWER + " " + wording)
        assert fidelity_of(payload)["facts_missing"] == 0, wording


def test_challenge_fidelity_catches_a_disagreeing_number():
    payload = challenge_check_payload(
        presented_text=SYNTHETIC_ANSWER.replace("34.3%", "24.3%"))
    assert fidelity_of(payload)["facts_missing"] == 1


def test_challenge_fidelity_catches_a_dropped_ticker_and_rule_text():
    payload = challenge_check_payload(
        presented_text=SYNTHETIC_ANSWER.replace("9999.TT", "an unpriced holding"))
    assert fidelity_of(payload)["facts_missing"] == 1

    reworded = SYNTHETIC_ANSWER.replace(
        "Single position must stay under a fifth of the book",
        "your position-size rule")
    payload = challenge_check_payload(presented_text=reworded)
    assert fidelity_of(payload)["facts_missing"] == 1


def test_challenge_fidelity_catches_a_paraphrased_quote():
    reworded = SYNTHETIC_ANSWER.replace(
        "It is still my highest-conviction synthetic name.",
        "you still rate it highly")
    payload = challenge_check_payload(presented_text=reworded)
    assert fidelity_of(payload)["quotes_verbatim"] is False


def test_challenge_fidelity_on_a_context_free_call():
    challenge = challenge_check_payload()["challenge"]
    challenge["quote_verbatim"] = []
    challenge["unchecked"] = ["liquidity", "valuation", "tax", "position_fit"]
    evidence = fidelity_of({"challenge": challenge, "presented_text": SYNTHETIC_ANSWER})
    assert evidence["quotes_expected"] is False
    assert evidence["quotes_verbatim"] is True
    assert evidence["unchecked_total"] == 4


def test_challenge_fidelity_hash_is_canonical():
    first = fidelity_of(challenge_check_payload())["challenge_hash"]
    again = fidelity_of(challenge_check_payload())["challenge_hash"]
    assert first == again
    smaller = challenge_check_payload()
    smaller["challenge"]["must_state"] = smaller["challenge"]["must_state"][:-1]
    assert fidelity_of(smaller)["challenge_hash"] != first


def test_challenge_fidelity_refuses_a_hollow_challenge():
    """Emptied lists are refused, not measured against: the engine's block
    always owes basis facts, always names its four unconditional unchecked
    risks, and always states the two-sided case floor. A payload below any
    floor cannot have come from the consider call this event claims."""
    for hollow in ({"must_state": []},
                   {"unchecked": ["liquidity", "valuation", "tax"]},
                   {"case_required": {}},
                   {"case_required": {"for": 1, "against": 0}}):
        payload = challenge_check_payload()
        payload["challenge"].update(hollow)
        try:
            fidelity_of(payload)
        except ux_receipt.ReceiptError:
            continue
        raise AssertionError(f"a hollowed challenge was accepted: {hollow}")


def test_a_bare_zero_token_does_not_state_a_nonzero_fact():
    # Every fraction below one half rounds to a bare "0", so that token
    # carries no information about the value.
    challenge = {**challenge_check_payload()["challenge"],
                 "must_state": [{"topic": "position", "value": 0.343,
                                 "anchor": "consequence.after.weights.SYNTH"}],
                 "quote_verbatim": []}
    zero_only = "the trade leaves 0 room under your cap"
    evidence = fidelity_of({"challenge": challenge, "presented_text": zero_only})
    assert evidence["facts_missing"] == 1, evidence
    # A frozen zero is still stated by "0".
    challenge["must_state"] = [{"topic": "cash", "value": 0.0,
                                "anchor": "consequence.after.cash.balance"}]
    evidence = fidelity_of({"challenge": challenge, "presented_text": zero_only})
    assert evidence["facts_missing"] == 0, evidence


def test_a_dollar_value_does_not_match_its_percent_form():
    # cash is dollar-shaped: a fifty-cent balance is not stated by "50%" —
    # while the same number as a position weight legitimately is.
    base = challenge_check_payload()["challenge"]
    text = "this leaves 50% of the book in one name"
    cash = {**base, "must_state": [{"topic": "cash", "value": 0.5,
                                    "anchor": "consequence.after.cash.balance"}],
            "quote_verbatim": []}
    assert fidelity_of({"challenge": cash, "presented_text": text})["facts_missing"] == 1
    weight = {**base, "must_state": [{"topic": "position", "value": 0.5,
                                      "anchor": "consequence.after.weights.SYNTH"}],
              "quote_verbatim": []}
    assert fidelity_of({"challenge": weight, "presented_text": text})["facts_missing"] == 0


def test_evaluation_evidence_must_be_internally_consistent():
    """These counts describe one engine challenge, whose shape has floors —
    a row below them was written by hand, not by _challenge_fidelity."""
    for impossible, fragment in (
            ({"must_state_total": 0}, "claims an empty must_state"),
            ({"unchecked_total": 0}, "fewer than the four unconditional"),
            ({"unchecked_total": 3}, "fewer than the four unconditional"),
            ({"facts_checked": 12, "must_state_total": 9},
             "checked more facts than the challenge stated")):
        rows = consider_rows()
        at(rows, EVALUATION).update(impossible)
        assert_has(ux_receipt.verify_rows(rows), fragment)


def test_owner_verdict_is_judgments_only():
    # The one remaining free-text channel into an archived trace: every other
    # content-restricted row already rejects unknown fields.
    rows = consider_rows()
    at(rows, VERDICT)["presented_text"] = "BUY SYNTH because the user said so"
    assert_has(ux_receipt.verify_rows(rows),
               "owner_verdict contains unsupported fields: presented_text")
    rows = good_markdown_rows()
    rows.append(row("owner_verdict", controls="pass", card="pass",
                    memory="not_applicable", note="looked fine to me"))
    assert_has(ux_receipt.verify_rows(rows),
               "owner_verdict contains unsupported fields: note")


def test_challenge_fidelity_refuses_a_truncated_challenge():
    payload = challenge_check_payload()
    del payload["challenge"]["required_coverage"]
    try:
        fidelity_of(payload)
    except ux_receipt.ReceiptError as caught:
        assert "missing required_coverage" in str(caught)
    else:
        raise AssertionError("a challenge missing a contract key was accepted")

    for broken in ({"challenge": "not-an-object", "presented_text": "answer"},
                   {"challenge": challenge_check_payload()["challenge"],
                    "presented_text": "  "}):
        try:
            fidelity_of(broken)
        except ux_receipt.ReceiptError:
            continue
        raise AssertionError(f"malformed payload was accepted: {broken}")


def test_cli_writes_and_verifies_a_consider_trace():
    with tempfile.TemporaryDirectory() as tmp:
        common = ["--session-id", "eval-1a2b3c4d5e6f7a8b", "--state-root", tmp]
        start = subprocess.run(
            [sys.executable, str(TOOL), "start", *common, "--client", "codex-desktop",
             "--route", "consider"],
            capture_output=True, text=True)
        assert start.returncode == 0, start.stderr

        missing = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "evaluation_presented"],
            capture_output=True, text=True)
        assert missing.returncode == 2
        assert "requires --challenge-check-file" in missing.stderr

        check_file = pathlib.Path(tmp) / "challenge-check.json"
        check_file.write_text(json.dumps(challenge_check_payload(), ensure_ascii=False),
                              encoding="utf-8")
        presented = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "evaluation_presented",
             "--challenge-check-file", str(check_file)],
            capture_output=True, text=True)
        assert presented.returncode == 0, presented.stderr

        stateless = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "resolution_presented"],
            capture_output=True, text=True)
        assert stateless.returncode == 2
        assert "requires --workflow-state" in stateless.stderr

        invited = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "resolution_presented",
             "--workflow-state", "open"],
            capture_output=True, text=True)
        assert invited.returncode == 0, invited.stderr

        for arguments in (
                ["--event", "findings_recorded", "--no-findings"],
                ["--event", "owner_verdict", "--controls", "not_applicable",
                 "--card", "not_applicable", "--memory", "not_applicable",
                 "--comprehension", "pass", "--usefulness", "pass",
                 "--friction", "pass", "--resolution", "pass"]):
            done = subprocess.run(
                [sys.executable, str(TOOL), "event", *common, *arguments],
                capture_output=True, text=True)
            assert done.returncode == 0, done.stderr

        verified = subprocess.run(
            [sys.executable, str(TOOL), "verify", *common, "--require-owner-verdict",
             "--require-findings"],
            capture_output=True, text=True)
        assert verified.returncode == 0, verified.stderr

        # The trace carries evidence, never content: neither the presented
        # answer, nor a user sentence, nor the rule's text may reach disk.
        trace = (pathlib.Path(tmp) / "ux" / "eval-1a2b3c4d5e6f7a8b.jsonl").read_text(
            encoding="utf-8")
        for leaked in ("34.3%", "highest-conviction", "fifth of the book", "9999.TT"):
            assert leaked not in trace, leaked


def test_every_route_declares_a_complete_contract():
    """`ROUTES` is derived from the table, so a route cannot exist without one.

    A route added with a missing key would raise a KeyError deep inside
    verification instead of failing here, and a verdict axis with no scale would
    silently accept anything.
    """
    assert ux_receipt.ROUTES == tuple(ux_receipt.ROUTE_CONTRACTS)
    for route, contract in ux_receipt.ROUTE_CONTRACTS.items():
        assert set(contract) == {"cards", "cash_anchor", "opener", "change",
                                 "evaluation", "verdict", "must_pass"}, route
        assert set(contract["verdict"]) <= set(ux_receipt.VERDICT_AXES), route
        assert set(contract["must_pass"]) <= set(contract["verdict"]), route
        for axis, scale in contract["verdict"].items():
            assert scale and set(scale) <= {"pass", "fail", "not_applicable"}, (route, axis)
        # A route must render something the user can be shown, or it has no
        # evidence to carry at all.
        assert contract["cards"] or contract["change"] or contract["evaluation"], route


# --- Declaration integrity ---------------------------------------------------

def test_session_id_must_be_consistent():
    rows = good_markdown_rows()
    at(rows, PREVIEW_ARTIFACT)["session_id"] = "another-session"
    assert_has(ux_receipt.verify_rows(rows), "declared session_id")


def test_unknown_route_fails():
    rows = good_markdown_rows()
    redeclare(rows, route="bogus_route")
    assert_has(ux_receipt.verify_rows(rows), "unsupported route")


def test_version_mismatch_fails():
    rows = good_markdown_rows()
    at(rows, PREVIEW_ARTIFACT)["version"] = 1
    assert_has(ux_receipt.verify_rows(rows), "unsupported version")


def test_missing_declaration_first_fails():
    rows = drop(good_markdown_rows(), DECLARATION)
    assert_has(ux_receipt.verify_rows(rows), "capabilities_declared event as its first row")


# --- Owner verdict / manual gate ---------------------------------------------

def test_owner_verdict_must_follow_final_card():
    rows = good_markdown_rows()
    before(rows, FINAL_CARD,
           row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    assert_has(ux_receipt.verify_rows(rows), "must follow the final card presentation")


def test_manual_verification_requires_owner_verdict():
    rows = good_markdown_rows()
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "requires exactly one owner_verdict",
    )
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True) == []


# --- Gate 7: where the misses went (#417) -------------------------------------
#
# The first six gates can all pass on a run that found real problems and left no
# replayable asset behind — the loop that produced eighteen receipts and zero
# episodes. This gate is the moment that asks, so its own mutations matter: a
# gate that accepts an omitted disposition, or a conversion claim with nothing
# behind it, is the same loop with more ceremony.

def test_qa_verification_requires_a_findings_disposition():
    rows = good_markdown_rows()
    assert_has(
        ux_receipt.verify_rows(rows, require_findings=True),
        "exactly one findings_recorded event",
    )
    rows.append(row("findings_recorded", findings=["episode:EP-008"]))
    assert ux_receipt.verify_rows(rows, require_findings=True) == []


def test_a_run_that_found_nothing_must_say_so_rather_than_omit_the_event():
    """'None observed' is a real outcome; inferring it from silence is not."""
    rows = good_markdown_rows()
    rows.append(row("findings_recorded", findings=[]))
    assert ux_receipt.verify_rows(rows, require_findings=True) == []


def test_findings_recorded_rejects_an_unrecognized_disposition():
    rows = good_markdown_rows()
    rows.append(row("findings_recorded", findings=["logged it somewhere"]))
    assert_has(ux_receipt.verify_rows(rows, require_findings=True),
               "unrecognized finding disposition")


def test_findings_recorded_may_not_appear_twice():
    rows = good_markdown_rows()
    rows.append(row("findings_recorded", findings=[]))
    rows.append(row("findings_recorded", findings=["episode:EP-008"]))
    assert_has(ux_receipt.verify_rows(rows, require_findings=True),
               "findings_recorded may appear at most once")


def test_findings_recorded_without_a_list_is_not_a_declaration():
    rows = good_markdown_rows()
    rows.append(row("findings_recorded"))
    assert_has(ux_receipt.verify_rows(rows, require_findings=True),
               "must carry a findings list")


def test_recording_a_conversion_that_did_not_happen_fails_closed():
    """The id is resolved against the bank in this checkout, so 'converted' is a
    claim the tool can check rather than one the reader has to take on trust."""
    try:
        ux_receipt._findings(["episode:EP-994"], False)
    except ux_receipt.ReceiptError as error:
        assert "not in evals/episodes/" in str(error), error
    else:
        raise AssertionError("an unconverted episode id was accepted as converted")
    # The clean half: a real id from the shipped bank, and a miss no episode can
    # hold, both accepted.
    accepted = ux_receipt._findings(
        ["episode:EP-008", "not-episodable:#230:the card never reached the screen"], False)
    assert accepted == {"findings": ["episode:EP-008",
                                     "not-episodable:#230:the card never reached the screen"]}


def test_findings_event_refuses_silence_and_refuses_contradiction():
    for finding, none_declared, expected in (
            ([], False, "omitted disposition is not a declaration of none"),
            (["episode:EP-008"], True, "mutually exclusive"),
    ):
        try:
            ux_receipt._findings(finding, none_declared)
        except ux_receipt.ReceiptError as error:
            assert expected in str(error), (finding, none_declared, error)
        else:
            raise AssertionError(f"accepted {finding!r} / no_findings={none_declared}")


# The four below are external-review findings (2026-07-27). Each was a path the
# write-side gate covered and the read-side gate did not, or a claim the docs
# made that the tool never held anyone to.

def test_verify_rejects_a_conversion_claim_the_bank_cannot_back():
    """The write path resolved `episode:EP-NNN` against the bank; `verify` only
    re-checked the regex, so a hand-authored or post-edited receipt claiming a
    conversion that never happened verified clean — at the gate archiving uses."""
    rows = good_markdown_rows()
    rows.append(row("findings_recorded", findings=["episode:EP-994"]))
    assert_has(ux_receipt.verify_rows(rows, require_findings=True),
               "conversion claim with nothing behind it")
    rows[-1]["findings"] = ["episode:EP-008"]
    assert ux_receipt.verify_rows(rows, require_findings=True) == []


def test_findings_recorded_rejects_content_fields():
    """This event is the one a maintainer is most tempted to paste miss text
    into, and it sits inside the state directory's trust boundary."""
    rows = good_markdown_rows()
    rows.append(row("findings_recorded", findings=[],
                    private_text="the user's actual position and amount"))
    assert_has(ux_receipt.verify_rows(rows, require_findings=True),
               "findings_recorded contains unsupported fields: private_text")


def test_findings_must_precede_the_owner_verdict():
    """Both documents said so; the first cut enforced no ordering at all."""
    rows = good_markdown_rows()
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="not_applicable"))
    rows.append(row("findings_recorded", findings=[]))
    assert_has(ux_receipt.verify_rows(rows, require_owner_verdict=True, require_findings=True),
               "must precede the owner verdict")
    rows[-2], rows[-1] = rows[-1], rows[-2]
    assert ux_receipt.verify_rows(rows, require_owner_verdict=True,
                                  require_findings=True) == []


def test_the_bank_is_read_from_declared_ids_not_filenames():
    """A file named `EP-123-anything.json` holding invalid JSON counted as a
    converted episode, because the id was split out of the filename — a fact the
    file already states, hand-mirrored."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "repo"
        (fake / "skills" / "fomo-kernel" / "tools").mkdir(parents=True)
        (fake / "evals" / "episodes").mkdir(parents=True)
        (fake / "evals" / "episodes" / "EP-123-not-replayable.json").write_text(
            "{ not json", encoding="utf-8")
        (fake / "evals" / "episodes" / "EP-124-real.json").write_text(
            json.dumps({"id": "EP-124"}), encoding="utf-8")
        copy = fake / "skills" / "fomo-kernel" / "tools" / "ux_receipt.py"
        copy.write_text(TOOL.read_text(encoding="utf-8"), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("ux_receipt_isolated", copy)
        isolated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolated)
        # A file this loop cannot read must never stop the gate from running.
        # The first fix caught `json.JSONDecodeError`, which a non-UTF-8 file
        # does not raise — `read_text` raises `UnicodeDecodeError` instead, and
        # the tool crashed rather than failing the claim closed (review round 2).
        (fake / "evals" / "episodes" / "EP-125-bad-encoding.json").write_bytes(
            b"\xff\xfe\x00")
        assert isolated._episode_bank() == {"EP-124"}
        try:
            isolated._findings(["episode:EP-123"], False)
        except isolated.ReceiptError as error:
            assert "nothing behind it" in str(error), error
        else:
            raise AssertionError("an unparseable episode file backed a conversion claim")
        assert isolated._findings(["episode:EP-124"], False) == {"findings": ["episode:EP-124"]}


def test_dynamic_surface_manual_verdict_requires_specificity_and_answer_fit():
    rows = good_markdown_rows()
    before(rows, PREVIEW_ARTIFACT, row("question_presented", mode="plain_text",
                                       surface_source="validated_dynamic",
                                       surface_digest=SURFACE_DIGEST))
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
    # The obligation now comes from ROUTE_CONTRACTS["weekly_review"]["must_pass"]
    # rather than a `route == "weekly_review"` branch inside the owner gate, so
    # the message names the route and the axis it wanted.
    rows = weekly_rows()
    rows.append(row("owner_verdict", controls="pass", card="pass", memory="fail"))
    assert_has(
        ux_receipt.verify_rows(rows, require_owner_verdict=True),
        "weekly_review trace requires memory=pass",
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
            ["--event", "cash_anchor_checked", "--cash-outcome", "found_in_source"],
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
        assert written[0]["adapter"] == "plain_text"

        nomode = subprocess.run(
            [sys.executable, str(TOOL), "event", *common, "--event", "rule_choice_presented"],
            capture_output=True, text=True,
        )
        assert nomode.returncode == 2 and "requires --mode" in nomode.stderr


def test_cli_start_auto_declares_universal_fallbacks():
    # #297: plain_text/markdown_inline are universal fallbacks every
    # text-based client can render; a caller must not have to remember to
    # pass them explicitly alongside a richer capability like native_options
    # or widget. Post-#304, a caller declaring both extra capabilities must
    # also name the matching resolved adapter (validated_widget requires both
    # native_options and widget together); the default plain_text adapter
    # accepts only the universal fallbacks by design (see the "adapter
    # capability errors" tests below).
    with tempfile.TemporaryDirectory() as tmp:
        common = ["--session-id", "session-297", "--state-root", tmp]
        start = subprocess.run(
            [sys.executable, str(TOOL), "start", *common,
             "--client", "claude", "--route", "first_review",
             "--adapter", "validated_widget",
             "--question-mode", "native_options", "--card-mode", "widget"],
            capture_output=True, text=True,
        )
        assert start.returncode == 0, start.stderr
        receipt = pathlib.Path(tmp) / "ux" / "session-297.jsonl"
        declared = json.loads(receipt.read_text(encoding="utf-8").splitlines()[0])
        assert set(declared["question_modes"]) == {"native_options", "plain_text"}
        assert set(declared["card_modes"]) == {"widget", "markdown_inline"}


def test_cli_unknown_host_needs_no_optional_mode_flags():
    with tempfile.TemporaryDirectory() as tmp:
        started = subprocess.run(
            [sys.executable, str(TOOL), "start", "--session-id", "unknown-host",
             "--state-root", tmp, "--client", "future-agent", "--route", "first_review"],
            capture_output=True, text=True,
        )
        assert started.returncode == 0, started.stderr
        trace = pathlib.Path(tmp) / "ux" / "unknown-host.jsonl"
        declaration_row = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
        assert declaration_row["adapter"] == "plain_text"
        assert declaration_row["question_modes"] == ["plain_text"]
        assert declaration_row["card_modes"] == ["markdown_inline"]


def test_cli_native_options_profile_requires_and_records_its_extra_mode():
    with tempfile.TemporaryDirectory() as tmp:
        started = subprocess.run(
            [sys.executable, str(TOOL), "start", "--session-id", "known-host",
             "--state-root", tmp, "--client", "known-agent", "--route", "first_review",
             "--adapter", "native_options", "--question-mode", "native_options"],
            capture_output=True, text=True,
        )
        assert started.returncode == 0, started.stderr
        trace = pathlib.Path(tmp) / "ux" / "known-host.jsonl"
        declaration_row = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
        assert declaration_row["adapter"] == "native_options"
        assert declaration_row["question_modes"] == ["plain_text", "native_options"]
        assert declaration_row["card_modes"] == ["markdown_inline"]


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
#
# Pinning prose is expensive: it makes the contract unrewritable, and every pin
# fires on rewording rather than on a real defect. So a pin belongs here only
# when the sentence *is* the mechanism — a rule with no code that can enforce
# it. Anything the CLI already knows is derived from the CLI instead, which is
# both cheaper to maintain and strictly stronger: a hardcoded list can only
# notice a doc that lost a flag, while derivation also catches a CLI that grew
# one nobody documented. Do not add a pin without a comment saying why that
# exact wording is load-bearing.

def _receipt_cli_surface():
    """Every ux_receipt flag, event kind, and adapter/mode name, taken from
    build_parser() itself rather than a list that has to be maintained twice."""
    import argparse
    tools_dir = str(ROOT / "skills" / "fomo-kernel" / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import ux_receipt as _receipt
    flags, events, vocabulary = set(), set(), set()
    for action in _receipt.build_parser()._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for sub in action.choices.values():
            for arg in sub._actions:
                flags.update(opt for opt in arg.option_strings
                             if opt.startswith("--") and opt != "--help")
                if not arg.choices:
                    continue
                # Every closed vocabulary, not a hand-picked subset: an agent
                # that cannot see a legal value cannot record it, so route,
                # stage, surface source, memory kind, cash outcome, and the
                # verdict scales all belong here alongside adapter and modes.
                (events if arg.dest == "event" else vocabulary).update(
                    str(choice) for choice in arg.choices)
    return flags, events, vocabulary


def test_runtime_contract_documents_the_whole_receipt_cli():
    # An agent can only record what the contract told it about, so a flag,
    # event kind, or adapter name that exists but is documented nowhere is dead
    # capability. Deriving the baseline means this fails when the CLI grows too,
    # not only when a doc drops something.
    text = SPEC.read_text(encoding="utf-8") + "\n" + RECEIPT_SPEC.read_text(encoding="utf-8")
    flags, events, vocabulary = _receipt_cli_surface()
    undocumented = sorted(f for f in flags
                          if not re.search(re.escape(f) + r"(?![\w-])", text))
    assert not undocumented, f"ux_receipt flags no runtime doc mentions: {undocumented}"
    for label, names in (("event kinds", events), ("adapter/mode names", vocabulary)):
        missing = sorted(n for n in names if n not in text)
        assert not missing, f"{label} no runtime doc mentions: {missing}"


def test_documented_receipt_commands_actually_run():
    """Every ux_receipt command shown in the docs must execute successfully.

    String mirroring proves a flag is mentioned somewhere; it cannot prove the
    command an agent copies is runnable. A `widget_attempt_failed` example
    shipped without its required --stage and nothing noticed, because every
    individual token in it was present. Parsing is not enough either: that
    requirement is enforced in the handler, not by argparse, so parse_args
    accepts the broken command. Only running it catches this class, so each
    documented event runs against its own fresh trace.
    """
    placeholders = {
        "<session_id>": "s", "<id>": "s", "<client>": "c", "<route>": "first_review",
        "<surface_digest>": "a" * 64,
    }
    doc = re.sub(r"\\\n\s*", " ", RECEIPT_SPEC.read_text(encoding="utf-8"))
    commands = [shlex.split(line)[2:] for line in
                re.findall(r"^python3 tools/ux_receipt\.py .*$", doc, re.M)]
    assert commands, "the receipt reference documents no commands at all"
    starts = [c for c in commands if c and c[0] == "start"]
    events = [c for c in commands if c and c[0] == "event"]
    assert starts and events, "expected both start and event examples"
    # The base trace every documented event is replayed against. Chosen by what
    # it declares, not by where it sits: `starts[-1]` meant "the widest
    # capability" only for as long as the widest example happened to be last,
    # and #523 added a second route with its own `start`.
    base = next((c for c in reversed(starts) if "validated_widget" in c), starts[-1])

    def resolve(argv, root, scratch):
        out = []
        for token in argv:
            token = placeholders.get(token, token)
            if token.startswith("<") and token.endswith(">"):
                # Any remaining placeholder is a path the example writes or reads.
                token = str(scratch / "artifact.md")
            out.append(token)
        return [sys.executable, str(TOOL)] + out + ["--state-root", str(root)]

    failures = []
    for command in starts + events:
        with tempfile.TemporaryDirectory() as tmp:
            root, scratch = pathlib.Path(tmp), pathlib.Path(tmp)
            (scratch / "artifact.md").write_text("card", encoding="utf-8")
            (scratch / "grounding.json").write_text(
                json.dumps({"candidates": [{"id": "candidate_0"}],
                            "presented_text": "text"}), encoding="utf-8")
            (scratch / "challenge.json").write_text(
                json.dumps(challenge_check_payload(), ensure_ascii=False),
                encoding="utf-8")
            if command[0] == "event":
                # Declare the widest capability so widget/native examples are
                # legal, then run the documented event against that trace.
                subprocess.run(resolve(base[:], root, scratch),
                               capture_output=True, text=True)
            argv = resolve(command[:], root, scratch)
            if "--grounding-check-file" in argv:
                argv[argv.index("--grounding-check-file") + 1] = str(scratch / "grounding.json")
            if "--challenge-check-file" in argv:
                argv[argv.index("--challenge-check-file") + 1] = str(scratch / "challenge.json")
            result = subprocess.run(argv, capture_output=True, text=True)
            if result.returncode != 0:
                failures.append(f"{' '.join(command)}\n    -> "
                                f"{(result.stderr or result.stdout).strip().splitlines()[-1]}")
    assert not failures, ("documented commands that fail when run:\n" +
                          "\n".join(failures))


def test_runtime_contract_contains_fixed_fallback_and_no_file_only_success():
    text = SPEC.read_text(encoding="utf-8") + "\n" + RECEIPT_SPEC.read_text(encoding="utf-8")
    for fragment in (
        # The literal template agents copy for the text route. Reword it and
        # every plain_text host renders a different question shape.
        "A. <label> — <description>",
        "Reply with one option label: A, B, ...",
        # #239: a generated file was being reported as a delivered card. No
        # engine check can see whether content reached the conversation, so
        # this stated rule is the only thing standing between the two.
        "Artifact generation is not presentation",
        "A file path or attachment without inline card content is not presentation",
        # The trace holds session-linked evidence. Naming the directory is not
        # the protection — these two prohibitions are, and a doc could keep the
        # phrase "protected state directory" while dropping both.
        "never committed and never published",
        # Backfilling a trace at wrap-up would fake the very evidence the trace
        # exists to provide. verify's timing heuristic catches only the crude
        # case, so the instruction carries the rest.
        "never replace it or reconstruct earlier events",
        # A suspect receipt must not be cited as owner-live ground truth; that
        # limit lives in prose because it is a claim about how to use evidence,
        # not a property the tool can assert about itself.
        "owner_live_eligible=false",
        # #442: the sentence this whole lock exists for was itself unlocked.
        # PR #399 review caught it deleted; 358357b restored it; but no
        # fragment here pinned it, so a second deletion would fail nothing --
        # the exact gap docs/development-guide.md §0 names.
        "Each question needs its own visible turn",
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
    redeclare(rows, route="weekly_review")
    after(rows, DECLARATION, row("question_presented", mode="plain_text"))
    after(rows, QUESTION, row("memory_presented", memory_kind="prior_commitment"))
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
