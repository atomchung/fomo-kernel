#!/usr/bin/env python3
"""Deterministic probes for tools/privacy_lint.py (#274 text-channel gate).

All reference data here is synthetic: fake tickers and amounts that exist in
no real ledger. The suite proves the gate fails closed (unreadable or empty
reference sets are errors, not passes) and that findings are masked so lint
output itself is safe to share.
"""

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills" / "fomo-kernel" / "tools" / "privacy_lint.py"

REFERENCE_CSV = """Symbol,Quantity,Price,Action,Description,TradeDate,Amount,RecordType
ZZZT,10,150.00,BUY,BOUGHT SYNTHETIC CORP,2023-01-10,-98765.00,Trade
9876.TW,1000,550.00,BUY,BOUGHT SYNTHETIC TW,2023-03-01,-4321.00,Trade
,,,,DEPOSIT USD FUNDING,2023-03-10,55443322.00,Deposit
"""


def run_lint(text, *reference_paths):
    args = [sys.executable, str(TOOL)]
    for path in reference_paths:
        args += ["--against", str(path)]
    args.append("-")
    return subprocess.run(args, input=text, capture_output=True, text=True)


def with_reference(callback):
    with tempfile.TemporaryDirectory() as tmp:
        reference = pathlib.Path(tmp) / "reference.csv"
        reference.write_text(REFERENCE_CSV, encoding="utf-8")
        callback(pathlib.Path(tmp), reference)


def test_clean_draft_passes():
    def check(_tmp, reference):
        done = run_lint("The account is concentrated in a few AI names.\n", reference)
        assert done.returncode == 0, done.stderr
        assert "PASS" in done.stdout
        # The pass line proves the reference set actually loaded (fail-closed UX).
        assert "tickers" in done.stdout and "amounts" in done.stdout
    with_reference(check)


def test_reference_ticker_fails_case_insensitive():
    def check(_tmp, reference):
        done = run_lint("I trimmed zzzt last week.\n", reference)
        assert done.returncode == 1, done.stdout + done.stderr
        assert "ticker" in done.stdout
    with_reference(check)


def test_dotted_tw_symbol_also_matches_bare_code():
    def check(_tmp, reference):
        done = run_lint("Concentration in 9876 is high.\n", reference)
        assert done.returncode == 1, done.stdout + done.stderr
        assert "ticker" in done.stdout
    with_reference(check)


def test_reference_amount_matches_plain_and_comma_grouped():
    def check(_tmp, reference):
        for variant in ("Deposited 98765 that month.", "Deposited 98,765.00 that month."):
            done = run_lint(variant + "\n", reference)
            assert done.returncode == 1, f"{variant!r}: {done.stdout}{done.stderr}"
            assert "amount" in done.stdout, variant
    with_reference(check)


def test_position_id_format_is_reported():
    def check(_tmp, reference):
        done = run_lint("Position ZZZT#2023-01-10#1 lacks a thesis.\n", reference)
        assert done.returncode == 1, done.stdout + done.stderr
        assert "position-id" in done.stdout
        # The embedded ticker/date must not double-report.
        assert "ticker" not in done.stdout
    with_reference(check)


def test_findings_are_masked():
    def check(_tmp, reference):
        done = run_lint("zzzt and 55443322 and ZZZT#2023-01-10#1\n", reference)
        assert done.returncode == 1
        for secret in ("ZZZT", "zzzt", "55443322", "2023-01-10"):
            assert secret not in done.stdout, f"unmasked {secret!r} in lint output"
    with_reference(check)


def test_short_numbers_are_not_flagged():
    # 3-digit values (prices like 550) would flood prose with false positives;
    # only 4+ digit integer parts enter the reference set.
    def check(_tmp, reference):
        done = run_lint("Bought at 550 and sold at 150.\n", reference)
        assert done.returncode == 0, done.stdout + done.stderr
    with_reference(check)


def test_missing_reference_fails_closed():
    done = run_lint("anything\n", "/nonexistent/none.csv")
    assert done.returncode == 2
    assert "not found" in done.stderr


def test_empty_reference_set_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        reference = pathlib.Path(tmp) / "empty.csv"
        reference.write_text("Notes\nhello\n", encoding="utf-8")
        done = run_lint("anything\n", reference)
        assert done.returncode == 2, done.stdout + done.stderr
        assert "no tickers or amounts" in done.stderr


def test_draft_file_mode():
    def check(tmp, reference):
        draft = tmp / "draft.md"
        draft.write_text("Position 9876.TW went up.\n", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(TOOL), "--against", str(reference), str(draft)],
            capture_output=True, text=True,
        )
        assert done.returncode == 1
        assert "ticker" in done.stdout
    with_reference(check)


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} privacy lint tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
