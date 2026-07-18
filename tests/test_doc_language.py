#!/usr/bin/env python3
"""Enforce English implementation surfaces and agent workflow boundaries."""

from pathlib import Path
import json
import re
import shlex


ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u3400-\u9fff]")
GTM_MARKDOWN_ALLOWLIST = {
    Path("README.md"),
    Path("README.zh-TW.md"),
}
ROOT_IMPLEMENTATION_DOCS = {
    Path("AGENTS.md"),
    Path("BACKLOG.md"),
    Path("CLAUDE.md"),
}
IMPLEMENTATION_DOC_DIRS = (
    Path("docs"),
    Path("evals"),
    Path("skills/fomo-kernel"),
    Path("tests/agent"),
)
ENGLISH_IMPLEMENTATION_ASSETS = (
    Path("skills/fomo-kernel/card-template.html"),
    Path("skills/fomo-kernel/copy/en.json"),
    Path("skills/fomo-kernel/evals/evals.json"),
)
SKILL_DIR = Path("skills/fomo-kernel")
AGENT_RUNTIME_SURFACES = (
    Path("AGENTS.md"),
    SKILL_DIR / "SKILL.md",
    SKILL_DIR / "card-spec.md",
    SKILL_DIR / "evals/evals.json",
    SKILL_DIR / "flows",
    SKILL_DIR / "references",
)
NON_NEGOTIABLE_SECTIONS = {
    Path("AGENTS.md"): "## Non-negotiable boundaries",
    Path("skills/fomo-kernel/SKILL.md"): "## Non-negotiable rules",
    Path("skills/fomo-kernel/references/agent-boundaries.md"): "The agent may not:",
}
REVIEW_COMMANDS = ("prepare", "resume", "preview", "finalize", "repair-projections")
LOCAL_FILE_REFERENCE = re.compile(
    r"`(?P<code_path>[^`\n]+\.(?:md|json))`"
    r"|\[[^\]]*\]\((?P<link_path>[^)\s]+\.(?:md|json)(?:#[^)]*)?)\)"
)
INLINE_CODE_SPAN = re.compile(r"`(?P<code>[^`\n]+)`")
MARKDOWN_CONTAINER_PREFIX = re.compile(
    r"^[ \t]*(?:>[ \t]*|[-+*][ \t]+|[0-9]+[.)][ \t]+)"
)
PYTHON_COMMAND_START = re.compile(r"(?<![A-Za-z0-9_.-])python(?:3(?:\.\d+)?)?\b")
ENGINE_SCRIPT_TOKEN = re.compile(
    r"(?:^|/)engine/(?P<module>[A-Za-z_][A-Za-z0-9_]*)\.py$"
)
DIRECT_ENGINE_CALL = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<path>\./(?:[^\s;|&`]+/)*engine/(?P<module>[A-Za-z_][A-Za-z0-9_]*)\.py)\b"
)
PYTHON_OPTIONS_WITH_VALUE = {"-W", "-X", "--check-hash-based-pycs"}
ENGINE_INTERNAL_MODULES = tuple(
    path.stem
    for path in sorted((ROOT / "skills/fomo-kernel/engine").glob("*.py"))
    if path.stem != "__init__"
)
SCRIPT_BYPASS_FIXTURES = (
    ("python option", "python3 -u engine/trade_recap.py mock.csv", ("trade_recap",)),
    (
        "multiline shell invocation",
        "python3 -u \\\n  engine/session.py repair",
        ("session",),
    ),
    (
        "prose apostrophe before command",
        "The user's workflow stays local.\npython3 -m engine.session repair",
        ("session",),
    ),
    ("direct executable", "./engine/coach.py close", ("coach",)),
    ("quoted script path", 'python3 "engine/session.py" repair', ("session",)),
    ("option with value", "python3 -X dev engine/session.py repair", ("session",)),
    ("module execution", "python3 -m engine.session repair", ("session",)),
    ("command-string import", 'python3 -c "import engine.session"', ("session",)),
    (
        "JSON eval string",
        '{"expectation": "python3 -m engine.session repair"}',
        ("session",),
    ),
    (
        "multiple same-line commands",
        "python3 engine/review.py prepare mock.csv; "
        "python3 engine/trade_recap.py mock.csv; ./engine/coach.py close",
        ("trade_recap", "coach"),
    ),
)
IMPORT_BYPASS_FIXTURES = (
    ("qualified from import", "from engine import trade_recap", "engine"),
    ("qualified module import", "import engine.session", "session"),
    ("bare internal import", "import trade_recap", "trade_recap"),
    ("inline code import", "Use `import engine.session`.", "session"),
    ("list import", "- import engine.session", "session"),
    ("list inline from import", "- `from engine import session`", "engine"),
    ("blockquote import", "> import engine.session", "session"),
)


def implementation_markdown_files():
    for rel in sorted(ROOT_IMPLEMENTATION_DOCS):
        yield rel, ROOT / rel
    for doc_dir in IMPLEMENTATION_DOC_DIRS:
        for path in sorted((ROOT / doc_dir).rglob("*.md")):
            rel = path.relative_to(ROOT)
            if rel not in GTM_MARKDOWN_ALLOWLIST:
                yield rel, path


def json_ref_values(value):
    if isinstance(value, list):
        for item in value:
            yield from json_ref_values(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                yield item
            yield from json_ref_values(item)


def local_file_references(text, suffix):
    for match in LOCAL_FILE_REFERENCE.finditer(text):
        yield match.group("code_path") or match.group("link_path")
    if suffix != ".json":
        return
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return
    for reference in json_ref_values(value):
        path = reference.split("#", 1)[0]
        if path.endswith((".md", ".json")):
            yield reference


def agent_runtime_files():
    files = set()
    for rel in AGENT_RUNTIME_SURFACES:
        path = ROOT / rel
        if path.is_file():
            files.add(rel)
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix in {".md", ".json"}:
                files.add(child.relative_to(ROOT))

    skill_root = (ROOT / SKILL_DIR).resolve()
    queue = list(files)
    while queue:
        source_rel = queue.pop()
        source = ROOT / source_rel
        if source.suffix not in {".md", ".json"}:
            continue
        text = source.read_text(encoding="utf-8")
        for raw_reference in local_file_references(text, source.suffix):
            reference = raw_reference.split("#", 1)[0]
            for base in (source.parent, skill_root):
                candidate = (base / reference).resolve()
                if (candidate.is_file()
                        and candidate.suffix in {".md", ".json"}
                        and candidate.is_relative_to(skill_root)):
                    rel = candidate.relative_to(ROOT)
                    if rel not in files:
                        files.add(rel)
                        queue.append(rel)
                    break

    for rel in sorted(files):
        yield rel, ROOT / rel


def normalize_shell_continuations(text):
    """Remove shell continuations without changing character offsets."""
    return re.sub(r"\\\r?\n", lambda match: " " * len(match.group(0)), text)


def shell_command_segments(text):
    """Yield quote-aware shell segments with their offsets in ``text``."""
    start = 0
    quote = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            previous = text[index - 1] if index else " "
            if previous.isspace() or previous in "=([{;|&":
                quote = char
        elif char in {"\n", ";", "|", "&"}:
            if text[start:index].strip():
                yield start, index, text[start:index]
            start = index + 1
    if text[start:].strip():
        yield start, len(text), text[start:]


def clean_shell_token(token):
    return token.strip().strip("`").rstrip("`),.:")


def inspect_python_tokens(tokens):
    """Return forbidden engine operations represented by one Python argv."""
    findings = []
    index = 1
    while index < len(tokens):
        token = clean_shell_token(tokens[index])
        if token == "--":
            index += 1
            continue
        if token == "-m":
            if index + 1 < len(tokens):
                module_name = clean_shell_token(tokens[index + 1])
                if module_name == "engine" or module_name.startswith("engine."):
                    module = module_name.split(".", 1)[1] if "." in module_name else "engine"
                    if module != "review":
                        findings.append({"kind": "module", "module": module})
            return findings
        if token == "-c":
            if index + 1 < len(tokens):
                for violation in forbidden_engine_imports(tokens[index + 1]):
                    findings.append({"kind": "command-import", "module": violation["module"]})
            return findings
        if token in PYTHON_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue

        script = ENGINE_SCRIPT_TOKEN.search(token)
        if script and script.group("module") != "review":
            findings.append(
                {"kind": "python", "module": script.group("module"), "path_token": token}
            )
        return findings
    return findings


def _forbidden_engine_script_calls_plain(text):
    normalized = normalize_shell_continuations(text)
    calls = []
    python_path_spans = set()
    for segment_start, _, segment in shell_command_segments(normalized):
        python = PYTHON_COMMAND_START.search(segment)
        if not python:
            continue
        command_start = segment_start + python.start()
        command = segment[python.start():].strip()
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            continue
        for finding in inspect_python_tokens(tokens):
            path_token = finding.pop("path_token", None)
            if path_token:
                relative_start = command.rfind(path_token)
                if relative_start >= 0:
                    path_start = command_start + relative_start
                    python_path_spans.add((path_start, path_start + len(path_token)))
            calls.append(
                {
                    **finding,
                    "_offset": command_start,
                    "line": text.count("\n", 0, command_start) + 1,
                    "command": command,
                }
            )

    for match in DIRECT_ENGINE_CALL.finditer(normalized):
        path_span = (match.start("path"), match.end("path"))
        if path_span in python_path_spans or match.group("module") == "review":
            continue
        calls.append(
            {
                "kind": "executable",
                "_offset": match.start(),
                "line": text.count("\n", 0, match.start()) + 1,
                "module": match.group("module"),
                "command": match.group(0),
            }
        )

    calls.sort(key=lambda item: item["_offset"])
    for call in calls:
        call.pop("_offset")
    return calls


def _forbidden_engine_imports_plain(text):
    violations = []
    from_import = re.compile(
        r"(?m)(?:^|;)[ \t]*from[ \t]+(?P<module>[A-Za-z_][A-Za-z0-9_.]*)[ \t]+import\b"
    )
    plain_import = re.compile(r"(?m)(?:^|;)[ \t]*import[ \t]+(?P<body>[^#;\n]+)")

    for match in from_import.finditer(text):
        name = match.group("module")
        root, *rest = name.split(".")
        if root == "engine" or root in ENGINE_INTERNAL_MODULES:
            module = rest[0] if root == "engine" and rest else root
            violations.append({"line": text.count("\n", 0, match.start()) + 1, "module": module})

    for match in plain_import.finditer(text):
        for imported in match.group("body").split(","):
            tokens = imported.strip().split()
            if not tokens:
                continue
            name = tokens[0]
            root, *rest = name.split(".")
            if root == "engine" or root in ENGINE_INTERNAL_MODULES:
                module = rest[0] if root == "engine" and rest else root
                violations.append({"line": text.count("\n", 0, match.start()) + 1, "module": module})
    return sorted(violations, key=lambda item: (item["line"], item["module"]))


def strip_markdown_container(line):
    previous = None
    while line != previous:
        previous = line
        line = MARKDOWN_CONTAINER_PREFIX.sub("", line, count=1)
    return line


def markdown_import_fragments(text):
    yield text, 0
    normalized = "\n".join(strip_markdown_container(line) for line in text.split("\n"))
    if normalized != text:
        yield normalized, 0
    for match in INLINE_CODE_SPAN.finditer(text):
        line_offset = text.count("\n", 0, match.start("code"))
        yield match.group("code"), line_offset


def json_string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from json_string_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from json_string_values(item)


def contract_text_fragments(text):
    """Decode JSON instruction strings; otherwise scan the source text once."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        yield text
        return
    yield from json_string_values(value)


def forbidden_engine_imports(text):
    violations = []
    for contract_fragment in contract_text_fragments(text):
        for fragment, line_offset in markdown_import_fragments(contract_fragment):
            for violation in _forbidden_engine_imports_plain(fragment):
                violations.append(
                    {**violation, "line": violation["line"] + line_offset}
                )
    return violations


def forbidden_engine_script_calls(text):
    calls = []
    for fragment in contract_text_fragments(text):
        calls.extend(_forbidden_engine_script_calls_plain(fragment))
    return calls


def markdown_section(text, heading):
    start = text.find(heading)
    assert start >= 0, f"Missing section: {heading}"
    content_start = start + len(heading)
    next_heading = text.find("\n## ", content_start)
    return text[content_start:] if next_heading < 0 else text[content_start:next_heading]


def test_implementation_markdown_is_english_only():
    violations = []
    for rel, path in implementation_markdown_files():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CJK.search(line):
                violations.append(f"{rel}:{line_number}: {line.strip()}")
    assert not violations, "Non-English text found in implementation docs:\n" + "\n".join(violations)


def test_english_skill_assets_are_english_only():
    paths = [ROOT / rel for rel in ENGLISH_IMPLEMENTATION_ASSETS]
    paths.extend(sorted((ROOT / "skills/fomo-kernel/rubric").glob("*.lens.json")))
    violations = []
    for path in paths:
        rel = path.relative_to(ROOT)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CJK.search(line):
                violations.append(f"{rel}:{line_number}: {line.strip()}")
    assert not violations, "Non-English text found in English skill assets:\n" + "\n".join(violations)


def test_gtm_locale_pair_exists():
    for rel in GTM_MARKDOWN_ALLOWLIST:
        assert (ROOT / rel).is_file(), f"Missing GTM locale file: {rel}"


def test_review_py_is_a_non_negotiable_boundary():
    for rel, heading in NON_NEGOTIABLE_SECTIONS.items():
        section = markdown_section((ROOT / rel).read_text(encoding="utf-8"), heading)
        assert "`engine/review.py`" in section, f"{rel}: review.py is not a boundary"
        lowered = section.lower()
        assert "call another `engine/*` script" in lowered, f"{rel}: direct calls are not forbidden"
        assert "import engine modules directly" in lowered, f"{rel}: direct imports are not forbidden"
        for command in REVIEW_COMMANDS:
            assert f"`{command}`" in section, f"{rel}: boundary omits {command}"


def test_agent_runtime_surface_scope_is_bounded():
    paths = {rel for rel, _ in agent_runtime_files()}
    required = {
        Path("AGENTS.md"),
        SKILL_DIR / "SKILL.md",
        SKILL_DIR / "card-spec.md",
        SKILL_DIR / "evals/evals.json",
        SKILL_DIR / "schemas/answers.schema.json",
        SKILL_DIR / "schemas/narrative.schema.json",
        SKILL_DIR / "schemas/review-plan.schema.json",
        SKILL_DIR / "schemas/session-bundle.schema.json",
    }
    assert required <= paths, f"Missing agent runtime surfaces: {sorted(required - paths)}"
    assert Path("README.md") not in paths
    assert Path("README.zh-TW.md") not in paths
    assert Path("CLAUDE.md") not in paths
    assert Path("BACKLOG.md") not in paths
    assert all(path == Path("AGENTS.md") or path.is_relative_to(SKILL_DIR) for path in paths)
    assert not any(path.is_relative_to(SKILL_DIR / "engine") for path in paths)


def test_json_ref_contract_links_are_discoverable():
    payload = '{"allOf": [{"$ref": "schemas/review-plan.schema.json#/$defs/plan"}]}'
    references = set(local_file_references(payload, ".json"))
    assert "schemas/review-plan.schema.json#/$defs/plan" in references


def test_agent_runtime_surfaces_only_invoke_review_py():
    violations = []
    for rel, path in agent_runtime_files():
        for match in forbidden_engine_script_calls(path.read_text(encoding="utf-8")):
            violations.append(f"{rel}:{match['line']}: {match['command']}")
    assert not violations, "Agent workflow invokes an engine internal directly:\n" + "\n".join(violations)


def test_agent_runtime_surfaces_do_not_import_engine_internals():
    violations = []
    for rel, path in agent_runtime_files():
        for match in forbidden_engine_imports(path.read_text(encoding="utf-8")):
            violations.append(f"{rel}:{match['line']}: {match['module']}")
    assert not violations, "Agent workflow imports an engine internal directly:\n" + "\n".join(violations)


def test_snapshot_runtime_uses_raw_facts_through_review_only():
    surfaces = [Path("AGENTS.md"), SKILL_DIR / "SKILL.md",
                SKILL_DIR / "flows/snapshot-review.md"]
    for rel in surfaces:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "--snapshot-json" in text, f"{rel}: snapshot entry point is missing"
        assert "engine/review.py" in text, f"{rel}: snapshot bypasses review.py"
    flow = (ROOT / SKILL_DIR / "flows/snapshot-review.md").read_text(encoding="utf-8")
    assert "--card-json" not in flow and "--state-json" not in flow, \
        "runtime flow must not ask the agent to assemble engine artifacts"
    contract = (ROOT / SKILL_DIR / "references/data-contract.md").read_text(encoding="utf-8")
    for field in ('"as_of"', '"positions"', '"ticker"', '"shares"', '"market"', '"currency"'):
        assert field in contract, f"snapshot contract omits {field}"
    assert "no OCR or cloud-upload path" in flow
    assert "Do not calculate weights" in flow
    # #214: *.json cannot be blanket-ignored, so the gitignore backstop only
    # covers the one recommended filename. The doc instruction to keep the
    # envelope outside the repository is the only defense layer for any other
    # filename — lock the wording so a future edit cannot silently drop it.
    assert "outside the repository" in flow, \
        "flows/snapshot-review.md must keep the temp-envelope privacy instruction"
    assert "outside the repository" in contract, \
        "references/data-contract.md must keep the temp-envelope privacy instruction"


def test_engine_script_bypass_mutations_are_caught():
    for label, mutation, expected_modules in SCRIPT_BYPASS_FIXTURES:
        found = tuple(match["module"] for match in forbidden_engine_script_calls(mutation))
        assert found == expected_modules, f"{label}: expected {expected_modules}, found {found}"


def test_engine_import_bypass_mutations_are_caught():
    for label, mutation, expected_module in IMPORT_BYPASS_FIXTURES:
        found = {match["module"] for match in forbidden_engine_imports(mutation)}
        assert expected_module in found, f"{label}: expected {expected_module}, found {sorted(found)}"


def main():
    tests = [
        test_implementation_markdown_is_english_only,
        test_english_skill_assets_are_english_only,
        test_gtm_locale_pair_exists,
        test_review_py_is_a_non_negotiable_boundary,
        test_agent_runtime_surface_scope_is_bounded,
        test_json_ref_contract_links_are_discoverable,
        test_agent_runtime_surfaces_only_invoke_review_py,
        test_agent_runtime_surfaces_do_not_import_engine_internals,
        test_snapshot_runtime_uses_raw_facts_through_review_only,
        test_engine_script_bypass_mutations_are_caught,
        test_engine_import_bypass_mutations_are_caught,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} documentation and agent workflow tests")


if __name__ == "__main__":
    main()
