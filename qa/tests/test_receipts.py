#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
WRITER = SKILL_DIR / "receipts.py"
QA_ENV_SH = SKILL_DIR / "qa_env.sh"


def receipt(path, client="codex"):
    path.write_text(
        json.dumps({"event": "capabilities_declared", "client": client, "route": "first_review"})
        + "\n"
        + json.dumps({"event": "owner_verdict", "controls": "pass", "card": "pass"})
        + "\n",
        encoding="utf-8",
    )


def _build_argv(receipt_path, dest_path, receipt_dir, overrides=None, omit=()):
    """Flag-value pairs for `receipts.py build`, with sane defaults for every
    required flag. Tests override just the flag(s) they care about, or omit a
    flag entirely (to prove it is actually required)."""
    kwargs = {
        "--receipt": str(receipt_path),
        "--archived-path": str(dest_path),
        "--receipt-dir": str(receipt_dir),
        "--sha": "abc123",
        "--data-source": "mock:sample",
        "--human": "owner_live",
        "--agent-model": "Claude Sonnet 5",
        "--effort": "high",
        "--run-id": "run1",
        "--stamp": "20260722-000000",
        "--campaign": "issue:#486",
        "--case-id": "M0-U01",
        "--state-mode": "fresh",
    }
    if overrides:
        kwargs.update(overrides)
    argv = []
    for flag, value in kwargs.items():
        if flag in omit:
            continue
        argv.append(flag)
        argv.append(value)
    return argv


class ReceiptManifestTest(unittest.TestCase):
    def run_writer(self, *args):
        return subprocess.run(
            [sys.executable, str(WRITER), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    # ---- existing tests, updated to the flag-based `build` CLI -----------

    def test_build_requires_explicit_model_and_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={
                "--agent-model": "GPT-5.6 Codex", "--effort": "high",
            })
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["agent"], {
                "client": "codex", "model": "GPT-5.6 Codex", "effort": "high",
            })

    def test_build_rejects_unspecified_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={
                "--agent-model": "unknown", "--effort": "default",
            })
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("agent model", result.stderr)

    def test_report_separates_agent_configurations_and_legacy_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            common = {
                "engine_version": {"sha": "abc123"},
                "human_involvement": "owner_live",
                "owner_verdict": {"controls": "pass", "card": "pass"},
            }
            (root / "new.manifest.json").write_text(json.dumps({
                **common,
                "client": "codex",
                "agent": {"client": "codex", "model": "GPT-5.6 Codex", "effort": "high"},
            }), encoding="utf-8")
            (root / "legacy.manifest.json").write_text(json.dumps({
                **common, "client": "codex",
            }), encoding="utf-8")
            result = self.run_writer("report", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("codex/GPT-5.6 Codex  x  effort=high", result.stdout)
            self.assertIn("codex/legacy-unattributed  x  effort=legacy-unattributed", result.stdout)

    # ---- fresh / continued campaign binding: accepted paths ---------------

    def test_build_fresh_run_records_campaign_binding_and_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root)
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["campaign"], "issue:#486")
            self.assertEqual(manifest["case_id"], "M0-U01")
            self.assertEqual(manifest["state_mode"], "fresh")
            self.assertIsNone(manifest["parent_run_id"])
            self.assertEqual(manifest["receipt_path"], str(dest))
            expected_sha256 = hashlib.sha256(trace.read_bytes()).hexdigest()
            self.assertEqual(manifest["receipt_sha256"], expected_sha256)

    def test_build_continued_run_with_existing_parent_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            parent_id = "20260720-000000__main-abc000__sample"
            (root / f"{parent_id}.manifest.json").write_text("{}", encoding="utf-8")
            argv = _build_argv(trace, dest, root, overrides={
                "--state-mode": "continued", "--parent-run-id": parent_id,
            })
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["state_mode"], "continued")
            self.assertEqual(manifest["parent_run_id"], parent_id)

    # ---- rejections ---------------------------------------------------

    def test_build_rejects_missing_campaign_flag_via_argparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, omit=("--campaign",))
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            # argparse's own "required argument" error, NOT the ValueError
            # "ERROR: ..." path used for value-rule violations.
            self.assertIn("--campaign", result.stderr)
            self.assertNotIn("ERROR:", result.stderr)

    def test_build_rejects_placeholder_campaign(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={"--campaign": "unknown"})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("ERROR:", result.stderr)
            self.assertIn("campaign", result.stderr)

    def test_build_rejects_placeholder_case_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={"--case-id": "default"})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("ERROR:", result.stderr)
            self.assertIn("case-id", result.stderr)

    def test_build_rejects_campaign_charset_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={"--campaign": "issue/486"})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must match", result.stderr)

    def test_build_rejects_case_id_charset_violation_slash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={"--case-id": "M0/U01"})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must match", result.stderr)

    def test_build_rejects_case_id_charset_violation_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={"--case-id": "case id with space"})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must match", result.stderr)

    def test_build_rejects_case_id_privacy_guard_csv_path(self):
        # The charset guard is a privacy guard as much as a hygiene one: a
        # realistic absolute CSV path must never be accepted as a case_id,
        # because manifests get quoted verbatim in public issues.
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            real_csv_path = "/Users/ting/Side_project/investment_note/trades/2026-real-trades.csv"
            argv = _build_argv(trace, dest, root, overrides={"--case-id": real_csv_path})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must match", result.stderr)
            # the real path must not have silently made it into a printed manifest
            self.assertNotIn(real_csv_path, result.stdout)

    def test_build_rejects_invalid_state_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={"--state-mode": "sideways"})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("ERROR:", result.stderr)
            self.assertIn("state-mode", result.stderr)

    def test_build_rejects_continued_without_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={"--state-mode": "continued"})
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("parent-run-id", result.stderr)

    def test_build_rejects_fresh_with_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={
                "--state-mode": "fresh", "--parent-run-id": "some-earlier-run",
            })
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("fresh", result.stderr)

    def test_build_rejects_parent_run_id_with_dotdot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={
                "--state-mode": "continued", "--parent-run-id": "../../etc/passwd",
            })
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must match", result.stderr)

    def test_build_rejects_parent_run_id_with_slash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            argv = _build_argv(trace, dest, root, overrides={
                "--state-mode": "continued", "--parent-run-id": "sub/dir",
            })
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must match", result.stderr)

    def test_build_rejects_continued_parent_manifest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            missing_parent = "20260101-000000__main-deadbee__nope"
            # deliberately do NOT create <missing_parent>.manifest.json
            argv = _build_argv(trace, dest, root, overrides={
                "--state-mode": "continued", "--parent-run-id": missing_parent,
            })
            result = self.run_writer("build", *argv)
            self.assertEqual(result.returncode, 2)
            self.assertIn("does not exist", result.stderr)
            self.assertIn(missing_parent, result.stderr)

    # ---- report: campaign coverage section --------------------------------

    def test_report_legacy_manifest_renders_unattributed_without_inventing_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "legacyrun1.manifest.json").write_text(json.dumps({
                "run_id": "legacyrun1",
                "engine_version": {"sha": "abc123"},
                "human_involvement": "owner_live",
                "client": "codex",
                "owner_verdict": {"controls": "pass"},
            }), encoding="utf-8")
            result = self.run_writer("report", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("campaign: legacy-unattributed", result.stdout)
            self.assertIn("case: legacy-unattributed", result.stdout)
            self.assertIn("legacyrun1", result.stdout)

    def test_report_new_manifest_shows_campaign_case_client_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "newrun1.manifest.json").write_text(json.dumps({
                "run_id": "newrun1",
                "engine_version": {"sha": "abc123"},
                "human_involvement": "owner_live",
                "client": "codex",
                "agent": {"client": "codex", "model": "GPT-5.6 Codex", "effort": "high"},
                "campaign": "issue:#486",
                "case_id": "M0-U01",
                "state_mode": "fresh",
                "parent_run_id": None,
                "owner_verdict": {"controls": "pass"},
            }), encoding="utf-8")
            result = self.run_writer("report", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("campaign: issue:#486", result.stdout)
            self.assertIn("case: M0-U01", result.stdout)
            self.assertIn("client: codex", result.stdout)
            self.assertIn("newrun1", result.stdout)
            self.assertIn("state=fresh", result.stdout)

    def test_report_continued_run_shows_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "parentrun1.manifest.json").write_text(json.dumps({
                "run_id": "parentrun1",
                "engine_version": {"sha": "abc000"},
                "human_involvement": "owner_live",
                "client": "codex",
                "campaign": "issue:#486",
                "case_id": "M0-U01",
                "state_mode": "fresh",
                "parent_run_id": None,
            }), encoding="utf-8")
            (root / "childrun1.manifest.json").write_text(json.dumps({
                "run_id": "childrun1",
                "engine_version": {"sha": "abc123"},
                "human_involvement": "owner_live",
                "client": "codex",
                "campaign": "issue:#486",
                "case_id": "M0-U01",
                "state_mode": "continued",
                "parent_run_id": "parentrun1",
            }), encoding="utf-8")
            result = self.run_writer("report", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("childrun1", result.stdout)
            self.assertIn("state=continued", result.stdout)
            self.assertIn("parent=parentrun1", result.stdout)
            self.assertNotIn("parent manifest not in this directory", result.stdout)

    def test_report_continued_run_flags_missing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "childrun2.manifest.json").write_text(json.dumps({
                "run_id": "childrun2",
                "engine_version": {"sha": "abc123"},
                "human_involvement": "owner_live",
                "client": "codex",
                "campaign": "issue:#486",
                "case_id": "M0-U01",
                "state_mode": "continued",
                "parent_run_id": "does-not-exist-run-id",
            }), encoding="utf-8")
            result = self.run_writer("report", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("childrun2", result.stdout)
            self.assertIn("parent=does-not-exist-run-id", result.stdout)
            self.assertIn("parent manifest not in this directory", result.stdout)


class ManifestFreeTextGateTest(ReceiptManifestTest):
    """#588: the manifest is quoted in public acceptance issues, so its two
    remaining free-text fields are held to closed grammars at build time —
    a path or prose fails closed instead of being one copy-paste from a
    #274-class leak. Read paths are untouched: old manifests stay readable."""

    def test_data_source_is_held_to_the_closed_grammar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            dest = root / "archived.jsonl"
            for good in ("real", "test-drive", "mock:sample_ai_holder", "mock:tw.mixed-2"):
                result = self.run_writer("build", *_build_argv(
                    trace, dest, root, overrides={"--data-source": good}))
                self.assertEqual(result.returncode, 0, (good, result.stderr))
                self.assertEqual(json.loads(result.stdout)["data_source"], good)
            for bad in ("/private/path/real-trades.csv",
                        "mock:with space",
                        "real trades from my broker export",
                        "mock:",
                        "csv"):
                result = self.run_writer("build", *_build_argv(
                    trace, dest, root, overrides={"--data-source": bad}))
                self.assertEqual(result.returncode, 2, (bad, result.stdout))
                self.assertIn("data-source must be", result.stderr, bad)

    def test_the_traces_declared_client_is_held_to_the_identifier_charset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            dest = root / "archived.jsonl"
            receipt(trace, client="/Users/someone/real notes about a trade")
            result = self.run_writer("build", *_build_argv(trace, dest, root))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("declared client must match", result.stderr)
            # The identifier form every real host writes still passes.
            receipt(trace, client="codex-desktop")
            result = self.run_writer("build", *_build_argv(trace, dest, root))
            self.assertEqual(result.returncode, 0, result.stderr)


class VerdictKeyDriftTest(unittest.TestCase):
    """The manifest must keep every axis a verdict can carry.

    `receipts.py` reads a *file*, not the tool, so its `VERDICT_KEYS` is a
    hand-written list — and an axis missing from it is dropped silently, at
    archive time, with nothing failing. #523's `change` axis is the case that
    makes this expensive: on the card-free `refresh` route it is the only
    judgment of what the user actually saw, so losing it would archive a run
    whose verdict recorded nothing about its own lane.

    Extra keys are deliberately allowed: an archived receipt from an older
    checkout simply carries none, and `_parse_receipt` skips what is absent.
    """

    def test_verdict_keys_cover_every_axis_the_tool_can_record(self):
        import importlib.util

        tool = SKILL_DIR.parent / "skills" / "fomo-kernel" / "tools" / "ux_receipt.py"
        spec = importlib.util.spec_from_file_location("ux_receipt_for_receipts", tool)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        writer = importlib.util.spec_from_file_location("qa_receipts", WRITER)
        receipts = importlib.util.module_from_spec(writer)
        writer.loader.exec_module(receipts)

        declared = set()
        for contract in module.ROUTE_CONTRACTS.values():
            declared |= set(contract["verdict"])
        missing = sorted(declared - set(receipts.VERDICT_KEYS))
        self.assertEqual(
            missing, [],
            "ux_receipt routes can record these owner-verdict axes, but "
            "receipts.py VERDICT_KEYS drops them from every archived manifest: "
            f"{missing}")


class ArchiveReceiptOrderingTest(unittest.TestCase):
    """Shell-level proof of the fail-closed ordering: `receipts.py build`
    must succeed BEFORE qa_env.sh touches the receipt dir at all, so a
    rejected run (e.g. a bad --state-mode) leaves no partial *.jsonl or
    *.manifest.json behind.

    qa_env.sh's own top-of-script isolation guards require FOMO_DOGFOOD_WT to
    (a) contain "dogfood" in its path and (b) actually be a git checkout —
    cmd_archive_receipt runs `git -C "$DOGFOOD_WT" rev-parse --short HEAD` to
    determine "which commit was actually tested", which fails on a plain
    empty directory. A real dogfood worktree is therefore required; this test
    creates (and always removes) a small, local, no-network throwaway
    `git worktree add --detach <path> HEAD` off this checkout rather than
    touching the real shared dogfood worktree another session might be using.
    """

    @classmethod
    def setUpClass(cls):
        cls.repo = SKILL_DIR.parent
        cls.scratch = pathlib.Path(tempfile.mkdtemp(prefix="fomo-qa-archive-order-"))
        cls.dogfood_wt = cls.scratch / "fomo-kernel-dogfood-ordering-test"
        subprocess.run(
            ["git", "-C", str(cls.repo), "worktree", "add", "--detach", str(cls.dogfood_wt), "HEAD"],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            ["git", "-C", str(cls.repo), "worktree", "remove", "--force", str(cls.dogfood_wt)],
            capture_output=True, text=True, check=False,
        )
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def _env_for(self, root, receipts_dir):
        env = os.environ.copy()
        env["FOMO_REPO"] = str(self.repo)
        env["FOMO_DOGFOOD_WT"] = str(self.dogfood_wt)
        env["FOMO_DOGFOOD_COACH"] = str(root / "trade-coach-dogfood")
        env["FOMO_DOGFOOD_BACKUPS"] = str(root / "trade-coach-dogfood-backups")
        env["FOMO_QA_RECEIPTS"] = str(receipts_dir)
        # #557: qa_env.sh refuses every command, archiving included, until the
        # shell it runs in has the account's own coach root out of reach — a
        # drifted run must not be able to produce citable evidence. So this
        # test archives the way a real run now has to: under a throwaway HOME
        # where `~/.trade-coach` names nothing, with the dogfood root declared.
        isolated_home = root / "qa-home"
        isolated_home.mkdir(exist_ok=True)
        env["HOME"] = str(isolated_home)
        env["TRADE_COACH_HOME"] = str(root / "trade-coach-dogfood")
        return env

    def test_bad_state_mode_leaves_no_partial_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            receipts_dir = root / "receipts"
            receipts_dir.mkdir()
            trace = root / "receipt.jsonl"
            receipt(trace)

            result = subprocess.run(
                [
                    "bash", str(QA_ENV_SH), "archive-receipt", str(trace),
                    "mock:sample", "agent_simulated",
                    "--agent-model", "Claude Sonnet 5", "--effort", "high",
                    "--campaign", "issue:#999test", "--case-id", "T-01",
                    "--state-mode", "sideways",
                ],
                capture_output=True, text=True, env=self._env_for(root, receipts_dir), check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("state-mode", result.stderr)
            self.assertEqual(list(receipts_dir.glob("*.jsonl")), [])
            self.assertEqual(list(receipts_dir.glob("*.manifest.json")), [])

    def test_successful_archive_exits_zero_and_writes_artifacts(self):
        # Regression guard: an earlier version of the temp-file-first ordering
        # left an EXIT trap referencing a `local` variable by deferred (single
        # quote) expansion. On the SUCCESS path that trap only fires after
        # cmd_archive_receipt has already returned and its `local`s are gone,
        # so under `set -u` the trap itself died with "unbound variable" —
        # turning a successful archive into a false non-zero exit. A caller
        # that only checks the exit code (a CI gate, `&&`, ...) would wrongly
        # believe the archive failed.
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            receipts_dir = root / "receipts"
            receipts_dir.mkdir()
            trace = root / "receipt.jsonl"
            receipt(trace)

            result = subprocess.run(
                [
                    "bash", str(QA_ENV_SH), "archive-receipt", str(trace),
                    "mock:sample_ai_holder", "owner_live",
                    "--agent-model", "Claude Sonnet 5", "--effort", "high",
                    "--campaign", "issue:#486", "--case-id", "M0-U01",
                    "--state-mode", "fresh",
                ],
                capture_output=True, text=True, env=self._env_for(root, receipts_dir), check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("unbound variable", result.stderr)
            jsonl_files = list(receipts_dir.glob("*.jsonl"))
            manifest_files = list(receipts_dir.glob("*.manifest.json"))
            self.assertEqual(len(jsonl_files), 1)
            self.assertEqual(len(manifest_files), 1)
            manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["campaign"], "issue:#486")
            self.assertEqual(manifest["case_id"], "M0-U01")
            self.assertEqual(manifest["state_mode"], "fresh")
            self.assertIsNone(manifest["parent_run_id"])
            expected_sha256 = hashlib.sha256(trace.read_bytes()).hexdigest()
            self.assertEqual(manifest["receipt_sha256"], expected_sha256)


if __name__ == "__main__":
    unittest.main()
