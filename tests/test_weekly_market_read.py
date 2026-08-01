#!/usr/bin/env python3
"""Offline contract tests for the #683 read-only weekly-market prototype."""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL = os.path.join(ROOT, "skills", "fomo-kernel")
ENGINE = os.path.join(SKILL, "engine")
REVIEW = os.path.join(ENGINE, "review.py")
sys.path.insert(0, ENGINE)
sys.path.insert(0, HERE)
import offline_posture  # noqa: E402
offline_posture.apply()
import market_data  # noqa: E402
import session  # noqa: E402
import weekly_market_read as weekly  # noqa: E402


def _plan():
    return {
        "route": "weekly_review",
        "input": {"price_feed": {"provenance": {"as_of": "2026-07-18"}}},
        "engine_state": {"holdings": {"positions": {"FICT": {}, "OTHER": {}}}},
        "engine_card": {"ticker_diagnosis": [
            {"ticker": "FICT", "tags": [{"code": "too_heavy"}]},
            {"ticker": "OTHER", "tags": []},
        ]},
        "state_snapshot": {"market_context": {
            "start": "2026-07-13", "end": "2026-07-18", "missing": [], "error": None,
            "benchmarks": {
                "SPY": {"window_ret": -0.01, "last_date": "2026-07-18"},
                "QQQ": {"window_ret": -0.03, "last_date": "2026-07-18"},
                "VIX": {"last": 19.0, "delta": 1.4, "last_date": "2026-07-18"},
            },
        }},
    }


def _run(*args):
    return subprocess.run([sys.executable, REVIEW, *map(str, args)], cwd=SKILL,
                          text=True, capture_output=True, check=False, timeout=60)


def _lifecycle_artifacts(tmp):
    """Fictional adapter artifacts accepted by the real prepare/preview path."""
    state = {
        "schema_version": 2, "date_start": "2026-07-01", "date_end": "2026-07-18",
        "n_trades": 4, "n_round_trips": 1, "n_held": 1, "headline_dim": "position sizing",
        "headline_metric": {"key": "max_pos_pct", "value": 0.42}, "commitment": None,
        "metrics": {"max_pos_pct": 0.42, "max_pos_ticker": "FICT", "avgdown_count": 0,
                    "avgdown_breach": 0, "payoff": None, "ai_pct": 0.42,
                    "max_sector_pct": 0.42, "top3_pct": 0.42, "n_holdings": 1,
                    "exit_severity": None, "hold_severity": None, "beta": None,
                    "alpha_ann": None, "alpha_t": None, "alpha_credible": None,
                    "longest_hold_days": 17, "longest_hold_ticker": "FICT",
                    "worst_cur_ret": -0.08, "worst_cur_ret_ticker": "FICT"},
        "rule": None, "insufficient_data": False,
        "holdings": {"as_of": "2026-07-18", "derived_from": "synthetic_fixture",
                     "positions": {"FICT": {"shares": 10, "cost": 1000, "avg_cost": 100,
                                            "cycle_start": "2026-07-01",
                                            "cycle_id": "FICT#2026-07-01#1",
                                            "add_count": 1,
                                            "decision_cursor": "FICT#2026-07-01#1#add#1"}}},
        "currency_meta": {"aggregate_currency": "USD", "mixed": False},
        "portfolio_structure": {"schema_version": 1, "allocation_weight": 0.42,
                                "concentrated_etf_weight": 0, "allocation_etfs": [],
                                "concentrated_etfs": [], "metadata_gaps": []},
        "cash": None, "problem_events": [], "problem_opportunities": {},
        "market_context": {"start": "2026-07-13", "end": "2026-07-18", "missing": [], "error": None,
                           "benchmarks": {"VIX": {"last": 19.0, "delta": 1.4,
                                                  "last_date": "2026-07-18"}}},
    }
    hole = {"dim": "position sizing", "severity": 0.8, "tier_weight": 1.0,
            "number_line": "Synthetic concentration is above the recorded cap.",
            "lens_rule": "Check size before adding.", "lens_quote": "Size first.",
            "raw": {"dim": "position sizing", "tier": 1, "triggered": True,
                    "severity": 0.8, "count": 1, "breach": 1, "tickers": ["FICT"]}}
    card = {
        "schema_version": 1, "philosophy": "synthetic fixture", "strength": "No synthetic strength.",
        "overview": {"total_pnl": 0, "realized": 0, "unrealized": 0, "payoff": None,
                     "avg_win": None, "avg_loss": None}, "what_if": None,
        "ticker_diagnosis": [{"ticker": "FICT", "tags": [{"code": "too_heavy"}]}],
        "thesis_questions": [], "top_holes": [hole],
        "candidate_rules": [{"dim": "position sizing", "rule": "Check size before adding."}],
        "prescriptions": [], "alpha_beta_breakdown": {}, "payoff_attribution": {},
        "dims_raw": [hole["raw"]], "data_integrity": {},
        "currency_meta": {"aggregate_currency": "USD"}, "cash": None,
        "acct_perf": {"note": "synthetic"}, "portfolio_structure": state["portfolio_structure"],
        "honesty_ledger": [], "pnl_curve": {"note": "synthetic"},
    }
    card_path, state_path = os.path.join(tmp, "card.json"), os.path.join(tmp, "state.json")
    with open(card_path, "w", encoding="utf-8") as handle:
        json.dump(card, handle)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    return card_path, state_path


def _answers(plan):
    return {
        "session_id": plan["session_id"],
        "answers": [{"question_id": row["id"], "choice": "skip"} for row in plan["question_queue"]],
        "thesis_updates": [{"cycle_id": row["cycle_id"], "why": "Synthetic reason remains held.",
                            "exit_trigger": "Synthetic reason no longer holds.", "horizon": "quarters"}
                           for row in plan.get("missing_thesis_positions") or []],
        "observations": [], "commitment": {"choice": "skip"},
    }


def _narrative(plan):
    return {"headline": "Synthetic concentration deserves a recorded check.",
            "mirror": "Synthetic review keeps the concentration check visible.",
            "honesty": {key: "Synthetic fixture records this limit plainly."
                        for key in plan["card_plan"]["required_honesty_keys"]}}


def test_positive_scene_is_complete_and_bounded():
    brief = weekly.build(_plan())
    assert brief["status"] == "available"
    assert len(brief["selected_holdings"]) <= 3
    assert len(brief["next_week_watch"]) <= 2
    assert brief["optional_question"] == {
        "required": False, "choices": ["business_evidence", "position_size", "not_sure"],
        "selected": None, "prompt": "Which check would make next week's follow-up more useful?"}
    assert all(row["kind"] == "engine_fact" and row["source"] and row["as_of"]
               for row in brief["market_facts"])
    text = weekly.render_zh_tw(brief)
    for heading in ("本週市場發生了什麼", "對你的組合意味著什麼", "這週最容易犯的錯誤", "下週關注"):
        assert heading in text
    assert "engine fact" not in text and "agent judgment" not in text
    assert "## What happened in markets this week" in weekly.render_en(brief)


def test_generic_recap_is_omitted_without_existing_book_connection():
    plan = _plan()
    plan["engine_card"]["ticker_diagnosis"] = []
    assert weekly.build(plan) == {"status": "omitted", "reason": "no_book_specific_connection"}


def test_unsupported_motive_is_not_claimed():
    text = weekly.render_zh_tw(weekly.build(_plan()))
    assert "你的持有動機" not in text
    assert "because you" not in text.lower()


def test_question_is_one_optional_hook_not_a_dump():
    question = weekly.build(_plan())["optional_question"]
    assert question["required"] is False
    assert question["choices"] == ["business_evidence", "position_size", "not_sure"]
    assert weekly.render_zh_tw(weekly.build(_plan())).count("## 可選問題") == 1


def test_two_answers_change_the_next_week_watch_and_skipping_stays_complete():
    business = weekly.build(_plan(), focus="business_evidence")
    size = weekly.build(_plan(), focus="position_size")
    skipped = weekly.build(_plan())
    assert business["next_week_watch"] != size["next_week_watch"]
    assert business["optional_question"]["selected"] == "business_evidence"
    assert "## 可選問題" not in weekly.render_zh_tw(size)
    assert skipped["next_week_watch"] and skipped["optional_question"]["selected"] is None


def test_reading_the_frozen_plan_cannot_fetch_a_provider():
    original = market_data.resolve
    try:
        market_data.resolve = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider fetch"))
        assert weekly.build(_plan())["status"] == "available"
    finally:
        market_data.resolve = original


def test_cli_requires_final_private_card_preview():
    with tempfile.TemporaryDirectory() as root:
        session.save_pending(root, "synthetic-weekly", plan=_plan())
        result = _run("weekly-market-read", "--root", root, "--session-id", "synthetic-weekly")
        assert result.returncode == 2
        assert "rendered private card preview" in result.stdout


def test_cli_real_prepare_preview_trajectory_is_read_only_and_no_duplicate_fetch():
    """Actual lifecycle: prepare -> previewed card -> brief -> answer -> changed watch."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        card, state = _lifecycle_artifacts(tmp)
        prepared = _run("prepare", "--root", root, "--route", "weekly_review", "--language", "zh-TW",
                        "--card-json", card, "--state-json", state)
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        plan = json.loads(prepared.stdout)["review_plan"]
        answers_path, narrative_path = os.path.join(tmp, "answers.json"), os.path.join(tmp, "narrative.json")
        with open(answers_path, "w", encoding="utf-8") as handle:
            json.dump(_answers(plan), handle)
        with open(narrative_path, "w", encoding="utf-8") as handle:
            json.dump(_narrative(plan), handle)
        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers_path, "--narrative", narrative_path)
        assert preview.returncode == 0, preview.stdout + preview.stderr
        preview_payload = json.loads(preview.stdout)
        assert preview_payload["private_card"], "the final precommit card must exist before the brief"

        before = session.load_pending(root, plan["session_id"])
        before_plan = copy.deepcopy(before["plan"])
        before_answers = copy.deepcopy(before["answers"])
        before_preview = before["card-private-preview"]
        first = _run("weekly-market-read", "--root", root, "--session-id", plan["session_id"])
        assert first.returncode == 0, first.stdout + first.stderr
        first_payload = json.loads(first.stdout)
        assert first_payload["status"] == "available"
        assert first_payload["weekly_market_read"]["optional_question"]["selected"] is None
        assert first_payload["private_markdown"].index("本週市場發生了什麼") < first_payload["private_markdown"].index("## 可選問題")

        second = _run("weekly-market-read", "--root", root, "--session-id", plan["session_id"],
                      "--focus", "position_size")
        assert second.returncode == 0, second.stdout + second.stderr
        second_payload = json.loads(second.stdout)
        assert second_payload["weekly_market_read"]["next_week_watch"] != \
            first_payload["weekly_market_read"]["next_week_watch"]
        after = session.load_pending(root, plan["session_id"])
        assert after["plan"] == before_plan and after["answers"] == before_answers
        assert after["card-private-preview"] == before_preview


def _main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS ", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1; print("FAIL ", name, exc)
    print(f"{failed} failed")
    return failed


if __name__ == "__main__":
    raise SystemExit(_main())
