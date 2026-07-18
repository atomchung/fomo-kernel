#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic persona answer-policies for the #60 experience harness.

Each policy is one *simulated user*.  Given a Review Plan question row emitted
by ``engine/review.py prepare`` (kinds ``add_thesis`` / ``revisit`` /
``due_revisit`` / ``rule_breach`` / ``headline_motive``) it returns a
schema-valid answer *in that persona's voice*.  Policies are plain code —
lookup tables and small methods, never an LLM.  Determinism is the whole
point: the same Review Plan always yields byte-identical answers, so the
experience the driver renders is reproducible and diffable.

What this layer deliberately does NOT do (documented ceiling, issue #159
layer (c) and #120): it does not judge tone, empathy, or whether the prose
"reads well".  That is the non-deterministic LLM-judge layer, kept opt-in and
out of the offline gate.  A code policy can prove the *mechanics* of listening
(every required question answered, a skip staying skipped, a motive not
re-asked) but cannot prove the *quality* of the wording.

Contract sources this file mirrors (it does not re-invent them):
- answer choices: ``review._add_options`` / ``_exit_options`` / ``_due_options``
  / ``_rule_breach_options`` / ``_generic_options``.
- note/evidence gates: ``thesis.build_decision_events`` (new_evidence needs
  claim+source; planned_tranche/valuation_change need a note),
  ``review._build_exit_narratives`` (exit ``other`` needs a note),
  ``review._build_rule_breach_decisions`` (revise_rule/exception need a note).
- thesis updates: ``thesis.validate_thesis_updates`` + horizon ids from the
  plan's ``card_plan.horizon_ids``.
- narrative: ``card_renderer.validate_narrative`` — headline+mirror required,
  every string free of ASCII/Unicode digits and CJK word-form numeric claims,
  ``honesty`` keys drawn from the plan's ``required_honesty_keys``.

The weekly-window fixtures each persona carries (its "CSV split into weeks")
live in :mod:`windows`; a persona binds a policy voice to those windows.
"""


class PolicyError(ValueError):
    """A question kind or shape the policy layer does not know how to answer."""


def _answer(question, choice, note=None, evidence=None):
    row = {"question_id": question["id"], "choice": choice}
    if note is not None:
        row["note"] = note
    if evidence is not None:
        row["evidence_delta"] = evidence
    return row


class AnswerPolicy:
    """One deterministic simulated user.

    Subclasses provide the voice through small tables and the per-kind
    ``_answer_*`` handlers; this base assembles the plan-level payload the
    runtime agent must also produce: answer every queued question, author a
    thesis update for every missing position, write a narrative carrying
    exactly the required honesty keys, and choose or skip the single final
    commitment.
    """

    name = "base"
    language = "en"
    observations = ()
    narrative_fields = {}
    honesty_fallback = "This limitation is disclosed as-is."
    honesty_lines = {}

    # ── per-question answers ────────────────────────────────────────────────
    def answer(self, question):
        handler = getattr(self, f"_answer_{question.get('kind')}", None)
        if handler is None:
            raise PolicyError(f"{self.name}: unsupported question kind {question.get('kind')!r}")
        return handler(question)

    # ── plan-level assembly ─────────────────────────────────────────────────
    def build_answers(self, plan, week_index):
        answers = [self.answer(q) for q in plan.get("question_queue") or []]
        updates = [self.thesis_update(row)
                   for row in plan.get("missing_thesis_positions") or []]
        payload = {
            "session_id": plan["session_id"],
            "answers": answers,
            "thesis_updates": updates,
            "observations": list(self.observations),
            "commitment": self.commitment(plan, week_index),
        }
        return payload

    def build_narrative(self, plan):
        required = (plan.get("card_plan") or {}).get("required_honesty_keys") or []
        narrative = dict(self.narrative_fields)
        narrative["honesty"] = {
            key: self.honesty_lines.get(key, self.honesty_fallback) for key in required
        }
        return narrative

    # ── subclass responsibilities ───────────────────────────────────────────
    def commitment(self, plan, week_index):
        raise NotImplementedError

    def thesis_update(self, missing_row):
        raise NotImplementedError


class SteadyConviction(AnswerPolicy):
    """zh-TW conviction holder.

    Explains every add with a falsifiable claim and source, stands by past
    exit reasons, keeps tracking a breached rule honestly, and commits to one
    rule in the first week then holds it.  The distinctive, digit-free thesis
    ``why`` is what the week-2 question stem must quote back (the #226 memory
    weave), so it is a stable constant here.
    """

    name = "steady_conviction"
    language = "zh-TW"
    observations = ("每個持倉都要有一個能被下次復盤推翻的理由",)

    # A single stable, digit-free thesis sentence. Week 2 replays this verbatim
    # inside the add question stem, so the harness can prove memory actually
    # reached the user. Keep it free of numbers (digit-ban) and recognisable.
    THESIS_WHY = "企業客戶的續約與資料合約仍在持續擴大"

    narrative_fields = {
        "headline": "價格變低，不等於理由變強",
        "mirror": "這次的加碼與出場，只有在理由能被下次復盤驗證時，才算有意識的決策。",
        "counterfactual": "如果沒有新的事實，攤低成本就只是修補帳面，不是判斷。",
        "rule_rationale": "這條規矩把持有的信心，變成一個可以被推翻的判斷。",
    }
    honesty_fallback = "這項限制照實揭露，不用猜測補上。"
    honesty_lines = {
        "alpha_credibility": "樣本還太短，技巧與運氣分不開，先不下結論。",
        "unrealized_coverage": "沒有可靠的即時價格，未實現損益按成本基礎保守呈現。",
        "sector_attribution": "部分持倉的產業歸類不完整，集中度可能被低估。",
        "unclassified_drivers": "尚未分類的持倉可能讓集中風險看起來比實際安全。",
        "orphan_sells": "有賣出找不到對應的買入紀錄，已實現數字只涵蓋對得上的部分。",
        "currency_mix": "缺少可靠匯率時，各幣別的數字保持分開，不硬換算。",
        "cash_reliability": "現金餘額缺少可靠錨點，帳戶層的數字先不採信。",
        "acct_perf_basis": "帳戶級報酬的基礎不完整，先看持倉層的數字。",
        "etf_metadata": "配置型基金有缺值，缺的部分維持未知，不用零補齊。",
    }

    def _answer_add_thesis(self, question):
        ticker = question.get("ticker") or "這檔"
        return _answer(question, "new_evidence", evidence={
            "claim": f"{ticker} 的企業需求仍在擴大",
            "source": "季度法說會",
            "falsifier": "續約與客戶數同時轉弱",
        })

    def _answer_revisit(self, question):
        return _answer(question, "price_target",
                       note="到了原先設定的減碼價位，先收回一部分成果")

    def _answer_due_revisit(self, question):
        return _answer(question, "still_valid",
                       note="回頭看，當時的賣出理由仍然成立")

    def _answer_rule_breach(self, question):
        return _answer(question, "keep_tracking")

    def _answer_headline_motive(self, question):
        return _answer(question, "deliberate_plan")

    def commitment(self, plan, week_index):
        candidates = (plan.get("card_plan") or {}).get("candidate_rules") or []
        if week_index == 0 and candidates:
            return {"choice": candidates[0]["id"]}
        return {"choice": "skip"}

    def thesis_update(self, missing_row):
        ticker = missing_row.get("ticker")
        return {
            "ticker": ticker,
            "cycle_id": missing_row.get("cycle_id"),
            "why": self.THESIS_WHY,
            "horizon": "quarters",
            "exit_trigger": "基本面惡化，或原始理由被新事實推翻",
            "stop": None,
            "target_size": "bounded",
            "driver": "企業軟體",
            "maturity": "inferred",
        }


class AnxiousSkipper(AnswerPolicy):
    """en anxious skipper.

    Never names an exit reason, defers every checkpoint verdict, blames the
    price for adds, and refuses to commit to a rule.  The point of this
    persona is negative: a skipped exit must not come back, and a refused
    commitment must leave the card without a chosen rule.
    """

    name = "anxious_skipper"
    language = "en"
    observations = ("The reason for each sale went unwritten while the position still moved",)

    narrative_fields = {
        "headline": "Selling fast feels safe, but the reasons stay unexamined",
        "mirror": "Every exit happened before its reason was written down, so next week the same doubt returns.",
        "counterfactual": "Without a recorded reason, each sale reads as relief rather than a decision.",
    }
    honesty_fallback = "This limitation is disclosed as-is instead of being smoothed over."
    honesty_lines = {
        "alpha_credibility": "The sample stays too short to separate skill from luck, so no verdict is drawn.",
        "unrealized_coverage": "Without reliable live prices, unrealized results stay on a conservative cost basis.",
        "sector_attribution": "Sector labels are incomplete, so concentration may look safer than it is.",
        "unclassified_drivers": "Unclassified positions can make concentration look safer than it is.",
        "orphan_sells": "Some sells have no matching buy history, so realized figures cover only matched lots.",
        "currency_mix": "Without reliable conversion, per-currency facts stay separate.",
        "cash_reliability": "Cash lacks a reliable anchor, so account-level figures stay ungated.",
        "acct_perf_basis": "The account-return basis is incomplete, so holding-level numbers lead.",
        "etf_metadata": "The allocation fund is missing metadata; the gap stays explicit instead of a filled-in zero.",
    }

    def _answer_add_thesis(self, question):
        return _answer(question, "price_only")

    def _answer_revisit(self, question):
        return _answer(question, "skip")

    def _answer_due_revisit(self, question):
        return _answer(question, "skip")

    def _answer_rule_breach(self, question):
        return _answer(question, "keep_tracking")

    def _answer_headline_motive(self, question):
        return _answer(question, "emotional_reaction")

    def commitment(self, plan, week_index):
        return {"choice": "skip"}

    def thesis_update(self, missing_row):
        ticker = missing_row.get("ticker")
        return {
            "ticker": ticker,
            "cycle_id": missing_row.get("cycle_id"),
            "why": f"{ticker} keeps moving and holding it feels safer than explaining it",
            "horizon": "weeks",
            "exit_trigger": "The drawdown becomes uncomfortable to watch",
            "stop": None,
            "target_size": "bounded",
            "driver": "momentum",
            "maturity": "inferred",
        }


POLICIES = {policy.name: policy for policy in (SteadyConviction(), AnxiousSkipper())}
