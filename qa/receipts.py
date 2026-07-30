#!/usr/bin/env python3
"""fomo-qa receipt manifest builder + aggregate reporter.

Two roles:

- ``build``: parse one ux_receipt jsonl into a run manifest recording the full
  dogfood provenance — engine version, client, exact host-declared agent model
  and effort, test data source, human involvement, the owner verdict, and the
  campaign binding (which named acceptance case this run tested, whether it
  started from fresh or continued state, and its predecessor run if any).
- ``report``: aggregate every manifest in the receipts dir, grouped by engine
  version, human involvement, and agent configuration, so model/effort changes
  never disappear inside one pass-rate bucket; then a second pass grouped by
  campaign/case/client, so a set of individually-valid manifests can be read
  as (or fail to be) proof that a required campaign was actually covered.

The human-involvement split is the point: an AI agent that plays the user and
fills its own verdict is self-attestation, not ground truth. Keeping the two
apart is what lets a pass rate mean "the experience is good" rather than "the
files were produced".

The campaign binding is the same idea one level up: two individually-valid
manifests cannot prove a required multi-step QA trajectory used the intended
starting state, or that every campaign case was covered rather than the same
easy case repeated, unless each run says which case it tested and whether it
picked up where an earlier accepted run left off.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

HUMAN_LEVELS = ("owner_live", "agent_with_owner_verdict", "agent_simulated")
# Every axis an owner verdict can carry. This is what the archived manifest
# keeps of the verdict, so an axis missing here is an axis the archive silently
# drops — for a card-free route that would be the only judgment it had (#523:
# `change` is to a refresh what `card` is to a review, and #544's four
# consider axes are that route's whole user-observable judgment). Locked
# against ux_receipt's own declared axes by a drift test in
# tests/test_receipts.py; extra keys here are harmless (an older archived
# receipt simply has none), a missing one is not.
VERDICT_KEYS = ("controls", "card", "memory", "change",
                "comprehension", "usefulness", "friction", "resolution",
                "question_specificity", "answer_fit")
TRUST = {
    "owner_live": "REAL UX ground truth",
    "agent_with_owner_verdict": "AI-run, owner-judged",
    "agent_simulated": "contract-only (NOT a UX signal)",
}
UNKNOWN_AGENT = "legacy-unattributed"
FORBIDDEN_DECLARATIONS = {"", "unknown", "unspecified", "default", "n/a", "na", "not_available", "?"}

# campaign/case_id charset: intentionally tighter than the general
# _declared_value hygiene below. This is a privacy guard as much as a hygiene
# one — banning '/' and all whitespace means a real CSV path (which contains
# '/') or a free-text finding (which contains spaces) cannot be smuggled into
# a manifest field that later gets quoted verbatim in a public issue. Only a
# stable, deliberately-chosen identifier like "issue:#486" or "M0-U01" fits
# through this hole.
CAMPAIGN_CASE_ID_RE = re.compile(r"^[A-Za-z0-9:#._-]{1,64}$")

# parent_run_id charset: blocks '/' and '..' so a parent-run-id can never walk
# outside the receipt dir it is looked up in.
PARENT_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# data_source grammar (#588): the manifest is the artifact whose values get
# quoted in public acceptance issues, and this field's discipline ("real /
# mock:<persona> / test-drive — no real CSV path") lived only in a comment.
# A closed grammar makes a path or free prose fail closed instead of being
# serialized one copy-paste away from a #274-class leak. The persona token
# shares the identifier charset every other manifest id uses.
DATA_SOURCE_RE = re.compile(r"^(real|test-drive|mock:[A-Za-z0-9._-]{1,64})$")


def _parse_receipt(path):
    """Best-effort pull of client/route/verdict out of a ux_receipt jsonl."""
    client = route = None
    verdict = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("event") == "capabilities_declared":
                    client = row.get("client", client)
                    route = row.get("route", route)
                if row.get("event") == "owner_verdict":
                    verdict = {k: row.get(k) for k in VERDICT_KEYS if row.get(k) is not None}
    except (OSError, json.JSONDecodeError):
        pass
    return client, route, verdict


def _declared_value(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if value.lower() in FORBIDDEN_DECLARATIONS:
        raise ValueError(f"{field} must be an exact host-declared value, not {value!r}")
    if "\n" in value or "\r" in value or len(value) > 200:
        raise ValueError(f"{field} must be one line no longer than 200 characters")
    return value


def _campaign_value(value, field):
    """campaign/case_id: the existing hygiene rule, plus a locked-down charset.

    See the CAMPAIGN_CASE_ID_RE comment above for why the charset itself is a
    privacy guard, not just a formatting nicety.
    """
    value = _declared_value(value, field)
    if not CAMPAIGN_CASE_ID_RE.match(value):
        raise ValueError(
            f"{field} must match ^[A-Za-z0-9:#._-]{{1,64}}$ "
            f"(letters, digits, ':#._-' only — no '/' and no whitespace) — got {value!r}"
        )
    return value


def _resolve_state_lineage(state_mode, parent_run_id, receipt_dir):
    """Validate state_mode and parent_run_id together — this is one rule, not two.

    fresh:      parent_run_id must be ABSENT. A fresh run has no predecessor;
                accepting one anyway would let lineage claims accumulate that
                mean nothing.
    continued:  parent_run_id must be PRESENT, path-safe, and must name a
                manifest that actually exists in receipt_dir. Lineage that
                points at nothing is a free-text claim, not evidence — the
                existence check is what turns it into evidence.

    Returns the parent_run_id to store in the manifest (None for fresh).
    """
    if state_mode not in ("fresh", "continued"):
        raise ValueError(f"state-mode must be 'fresh' or 'continued', not {state_mode!r}")

    if state_mode == "fresh":
        if parent_run_id:
            raise ValueError(
                "state-mode=fresh must not have --parent-run-id "
                "(a fresh run has no predecessor)"
            )
        return None

    # continued
    if not parent_run_id:
        raise ValueError("state-mode=continued requires --parent-run-id")
    if not PARENT_RUN_ID_RE.match(parent_run_id):
        raise ValueError(
            f"parent-run-id must match ^[A-Za-z0-9._-]+$ "
            f"(no '/' and no '..') — got {parent_run_id!r}"
        )
    parent_manifest = os.path.join(receipt_dir, f"{parent_run_id}.manifest.json")
    if not os.path.isfile(parent_manifest):
        raise ValueError(f"parent-run-id names a manifest that does not exist: {parent_manifest}")
    return parent_run_id


def _receipt_sha256(path):
    """SHA-256 of the receipt file's bytes, computed here — never supplied by
    the caller, so the digest cannot silently drift from the archived bytes.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ValueError(f"could not read --receipt {path!r}: {exc}")
    return hashlib.sha256(data).hexdigest()


def _agent_fields(run):
    agent = run.get("agent") or {}
    return (
        agent.get("client") or run.get("client") or "?",
        agent.get("model") or UNKNOWN_AGENT,
        agent.get("effort") or UNKNOWN_AGENT,
    )


def _build_arg_parser():
    parser = argparse.ArgumentParser(prog="receipts.py build")
    parser.add_argument("--receipt", required=True, help="source receipt jsonl path (read and hashed)")
    parser.add_argument("--archived-path", required=True, help="path recorded in the manifest's receipt_path")
    parser.add_argument("--receipt-dir", required=True, help="archive dir; where the parent-run-id lookup happens")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--data-source", required=True)
    parser.add_argument("--human", required=True)
    parser.add_argument("--agent-model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stamp", required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--state-mode", required=True, help="fresh|continued")
    parser.add_argument("--parent-run-id", default=None)
    return parser


def build(argv):
    args = _build_arg_parser().parse_args(argv)

    agent_model = _declared_value(args.agent_model, "agent model")
    agent_effort = _declared_value(args.effort, "agent effort")
    campaign = _campaign_value(args.campaign, "campaign")
    case_id = _campaign_value(args.case_id, "case-id")
    data_source = _declared_value(args.data_source, "data-source")
    if not DATA_SOURCE_RE.match(data_source):
        raise ValueError(
            f"data-source must be 'real', 'test-drive', or 'mock:<persona>' "
            f"(persona limited to [A-Za-z0-9._-], max 64) — got {data_source!r}; "
            "never a path or free text, because the manifest is quoted in "
            "public issues"
        )
    parent_run_id = _resolve_state_lineage(args.state_mode, args.parent_run_id, args.receipt_dir)
    receipt_sha256 = _receipt_sha256(args.receipt)

    client, route, verdict = _parse_receipt(args.receipt)
    # The trace's own client string ends up quoted in public campaign-coverage
    # reports exactly like the ids above, and _parse_receipt never charset-checks
    # it. None stays None: an unreadable trace is already "unattributed", and
    # inventing a value for it is what report's legacy handling exists to avoid.
    if client is not None and not PARENT_RUN_ID_RE.match(str(client)):
        raise ValueError(
            f"the receipt's declared client must match ^[A-Za-z0-9._-]+$ "
            f"(no '/', no whitespace) — got {client!r}"
        )
    manifest = {
        "run_id": args.run_id,
        "timestamp": args.stamp,
        "engine_version": {"sha": args.sha},
        "campaign": campaign,
        "case_id": case_id,
        "state_mode": args.state_mode,
        "parent_run_id": parent_run_id,
        "receipt_sha256": receipt_sha256,
        "client": client,               # legacy reader compatibility
        "agent": {
            "client": client,           # test surface: claude / codex / ...
            "model": agent_model,       # exact host label; supplied, never inferred
            "effort": agent_effort,     # exact host setting; supplied, never inferred
        },
        "data_source": data_source,  # closed grammar, gated above (#588)
        "human_involvement": args.human,  # owner_live | agent_with_owner_verdict | agent_simulated
        "route": route,
        "owner_verdict": verdict or None,
        "receipt_path": args.archived_path,
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def _print_campaign_coverage(runs, receipt_dir):
    """Which named case did each archived run test, on which client, and what
    was its state lineage.

    A manifest missing campaign/case_id is grouped under UNKNOWN_AGENT
    ("legacy-unattributed") rather than inventing an attribution for it —
    the whole point is that unattributed evidence stays visibly unattributed
    rather than being retrofitted.
    """
    print("== Campaign coverage ==")
    print()
    tree = {}
    for run in runs:
        campaign = run.get("campaign") or None
        case_id = run.get("case_id") or None
        if not campaign or not case_id:
            campaign = UNKNOWN_AGENT
            case_id = UNKNOWN_AGENT
        client, _model, _effort = _agent_fields(run)
        tree.setdefault(campaign, {}).setdefault(case_id, {}).setdefault(client, []).append(run)

    for campaign in sorted(tree):
        print(f"campaign: {campaign}")
        cases = tree[campaign]
        for case_id in sorted(cases):
            print(f"  case: {case_id}")
            clients = cases[case_id]
            for client in sorted(clients):
                print(f"    client: {client}")
                group = sorted(clients[client], key=lambda r: r.get("run_id") or "")
                for run in group:
                    run_id = run.get("run_id") or "?"
                    state_mode = run.get("state_mode") or "?"
                    line = f"      run {run_id}  state={state_mode}"
                    if state_mode == "continued":
                        parent = run.get("parent_run_id") or "?"
                        line += f"  parent={parent}"
                        # report reads an arbitrary (possibly partial) directory,
                        # so a missing parent is an inline annotation, not a
                        # hard failure the way it is at archive time in build().
                        if parent != "?" and not os.path.isfile(
                            os.path.join(receipt_dir, f"{parent}.manifest.json")
                        ):
                            line += "  (parent manifest not in this directory)"
                    print(line)
        print()


def report(argv):
    receipt_dir = argv[0] if argv else os.path.expanduser("~/.fomo-qa-receipts")
    manifests = sorted(glob.glob(os.path.join(receipt_dir, "*.manifest.json")))
    if not manifests:
        print(f"No run manifests in {receipt_dir}. Run a dogfood via /fomo-qa first.")
        return
    runs = []
    for path in manifests:
        try:
            with open(path, encoding="utf-8") as handle:
                runs.append(json.load(handle))
        except (OSError, json.JSONDecodeError):
            continue

    print(f"fomo-qa dogfood runs: {len(runs)} total in {receipt_dir}\n")

    groups = {}
    for run in runs:
        sha = (run.get("engine_version") or {}).get("sha", "?")
        human = run.get("human_involvement", "?")
        client, model, effort = _agent_fields(run)
        groups.setdefault((sha, human, client, model, effort), []).append(run)

    order = {h: i for i, h in enumerate(HUMAN_LEVELS)}
    for key in sorted(groups, key=lambda k: (k[0], order.get(k[1], 99))):
        sha, human, client, model, effort = key
        group = groups[key]
        trust = TRUST.get(human, "?")
        plural = "s" if len(group) != 1 else ""
        print(
            f"== main@{sha}  x  {human}  x  {client}/{model}  x  effort={effort} "
            f"[{trust}]  ({len(group)} run{plural})"
        )
        for verdict_key in VERDICT_KEYS:
            vals = [(r.get("owner_verdict") or {}).get(verdict_key) for r in group]
            # not_applicable and None stay out of the denominator: a verdict the
            # route never asked for is not a verdict the run failed to earn.
            vals = [v for v in vals if v in ("pass", "fail")]
            if not vals:
                continue
            passes = sum(1 for v in vals if v == "pass")
            print(f"   {verdict_key:20} {passes}/{len(vals)} pass")
        srcs = sorted({r.get("data_source") or "?" for r in group})
        print(f"   {'data sources':20} {', '.join(srcs)}")
        print()

    _print_campaign_coverage(runs, receipt_dir)

    owner_runs = sum(len(v) for k, v in groups.items() if k[1] == "owner_live")
    print(
        f"Note: only owner_live runs ({owner_runs}) count as real UX ground truth; "
        "agent_simulated runs verify contract adherence only."
    )


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("build", "report"):
        print(
            "usage: receipts.py {build --receipt <path> --archived-path <path> --receipt-dir <dir> "
            "--sha <sha> --data-source <s> --human <h> --agent-model <m> --effort <e> "
            "--run-id <id> --stamp <s> --campaign <c> --case-id <c> --state-mode fresh|continued "
            "[--parent-run-id <id>] | report [dir]}",
            file=sys.stderr,
        )
        return 2
    try:
        if sys.argv[1] == "build":
            build(sys.argv[2:])
        else:
            report(sys.argv[2:])
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
