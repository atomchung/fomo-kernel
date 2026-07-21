#!/usr/bin/env python3
"""Deterministic contracts for bounded private question surfaces (#238)."""
import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "fomo-kernel" / "engine"
REVIEW = ENGINE / "review.py"
SCHEMAS = ROOT / "skills" / "fomo-kernel" / "schemas"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ROOT / "tests"))
import question_surface  # noqa: E402
import review as review_engine  # noqa: E402
import session as session_engine  # noqa: E402
import thesis as thesis_engine  # noqa: E402
import test_review_v2 as v2  # noqa: E402

UX_RECEIPT_PATH = ROOT / "skills" / "fomo-kernel" / "tools" / "ux_receipt.py"
UX_SPEC = importlib.util.spec_from_file_location("question_surface_ux_receipt", UX_RECEIPT_PATH)
ux_receipt = importlib.util.module_from_spec(UX_SPEC)
assert UX_SPEC.loader is not None
UX_SPEC.loader.exec_module(ux_receipt)


def _add_question(prior_text="Enterprise demand is accelerating", maturity="testable"):
    cycle_id = "NVDA#2026-06-01#1"
    state = {"holdings": {"positions": {
        "NVDA": {"cycle_id": cycle_id, "cost": 5000,
                 "decision_cursor": cycle_id + "#add#2"}
    }}}
    card = {"thesis_questions": [{"ticker": "NVDA"}]}
    active = {cycle_id: {"why": prior_text, "maturity": maturity,
                         "session_date": "2026-07-01"}}
    queue, _report = review_engine._question_queue(card, state, active, None, "en")
    return queue[0]


def _plan(question, session_id="2026-07-19__surface"):
    return {"session_id": session_id, "question_queue": [question],
            "engine_state": {"date_end": "2026-07-19"}}


def _surface_artifact(plan, question, marker="Focused"):
    context = question["question_opportunity"]["context"]
    if (context.get("prior_thesis") or {}).get("text"):
        fact = context["prior_thesis"]["text"]
        ref = "context.prior_thesis.text"
    elif context.get("ticker"):
        fact = context["ticker"]
        ref = "context.ticker"
    else:
        fact = context["headline_dimension"]["label"]
        ref = "context.headline_dimension.label"
    choices = question["question_opportunity"]["answer_contract"]["canonical_choices"]
    options = []
    for index, choice in enumerate(choices):
        if index == 0:
            options.append({
                "label": f"{marker} evidence for {fact}",
                "description": f"Use this when {fact} changed what you believed.",
                "maps_to": choice,
                "grounding_refs": [ref],
            })
        else:
            options.append({
                "label": choice.replace("_", " ").title(),
                "description": "Use this only when its engine-defined meaning matches your answer.",
                "maps_to": choice,
                "grounding_refs": [],
            })
    return {
        "schema_version": 1,
        "session_id": plan["session_id"],
        "surfaces": [{
            "question_id": question["id"],
            "stem": f"You previously said {fact}. What best explains this decision?",
            "stem_grounding_refs": [ref],
            "options": options,
            "none_of_above": {
                "label": "None of these fits",
                "description": "Say it in your own words and keep the classification unresolved if needed.",
            },
            "clarification": {
                "stem": f"For {fact}, which engine-defined meaning is closest to your own words?",
                "grounding_refs": [ref],
            },
        }],
    }


def _run(*args):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True)


def test_opportunity_is_engine_owned_and_limited_to_first_slice():
    add = _add_question()
    opportunity = add["question_opportunity"]
    assert opportunity["intent"] == "classify_losing_position_add"
    assert opportunity["answer_contract"]["canonical_choices"] == \
        [row["value"] for row in add["options"]]
    assert opportunity["answer_contract"]["requirements_by_choice"]["new_evidence"] == \
        ["evidence_delta.claim", "evidence_delta.source"]
    assert opportunity["answer_contract"]["max_clarifications"] == 1

    headline_queue, _headline_report = review_engine._question_queue(
        {"top_holes": [{"dim": "averaging_down"}]}, {"holdings": {"positions": {}}},
        {}, None, "en"
    )
    headline = headline_queue[0]
    assert headline["kind"] == "headline_motive"
    assert headline["question_opportunity"]["intent"] == "classify_headline_motive"

    exit_question = review_engine._exit_question({
        "revisit_id": "exit-one", "ticker": "A", "cycle_id": "A#one",
        "exit_date": "2026-07-18", "exit_price": 10, "shares_sold": 1,
        "shares_before": 1, "kind": "full", "currency": "USD",
    }, "en")
    assert "question_opportunity" not in exit_question
    assert {"due_revisit", "rule_breach", "revisit"}.isdisjoint(
        {add["kind"], headline["kind"]}
    )


def _grounded_headline(ticker, pct):
    raw = {"dim": "部位 sizing", "max_ticker": ticker, "max_pct": pct,
           "risk_weights": {ticker: pct}}
    card = {
        "top_holes": [{"dim": "部位 sizing", "raw": raw}],
        "ticker_diagnosis": [{"ticker": ticker, "impact": -100}],
    }
    queue, _report = review_engine._question_queue(
        card, {"holdings": {"positions": {}}}, {}, None, "en"
    )
    return queue[0]


def test_headline_fallback_changes_with_engine_grounding_but_keeps_contract():
    first = _grounded_headline("NVDA", 0.47)
    second = _grounded_headline("AMD", 0.31)

    for key in ("id", "kind", "required"):
        assert first[key] == second[key], key
    assert [row["value"] for row in first["options"]] == \
        [row["value"] for row in second["options"]]

    first_context = first["question_opportunity"]["context"]
    second_context = second["question_opportunity"]["context"]
    assert first_context["headline_dimension"] == second_context["headline_dimension"]
    assert first_context["ticker"] == "NVDA"
    assert second_context["ticker"] == "AMD"
    assert "NVDA" in first_context["asked_because"] and "47%" in first_context["asked_because"]
    assert "AMD" in second_context["asked_because"] and "31%" in second_context["asked_because"]
    assert first["question"] != second["question"]
    assert first_context["asked_because"] in first["question"]
    assert second_context["asked_because"] in second["question"]

    # A bounded private surface can cite the concrete engine fact through the
    # existing grounding ref; no new schema field or free-form data lane is
    # needed.
    for question in (first, second):
        plan = _plan(question)
        fact = question["question_opportunity"]["context"]["asked_because"]
        artifact = _surface_artifact(plan, question)
        surface = artifact["surfaces"][0]
        surface["stem"] = f"{fact} What mainly drove this behavior?"
        surface["stem_grounding_refs"] = ["context.asked_because"]
        validated = question_surface.validate_surfaces(plan, artifact)
        presentation = question_surface.build_presentations(plan, validated)[0]
        assert presentation["stem"] == surface["stem"]
        assert presentation["stem_grounding_refs"] == ["context.asked_because"]


def test_headline_fallback_without_citable_fact_stays_dimension_only():
    queue, _report = review_engine._question_queue(
        {"top_holes": [{"dim": "部位 sizing", "raw": {
            "dim": "部位 sizing", "max_ticker": "NVDA", "max_pct": None,
        }}], "dims_raw": [{"dim": "部位 sizing", "max_ticker": "NVDA", "max_pct": None}]},
        {"holdings": {"positions": {}}}, {}, None, "en"
    )
    question = queue[0]
    assert question["question"] == "What mainly drove the behavior behind position sizing?"
    assert "ticker" not in question and "asked_because" not in question
    assert question["question_opportunity"]["context"] == {
        "headline_dimension": {"id": "部位 sizing", "label": "position sizing"}
    }


def test_differential_personas_change_surface_not_engine_contract():
    first = _add_question("Enterprise demand is accelerating")
    second = _add_question("Margin recovery is the core thesis")
    for key in ("id", "kind", "required", "ticker", "cycle_id", "decision_cursor"):
        assert first[key] == second[key], key
    assert [row["value"] for row in first["options"]] == [row["value"] for row in second["options"]]
    first_plan, second_plan = _plan(first), _plan(second)
    first_artifact = question_surface.validate_surfaces(
        first_plan, _surface_artifact(first_plan, first, "Operating")
    )
    second_artifact = question_surface.validate_surfaces(
        second_plan, _surface_artifact(second_plan, second, "Valuation")
    )
    first_presentation = question_surface.build_presentations(first_plan, first_artifact)[0]
    second_presentation = question_surface.build_presentations(second_plan, second_artifact)[0]
    assert first_presentation["stem"] != second_presentation["stem"]
    assert first_presentation["options"][0]["label"] != second_presentation["options"][0]["label"]
    assert [row["value"] for row in first_presentation["options"]] == \
        [row["value"] for row in second_presentation["options"]]
    assert [row["semantic_anchor"] for row in first_presentation["options"]] == \
        [row["description"] for row in first["options"]]
    assert [row["payload_requirements"] for row in first_presentation["options"]] == \
        [row["payload_requirements"] for row in second_presentation["options"]]
    assert first_presentation["required"] is second_presentation["required"] is True


def test_surface_mutations_fail_closed():
    question = _add_question()
    plan = _plan(question)
    good = _surface_artifact(plan, question)
    question_surface.validate_surfaces(plan, good)
    mutations = []

    reordered = copy.deepcopy(good)
    reordered["surfaces"][0]["options"][0], reordered["surfaces"][0]["options"][1] = \
        reordered["surfaces"][0]["options"][1], reordered["surfaces"][0]["options"][0]
    mutations.append(reordered)
    duplicate = copy.deepcopy(good)
    duplicate["surfaces"][0]["options"][1]["maps_to"] = duplicate["surfaces"][0]["options"][0]["maps_to"]
    mutations.append(duplicate)
    missing = copy.deepcopy(good)
    missing["surfaces"][0]["options"].pop()
    mutations.append(missing)
    invented_ref = copy.deepcopy(good)
    invented_ref["surfaces"][0]["stem_grounding_refs"] = ["context.external_news"]
    mutations.append(invented_ref)
    invented_number = copy.deepcopy(good)
    invented_number["surfaces"][0]["stem"] += " It rose 99%."
    mutations.append(invented_number)
    changed_required = copy.deepcopy(good)
    changed_required["surfaces"][0]["required"] = False
    mutations.append(changed_required)
    changed_payload = copy.deepcopy(good)
    changed_payload["surfaces"][0]["options"][0]["payload_requirements"] = []
    mutations.append(changed_payload)
    changed_semantics = copy.deepcopy(good)
    changed_semantics["surfaces"][0]["options"][0]["semantic_anchor"] = "Price fell"
    mutations.append(changed_semantics)
    second_followup = copy.deepcopy(good)
    second_followup["surfaces"][0]["second_clarification"] = "another question"
    mutations.append(second_followup)

    for mutation in mutations:
        try:
            question_surface.validate_surfaces(plan, mutation)
            assert False, mutation
        except question_surface.QuestionSurfaceError:
            pass


def test_surface_list_order_cannot_change_engine_queue_order():
    add = _add_question()
    headline_queue, _headline_report = review_engine._question_queue(
        {"top_holes": [{"dim": "averaging_down"}]}, {"holdings": {"positions": {}}},
        {}, None, "en"
    )
    headline = headline_queue[0]
    plan = {"session_id": "2026-07-19__two-surfaces",
            "question_queue": [add, headline]}
    add_surface = _surface_artifact(plan, add)["surfaces"][0]
    headline_surface = _surface_artifact(plan, headline)["surfaces"][0]
    reversed_artifact = {
        "schema_version": 1,
        "session_id": plan["session_id"],
        "surfaces": [headline_surface, add_surface],
    }
    validated = question_surface.validate_surfaces(plan, reversed_artifact)
    presentations = question_surface.build_presentations(plan, validated)
    assert [row["question_id"] for row in presentations] == [add["id"], headline["id"]]


def test_resume_freezes_validated_surface_and_invalid_generation_falls_back():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        question = _add_question()
        plan = _plan(question)
        session_engine.save_pending(root, plan["session_id"], plan=plan)
        artifact = _surface_artifact(plan, question)
        artifact_path = pathlib.Path(tmp) / "surfaces.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

        bound = _run("resume", "--root", root, "--session-id", plan["session_id"],
                     "--question-surfaces", artifact_path)
        assert bound.returncode == 0, bound.stdout + bound.stderr
        first = json.loads(bound.stdout)
        assert first["status"] == "surface_validated"
        assert "engine_card" not in first["plan"]
        assert "engine_state" not in first["plan"]
        assert "question_surfaces" not in first
        surface_bytes = (root / ".pending" / plan["session_id"] / "question-surfaces.json").read_bytes()
        presentation_bytes = (root / ".pending" / plan["session_id"] / "question-presentations.json").read_bytes()

        resumed = _run("resume", "--root", root, "--session-id", plan["session_id"])
        rebound = _run("resume", "--root", root, "--session-id", plan["session_id"],
                       "--question-surfaces", artifact_path)
        assert resumed.returncode == rebound.returncode == 0
        assert json.loads(resumed.stdout)["question_presentations"] == first["question_presentations"]
        assert json.loads(rebound.stdout)["question_presentations"] == first["question_presentations"]
        assert surface_bytes == (root / ".pending" / plan["session_id"] / "question-surfaces.json").read_bytes()
        assert presentation_bytes == (root / ".pending" / plan["session_id"] / "question-presentations.json").read_bytes()

        changed = copy.deepcopy(artifact)
        changed["surfaces"][0]["stem"] += " Please choose carefully."
        changed_path = pathlib.Path(tmp) / "changed.json"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        conflict = _run("resume", "--root", root, "--session-id", plan["session_id"],
                        "--question-surfaces", changed_path)
        assert conflict.returncode == 2
        assert "already fixed" in json.loads(conflict.stdout)["error"]

        invalid_after_freeze = copy.deepcopy(artifact)
        invalid_after_freeze["surfaces"][0]["options"].reverse()
        invalid_after_freeze_path = pathlib.Path(tmp) / "invalid-after-freeze.json"
        invalid_after_freeze_path.write_text(json.dumps(invalid_after_freeze), encoding="utf-8")
        frozen_conflict = _run("resume", "--root", root, "--session-id", plan["session_id"],
                               "--question-surfaces", invalid_after_freeze_path)
        assert frozen_conflict.returncode == 2
        assert "already fixed" in json.loads(frozen_conflict.stdout)["error"]
        assert presentation_bytes == (root / ".pending" / plan["session_id"] /
                                      "question-presentations.json").read_bytes()

        fallback_root = pathlib.Path(tmp) / "fallback"
        fallback_plan = _plan(question, "2026-07-19__fallback")
        session_engine.save_pending(fallback_root, fallback_plan["session_id"], plan=fallback_plan)
        invalid = _surface_artifact(fallback_plan, question)
        invalid["surfaces"][0]["options"].reverse()
        invalid_path = pathlib.Path(tmp) / "invalid.json"
        invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
        failed_soft = _run("resume", "--root", fallback_root,
                           "--session-id", fallback_plan["session_id"],
                           "--question-surfaces", invalid_path)
        assert failed_soft.returncode == 0
        payload = json.loads(failed_soft.stdout)
        assert payload["status"] == "surface_fallback"
        assert "engine_card" not in payload["plan"]
        assert "engine_state" not in payload["plan"]
        fallback = payload["question_presentations"][0]
        assert fallback["stem"] == question["question"]
        assert fallback["options"] == question["options"]
        assert not (fallback_root / ".pending" / fallback_plan["session_id"] /
                    "question-surfaces.json").exists()


def test_cross_client_receipts_share_digest_without_private_copy():
    question = _add_question()
    plan = _plan(question)
    artifact = question_surface.validate_surfaces(plan, _surface_artifact(plan, question))
    presentation = question_surface.build_presentations(plan, artifact)[0]
    base = {"version": ux_receipt.VERSION, "session_id": plan["session_id"]}
    for mode in ("native_options", "plain_text"):
        rows = [
            {**base, "event": "capabilities_declared", "client": "test-client",
             "route": "first_review", "question_modes": ["plain_text", mode],
             "card_modes": ["markdown_inline"]},
            {**base, "event": "question_presented", "mode": mode,
             "surface_source": presentation["source"],
             "surface_digest": presentation["surface_digest"]},
            {**base, "event": "artifact_generated", "stage": "preview",
             "artifact_path": "/tmp/preview.md"},
            {**base, "event": "card_presented", "stage": "preview", "mode": "markdown_inline"},
            {**base, "event": "artifact_generated", "stage": "final",
             "artifact_path": "/tmp/final.md"},
            {**base, "event": "card_presented", "stage": "final", "mode": "markdown_inline"},
        ]
        assert ux_receipt.verify_rows(rows) == []
        receipt_text = json.dumps(rows[1], ensure_ascii=False)
        assert "NVDA" not in receipt_text
        assert "Enterprise demand" not in receipt_text
        assert presentation["surface_digest"] in receipt_text


def test_own_words_mapping_preserves_uncertainty_and_one_clarification():
    question = _add_question()
    plan = _plan(question)
    artifact = question_surface.validate_surfaces(plan, _surface_artifact(plan, question))
    presentation = question_surface.build_presentations(plan, artifact)[0]
    answer = {
        "question_id": question["id"], "choice": "skip", "note": "Buying the dip",
        "response_mode": "own_words",
        "response_provenance": {
            "user_statement": "Buying the dip",
            "motive_summary": "No stable canonical mapping was confirmed.",
            "summary_author": "ai_interpretation",
            "mapping_confidence": "low",
            "unresolved": True,
            "interpretation_confirmed": False,
            "clarification": {
                "stem": presentation["clarification"]["stem"],
                "grounding_refs": presentation["clarification"]["grounding_refs"],
                "user_statement": "No new fact I can name",
            },
        },
    }
    answers = {"answers": [answer]}
    question_surface.validate_answer_contract(plan, answers, [presentation])

    forced = copy.deepcopy(answers)
    forced["answers"][0]["choice"] = "price_only"
    forced["answers"][0]["response_provenance"]["unresolved"] = False
    try:
        question_surface.validate_answer_contract(plan, forced, [presentation])
        assert False, "own-words mapping cannot be forced without user confirmation"
    except question_surface.QuestionSurfaceError as exc:
        assert "explicit user confirmation" in str(exc)

    confirmed = copy.deepcopy(forced)
    confirmed["answers"][0]["response_provenance"]["interpretation_confirmed"] = True
    confirmed["answers"][0]["response_provenance"]["mapping_confidence"] = "high"
    confirmed["answers"][0]["note"] = None
    question_surface.validate_answer_contract(plan, confirmed, [presentation])

    extra = copy.deepcopy(answers)
    extra["answers"][0]["response_provenance"]["clarification"]["second"] = "not allowed"
    try:
        question_surface.validate_answer_contract(plan, extra, [presentation])
        assert False, "a second clarification surface must be rejected"
    except question_surface.QuestionSurfaceError:
        pass

    new_evidence = {"answers": [{"question_id": question["id"], "choice": "new_evidence"}]}
    try:
        thesis_engine.validate_required_answers(plan, new_evidence, allow_commitment_missing=True)
        thesis_engine.build_decision_events(plan, new_evidence)
        assert False, "new_evidence still requires claim and source"
    except thesis_engine.ThesisError as exc:
        assert "requires evidence_delta" in str(exc)

    for choice in ("planned_tranche", "valuation_change"):
        without_note = {"answers": [{"question_id": question["id"], "choice": choice}]}
        try:
            thesis_engine.build_decision_events(plan, without_note)
            assert False, f"{choice} still requires the user's short note"
        except thesis_engine.ThesisError as exc:
            assert "requires a short note" in str(exc)
        with_note = copy.deepcopy(without_note)
        with_note["answers"][0]["note"] = "This was already in my plan"
        events = thesis_engine.build_decision_events(plan, with_note)
        assert events[0]["decision"] == choice


def test_surface_is_private_and_committed_with_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = v2._prepare(tmp, root, language="en")
        question = next(row for row in plan["question_queue"]
                        if row.get("question_opportunity"))
        artifact = _surface_artifact(plan, question, "Private sentinel wording")
        artifact_path = pathlib.Path(tmp) / "private-surfaces.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        bound = _run("resume", "--root", root, "--session-id", plan["session_id"],
                     "--question-surfaces", artifact_path)
        assert bound.returncode == 0, bound.stdout + bound.stderr
        presentations = json.loads(bound.stdout)["question_presentations"]

        answers = v2._answers(plan, commitment="candidate_0")
        answer_by_id = {row["question_id"]: row for row in answers["answers"]}
        selected = answer_by_id[question["id"]]
        selected["response_mode"] = "own_words"
        selected["response_provenance"] = {
            "user_statement": "Private own words sentinel",
            "motive_summary": "Private AI interpretation sentinel",
            "summary_author": "ai_interpretation",
            "mapping_confidence": "high",
            "unresolved": False,
            "interpretation_confirmed": True,
        }
        answer_path = pathlib.Path(tmp) / "answers-surface.json"
        narrative_path = pathlib.Path(tmp) / "narrative-surface.json"
        answer_path.write_text(json.dumps(answers), encoding="utf-8")
        narrative_path.write_text(json.dumps(v2._narrative("en")), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answer_path, "--narrative", narrative_path)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        session_dir = pathlib.Path(result["path"])
        bundle = json.loads((session_dir / "bundle.json").read_text(encoding="utf-8"))
        assert bundle["question_surfaces"] == artifact
        assert (session_dir / "question-surfaces.json").is_file()
        assert (session_dir / "question-presentations.json").is_file()
        manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))["sha256"]
        assert "question-surfaces.json" in manifest and "question-presentations.json" in manifest
        public = (session_dir / "card-public.md").read_text(encoding="utf-8")
        for private in ("Private sentinel wording", "Private own words sentinel",
                        "Private AI interpretation sentinel", question.get("ticker") or "NVDA"):
            assert private not in public


def test_published_schemas_and_cross_client_contract_are_routed():
    names = {path.name for path in SCHEMAS.glob("*.json")}
    assert {"question-opportunity.schema.json", "question-surface.schema.json"} <= names
    plan_schema = json.loads((SCHEMAS / "review-plan.schema.json").read_text(encoding="utf-8"))
    item = plan_schema["properties"]["question_queue"]["items"]
    assert item["properties"]["question_opportunity"]["$ref"] == "question-opportunity.schema.json"
    answers_schema = json.loads((SCHEMAS / "answers.schema.json").read_text(encoding="utf-8"))
    answer_item = answers_schema["properties"]["answers"]["items"]["properties"]
    assert {"response_mode", "response_provenance"} <= set(answer_item)
    contract = ROOT / "skills" / "fomo-kernel" / "references" / "interaction-delivery.md"
    text = contract.read_text(encoding="utf-8")
    for phrase in ("validated_dynamic", "engine_fallback", "native_options",
                   "plain_text", "surface_digest"):
        assert phrase in text
    for rel in ("AGENTS.md", "skills/fomo-kernel/SKILL.md",
                "skills/fomo-kernel/flows/first-review.md",
                "skills/fomo-kernel/flows/weekly-review.md"):
        assert "references/interaction-delivery.md" in (ROOT / rel).read_text(encoding="utf-8")


def main():
    tests = sorted((name, fn) for name, fn in globals().items()
                   if name.startswith("test_") and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS ", name)
        except Exception as exc:
            failed += 1
            print("FAIL ", name, repr(exc))
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
