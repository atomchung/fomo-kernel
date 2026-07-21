#!/usr/bin/env python3
"""Scan draft text against real trade CSVs before posting it publicly.

This repository is public. `.gitignore` blocks real trade CSVs from ever being
committed, but that guard only covers the FILE channel. Issue #274 documented
the TEXT channel leak: dogfood findings written into public issues/PR comments
carried real tickers, amounts, and position ids as supporting evidence. This
tool is the mechanical gate for that channel: run every draft (issue body,
PR body, comment, commit message) through it BEFORE posting, whenever the
dogfood session touched real trade data.

What it detects, derived from the four real leaks in #274:

- ticker:      any Symbol value from the reference CSVs appearing in the draft
               (word-bounded, case-insensitive; `2330.TW` also matches `2330`)
- amount:      any reference CSV numeric value with an integer part of 4+
               digits, in plain or thousands-separated form (`13500`,
               `13,500.00`) — deposit/position amounts, not everyday numbers
- position-id: the internal `TICKER#YYYY-MM-DD#n` position identifier format,
               reported regardless of the reference set (the format itself
               implies a real ledger row)

Behavior is fail-closed: an unreadable reference CSV, or a reference set that
yields no tokens at all, is an error (exit 2) — never a silent pass. Findings
are printed MASKED (first/last characters only) so the lint output itself is
safe to share. Exit codes: 0 clean, 1 findings, 2 usage/reference error.

Usage:
  python3 tools/privacy_lint.py --against ~/private/trades.csv draft.md
  gh issue view 123 --json body -q .body | \
      python3 tools/privacy_lint.py --against ~/private/trades.csv -

The reference CSVs are read locally and never echoed; only masked fragments
and aggregate counts appear in output.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import sys

TICKER_US = re.compile(r"^[A-Z]{1,6}(\.[A-Z]{1,2})?$")
TICKER_TW = re.compile(r"^\d{4,6}(\.TWO?)?$")
NUMERIC_CELL = re.compile(r"^-?\d[\d,]*(\.\d+)?$")
POSITION_ID = re.compile(r"[A-Za-z0-9.]{1,10}#\d{4}-\d{2}-\d{2}#\d+")
SYMBOL_HEADERS = {"symbol", "ticker", "code"}


def _mask(value: str) -> str:
    """First and last character kept, everything between starred out."""
    if len(value) <= 2:
        return value[:1] + "*"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def _mask_position_id(value: str) -> str:
    head = value.split("#", 1)[0]
    return f"{_mask(head)}#****-**-**#*"


def _load_reference(path: pathlib.Path) -> tuple[set[str], set[str]]:
    """Extract ticker and amount tokens from one reference CSV.

    Tickers come from a Symbol-like column when a header row names one, else
    from any cell matching a ticker shape. Amounts come from any numeric cell
    whose integer part has 4+ digits — long enough that a hit in prose almost
    certainly came from the ledger, short enough to catch deposits and costs.
    """
    tickers: set[str] = set()
    amounts: set[str] = set()
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return tickers, amounts
    header = [cell.strip().lower() for cell in rows[0]]
    symbol_columns = [index for index, name in enumerate(header) if name in SYMBOL_HEADERS]
    body = rows[1:] if symbol_columns else rows
    for row in body:
        cells = (
            [row[index] for index in symbol_columns if index < len(row)]
            if symbol_columns else row
        )
        for cell in cells:
            cell = cell.strip()
            if not cell:
                continue
            if TICKER_US.fullmatch(cell) or TICKER_TW.fullmatch(cell):
                tickers.add(cell.upper())
                if "." in cell:  # `2330.TW` is also leakable as bare `2330`
                    tickers.add(cell.split(".", 1)[0].upper())
        for cell in row:
            cell = cell.strip().lstrip("-")
            if not cell or not NUMERIC_CELL.fullmatch(cell):
                continue
            integer_part = cell.replace(",", "").split(".", 1)[0]
            if len(integer_part) >= 4:
                amounts.add(integer_part)
    return tickers, amounts


def _amount_pattern(amounts: set[str]) -> re.Pattern | None:
    """One regex matching every reference amount, plain or comma-grouped."""
    if not amounts:
        return None
    variants = []
    for digits in sorted(amounts, key=len, reverse=True):
        grouped = f"{int(digits):,}"
        variants.append(re.escape(digits))
        variants.append(re.escape(grouped))
    return re.compile(r"(?<![\d.,])(" + "|".join(variants) + r")(?:\.\d+)?(?![\d,])")


def _ticker_pattern(tickers: set[str]) -> re.Pattern | None:
    if not tickers:
        return None
    variants = sorted((re.escape(ticker) for ticker in tickers), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9.])(" + "|".join(variants) + r")(?![A-Za-z0-9])",
                      re.IGNORECASE)


def scan(text: str, tickers: set[str], amounts: set[str]) -> list[tuple[int, str, str]]:
    """Return (line_number, kind, masked_value) findings for the draft text."""
    findings: list[tuple[int, str, str]] = []
    ticker_re = _ticker_pattern(tickers)
    amount_re = _amount_pattern(amounts)
    for line_number, line in enumerate(text.splitlines(), 1):
        covered: list[tuple[int, int]] = []
        for match in POSITION_ID.finditer(line):
            findings.append((line_number, "position-id", _mask_position_id(match.group(0))))
            covered.append(match.span())
        def _inside_position_id(span: tuple[int, int]) -> bool:
            return any(start <= span[0] and span[1] <= end for start, end in covered)
        if ticker_re:
            for match in ticker_re.finditer(line):
                if not _inside_position_id(match.span()):
                    findings.append((line_number, "ticker", _mask(match.group(1).upper())))
        if amount_re:
            for match in amount_re.finditer(line):
                if not _inside_position_id(match.span()):
                    findings.append((line_number, "amount", _mask(match.group(1))))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("draft", help="text file to check, or '-' for stdin")
    parser.add_argument("--against", action="append", required=True, metavar="CSV",
                        help="reference trade CSV (repeatable); read locally, never echoed")
    args = parser.parse_args()

    tickers: set[str] = set()
    amounts: set[str] = set()
    for raw in args.against:
        path = pathlib.Path(raw).expanduser()
        if not path.is_file():
            print(f"ERROR: reference CSV not found: {path}", file=sys.stderr)
            return 2
        try:
            file_tickers, file_amounts = _load_reference(path)
        except OSError as exc:
            print(f"ERROR: cannot read reference CSV {path}: {exc}", file=sys.stderr)
            return 2
        tickers |= file_tickers
        amounts |= file_amounts
    if not tickers and not amounts:
        print("ERROR: reference CSVs yielded no tickers or amounts — wrong file? "
              "Refusing to pass an empty reference set.", file=sys.stderr)
        return 2

    if args.draft == "-":
        text = sys.stdin.read()
    else:
        draft_path = pathlib.Path(args.draft).expanduser()
        if not draft_path.is_file():
            print(f"ERROR: draft not found: {draft_path}", file=sys.stderr)
            return 2
        text = draft_path.read_text(encoding="utf-8", errors="replace")

    findings = scan(text, tickers, amounts)
    if findings:
        for line_number, kind, masked in findings:
            print(f"line {line_number}: {kind} \"{masked}\"")
        print(f"FAIL: {len(findings)} finding(s) matching the reference trade data. "
              "De-identify each before posting anywhere public (see #274).")
        return 1
    print(f"PASS: no reference tickers, amounts, or position ids found "
          f"({len(args.against)} reference file(s), {len(tickers)} tickers, "
          f"{len(amounts)} amounts checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
