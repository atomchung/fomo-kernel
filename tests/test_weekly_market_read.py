#!/usr/bin/env python3
"""Offline contract tests for the #683 read-only weekly-market prototype."""
import os
import json
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
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


def test_positive_scene_is_complete_and_bounded():
    brief = weekly.build(_plan())
    assert brief["status"] == "available"
    assert len(brief["selected_holdings"]) <= 3
    assert len(brief["next_week_watch"]) <= 2
    assert brief["optional_question"]["required"] is False
    assert all(row["kind"] == "engine_fact" and row["source"] and row["as_of"]
               for row in brief["market_facts"])
    text = weekly.render_zh_tw(brief)
    for heading in ("本周市場發生了什麼", "對你的組合意味著什麼", "這周最容易犯的錯誤", "下周關注"):
        assert heading in text


def test_generic_recap_is_omitted_without_existing_book_connection():
    plan = _plan()
    plan["engine_card"]["ticker_diagnosis"] = []
    assert weekly.build(plan) == {"status": "omitted", "reason": "no_book_specific_connection"}


def test_market_observation_never_becomes_user_motive():
    text = weekly.render_zh_tw(weekly.build(_plan()))
    assert "不是你的持有動機" in text
    assert "user_motive" not in repr(weekly.build(_plan()))


def test_question_is_one_optional_hook_not_a_dump():
    question = weekly.build(_plan())["optional_question"]
    assert question["required"] is False
    assert question["choices"] == ["business_evidence", "position_size", "not_sure"]


def test_two_answers_change_the_next_week_watch():
    business = weekly.build(_plan(), focus="business_evidence")["next_week_watch"]
    size = weekly.build(_plan(), focus="position_size")["next_week_watch"]
    skipped = weekly.build(_plan())["next_week_watch"]
    assert business != size
    assert skipped and weekly.build(_plan())["status"] == "available"


def test_reading_the_frozen_plan_cannot_fetch_a_provider():
    original = market_data.resolve
    try:
        market_data.resolve = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider fetch"))
        assert weekly.build(_plan())["status"] == "available"
    finally:
        market_data.resolve = original


def test_cli_vertical_slice_reads_pending_plan_without_writing_it():
    with tempfile.TemporaryDirectory() as root:
        session.save_pending(root, "synthetic-weekly", plan=_plan())
        command = [sys.executable, os.path.join(ENGINE, "review.py"), "weekly-market-read",
                   "--root", root, "--session-id", "synthetic-weekly", "--focus", "position_size"]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["status"] == "available"
        assert out["weekly_market_read"]["persistence"] == "none"
        assert "本周市場發生了什麼" in out["private_markdown"]
        assert session.load_pending(root, "synthetic-weekly")["plan"] == _plan()


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
