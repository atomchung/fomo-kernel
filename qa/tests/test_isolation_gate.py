#!/usr/bin/env python3
"""A dogfood run cannot start until its isolation actually took effect (#557).

`TRADE_COACH_HOME` routes a *writer*. It cannot bound a *reader*, and the
reader here is a language model with a shell: pointed at a throwaway root it
judged unfamiliar and empty, one composed the hardcoded default
`$HOME/.trade-coach` on its own initiative and read the account's real ledger
into its answer. A read-only sandbox stopped nothing, because nothing was
written. That was the third instance of one shape (#255, #269), and the first
two were both repaired by teaching one more module to honour the variable —
a repair that does not exist when the reader is a shell command composed at
runtime.

So `qa_env.sh isolate` replaces `HOME` for the run, and every other command in
that script refuses until the replacement is in effect. This suite is about the
refusal, not about the export: the failure mode is an agent not following an
instruction it was never given, so what has to be tested is that the run stops.

Four facts, each of which has a way of quietly going missing:

1. an unisolated shell is refused, before the script does anything at all;
2. `isolate` is exempt (it is what establishes isolation) and what it prints is
   accepted by the same gate that refused — the two halves cannot drift apart
   without this round trip going red;
3. the harness's own paths still resolve against the account's real home while
   the agent-facing `$HOME` points elsewhere. A version that "works" because it
   quietly stopped isolating anything, and a version that isolates so hard the
   harness can no longer find its own worktree, are both failures;
4. the gate's verdict is the product tool's verdict, so a condition only
   `qa_preflight.py` knows about still stops the run.

Run directly: `python3 qa/tests/test_isolation_gate.py`.
"""
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest


QA_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO = QA_DIR.parent
QA_ENV = QA_DIR / "qa_env.sh"

# Commands that only echo a resolved path: no fetch, no network, no writes. The
# gate fires before any of them, which is the point being tested.
INERT = "coach-root"


def account_home():
    """This account's home, from the password database rather than `$HOME`.

    The same source `qa_env.sh` uses, for the same reason: `$HOME` is the thing
    under test and cannot testify about itself.
    """
    import pwd

    return pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)


class IsolationGateTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.workspace.name)
        # A stand-in for a home that still has a coach root in it. Never the
        # real one: this suite proves a hole is closed, it does not open it.
        self.unisolated_home = self.root / "home-with-a-coach-root"
        (self.unisolated_home / ".trade-coach").mkdir(parents=True)
        self.qa_home = self.root / "qa-home"
        self.dogfood_root = self.root / "state" / "coach-dogfood"
        self.dogfood_root.mkdir(parents=True)
        self.addCleanup(self.workspace.cleanup)

    def base_env(self, home):
        env = dict(os.environ)
        env.pop("TRADE_COACH_HOME", None)
        env.update({
            "HOME": str(home),
            # Pinned so the script never reaches for the maintainer's own
            # checkout, worktree, backups or receipt archive during a test.
            "FOMO_REPO": str(REPO),
            "FOMO_DOGFOOD_COACH": str(self.dogfood_root),
            "FOMO_DOGFOOD_BACKUPS": str(self.root / "backups-dogfood"),
            "FOMO_QA_RECEIPTS": str(self.root / "receipts"),
            "FOMO_QA_HOME": str(self.qa_home),
        })
        return env

    def run_qa_env(self, *args, env):
        return subprocess.run(
            ["bash", str(QA_ENV), *args],
            cwd=str(REPO), capture_output=True, text=True, env=env)

    # --- 1. an unisolated shell is refused -----------------------------------

    def test_an_unisolated_shell_cannot_run_any_command(self):
        env = self.base_env(self.unisolated_home)
        env["FOMO_DOGFOOD_WT"] = str(self.root / "wt-dogfood")
        result = self.run_qa_env(INERT, env=env)
        self.assertNotEqual(
            result.returncode, 0,
            f"qa_env.sh {INERT} succeeded in a shell whose ~/.trade-coach still resolves. "
            "The gate is the only thing standing between a dogfood run and the account's "
            f"own records.\nstdout: {result.stdout}")
        self.assertIn("REFUSING", result.stderr)
        self.assertIn("default_root_reachable", result.stderr)
        # The refusal has to say what to do, or it is an obstacle rather than a gate.
        self.assertIn("isolate", result.stderr)

    def test_a_declared_state_root_is_not_enough_on_its_own(self):
        """`TRADE_COACH_HOME` was always set, and the leak happened anyway.

        A gate satisfied by the export that failed would restate the bug.
        """
        env = self.base_env(self.unisolated_home)
        env["TRADE_COACH_HOME"] = str(self.dogfood_root)
        result = self.run_qa_env(INERT, env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("default_root_reachable", result.stderr)

    # --- 2. isolate is exempt, and what it prints passes the gate ------------

    def isolate_block(self, env):
        result = self.run_qa_env("isolate", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def applied(self, env, block):
        """Apply an `export K=V` block to a copy of `env`, as `eval` would."""
        applied = dict(env)
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("export "):
                continue
            key, _, value = line[len("export "):].partition("=")
            # The block is shell-quoted, and PYTHONPATH deliberately carries a
            # `${PYTHONPATH:+...}` suffix that only `eval` can resolve; drop it
            # the way an empty PYTHONPATH would.
            value = value.replace("${PYTHONPATH:+:$PYTHONPATH}", "")
            applied[key] = " ".join(shlex.split(value))
        return applied

    def test_isolate_is_exempt_and_its_block_satisfies_the_same_gate(self):
        env = self.base_env(self.unisolated_home)
        block = self.isolate_block(env)
        self.assertIn("export HOME=", block)
        self.assertIn("export TRADE_COACH_HOME=", block)

        isolated = self.applied(env, block)
        self.assertEqual(isolated["HOME"], str(self.qa_home))
        self.assertFalse(
            (self.qa_home / ".trade-coach").exists(),
            "the one fact this lane buys is that this path names nothing")

        result = self.run_qa_env(INERT, env=isolated)
        self.assertEqual(
            result.returncode, 0,
            "qa_env.sh printed an export block its own gate then rejected; the two "
            f"halves have drifted.\nstderr: {result.stderr}")
        self.assertEqual(result.stdout.strip(), str(self.dogfood_root))

    def test_isolate_refuses_a_home_that_already_holds_a_default_coach_root(self):
        (self.qa_home / ".trade-coach").mkdir(parents=True)
        result = self.run_qa_env("isolate", env=self.base_env(self.unisolated_home))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("REFUSING", result.stderr)

    # --- 3. the harness keeps its own paths while the agent's ~ moves --------

    def test_the_harness_resolves_its_own_paths_against_the_account_home(self):
        """`qa_env.sh`'s defaults are `$HOME`-relative, and `$HOME` just moved.

        The dogfood worktree, the repo and the receipt archive really do live
        under the account's home; a HOME override that relocated them would
        leave the harness unable to find anything, and the natural repair for
        that is to drop the override.
        """
        env = self.base_env(self.unisolated_home)
        isolated = self.applied(env, self.isolate_block(env))
        # Deliberately unpinned, so the script has to resolve the default itself.
        isolated.pop("FOMO_DOGFOOD_WT", None)

        result = self.run_qa_env("path", env=isolated)
        self.assertEqual(result.returncode, 0, result.stderr)
        worktree = pathlib.Path(result.stdout.strip())
        self.assertTrue(
            str(worktree).startswith(str(account_home()) + os.sep),
            f"the default dogfood worktree resolved to {worktree}, which is not under this "
            "account's home: the harness followed the agent-facing HOME override instead of "
            "its own")
        self.assertFalse(
            str(worktree).startswith(str(self.qa_home) + os.sep),
            "the default dogfood worktree moved inside the throwaway QA home")

    # --- 4. the gate's verdict is the product tool's verdict ----------------

    def test_a_condition_only_the_product_tool_knows_about_still_stops_the_run(self):
        """An isolated HOME with no declared state root is still refused.

        Nothing in `qa_env.sh` looks at `TRADE_COACH_HOME` being absent; that
        rule lives in `qa_preflight.py isolate-check`, which is where
        `docs/qa-runbook.md`'s gate 2 is stated for every client. A local
        reimplementation of the check would pass this shell and fail here.
        """
        env = self.base_env(self.unisolated_home)
        isolated = self.applied(env, self.isolate_block(env))
        isolated.pop("TRADE_COACH_HOME")

        result = self.run_qa_env(INERT, env=isolated)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("state_root_undeclared", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
