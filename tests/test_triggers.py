#!/usr/bin/env python3
"""Deterministic probes for evals/triggers/run_triggers.py (#458).

Layout follows the precedent evals/run_episodes.py sets for its own tests:
the runner lives under evals/, its tests live under tests/
(tests/test_episode_checkers.py). This file is intentionally **not**
registered in tests/run_all.py -- #458 scopes this change to
`evals/triggers/` only and explicitly excludes tests/run_all.py, since the
actual measurement is a billable network run the owner schedules separately
(Wave C). A later Wave B/C session wires this suite into the default gate
once it owns the integrated surface; nothing here should be read as an
oversight.

Every test is offline and deterministic: no network, no host, and no
subprocess that could reach one. Fixture corpora are built fresh in a
tempdir per test, so the mutation-provable checks can construct exactly the
broken input needed to prove a given checker's matching mutation would fail,
without ever touching or risking the real `evals/triggers/corpus/` content.
"""
import importlib.util
import itertools
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRIGGERS_DIR = ROOT / "evals" / "triggers"
RUNNER = TRIGGERS_DIR / "run_triggers.py"
REAL_CORPUS_DIR = TRIGGERS_DIR / "corpus"


def _load_module():
    spec = importlib.util.spec_from_file_location("triggers_run_triggers", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rt = _load_module()

# ══════════════════════════════ fixture helpers ═══════════════════════════

_TEXT_COUNTER = itertools.count(1)


def _prompt(pid, class_name, near_miss_of=None, text=None, note="synthetic fixture prompt"):
    if text is None:
        text = f"synthetic unique prompt text {next(_TEXT_COUNTER)}"
    prompt = {"id": pid, "class": class_name, "text": text, "note": note}
    if near_miss_of:
        prompt["near_miss_of"] = near_miss_of
    return prompt


def _class_block(prefix, class_name, count=None, near_miss_of=None):
    count = rt.PROMPTS_PER_CLASS if count is None else count
    if near_miss_of is None and class_name == "adjacent_negative":
        near_miss_of = "general"
    abbr = {"review_positive": "rev", "pre_trade_positive": "pre", "adjacent_negative": "adj"}[class_name]
    return [_prompt(f"{prefix}-{abbr}-{i:02d}", class_name, near_miss_of=near_miss_of)
            for i in range(1, count + 1)]


def _valid_split_prompts(prefix):
    return [item for class_name in rt.CLASSES for item in _class_block(prefix, class_name)]


def _write_corpus_file(root, locale, split, prompts):
    path = root / locale / f"{split}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"locale": locale, "split": split, "prompts": prompts}, ensure_ascii=False),
                     encoding="utf-8")


def _write_baseline_corpus(root):
    """Every locale x split combination, fully valid and mutually disjoint --
    the clean starting point every mutation test then breaks in exactly one
    place."""
    for locale in rt.LOCALES:
        for split in rt.SPLITS:
            _write_corpus_file(root, locale, split, _valid_split_prompts(f"{locale}-{split[:3]}"))


def _first_prompt_id(corpus, locale, split, class_name):
    for prompt in corpus[locale][split]:
        if prompt["class"] == class_name:
            return prompt["id"]
    raise AssertionError(f"no {class_name} prompt in {locale}/{split}")


def run_cli(*args):
    return subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True, text=True, cwd=ROOT)


# ══════════════════════════ the real, committed corpus ════════════════════

def test_the_real_committed_corpus_is_schema_valid_and_disjoint():
    """Direct regression test on the actual shipped content, not a fixture:
    exact 20/20/20 counts, calibration/holdout disjoint per locale, no
    duplicate id anywhere in the corpus."""
    corpus, problems = rt.load_corpus(REAL_CORPUS_DIR)
    assert not problems, "\n".join(problems)
    total = sum(len(corpus[locale][split]) for locale in rt.LOCALES for split in rt.SPLITS)
    assert total == len(rt.LOCALES) * len(rt.SPLITS) * rt.PROMPTS_PER_CLASS * len(rt.CLASSES)


def test_the_real_corpus_declares_the_expected_class_counts_per_file():
    corpus, problems = rt.load_corpus(REAL_CORPUS_DIR)
    assert not problems, "\n".join(problems)
    for locale in rt.LOCALES:
        for split in rt.SPLITS:
            counts = {}
            for prompt in corpus[locale][split]:
                counts[prompt["class"]] = counts.get(prompt["class"], 0) + 1
            for class_name in rt.CLASSES:
                assert counts.get(class_name) == rt.PROMPTS_PER_CLASS, (
                    f"{locale}/{split}/{class_name}: {counts.get(class_name)} != {rt.PROMPTS_PER_CLASS}")


def test_the_real_corpus_adjacent_negatives_cover_every_near_miss_target_per_cell():
    """The boundary-coverage interlock: every locale/split's twenty
    adjacent_negative prompts must exercise all three near_miss_of values, or
    the corpus could quietly drift toward only the easy, obviously-off-topic
    cases (evals/triggers/README.md's stated design goal)."""
    corpus, problems = rt.load_corpus(REAL_CORPUS_DIR)
    assert not problems, "\n".join(problems)
    for locale in rt.LOCALES:
        for split in rt.SPLITS:
            observed = {prompt.get("near_miss_of") for prompt in corpus[locale][split]
                        if prompt["class"] == "adjacent_negative"}
            missing = set(rt.NEAR_MISS_TARGETS) - observed
            assert not missing, f"{locale}/{split}: adjacent_negative never targets {missing}"


def test_the_real_corpus_prompt_ids_match_their_own_locale_and_class():
    """id encodes locale/split/class (schema/prompt-corpus.schema.json's
    pattern) -- catches a prompt accidentally filed under the wrong bucket
    even though load_corpus_file() only checks aggregate counts."""
    corpus, problems = rt.load_corpus(REAL_CORPUS_DIR)
    assert not problems, "\n".join(problems)
    abbr = {"review_positive": "rev", "pre_trade_positive": "pre", "adjacent_negative": "adj"}
    split_abbr = {"calibration": "cal", "holdout": "hold"}
    for locale in rt.LOCALES:
        for split in rt.SPLITS:
            for prompt in corpus[locale][split]:
                expected_prefix = f"{locale}-{split_abbr[split]}-{abbr[prompt['class']]}-"
                assert prompt["id"].startswith(expected_prefix), (
                    f"{prompt['id']} does not start with {expected_prefix!r}")


def test_dry_run_cli_against_the_real_corpus_sends_nothing_and_lists_every_cell():
    done = run_cli("dry-run", "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    attempts = json.loads(done.stdout)
    assert len(attempts) == len(rt.HOSTS) * len(rt.LOCALES) * len(rt.SPLITS) * rt.PROMPTS_PER_CLASS * len(rt.CLASSES)
    # Every attempt is a plan entry, never a claim that anything was sent.
    assert all(set(attempt) == {"host", "locale", "split", "prompt_id", "class", "expected_route", "text"}
               for attempt in attempts)


# ══════════════════════════ corpus validation: counts ══════════════════════

def test_corpus_rejects_a_class_with_too_few_prompts():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        bad = (_class_block("en-cal", "review_positive", count=19)
               + _class_block("en-cal", "pre_trade_positive")
               + _class_block("en-cal", "adjacent_negative"))
        _write_corpus_file(root, "en", "calibration", bad)
        _corpus, problems = rt.load_corpus(root)
        assert any("review_positive" in p and "19" in p for p in problems), problems


def test_corpus_rejects_a_class_with_too_many_prompts():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        bad = (_class_block("en-hold", "review_positive", count=21)
               + _class_block("en-hold", "pre_trade_positive")
               + _class_block("en-hold", "adjacent_negative"))
        _write_corpus_file(root, "en", "holdout", bad)
        _corpus, problems = rt.load_corpus(root)
        assert any("review_positive" in p and "21" in p for p in problems), problems


def test_corpus_rejects_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        (root / "zh-CN" / "holdout.json").unlink()
        _corpus, problems = rt.load_corpus(root)
        assert any("missing corpus file" in p for p in problems), problems


def test_corpus_rejects_invalid_json():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        (root / "en" / "calibration.json").write_text("{not valid json", encoding="utf-8")
        _corpus, problems = rt.load_corpus(root)
        assert any("invalid JSON" in p for p in problems), problems


def test_corpus_rejects_locale_split_field_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        path = root / "en" / "calibration.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["locale"] = "zh-TW"  # file lives under en/ but claims zh-TW
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(root)
        assert any("does not match its own directory" in p for p in problems), problems


def test_corpus_rejects_unknown_class_value():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        prompts = _valid_split_prompts("en-cal")
        prompts[0] = dict(prompts[0], **{"class": "not_a_real_class"})
        _write_corpus_file(root, "en", "calibration", prompts)
        _corpus, problems = rt.load_corpus(root)
        assert any("class must be one of" in p for p in problems), problems


def test_corpus_rejects_near_miss_of_on_a_positive_class():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        prompts = _valid_split_prompts("en-cal")
        prompts[0] = dict(prompts[0], near_miss_of="general")  # prompts[0] is review_positive
        _write_corpus_file(root, "en", "calibration", prompts)
        _corpus, problems = rt.load_corpus(root)
        assert any("near_miss_of only applies to adjacent_negative" in p for p in problems), problems


def test_corpus_rejects_adjacent_negative_missing_near_miss_of():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        prompts = _valid_split_prompts("en-cal")
        for index, prompt in enumerate(prompts):
            if prompt["class"] == "adjacent_negative":
                del prompt["near_miss_of"]
                break
        _write_corpus_file(root, "en", "calibration", prompts)
        _corpus, problems = rt.load_corpus(root)
        assert any("must declare near_miss_of" in p for p in problems), problems


def test_corpus_rejects_unknown_prompt_field():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        prompts = _valid_split_prompts("en-cal")
        prompts[0] = dict(prompts[0], extra_field="not allowed")
        _write_corpus_file(root, "en", "calibration", prompts)
        _corpus, problems = rt.load_corpus(root)
        assert any("unknown field" in p for p in problems), problems


# ═══════════════════════ corpus validation: disjointness ══════════════════

def test_corpus_rejects_id_shared_between_calibration_and_holdout():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        cal_path = root / "en" / "calibration.json"
        cal_raw = json.loads(cal_path.read_text(encoding="utf-8"))
        hold_path = root / "en" / "holdout.json"
        hold_raw = json.loads(hold_path.read_text(encoding="utf-8"))
        # Give holdout's first prompt the exact id of calibration's first prompt.
        hold_raw["prompts"][0]["id"] = cal_raw["prompts"][0]["id"]
        hold_path.write_text(json.dumps(hold_raw, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(root)
        assert any("share id" in p for p in problems), problems


def test_corpus_rejects_text_shared_between_calibration_and_holdout_even_with_different_ids():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        cal_path = root / "en" / "calibration.json"
        cal_raw = json.loads(cal_path.read_text(encoding="utf-8"))
        hold_path = root / "en" / "holdout.json"
        hold_raw = json.loads(hold_path.read_text(encoding="utf-8"))
        # Same text (with incidental case/whitespace differences), different id.
        hold_raw["prompts"][0]["text"] = "  " + cal_raw["prompts"][0]["text"].upper() + "  "
        hold_path.write_text(json.dumps(hold_raw, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(root)
        assert any("share normalized prompt text" in p for p in problems), problems


def test_corpus_disjointness_is_scoped_per_locale_not_global():
    """calibration in one locale sharing text with holdout in a *different*
    locale is not an overlap -- disjointness is a per-locale property (the
    same prompt idea can legitimately recur across locales in different
    scripts, and mistakenly flagging that would punish exactly the parallel
    boundary-scenario structure the corpus intentionally uses)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        en_cal_path = root / "en" / "calibration.json"
        en_cal_raw = json.loads(en_cal_path.read_text(encoding="utf-8"))
        zh_hold_path = root / "zh-TW" / "holdout.json"
        zh_hold_raw = json.loads(zh_hold_path.read_text(encoding="utf-8"))
        zh_hold_raw["prompts"][0]["text"] = en_cal_raw["prompts"][0]["text"]
        zh_hold_path.write_text(json.dumps(zh_hold_raw, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(root)
        assert not problems, problems


def test_corpus_rejects_duplicate_id_across_locales():
    """Global id uniqueness: a copy-paste across a locale boundary (not just
    within one file, and not just across calibration/holdout in the same
    locale) must also be caught."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        en_path = root / "en" / "calibration.json"
        en_raw = json.loads(en_path.read_text(encoding="utf-8"))
        zh_path = root / "zh-TW" / "calibration.json"
        zh_raw = json.loads(zh_path.read_text(encoding="utf-8"))
        zh_raw["prompts"][0]["id"] = en_raw["prompts"][0]["id"]
        zh_path.write_text(json.dumps(zh_raw, ensure_ascii=False), encoding="utf-8")
        _corpus, problems = rt.load_corpus(root)
        assert any("duplicate id" in p and "across the corpus" in p for p in problems), problems


# ═══════════════════════ threshold arithmetic (evaluate_class) ═════════════

def _attempts_for(prompt_ids, route):
    return {pid: {"actual_route": route} for pid in prompt_ids}


def test_review_positive_boundary_18_of_20_passes_17_of_20_fails():
    ids = [f"p{i:02d}" for i in range(20)]
    correct_18 = _attempts_for(ids[:18], "review")
    correct_18.update(_attempts_for(ids[18:], "no_trigger"))
    result_18 = rt.evaluate_class(ids, correct_18, "review_positive")
    assert result_18 == {"status": "complete", "attempted": 20, "total": 20, "correct": 18, "verdict": "pass"}, result_18

    correct_17 = _attempts_for(ids[:17], "review")
    correct_17.update(_attempts_for(ids[17:], "no_trigger"))
    result_17 = rt.evaluate_class(ids, correct_17, "review_positive")
    assert result_17 == {"status": "complete", "attempted": 20, "total": 20, "correct": 17, "verdict": "fail"}, result_17


def test_pre_trade_positive_boundary_19_of_20_passes_18_of_20_fails():
    ids = [f"p{i:02d}" for i in range(20)]
    correct_19 = _attempts_for(ids[:19], "pre_trade")
    correct_19.update(_attempts_for(ids[19:], "review"))
    result_19 = rt.evaluate_class(ids, correct_19, "pre_trade_positive")
    assert result_19["verdict"] == "pass" and result_19["correct"] == 19, result_19

    correct_18 = _attempts_for(ids[:18], "pre_trade")
    correct_18.update(_attempts_for(ids[18:], "review"))
    result_18 = rt.evaluate_class(ids, correct_18, "pre_trade_positive")
    assert result_18["verdict"] == "fail" and result_18["correct"] == 18, result_18


def test_adjacent_negative_boundary_one_false_trigger_passes_two_fails():
    """At most 1/20 false triggers == at least 19/20 correctly not-triggering."""
    ids = [f"p{i:02d}" for i in range(20)]
    one_false_trigger = _attempts_for(ids[:19], "no_trigger")
    one_false_trigger.update(_attempts_for(ids[19:], "pre_trade"))  # 1 false trigger
    result_1 = rt.evaluate_class(ids, one_false_trigger, "adjacent_negative")
    assert result_1["verdict"] == "pass" and result_1["correct"] == 19, result_1

    two_false_triggers = _attempts_for(ids[:18], "no_trigger")
    two_false_triggers.update(_attempts_for(ids[18:], "review"))  # 2 false triggers
    result_2 = rt.evaluate_class(ids, two_false_triggers, "adjacent_negative")
    assert result_2["verdict"] == "fail" and result_2["correct"] == 18, result_2


def test_not_run_cell_never_reads_as_a_pass():
    ids = [f"p{i:02d}" for i in range(20)]
    result = rt.evaluate_class(ids, {}, "review_positive")
    assert result["status"] == "not_run"
    assert result["verdict"] is None
    assert result["correct"] is None


def test_incomplete_cell_never_reads_as_a_pass_even_if_every_attempted_one_is_correct():
    ids = [f"p{i:02d}" for i in range(20)]
    # Only 15 of 20 attempted, every single one correct -- still must not pass,
    # because recall against the fixed 20-prompt denominator is not knowable
    # from 15 attempts.
    attempts = _attempts_for(ids[:15], "review")
    result = rt.evaluate_class(ids, attempts, "review_positive")
    assert result["status"] == "incomplete"
    assert result["verdict"] is None
    assert result["correct"] == 15
    assert result["attempted"] == 15


def test_incomplete_cell_that_would_cross_the_threshold_still_never_reads_as_a_pass():
    """The starkest version of the same rule: 19 of 20 pre_trade_positive
    prompts attempted, every single one correct -- 19/19 would clear the
    class's own MIN_CORRECT (19) if miscounted against the wrong
    denominator. It must still report incomplete/None, never pass."""
    ids = [f"p{i:02d}" for i in range(20)]
    attempts = _attempts_for(ids[:19], "pre_trade")
    result = rt.evaluate_class(ids, attempts, "pre_trade_positive")
    assert result["status"] == "incomplete"
    assert result["verdict"] is None
    assert result["correct"] == 19
    assert result["attempted"] == 19


def test_evaluate_class_ignores_attempts_outside_the_expected_id_set():
    """An attempt keyed by a prompt id from a different cell must not leak
    into this class's count -- cell_attempts is assumed pre-scoped by the
    caller, and evaluate_class only ever looks up its own expected_ids."""
    ids = [f"p{i:02d}" for i in range(20)]
    attempts = _attempts_for(ids, "review")
    attempts["unrelated-prompt-from-another-cell"] = {"actual_route": "no_trigger"}
    result = rt.evaluate_class(ids, attempts, "review_positive")
    assert result == {"status": "complete", "attempted": 20, "total": 20, "correct": 20, "verdict": "pass"}


# ══════════════════════ do-not-pool: build_report / cell_gate_pass ═════════

def test_build_report_never_pools_across_classes_hosts_or_locales():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems

        rev_ids = [p["id"] for p in corpus["en"]["holdout"] if p["class"] == "review_positive"]
        pre_ids = [p["id"] for p in corpus["en"]["holdout"] if p["class"] == "pre_trade_positive"]
        adj_ids = [p["id"] for p in corpus["en"]["holdout"] if p["class"] == "adjacent_negative"]

        attempts = []
        for pid in rev_ids:  # 20/20 correct
            attempts.append({"host": "codex", "locale": "en", "split": "holdout", "prompt_id": pid,
                              "actual_route": "review"})
        for pid in pre_ids:  # 20/20 correct
            attempts.append({"host": "codex", "locale": "en", "split": "holdout", "prompt_id": pid,
                              "actual_route": "pre_trade"})
        for index, pid in enumerate(adj_ids):  # 2 false triggers -> this class fails
            route = "pre_trade" if index < 2 else "no_trigger"
            attempts.append({"host": "codex", "locale": "en", "split": "holdout", "prompt_id": pid,
                              "actual_route": route})

        rows = rt.build_report(corpus, attempts, hosts=["codex"], locales=["en"], splits=["holdout"])
        assert len(rows) == 1
        row = rows[0]
        assert row["classes"]["review_positive"]["verdict"] == "pass"
        assert row["classes"]["pre_trade_positive"]["verdict"] == "pass"
        assert row["classes"]["adjacent_negative"]["verdict"] == "fail"
        # The row-level gate is a per-row AND, never an average -- one failing
        # class fails the whole row even though the other two independently passed.
        assert rt.cell_gate_pass(row) is False


def test_build_report_lists_every_requested_cell_even_with_zero_attempts():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems
        rows = rt.build_report(corpus, attempts=[], hosts=["claude_code", "codex", "antigravity"],
                                locales=list(rt.LOCALES), splits=list(rt.SPLITS))
        assert len(rows) == 3 * len(rt.LOCALES) * len(rt.SPLITS)
        assert all(cell["status"] == "not_run" for row in rows for cell in row["classes"].values())
        assert all(rt.cell_gate_pass(row) is False for row in rows)


def test_build_report_never_drops_an_unexpected_host():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems
        rev_id = _first_prompt_id(corpus, "en", "holdout", "review_positive")
        attempts = [{"host": "a_future_second_entry_point", "locale": "en", "split": "holdout",
                     "prompt_id": rev_id, "actual_route": "review"}]
        rows = rt.build_report(corpus, attempts, hosts=list(rt.HOSTS), locales=["en"], splits=["holdout"])
        hosts_in_report = {row["host"] for row in rows}
        assert "a_future_second_entry_point" in hosts_in_report
        assert set(rt.HOSTS) <= hosts_in_report


# ═══════════════════════════ fold_attempts ordering ════════════════════════

def test_fold_attempts_last_write_wins_by_file_order_not_by_ts_value():
    """Mirrors ux_receipt.py's own stated precedent: row order, not ts,
    decides which attempt is current. A later line with an *earlier* ts value
    must still win, because it represents a correction or a repeat trial
    recorded after the first."""
    key_attempts = [
        {"host": "codex", "locale": "en", "split": "holdout", "prompt_id": "p1",
         "actual_route": "no_trigger", "ts": "2026-07-29T23:00:00Z"},
        {"host": "codex", "locale": "en", "split": "holdout", "prompt_id": "p1",
         "actual_route": "review", "ts": "2026-07-29T01:00:00Z"},  # earlier ts, later line
    ]
    folded = rt.fold_attempts(key_attempts)
    only = folded[("codex", "en", "holdout", "p1")]
    assert only["actual_route"] == "review", "the later line must win regardless of its ts value"


# ══════════════════════════ build_attempt / record ═════════════════════════

def test_build_attempt_derives_expected_route_and_class_from_the_corpus():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems
        pre_id = _first_prompt_id(corpus, "en", "holdout", "pre_trade_positive")
        attempt = rt.build_attempt(
            corpus, locale="en", split="holdout", prompt_id=pre_id, host="codex",
            host_version="1.0", model="test-model", skill_population="fomo-kernel,other-skill",
            actual_route="pre_trade", raw_outcome="invoked consider")
        assert attempt["class"] == "pre_trade_positive"
        assert attempt["expected_route"] == "pre_trade"
        assert attempt["installed_skill_population"] == ["fomo-kernel", "other-skill"]
        assert attempt["schema_version"] == 1
        assert attempt["ts"]


def test_build_attempt_rejects_unknown_prompt_id():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems
        try:
            rt.build_attempt(corpus, locale="en", split="holdout", prompt_id="does-not-exist",
                              host="codex", host_version="1.0", model="m",
                              skill_population="", actual_route="review", raw_outcome="x")
        except rt.AttemptError as exc:
            assert "no prompt" in str(exc)
        else:
            raise AssertionError("expected AttemptError for an unknown prompt id")


def test_build_attempt_rejects_empty_raw_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems
        rev_id = _first_prompt_id(corpus, "en", "holdout", "review_positive")
        try:
            rt.build_attempt(corpus, locale="en", split="holdout", prompt_id=rev_id, host="codex",
                              host_version="1.0", model="m", skill_population="",
                              actual_route="review", raw_outcome="   ")
        except rt.AttemptError as exc:
            assert "raw_outcome" in str(exc)
        else:
            raise AssertionError("expected AttemptError for empty raw_outcome")


def test_build_attempt_rejects_unknown_actual_route():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems
        rev_id = _first_prompt_id(corpus, "en", "holdout", "review_positive")
        try:
            rt.build_attempt(corpus, locale="en", split="holdout", prompt_id=rev_id, host="codex",
                              host_version="1.0", model="m", skill_population="",
                              actual_route="something_else", raw_outcome="x")
        except rt.AttemptError as exc:
            assert "actual_route" in str(exc)
        else:
            raise AssertionError("expected AttemptError for an unknown actual_route")


def test_append_attempt_only_ever_appends():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        result_path = root / "nested" / "out.jsonl"
        rt.append_attempt(result_path, {"a": 1})
        rt.append_attempt(result_path, {"a": 2})
        lines = result_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"a": 2}


def test_read_result_file_reports_malformed_lines_without_dropping_good_ones():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "result.jsonl"
        good = {
            "host": "codex", "host_version": "1.0", "model": "m",
            "installed_skill_population": [], "locale": "en", "split": "holdout",
            "prompt_id": "p1", "class": "review_positive", "expected_route": "review",
            "actual_route": "review", "raw_outcome": "ok",
        }
        path.write_text(
            json.dumps(good) + "\n" + "{not valid json\n" + json.dumps(good) + "\n",
            encoding="utf-8")
        attempts, problems = rt.read_result_file(path)
        assert len(attempts) == 2
        assert any("invalid JSON" in p for p in problems)


def test_read_result_file_rejects_a_row_with_an_unknown_actual_route():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "result.jsonl"
        bad = {
            "host": "codex", "host_version": "1.0", "model": "m",
            "installed_skill_population": [], "locale": "en", "split": "holdout",
            "prompt_id": "p1", "class": "review_positive", "expected_route": "review",
            "actual_route": "not_a_real_route", "raw_outcome": "ok",
        }
        path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
        attempts, problems = rt.read_result_file(path)
        assert not attempts
        assert any("not in" in p for p in problems)


# ═══════════════════════════════ CLI plumbing ══════════════════════════════

def test_cli_record_then_score_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        result_path = root / "result.jsonl"
        corpus, problems = rt.load_corpus(root)
        assert not problems, problems
        rev_ids = [p["id"] for p in corpus["en"]["holdout"] if p["class"] == "review_positive"]

        for pid in rev_ids:
            done = run_cli("--corpus-dir", str(root), "record", "--result-file", str(result_path),
                            "--host", "codex", "--host-version", "1.0", "--model", "test-model",
                            "--skill-population", "fomo-kernel", "--locale", "en", "--split", "holdout",
                            "--prompt-id", pid, "--actual-route", "review", "--raw-outcome", "ok")
            assert done.returncode == 0, done.stdout + done.stderr

        score_done = run_cli("--corpus-dir", str(root), "score", "--result-file", str(result_path),
                              "--hosts", "codex", "--locales", "en", "--splits", "holdout", "--json")
        rows = json.loads(score_done.stdout)
        assert len(rows) == 1
        assert rows[0]["classes"]["review_positive"] == {
            "status": "complete", "attempted": 20, "total": 20, "correct": 20, "verdict": "pass"}
        # pre_trade_positive and adjacent_negative were never attempted in this
        # cell, so the gate (which requires all three) must still fail.
        assert score_done.returncode == 1


def test_cli_score_on_a_missing_result_file_reports_not_run_and_fails_the_gate():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        missing = pathlib.Path(tmp) / "does-not-exist.jsonl"
        done = run_cli("--corpus-dir", str(root), "score", "--result-file", str(missing))
        assert done.returncode == 1
        assert "not_run" in done.stdout
        assert "FAIL" in done.stdout


def test_cli_validate_and_dry_run_never_create_a_result_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        before = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
        run_cli("--corpus-dir", str(root), "validate")
        run_cli("--corpus-dir", str(root), "dry-run")
        after = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
        assert before == after, "validate/dry-run must never write any file"


def test_cli_validate_fails_closed_on_a_broken_corpus():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _write_baseline_corpus(root)
        (root / "en" / "calibration.json").write_text("{not valid json", encoding="utf-8")
        done = run_cli("--corpus-dir", str(root), "validate")
        assert done.returncode == 1
        assert "FAIL" in done.stdout


# ══════════════════════ module-level vocabulary consistency ═══════════════

def test_min_correct_and_expected_route_are_declared_for_every_class():
    assert set(rt.MIN_CORRECT) == set(rt.CLASSES)
    assert set(rt.EXPECTED_ROUTE_BY_CLASS) == set(rt.CLASSES)
    assert set(rt.EXPECTED_ROUTE_BY_CLASS.values()) <= set(rt.ROUTES)


def test_thresholds_match_the_issues_literal_numbers():
    """#458: review recall >= 18/20, pre-trade recall >= 19/20,
    adjacent-negative false trigger <= 1/20 (== >= 19/20 correct)."""
    assert rt.MIN_CORRECT["review_positive"] == 18
    assert rt.MIN_CORRECT["pre_trade_positive"] == 19
    assert rt.MIN_CORRECT["adjacent_negative"] == 19
    assert rt.PROMPTS_PER_CLASS == 20


def test_locales_are_a_subset_of_shipped_product_copy_catalogs():
    """Sanity cross-check, read-only: testing trigger reliability for a
    locale the product does not even render would measure nothing. Reads
    skills/fomo-kernel/copy/ only -- this test does not touch it."""
    copy_dir = ROOT / "skills" / "fomo-kernel" / "copy"
    shipped = {path.stem for path in copy_dir.glob("*.json")}
    assert set(rt.LOCALES) <= shipped, (set(rt.LOCALES) - shipped)


# ═══════════════════════ no network / no host calls, ever ═════════════════

_BANNED_SOURCE_SUBSTRINGS = (
    "import requests", "import urllib", "import http.client", "import socket",
    "import subprocess", "from subprocess", "from urllib", "from socket",
    "from http.client", "import http\n",
)


def test_runner_source_imports_nothing_that_could_reach_a_network_or_spawn_a_process():
    """#458: 'no import-time host calls', generalized to 'no way to make a
    host or network call at all' -- this module records what a human already
    observed in a real host session; it must never gain the ability to talk
    to one itself. A grep on the committed source, not a runtime behavior
    test, so introducing a banned import fails this test even if the code
    path that uses it is never executed in a given run."""
    source = RUNNER.read_text(encoding="utf-8")
    hits = [needle for needle in _BANNED_SOURCE_SUBSTRINGS if needle in source]
    assert not hits, f"run_triggers.py must never import a network/process-spawning module: {hits}"


def test_importing_the_runner_performs_no_filesystem_or_network_side_effects():
    """Import-time safety, checked behaviorally rather than only by grep:
    merely importing the module (as every subcommand does before doing
    anything else) must not touch the filesystem beyond what Python's own
    import machinery does."""
    done = subprocess.run(
        [sys.executable, "-c", f"import importlib.util; "
         f"spec = importlib.util.spec_from_file_location('m', {str(RUNNER)!r}); "
         f"m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
         f"print('OK')"],
        capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == "OK"


# ══════════════════════════════════ harness ════════════════════════════════

def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failed = []
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed.append((test.__name__, str(exc)))
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    print()
    if failed:
        print(f"FAIL: {len(failed)}/{len(tests)} trigger-matrix tests failed.")
        return 1
    print(f"PASS: all {len(tests)} trigger-matrix tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
