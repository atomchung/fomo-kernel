#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tool-neutral orchestration CLI for one-card trade reviews.

Lifecycle:

    prepare  -> agent asks the returned question_queue
    preview  -> validates answers/theses/narrative and renders a pending card
    finalize -> user chooses one commitment; commits an atomic session bundle
    resume   -> returns pending state after interruption

All commands emit JSON on stdout.  Human-readable diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import answer_provenance
import book_refresh
import card_renderer
import conditions
import consequence
import evaluation_challenge
import horizon
import instruments
import ledger
import market_data
import weekly_market_read
import price_feed
import problems
import portfolio_basis
import question_surface
import revisit
import session
import snapshot_adapter
import splits as split_policy   # #550 一份分割規則;別名讓 `splits=` 參數不遮蔽模組
import symbols
import thesis
import trade_recap
import verdicts


HERE = pathlib.Path(__file__).resolve().parent
TRADE_RECAP = HERE / "trade_recap.py"
MOCK_CSV = HERE.parent / "mock" / "mock_trades.csv"
DIM_METRIC = {
    "exit_discipline": "exit_severity",
    "position_sizing": "max_pos_pct",
    "diversification": "top3_pct",
    "holding_period": "hold_severity",
    "averaging_down": "avgdown_count",
}
# Question density is a product contract per route (#291): a first review earns
# up to five questions when each extra answer creates durable decision-relevant
# information, weekly reviews stay one to three, a snapshot asks none. The min is
# a floor the min-backfill defends by information gain, never questionnaire
# volume. An unknown route falls back to the weekly band (defensive).
QUESTION_POLICY = {
    "first_review": {"min": 3, "max": 5},
    "weekly_review": {"min": 1, "max": 3},
    "snapshot_review": {"min": 0, "max": 0},
    "test_drive": {"min": 1, "max": 3},
}
HORIZON_MARKER_LIMIT = 2
RULE_BREACH_LIMIT = 2
INITIAL_THESIS_LIMIT = 2  # at most two first-review entry-thesis captures per review
INITIAL_THESIS_CHOICES = {"planned_entry", "momentum_follow", "external_call",
                          "no_clear_thesis", "skip"}
EXIT_DECISIONS = {"price_target", "thesis_broken", "swap", "anxiety", "other", "skip"}
RULE_BREACH_CHOICES = {"keep_tracking", "revise_rule", "exception"}
HEADLINE_MOTIVE_CHOICES = {"deliberate_plan", "emotional_reaction", "external_constraint"}
# #303: exit-consistency classifies the same motive axis as a headline motive
# (deliberate / emotional / external), so it reuses the choice contract and the
# `_generic_options` labels — only the durable event stream is kept separate.
EXIT_CONSISTENCY_CHOICES = HEADLINE_MOTIVE_CHOICES
# #434: how many condition slots one review asks the agent to look up. The plan
# used to send every stored slot raw, so a user with thirty standing conditions
# got a review whose per-turn context grew without bound and whose lookup work
# had no ceiling. The bound is on the *plan*, never on the record: the rest stay
# in conditions.jsonl, return next review (oldest-last-checked first), and the
# card says how many were left — a bounded surface must say what it dropped.
CONDITION_LOOKUP_CAP = 8
# At most one crossing question per review. A week that trips four conditions is
# a week with one conversation to have, not four; the rest state their facts and
# come back. (Owner ruling, #412.)
CONDITION_CROSSING_LIMIT = 1
CONDITION_CROSSING_NUMERIC_CHOICES = {"confirmed", "overridden", "skip"}
CONDITION_CROSSING_EVENT_CHOICES = {"yes", "no", "skip"}
CONDITION_BASIS_CHOICES = {"revise_threshold", "revise_metric", "keep", "skip"}
# `consider`'s own vocabulary (Layer 2, docs/decision-fomo-kernel-shape.md §3-4).
# CONSIDER_DECISIONS is what --resolve may record; "open" (schemas/trade-
# evaluation.schema.json's default) is a row's starting state, never something
# a caller resolves *to*. Kept as one tuple so the argparse choices and the
# schema enum cannot silently drift apart (tests/test_consider.py checks it).
CONSIDER_DECISIONS = ("acted", "declined", "modified")
# Layer 3 provenance vocabulary for an optional --agent-case claim
# (docs/decision-fomo-kernel-shape.md §3: "mark each claim as your record says,
# public fact, or my judgment"). engine_fact is that first category under a
# name that reads correctly next to consequence.py's own output; public_fact
# and agent_judgment are the doc's own words.
AGENT_CASE_PROVENANCE = ("engine_fact", "public_fact", "agent_judgment")
# #429's rule one layer up: an evaluation nobody reconciles is the same dead-
# store shape that issue names for a question nobody reads. The bound is the
# same discipline CONDITION_LOOKUP_CAP states just above -- the Review Plan is
# re-sent as agent context on every later turn, so a user who never resolves
# old evaluations must not grow it without limit. Oldest `created` first;
# _evaluation_reconciliation's summary discloses whatever the cap holds
# back, the same "a bounded surface must say what it dropped" rule.
EVALUATION_RECONCILE_CAP = 8
# The optional DecisionContext an agent may hand `consider` (#479 Wave A,
# schemas/decision-context.schema.json). Both bounds refuse rather than
# truncate: a shortened reason, or an evidence list quietly cut to fit, is a
# statement the user never made, and this envelope's whole purpose is to hold
# their exact words. The count is five because the issue's own scene is a
# person naming "a few" things they are looking at in the moment they decide,
# not an engine-maintained rotation over their whole history — the two caps
# above are eight for that different job. Unbounded is not an option: this
# envelope is re-read into agent context on every later turn that surfaces the
# evaluation, which is the #429 shape one layer up.
EVALUATION_EVIDENCE_REFS_CAP = 5
EVALUATION_CONTEXT_TEXT_MAX = 1000
EVALUATION_EVIDENCE_REF_MAX = 500

# #674: when `consider` refuses a whole book for a genuinely non-recoverable
# reason (structural corruption, an integrity warning this route cannot
# scope, or no usable holding left -- see `_consider_rows`), the refusal may
# still hand the agent a bounded packet of already-computed facts to frame a
# decision from, drawn from the last finalized review's frozen state
# (`last_state.json`) rather than recomputed. These two tuples are the single
# declaration of which keys of that frozen state count as "usable" for this
# purpose, so the refusal payload and evals/run_episodes.py's grounding check
# read the same list instead of two hand-copied ones drifting apart.
# `max_pos_pct`/`ai_pct`/`max_sector_pct`/`top3_pct` are the whole-book
# concentration readings `consequence.py` also judges rule collisions
# against; `max_pos_ticker` is the one non-numeric reading that names them.
CONSIDER_REFUSAL_CONCENTRATION_KEYS = ("max_pos_pct", "max_pos_ticker", "ai_pct",
                                       "max_sector_pct", "top3_pct")
# The subset of a frozen `commitment` (`_resolve_commitment`'s own shape) that
# is a fact about what the user already committed to, not an engine
# computation that could go stale between reviews: the rule's own words,
# which metric it watches, the value frozen when it was chosen, and the
# direction that counts as a breach.
CONSIDER_REFUSAL_COMMITMENT_KEYS = ("rule", "metric_key", "metric_value", "goal")


class ReviewError(ValueError):
    """A refusal or invalid input one of this CLI's commands raised.

    ``payload_extra``, when supplied, is merged into ``main()``'s emitted
    error JSON beside ``status``/``error`` (#674) -- a small, deterministic,
    engine-computed addition the raiser attaches, never prose the agent has
    to parse back out of the message. Every existing raise site is
    unaffected: the keyword defaults to nothing, and the positional message
    still reaches ``ValueError`` exactly as before, so ``str(exc)`` -- and
    every existing test asserting on it -- is unchanged.
    """

    def __init__(self, message, *, payload_extra=None):
        super().__init__(message)
        self.payload_extra = payload_extra


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


_ENGINE_VERSION = None


def _engine_version(repo_root=None):
    """Provenance stamp: which build produced this artifact.

    Pure metadata — it never enters narrative, numeric facts, or the public
    card. Resolution is fail-safe at every step so a missing git checkout or
    VERSION file can never break a review:

      1. a committed ``VERSION`` file (what a future release will ship);
      2. the git short SHA plus a dirty flag;
      3. ``unknown``.

    ``dirty`` reflects tracked-file state only — staged or unstaged changes
    to a file git already knows about — never an untracked one. This is
    `git describe --dirty`'s own convention, and it exists here because an
    untracked file is not a reliable signal either way: an environment that
    cannot reach the account's global excludes file (the QA runbook's HOME
    replacement, for one — #747) surfaces an otherwise locally-ignored file
    as untracked, and a real repo routinely collects scratch files no one
    ever ran ``git add`` on. Neither describes the tracked tree diverging
    from ``HEAD``, which is the only claim this flag makes. An actual edit —
    modifying a tracked file, or staging a new one — still flips it.

    Cached per process so repeated prepare/preview/finalize calls agree.
    ``repo_root`` is test-only: every production call site omits it and gets
    this skill's own checkout, below.
    """
    global _ENGINE_VERSION
    if _ENGINE_VERSION is not None:
        return _ENGINE_VERSION
    # Named repo_root, not root: this is the skill's own checkout (to read its
    # build-provenance VERSION file / git SHA), never the user's coach data
    # root. A bare `root` here would be indistinguishable, by name alone, from
    # every other function's coach-root variable to a naive source scan —
    # tests/test_coach_data_cli.py's DATA_FILES-registry completeness check
    # (#452) walks exactly that convention, so the distinct name is load-bearing
    # for that check's precision, not a style preference.
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(repo_root, "VERSION"), encoding="utf-8") as handle:
            tag = handle.read().strip()
        if tag:
            _ENGINE_VERSION = {"id": tag, "source": "file"}
            return _ENGINE_VERSION
    except (OSError, UnicodeDecodeError):
        pass
    try:
        head = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if head.returncode == 0 and head.stdout.strip():
            # --untracked-files=no: see the docstring's `dirty` paragraph.
            # Tracked modifications (staged or not) still show here; only an
            # untracked file's `??` line is excluded.
            status = subprocess.run(
                ["git", "-C", repo_root, "status", "--porcelain", "--untracked-files=no"],
                capture_output=True, text=True, timeout=2,
            )
            _ENGINE_VERSION = {
                "id": head.stdout.strip()[:12],
                "source": "git",
                "dirty": bool(status.stdout.strip()),
            }
            return _ENGINE_VERSION
    except (OSError, subprocess.SubprocessError):
        pass
    _ENGINE_VERSION = {"id": "unknown", "source": "unknown"}
    return _ENGINE_VERSION


def _load_json(path, label):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as exc:
        raise ReviewError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must be a JSON object")
    return value


def _jsonl(path):
    return thesis.read_jsonl(path)


def _fingerprint(paths, language, route, prepared=None, nonce="", prices=None, cash=None,
                 condition_checks=None, prices_unavailable=None):
    # nonce participates so an explicit --session-nonce starts a genuinely new
    # session instead of being swallowed by same-content pending resume.
    h = hashlib.sha256()
    h.update(f"{language}\0{route}\0{nonce}\0".encode())
    if prepared:
        h.update(session.canonical(prepared).encode())
    if prices:
        # A supplied price envelope changes every valuation number, so it has to
        # change the fingerprint too. Without this, rerunning prepare after a
        # degraded run would resume the priceless pending session and silently
        # discard the prices the agent just retrieved (#289).
        h.update(b"prices\0" + session.canonical(prices).encode())
    if cash:
        # Same #289 class, for the cash anchor (#357/#369): the weekly flow may
        # legitimately learn the anchor only after a cash-less prepare resolved
        # the cadence tier, so `prepare --cash` must open a fresh session
        # instead of resuming the cash-less pending one and silently discarding
        # the balance the user just confirmed.
        try:
            canonical_cash = session.canonical(json.loads(cash))
        except (TypeError, ValueError):
            canonical_cash = str(cash)
        h.update(b"cash\0" + canonical_cash.encode())
    if condition_checks:
        # Same #289 class again (#412): the first prepare publishes what is due
        # and the second carries the results back. Without this the second pass
        # would resume the check-less pending session, and the crossing question
        # the lookups just earned would never be asked.
        h.update(b"condition_checks\0" + session.canonical(condition_checks).encode())
    if prices_unavailable:
        # Same #289 class once more (#623). The declaration that recovery was
        # attempted and the sources publish nothing arrives on a *second*
        # prepare, after the first one reported the gap. Without this the rerun
        # would resume the undeclared pending session and the declaration —
        # the only thing separating a skipped step from an honest dead end —
        # would be silently dropped.
        h.update(b"prices_unavailable\0" + str(prices_unavailable).encode())
    for path in paths or []:
        p = os.path.abspath(path)
        h.update(p.encode() + b"\0")
        with open(p, "rb") as f:
            while True:
                block = f.read(1024 * 1024)
                if not block:
                    break
                h.update(block)
    return h.hexdigest()


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


# One controlled message for every #501 pre-append refusal (owner decision A1).
# The user's action is identical whichever input moved — rerun prepare — so the
# distinction stays in the tests, not in copy that would have to name
# state_version / receipt / ValuationFrame to be precise.
BASIS_CHANGED_MESSAGE = (
    "Your portfolio or source file changed while this review was being prepared. "
    "Nothing from this attempt was saved. Start the review again so every number "
    "uses one consistent snapshot."
)


def _candidate_receipt(paths, frozen_dir=None):
    """Freeze ordered source bytes once, before engine work (#501)."""
    files, frozen_paths = [], []
    for index, original_path in enumerate(paths):
        original_path = os.path.abspath(original_path)
        try:
            with open(original_path, "rb") as handle:
                payload = handle.read()
        except OSError as exc:
            raise ReviewError(f"cannot freeze candidate input {original_path}: {exc}") from exc
        if frozen_dir is not None:
            basename = os.path.basename(original_path) or f"candidate-{index}.csv"
            ordinal_dir = os.path.join(frozen_dir, f"{index:03d}")
            frozen_path = os.path.join(ordinal_dir, basename)
            try:
                os.mkdir(ordinal_dir)
                with open(frozen_path, "xb") as handle:
                    handle.write(payload)
            except OSError as exc:
                raise ReviewError(f"cannot write private candidate snapshot: {exc}") from exc
            frozen_paths.append(frozen_path)
        files.append({"original_path": original_path, "sha256": _sha256_bytes(payload),
                      "bytes_n": len(payload)})
    receipt = {"contract_version": "frozen-candidates-v1", "files": files}
    receipt["digest"] = _sha256_bytes(session.canonical(receipt).encode("utf-8"))
    return receipt, frozen_paths


def _read_live_ledger(root):
    """Strictly read one locked live-ledger snapshot and its byte receipt."""
    ledger_path = os.path.join(root, "ledger.jsonl")
    try:
        events, skipped = ledger.load_ledger(ledger_path)
        if os.path.exists(ledger_path):
            with open(ledger_path, "rb") as handle:
                payload = handle.read()
        else:
            payload = b""
    except (OSError, ledger.LedgerIntegrityError) as exc:
        raise ReviewError(str(exc)) from exc
    return events, {"sha256": _sha256_bytes(payload), "bytes_n": len(payload),
                    "events_digest": _sha256_bytes(session.canonical(events).encode("utf-8")),
                    "skipped_lines": skipped}, payload


def _freeze_transaction_inputs(root, paths, frozen_dir):
    """Take the short pre-engine candidate + ledger snapshot, never a network lock."""
    candidate, frozen_paths = _candidate_receipt(paths, frozen_dir)
    with session.projection_transaction(root):
        events, ledger_receipt, ledger_bytes = _read_live_ledger(root)
    ledger_snapshot = os.path.join(frozen_dir, "ledger.jsonl")
    try:
        with open(ledger_snapshot, "xb") as handle:
            handle.write(ledger_bytes)
    except OSError as exc:
        # An unguarded write here would leave a raw traceback on the agent's
        # stdout instead of one controlled error line.
        raise ReviewError(f"cannot write the private review snapshot: {exc}") from exc
    return {"candidate": candidate, "frozen_paths": frozen_paths,
            "ledger_events": events, "ledger_receipt": ledger_receipt,
            "ledger_snapshot": ledger_snapshot}


def _parse_frozen_candidates(frozen_paths):
    batches, skipped_non_trade, skipped_future = [], 0, 0
    for path in frozen_paths:
        trades, non_trade, future = ledger.trades_from_csv(path)
        batches.append(trades)
        skipped_non_trade += non_trade
        skipped_future += future
    if skipped_future:
        raise ReviewError("ledger ingestion rejected normalized input before writing: "
                          f"{skipped_future} future-dated row(s)")
    return batches, skipped_non_trade, skipped_future


def _frame_identity(frame):
    return _sha256_bytes(session.canonical(frame).encode("utf-8"))


def _basis_reference(frame_as_of, book_as_of):
    """The 'now' a virtual basis is measured against.

    `reference_as_of` answers "how stale is this book", so it may never precede
    the book itself. The price frame's `as_of` is the last close, and a book is
    routinely newer than that — a weekend review, or any snapshot/trade dated
    after the last bar. Feeding the frame date in directly made that ordinary
    case raise instead of report zero staleness (#501 review).
    """
    return max(value for value in (frame_as_of, book_as_of) if value)


def _virtual_valuation_frame(events, source_frame, *, splits):
    """Restrict the engine frame to the exact frozen virtual current book.

    Returns the narrowed frame and the book's own effective date, which the
    caller needs to measure staleness from without inverting the two.

    ``splits`` is the same frozen map the caller hands the canonical query it
    runs immediately afterwards, and the two must be the same map: this
    provisional query decides which tickers get a price, and the canonical one
    then validates that frame against holdings it derived on its own basis.
    Split-blind here and split-aware there, a position whose raw quantities
    reach zero across a split is dropped from the frame and does not even
    appear as ``missing_price`` — so the canonical query raises "prices do not
    exactly partition holdings" and ``prepare`` refuses the very book
    ``derive_holdings``, ``refresh`` and ``consider`` read correctly (#558
    follow-up).

    Required, and keyword-only, deliberately. A default would let the next
    caller omit it silently, and the reader net in ``tests/test_split_basis.py``
    could not report that: the net reads *this function's* call to
    ``query_current_book``, which forwards the map faithfully whatever the
    caller passed. A caller with genuinely nothing to supply passes ``None``
    and says so.
    """
    if not isinstance(source_frame, dict):
        raise ReviewError("this review has no usable price basis; rerun prepare")
    try:
        portfolio_basis.validate_valuation_frame(source_frame)
        # No reference and no manifest: this query exists only to learn which
        # tickers the virtual book holds, so it must not also adjudicate
        # freshness against a price date it was never measured against.
        provisional = portfolio_basis.query_current_book(events, skipped_lines=0, splits=splits)
    except portfolio_basis.PortfolioBasisError as exc:
        raise ReviewError(f"this review's price basis could not be read: {exc}") from exc
    if provisional is None:
        raise ReviewError("your current holdings could not be derived from this input")
    holdings = provisional.current_book["holdings"]
    aggregate = source_frame["aggregate_currency"]
    prices = {ticker: row for ticker, row in source_frame["prices"].items()
              if ticker in holdings and row.get("currency") == holdings[ticker]["currency"]}
    needed_fx = set(row["currency"] for row in holdings.values()) - {aggregate}
    fx = {currency: row for currency, row in source_frame["fx_to_aggregate"].items()
          if currency == aggregate or currency in needed_fx}
    missing_price = [{"ticker": ticker, "currency": row["currency"]}
                     for ticker, row in sorted(holdings.items()) if ticker not in prices]
    missing_fx = sorted(needed_fx - set(fx))
    if missing_price and missing_fx:
        reason = "missing_price_and_fx"
    elif missing_price:
        reason = "missing_price"
    elif missing_fx:
        reason = "missing_fx"
    else:
        reason = None
    frame = {"contract_version": portfolio_basis.VALUATION_FRAME_VERSION,
             "as_of": source_frame["as_of"], "aggregate_currency": aggregate,
             "prices": prices, "fx_to_aggregate": fx,
             "coverage": {"missing_price": missing_price, "missing_fx": missing_fx},
             "usable": reason is None, "reason": reason}
    try:
        portfolio_basis.validate_valuation_frame(frame, positions=holdings)
    except portfolio_basis.PortfolioBasisError as exc:
        raise ReviewError(f"this review's price basis does not match your holdings: {exc}") from exc
    return frame, provisional.as_of


def _virtual_review_basis(inputs, batches, state):
    try:
        overlay = ledger.virtualize(inputs["ledger_events"], batches)
        frame, book_as_of = _virtual_valuation_frame(overlay["events"], state.get("valuation_frame"),
                                                     splits=state.get("splits"))
        basis = portfolio_basis.query_current_book(
            overlay["events"], valuation_manifest=frame,
            reference_as_of=_basis_reference(frame.get("as_of"), book_as_of),
            skipped_lines=inputs["ledger_receipt"]["skipped_lines"],
            splits=state.get("splits"))
    except (ValueError, portfolio_basis.PortfolioBasisError) as exc:
        raise ReviewError(f"your book could not be read from this input: {exc}") from exc
    if basis is None:
        raise ReviewError("your book could not be derived from this input")
    receipt = {"contract_version": "virtual-review-basis-v1",
               "candidate_receipt": inputs["candidate"],
               "ledger_receipt": inputs["ledger_receipt"],
               "valuation_frame": frame, "valuation_frame_identity": _frame_identity(frame),
               "basis_state_version": basis.state_version,
               "basis": basis.to_dict()}
    return overlay, receipt


def _carried_declaration(root, snapshot_path):
    """The declaration with each still-held cycle start carried from the record.

    #539: a declaration says what is held, never since when, so an ordinary
    second declaration would give every continuously held position a cycle id
    minted from its own ``as_of`` — and the user's thesis, written against the
    previous id, would be asked for again as if they had never answered.

    Returns ``None`` for a root with no recorded book, which is onboarding: there
    is nothing to carry, and ``prepare`` loads the file the way it always has.

    Deliberately here, before ``snapshot_adapter.prepare`` builds anything: the
    stamped envelope is what ``_anchor`` writes to the ledger *and* what
    ``_state_positions`` derives the plan's cycle ids from, so the questions the
    user is asked and the book that is recorded cannot disagree about identity.
    Doing it at finalize instead would fix the ledger after the review had
    already re-asked. The refresh lane calls the same primitive from
    ``build_adoption`` (#536): one implementation, and the only path through
    which engine-assigned provenance enters a book.
    """
    events, _skipped = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
    if not events or ledger.latest_anchor(events) is None:
        return None
    snapshot, _anchor = book_refresh.carry_recorded_starts(
        snapshot_adapter.normalize_book(snapshot_path)[0], events,
        splits=_recorded_splits(root))
    return snapshot


def _validate_initial_snapshot_root(root, anchor):
    """Resolve how a runtime snapshot declaration may enter this coach root.

    Empty history or an exact idempotent replay returns ``None`` (initial
    onboarding path, unchanged).  A different declaration against a root that
    has recorded a book returns the reconciliation the Review Plan freezes: the
    narrow fact diff plus the ``reconciled``/``adjusted`` verdict from
    ``ledger.snapshot_reconciliation``.  Everything else stays fail-closed — a
    declaration older than the current book, and history that recorded no book
    at all (unknown ledger event types or an unrepaired ledger projection) are
    rejected.

    Whether a returned verdict may be *reviewed* is a separate question, and a
    different function answers it: see ``_refuse_if_the_book_must_catch_up``
    (#530).  Deliberately not folded in here — the reconciliation status is not
    the criterion, and this function's job is to state what the ledger says.

    This is the prepare-time UX layer only; the authoritative check reruns
    under the root projection lock at finalize
    (``session._assert_initial_snapshot_boundary``) and fails closed when the
    frozen diff no longer matches the ledger.  Both layers share
    ``session.scan_initial_snapshot_conflicts`` and
    ``ledger.snapshot_reconciliation`` so their verdicts cannot drift.
    """
    if not isinstance(anchor, dict):
        return None
    if not session.scan_initial_snapshot_conflicts(root, anchor):
        return None
    # #462: a corrupt/unknown row anywhere in the ledger must block the
    # reconciliation this function computes, not just the conflict-detection
    # scan_initial_snapshot_conflicts already did above — that scan only
    # answers "is there history to reconcile against," never "is the history
    # this reconciliation reads complete."
    try:
        events, _skipped = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
        reconciliation = ledger.snapshot_reconciliation(events, anchor)
    except ValueError as exc:
        raise ReviewError(str(exc)) from exc
    if reconciliation is None:
        raise ReviewError(session.INITIAL_SNAPSHOT_CONFLICT)
    return reconciliation


def _refuse_if_the_book_must_catch_up(root, snapshot_path):
    """Route a declaration the book-update lane would have to ask about (#530).

    Owner ruling 2026-07-28: updating the recorded book is a mandatory node, not
    an alternative to reviewing it.  New facts arriving means the book is
    brought up to date first, and only a completed book is discussed.

    *Which* differences make that true is deliberately not decided here.  This
    asks ``book_refresh``, the lane that owns the question, and refuses exactly
    when it would raise one — today a vanished position, an appeared one, or a
    large move on a large holding, and whatever that lane adds next without a
    line changing here.  Two reasons that is the criterion rather than "the
    declaration does not reconcile clean".  It is the only set that can lose
    information: a position in the record and absent from the view leaves the
    book with no exit record, no closed cycle and no revisit, and a position in
    the view and absent from the record can only be given a provenance at the
    moment it appears (#531), while every other difference merely replaces one
    number with another and the next reconciliation still sees the
    truth.  And ``avg_cost`` differs for legitimate reasons on almost every real
    book — ``derive_holdings`` keeps a moving average while brokers may use FIFO
    or amortize fees, past a half-cent tolerance — so refusing on the status
    would block users over an arithmetic convention while doing nothing about
    the case that actually costs them their exits.

    Asking the other lane also means one definition, not two: whatever refresh
    starts or stops asking about moves this refusal with it, and the two can
    never disagree about the same declaration.  ``plan_refresh`` writes nothing
    at all (that is what its phase 1 is for), so this is a pure read, and it
    runs on the same ``normalize_book`` output the user's next ``refresh`` call
    will produce from the same file.
    """
    try:
        events, _skipped = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
        snapshot, anchor = snapshot_adapter.normalize_book(snapshot_path)
        recorded_anchor = ledger.latest_anchor(events or [], declared_only=True) or {}
        _refuse_an_unprovable_split_basis(root, recorded_anchor.get("as_of"))
        plan = book_refresh.plan_refresh(events, snapshot, anchor,
                                         splits=_recorded_splits(root))
    except (ValueError, snapshot_adapter.SnapshotError) as exc:
        # Fail closed. Everything here already passed its own validator on the
        # way in, so a refusal at this point is not a user input error to
        # explain away — it is the engine losing confidence in the comparison.
        raise ReviewError(str(exc)) from exc
    if plan.get("pending_confirmations"):
        raise ReviewError(book_refresh.NEEDS_BOOK_UPDATE)


def _refuse_an_unsettled_transaction_basis(card):
    """Refuse a transaction file whose own prices contradict its split basis (#582).

    The mirror of the refusal above, one input lane over. That one routes a
    *holdings view* carrying something only the user can settle into the lane
    that asks them (#530); this one has no such lane to route to, because a
    transaction file is exactly the input the user has handed over without a
    second account of their position, and `book_refresh` needs a declaration to
    compare against. Measured, not assumed: a user who does eventually supply
    one is protected by accident — derived comes out exactly `factor ×`
    declared, which `plan_refresh` raises as `large_change` — but only while
    that position clears `REFRESH_MAJOR_DELTA` *and* `REFRESH_CORE_WEIGHT`. The
    same defect on a small corner of the book returns `status: ready` with
    nothing pending and is adopted silently. This runs at ingest instead, on the
    file alone, at any position size, so the two complement rather than overlap.

    So the difference is stated here and the answer is asked for in the same
    breath, on the same terms #530 established: the engine names the specific
    observed fact, offers the readings it cannot choose between, and records
    nothing until a person settles it. The engine deliberately does not re-base
    anything either way. An adjusted export, a currency in the wrong unit and a
    strange fill all print the same disagreement, and `engine/splits.py` says
    why picking one from the numbers is the adjudication #416 forbids.

    Refusing rather than disclosing is the point. Every weight, concentration
    verdict, position-size number and rule on the card is measured against the
    share count this premise produces, so continuing *is* choosing the
    as-executed reading — silently, on a card full of numbers. The user gets to
    answer instead, and each answer has a real next step: re-export as executed,
    declare the ticker's real corporate actions in a price envelope, or fix the
    currency during normalization.
    """
    findings = ((card or {}).get("data_integrity") or {}).get("split_basis") or []
    if not findings:
        return
    lines = []
    for row in findings:
        evidence = row.get("rows") or [{}]
        first = evidence[0]
        lines.append(
            f"{row.get('ticker')}: {row.get('examined_n')} trade(s) dated before its "
            f"{', '.join(str(day) for day, _ratio in (row.get('splits') or []))} split "
            f"(cumulative factor {row.get('factor')}x) record prices this review reads as "
            f"{first.get('rebased_price')} against a market close of {first.get('market_close')} "
            f"on {first.get('close_date')} — off by that same factor, on every trade checked")
    raise ReviewError(
        "; ".join(lines) + ". This engine reads a transaction file as recording what executed, "
        "un-rebased, and multiplies each share count by the splits that came after it; a file "
        "that is already split-adjusted therefore has the split applied twice, which is ten "
        "times the real share count on a ten-for-one, with the cost preserved — so avg_cost, "
        "weight, concentration and every rule measured against them would be computed on a book "
        "that never existed. An unusual fill, a currency written in the wrong unit and an "
        "already-adjusted export all produce this same disagreement and the engine may not pick "
        "between them, so nothing has been recorded and no number has been changed. Ask the user "
        "which it is: 'the prices your file records for this ticker look like they are already "
        "split-adjusted — is that what your broker exports?' If yes, export as-executed "
        "transaction history and rerun prepare. If the corporate action is what is wrong for "
        "this holding, supply that ticker's real split events in a price envelope "
        "(references/price-feed.md) and rerun prepare --prices <path>. If the currency is wrong, "
        "correct it during normalization and rerun prepare.")


def _apply_snapshot_reconciliation(card, state, reconciliation):
    """Freeze the reconciliation into both engine artifacts, honesty included.

    The full fact diff lives in ``state.snapshot_reconciliation`` (and thereby
    in the Review Plan the user confirms).  The card carries a summary through
    the existing honesty-ledger and data-integrity channels only — disclosure
    stays an engine decision (#82), wording stays with the renderer copy and
    the agent-authored narrative sentence.
    """
    card = dict(card)
    state = dict(state)
    state["snapshot_reconciliation"] = reconciliation
    diff = reconciliation.get("diff") or {}
    positions = diff.get("positions") or []
    summary = {
        "status": reconciliation.get("status"),
        "as_of": reconciliation.get("as_of"),
        "against_as_of": (reconciliation.get("against") or {}).get("as_of"),
        "positions_changed": sorted({row["ticker"] for row in positions
                                     if row.get("kind") not in ("only_declared", "only_derived")}),
        "only_declared": sorted({row["ticker"] for row in positions
                                 if row.get("kind") == "only_declared"}),
        "only_derived": sorted({row["ticker"] for row in positions
                                if row.get("kind") == "only_derived"}),
        "cash_currencies": sorted({row["currency"] for row in diff.get("cash") or []}),
    }
    integrity = dict(card.get("data_integrity") or {})
    integrity["snapshot_reconciliation"] = summary
    card["data_integrity"] = integrity
    honesty = [row for row in card.get("honesty_ledger") or []
               if row.get("key") != "snapshot_reconciliation"]
    honesty.append({"key": "snapshot_reconciliation",
                    "status": summary["status"], "data": summary})
    card["honesty_ledger"] = honesty
    return card, state


def _pending_by_fingerprint(root, fingerprint):
    base = os.path.join(root, ".pending")
    if not os.path.isdir(base):
        return None
    for sid in sorted(os.listdir(base)):
        plan_path = os.path.join(base, sid, "plan.json")
        if not os.path.exists(plan_path):
            continue
        try:
            plan = session.read_json(plan_path)
        except (OSError, ValueError):
            continue
        if (plan.get("input") or {}).get("fingerprint") == fingerprint:
            return plan
    return None


def _has_history(root):
    # Canonical-bundle semantics, same as every other scanner (#215): a
    # finalized test drive in an explicit --root leaves a sessions/ directory,
    # and counting it flipped --route auto from first_review to weekly_review.
    if next(session.iter_canonical_bundles(root), None) is not None:
        return True
    return bool(_jsonl(os.path.join(root, "log.jsonl")))


def _completed_review_count(root, exclude_session_id=None):
    """Count completed local reviews without double-counting projections.

    Canonical persistent bundles are authoritative in v2, including when their
    legacy ``log.jsonl`` projection needs repair. Valid legacy log rows still
    count so pre-v2 history remains visible. Session ids deduplicate the same
    review across both stores; older rows without an id each represent one
    completed review. A matching current session id is excluded so an
    idempotent retry cannot present itself as a new return visit.
    """
    session_ids = set()
    for _name, bundle in session.iter_canonical_bundles(root):
        session_id = bundle.get("session_id")
        if session_id:
            session_ids.add(str(session_id))

    legacy_without_id = 0
    for row in _jsonl(os.path.join(root, "log.jsonl")):
        session_id = row.get("session_id")
        if session_id:
            session_ids.add(str(session_id))
        else:
            legacy_without_id += 1
    if exclude_session_id:
        session_ids.discard(str(exclude_session_id))
    return len(session_ids) + legacy_without_id


def _previous_state(root):
    path = os.path.join(root, "last_state.json")
    if not os.path.exists(path):
        return None
    try:
        return session.read_json(path)
    except (OSError, ValueError):
        return None


def _fallback_cash_anchor(root):
    """The last finalized review's own anchored cash balance, reshaped into
    the ``{currency, amount, as_of}`` (or per-currency list) form ``--cash``
    accepts, for a ``consider`` call that supplied no ``--cash`` of its own
    (#756).

    ``last_state.json``'s ``cash`` field never carries the original anchor's
    own ``as_of`` — only ``trade_recap.cash_position``'s aggregated result
    survives ``finalize``. But that result already has every cash flow up to
    the review's own ``date_end`` folded in, so restating it "as of
    ``date_end``" is exact, not an approximation: nothing this account has
    recorded moved that balance between the true (unstored) ``as_of`` and
    ``date_end``, because everything in between is already inside the sum —
    and ``consider``'s own ledger reconstruction cannot see anything past
    ``date_end`` that this state does not already know about either.

    Built per currency from ``by_currency`` rather than from the blended
    aggregate ``balance``, because the aggregate's own currency is whichever
    one ``fx`` happened to convert into (USD for a mixed-currency book) and
    is not safe to re-declare as a single-currency anchor.

    Returns ``None`` — "no fallback, behave exactly as before `--cash`
    existed" — when no review has ever been finalized here, when the last
    one's cash reading was never itself genuinely anchored
    (``source: csv_sum``/``partial``), or when a currency bucket was
    individually unreliable even though the account overall read reliable.
    Promoting an unreliable running sum into a synthetic anchor would
    misrepresent an approximation as a recorded fact — the opposite of this
    fix, and the same fail-closed rule AGENTS.md invariant 2 states everywhere
    else in this product.
    """
    state = _previous_state(root)
    if not isinstance(state, dict):
        return None
    cash = state.get("cash")
    if not isinstance(cash, dict) or not cash.get("reliable"):
        return None
    as_of = state.get("date_end")
    if not as_of:
        return None
    by_currency = cash.get("by_currency")
    if not isinstance(by_currency, dict) or not by_currency:
        return None
    anchors = [
        {"currency": currency, "amount": entry["balance"], "as_of": as_of}
        for currency, entry in sorted(by_currency.items())
        if isinstance(entry, dict) and entry.get("reliable") and entry.get("balance") is not None
    ]
    return anchors or None


def _recorded_splits(root):
    """The split map the last review froze, for lanes that have no review of
    their own (#558).

    ``refresh`` runs as two separate CLI calls and content-addresses exactly
    what the user was shown, so its split basis has to be identical across
    both. Reading the frozen map keeps that deterministic; fetching per call
    would let a network blip between the plan and the answer invalidate a
    refresh_id the user is in the middle of answering. A root that has never
    been reviewed carries no map and degrades to unadjusted, as before.
    """
    return (_previous_state(root) or {}).get("splits")


def _refuse_an_unprovable_split_basis(root, anchor_as_of):
    """Refuse when the frozen split map cannot testify about this book's anchor.

    #605's residue, and it is a silent-wrong-number one. Retrieval used to be
    unbounded, so whatever date a reader rebased from, the frozen map covered it.
    One batched request carries only the splits inside its window, and the two
    readers here — ``refresh`` and ``prepare``'s own catch-up gate — may not
    re-resolve: ``refresh`` runs as two CLI calls and content-addresses what the
    user was shown, so a fetch between them could invalidate a ``refresh_id``
    mid-answer (#558's ruling, unchanged).

    Measured, not argued. A book holding 90 shares declared before a ten-for-one
    and never traded since reconciles cleanly against a post-split broker view of
    900 when the map covers the split; with the map one window short,
    ``plan_refresh`` returns a ``large_change`` asking the user to confirm going
    from 90 shares to 900 — a change that never happened — plus a matching
    ``avg_cost`` move. Confirm it and a wrong share count enters the book
    durably.

    ``trade_recap.market_request`` makes this unreachable for an ordinary root:
    it derives its window from the union of the CSV's first trade and the ledger
    anchor, and ``prepare`` passes the root's own ledger through ``TR_LEDGER``.
    What it cannot cover is an anchor that arrives *after* the last review with
    an older ``as_of``. That case refuses here rather than reconciling against a
    basis nothing established — and refusing is cheap, because the repair is one
    command: run ``prepare`` again and the map is re-frozen with the anchor in
    its window.

    A root with no recorded window at all is a pre-#605 review, whose map was
    unbounded and therefore sufficient. Silence is correct there; treating a
    missing stamp as insufficient would refuse every user mid-upgrade.
    """
    state = _previous_state(root) or {}
    window = state.get("splits_window") or {}
    start = str(window.get("start") or "").strip()
    if not start or not anchor_as_of:
        return
    if str(anchor_as_of) >= start:
        return
    raise ReviewError(
        f"the recorded split basis was resolved from {start} and cannot say what happened to "
        f"this book's share counts before it, but the recorded book is anchored on "
        f"{anchor_as_of}. Reconciling across that gap would read an unseen split as a share "
        "change you never made. Run prepare again to re-resolve the split basis over the "
        "anchor's own window, then repeat this command.")


def _effective_splits(root, supplied):
    """The one split map ``consider`` reasons over (#558 precedence, #583).

    Per ticker, never per call (#583 post-merge finding). The recorded map is
    the floor, and a ticker with supplied events is overridden by them — they
    arrived with the quotes, on one basis at one instant, and a CSV-route
    caller may have no review in this root to have frozen anything. What a
    supplied envelope must not do is *remove* another ticker's already-recorded
    split: an envelope legitimately omits ``splits`` for a ticker whose close
    already post-dates its split (references/price-feed.md calls that the
    compatible basis), and whole-map replacement read that omission as "no
    split ever existed" — the book was then valued at that ticker's raw
    pre-split count, wrong by the split factor, under a valid
    ``state_version``. Dropping the recorded event also disarmed
    ``basis_conflicts`` for exactly that ticker, since the check only sees
    tickers the map carries. Neither side is fetched.

    Two callers need this answer for the same call, which is why it is a
    function rather than an expression: ``_consider_rows`` uses it to carry the
    running position across each split, and ``cmd_consider`` uses it to check
    that the prices it is about to multiply those shares by are on the same
    basis. Resolving it twice by hand is how a check ends up validating a
    different map from the one the arithmetic used.
    """
    recorded = _recorded_splits(root)
    if supplied is None:
        return recorded
    merged = dict(recorded or {})
    merged.update(supplied)
    return merged


def _consider_market_universe(args, root):
    """What ``consider`` must ask the market about, read from the raw source once.

    The same ordering problem ``trade_recap.market_request`` has, one route over:
    the book cannot be built until the split map is known, and the request cannot
    be built from a book that does not exist yet. So this reads the *source* — the
    CSV rows, or the ledger's own events — for facts that need no arithmetic:
    which instruments, which currency each one trades in, and the oldest date a
    consumer will rebase a split from.

    That last one is the whole reason this is not a one-liner. On the ledger route
    the origin is the recorded anchor, which routinely predates every trade in any
    CSV the user has handed over; a window scoped to the trades alone would leave
    the resolved split map short of exactly the events ``ledger.derive_holdings``
    applies at its anchor, and a missing split is a silent factor of 1.0.

    One reader, one pass. The currency map travels with the universe rather than
    being read again by a second function: they come from the same rows, and two
    walks over one source is the divergent-derivation shape
    docs/development-guide.md section 7 names — here it would put the envelope's
    declared currency and the request's currency list one edit apart from
    disagreeing.

    Returns ``None`` when the source yields nothing to ask about. That is not an
    error at this layer: the existing refusals in ``_consider_rows`` own it, and
    say something more useful than this function could.
    """
    tickers, currencies, origin = {}, set(), None
    if args.paths:
        try:
            rows = trade_recap.load([os.path.abspath(os.path.expanduser(path))
                                     for path in args.paths])
        except Exception:                                   # noqa: BLE001
            return None                                     # _consider_rows refuses, with its message
        for row in rows or ():
            currency = str(row.get("currency") or "USD").upper()
            tickers[symbols.canonical_ticker(row["ticker"])] = currency
            currencies.add(currency)
            day = str(row["date"])
            origin = day if origin is None else min(origin, day)
    else:
        try:
            events, _skipped = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
        except Exception:                                   # noqa: BLE001
            return None
        anchor_row = ledger.latest_anchor(events or [], declared_only=True) or {}
        for position in anchor_row.get("positions") or ():
            ticker = symbols.canonical_ticker(position.get("ticker"))
            if ticker:
                currency = str(position.get("currency") or "USD").upper()
                tickers[ticker] = currency
                currencies.add(currency)
        if anchor_row.get("as_of"):
            origin = str(anchor_row["as_of"])
        for event in events or ():
            if event.get("type") != "trade":
                continue
            ticker = symbols.canonical_ticker(event.get("ticker"))
            if ticker:
                currency = str(event.get("currency") or "USD").upper()
                tickers.setdefault(ticker, currency)
                currencies.add(currency)
            day = str(event.get("date") or "")
            if day:
                origin = day if origin is None else min(origin, day)
    if not tickers or origin is None:
        return None
    return {"currency_by_ticker": tickers, "currencies": currencies, "origin": origin}


def _resolve_consider_prices(args, root, premise_ticker=None, premise_currency=None):
    """Resolve the smallest current bundle this ``consider`` needs, as an envelope.

    #605 section E. A ``consider`` with no ``--prices`` used to reason about the
    book at whatever the last review froze — or refuse outright for a
    mixed-currency book, because no rate was ever in reach (#602). There is no
    product rule against retrieving current facts here, so it retrieves them.

    They enter through ``market_data.to_price_feed_envelope`` and then through the
    *existing* supplied-price path, unchanged. That lane already reconciles
    declared currencies against the trades, checks that the prices and the share
    counts are on one split basis, builds the valuation manifest and freezes the
    provenance — so live resolution adds no second downstream path to get wrong,
    and the acceptance criterion that a resolved run produces the same normalized
    valuation facts as an equivalent supplied fixture holds by construction.

    Returns ``(feed, bundle)``, either of which may be ``None``. Every failure
    lands on the pre-#605 behaviour rather than on a refusal: no prices, the
    book's own basis, and — for a mixed-currency book still missing a rate —
    ``consequence.portfolio_state``'s existing fail-closed refusal, which remains
    the residual floor. Nothing here ever substitutes a rate nobody observed.
    """
    universe = _consider_market_universe(args, root)
    if universe is None:
        return None, None
    instruments = set(universe["currency_by_ticker"])
    currencies = set(universe["currencies"])
    currency_by_ticker = dict(universe["currency_by_ticker"])
    if premise_ticker:
        instruments.add(premise_ticker)                     # the trade being asked about
        # And its currency. A premise may name an instrument the book has never
        # held, in a currency the book has never held either — a USD-only book
        # asked about a TWD listing. Adding the ticker without its currency
        # requested no rate for it and declared the row as USD, so automatic
        # retrieval could not complete an otherwise valid non-USD question
        # (external review, finding 8). The downstream refusal was correct; it was
        # refusing something the provider could have answered.
        if premise_currency:
            currency_by_ticker[premise_ticker] = str(premise_currency).upper()
            currencies.add(str(premise_currency).upper())
    try:
        request = market_data.build_request(
            instruments=instruments,
            currencies=currencies,
            window_start=universe["origin"],
            rebase_origin=universe["origin"])
    except market_data.MarketDataError:
        return None, None
    bundle = market_data.resolve(request, root=root)
    envelope = market_data.to_price_feed_envelope(
        bundle, currency_by_ticker=currency_by_ticker)
    if envelope is None:
        return None, bundle
    try:
        return price_feed.parse(envelope), bundle
    except price_feed.PriceFeedError:
        # An envelope this repository built and cannot itself parse is a defect
        # here, not a user error — so it degrades rather than refusing a review
        # the user can otherwise have.
        return None, bundle


def _price_observation_record(feed, universe):
    """Which market session each number in this answer was valued at (#618).

    Before #611 a ``consider`` weight was a share of cost, or of whatever the
    last review froze, so the same premise re-asked returned the same weights.
    It is now a share of the current close — which is the point, and which
    makes every weight a function of a market day the row never named. The
    user could see that the numbers moved and had no way to tell whether the
    market moved or their own book did.

    **Per ticker, with a frame-level summary**, rather than one date for the
    frame. That choice is #583 §2's, already paid for one surface over: a
    single ``as_of`` over a mixed frame makes one fresh instrument stand in
    for every stale one, and ``price_feed.parse`` already stores each row's
    own ``observed_date`` so there is nothing to derive a second time here.

    ``as_of`` is the newest observation **actually used**, computed from the
    per-ticker map rather than copied from ``feed["as_of"]``. The envelope's
    own declared frame date is an upper bound the parser enforces (a row may
    not post-date it), not an observation: a supplied envelope may legitimately
    declare today while every close in it is yesterday's, and copying that
    forward would put the summary ahead of every number it summarizes — the
    same confusion at frame level that the per-ticker map exists to prevent.
    ``market_data.to_price_feed_envelope`` already takes ``max(row date)`` for
    exactly this reason; this is that definition, applied to the subset that
    reached the answer.

    ``universe`` is what *this answer* needed priced — the held book, whatever
    it could not value, and the premise's own ticker — the same set the
    recovery kit is scoped to, read here rather than derived a second time. An
    envelope may carry closes for instruments no number in this answer uses,
    and dating those would owe the agent a sentence about a position the user
    does not hold.

    Returns ``None`` when no observation reached this book, which is what keeps
    the hard rule of #618 mechanical: an unpriced run grows no date at all,
    rather than a null, a placeholder, or today's.
    """
    by_ticker = {ticker: row["observed_date"]
                 for ticker, row in ((feed or {}).get("prices") or {}).items()
                 if ticker in set(universe or ()) and row.get("observed_date")}
    if not by_ticker:
        return None
    return {"as_of": max(by_ticker.values()),
            "by_ticker": {ticker: by_ticker[ticker] for ticker in sorted(by_ticker)}}


def _profile_path(root):
    return os.path.join(root, "profile.json")


def _position_cap_override(root):
    """The user's standing single-position cap from ``profile.json`` (#324).

    Validated to a (0,1) fraction or ``None`` (fail-closed): a missing,
    unreadable, or out-of-range profile silently falls back to the universal
    default rather than poisoning diagnosis. This is a standing preference, not
    per-session state, so it lives outside ``last_state.json`` (which the engine
    overwrites every run)."""
    path = _profile_path(root)
    if not os.path.exists(path):
        return None
    try:
        profile = session.read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(profile, dict):
        return None
    return card_renderer.valid_position_cap(profile.get("max_position_pct"))


def _positive_fx_rate(value):
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _apply_display_currency(card, state, previous, language):
    """Freeze the locale display-currency decision into both engine artifacts.

    The engine fetches current rates during ``prepare``.  If the requested
    display rate is unavailable, only presentation may reuse the prior state's
    rate; portfolio calculations keep their current honesty/fx-gap semantics.
    ``preview`` and ``finalize`` therefore remain deterministic and offline.
    """
    requested = card_renderer.default_display_currency(language)
    card_meta = dict((card or {}).get("currency_meta") or {})
    state_meta = dict((state or {}).get("currency_meta") or {})
    aggregate = str(card_meta.get("aggregate_currency") or
                    state_meta.get("aggregate_currency") or "USD").upper()
    mixed = bool(card_meta.get("mixed") if "mixed" in card_meta else state_meta.get("mixed"))

    rate = None
    source = "identity"
    reason = None
    as_of = None
    effective = aggregate
    if mixed:
        effective = requested
        current_fx = dict(state_meta.get("fx") or {})
        current_fx.update(card_meta.get("fx") or {})
        currencies = list(card_meta.get("currencies") or state_meta.get("currencies") or [])
        explicit_gaps = (((card or {}).get("data_integrity") or {}).get("fx_gaps") or [])
        held_rate_missing = bool(explicit_gaps) or bool(currencies and any(
            str(currency).upper() != "USD" and _positive_fx_rate(current_fx.get(str(currency).upper())) is None
            for currency in currencies
        ))
        if held_rate_missing:
            # The engine already aggregated the missing held currency with its
            # explicit 1:1 approximation.  A display-only cache cannot repair
            # that common-currency amount, so fall back to original buckets.
            effective = None
            source = "unavailable"
            reason = "portfolio_fx_gap"
        else:
            rate = 1.0 if requested == "USD" else _positive_fx_rate(current_fx.get(requested))
            source = "current"
        if not held_rate_missing and rate is None:
            previous_meta = ((previous or {}).get("currency_meta") or {})
            if previous_meta.get("mixed") and previous_meta.get("display_currency") == requested:
                rate = _positive_fx_rate(previous_meta.get("display_fx_rate"))
            if rate is None:
                rate = _positive_fx_rate((previous_meta.get("fx") or {}).get(requested))
            if rate is not None:
                source = "cached"
                as_of = previous_meta.get("display_fx_as_of") or (previous or {}).get("date_end")
            else:
                effective = None
                source = "unavailable"
                reason = "display_fx_gap"

    def enrich(artifact):
        out = dict(artifact or {})
        meta = dict(out.get("currency_meta") or {})
        meta.update({
            "requested_display_currency": requested,
            "display_currency": effective,
            "display_fx_source": source,
            "display_fx_rate": rate if mixed else None,
        })
        if reason:
            meta["display_fx_reason"] = reason
        else:
            meta.pop("display_fx_reason", None)
        if as_of:
            meta["display_fx_as_of"] = as_of
        else:
            meta.pop("display_fx_as_of", None)
        out["currency_meta"] = meta
        return out

    return enrich(card), enrich(state)


def _review_date(state):
    try:
        return dt.date.fromisoformat(str((state or {}).get("date_end")))
    except (TypeError, ValueError):
        return dt.date.today()


# Cadence detection (#237). The span between the previous review and this one
# decides how heavy a card is warranted: a short span is a high-frequency check
# that later stages should render light, while a long span, a first review, or a
# snapshot opening check warrants the full story card. The threshold is the one
# human knob ("how short counts as high-frequency"); everything else keys off
# real timestamps. This is a presentation-selection signal only — it never
# changes an engine number, so card output is unchanged until a consumer reads
# the tier.
CADENCE_LIGHT_MAX_DAYS = 5


def _review_span_days(date_end, previous):
    """Calendar days from the previous review's ``date_end`` to this one.

    Returns None when there is no comparable prior boundary (first review,
    missing or unparseable dates). An out-of-order re-run clamps to 0 instead of
    going negative, so a stale resend cannot read as a long span.
    """
    prev_end = (previous or {}).get("date_end")
    if not date_end or not prev_end:
        return None
    try:
        start = dt.date.fromisoformat(str(prev_end))
        end = dt.date.fromisoformat(str(date_end))
    except (TypeError, ValueError):
        return None
    return max(0, (end - start).days)


def _cadence(route, date_end, previous):
    """Classify this review's cadence tier from its span.

    ``light`` marks a short-span, high-frequency review that later stages should
    render as a light capture rather than the full story card; ``full`` marks a
    first review, a snapshot opening check, a returning review with no
    comparable prior boundary, or any span past the threshold. The tier is
    advisory metadata for downstream rendering and questioning; it does not gate
    or alter any engine calculation, so existing output is unchanged until a
    consumer opts in.
    """
    threshold = CADENCE_LIGHT_MAX_DAYS
    if route in ("first_review", "snapshot_review"):
        return {"tier": "full", "span_days": None, "threshold_days": threshold,
                "basis": route, "override": None}
    span = _review_span_days(date_end, previous)
    if span is None:
        return {"tier": "full", "span_days": None, "threshold_days": threshold,
                "basis": "no_prior_boundary", "override": None}
    tier = "light" if span <= threshold else "full"
    return {"tier": tier, "span_days": span, "threshold_days": threshold,
            "basis": "span", "override": None}


def _trade_span_days(date_start, date_end):
    """Calendar-day span of the trade file itself (first row to last).

    Mirrors ``trade_recap.build_state``'s own span basis; used only for the
    advisory ``durability_short`` flag in :func:`_review_tier`. Returns None when
    either boundary is missing or unparseable. Distinct from
    ``_review_span_days``, which measures the gap *between* reviews.
    """
    if not date_start or not date_end:
        return None
    try:
        start = dt.date.fromisoformat(str(date_start))
        end = dt.date.fromisoformat(str(date_end))
    except (TypeError, ValueError):
        return None
    return max(0, (end - start).days)


def _review_tier(state):
    """Engine-owned classification of how much a file can support (#306).

    Additive Review Plan metadata (frozen into ``state_snapshot``), deterministic
    and fail-closed. It records the entry decision in one place so later
    consumers — routing, question density, card framing — read a single
    engine-owned source instead of re-deriving thresholds in agent prose:

    - ``empty``:       no current holdings and no closed round trips — nothing to
                       diagnose; the flow must tell the user exactly what to add.
    - ``behavioral``:  at least ``MIN_ROUND_TRIPS`` closed round trips — enough
                       realized history for a full behavioral review.
    - ``structural``:  holdings exist but fewer round trips — an opening
                       structural check.

    Per #306 the calendar span is advisory only: ``durability_short`` flags a
    short trade window for a later note, but round-trip *count* — never span —
    decides ``behavioral``, so a high-frequency short-window file is not demoted
    the way the ``rts<3 or span<MIN_SPAN_DAYS`` commitment gate would demote it.
    Nothing consumes this field yet; emitting it first keeps the tier a single
    source of truth for the follow-up routing/rendering changes.
    """
    n_rt = state.get("n_round_trips") or 0
    n_held = state.get("n_held")
    if n_held is None:
        n_held = len(((state.get("holdings") or {}).get("positions")) or {})
    span_days = _trade_span_days(state.get("date_start"), state.get("date_end"))
    if n_held == 0 and n_rt == 0:
        tier = "empty"
    elif n_rt >= trade_recap.MIN_ROUND_TRIPS:
        tier = "behavioral"
    else:
        tier = "structural"
    return {
        "tier": tier,
        "n_round_trips": n_rt,
        "n_held": n_held,
        "span_days": span_days,
        "min_round_trips": trade_recap.MIN_ROUND_TRIPS,
        "min_span_days": trade_recap.MIN_SPAN_DAYS,
        "durability_short": span_days is not None and span_days < trade_recap.MIN_SPAN_DAYS,
    }


# Monthly vs-market cadence (#284, output contract §3): the vs-market
# comparison segment renders on the first full review of each calendar month.
# "First this month" derives from committed-session history — canonical
# bundles plus pre-v2 log.jsonl rows — judged by each review's own date_end,
# never the wall clock. The decision is frozen into the engine card at
# prepare time so preview/finalize retries and later re-renders stay
# deterministic even after other sessions commit.


def _month_key(value):
    """`YYYY-MM` of an ISO date, or None when the date cannot be parsed."""
    try:
        date = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return f"{date.year:04d}-{date.month:02d}"


def _vs_market_gate(root, date_end, exclude_session_id=None):
    """Decide whether this review renders the vs-market segment (#284).

    Consumers of the monthly slot are committed reviews that could have
    rendered the segment: canonical persistent bundles on the trades routes
    (snapshot reviews suppress the segment by design and must not burn the
    month; test drives never touch coach memory; light-tier sessions never
    finalize a card, so they neither consume nor reset the slot) plus pre-v2
    ``log.jsonl`` rows not already represented by a canonical bundle. The
    current session id is excluded so an idempotent re-prepare of an already
    committed review cannot flip its own decision. Fail-closed toward
    showing: an unreadable history or an unparseable review date renders the
    segment — over-showing is safer than silently hiding the comparison.
    """
    month = _month_key(date_end)
    if month is None:
        return {"render": True, "basis": "no_review_date", "month": None}
    try:
        seen_sessions = set()
        for dir_session_id, bundle in session.iter_canonical_bundles(root):
            session_id = str(bundle.get("session_id") or dir_session_id)
            seen_sessions.add(session_id)
            if exclude_session_id and session_id == str(exclude_session_id):
                continue
            if bundle.get("route") == "snapshot_review":
                continue
            if _month_key((bundle.get("engine_state") or {}).get("date_end")) == month:
                return {"render": False, "basis": "already_rendered_this_month",
                        "month": month}
        for row in _jsonl(os.path.join(root, "log.jsonl")):
            session_id = row.get("session_id")
            if session_id and str(session_id) in seen_sessions:
                continue  # projection of a canonical bundle classified above
            if exclude_session_id and session_id and str(session_id) == str(exclude_session_id):
                continue
            if _month_key(row.get("date_end")) == month:
                return {"render": False, "basis": "already_rendered_this_month",
                        "month": month}
    except Exception:
        # Fail-closed toward showing (#284): a gate helper must never crash
        # prepare, and a history it cannot read must not hide the segment.
        return {"render": True, "basis": "history_unreadable", "month": month}
    return {"render": True, "basis": "first_full_review_of_month", "month": month}


_CURRENT_VIEW_DIMS = {"position_sizing", "diversification"}
_CURRENT_VIEW_METRICS = {
    "max_pos_pct", "max_pos_ticker", "ai_pct", "max_sector_pct", "top3_pct"
}


def _is_current_view_dimension(row):
    if not isinstance(row, dict):
        return False
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    dim = row.get("dim") or row.get("kind") or raw.get("dim")
    return bool(dim) and card_renderer.dimension_id(dim) in _CURRENT_VIEW_DIMS


def _empty_portfolio_structure(source=None):
    return {
        "schema_version": 1,
        "policy": (source or {}).get("policy"),
        "allocation_weight": None,
        "concentrated_etf_weight": None,
        "by_kind": {},
        "allocation_etfs": [],
        "concentrated_etfs": [],
        "metadata_gaps": [],
    }


def _gate_current_view(card, state, detail):
    """Remove raw-CSV current-position claims that disagree with the ledger.

    Transaction rows still support history diagnostics such as exits, holding
    time, averaging-down events, payoff, and attribution.  They cannot support
    current sizing, diversification, unrealized P&L, or ETF weights when the
    complete snapshot anchor plus later ledger trades says the current account
    contains something else.
    """
    card["dims_raw"] = [row for row in card.get("dims_raw") or []
                        if not _is_current_view_dimension(row)]
    card["top_holes"] = [row for row in card.get("top_holes") or []
                         if not _is_current_view_dimension(row)]
    card["candidate_rules"] = [row for row in card.get("candidate_rules") or []
                               if not _is_current_view_dimension(row)]
    card["prescriptions"] = [row for row in card.get("prescriptions") or []
                             if not _is_current_view_dimension(row)]
    card["what_if"] = None
    card["ticker_diagnosis"] = []
    card["strength"] = None

    overview = dict(card.get("overview") or {})
    overview["total_pnl"] = None
    overview["unrealized"] = None
    card["overview"] = overview
    card["acct_perf"] = {"gate": {"status": "accounting_reconciliation", "data": {}}}

    for artifact in (card, state):
        meta = dict(artifact.get("currency_meta") or {})
        meta["pnl_by_currency"] = None
        artifact["currency_meta"] = meta
        cash = artifact.get("cash")
        if isinstance(cash, dict):
            cash = dict(cash)
            cash["weight"] = None
            artifact["cash"] = cash

    prior_structure = card.get("portfolio_structure") or state.get("portfolio_structure") or {}
    structure = _empty_portfolio_structure(prior_structure)
    card["portfolio_structure"] = structure
    state["portfolio_structure"] = dict(structure)

    metrics = dict(state.get("metrics") or {})
    for key in _CURRENT_VIEW_METRICS:
        metrics[key] = None
    state["metrics"] = metrics
    state["rule"] = None
    state["problem_events"] = [
        row for row in state.get("problem_events") or []
        if row.get("key") not in {"oversize", "concentration"}
    ]
    opportunities = state.get("problem_opportunities")
    if isinstance(opportunities, dict):
        opportunities = dict(opportunities)
        opportunities["oversize"] = False
        opportunities["concentration"] = False
        state["problem_opportunities"] = opportunities

    first_hole = (card.get("top_holes") or [None])[0]
    if isinstance(first_hole, dict):
        dim = first_hole.get("dim") or (first_hole.get("raw") or {}).get("dim")
        metric_key = DIM_METRIC.get(card_renderer.dimension_id(dim)) if dim else None
        state["headline_dim"] = dim
        state["headline_metric"] = {
            "key": metric_key,
            "value": metrics.get(metric_key) if metric_key else None,
        }
    else:
        state["headline_dim"] = None
        state["headline_metric"] = {"key": None, "value": None}

    integrity = dict(card.get("data_integrity") or {})
    integrity["accounting_reconciliation"] = detail
    card["data_integrity"] = integrity
    honesty = [row for row in card.get("honesty_ledger") or []
               if row.get("key") != "accounting_reconciliation"]
    honesty.append({
        "key": "accounting_reconciliation",
        "status": "gated",
        "data": detail,
    })
    card["honesty_ledger"] = honesty


#: The book-against-book re-keying, owned by `symbols` since `ledger.reconcile`
#: needs the identical rule and cannot import this module (#805). Kept as a name
#: here because every call site below reads better for it.
_by_canonical_identity = symbols.by_canonical_identity


def _overlay_ledger_holdings(card, state, derived, *, declared_anchor=True):
    """Make ledger holdings/cycles canonical and gate divergent card surfaces.

    ``declared_anchor`` says whether this root's book was declared by the user
    (a holdings view) or is the engine's own restatement of the trades it has
    been given (#549's ``trades_derived`` row). Both are recorded books and
    both are canonical for *which positions are held*, so both reconcile —
    but the derived lane compares strictly less, because on that lane the two
    sides are two readings of the same trade rows and only some of their
    fields can disagree for a real reason:

    - **Position coverage and share counts** are pure arithmetic over those
      rows and agree in both lanes. A file covering part of the book differs
      in exactly these, and that difference is #630. Compared always.
    - **Cost basis** is computed by two different, both-legitimate methods:
      ``ledger.derive_holdings`` keeps a moving average while the card's own
      accumulation is FIFO. On a history with partial sells they disagree by
      construction (NVDA in ``mock/sample_ai_holder.csv``: 24900 FIFO against
      23250 moving-average), so comparing them would gate every *cumulative*
      weekly review on a methodology difference that is not an error.
    - **Market and currency** are not on the raw side at all —
      ``trade_recap.build_state`` writes neither onto a holding — so the
      comparison below reads the US/USD default for every position and would
      report every non-US holding as misclassified.

    Both of those last two are meaningful against a *declared* holdings view,
    which states cost, market, and currency in its own right and can genuinely
    contradict the trades. They are meaningless against the engine's own
    restatement of those trades, which is why they are skipped there rather
    than loosened for everyone.
    """
    # Both sides read through one identity rule (#803). `derived` is the
    # ledger's canonical book; `state` carries whatever spelling the trade source
    # used, so an exact set comparison reports a phantom `ticker_set` difference
    # for a book that matches perfectly — and gates it on a disagreement about
    # case.
    raw_positions = _by_canonical_identity((state.get("holdings") or {}).get("positions"))
    canonical = _by_canonical_identity(derived.get("holdings"))
    raw_tickers, canonical_tickers = set(raw_positions), set(canonical)
    mismatches = []
    for ticker in sorted(raw_tickers | canonical_tickers):
        raw, fact = raw_positions.get(ticker), canonical.get(ticker)
        if raw is None or fact is None:
            mismatches.append({"ticker": ticker, "kind": "ticker_set"})
            continue
        raw_shares = card_renderer._finite_number(raw.get("shares"))
        canonical_shares = card_renderer._finite_number(fact.get("shares"))
        if (raw_shares is None or canonical_shares is None
                or abs(raw_shares - canonical_shares) > ledger.SHARES_TOL):
            mismatches.append({"ticker": ticker, "kind": "shares"})
            continue
        if not declared_anchor:
            # Both sides of the derived lane were built from the same trade rows,
            # and `trade_recap.build_state` writes no market/currency onto a
            # holding at all — so the raw side is *always* the US/USD default
            # below, and every non-US position would read as misclassified. The
            # `tw_mixed` persona's second review is exactly that false positive.
            continue
        # Transaction artifacts historically omit these fields for the default
        # US/USD case.  Default the raw side accordingly; falling back to the
        # canonical fact would hide a missing/misclassified non-US position.
        raw_market = str(raw.get("market") or "US").upper()
        raw_currency = str(raw.get("currency") or "USD").upper()
        if raw_market != str(fact.get("market") or "US").upper():
            mismatches.append({"ticker": ticker, "kind": "market"})
        if raw_currency != str(fact.get("currency") or "USD").upper():
            mismatches.append({"ticker": ticker, "kind": "currency"})

    prices = ((state.get("price_snapshot") or {}).get("prices") or {})
    full_price_coverage = bool(canonical) and all(
        card_renderer._finite_number(prices.get(ticker)) is not None
        and float(prices[ticker]) > 0 for ticker in canonical
    )
    # Current prices can verify today's market value, but they cannot repair a
    # divergent or unknown cost basis.  Unrealized and total P&L still depend
    # on that basis, so compare it even when every ticker has a live price.
    if declared_anchor and not mismatches and canonical:
        for ticker, fact in sorted(canonical.items()):
            raw_cost = card_renderer._finite_number((raw_positions.get(ticker) or {}).get("cost"))
            canonical_cost = card_renderer._finite_number(fact.get("cost_total"))
            if (raw_cost is None or canonical_cost is None
                    or not math.isclose(raw_cost, canonical_cost, rel_tol=1e-6, abs_tol=0.05)):
                mismatches.append({"ticker": ticker, "kind": "valuation"})

    if not declared_anchor and not mismatches:
        # On the derived lane the reconciliation is a **check, not an adoption**.
        # Both sides are readings of the same trade rows, so where they agree on
        # what is held there is nothing here the card does not already have, and
        # adopting anyway is not idempotent: the first review of a fresh root has
        # no ledger to reconcile against, every later run of the identical file
        # does, and the second one would come back with a different book —
        # `derive_holdings` keeps a moving average where the card's own
        # accumulation is FIFO, so `cost`/`avg_cost` move, and `origin`/`market`/
        # `currency` appear. `add-cash` re-enters this exact pipeline to add an
        # anchor to a session the user has already answered against, and refuses
        # when the recorded book underneath it moved; that guard is what caught
        # this.
        #
        # Disagreement is the other case, and it is still adopted below: there
        # the supplied file did not cover the book, so the card's own view is not
        # a second reading of the same rows but a narrower one (#630).
        return card, state, {"status": "matched",
                             "raw_positions_n": len(raw_positions),
                             "canonical_positions_n": len(canonical),
                             "full_price_coverage": full_price_coverage,
                             "mismatches": []}

    positions = {}
    for ticker, fact in sorted(canonical.items()):
        raw = dict(raw_positions.get(ticker) or {})
        observed_cycle = raw.get("cycle_id")
        observed_start = raw.get("cycle_start")
        add_count = int(fact.get("add_count") or 0)
        row = dict(raw)
        row.update({
            "shares": fact.get("shares"),
            "cost": fact.get("cost_total"),
            "avg_cost": fact.get("avg_cost"),
            "market": fact.get("market"),
            "currency": fact.get("currency"),
            "cycle_start": fact.get("since"),
            "cycle_id": fact.get("cycle_id"),
            "origin": fact.get("origin"),
            "left_truncated": fact.get("origin") == "snapshot",
            "add_count": add_count,
            "decision_cursor": fact.get("decision_cursor"),
        })
        if observed_cycle and observed_cycle != fact.get("cycle_id"):
            row["observed_cycle_id"] = observed_cycle
        if observed_start and observed_start != fact.get("since"):
            row["observed_cycle_start"] = observed_start
        positions[ticker] = row

    state["holdings"] = {
        "as_of": state.get("date_end") or (state.get("holdings") or {}).get("as_of"),
        "derived_from": "snapshot_plus_trades" if declared_anchor else "ledger_trades",
        "positions": positions,
    }
    state["n_held"] = len(positions)
    metrics = dict(state.get("metrics") or {})
    metrics["n_holdings"] = len(positions)
    state["metrics"] = metrics

    # A pre-anchor add is history, not a new decision after the opening snapshot.
    post_anchor_adds = {ticker for ticker, row in positions.items()
                        if row.get("decision_cursor")}
    card["thesis_questions"] = [row for row in card.get("thesis_questions") or []
                                if row.get("ticker") in post_anchor_adds]

    detail = {
        "status": "matched" if not mismatches else "current_view_gated",
        "raw_positions_n": len(raw_positions),
        "canonical_positions_n": len(positions),
        "full_price_coverage": full_price_coverage,
        "mismatches": mismatches,
    }
    if mismatches:
        _gate_current_view(card, state, detail)
    return card, state, detail


def _stamp_recorded_book_basis(card, reconciliation):
    """Say that this card's current-book figures were measured over the book (#630).

    The single decider. ``trade_recap.current_book_projection`` mints
    ``book_basis: supplied_rows`` because it is handed holdings and no book to
    check them against; this is the one place that holds both, and it runs only
    after ``_record_derived_book`` wrote down the book this import produced —
    which is ``derive_holdings`` over exactly the events the card was built
    from, so on a first review, and on any later review whose file still covers
    the whole account, the two are the same book.

    A reconciliation that found mismatches is the case where they are not, and
    it has already stripped every current-view surface through
    ``_gate_current_view``; stamping there would relabel figures that no longer
    exist, so it is refused rather than skipped silently.
    """
    if reconciliation is not None and reconciliation.get("mismatches"):
        return
    for row in (card.get("dims_raw") or []) + [
            (hole.get("raw") or {}) for hole in card.get("top_holes") or []]:
        coverage = row.get("sizing_coverage") if isinstance(row, dict) else None
        if not isinstance(coverage, dict):
            continue
        coverage["book_basis"] = trade_recap.BOOK_BASIS_RECORDED
        coverage["scope"] = trade_recap.book_scope(
            trade_recap.BOOK_BASIS_RECORDED, bool(coverage.get("unavailable")))


def _record_derived_book(root, ledger_path, events, state):
    """Write down the book this import produced, at the time it produced it (#549).

    The single place either transaction lane records its result, so the two
    cannot drift about what a recorded book is or when one is written.  Before
    this, a CSV import left trade rows and no statement of what the engine
    concluded was held, so a holdings view arriving later had no predecessor to
    update: ``refresh`` said the root had no recorded book while the root held
    twenty-three trades and three positions, and ``prepare --route
    snapshot_review`` said the history needed reconciliation that could not be
    computed without one.  Owner ruling on #549: every source that arrives
    records the book at its own time, and ``source`` records which kind of
    source it was without that ever deciding whether the row counts.

    The row is written under the caller's projection transaction, immediately
    after the trades it summarizes, so an abandoned session can never leave
    trades whose conclusion was never recorded.  ``ledger.build_derived_book``
    returns ``None`` for a book with no positions, which is what keeps a root
    with no history at all on the onboarding path.
    """
    day = state.get("date_end")
    trade_days = [row for row in (ledger._norm_trade(event) for event in events
                                  if event.get("type") == "trade") if row is not None]
    latest_trade = max((row[0].isoformat() for row in trade_days), default=None)
    # The book cannot be dated before its own newest fact: `snapshot_reconciliation`
    # refuses a declaration older than the recorded book, so a row stamped behind
    # its trades would refuse a holdings view that is genuinely newer.
    as_of = max(value for value in (day, latest_trade) if value) if (day or latest_trade) else None
    if as_of is None:
        return None
    recorded = ledger.build_derived_book(events, as_of=as_of,
                                         splits=state.get("splits"))
    if recorded is None:
        return None
    return session.append_book_adoption(
        ledger_path, anchor=recorded, reconciliation=None,
        actor_id=f"trades-import-{as_of}",
        sequence=session.next_projection_sequence(root),
        recorded_at=day)


def _ingest_trades(root, paths, card, state):
    """Validate all normalized CSVs, then append their trade facts once.

    Validation completes before the first write so a bad file cannot leave a
    partially ingested multi-file review.  Overlapping weekly files remain safe:
    each later batch deduplicates against both the existing ledger and earlier
    batches from this prepare call.

    Only future-dated rows reject the import (#169: the one zero-false-positive
    corruption signal).  Non-trade rows — deposits, dividends, interest, fees,
    reinvest notices — legitimately coexist in the same normalized CSV because
    the engine's cash pipeline consumes them; they are counted and reported,
    never fatal (#50: visible, not silent).
    """
    batches = []
    skipped_non_trade = skipped_future = 0
    for path in paths or []:
        trades, non_trade, future = ledger.trades_from_csv(path)
        batches.append(trades)
        skipped_non_trade += non_trade
        skipped_future += future
    if skipped_future:
        raise ReviewError(
            "ledger ingestion rejected normalized input before writing: "
            f"{skipped_future} future-dated row(s)"
        )

    ledger_path = os.path.join(root, "ledger.jsonl")
    # This is one root-wide check/derive/append transaction.  Snapshot finalize
    # holds the same lock from its final empty-history check through canonical
    # commit and anchor projection, so neither path can observe an empty root
    # and then write across the other's boundary.
    with session.projection_transaction(root):
        # #462: this existing-ledger read feeds derive_holdings a few lines
        # down (the overlay the card and any reconciliation are built from),
        # so a corrupt row here must block the import rather than let the
        # overlay compute over a silently shortened history.
        try:
            existing, skipped_lines = ledger.load_ledger(ledger_path)
        except ledger.LedgerIntegrityError as exc:
            raise ReviewError(str(exc)) from exc
        virtual = list(existing)
        fresh_all = []
        skipped_dup = 0
        for batch in batches:
            fresh, dup = ledger.dedupe_against(virtual, batch)
            fresh_all.extend(fresh)
            virtual.extend(fresh)
            skipped_dup += dup
        reconciliation = None
        # A declared snapshot is the accounting source of truth for current
        # holdings.  Derive against the virtual post-import ledger before the first
        # write so the card can fail closed without leaving a partial import.
        # `declared_only` deliberately: this lane's own `trades_derived`
        # restatement (#549) is the very derivation being compared, so routing it
        # here would reconcile the book against itself and gate the card on
        # rounding.
        #
        # #630's wider reconciliation is deliberately NOT mirrored here, and the
        # reason is what this lane is: `cmd_prepare` reaches it only when the
        # caller supplied `--card-json`/`--state-json`, so `state["holdings"]` was
        # asserted by that caller and is not a derivation of the CSVs beside it.
        # Comparing the two would not reconcile one book against itself; it would
        # compare an asserted artifact with an unrelated file. Every review a real
        # user reaches freezes its inputs and goes through
        # `_verify_and_ingest_frozen_trades`, which does carry #630's rule —
        # `tests/test_review_v2.py::test_only_the_adapter_lane_skips_the_recorded_book_reconciliation`
        # fails if that stops being true.
        if ledger.latest_anchor(existing, declared_only=True) is not None:
            card, state, reconciliation = _overlay_ledger_holdings(
                card, state, ledger.derive_holdings(virtual, splits=state.get("splits"))
            )
        if fresh_all:
            # #472: recorded_at = this review period's own date_end, the same
            # proxy _build_exit_narratives already uses for "when the system
            # learned this" — deterministic within a review, unlike wall-clock.
            ledger.append_events(ledger_path, fresh_all, recorded_at=state.get("date_end"))
        recorded_book = _record_derived_book(root, ledger_path, virtual, state)
        result = {
            "path": ledger_path,
            "appended": len(fresh_all),
            "skipped_dup": skipped_dup,
            "skipped_non_trade": skipped_non_trade,
            "skipped_future_dated": skipped_future,
            "skipped_ledger_lines": skipped_lines,
        }
        if recorded_book is not None:
            result["recorded_book"] = recorded_book
        if reconciliation is not None:
            result["holdings_reconciliation"] = reconciliation
    return result, card, state


def _verify_and_ingest_frozen_trades(root, inputs, batches, overlay, basis_receipt, card, state, *,
                                     append=True, amending=False):
    """Final short-lock gate for one frozen engine/PB transaction (#501).

    ``amending`` is a pass recomputing a review the user has already been shown
    — today only ``cmd_add_cash``. Such a pass may find every one of its rows
    already recorded and nothing else: a transaction file that grew since the
    card was rendered is a different review, and the answers already given were
    made against a book that no longer exists. Refused *here*, before the
    append, because a refusal that lands after it has already written the rows
    it is refusing (#665, maintainer disposition: "remains a refusal before any
    write"). The dedup that decides it is the engine's own, not a second digest
    with its own opinion about what counts as the same file.
    """
    frame = basis_receipt.get("valuation_frame")
    if not isinstance(frame, dict) or _frame_identity(frame) != basis_receipt["valuation_frame_identity"]:
        raise ReviewError("this review's price basis changed while it was being prepared; rerun prepare")
    original_paths = [row["original_path"] for row in inputs["candidate"]["files"]]
    ledger_path = os.path.join(root, "ledger.jsonl")
    with session.projection_transaction(root):
        # Byte receipt comes first: a whitespace-only change is still a different
        # engine input, even if the CSV parser would yield identical trades.
        with tempfile.TemporaryDirectory(prefix="fomo-verify-") as verify_dir:
            current_candidate, verify_paths = _candidate_receipt(original_paths, verify_dir)
            if current_candidate != inputs["candidate"]:
                raise ReviewError(BASIS_CHANGED_MESSAGE)
            # Reparse the just-frozen verifier copy, never the mutable path after
            # digest comparison. Append reuses the resulting overlay below.
            # skipped_future is always zero here: _parse_frozen_candidates raises
            # on a future-dated row, and identical bytes cannot have grown one
            # anyway. It stays in the result only to match the legacy lane's
            # reported shape.
            verified_batches, skipped_non_trade, skipped_future = _parse_frozen_candidates(verify_paths)
        live_events, live_receipt, _payload = _read_live_ledger(root)
        if live_receipt != inputs["ledger_receipt"]:
            raise ReviewError(BASIS_CHANGED_MESSAGE)
        try:
            verified_overlay = ledger.virtualize(live_events, verified_batches)
            verified_frame, verified_book_as_of = _virtual_valuation_frame(
                verified_overlay["events"], state.get("valuation_frame"),
                splits=state.get("splits"))
            verified_basis = portfolio_basis.query_current_book(
                verified_overlay["events"], valuation_manifest=verified_frame,
                reference_as_of=_basis_reference(frame.get("as_of"), verified_book_as_of),
                skipped_lines=live_receipt["skipped_lines"],
                splits=state.get("splits"))
        except (ValueError, portfolio_basis.PortfolioBasisError) as exc:
            raise ReviewError(f"your book could not be read before saving: {exc}") from exc
        if (verified_basis is None or _frame_identity(verified_frame) != basis_receipt["valuation_frame_identity"]
                or verified_basis.state_version != basis_receipt["basis_state_version"]
                or verified_overlay != overlay):
            raise ReviewError(BASIS_CHANGED_MESSAGE)
        reconciliation = None
        # See `_ingest_trades`: any recorded book, declared or derived (#630).
        #
        # The predicate reads the **post-import** book, never the pre-import
        # ledger, because this command writes that ledger: `latest_anchor(
        # live_events)` is false on a fresh root's first review and true on every
        # later run of the identical file, so the same input would reconcile on
        # one pass and not on the other. `declared_anchor` may read `live_events`
        # — a `trades_derived` row is never declared, so that answer does not
        # move when this import records one.
        derived_book = ledger.derive_holdings(verified_overlay["events"],
                                              splits=state.get("splits"))
        declared_anchor = ledger.latest_anchor(live_events, declared_only=True) is not None
        if append and (derived_book.get("holdings") or declared_anchor):
            card, state, reconciliation = _overlay_ledger_holdings(
                card, state, derived_book, declared_anchor=declared_anchor)
        if amending and verified_overlay["fresh"]:
            raise ReviewError(
                "the facts moved: this is not the anchor propagating into the account pillar — "
                f"the transaction file has grown by {len(verified_overlay['fresh'])} row(s) since "
                "this review's card was rendered, so the answers already given were made against "
                "a book that no longer exists. Nothing was written. Start a fresh review with "
                "prepare (adding --cash if you have the balance) rather than amending one the "
                "user answered against different trades")
        if append and verified_overlay["fresh"]:
            ledger.append_events(ledger_path, verified_overlay["fresh"], recorded_at=state.get("date_end"))
        recorded_book = (_record_derived_book(root, ledger_path, verified_overlay["events"], state)
                         if append else None)
        if recorded_book is not None:
            _stamp_recorded_book_basis(card, reconciliation)
        result = {"path": ledger_path, "appended": len(verified_overlay["fresh"]) if append else 0,
                  "skipped_dup": verified_overlay["skipped_dup"],
                  "skipped_non_trade": skipped_non_trade,
                  "skipped_future_dated": skipped_future,
                  "skipped_ledger_lines": live_receipt["skipped_lines"]}
        if recorded_book is not None:
            result["recorded_book"] = recorded_book
        if reconciliation is not None:
            result["holdings_reconciliation"] = reconciliation
    return result, card, state


def _exit_narrative_index(root):
    """Map revisit_id -> latest captured exit narrative (canonical sessions win).

    Legacy `theses.jsonl` rows load first, then canonical bundles override them
    in the iterator's shared (date_end, session_id) order — the same precedence
    `_thesis_event_history` uses — so capture identity and the recorded reason
    stay consistent even when an undated bundle is present.
    """
    index = {}
    for row in _jsonl(os.path.join(root, "theses.jsonl")):
        if row.get("event") == "exit_narrative" and row.get("revisit_id"):
            index[row["revisit_id"]] = row
    for _session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
        for row in bundle.get("exit_narratives") or []:
            if row.get("revisit_id"):
                index[row["revisit_id"]] = row
    return index


def _thesis_event_history(root):
    """Load canonical thesis events first and retain pre-v2 legacy-only rows.

    Projection files remain supported, but deleting one cannot erase continuity
    while its canonical session bundle still exists.
    """
    legacy_theses = _jsonl(os.path.join(root, "theses.jsonl"))
    legacy_decisions = _jsonl(os.path.join(root, "thesis_decisions.jsonl"))
    canonical_sessions = set()
    ordered_bundles = []
    for session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
        canonical_sessions.add(session_id)
        ordered_bundles.append(bundle)

    thesis_rows = [row for row in legacy_theses
                   if row.get("session_id") not in canonical_sessions]
    decision_rows = [row for row in legacy_decisions
                     if row.get("session_id") not in canonical_sessions]
    for bundle in ordered_bundles:
        thesis_rows.extend(bundle.get("thesis_updates") or [])
        thesis_rows.extend(bundle.get("exit_narratives") or [])
        decision_rows.extend(bundle.get("thesis_decisions") or [])
    return thesis_rows, decision_rows


def _rule_breach_history(root):
    """Return the latest canonical breach decision per rule.

    The history stays in immutable bundles rather than a second mutable ledger.
    It is used only to enforce the first-breach-or-worsening question cadence.
    """
    latest = {}
    for _session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
        for row in bundle.get("rule_breach_decisions") or []:
            if row.get("rule_id"):
                latest[row["rule_id"]] = row
    return latest


def _headline_motive_history(root):
    """Reconstruct typed headline-motive decisions from canonical bundles.

    The JSONL file is only a compatibility projection.  Canonical bundles win
    for their session, so deleting or partially rebuilding projections cannot
    erase the user's recorded classification from a later Review Plan.
    """
    legacy = _jsonl(os.path.join(root, "headline_motives.jsonl"))
    canonical_sessions = set()
    rows = []
    for session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
        canonical_sessions.add(session_id)
        rows.extend(bundle.get("headline_motive_events") or [])
    return [row for row in legacy if row.get("session_id") not in canonical_sessions] + rows


def _prepare_exit_capture(root, state, persist):
    """Enqueue ledger exits and return capture, due-checkpoint, and backlog signals.

    Returns (recent, due, backlog, ingest_meta):
      recent  - fresh exits still inside the capture window and not yet captured
      due     - 30/60/90 checkpoints that matured after tracking started (#170);
                each row carries the prior recorded exit reason and the frozen
                engine-price swap comparison (missing prices stay honest)
      backlog - pre-activation historical exits: top items + aggregate summary
    """
    if not persist:
        return [], [], None, {"enqueued": 0, "skipped_dup": 0, "skipped_queue_lines": 0}
    ledger_path = os.path.join(root, "ledger.jsonl")
    queue_path = os.path.join(root, "revisit.jsonl")
    as_of = _review_date(state)
    # #462: revisit.enqueue_from_ledger scans for exits with ledger.load_ledger
    # underneath; a corrupt row must block enqueueing rather than let a real
    # exit go undetected and silently never reach the 30/60/90 follow-up.
    # #550: the exit walk accumulates share counts across a ticker's whole
    # history, and the ledger stores quantities exactly as transacted. Without
    # this map a sale after a split is subtracted from pre-split buys, and a
    # partial trim reads as a full liquidation -- which closes the thesis
    # permanently and prints "fully exited" on the saved card. The map is the
    # one this review already applied to its own analytics, frozen into state,
    # so the two readers cannot disagree about what a split did.
    try:
        new, dup = revisit.enqueue_from_ledger(ledger_path, queue_path, today=as_of,
                                               splits=state.get("splits"))
    except ledger.LedgerIntegrityError as exc:
        raise ReviewError(str(exc)) from exc
    except split_policy.SplitDataError as exc:
        raise ReviewError(f"this review's split history could not be read: {exc}") from exc
    revisits, resolutions, skipped = revisit.load_queue(queue_path)
    narratives = _exit_narrative_index(root)
    raw_prices = ((state.get("price_snapshot") or {}).get("prices") or {})
    prices = {}
    for ticker, value in raw_prices.items():
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            prices[str(ticker)] = value
    # #583 §4: the split basis each of those quotes is stated in, stamped per
    # ticker by the review that froze them. Without it `compare` assumed every
    # quote postdated every split in the map and rebased the exit across all of
    # them — true whenever the quote really is current, and unprovable, which is
    # the whole objection. A state frozen before this evidence existed carries
    # no `observations` and degrades to that same unbounded rebase.
    price_basis = {str(ticker): (row or {}).get("basis_date")
                   for ticker, row in (((state.get("price_snapshot") or {})
                                        .get("observations") or {}).items())
                   if isinstance(row, dict) and row.get("basis_date")}
    recent = [row for row in revisit.scan_recent_exits(revisits, as_of)
              if row.get("revisit_id") not in narratives]
    recent_ids = {row.get("revisit_id") for row in recent}
    due = []
    for row in revisit.scan_due(revisits, resolutions, as_of):
        if row.get("revisit_id") in recent_ids:
            continue  # capture wins while the exit is still inside its reason window
        item = row.get("item") or {}
        prior = narratives.get(row.get("revisit_id")) or {}
        due.append({
            "revisit_id": row.get("revisit_id"), "checkpoint": row.get("checkpoint"),
            "due_date": row.get("due_date"), "item": item,
            "compare": revisit.compare(item, prices, splits=state.get("splits"),
                                       price_basis=price_basis),
            "prior_exit_reason": prior.get("exit_reason"),
            "prior_note": prior.get("note"),
            "prior_capture": prior.get("capture"),
        })
    topn, summary, total = revisit.scan_backlog(revisits, resolutions, prices=prices,
                                                splits=state.get("splits"),
                                                price_basis=price_basis)
    backlog = {"items": topn[:2], "summary": summary, "total": total} if total else None
    return recent, due, backlog, {"enqueued": len(new), "skipped_dup": dup,
                                  "skipped_queue_lines": skipped, "path": queue_path}


def _run_engine(paths, root, args, *, ledger_path=None):
    os.makedirs(root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fomo-review-") as tmp:
        state_path = os.path.join(tmp, "state.json")
        env = dict(os.environ, TR_JSON="1", TR_STATE_OUT=state_path,
                   TR_LEDGER=ledger_path or os.path.join(root, "ledger.jsonl"),
                   # #627: the engine cannot see `--root`, and its market-data
                   # cache is state — same-root-as-the-session, not
                   # same-root-as-the-account. Without this the cache resolved
                   # its own root and an isolated run wrote the user's tickers
                   # into the real `~/.trade-coach`.
                   TR_STATE_ROOT=root,
                   TR_DISPLAY_CURRENCY=card_renderer.default_display_currency(args.language))
        env.pop("TR_PRICES", None)      # only an explicit --prices may inject a price envelope
        if getattr(args, "prices", None):
            env["TR_PRICES"] = os.path.abspath(os.path.expanduser(args.prices))
        # #665: a pass that amends a review the user has already read may only
        # reuse the frame that review was rendered from. Never a CLI flag — it is
        # a property of the lane, set by `cmd_add_cash` alone, so no caller can
        # ask an ordinary `prepare` to answer from an older instant.
        env.pop(market_data.FROZEN_ENV, None)
        if getattr(args, "amending_session", False):
            env[market_data.FROZEN_ENV] = "1"
        previous = _previous_state(root)
        if previous and previous.get("date_end"):
            env["TR_PREV_END"] = str(previous["date_end"])
            # #270: last_state.date_end is THIS run's own date_end when the
            # identical week is reviewed again (a prior finalize already
            # advanced the anchor to it) — this is decided before the engine
            # has parsed the CSV, so it cannot be detected here. Also pass the
            # prev_end that state itself was built from, so the engine can
            # fall back to the closest genuinely-earlier review boundary
            # instead of aliasing prev_end to its own date_end (#166).
            if previous.get("prev_end"):
                env["TR_PREV_PREV_END"] = str(previous["prev_end"])
        # #758: an amending pass reuses the position cap the review being
        # amended was rendered with, the same "reuse the frame" posture
        # #665 already gave the market frame two lines up — `cmd_add_cash`
        # alone sets `frozen_position_cap`, carrying forward whatever
        # `state["max_position_pct"]` this pending session was originally
        # prepared with (references/data-contract.md's own words: a
        # standing cap change "reconciles against" the *next* review, not
        # the one already on screen). Without this, a `set-cap` run in the
        # same message beat as `add-cash` (`flows/first-review.md` step 6
        # collects both) rewrites profile.json first, the recompute below
        # would read the new value, `_candidate_rules` would emit different
        # candidates than the ones already shown, and `_cash_recompute_drift`
        # would refuse on "the rules the user was offered" -- correctly, by
        # its own contract, since the candidates genuinely did move. An
        # ordinary `prepare` carries no such attribute and is unaffected.
        if getattr(args, "amending_session", False) and hasattr(args, "frozen_position_cap"):
            cap_override = args.frozen_position_cap
        else:
            cap_override = _position_cap_override(root)      # #324:標準版單一部位上限,通用預設可被覆寫
        if cap_override is not None:
            env["TR_MAX_POSITION_PCT"] = repr(cap_override)
        for arg_name, env_name in (("driver_map", "TR_DRIVER_MAP"),
                                   ("instrument_map", "TR_INSTRUMENT_MAP"),
                                   ("cash", "TR_CASH")):
            value = getattr(args, arg_name, None)
            if value:
                env[env_name] = value
        run = subprocess.run([sys.executable, str(TRADE_RECAP)] + list(paths), cwd=str(HERE.parent),
                             env=env, capture_output=True, text=True, timeout=args.timeout)
        if run.returncode:
            raise ReviewError(f"engine failed ({run.returncode}): {run.stderr.strip()}")
        try:
            card = json.loads(run.stdout)
            state = session.read_json(state_path)
        except (ValueError, OSError) as exc:
            raise ReviewError(f"engine returned invalid artifacts: {exc}") from exc
        return card, state, run.stderr.strip()


def _active_positions(state):
    return ((state.get("holdings") or {}).get("positions") or {})


def _add_options(language):
    copy = card_renderer.load_copy(language)
    descriptions = copy.get("add_descriptions") or {}
    return [{"value": key, "label": copy["add_choices"][key],
             "description": descriptions[key]}
            for key in ("new_evidence", "planned_tranche", "valuation_change", "price_only", "skip")]


def _generic_options(language):
    copy = card_renderer.load_copy(language)
    labels = copy.get("generic_choices") or {}
    descriptions = copy.get("generic_descriptions") or {}
    return [{"value": key, "label": labels[key], "description": descriptions[key]}
            for key in ("deliberate_plan", "emotional_reaction",
                        "external_constraint", "skip")]


def _exit_consistency_question(card, language):
    """#303: one answerable motive question for the aggregated early-exit
    pattern, or ``None`` when no instrument carried a ``sold_winner_early`` tag.

    The stem is grounded in the exact engine facts the read-only ``[?]`` panel
    would otherwise state (same counts, same named instruments), so a user can
    reply with why those exits happened instead of reading a verdict-free
    observation they cannot answer. The answer contract is the shared motive
    axis; only the durable ``exit_consistency`` event stream is distinct, so
    these classifications never pollute the headline-motive history (#296/#299).
    """
    copy = card_renderer.load_copy(language)
    facts, sentence = card_renderer.exit_consistency_line(card, copy)
    stem = (copy.get("exit_consistency") or {}).get("question")
    if not facts or not sentence or not stem:
        return None
    en = str(language).lower().startswith("en")
    question = f"{sentence} {stem}" if en else f"{sentence}{stem}"
    row = {
        "id": "exit_consistency", "kind": "exit_consistency", "required": True,
        "question": question, "options": _generic_options(language),
        "ticker": facts["instruments"][0]["ticker"], "asked_because": sentence,
        "_importance": 0.0, "_tie": 2,
    }
    row["question_opportunity"] = question_surface.build_opportunity(row, language)
    return row


def _initial_thesis_options(language):
    """Canonical first-review entry-motive choices (#291), localized labels.

    Engine owns the stable codes; the copy layer localizes the labels. `skip`
    is the standard escape consistent with the other kinds.
    """
    copy = card_renderer.load_copy(language)
    labels = copy.get("initial_thesis_choices") or {}
    descriptions = copy.get("initial_thesis_descriptions") or {}
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]}
            for key in ("planned_entry", "momentum_follow", "external_call",
                        "no_clear_thesis", "skip")]


def _exit_options(language, exit_kind):
    copy = card_renderer.load_copy(language)
    labels = (copy.get("exit_choices") or {}).get(exit_kind) or {}
    descriptions = copy.get("exit_descriptions") or {}
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]}
            for key in ("price_target", "thesis_broken", "swap", "anxiety", "other", "skip")]


def _due_options(language):
    copy = card_renderer.load_copy(language)
    labels = copy.get("due_choices") or {}
    descriptions = copy.get("due_descriptions") or {}
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]}
            for key in ("still_valid", "modified", "falsified", "skip")]


def _rule_breach_options(language, can_revise=True):
    copy = card_renderer.load_copy(language)
    labels = copy.get("rule_breach_choices") or {}
    descriptions = copy.get("rule_breach_descriptions") or {}
    keys = ("keep_tracking", "revise_rule", "exception") if can_revise else ("keep_tracking", "exception")
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]} for key in keys]


def _breach_evidence_text(last_breach, language):
    events = (last_breach or {}).get("events") or []
    parts = []
    for event in events:
        ticker = event.get("ticker")
        note = event.get("note")
        if ticker and note:
            parts.append(f"{ticker}: {note}")
        elif ticker or note:
            parts.append(str(ticker or note))
    breach_copy = (card_renderer.load_copy(language).get("breach_evidence") or {})
    if not parts:
        return breach_copy.get("none") or ""
    shown = (breach_copy.get("joiner") or "; ").join(parts)
    extra = int((last_breach or {}).get("event_count") or 0) - len(events)
    if extra > 0:
        shown += (breach_copy.get("more") or "").format(extra=extra)
    return shown


def _rule_breach_questions(problem_stats, history, language):
    if not problem_stats:
        return []
    top_rank = {key: index for index, key in enumerate(problem_stats.get("top") or [])}
    candidates = []
    for rule in problem_stats.get("rules_check") or []:
        breach = rule.get("last_breach") or {}
        rule_id = rule.get("rule_id")
        problem_key = rule.get("problem_key")
        if not rule_id or not breach.get("week"):
            continue
        stats = (problem_stats.get("per_key") or {}).get(problem_key) or {}
        prior = (history or {}).get(rule_id)
        if prior:
            if prior.get("breach_week") == breach.get("week"):
                continue
            worsened = stats.get("trend") == "worse" and (
                prior.get("trend") != "worse"
                or int(stats.get("recent_count") or 0) > int(prior.get("recent_count") or 0)
                or float(stats.get("recent_amount") or 0) > float(prior.get("recent_amount") or 0)
            )
            if not worsened:
                continue
        evidence_text = _breach_evidence_text(breach, language)
        if str(language).lower().startswith("en"):
            question = (f'The ledger recorded an event against rule "{rule.get("text") or rule_id}" '
                        f'in the review period ending {breach.get("week")} ({evidence_text}). '
                        'Which reading is accurate?')
        else:
            question = (f'問題帳在 {breach.get("week")} 這期記到一筆和規矩'
                        f'「{rule.get("text") or rule_id}」相衝的事件（{evidence_text}）。這次該怎麼定性？')
        digest = hashlib.sha256(f"{rule_id}|{breach.get('week')}".encode("utf-8")).hexdigest()[:12]
        rank = top_rank.get(problem_key, len(top_rank) + 1)
        can_revise = problem_key in set(session.PKEY.values())
        candidates.append({
            "id": f"rule_breach_{digest}", "kind": "rule_breach", "required": True,
            "question": question, "options": _rule_breach_options(language, can_revise=can_revise),
            "rule_id": rule_id, "rule_text": rule.get("text"), "problem_key": problem_key,
            "breach_week": breach.get("week"), "evidence": list(breach.get("events") or []),
            "recent_count": int(stats.get("recent_count") or 0),
            "recent_amount": float(stats.get("recent_amount") or 0), "trend": stats.get("trend"),
            "_priority": 1, "_importance": float(max(0, len(top_rank) - rank)), "_tie": 3,
        })
    candidates.sort(key=lambda row: (-float(row.get("_importance") or 0), str(row.get("id"))))
    # #291: the caller (_question_queue) applies RULE_BREACH_LIMIT so it can
    # record the trimmed rows in the selection report. Direct callers see the
    # full ranked list, still bounded by the two-per-key/worsening cadence above.
    return candidates


# ──────────────── the per-period condition check flow (#412 / #434) ────────────────
#
# A condition slot without a check flow is a promise made into a file. The plan
# now names what is due, the agent performs each frozen query and submits the
# result, and the engine — never the agent's prose — decides whether a line was
# crossed. Two exchanges can follow, and only two: a crossing worth one
# two-sided question, and a doubt about whether the threshold still measures
# what it measured. Everything else is recorded and stays quiet.
#
# The two-turn shape mirrors the price envelope (#289, references/price-feed.md):
# `prepare` publishes the request, the agent looks the figures up, and reruns
# `prepare --condition-checks <path>` so the questions can be posed against real
# evidence. Skipping the second pass is a legal, degraded run — the checks still
# arrive with `answers` and are still recorded, only without the conversation.

def _condition_paths(root):
    return (os.path.join(root, "conditions.jsonl"),
            os.path.join(root, "condition_checks.jsonl"))


def _with_condition_line(commitment):
    """The prior commitment with its condition's line identity resolved.

    A root slot row carries no ``line_id`` on disk (it *is* its line's first
    row), and a revised one does. Resolving it once here — with the same
    ``conditions.slot_line_id`` every other reader uses — is what lets the card
    match this period's check back to last period's commitment without owning a
    second copy of the line rules. Matching on ``slot_id`` alone silently
    stopped working after a second revision, because the new check carries the
    newest slot_id while the prior commitment still names the one before it
    (external review, round 1)."""
    condition = (commitment or {}).get("condition")
    if not isinstance(condition, dict) or not condition.get("slot_id"):
        return commitment
    return {**commitment,
            "condition": {**condition, "line_id": conditions.slot_line_id(condition)}}


def _condition_store(root):
    """Both append-only condition stores, read once per review.

    Returns ``(slots, checks, unreadable)`` where ``unreadable`` counts the
    lines each file lost. Corruption is reported, never silently skipped: a
    dropped row would otherwise read as a condition the user never wrote."""
    slots_path, checks_path = _condition_paths(root)
    slots, slots_unreadable = conditions.load_slots(slots_path)
    checks, checks_unreadable = conditions.load_checks(checks_path)
    return slots, checks, {"slots": slots_unreadable, "checks": checks_unreadable}


def _last_check_summary(check):
    """What the plan says about a line's most recent check — four fields, not
    the row. The full history is on disk; what the next lookup needs to know is
    when it last happened, whether it worked, whether it found anything new,
    and what the verdict of record was."""
    if not check:
        return None
    return {"date_end": check.get("date_end"), "lookup_status": check.get("lookup_status"),
            "information_state": check.get("information_state"),
            "final_verdict": check.get("final_verdict")}


def _thesis_cycle_index(positions, thesis_rows, cycle_relinks):
    """``{cycle_id: {"ticker": ..., "live": bool}}`` — every position cycle a
    thesis-linked condition could name, and the one place liveness is decided.

    ``cycle_relinks`` is **required**, with no default, and this function is the
    only place the two row sources are joined. A relink built this session is
    not on disk until finalize writes it, so a caller reading only
    ``_thesis_event_history(root)`` sees a provisional cycle as dead and retires
    a condition the user is still holding. That is what happened: one derivation
    with its input assembled in two places is the same defect as two derivations
    (external review, round 3; docs/development-guide.md section 7). Making the
    argument mandatory is the guard — a new call site cannot forget it quietly.
    Plan-side consumers should use ``_plan_thesis_cycles`` rather than composing
    the arguments themselves.

    A thesis falsifier guards a position (#416 C2). Once that position is fully
    exited the cycle leaves ``holdings.positions``, and with it the reason the
    condition existed: there is nothing left to sell if it triggers. So liveness
    is exactly "this cycle is still held", read from the same rows that already
    own it — no second fold of the thesis event history to disagree with.

    A closed cycle stays in the map, with ``live: False`` and the ticker its
    thesis history recorded. That is not decoration: a condition that retires
    has to be able to say *which* thesis it stopped guarding, and the position
    it names is by then gone from ``positions``.

    The one wrinkle is the provisional cycle id an opening snapshot
    hands out. A later transaction review can relink that holding to its real
    cycle (``thesis.build_snapshot_cycle_relinks``), and a condition
    written against the provisional id would then look closed while the user
    still holds the position. Raw thesis rows carry the mapping permanently, so
    the alias is read from them rather than from the folded state, which a later
    plain thesis update overwrites.

    Every other reader takes what this stamps (``thesis_link`` on a live entry,
    ``condition_slots_retired`` on a closed one), so nothing downstream
    re-derives which thesis a slot belongs to or whether it still stands."""
    # The one composition: persisted history plus this session's relinks, which
    # are real but not yet on disk. Every caller passes the two ingredients and
    # none of them concatenates.
    rows = [row for row in list(thesis_rows or []) + list(cycle_relinks or [])
            if isinstance(row, dict)]
    index = {row["cycle_id"]: {"ticker": ticker, "live": True}
             for ticker, row in (positions or {}).items()
             if isinstance(row, dict) and row.get("cycle_id")}
    # Aliases first, while the map holds only live cycles: a provisional id may
    # only inherit liveness from a cycle that actually has it. A genuinely
    # exited cycle is in no live entry, so nothing here can resurrect it.
    for row in rows:
        provenance = row.get("cycle_provenance")
        origin = provenance.get("from_cycle_id") if isinstance(provenance, dict) else None
        target = row.get("cycle_id")
        if origin and target in index and origin not in index:
            index[origin] = index[target]
    for row in rows:
        cycle_id, ticker = row.get("cycle_id"), row.get("ticker")
        if cycle_id and ticker and cycle_id not in index:
            index[cycle_id] = {"ticker": ticker, "live": False}
    return index


def _plan_thesis_cycles(plan):
    """The cycle index for a plan that already exists — the single way any
    finalize-side consumer obtains one.

    It reads both ingredients from where the plan itself carries them: the
    persisted thesis history under ``state_root``, and this session's relinks
    from ``state_snapshot.thesis_cycle_relinks``, which ``_build_plan`` stamped
    and ``_draft_bundle`` is about to persist. Before this existed, the card
    context re-read only the history, so a cycle that is live *only* via a
    relink read as dead and its condition was dropped from the card join
    (external review, round 3)."""
    thesis_rows, _decisions = _thesis_event_history(plan.get("state_root"))
    return _thesis_cycle_index(
        _active_positions(plan.get("engine_state") or {}), thesis_rows,
        ((plan.get("state_snapshot") or {}).get("thesis_cycle_relinks") or []))


def _condition_lines(root, thesis_cycles=None, previous_date_end=None):
    """``(live, retired, unreadable)`` — the whole standing-condition picture,
    derived once so no later surface has to work any of it out again.

    ``live`` is every line still standing, one entry per *line* (a revised
    criterion is the same line), ordered oldest-last-checked first, each stamped
    with ``line_id``, ``last_check``, and — when it guards a thesis —
    ``thesis_link``. It is deliberately **not** capped here: the cap is about
    how much lookup work one review asks for, and every other consumer (the
    question layer, the card's join, the summary's total) needs the whole live
    set. Scoping any of them to the capped slice makes a line the cap held back
    indistinguishable from one that no longer exists.

    ``retired`` is what left, because its thesis cycle closed (#416 C2): the
    position is fully exited, so there is nothing left to sell if the condition
    triggers. Each entry carries the ticker it guarded, the criterion, and
    whether *this* review is the one where it left — ``announce``, true when the
    line's last check belongs to the immediately preceding review, which is
    exactly the period it was still being looked at. That makes the card's
    retirement sentence an **event**, said once, rather than a state sentence
    that would print for the rest of the user's history.

    Nothing is deleted: the store is append-only and every row is a fact about
    what the user meant when they wrote it."""
    slots, checks, unreadable = _condition_store(root)
    lines = conditions.latest_by_line(slots)
    entries, retired = [], []
    for line_id, slot in lines.items():
        cycle_id = slot.get("thesis_cycle_id")
        last = conditions.last_check_for(checks, slots, slot)
        link = None
        if cycle_id:
            cycle = (thesis_cycles or {}).get(cycle_id) or {}
            if not cycle.get("live"):
                retired.append({
                    "cycle_id": cycle_id, "ticker": cycle.get("ticker"),
                    "line_id": str(line_id), "criterion": slot.get("criterion"),
                    "announce": bool(previous_date_end and last
                                     and last.get("date_end") == previous_date_end)})
                continue
            link = {"cycle_id": cycle_id, "ticker": cycle.get("ticker")}
        # `line_id` is stamped on every entry, including a root row that has
        # none on disk. It is how the card resolves a check back to the
        # condition it belongs to without owning a second copy of the line
        # semantics — one reader, here (external review, round 1). `thesis_link`
        # is stamped for the same reason: the renderer and the question layer
        # read which thesis this guards, they never work it out again.
        entry = {**slot, "line_id": str(line_id), "last_check": _last_check_summary(last)}
        if link:
            entry["thesis_link"] = link
        entries.append((str(last.get("date_end") or "") if last else "", str(line_id), entry))
    # A never-checked line sorts first on the empty string, which is exactly
    # "oldest": nothing has ever been looked up for it.
    entries.sort(key=lambda item: (item[0], item[1]))
    return [entry[2] for entry in entries], retired, unreadable


def _condition_due(root, thesis_cycles=None, previous_date_end=None):
    """``(due, summary, thesis_links, retired_now)`` — what this review publishes.

    The bounded lookup request is ``live[:CONDITION_LOOKUP_CAP]``, and the
    summary is what makes that bound honest: it states the total, the number
    sent, and the number held back, so no reader — agent or card — can mistake
    the list for the whole record.

    A retired line leaves ``lines_total`` and is counted in ``retired_lines``
    instead. Inside the total, the card's own arithmetic would report it as a
    concern "coming back next review" forever, which is the opposite of true.

    ``thesis_links`` and ``retired_now`` both span the full picture rather than
    the capped slice, for the reason ``_condition_lines`` states."""
    live, retired, unreadable = _condition_lines(root, thesis_cycles, previous_date_end)
    due = live[:CONDITION_LOOKUP_CAP]
    summary = {"lines_total": len(live), "due_now": len(due),
               "beyond_cap": max(0, len(live) - len(due)),
               "unmapped_lines": sum(1 for row in live if row.get("tier") == "unmapped"),
               "retired_lines": len(retired),
               "unreadable_slots": unreadable["slots"],
               "unreadable_checks": unreadable["checks"]}
    thesis_links = {row["line_id"]: row["thesis_link"] for row in live if row.get("thesis_link")}
    retired_now = [{"cycle_id": row["cycle_id"], "ticker": row["ticker"],
                    "criterion": row["criterion"]}
                   for row in retired if row["announce"]]
    return due, summary, thesis_links, retired_now


_CHECK_ENVELOPE_KEYS = frozenset({"slot_id", "check"})


def _condition_check_envelopes(raw, where):
    """Normalize the agent's submitted lookups into ``[(slot_id, check_input)]``.

    One shape in both places it can arrive — ``prepare --condition-checks`` and
    ``answers.condition_checks`` — because they are the same envelope submitted
    at two moments, and two shapes would make the equality gate below compare
    apples to pears."""
    if raw is None:
        return []
    rows = raw.get("condition_checks") if isinstance(raw, dict) else raw
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ReviewError(f"{where}.condition_checks must be an array")
    out, seen = [], set()
    for index, row in enumerate(rows):
        label = f"{where}.condition_checks[{index}]"
        if not isinstance(row, dict):
            raise ReviewError(f"{label} must be an object")
        unknown = set(row) - _CHECK_ENVELOPE_KEYS
        if unknown:
            raise ReviewError(f"{label} has unknown fields: " + ", ".join(sorted(unknown)))
        slot_id = str(row.get("slot_id") or "").strip()
        if not slot_id:
            raise ReviewError(f"{label} requires slot_id")
        if slot_id in seen:
            raise ReviewError(f"{label}: two checks for the same condition in one review — "
                              "a period has one result per line, not a running commentary")
        seen.add(slot_id)
        check = row.get("check")
        if not isinstance(check, dict):
            raise ReviewError(f"{label}.check must be the lookup envelope object")
        out.append((slot_id, check))
    return out


def _check_id(session_id, slot_id):
    """Content-addressed, so re-finalizing the same session rebuilds byte-identical
    rows and the idempotent append stays a no-op instead of doubling the record."""
    digest = hashlib.sha256(f"{session_id}|{slot_id}".encode("utf-8")).hexdigest()[:16]
    return "check-" + digest


def _resolve_check_slot(slots, slot_id):
    """The live head of the line ``slot_id`` belongs to.

    A check always evaluates the criterion that is current, and a revision
    changes slot_id while keeping the line — so an envelope naming a superseded
    version still lands on the right line rather than being refused for naming
    an id the user never saw change."""
    by_id = {row["slot_id"]: row for row in slots if isinstance(row, dict) and row.get("slot_id")}
    named = by_id.get(slot_id)
    if named is None:
        return None
    return conditions.latest_by_line(slots).get(conditions.slot_line_id(named)) or named


def _build_condition_checks(slots, checks, envelopes, session_id, date_end, responses=None):
    """Validate every submitted lookup into a durable check row.

    ``responses`` maps a line id to what the user's own answer contributes
    (``user_response`` / ``basis_resolution``). Those reach ``build_check`` as
    keyword arguments, never through the envelope — an envelope carrying either
    is refused there by name — so one row carries the complete story (the
    evidence, the engine's comparison, and what the user said) without the
    agent ever being able to author the last part.

    Dedup is by *line*, not by the ``slot_id`` the envelope happened to name. A
    revision changes slot_id while keeping the line, so a superseded id and the
    live head are two names for one condition; accepting both would append two
    rows for the same (condition, period), and the second would silently win
    every later read (external review, round 1).

    A rejection is the agent's to fix and is reported as such. In particular an
    answer paired with a lookup that did not succeed this period is refused by
    ``build_check`` rather than quietly attached: there is no fresh evidence for
    that answer to be about."""
    responses = responses or {}
    built, seen_lines = [], {}
    for slot_id, envelope in envelopes:
        slot = _resolve_check_slot(slots, slot_id)
        if slot is None:
            raise ReviewError(
                f"condition check names an unknown condition: {slot_id!r}. A check is a result for "
                "a condition the user committed to; there is no such condition in the record")
        line_id = conditions.slot_line_id(slot)
        if line_id in seen_lines:
            raise ReviewError(
                f"two condition checks for the same condition in one review: {seen_lines[line_id]!r} "
                f"and {slot_id!r} are the same line ({line_id!r}) — a re-stated criterion keeps its "
                "line, so those are two names for one condition, and a period has one result per "
                "condition, not a running commentary")
        seen_lines[line_id] = slot_id
        answer = responses.get(line_id) or {}
        try:
            built.append(conditions.build_check(
                envelope, slot=slot,
                previous=conditions.previous_check_for(checks, slots, slot),
                check_id=_check_id(session_id, slot["slot_id"]),
                session_id=session_id or None, date_end=date_end,
                user_response=answer.get("user_response"),
                basis_resolution=answer.get("basis_resolution")))
        except conditions.ConditionError as exc:
            raise ReviewError(f"condition check rejected ({slot['slot_id']}): {exc}") from exc
    return built


def _synthesized_not_checked(slots, checks, due, built, session_id, date_end):
    """One row per due condition nobody submitted a result for.

    Absence is recorded on the ``lookup_status`` axis rather than left as a gap
    in the file, because a gap and a successful quiet period are indistinguishable
    when read back — and "we did not look" is the one thing the record must never
    lose. Silence about a condition is exactly how a user comes to believe a
    tripwire is set."""
    answered = {row["slot_id"] for row in built}
    rows = []
    for entry in due:
        slot = _resolve_check_slot(slots, entry.get("slot_id")) or entry
        if slot.get("slot_id") in answered:
            continue
        try:
            rows.append(conditions.build_check(
                {"lookup_status": "not_checked", "reason": "not submitted this review"},
                slot=slot, previous=conditions.previous_check_for(checks, slots, slot),
                check_id=_check_id(session_id, slot["slot_id"]),
                session_id=session_id or None, date_end=date_end))
        except conditions.ConditionError as exc:            # pragma: no cover - defensive
            raise ReviewError(f"condition check rejected ({slot.get('slot_id')}): {exc}") from exc
    return rows


def _crossing_distance(slot, check):
    """How far this observation sits past the user's line, relative to the line.

    Positive means crossed; a ``near_line`` reading is negative and approaches
    zero as it approaches the line. Sorting descending therefore ranks the
    deepest breach first and, among the not-yet-crossed, the nearest — one
    ordering over both states, so a `met` reading is never out-ranked by a
    `near_line` one."""
    threshold = slot.get("threshold") or {}
    line = card_renderer._finite_number(threshold.get("value"))
    value = card_renderer._finite_number((check.get("observation") or {}).get("value"))
    if line is None or value is None:
        return 0.0
    past = (line - value) if threshold.get("direction") == "below" else (value - line)
    scale = abs(line) or abs(card_renderer._finite_number(slot.get("near_line")) or 0) or 1.0
    return past / scale


def _condition_evidence(check, slot):
    """The one evidence string a question stem may be grounded in: what was found,
    where, and as of when. Never a verdict — the verdict is the engine's field."""
    observation = check.get("observation") or {}
    if "value" in observation:
        found = card_renderer.condition_value(observation["value"],
                                              (slot.get("threshold") or {}).get("unit"))
    else:
        found = str(observation.get("summary") or "").strip()
    source, as_of = observation.get("source"), observation.get("as_of")
    if source and as_of:
        return f"{found} ({source}, as of {as_of})"
    return found


def _condition_question_id(prefix, session_id, line_id):
    digest = hashlib.sha256(f"{prefix}|{session_id}|{line_id}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _options_from_copy(language, register, values):
    """Engine-owned option rows from one copy register. Same shape every other
    kind emits (`value` / `label` / `description`), so a `plain_text` host
    renders them without a special case — and a missing key surfaces as an
    empty label rather than an internal enum value on the user's screen (#262)."""
    copy = card_renderer.load_copy(language)
    labels = copy.get(register) or {}
    descriptions = copy.get(register + "_descriptions") or {}
    return [{"value": value, "label": labels.get(value, ""),
             "description": descriptions.get(value, "")} for value in values]


def _condition_crossing_options(condition_kind, language):
    values = (("yes", "no") if condition_kind == "event" else ("confirmed", "overridden"))
    return _options_from_copy(language, "condition_crossing_choices", values + ("skip",))


def _condition_basis_options(language):
    return _options_from_copy(language, "condition_basis_choices",
                              ("revise_threshold", "revise_metric", "keep", "skip"))


def _condition_questions(built_checks, slots, session_id, language, rejected, thesis_links=None):
    """The at-most-one crossing question and every basis question this review earns.

    A crossing is emitted only from a lookup that succeeded — the engine's own
    comparison came back ``met``/``near_line``, or an event check carried the
    agent's alert. The stem is agent-authored (``question_opportunity``), which
    is the point: a crossing needs one sentence for acting and one for not, and
    a frozen template can only ever write one of them.

    `rejected` collects the crossings that lost the budget, so the plan states
    the deferral rather than the queue quietly shrinking.

    `thesis_links` is what ``_condition_due`` stamped for the conditions that
    guard a still-open thesis. It does two things here: it names the thesis on
    the question, so the adjudication never arrives detached from what it is
    about, and it is the liveness test for a thesis-linked line — a slot whose
    cycle is closed raises nothing even if a check for it was submitted out of
    band, because its position is gone and the answer could not change anything
    (#416 C2)."""
    by_id = {row["slot_id"]: row for row in slots if isinstance(row, dict) and row.get("slot_id")}
    thesis_links = thesis_links or {}
    crossings, basis = [], []
    for check in built_checks:
        slot = by_id.get(check.get("slot_id"))
        if slot is None or check.get("lookup_status") != "ok":
            continue
        line_id = conditions.slot_line_id(slot)
        link = thesis_links.get(line_id)
        if slot.get("thesis_cycle_id") and not link:
            continue
        condition_kind = slot.get("kind") or "numeric"
        evidence = _condition_evidence(check, slot)
        ticker = (link or {}).get("ticker")
        if check.get("basis_alert"):
            note = check["basis_alert"]["note"]
            row = {"id": _condition_question_id("condition_basis", session_id, line_id),
                   "kind": "condition_basis", "required": True,
                   "question": _condition_basis_stem(slot, note, language),
                   "options": _condition_basis_options(language),
                   "slot_id": slot["slot_id"], "line_id": line_id, "ticker": ticker,
                   "condition_kind": condition_kind, "criterion": slot.get("criterion"),
                   "evidence": evidence, "basis_note": note,
                   "_priority": 1, "_importance": 0.5, "_tie": 4}
            row["question_opportunity"] = question_surface.build_opportunity(row, language)
            basis.append(row)
        alerted = bool(check.get("event_alert"))
        if not alerted and check.get("engine_verdict") not in ("met", "near_line"):
            continue
        row = {"id": _condition_question_id("condition_crossing", session_id, line_id),
               "kind": "condition_crossing", "required": True,
               "question": _condition_crossing_stem(slot, check, evidence, language, ticker),
               "options": _condition_crossing_options(condition_kind, language),
               "slot_id": slot["slot_id"], "line_id": line_id, "ticker": ticker,
               "condition_kind": condition_kind, "criterion": slot.get("criterion"),
               "evidence": evidence,
               # An alerted event outranks every numeric reading: the engine can
               # re-derive a number next week, but an occurrence the user never
               # confirmed decays into "nobody said".
               "_priority": 1, "_tie": 0,
               "_crossing_rank": (0 if alerted else 1, -_crossing_distance(slot, check),
                                  str(slot["slot_id"]))}
        row["question_opportunity"] = question_surface.build_opportunity(row, language)
        crossings.append(row)
    crossings.sort(key=lambda row: row["_crossing_rank"])
    for row in crossings:
        row.pop("_crossing_rank", None)
        row["_importance"] = 1.0
    for row in crossings[CONDITION_CROSSING_LIMIT:]:
        rejected.append(_rejection(row.get("id"), "condition_crossing", "condition_crossing_limit"))
    return crossings[:CONDITION_CROSSING_LIMIT] + basis


def _condition_crossing_stem(slot, check, evidence, language, ticker=None):
    """The engine's fallback stem. Deliberately the flattest possible sentence:
    it states the criterion, what came back, and asks. The two-sided version is
    the agent's to author against `question_opportunity` — this one exists so an
    unauthored surface is still answerable, never so it reads well.

    A thesis-linked condition leads with the thesis it guards (#416 C2). The
    fallback carries it too, and not only the authored surface: a host that
    could not bind the private surface is exactly where an adjudication would
    otherwise arrive with no idea what it is about."""
    criterion = slot.get("criterion") or ""
    all_copy = card_renderer.load_copy(language)
    copy = all_copy.get("condition_crossing") or {}
    key = "event" if (slot.get("kind") or "numeric") == "event" else (
        "near_line" if check.get("engine_verdict") == "near_line" else "met")
    template = copy.get(key) or "{criterion} — {evidence}"
    stem = card_renderer._format_copy(template, criterion=criterion, evidence=evidence)
    guard = card_renderer.thesis_guard_sentence({"thesis_link": {"ticker": ticker}}, all_copy)
    return f"{guard} {stem}" if (guard and stem) else stem


def _condition_basis_stem(slot, note, language):
    copy = card_renderer.load_copy(language).get("condition_basis") or {}
    template = copy.get("stem") or "{criterion} — {note}"
    return card_renderer._format_copy(template, criterion=slot.get("criterion") or "", note=note)


def _due_question(row, language, card=None):
    """One 30/60/90 checkpoint question that replays the user's own recorded reason.

    The recalled label comes from the same kind-aware copy table the capture
    question showed and the card rendered — quoting anything else would put
    words in the user's mouth (a reduce answered price_target said 到了減碼點,
    not 到價了). The voice is interpolated, never patched afterwards, so an
    inferred capture can never read as user-confirmed.
    """
    item = row.get("item") or {}
    ticker = item.get("ticker") or "position"
    copy = card_renderer.load_copy(language)
    en = copy["language"] == "en"
    reason = row.get("prior_exit_reason")
    kind = item.get("kind") or "full"
    label = ((copy.get("exit_choices") or {}).get(kind) or {}).get(reason) if reason else None
    voice_guessed = row.get("prior_capture") == "inferred"
    if revisit.is_priced_exit(item):
        base = (f"{ticker} was sold on {item.get('exit_date')} at {item.get('exit_price')}."
                if en else f"{ticker} 你在 {item.get('exit_date')} 以 {item.get('exit_price')} 賣出。")
    else:
        # #485 Slice C: a confirmed disappearance has a date and no fill. Naming
        # a price here — even a cost basis — would put a number in the user's
        # mouth that nobody supplied.
        base = (f"{ticker} left your record on {item.get('exit_date')}, with no sale price recorded."
                if en else f"{ticker} 在 {item.get('exit_date')} 從紀錄中消失，沒有成交價紀錄。")
    recall = ""
    if label:
        if en:
            lead = "At the time I guessed the reason was" if voice_guessed else "At the time you said"
            recall = f'{lead} "{label}".'
        else:
            lead = "我當時猜你是" if voice_guessed else "你當時說是"
            recall = f"{lead}「{label}」。"
    ask = (f"Looking back after {row.get('checkpoint')} days, does that reason still hold?" if en
           else f"{row.get('checkpoint')} 天後回頭看，當時的理由現在還成立嗎？")
    question = " ".join(part for part in (base, recall, ask) if part)
    digest = hashlib.sha256(f"{row.get('revisit_id')}|{row.get('checkpoint')}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"due_{digest}", "kind": "due_revisit", "ticker": ticker,
        "cycle_id": item.get("cycle_id"), "required": True, "question": question,
        "options": _due_options(language), "revisit_id": row.get("revisit_id"),
        "checkpoint": row.get("checkpoint"), "due_date": row.get("due_date"),
        "exit_date": item.get("exit_date"), "exit_price": item.get("exit_price"),
        "exit_kind": item.get("kind"), "currency": item.get("currency") or "USD",
        "swaps": item.get("swaps") or [], "compare": row.get("compare"),
        "prior_exit_reason": reason, "prior_note": row.get("prior_note"),
        "_importance": _exit_importance(item, card), "_tie": 2,
    }


def _format_notional(value, currency):
    value = float(value or 0)
    rendered = f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"
    return f"{currency or 'USD'} {rendered}"


def _exit_importance(item, card):
    """Compare exit amounts in the engine's aggregate currency when FX is available."""
    notional = revisit._notional(item)
    meta = (card or {}).get("currency_meta") or {}
    currency = str(item.get("currency") or "USD").upper()
    aggregate = str(meta.get("aggregate_currency") or currency).upper()
    if not meta.get("mixed") or currency == aggregate:
        return abs(notional)
    factor = (meta.get("fx") or {}).get(currency)
    try:
        return abs(notional * float(factor)) if factor is not None else abs(notional)
    except (TypeError, ValueError):
        return abs(notional)


QUOTE_CLIP = 80  # word-safe character budget for a replayed thesis quote in a stem


def _clip_quote(text):
    """Collapse whitespace and clip a quote near QUOTE_CLIP at a word boundary.

    Spaced scripts back off to the last complete word; CJK text has no spaces
    and keeps the raw character budget. A clipped quote always ends with an
    ellipsis so it never reads as the user's complete sentence.
    """
    text = " ".join(str(text or "").split())
    if len(text) <= QUOTE_CLIP:
        return text
    clipped = text[:QUOTE_CLIP]
    head = clipped.rpartition(" ")[0]
    if head:
        clipped = head
    return clipped.rstrip() + "…"


def _thesis_recall(prior, language, frame):
    """One lead sentence replaying the user's own recorded thesis (#226).

    Same voice contract as `_due_question`: only text the user confirmed reads
    as "you said"; an inferred thesis that was never confirmed stays a guess
    (first-review contract: never present it as user-confirmed). The quote is
    the stored `why` verbatim (clipped, never paraphrased) and the date uses
    the same event-date resolution the thesis fold orders by. A missing or
    corrupt record returns None so the caller keeps today's plain stem.

    frame "add" returns a lowercase clause the add stem prefixes with its
    ticker; frame "entry" returns a standalone sentence for the exit capture.
    """
    if not isinstance(prior, dict):
        return None
    quote = _clip_quote(prior.get("why"))
    if not quote:
        return None
    date = thesis._event_date(prior)
    guessed = prior.get("maturity") == "inferred"
    if str(language).lower().startswith("en"):
        quoted = f'"{quote}"'
        if frame == "entry":
            lead = f"At entry on {date}" if date else "At entry"
            return (f"{lead} I guessed your thesis was {quoted}." if guessed
                    else f"{lead} you said {quoted}.")
        if guessed:
            return (f"on {date} I guessed your thesis was {quoted}." if date
                    else f"earlier I guessed your thesis was {quoted}.")
        return (f"on {date} you said {quoted}." if date
                else f"earlier you said {quoted}.")
    quoted = f"『{quote}』"
    if frame == "entry":
        if guessed:
            return (f"進場時（{date}）我猜你的論點是{quoted}。" if date
                    else f"進場時我猜你的論點是{quoted}。")
        return (f"你進場時（{date}）說的是{quoted}。" if date
                else f"你進場時說的是{quoted}。")
    if guessed:
        return (f"我在 {date} 猜你的論點是{quoted}。" if date
                else f"我先前猜你的論點是{quoted}。")
    return (f"你在 {date} 說過{quoted}。" if date
            else f"你先前說過{quoted}。")


def _asked_because(basis, language):
    """Localized display reason a question was picked (#226, former option C).

    Only vetted display strings leave the engine; the raw basis key remains an
    internal sort detail that `_question_queue` strips with `_importance`.
    """
    table = (card_renderer.load_copy(language).get("asked_because") or {})
    return table.get(basis) or None


# #714: what answering changes, per question kind. `asked_because` above says
# why this row ranked into the queue; this says what the user's answer buys.
# Two different questions, and only the second one earns the turn.
#
# The set is deliberately not "every kind". It is the wired half of
# evals/run_episodes.py's QUESTION_CONSUMERS — the kinds whose answer provably
# reaches the card or the next review's state. `initial_thesis` and
# `exit_consistency` are absent because nothing reads their answers at all
# (QUESTION_CONSUMERS' KNOWN_UNWIRED, owned by #429); writing them an effect
# sentence would be the product promising a consequence it cannot deliver,
# which is worse than the silence. tests/test_review_v2.py pins this set
# against QUESTION_CONSUMERS so the two cannot drift apart.
ANSWER_EFFECT_KINDS = ("add_thesis", "headline_motive", "revisit", "due_revisit",
                       "rule_breach", "condition_crossing", "condition_basis")


def _answer_effect(kind, language):
    """Localized sentence naming the visible consequence of answering (#714)."""
    if kind not in ANSWER_EFFECT_KINDS:
        return None
    table = (card_renderer.load_copy(language).get("answer_effect") or {})
    return table.get(kind) or None


def _exit_question(item, language, card=None, prior=None):
    ticker = item.get("ticker") or "position"
    kind = item.get("kind") or "full"
    priced = revisit.is_priced_exit(item)
    # An unpriced exit has no exit amount. _notional still returns the recorded
    # cost basis so importance ranking has a magnitude, but that number is not
    # proceeds and never reaches the stem or the stored `exit_notional`.
    notional = revisit._notional(item) if priced else None
    amount = _format_notional(notional, item.get("currency")) if priced else None
    # #226: replay the entry thesis inside the stem. Without a prior thesis the
    # joined parts stay byte-identical to the historical plain stem.
    recall = _thesis_recall(prior, language, "entry")
    if str(language).lower().startswith("en"):
        action = "fully exited" if kind == "full" else "substantially reduced"
        base = (f"{ticker} was {action} on {item.get('exit_date')} for about {amount}."
                if priced else
                f"{ticker} left your record on {item.get('exit_date')} — you confirmed it was "
                "sold, and no sale price was recorded.")
        ask = "What mainly drove that decision?"
        question = " ".join(part for part in (base, recall, ask) if part)
    else:
        action = "全部出清" if kind == "full" else "大幅減倉"
        base = (f"{ticker} 在 {item.get('exit_date')} {action}，出場金額約 {amount}。"
                if priced else
                f"{ticker} 在 {item.get('exit_date')} 從紀錄中消失，你確認是賣掉了，但沒有成交價紀錄。")
        ask = "當時主要是什麼理由？"
        question = "".join(part for part in (base, recall, ask) if part)
    digest = hashlib.sha256(str(item.get("revisit_id")).encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"exit_{digest}", "kind": "revisit", "ticker": ticker,
        "cycle_id": item.get("cycle_id"), "required": True, "question": question,
        "options": _exit_options(language, kind), "revisit_id": item.get("revisit_id"),
        "exit_kind": kind, "exit_date": item.get("exit_date"),
        "exit_price": item.get("exit_price"), "shares_sold": item.get("shares_sold"),
        "shares_before": item.get("shares_before"), "currency": item.get("currency") or "USD",
        "exit_notional": notional,
        "asked_because": _asked_because("exit_notional", language),
        "_importance": _exit_importance(item, card), "_tie": 0,
    }


def _normalized_position_cost(cost, currency, card):
    """A native-currency cost basis converted into the card's aggregate
    currency (#664), or ``None`` when the review's own resolved FX map has no
    rate for it.

    Reads the same frozen ``currency_meta`` (#649's shared conversion result)
    ``_exit_importance`` already reads, rather than a second acquisition of
    the aggregate lookup. Never an identity/raw fallback: a book the engine
    reports ``mixed`` already has a complete rate for every held currency —
    ``trade_recap.usd_view`` fails the whole review closed otherwise — so
    ``None`` here only guards a caller whose card/state did not go through
    that gate, which is exactly the shape a unit test exercises directly.

    The aggregate currency itself has exactly one reader,
    ``card_renderer._currency`` -- its ``or "USD"`` fallback is the review's
    own convention for "no aggregate declared," not this function's caller's
    currency. Resolving a missing ``aggregate_currency`` key to the position's
    *own* currency would make every position trivially "already the
    aggregate" and silently turn off normalization for the whole book instead
    of naming the gap -- the same identity-factor failure #649 removed from
    the aggregate reader itself, reintroduced one layer up.
    """
    meta = (card or {}).get("currency_meta") or {}
    currency = str(currency or "USD").upper()
    aggregate = card_renderer._currency(card or {})
    if not meta.get("mixed") or currency == aggregate:
        return abs(float(cost or 0))
    factor = (meta.get("fx") or {}).get(currency)
    if factor is None:
        return None
    try:
        return abs(float(cost) * float(factor))
    except (TypeError, ValueError):
        return None


def _ticker_importance(card, state, ticker):
    for row in card.get("ticker_diagnosis") or []:
        if row.get("ticker") == ticker and row.get("impact") is not None:
            return abs(float(row["impact"])), "pnl_impact"
    pos = (_active_positions(state).get(ticker) or {})
    try:
        cost = float(pos.get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0, "unknown"
    # #664: the position_cost fallback used to compare this raw native-currency
    # magnitude directly against other tickers' raw magnitudes -- silently
    # wrong on a mixed-currency book, where the same face value in TWD and USD
    # differ by the exchange rate. Normalize it exactly like the ticker_diagnosis
    # branch above already is (its rts/held/last_px are usd_view's own output).
    normalized = _normalized_position_cost(cost, pos.get("currency"), card)
    if normalized is None:
        # Never fall back to the raw amount: refuse the ranking for this
        # candidate rather than let an unconverted magnitude compete.
        return None, "fx_unavailable"
    return normalized, "position_cost"


def _initial_thesis_id(cycle_id):
    return "initial_thesis_" + hashlib.sha256(str(cycle_id).encode("utf-8")).hexdigest()[:12]


def _initial_thesis_question(ticker, pos, cost, card, state, language, recalled=None):
    """One first-review entry-thesis capture (#291) grounded in ticker + cost.

    The stem cites the engine-owned cost basis (the deterministic per-position
    magnitude the engine stores; live-price weights are not persisted). Both the
    stem number and the stored `cost_basis` come from the same value so the card
    context and the recorded event cannot drift.

    ``recalled`` is a ``_recalled_entry_statement`` result: the user's own words
    about this ticker, recorded through ``consider`` no later than this cycle's
    entry. When one exists the stem stops asking the user to reconstruct a
    motive from memory and shows them what they actually said, dated, so the
    question becomes confirm-or-correct (#636). The words are inserted verbatim
    and never truncated, translated or paraphrased — the same rule
    schemas/decision-context.schema.json puts on storing them.

    The choice set, the event and the question budget are deliberately
    unchanged: this is the same question asked better in the same slot, not an
    extra one. What a different answer branches to is unchanged too, so the
    recall cannot turn into the #429 shape of a question nothing consumes.
    """
    cycle_id = pos.get("cycle_id")
    currency = str(pos.get("currency") or "USD")
    amount = _format_notional(cost, currency)
    importance, basis = _ticker_importance(card, state, ticker)
    because = _asked_because(basis, language)
    said = (recalled or {}).get("reason") or (recalled or {}).get("why_now")
    if str(language).lower().startswith("en"):
        stem = f"You are holding {ticker} at a cost basis of about {amount}. "
        if said:
            stem += (f"On {recalled['created'].isoformat()} you said: “{said}” "
                     "Was that the thesis you entered on?")
        else:
            stem += "When you first entered this position, what was your thesis?"
        if because:
            stem += f" (Asked because {because}.)"
    else:
        stem = f"你持有 {ticker}，成本約 {amount}。"
        if said:
            stem += (f"{recalled['created'].isoformat()} 你說：「{said}」"
                     "當初進場的論點就是這個嗎？")
        else:
            stem += "當初第一次進場時，你的論點是什麼？"
        if because:
            stem += f"（問這題是因為{because}）"
    row = {
        "id": _initial_thesis_id(cycle_id), "kind": "initial_thesis", "ticker": ticker,
        "cycle_id": cycle_id, "required": True, "question": stem,
        "options": _initial_thesis_options(language),
        "cost_basis": cost, "currency": currency,
        "_importance": importance, "_importance_basis": basis, "_tie": 1,
    }
    if said:
        # Provenance for the agent and for anything reading the plan: which
        # stored statement the stem quoted, so a reader can check the quote
        # against its source rather than trusting the rendered stem. Not the
        # receipt — `question_presented` accepts only source and digest, never
        # content, and this must not be read as travelling there.
        row["recalled_statement"] = {
            "evaluation_id": recalled.get("evaluation_id"),
            "created": recalled["created"].isoformat(),
            "quoted": said,
        }
    if because:
        row["asked_because"] = because
    row["question_opportunity"] = question_surface.build_opportunity(row, language)
    return row


CAPTURE_LIMIT = 2  # at most two exit-reason captures per session (c6850f0 contract)


def _rejection(id_, kind, reason, cycle_id=None):
    """One question_selection.rejected row with a uniform shape (#291).

    `id` is the question id whenever the candidate became a real (if unqueued)
    question; `cycle_id` is the stable join key present whenever a cycle is
    known, so a QA tool always has one reliable key to cross-reference.
    """
    return {"id": id_, "kind": kind, "cycle_id": cycle_id, "reason": reason}


def _question_queue(card, state, active, previous_state, language, recent_exits=None, thesis_states=None,
                    due_revisits=None, problem_stats=None, rule_history=None, horizon_markers=None,
                    route=None, missing_thesis_positions=None, tier=None,
                    condition_questions=None, evaluation_recall=None):
    """Return (queue, selection_report). The report states, plan-internally, how
    the route's density band was filled: the eligible/selected counts, why the
    queue fell short of the route minimum, and every candidate rejected with its
    reason (#291). It is QA/agent-facing and never rendered on the card."""
    policy = QUESTION_POLICY.get(route) or QUESTION_POLICY["weekly_review"]
    # #306: a structural/empty first review is an opening structural check, not a
    # behavioral interrogation — suppress the question band entirely (0 required
    # questions) so a thin first file never triggers the 3–5 question string.
    # Scoped to first_review by design: a returning weekly review keeps its
    # perishable revisit / exit / due-checkpoint questions no matter how thin the
    # new activity is (there the tier is advisory, never a suppressor).
    if route == "first_review" and tier in ("structural", "empty"):
        policy = {"min": 0, "max": 0}
    report = {"route": route, "min": policy["min"], "max": policy["max"],
              "eligible": 0, "selected": 0, "shortfall_reason": None, "rejected": []}
    rejected = report["rejected"]
    # A position snapshot can establish structure and thesis baselines, but it
    # contains no action history.  Do not turn the generic fallback into a
    # fabricated motive question, and do not replay exit/problem questions from
    # an unrelated older ledger into this opening portfolio check.
    if route == "snapshot_review":
        return [], report
    positions = _active_positions(state)
    by_ticker = {ticker: row for ticker, row in positions.items()}
    del previous_state  # retained in the call contract for older adapters
    # #416: no longer read in here — its one use was stamping a per-question
    # field on queue rows that no flow read, now removed. state_snapshot's
    # plural roster is the surface flows actually consume, so the parameter
    # stays for call-contract compatibility with existing positional callers.
    del horizon_markers
    thesis_states = thesis_states or active
    candidates = []
    # Exit-reason capture is the only perishable question: its 14-day window
    # cannot be backfilled, while a skipped due checkpoint or an unanswered add
    # legitimately returns next review. Perishable questions therefore outrank
    # everything regardless of notional — but take at most CAPTURE_LIMIT slots
    # so one busy week cannot turn the review into an exit interrogation.
    for index, item in enumerate(recent_exits or []):
        # #226: the stem itself replays the entry thesis for this cycle, so the
        # agent never has to resolve the attached IDs from disk.
        prior = thesis_states.get(item.get("cycle_id")) or {}
        question = _exit_question(item, language, card, prior)
        if index >= CAPTURE_LIMIT:
            rejected.append(_rejection(question["id"], "revisit", "capture_limit",
                                       cycle_id=question.get("cycle_id")))
            continue
        question["prior_thesis_id"] = prior.get("thesis_id")
        question["prior_event_id"] = prior.get("last_event_id") or prior.get("event_id")
        question["_priority"] = 0
        candidates.append(question)
    for row in due_revisits or []:
        candidates.append(_due_question(row, language, card))
    for index, item in enumerate(card.get("thesis_questions") or []):
        ticker = item.get("ticker")
        pos = by_ticker.get(ticker) or {}
        cycle_id = pos.get("cycle_id")
        old = active.get(cycle_id)
        decision_cursor = pos.get("decision_cursor")
        # The add question id is a pure function of the cursor key, so derive it
        # once up front — the two dedup rejections below then carry the same
        # question id (plus cycle_id) that the emitted row would have used.
        cursor_key = decision_cursor or f"{cycle_id}|legacy|{index}"
        add_id = "add_" + hashlib.sha256(cursor_key.encode("utf-8")).hexdigest()[:12]
        if old and decision_cursor and old.get("decision_cursor") == decision_cursor:
            rejected.append(_rejection(add_id, "add_thesis", "already_captured", cycle_id=cycle_id))
            continue
        if old and not decision_cursor and old.get("maturity") == "testable":
            rejected.append(_rejection(add_id, "add_thesis", "already_captured", cycle_id=cycle_id))
            continue
        importance, basis = _ticker_importance(card, state, ticker)
        # #226: quote the cycle's own recorded thesis in the stem and say why
        # this question was picked. Without a prior thesis (or a mapped basis)
        # each part degrades independently to today's plain sentence.
        recall = _thesis_recall(old, language, "add")
        because = _asked_because(basis, language)
        if str(language).lower().startswith("en"):
            question = (f"For {ticker}: {recall} Was the add based on new evidence, "
                        "a pre-planned tranche, a valuation change, or only the lower price?"
                        if recall else
                        f"For {ticker}, was the add based on new evidence, a pre-planned tranche, "
                        "a valuation change, or only the lower price?")
            if because:
                question += f" (Asked because {because}.)"
        else:
            tail = (item.get("question") or
                    "這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？")
            question = (f"{ticker} {recall}{tail}" if recall
                        else (item.get("question") or f"{ticker} {tail}"))
            if because:
                question += f"（問這題是因為{because}）"
        row = {
            "id": add_id, "kind": "add_thesis", "ticker": ticker,
            "cycle_id": cycle_id, "required": True, "question": question,
            "options": _add_options(language),
            "prior_thesis_id": (old or {}).get("thesis_id"),
            "prior_event_id": (old or {}).get("last_event_id") or (old or {}).get("event_id"),
            "decision_cursor": decision_cursor,
            "_importance": importance, "_importance_basis": basis, "_tie": 1,
        }
        if because:
            row["asked_because"] = because
        prior_context = None
        prior_quote = _clip_quote(old.get("why")) if isinstance(old, dict) else None
        if prior_quote:
            prior_context = {
                "text": prior_quote,
                "voice": "inferred" if old.get("maturity") == "inferred" else "user_confirmed",
            }
        row["question_opportunity"] = question_surface.build_opportunity(
            row, language, prior_thesis=prior_context
        )
        candidates.append(row)
    # #291: first-review entry-thesis capture. Source is the same missing-thesis
    # set the inferred-skeleton path consumes; a cycle already covered by an
    # add_thesis question above needs no second motive question, and a cycle that
    # already carries a real (draft/testable) thesis is a no-duplicate rejection.
    # The over-INITIAL_THESIS_LIMIT rows are held in `initial_overflow`, not
    # rejected yet: a below-min queue prefers these grounded rows over the
    # generic motive backfill (refill loop below).
    initial_overflow = []
    if route == "first_review" and policy["max"]:
        add_covered = {row.get("cycle_id") for row in candidates
                       if row.get("kind") == "add_thesis"}
        missing_cycles = {entry.get("cycle_id") for entry in (missing_thesis_positions or [])
                          if entry.get("cycle_id")}
        initial_candidates = []
        for ticker, pos in sorted(positions.items()):
            cycle_id = pos.get("cycle_id")
            if not cycle_id or cycle_id in add_covered:
                continue
            existing = active.get(cycle_id)
            if existing and existing.get("maturity") in ("testable", "draft"):
                rejected.append(_rejection(_initial_thesis_id(cycle_id), "initial_thesis",
                                           "has_existing_thesis", cycle_id=cycle_id))
                continue
            if cycle_id not in missing_cycles:
                continue  # carries an inferred thesis already; nothing new to capture
            cost = card_renderer._finite_number(pos.get("cost"))
            if cost is None or cost <= 0:
                continue  # cannot ground the stem in a concrete magnitude
            candidate = _initial_thesis_question(
                ticker, pos, cost, card, state, language,
                recalled=_recalled_entry_statement(evaluation_recall, ticker, cycle_id))
            if candidate.get("_importance") is None:
                # #664: the "largest cost" ranking compares normalized amounts
                # only. When the aggregate FX map has no rate for this
                # position's currency, refuse to rank it rather than let its
                # raw native-currency magnitude compete against one already
                # normalized -- the same fail-closed posture #649 gives the
                # aggregate reader this candidate's importance came from.
                rejected.append(_rejection(candidate["id"], "initial_thesis",
                                           "fx_unavailable", cycle_id=cycle_id))
                continue
            initial_candidates.append(candidate)
        initial_candidates.sort(key=lambda row: (-float(row.get("_importance") or 0), str(row.get("id"))))
        candidates.extend(initial_candidates[:INITIAL_THESIS_LIMIT])
        initial_overflow = initial_candidates[INITIAL_THESIS_LIMIT:]
    # #412: a crossing or a doubted basis is evidence the user themselves asked
    # to be watched for, so it enters the ranking beside a rule breach rather
    # than behind the motive questions. Built by _build_plan (it needs the
    # store), budgeted there too — this only ranks what arrives.
    candidates.extend(condition_questions or [])
    breach_questions = _rule_breach_questions(problem_stats, rule_history, language)
    candidates.extend(breach_questions[:RULE_BREACH_LIMIT])
    for row in breach_questions[RULE_BREACH_LIMIT:]:
        rejected.append(_rejection(row.get("id"), "rule_breach", "rule_breach_limit",
                                   cycle_id=row.get("cycle_id")))
    # #291 P2-A: a below-min queue earns its extra slots through durable
    # information gain first — refill from the grounded initial-thesis overflow
    # (importance order) before falling back to the generic motive backfill
    # below. The non-shortfall case is unchanged: a queue already at the route
    # min keeps thesis questions capped at two and the leftover overflow is a
    # genuine over-limit trim.
    while len(candidates) < policy["min"] and initial_overflow:
        candidates.append(initial_overflow.pop(0))
    for row in initial_overflow:
        rejected.append(_rejection(row.get("id"), "initial_thesis", "initial_thesis_limit",
                                   cycle_id=row.get("cycle_id")))
    # #303: the aggregated early-exit pattern becomes one answerable motive
    # question when it fired. It is a grounded candidate (real tickers/counts),
    # not a min-only backfill, so it competes for a slot up to the route max —
    # "answerable when the queue has room". It ranks at the bottom of its tier
    # (_importance 0.0) so it never displaces a higher-signal question; when the
    # queue is already full it is trimmed below and the read-only `[?]` panel
    # states the same facts as an explicit observation instead. Counting it
    # here, before the generic headline backfill, keeps a grounded question
    # ahead of an invented one (#291).
    exit_consistency = _exit_consistency_question(card, language)
    if exit_consistency is not None:
        candidates.append(exit_consistency)
    # #291: route-min-aware min-backfill (was `if not candidates:`); weekly
    # min=1 makes it exactly equivalent to the prior behavior, while
    # first-review min=3 lets the motive question backfill when 1-2 grounded
    # candidates exist.
    if len(candidates) < policy["min"]:
        top_hole = (card.get("top_holes") or [{}])[0]
        top = top_hole.get("dim") or state.get("headline_dim")
        # An insufficient or quiet history can trigger no hole and carry
        # headline_dim=None (#227). With no dimension to anchor the motive
        # question to, asking would fabricate one; an empty queue is the same
        # legal contract the snapshot route already returns.
        if top is not None:
            top_label = card_renderer.localized_dimension(top, language)
            question = (f"What mainly drove the behavior behind {top_label}?" if str(language).lower().startswith("en")
                        else f"這次「{top}」背後，主要是事先規劃、情緒反應，還是外部限制？")
            row = {"id": "headline_motive", "kind": "headline_motive", "required": True,
                   "question": question, "options": _generic_options(language),
                   "_importance": 0.0, "_tie": 2}
            # Reuse the same deterministic, engine-owned fact selector as
            # candidate-rule grounding.  The selected top-hole dimension must
            # have both a renderable fact and at least one citable ticker; if
            # either is absent, preserve the dimension-only safe fallback.
            # This changes presentation grounding only—not ranking, choices,
            # calculations, or the canonical answer contract.
            top_dim_id = card_renderer.dimension_id(top)
            grounding_card = card
            top_raw = top_hole.get("raw")
            if isinstance(top_raw, dict):
                grounding_card = dict(card)
                other_dims = [dim for dim in (card.get("dims_raw") or [])
                              if (not isinstance(dim, dict)
                                  or card_renderer.dimension_id(dim.get("dim")) != top_dim_id)]
                # Keep other dimensions for facts such as diversification's
                # sizing weights, while making this exact selected hole the
                # authoritative row for its own dimension.
                grounding_card["dims_raw"] = other_dims + [top_raw]
            top_facts = card_renderer.rule_grounding_facts(grounding_card, top_dim_id)
            grounding = card_renderer.localized_rule_grounding(
                top, language, grounding_card
            )
            tickers = (top_facts or {}).get("tickers") or []
            if (top_hole.get("dim") is not None and grounding and tickers
                    and isinstance(tickers[0], str) and tickers[0]):
                row["ticker"] = tickers[0]
                row["asked_because"] = grounding
                row["question"] = f"{grounding} {question}"
            row["question_opportunity"] = question_surface.build_opportunity(
                row, language,
                headline_dimension={"id": top, "label": top_label},
            )
            candidates.append(row)
    # Priority tiers are semantic, then amount/rank resolves within a tier:
    # perishable exit capture -> unqualified chosen-rule breach -> due/add motive.
    candidates.sort(key=lambda row: (int(row.get("_priority", 2)),
                                     -float(row.get("_importance") or 0),
                                     int(row.get("_tie") or 0), str(row.get("id"))))
    # `eligible` counts everything that survived to the sort; the over-max rows
    # below are then also recorded in `rejected`, so `eligible + len(rejected)`
    # double-counts them. Each field is individually correct — do not sum them.
    report["eligible"] = len(candidates)
    queue = candidates[:policy["max"]]
    for row in candidates[policy["max"]:]:
        rejected.append(_rejection(row.get("id"), row.get("kind"), "over_max_capacity",
                                   cycle_id=row.get("cycle_id")))
    report["selected"] = len(queue)
    if len(queue) < policy["min"]:
        report["shortfall_reason"] = "insufficient_eligible_candidates"
    for row in queue:
        row.pop("_importance", None)
        row.pop("_importance_basis", None)
        row.pop("_tie", None)
        row.pop("_priority", None)
    return queue, report


def _candidate_rules(card, state, language):
    candidates = []
    seen = set()
    source = list(card.get("candidate_rules") or [])
    for hole in card.get("top_holes") or []:
        source.append({"dim": hole.get("dim"), "rule": hole.get("lens_rule"),
                       "applicable": hole.get("applicable"), "raw": hole.get("raw")})
    metrics = state.get("metrics") or {}
    cap_override = (state or {}).get("max_position_pct")  # #324:sizing 規矩文案帶用戶自訂上限(engine 已回填 state)
    for row in source:
        dim = row.get("dim") or row.get("kind")
        dim_id = card_renderer.dimension_id(dim)
        metric = DIM_METRIC.get(dim_id)
        raw = row.get("raw") or {}
        # One applicability gate covers both direct candidate rows and
        # lens-rule fallbacks synthesized from top_holes.  Existing committed
        # rules are deliberately outside this function and remain reconcilable.
        if (not dim or row.get("applicable", True) is False
                or raw.get("applicable", True) is False
                or not card_renderer._dimension_is_applicable(card, dim)
                or dim in seen or metric not in metrics):
            continue
        rule = card_renderer.localized_rule(dim, language, cap=cap_override) or row.get("rule")
        if not rule:
            continue
        seen.add(dim)
        candidate = {"id": f"candidate_{len(candidates)}", "dim": dim_id, "rule": rule,
                     "metric_key": metric, "goal": "down"}
        # #248: engine-owned grounding ties the reusable rule template to this
        # period's actual positions (tickers + behavior fact). Omitted when the
        # dimension has nothing citable; the canonical rule text tracked in
        # rules.jsonl stays generic either way.
        grounding = card_renderer.localized_rule_grounding(dim, language, card)
        if grounding:
            candidate["grounding"] = grounding
        candidates.append(candidate)
        if len(candidates) == 3:
            break
    return candidates


def _severity_rank_key(card, dim_id):
    """The same severity x tier-weight key `trade_recap._rank_holes` uses to
    order the card's headline dimensions (#63 single fact source), looked up
    by canonical `dim_id`.

    Not a new score: `HEADLINE_TIER_W` and each dimension's `severity`/`tier`
    already live on `card['dims_raw']`. Returns None when the dimension has
    no raw entry or no numeric severity -- `_candidate_comparison` must never
    guess a rank for data it cannot see.
    """
    for row in card.get("dims_raw") or []:
        if not isinstance(row, dict) or card_renderer.dimension_id(row.get("dim")) != dim_id:
            continue
        severity = row.get("severity")
        if not isinstance(severity, (int, float)):
            return None
        weight = trade_recap.HEADLINE_TIER_W.get(row.get("tier", 2), 0.7)
        return float(severity) * weight
    return None


def _candidate_comparison(candidates, card, language):
    """#302(c): one engine-generated sentence explaining why the candidates
    other than the top-ranked one rank lower this period.

    Derived strictly from the existing severity x tier-weight ranking (the
    same key `_rank_holes` uses to pick `top_holes`) -- no new score is
    invented here. Consumed only by `card_plan.candidate_comparison` /
    `cmd_preview`'s echo of it, i.e. the interaction layer where the agent
    presents the candidate-rule choice (`references/interaction-delivery.md`);
    `card_renderer` never reads this field, so it cannot reach the rendered
    card.

    Degrades to None (no sentence -- never an empty or dangling one) whenever
    an honest ranking claim cannot be made: fewer than two candidates, a
    candidate whose severity cannot be located in `dims_raw`, or a tie at the
    top. The sentence explains *ranking* only; it never claims the top
    candidate is the objectively right rule for the user.
    """
    if len(candidates) < 2:
        return None
    ranked = []
    for candidate in candidates:
        key = _severity_rank_key(card, candidate.get("dim"))
        if key is None:
            return None  # can't compare fairly when any candidate's rank is unknown
        ranked.append((key, candidate))
    ranked.sort(key=lambda pair: -pair[0])
    top_key, top = ranked[0]
    others = ranked[1:]
    if top_key <= others[0][0]:
        return None  # tie at the top: no honest "ranked lower" claim to make
    top_label = card_renderer.localized_dimension(top.get("dim"), language)
    other_labels = [card_renderer.localized_dimension(c.get("dim"), language) for _, c in others]
    comparison = (card_renderer.load_copy(language).get("candidate_comparison") or {})
    others_text = (comparison.get("joiner") or ", ").join(other_labels)
    return (comparison.get("line") or "").format(
        top=top_label, others=others_text) or None


def _problem_snapshot(root, state):
    """Fold the problem book and rules into review-ready stats.

    Offline and read-only: prepare must be able to show trends and rule verdicts
    without mutating the book (appending happens at finalize via projections).
    Assembly lives in problems.snapshot so the CLI and this path cannot drift.

    #292: also feeds this period's not-yet-appended draft problem_events (plus
    its date_end) so `rules_check[*].draft_breach` can flag an in-progress
    breach of a just-committed rule — still read-only, since draft_events are
    only compared, never written to problems.jsonl here.
    """
    payload = problems.snapshot(os.path.join(root, "problems.jsonl"),
                                os.path.join(root, "rules.jsonl"),
                                today=_review_date(state).isoformat(), span_aware=True,
                                draft_events=state.get("problem_events"),
                                draft_week=state.get("date_end"),
                                muted_ids=_muted_rule_ids(root))
    if not payload["events_n"] and not payload["marks_n"]:
        return None
    return payload


def _horizon_markers_all(state, thesis_states, active_cycle_ids, recent_exits):
    """Join stored theses with engine-owned position/exit dates and rank mirrors.
    Every triggered marker, ranked but NOT truncated.

    Reductions remain active positions. Only a recent full exit receives an
    `exit_date`; otherwise horizon.scan would silently turn a reduction into a
    closed thesis. Ranking uses position cost or exit notional and is fixed here,
    never invented by the renderer.

    `_horizon_markers` (below) is this function's HORIZON_MARKER_LIMIT-bounded
    slice for the card's attention budget. #446 cut 1's verdict rows are built
    from THIS untruncated list, never from that slice: the cap exists to keep
    what the agent is asked about small, not to decide which periods get a
    durable record, and writing from the truncated slice would repeat #444's
    round 3-4 defect verbatim -- nothing downstream may be scoped to a display
    cap's slice.
    """
    as_of = state.get("date_end")
    if not as_of:
        return []
    by_cycle = {row.get("cycle_id"): row for row in thesis_states if row.get("cycle_id")}
    positions = _active_positions(state)
    costs = {}
    for row in positions.values():
        cycle_id = row.get("cycle_id")
        if not cycle_id:
            continue
        try:
            costs[cycle_id] = abs(float(row.get("cost") or 0))
        except (TypeError, ValueError):
            costs[cycle_id] = 0.0
    scan_rows = []
    importance = {}
    source = {}
    for cycle_id in active_cycle_ids:
        prior = by_cycle.get(cycle_id)
        if not prior:
            continue
        scan_rows.append({"cycle_id": cycle_id, "ticker": prior.get("ticker"),
                          "horizon": prior.get("horizon"), "maturity": prior.get("maturity")})
        importance[cycle_id] = costs.get(cycle_id, 0.0)
        source[cycle_id] = "active_thesis"
    for item in recent_exits or []:
        if item.get("kind") != "full":
            continue
        cycle_id = item.get("cycle_id")
        prior = by_cycle.get(cycle_id)
        if not prior:
            continue
        scan_rows.append({"cycle_id": cycle_id, "ticker": item.get("ticker") or prior.get("ticker"),
                          "horizon": prior.get("horizon"), "maturity": prior.get("maturity"),
                          "exit_date": item.get("exit_date")})
        importance[cycle_id] = abs(revisit._notional(item))
        source[cycle_id] = "recent_exit"
    try:
        markers = horizon.scan(scan_rows, str(as_of))
    except (TypeError, ValueError):
        return []
    for marker in markers:
        marker["source"] = source.get(marker.get("cycle_id"))
        marker["_importance"] = importance.get(marker.get("cycle_id"), 0.0)
    markers.sort(key=lambda marker: (0 if marker.get("kind") == "exit_too_fast" else 1,
                                     -float(marker.get("_importance") or 0),
                                     str(marker.get("ticker") or "")))
    for marker in markers:
        marker.pop("_importance", None)
    return markers


def _horizon_markers(state, thesis_states, active_cycle_ids, recent_exits):
    """`_horizon_markers_all`'s display-bounded slice: at most
    HORIZON_MARKER_LIMIT entries, for the card's attention budget and the
    question queue. See that function for the join/ranking rules."""
    return _horizon_markers_all(state, thesis_states, active_cycle_ids, recent_exits)[:HORIZON_MARKER_LIMIT]


def _horizon_verdict_rows(plan, session_id):
    """Behavior verdict rows this review's horizon judgments produce (#446 cut
    1), built from `_horizon_markers_all`'s UNTRUNCATED result -- never the
    display-capped `state_snapshot.horizon_markers` slice `_horizon_markers`
    returns.

    Every ingredient here is read from what `_build_plan` already stamped into
    the plan (`engine_state`, `state_snapshot.thesis_states`,
    `state_snapshot.recent_exits`) -- never re-derived from disk or assembled
    a second, differently-sourced way (development-guide.md section 7).
    `_active_positions` is a pure accessor of the stamped `engine_state`, so
    `active_cycle_ids` here is exactly what `_build_plan` itself used to build
    the display slice, not an independent reconstruction that could disagree
    with it on an edge case.

    Skipped entirely on `snapshot_review`, the same route `_horizon_markers`
    itself is skipped for: a position snapshot carries no review history for a
    said-vs-done judgment to compare against.
    """
    if plan.get("route") == "snapshot_review":
        return []
    state = plan.get("engine_state") or {}
    date_end = state.get("date_end")
    if not date_end:
        return []
    snapshot = plan.get("state_snapshot") or {}
    thesis_states = snapshot.get("thesis_states") or []
    by_cycle = {row.get("cycle_id"): row for row in thesis_states if row.get("cycle_id")}
    cycle_ids = [row.get("cycle_id") for row in _active_positions(state).values()
                if row.get("cycle_id")]
    recent_exits = snapshot.get("recent_exits") or []
    markers = _horizon_markers_all(state, thesis_states, cycle_ids, recent_exits)
    exit_dates = {item.get("cycle_id"): item.get("exit_date") for item in recent_exits
                 if item.get("kind") == "full"}
    rows = []
    for marker in markers:
        cycle_id = marker.get("cycle_id")
        prior = by_cycle.get(cycle_id) or {}
        window_to = exit_dates.get(cycle_id) if marker.get("exited") else date_end
        row = verdicts.build_horizon_verdict(
            marker, row_id=prior.get("event_id"), session_id=session_id,
            date_end=date_end, window_to=window_to)
        if row is not None:
            rows.append(row)
    return rows


def _missing_thesis_entry(ticker, position):
    """One uncovered-cycle row: the join keys plus the engine-owned provenance
    the agent needs to ground an inferred thesis without reading engine_state.
    origin is forwarded only when the ingestion path recorded it — the engine
    must not fabricate provenance it does not own."""
    entry = {"ticker": ticker, "cycle_id": position.get("cycle_id")}
    if position.get("origin"):
        entry["origin"] = position["origin"]
    return entry


def _authoring_contract(route):
    """Surface the artifact-authoring contract in the Review Plan so the agent
    self-checks before submitting instead of rediscovering field rules from
    engine source at runtime (#251).

    Every vocabulary here derives from the constant validation enforces
    (thesis.MATURITY_VALUES, thesis.INFERENCE_ENUMS, card_renderer.ALLOWED_NARRATIVE);
    a contract test pins the equivalence so this cannot drift into a second
    source of truth.

    #773: ``allowed_fields`` alone answered "will `validate_narrative` accept
    this key" but not "will this route's card ever show it" — a field could
    be accepted (some, like ``rule_rationale``, deliberately so, only to keep
    a documented-safe ``finalize`` retry on an old committed session from
    failing validation on its own stored narrative) and still consumed by no
    renderer on this route, discoverable only by diffing the authored text
    against the rendered card. ``not_rendered_on_this_route`` names that gap
    explicitly, from the same ``card_renderer.narrative_fields_not_rendered``
    the renderer itself is built from, so the two cannot drift apart again.
    """
    contract = {
        "thesis_updates": {
            "required_from_agent": ["cycle_id", "why", "exit_trigger"],
            "engine_prefilled_for_missing_cycles": {
                "ticker": "from missing_thesis_positions",
                "maturity": "inferred",
            },
            "optional_fields": ["horizon", "stop", "target_size", "driver",
                                "source_type", "source_name", "source_confidence",
                                "emotion", "emotion_inferred",
                                "confidence", "confidence_inferred"],
            "maturity_values": sorted(thesis.MATURITY_VALUES),
            "horizon_values": "card_plan.horizon_ids, or null",
            "inference_enums": {key: sorted(values)
                                for key, values in thesis.INFERENCE_ENUMS.items()},
            "engine_owned_identity": ["thesis_id", "event_id", "revises", "decision_cursor"],
        },
        "narrative": {
            "required": ["headline", "mirror"],
            "allowed_fields": sorted(card_renderer.ALLOWED_NARRATIVE),
            "not_rendered_on_this_route": card_renderer.narrative_fields_not_rendered(route),
            "digit_ban": ("no digits and no spelled-out numeric magnitudes in any field; "
                          "numbers come only from engine artifacts"),
            "honesty_keys": "cover exactly card_plan.required_honesty_keys",
            "unprompted_gaps": ("coverage gaps the engine chose not to ask about "
                                "(e.g. missing_thesis_positions) may appear only as "
                                "neutral coverage facts; do not frame them as the "
                                "user's negligence, and do not make them the central "
                                "judgment of the headline or mirror"),
            "synthesis": ("optional (#345); two to three sentences giving the period's "
                          "single most important cross-section judgment as the card's "
                          "closing block, after the next-step rule; it must connect facts "
                          "that otherwise sit in separate sections into one point of view "
                          "and must never restate a number, tile, or sentence already on "
                          "the card; omit it entirely, rather than a placeholder, when "
                          "nothing rises to that synthesis"),
        },
    }
    if route == "snapshot_review":
        contract["thesis_updates"]["engine_prefilled_for_missing_cycles"]["source_confidence"] = "candidate"
        contract["thesis_updates"]["route_locked"] = {"maturity": "inferred",
                                                      "source_confidence": "candidate"}
    return contract


def _narrative_fields_discarded(narrative, route):
    """The authored ``narrative`` keys this route's card will not render
    (#773) -- the ones ``_authoring_contract``'s ``not_rendered_on_this_route``
    already named, intersected with what the agent actually supplied. Empty
    unless the agent authored one of them anyway (the contract marker is
    informational, not a rejection: ``validate_narrative`` still accepts the
    field, per the acceptance criteria's non-goal of not re-litigating the
    routing decision itself)."""
    if not isinstance(narrative, dict):
        return []
    not_rendered = set(card_renderer.narrative_fields_not_rendered(route))
    return sorted(set(narrative) & not_rendered)


def _flag_unpriced_exits(card, recent_exits, due_revisits, exit_backlog):
    """#485 Slice C: name the exits recorded without a fill, so the figures that
    skip them say so.

    A disappearance the user confirmed as a sale is recorded as a
    ``position_absence``: the date is known, the fill is not, and the engine may
    not invent one. Win rate, payoff and exit discipline are computed from the
    transaction file, so such an exit is excluded from all three *structurally*
    — nothing needs to remember to skip it. What does need a mechanism is
    saying so: excluding a real exit from the numbers without naming it is the
    silent-partial-denominator defect the owner ruled against (exclude and
    disclose). ``absence_id`` is the marker rather than a missing price, because
    only an absence is genuinely outside those figures; a trade-sourced row with
    a damaged price is a different problem and must not borrow this sentence.
    """
    items = list(recent_exits or [])
    items += [row.get("item") or {} for row in due_revisits or []]
    items += list((exit_backlog or {}).get("items") or [])
    tickers = sorted({str(row.get("ticker")) for row in items
                      if isinstance(row, dict) and row.get("ticker") and row.get("absence_id")})
    if not tickers:
        return card
    card = dict(card)
    honesty = [row for row in card.get("honesty_ledger") or []
               if row.get("key") != "unpriced_exits"]
    honesty.append({"key": "unpriced_exits", "status": "excluded",
                    "data": {"tickers": tickers, "count": len(tickers)}})
    card["honesty_ledger"] = honesty
    return card


def _flag_prior_commitment_breach(card, problem_stats, prior_commitment):
    """#292: surface an in-period breach of the rule the user committed to last time.

    `problems.check_rules` only writes `last_breach` once a *finalized* review
    boundary (a committed mark) closes over the breaching event; a breach that
    happens inside the still-open current period never crosses that boundary
    until the *next* review commits its own mark. `_rule_breach_questions`
    reads only `last_breach`, so it silently skips a same-period violation of
    a rule the user just promised to keep — the card ships with zero
    acknowledgment (#292). This reads the already-computed, read-only
    `draft_breach` (problems.py's additive draft-window judgment, keyed off
    this period's not-yet-appended problem_events) and, on a match against the
    rule `prior_commitment` names, appends one honesty_ledger entry. That entry
    flows into `required_honesty_keys` unchanged, so the existing
    narrative.honesty gate in `_draft_bundle` forces the agent to author one
    sentence about it — no new checker, and `last_breach`/`held_streak`/
    `verdict` are never touched.

    problem_key + text is a two-part match because one problem_key can carry
    more than one historical rule line (revisions); `session.PKEY` maps the
    commitment's metric_key the same way `session.py`'s finalize path derives
    a rules.jsonl row's problem_key from that same commitment, so the join is
    exact for the immediately-following review regardless of revision history.

    Returns `card` unchanged when nothing matches. On a match, returns a *new*
    dict with a freshly built honesty_ledger list — mirrors `_gate_current_view`
    (review.py) building a new list and reassigning rather than appending to
    the existing list object in place, so no caller-held reference is mutated.
    """
    if not prior_commitment or not problem_stats:
        return card
    problem_key = session.PKEY.get(prior_commitment.get("metric_key"))
    if not problem_key:
        return card
    rule_text = prior_commitment.get("rule")
    if not rule_text:
        return card
    for rule in problem_stats.get("rules_check") or []:
        if (rule.get("problem_key") != problem_key
                or rule.get("text") != rule_text
                or not rule.get("draft_breach")):
            continue
        ledger = list(card.get("honesty_ledger") or [])
        ledger.append({
            "key": "prior_commitment_breach",
            "status": "draft",
            "data": {"problem_key": problem_key, "week": rule["draft_breach"].get("week")},
        })
        return {**card, "honesty_ledger": ledger}
    return card


def _declared_prices_unavailable(args):
    """The one reader of ``--prices-unavailable`` (#623, extended to ``consider``
    by #629).

    The escape hatch is a *declaration*, so it has to say something: an empty or
    one-character value would let "I looked" be asserted without naming what was
    looked at, which is the shape of the miss this gate exists to make visible.
    Two commands accept the flag and they must agree on what counts as a
    declaration — a second copy of this rule would let one of them accept a
    claim the other refuses.

    Returns the trimmed declaration, or ``None`` when the flag was not sent.
    """
    declared = (getattr(args, "prices_unavailable", None) or "").strip()
    if not declared:
        return None
    if len(declared) < 3:
        raise ReviewError("--prices-unavailable must name the market-data sources you "
                          "checked, so a dead end is a stated fact rather than a claim")
    return declared


def _price_feed_status(source, *, supplied=None, unavailable_declared=None,
                       command="prepare", request_path="input.price_feed.request"):
    """Agent-visible price availability for this run (#289).

    ``source`` is any mapping carrying ``price_provenance`` / ``price_request``
    — the engine card on the review lane, and the pair
    :func:`_consider_price_feed_status` assembles on the ``consider`` lane
    (#629). One builder, two routes: the manifest, the ``recovery`` block and
    the instruction below are stated once, so a route cannot grow its own
    slightly different wording for the same gap. Only two things genuinely
    differ between the routes — which subcommand takes the envelope back, and
    where the caller finds the manifest in the payload it is holding — and both
    are parameters rather than a second sentence.

    ``provenance`` records where the prices came from; ``request`` is the
    machine-readable manifest of what is still unpriced, present only when
    coverage is incomplete. A degraded run stays visible instead of quietly
    dropping the portfolio-level return.

    ``recovery`` (#623) records what happened about that manifest, because the
    two states behind an identical degraded card were previously
    indistinguishable: the agent looked and the sources publish nothing, or the
    agent never looked. `flows/first-review.md` step 0 requires recovery
    *before* a degraded card is delivered, so the second one is not a
    disclosure — it is evidence a required step was skipped, and the card said
    the same sentence either way. ``attempted`` comes from whether an envelope
    or an explicit "nothing published" declaration actually arrived, never
    inferred from ``provenance.mode``: an envelope too broken to frame anything
    still leaves that mode ``unavailable``, and reading the attempt off it would
    call a real attempt a skipped one.
    """
    provenance = (source or {}).get("price_provenance")
    request = (source or {}).get("price_request")
    if not provenance and not request:
        return None
    status = {"provenance": provenance}
    if request:
        status["request"] = request
        if unavailable_declared:
            recovery = {"attempted": True, "outcome": "declared_unavailable",
                        "checked": str(unavailable_declared)}
        elif supplied is not None:
            recovery = {"attempted": True, "outcome": "supplied"}
        else:
            recovery = {"attempted": False, "outcome": "not_attempted"}
        status["recovery"] = recovery
        # An empty ticker list means either an FX-only recovery or a benchmark
        # gap.  Those are distinct actions: an FX-only request must not send an
        # agent after an irrelevant close merely to make an envelope non-empty.
        missing_tickers = request.get("tickers") or []
        missing_currencies = request.get("currencies") or []
        if missing_currencies and not missing_tickers and not request.get("benchmarks"):
            status["next_action"] = (
                "currency conversion coverage is incomplete for the FX rates in "
                f"{request_path}.currencies. Look up only those FX rates in a recognized "
                "market-data source, transcribe them into the `fx` block of the envelope "
                f"documented in references/price-feed.md, and rerun {command} with --prices "
                "<path>. Never invent a rate or treat a missing conversion as identity")
        else:
            # Held instruments and benchmarks fail differently: unpriced holdings
            # remove P&L itself, unpriced benchmarks only remove the vs-market
            # segment. Saying which one is missing keeps the agent from treating an
            # optional enrichment as a blocker, or the reverse.
            blocking = bool(missing_tickers)
            scope = (f"the instruments in {request_path}.tickers — without them there is no "
                     "unrealized P&L or portfolio-level return" if blocking else
                     f"the benchmark symbols in {request_path}.benchmarks — holdings are "
                     "priced, but the benchmark comparison stays unavailable without them")
            status["next_action"] = (
                f"price coverage is incomplete for {scope}. Look those closes up in a recognized "
                "market-data source, transcribe them into the envelope documented in "
                f"references/price-feed.md, and rerun {command} with --prices <path>. Never invent a "
                "price, and never read a missing price as a delisting or as a zero return")
    return status


def _consider_price_feed_status(*, requested, last_px, missing_fx=(),
                                fx_required=False, feed, agent_supplied,
                                unavailable_declared, bundle):
    """The same recovery kit, for the ``consider`` lane (#629).

    ``prepare`` has always reported a complete manifest when it could not price
    the book — which instruments are missing, which envelope takes them back,
    and what to rerun. ``consider`` in the identical condition reported one enum
    value, ``basis.valuation_basis: "unpriced"``, and named nothing: no missing
    instrument, no envelope pointer, no instruction, while ``consider --prices``
    already existed. So the agent had no way to know that recovery was the move,
    and the answer it relayed weighted a forward-looking decision on cost.

    Deliberately not a second builder. ``price_feed.provenance`` and
    ``price_feed.build_request`` are the existing producers of the two halves,
    and :func:`_price_feed_status` is the existing assembler of the kit around
    them; this function only supplies this route's own inputs. A hand-written
    second manifest is the mirrored surface docs/maintainer-guide.md forbids,
    and the wording would drift on the first edit to either.

    ``agent_supplied`` records an actual ``--prices`` envelope.  The engine's
    successful resolver also returns a feed-shaped mapping, but that must not
    make engine-retrieved prices appear to have been supplied by an agent.

    ``requested`` is what *this answer* needed priced — the book's own held
    instruments plus the premise ticker — rather than a review's wider universe
    of benchmarks and history: naming a benchmark here would send the agent
    after closes no consequence figure uses.
    """
    priced = {ticker for ticker in (last_px or {}) if ticker in set(requested)}
    # The canonical valuation frame has already decided whether an aggregate
    # conversion is needed and, if it is, which rate is absent.  Recovery must
    # surface that fact, not recreate a currency universe from all holdings or
    # from an unnormalised premise (#629).
    unresolved_fx = sorted({str(code).upper() for code in (missing_fx or ()) if code})
    provenance = price_feed.provenance(
        mode=("agent_feed" if agent_supplied else
              ("engine_fetch" if priced else "unavailable")),
        feed=feed,
        # The bundle's own stated degradations, classified into the stable
        # reason code price_feed.classify_error owns. The raw text never enters
        # a record: `provenance` is emitted beside a content-addressed row and a
        # volatile string there would make two same-cause runs look different.
        error="; ".join(
            filter(None, (f"{gap['code']}: {gap.get('detail') or ''}"
                          for gap in (getattr(bundle, "gaps", None) or ())))) or None,
        requested=requested, priced=priced,
        fx_mode=("missing" if unresolved_fx else
                 ("feed" if fx_required and agent_supplied else
                  ("engine_fetch" if fx_required else "not_needed"))),
        as_of=(feed or {}).get("as_of"))
    status_source = {"price_provenance": provenance}
    if provenance["coverage"]["missing"] or unresolved_fx:
        status_source["price_request"] = price_feed.build_request(
            tickers=requested, currencies=unresolved_fx,
            missing=provenance["coverage"]["missing"],
            reason=provenance["error"])
    return _price_feed_status(status_source, supplied=feed,
                              unavailable_declared=unavailable_declared,
                              command="consider", request_path="price_feed.request")


def _consider_sector_display(consequence, language):
    """The user-facing name of each snapshot's largest sector (#746).

    ``consequence.before/after.max_sector`` is the engine's own label, and
    ``trade_recap.SECTOR_MAP`` stores those as zh literals. The card has
    localized them since #387 through ``card_renderer.localized_sector``; the
    ``consider`` response never did, so an ``--language en`` answer obliged to
    state ``max_sector_pct`` had no English name for the sector that figure
    measures.

    Emitted beside the evaluation row rather than onto it: this is a pure
    function of a value the row already freezes plus the caller's language, and
    the row seeds ``_evaluation_id``. Putting it on the row would make one trade
    evaluated in two languages two different evaluations.

    A label with no mapping is a user-supplied driver-map category and passes
    through unchanged — ``localized_sector`` owns that rule, and this function
    does not second-guess it. A snapshot with no largest sector contributes no
    key, so an empty result means "no sector to name", never "not localized".
    """
    display = {}
    for side in ("before", "after"):
        sector = (consequence.get(side) or {}).get("max_sector")
        if sector:
            display[side] = card_renderer.localized_sector(sector, language)
    return display


def _consider_disclosures_display(consequence, language):
    """Localized companion text for each machine-readable disclosure key
    (#739), the same shape as ``_consider_sector_display`` right above.

    ``consequence.disclosures`` is the engine's own stable English
    snake_case list (``consequence.DISCLOSURES``) -- ``required_coverage``
    anchors a claim at it by array position
    (references/trade-consequence.md, "What the answer owes"), so the list
    itself must stay exactly what it is. What was missing is a human
    sentence beside it in the language the caller actually asked for: a
    ``--language zh-TW`` answer had nothing but the raw English key to draw
    on, and an agent facing that gap tended to quote the key itself rather
    than translate it on the spot (#739's own evidence). This resolves each
    key through ``copy/<locale>.json``'s own ``disclosures`` table -- the
    one place localized product wording is allowed to live
    (docs/output-language.md rule 2) -- rather than duplicating a
    translation at every call site that happens to emit this list.

    A key with no copy entry cannot happen on ``en``/``zh-TW`` (held to the
    same key-parity gate every other copy table already passes,
    ``test_locale_copy_files_keep_key_parity``); ``.get(key, key)`` is a
    fail-soft fallback for an unrecognized or a stored retired key
    (``consequence.RETIRED_DISCLOSURES``) read back through ``--resolve``,
    so a genuinely unmapped code still reads as itself instead of raising
    mid-answer.

    Returns ``{}`` when the evaluation carries no disclosure at all, so an
    absent key reads as "nothing to add", matching ``sector_display``'s own
    convention right above.
    """
    keys = consequence.get("disclosures") or ()
    table = card_renderer.load_copy(language).get("disclosures") or {}
    return {key: table.get(key, key) for key in keys}


def _consider_recovery_tickers(rows, premise_ticker, _excluded_holdings=()):
    """The only tickers a ``consider`` recovery may ask the agent to price.

    ``rows`` are the usable current holdings copied from the canonical
    PortfolioBasis; the premise is the only prospective position this command
    can add.  ``excluded_holdings`` deliberately does not widen that set.  An
    integrity warning, unusable quantity, absent cost, or an unmatched-history
    orphan is a disclosure/provenance fact, not a missing close a market-data
    lookup can repair.

    The unused third argument is intentional: the caller has both facts side
    by side, and passing exclusions through this boundary makes the forbidden
    legacy union directly mutation-testable without creating a second recovery
    taxonomy or persistence surface.
    """
    tickers = {symbols.canonical_ticker(row.get("ticker"))
               for row in rows or () if isinstance(row, dict)}
    if premise_ticker:
        tickers.add(symbols.canonical_ticker(premise_ticker))
    return {ticker for ticker in tickers if ticker}


def _consider_canonical_fx_recovery(basis):
    """Return the canonical frame's FX recovery facts, if that frame exists.

    Single-currency books intentionally retain their legacy valuation receipt,
    so the absence of a typed frame means no conversion request.  A typed
    frame is the sole source for both whether conversion is applicable and
    which rate is actually missing; recovery does not infer either from row
    currencies or an omitted premise currency.
    """
    current_book = getattr(basis, "current_book", {}) if basis is not None else {}
    frame = current_book.get("valuation_manifest") if isinstance(current_book, dict) else None
    if not isinstance(frame, dict):
        return False, []
    coverage = frame.get("coverage")
    if not isinstance(coverage, dict):
        return False, []
    return True, sorted({str(code).upper() for code in coverage.get("missing_fx", ()) if code})


# What answering the cash question buys, in engine vocabulary rather than
# numbers. The agent turns these into one short sentence so the ask is informed
# rather than blind (#357 owner note, 2026-07-29).
CASH_ANCHOR_UNLOCKS = ("account_level_return", "annualized_return", "cash_drag")


def _cash_anchor_status(state, route, cadence):
    """Whether this review has an accounting anchor, and what to do when it has none (#357).

    The gap this closes is an asymmetry, not a missing convenience. *With* an
    anchor the engine demands a disclosure — ``acct_perf_basis`` enters
    ``card_plan.required_honesty_keys`` — and *without* one it demanded nothing
    at all, so the single condition the agent had to notice unprompted was the
    only one the Review Plan never stated. Five recurrences, three of them on
    real data; the fifth passed every mechanical gate because "the agent decided
    not to ask" was a legitimate receipt outcome.

    Deliberately **not** symmetric with ``input.price_feed.request`` in timing,
    which is the neighbouring precedent for "the engine reports what it is
    missing and how to supply it". A price gap is recovered *before* the user is
    shown anything, because every price-dependent number on the card is degraded
    without it. A missing anchor degrades exactly one pillar and changes nothing
    else — proven by the recompute gate in :func:`cmd_add_cash` — so the ask
    belongs *after* the card, where the user can see what answering would buy
    (owner ruling 2026-07-30; #507 design principle 1, "show value before asking
    for optional work"). ``ask_after`` carries that difference rather than
    leaving two adjacent gaps looking interchangeable.

    ``not_applicable`` is a positive claim rather than an absent key, the same
    reason ``ux_receipt``'s card-free routes must record ``card
    not_applicable``: "no anchor was owed here" is checkable, and a light-tier
    week that simply had no entry would be indistinguishable from one the engine
    forgot to classify. It is what keeps the light-tier promise mechanical
    instead of depending on a flow file asking after a tier check three lines
    later (#358 post-merge finding 1).
    """
    if route == "snapshot_review":
        return {"status": "not_applicable", "reason": "snapshot_envelope"}
    if route == "test_drive":
        return {"status": "not_applicable", "reason": "test_drive"}
    if (cadence or {}).get("tier") == "light":
        return {"status": "not_applicable", "reason": "light_tier"}
    cash = (state or {}).get("cash")
    if not isinstance(cash, dict):
        return {"status": "not_applicable", "reason": "no_cash_pipeline"}
    source = cash.get("source")
    if source == "anchored":
        return {"status": "anchored", "source": source}
    by_currency = cash.get("by_currency") or {}
    unanchored = sorted(code for code, row in by_currency.items()
                        if not (row or {}).get("reliable"))
    return {"status": "partial" if source == "partial" else "absent",
            "source": source,
            "unanchored_currencies": unanchored,
            "unlocks": list(CASH_ANCHOR_UNLOCKS),
            # The one field that says this gap is not the price gap beside it.
            "ask_after": "card_presented",
            "next_action": (
                "this review has no cash balance to anchor the account on, so account-level "
                "return, annualized return and cash drag stay unavailable while every other "
                "number is unaffected. Do not ask for it before the card: show the card, and "
                "in that same message ask the user for the account's current cash balance in "
                + (", ".join(unanchored) or "the account's own currency")
                + " — stating what it unlocks and that skipping keeps the holdings-only view. "
                "Accept either an absolute currency amount or a percentage of the account's "
                "total value (cash plus current position market value — never position value "
                "alone). If they answer with a percentage, state that denominator in plain "
                "words and get it confirmed once before converting — never more than one "
                "clarification round-trip for this ask, and never compute the dollar figure "
                "yourself. Then run add-cash --session-id <id> --cash "
                "'{\"currency\":\"<CUR>\",\"amount\":<number>,\"as_of\":\"<date>\"}' for an "
                "absolute amount, or --cash '{\"currency\":\"<CUR>\",\"percent_of_total\":<0-100>,"
                "\"as_of\":\"<date>\"}' for a percentage (#662) — the engine converts against "
                "this session's own frozen position value and returns the derivation in "
                "anchor_conversion, which you must show the user rather than silently apply. "
                "Continue on the session add-cash returns; it reuses this session's frozen "
                "prices rather than fetching new ones, and refuses if the facts underneath the "
                "card moved. Never guess a balance")}


def _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint, nonce, persist,
                recent_exits=None, ledger_ingest=None,
                due_revisits=None, exit_backlog=None, problem_stats=None,
                submitted_condition_checks=None, supplied_prices=None,
                prices_unavailable=None):
    positions = _active_positions(state)
    cycle_ids = [row.get("cycle_id") for row in positions.values() if row.get("cycle_id")]
    session_id = ledger.session_id_from_state(state, f"{nonce}|{route}|{language}")
    thesis_rows, decision_rows = _thesis_event_history(root)
    thesis_states = thesis.reconstruct_states(thesis_rows, decision_rows, cycle_ids)
    cycle_relinks = []
    if route != "snapshot_review":
        cycle_relinks = thesis.build_snapshot_cycle_relinks(
            thesis_states, positions, session_id, state.get("date_end")
        )
        if cycle_relinks:
            thesis_states = thesis.reconstruct_states(
                thesis_rows + cycle_relinks, decision_rows, cycle_ids
            )
    active_rows = [row for row in thesis_states
                   if row.get("cycle_id") in set(cycle_ids) and row.get("position_status") != "closed"]
    active = {row.get("cycle_id"): row for row in active_rows}
    by_cycle = {row.get("cycle_id"): row for row in thesis_states}
    horizon_markers = ([] if route == "snapshot_review" else
                       _horizon_markers(state, thesis_states, cycle_ids, recent_exits))
    rule_history = {} if route == "snapshot_review" else _rule_breach_history(root)
    headline_motive_events = ([] if route == "snapshot_review" else
                              _headline_motive_history(root))
    missing = [_missing_thesis_entry(ticker, row)
               for ticker, row in sorted(positions.items()) if row.get("cycle_id") not in active]
    previous = _previous_state(root)
    completed_reviews = _completed_review_count(root, exclude_session_id=session_id)
    cadence = _cadence(route, state.get("date_end"), previous)
    if route != "snapshot_review":
        # #284: freeze the monthly vs-market decision into the card artifact
        # (precedent: _apply_display_currency). Snapshot cards keep their
        # existing route-level suppression — no second gate layered on top.
        card = {**card, "vs_market_gate": _vs_market_gate(
            root, state.get("date_end"), exclude_session_id=session_id)}
    # #292: read-only, additive check against this period's draft problem_events —
    # must run after problem_stats/previous are both available and before the
    # honesty_ledger is read into required_honesty_keys below, so a match cannot
    # be silently dropped from the agent's authoring gate.
    card = _flag_prior_commitment_breach(card, problem_stats, (previous or {}).get("commitment"))
    # Same placement rule as the line above: an exit recorded without a fill must
    # reach required_honesty_keys, or the card ships with the exclusion unstated.
    card = _flag_unpriced_exits(card, recent_exits, due_revisits, exit_backlog)
    required_honesty_keys = [x.get("key") for x in card.get("honesty_ledger") or []]
    if card_renderer.vs_market_suppressed(card):
        # The ledger keeps recording what the engine triggered; the agent is
        # only asked to author sentences whose host lines render this month.
        required_honesty_keys = [key for key in required_honesty_keys
                                 if key not in card_renderer.VS_MARKET_HONESTY_KEYS]
    review_tier = _review_tier(state)
    # Carry the engine decision inside engine_state too, so the deterministic
    # renderer can frame a thin first file as an opening structural check without
    # re-deriving thresholds (#306). state_snapshot keeps the agent-facing copy.
    state["review_tier"] = review_tier
    flow_path = f"flows/{route.replace('_', '-')}.md"
    if route == "first_review" and review_tier["tier"] in ("structural", "empty"):
        # #306: a thin first file is an opening structural check, not a full
        # behavioral review — send the agent to the structural flow.
        flow_path = "flows/first-review-structural.md"
    # #412/#434: what this review asks the agent to look up, bounded, plus the
    # results the agent already submitted (the second `prepare` pass). Snapshot
    # reviews are excluded for the same reason they queue no questions: a
    # position snapshot carries no review history to reconcile against.
    # #416 C2: which position cycles a thesis-linked condition is still standing
    # on, derived once here and read everywhere else from what the plan stamps
    # (`thesis_link` on a live entry, `condition_slots_retired` on a closed one).
    thesis_cycles = _thesis_cycle_index(positions, thesis_rows, cycle_relinks)
    condition_due, condition_summary, thesis_links, condition_retired = (
        ([], None, {}, []) if route == "snapshot_review"
        else _condition_due(root, thesis_cycles, (previous or {}).get("date_end")))
    # #317/#429: reconcile any `consider` evaluation still open against what
    # the local ledger actually shows happened. `_ledger_trade_events` reads
    # only real, dated trade events — never `_rows_from_ledger`'s synthesized
    # anchor rows, which exist for FIFO/cost-basis pricing `consider` needs
    # and fail closed on a position with no cost basis. A snapshot anchor
    # legitimately carries no cost basis at all, and `prepare` must never fail
    # over an unrelated position's pricing gap. Every route computes this —
    # unlike the condition-slot lookup queue, it needs no review history or
    # thesis cycle, only the ledger and date_end, both of which every route
    # already has.
    # #462: a corrupt row here must not let the trade-event extraction below
    # silently run over a shortened ledger — a matched/unmatched evaluation
    # verdict computed from an incomplete read is a wrong verdict, not a
    # missing one.
    try:
        ledger_events, _skipped_ledger_lines = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
    except ledger.LedgerIntegrityError as exc:
        raise ReviewError(str(exc)) from exc
    evaluation_reconciliation = _evaluation_reconciliation(
        root, _ledger_trade_events(ledger_events), state.get("date_end"))
    condition_checks, condition_questions, condition_deferred = [], [], []
    if route != "snapshot_review" and submitted_condition_checks:
        condition_slots, prior_checks, _unreadable = _condition_store(root)
        condition_checks = _build_condition_checks(
            condition_slots, prior_checks, submitted_condition_checks, session_id,
            state.get("date_end"))
        condition_questions = _condition_questions(
            condition_checks, condition_slots, session_id, language, condition_deferred,
            thesis_links)
    question_queue, question_selection = _question_queue(
        card, state, active, previous, language, recent_exits, by_cycle, due_revisits,
        problem_stats, rule_history, horizon_markers, route=route,
        missing_thesis_positions=missing, tier=review_tier["tier"],
        condition_questions=condition_questions,
        evaluation_recall=_evaluation_recall(root))
    question_selection["rejected"].extend(condition_deferred)
    candidate_rules = _candidate_rules(card, state, language)
    # #714: stamped here rather than inside each of _question_queue's builders.
    # `asked_because` is assigned at four separate sites already, and a fifth
    # per-kind assignment is how one branch silently ships without it.
    for row in question_queue:
        effect = _answer_effect(row.get("kind"), language)
        if effect:
            row["answer_effect"] = effect
    # #714: the leading engine finding, projected before question 1. Built from
    # `card` — the same object the renderer reads — so the opening and the card
    # cannot disagree about which finding leads. It survives _plan_for_agent by
    # construction: it carries rendered sentences, never the raw card.
    opening_value = card_renderer.build_opening_value(
        {"engine_card": card}, language,
        questions_required=sum(1 for row in question_queue if row.get("required")))
    plan = {
        "schema_version": 2,
        "engine_version": _engine_version(),
        "session_id": session_id,
        "status": "awaiting_answers",
        "route": route,
        "flow_path": flow_path,
        "language": card_renderer.resolve_language(language),
        "persist": bool(persist),
        "state_root": root,
        "input": {"paths": [os.path.abspath(p) for p in paths],
                  "kind": "positions_snapshot" if route == "snapshot_review" else "trades_csv",
                  "fingerprint": fingerprint, "engine_meta": engine_meta,
                  "ledger_ingest": ledger_ingest,
                  "price_feed": _price_feed_status(
                      card, supplied=supplied_prices,
                      unavailable_declared=prices_unavailable),
                  # #357: the other input gap, with the opposite timing contract
                  # — see _cash_anchor_status. Always present, including as an
                  # explicit not_applicable, so "this route never asks" is a
                  # claim rather than a silence.
                  "cash_anchor": _cash_anchor_status(state, route, cadence)},
        "state_snapshot": {"prior_commitment": _with_condition_line(
                               (previous or {}).get("commitment")),
                           # #412/#434: the conditions the engine cannot compute,
                           # as a bounded lookup request rather than the whole
                           # store. Each entry is the line's live row plus what
                           # its last check found; the summary beside it states
                           # the total, so nobody can mistake this list for the
                           # complete record. Look each one up per its frozen
                           # query and submit the results — the engine decides the
                           # verdict, never the prose (references/condition-slots.md).
                           "condition_slots_due": condition_due,
                           "condition_slots_summary": condition_summary,
                           # #416 C2: the thesis conditions that stopped being
                           # checked *this* period, because their position was
                           # fully exited. An event, so the card says it once;
                           # empty on every later review, which is what keeps
                           # a retirement from becoming permanent noise.
                           "condition_slots_retired": condition_retired,
                           # The results already submitted on this pass, validated
                           # into durable rows. Present only on the second prepare;
                           # a crossing question below was posed against these.
                           **({"condition_checks": condition_checks}
                              if condition_checks else {}),
                           "review_progress": {
                               "completed_reviews_before_start": completed_reviews,
                               "returning": completed_reviews > 0,
                           },
                           "cadence": cadence,
                           "review_tier": review_tier,
                           "active_theses": active_rows,
                           "thesis_states": thesis_states,
                           # audit summary only — the question payload is the single
                           # complete source the flow reads, so the two can't diverge
                           "due_revisits": [{"revisit_id": row.get("revisit_id"),
                                             "checkpoint": row.get("checkpoint"),
                                             "due_date": row.get("due_date"),
                                             "ticker": (row.get("item") or {}).get("ticker")}
                                            for row in due_revisits or []],
                           "recent_exits": list(recent_exits or []),
                           "exit_backlog": exit_backlog,
                           "problem_stats": problem_stats,
                           "headline_motive_events": headline_motive_events,
                           "market_context": state.get("market_context"),
                           "horizon_markers": horizon_markers},
        "question_queue": question_queue,
        "missing_thesis_positions": missing,
        "authoring_contract": _authoring_contract(route),
        # Omitted, never emitted empty, when the engine has no applicable hole
        # to lead with. An absent key and a blank block read the same to the
        # agent; only one of them is honest about having found nothing.
        **({"opening_value": opening_value} if opening_value else {}),
        "card_plan": {"candidate_rules": candidate_rules,
                      # #302(c): engine-authored, interaction-layer-only sentence
                      # explaining why the other candidates rank lower; None when
                      # fewer than two candidates or the ranking can't honestly
                      # compare them (see _candidate_comparison).
                      "candidate_comparison": _candidate_comparison(candidate_rules, card, language),
                      "question_policy": {"route": question_selection["route"],
                                          "min": question_selection["min"],
                                          "max": question_selection["max"]},
                      "question_selection": question_selection,
                      "horizon_ids": ["weeks", "quarters", "years"],
                      "required_honesty_keys": required_honesty_keys},
        # #317/#429: a supply-side fact surface, not a card section or a
        # question (issue #440 parks card layout; issue #453 puts the
        # judgment of whether this earns a turn on the agent, not the
        # engine). See schemas/review-plan.schema.json for the declared shape.
        "evaluation_reconciliation": evaluation_reconciliation,
        "engine_card": card,
        "engine_state": state,
    }
    if cycle_relinks:
        plan["state_snapshot"]["thesis_cycle_relinks"] = cycle_relinks
    return plan


# Fields the agent never reads but that inflate every subsequent turn.  The
# prepare stdout is re-sent by the agent as context on each later turn
# (narrative authoring, preview, finalize, and any retries), so anything the
# flow contract does not read is pure ballast multiplied by the turn count.
# engine_card and the bulk of engine_state are agent-unreadable by rule #1
# (the agent must never compute or alter a number) and are reloaded from the
# on-disk pending bundle by preview/finalize, so dropping them from the stdout
# copy is lossless for the agent.  The only engine_state field the flow reads
# directly is snapshot_reconciliation (SKILL.md, flows/snapshot-review.md,
# references/data-contract.md), which is preserved.
_AGENT_PLAN_DROP = ("engine_card", "engine_state")


def _plan_for_agent(plan):
    """Project the Review Plan down to what the flow contract reads.

    The full plan (with engine_card/engine_state) is still persisted on disk by
    save_pending; this only trims the copy echoed to stdout for the agent.
    """
    projection = {key: value for key, value in plan.items() if key not in _AGENT_PLAN_DROP}
    reconciliation = (plan.get("engine_state") or {}).get("snapshot_reconciliation")
    if reconciliation is not None:
        projection["engine_state"] = {"snapshot_reconciliation": reconciliation}
    return projection


def _pending_for_agent(bundle):
    """Trim a resumed pending bundle without dropping its frozen presentation."""
    projection = dict(bundle)
    if isinstance(projection.get("plan"), dict):
        projection["plan"] = _plan_for_agent(projection["plan"])
    # The resolved presentation is the runtime handoff. The authored candidate
    # remains private canonical state and would only duplicate that copy here.
    projection.pop("question_surfaces", None)
    # #628's receipt is the same kind of private state, and a raw digest answers
    # no question the agent can act on: the recovery it would hint at is stated
    # by finalize's own refusal, at the moment it applies.
    projection.pop("preview_receipt", None)
    return projection


def cmd_prepare(args):
    _emit(_prepare_session(args))


def _prepare_session(args):
    """Run one prepare and return exactly what ``cmd_prepare`` emits.

    Split out so ``cmd_add_cash`` can re-enter this same lane with one extra
    input rather than reimplementing it. A second writer of "run the engine,
    ingest, build the plan, save the pending session" is precisely the shape
    docs/development-guide.md §7 exists to prevent: the ingest is idempotent and
    the exit-capture queue dedupes, but only because *this* function is the one
    place either happens.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    # Resolve once at the boundary (#389): everything downstream — fingerprint,
    # plan, renderer, display currency — sees only a canonical supported locale.
    # args.language is rewritten so helpers that read the namespace agree.
    language = args.language = card_renderer.resolve_language(args.language)
    route = args.route
    persist = not args.test_drive
    if args.snapshot_json:
        if args.test_drive:
            raise ReviewError("--snapshot-json cannot be combined with --test-drive")
        if route not in ("auto", "snapshot_review"):
            raise ReviewError("--snapshot-json requires --route snapshot_review")
        route = "snapshot_review"
    if args.test_drive:
        route = "test_drive"
        if not args.root:
            root = tempfile.mkdtemp(prefix="fomo-kernel-test-drive-")
    elif route == "auto":
        route = "weekly_review" if _has_history(root) else "first_review"
    if args.snapshot_json and (args.card_json or args.state_json):
        raise ReviewError("--snapshot-json cannot be combined with --card-json or --state-json")
    if args.snapshot_json and args.cash:
        raise ReviewError("--snapshot-json cannot be combined with --cash; include cash in the snapshot envelope")
    if args.snapshot_json and args.paths:
        raise ReviewError("pass the normalized snapshot only through --snapshot-json")
    # #289: validate the supplied price envelope here, before any engine work.
    # A malformed envelope is an input error the agent can fix and retry; it
    # must never reach the engine as half-usable prices.
    supplied_prices = None
    if getattr(args, "prices", None):
        if args.snapshot_json or args.card_json or args.state_json:
            raise ReviewError("--prices applies to transaction-history reviews; a snapshot or "
                              "precomputed artifacts already carry their own valuation basis")
        try:
            supplied_prices = price_feed.load(os.path.abspath(os.path.expanduser(args.prices)))
        except price_feed.PriceFeedError as exc:
            raise ReviewError(f"price feed rejected: {exc}") from exc
    declared_unavailable = _declared_prices_unavailable(args)
    args.prices_unavailable = declared_unavailable
    # #412: the results of this period's condition lookups, submitted the same
    # way prices are — the plan publishes what is due, the agent runs each
    # frozen query, and this second pass is what lets a crossing be posed as a
    # question instead of only recorded. Parsed here, before any engine work,
    # so a malformed envelope is an input error the agent can fix and retry.
    submitted_checks = []
    if getattr(args, "condition_checks", None):
        if route == "snapshot_review":
            raise ReviewError("--condition-checks applies to a review with history; a position "
                              "snapshot has no standing conditions to check")
        submitted_checks = _condition_check_envelopes(
            _load_json(args.condition_checks, "condition checks"), "input")
    if route == "snapshot_review" and not args.snapshot_json and not (args.card_json and args.state_json):
        raise ReviewError("snapshot_review requires --snapshot-json")
    paths = ([args.snapshot_json] if args.snapshot_json else
             list(args.paths or ([] if args.card_json else
                                 [str(MOCK_CSV) if args.test_drive else None])))
    if any(p is None for p in paths) or (not paths and not args.card_json):
        raise ReviewError("provide at least one CSV path, --snapshot-json, or use --test-drive")
    # Resolve to absolute paths once: the engine subprocess runs with cwd at the
    # skill directory, so a caller-relative path would otherwise be fingerprinted
    # from one file and processed from another (or crash mid-run).
    paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
    frozen_transaction = None
    frozen_tmp = None
    # A CSV route owns the #501 frozen transaction.  The snapshot/card-json
    # developer routes do not carry candidate trade batches, so they keep the
    # pre-existing lane below.
    freeze_candidates = bool(paths) and not args.snapshot_json and not args.card_json
    prepared = None
    if args.snapshot_json:
        try:
            card, state, adapter_meta = snapshot_adapter.prepare(
                paths[0], driver_map=args.driver_map, instrument_map=args.instrument_map,
                snapshot=_carried_declaration(root, paths[0])
            )
        except (OSError, ValueError, snapshot_adapter.SnapshotError) as exc:
            raise ReviewError(f"snapshot adapter rejected input: {exc}") from exc
        reconciliation = _validate_initial_snapshot_root(root, state.get("snapshot_anchor"))
        if reconciliation is not None:
            # #530, and deliberately before save_pending, before any ledger
            # write, and before the user is shown a card built on a book that
            # has not caught up yet.
            _refuse_if_the_book_must_catch_up(root, paths[0])
            card, state = _apply_snapshot_reconciliation(card, state, reconciliation)
        prepared = {"card": card, "state": state}
        if isinstance(adapter_meta, str):
            engine_meta = adapter_meta
        else:
            # The Review Plan already contains the local input path and engine
            # artifacts.  Keep metadata diagnostic rather than duplicating the
            # full private anchor inside a display string.
            safe_meta = {key: adapter_meta.get(key) for key in (
                "source", "input_rows", "positions_n", "merged_rows",
                "valuation_basis", "weights_available", "driver_map", "instrument_map"
            ) if key in adapter_meta}
            engine_meta = session.canonical(safe_meta)
    elif args.card_json or args.state_json:
        if not (args.card_json and args.state_json):
            raise ReviewError("--card-json and --state-json must be provided together")
        card = _load_json(args.card_json, "engine card")
        state = _load_json(args.state_json, "engine state")
        prepared = {"card": card, "state": state}
        engine_meta = "prepared artifacts"
    fingerprint = _fingerprint(paths, language, route, prepared=prepared,
                               nonce=args.session_nonce or "", prices=supplied_prices,
                               cash=getattr(args, "cash", None),
                               condition_checks=submitted_checks,
                               prices_unavailable=getattr(args, "prices_unavailable", None))
    existing = _pending_by_fingerprint(root, fingerprint)
    if existing:
        return {"status": "resumed", "session_id": existing["session_id"],
                "review_plan": _plan_for_agent(existing),
                "next_action": ("run resume --session-id to reuse any validated question surface; "
                                "then ask question_queue and run preview")}
    if freeze_candidates:
        frozen_tmp = tempfile.TemporaryDirectory(prefix="fomo-review-input-")
    try:
        if frozen_tmp is not None:
            frozen_transaction = _freeze_transaction_inputs(root, paths, frozen_tmp.name)
        if prepared is None:
            card, state, engine_meta = _run_engine(
                (frozen_transaction or {}).get("frozen_paths", paths), root, args,
                ledger_path=(frozen_transaction or {}).get("ledger_snapshot"))
        # #582, and deliberately here: before save_pending, before any ledger
        # write, and before the user is shown a card whose every weight rests on
        # a share basis the file's own prices contradict.
        _refuse_an_unsettled_transaction_basis(card)
        if frozen_transaction is not None:
            batches, _skipped_non_trade, _skipped_future = _parse_frozen_candidates(
                frozen_transaction["frozen_paths"])
            # Deliberately not stored on `state`. The gate reads the in-memory
            # receipt below, and nothing else reads it at all — while `state` is
            # what session_id_from_state() hashes, so persisting a receipt that
            # carries the ledger's byte digest would give the same CSV a new
            # session id the moment its own trades landed, breaking the #166
            # content-addressed identity the idempotent-finalize guard needs.
            overlay, virtual_basis = _virtual_review_basis(frozen_transaction, batches, state)
        card, state = _apply_display_currency(card, state, _previous_state(root), language)
        ledger_ingest = None
        if persist and route == "snapshot_review" and state.get("snapshot_anchor"):
            ledger_ingest = {"mode": "finalize_projection", "kind": "positions_snapshot"}
            if isinstance(state.get("snapshot_reconciliation"), dict):
                ledger_ingest["reconciliation"] = state["snapshot_reconciliation"].get("status")
        elif frozen_transaction is not None:
            ledger_ingest, card, state = _verify_and_ingest_frozen_trades(
                root, frozen_transaction, batches, overlay, virtual_basis, card, state,
                append=persist, amending=bool(getattr(args, "amending_session", False)))
        elif persist and paths:
            ledger_ingest, card, state = _ingest_trades(root, paths, card, state)
    finally:
        # The frozen directory holds byte copies of the user's CSV and ledger.
        # A refusal above is an expected path (#501 A1), so cleanup cannot sit
        # on the success line only.
        if frozen_tmp is not None:
            frozen_tmp.cleanup()
    if route == "snapshot_review":
        recent_exits, due_revisits, exit_backlog = [], [], None
        problem_stats = None
    else:
        # #416: _prepare_exit_capture still returns (recent, due, backlog,
        # ingest_meta); the 4th element (revisit-queue enqueue counters) is
        # discarded here — its only sink in state_snapshot was written but
        # never read by any flow, and that sink has been removed.
        recent_exits, due_revisits, exit_backlog, _ = \
            _prepare_exit_capture(root, state, persist)
        problem_stats = _problem_snapshot(root, state) if persist else None
    plan = _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint,
                       args.session_nonce or "", persist,
                       recent_exits=recent_exits, ledger_ingest=ledger_ingest,
                       due_revisits=due_revisits,
                       exit_backlog=exit_backlog, problem_stats=problem_stats,
                       submitted_condition_checks=submitted_checks,
                       supplied_prices=supplied_prices,
                       prices_unavailable=getattr(args, "prices_unavailable", None))
    committed = session.session_dir(root, plan["session_id"])
    if os.path.isdir(committed):
        return {"status": "already_committed", "session_id": plan["session_id"], "path": committed}
    session.save_pending(root, plan["session_id"], plan=plan)
    next_action = ("for question_opportunity rows, author a private surface and bind it with "
                   "resume --question-surfaces, or keep the engine question/options fallback; "
                   "then ask every required question, author thesis_updates and prose-only narrative, "
                   "and run preview")
    if not persist:
        # The test drive lives in an isolated root that preview/finalize cannot
        # discover on their own; without this handoff they report "pending session
        # not found" against the default root.
        next_action += f"; test drive is isolated — pass --root {root} to every later command"
    due = ((plan.get("state_snapshot") or {}).get("condition_slots_due") or [])
    if due and not submitted_checks:
        # #412: the same recoverable-gap posture as the price request. A review
        # that never runs the lookups still finalizes — every due slot is
        # recorded as not_checked — but the crossing it might have caught is
        # never asked about, so say what is outstanding at the point the agent
        # decides what to do next.
        next_action = (f"{len(due)} standing condition(s) are due: run each frozen "
                       "state_snapshot.condition_slots_due[].query, transcribe the results into "
                       "the envelope in references/condition-slots.md, and rerun prepare with "
                       "--condition-checks <path>. Never assert a verdict — the engine performs "
                       "the comparison and the user answers an event; otherwise continue: "
                       + next_action)
    price_status = ((plan.get("input") or {}).get("price_feed") or {})
    if price_status.get("request"):
        # #289: a host that cannot retrieve prices is a recoverable input gap,
        # not a dead end. Say so at the point the agent decides what to do next.
        next_action = (price_status["next_action"] + "; otherwise continue: " + next_action)
    return {"status": "prepared", "session_id": plan["session_id"],
            "review_plan": _plan_for_agent(plan),
            "next_action": next_action}


def _apply_thesis_skeletons(plan, updates):
    """Merge engine-known defaults into agent updates for uncovered cycles (#251).

    The agent submits only the join key and the qualitative fields; the engine
    fills the mechanical fields it already owns. Explicit agent values are never
    rewritten — a snapshot-route override still reaches (and is rejected by)
    the provenance gates in _validate_thesis_completeness. A ticker that
    contradicts the engine-owned cycle mapping fails closed instead of being
    persisted."""
    missing = {row.get("cycle_id"): row
               for row in plan.get("missing_thesis_positions") or []
               if isinstance(row, dict) and row.get("cycle_id")}
    snapshot_route = plan.get("route") == "snapshot_review"
    merged = []
    for update in updates:
        cycle_id = update.get("cycle_id") if isinstance(update, dict) else None
        if not isinstance(cycle_id, str) or cycle_id not in missing:
            merged.append(update)
            continue
        row = dict(update)
        entry = missing[cycle_id]
        supplied_ticker = str(row.get("ticker") or "").strip()
        if supplied_ticker and supplied_ticker.upper() != entry.get("ticker"):
            raise ReviewError(
                f"thesis update ticker {supplied_ticker!r} does not match engine-owned "
                f"ticker {entry.get('ticker')!r} for cycle: {cycle_id}")
        row["ticker"] = entry.get("ticker")
        if row.get("maturity") is None:
            row["maturity"] = "inferred"
        if snapshot_route and row.get("source_confidence") is None:
            row["source_confidence"] = "candidate"
        merged.append(row)
    return merged


def _validate_thesis_completeness(plan, answers):
    updates = _apply_thesis_skeletons(plan, answers.get("thesis_updates") or [])
    positions = _active_positions(plan.get("engine_state") or {})
    allowed_horizons = (plan.get("card_plan") or {}).get("horizon_ids")
    thesis.validate_thesis_updates(updates, positions, allowed_horizons=allowed_horizons)
    needed = {row.get("cycle_id") for row in plan.get("missing_thesis_positions") or []}
    supplied = {row.get("cycle_id") for row in updates}
    missing = sorted(x for x in needed - supplied if x)
    if missing:
        raise ReviewError("missing inferred thesis updates for cycles: " + ", ".join(missing))
    # #291: a `planned_entry` initial-thesis answer asserts the user entered with
    # an explicit thesis, so a silently-inferred update is not an honest record of
    # it — that cycle must carry a real capture (maturity draft/testable). Every
    # other answer (momentum_follow/external_call/no_clear_thesis/skip) keeps
    # inferred legal, so different answers produce different downstream state.
    # #667: this rule is enforced here, strictly, and unchanged by that fix — the
    # defect it found was that `planned_entry`'s own declared answer contract
    # (`question_surface._INITIAL_THESIS_REQUIREMENTS["planned_entry"]`) named
    # none of this, so an agent could satisfy the contract and still land in the
    # refusal below. Keep the two in sync (maintainer-guide.md mirrored-surfaces
    # table) rather than loosening this check.
    answer_choice = {row.get("question_id"): row.get("choice")
                     for row in (answers.get("answers") or []) if isinstance(row, dict)}
    planned_entry_cycles = {q.get("cycle_id") for q in plan.get("question_queue") or []
                            if q.get("kind") == "initial_thesis"
                            and answer_choice.get(q.get("id")) == "planned_entry"}
    update_by_cycle = {row.get("cycle_id"): row for row in updates}
    inferred_planned = sorted(
        cid for cid in planned_entry_cycles
        if cid and (update_by_cycle.get(cid) or {}).get("maturity") == "inferred")
    if inferred_planned:
        raise ReviewError(
            "planned_entry initial-thesis answers require a captured thesis "
            "(maturity draft or testable) for cycles: " + ", ".join(inferred_planned))
    if plan.get("route") == "snapshot_review":
        not_inferred = sorted(
            row.get("cycle_id") for row in updates
            if row.get("cycle_id") in needed and row.get("maturity") != "inferred"
        )
        if not_inferred:
            raise ReviewError(
                "snapshot-origin thesis updates must remain inferred for cycles: "
                + ", ".join(not_inferred)
            )
        non_candidate = sorted(
            row.get("cycle_id") for row in updates
            if row.get("cycle_id") in needed
            and row.get("source_confidence") != "candidate"
        )
        if non_candidate:
            raise ReviewError(
                "snapshot-origin thesis updates require candidate provenance for cycles: "
                + ", ".join(non_candidate)
            )
    return updates


def _split_thesis_conditions(updates):
    """``(clean_updates, [(cycle_id, exit_trigger, envelope)])`` (#416 C2).

    A thesis row may carry the falsifier it just stated as a condition envelope.
    Attaching it to the row rather than to a parallel array is #412 Ruling 1's
    same-exchange precedent: the pairing is structural, so there is no
    cross-reference between two lists that can drift.

    It is lifted **out** of the row before the row becomes a thesis event. A
    condition lives in ``conditions.jsonl`` with its own identity and its own
    check history; a second copy riding along inside ``theses.jsonl`` would be a
    mirror nobody reconciles, which is precisely what #416 ruled against."""
    clean, attached = [], []
    for row in updates:
        if not isinstance(row, dict) or "condition" not in row:
            clean.append(row)
            continue
        envelope = row["condition"]
        clean.append({key: value for key, value in row.items() if key != "condition"})
        if envelope is None:
            continue
        attached.append((row.get("cycle_id"), row.get("exit_trigger"), envelope))
    return clean, attached


def _build_thesis_condition_slots(plan, attached):
    """Every stated falsifier this review turns into a watched condition (#416 C2).

    ``thesis_cycle_id`` comes from the thesis row's own ``cycle_id`` — never
    from the envelope, which could otherwise attach a condition to a thesis the
    user never wrote.

    The criterion must be the ``exit_trigger`` verbatim, the same refusal
    ``_slot_commitment`` makes about ``commitment.rule``. The record stores
    ``criterion`` and the thesis stores ``exit_trigger``; one being a tidied
    paraphrase of the other is exactly what #396 forbids, and here it would also
    mean the condition being watched is not the one the thesis says breaks it."""
    if not attached:
        return []
    session_id = str(plan.get("session_id") or "")
    created = (plan.get("engine_state") or {}).get("date_end")
    rows = []
    for index, (cycle_id, exit_trigger, envelope) in enumerate(attached):
        if not isinstance(envelope, dict):
            raise ReviewError(f"thesis_updates condition for {cycle_id!r} must be an object")
        criterion = str(envelope.get("criterion") or "").strip()
        if criterion != str(exit_trigger or "").strip():
            raise ReviewError(
                f"thesis_updates condition for {cycle_id!r} must carry the thesis's own "
                "exit_trigger as its criterion, verbatim: a watched condition that paraphrases "
                "the falsifier is watching something the user did not say")
        try:
            rows.append(conditions.build_slot(
                envelope,
                slot_id="slot-" + (session_id.split("__")[-1] or "0") + f"-t{index}",
                created=created, session_id=session_id or None,
                thesis_cycle_id=cycle_id))
        except conditions.ConditionError as exc:
            raise ReviewError(f"thesis falsifier condition rejected ({cycle_id}): {exc}") from exc
    return rows


def _assign_thesis_ids(plan, updates):
    date = (plan.get("engine_state") or {}).get("date_end")
    prior_rows = ((plan.get("state_snapshot") or {}).get("thesis_states") or [])
    prior_by_cycle = {row.get("cycle_id"): row for row in prior_rows if row.get("cycle_id")}
    rows = []
    for update in updates:
        row = dict(update)
        # decision_cursor is written only by engine-built thesis_decision events;
        # an agent-supplied entry would poison question dedup on the next review
        # (reconstruct_states stops carrying the engine cursor forward once the
        # row carries the key — even with a null value), so key presence itself
        # fails closed.
        if "decision_cursor" in row:
            raise ReviewError(
                f"thesis update carries engine-owned decision_cursor for cycle: {row.get('cycle_id')}")
        if plan.get("route") == "snapshot_review":
            # Provenance is an engine-owned route fact, not an agent label.
            row["origin"] = "snapshot"
            anchor = (plan.get("engine_state") or {}).get("snapshot_anchor")
            row["cycle_provenance"] = {
                "kind": "snapshot_inference",
                "snapshot_as_of": anchor.get("as_of") if isinstance(anchor, dict) else None,
            }
        prior = prior_by_cycle.get(row.get("cycle_id")) or {}
        thesis_id = prior.get("thesis_id") or thesis.stable_thesis_id(row.get("cycle_id"))
        if row.get("thesis_id") and row["thesis_id"] != thesis_id:
            raise ReviewError(f"thesis update changes stable identity for cycle: {row.get('cycle_id')}")
        row["schema_version"] = 2
        row["thesis_id"] = thesis_id
        row["status"] = "open" if not prior else row.get("status") or "modified"
        if row["status"] == "active":
            row["status"] = "open"
        if row["status"] not in thesis.THESIS_STATUSES:
            raise ReviewError(f"invalid thesis status for cycle: {row.get('cycle_id')}")
        row["position_status"] = "open"
        row["session_date"] = date
        row["session_id"] = plan["session_id"]
        revises = prior.get("last_event_id") or prior.get("event_id")
        if row.get("revises") and row["revises"] != revises:
            raise ReviewError(f"thesis update has stale revises link for cycle: {row.get('cycle_id')}")
        if revises:
            row["revises"] = revises
        identity_payload = dict(row)
        supplied_event_id = identity_payload.pop("event_id", None)
        event_id = thesis.stable_event_id("thesis-update", identity_payload)
        if supplied_event_id and supplied_event_id != event_id:
            raise ReviewError(f"thesis update has invalid event_id for cycle: {row.get('cycle_id')}")
        row["event_id"] = event_id
        rows.append(row)
    return rows


def _clean_note(question_id, answer, context):
    """Shared note contract for narrated answers: evidence_delta is never valid,
    whitespace collapses, and 500 characters is the cap for every question kind."""
    if answer.get("evidence_delta") is not None:
        raise ReviewError(f"{question_id}: evidence_delta is not valid for {context}")
    note = " ".join(str(answer.get("note") or "").split()) or None
    if note and len(note) > 500:
        raise ReviewError(f"{question_id}: note must be at most 500 characters")
    return note


def _build_exit_narratives(plan, answers, amap=None):
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    thesis_states = {row.get("cycle_id"): row for row in
                     ((plan.get("state_snapshot") or {}).get("thesis_states") or [])
                     if row.get("cycle_id")}
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "revisit":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice not in EXIT_DECISIONS:
            raise ReviewError(f"unsupported exit decision: {choice}")
        note = _clean_note(question["id"], answer, "an exit reason")
        if choice == "other" and not note:
            raise ReviewError(f"{question['id']}: other requires a short note")
        if choice == "skip":
            note = None
        event = {
            "event": "exit_narrative", "schema_version": 2,
            "session_id": plan.get("session_id"), "revisit_id": question.get("revisit_id"),
            "cycle_id": question.get("cycle_id"), "ticker": question.get("ticker"),
            "exit_date": question.get("exit_date"), "exit_kind": question.get("exit_kind"),
            "exit_price": question.get("exit_price"), "shares_sold": question.get("shares_sold"),
            "shares_before": question.get("shares_before"), "currency": question.get("currency"),
            "exit_notional": question.get("exit_notional"),
            "exit_reason": choice if choice not in {"other", "skip"} else None,
            "note": note, "capture": "skipped" if choice == "skip" else "confirmed",
            "recorded_at": (plan.get("engine_state") or {}).get("date_end"),
        }
        prior = thesis_states.get(question.get("cycle_id")) or {}
        if prior.get("thesis_id"):
            event["thesis_id"] = prior["thesis_id"]
            event["revises"] = prior.get("last_event_id") or prior.get("event_id")
        raw_id = f"{plan.get('session_id')}|{question.get('revisit_id')}|{choice}|{note or ''}"
        event["event_id"] = "exit-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
        events.append(event)
    return events


def _build_revisit_resolutions(plan, answers, amap=None):
    """Turn due-checkpoint answers into revisit resolution events.

    `skip` is deliberately NOT saved: the checkpoint stays open and returns at
    the next review (the capture contract's skip-dedup applies to exit reasons,
    not to 30/60/90 verdicts — an unanswered verdict is missing data, not a
    decision).
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    date = (plan.get("engine_state") or {}).get("date_end")
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "due_revisit":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice == "skip":
            continue
        if choice not in revisit.STATUSES:
            raise ReviewError(f"unsupported revisit resolution: {choice}")
        note = _clean_note(question["id"], answer, "a revisit verdict")
        event = {
            "type": "resolution", "revisit_id": question.get("revisit_id"),
            "checkpoint": str(question.get("checkpoint")), "status": choice,
            "date": date, "session_id": plan.get("session_id"),
        }
        if note:
            event["note"] = note
        events.append(event)
    return events


def _build_rule_breach_decisions(plan, answers, amap=None):
    """Persist the user's qualitative reading without rewriting problem history."""
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "rule_breach":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in RULE_BREACH_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported rule breach decision: {choice}")
        note = _clean_note(question["id"], answer, "a rule breach decision")
        if choice in {"revise_rule", "exception"} and not note:
            raise ReviewError(f"{question['id']}: {choice} requires a short note")
        event = {
            "event": "rule_breach_decision", "schema_version": 1,
            "session_id": plan.get("session_id"), "rule_id": question.get("rule_id"),
            "rule_text": question.get("rule_text"), "problem_key": question.get("problem_key"),
            "breach_week": question.get("breach_week"), "evidence": list(question.get("evidence") or []),
            "decision": choice, "note": note,
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
            "recent_count": question.get("recent_count"),
            "recent_amount": question.get("recent_amount"), "trend": question.get("trend"),
        }
        identity = session.canonical(event)
        event["event_id"] = "rule-breach-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        events.append(event)
    return events


def _build_headline_motive_events(plan, answers, amap=None):
    """Consume a headline-motive answer into one typed canonical event.

    Only the canonical non-skip choice and engine-owned question context are
    persisted.  In particular, ticker/fact grounding is copied from the
    validated question opportunity when present; this function never derives
    or invents either.  A skip remains explicit in ``answers`` and deliberately
    creates no motive event.
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "headline_motive":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice == "skip":
            continue
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in HEADLINE_MOTIVE_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported headline motive decision: {choice}")
        if answer.get("evidence_delta") is not None:
            raise ReviewError(
                f"{question['id']}: evidence_delta is not valid for a headline motive")
        opportunity = question.get("question_opportunity") or {}
        context = opportunity.get("context") or {}
        event = {
            "event": "headline_motive_decision",
            "schema_version": 1,
            "session_id": plan.get("session_id"),
            "question_id": question.get("id"),
            "decision": choice,
            "context": json.loads(json.dumps(context, ensure_ascii=False, sort_keys=True)),
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
        }
        identity = session.canonical(event)
        event["event_id"] = (
            "headline-motive-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        )
        events.append(event)
    return events


def _build_exit_consistency_events(plan, answers, amap=None):
    """Consume an exit-consistency answer into one typed canonical event (#303).

    Mirrors ``_build_headline_motive_events`` (#299): a non-skip classification
    becomes an append-only typed event carrying only the canonical choice and
    the engine-owned question context (the named instruments / counts). A skip
    stays explicit in ``answers`` and records nothing. The event lands in its
    own ``exit_consistency`` stream — never the headline-motive history — so the
    two motive axes stay separable in durable state.
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "exit_consistency":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice == "skip":
            continue
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in EXIT_CONSISTENCY_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported exit consistency decision: {choice}")
        if answer.get("evidence_delta") is not None:
            raise ReviewError(
                f"{question['id']}: evidence_delta is not valid for an exit consistency motive")
        opportunity = question.get("question_opportunity") or {}
        context = opportunity.get("context") or {}
        event = {
            "event": "exit_consistency_decision",
            "schema_version": 1,
            "session_id": plan.get("session_id"),
            "question_id": question.get("id"),
            "decision": choice,
            "context": json.loads(json.dumps(context, ensure_ascii=False, sort_keys=True)),
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
        }
        identity = session.canonical(event)
        event["event_id"] = (
            "exit-consistency-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        )
        events.append(event)
    return events


def _build_initial_thesis_events(plan, answers, amap=None):
    """Persist the first-review entry-motive classification as append-only events (#291).

    Non-skip answers become typed events with the question's grounding facts
    (cost basis, currency) and session refs. `skip` records nothing — it is an
    explicit non-classification, not a decision. These rows project to their own
    `initial_theses.jsonl` audit log; they never enter the thesis-reconstruction
    streams, so they cannot corrupt `_thesis_event_history`.
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "initial_thesis":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in INITIAL_THESIS_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported initial thesis decision: {choice}")
        note = _clean_note(question["id"], answer, "an initial thesis")
        if choice == "skip":
            continue
        event = {
            "event": "initial_thesis", "schema_version": 1,
            "session_id": plan.get("session_id"), "cycle_id": question.get("cycle_id"),
            "ticker": question.get("ticker"), "choice": choice, "note": note,
            "cost_basis": question.get("cost_basis"), "currency": question.get("currency"),
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
        }
        identity = session.canonical(event)
        event["event_id"] = "initial-thesis-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        events.append(event)
    return events


_REVISION_KEYS = frozenset({"of_line_id", "condition"})
_CONDITION_REVISE_CHOICES = ("revise_threshold", "revise_metric")


def _condition_answers(plan, answers, amap):
    """What the user said about this review's condition questions, keyed by line.

    Returns ``(responses, revise_lines, crossed_lines)``:

    - ``responses`` are the extra check fields the answer contributes, folded
      into the envelope before ``build_check`` runs so one row carries the
      evidence, the engine's comparison and the user's word together.
    - ``revise_lines`` maps a line that answered `revise_*` to its question, and
      ``crossed_lines`` is every line that answered a crossing question — the
      two guards below are the only readers.

    A `skip` records nothing at all. It is the honest shape of "the question was
    posed and not answered": the check row still lands with the engine's own
    verdict, and nothing pretends the user weighed in."""
    responses, revise_lines, crossed_lines = {}, {}, set()
    date_end = (plan.get("engine_state") or {}).get("date_end")
    for question in plan.get("question_queue") or []:
        kind = question.get("kind")
        if kind not in ("condition_crossing", "condition_basis"):
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        offered = {option.get("value") for option in question.get("options") or []}
        valid = (CONDITION_BASIS_CHOICES if kind == "condition_basis" else
                 (CONDITION_CROSSING_EVENT_CHOICES if question.get("condition_kind") == "event"
                  else CONDITION_CROSSING_NUMERIC_CHOICES))
        if choice not in valid or choice not in offered:
            raise ReviewError(f"unsupported condition decision: {choice}")
        if answer.get("evidence_delta") is not None:
            raise ReviewError(f"{question['id']}: evidence_delta is not valid for a condition answer")
        line_id = question.get("line_id")
        if choice == "skip":
            continue
        if kind == "condition_crossing":
            crossed_lines.add(line_id)
            response = {"answer": choice, "answered_at": date_end}
            note = _clean_note(question["id"], answer, "a condition crossing")
            if note:
                response["note"] = note
            responses.setdefault(line_id, {})["user_response"] = response
            continue
        if choice == "keep":
            responses.setdefault(line_id, {})["basis_resolution"] = "kept"
            continue
        revise_lines[line_id] = question
        responses.setdefault(line_id, {})["basis_resolution"] = "revised"
    return responses, revise_lines, crossed_lines


def _condition_revisions(plan, answers, revise_lines, crossed_lines, slots, session_id):
    """Turn `revise_threshold`/`revise_metric` into new slot rows on the same lines.

    A re-stated criterion is a new row, never an edit: the old row is a fact
    about what the user meant when they wrote it, and every check already
    recorded points at it. ``build_slot(revises=...)`` carries the line forward
    so the history is not restarted.

    Three refusals, all firewalls rather than validation:

    - The revision must answer a basis question that was actually asked, and a
      basis question answered `revise_*` must carry one. Either half alone is a
      contradiction the engine names instead of resolving.
    - One change per line per session. A line that also answered a crossing this
      review has already been acted on; revising it in the same breath means the
      answer the user just gave was about a criterion that no longer exists.
      Same shape as #416's muted-then-revised rule guard.
    - The line must be live. A revision of something not in the record would
      create a second root rather than continue a line."""
    raw = answers.get("condition_revision")
    rows = []
    if raw is None:
        if revise_lines:
            question = next(iter(revise_lines.values()))
            raise ReviewError(
                f"{question['id']}: a revise answer requires answers.condition_revision carrying "
                "the re-stated condition — otherwise the user asked for a change that was never made")
        return rows
    submitted = raw if isinstance(raw, list) else [raw]
    seen = set()
    for index, entry in enumerate(submitted):
        label = f"answers.condition_revision[{index}]"
        if not isinstance(entry, dict):
            raise ReviewError(f"{label} must be an object")
        unknown = set(entry) - _REVISION_KEYS
        if unknown:
            raise ReviewError(f"{label} has unknown fields: " + ", ".join(sorted(unknown)))
        line_id = str(entry.get("of_line_id") or "").strip()
        if not line_id:
            raise ReviewError(f"{label} requires of_line_id")
        if line_id in seen:
            raise ReviewError(f"{label}: one change per condition per review")
        seen.add(line_id)
        if line_id not in revise_lines:
            raise ReviewError(
                f"{label}: no condition question this review asked to re-state {line_id!r}. A "
                "condition is re-stated because its basis was questioned, never on its own")
        if line_id in crossed_lines:
            raise ReviewError(
                f"{label}: condition {line_id!r} was also answered as a crossing this review — "
                "the answer you just gave is about the criterion as it stands, so replacing that "
                "criterion in the same review would leave the answer pointing at nothing. "
                "Re-state it next review, or answer the crossing without a replacement")
        parent = conditions.latest_by_line(slots).get(line_id)
        if parent is None:
            raise ReviewError(f"{label}: {line_id!r} is not a live condition in this record")
        try:
            rows.append(conditions.build_slot(
                entry.get("condition"),
                slot_id=f"slot-{session_id.split('__')[-1]}-r{index}",
                created=(plan.get("engine_state") or {}).get("date_end"),
                session_id=session_id or None, revises=parent))
        except conditions.ConditionError as exc:
            raise ReviewError(f"condition revision rejected ({line_id}): {exc}") from exc
    missing = sorted(set(revise_lines) - seen)
    if missing:
        raise ReviewError(
            f"{revise_lines[missing[0]]['id']}: a revise answer requires a replacement condition "
            f"for {missing[0]!r}")
    return rows


def _condition_card_context(plan, built):
    """The live condition behind every check row the plan's capped due list does
    not already carry (external review, round 2 BLOCK).

    The card joins a check to its condition through what the engine stamped —
    criterion, unit, kind, ``line_id``, ``thesis_link``. That stamp lived only on
    ``condition_slots_due``, which is capped, so a check submitted for a line
    beyond the cap — always legal, and after #416 C2 able to raise a crossing
    question — reached no card line at all while still counting toward the
    summary's `checked`. A crossed falsifier could be asked about and then be
    absent from the card, which is the exact "dropped without being named"
    defect this whole tier exists to prevent.

    It re-runs the *same* derivation the plan used — ``_condition_lines`` over a
    cycle index from ``_plan_thesis_cycles``, which assembles the same two row
    sources the plan did, this session's relinks included. Assembling that input
    a second way here is what round 3 caught: the derivation was single and its
    ingredients were not, so a cycle live only via a relink read as dead and its
    condition fell out of the join exactly as before.

    The renderer still reads one engine-stamped answer and never works liveness
    out for itself. A retired line is absent from the derivation and therefore
    stays absent here: retired means retired, and the retirement sentence is
    what speaks for it."""
    root = plan.get("state_root")
    checked = {row.get("slot_id") for row in built if isinstance(row, dict)}
    if not checked or not root or not os.path.isdir(root):
        return []
    already = {entry.get("slot_id")
               for entry in ((plan.get("state_snapshot") or {}).get("condition_slots_due") or [])}
    live, _retired, _unreadable = _condition_lines(root, _plan_thesis_cycles(plan))
    return [entry for entry in live
            if entry.get("slot_id") in checked and entry.get("slot_id") not in already]


def _build_condition_records(plan, answers, amap):
    """``(check_rows, revision_rows, card_context)`` — everything this review
    learned about the user's standing conditions, ready for the append-only
    stores, plus the join surface the card needs for the checks the plan's
    bounded due list does not carry.

    Order matters and is deliberate: the answers are resolved first so a check
    row is written *complete*, then the missing due slots are synthesized so the
    period has no silent gaps, then the revisions are built against the slots as
    they stood when the checks were taken."""
    root = plan.get("state_root")
    if not root or not os.path.isdir(root) or plan.get("route") == "snapshot_review":
        return [], [], []
    session_id = str(plan.get("session_id") or "")
    date_end = (plan.get("engine_state") or {}).get("date_end")
    slots, checks, _unreadable = _condition_store(root)
    responses, revise_lines, crossed_lines = _condition_answers(plan, answers, amap)
    envelopes = _condition_check_envelopes(answers, "answers")
    built = _build_condition_checks(slots, checks, envelopes, session_id, date_end, responses)
    _refuse_check_drift(plan, built)
    _refuse_dropped_ingested_check(plan, built)
    due = ((plan.get("state_snapshot") or {}).get("condition_slots_due") or [])
    built += _synthesized_not_checked(slots, checks, due, built, session_id, date_end)
    revisions = _condition_revisions(plan, answers, revise_lines, crossed_lines, slots, session_id)
    return built, revisions, _condition_card_context(plan, built)


def _refuse_dropped_ingested_check(plan, built):
    """A reading this session already took cannot quietly become "not checked".

    ``prepare --condition-checks`` ingests real lookups: they are validated into
    rows, frozen into the plan, and a crossing question may have been posed
    against them. If the answers then omit one of those slots, the synthesized
    ``not_checked`` path — which exists for conditions nobody looked at — would
    overwrite a lookup that *did* happen with a row saying nobody looked. The
    user could have been asked about a crossed line and the file would end up
    claiming the condition went unchecked that period.

    So the synthesized path is legal only for a due condition that never had a
    prepare-side check. Anything ingested must come back (external review,
    round 1)."""
    ingested = {row.get("slot_id")
                for row in ((plan.get("state_snapshot") or {}).get("condition_checks") or [])}
    dropped = sorted(ingested - {row.get("slot_id") for row in built})
    if dropped:
        raise ReviewError(
            "condition check(s) " + ", ".join(repr(slot_id) for slot_id in dropped) +
            " were looked up when this review was prepared but are missing from "
            "answers.condition_checks. Recording them as not-checked would erase a lookup that "
            "happened — resubmit the same envelope, or rerun prepare without it")


# What the user's own answer is allowed to move: their response, how the basis
# question ended, and the two verdict-of-record fields those derive. Everything
# else — the observation, `engine_verdict`, the alerts — is the evidence the
# question was posed against and must come back identical.
_ANSWER_ONLY_CHECK_FIELDS = ("user_response", "basis_resolution",
                             "final_verdict", "verdict_source")


def _refuse_check_drift(plan, built):
    """The question was posed against a number; the record must be that number.

    The plan freezes the check rows the crossing question was built from. If the
    envelope resubmitted with the answers produces a different row, the user
    answered about one reading and the file would store another — which is
    exactly the class of silent divergence the frozen question surfaces exist to
    prevent. The user's own contribution is excluded from the comparison,
    because adding it is the entire point of the second submission."""
    frozen = {row.get("slot_id"): row
              for row in ((plan.get("state_snapshot") or {}).get("condition_checks") or [])}
    if not frozen:
        return

    def _evidence(row):
        return session.canonical({key: value for key, value in row.items()
                                  if key not in _ANSWER_ONLY_CHECK_FIELDS})

    for row in built:
        prior = frozen.get(row.get("slot_id"))
        if prior is not None and _evidence(prior) != _evidence(row):
            raise ReviewError(
                f"condition check for {row.get('slot_id')!r} changed between the question and the "
                "answer: the user was asked about one reading and this would record another. "
                "Rerun prepare with the corrected envelope and ask again")


def _refuse_revision_of_a_muted_line(plan, expected_revision):
    """A rule silenced this session cannot also be replaced this session (#416)."""
    root = plan.get("state_root")
    if not root or not os.path.isdir(root):
        return
    muted_ids = _muted_rule_ids(root)
    if not muted_ids:
        return
    _tracking, muted = problems.load_rules(os.path.join(root, "rules.jsonl"), muted_ids)
    target = str(expected_revision.get("rule_id") or "")
    for row in muted:
        if target in {str(row.get("rule_id")), problems.rule_line_id(row)}:
            raise ReviewError(
                f"rule {problems.rule_line_id(row)!r} is muted, so it cannot be revised in the "
                "same review — a replacement would inherit the silence and never be asked "
                "about. Unmute it first, or answer the breach without a replacement")


def _slot_commitment(plan, chosen, condition, expected_revision):
    """#412: a condition the engine cannot compute becomes a stored slot.

    The metric-shaped fields (`metric_key`, `metric_value`, `goal`, `dim`) are
    dropped rather than nulled: they exist to join a commitment to an engine
    metric, and a slot has none — carrying them would be four written-never-read
    fields inviting a later reader to join on them anyway.

    Two refusals stay, both firewalls rather than validation:

    - A slot cannot replace a rule under `revise_rule`. `problems.check_rules`
      reconciles that rule mechanically against problem events every period; a
      researched condition has no problem key to join on, so accepting it would
      silently retire a tracked rule into something `held_streak` can never
      count (`docs/development-guide.md` section 5).
    - A commitment carries a metric_key or a condition, never both. Two anchors
      means two answers to "what did this review actually track", and the card's
      then/now reconciliation would have to pick one.
    """
    if expected_revision:
        raise ReviewError("a revise_rule replacement must be tracked by an engine metric; "
                          "a condition slot cannot enter the rule reconciliation")
    if chosen.get("metric_key"):
        raise ReviewError("a commitment carries either a metric_key or a condition, not both")
    session_id = str(plan.get("session_id") or "")
    try:
        slot = conditions.build_slot(
            condition,
            slot_id="slot-" + (session_id.split("__")[-1] or "0") + "-0",
            created=(plan.get("engine_state") or {}).get("date_end"),
            session_id=session_id or None)
    except conditions.ConditionError as exc:
        raise ReviewError(f"condition slot rejected: {exc}")
    rule = (chosen.get("rule") or "").strip()
    if rule and rule != slot["criterion"]:
        # The card prints `rule`; the record stores `criterion`. One of them
        # being a paraphrase of the other is exactly what #396 forbids.
        raise ReviewError("commitment.rule must be the condition's criterion, verbatim")
    return {"rule": slot["criterion"], "origin": "custom", "condition": slot}


def _resolve_commitment(plan, answers):
    choice = answers.get("commitment") or {}
    selected = choice.get("choice")
    answer_map = {row.get("question_id"): row for row in answers.get("answers") or []
                  if isinstance(row, dict)}
    revise_questions = [
        row for row in plan.get("question_queue") or []
        if row.get("kind") == "rule_breach"
        and (answer_map.get(row.get("id")) or {}).get("choice") == "revise_rule"
    ]
    if len(revise_questions) > 1:
        raise ReviewError("one card can revise at most one rule")
    expected_revision = revise_questions[0] if revise_questions else None
    revises_rule_id = choice.get("revises_rule_id")
    if expected_revision and revises_rule_id != expected_revision.get("rule_id"):
        raise ReviewError("a revise_rule answer requires the one final commitment to revise that rule")
    if not expected_revision and revises_rule_id:
        raise ReviewError("revises_rule_id requires a revise_rule answer for that rule")
    if expected_revision:
        # #416: the frozen question was posed before this session could mute that
        # rule. Without this, muting at step 8 and revising at step 9 both land:
        # the replacement inherits the line's silence and a rule the user authored
        # *this week* is born muted, absent from the rotation and from #292's
        # breach disclosure, with nothing said. The two answers contradict each
        # other, so the engine names the contradiction instead of picking one.
        _refuse_revision_of_a_muted_line(plan, expected_revision)
    if selected == "skip":
        if expected_revision:
            raise ReviewError("a revise_rule answer requires a replacement commitment")
        return None
    candidates = {row["id"]: row for row in (plan.get("card_plan") or {}).get("candidate_rules") or []}
    condition = choice.get("condition")
    if selected in candidates:
        if condition is not None:
            raise ReviewError("a candidate rule is already tracked by an engine metric; "
                              "a condition slot belongs to a self-authored commitment")
        chosen = dict(candidates[selected])
        chosen["origin"] = "candidate"
    elif selected == "custom":
        chosen = {"rule": (choice.get("rule") or "").strip(), "metric_key": choice.get("metric_key"),
                  "goal": choice.get("goal") or "down", "dim": choice.get("dim"), "origin": "custom"}
        if not chosen["rule"] and condition is None:
            raise ReviewError("custom commitment requires rule")
    else:
        raise ReviewError("commitment.choice must be a candidate id, custom, or skip")
    metrics = (plan.get("engine_state") or {}).get("metrics") or {}
    chosen.pop("id", None)
    if condition is not None:
        chosen = _slot_commitment(plan, chosen, condition, expected_revision)
    elif chosen.get("metric_key") not in metrics:
        # Still an error, deliberately: a metric_key the engine does not compute
        # is an agent mistake, not a user's condition. A condition arrives as a
        # condition (#412).
        raise ReviewError(f"commitment metric is not in engine state: {chosen.get('metric_key')}")
    else:
        chosen["metric_value"] = metrics.get(chosen["metric_key"])
    chosen["source"] = "user_chosen"
    if expected_revision:
        replacement_key = session.PKEY.get(chosen.get("metric_key"))
        if replacement_key != expected_revision.get("problem_key"):
            raise ReviewError("replacement commitment must track the same problem_key as the revised rule")
        chosen["revises_rule_id"] = revises_rule_id
    # `insufficient_data` describes this file's round-trip sample, which is what an
    # engine metric's baseline is computed from. A slot's baseline came from an
    # outside source and is not affected by it (#412).
    if (plan.get("engine_state") or {}).get("insufficient_data") and not chosen.get("condition"):
        chosen["baseline_note"] = "short-sample baseline"
    return chosen


def _refuse_a_card_built_on_a_skipped_price_recovery(plan):
    """#623 class 2: the degraded price card is not a disclosure by default.

    `flows/first-review.md` step 0 requires the agent to look the requested
    closes up and rerun `prepare --prices` **before** delivering a degraded
    card. Two different runs produced the identical sentence on the card —
    "current prices could not be retrieved" — and nothing could tell them
    apart: the sources genuinely publish nothing, or the step was skipped. The
    second is not something to disclose to the user, who can do nothing about
    it; it is a review whose every weight came from cost basis when it did not
    have to.

    So the refusal fires exactly where the harm is: the card is about to be
    built, `price_retrieval_blocked` says it will carry that sentence, and
    nothing was ever handed back. It is cleared by doing the step, or by
    declaring in one flag that it was done and found nothing — never by
    proceeding silently. That is why this is not the hard block #357 ruled out:
    the user is not asked for anything and nothing waits on them.

    Called from both commands that build a card from a *pending* session, so
    `finalize` invoked directly cannot walk around `preview`. Deliberately not
    inside `_draft_bundle`: that function also runs on `finalize`'s
    already-committed branch, where the card was delivered long ago and there
    is nothing left to prevent — gating there would turn the documented
    "retrying the same session with identical content is a no-op" into a
    refusal for every price-degraded session committed before this rule
    existed.
    """
    price_feed_status = ((plan.get("input") or {}).get("price_feed") or {})
    if not price_feed_status.get("request"):
        return
    if not card_renderer.price_retrieval_blocked(plan.get("engine_card") or {}):
        return
    if (price_feed_status.get("recovery") or {}).get("attempted"):
        return
    raise ReviewError(
        "this card would tell the user their prices could not be retrieved, and no price "
        "recovery was ever attempted — every weight on it came from cost basis when it did "
        "not have to. Look the closes in input.price_feed.request up in a recognized "
        "market-data source, transcribe them into the envelope in references/price-feed.md, "
        "and rerun prepare --prices <path>. If those sources genuinely publish nothing for "
        "these instruments, rerun prepare --prices-unavailable '<the sources you checked>' "
        "and the degraded card is delivered as an honest dead end. Never invent a price")


def _draft_bundle(plan, answers, narrative, require_commitment,
                  question_surfaces=None, question_presentations=None):
    if answers.get("session_id") != plan.get("session_id"):
        raise ReviewError("answers.session_id does not match Review Plan")
    if question_surfaces is not None and not isinstance(question_presentations, list):
        raise ReviewError("validated question surface is missing its frozen presentation")
    question_surface.validate_answer_contract(
        plan, answers,
        presentations=question_presentations if question_surfaces is not None else None,
    )
    amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=not require_commitment)
    # #416 C2: the falsifier a thesis row states can arrive as a condition
    # envelope on that same row. It is lifted out before the row becomes a
    # thesis event and built into a slot below — one exchange, two records, no
    # copy of the condition inside the thesis store.
    validated_updates, thesis_conditions = _split_thesis_conditions(
        _validate_thesis_completeness(plan, answers))
    agent_updates = _assign_thesis_ids(plan, validated_updates)
    cycle_relinks = list(
        ((plan.get("state_snapshot") or {}).get("thesis_cycle_relinks") or [])
    )
    updates = cycle_relinks + agent_updates
    decisions = thesis.build_decision_events(plan, answers, updates)
    exit_narratives = _build_exit_narratives(plan, answers, amap)
    revisit_resolutions = _build_revisit_resolutions(plan, answers, amap)
    rule_breach_decisions = _build_rule_breach_decisions(plan, answers, amap)
    headline_motive_events = _build_headline_motive_events(plan, answers, amap)
    exit_consistency_events = _build_exit_consistency_events(plan, answers, amap)
    initial_thesis_events = _build_initial_thesis_events(plan, answers, amap)
    condition_checks, condition_revisions, condition_context = _build_condition_records(
        plan, answers, amap)
    thesis_condition_slots = _build_thesis_condition_slots(plan, thesis_conditions)
    card_renderer.validate_narrative(narrative)
    # #82 gate: every required honesty key must be covered by an agent-authored
    # sentence, and no sentence may claim a key the plan does not require —
    # either untriggered by the engine or month-gated out with its vs-market
    # host lines (#284).
    required = set((plan.get("card_plan") or {}).get("required_honesty_keys") or [])
    provided = set((narrative.get("honesty") or {}).keys())
    if required - provided:
        raise ReviewError("narrative.honesty is missing required keys: " + ", ".join(sorted(required - provided)))
    if provided - required:
        raise ReviewError("narrative.honesty has keys this review does not require: " + ", ".join(sorted(provided - required)))
    commitment = _resolve_commitment(plan, answers) if require_commitment else None
    bundle = {
        "schema_version": 2,
        "engine_version": plan.get("engine_version") or _engine_version(),
        "session_id": plan["session_id"],
        "route": plan["route"],
        "language": plan["language"],
        "review_plan": plan,
        "engine_state": plan["engine_state"],
        "engine_card": plan["engine_card"],
        "answers": answers,
        "narrative": narrative,
        "thesis_updates": updates,
        "thesis_decisions": decisions,
        "exit_narratives": exit_narratives,
        "commitment": commitment,
        "observations": list(answers.get("observations") or []),
    }
    if question_surfaces is not None:
        bundle["question_surfaces"] = question_surfaces
        bundle["question_presentations"] = question_presentations
    # Only present when a due checkpoint was actually answered: sessions committed
    # before this key existed must re-draft to the identical canonical bundle, or
    # the documented-safe finalize retry would fail closed on every old session.
    if revisit_resolutions:
        bundle["revisit_resolutions"] = revisit_resolutions
    if rule_breach_decisions:
        bundle["rule_breach_decisions"] = rule_breach_decisions
    if headline_motive_events:
        bundle["headline_motive_events"] = headline_motive_events
    # #303: absent-when-empty, same replay-compatibility contract as the keys
    # above — only present when at least one early-exit motive was classified.
    if exit_consistency_events:
        bundle["exit_consistency_events"] = exit_consistency_events
    # Absent-when-empty, same replay-compatibility contract as the keys above:
    # first-review-only, and only when at least one entry motive was classified.
    if initial_thesis_events:
        bundle["initial_thesis_events"] = initial_thesis_events
    # #412: this period's condition results, and any criterion the user re-stated
    # because its basis was questioned. Absent-when-empty for the same reason —
    # a session committed before the check flow existed must re-draft to the
    # identical canonical bundle or its documented-safe finalize retry fails.
    if condition_checks:
        bundle["condition_checks"] = condition_checks
    if condition_revisions:
        bundle["condition_revisions"] = condition_revisions
    # The conditions behind checks the plan's bounded due list does not carry.
    # Not a store — nothing here is appended anywhere; it is the card's join
    # surface for a beyond-cap reading, stamped by the engine so the renderer
    # never re-derives it (external review, round 2).
    if condition_context:
        bundle["condition_slots_context"] = condition_context
    # #416 C2: the falsifiers stated with this review's theses, now watched
    # conditions. Absent-when-empty for the same retry reason as the two above.
    if thesis_condition_slots:
        bundle["thesis_conditions"] = thesis_condition_slots
    # #446 cut 1: this review's horizon said-vs-done judgments, turned into
    # durable rows instead of being recomputed and discarded every period.
    # Absent-when-empty for the same retry reason as the keys above.
    horizon_verdicts = _horizon_verdict_rows(plan, plan["session_id"])
    if horizon_verdicts:
        bundle["verdicts"] = horizon_verdicts
    return bundle


def _load_interaction(args, pending):
    answers = _load_json(args.answers, "answers") if args.answers else pending.get("answers")
    narrative = _load_json(args.narrative, "narrative") if args.narrative else pending.get("narrative")
    if not answers or not narrative:
        raise ReviewError("answers and narrative are required (pass files or save them with preview)")
    return answers, narrative


# ── the preview precondition (#628) ────────────────────────────────────────────
# `preview` renders the card the user is shown; `finalize` commits it. Nothing
# mechanically linked the two, so an agent could go from answers straight to
# finalize and the user would receive a committed card — carrying a standing
# rule — that they never saw. #357 recorded five recurrences of an agent
# skipping an instructed step in this exact lifecycle, so the guarantee is not
# left to instructions.
PREVIEW_RECEIPT_ARTIFACT = "preview-receipt"


def _preview_receipt_key(plan, answers, narrative,
                         question_surfaces=None, question_presentations=None):
    """The identity a preview receipt is keyed on: every input to the card the
    user was shown, with the one field the lifecycle deliberately defers.

    `commitment` is excluded, and that is a contract statement rather than a
    convenience. The documented order (`flows/first-review.md` step 6) is: show the card,
    *then* ask the user to choose a candidate rule, supply a custom one, or
    skip, *then* write that choice to `answers.commitment` and finalize. The
    choice is made after the card was shown, so requiring it to have been in
    the previewed answers would make the contract's own sequence unsatisfiable.

    Verified rather than assumed, because the obvious reading of `_draft_bundle`
    is wrong: `require_commitment=False` does null `bundle["commitment"]`, but
    `card_renderer` also reads `answers.commitment.choice` directly, so a
    previewed `skip` really does render a different card from a previewed
    nothing. What the exclusion means is therefore narrow and exact — a user who
    saw the card and then chose a rule, or chose to skip, is the flow this
    contract describes, and only that one field may move between the two calls.

    Everything else is in the key, so a preview that ran against different
    answers, a different narrative, a re-prepared plan, or different frozen
    question surfaces does not certify what is being committed.
    """
    subject = {
        "plan": plan,
        "answers": {key: value for key, value in (answers or {}).items()
                    if key != "commitment"},
        "narrative": narrative,
        "question_surfaces": question_surfaces,
        "question_presentations": question_presentations,
    }
    return hashlib.sha256(session.canonical(subject).encode("utf-8")).hexdigest()


def _require_preview(pending, plan, answers, narrative):
    """Refuse to commit artifacts this session's `preview` never rendered.

    Recoverable by construction: the pending session is untouched and the fix
    is one local, side-effect-free command. `AGENTS.md` is explicit that an
    existing canonical session is not data loss, so the message says so rather
    than reading like a session that has to be rebuilt.
    """
    recorded = (pending.get("preview_receipt") or {}).get("key")
    expected = _preview_receipt_key(plan, answers, narrative,
                                    pending.get("question_surfaces"),
                                    pending.get("question_presentations"))
    if recorded == expected:
        return
    session_id = plan.get("session_id")
    fix = (f"Run `review.py preview --session-id {session_id} "
           "--answers <answers.json> --narrative <narrative.json>`, show the user the "
           "review card it renders, then finalize. Nothing was lost: this session is "
           "still pending and its answers are unchanged.")
    if recorded is None:
        raise ReviewError(
            "finalize refuses: this session has no preview receipt, so the review card "
            f"has not been rendered for the user yet. {fix}")
    raise ReviewError(
        "finalize refuses: this session's preview rendered a different card than the one "
        "being committed — the answers, narrative, review plan, or frozen question "
        "surfaces changed since it ran, so the user has not seen what would be "
        f"committed. {fix}")


CAPTURE_INFERENCE_FIELDS = ("source_type", "source_name", "source_confidence",
                           "emotion", "emotion_inferred", "confidence", "confidence_inferred")


def _load_capture_entries(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as exc:
        raise ReviewError(f"cannot read entries: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise ReviewError("entries must be a non-empty JSON array")
    for entry in value:
        if not isinstance(entry, dict):
            raise ReviewError("each capture entry must be a JSON object")
        if not entry.get("cycle_id"):
            raise ReviewError("each capture entry requires cycle_id")
        if not entry.get("note"):
            raise ReviewError("each capture entry requires note")
        for key in ("emotion", "confidence", "source_type", "source_confidence"):
            if key in entry and entry[key] not in thesis.INFERENCE_ENUMS[key]:
                raise ReviewError(f"invalid {key}: {entry[key]!r}")
    return value


def _capture_rows(entries, plan, capture_session_id):
    """Turn validated capture entries into `theses.jsonl`-safe rows (#237 #4).

    `thesis.reconstruct_states` treats a row with no ``event`` (or
    ``thesis_cycle_relink``) as a full-content replace of that cycle's thesis —
    the only carried-forward keys from the prior state are decision_cursor/
    last_decision/last_exit/final_outcome/evidence_history/last_evidence/
    source_state, never why/exit_trigger/horizon/etc. A capture entry for a
    cycle that already has an established thesis must never take that path, or
    it silently wipes the cycle's existing why/exit_trigger. It goes through a
    `thesis_decision` event instead, which only ever attaches to (never
    replaces) the cycle's content. A cycle with no established thesis yet takes
    the opposite risk: `thesis_decision` for a cycle with no current state is
    dropped entirely (`if not current: continue`), so it must go through the
    full-content path, which requires an honest why/exit_trigger the same way a
    full review's inferred-thesis path does — otherwise the capture is silently
    lost, exactly what #237's #4 is meant to prevent.

    Every row carries `session_id: capture_session_id` — `_append_session_rows`
    only uses its `session_id` argument to *filter* existing rows for dedup; it
    never stamps the tag onto what it writes, so the caller must.
    """
    active_cycle_ids = {row.get("cycle_id") for row in
                        (plan.get("state_snapshot") or {}).get("active_theses") or []}
    # missing_thesis_positions is plan-top-level, not under state_snapshot (see _build_plan).
    missing_by_cycle = {row.get("cycle_id"): row for row in plan.get("missing_thesis_positions") or []}
    rows = []
    for entry in entries:
        cycle_id = entry["cycle_id"]
        inference = {key: entry[key] for key in CAPTURE_INFERENCE_FIELDS if key in entry}
        if cycle_id in active_cycle_ids:
            rows.append({"event": "thesis_decision", "cycle_id": cycle_id,
                        "session_id": capture_session_id,
                        "note": entry["note"], **inference})
            continue
        missing = missing_by_cycle.get(cycle_id)
        if missing is None:
            raise ReviewError(
                f"cycle_id {cycle_id!r} is neither an active thesis nor a missing "
                "thesis position in this Review Plan; cannot capture against it")
        if not entry.get("why") or not entry.get("exit_trigger"):
            raise ReviewError(
                f"cycle_id {cycle_id!r} has no established thesis yet; capture "
                "entries for a new cycle must include why and exit_trigger, the "
                "same as a full review's inferred thesis")
        row = {"cycle_id": cycle_id, "ticker": missing.get("ticker"), "maturity": "inferred",
              "session_id": capture_session_id,
              "why": entry["why"], "exit_trigger": entry["exit_trigger"],
              "note": entry["note"], **inference}
        if missing.get("origin"):
            row["origin"] = missing["origin"]
        rows.append(row)
    return rows


def cmd_capture(args):
    """Light-tier capture-only action (#237 #4): no finalize, no review_mark,
    no commitment, no counted question budget. Appends directly to
    `theses.jsonl` under a distinct session id so a later real `finalize` for
    the same underlying state can never collide with what capture wrote (see
    `_capture_rows` for why the row shape itself must also stay non-destructive).

    Cleans up its `.pending/<session_id>/` entry once appended, so repeated
    captures do not grow `_pending_by_fingerprint`'s scan forever the way an
    abandoned full review already does today. That cleanup would otherwise
    break retry safety — an interrupted agent turn that is unsure whether its
    first `capture` call actually landed must be able to repeat the identical
    call and get the same answer, not a "pending session not found" crash — so
    a missing pending dir is first checked against `theses.jsonl` for rows
    already tagged with this session's derived capture id before it is treated
    as an error."""
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    capture_session_id = f"{args.session_id}--capture"
    theses_path = os.path.join(root, "theses.jsonl")
    try:
        pending = session.load_pending(root, args.session_id)
    except session.SessionError:
        already = [row for row in thesis.read_jsonl(theses_path)
                  if row.get("session_id") == capture_session_id]
        if not already:
            raise
        _emit({"status": "captured", "session_id": args.session_id,
              "capture_session_id": capture_session_id, "entries": len(already),
              "report": {"status": "no-op (already captured)"}})
        return
    plan = pending.get("plan") or {}
    tier = ((plan.get("state_snapshot") or {}).get("cadence") or {}).get("tier")
    if tier != "light":
        raise ReviewError(
            f"capture is only valid for a light-tier session (cadence.tier={tier!r}); "
            "a full-tier review must go through preview/finalize")
    entries = _load_capture_entries(args.entries)
    rows = _capture_rows(entries, plan, capture_session_id)
    with session.projection_transaction(root) as locked_root:
        report = session._append_session_rows(theses_path, capture_session_id, rows)
    shutil.rmtree(session.pending_dir(root, args.session_id), ignore_errors=True)
    _emit({"status": "captured", "session_id": args.session_id,
          "capture_session_id": capture_session_id, "entries": len(rows),
          "report": report})


def cmd_preview(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    pending = session.load_pending(root, args.session_id)
    plan = pending.get("plan")
    _refuse_a_card_built_on_a_skipped_price_recovery(plan)
    answers, narrative = _load_interaction(args, pending)
    bundle = _draft_bundle(
        plan, answers, narrative, require_commitment=False,
        question_surfaces=pending.get("question_surfaces"),
        question_presentations=pending.get("question_presentations"),
    )
    private_md = card_renderer.render_private(bundle)
    public_md = card_renderer.render_public(bundle)
    private_html = card_renderer.render_html(bundle)
    paths = session.save_pending(root, args.session_id, answers=answers, narrative=narrative,
                                 **{"card-private-preview": private_md,
                                    "card-public-preview": public_md,
                                    "card-private-preview.html": private_html,
                                    # #628: the receipt finalize requires. Written
                                    # only here, only after the card really rendered.
                                    PREVIEW_RECEIPT_ARTIFACT: {
                                        "key": _preview_receipt_key(
                                            plan, answers, narrative,
                                            pending.get("question_surfaces"),
                                            pending.get("question_presentations"))}})
    # #773: name any authored narrative field this route's card never reads,
    # rather than let it go missing silently -- the agent can then compare
    # its own draft against the two rendered cards above and see, not guess,
    # whether the discard is expected (see authoring_contract.narrative.
    # not_rendered_on_this_route, which already told it this up front).
    narrative_fields_discarded = _narrative_fields_discarded(narrative, plan.get("route"))
    _emit({"status": "previewed", "session_id": args.session_id,
           "private_card": private_md, "public_card": public_md,
           "private_card_html_path": paths.get("card-private-preview.html"),
           "candidate_rules": (plan.get("card_plan") or {}).get("candidate_rules") or [],
           # #302(c): interaction-layer-only; None when there is nothing honest to compare.
           "candidate_comparison": (plan.get("card_plan") or {}).get("candidate_comparison"),
           "narrative_fields_discarded": narrative_fields_discarded,
           "paths": paths, "next_action": "show the review-card preview (delivery contract: references/card-delivery.md); ask the user to choose one rule or skip; then finalize"})


def cmd_finalize(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    with session.finalize_transaction(root, args.session_id) as transaction:
        committed_path = session.session_dir(root, args.session_id)
        already_committed = os.path.isdir(committed_path)
        if already_committed:
            existing = session.load_committed(root, args.session_id)
            plan = existing.get("review_plan")
            pending = {"answers": existing.get("answers"), "narrative": existing.get("narrative"),
                       "question_surfaces": existing.get("question_surfaces"),
                       "question_presentations": existing.get("question_presentations")}
        else:
            pending = session.load_pending(root, args.session_id)
            plan = pending.get("plan")
            # Only on the pending branch: an already-committed session's card
            # reached the user long ago, and refusing its idempotent replay
            # would break "retrying the same session with identical content is
            # a no-op" for every review committed before this gate existed.
            _refuse_a_card_built_on_a_skipped_price_recovery(plan)
        answers, narrative = _load_interaction(args, pending)
        bundle = _draft_bundle(
            plan, answers, narrative, require_commitment=True,
            question_surfaces=pending.get("question_surfaces"),
            question_presentations=pending.get("question_presentations"),
        )
        # #628: a precondition on *committing*, deliberately after the draft
        # above. finalize stays the complete independent validator the issue
        # measured — invalid answers still get their own unprompted rejection
        # here, not a preview complaint — and this only stops the commit.
        # An already-committed session is exempt because it has no pending
        # directory left to hold a receipt: it passed this gate on the way in,
        # and a documented-safe finalize retry must not become data loss.
        if not already_committed:
            _require_preview(pending, plan, answers, narrative)
        private_md = card_renderer.render_private(bundle)
        public_md = card_renderer.render_public(bundle)
        private_html = card_renderer.render_html(bundle)
        result, projection, projection_error = transaction.commit_bundle(
            bundle, private_md, public_md, private_html, persist=bool(plan.get("persist"))
        )
    # A no-op idempotent retry writes nothing and legacy sessions may lack the
    # HTML artifact; emit its path only when the file is really there so the
    # delivery contract's markdown fallback triggers instead of file-not-found.
    html_path = os.path.join(result["path"], "card-private.html")
    _emit({"status": result["status"], "session_id": args.session_id, "path": result["path"],
           "private_card": os.path.join(result["path"], "card-private.md"),
           "public_card": os.path.join(result["path"], "card-public.md"),
           "private_card_html": html_path if os.path.isfile(html_path) else None,
           "projection": projection, "projection_error": projection_error,
           "recoverable": bool(projection_error)})


def cmd_resume(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    if args.session_id:
        pending = session.load_pending(root, args.session_id)
        plan = pending.get("plan")
        existing = pending.get("question_surfaces")
        if args.question_surfaces:
            try:
                candidate = _load_json(args.question_surfaces, "question surfaces")
                validated = question_surface.validate_surfaces(plan, candidate)
            except (ReviewError, question_surface.QuestionSurfaceError) as exc:
                if existing is not None:
                    raise ReviewError(
                        "validated question surfaces are already fixed for this pending session"
                    ) from exc
                fallback = question_surface.build_presentations(plan)
                _emit({**_pending_for_agent(pending), "status": "surface_fallback",
                       "question_presentations": fallback,
                       "surface_error": str(exc),
                       "next_action": "present the unchanged engine question/options fallback"})
                return
            if existing is not None and session.canonical(existing) != session.canonical(validated):
                raise ReviewError("validated question surfaces are already fixed for this pending session")
            if existing is None:
                presentations = question_surface.build_presentations(plan, validated)
                session.save_pending(root, args.session_id,
                                     **{"question-presentations": presentations,
                                        "question-surfaces": validated})
                pending = session.load_pending(root, args.session_id)
            else:
                presentations = pending.get("question_presentations")
                if not isinstance(presentations, list):
                    raise ReviewError("validated question surface is missing its frozen presentation")
            _emit({**_pending_for_agent(pending), "status": "surface_validated",
                   "question_presentations": presentations,
                   "next_action": "present these exact questions in queue order, then run preview"})
            return
        if existing is not None:
            if not isinstance(pending.get("question_presentations"), list):
                raise ReviewError("validated question surface is missing its frozen presentation")
            pending["status"] = "surface_validated"
            pending["next_action"] = "reuse these exact question presentations, then run preview"
        else:
            pending["status"] = "engine_fallback"
            pending["question_presentations"] = question_surface.build_presentations(plan)
            pending["next_action"] = ("author eligible private surfaces with resume --question-surfaces, "
                                      "or present the unchanged engine fallback")
        _emit(_pending_for_agent(pending))
        return
    base = os.path.join(root, ".pending")
    pending = [] if not os.path.isdir(base) else sorted(
        x for x in os.listdir(base) if os.path.isdir(os.path.join(base, x)))
    _emit({"status": "pending" if pending else "idle", "pending_sessions": pending,
           "next_action": "run resume with --session-id" if pending else "run prepare"})


# Artifacts the recomputed session inherits verbatim. `answers`/`narrative` are
# what the user already answered; `question-surfaces`/`question-presentations`
# are the exact question bytes they were asked, frozen so an interrupted session
# never re-asks something subtly different. The preview cards are deliberately
# not here: they render the pre-anchor card and must be rebuilt by `preview`.
CASH_RECOMPUTE_CARRIED = ("answers", "narrative",
                          "question-surfaces", "question-presentations")

# The facts underneath the card the user already read, which a cash anchor may
# not move — stated positively, and it is the direction that matters (#665).
#
# The predecessor asked the opposite question. It excluded a hand-maintained set
# of cash-derived keys from `engine_state` and `engine_card`, then compared
# `state_snapshot` and `question_queue` **whole**, and that shape cannot be kept
# true: the recompute re-enters the entire lane, so any downstream effect of the
# re-entry — including its own second market-data resolution — arrived as
# "drift". The command refused the exact session it exists for, and a card-beat
# cash answer became unrecordable. What the recompute owes is not that nothing
# moved; a cash anchor is *supposed* to move the account pillar. It owes that
# the facts the user's answers were made against are the same facts. So this
# names those facts, and compares nothing else.
#
# Two rules for adding a row. It must be an input or a frozen observation, never
# a projection of one — a projection is where a cash-derived value eventually
# appears, and that is the defect above rebuilt. And it must be measurably
# invariant under `--cash` on the same book: measured across five mock books, an
# anchor moves `engine_state.cash`, `engine_card`'s `cash`/`acct_perf`/
# `honesty_ledger`, and `card_plan.required_honesty_keys`, and nothing else.

# The valuation inputs the card was priced from. `add-cash` reuses them rather
# than re-resolving (see `cmd_add_cash`), so on the ordinary path these are
# identical by construction and this row is the proof, not the hope.
CASH_RECOMPUTE_FRAME_KEYS = ("price_snapshot", "price_provenance", "price_request",
                             "valuation_frame", "splits", "splits_window",
                             "currency_meta", "market_context")
# The recorded book and the window it was read over. An edited CSV, a different
# input file, or a ledger some other session moved in between lands here.
CASH_RECOMPUTE_BOOK_KEYS = ("holdings", "portfolio_structure", "metrics",
                            "date_start", "date_end", "prev_end", "n_trades",
                            "n_round_trips", "n_held", "insufficient_data",
                            "review_tier", "problem_events")
# What the behavior review found. The driver and instrument maps reach a review
# only through here, so a map supplied to this command but not to `prepare` is a
# refusal rather than a silently re-attributed card.
CASH_RECOMPUTE_DIAGNOSIS_KEYS = ("ticker_diagnosis", "top_holes", "dims_raw",
                                 "overview", "strength", "payoff_attribution",
                                 "alpha_beta_breakdown", "what_if",
                                 "portfolio_structure", "prescriptions",
                                 "thesis_questions", "vs_market_gate")


def _named(mapping, keys):
    return {key: (mapping or {}).get(key) for key in keys}


def _question_identities(plan):
    """Which questions were asked, without their wording.

    The identity, not the bytes. A queue row's text quotes this period's
    numbers, so comparing it byte for byte makes the gate hostage to every
    figure on the card — which is how `question_queue` ended up in a refusal
    naming a surface a cash anchor cannot reach (#665). What has to hold is that
    the user's recorded answers still map onto the questions this session asks,
    and that is the id, the kind, and whether an answer was required.
    """
    return [{"id": row.get("id"), "kind": row.get("kind"), "required": row.get("required"),
             "ticker": row.get("ticker"), "cycle_id": row.get("cycle_id")}
            for row in plan.get("question_queue") or []]


CASH_RECOMPUTE_SOURCE_FACTS = (
    ("the input files", lambda plan: (plan.get("input") or {}).get("paths")),
    ("the valuation frame the card was priced from",
     lambda plan: {"engine_state": _named(plan.get("engine_state"),
                                          CASH_RECOMPUTE_FRAME_KEYS),
                   "price_feed": ((plan.get("input") or {}).get("price_feed")
                                  or {}).get("provenance")}),
    ("the recorded book",
     lambda plan: _named(plan.get("engine_state"), CASH_RECOMPUTE_BOOK_KEYS)),
    ("what the review diagnosed",
     lambda plan: _named(plan.get("engine_card"), CASH_RECOMPUTE_DIAGNOSIS_KEYS)),
    ("the positions with no recorded thesis",
     lambda plan: plan.get("missing_thesis_positions")),
    ("the rules the user was offered",
     lambda plan: {"candidate_rules": (plan.get("card_plan") or {}).get("candidate_rules"),
                   "candidate_comparison":
                       (plan.get("card_plan") or {}).get("candidate_comparison")}),
    ("the questions the user already answered", _question_identities),
)


def _cash_recompute_drift(before, after):
    """The source facts that moved under the recompute, as human-readable labels.

    Empty means the two plans were computed over the same facts — the anchor
    propagated into the account pillar and nothing underneath it moved, which is
    the outcome this command exists to produce rather than to refuse.
    """
    return [label for label, read in CASH_RECOMPUTE_SOURCE_FACTS
            if session.canonical(read(before)) != session.canonical(read(after))]


def cmd_add_cash(args):
    """Add a cash anchor to a prepared session and recompute it (#357, #507).

    The owner ruled out refusing `prepare` when no anchor was supplied: compute
    the first card, then put a directly answerable question in front of the user
    and re-render if they answer. That question lands at the card beat, so by
    the time an anchor arrives the user has already answered every required
    question and seen a card — which is the whole reason this is a subcommand
    rather than the documented `prepare --cash` rerun.

    `prepare --cash` is a full second review: it re-resolves market data (a
    later instant, therefore different closes) and mints a session with none of
    the first one's frozen work. This command re-enters the same lane with the
    anchor added, and the two halves below are what make that a recompute of the
    same review rather than a second one.

    **It reuses the frame instead of re-resolving it** (`frozen_market_frame`;
    `market_data.frame_frozen`). The same-day cache was carrying that claim
    before, and it could not: `MarketDataBundle.covers` refuses to serve a
    request naming a symbol the stored bundle failed to price — deliberately, so
    a transient outage gets a retry — so a single unpriced symbol anywhere in
    the universe made this command re-price the entire review at a second
    instant. Every downstream number then moved, and the recompute reported its
    own movement to the user as facts moving underneath them (#665). Amending a
    review does not want a retry; it wants the frame the card being amended was
    rendered from.

    **The position cap is reused on the same posture** (#758). `flows/first-
    review.md` step 6 collects a stated cap (`set-cap`) and a cash answer in
    the same message beat, and `set-cap` takes effect immediately by rewriting
    `profile.json` — so a cap stated just before this command runs would
    otherwise be picked up mid-amendment, changing `candidate_rules` under a
    card the user has already seen and tripping the drift gate below on "the
    rules the user was offered" the moment the two same-beat actions ran in
    that order. `references/data-contract.md`'s own words are that a standing
    cap change "reconciles against" the *next* review, so this pass freezes
    the cap this pending session actually carries (`_run_engine`'s
    `frozen_position_cap`) instead of re-reading `profile.json` live, exactly
    as it freezes the market frame above. Both orders of the two actions now
    succeed, and a `set-cap` run here still lands in `profile.json` for the
    review after this one.

    **And it proves the facts held rather than asserting it.** The source facts
    the user's answers were made against — the input files, that frame, the
    recorded book, the diagnosis, the rules they were offered, the questions
    they answered — must come back identical. When they do not (an edited CSV, a
    ledger another session moved, a driver map supplied here but not to
    `prepare`, a frame no longer recorded) the command refuses and names what
    moved, instead of re-basing answers onto numbers nobody saw. SKILL.md's
    "refetching live data would silently change the facts the user already
    answered against" is the rule; these two are its enforcement.

    A new session id is unavoidable and correct: the id is content-addressed
    from engine state and the anchor is part of that state. What must not change
    is the user's work, so the pending session's answers, narrative and frozen
    question surfaces are carried over, and the superseded pending directory is
    removed rather than left behind as a second, finalizable, cash-less session.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    pending = session.load_pending(root, args.session_id)
    plan = pending.get("plan") or {}
    status = ((plan.get("input") or {}).get("cash_anchor") or {})
    if status.get("status") == "anchored":
        raise ReviewError("this session already carries a cash anchor; there is nothing to add")
    if status.get("status") not in ("absent", "partial"):
        raise ReviewError(
            f"this session does not take a cash anchor "
            f"(input.cash_anchor.status={status.get('status')!r}, "
            f"reason={status.get('reason')!r}); see references/data-contract.md")
    try:
        anchor = json.loads(args.cash)
    except (TypeError, ValueError) as exc:
        raise ReviewError(f"--cash is not valid JSON: {exc}") from exc
    if not isinstance(anchor, (dict, list)) or not anchor:
        raise ReviewError("--cash takes one {currency,amount,as_of} anchor, or a list of them")
    # #623: a declared price dead end is an input of this session, recoverable
    # from its own plan rather than re-typed. Dropping it would leave the
    # recomputed session reading as a skipped recovery, and the draft gate would
    # then refuse a review the agent had already declared honestly.
    recovery = (((plan.get("input") or {}).get("price_feed") or {}).get("recovery") or {})
    declared_unavailable = (recovery.get("checked")
                            if recovery.get("outcome") == "declared_unavailable" else None)
    rerun = argparse.Namespace(
        root=root, language=plan.get("language") or "en", route=plan.get("route") or "auto",
        test_drive=False, session_nonce="", paths=list((plan.get("input") or {}).get("paths") or []),
        cash=args.cash, prices=args.prices, driver_map=args.driver_map,
        instrument_map=args.instrument_map, condition_checks=args.condition_checks,
        prices_unavailable=declared_unavailable,
        # #665: this pass amends a review the user has already read, so the
        # anchor is its only new input. Two enforcement points, one idea: the
        # market frame is reused rather than re-observed (`_run_engine`), and a
        # transaction file that grew is refused before anything is written
        # (`_verify_and_ingest_frozen_trades`).
        amending_session=True,
        # #758: the third enforcement point of the same idea. `state`
        # (`plan["engine_state"]`) already echoes back whichever cap this
        # pending session was actually prepared with -- trade_recap.py stamps
        # `state["max_position_pct"]` from the same `TR_MAX_POSITION_PCT` env
        # var `_run_engine` sets, so reading it back here needs no second
        # source of truth. `None` means the universal default was in force;
        # it is still an explicit freeze, not "no opinion", which is why
        # `_run_engine` keys off `hasattr` rather than truthiness.
        frozen_position_cap=(plan.get("engine_state") or {}).get("max_position_pct"),
        snapshot_json=None, card_json=None, state_json=None, timeout=args.timeout)
    result = _prepare_session(rerun)
    if result["status"] == "already_committed":
        raise ReviewError("the anchored review is already committed as session "
                          f"{result['session_id']}; there is nothing pending to recompute")
    recomputed = session.load_pending(root, result["session_id"])
    drift = _cash_recompute_drift(plan, recomputed.get("plan") or {})
    if drift:
        if result["status"] == "prepared" and result["session_id"] != args.session_id:
            # The refused recompute must leave nothing finalizable behind: a
            # pending session carrying an anchor and a card nobody saw is worse
            # than no recompute at all.
            shutil.rmtree(session.pending_dir(root, result["session_id"]), ignore_errors=True)
        raise ReviewError(
            "the facts moved: this is not the anchor propagating into the account pillar, "
            "which is expected and allowed — it is that the review underneath it is no longer "
            "the one the user answered. These no longer match the session you are amending: "
            + ", ".join(drift)
            + ". The answers already given were made against different facts, which is what an "
              "edited input file, a ledger another session moved, or a driver/instrument map "
              "supplied here but not to prepare looks like — and, when the frame itself moved, "
              "a review whose frozen prices are no longer on record. Pass the same "
              "--prices/--driver-map/--instrument-map this session was prepared with; otherwise "
              "start a fresh review with prepare --cash rather than re-basing answers onto "
              "numbers the user never saw")
    carried = {name: pending.get(name.replace("-", "_")) for name in CASH_RECOMPUTE_CARRIED}
    session.save_pending(root, result["session_id"], **carried)
    if result["session_id"] != args.session_id:
        # Reconstructible by prepare, and everything the user authored has just
        # been carried forward. Leaving it would leave a cash-less session that
        # `preview`/`finalize` would happily commit.
        shutil.rmtree(session.pending_dir(root, args.session_id), ignore_errors=True)
    recomputed_plan = (session.load_pending(root, result["session_id"]).get("plan") or {})
    # #662: present only when this --cash payload was a percentage (trade_recap's
    # resolve_cash_anchor_input stamped it into engine_state); the absolute-amount
    # path leaves this None, so the response below carries no new key at all --
    # byte-identical to before the percentage format existed.
    anchor_conversion = (recomputed_plan.get("engine_state") or {}).get("cash_anchor_conversion")
    response = {"status": "anchored", "session_id": result["session_id"],
                "superseded_session_id": args.session_id,
                # #665: the gate has exactly two verdicts and both are stated. This is
                # the allowed one — the anchor reached the account pillar and every
                # fact underneath it held. The other is the refusal above, and the
                # difference between them is what this command used to get wrong.
                "recompute": {"outcome": "anchor_propagated",
                              "market_frame": "reused",
                              "source_facts_verified": [label for label, _read
                                                        in CASH_RECOMPUTE_SOURCE_FACTS]},
                "review_plan": _plan_for_agent(recomputed_plan),
                "carried_forward": sorted(name for name, value in carried.items()
                                          if value is not None),
                "next_action": (
                    "the account pillar is now computed. Every required answer, thesis and "
                    "frozen question surface carried over unchanged, and "
                    "card_plan.required_honesty_keys gained the account-basis key — write one "
                    "sentence for it in narrative.honesty, then rerun preview and finalize on "
                    "this session id"
                    + (" — and show anchor_conversion's derivation to the user first: they "
                       "answered a percentage, so the stored dollar amount must not reach them "
                       "silently." if anchor_conversion else ""))}
    if anchor_conversion:
        response["anchor_conversion"] = anchor_conversion
    _emit(response)


def cmd_render(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    bundle = session.load_committed(root, args.session_id)
    private_md = card_renderer.render_private(bundle)
    public_md = card_renderer.render_public(bundle)
    if args.format == "private-markdown":
        sys.stdout.write(private_md)
        return
    if args.format == "public-markdown":
        sys.stdout.write(public_md)
        return
    _emit({"session_id": args.session_id, "private_card": private_md, "public_card": public_md})


def cmd_repair(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    outcome = session.repair_projections(root)
    _emit({"status": "repaired" if not outcome["errors"] else "partially_repaired", **outcome})


def cmd_set_cap(args):
    """Record (or clear) the user's standing single-position cap (#324).

    Persists to ``profile.json`` so diagnosis, prescription, and the rule text
    all reconcile against the user's own number next review. Fail-closed: a cap
    outside (0,1) is rejected, never silently stored. The write is a whole-file
    atomic replace that preserves any other profile keys."""
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    os.makedirs(root, exist_ok=True)
    path = _profile_path(root)
    profile = {}
    if os.path.exists(path):
        try:
            loaded = session.read_json(path)
            if isinstance(loaded, dict):
                profile = loaded
        except (OSError, ValueError):
            profile = {}
    if args.clear:
        profile.pop("max_position_pct", None)
        ledger.atomic_write_text(path, session.pretty(profile))
        _emit({"status": "cleared", "root": root, "max_position_pct": None})
        return
    cap = card_renderer.valid_position_cap(args.pct)
    if cap is None:
        raise ReviewError(
            "max position cap must be a fraction strictly between 0 and 1 "
            "(for example 0.25 for 25%), not a percentage or an out-of-range value")
    profile["max_position_pct"] = cap
    ledger.atomic_write_text(path, session.pretty(profile))
    _emit({"status": "set", "root": root, "max_position_pct": cap})


def _muted_rule_ids(root):
    """The rule lines the user asked not to be asked about (#416).

    Standing preference, so it lives in ``profile.json`` beside the position cap
    and not in ``rules.jsonl``: that file is a tier-3 rebuildable projection
    (`references/data-contract.md`), rebuilt from committed bundles by
    ``repair-projections``, and a bundle carries commitments only. A mute stored
    there would survive until the first repair and then silently un-mute every
    rule — the user starts being asked again with no signal that anything
    changed. Same fail-soft posture as the cap: an unreadable profile means no
    mutes, never a crash."""
    path = _profile_path(root)
    if not os.path.exists(path):
        return []
    try:
        profile = session.read_json(path)
    except (OSError, ValueError):
        return []
    ids = profile.get("muted_rules") if isinstance(profile, dict) else None
    return [str(rule_id) for rule_id in ids if str(rule_id).strip()] if isinstance(ids, list) else []


def _write_mutes(root, line_ids):
    """Replace the profile's mute list, preserving every other preference."""
    path = _profile_path(root)
    profile = {}
    if os.path.exists(path):
        try:
            loaded = session.read_json(path)
            if isinstance(loaded, dict):
                profile = loaded
        except (OSError, ValueError):
            profile = {}
    if line_ids:
        profile["muted_rules"] = sorted(set(line_ids))
    else:
        profile.pop("muted_rules", None)
    ledger.atomic_write_text(path, session.pretty(profile))


def _clear_mute(root, line_id, muted_ids):
    _write_mutes(root, [rule_id for rule_id in muted_ids if rule_id != line_id])


def cmd_mute_rule(args):
    """Silence a rule without retiring it, or bring it back (#416).

    A rule the user no longer wants to be asked about has had exactly two exits:
    keep answering for it, or lose it. Muting is the third — the rule stops
    entering the card's attention rotation while its reconciliation keeps
    running, so the statistics are there when the user comes back to decide.

    Owner direction, 2026-07-26: establish the rule first, keep accumulating,
    look again once there is data. That only works if the accumulation is real,
    which is why `problems.snapshot` reconciles muted rules on the same path as
    tracked ones.

    Mute is **state on a stable identity**, never a new rule row. The identity is
    the rule *line* (`problems.rule_line_id` — the root of the `revises` chain),
    so a later revision of the same rule inherits the mute, and nothing about
    muting can move the `rule_id` that `_rule_breach_history` keys answered
    breach questions on. An earlier cut of this wrote a superseding row instead
    and produced exactly those two failures: a required question re-asked after
    an unmute, and a chain with two live heads when a revision landed on top.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    os.makedirs(root, exist_ok=True)
    muted_ids = _muted_rule_ids(root)
    tracking, muted = problems.load_rules(os.path.join(root, "rules.jsonl"), muted_ids)
    lines = {}
    for row in tracking + muted:
        lines[problems.rule_line_id(row)] = row
        lines[str(row.get("rule_id"))] = row          # the head id is what a payload shows
    rule = lines.get(str(args.rule_id))
    if rule is None:
        if args.unmute and str(args.rule_id) in set(muted_ids):
            # A silenced line whose rows are gone (a reset, a hand-edited file):
            # the profile entry is inert but permanent, and refusing here would
            # leave the user no way to clear it.
            _clear_mute(root, str(args.rule_id), muted_ids)
            _emit({"status": "tracking", "root": root, "rule_line_id": str(args.rule_id),
                   "rule_id": None, "text": None,
                   "note": "the rule itself is no longer in rules.jsonl; cleared the stale entry"})
            return
        raise ReviewError(
            f"no live rule matching {args.rule_id!r} — a superseded id names a version, not a "
            f"rule (live: {sorted({problems.rule_line_id(r) for r in tracking + muted}) or 'none'})")
    line_id = problems.rule_line_id(rule)
    target_muted = not args.unmute
    # Effective state, not the profile's copy of it: `load_rules` also honours a
    # row-level `status: "muted"` (contract since #137), so asking the profile
    # would tell a user their rule is tracked while the engine's own reader
    # silences it — one boolean with two sources of truth.
    silent_now = any(problems.rule_line_id(row) == line_id for row in muted)
    if silent_now == target_muted:
        raise ReviewError(f"rule {line_id!r} is already {'muted' if target_muted else 'tracking'}")
    if args.unmute and line_id not in set(muted_ids):
        raise ReviewError(
            f"rule {line_id!r} is silenced by a `status: \"muted\"` field in rules.jsonl, not by "
            "a preference this command owns — remove that field from the row to bring it back")
    remaining = [rule_id for rule_id in muted_ids if rule_id != line_id]
    if target_muted:
        remaining.append(line_id)
    _write_mutes(root, remaining)
    _emit({"status": "muted" if target_muted else "tracking", "root": root,
           "rule_line_id": line_id, "rule_id": rule.get("rule_id"), "text": rule.get("text")})


def _evaluation_path(root):
    return os.path.join(root, "trade_evaluations.jsonl")


def _anchor_position_row(position, anchor_date):
    """One synthesized buy row for an anchored position, or None for a
    structurally malformed entry — ledger.derive_holdings tolerates the same
    shapes as a soft ``bad_snapshot_position`` integrity issue rather than
    raising, and reconstruction here mirrors that leniency.

    A missing ``avg_cost`` falls back to ``market_value / shares``, the same
    alternative cost basis ``snapshot_adapter._valuation()`` itself accepts.
    A position with neither fails closed by name: unlike ``derive_holdings``
    (which only needs share counts), this module must put a price on every
    row it hands to trade_recap's FIFO functions, and inventing one would
    silently mis-state a real held position's weight — worse than refusing.
    """
    if not isinstance(position, dict):
        return None
    ticker = position.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        return None
    try:
        shares = float(position.get("shares"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(shares) or shares <= 1e-9:
        return None
    price = position.get("avg_cost")
    if price is None:
        market_value = position.get("market_value")
        if market_value is not None:
            try:
                price = float(market_value) / shares
            except (TypeError, ValueError, ZeroDivisionError):
                price = None
    if price is None:
        raise ReviewError(
            f"the ledger's snapshot anchor carries no avg_cost or market_value for {ticker}; "
            "consider cannot price this position without a cost basis")
    try:
        price = float(price)
    except (TypeError, ValueError):
        raise ReviewError(f"the ledger's snapshot anchor has a non-numeric cost basis for {ticker}")
    if not math.isfinite(price) or price <= 0:
        raise ReviewError(f"the ledger's snapshot anchor has a non-positive cost basis for {ticker}")
    return {"ticker": symbols.canonical_ticker(ticker), "side": "buy", "qty": shares,
            "price": price, "date": anchor_date, "market": (position.get("market") or "US"),
            "currency": (position.get("currency") or "USD").upper()}


def _ledger_trade_row(event):
    """One trade_recap row from a ledger ``trade`` event
    (``{type:"trade", date, ticker, action, qty, price, market, currency}``),
    or None for a structurally malformed event. Mirrors ``ledger._norm_trade``'s
    own tolerance rather than calling it directly, because that helper drops
    market/currency, which this module's rows need."""
    try:
        date = dt.date.fromisoformat(str(event.get("date")))
        ticker = event["ticker"]
        side = str(event.get("action", "")).strip().lower()
        qty = float(event["qty"])
        price = float(event["price"])
    except (KeyError, TypeError, ValueError):
        return None
    if (not isinstance(ticker, str) or not ticker.strip() or side not in ("buy", "sell")
            or not math.isfinite(qty) or qty <= 0 or not math.isfinite(price) or price <= 0):
        return None
    return {"ticker": symbols.canonical_ticker(ticker), "side": side, "qty": qty,
            "price": price, "date": date, "market": (event.get("market") or "US"),
            "currency": (event.get("currency") or "USD").upper()}


def _ledger_trade_events(events):
    """Only real, dated trade events from the ledger — deliberately *not*
    ``_rows_from_ledger``'s reconstruction. That function also synthesizes one
    priced "buy" row per position from the latest snapshot anchor for the
    FIFO/cost-basis math ``consider`` needs, and fails closed when a position
    carries no cost basis at all. A snapshot anchor legitimately declares
    shares with no cost basis — a user re-syncing positions may not know it —
    and a synthesized anchor row has no genuine execution date to begin with:
    reading one as a matched trade would misrepresent a declared holding as
    something the user did on a specific day. A caller that only needs to
    know whether a real trade happened, never what the book would be worth,
    uses this instead — it never raises, matching ``_ledger_trade_row``'s own
    tolerance for a malformed event."""
    rows = []
    for event in events:
        if event.get("type") != "trade":
            continue
        row = _ledger_trade_row(event)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: row["date"])
    return rows


def _rows_from_ledger(events):
    """trade_recap.load()'s row shape, reconstructed from ledger events the
    way ``ledger.derive_holdings`` trusts them: the same replay base it uses
    (``ledger.latest_anchor(..., declared_only=True)`` — a trades-derived
    restatement is not one, so this lane replays the trades it summarizes
    exactly as ``derive_holdings`` does), synthesized as one buy per position
    dated at the anchor's ``as_of``, then every trade strictly after that
    date layered on top — an anchor is ``as_of``'s close-of-day state, so a
    same-day trade is already reflected in its declared numbers, the same
    cutoff ``derive_holdings`` applies. No anchor at all falls back to every
    trade event, matching ``derive_holdings``' own backward-compatible
    pure-replay path."""
    anchor = ledger.latest_anchor(events, declared_only=True)
    anchor_date = None
    rows = []
    if anchor is not None:
        anchor_date = dt.date.fromisoformat(str(anchor.get("as_of")))
        # A ticker declared twice in one anchor is malformed input, not two
        # positions; keep only the last declaration, the same overwrite
        # ledger.derive_holdings' own `pos[t] = {...}` assignment produces,
        # so a hand-edited or corrupted ledger cannot double-count a position
        # here while derive_holdings reads it as one.
        by_ticker = {}
        for position in anchor.get("positions") or []:
            if isinstance(position, dict) and isinstance(position.get("ticker"), str):
                # Canonical, like `derive_holdings`' own key since #803, so the
                # mirrored overwrite stays a mirror.
                by_ticker[symbols.canonical_ticker(position["ticker"])] = position
            else:
                by_ticker[id(position)] = position   # malformed entry: let _anchor_position_row reject it
        for position in by_ticker.values():
            row = _anchor_position_row(position, anchor_date)
            if row is not None:
                rows.append(row)
    for event in events:
        if event.get("type") != "trade":
            continue
        row = _ledger_trade_row(event)
        if row is None:
            continue           # malformed event — ledger.derive_holdings tolerates the same rows
        if anchor_date is not None and row["date"] <= anchor_date:
            continue
        rows.append(row)
    rows.sort(key=lambda row: row["date"])
    return rows


def _legacy_transaction_basis(rows, last_px):
    """Frozen disclosure for the explicit CSV compatibility lane.

    CSV input remains the historical FIFO/round-trip route.  It is not a
    declared current-book anchor, so callers can see that its completeness is
    unverified instead of mistaking a context-free invocation for the ledger
    basis used by current-book claims.
    """
    as_of = rows[-1]["date"]
    evidence = [{"ticker": row["ticker"], "side": row["side"], "qty": row["qty"],
                 "price": row["price"], "date": row["date"].isoformat(),
                 "currency": row.get("currency", "USD")}
                for row in rows]
    digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"source": "transactions", "as_of": as_of.isoformat(),
            "stale_days": (dt.date.today() - as_of).days, "completeness": "unverified",
            "cost_basis": "fifo", "valuation_basis": "priced" if last_px else "unpriced",
            "reconciliation_ref": None, "state_version": "csv-v1:" + digest}


def _canonical_consider_before(rows, basis, projection, last_px, max_pos_override,
                               cash_anchor, fx, excluded_holdings=()):
    """Use #494's one canonical denominator for ledger-backed ``consider``.

    The consequence engine still owns the hypothetical after-state arithmetic.
    This facade replaces only its *before* sizing facts with the typed
    PortfolioBasis projection and refuses any mismatch, so an old FIFO/cost
    reader can never silently become a second current-book truth source.

    A ``bounded_valued_subset`` projection is accepted (#515): a holding that
    cannot be valued is excluded and named, not a reason to refuse the whole
    question. What is *not* relaxed is the agreement requirement — the two
    readers must name the same excluded set. If the projection can value a
    holding this adapter dropped (or the reverse), they are describing two
    different books, and the weight the user is shown would be measured
    against a denominator no other surface uses. That stays a refusal, because
    the alternative is a silently wrong percentage.

    An *integrity* exclusion (#673) is the one exclusion the two readers are
    expected to disagree about, and it is not a disagreement about valuation.
    The projection can value the holding perfectly well — it has a price and a
    cost; what the ledger's integrity record says is that the share count those
    are multiplied by is not derivable from the supplied history. So the
    agreement gate above is checked against the *valuation* half of the
    excluded set only, and the integrity half is taken out of the denominator
    here instead.
    """
    projected = projection.to_dict()
    coverage = projected["coverage"]
    if not projected["applicable"] or coverage["scope"] == "unavailable_mixed_currency":
        raise ReviewError("canonical PortfolioBasis has no usable current-book sizing projection")
    excluded_tickers = {row["ticker"] for row in excluded_holdings or ()}
    integrity_excluded = consequence.integrity_excluded_tickers(excluded_holdings)
    valuation_excluded = excluded_tickers - integrity_excluded
    projection_unavailable = set(coverage["unavailable"])
    # A holding the projection CAN value but this adapter cannot use: it has a
    # current price and no cost on record. The projection weighs it at market;
    # the consequence engine cannot represent it at all, because a synthetic
    # row is `{side: buy, qty, price}` where price is the cost. Keeping the
    # projection's whole-book denominator for `before` while `after` is
    # computed without that position would compare two different books, so
    # this refuses rather than produce a delta measured across two
    # denominators. Refusing here is narrow and actionable -- the two paths
    # below both work today -- and #528 tracks teaching the consequence engine
    # about value-only positions so it need not refuse at all.
    priced_without_cost = sorted(valuation_excluded - projection_unavailable)
    if priced_without_cost:
        raise ReviewError(
            f"{', '.join(priced_without_cost)} has a current price but no cost on record, and a "
            "consequence answer needs a cost for every position it reasons about. Add the "
            "average cost for it, or ask again without --prices to get the answer over the "
            "part of the book that has costs.")
    if projection_unavailable != valuation_excluded:
        raise ReviewError(
            "canonical PortfolioBasis and the consider adapter disagree about which "
            "holdings cannot be valued: projection "
            f"{sorted(projection_unavailable)} vs adapter {sorted(valuation_excluded)}")
    try:
        before = consequence.portfolio_state(rows, last_px=last_px,
                                             max_pos_override=max_pos_override,
                                             cash_anchor=cash_anchor, fx=fx)
    except consequence.ConsequenceError as exc:
        # #600's refusal reaches this facade too. The projection above rejects
        # `unavailable_mixed_currency`, but that scope answers a question about
        # the canonical book's own valuation, not about whether *this* caller
        # supplied the rate the FIFO adapter needs, so the two can disagree.
        # Surfaced as a ReviewError rather than escaping as a traceback.
        raise ReviewError(str(exc)) from exc
    holdings = {ticker: holding for ticker, holding in basis.current_book["holdings"].items()
                if ticker not in excluded_tickers}
    if set(before["held"]) != set(holdings):
        raise ReviewError("canonical PortfolioBasis holdings disagree with consider adapter")
    for ticker, holding in holdings.items():
        held = before["held"][ticker]
        if (abs(held["shares"] - float(holding["shares"])) > 1e-6
                or abs(held["cost"] - float(holding["cost_total"])) > 1e-6):
            raise ReviewError("canonical PortfolioBasis cost disagrees with consider adapter")
    # #673. The projection is the canonical *whole-book* denominator and stays
    # that way -- it is the record, and nothing here rewrites it. What this
    # builds is the denominator for the bounded book the answer is actually
    # about, from the projection's own per-holding values, restricted to the
    # holdings that survived. Re-dividing those values is deliberately not a
    # second valuation: no price, cost, share count or FX rate is read here, so
    # a value that reached this line came from `sizing_projection` and nowhere
    # else. `math.fsum` is `value_partition`'s own summation, and it is exactly
    # rounded, so with nothing integrity-excluded the sum is bit-for-bit the
    # projection's denominator and every weight is the projection's weight --
    # the pre-#673 answer, unchanged.
    usable_values = {ticker: entry["value"] for ticker, entry in projected["values"].items()
                     if entry["applicable"] and ticker not in integrity_excluded}
    denominator = math.fsum(usable_values.values()) if usable_values else 0.0
    if not math.isfinite(denominator) or denominator <= 0:
        raise ReviewError(
            "canonical PortfolioBasis has no holding left to size this answer against once "
            f"{sorted(integrity_excluded)} is excluded for an integrity warning")
    weights = {ticker: value / denominator for ticker, value in usable_values.items()}
    if set(weights) != set(before["held"]):
        raise ReviewError("canonical PortfolioBasis sizing coverage is incomplete")
    before = dict(before)
    before["weights"] = weights
    # The legibility lists `portfolio_state` stamped were measured against the
    # weights this line just replaced, so they are recomputed rather than
    # carried forward: a listed position's weight must always be the weight the
    # answer will actually show, and this facade is the one place a state's
    # denominator changes after it was built (#598/#599).
    (before["unclassified_holdings"],
     before["undecomposed_etfs"]) = consequence.book_legibility(before["held"], weights)
    before["max_ticker"] = max(sorted(weights), key=weights.get)
    before["max_pct"] = weights[before["max_ticker"]]
    before["oversize_triggered"] = (
        before["max_pct"] > trade_recap.effective_oversize_trigger(max_pos_override))
    return before


def _usable_facts_snapshot(root):
    """Already-computed facts the last finalized review froze, for a
    `consider` refusal that cannot compute a fresh consequence (#674).

    Reads ``last_state.json`` through ``_previous_state`` -- the same helper
    ``_recorded_splits`` already relies on for a lane with no review of its
    own -- and carries forward only values that call for no new arithmetic:
    the account's own whole-book concentration reading and the rule the user
    is actually committed to, both copied verbatim from
    ``CONSIDER_REFUSAL_CONCENTRATION_KEYS`` / ``CONSIDER_REFUSAL_COMMITMENT_KEYS``.
    Never rescaled, recombined, or judged against the premise the caller
    supplied: this function's whole job is to hand over a bounded, frozen
    fact surface, not to compute one.

    Returns ``None`` when no review has ever been finalized in this root, or
    when a finalized one froze neither a concentration reading nor a
    commitment. The caller reads ``None`` as "no usable computed fact
    exists" and falls back to the no-book framing contract
    (references/decision-framing.md) rather than inventing a portfolio claim
    -- the owning issue's disposition is explicit that a non-recoverable
    refusal must never manufacture a fact the last review did not actually
    freeze.
    """
    state = _previous_state(root)
    if not isinstance(state, dict):
        return None
    metrics = state.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    concentration = {key: metrics[key] for key in CONSIDER_REFUSAL_CONCENTRATION_KEYS
                     if key in metrics and metrics[key] is not None}
    commitment = state.get("commitment")
    commitment = commitment if isinstance(commitment, dict) else {}
    rule = {key: commitment[key] for key in CONSIDER_REFUSAL_COMMITMENT_KEYS
            if key in commitment and commitment[key] is not None}
    if not concentration and not rule:
        return None
    facts = {"as_of": state.get("date_end")}
    if concentration:
        facts["concentration"] = concentration
    if rule:
        facts["commitment"] = rule
    return facts


def _consider_valuation_frame(basis, feed, *, agent_supplied):
    """Bind ``consider``'s already-normalized prices and FX to this exact book.

    ``price_feed`` carries native closes plus USD-per-unit FX; PortfolioBasis'
    typed frame is the canonical receipt that can safely aggregate those facts.
    The holdings filter is load-bearing: a feed may also contain the premise
    ticker or an already-closed instrument, while a valuation frame must exactly
    partition the current book it sizes.
    """
    if feed is None or not (feed.get("prices") or {}):
        return None
    holdings = basis.current_book["holdings"]
    # Keep the established single-currency receipt byte-for-byte. Its sizing
    # lane already reads the legacy numeric map, while the typed native/FX
    # frame is specifically what unlocks otherwise-inapplicable mixed-currency
    # sizing. Narrowing this repair further also preserves the existing
    # priced-without-cost refusal and evaluation identities on ordinary books.
    if len({holding["currency"] for holding in holdings.values()}) <= 1:
        return {"as_of": feed["as_of"],
                "prices": {ticker: row["close"]
                           for ticker, row in feed["prices"].items()}}
    currency_by_ticker = {ticker: holding["currency"]
                          for ticker, holding in holdings.items()}
    conflicts = price_feed.currency_conflicts(feed, currency_by_ticker)
    if conflicts:
        details = ", ".join(
            f"{row['ticker']} is {row['trades']} in the recorded holding but its "
            f"close is {row['feed']}"
            for row in conflicts)
        raise ReviewError(
            "current-book price currency conflict: " + details
            + ". Correct the close currency and ask again; the trade was not evaluated.")
    aggregate = "USD"  # price_feed.fx_rates' declared aggregate direction
    prices = {ticker: feed["prices"].get(ticker, {}).get("close")
              for ticker in holdings}
    observations = {
        ticker: {"observed_at": feed["prices"][ticker]["observed_date"],
                 "basis_date": feed["prices"][ticker]["basis_date"]}
        for ticker in holdings if ticker in feed["prices"]
    }
    rates = price_feed.fx_rates(feed)
    needed_fx = {holding["currency"] for holding in holdings.values()} - {aggregate}
    provenance = "agent_feed" if agent_supplied else "engine_fetch"
    frame = portfolio_basis.build_valuation_frame(
        as_of=feed["as_of"], positions=holdings, prices=prices,
        price_observations=observations,
        aggregate_currency=aggregate,
        fx_to_aggregate={currency: rates[currency]
                         for currency in needed_fx if currency in rates},
        price_provenance=provenance, fx_provenance=provenance,
    )
    # Preserve the partial typed frame through the canonical reader.  The
    # caller turns its exact missing-price/missing-FX coverage into the existing
    # recovery payload before any consequence can use a cost denominator.
    return frame.to_dict()


def _ticker_collision_detail(events, splits):
    """The ledger's own words for a two-spelling ticker collision, or ``None``.

    Read back off ``ledger.derive_holdings``' integrity record rather than
    re-detected here, so the refusal the user sees and the condition that
    stopped the book are the same fact (#803). Called only after
    ``query_current_book`` has already refused, so the second derivation costs
    nothing on any path that answers, and it never raises: a book too corrupt
    to derive at all is one this helper simply has no collision to report for,
    and the general refusal below stands.
    """
    try:
        integrity = ledger.derive_holdings(events, splits=splits).get("integrity") or ()
    except Exception:                                       # noqa: BLE001
        return None
    named = [row.get("detail") for row in integrity
             if isinstance(row, dict) and row.get("issue") == "bad_ticker_collision"
             and isinstance(row.get("detail"), str)]
    return "; ".join(named) if named else None


def _consider_rows(args, root, feed=None, last_px=None, splits=None, *, agent_supplied=False):
    """Resolve the book ``consider`` reasons over: the supplied CSV paths, or
    a reconstruction from ``<root>/ledger.jsonl`` when none are given (issue
    #456 names this the ledger basis, distinct from a review's own CSV/FIFO
    path — the two can disagree about a position's weight, and the record
    says which one it used rather than implying a currency it does not have).
    Fails closed when neither source yields a usable row: an empty book
    cannot answer a pre-trade question, and inventing one would be worse than
    refusing. A book where only *some* holding cannot be used is not that case
    -- because it could not be valued (#515) or because the ledger's integrity
    record names it (#673): those holdings come back in the fifth return value
    and must be carried to wherever the derived numbers are stated.

    ``splits`` reaches both routes, because both accumulate share counts and a
    split is what makes two of them incomparable (#550/#558). The two apply it
    differently, and that difference is the established one: the ledger route
    hands the map to the canonical book, which carries the *running position*
    across each split, while the CSV route rebases the rows themselves onto
    today's basis with ``trade_recap.adjust_for_splits`` — the same call
    ``prepare`` makes at ``trade_recap.py:2365``, and the right one here
    because ``last_px`` is a current quote and the rows have to be denominated
    against it. Absent map, both degrade to as-transacted quantities exactly as
    before.

    The map is resolved once, here, for both routes: an agent-supplied one
    (``--prices``' envelope, passed in) wins, otherwise the one the last review
    froze. `consider` is a single CLI call, so unlike `refresh` it has no
    two-call determinism to protect — but it still never fetches, per #558's
    retrieval ruling: no store, no schedule, no new path."""
    splits = _effective_splits(root, splits)
    if args.paths:
        paths = [os.path.abspath(os.path.expanduser(p)) for p in args.paths]
        for path in paths:
            if not os.path.isfile(path):
                raise ReviewError(f"CSV path does not exist: {path}")
        rows = trade_recap.load(paths)
        if not rows:
            raise ReviewError(
                "none of the supplied CSV paths contained a usable BUY/SELL trade row; "
                "consider cannot answer against an empty book")
        # The rows are what `consequence.portfolio_state` sums into the book
        # this answer reasons about, so an unadjusted pre-split quantity is
        # #558's defect on the one route that never got a fix: 90 bought
        # before a ten-for-one minus 100 sold after it is zero, and `consider`
        # then challenges a trade against a book missing the position it is
        # about. Rebase before the basis digest is taken, so the frozen
        # `state_version` describes the rows the answer actually used.
        try:
            trade_recap.adjust_for_splits(rows, splits)
        except split_policy.SplitDataError as exc:
            raise ReviewError(f"split data rejected: {exc}") from exc
        # A CSV book has no basis-level exclusion: every row is a real trade
        # the user supplied, not a holding whose cost could not be read.
        return rows, _legacy_transaction_basis(rows, last_px), None, None, []
    ledger_path = os.path.join(root, "ledger.jsonl")
    # A ledger-backed current-book answer has one owner: PortfolioBasis.  Do
    # not reconstruct source events into FIFO rows here; that was a second
    # cost/weight reader and diverged on multi-lot partial sells.
    try:
        events, skipped_lines = ledger.load_ledger(ledger_path)
    except ledger.LedgerIntegrityError as exc:
        raise ReviewError(str(exc)) from exc
    if not events:
        raise ReviewError(
            f"no usable trade or snapshot history in {ledger_path}; run a review first, or pass "
            "CSV paths directly, before asking consider about a hypothetical trade")
    # #674: every refusal in this block is genuinely non-recoverable -- no
    # corrected premise and no different ticker fixes structural corruption
    # (`basis is None`), an integrity warning this route cannot scope to one
    # holding, or every holding being excluded and leaving no usable row.
    # That is what distinguishes this block from the single excluded-holding
    # refusal `consequence.consequence` raises later (still one ticker away
    # from answerable) and from a plain caller error above (still one
    # corrected argument away). Wrapped so every ReviewError raised inside --
    # whichever of the three fires -- carries the same bounded usable_facts
    # packet, attached once here rather than duplicated at each raise site.
    # Learn the exact canonical holdings first, then bind the normalized native
    # closes and FX to that same book. The #674 packet belongs only to a
    # structurally unusable recorded book; a fixable close/FX problem must stay
    # outside that catch so it never masquerades as a bounded final refusal.
    try:
        basis = portfolio_basis.query_current_book(
            events, skipped_lines=skipped_lines,
            reference_as_of=dt.date.today().isoformat(),
            splits=splits)
        if basis is None:
            # #803. `query_current_book` returns None for every unknowable
            # book, which is the right shape for corruption nobody can act on
            # — but a ticker collision *is* actionable, and a refusal the user
            # cannot act on is indistinguishable from a broken product. Named
            # here, on the refusal path only, so failing closed still says
            # which two records to reconcile.
            collision = _ticker_collision_detail(events, splits)
            if collision:
                raise ReviewError(
                    f"{ledger_path} records {collision}. Those are one instrument to this "
                    "engine, and which of the two declarations is the position is not "
                    "derivable from the record — so no consequence is computed rather than "
                    "one of them silently winning. Record it one way, then ask again.")
            raise ReviewError(
                f"no trustworthy canonical current book in {ledger_path}; pass CSV paths for the "
                "separate historical transaction view")
        preliminary_basis = basis.to_dict()
        try:
            rows, excluded_holdings = consequence.rows_from_portfolio_basis(preliminary_basis)
        except consequence.ConsequenceError as exc:
            raise ReviewError(str(exc)) from exc
    except ReviewError as exc:
        raise ReviewError(str(exc), payload_extra={"usable_facts": _usable_facts_snapshot(root)}) from exc

    valuation_frame = _consider_valuation_frame(
        basis, feed, agent_supplied=agent_supplied)
    if valuation_frame is not None:
        basis = portfolio_basis.query_current_book(
            events, skipped_lines=skipped_lines, valuation_manifest=valuation_frame,
            reference_as_of=dt.date.today().isoformat(), splits=splits)
        if basis is None:  # defensive: identical validated events yielded the book above
            raise ReviewError(
                f"no trustworthy canonical current book in {ledger_path}; pass CSV paths for "
                "the separate historical transaction view")

    basis_dict = basis.to_dict()
    # Freeze only the portable fact identity/disclosure envelope.  The full
    # current_book remains the canonical ledger query, not a copied second
    # persisted book inside every evaluation. A sizing failure is a valuation
    # refusal, never one of #674's three bounded structural refusal classes.
    projection = portfolio_basis.sizing_projection(basis)
    if projection is None:
        raise ReviewError("canonical PortfolioBasis sizing projection is invalid")
    basis_meta = {key: basis_dict[key] for key in (
        "source", "as_of", "stale_days", "completeness", "cost_basis",
        "valuation_basis", "reconciliation_ref", "state_version")}
    basis_meta["valuation_coverage"] = projection.to_dict()["coverage"]
    return rows, basis_meta, basis, projection, excluded_holdings


def _load_json_arg(value, label):
    """Accept ``value`` as either a path to a JSON file or an inline JSON
    object — both are equally natural for an agent to produce. A value that
    names a readable file is read from disk; anything else is parsed as
    inline JSON directly."""
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{label} must not be empty")
    candidate = os.path.abspath(os.path.expanduser(value))
    if os.path.isfile(candidate):
        try:
            with open(candidate, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as exc:
            raise ReviewError(f"cannot read {label} file {candidate}: {exc}") from exc
    else:
        try:
            payload = json.loads(value)
        except ValueError as exc:
            raise ReviewError(
                f"{label} is neither a readable file path nor valid inline JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewError(f"{label} must decode to a JSON object")
    return payload


def _validate_agent_case(payload):
    """Fail-closed structural precheck for ``--agent-case``: cheap shape
    only, run before any book is read or any number is computed (right
    after the flag is loaded in ``cmd_consider``). Both ``for`` and
    ``against`` are required once ``agent_case`` is sent at all: owner
    ruling 2026-07-27, the agent lists the case for and against and does
    not take a position, so a one-sided submission is refused rather than
    accepted.

    This does not enforce the exact field set a claim may carry — that set
    is provenance-dependent (``anchor``/``worsens`` for ``engine_fact``,
    ``source``/``as_of`` for ``public_fact``; see
    ``schemas/answer-provenance.schema.json``'s ``$defs``) and is checked
    exactly once, by ``answer_provenance.validate_agent_case``, after the
    frozen ``consequence``/``rule_collisions`` this call needs actually
    exist (docs/development-guide.md section 7: one reader, not two). An
    earlier version of this function required every claim to carry exactly
    ``{claim, provenance}`` regardless of provenance — which rejected every
    engine_fact/public_fact claim carrying the field its own provenance
    requires before it could ever reach that check, making both provenance
    kinds unusable (#479 Wave B)."""
    if not isinstance(payload, dict):
        raise ReviewError("--agent-case must be a JSON object")
    unknown = set(payload) - {"for", "against"}
    if unknown:
        raise ReviewError("--agent-case has unknown fields: " + ", ".join(sorted(unknown)))
    for side in ("for", "against"):
        if side not in payload:
            raise ReviewError(f"--agent-case must carry both 'for' and 'against' (missing {side!r})")
        claims = payload[side]
        if not isinstance(claims, list):
            raise ReviewError(f"--agent-case.{side} must be a list")
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or not {"claim", "provenance"} <= set(claim):
                raise ReviewError(
                    f"--agent-case.{side}[{index}] must be an object carrying at least "
                    "'claim' and 'provenance'")
            if not isinstance(claim["claim"], str) or not claim["claim"].strip():
                raise ReviewError(f"--agent-case.{side}[{index}].claim must be a non-empty string")
            if claim["provenance"] not in AGENT_CASE_PROVENANCE:
                raise ReviewError(
                    f"--agent-case.{side}[{index}].provenance must be one of "
                    + ", ".join(AGENT_CASE_PROVENANCE))


def _validate_decision_context(payload):
    """Fail-closed structural check for ``--decision-context``, hand-rolled to
    mirror ``schemas/decision-context.schema.json`` — the same "no jsonschema
    dependency" posture ``_validate_agent_case`` just above already keeps for
    its own envelope. The bounds are the module constants, and
    tests/test_consider.py locks them against the schema's own maxLength /
    maxItems so the two cannot drift.

    Everything here refuses rather than repairs (#479's acceptance:
    "oversized/malformed/unsupported context fails clearly without truncation
    or rewriting"). The envelope holds the user's exact words at the moment
    they decided; a value this function shortened, filled in, or accepted
    half-stated would be attributed to them anyway by every surface that reads
    it back. Both ``reason`` and ``why_now`` are required once the envelope is
    sent at all — the same both-sides-or-nothing rule ``--agent-case`` follows,
    and for the same reason: distinguishing new evidence from a price move is
    the question this envelope exists to make askable, and a reason with no
    why_now is the half that lets it pass unasked. A caller who does not have
    that answer asks the user for it, or calls ``consider`` with no envelope at
    all — which stays a complete, unchanged use of the command."""
    if not isinstance(payload, dict):
        raise ReviewError("--decision-context must be a JSON object")
    unknown = set(payload) - {"reason", "why_now", "evidence_refs"}
    if unknown:
        raise ReviewError("--decision-context has unknown fields: " + ", ".join(sorted(unknown)))
    for field in ("reason", "why_now"):
        if field not in payload:
            raise ReviewError(
                f"--decision-context must carry both 'reason' and 'why_now' (missing {field!r}); "
                "ask the user for the missing one, or call consider without --decision-context")
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise ReviewError(f"--decision-context.{field} must be a non-empty string")
        if len(value) > EVALUATION_CONTEXT_TEXT_MAX:
            raise ReviewError(
                f"--decision-context.{field} is {len(value)} characters, over the limit of "
                f"{EVALUATION_CONTEXT_TEXT_MAX}; send the user's own words within the limit "
                "rather than a shortened version of them")
    refs = payload.get("evidence_refs")
    if refs is None:
        return
    if not isinstance(refs, list):
        raise ReviewError("--decision-context.evidence_refs must be a list of strings")
    if len(refs) > EVALUATION_EVIDENCE_REFS_CAP:
        raise ReviewError(
            f"--decision-context.evidence_refs carries {len(refs)} references, over the limit "
            f"of {EVALUATION_EVIDENCE_REFS_CAP}; send the ones that actually moved the "
            "decision — the list is refused rather than shortened, because a truncated one "
            "would read as everything the user cited")
    for index, ref in enumerate(refs):
        if not isinstance(ref, str) or not ref.strip():
            raise ReviewError(
                f"--decision-context.evidence_refs[{index}] must be a non-empty string")
        if len(ref) > EVALUATION_EVIDENCE_REF_MAX:
            raise ReviewError(
                f"--decision-context.evidence_refs[{index}] is {len(ref)} characters, over the "
                f"limit of {EVALUATION_EVIDENCE_REF_MAX}; name the source rather than pasting it")


def _json_safe_premise(normalized):
    """consequence.validate_premise()'s normalized premise carries a real
    ``datetime.date`` for ``date``; every other field is already JSON-safe.
    Converted once, here, so the hash seed, the stored row, and the emitted
    JSON all see the identical string form rather than three independent
    ``str()`` calls that could drift apart."""
    premise = dict(normalized)
    premise["date"] = premise["date"].isoformat()
    return premise


def _evaluation_id(premise, basis, created, consequence_frozen, rule_collisions,
                   context=None):
    """Engine-assigned, content-addressed identity — the same convention
    session.py uses for ``snapshot_id``/``adjustment_id``/``reconciliation_id``.

    Seeded on the frozen premise/basis/created *and* the computed
    consequence/rule_collisions — not on premise/basis/created alone.
    ``--cash``, ``--prices``, ``--driver-map``, ``--instrument-map``, and the
    position cap override can each change the computed answer without
    changing the premise text itself; naming each of those in the seed would
    still leave the next such input open the same way (external review: a
    same-day, same-premise call with a different ``--cash`` anchor produced
    the *same* id for two materially different frozen answers, and
    ``_fold_evaluations``' latest-wins semantics silently treated the
    second as superseding the first — a ``--resolve`` naming that id then
    targets whichever one happened to be folded last). Seeding on the frozen
    result instead closes the whole class at once: any input that changes
    the answer necessarily changes what gets hashed, without this function
    having to enumerate that input by name.

    Two byte-identical inputs still produce a byte-identical seed and
    therefore the same id — ``_append_evaluation_row`` relies on this for
    its no-op-repeat idempotency — and ``created`` stays in the seed so an
    unchanged premise asked again on a different day mints a fresh
    evaluation rather than silently reusing yesterday's answer.

    ``context`` (#479 Wave A) is the optional DecisionContext, and it joins the
    seed for one reason: the same premise, the same book, the same day, but a
    different stated ``why_now`` is a different question about the same trade,
    and without this it hashed identically — so the second ask would have
    folded silently over the first under ``_fold_evaluations``' latest-wins
    semantics. Seeding identity on it does **not** make it an input to any
    arithmetic: ``consequence`` and ``rule_collisions`` are already computed,
    from the premise and the book alone, before this function is called at all.

    An absent context contributes **nothing** — the key is left out of the
    seed rather than sent as ``None``. Sending ``"context": null`` would change
    the hash of every context-free call ever made, so an existing user's next
    plain re-ask would mint a fresh id instead of converging on the row already
    on disk: a duplicated row, and both halves of #479's own "context-free
    ``consider`` remains compatible" / "exact retry does not duplicate"
    acceptance broken at once. The presence test here is ``is not None``, which
    is the identical condition ``cmd_consider`` uses to decide whether the row
    carries a ``context`` key at all — the row and its identity read one fact,
    not two (docs/development-guide.md section 7)."""
    seed_obj = {"premise": premise, "basis": basis, "created": created,
                "consequence": consequence_frozen, "rule_collisions": rule_collisions}
    if context is not None:
        seed_obj["context"] = context
    seed = session.canonical(seed_obj)
    return "eval-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _fold_evaluations(rows):
    """Latest row per ``evaluation_id``. File order decides ties — the same
    supersede-by-append convention ``conditions.fold_slots`` documents: a
    resolution is a new row carrying the same id, never a rewrite of the old
    one, and the last one written is the current fact."""
    latest = {}
    for row in rows:
        if isinstance(row, dict) and row.get("evaluation_id"):
            latest[row["evaluation_id"]] = row
    return latest


# The three values `--resolve` can record. `open` is deliberately absent: it is
# a row's starting state, not a resolution, and #609 makes an unresolved prior
# ineligible rather than exposing an unfinished consultation as settled memory.
PRIOR_DECISION_RESOLVED = ("acted", "declined", "modified")


def _canonical_iso_date(value):
    """A stored date field verbatim when it is already a canonical ISO date,
    else ``None``. Used for both ``decided_on`` and ``created``.

    Round-trip equality rather than a bare ``fromisoformat``, so this reader
    projects the stored bytes instead of a reparsed re-rendering of them. Since
    3.11 that function also accepts ``"20260105"`` and ``"2026-W01-1"``, and
    reparsing either one would hand back a date string the user's file does not
    contain — measured identical on 3.11.9 and 3.12.4, so this is about what
    ``fromisoformat`` accepts, not about which interpreter is running.
    ``_cmd_consider_resolve`` and ``cmd_consider`` are the only writers of these
    two fields and both emit ``date.today().isoformat()``, so no row this engine
    produced is refused here."""
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _prior_decision(root, ticker, side, current_evaluation_id):
    """At most one earlier *resolved* consultation of the same ticker, projected
    read-only beside the current answer (#609).

    Continuity, not learning. The projection restates canonical stored fields
    and nothing else — no summary, comparison, pattern label, inferred motive or
    market outcome — because every one of those is the Agent's transient current
    judgment, and this repository's standing rule is that a judgment nobody
    recomputed is not a fact (schemas/condition-check.schema.json's boundary
    around ``user_response``, one layer out). ``decision`` is projected under the
    canonical field's own name and vocabulary rather than a second one: ``acted``
    means the user *reported* acting on that consultation, and only a later
    transaction import proves a trade happened.

    Eligibility — all six, each of which drops a row rather than repairing it:

    1. the same canonical ticker. The current side came out of
       ``consequence._ticker``, which since #803 canonicalizes case as well as
       whitespace through ``symbols.canonical_ticker``, so a premise written
       ``nvda`` recalls the user's earlier consultation of ``NVDA`` — the same
       identity the arithmetic beside it now uses, never a second case rule
       owned by recall. The stored side is read through that *same* function
       rather than trusted, because rows written before #803 froze whatever
       case the user typed and this repository does not rewrite append-only
       history to make a reader's life easier: the bytes on disk stay as they
       were, and the projection below restates the canonical identity.
    2. a different ``evaluation_id`` than the current one. An exact retry —
       identical premise, book, day and context — converges on the id already on
       disk, so without this the answer would recall itself.
    3. complete stored context: both ``reason`` and ``why_now``, non-empty. The
       envelope is written both-sides-or-nothing (``_validate_decision_context``)
       and a half-stated recall is worse than silence.
    4. a resolved ``decision``.
    5. a canonical non-null ``decided_on``.
    6. readable under the existing folded-reader policy. ``thesis.read_jsonl``
       already drops an unparseable line, and every check here drops rather than
       defaults, so a corrupt candidate is skipped and an older valid row still
       wins — never a guessed field, never corrupt bytes. Nothing here may raise
       either: a broken row must cost the user their *memory*, never the answer
       they are asking for.

    A seventh check the issue's list does not name, because it is about this
    projection rather than about eligibility: ``premise.side`` must be a side
    this engine recognizes. Without it a row storing anything else falls into
    the opposite-side bucket by default and that value is handed to the agent
    verbatim as the user's earlier direction.

    Selection: newest eligible same-side prior; only when none exists, the
    newest eligible opposite-side one. Within a side, one total order —
    ``(decided_on, created, evaluation_id)`` descending — never raw file order,
    which ``_fold_evaluations`` preserves and which says nothing about when a
    decision was made. ``created`` sits between the other two because
    ``decided_on`` alone ties whenever a user settles two consultations of one
    ticker in the same sitting, and ``evaluation_id`` is a content address
    carrying no time at all: on that tie, identity alone would order two
    consultations by hash and could hand back the older one as "the most
    recent". Both keys are canonical stored fields, and identity stays last so
    the order is still total. ``_evaluation_recall`` already sorts on
    ``(created, evaluation_id)`` for the same reason.

    Returns the projection dict, or ``None`` when nothing is eligible; the
    caller omits the field entirely rather than emitting a null.

    Read-only in the strong sense: the caller computes this *after* the frozen
    consequence, the rule collisions and the ``evaluation_id`` already exist, so
    there is no path by which a recalled row can reach this call's arithmetic,
    its rule effects, its identity, or the row it stores."""
    candidates = {"same": [], "opposite": []}
    for row in _fold_evaluations(thesis.read_jsonl(_evaluation_path(root))).values():
        # Every field below is type-checked before it is used, because
        # ``thesis.read_jsonl`` proves each surviving line is a JSON object and
        # nothing whatever about what is inside it. A hand-edited, truncated or
        # half-written row has to skip this candidate; it must not raise out of
        # the decision the user is asking about right now. All three of these
        # were live crashes before the checks existed: a non-dict ``premise`` or
        # ``context`` reached ``.get`` on a string, and a non-string
        # ``evaluation_id`` reached the ordering comparison below, where a str
        # and an int are not orderable at all.
        evaluation_id = row.get("evaluation_id")
        if not isinstance(evaluation_id, str) or evaluation_id == current_evaluation_id:
            continue
        if row.get("decision") not in PRIOR_DECISION_RESOLVED:
            continue
        decided_on = _canonical_iso_date(row.get("decided_on"))
        if decided_on is None:
            continue
        # Only a sort key, so a row whose `created` is missing or malformed is
        # still eligible -- it simply loses a same-day tie rather than being
        # dropped for a field the projection does not carry.
        created = _canonical_iso_date(row.get("created")) or ""
        premise = row.get("premise")
        if not isinstance(premise, dict) or symbols.canonical_ticker(premise.get("ticker")) != ticker:
            continue
        prior_side = premise.get("side")
        if prior_side not in consequence.SIDES:
            continue
        # Absent, null and non-object all land here, and all three mean the same
        # thing: this row carries no stated context, so there is nothing of the
        # user's to recall.
        context = row.get("context")
        if not isinstance(context, dict):
            continue
        reason, why_now = context.get("reason"), context.get("why_now")
        if not all(isinstance(text, str) and text.strip() for text in (reason, why_now)):
            continue
        # Absent is the common, valid case -- `evidence_refs` is optional on the
        # stored envelope -- and projects as the empty list the shape declares.
        # A stored `null` reads the same way, deliberately: it is the exact
        # value `_validate_decision_context` accepts and persists for a caller
        # who sends the key with nothing in it, so reading it as anything but
        # "no references" would make this reader disagree with the writer about
        # a row the engine itself wrote. (That the schema declares a bare
        # `array` and so does not admit the null it stores is a real
        # writer/schema disagreement, but it is #479's to settle at the writer,
        # not something to punish the user's `reason` and `why_now` for here.)
        #
        # Present and neither of those is a corrupt row, so it is dropped whole
        # rather than pruned down to the entries that happen to parse: a
        # filtered list would reach the user as everything they cited.
        refs = context.get("evidence_refs")
        if refs is None:
            refs = []
        elif not (isinstance(refs, list)
                  and all(isinstance(ref, str) and ref.strip() for ref in refs)):
            continue
        # The sort key travels beside the projection rather than inside it, so
        # ordering can read a field the agent is not shown: `created` decides
        # same-day ties and is not one of the eight fields #609 specifies.
        # `evidence_refs` is copied because it is the one mutable value here;
        # every other one is an immutable string, so the returned projection
        # shares no object with the folded row.
        candidates["same" if prior_side == side else "opposite"].append((
            (decided_on, created, evaluation_id),
            {"evaluation_id": evaluation_id,
             # The canonical identity, not the stored spelling: this field is
             # the instrument the recalled decision was about, and a pre-#803
             # row that froze `nvda` is still a decision about `NVDA`.
             "ticker": ticker,
             "side": prior_side,
             "reason": reason,
             "why_now": why_now,
             "evidence_refs": list(refs),
             "decision": row["decision"],
             "decided_on": decided_on}))
    for bucket in ("same", "opposite"):
        if candidates[bucket]:
            return max(candidates[bucket], key=lambda item: item[0])[1]
    return None


def _evaluation_recall(root):
    """What the user already told us, in their own words, about a ticker.

    ``consider`` stores the user's exact ``reason`` and ``why_now`` on
    ``trade_evaluations.jsonl`` (schemas/decision-context.schema.json: stored
    verbatim, never rewritten or translated). Nothing outside ``consider`` ever
    read that text, so a first review asked its entry-motive question from
    scratch — offering five buckets to a user who had already answered in their
    own language, and storing a choice ``evals/run_episodes.py`` still lists in
    ``KNOWN_UNWIRED``. This returns the text so ``_question_queue`` can show it
    back instead of asking someone to reconstruct it from memory (#636).

    Returns ``{ticker: [statement, ...]}``, each list oldest ``created`` first.
    Cycle matching belongs to the caller, which is the layer that knows which
    cycle a ticker is currently in.

    Two scope choices worth stating, because both differ from
    ``_evaluation_reconciliation`` directly below:

    * ``decision`` is not read at all. A resolved evaluation is still something
      the user said, so that function's open-only filter is the wrong one here.
    * A statement with neither ``reason`` nor ``why_now`` is dropped rather than
      returned empty. ``consider`` may run with no ``--decision-context``, and a
      recalled blank is worse than the canned question it would replace.

    Like that function this states a fact — "you said this, on this date" — and
    never a cause. Nothing here claims a position was opened *because* of a
    statement; ``premise`` is what the user contemplated, not what they did.
    """
    recall = {}
    for row in _fold_evaluations(thesis.read_jsonl(_evaluation_path(root))).values():
        premise = row.get("premise") or {}
        ticker = premise.get("ticker")
        context = row.get("context") or {}
        reason = context.get("reason")
        why_now = context.get("why_now")
        if not ticker or not (reason or why_now):
            continue
        try:
            created = dt.date.fromisoformat(str(row.get("created")))
        except (TypeError, ValueError):
            continue
        # Keyed canonically (#803): the lookup side is a holding's own
        # spelling and this side is whatever the stored premise froze, so
        # indexing on the raw string loses a legacy row's statement against
        # a canonical position and vice versa. `_recalled_entry_statement`
        # reads the same rule.
        recall.setdefault(symbols.canonical_ticker(ticker), []).append({
            "evaluation_id": row.get("evaluation_id"),
            "created": created,
            "reason": reason,
            "why_now": why_now,
        })
    for statements in recall.values():
        statements.sort(key=lambda item: (item["created"], str(item.get("evaluation_id") or "")))
    return recall


def _cycle_entry(cycle_id):
    """``(entry_date, sequence)`` for a canonical cycle id, else ``(None, None)``.

    ``trade_recap.CYCLE_ID_RE`` is the single source of truth for the shape
    (``"{ticker}#{start}#{seq}"``), with ``CYCLE_ID_UNKNOWN_RE`` covering the
    two-segment ``"{ticker}#unknown"`` a CSV without opening holdings produces.
    Matching against that regex rather than splitting on ``#`` matters: a split
    accepts ``"AAA#2026-01-01#garbage"`` and fails open into a valid-looking
    entry date, and every caller here is deciding whether to attribute the
    user's own words to a position.
    """
    if not trade_recap.CYCLE_ID_RE.match(str(cycle_id or "")):
        return None, None
    _ticker, start, seq = str(cycle_id).split("#")
    try:
        return dt.date.fromisoformat(start), int(seq)
    except ValueError:
        return None, None


def _recalled_entry_statement(recall, ticker, cycle_id):
    """The user's latest own-words statement recorded before this cycle opened,
    or None.

    Two bounds, and the repository has a rule behind each.

    *Upper*: no later than the cycle's entry. That is what makes this an
    *entry* thesis rather than a later add — an evaluation recorded after the
    position opened describes a decision this question is not asking about.
    Same-day counts, because the contemplation and the fill routinely land on
    one date.

    *Lower*: only the position's **first** cycle. A ticker fully exited and
    re-entered is a new position with its own reason (the owner's per-cycle
    ruling on #636), and a cycle id carries no lower bound — every statement
    made before the *first* entry also satisfies ``created <= start`` for the
    second. Rather than quote the previous position's reason as this one's,
    a re-entry recalls nothing. Bounding it properly needs the prior cycle's
    exit date, which this layer does not have; failing closed is the honest
    version until it does.

    Among the eligible, the most recent is used. Note what that is *not*: two
    evaluations for one ticker are two distinct decisions, not a revision
    chain — ``_evaluation_id`` seeds on ``context``, so re-asking the same
    premise with a different ``why_now`` mints a new evaluation rather than
    superseding the old one. Taking the latest is a choice of the closest
    statement to the entry, not a supersede semantic.
    """
    start, seq = _cycle_entry(cycle_id)
    if start is None or seq != 1:
        return None
    eligible = [item for item in (recall or {}).get(symbols.canonical_ticker(ticker), [])
                if item["created"] <= start]
    if not eligible:
        return None
    return eligible[-1]


def _evaluation_reconciliation(root, rows, date_end):
    """Reconcile every unsettled ``consider`` evaluation against the
    transaction record, for ``_build_plan`` (#317; #429's rule one layer up —
    a stored answer nothing reads is a defect, and until this, nothing
    outside ``consider`` itself ever read ``trade_evaluations.jsonl``).

    For each folded evaluation still at ``decision: "open"`` (a resolved
    one never reaches here — ``_fold_evaluations`` already picked the
    latest row per id), search ``rows`` — trade_recap-shaped dicts carrying
    ``ticker``/``side``/``qty``/``price``/``date`` as a real ``datetime.date``,
    the same shape ``_ledger_trade_events``, ``_rows_from_ledger``, and
    ``trade_recap.load`` all produce — for a trade of the identical ticker and
    side, dated on or after the evaluation's own ``created`` day and on or
    before ``date_end``. The earliest such trade, when more than one
    qualifies, is what gets reported. The caller decides which loader feeds
    ``rows``; this function only ever searches what it is given, and a
    synthesized position row has no business here — see
    ``_ledger_trade_events`` for why ``_build_plan`` uses that one.

    This states a fact, never a cause. A ``matched`` result means a
    qualifying trade exists in the record inside that window — it is not,
    and must never be read as, evidence the user traded *because of* this
    evaluation. The same boundary schemas/condition-check.schema.json draws
    around ``user_response`` (an engine-computed verdict is never substituted
    for the user's own word) applies here: this function only ever reads
    ``decision``, never writes it — that field moves through
    ``consider --resolve`` alone.

    Returns ``{"items": [...], "summary": {...}}``. ``items`` is capped at
    ``EVALUATION_RECONCILE_CAP``, oldest ``created`` first — the bounded-
    plan-surface shape ``_condition_due`` uses, and for the same reason
    (the comment on ``CONDITION_LOOKUP_CAP``): the plan is re-sent as agent
    context on every later turn, so a user who never resolves old
    evaluations must not grow it without limit. ``summary`` states the
    total still open, how many are shown, and how many were held back, so a
    capped list can never be mistaken for the complete record.
    """
    evaluations = _fold_evaluations(thesis.read_jsonl(_evaluation_path(root)))
    open_rows = [row for row in evaluations.values() if row.get("decision") == "open"]
    open_rows.sort(key=lambda row: (str(row.get("created") or ""), str(row.get("evaluation_id") or "")))

    try:
        end = dt.date.fromisoformat(str(date_end)) if date_end else None
    except ValueError:
        end = None

    items = []
    for row in open_rows:
        premise = row.get("premise") or {}
        ticker = premise.get("ticker")
        side = premise.get("side")
        created = row.get("created")
        try:
            created_date = dt.date.fromisoformat(str(created))
        except ValueError:
            created_date = None
        match = None
        if created_date is not None and end is not None and ticker and side:
            # #803, storage-reader half: `ticker` came off a stored evaluation
            # row, which froze whatever case the user typed before the rule
            # existed, while `rows` are the canonical book. Comparing them raw
            # reports a trade that plainly happened as `unmatched`.
            candidates = sorted(
                (r for r in rows
                 if symbols.canonical_ticker(r.get("ticker")) == symbols.canonical_ticker(ticker)
                 and r.get("side") == side
                 and isinstance(r.get("date"), dt.date) and created_date <= r["date"] <= end),
                key=lambda r: r["date"])
            if candidates:
                nearest = candidates[0]
                match = {"date": nearest["date"].isoformat(), "qty": nearest["qty"]}
        items.append({
            "evaluation_id": row.get("evaluation_id"),
            "created": created,
            "premise": premise,
            "status": "matched" if match else "unmatched",
            "matched_trade": match,
        })

    total = len(items)
    shown = items[:EVALUATION_RECONCILE_CAP]
    return {"items": shown,
            "summary": {"open_total": total, "shown": len(shown),
                        "beyond_cap": max(0, total - len(shown))}}


def _append_evaluation_row(root, row):
    """Append-only writer for ``trade_evaluations.jsonl``.

    Neither of this repo's existing append helpers fits. ``session.
    _append_session_rows``' idempotency key is a session_id, and an
    evaluation has none — ``consider`` is explicitly session-less (see
    ``cmd_consider``'s docstring). ``ledger.append_events`` stamps every row
    with ``ledger.SCHEMA_V``, which names *ledger.jsonl's* own shape;
    borrowing it would tie an unrelated file's rows to a version number that
    can change for reasons that have nothing to do with this schema
    (docs/development-guide.md section 7's "two readers, one fact", one layer
    down — a stamp instead of a derivation, but the same silent-drift shape).

    So this hand-rolls the one property both of those helpers guarantee and
    this file still needs: build the complete line up front and issue exactly
    one ``write()`` call, so a concurrent reader never observes a torn line —
    the same trailing-newline guard ``_append_session_rows`` uses against a
    prior crash leaving the file without one. Idempotency is content-based
    rather than session-keyed: if the current latest row for this
    ``evaluation_id`` is already byte-identical to ``row``, appending is
    skipped — a retried ``consider`` or ``--resolve`` call is a no-op, not a
    duplicate line.
    """
    path = _evaluation_path(root)
    existing = thesis.read_jsonl(path)
    current = _fold_evaluations(existing).get(row.get("evaluation_id"))
    if current is not None and session.canonical(current) == session.canonical(row):
        return {"path": path, "appended": 0, "status": "no-op"}
    os.makedirs(root, exist_ok=True)
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"path": path, "appended": 1, "status": "appended"}


def _cmd_consider_resolve(root, evaluation_id, decision, language):
    """``--resolve``: append a new row recording what the user did, never
    rewrite the old one (this repo decides supersession by chain, never by
    mutating history — ``conditions.fold_slots``, conditions.py:401)."""
    path = _evaluation_path(root)
    current = _fold_evaluations(thesis.read_jsonl(path)).get(evaluation_id)
    if current is None:
        raise ReviewError(
            f"no evaluation matching {evaluation_id!r} in {path} — --resolve only applies "
            "to an evaluation_id `consider` itself returned")
    updated = dict(current)
    updated["decision"] = decision
    updated["decided_on"] = dt.date.today().isoformat()
    report = _append_evaluation_row(root, updated)
    payload = {"status": "resolved", "root": root, "language": language,
               "evaluation_id": evaluation_id, "decision": decision,
               "evaluation": updated, "append": report}
    # #739: --resolve re-emits the frozen row's own consequence.disclosures,
    # the same list the original --premise call could carry, so a caller
    # reading this response in a non-English --language is back at the same
    # raw-key gap without this. Same helper, same field name, so a reader
    # that resolved an evaluation sees the identical shape it saw when it
    # first considered the trade.
    disclosures_display = _consider_disclosures_display(
        updated.get("consequence") or {}, language)
    if disclosures_display:
        payload["disclosures_display"] = disclosures_display
    _emit(payload)


def cmd_consider(args):
    """Layer 2 entry point (docs/decision-fomo-kernel-shape.md §3-4): the
    deterministic consequence of one hypothetical trade against the book the
    product already stores, asked away from any review — "I'm thinking of
    buying NVDA, what does that do to my book?". Two independent modes on one
    subcommand, matching the CLI whitelist's single ``consider`` entry
    (AGENTS.md, SKILL.md, references/agent-boundaries.md):

      --premise <path-or-inline-JSON>    compute and record a new evaluation
      --resolve <evaluation_id> --decision {acted,declined,modified}
                                          record what the user did with one

    Read-only with respect to review state: unlike every other mutating
    command in this file, ``consider`` never writes rules.jsonl, never calls
    problems.check_rules, and never creates or touches a session. The one
    thing it persists is its own append-only
    ``<root>/trade_evaluations.jsonl``
    (schemas/trade-evaluation.schema.json), registered in coach.py's
    DATA_FILES so data-export and data-reset see it like every other stored
    file (#452 is exactly the bug that shipped when a file skipped that step).

    Every stored field is a frozen value, never a pointer into mutable state
    (the frozen-subject design ratified in issue #446's specification
    comment) — a later ``--resolve``, or next review, must see what the
    engine actually said *at the time*, not what it would say recomputed
    against a ledger that has since grown. ``basis`` freezes which book
    answered (issue #456): the CSV/FIFO path a review uses, or a
    reconstruction from the local ledger when no CSV is handed over — the two
    can disagree about a position's weight, and how many days stale the
    record was when asked.

    ``--decision-context`` (#479 Wave A, schemas/decision-context.schema.json)
    freezes what the user said beside what the engine computed: their reason,
    their why-now, and up to ``EVALUATION_EVIDENCE_REFS_CAP`` things they
    pointed at. Entirely optional — a plain ``--premise`` call behaves exactly
    as it did before the flag existed, down to the ``evaluation_id`` it
    returns. Supplied, it joins the identity seed, so the same trade re-asked
    with a different why-now is a distinct evaluation rather than a silent
    supersede; it never joins a computation.

    ``--agent-case`` (schemas/answer-provenance.schema.json) is optionally a
    structured case for and against, checked against this call's own frozen
    ``basis``/``consequence``/``rule_collisions`` by
    ``answer_provenance.validate_agent_case`` before the row is written or
    anything is returned (#414, wired in here by #479 Wave B). A claim that
    misquotes the frozen record, cites an anchor that does not resolve,
    drops a disclosure this evaluation owes, or relabels the user's own
    ``--decision-context`` words as an outside ``public_fact`` is refused —
    the caller sees the validator's own message and nothing is persisted.
    Like ``--agent-case`` itself, none of this touches the ``evaluation_id``
    seed or the computed consequence.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    language = card_renderer.resolve_language(args.language)

    if args.resolve:
        conflicting = [name for name, value in (
            ("--premise", args.premise), ("CSV paths", args.paths),
            ("--prices", args.prices),
            ("--prices-unavailable", getattr(args, "prices_unavailable", None)),
            ("--driver-map", args.driver_map),
            ("--instrument-map", args.instrument_map), ("--cash", args.cash),
            ("--agent-case", args.agent_case),
            ("--decision-context", args.decision_context),
        ) if value]
        if conflicting:
            raise ReviewError("--resolve takes no premise; remove " + ", ".join(conflicting))
        if not args.decision:
            raise ReviewError("--resolve requires --decision {acted,declined,modified}")
        _cmd_consider_resolve(root, args.resolve, args.decision, language)
        return
    if args.decision:
        raise ReviewError("--decision only applies together with --resolve")
    if not args.premise:
        raise ReviewError("consider requires --premise, or --resolve together with --decision")

    premise_payload = _load_json_arg(args.premise, "--premise")
    # #479 Wave A. Validated here, before any book is read or any number is
    # computed: a refusal costs the caller one round trip, where an envelope
    # accepted half-stated is attributed to the user by every surface that
    # reads the row back. Nothing below this line lets it near an arithmetic
    # path — the frozen consequence is computed from the premise and the book,
    # and the context only ever reaches the identity seed and the stored row.
    # `is not None`, not truthiness: a caller who sent the flag with an empty
    # value had something to say and lost it somewhere, and silently recording
    # the evaluation as though the user stated nothing is the worse of the two
    # outcomes. _load_json_arg refuses it by name.
    decision_context = None
    if args.decision_context is not None:
        decision_context = _load_json_arg(args.decision_context, "--decision-context")
        _validate_decision_context(decision_context)

    if args.driver_map:
        trade_recap.load_driver_map(os.path.abspath(os.path.expanduser(args.driver_map)))
    if args.instrument_map:
        instruments.load_map(os.path.abspath(os.path.expanduser(args.instrument_map)))

    # #629: the same declaration `prepare` already accepts, read through the same
    # single validator. Here it does the opposite of what it does there — see the
    # refusal below.
    declared_unavailable = _declared_prices_unavailable(args)

    last_px, fx, supplied_splits, market_bundle = None, None, None, None
    agent_supplied = bool(args.prices)
    feed = None
    if agent_supplied:
        try:
            feed = price_feed.load(os.path.abspath(os.path.expanduser(args.prices)))
        except price_feed.PriceFeedError as exc:
            raise ReviewError(f"price feed rejected: {exc}") from exc
    else:
        # #605 section E: resolve the current facts this answer needs, rather than
        # reasoning about the book at whatever the last review happened to freeze.
        # The resolved envelope is fed into the identical path below — the supplied
        # lane is the tested one, and a second lane is a second thing to get wrong.
        # A supplied envelope is never topped up from here: past this branch the
        # two are indistinguishable, which is the point.
        feed, market_bundle = _resolve_consider_prices(
            args, root,
            premise_ticker=symbols.canonical_ticker(premise_payload.get("ticker")),
            premise_currency=str(premise_payload.get("currency") or "").strip() or None)
    if feed is not None:
        last_px = {ticker: row["close"] for ticker, row in feed["prices"].items()}
        fx = price_feed.fx_rates(feed)
        # The envelope's own splits, already schema-validated on the way in
        # (references/price-feed.md). Preferred over the frozen map below
        # because it arrived with these quotes, on one basis at one instant —
        # and because a CSV-route caller may have no review in this root to
        # have frozen anything.
        supplied_splits = price_feed.splits_map(feed) or None
        # #583. `last_px` above is now on the basis of the envelope's own
        # declared splits, and the shares it will be multiplied by are carried
        # across whichever map wins below. Equal on both sides, the operands are
        # comparable; unequal, this call cannot establish one basis and refuses
        # before a book is read or a number computed — a supplied pre-split
        # close against a post-split share count is a tenfold weight,
        # concentration verdict and consequence, all of them stated with a valid
        # state_version and no disclosure that anything is off.
        conflicts = price_feed.basis_conflicts(
            feed, _effective_splits(root, supplied_splits))
        if conflicts:
            detail = "; ".join(
                f"{row['ticker']} priced from {row['observed_date']}"
                + (f", but this book is carried across a split dated {row['split_date']}"
                   if row.get("split_date") else "")
                + (f" ({row['error']})" if row.get("error") else "")
                for row in conflicts)
            # A resolved envelope carries current observations, so both factors
            # are normally 1.0 and this cannot fire; it still can for a quote that
            # is genuinely stale (a halted or delisted instrument whose last close
            # predates a split), and telling that user to fix an envelope they
            # never wrote would send them after a file that does not exist.
            repair = ("Declare the split in the price envelope, or supply a close dated on or "
                      "after it." if args.prices else
                      "Supply --prices with a close dated on or after that split, or refresh the "
                      "book so its share counts and the available quote share one basis.")
            raise ReviewError(
                f"the {'supplied' if args.prices else 'retrieved'} prices and this book's share "
                "counts are on different split bases, so no weight or consequence can be "
                f"computed from them: {detail}. {repair}")

    cash_anchor = None
    if args.cash:
        try:
            cash_anchor = json.loads(args.cash)
        except ValueError as exc:
            raise ReviewError(f"--cash is not valid JSON: {exc}") from exc
    else:
        # #756: without an explicit --cash, fall back to the balance the
        # last finalized review in this root already anchored, rather than
        # silently dropping it and falling through to an unanchored
        # csv_sum guess on the very next call after the user declared it.
        cash_anchor = _fallback_cash_anchor(root)

    rows, basis, canonical_basis, canonical_projection, excluded_holdings = _consider_rows(
        args, root, feed=feed, last_px=last_px, splits=supplied_splits,
        agent_supplied=agent_supplied)

    # #629: recovery names only the current positions this consequence can
    # value plus its premise.  The ledger lane already has one row per usable
    # canonical current holding here, before the frame-sensitive canonical
    # `before` state can be built.  The CSV lane derives its current set below.
    # Exclusions travel through disclosures and provenance, never through the
    # price manifest: neither an integrity orphan nor an unusable quantity
    # becomes repairable because a close was fetched for it.
    # Canonical, like every other identity this answer compares (#803): the
    # price universe and the recovery list are matched against the book's own
    # keys, so an agent that spelled the premise in lower case must not request
    # a quote for a second instrument beside the one it is asking about.
    premise_ticker = symbols.canonical_ticker(premise_payload.get("ticker")) or ""
    priced_universe = None
    price_status = None
    if canonical_basis is not None:
        priced_universe = _consider_recovery_tickers(rows, premise_ticker, excluded_holdings)
        fx_required, missing_fx = _consider_canonical_fx_recovery(canonical_basis)
        price_status = _consider_price_feed_status(
            requested=sorted(priced_universe), last_px=last_px, missing_fx=missing_fx,
            fx_required=fx_required, feed=feed, agent_supplied=agent_supplied,
            unavailable_declared=declared_unavailable,
            bundle=market_bundle)

    # The typed valuation frame has already proved the book has all native
    # closes, but it cannot aggregate a mixed-currency book without its named
    # FX rate.  Surface the existing recovery kit instead of letting canonical
    # sizing fall through to a cost-basis answer or a prose-only error.
    request = (price_status or {}).get("request") or {}
    if (canonical_basis is not None and canonical_projection is not None
            and not canonical_projection.applicable
            and request.get("currencies") and not request.get("tickers")):
        raise ReviewError(
            "this current book cannot be valued in USD because no FX rate covers "
            f"{', '.join(request['currencies'])}. Supply the missing rate through --prices, then "
            "ask again; the trade was not evaluated on cost basis.",
            payload_extra={"price_feed": price_status})

    agent_case = None
    if args.agent_case:
        agent_case = _load_json(os.path.abspath(os.path.expanduser(args.agent_case)), "--agent-case")
        _validate_agent_case(agent_case)
    max_pos_override = _position_cap_override(root)

    before_override = None
    if canonical_basis is not None:
        before_override = _canonical_consider_before(
            rows, canonical_basis, canonical_projection, last_px, max_pos_override,
            cash_anchor, fx, excluded_holdings=excluded_holdings)

    try:
        result = consequence.consequence(rows, premise_payload, last_px=last_px,
                                         max_pos_override=max_pos_override,
                                         cash_anchor=cash_anchor, fx=fx,
                                         before_override=before_override,
                                         excluded_holdings=excluded_holdings)
    except consequence.ConsequenceError as exc:
        raise ReviewError(str(exc)) from exc

    if price_status is None:
        # The CSV lane has no PortfolioBasis.  `consequence` is its one reader
        # of current holdings, so scope the same recovery helper to that result
        # rather than every historical row in the supplied files.
        current_tickers = set(result["before"].get("held") or {})
        current_rows = [row for row in rows
                        if symbols.canonical_ticker(row.get("ticker")) in current_tickers]
        priced_universe = _consider_recovery_tickers(
            current_rows, premise_ticker, excluded_holdings)
        price_status = _consider_price_feed_status(
            requested=sorted(priced_universe), last_px=last_px, feed=feed,
            agent_supplied=agent_supplied,
            unavailable_declared=declared_unavailable,
            bundle=market_bundle)

    # #618. Frozen onto the basis beside `valuation_basis`, where "was this
    # priced" already lives, and from the same `priced_universe` the kit above
    # is scoped to. Stamped here rather than inside `_consider_rows` because the
    # observation is a property of the feed, not of which lane read the book —
    # the CSV and ledger lanes would otherwise each need their own copy of it.
    #
    # Conditional, and that is the contract: `valuation_basis: "unpriced"` runs
    # carry no key at all, so there is no placeholder for a reader to mistake
    # for an observation. `_price_observation_record` returns None for them.
    price_observations = _price_observation_record(feed, priced_universe)
    if price_observations is not None:
        basis["price_observations"] = price_observations
    # #629, the refusal. Scoped to the one state that produces the harm: nothing
    # current reached this book at all, so every weight above would be a share
    # of *cost*. A retrospective card's cost weights describe what the user
    # actually paid and are delivered degraded (references/price-feed.md); a
    # forward concentration decision computed on cost describes a book that no
    # longer exists, and on this repository's own momentum fixture it inverts
    # the second and third position by size while moving the largest one by more
    # than thirteen points. Two lanes, two rules, on purpose.
    #
    # Gated on `recovery.attempted` rather than on the flag directly, so this
    # reads #623's own single statement of "was recovery ever tried" instead of
    # deciding it a second time. Untried recovery is not this refusal: that run
    # still gets the kit above, which is the thing that tells the agent to go and
    # look. This fires only once looking has happened and produced nothing.
    #
    # Placed after the arithmetic and before anything is stored or emitted: the
    # condition is a property of the computed basis, and a refused question must
    # leave no row behind.
    if (basis.get("valuation_basis") == "unpriced"
            and ((price_status or {}).get("recovery") or {}).get("attempted")):
        raise ReviewError(
            "price recovery was attempted and no current price reached this book, so every "
            "weight in this answer would be a share of cost rather than of market value. A "
            "trade the user has not placed yet is a forward-looking decision, and cost weights "
            "describe a book that no longer exists — the largest position and the ranking "
            "beneath it can both come out differently — so this question is refused rather "
            "than answered on cost basis. Supply whatever closes you did find with "
            "--prices <path>: partial coverage is accepted and names what it could not value. "
            "A review card is the other lane and still delivers degraded "
            "(references/price-feed.md)")

    muted_ids = _muted_rule_ids(root)
    rules_report = problems.load_rules_report(os.path.join(root, "rules.jsonl"), muted_ids)
    collisions = consequence.rule_collision(rows, premise_payload, rules_report,
                                            last_px=last_px, max_pos_override=max_pos_override,
                                            cash_anchor=cash_anchor, fx=fx,
                                            before_override=before_override,
                                            excluded_holdings=excluded_holdings)

    premise_stored = _json_safe_premise(result["premise"])
    created = dt.date.today().isoformat()
    # Built once and reused for both the id seed and the stored field below —
    # a second, separately-assembled copy is exactly the "two readers, one
    # fact" shape this fix exists to close (docs/development-guide.md
    # section 7): the id must hash what the row actually carries, not a
    # parallel reconstruction of it that could drift.
    consequence_stored = {"before": result["before"], "after": result["after"],
                          "delta": result["delta"], "disclosures": result["disclosures"],
                          # #515 invariant 1: the excluded holding travels with
                          # every number derived from the partial denominator,
                          # including into the frozen record a later --resolve
                          # or reconciliation reads back.
                          "excluded_holdings": result["excluded_holdings"],
                          # #598/#599, on the identical reasoning: the key in
                          # `disclosures` says the concentration figures were
                          # computed over a partially-legible book, and these
                          # say which positions and how large, so the frozen
                          # row carries the size of what it could not see.
                          "unclassified_holdings": result["unclassified_holdings"],
                          "undecomposed_etfs": result["undecomposed_etfs"]}

    challenge = evaluation_challenge.build_challenge(
        premise=premise_stored, basis=basis, consequence=consequence_stored,
        rule_collisions=collisions, context=decision_context)

    # #414 / #479 Wave B: the semantic provenance gate. Runs here — after
    # consequence_stored and collisions exist, before the row is built,
    # appended, or emitted — because answer_provenance.validate_agent_case
    # checks each claim against the frozen result it claims to describe,
    # and that result does not exist any earlier in this function. A
    # rejected case is neither stored nor delivered: fail closed, surfacing
    # the validator's own message, which names the exact claim and rule (or
    # disclosure) that failed. agent_case itself never joins _evaluation_id's
    # seed below — it is the agent's interpretation, not the subject being
    # evaluated — so this call decides only whether the row gets written at
    # all, never what its identity is.
    if agent_case is not None:
        # user_statements is the exact reason/why_now the user supplied
        # through --decision-context (#479), never a summary, normalization,
        # or translation of them: case 8 catches an agent that copies the
        # user's own words and relabels them with a public_fact citation
        # they never earned, and a paraphrase would defeat that comparison.
        # () when no context was supplied — a context-free consider call
        # captured no user prose, so case 8 has nothing to compare against,
        # exactly as answer_provenance.py's own module docstring specifies.
        user_statements = ((decision_context["reason"], decision_context["why_now"])
                           if decision_context is not None else ())
        try:
            answer_provenance.validate_agent_case(
                agent_case, basis=basis, consequence=consequence_stored,
                rule_collisions=collisions, user_statements=user_statements)
        except answer_provenance.AnswerProvenanceError as exc:
            raise ReviewError(str(exc)) from exc

    row = {
        "evaluation_id": _evaluation_id(premise_stored, basis, created,
                                        consequence_stored, collisions,
                                        context=decision_context),
        "created": created,
        "premise": premise_stored,
        "basis": basis,
        "consequence": consequence_stored,
        "rule_collisions": collisions,
        "decision": "open",
        "decided_on": None,
    }
    # `is not None` on both sides: the row carries the key exactly when the
    # seed above did, so an evaluation's identity and its stored content can
    # never disagree about whether a context was supplied. A context is never
    # stored as null — the absence is the fact.
    if decision_context is not None:
        row["context"] = decision_context
    if agent_case is not None:
        row["agent_case"] = agent_case

    # #479 Wave B cut 2, the visible half. Everything above decides whether
    # this evaluation may exist and whether a supplied case may be believed;
    # nothing above says what the *user* has to be told. `build_challenge`
    # reads the values just frozen and returns that obligation as data —
    # which facts, whose exact words, which of their own rules, which
    # limitations, and what the engine never looked at — so a brief answer
    # (SKILL.md's answer shape) is bounded from below by a computed list rather than
    # by what an agent remembers of references/trade-consequence.md.
    #
    # Emitted beside the row, never onto it: it is a pure function of
    # premise/basis/consequence/rule_collisions/context, all of which the row
    # already freezes, so storing it would be a derived duplicate able to
    # disagree with its own inputs, and no reader needs the historical
    # version (evaluation_challenge.py, "Emitted, not stored"). It is
    # likewise absent from _evaluation_id's seed above — the seed identifies
    # the subject evaluated, and a presentation obligation is not part of it.
    # #609. Read here — after the identity, the frozen consequence and the rule
    # collisions all exist, and before the append — so the ordering itself
    # states the boundary: nothing recalled can have reached any number above,
    # and the file this reads is the record as it stood *before* this
    # consultation joined it. `row["evaluation_id"]` rather than a recomputed
    # one, so an exact retry excludes the very row it converged onto.
    prior_decision = _prior_decision(root, premise_stored["ticker"],
                                     premise_stored["side"], row["evaluation_id"])
    report = _append_evaluation_row(root, row)
    payload = {"status": "considered", "root": root, "language": language,
               "evaluation": row, "challenge": challenge, "append": report}
    if prior_decision is not None:
        # Beside the row, never on it, for the same reason `challenge` is
        # (evaluation_challenge.py, "Emitted, not stored"): this is a read
        # projection of *another* row, and storing it here would mint a second
        # copy of a fact that already has a canonical home and can drift from
        # it. Omitted outright when nothing is eligible — never `null`, never a
        # placeholder — so the absence is the fact and no reader has to tell an
        # empty recall from an unasked one.
        payload["prior_decision"] = prior_decision
    sector_display = _consider_sector_display(consequence_stored, language)
    if sector_display:
        # #746. `max_sector` is a canonical engine label — `trade_recap.SECTOR_MAP`
        # stores zh literals — and it stays that way on the row: `_evaluation_id`
        # is seeded on the row, so the same trade evaluated in two languages has
        # to be one evaluation, not two. The name the user reads therefore goes
        # beside the row, for the same reason `challenge` and `price_feed` do.
        #
        # It is not optional politeness. `max_sector_pct` is on `must_state`, and
        # the natural way to state a sector weight is to name the sector, so
        # without this the only sector name in the response is the one an English
        # answer must not contain.
        payload["sector_display"] = sector_display
    disclosures_display = _consider_disclosures_display(consequence_stored, language)
    if disclosures_display:
        # #739. Same posture as `sector_display` right above: `disclosures`
        # itself stays the engine's stable English key list -- an anchor in
        # `required_coverage` addresses it by array position -- and this adds,
        # beside it, one localized sentence per key so a non-English answer has
        # something to draw from besides quoting the raw machine key back at
        # the user.
        payload["disclosures_display"] = disclosures_display
    if price_status:
        # #629. Beside the row, never on it — the same place and for the same
        # reason as `challenge`: the row is content-addressed and this is a
        # statement about the retrieval that produced it, not about the trade
        # being evaluated. Always present when there is any provenance to state,
        # so "this answer is fully priced" is a claim rather than a silence.
        payload["price_feed"] = price_status
        if price_status.get("next_action"):
            # The route-specific tail, composed at the call site exactly as
            # `cmd_prepare` composes its own — the shared builder states the gap
            # and the envelope; only this lane's consequence for skipping it
            # belongs here.
            price_status["next_action"] += (
                ". This is completing the input, not producing an artifact: the answer stays "
                "a brief, direct one. If those sources genuinely publish nothing for these "
                "instruments, rerun consider --prices-unavailable '<the sources you checked>' "
                "and the question is refused rather than answered on cost basis")
    _emit(payload)


def _lifetime_cash_flows(rows):
    """Every dollar a ticker's rows have ever put into or taken out of it,
    in that ticker's own native currency. ``rows`` includes the synthesized
    anchor row (`_anchor_position_row`), so a declared position's stated
    cost counts as its lifetime buy cost exactly as a real purchase would --
    the same convention `_anchor_position_row` already establishes.

    Returns ``(buy_cost, sell_proceeds)``, each ``{ticker: dollars}``. This
    is the half of a ticker's P&L that needs no lot-matching or
    cost-averaging convention at all: paired with the ticker's *current*
    value under whichever basis prices it, ``value + sell_proceeds -
    buy_cost`` is the ticker's total economic result, and that identity
    holds regardless of how "remaining cost" is computed, because it never
    references a remaining-cost figure in the first place (see
    ``_positions_diagnosis``).
    """
    buy_cost, sell_proceeds = {}, {}
    for row in rows:
        amount = row["qty"] * row["price"]
        bucket = buy_cost if row["side"] == "buy" else sell_proceeds
        bucket[row["ticker"]] = bucket.get(row["ticker"], 0.0) + amount
    return buy_cost, sell_proceeds


def _positions_diagnosis(rows, canonical_held, weights, last_px, max_pos_override):
    """The per-position diagnosis README's "What it looks like" demonstrates
    (#561), computed once here so ``cmd_positions`` stays a thin CLI facade.

    ``canonical_held`` (``{ticker: {shares, avg_cost, cost_total,
    currency, ...}}``) and ``weights`` come from the caller, sourced from
    ``portfolio_basis.query_current_book`` / ``sizing_projection`` -- the
    exact canonical reader ``consider``'s ledger route already uses. This
    function receives them and never derives its own; owner ruling
    2026-07-30 on this PR overturned an earlier FIFO-reconstruction cut
    after a reproduced divergence: the same multi-lot-partial-sell book
    (buy, buy at a different price, partial sell) gave two different
    weights for the same ticker at the same instant depending on which
    entry point answered -- 28.6% from the FIFO route this function used to
    read, 37.5% from `consider`'s canonical one, and weight is the number
    this product's own rules are built on. `AGENTS.md` boundary 6 --
    ledger-derived current holdings stay canonical -- settles which one
    wins, and issue #456 already owns the general tension (a considered
    trade reasons on a different basis than a review's own CSV/FIFO path);
    this function no longer relitigates it, it picks the canonical side.

    Per-position *shares*/*avg_cost*/*cost_total*/*weight* below are exactly
    what `consider`'s `before.held`/`before.weights` would show for the same
    book at the same instant -- not a second, possibly-disagreeing
    computation.

    The $ P&L (`impact`) figure cannot simply add `ticker_diagnosis`'s own
    FIFO-matched `realized` to an average-cost `unreal`: that mixes two
    conventions' idea of how much cost was assigned to the shares already
    sold, and misstates the sum whenever a ticker carries both a closed and
    an open lot (confirmed with a concrete numeric example while
    diagnosing this). So `impact` is computed here from
    `_lifetime_cash_flows` instead -- `current_value + lifetime_sell_
    proceeds - lifetime_buy_cost` -- an identity that needs no lot-matching
    or cost-averaging convention at all (see that function's docstring),
    and is therefore correct under *any* basis, including this one.

    Every tag below was individually checked against the basis change:
    `too_heavy` reads the canonical `weights` passed in (never recomputed);
    `disciplined_hold`/`deep_underwater` read `cur_ret`/`avg_cost` from the
    canonical `held` this function now feeds `ticker_diagnosis` -- these
    were the FIFO-cost-basis versions before, so this is a second latent
    inconsistency this same fix closes, not a new one; `suspected_dca`/
    `suspected_averaging_down_*`/`adds_pending_confirmation` read
    `classify_adds(rows)`, which was already average-cost-consistent
    internally (its own running `pos` tracker removes sold cost at
    `cost/shares`, the same formula `ledger.derive_holdings` and
    `trade_recap.positions` use) and so needed no change at all;
    `sold_winner_early` reads `win_n`/`win_early` from each FIFO round
    trip's own `ret`/`fwd` field, self-contained and never combined with
    `held`'s cost -- a behavioral question about which specific purchase a
    sale corresponds to, orthogonal to which basis the *current* book's
    cost/weight uses, so it is left on FIFO round trips deliberately, not
    left over.

    One narrow, accepted gap: `ticker_diagnosis`'s own `abs(impact) < 1`
    floor still gates whether a ticker reaches its `tags` output at all,
    and that internal `impact` is the same FIFO/average-cost mix this
    function no longer trusts for display. A ticker whose true impact
    (this function's own computation) is large could in principle be
    excluded from tagging if that internal, uncorrected figure happens to
    round under the floor -- this function still reports that ticker's
    shares/cost/weight/impact correctly, just with `tags: []`, the same
    shape as any other undiagnosed position.

    Returns ``(diagnosed, residual)``. ``diagnosed`` covers every
    currently-held ticker at or above the meaningful-position floor
    (`trade_recap.RESIDUAL_POS_TH`), read directly off the canonical
    `weights` rather than recomputed in some other currency view.
    ``residual`` covers every other currently-held ticker: shares/cost/
    value/weight only, no diagnosis, matching the product's own "small
    lots not nitpicked" framing. Sorted by ``|impact|`` descending when
    every held ticker shares one currency (the same ranking
    ``ticker_diagnosis`` and the README-demoed card use); a mixed-currency
    book falls back to sorting by the already-comparable canonical
    ``weight`` instead, because native-currency impacts are not
    comparable across tickers without a conversion this function does not
    perform.
    """
    if not canonical_held:
        raise ReviewError("the recorded book has no open position to report")

    currencies = {holding.get("currency", "USD") for holding in canonical_held.values()}
    mixed_currency = len(currencies) > 1

    rts, _open_lots = trade_recap.round_trips(rows)
    adds_class = trade_recap.classify_adds(rows)
    buy_cost, sell_proceeds = _lifetime_cash_flows(rows)

    # ticker_diagnosis needs (shares, cost_total) tuples, and can only price
    # cur_ret/avg_cost for a ticker with a known cost -- a canonical holding
    # with none (declared shares, no avg_cost) is excluded here exactly as
    # dim_size/current_book_projection already exclude such a ticker from
    # cost-based diagnosis elsewhere, never fabricated.
    held_tuples = {t: (h["shares"], h["cost_total"]) for t, h in canonical_held.items()
                  if h.get("cost_total") is not None}
    floor = trade_recap.RESIDUAL_POS_TH
    held_dx = {t: v for t, v in held_tuples.items() if (weights.get(t) or 0.0) >= floor}
    # top_n: this is a lookup, not a card with a real-estate budget, so ask
    # for every candidate ticker_diagnosis could possibly emit rather than
    # its card-tuned default of 7 -- truncating here would silently drop a
    # real holding from the answer to "what do I currently hold".
    tdiag_by_ticker = {
        row["ticker"]: row
        for row in trade_recap.ticker_diagnosis(
            rts, adds_class, held_dx, last_px or {},
            max_pos_override=max_pos_override,
            top_n=len(held_tuples) + len(rts) + 1,
            sizing_weights=weights)
    }

    def _impact(ticker, shares):
        price = (last_px or {}).get(ticker)
        if price is None:
            return None
        return shares * price + sell_proceeds.get(ticker, 0.0) - buy_cost.get(ticker, 0.0)

    def _row(ticker, holding, diag):
        shares = holding["shares"]
        price = (last_px or {}).get(ticker)
        return {
            "ticker": ticker,
            "shares": shares,
            "avg_cost": holding.get("avg_cost"),
            "cost_total": holding.get("cost_total"),
            "value": (shares * price) if price is not None else None,
            "weight": weights.get(ticker),
            "impact": _impact(ticker, shares),
            "tags": diag["tags"] if diag else [],
        }

    diagnosed, residual = [], []
    for ticker, holding in canonical_held.items():
        row = _row(ticker, holding, tdiag_by_ticker.get(ticker))
        if (weights.get(ticker) or 0.0) >= floor:
            diagnosed.append(row)
        else:
            residual.append({k: row[k] for k in ("ticker", "shares", "avg_cost", "cost_total", "value", "weight")})

    if mixed_currency:
        diagnosed.sort(key=lambda row: row["weight"] or 0, reverse=True)
    else:
        diagnosed.sort(key=lambda row: abs(row["impact"]) if row["impact"] is not None else -1,
                       reverse=True)
    residual.sort(key=lambda row: row["weight"] or 0, reverse=True)
    return diagnosed, residual


def cmd_positions(args):
    """Read-only current-book outlet (#561): "what do I currently hold,"
    asked away from any review, `consider` premise, or `refresh` snapshot.

    Sources the book from ``<root>/ledger.jsonl`` alone -- no CSV, no
    premise, no supplied holdings view. Shares, average cost, cost total,
    and weight come from ``portfolio_basis.query_current_book`` /
    ``sizing_projection`` -- the exact canonical reader ``consider``'s
    ledger route already calls, never a second one (owner ruling
    2026-07-30; see ``_positions_diagnosis`` for the reproduced divergence
    that settled it). ``_rows_from_ledger`` -- the tested-but-until-now-
    production-unused reconstruction ``tests/test_consider.py`` already
    proves agrees with ``ledger.derive_holdings`` for the common case --
    still supplies the round trips and add-pattern history the diagnosis
    tags need, which the canonical reader does not compute at all.

    Genuinely read-only: unlike every mutating command in this file, this
    creates no session, appends no row to any ``*.jsonl`` the coach root
    tracks, and asks no question. The only disk activity is the same-day
    market-data acquisition cache every price-resolving route already
    shares (``coach.py``'s registered ``cache`` entry) -- resolving current
    prices is what this command does, the same as ``consider`` with no
    ``--prices``.

    Cash, and the disclosure set a stale price / an unreliable cash balance
    / a partial book name, are deliberately not computed here: they stay on
    Rule 1's existing freeform-answer disclosure boundary rather than a
    second computation this entry restates (`references/freeform-answers.md`).
    ``price_snapshot`` below carries just enough (``as_of`` and each
    ticker's own observed date) for the agent to judge staleness itself,
    the same raw material every other freeform answer already has.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    if args.driver_map:
        trade_recap.load_driver_map(os.path.abspath(os.path.expanduser(args.driver_map)))
    if args.instrument_map:
        instruments.load_map(os.path.abspath(os.path.expanduser(args.instrument_map)))

    ledger_path = os.path.join(root, "ledger.jsonl")
    try:
        events, skipped_lines = ledger.load_ledger(ledger_path)
    except ledger.LedgerIntegrityError as exc:
        raise ReviewError(str(exc)) from exc
    if not events:
        raise ReviewError(
            f"no recorded book in {ledger_path}; run `prepare` or `refresh` first "
            "before asking for current positions")
    rows = _rows_from_ledger(events)
    if not rows:
        raise ReviewError("the ledger carries no usable trade or snapshot position; nothing to report")

    feed = None
    if args.prices:
        try:
            feed = price_feed.load(os.path.abspath(os.path.expanduser(args.prices)))
        except price_feed.PriceFeedError as exc:
            raise ReviewError(f"price feed rejected: {exc}") from exc
    else:
        # Same live-resolution lane `consider` uses with no `--prices` (#605
        # section E): one resolved bundle, reused through the identical
        # supplied-price path rather than a second one.
        feed, _bundle = _resolve_consider_prices(args, root)

    last_px, supplied_splits = None, None
    if feed is not None:
        last_px = {ticker: row["close"] for ticker, row in feed["prices"].items()}
        supplied_splits = price_feed.splits_map(feed) or None

    splits = _effective_splits(root, supplied_splits)
    try:
        trade_recap.adjust_for_splits(rows, splits)
    except split_policy.SplitDataError as exc:
        raise ReviewError(f"split data rejected: {exc}") from exc

    valuation_manifest = None
    if last_px:
        valuation_manifest = {"as_of": feed["as_of"], "prices": last_px}
    # The identical canonical-book call `_consider_rows`'s ledger route
    # makes: one reader, so `positions` and `consider` cannot describe two
    # different current books for the same root at the same instant.
    basis = portfolio_basis.query_current_book(
        events, skipped_lines=skipped_lines, valuation_manifest=valuation_manifest,
        reference_as_of=dt.date.today().isoformat(), splits=splits)
    if basis is None:
        raise ReviewError(
            f"no trustworthy canonical current book in {ledger_path}; the ledger "
            "may be unreadable or its integrity checks failed")
    canonical_held = basis.current_book["holdings"]
    projection = portfolio_basis.sizing_projection(basis)
    weights = {}
    if projection is not None:
        weights = {ticker: entry["weight"] for ticker, entry in projection.to_dict()["values"].items()
                  if entry["applicable"]}

    max_pos_override = _position_cap_override(root)
    diagnosed, residual = _positions_diagnosis(
        rows, canonical_held, weights, last_px, max_pos_override)
    mixed_currency = len({h.get("currency", "USD") for h in canonical_held.values()}) > 1

    price_snapshot = None
    if feed is not None:
        price_snapshot = {
            "as_of": feed["as_of"],
            "observed": {ticker: row.get("observed_date")
                        for ticker, row in sorted(feed["prices"].items())},
        }
    unpriced = sorted(row["ticker"] for row in (diagnosed + residual)
                      if row["value"] is None)

    _emit({
        "status": "ok",
        "root": root,
        "basis": "priced" if last_px else "cost",
        "mixed_currency": mixed_currency,
        "n_holdings": len(diagnosed) + len(residual),
        "positions": diagnosed,
        "residual_positions": residual,
        "unpriced": unpriced,
        "price_snapshot": price_snapshot,
    })


def cmd_refresh(args):
    """Update the recorded book from a newer holdings view (#485 Slice C).

    An independent lane, not a review (owner ruling, 2026-07-28): it produces no
    card, consumes no review question budget, creates no session, and touches no
    thesis, rule or problem state. Review and ``consider`` simply read the book
    it maintains. The engine logic lives in ``book_refresh.py``; this function
    is the CLI facade and the transaction boundary.

    Two calls:

      refresh --snapshot-json P            what differs, and what needs confirming
      refresh --snapshot-json P --answers A adopt it

    The first writes nothing. The second recomputes the first from scratch while
    holding the root projection lock and refuses if the recorded book moved in
    between (``refresh_id`` is content-addressed over exactly what the user was
    shown), which is the same fail-closed shape ``SNAPSHOT_RECONCILIATION_STALE``
    gives the review lane. Onboarding is unchanged and stays where it is: a root
    with no recorded book is routed to ``prepare --snapshot-json``.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    ledger_path = os.path.join(root, "ledger.jsonl")
    snapshot, anchor = snapshot_adapter.normalize_book(args.snapshot_json)

    frozen_splits = _recorded_splits(root)

    def _frozen(events):
        # Both refresh calls check the same condition against the same recorded
        # window, so the plan and the answer cannot disagree about whether the
        # basis was provable.
        recorded_anchor = ledger.latest_anchor(events or [], declared_only=True) or {}
        _refuse_an_unprovable_split_basis(root, recorded_anchor.get("as_of"))
        return book_refresh.plan_refresh(events, snapshot, anchor,
                                         splits=frozen_splits)

    if not args.answers:
        events, _skipped = ledger.load_ledger(ledger_path)
        _emit(_frozen(events))
        return

    submitted = _load_json_arg(args.answers, "--answers")
    if not isinstance(submitted, dict):
        raise ReviewError("--answers must be an object with refresh_id and answers")
    with session.projection_transaction(root) as locked_root:
        ledger_path = os.path.join(locked_root, "ledger.jsonl")
        events, _skipped = ledger.load_ledger(ledger_path)
        receipt = _frozen(events)
        if submitted.get("refresh_id") != receipt["refresh_id"]:
            raise ReviewError(book_refresh.REFRESH_STALE)
        adoption = book_refresh.build_adoption(
            receipt, events, snapshot, anchor, submitted.get("answers") or [],
            splits=frozen_splits)
        if adoption["status"] == "resupply":
            _emit({"status": "resupply_requested", "refresh_id": adoption["refresh_id"],
                   "tickers": adoption["tickers"],
                   "next_action": "supply a corrected holdings view and run refresh again; "
                                  "nothing was written"})
            return
        report = session.append_book_adoption(
            ledger_path,
            anchor=adoption["anchor"], reconciliation=adoption["reconciliation"],
            actor_id=adoption["refresh_id"],
            sequence=session.next_projection_sequence(locked_root),
            recorded_at=adoption["anchor"]["as_of"],
            absences=adoption["absences"])
    _emit({"status": "adopted", "refresh_id": adoption["refresh_id"],
           "as_of": adoption["anchor"]["as_of"],
           "reconciliation": adoption["reconciliation"]["status"],
           "carried_forward": adoption["carried"], "recorded_absent": adoption["sold"],
           "recorded_new": adoption["appeared"], "ledger": report})


def cmd_resolve_market_data(args):
    """The supported, observable acquisition entry point (#605 section F).

    Normal users never need this: ``prepare`` and ``consider`` resolve their own
    facts. It exists so a host that wants to *see* what retrieval produced — or
    to capture it for a machine that cannot reach the provider — has a real
    command instead of importing engine modules or hand-coding provider calls,
    which is what every host was doing and what problem 4 of #605 is.

    Deliberately inert with respect to state: it computes no portfolio result and
    writes no canonical session, ledger, evaluation or projection row. The only
    thing it touches is the same-day acquisition cache every other route shares,
    and only because resolving is what it does.

    The envelope it emits is the existing ``price-feed`` one, so its output is
    directly usable as ``prepare --prices`` / ``consider --prices`` input and is
    validated by the same parser those two run.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    payload = _load_json_arg(args.request, "--request")
    try:
        request = market_data.build_request(
            instruments=payload.get("instruments") or (),
            benchmarks=payload.get("benchmarks") or (),
            currencies=payload.get("currencies") or (),
            window_start=payload.get("window_start"),
            window_end=payload.get("window_end"),
            rebase_origin=payload.get("rebase_origin"))
    except (market_data.MarketDataError, TypeError) as exc:
        raise ReviewError(f"--request is not a usable market-data request: {exc}") from exc
    bundle = market_data.resolve(request, root=root)
    envelope = market_data.to_price_feed_envelope(
        bundle, currency_by_ticker=payload.get("currency_by_ticker") or {},
        instruments_only=False)
    result = {
        "status": "ok",
        "source": bundle.source,
        "as_of": bundle.as_of,
        "window": bundle.window,
        "rebase_origin": bundle.rebase_origin,
        "coverage": bundle.coverage(),
        "gaps": bundle.gaps,
        "envelope": envelope,
    }
    if envelope is not None:
        # Refuse to hand out an envelope this repository's own parser rejects: the
        # whole value of emitting the existing shape is that the two routes that
        # consume it will accept it.
        try:
            price_feed.parse(envelope)
        except price_feed.PriceFeedError as exc:
            raise ReviewError(
                f"the resolved envelope does not satisfy the price-feed contract: {exc}") from exc
    if args.output:
        path = os.path.abspath(os.path.expanduser(args.output))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle, ensure_ascii=False, indent=2, sort_keys=True)
        result["output"] = path
    _emit(result)


def cmd_weekly_market_read(args):
    """Read-only #683 prototype companion; never refetches or saves state."""
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    pending = session.load_pending(root, args.session_id)
    plan = pending.get("plan") or {}
    # The companion belongs after the settled, rendered pre-commit card.  This
    # also means an add-cash recomputation must be previewed again before a
    # host can read the new plan here.
    if not pending.get("card-private-preview") or not pending.get("preview_receipt"):
        raise ReviewError("weekly-market-read requires the current session's rendered private card preview")
    try:
        brief = weekly_market_read.build(plan, focus=args.focus)
    except ValueError as exc:
        raise ReviewError(str(exc)) from exc
    _emit({"status": brief.get("status"), "weekly_market_read": brief,
           "private_markdown": weekly_market_read.render(brief, plan.get("language")),
           "next_action": "show this after the current private card and before the existing closing choice; if the optional question is answered, rerun this read-only command with --focus, otherwise continue without another call"})


def cmd_doctor(args):
    """Report optional runtime dependencies and what each unlocks (#322).

    Read-only preflight. The engine fail-soft degrades and never crashes on a
    missing package, so a user who skipped ``pip install`` silently loses the
    price-dependent half of the card — disclosed only as a data-coverage gap
    that misattributes the cause. ``doctor`` names the real cause: it lists each
    runtime dependency, whether it imports, and the card capability it unlocks,
    then exits non-zero when a full-experience dependency (yfinance / pandas) is
    missing so an installer or CI can gate on it. ``rich`` is optional for the v2
    card and never gates the exit code.
    """
    checks = (
        ("yfinance", True, "current prices, unrealized P&L, alpha/beta, market context, FX"),
        ("pandas", True, "the P&L curve and the alpha/beta regression"),
        ("rich", False, "the v1 terminal card (the v2 review card renders without it)"),
    )
    lines = ["fomo-kernel runtime dependencies:"]
    missing_full = []
    for module, full_experience, unlocks in checks:
        try:
            __import__(module)
            present = True
        except Exception:                        # ImportError or a broken install
            present = False
            if full_experience:
                missing_full.append(module)
        lines.append(f"  [{'ok  ' if present else 'MISS'}] {module:9s} — {unlocks}")
    if missing_full:
        lines += ["",
                  "Full-experience dependencies missing: " + ", ".join(missing_full) + ".",
                  "The card silently drops price-dependent sections without them. Install with:",
                  "  pip install -r skills/fomo-kernel/requirements.txt"]
    print("\n".join(lines))
    if missing_full:
        raise SystemExit(1)


def build_parser():
    parser = argparse.ArgumentParser(description="fomo-kernel stable review orchestration")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="run engine and emit a resumable Review Plan")
    prepare.add_argument("paths", nargs="*", help="normalized trade CSV files")
    prepare.add_argument("--root")
    prepare.add_argument("--language", default="en",
                         help="any language tag; unsupported tags fall back to en "
                              "(supported locales are the copy/<locale>.json files)")
    prepare.add_argument("--route", default="auto",
                         choices=("auto", "first_review", "weekly_review", "snapshot_review"))
    prepare.add_argument("--test-drive", action="store_true")
    prepare.add_argument("--session-nonce", default="")
    prepare.add_argument("--driver-map")
    prepare.add_argument("--instrument-map")
    prepare.add_argument("--cash", help="TR_CASH JSON string")
    prepare.add_argument("--prices",
                         help="agent-supplied price envelope (references/price-feed.md); "
                              "use when the host cannot retrieve prices itself")
    prepare.add_argument("--prices-unavailable", dest="prices_unavailable",
                         metavar="SOURCES_CHECKED",
                         help="declare that price recovery was attempted and the sources "
                              "publish nothing for the requested instruments; name the "
                              "sources you checked. Without it, a card built on no prices "
                              "at all is refused as a skipped recovery step (#623)")
    prepare.add_argument("--condition-checks", dest="condition_checks",
                         help="this period's results for state_snapshot.condition_slots_due "
                              "(references/condition-slots.md); rerun prepare with it so a "
                              "crossing can be asked about rather than only recorded")
    prepare.add_argument("--snapshot-json",
                         help="normalized position-snapshot facts; valid only for snapshot_review")
    prepare.add_argument("--card-json", help="precomputed engine card (adapter/testing)")
    prepare.add_argument("--state-json", help="precomputed engine state (adapter/testing)")
    prepare.add_argument("--timeout", type=int, default=180)
    prepare.set_defaults(func=cmd_prepare)

    for name, func in (("preview", cmd_preview), ("finalize", cmd_finalize)):
        p = sub.add_parser(name)
        p.add_argument("--session-id", required=True)
        p.add_argument("--root")
        p.add_argument("--answers")
        p.add_argument("--narrative")
        p.set_defaults(func=func)
    capture = sub.add_parser("capture", help="light-tier capture-only action (#237)")
    capture.add_argument("--session-id", required=True)
    capture.add_argument("--root")
    capture.add_argument("--entries", required=True)
    capture.set_defaults(func=cmd_capture)
    resume = sub.add_parser("resume")
    resume.add_argument("--session-id")
    resume.add_argument("--root")
    resume.add_argument("--question-surfaces",
                        help="private AI-authored surfaces to validate and freeze before presentation")
    resume.set_defaults(func=cmd_resume)
    add_cash = sub.add_parser(
        "add-cash",
        help="add the cash anchor to a prepared session and recompute it without "
             "refetching prices or re-asking anything (#357)")
    add_cash.add_argument("--session-id", required=True)
    add_cash.add_argument("--root")
    add_cash.add_argument("--cash", required=True,
                          help="TR_CASH JSON string: one {currency,amount,as_of} anchor, one "
                               "{currency,percent_of_total,as_of} anchor converted against this "
                               "session's own frozen position value and disclosed in the "
                               "response's anchor_conversion (#662), or a list of absolute-"
                               "amount anchors for a multi-currency account")
    add_cash.add_argument("--prices",
                          help="the same agent-supplied price envelope this session was "
                               "prepared with; omit when prepare fetched its own")
    add_cash.add_argument("--driver-map",
                          help="the same map this session was prepared with, if any")
    add_cash.add_argument("--instrument-map",
                          help="the same map this session was prepared with, if any")
    add_cash.add_argument("--condition-checks", dest="condition_checks",
                          help="the same condition-check envelope this session was prepared "
                               "with, if any")
    add_cash.add_argument("--timeout", type=int, default=180)
    add_cash.set_defaults(func=cmd_add_cash)
    render = sub.add_parser("render")
    render.add_argument("--session-id", required=True)
    render.add_argument("--root")
    render.add_argument("--format", choices=("json", "private-markdown", "public-markdown"),
                        default="json", help="emit JSON (default) or one canonical Markdown card")
    render.set_defaults(func=cmd_render)
    weekly_market = sub.add_parser("weekly-market-read", help="read-only weekly market companion prototype (#683)")
    weekly_market.add_argument("--session-id", required=True)
    weekly_market.add_argument("--root")
    weekly_market.add_argument("--focus", choices=weekly_market_read.FOCUSES)
    weekly_market.set_defaults(func=cmd_weekly_market_read)
    repair = sub.add_parser("repair-projections")
    repair.add_argument("--root")
    repair.set_defaults(func=cmd_repair)
    setcap = sub.add_parser("set-cap",
                            help="record the user's standing single-position cap (#324)")
    setcap.add_argument("--root")
    cap_group = setcap.add_mutually_exclusive_group(required=True)
    cap_group.add_argument("--pct",
                           help="single-position cap as a fraction in (0,1), e.g. 0.25 for 25%%")
    cap_group.add_argument("--clear", action="store_true",
                           help="remove the override and revert to the universal default")
    setcap.set_defaults(func=cmd_set_cap)
    mute = sub.add_parser("mute-rule",
                          help="stop asking about a rule while its statistics keep running (#416)")
    mute.add_argument("--root")
    mute.add_argument("--rule-id", required=True)
    mute.add_argument("--unmute", action="store_true",
                      help="bring a muted rule back into the card's rotation")
    mute.set_defaults(func=cmd_mute_rule)
    consider = sub.add_parser(
        "consider",
        help="deterministic consequence of one hypothetical trade against the current book "
             "(Layer 2, docs/decision-fomo-kernel-shape.md §3-4)")
    consider.add_argument("paths", nargs="*",
                          help="normalized trade CSV files; omit to reconstruct the book from "
                               "<root>/ledger.jsonl instead")
    consider.add_argument("--root")
    consider.add_argument("--premise",
                          help="the hypothetical trade: a path to a JSON file, or an inline JSON "
                               "object (schemas/trade-premise.schema.json)")
    consider.add_argument("--resolve", metavar="EVALUATION_ID",
                          help="record what the user did with a prior evaluation; takes no "
                               "premise")
    consider.add_argument("--decision", choices=CONSIDER_DECISIONS,
                          help="required together with --resolve")
    consider.add_argument("--prices",
                          help="agent-supplied price envelope (references/price-feed.md)")
    consider.add_argument("--prices-unavailable", dest="prices_unavailable",
                          metavar="SOURCES_CHECKED",
                          help="declare that price recovery was attempted and the sources "
                               "publish nothing for these instruments; name the sources you "
                               "checked. A forward-looking decision is then refused rather "
                               "than answered on cost basis (#629)")
    consider.add_argument("--driver-map")
    consider.add_argument("--instrument-map")
    consider.add_argument("--cash", help="TR_CASH-shaped JSON string: a single "
                                        "{as_of,amount,currency} anchor, or a list of them. "
                                        "Omitting this falls back to the last finalized "
                                        "review's own anchored balance, if it had one (#756)")
    consider.add_argument("--agent-case",
                          help="optional path to a JSON file: the structured case for and "
                               "against, {for: [...], against: [...]} "
                               "(references/trade-consequence.md)")
    consider.add_argument("--decision-context",
                          help="optional: what the user said at the moment of deciding, as a "
                               "path to a JSON file or an inline JSON object — "
                               "{reason, why_now, evidence_refs?} "
                               "(schemas/decision-context.schema.json)")
    consider.add_argument("--language", default="en",
                          help="any language tag; unsupported tags fall back to en")
    consider.set_defaults(func=cmd_consider)
    refresh = sub.add_parser(
        "refresh",
        help="update the recorded book from a newer holdings view; independent of "
             "review and consider (#485 Slice C)")
    refresh.add_argument("--root")
    refresh.add_argument("--snapshot-json", required=True,
                         help="normalized positions snapshot (references/data-contract.md)")
    refresh.add_argument("--answers",
                         help="a path to a JSON file, or an inline JSON object: "
                              "{refresh_id, answers: [{ticker, classification}]} "
                              "(schemas/book-refresh.schema.json). Omit for the "
                              "read-only difference and its pending confirmations.")
    refresh.set_defaults(func=cmd_refresh)
    positions = sub.add_parser(
        "positions",
        help="read-only per-position diagnosis of the recorded current book; no "
             "premise, no session, no durable write (#561)")
    positions.add_argument("--root")
    positions.add_argument("--prices",
                           help="agent-supplied price envelope (references/price-feed.md); "
                                "omit to resolve current prices live, as consider does")
    positions.add_argument("--driver-map")
    positions.add_argument("--instrument-map")
    # No CSV route: unlike consider, this command always reads the recorded
    # book from <root>/ledger.jsonl. `paths` still needs to exist and be
    # falsy so the consider-price-resolution helpers this reuses take their
    # ledger branch rather than raising on a missing attribute.
    positions.set_defaults(func=cmd_positions, paths=None)
    resolve_md = sub.add_parser(
        "resolve-market-data",
        help="resolve current market facts into a price-feed envelope for inspection "
             "or host reuse; prepare and consider do this themselves (#605)")
    resolve_md.add_argument("--root")
    resolve_md.add_argument("--request", required=True,
                            help="a path to a JSON file, or an inline JSON object: "
                                 "{instruments, benchmarks, currencies, window_start, "
                                 "window_end, rebase_origin}")
    resolve_md.add_argument("--output", help="write the envelope here instead of stdout")
    resolve_md.set_defaults(func=cmd_resolve_market_data)
    doctor = sub.add_parser(
        "doctor", help="check optional runtime dependencies and what each unlocks (#322)")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ReviewError, session.SessionError, thesis.ThesisError, card_renderer.RenderError,
            question_surface.QuestionSurfaceError, book_refresh.RefreshError,
            snapshot_adapter.SnapshotError) as exc:
        payload = {"status": "error", "error": str(exc)}
        # #674: a narrow, deterministic extra a raiser may attach to itself --
        # today only a non-recoverable `consider` book refusal sets one,
        # carrying the usable_facts packet the decision-framing contract
        # reads. Every other raise site across this CLI leaves it unset, so
        # this is a no-op for them and the emitted shape is unchanged.
        extra = getattr(exc, "payload_extra", None)
        if extra:
            payload.update(extra)
        _emit(payload)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
