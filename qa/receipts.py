#!/usr/bin/env python3
"""fomo-qa receipt manifest builder + aggregate reporter.

Two roles:

- ``build``: parse one ux_receipt jsonl into a run manifest recording the full
  dogfood provenance — engine version, client, exact host-declared agent model
  and effort, test data source, human involvement, and the owner verdict.
- ``report``: aggregate every manifest in the receipts dir, grouped by engine
  version, human involvement, and agent configuration, so model/effort changes
  never disappear inside one pass-rate bucket.

The human-involvement split is the point: an AI agent that plays the user and
fills its own verdict is self-attestation, not ground truth. Keeping the two
apart is what lets a pass rate mean "the experience is good" rather than "the
files were produced".
"""
import glob
import json
import os
import sys

HUMAN_LEVELS = ("owner_live", "agent_with_owner_verdict", "agent_simulated")
VERDICT_KEYS = ("controls", "card", "memory", "question_specificity", "answer_fit")
TRUST = {
    "owner_live": "REAL UX ground truth",
    "agent_with_owner_verdict": "AI-run, owner-judged",
    "agent_simulated": "contract-only (NOT a UX signal)",
}
UNKNOWN_AGENT = "legacy-unattributed"
FORBIDDEN_DECLARATIONS = {"", "unknown", "unspecified", "default", "n/a", "na", "not_available", "?"}


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


def _agent_fields(run):
    agent = run.get("agent") or {}
    return (
        agent.get("client") or run.get("client") or "?",
        agent.get("model") or UNKNOWN_AGENT,
        agent.get("effort") or UNKNOWN_AGENT,
    )


def build(argv):
    if len(argv) != 8:
        raise ValueError("build requires <receipt> <sha> <data> <human> <agent-model> <effort> <run-id> <stamp>")
    receipt, sha, data_source, human, agent_model, agent_effort, run_id, stamp = argv
    agent_model = _declared_value(agent_model, "agent model")
    agent_effort = _declared_value(agent_effort, "agent effort")
    client, route, verdict = _parse_receipt(receipt)
    manifest = {
        "run_id": run_id,
        "timestamp": stamp,
        "engine_version": {"sha": sha},
        "client": client,               # legacy reader compatibility
        "agent": {
            "client": client,           # test surface: claude / codex / ...
            "model": agent_model,       # exact host label; supplied, never inferred
            "effort": agent_effort,     # exact host setting; supplied, never inferred
        },
        "data_source": data_source,     # real / mock:<persona> / test-drive (no real CSV path)
        "human_involvement": human,     # owner_live | agent_with_owner_verdict | agent_simulated
        "route": route,
        "owner_verdict": verdict or None,
        "receipt_path": receipt,
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


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
            vals = [v for v in vals if v in ("pass", "fail")]  # not_applicable / None 不計入分母
            if not vals:
                continue
            passes = sum(1 for v in vals if v == "pass")
            print(f"   {verdict_key:20} {passes}/{len(vals)} pass")
        srcs = sorted({r.get("data_source") or "?" for r in group})
        print(f"   {'data sources':20} {', '.join(srcs)}")
        print()

    owner_runs = sum(len(v) for k, v in groups.items() if k[1] == "owner_live")
    print(
        f"Note: only owner_live runs ({owner_runs}) count as real UX ground truth; "
        "agent_simulated runs verify contract adherence only."
    )


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("build", "report"):
        print(
            "usage: receipts.py {build <receipt> <sha> <data> <human> <agent-model> <effort> <run_id> <stamp> "
            "| report [dir]}",
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
