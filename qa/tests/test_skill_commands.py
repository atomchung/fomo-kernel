#!/usr/bin/env python3
"""The maintainer QA docs must not drift from the tool an operator copies into.

`qa/SKILL.md` is the `/fomo-qa` walkthrough: an operator pastes its commands
during a formal QA run. `tools/ux_receipt.py` is append-only, so a stale example
is discovered at the *end* of the walkthrough — when `verify` refuses the trace
— and the whole run's evidence has to be thrown away and redone. That cost is
what this suite removes (#520). It had already been paid: the examples carried a
removed `--question-id`, dropped `--session-id` on most calls, opened with a
`start` that failed its own adapter check, presented a card before its artifact
existed, omitted the required `--grounding-check-file` and the cash anchor, and
documented `findings_recorded` after `owner_verdict` when `verify` requires it
before.

Four checks, none of which restate the tool's vocabulary:

1. every documented invocation parses against the real `build_parser()`;
2. the documented order, replayed for real, produces a trace that verifies;
3. the doc's `verify` example is not weaker than the gate `qa_env.sh` applies
   at archive time (derived from that script, not copied into this file);
4. the public runbook's non-placeholder invocations still parse.

The single authority for every flag is the parser itself, and for the archive
gate it is `qa_env.sh`. Nothing here hand-mirrors either: a hand-copied list
would be a third place to drift.

Run directly: `python3 qa/tests/test_skill_commands.py`.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


QA_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO = QA_DIR.parent
SKILL_DOC = QA_DIR / "SKILL.md"
QA_ENV = QA_DIR / "qa_env.sh"
RUNBOOK = REPO / "docs" / "qa-runbook.md"
SKILL_ROOT = REPO / "skills" / "fomo-kernel"
TOOL = SKILL_ROOT / "tools" / "ux_receipt.py"
EPISODE_BANK = REPO / "evals" / "episodes"

SESSION_ID = "qa-doc-replay"
# A documented invocation is a `python3 .../ux_receipt.py ...` line; a commented
# one is the doc's way of showing an either/or alternative and is parser-checked
# too, because a broken alternative is copied just as literally as a live one.
UX_COMMAND = re.compile(r"^python3\s+(\S*ux_receipt\.py)\b")
COMMENT_PREFIX = re.compile(r"^#\s?")
QA_TRACE_TAG = re.compile(r"^#\s*qa-trace:\s*(\S+)\s*$")
# The runbook deliberately writes some snippets with `...` elisions; those are
# prose, not copy-paste commands, and are excluded rather than "fixed".
ELISION = "..."


def load_receipt_tool():
    """Import the real CLI module so its parser is the only flag authority."""
    spec = importlib.util.spec_from_file_location("ux_receipt_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def code_blocks(text, language):
    """Every fenced block of `language`, as (first_content_line_number, lines)."""
    blocks = []
    opened_at = None
    body = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if opened_at is None:
            if stripped == "```" + language:
                opened_at, body = number + 1, []
        elif stripped == "```":
            blocks.append((opened_at, body))
            opened_at = None
        else:
            body.append(line)
    return blocks


def logical_lines(lines):
    """Join `\\` continuations into one logical command per entry.

    Comment markers are stripped per physical line before joining, so a
    multi-line commented example cannot be silently truncated into something
    that happens to parse. Returns (offset_of_first_physical_line, commented,
    text).
    """
    joined = []
    buffer = None
    start = 0
    commented = False
    for offset, raw in enumerate(lines):
        stripped = raw.strip()
        body = COMMENT_PREFIX.sub("", stripped).lstrip()
        if buffer is None:
            start, commented, buffer = offset, stripped.startswith("#"), body
        else:
            buffer = f"{buffer} {body}"
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
        else:
            joined.append((start, commented, buffer))
            buffer = None
    if buffer is not None:
        joined.append((start, commented, buffer))
    return joined


class DocCommand:
    """One `ux_receipt.py` invocation lifted out of a documentation fence."""

    def __init__(self, path, line, block_start, executable, text, tag):
        self.path = path
        self.line = line
        self.block_start = block_start
        self.executable = executable
        self.text = text
        self.tag = tag

    def where(self):
        return f"{self.path}:{self.line}"

    def __repr__(self):
        kind = "executable" if self.executable else "illustrative"
        return f"<{kind} {self.where()}: {self.text}>"


def documented_commands(path, want_tags):
    """Extract every ux_receipt invocation from a document's bash fences.

    `want_tags` records the `# qa-trace: <route>` header a fence carries so
    check 2 can replay a route in document order; a fence with an executable
    invocation and no header is reported by the caller as a failure.
    """
    text = path.read_text(encoding="utf-8")
    commands = []
    untagged = []
    for block_start, lines in code_blocks(text, "bash"):
        tag = None
        for raw in lines:
            if raw.strip():
                match = QA_TRACE_TAG.match(raw.strip())
                tag = match.group(1) if match else None
                break
        block = []
        for offset, commented, joined in logical_lines(lines):
            if not UX_COMMAND.match(joined):
                continue
            block.append(DocCommand(
                path, block_start + offset, block_start, not commented, joined, tag))
        if want_tags and tag is None and any(item.executable for item in block):
            untagged.append(block_start)
        commands.extend(block)
    return commands, untagged


def episode_id():
    """A real id from the bank, read from the file's `id` field.

    `ux_receipt.py` resolves `episode:EP-NNN` against `evals/episodes/`, so the
    replay needs an id that is actually there. Deriving it from a filename is
    the exact shortcut the tool itself refuses to take.
    """
    for path in sorted(EPISODE_BANK.glob("EP-*.json")):
        declared = json.loads(path.read_text(encoding="utf-8")).get("id")
        if isinstance(declared, str) and declared:
            return declared
    raise AssertionError(f"no episode with a declared id under {EPISODE_BANK}")


def grounding_check_payload():
    """Build the transient `--grounding-check-file` from the doc's own example.

    The shape comes from the ```json block in `qa/SKILL.md` (so that block is
    parsed rather than trusted), but `presented_text` is rewritten to contain
    every candidate grounding verbatim: the fixture has to satisfy the
    verbatim-containment check the tool performs, and the doc's illustrative
    strings describe the fields instead of demonstrating a match.
    """
    blocks = code_blocks(SKILL_DOC.read_text(encoding="utf-8"), "json")
    for _, lines in blocks:
        payload = json.loads("\n".join(lines))
        if isinstance(payload, dict) and "candidates" in payload and "presented_text" in payload:
            groundings = [
                candidate["grounding"]
                for candidate in payload["candidates"]
                if isinstance(candidate, dict) and "grounding" in candidate
            ]
            payload["presented_text"] = " ".join([payload["presented_text"], *groundings])
            return payload
    raise AssertionError(
        f"{SKILL_DOC} documents --grounding-check-file but has no json block "
        "declaring candidates and presented_text")


def placeholders(workspace):
    """The documented angle-bracket stand-ins, resolved to values a CLI accepts.

    Deliberately small and explicit. `test_placeholder_map_has_no_dead_entries`
    fails if any entry stops appearing in the documents, so a stale entry is a
    red suite rather than dead code.
    """
    preview = workspace / "preview-card.html"
    final = workspace / "final-card.html"
    grounding = workspace / "grounding-check.json"
    preview.write_text("<h1>preview</h1>", encoding="utf-8")
    final.write_text("<h1>final</h1>", encoding="utf-8")
    grounding.write_text(json.dumps(grounding_check_payload(), ensure_ascii=False), encoding="utf-8")
    return {
        "<ID>": SESSION_ID,
        "<64-hex-digest>": hashlib.sha256(b"documented question surface").hexdigest(),
        "<preview-card.html>": str(preview),
        "<final-card.html>": str(final),
        "<grounding-check.json>": str(grounding),
        "EP-0NN": episode_id(),
        "#NN": "#520",
        # Runbook-only: it documents the route and client as stand-ins where
        # `qa/SKILL.md` writes concrete values. Parser-checked, never replayed.
        "<route>": "first_review",
        "<your-client>": "claude",
    }


def substitute(text, values):
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    return text


def tokenize(text, values):
    """Placeholders first, then shlex — a stand-in may contain shell-ish text."""
    return shlex.split(substitute(text, values), comments=True)


def archive_required_flags():
    """The `--require-*` flags `qa_env.sh` applies when archiving a run.

    Derived from the script, never copied here: archiving is what actually
    enforces the gate, so a literal list in this file would be a third place
    for the same fact to drift. Matched tolerantly (any `--require-*` inside
    the archive function) so an unrelated edit to that function does not need
    this parser updated in lockstep.
    """
    text = QA_ENV.read_text(encoding="utf-8")
    start = text.find("cmd_archive_receipt()")
    if start < 0:
        raise AssertionError(f"cannot locate cmd_archive_receipt in {QA_ENV}")
    end = text.find("\n}\n", start)
    if end < 0:
        raise AssertionError(f"cannot find the end of cmd_archive_receipt in {QA_ENV}")
    flags = sorted(set(re.findall(r"--require-[a-z-]+", text[start:end])))
    if not flags:
        raise AssertionError(
            f"no --require-* flag found in {QA_ENV}'s archive function; this check "
            "would silently pass, so it fails instead")
    return flags


class SkillCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_receipt_tool()
        cls.skill_commands, cls.untagged = documented_commands(SKILL_DOC, want_tags=True)
        cls.runbook_commands, _ = documented_commands(RUNBOOK, want_tags=False)
        cls.workspace = tempfile.TemporaryDirectory()
        cls.values = placeholders(pathlib.Path(cls.workspace.name))

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    # --- check 1 / 4: every documented invocation is parser-valid ------------

    def build_parser(self):
        """The real parser, with argparse's `prog` reading as the tool's name.

        Cosmetic only: argparse derives `prog` from `sys.argv[0]` when the
        parser and each subparser are built, which here would be this test
        file — naming the wrong program to whoever has to fix the doc.
        """
        original = sys.argv[0]
        sys.argv[0] = TOOL.name
        try:
            return self.tool.build_parser()
        finally:
            sys.argv[0] = original

    def parse(self, command):
        """Parse one documented invocation with the tool's own parser."""
        tokens = tokenize(command.text, self.values)
        self.assertEqual(tokens[0], "python3", f"{command.where()}: {command.text}")
        script = (SKILL_ROOT / tokens[1]).resolve()
        self.assertEqual(
            script, TOOL.resolve(),
            f"{command.where()} invokes {tokens[1]!r}, which is not the tool "
            f"({TOOL}) relative to the documented working directory {SKILL_ROOT}")
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                return self.build_parser().parse_args(tokens[2:])
        except SystemExit:
            # argparse exits rather than raising; the reason is on its stderr.
            reported = stderr.getvalue().strip().splitlines()
        # Reported outside the `except` block so the failure shows the doc line
        # to fix instead of argparse's internal traceback.
        self.fail(
            f"{command.where()} does not parse against {TOOL.name} build_parser():\n"
            f"  command: {command.text}\n"
            f"  {reported[-1] if reported else 'argparse exited without a message'}")

    def test_skill_commands_parse(self):
        self.assertTrue(self.skill_commands, f"no ux_receipt commands found in {SKILL_DOC}")
        for command in self.skill_commands:
            with self.subTest(line=command.line, text=command.text):
                self.parse(command)

    def test_runbook_commands_without_elisions_parse(self):
        checked = [item for item in self.runbook_commands if ELISION not in item.text]
        self.assertTrue(checked, f"no complete ux_receipt commands found in {RUNBOOK}")
        for command in checked:
            with self.subTest(line=command.line, text=command.text):
                self.parse(command)

    def test_placeholder_map_has_no_dead_entries(self):
        documented = "\n".join(
            item.text for item in [*self.skill_commands, *self.runbook_commands])
        for placeholder in self.values:
            self.assertIn(
                placeholder, documented,
                f"placeholder {placeholder!r} no longer appears in any documented "
                "command; drop it from the substitution map")

    # --- check 2: the documented order really produces a verifying trace -----

    def test_every_fence_with_a_command_declares_its_route(self):
        self.assertEqual(
            self.untagged, [],
            f"{SKILL_DOC} fences starting at line(s) {self.untagged} contain an "
            "executable ux_receipt.py command but no leading '# qa-trace: <route>' "
            "marker, so nothing replays them. Tag the fence with the route its "
            "commands belong to.")

    def routes(self):
        """Executable commands grouped by their fence's route tag, in doc order."""
        grouped = {}
        for command in self.skill_commands:
            if command.executable:
                grouped.setdefault(command.tag, []).append(command)
        return grouped

    def replay(self, commands, root):
        """Run the documented commands for real against a throwaway state root."""
        environment = {**os.environ}
        environment.pop("TRADE_COACH_HOME", None)
        for command in commands:
            tokens = tokenize(command.text, self.values)
            parsed = self.parse(command)
            # A stand-in this map does not know about would be replayed as the
            # literal string `<something>` and could still exit 0 — a green run
            # proving nothing. Fail on it instead.
            leftover = [token for token in tokens if "<" in token]
            self.assertEqual(
                leftover, [],
                f"{command.where()} uses placeholder(s) {leftover} that the "
                "substitution map does not resolve, so this command cannot be "
                f"replayed honestly.\n  command: {command.text}")
            if parsed.command == "verify":
                # The doc's own verify examples are checked by check 3 and
                # parsed above, but never executed: this test decides which
                # gates the replayed trace is held to.
                continue
            result = subprocess.run(
                [sys.executable, *tokens[1:], "--state-root", str(root)],
                cwd=SKILL_ROOT, env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(
                result.returncode, 0,
                f"{command.where()} failed when replayed:\n"
                f"  command: {command.text}\n  {result.stderr.strip()}")

    def verify(self, root, *gates):
        environment = {**os.environ}
        environment.pop("TRADE_COACH_HOME", None)
        return subprocess.run(
            [sys.executable, str(TOOL), "verify", "--session-id", SESSION_ID,
             "--state-root", str(root), *gates],
            cwd=SKILL_ROOT, env=environment, capture_output=True, text=True, check=False)

    def replay_route(self, route, *gates):
        commands = self.routes().get(route)
        self.assertTrue(commands, f"{SKILL_DOC} documents no '# qa-trace: {route}' commands")
        opener = self.parse(commands[0])
        self.assertEqual(
            opener.command, "start",
            f"{commands[0].where()}: the first command of the {route} trace must be "
            "`start`, otherwise the replay begins mid-trace")
        self.assertEqual(
            opener.route, route,
            f"{commands[0].where()}: fence is tagged '# qa-trace: {route}' but its "
            f"start declares --route {opener.route}")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.replay(commands, root)
            result = self.verify(root, *gates)
            self.assertEqual(
                result.returncode, 0,
                f"the documented {route} sequence does not verify:\n{result.stderr.strip()}")
            return json.loads(result.stdout)

    def test_documented_first_review_trace_verifies(self):
        # `--require-timing-integrity` is deliberately absent. A replay finishes
        # far inside the tool's 3-second minimum span, so timing integrity is
        # `suspect` BY CONSTRUCTION here — an honest, stated limit of this
        # oracle. Sleeping or rewriting timestamps to get past it would only
        # teach the suite to fake the one signal that exists to catch faking.
        report = self.replay_route(
            "first_review", "--require-owner-verdict", "--require-findings")
        self.assertEqual(report["status"], "pass")

    def test_documented_weekly_review_trace_verifies(self):
        # Held to the same gates as first_review, because the weekly wrap-up is
        # now written out rather than delegated to Step 5. That difference is
        # the point: a weekly verdict must carry `--memory pass|fail`, and an
        # example that borrowed Step 5's `not_applicable` would only bite at
        # archive time. Timing integrity is excluded for the same
        # replay-burst reason as first_review.
        report = self.replay_route(
            "weekly_review", "--require-owner-verdict", "--require-findings")
        self.assertEqual(report["status"], "pass")

    # --- check 3: the documented gate is not weaker than the enforced one ----

    def test_documented_verify_matches_the_archive_gate(self):
        """Every documented verify example, not just the first one found.

        The doc carries one per route. Checking only the first would let a
        future author weaken the other silently — and the weekly one is
        precisely where a weaker gate hides longest, since that route is walked
        less often.
        """
        required = archive_required_flags()
        checked = 0
        for command in self.skill_commands:
            parsed = self.parse(command)
            if parsed.command != "verify":
                continue
            checked += 1
            for flag in required:
                with self.subTest(line=command.line, flag=flag):
                    attribute = flag.lstrip("-").replace("-", "_")
                    self.assertTrue(
                        hasattr(parsed, attribute),
                        f"{QA_ENV} passes {flag} to verify, but the parser has no such flag")
                    self.assertTrue(
                        getattr(parsed, attribute),
                        f"{command.where()} documents a weaker gate than archiving "
                        f"applies: {QA_ENV}'s archive step requires {' '.join(required)}, "
                        f"and this example omits {flag}.\n  command: {command.text}")
        self.assertTrue(checked, f"{SKILL_DOC} documents no verify command to check")


if __name__ == "__main__":
    unittest.main()
