#!/usr/bin/env python3
"""Enforce English implementation surfaces and explicit locale boundaries."""

from pathlib import Path
import re


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


def implementation_markdown_files():
    for rel in sorted(ROOT_IMPLEMENTATION_DOCS):
        yield rel, ROOT / rel
    for doc_dir in IMPLEMENTATION_DOC_DIRS:
        for path in sorted((ROOT / doc_dir).rglob("*.md")):
            rel = path.relative_to(ROOT)
            if rel not in GTM_MARKDOWN_ALLOWLIST:
                yield rel, path


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


def main():
    tests = [
        test_implementation_markdown_is_english_only,
        test_english_skill_assets_are_english_only,
        test_gtm_locale_pair_exists,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} documentation language tests")


if __name__ == "__main__":
    main()
