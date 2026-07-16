#!/usr/bin/env python3
"""Skill v2 orchestration / ETF / recovery tests (offline, standard library only)."""
import hashlib
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "skills" / "fomo-kernel" / "engine"
REVIEW = ENGINE_DIR / "review.py"
SCHEMAS = ROOT / "skills" / "fomo-kernel" / "schemas"
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ROOT / "tests" / "agent"))
import instruments  # noqa: E402
import review as review_engine  # noqa: E402
import thesis as thesis_engine  # noqa: E402
import trade_recap as tr  # noqa: E402
from check_card import check_card  # noqa: E402


def _artifacts(tmp):
    state = {
        "schema_version": 2,
        "date_start": "2026-01-01", "date_end": "2026-07-14",
        "n_trades": 8, "n_round_trips": 3, "n_held": 1,
        "headline_dim": "加碼攤平",
        "headline_metric": {"key": "avgdown_count", "value": 3},
        "commitment": None,
        "metrics": {
            "max_pos_pct": 0.42, "max_pos_ticker": "PLTR", "avgdown_count": 3,
            "avgdown_breach": 1, "payoff": 1.4, "ai_pct": 0.42,
            "max_sector_pct": 0.42, "top3_pct": 0.42, "n_holdings": 2,
            "exit_severity": 0.2, "hold_severity": 0.1,
            "beta": None, "alpha_ann": None, "alpha_t": None, "alpha_credible": None,
        },
        "rule": None, "insufficient_data": False,
        "holdings": {"as_of": "2026-07-14", "derived_from": "trades_csv", "is_complete": False,
                     "positions": {"PLTR": {"shares": 10, "cost": 1000, "avg_cost": 100,
                                                "cycle_start": "2026-01-01",
                                                "cycle_id": "PLTR#2026-01-01#1",
                                                "add_count": 3,
                                                "decision_cursor": "PLTR#2026-01-01#1#add#3"}}},
        "currency_meta": {"aggregate_currency": "USD", "mixed": False},
        "portfolio_structure": {"schema_version": 1, "allocation_weight": 0.58,
                                "concentrated_etf_weight": 0, "allocation_etfs": [
                                    {"ticker": "SPY", "kind": "broad_market_etf", "weight": 0.58}],
                                "concentrated_etfs": [],
                                "metadata_gaps": [{"ticker": "SPY", "fields": ["expense_ratio"]}]},
        "cash": None,
        "problem_events": [{"key": "avgdown_breach", "kind": "event", "week": "2026-07-14",
                            "ticker": "PLTR", "amount": 1, "note": "test"}],
        "problem_opportunities": {"avgdown_breach": True},
    }
    hole = {"dim": "加碼攤平", "severity": 0.8, "tier_weight": 1.0,
            "number_line": "你有 3 次在虧損倉往下加碼，其中 1 次加到 >25%",
            "lens_rule": "往下加碼前先寫新證據。", "lens_quote": "先驗證再加碼。",
            "raw": {"dim": "加碼攤平", "tier": 1, "triggered": True, "severity": 0.8,
                    "count": 3, "breach": 1, "tickers": ["PLTR"]}}
    card = {
        "schema_version": 1, "philosophy": "test lens",
        "strength": "你守住了其他部位的上限。",
        "overview": {"total_pnl": -300, "realized": 200, "unrealized": -500,
                     "payoff": 1.4, "avg_win": 140, "avg_loss": -100},
        "best_trade": {"ticker": "NVDA", "ret": 0.2, "pnl": 200},
        "worst_trade": {"ticker": "AMD", "ret": -0.1, "pnl": -100}, "what_if": None,
        "ticker_diagnosis": [],
        "thesis_questions": [{"ticker": "PLTR", "question": "PLTR 加碼時有新證據，還是只想攤低成本？"}],
        "top_holes": [hole],
        "candidate_rules": [{"dim": "加碼攤平", "rule": "往下加碼前先寫新證據。"}],
        "prescriptions": [], "alpha_beta_breakdown": {}, "payoff_attribution": {},
        "dims_raw": [hole["raw"]], "data_integrity": {},
        "currency_meta": {"aggregate_currency": "USD"}, "cash": None, "acct_perf": {"note": "offline"},
        "portfolio_structure": state["portfolio_structure"],
        "honesty_ledger": [{"key": "etf_metadata", "status": "partial", "data": {}}],
        "pnl_curve": {"note": "offline"},
    }
    card_path = pathlib.Path(tmp) / "card.json"
    state_path = pathlib.Path(tmp) / "state.json"
    card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return card_path, state_path


def _run(*args):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


def _prepare(tmp, root, language="zh-TW"):
    card, state = _artifacts(tmp)
    run = _run("prepare", "--root", root, "--language", language,
               "--card-json", card, "--state-json", state)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)["review_plan"]


def _trade_csv(tmp, future=False):
    path = pathlib.Path(tmp) / ("future.csv" if future else "exits.csv")
    rows = ["Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency"]
    if future:
        rows.append("BIG,BUY,10,100,2099-01-01,Trade,US,USD")
    else:
        rows.extend([
            "OLD,BUY,1,100,2025-01-01,Trade,US,USD",
            "OLD,SELL,1,110,2025-02-01,Trade,US,USD",
            "BIG,BUY,10,100,2026-07-01,Trade,US,USD",
            "MID,BUY,10,100,2026-07-02,Trade,US,USD",
            "SMALL,BUY,2,100,2026-07-03,Trade,US,USD",
            "BIG,SELL,10,200,2026-07-10,Trade,US,USD",
            "MID,SELL,6,150,2026-07-11,Trade,US,USD",
            "SMALL,SELL,2,200,2026-07-12,Trade,US,USD",
        ])
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _prepare_with_trades(tmp, root, language="zh-TW", nonce=""):
    card, state = _artifacts(tmp)
    csv_path = _trade_csv(tmp)
    args = ["prepare", csv_path, "--root", root, "--language", language,
            "--card-json", card, "--state-json", state]
    if nonce:
        args.extend(["--session-nonce", nonce])
    run = _run(*args)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)["review_plan"], csv_path, card, state


def _exit_answers(plan, commitment=None):
    out = _answers(plan, commitment=commitment)
    answers = []
    for question in plan["question_queue"]:
        if question["kind"] == "add_thesis":
            answers.append({"question_id": question["id"], "choice": "new_evidence",
                            "evidence_delta": {"claim": "Enterprise demand accelerated",
                                               "source": "earnings call",
                                               "falsifier": "renewals weaken"}})
        elif question["kind"] == "revisit" and question["ticker"] == "BIG":
            answers.append({"question_id": question["id"], "choice": "other",
                            "note": "Risk limit for BIG before 2026-08-01"})
        elif question["kind"] == "revisit":
            answers.append({"question_id": question["id"], "choice": "skip"})
        else:
            answers.append({"question_id": question["id"], "choice": "deliberate_plan"})
    out["answers"] = answers
    return out


def _answers(plan, evidence=True, commitment=None):
    answer = {"question_id": plan["question_queue"][0]["id"], "choice": "new_evidence"}
    if evidence:
        answer["evidence_delta"] = {"claim": "Enterprise demand accelerated", "source": "earnings call",
                                    "falsifier": "renewals weaken"}
    out = {
        "session_id": plan["session_id"], "answers": [answer],
        "thesis_updates": [{"ticker": "PLTR", "cycle_id": "PLTR#2026-01-01#1",
                            "why": "Enterprise adoption may still be underpriced",
                            "horizon": "季", "exit_trigger": "Renewals weaken",
                            "stop": None, "target_size": "bounded", "driver": "AI software",
                            "maturity": "inferred"}],
        "observations": ["Agent interpretation remains separate from engine facts"],
    }
    if commitment is not None:
        out["commitment"] = {"choice": commitment}
    return out


def _narrative(language="zh-TW"):
    if language == "en":
        return {"headline": "A lower price is not automatically a stronger thesis",
                "mirror": "The add only becomes deliberate when the reason can survive the next review.",
                "counterfactual": "Without a new fact, the action would have been cost-basis repair.",
                "rule_rationale": "This rule turns conviction into something falsifiable.",
                "honesty": {"etf_metadata": "The allocation ETF is missing expense-ratio data, "
                                            "and the gap was disclosed instead of treated as zero."}}
    return {"headline": "價格變低，不等於 thesis 自動變強",
            "mirror": "這次加碼只有在理由能被下次復盤驗證時，才算有意識的決策。",
            "counterfactual": "如果沒有新事實，這個動作就只是修補成本。",
            "rule_rationale": "這條規矩把信心變成可被推翻的判斷。",
            "honesty": {"etf_metadata": "配置型 ETF 缺費用率資料，這裡把缺口講明，而不是把缺值當成零。"}}


def test_etf_allocation_exemption_and_focused_etf_risk():
    instruments.reset_map()
    broad = tr.dim_size([], {"SPY": (80, 8000), "PLTR": (20, 2000)}, None)
    assert broad["max_ticker"] == "PLTR" and abs(broad["max_pct"] - 0.2) < 1e-9
    assert broad["triggered"] is False and broad["allocation_etfs"] == {"SPY": 0.8}
    focused = tr.dim_size([], {"QQQ": (80, 8000), "PLTR": (20, 2000)}, None)
    assert focused["max_ticker"] == "QQQ" and focused["triggered"] is True
    div = tr.dim_diversify({"SPY": (80, 8000), "PLTR": (20, 2000)}, None)
    assert abs(div["top3"] - 0.2) < 1e-9, "allocation ETF must not inflate risk top-three"
    assert tr.what_if({"SPY": (80, 8000), "PLTR": (20, 2000)}, {"SPY": 100, "PLTR": 100}) is None, \
        "allocation ETF must not become the single-risk drawdown scenario"


def test_etf_allocation_exemption_covers_avgdown_and_problem_events():
    import datetime as dt
    instruments.reset_map()
    events = [{"ticker": "SPY", "weight_then": 0.6, "date": dt.date(2026, 7, 1), "px": 500.0},
              {"ticker": "PLTR", "weight_then": 0.3, "date": dt.date(2026, 7, 2), "px": 100.0}]
    d = tr.dim_avgdown(events, {}, {}, None)
    assert d["breach"] == 1 and d["count"] == 1 and d["tickers"] == ["PLTR"], \
        "an allocation-ETF DCA below cost is not single-name averaging down"
    assert d["allocation_exempt_tickers"] == ["SPY"]
    problem, _opps = tr.build_problem_events([], [], events, {}, {}, "2026-07-14")
    breaches = [e for e in problem if e["key"] == "avgdown_breach"]
    assert [e["ticker"] for e in breaches] == ["PLTR"], \
        "problem ledger must apply the same allocation-ETF exemption as dim_avgdown"


def test_unknown_instrument_never_gets_etf_exemption():
    instruments.reset_map()
    unknown = instruments.info("NOTAREALETF")
    assert unknown["kind"] == "equity" and unknown["allocation_exempt"] is False


def test_instrument_map_and_metadata_gaps_are_explicit():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "map.json"
        path.write_text(json.dumps({"CUSTOM": {"kind": "regional_etf", "expense_ratio": 0.002}}),
                        encoding="utf-8")
        instruments.reset_map()
        assert instruments.load_map(path)["loaded"] == 1
        analysis = instruments.portfolio_analysis({"CUSTOM": 1.0})
        assert analysis["allocation_weight"] == 1.0
        assert analysis["metadata_gaps"] == [{"ticker": "CUSTOM", "fields": ["tracking_error"]}]
    instruments.reset_map()


def test_prepare_is_resumable_without_rerunning_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root)
        resumed = _run("resume", "--root", root, "--session-id", plan["session_id"])
        assert resumed.returncode == 0 and json.loads(resumed.stdout)["plan"]["session_id"] == plan["session_id"]
        card, state = _artifacts(tmp)
        again = _run("prepare", "--root", root, "--card-json", card, "--state-json", state)
        assert json.loads(again.stdout)["status"] == "resumed"


def test_session_nonce_starts_a_distinct_session():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        first = json.loads(_run("prepare", "--root", root, "--card-json", card, "--state-json", state,
                                "--session-nonce", "alpha").stdout)
        second = json.loads(_run("prepare", "--root", root, "--card-json", card, "--state-json", state,
                                 "--session-nonce", "beta").stdout)
        assert first["status"] == "prepared" and second["status"] == "prepared", \
            "an explicit nonce must not be swallowed by same-content pending resume"
        assert first["session_id"] != second["session_id"]


def test_test_drive_is_labeled_and_never_projects_into_coach_memory():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "demo-root"
        card, state = _artifacts(tmp)
        prepared = _run("prepare", "--test-drive", "--root", root,
                        "--card-json", card, "--state-json", state)
        plan = json.loads(prepared.stdout)["review_plan"]
        assert plan["route"] == "test_drive" and plan["persist"] is False
        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(_answers(plan, commitment="candidate_0")), encoding="utf-8")
        narrative.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        result = json.loads(final.stdout)
        private = pathlib.Path(result["private_card"]).read_text(encoding="utf-8")
        public = pathlib.Path(result["public_card"]).read_text(encoding="utf-8")
        assert "示範資料／演練" in private and "示範資料／演練" in public
        assert not (root / "log.jsonl").exists() and not (root / "theses.jsonl").exists()

        (root / "sessions" / "0000-00-00__corrupt").mkdir(parents=True)  # bundle-less dir must not abort repair
        repaired = _run("repair-projections", "--root", root)
        outcome = json.loads(repaired.stdout)
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr
        assert outcome["skipped"] and outcome["skipped"][0]["session_id"] == plan["session_id"]
        assert outcome["errors"] and "0000-00-00__corrupt" in outcome["errors"][0]["session_id"]
        assert not (root / "log.jsonl").exists() and not (root / "last_state.json").exists(), \
            "repair-projections must never project demo sessions into coach memory"


def test_preview_rejects_new_evidence_without_delta_and_narrative_numbers():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root)
        answers_path = pathlib.Path(tmp) / "answers.json"
        narrative_path = pathlib.Path(tmp) / "narrative.json"
        answers_path.write_text(json.dumps(_answers(plan, evidence=False)), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        bad = _run("preview", "--root", root, "--session-id", plan["session_id"],
                   "--answers", answers_path, "--narrative", narrative_path)
        assert bad.returncode == 2 and "requires evidence_delta" in json.loads(bad.stdout)["error"]
        answers_path.write_text(json.dumps(_answers(plan), ensure_ascii=False), encoding="utf-8")
        narrative = _narrative(); narrative["mirror"] += " 42"
        narrative_path.write_text(json.dumps(narrative, ensure_ascii=False), encoding="utf-8")
        bad_number = _run("preview", "--root", root, "--session-id", plan["session_id"],
                          "--answers", answers_path, "--narrative", narrative_path)
        assert bad_number.returncode == 2 and "contains digits" in json.loads(bad_number.stdout)["error"]
        narrative = _narrative(); del narrative["honesty"]      # #82 gate: every triggered key needs a sentence
        narrative_path.write_text(json.dumps(narrative, ensure_ascii=False), encoding="utf-8")
        bad_missing = _run("preview", "--root", root, "--session-id", plan["session_id"],
                           "--answers", answers_path, "--narrative", narrative_path)
        assert bad_missing.returncode == 2 and "missing required keys: etf_metadata" in json.loads(bad_missing.stdout)["error"]
        narrative = _narrative(); narrative["honesty"]["alpha_credibility"] = "not triggered by this card"
        narrative_path.write_text(json.dumps(narrative, ensure_ascii=False), encoding="utf-8")
        bad_extra = _run("preview", "--root", root, "--session-id", plan["session_id"],
                         "--answers", answers_path, "--narrative", narrative_path)
        assert bad_extra.returncode == 2 and "did not trigger" in json.loads(bad_extra.stdout)["error"]


def test_preview_finalize_atomic_bundle_redaction_and_retry():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root)
        answers_path = pathlib.Path(tmp) / "answers.json"
        narrative_path = pathlib.Path(tmp) / "narrative.json"
        answers_path.write_text(json.dumps(_answers(plan), ensure_ascii=False), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers_path, "--narrative", narrative_path)
        payload = json.loads(preview.stdout)
        assert preview.returncode == 0 and payload["status"] == "previewed"
        assert payload["candidate_rules"][0]["id"] == "candidate_0"

        answers_path.write_text(json.dumps(_answers(plan, commitment="candidate_0"), ensure_ascii=False),
                                encoding="utf-8")
        finalized = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                         "--answers", answers_path, "--narrative", narrative_path)
        result = json.loads(finalized.stdout)
        assert finalized.returncode == 0 and result["status"] == "committed" and not result["projection_error"]
        session_dir = pathlib.Path(result["path"])
        expected = {"bundle.json", "state.json", "plan.json", "answers.json", "narrative.json",
                    "card-private.md", "card-public.md", "card-private.html", "manifest.json"}
        assert expected == {p.name for p in session_dir.iterdir()}
        manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))["sha256"]
        for name, digest in manifest.items():
            assert hashlib.sha256((session_dir / name).read_bytes()).hexdigest() == digest
        private = (session_dir / "card-private.md").read_text(encoding="utf-8")
        public = (session_dir / "card-public.md").read_text(encoding="utf-8")
        assert "PLTR" in private and "$-300" in private and "session_id" in private
        assert "已實現盈虧比 1.4" in private and "NVDA 20%" in private and "AMD -10%" in private
        assert "缺費用率資料" in private, "agent-authored honesty sentence must reach the card"
        assert "資料邊界" not in private and "Evidence boundaries" not in private, \
            "#82: honesty is woven into sections, never a standalone checklist section"
        assert all(f.passed for f in check_card(private)), "v2 private renderer must satisfy card iron rules"
        assert "PLTR" not in public and "$" not in public and "2026" not in public and "session_id" not in public
        assert (root / "thesis_decisions.jsonl").exists() and (root / "log.jsonl").exists()
        retry = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers_path, "--narrative", narrative_path)
        assert retry.returncode == 0 and json.loads(retry.stdout)["status"] == "no-op"
        bundle_before = (session_dir / "bundle.json").read_bytes()
        (root / "thesis_decisions.jsonl").unlink()       # simulate a projection interrupted after commit
        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0 and (root / "thesis_decisions.jsonl").exists()
        assert (session_dir / "bundle.json").read_bytes() == bundle_before, \
            "repair must rebuild projections without mutating canonical bundle"


def test_public_card_never_reuses_user_authored_rule_text():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan)
        answers["commitment"] = {"choice": "custom",
                                 "rule": "PLTR above 40% or below $80.50: stop adding before 2026-08-01",
                                 "metric_key": "max_pos_pct", "goal": "down", "dim": "position_sizing"}
        answers_path = pathlib.Path(tmp) / "answers.json"
        narrative_path = pathlib.Path(tmp) / "narrative.json"
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative("en")), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers_path, "--narrative", narrative_path)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        private = pathlib.Path(result["private_card"]).read_text(encoding="utf-8")
        public = pathlib.Path(result["public_card"]).read_text(encoding="utf-8")
        assert "$80.50" in private, "custom rule text belongs on the private card"
        for fragment in ("PLTR", "$80.50", "2026-08-01", "40%"):
            assert fragment not in public, f"custom rule leaked {fragment!r} into the public card"
        assert "One self-authored process rule" in public
        assert not re.search(r"[一-鿿]", public), "en public card must not mix CJK labels"


def test_recent_exit_capture_is_ranked_bounded_canonical_and_private_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, csv_path, card_path, state_path = _prepare_with_trades(tmp, root)
        assert plan["card_plan"]["question_limit"] == 3
        assert len(plan["question_queue"]) == 3, "one review asks at most three important questions"
        assert [(q["kind"], q.get("ticker")) for q in plan["question_queue"]] == [
            ("revisit", "BIG"), ("add_thesis", "PLTR"), ("revisit", "MID")], \
            "exit notional and position impact must deterministically select the top three"
        big, _add, mid = plan["question_queue"]
        assert big["exit_notional"] == 2000 and mid["exit_notional"] == 900
        assert mid["exit_kind"] == "reduce" and "大幅減倉" in mid["question"]
        assert "SMALL" not in {q.get("ticker") for q in plan["question_queue"]}, \
            "lower-impact exits remain queued for a later review inside the freshness window"
        assert "OLD" not in {q.get("ticker") for q in plan["question_queue"]}, \
            "historical exits must not flood a cold-start review"
        ledger_rows = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert len(ledger_rows) == 8 and not (root / "theses.jsonl").exists(), \
            "validated trade facts persist at prepare, but answers do not project before finalize"

        resumed = _run("resume", "--root", root, "--session-id", plan["session_id"])
        resumed_plan = json.loads(resumed.stdout)["plan"]
        assert resumed_plan["question_queue"] == plan["question_queue"], \
            "resume returns the exact same ranked questions without re-ingesting"
        assert len((root / "ledger.jsonl").read_text().splitlines()) == 8

        answers_path = pathlib.Path(tmp) / "exit-answers.json"
        narrative_path = pathlib.Path(tmp) / "exit-narrative.json"
        answers_path.write_text(json.dumps(_exit_answers(plan, commitment="candidate_0"), ensure_ascii=False),
                                encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers_path, "--narrative", narrative_path)
        assert preview.returncode == 0, preview.stdout + preview.stderr
        preview_payload = json.loads(preview.stdout)
        assert "復盤卡，只留在本機" in preview_payload["private_card"]
        assert "Risk limit for BIG before 2026-08-01" in preview_payload["private_card"]
        assert "MID：你把" not in preview_payload["private_card"], "skipped answers stay off the card"
        for private_fragment in ("BIG", "Risk limit", "2026-08-01"):
            assert private_fragment not in preview_payload["public_card"]

        finalized = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                         "--answers", answers_path, "--narrative", narrative_path)
        result = json.loads(finalized.stdout)
        assert finalized.returncode == 0 and not result["projection_error"], finalized.stdout + finalized.stderr
        bundle_path = pathlib.Path(result["path"]) / "bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        exits = bundle["exit_narratives"]
        assert [(e["ticker"], e["capture"]) for e in exits] == [("BIG", "confirmed"), ("MID", "skipped")]
        assert all(e["event_id"].startswith("exit-") for e in exits)
        projected = [json.loads(line) for line in (root / "theses.jsonl").read_text().splitlines()]
        assert {e["ticker"] for e in projected if e.get("event") == "exit_narrative"} == {"BIG", "MID"}

        # Canonical session remains the dedup authority even if its compatibility
        # projection disappeared before repair.
        (root / "theses.jsonl").unlink()
        again = _run("prepare", csv_path, "--root", root, "--card-json", card_path,
                     "--state-json", state_path, "--session-nonce", "next")
        assert again.returncode == 0, again.stdout + again.stderr
        next_plan = json.loads(again.stdout)["review_plan"]
        next_tickers = {q.get("ticker") for q in next_plan["question_queue"]}
        assert "BIG" not in next_tickers and "MID" not in next_tickers, \
            "confirmed and skipped exits must both deduplicate from the canonical bundle"
        assert next_plan["input"]["ledger_ingest"]["appended"] == 0
        assert next_plan["input"]["ledger_ingest"]["skipped_dup"] == 8

        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr
        repaired_rows = [json.loads(line) for line in (root / "theses.jsonl").read_text().splitlines()]
        assert {e["ticker"] for e in repaired_rows if e.get("event") == "exit_narrative"} == {"BIG", "MID"}
        assert json.loads(bundle_path.read_text(encoding="utf-8")) == bundle, \
            "repair must not mutate the canonical session"


def test_exit_capture_validates_before_ledger_write_and_test_drive_never_ingests():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        valid = _trade_csv(tmp)
        future = _trade_csv(tmp, future=True)
        rejected = _run("prepare", valid, future, "--root", root,
                        "--card-json", card, "--state-json", state)
        assert rejected.returncode == 2 and "before writing" in json.loads(rejected.stdout)["error"]
        assert not (root / "ledger.jsonl").exists() and not (root / "revisit.jsonl").exists(), \
            "a later invalid file must reject the whole batch before the earlier valid file is written"

        demo_root = pathlib.Path(tmp) / "demo"
        demo = _run("prepare", valid, "--test-drive", "--root", demo_root,
                    "--card-json", card, "--state-json", state)
        assert demo.returncode == 0, demo.stdout + demo.stderr
        assert json.loads(demo.stdout)["review_plan"]["persist"] is False
        assert not (demo_root / "ledger.jsonl").exists() and not (demo_root / "revisit.jsonl").exists(), \
            "test drive cannot persist real trade facts or exit queues"


def test_ingestion_tolerates_cash_flow_rows_in_the_same_csv():
    """Deposits, dividends, interest, fees, and reinvest notices legitimately share
    the normalized CSV with trades — load_cash_flows() consumes them for the cash
    pillar — so persist-mode prepare must count them, not die on them."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        csv_path = pathlib.Path(tmp) / "with-cash.csv"
        csv_path.write_text("\n".join([
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency,Amount",
            ",,0,0,2026-07-01,Deposit,US,USD,5000",
            "BIG,BUY,10,100,2026-07-01,Trade,US,USD,-1000",
            "KO,REINVEST,1.2,60,2026-07-02,Trade,US,USD,-72",
            "KO,,0,0,2026-07-03,Dividend,US,USD,32",
            ",,0,0,2026-07-05,Interest,US,USD,1.5",
            "BIG,SELL,10,200,2026-07-10,Trade,US,USD,2000",
        ]) + "\n", encoding="utf-8")
        run = _run("prepare", csv_path, "--root", root, "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        ingest = json.loads(run.stdout)["review_plan"]["input"]["ledger_ingest"]
        assert ingest["appended"] == 2 and ingest["skipped_non_trade"] == 4 \
            and ingest["skipped_future_dated"] == 0, ingest
        rows = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert [(r["ticker"], r["action"]) for r in rows] == [("BIG", "buy"), ("BIG", "sell")], \
            "only BUY/SELL trade facts enter the ledger; cash rows stay with the cash pipeline"
        assert any(q.get("ticker") == "BIG" and q["kind"] == "revisit"
                   for q in json.loads(run.stdout)["review_plan"]["question_queue"]), \
            "the exit detected among cash-flow noise still reaches the question queue"

        # The shipped noisy-broker persona exists to pin broker-noise tolerance:
        # its Transfer/Dividend/Interest/Fee/REINVEST rows must never kill prepare.
        fixture = ROOT / "skills" / "fomo-kernel" / "mock" / "sample_noisy_broker.csv"
        fixture_root = pathlib.Path(tmp) / "coach-fixture"
        run2 = _run("prepare", fixture, "--root", fixture_root,
                    "--card-json", card, "--state-json", state)
        assert run2.returncode == 0, run2.stdout + run2.stderr
        ingest2 = json.loads(run2.stdout)["review_plan"]["input"]["ledger_ingest"]
        assert ingest2["skipped_non_trade"] == 6 and ingest2["appended"] > 0, ingest2

        # Keep mixed-market brokerage input on the same persist path too. This
        # fixture carries TWD cash rows and protects against a US-only fix.
        tw_fixture = ROOT / "skills" / "fomo-kernel" / "mock" / "sample_tw_mixed.csv"
        tw_root = pathlib.Path(tmp) / "coach-tw-fixture"
        run3 = _run("prepare", tw_fixture, "--root", tw_root,
                    "--card-json", card, "--state-json", state)
        assert run3.returncode == 0, run3.stdout + run3.stderr
        ingest3 = json.loads(run3.stdout)["review_plan"]["input"]["ledger_ingest"]
        assert ingest3["skipped_non_trade"] == 4 and ingest3["appended"] > 0, ingest3


def test_exit_capture_english_copy_uses_review_card_language():
    item = {"revisit_id": "BIG#2026-07-01#1#2026-07-10#10.0", "ticker": "BIG",
            "cycle_id": "BIG#2026-07-01#1", "exit_date": "2026-07-10",
            "exit_price": 200.0, "shares_sold": 10.0, "shares_before": 10.0,
            "kind": "full", "currency": "USD"}
    question = review_engine._exit_question(item, "en")
    assert "fully exited" in question["question"] and "USD 2,000" in question["question"]
    assert question["options"][0]["label"] == "The target was reached"
    assert review_engine._exit_question({**item, "kind": "reduce"}, "en")["options"][0]["label"] == \
        "The planned reduction point was reached"


def test_exit_question_ranking_uses_engine_fx_for_mixed_currency_amounts():
    card = {"currency_meta": {"mixed": True, "aggregate_currency": "USD",
                              "fx": {"TWD": 1 / 30}}}
    tw = {"exit_price": 1000.0, "shares_sold": 1500.0, "currency": "TWD"}
    us = {"exit_price": 300.0, "shares_sold": 200.0, "currency": "USD"}
    assert review_engine._exit_importance(tw, card) == 50000
    assert review_engine._exit_importance(us, card) == 60000, \
        "raw TWD notional must not outrank a larger aggregate-currency exit"


def test_custom_exit_reason_requires_the_users_words():
    question = review_engine._exit_question(
        {"revisit_id": "A#2026-07-01#1#2026-07-10#1.0", "ticker": "A",
         "cycle_id": "A#2026-07-01#1", "exit_date": "2026-07-10",
         "exit_price": 100.0, "shares_sold": 1.0, "shares_before": 1.0,
         "kind": "full", "currency": "USD"}, "en")
    plan = {"session_id": "session-123", "question_queue": [question],
            "engine_state": {"date_end": "2026-07-14"}}
    answers = {"answers": [{"question_id": question["id"], "choice": "other"}]}
    try:
        review_engine._build_exit_narratives(plan, answers)
        assert False, "other without a note must not create an empty confirmed memory"
    except review_engine.ReviewError as exc:
        assert "requires a short note" in str(exc)


def test_add_decision_cursor_is_per_cycle_and_reopens_only_for_a_new_add():
    rows = [
        {"ticker": "A", "side": "buy", "qty": 1, "price": 10, "date": dt.date(2026, 1, 1)},
        {"ticker": "A", "side": "buy", "qty": 1, "price": 9, "date": dt.date(2026, 1, 2)},
        {"ticker": "B", "side": "buy", "qty": 1, "price": 20, "date": dt.date(2026, 1, 3)},
        {"ticker": "A", "side": "buy", "qty": 1, "price": 8, "date": dt.date(2026, 1, 4)},
    ]
    cursors = tr.current_cycle_add_cursors(rows)
    assert cursors["A"]["decision_cursor"] == "A#2026-01-01#1#add#2"
    assert cursors["B"]["decision_cursor"] is None, \
        "another ticker's entry cannot advance A's or B's add-decision cursor"

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card_path, state_path = _artifacts(tmp)
        first = _run("prepare", "--root", root, "--card-json", card_path,
                     "--state-json", state_path)
        first_plan = json.loads(first.stdout)["review_plan"]
        first_question = next(q for q in first_plan["question_queue"] if q["kind"] == "add_thesis")
        answers = pathlib.Path(tmp) / "cursor-answers.json"
        narrative = pathlib.Path(tmp) / "cursor-narrative.json"
        answers.write_text(json.dumps(_answers(first_plan, commitment="candidate_0")), encoding="utf-8")
        narrative.write_text(json.dumps(_narrative()), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", first_plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        bundle = json.loads((pathlib.Path(json.loads(final.stdout)["path"]) / "bundle.json").read_text())
        evidence_event = bundle["thesis_decisions"][0]
        assert evidence_event["evidence_id"].startswith("evidence-")
        assert evidence_event["provenance"] == {
            "source": "earnings call", "source_state": "confirmed",
            "captured_at": "2026-07-14", "observed_at": None,
        }
        assert evidence_event["evaluation"] == {"state": "pending", "evaluated_at": None}

        # Canonical bundles remain authoritative even when compatibility
        # projections disappear before repair.
        (root / "theses.jsonl").unlink()
        (root / "thesis_decisions.jsonl").unlink()
        same = _run("prepare", "--root", root, "--card-json", card_path,
                    "--state-json", state_path, "--session-nonce", "same-cursor")
        same_plan = json.loads(same.stdout)["review_plan"]
        assert not any(q["kind"] == "add_thesis" for q in same_plan["question_queue"])
        active = same_plan["state_snapshot"]["active_theses"][0]
        assert active["decision_cursor"] == "PLTR#2026-01-01#1#add#3"
        assert active["thesis_id"].startswith("thesis-") and active["last_event_id"].startswith("thesis-decision-")
        assert active["last_evidence"]["source_state"] == "confirmed"
        assert active["last_evidence"]["observed_at"] is None, \
            "review time cannot be substituted for a missing observation date"

        state = json.loads(state_path.read_text(encoding="utf-8"))
        position = state["holdings"]["positions"]["PLTR"]
        position["add_count"] = 4
        position["decision_cursor"] = "PLTR#2026-01-01#1#add#4"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        changed = _run("prepare", "--root", root, "--card-json", card_path,
                       "--state-json", state_path, "--session-nonce", "new-cursor")
        changed_plan = json.loads(changed.stdout)["review_plan"]
        changed_question = next(q for q in changed_plan["question_queue"] if q["kind"] == "add_thesis")
        assert changed_question["decision_cursor"].endswith("#add#4")
        assert changed_question["id"] != first_question["id"]


def test_stable_thesis_identity_does_not_depend_on_update_order():
    plan = {"session_id": "2026-07-14__stable", "engine_state": {"date_end": "2026-07-14"},
            "state_snapshot": {"thesis_states": []}}
    updates = [
        {"ticker": "A", "cycle_id": "A#2026-01-01#1", "why": "a", "exit_trigger": "x",
         "maturity": "inferred"},
        {"ticker": "B", "cycle_id": "B#2026-01-02#1", "why": "b", "exit_trigger": "y",
         "maturity": "inferred"},
    ]
    forward = {row["cycle_id"]: row for row in review_engine._assign_thesis_ids(plan, updates)}
    reverse = {row["cycle_id"]: row for row in review_engine._assign_thesis_ids(plan, list(reversed(updates)))}
    for cycle_id in forward:
        assert forward[cycle_id]["thesis_id"] == reverse[cycle_id]["thesis_id"]
        assert forward[cycle_id]["event_id"] == reverse[cycle_id]["event_id"]

    cycle_id = "A#2026-01-01#1"
    first = {**forward[cycle_id], "why": "first", "event_id": "event-first"}
    decision = {"event": "thesis_decision", "cycle_id": cycle_id, "ticker": "A",
                "event_id": "event-decision", "revises": "event-first",
                "decision": "new_evidence", "decision_cursor": f"{cycle_id}#add#2",
                "review_date": "2026-07-14"}
    revision = {**first, "why": "revised", "event_id": "event-revision",
                "revises": "event-decision"}
    folded = thesis_engine.reconstruct_states([revision, first], [decision])[0]
    assert folded["why"] == "revised" and folded["last_event_id"] == "event-revision"
    assert folded["decision_cursor"].endswith("#add#2"), \
        "revises links, not same-day session digest order, must define the event chain"


def test_fold_preserves_legacy_thesis_and_explicit_full_exit_outcome():
    cycle_id = "OLD#2025-01-01#1"
    base = {"ticker": "OLD", "cycle_id": cycle_id, "why": "legacy claim",
            "exit_trigger": "claim fails", "maturity": "testable", "status": "active",
            "session_date": "2025-01-01"}
    decision = {"event": "thesis_decision", "cycle_id": cycle_id, "ticker": "OLD",
                "decision": "new_evidence", "decision_cursor": f"{cycle_id}#add#2",
                "evidence_delta": {"claim": "legacy claim changed", "source": "legacy note"},
                "review_date": "2025-02-01"}
    closed = {"event": "exit_narrative", "cycle_id": cycle_id, "ticker": "OLD",
              "exit_kind": "full", "exit_reason": None, "capture": "skipped",
              "recorded_at": "2025-03-01"}
    state = thesis_engine.reconstruct_states([base, closed], [decision])[0]
    assert state["thesis_id"].startswith("thesis-") and state["event_id"].startswith("legacy-thesis-")
    assert state["decision_cursor"].endswith("#add#2")
    assert state["last_evidence"]["source_state"] == "captured", \
        "legacy evidence must not be silently promoted to the newer confirmed contract"
    assert state["last_evidence"]["captured_at"] == "2025-02-01"
    assert state["position_status"] == "closed" and state["status"] == "closed"
    assert state["final_outcome"]["side_state"] == "skipped", \
        "a skipped explanation still preserves the deterministic cycle-close outcome"


def test_english_is_same_contract_with_localized_questions_and_card():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root, language="en")
        assert plan["language"] == "en" and "new evidence" in plan["question_queue"][0]["question"]
        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(_answers(plan, commitment="candidate_0")), encoding="utf-8")
        narrative.write_text(json.dumps(_narrative("en")), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        result = json.loads(final.stdout)
        assert final.returncode == 0
        text = pathlib.Path(result["private_card"]).read_text(encoding="utf-8")
        assert "Trade Review" not in text or "The account for this review" in text
        assert "Before averaging down" in text and "這期的帳" not in text


def test_reconciliation_opens_the_card_with_prior_commitment():
    import card_renderer
    bundle = {"review_plan": {"state_snapshot": {"prior_commitment": {
                  "rule": "下單前先檢查單一風險部位上限", "metric_key": "max_pos_pct", "metric_value": 0.51}}},
              "engine_state": {"metrics": {"max_pos_pct": 0.48}}}
    zh = card_renderer._reconciliation_lines(bundle, "zh-TW")
    assert zh and "上次你承諾" in zh[0] and "51%" in zh[0] and "48%" in zh[0], \
        "#151: the card must open against last time's commitment with verbatim then/now values"
    en = card_renderer._reconciliation_lines(bundle, "en")
    assert en and "Last time you committed" in en[0] and "51%" in en[0]
    assert "max_pos_pct" not in zh[0] and "max_pos_pct" not in en[0], \
        "A-12: internal metric keys never appear on the card"
    assert card_renderer._reconciliation_lines({"review_plan": {}}, "en") == [], \
        "first review has no prior commitment and no reconciliation line"


def test_account_performance_pillar_gate_and_full_render():
    import card_renderer
    gated = {"acct_perf": {"hold_twr": 0.12, "acct_twr": None, "irr_annual": None,
                           "cash_drag": None, "note": "gate", "window": {"days": 30}}}
    lines = card_renderer._performance_lines(gated, "en", {})
    assert any("Holdings-only time-weighted return was 12%" in x for x in lines)
    assert any("stays locked until cash has a complete anchor" in x for x in lines), \
        "#181: gate must render the unlock invitation, not the engine note text"
    assert not any("錨點" in x for x in lines), "engine's internal zh note must not leak into en cards"
    full = {"acct_perf": {"hold_twr": 0.12, "acct_twr": 0.10, "irr_annual": 0.15,
                          "cash_drag": -0.02, "note": None, "window": {"days": 30}}}
    zh = card_renderer._performance_lines(full, "zh-TW", {})
    assert any("帳戶級時間加權報酬為 10%" in x and "IRR 15%" in x for x in zh)
    assert any("不是對錯判定" in x for x in zh), "#179: cash drag stays neutral, never a verdict"
    assert card_renderer._performance_lines({"acct_perf": {"note": "offline"}}, "en", {}) == [], \
        "no holdings pillar computed -> no account section"


def test_all_json_schemas_parse():
    names = {"review-plan.schema.json", "answers.schema.json", "narrative.schema.json",
             "session-bundle.schema.json"}
    assert names == {p.name for p in SCHEMAS.glob("*.json")}
    for path in SCHEMAS.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["$schema"].endswith("2020-12/schema")


def main():
    tests = sorted((name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            fn(); print("PASS ", name)
        except Exception as exc:
            failed += 1; print("FAIL ", name, repr(exc))
    print(f"\n{len(tests)-failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
