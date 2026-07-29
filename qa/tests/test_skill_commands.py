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

Seven checks, none of which restate the tool's vocabulary:

1. every documented invocation parses against the real `build_parser()`;
2. every documented route's order, replayed for real, produces a trace that
   verifies — the routes come from the doc's own fences, so a fence nothing
   executes is a failure rather than an omission;
3. the doc's `verify` example is not weaker than the gate `qa_env.sh` applies
   at archive time (derived from that script, not copied into this file);
4. the public runbook's non-placeholder invocations still parse;
5. every route the tool supports is either walked by the doc or explicitly
   exempted with a reason — a route grown without a walkthrough is a silent
   invitation to improvise a receipt;
6. the runbook's two independent gate enumerations agree on numbering and
   order (#527) — structure only, never wording, since the two documents are
   in different languages by design;
7. `qa/` is English outside declared trigger phrases, which is the check that
   was missing when this directory was copied in carrying another language.

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

# --- issue #527: the runbook states its gates twice, ~50 lines apart --------
#
# `docs/qa-runbook.md` enumerates its QA gates in two independent places: a
# numbered list under RUNBOOK_GATE_LIST_HEADING (`1. **Version gate** — ...`
# through `7. **Findings disposition** — ...`), and an enforcement table
# further down (`| 1. Version | ... |` through `| 7. Findings | ... |`, plus
# a lettered sub-row `| 3b. Grounding fidelity | ... |` the list has no
# counterpart for). Nothing keeps the two in sync; see GateCountConsistencyTest
# below for what this checks and, just as importantly, what it deliberately
# does not.
RUNBOOK_GATE_LIST_HEADING = "## What counts as a QA run (fail closed)"
RUNBOOK_GATE_LIST_ITEM = re.compile(r"^(\d+)\.\s+\*\*", re.MULTILINE)
RUNBOOK_TABLE_HEADER = "| Gate | Machine-enforced by | Procedural part |"
RUNBOOK_TABLE_ROW = re.compile(r"^\|\s*(\d+)([A-Za-z]?)\.\s+\S", re.MULTILINE)

# Routes `ux_receipt.py` accepts for which `SKILL.md` documents no walkthrough,
# each with the reason it is absent. Every other supported route must have a
# `# qa-trace:` fence.
#
# This map is deliberately hand-written and deliberately tiny, because it is
# the one place a newly supported route has to be looked at. The rest of this
# file stops a documented command from drifting; nothing stopped the skill from
# having no walkthrough at all for a route the tool grew — the operator would
# then improvise one, which is the same failure one level up, and a receipt
# improvised at 11pm is exactly what gate 3 exists to refuse.
#
# The test below fails in both directions: an undocumented, unexempted route,
# and an exemption for a route that is documented or no longer exists. An
# exemption that stops being true is as bad as a missing one.
UNDOCUMENTED_ROUTES = {
    "test_drive": (
        "the demo route: persist:false, no owner_live acceptance journey rides "
        "it, and docs/qa-runbook.md already states it is not evidence for "
        "first-review or weekly-memory behavior. Permanent, not a backlog item."
    ),
}


# --- language: qa/ is developer documentation, and that means English --------
#
# `docs/language-policy.md` requires English for implementation and developer
# surfaces. `tests/test_doc_language.py` enforces it, but its scan covers
# `docs/`, `evals/`, `skills/fomo-kernel/` and `tests/agent/` — not `qa/`, which
# arrived later by being *copied* out of a personal skill directory, carrying
# the Traditional Chinese it had been written in. Nothing checked, so it sat in
# a public repository in the wrong language, and the cost was concrete: the two
# QA documents could not be kept in sync by generating one from the other,
# because generating across a language boundary is translation.
#
# CJK is permitted on exactly the lines below, for exactly one reason: a
# skill's trigger phrases are the words the maintainer actually types. They are
# data, not prose — translating them would stop `/fomo-qa` matching a request
# made in Chinese. Everything else in `qa/` is English.
# Written as escapes, not literal characters, so this file does not trip its own
# scan — the first cut did exactly that, and the failure was indistinguishable
# from a real one.
CJK = re.compile(r"[\u3400-\u9fff]")
CJK_ALLOWED_PREFIXES = {
    "SKILL.md": (
        # the frontmatter description, which is what the host matches against
        "description:",
        # the same trigger phrases restated for a human reader
        "- The user says",
    ),
}


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
        # A refresh creates no session, so its trace is keyed by the engine's
        # own refresh_id. The doc writes that stand-in under its real name
        # rather than reusing `<ID>`, because reading "session id" on a lane
        # that has no session is how an operator ends up inventing one.
        "<refresh_id>": SESSION_ID,
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


def runbook_gate_list():
    """The runbook's `N. **Name** — ...` gate numbers, in document order.

    Scoped to the body of RUNBOOK_GATE_LIST_HEADING's section (up to the next
    top-level heading) so a numbered list elsewhere in the document — there is
    none today, but nothing stops one appearing — cannot be swept in.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    start = text.find(RUNBOOK_GATE_LIST_HEADING)
    if start < 0:
        raise AssertionError(f"{RUNBOOK}: heading {RUNBOOK_GATE_LIST_HEADING!r} not found")
    start += len(RUNBOOK_GATE_LIST_HEADING)
    next_heading = re.search(r"^## ", text[start:], re.MULTILINE)
    body = text[start:start + next_heading.start()] if next_heading else text[start:]
    numbers = [int(match.group(1)) for match in RUNBOOK_GATE_LIST_ITEM.finditer(body)]
    if not numbers:
        raise AssertionError(
            f"{RUNBOOK}: RUNBOOK_GATE_LIST_ITEM matched no numbered gate under "
            f"{RUNBOOK_GATE_LIST_HEADING!r}; the heading text or the list's "
            "markup changed and this regex needs updating, not silently skipping")
    return numbers


def runbook_enforcement_table_rows():
    """The enforcement table's `(number, letter)` row keys, in document order.

    `letter` is `""` for a top-level gate row (`1.`) and e.g. `"b"` for a
    lettered sub-row (`3b.`). The table's extent is found by scanning forward
    from its header line for the first line break not immediately followed by
    another `|`-prefixed line — i.e. the first line that falls out of the
    table — so trailing prose is never mistaken for a row.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    start = text.find(RUNBOOK_TABLE_HEADER)
    if start < 0:
        raise AssertionError(f"{RUNBOOK}: enforcement table header {RUNBOOK_TABLE_HEADER!r} not found")
    end = re.search(r"\n(?!\|)", text[start:])
    table = text[start:start + end.start()] if end else text[start:]
    rows = [
        (int(match.group(1)), match.group(2).lower())
        for match in RUNBOOK_TABLE_ROW.finditer(table)
    ]
    if not rows:
        raise AssertionError(
            f"{RUNBOOK}: RUNBOOK_TABLE_ROW matched no row under header "
            f"{RUNBOOK_TABLE_HEADER!r}; the table's markup changed and this "
            "regex needs updating, not silently skipping")
    return rows


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

    def test_every_receipt_route_is_documented_or_explicitly_exempt(self):
        """A route the tool supports but the skill never walks is a silent hole.

        Checks 1-4 all assume a documented command exists to check. This one
        asks the question they cannot: did the tool grow a route the skill
        forgot? Read from `ux_receipt.ROUTES` rather than restated, so adding a
        route there is what forces the decision.
        """
        supported = set(self.tool.ROUTES)
        documented = set(self.routes())
        exempt = set(UNDOCUMENTED_ROUTES)

        self.assertEqual(
            sorted(supported - documented - exempt), [],
            f"{SKILL_DOC} documents no walkthrough for these ux_receipt routes, and "
            "UNDOCUMENTED_ROUTES gives no reason for their absence. Either add a "
            "'# qa-trace: <route>' fence, or record why the route has none.")
        self.assertEqual(
            sorted(exempt - supported), [],
            "UNDOCUMENTED_ROUTES excuses a route ux_receipt.ROUTES no longer has; "
            "drop the stale entry rather than leaving a reason nothing needs.")
        self.assertEqual(
            sorted(exempt & documented), [],
            "UNDOCUMENTED_ROUTES claims a route is undocumented, but SKILL.md now "
            "walks it. Remove the exemption so the reason cannot outlive its truth.")

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

    def replay_gates(self):
        """The archive gate, minus the one a replay cannot honestly satisfy.

        Derived from `qa_env.sh` like check 3 rather than listed here, so a gate
        added to archiving is applied to the replay too instead of being
        remembered. `--require-timing-integrity` is the single deliberate
        exclusion: a replay finishes far inside the tool's 3-second minimum
        span, so timing integrity is `suspect` BY CONSTRUCTION — an honest,
        stated limit of this oracle. Sleeping or rewriting timestamps to get
        past it would only teach the suite to fake the one signal that exists
        to catch faking. Naming the excluded flag as a literal is safe in the
        one direction that matters: if it is ever renamed, this stops matching
        and the replay goes red rather than quietly passing.
        """
        return [flag for flag in archive_required_flags()
                if flag != "--require-timing-integrity"]

    def test_every_documented_route_trace_verifies(self):
        """Replay every `# qa-trace:` route, not a hand-picked list of them.

        This used to be one test method per route, and adding a route meant
        remembering to add a method — the same shape of hole `UNDOCUMENTED_ROUTES`
        exists to close one level up. `snapshot_review` and `refresh` (#526) are
        the routes that made the difference concrete: a fence can be written,
        pass the parser check, satisfy the exemption check, and still never be
        executed.

        What the loop holds each route to is the archive gate, so a route-specific
        wrap-up cannot document something weaker than what archiving applies. Two
        such differences are live today and only bite at archive time: a weekly
        verdict must carry `--memory pass|fail` where a first or snapshot review
        may say `not_applicable`, and the card-free `refresh` verdict must carry
        `--card not_applicable` plus `--change`.

        The coverage assertion is what stops this passing vacuously: replaying
        zero fences, or all but the one someone just added, is a failure rather
        than a green run.
        """
        expected = set(self.tool.ROUTES) - set(UNDOCUMENTED_ROUTES)
        replayed = {route for route in self.routes() if route in expected}
        self.assertEqual(
            replayed, expected,
            "the routes this test actually replayed do not cover every supported, "
            f"non-exempt route: replayed {sorted(replayed)}, expected {sorted(expected)}")
        for route in sorted(replayed):
            with self.subTest(route=route):
                report = self.replay_route(route, *self.replay_gates())
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


class QaDirectoryLanguageTest(unittest.TestCase):
    """Everything under `qa/` is English, except declared trigger phrases.

    Deliberately scoped to `qa/` rather than added to
    `tests/test_doc_language.py`: that suite's allowlist is whole-file, which
    here would license the skill to drift back into Chinese wholesale. The
    exception this needs is line-level and reasoned, so it is declared as one.
    """

    def scanned_files(self):
        files = sorted(
            path for pattern in ("*.md", "*.py", "*.sh")
            for path in QA_DIR.rglob(pattern)
        )
        # A scan that finds nothing to scan is not a passing scan.
        self.assertTrue(files, f"no documentation or source files found under {QA_DIR}")
        return files

    def test_qa_directory_is_english_outside_declared_trigger_phrases(self):
        offenders = []
        for path in self.scanned_files():
            allowed = CJK_ALLOWED_PREFIXES.get(path.name, ())
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not CJK.search(line):
                    continue
                if any(line.lstrip().startswith(prefix) for prefix in allowed):
                    continue
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()[:90]}")
        self.assertEqual(
            offenders, [],
            "qa/ is developer documentation and docs/language-policy.md requires English "
            "there. Translate these lines, or — if one is a trigger phrase the maintainer "
            "actually types — add its line prefix to CJK_ALLOWED_PREFIXES with the reason:\n  "
            + "\n  ".join(offenders))

    def test_declared_trigger_phrase_exceptions_are_all_still_used(self):
        """An exception nothing needs is an exception that will be reused wrongly."""
        for name, prefixes in CJK_ALLOWED_PREFIXES.items():
            path = QA_DIR / name
            self.assertTrue(path.is_file(), f"CJK_ALLOWED_PREFIXES names {name}, which does not exist")
            lines = path.read_text(encoding="utf-8").splitlines()
            for prefix in prefixes:
                matched = [
                    line for line in lines
                    if line.lstrip().startswith(prefix) and CJK.search(line)
                ]
                self.assertTrue(
                    matched,
                    f"CJK_ALLOWED_PREFIXES excuses {name} lines starting {prefix!r}, but no such "
                    "line carries CJK any more. Drop the entry rather than leaving a licence "
                    "nothing needs.")


class GateCountConsistencyTest(unittest.TestCase):
    """`docs/qa-runbook.md` enumerates its QA gates twice — a numbered list
    under `## What counts as a QA run (fail closed)`, and an enforcement
    table roughly fifty lines later — and nothing forces the two to agree.
    That is the gap issue #527 is about: a gate can be added, removed, or
    renumbered in one enumeration and not the other, and both still read as
    complete on their own.

    This class pins STRUCTURE only: which gate numbers exist, and in what
    order, in each enumeration. It does not, and must not, compare gate
    NAMES between the list and the table (e.g. "Version gate" vs.
    "Version") — those are two independent phrasings of the same gate and
    are free to word it differently. Do not "improve" this into a wording
    match; that would pin prose this repository has no rule requiring to
    match, only numbering it does.

    `qa/SKILL.md` also states a gate count — in Traditional Chinese CJK
    numerals, in more than one paragraph — and is not checked here. It is
    about to be converted from Traditional Chinese to English, so a check
    anchored to its current CJK wording would fail on contact with that
    rewrite; a follow-up check belongs beside `runbook_gate_list()` once the
    conversion lands, comparing the skill's (English) stated count the same
    way `test_enforcement_table_matches_gate_list` below compares the table.
    """

    def test_runbook_gate_list_is_contiguous(self):
        numbers = runbook_gate_list()
        self.assertEqual(
            len(numbers), len(set(numbers)),
            f"{RUNBOOK}: the numbered gate list under "
            f"{RUNBOOK_GATE_LIST_HEADING!r} repeats a gate number: "
            f"{sorted(numbers)}. Renumber so each gate appears exactly once.")
        total = len(numbers)
        self.assertEqual(
            sorted(numbers), list(range(1, total + 1)),
            f"{RUNBOOK}: the numbered gate list under "
            f"{RUNBOOK_GATE_LIST_HEADING!r} is not contiguous starting at 1: "
            f"found {sorted(numbers)}. A gap means some later reference to "
            "'gate N' does not point at what its author thinks it does.")

    def test_enforcement_table_matches_gate_list(self):
        gate_numbers = runbook_gate_list()
        rows = runbook_enforcement_table_rows()
        top_level = [number for number, letter in rows if not letter]
        sub_rows = [(number, letter) for number, letter in rows if letter]

        self.assertEqual(
            top_level, gate_numbers,
            f"{RUNBOOK}: the enforcement table's top-level rows are "
            f"{top_level} but the numbered gate list under "
            f"{RUNBOOK_GATE_LIST_HEADING!r} is {gate_numbers}. Add, remove, or "
            "reorder a table row so the two enumerate the same gate numbers "
            "in the same order — the gate NAMES may still differ in wording.")

        gate_set = set(gate_numbers)
        for number, letter in sub_rows:
            self.assertIn(
                number, gate_set,
                f"{RUNBOOK}: enforcement table sub-row '{number}{letter}.' "
                f"attaches to gate {number}, which is not one of the numbered "
                f"gates {sorted(gate_set)}. Point the sub-row at a real gate "
                "number, or remove it if the gate it described is gone.")


if __name__ == "__main__":
    unittest.main()
