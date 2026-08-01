"""Schema-v3 packaging of a byte-identical product answer capture for #718."""
from __future__ import annotations

import hashlib


def build(product_surface, challenge, *, fixture_id, revision):
    """Package, but never render or alter, the route's captured surface."""
    presented_text = product_surface["presented_text"]
    return {
        "schema_version": 3,
        "fixture_id": fixture_id,
        "answer_id": "consider-ai-momentum-product-capture",
        "agent_case": product_surface["agent_case"],
        "challenge": challenge,
        "presented_text": presented_text,
        "segments": product_surface["segments"],
        "generator": {"host": "fomo-kernel-consider-route-capture", "revision": revision,
                      "presented_text_sha256": hashlib.sha256(presented_text.encode()).hexdigest()},
    }


def source_fixture(evaluation, candidate, fixture_id):
    """A deterministic local source fixture for the existing #705 judge."""
    return {
        "id": fixture_id, "title": "Captured synthetic consider route", "declared_by": "agent",
        "axes": ["decision_focus", "internal_consistency", "decision_synthesis", "caveat_discipline"],
        "question": {"kind": "trade_evaluation", "text": "Synthetic AI momentum add."},
        "frozen_evaluation": evaluation,
        "agent_cases": {"captured": candidate["agent_case"]},
        "answers": [{"id": "captured-product-surface", "agent_case_ref": "captured",
                     "expect_eligible": True, "judge_fails": [], "prose": candidate["presented_text"]}],
    }
