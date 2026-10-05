#!/usr/bin/env python3
"""Opt-in A01/A07/A10 no-book Generic Parity fast probe (#715).

The command freezes the four host-side authorities, captures one exact answer
per synthetic case, runs mechanical gates before any judge call, then asks the
existing repeated structured judge to grade the six G0 axes. It writes only to
the explicit output directory and never touches coach state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import generic_parity as GP  # noqa: E402
import judge_episodes as BASE  # noqa: E402


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True,
                          check=True).stdout.strip()


def repository_proof():
    return {"sha": _git("rev-parse", "HEAD"),
            "dirty": bool(_git("status", "--porcelain"))}


def model_family(model, backend):
    lowered = model.lower()
    if lowered.startswith("gemini-"):
        return "gemini"
    if lowered.startswith("gpt-"):
        return "gpt-oss"
    if lowered.startswith("claude-"):
        return "anthropic"
    return backend


def _models(args, *, live):
    def resolve(name, override):
        try:
            backend, model = BASE.resolve_backend(name)
        except SystemExit:
            if live:
                raise
            return name, override or "unavailable"
        return backend, override or model
    generator_backend, generator_model = resolve(args.generator_backend, args.generator_model)
    judge_backend, judge_model = resolve(args.judge_backend, args.judge_model)
    return generator_backend, args.generator_model or generator_model, \
        judge_backend, args.judge_model or judge_model


def plan(cases, args):
    witness_errors = GP.validate_witness_bank()
    if witness_errors:
        raise ValueError("; ".join(witness_errors))
    bundle = GP.instruction_bundle()
    generator_backend, generator_model, judge_backend, judge_model = _models(args, live=False)
    mutation = next(row for row in GP.load_witnesses() if row["id"] == "question_first")
    mutation_findings = GP.deterministic_findings(mutation["answer"], cases[0]["prompt"])
    return {
        "cases": [case["id"] for case in cases],
        "instruction_paths": [item["path"] for item in bundle],
        "instruction_bundle_sha256": GP.digest("".join(item["sha256"] for item in bundle)),
        "generator": {"backend": generator_backend, "model": generator_model},
        "judge": {"backend": judge_backend, "model": judge_model, "runs_per_case": args.runs},
        "planned_model_calls": len(cases) * (1 + args.runs),
        "controlled_mutation": {"case_id": "A01", "witness_id": mutation["id"],
                                "judge_calls": 0, "caught": bool(mutation_findings)},
        "output_dir": str(args.output_dir),
    }


def _judge_samples(case, answer, *, backend, model, runs, client=None, anthropic=None):
    episode = {"id": case["id"]}
    answer_obj = {"id": "generated", "text": answer}
    material_fn = lambda _episode, _answer: GP.material(case, answer)
    samples, errors = [], []
    for number in range(1, runs + 1):
        try:
            if backend == "agy":
                sample = BASE.judge_once_agy(model, episode, answer_obj, GP.AXES,
                    system=GP.SYSTEM, rubric=GP.RUBRIC, material_fn=material_fn)
            else:
                sample = BASE.judge_once(model, client, anthropic, episode, answer_obj, GP.AXES,
                    system=GP.SYSTEM, rubric=GP.RUBRIC, material_fn=material_fn)
        except RuntimeError as exc:
            sample = None
            errors.append({"run": number, "error": str(exc)})
        samples.append(sample)
    failures, _observed, report = BASE.grade_answer_report(episode, answer_obj, GP.AXES, samples)
    return samples, errors, failures, report


def run(cases, args, *, generate=None, judge=None):
    """Run the live loop; injectable seams keep all tests local and free."""
    witness_errors = GP.validate_witness_bank()
    if witness_errors:
        raise ValueError("; ".join(witness_errors))
    generator_backend, generator_model, judge_backend, judge_model = _models(args, live=True)
    bundle = GP.instruction_bundle()
    proof = repository_proof()
    generator_family = model_family(generator_model, generator_backend)
    judge_family = model_family(judge_model, judge_backend)
    independent_families = generator_family != judge_family
    client = anthropic = None
    if generator_backend == "anthropic" or judge_backend == "anthropic":
        import anthropic as anthropic_module
        anthropic = anthropic_module
        client = anthropic.Anthropic()
    run_dir = args.output_dir / f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    case_rows = []
    for case in cases:
        prompt = GP.generator_prompt(case, bundle)
        try:
            output = generate(case, prompt) if generate else BASE.generate_text_once(
                generator_backend, generator_model, prompt, system=GP.SYSTEM,
                client=client, anthropic=anthropic)
            output_path = run_dir / f"{case['id']}.raw.txt"
            output_path.write_text(output, encoding="utf-8")
            findings = GP.deterministic_findings(output, case["prompt"])
            samples = errors = failures = []
            report = {"axes": {}}
            if not findings:
                if judge:
                    samples = [judge(case, output, index) for index in range(args.runs)]
                    episode, answer_obj = {"id": case["id"]}, {"id": "generated"}
                    failures, _observed, report = BASE.grade_answer_report(
                        episode, answer_obj, GP.AXES, samples)
                else:
                    samples, errors, failures, report = _judge_samples(
                        case, output, backend=judge_backend, model=judge_model,
                        runs=args.runs, client=client, anthropic=anthropic)
            disposition = GP.receipt_disposition(findings, report["axes"])
            if disposition == "candidate_pass" and not independent_families:
                disposition = "ambiguous"
            case_rows.append({
                "case_id": case["id"], "prompt_sha256": GP.digest(case["prompt"]),
                "raw_output_path": str(output_path.relative_to(run_dir)), "raw_output_sha256": GP.digest(output),
                "deterministic_findings": findings, "judge_samples": samples,
                "judge_errors": errors, "judge_report": report, "judge_failures": failures,
                "disposition": disposition,
            })
        except RuntimeError as exc:
            case_rows.append({"case_id": case["id"], "prompt_sha256": GP.digest(case["prompt"]),
                              "generator_error": str(exc), "deterministic_findings": ["generator unavailable"],
                              "disposition": "ambiguous"})
    mutation = next(row for row in GP.load_witnesses() if row["id"] == "question_first")
    mutation_case = next(case for case in GP.load_cases(("A01",)) if case["id"] == mutation["case_id"])
    mutation_findings = GP.deterministic_findings(mutation["answer"], mutation_case["prompt"])
    receipt = {
        "schema_version": 1, "run_id": run_dir.name, "repository": proof,
        "instruction_bundle": [{key: item[key] for key in ("path", "sha256")} for item in bundle],
        "generator": {"backend": generator_backend, "model": generator_model, "family": generator_family,
                      "tool_or_web_use": "none"},
        "judge": {"backend": judge_backend, "model": judge_model, "family": judge_family,
                  "runs_per_case": args.runs, "tool_or_web_use": "structured judge tool only; no web",
                  "usage": None},
        "independent_model_families": independent_families,
        "controlled_mutation": {"case_id": "A01", "witness_id": mutation["id"],
                                "judge_calls": 0, "deterministic_findings": mutation_findings,
                                "caught": bool(mutation_findings)},
        "cases": case_rows, "owner_acceptance": "owner_unreviewed",
        "merge_authorization": "not_granted",
    }
    (run_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = ["# Generic Parity fast probe", "", f"Run: `{run_dir.name}`", "",
               "| Case | disposition |", "| --- | --- |"]
    summary.extend(f"| {row['case_id']} | {row['disposition']} |" for row in case_rows)
    summary += ["", "Owner acceptance: `owner_unreviewed`", "", "Merge authorization: `not_granted`", ""]
    (run_dir / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    return receipt, run_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", default=["A01", "A07", "A10"])
    parser.add_argument("--plan", action="store_true", help="print zero-call plan and exit")
    parser.add_argument("--generator-backend", default="auto", choices=("auto", "agy", "anthropic"))
    parser.add_argument("--generator-model")
    parser.add_argument("--judge-backend", default="auto", choices=("auto", "agy", "anthropic"))
    parser.add_argument("--judge-model")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    if args.runs < 3:
        parser.error("--runs must be at least 3")
    try:
        cases = GP.load_cases(args.cases)
        if set(case["id"] for case in cases) != set(args.cases):
            raise ValueError("case selection did not resolve exactly")
    except ValueError as exc:
        parser.error(str(exc))
    if args.plan:
        print(json.dumps(plan(cases, args), ensure_ascii=False, indent=2))
        return 0
    receipt, run_dir = run(cases, args)
    print(f"wrote {run_dir / 'summary.md'}")
    return 0 if all(row["disposition"] == "candidate_pass" for row in receipt["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
