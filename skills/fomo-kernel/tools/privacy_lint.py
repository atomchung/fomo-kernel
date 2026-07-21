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
               (word-bounded, case-insensitive; suffixed symbols such as
               `2330.TW`, `0700.HK`, or `BRK-B` also match their bare stem)
- amount:      any reference numeric value with an integer part of 4+ digits,
               in plain or thousands-separated form (`13500`, `13,500.00`).
               Cell values AND per-row quantity x price products both count,
               because broker exports often store only quantity and price
               while the leakable "total amount" is their product.
- date:        any trade date present in the reference CSVs, in ISO
               (`2023-01-10`) or slash (`1/10/2023`, `01/10/2023`) form
- position-id: the internal `TICKER#YYYY-MM-DD#n` position identifier format,
               reported regardless of the reference set (the format itself
               implies a real ledger row)

Sub-4-digit numbers (e.g. a bare share price like `550`) are intentionally
not flagged — they would flood prose with false positives. De-identify those
by hand; the runbook documents this limit.

Behavior is fail-closed: an unreadable reference CSV, or a reference set that
yields no tokens at all, is an error (exit 2) — never a silent pass. Findings
are printed MASKED so the lint output itself is safe to share. Exit codes:
0 clean, 1 findings, 2 usage/reference error.

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

# At least one letter (NVDA, BRK-B, F, 0700.HK's HK part comes via suffix)...
TICKER_ALPHA = re.compile(r"^(?=.*[A-Z])[A-Z0-9]{1,7}([.-][A-Z0-9]{1,4})?$")
# ...or a 4-6 digit numeric code with optional exchange suffix (2330, 2330.TW, 0700.HK).
TICKER_NUMERIC = re.compile(r"^\d{4,6}([.-][A-Z]{1,4})?$")
NUMERIC_CELL = re.compile(r"^-?\d[\d,]*(\.\d+)?$")
DATE_CELL = re.compile(r"^(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})/(\d{1,2})/(\d{4}))$")
DRAFT_DATE = re.compile(r"(?<!\d)(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})/(\d{1,2})/(\d{4}))(?!\d)")
POSITION_ID = re.compile(r"[A-Za-z0-9.\-]{1,12}#\d{4}-\d{2}-\d{2}#\d+")
SYMBOL_HEADERS = {"symbol", "ticker", "code"}
QUANTITY_HEADERS = {"quantity", "qty", "shares"}
PRICE_HEADERS = {"price", "unit price", "unit_price"}


def _mask(value: str) -> str:
    """First and last character kept, everything between starred out.

    One- and two-character values are fully starred: echoing `F*` for the
    ticker `F` would leak the whole secret.
    """
    if len(value) <= 2:
        return "**"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def _mask_position_id(value: str) -> str:
    head = value.split("#", 1)[0]
    return f"{_mask(head)}#****-**-**#*"


def _date_key(match: re.Match) -> tuple[int, int, int]:
    groups = match.groups()
    if groups[0] is not None:  # ISO YYYY-MM-DD
        return int(groups[0]), int(groups[1]), int(groups[2])
    return int(groups[5]), int(groups[3]), int(groups[4])  # M/D/YYYY


def _big_digits(raw: str) -> str | None:
    """Integer-part digits of a numeric string when 4+ digits long, else None."""
    integer_part = raw.replace(",", "").split(".", 1)[0].lstrip("-")
    return integer_part if len(integer_part) >= 4 and integer_part.isdigit() else None


def _load_reference(path: pathlib.Path) -> tuple[set[str], set[str], set[tuple[int, int, int]]]:
    """Extract ticker, amount, and trade-date tokens from one reference CSV.

    Tickers come from a Symbol-like column when a header row names one, else
    from any cell matching a ticker shape. Amounts come from any numeric cell
    whose integer part has 4+ digits, plus each row's |quantity x price|
    product (broker exports often omit a total-amount column, yet the product
    is exactly the figure a draft would leak). Dates come from date-shaped
    cells in either ISO or slash form.
    """
    tickers: set[str] = set()
    amounts: set[str] = set()
    dates: set[tuple[int, int, int]] = set()
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return tickers, amounts, dates
    header = [cell.strip().lower() for cell in rows[0]]
    symbol_columns = [index for index, name in enumerate(header) if name in SYMBOL_HEADERS]
    quantity_columns = [index for index, name in enumerate(header) if name in QUANTITY_HEADERS]
    price_columns = [index for index, name in enumerate(header) if name in PRICE_HEADERS]
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
            if TICKER_ALPHA.fullmatch(cell) or TICKER_NUMERIC.fullmatch(cell):
                tickers.add(cell.upper())
                stem = re.split(r"[.-]", cell, maxsplit=1)[0]
                if stem != cell:  # `2330.TW` / `BRK-B` are also leakable bare
                    tickers.add(stem.upper())
        for cell in row:
            cell = cell.strip()
            if not cell:
                continue
            date_match = DATE_CELL.fullmatch(cell)
            if date_match:
                dates.add(_date_key(date_match))
                continue
            stripped = cell.lstrip("-")
            if NUMERIC_CELL.fullmatch(stripped):
                digits = _big_digits(stripped)
                if digits:
                    amounts.add(digits)
        for q_index in quantity_columns:
            for p_index in price_columns:
                if q_index >= len(row) or p_index >= len(row):
                    continue
                try:
                    product = abs(float(row[q_index].replace(",", "")) *
                                  float(row[p_index].replace(",", "")))
                except ValueError:
                    continue
                digits = _big_digits(f"{product:.2f}")
                if digits:
                    amounts.add(digits)
    return tickers, amounts, dates


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
    # No `.` in the lookbehind: `Issuer.AAPL` must still flag AAPL. Suffixed
    # forms sort longer, so `2330.TW` consumes before bare `2330` can match.
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(variants) + r")(?![A-Za-z0-9])",
                      re.IGNORECASE)


def scan(text: str, tickers: set[str], amounts: set[str],
         dates: set[tuple[int, int, int]]) -> list[tuple[int, str, str]]:
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
        for match in DRAFT_DATE.finditer(line):
            if _date_key(match) in dates and not _inside_position_id(match.span()):
                findings.append((line_number, "date", "<reference trade date>"))
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
    dates: set[tuple[int, int, int]] = set()
    for raw in args.against:
        path = pathlib.Path(raw).expanduser()
        if not path.is_file():
            print(f"ERROR: reference CSV not found: {path}", file=sys.stderr)
            return 2
        try:
            file_tickers, file_amounts, file_dates = _load_reference(path)
        except OSError as exc:
            print(f"ERROR: cannot read reference CSV {path}: {exc}", file=sys.stderr)
            return 2
        tickers |= file_tickers
        amounts |= file_amounts
        dates |= file_dates
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

    findings = scan(text, tickers, amounts, dates)
    if findings:
        for line_number, kind, masked in findings:
            print(f"line {line_number}: {kind} \"{masked}\"")
        print(f"FAIL: {len(findings)} finding(s) matching the reference trade data. "
              "De-identify each before posting anywhere public (see #274).")
        return 1
    print(f"PASS: no reference tickers, amounts, dates, or position ids found "
          f"({len(args.against)} reference file(s), {len(tickers)} tickers, "
          f"{len(amounts)} amounts, {len(dates)} dates checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
