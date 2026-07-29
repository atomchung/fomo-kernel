#!/usr/bin/env python3
"""Run content-free automated checks for a maintainer QA preflight.

This command intentionally does *not* create a maintainer dogfood session,
archive a UX receipt, or claim that a person saw a card.  The deterministic
suite may exercise temporary review and receipt fixtures internally; those
fixtures are not attributable QA evidence.  A green result means the engine
and artifact contracts passed, not that a target client's controls or card
delivery passed.

``isolate-check`` is a different kind of check and makes no claim about the
suite at all: it inspects the environment a QA run is about to execute in and
fails closed when the account's own coach root is still reachable through the
path every reader composes.  It is runbook gate 2's machine half (#557).

Usage:
  python3 skills/fomo-kernel/tools/qa_preflight.py status
  python3 skills/fomo-kernel/tools/qa_preflight.py refresh
  python3 skills/fomo-kernel/tools/qa_preflight.py run [--refresh]
  python3 skills/fomo-kernel/tools/qa_preflight.py isolate-check
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

try:  # POSIX only, which is every platform this toolchain supports.
    import pwd
except ImportError:  # pragma: no cover - Windows has no password database
    pwd = None  # type: ignore[assignment]


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
# The directory name every reader of this product composes under a home
# directory: the engine (`session.default_root()`), `coach.py`, and
# `tools/ux_receipt.py` all fall back to `~/.trade-coach`, and so does a model
# with a shell that decides the isolated root looks wrong (#557).
DEFAULT_STATE_DIR = ".trade-coach"
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


# --- runbook gate 2: isolation, verified rather than instructed (#557) -------
#
# `TRADE_COACH_HOME` routes a *writer*.  It cannot bound a *reader*, and during
# a real probe the reader was a language model with a shell: pointed at a
# throwaway root it judged empty, it composed the hardcoded default
# `$HOME/.trade-coach` on its own initiative and read the account's real store.
# A read-only sandbox stopped nothing, because nothing was written.
#
# So the run's `HOME` is replaced for its duration, which makes that default
# composition name nothing -- and this check is what refuses to let a run start
# until that actually took effect.  It observes the environment, never the code
# that is supposed to set it: asserting "the export exists" is exactly the shape
# of assurance #557 says failed twice already (#255, #269).


def _account_home() -> Path | None:
    """The account's own home directory, read from the password database.

    Deliberately not ``$HOME`` and not ``Path.home()`` (which reads ``$HOME``
    first): the whole point of this lane is that ``$HOME`` has been replaced,
    so a check reading the replacement cannot tell whether the replacement
    happened.  ``pwd`` is the one source that survives the override.
    """
    if pwd is None:
        return None
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):  # pragma: no cover - unusable password database
        return None


def _resolved(value: str) -> Path | None:
    try:
        return Path(value).resolve()
    except (OSError, RuntimeError, ValueError):  # pragma: no cover - hostile path
        return None


def _expanded(value: str, home: str) -> str:
    """POSIX ``~`` expansion against the environment being inspected.

    ``os.path.expanduser`` would expand against *this* process's ``HOME``,
    which is not necessarily the one under inspection.
    """
    if value == "~":
        return home
    if value.startswith("~/"):
        return os.path.join(home, value[2:])
    return value


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def isolation_report(
    env: dict[str, str] | None = None,
    account_home: str | Path | None = None,
) -> dict[str, Any]:
    """Judge whether this environment has the account's coach root out of reach.

    ``account_home`` exists so tests can state an account home instead of
    touching the real one.  It is a Python-API argument only: the CLI never
    reads it from the environment, because a gate an agent can redirect with an
    export is not a gate.

    The report carries booleans and check names, never absolute paths -- the
    output is meant to be pasteable into a public issue or PR without passing
    through `tools/privacy_lint.py` first.
    """
    env = os.environ if env is None else env
    account = Path(account_home) if account_home is not None else _account_home()
    account = _resolved(str(account)) if account is not None else None
    account_root = account / DEFAULT_STATE_DIR if account is not None else None

    failures: list[dict[str, str]] = []

    def fail(check: str, detail: str) -> None:
        failures.append({"check": check, "detail": detail})

    home_declared = (env.get("HOME") or "").strip()
    home = _resolved(home_declared) if home_declared else None
    if not home_declared:
        fail("home_undeclared",
             "HOME is unset, so every '~' composition falls back to the account's own home.")
    elif home is None or not home.is_dir():
        fail("home_not_a_directory",
             "HOME does not name a directory, so a tool reading it falls back unpredictably.")

    default_root = home / DEFAULT_STATE_DIR if home is not None else None
    default_root_present = default_root is not None and os.path.lexists(default_root)
    if default_root_present:
        fail("default_root_reachable",
             f"'~/{DEFAULT_STATE_DIR}' resolves to something that exists, so an agent "
             "composing the product's default path reaches a live store.")

    if account is None:
        fail("account_home_unresolvable",
             "this account's home cannot be read from the password database, so whether "
             "its coach root is reachable cannot be established. Fail closed.")

    # `home_is_the_account_home` is reported below but is deliberately NOT a
    # failure on its own.  The criterion is "the account's coach root cannot be
    # reached", not "HOME was changed": in a container or ephemeral runner --
    # the other lane #557 names, and the one the owner may still choose -- the
    # password database and `$HOME` agree on `/root`, and there is no coach root
    # under it.  Refusing that would build this gate as an obstacle to the
    # stronger fix.  Where they agree AND a root is there, `default_root_reachable`
    # is already the same path and fires.

    state_declared = (env.get("TRADE_COACH_HOME") or "").strip()
    if not state_declared:
        fail("state_root_undeclared",
             "TRADE_COACH_HOME is unset, so review.py, coach.py and ux_receipt.py all fall "
             f"back to '~/{DEFAULT_STATE_DIR}'.")
    elif account_root is not None:
        state_root = _resolved(_expanded(state_declared, home_declared))
        if state_root is not None and _is_within(state_root, account_root):
            fail("state_root_is_the_account_root",
                 "TRADE_COACH_HOME points into this account's own coach root, so the run "
                 "would write its dogfood state into the real one.")

    return {
        "kind": "fomo_kernel_isolation_check",
        "isolated": not failures,
        "failures": failures,
        "observations": {
            "home_declared": bool(home_declared),
            "home_is_the_account_home": (
                home is not None and account is not None and home == account
            ),
            "default_root_present": default_root_present,
            "account_root_present": account_root is not None and os.path.lexists(account_root),
            "state_root_declared": bool(state_declared),
        },
    }


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
    commands.add_parser(
        "isolate-check",
        help="runbook gate 2: exit non-zero unless this environment has the account's "
             "own coach root out of reach")
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
        elif args.command == "isolate-check":
            report = isolation_report()
            _emit(report, report_path)
            for failure in report["failures"]:
                print(f"ISOLATION: {failure['check']}: {failure['detail']}", file=sys.stderr)
            return 0 if report["isolated"] else 1
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
