#!/usr/bin/env python3
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
WRITER = SKILL_DIR / "receipts.py"


def receipt(path, client="codex"):
    path.write_text(
        json.dumps({"event": "capabilities_declared", "client": client, "route": "first_review"})
        + "\n"
        + json.dumps({"event": "owner_verdict", "controls": "pass", "card": "pass"})
        + "\n",
        encoding="utf-8",
    )


class ReceiptManifestTest(unittest.TestCase):
    def run_writer(self, *args):
        return subprocess.run(
            [sys.executable, str(WRITER), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_build_requires_explicit_model_and_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            trace = root / "receipt.jsonl"
            receipt(trace)
            result = self.run_writer(
                "build", str(trace), "abc123", "mock:sample", "owner_live",
                "GPT-5.6 Codex", "high", "run", "20260722-000000",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["agent"], {
                "client": "codex", "model": "GPT-5.6 Codex", "effort": "high",
            })

    def test_build_rejects_unspecified_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = pathlib.Path(tmp) / "receipt.jsonl"
            receipt(trace)
            result = self.run_writer(
                "build", str(trace), "abc123", "mock:sample", "owner_live",
                "unknown", "default", "run", "20260722-000000",
            )
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


if __name__ == "__main__":
    unittest.main()
