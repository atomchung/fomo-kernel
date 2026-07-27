#!/usr/bin/env python3
"""Skill v2 orchestration / ETF / recovery tests (offline, standard library only)."""
import concurrent.futures
import hashlib
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time


ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "skills" / "fomo-kernel" / "engine"
REVIEW = ENGINE_DIR / "review.py"
SCHEMAS = ROOT / "skills" / "fomo-kernel" / "schemas"
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ROOT / "tests" / "agent"))
import card_renderer  # noqa: E402
import instruments  # noqa: E402
import ledger as ledger_engine  # noqa: E402
import problems as problems_engine  # noqa: E402
import review as review_engine  # noqa: E402
import session as session_engine  # noqa: E402
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
            # #400/#412 neutral observables: consistent with the PLTR position below
            # (cycle_start 2026-01-01 → date_end 2026-07-14 = 194 days).
            "longest_hold_days": 194, "longest_hold_ticker": "PLTR",
            "worst_cur_ret": -0.18, "worst_cur_ret_ticker": "PLTR",
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
            "number_line": "你有 3 次在虧損倉往下加碼，其中 1 次加碼當下佔成本 >25%",
            "lens_rule": "往下加碼前先寫新證據。", "lens_quote": "先驗證再加碼。",
            "raw": {"dim": "加碼攤平", "tier": 1, "triggered": True, "severity": 0.8,
                    "count": 3, "breach": 1, "tickers": ["PLTR"]}}
    card = {
        "schema_version": 1, "philosophy": "test lens",
        "strength": "你守住了其他部位的上限。",
        "overview": {"total_pnl": -300, "realized": 200, "unrealized": -500,
                     "payoff": 1.4, "avg_win": 140, "avg_loss": -100},
        "what_if": None,
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


def _run(*args, env=None):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60, env=env)


def _pending_plan(root, stdout):
    """Read the full persisted plan from the pending bundle on disk.

    prepare's stdout now carries only the agent-facing projection: engine_card
    and most of engine_state are trimmed to cut the context the agent re-sends
    every turn.  The canonical full plan lives in the pending bundle — where
    preview/finalize read it, and where these engine-correctness assertions
    must read it too.
    """
    session_id = json.loads(stdout)["review_plan"]["session_id"]
    return session_engine.load_pending(str(root), session_id)["plan"]


def _prepare(tmp, root, language="zh-TW"):
    card, state = _artifacts(tmp)
    run = _run("prepare", "--root", root, "--language", language,
               "--card-json", card, "--state-json", state)
    assert run.returncode == 0, run.stdout + run.stderr
    return _pending_plan(root, run.stdout)


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


def _snapshot_json(tmp, payload=None, name="positions.json"):
    payload = payload or {
        "as_of": "2026-07-16",
        "positions": [
            {"ticker": "SPY", "shares": 2, "avg_cost": 600, "market_value": 1240,
             "market": "US", "currency": "USD"},
            {"ticker": "QQQ", "shares": 10, "avg_cost": 500, "market_value": 5100,
             "market": "US", "currency": "USD"},
            {"ticker": "2330.TW", "shares": 1000, "avg_cost": 1000,
             "market_value": 1040000, "market": "TW", "currency": "TWD"},
        ],
        "fx": {"USD": 1, "TWD": 0.033},
    }
    path = pathlib.Path(tmp) / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _snapshot_prepare(tmp, root, payload=None, language="en", name="positions.json"):
    path = _snapshot_json(tmp, payload=payload, name=name)
    run = _run("prepare", "--route", "snapshot_review", "--snapshot-json", path,
               "--root", root, "--language", language)
    assert run.returncode == 0, run.stdout + run.stderr
    return _pending_plan(root, run.stdout), path


def _snapshot_answers(plan, commitment=None):
    updates = []
    for row in plan["missing_thesis_positions"]:
        updates.append({
            "ticker": row["ticker"], "cycle_id": row["cycle_id"],
            "why": "The opening snapshot suggests a portfolio role that remains inferred",
            "horizon": None,
            "exit_trigger": "A later review contradicts the inferred portfolio role",
            "target_size": "bounded", "driver": "opening snapshot",
            "maturity": "inferred", "source_type": "other",
            "source_name": "opening snapshot", "source_confidence": "candidate",
        })
    out = {"session_id": plan["session_id"], "answers": [], "thesis_updates": updates,
           "observations": ["The snapshot establishes structure without historical behavior claims"]}
    if commitment is not None:
        out["commitment"] = {"choice": commitment}
    return out


def _snapshot_narrative(plan, language="en"):
    honesty = {}
    for key in plan["card_plan"]["required_honesty_keys"]:
        honesty[key] = {
            "snapshot_scope": "This opening check cannot score transaction history yet.",
            "currency_mix": "Currency facts remain separate unless reliable conversion is available.",
            "unclassified_drivers": "Unclassified positions can make concentration look safer than it is.",
            "etf_metadata": "Missing fund metadata remains unknown instead of being filled with zero.",
        }.get(key, "The available snapshot leaves this limitation explicit.")
    if language == "en":
        return {"headline": "An opening structure baseline",
                "mirror": "The supplied positions show structure without proving past behavior.",
                "honesty": honesty}
    zh = {
        "snapshot_scope": "這次只建立持倉結構，交易歷史仍維持未判定。",
        "currency_mix": "缺少可靠換算時，各幣別事實保持分開。",
        "unclassified_drivers": "尚未分類的持倉可能讓集中風險看起來偏低。",
        "etf_metadata": "基金資料缺值維持未知，不用零補齊。",
    }
    return {"headline": "先建立組合結構基線",
            "mirror": "現有持倉能看結構，不能證明過去行為。",
            "honesty": {key: zh.get(key, "這項快照限制保持明示。") for key in honesty}}


def _prepare_with_trades(tmp, root, language="zh-TW", nonce=""):
    card, state = _artifacts(tmp)
    csv_path = _trade_csv(tmp)
    args = ["prepare", csv_path, "--root", root, "--language", language,
            "--card-json", card, "--state-json", state]
    if nonce:
        args.extend(["--session-nonce", nonce])
    run = _run(*args)
    assert run.returncode == 0, run.stdout + run.stderr
    return _pending_plan(root, run.stdout), csv_path, card, state


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
        elif question["kind"] == "rule_breach":
            answers.append({"question_id": question["id"], "choice": "keep_tracking"})
        elif question["kind"] == "initial_thesis":
            answers.append({"question_id": question["id"], "choice": "no_clear_thesis"})
        else:
            answers.append({"question_id": question["id"], "choice": "deliberate_plan"})
    out["answers"] = answers
    return out


def _answers(plan, evidence=True, commitment=None):
    # Answer every queued question. On a sparse first review the route-min
    # backfill (#291) adds a grounded headline_motive beside the PLTR add, so a
    # single-answer helper would now under-answer the required queue.
    answers = []
    for question in plan["question_queue"]:
        kind = question["kind"]
        if kind == "add_thesis":
            row = {"question_id": question["id"], "choice": "new_evidence"}
            if evidence:
                row["evidence_delta"] = {"claim": "Enterprise demand accelerated",
                                         "source": "earnings call", "falsifier": "renewals weaken"}
            answers.append(row)
        elif kind == "initial_thesis":
            answers.append({"question_id": question["id"], "choice": "no_clear_thesis"})
        elif kind == "rule_breach":
            answers.append({"question_id": question["id"], "choice": "keep_tracking"})
        elif kind in ("revisit", "due_revisit", "condition_crossing", "condition_basis"):
            # #412: a condition question defaults to skip here so the generic
            # helper stays generic. Every test that is *about* the answer
            # replaces it explicitly.
            answers.append({"question_id": question["id"], "choice": "skip"})
        else:
            answers.append({"question_id": question["id"], "choice": "deliberate_plan"})
    out = {
        "session_id": plan["session_id"], "answers": answers,
        "thesis_updates": [{"ticker": "PLTR", "cycle_id": "PLTR#2026-01-01#1",
                            "why": "Enterprise adoption may still be underpriced",
                            "horizon": "quarters", "exit_trigger": "Renewals weaken",
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


def _minimal_bundle(session_id, marker="same"):
    """Small direct-storage fixture: renderer/schema behavior is out of scope."""
    return {
        "schema_version": 2, "session_id": session_id, "route": "test_drive",
        "language": "en", "review_plan": {"persist": False, "marker": marker},
        "engine_state": {"date_end": "2026-07-17"}, "engine_card": {},
        "answers": {"marker": marker}, "narrative": {"marker": marker},
        "thesis_updates": [], "thesis_decisions": [], "exit_narratives": [],
        "commitment": None, "observations": [],
    }


def _runtime_snapshot_bundle(session_id, ticker="SPY"):
    bundle = _minimal_bundle(session_id)
    bundle.update({
        "route": "snapshot_review",
        "review_plan": {"persist": True, "input": {"kind": "positions_snapshot"}},
        "engine_state": {
            "date_end": "2026-07-17", "metrics": {}, "problem_events": [],
            "snapshot_anchor": {
                "type": "snapshot", "as_of": "2026-07-17",
                "source": "user_declared", "is_complete": True,
                "positions": [{
                    "ticker": ticker, "shares": 1, "avg_cost": 100,
                    "market": "US", "currency": "USD",
                }],
            },
        },
    })
    return bundle


def _direct_finalize(root, bundle):
    with session_engine.finalize_transaction(root, bundle["session_id"]) as transaction:
        return transaction.commit_bundle(bundle, "private\n", "public\n", persist=True)


def _write_pre_durability_canonical(root, bundle, private_md="private", public_md="public",
                                    private_html=None, manifest=True):
    """Emulate the origin/main writer: complete visible files, but no fsync."""
    final = pathlib.Path(root) / "sessions" / bundle["session_id"]
    final.mkdir(parents=True)
    artifacts = {
        "bundle.json": session_engine.pretty(bundle),
        "state.json": session_engine.pretty(bundle.get("engine_state") or {}),
        "plan.json": session_engine.pretty(bundle.get("review_plan") or {}),
        "answers.json": session_engine.pretty(bundle.get("answers") or {}),
        "narrative.json": session_engine.pretty(bundle.get("narrative") or {}),
        "card-private.md": private_md if private_md.endswith("\n") else private_md + "\n",
        "card-public.md": public_md if public_md.endswith("\n") else public_md + "\n",
    }
    if private_html is not None:
        artifacts["card-private.html"] = (
            private_html if private_html.endswith("\n") else private_html + "\n")
    if manifest:
        hashes = {name: session_engine._artifact_hash(text) for name, text in artifacts.items()}
        artifacts["manifest.json"] = session_engine.pretty(
            {"schema_version": 1, "sha256": hashes})
    for name, text in artifacts.items():
        (final / name).write_text(text, encoding="utf-8")
    return final


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


def test_snapshot_prepare_builds_narrow_plan_without_writing_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, snapshot_path = _snapshot_prepare(tmp, root)
        state, card = plan["engine_state"], plan["engine_card"]

        assert plan["route"] == "snapshot_review"
        assert plan["flow_path"] == "flows/snapshot-review.md"
        assert plan["input"]["kind"] == "positions_snapshot"
        assert plan["input"]["ledger_ingest"] == {
            "mode": "finalize_projection", "kind": "positions_snapshot"}
        assert plan["question_queue"] == [], "a snapshot must not invent a historical motive"
        assert {row["ticker"] for row in plan["missing_thesis_positions"]} == {
            "SPY", "QQQ", "2330.TW"}
        assert set(state["holdings"]["positions"]) == {"SPY", "QQQ", "2330.TW"}
        assert all(row["cycle_id"].endswith("#2026-07-16#1")
                   for row in state["holdings"]["positions"].values())
        assert state["problem_opportunities"] is None and state["problem_events"] == []
        for key in ("avgdown_count", "avgdown_breach", "payoff", "exit_severity",
                    "hold_severity", "beta", "alpha_ann", "alpha_t", "alpha_credible"):
            assert state["metrics"][key] is None, key
        assert card["overview"] == {} and card["ticker_diagnosis"] == []
        assert card["thesis_questions"] == [] and card["alpha_beta_breakdown"] == {}
        assert {row["dim"] for row in card["dims_raw"]} <= {"部位 sizing", "分散"}
        assert not (root / "ledger.jsonl").exists(), "prepare cannot leave an orphan anchor"

        resumed = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                       snapshot_path, "--root", root, "--language", "en")
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        payload = json.loads(resumed.stdout)
        assert payload["status"] == "resumed" and payload["session_id"] == plan["session_id"]
        assert not (root / "ledger.jsonl").exists()


def test_snapshot_validation_is_strict_and_atomic():
    valid = {
        "as_of": "2026-07-16",
        "positions": [{"ticker": "NVDA", "shares": 10, "avg_cost": 100,
                       "market": "US", "currency": "USD"}],
    }
    mutations = {
        "empty": {**valid, "positions": []},
        "future": {**valid, "as_of": "2999-01-01"},
        "negative": {**valid, "positions": [{**valid["positions"][0], "shares": -1}]},
        "nan": {**valid, "positions": [{**valid["positions"][0], "shares": float("nan")}]},
        "missing_market": {**valid, "positions": [
            {key: value for key, value in valid["positions"][0].items() if key != "market"}]},
        "unknown_field": {**valid, "positions": [{**valid["positions"][0], "weight": 1}]},
        "bad_fx": {**valid, "fx": {"USD": 2}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        for name, payload in mutations.items():
            root = pathlib.Path(tmp) / f"coach-{name}"
            path = _snapshot_json(tmp, payload=payload, name=f"{name}.json")
            run = _run("prepare", "--route", "snapshot_review", "--snapshot-json", path,
                       "--root", root)
            assert run.returncode == 2, (name, run.stdout, run.stderr)
            assert json.loads(run.stdout)["status"] == "error", name
            assert not (root / "ledger.jsonl").exists(), name
            assert not (root / ".pending").exists(), name

        valid_path = _snapshot_json(tmp, payload=valid, name="valid.json")
        cash_root = pathlib.Path(tmp) / "coach-cash-arg"
        cash_run = _run(
            "prepare", "--route", "snapshot_review", "--snapshot-json", valid_path,
            "--cash", '{"currency":"USD","amount":100}', "--root", cash_root,
        )
        assert cash_run.returncode == 2, cash_run.stdout + cash_run.stderr
        assert "include cash in the snapshot envelope" in json.loads(cash_run.stdout)["error"]
        assert not (cash_root / ".pending").exists()


def test_snapshot_duplicate_rows_merge_in_code_and_conflicts_fail_closed():
    payload = {
        "as_of": "2026-07-16",
        "positions": [
            {"ticker": "NVDA", "shares": 2, "avg_cost": 100, "market_value": 240,
             "market": "US", "currency": "USD"},
            {"ticker": "nvda", "shares": 3, "avg_cost": 200, "market_value": 660,
             "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload)
        row = plan["engine_state"]["holdings"]["positions"]["NVDA"]
        assert row["shares"] == 5 and row["avg_cost"] == 160
        assert row["market_value"] == 900
        assert json.loads(plan["input"]["engine_meta"])["merged_rows"] == 1

        conflict = {**payload, "positions": [payload["positions"][0],
                    {**payload["positions"][1], "market": "TW", "currency": "TWD"}]}
        bad_root = pathlib.Path(tmp) / "bad-coach"
        path = _snapshot_json(tmp, payload=conflict, name="conflict.json")
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json", path,
                   "--root", bad_root)
        assert run.returncode == 2 and "conflicting market or currency" in run.stdout
        assert not bad_root.exists()


def test_snapshot_currency_gates_weights_but_preserves_etf_structure():
    payload = {
        "as_of": "2026-07-16",
        "positions": [
            {"ticker": "SPY", "shares": 1, "avg_cost": 600,
             "market": "US", "currency": "USD"},
            {"ticker": "QQQ", "shares": 1, "avg_cost": 500,
             "market": "US", "currency": "USD"},
            {"ticker": "2330.TW", "shares": 100, "avg_cost": 1000,
             "market": "TW", "currency": "TWD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload)
        state, card = plan["engine_state"], plan["engine_card"]
        assert card["snapshot_summary"]["valuation_basis"] == "cost"
        assert card["snapshot_summary"]["weights_available"] is False
        assert card["snapshot_summary"]["fx_gaps"] == ["TWD"]
        assert card["top_holes"] == [] and card["dims_raw"] == []
        assert state["metrics"]["max_pos_pct"] is None
        assert state["holdings"]["positions"]["2330.TW"]["currency"] == "TWD"
        structure = card["portfolio_structure"]
        assert [(row["ticker"], row["weight"]) for row in structure["allocation_etfs"]] == \
            [("SPY", None)]
        assert [(row["ticker"], row["weight"]) for row in structure["concentrated_etfs"]] == \
            [("QQQ", None)]
        assert {row["key"] for row in card["honesty_ledger"]} >= {
            "snapshot_scope", "currency_mix", "etf_metadata"}

        complete = {**payload, "fx": {"USD": 1, "TWD": 0.033}}
        plan2, _path2 = _snapshot_prepare(tmp, pathlib.Path(tmp) / "coach-fx",
                                         payload=complete, name="complete-fx.json")
        card2 = plan2["engine_card"]
        assert card2["snapshot_summary"]["weights_available"] is True
        assert card2["portfolio_structure"]["allocation_etfs"][0]["ticker"] == "SPY"
        assert card2["portfolio_structure"]["concentrated_etfs"][0]["ticker"] == "QQQ"


def _snapshot_render_bundle(plan, language, session_id="test"):
    card, state = plan["engine_card"], plan["engine_state"]
    en = language == "en"
    return {
        "schema_version": 2, "session_id": session_id, "route": "snapshot_review",
        "language": language, "review_plan": {}, "engine_state": state, "engine_card": card,
        "answers": {},
        "narrative": {
            "headline": "Opening structure check" if en else "開場結構檢查",
            "mirror": ("This review looks at how the portfolio is put together."
                       if en else "這次檢視聚焦組合怎麼組成。"),
        },
        "thesis_updates": [], "thesis_decisions": [], "exit_narratives": [],
        "commitment": None, "observations": [],
    }


def test_snapshot_card_states_scope_once_and_leads_with_both_structural_holes():
    """#316: the history-dimension disclosure must not repeat (once, in the
    Block-1 footnote), the card's last block must name the unlock payoff
    exactly once, and both structural findings a snapshot can diagnose —
    single-position concentration and driver/sector concentration — must
    render as real content instead of the less severe one silently dropping
    out behind the other."""
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": "NVDA", "shares": 40, "avg_cost": 152.3, "market": "US",
             "currency": "USD", "market_value": 6800},
            {"ticker": "PLTR", "shares": 200, "avg_cost": 18.5, "market": "US",
             "currency": "USD", "market_value": 4200},
            {"ticker": "SPY", "shares": 10, "avg_cost": 500, "market": "US",
             "currency": "USD", "market_value": 5300},
            {"ticker": "2330.TW", "shares": 1000, "avg_cost": 900, "market": "TW",
             "currency": "TWD", "market_value": 985000},
        ],
        "fx": {"USD": 1, "TWD": 0.0307},
    }
    markers = {
        "en": ("are out of scope for this position-snapshot review",
               "unlocks behavior diagnostics",
               "Import transaction history later"),
        "zh-TW": ("不在這次持倉快照的評分範圍內",
                  "匯入交易歷史 CSV 可解鎖行為診斷",
                  "之後匯入交易紀錄即可解鎖"),
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload, language="en")
        card = plan["engine_card"]
        dim_ids = {card_renderer.dimension_id(h["dim"]) for h in card["top_holes"]}
        assert dim_ids == {"position_sizing", "diversification"}, \
            "fixture must trigger both structural dimensions to exercise the fix"

        for language in ("en", "zh-TW"):
            bundle = _snapshot_render_bundle(plan, language)
            scope_marker, unlock_marker, old_marker = markers[language]
            for surface in (card_renderer.render_private(bundle), card_renderer.render_html(bundle)):
                assert surface.count(scope_marker) == 1, \
                    f"[{language}] consolidated scope sentence must appear exactly once"
                assert surface.count(unlock_marker) == 1, \
                    f"[{language}] unlock hint must appear exactly once"
                assert old_marker not in surface, \
                    f"[{language}] the old duplicated wording must not resurface"
            # Structure-health content leads as real content: both structural
            # findings render with engine numbers, neither is a dropped leftover.
            # (Markers deliberately avoid the "driver"/thesis glossary text that
            # #313/#314/#272 own — this test asserts presence, not wording.)
            md = card_renderer.render_private(bundle)
            assert "2330.TW" in md and "65" in md, \
                f"[{language}] single-position concentration must render as main content"
            # #387: the en marker was "top three non-allocation risks", the
            # renderer's own short sentence for this dimension. That sentence
            # is gone — both locales now render the richer narration from copy
            # `hole_lines.diversification` — so the marker follows the content
            # it was always meant to assert (the top-three concentration is
            # present), not the wording that happened to carry it.
            sector_marker = "top three" if language == "en" else "top3"
            assert sector_marker in md, \
                f"[{language}] driver/sector concentration must also render, not be dropped"

        # A well-diversified snapshot (weights available, nothing triggered) must
        # report a clean structural read, never misreport unscored weights as
        # the reason nothing was flagged.
        clean_payload = {
            "as_of": "2026-07-20",
            "positions": [
                {"ticker": t, "shares": 10, "avg_cost": 100, "market": "US",
                 "currency": "USD", "market_value": 2000}
                for t in ("MSTR", "HOOD", "CAVA", "MP", "ONDS", "NOK")
            ],
        }
        clean_plan, _ = _snapshot_prepare(tmp, pathlib.Path(tmp) / "coach-clean",
                                          payload=clean_payload, name="clean.json")
        assert clean_plan["engine_card"]["top_holes"] == [], \
            "fixture must be clean (no structural dimension triggered)"
        assert clean_plan["engine_card"]["snapshot_summary"]["weights_available"] is True
        clean_md = card_renderer.render_private(_snapshot_render_bundle(clean_plan, "en", "test-clean"))
        assert "did not flag concentration or diversification" in clean_md
        assert "unavailable weights as low risk" not in clean_md, \
            "weights ARE available here; the no-data fallback must not misreport them as unavailable"
        assert clean_md.count(markers["en"][1]) == 1, "unlock hint still renders exactly once"


def test_incomplete_snapshot_commits_review_without_accounting_anchor():
    payload = {
        "as_of": "2026-07-16",
        "is_complete": False,
        "positions": [{"ticker": "PLTR", "shares": 5, "avg_cost": 100,
                       "market": "US", "currency": "USD"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload, language="en")
        assert plan["input"]["ledger_ingest"] == {
            "mode": "canonical_only", "kind": "positions_snapshot",
            "reason": "incomplete_snapshot",
        }
        assert plan["engine_card"]["snapshot_summary"]["weights_available"] is False
        answers = pathlib.Path(tmp) / "answers-incomplete.json"
        narrative = pathlib.Path(tmp) / "narrative-incomplete.json"
        answers.write_text(json.dumps(_snapshot_answers(plan, commitment="skip")), encoding="utf-8")
        narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        assert result["projection_error"] is None
        assert result["projection"]["rows"][0]["status"] == "skipped_incomplete"
        assert not (root / "ledger.jsonl").exists()
        bundle = json.loads(
            (root / "sessions" / plan["session_id"] / "bundle.json").read_text()
        )
        inferred = bundle["thesis_updates"][0]
        assert inferred["cycle_provenance"] == {
            "kind": "snapshot_inference",
            "snapshot_as_of": "2026-07-16",
            "snapshot_complete": False,
        }
        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0 and not (root / "ledger.jsonl").exists()


def test_incomplete_snapshot_thesis_relinks_to_earlier_visible_cycle_and_persists():
    payload = {
        "as_of": "2026-07-16",
        "is_complete": False,
        "positions": [{"ticker": "PLTR", "shares": 10, "avg_cost": 100,
                       "market": "US", "currency": "USD"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        opening, _path = _snapshot_prepare(tmp, root, payload=payload, language="en")
        opening_answers = pathlib.Path(tmp) / "opening-answers.json"
        opening_narrative = pathlib.Path(tmp) / "opening-narrative.json"
        opening_answers.write_text(
            json.dumps(_snapshot_answers(opening, commitment="skip")), encoding="utf-8"
        )
        opening_narrative.write_text(
            json.dumps(_snapshot_narrative(opening), ensure_ascii=False), encoding="utf-8"
        )
        committed = _run(
            "finalize", "--root", root, "--session-id", opening["session_id"],
            "--answers", opening_answers, "--narrative", opening_narrative,
        )
        assert committed.returncode == 0, committed.stdout + committed.stderr
        opening_bundle = json.loads(
            (root / "sessions" / opening["session_id"] / "bundle.json").read_text()
        )
        prior = opening_bundle["thesis_updates"][0]

        card, state = _artifacts(tmp)
        card_data = json.loads(card.read_text())
        state_data = json.loads(state.read_text())
        card_data["thesis_questions"] = []
        state_data.update({"date_start": "2026-07-01", "date_end": "2026-07-18",
                           "n_held": 1})
        state_data["holdings"] = {
            "as_of": "2026-07-18", "derived_from": "trades_csv", "is_complete": False,
            "positions": {"PLTR": {
                "shares": 10, "cost": 1000, "avg_cost": 100,
                "market": "US", "currency": "USD", "cycle_start": "2026-07-01",
                "cycle_id": "PLTR#2026-07-01#1", "add_count": 0,
                "decision_cursor": None,
            }},
        }
        card.write_text(json.dumps(card_data, ensure_ascii=False), encoding="utf-8")
        state.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
        history = pathlib.Path(tmp) / "full-history.csv"
        history.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "PLTR,BUY,10,100,2026-07-01,Trade,US,USD\n",
            encoding="utf-8",
        )

        prepared = _run(
            "prepare", history, "--root", root, "--language", "en",
            "--card-json", card, "--state-json", state,
            "--session-nonce", "reveal-cycle-start",
        )
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        plan = json.loads(prepared.stdout)["review_plan"]
        target_cycle = "PLTR#2026-07-01#1"
        assert plan["missing_thesis_positions"] == []
        active = plan["state_snapshot"]["active_theses"]
        assert len(active) == 1 and active[0]["cycle_id"] == target_cycle
        assert active[0]["thesis_id"] == prior["thesis_id"]
        relinks = plan["state_snapshot"]["thesis_cycle_relinks"]
        assert len(relinks) == 1
        relink = relinks[0]
        assert relink["event"] == "thesis_cycle_relink"
        assert relink["thesis_id"] == prior["thesis_id"]
        assert relink["revises"] == prior["event_id"]
        assert relink["cycle_provenance"] == {
            "kind": "incomplete_snapshot_cycle_relink",
            "from_cycle_id": prior["cycle_id"],
            "snapshot_as_of": "2026-07-16",
            "revealed_cycle_start": "2026-07-01",
            "basis": "unique_open_ticker",
        }

        later_answers = {
            "session_id": plan["session_id"],
            "answers": [
                {"question_id": question["id"], "choice": "skip"}
                for question in plan["question_queue"]
            ],
            "thesis_updates": [], "observations": [],
            "commitment": {"choice": "skip"},
        }
        answers_path = pathlib.Path(tmp) / "later-answers.json"
        narrative_path = pathlib.Path(tmp) / "later-narrative.json"
        answers_path.write_text(json.dumps(later_answers), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative("en")), encoding="utf-8")
        finalized = _run(
            "finalize", "--root", root, "--session-id", plan["session_id"],
            "--answers", answers_path, "--narrative", narrative_path,
        )
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        later_bundle = json.loads(
            (root / "sessions" / plan["session_id"] / "bundle.json").read_text()
        )
        assert later_bundle["thesis_updates"] == [relink]

        replay = _run(
            "prepare", history, "--root", root, "--language", "en",
            "--card-json", card, "--state-json", state,
            "--session-nonce", "after-cycle-relink",
        )
        assert replay.returncode == 0, replay.stdout + replay.stderr
        replay_plan = json.loads(replay.stdout)["review_plan"]
        assert "thesis_cycle_relinks" not in replay_plan["state_snapshot"]
        replay_active = replay_plan["state_snapshot"]["active_theses"]
        assert len(replay_active) == 1
        assert replay_active[0]["cycle_id"] == target_cycle
        assert replay_active[0]["thesis_id"] == prior["thesis_id"]


def test_incomplete_snapshot_thesis_relink_fails_closed_for_reopened_or_ambiguous_ticker():
    prior = {
        "ticker": "PLTR", "cycle_id": "PLTR#2026-07-16#1",
        "thesis_id": "thesis-opening", "event_id": "event-opening",
        "last_event_id": "event-opening", "why": "inferred role",
        "exit_trigger": "role breaks", "maturity": "inferred",
        "source_confidence": "candidate", "origin": "snapshot",
        "position_status": "open",
        "cycle_provenance": {
            "kind": "snapshot_inference", "snapshot_as_of": "2026-07-16",
            "snapshot_complete": False,
        },
    }
    reopened = {"PLTR": {
        "cycle_id": "PLTR#2026-07-17#2", "cycle_start": "2026-07-17", "shares": 1,
    }}
    assert thesis_engine.build_incomplete_snapshot_cycle_relinks(
        [prior], reopened, "session-reopened", "2026-07-18"
    ) == [], "a post-snapshot cycle may be a close/reopen and must receive a new thesis"

    earlier = {"PLTR": {
        "cycle_id": "PLTR#2026-07-01#1", "cycle_start": "2026-07-01", "shares": 1,
    }}
    ambiguous = {**prior, "cycle_id": "PLTR#2026-07-15#9",
                 "event_id": "event-other", "last_event_id": "event-other"}
    assert thesis_engine.build_incomplete_snapshot_cycle_relinks(
        [prior, ambiguous], earlier, "session-ambiguous", "2026-07-18"
    ) == [], "ticker-only matching must not choose between two open snapshot candidates"


def test_snapshot_preview_finalize_and_repair_keep_one_private_anchor():
    payload = {
        "as_of": "2026-07-16",
        "positions": [
            {"ticker": "SPY", "shares": 2, "market_value": 1200,
             "market": "US", "currency": "USD"},
            {"ticker": "PLTR", "shares": 20, "market_value": 3000,
             "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload, language="en")
        answers = pathlib.Path(tmp) / "snapshot-answers.json"
        narrative = pathlib.Path(tmp) / "snapshot-narrative.json"
        answer_payload = _snapshot_answers(plan, commitment="skip")
        narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False),
                             encoding="utf-8")
        for field, value, message in (
            ("maturity", "testable", "must remain inferred"),
            ("source_confidence", "confirmed", "candidate provenance"),
        ):
            rejected = json.loads(json.dumps(answer_payload))
            rejected["thesis_updates"][0][field] = value
            rejected_path = pathlib.Path(tmp) / f"snapshot-answers-bad-{field}.json"
            rejected_path.write_text(json.dumps(rejected, ensure_ascii=False), encoding="utf-8")
            rejected_preview = _run(
                "preview", "--root", root, "--session-id", plan["session_id"],
                "--answers", rejected_path, "--narrative", narrative,
            )
            assert rejected_preview.returncode == 2
            assert message in rejected_preview.stdout

        answers.write_text(json.dumps(answer_payload, ensure_ascii=False),
                           encoding="utf-8")

        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers, "--narrative", narrative)
        assert preview.returncode == 0, preview.stdout + preview.stderr
        preview_payload = json.loads(preview.stdout)
        private, public = preview_payload["private_card"], preview_payload["public_card"]
        assert "opening portfolio check" in private.lower()
        # #316: the out-of-scope disclosure collapses into the Block-1 footnote
        # exactly once (agent-authored honesty text here), and the card's last
        # block names the concrete unlock payoff exactly once — regardless of
        # the "skip commitment" answer this scenario exercises.
        assert private.count("cannot score transaction history yet") == 1
        assert private.count("unlocks behavior diagnostics") == 1
        assert "Total P&L" not in private and "Best:" not in private and "Worst:" not in private
        assert "opening portfolio check" in public.lower()
        assert "behavioral pressure" not in public and "highlighted behavior" not in public
        for secret in ("SPY", "PLTR", "2026-07-16", plan["session_id"],
                       "The supplied positions show structure"):
            assert secret not in public, secret
        assert not (root / "ledger.jsonl").exists(), "preview cannot project accounting facts"

        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        assert result["status"] == "committed" and result["projection_error"] is None
        bundle = json.loads((root / "sessions" / plan["session_id"] / "bundle.json").read_text())
        assert all(row["origin"] == "snapshot" for row in bundle["thesis_updates"])
        rows = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert [row["type"] for row in rows] == ["snapshot"]
        assert rows[0]["snapshot_id"].startswith("snapshot-")

        repeated = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                        "--answers", answers, "--narrative", narrative)
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        rows2 = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert rows2 == rows, "an identical finalize retry must not append a second anchor"

        same_prepare = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                            _path, "--root", root, "--language", "en")
        assert same_prepare.returncode == 0
        assert json.loads(same_prepare.stdout)["status"] == "already_committed"
        # A different second declaration no longer fails closed at prepare: it
        # enters the reconciliation path (#220) with the narrow diff frozen in
        # the Review Plan — and prepare still writes nothing to the ledger.
        changed_payload = {**payload, "positions": [
            {**payload["positions"][0], "shares": 3}, payload["positions"][1]]}
        changed = _snapshot_json(tmp, payload=changed_payload, name="changed-snapshot.json")
        second = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                      changed, "--root", root, "--language", "en")
        assert second.returncode == 0, second.stdout + second.stderr
        second_plan = json.loads(second.stdout)["review_plan"]
        reconciliation = second_plan["engine_state"]["snapshot_reconciliation"]
        assert reconciliation["status"] == "adjusted"
        assert reconciliation["diff"]["positions"] == [
            {"ticker": "SPY", "kind": "shares", "derived": 2.0, "declared": 3.0}]
        assert "snapshot_reconciliation" in \
            second_plan["card_plan"]["required_honesty_keys"]
        assert [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()] == rows, \
            "prepare freezes the reconciliation diff without any ledger write"

        (root / "ledger.jsonl").unlink()
        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr
        repaired_rows = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert len(repaired_rows) == 1 and repaired_rows[0]["snapshot_id"] == rows[0]["snapshot_id"]
        repaired_again = _run("repair-projections", "--root", root)
        assert repaired_again.returncode == 0
        assert len((root / "ledger.jsonl").read_text().splitlines()) == 1


def _finalize_snapshot_session(tmp, root, plan, tag):
    answers = pathlib.Path(tmp) / f"answers-{tag}.json"
    narrative = pathlib.Path(tmp) / f"narrative-{tag}.json"
    answers.write_text(json.dumps(_snapshot_answers(plan, commitment="skip")), encoding="utf-8")
    narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False), encoding="utf-8")
    return _run("finalize", "--root", root, "--session-id", plan["session_id"],
                "--answers", answers, "--narrative", narrative)


def _ledger_rows(root):
    return [json.loads(line)
            for line in (pathlib.Path(root) / "ledger.jsonl").read_text().splitlines()]


def test_second_snapshot_adjusted_writes_adjustment_and_adopts_new_anchor():
    """The #220 adjusted path: narrow frozen diff -> adjustment event preserving
    history -> newer declaration adopted by latest_anchor -> idempotent replay."""
    initial = {
        "as_of": "2026-07-10",
        "positions": [
            {"ticker": "SPY", "shares": 2, "avg_cost": 600, "market": "US", "currency": "USD"},
            {"ticker": "PLTR", "shares": 20, "avg_cost": 30, "market": "US", "currency": "USD"},
        ],
        "cash": {"USD": 1000},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan1, _path = _snapshot_prepare(tmp, root, payload=initial, name="first.json")
        first = _finalize_snapshot_session(tmp, root, plan1, "first")
        assert first.returncode == 0, first.stdout + first.stderr

        # Prior weekly ingests: one trade inside the declared window, one after
        # the second declaration's end-of-day view.
        ledger_engine.append_events(str(root / "ledger.jsonl"), [
            {"type": "trade", "date": "2026-07-12", "ticker": "PLTR", "action": "buy",
             "qty": 5, "price": 40, "market": "US", "currency": "USD"},
            {"type": "trade", "date": "2026-07-16", "ticker": "SPY", "action": "buy",
             "qty": 1, "price": 610, "market": "US", "currency": "USD"},
        ])

        second_payload = {
            "as_of": "2026-07-15",
            "positions": [
                {"ticker": "SPY", "shares": 2, "avg_cost": 600, "market": "US", "currency": "USD"},
                {"ticker": "PLTR", "shares": 30, "avg_cost": 32, "market": "US", "currency": "USD"},
            ],
            "cash": {"USD": 800},
        }
        plan2, _second = _snapshot_prepare(tmp, root, payload=second_payload, name="second.json")
        frozen = plan2["engine_state"]["snapshot_reconciliation"]
        assert frozen["status"] == "adjusted"
        assert frozen["against"]["as_of"] == "2026-07-10"
        # Facts only, in the declared as-of window: the 2026-07-12 buy counts
        # (derived 25), the 2026-07-16 buy does not (SPY stays clean).
        assert frozen["diff"]["positions"] == [
            {"ticker": "PLTR", "kind": "shares", "derived": 25.0, "declared": 30.0}]
        assert frozen["diff"]["cash"] == [
            {"currency": "USD", "derived": 1000.0, "declared": 800.0}]
        assert plan2["input"]["ledger_ingest"]["reconciliation"] == "adjusted"
        summary = plan2["engine_card"]["data_integrity"]["snapshot_reconciliation"]
        assert summary["positions_changed"] == ["PLTR"] and summary["cash_currencies"] == ["USD"]
        honesty = {row["key"]: row for row in plan2["engine_card"]["honesty_ledger"]}
        assert honesty["snapshot_reconciliation"]["status"] == "adjusted"

        second = _finalize_snapshot_session(tmp, root, plan2, "second")
        assert second.returncode == 0, second.stdout + second.stderr
        snapshot_report = json.loads(second.stdout)["projection"]["rows"][0]
        assert snapshot_report["reconciliation"] == "adjusted"
        assert snapshot_report["appended"] == 2 and snapshot_report["projection_sequence"] == 2

        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == \
            ["snapshot", "trade", "trade", "adjustment", "snapshot"], \
            "history is preserved: old anchor and trades stay, adjustment precedes the new anchor"
        adjustment = rows[3]
        assert adjustment["adjustment_id"].startswith("adjust-")
        assert adjustment["reason"] == "snapshot_reconciliation"
        assert adjustment["diff"] == frozen["diff"]
        assert adjustment["against"]["as_of"] == "2026-07-10"
        assert rows[4]["snapshot_id"].startswith("snapshot-")
        assert rows[4]["projection_sequence"] == 2

        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        assert ledger_engine.latest_anchor(events)["as_of"] == "2026-07-15"
        derived = ledger_engine.derive_holdings(events)["holdings"]
        assert derived["PLTR"]["shares"] == 30, "holdings derive from the adopted anchor"
        assert derived["SPY"]["shares"] == 3, "post-adoption trades still apply on top"
        assert derived["PLTR"]["cycle_id"] == "PLTR#2026-07-15#1"

        retry = _finalize_snapshot_session(tmp, root, plan2, "second-retry")
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert json.loads(retry.stdout)["status"] == "no-op"
        assert _ledger_rows(root) == rows, \
            "an identical finalize replay appends neither a second adjustment nor a second anchor"


def test_second_snapshot_reconciled_marks_ledger_without_new_anchor():
    """The #220 clean path: agreement appends only a content-addressed
    reconciliation mark; the anchor, ordering numbers, and repair stay stable."""
    initial = {
        "as_of": "2026-07-10",
        "positions": [{"ticker": "SPY", "shares": 2, "avg_cost": 600,
                       "market": "US", "currency": "USD"}],
        "cash": {"USD": 1000},
    }
    matching = {
        "as_of": "2026-07-15",
        "positions": [{"ticker": "SPY", "shares": 2, "avg_cost": 600,
                       "market": "US", "currency": "USD"}],
        "cash": {"USD": 1000},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan1, _path = _snapshot_prepare(tmp, root, payload=initial, name="first.json")
        assert _finalize_snapshot_session(tmp, root, plan1, "first").returncode == 0
        plan2, _second = _snapshot_prepare(tmp, root, payload=matching, name="match.json")
        frozen = plan2["engine_state"]["snapshot_reconciliation"]
        assert frozen["status"] == "reconciled"
        assert frozen["diff"] == {"positions": [], "cash": []}
        assert plan2["input"]["ledger_ingest"]["reconciliation"] == "reconciled"

        second = _finalize_snapshot_session(tmp, root, plan2, "second")
        assert second.returncode == 0, second.stdout + second.stderr
        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == ["snapshot", "reconciliation"]
        mark = rows[1]
        assert mark["status"] == "reconciled"
        assert mark["reconciliation_id"].startswith("reconcile-")
        assert mark["date"] == "2026-07-15" and mark["against"]["as_of"] == "2026-07-10"
        assert mark["declared_snapshot_id"].startswith("snapshot-")

        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        assert ledger_engine.latest_anchor(events)["as_of"] == "2026-07-10", \
            "agreement never churns the anchor or the derived cycle identities"
        bundle = json.loads((root / "sessions" / plan2["session_id"] / "bundle.json").read_text())
        assert "projection_sequence" not in bundle["engine_state"], \
            "a clean reconciliation must not consume a root-wide ordering number"

        retry = _finalize_snapshot_session(tmp, root, plan2, "second-retry")
        assert retry.returncode == 0 and json.loads(retry.stdout)["status"] == "no-op"
        assert _ledger_rows(root) == rows

        (root / "ledger.jsonl").unlink()
        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr
        rebuilt = _ledger_rows(root)
        assert [row["type"] for row in rebuilt] == ["snapshot", "reconciliation"], \
            "repair rebuilds the mark from the canonical bundle without a second anchor"
        assert rebuilt[1]["reconciliation_id"] == mark["reconciliation_id"]


def test_second_snapshot_same_day_adoption_uses_projection_sequence():
    initial = {
        "as_of": "2026-07-16",
        "positions": [{"ticker": "SPY", "shares": 2, "market": "US", "currency": "USD"}],
    }
    corrected = {
        "as_of": "2026-07-16",
        "positions": [{"ticker": "SPY", "shares": 3, "market": "US", "currency": "USD"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan1, _path = _snapshot_prepare(tmp, root, payload=initial, name="first.json")
        assert _finalize_snapshot_session(tmp, root, plan1, "first").returncode == 0
        plan2, _second = _snapshot_prepare(tmp, root, payload=corrected, name="same-day.json")
        assert plan2["engine_state"]["snapshot_reconciliation"]["status"] == "adjusted"
        assert _finalize_snapshot_session(tmp, root, plan2, "second").returncode == 0
        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        anchors = [row for row in events if row.get("type") == "snapshot"]
        assert [row["projection_sequence"] for row in anchors] == [1, 2]
        adopted = ledger_engine.latest_anchor(events)
        assert adopted["projection_sequence"] == 2
        assert adopted["positions"][0]["shares"] == 3, \
            "the same-day tie-break adopts the newer declaration by sequence"


def test_second_snapshot_fail_closed_edges():
    """Incomplete second declarations, older-than-anchor views, and a ledger
    that changed after prepare all fail closed without partial writes."""
    initial = {
        "as_of": "2026-07-10",
        "positions": [{"ticker": "SPY", "shares": 2, "avg_cost": 600,
                       "market": "US", "currency": "USD"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan1, _path = _snapshot_prepare(tmp, root, payload=initial, name="first.json")
        assert _finalize_snapshot_session(tmp, root, plan1, "first").returncode == 0
        baseline = _ledger_rows(root)

        incomplete = _snapshot_json(tmp, payload={
            "as_of": "2026-07-15", "is_complete": False,
            "positions": [{"ticker": "SPY", "shares": 3, "market": "US", "currency": "USD"}],
        }, name="incomplete.json")
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                   incomplete, "--root", root, "--language", "en")
        assert run.returncode == 2
        assert "incomplete snapshot cannot reconcile" in run.stdout

        older = _snapshot_json(tmp, payload={
            "as_of": "2026-07-05",
            "positions": [{"ticker": "SPY", "shares": 1, "market": "US", "currency": "USD"}],
        }, name="older.json")
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                   older, "--root", root, "--language", "en")
        assert run.returncode == 2
        assert "older than the current ledger anchor" in run.stdout
        assert _ledger_rows(root) == baseline, "rejected declarations write nothing"

        # Drift between prepare and finalize: the frozen diff no longer matches
        # the ledger, so finalize refuses to write an unpreviewed adjustment.
        drifting = {
            "as_of": "2026-07-15",
            "positions": [{"ticker": "SPY", "shares": 3, "market": "US", "currency": "USD"}],
        }
        plan2, _second = _snapshot_prepare(tmp, root, payload=drifting, name="drift.json")
        assert plan2["engine_state"]["snapshot_reconciliation"]["status"] == "adjusted"
        ledger_engine.append_events(str(root / "ledger.jsonl"), [
            {"type": "trade", "date": "2026-07-12", "ticker": "SPY", "action": "buy",
             "qty": 1, "price": 610, "market": "US", "currency": "USD"}])
        stale = _finalize_snapshot_session(tmp, root, plan2, "stale")
        assert stale.returncode == 2
        assert "run prepare again" in stale.stdout
        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == ["snapshot", "trade"], \
            "a stale finalize must not write an adjustment or a new anchor"

        # Re-preparing recomputes honestly: the interleaved buy explains the
        # whole difference, so the same declaration is now simply reconciled.
        rerun = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                     _second, "--root", root, "--language", "en")
        assert rerun.returncode == 0, rerun.stdout + rerun.stderr
        replanned = json.loads(rerun.stdout)["review_plan"]
        assert replanned["engine_state"]["snapshot_reconciliation"]["status"] == "reconciled"


def test_snapshot_then_transactions_unlock_history_without_rewriting_anchor():
    payload = {
        "as_of": "2026-07-01",
        "positions": [
            {"ticker": "PLTR", "shares": 10, "avg_cost": 100,
             "market": "US", "currency": "USD"},
            {"ticker": "SPY", "shares": 2, "avg_cost": 600,
             "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload)
        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(_snapshot_answers(plan, commitment="skip")), encoding="utf-8")
        narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        anchor_before = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()][0]
        initial_bundle = json.loads(
            (root / "sessions" / plan["session_id"] / "bundle.json").read_text()
        )
        initial_theses = {row["ticker"]: row for row in initial_bundle["thesis_updates"]}

        # A weekly/incremental file contains only the post-anchor add.  Raw CSV
        # artifacts therefore see two PLTR shares and omit SPY; the ledger must
        # retain the complete anchor, add the two shares, and gate every raw
        # current-view claim without discarding history diagnostics.
        card, state = _artifacts(tmp)
        card_data = json.loads(card.read_text())
        state_data = json.loads(state.read_text())
        state_data.update({"date_start": "2026-07-02", "date_end": "2026-07-02", "n_held": 1})
        state_data["holdings"] = {
            "as_of": "2026-07-02", "derived_from": "trades_csv", "is_complete": False,
            "positions": {"PLTR": {
                "shares": 2, "cost": 220, "avg_cost": 110,
                "market": "US", "currency": "USD",
                "cycle_start": "2026-07-02", "cycle_id": "PLTR#2026-07-02#1",
                "add_count": 0, "decision_cursor": None,
            }},
        }
        state_data["metrics"]["n_holdings"] = 1
        sizing_raw = {"dim": "部位 sizing", "tier": 1, "triggered": True,
                      "severity": 0.9, "max_pct": 1.0, "max_ticker": "PLTR"}
        sizing_hole = {"dim": "部位 sizing", "severity": 0.9, "tier_weight": 1.0,
                       "number_line": "raw current sizing", "lens_rule": "size rule",
                       "lens_quote": "size quote", "raw": sizing_raw}
        card_data["dims_raw"].insert(0, sizing_raw)
        card_data["top_holes"].insert(0, sizing_hole)
        card_data["candidate_rules"].append({"dim": "部位 sizing", "rule": "size rule"})
        card_data["what_if"] = {"ticker": "PLTR", "loss": -100}
        card_data["ticker_diagnosis"] = [{"ticker": "PLTR", "tag": "raw-current"}]
        card.write_text(json.dumps(card_data, ensure_ascii=False), encoding="utf-8")
        state.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
        csv_path = pathlib.Path(tmp) / "incremental.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "PLTR,BUY,2,110,2026-07-02,Trade,US,USD\n",
            encoding="utf-8",
        )
        later = _run("prepare", csv_path, "--root", root, "--card-json", card,
                     "--state-json", state, "--session-nonce", "history-upgrade")
        assert later.returncode == 0, later.stdout + later.stderr
        later_plan = _pending_plan(root, later.stdout)
        assert later_plan["route"] == "weekly_review"
        later_state, later_card = later_plan["engine_state"], later_plan["engine_card"]
        positions = later_state["holdings"]["positions"]
        assert set(positions) == {"PLTR", "SPY"}
        assert positions["PLTR"]["shares"] == 12
        assert positions["PLTR"]["cycle_id"] == \
            plan["engine_state"]["holdings"]["positions"]["PLTR"]["cycle_id"]
        assert positions["PLTR"]["decision_cursor"].endswith("#add#1")
        assert positions["PLTR"]["observed_cycle_id"] == "PLTR#2026-07-02#1"
        assert later_plan["missing_thesis_positions"] == []
        active = {row["ticker"]: row for row in later_plan["state_snapshot"]["active_theses"]}
        assert {ticker: row["thesis_id"] for ticker, row in active.items()} == \
            {ticker: row["thesis_id"] for ticker, row in initial_theses.items()}
        add_questions = [row for row in later_plan["question_queue"]
                         if row.get("kind") == "add_thesis"]
        assert [row["ticker"] for row in add_questions] == ["PLTR"]
        assert add_questions[0]["prior_thesis_id"] == initial_theses["PLTR"]["thesis_id"]

        assert later_state["metrics"]["avgdown_count"] == 3
        assert later_state["metrics"]["max_pos_pct"] is None
        assert all(review_engine.card_renderer.dimension_id(row["dim"]) != "position_sizing"
                   for row in later_card["top_holes"])
        assert later_card["what_if"] is None and later_card["ticker_diagnosis"] == []
        assert later_card["overview"]["unrealized"] is None
        assert "accounting_reconciliation" in \
            later_plan["card_plan"]["required_honesty_keys"]
        reconciliation = later_plan["input"]["ledger_ingest"]["holdings_reconciliation"]
        assert reconciliation["status"] == "current_view_gated"

        events = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert events[0] == anchor_before and sum(row["type"] == "snapshot" for row in events) == 1
        assert sum(row["type"] == "trade" for row in events[1:]) == 1
        resumed = _run("prepare", csv_path, "--root", root, "--card-json", card,
                       "--state-json", state, "--session-nonce", "history-upgrade")
        assert resumed.returncode == 0 and json.loads(resumed.stdout)["status"] == "resumed"
        assert len((root / "ledger.jsonl").read_text().splitlines()) == len(events)


def test_snapshot_full_history_keeps_stable_thesis_and_current_surfaces():
    payload = {
        "as_of": "2026-07-01",
        "positions": [
            {"ticker": "PLTR", "shares": 10, "avg_cost": 100,
             "market": "US", "currency": "USD"},
            {"ticker": "SPY", "shares": 2, "avg_cost": 600,
             "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload)
        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(_snapshot_answers(plan, commitment="skip")), encoding="utf-8")
        narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        initial = json.loads((root / "sessions" / plan["session_id"] / "bundle.json").read_text())
        initial_ids = {row["ticker"]: row["thesis_id"] for row in initial["thesis_updates"]}

        card, state = _artifacts(tmp)
        card_data, state_data = json.loads(card.read_text()), json.loads(state.read_text())
        state_data.update({"date_start": "2026-06-01", "date_end": "2026-07-01", "n_held": 2})
        state_data["holdings"] = {
            "as_of": "2026-07-01", "derived_from": "trades_csv", "is_complete": False,
            "positions": {
                "PLTR": {"shares": 10, "cost": 1000, "avg_cost": 100,
                         "market": "US", "currency": "USD", "cycle_start": "2026-06-01",
                         "cycle_id": "PLTR#2026-06-01#1", "add_count": 0,
                         "decision_cursor": None},
                "SPY": {"shares": 2, "cost": 1200, "avg_cost": 600,
                        "market": "US", "currency": "USD", "cycle_start": "2026-06-01",
                        "cycle_id": "SPY#2026-06-01#1", "add_count": 0,
                        "decision_cursor": None},
            },
        }
        state_data["metrics"]["n_holdings"] = 2
        card.write_text(json.dumps(card_data, ensure_ascii=False), encoding="utf-8")
        state.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
        history = pathlib.Path(tmp) / "full-history.csv"
        history.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "PLTR,BUY,10,100,2026-06-01,Trade,US,USD\n"
            "SPY,BUY,2,600,2026-06-01,Trade,US,USD\n",
            encoding="utf-8",
        )
        run = _run("prepare", history, "--root", root, "--card-json", card,
                   "--state-json", state, "--session-nonce", "full-history")
        assert run.returncode == 0, run.stdout + run.stderr
        upgraded = _pending_plan(root, run.stdout)
        positions = upgraded["engine_state"]["holdings"]["positions"]
        assert positions["PLTR"]["observed_cycle_id"] == "PLTR#2026-06-01#1"
        assert positions["PLTR"]["cycle_id"].endswith("#2026-07-01#1")
        assert upgraded["missing_thesis_positions"] == []
        active = {row["ticker"]: row["thesis_id"]
                  for row in upgraded["state_snapshot"]["active_theses"]}
        assert active == initial_ids
        assert not [row for row in upgraded["question_queue"]
                    if row.get("kind") == "add_thesis"]
        reconciliation = upgraded["input"]["ledger_ingest"]["holdings_reconciliation"]
        assert reconciliation["status"] == "matched"
        assert "accounting_reconciliation" not in \
            upgraded["card_plan"]["required_honesty_keys"]
        assert upgraded["engine_card"]["portfolio_structure"] == card_data["portfolio_structure"]


def test_snapshot_full_prices_do_not_hide_a_cost_basis_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        card_path, state_path = _artifacts(tmp)
        card = json.loads(card_path.read_text())
        state = json.loads(state_path.read_text())
        state["price_snapshot"] = {"prices": {"PLTR": 200}}
        state["holdings"]["positions"]["PLTR"].update({
            "market": "US", "currency": "USD",
        })
        derived = {"holdings": {"PLTR": {
            "shares": 10, "cost_total": 1500, "avg_cost": 150,
            "market": "US", "currency": "USD", "since": "2026-07-01",
            "cycle_id": "PLTR#2026-07-01#1", "origin": "snapshot",
            "add_count": 0, "decision_cursor": None,
        }}}

        gated_card, _gated_state, detail = review_engine._overlay_ledger_holdings(
            card, state, derived
        )

        assert detail["full_price_coverage"] is True
        assert detail["status"] == "current_view_gated"
        assert detail["mismatches"] == [{"ticker": "PLTR", "kind": "valuation"}]
        assert gated_card["overview"]["total_pnl"] is None
        assert gated_card["overview"]["unrealized"] is None
        assert {row["key"] for row in gated_card["honesty_ledger"]} >= {
            "accounting_reconciliation"
        }


def test_snapshot_raw_market_defaults_cannot_mask_a_non_us_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        card_path, state_path = _artifacts(tmp)
        card = json.loads(card_path.read_text())
        state = json.loads(state_path.read_text())
        state["price_snapshot"] = {"prices": {"PLTR": 200}}
        raw = state["holdings"]["positions"]["PLTR"]
        raw.pop("market", None)
        raw.pop("currency", None)
        derived = {"holdings": {"PLTR": {
            "shares": 10, "cost_total": 1000, "avg_cost": 100,
            "market": "TW", "currency": "TWD", "since": "2026-07-01",
            "cycle_id": "PLTR#2026-07-01#1", "origin": "snapshot",
            "add_count": 0, "decision_cursor": None,
        }}}

        _card, _state, detail = review_engine._overlay_ledger_holdings(card, state, derived)

        assert detail["status"] == "current_view_gated"
        assert detail["mismatches"] == [
            {"ticker": "PLTR", "kind": "market"},
            {"ticker": "PLTR", "kind": "currency"},
        ]


def test_snapshot_full_exit_and_reopen_requires_a_new_thesis_cycle():
    payload = {
        "as_of": "2026-07-01",
        "positions": [{"ticker": "PLTR", "shares": 10, "avg_cost": 100,
                       "market": "US", "currency": "USD"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload)
        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(_snapshot_answers(plan, commitment="skip")), encoding="utf-8")
        narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr

        card, state = _artifacts(tmp)
        state_data = json.loads(state.read_text())
        state_data.update({"date_start": "2026-07-02", "date_end": "2026-07-03", "n_held": 1})
        state_data["holdings"] = {
            "as_of": "2026-07-03", "derived_from": "trades_csv", "is_complete": False,
            "positions": {"PLTR": {"shares": 5, "cost": 600, "avg_cost": 120,
                                      "market": "US", "currency": "USD",
                                      "cycle_start": "2026-07-03",
                                      "cycle_id": "PLTR#2026-07-03#1",
                                      "add_count": 0, "decision_cursor": None}},
        }
        state_data["metrics"]["n_holdings"] = 1
        state.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
        history = pathlib.Path(tmp) / "reopen.csv"
        history.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "PLTR,SELL,10,110,2026-07-02,Trade,US,USD\n"
            "PLTR,BUY,5,120,2026-07-03,Trade,US,USD\n",
            encoding="utf-8",
        )
        run = _run("prepare", history, "--root", root, "--card-json", card,
                   "--state-json", state, "--session-nonce", "reopen")
        assert run.returncode == 0, run.stdout + run.stderr
        upgraded = _pending_plan(root, run.stdout)
        position = upgraded["engine_state"]["holdings"]["positions"]["PLTR"]
        assert position["cycle_id"] == "PLTR#2026-07-03#2"
        assert position["origin"] == "trades" and position["left_truncated"] is False
        # The uncovered-cycle row forwards engine-owned provenance (#251) so the
        # agent can ground the inferred thesis without reading engine_state.
        assert upgraded["missing_thesis_positions"] == [
            {"ticker": "PLTR", "cycle_id": "PLTR#2026-07-03#2", "origin": "trades"}
        ]


def test_snapshot_precomputed_artifacts_remain_a_developer_compatibility_path():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        run = _run("prepare", "--route", "snapshot_review", "--root", root,
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        assert plan["route"] == "snapshot_review" and plan["question_queue"] == []
        assert plan["input"]["ledger_ingest"] is None
        assert not (root / "ledger.jsonl").exists()


def test_prepare_is_resumable_without_rerunning_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root)
        resumed = _run("resume", "--root", root, "--session-id", plan["session_id"])
        assert resumed.returncode == 0 and json.loads(resumed.stdout)["plan"]["session_id"] == plan["session_id"]
        card, state = _artifacts(tmp)
        again = _run("prepare", "--root", root, "--card-json", card, "--state-json", state,
                     "--language", "zh-TW")
        assert json.loads(again.stdout)["status"] == "resumed"


def test_prepare_with_cash_anchor_opens_a_new_session_not_a_silent_resume():
    """#369, the #289 class for cash: the weekly flow resolves the cash anchor
    after the tier gate, so the legitimate call order is a cash-less prepare
    (which is what produces the tier) followed by `prepare --cash` once the
    user confirms the balance. Cash therefore participates in the session
    fingerprint; without it the second call would resume the cash-less pending
    session and silently discard the anchor -- exactly what #289 fixed for the
    price envelope."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        first = _run("prepare", "--root", root, "--card-json", card, "--state-json", state)
        assert first.returncode == 0, first.stdout + first.stderr
        anchor = '{"currency":"USD","amount":8200,"as_of":"2024-10-08"}'
        with_cash = _run("prepare", "--root", root, "--card-json", card,
                         "--state-json", state, "--cash", anchor)
        assert with_cash.returncode == 0, with_cash.stdout + with_cash.stderr
        assert json.loads(with_cash.stdout)["status"] != "resumed", \
            "prepare --cash must not silently resume the cash-less session"
        again = _run("prepare", "--root", root, "--card-json", card,
                     "--state-json", state, "--cash", anchor)
        assert json.loads(again.stdout)["status"] == "resumed", \
            "the same cash anchor rerun stays idempotent at its own fingerprint"


def test_stdout_plan_is_projected_for_the_agent_but_full_on_disk():
    """#234: the agent re-sends the emitted plan as context on every later turn,
    so prepare/resume stdout must carry only the fields the flow contract reads.
    engine_card and engine_state stay in the pending bundle on disk, where
    preview/finalize reload them. The one engine_state field the flow reads
    directly — snapshot_reconciliation — must survive the projection."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        run = _run("prepare", "--root", root, "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        # strict=True: the trimmed payload must be clean JSON (the engine blobs
        # were what carried the bare control character).
        stdout_plan = json.loads(run.stdout, strict=True)["review_plan"]
        assert "engine_card" not in stdout_plan
        assert "engine_state" not in stdout_plan
        for key in ("session_id", "question_queue", "card_plan", "state_snapshot",
                    "missing_thesis_positions", "flow_path"):
            assert key in stdout_plan, key
        disk = session_engine.load_pending(str(root), stdout_plan["session_id"])["plan"]
        assert "engine_card" in disk and "engine_state" in disk, \
            "the canonical pending bundle must keep the full plan"

        # The resumed-prepare and resume paths re-emit the plan; both project.
        again = _run("prepare", "--root", root, "--card-json", card, "--state-json", state)
        resumed = json.loads(again.stdout, strict=True)
        assert resumed["status"] == "resumed"
        assert "engine_card" not in resumed["review_plan"]
        assert "engine_state" not in resumed["review_plan"]
        cmd = _run("resume", "--root", root, "--session-id", stdout_plan["session_id"])
        resumed_bundle = json.loads(cmd.stdout, strict=True)
        assert "engine_card" not in resumed_bundle["plan"]
        assert resumed_bundle["plan"]["question_queue"] == stdout_plan["question_queue"]

        # Unit pin: snapshot_reconciliation is preserved, everything else drops.
        projected = review_engine._plan_for_agent({
            "session_id": "s", "engine_card": {"x": 1},
            "engine_state": {"holdings": {"y": 2},
                             "snapshot_reconciliation": {"events": []}}})
        assert projected == {"session_id": "s",
                             "engine_state": {"snapshot_reconciliation": {"events": []}}}


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
                        "--card-json", card, "--state-json", state,
                        "--language", "zh-TW")
        plan = json.loads(prepared.stdout)["review_plan"]
        assert plan["route"] == "test_drive" and plan["persist"] is False
        # #273: cross-client test-drive artifacts must stay attributable — the
        # engine_version provenance stamp (#250) covers this route too.
        version = plan.get("engine_version")
        assert isinstance(version, dict) and version.get("id"), \
            "test_drive plan missing engine_version provenance"
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


def test_prepare_completes_when_no_hole_and_no_headline_dimension():
    """#227: sample_insufficient (2 round trips, 41-day span) trips the
    insufficiency gate, so the card has no top hole and headline_dim is None.
    The generic motive fallback must skip instead of localizing None — an empty
    queue is the same contract the snapshot route returns. yfinance is stubbed
    to an ImportError so the real CSV build stays offline-deterministic."""
    mock = ROOT / "skills" / "fomo-kernel" / "mock"
    with tempfile.TemporaryDirectory() as tmp:
        stub_dir = pathlib.Path(tmp) / "stubs"
        stub_dir.mkdir()
        (stub_dir / "yfinance.py").write_text('raise ImportError("offline stub")\n',
                                              encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(stub_dir), env.get("PYTHONPATH")) if part)
        root = pathlib.Path(tmp) / "demo-root"
        for language in ("en", "zh-TW"):
            run = _run("prepare", mock / "sample_insufficient.csv", "--test-drive",
                       "--root", root / language, "--language", language,
                       "--driver-map", mock / "sample_insufficient.driver_map.json",
                       env=env)
            assert run.returncode == 0, run.stdout + run.stderr
            plan = json.loads(run.stdout)["review_plan"]
            assert plan["question_queue"] == [], \
                "no hole and no headline dimension must not fabricate a motive question"

        # Positive side of the same guard: when a hole exists and nothing else
        # fills the queue, the generic motive question must still appear.
        card, state = _artifacts(tmp)
        payload = json.loads(card.read_text(encoding="utf-8"))
        payload["thesis_questions"] = []
        card.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        for language in ("en", "zh-TW"):
            run = _run("prepare", "--test-drive", "--root", root / f"anchored-{language}",
                       "--language", language, "--card-json", card, "--state-json", state,
                       env=env)
            assert run.returncode == 0, run.stdout + run.stderr
            queue = json.loads(run.stdout)["review_plan"]["question_queue"]
            assert [q["id"] for q in queue] == ["headline_motive"]
            assert "None" not in queue[0]["question"]


def test_review_tier_frozen_into_plan_and_span_is_soft():
    """#306: the engine freezes a deterministic review_tier into the plan's
    state_snapshot. Round-trip COUNT decides behavioral vs structural; calendar
    span is advisory only (durability_short), so a high-frequency short-window
    file is NOT demoted the way the old ``rts<3 or span<84`` OR-gate would.
    Nothing consumes the field yet, so user-visible behavior is unchanged."""
    # 1) Direct classifier coverage, including the empty edge no fixture has.
    def _tier(**state):
        return review_engine._review_tier(state)
    assert _tier(n_round_trips=0, n_held=0)["tier"] == "empty"
    assert _tier(n_round_trips=0, n_held=3)["tier"] == "structural"
    assert _tier(n_round_trips=2, n_held=0)["tier"] == "structural"
    assert _tier(n_round_trips=3, n_held=0)["tier"] == "behavioral"
    # span is soft: 14 round trips in a 15-day window still promotes to behavioral
    short = _tier(n_round_trips=14, n_held=0, date_start="2026-01-01", date_end="2026-01-16")
    assert short["tier"] == "behavioral" and short["durability_short"] is True
    long_ = _tier(n_round_trips=8, n_held=4, date_start="2026-01-01", date_end="2026-12-01")
    assert long_["tier"] == "behavioral" and long_["durability_short"] is False
    # missing dates -> no span, fail-closed to not-short
    assert _tier(n_round_trips=0, n_held=0)["durability_short"] is False

    # 2) End-to-end: the tier is frozen into the plan. sample_insufficient has 2
    #    round trips over a 41-day span -> structural + durability_short.
    mock = ROOT / "skills" / "fomo-kernel" / "mock"
    with tempfile.TemporaryDirectory() as tmp:
        stub_dir = pathlib.Path(tmp) / "stubs"
        stub_dir.mkdir()
        (stub_dir / "yfinance.py").write_text('raise ImportError("offline stub")\n',
                                              encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(stub_dir), env.get("PYTHONPATH")) if part)
        run = _run("prepare", mock / "sample_insufficient.csv", "--test-drive",
                   "--root", pathlib.Path(tmp) / "root", "--language", "en",
                   "--driver-map", mock / "sample_insufficient.driver_map.json",
                   env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        tier = json.loads(run.stdout)["review_plan"]["state_snapshot"]["review_tier"]
        assert tier["tier"] == "structural", tier
        assert tier["n_round_trips"] == 2 and tier["durability_short"] is True, tier
        assert tier["min_round_trips"] == 3 and tier["min_span_days"] == 84, tier


def test_structural_first_review_suppresses_questions_and_routes_to_structural_flow():
    """#306: a thin first file (structural tier) must not trigger the 3-5
    question first-review interrogation. The engine forces the question band to
    zero and routes the agent to the structural flow; a behavioral first file is
    untouched. A real first review is used (not --test-drive, which forces the
    test_drive route)."""
    mock = ROOT / "skills" / "fomo-kernel" / "mock"
    with tempfile.TemporaryDirectory() as tmp:
        stub_dir = pathlib.Path(tmp) / "stubs"
        stub_dir.mkdir()
        (stub_dir / "yfinance.py").write_text('raise ImportError("offline stub")\n',
                                              encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(stub_dir), env.get("PYTHONPATH")) if part)

        # Structural: sample_value has 5 holdings but only 2 closed round trips.
        # Under the old first-review band those holdings would have produced a
        # string of initial-thesis questions; the tier gate now yields zero.
        run = _run("prepare", mock / "sample_value.csv",
                   "--root", pathlib.Path(tmp) / "structural", "--language", "en", env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        assert plan["route"] == "first_review"
        assert plan["state_snapshot"]["review_tier"]["tier"] == "structural"
        assert plan["question_queue"] == [], "a structural first file must ask no questions"
        assert plan["card_plan"]["question_policy"] == {
            "route": "first_review", "min": 0, "max": 0}
        assert plan["flow_path"] == "flows/first-review-structural.md"

        # Behavioral: mock_trades has 8 round trips -> the full first review is
        # untouched (density band and flow path unchanged).
        run2 = _run("prepare", mock / "mock_trades.csv",
                    "--root", pathlib.Path(tmp) / "behavioral", "--language", "en", env=env)
        assert run2.returncode == 0, run2.stdout + run2.stderr
        plan2 = json.loads(run2.stdout)["review_plan"]
        assert plan2["state_snapshot"]["review_tier"]["tier"] == "behavioral"
        assert plan2["flow_path"] == "flows/first-review.md"
        assert plan2["card_plan"]["question_policy"]["max"] == 5


def test_structural_card_next_step_names_the_unlock_path():
    """#306: a structural first-file card frames itself as an opening check and
    names what unlocks the full behavioral review. A behavioral tier must NOT get
    that line even when a short span sets insufficient_data, so a high-frequency
    short-window file is not mis-framed (span is soft at the render layer too)."""
    def _bundle(tier, n_round_trips):
        return {
            "schema_version": 2, "language": "en", "route": "first_review",
            "engine_card": {}, "commitment": None, "answers": {}, "thesis_updates": [],
            "narrative": {"headline": "h", "mirror": "m", "honesty": {}},
            "engine_state": {"date_start": "2026-01-01", "date_end": "2026-02-01",
                             "n_round_trips": n_round_trips, "n_held": 5,
                             "insufficient_data": True,  # short span in both cases
                             "review_tier": {"tier": tier}, "metrics": {},
                             "holdings": {"positions": {}}},
        }
    unlock = "unlocks the full behavioral review"
    assert unlock in card_renderer.render_private(_bundle("structural", 2))
    # behavioral (14 round trips) with a short-span insufficient flag must not be
    # framed as an opening structural check.
    assert unlock not in card_renderer.render_private(_bundle("behavioral", 14))


def test_canonical_bundle_fsyncs_artifacts_and_required_directories():
    """#194A: files and staging dir land before rename; parent dir lands after."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__durable"
        bundle = _minimal_bundle(session_id)
        events = []
        real_file = session_engine._fsync_file
        real_dir = session_engine._fsync_dir
        real_replace = session_engine.os.replace
        final = os.path.join(root, "sessions", session_id)

        def track_file(path):
            events.append(("file", str(path)))
            return real_file(path)

        def track_dir(path):
            events.append(("dir", str(path)))
            return real_dir(path)

        def track_replace(src, dst):
            if str(dst) == final:
                events.append(("replace", str(dst)))
            return real_replace(src, dst)

        session_engine._fsync_file = track_file
        session_engine._fsync_dir = track_dir
        session_engine.os.replace = track_replace
        try:
            result = session_engine.commit_bundle(
                root, bundle, "private", "public", "<html>private</html>")
        finally:
            session_engine._fsync_file = real_file
            session_engine._fsync_dir = real_dir
            session_engine.os.replace = real_replace

        assert result["status"] == "committed"
        file_names = {os.path.basename(path) for kind, path in events if kind == "file"}
        assert file_names == {
            "bundle.json", "state.json", "plan.json", "answers.json", "narrative.json",
            "card-private.md", "card-public.md", "card-private.html", "manifest.json",
        }
        staging_syncs = [index for index, (kind, path) in enumerate(events)
                         if kind == "dir" and os.path.basename(path).startswith(
                             f".{session_id}.staging-")]
        parent_syncs = [index for index, (kind, path) in enumerate(events)
                        if kind == "dir" and path == os.path.join(root, "sessions")]
        renames = [index for index, (kind, path) in enumerate(events)
                   if kind == "replace" and path == final]
        file_syncs = [index for index, (kind, _path) in enumerate(events) if kind == "file"]
        assert file_syncs and staging_syncs and renames and parent_syncs
        assert max(file_syncs) < staging_syncs[-1] < renames[0] < parent_syncs[0], \
            "required order is artifact fsync -> staging fsync -> rename -> sessions fsync"
        assert ("dir", root) in events, "creation of sessions/ must be persisted in its parent"


def test_existing_origin_writer_bundle_fsyncs_artifacts_then_manifest_then_directories():
    """An origin/main-visible bundle is not durable until every level is synced."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__existing-origin"
        bundle = _minimal_bundle(session_id)
        final = _write_pre_durability_canonical(root, bundle)
        sessions = final.parent
        events = []
        real_file = session_engine._fsync_file
        real_dir = session_engine._fsync_dir

        def track_file(path):
            if pathlib.Path(path).parent == final:
                events.append(("file", pathlib.Path(path).name))
            return real_file(path)

        def track_dir(path):
            if pathlib.Path(path) in {final, sessions}:
                events.append(("dir", str(pathlib.Path(path))))
            return real_dir(path)

        session_engine._fsync_file = track_file
        session_engine._fsync_dir = track_dir
        try:
            result = session_engine.commit_bundle(root, bundle, "private", "public")
        finally:
            session_engine._fsync_file = real_file
            session_engine._fsync_dir = real_dir

        assert result["status"] == "no-op"
        manifest_index = events.index(("file", "manifest.json"))
        artifact_indices = [index for index, event in enumerate(events)
                            if event[0] == "file" and event[1] != "manifest.json"]
        final_index = events.index(("dir", str(final)))
        sessions_index = events.index(("dir", str(sessions)))
        assert artifact_indices and max(artifact_indices) < manifest_index < final_index < sessions_index


def test_manifest_hash_mismatch_fails_closed_and_corrected_retry_adopts_bundle():
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__manifest-mismatch"
        bundle = _minimal_bundle(session_id)
        final = _write_pre_durability_canonical(root, bundle)
        private_card = final / "card-private.md"
        private_card.write_text("tampered\n", encoding="utf-8")
        try:
            session_engine.commit_bundle(root, bundle, "private", "public")
        except session_engine.SessionError as exc:
            error = str(exc)
        else:
            assert False, "manifest-bearing canonical artifacts must be hash verified"
        assert "canonical artifact hash mismatch: card-private.md" in error

        private_card.write_text("private\n", encoding="utf-8")
        retry = session_engine.commit_bundle(root, bundle, "private", "public")
        assert retry["status"] == "no-op"


def test_unverifiable_legacy_without_manifest_syncs_known_regular_artifacts():
    """No-manifest compatibility is explicit and limited to canonical files."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__unverifiable-legacy"
        bundle = _minimal_bundle(session_id)
        final = _write_pre_durability_canonical(root, bundle, manifest=False)
        synced = []
        real_file = session_engine._fsync_file

        def track_file(path):
            if pathlib.Path(path).parent == final:
                synced.append(pathlib.Path(path).name)
            return real_file(path)

        session_engine._fsync_file = track_file
        try:
            result = session_engine.commit_bundle(root, bundle, "private", "public")
        finally:
            session_engine._fsync_file = real_file
        assert result["status"] == "no-op"
        assert set(synced) == set(session_engine._REQUIRED_CANONICAL_ARTIFACTS)


def test_finalize_fsyncs_root_parent_when_pending_precreated_root():
    """prepare can create root first; finalize must still persist root's name."""
    with tempfile.TemporaryDirectory() as parent:
        root = os.path.join(parent, "new-coach-root")
        session_id = "2026-07-17__new-root"
        bundle = _minimal_bundle(session_id)
        session_engine.save_pending(root, session_id, plan=bundle["review_plan"])
        assert os.path.isdir(root) and not os.path.exists(os.path.join(root, "sessions"))
        events = []
        real_dir = session_engine._fsync_dir

        def track_dir(path):
            events.append(str(path))
            return real_dir(path)

        session_engine._fsync_dir = track_dir
        try:
            with session_engine.finalize_transaction(root, session_id) as transaction:
                result, projection, projection_error = transaction.commit_bundle(
                    bundle, "private", "public", persist=False)
        finally:
            session_engine._fsync_dir = real_dir

        assert result["status"] == "committed" and projection is None and not projection_error
        assert parent in events and root in events and events.index(parent) < events.index(root), \
            "finalize must persist a root created earlier by pending storage"


def test_unsupported_durable_platform_fails_at_a_controlled_boundary():
    """Missing POSIX locking must not make importing session.py crash."""
    with tempfile.TemporaryDirectory() as root:
        real_fcntl = session_engine.fcntl
        session_engine.fcntl = None
        try:
            try:
                session_engine.commit_bundle(
                    root, _minimal_bundle("2026-07-17__unsupported"), "private", "public")
            except session_engine.SessionError as exc:
                error = str(exc)
            else:
                assert False, "unsupported durability must fail closed"
        finally:
            session_engine.fcntl = real_fcntl

        assert "unsupported on this platform" in error
        assert not (pathlib.Path(root) / "sessions").exists(), \
            "the platform boundary must run before canonical storage mutation"


def test_directory_fsync_failure_is_controlled_and_retryable():
    """A visible rename without a durable parent entry reports SessionError;
    identical retry completes the sync and stays a no-op."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__fsync-failure"
        bundle = _minimal_bundle(session_id)
        sessions = os.path.join(root, "sessions")
        final = os.path.join(sessions, session_id)
        real_dir = session_engine._fsync_dir
        injected = {"done": False}

        def fail_after_rename(path):
            if path == sessions and os.path.isdir(final) and not injected["done"]:
                injected["done"] = True
                raise OSError("injected parent fsync failure")
            return real_dir(path)

        session_engine._fsync_dir = fail_after_rename
        try:
            try:
                session_engine.commit_bundle(root, bundle, "private", "public")
            except session_engine.SessionError as exc:
                error = str(exc)
            else:
                assert False, "parent fsync failure must not report a durable commit"
        finally:
            session_engine._fsync_dir = real_dir

        assert "committed but directory sync failed" in error and os.path.isdir(final)
        retry = session_engine.commit_bundle(root, bundle, "private", "public")
        assert retry["status"] == "no-op"


def test_old_writer_rename_race_sync_failure_is_controlled_and_retryable_in_order():
    """A lock-unaware writer can win rename; adoption still runs the full sync ladder."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__old-writer-race"
        bundle = _minimal_bundle(session_id)
        final = pathlib.Path(root) / "sessions" / session_id
        sessions = final.parent
        real_replace = session_engine.os.replace
        real_file = session_engine._fsync_file
        published = {"done": False}
        failed = {"done": False}

        def old_writer_wins(src, dst):
            if pathlib.Path(dst) == final and not published["done"]:
                published["done"] = True
                _write_pre_durability_canonical(root, bundle)
                raise OSError("injected old writer rename win")
            return real_replace(src, dst)

        def fail_final_manifest_once(path):
            path = pathlib.Path(path)
            if path.parent == final and path.name == "manifest.json" and not failed["done"]:
                failed["done"] = True
                raise OSError("injected existing manifest fsync failure")
            return real_file(path)

        session_engine.os.replace = old_writer_wins
        session_engine._fsync_file = fail_final_manifest_once
        try:
            try:
                session_engine.commit_bundle(root, bundle, "private", "public")
            except session_engine.SessionError as exc:
                error = str(exc)
            else:
                assert False, "old-writer adoption fsync failure must not report success"
        finally:
            session_engine.os.replace = real_replace
            session_engine._fsync_file = real_file

        assert published["done"] and final.is_dir()
        assert "cannot make existing session" in error

        events = []
        real_dir = session_engine._fsync_dir

        def track_file(path):
            if pathlib.Path(path).parent == final:
                events.append(("file", pathlib.Path(path).name))
            return real_file(path)

        def track_dir(path):
            if pathlib.Path(path) in {final, sessions}:
                events.append(("dir", str(pathlib.Path(path))))
            return real_dir(path)

        session_engine._fsync_file = track_file
        session_engine._fsync_dir = track_dir
        try:
            retry = session_engine.commit_bundle(root, bundle, "private", "public")
        finally:
            session_engine._fsync_file = real_file
            session_engine._fsync_dir = real_dir

        manifest_index = events.index(("file", "manifest.json"))
        artifact_indices = [index for index, event in enumerate(events)
                            if event[0] == "file" and event[1] != "manifest.json"]
        assert retry["status"] == "no-op" and artifact_indices
        assert max(artifact_indices) < manifest_index
        assert manifest_index < events.index(("dir", str(final))) \
            < events.index(("dir", str(sessions)))


def test_staging_gc_waits_for_canonical_final_then_cleans_same_session_only():
    """No TTL guess: a failed pre-rename attempt preserves unknown staging;
    the next successful canonical commit makes it provably orphaned and GC-able."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__staging-gc"
        bundle = _minimal_bundle(session_id)
        sessions = pathlib.Path(root) / "sessions"
        sessions.mkdir()
        stale = sessions / f".{session_id}.staging-crashed"
        stale.mkdir()
        (stale / "partial").write_text("partial", encoding="utf-8")
        unrelated = sessions / ".other-session.staging-crashed"
        unrelated.mkdir()
        real_write = session_engine.ledger.atomic_write_text

        def fail_write(_path, _text):
            raise OSError("injected artifact failure")

        session_engine.ledger.atomic_write_text = fail_write
        try:
            try:
                session_engine.commit_bundle(root, bundle, "private", "public")
            except session_engine.SessionError as exc:
                assert "cannot commit session" in str(exc)
            else:
                assert False, "injected artifact failure must abort before canonical rename"
        finally:
            session_engine.ledger.atomic_write_text = real_write

        assert stale.is_dir(), "without a canonical final there is no safe stale-age contract"
        assert not (sessions / session_id).exists()
        committed = session_engine.commit_bundle(root, bundle, "private", "public")
        assert committed["status"] == "committed" and not stale.exists()
        assert unrelated.is_dir(), "GC must stay scoped to the committed session id"


def test_staging_gc_and_cleanup_sync_are_best_effort_after_durable_commit():
    """Cleanup failure cannot invalidate or block an identical canonical retry."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__gc-best-effort"
        bundle = _minimal_bundle(session_id)
        committed = session_engine.commit_bundle(root, bundle, "private", "public")
        assert committed["status"] == "committed"
        sessions = pathlib.Path(root) / "sessions"
        stale = sessions / f".{session_id}.staging-crashed"
        stale.mkdir()

        real_cleanup = session_engine._cleanup_committed_staging

        def fail_cleanup(_sessions, _final, _session_id):
            raise OSError("injected staging cleanup failure")

        session_engine._cleanup_committed_staging = fail_cleanup
        try:
            retry = session_engine.commit_bundle(root, bundle, "private", "public")
        finally:
            session_engine._cleanup_committed_staging = real_cleanup
        assert retry["status"] == "no-op" and stale.is_dir()

        real_dir = session_engine._fsync_dir

        def fail_cleanup_sync(path):
            if str(path) == str(sessions) and not stale.exists():
                raise OSError("injected post-GC directory sync failure")
            return real_dir(path)

        session_engine._fsync_dir = fail_cleanup_sync
        try:
            retry = session_engine.commit_bundle(root, bundle, "private", "public")
        finally:
            session_engine._fsync_dir = real_dir
        assert retry["status"] == "no-op" and not stale.exists(), \
            "post-GC fsync is non-authoritative once canonical parent fsync succeeded"


def _forced_commit_race(root, first_bundle, second_bundle):
    """Hold the first directory rename so the second writer is truly concurrent."""
    final = os.path.join(root, "sessions", first_bundle["session_id"])
    real_replace = session_engine.os.replace
    first_entered = threading.Event()
    second_entered = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    call_count = {"value": 0}
    call_lock = threading.Lock()

    def gated_replace(src, dst):
        is_commit = (dst == final and os.path.basename(src).startswith(
            f".{first_bundle['session_id']}.staging-"))
        if is_commit:
            with call_lock:
                call_count["value"] += 1
                index = call_count["value"]
            if index == 1:
                first_entered.set()
                if not release_first.wait(5):
                    raise RuntimeError("timed out waiting to release forced commit race")
            else:
                second_entered.set()
        return real_replace(src, dst)

    def second_call():
        second_started.set()
        return session_engine.commit_bundle(root, second_bundle, "private", "public")

    session_engine.os.replace = gated_replace
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        first = pool.submit(session_engine.commit_bundle, root, first_bundle, "private", "public")
        assert first_entered.wait(5), "first writer never reached canonical rename"
        second = pool.submit(second_call)
        assert second_started.wait(5), "second writer never started"
        serialized = not second_entered.wait(0.5)
        release_first.set()
        outcomes = []
        for future in (first, second):
            try:
                outcomes.append(("ok", future.result(timeout=5)))
            except Exception as exc:  # returned for assertions below
                outcomes.append(("error", exc))
    finally:
        release_first.set()
        pool.shutdown(wait=True)
        session_engine.os.replace = real_replace
    return serialized, outcomes


def test_concurrent_bundle_commit_serializes_identical_and_conflicting_retries():
    """Canonical writers serialize; same content no-ops and conflicts fail closed."""
    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__identical-race"
        bundle = _minimal_bundle(session_id)
        serialized, outcomes = _forced_commit_race(root, bundle, bundle)
        assert serialized, "same-session writers must not enter bundle rename concurrently"
        assert sorted(result["status"] for kind, result in outcomes if kind == "ok") == \
            ["committed", "no-op"]
        assert all(kind == "ok" for kind, _value in outcomes)

    with tempfile.TemporaryDirectory() as root:
        session_id = "2026-07-17__conflict-race"
        first = _minimal_bundle(session_id, marker="first")
        second = _minimal_bundle(session_id, marker="second")
        serialized, outcomes = _forced_commit_race(root, first, second)
        assert serialized
        successes = [value for kind, value in outcomes if kind == "ok"]
        errors = [value for kind, value in outcomes if kind == "error"]
        assert len(successes) == len(errors) == 1 and successes[0]["status"] == "committed"
        assert isinstance(errors[0], session_engine.SessionError)
        assert "already committed with different content" in str(errors[0])
        assert not isinstance(errors[0], OSError), "CLI catch boundary must receive SessionError"


def test_cross_session_projections_serialize_shared_legacy_books():
    """Different session locks still share one root-wide projection lock."""
    with tempfile.TemporaryDirectory() as root:
        first = _minimal_bundle("2026-07-17__projection-a")
        second = _minimal_bundle("2026-07-17__projection-b")
        event = {"key": "avgdown_breach", "kind": "event", "week": "2026-07-17",
                 "ticker": "PLTR", "amount": 1, "note": "same event"}
        for bundle in (first, second):
            bundle["engine_state"].update({
                "problem_events": [event],
                "problem_opportunities": {"avgdown_breach": True},
            })

        real_append = session_engine.problems.append_book
        first_entered = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        call_count = {"value": 0}
        call_lock = threading.Lock()

        def gated_append(*args, **kwargs):
            with call_lock:
                call_count["value"] += 1
                index = call_count["value"]
            if index == 1:
                first_entered.set()
                if not release_first.wait(5):
                    raise RuntimeError("timed out waiting to release shared projection")
            else:
                second_entered.set()
            return real_append(*args, **kwargs)

        session_engine.problems.append_book = gated_append
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            a = pool.submit(session_engine.project_legacy, root, first, "private a\n")
            assert first_entered.wait(5), "first projection never reached the shared problem book"
            b = pool.submit(session_engine.project_legacy, root, second, "private b\n")
            assert not second_entered.wait(0.5), \
                "cross-session projections must not enter shared books concurrently"
            release_first.set()
            assert a.result(timeout=5)["session_id"] == first["session_id"]
            assert b.result(timeout=5)["session_id"] == second["session_id"]
        finally:
            release_first.set()
            pool.shutdown(wait=True)
            session_engine.problems.append_book = real_append

        events, marks, skipped = session_engine.problems.load_book(
            os.path.join(root, "problems.jsonl"))
        assert not skipped and len(events) == len(marks) == 1, \
            "shared event/mark dedupe must survive different-session finalizers"


def test_trade_ingest_and_initial_snapshot_share_one_root_boundary_lock():
    """A trade append that wins the lock makes the initial snapshot fail closed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card_path, state_path = _artifacts(tmp)
        card = json.loads(card_path.read_text())
        state = json.loads(state_path.read_text())
        csv_path = pathlib.Path(tmp) / "race-trade.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "PLTR,BUY,1,100,2026-07-17,Trade,US,USD\n",
            encoding="utf-8",
        )
        snapshot = _runtime_snapshot_bundle("2026-07-17__snapshot-after-trade")
        ledger_path = root / "ledger.jsonl"

        real_append = review_engine.ledger.append_events
        real_boundary = session_engine._assert_initial_snapshot_boundary
        append_entered = threading.Event()
        boundary_entered = threading.Event()
        release_append = threading.Event()

        def gated_append(path, events):
            if os.path.abspath(path) == os.path.abspath(ledger_path):
                append_entered.set()
                if not release_append.wait(5):
                    raise RuntimeError("timed out waiting to release trade append")
            return real_append(path, events)

        def observed_boundary(*args, **kwargs):
            boundary_entered.set()
            return real_boundary(*args, **kwargs)

        review_engine.ledger.append_events = gated_append
        session_engine._assert_initial_snapshot_boundary = observed_boundary
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            ingest = pool.submit(
                review_engine._ingest_trades, str(root), [str(csv_path)], card, state
            )
            assert append_entered.wait(5), "trade ingest never reached its locked append"
            finalize = pool.submit(_direct_finalize, str(root), snapshot)
            assert not boundary_entered.wait(0.5), \
                "snapshot boundary ran while the trade ledger transaction held the root lock"
            release_append.set()
            ingest_result, _card, _state = ingest.result(timeout=5)
            assert ingest_result["appended"] == 1
            try:
                finalize.result(timeout=5)
                raise AssertionError("snapshot crossed trade history instead of failing closed")
            except session_engine.SessionError as exc:
                assert "existing coach history" in str(exc)
        finally:
            release_append.set()
            pool.shutdown(wait=True)
            review_engine.ledger.append_events = real_append
            session_engine._assert_initial_snapshot_boundary = real_boundary

        rows = session_engine._read_jsonl(str(ledger_path))
        assert [row["type"] for row in rows] == ["trade"]
        assert not os.path.isdir(
            session_engine.session_dir(str(root), snapshot["session_id"])
        )


def test_ingest_trades_fails_closed_on_a_corrupt_existing_ledger():
    """#462: _ingest_trades reads the existing ledger before deriving the
    overlay holdings a card/reconciliation get built from (the `if
    ledger.latest_anchor(existing) is not None:` branch a few lines below its
    own load_ledger call). A corrupt row in that existing history must block
    the import instead of letting the overlay compute over a silently
    shortened read."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir()
        card_path, state_path = _artifacts(tmp)
        card = json.loads(card_path.read_text())
        state = json.loads(state_path.read_text())
        csv_path = pathlib.Path(tmp) / "new-trade.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "PLTR,BUY,1,100,2026-07-17,Trade,US,USD\n",
            encoding="utf-8",
        )
        (root / "ledger.jsonl").write_text(
            json.dumps({"type": "trade", "date": "2026-07-01", "ticker": "NVDA",
                        "action": "buy", "qty": 10, "price": 100.0}) + "\n"
            + "not json at all\n",
            encoding="utf-8")
        try:
            review_engine._ingest_trades(str(root), [str(csv_path)], card, state)
            raise AssertionError("a corrupt existing ledger must not let ingest proceed")
        except review_engine.ReviewError as exc:
            assert "unreadable row(s)" in str(exc), str(exc)
        # session_engine._read_jsonl is a separate, more lenient reader (it
        # drops an unparseable line with no count at all) -- read the raw
        # bytes instead so this assertion does not depend on that reader's
        # own tolerance and stays a direct check of "nothing new was written".
        raw = (root / "ledger.jsonl").read_text(encoding="utf-8")
        assert "PLTR" not in raw, "a failed ingest must not have appended the new trade"
        assert raw.count("\n") == 2, "a failed ingest must not have touched the existing file"


def test_prepare_exit_capture_fails_closed_on_a_corrupt_ledger():
    """#462: _prepare_exit_capture's enqueue_from_ledger call must surface as
    a ReviewError at the review.py boundary, not a bare exception escaping
    unwrapped past review.py's own error-handling convention."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir()
        (root / "ledger.jsonl").write_text(
            json.dumps({"type": "trade", "date": "2026-07-01", "ticker": "NVDA",
                        "action": "buy", "qty": 10, "price": 100.0}) + "\n"
            + json.dumps({"type": "trade", "date": "2026-07-10", "ticker": "NVDA",
                          "action": "sell", "qty": 10, "price": 110.0}) + "\n"  # a real exit
            + "not json at all\n",
            encoding="utf-8")
        state = {"date_end": "2026-07-17"}
        try:
            review_engine._prepare_exit_capture(str(root), state, True)
            raise AssertionError("a corrupt ledger must not let exit capture proceed")
        except review_engine.ReviewError as exc:
            assert "unreadable row(s)" in str(exc), str(exc)
        assert not os.path.exists(str(root / "revisit.jsonl")), \
            "a failed exit capture must not have written a partial queue"


def test_persistent_review_commit_cannot_appear_inside_snapshot_check_and_commit():
    """A non-snapshot canonical commit that wins the lock blocks onboarding."""
    with tempfile.TemporaryDirectory() as root:
        weekly = _minimal_bundle("2026-07-17__weekly-wins")
        weekly.update({
            "route": "weekly_review",
            "review_plan": {"persist": True, "input": {"kind": "trades_csv"}},
            "engine_state": {"date_end": "2026-07-17", "metrics": {},
                             "problem_events": []},
        })
        snapshot = _runtime_snapshot_bundle("2026-07-17__snapshot-loses")

        real_commit = session_engine._commit_bundle_locked
        real_boundary = session_engine._assert_initial_snapshot_boundary
        weekly_commit_entered = threading.Event()
        boundary_entered = threading.Event()
        release_weekly = threading.Event()

        def gated_commit(root_arg, sessions, bundle, *args, **kwargs):
            if bundle.get("session_id") == weekly["session_id"]:
                weekly_commit_entered.set()
                if not release_weekly.wait(5):
                    raise RuntimeError("timed out waiting to release weekly commit")
            return real_commit(root_arg, sessions, bundle, *args, **kwargs)

        def observed_boundary(*args, **kwargs):
            boundary_entered.set()
            return real_boundary(*args, **kwargs)

        session_engine._commit_bundle_locked = gated_commit
        session_engine._assert_initial_snapshot_boundary = observed_boundary
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            weekly_future = pool.submit(_direct_finalize, root, weekly)
            assert weekly_commit_entered.wait(5), "weekly finalize never reached canonical commit"
            snapshot_future = pool.submit(_direct_finalize, root, snapshot)
            assert not boundary_entered.wait(0.5), \
                "snapshot boundary ran while another persistent commit held the root lock"
            release_weekly.set()
            assert weekly_future.result(timeout=5)[0]["status"] == "committed"
            try:
                snapshot_future.result(timeout=5)
                raise AssertionError("snapshot crossed canonical review history")
            except session_engine.SessionError as exc:
                assert "existing coach history" in str(exc)
        finally:
            release_weekly.set()
            pool.shutdown(wait=True)
            session_engine._commit_bundle_locked = real_commit
            session_engine._assert_initial_snapshot_boundary = real_boundary

        assert os.path.isdir(session_engine.session_dir(root, weekly["session_id"]))
        assert not os.path.isdir(session_engine.session_dir(root, snapshot["session_id"]))


def test_concurrent_identical_finalize_cli_is_controlled_and_projects_once():
    """Two real CLI processes: one commits, one fails busy, later retry is no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root)
        answers_path = pathlib.Path(tmp) / "answers-concurrent.json"
        narrative_path = pathlib.Path(tmp) / "narrative-concurrent.json"
        answers_path.write_text(
            json.dumps(_answers(plan, commitment="candidate_0"), ensure_ascii=False),
            encoding="utf-8",
        )
        narrative_path.write_text(
            json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")

        # The wrapper still executes review.py's full parser/command path in a
        # separate OS process.  One process pauses after observing pending/ but
        # before opening plan.json.  Without an outer finalize transaction the
        # other process can remove pending/ and force a raw FileNotFoundError.
        barrier = pathlib.Path(tmp) / "barrier"
        barrier.mkdir()
        wrapper = pathlib.Path(tmp) / "concurrent_finalize_cli.py"
        wrapper.write_text(
            """import json
import os
import pathlib
import sys
import time

engine_dir = sys.argv[1]
barrier = pathlib.Path(sys.argv[2])
cli = sys.argv[3:]
sys.path.insert(0, engine_dir)
import session

real_load_pending = session.load_pending
def gated_load_pending(root, session_id):
    claim = barrier / "pending-reader.claim"
    try:
        claim.mkdir()
        owner = True
    except FileExistsError:
        owner = False
    if owner:
        base = pathlib.Path(session.pending_dir(root, session_id))
        if base.is_dir():
            (barrier / "pending-reader-entered").touch()
            deadline = time.monotonic() + 20
            while not (barrier / "release-pending-reader").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("pending reader release timed out")
                time.sleep(0.01)
            # Deliberately open after the earlier existence observation.  This
            # is the real TOCTOU window the outer session lock must eliminate.
            with (base / "plan.json").open(encoding="utf-8") as handle:
                json.load(handle)
    else:
        (barrier / "second-pending-reader-entered").touch()
    return real_load_pending(root, session_id)
session.load_pending = gated_load_pending

(barrier / (str(os.getpid()) + ".ready")).touch()
deadline = time.monotonic() + 10
while len(list(barrier.glob("*.ready"))) < 2:
    if time.monotonic() > deadline:
        raise RuntimeError("concurrent CLI start barrier timed out")
    time.sleep(0.01)

sys.argv = [str(pathlib.Path(engine_dir) / "review.py"), *cli]
import runpy
runpy.run_path(sys.argv[0], run_name="__main__")
""",
            encoding="utf-8",
        )
        command = [
            sys.executable, str(wrapper), str(ENGINE_DIR), str(barrier),
            "finalize", "--root", str(root), "--session-id", plan["session_id"],
            "--answers", str(answers_path), "--narrative", str(narrative_path),
        ]
        processes = [subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True) for _ in range(2)]
        pending_reader = barrier / "pending-reader-entered"
        second_reader = barrier / "second-pending-reader-entered"
        deadline = time.monotonic() + 15
        while not pending_reader.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        deadline = time.monotonic() + 15
        while all(process.poll() is None for process in processes) and time.monotonic() < deadline:
            time.sleep(0.01)
        pre_release_codes = [process.poll() for process in processes if process.poll() is not None]
        second_reader_entered = second_reader.exists()
        (barrier / "release-pending-reader").touch()
        completed = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            completed.append((process.returncode, json.loads(stdout), stderr))

        assert pending_reader.exists(), "one finalize never reached the gated pending read"
        assert pre_release_codes == [2], \
            "the overlapping finalize must fail busy while the winner still reads pending"
        assert not second_reader_entered, \
            "the loser must be rejected before touching pending session files"
        assert sorted(code for code, _payload, _stderr in completed) == [0, 2]
        success = next(payload for code, payload, _stderr in completed if code == 0)
        busy = next(payload for code, payload, _stderr in completed if code == 2)
        assert success["status"] == "committed" and not success["projection_error"]
        assert "finalize already in progress for session" in busy["error"]
        assert all("Traceback" not in stderr for _code, _payload, stderr in completed)

        retry = _run(
            "finalize", "--root", root, "--session-id", plan["session_id"],
            "--answers", answers_path, "--narrative", narrative_path,
        )
        retry_payload = json.loads(retry.stdout)
        assert retry.returncode == 0 and retry_payload["status"] == "no-op"
        assert not retry_payload["projection_error"]

        def session_rows(name):
            path = root / name
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and json.loads(line).get("session_id") == plan["session_id"]]

        assert len(session_rows("log.jsonl")) == 1
        assert len(session_rows("rules.jsonl")) == 1
        problem_rows = session_rows("problems.jsonl")
        assert sorted(row["type"] for row in problem_rows) == ["event", "review_mark"], \
            "identical concurrent finalize must not duplicate problem events or marks"


def test_rule_grounding_facts_and_localization():
    """#248 unit: candidate-rule grounding selects deterministic tickers from
    existing engine-card facts, localizes through the copy templates in both
    languages, and stays silent when a dimension has nothing citable."""
    card = {
        "dims_raw": [
            {"dim": "加碼攤平", "count": 6, "breach": 2, "tickers": ["CVS", "INTC", "PYPL"]},
            {"dim": "部位 sizing", "max_ticker": "INTC", "max_pct": 0.431,
             "risk_weights": {"INTC": 0.431, "CVS": 0.2, "PYPL": 0.15, "F": 0.1}},
            {"dim": "分散", "top3": 0.784},
            {"dim": "持有時間", "incon_tickers": ["ABNB", "SHOP", "UBER"], "n_incon": 3},
            {"dim": "出場紀律", "disp_gap": 40.0},
        ],
        "ticker_diagnosis": [{"ticker": "INTC", "impact": -900.0},
                             {"ticker": "PYPL", "impact": 300.0}],
    }
    zh = card_renderer.localized_rule_grounding("加碼攤平", "zh-TW", card)
    assert "INTC、PYPL" in zh and "6 次" in zh and "CVS" not in zh, zh  # |impact| order, capped at 2
    en = card_renderer.localized_rule_grounding("averaging_down", "en", card)
    assert "INTC, PYPL" in en and "6 times" in en, en
    size = card_renderer.localized_rule_grounding("部位 sizing", "zh-TW", card)
    assert "INTC" in size and "43%" in size, size
    div = card_renderer.localized_rule_grounding("diversification", "en", card)
    assert "INTC, CVS, PYPL" in div and "78%" in div, div  # top 3 by sizing risk weight
    hold = card_renderer.localized_rule_grounding("持有時間", "zh-TW", card)
    assert "ABNB、SHOP" in hold and "UBER" not in hold, hold  # capped at 2
    # exit_discipline has no per-ticker fact in the engine card -> no grounding
    assert card_renderer.localized_rule_grounding("出場紀律", "zh-TW", card) is None
    # Graceful absence: missing or empty facts never produce an empty shell.
    assert card_renderer.localized_rule_grounding(
        "加碼攤平", "zh-TW",
        {"dims_raw": [{"dim": "加碼攤平", "count": 0, "breach": 0, "tickers": []}]}) is None
    assert card_renderer.localized_rule_grounding("部位 sizing", "en", {}) is None
    assert card_renderer.localized_rule_grounding(
        "分散", "en", {"dims_raw": [{"dim": "分散", "top3": 0.7}]}) is None
    # Payload contract: _candidate_rules attaches grounding only when citable.
    bare = {"candidate_rules": [{"dim": "加碼攤平", "rule": "r"}], "top_holes": []}
    rows = review_engine._candidate_rules(bare, {"metrics": {"avgdown_count": 2}}, "zh-TW")
    assert rows and rows[0]["dim"] == "averaging_down" and "grounding" not in rows[0], rows
    grounded = review_engine._candidate_rules(card | bare, {"metrics": {"avgdown_count": 6}}, "en")
    assert grounded and "INTC, PYPL" in grounded[0]["grounding"], grounded


def test_candidate_comparison_reflects_severity_not_list_order_and_degrades_cleanly():
    """#302(c): the interaction-layer "why the other candidates ranked lower"
    sentence must follow the same severity x tier-weight key `_rank_holes`
    uses (`trade_recap.HEADLINE_TIER_W`), not the candidate list's own
    insertion/display order, and must degrade to None -- never an empty or
    dangling string -- whenever an honest ranking claim cannot be made.

    Insertion order below deliberately puts the LOWER-severity dimension
    first: `trade_recap.prescribe()` always emits averaging_down's rule
    before position_sizing's, regardless of which is more severe this
    period (see review.py:_candidate_rules' `source` construction). A test
    that only used a persona where severity happens to agree with insertion
    order (as every current mock persona does) would not catch a regression
    that silently swapped in list position instead of severity.
    """
    card = {
        "candidate_rules": [{"dim": "加碼攤平", "rule": "r1"}, {"dim": "部位 sizing", "rule": "r2"}],
        "top_holes": [],
        "dims_raw": [
            {"dim": "加碼攤平", "tier": 1, "severity": 0.3},   # lower severity, listed first
            {"dim": "部位 sizing", "tier": 1, "severity": 0.9},  # higher severity, listed second
        ],
    }
    state = {"metrics": {"avgdown_count": 5, "max_pos_pct": 0.9}}
    candidates = review_engine._candidate_rules(card, state, "en")
    assert [c["dim"] for c in candidates] == ["averaging_down", "position_sizing"], candidates

    en = review_engine._candidate_comparison(candidates, card, "en")
    assert en == ("position sizing scored higher than averaging-down discipline on this "
                  "period's severity ranking -- that reflects which pattern showed up more "
                  "strongly this period, not which rule is the right fit for you."), en
    zh = review_engine._candidate_comparison(candidates, card, "zh-TW")
    assert zh == ("本期「部位 sizing」的訊號比「加碼攤平」更強——"
                  "這只反映本期哪個模式更明顯，不代表哪條規矩更適合你。"), zh

    # Single candidate: clean degrade to None, not an empty or dangling sentence.
    one = review_engine._candidate_rules(
        {"candidate_rules": [{"dim": "加碼攤平", "rule": "r1"}], "top_holes": [],
         "dims_raw": [{"dim": "加碼攤平", "tier": 1, "severity": 0.3}]},
        {"metrics": {"avgdown_count": 5}}, "en")
    assert len(one) == 1
    assert review_engine._candidate_comparison(one, card, "en") is None

    # Zero candidates: same clean degrade.
    assert review_engine._candidate_comparison([], card, "en") is None

    # A tie at the top is not an honest "ranked lower" claim -> None rather
    # than an arbitrary pick between equals.
    tie_card = {
        "candidate_rules": [{"dim": "加碼攤平", "rule": "r1"}, {"dim": "部位 sizing", "rule": "r2"}],
        "top_holes": [],
        "dims_raw": [
            {"dim": "加碼攤平", "tier": 1, "severity": 0.5},
            {"dim": "部位 sizing", "tier": 1, "severity": 0.5},
        ],
    }
    tie_state = {"metrics": {"avgdown_count": 5, "max_pos_pct": 0.9}}
    tie_candidates = review_engine._candidate_rules(tie_card, tie_state, "en")
    assert len(tie_candidates) == 2
    assert review_engine._candidate_comparison(tie_candidates, tie_card, "en") is None

    # A candidate whose severity cannot be located in dims_raw (e.g. an
    # adapter-built card with a partial fact source): fail closed instead of
    # silently comparing only the candidates it can see.
    incomplete_card = {
        "candidate_rules": [{"dim": "加碼攤平", "rule": "r1"}, {"dim": "部位 sizing", "rule": "r2"}],
        "top_holes": [],
        "dims_raw": [{"dim": "加碼攤平", "tier": 1, "severity": 0.3}],  # position_sizing missing
    }
    incomplete_candidates = review_engine._candidate_rules(incomplete_card, tie_state, "en")
    assert len(incomplete_candidates) == 2
    assert review_engine._candidate_comparison(incomplete_candidates, incomplete_card, "en") is None

    # Structural guarantee that this sentence cannot reach the rendered card:
    # card_renderer has no code path that reads this field at all.
    import inspect
    assert "candidate_comparison" not in inspect.getsource(card_renderer), \
        "card_renderer must never read the interaction-layer comparison sentence"


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
        # #284 wording: the exact-cover gate is against required_honesty_keys
        # (untriggered or month-gated keys are equally "not required").
        assert bad_extra.returncode == 2 and "does not require" in json.loads(bad_extra.stdout)["error"]


def test_preview_finalize_atomic_bundle_redaction_and_retry():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root)
        assert plan["state_snapshot"]["review_progress"] == {
            "completed_reviews_before_start": 0, "returning": False,
        }
        answers_path = pathlib.Path(tmp) / "answers.json"
        narrative_path = pathlib.Path(tmp) / "narrative.json"
        answers_path.write_text(json.dumps(_answers(plan), ensure_ascii=False), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers_path, "--narrative", narrative_path)
        payload = json.loads(preview.stdout)
        assert preview.returncode == 0 and payload["status"] == "previewed"
        assert payload["candidate_rules"][0]["id"] == "candidate_0"
        # #248: the payload row keeps the reusable canonical rule text and adds
        # an engine-authored grounding sentence citing this period's positions.
        candidate = payload["candidate_rules"][0]
        assert candidate["rule"] == card_renderer.localized_rule("加碼攤平", "zh-TW")
        assert "PLTR" in candidate["grounding"] and "3 次" in candidate["grounding"], candidate

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
        assert "PLTR" in private and "-$300" in private and "session_id" in private
        assert "已實現盈虧比 1.4" in private
        assert "最賺" not in private and "最虧" not in private, \
            "closes #346: best/worst single-trade extremes must never print"
        assert "缺費用率資料" in private, "agent-authored honesty sentence must reach the card"
        assert "資料邊界" not in private and "Evidence boundaries" not in private, \
            "#82: honesty is woven into sections, never a standalone checklist section"
        assert all(f.passed for f in check_card(private)), "v2 private renderer must satisfy card iron rules"
        assert "PLTR" not in public and "$" not in public and "2026" not in public and "session_id" not in public
        assert (root / "thesis_decisions.jsonl").exists() and (root / "log.jsonl").exists()
        # #248: the chosen candidate carries its grounding onto the private card
        # only; rules.jsonl keeps the generic canonical text for cross-week
        # tracking, with no single-period tickers baked in.
        bundle = json.loads((session_dir / "bundle.json").read_text(encoding="utf-8"))
        assert bundle["commitment"]["grounding"] == candidate["grounding"]
        assert candidate["grounding"] in private, "grounding sub-line missing from the private card"
        rule_rows = [json.loads(line)
                     for line in (root / "rules.jsonl").read_text(encoding="utf-8").splitlines()
                     if line.strip()]
        assert rule_rows and rule_rows[0]["text"] == candidate["rule"]
        assert all("grounding" not in row and "PLTR" not in row["text"] for row in rule_rows), \
            "rules.jsonl must keep the canonical rule text free of period tickers"
        retry = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers_path, "--narrative", narrative_path)
        assert retry.returncode == 0 and json.loads(retry.stdout)["status"] == "no-op"
        conflicting = _answers(plan, commitment="candidate_0")
        conflicting["observations"].append("different retry payload")
        answers_path.write_text(json.dumps(conflicting, ensure_ascii=False), encoding="utf-8")
        rejected = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                        "--answers", answers_path, "--narrative", narrative_path)
        rejected_payload = json.loads(rejected.stdout)
        assert rejected.returncode == 2 and rejected_payload["status"] == "error"
        assert "already committed with different content" in rejected_payload["error"]
        assert "Traceback" not in rejected.stderr, "conflicting finalize must be a controlled CLI error"
        bundle_before = (session_dir / "bundle.json").read_bytes()
        (root / "thesis_decisions.jsonl").unlink()       # simulate a projection interrupted after commit
        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0 and (root / "thesis_decisions.jsonl").exists()
        assert (session_dir / "bundle.json").read_bytes() == bundle_before, \
            "repair must rebuild projections without mutating canonical bundle"
        card, state = _artifacts(tmp)
        pending_plans = []
        for nonce in ("returning-review-a", "returning-review-b"):
            returning = _run("prepare", "--root", root, "--card-json", card, "--state-json", state,
                             "--session-nonce", nonce)
            returning_plan = _pending_plan(root, returning.stdout)
            assert returning_plan["route"] == "weekly_review"
            assert returning_plan["state_snapshot"]["review_progress"] == {
                "completed_reviews_before_start": 1, "returning": True,
            }
            pending_plans.append(returning_plan)
        assert pending_plans[0]["session_id"] != pending_plans[1]["session_id"]
        for pending_plan in pending_plans:
            opening = review_engine.card_renderer._review_opening_lines({
                "review_plan": pending_plan,
                "engine_state": pending_plan["engine_state"],
            }, "zh-TW")
            assert "開始這次復盤時，你已有 1 次完成紀錄。" in opening[0], \
                "multiple pending plans report the same truthful prepare-time history"


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


def test_user_may_commit_to_a_neutral_observable_outside_the_diagnostic_dimensions():
    """#400/#412: `state.metrics` is the ceiling on what a user may commit to —
    `_commitment` fail-closes on a metric_key absent from it. Until the neutral
    observables landed, that ceiling was the five diagnostic dimensions, so a user
    who wanted to track how deep a position sits under water had no anchor at all
    and the whole finalize failed rather than the one rule being declined.

    Also pins `goal: "up"`. Direction belongs to the condition, not to the
    observable: a raw measurement has no inherently good direction, and the older
    catalog entries could hardcode `down` only because each was a diagnosis."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root, language="en")
        assert "worst_cur_ret" in plan["engine_state"]["metrics"], \
            "the neutral observable must reach the plan the agent reads, or nothing can reference it"
        answers = _answers(plan)
        answers["commitment"] = {
            "choice": "custom",
            "rule": "No position sits more than 30% under water without a written reason",
            "metric_key": "worst_cur_ret", "goal": "up", "dim": "position_sizing"}
        answers_path = pathlib.Path(tmp) / "answers.json"
        narrative_path = pathlib.Path(tmp) / "narrative.json"
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative("en")), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers_path, "--narrative", narrative_path)
        assert final.returncode == 0, \
            "a commitment on a neutral observable must finalize, not fail closed:\n" + final.stdout + final.stderr
        result = json.loads(final.stdout)
        assert result["status"] == "committed"
        committed = json.loads(
            (root / "last_state.json").read_text(encoding="utf-8"))["commitment"]
        assert committed["metric_key"] == "worst_cur_ret"
        assert abs(committed["metric_value"] - (-0.18)) < 1e-9, \
            f"the anchor freezes this review's reading for next time, got {committed}"
        assert committed["goal"] == "up", "the condition supplies direction, not the observable"


_CONDITION = {
    "criterion": "sell if quarterly revenue growth drops under 30%",
    "query": "what was the most recent quarterly revenue, and the year-ago quarter?",
    "threshold": {"value": 30, "unit": "%", "direction": "below"},
    "observation": {"value": 38.0, "as_of": "2026-05-20", "source": "Q1 FY2027 press release",
                    "period": "FY2027Q1", "document": "8-K 2026-05-20"},
}


def _finalize_with(tmp, root, commitment, language="en"):
    plan = _prepare(tmp, root, language=language)
    answers = _answers(plan)
    answers["commitment"] = commitment
    answers_path = pathlib.Path(tmp) / "answers.json"
    narrative_path = pathlib.Path(tmp) / "narrative.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8")
    narrative_path.write_text(json.dumps(_narrative(language)), encoding="utf-8")
    return _run("finalize", "--root", root, "--session-id", plan["session_id"],
                "--answers", answers_path, "--narrative", narrative_path)


def test_a_condition_the_engine_cannot_compute_is_stored_instead_of_refused():
    """#412: the commitment gate used to have two exits — an engine metric, or
    ReviewError. The condition a user reaches for that the engine cannot compute
    is the most informative input a review receives, and it was the one thing
    thrown away.

    Also pins the firewall in the same run: the slot goes to conditions.jsonl and
    *not* to rules.jsonl, because `problems.check_rules` reconciles that file
    against problem events every period and a researched condition has no problem
    key to join on."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        final = _finalize_with(tmp, root, {"choice": "custom", "condition": _CONDITION})
        assert final.returncode == 0, \
            "a condition outside state.metrics must be stored, not refused:\n" + final.stdout + final.stderr
        assert json.loads(final.stdout)["status"] == "committed"

        rows = [json.loads(line) for line in
                (root / "conditions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 1, rows
        slot = rows[0]
        assert slot["criterion"] == _CONDITION["criterion"], "the user's words are stored verbatim"
        assert slot["tier"] == "researched", slot
        assert slot["baseline_verdict"] == "not_met", "38% is clear of a 30% line"
        assert slot["baseline"]["source"] and slot["baseline"]["as_of"], \
            "the evidence anchor is source + as-of date (#414's public_fact shape)"
        assert slot["near_line"] == 3.0, "the margin is frozen at creation, not left adjustable"

        assert not (root / "rules.jsonl").exists() or not [
            line for line in (root / "rules.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()], "a condition slot must never become a rules.jsonl row"
        stats = problems_engine.snapshot(str(root / "problems.jsonl"), str(root / "rules.jsonl"),
                                         today="2026-07-14")
        assert not stats["rules_check"], \
            "a researched condition must not appear in the mechanical rule reconciliation"


def test_a_stored_condition_reaches_the_users_own_words_but_never_the_public_card():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        condition = dict(_CONDITION,
                         criterion="sell PLTR if quarterly revenue growth drops under 30%")
        final = _finalize_with(tmp, root, {"choice": "custom", "condition": condition})
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        private = pathlib.Path(result["private_card"]).read_text(encoding="utf-8")
        public = pathlib.Path(result["public_card"]).read_text(encoding="utf-8")
        assert condition["criterion"] in private, "the private card prints the criterion verbatim"
        for fragment in ("PLTR", "30%", "press release"):
            assert fragment not in public, f"condition leaked {fragment!r} into the public card"


def test_a_stored_condition_is_read_back_into_the_next_review():
    """The record is the product: a condition nobody reads back is a promise
    made into a file.

    #434: it comes back as a *lookup request* — the line's live row plus what
    its last check found — not as a raw dump of the store."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        assert _finalize_with(tmp, root, {"choice": "custom", "condition": _CONDITION}).returncode == 0
        later = _prepare(tmp, root, language="en")
        snapshot = later["state_snapshot"]
        assert "condition_slots" not in snapshot, \
            "the unbounded raw roster is replaced by the bounded due list (#434)"
        due = snapshot["condition_slots_due"]
        assert [row["criterion"] for row in due] == [_CONDITION["criterion"]]
        assert due[0]["query"] == _CONDITION["query"], \
            "the query is frozen at creation; re-deriving it later reintroduces the restate risk"
        assert due[0]["last_check"] is None, "nothing has been checked for it yet"
        assert snapshot["condition_slots_summary"] == {
            "lines_total": 1, "due_now": 1, "beyond_cap": 0, "unmapped_lines": 0,
            "retired_lines": 0, "unreadable_slots": 0, "unreadable_checks": 0}
        assert "thesis_link" not in due[0], \
            "a commitment condition guards the portfolio, not one position's thesis (#416 C2)"


def test_a_condition_that_could_not_be_looked_up_is_stored_as_unmapped():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        blind = {key: value for key, value in _CONDITION.items() if key != "observation"}
        final = _finalize_with(tmp, root, {"choice": "custom", "condition": blind})
        assert final.returncode == 0, final.stdout + final.stderr
        slot = json.loads((root / "conditions.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert (slot["tier"], slot["unmapped_reason"]) == ("unmapped", "no_baseline")
        assert "baseline_verdict" not in slot, \
            "nothing was looked up, so nothing may read as checked and fine"


def test_a_repeated_finalize_appends_one_condition_row_and_a_changed_one_fails_closed():
    """Append-only state with an idempotent finalize is on the never-loosen list,
    and a new projection inherits neither for free. Without this, a retry could
    silently double every condition in the user's record."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan)
        answers["commitment"] = {"choice": "custom", "condition": _CONDITION}
        answers_path = pathlib.Path(tmp) / "answers.json"
        narrative_path = pathlib.Path(tmp) / "narrative.json"
        narrative_path.write_text(json.dumps(_narrative("en")), encoding="utf-8")
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        args = ("finalize", "--root", root, "--session-id", plan["session_id"],
                "--answers", answers_path, "--narrative", narrative_path)

        def rows():
            text = (root / "conditions.jsonl").read_text(encoding="utf-8")
            return [json.loads(line) for line in text.splitlines() if line.strip()]

        assert _run(*args).returncode == 0
        assert len(rows()) == 1
        _run(*args)                                   # documented-safe retry
        assert len(rows()) == 1, "an identical retry must not append a second row"

        answers["commitment"]["condition"] = dict(_CONDITION, criterion="sell if margin drops")
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        changed = _run(*args)
        assert changed.returncode != 0, "a different condition under a committed session id " \
                                        "must fail closed, not overwrite the record"
        assert len(rows()) == 1 and rows()[0]["criterion"] == _CONDITION["criterion"]


def test_a_condition_the_engine_reads_as_already_crossed_says_so_on_the_card():
    """Owner ruling: showing the value back is what makes a wrong basis expose
    itself. The engine performs this comparison, so the card is where a line the
    user has already crossed stops being invisible."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        breached = dict(_CONDITION,
                        observation={"value": 21.0, "as_of": "2026-05-20", "source": "release"})
        final = _finalize_with(tmp, root, {"choice": "custom", "condition": breached})
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        private = pathlib.Path(result["private_card"]).read_text(encoding="utf-8")
        assert "already crossed" in private, \
            "a line already crossed at commit time is a decision now, not a tripwire:\n" + private
        public = pathlib.Path(result["public_card"]).read_text(encoding="utf-8")
        assert "already crossed" not in public, "the public card carries no condition detail"


def test_a_watched_condition_that_is_nowhere_near_its_line_stays_silent():
    """The counterweight to the test above: a card does not explain itself. If
    every condition earned a sentence, the line that matters would be noise."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        final = _finalize_with(tmp, root, {"choice": "custom", "condition": _CONDITION})
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        for fragment in ("already crossed", "not being watched", "cannot be checked"):
            assert fragment not in private, f"a clear condition must not add {fragment!r}"


# ───────────── the per-period condition check flow (#412 / #434) ─────────────
#
# The plan side (what is due, bounded, ordered), the answer side (what is
# recorded, including the periods nobody looked), the question side (one
# crossing, two-sided, budgeted) and the firewalls. Every gate here has a named
# mutation in the PR body.

_OBS = {"value": 36.0, "as_of": "2026-08-20", "source": "10-Q",
        "period": "FY2027Q2", "document": "10-Q 2026-08-20"}
_CROSSED = {"value": 21.0, "as_of": "2026-08-20", "source": "10-Q",
            "period": "FY2027Q2", "document": "10-Q 2026-08-20"}


def _write_json(tmp, name, payload):
    path = pathlib.Path(tmp) / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _seed_condition(tmp, root, condition=None):
    """Commit one condition so later reviews have something standing."""
    final = _finalize_with(tmp, root, {"choice": "custom", "condition": condition or _CONDITION})
    assert final.returncode == 0, final.stdout + final.stderr
    return json.loads((root / "conditions.jsonl").read_text(encoding="utf-8").splitlines()[0])


def _seed_conditions(tmp, root, criteria):
    """Several conditions on separate lines, written straight to the store.

    One commitment per review is the product contract, so seeding N standing
    conditions through the CLI would need N reviews; the plan side under test
    reads the file, and this keeps the fixture about the cap rather than about
    running the loop five times."""
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, criterion in enumerate(criteria):
        rows.append({"slot_id": f"slot-seed-{index}", "kind": "numeric", "criterion": criterion,
                     "query": f"what is the current reading for item {index}?",
                     "created": "2026-07-01", "tier": "researched",
                     "threshold": {"value": 30, "unit": "%", "direction": "below"},
                     "near_line": 3.0,
                     "baseline": {"value": 38.0, "as_of": "2026-05-20", "source": "release"},
                     "baseline_verdict": "not_met"})
    (root / "conditions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


def _prepare_with_checks(tmp, root, checks, language="en"):
    card, state = _artifacts(tmp)
    path = _write_json(tmp, "condition-checks.json", {"condition_checks": checks})
    run = _run("prepare", "--root", root, "--language", language,
               "--card-json", card, "--state-json", state, "--condition-checks", path)
    assert run.returncode == 0, run.stdout + run.stderr
    return _pending_plan(root, run.stdout)


def _finalize_plan(tmp, root, plan, answers, language="en"):
    answers_path = _write_json(tmp, "answers.json", answers)
    narrative_path = _write_json(tmp, "narrative.json", _narrative(language))
    return _run("finalize", "--root", root, "--session-id", plan["session_id"],
                "--answers", answers_path, "--narrative", narrative_path)


def _check_rows(root):
    path = root / "condition_checks.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_plan_sends_a_bounded_lookup_request_and_says_what_it_held_back():
    """#434: the plan used to hand over every stored slot. A user with more
    standing conditions than a review can act on then paid for all of them in
    every turn's context, and the lookup work had no ceiling.

    The bound is on the plan, never on the record — and a bounded surface that
    does not say what it dropped is the same lie as an unchecked condition
    presented as fine."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        seeded = _seed_conditions(tmp, root, [f"sell if metric {i} drops under 30%" for i in range(11)])
        plan = _prepare(tmp, root, language="en")
        snapshot = plan["state_snapshot"]
        due = snapshot["condition_slots_due"]
        assert len(due) == review_engine.CONDITION_LOOKUP_CAP == 8, len(due)
        assert snapshot["condition_slots_summary"] == {
            "lines_total": 11, "due_now": 8, "beyond_cap": 3, "unmapped_lines": 0,
            "retired_lines": 0, "unreadable_slots": 0, "unreadable_checks": 0}
        assert {row["slot_id"] for row in due} <= {row["slot_id"] for row in seeded}
        assert all(row["last_check"] is None for row in due), "none has ever been checked"


def test_the_due_list_rotates_oldest_last_checked_first():
    """Without an order the cap starves the tail: the same eight conditions are
    checked forever and the ninth is never looked at again. A failed lookup
    still counts as attention — otherwise a line that fails every week parks
    itself at the front of the queue permanently."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%", "sell if b drops under 30%",
                                     "sell if c drops under 30%"])
        (root / "condition_checks.jsonl").write_text("".join(json.dumps(row) + "\n" for row in [
            # slot-seed-0 checked most recently, slot-seed-1 long ago (and it
            # failed, which is still attention), slot-seed-2 never.
            {"check_id": "c1", "slot_id": "slot-seed-1", "session_id": "s1",
             "date_end": "2026-07-05", "created": "2026-07-05", "lookup_status": "failed",
             "information_state": None, "engine_verdict": None,
             "final_verdict": "unknown", "verdict_source": "engine"},
            {"check_id": "c2", "slot_id": "slot-seed-0", "session_id": "s2",
             "date_end": "2026-07-20", "created": "2026-07-20", "lookup_status": "ok",
             "observation": dict(_OBS), "information_state": "new_period",
             "engine_verdict": "not_met", "final_verdict": "not_met", "verdict_source": "engine"},
        ]), encoding="utf-8")
        due = _prepare(tmp, root, language="en")["state_snapshot"]["condition_slots_due"]
        assert [row["slot_id"] for row in due] == ["slot-seed-2", "slot-seed-1", "slot-seed-0"], \
            "never-checked first, then oldest attention, then the most recent"
        assert due[0]["last_check"] is None
        assert due[1]["last_check"] == {"date_end": "2026-07-05", "lookup_status": "failed",
                                        "information_state": None, "final_verdict": "unknown"}
        assert due[2]["last_check"]["final_verdict"] == "not_met"


def test_a_submitted_check_is_recorded_and_a_due_one_nobody_ran_says_so():
    """The record must never have silent gaps. A period with no row for a
    condition and a period where the lookup quietly succeeded are
    indistinguishable when read back — and "we did not look" is precisely what
    a user needs to know before they act as though a tripwire is armed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%", "sell if b drops under 30%"])
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": "slot-seed-0", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        assert _finalize_plan(tmp, root, plan, answers).returncode == 0
        rows = {row["slot_id"]: row for row in _check_rows(root)}
        assert set(rows) == {"slot-seed-0", "slot-seed-1"}, \
            "every due condition gets a row, including the one nobody submitted"
        assert rows["slot-seed-0"]["lookup_status"] == "ok"
        assert rows["slot-seed-0"]["engine_verdict"] == "not_met", "the engine did the comparison"
        assert rows["slot-seed-1"]["lookup_status"] == "not_checked"
        assert rows["slot-seed-1"]["reason"] == "not submitted this review"
        assert rows["slot-seed-1"]["final_verdict"] == "unknown", \
            "an unchecked condition must never read as checked and fine"


def test_a_check_for_a_condition_held_back_by_the_cap_is_still_accepted():
    """The cap bounds what the plan asks for, never what the record accepts. An
    agent with spare capacity that looked one up anyway has produced a real
    observation, and refusing it would throw away evidence to enforce a budget."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, [f"sell if metric {i} drops under 30%" for i in range(10)])
        plan = _prepare(tmp, root, language="en")
        beyond = ({row["slot_id"] for row in
                   [{"slot_id": f"slot-seed-{i}"} for i in range(10)]}
                  - {row["slot_id"] for row in plan["state_snapshot"]["condition_slots_due"]})
        assert beyond, "the fixture must actually overflow the cap"
        extra = sorted(beyond)[0]
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": extra, "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        assert _finalize_plan(tmp, root, plan, answers).returncode == 0
        assert any(row["slot_id"] == extra and row["lookup_status"] == "ok"
                   for row in _check_rows(root))


def test_a_check_for_a_condition_that_is_not_in_the_record_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%"])
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": "slot-invented", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        failed = _finalize_plan(tmp, root, plan, answers)
        assert failed.returncode != 0
        assert "unknown condition" in failed.stdout + failed.stderr
        assert not _check_rows(root), "nothing is written when the envelope is refused"


def test_a_repeated_finalize_appends_one_check_row_and_a_changed_one_fails_closed():
    """Idempotent finalize and append-only state are on the never-loosen list; a
    new store inherits neither for free. Without this a documented-safe retry
    would double every condition reading in the user's history."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%"])
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": "slot-seed-0", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        answers_path = _write_json(tmp, "answers.json", answers)
        narrative_path = _write_json(tmp, "narrative.json", _narrative("en"))
        args = ("finalize", "--root", root, "--session-id", plan["session_id"],
                "--answers", answers_path, "--narrative", narrative_path)
        assert _run(*args).returncode == 0
        assert len(_check_rows(root)) == 1
        _run(*args)
        assert len(_check_rows(root)) == 1, "an identical retry must not append a second reading"

        answers["condition_checks"][0]["check"]["observation"]["value"] = 12.0
        _write_json(tmp, "answers.json", answers)
        changed = _run(*args)
        assert changed.returncode != 0, \
            "a different reading under a committed session id must fail closed"
        rows = _check_rows(root)
        assert len(rows) == 1 and rows[0]["observation"]["value"] == 36.0


def test_a_crossing_raises_exactly_one_two_sided_question():
    """The engine performs the comparison; the question exists because the user
    may know the reading is wrong. Its stem is agent-authored for a reason — one
    sentence for acting and one for not — and both answers stay available."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot_id, "check": {"lookup_status": "ok", "observation": dict(_CROSSED)}}])
        crossings = [q for q in plan["question_queue"] if q["kind"] == "condition_crossing"]
        assert len(crossings) == 1, plan["question_queue"]
        question = crossings[0]
        assert question["criterion"] == _CONDITION["criterion"], "the user's own words, verbatim"
        assert "21%" in question["evidence"] and "10-Q" in question["evidence"] \
            and "2026-08-20" in question["evidence"], question["evidence"]
        choices = [option["value"] for option in question["options"]]
        assert choices == ["confirmed", "overridden", "skip"], choices
        # #262: a missing copy key would leave an empty label or fall it back to
        # the enum value, putting an internal identifier on the surface a
        # plain_text host reads out. The corpus cannot pin this — the question
        # layer renders into the Review Plan, not the card — so it is pinned
        # where it is produced, on both halves a host displays.
        for option in question["options"]:
            for field in ("label", "description"):
                assert option[field] and option[field] != option["value"], option
                assert "_" not in option[field], option
        opportunity = question["question_opportunity"]
        assert opportunity["intent"] == "adjudicate_condition_crossing"
        assert opportunity["context"]["condition"]["criterion"] == _CONDITION["criterion"]
        assert opportunity["answer_contract"]["requirements_by_choice"]["overridden"] == ["note"], \
            "rejecting the engine's own reading is a claim; the reason goes into the record"


def test_a_reading_clear_of_the_line_raises_no_question():
    """The counterweight. If every check earned a question the one that matters
    would be buried, and a review would become a survey of things that are fine."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot_id, "check": {"lookup_status": "ok", "observation": dict(_OBS)}}])
        assert not [q for q in plan["question_queue"]
                    if q["kind"] in ("condition_crossing", "condition_basis")]


def test_only_one_crossing_is_asked_and_the_alerted_event_outranks_the_deepest_number():
    """A week that trips four conditions is a week with one conversation to
    have. The order is not arbitrary: an occurrence nobody confirmed decays into
    "nobody said", while a number can be re-derived next week — so an alerted
    event goes first, then the deepest breach."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True, exist_ok=True)
        rows = [
            {"slot_id": "slot-num-shallow", "kind": "numeric", "criterion": "sell if a drops under 30%",
             "query": "what is a?", "created": "2026-07-01", "tier": "researched",
             "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0},
            {"slot_id": "slot-num-deep", "kind": "numeric", "criterion": "sell if b drops under 30%",
             "query": "what is b?", "created": "2026-07-01", "tier": "researched",
             "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0},
            {"slot_id": "slot-evt", "kind": "event", "criterion": "sell if the CEO leaves",
             "query": "who is the current chief executive?", "created": "2026-07-01",
             "tier": "researched"},
        ]
        (root / "conditions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": "slot-num-shallow",
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=28.0)}},
            {"slot_id": "slot-num-deep",
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=4.0)}},
            {"slot_id": "slot-evt",
             "check": {"lookup_status": "ok", "event_alert": True,
                       "observation": {"summary": "the CEO announced a departure",
                                       "as_of": "2026-08-20", "source": "8-K"}}},
        ])
        crossings = [q for q in plan["question_queue"] if q["kind"] == "condition_crossing"]
        assert len(crossings) == 1, [q["slot_id"] for q in crossings]
        assert crossings[0]["slot_id"] == "slot-evt", "an alerted event outranks every number"
        assert [option["value"] for option in crossings[0]["options"]] == ["yes", "no", "skip"]
        deferred = [row for row in plan["card_plan"]["question_selection"]["rejected"]
                    if row["reason"] == "condition_crossing_limit"]
        assert {row["kind"] for row in deferred} == {"condition_crossing"}
        assert len(deferred) == 2, "deferral is stated, not silent"


def test_the_deepest_breach_wins_when_no_event_is_alerted():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True, exist_ok=True)
        rows = [
            {"slot_id": "slot-shallow", "kind": "numeric", "criterion": "sell if a drops under 30%",
             "query": "what is a?", "created": "2026-07-01", "tier": "researched",
             "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0},
            {"slot_id": "slot-deep", "kind": "numeric", "criterion": "sell if b drops under 30%",
             "query": "what is b?", "created": "2026-07-01", "tier": "researched",
             "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0},
        ]
        (root / "conditions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": "slot-shallow",
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=29.0)}},
            {"slot_id": "slot-deep",
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=3.0)}},
        ])
        crossings = [q for q in plan["question_queue"] if q["kind"] == "condition_crossing"]
        assert len(crossings) == 1 and crossings[0]["slot_id"] == "slot-deep", \
            "a near-line reading must never out-rank a line that is genuinely past"


def test_the_answer_to_a_crossing_lands_on_the_row_it_was_asked_about():
    """One row carries the complete story — the evidence, the engine's
    comparison, and the user's word — instead of a row written now and patched
    later. The override never rewrites what the engine computed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        envelope = [{"slot_id": slot_id,
                     "check": {"lookup_status": "ok", "observation": dict(_CROSSED)}}]
        plan = _prepare_with_checks(tmp, root, envelope)
        question = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
        answers = _answers(plan, commitment="skip")
        answers["answers"] = [row for row in answers["answers"]
                              if row["question_id"] != question["id"]]
        answers["answers"].append({"question_id": question["id"], "choice": "overridden",
                                   "note": "the filing restated the prior-year base"})
        answers["condition_checks"] = envelope
        assert _finalize_plan(tmp, root, plan, answers).returncode == 0
        row = next(r for r in _check_rows(root) if r["slot_id"] == slot_id)
        assert row["engine_verdict"] == "met", "the engine's own read is never overwritten"
        assert (row["final_verdict"], row["verdict_source"]) == ("not_met", "user")
        assert row["user_response"]["answer"] == "overridden"
        assert row["user_response"]["note"].startswith("the filing restated")


# A per-condition reading line, as the card prints it: `criterion — value`.
# Distinct from the reconciliation opener, which names the same condition when
# it is also the prior commitment.
_READING_LINE = _CONDITION["criterion"] + " —"


def _crossing_answered(tmp, root, choice, note=None):
    """One crossed condition, its question posed, and `choice` given to it.

    Returns the finalize result so a caller can read the stored row *and* the
    card — the round-1 version of this only looked at the row, which is exactly
    how "asked" got mistaken for "answered" on the card side (external review,
    round 2)."""
    _seed_condition(tmp, root)
    slot_id = json.loads((root / "conditions.jsonl").read_text(
        encoding="utf-8").splitlines()[0])["slot_id"]
    envelope = [{"slot_id": slot_id,
                 "check": {"lookup_status": "ok", "observation": dict(_CROSSED)}}]
    plan = _prepare_with_checks(tmp, root, envelope)
    question = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
    answers = _answers(plan, commitment="skip")
    for row in answers["answers"]:
        if row["question_id"] == question["id"]:
            row["choice"] = choice
            if note:
                row["note"] = note
    answers["condition_checks"] = envelope
    return slot_id, _finalize_plan(tmp, root, plan, answers)


def test_a_crossing_that_was_asked_but_not_answered_still_speaks_on_the_card():
    """External review, round 2 BLOCK: the card silenced a crossing as soon as a
    question for it sat in `question_queue`. But a skip records no answer, and
    an undelivered question records nothing at all — in both cases the row
    finalizes as met/engine with no `user_response`, and the crossed line
    vanished again. Round 1's silence, reincarnated through a different door.

    The silence condition is ANSWERED, not queued."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot_id, final = _crossing_answered(tmp, root, "skip")
        assert final.returncode == 0, final.stdout + final.stderr
        row = next(r for r in _check_rows(root) if r["slot_id"] == slot_id)
        assert "user_response" not in row, "a skip is not an answer"
        assert (row["final_verdict"], row["verdict_source"]) == ("met", "engine")
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert _CONDITION["criterion"] in private, \
            "a crossed line nobody answered on must still reach the card:\n" + private
        assert "you have not said either way" in private, private
        assert "Open concerns coming back next review: 1." in private, \
            "and the summary must count it as unresolved:\n" + private


def test_a_crossing_the_user_answered_goes_quiet():
    """The counterweight, and the thing that makes the fix a distinction rather
    than a blanket. Once the user actually answers, the exchange told the story
    and the card does not re-litigate it — including an override, whose verdict
    of record is `not_met` while the engine's own finding stays `met`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot_id, final = _crossing_answered(tmp, root, "confirmed")
        assert final.returncode == 0, final.stdout + final.stderr
        row = next(r for r in _check_rows(root) if r["slot_id"] == slot_id)
        assert row["user_response"]["answer"] == "confirmed"
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        # The reading-line shape specifically. The reconciliation opener legitimately
        # names this same condition — it is the prior commitment — and that is a
        # different surface with a different job.
        assert _READING_LINE not in private, \
            "an answered crossing is not restated as a reading:\n" + private
        assert "Open concerns" not in private, private
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _slot_id, final = _crossing_answered(tmp, root, "overridden", note="the base was restated")
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert _READING_LINE not in private, \
            "an override moves the verdict of record to not_met; it must not reappear " \
            "as an all-clear reading:\n" + private


def test_an_unresolved_basis_concern_does_not_read_as_a_clean_number():
    """The symmetric half of the same BLOCK. A basis question that was skipped
    (or never delivered) leaves the row with a `basis_alert` and no
    `basis_resolution`. Before this the check fell through to the ordinary fact
    branch — the reading printed as a clean all-clear and the summary counted
    nothing open, so the doubt disappeared exactly the way a silent crossing
    made a crossed line disappear."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot, envelope, plan, question = _basis_fixture(tmp, root)
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "skip"
        answers["condition_checks"] = envelope
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        row = next(r for r in _check_rows(root) if r["slot_id"] == slot["slot_id"])
        assert "basis_resolution" not in row, "a skip resolves nothing"
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "the reporting segment was restated" in private, \
            "the raised concern must stay visible:\n" + private
        assert "may have changed" in private and "unsettled" in private, private
        assert "Open concerns coming back next review: 1." in private, private


def _crossed_and_doubted(tmp, root, crossing_choice, basis_choice):
    """One check that is BOTH a crossing and a basis alert — the combined row.

    `build_check` writes `basis_alert` and `engine_verdict` on the same row and
    `_condition_questions` emits both questions for that line, so this is not a
    synthetic shape: it is what a quarter that both crossed a line and restated
    its segment actually produces."""
    slot = _seed_condition(tmp, root)
    envelope = [{"slot_id": slot["slot_id"],
                 "check": {"lookup_status": "ok", "observation": dict(_CROSSED),
                           "basis_alert": {"note": "the reporting segment was restated"}}}]
    plan = _prepare_with_checks(tmp, root, envelope)
    crossing = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
    basis = next(q for q in plan["question_queue"] if q["kind"] == "condition_basis")
    answers = _answers(plan, commitment="skip")
    for row in answers["answers"]:
        if row["question_id"] == crossing["id"]:
            row["choice"] = crossing_choice
        elif row["question_id"] == basis["id"]:
            row["choice"] = basis_choice
    answers["condition_checks"] = envelope
    return slot, _finalize_plan(tmp, root, plan, answers)


def test_a_row_that_is_both_crossed_and_doubted_reports_both():
    """External review, round 3 BLOCK: the crossing and the basis ran through one
    single-valued if-chain, so whichever matched first won and the other fact
    was neither printed nor counted. A user could confirm the crossing, skip the
    basis question, and never hear again that the measurement may have moved
    underneath the line they just confirmed.

    They are independent axes. This is the cell that proves it: the crossing is
    answered, the basis is not, and the basis concern must survive."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot, final = _crossed_and_doubted(tmp, root, "confirmed", "skip")
        assert final.returncode == 0, final.stdout + final.stderr
        row = next(r for r in _check_rows(root) if r["slot_id"] == slot["slot_id"])
        assert row["user_response"]["answer"] == "confirmed" and "basis_resolution" not in row, row
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "the reporting segment was restated" in private, \
            "the answered crossing must not swallow the open basis concern:\n" + private
        assert "Open concerns coming back next review: 1." in private, private


def test_a_row_open_on_both_axes_prints_both_notes_and_counts_two_concerns():
    """The other combined cell, and the one that makes "count concerns, not
    rows" load-bearing: an unanswered crossing beside an open basis is two
    separate things left undone on one reading."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot, final = _crossed_and_doubted(tmp, root, "skip", "skip")
        assert final.returncode == 0, final.stdout + final.stderr
        row = next(r for r in _check_rows(root) if r["slot_id"] == slot["slot_id"])
        assert "user_response" not in row and "basis_resolution" not in row, row
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "you have not said either way" in private, private
        assert "the reporting segment was restated" in private, \
            "both notes ride the one reading:\n" + private
        assert private.count(_READING_LINE) == 1, \
            "one reading, two notes — not the same condition printed twice:\n" + private
        assert "Open concerns coming back next review: 2." in private, \
            "an unanswered crossing and an open basis are two concerns:\n" + private


def test_a_deferred_crossing_beside_an_open_basis_reports_both():
    """The fourth combined cell, and the second one the round-2 single-value
    classifier actually broke. Same loss as the unanswered variant — the
    crossing branch matched first and the basis note went with it — but reached
    through the other door: this crossing never got a question at all, because
    a deeper breach on another condition took the one-question budget.

    Two crossed conditions, and the one that *loses* the budget is the one whose
    basis is also in doubt, so its crossing is `deferred` rather than
    `unanswered` while its basis stays open."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True, exist_ok=True)
        rows = [{"slot_id": f"slot-d-{index}", "kind": "numeric",
                 "criterion": f"sell if metric {index} drops under 30%",
                 "query": f"what is metric {index}?", "created": "2026-07-01",
                 "tier": "researched", "near_line": 3.0,
                 "threshold": {"value": 30, "unit": "%", "direction": "below"}}
                for index in range(2)]
        (root / "conditions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        # slot-d-0 is the deeper breach, so it takes the single crossing slot;
        # slot-d-1 is the shallower one carrying the basis alert.
        envelope = [
            {"slot_id": "slot-d-0",
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=3.0)}},
            {"slot_id": "slot-d-1",
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=27.0),
                       "basis_alert": {"note": "the reporting segment was restated"}}}]
        plan = _prepare_with_checks(tmp, root, envelope)
        queued = {q["slot_id"] for q in plan["question_queue"]
                  if q["kind"] == "condition_crossing"}
        assert queued == {"slot-d-0"}, \
            "the fixture must actually defer slot-d-1's crossing, got " + str(queued)
        basis = next(q for q in plan["question_queue"]
                     if q["kind"] == "condition_basis" and q["slot_id"] == "slot-d-1")
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == basis["id"]:
                row["choice"] = "skip"
        answers["condition_checks"] = envelope
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        row = next(r for r in _check_rows(root) if r["slot_id"] == "slot-d-1")
        assert "user_response" not in row and "basis_resolution" not in row, row
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        deferred_line = "sell if metric 1 drops under 30% —"
        assert deferred_line in private, \
            "the deferred crossing must reach the card:\n" + private
        assert "past your line; I will ask you about it next review" in private, private
        assert "the reporting segment was restated" in private, \
            "the deferred crossing must not swallow the open basis concern:\n" + private
        assert private.count(deferred_line) == 1, \
            "one reading, two notes — not the same condition printed twice:\n" + private
        # slot-d-0's crossing was queued and skipped (one concern), and
        # slot-d-1 is open on both axes (two) — three in total.
        assert "Open concerns coming back next review: 3." in private, \
            "a deferred crossing and its open basis are two separate concerns:\n" + private


def test_a_row_settled_on_both_axes_goes_quiet():
    """The closing cell of the combined set: answer both and the card says
    nothing, so the fix is a distinction rather than a blanket."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _slot, final = _crossed_and_doubted(tmp, root, "confirmed", "keep")
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert _READING_LINE not in private, private
        assert "the reporting segment was restated" not in private, private
        assert "Open concerns" not in private, private


def test_a_resolved_basis_concern_goes_quiet():
    """And the counterweight: `keep` settles it, so the reading prints as the
    ordinary fact it is and nothing is left open."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot, envelope, plan, question = _basis_fixture(tmp, root)
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "keep"
        answers["condition_checks"] = envelope
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "may have changed" not in private, private
        assert "Open concerns" not in private, private
        assert _READING_LINE in private, \
            "the reading itself still prints as an ordinary fact:\n" + private


def test_a_reading_that_changes_between_the_question_and_the_answer_fails_closed():
    """The user was asked about one number. Recording another is the silent
    divergence the frozen question surfaces exist to prevent, one store over."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot_id, "check": {"lookup_status": "ok", "observation": dict(_CROSSED)}}])
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": slot_id,
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=29.0)}}]
        failed = _finalize_plan(tmp, root, plan, answers)
        assert failed.returncode != 0
        assert "changed between the question and the answer" in failed.stdout + failed.stderr


def test_an_answer_cannot_be_attached_to_a_lookup_that_did_not_succeed():
    """No fresh evidence means no verdict to answer against. The refusal comes
    from the engine's own validator rather than from a check here, so the two
    can never disagree about what a contradictory row looks like."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot_id, "check": {"lookup_status": "ok", "observation": dict(_CROSSED)}}])
        question = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "confirmed"
        answers["condition_checks"] = [
            {"slot_id": slot_id, "check": {"lookup_status": "failed", "reason": "source offline"}}]
        failed = _finalize_plan(tmp, root, plan, answers)
        assert failed.returncode != 0, failed.stdout
        assert "condition check rejected" in failed.stdout + failed.stderr
        assert not _check_rows(root)


def _basis_fixture(tmp, root):
    slot = _seed_condition(tmp, root)
    envelope = [{"slot_id": slot["slot_id"],
                 "check": {"lookup_status": "ok", "observation": dict(_OBS),
                           "basis_alert": {"note": "the reporting segment was restated",
                                           "source": "10-Q", "as_of": "2026-08-20"}}}]
    plan = _prepare_with_checks(tmp, root, envelope)
    question = next(q for q in plan["question_queue"] if q["kind"] == "condition_basis")
    return slot, envelope, plan, question


def test_a_doubted_basis_raises_a_question_that_can_end_in_keep():
    """False alarms are allowed by design: the user resolves it. What must not
    happen is silence — a threshold that has quietly stopped measuring what it
    measured poisons every verdict after it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot, envelope, plan, question = _basis_fixture(tmp, root)
        assert [option["value"] for option in question["options"]] == \
            ["revise_threshold", "revise_metric", "keep", "skip"]
        for option in question["options"]:
            for field in ("label", "description"):
                assert option[field] and option[field] != option["value"], option
                assert "_" not in option[field], option
        assert question["basis_note"] == "the reporting segment was restated"
        assert question["basis_note"] in question["question"], \
            "the engine fallback states the doubt; an unauthored surface is still answerable"
        assert question["question_opportunity"]["intent"] == "resolve_condition_basis"
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "keep"
        answers["condition_checks"] = envelope
        assert _finalize_plan(tmp, root, plan, answers).returncode == 0
        row = next(r for r in _check_rows(root) if r["slot_id"] == slot["slot_id"])
        assert row["basis_resolution"] == "kept"
        assert row["engine_verdict"] == "not_met", "doubt about the basis is not a verdict"
        assert len((root / "conditions.jsonl").read_text(
            encoding="utf-8").strip().splitlines()) == 1, "keep writes no new slot row"


def test_a_revised_condition_is_a_new_row_on_the_same_line_never_a_rule():
    """A re-stated criterion is a new row, not an edit: the old row is a fact
    about what the user meant when they wrote it, and every check already
    recorded points at it. The firewall holds — a condition can never enter the
    mechanical rule reconciliation."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot, envelope, plan, question = _basis_fixture(tmp, root)
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "revise_metric"
        answers["condition_checks"] = envelope
        answers["condition_revision"] = {
            "of_line_id": question["line_id"],
            "condition": {"criterion": "sell if segment revenue growth drops under 30%",
                          "query": "what was segment revenue this quarter and a year ago?",
                          "threshold": {"value": 30, "unit": "%", "direction": "below"}}}
        assert _finalize_plan(tmp, root, plan, answers).returncode == 0
        rows = [json.loads(line) for line in
                (root / "conditions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 2, rows
        child = rows[-1]
        assert child["revises"] == slot["slot_id"]
        assert child["line_id"] == question["line_id"], "the line carries forward"
        assert child["criterion"] == "sell if segment revenue growth drops under 30%"
        assert not (root / "rules.jsonl").exists() or not [
            line for line in (root / "rules.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()], "a condition revision must never become a rules.jsonl row"
        check = next(r for r in _check_rows(root) if r["slot_id"] == slot["slot_id"])
        assert check["basis_resolution"] == "revised", "the check row is never silent about it"


def test_a_revision_requires_the_question_that_asked_for_it_and_vice_versa():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot, envelope, plan, question = _basis_fixture(tmp, root)
        # revise answered, no replacement supplied
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "revise_threshold"
        answers["condition_checks"] = envelope
        missing = _finalize_plan(tmp, root, plan, answers)
        assert missing.returncode != 0
        assert "requires answers.condition_revision" in missing.stdout + missing.stderr
        # replacement supplied against a question nobody answered that way
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "keep"
        answers["condition_checks"] = envelope
        answers["condition_revision"] = {
            "of_line_id": question["line_id"],
            "condition": {"criterion": "sell if segment growth drops under 30%",
                          "query": "what was segment revenue this quarter and a year ago?",
                          "threshold": {"value": 30, "unit": "%", "direction": "below"}}}
        unasked = _finalize_plan(tmp, root, plan, answers)
        assert unasked.returncode != 0
        assert "no condition question this review asked to re-state" in unasked.stdout + unasked.stderr


def test_a_line_answered_as_a_crossing_cannot_also_be_re_stated_this_review():
    """One change per line per session — the same shape as #416's muted-then-
    revised rule guard. The user just answered about the criterion as it stands;
    replacing it in the same breath leaves that answer pointing at nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot = _seed_condition(tmp, root)
        envelope = [{"slot_id": slot["slot_id"],
                     "check": {"lookup_status": "ok", "observation": dict(_CROSSED),
                               "basis_alert": {"note": "the reporting segment was restated"}}}]
        plan = _prepare_with_checks(tmp, root, envelope)
        crossing = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
        basis = next(q for q in plan["question_queue"] if q["kind"] == "condition_basis")
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == crossing["id"]:
                row["choice"] = "confirmed"
            elif row["question_id"] == basis["id"]:
                row["choice"] = "revise_threshold"
        answers["condition_checks"] = envelope
        answers["condition_revision"] = {
            "of_line_id": basis["line_id"],
            "condition": {"criterion": "sell if growth drops under 25%",
                          "query": "what was revenue this quarter and a year ago?",
                          "threshold": {"value": 25, "unit": "%", "direction": "below"}}}
        refused = _finalize_plan(tmp, root, plan, answers)
        assert refused.returncode != 0
        assert "also answered as a crossing" in refused.stdout + refused.stderr
        assert len((root / "conditions.jsonl").read_text(
            encoding="utf-8").strip().splitlines()) == 1, "nothing is written when it is refused"


def test_a_revision_of_a_condition_that_is_not_live_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _slot, envelope, plan, question = _basis_fixture(tmp, root)
        answers = _answers(plan, commitment="skip")
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "revise_metric"
        answers["condition_checks"] = envelope
        answers["condition_revision"] = {
            "of_line_id": "slot-never-existed",
            "condition": {"criterion": "sell if growth drops under 25%",
                          "query": "what was revenue this quarter and a year ago?",
                          "threshold": {"value": 25, "unit": "%", "direction": "below"}}}
        refused = _finalize_plan(tmp, root, plan, answers)
        assert refused.returncode != 0
        assert "asked to re-state" in refused.stdout + refused.stderr


def test_the_card_reconciles_a_condition_then_and_now_and_says_what_it_skipped():
    """The loop is the product. A condition-anchored commitment reconciles the
    same way a metric-anchored one does — the baseline taken when it was written
    against what this period found — and a review that could not look at
    everything says so rather than implying completeness."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        # A second standing condition nobody checks, so the summary has to speak.
        with (root / "conditions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"slot_id": "slot-extra-0", "kind": "numeric",
                 "criterion": "sell if churn rises above 8%",
                 "query": "what is the latest reported churn?", "created": "2026-07-01",
                 "tier": "researched", "threshold": {"value": 8, "unit": "%", "direction": "above"},
                 "near_line": 0.8}) + "\n")
        envelope = [{"slot_id": slot_id,
                     "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        plan = _prepare_with_checks(tmp, root, envelope)
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = envelope
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "it was 38% when you set it, 36% now" in private, private
        assert "still clear of your line" in private.lower(), private
        assert "Checked 1 of 2 conditions this period." in private, private
        assert "Open concerns coming back next review: 1." in private, private
        public = pathlib.Path(json.loads(final.stdout)["public_card"]).read_text(encoding="utf-8")
        for fragment in ("36%", "38%", "10-Q", "churn"):
            assert fragment not in public, f"condition detail leaked {fragment!r} to the public card"


def test_a_failed_lookup_is_stated_plainly_on_the_card():
    """The failure this whole tier exists to prevent is a user walking away
    believing a tripwire is set. A lookup that came back with nothing has to say
    so where they will read it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%"])
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": "slot-seed-0",
             "check": {"lookup_status": "failed", "reason": "the publisher withdrew the release"}}]
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "not checked this period" in private and "currently blind" in private, private
        assert "the publisher withdrew the release" in private, private


def test_an_event_condition_is_watched_from_the_moment_it_is_committed():
    """This PR is what makes the card's claim true. Before the check flow the
    honest state was `unmapped`/`no_adjudicator`; the adjudicator it was missing
    is the user, and now something asks them."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        event = {"kind": "event", "criterion": "sell if the CEO leaves",
                 "query": "who is the current chief executive, and when did they take the role?"}
        final = _finalize_with(tmp, root, {"choice": "custom", "condition": event})
        assert final.returncode == 0, final.stdout + final.stderr
        slot = json.loads((root / "conditions.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert slot["tier"] == "researched" and "unmapped_reason" not in slot
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "yes-or-no call" in private and "the call stays yours" in private, private
        assert "cannot be checked for you" not in private, \
            "an event condition is watched now; saying otherwise is the old state"


# ───────────── external review round 1: six findings, six gates ─────────────

def _two_crossings(tmp, root):
    """Two conditions, both crossed. Only one can be asked about."""
    root.mkdir(parents=True, exist_ok=True)
    rows = [{"slot_id": f"slot-x-{i}", "kind": "numeric",
             "criterion": f"sell if metric {i} drops under 30%",
             "query": f"what is metric {i}?", "created": "2026-07-01", "tier": "researched",
             "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0}
            for i in range(2)]
    (root / "conditions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    envelope = [{"slot_id": "slot-x-0",
                 "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=4.0)}},
                {"slot_id": "slot-x-1",
                 "check": {"lookup_status": "ok", "observation": dict(_CROSSED, value=12.0)}}]
    return envelope, _prepare_with_checks(tmp, root, envelope)


def test_a_crossing_that_lost_the_budget_still_states_its_figure_on_the_card():
    """External review, BLOCK: a met/near_line reading that lost the one-question
    budget produced *nothing* — no question, no fact line (those were not_met
    only), no mention in the summary. A crossed line going completely unmentioned
    is the worst outcome this tier exists to prevent, and it was the one the card
    actually produced."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        envelope, plan = _two_crossings(tmp, root)
        asked = {q["slot_id"] for q in plan["question_queue"]
                 if q["kind"] == "condition_crossing"}
        assert len(asked) == 1, "the budget still binds"
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = envelope
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        deferred = ({"slot-x-0", "slot-x-1"} - asked).pop()
        index = int(deferred[-1])
        assert f"sell if metric {index} drops under 30%" in private, \
            "the deferred crossing must reach the card at all:\n" + private
        assert "past your line" in private and "next review" in private, private
        # The queued one was answered `skip` by the helper, so it is unresolved
        # too and must also speak — see the round-2 tests below for that branch
        # in isolation. Both are open, and the summary says two.
        assert "Open concerns coming back next review: 2." in private, \
            "a checked-but-unresolved crossing must count as open:\n" + private


def test_a_prepare_side_check_cannot_be_dropped_into_a_not_checked_row():
    """External review, BLOCK: prepare ingested a real reading (and may have posed
    a question about it). If the answers then omit that slot, the synthesized
    not-checked path — meant for conditions nobody looked at — overwrote the
    lookup with a row claiming nobody looked."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot_id, "check": {"lookup_status": "ok", "observation": dict(_CROSSED)}}])
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = []          # the reading silently disappears
        failed = _finalize_plan(tmp, root, plan, answers)
        assert failed.returncode != 0, failed.stdout
        assert "missing from answers.condition_checks" in failed.stdout + failed.stderr
        assert not _check_rows(root), "nothing is written when the record would lose a lookup"


def test_a_due_condition_that_was_never_looked_up_may_still_be_synthesized():
    """The other half of the same guard: the not-checked path stays legal for a
    condition that genuinely had no prepare-side check. Without this the fix
    would have closed the honest branch along with the dishonest one."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%", "sell if b drops under 30%"])
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": "slot-seed-0", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}])
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": "slot-seed-0", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        assert _finalize_plan(tmp, root, plan, answers).returncode == 0
        rows = {row["slot_id"]: row["lookup_status"] for row in _check_rows(root)}
        assert rows == {"slot-seed-0": "ok", "slot-seed-1": "not_checked"}


def test_an_envelope_cannot_carry_a_verdict_the_user_never_gave():
    """External review, BLOCK: the submittable envelope accepted `user_response`,
    so an agent could record `overridden` for a question that was never shown —
    and the stored row would be indistinguishable from one the user answered."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%"])
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": "slot-seed-0",
             "check": {"lookup_status": "ok", "observation": dict(_CROSSED),
                       "user_response": {"answer": "overridden", "answered_at": "2026-07-14"}}}]
        failed = _finalize_plan(tmp, root, plan, answers)
        assert failed.returncode != 0, failed.stdout
        assert "must not carry user_response" in failed.stdout + failed.stderr
        assert not _check_rows(root)


def test_two_names_for_one_condition_cannot_both_be_submitted():
    """External review, MARK: dedup keyed on the raw slot_id, so a superseded
    version and the live head both passed and both appended — two rows for one
    (condition, period), the second silently winning every later read."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True, exist_ok=True)
        parent = {"slot_id": "slot-root-0", "kind": "numeric",
                  "criterion": "sell if a drops under 30%", "query": "what is a?",
                  "created": "2026-06-01", "tier": "researched",
                  "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0}
        child = dict(parent, slot_id="slot-root-1", criterion="sell if a drops under 25%",
                     created="2026-07-01", revises="slot-root-0", line_id="slot-root-0",
                     threshold={"value": 25, "unit": "%", "direction": "below"})
        (root / "conditions.jsonl").write_text(
            json.dumps(parent) + "\n" + json.dumps(child) + "\n", encoding="utf-8")
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": "slot-root-0", "check": {"lookup_status": "ok", "observation": dict(_OBS)}},
            {"slot_id": "slot-root-1", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        failed = _finalize_plan(tmp, root, plan, answers)
        assert failed.returncode != 0, failed.stdout
        assert "are the same line" in failed.stdout + failed.stderr
        assert not _check_rows(root)


# ───────── a thesis falsifier is a watched condition (#416 C2 / #412) ─────────
#
# The regrow half of #416's ratified direction. Before this, the fact a user
# said would break their thesis was stored as free text and nothing ever looked
# at it again. Now it is a condition slot: the same due rotation, the same one
# crossing question, the same card lines — attached to the thesis it guards, and
# retiring with it. What it deliberately is NOT is a second reconciliation
# lifecycle: no check verdict ever moves a thesis's own status.

# The falsifier the standing `_answers` thesis row already states, as a
# condition envelope. `criterion` is that row's `exit_trigger`, verbatim.
_FALSIFIER = {
    "criterion": "Renewals weaken",
    "query": "what was the most recent reported net revenue retention rate?",
    "threshold": {"value": 100, "unit": "%", "direction": "below"},
    "observation": {"value": 118.0, "as_of": "2026-05-20", "source": "Q1 FY2027 press release",
                    "period": "FY2027Q1", "document": "8-K 2026-05-20"},
}
_FALSIFIER_CROSSED = {"value": 92.0, "as_of": "2026-08-20", "source": "10-Q",
                      "period": "FY2027Q2", "document": "10-Q 2026-08-20"}
_PLTR_CYCLE = "PLTR#2026-01-01#1"


def _thesis_answers(plan, condition=_FALSIFIER, commitment="skip"):
    answers = _answers(plan, commitment=commitment)
    if condition is not None:
        answers["thesis_updates"][0]["condition"] = dict(condition)
    return answers


def _seed_thesis_condition(tmp, root, condition=_FALSIFIER):
    """One review that states a thesis and its falsifier in the same exchange."""
    plan = _prepare(tmp, root, language="en")
    final = _finalize_plan(tmp, root, plan, _thesis_answers(plan, condition))
    assert final.returncode == 0, final.stdout + final.stderr
    store = root / "conditions.jsonl"
    assert store.exists(), "a falsifier stated with the thesis must reach the condition store"
    rows = [json.loads(line) for line
            in store.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1, rows
    return rows[0]


def _state_without_pltr(tmp, date_end=None):
    """The same review inputs with the PLTR position fully exited.

    Liveness is "this cycle is still held", so this is what a closed thesis
    cycle looks like from the engine's own state. The card's `thesis_questions`
    go with it: they name a holding that no longer exists, and the shared
    `_artifacts` fixture would otherwise keep asking about it."""
    card, state = _artifacts(tmp)
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["holdings"]["positions"] = {}
    payload["n_held"] = 0
    payload["metrics"]["n_holdings"] = 0
    if date_end:
        payload["date_end"] = date_end
    suffix = date_end or "same"
    path = pathlib.Path(tmp) / f"state-exited-{suffix}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    card_payload = json.loads(card.read_text(encoding="utf-8"))
    card_payload["thesis_questions"] = []
    card_path = pathlib.Path(tmp) / "card-exited.json"
    card_path.write_text(json.dumps(card_payload, ensure_ascii=False), encoding="utf-8")
    return card_path, path


def test_a_stated_falsifier_becomes_a_watched_condition_attached_to_its_thesis():
    """#416's ruling: a thesis falsifier is precisely a condition slot attached
    to a cycle. Before this it was free text nobody read back — the user named
    the one fact that would change their mind and the product forgot it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot = _seed_thesis_condition(tmp, root)
        assert slot["thesis_cycle_id"] == _PLTR_CYCLE
        assert slot["criterion"] == "Renewals weaken", "the user's own falsifier, verbatim"
        assert slot["tier"] == "researched" and slot["baseline_verdict"] == "not_met"
        # The firewall #412 established is untouched: still never a rule.
        rules = root / "rules.jsonl"
        assert not (rules.exists() and rules.read_text(encoding="utf-8").strip()), \
            "a falsifier must never become a rules.jsonl row"
        # And no copy of the condition rides into the thesis store, where nothing
        # would ever reconcile it.
        thesis_rows = [json.loads(line) for line
                       in (root / "theses.jsonl").read_text(encoding="utf-8").splitlines()
                       if line.strip()]
        assert thesis_rows and all("condition" not in row for row in thesis_rows), thesis_rows
        assert thesis_rows[0]["exit_trigger"] == "Renewals weaken", "the thesis still states it"


def test_a_falsifier_that_paraphrases_the_thesis_fails_closed():
    """The record stores `criterion` and the thesis stores `exit_trigger`. One
    being a tidied version of the other means the condition being watched is not
    the one the thesis says breaks it — the same refusal `commitment.rule`
    already earns (#396)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root, language="en")
        answers = _thesis_answers(plan, dict(_FALSIFIER, criterion="renewal growth slows down"))
        failed = _finalize_plan(tmp, root, plan, answers)
        assert failed.returncode != 0, failed.stdout
        assert "exit_trigger as its criterion, verbatim" in failed.stdout + failed.stderr
        assert not (root / "conditions.jsonl").exists(), "nothing is committed on a rejected envelope"


def test_a_thesis_falsifier_joins_the_same_rotation_and_names_the_thesis_it_guards():
    """No separate budget: it is due like every other condition. What is extra is
    the attribution the engine stamps, so no later reader has to work out which
    thesis a slot belongs to."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_thesis_condition(tmp, root)
        snapshot = _prepare(tmp, root, language="en")["state_snapshot"]
        due = snapshot["condition_slots_due"]
        assert [row["criterion"] for row in due] == ["Renewals weaken"]
        assert due[0]["thesis_link"] == {"cycle_id": _PLTR_CYCLE, "ticker": "PLTR"}
        assert snapshot["condition_slots_summary"] == {
            "lines_total": 1, "due_now": 1, "beyond_cap": 0, "unmapped_lines": 0,
            "retired_lines": 0, "unreadable_slots": 0, "unreadable_checks": 0}


def test_a_falsifier_whose_position_is_gone_stops_being_checked_and_says_so():
    """A condition guarding a thesis on a position the user fully exited has
    nothing left to protect — there is nothing to sell if it triggers. It must
    stop occupying the lookup cap and stop raising questions.

    It leaves `lines_total` with it, and is counted in `retired_lines` instead.
    Left inside the total, the card's own arithmetic would report it as a
    concern "coming back next review" forever, which is the opposite of true."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_thesis_condition(tmp, root)
        card, state = _state_without_pltr(tmp)
        run = _run("prepare", "--root", root, "--language", "en",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        snapshot = _pending_plan(root, run.stdout)["state_snapshot"]
        assert snapshot["condition_slots_due"] == [], \
            "a closed thesis cycle must not occupy a lookup slot"
        assert snapshot["condition_slots_summary"]["lines_total"] == 0
        assert snapshot["condition_slots_summary"]["retired_lines"] == 1
        # The row itself is untouched: the store is append-only and it is a fact
        # about what the user meant when they wrote it.
        assert len((root / "conditions.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 1


def test_retirement_is_said_once_in_the_review_where_it_happens():
    """External review, round 2 MARK. Retiring a condition silently reads as the
    system having quietly stopped watching, not as a deliberate close — the user
    saw this line last week and it simply vanishes.

    So it is announced, and it is announced **once**: the engine says "retired
    this period" only while the line's last check belongs to the immediately
    preceding review, which is exactly the period it was still being looked at.
    A state sentence would print for the rest of the user's history; an event
    sentence is said and done."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot = _seed_thesis_condition(tmp, root)
        # Review 2: still held, so the line is due and gets its per-period row.
        plan = _prepare(tmp, root, language="en")
        assert _finalize_plan(tmp, root, plan, _thesis_answers(plan, None)).returncode == 0
        assert [row["slot_id"] for row in _check_rows(root)] == [slot["slot_id"]], _check_rows(root)

        def _exited_review(date_end):
            card, state = _state_without_pltr(tmp, date_end)
            run = _run("prepare", "--root", root, "--language", "en",
                       "--card-json", card, "--state-json", state)
            assert run.returncode == 0, run.stdout + run.stderr
            exited = _pending_plan(root, run.stdout)
            answers = _answers(exited, commitment="skip")
            answers["thesis_updates"] = []      # the position is gone with its thesis
            done = _finalize_plan(tmp, root, exited, answers)
            assert done.returncode == 0, done.stdout + done.stderr
            return exited, pathlib.Path(
                json.loads(done.stdout)["private_card"]).read_text(encoding="utf-8")

        # Review 3: the position is gone. The line retires, and the card says so.
        exited, private = _exited_review("2026-07-21")
        assert exited["state_snapshot"]["condition_slots_retired"] == [
            {"cycle_id": _PLTR_CYCLE, "ticker": "PLTR", "criterion": "Renewals weaken"}]
        assert "no longer checked from here on" in private, \
            "a retiring condition must not simply vanish:\n" + private
        assert "PLTR" in private and "Renewals weaken" in private, private

        # Review 4: same state, one period later. It is old news and stays quiet.
        again, private_again = _exited_review("2026-07-28")
        assert again["state_snapshot"]["condition_slots_retired"] == [], \
            "retirement is an event, not a state the card repeats forever"
        assert "no longer checked from here on" not in private_again, private_again


def test_a_falsifier_whose_position_is_gone_raises_no_question_even_if_checked():
    """The other half of retirement, and the one a due-list test cannot see. A
    slot held back by the cap is still a legal thing to submit a check for, so
    "not in the due list" is not by itself "cannot be asked about". A closed
    thesis cycle has to be refused at the question layer too, or the user is
    asked to adjudicate a line on a position they no longer hold."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot = _seed_thesis_condition(tmp, root)
        card, state = _state_without_pltr(tmp)
        path = _write_json(tmp, "condition-checks.json", {"condition_checks": [
            {"slot_id": slot["slot_id"],
             "check": {"lookup_status": "ok", "observation": dict(_FALSIFIER_CROSSED)}}]})
        run = _run("prepare", "--root", root, "--language", "en",
                   "--card-json", card, "--state-json", state, "--condition-checks", path)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        assert not [q for q in plan["question_queue"]
                    if q["kind"] in ("condition_crossing", "condition_basis")], \
            "a condition guarding an exited position must not be adjudicated"


def test_a_crossed_falsifier_asks_about_it_beside_the_thesis_it_belongs_to():
    """The adjudication always arrives with its thesis. The card has no thesis
    block, so the ticker rides the question the user is actually asked — in the
    engine's own fallback stem *and* in the grounded surface an agent authors
    from, because the host that could not bind the second one is exactly where
    an unattributed adjudication would land."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot = _seed_thesis_condition(tmp, root)
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot["slot_id"],
             "check": {"lookup_status": "ok", "observation": dict(_FALSIFIER_CROSSED)}}])
        question = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
        assert question["ticker"] == "PLTR"
        assert "PLTR" in question["question"], question["question"]
        assert question["question_opportunity"]["context"].get("ticker") == "PLTR", \
            question["question_opportunity"]["context"]
        assert question["question_opportunity"]["context"]["condition"]["criterion"] \
            == "Renewals weaken", "the user's own words are still the anchor"


def test_a_live_falsifier_past_the_lookup_cap_is_still_adjudicated():
    """The counterweight to retirement, and the edge that separates the two
    facts. "Guards a thesis" and "that thesis is still live" are different
    things, and only the second silences a question. Attribution scoped to the
    capped due list would have collapsed them: a falsifier the cap held back
    this week — still a legal thing to submit a check for — would have been
    indistinguishable from one whose position was sold, and its crossing
    dropped for the one reason this whole tier exists to prevent."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot = _seed_thesis_condition(tmp, root)
        # Push it past the cap: give it a recent check (the rotation puts
        # never-checked lines first) and add a full cap's worth of lines that
        # have never been looked at.
        _seed_conditions(tmp, root, [f"sell if metric {i} drops under 30%"
                                     for i in range(review_engine.CONDITION_LOOKUP_CAP)])
        (root / "conditions.jsonl").write_text(
            json.dumps(slot) + "\n" + (root / "conditions.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8")
        (root / "condition_checks.jsonl").write_text(json.dumps(
            {"check_id": "c-old", "slot_id": slot["slot_id"], "session_id": "s0",
             "date_end": "2026-07-05", "created": "2026-07-05", "lookup_status": "ok",
             "observation": {"value": 118.0, "as_of": "2026-05-20", "source": "release"},
             "information_state": "new_period", "engine_verdict": "not_met",
             "final_verdict": "not_met", "verdict_source": "engine"}) + "\n", encoding="utf-8")
        plan = _prepare(tmp, root, language="en")
        due_ids = {row["slot_id"] for row in plan["state_snapshot"]["condition_slots_due"]}
        assert slot["slot_id"] not in due_ids, "fixture precondition: it is past the cap"
        assert plan["state_snapshot"]["condition_slots_summary"]["retired_lines"] == 0, \
            "held back by the cap is not retired"
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot["slot_id"],
             "check": {"lookup_status": "ok", "observation": dict(_FALSIFIER_CROSSED)}}])
        crossings = [q for q in plan["question_queue"] if q["kind"] == "condition_crossing"]
        assert len(crossings) == 1, \
            f"a live falsifier past the cap still earns its crossing: {plan['question_queue']}"
        assert crossings[0]["line_id"] == slot["slot_id"]
        assert crossings[0]["ticker"] == "PLTR", "and it still arrives with its thesis"
        # External review, round 2 BLOCK: the question layer was only half of it.
        # The card joins a check to its condition through what the engine
        # stamped, and that stamp lived only on the capped due list — so this
        # reading was asked about and then absent from the card, while the
        # summary still counted it as checked. It must reach the card, carry its
        # attribution, and count as an open concern like any other.
        answers = _thesis_answers(plan, condition=None)
        answers["condition_checks"] = [
            {"slot_id": slot["slot_id"],
             "check": {"lookup_status": "ok", "observation": dict(_FALSIFIER_CROSSED)}}]
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "Renewals weaken —" in private, \
            "a beyond-cap reading must reach the card at all:\n" + private
        assert "break your PLTR thesis" in private, \
            "and it must arrive with the thesis it guards:\n" + private
        assert "past your line" in private, private
        assert "Open concerns coming back next review:" in private, \
            "and count in the summary's arithmetic like any other:\n" + private


def _relink_scenario(tmp, root):
    """An incomplete opening snapshot, then the transaction review that reveals
    the holding's real cycle start.

    Returns ``(provisional_cycle_id, card, state, history)``. The thesis is
    relinked from the snapshot's provisional cycle id to the revealed one, and
    the relink exists **only in the plan** until that review is finalized — which
    is the whole point of the scenario below."""
    opening, _path = _snapshot_prepare(tmp, root, payload={
        "as_of": "2026-07-16", "is_complete": False,
        "positions": [{"ticker": "PLTR", "shares": 10, "avg_cost": 100,
                       "market": "US", "currency": "USD"}]}, language="en")
    answers = _write_json(tmp, "relink-open-answers.json",
                          _snapshot_answers(opening, commitment="skip"))
    narrative = _write_json(tmp, "relink-open-narrative.json", _snapshot_narrative(opening))
    done = _run("finalize", "--root", root, "--session-id", opening["session_id"],
                "--answers", answers, "--narrative", narrative)
    assert done.returncode == 0, done.stdout + done.stderr
    prior = json.loads((root / "sessions" / opening["session_id"] / "bundle.json")
                       .read_text(encoding="utf-8"))["thesis_updates"][0]

    card, state = _artifacts(tmp)
    card_data = json.loads(card.read_text(encoding="utf-8"))
    card_data["thesis_questions"] = []
    state_data = json.loads(state.read_text(encoding="utf-8"))
    state_data.update({"date_start": "2026-07-01", "date_end": "2026-07-18", "n_held": 1})
    state_data["holdings"] = {
        "as_of": "2026-07-18", "derived_from": "trades_csv", "is_complete": False,
        "positions": {"PLTR": {"shares": 10, "cost": 1000, "avg_cost": 100,
                               "market": "US", "currency": "USD",
                               "cycle_start": "2026-07-01",
                               "cycle_id": "PLTR#2026-07-01#1", "add_count": 0,
                               "decision_cursor": None}}}
    card.write_text(json.dumps(card_data, ensure_ascii=False), encoding="utf-8")
    state.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
    history = pathlib.Path(tmp) / "relink-history.csv"
    history.write_text("Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
                       "PLTR,BUY,10,100,2026-07-01,Trade,US,USD\n", encoding="utf-8")
    return prior["cycle_id"], card, state, history


def test_a_falsifier_live_only_through_a_relink_survives_the_cap_and_reaches_the_card():
    """External review, round 3 BLOCK — the two-readers pattern at the *input*
    layer (docs/development-guide.md section 7).

    Liveness had one derivation, but its input was assembled twice: the plan
    joined the thesis history with this session's cycle relinks, and the card
    context re-read the history alone. A relink is not on disk until the review
    that made it is finalized, so a condition whose cycle is live *only* via a
    relink read as dead at finalize — and, if it was also past the lookup cap,
    landed in neither `condition_slots_due` nor `condition_slots_context` and
    fell off the card again. A single reader is necessary and not sufficient
    when its ingredients are composed in two places.

    Each half of this had a test; the combination had none, which is exactly why
    it survived two rounds."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        provisional, card, state, history = _relink_scenario(tmp, root)

        # A falsifier written against the provisional cycle, plus a full cap's
        # worth of never-checked lines, so it sorts past the cap (its own check
        # is older than "never").
        guarded = {"slot_id": "slot-guard-0", "kind": "numeric",
                   "criterion": "Renewals weaken", "thesis_cycle_id": provisional,
                   "query": "what was the most recent reported net revenue retention rate?",
                   "created": "2026-07-16", "tier": "researched",
                   "threshold": {"value": 100, "unit": "%", "direction": "below"},
                   "near_line": 10.0,
                   "baseline": {"value": 118.0, "as_of": "2026-05-20", "source": "release"},
                   "baseline_verdict": "not_met"}
        filler = [{"slot_id": f"slot-fill-{i}", "kind": "numeric",
                   "criterion": f"sell if metric {i} drops under 30%",
                   "query": f"what is the current reading for item {i}?",
                   "created": "2026-07-16", "tier": "researched",
                   "threshold": {"value": 30, "unit": "%", "direction": "below"},
                   "near_line": 3.0}
                  for i in range(review_engine.CONDITION_LOOKUP_CAP)]
        (root / "conditions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in [guarded] + filler), encoding="utf-8")
        (root / "condition_checks.jsonl").write_text(json.dumps(
            {"check_id": "c-guard", "slot_id": "slot-guard-0", "session_id": "s0",
             "date_end": "2026-07-17", "created": "2026-07-17", "lookup_status": "ok",
             "observation": {"value": 118.0, "as_of": "2026-05-20", "source": "release"},
             "information_state": "new_period", "engine_verdict": "not_met",
             "final_verdict": "not_met", "verdict_source": "engine"}) + "\n", encoding="utf-8")

        checks = _write_json(tmp, "relink-checks.json", {"condition_checks": [
            {"slot_id": "slot-guard-0",
             "check": {"lookup_status": "ok",
                       "observation": {"value": 92.0, "as_of": "2026-07-18",
                                       "source": "10-Q", "period": "FY2027Q2",
                                       "document": "10-Q 2026-07-18"}}}]})
        run = _run("prepare", history, "--root", root, "--language", "en",
                   "--card-json", card, "--state-json", state,
                   "--session-nonce", "relink-beyond-cap", "--condition-checks", checks)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        snapshot = plan["state_snapshot"]

        assert len(snapshot["thesis_cycle_relinks"]) == 1, "fixture precondition: a relink happened"
        assert "slot-guard-0" not in {row["slot_id"] for row in snapshot["condition_slots_due"]}, \
            "fixture precondition: it is past the cap"
        # The mirror case: live via a relink is live. It must not read as retired.
        assert snapshot["condition_slots_retired"] == [], \
            "a line still live through a relink must never announce its retirement"
        assert snapshot["condition_slots_summary"]["retired_lines"] == 0
        crossing = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
        assert crossing["ticker"] == "PLTR"

        answers = {"session_id": plan["session_id"],
                   "answers": [{"question_id": q["id"], "choice": "skip"}
                               for q in plan["question_queue"]],
                   "thesis_updates": [], "observations": [],
                   "commitment": {"choice": "skip"},
                   "condition_checks": json.loads(checks.read_text())["condition_checks"]}
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "Renewals weaken —" in private, \
            "a relink-live beyond-cap reading must reach the card:\n" + private
        assert "break your PLTR thesis" in private, \
            "and arrive with the thesis it guards:\n" + private
        assert "Open concerns coming back next review:" in private, \
            "and count in the summary like any other:\n" + private


def test_a_genuinely_exited_cycle_still_retires_when_a_relink_exists_for_another_ticker():
    """The counterweight the relink fix must not break: composing the relinks in
    cannot resurrect a line whose position is really gone. An alias only ever
    inherits liveness from a cycle that has it, so a dead cycle stays dead."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        index = review_engine._thesis_cycle_index(
            {"PLTR": {"cycle_id": "PLTR#2026-07-01#1"}},
            [{"cycle_id": "NVDA#2026-01-05#1", "ticker": "NVDA"}],
            [{"cycle_id": "PLTR#2026-07-01#1", "ticker": "PLTR",
              "cycle_provenance": {"kind": "incomplete_snapshot_cycle_relink",
                                   "from_cycle_id": "PLTR#2026-07-16#1"}}])
        assert index["PLTR#2026-07-16#1"]["live"] is True, "the provisional id is live via the relink"
        assert index["PLTR#2026-07-01#1"]["live"] is True
        assert index["NVDA#2026-01-05#1"] == {"ticker": "NVDA", "live": False}, \
            "a cycle with no live position stays dead however many relinks are in the batch"
        del root


def test_a_condition_that_guards_nothing_still_names_no_position():
    """The counterweight, and the reason #412's original prohibition stands. A
    portfolio-level commitment condition is attached to no position, so grounding
    its stem in one would invite the question to argue about the position instead
    of the line."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_condition(tmp, root)
        slot_id = json.loads((root / "conditions.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["slot_id"]
        plan = _prepare_with_checks(tmp, root, [
            {"slot_id": slot_id, "check": {"lookup_status": "ok", "observation": dict(_CROSSED)}}])
        question = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
        assert question["ticker"] is None
        assert "ticker" not in question["question_opportunity"]["context"]


def test_a_falsifier_that_crossed_never_moves_the_thesis_status_by_itself():
    """#416 forbids a second reconciliation lifecycle, and an automatic status
    flip would be exactly that: the engine deciding a thesis is falsified from a
    lookup it performed. The user's answer is the verdict of record for the
    *check*; what happens to the thesis stays theirs to say."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        slot = _seed_thesis_condition(tmp, root)
        envelope = [{"slot_id": slot["slot_id"],
                     "check": {"lookup_status": "ok", "observation": dict(_FALSIFIER_CROSSED)}}]
        plan = _prepare_with_checks(tmp, root, envelope)
        question = next(q for q in plan["question_queue"] if q["kind"] == "condition_crossing")
        answers = _thesis_answers(plan, condition=None)
        for row in answers["answers"]:
            if row["question_id"] == question["id"]:
                row["choice"] = "confirmed"
        answers["condition_checks"] = envelope
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        # The check says the line was crossed and the user agreed.
        check = next(r for r in _check_rows(root) if r["slot_id"] == slot["slot_id"])
        assert (check["engine_verdict"], check["final_verdict"]) == ("met", "met")
        assert check["verdict_source"] == "user"
        # The thesis status stayed where the user's own thesis events put it.
        # (`modified` here is the second review re-stating the thesis, which is
        # the ordinary agent-authored path and has nothing to do with the check.)
        thesis_rows = [json.loads(line) for line
                       in (root / "theses.jsonl").read_text(encoding="utf-8").splitlines()
                       if line.strip()]
        pltr = [row for row in thesis_rows if row.get("cycle_id") == _PLTR_CYCLE]
        assert pltr and not any(row.get("status") == "falsified" for row in pltr), pltr
        assert not any(row.get("final_outcome") for row in pltr), pltr
        for name in ("theses.jsonl", "thesis_decisions.jsonl", "exit_narratives.jsonl"):
            path = root / name
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            assert "falsified" not in text, \
                f"a condition verdict must never write a thesis outcome into {name}"


def test_a_card_that_trims_readings_says_it_trimmed_them():
    """External review, MARK: with five all-clear readings the card showed two and
    the summary stayed silent, because it only spoke when something was
    unchecked. Three readings vanished without a word — the same completeness
    claim the cap disclosure exists to prevent, one level up."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, [f"sell if metric {i} drops under 30%" for i in range(5)])
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan, commitment="skip")
        answers["condition_checks"] = [
            {"slot_id": f"slot-seed-{i}",
             "check": {"lookup_status": "ok", "observation": dict(_OBS)}} for i in range(5)]
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "Checked 5 of 5 conditions this period." in private, private
        assert "shows 2 of 5 readings" in private, \
            "a trimmed card must say how much it is showing:\n" + private
        shown = sum(1 for i in range(5) if f"sell if metric {i} drops under 30% —" in private)
        assert shown == 2, f"the per-kind cap still binds, showed {shown}"


# #438's recorded followup, closed here: `CONDITION_CARD_LINES` used to trim a
# dual-open row (crossing unanswered/deferred *and* basis open) exactly like
# any other, so a condition with two live concerns could vanish from the card
# entirely while the summary kept counting both. Built directly against
# `card_renderer._reconciliation_lines` rather than through the CLI, like
# `test_reconciliation_opens_the_card_with_prior_commitment` above — the
# per-kind trim is the renderer's own concern, not `review.py`'s crossing
# selection or one-question budget, so the fixture should not have to drive
# either.
def _cap_group_slot(index):
    return {"slot_id": f"slot-cap-{index}", "line_id": f"slot-cap-{index}", "kind": "numeric",
            "criterion": f"sell if metric {index} drops under 30%",
            "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0}


def _cap_group_check(index, basis_open=False):
    """A check the engine reads as a crossing candidate that lost the
    one-question budget (`deferred`, never queued), so every row built this
    way lands in the same per-kind group and the cap alone decides what is
    kept. ``basis_open`` makes the row dual-open."""
    check = {"slot_id": f"slot-cap-{index}", "lookup_status": "ok",
             "observation": {"value": 21.0, "as_of": "2026-08-20", "source": "10-Q"},
             "engine_verdict": "met", "final_verdict": "met"}
    if basis_open:
        check["basis_alert"] = {"note": "the reporting segment was restated"}
    return check


def _cap_group_bundle(count, dual_index):
    """``count`` deferred-crossing conditions sharing one per-kind cap. The
    condition at ``dual_index`` (or none, if ``None``) also carries an open
    basis concern, making it dual-open."""
    slots = [_cap_group_slot(i) for i in range(count)]
    checks = [_cap_group_check(i, basis_open=(i == dual_index)) for i in range(count)]
    summary = {"lines_total": count, "due_now": count, "beyond_cap": 0,
               "unmapped_lines": 0, "unreadable_slots": 0, "unreadable_checks": 0}
    return {"review_plan": {"state_snapshot": {
                "condition_slots_due": slots, "condition_slots_summary": summary}},
            "condition_checks": checks}


def test_a_dual_open_row_beyond_the_cap_still_prints_both_notes_and_the_trim_count_excludes_it():
    """Owner ruling, 2026-07-27 (#412 recorded followup from #438): information
    completeness wins over the per-kind card cap. Four conditions land in the
    same `deferred` group — three plain, one also carrying an open basis
    concern (dual-open) and placed last, the position the old unconditional
    `rows[:CONDITION_CARD_LINES]` cap would have cut. It must render anyway,
    with both its notes, and the trim sentence must count only the plain row
    the cap actually dropped — not the dual-open row it let through."""
    bundle = _cap_group_bundle(4, dual_index=3)
    text = "\n".join(card_renderer._reconciliation_lines(bundle, "en"))
    assert "sell if metric 0 drops under 30% —" in text
    assert "sell if metric 1 drops under 30% —" in text
    assert "sell if metric 2 drops under 30% —" not in text, \
        "the cap still trims a plain row once two are already shown:\n" + text
    assert "sell if metric 3 drops under 30% —" in text, \
        "the dual-open row must survive the cap:\n" + text
    assert "past your line; I will ask you about it next review" in text
    assert "the reporting segment was restated" in text, \
        "the exempted row keeps both its notes on the one reading:\n" + text
    assert "shows 3 of 4 readings" in text, \
        "the trim count must not charge the dual-open row as withheld:\n" + text
    assert "Open concerns coming back next review: 5." in text, \
        "three single-concern rows plus one two-concern row is five:\n" + text


def test_non_dual_rows_beyond_the_cap_are_still_trimmed_and_counted_as_before():
    """Regression pin for the exemption above: with no basis concern anywhere,
    the per-kind cap keeps behaving exactly as it did before this change —
    first two shown, the rest counted as trimmed."""
    bundle = _cap_group_bundle(4, dual_index=None)
    text = "\n".join(card_renderer._reconciliation_lines(bundle, "en"))
    assert "sell if metric 0 drops under 30% —" in text
    assert "sell if metric 1 drops under 30% —" in text
    assert "sell if metric 2 drops under 30% —" not in text
    assert "sell if metric 3 drops under 30% —" not in text, \
        "no row here is dual-open, so the cap trims it like any other:\n" + text
    assert "shows 2 of 4 readings" in text, text
    assert "Open concerns coming back next review: 4." in text, text


def test_reconciliation_survives_a_second_revision_of_the_same_condition():
    """External review, MARK: the card matched checks to the prior commitment by
    slot_id. A check names the live head of its line, so after a second revision
    that head is neither the commitment's slot nor the line root — the then/now
    went quiet exactly when the condition had the most history behind it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True, exist_ok=True)
        # commit -> revise -> revise, written directly: three rows, one line.
        rows = [{"slot_id": "slot-gen-0", "kind": "numeric",
                 "criterion": "sell if growth drops under 30%", "query": "what is growth?",
                 "created": "2026-05-01", "tier": "researched",
                 "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0,
                 "baseline": {"value": 38.0, "as_of": "2026-04-20", "source": "release"},
                 "baseline_verdict": "not_met"}]
        for index, (line_value, created) in enumerate(((28, "2026-06-01"), (25, "2026-07-01")), 1):
            rows.append({**rows[0], "slot_id": f"slot-gen-{index}",
                         "criterion": f"sell if growth drops under {line_value}%",
                         "created": created, "revises": f"slot-gen-{index - 1}",
                         "line_id": "slot-gen-0",
                         "threshold": {"value": line_value, "unit": "%", "direction": "below"}})
        (root / "conditions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        # The prior commitment names the ROOT slot — two revisions ago.
        (root / "last_state.json").write_text(json.dumps({
            "date_end": "2026-05-01",
            "commitment": {"rule": rows[0]["criterion"], "origin": "custom",
                           "condition": rows[0]}}), encoding="utf-8")
        plan = _prepare(tmp, root, language="en")
        prior = plan["state_snapshot"]["prior_commitment"]
        assert prior["condition"]["line_id"] == "slot-gen-0", \
            "the engine resolves the line once; the card never derives it"
        answers = _answers(plan, commitment="skip")
        # The check lands on the NEWEST slot_id, which is what the engine resolves to.
        answers["condition_checks"] = [
            {"slot_id": "slot-gen-2", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]
        final = _finalize_plan(tmp, root, plan, answers)
        assert final.returncode == 0, final.stdout + final.stderr
        row = _check_rows(root)[0]
        assert row["slot_id"] == "slot-gen-2", "a check names the line's live head"
        private = pathlib.Path(json.loads(final.stdout)["private_card"]).read_text(encoding="utf-8")
        assert "when you set it, 36% now" in private, \
            "the then/now must still connect across two revisions:\n" + private


def test_a_snapshot_review_has_no_condition_flow():
    """A position snapshot carries no review history to reconcile against — the
    same reason it queues no questions at all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        _seed_conditions(tmp, root, ["sell if a drops under 30%"])
        path = _write_json(tmp, "checks.json", {"condition_checks": [
            {"slot_id": "slot-seed-0", "check": {"lookup_status": "ok", "observation": dict(_OBS)}}]})
        card, state = _artifacts(tmp)
        refused = _run("prepare", "--root", root, "--language", "en", "--route", "snapshot_review",
                       "--card-json", card, "--state-json", state, "--condition-checks", path)
        assert refused.returncode != 0
        assert "position snapshot has no standing conditions" in refused.stdout + refused.stderr


def _rule_root(tmp, rows=None):
    root = pathlib.Path(tmp) / "coach"
    root.mkdir(exist_ok=True)
    rows = rows or [{"rule_id": "rule-abc-0", "text": "no adding into a loser",
                     "problem_key": "avgdown_breach", "created": "2026-06-13"}]
    (root / "rules.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return root


def test_mute_rule_is_a_preference_not_a_rule_row():
    """#416: a rule the user stops wanting to be asked about had two exits — keep
    answering, or lose it. Muting is the third.

    It lives in `profile.json`, beside the position cap, because `rules.jsonl` is
    a tier-3 rebuildable projection: `repair-projections` rebuilds it from
    committed bundles, and a bundle carries commitments only. A mute written
    there survives until the first repair and then silently un-mutes — the user
    starts being asked again with no signal. This test pins the file that must
    change and the file that must not."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp)
        rules_before = (root / "rules.jsonl").read_text(encoding="utf-8")
        muted = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0")
        assert muted.returncode == 0, muted.stdout + muted.stderr
        assert json.loads(muted.stdout)["status"] == "muted"
        assert (root / "rules.jsonl").read_text(encoding="utf-8") == rules_before, \
            "muting must not write a rule row — that is what moves the breach-question key"
        profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        assert profile["muted_rules"] == ["rule-abc-0"]

        tracking, silent = problems_engine.load_rules(
            str(root / "rules.jsonl"), profile["muted_rules"])
        assert tracking == [] and len(silent) == 1
        assert silent[0]["rule_id"] == "rule-abc-0" and silent[0]["created"] == "2026-06-13"

        back = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0", "--unmute")
        assert back.returncode == 0, back.stdout + back.stderr
        profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        assert not profile.get("muted_rules"), "unmuting leaves nothing silenced"
        tracking, silent = problems_engine.load_rules(
            str(root / "rules.jsonl"), profile.get("muted_rules") or [])
        assert len(tracking) == 1 and silent == []


def test_mute_rule_leaves_the_rest_of_the_profile_alone():
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp)
        assert _run("set-cap", "--root", root, "--pct", "0.25").returncode == 0
        assert _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0").returncode == 0
        profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        assert profile["max_position_pct"] == 0.25 and profile["muted_rules"] == ["rule-abc-0"]


def test_mute_rule_takes_a_rule_line_not_a_superseded_version():
    """A superseded id names a version, not a rule. Accepting one was how the
    first cut forked a `revises` chain into two live heads."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp, rows=[
            {"rule_id": "rule-abc-0", "text": "no adding into a loser",
             "problem_key": "avgdown_breach", "created": "2026-06-13"},
            {"rule_id": "rule-def-0", "text": "wait a day before adding",
             "problem_key": "avgdown_breach", "created": "2026-06-20",
             "revises": "rule-abc-0"}])
        # The chain root is the identity, so the head id and the root id name the
        # same live line and both resolve to one entry.
        for rule_id in ("rule-def-0", "rule-abc-0"):
            probe = _run("mute-rule", "--root", root, "--rule-id", rule_id)
            if rule_id == "rule-def-0":
                assert probe.returncode == 0, probe.stdout + probe.stderr
                assert json.loads(probe.stdout)["rule_line_id"] == "rule-abc-0"
            else:
                assert probe.returncode != 0 and "already muted" in probe.stdout + probe.stderr, \
                    "the root id names the same line, so it must not mute a second time"
        profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        assert profile["muted_rules"] == ["rule-abc-0"], "one line, one entry, no duplicates"
        tracking, silent = problems_engine.load_rules(
            str(root / "rules.jsonl"), profile["muted_rules"])
        assert tracking == [] and len(silent) == 1, "a revision inherits the mute, chain intact"


def test_a_mute_for_a_rule_that_no_longer_exists_can_still_be_cleared():
    """A reset or a hand-edited file can leave the rule gone and the preference
    behind. Refusing to touch it would make that entry permanent."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp)
        assert _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0").returncode == 0
        (root / "rules.jsonl").write_text("", encoding="utf-8")
        cleared = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0", "--unmute")
        assert cleared.returncode == 0, cleared.stdout + cleared.stderr
        profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        assert not profile.get("muted_rules")
        still_gone = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0", "--unmute")
        assert still_gone.returncode != 0, "with nothing left to clear it is an error again"


def test_the_profile_keeps_every_muted_line_in_a_stable_order():
    """Two entries, so ordering is actually observed: an unpinned order makes the
    file churn between runs and every diff of it unreadable."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp, rows=[
            {"rule_id": "rule-zzz-0", "text": "cap at 20%", "problem_key": "oversize",
             "created": "2026-06-13"},
            {"rule_id": "rule-aaa-0", "text": "no adding into a loser",
             "problem_key": "avgdown_breach", "created": "2026-06-13"}])
        for rule_id in ("rule-zzz-0", "rule-aaa-0"):
            assert _run("mute-rule", "--root", root, "--rule-id", rule_id).returncode == 0
        profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        assert profile["muted_rules"] == ["rule-aaa-0", "rule-zzz-0"]


def test_a_rule_muted_this_session_cannot_also_be_revised_this_session():
    """The breach question was frozen before the mute existed. Letting both land
    means the replacement inherits the silence and a rule the user wrote *this
    week* is born muted, absent from the rotation and from #292's disclosure,
    with nothing said. The two answers contradict; the engine says so."""
    plan = _commitment_plan()
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp)
        plan["state_root"] = str(root)
        assert _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0").returncode == 0
        # `oversize` is what session.PKEY maps max_pos_pct to, so the replacement
        # is otherwise valid: without the guard this commitment lands silently,
        # which is the whole point of the pair.
        plan["question_queue"] = [{"id": "q1", "kind": "rule_breach", "rule_id": "rule-abc-0",
                                   "problem_key": "oversize"}]
        answers = {"commitment": {"choice": "custom", "rule": "cap any single position at 15%",
                                  "metric_key": "max_pos_pct", "revises_rule_id": "rule-abc-0"},
                   "answers": [{"question_id": "q1", "choice": "revise_rule", "note": "too tight"}]}
        try:
            review_engine._resolve_commitment(plan, answers)
        except review_engine.ReviewError as exc:
            assert "muted" in str(exc) and "revised in the same review" in str(exc), exc
        else:
            raise AssertionError("a muted line must not be revised in the same review")


def test_mute_rule_fails_closed_on_both_halves_of_its_state_guard():
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp)
        missing = _run("mute-rule", "--root", root, "--rule-id", "rule-nope-0")
        assert missing.returncode != 0 and "no live rule" in missing.stdout + missing.stderr

        unmute_first = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0", "--unmute")
        assert unmute_first.returncode != 0, \
            "unmuting a tracked rule is a no-op the user should hear about, not a silent write"
        assert "already tracking" in unmute_first.stdout + unmute_first.stderr
        assert not (root / "profile.json").exists(), "a refused mute writes nothing"

        assert _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0").returncode == 0
        again = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0")
        assert again.returncode != 0 and "already muted" in again.stdout + again.stderr


def test_the_state_guard_reads_the_engine_not_its_own_copy_of_the_answer():
    """`load_rules` also honours a row-level `status: "muted"` (contract since
    #137). A guard that consults only the profile would tell the user a rule is
    tracked while the engine's own reader silences it — one boolean, two sources
    of truth. This fixture is the only one where the two answers differ."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _rule_root(tmp, rows=[
            {"rule_id": "rule-abc-0", "text": "cap at 20%", "problem_key": "oversize",
             "created": "2026-06-13", "status": "muted"}])
        redundant = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0")
        assert redundant.returncode != 0, \
            "the engine already silences this rule; muting it again is not a fresh action"
        assert "already muted" in redundant.stdout + redundant.stderr
        assert not (root / "profile.json").exists(), "and it must not write a redundant entry"

        back = _run("mute-rule", "--root", root, "--rule-id", "rule-abc-0", "--unmute")
        assert back.returncode != 0, "this command does not own that mute, so it cannot lift it"
        assert "rules.jsonl" in back.stdout + back.stderr, \
            "the refusal has to name what is actually silencing the rule, or it is a dead end"


def test_a_mute_survives_the_documented_projection_repair():
    """`repair-projections` rebuilds `rules.jsonl` from committed bundles. The
    mute has to outlive that, or the recovery path the docs recommend silently
    un-mutes everything the user silenced."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root, language="en")
        answers = _answers(plan)
        answers["commitment"] = {"choice": "custom", "rule": "cap any single position at 20%",
                                 "metric_key": "max_pos_pct", "goal": "down"}
        answers_path = pathlib.Path(tmp) / "answers.json"
        narrative_path = pathlib.Path(tmp) / "narrative.json"
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative("en")), encoding="utf-8")
        assert _run("finalize", "--root", root, "--session-id", plan["session_id"],
                    "--answers", answers_path, "--narrative", narrative_path).returncode == 0
        committed = [json.loads(line) for line
                     in (root / "rules.jsonl").read_text(encoding="utf-8").splitlines() if line]
        rule_id = committed[0]["rule_id"]
        assert _run("mute-rule", "--root", root, "--rule-id", rule_id).returncode == 0

        (root / "rules.jsonl").unlink()
        assert _run("repair-projections", "--root", root).returncode == 0
        rebuilt = (root / "rules.jsonl").read_text(encoding="utf-8")
        assert rule_id in rebuilt, "the repair must actually have rebuilt the rule"
        muted = json.loads((root / "profile.json").read_text(encoding="utf-8"))["muted_rules"]
        tracking, silent = problems_engine.load_rules(str(root / "rules.jsonl"), muted)
        assert tracking == [] and len(silent) == 1, \
            "a rebuilt projection must not resurrect a rule the user silenced"


def _commitment_plan():
    return {"session_id": "2026-07-14__abc123", "engine_state": {"date_end": "2026-07-14",
                                                                 "metrics": {"max_pos_pct": 0.4}},
            "card_plan": {"candidate_rules": []}, "question_queue": []}


def test_a_condition_cannot_be_smuggled_in_beside_an_engine_metric():
    plan = _commitment_plan()
    try:
        review_engine._resolve_commitment(
            plan, {"commitment": {"choice": "custom", "rule": "cap at 20%",
                                  "metric_key": "max_pos_pct", "condition": _CONDITION}})
    except review_engine.ReviewError as exc:
        assert "either a metric_key or a condition" in str(exc), exc
    else:
        raise AssertionError("two anchors means two answers to what this review tracked")


def test_a_condition_cannot_replace_a_mechanically_tracked_rule():
    """`revise_rule` retires a rule the Opportunity Check reconciles every
    period. Letting a slot take its place would silently move it somewhere
    `held_streak` can never count."""
    plan = _commitment_plan()
    plan["question_queue"] = [{"id": "q1", "kind": "rule_breach", "rule_id": "rule-abc-0",
                               "problem_key": "oversize"}]
    answers = {"commitment": {"choice": "custom", "condition": _CONDITION,
                              "revises_rule_id": "rule-abc-0"},
               "answers": [{"question_id": "q1", "choice": "revise_rule", "note": "too tight"}]}
    try:
        review_engine._resolve_commitment(plan, answers)
    except review_engine.ReviewError as exc:
        assert "cannot enter the rule reconciliation" in str(exc), exc
    else:
        raise AssertionError("a slot must not be accepted as a revise_rule replacement")


def test_a_restated_criterion_is_refused_at_the_answer_boundary():
    """The gate belongs where the payload arrives, not one layer deeper: the
    agent gets a message it can act on before anything is committed."""
    plan = _commitment_plan()
    leaked = dict(_CONDITION, query="did quarterly revenue growth fall below 30%?")
    try:
        review_engine._resolve_commitment(plan, {"commitment": {"choice": "custom",
                                                                "condition": leaked}})
    except review_engine.ReviewError as exc:
        assert "condition slot rejected" in str(exc) and "after retrieval" in str(exc), exc
    else:
        raise AssertionError("a query carrying the threshold must be refused")


def test_a_paraphrased_rule_beside_the_criterion_is_refused():
    plan = _commitment_plan()
    try:
        review_engine._resolve_commitment(
            plan, {"commitment": {"choice": "custom", "condition": _CONDITION,
                                  "rule": "watch revenue growth closely"}})
    except review_engine.ReviewError as exc:
        assert "verbatim" in str(exc), exc
    else:
        raise AssertionError("the card's rule text and the stored criterion are one string")


def _mixed_market_card_for_rendering():
    """Synthetic renderer input with sentinels in every field public copy must ignore."""
    return {
        "top_holes": [{"severity": 0.52, "raw": {
            "dim": "加碼攤平", "tickers": ["PRIVATE_HOLDING"],
            "number_line": "PRIVATE_HOLDING above $1234 on 2026-07-14"}}],
        "alpha_beta_breakdown": {
            # Compatibility fields describe only the largest market. Deliberately
            # make them impossible sentinels so mixed rendering cannot masquerade
            # as a combined portfolio result.
            "scope": "TW", "port_tot": 9.99, "spy_tot": -9.99,
            "excess_vs_spy": 19.98, "beta": 99.0,
            "by_market": {
                "TW": {
                    "bench": "^TWII", "port_tot": 0.20, "spy_tot": 0.10,
                    "excess_vs_spy": 0.10, "beta": 1.10,
                    "benchmarks": {"PRIVATE_HOLDING": {"secret": "2026-07-14"}},
                    "excess_split": {"excess": 0.10, "allocation": 0.04,
                                     "selection": 0.06, "coverage": 0.80,
                                     "proxy": {"PRIVATE_HOLDING": "PRIVATE_PROXY"},
                                     "unproxied": ["PRIVATE_HOLDING"]}},
                "US": {
                    "bench": "SPY", "port_tot": 0.05, "spy_tot": 0.08,
                    "excess_vs_spy": -0.03, "beta": 0.80,
                    "excess_split": {"excess": -0.03, "allocation": 0.01,
                                     "selection": -0.04, "coverage": 1.0,
                                     "proxy": {}, "unproxied": []}},
                "PRIVATE_MARKET": {"port_tot": 4.2, "spy_tot": 0,
                                   "excess_vs_spy": 4.2, "beta": 4.2},
            },
        },
    }


def test_mixed_market_private_card_renders_each_market_and_winning_split():
    import card_renderer
    card = _mixed_market_card_for_rendering()
    honesty = {"sector_attribution": "部分標的缺板塊基準，賽道與選股拆帳不完整。"}
    text = "\n".join(card_renderer._performance_lines(card, "zh-TW", honesty))
    # #363: the absolute per-market returns are internal again; the sentence
    # states the excess it feeds, which no other line on the card carries.
    assert "TW 部位對 ^TWII 的超額報酬 +10 個百分點" in text and "β 1.10" in text
    assert "US 部位對 SPY 的超額報酬 -3 個百分點" in text and "β 0.80" in text
    assert "部位報酬 20%" not in text and "同期 ^TWII 10%" not in text
    assert "TW 贏大盤的 +10 個百分點" in text
    assert "市場／賽道配置 +4 個百分點" in text and "標的選擇 +6 個百分點" in text
    assert text.count("賽道與選股拆帳不完整") == 1, \
        "the engine-triggered attribution caveat must be placed exactly once"
    assert "US 贏大盤" not in text, "a losing market must not be described as beating its benchmark"
    assert "999%" not in text and "99.00" not in text and "PRIVATE_MARKET" not in text, \
        "mixed cards must never render the top-level scope row as a combined third result"


def test_display_currency_converts_aggregate_amounts():
    import card_renderer
    base = {
        "overview": {"total_pnl": -300, "realized": 200, "unrealized": -500,
                     "payoff": 1.5, "avg_win": 100, "avg_loss": -50},
        "currency_meta": {"mixed": True, "aggregate_currency": "USD",
                          "currencies": ["TWD", "USD"], "fx": {"TWD": 1 / 32},
                          "pnl_by_currency": {
                              "TWD": {"realized": 1200, "unrealized": -3200},
                              "USD": {"realized": 40, "unrealized": 10},
                          }},
    }
    state = {"currency_meta": dict(base["currency_meta"])}
    zh_card, _ = review_engine._apply_display_currency(base, state, None, "zh-TW")
    assert zh_card["currency_meta"]["display_currency"] == "TWD"
    overview = "\n".join(card_renderer._overview_lines(zh_card, "zh-TW"))
    assert "-TWD 9,600" in overview and "+TWD 6,400" in overview and "-TWD 16,000" in overview

    en_card, _ = review_engine._apply_display_currency(base, state, None, "en")
    assert en_card["currency_meta"]["display_currency"] == "USD"
    assert "-$300" in "\n".join(card_renderer._overview_lines(en_card, "en"))

    single = {"overview": base["overview"],
              "currency_meta": {"mixed": False, "aggregate_currency": "USD"}}
    single_zh, _ = review_engine._apply_display_currency(single, {}, None, "zh-TW")
    assert single_zh["currency_meta"]["display_currency"] == "USD"
    assert "-$300" in "\n".join(card_renderer._overview_lines(single_zh, "zh-TW")), \
        "single-market cards stay in their own currency regardless of locale"


def test_display_currency_uses_dated_cache_then_falls_back_to_original_buckets():
    import card_renderer
    card = {
        "overview": {"total_pnl": 10, "realized": 4, "unrealized": 6},
        "currency_meta": {"mixed": True, "aggregate_currency": "USD",
                          "currencies": ["EUR", "USD"], "fx": {"EUR": 1.1},
                          "pnl_by_currency": {
                              "EUR": {"realized": 2, "unrealized": 3},
                              "USD": {"realized": 4, "unrealized": 6},
                          }},
    }
    state = {"currency_meta": dict(card["currency_meta"])}
    previous = {"date_end": "2026-07-10", "currency_meta": {"fx": {"TWD": 1 / 31}}}
    cached, cached_state = review_engine._apply_display_currency(card, state, previous, "zh-TW")
    assert cached["currency_meta"]["display_fx_source"] == "cached"
    assert "+TWD 310" in "\n".join(card_renderer._overview_lines(cached, "zh-TW"))
    note = card_renderer._currency_note(cached, "zh-TW")
    assert "2026-07-10" in note and "上次對帳匯率" in note

    cached_state["date_end"] = "2026-07-17"
    recached, _ = review_engine._apply_display_currency(card, state, cached_state, "zh-TW")
    assert recached["currency_meta"]["display_fx_as_of"] == "2026-07-10", \
        "reusing the same cached rate must not refresh its provenance date"

    original, _ = review_engine._apply_display_currency(card, state, None, "zh-TW")
    assert original["currency_meta"]["display_fx_source"] == "unavailable"
    text = "\n".join(card_renderer._overview_lines(original, "zh-TW"))
    assert "EUR 帳面損益" in text and "USD 帳面損益" in text
    assert "TWD" not in text, "missing display FX must not invent a locale conversion"
    assert "保留原幣" in card_renderer._currency_note(original, "zh-TW")


def test_display_currency_rejects_approximate_aggregate_and_single_currency_identity_cache():
    import card_renderer
    incomplete = {
        "overview": {"total_pnl": 1100, "realized": 1100, "unrealized": 0},
        "data_integrity": {"fx_gaps": ["EUR"]},
        "currency_meta": {"mixed": True, "aggregate_currency": "USD",
                          "currencies": ["EUR", "USD"], "fx": {"TWD": 1 / 32},
                          "pnl_by_currency": {
                              "EUR": {"realized": 1000, "unrealized": 0},
                              "USD": {"realized": 100, "unrealized": 0},
                          }},
    }
    state = {"currency_meta": dict(incomplete["currency_meta"])}
    legacy_text = "\n".join(card_renderer._overview_lines(incomplete, "en"))
    assert "EUR" in legacy_text and "+$1,100" not in legacy_text, \
        "re-rendering a pre-display-currency bundle must also fail closed on held FX gaps"
    for language in ("en", "zh-TW"):
        resolved, _ = review_engine._apply_display_currency(incomplete, state, None, language)
        assert resolved["currency_meta"]["display_fx_source"] == "unavailable"
        assert resolved["currency_meta"]["display_fx_reason"] == "portfolio_fx_gap"
        text = "\n".join(card_renderer._overview_lines(resolved, language))
        assert "EUR" in text and ("+$100" in text or "USD" in text)
        assert "+TWD 35,200" not in text and "+$1,100" not in text, \
            "a 1:1 approximate engine aggregate must never be relabeled or converted"
        assert "held-currency" in card_renderer._currency_note(resolved, "en")

    pure_twd = {"currency_meta": {"mixed": False, "aggregate_currency": "TWD"}}
    _, pure_state = review_engine._apply_display_currency(pure_twd, pure_twd, None, "zh-TW")
    pure_state["date_end"] = "2026-07-10"
    assert pure_state["currency_meta"]["display_fx_rate"] is None
    offline_mixed = {
        "overview": incomplete["overview"],
        "currency_meta": {"mixed": True, "aggregate_currency": "USD",
                          "currencies": ["EUR", "USD"], "fx": {"EUR": 1.1},
                          "pnl_by_currency": incomplete["currency_meta"]["pnl_by_currency"]},
    }
    resolved, _ = review_engine._apply_display_currency(
        offline_mixed, {"currency_meta": dict(offline_mixed["currency_meta"])}, pure_state, "zh-TW")
    assert resolved["currency_meta"]["display_fx_source"] == "unavailable", \
        "single-currency identity factor is not a USD-per-unit FX cache"


def test_public_card_keeps_behavior_and_relative_performance_without_identifiers():
    import card_renderer
    card = _mixed_market_card_for_rendering()
    bundle = {
        "route": "weekly_review", "engine_card": card,
        "review_plan": {"state_snapshot": {"market_context": {
            "start": "2026-07-01", "benchmarks": {"PRIVATE_HOLDING": {"last": 1234}}}}},
        "narrative": {"mirror": "PRIVATE_NARRATIVE $1234 2026-07-14"},
        "commitment": {"origin": "custom", "rule": "PRIVATE_RULE $1234 2026-07-14"},
    }
    for language in ("en", "zh-TW"):
        bundle["language"] = language
        public = card_renderer.render_public(bundle)
        assert "β 1.10" in public and "β 0.80" in public
        if language == "en":
            assert "TW: +10 pp" in public and "US: -3 pp" in public
            assert "The highlighted behavior concerned how additions to losing positions were bounded" in public
        else:
            assert "TW：相對各自市場大盤 +10 個百分點" in public
            assert "US：相對各自市場大盤 -3 個百分點" in public
            assert "這次浮現的模式，關乎虧損部位的加碼如何受到界線約束" in public
        for secret in ("PRIVATE_HOLDING", "PRIVATE_PROXY", "PRIVATE_MARKET", "PRIVATE_NARRATIVE",
                       "PRIVATE_RULE", "SPY", "^TWII", "$1234", "2026-07-14", "999", "99.00"):
            assert secret not in public, f"public card leaked {secret!r}"


def test_public_behavior_copy_does_not_invent_a_specific_subsignal():
    import card_renderer
    cases = [
        ("holding_period", {"median_days": 0, "incon_rate": 0},
         "whether holding durations matched a consistent decision horizon", "mixing different"),
        ("exit_discipline", {"disposition_gap": 0.25},
         "how exit decisions were timed and evaluated", "original thesis"),
    ]
    for dim, raw, expected, unsupported in cases:
        raw["dim"] = dim
        public = card_renderer.render_public({
            "language": "en",
            "engine_card": {"top_holes": [{"severity": 0.6, "raw": raw}]},
        })
        assert expected in public
        assert unsupported not in public, \
            "dimension-level public copy must not diagnose a sub-signal the engine did not establish"


def test_public_relative_performance_omits_bad_rows_and_preserves_zero():
    import card_renderer
    assert card_renderer._benchmark_pp(-0.0001) == "+0", "rounded ratios must not render as negative zero"
    assert card_renderer._benchmark_pp(-0.005) == "+0"
    assert card_renderer._benchmark_pp(0.005) == "+0"
    assert card_renderer._beta_text(-0.004) == "0.00"
    assert card_renderer._beta_text(-0.0) == "0.00"
    mixed = _mixed_market_card_for_rendering()
    mixed["alpha_beta_breakdown"]["by_market"]["TW"] = {
        "note": "PRIVATE_HOLDING missing on 2026-07-14"}
    mixed["alpha_beta_breakdown"]["by_market"]["US"]["excess_vs_spy"] = float("nan")
    public = card_renderer.render_public({"language": "en", "engine_card": mixed})
    assert "Relative performance" not in public and "nan" not in public.lower()
    assert "PRIVATE_HOLDING" not in public and "2026-07-14" not in public

    single = {"alpha_beta_breakdown": {
        "scope": None, "by_market": None, "bench": "SPY",
        "port_tot": 0.0, "spy_tot": 0.0, "excess_vs_spy": -0.005, "beta": -0.004}}
    public = card_renderer.render_public({"language": "en", "engine_card": single})
    assert "Portfolio: +0 pp versus its market benchmark; β 0.00." in public, \
        "rounded zero is a valid engine result and must never expose a negative sign"


def test_public_card_renders_the_two_section_headings_only_it_owns():
    """``sections.performance`` and ``sections.etf`` are read by nothing but
    ``render_public``, and no test covered either heading before #368.

    That combination is how a live key comes to look dead: the persona sweep
    renders public cards, but it runs offline (so ``_public_performance_lines``
    never gets the ``excess_vs_spy`` + ``beta`` pair it needs) against personas
    that hold no ETFs — both headings are dark to it for reasons that have
    nothing to do with whether the product renders them. The #368 audit
    initially counted both as unread; this test is the standing evidence that
    they are not."""
    import card_renderer
    card = _mixed_market_card_for_rendering()
    card["portfolio_structure"] = {
        "allocation_etfs": [{"ticker": "SPY", "weight": 0.30}],
        "concentrated_etfs": [],
    }
    expected = {
        "en": ("## Relative performance", "## ETF and portfolio structure"),
        "zh-TW": ("## 相對績效", "## ETF 與組合結構"),
    }
    for language, (performance, etf) in expected.items():
        public = card_renderer.render_public({"language": language, "engine_card": card})
        assert performance in public, f"{language}: public card dropped the performance heading"
        assert etf in public, f"{language}: public card dropped the ETF heading"
        # Both are data-gated, and the gates are independent: drop the ETF
        # structure and only that heading goes, which is what makes the two
        # headings separate keys rather than one.
        without_etf = card_renderer.render_public(
            {"language": language, "engine_card": {**card, "portfolio_structure": {}}})
        assert performance in without_etf and etf not in without_etf, \
            f"{language}: the two headings must gate independently"


def test_recent_exit_capture_is_ranked_bounded_canonical_and_private_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, csv_path, card_path, state_path = _prepare_with_trades(tmp, root)
        # This fixture routes first_review, whose density band is three to five (#291).
        assert plan["route"] == "first_review"
        assert plan["card_plan"]["question_policy"] == {"route": "first_review", "min": 3, "max": 5}
        assert len(plan["question_queue"]) == 3, "these three grounded candidates fit inside the five-slot band"
        assert [(q["kind"], q.get("ticker")) for q in plan["question_queue"]] == [
            ("revisit", "BIG"), ("revisit", "MID"), ("add_thesis", "PLTR")], \
            "perishable captures (amount-ranked, max two) lead; the rest rank by impact"
        big, mid, _add = plan["question_queue"]
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


def test_prepare_unknown_language_falls_back_to_en_and_stays_idempotent():
    """#389: an unsupported --language must not fail argparse and must not fall
    back to zh-TW. The tag resolves to en at the CLI boundary, so the plan
    carries the canonical locale and the session fingerprint matches an
    explicit en run (resolve happens before fingerprinting — a retry that
    spells the tag differently resumes the same session instead of forking)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan = _prepare(tmp, root, language="ja")
        assert plan["language"] == "en"
        # en question copy actually selected, not just the label rewritten
        stems = [row["question"] for row in plan["question_queue"]]
        assert all(not re.search(r"[一-鿿]", stem) for stem in stems), stems

        resumed = _prepare(tmp, root, language="en")
        assert resumed["session_id"] == plan["session_id"], \
            "ja and en must fingerprint identically after boundary resolution"

        # Case variants of a supported locale normalize instead of falling back
        with tempfile.TemporaryDirectory() as tmp2:
            root2 = pathlib.Path(tmp2) / "coach"
            zh = _prepare(tmp2, root2, language="zh-tw")
            assert zh["language"] == "zh-TW"


def test_prepare_zh_cn_renders_simplified_copy_with_documented_mixed_script():
    """#387 option (b) light-up, owner-ruled 2026-07-24: copy/zh-CN.json makes
    zh-CN supported through the #389 directory scan alone — zero engine edits.

    Positive gates: the plan carries the canonical tag, question copy is CJK
    (not the en fallback), Simplified-only characters appear (the zh-CN file is
    actually selected), and the render differs from zh-TW.

    TRANSITIONAL PIN — mixed script is a known, owner-waived state: engine
    ternaries still hardcode Traditional stem/option text (leak inventory on
    #387), so zh-CN surfaces mix scripts today. The pin below asserts that
    leak still exists. When the #387 copy migration lands and this pin turns
    red, do not delete it — flip it into the purity gate
    (``assert not set(joined) & traditional_only``)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        cn = _prepare(tmp, root, language="zh-CN")
        assert cn["language"] == "zh-CN"
        cn_stems = [row["question"] for row in cn["question_queue"]]
        assert cn_stems and all(re.search(r"[一-鿿]", s) for s in cn_stems), \
            "zh-CN stems must be CJK, not the en fallback"
        joined = "".join(cn_stems)
        simplified_only = set("风险亏损复币账买卖张档时实际转动价还这为么后")
        traditional_only = set("風險虧損復幣賬買賣張檔時實際轉動價還這為麼後")
        assert set(joined) & simplified_only, \
            f"no Simplified-only characters — zh-CN copy not selected: {joined[:120]}"
        assert set(joined) & traditional_only, \
            "Traditional leak is gone — the #387 migration must have landed; " \
            "flip this pin into the purity gate (see docstring)"
    with tempfile.TemporaryDirectory() as tmp2:
        root2 = pathlib.Path(tmp2) / "coach"
        tw = _prepare(tmp2, root2, language="zh-TW")
        tw_stems = [row["question"] for row in tw["question_queue"]]
        assert tw_stems != cn_stems, "zh-CN must not silently render the zh-TW copy"


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


def _memory_add_queue(active_row, language, diagnosis=None, cost=5000, custom_question=None):
    """Build one reopenable NVDA add question through _question_queue (#226)."""
    positions = {"NVDA": {"cycle_id": "NVDA#2026-06-01#1", "cost": cost,
                          "decision_cursor": "NVDA#2026-06-01#1#add#2"}}
    state = {"holdings": {"positions": positions}}
    item = {"ticker": "NVDA"}
    if custom_question:
        item["question"] = custom_question
    card = {"thesis_questions": [item], "ticker_diagnosis": diagnosis or []}
    active = {}
    if active_row is not None:
        active["NVDA#2026-06-01#1"] = active_row
    queue, _report = review_engine._question_queue(card, state, active, None, language)
    assert [row["kind"] for row in queue] == ["add_thesis"]
    return queue[0]


def test_add_question_stem_weaves_prior_thesis_with_voice_rules():
    """#226 option A: the add stem quotes the user's own recorded thesis with the
    same inferred/confirmed voice split `_due_question` uses, localized both ways,
    and exposes asked_because instead of discarding the importance basis."""
    confirmed = {"why": "AI capex 還在加速", "maturity": "testable",
                 "session_date": "2026-07-02"}
    row = _memory_add_queue(confirmed, "zh-TW")
    assert row["question"] == ("NVDA 你在 2026-07-02 說過『AI capex 還在加速』。"
                               "這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？"
                               "（問這題是因為它是你本週成本最大的部位）")
    assert row["asked_because"] == "它是你本週成本最大的部位"

    english = {"why": "AI capex is still accelerating", "maturity": "testable",
               "session_date": "2026-07-02"}
    row = _memory_add_queue(english, "en", diagnosis=[{"ticker": "NVDA", "impact": -1200}])
    assert row["question"] == ('For NVDA: on 2026-07-02 you said "AI capex is still accelerating". '
                               "Was the add based on new evidence, a pre-planned tranche, "
                               "a valuation change, or only the lower price? "
                               "(Asked because it is the position with the largest P&L impact this week.)")
    assert row["asked_because"] == "it is the position with the largest P&L impact this week"

    # Inferred-and-never-confirmed stays a guess; the date may fall back to the
    # session-id prefix exactly like the thesis fold's event-date resolution.
    guessed = {"why": "AI capex 還在加速", "maturity": "inferred",
               "session_id": "2026-07-02__w1"}
    row = _memory_add_queue(guessed, "zh-TW")
    assert row["question"].startswith("NVDA 我在 2026-07-02 猜你的論點是『AI capex 還在加速』。")
    row = _memory_add_queue(dict(guessed, why="AI capex is still accelerating"), "en")
    assert row["question"].startswith('For NVDA: on 2026-07-02 I guessed your thesis was '
                                      '"AI capex is still accelerating".')

    # An undated record still replays the quote without inventing a date.
    row = _memory_add_queue({"why": "AI capex 還在加速", "maturity": "testable"}, "zh-TW")
    assert row["question"].startswith("NVDA 你先前說過『AI capex 還在加速』。")


def test_add_question_stem_falls_back_byte_identical_without_memory():
    """No prior thesis -> today's exact sentence; no mapped basis -> no suffix."""
    # Unparseable cost makes the importance basis unknown: the whole stem must
    # be byte-identical to the pre-#226 template.
    row = _memory_add_queue(None, "zh-TW", cost="n/a")
    assert row["question"] == "NVDA 這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？"
    assert "asked_because" not in row
    row = _memory_add_queue(None, "en", cost="n/a")
    assert row["question"] == ("For NVDA, was the add based on new evidence, a pre-planned tranche, "
                               "a valuation change, or only the lower price?")

    # A known basis appends only the parenthetical; the base sentence is unchanged.
    row = _memory_add_queue(None, "zh-TW")
    assert row["question"] == ("NVDA 這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？"
                               "（問這題是因為它是你本週成本最大的部位）")

    # An engine-authored zh question (thesis_q) still passes through verbatim.
    custom = "虧損中加碼 4 次、現在還虧 15%——你還相信當初買它的理由嗎?"
    row = _memory_add_queue(None, "zh-TW", cost="n/a", custom_question=custom)
    assert row["question"] == custom
    woven = _memory_add_queue({"why": "AI capex 還在加速", "maturity": "testable",
                               "session_date": "2026-07-02"}, "zh-TW", cost="n/a",
                              custom_question=custom)
    assert woven["question"] == f"NVDA 你在 2026-07-02 說過『AI capex 還在加速』。{custom}"

    # Corrupt records fail soft to the plain stem, never to an exception.
    for broken in ({"why": "   ", "maturity": "testable"}, {"maturity": "inferred"},
                   {"why": None}):
        row = _memory_add_queue(broken, "zh-TW", cost="n/a")
        assert row["question"] == "NVDA 這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？"
    assert review_engine._thesis_recall("not-a-dict", "zh-TW", "add") is None


def test_thesis_quote_clips_word_safe_with_ellipsis():
    long_why = ("AI capex is still accelerating across every hyperscaler and the "
                "backlog keeps growing while supply stays tight")
    quote = review_engine._clip_quote(long_why)
    assert quote.endswith("…") and len(quote) <= review_engine.QUOTE_CLIP + 1
    assert long_why.startswith(quote[:-1])
    assert long_why[len(quote) - 1] == " ", "clip must land on a word boundary"
    row = _memory_add_queue({"why": long_why, "maturity": "testable",
                             "session_date": "2026-07-02"}, "en")
    assert f'you said "{quote}"' in row["question"]
    assert "supply stays tight" not in row["question"]
    # CJK has no word boundaries: keep the raw budget, still mark the cut.
    cjk = "半" * 90
    assert review_engine._clip_quote(cjk) == "半" * review_engine.QUOTE_CLIP + "…"
    assert review_engine._clip_quote("短句") == "短句"


def test_exit_question_weaves_entry_thesis_memory():
    """#226: the exit-reason capture stem replays the entry thesis for that cycle
    with the same voice rules; without one it stays byte-identical to today."""
    item = {"revisit_id": "BIG#2026-07-01#1#2026-07-10#10.0", "ticker": "BIG",
            "cycle_id": "BIG#2026-07-01#1", "exit_date": "2026-07-10",
            "exit_price": 200.0, "shares_sold": 10.0, "shares_before": 10.0,
            "kind": "full", "currency": "USD"}
    confirmed = {"why": "Data-center demand is not priced in", "maturity": "testable",
                 "session_date": "2026-07-01"}
    question = review_engine._exit_question(item, "en", None, confirmed)
    assert question["question"] == (
        "BIG was fully exited on 2026-07-10 for about USD 2,000. "
        'At entry on 2026-07-01 you said "Data-center demand is not priced in". '
        "What mainly drove that decision?")
    assert question["asked_because"] == "it is one of your largest recent exits by amount"
    zh = review_engine._exit_question(item, "zh-TW", None, confirmed)
    assert zh["question"] == (
        "BIG 在 2026-07-10 全部出清，出場金額約 USD 2,000。"
        "你進場時（2026-07-01）說的是『Data-center demand is not priced in』。"
        "當時主要是什麼理由？")
    assert zh["asked_because"] == "它是你近期金額最大的出場之一"

    guessed = dict(confirmed, maturity="inferred")
    assert "進場時（2026-07-01）我猜你的論點是『" in \
        review_engine._exit_question(item, "zh-TW", None, guessed)["question"]
    assert "At entry on 2026-07-01 I guessed your thesis was" in \
        review_engine._exit_question(item, "en", None, guessed)["question"]

    plain = review_engine._exit_question(item, "zh-TW")
    assert plain["question"] == "BIG 在 2026-07-10 全部出清，出場金額約 USD 2,000。當時主要是什麼理由？"
    assert review_engine._exit_question(item, "en", None, {"why": "   "})["question"] == \
        "BIG was fully exited on 2026-07-10 for about USD 2,000. What mainly drove that decision?"


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
        assert "evaluation" not in evidence_event, \
            "dead thesis evaluation scaffolding must not resurface via " \
            "build_decision_events's raw persisted event (refs #416)"

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
        # Twin guard for the *other* removed write site: build_decision_events's
        # raw event and _evidence_record's folded evidence are two independent
        # paths (reconstruct_states re-derives evidence_history/last_evidence
        # from scratch), so a mutation that reintroduces only one of them would
        # slip past a test that checks only the other.
        assert "evaluation" not in active["last_evidence"], \
            "dead thesis evaluation scaffolding must not resurface via " \
            "_evidence_record's folded last_evidence (refs #416)"
        assert "evaluation" not in active["evidence_history"][0], \
            "dead thesis evaluation scaffolding must not resurface via " \
            "_evidence_record's folded evidence_history (refs #416)"

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
        # Was: `"Trade Review" not in text or "The account for this review" in
        # text` — vacuous since the en title became "Review Card", and its
        # right-hand side named `sections.numbers`, pruned in #368. The English
        # card's no-CJK property it gestured at is now gated far harder, on
        # every en persona and all three surfaces, by persona_sweep's
        # locale_purity (#356).
        assert "Before averaging down" in text


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


def test_reconciliation_lines_appends_prior_commitment_breach_sentence():
    """#292: a `prior_commitment_breach` honesty_ledger entry adds one more
    sentence right after the then/now reconciliation line. This sentence is
    copy-fallback only — it never reads narrative.honesty — so it is
    guaranteed to reach the reader regardless of the agent's separately
    required (and separately gated) honesty wording."""
    import card_renderer
    base_bundle = {"review_plan": {"state_snapshot": {"prior_commitment": {
                       "rule": "下單前先檢查單一風險部位上限", "metric_key": "max_pos_pct", "metric_value": 0.47}}},
                   "engine_state": {"metrics": {"max_pos_pct": 0.48}}}
    unbreached_zh = card_renderer._reconciliation_lines(base_bundle, "zh-TW")
    unbreached_en = card_renderer._reconciliation_lines(base_bundle, "en")
    assert len(unbreached_zh) == 1 and len(unbreached_en) == 1

    breached_bundle = {**base_bundle, "engine_card": {"honesty_ledger": [
        {"key": "prior_commitment_breach", "status": "draft",
         "data": {"problem_key": "oversize", "week": "2026-07-21"}}]}}
    zh = card_renderer._reconciliation_lines(breached_bundle, "zh-TW")
    en = card_renderer._reconciliation_lines(breached_bundle, "en")
    assert len(zh) == 1 and len(en) == 1, \
        "the breach sentence rides the same opening line, not a second list entry"
    assert zh[0].startswith(unbreached_zh[0]) and zh[0] != unbreached_zh[0], \
        "the then/now numbers must render exactly as before, with the breach sentence appended"
    assert en[0].startswith(unbreached_en[0]) and en[0] != unbreached_en[0]
    assert zh[0][len(unbreached_zh[0]):].strip() == \
        card_renderer.load_copy("zh-TW")["honesty"]["prior_commitment_breach"]
    assert en[0][len(unbreached_en[0]):].strip() == \
        card_renderer.load_copy("en")["honesty"]["prior_commitment_breach"]

    # An unrelated honesty-ledger key must not trigger the sentence.
    unrelated_bundle = {**base_bundle, "engine_card": {"honesty_ledger": [
        {"key": "etf_metadata", "status": "partial", "data": {}}]}}
    assert card_renderer._reconciliation_lines(unrelated_bundle, "en") == unbreached_en

    # No prior commitment at all still short-circuits before the ledger is read.
    assert card_renderer._reconciliation_lines(
        {"review_plan": {}, "engine_card": {"honesty_ledger": [
            {"key": "prior_commitment_breach", "status": "draft", "data": {}}]}}, "en") == [], \
        "first review has no prior commitment; a stray ledger entry must not fabricate one"


def test_draft_breach_of_a_prior_commitment_forces_a_required_honesty_key():
    """#292: rules.jsonl carries the rule the user is tracking; last_state.json's
    commitment names that same rule (the join key session.PKEY + text); the
    _artifacts() fixture state already carries one 2026-07-14 avgdown_breach
    problem_event. problems.jsonl needs one real prior mark (2026-07-07, before
    the fixture's own date_end) so the book is non-empty and prev_week resolves
    to a date strictly before the draft window — otherwise _problem_snapshot
    short-circuits to None before check_rules ever runs."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root_dir:
        root = pathlib.Path(root_dir)
        rule_text = "虧損不加碼"
        (root / "rules.jsonl").write_text(json.dumps({
            "rule_id": "rule-prior", "text": rule_text, "problem_key": "avgdown_breach",
            "status": "tracking", "created": "2026-06-01",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        (root / "problems.jsonl").write_text(json.dumps({
            "type": "review_mark", "week": "2026-07-07", "opportunities": {"avgdown_breach": True},
        }) + "\n", encoding="utf-8")
        (root / "last_state.json").write_text(json.dumps({
            "date_end": "2026-07-07",
            "commitment": {"rule": rule_text, "metric_key": "avgdown_count",
                          "metric_value": 2, "goal": "down", "origin": "candidate",
                          "source": "user_chosen"},
        }, ensure_ascii=False), encoding="utf-8")

        card, state = _artifacts(tmp)
        run = _run("prepare", "--root", root, "--route", "weekly_review", "--language", "zh-TW",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)

        rules_check = plan["state_snapshot"]["problem_stats"]["rules_check"]
        assert len(rules_check) == 1
        assert rules_check[0]["last_breach"] is None, \
            "no finalized mark has closed over this period yet — only the draft window sees it"
        assert rules_check[0]["draft_breach"] == {
            "week": "2026-07-14", "event_count": 1,
            "events": [{"key": "avgdown_breach", "kind": "event", "week": "2026-07-14",
                       "ticker": "PLTR", "amount": 1, "note": "test"}],
        }
        assert rules_check[0]["verdict"] == "held" and rules_check[0]["held_streak"] == 1, \
            "the finalized verdict/streak stay driven by real marks only, untouched by the draft window"

        assert "prior_commitment_breach" in plan["card_plan"]["required_honesty_keys"]
        ledger_entry = next(e for e in plan["engine_card"]["honesty_ledger"]
                            if e["key"] == "prior_commitment_breach")
        assert ledger_entry == {"key": "prior_commitment_breach", "status": "draft",
                                "data": {"problem_key": "avgdown_breach", "week": "2026-07-14"}}


def test_review_count_unifies_canonical_and_legacy_history():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        sessions = root / "sessions"
        sessions.mkdir()
        canonical_id = "2026-07-14__canonical"
        committed = sessions / canonical_id
        committed.mkdir()
        (committed / "bundle.json").write_text(json.dumps({
            "session_id": canonical_id,
            "route": "weekly_review",
            "review_plan": {"persist": True},
        }), encoding="utf-8")
        demo = sessions / "2026-07-14__demo"
        demo.mkdir()
        (demo / "bundle.json").write_text(json.dumps({
            "session_id": "2026-07-14__demo",
            "route": "test_drive",
            "review_plan": {"persist": False},
        }), encoding="utf-8")
        corrupt = sessions / "2026-07-14__corrupt"
        corrupt.mkdir()
        (corrupt / "bundle.json").write_text("not json", encoding="utf-8")
        wrong_shape = sessions / "2026-07-14__wrong-shape"
        wrong_shape.mkdir()
        (wrong_shape / "bundle.json").write_text("[]", encoding="utf-8")
        wrong_plan = sessions / "2026-07-14__wrong-plan"
        wrong_plan.mkdir()
        (wrong_plan / "bundle.json").write_text(json.dumps({
            "session_id": "2026-07-14__wrong-plan", "review_plan": ["invalid"],
        }), encoding="utf-8")
        (root / "log.jsonl").write_text("\n".join([
            json.dumps({"session_id": canonical_id}),
            json.dumps({"session_id": "2026-07-01__legacy-with-id"}),
            json.dumps({"date_end": "2026-06-01"}),
            "not json",
        ]) + "\n", encoding="utf-8")
        assert review_engine._completed_review_count(root) == 3, \
            "canonical/log projections dedupe by session id; old id-less rows still count"
        assert review_engine._completed_review_count(root, exclude_session_id=canonical_id) == 2, \
            "a committed-session retry is not counted as its own prior review"


def test_route_auto_ignores_finalized_test_drive_history():
    """#215: a finalized demo in an explicit --root must not fake weekly history."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        sessions = root / "sessions"
        demo = sessions / "2026-07-17__demo"
        demo.mkdir(parents=True)
        (demo / "bundle.json").write_text(
            json.dumps(_minimal_bundle("2026-07-17__demo")), encoding="utf-8")
        corrupt = sessions / "2026-07-17__corrupt"
        corrupt.mkdir()
        (corrupt / "bundle.json").write_text("not json", encoding="utf-8")
        assert review_engine._has_history(str(root)) is False, \
            "finalized test-drive bundles and corrupt directories are not coach history"
        plan = _prepare(tmp, root)  # --route defaults to auto
        assert plan["route"] == "first_review", \
            "route=auto must stay first_review when only demo sessions exist"

        persistent = sessions / "2026-07-10__real"
        persistent.mkdir()
        real_bundle = _minimal_bundle("2026-07-10__real")
        real_bundle["route"] = "weekly_review"
        real_bundle["review_plan"] = {"persist": True}
        (persistent / "bundle.json").write_text(json.dumps(real_bundle), encoding="utf-8")
        assert review_engine._has_history(str(root)) is True
        card, state = _artifacts(tmp)
        rerun = _run("prepare", "--root", root, "--card-json", card, "--state-json", state,
                     "--session-nonce", "after-real-history")
        assert rerun.returncode == 0, rerun.stdout + rerun.stderr
        assert json.loads(rerun.stdout)["review_plan"]["route"] == "weekly_review"


def test_initial_snapshot_boundary_layers_share_one_verdict():
    """The prepare fail-fast and finalize's authoritative check cannot drift."""
    bundle = _runtime_snapshot_bundle("2026-07-17__snapshot")
    anchor = bundle["engine_state"]["snapshot_anchor"]

    def verdicts(root):
        try:
            review_engine._validate_initial_snapshot_root(str(root), anchor)
            prepare_ok = True
        except review_engine.ReviewError:
            prepare_ok = False
        try:
            session_engine._assert_initial_snapshot_boundary(str(root), bundle)
            commit_ok = True
        except session_engine.SessionError:
            commit_ok = False
        return prepare_ok, commit_ok

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir()
        assert verdicts(root) == (True, True), "an empty root admits the initial declaration"

        sessions = root / "sessions"
        demo = sessions / "2026-07-16__demo"
        demo.mkdir(parents=True)
        (demo / "bundle.json").write_text(
            json.dumps(_minimal_bundle("2026-07-16__demo")), encoding="utf-8")
        assert verdicts(root) == (True, True), "a finalized demo is history for neither layer"

        # Unknown ledger event types count as existing history for BOTH layers
        # (fail-closed) via scan_initial_snapshot_conflicts' own raw scan,
        # independent of ledger.load_ledger: this root's only row is
        # unreadable, so snapshot_reconciliation would find no complete
        # anchor and refuse for that reason even before #462. See
        # test_snapshot_reconciliation_fails_closed_on_a_corrupt_ledger_row
        # below for the case that isolates #462's own gate: a corrupt row
        # sitting *beside* an already-anchored, otherwise-valid history.
        (root / "ledger.jsonl").write_text(
            json.dumps({"type": "mystery_event", "as_of": "2026-07-01"}) + "\n",
            encoding="utf-8")
        assert verdicts(root) == (False, False)

        (root / "ledger.jsonl").unlink()
        committed = sessions / bundle["session_id"]
        committed.mkdir()
        (committed / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        assert verdicts(root) == (True, True), \
            "an identical committed declaration replays in both layers"

        other = _runtime_snapshot_bundle("2026-07-15__other", ticker="QQQ")
        (sessions / other["session_id"]).mkdir()
        (sessions / other["session_id"] / "bundle.json").write_text(
            json.dumps(other), encoding="utf-8")
        assert verdicts(root) == (False, False), \
            "a different prior snapshot conflicts in both layers"


def test_snapshot_reconciliation_fails_closed_on_a_corrupt_ledger_row():
    """#462, isolating the gap the previous test's comment now points at:
    scan_initial_snapshot_conflicts already fails closed when the *entire*
    ledger is unreadable (previous test), but it only asks "does anything
    here fail to match the declared anchor" -- it stops at the first
    mismatching row, so it does not itself notice a *separate* corrupt row
    sitting beside an already-anchored, otherwise-valid history. That
    corrupt row only gets read once scan_initial_snapshot_conflicts has
    already found a real conflict (the differing anchor below) and both
    layers move on to ledger.load_ledger to compute the actual
    reconciliation -- which is exactly where a pre-#462 silent drop would
    have let snapshot_reconciliation compute a diff over a shortened trade
    history instead of refusing."""
    root_dir = tempfile.mkdtemp()
    root = pathlib.Path(root_dir)
    (root / "ledger.jsonl").write_text(
        json.dumps({"type": "snapshot", "as_of": "2026-07-01", "source": "user_declared",
                    "is_complete": True,
                    "positions": [{"ticker": "NVDA", "shares": 100, "avg_cost": 100.0,
                                   "market": "US", "currency": "USD"}]}) + "\n"
        + json.dumps({"type": "mystery_event", "date": "2026-07-05"}) + "\n",
        encoding="utf-8")
    new_anchor = {
        "type": "snapshot", "as_of": "2026-07-17", "source": "user_declared",
        "is_complete": True,
        "positions": [{"ticker": "NVDA", "shares": 120, "avg_cost": 100.0,
                       "market": "US", "currency": "USD"}],
    }

    try:
        review_engine._validate_initial_snapshot_root(str(root), new_anchor)
        raise AssertionError("prepare-time check must fail closed on an unreadable ledger row")
    except review_engine.ReviewError as exc:
        assert "unreadable row(s)" in str(exc), str(exc)

    bundle = _runtime_snapshot_bundle("2026-07-17__corrupt-row")
    bundle["engine_state"]["snapshot_anchor"] = new_anchor
    # Get past the "did prepare already freeze a reconciliation" gate below
    # is_complete -- any dict clears it, since load_ledger raises before this
    # placeholder is ever compared against a freshly recomputed one.
    bundle["engine_state"]["snapshot_reconciliation"] = {
        "schema_version": 1, "status": "adjusted", "as_of": "2026-07-17",
        "against": {"as_of": "2026-07-01", "snapshot_id": None},
        "diff": {"positions": [], "cash": []},
    }
    try:
        session_engine._assert_initial_snapshot_boundary(str(root), bundle)
        raise AssertionError("finalize-time check must fail closed on an unreadable ledger row")
    except session_engine.SessionError as exc:
        assert "unreadable row(s)" in str(exc), str(exc)


def test_returning_private_card_shows_completed_history_snapshot_only_locally():
    import card_renderer
    progress = {"completed_reviews_before_start": 3, "returning": True}
    bundle = {
        "review_plan": {"state_snapshot": {
            "prior_commitment": {"rule": "Keep the position bounded",
                                 "metric_key": "max_pos_pct", "metric_value": 0.51},
            "review_progress": progress,
        }},
        "engine_state": {"metrics": {"max_pos_pct": 0.48}},
    }
    opening = card_renderer._review_opening_lines(bundle, "en")
    assert len(opening) == 1 and "Last time you committed" in opening[0]
    assert "already had 3 completed reviews" in opening[0]
    public = card_renderer.render_public({**bundle, "language": "en", "engine_card": {}})
    assert "completed reviews" not in public.lower(), "review progress remains local/private"
    without_rule = {"review_plan": {"state_snapshot": {"review_progress": progress}}}
    assert card_renderer._review_opening_lines(without_rule, "zh-TW") == [
        "開始這次復盤時，你已有 3 次完成紀錄。"
    ], \
        "a returning user still sees progress after previously skipping a commitment"
    first = {"review_plan": {"state_snapshot": {"review_progress": {
        "completed_reviews_before_start": 0, "returning": False,
    }}}}
    assert card_renderer._review_opening_lines(first, "en") == [], \
        "first reviews must not get a returner milestone"


def test_feedback_form_collects_review_stage_without_trade_details():
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "card-feedback.yml").read_text(encoding="utf-8")
    block = text.split("    id: review_count", 1)[1].split("  - type: textarea", 1)[0]
    assert "label: 這是你第幾次復盤?" in block
    assert all(option in block for option in ("第 1 次", "第 2–3 次", "第 4 次以上"))
    assert "required: true" in block and "交易內容" in block, \
        "retention signal stays coarse, required within voluntary feedback, and privacy-safe"


def test_account_performance_pillar_gate_and_full_render():
    import card_renderer
    gated = {"acct_perf": {"hold_twr": 0.12, "acct_twr": None, "irr_annual": None,
                           "cash_drag": None, "note": "gate", "window": {"days": 30}}}
    lines = card_renderer._performance_lines(gated, "en", {})
    # #363: "time-weighted return" -> "cumulative return" (wording only; the
    # engine field and its number are unchanged).
    assert any("Holdings-only cumulative return was 12%" in x for x in lines)
    # #363: the account-gate sentence is now action-only copy (owner ruling) —
    # no "stays locked"/"unaffected" framing. This is the `default` fallback
    # (no `gate.status` on this fixture), still resolved from copy, not from
    # the engine's internal note text.
    assert any("Complete the cash anchor" in x for x in lines), \
        "#181: gate must render the unlock invitation, not the engine note text"
    assert not any("錨點" in x for x in lines), "engine's internal zh note must not leak into en cards"
    full = {"acct_perf": {"hold_twr": 0.12, "acct_twr": 0.10, "irr_annual": 0.15,
                          "cash_drag": -0.02, "note": None, "window": {"days": 30}}}
    zh = card_renderer._performance_lines(full, "zh-TW", {})
    assert any("帳戶級累積報酬為 10%" in x and "年化報酬 15%" in x for x in zh)
    assert not any("IRR" in x for x in zh), \
        "#279/#272 output contract: the IRR jargon token is banned from the zh card"
    en_full = card_renderer._performance_lines(full, "en", {})
    assert any("annualized return was 15%" in x for x in en_full)
    assert not any("IRR" in x for x in en_full), \
        "#279/#272 output contract: the IRR jargon token is banned from the en card"
    assert any("不是對錯判定" in x for x in zh), "#179: cash drag stays neutral, never a verdict"
    # #363: cash_drag = acct_twr − hold_twr is a difference of two returns, so
    # it renders in percentage points, never percent (output contract §5).
    assert any("-2pp" in x for x in zh), f"cash_drag must render in pp, not %: {zh}"
    assert any("-2pp" in x for x in en_full), f"cash_drag must render in pp, not %: {en_full}"
    assert not any("-2%" in x for x in zh), f"cash_drag must not render in %: {zh}"
    # #368 Phase 2 continuation (#363): account_hold/account moved into the
    # account_perf copy group (holdings_only, account_base, annualized_suffix,
    # cash_drag_suffix, terminator). Pin the complete, exact sentence each
    # renders to — hardcoded here, not read from copy/*.json (see the
    # test_card_html.py reconciliation pin test for why this pattern exists)
    # — so a corruption of any one of those five keys is caught even though
    # persona_sweep can never reach this code path (no mock persona has live
    # prices, so acct_perf never populates in the offline sweep).
    assert ("僅計持倉的累積報酬為 12%。" in zh
            and "帳戶級累積報酬為 10%，年化報酬 15%；與僅計持倉的差距 -2pp 來自持有現金——"
                "這是觀察，不是對錯判定。" in zh), zh
    assert ("Holdings-only cumulative return was 12%." in en_full
            and "Account-level cumulative return was 10%; annualized return was 15%; "
                "the gap versus the holdings pillar, -2pp, is explained by holding cash "
                "— an observation, not a verdict." in en_full), en_full
    assert card_renderer._performance_lines({"acct_perf": {"note": "offline"}}, "en", {}) == [], \
        "no holdings pillar computed -> no account section"
    # #314: the internal 持倉柱 (holdings-pillar) metaphor must not leak onto the
    # zh card; user-facing wording (僅計持倉) replaces it everywhere it appeared —
    # the hold_twr line, the cash-drag comparison, and the gated unlock-invitation.
    assert any("僅計持倉的累積報酬為 12%" in x for x in zh)
    assert any("與僅計持倉的差距" in x for x in zh)
    assert not any("持倉柱" in x for x in zh), \
        "#314: internal pillar jargon must not appear on the rendered zh card"
    gated_zh = card_renderer._performance_lines(gated, "zh-TW", {})
    assert any("補齊現金錨點" in x for x in gated_zh), \
        "#363: account-gate default is now action-only copy"


def test_alpha_interval_line_uses_arabic_digits_for_the_interval_level():
    """#272/#279: one digit style per sentence — the zh alpha-interval line
    prints the 95% level with Arabic digits, not a spelled-out zh numeral."""
    import card_renderer
    ab = {"alpha_stat": {"alpha_ann": 0.33, "ci95": [0.10, 0.56]}}
    line = card_renderer._alpha_interval_line(ab, "zh-TW")
    assert line and "95% 區間" in line, f"expected Arabic 95% interval wording, got: {line}"
    assert "九十五" not in line, "spelled-out zh numeral must not mix with Arabic percentages"
    en_line = card_renderer._alpha_interval_line(ab, "en")
    assert en_line and "95% interval" in en_line


def test_alpha_interval_line_adds_plain_language_caveat_when_interval_crosses_zero():
    """#313: "95% interval from -10% to +74%" is statistically opaque to a
    retail reader on its own. When the lower bound is negative, the renderer
    appends one plain-language sentence (both locales) saying the interval
    does not yet confirm a durable edge; a comfortably positive interval gets
    no such caveat, so the card does not print a warning nobody needs."""
    import card_renderer
    crossing = {"alpha_stat": {"alpha_ann": 0.32, "ci95": [-0.10, 0.74]}}
    zh = card_renderer._alpha_interval_line(crossing, "zh-TW")
    assert "區間包含負值" in zh and "還不能視為穩定能力" in zh, zh
    en = card_renderer._alpha_interval_line(crossing, "en")
    assert "includes a negative value" in en and "not yet statistically confirmed" in en, en

    positive_only = {"alpha_stat": {"alpha_ann": 0.32, "ci95": [0.10, 0.74]}}
    zh_clean = card_renderer._alpha_interval_line(positive_only, "zh-TW")
    assert "區間包含負值" not in zh_clean, \
        "no caveat needed when the interval excludes zero on the downside"
    en_clean = card_renderer._alpha_interval_line(positive_only, "en")
    assert "includes a negative value" not in en_clean


def test_zh_copy_glossary_drops_untranslated_jargon():
    """#314: zh-TW cards must not mix untranslated English (thesis/driver) into
    otherwise-Chinese sentences. Covers the period line, instrument tag, rule,
    and problem-ledger surfaces the issue named, plus the internal 驅動因子/
    交易論述 replacement terms staying consistent everywhere they appear."""
    import card_renderer
    copy_zh = card_renderer.load_copy("zh-TW")
    # `sections.motive` used to carry the 交易論述 term here; it was pruned
    # (#368, 2026-07-23) as one of ten section headings no renderer reads. The
    # glossary rule it stood for is asserted below on surfaces that do render:
    # problem_keys.horizon_break and localized_instrument_tag.
    # The period line's SPY half was cut (#366, owner ruling 2026-07-23), so
    # the glossary has no `period.spy` key left to check — the surviving VIX
    # half is asserted against the renderer below.
    assert "spy" not in copy_zh["period"], copy_zh["period"]
    assert "thesis" not in copy_zh["rules"]["exit_discipline"]
    assert "driver" not in copy_zh["rules"]["diversification"]
    assert "驅動因子" in copy_zh["rules"]["diversification"]
    assert copy_zh["problem_keys"]["concentration"] == "同一驅動因子集中"
    assert copy_zh["problem_keys"]["horizon_break"] == "交易論述時間軸破戒"

    tag = {"code": "suspected_averaging_down_losing", "params": {"n_adds": 3, "cur": -0.22}}
    resolved = card_renderer.localized_instrument_tag(tag, "zh-TW")
    assert "thesis" not in resolved and "交易論述" in resolved, resolved

    bundle = {"engine_state": {"date_start": "2026-06-01", "date_end": "2026-07-14"},
              "review_plan": {"state_snapshot": {"market_context": {
                  "benchmarks": {"SPY": {"window_ret": 0.011},
                                 "VIX": {"last": 17.2, "delta": -1.8}}}}}}
    backdrop = card_renderer._market_backdrop(bundle, copy_zh)
    assert backdrop == "VIX 17.2 (-1.8)", backdrop
    # #366: the benchmark's window return is gone from this line entirely — it
    # was the card's only period-scoped figure, with nothing period-local to
    # compare it against, and "同期" ended up naming two different windows.
    assert "SPY" not in backdrop and "+1.1%" not in backdrop, backdrop
    # The review span is card-level metadata and leads the keynote preamble
    # (owner ruling 2026-07-22); it must not ride the market backdrop that
    # qualifies the excess tile.
    assert "2026-06-01" not in backdrop and "2026-07-14" not in backdrop, backdrop
    span = card_renderer._period_span(bundle, copy_zh)
    assert "2026-06-01" in span and "2026-07-14" in span, span
    assert "SPY" not in span, span

    problem_bundle = {"review_plan": {"state_snapshot": {"problem_stats": {
        "top": ["concentration", "horizon_break"],
        "per_key": {"concentration": {"recent_count": 3, "prev_count": 1, "trend": "worse"},
                    "horizon_break": {"recent_count": 1, "prev_count": 2, "trend": "better"}}}}},
        "rule_breach_decisions": []}
    copy_with_lang = dict(copy_zh, language="zh-TW")
    problem_lines = card_renderer._problem_lines(problem_bundle, copy_with_lang)
    assert any("同一驅動因子集中" in x for x in problem_lines)
    assert any("交易論述時間軸破戒" in x for x in problem_lines)
    assert not any("driver" in x or "thesis" in x for x in problem_lines)


def test_horizon_plan_join_ranks_full_exits_and_never_closes_a_reduction():
    state = {"date_end": "2026-07-14", "holdings": {"positions": {
        "ACTIVE": {"cycle_id": "ACTIVE#2026-01-01#1", "cost": 5000},
        "RED": {"cycle_id": "RED#2026-06-20#1", "cost": 2000},
    }}}
    theses = [
        {"cycle_id": "ACTIVE#2026-01-01#1", "ticker": "ACTIVE", "horizon": "weeks",
         "maturity": "testable", "position_status": "open"},
        {"cycle_id": "RED#2026-06-20#1", "ticker": "RED", "horizon": "years",
         "maturity": "testable", "position_status": "open"},
        {"cycle_id": "EXIT#2026-06-01#1", "ticker": "EXIT", "horizon": "years",
         "maturity": "inferred", "position_status": "open"},
    ]
    recent = [
        {"cycle_id": "EXIT#2026-06-01#1", "ticker": "EXIT", "kind": "full",
         "exit_date": "2026-07-01", "exit_price": 100, "shares_sold": 100},
        {"cycle_id": "RED#2026-06-20#1", "ticker": "RED", "kind": "reduce",
         "exit_date": "2026-07-01", "exit_price": 100, "shares_sold": 10},
    ]
    markers = review_engine._horizon_markers(
        state, theses, ["ACTIVE#2026-01-01#1", "RED#2026-06-20#1"], recent)
    by_ticker = {row["ticker"]: row for row in markers}
    assert by_ticker["EXIT"]["kind"] == "exit_too_fast" and by_ticker["EXIT"]["exited"]
    assert by_ticker["ACTIVE"]["kind"] == "held_too_long" and not by_ticker["ACTIVE"]["exited"]
    assert "RED" not in by_ticker, "a reduction must remain active, never masquerade as a full exit"


def test_weekly_memory_surfaces_render_private_only_with_swap_framing():
    import card_renderer
    with tempfile.TemporaryDirectory() as tmp:
        card_path, state_path = _artifacts(tmp)
        card = json.loads(card_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
    due = {"id": "due-secret", "kind": "due_revisit", "required": True,
           "question": "SECRET exited", "options": [], "revisit_id": "SECRET-RID",
           "checkpoint": "30", "ticker": "SECRET", "swaps": [{"ticker": "SWAPSECRET"}],
           "compare": {"orig_ret": 0.20, "swap_ret": 0.05, "swap_net_pp": -0.15,
                       "idle_cash": False, "needs_prices": []}}
    snapshot = {
        "market_context": {"start": "2026-07-01", "end": "2026-07-14", "missing": [], "error": None,
                           "benchmarks": {"SPY": {"window_ret": 0.03, "ytd_ret": 0.11},
                                          "QQQ": {"window_ret": -0.02, "ytd_ret": 0.07},
                                          "VIX": {"last": 17.2, "delta": -1.8}}},
        "horizon_markers": [{"cycle_id": "SECRET#2026-01-01#1", "ticker": "SECRET",
                             "horizon": "weeks", "holding_days": 194, "kind": "held_too_long",
                             "exited": False, "maturity": "inferred"}],
        "exit_backlog": {"total": 4, "items": [{
            "revisit_id": "FOCUSSECRET-RID", "ticker": "FOCUSSECRET",
            "exit_date": "2025-06-01", "kind": "full",
            "compare": {"orig_ret": 0.25, "swap_ret": None, "swap_net_pp": None,
                        "idle_cash": True, "needs_prices": []}}], "summary": {
            "count": 4, "full": 3, "reduce": 1, "top_tickers": [["OLDSECRET", 2]],
            "span": {"first": "2025-01-01", "last": "2025-06-01"}, "priced": 3,
            "sold_before_rise": 1, "avg_hindsight_pp": -0.03}},
        "problem_stats": {"top": ["avgdown_breach"], "per_key": {
            "avgdown_breach": {"recent_count": 3, "prev_count": 1, "trend": "worse"}},
            "rules_check": [], "muted_rules": []},
    }
    state["price_snapshot"] = {"as_of": "2026-07-15", "prices": {}}
    bundle = {"session_id": "weekly-secret", "route": "weekly_review", "language": "en",
              "review_plan": {"state_snapshot": snapshot, "question_queue": [due]},
              "engine_state": state, "engine_card": card, "narrative": _narrative("en"),
              "commitment": None, "answers": {}, "thesis_updates": [], "thesis_decisions": [],
              "exit_narratives": [], "observations": [],
              "revisit_resolutions": [{"type": "resolution", "revisit_id": "SECRET-RID",
                                        "checkpoint": "30", "status": "falsified",
                                        "date": "2026-07-14", "session_id": "weekly-secret",
                                        "note": "SECRET lesson"}]}
    private = card_renderer.render_private(bundle)
    public = card_renderer.render_public(bundle)
    for fragment in ("VIX 17.2 (-1.8)", "inferred thesis horizon was weeks",
                     "prices frozen on 2026-07-15", "swap net -15.0 pp",
                     "Historical exit backlog: 4",
                     "Across 3 price-covered exits, the average post-exit move was -3.0 pp; 1 later rose",
                     "Backlog focus: FOCUSSECRET, full exit on 2025-06-01",
                     "Proceeds stayed idle while the original moved +25.0% using prices frozen on 2026-07-15",
                     "Averaging-down boundary", "SECRET lesson"):
        assert fragment in private, fragment
    # #366: the benchmark's window return no longer renders on either surface.
    assert "SPY window" not in private and "+3.0%" not in private, \
        "#366: the period-scoped benchmark return must be gone from the card"
    for fragment in ("SECRET", "SWAPSECRET", "OLDSECRET", "FOCUSSECRET", "2026-07-01", "+3.0%", "194"):
        assert fragment not in public, f"private weekly-memory fact leaked: {fragment}"


def test_rule_breach_decision_is_durable_deduped_and_revision_supersedes():
    stats = {"top": ["avgdown_breach"], "per_key": {"avgdown_breach": {
        "recent_count": 2, "prev_count": 0, "recent_amount": 0, "trend": "worse"}},
        "rules_check": [{"rule_id": "rule-old", "text": "Never add while underwater",
                         "problem_key": "avgdown_breach", "verdict": "held", "held_streak": 1,
                         "last_breach": {"week": "2026-07-14", "event_count": 1, "events": [
                             {"key": "avgdown_breach", "week": "2026-07-10", "ticker": "PLTR",
                              "note": "crossed the position boundary"}]}}]}
    questions = review_engine._rule_breach_questions(stats, {}, "en")
    assert len(questions) == 1 and questions[0]["evidence"][0]["ticker"] == "PLTR"
    question = questions[0]
    assert "The ledger recorded an event against rule" in question["question"]
    assert "note why it needs revision" in next(
        option["description"] for option in question["options"] if option["value"] == "revise_rule")
    manual_stats = json.loads(json.dumps(stats))
    manual_stats["top"] = ["exit_anxiety"]
    manual_stats["per_key"] = {"exit_anxiety": manual_stats["per_key"]["avgdown_breach"]}
    manual_stats["rules_check"][0]["problem_key"] = "exit_anxiety"
    manual_question = review_engine._rule_breach_questions(manual_stats, {}, "en")[0]
    assert {option["value"] for option in manual_question["options"]} == {"keep_tracking", "exception"}, \
        "manual problem keys must not offer a revision that no engine metric can track"
    try:
        manual_answer = {"question_id": manual_question["id"], "choice": "revise_rule",
                         "note": "replace it"}
        review_engine._build_rule_breach_decisions(
            {"session_id": "manual", "question_queue": [manual_question],
             "engine_state": {"date_end": "2026-07-14"}},
            {"answers": [manual_answer]}, {manual_question["id"]: manual_answer})
        assert False, "an unoffered manual-key revision must fail closed"
    except review_engine.ReviewError as exc:
        assert "unsupported rule breach decision" in str(exc)
    assert review_engine.session.PKEY["hold_severity"] == "hold_inconsistency"
    recent_exit = {"revisit_id": "EXIT#2026-07-01#1#2026-07-10#1.0", "ticker": "EXIT",
                   "cycle_id": "EXIT#2026-07-01#1", "exit_date": "2026-07-10",
                   "exit_price": 10.0, "shares_sold": 1.0, "shares_before": 1.0,
                   "kind": "full", "currency": "USD"}
    queue, _report = review_engine._question_queue(
        {"thesis_questions": [{"ticker": "PLTR", "question": "why add"}],
         "ticker_diagnosis": [{"ticker": "PLTR", "impact": 99999}]},
        {"holdings": {"positions": {"PLTR": {"cycle_id": "PLTR#2026-01-01#1", "cost": 99999}}}},
        {}, None, "en", [recent_exit], {}, [], stats, {}, [])
    assert [row["kind"] for row in queue] == ["revisit", "rule_breach", "add_thesis"], \
        "chosen-rule qualification must survive a larger non-perishable add question"
    plan = {"session_id": "2026-07-14__breach", "question_queue": [question],
            "engine_state": {"date_end": "2026-07-14", "metrics": {"avgdown_count": 2}}}
    missing_note = {"answers": [{"question_id": question["id"], "choice": "revise_rule"}]}
    try:
        review_engine._build_rule_breach_decisions(plan, missing_note)
        assert False, "revise_rule without a revision rationale must fail"
    except review_engine.ReviewError as exc:
        assert "requires a short note" in str(exc)

    answers = {"answers": [{"question_id": question["id"], "choice": "revise_rule",
                            "note": "Require written evidence before adding"}],
               "commitment": {"choice": "custom", "rule": "Require written evidence before adding",
                              "metric_key": "avgdown_count", "goal": "down",
                              "dim": "averaging_down", "revises_rule_id": "rule-old"}}
    missing_revision_link = json.loads(json.dumps(answers))
    missing_revision_link["commitment"].pop("revises_rule_id")
    try:
        review_engine._resolve_commitment(plan, missing_revision_link)
        assert False, "revise_rule must not leave the old rule tracking beside an unlinked replacement"
    except review_engine.ReviewError as exc:
        assert "one final commitment" in str(exc)
    skipped_revision = json.loads(json.dumps(answers))
    skipped_revision["commitment"] = {"choice": "skip", "revises_rule_id": "rule-old"}
    try:
        review_engine._resolve_commitment(plan, skipped_revision)
        assert False, "revise_rule must not finalize with a skipped replacement"
    except review_engine.ReviewError as exc:
        assert "replacement commitment" in str(exc)
    decisions = review_engine._build_rule_breach_decisions(plan, answers)
    commitment = review_engine._resolve_commitment(plan, answers)
    assert decisions[0]["decision"] == "revise_rule" and commitment["revises_rule_id"] == "rule-old"

    with tempfile.TemporaryDirectory() as root:
        rules = pathlib.Path(root) / "rules.jsonl"
        rules.write_text(json.dumps({"rule_id": "rule-old", "text": "Never add while underwater",
                                     "problem_key": "avgdown_breach", "status": "tracking"}) + "\n",
                         encoding="utf-8")
        bundle = {"session_id": "2026-07-14__breach", "route": "weekly_review", "language": "en",
                  "review_plan": {"persist": True}, "engine_state": {"date_end": "2026-07-14",
                  "metrics": {"avgdown_count": 2}, "problem_events": [], "problem_opportunities": {}},
                  "commitment": commitment, "thesis_updates": [], "thesis_decisions": [],
                  "exit_narratives": [], "rule_breach_decisions": decisions}
        review_engine.session.project_legacy(root, bundle, "private card\n")
        tracking, _muted = review_engine.problems.load_rules(str(rules))
        assert len(tracking) == 1 and tracking[0].get("revises") == "rule-old"

        session_dir = pathlib.Path(root) / "sessions" / bundle["session_id"]
        session_dir.mkdir(parents=True)
        (session_dir / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        history = review_engine._rule_breach_history(root)
        assert review_engine._rule_breach_questions(stats, history, "en") == [], \
            "the same breach period must not be asked again"
        worsened = json.loads(json.dumps(stats))
        worsened["per_key"]["avgdown_breach"]["recent_count"] = 3
        worsened["rules_check"][0]["last_breach"]["week"] = "2026-07-21"
        assert len(review_engine._rule_breach_questions(worsened, history, "en")) == 1


def _prepare_dated(tmp, root, date_end, tag, language="zh-TW"):
    """Prepare with the shared fixtures but a caller-controlled review date."""
    card_path, state_path = _artifacts(tmp)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["date_end"] = date_end
    dated = pathlib.Path(tmp) / f"state_{tag}.json"
    dated.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    csv_path = _trade_csv(tmp)
    run = _run("prepare", csv_path, "--root", root, "--language", language,
               "--card-json", card_path, "--state-json", dated)
    assert run.returncode == 0, run.stdout + run.stderr
    return _pending_plan(root, run.stdout)


def _prepare_headline_motive(tmp, root, tag, language="zh-TW"):
    """Prepare the fallback motive path with identical engine facts per tag.

    Pinned to weekly_review: the quiet-week backfill is the motive question's
    native route, and first_review would add #291 initial-thesis captures on
    top of the single question this fixture depends on."""
    card_path, state_path = _artifacts(tmp)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["thesis_questions"] = []
    headline_card = pathlib.Path(tmp) / f"headline_card_{tag}.json"
    headline_card.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    run = _run("prepare", "--root", root, "--language", language,
               "--route", "weekly_review",
               "--card-json", headline_card, "--state-json", state_path,
               "--session-nonce", tag)
    assert run.returncode == 0, run.stdout + run.stderr
    plan = _pending_plan(root, run.stdout)
    assert [row["kind"] for row in plan["question_queue"]] == ["headline_motive"]
    return plan


def _headline_answers(plan, choice):
    return {
        "session_id": plan["session_id"],
        "answers": [{"question_id": "headline_motive", "choice": choice}],
        "thesis_updates": [_base_thesis_update()],
        "observations": [],
        "commitment": {"choice": "skip"},
    }


def _write_headline_interaction(tmp, plan, choice, tag):
    answers_path = pathlib.Path(tmp) / f"headline_answers_{tag}.json"
    narrative_path = pathlib.Path(tmp) / f"headline_narrative_{tag}.json"
    answers_path.write_text(
        json.dumps(_headline_answers(plan, choice), ensure_ascii=False), encoding="utf-8")
    narrative_path.write_text(
        json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
    return answers_path, narrative_path


def test_headline_motive_choice_changes_private_card_and_persists_canonically():
    """#294: the required answer is consumed, durable, private, and replay-safe."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_headline_motive(tmp, root, "headline")
        deliberate_a, narrative = _write_headline_interaction(
            tmp, plan, "deliberate_plan", "deliberate")
        emotional_a, _ = _write_headline_interaction(
            tmp, plan, "emotional_reaction", "emotional")

        deliberate = _run("preview", "--root", root, "--session-id", plan["session_id"],
                          "--answers", deliberate_a, "--narrative", narrative)
        emotional = _run("preview", "--root", root, "--session-id", plan["session_id"],
                         "--answers", emotional_a, "--narrative", narrative)
        assert deliberate.returncode == emotional.returncode == 0
        deliberate_payload = json.loads(deliberate.stdout)
        emotional_payload = json.loads(emotional.stdout)
        assert deliberate_payload["private_card"] != emotional_payload["private_card"]
        assert "動機記為：事先規劃" in deliberate_payload["private_card"]
        assert "動機記為：情緒反應" in emotional_payload["private_card"]
        assert deliberate_payload["public_card"] == emotional_payload["public_card"], \
            "a private motive choice must not affect or leak into the public card"
        for secret in ("事先規劃", "情緒反應", "headline_motive"):
            assert secret not in deliberate_payload["public_card"]
            assert secret not in emotional_payload["public_card"]

        skipped_a, _ = _write_headline_interaction(tmp, plan, "skip", "skip")
        skipped = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", skipped_a, "--narrative", narrative)
        assert skipped.returncode == 0, skipped.stdout + skipped.stderr
        assert "動機記為：" not in json.loads(skipped.stdout)["private_card"], \
            "skip must not fabricate a motive classification"

        finalized = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                         "--answers", deliberate_a, "--narrative", narrative)
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        session_dir = pathlib.Path(json.loads(finalized.stdout)["path"])
        bundle_path = session_dir / "bundle.json"
        before_retry = bundle_path.read_text(encoding="utf-8")
        bundle = json.loads(before_retry)
        assert len(bundle["headline_motive_events"]) == 1
        event = bundle["headline_motive_events"][0]
        assert event["event"] == "headline_motive_decision"
        assert event["decision"] == "deliberate_plan"
        assert event["context"]["headline_dimension"]["id"] == "加碼攤平"
        assert event["event_id"].startswith("headline-motive-")

        retry = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", deliberate_a, "--narrative", narrative)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert json.loads(retry.stdout)["status"] == "no-op"
        assert bundle_path.read_text(encoding="utf-8") == before_retry

        projection = pathlib.Path(root) / "headline_motives.jsonl"
        projected = [json.loads(line) for line in projection.read_text(encoding="utf-8").splitlines()]
        assert projected == [event]
        projection.unlink()

        later = _prepare_headline_motive(tmp, root, "later")
        assert later["state_snapshot"]["headline_motive_events"] == [event], \
            "later state reconstruction must use the canonical bundle, not the projection"

        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr
        repaired_rows = [json.loads(line) for line in
                         projection.read_text(encoding="utf-8").splitlines()]
        assert repaired_rows == [event]
        assert bundle_path.read_text(encoding="utf-8") == before_retry


def test_headline_motive_skip_keeps_bundle_key_absent_for_replay_compat():
    """#294: a skip produces no event AND no bundle key, the same
    absent-when-empty contract as revisit_resolutions — so sessions finalized
    before this key existed re-draft byte-identically and the documented-safe
    finalize retry stays a no-op instead of failing closed (#257 class)."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_headline_motive(tmp, root, "skipcompat")
        skipped_a, narrative = _write_headline_interaction(tmp, plan, "skip", "skipcompat")
        finalized = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                         "--answers", skipped_a, "--narrative", narrative)
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        bundle_path = pathlib.Path(json.loads(finalized.stdout)["path"]) / "bundle.json"
        before_retry = bundle_path.read_text(encoding="utf-8")
        assert "headline_motive_events" not in json.loads(before_retry)
        retry = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", skipped_a, "--narrative", narrative)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert json.loads(retry.stdout)["status"] == "no-op"
        assert bundle_path.read_text(encoding="utf-8") == before_retry


def test_headline_motive_event_copies_only_engine_context_and_routes_ticker_row():
    """#294/#288 boundary: consume existing context keys without inventing them."""
    question = {
        "id": "headline_motive", "kind": "headline_motive", "required": True,
        "options": [{"value": value} for value in
                    ("deliberate_plan", "emotional_reaction", "external_constraint", "skip")],
        "question_opportunity": {"context": {
            "ticker": "PLTR",
            "asked_because": "PLTR is the largest engine-ranked risk position",
            "headline_dimension": {"id": "position_sizing", "label": "Position sizing"},
        }},
    }
    plan = {"session_id": "2026-07-21__headline", "question_queue": [question],
            "engine_state": {"date_end": "2026-07-21"}}
    answers = {"answers": [{"question_id": "headline_motive",
                             "choice": "external_constraint"}]}
    events = review_engine._build_headline_motive_events(plan, answers)
    assert len(events) == 1
    event = events[0]
    assert event["context"] == question["question_opportunity"]["context"]
    assert "note" not in event and "evidence_delta" not in event
    assert review_engine._build_headline_motive_events(
        plan, {"answers": [{"question_id": "headline_motive", "choice": "skip"}]}) == []

    bundle = {"headline_motive_events": events}
    copy = card_renderer.load_copy("en")
    facts = {"instruments": [{"ticker": "PLTR"}]}
    trades = card_renderer._trades_block(bundle, {}, copy, facts, [], None, False)
    rows = next(payload for kind, payload in trades if kind == "rows")
    assert "External constraint" in rows[0]["subs"][0]
    risks = card_renderer._risks_block(
        bundle, {"top_holes": [{"dim": "position_sizing"}]}, copy,
        {"strength": "Process strength", "counterfactual": "Counterfactual"}, False,
        trade_tickers=["PLTR"])
    assert all("External constraint" not in str(payload) for _kind, payload in risks), \
        "an engine-grounded ticker motive must render once under its existing trade row"


def _exit_diagnosis():
    """A ticker_diagnosis carrying the aggregated early-exit pattern (#303):
    two instruments whose winners kept rising after the sell."""
    return [
        {"ticker": "TSLA", "impact": 8200.0,
         "tags": [{"code": "sold_winner_early", "params": {"win_early": 3, "win_n": 4}}]},
        {"ticker": "AMD", "impact": -1000.0,
         "tags": [{"code": "sold_winner_early", "params": {"win_early": 2, "win_n": 3}}]},
    ]


def _prepare_exit_consistency(tmp, root, tag, language="zh-TW"):
    """Prepare a weekly review whose card carries the early-exit pattern, so the
    one answerable exit-consistency question is the entire queue (#303).

    Pinned to weekly_review for the same reason as the headline-motive fixture:
    first_review would add #291 initial-thesis captures beside the one question
    this fixture asserts on."""
    card_path, state_path = _artifacts(tmp)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["thesis_questions"] = []
    card["ticker_diagnosis"] = _exit_diagnosis()
    exit_card = pathlib.Path(tmp) / f"exit_card_{tag}.json"
    exit_card.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    run = _run("prepare", "--root", root, "--language", language,
               "--route", "weekly_review",
               "--card-json", exit_card, "--state-json", state_path,
               "--session-nonce", tag)
    assert run.returncode == 0, run.stdout + run.stderr
    plan = _pending_plan(root, run.stdout)
    assert [row["kind"] for row in plan["question_queue"]] == ["exit_consistency"], \
        plan["question_queue"]
    return plan


def _exit_consistency_answers(plan, choice):
    return {
        "session_id": plan["session_id"],
        "answers": [{"question_id": "exit_consistency", "choice": choice}],
        "thesis_updates": [_base_thesis_update()],
        "observations": [],
        "commitment": {"choice": "skip"},
    }


def test_exit_consistency_question_is_answerable_and_persists_canonically():
    """#303: the aggregated early-exit pattern is put to the user as one grounded
    motive question (tickers + counts in the stem); a non-skip answer becomes a
    durable typed event in its own stream, the read-only [?] observation panel
    yields to the question on the card, and nothing leaks to the public card."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_exit_consistency(tmp, root, "ec")
        q = plan["question_queue"][0]
        assert q["kind"] == "exit_consistency" and q["required"] is True
        assert "TSLA 3/4" in q["question"] and "AMD 2/3" in q["question"], \
            "the stem must cite the exact engine facts so the user can answer it"
        assert [o["value"] for o in q["options"]] == [
            "deliberate_plan", "emotional_reaction", "external_constraint", "skip"]
        assert q["question_opportunity"]["intent"] == "classify_exit_consistency"

        answers_path = pathlib.Path(tmp) / "ec_answers.json"
        narrative_path = pathlib.Path(tmp) / "ec_narrative.json"
        answers_path.write_text(json.dumps(_exit_consistency_answers(plan, "deliberate_plan"),
                                           ensure_ascii=False), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")

        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers_path, "--narrative", narrative_path)
        assert preview.returncode == 0, preview.stdout + preview.stderr
        payload = json.loads(preview.stdout)
        assert "[?]" not in payload["private_card"] and "不用回答" not in payload["private_card"], \
            "the observation panel must yield to the question the user just answered"
        for secret in ("exit_consistency", "賣完還漲", "TSLA 3/4"):
            assert secret not in payload["public_card"], "private motive facts never go public"

        finalized = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                         "--answers", answers_path, "--narrative", narrative_path)
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        bundle_path = pathlib.Path(json.loads(finalized.stdout)["path"]) / "bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        assert len(bundle["exit_consistency_events"]) == 1
        event = bundle["exit_consistency_events"][0]
        assert event["event"] == "exit_consistency_decision"
        assert event["decision"] == "deliberate_plan"
        assert event["context"]["ticker"] == "TSLA"
        assert event["event_id"].startswith("exit-consistency-")

        projection = pathlib.Path(root) / "exit_consistency.jsonl"
        projected = [json.loads(line) for line in
                     projection.read_text(encoding="utf-8").splitlines()]
        assert projected == [event], "the answer projects to its own isolated audit log"
        headline = pathlib.Path(root) / "headline_motives.jsonl"
        assert not headline.exists() or headline.read_text(encoding="utf-8").strip() == "", \
            "an exit-consistency answer must never enter the headline-motive stream"


def test_exit_consistency_skip_keeps_bundle_key_absent_for_replay_compat():
    """#303: a skip produces no event and no bundle key, the same
    absent-when-empty contract as headline_motive_events — so the documented-safe
    finalize retry stays a no-op instead of failing closed."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_exit_consistency(tmp, root, "ecskip")
        answers_path = pathlib.Path(tmp) / "ecskip_answers.json"
        narrative_path = pathlib.Path(tmp) / "ecskip_narrative.json"
        answers_path.write_text(json.dumps(_exit_consistency_answers(plan, "skip"),
                                           ensure_ascii=False), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        finalized = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                         "--answers", answers_path, "--narrative", narrative_path)
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        bundle_path = pathlib.Path(json.loads(finalized.stdout)["path"]) / "bundle.json"
        before_retry = bundle_path.read_text(encoding="utf-8")
        assert "exit_consistency_events" not in json.loads(before_retry)
        retry = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers_path, "--narrative", narrative_path)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert json.loads(retry.stdout)["status"] == "no-op"
        assert bundle_path.read_text(encoding="utf-8") == before_retry


def test_exit_consistency_event_copies_only_engine_context():
    """#303 boundary (mirrors #299): consume existing context keys without
    inventing them, and never emit for a skip."""
    question = {
        "id": "exit_consistency", "kind": "exit_consistency", "required": True,
        "options": [{"value": value} for value in
                    ("deliberate_plan", "emotional_reaction", "external_constraint", "skip")],
        "question_opportunity": {"context": {
            "ticker": "TSLA",
            "asked_because": "5 of the 7 positions you sold kept rising, most clearly TSLA 3/4",
        }},
    }
    plan = {"session_id": "2026-07-22__exit", "question_queue": [question],
            "engine_state": {"date_end": "2026-07-22"}}
    events = review_engine._build_exit_consistency_events(
        plan, {"answers": [{"question_id": "exit_consistency", "choice": "emotional_reaction"}]})
    assert len(events) == 1
    event = events[0]
    assert event["decision"] == "emotional_reaction"
    assert event["context"] == question["question_opportunity"]["context"]
    assert "note" not in event and "evidence_delta" not in event
    assert review_engine._build_exit_consistency_events(
        plan, {"answers": [{"question_id": "exit_consistency", "choice": "skip"}]}) == []


def test_exit_consistency_respects_density_and_falls_back_to_observation_when_full():
    """#303: the exit-consistency question competes for a slot up to the route
    max but never past it. With room it is offered; when higher-signal due
    checkpoints already fill the band it is trimmed as over_max_capacity — its
    facts then survive as the read-only [?] observation panel, never dropped."""
    card = {"ticker_diagnosis": _exit_diagnosis(),
            "top_holes": [{"dim": "出場紀律", "raw": {"dim": "出場紀律"}}],
            "thesis_questions": [], "dims_raw": [{"dim": "出場紀律"}]}
    state = {"headline_dim": "出場紀律", "holdings": {"positions": {}}}
    # Room in the weekly band (max 3): the question is offered.
    queue, _ = review_engine._question_queue(card, state, {}, None, "zh-TW",
                                             route="weekly_review")
    assert [q["kind"] for q in queue] == ["exit_consistency"]
    # Band full with three higher-signal due checkpoints: the question is trimmed
    # and recorded, so the renderer falls back to the [?] observation panel.
    due = [{"item": {"ticker": f"DUE{i}", "exit_date": "2026-06-01", "exit_price": 100,
                     "shares_sold": 50 + i, "kind": "full", "cycle_id": f"DUE{i}#c"},
            "revisit_id": f"r{i}", "checkpoint": 30, "due_date": "2026-07-01"}
           for i in range(3)]
    queue, report = review_engine._question_queue(card, state, {}, None, "zh-TW",
                                                  due_revisits=due, route="weekly_review")
    assert len(queue) == 3 and "exit_consistency" not in [q["kind"] for q in queue]
    assert any(r.get("id") == "exit_consistency" and r.get("reason") == "over_max_capacity"
               for r in report["rejected"]), "a trimmed question must be recorded, not lost"
    # The same aggregated facts still render as the observation panel.
    panel = card_renderer._pattern_panel(card, card_renderer.load_copy("zh-TW"), False)
    assert panel is not None and panel[1]["mark"] == "?"


def _finalize(tmp, root, plan, answers, tag):
    a_path = pathlib.Path(tmp) / f"answers_{tag}.json"
    a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    n_path = pathlib.Path(tmp) / f"narrative_{tag}.json"
    n_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
    run = _run("finalize", "--session-id", plan["session_id"], "--root", root,
               "--answers", a_path, "--narrative", n_path)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def _base_thesis_update(extra=None):
    row = {"ticker": "PLTR", "cycle_id": "PLTR#2026-01-01#1",
           "why": "Enterprise adoption may still be underpriced",
           "horizon": "quarters", "exit_trigger": "Renewals weaken",
           "stop": None, "target_size": "bounded", "driver": "AI software",
           "maturity": "inferred"}
    row.update(extra or {})
    return row


def _answer_queue(plan, choose, commitment_choice="candidate_0"):
    """Answer every queued question via choose(question) -> answer dict."""
    answers = {"session_id": plan["session_id"], "answers": [], "observations": [],
               "commitment": {"choice": commitment_choice}, "thesis_updates": []}
    if plan["missing_thesis_positions"]:
        answers["thesis_updates"] = [_base_thesis_update()]
    for question in plan["question_queue"]:
        answers["answers"].append({"question_id": question["id"], **choose(question)})
    return answers


def _week1_choices(question):
    if question["kind"] == "revisit" and question["ticker"] == "BIG":
        return {"choice": "price_target"}
    if question["kind"] == "revisit":
        return {"choice": "skip"}
    if question["kind"] == "add_thesis":
        return {"choice": "new_evidence",
                "evidence_delta": {"claim": "Enterprise demand accelerated",
                                   "source": "earnings call"}}
    if question["kind"] == "rule_breach":
        return {"choice": "keep_tracking"}
    return {"choice": "deliberate_plan"}


def test_due_revisit_lifecycle_asks_resolves_and_requeues_skips():
    """#191: 30/60/90 checkpoints mature after capture, replay the user's own
    reason, persist non-skip verdicts as queue resolutions, and requeue skips."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "w1")
        assert plan1["state_snapshot"]["due_revisits"] == []          # fresh exits stay in capture
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices, "skip"), "w1")

        plan2 = _prepare_dated(tmp, root, "2026-08-15", "w2")
        queue2 = plan2["question_queue"]
        assert [q["kind"] for q in queue2] == ["due_revisit"] * 3     # 30d matured, capture window closed
        assert [q["ticker"] for q in queue2] == ["BIG", "MID", "SMALL"]  # largest exit first
        big = queue2[0]
        assert big["checkpoint"] == "30" and big["prior_exit_reason"] == "price_target"
        # Replays the exact kind-aware label the capture showed (full exit -> 到價了).
        assert "你當時說是「到價了」" in big["question"]
        assert big["compare"]["needs_prices"] == ["BIG"]              # offline stays honest
        assert {o["value"] for o in big["options"]} == {"still_valid", "modified", "falsified", "skip"}
        # PLTR's add question must not reopen: the decision cursor was answered in week 1.
        assert all(q.get("ticker") != "PLTR" for q in queue2)
        # Audit summary in the snapshot stays lightweight; the payload is the full source.
        snapshot_rows = plan2["state_snapshot"]["due_revisits"]
        assert [row["ticker"] for row in snapshot_rows] == ["BIG", "MID", "SMALL"]
        assert all(set(row) == {"revisit_id", "checkpoint", "due_date", "ticker"} for row in snapshot_rows)

        def week2(question):
            if question["ticker"] == "BIG":
                return {"choice": "falsified", "note": "Target was set too low; trend continued"}
            if question["ticker"] == "MID":
                return {"choice": "skip"}
            return {"choice": "still_valid"}
        result = _finalize(tmp, root, plan2, _answer_queue(plan2, week2), "w2")
        assert result["projection_error"] is None

        sys.path.insert(0, str(ENGINE_DIR))
        import revisit as revisit_engine
        _, resolutions, _ = revisit_engine.load_queue(os.path.join(root, "revisit.jsonl"))
        by_key = {(rid.split("#")[0], cp): row["status"] for (rid, cp), row in resolutions.items()}
        assert by_key == {("BIG", "30"): "falsified", ("SMALL", "30"): "still_valid"}
        falsified = [row for row in resolutions.values() if row["status"] == "falsified"]
        assert falsified[0]["note"] == "Target was set too low; trend continued"

        plan3 = _prepare_dated(tmp, root, "2026-08-16", "w3")
        pending = [(q["ticker"], q["checkpoint"]) for q in plan3["question_queue"]
                   if q["kind"] == "due_revisit"]
        assert pending == [("MID", "30")]                             # skip returns; verdicts do not

        # Replay compatibility: week 1 answered no due checkpoint, so its bundle
        # must not carry the key at all — a pre-upgrade session re-finalized with
        # this code must re-draft to the identical canonical bundle (no-op retry).
        bundle1 = json.loads((pathlib.Path(root) / "sessions" / plan1["session_id"] / "bundle.json")
                             .read_text(encoding="utf-8"))
        assert "revisit_resolutions" not in bundle1
        bundle2 = json.loads((pathlib.Path(root) / "sessions" / plan2["session_id"] / "bundle.json")
                             .read_text(encoding="utf-8"))
        assert len(bundle2["revisit_resolutions"]) == 2


def test_due_swap_comparison_uses_frozen_engine_price_snapshot():
    with tempfile.TemporaryDirectory() as root:
        item = {"type": "revisit", "revisit_id": "ORIG#2026-01-01#1#2026-07-10#1.0",
                "ticker": "ORIG", "cycle_id": "ORIG#2026-01-01#1",
                "exit_date": "2026-07-10", "exit_price": 100.0, "shares_sold": 1.0,
                "shares_before": 1.0, "kind": "full", "currency": "USD",
                "due": {"30": "2026-08-09", "60": "2026-09-08", "90": "2026-10-08"},
                "enqueued_at": "2026-07-14", "idle_cash": False,
                "swaps": [{"ticker": "SWAP", "date": "2026-07-11", "price": 100.0, "qty": 1.0}]}
        pathlib.Path(root, "revisit.jsonl").write_text(json.dumps(item) + "\n", encoding="utf-8")
        state = {"date_end": "2026-08-15", "price_snapshot": {
            "as_of": "2026-08-15", "prices": {"ORIG": 120.0, "SWAP": 105.0}}}
        _recent, due, _backlog, _meta = review_engine._prepare_exit_capture(root, state, True)
        assert len(due) == 1
        assert due[0]["compare"] == {"orig_ret": 0.2, "swap_ret": 0.05,
                                      "swap_net_pp": -0.15, "idle_cash": False,
                                      "needs_prices": []}


def test_perishable_capture_outranks_larger_due_checkpoints():
    """#136: a fresh exit's reason window cannot be backfilled, so its capture
    question must survive a week whose matured checkpoints carry bigger amounts.
    (All dates sit in the past relative to the wall clock — #169 rejects
    future-dated trade rows at ingestion.)"""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        early = pathlib.Path(tmp) / "early.csv"
        early.write_text("\n".join([
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency",
            "BIG,BUY,10,100,2026-05-01,Trade,US,USD",
            "MID,BUY,10,100,2026-05-02,Trade,US,USD",
            "SMALL,BUY,2,100,2026-05-03,Trade,US,USD",
            "BIG,SELL,10,200,2026-05-10,Trade,US,USD",
            "MID,SELL,6,150,2026-05-11,Trade,US,USD",
            "SMALL,SELL,2,200,2026-05-12,Trade,US,USD",
        ]) + "\n", encoding="utf-8")

        def prepare(csv_path, date_end, tag):
            card_path, state_path = _artifacts(tmp)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["date_end"] = date_end
            dated = pathlib.Path(tmp) / f"state_{tag}.json"
            dated.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            run = _run("prepare", csv_path, "--root", root,
                       "--card-json", card_path, "--state-json", dated)
            assert run.returncode == 0, run.stdout + run.stderr
            return json.loads(run.stdout)["review_plan"]

        plan1 = prepare(early, "2026-05-14", "w1")
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices, "skip"), "w1")

        late = pathlib.Path(tmp) / "late.csv"
        late.write_text("\n".join([
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency",
            "TINY,BUY,3,100,2026-06-08,Trade,US,USD",
            "TINY,SELL,3,100,2026-06-12,Trade,US,USD",
        ]) + "\n", encoding="utf-8")
        queue = prepare(late, "2026-06-15", "w2")["question_queue"]
        # TINY notional (300) is far below the matured BIG/MID/SMALL checkpoints
        # (2000/900/400) — the capture must still hold the first slot.
        assert queue[0]["kind"] == "revisit" and queue[0]["ticker"] == "TINY"
        assert [q["kind"] for q in queue[1:]] == ["due_revisit", "due_revisit"]
        assert [q["ticker"] for q in queue[1:]] == ["BIG", "MID"]


def test_week_two_question_stems_quote_the_week_one_thesis_verbatim():
    """#226 option A: prepare weaves the user's own recorded thesis into add/exit
    stems deterministically — the engine resolves text and date from the same
    folded thesis states the plan already carries, the quote is verbatim, and a
    cycle without any recorded thesis keeps today's plain stem."""
    claim = "Enterprise adoption may still be underpriced"        # _base_thesis_update wording
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        w1_csv = pathlib.Path(tmp) / "memory_w1.csv"
        w1_csv.write_text("\n".join([
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency",
            "PLTR,BUY,10,100,2026-01-01,Trade,US,USD",
        ]) + "\n", encoding="utf-8")
        card_path, state_path = _artifacts(tmp)
        state = json.loads(pathlib.Path(state_path).read_text(encoding="utf-8"))
        state["date_end"] = "2026-06-14"                          # thesis recording date
        w1_state = pathlib.Path(tmp) / "memory_state_w1.json"
        w1_state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        run = _run("prepare", w1_csv, "--root", root, "--card-json", card_path,
                   "--state-json", w1_state, "--language", "zh-TW")
        assert run.returncode == 0, run.stdout + run.stderr
        plan1 = json.loads(run.stdout)["review_plan"]
        answers1 = _answer_queue(plan1, _week1_choices, "skip")
        # The user states the thesis in their own words -> user-confirmed voice.
        answers1["thesis_updates"] = [_base_thesis_update({"maturity": "testable"})]
        _finalize(tmp, root, plan1, answers1, "memory-w1")

        # All trade dates sit in the past relative to the wall clock (#169
        # rejects future-dated rows); both exits stay inside the 14-day capture
        # window of the week-2 review date and below their 30-day checkpoints.
        w2_csv = pathlib.Path(tmp) / "memory_w2.csv"
        w2_csv.write_text("\n".join([
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency",
            "PLTR,BUY,10,100,2026-01-01,Trade,US,USD",
            "PLTR,SELL,10,150,2026-07-05,Trade,US,USD",
            "NEW,BUY,2,100,2026-07-01,Trade,US,USD",
            "NEW,SELL,2,100,2026-07-10,Trade,US,USD",
        ]) + "\n", encoding="utf-8")
        state = json.loads(pathlib.Path(state_path).read_text(encoding="utf-8"))
        state["date_end"] = "2026-07-12"
        position = state["holdings"]["positions"]["PLTR"]
        position["add_count"] = 4
        position["decision_cursor"] = "PLTR#2026-01-01#1#add#4"   # a new add reopens the question
        dated = pathlib.Path(tmp) / "memory_state_w2.json"
        dated.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        run = _run("prepare", w2_csv, "--root", root, "--card-json", card_path,
                   "--state-json", dated, "--session-nonce", "memory-w2",
                   "--language", "zh-TW")
        assert run.returncode == 0, run.stdout + run.stderr
        queue = json.loads(run.stdout)["review_plan"]["question_queue"]
        assert [(q["kind"], q["ticker"]) for q in queue] == \
            [("revisit", "PLTR"), ("revisit", "NEW"), ("add_thesis", "PLTR")]
        pltr_exit, new_exit, add = queue

        assert pltr_exit["question"] == (
            "PLTR 在 2026-07-05 全部出清，出場金額約 USD 1,500。"
            f"你進場時（2026-06-14）說的是『{claim}』。"
            "當時主要是什麼理由？")
        assert pltr_exit["asked_because"] == "它是你近期金額最大的出場之一"
        # NEW never recorded a thesis: its capture stem is byte-identical to today's.
        assert new_exit["question"] == \
            "NEW 在 2026-07-10 全部出清，出場金額約 USD 200。當時主要是什麼理由？"
        assert claim not in new_exit["question"]
        assert add["question"] == (
            f"PLTR 你在 2026-06-14 說過『{claim}』。"
            "PLTR 加碼時有新證據，還是只想攤低成本？"
            "（問這題是因為它是你本週成本最大的部位）")
        assert add["asked_because"] == "它是你本週成本最大的部位"
        assert add["prior_thesis_id"] and add["prior_thesis_id"].startswith("thesis-"), \
            "IDs stay attached for provenance even though the stem already quotes the text"


def test_problem_book_projection_is_readable_marked_and_self_healing():
    """#191/#194: projected problem events must round-trip through load_book,
    each review records its Opportunity Check mark, and replays stay idempotent."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        book = pathlib.Path(root) / "problems.jsonl"
        legacy_bad = {"key": "oversize", "kind": "state", "week": "2026-06-01",
                      "ticker": "OLD", "amount": None, "note": "untyped legacy row"}
        book.write_text(json.dumps(legacy_bad, ensure_ascii=False) + "\n", encoding="utf-8")

        plan1 = _prepare_dated(tmp, root, "2026-07-14", "w1")
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices), "w1")

        sys.path.insert(0, str(ENGINE_DIR))
        import problems as problems_engine
        events, marks, skipped = problems_engine.load_book(str(book))
        assert skipped == 1                                            # untyped legacy row stays unreadable
        assert [e["key"] for e in events] == ["avgdown_breach"]        # typed projection reads back
        assert marks and marks[0]["week"] == "2026-07-14"
        assert marks[0]["opportunities"] == {"avgdown_breach": True}

        # Finalize replay (already-committed session) must not duplicate the book.
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices), "w1-replay")
        events2, marks2, _ = problems_engine.load_book(str(book))
        assert len(events2) == len(events) and len(marks2) == len(marks)

        # The next review folds the book into review-ready stats.
        plan2 = _prepare_dated(tmp, root, "2026-08-15", "w2")
        stats = plan2["state_snapshot"]["problem_stats"]
        assert stats["events_n"] == 1 and stats["marks_n"] == 1
        assert "avgdown_breach" in stats["per_key"]
        assert isinstance(stats["rules_check"], list)                  # week-1 commitment rule is tracked
        assert stats["rules_check"] and stats["rules_check"][0]["problem_key"] == "avgdown_breach"


def test_same_week_conflicting_mark_fails_closed_but_commit_survives():
    """#166 semantics through v2: a second same-week session whose opportunities
    differ must surface a recoverable projection error, not corrupt the book."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "w1")
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices), "w1")

        card_path, state_path = _artifacts(tmp)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["problem_opportunities"] = {"avgdown_breach": False}     # same week, different mark
        conflicted = pathlib.Path(tmp) / "state_conflict.json"
        conflicted.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        run = _run("prepare", _trade_csv(tmp), "--root", root,
                   "--card-json", card_path, "--state-json", conflicted)
        assert run.returncode == 0, run.stdout + run.stderr
        plan2 = json.loads(run.stdout)["review_plan"]
        answers = _answer_queue(plan2, _week1_choices)
        a_path = pathlib.Path(tmp) / "answers_conflict.json"
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        n_path = pathlib.Path(tmp) / "narrative_conflict.json"
        n_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        run = _run("finalize", "--session-id", plan2["session_id"], "--root", root,
                   "--answers", a_path, "--narrative", n_path)
        assert run.returncode == 0, run.stdout + run.stderr
        payload = json.loads(run.stdout)
        assert payload["status"] == "committed"                        # canonical bundle is never blocked
        assert payload["recoverable"] and "review_mark" in payload["projection_error"]
        # A mark conflict is one projection failing — it must not hold the card
        # or the projection report hostage (they land before the conflict raises).
        cards = list((pathlib.Path(root) / "cards").glob("*.md"))
        assert len(cards) == 2, [c.name for c in cards]
        report = json.loads((pathlib.Path(root) / "projections" / (plan2["session_id"] + ".json"))
                            .read_text(encoding="utf-8"))
        problems_rows = [row for row in report["rows"] if row.get("status") == "mark_conflict"]
        assert problems_rows and "review_mark" in problems_rows[0]["error"]


def _offline_engine_env(tmp):
    """PYTHONPATH-injected yfinance ImportError stub so a REAL (non-injected)
    engine subprocess run stays offline-deterministic -- same pattern as
    test_prepare_completes_when_no_hole_and_no_headline_dimension."""
    stub_dir = pathlib.Path(tmp) / "stubs"
    stub_dir.mkdir()
    (stub_dir / "yfinance.py").write_text('raise ImportError("offline stub")\n', encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(stub_dir), env.get("PYTHONPATH")) if part)
    return env


def _run_real_review(tmp, root, csv_path, env, tag):
    """Full prepare+finalize over a REAL CSV through the real engine
    subprocess (no --card-json/--state-json injection, unlike _prepare_dated)
    so TR_PREV_END/TR_PREV_PREV_END actually get exercised. Every queued
    question is answered "skip" -- valid for every kind this minimal fixture
    can produce (revisit / headline_motive quiet-week backfill). This fixture
    has no open position and no ETF, but it runs the engine offline (the
    _offline_engine_env stub blocks yfinance), so #289 makes `price_source`
    (unavailable) a required honesty key: author one digit-free sentence per
    key the plan actually requires, exactly as a real degraded review must."""
    run = _run("prepare", csv_path, "--root", root, "--route", "weekly_review",
               "--session-nonce", tag, env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    plan = _pending_plan(root, run.stdout)
    answers = {"session_id": plan["session_id"], "answers": [], "observations": [],
               "commitment": {"choice": "skip"}, "thesis_updates": []}
    for question in plan["question_queue"]:
        answers["answers"].append({"question_id": question["id"], "choice": "skip"})
    a_path = pathlib.Path(tmp) / f"answers_{tag}.json"
    a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    honesty = {
        key: {
            "price_source": "這期的現價引擎抓不到，卡上據此說明是價格缺了，不當成下市或零報酬。",
        }.get(key, "這項限制在卡上保持明示，而不是把缺口當成零。")
        for key in plan["card_plan"]["required_honesty_keys"]
    }
    narrative = {"headline": "測試標題", "mirror": "測試鏡像"}
    if honesty:
        narrative["honesty"] = honesty
    n_path = pathlib.Path(tmp) / f"narrative_{tag}.json"
    n_path.write_text(json.dumps(narrative, ensure_ascii=False), encoding="utf-8")
    final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                 "--answers", a_path, "--narrative", n_path, env=env)
    assert final.returncode == 0, final.stdout + final.stderr
    return plan["engine_state"], json.loads(final.stdout)


def test_same_week_rerun_keeps_opportunity_flags_stable():
    """#270: review.py sets TR_PREV_END from last_state.json's date_end
    *before* invoking the engine -- before this run's own date_end can be
    known, since that requires parsing the CSV. Re-running a byte-identical
    CSV for the identical week therefore used to make TR_PREV_END alias THIS
    run's own date_end (a prior finalize had already advanced the anchor to
    it), which collapsed every "new since prev_end" boundary
    (build_problem_events) and flipped exit_anxiety/fomo_entry from True to
    False on the second pass -- tripping the #166 fail-closed mark guard on a
    rerun that changed nothing about the underlying trades.

    Three independent sessions (distinct --session-nonce so they are not
    deduped as the identical session) over the identical CSV/week must keep
    producing byte-identical problem_opportunities, and none may report a
    #166 mark conflict."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        csv_path = pathlib.Path(tmp) / "rerun.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "ACME,BUY,10,50,2026-03-02,Trade,US,USD\n"
            "ACME,SELL,10,55,2026-03-03,Trade,US,USD\n",
            encoding="utf-8")
        env = _offline_engine_env(tmp)

        es1, result1 = _run_real_review(tmp, root, csv_path, env, "rerun1")
        opps1 = es1["problem_opportunities"]
        assert opps1["exit_anxiety"] is True and opps1["fomo_entry"] is True, \
            f"fixture must actually exercise both opportunities on the first pass: {opps1}"
        assert result1.get("projection_error") is None, \
            f"first (fresh-root) finalize must not conflict: {result1.get('projection_error')}"

        es2, result2 = _run_real_review(tmp, root, csv_path, env, "rerun2")
        assert es2["problem_opportunities"] == opps1, (
            "same CSV/week rerun flipped problem_opportunities (#270): "
            f"{opps1} -> {es2['problem_opportunities']}")
        assert result2.get("projection_error") is None, (
            "same-content rerun must not trip the #166 mark-conflict guard: "
            f"{result2.get('projection_error')}")

        # The fixed point must hold indefinitely, not just survive one retry.
        es3, result3 = _run_real_review(tmp, root, csv_path, env, "rerun3")
        assert es3["problem_opportunities"] == opps1
        assert result3.get("projection_error") is None


def test_prev_end_advances_correctly_across_genuinely_different_weeks():
    """#270 companion guard: the self-exclusion fix must not turn every review
    into an unconditional None. A second, genuinely later CSV (a realistic
    incremental broker export) must anchor prev_end to the first review's real
    date_end -- neither None nor aliased to its own date_end. Nothing else in
    this suite exercises the real engine subprocess across two real weeks
    (every other multi-week test injects --card-json/--state-json and never
    runs _run_engine), so this is the only coverage for the TR_PREV_END /
    TR_PREV_PREV_END wiring in review.py on the ordinary advancing path."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        week1 = pathlib.Path(tmp) / "week1.csv"
        week1.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "ACME,BUY,10,50,2026-03-02,Trade,US,USD\n"
            "ACME,SELL,10,55,2026-03-03,Trade,US,USD\n",
            encoding="utf-8")
        week2 = pathlib.Path(tmp) / "week2.csv"
        week2.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "ACME,BUY,10,50,2026-03-02,Trade,US,USD\n"
            "ACME,SELL,10,55,2026-03-03,Trade,US,USD\n"
            "BETA,BUY,5,80,2026-03-12,Trade,US,USD\n"
            "BETA,SELL,5,90,2026-03-13,Trade,US,USD\n",
            encoding="utf-8")
        env = _offline_engine_env(tmp)

        es1, result1 = _run_real_review(tmp, root, week1, env, "wk1")
        assert es1["prev_end"] is None, "first-ever review has no prior boundary"
        assert result1.get("projection_error") is None

        es2, result2 = _run_real_review(tmp, root, week2, env, "wk2")
        assert es2["date_end"] == "2026-03-13"
        assert es2["prev_end"] == "2026-03-03", (
            "a genuinely later week must anchor to the prior review's real "
            f"date_end, not self-alias or reset to None: got {es2['prev_end']}")
        assert result2.get("projection_error") is None


def test_thesis_updates_reject_out_of_vocabulary_inference_values():
    """New canonical enum and horizon values fail closed without breaking legacy reads."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_dated(tmp, root, "2026-07-14", "w1")
        answers = _answer_queue(plan, _week1_choices)
        answers["thesis_updates"] = [_base_thesis_update({"emotion": "FOMO"})]
        a_path = pathlib.Path(tmp) / "answers_vocab.json"
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        n_path = pathlib.Path(tmp) / "narrative_vocab.json"
        n_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
        run = _run("finalize", "--session-id", plan["session_id"], "--root", root,
                   "--answers", a_path, "--narrative", n_path)
        payload = json.loads(run.stdout)
        assert payload["status"] == "error" and "invalid emotion" in payload["error"]
        assert not (pathlib.Path(root) / "sessions" / plan["session_id"]).exists()

        answers["thesis_updates"] = [_base_thesis_update({"horizon": "季"})]
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        run = _run("finalize", "--session-id", plan["session_id"], "--root", root,
                   "--answers", a_path, "--narrative", n_path)
        payload = json.loads(run.stdout)
        assert payload["status"] == "error" and "invalid horizon" in payload["error"]
        assert not (pathlib.Path(root) / "sessions" / plan["session_id"]).exists()

        positions = (plan["engine_state"]["holdings"]["positions"])
        legacy = [_base_thesis_update({"horizon": "季"})]
        assert review_engine.thesis.validate_thesis_updates(legacy, positions) == legacy, \
            "plans prepared before stable IDs must remain retry-compatible"


def test_schemas_cover_due_revisit_and_resolutions():
    """Contract-sync pin (CLAUDE.md): the published schemas must describe what
    the code emits — a new question kind or bundle key updates them in the same
    change. (Offline suite has no jsonschema validator; pin the vocabulary.)"""
    plan_schema = json.loads((SCHEMAS / "review-plan.schema.json").read_text(encoding="utf-8"))
    item = plan_schema["properties"]["question_queue"]["items"]
    assert "due_revisit" in item["properties"]["kind"]["enum"]
    for key in ("checkpoint", "due_date", "compare", "prior_exit_reason", "prior_note", "swaps"):
        assert key in item["properties"], key
    assert "rule_breach" in item["properties"]["kind"]["enum"]
    horizon_ids = plan_schema["properties"]["card_plan"]["properties"]["horizon_ids"]
    assert set(horizon_ids["items"]["enum"]) == {"weeks", "quarters", "years"}
    for key in ("rule_id", "rule_text", "problem_key", "breach_week", "evidence",
                "recent_count", "recent_amount", "trend"):
        assert key in item["properties"], key
    # #226: the localized "why this question was picked" display field is part
    # of the published queue-row shape (add/exit questions).
    assert "asked_because" in item["properties"]
    bundle_schema = json.loads((SCHEMAS / "session-bundle.schema.json").read_text(encoding="utf-8"))
    resolutions = bundle_schema["properties"]["revisit_resolutions"]
    assert set(resolutions["items"]["properties"]["status"]["enum"]) == {"still_valid", "modified", "falsified"}
    # Absent-when-empty is the replay-compatibility contract, so it must stay optional.
    assert "revisit_resolutions" not in bundle_schema["required"]
    breach = bundle_schema["properties"]["rule_breach_decisions"]
    assert set(breach["items"]["properties"]["decision"]["enum"]) == \
        {"keep_tracking", "revise_rule", "exception"}
    assert "rule_breach_decisions" not in bundle_schema["required"]
    motive = bundle_schema["properties"]["headline_motive_events"]["items"]
    assert motive["properties"]["event"]["const"] == "headline_motive_decision"
    assert set(motive["properties"]["decision"]["enum"]) == \
        {"deliberate_plan", "emotional_reaction", "external_constraint"}
    assert motive["properties"]["context"]["$ref"].endswith("#/properties/context")
    assert "headline_motive_events" not in bundle_schema["required"], \
        "older canonical bundles must remain replay-compatible"
    # #250: engine_version provenance is a published top-level metadata key on
    # both the plan and the bundle. It must stay optional — older artifacts
    # predate it, so it is off the required list for replay compatibility.
    for schema in (plan_schema, bundle_schema):
        engine_version = schema["properties"]["engine_version"]
        assert engine_version["required"] == ["id", "source"]
        assert set(engine_version["properties"]["source"]["enum"]) == {"file", "git", "unknown"}
        assert "engine_version" not in schema["required"]


def test_thesis_updates_preserve_inference_only_fields():
    """#155/#38: emotion/confidence/source fields ride through validation,
    the canonical bundle, and the legacy projection without being stripped."""
    inference = {"source_type": "self", "source_name": None, "source_confidence": "candidate",
                 "emotion": "composed", "emotion_inferred": True,
                 "confidence": "medium", "confidence_inferred": True}
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_dated(tmp, root, "2026-07-14", "w1")
        answers = _answer_queue(plan, _week1_choices)
        answers["thesis_updates"] = [_base_thesis_update(inference)]
        _finalize(tmp, root, plan, answers, "w1")

        bundle = json.loads((pathlib.Path(root) / "sessions" / plan["session_id"] / "bundle.json")
                            .read_text(encoding="utf-8"))
        stored = bundle["thesis_updates"][0]
        projected = [json.loads(line) for line in
                     (pathlib.Path(root) / "theses.jsonl").read_text(encoding="utf-8").splitlines()]
        projected_thesis = [row for row in projected if row.get("event") is None][0]
        for key, value in inference.items():
            assert stored.get(key) == value, key
            assert projected_thesis.get(key) == value, key


def test_thesis_update_delta_fills_skeleton_and_rejects_ticker_mismatch():
    """#251: for uncovered cycles the agent submits only the join key and the
    qualitative fields; the engine fills ticker/maturity from the plan. An
    explicit ticker that contradicts the engine-owned mapping, or any
    agent-supplied decision_cursor, fails closed with a structured error."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_dated(tmp, root, "2026-07-14", "w1")
        assert plan["authoring_contract"]["thesis_updates"]["required_from_agent"] == \
            ["cycle_id", "why", "exit_trigger"]
        delta = {"cycle_id": "PLTR#2026-01-01#1",
                 "why": "Enterprise adoption may still be underpriced",
                 "exit_trigger": "Renewals weaken", "horizon": "quarters"}

        answers = _answer_queue(plan, _week1_choices)
        a_path = pathlib.Path(tmp) / "answers_mismatch.json"
        n_path = pathlib.Path(tmp) / "narrative_delta.json"
        n_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")

        def reject(update, needle):
            answers["thesis_updates"] = [update]
            a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
            run = _run("finalize", "--session-id", plan["session_id"], "--root", root,
                       "--answers", a_path, "--narrative", n_path)
            payload = json.loads(run.stdout)
            assert payload["status"] == "error" and needle in payload["error"], payload
            assert not (pathlib.Path(root) / "sessions" / plan["session_id"]).exists()

        reject(dict(delta, ticker="NVDA"), "does not match engine-owned")
        # SKILL.md rule: the agent may not invent decision_cursor — enforced, not
        # just documented (#251 review finding). A null value must also be
        # rejected: key presence alone blocks reconstruct_states carry-forward.
        reject(dict(delta, decision_cursor="AGENT-INVENTED"), "engine-owned decision_cursor")
        reject(dict(delta, decision_cursor=None), "engine-owned decision_cursor")
        # A non-string cycle_id must produce the structured error contract, not a
        # bare TypeError traceback.
        reject(dict(delta, cycle_id=["not", "hashable"]), "unknown/inactive cycle_id")

        # A redundant lowercase ticker is the same instrument, not a mismatch.
        answers["thesis_updates"] = [dict(delta, ticker="pltr")]
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        run = _run("preview", "--session-id", plan["session_id"], "--root", root,
                   "--answers", a_path, "--narrative", n_path)
        assert run.returncode == 0, run.stdout + run.stderr

        answers["thesis_updates"] = [dict(delta)]
        _finalize(tmp, root, plan, answers, "delta")
        bundle = json.loads((pathlib.Path(root) / "sessions" / plan["session_id"] / "bundle.json")
                            .read_text(encoding="utf-8"))
        stored = [row for row in bundle["thesis_updates"]
                  if row.get("cycle_id") == "PLTR#2026-01-01#1"][0]
        assert stored["ticker"] == "PLTR" and stored["maturity"] == "inferred"
        assert stored["why"] == delta["why"] and stored["horizon"] == "quarters"


def test_snapshot_delta_inherits_candidate_provenance_and_stays_locked():
    """#251: snapshot-route deltas inherit source_confidence:"candidate" from the
    skeleton, while an explicit maturity override is still rejected — prefills
    must not weaken the no-laundering gate."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root)
        assert all(row.get("origin") == "snapshot" for row in plan["missing_thesis_positions"])
        assert plan["authoring_contract"]["thesis_updates"]["route_locked"] == \
            {"maturity": "inferred", "source_confidence": "candidate"}
        deltas = [{"cycle_id": row["cycle_id"],
                   "why": "The opening snapshot suggests a portfolio role that remains inferred",
                   "exit_trigger": "A later review contradicts the inferred portfolio role"}
                  for row in plan["missing_thesis_positions"]]
        answers = {"session_id": plan["session_id"], "answers": [],
                   "commitment": {"choice": "skip"}}
        a_path = pathlib.Path(tmp) / "answers.json"
        n_path = pathlib.Path(tmp) / "narrative.json"
        n_path.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False),
                          encoding="utf-8")

        answers["thesis_updates"] = [dict(deltas[0], maturity="testable")] + \
            [dict(row) for row in deltas[1:]]
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        run = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                   "--answers", a_path, "--narrative", n_path)
        payload = json.loads(run.stdout)
        assert payload["status"] == "error" and "must remain inferred" in payload["error"]

        answers["thesis_updates"] = [dict(row) for row in deltas]
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        run = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                   "--answers", a_path, "--narrative", n_path)
        assert run.returncode == 0, run.stdout + run.stderr
        bundle = json.loads((root / "sessions" / plan["session_id"] / "bundle.json")
                            .read_text(encoding="utf-8"))
        by_cycle = {row["cycle_id"]: row for row in bundle["thesis_updates"]}
        for delta in deltas:
            stored = by_cycle[delta["cycle_id"]]
            assert stored["maturity"] == "inferred"
            assert stored["source_confidence"] == "candidate"
            assert stored["origin"] == "snapshot"


def test_authoring_contract_mirrors_validation_constants():
    """#251 single-source pin: the contract surfaced to the agent must equal the
    constants validation enforces, or it silently becomes a second contract."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root)
        contract = plan["authoring_contract"]["thesis_updates"]
        assert contract["inference_enums"] == \
            {key: sorted(values) for key, values in review_engine.thesis.INFERENCE_ENUMS.items()}
        assert contract["maturity_values"] == sorted(review_engine.thesis.MATURITY_VALUES)
        assert contract["engine_owned_identity"] == \
            ["thesis_id", "event_id", "revises", "decision_cursor"]
        narrative_contract = plan["authoring_contract"]["narrative"]
        assert narrative_contract["allowed_fields"] == \
            sorted(review_engine.card_renderer.ALLOWED_NARRATIVE)
        assert narrative_contract["required"] == ["headline", "mirror"]
        # #260: gaps the engine chose not to ask about must stay neutral
        # coverage facts — the clause is contract surface, so pin its wording.
        assert narrative_contract["unprompted_gaps"] == (
            "coverage gaps the engine chose not to ask about "
            "(e.g. missing_thesis_positions) may appear only as neutral coverage "
            "facts; do not frame them as the user's negligence, and do not make "
            "them the central judgment of the headline or mirror"
        )


def test_repair_projections_never_regresses_a_newer_last_state():
    """#194.5: replaying old bundles (repair walks every session) must not
    overwrite a reconciliation anchor the engine has already advanced."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_dated(tmp, root, "2026-07-14", "w1")
        _finalize(tmp, root, plan, _answer_queue(plan, _week1_choices), "w1")
        last_state = pathlib.Path(root) / "last_state.json"

        newer = json.loads(last_state.read_text(encoding="utf-8"))
        newer["date_end"] = "2026-09-01"                               # engine moved on after commit
        last_state.write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
        run = _run("repair-projections", "--root", root)
        assert run.returncode == 0, run.stdout + run.stderr
        payload = json.loads(run.stdout)
        assert payload["status"] == "repaired"
        assert [r["last_state"] for r in payload["reports"]] == ["kept_newer"]
        assert json.loads(last_state.read_text(encoding="utf-8"))["date_end"] == "2026-09-01"

        stale = json.loads(last_state.read_text(encoding="utf-8"))
        stale["date_end"] = "2026-01-01"                               # corrupted/rolled-back anchor
        last_state.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
        run = _run("repair-projections", "--root", root)
        assert run.returncode == 0, run.stdout + run.stderr
        assert json.loads(last_state.read_text(encoding="utf-8"))["date_end"] == "2026-07-14"

        # A corrupted date_end is NOT "newer": only a valid ISO date may win,
        # or the documented repair path could never heal the anchor.
        broken = json.loads(last_state.read_text(encoding="utf-8"))
        broken["date_end"] = "9999-oops"
        last_state.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        run = _run("repair-projections", "--root", root)
        assert run.returncode == 0, run.stdout + run.stderr
        assert json.loads(last_state.read_text(encoding="utf-8"))["date_end"] == "2026-07-14"


def test_all_json_schemas_parse():
    names = {"review-plan.schema.json", "answers.schema.json", "narrative.schema.json",
             "session-bundle.schema.json", "question-opportunity.schema.json",
             "question-surface.schema.json", "capture.schema.json",
             "price-feed.schema.json", "condition-slot.schema.json",
             "condition-check.schema.json", "trade-premise.schema.json",
             "pre-trade-consultation.schema.json", "behavior-verdict.schema.json"}
    assert names == {p.name for p in SCHEMAS.glob("*.json")}
    for path in SCHEMAS.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["$schema"].endswith("2020-12/schema")


def test_cadence_classification_is_span_driven_and_fails_safe():
    """#237: the cadence tier keys off the span between reviews. A first review,
    a snapshot opening check, or any span past the 5-day threshold warrants the
    full story card; a short-span return is a light high-frequency check. Any
    unmeasurable span fails safe to full so nothing is silently hidden."""
    rv = review_engine
    prev = {"date_end": "2026-07-01"}
    # No prior boundary to measure against -> full, tagged with the reason.
    for route in ("first_review", "snapshot_review"):
        cad = rv._cadence(route, "2026-07-14", prev)
        assert cad["tier"] == "full" and cad["basis"] == route
        assert cad["span_days"] is None and cad["threshold_days"] == 5
        assert cad["override"] is None
    # Threshold is inclusive: 5 days is still light, 6 tips over to full.
    assert rv._cadence("weekly_review", "2026-07-06", prev) == {
        "tier": "light", "span_days": 5, "threshold_days": 5,
        "basis": "span", "override": None}
    assert rv._cadence("weekly_review", "2026-07-07", prev)["tier"] == "full"
    assert rv._cadence("weekly_review", "2026-07-07", prev)["span_days"] == 6
    # Same-day re-review is the lightest case; an out-of-order resend clamps to
    # 0 rather than reading as a long span.
    assert rv._cadence("weekly_review", "2026-07-01", prev)["span_days"] == 0
    assert rv._cadence("weekly_review", "2026-06-20", prev)["span_days"] == 0
    assert rv._cadence("weekly_review", "2026-06-20", prev)["tier"] == "light"
    # Returning with no comparable boundary, or unparseable/missing dates -> full.
    no_prior = rv._cadence("weekly_review", "2026-07-14", None)
    assert no_prior["tier"] == "full" and no_prior["basis"] == "no_prior_boundary"
    assert rv._cadence("weekly_review", "garbage", prev)["tier"] == "full"
    assert rv._cadence("weekly_review", "2026-07-06", {"date_end": None})["tier"] == "full"
    # The span helper is standalone and honest about missing inputs.
    assert rv._review_span_days("2026-07-06", prev) == 5
    assert rv._review_span_days(None, prev) is None
    assert rv._review_span_days("2026-07-06", {}) is None


def test_cadence_tier_is_wired_into_the_review_plan():
    """#237: the tier reaches the Review Plan's state_snapshot for both a first
    review (full) and a short-span return (light), proving the engine wiring —
    not just the pure classifier — carries it end to end."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "cad1")
        assert plan1["route"] == "first_review"
        cad1 = plan1["state_snapshot"]["cadence"]
        assert cad1["tier"] == "full" and cad1["basis"] == "first_review"
        assert cad1["span_days"] is None and cad1["threshold_days"] == 5
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices, "skip"), "cad1")

        # A 3-day return is a high-frequency check -> light.
        plan2 = _prepare_dated(tmp, root, "2026-07-17", "cad2")
        assert plan2["route"] == "weekly_review"
        cad2 = plan2["state_snapshot"]["cadence"]
        assert cad2["tier"] == "light" and cad2["basis"] == "span" and cad2["span_days"] == 3


def _prepare_dated_with_position(tmp, root, date_end, tag, extra_position=None, language="zh-TW"):
    """Like `_prepare_dated`, but can inject one additional holdings position
    so a test can exercise a cycle with no established thesis alongside one
    that already has one."""
    card_path, state_path = _artifacts(tmp)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["date_end"] = date_end
    if extra_position:
        ticker, row = extra_position
        state["holdings"]["positions"][ticker] = row
    dated = pathlib.Path(tmp) / f"state_{tag}.json"
    dated.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    csv_path = _trade_csv(tmp)
    run = _run("prepare", csv_path, "--root", root, "--language", language,
               "--card-json", card_path, "--state-json", dated)
    assert run.returncode == 0, run.stdout + run.stderr
    return _pending_plan(root, run.stdout)


def test_capture_light_tier_two_cycle_entries_end_to_end():
    """#237 #4: a light-tier capture attaches a note to a cycle that already
    has a thesis via a non-destructive `thesis_decision`, and seeds a minimal
    inferred thesis (why/exit_trigger required, else rejected rather than
    silently dropped) for a cycle that has none yet — without touching any of
    the shared full-review books, and cleaning up its own pending entry."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "capw1")
        assert plan1["route"] == "first_review"
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices, "skip"), "capw1")
        # PLTR#2026-01-01#1 now has an established thesis from _base_thesis_update().

        newco = ("NEWCO", {"shares": 5, "cost": 500, "avg_cost": 100,
                           "cycle_start": "2026-07-16", "cycle_id": "NEWCO#2026-07-16#1",
                           "add_count": 1, "decision_cursor": "NEWCO#2026-07-16#1#add#1"})
        plan2 = _prepare_dated_with_position(tmp, root, "2026-07-17", "capw2", extra_position=newco)
        assert plan2["route"] == "weekly_review"
        assert plan2["state_snapshot"]["cadence"]["tier"] == "light"

        active_ids = {row["cycle_id"] for row in plan2["state_snapshot"]["active_theses"]}
        assert "PLTR#2026-01-01#1" in active_ids
        missing_ids = {row["cycle_id"] for row in plan2["missing_thesis_positions"]}
        assert "NEWCO#2026-07-16#1" in missing_ids

        theses_path = pathlib.Path(root) / "theses.jsonl"
        watched = {p: p.read_text(encoding="utf-8") for p in
                  (pathlib.Path(root) / "log.jsonl", pathlib.Path(root) / "rules.jsonl",
                   pathlib.Path(root) / "problems.jsonl", pathlib.Path(root) / "last_state.json")
                  if p.exists()}

        # A brand-new cycle without why/exit_trigger is rejected outright, not
        # silently dropped by thesis.reconstruct_states's `if not current: continue`.
        bad_path = pathlib.Path(tmp) / "entries_bad.json"
        bad_path.write_text(json.dumps(
            [{"cycle_id": "NEWCO#2026-07-16#1", "note": "先追一小筆試試"}],
            ensure_ascii=False), encoding="utf-8")
        bad_run = _run("capture", "--session-id", plan2["session_id"], "--root", root,
                       "--entries", bad_path)
        assert bad_run.returncode != 0
        assert "why and exit_trigger" in json.loads(bad_run.stdout)["error"]
        # A rejected call must not have consumed the pending entry.
        assert (pathlib.Path(root) / ".pending" / plan2["session_id"]).exists()

        good_path = pathlib.Path(tmp) / "entries_good.json"
        good_path.write_text(json.dumps([
            {"cycle_id": "PLTR#2026-01-01#1", "note": "加碼是因為財報超預期", "emotion": "planned"},
            {"cycle_id": "NEWCO#2026-07-16#1", "note": "先追一小筆試試",
             "why": "看到帶量突破先小注跟", "exit_trigger": "跌破昨低就出"},
        ], ensure_ascii=False), encoding="utf-8")
        good_run = _run("capture", "--session-id", plan2["session_id"], "--root", root,
                        "--entries", good_path)
        assert good_run.returncode == 0, good_run.stdout + good_run.stderr
        out = json.loads(good_run.stdout)
        assert out["status"] == "captured" and out["entries"] == 2
        capture_session_id = out["capture_session_id"]
        assert capture_session_id == f"{plan2['session_id']}--capture"

        rows = [json.loads(l) for l in theses_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        captured = {r["cycle_id"]: r for r in rows if r.get("session_id") == capture_session_id}
        assert set(captured) == {"PLTR#2026-01-01#1", "NEWCO#2026-07-16#1"}
        assert captured["PLTR#2026-01-01#1"]["event"] == "thesis_decision"
        assert captured["PLTR#2026-01-01#1"]["note"] == "加碼是因為財報超預期"
        assert captured["PLTR#2026-01-01#1"]["emotion"] == "planned"
        # The decision-kind row must never carry a full thesis payload that
        # could shadow the established why/exit_trigger at reconstruct time.
        assert "why" not in captured["PLTR#2026-01-01#1"]
        new_row = captured["NEWCO#2026-07-16#1"]
        assert "event" not in new_row
        assert new_row["maturity"] == "inferred"
        assert new_row["why"] == "看到帶量突破先小注跟"
        assert new_row["exit_trigger"] == "跌破昨低就出"
        assert new_row["ticker"] == "NEWCO"

        # Neither book untouched by a full review, nor the established PLTR
        # thesis content, may be disturbed by a capture.
        for path, content in watched.items():
            assert path.read_text(encoding="utf-8") == content, f"{path} changed"
        reconstructed = thesis_engine.reconstruct_states(rows)
        pltr = next(r for r in reconstructed if r["cycle_id"] == "PLTR#2026-01-01#1")
        assert pltr["why"] == "Enterprise adoption may still be underpriced"

        # A successful call cleans up its own pending entry.
        assert not (pathlib.Path(root) / ".pending" / plan2["session_id"]).exists()


def test_capture_rejects_full_tier_session():
    """#237 #4: capture is only valid for a light-tier session; a full review
    must go through preview/finalize, not the capture-only shortcut."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "capfull")
        assert plan1["state_snapshot"]["cadence"]["tier"] == "full"
        entries_path = pathlib.Path(tmp) / "entries.json"
        entries_path.write_text(json.dumps(
            [{"cycle_id": "PLTR#2026-01-01#1", "note": "n/a"}], ensure_ascii=False),
            encoding="utf-8")
        run = _run("capture", "--session-id", plan1["session_id"], "--root", root,
                   "--entries", entries_path)
        assert run.returncode != 0
        assert "light-tier" in json.loads(run.stdout)["error"]


def test_capture_retry_after_pending_cleanup_is_idempotent():
    """#237 #4: an interrupted agent turn must be able to repeat the identical
    `capture` call after the first attempt already succeeded and cleaned up
    its pending entry, and get the same answer instead of a crash."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "capretry1")
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices, "skip"), "capretry1")
        plan2 = _prepare_dated(tmp, root, "2026-07-17", "capretry2")
        assert plan2["state_snapshot"]["cadence"]["tier"] == "light"

        entries_path = pathlib.Path(tmp) / "entries.json"
        entries_path.write_text(json.dumps(
            [{"cycle_id": "PLTR#2026-01-01#1", "note": "情緒性加碼"}], ensure_ascii=False),
            encoding="utf-8")
        first = _run("capture", "--session-id", plan2["session_id"], "--root", root,
                     "--entries", entries_path)
        assert first.returncode == 0, first.stdout + first.stderr
        assert not (pathlib.Path(root) / ".pending" / plan2["session_id"]).exists()

        retry = _run("capture", "--session-id", plan2["session_id"], "--root", root,
                     "--entries", entries_path)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        retry_out = json.loads(retry.stdout)
        assert retry_out["status"] == "captured" and retry_out["entries"] == 1
        assert retry_out["capture_session_id"] == json.loads(first.stdout)["capture_session_id"]

        theses_path = pathlib.Path(root) / "theses.jsonl"
        rows = [json.loads(l) for l in theses_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        matching = [r for r in rows if r.get("session_id") == retry_out["capture_session_id"]]
        assert len(matching) == 1, "a retry after cleanup must not duplicate the captured row"


def test_capture_serializes_with_finalize_on_the_shared_projection_lock():
    """#237 #4: `capture` must share the same root-wide projection lock as
    `finalize`'s legacy-book writers, or a concurrent capture/finalize pair can
    defeat `_append_session_rows`'s idempotency guarantee on `theses.jsonl`.

    Monkeypatching only affects code running in this test process, so both
    sides must call `review_engine.cmd_capture`/`cmd_finalize` directly
    in-process (an `argparse.Namespace` stand-in for parsed CLI args) rather
    than through `_run`'s subprocess, which would import a fresh, unpatched
    `session` module and never observe the gate at all."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "caplockw1")
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices, "skip"), "caplockw1")
        plan2 = _prepare_dated(tmp, root, "2026-07-17", "caplockw2")
        assert plan2["state_snapshot"]["cadence"]["tier"] == "light"
        entries_path = pathlib.Path(tmp) / "entries.json"
        entries_path.write_text(json.dumps(
            [{"cycle_id": "PLTR#2026-01-01#1", "note": "情緒性加碼"}], ensure_ascii=False),
            encoding="utf-8")

        real_append = session_engine._append_session_rows
        capture_entered = threading.Event()
        finalize_entered = threading.Event()
        release_capture = threading.Event()
        theses_calls = {"value": 0}
        call_lock = threading.Lock()

        def gated_append(path, *args, **kwargs):
            # finalize's projection makes several _append_session_rows calls
            # (log.jsonl, theses.jsonl, thesis_decisions.jsonl, revisit.jsonl,
            # rules.jsonl) per invocation; only the theses.jsonl calls are
            # meaningful here, so they get their own counter rather than a
            # global one that could misattribute an unrelated book's call.
            if str(path).endswith("theses.jsonl"):
                with call_lock:
                    theses_calls["value"] += 1
                    index = theses_calls["value"]
                if index == 1:
                    capture_entered.set()
                    if not release_capture.wait(5):
                        raise RuntimeError("timed out waiting to release the shared projection lock")
                else:
                    finalize_entered.set()
            return real_append(path, *args, **kwargs)

        session_engine._append_session_rows = gated_append
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

        class _Args:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def _run_capture():
            review_engine.cmd_capture(_Args(session_id=plan2["session_id"], root=root,
                                            entries=str(entries_path)))

        def _run_finalize():
            plan3 = _prepare_dated(tmp, root, "2026-07-24", "caplockw3")
            answers = _answer_queue(plan3, _week1_choices, "skip")
            a_path = pathlib.Path(tmp) / "answers_caplockw3.json"
            a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
            n_path = pathlib.Path(tmp) / "narrative_caplockw3.json"
            n_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")
            review_engine.cmd_finalize(_Args(session_id=plan3["session_id"], root=root,
                                             answers=str(a_path), narrative=str(n_path)))

        try:
            capture_future = pool.submit(_run_capture)
            assert capture_entered.wait(5), "capture never reached the shared projection lock"

            finalize_future = pool.submit(_run_finalize)
            assert not finalize_entered.wait(0.5), \
                "finalize's theses.jsonl projection must not enter while capture holds the lock"
            release_capture.set()

            capture_future.result(timeout=5)
            finalize_future.result(timeout=5)
        finally:
            release_capture.set()
            pool.shutdown(wait=True)
            session_engine._append_session_rows = real_append


def test_thesis_update_rejects_forged_engine_owned_identity():
    """thesis_id, revises, and event_id are engine-owned: on a cycle that has a
    prior thesis, an agent-supplied value that contradicts the engine's fails
    closed with a structured error, while echoing the engine's own values back
    is accepted (#251 covers the decision_cursor rejection; these are the other
    three enforce paths in _assign_thesis_ids)."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_dated(tmp, root, "2026-07-14", "w1")
        _finalize(tmp, root, plan1, _answer_queue(plan1, _week1_choices), "w1")

        plan2 = _prepare_dated(tmp, root, "2026-08-15", "w2")
        prior = [row for row in plan2["state_snapshot"]["thesis_states"]
                 if row.get("cycle_id") == "PLTR#2026-01-01#1"][0]
        # The stale-link case below only bites if the chain has advanced past
        # the original update event (week 1's decision moved last_event_id).
        assert prior["thesis_id"] and prior["last_event_id"] != prior["event_id"]

        answers = _answer_queue(plan2, lambda question: {"choice": "still_valid"})
        a_path = pathlib.Path(tmp) / "answers_identity.json"
        n_path = pathlib.Path(tmp) / "narrative_identity.json"
        n_path.write_text(json.dumps(_narrative(), ensure_ascii=False), encoding="utf-8")

        def reject(update, needle):
            answers["thesis_updates"] = [update]
            a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
            run = _run("finalize", "--session-id", plan2["session_id"], "--root", root,
                       "--answers", a_path, "--narrative", n_path)
            payload = json.loads(run.stdout)
            assert payload["status"] == "error" and needle in payload["error"], payload
            assert not (pathlib.Path(root) / "sessions" / plan2["session_id"]).exists()

        base = _base_thesis_update()
        reject(dict(base, thesis_id="thesis-invented"), "changes stable identity")
        reject(dict(base, revises="thesis-update-invented"), "stale revises link")
        # A real-but-superseded link (the original update event instead of the
        # latest decision) is the literal stale case and must also fail.
        reject(dict(base, revises=prior["event_id"]), "stale revises link")
        reject(dict(base, event_id="thesis-update-invented"), "invalid event_id")

        # Echoing the engine-owned values back is not a forgery: finalize
        # succeeds and the stored row carries the engine-assigned identity.
        answers["thesis_updates"] = [dict(base, thesis_id=prior["thesis_id"],
                                          revises=prior["last_event_id"])]
        _finalize(tmp, root, plan2, answers, "identity")
        bundle = json.loads((pathlib.Path(root) / "sessions" / plan2["session_id"] / "bundle.json")
                            .read_text(encoding="utf-8"))
        stored = [row for row in bundle["thesis_updates"]
                  if row.get("cycle_id") == "PLTR#2026-01-01#1"][0]
        assert stored["thesis_id"] == prior["thesis_id"]
        assert stored["revises"] == prior["last_event_id"]
        assert stored["event_id"].startswith("thesis-update-")
        assert stored["event_id"] != prior["event_id"]


# ── #284 monthly vs-market cadence (output contract §3) ─────────────────────
# The vs-market segment renders on the first full review of each calendar
# month; other full reviews the same month render Block 1 without it and
# without a gap note, and the segment-hosted honesty keys drop out of
# required_honesty_keys. Light capture sessions never finalize, so they
# neither consume nor reset the monthly slot; unreadable history fails
# closed toward showing.

_VS_MARKET_AB = {
    "port_tot": 0.24, "spy_tot": 0.11, "excess_vs_spy": 0.13,
    "bench": "SPY", "beta": 1.31, "alpha_ann": 0.09, "credible": False,
    "excess_split": {"allocation": 0.05, "selection": 0.08},
    "alpha_stat": {"alpha_ann": 0.09, "ci95": [-0.02, 0.2], "t": 1.2, "n_days": 40,
                   "gate": {"reason": "short_window", "need": "longer history"}},
    "benchmarks": {"SPY": {"excess": 0.13}, "QQQ": {"excess": 0.04}},
}
_VS_MARKET_LEDGER = [
    {"key": "alpha_credibility", "status": "short_window",
     "data": {"need": "longer history", "t": 1.2, "ci95": [-0.02, 0.2], "n_days": 40}},
    {"key": "sector_attribution", "status": "partial",
     "data": {"coverage": 0.8, "unproxied": ["SMALL"]}},
]
_VS_HONESTY_SENTENCES = {
    "alpha_credibility": "alpha 樣本仍短，只能當假設，不能當能力定論。",
    "sector_attribution": "板塊歸因不完整，配置拆帳只蓋到已分類的部位。",
    "etf_metadata": "配置型 ETF 缺費用率資料，這裡把缺口講明，而不是把缺值當成零。",
}
_VS_ZH_COPY_HONESTY = {  # renderer fallback wording; must never leak on a gated card
    "alpha_credibility": "Alpha 的樣本或統計強度不足，不能當成穩定能力。",
    "sector_attribution": "部分標的缺板塊基準，賽道與選股拆帳不完整。",
}
_VS_NOTE_ZH = "本期無法比對大盤：缺可用的基準序列。"  # copy block_missing.vs_market


def _prepare_vs_market(tmp, root, date_end, tag):
    """Prepare with the shared fixtures plus a complete vs-market cluster."""
    card_path, state_path = _artifacts(tmp)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["alpha_beta_breakdown"] = dict(_VS_MARKET_AB)
    card["honesty_ledger"] = list(_VS_MARKET_LEDGER) + list(card["honesty_ledger"])
    vs_card = pathlib.Path(tmp) / f"card_vs_{tag}.json"
    vs_card.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["date_end"] = date_end
    vs_state = pathlib.Path(tmp) / f"state_vs_{tag}.json"
    vs_state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    run = _run("prepare", "--root", root, "--language", "zh-TW",
               "--card-json", vs_card, "--state-json", vs_state)
    assert run.returncode == 0, run.stdout + run.stderr
    return _pending_plan(root, run.stdout)


def _vs_narrative(plan, extra_honesty=None):
    narrative = _narrative()
    narrative["honesty"] = {key: _VS_HONESTY_SENTENCES.get(key, "這項限制先講明。")
                            for key in plan["card_plan"]["required_honesty_keys"]}
    narrative["honesty"].update(extra_honesty or {})
    return narrative


def _vs_preview(tmp, root, plan, tag, extra_honesty=None):
    answers = _answer_queue(plan, _week1_choices, "skip")
    answers.pop("commitment", None)
    a_path = pathlib.Path(tmp) / f"answers_vs_{tag}.json"
    a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    n_path = pathlib.Path(tmp) / f"narrative_vs_{tag}.json"
    n_path.write_text(json.dumps(_vs_narrative(plan, extra_honesty), ensure_ascii=False),
                      encoding="utf-8")
    return _run("preview", "--root", root, "--session-id", plan["session_id"],
                "--answers", a_path, "--narrative", n_path), a_path, n_path


def _vs_finalize(root, plan, a_path, n_path, commitment="candidate_0"):
    answers = json.loads(a_path.read_text(encoding="utf-8"))
    answers["commitment"] = {"choice": commitment}
    a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    run = _run("finalize", "--root", root, "--session-id", plan["session_id"],
               "--answers", a_path, "--narrative", n_path)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def _s2_finding(card_text, engine_card):
    findings = check_card(card_text, {"engine_card": engine_card})
    return next(f for f in findings if f.assertion == "S-2")


def test_vs_market_month_gate_first_second_and_next_month():
    """#284 (a)(b)(c): the segment renders on the first full review of a
    month, disappears without a gap note on the second, and returns with the
    calendar month — with the segment-hosted honesty keys tracking it."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_vs_market(tmp, root, "2026-07-14", "w1")
        gate1 = plan1["engine_card"]["vs_market_gate"]
        assert gate1 == {"render": True, "basis": "first_full_review_of_month",
                         "month": "2026-07"}
        assert plan1["card_plan"]["required_honesty_keys"] == [
            "alpha_credibility", "sector_attribution", "etf_metadata"]
        preview1, a1, n1 = _vs_preview(tmp, root, plan1, "w1")
        assert preview1.returncode == 0, preview1.stdout + preview1.stderr
        card1 = json.loads(preview1.stdout)["private_card"]
        assert "超額報酬 +13 個百分點" in card1 and "β 1.31" in card1
        assert "贏大盤的 +13 個百分點拆為" in card1 and "vs QQQ +4pp" in card1
        assert "風險調整後 alpha" in card1
        for sentence in (_VS_HONESTY_SENTENCES["alpha_credibility"],
                         _VS_HONESTY_SENTENCES["sector_attribution"]):
            assert sentence in card1, \
                "honesty sentences required by the rendered segment must appear (now in the Block-1 footnote)"
        assert _VS_NOTE_ZH not in card1, "segment present -> no missing-data note"
        finding1 = _s2_finding(card1, plan1["engine_card"])
        assert finding1.passed, finding1.evidence
        _vs_finalize(root, plan1, a1, n1)

        plan2 = _prepare_vs_market(tmp, root, "2026-07-21", "w2")
        assert plan2["state_snapshot"]["cadence"]["tier"] == "full"  # span 7 > threshold
        gate2 = plan2["engine_card"]["vs_market_gate"]
        assert gate2 == {"render": False, "basis": "already_rendered_this_month",
                         "month": "2026-07"}
        assert plan2["card_plan"]["required_honesty_keys"] == ["etf_metadata"], \
            "segment-hosted honesty keys must not be required on a gated review"
        # The exact-cover gate rejects a sentence for a month-gated key:
        # required_honesty_keys excludes it this review (the segment that
        # would require it did not render), independent of where any
        # rendered honesty text ends up (2026-07-22: always the footnote).
        bad, _a, _n = _vs_preview(tmp, root, plan2, "w2bad",
                                  extra_honesty={"alpha_credibility": "多寫的一句。"})
        assert bad.returncode == 2
        assert "does not require" in json.loads(bad.stdout)["error"]
        preview2, a2, n2 = _vs_preview(tmp, root, plan2, "w2")
        assert preview2.returncode == 0, preview2.stdout + preview2.stderr
        payload2 = json.loads(preview2.stdout)
        card2 = payload2["private_card"]
        assert ("個百分點" not in card2 and "vs QQQ" not in card2
                and "風險調整後 alpha" not in card2 and "同期 SPY" not in card2), \
            "gated review renders no vs-market line"
        assert _VS_NOTE_ZH not in card2, "§3: month-gated -> simply absent, no gap note"
        assert "帳面總損益 -$300" in card2 and "本期算不出年化報酬" in card2, \
            "absolute P&L and the annualized module keep their own behavior"
        for sentence in list(_VS_ZH_COPY_HONESTY.values()) + [
                _VS_HONESTY_SENTENCES["alpha_credibility"],
                _VS_HONESTY_SENTENCES["sector_attribution"]]:
            assert sentence not in card2, "gated honesty keys must not leak into the footnote"
        html2 = pathlib.Path(payload2["private_card_html_path"]).read_text(encoding="utf-8")
        assert "相對大盤" not in html2 and "vs QQQ" not in html2 and "年化 α" not in html2, \
            "HTML surface drops the excess/alpha tiles and attribution bars too"
        finding2 = _s2_finding(card2, plan2["engine_card"])
        assert finding2.passed, finding2.evidence
        # S-2 stays strict in both directions on the real renderer output:
        # the gated card against an ungated context is a missing segment, and
        # the ungated card against a gated context is a gate violation.
        assert not _s2_finding(card2, plan1["engine_card"]).passed
        assert not _s2_finding(card1, plan2["engine_card"]).passed
        _vs_finalize(root, plan2, a2, n2, commitment="skip")

        plan3 = _prepare_vs_market(tmp, root, "2026-08-03", "w3")
        gate3 = plan3["engine_card"]["vs_market_gate"]
        assert gate3 == {"render": True, "basis": "first_full_review_of_month",
                         "month": "2026-08"}
        assert plan3["card_plan"]["required_honesty_keys"] == [
            "alpha_credibility", "sector_attribution", "etf_metadata"]
        preview3, _a3, _n3 = _vs_preview(tmp, root, plan3, "w3")
        assert preview3.returncode == 0, preview3.stdout + preview3.stderr
        card3 = json.loads(preview3.stdout)["private_card"]
        assert "超額報酬 +13 個百分點" in card3, "next calendar month re-renders the segment"


def test_vs_market_gate_light_capture_does_not_consume_slot():
    """#284 (e): a light-tier capture session never finalizes a card, so it
    neither consumes nor resets the monthly vs-market slot."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan1 = _prepare_vs_market(tmp, root, "2026-07-30", "y1")
        _preview1, a1, n1 = _vs_preview(tmp, root, plan1, "y1")
        _vs_finalize(root, plan1, a1, n1)

        plan2 = _prepare_vs_market(tmp, root, "2026-08-02", "y2")
        assert plan2["state_snapshot"]["cadence"]["tier"] == "light"  # span 3
        entries = pathlib.Path(tmp) / "capture_entries.json"
        entries.write_text(json.dumps(
            [{"cycle_id": "PLTR#2026-01-01#1", "note": "加碼是因為財報超預期"}],
            ensure_ascii=False), encoding="utf-8")
        captured = _run("capture", "--session-id", plan2["session_id"], "--root", root,
                        "--entries", entries)
        assert captured.returncode == 0, captured.stdout + captured.stderr
        assert json.loads(captured.stdout)["status"] == "captured"
        # The invariant the gate relies on: capture leaves no canonical bundle
        # and no log row, so the committed history cannot see the session.
        assert not (pathlib.Path(root) / "sessions" / plan2["session_id"]).exists()
        log_rows = [json.loads(line) for line in
                    (pathlib.Path(root) / "log.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        assert all(row.get("session_id") != plan2["session_id"] for row in log_rows)

        plan3 = _prepare_vs_market(tmp, root, "2026-08-10", "y3")
        assert plan3["state_snapshot"]["cadence"]["tier"] == "full"  # span 11 from y1
        assert plan3["engine_card"]["vs_market_gate"] == {
            "render": True, "basis": "first_full_review_of_month", "month": "2026-08"}
        assert plan3["card_plan"]["required_honesty_keys"] == [
            "alpha_credibility", "sector_attribution", "etf_metadata"]


def _gate_bundle(root, session_id, date_end, route="weekly_review", persist=True):
    final = pathlib.Path(root) / "sessions" / session_id
    final.mkdir(parents=True)
    (final / "bundle.json").write_text(json.dumps({
        "session_id": session_id, "route": route,
        "review_plan": {"persist": persist},
        "engine_state": {"date_end": date_end},
    }), encoding="utf-8")


def test_vs_market_gate_slot_consumers_and_fail_closed():
    """#284 (d) + consumer classification: only committed card-rendering
    reviews consume the month; snapshot and demo sessions do not; unreadable
    history or an unparseable review date renders the segment."""
    gate = review_engine._vs_market_gate
    with tempfile.TemporaryDirectory() as root:
        assert gate(root, "2026-07-21") == {
            "render": True, "basis": "first_full_review_of_month", "month": "2026-07"}
        assert gate(root, None) == {"render": True, "basis": "no_review_date", "month": None}
        assert gate(root, "not-a-date")["basis"] == "no_review_date"

    with tempfile.TemporaryDirectory() as root:
        _gate_bundle(root, "2026-07-14__w1", "2026-07-14")
        assert gate(root, "2026-07-21") == {
            "render": False, "basis": "already_rendered_this_month", "month": "2026-07"}
        assert gate(root, "2026-08-03")["render"] is True, "month boundary reopens the slot"
        assert gate(root, "2026-07-21", exclude_session_id="2026-07-14__w1")["render"] is True, \
            "an idempotent re-prepare of the committed session cannot flip its own decision"

    with tempfile.TemporaryDirectory() as root:
        _gate_bundle(root, "2026-07-10__snap", "2026-07-10", route="snapshot_review")
        assert gate(root, "2026-07-21")["render"] is True, \
            "snapshot reviews suppress the segment by design and must not burn the month"
        # The snapshot session's own log projection stays deduplicated by id.
        (pathlib.Path(root) / "log.jsonl").write_text(
            json.dumps({"date_end": "2026-07-10", "session_id": "2026-07-10__snap"}) + "\n",
            encoding="utf-8")
        assert gate(root, "2026-07-21")["render"] is True

    with tempfile.TemporaryDirectory() as root:
        _gate_bundle(root, "2026-07-12__demo", "2026-07-12", route="test_drive", persist=False)
        assert gate(root, "2026-07-21")["render"] is True, "demo bundles never reach coach memory"

    with tempfile.TemporaryDirectory() as root:
        # Pre-v2 history: a legacy log row with no canonical bundle still counts.
        (pathlib.Path(root) / "log.jsonl").write_text(
            json.dumps({"date_end": "2026-07-05", "headline_dim": "x"}) + "\n",
            encoding="utf-8")
        assert gate(root, "2026-07-21")["render"] is False
        assert gate(root, "2026-08-01")["render"] is True

    if os.geteuid() != 0:  # root ignores permission bits; the guard keeps CI honest
        with tempfile.TemporaryDirectory() as root:
            sessions = pathlib.Path(root) / "sessions"
            sessions.mkdir()
            sessions.chmod(0)
            try:
                verdict = gate(root, "2026-07-21")
            finally:
                sessions.chmod(0o755)
            assert verdict == {"render": True, "basis": "history_unreadable",
                               "month": "2026-07"}, \
                "unreadable history fails closed toward showing the segment"


# ─────────────── #291 route-specific question density ───────────────

def _pos(ticker, cost, start="2026-01-01"):
    return {"shares": 10, "cost": cost, "avg_cost": cost / 10, "cycle_start": start,
            "cycle_id": f"{ticker}#{start}#1", "market": "US", "currency": "USD"}


def _density_artifacts(tmp, tag, positions, thesis_questions, date_end="2026-07-14"):
    """First-review card/state with caller-chosen holdings and add questions."""
    card_path, state_path = _artifacts(tmp)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    card = json.loads(card_path.read_text(encoding="utf-8"))
    state["date_end"] = date_end
    state["holdings"]["positions"] = positions
    state["holdings"]["is_complete"] = False
    state["metrics"]["n_holdings"] = len(positions)
    card["thesis_questions"] = list(thesis_questions)
    card["ticker_diagnosis"] = []
    cp = pathlib.Path(tmp) / f"card_{tag}.json"
    sp = pathlib.Path(tmp) / f"state_{tag}.json"
    cp.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return cp, sp


def _exits_csv(tmp, tag, sells):
    """One BUY+SELL round trip per (ticker, sell_price, sell_date) → recent exits."""
    rows = ["Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency"]
    for ticker, price, date in sells:
        rows.append(f"{ticker},BUY,10,100,2026-07-01,Trade,US,USD")
        rows.append(f"{ticker},SELL,10,{price},{date},Trade,US,USD")
    path = pathlib.Path(tmp) / f"exits_{tag}.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _thesis_update(ticker, start="2026-01-01", maturity="inferred"):
    return {"ticker": ticker, "cycle_id": f"{ticker}#{start}#1",
            "why": f"{ticker} inferred entry rationale", "horizon": "quarters",
            "exit_trigger": f"{ticker} thesis is contradicted", "maturity": maturity}


def test_first_review_one_exit_still_returns_three_grounded_questions():
    """#291 acceptance: one recent exit plus two un-thesised holdings must still
    return at least three grounded questions on a first review, not just one."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        positions = {"AAA": _pos("AAA", 4000), "BBB": _pos("BBB", 3000)}
        card, state = _density_artifacts(tmp, "one_exit", positions, thesis_questions=[])
        csv = _exits_csv(tmp, "one_exit", [("SOLDX", 200, "2026-07-10")])
        run = _run("prepare", csv, "--root", root, "--language", "en", "--route", "first_review",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        assert plan["route"] == "first_review"
        queue = plan["question_queue"]
        assert len(queue) >= 3, "one exit must not collapse the review to a single question"
        assert any(q["kind"] == "revisit" and q.get("ticker") == "SOLDX" for q in queue)
        initial = [q for q in queue if q["kind"] == "initial_thesis"]
        assert len(initial) >= 1
        for q in initial:
            assert q["ticker"] in q["question"], "the stem must cite the ticker"
            assert f"{q['cost_basis']:,.0f}" in q["question"], "the stem must cite the cost-basis magnitude"
            assert {o["value"] for o in q["options"]} == \
                {"planned_entry", "momentum_follow", "external_call", "no_clear_thesis", "skip"}
        assert plan["card_plan"]["question_selection"]["shortfall_reason"] is None


def test_first_review_high_information_queue_is_bounded_and_durable():
    """#291: a high-information first review caps at five, every selected answer
    has a durable destination, and the trimmed candidates carry typed reasons."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        positions = {"ADDHI": _pos("ADDHI", 9000), "ADDLO": _pos("ADDLO", 1000),
                     "INITHI": _pos("INITHI", 8000), "INITLO": _pos("INITLO", 2000)}
        card, state = _density_artifacts(
            tmp, "hi", positions,
            thesis_questions=[{"ticker": "ADDHI"}, {"ticker": "ADDLO"}])
        csv = _exits_csv(tmp, "hi", [("EXA", 300, "2026-07-10"),
                                     ("EXB", 250, "2026-07-11"), ("EXC", 200, "2026-07-12")])
        run = _run("prepare", csv, "--root", root, "--language", "en", "--route", "first_review",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        queue = plan["question_queue"]
        assert len(queue) <= 5, "the first-review band is capped at five"
        report = plan["card_plan"]["question_selection"]
        reasons = {row["reason"] for row in report["rejected"]}
        assert "over_max_capacity" in reasons and "capture_limit" in reasons, report
        assert {"revisit", "add_thesis", "initial_thesis"} <= {q["kind"] for q in queue}, \
            "the five slots must mix a durable exit, add, and initial-thesis question"

        answers = {"session_id": plan["session_id"], "answers": [],
                   "observations": ["Agent interpretation stays separate from engine facts"],
                   "commitment": {"choice": "candidate_0"},
                   "thesis_updates": [_thesis_update(t) for t in positions]}
        for q in queue:
            if q["kind"] == "revisit":
                answers["answers"].append({"question_id": q["id"], "choice": "thesis_broken"})
            elif q["kind"] == "add_thesis":
                answers["answers"].append({"question_id": q["id"], "choice": "new_evidence",
                                           "evidence_delta": {"claim": "demand accelerated",
                                                              "source": "earnings call"}})
            elif q["kind"] == "initial_thesis":
                answers["answers"].append({"question_id": q["id"], "choice": "momentum_follow"})
            else:
                answers["answers"].append({"question_id": q["id"], "choice": "deliberate_plan"})
        a_path = pathlib.Path(tmp) / "hi-answers.json"
        n_path = pathlib.Path(tmp) / "hi-narrative.json"
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        n_path.write_text(json.dumps(_narrative("en"), ensure_ascii=False), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", a_path, "--narrative", n_path)
        assert final.returncode == 0, final.stdout + final.stderr
        bundle = json.loads((pathlib.Path(json.loads(final.stdout)["path"]) / "bundle.json")
                            .read_text(encoding="utf-8"))
        # Every selected kind reached its durable append-only destination.
        assert bundle.get("exit_narratives"), "selected exit answers must persist"
        assert bundle.get("thesis_decisions"), "selected add answers must persist"
        assert bundle.get("initial_thesis_events"), "selected initial-thesis answers must persist"


def test_weekly_review_quiet_week_backfills_exactly_one_never_zero():
    """#291: a weekly queue stays in the one-to-three band; a quiet week with a
    scored hole still yields exactly one grounded backfill question, never zero."""
    quiet_card = {"top_holes": [{"dim": "averaging_down"}], "ticker_diagnosis": []}
    quiet_state = {"holdings": {"positions": {}}, "headline_dim": "averaging_down"}
    queue, report = review_engine._question_queue(
        quiet_card, quiet_state, {}, None, "en", route="weekly_review")
    assert [q["kind"] for q in queue] == ["headline_motive"], "quiet week backfills exactly one"
    assert report["selected"] == 1 and report["shortfall_reason"] is None

    # A weekly with two add questions stays inside the one-to-three band.
    positions = {"T0": _pos("T0", 5000), "T1": _pos("T1", 4000)}
    busy_card = {"thesis_questions": [{"ticker": "T0"}, {"ticker": "T1"}],
                 "ticker_diagnosis": [], "top_holes": [{"dim": "averaging_down"}]}
    busy_state = {"holdings": {"positions": positions}, "headline_dim": "averaging_down"}
    busy_queue, busy_report = review_engine._question_queue(
        busy_card, busy_state, {}, None, "en", route="weekly_review")
    assert 1 <= len(busy_queue) <= 3 and busy_report["selected"] == 2


def test_initial_thesis_dedup_skips_a_position_with_an_existing_thesis():
    """#291: a holding that already carries a real (testable) thesis is not asked
    an entry-thesis question, and the selection report records why."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True)
        seed = {"cycle_id": "TESTED#2026-01-01#1", "ticker": "TESTED",
                "why": "seeded durable thesis", "exit_trigger": "seeded falsifier",
                "maturity": "testable", "status": "open", "position_status": "open",
                "schema_version": 2, "session_id": "2026-06-01__seed", "session_date": "2026-06-01"}
        (root / "theses.jsonl").write_text(json.dumps(seed, ensure_ascii=False) + "\n", encoding="utf-8")
        positions = {"TESTED": _pos("TESTED", 6000), "AAA": _pos("AAA", 3000)}
        card, state = _density_artifacts(tmp, "dedup", positions, thesis_questions=[])
        run = _run("prepare", "--root", root, "--language", "en", "--route", "first_review",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        assert "TESTED" not in {q.get("ticker") for q in plan["question_queue"]}, \
            "a position with an existing thesis must not be asked an entry-thesis question"
        rejected = plan["card_plan"]["question_selection"]["rejected"]
        assert {"id": review_engine._initial_thesis_id("TESTED#2026-01-01#1"),
                "kind": "initial_thesis", "cycle_id": "TESTED#2026-01-01#1",
                "reason": "has_existing_thesis"} in rejected
        # P2-B: every rejected entry carries a uniform shape with a join key.
        assert all(set(row) == {"id", "kind", "cycle_id", "reason"} for row in rejected)
        assert any(q["kind"] == "initial_thesis" and q.get("ticker") == "AAA"
                   for q in plan["question_queue"]), "the un-thesised holding is still asked"


def test_initial_thesis_consumption_maturity_gate_and_idempotency():
    """#291: planned_entry forces a real captured thesis; other answers keep the
    inferred record legal; the classification projects; finalize stays idempotent."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        positions = {"AAA": _pos("AAA", 5000), "BBB": _pos("BBB", 4000)}
        card, state = _density_artifacts(tmp, "consume", positions, thesis_questions=[])
        run = _run("prepare", "--root", root, "--language", "en", "--route", "first_review",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        aaa_q = next(q for q in plan["question_queue"]
                     if q["kind"] == "initial_thesis" and q["ticker"] == "AAA")
        bbb_q = next(q for q in plan["question_queue"]
                     if q["kind"] == "initial_thesis" and q["ticker"] == "BBB")

        base = {"session_id": plan["session_id"], "observations": [],
                "commitment": {"choice": "candidate_0"}}
        base["answers"] = [{"question_id": aaa_q["id"], "choice": "planned_entry"},
                           {"question_id": bbb_q["id"], "choice": "no_clear_thesis"}]
        for q in plan["question_queue"]:
            if q["kind"] not in ("initial_thesis",):
                base["answers"].append({"question_id": q["id"], "choice": "deliberate_plan"})
        n_path = pathlib.Path(tmp) / "c-narrative.json"
        n_path.write_text(json.dumps(_narrative("en"), ensure_ascii=False), encoding="utf-8")

        # planned_entry with a silently-inferred thesis is rejected.
        bad = json.loads(json.dumps(base))
        bad["thesis_updates"] = [_thesis_update("AAA", maturity="inferred"),
                                 _thesis_update("BBB", maturity="inferred")]
        bad_path = pathlib.Path(tmp) / "c-bad.json"
        bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        rejected = _run("preview", "--root", root, "--session-id", plan["session_id"],
                        "--answers", bad_path, "--narrative", n_path)
        assert rejected.returncode == 2 and "planned_entry" in json.loads(rejected.stdout)["error"]

        # A real captured thesis for the planned_entry cycle passes; the
        # no_clear_thesis cycle stays honestly inferred.
        good = json.loads(json.dumps(base))
        good["thesis_updates"] = [_thesis_update("AAA", maturity="testable"),
                                  _thesis_update("BBB", maturity="inferred")]
        good_path = pathlib.Path(tmp) / "c-good.json"
        good_path.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", good_path, "--narrative", n_path)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        assert not result["projection_error"], final.stdout
        bundle = json.loads((pathlib.Path(result["path"]) / "bundle.json").read_text(encoding="utf-8"))
        events = {e["ticker"]: e["choice"] for e in bundle["initial_thesis_events"]}
        assert events == {"AAA": "planned_entry", "BBB": "no_clear_thesis"}
        projected = [json.loads(line) for line in
                     (root / "initial_theses.jsonl").read_text(encoding="utf-8").splitlines()]
        assert {r["ticker"] for r in projected} == {"AAA", "BBB"}, "the classification projects to its own log"

        # Idempotent finalize retry writes nothing new.
        retry = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                     "--answers", good_path, "--narrative", n_path)
        assert retry.returncode == 0 and json.loads(retry.stdout)["status"] in ("committed", "no-op")
        again = [json.loads(line) for line in
                 (root / "initial_theses.jsonl").read_text(encoding="utf-8").splitlines()]
        assert again == projected, "an idempotent finalize retry must not duplicate rows"


def test_question_density_matrix_selects_expected_counts_per_route():
    """#291: candidate counts of 1, 3, and 5 resolve to the per-route band —
    first review floors at three (backfilling), weekly caps at three."""
    def selected(k, route):
        positions = {f"T{i}": {"cycle_id": f"T{i}#2026-01-01#1", "cost": 1000 * (k - i)}
                     for i in range(k)}
        card = {"thesis_questions": [{"ticker": f"T{i}"} for i in range(k)],
                "ticker_diagnosis": [], "top_holes": [{"dim": "averaging_down"}]}
        state = {"holdings": {"positions": positions}, "headline_dim": "averaging_down"}
        return review_engine._question_queue(card, state, {}, None, "en", route=route)

    _q, r1 = selected(1, "first_review")
    assert r1["selected"] == 2 and r1["shortfall_reason"] == "insufficient_eligible_candidates", \
        "one candidate plus the single grounded backfill still cannot reach the floor of three"
    _q, r3 = selected(3, "first_review")
    assert r3["selected"] == 3 and r3["shortfall_reason"] is None
    _q, r5 = selected(5, "first_review")
    assert r5["selected"] == 5 and r5["eligible"] == 5

    _q, w1 = selected(1, "weekly_review")
    assert w1["selected"] == 1
    _q, w3 = selected(3, "weekly_review")
    assert w3["selected"] == 3
    q5, w5 = selected(5, "weekly_review")
    assert w5["selected"] == 3 and w5["eligible"] == 5
    assert sum(1 for row in w5["rejected"] if row["reason"] == "over_max_capacity") == 2


def test_first_review_grounded_refill_beats_generic_backfill():
    """#291 P2-A: below the route min, a suppressed grounded initial-thesis
    candidate refills the queue before the generic motive backfill is used —
    an extra slot earns its place through durable information gain."""
    def first_review_holdings(n):
        positions = {f"H{i}": _pos(f"H{i}", 9000 - 1000 * i) for i in range(n)}
        missing = [{"ticker": f"H{i}", "cycle_id": f"H{i}#2026-01-01#1"} for i in range(n)]
        card = {"thesis_questions": [], "ticker_diagnosis": [],
                "top_holes": [{"dim": "averaging_down"}]}
        state = {"holdings": {"positions": positions}, "headline_dim": "averaging_down"}
        return review_engine._question_queue(card, state, {}, None, "en",
                                             route="first_review", missing_thesis_positions=missing)

    # Three un-thesised holdings fill the floor of three with grounded questions;
    # the generic motive never appears even though a hole dimension is available.
    q3, r3 = first_review_holdings(3)
    assert [x["kind"] for x in q3] == ["initial_thesis"] * 3, "grounded refill, not a generic motive"
    assert not any(x["kind"] == "headline_motive" for x in q3)
    assert r3["selected"] == 3 and r3["shortfall_reason"] is None and r3["rejected"] == []

    # A fourth holding still caps the queue at the floor; the one unused grounded
    # row is the only over-limit trim and no generic motive is fabricated.
    q4, r4 = first_review_holdings(4)
    assert [x["kind"] for x in q4] == ["initial_thesis"] * 3
    assert not any(x["kind"] == "headline_motive" for x in q4)
    trims = [x for x in r4["rejected"] if x["reason"] == "initial_thesis_limit"]
    assert len(trims) == 1 and trims[0]["cycle_id"] == "H3#2026-01-01#1"


def test_add_thesis_already_captured_rejection_carries_join_keys():
    """#291 P2-B: an already-captured add dedup records the same question id the
    row would have used, plus the cycle_id, so QA joins never silently miss."""
    cycle_id = "NVDA#2026-06-01#1"
    cursor = cycle_id + "#add#2"
    positions = {"NVDA": {"cycle_id": cycle_id, "cost": 5000, "decision_cursor": cursor}}
    card = {"thesis_questions": [{"ticker": "NVDA"}], "ticker_diagnosis": [],
            "top_holes": [{"dim": "averaging_down"}]}
    state = {"holdings": {"positions": positions}, "headline_dim": "averaging_down"}
    active = {cycle_id: {"decision_cursor": cursor, "maturity": "testable"}}
    queue, report = review_engine._question_queue(card, state, active, None, "en", route="weekly_review")
    assert all(q["kind"] != "add_thesis" for q in queue), "the captured add is deduped away"
    dedup = [r for r in report["rejected"] if r["reason"] == "already_captured"]
    assert len(dedup) == 1 and dedup[0]["kind"] == "add_thesis" and dedup[0]["cycle_id"] == cycle_id
    assert dedup[0]["id"] == "add_" + hashlib.sha256(cursor.encode("utf-8")).hexdigest()[:12], \
        "the rejection id matches the add question's own id derivation"
    assert set(dedup[0]) == {"id", "kind", "cycle_id", "reason"}


def test_set_cap_persists_override_and_engine_plumbing_reads_it():
    """#324: `review.py set-cap` writes a validated single-position override to
    profile.json (fail-closed on out-of-range); the engine plumbing reads it back
    and the prepared sizing candidate rule interpolates the user's number."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        run = _run("set-cap", "--root", root, "--pct", "0.30")
        assert run.returncode == 0, run.stdout + run.stderr
        assert json.loads(run.stdout)["max_position_pct"] == 0.30
        assert json.loads(pathlib.Path(root, "profile.json").read_text())["max_position_pct"] == 0.30
        assert review_engine._position_cap_override(root) == 0.30, "engine reader agrees with the store"
        # Out-of-range is rejected and must not corrupt the stored value.
        bad = _run("set-cap", "--root", root, "--pct", "1.5")
        assert bad.returncode == 2 and json.loads(bad.stdout)["status"] == "error", bad.stdout
        assert review_engine._position_cap_override(root) == 0.30, "a rejected write keeps the prior cap"
        # The sizing candidate rule interpolates the override (state → localized_rule).
        card = {"candidate_rules": [], "ticker_diagnosis": [],
                "top_holes": [{"dim": "部位 sizing", "lens_rule": "fallback"}]}
        state = {"metrics": {"max_pos_pct": 0.42}, "max_position_pct": 0.30}
        sizing = [r for r in review_engine._candidate_rules(card, state, "en")
                  if r["dim"] == "position_sizing"]
        assert sizing and "30%" in sizing[0]["rule"], \
            "candidate sizing rule must carry the user's cap, not the 20% default"
        # Clear reverts to the universal default.
        cleared = _run("set-cap", "--root", root, "--clear")
        assert cleared.returncode == 0 and json.loads(cleared.stdout)["status"] == "cleared"
        assert review_engine._position_cap_override(root) is None, "clear falls back to the universal default"


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
