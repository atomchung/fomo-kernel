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
order, not `ts`, remains the ordering authority. For a fully timestamped,
multi-stage trace ending in an owner verdict, verification also reports a
machine-readable timing-integrity assessment. Timestamp reversal or a trace
backfilled inside a few seconds is suspect: normal verification warns for
compatibility, while human-graded QA can fail closed with
`--require-timing-integrity`.

Not every route renders a card. A book refresh (`flows/book-refresh.md`) shows
the user a difference and what it recorded, and nothing else; it is still a real
user-visible step that acceptance evidence has to be able to cover. What each
route owes — cards, cash anchor, opening memory, change surface, verdict axes —
is therefore declared once in `ROUTE_CONTRACTS` and read by `verify_rows`, so a
route either carries an obligation or it does not, and a lane with no card is
never left with fabricating one as its only way to pass.

The trace lives inside the protected state directory, the same trust boundary
as the canonical ledger: never committed and never published. The root resolves
exactly like the engine CLIs (`--state-root` > `TRADE_COACH_HOME` >
~/.trade-coach), so one `export TRADE_COACH_HOME=...` routes every tool in the
lifecycle into the same root (#269). Question-presentation rows are additionally restricted to mode,
surface source, and an opaque digest so cross-client evidence cannot copy the
private question surface into the trace.

`rule_choice_presented` additionally carries machine-checked grounding
fidelity (#293): whether the engine's candidate `grounding` text, if any, was
shown to the user verbatim. The comparison is computed once from a transient
`--grounding-check-file` (raw candidate groundings plus the exact presented
text) and only the boolean result and a sha256 hash of the grounding text are
persisted; the raw strings never reach the trace. See `_grounding_fidelity`.
"""

from __future__ import annotations

import argparse
import hashlib
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
# An adapter is the single runtime route selected by the host.  It is not the
# host name and must never be guessed from one (for example, "Codex" does not
# prove an AppBridge widget is installed or usable in this task).
ADAPTERS = ("plain_text", "native_options", "validated_widget")
ADAPTER_REQUIREMENTS = {
    "plain_text": ({"plain_text"}, {"markdown_inline"}),
    "native_options": ({"plain_text", "native_options"}, {"markdown_inline"}),
    "validated_widget": (
        {"plain_text", "native_options"},
        {"markdown_inline", "widget"},
    ),
}
STAGES = ("preview", "final")
MEMORY_KINDS = ("prior_commitment", "prior_skip", "exit_reason", "due_revisit")
WEEKLY_OPENERS = ("prior_commitment", "prior_skip")
CASH_OUTCOMES = ("found_in_source", "asked_user", "skipped")
# The two moments a card-free lane becomes visible to the user (#523). A book
# refresh (`flows/book-refresh.md`) narrates the engine's own difference and
# then reports what it recorded; those are the surfaces this trace can prove
# reached a human, in place of a card it never renders.
CHANGE_KINDS = ("diff", "result")
EVENT_KINDS = (
    "question_presented",
    "answers_received",
    "artifact_generated",
    "card_presented",
    "change_presented",
    "rule_choice_presented",
    "memory_presented",
    "widget_attempt_failed",
    "cash_anchor_checked",
    "findings_recorded",
    "owner_verdict",
)
# Everything a card delivery is made of. A route that renders no card must show
# none of these, so the list is named once rather than restated per check.
CARD_LIFECYCLE_EVENTS = (
    "artifact_generated",
    "card_presented",
    "widget_attempt_failed",
    # the rule choice is offered after the preview card and only there, so it
    # belongs to the card lifecycle rather than to questions in general
    "rule_choice_presented",
)
PASS_FAIL = ("pass", "fail")
PASS_FAIL_OR_NA = ("pass", "fail", "not_applicable")
ONLY_NOT_APPLICABLE = ("not_applicable",)
# Ordered for message stability; `question_specificity`/`answer_fit` are not
# here because they are gated by what the trace shows (a validated dynamic
# surface), not by which route it declared.
VERDICT_AXES = ("controls", "card", "memory", "change")

# --- What each route owes -----------------------------------------------------
#
# Every obligation below used to sit beside the check that enforced it, one
# `if route == ...` at a time: `CASH_ANCHOR_ROUTES` for the #357 pre-flight, a
# `route == "weekly_review"` block for the opening memory, an ungated
# `for stage in STAGES` loop for the cards, and a second weekly branch inside
# the owner gate. #523 would have made a fifth, because a book refresh renders
# no card by design and that loop could therefore only ever fail it — leaving
# fabricated card events as the one way to pass, which is precisely the
# dishonesty this tool exists to prevent.
#
# So the obligations are declared once, here, and `verify_rows` reads them.
# Adding a route means adding a row, not adding a branch.
#
#   cards       True  — exactly one `artifact_generated` and one
#                       `card_presented` per stage, each card after its
#                       artifact, final after preview.
#               False — the card lifecycle must be ABSENT. Not merely
#                       unrequired: a card-free route that only stopped
#                       *demanding* cards would accept a receipt claiming a
#                       delivery that could not have happened, which is the
#                       same hole one level down.
#   cash_anchor the #357 pre-flight, on the routes that read a trade history.
#               A declared positions snapshot states `cash` inline in its own
#               envelope, and `test_drive` persists no accounting anchor at
#               all, so neither owes one.
#   opener      memory kinds, exactly one of which must precede the first
#               surface. `()` for a route that carries no opening memory.
#   change      True  — at least one visible change surface: the narrated diff
#                       or recorded result (`change_presented`), or the
#                       confirmation question when one was raised. A refresh
#                       receipt holding only a start row and a verdict has
#                       proven nothing; the change surface is what makes it
#                       evidence.
#               False — `change_presented` must be absent, for the same reason
#                       `cards: False` forbids rather than exempts.
#   verdict     legal values per owner-verdict axis. An axis this map omits
#               must not appear on the route's verdict at all — a card route
#               claiming a `change` judgment, or a refresh claiming a card one,
#               is judging a surface it never had.
#   must_pass   axes that must carry their affirmative value (see `_affirmative`)
#               before `--require-owner-verdict` accepts the run.
ROUTE_CONTRACTS = {
    "first_review": {
        "cards": True, "cash_anchor": True, "opener": (), "change": False,
        "verdict": {"controls": PASS_FAIL, "card": PASS_FAIL, "memory": PASS_FAIL_OR_NA},
        "must_pass": ("controls", "card"),
    },
    "weekly_review": {
        "cards": True, "cash_anchor": True, "opener": WEEKLY_OPENERS, "change": False,
        "verdict": {"controls": PASS_FAIL, "card": PASS_FAIL, "memory": PASS_FAIL_OR_NA},
        # memory continuity is the entire reason this route exists, so it may
        # not be waived as inapplicable the way a first review legitimately can.
        "must_pass": ("controls", "card", "memory"),
    },
    "snapshot_review": {
        "cards": True, "cash_anchor": False, "opener": (), "change": False,
        "verdict": {"controls": PASS_FAIL, "card": PASS_FAIL, "memory": PASS_FAIL_OR_NA},
        "must_pass": ("controls", "card"),
    },
    "test_drive": {
        "cards": True, "cash_anchor": False, "opener": (), "change": False,
        "verdict": {"controls": PASS_FAIL, "card": PASS_FAIL, "memory": PASS_FAIL_OR_NA},
        "must_pass": ("controls", "card"),
    },
    # The book-refresh lane (#523). It records new facts about the book; it is
    # not a review, produces no card, and creates no session — so the evidence
    # it can honestly carry is the change it showed the user.
    #
    # The verdict still judges what the user actually saw, which is the whole
    # point of asking for one:
    #   change  — the new axis, and the load-bearing one: did what the receipt
    #             showed match what actually happened to the book.
    #   card    — pinned to `not_applicable`. Stating "no card was owed" is a
    #             positive claim the gate can check; leaving the axis free would
    #             let a lane with no card record a passing card verdict.
    #   controls — narrowed by the trace itself (`_verdict_scales`), because a
    #             refresh that raised nothing has no control to judge and a
    #             refresh that raised something must not call it inapplicable.
    #   memory  — left open. The diff is computed against the recorded book, so
    #             "did it remember my book" is a real judgment here, but it is
    #             already visible through `change` and is not gated twice.
    "refresh": {
        "cards": False, "cash_anchor": False, "opener": (), "change": True,
        "verdict": {"controls": PASS_FAIL_OR_NA, "card": ONLY_NOT_APPLICABLE,
                    "memory": PASS_FAIL_OR_NA, "change": PASS_FAIL},
        "must_pass": ("controls", "card", "change"),
    },
}
ROUTES = tuple(ROUTE_CONTRACTS)
# An unrecognized route is already an error; fall back to the strictest
# card-producing contract so every remaining check still runs against it rather
# than silently exempting the trace from all of them.
UNKNOWN_ROUTE_CONTRACT = ROUTE_CONTRACTS["snapshot_review"]
SURFACE_DIGEST = re.compile(r"^[a-f0-9]{64}$")
# Where a miss went. #417 gave converted misses a home; what it did not give
# them was a moment that asks. A QA run's last act is archiving, and nothing at
# that moment asked "what did you find, and where did it go?" — so the loop was
# left to whoever remembered, which is the failure eval-design.md had already
# recorded once. These are the only two honest answers, plus declaring none.
FINDING_DISPOSITIONS = (
    # converted on the spot into a replayable episode; the id is resolved below
    re.compile(r"^episode:(EP-\d{3})$"),
    # recorded but not replayable, tied to the issue that owns it and saying why
    re.compile(r"^not-episodable:(#\d+):(.{10,})$", re.S),
)
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MIN_OWNER_TRACE_SPAN_SECONDS = 3


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


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


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
    question_modes = list(dict.fromkeys(["plain_text", *args.question_mode]))
    card_modes = list(dict.fromkeys(["markdown_inline", *args.card_mode]))
    errors = _adapter_capability_errors(args.adapter, set(question_modes), set(card_modes))
    if errors:
        raise ReceiptError("; ".join(errors))
    row = {
        "version": VERSION,
        "event": "capabilities_declared",
        "session_id": args.session_id,
        "client": args.client,
        "route": args.route,
        "adapter": args.adapter,
        "question_modes": question_modes,
        "card_modes": card_modes,
    }
    _append(path, row)


def _grounding_fidelity(path: str | None) -> dict:
    """Compute privacy-safe grounding-fidelity evidence for `rule_choice_presented`.

    `path` points at a transient, never-persisted JSON file (analogous to
    `--question-surfaces`, kept outside the repository and outside the trace)
    shaped as::

        {"candidates": [{"id": "candidate_0", "grounding": "<engine text>"},
                         {"id": "candidate_1"}],
         "presented_text": "<the exact text shown to the user>"}

    Each candidate's `grounding` is the engine-authored
    `card_plan.candidate_rules[].grounding` string when the candidate carries
    one, omitted otherwise. This function performs the verbatim-containment
    comparison itself — a machine fact, not a self-reported claim — and
    returns only a boolean-plus-hash summary. The raw grounding text and the
    raw presented text are read once, used for this one comparison, and
    discarded: neither this function's return value nor the caller may persist
    them (#293's privacy constraint: receipts may be archived or posted
    publicly, so no candidate grounding, however short, may enter the trace).
    """
    if not path:
        raise ReceiptError("rule_choice_presented requires --grounding-check-file")
    try:
        raw = pathlib.Path(os.path.expanduser(path)).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReceiptError(f"cannot read --grounding-check-file: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReceiptError(f"--grounding-check-file is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("--grounding-check-file must contain a JSON object")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ReceiptError("--grounding-check-file candidates must be a list")
    presented_text = payload.get("presented_text")
    if not isinstance(presented_text, str) or not presented_text.strip():
        raise ReceiptError("--grounding-check-file presented_text must be a non-empty string")

    groundings = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ReceiptError("--grounding-check-file candidates entries must be objects")
        grounding = candidate.get("grounding")
        if grounding is None:
            continue
        if not isinstance(grounding, str) or not grounding.strip():
            raise ReceiptError(
                "--grounding-check-file candidate grounding must be a non-empty string when present"
            )
        groundings.append(grounding)

    if not groundings:
        # Nothing the engine asked to be cited verbatim: trivially satisfied.
        # This cannot detect a candidate that had no grounding but was
        # presented with a fabricated one (there is no engine text to compare
        # against) — that half of #293 remains an accepted limitation.
        return {"grounding_expected": False, "grounding_verbatim": True}

    verbatim = all(grounding in presented_text for grounding in groundings)
    digest = hashlib.sha256("\n".join(groundings).encode("utf-8")).hexdigest()
    return {"grounding_expected": True, "grounding_hash": digest, "grounding_verbatim": verbatim}


def _episode_bank() -> "set[str] | None":
    """Episode ids in this checkout, or None when the bank is not reachable.

    Returns None rather than an empty set for a vendored skill directory with no
    `evals/` beside it: "the bank is not here" and "the bank is empty" are
    different facts, and only the second is a reason to reject an id.

    Reads each episode's declared `id`, not its filename. The first cut derived
    the id by splitting the filename, so a file named `EP-123-anything.json`
    containing invalid JSON counted as a converted episode — hand-mirroring a
    fact the file already states, and admitting a claim with nothing behind it
    (external review, 2026-07-27). A file that does not parse, or declares no
    id, contributes nothing rather than contributing its name.
    """
    bank = pathlib.Path(__file__).resolve().parents[3] / "evals" / "episodes"
    if not bank.is_dir():
        return None
    ids = set()
    for path in sorted(bank.glob("EP-*.json")):
        try:
            declared = json.loads(path.read_text(encoding="utf-8")).get("id")
        except (OSError, ValueError, AttributeError):
            # ValueError rather than json.JSONDecodeError: `read_text` raises
            # UnicodeDecodeError on a non-UTF-8 file, which is a ValueError but
            # not a JSONDecodeError, so the narrower list let it escape and
            # crashed the tool instead of failing the claim closed (external
            # review round 2). A file this loop cannot read backs no claim; it
            # must never be able to stop the gate from running.
            continue
        if isinstance(declared, str) and declared:
            ids.add(declared)
    return ids


def _check_findings_row(row: dict, index: int) -> list:
    """Validate one persisted `findings_recorded` row. Shared by write and verify.

    `_findings` gates what this tool writes; this gates what `verify` will accept
    from a file on disk. Both matter, and they must not disagree: the first cut
    resolved `episode:EP-NNN` against the bank only on the write path, so a
    hand-authored or post-edited receipt claiming a conversion that never
    happened verified clean — at exactly the gate archiving depends on (external
    review, 2026-07-27).
    """
    errors = []
    # Same discipline as `question_presented` and `answers_received`: this event
    # is the one a maintainer is most tempted to paste miss text into, and it
    # sits inside the state directory's trust boundary. Anything beyond the
    # dispositions is content this trace is not allowed to carry.
    extra = sorted(set(row) - {"version", "event", "session_id", "ts", "findings"})
    if extra:
        errors.append(f"row {index} findings_recorded contains unsupported fields: "
                      f"{', '.join(extra)}")
    recorded = row.get("findings")
    if recorded is None:
        return errors + ["findings_recorded must carry a findings list, empty for none observed"]
    if not isinstance(recorded, list):
        return errors + ["findings_recorded findings must be a list of dispositions"]
    known = _episode_bank()
    for finding in recorded:
        for pattern in FINDING_DISPOSITIONS:
            match = pattern.match(str(finding))
            if match:
                break
        else:
            errors.append(f"unrecognized finding disposition {finding!r}")
            continue
        if str(finding).startswith("episode:") and known is not None and match.group(1) not in known:
            errors.append(f"{match.group(1)} is recorded as converted but is not in "
                          f"evals/episodes/ — a conversion claim with nothing behind it")
    return errors


def _findings(findings: list, none_declared: bool) -> dict:
    """Every miss this run produced, and where it went.

    Fails closed in both directions: an omitted disposition is not silence, and
    "we found nothing" has to be said rather than left to be inferred from an
    absent event. A run that observed nothing is a real and common outcome; a
    run that observed something and never said where it went is the one this
    exists to make impossible.
    """
    if none_declared and findings:
        raise ReceiptError("--no-findings and --finding are mutually exclusive")
    if not none_declared and not findings:
        raise ReceiptError(
            "findings_recorded requires --finding (repeatable) or an explicit "
            "--no-findings; an omitted disposition is not a declaration of none")
    # One reader for what a disposition is: the same function `verify` applies to
    # a row read back from disk, so the write path and the gate cannot disagree.
    problems = _check_findings_row({"findings": list(findings)}, 0)
    if problems:
        raise ReceiptError(
            "; ".join(problems)
            + " — use 'episode:EP-NNN' for a miss converted into a replayable "
              "episode (convert it first), or 'not-episodable:#NN:<why it cannot "
              "be replayed>'")
    return {"findings": list(findings)}


def _event_row(args: argparse.Namespace, declaration: dict) -> dict:
    row = {"version": VERSION, "event": args.event, "session_id": declaration["session_id"]}
    if args.event in ("question_presented", "rule_choice_presented"):
        if not args.mode:
            raise ReceiptError(f"{args.event} requires --mode")
        if args.mode not in QUESTION_MODES:
            raise ReceiptError(f"question mode must be one of {QUESTION_MODES}")
        row["mode"] = args.mode
    if args.event == "rule_choice_presented":
        row.update(_grounding_fidelity(args.grounding_check_file))
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
    if args.event == "change_presented":
        if args.change_kind not in CHANGE_KINDS:
            raise ReceiptError(f"change_presented requires --change-kind in {CHANGE_KINDS}")
        row["change_kind"] = args.change_kind
    if args.event == "memory_presented":
        if not args.memory_kind:
            raise ReceiptError("memory_presented requires --memory-kind")
        row["memory_kind"] = args.memory_kind
    if args.event == "cash_anchor_checked":
        if args.cash_outcome not in CASH_OUTCOMES:
            raise ReceiptError(f"cash_anchor_checked requires --cash-outcome in {CASH_OUTCOMES}")
        row["cash_outcome"] = args.cash_outcome
    if args.event == "findings_recorded":
        row.update(_findings(args.finding, args.no_findings))
    if args.event == "owner_verdict":
        verdicts = {"controls": args.controls, "card": args.card, "memory": args.memory}
        if any(value is None for value in verdicts.values()):
            raise ReceiptError("owner_verdict requires --controls, --card, and --memory")
        row.update(verdicts)
        # Deliberately not route-gated here. No cross-row or per-route rule in
        # this tool is enforced at write time — card ordering, the cash-anchor
        # pre-flight and the weekly opener are all `verify`'s job — and route
        # obligations live in exactly one reader (ROUTE_CONTRACTS, read by
        # verify_rows). Teaching the append path a second, partial copy of them
        # is how the two would eventually disagree.
        if args.change is not None:
            row["change"] = args.change
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


def _contract(rows: list[dict]) -> dict:
    """The obligations the trace's declared route carries."""
    route = rows[0].get("route") if rows else None
    return ROUTE_CONTRACTS.get(route, UNKNOWN_ROUTE_CONTRACT)


def _change_surfaces(rows: list[dict]) -> list[int]:
    """Rows where a card-free route showed the user what changed.

    Two kinds count, and #523's design names both: `change_presented` (the
    narrated diff, or the report of what was recorded) and the confirmation
    question a raised item produces — in `flows/book-refresh.md` that question
    *is* the difference, stated for the items only the user can settle.
    """
    return sorted(_positions(rows, "change_presented")
                  + _positions(rows, "question_presented"))


def _first_surface(rows: list[dict]) -> int:
    """Index of the first thing the user saw, whatever kind of surface it was.

    The anti-backfill checks (cash anchor, weekly opener) are all of the form
    "this had to happen before anything was shown", so they share one reading
    of what "shown" means rather than each enumerating surfaces it knows about.
    """
    return min(_positions(rows, "question_presented")
               + _positions(rows, "card_presented")
               + _positions(rows, "change_presented")
               or [len(rows)])


def _verdict_scales(contract: dict, rows: list[dict]) -> dict:
    """The route's verdict scales, narrowed by what the trace actually shows.

    Exactly one narrowing exists, and it is the card-free lane's own honesty
    problem. `controls` asks whether the user could answer through real
    controls; a book refresh that raised no confirmation never showed one, so
    "pass" there would be a judgment about nothing — the same fabrication as a
    card verdict on a run that rendered no card. A refresh that *did* raise one
    must judge it rather than calling it inapplicable. Both directions are
    mechanically decidable from the trace, so neither is left to discipline.

    Routes whose declared `controls` scale has no `not_applicable` (every
    card-producing route) are untouched by this.
    """
    scales = dict(contract["verdict"])
    if "not_applicable" in scales.get("controls", ()):
        scales["controls"] = PASS_FAIL if _positions(rows, "question_presented") \
            else ONLY_NOT_APPLICABLE
    return scales


def _affirmative(scale: tuple) -> str:
    """The value that means "this axis is fine" on a given scale.

    `pass` wherever the axis can pass; otherwise the single value the scale
    allows — which is how `card: not_applicable` satisfies the owner gate on a
    lane that owes no card without that lane needing its own branch.
    """
    return "pass" if "pass" in scale else scale[0]


def timing_integrity(rows: list[dict]) -> dict:
    """Assess timestamp plausibility without invalidating legacy receipts.

    Only a structurally complete owner-verdict trace is eligible for the
    assessment, and what "complete" means is the route's own contract: the
    preview-and-final card walk on a card-producing route, the visible change
    surface on a card-free one. Reading the same table `verify_rows` reads is
    what keeps a lane that cannot render cards from being permanently
    ``not_assessed`` — and therefore permanently unarchivable under
    ``--require-timing-integrity`` (#523). Missing timestamps remain compatible
    and machine-visible as ``not_assessed``. A fully timestamped trace is
    ``suspect`` when row timestamps reverse or the complete walk was recorded in
    less than ``MIN_OWNER_TRACE_SPAN_SECONDS``. These checks do not assert what
    happened in the host; they prevent implausible self-attestation from being
    cited directly as owner-live ground truth.
    """
    contract = _contract(rows)
    verdicts = _positions(rows, "owner_verdict")
    if contract["cards"]:
        anchors = (
            _positions(rows, "artifact_generated", stage="preview"),
            _positions(rows, "card_presented", stage="preview"),
            _positions(rows, "artifact_generated", stage="final"),
            _positions(rows, "card_presented", stage="final"),
        )
        complete = all(len(positions) == 1 for positions in anchors)
        reason = "complete owner-verdict multi-stage trace required"
    else:
        complete = bool(_change_surfaces(rows))
        reason = "owner-verdict trace with a visible change surface required"
    complete = complete and len(verdicts) == 1
    base = {
        "status": "not_assessed",
        "owner_live_eligible": None,
        "minimum_span_seconds": MIN_OWNER_TRACE_SPAN_SECONDS,
        "span_seconds": None,
        "findings": [],
    }
    if not complete:
        base["reason"] = reason
        return base

    if not all(_valid_ts(row.get("ts")) for row in rows):
        base["reason"] = "complete valid timestamps required; legacy receipt remains compatible"
        return base

    timestamps = [_parse_ts(row["ts"]) for row in rows]
    findings = []
    for index in range(1, len(timestamps)):
        if timestamps[index] < timestamps[index - 1]:
            findings.append({
                "code": "timestamp_reversal",
                "row": index + 1,
                "previous_row": index,
            })

    span_seconds = int((timestamps[verdicts[0]] - timestamps[0]).total_seconds())
    if 0 <= span_seconds < MIN_OWNER_TRACE_SPAN_SECONDS:
        findings.append({
            "code": "implausible_one_burst_backfill",
            "span_seconds": span_seconds,
            "minimum_span_seconds": MIN_OWNER_TRACE_SPAN_SECONDS,
        })

    return {
        **base,
        "status": "suspect" if findings else "credible",
        "owner_live_eligible": not findings,
        "span_seconds": span_seconds,
        "findings": findings,
    }


def _adapter_capability_errors(
    adapter: object, question_modes: set[str], card_modes: set[str]
) -> list[str]:
    """Return errors for a newly declared adapter without naming any host.

    Missing ``adapter`` is intentionally accepted by ``verify_rows`` so traces
    created before this contract extension stay readable.  New CLI traces always
    include one, and therefore make unknown-host text fallback observable.
    """
    if adapter not in ADAPTERS:
        return [f"unsupported adapter: {adapter!r}"]
    required_questions, required_cards = ADAPTER_REQUIREMENTS[adapter]
    errors = []
    if not required_questions <= question_modes:
        errors.append(f"adapter {adapter!r} requires question modes {sorted(required_questions)}")
    if not required_cards <= card_modes:
        errors.append(f"adapter {adapter!r} requires card modes {sorted(required_cards)}")
    if adapter == "plain_text" and (
        question_modes != required_questions or card_modes != required_cards
    ):
        errors.append("plain_text adapter may declare only the universal text and Markdown fallbacks")
    return errors


def verify_rows(rows: list[dict], require_owner_verdict: bool = False,
                require_findings: bool = False) -> list[str]:
    """Return deterministic presentation-contract errors; an empty list means pass.

    Scope is deliberately narrow: prove that whatever the route renders — an
    engine card, or the change a card-free lane showed instead — actually
    reached the user, in order, plus the weekly opening memory. Which of those a
    trace owes is read from `ROUTE_CONTRACTS`, never branched on here. Answer and
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
    contract = _contract(rows)
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
        if event == "change_presented":
            # Same discipline as answers_received and findings_recorded: this
            # row states that a surface appeared, never what it said. The diff
            # it narrates holds tickers and share counts, so a free-text field
            # here would put position content inside the trace.
            extra = sorted(set(row) - {"version", "event", "session_id", "ts", "change_kind"})
            if extra:
                errors.append(
                    f"row {index} change_presented contains unsupported fields: {', '.join(extra)}"
                )
            if row.get("change_kind") not in CHANGE_KINDS:
                errors.append(f"row {index} has unsupported change kind {row.get('change_kind')!r}")
        if event == "cash_anchor_checked" and row.get("cash_outcome") not in CASH_OUTCOMES:
            errors.append(f"row {index} has unsupported cash outcome {row.get('cash_outcome')!r}")
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
            allowed = {
                "version", "event", "session_id", "ts", "mode",
                "grounding_expected", "grounding_hash", "grounding_verbatim",
            }
            extra = sorted(set(row) - allowed)
            if extra:
                errors.append(
                    f"row {index} rule_choice_presented contains unsupported fields: {', '.join(extra)}"
                )
            # #293: presenting candidate rules without proving the engine's
            # grounding text was shown verbatim is not a passing QA run. Fail
            # closed whether the evidence is absent (legacy/unrecorded) or
            # present but false (paraphrased) — there is no grandfathered
            # legacy state for this field, unlike optional `ts`.
            expected = row.get("grounding_expected")
            if not isinstance(expected, bool):
                errors.append(
                    f"row {index} rule_choice_presented is missing grounding-fidelity evidence "
                    "(grounding_expected must be recorded as true or false)"
                )
            if row.get("grounding_verbatim") is not True:
                errors.append(
                    f"row {index} rule_choice_presented did not prove its candidates' grounding "
                    "was presented verbatim (grounding_verbatim must be true)"
                )
            digest = row.get("grounding_hash")
            if expected is True:
                if not isinstance(digest, str) or not SURFACE_DIGEST.fullmatch(digest):
                    errors.append(
                        f"row {index} rule_choice_presented has an invalid or missing grounding_hash"
                    )
            elif expected is False and digest is not None:
                errors.append(
                    f"row {index} rule_choice_presented has a grounding_hash but no grounding was expected"
                )

    question_modes = set(declaration.get("question_modes") or [])
    card_modes = set(declaration.get("card_modes") or [])
    if not question_modes <= set(QUESTION_MODES) or "plain_text" not in question_modes:
        errors.append("capabilities must declare plain_text as the universal question fallback")
    if not card_modes <= set(CARD_MODES) or "markdown_inline" not in card_modes:
        errors.append("capabilities must declare markdown_inline as the universal card fallback")
    # Adapter was introduced after the first trace version.  Keep old receipts
    # verifiable, but require every new declaration written by this CLI to bind
    # its actual capability set to one host-agnostic adapter profile.
    if "adapter" in declaration:
        errors.extend(_adapter_capability_errors(declaration["adapter"], question_modes, card_modes))
    for row in rows:
        if row.get("event") == "question_presented" and row.get("mode") not in question_modes:
            errors.append(f"question used undeclared mode {row.get('mode')!r}")
        if row.get("event") == "rule_choice_presented" and row.get("mode") not in question_modes:
            errors.append(f"rule choice used undeclared mode {row.get('mode')!r}")

    preview_card = _positions(rows, "card_presented", stage="preview")
    final_card = _positions(rows, "card_presented", stage="final")
    final_artifact = _positions(rows, "artifact_generated", stage="final")
    first_surface = _first_surface(rows)

    # Presentation is the whole point: each stage must show its engine artifact
    # actually reached the user inline, in order.
    if contract["cards"]:
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
        if preview_card and final_card and preview_card[0] >= final_card[0]:
            errors.append("final card presentation must follow the preview card")
        if preview_card and final_artifact and final_artifact[0] <= preview_card[0]:
            errors.append("final artifact was generated before the preview card was presented")
    else:
        # Forbidden, not merely unrequired. Exempting a card-free route from the
        # sequence above and stopping there would let its receipt claim a card
        # delivery that structurally cannot have happened — the same fabricated
        # evidence, recorded voluntarily instead of under duress.
        claimed = [index for index, row in enumerate(rows, 1)
                   if row.get("event") in CARD_LIFECYCLE_EVENTS]
        if claimed:
            errors.append(
                f"{route} renders no card, so rows {claimed} record a card delivery "
                "that cannot have happened")

    # The card-free counterpart: what the user saw instead. A trace holding only
    # a declaration and a verdict has proven nothing about the lane it claims to
    # have walked.
    change_surfaces = _change_surfaces(rows) if contract["change"] else []
    if contract["change"]:
        if not change_surfaces:
            errors.append(
                f"{route} must show at least one visible change surface — the narrated "
                "diff or recorded result (change_presented), or the confirmation "
                "question when one was raised")
    elif _positions(rows, "change_presented"):
        errors.append(f"{route} presents cards, not a change surface; change_presented "
                      "does not belong on this route")

    # #357: the cash anchor is resolved before the first surface (first_review:
    # before `prepare` runs; weekly_review: after the cadence-tier gate, and a
    # light-tier session never calls this tool at all), so this event is
    # retrospective evidence the check happened — it cannot be skipped by
    # forgetting, the way plain data-contract.md prose could.
    if contract["cash_anchor"]:
        checks = _positions(rows, "cash_anchor_checked")
        if len(checks) != 1:
            errors.append(f"{route} must record exactly one cash_anchor_checked event")
        elif checks[0] >= first_surface:
            errors.append("cash_anchor_checked was recorded after the first question or card")

    if Counter(row.get("event") for row in rows)["widget_attempt_failed"] and "widget" not in card_modes:
        errors.append("widget failure was recorded without declared widget capability")

    # A route that carries an opening memory must prove it (the committed prior
    # rule, or an explicit prior skip) was actually surfaced before the first
    # card. Today only weekly_review does; the contract says so, not this check.
    if contract["opener"]:
        openers = [
            index
            for index, row in enumerate(rows)
            if row.get("event") == "memory_presented"
            and row.get("memory_kind") in contract["opener"]
        ]
        if len(openers) != 1:
            errors.append(f"{route} must present exactly one prior commitment or skip opener")
        elif openers[0] >= first_surface:
            errors.append("weekly opening memory was presented after the first question or card")

    # Gate 7 (#417): a run that produced misses and never said where they went
    # is the loop that produced eighteen receipts and zero replayable assets.
    findings = _positions(rows, "findings_recorded")
    if len(findings) > 1:
        errors.append("findings_recorded may appear at most once")
    for index in findings:
        errors.extend(_check_findings_row(rows[index], index + 1))
    if require_findings and len(findings) != 1:
        errors.append(
            "a QA run must record exactly one findings_recorded event — every miss "
            "disposed of as a converted episode or an issue it cannot be replayed "
            "from, and 'none observed' declared rather than omitted")

    verdicts = _positions(rows, "owner_verdict")
    if len(verdicts) > 1:
        errors.append("owner_verdict may appear at most once")
    scales = _verdict_scales(contract, rows)
    if verdicts:
        verdict = rows[verdicts[0]]
        for axis in VERDICT_AXES:
            allowed = scales.get(axis)
            if allowed is None:
                # An axis this route has no surface for. Judging it would be
                # judging something the user was never shown.
                if verdict.get(axis) is not None:
                    errors.append(f"owner {axis} verdict does not apply on a {route} trace")
            elif verdict.get(axis) not in allowed:
                errors.append(f"owner {axis} verdict must be {' or '.join(allowed)} "
                              f"on a {route} trace")
        question_verdicts = (verdict.get("question_specificity"), verdict.get("answer_fit"))
        if any(value is not None for value in question_verdicts):
            if any(value not in {"pass", "fail"} for value in question_verdicts):
                errors.append("owner question specificity and answer fit verdicts must both be pass or fail")
        # The verdict is the last act, so it must follow whatever the route
        # actually put in front of the user — the final card, or the change
        # surface on a lane that renders none. A verdict recorded before it
        # judged nothing.
        if final_card and verdicts[0] <= final_card[0]:
            errors.append("owner_verdict must follow the final card presentation")
        if change_surfaces and verdicts[0] <= max(change_surfaces):
            errors.append("owner_verdict must follow the change surface it judges")
        # Both documents say findings are recorded before the verdict, and the
        # first cut enforced no ordering at all — the docs described a discipline
        # the tool did not hold anyone to (external review, 2026-07-27). The
        # ordering is the same anti-backfill rule the card sequence already has:
        # the verdict is the last act, so a disposition recorded after it was
        # reconstructed rather than observed.
        if findings and findings[0] > verdicts[0]:
            errors.append("findings_recorded must precede the owner verdict — a "
                          "disposition recorded after the run was judged is a backfill")
    if require_owner_verdict:
        # Which axes must be affirmative is the route's contract, not a chain of
        # route tests: `card=pass` on a card route and `card=not_applicable` on
        # a card-free one are the same statement — "the thing this route renders
        # was fine" — and `_affirmative` is where that equivalence lives.
        wanted = {axis: _affirmative(scales[axis]) for axis in contract["must_pass"]}
        unmet = [f"{axis}={value}" for axis, value in wanted.items()
                 if verdicts and rows[verdicts[0]].get(axis) != value]
        if len(verdicts) != 1:
            errors.append("manual verification requires exactly one owner_verdict")
        elif unmet:
            errors.append(f"manual verification of a {route} trace requires "
                          f"{', '.join(unmet)} in the owner verdict")
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
    errors = verify_rows(rows, require_owner_verdict=args.require_owner_verdict,
                         require_findings=args.require_findings)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
    integrity = timing_integrity(rows)
    if integrity["status"] == "suspect":
        for finding in integrity["findings"]:
            print(f"WARN: timing_integrity {finding['code']}", file=sys.stderr)
    if args.require_timing_integrity and integrity["status"] != "credible":
        print(
            f"FAIL: receipt timing integrity is {integrity['status']} and cannot be used as "
            "owner_live UX ground truth; audit or re-run the walkthrough",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(_compact_json({
        "status": "pass",
        "events": len(rows),
        "session_id": rows[0]["session_id"],
        "timing_integrity": integrity,
    }))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--session-id", required=True)
        sub.add_argument("--state-root", default=None,
                         help="protected state directory (default: $TRADE_COACH_HOME, else ~/.trade-coach)")

    start = subparsers.add_parser("start", help="declare one resolved host adapter and its capabilities")
    add_common(start)
    start.add_argument("--client", required=True)
    start.add_argument("--route", required=True, choices=ROUTES)
    start.add_argument("--adapter", choices=ADAPTERS, default="plain_text",
                       help="resolved runtime route; default is the universal unknown-host fallback")
    start.add_argument("--question-mode", action="append", choices=QUESTION_MODES, default=[],
                       help="extra capability beyond the universal plain_text fallback")
    start.add_argument("--card-mode", action="append", choices=CARD_MODES, default=[],
                       help="extra capability beyond the universal markdown_inline fallback")
    start.set_defaults(handler=start_receipt)

    event = subparsers.add_parser("event", help="append a presentation fact after the user-visible action")
    add_common(event)
    event.add_argument("--event", required=True, choices=EVENT_KINDS)
    event.add_argument("--mode")
    event.add_argument(
        "--grounding-check-file",
        help="rule_choice_presented only: path to a transient JSON file pairing each "
             "presented candidate's engine grounding with the exact presented text "
             "(never persisted; see _grounding_fidelity)",
    )
    event.add_argument("--surface-source", choices=SURFACE_SOURCES)
    event.add_argument("--surface-digest")
    event.add_argument("--stage", choices=STAGES)
    event.add_argument("--artifact-path")
    event.add_argument("--memory-kind", choices=MEMORY_KINDS)
    event.add_argument("--change-kind", choices=CHANGE_KINDS,
                       help="change_presented only: the narrated engine diff, or "
                            "the report of what the refresh recorded")
    event.add_argument("--cash-outcome", choices=CASH_OUTCOMES)
    event.add_argument("--controls", choices=PASS_FAIL_OR_NA)
    event.add_argument("--card", choices=PASS_FAIL_OR_NA)
    event.add_argument("--memory", choices=PASS_FAIL_OR_NA)
    event.add_argument("--change", choices=PASS_FAIL,
                       help="owner_verdict on a card-free route: did what the "
                            "receipt showed match what happened to the book")
    event.add_argument("--question-specificity", choices=("pass", "fail"))
    event.add_argument("--answer-fit", choices=("pass", "fail"))
    event.add_argument("--finding", action="append", default=[],
                       metavar="episode:EP-NNN | not-episodable:#NN:<why>",
                       help="where one miss from this run went; repeatable")
    event.add_argument("--no-findings", action="store_true",
                       help="declare that this run observed no miss (not the same "
                            "as omitting the event)")
    event.set_defaults(handler=record_event)

    verify = subparsers.add_parser("verify", help="confirm each card actually reached the user")
    add_common(verify)
    verify.add_argument("--require-owner-verdict", action="store_true")
    verify.add_argument("--require-findings", action="store_true",
                        help="gate 7: fail unless the run disposed of its misses")
    verify.add_argument(
        "--require-timing-integrity",
        action="store_true",
        help="fail when a complete owner-verdict trace has reversed or implausibly burst timestamps",
    )
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
