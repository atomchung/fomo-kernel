#!/usr/bin/env python3
"""Durable local receipts for opt-in TradeEvaluation judge runs.

Judge output may quote an answer verbatim, so receipts stay under the protected
coach root rather than in this public repository.  A successful model verdict
is not evidence unless its JSONL row and the directory entries leading to it
have been synced to disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any


class ReceiptError(RuntimeError):
    """The judge receipt store could not be written or read faithfully."""


def canonical_sha256(value: Any) -> str:
    """Return a stable SHA-256 over a JSON value's canonical UTF-8 encoding."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReceiptError(f"value is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def state_root() -> pathlib.Path:
    """Mirror engine/session.default_root without importing the runtime."""
    return pathlib.Path(os.path.expanduser(
        os.environ.get("TRADE_COACH_HOME", "~/.trade-coach")))


def history_path() -> pathlib.Path:
    return state_root() / "judge" / "trade-answer-runs.jsonl"


def _fsync_dir(path: pathlib.Path) -> None:
    """Persist a directory entry; unsupported filesystems fail closed."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _missing_directory_chain(path: pathlib.Path) -> list[pathlib.Path]:
    """Return missing ancestors outermost first, stopping at an existing one."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return list(reversed(missing))


def append_receipt(row: dict[str, Any]) -> pathlib.Path:
    """Append and sync one receipt, raising when durability is not proven."""
    if not isinstance(row, dict):
        raise ReceiptError("judge receipt must be a JSON object")
    try:
        line = json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise ReceiptError(f"judge receipt is not valid JSON: {exc}") from exc

    path = history_path()
    try:
        created = _missing_directory_chain(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        for directory in created:
            _fsync_dir(directory.parent)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        # Needed when this append created the file; harmless and explicit on
        # later appends, where it keeps one durability contract for all calls.
        _fsync_dir(path.parent)
    except OSError as exc:
        raise ReceiptError(f"judge receipt was not durably recorded at {path}: {exc}") from exc
    return path


def read_history() -> list[dict[str, Any]]:
    """Read every receipt; malformed or non-object rows are visible failures."""
    path = history_path()
    if not path.exists():
        return []
    rows = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ReceiptError(
                        f"malformed judge receipt at {path}:{line_number}: {exc.msg}") from exc
                if not isinstance(row, dict):
                    raise ReceiptError(
                        f"malformed judge receipt at {path}:{line_number}: expected JSON object")
                rows.append(row)
    except (OSError, UnicodeError) as exc:
        raise ReceiptError(f"judge receipt history is unreadable at {path}: {exc}") from exc
    return rows
