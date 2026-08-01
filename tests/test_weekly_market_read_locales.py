#!/usr/bin/env python3
"""Locale regressions for #683 WeeklyMarketRead watch copy."""
import json
import os
import tempfile

import test_weekly_market_read as base

weekly = base.weekly

ENGLISH_WATCH_FRAGMENTS = (
    "Before explaining",
    "Before treating",
    "Before acting",
    "VIX rose in the frozen review window",
)


def _assert_no_english_watch_copy(text):
    for fragment in ENGLISH_WATCH_FRAGMENTS:
        assert fragment not in text, f"locale output leaked English watch copy: {fragment!r}"


def test_every_focus_is_localized_in_both_chinese_renderers():
    zh_tw_expected = {
        None: "在行動前，先判斷應檢查公司特定證據，還是已記錄的部位大小。",
        "business_evidence": "在用市場解釋價格變動前，先確認是否出現新的公司特定證據。",
        "position_size": "在把較低價格當成證據前，先將已記錄的部位大小與既有上限比較。",
        "not_sure": "在行動前，先判斷應檢查公司特定證據，還是已記錄的部位大小。",
    }
    zh_cn_expected = {
        None: "在行动前，先判断应检查公司特定证据，还是已记录的仓位大小。",
        "business_evidence": "在用市场解释价格变动前，先确认是否出现新的公司特定证据。",
        "position_size": "在把较低价格当成证据前，先将已记录的仓位大小与既有上限比较。",
        "not_sure": "在行动前，先判断应检查公司特定证据，还是已记录的仓位大小。",
    }
    for focus, expected in zh_tw_expected.items():
        text = weekly.render_zh_tw(weekly.build(base._plan(), focus=focus))
        assert expected in text
        assert "波動率指數在凍結復盤窗口內上升" in text
        _assert_no_english_watch_copy(text)
    for focus, expected in zh_cn_expected.items():
        text = weekly.render_zh_cn(weekly.build(base._plan(), focus=focus))
        assert expected in text
        assert "波动率指数在冻结复盘窗口内上升" in text
        _assert_no_english_watch_copy(text)


def test_real_zh_tw_second_focus_read_stays_traditional_chinese():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        card, state = base._lifecycle_artifacts(tmp)
        prepared = base._run(
            "prepare", "--root", root, "--route", "weekly_review", "--language", "zh-TW",
            "--card-json", card, "--state-json", state,
        )
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        plan = json.loads(prepared.stdout)["review_plan"]
        answers_path = os.path.join(tmp, "answers.json")
        narrative_path = os.path.join(tmp, "narrative.json")
        with open(answers_path, "w", encoding="utf-8") as handle:
            json.dump(base._answers(plan), handle)
        with open(narrative_path, "w", encoding="utf-8") as handle:
            json.dump(base._narrative(plan), handle)
        preview = base._run(
            "preview", "--root", root, "--session-id", plan["session_id"],
            "--answers", answers_path, "--narrative", narrative_path,
        )
        assert preview.returncode == 0, preview.stdout + preview.stderr

        first = base._run(
            "weekly-market-read", "--root", root, "--session-id", plan["session_id"],
        )
        assert first.returncode == 0, first.stdout + first.stderr
        first_text = json.loads(first.stdout)["private_markdown"]
        assert "## 下週關注" in first_text
        _assert_no_english_watch_copy(first_text)

        second = base._run(
            "weekly-market-read", "--root", root, "--session-id", plan["session_id"],
            "--focus", "business_evidence",
        )
        assert second.returncode == 0, second.stdout + second.stderr
        second_payload = json.loads(second.stdout)
        second_text = second_payload["private_markdown"]
        assert second_payload["weekly_market_read"]["optional_question"]["selected"] == "business_evidence"
        assert "在用市場解釋價格變動前，先確認是否出現新的公司特定證據。" in second_text
        assert "## 可選問題" not in second_text
        _assert_no_english_watch_copy(second_text)


def _main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS ", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("FAIL ", name, exc)
    print(f"{failed} failed")
    return failed


if __name__ == "__main__":
    raise SystemExit(_main())
