#!/usr/bin/env python3
"""slice_csv.py — cut a normalized trade CSV at a TradeDate cutoff.

Usage:
  python3 slice_csv.py <src.csv> <until YYYY-MM-DD> <dst.csv>

Keeps the header and every row whose TradeDate parses (ISO YYYY-MM-DD, or
US MM/DD/YYYY or MM/DD/YY) and falls on or before the cutoff date. Rows
whose TradeDate does not parse in any of those formats are dropped: the
engine's own loaders (fomo-kernel's engine/ledger.py, engine/trade_recap.py)
only accept dt.date.fromisoformat and would skip such a row anyway, so a
slice never fabricates a placement the engine could not itself have made.

Why this exists: fomo-kernel's `_cadence()` (engine/review.py) classifies a
review's tier from the calendar-day span between the previous committed
review's TradeDate high-water mark and the current review's — not from
wall-clock "now". Re-running `review.py prepare` against the *same* CSV
twice in one sitting always reads as span=0 (a light-tier capture with
nothing new to capture), which cannot exercise weekly_review's due-revisit
or rule-breach reconciliation.

Feeding a sequence of slices instead — each cutoff more than
CADENCE_LIGHT_MAX_DAYS (5, see engine/review.py) later than the previous
committed review's cutoff — lets one QA session replay several full-tier
review cycles (first_review -> weekly_review -> weekly_review -> ...) in a
single sitting, using only real historical data staged chronologically. No
system date is touched and no data is altered — only which rows are
present in a given slice changes.

Typical staged-replay sequence against a real or mock trade history:
  python3 slice_csv.py trades.csv 2024-01-31 /tmp/slice-1.csv   # round 1: first_review
  # ... prepare/preview/finalize slice-1 ...
  python3 slice_csv.py trades.csv 2024-02-10 /tmp/slice-2.csv   # round 2: >5 days later
  # ... prepare/preview/finalize slice-2 ...
  python3 slice_csv.py trades.csv 2024-02-20 /tmp/slice-3.csv   # round 3: >5 days later
  # ... and so on
"""
import csv
import datetime as dt
import sys

_FORMATS = ("%m/%d/%Y", "%m/%d/%y")


def _parse_trade_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        pass
    for fmt in _FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def slice_csv(src_path, until, dst_path):
    cutoff = dt.date.fromisoformat(until)
    with open(src_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames or "TradeDate" not in fieldnames:
            raise SystemExit(f"ERROR: {src_path} has no TradeDate column")
        kept, dropped_later, dropped_unparseable = [], 0, 0
        for row in reader:
            parsed = _parse_trade_date(row.get("TradeDate"))
            if parsed is None:
                dropped_unparseable += 1
                continue
            if parsed <= cutoff:
                kept.append(row)
            else:
                dropped_later += 1
    with open(dst_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    print(
        f"wrote {dst_path}: kept {len(kept)} row(s) with TradeDate <= {until} "
        f"(dropped {dropped_later} later, {dropped_unparseable} unparseable)"
    )


def main(argv):
    if len(argv) != 4:
        sys.stderr.write(__doc__)
        return 2
    _, src_path, until, dst_path = argv
    try:
        slice_csv(src_path, until, dst_path)
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
