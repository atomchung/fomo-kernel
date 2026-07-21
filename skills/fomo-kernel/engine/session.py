#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical review-session storage and recoverable legacy projections.

The committed session directory is the source of truth.  It is assembled in a
staging directory and renamed into place in one filesystem operation.  Existing
JSONL files remain supported as projections so older tooling keeps working; a
projection failure never corrupts or invalidates the committed session.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile

try:  # Windows has no fcntl; fail at the durable-finalize boundary, not import.
    import fcntl
except ImportError:
    fcntl = None

import ledger
import problems


class SessionError(ValueError):
    pass


PKEY = {
    "max_pos_pct": "oversize",
    "avgdown_count": "avgdown_breach",
    "ai_pct": "concentration",
    "max_sector_pct": "concentration",
    "top3_pct": "concentration",
    "exit_severity": "sell_winner_early",
    "hold_severity": "hold_inconsistency",
}

_REQUIRED_CANONICAL_ARTIFACTS = frozenset({
    "bundle.json", "state.json", "plan.json", "answers.json", "narrative.json",
    "card-private.md", "card-public.md",
})
_LEGACY_CANONICAL_ARTIFACTS = _REQUIRED_CANONICAL_ARTIFACTS | {"card-private.html"}


def default_root():
    return os.path.expanduser(os.environ.get("TRADE_COACH_HOME", "~/.trade-coach"))


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _safe_id(session_id):
    if not session_id or session_id != os.path.basename(session_id) or session_id in {".", ".."}:
        raise SessionError("invalid session_id")
    return session_id


def pending_dir(root, session_id):
    return os.path.join(root, ".pending", _safe_id(session_id))


def session_dir(root, session_id):
    return os.path.join(root, "sessions", _safe_id(session_id))


def save_pending(root, session_id, **artifacts):
    """Atomically update named pending artifacts; returns their stable paths."""
    base = pending_dir(root, session_id)
    os.makedirs(base, exist_ok=True)
    paths = {}
    for name, value in artifacts.items():
        if value is None:
            continue
        if os.path.splitext(name)[1] in {".json", ".md", ".html"}:
            filename = name  # caller-pinned extension (e.g. card-private-preview.html)
        else:
            filename = name + (".json" if isinstance(value, (dict, list)) else ".md")
        path = os.path.join(base, filename)
        text = pretty(value) if isinstance(value, (dict, list)) else str(value)
        if text and not text.endswith("\n"):
            text += "\n"
        ledger.atomic_write_text(path, text)
        paths[name] = path
    return paths


def load_pending(root, session_id):
    base = pending_dir(root, session_id)
    if not os.path.isdir(base):
        raise SessionError(f"pending session not found: {session_id}")
    out = {"session_id": session_id, "path": base}
    for name in ("plan", "answers", "narrative", "question-surfaces", "question-presentations"):
        path = os.path.join(base, name + ".json")
        if os.path.exists(path):
            out[name.replace("-", "_")] = read_json(path)
    for key, filename in (("card-private-preview", "card-private-preview.md"),
                          ("card-public-preview", "card-public-preview.md")):
        path = os.path.join(base, filename)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out[key] = f.read()
    # The styled preview surfaces as a path, not content: resume must expose
    # the same `private_card_html_path` key the preview emit documents (see
    # references/card-delivery.md) without dumping the HTML blob into stdout.
    html_path = os.path.join(base, "card-private-preview.html")
    if os.path.exists(html_path):
        out["private_card_html_path"] = html_path
    return out


def _artifact_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_durable_platform():
    """Fail at a controlled boundary when POSIX durability is unavailable."""
    if fcntl is None or os.name == "nt":
        raise SessionError(
            "durable session finalization is unsupported on this platform "
            "(requires POSIX flock and directory fsync)"
        )


def _fsync_file(path):
    """Flush one staged canonical artifact before its directory is published."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path):
    """Flush directory entries needed by the canonical directory rename."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _prepare_session_storage(root, session_id):
    """Create the canonical parent and persist every newly relied-on entry."""
    _require_durable_platform()
    root = os.path.abspath(os.fspath(root))
    sessions = os.path.join(root, "sessions")
    try:
        os.makedirs(sessions, exist_ok=True)
        # prepare/save_pending may have created root before finalize.  Persist
        # its name unconditionally; existence alone does not prove durability.
        _fsync_dir(os.path.dirname(root) or os.curdir)
        # Persist creation of sessions/ itself before relying on it as the
        # parent of the canonical staging->final rename.
        _fsync_dir(root)
    except OSError as exc:
        raise SessionError(f"cannot prepare session storage for {session_id}: {exc}") from exc
    return root, sessions


@contextlib.contextmanager
def _file_lock(path, label, busy_message=None):
    """Hold a persistent flock; ``busy_message`` selects nonblocking mode."""
    _require_durable_platform()
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise SessionError(f"cannot open lock for {label}: {exc}") from exc
    try:
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if busy_message else 0)
        fcntl.flock(fd, operation)
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        if busy_message and exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise SessionError(busy_message) from exc
        raise SessionError(f"cannot lock {label}: {exc}") from exc
    try:
        yield
    finally:
        os.close(fd)  # closing releases flock even when bundle assembly fails


@contextlib.contextmanager
def _session_lock(sessions, session_id, fail_if_busy=False):
    """Serialize one session's canonical commit and projection.

    The lock file intentionally stays in ``sessions/``.  Unlinking a lock file
    while another process is waiting on its inode can create two independent
    locks for the same session.  Hidden entries are ignored by discovery and
    projection repair.
    """
    busy = f"finalize already in progress for session {session_id}" if fail_if_busy else None
    path = os.path.join(sessions, f".{session_id}.finalize.lock")
    with _file_lock(path, f"session {session_id}", busy_message=busy):
        yield


@contextlib.contextmanager
def _projection_lock(sessions):
    """Serialize shared legacy books across all session IDs in one root."""
    path = os.path.join(sessions, ".projections.lock")
    with _file_lock(path, "legacy projections"):
        yield


@contextlib.contextmanager
def projection_transaction(root):
    """Serialize a root-wide ledger read/derive/write transaction.

    Snapshot finalize uses the same persistent lock for its initial-history
    boundary, canonical commit, and ledger projection.  Trade preparation must
    therefore take this transaction around the complete existing-ledger check
    and append; sharing only the append would leave a check-then-write race.
    """
    root, sessions = _prepare_session_storage(root, "ledger-transaction")
    with _projection_lock(sessions):
        yield root


def _existing_commit(final, bundle, session_id):
    """Return the identical existing commit, or fail closed on a conflict."""
    if not os.path.isdir(final):
        return None
    try:
        existing = read_json(os.path.join(final, "bundle.json"))
    except (OSError, ValueError) as exc:
        raise SessionError(f"session {session_id} has an unreadable canonical bundle: {exc}") from exc
    if canonical(existing) != canonical(bundle):
        raise SessionError(f"session {session_id} already committed with different content")
    return {"status": "no-op", "path": final, "session_id": session_id}


def _cleanup_committed_staging(sessions, final, session_id):
    """Remove same-session staging only after an immutable final exists.

    There is deliberately no age/TTL guess.  The caller holds the per-session
    session lock, so current writers cannot be inside assembly, and a canonical
    final proves that every older same-session staging directory has lost the
    only rename destination it could validly claim.
    """
    if not os.path.isdir(final):
        return 0
    prefix = f".{session_id}.staging-"
    removed = 0
    for entry in os.scandir(sessions):
        if not entry.name.startswith(prefix) or not entry.is_dir(follow_symlinks=False):
            continue
        shutil.rmtree(entry.path)
        removed += 1
    return removed


def _cleanup_committed_staging_best_effort(sessions, final, session_id):
    """Collect provably orphaned staging without changing commit success."""
    try:
        removed = _cleanup_committed_staging(sessions, final, session_id)
        if removed:
            # Cleanup durability is useful but not authoritative.  Once the
            # canonical rename's parent sync succeeded, a GC failure must not
            # turn a committed bundle into a false failure or block its retry.
            _fsync_dir(sessions)
    except (OSError, SessionError):
        pass


def _existing_canonical_artifacts(final, session_id):
    """Return safe direct-child artifacts, with manifest kept last."""
    manifest_path = os.path.join(final, "manifest.json")
    manifest_present = os.path.lexists(manifest_path)
    if manifest_present:
        try:
            mode = os.stat(manifest_path, follow_symlinks=False).st_mode
        except OSError as exc:
            raise SessionError(
                f"session {session_id} canonical manifest is unreadable: {exc}"
            ) from exc
        if not stat.S_ISREG(mode):
            raise SessionError(f"session {session_id} canonical manifest is not a regular file")
        try:
            manifest = read_json(manifest_path)
        except (OSError, ValueError) as exc:
            raise SessionError(
                f"session {session_id} has an unreadable canonical manifest: {exc}"
            ) from exc
        if not isinstance(manifest, dict):
            raise SessionError(f"session {session_id} has an invalid canonical manifest")
        if manifest.get("schema_version") != 1:
            raise SessionError(
                f"session {session_id} has an unsupported canonical manifest schema"
            )
        expected_hashes = manifest.get("sha256")
        if not isinstance(expected_hashes, dict):
            raise SessionError(f"session {session_id} has an invalid canonical manifest")
        names = set()
        for name, expected in expected_hashes.items():
            if (not isinstance(name, str) or not name or name != os.path.basename(name)
                    or name in {".", ".."}):
                raise SessionError(
                    f"session {session_id} manifest contains an unsafe artifact name: {name!r}"
                )
            if name == "manifest.json":
                raise SessionError(f"session {session_id} manifest cannot include itself")
            if not isinstance(expected, str):
                raise SessionError(
                    f"session {session_id} manifest has an invalid hash for {name}"
                )
            names.add(name)
    else:
        # Compatibility for bundles predating manifest support.  Their content
        # is unverifiable legacy state, so only the fixed canonical regular-file
        # set is eligible for durability adoption.
        expected_hashes = None
        names = set()
        try:
            for entry in os.scandir(final):
                if entry.name not in _LEGACY_CANONICAL_ARTIFACTS:
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise SessionError(
                        f"session {session_id} canonical artifact is not a regular file: {entry.name}"
                    )
                names.add(entry.name)
        except OSError as exc:
            raise SessionError(
                f"cannot inspect existing session {session_id}: {exc}"
            ) from exc

    missing = _REQUIRED_CANONICAL_ARTIFACTS - names
    if missing:
        raise SessionError(
            f"session {session_id} canonical artifacts are incomplete: {', '.join(sorted(missing))}"
        )

    paths = []
    for name in sorted(names):
        path = os.path.join(final, name)
        try:
            mode = os.stat(path, follow_symlinks=False).st_mode
        except OSError as exc:
            raise SessionError(
                f"session {session_id} canonical artifact is unreadable: {name}: {exc}"
            ) from exc
        if not stat.S_ISREG(mode):
            raise SessionError(
                f"session {session_id} canonical artifact is not a regular file: {name}"
            )
        if expected_hashes is not None:
            expected = expected_hashes.get(name)
            digest = hashlib.sha256()
            try:
                with open(path, "rb") as artifact:
                    for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise SessionError(
                    f"session {session_id} canonical artifact is unreadable: {name}: {exc}"
                ) from exc
            if digest.hexdigest() != expected:
                raise SessionError(
                    f"session {session_id} canonical artifact hash mismatch: {name}"
                )
        paths.append(path)

    return paths, manifest_path if manifest_present else None


def _sync_existing_canonical(final, sessions, session_id):
    """Adopt a visible old-writer commit only after making all levels durable."""
    artifact_paths, manifest_path = _existing_canonical_artifacts(final, session_id)
    try:
        for path in artifact_paths:
            _fsync_file(path)
        if manifest_path is not None:
            _fsync_file(manifest_path)
        _fsync_dir(final)
        _fsync_dir(sessions)
    except OSError as exc:
        raise SessionError(
            f"cannot make existing session {session_id} durable: {exc}"
        ) from exc


def _sync_new_canonical_parent(sessions, session_id):
    """Persist a rename whose staged files and directory were already synced."""
    try:
        _fsync_dir(sessions)
    except OSError as exc:
        raise SessionError(
            f"session {session_id} committed but directory sync failed: {exc}"
        ) from exc


def _commit_bundle_locked(root, sessions, bundle, private_md, public_md, private_html=None):
    """Commit while the caller holds this session's finalize lock."""
    session_id = _safe_id(bundle.get("session_id"))
    final = os.path.join(sessions, session_id)
    existing = _existing_commit(final, bundle, session_id)
    if existing is not None:
        # Existing directories may come from the pre-durability writer.  Sync
        # artifacts -> final dir -> parent before accepting an identical no-op.
        _sync_existing_canonical(final, sessions, session_id)
        _cleanup_committed_staging_best_effort(sessions, final, session_id)
        return existing

    staging = None
    race_existing = None
    try:
        staging = tempfile.mkdtemp(prefix=f".{session_id}.staging-", dir=sessions)
        artifacts = {
            "bundle.json": pretty(bundle),
            "state.json": pretty(bundle.get("engine_state") or {}),
            "plan.json": pretty(bundle.get("review_plan") or {}),
            "answers.json": pretty(bundle.get("answers") or {}),
            "narrative.json": pretty(bundle.get("narrative") or {}),
            "card-private.md": private_md if private_md.endswith("\n") else private_md + "\n",
            "card-public.md": public_md if public_md.endswith("\n") else public_md + "\n",
        }
        if bundle.get("question_surfaces") is not None:
            artifacts["question-surfaces.json"] = pretty(bundle["question_surfaces"])
        if bundle.get("question_presentations") is not None:
            artifacts["question-presentations.json"] = pretty(bundle["question_presentations"])
        if private_html is not None:
            artifacts["card-private.html"] = private_html if private_html.endswith("\n") else private_html + "\n"
        manifest = {name: _artifact_hash(text) for name, text in artifacts.items()}
        artifacts["manifest.json"] = pretty({"schema_version": 1, "sha256": manifest})
        for name, text in artifacts.items():
            path = os.path.join(staging, name)
            ledger.atomic_write_text(path, text)
            _fsync_file(path)
        # Persist every artifact name inside the directory before exposing
        # that directory at its canonical final path.
        _fsync_dir(staging)
        os.replace(staging, final)
        staging = None
    except OSError as exc:
        # A writer from an older process may not honor the new lock.  If it won
        # the rename race, collapse the loser to the same stable contract.
        race_existing = _existing_commit(final, bundle, session_id)
        if race_existing is None:
            raise SessionError(f"cannot commit session {session_id}: {exc}") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    if race_existing is not None:
        _sync_existing_canonical(final, sessions, session_id)
        _cleanup_committed_staging_best_effort(sessions, final, session_id)
        return race_existing

    # This sync, and only this sync, decides whether the canonical rename is
    # reported as durable.  Staging GC below is deliberately best effort.
    _sync_new_canonical_parent(sessions, session_id)
    _cleanup_committed_staging_best_effort(sessions, final, session_id)
    shutil.rmtree(pending_dir(root, session_id), ignore_errors=True)
    return {"status": "committed", "path": final, "session_id": session_id}


def commit_bundle(root, bundle, private_md, public_md, private_html=None):
    """Durably commit an immutable canonical bundle via directory rename."""
    session_id = _safe_id(bundle.get("session_id"))
    root, sessions = _prepare_session_storage(root, session_id)
    with _session_lock(sessions, session_id):
        # Canonical session discovery is part of the snapshot initial-history
        # boundary, so even callers that only commit a bundle share the root
        # lock with snapshot finalize.
        with _projection_lock(sessions):
            if bundle.get("route") == "snapshot_review":
                _assert_initial_snapshot_boundary(root, bundle)
                prepared = _snapshot_bundle_for_commit(root, bundle)
                return _commit_bundle_locked(
                    root, sessions, prepared, private_md, public_md, private_html
                )
            return _commit_bundle_locked(
                root, sessions, bundle, private_md, public_md, private_html
            )


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _is_demo_bundle(bundle):
    """True when a canonical bundle must never reach real coach memory.

    Finalized test drives still commit canonical bundles (they only skip
    projection), so every ``sessions/`` consumer shares this one filter:
    ``route == "test_drive"`` or an explicit ``review_plan.persist: false``.
    """
    plan = bundle.get("review_plan") or {}
    return bundle.get("route") == "test_drive" or plan.get("persist") is False


def iter_canonical_bundles(root, *, skip_test_drive=True, sort_by_date=False):
    """Yield ``(session_id, bundle)`` for each readable canonical bundle.

    The single shared ``sessions/`` walk: hidden entries (locks, staging
    directories) are ignored, unreadable or non-object bundles are skipped, and
    demo bundles are filtered out unless ``skip_test_drive`` is False.  Under
    the default filter a bundle whose ``review_plan`` is not an object cannot
    prove it is persistent, so it is skipped as well.  ``sort_by_date`` yields
    in ``(engine_state.date_end, session_id)`` order — the override order every
    continuity consumer shares; the default is directory-name order.
    """
    sessions = os.path.join(root, "sessions")
    if not os.path.isdir(sessions):
        return
    dated = []
    for session_id in sorted(os.listdir(sessions)):
        if session_id.startswith("."):
            continue
        try:
            bundle = read_json(os.path.join(sessions, session_id, "bundle.json"))
        except (OSError, ValueError):
            continue
        if not isinstance(bundle, dict):
            continue
        if skip_test_drive:
            plan = bundle.get("review_plan") or {}
            if not isinstance(plan, dict) or _is_demo_bundle(bundle):
                continue
        if not sort_by_date:
            yield session_id, bundle
            continue
        state = bundle.get("engine_state")
        state = state if isinstance(state, dict) else {}
        dated.append(((str(state.get("date_end") or ""), session_id), bundle))
    for (_date, session_id), bundle in sorted(dated, key=lambda item: item[0]):
        yield session_id, bundle


def _append_session_rows(path, session_id, new_rows):
    """Idempotent per-session append; conflicting retries fail closed.

    Writes only the delta in append mode — never rewrites the file — so rows and
    partial lines produced by other writers (coach.py appends concurrently) are
    never reformatted or dropped."""
    if not new_rows:
        return {"path": path, "appended": 0, "status": "empty"}
    existing = _read_jsonl(path)
    same = [row for row in existing if row.get("session_id") == session_id]
    old_set = {canonical(row) for row in same}
    new_set = {canonical(row) for row in new_rows}
    if same and old_set == new_set:
        return {"path": path, "appended": 0, "status": "no-op"}
    if same and not old_set.issubset(new_set):
        raise SessionError(f"legacy projection conflict: {path} / {session_id}")
    delta = [row for row in new_rows if canonical(row) not in old_set]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in delta))
    return {"path": path, "appended": len(delta), "status": "projected"}


def _project_card(root, bundle, private_md):
    date = (bundle.get("engine_state") or {}).get("date_end") or "undated"
    suffix = bundle["session_id"].split("__")[-1]
    path = os.path.join(root, "cards", f"{date}--{suffix}.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() != private_md:
                raise SessionError(f"legacy card conflict: {path}")
        return {"path": path, "status": "no-op"}
    ledger.atomic_write_text(path, private_md)
    return {"path": path, "status": "projected"}


def _snapshot_payload(event):
    """Return the stable snapshot identity payload.

    Completeness is part of the accounting fact: an explicitly partial
    declaration must never collide with a complete one.  The old envelope did
    not persist the field, so absence normalizes to ``True``.  Projection order
    is deliberately excluded; a retry with the same fact and sequence is still
    the same snapshot.
    """
    payload = {key: event[key] for key in ("type", "as_of", "source", "positions", "cash")
               if key in event}
    payload["is_complete"] = event.get("is_complete", True)
    return payload


def _positive_projection_sequence(value):
    """Return a valid root-wide projection sequence, else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _max_projection_sequence(root):
    """Find every durable sequence reservation in one coach root.

    Canonical bundles reserve a number before their ledger projection runs, so
    a projection failure cannot let a later finalize reuse that number.  The
    ledger scan bootstraps roots that gained sequence-aware sessions before
    their canonical directory was copied here.
    """
    highest = 0
    for row in _read_jsonl(os.path.join(root, "ledger.jsonl")):
        sequence = _positive_projection_sequence(row.get("projection_sequence"))
        if sequence is not None:
            highest = max(highest, sequence)
    # Deliberately unfiltered (skip_test_drive=False): a sequence reservation is
    # a durability fact, not coach memory.  The snapshot route rejects
    # --test-drive so demo bundles never carry a sequence today, but if one ever
    # did, reusing its number would still be wrong.
    for _session_id, stored in iter_canonical_bundles(root, skip_test_drive=False):
        sequence = _positive_projection_sequence(
            (stored.get("engine_state") or {}).get("projection_sequence")
        )
        if sequence is not None:
            highest = max(highest, sequence)
    return highest


def _snapshot_bundle_for_commit(root, bundle):
    """Attach or reuse a sequence while the caller holds the projection lock."""
    if bundle.get("route") != "snapshot_review":
        return bundle
    anchor = ((bundle.get("engine_state") or {}).get("snapshot_anchor"))
    if not isinstance(anchor, dict) or anchor.get("type") != "snapshot":
        return bundle  # compatibility for pre-adapter snapshot bundles
    if anchor.get("is_complete", True) is False:
        # Partial declarations are review evidence, never accounting anchors,
        # so they do not reserve a ledger ordering number.
        prepared = dict(bundle)
        state = dict(bundle.get("engine_state") or {})
        state.pop("projection_sequence", None)
        prepared["engine_state"] = state
        return prepared
    reconciliation = (bundle.get("engine_state") or {}).get("snapshot_reconciliation")
    if isinstance(reconciliation, dict) and reconciliation.get("status") == "reconciled":
        # A clean reconciliation marks the ledger without adopting a new
        # anchor, so it must not consume a root-wide ordering number either.
        prepared = dict(bundle)
        state = dict(bundle.get("engine_state") or {})
        state.pop("projection_sequence", None)
        prepared["engine_state"] = state
        return prepared

    final = session_dir(root, bundle["session_id"])
    if os.path.isdir(final):
        try:
            existing = read_json(os.path.join(final, "bundle.json"))
        except (OSError, ValueError) as exc:
            raise SessionError(
                f"session {bundle['session_id']} has an unreadable canonical bundle: {exc}"
            ) from exc
        sequence = _positive_projection_sequence(
            (existing.get("engine_state") or {}).get("projection_sequence")
        )
        if sequence is None:
            # An immutable pre-sequence bundle must remain byte-for-byte
            # retryable.  Its projected event retains the legacy file-order
            # same-day tie-break.
            prepared = dict(bundle)
            state = dict(bundle.get("engine_state") or {})
            state.pop("projection_sequence", None)
            prepared["engine_state"] = state
            return prepared
    else:
        sequence = _max_projection_sequence(root) + 1

    prepared = dict(bundle)
    state = dict(bundle.get("engine_state") or {})
    state["projection_sequence"] = sequence
    prepared["engine_state"] = state
    return prepared


INITIAL_SNAPSHOT_CONFLICT = (
    "initial snapshot onboarding cannot replace existing coach history; "
    "second or subsequent snapshots require reconciliation"
)

INCOMPLETE_SNAPSHOT_RECONCILIATION = (
    "an incomplete snapshot cannot reconcile existing coach history; "
    "declare the complete account view to compare it with the ledger"
)

SNAPSHOT_RECONCILIATION_STALE = (
    "the ledger changed after this reconciliation diff was prepared; "
    "run prepare again with the same snapshot to refreeze the diff"
)


def scan_initial_snapshot_conflicts(root, anchor, exclude_session_id=None):
    """Return the conflict sources blocking an initial snapshot declaration.

    Single implementation behind both boundary layers: the prepare-time UX
    fail-fast in review.py and the authoritative finalize check that runs under
    the root projection lock.  Any ledger row that is not the identical
    snapshot fact counts as existing history — including unknown event types,
    which the ledger loader would silently drop (strict fail-closed).  Only
    canonical persistent bundles participate, and an exact replay of the same
    declaration is not a conflict.  ``exclude_session_id`` removes the session
    being committed so an idempotent finalize retry cannot conflict with
    itself.
    """
    requested = canonical(_snapshot_payload(anchor))
    conflicts = []
    for event in _read_jsonl(os.path.join(root, "ledger.jsonl")):
        if event.get("type") != "snapshot" or canonical(_snapshot_payload(event)) != requested:
            conflicts.append("ledger")
            break
    for session_id, existing in iter_canonical_bundles(root):
        if exclude_session_id is not None and session_id == exclude_session_id:
            continue
        prior_anchor = (existing.get("engine_state") or {}).get("snapshot_anchor")
        if (not isinstance(prior_anchor, dict)
                or canonical(_snapshot_payload(prior_anchor)) != requested):
            conflicts.append("session")
            break
    return conflicts


def _assert_initial_snapshot_boundary(root, bundle):
    """Admit a runtime snapshot only as initial onboarding or as reconciliation.

    Direct legacy/test bundles do not carry the adapter input kind and retain
    their compatibility path.  Runtime adapter bundles may replay the identical
    declaration (for example in another language).  Any other declaration
    against existing history must carry the reconciliation frozen at prepare,
    and that frozen diff must still equal a recomputation under this root
    projection lock — otherwise finalize fails closed instead of writing an
    adjustment the user never previewed.  A ledger whose current anchor is
    already the declared fact is an idempotent post-adoption replay.  History
    without a complete anchor (replay-only trades, unknown event types, or an
    unrepaired ledger projection) keeps the original fail-closed rejection, and
    an incomplete declaration can never reconcile.  The caller holds the root
    projection lock, closing the two-finalize race.
    """
    plan = bundle.get("review_plan") or {}
    if (plan.get("input") or {}).get("kind") != "positions_snapshot":
        return
    anchor = (bundle.get("engine_state") or {}).get("snapshot_anchor")
    if not isinstance(anchor, dict):
        return
    if not scan_initial_snapshot_conflicts(root, anchor,
                                           exclude_session_id=bundle.get("session_id")):
        return
    frozen = (bundle.get("engine_state") or {}).get("snapshot_reconciliation")
    if not isinstance(frozen, dict):
        raise SessionError(INITIAL_SNAPSHOT_CONFLICT)
    if anchor.get("is_complete", True) is not True:
        raise SessionError(INCOMPLETE_SNAPSHOT_RECONCILIATION)
    events, _skipped = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
    current_anchor = ledger.latest_anchor(events)
    if (current_anchor is not None
            and canonical(_snapshot_payload(current_anchor))
            == canonical(_snapshot_payload(anchor))):
        return
    try:
        current = ledger.snapshot_reconciliation(events, anchor)
    except ValueError as exc:
        raise SessionError(str(exc)) from exc
    if current is None:
        raise SessionError(INITIAL_SNAPSHOT_CONFLICT)
    if canonical(current) != canonical(frozen):
        raise SessionError(SNAPSHOT_RECONCILIATION_STALE)


def _project_snapshot_anchor(root, bundle):
    """Project one validated snapshot fact into the shared ledger idempotently.

    The canonical session is committed before projections run.  A ledger write
    failure is therefore recoverable through ``repair-projections`` and an
    abandoned pending review can never leave an orphan accounting anchor.

    A frozen ``snapshot_reconciliation`` in the bundle's engine state selects
    the repeated-snapshot path (#220): status ``reconciled`` appends only a
    content-addressed reconciliation mark and never a new anchor, while status
    ``adjusted`` appends one content-addressed adjustment event preserving the
    narrow diff plus the newly declared anchor, whose ``projection_sequence``
    lets ``ledger.latest_anchor`` adopt it.  Every write replays as a no-op.
    """
    if bundle.get("route") != "snapshot_review":
        return None
    anchor = ((bundle.get("engine_state") or {}).get("snapshot_anchor"))
    if anchor is None:  # compatibility for pre-adapter snapshot bundles
        return None
    if not isinstance(anchor, dict) or anchor.get("type") != "snapshot":
        raise SessionError("snapshot projection requires a snapshot anchor object")
    try:
        dt.date.fromisoformat(str(anchor.get("as_of")))
    except (TypeError, ValueError) as exc:
        raise SessionError("snapshot projection has invalid as_of") from exc
    if not isinstance(anchor.get("positions"), list) or not anchor["positions"]:
        raise SessionError("snapshot projection requires non-empty positions")

    payload = _snapshot_payload(anchor)
    snapshot_id = "snapshot-" + hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()[:16]
    ledger_path = os.path.join(root, "ledger.jsonl")
    state = bundle.get("engine_state") or {}
    reconciliation = state.get("snapshot_reconciliation")
    if reconciliation is not None and not isinstance(reconciliation, dict):
        raise SessionError("snapshot_reconciliation must be an object")
    status = (reconciliation or {}).get("status")
    if reconciliation is not None and status not in ("reconciled", "adjusted"):
        raise SessionError(f"unsupported snapshot reconciliation status: {status}")
    if payload["is_complete"] is False:
        if reconciliation is not None:
            raise SessionError("an incomplete snapshot cannot carry a reconciliation")
        return {"path": ledger_path, "appended": 0, "status": "skipped_incomplete",
                "snapshot_id": snapshot_id}
    if payload["is_complete"] is not True:
        raise SessionError("snapshot projection requires boolean is_complete")
    sequence = _positive_projection_sequence(state.get("projection_sequence"))
    if "projection_sequence" in state and sequence is None:
        raise SessionError("snapshot projection_sequence must be a positive integer")
    existing = _read_jsonl(ledger_path)

    if status == "reconciled":
        identity = {"type": "reconciliation", "status": "reconciled",
                    "date": reconciliation.get("as_of"),
                    "declared_snapshot_id": snapshot_id,
                    "against": reconciliation.get("against")}
        reconciliation_id = ("reconcile-" + hashlib.sha256(
            canonical(identity).encode("utf-8")).hexdigest()[:16])
        report = {"path": ledger_path, "snapshot_id": snapshot_id,
                  "reconciliation": "reconciled", "reconciliation_id": reconciliation_id}
        if any(row.get("type") == "reconciliation"
               and row.get("reconciliation_id") == reconciliation_id for row in existing):
            return dict(report, appended=0, status="no-op")
        event = dict(identity)
        event["reconciliation_id"] = reconciliation_id
        event["session_id"] = bundle["session_id"]
        ledger.append_events(ledger_path, [event])
        return dict(report, appended=1, status="projected")

    to_append = []
    report = {"path": ledger_path, "snapshot_id": snapshot_id}
    if status == "adjusted":
        identity = {"type": "adjustment", "date": reconciliation.get("as_of"),
                    "reason": "snapshot_reconciliation",
                    "declared_snapshot_id": snapshot_id,
                    "against": reconciliation.get("against"),
                    "diff": reconciliation.get("diff")}
        adjustment_id = ("adjust-" + hashlib.sha256(
            canonical(identity).encode("utf-8")).hexdigest()[:16])
        report["reconciliation"] = "adjusted"
        report["adjustment_id"] = adjustment_id
        if not any(row.get("type") == "adjustment"
                   and row.get("adjustment_id") == adjustment_id for row in existing):
            event = dict(identity)
            event["adjustment_id"] = adjustment_id
            event["session_id"] = bundle["session_id"]
            to_append.append(event)

    anchor_exists = any(
        row.get("type") == "snapshot"
        and (row.get("snapshot_id") == snapshot_id
             or canonical(_snapshot_payload(row)) == canonical(payload))
        for row in existing)
    if not anchor_exists:
        event = dict(payload)
        event["snapshot_id"] = snapshot_id
        event["session_id"] = bundle["session_id"]
        if sequence is not None:
            event["projection_sequence"] = sequence
        to_append.append(event)
    if sequence is not None:
        report["projection_sequence"] = sequence
    if not to_append:
        return dict(report, appended=0, status="no-op")
    ledger.append_events(ledger_path, to_append)
    return dict(report, appended=len(to_append), status="projected")


def _project_legacy_locked(root, bundle, private_md):
    """Project while the caller holds this session's finalize lock."""
    session_id = bundle["session_id"]
    state = dict(bundle.get("engine_state") or {})
    commitment = bundle.get("commitment")
    state["commitment"] = commitment
    state["rule"] = (commitment or {}).get("rule")
    # Replaying an old bundle (repair-projections walks every session) must not
    # regress a newer reconciliation anchor.  Equal-date legacy states keep their
    # file/projection-order behavior, while sequence-aware snapshot states use the
    # same monotonic tie-break as ledger.latest_anchor().
    # Only a VALID ISO date can win — a corrupted date_end ("N/A", "9999-oops")
    # must stay overwritable or the documented repair path could never heal it.
    # A legitimately newer anchor is kept even when it has no bundle: the v1
    # coach.py path writes last_state.json directly without committing one.
    last_state_path = os.path.join(root, "last_state.json")
    last_state_status = "written"

    def _valid_date(value):
        try:
            return dt.date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    try:
        existing_state = read_json(last_state_path)
    except (OSError, ValueError):
        existing_state = None
    existing_date = (_valid_date((existing_state or {}).get("date_end"))
                     if isinstance(existing_state, dict) else None)
    bundle_date = _valid_date(state.get("date_end"))
    existing_snapshot = ((existing_state or {}).get("snapshot_anchor")
                         if isinstance(existing_state, dict) else None)
    bundle_snapshot = state.get("snapshot_anchor")
    existing_sequence = _positive_projection_sequence(
        (existing_state or {}).get("projection_sequence")
        if isinstance(existing_state, dict) else None
    )
    bundle_sequence = _positive_projection_sequence(state.get("projection_sequence"))
    same_day_sequence_regression = (
        existing_date is not None
        and existing_date == bundle_date
        and isinstance(existing_snapshot, dict)
        and isinstance(bundle_snapshot, dict)
        and existing_sequence is not None
        and (bundle_sequence is None or existing_sequence > bundle_sequence)
    )
    if (existing_date and (bundle_date is None or existing_date > bundle_date)
            or same_day_sequence_regression):
        last_state_status = "kept_newer"
    else:
        ledger.atomic_write_text(last_state_path, pretty(state))

    date_end = state.get("date_end")
    log_row = {
        "date_end": date_end,
        "headline_dim": state.get("headline_dim"),
        "commitment": commitment,
        "metrics_snapshot": dict(state.get("metrics") or {}),
        "session_id": session_id,
    }
    reports = []
    snapshot_report = _project_snapshot_anchor(root, bundle)
    if snapshot_report is not None:
        reports.append(snapshot_report)
    reports.append(_append_session_rows(os.path.join(root, "log.jsonl"), session_id, [log_row]))

    thesis_updates = list(bundle.get("thesis_updates") or [])
    exit_narratives = list(bundle.get("exit_narratives") or [])
    reports.append(_append_session_rows(os.path.join(root, "theses.jsonl"), session_id,
                                        thesis_updates + exit_narratives))
    reports.append(_append_session_rows(os.path.join(root, "thesis_decisions.jsonl"), session_id,
                                        list(bundle.get("thesis_decisions") or [])))
    reports.append(_append_session_rows(os.path.join(root, "headline_motives.jsonl"), session_id,
                                        list(bundle.get("headline_motive_events") or [])))
    reports.append(_append_session_rows(os.path.join(root, "revisit.jsonl"), session_id,
                                        list(bundle.get("revisit_resolutions") or [])))

    rule_rows = []
    if commitment and commitment.get("rule"):
        suffix = session_id.split("__")[-1]
        rule_row = {
            "rule_id": f"rule-{suffix}-0",
            "text": commitment["rule"],
            "metric_key": commitment.get("metric_key"),
            "problem_key": PKEY.get(commitment.get("metric_key")),
            "source": "user_chosen",
            "status": "tracking",
            "created": date_end,
            "session_id": session_id,
        }
        if commitment.get("revises_rule_id"):
            rule_row["revises"] = commitment["revises_rule_id"]
        rule_rows.append(rule_row)
    reports.append(_append_session_rows(os.path.join(root, "rules.jsonl"), session_id, rule_rows))

    # The problem book goes through problems.append_book, not _append_session_rows:
    # it stamps type:"event" (load_book drops untyped rows), dedupes by content so
    # replays and overlapping sessions stay idempotent, and records the review_mark
    # that defines the Opportunity Check period boundary (#146). A same-week mark
    # with different opportunities fails closed (#166) after events are written —
    # but visibly, AFTER the card and report projections land: a mark conflict is
    # one projection failing, and it must not hold the session's card hostage.
    problems_path = os.path.join(root, "problems.jsonl")
    opportunities = state.get("problem_opportunities")
    mark = ({"week": date_end, "opportunities": opportunities}
            if date_end and opportunities is not None else None)
    mark_conflict = None
    try:
        n_events, n_marks = problems.append_book(
            problems_path, list(state.get("problem_events") or []), mark, session_id=session_id)
        problems_report = {"path": problems_path, "appended": n_events, "marks": n_marks,
                           "status": "projected" if (n_events or n_marks) else "no-op"}
    except ValueError as exc:
        mark_conflict = exc
        problems_report = {"path": problems_path, "status": "mark_conflict", "error": str(exc)}
    reports.append(problems_report)
    card_report = _project_card(root, bundle, private_md)
    report = {"schema_version": 1, "session_id": session_id, "rows": reports, "card": card_report,
              "last_state": last_state_status}
    ledger.atomic_write_text(os.path.join(root, "projections", session_id + ".json"), pretty(report))
    if mark_conflict is not None:
        raise mark_conflict
    return report


def project_legacy(root, bundle, private_md):
    """Project a committed bundle into v1 files. Safe to rerun after interruption."""
    session_id = _safe_id(bundle.get("session_id"))
    root, sessions = _prepare_session_storage(root, session_id)
    with _session_lock(sessions, session_id):
        with _projection_lock(sessions):
            return _project_legacy_locked(root, bundle, private_md)


class FinalizeTransaction:
    """Operations available only while one session's finalize lock is held."""

    def __init__(self, root, sessions, session_id):
        self.root = root
        self.sessions = sessions
        self.session_id = session_id
        self._active = True

    def commit_bundle(self, bundle, private_md, public_md, private_html=None, persist=True):
        if not self._active:
            raise SessionError("finalize transaction is no longer active")
        if _safe_id(bundle.get("session_id")) != self.session_id:
            raise SessionError("finalize transaction session_id mismatch")
        if persist:
            projection = None
            projection_error = None
            # Every persistent canonical session participates in the snapshot
            # initial-history boundary.  Hold the root lock across commit and
            # projection so a weekly/first review cannot appear between the
            # snapshot's final check and canonical rename.
            with _projection_lock(self.sessions):
                prepared = bundle
                if bundle.get("route") == "snapshot_review":
                    _assert_initial_snapshot_boundary(self.root, bundle)
                    prepared = _snapshot_bundle_for_commit(self.root, bundle)
                result = _commit_bundle_locked(
                    self.root, self.sessions, prepared, private_md, public_md, private_html
                )
                try:
                    projection = _project_legacy_locked(self.root, prepared, private_md)
                except Exception as exc:  # canonical bundle is safe; repair can retry
                    projection_error = str(exc)
            return result, projection, projection_error

        result = _commit_bundle_locked(
            self.root, self.sessions, bundle, private_md, public_md, private_html
        )
        return result, None, None


@contextlib.contextmanager
def finalize_transaction(root, session_id):
    """Lock before any pending/canonical read and hold through projection."""
    session_id = _safe_id(session_id)
    root, sessions = _prepare_session_storage(root, session_id)
    with _session_lock(sessions, session_id, fail_if_busy=True):
        transaction = FinalizeTransaction(root, sessions, session_id)
        try:
            yield transaction
        finally:
            transaction._active = False


def load_committed(root, session_id):
    path = session_dir(root, session_id)
    if not os.path.isdir(path):
        raise SessionError(f"committed session not found: {session_id}")
    return read_json(os.path.join(path, "bundle.json"))


def repair_projections(root):
    """Rebuild legacy projections from committed bundles.

    Skips non-persistent sessions (test drive) so demo data never reaches real
    coach memory, and keeps going past corrupt session directories instead of
    aborting the whole repair.

    This walk deliberately does not go through ``iter_canonical_bundles``: the
    bundle must be read under the per-session finalize lock, and unreadable
    bundles are reported as errors rather than silently skipped.  Only the
    demo-filter decision is shared, via ``_is_demo_bundle``."""
    reports, skipped, errors = [], [], []
    base = os.path.join(root, "sessions")
    if not os.path.isdir(base):
        return {"reports": reports, "skipped": skipped, "errors": errors}
    for session_id in sorted(os.listdir(base)):
        path = os.path.join(base, session_id)
        if not os.path.isdir(path) or session_id.startswith("."):
            continue
        try:
            with _session_lock(base, session_id):
                bundle = read_json(os.path.join(path, "bundle.json"))
                if _is_demo_bundle(bundle):
                    skipped.append({"session_id": session_id, "reason": "test_drive or persist:false"})
                    continue
                with open(os.path.join(path, "card-private.md"), encoding="utf-8") as f:
                    private_md = f.read()
                with _projection_lock(base):
                    reports.append(_project_legacy_locked(root, bundle, private_md))
        except (OSError, ValueError) as exc:
            errors.append({"session_id": session_id, "error": str(exc)})
    return {"reports": reports, "skipped": skipped, "errors": errors}
