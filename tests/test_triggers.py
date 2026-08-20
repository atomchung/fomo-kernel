#!/usr/bin/env python3
"""Offline deterministic gates for the current #827 trigger corpus v2."""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRIGGERS_DIR = ROOT / "evals" / "triggers"
RUNNER = TRIGGERS_DIR / "run_triggers.py"
V2_CORPUS_DIR = TRIGGERS_DIR / "corpus-v2"
ARCHIVAL_CORPUS_DIR = TRIGGERS_DIR / "corpus"


def _load_module():
    spec = importlib.util.spec_from_file_location("triggers_run_triggers_v2", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rt = _load_module()


def _run(*args):
    return subprocess.run(
        [sys.executable, str(RUNNER), *args], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )


def _prompt(pid, class_name, text, near_miss_of=None):
    row = {"id": pid, "class": class_name, "text": text, "note": "synthetic test fixture"}
    if near_miss_of is not None:
        row["near_miss_of"] = near_miss_of
    return row


def _fixture_prompts(locale, split):
    split_tag = "cal" if split == "calibration" else "hold"
    rows = []
    specs = (
        ("named_security_decision", "dec", None),
        ("candidate_discovery", "dis", None),
        ("adjacent_negative", "neg", "general"),
    )
    for class_name, class_tag, near_miss in specs:
        for index in range(1, rt.PROMPTS_PER_CLASS + 1):
            pid = f"v2-{locale}-{split_tag}-{class_tag}-{index:02d}"
            target = (rt.NEAR_MISS_TARGETS[(index - 1) % len(rt.NEAR_MISS_TARGETS)]
                      if class_name == "adjacent_negative" else near_miss)
            rows.append(_prompt(pid, class_name, f"{locale} {split} {class_name} unique {index}", target))
    return rows


def _write_fixture(root):
    root = pathlib.Path(root)
    for locale in rt.LOCALES:
        locale_dir = root / locale
        locale_dir.mkdir(parents=True, exist_ok=True)
        for split in rt.SPLITS:
            payload = {
                "version": rt.CORPUS_VERSION,
                "locale": locale,
                "split": split,
                "prompts": _fixture_prompts(locale, split),
            }
            (locale_dir / f"{split}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _attempt(corpus, locale, split, prompt, actual_route=None):
    return rt.build_attempt(
        corpus, locale=locale, split=split, prompt_id=prompt["id"],
        host="codex", host_version="test", model="test-model",
        skill_population="fomo-kernel",
        actual_route=actual_route or rt.EXPECTED_ROUTE_BY_CLASS[prompt["class"]],
        raw_outcome="synthetic observed route",
    )


def test_default_release_authority_is_v2_not_the_archival_corpus():
    assert rt.CORPUS_VERSION == "v2"
    assert rt.DEFAULT_CORPUS_DIR == V2_CORPUS_DIR
    assert rt.DEFAULT_CORPUS_DIR != ARCHIVAL_CORPUS_DIR
    assert rt.CLASSES == (
        "named_security_decision", "candidate_discovery", "adjacent_negative")
    assert rt.ROUTES == ("trigger", "no_trigger")
    assert "review_positive" not in rt.CLASSES
    assert "pre_trade_positive" not in rt.CLASSES


def test_committed_v2_corpus_is_complete_disjoint_and_strict():
    corpus, problems = rt.load_corpus()
    assert problems == [], problems
    assert rt.corpus_totals(corpus) == 108
    for locale in rt.LOCALES:
        for split in rt.SPLITS:
            rows = corpus[locale][split]
            assert len(rows) == 18
            for class_name in rt.CLASSES:
                assert sum(row["class"] == class_name for row in rows) == 6
            targets = {
                row["near_miss_of"] for row in rows
                if row["class"] == "adjacent_negative"
            }
            assert targets == set(rt.NEAR_MISS_TARGETS)
    assert set(rt.MIN_CORRECT.values()) == {rt.PROMPTS_PER_CLASS}


def test_v2_schema_is_json_and_names_the_current_classes():
    schema = json.loads(
        (TRIGGERS_DIR / "schema" / "prompt-corpus-v2.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["version"]["const"] == rt.CORPUS_VERSION
    classes = schema["properties"]["prompts"]["items"]["properties"]["class"]["enum"]
    assert tuple(classes) == rt.CLASSES

    attempt_schema = json.loads(
        (TRIGGERS_DIR / "schema" / "trigger-attempt.schema.json").read_text(encoding="utf-8"))
    assert attempt_schema["properties"]["corpus_version"]["const"] == rt.CORPUS_VERSION
    assert tuple(attempt_schema["properties"]["class"]["enum"]) == rt.CLASSES
    assert tuple(attempt_schema["properties"]["expected_route"]["enum"]) == rt.ROUTES
    assert tuple(attempt_schema["properties"]["actual_route"]["enum"]) == rt.ROUTES
    assert set(attempt_schema["required"]) == set(attempt_schema["properties"])


def test_default_validate_and_dry_run_use_only_v2():
    validated = _run("validate")
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert "v2 corpus valid" in validated.stdout
    planned = _run("dry-run", "--hosts", "codex", "--locales", "en", "--splits", "holdout", "--json")
    assert planned.returncode == 0, planned.stdout + planned.stderr
    attempts = json.loads(planned.stdout)
    assert len(attempts) == 18
    assert {row["corpus_version"] for row in attempts} == {"v2"}
    assert {row["expected_route"] for row in attempts} == {"trigger", "no_trigger"}


def test_wrong_version_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp)
        path = pathlib.Path(tmp) / "en" / "calibration.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = "v1"
        path.write_text(json.dumps(payload), encoding="utf-8")
        _corpus, problems = rt.load_corpus(tmp)
        assert any("does not match current release corpus" in problem for problem in problems)


def test_count_and_overlap_mutations_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp)
        path = pathlib.Path(tmp) / "en" / "holdout.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["prompts"].pop()
        path.write_text(json.dumps(payload), encoding="utf-8")
        _corpus, problems = rt.load_corpus(tmp)
        assert any("expected exactly 6" in problem for problem in problems)

    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp)
        cal = pathlib.Path(tmp) / "zh-TW" / "calibration.json"
        hold = pathlib.Path(tmp) / "zh-TW" / "holdout.json"
        cal_payload = json.loads(cal.read_text(encoding="utf-8"))
        hold_payload = json.loads(hold.read_text(encoding="utf-8"))
        hold_payload["prompts"][0]["text"] = cal_payload["prompts"][0]["text"]
        hold.write_text(json.dumps(hold_payload, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(tmp)
        assert any("share normalized prompt text" in problem for problem in problems)


def test_id_and_boundary_coverage_mutations_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp)
        path = pathlib.Path(tmp) / "zh-CN" / "calibration.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["prompts"][0]["id"] = "v2-zh-CN-cal-dec-99"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(tmp)
        assert any("ids do not match the v2 locale/split sequence" in problem for problem in problems)

    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp)
        path = pathlib.Path(tmp) / "zh-CN" / "holdout.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for prompt in payload["prompts"]:
            if prompt["class"] == "adjacent_negative":
                prompt["near_miss_of"] = "general"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(tmp)
        assert any("must cover every near-miss target" in problem for problem in problems)


def test_attempts_are_versioned_and_derive_expected_route():
    corpus, problems = rt.load_corpus()
    assert problems == []
    positive = next(row for row in corpus["en"]["holdout"] if row["class"] == "candidate_discovery")
    negative = next(row for row in corpus["en"]["holdout"] if row["class"] == "adjacent_negative")
    assert _attempt(corpus, "en", "holdout", positive)["expected_route"] == "trigger"
    assert _attempt(corpus, "en", "holdout", negative)["expected_route"] == "no_trigger"
    assert _attempt(corpus, "en", "holdout", positive)["corpus_version"] == "v2"


def test_result_reader_rejects_unversioned_or_stale_rows():
    corpus, problems = rt.load_corpus()
    assert problems == []
    prompt = corpus["en"]["holdout"][0]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "attempts.jsonl"
        row = _attempt(corpus, "en", "holdout", prompt)
        row.pop("corpus_version")
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        attempts, read_problems = rt.read_result_file(path)
        assert attempts == []
        assert any("missing field" in problem for problem in read_problems)

        stale = _attempt(corpus, "en", "holdout", prompt)
        stale["corpus_version"] = "v1"
        path.write_text(json.dumps(stale) + "\n", encoding="utf-8")
        attempts, read_problems = rt.read_result_file(path)
        assert attempts == []
        assert any("does not match current gate" in problem for problem in read_problems)


def test_result_reader_and_corpus_binding_match_the_attempt_schema():
    corpus, problems = rt.load_corpus()
    assert problems == []
    prompt = corpus["en"]["holdout"][0]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "attempts.jsonl"
        for field in ("schema_version", "ts"):
            row = _attempt(corpus, "en", "holdout", prompt)
            row.pop(field)
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            attempts, read_problems = rt.read_result_file(path)
            assert attempts == []
            assert any("missing field" in problem and field in problem for problem in read_problems)

        row = _attempt(corpus, "en", "holdout", prompt)
        row["unexpected"] = True
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        attempts, read_problems = rt.read_result_file(path)
        assert attempts == []
        assert any("unknown field" in problem for problem in read_problems)

        row = _attempt(corpus, "en", "holdout", prompt)
        row["class"] = "adjacent_negative"
        row["expected_route"] = "no_trigger"
        assert rt.validate_attempts_against_corpus(corpus, [row])


def test_accuracy_gate_requires_a_complete_perfect_class():
    ids = [f"p{i}" for i in range(rt.PROMPTS_PER_CLASS)]
    perfect = {pid: {"actual_route": "trigger"} for pid in ids}
    result = rt.evaluate_class(ids, perfect, "named_security_decision")
    assert result["accuracy"] == 1.0
    assert result["verdict"] == "pass"

    one_wrong = dict(perfect)
    one_wrong[ids[0]] = {"actual_route": "no_trigger"}
    result = rt.evaluate_class(ids, one_wrong, "named_security_decision")
    assert result["accuracy"] == 5 / 6
    assert result["verdict"] == "fail"

    incomplete = rt.evaluate_class(ids, {ids[0]: {"actual_route": "trigger"}}, "named_security_decision")
    assert incomplete["status"] == "incomplete"
    assert incomplete["verdict"] is None


def test_perfect_holdout_score_reports_one_hundred_percent():
    corpus, problems = rt.load_corpus()
    assert problems == []
    with tempfile.TemporaryDirectory() as tmp:
        result_path = pathlib.Path(tmp) / "results.jsonl"
        for prompt in corpus["en"]["holdout"]:
            rt.append_attempt(result_path, _attempt(corpus, "en", "holdout", prompt))
        scored = _run(
            "score", "--result-file", str(result_path), "--hosts", "codex",
            "--locales", "en", "--splits", "holdout", "--json")
        assert scored.returncode == 0, scored.stdout + scored.stderr
        rows = json.loads(scored.stdout)
        assert len(rows) == 1
        assert all(cell["accuracy"] == 1.0 and cell["verdict"] == "pass"
                   for cell in rows[0]["classes"].values())


def test_score_refuses_a_stale_correction_after_an_otherwise_green_history():
    corpus, problems = rt.load_corpus()
    assert problems == []
    with tempfile.TemporaryDirectory() as tmp:
        result_path = pathlib.Path(tmp) / "results.jsonl"
        for prompt in corpus["en"]["holdout"]:
            rt.append_attempt(result_path, _attempt(corpus, "en", "holdout", prompt))
        correction = _attempt(corpus, "en", "holdout", corpus["en"]["holdout"][0], "no_trigger")
        correction["corpus_version"] = "v1"
        rt.append_attempt(result_path, correction)

        scored = _run(
            "score", "--result-file", str(result_path), "--hosts", "codex",
            "--locales", "en", "--splits", "holdout", "--json")
        assert scored.returncode == 2, (scored.stdout, scored.stderr)
        assert scored.stdout == "", "a refused JSON score must not emit a green-looking report"
        assert "refusing to score" in scored.stderr


def test_runner_cannot_reach_a_network_or_spawn_a_host():
    source = RUNNER.read_text(encoding="utf-8")
    forbidden = ("import requests", "import urllib", "import socket", "import subprocess", "os.system(")
    assert not any(token in source for token in forbidden)


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} trigger-v2 tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
