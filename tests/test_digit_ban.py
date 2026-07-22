#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narrative digit-ban tests (#194 item 1) — offline, deterministic, no pytest.

The narrative contract is "prose only; magnitudes come from the engine".
``re.search(r"\\d", ...)`` already rejects ASCII/Unicode digits, but not
spelled-out quantities.  ``card_renderer.numeric_claim`` closes that hole for
both zh ("三成"/"五萬"/"兩倍") and en ("thirty percent").

Coverage:
  A. Spelled-out CJK quantity claims are rejected.
  B. ASCII and full-width digits are still rejected (the pre-existing gate).
  C. English number-word quantities are rejected.
  D. Idioms and legitimate prose (incl. every existing v2 narrative fixture)
     are NOT false-positived.
  E. Soft units only fire at a word boundary, and the numeral is load-bearing
     (成本/達成/這個 stay clean; the mutation dance below drops the numeral table).
  F. schema/code single source of truth: narrative.schema.json no longer carries
     the divergent ECMA "^\\D+$" pattern and points at validate_narrative.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
SCHEMA = os.path.join(ROOT, "skills", "fomo-kernel", "schemas", "narrative.schema.json")
sys.path.insert(0, ENGINE)
import card_renderer as cr  # noqa: E402


def _narr(**over):
    """A legal minimal narrative with optional field overrides."""
    base = {"headline": "先看清楚這次的行為", "mirror": "The pattern is what matters, not the outcome."}
    base.update(over)
    return base


# ─────────────── A. spelled-out CJK quantity claims are rejected ───────────────

CJK_CLAIMS = [
    "三成", "五萬", "兩倍", "三十趴", "佔了三成五", "賺了九成", "虧了五萬元",
    "加了兩成", "報酬翻了三倍", "幾十趴", "數百檔", "百分之三十", "百分之五",
    "十股", "五張", "第三名賺了兩成",  # 第三 is an idiom, but 兩成 still fires
]


def test_cjk_quantity_claims_flagged_by_numeric_claim():
    for text in CJK_CLAIMS:
        assert cr.numeric_claim(text) is not None, f"should flag CJK claim: {text!r}"


def test_cjk_quantity_claims_rejected_by_validate_narrative():
    for text in CJK_CLAIMS:
        try:
            cr.validate_narrative(_narr(mirror=text))
        except cr.RenderError as exc:
            assert "numeric claim" in str(exc), f"unexpected message for {text!r}: {exc}"
        else:
            raise AssertionError(f"validate_narrative accepted a CJK claim: {text!r}")


# ─────────────── B. ASCII/full-width digits still rejected (pre-existing) ──────

def test_ascii_and_fullwidth_digits_still_rejected():
    # These hit the original re.search(r"\d") gate — message must stay "contains digits"
    for text in ["30%", "賺了 30%", "報酬 ３０", "３０％", "loss of 0"]:
        assert re.search(r"\d", text), text  # confirm the fixture actually has a digit
        try:
            cr.validate_narrative(_narr(mirror=text))
        except cr.RenderError as exc:
            assert "contains digits" in str(exc), f"expected digit message for {text!r}: {exc}"
        else:
            raise AssertionError(f"validate_narrative accepted digits: {text!r}")


# ─────────────── C. English number-word quantities are rejected ────────────────

EN_CLAIMS = [
    "thirty percent", "two times", "five thousand dollars", "twenty five",
    "one hundred shares", "made thousands", "fifty percentage points",
    "THIRTY PERCENT", "up two hundred percent",
]


def test_english_number_word_quantities_flagged():
    for text in EN_CLAIMS:
        assert cr.numeric_claim(text) is not None, f"should flag English claim: {text!r}"


def test_english_number_word_quantities_rejected_by_validate():
    for text in EN_CLAIMS:
        try:
            cr.validate_narrative(_narr(counterfactual=text))
        except cr.RenderError as exc:
            assert "numeric claim" in str(exc), f"unexpected message for {text!r}: {exc}"
        else:
            raise AssertionError(f"validate_narrative accepted English claim: {text!r}")


# ─────────────── D. idioms and legitimate prose are NOT flagged ────────────────

# Idioms that reuse a numeral character without asserting a quantity.
IDIOMS_OK = [
    "一起面對這個決策", "你一直守住紀律", "放下一些執念", "跟上次一樣的模式",
    "十分關鍵的一步", "萬一行情反轉", "千萬別追高", "我百分之百確定這是情緒",
    "唯一該問的問題", "先想清楚再一次進場", "這是第一次的觀察",
    # numerals reused inside ordinary words, must not fire
    "數字只來自引擎", "數據不會說謊", "幾乎沒有懸念", "多數時候你是對的",
    "把缺值當成零", "零成本的錯覺", "這個動作只是修補成本", "變成可被推翻的判斷",
    "達成共識", "成本控制", "兩者之間的取捨", "十字路口上的抉擇",
]

# English prose that reuses risky tokens without a quantity.
EN_OK = [
    "treated as zero instead of guessed", "think twice before adding",
    "at one point you sold early", "double down was the temptation",
    "the next review will test this", "a stronger thesis without new facts",
    "half-hearted conviction is the risk", "one of your positions drifted",
]


def test_idioms_and_prose_not_flagged():
    for text in IDIOMS_OK + EN_OK:
        assert cr.numeric_claim(text) is None, f"false positive on legit prose: {text!r}"


def test_idioms_pass_validate_narrative():
    for text in IDIOMS_OK + EN_OK:
        cr.validate_narrative(_narr(mirror=text))  # must not raise


# ─────────────── D2. existing v2 narrative fixtures are not false-positived ────
# These are the exact strings authored in tests/test_review_v2.py (_narrative /
# _snapshot_narrative).  Any change that starts flagging them is a regression.

V2_FIXTURE_STRINGS = [
    "你守住了其他部位的上限。",
    "This opening check cannot score transaction history yet.",
    "Currency facts remain separate unless reliable conversion is available.",
    "Unclassified positions can make concentration look safer than it is.",
    "Missing fund metadata remains unknown instead of being filled with zero.",
    "An opening structure baseline",
    "The supplied positions show structure without proving past behavior.",
    "先建立組合結構基線",
    "現有持倉能看結構，不能證明過去行為。",
    "這次只建立持倉結構，交易歷史仍維持未判定。",
    "缺少可靠換算時，各幣別事實保持分開。",
    "尚未分類的持倉可能讓集中風險看起來偏低。",
    "基金資料缺值維持未知，不用零補齊。",
    "這項快照限制保持明示。",
    "The available snapshot leaves this limitation explicit.",
    "A lower price is not automatically a stronger thesis",
    "The add only becomes deliberate when the reason can survive the next review.",
    "Without a new fact, the action would have been cost-basis repair.",
    "This rule turns conviction into something falsifiable.",
    "The allocation ETF is missing expense-ratio data, and the gap was disclosed instead of treated as zero.",
    "價格變低，不等於 thesis 自動變強",
    "這次加碼只有在理由能被下次復盤驗證時，才算有意識的決策。",
    "如果沒有新事實，這個動作就只是修補成本。",
    "這條規矩把信心變成可被推翻的判斷。",
    "配置型 ETF 缺費用率資料，這裡把缺口講明，而不是把缺值當成零。",
]


def test_existing_v2_fixtures_not_false_positived():
    for text in V2_FIXTURE_STRINGS:
        assert cr.numeric_claim(text) is None, f"regression — flags a live v2 fixture: {text!r}"


# ─────────────── E. soft-unit boundary + numeral is load-bearing ───────────────

def test_soft_unit_requires_numeral_at_word_boundary():
    # numeral + soft unit at a boundary → flagged
    assert cr.numeric_claim("三成") is not None
    assert cr.numeric_claim("五股") is not None
    # soft-unit char heading a compound word (no numeral in front) → clean
    for word in ["成本", "達成", "成長", "個人", "天氣", "年度", "次數", "股東", "點滴"]:
        assert cr.numeric_claim(word) is None, f"soft unit as word-head must be clean: {word!r}"
    # numeral + soft unit glued into a compound word (零成本) → clean
    assert cr.numeric_claim("零成本") is None
    # the numeral is what triggers: same unit without a numeral in front stays clean
    assert cr.numeric_claim("成") is None and cr.numeric_claim("倍感壓力") is None


# ─────────────── F. schema/code single source of truth ─────────────────────────

def test_schema_dropped_divergent_pattern_and_points_at_code():
    raw = open(SCHEMA, encoding="utf-8").read()
    # The divergence source was the per-field ECMA "^\D+$"; no regex "pattern"
    # key may remain, or schema and code could disagree again.  (The prose in
    # $comment may still discuss patterns, so match the quoted JSON key.)
    assert '"pattern"' not in raw, "narrative schema still declares a regex pattern (divergence source)"
    schema = json.loads(raw)
    for name in ("headline", "mirror", "counterfactual", "rule_rationale", "strength", "synthesis"):
        assert "pattern" not in schema["properties"][name], f"{name} still carries a digit pattern"
    assert "pattern" not in schema["properties"]["honesty"]["additionalProperties"]
    # The schema now names the authoritative gate.
    assert "validate_narrative" in schema.get("$comment", ""), "schema must document the code gate"
    # Contract-pin invariants other suites rely on stay intact.
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["required"] == ["headline", "mirror"]
    assert schema["additionalProperties"] is False


# ─────────────── G. #345 closing synthesis: same gate, still optional ──────────

def test_synthesis_is_declared_optional_in_schema_and_code():
    """#345: narrative.synthesis is a new field (not an extension of mirror or
    strength), but it must not become a second required field — the closing
    block is optional and degrades cleanly when absent."""
    assert "synthesis" in cr.ALLOWED_NARRATIVE
    schema = json.loads(open(SCHEMA, encoding="utf-8").read())
    assert "synthesis" in schema["properties"]
    assert schema["properties"]["synthesis"]["type"] == "string"
    assert schema["properties"]["synthesis"]["minLength"] == 1
    assert "synthesis" not in schema["required"], \
        "the closing synthesis must stay optional, unlike headline/mirror"
    # A legal minimal narrative (no synthesis at all) must still validate.
    cr.validate_narrative(_narr())


def test_synthesis_field_rejects_cjk_and_english_quantity_claims():
    """The digit-ban gate is generic over every narrative key (validate_narrative
    loops all of ``narrative`` except ``honesty``), so a brand-new field gets
    the same enforcement automatically — pin that explicitly for #345 rather
    than relying only on coverage of the older fields."""
    for text in CJK_CLAIMS[:6] + EN_CLAIMS[:6]:
        try:
            cr.validate_narrative(_narr(synthesis=text))
        except cr.RenderError as exc:
            assert "numeric claim" in str(exc), f"unexpected message for {text!r}: {exc}"
        else:
            raise AssertionError(f"validate_narrative accepted a quantity claim in synthesis: {text!r}")


def test_synthesis_field_rejects_ascii_and_fullwidth_digits():
    for text in ["30%", "concentration at 30%", "報酬 ３０"]:
        try:
            cr.validate_narrative(_narr(synthesis=text))
        except cr.RenderError as exc:
            assert "contains digits" in str(exc), f"unexpected message for {text!r}: {exc}"
        else:
            raise AssertionError(f"validate_narrative accepted digits in synthesis: {text!r}")


def test_synthesis_field_accepts_clean_qualitative_prose():
    """A legitimate closing synthesis — qualitative, digit-free, no spelled-out
    quantity — must validate without raising."""
    clean = ("Concentration is what defined this period, and it is still the "
             "single biggest swing factor going forward, not a footnote to it.")
    cr.validate_narrative(_narr(synthesis=clean))
    clean_zh = "這期的處境由集中度主導，往前看它仍是最大的擺動因子，不是附註。"
    cr.validate_narrative(_narr(synthesis=clean_zh))


def test_code_gate_catches_what_the_old_ascii_pattern_missed():
    # Full-width ３０ passed the old ECMA "^\D+$" (ASCII-only) but the engine's
    # Unicode \d rejects it — the exact divergence #194 called out.  With the
    # schema pattern removed, the code is the sole, consistent gate.
    old_ascii_pattern = re.compile(r"^[^0-9]+$")  # what ECMA \D meant in the schema
    assert old_ascii_pattern.match("報酬 ３０"), "premise: ASCII-only pattern would have accepted full-width"
    try:
        cr.validate_narrative(_narr(mirror="報酬 ３０"))
    except cr.RenderError as exc:
        assert "contains digits" in str(exc)
    else:
        raise AssertionError("code gate failed to reject full-width digits")


# ─────────────────────────── runner ───────────────────────────

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _main() else 0)
