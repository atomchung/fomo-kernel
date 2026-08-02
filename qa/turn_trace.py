"""Ordered visible-turn trace and honest attempt accounting for the #718 walk.

Two failures this module exists to make impossible.

**A multi-turn experience that cannot be inspected afterwards.** Keeping only
the last surface loses the thing a maintainer needs: what the user saw, in what
order, which reply moved the route to its next state, and whether a given
message was the decision result, a question, a limitation or an abnormal
process surface. ``TurnTrace`` records all of that in order and chains the
surface digests so a reordering cannot go unnoticed.

**A run that disappears from the denominator.** A campaign and a route run are
counted the moment they start, and an unsettled route run reads
``harness_incomplete`` -- the pessimistic state -- until a terminal product
verdict replaces it. Strict pass verification can therefore only move a run
between buckets; it can never remove one.

Two things this module deliberately does not hold. It records only what a user
could see: no hidden reasoning, no model scratchpad, no raw tool log -- machine
diagnostics are a separate artifact the walk writes elsewhere. And its
classification answers a delivery question ("is this surface a decision result
at all, or the system narrating its own repair work"), never an answer-quality
question; that remains the #705 semantic judge's, and nothing here calls it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

VISIBLE_ROLES = ("assistant", "user")
# #718's taxonomy for what a visible message *is*. The first four are the
# owner's categories for a product-visible surface; ``correction`` is the user
# reply that moves the route to its next state.
MESSAGE_TYPES = ("decision_result", "question", "limitation", "process_error", "correction")
# ``harness`` is the honest provenance for text this QA lane wrote itself -- an
# abnormal process surface is not a product surface and must never be counted
# as one. ``fixture`` is committed fictional text; ``host_capture`` is reserved
# for a real host that byte-captured what it showed the user.
SURFACE_PROVENANCE = ("host_capture", "fixture", "harness")
PRODUCT_PROVENANCE = ("host_capture", "fixture")
VERDICTS = ("product_pass", "product_fail")

# A bounded lexical detector for the one failure mode #718 names: a final
# assistant surface that narrates implementation diagnosis or recovery instead
# of delivering the user's decision result. It is a floor, not a judge -- the
# structural signal below (`delivers_decision_result`) is the primary one, and
# a surface that evades every marker while still failing to deliver a decision
# is caught there.
#
# English only, and deliberately so rather than by oversight: `qa/` is held to
# English by `docs/language-policy.md`, and the allowlist that file's gate
# offers is keyed on a line prefix, which for a list of quoted needles would
# be `"` -- an exception wide enough to retire the gate for this whole module.
# The product does ship zh-CN and zh-TW, so this lane is blind on those
# locales until a detector that can hold its needles elsewhere exists. The walk
# runs the route with `--language en` for the same reason.
PROCESS_NARRATION_MARKERS = (
    "root cause",
    "root causes",
    "will fix",
    "we'll fix",
    "i'll fix",
    "fixing it",
    "reprocess",
    "reprocessing",
    "re-processing",
    "investigating",
    "looking into it",
    "debugging",
    "known issue",
    "next iteration",
    "on the next pass",
    "rerun the pipeline",
)


class TraceError(ValueError):
    """Raised when a trace or ledger is asked to record something dishonest."""


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Turn:
    """One user-visible surface, exactly as it was delivered."""

    index: int
    role: str
    message_type: str
    provenance: str
    route_state: str
    recorded_at: str
    text: str

    @property
    def surface_sha256(self):
        return _sha256(self.text)

    def public(self):
        """Counts, digests and labels only -- never the surface text itself."""
        return {"index": self.index, "role": self.role, "message_type": self.message_type,
                "provenance": self.provenance, "route_state": self.route_state,
                "recorded_at": self.recorded_at, "chars": len(self.text),
                "surface_sha256": self.surface_sha256}

    def raw(self):
        return {**self.public(), "text": self.text}


class TurnTrace:
    """The ordered record of one route run's visible surfaces."""

    def __init__(self):
        self._setup = []
        self._turns = []

    # -- recording ---------------------------------------------------------
    def record_setup(self, *, field_name, text):
        """A pre-trajectory synthetic input. Retained, but not a visible turn.

        The scenario's ``reason`` and ``why_now`` are policy the QA lane hands
        the route, not something the product showed anyone. Counting them as
        visible turns would inflate every turn count in the report.
        """
        if not isinstance(text, str) or not text:
            raise TraceError("a setup entry needs text")
        self._setup.append({"stage": "setup", "field": field_name, "recorded_at": _now(),
                            "chars": len(text), "surface_sha256": _sha256(text), "text": text})

    def record(self, *, role, message_type, text, provenance, route_state, recorded_at=None):
        if role not in VISIBLE_ROLES:
            raise TraceError(f"unknown turn role {role!r}")
        if message_type not in MESSAGE_TYPES:
            raise TraceError(f"unknown message type {message_type!r}")
        if provenance not in SURFACE_PROVENANCE:
            raise TraceError(f"unknown surface provenance {provenance!r}")
        if not isinstance(route_state, str) or not route_state:
            raise TraceError("a visible turn needs a route state label")
        if not isinstance(text, str) or not text.strip():
            raise TraceError("a visible turn needs surface text")
        turn = Turn(len(self._turns) + 1, role, message_type, provenance, route_state,
                    recorded_at or _now(), text)
        self._turns.append(turn)
        return turn

    # -- derived counts ----------------------------------------------------
    @property
    def turns(self):
        return tuple(self._turns)

    @property
    def turn_count(self):
        return len(self._turns)

    @property
    def correction_turn_count(self):
        return sum(1 for turn in self._turns if turn.message_type == "correction")

    @property
    def ordered_surface_digest(self):
        """A digest chained over the turns in the order they were recorded.

        The order is carried by the chaining itself -- feeding the same
        surfaces in a different sequence produces a different digest -- so no
        position field is mixed in; one would be redundant and untestable.
        Anything that sorts, dedupes or re-groups the turns before this loop
        therefore changes the digest, which is the point. The timestamp is
        deliberately outside the chain, so replaying the same trajectory
        reproduces it.
        """
        chain = hashlib.sha256()
        for turn in self._turns:
            chain.update(f"{turn.role}|{turn.message_type}|{turn.surface_sha256}\n".encode("utf-8"))
        return chain.hexdigest()

    # -- serialization -----------------------------------------------------
    def public_index(self):
        """The view safe for a public receipt: no surface text anywhere."""
        return [turn.public() for turn in self._turns]

    def raw_records(self):
        """The local protected view: full text, setup entries first."""
        return {"setup": list(self._setup), "turns": [turn.raw() for turn in self._turns]}

    def write_raw(self, path):
        rows = list(self._setup) + [turn.raw() for turn in self._turns]
        lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def classify_surface(text, *, instrument, resolutions):
    """Is this assistant surface a decision result, or implementation narration?

    ``instrument`` and ``resolutions`` are the route's own facts -- the premise
    ticker the engine evaluated and the resolution states the scenario policy
    declares. Nothing here reads the fixture's expectations, so a fixture
    cannot declare itself passing.
    """
    if not isinstance(text, str):
        raise TraceError("a surface must be text")
    lowered = text.lower()
    narration = tuple(marker for marker in PROCESS_NARRATION_MARKERS if marker in lowered)
    names_instrument = bool(instrument) and instrument.lower() in lowered
    states_resolution = tuple(state for state in resolutions if state.lower() in lowered)
    delivers = names_instrument and bool(states_resolution)
    if narration:
        reason = "process_narration"
    elif not delivers:
        reason = "no_decision_result_delivered"
    else:
        reason = "decision_result_delivered"
    return {"delivers_decision_result": delivers,
            "names_instrument": names_instrument,
            "states_resolution": list(states_resolution),
            "process_narration_markers": list(narration),
            "verdict": "product_fail" if (narration or not delivers) else "product_pass",
            "reason": reason}


@dataclass
class RunLedger:
    """Attempt accounting that a later failure can only re-bucket, never erase."""

    campaigns_started: int = 0
    route_runs_started: int = 0
    route_runs_terminal: int = 0
    product_passes: int = 0
    product_failures: int = 0
    harness_incomplete: int = 0
    stop_reason: str | None = None

    def start_campaign(self):
        self.campaigns_started += 1

    def start_route(self):
        """Count the attempt and assume the worst until a verdict replaces it."""
        self.route_runs_started += 1
        self.harness_incomplete += 1
        self.stop_reason = "in_progress"

    def settle(self, verdict, *, stop_reason):
        if verdict not in VERDICTS:
            raise TraceError(f"unknown product verdict {verdict!r}")
        if self.harness_incomplete < 1:
            raise TraceError("no unsettled route run to settle")
        self.harness_incomplete -= 1
        self.route_runs_terminal += 1
        if verdict == "product_pass":
            self.product_passes += 1
        else:
            self.product_failures += 1
        self.stop_reason = stop_reason

    def stop_incomplete(self, stop_reason):
        """Leave the run in the denominator, and say why it never settled."""
        self.stop_reason = stop_reason

    def counters(self):
        self.check()
        return {"campaigns_started": self.campaigns_started,
                "route_runs_started": self.route_runs_started,
                "route_runs_terminal": self.route_runs_terminal,
                "product_passes": self.product_passes,
                "product_failures": self.product_failures,
                "harness_incomplete": self.harness_incomplete,
                "stop_reason": self.stop_reason}

    def check(self):
        settled = self.product_passes + self.product_failures
        if settled + self.harness_incomplete != self.route_runs_started:
            raise TraceError("route runs started must equal passes + failures + incomplete")
        if settled != self.route_runs_terminal:
            raise TraceError("terminal route runs must equal passes + failures")
        if self.route_runs_started and not self.campaigns_started:
            raise TraceError("a route run cannot start outside a campaign")


def build_report(*, ledger, trace, base):
    """One report shape for every outcome, so no outcome needs a special path."""
    counters = dict(ledger.counters())
    workflow = "incomplete" if ledger.harness_incomplete else (
        "pass" if ledger.product_passes else "fail")
    report = dict(base)
    report.update(counters)
    report.update({"workflow": workflow,
                   "turn_count": trace.turn_count,
                   "correction_turn_count": trace.correction_turn_count,
                   "ordered_surface_digest": trace.ordered_surface_digest,
                   "turn_index": trace.public_index()})
    return report
