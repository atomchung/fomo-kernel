"""The bounded, product-owned final surface for a ``consider`` evaluation.

This is deliberately deterministic.  A host may ask an LLM to act as the
user, but it must not ask that model to write the answer the user sees.
``review.py consider --product-surface`` uses this renderer after the route
has frozen its consequence and challenge obligations.
"""
from __future__ import annotations


def _display(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if abs(value) <= 1:
            return f"{value * 100:.4g}%"
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _claim_for(entry):
    topic, value, anchor = entry["topic"], _display(entry["value"]), entry.get("anchor")
    if topic == "rule_collision":
        detail = entry.get("detail") or {}
        return f"The recorded rule is {detail.get('text')}; this trade is {value}.", anchor
    return f"The frozen {topic} fact is {value}.", anchor


def render(challenge):
    """Return the exact answer surface and its validated structured claims."""
    claims_for = [{
        "claim": "If the demand observation proves durable, changing the cap can be a deliberate choice.",
        "provenance": "agent_judgment",
    }]
    quotes = [row["text"] for row in challenge.get("quote_verbatim", [])]
    if quotes:
        quoted = "The user said: " + " and ".join(repr(item) for item in quotes) + "."
        claims_for.append({"claim": quoted, "provenance": "agent_judgment"})
    claims_against = []
    for entry in challenge["must_state"]:
        claim, anchor = _claim_for(entry)
        if anchor:
            claims_against.append({"claim": claim, "provenance": "engine_fact", "anchor": anchor})
    agent_case = {"for": claims_for, "against": claims_against}
    parts, segments, cursor = [], [], 0

    def add(kind, text, **fields):
        nonlocal cursor
        start = cursor
        parts.append(text)
        cursor += len(text)
        segments.append({"kind": kind, **fields, "start": start, "end": cursor})

    def sep(text):
        add("separator", text)

    for side in ("for", "against"):
        for index, row in enumerate(agent_case[side]):
            if parts:
                sep(" ")
            add("claim_ref", row["claim"], side=side, index=index)
    sep("\n\n")
    add("connective", "The decision tension is whether the stated demand observation justifies accepting these frozen consequences.",
        provenance="agent_judgment")
    sep(" ")
    obligations = [f"must_state[{index}]" for index, entry in enumerate(challenge["must_state"])
                   if entry["topic"] in {"basis", "price_basis", "disclosure", "excluded_holding"}]
    obligations.extend(f"unchecked.{key}" for key in challenge["unchecked"])
    add("limitation", "The product did not check: " + ", ".join(challenge["unchecked"]) + ".",
        obligation_refs=obligations)
    sep("\n\n")
    add("resolution", "Your call: keep it open, decline it, or modify the size; nothing has been executed.",
        workflow_options=["open", "declined", "modified"])
    return {"route": "consider", "agent_case": agent_case,
            "presented_text": "".join(parts), "segments": segments}
