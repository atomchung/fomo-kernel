#!/usr/bin/env python3
"""Append and verify a local presentation trace for a cross-client review.

The engine owns canonical state. `engine/review.py preview`/`finalize` already
fail closed on answer completeness (`thesis.validate_required_answers`) and on
the final commitment, so this helper never re-checks them. It records only the
execution-layer facts the engine cannot observe: whether the host actually
presented the preview/final card inline, whether a declared widget silently
degraded to a file link, and how required questions or the weekly opening
memory were surfaced.

Every appended row is stamped with a UTC `ts` (ISO-8601 seconds, e.g.
2026-07-20T13:46:02Z) at write time. Two content-free marker events make
user-visible latency measurable: `answers_received` (the user's final required
answer arrived, recorded just before calling `preview`) and
`rule_choice_presented` (the rule choice — candidate rules, custom, or skip —
was shown to the user after the preview card). The machine wait between
answering and seeing the preview card is `card_presented(stage=preview).ts -
answers_received.ts`. Verification treats `ts` as optional so traces written
before this field existed still verify, but rejects a malformed value; row
order, not `ts`, remains the ordering authority.

The trace lives inside the protected state directory, the same trust boundary
as the canonical ledger: never committed and never published. The root resolves
exactly like the engine CLIs (`--state-root` > `TRADE_COACH_HOME` >
~/.trade-coach), so one `export TRADE_COACH_HOME=...` routes every tool in the
lifecycle into the same root (#269). Question-presentation rows are additionally restricted to mode,
surface source, and an opaque digest so cross-client evidence cannot copy the
private question surface into the trace.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections import Counter
from datetime import datetime, timezone


VERSION = 2
DEFAULT_STATE_ROOT = "~/.trade-coach"


def _default_state_root() -> str:
    """Mirror engine/session.default_root(): TRADE_COACH_HOME, else ~/.trade-coach.

    This tool is deliberately stdlib-only, so the one-line resolution is
    mirrored here instead of importing the engine. Resolved at invocation time
    (not parser-build time) so the CLI honors the environment it runs in.
    """
    return os.environ.get("TRADE_COACH_HOME", DEFAULT_STATE_ROOT)
QUESTION_MODES = ("native_options", "plain_text")
SURFACE_SOURCES = ("validated_dynamic", "engine_fallback")
CARD_MODES = ("widget", "markdown_inline")
ROUTES = ("first_review", "weekly_review", "snapshot_review", "test_drive")
STAGES = ("preview", "final")
MEMORY_KINDS = ("prior_commitment", "prior_skip", "exit_reason", "due_revisit")
WEEKLY_OPENERS = ("prior_commitment", "prior_skip")
EVENT_KINDS = (
    "question_presented",
    "answers_received",
    "artifact_generated",
    "card_presented",
    "rule_choice_presented",
    "memory_presented",
    "widget_attempt_failed",
    "owner_verdict",
)
SURFACE_DIGEST = re.compile(r"^[a-f0-9]{64}$")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ReceiptError(ValueError):
    """A trace command or verification violates the presentation contract."""


def _compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def _valid_ts(value: object) -> bool:
    if not isinstance(value, str) or not TS_PATTERN.fullmatch(value):
        return False
    try:
        datetime.strptime(value, TS_FORMAT)
    except ValueError:
        return False
    return True


def _receipt_path(session_id: str, state_root: str) -> pathlib.Path:
    """Resolve the trace path inside the protected state directory.

    The tool owns placement so an adapter cannot put the trace somewhere
    untrusted. `ux/` keeps it out of the way of session/snapshot scans.
    """
    if not session_id:
        raise ReceiptError("a session id is required")
    if session_id in {".", ".."} or any(ch in session_id for ch in ("/", "\\", "\x00")):
        raise ReceiptError("session id must be a bare identifier, not a path")
    if state_root is None:  # direct-import callers may pass parser output unnormalized
        state_root = _default_state_root()
    root = pathlib.Path(os.path.expanduser(state_root))
    return root / "ux" / f"{session_id}.jsonl"


def _read_rows(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        raise ReceiptError(f"trace not found: {path}")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReceiptError(f"trace line {line_number} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ReceiptError(f"trace line {line_number} must be a JSON object")
        rows.append(row)
    if not rows:
        raise ReceiptError("trace is empty")
    return rows


def _append(path: pathlib.Path, row: dict) -> None:
    # Write-time stamp on every persisted row: latency between user-visible
    # moments is measurable from the trace while verify keeps ts optional.
    row = {**row, "ts": _utc_now()}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (_compact_json(row) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def start_receipt(args: argparse.Namespace) -> None:
    path = _receipt_path(args.session_id, args.state_root)
    if path.exists():
        raise ReceiptError(f"refusing to overwrite existing trace: {path}")
    # plain_text and markdown_inline are universal fallbacks every text-based
    # client can render (interaction-delivery.md: "must always be declared").
    # Guarantee them here instead of leaving it to the caller to remember —
    # a caller only needs to additionally declare native_options/widget when
    # the host actually exposes them.
    question_modes = list(dict.fromkeys([*args.question_mode, "plain_text"]))
    card_modes = list(dict.fromkeys([*args.card_mode, "markdown_inline"]))
    row = {
        "version": VERSION,
        "event": "capabilities_declared",
        "session_id": args.session_id,
        "client": args.client,
        "route": args.route,
        "question_modes": question_modes,
        "card_modes": card_modes,
    }
    _append(path, row)


def _event_row(args: argparse.Namespace, declaration: dict) -> dict:
    row = {"version": VERSION, "event": args.event, "session_id": declaration["session_id"]}
    if args.event in ("question_presented", "rule_choice_presented"):
        if not args.mode:
            raise ReceiptError(f"{args.event} requires --mode")
        if args.mode not in QUESTION_MODES:
            raise ReceiptError(f"question mode must be one of {QUESTION_MODES}")
        row["mode"] = args.mode
    if args.event == "question_presented":
        has_source = bool(args.surface_source)
        has_digest = bool(args.surface_digest)
        if has_source != has_digest:
            raise ReceiptError("question_presented surface source and digest must be recorded together")
        if has_source:
            if args.surface_source not in SURFACE_SOURCES:
                raise ReceiptError(f"surface source must be one of {SURFACE_SOURCES}")
            if not SURFACE_DIGEST.fullmatch(args.surface_digest):
                raise ReceiptError("surface digest must be a lowercase sha256 hex string")
            row["surface_source"] = args.surface_source
            row["surface_digest"] = args.surface_digest
    if args.event in {"artifact_generated", "card_presented", "widget_attempt_failed"}:
        if args.stage not in STAGES:
            raise ReceiptError(f"{args.event} requires --stage in {STAGES}")
        row["stage"] = args.stage
    if args.event == "artifact_generated":
        if not args.artifact_path:
            raise ReceiptError("artifact_generated requires --artifact-path")
        row["artifact_path"] = os.path.abspath(os.path.expanduser(args.artifact_path))
    if args.event == "card_presented":
        if not args.mode:
            raise ReceiptError("card_presented requires --mode")
        if args.mode not in CARD_MODES:
            raise ReceiptError(f"card mode must be one of {CARD_MODES}")
        row["mode"] = args.mode
    if args.event == "memory_presented":
        if not args.memory_kind:
            raise ReceiptError("memory_presented requires --memory-kind")
        row["memory_kind"] = args.memory_kind
    if args.event == "owner_verdict":
        verdicts = {"controls": args.controls, "card": args.card, "memory": args.memory}
        if any(value is None for value in verdicts.values()):
            raise ReceiptError("owner_verdict requires --controls, --card, and --memory")
        row.update(verdicts)
        question_verdicts = {
            "question_specificity": args.question_specificity,
            "answer_fit": args.answer_fit,
        }
        if any(value is not None for value in question_verdicts.values()):
            if any(value is None for value in question_verdicts.values()):
                raise ReceiptError(
                    "owner_verdict question specificity and answer fit must be recorded together"
                )
            row.update(question_verdicts)
    return row


def record_event(args: argparse.Namespace) -> None:
    path = _receipt_path(args.session_id, args.state_root)
    rows = _read_rows(path)
    if rows[0].get("event") != "capabilities_declared":
        raise ReceiptError("first trace row must declare capabilities")
    _append(path, _event_row(args, rows[0]))


def _positions(rows: list[dict], event: str, **matches) -> list[int]:
    return [
        index
        for index, row in enumerate(rows)
        if row.get("event") == event and all(row.get(key) == value for key, value in matches.items())
    ]


def verify_rows(rows: list[dict], require_owner_verdict: bool = False) -> list[str]:
    """Return deterministic presentation-contract errors; an empty list means pass.

    Scope is deliberately narrow: prove that each engine-rendered card actually
    reached the user, in order, plus the weekly opening memory. Answer and
    commitment completeness belong to the engine and are not re-verified here.
    `ts` is optional metadata (legacy traces predate it) validated only for
    format when present; row order, not `ts`, is the ordering authority.
    """
    errors: list[str] = []
    if _positions(rows, "capabilities_declared") != [0]:
        return ["trace must contain exactly one capabilities_declared event as its first row"]
    declaration = rows[0]
    session_id = declaration.get("session_id")
    route = declaration.get("route")
    if declaration.get("version") != VERSION:
        errors.append(f"unsupported trace version: {declaration.get('version')!r}")
    if route not in ROUTES:
        errors.append(f"unsupported route: {route!r}")
    if any(row.get("session_id") != session_id for row in rows):
        errors.append("all events must use the declared session_id")
    for index, row in enumerate(rows, 1):
        event = row.get("event")
        if event != "capabilities_declared" and event not in EVENT_KINDS:
            errors.append(f"row {index} has unsupported event {event!r}")
        if row.get("version") != VERSION:
            errors.append(f"row {index} has unsupported version {row.get('version')!r}")
        if "ts" in row and not _valid_ts(row["ts"]):
            errors.append(
                f"row {index} has invalid ts {row.get('ts')!r}"
                " (expected UTC seconds like 2026-07-20T13:46:02Z)"
            )
        if event in {"artifact_generated", "card_presented", "widget_attempt_failed"} and row.get("stage") not in STAGES:
            errors.append(f"row {index} has unsupported stage {row.get('stage')!r}")
        if event == "memory_presented" and row.get("memory_kind") not in MEMORY_KINDS:
            errors.append(f"row {index} has unsupported memory kind {row.get('memory_kind')!r}")
        if event == "question_presented":
            allowed = {"version", "event", "session_id", "ts", "mode", "surface_source", "surface_digest"}
            extra = sorted(set(row) - allowed)
            if extra:
                errors.append(
                    f"row {index} question trace contains content fields: {', '.join(extra)}"
                )
            source = row.get("surface_source")
            digest = row.get("surface_digest")
            if bool(source) != bool(digest):
                errors.append(f"row {index} surface source and digest must appear together")
            elif source:
                if source not in SURFACE_SOURCES:
                    errors.append(f"row {index} has unsupported surface source {source!r}")
                if not isinstance(digest, str) or not SURFACE_DIGEST.fullmatch(digest):
                    errors.append(f"row {index} has invalid surface digest")
        # The latency markers are pure timestamps: any extra key is rejected so
        # they can never grow into a content side channel.
        if event == "answers_received":
            extra = sorted(set(row) - {"version", "event", "session_id", "ts"})
            if extra:
                errors.append(
                    f"row {index} answers_received contains unsupported fields: {', '.join(extra)}"
                )
        if event == "rule_choice_presented":
            extra = sorted(set(row) - {"version", "event", "session_id", "ts", "mode"})
            if extra:
                errors.append(
                    f"row {index} rule_choice_presented contains unsupported fields: {', '.join(extra)}"
                )

    question_modes = set(declaration.get("question_modes") or [])
    card_modes = set(declaration.get("card_modes") or [])
    if not question_modes <= set(QUESTION_MODES) or "plain_text" not in question_modes:
        errors.append("capabilities must declare plain_text as the universal question fallback")
    if not card_modes <= set(CARD_MODES) or "markdown_inline" not in card_modes:
        errors.append("capabilities must declare markdown_inline as the universal card fallback")
    for row in rows:
        if row.get("event") == "question_presented" and row.get("mode") not in question_modes:
            errors.append(f"question used undeclared mode {row.get('mode')!r}")
        if row.get("event") == "rule_choice_presented" and row.get("mode") not in question_modes:
            errors.append(f"rule choice used undeclared mode {row.get('mode')!r}")

    # Presentation is the whole point: each stage must show its engine artifact
    # actually reached the user inline, in order.
    for stage in STAGES:
        artifacts = _positions(rows, "artifact_generated", stage=stage)
        cards = _positions(rows, "card_presented", stage=stage)
        if len(artifacts) != 1:
            errors.append(f"{stage} artifact_generated must appear exactly once")
        if len(cards) != 1:
            errors.append(f"{stage} card_presented must appear exactly once")
        if len(artifacts) == len(cards) == 1 and artifacts[0] >= cards[0]:
            errors.append(f"{stage} card was marked presented before its artifact existed")
        for position in cards:
            mode = rows[position].get("mode")
            if mode not in card_modes:
                errors.append(f"{stage} card used undeclared mode {mode!r}")
            if mode == "markdown_inline" and "widget" in card_modes:
                if not _positions(rows[:position], "widget_attempt_failed"):
                    errors.append(
                        f"{stage} used Markdown despite widget capability without recording a failed widget attempt"
                    )

    preview_card = _positions(rows, "card_presented", stage="preview")
    final_card = _positions(rows, "card_presented", stage="final")
    final_artifact = _positions(rows, "artifact_generated", stage="final")
    if preview_card and final_card and preview_card[0] >= final_card[0]:
        errors.append("final card presentation must follow the preview card")
    if preview_card and final_artifact and final_artifact[0] <= preview_card[0]:
        errors.append("final artifact was generated before the preview card was presented")

    if Counter(row.get("event") for row in rows)["widget_attempt_failed"] and "widget" not in card_modes:
        errors.append("widget failure was recorded without declared widget capability")

    # Weekly review must prove the opening memory (the committed prior rule, or an
    # explicit prior skip) was actually surfaced before the first card.
    if route == "weekly_review":
        openers = [
            index
            for index, row in enumerate(rows)
            if row.get("event") == "memory_presented" and row.get("memory_kind") in WEEKLY_OPENERS
        ]
        first_surface = min(
            _positions(rows, "question_presented") + preview_card + final_card or [len(rows)]
        )
        if len(openers) != 1:
            errors.append("weekly_review must present exactly one prior commitment or skip opener")
        elif openers[0] >= first_surface:
            errors.append("weekly opening memory was presented after the first question or card")

    verdicts = _positions(rows, "owner_verdict")
    if len(verdicts) > 1:
        errors.append("owner_verdict may appear at most once")
    if verdicts:
        verdict = rows[verdicts[0]]
        if verdict.get("controls") not in {"pass", "fail"}:
            errors.append("owner controls verdict must be pass or fail")
        if verdict.get("card") not in {"pass", "fail"}:
            errors.append("owner card verdict must be pass or fail")
        if verdict.get("memory") not in {"pass", "fail", "not_applicable"}:
            errors.append("owner memory verdict must be pass, fail, or not_applicable")
        question_verdicts = (verdict.get("question_specificity"), verdict.get("answer_fit"))
        if any(value is not None for value in question_verdicts):
            if any(value not in {"pass", "fail"} for value in question_verdicts):
                errors.append("owner question specificity and answer fit verdicts must both be pass or fail")
        if final_card and verdicts[0] <= final_card[0]:
            errors.append("owner_verdict must follow the final card presentation")
    if require_owner_verdict:
        if len(verdicts) != 1:
            errors.append("manual verification requires exactly one owner_verdict")
        elif any(rows[verdicts[0]].get(key) != "pass" for key in ("controls", "card")):
            errors.append("manual verification requires passing controls and card verdicts")
        elif route == "weekly_review" and rows[verdicts[0]].get("memory") != "pass":
            errors.append("weekly manual verification requires a passing memory verdict")
        elif any(row.get("event") == "question_presented" and
                 row.get("surface_source") == "validated_dynamic" for row in rows):
            verdict = rows[verdicts[0]]
            if any(verdict.get(key) != "pass" for key in
                   ("question_specificity", "answer_fit")):
                errors.append(
                    "dynamic-surface manual verification requires passing question specificity "
                    "and answer fit verdicts"
                )

    return errors


def verify_receipt(args: argparse.Namespace) -> None:
    rows = _read_rows(_receipt_path(args.session_id, args.state_root))
    errors = verify_rows(rows, require_owner_verdict=args.require_owner_verdict)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(_compact_json({"status": "pass", "events": len(rows), "session_id": rows[0]["session_id"]}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--session-id", required=True)
        sub.add_argument("--state-root", default=None,
                         help="protected state directory (default: $TRADE_COACH_HOME, else ~/.trade-coach)")

    start = subparsers.add_parser("start", help="declare one host adapter's capabilities")
    add_common(start)
    start.add_argument("--client", required=True)
    start.add_argument("--route", required=True, choices=ROUTES)
    start.add_argument("--question-mode", action="append", choices=QUESTION_MODES, required=True)
    start.add_argument("--card-mode", action="append", choices=CARD_MODES, required=True)
    start.set_defaults(handler=start_receipt)

    event = subparsers.add_parser("event", help="append a presentation fact after the user-visible action")
    add_common(event)
    event.add_argument("--event", required=True, choices=EVENT_KINDS)
    event.add_argument("--mode")
    event.add_argument("--surface-source", choices=SURFACE_SOURCES)
    event.add_argument("--surface-digest")
    event.add_argument("--stage", choices=STAGES)
    event.add_argument("--artifact-path")
    event.add_argument("--memory-kind", choices=MEMORY_KINDS)
    event.add_argument("--controls", choices=("pass", "fail"))
    event.add_argument("--card", choices=("pass", "fail"))
    event.add_argument("--memory", choices=("pass", "fail", "not_applicable"))
    event.add_argument("--question-specificity", choices=("pass", "fail"))
    event.add_argument("--answer-fit", choices=("pass", "fail"))
    event.set_defaults(handler=record_event)

    verify = subparsers.add_parser("verify", help="confirm each card actually reached the user")
    add_common(verify)
    verify.add_argument("--require-owner-verdict", action="store_true")
    verify.set_defaults(handler=verify_receipt)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.state_root is None:
        args.state_root = _default_state_root()
    try:
        args.handler(args)
    except ReceiptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
