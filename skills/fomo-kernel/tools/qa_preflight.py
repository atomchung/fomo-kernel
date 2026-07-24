#!/usr/bin/env python3
"""Run content-free automated checks for a maintainer QA preflight.

This command intentionally does *not* create a maintainer dogfood session,
archive a UX receipt, or claim that a person saw a card.  The deterministic
suite may exercise temporary review and receipt fixtures internally; those
fixtures are not attributable QA evidence.  A green result means the engine
and artifact contracts passed, not that a target client's controls or card
delivery passed.

Usage:
  python3 skills/fomo-kernel/tools/qa_preflight.py status
  python3 skills/fomo-kernel/tools/qa_preflight.py refresh
  python3 skills/fomo-kernel/tools/qa_preflight.py run [--refresh]
"""

from __future__ import annotations

import argparse
import json
import os
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
ISOLATED_ENV_KEYS = (
    "TRADE_COACH_HOME",
    "TR_TEST_NETWORK",
    "TR_DRIVER_MAP",
    "TR_PREV_END",
    "TR_DISPLAY_CURRENCY",
    "TR_CASH",
    "TR_LEDGER",
    "TR_JSON",
    "TR_STATE_OUT",
    "TR_INSTRUMENT_MAP",
)


class PreflightError(RuntimeError):
    """The requested preflight cannot establish its bounded report."""


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise PreflightError(detail)
    return result.stdout.strip()


def _revision(repo_root: Path, remote_freshness: str) -> dict[str, Any]:
    """Describe local revision facts without treating cached refs as fresh.

    ``origin/main`` is a best-effort lookup, not a guaranteed one: a shallow
    or narrow-refspec checkout (this repo's own CI included -- caught by
    ``test_cli_status_prints_json`` running the real CLI, not the mocked
    unit tests) may never populate that remote-tracking ref at all, and a
    missing ref must not crash `status`, which promises to work network-free
    on whatever is already on disk. Right after a fetch, ``FETCH_HEAD`` is a
    reliable fallback -- it is always set by `git fetch`, independent of how
    the checkout's fetch refspec maps remote-tracking branches."""
    head = _git(repo_root, "rev-parse", "--short", "HEAD")
    origin_main = None
    candidates = ("origin/main", "FETCH_HEAD") if remote_freshness == "refreshed" else ("origin/main",)
    for ref in candidates:
        try:
            origin_main = _git(repo_root, "rev-parse", "--short", ref)
            break
        except PreflightError:
            continue
    return {
        "head": head,
        "origin_main": origin_main,
        "head_matches_origin_main": origin_main is not None and head == origin_main,
        "remote_freshness": remote_freshness,
    }


def _base_report(repo_root: Path, remote_freshness: str) -> dict[str, Any]:
    return {
        "kind": "fomo_kernel_contract_preflight",
        "formal_qa": False,
        "human_involvement": "agent_simulated",
        "ux_evidence": "not_assessed",
        "receipt": "not_created",
        "revision": _revision(repo_root, remote_freshness),
    }


def _emit(report: dict[str, Any], report_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    print(text)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")


def _refresh(repo_root: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "fetch", "origin", "main"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "could not refresh origin/main"
        raise PreflightError(detail)


def _run_suite(repo_root: Path, runner: Path | None = None) -> int:
    runner = runner or repo_root / "tests" / "run_all.py"
    if not runner.is_file():
        raise PreflightError(f"deterministic suite is missing: {runner}")
    # Some deterministic fixtures exercise the lifecycle. Never let a caller's
    # real or dogfood state root leak into those child processes. Remove every
    # input/state/network override that could turn this fixed offline suite
    # into a caller-specific run, then replace HOME with a throwaway root. Do
    # not set TRADE_COACH_HOME: several existing tests deliberately override
    # only HOME, and the engine gives TRADE_COACH_HOME precedence over HOME.
    # A managed Python runtime can keep user-installed dependencies under the
    # caller's user-site directory, so retain that import path explicitly.
    child_env = dict(os.environ)
    for key in ISOLATED_ENV_KEYS:
        child_env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="fomo-qa-preflight-") as temp_root:
        child_env["HOME"] = temp_root
        user_site = site.getusersitepackages()
        user_base = site.getuserbase()
        if user_base:
            child_env["PYTHONUSERBASE"] = user_base
        if os.path.isdir(user_site):
            previous_pythonpath = child_env.get("PYTHONPATH")
            child_env["PYTHONPATH"] = (
                user_site if not previous_pythonpath else f"{user_site}{os.pathsep}{previous_pythonpath}"
            )
        result = subprocess.run(
            [sys.executable, str(runner)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=child_env,
        )
    if result.returncode:
        # A passing preflight stays one content-free JSON row. Preserve test
        # diagnostics only when the caller needs them to fix a failure.
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def _repo_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not (root / ".git").exists() or not (root / "tests" / "run_all.py").is_file():
        raise argparse.ArgumentTypeError(f"not a fomo-kernel checkout: {root}")
    return root


def _report_path(value: str | None) -> Path | None:
    return None if value is None else Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=_repo_root, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--report", help="optional local path for the content-free JSON result")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="inspect cached Git facts; does not access the network")
    commands.add_parser("refresh", help="fetch origin/main, then report refreshed Git facts")
    run = commands.add_parser("run", help="run the deterministic suite and emit a preflight result")
    run.add_argument("--refresh", action="store_true", help="fetch origin/main before running")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root
    report_path = _report_path(args.report)
    try:
        if args.command == "refresh":
            _refresh(repo_root)
            report = _base_report(repo_root, "refreshed")
            report["status"] = "ready"
        elif args.command == "status":
            report = _base_report(repo_root, "unverified")
            report["status"] = "ready"
        else:
            remote_freshness = "unverified"
            if args.refresh:
                _refresh(repo_root)
                remote_freshness = "refreshed"
            exit_code = _run_suite(repo_root)
            report = _base_report(repo_root, remote_freshness)
            report["status"] = "engine_contract_pass" if exit_code == 0 else "engine_contract_fail"
            _emit(report, report_path)
            return exit_code
        _emit(report, report_path)
        return 0
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
