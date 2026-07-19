#!/usr/bin/env python3
"""Append and verify a privacy-safe surface interaction receipt.

The review engine owns artifacts and canonical state. This helper owns only
execution-layer evidence that the engine cannot observe: which controls the
host exposed, whether a question was actually shown and answered, and whether
the preview/final card was actually presented inline.

The receipt deliberately accepts identifiers, modes, and paths only. It has no
argument for question text, answers, card text, tickers, or amounts.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections import Counter


VERSION = 1
QUESTION_MODES = ("native_options", "plain_text")
CARD_MODES = ("widget", "markdown_inline")
ROUTES = ("first_review", "weekly_review", "snapshot_review", "test_drive")
STAGES = ("preview", "final")
MEMORY_KINDS = ("prior_commitment", "prior_skip", "exit_reason", "due_revisit")
EVENTS = (
    "question_presented",
    "question_answered",
    "artifact_generated",
    "card_presented",
    "commitment_answered",
    "memory_presented",
    "widget_attempt_failed",
    "owner_verdict",
)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:#@/-]{0,255}$")
COMMON_FIELDS = {"version", "event", "session_id"}
EVENT_FIELDS = {
    "capabilities_declared": COMMON_FIELDS | {
        "client", "route", "question_modes", "card_modes",
        "required_question_ids", "expected_memory",
    },
    "question_presented": COMMON_FIELDS | {"question_id", "mode"},
    "question_answered": COMMON_FIELDS | {"question_id"},
    "artifact_generated": COMMON_FIELDS | {"stage", "artifact_path"},
    "card_presented": COMMON_FIELDS | {"stage", "mode"},
    "commitment_answered": COMMON_FIELDS,
    "memory_presented": COMMON_FIELDS | {"memory_kind"},
    "widget_attempt_failed": COMMON_FIELDS | {"stage"},
    "owner_verdict": COMMON_FIELDS | {"controls", "card", "memory"},
}


class ReceiptError(ValueError):
    """A receipt command or trajectory violates the adapter contract."""


def _compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _require_safe_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ReceiptError(f"{label} must be a non-sensitive identifier, not free text")
    return value


def _read_rows(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        raise ReceiptError(f"receipt not found: {path}")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReceiptError(f"receipt line {line_number} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ReceiptError(f"receipt line {line_number} must be a JSON object")
        rows.append(row)
    if not rows:
        raise ReceiptError("receipt is empty")
    return rows


def _append(path: pathlib.Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (_compact_json(row) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def start_receipt(args: argparse.Namespace) -> None:
    path = pathlib.Path(args.path).expanduser()
    if path.exists():
        raise ReceiptError(f"refusing to overwrite existing receipt: {path}")
    question_ids = [_require_safe_id(value, "required question id") for value in args.required_question]
    if len(question_ids) != len(set(question_ids)):
        raise ReceiptError("required question ids must be unique")
    expected_memory = list(args.expected_memory)
    if len(expected_memory) != len(set(expected_memory)):
        raise ReceiptError("expected memory kinds must be unique")
    if args.route == "weekly_review":
        openers = {"prior_commitment", "prior_skip"} & set(expected_memory)
        if len(openers) != 1:
            raise ReceiptError(
                "weekly_review must expect exactly one opening memory: prior_commitment or prior_skip"
            )
    row = {
        "version": VERSION,
        "event": "capabilities_declared",
        "session_id": _require_safe_id(args.session_id, "session id"),
        "client": _require_safe_id(args.client, "client"),
        "route": args.route,
        "question_modes": list(dict.fromkeys(args.question_mode)),
        "card_modes": list(dict.fromkeys(args.card_mode)),
        "required_question_ids": question_ids,
        "expected_memory": expected_memory,
    }
    _append(path, row)


def _event_row(args: argparse.Namespace, declaration: dict) -> dict:
    row = {
        "version": VERSION,
        "event": args.event,
        "session_id": declaration["session_id"],
    }
    if args.event in {"question_presented", "question_answered"}:
        if not args.question_id:
            raise ReceiptError(f"{args.event} requires --question-id")
        row["question_id"] = _require_safe_id(args.question_id, "question id")
    if args.event == "question_presented":
        if not args.mode:
            raise ReceiptError("question_presented requires --mode")
        if args.mode not in QUESTION_MODES:
            raise ReceiptError(f"question mode must be one of {QUESTION_MODES}")
        row["mode"] = args.mode
    if args.event in {"artifact_generated", "card_presented", "widget_attempt_failed"}:
        if not args.stage:
            raise ReceiptError(f"{args.event} requires --stage")
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
        verdicts = {
            "controls": args.controls,
            "card": args.card,
            "memory": args.memory,
        }
        if any(value is None for value in verdicts.values()):
            raise ReceiptError("owner_verdict requires --controls, --card, and --memory")
        row.update(verdicts)
    return row


def record_event(args: argparse.Namespace) -> None:
    path = pathlib.Path(args.path).expanduser()
    rows = _read_rows(path)
    declaration = rows[0]
    if declaration.get("event") != "capabilities_declared":
        raise ReceiptError("first receipt row must declare capabilities")
    _append(path, _event_row(args, declaration))


def _positions(rows: list[dict], event: str, **matches) -> list[int]:
    return [
        index
        for index, row in enumerate(rows)
        if row.get("event") == event and all(row.get(key) == value for key, value in matches.items())
    ]


def verify_rows(rows: list[dict], require_owner_verdict: bool = False) -> list[str]:
    """Return deterministic contract errors; an empty list means pass."""
    errors: list[str] = []
    declarations = _positions(rows, "capabilities_declared")
    if declarations != [0]:
        return ["receipt must contain exactly one capabilities_declared event as its first row"]
    declaration = rows[0]
    if declaration.get("version") != VERSION:
        errors.append(f"unsupported receipt version: {declaration.get('version')!r}")
    session_id = declaration.get("session_id")
    if not isinstance(session_id, str) or not SAFE_ID.fullmatch(session_id):
        errors.append("declared session_id is not a bounded identifier")
    if declaration.get("route") not in ROUTES:
        errors.append(f"unsupported route: {declaration.get('route')!r}")
    if any(row.get("session_id") != session_id for row in rows):
        errors.append("all events must use the declared session_id")
    for index, row in enumerate(rows, 1):
        event = row.get("event")
        allowed = EVENT_FIELDS.get(event)
        if allowed is None:
            errors.append(f"row {index} has unsupported event {event!r}")
            continue
        extra = set(row) - allowed
        if extra:
            errors.append(f"row {index} contains forbidden receipt fields: {sorted(extra)}")
        if row.get("version") != VERSION:
            errors.append(f"row {index} has unsupported version {row.get('version')!r}")

    question_modes = set(declaration.get("question_modes") or [])
    card_modes = set(declaration.get("card_modes") or [])
    if not question_modes or not question_modes <= set(QUESTION_MODES):
        errors.append("capabilities must declare at least one supported question mode")
    if "plain_text" not in question_modes:
        errors.append("plain_text must be declared as the universal question fallback")
    if not card_modes or not card_modes <= set(CARD_MODES):
        errors.append("capabilities must declare at least one supported inline card mode")
    if "markdown_inline" not in card_modes:
        errors.append("markdown_inline must be declared as the universal card fallback")

    required_ids = declaration.get("required_question_ids") or []
    if len(required_ids) != len(set(required_ids)):
        errors.append("required_question_ids must be unique")
    for question_id in required_ids:
        if not isinstance(question_id, str) or not SAFE_ID.fullmatch(question_id):
            errors.append(f"required question id is not bounded: {question_id!r}")
        shown = _positions(rows, "question_presented", question_id=question_id)
        answered = _positions(rows, "question_answered", question_id=question_id)
        if len(shown) != 1:
            errors.append(f"required question {question_id!r} must be presented exactly once")
        if len(answered) != 1:
            errors.append(f"required question {question_id!r} must be answered exactly once")
        if len(shown) == len(answered) == 1 and shown[0] >= answered[0]:
            errors.append(f"required question {question_id!r} was answered before it was presented")
    known_questions = set(required_ids)
    for row in rows:
        if row.get("event") in {"question_presented", "question_answered"}:
            if row.get("question_id") not in known_questions:
                errors.append(f"receipt contains undeclared question {row.get('question_id')!r}")
        if row.get("event") == "question_presented" and row.get("mode") not in question_modes:
            errors.append(f"question used undeclared mode {row.get('mode')!r}")
    for current_id, next_id in zip(required_ids, required_ids[1:]):
        current_shown = _positions(rows, "question_presented", question_id=current_id)
        current_answered = _positions(rows, "question_answered", question_id=current_id)
        next_shown = _positions(rows, "question_presented", question_id=next_id)
        if current_shown and next_shown and current_shown[0] >= next_shown[0]:
            errors.append("required questions were not presented in queue order")
        if current_answered and next_shown and current_answered[0] >= next_shown[0]:
            errors.append(f"question {next_id!r} was presented before {current_id!r} was answered")

    expected_memory = declaration.get("expected_memory") or []
    if len(expected_memory) != len(set(expected_memory)) or not set(expected_memory) <= set(MEMORY_KINDS):
        errors.append("expected_memory must contain unique supported memory kinds")
    if declaration.get("route") == "weekly_review":
        openers = {"prior_commitment", "prior_skip"} & set(expected_memory)
        if len(openers) != 1:
            errors.append("weekly_review must expect exactly one prior commitment or skip opener")
    first_question_or_preview = min(
        _positions(rows, "question_presented") + _positions(rows, "artifact_generated", stage="preview")
        or [len(rows)]
    )
    for memory_kind in expected_memory:
        shown = _positions(rows, "memory_presented", memory_kind=memory_kind)
        if len(shown) != 1:
            errors.append(f"expected memory {memory_kind!r} must be presented exactly once")
        elif memory_kind in {"prior_commitment", "prior_skip"} and shown[0] >= first_question_or_preview:
            errors.append(f"weekly opening memory {memory_kind!r} was presented too late")
    unexpected_memory = [
        row.get("memory_kind")
        for row in rows
        if row.get("event") == "memory_presented"
        and row.get("memory_kind") not in set(expected_memory)
    ]
    if unexpected_memory:
        errors.append(f"receipt contains undeclared memory presentations: {unexpected_memory}")

    event_counts = Counter(row.get("event") for row in rows)
    if event_counts["widget_attempt_failed"] > 1:
        errors.append("widget may be attempted and fail at most once per session")
    if event_counts["widget_attempt_failed"] and "widget" not in card_modes:
        errors.append("widget failure was recorded without declared widget capability")
    if event_counts["commitment_answered"] != 1:
        errors.append("commitment must be answered exactly once after preview")
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
                failed = _positions(rows[:position], "widget_attempt_failed")
                if not failed:
                    errors.append(
                        f"{stage} used Markdown despite widget capability without recording a failed widget attempt"
                    )

    preview_artifact = _positions(rows, "artifact_generated", stage="preview")
    preview_card = _positions(rows, "card_presented", stage="preview")
    commitment = _positions(rows, "commitment_answered")
    final_artifact = _positions(rows, "artifact_generated", stage="final")
    final_card = _positions(rows, "card_presented", stage="final")
    answer_positions = _positions(rows, "question_answered")
    if preview_artifact and answer_positions and max(answer_positions) >= preview_artifact[0]:
        errors.append("preview artifact was generated before every required question was answered")
    if preview_card and commitment and preview_card[0] >= commitment[0]:
        errors.append("commitment was answered before the preview card was presented")
    if commitment and final_artifact and commitment[0] >= final_artifact[0]:
        errors.append("final artifact was generated before the commitment answer")
    if final_artifact and final_card and final_artifact[0] >= final_card[0]:
        errors.append("final card presentation must follow final artifact generation")

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
    if verdicts and final_card and verdicts[0] <= final_card[0]:
        errors.append("owner_verdict must follow the final card presentation")
    if require_owner_verdict:
        if len(verdicts) != 1:
            errors.append("manual verification requires exactly one owner_verdict")
        elif any(rows[verdicts[0]].get(key) != "pass" for key in ("controls", "card")):
            errors.append("manual verification requires passing controls and card verdicts")
        elif declaration.get("route") == "weekly_review" and rows[verdicts[0]].get("memory") != "pass":
            errors.append("weekly manual verification requires a passing memory verdict")

    return errors


def verify_receipt(args: argparse.Namespace) -> None:
    rows = _read_rows(pathlib.Path(args.path).expanduser())
    errors = verify_rows(rows, require_owner_verdict=args.require_owner_verdict)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(_compact_json({"status": "pass", "events": len(rows), "session_id": rows[0]["session_id"]}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="declare one host adapter's capabilities")
    start.add_argument("--path", required=True)
    start.add_argument("--session-id", required=True)
    start.add_argument("--client", required=True)
    start.add_argument("--route", required=True, choices=ROUTES)
    start.add_argument("--question-mode", action="append", choices=QUESTION_MODES, required=True)
    start.add_argument("--card-mode", action="append", choices=CARD_MODES, required=True)
    start.add_argument("--required-question", action="append", default=[])
    start.add_argument("--expected-memory", action="append", choices=MEMORY_KINDS, default=[])
    start.set_defaults(handler=start_receipt)

    event = subparsers.add_parser("event", help="append evidence only after the user-visible action")
    event.add_argument("--path", required=True)
    event.add_argument("--event", required=True, choices=EVENTS)
    event.add_argument("--question-id")
    event.add_argument("--mode")
    event.add_argument("--stage", choices=STAGES)
    event.add_argument("--artifact-path")
    event.add_argument("--memory-kind", choices=MEMORY_KINDS)
    event.add_argument("--controls", choices=("pass", "fail"))
    event.add_argument("--card", choices=("pass", "fail"))
    event.add_argument("--memory", choices=("pass", "fail", "not_applicable"))
    event.set_defaults(handler=record_event)

    verify = subparsers.add_parser("verify", help="distinguish generated artifacts from completed UX")
    verify.add_argument("--path", required=True)
    verify.add_argument("--require-owner-verdict", action="store_true")
    verify.set_defaults(handler=verify_receipt)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except ReceiptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
