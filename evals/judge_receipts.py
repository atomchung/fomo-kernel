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

try:
    import fcntl
except ImportError:  # pragma: no cover - supported maintainer hosts are POSIX
    fcntl = None


class ReceiptError(RuntimeError):
    """The judge receipt store could not be written or read faithfully."""


def canonical_sha256(value: Any) -> str:
    """Return a stable SHA-256 over a JSON value's canonical UTF-8 encoding."""
    try:
        encoded = json.dumps(
            value,
            # ASCII escaping is reversible JSON and also makes untrusted model
            # text containing an isolated surrogate safely content-addressable.
            ensure_ascii=True,
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


def _validate_locked_history(fd: int, path: pathlib.Path) -> int:
    """Validate every existing row under an exclusive lock; return its end."""
    end = os.lseek(fd, 0, os.SEEK_END)
    if not end:
        return 0
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    remaining = end
    while remaining:
        chunk = os.read(fd, min(remaining, 1024 * 1024))
        if not chunk:
            raise ReceiptError(
                f"judge receipt history at {path} became unreadable during validation")
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if not raw.endswith(b"\n"):
        raise ReceiptError(
            f"judge receipt history at {path} ends with an incomplete row; "
            "refusing to append")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ReceiptError(
            f"judge receipt history at {path} is not valid UTF-8: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReceiptError(
                f"malformed judge receipt at {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ReceiptError(
                f"malformed judge receipt at {path}:{line_number}: expected JSON object")
    os.lseek(fd, end, os.SEEK_SET)
    return end


def preflight_append() -> pathlib.Path:
    """Reject a receipt store already known to be unsafe before model calls."""
    path = history_path()
    fd = None
    try:
        created = _missing_directory_chain(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        for directory in created:
            _fsync_dir(directory.parent)
        if fcntl is None:
            raise ReceiptError(
                "judge receipt locking is unavailable on this platform; refusing model calls")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        _fsync_dir(path.parent)
        _validate_locked_history(fd, path)
    except OSError as exc:
        raise ReceiptError(
            f"judge receipt store is not appendable at {path}: {exc}") from exc
    finally:
        if fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    return path


def append_receipt(row: dict[str, Any]) -> pathlib.Path:
    """Append and sync one receipt, raising when durability is not proven."""
    if not isinstance(row, dict):
        raise ReceiptError("judge receipt must be a JSON object")
    try:
        line = json.dumps(
            row,
            # Judge text is untrusted. Python's JSON parser accepts isolated
            # surrogate escapes; spelling every non-ASCII code point as JSON
            # escapes prevents a later UTF-8 encode from losing the whole run.
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise ReceiptError(f"judge receipt is not valid JSON: {exc}") from exc

    path = history_path()
    fd = None
    try:
        created = _missing_directory_chain(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        for directory in created:
            _fsync_dir(directory.parent)
        if fcntl is None:
            raise ReceiptError(
                "judge receipt locking is unavailable on this platform; refusing an unsafe append")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)

        # Persist the file's directory entry before exposing any receipt row.
        # A directory-fsync failure after the write left a readable PASS row
        # behind even though append_receipt correctly reported non-evidence.
        # Doing this first makes that failure leave only an empty file.
        _fsync_dir(path.parent)

        # Never append beside a torn or malformed prior row and then claim the
        # new result is readable history. Preflight performs this same check
        # before calls; repeat it under this append's lock to close the race.
        start = _validate_locked_history(fd, path)
        payload = line.encode("utf-8")
        written = 0
        try:
            while written < len(payload):
                count = os.write(fd, payload[written:])
                if count <= 0:
                    raise OSError("receipt append made no progress")
                written += count
            os.fsync(fd)
        except OSError:
            # Do not leave a fully readable but unproven PASS behind after a
            # failed write/fsync. Under the exclusive lock, restore the exact
            # pre-append boundary before reporting the failure.
            try:
                os.ftruncate(fd, start)
                os.fsync(fd)
            except OSError as rollback_exc:
                raise ReceiptError(
                    f"judge receipt append failed and rollback at {path} also failed: "
                    f"{rollback_exc}") from rollback_exc
            raise
    except OSError as exc:
        raise ReceiptError(f"judge receipt was not durably recorded at {path}: {exc}") from exc
    finally:
        if fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    return path


def read_history() -> list[dict[str, Any]]:
    """Read every receipt; malformed or non-object rows are visible failures."""
    path = history_path()
    if not path.exists():
        return []
    rows = []
    try:
        with path.open(encoding="utf-8") as handle:
            if fcntl is None:
                raise ReceiptError(
                    "judge receipt locking is unavailable on this platform; refusing an unsafe read")
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
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
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, UnicodeError) as exc:
        raise ReceiptError(f"judge receipt history is unreadable at {path}: {exc}") from exc
    return rows
