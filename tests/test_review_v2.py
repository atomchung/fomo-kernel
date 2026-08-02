#!/usr/bin/env python3
"""Skill v2 orchestration / ETF / recovery tests (offline, standard library only)."""
import concurrent.futures
import hashlib
import datetime as dt
import json
import os
import pathlib
import re
import shutil
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

# The market must not be an input to these assertions (#620). Declared in
# tests/offline_posture.py so a direct `python3 tests/<this file>` run and a
# `run_all.py` run reach the same answer; TR_TEST_NETWORK=1 still opts in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()
import book_refresh as book_refresh_engine  # noqa: E402
import card_renderer  # noqa: E402
import instruments  # noqa: E402
import ledger as ledger_engine  # noqa: E402
import problems as problems_engine  # noqa: E402
import review as review_engine  # noqa: E402
import session as session_engine  # noqa: E402
import snapshot_adapter  # noqa: E402
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
        "holdings": {"as_of": "2026-07-14", "derived_from": "trades_csv",
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


def _run_finalize(*args, env=None):
    """Drive the two-step commit the product actually requires (#628).

    `finalize` refuses to commit artifacts this session's own `preview` never
    rendered, so every finalize in this suite runs that preview first, against
    the same answers and narrative it is about to commit. This is the real
    lifecycle rather than a way around the gate: `preview` takes the identical
    flags, and the `commitment` these answers may already carry is the one
    field the receipt key ignores — which is why one answers file serves both
    calls, exactly as `SKILL.md`'s lifecycle describes.

    The preview's own result is deliberately not asserted, for two reasons that
    both matter. Several tests here drive `finalize` with artifacts it must
    reject on its own, and *that rejection is the assertion* — a preview
    failing for the same reason must not replace it with a complaint about the
    preview, and `finalize` still performs its complete independent validation
    either way. Second, an idempotent finalize retry runs after the pending
    directory is gone, so its preview cannot succeed and does not need to: the
    already-committed session skips the gate by design.
    """
    _run("preview", *args, env=env)
    return _run("finalize", *args, env=env)


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
        # #771: this fixture has both avg_cost and market_value on every
        # position (basis "market_value"), so unrealized P&L and the
        # per-position money ranking are both genuinely supportable and must
        # reach the card rather than being zeroed regardless of what the
        # snapshot actually supports.
        assert card["overview"] == {
            "unrealized": 1460.0,
            "unrealized_coverage": {"held_n": 3, "priced_n": 3, "unpriced": []},
        }
        assert [row["ticker"] for row in card["ticker_diagnosis"]] == \
            ["2330.TW", "QQQ", "SPY"], "ranked by |impact| descending, same as the trade lane"
        impacts = {row["ticker"]: row["impact"] for row in card["ticker_diagnosis"]}
        assert impacts == {"2330.TW": 1320.0, "QQQ": 100.0, "SPY": 40.0}
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
    # The unlock marker is the catalog entry itself, not a fragment of its
    # wording (#623). This test asserts which branch rendered and how often --
    # a wording pin here made a copy edit look like a branching regression, and
    # `tests/copy_corpus.py`'s golden is the surface that owns wording.
    markers = {
        "en": ("are out of scope for this position-snapshot review",
               card_renderer.load_copy("en")["block_missing"]["snapshot_unlock"],
               "Import transaction history later"),
        "zh-TW": ("不在這次持倉快照的評分範圍內",
                  card_renderer.load_copy("zh-TW")["block_missing"]["snapshot_unlock"],
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


def test_snapshot_finalize_preserves_the_five_previously_zeroed_facts():
    """#771 regression: cash, what_if, ticker_diagnosis, overview.unrealized
    and strength were unconditionally zeroed by the adapter regardless of
    what the snapshot actually supported. This asserts each is populated
    right out of `prepare` on a fixture that genuinely supports it, and that
    a full finalize of the session does not re-zero any of them -- so a
    later refactor of either path cannot silently regress this."""
    payload = {
        "as_of": "2026-07-16",
        "positions": [
            {"ticker": "SPY", "shares": 2, "avg_cost": 600, "market_value": 1240,
             "market": "US", "currency": "USD"},
            {"ticker": "QQQ", "shares": 10, "avg_cost": 500, "market_value": 5100,
             "market": "US", "currency": "USD"},
            {"ticker": "2330.TW", "shares": 1000, "avg_cost": 1000,
             "market_value": 1040000, "market": "TW", "currency": "TWD"},
        ],
        "cash": {"USD": 500},
        "fx": {"USD": 1, "TWD": 0.033},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload, language="en")
        card = plan["engine_card"]
        assert card["cash"]["balance"] == 500.0, card["cash"]
        assert card["what_if"]["scenario"] == {"kind": "single_ticker", "ticker": "2330.TW"}
        assert [row["ticker"] for row in card["ticker_diagnosis"]] == \
            ["2330.TW", "QQQ", "SPY"]
        assert card["overview"]["unrealized"] == 1460.0
        # 2330.TW alone is 84% of this three-position book -- nothing here
        # supports a positive sizing/diversification claim, and `strength`
        # staying `None` is the honest answer, not a gap.
        assert card["strength"] is None

        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(_snapshot_answers(plan, commitment="skip")), encoding="utf-8")
        narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False), encoding="utf-8")
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr

        bundle = json.loads(
            (root / "sessions" / plan["session_id"] / "bundle.json").read_text()
        )
        finalized_card = bundle["engine_card"]
        assert finalized_card["cash"] == card["cash"]
        assert finalized_card["what_if"] == card["what_if"]
        assert finalized_card["ticker_diagnosis"] == card["ticker_diagnosis"]
        assert finalized_card["overview"] == card["overview"]
        assert bundle["engine_state"]["cash"] == card["cash"]

        # The delivered private card exists on disk. Its *content* does not
        # yet include these five facts: card_renderer._card_facts's
        # route == "snapshot_review" gate and the `if snapshot:` branches in
        # _performance_block/_risks_block bypass card["cash"]/what_if/
        # ticker_diagnosis/strength/overview["unrealized"] independently of
        # what this adapter supplies (confirmed by injecting fully-populated
        # values into a snapshot bundle and finding no change in
        # render_private/render_html output). That renderer wiring is a
        # separate change from this adapter fix; what this test locks is
        # that the engine artifact a renderer would read from stops being
        # zeroed regardless of what the snapshot supports.
        card_path = root / "sessions" / plan["session_id"] / "card-private.md"
        assert card_path.exists() and card_path.read_text().strip()


def test_a_view_covering_part_of_an_account_records_the_book_like_any_other():
    """#549. This test used to assert the opposite: a user who told the agent
    their screenshot covered one brokerage of several got `is_complete:false`,
    the review still ran, and the ledger projection was *visibly skipped* --
    their recorded book silently stopped moving. The flag is gone from the
    input contract entirely, so this declaration records the book like any
    other, and the ledger row is written."""
    payload = {
        "as_of": "2026-07-16",
        "positions": [{"ticker": "PLTR", "shares": 5, "avg_cost": 100,
                       "market": "US", "currency": "USD"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _path = _snapshot_prepare(tmp, root, payload=payload, language="en")
        # A single PLTR position with a known avg_cost has every fact a weight
        # needs, so the sizing/diversification dimensions populate.
        assert plan["input"]["ledger_ingest"] == {
            "mode": "finalize_projection", "kind": "positions_snapshot",
        }
        assert plan["engine_card"]["snapshot_summary"]["weights_available"] is True
        assert "weights_unavailable_reason" not in plan["engine_card"]["data_integrity"]
        assert [row["dim"] for row in plan["engine_card"]["dims_raw"]] == ["部位 sizing", "分散"]
        assert [hole["dim"] for hole in plan["engine_card"]["top_holes"]] == ["部位 sizing", "分散"]
        assert plan["engine_state"]["metrics"]["max_pos_pct"] == 1.0
        assert plan["engine_state"]["metrics"]["max_pos_ticker"] == "PLTR"
        answers = pathlib.Path(tmp) / "answers-incomplete.json"
        narrative = pathlib.Path(tmp) / "narrative-incomplete.json"
        answers.write_text(json.dumps(_snapshot_answers(plan, commitment="skip")), encoding="utf-8")
        narrative.write_text(json.dumps(_snapshot_narrative(plan), ensure_ascii=False), encoding="utf-8")
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        assert result["projection_error"] is None
        assert result["projection"]["rows"][0]["status"] == "projected"
        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == ["snapshot"]
        assert rows[0]["positions"][0]["ticker"] == "PLTR"
        assert "is_complete" not in rows[0]
        bundle = json.loads(
            (root / "sessions" / plan["session_id"] / "bundle.json").read_text()
        )
        inferred = bundle["thesis_updates"][0]
        assert inferred["cycle_provenance"] == {
            "kind": "snapshot_inference",
            "snapshot_as_of": "2026-07-16",
        }
        repaired = _run("repair-projections", "--root", root)
        assert repaired.returncode == 0 and _ledger_rows(root) == rows


def test_snapshot_thesis_relinks_to_earlier_visible_cycle_and_persists():
    """#549 widened this from incomplete snapshots to every snapshot-inferred
    cycle. The completeness flag was never the reason the cycle start was
    provisional -- a declaration says what is held, never since when -- and it
    is gone, so the narrow fail-closed conditions below are what holds."""
    payload = {
        "as_of": "2026-07-16",
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
        committed = _run_finalize(
            "--root", root, "--session-id", opening["session_id"],
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
            "as_of": "2026-07-18", "derived_from": "trades_csv",
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
            "prepare", "--root", root, "--language", "en",
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
            "kind": "snapshot_cycle_relink",
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
        finalized = _run_finalize(
            "--root", root, "--session-id", plan["session_id"],
            "--answers", answers_path, "--narrative", narrative_path,
        )
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        later_bundle = json.loads(
            (root / "sessions" / plan["session_id"] / "bundle.json").read_text()
        )
        assert later_bundle["thesis_updates"] == [relink]

        replay = _run(
            "prepare", "--root", root, "--language", "en",
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


def test_snapshot_thesis_relink_fails_closed_for_reopened_or_ambiguous_ticker():
    prior = {
        "ticker": "PLTR", "cycle_id": "PLTR#2026-07-16#1",
        "thesis_id": "thesis-opening", "event_id": "event-opening",
        "last_event_id": "event-opening", "why": "inferred role",
        "exit_trigger": "role breaks", "maturity": "inferred",
        "source_confidence": "candidate", "origin": "snapshot",
        "position_status": "open",
        "cycle_provenance": {
            "kind": "snapshot_inference", "snapshot_as_of": "2026-07-16",
        },
    }
    reopened = {"PLTR": {
        "cycle_id": "PLTR#2026-07-17#2", "cycle_start": "2026-07-17", "shares": 1,
    }}
    assert thesis_engine.build_snapshot_cycle_relinks(
        [prior], reopened, "session-reopened", "2026-07-18"
    ) == [], "a post-snapshot cycle may be a close/reopen and must receive a new thesis"

    earlier = {"PLTR": {
        "cycle_id": "PLTR#2026-07-01#1", "cycle_start": "2026-07-01", "shares": 1,
    }}
    ambiguous = {**prior, "cycle_id": "PLTR#2026-07-15#9",
                 "event_id": "event-other", "last_event_id": "event-other"}
    assert thesis_engine.build_snapshot_cycle_relinks(
        [prior, ambiguous], earlier, "session-ambiguous", "2026-07-18"
    ) == [], "ticker-only matching must not choose between two open snapshot candidates"


def test_weights_unavailable_reason_reports_incomplete_valuation_or_fx_gap():
    """snapshot_adapter.prepare's weights_unavailable_reason had zero coverage.
    #485 collapses it to the two causes _global_values can actually produce: a
    missing price/cost fact (native_values empty -> "incomplete_valuation")
    and a missing FX fact for a held currency (fx_gaps non-empty ->
    "fx_gap")."""
    with tempfile.TemporaryDirectory() as tmp:
        missing_valuation = _snapshot_json(tmp, payload={
            "as_of": "2026-07-16",
            "positions": [{"ticker": "PLTR", "shares": 5,
                           "market": "US", "currency": "USD"}],
        }, name="missing-valuation.json")
        card, _state, _meta = snapshot_adapter.prepare(str(missing_valuation))
        assert card["snapshot_summary"]["weights_available"] is False
        assert card["data_integrity"]["weights_unavailable_reason"] == "incomplete_valuation"

        missing_fx = _snapshot_json(tmp, payload={
            "as_of": "2026-07-16",
            "positions": [
                {"ticker": "PLTR", "shares": 5, "avg_cost": 100,
                 "market": "US", "currency": "USD"},
                {"ticker": "2330.TW", "shares": 100, "avg_cost": 500,
                 "market": "TW", "currency": "TWD"},
            ],
            "fx": {"USD": 1},
        }, name="missing-fx.json")
        card, _state, _meta = snapshot_adapter.prepare(str(missing_fx))
        assert card["snapshot_summary"]["weights_available"] is False
        assert card["data_integrity"]["weights_unavailable_reason"] == "fx_gap"


def test_weights_unavailable_reason_always_names_the_missing_fact():
    """Regression guard for #485, kept alive after #549 removed the flag it was
    written about. Before #485's fix, `is_complete: false` alone forced
    weights_available to False inside `_global_values`, and the reason
    derivation checked it before basis/fx -- so a declaration that was both
    marked partial AND missing a real price or FX fact was mislabeled
    "incomplete_snapshot" instead of naming the fact that was actually absent.
    #549 then removed the flag from the envelope entirely, which is why the
    fixtures below no longer carry it; what still needs a guard is the rule it
    exposed: a suppressed weight names the fact it is missing, and a book whose
    facts are all present is scored."""
    with tempfile.TemporaryDirectory() as tmp:
        missing_valuation = _snapshot_json(tmp, payload={
            "as_of": "2026-07-16",
            "positions": [{"ticker": "PLTR", "shares": 5,
                           "market": "US", "currency": "USD"}],
        }, name="missing-valuation.json")
        card, _state, _meta = snapshot_adapter.prepare(str(missing_valuation))
        assert card["snapshot_summary"]["weights_available"] is False
        reason = card["data_integrity"]["weights_unavailable_reason"]
        assert reason == "incomplete_valuation"
        assert reason != "incomplete_snapshot"

        missing_fx = _snapshot_json(tmp, payload={
            "as_of": "2026-07-16",
            "positions": [
                {"ticker": "PLTR", "shares": 5, "avg_cost": 100,
                 "market": "US", "currency": "USD"},
                {"ticker": "2330.TW", "shares": 100, "avg_cost": 500,
                 "market": "TW", "currency": "TWD"},
            ],
            "fx": {"USD": 1},
        }, name="missing-fx.json")
        card, _state, _meta = snapshot_adapter.prepare(str(missing_fx))
        assert card["snapshot_summary"]["weights_available"] is False
        reason = card["data_integrity"]["weights_unavailable_reason"]
        assert reason == "fx_gap"
        assert reason != "incomplete_snapshot"

        fact_complete = _snapshot_json(tmp, payload={
            "as_of": "2026-07-16",
            "positions": [{"ticker": "PLTR", "shares": 5, "avg_cost": 100,
                           "market": "US", "currency": "USD"}],
        }, name="fact-complete.json")
        card, _state, _meta = snapshot_adapter.prepare(str(fact_complete))
        assert card["snapshot_summary"]["weights_available"] is True
        assert "weights_unavailable_reason" not in card["data_integrity"]


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
        # The catalog entry, not a fragment of its wording (#623): this asserts
        # the at-most-one invitation rule, and copy_corpus's golden owns the
        # sentence.
        assert private.count(
            card_renderer.load_copy("en")["block_missing"]["snapshot_unlock"]) == 1
        assert "Total P&L" not in private and "Best:" not in private and "Worst:" not in private
        assert "opening portfolio check" in public.lower()
        assert "behavioral pressure" not in public and "highlighted behavior" not in public
        for secret in ("SPY", "PLTR", "2026-07-16", plan["session_id"],
                       "The supplied positions show structure"):
            assert secret not in public, secret
        assert not (root / "ledger.jsonl").exists(), "preview cannot project accounting facts"

        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        assert result["status"] == "committed" and result["projection_error"] is None
        bundle = json.loads((root / "sessions" / plan["session_id"] / "bundle.json").read_text())
        assert all(row["origin"] == "snapshot" for row in bundle["thesis_updates"])
        rows = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert [row["type"] for row in rows] == ["snapshot"]
        assert rows[0]["snapshot_id"].startswith("snapshot-")

        repeated = _run_finalize("--root", root, "--session-id", plan["session_id"],
                        "--answers", answers, "--narrative", narrative)
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        rows2 = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        assert rows2 == rows, "an identical finalize retry must not append a second anchor"

        same_prepare = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                            _path, "--root", root, "--language", "en")
        assert same_prepare.returncode == 0
        assert json.loads(same_prepare.stdout)["status"] == "already_committed"
        # A different second declaration is routed to the book-update lane
        # (#530): it carries facts the record does not have, and this lane
        # never asks what happened to them. Nothing is written and no pending
        # session opens, so it cannot reach a preview or a card.
        changed_payload = {**payload, "positions": [
            {**payload["positions"][0], "shares": 3}, payload["positions"][1]]}
        changed = _snapshot_json(tmp, payload=changed_payload, name="changed-snapshot.json")
        second = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                      changed, "--root", root, "--language", "en")
        assert second.returncode == 2, second.stdout + second.stderr
        assert "refresh --snapshot-json" in json.loads(second.stdout)["error"], second.stdout
        assert [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()] == rows, \
            "a refused declaration writes nothing to the ledger"
        assert not [entry for entry in (root / ".pending").glob("*")
                    if entry.is_dir()], "a refused declaration opens no pending session"

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
    return _run_finalize("--root", root, "--session-id", plan["session_id"],
                "--answers", answers, "--narrative", narrative)


def _ledger_rows(root):
    return [json.loads(line)
            for line in (pathlib.Path(root) / "ledger.jsonl").read_text().splitlines()]


def _refresh(root, snapshot_path, answers=None):
    """Drive the book-update lane's CLI, the way flows/book-refresh.md does."""
    argv = ["refresh", "--root", root, "--snapshot-json", snapshot_path]
    if answers is not None:
        argv += ["--answers", json.dumps(answers)]
    run = _run(*argv)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def test_a_differing_second_declaration_is_recorded_first_then_reviewed():
    """#530: updating the book is a mandatory node, not a fork the agent picks.

    The review lane used to accept a differing declaration and adopt it at
    finalize, which is how a position that vanished from the new view could
    leave the book with nobody ever asked whether it was sold -- the review lane
    passes no ``absences`` because it asks no question that could produce one.
    So the order is now mechanical: prepare refuses and names ``refresh``,
    ``refresh`` records (asking about what changed), and only then does the same
    declaration reconcile clean and get its review.

    The adjustment/anchor/ordering half of the old #220 assertions still runs
    here; it has simply moved behind the lane that owns it."""
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
        second_path = _snapshot_json(tmp, payload=second_payload, name="second.json")
        before = _ledger_rows(root)
        refused = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                       second_path, "--root", root, "--language", "en")
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert json.loads(refused.stdout)["error"] == book_refresh_engine.NEEDS_BOOK_UPDATE
        assert _ledger_rows(root) == before, "the refusal happens before any append"
        assert not [entry for entry in (root / ".pending").glob("*") if entry.is_dir()], \
            "a refused declaration cannot reach preview, so it cannot reach a card"

        # The book-update lane: the same facts, recorded rather than discussed.
        # PLTR moved 25 -> 30 on a position worth 40% of the recorded book, so
        # this lane spends its one question on it; the cash change is adopted
        # without ceremony.
        receipt = _refresh(str(root), str(second_path))
        assert receipt["status"] == "pending_confirmation"
        assert [(row["kind"], row["ticker"]) for row in receipt["pending_confirmations"]] == \
            [("large_change", "PLTR")]
        # Facts only, in the declared as-of window: the 2026-07-12 buy counts
        # (derived 25), the 2026-07-16 buy does not (SPY stays clean).
        assert receipt["diff"]["positions"] == [
            {"ticker": "PLTR", "kind": "shares", "derived": 25.0, "declared": 30.0}]
        assert receipt["diff"]["cash"] == [
            {"currency": "USD", "derived": 1000.0, "declared": 800.0}]
        assert receipt["against"]["as_of"] == "2026-07-10"

        adopted = _refresh(str(root), str(second_path),
                           {"refresh_id": receipt["refresh_id"],
                            "answers": [{"ticker": "PLTR", "classification": "confirmed"}]})
        assert adopted["status"] == "adopted" and adopted["reconciliation"] == "adjusted"

        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == \
            ["snapshot", "trade", "trade", "adjustment", "snapshot"], \
            "history is preserved: old anchor and trades stay, adjustment precedes the new anchor"
        adjustment = rows[3]
        assert adjustment["adjustment_id"].startswith("adjust-")
        assert adjustment["reason"] == "snapshot_reconciliation"
        assert adjustment["diff"] == receipt["diff"]
        assert adjustment["against"]["as_of"] == "2026-07-10"
        assert rows[4]["snapshot_id"].startswith("snapshot-")
        assert rows[4]["projection_sequence"] == 2

        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        assert ledger_engine.latest_anchor(events)["as_of"] == "2026-07-15"
        derived = ledger_engine.derive_holdings(events)["holdings"]
        assert derived["PLTR"]["shares"] == 30, "holdings derive from the adopted anchor"
        assert derived["SPY"]["shares"] == 3, "post-adoption trades still apply on top"
        # #539: adopting a newer view of a position that was never sold does not
        # restart it. PLTR was on the books on 2026-07-10 and was added to on the
        # 12th; the cycle the user's thesis is written against is still that one,
        # so the declaration's own date does not become its start.
        assert derived["PLTR"]["cycle_id"] == "PLTR#2026-07-10#1"
        assert derived["SPY"]["cycle_id"] == "SPY#2026-07-10#1"

        # ...and only now is there a review. The same declaration that was
        # refused above reconciles clean against the book it just updated, so it
        # produces its card with no extra ceremony and marks the ledger without
        # a second anchor.
        plan2, _again = _snapshot_prepare(tmp, root, payload=second_payload, name="second.json")
        frozen = plan2["engine_state"]["snapshot_reconciliation"]
        assert frozen["status"] == "reconciled"
        assert frozen["diff"] == {"positions": [], "cash": []}
        assert plan2["input"]["ledger_ingest"]["reconciliation"] == "reconciled"
        review = _finalize_snapshot_session(tmp, root, plan2, "second")
        assert review.returncode == 0, review.stdout + review.stderr
        assert [row["type"] for row in _ledger_rows(root)] == \
            ["snapshot", "trade", "trade", "adjustment", "snapshot", "reconciliation"]

        retry = _finalize_snapshot_session(tmp, root, plan2, "second-retry")
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert json.loads(retry.stdout)["status"] == "no-op"
        assert [row["type"] for row in _ledger_rows(root)] == \
            ["snapshot", "trade", "trade", "adjustment", "snapshot", "reconciliation"], \
            "an identical finalize replay appends neither a second mark nor a second anchor"


def test_a_routine_adoption_does_not_reask_a_captured_add_decision():
    """#660: the real prepare route reads the carried cursor after adoption.

    An opening holdings review records the current cycle and its thesis.  A
    normal CSV review then records two canonical adds and captures the second
    add's reason.  When the user later refreshes an otherwise unchanged
    holdings view, the next real ``review.py prepare`` must join that captured
    reason to the same cursor, rather than treating the routine declaration as
    a fresh zero-add anchor and asking the same question again.
    """
    initial = {
        "as_of": "2026-06-30",
        "positions": [{"ticker": "PLTR", "shares": 100, "avg_cost": 12,
                       "market": "US", "currency": "USD"}],
    }
    refreshed = {
        "as_of": "2026-07-15",
        "positions": [{"ticker": "PLTR", "shares": 120, "avg_cost": 12,
                       "market": "US", "currency": "USD"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        opening, _path = _snapshot_prepare(tmp, root, payload=initial, name="opening.json")
        assert _finalize_snapshot_session(tmp, root, opening, "opening").returncode == 0

        # Capture the existing add decision through review.py's two-step
        # lifecycle.  The compact fixture supplies only presentation artifacts;
        # the existing declared ledger is still the source for the overlaid
        # holding, its cycle, and its count.  This keeps the test focused on
        # continuity rather than market-data rendering.
        card, state = _artifacts(tmp)
        trades = pathlib.Path(tmp) / "post-anchor-adds.csv"
        trades.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "PLTR,BUY,10,11,2026-07-02,Trade,US,USD\n"
            "PLTR,BUY,10,10,2026-07-03,Trade,US,USD\n",
            encoding="utf-8")
        recorded = _run(
            "prepare", trades, "--root", root, "--language", "en", "--card-json", card,
            "--state-json", state, "--session-nonce", "capture-add")
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr
        add_plan = _pending_plan(root, recorded.stdout)
        add_questions = [q for q in add_plan["question_queue"] if q["kind"] == "add_thesis"]
        assert add_questions
        add_question = add_questions[0]
        assert add_question["decision_cursor"] == "PLTR#2026-06-30#1#add#2"

        answers = _answers(add_plan, commitment="skip")
        answers["thesis_updates"] = []
        answers_path = pathlib.Path(tmp) / "captured-add-answers.json"
        narrative_path = pathlib.Path(tmp) / "captured-add-narrative.json"
        answers_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        narrative_path.write_text(json.dumps(_narrative_for_plan(add_plan), ensure_ascii=False),
                                  encoding="utf-8")
        captured = _run_finalize("--root", root, "--session-id", add_plan["session_id"],
                                 "--answers", answers_path, "--narrative", narrative_path)
        assert captured.returncode == 0, captured.stdout + captured.stderr

        before_refresh, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        held_before = ledger_engine.derive_holdings(before_refresh)["holdings"]["PLTR"]
        assert held_before["add_count"] == 2
        assert held_before["decision_cursor"] == add_question["decision_cursor"]

        snapshot_path = _snapshot_json(tmp, payload=refreshed, name="routine-refresh.json")
        receipt = _refresh(str(root), str(snapshot_path))
        assert receipt["status"] == "ready" and receipt["pending_confirmations"] == []
        adopted = _refresh(str(root), str(snapshot_path),
                           {"refresh_id": receipt["refresh_id"], "answers": []})
        assert adopted["status"] == "adopted"

        # This final command is the production snapshot-review entry point,
        # not a helper-level question builder.  Its canonical reader sees the
        # retained count and rebuilds exactly the old cursor.
        prepared = _run("prepare", "--route", "snapshot_review", "--snapshot-json", snapshot_path,
                        "--root", root, "--language", "en")
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        after = _pending_plan(root, prepared.stdout)
        after_position = after["engine_state"]["holdings"]["positions"]["PLTR"]
        assert after_position["add_count"] == 2
        assert after_position["decision_cursor"] == add_question["decision_cursor"]
        assert not any(q["kind"] == "add_thesis" for q in after["question_queue"])

        # Snapshot cards intentionally have no historical add prompt of their
        # own.  Feed the exact production-built state into the normal prepare
        # queue with one synthetic card prompt: this remains the CLI/persisted
        # plan path, while making the user-visible dedup branch observable.  If
        # routine adoption had reset add_count, this question would reappear.
        probe_card = dict(after["engine_card"])
        probe_card["thesis_questions"] = [{
            "ticker": "PLTR", "question": "Synthetic add-decision prompt",
        }]
        probe_card_path = pathlib.Path(tmp) / "after-refresh-card.json"
        probe_state_path = pathlib.Path(tmp) / "after-refresh-state.json"
        probe_card_path.write_text(json.dumps(probe_card, ensure_ascii=False), encoding="utf-8")
        probe_state_path.write_text(json.dumps(after["engine_state"], ensure_ascii=False), encoding="utf-8")
        deduped = _run("prepare", "--root", root, "--language", "en",
                       "--card-json", probe_card_path, "--state-json", probe_state_path,
                       "--session-nonce", "after-refresh-add-probe")
        assert deduped.returncode == 0, deduped.stdout + deduped.stderr
        queued = _pending_plan(root, deduped.stdout)
        assert not any(q["kind"] == "add_thesis" for q in queued["question_queue"])
        rejection = next(row for row in queued["card_plan"]["question_selection"]["rejected"]
                         if row["kind"] == "add_thesis")
        assert rejection["reason"] == "already_captured"


def test_a_second_declaration_does_not_ask_for_theses_it_already_has():
    """#539's acceptance, end to end through the lane a user actually walks.

    The user declares their holdings and writes a thesis for each position --
    the most effortful thing a review asks of them. They declare again a few
    weeks later, having sold nothing, and the review asks for the same theses
    again: the snapshot-derived cycle ids were minted from each declaration's
    own `as_of`, so the theses on file belonged to the previous generation of
    ids and `missing_thesis_positions` was honestly reporting that none of the
    current ids had one.

    Nothing here touches the question layer. `missing_thesis_positions` is still
    `positions where cycle_id not in active`; what changed is that a position
    nobody sold keeps its cycle id, so it finds the thesis that was always there.
    """
    positions = [{"ticker": "SPY", "shares": 2, "avg_cost": 600,
                  "market": "US", "currency": "USD"},
                 {"ticker": "PLTR", "shares": 20, "avg_cost": 30,
                  "market": "US", "currency": "USD"}]
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        first, _path = _snapshot_prepare(
            tmp, root, payload={"as_of": "2026-06-30", "positions": positions},
            name="first.json")
        assert sorted(row["cycle_id"] for row in first["missing_thesis_positions"]) == \
            ["PLTR#2026-06-30#1", "SPY#2026-06-30#1"], (
                "the opening declaration has no theses yet, so both are asked for")
        assert _finalize_snapshot_session(tmp, root, first, "first").returncode == 0

        moved = [dict(positions[0]), dict(positions[1], shares=21)]
        second, _later = _snapshot_prepare(
            tmp, root, payload={"as_of": "2026-07-28", "positions": moved},
            name="second.json")
        assert second["missing_thesis_positions"] == [], (
            "they answered this in the first review and sold nothing since; "
            "asking again is the product telling them it does not remember")
        assert sorted(row["cycle_id"] for row in
                      second["state_snapshot"]["thesis_states"]) == \
            ["PLTR#2026-06-30#1", "SPY#2026-06-30#1"], (
                "and the theses are the same ones, still on the same cycles")
        assert _finalize_snapshot_session(tmp, root, second, "second").returncode == 0
        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        assert sorted(row["cycle_id"] for row
                      in ledger_engine.derive_holdings(events)["holdings"].values()) == \
            ["PLTR#2026-06-30#1", "SPY#2026-06-30#1"], (
                "the recorded book agrees with the plan the user was shown")


def test_a_review_lane_adoption_keeps_the_holding_duration_the_user_answered():
    """#536: the review lane used to spend an answer the refresh lane collected.

    The user updates their book, is asked about a position that appeared, and
    says they have held it about fourteen months. The refresh lane records that.
    Later they hand over a holdings view whose only change is small -- an
    ordinary path, adopted without ceremony -- and get a review. Before this, the
    review lane rebuilt the anchor from the declared envelope, which structurally
    cannot carry provenance, so the answered start reverted to a bookkeeping date
    and every holding-period reading for that position quietly became wrong. They
    were never told and never re-asked, because the position is held, not
    appearing.

    The carry-forward has one implementation now, reached by both lanes, which is
    this issue's third acceptance criterion.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        opening = [{"ticker": "SPY", "shares": 2, "avg_cost": 600,
                    "market": "US", "currency": "USD"}]
        plan, _path = _snapshot_prepare(
            tmp, root, payload={"as_of": "2026-06-30", "positions": opening},
            name="opening.json")
        assert _finalize_snapshot_session(tmp, root, plan, "opening").returncode == 0

        appeared = opening + [{"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
                               "market": "US", "currency": "USD"}]
        with_newco = _snapshot_json(tmp, payload={"as_of": "2026-07-15",
                                                  "positions": appeared},
                                    name="with-newco.json")
        receipt = _refresh(str(root), str(with_newco))
        assert [row["ticker"] for row in receipt["pending_confirmations"]] == ["NEWCO"]
        adopted = _refresh(str(root), str(with_newco),
                           {"refresh_id": receipt["refresh_id"],
                            "answers": [{"ticker": "NEWCO", "classification": "confirmed",
                                         "held_months": 14}]})
        assert adopted["status"] == "adopted"
        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        assert ledger_engine.derive_holdings(events)["holdings"]["NEWCO"]["cycle_id"] == \
            "NEWCO#2025-05-15#1", "the refresh lane records the answer correctly"

        # A small share change: `plan_refresh` raises nothing, so this reaches the
        # review lane rather than being routed back (#530).
        later = [dict(appeared[0]), dict(appeared[1], shares=11)]
        review, _again = _snapshot_prepare(
            tmp, root, payload={"as_of": "2026-07-28", "positions": later},
            name="later.json")
        assert review["engine_state"]["holdings"]["positions"]["NEWCO"]["cycle_start"] == \
            "2025-05-15", (
                "the plan the user is questioned from must not restart a "
                "position they answered for")
        assert _finalize_snapshot_session(tmp, root, review, "later").returncode == 0
        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        holdings = ledger_engine.derive_holdings(events)["holdings"]
        assert holdings["NEWCO"]["cycle_id"] == "NEWCO#2025-05-15#1", (
            "and the adopted book keeps it: an answer the product spent "
            "silently is the same defect as one it never collected")
        assert holdings["SPY"]["cycle_id"] == "SPY#2026-06-30#1"


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


def test_a_vanished_position_is_refused_but_an_avg_cost_difference_is_reviewed():
    """#530's criterion, in both directions, against one recorded book.

    The refusal is not "the declaration does not reconcile clean" -- it is
    "the book-update lane would have to ask a human about this", which
    `prepare` decides by asking that lane rather than writing a second rule
    about which differences matter.

    Refusing on the status instead would be strict in the wrong place. A
    vanished position is the only difference that destroys information when
    adopted quietly: no exit record, no closed cycle, no revisit, and win rate
    and exit discipline undercount permanently. An `avg_cost` difference costs
    nothing and happens constantly for legitimate reasons -- `derive_holdings`
    keeps a moving average while a broker may use FIFO or amortize fees, and
    the tolerance is half a cent -- so refusing on it would block a real user
    over an arithmetic convention while the case that loses their exits went
    through."""
    initial = {
        "as_of": "2026-07-10",
        "positions": [
            {"ticker": "SPY", "shares": 2, "avg_cost": 600, "market": "US", "currency": "USD"},
            {"ticker": "PLTR", "shares": 20, "avg_cost": 30, "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan1, _path = _snapshot_prepare(tmp, root, payload=initial, name="first.json")
        assert _finalize_snapshot_session(tmp, root, plan1, "first").returncode == 0
        baseline = _ledger_rows(root)

        vanished = _snapshot_json(tmp, payload={
            "as_of": "2026-07-15",
            "positions": [{"ticker": "SPY", "shares": 2, "avg_cost": 600,
                           "market": "US", "currency": "USD"}],
        }, name="vanished.json")
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                   vanished, "--root", root, "--language", "en")
        assert run.returncode == 2, run.stdout + run.stderr
        assert json.loads(run.stdout)["error"] == book_refresh_engine.NEEDS_BOOK_UPDATE
        assert _ledger_rows(root) == baseline, "a refused declaration writes nothing"

        # The other direction, and the regression guard for over-strictness:
        # the book is a hair off on cost basis and nothing else. Nobody has to
        # answer anything, so this reviews normally -- and still reconciles
        # `adjusted`, which is exactly why the status is not the criterion.
        drifted = _snapshot_json(tmp, payload={
            "as_of": "2026-07-15",
            "positions": [
                {"ticker": "SPY", "shares": 2, "avg_cost": 600.05,
                 "market": "US", "currency": "USD"},
                {"ticker": "PLTR", "shares": 20, "avg_cost": 30,
                 "market": "US", "currency": "USD"},
            ],
        }, name="drifted.json")
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                   drifted, "--root", root, "--language", "en")
        assert run.returncode == 0, run.stdout + run.stderr
        plan2 = _pending_plan(root, run.stdout)
        frozen = plan2["engine_state"]["snapshot_reconciliation"]
        assert frozen["status"] == "adjusted"
        assert frozen["diff"]["positions"] == [
            {"ticker": "SPY", "kind": "avg_cost", "derived": 600.0, "declared": 600.05}]
        assert _finalize_snapshot_session(tmp, root, plan2, "drifted").returncode == 0
        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == ["snapshot", "adjustment", "snapshot"], \
            "a difference nobody had to answer for is adopted by the review lane itself"
        assert not [row for row in rows if row["type"] == "position_absence"], \
            "and it produces no absence, because none was ever confirmed"


def test_finalize_refuses_a_vanished_position_prepare_never_saw():
    """The backstop under the projection lock, for the bundles prepare cannot reach (#530).

    Prepare's refusal covers the live route, but two paths arrive at finalize
    without having passed it: a bundle prepared before #530 and finalized after
    it, and the `--card-json`/`--state-json` developer route, which skips
    `_validate_initial_snapshot_root` entirely. Either one carries a frozen
    `adjusted` diff that may drop a position the record still holds, and the
    review lane has no absence to hand the shared writer -- so the exit would
    vanish exactly as it did before this change.

    This asserts only what a frozen diff can answer by itself. It is not a
    second copy of prepare's criterion: *which* differences need the user is
    still decided in one place, by asking the book-update lane. What is
    asserted here is narrower and permanent -- the one difference kind that
    destroys information never reaches `append_book_adoption`."""
    initial = {
        "as_of": "2026-07-10",
        "positions": [
            {"ticker": "SPY", "shares": 2, "avg_cost": 600, "market": "US", "currency": "USD"},
            {"ticker": "PLTR", "shares": 20, "avg_cost": 30, "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan1, _path = _snapshot_prepare(tmp, root, payload=initial, name="first.json")
        assert _finalize_snapshot_session(tmp, root, plan1, "first").returncode == 0
        baseline = _ledger_rows(root)

        # Built the way a pre-#530 bundle was: the adapter runs, the frozen
        # reconciliation is whatever the ledger says, and nothing asked.
        vanished = _snapshot_json(tmp, payload={
            "as_of": "2026-07-15",
            "positions": [{"ticker": "SPY", "shares": 2, "avg_cost": 600,
                           "market": "US", "currency": "USD"}],
        }, name="vanished.json")
        _card, state, _meta = snapshot_adapter.prepare(vanished)
        anchor = state["snapshot_anchor"]
        events, _skipped = ledger_engine.load_ledger(str(root / "ledger.jsonl"))
        frozen = ledger_engine.snapshot_reconciliation(events, anchor)
        assert frozen["status"] == "adjusted"
        assert [row["kind"] for row in frozen["diff"]["positions"]] == ["only_derived"], \
            "PLTR is in the record and absent from the declaration"

        bundle = {
            "session_id": "legacy-pending",
            "review_plan": {"input": {"kind": "positions_snapshot"}},
            "engine_state": {"snapshot_anchor": anchor, "snapshot_reconciliation": frozen},
        }
        try:
            session_engine._assert_initial_snapshot_boundary(str(root), bundle)
        except session_engine.SessionError as exc:
            assert str(exc) == session_engine.VANISHED_POSITION_NEEDS_ANSWER, str(exc)
        else:
            raise AssertionError(
                "finalize adopted a declaration that drops a held position without "
                "anyone having been asked whether it was sold")
        assert _ledger_rows(root) == baseline, "the refusal happens before any append"


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

        # #549: a declaration can no longer disqualify itself. The flag that let
        # it is refused at the envelope boundary rather than quietly ignored, so
        # an agent still writing it is told, not silently taken at face value.
        with_flag = _snapshot_json(tmp, payload={
            "as_of": "2026-07-15", "is_complete": False,
            "positions": [{"ticker": "SPY", "shares": 3, "market": "US", "currency": "USD"}],
        }, name="with-completeness-flag.json")
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                   with_flag, "--root", root, "--language", "en")
        assert run.returncode == 2
        assert "unknown fields: is_complete" in run.stdout

        older = _snapshot_json(tmp, payload={
            "as_of": "2026-07-05",
            "positions": [{"ticker": "SPY", "shares": 1, "market": "US", "currency": "USD"}],
        }, name="older.json")
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                   older, "--root", root, "--language", "en")
        assert run.returncode == 2
        assert "older than the current ledger anchor" in run.stdout
        assert _ledger_rows(root) == baseline, "rejected declarations write nothing"

        # Drift between prepare and finalize. Since #530 only a clean
        # reconciliation gets this far, so the drift runs the other way: a
        # declaration that agreed with the record at prepare no longer agrees
        # with it at finalize, and finalize refuses to mark a ledger the user
        # never previewed.
        agreeing = {
            "as_of": "2026-07-15",
            "positions": [{"ticker": "SPY", "shares": 2, "market": "US", "currency": "USD"}],
        }
        plan2, _second = _snapshot_prepare(tmp, root, payload=agreeing, name="drift.json")
        assert plan2["engine_state"]["snapshot_reconciliation"]["status"] == "reconciled"
        ledger_engine.append_events(str(root / "ledger.jsonl"), [
            {"type": "trade", "date": "2026-07-12", "ticker": "SPY", "action": "buy",
             "qty": 1, "price": 610, "market": "US", "currency": "USD"}])
        stale = _finalize_snapshot_session(tmp, root, plan2, "stale")
        assert stale.returncode == 2
        assert "run prepare again" in stale.stdout
        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == ["snapshot", "trade"], \
            "a stale finalize must not write a mark, an adjustment, or a new anchor"

        # Re-preparing recomputes honestly: the interleaved buy makes the same
        # declaration disagree with the record, so the honest answer is now that
        # the book has to be brought up to date before there is anything to
        # review.
        rerun = _run("prepare", "--route", "snapshot_review", "--snapshot-json",
                     _second, "--root", root, "--language", "en")
        assert rerun.returncode == 2, rerun.stdout + rerun.stderr
        assert json.loads(rerun.stdout)["error"] == book_refresh_engine.NEEDS_BOOK_UPDATE


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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
            "as_of": "2026-07-02", "derived_from": "trades_csv",
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
        # The declaration is untouched: a transaction import records the book it
        # derived beside it (#549) and never rewrites the declared anchor.
        declared = [row for row in events if row["type"] == "snapshot"
                    and row.get("source") != ledger_engine.DERIVED_BOOK_SOURCE]
        assert events[0] == anchor_before and declared == [anchor_before]
        assert sum(row["type"] == "trade" for row in events[1:]) == 1
        assert ledger_engine.latest_anchor(events, declared_only=True) == anchor_before
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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        initial = json.loads((root / "sessions" / plan["session_id"] / "bundle.json").read_text())
        initial_ids = {row["ticker"]: row["thesis_id"] for row in initial["thesis_updates"]}

        card, state = _artifacts(tmp)
        card_data, state_data = json.loads(card.read_text()), json.loads(state.read_text())
        state_data.update({"date_start": "2026-06-01", "date_end": "2026-07-01", "n_held": 2})
        state_data["holdings"] = {
            "as_of": "2026-07-01", "derived_from": "trades_csv",
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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
                     "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr

        card, state = _artifacts(tmp)
        state_data = json.loads(state.read_text())
        state_data.update({"date_start": "2026-07-02", "date_end": "2026-07-03", "n_held": 1})
        state_data["holdings"] = {
            "as_of": "2026-07-03", "derived_from": "trades_csv",
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


# --- The cash anchor is a stated gap, and answerable after the card (#357) ----
#
# Five recurrences, three of them on real data, all of the same shape: nothing
# in the Review Plan said the anchor was missing. The engine demanded a
# disclosure when the balance was *present* (`acct_perf_basis`) and demanded
# nothing when it was absent, so the one condition the agent had to notice
# unprompted was the only one the plan never mentioned.
#
# The owner ruled out making `prepare` refuse: compute the first card, then ask
# a directly answerable question, then re-render. So the gap is stated, the ask
# lands at the card beat, and `add-cash` is what makes answering cheap enough to
# be worth asking for.

_OFFLINE_MOCK = ROOT / "skills" / "fomo-kernel" / "mock" / "mock_trades.csv"


def _offline_env(tmp):
    """A prepare that cannot reach a price source, so the run is deterministic."""
    stub_dir = pathlib.Path(tmp) / "stubs"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / "yfinance.py").write_text('raise ImportError("offline stub")\n', encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(stub_dir), env.get("PYTHONPATH")) if part)
    return env


def _prepared_without_an_anchor(tmp, root):
    env = _offline_env(tmp)
    run = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en", env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    return env, json.loads(run.stdout)["review_plan"]


def test_a_review_with_no_cash_anchor_says_so_in_its_own_plan():
    """The signal that did not exist. It also has to carry enough to ask a
    specific question -- which currency, and what answering buys -- because a
    blind "give me a number, trust me" is what the #357 owner note singled out."""
    with tempfile.TemporaryDirectory() as tmp:
        _env, plan = _prepared_without_an_anchor(tmp, pathlib.Path(tmp) / "coach")
        anchor = plan["input"]["cash_anchor"]
        assert anchor["status"] == "absent", anchor
        assert anchor["unanchored_currencies"] == ["USD"], anchor
        assert set(anchor["unlocks"]) == {"account_level_return", "annualized_return",
                                          "cash_drag"}, anchor
        # The one field that separates this gap from the price gap beside it:
        # a price request is recovered before the user sees anything, this is
        # asked after they have seen the card (owner ruling 2026-07-30).
        assert anchor["ask_after"] == "card_presented", anchor
        assert "add-cash" in anchor["next_action"], anchor["next_action"]


def test_a_route_that_never_asks_for_cash_says_that_too():
    """`not_applicable` is a positive claim with a reason, never an absent key
    -- the same discipline as `--card not_applicable` on a card-free receipt
    route. A light week that merely had no entry would be indistinguishable
    from one the engine forgot to classify, which is how #358's light-tier gap
    shipped: the flow asked before a tier check three lines below it."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        first = _prepare_dated(tmp, root, "2026-07-14", "cashw1")
        _finalize(tmp, root, first, _answer_queue(first, _week1_choices, "skip"), "cashw1")
        light = _prepare_dated(tmp, root, "2026-07-17", "cashw2")
        assert light["state_snapshot"]["cadence"]["tier"] == "light"
        assert light["input"]["cash_anchor"] == {
            "status": "not_applicable", "reason": "light_tier"}, light["input"]["cash_anchor"]


def test_add_cash_recomputes_the_same_review_without_re_asking_anything():
    """The owner's "re-render when they answer", with the part that makes it
    safe. Everything the user already did carries over untouched, and the
    engine's own artifacts move only where a cash anchor is allowed to move
    them -- so the recomputed card is the same review with one more pillar,
    not a second review the user never saw the first version of."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_without_an_anchor(tmp, root)
        pending = pathlib.Path(session_engine.pending_dir(str(root), plan["session_id"]))
        (pending / "answers.json").write_text('{"marker": "already answered"}', encoding="utf-8")
        (pending / "narrative.json").write_text('{"marker": "already written"}', encoding="utf-8")
        # Read the pre-recompute plan now: the superseded pending directory is
        # gone by the time the assertions below compare against it.
        full_before = json.loads((pending / "plan.json").read_text(encoding="utf-8"))

        run = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                   "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-30"}', env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        out = json.loads(run.stdout)
        assert out["status"] == "anchored"
        assert out["session_id"] != plan["session_id"], \
            "the id is content-addressed from engine state and the anchor is part of it"
        assert out["superseded_session_id"] == plan["session_id"]
        assert sorted(out["carried_forward"]) == ["answers", "narrative"]

        after = session_engine.load_pending(str(root), out["session_id"])
        assert after["answers"] == {"marker": "already answered"}
        assert after["narrative"] == {"marker": "already written"}
        assert not pending.exists(), (
            "the superseded pending session must not survive as a second, cash-less "
            "review that preview/finalize would happily commit")

        recomputed = after["plan"]
        assert recomputed["input"]["cash_anchor"] == {"status": "anchored",
                                                      "source": "anchored"}
        assert recomputed["engine_state"]["cash"]["source"] == "anchored"
        # Nothing the user acted on moved. This is the acceptance line, asserted
        # rather than argued.
        for key in ("question_queue", "missing_thesis_positions"):
            assert recomputed[key] == full_before[key], key
        assert (recomputed["card_plan"]["candidate_rules"]
                == full_before["card_plan"]["candidate_rules"])


def test_add_cash_refuses_when_more_than_the_anchor_moved():
    """The gate that makes "reuses this session's frozen prices" true rather
    than hoped for. `market_data`'s same-day cache is what normally makes the
    recompute a zero-request replay of the identical bundle; when it is not --
    a day boundary crossed, an edited input file, a ledger that moved -- the
    difference has to be a refusal, because the user answered against the card
    the first set of numbers rendered.

    Simulated here by moving one frozen close, which is exactly what a second
    live resolution would do."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_without_an_anchor(tmp, root)
        pending = pathlib.Path(session_engine.pending_dir(str(root), plan["session_id"]))
        frozen = json.loads((pending / "plan.json").read_text(encoding="utf-8"))
        frozen["engine_state"]["price_snapshot"]["prices"]["MOVED"] = 1.23
        (pending / "plan.json").write_text(json.dumps(frozen), encoding="utf-8")

        run = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                   "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-30"}', env=env)
        assert run.returncode != 0, run.stdout
        error = json.loads(run.stdout)["error"]
        # The refusal names which of the gate's two verdicts it reached (#665).
        # "the facts moved" is the one that refuses; the other -- the anchor
        # propagating into the account pillar -- is what this command is for, and
        # naming only "something changed" is what made the two indistinguishable.
        assert "the facts moved" in error, error
        assert "the valuation frame the card was priced from" in error, error
        assert sorted(os.listdir(pathlib.Path(root) / ".pending")) == [plan["session_id"]], \
            "a refused recompute must leave no anchored, finalizable session behind"


def test_add_cash_carries_a_declared_price_dead_end_forward():
    """The seam between #357's recompute and #623's gate. `add-cash` re-enters
    prepare, so a declaration it dropped would come back as `attempted: false`
    and the draft gate would then refuse a review the agent had already
    declared honestly — a refusal caused entirely by supplying a cash balance."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env = _offline_env(tmp)
        run = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en",
                   "--prices-unavailable", "no market-data source reachable from this host",
                   env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        added = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                     "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-30"}', env=env)
        assert added.returncode == 0, added.stdout + added.stderr
        recovery = (json.loads(added.stdout)["review_plan"]["input"]["price_feed"]["recovery"])
        assert recovery["outcome"] == "declared_unavailable", recovery
        assert "reachable from this host" in recovery["checked"], recovery


# ── #758: set-cap and add-cash are one message beat (flows/first-review.md ──
# ── step 6), and set-cap rewrites profile.json immediately -- both orders ──
# ── of the two same-beat actions must succeed. ──

def test_add_cash_after_set_cap_in_the_same_beat_succeeds_and_freezes_the_original_cap():
    """The exact repro in the issue. Before this fix, `set-cap` (which
    rewrites profile.json synchronously) run just before `add-cash` made the
    recompute below read the NEW cap live, `card_plan.candidate_rules`
    changed under a card the user had already been shown, and
    `_cash_recompute_drift` correctly-by-its-own-contract refused on "the
    rules the user was offered" -- confirmed by reverting just the
    `_run_engine`/`cmd_add_cash` fix and rerunning this exact sequence, which
    reproduces the issue's own error text verbatim.

    `add-cash` now freezes the cap this pending session was actually
    prepared with (`plan["engine_state"]["max_position_pct"]`, echoed back by
    trade_recap.py), the same "reuse the frame" posture already proven for
    market data above. Both the ordering and the frozen-not-live value are
    asserted: the recompute must succeed, and its sizing rule must still
    read the DEFAULT cap the user was shown (20%), never the 30% they just
    stated -- data-contract.md's own words are that a stated cap reconciles
    against the *next* review, not the one already on screen."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_without_an_anchor(tmp, root)
        original_rule = plan["card_plan"]["candidate_rules"][0]["rule"]
        assert "20%" in original_rule, original_rule   # the universal default

        cap_run = _run("set-cap", "--root", root, "--pct", "0.30")
        assert cap_run.returncode == 0, cap_run.stdout + cap_run.stderr

        run = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                   "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-30"}', env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        out = json.loads(run.stdout)
        assert out["status"] == "anchored"

        recomputed_rule = out["review_plan"]["card_plan"]["candidate_rules"][0]["rule"]
        assert recomputed_rule == original_rule, (
            "the recompute must keep the candidates already shown, not silently re-derive "
            f"them under the cap the user just stated: {recomputed_rule!r}")
        assert "20%" in recomputed_rule and "30%" not in recomputed_rule, recomputed_rule

        internal = session_engine.load_pending(str(root), out["session_id"])["plan"]
        assert internal["engine_state"]["max_position_pct"] is None, (
            "None means the universal default was frozen through -- the same value the "
            "original plan carried, not the 0.30 now sitting in profile.json")


def test_add_cash_freezes_the_original_sessions_own_cap_not_the_default_or_the_live_one():
    """Sharper than the test above on purpose: that one's session was
    prepared under the universal default (None), so a wiring bug that
    hardcoded `frozen_position_cap=None` instead of actually reading
    `plan["engine_state"]["max_position_pct"]` would have produced an
    identical result and gone undetected (caught during this fix's own
    mutation verification). Preparing under an already-nonstandard cap
    (0.22) makes three different outcomes -- the frozen original (22%), the
    live profile.json value at add-cash time (30%), and a wrong hardcoded
    default (20%) -- mutually distinguishable, so a wiring regression to any
    of the other two shows up as a wrong percentage rather than a
    coincidental match."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env = _offline_env(tmp)

        first_cap = _run("set-cap", "--root", root, "--pct", "0.22")
        assert first_cap.returncode == 0, first_cap.stdout + first_cap.stderr

        prep = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en", env=env)
        assert prep.returncode == 0, prep.stdout + prep.stderr
        plan = json.loads(prep.stdout)["review_plan"]
        original_rule = plan["card_plan"]["candidate_rules"][0]["rule"]
        assert "22%" in original_rule, original_rule

        second_cap = _run("set-cap", "--root", root, "--pct", "0.30")
        assert second_cap.returncode == 0, second_cap.stdout + second_cap.stderr

        run = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                   "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-30"}', env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        recomputed_rule = json.loads(run.stdout)["review_plan"]["card_plan"]["candidate_rules"][0]["rule"]
        assert "22%" in recomputed_rule, (
            f"must freeze the 22% this session was actually prepared with: {recomputed_rule!r}")
        assert "30%" not in recomputed_rule and "20%" not in recomputed_rule, recomputed_rule


def test_set_cap_after_add_cash_still_succeeds_and_still_reaches_the_next_review():
    """The order that already worked must keep working (no regression), and
    the deferred half of #758's fix must be provably true rather than merely
    implied by the code shape: `set-cap` run after `add-cash` is untouched by
    this fix (`cmd_set_cap` and `_position_cap_override` are not part of it)
    and its write still reaches a later, ordinary `prepare` -- the cap is
    deferred to the next review, never dropped."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_without_an_anchor(tmp, root)

        run = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                   "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-30"}', env=env)
        assert run.returncode == 0, run.stdout + run.stderr

        cap_run = _run("set-cap", "--root", root, "--pct", "0.30")
        assert cap_run.returncode == 0, cap_run.stdout + cap_run.stderr

        # A later, ordinary prepare (`amending_session` unset, so the live
        # `_position_cap_override` path is what must answer, not a frozen
        # one) is the next review this cap was always meant to reach.
        later = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en",
                     "--session-nonce", "next-review-probe", env=env)
        assert later.returncode == 0, later.stdout + later.stderr
        later_rule = json.loads(later.stdout)["review_plan"]["card_plan"]["candidate_rules"][0]["rule"]
        assert "30%" in later_rule, later_rule


# ── the card-beat answer has to be recordable on a priced review (#665) ──
#
# Everything above runs price-degraded, and that is why it stayed green through
# the defect: with nothing retrieved there is no second retrieval to disagree
# with the first. On a real user's review the engine fetches, and `add-cash`
# fetched again — a second observation instant, therefore different closes,
# therefore every derived number moved, and the recompute reported its own
# movement as "the facts moved". The user answered the question the card asked
# them and the answer could not be recorded (#665).
#
# The provider below is what makes that reproducible offline. Two properties,
# both taken from the real thing: it answers with different closes every pass
# (`market_data`'s own docstring records ^VIX moving between two calls seconds
# apart), and one benchmark comes back unpriced, which is what makes the
# same-day cache refuse to serve the request that produced it.

_MOVING_PROVIDER = '''
# Injected as usercustomize (never sitecustomize -- Homebrew ships its own and
# shadowing it removes site-packages), so the real CLI subprocess runs against a
# deterministic stand-in for a live market.
import datetime as dt
import os
import sys
sys.path.insert(0, os.environ["ENGINE_DIR"])

UNPRICED = {"XLY"}          # present-but-empty, exactly as a dead symbol returns


def _fake_download(symbols, start, end=None):
    import pandas as pd
    log = os.environ["PROVIDER_LOG"]
    with open(log, "a") as handle:
        handle.write(",".join(sorted(symbols)) + "\\n")
    with open(log) as handle:
        nth = len([line for line in handle if line.strip()])
    days = [dt.date(2026, 7, 27), dt.date(2026, 7, 28), dt.date(2026, 7, 29)]
    index = pd.DatetimeIndex([dt.datetime(d.year, d.month, d.day) for d in days])
    data = {}
    for symbol in symbols:
        seed = sum(ord(c) for c in symbol)
        # The nudge is the whole point: a second pass is a second instant.
        base = 20.0 + (seed % 400) + 0.37 * (nth - 1)
        if symbol in UNPRICED:
            data[("Close", symbol)] = [float("nan")] * len(days)
        elif symbol.endswith("=X"):
            data[("Close", symbol)] = [32.0 + 0.01 * (nth - 1)] * len(days)
        else:
            data[("Close", symbol)] = [base - 1.0, base - 0.5, base]
        data[("Stock Splits", symbol)] = [float("nan")] * len(days)
    frame = pd.DataFrame(data, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    # (data, per-symbol failures). XLY above is present-but-empty with *no*
    # failure entry, which is the point: a dead symbol is a definitive answer,
    # not a request that never came back.
    return frame, {}


import market_data
market_data._download = _fake_download
# The other half of the provider seam. Without it `_from_yahoo` answers
# `provider_missing` above the fake and never calls it -- which is every CI
# runner, none of which install yfinance (#621).
market_data._provider_available = lambda: True
'''


def _priced_env(tmp):
    """A CLI environment that really resolves market data, against a live-like fake."""
    sitedir = pathlib.Path(tmp) / "provider-site"
    sitedir.mkdir(exist_ok=True)
    (sitedir / "usercustomize.py").write_text(_MOVING_PROVIDER, encoding="utf-8")
    env = dict(os.environ)
    env.pop("TR_OFFLINE", None)          # the point here is resolution, not degradation
    env["ENGINE_DIR"] = str(ENGINE_DIR)
    env["PROVIDER_LOG"] = str(pathlib.Path(tmp) / "provider.log")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(sitedir), str(ENGINE_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return env


def _provider_calls(env):
    try:
        with open(env["PROVIDER_LOG"], encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    except OSError:
        return []


def _prepared_on_a_priced_review(tmp, root, csv=None):
    env = _priced_env(tmp)
    run = _run("prepare", csv or _OFFLINE_MOCK, "--root", root, "--language", "en", env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    plan = json.loads(run.stdout)["review_plan"]
    assert plan["input"]["price_feed"]["provenance"]["mode"] == "engine_fetch", \
        f"fixture must actually be engine-priced: {plan['input']['price_feed']}"
    assert plan["input"]["cash_anchor"]["status"] == "absent", plan["input"]["cash_anchor"]
    return env, plan


def _preview(root, plan, tmp, env, tag):
    answers = pathlib.Path(tmp) / f"answers-{tag}.json"
    answers.write_text(json.dumps(_answers_for_plan(plan)), encoding="utf-8")
    narrative = pathlib.Path(tmp) / f"narrative-{tag}.json"
    narrative.write_text(json.dumps(_narrative_for_plan(plan)), encoding="utf-8")
    return _run("preview", "--root", root, "--session-id", plan["session_id"],
                "--answers", answers, "--narrative", narrative, env=env)


def test_the_card_beat_cash_answer_is_recordable_on_an_engine_priced_review():
    """#665's owning outcome, walked the way the product prescribes it.

    prepare -> the card is previewed and shown -> the user answers the cash
    question that card asked -> add-cash -> the amended card. Every step is the
    real CLI, the prices are the engine's own, and the provider moves between
    passes because a real one does.

    The load-bearing assertion is the request count. The amended card must be
    priced from the frame the user was reading, and the only way to know that is
    that the recompute asked for nothing: a second resolution that happened to
    agree would prove nothing, and one that disagreed is the defect.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_on_a_priced_review(tmp, root)
        frozen_frame = json.loads(
            (pathlib.Path(session_engine.pending_dir(str(root), plan["session_id"]))
             / "plan.json").read_text(encoding="utf-8"))["engine_state"]["price_snapshot"]

        shown = _preview(root, plan, tmp, env, "first")
        assert shown.returncode == 0, shown.stdout + shown.stderr
        assert json.loads(shown.stdout)["private_card"], "the card the user is asked at"
        calls_before = len(_provider_calls(env))
        assert calls_before >= 1, "the review must really have fetched its own prices"

        added = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                     "--cash", '{"currency":"USD","amount":0,"as_of":"2026-07-29"}', env=env)
        assert added.returncode == 0, (
            "a declared balance answered at the card beat must be recordable — this is the "
            f"session the command exists for:\n{added.stdout}{added.stderr}")
        out = json.loads(added.stdout)
        assert out["recompute"]["outcome"] == "anchor_propagated", out["recompute"]
        assert len(_provider_calls(env)) == calls_before, (
            "the recompute re-resolved the market. The user is being shown an amended version "
            "of a card they already read; a second observation instant is a different review "
            f"wearing the same session: {_provider_calls(env)[calls_before:]}")

        amended = session_engine.load_pending(str(root), out["session_id"])["plan"]
        assert amended["engine_state"]["price_snapshot"] == frozen_frame, (
            "the amended card must be priced from the same frame the user was reviewing")
        assert amended["input"]["cash_anchor"] == {"status": "anchored", "source": "anchored"}
        acct = amended["engine_card"]["acct_perf"]
        assert acct.get("acct_twr") is not None and acct.get("cash_drag") is not None, (
            f"the account pillar is what answering buys; it is still empty: {acct}")
        assert amended["engine_card"]["cash"]["source"] == "anchored"

        # And the amended card renders, which is the beat the user actually gets.
        again = _preview(root, amended, tmp, env, "second")
        assert again.returncode == 0, again.stdout + again.stderr
        assert json.loads(again.stdout)["private_card"]


def test_a_source_that_really_moved_still_refuses_and_leaves_nothing_behind():
    """The other half, and the one that keeps the fix from being a rubber stamp.

    An input file edited between the card and the answer is not the anchor
    propagating — it is a different review, and the answers already given were
    made against numbers that no longer exist. Refused before any write: no
    anchored pending session to finalize, and a ledger byte-identical to the one
    the refusal started with.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        csv = pathlib.Path(tmp) / "trades.csv"
        csv.write_text(_OFFLINE_MOCK.read_text(encoding="utf-8"), encoding="utf-8")
        env, plan = _prepared_on_a_priced_review(tmp, root, csv=csv)
        ledger_path = pathlib.Path(root) / "ledger.jsonl"
        ledger_before = ledger_path.read_bytes()

        with open(csv, "a", encoding="utf-8") as handle:
            handle.write("TSLA,5,300.00,BUY,BOUGHT TESLA,2024-12-04,2024-12-06,0,"
                         "-1500.00,0,0,,Trade\n")

        run = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                   "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-29"}', env=env)
        assert run.returncode != 0, run.stdout
        assert ledger_path.read_bytes() == ledger_before, (
            "the refusal must land before any write. The recompute re-enters the ingest, so a "
            "gate that only reads the recomputed plan has already appended the rows it is about "
            "to refuse -- and the user's next command would then run against a book neither "
            "review was computed over")
        error = json.loads(run.stdout)["error"]
        assert "the facts moved" in error, error
        assert "the transaction file has grown by 1 row(s)" in error, (
            f"the refusal must name which fact moved, not only that something did: {error}")
        assert sorted(os.listdir(pathlib.Path(root) / ".pending")) == [plan["session_id"]], \
            "a refused recompute must leave no anchored, finalizable session behind"
        pending = pathlib.Path(session_engine.pending_dir(str(root), plan["session_id"]))
        assert not list(pending.glob("card-*")), \
            f"and no card artifact for a review nobody accepted: {list(pending.iterdir())}"


def test_a_frame_no_longer_on_record_refuses_instead_of_fetching_a_fresh_one():
    """The fail-closed half of reusing the frame, and the more tempting failure.

    The frame this session was built on can genuinely be gone — a day boundary,
    a reset root, a session prepared on another machine. Quietly resolving a new
    one would look like a fix and would be the original defect: the user would be
    handed an amended card priced at an instant they never saw, with the account
    pillar computed against it. So the recompute asks for nothing, the review
    degrades, and the gate refuses naming the frame.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_on_a_priced_review(tmp, root)
        ledger_before = (pathlib.Path(root) / "ledger.jsonl").read_bytes()
        calls_before = len(_provider_calls(env))
        shutil.rmtree(pathlib.Path(root) / "cache")      # the frame is no longer on record

        run = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                   "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-29"}', env=env)
        assert run.returncode != 0, run.stdout
        error = json.loads(run.stdout)["error"]
        assert "the facts moved" in error, error
        assert "the valuation frame the card was priced from" in error, error
        assert len(_provider_calls(env)) == calls_before, (
            "the recompute went and fetched a replacement frame. That is the defect wearing a "
            f"repair: {_provider_calls(env)[calls_before:]}")
        assert sorted(os.listdir(pathlib.Path(root) / ".pending")) == [plan["session_id"]]
        assert (pathlib.Path(root) / "ledger.jsonl").read_bytes() == ledger_before


def test_replaying_the_same_cash_anchor_changes_nothing():
    """Idempotence, at the level the user can retry at.

    An agent that loses the response and re-runs the command must not produce a
    second review, a second ledger row, or a different answer. The anchored
    session is already anchored, so the replay is refused with a stable message
    and the session on disk is byte-identical afterwards.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_on_a_priced_review(tmp, root)
        anchor = '{"currency":"USD","amount":1200,"as_of":"2026-07-29"}'
        added = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                     "--cash", anchor, env=env)
        assert added.returncode == 0, added.stdout + added.stderr
        anchored_id = json.loads(added.stdout)["session_id"]
        pending = pathlib.Path(session_engine.pending_dir(str(root), anchored_id))
        before = {path.name: path.read_bytes() for path in sorted(pending.iterdir())}
        ledger_before = (pathlib.Path(root) / "ledger.jsonl").read_bytes()
        calls_before = len(_provider_calls(env))

        replay = _run("add-cash", "--root", root, "--session-id", anchored_id,
                      "--cash", anchor, env=env)
        assert replay.returncode != 0, replay.stdout
        assert "already carries a cash anchor" in json.loads(replay.stdout)["error"]
        assert {path.name: path.read_bytes() for path in sorted(pending.iterdir())} == before, \
            "a replay must not rewrite the session it is replaying"
        assert (pathlib.Path(root) / "ledger.jsonl").read_bytes() == ledger_before
        assert len(_provider_calls(env)) == calls_before, \
            "and it must not reach the market on the way to refusing"
        assert sorted(os.listdir(pathlib.Path(root) / ".pending")) == [anchored_id], \
            "no second pending session survives the replay"


# --- #662: the cash-anchor ask accepts an absolute amount or a percentage ---
#
# A user answering "30%" used to cost three free-form clarification round
# trips (#662's trigger). Owner disposition 2026-08-01: accept both formats;
# the denominator a percentage names is the account's TOTAL value (cash plus
# current position market value), stated in plain words and confirmed once;
# the engine -- never the agent -- converts and discloses the derivation.

def test_add_cash_converts_a_percentage_against_this_sessions_frozen_position_value():
    """#662 proofs 1 and 2, walked through the real CLI on an engine-priced
    review. The percentage converts against this session's own frozen
    position value (no second market resolution), the stored amount is
    exactly p/(100-p) * position_value -- never p/100, which would be the
    position value alone rather than the total account value -- and the
    response discloses that derivation instead of applying it silently.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_on_a_priced_review(tmp, root)
        calls_before = len(_provider_calls(env))

        added = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                     "--cash", '{"currency":"USD","percent_of_total":20,"as_of":"2026-07-29"}',
                     env=env)
        assert added.returncode == 0, added.stdout + added.stderr
        out = json.loads(added.stdout)
        assert out["recompute"]["outcome"] == "anchor_propagated", out["recompute"]
        assert len(_provider_calls(env)) == calls_before, (
            "converting a percentage must not re-resolve prices -- it uses this session's own "
            f"frozen position value: {_provider_calls(env)[calls_before:]}")

        assert "anchor_conversion" in out, (
            "a percentage was converted but the response discloses nothing about it", out)
        conversion = out["anchor_conversion"]
        assert conversion["percent_of_total"] == 20
        assert conversion["currency"] == "USD"
        position_value = conversion["position_value"]
        assert position_value > 0, "the fixture must really have a nonzero position value"
        # The algebra (#662 owner ruling), recomputed independently here rather
        # than trusted from the module under test: 20% of the ACCOUNT'S TOTAL
        # (cash + positions) is 20/80 of the position value alone, not 20/100.
        expected = round(20 / 80 * position_value, 2)
        assert conversion["amount"] == expected, (conversion["amount"], expected, position_value)
        assert conversion["amount"] != round(0.20 * position_value, 2), (
            "20% of the position value alone is the wrong denominator (#662)")

        amended = session_engine.load_pending(str(root), out["session_id"])["plan"]
        cash = amended["engine_state"]["cash"]
        assert cash["balance"] == conversion["amount"], cash
        assert cash["source"] == "anchored"
        # The denominator claim proven end to end: after storing the converted
        # amount, cash really is 20% of cash + positions on the amended card.
        assert abs(cash["weight"] - 0.20) < 1e-9, cash["weight"]
        assert amended["engine_card"]["cash"]["balance"] == conversion["amount"]


def test_add_cash_percent_leaves_the_absolute_amount_response_unchanged():
    """Regression (#662 proof 3): an absolute-amount payload's response must
    carry no new key at all -- not even a null one -- now that a percentage
    format exists beside it, and the stored figure is untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _prepared_on_a_priced_review(tmp, root)
        added = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                     "--cash", '{"currency":"USD","amount":8200,"as_of":"2026-07-29"}', env=env)
        assert added.returncode == 0, added.stdout + added.stderr
        out = json.loads(added.stdout)
        assert "anchor_conversion" not in out, (
            "an absolute-amount response must not grow a new key", out)
        amended = session_engine.load_pending(str(root), out["session_id"])["plan"]
        assert amended["engine_state"]["cash_anchor_conversion"] is None
        assert amended["engine_state"]["cash"]["balance"] == 8200.0


def test_add_cash_percent_refuses_without_a_usable_position_value():
    """#662 proof 4: fails closed rather than converting against a garbage
    denominator. A book with no currently held position has a position value
    of zero, so a percentage cannot be resolved, and the refusal must leave no
    pending session or ledger row behind -- exactly like every other add-cash
    refusal in this file. The same book still takes a plain absolute amount:
    the refusal is specific to the percentage format needing a denominator,
    not to this book being otherwise unable to anchor cash at all.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        csv = pathlib.Path(tmp) / "fully_exited.csv"
        csv.write_text(
            "Symbol,Quantity,Price,Action,Description,TradeDate,SettledDate,Interest,Amount,"
            "Commission,Fee,CUSIP,RecordType\n"
            "AAA,10,100.00,BUY,BOUGHT AAA,2024-01-02,2024-01-04,0,-1000.00,0,0,,Trade\n"
            "AAA,10,110.00,SELL,SOLD AAA,2024-06-02,2024-06-04,0,1100.00,0,0,,Trade\n",
            encoding="utf-8")
        env = _offline_env(tmp)
        run = _run("prepare", csv, "--root", root, "--language", "en", env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        assert plan["input"]["cash_anchor"]["status"] == "absent", plan["input"]["cash_anchor"]
        ledger_before = (pathlib.Path(root) / "ledger.jsonl").read_bytes()

        refused = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                       "--cash", '{"currency":"USD","percent_of_total":30,"as_of":"2024-06-02"}',
                       env=env)
        assert refused.returncode != 0, refused.stdout
        error = json.loads(refused.stdout)["error"]
        assert "position market value" in error and "not usable" in error, error
        assert sorted(os.listdir(pathlib.Path(root) / ".pending")) == [plan["session_id"]], \
            "a refused conversion must leave no anchored, finalizable session behind"
        assert (pathlib.Path(root) / "ledger.jsonl").read_bytes() == ledger_before

        recovered = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                         "--cash", '{"currency":"USD","amount":500,"as_of":"2024-06-02"}', env=env)
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        amended = session_engine.load_pending(
            str(root), json.loads(recovered.stdout)["session_id"])["plan"]
        assert amended["engine_state"]["cash"]["balance"] == 500.0
        assert amended["engine_state"]["cash"]["weight"] == 1.0


def test_add_cash_refuses_a_session_that_never_takes_an_anchor():
    """A snapshot states cash inline in its own envelope, so there is no second
    place to supply one -- and the plan already says `not_applicable`. The
    command reads that claim rather than re-deriving the condition."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        snapshot = pathlib.Path(tmp) / "positions.json"
        snapshot.write_text(json.dumps({
            "as_of": "2026-07-14",
            "positions": [{"ticker": "SPY", "shares": 10, "avg_cost": 100,
                           "market": "US", "currency": "USD"}]}), encoding="utf-8")
        env = _offline_env(tmp)
        run = _run("prepare", "--route", "snapshot_review", "--snapshot-json", snapshot,
                   "--root", root, "--language", "en", env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        assert plan["input"]["cash_anchor"] == {"status": "not_applicable",
                                                "reason": "snapshot_envelope"}
        refused = _run("add-cash", "--root", root, "--session-id", plan["session_id"],
                       "--cash", '{"currency":"USD","amount":1,"as_of":"2026-07-14"}', env=env)
        assert refused.returncode != 0, refused.stdout
        assert "does not take a cash anchor" in json.loads(refused.stdout)["error"]


# The catalogue phrasings #623 retired, in every locale. A presence test on the
# new wording would go green the moment someone appended a feature list back
# onto it; what has to stay dead is the shape, so the retired fragments are what
# this pins -- the `RECORDED_BOOK_RETIRED_PHRASES` idiom, one surface over.
_RETIRED_INVITATION_CATALOGUES = {
    "en": ("unlocks behavior diagnostics", "and more",
           "unlocks the full behavioral review"),
    "zh-TW": ("解鎖行為診斷", "等）", "解鎖出場紀律"),
    "zh-CN": ("解锁行为诊断", "等）", "解锁出场纪律"),
}


def test_a_card_invitation_never_regrows_a_feature_checklist():
    """#623/#617: an invitation names the one answer this user's book cannot
    reach, not a catalogue of features. Both retired strings enumerated three
    or more diagnostics, which is the shape the rule forbids and the shape a
    future edit is most likely to reintroduce."""
    for language, retired in _RETIRED_INVITATION_CATALOGUES.items():
        missing = card_renderer.load_copy(language)["block_missing"]
        for key in ("snapshot_unlock", "rule_structural"):
            for fragment in retired:
                assert fragment not in missing[key], (language, key, fragment)
            assert missing[key].strip(), (language, key, "an invitation may be absent by "
                                          "branch, never blank when its branch fires")


# --- A degraded price card is a stated dead end, never a skipped step (#623) ---
#
# `flows/first-review.md` step 0 requires the agent to recover the requested
# closes and rerun `prepare --prices` BEFORE delivering a degraded card. Two
# very different runs rendered the identical sentence — "current prices could
# not be retrieved" — and nothing could tell them apart: the sources publish
# nothing, or nobody looked. The second is not a disclosure the user can act
# on; it is a review whose every weight came from cost basis when it did not
# have to. Same structural shape as #357's cash gap: the engine went quiet
# about something it already knew.


def _degraded_price_session(tmp, root):
    """A real offline prepare whose card will carry the price-blocked note."""
    env = _offline_env(tmp)
    run = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en", env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    plan = json.loads(run.stdout)["review_plan"]
    assert plan["input"]["price_feed"]["request"], "fixture must actually be price-degraded"
    return env, plan


def _answers_for_plan(plan):
    """Answer this plan's own queue and its own uncovered cycles.

    `_answers` carries a hardcoded PLTR thesis row for the `--card-json`
    fixture; a real mock-CSV plan has different cycles, and an unknown
    `cycle_id` fails closed before any of the gates under test are reached.
    """
    return {
        "session_id": plan["session_id"],
        "answers": [{"question_id": q["id"], "choice": "skip"}
                    for q in plan["question_queue"]],
        "thesis_updates": [{"cycle_id": row["cycle_id"],
                            "why": "Held while the entry reason is confirmed",
                            "exit_trigger": "The reason for holding stops being true",
                            "horizon": "quarters"}
                           for row in plan.get("missing_thesis_positions") or []],
        "observations": [],
        "commitment": {"choice": "skip"},
    }


def _narrative_for_plan(plan):
    payload = _narrative("en")
    payload["honesty"] = {
        key: "This limitation stays stated on the card rather than treated as a zero."
        for key in plan["card_plan"]["required_honesty_keys"]}
    return payload


def test_a_price_degraded_run_records_whether_recovery_was_ever_attempted():
    with tempfile.TemporaryDirectory() as tmp:
        env, plan = _degraded_price_session(tmp, pathlib.Path(tmp) / "coach")
        assert plan["input"]["price_feed"]["recovery"] == {
            "attempted": False, "outcome": "not_attempted"}

        declared = _run("prepare", _OFFLINE_MOCK, "--root", pathlib.Path(tmp) / "coach",
                        "--language", "en", "--prices-unavailable",
                        "the exchange's own market-data site publishes no close for these",
                        env=env)
        assert declared.returncode == 0, declared.stdout + declared.stderr
        recovery = json.loads(declared.stdout)["review_plan"]["input"]["price_feed"]["recovery"]
        assert recovery["attempted"] is True and recovery["outcome"] == "declared_unavailable"
        assert "market-data site" in recovery["checked"]


def test_the_declaration_is_not_swallowed_by_the_undeclared_pending_session():
    """The #289/#369 class, a fourth time. The declaration necessarily arrives
    on a *second* prepare, after the first reported the gap. Without it in the
    fingerprint that rerun resumes the undeclared pending session, returns its
    stale plan, and the only thing separating a skipped step from an honest
    dead end is silently discarded — leaving the gate still refusing a run that
    did in fact declare.

    The session id legitimately does not move: it is content-addressed from
    engine state, and a declaration about the outside world changes no number.
    The plan on disk is what must carry it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _degraded_price_session(tmp, root)
        declared = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en",
                        "--prices-unavailable", "checked the listing venue's own site", env=env)
        out = json.loads(declared.stdout)
        assert out["status"] != "resumed", \
            "prepare --prices-unavailable must not resume the undeclared session unchanged"
        stored = session_engine.load_pending(str(root), out["session_id"])["plan"]
        assert stored["input"]["price_feed"]["recovery"]["attempted"] is True, \
            "the declaration has to reach the plan the draft gate reads, not just stdout"
        again = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en",
                     "--prices-unavailable", "checked the listing venue's own site", env=env)
        assert json.loads(again.stdout)["status"] == "resumed", \
            "the same declaration rerun stays idempotent at its own fingerprint"
        assert plan["session_id"] == out["session_id"], \
            "a declaration about the outside world moves no engine number, so no new id"


def test_a_card_built_on_a_skipped_price_recovery_is_refused():
    """The gate, on the shared draft path so `finalize` called directly cannot
    walk around `preview`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env, plan = _degraded_price_session(tmp, root)
        answers = pathlib.Path(tmp) / "answers.json"
        answers.write_text(json.dumps(_answers_for_plan(plan)), encoding="utf-8")
        narrative = pathlib.Path(tmp) / "narrative.json"
        narrative.write_text(json.dumps(_narrative_for_plan(plan)), encoding="utf-8")
        for command in ("preview", "finalize"):
            run = _run(command, "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers, "--narrative", narrative, env=env)
            assert run.returncode != 0, (command, run.stdout)
            error = json.loads(run.stdout)["error"]
            assert "no price recovery was ever attempted" in error, (command, error)
            assert "--prices-unavailable" in error, \
                f"{command}: the refusal must name both ways out, not only the envelope"


def test_a_declared_dead_end_delivers_the_degraded_card():
    """The counterweight, and what keeps this from being the hard block #357
    ruled out: a host that genuinely cannot look anything up says so once and
    the review completes. The user is never asked for anything."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env = _offline_env(tmp)
        run = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en",
                   "--prices-unavailable", "no market-data source reachable from this host",
                   env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        answers = pathlib.Path(tmp) / "answers.json"
        answers.write_text(json.dumps(_answers_for_plan(plan)), encoding="utf-8")
        narrative = pathlib.Path(tmp) / "narrative.json"
        narrative.write_text(json.dumps(_narrative_for_plan(plan)), encoding="utf-8")
        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers, "--narrative", narrative, env=env)
        assert preview.returncode == 0, preview.stdout + preview.stderr


def test_an_already_committed_price_degraded_session_still_replays():
    """The gate lives on the pending branch only. A session committed before
    this rule existed carries no `recovery` key at all, and refusing its
    idempotent finalize replay would break the documented no-op — punishing a
    card that reached the user long before there was anything to prevent."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env = _offline_env(tmp)
        run = _run("prepare", _OFFLINE_MOCK, "--root", root, "--language", "en",
                   "--prices-unavailable", "no market-data source reachable from this host",
                   env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        answers = pathlib.Path(tmp) / "answers.json"
        answers.write_text(json.dumps(_answers_for_plan(plan)), encoding="utf-8")
        narrative = pathlib.Path(tmp) / "narrative.json"
        narrative.write_text(json.dumps(_narrative_for_plan(plan)), encoding="utf-8")
        # The first commit is an ordinary review and takes the ordinary two-step
        # lifecycle (#628). The replay deliberately does not: it runs after the
        # pending directory is gone, which is precisely the already-committed
        # branch both gates exempt, and calling it directly is what proves that.
        first = _run_finalize("--root", root, "--session-id", plan["session_id"],
                              "--answers", answers, "--narrative", narrative, env=env)
        assert first.returncode == 0, first.stdout + first.stderr
        replay = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                      "--answers", answers, "--narrative", narrative, env=env)
        assert replay.returncode == 0, replay.stdout + replay.stderr

        # The predicate itself is fail-closed on an absent `recovery` key, which
        # is exactly the shape of a plan written before #623. That is correct on
        # the pending path and wrong on the committed one, and the placement is
        # what separates them — a committed bundle cannot be forged into this
        # shape here (its manifest hash refuses), so the predicate's own
        # posture is asserted directly and the call sites carry the reason.
        legacy = {"input": {"price_feed": {
            "request": {"tickers": ["ACME"]},
            "provenance": {"mode": "unavailable"}}},
            "engine_card": {"price_provenance": {"mode": "unavailable"}}}
        try:
            review_engine._refuse_a_card_built_on_a_skipped_price_recovery(legacy)
        except review_engine.ReviewError:
            pass
        else:
            raise AssertionError("an absent recovery key must read as not attempted")


def test_the_gate_is_silent_when_prices_were_not_the_problem():
    """A fully priced review must never meet this refusal. Without the
    `request` and `price_retrieval_blocked` conditions the gate would fire on
    every ordinary review that happens to be missing one benchmark."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        run = _run("prepare", "--root", root, "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        review_engine._refuse_a_card_built_on_a_skipped_price_recovery(plan)


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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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


def test_engine_version_dirty_ignores_untracked_but_still_catches_a_real_edit():
    """#747: an untracked file must never flip engine_version.dirty, but an
    actual edit to a tracked file still must.

    The bug: the QA runbook's mandated HOME replacement makes git lose the
    account's global excludes file, so a locally-ignored file (e.g.
    `.claude/settings.local.json`) starts showing up as `??` -- untracked --
    and every isolated QA run was reported dirty on an otherwise clean
    checkout. The fix scopes `dirty` to tracked-file state only
    (`git status --porcelain --untracked-files=no`), matching
    `git describe --dirty`'s own convention, so an untracked file -- whichever
    reason it is untracked -- can no longer manufacture the flag.

    This drives real `git` against a throwaway repo rather than asserting a
    flag was merely passed to a mocked subprocess call, because the actual
    defect was in git's real behavior, not in this function's control flow.
    `_engine_version`'s `repo_root` parameter exists only so this test can
    point it somewhere other than this skill's own checkout; every production
    call site still omits it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp)

        def _git(*args):
            result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                                    text=True, timeout=10)
            assert result.returncode == 0, f"git {args} failed: {result.stderr}"
            return result

        _git("init", "-q")
        _git("config", "user.email", "test@example.com")
        _git("config", "user.name", "Test")
        tracked = repo / "tracked.txt"
        tracked.write_text("v1\n", encoding="utf-8")
        _git("add", "tracked.txt")
        _git("commit", "-q", "-m", "initial")

        saved = review_engine._ENGINE_VERSION
        try:
            review_engine._ENGINE_VERSION = None
            clean = review_engine._engine_version(repo_root=str(repo))
            assert clean["source"] == "git", "a freshly committed throwaway repo must resolve to the git source"
            assert clean["dirty"] is False, "a fresh commit with nothing changed must not be dirty"

            # The #747 shape: an untracked file, however it got there, must
            # not move the flag.
            (repo / "untracked_scratch.tmp").write_text("noise\n", encoding="utf-8")
            review_engine._ENGINE_VERSION = None
            still_clean = review_engine._engine_version(repo_root=str(repo))
            assert still_clean["dirty"] is False, \
                "an untracked file must not report the checkout as dirty (#747)"

            # The honest signal this fix must not lose: a real maintainer
            # edit to a tracked file still fires.
            tracked.write_text("v2\n", encoding="utf-8")
            review_engine._ENGINE_VERSION = None
            edited = review_engine._engine_version(repo_root=str(repo))
            assert edited["dirty"] is True, \
                "an edited tracked file must still report the checkout as dirty"

            # And staging a new file -- the other half of "a real edit" --
            # still fires too, even though it began life untracked.
            _git("add", "tracked.txt")
            (repo / "new_tracked.txt").write_text("v1\n", encoding="utf-8")
            _git("add", "new_tracked.txt")
            review_engine._ENGINE_VERSION = None
            staged = review_engine._engine_version(repo_root=str(repo))
            assert staged["dirty"] is True, \
                "a staged new file must still report the checkout as dirty"
        finally:
            review_engine._ENGINE_VERSION = saved


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
    # The catalog entry itself, not a fragment of its wording (#623): this test
    # asserts which branch fired, and `tests/copy_corpus.py`'s golden owns what
    # the sentence says.
    unlock = card_renderer.load_copy("en")["block_missing"]["rule_structural"]
    assert unlock in card_renderer.render_private(_bundle("structural", 2))
    # behavioral (14 round trips) with a short-span insufficient flag must not be
    # framed as an opening structural check.
    assert unlock not in card_renderer.render_private(_bundle("behavioral", 14))
    # #623/#617's other half, and the one a presence-only test cannot see: an
    # invitation is absent when nothing further is needed. A complete
    # behavioral review names no unreachable answer at all, because a
    # manufactured invitation is the same defect as a manufactured disclosure.
    behavioral = card_renderer.render_private(_bundle("behavioral", 14))
    for key in ("rule_structural", "snapshot_unlock"):
        assert card_renderer.load_copy("en")["block_missing"][key] not in behavioral, key


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

        def gated_append(path, events, **kwargs):
            if os.path.abspath(path) == os.path.abspath(ledger_path):
                append_entered.set()
                if not release_append.wait(5):
                    raise RuntimeError("timed out waiting to release trade append")
            return real_append(path, events, **kwargs)

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
        assert [row["type"] for row in rows] == ["trade", "snapshot"]
        assert rows[1]["source"] == ledger_engine.DERIVED_BOOK_SOURCE
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


def _cross_split_ledger(root):
    """The #550 repro: two pre-split buys, a pre-split trim, then a ~10% post-split trim.

    Raw quantities: 90 + 30 - 20 - 100 == 0, which is why the last sale reads as
    a full liquidation. In post-split terms it is 100 of 1000 shares.
    """
    (root / "ledger.jsonl").write_text("".join(
        json.dumps(row) + "\n" for row in [
            {"type": "trade", "date": "2023-01-10", "ticker": "NVDA",
             "action": "buy", "qty": 90, "price": 150.0},
            {"type": "trade", "date": "2023-11-15", "ticker": "NVDA",
             "action": "buy", "qty": 30, "price": 480.0},
            {"type": "trade", "date": "2024-05-20", "ticker": "NVDA",
             "action": "sell", "qty": 20, "price": 950.0},
            {"type": "trade", "date": "2026-07-28", "ticker": "NVDA",
             "action": "sell", "qty": 100, "price": 197.0},
        ]), encoding="utf-8")


def test_exit_capture_reads_the_split_history_this_review_already_applied():
    """#550: the ledger stores as-transacted quantities, so exit capture has to be
    told what the splits were or it subtracts two different share bases.

    The map is not fetched here — it is the one the engine already applied to
    its own analytics and froze into ``state``, so the two readers of "how many
    shares" cannot disagree about what a split did. Without it this trim reads
    as ``kind: "full"``, which permanently closes the thesis (thesis.py) and
    prints "fully exited" on the saved card.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "unadjusted"
        root.mkdir()
        _cross_split_ledger(root)
        recent, _due, _backlog, _meta = review_engine._prepare_exit_capture(
            str(root), {"date_end": "2026-07-28"}, True)
        assert [row["kind"] for row in recent] == ["full"], \
            f"control: with no split history the shipped defect still reproduces {recent}"

        adjusted_root = pathlib.Path(tmp) / "adjusted"
        adjusted_root.mkdir()
        _cross_split_ledger(adjusted_root)
        state = {"date_end": "2026-07-28", "splits": {"NVDA": [["2024-06-10", 10]]}}
        recent, due, backlog, _meta = review_engine._prepare_exit_capture(
            str(adjusted_root), state, True)
        assert recent == [] and due == [] and backlog is None, \
            f"100 of 1000 shares is a 10% trim, not an exit of any kind: {recent}"
        assert not (adjusted_root / "revisit.jsonl").exists(), \
            "nothing was an exit, so nothing may enter the 30/60/90 queue"


def test_exit_capture_fails_closed_on_unreadable_split_history():
    """A split ratio is a multiplier on a share count. Dropping a bad one silently
    would hand back a confident wrong number, which is the defect, not the fix."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir()
        _cross_split_ledger(root)
        state = {"date_end": "2026-07-28", "splits": {"NVDA": [["2024-06-10", 0]]}}
        try:
            review_engine._prepare_exit_capture(str(root), state, True)
            raise AssertionError("an unusable split ratio must not be quietly ignored")
        except review_engine.ReviewError as exc:
            assert "split history" in str(exc), str(exc)
        assert not (root / "revisit.jsonl").exists(), \
            "a refused exit capture must not have written a partial queue"


def test_prepare_carries_state_splits_into_the_exit_question():
    """End to end through the CLI: the frozen map has to survive the plan boundary.

    The delivery surface is the exit-revisit question the agent reads to the
    user verbatim, so this asserts on the question text rather than on an
    internal value.
    """
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = pathlib.Path(tmp) / "cross-split.csv"
        csv_path.write_text("\n".join([
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency",
            "NVDA,BUY,90,150,2023-01-10,Trade,US,USD",
            "NVDA,BUY,30,480,2023-11-15,Trade,US,USD",
            "NVDA,SELL,20,950,2024-05-20,Trade,US,USD",
            "NVDA,SELL,100,197,2026-07-28,Trade,US,USD",
        ]) + "\n", encoding="utf-8")
        card, state_path = _artifacts(tmp)
        state = json.loads(pathlib.Path(state_path).read_text(encoding="utf-8"))
        state["date_end"] = "2026-07-28"
        state["splits"] = {"NVDA": [["2024-06-10", 10]]}
        pathlib.Path(state_path).write_text(json.dumps(state, ensure_ascii=False),
                                            encoding="utf-8")
        root = pathlib.Path(tmp) / "coach"
        run = _run("prepare", csv_path, "--root", root, "--language", "en",
                   "--card-json", card, "--state-json", state_path)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        exits = [q for q in plan["question_queue"] if q["kind"] in ("revisit", "due_revisit")]
        assert exits == [], f"a 10% trim must not surface as an exit to answer for: {exits}"
        assert "exited" not in json.dumps(plan["question_queue"], ensure_ascii=False), \
            "no question may describe this position as exited"


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
        # #628: both racing processes finalize the same pending session, so the
        # preview receipt has to exist before either starts. It is written once
        # here rather than through `_run_finalize` because what this test races
        # is two *finalize* processes, not two lifecycles.
        preview = _run("preview", "--root", root, "--session-id", plan["session_id"],
                       "--answers", answers_path, "--narrative", narrative_path)
        assert preview.returncode == 0, preview.stdout + preview.stderr

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


def test_zero_denominator_renderer_skips_structural_claims_and_keeps_same_day_fact():
    """#329: defensive rendering honors producer applicability across locales."""
    same_day = {"dim": "持有時間", "tier": 2, "triggered": True,
                "severity": 1.0, "median_hold": 0, "all_same_day": True}
    sizing = {"dim": "部位 sizing", "tier": 1, "applicable": False,
              "triggered": True, "severity": 1.0, "max_ticker": "GHOST", "max_pct": 1.0}
    diversification = {"dim": "分散", "tier": 2, "applicable": False,
                       "triggered": True, "severity": 1.0, "n": 0}
    card = {"dims_raw": [sizing, diversification, same_day],
            "top_holes": [{"dim": "部位 sizing", "raw": sizing},
                          {"dim": "持有時間", "raw": same_day}]}
    assert [hole["dim"] for hole in card_renderer._applicable_holes(card)] == ["持有時間"]
    assert card_renderer.rule_grounding_facts(card, "position_sizing") is None
    assert "GHOST" not in (card_renderer._best_strength(card, "en") or "")
    # The hole's own applicability is authoritative even when a legacy card
    # omitted dims_raw.  Public rendering must not revive that claim.
    stale_hole_card = {"top_holes": [{"dim": "部位 sizing", "raw": sizing}]}
    assert card_renderer._applicable_holes(stale_hole_card) == []
    assert "position sizing" not in card_renderer.render_public(
        {"language": "en", "engine_card": stale_hole_card})
    selectable = review_engine._candidate_rules(
        {"candidate_rules": [{"dim": "部位 sizing", "rule": "stale", "applicable": False}],
         "top_holes": [{"dim": "部位 sizing", "lens_rule": "stale", "raw": sizing}]},
        {"metrics": {"max_pos_pct": 1.0}}, "en")
    assert selectable == [], selectable
    for language, expected in (("en", "same-day"), ("zh-TW", "當日進出"),
                               ("zh-CN", "当日进出")):
        line = card_renderer._hole_line({"raw": same_day}, language)
        assert expected in line, (language, line)
        public = card_renderer.render_public({"language": language, "engine_card": card})
        assert expected in public, (language, public)


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
        finalized = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        retry = _run_finalize("--root", root, "--session-id", plan["session_id"],
                     "--answers", answers_path, "--narrative", narrative_path)
        assert retry.returncode == 0 and json.loads(retry.stdout)["status"] == "no-op"
        conflicting = _answers(plan, commitment="candidate_0")
        conflicting["observations"].append("different retry payload")
        answers_path.write_text(json.dumps(conflicting, ensure_ascii=False), encoding="utf-8")
        rejected = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
            assert "開始這次復盤時，你已有 1 次完成復盤。" in opening[0], \
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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
    return _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        args = ("--root", root, "--session-id", plan["session_id"],
                "--answers", answers_path, "--narrative", narrative_path)

        def rows():
            text = (root / "conditions.jsonl").read_text(encoding="utf-8")
            return [json.loads(line) for line in text.splitlines() if line.strip()]

        assert _run_finalize(*args).returncode == 0
        assert len(rows()) == 1
        _run_finalize(*args)                          # documented-safe retry
        assert len(rows()) == 1, "an identical retry must not append a second row"

        answers["commitment"]["condition"] = dict(_CONDITION, criterion="sell if margin drops")
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        changed = _run_finalize(*args)
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
    return _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        args = ("--root", root, "--session-id", plan["session_id"],
                "--answers", answers_path, "--narrative", narrative_path)
        assert _run_finalize(*args).returncode == 0
        assert len(_check_rows(root)) == 1
        _run_finalize(*args)
        assert len(_check_rows(root)) == 1, "an identical retry must not append a second reading"

        answers["condition_checks"][0]["check"]["observation"]["value"] = 12.0
        _write_json(tmp, "answers.json", answers)
        changed = _run_finalize(*args)
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
    """An opening snapshot, then the review that reveals the holding's real
    cycle start.

    Returns ``(provisional_cycle_id, card, state, history)``. The thesis is
    relinked from the snapshot's provisional cycle id to the revealed one, and
    the relink exists **only in the plan** until that review is finalized — which
    is the whole point of the scenario below.

    #549 note: the reveal used to arrive with a transaction CSV, because an
    opening snapshot the user marked partial never became a ledger anchor and
    the ledger kept replaying the trades that showed the real open date. With
    the completeness flag gone, every declaration anchors, and an anchor
    absorbs the trades dated before it -- so the transaction import can no
    longer surface an earlier cycle start on its own. The reveal is expressed
    here through the developer `--state-json` route instead: what this test
    exists for is the plan/card join over a relink (#444 rounds 3-4), not the
    input that produced one.
    """
    opening, _path = _snapshot_prepare(tmp, root, payload={
        "as_of": "2026-07-16",
        "positions": [{"ticker": "PLTR", "shares": 10, "avg_cost": 100,
                       "market": "US", "currency": "USD"}]}, language="en")
    answers = _write_json(tmp, "relink-open-answers.json",
                          _snapshot_answers(opening, commitment="skip"))
    narrative = _write_json(tmp, "relink-open-narrative.json", _snapshot_narrative(opening))
    done = _run_finalize("--root", root, "--session-id", opening["session_id"],
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
        "as_of": "2026-07-18", "derived_from": "trades_csv",
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
        run = _run("prepare", "--root", root, "--language", "en",
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
              "cycle_provenance": {"kind": "snapshot_cycle_relink",
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
        assert _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        assert ([row["type"] for row in ledger_rows] == ["trade"] * 8 + ["snapshot"]
                and not (root / "theses.jsonl").exists()), \
            "validated trade facts and the book they derive persist at prepare, " \
            "but answers do not project before finalize"

        resumed = _run("resume", "--root", root, "--session-id", plan["session_id"])
        resumed_plan = json.loads(resumed.stdout)["plan"]
        assert resumed_plan["question_queue"] == plan["question_queue"], \
            "resume returns the exact same ranked questions without re-ingesting"
        assert len((root / "ledger.jsonl").read_text().splitlines()) == 9

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

        finalized = _run_finalize("--root", root, "--session-id", plan["session_id"],
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


def test_ingest_trades_stamps_recorded_at_from_review_period_not_wall_clock():
    """#472: a ledger row must carry *when the system learned this*, separate
    from the trade's own `date`. `_ingest_trades` has `state` in hand, so it
    must inject `state["date_end"]` (here `_artifacts`' fixed 2026-07-14)
    rather than let `append_events` fall back to `dt.date.today()` — a
    wall-clock stamp would make every stored ledger row vary by run day,
    exactly the flakiness #472's determinism constraint forbids."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card, state = _artifacts(tmp)
        csv_path = pathlib.Path(tmp) / "trades.csv"
        csv_path.write_text("\n".join([
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency",
            "BIG,BUY,10,100,2026-01-05,Trade,US,USD",
        ]) + "\n", encoding="utf-8")
        run = _run("prepare", csv_path, "--root", root, "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        rows = [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]
        # #549: the trade, then the book that import derived, both stamped from
        # the same review period.
        assert [row["type"] for row in rows] == ["trade", "snapshot"]
        assert rows[0]["date"] == "2026-01-05", "the trade's own date must stay untouched"
        for row in rows:
            assert row["recorded_at"] == "2026-07-14", (
                "recorded_at must come from the review period's date_end "
                "(state['date_end']), not the trade's own historical date and not wall-clock today"
            )
        assert rows[1]["source"] == ledger_engine.DERIVED_BOOK_SOURCE
        assert rows[1]["as_of"] == "2026-07-14", (
            "the book is recorded at the period it was derived in, never behind its own trades"
        )


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


def test_ticker_importance_ranking_uses_engine_fx_for_mixed_currency_amounts():
    """#664: the initial-thesis "largest cost" ranking must hold the same
    discipline the sibling test above already holds for exit-notional ranking.
    The `position_cost` fallback used to compare `pos["cost"]` -- a raw
    native-currency face value -- directly across tickers, so a TWD position
    could outrank a larger USD one purely because TWD amounts carry more
    zeros. It must read `currency_meta.fx` (#649's already-resolved rate)
    exactly like `_exit_importance` does, never the raw amount.

    Two `currency_meta` shapes, both must produce the identical answer. The
    first carries no `aggregate_currency` key at all -- reachable through the
    `--card-json`/`--state-json` adapter lane, or any future/differently
    shaped producer. `_normalized_position_cost` must resolve the aggregate
    through the one shared reader, `card_renderer._currency` (its own
    documented `or "USD"` fallback), never by defaulting to the *position's
    own* currency -- that would make every position trivially "already the
    aggregate" and silently turn normalization off for the whole book, the
    identity-factor failure #649 removed from the aggregate reader itself,
    reintroduced one layer up. The second shape is the explicit key both of
    the engine's two current real producers (`trade_recap.build_state`,
    `snapshot_adapter`) always populate it with, pinned so the common case
    stays covered too.
    """
    positions = {
        "TWX": {"cost": 900000.0, "currency": "TWD"},
        "USX": {"cost": 30000.0, "currency": "USD"},
    }
    state = {"holdings": {"positions": positions}}
    for label, meta in [
        ("no aggregate_currency key", {"mixed": True, "currencies": ["TWD", "USD"],
                                       "fx": {"TWD": 0.0317}}),
        ("explicit aggregate_currency", {"mixed": True, "aggregate_currency": "USD",
                                         "currencies": ["TWD", "USD"], "fx": {"TWD": 0.0317}}),
    ]:
        card = {"currency_meta": meta, "ticker_diagnosis": []}
        tw_importance, tw_basis = review_engine._ticker_importance(card, state, "TWX")
        us_importance, us_basis = review_engine._ticker_importance(card, state, "USX")
        assert tw_basis == us_basis == "position_cost", (label, tw_basis, us_basis)
        assert abs(tw_importance - 28530.0) < 1e-6, (label, tw_importance)
        assert us_importance == 30000.0, (label, us_importance)
        assert us_importance > tw_importance, \
            f"{label}: a smaller USD position must outrank a larger raw-TWD one once normalized"


def test_ticker_importance_refuses_rather_than_falls_back_to_raw_units_on_missing_fx():
    """#664: a missing rate must never resolve to an identity factor of 1.0 --
    the same "the identity factor is removed wherever currencies differ, not
    merely guarded upstream" discipline #649 holds for the aggregate reader.
    Covers both `currency_meta` shapes -- no `aggregate_currency` key and the
    explicit key -- since both must fail the same way once a rate is missing.
    This is exercised directly because the real pipeline cannot reach this
    state: a book the engine reports `mixed` already carries a complete `fx`
    map for every held currency, since `trade_recap.usd_view` refuses the
    whole review before a card/state with a gap could ever be built."""
    state = {"holdings": {"positions": {"TWX": {"cost": 900000.0, "currency": "TWD"}}}}
    for label, meta in [
        ("no aggregate_currency key", {"mixed": True, "fx": {}}),
        ("explicit aggregate_currency", {"mixed": True, "aggregate_currency": "USD", "fx": {}}),
    ]:
        card = {"currency_meta": meta, "ticker_diagnosis": []}
        importance, basis = review_engine._ticker_importance(card, state, "TWX")
        assert importance is None and basis == "fx_unavailable", (label, importance, basis)


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
        final = _run_finalize("--root", root, "--session-id", first_plan["session_id"],
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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
                  "rule": "下單前先檢查單一風險部位上限", "metric_key": "max_pos_pct",
                  "metric_value": 0.51, "goal": "down"}}},
              "engine_state": {"metrics": {"max_pos_pct": 0.48}}}
    zh = card_renderer._reconciliation_lines(bundle, "zh-TW")
    assert zh and "上次你承諾" in zh[0] and "51%" in zh[0] and "48%" in zh[0], \
        "#151: the card must open against last time's commitment with verbatim then/now values"
    en = card_renderer._reconciliation_lines(bundle, "en")
    assert en and "Last time you committed" in en[0] and "51%" in en[0]
    assert "max_pos_pct" not in zh[0] and "max_pos_pct" not in en[0], \
        "A-12: internal metric keys never appear on the card"
    assert "最大單一部位比重" in zh[0] and "改善" in zh[0]
    assert "largest-position weight" in en[0] and "improved" in en[0]
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
                       "rule": "下單前先檢查單一風險部位上限", "metric_key": "max_pos_pct",
                       "metric_value": 0.47, "goal": "down"}}},
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


def test_scan_initial_snapshot_conflicts_fails_closed_on_unreadable_ledger_rows():
    """#470: scan_initial_snapshot_conflicts previously read the ledger through
    session._read_jsonl, which drops any line that fails json.loads with no
    count and no signal at all -- strictly weaker than even pre-#462
    load_ledger. A ledger whose *only* row is not valid JSON at all (as
    opposed to valid-JSON-but-unknown-type, which this scan already caught --
    see the "mystery_event" case in test_initial_snapshot_boundary_layers_
    share_one_verdict above) made _read_jsonl return [], so the scan's own
    loop never ran and found nothing to flag: both boundary layers silently
    treated real, unreadable ledger history as if the root had none at all,
    admitting the declaration as initial onboarding instead of routing it
    into reconciliation the way any other non-identical row already does."""
    bundle = _runtime_snapshot_bundle("2026-07-18__snapshot-470")
    anchor = bundle["engine_state"]["snapshot_anchor"]
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir()
        (root / "ledger.jsonl").write_text("not json at all\n", encoding="utf-8")

        assert session_engine.scan_initial_snapshot_conflicts(str(root), anchor) == ["ledger"], \
            "an unreadable row must register as a ledger conflict, same as a mismatching one"

        try:
            review_engine._validate_initial_snapshot_root(str(root), anchor)
            raise AssertionError("prepare-time check must fail closed on an unreadable ledger row")
        except review_engine.ReviewError as exc:
            assert "unreadable row(s)" in str(exc), str(exc)

        try:
            session_engine._assert_initial_snapshot_boundary(str(root), bundle)
            raise AssertionError("finalize-time check must fail closed on an unreadable ledger row")
        except session_engine.SessionError as exc:
            # This bundle carries no frozen snapshot_reconciliation, so this
            # layer stops at the "must reconcile" gate before it would reach
            # its own ledger.load_ledger call -- the same shape as the
            # pre-existing "mystery_event" case above. What is being proven
            # here is that it stops at all, instead of returning normally and
            # silently admitting the declaration.
            assert str(exc) == session_engine.INITIAL_SNAPSHOT_CONFLICT, str(exc)


def test_scan_initial_snapshot_conflicts_does_not_relabel_a_non_integrity_valueerror():
    """Reviewing #469/#470: every #462 call site (the four in review.py, plus
    scan_initial_snapshot_conflicts and _assert_initial_snapshot_boundary's
    own ledger.load_ledger call, both in session.py) must catch narrowly --
    ledger.LedgerIntegrityError, not bare ValueError -- because
    LedgerIntegrityError subclasses ValueError but not everything
    ledger.load_ledger can raise is a row-integrity problem safe to relabel
    as a "ledger" conflict. A ledger.jsonl with invalid UTF-8 bytes makes
    load_ledger raise UnicodeDecodeError while reading the file itself (a
    ValueError subclass, but not a LedgerIntegrityError); a wide except would
    swallow that as if it were an ordinary bad row instead of letting it
    surface as what it actually is."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "ledger.jsonl").write_bytes(b"\xff\xfe not valid utf-8 at all\n")
        anchor = {
            "type": "snapshot", "as_of": "2026-07-17", "source": "user_declared",
            "is_complete": True,
            "positions": [{"ticker": "NVDA", "shares": 100, "avg_cost": 100.0,
                           "market": "US", "currency": "USD"}],
        }
        try:
            session_engine.scan_initial_snapshot_conflicts(str(root), anchor)
            raise AssertionError(
                "an undecodable ledger file must not be silently swallowed as a plain conflict")
        except UnicodeDecodeError:
            pass  # correct: only ledger.LedgerIntegrityError becomes a "ledger" conflict


def test_returning_private_card_shows_completed_history_snapshot_only_locally():
    import card_renderer
    progress = {"completed_reviews_before_start": 3, "returning": True}
    bundle = {
        "review_plan": {"state_snapshot": {
            "prior_commitment": {"rule": "Keep the position bounded",
                                 "metric_key": "max_pos_pct", "metric_value": 0.51,
                                 "goal": "down"},
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
        "開始這次復盤時，你已有 3 次完成復盤。"
    ], \
        "a returning user still sees progress after previously skipping a commitment"
    first = {"review_plan": {"state_snapshot": {"review_progress": {
        "completed_reviews_before_start": 0, "returning": False,
    }}}}
    assert card_renderer._review_opening_lines(first, "en") == [], \
        "first reviews must not get a returner milestone"


def test_reconciliation_legacy_or_unknown_metric_omits_unowned_semantics():
    import card_renderer
    for prior in (
        {"rule": "Keep the position bounded", "metric_key": "max_pos_pct", "metric_value": 0.51},
        {"rule": "Keep the position bounded", "metric_key": "unknown_metric", "metric_value": 0.51,
         "goal": "down"},
    ):
        bundle = {"review_plan": {"state_snapshot": {"prior_commitment": prior}},
                  "engine_state": {"metrics": {prior["metric_key"]: 0.48}}}
        line = card_renderer._reconciliation_lines(bundle, "en")[0]
        assert line == 'Last time you committed: "Keep the position bounded".'
        assert "0.51" not in line and "0.48" not in line
        assert "improved" not in line and "worsened" not in line


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


def test_weekly_memory_surfaces_render_private_only_with_impact_framing():
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
            # #670: the focus line prints `impact` — the figure that ranked it
            "impact": -4200.0, "currency": "USD",
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
                     "Across 3 price-covered full exits, the average post-exit move was -3.0 pp; 1 later rose",
                     "Backlog focus: FOCUSSECRET, full exit on 2025-06-01",
                     "That decision moved the account by -$4,200 using prices frozen on 2026-07-15",
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

        finalized = _run_finalize("--root", root, "--session-id", plan["session_id"],
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

        retry = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        finalized = _run_finalize("--root", root, "--session-id", plan["session_id"],
                         "--answers", skipped_a, "--narrative", narrative)
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        bundle_path = pathlib.Path(json.loads(finalized.stdout)["path"]) / "bundle.json"
        before_retry = bundle_path.read_text(encoding="utf-8")
        assert "headline_motive_events" not in json.loads(before_retry)
        retry = _run_finalize("--root", root, "--session-id", plan["session_id"],
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

        finalized = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        finalized = _run_finalize("--root", root, "--session-id", plan["session_id"],
                         "--answers", answers_path, "--narrative", narrative_path)
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        bundle_path = pathlib.Path(json.loads(finalized.stdout)["path"]) / "bundle.json"
        before_retry = bundle_path.read_text(encoding="utf-8")
        assert "exit_consistency_events" not in json.loads(before_retry)
        retry = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
    run = _run_finalize("--session-id", plan["session_id"], "--root", root,
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
        run = _run_finalize("--session-id", plan2["session_id"], "--root", root,
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
    key the plan actually requires, exactly as a real degraded review must.

    `--prices-unavailable` is what a genuinely priceless host does (#623): this
    fixture's host has no market data at all, so recovery is declared as
    attempted-and-empty rather than skipped. Without it the draft path refuses,
    which is the point — a degraded card is a dead end that was stated, never a
    step nobody took."""
    run = _run("prepare", csv_path, "--root", root, "--route", "weekly_review",
               "--prices-unavailable", "no market-data source reachable from this host",
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
    final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        run = _run_finalize("--session-id", plan["session_id"], "--root", root,
                   "--answers", a_path, "--narrative", n_path)
        payload = json.loads(run.stdout)
        assert payload["status"] == "error" and "invalid emotion" in payload["error"]
        assert not (pathlib.Path(root) / "sessions" / plan["session_id"]).exists()

        answers["thesis_updates"] = [_base_thesis_update({"horizon": "季"})]
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        run = _run_finalize("--session-id", plan["session_id"], "--root", root,
                   "--answers", a_path, "--narrative", n_path)
        payload = json.loads(run.stdout)
        assert payload["status"] == "error" and "invalid horizon" in payload["error"]
        assert not (pathlib.Path(root) / "sessions" / plan["session_id"]).exists()

        positions = (plan["engine_state"]["holdings"]["positions"])
        legacy = [_base_thesis_update({"horizon": "季"})]
        assert review_engine.thesis.validate_thesis_updates(legacy, positions) == legacy, \
            "plans prepared before stable IDs must remain retry-compatible"


def test_schemas_cover_due_revisit_and_resolutions():
    """Contract-sync pin (docs/maintainer-guide.md): the published schemas must
    describe what
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
            run = _run_finalize("--session-id", plan["session_id"], "--root", root,
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
        run = _run_finalize("--root", root, "--session-id", plan["session_id"],
                   "--answers", a_path, "--narrative", n_path)
        payload = json.loads(run.stdout)
        assert payload["status"] == "error" and "must remain inferred" in payload["error"]

        answers["thesis_updates"] = [dict(row) for row in deltas]
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        run = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
             "trade-evaluation.schema.json", "behavior-verdict.schema.json",
             "portfolio-basis.schema.json", "book-refresh.schema.json",
             # #479 Wave A: the bounded DecisionContext envelope, declared once
             # here and $ref-ed by trade-evaluation.schema.json's `context`
             # property -- the same treatment `premise` already gets from
             # trade-premise.schema.json, so the agent-facing shape has exactly
             # one definition.
             "decision-context.schema.json",
             # #414 Wave A: a new, standalone schema for the semantically-richer
             # --agent-case claim envelope engine/answer_provenance.py checks.
             # #479 Wave B $ref-ed it from trade-evaluation.schema.json's own
             # `agent_case` property (the `context`/decision-context.schema.json
             # precedent just above) rather than restating it a second time,
             # and wired engine/answer_provenance.py::validate_agent_case into
             # cmd_consider itself -- see answer-provenance.schema.json's own
             # description for why this stayed a new file rather than an edit
             # to trade-evaluation.schema.json's old, narrower claim $defs.
             "answer-provenance.schema.json",
             # #479 Wave B cut 2: the `challenge` block cmd_consider emits
             # beside the stored row -- what this answer owes the user.
             # Emitted, never stored, so it is deliberately NOT $ref-ed from
             # trade-evaluation.schema.json the way `context` and `agent_case`
             # above are: a row carrying it would be a derived duplicate of
             # fields that row already freezes.
             "evaluation-challenge.schema.json"}
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
            cli = _Args(session_id=plan3["session_id"], root=root,
                        answers=str(a_path), narrative=str(n_path))
            # #628: in-process for the same reason the rest of this test is —
            # a subprocess would not see the monkeypatch. `preview` renders and
            # saves the pending bundle; it touches no projection writer, so it
            # cannot interfere with the lock this test is about.
            review_engine.cmd_preview(cli)
            review_engine.cmd_finalize(cli)

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
            run = _run_finalize("--session-id", plan2["session_id"], "--root", root,
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
    run = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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


_664_MIXED_BOOK_ROWS = [
    # Three closed US round trips -- only their count matters, so the tier
    # classifier (>= MIN_ROUND_TRIPS) reaches "behavioral" and the first-review
    # question band is not suppressed as a thin/structural file (#306). Dated
    # well before the open positions below so they never register as recent
    # exits and compete for a capture slot.
    "RTA,BUY,100,50.00,2024-01-05,Trade,US,USD",
    "RTA,SELL,100,60.00,2024-02-05,Trade,US,USD",
    "RTB,BUY,50,40.00,2024-01-06,Trade,US,USD",
    "RTB,SELL,50,45.00,2024-02-06,Trade,US,USD",
    "RTC,BUY,80,30.00,2024-01-07,Trade,US,USD",
    "RTC,SELL,80,35.00,2024-02-07,Trade,US,USD",
    # Four open, un-thesised positions -- two TW/TWD, two US/USD -- with no
    # existing thesis, so every one is an initial_thesis candidate.
    "3001.TW,BUY,900,1000.00,2024-03-01,Trade,TW,TWD",   # raw 900,000 TWD; normalized ~28,530
    "3002.TW,BUY,600,1000.00,2024-03-02,Trade,TW,TWD",   # raw 600,000 TWD; normalized ~19,020 (smallest)
    "USOPEN1,BUY,300,100.00,2024-03-03,Trade,US,USD",    # 30,000 USD (largest normalized)
    "USOPEN2,BUY,220,100.00,2024-03-04,Trade,US,USD",    # 22,000 USD
]
_664_MIXED_BOOK_CLOSES = [
    # Close == avg cost on every open position, so realized+unrealized impact
    # is ~0 and `ticker_diagnosis` (which drops |impact| < 1) never reports
    # them -- forcing `_ticker_importance`'s `position_cost` fallback, the
    # exact path #664 fixes, rather than the already-normalized pnl_impact
    # branch fed by usd_view's own `_u` arrays.
    {"ticker": "3001.TW", "close": 1000.0, "date": "2026-07-30", "currency": "TWD"},
    {"ticker": "3002.TW", "close": 1000.0, "date": "2026-07-30", "currency": "TWD"},
    {"ticker": "USOPEN1", "close": 100.0, "date": "2026-07-30", "currency": "USD"},
    {"ticker": "USOPEN2", "close": 100.0, "date": "2026-07-30", "currency": "USD"},
]


def test_initial_thesis_native_currency_label_and_normalized_cost_ranking_on_mixed_book():
    """#664, driven through the real CLI (CSV ingestion -> `trade_recap.main`'s
    `build_state` -> `review.py`'s question queue) rather than injected
    artifacts, so a regression in either the write side (build_state
    threading `cur_map` onto each position) or the read side
    (`_ticker_importance`/`_initial_thesis_question`) is caught.

    On this mixed TW/US first review, a TWD position's cost basis must reach
    the initial-thesis stem in its own currency -- never relabeled USD -- and
    the "largest cost" selection must rank on the FX-normalized value, not the
    raw face value. Reproduces the book shape from the issue: 3001.TW's raw
    TWD cost (900,000) is larger than 3002.TW's (600,000) and both dwarf the
    USD positions' raw figures (30,000 / 22,000), but at the review's own
    resolved rate (0.0317 USD per TWD) they convert to ~28,530 and ~19,020 --
    both inside the USD positions' range. The normalized ranking must
    interleave them (USOPEN1 > 3001.TW > USOPEN2 > 3002.TW), never group
    every TWD position ahead of every USD one by raw magnitude alone.

    This is the shape a real review actually produces: `trade_recap.main`'s
    `currency_meta` literal always carries an explicit `aggregate_currency`
    (verified below), so this test cannot by itself catch a card missing that
    key -- `test_ticker_importance_ranking_uses_engine_fx_for_mixed_currency_amounts`
    covers that shape directly. What only the real CLI proves is that
    `build_state` actually threads `cur_map` end to end and that the
    `position_cost` fallback -- not the already-normalized `ticker_diagnosis`
    branch -- is the one doing the work: `_664_MIXED_BOOK_CLOSES` prices every
    open position at exactly its own average cost, so realized+unrealized
    impact is ~0 and `ticker_diagnosis` (which drops `abs(impact) < 1`) omits
    all four of them, forced and asserted below rather than assumed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _offline_engine_env(tmp)
        csv_path, prices = _fx_case(tmp, "initial_thesis_664", _664_MIXED_BOOK_ROWS,
                                    fx={"TWD": 0.0317}, prices=_664_MIXED_BOOK_CLOSES)
        root = pathlib.Path(tmp) / "coach"
        run = _run("prepare", csv_path, "--root", root, "--language", "en",
                   "--prices", prices, env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _fx_plan(root)
        assert plan["route"] == "first_review"
        assert plan["engine_state"]["review_tier"]["tier"] == "behavioral", \
            "this fixture must actually clear the thin-file suppression (#306)"
        assert plan["engine_card"]["currency_meta"].get("aggregate_currency") == "USD", \
            "this fixture's own currency_meta carries the key -- the missing-key shape is covered separately"

        diagnosed = {row["ticker"] for row in plan["engine_card"].get("ticker_diagnosis") or []}
        for ticker in ("3001.TW", "3002.TW", "USOPEN1", "USOPEN2"):
            assert ticker not in diagnosed, \
                (f"{ticker} must be absent from ticker_diagnosis, or this test exercises the "
                 f"already-normalized pnl_impact branch instead of the position_cost fallback "
                 f"#664 fixes: {diagnosed}")

        initial = [q for q in plan["question_queue"] if q["kind"] == "initial_thesis"]
        assert initial, "a mixed-currency first review must still emit initial-thesis questions"

        by_ticker = {q["ticker"]: q for q in initial}
        assert by_ticker["3001.TW"]["currency"] == "TWD", by_ticker["3001.TW"]
        assert "TWD 900,000" in by_ticker["3001.TW"]["question"], by_ticker["3001.TW"]["question"]
        assert "USD" not in by_ticker["3001.TW"]["question"], \
            "a TWD position's stem must never relabel its native amount as USD"
        assert by_ticker["USOPEN1"]["currency"] == "USD"

        # Normalized order: USOPEN1 (30,000) > 3001.TW (~28,530) > USOPEN2
        # (22,000) > 3002.TW (~19,020, the smallest, trimmed by INITIAL_THESIS_LIMIT).
        assert [q["ticker"] for q in initial] == ["USOPEN1", "3001.TW", "USOPEN2"], \
            (f"selection must rank by normalized cost, not raw face value: "
             f"{[q['ticker'] for q in initial]}")
        rejected = plan["card_plan"]["question_selection"]["rejected"]
        assert {"id": review_engine._initial_thesis_id("3002.TW#2024-03-02#1"),
                "kind": "initial_thesis", "cycle_id": "3002.TW#2024-03-02#1",
                "reason": "initial_thesis_limit"} in rejected, \
            "3002.TW has the smallest normalized cost and must be the one trimmed, not by raw units"


def test_initial_thesis_selection_refuses_a_candidate_it_cannot_normalize_instead_of_ranking_it_raw():
    """#664: when the aggregate FX map has no rate for a held currency, the
    candidate must be refused from the ranked selection rather than compared
    on its raw native-currency magnitude. Exercised through `_question_queue`
    directly (the same pattern `test_weekly_review_quiet_week_backfills_...`
    above uses) because the real CLI cannot construct this state -- a book
    the engine reports `mixed` already has a complete `fx` map by the time a
    card exists at all."""
    card = {"currency_meta": {"mixed": True, "aggregate_currency": "USD", "fx": {}},
           "ticker_diagnosis": [], "thesis_questions": []}
    positions = {
        "TWX": {"cost": 900000.0, "currency": "TWD", "cycle_id": "TWX#2026-01-01#1"},
        "USX": {"cost": 30000.0, "currency": "USD", "cycle_id": "USX#2026-01-01#1"},
    }
    state = {"holdings": {"positions": positions}}
    missing = [{"ticker": t, "cycle_id": p["cycle_id"]} for t, p in positions.items()]
    queue, report = review_engine._question_queue(
        card, state, {}, None, "en", route="first_review",
        missing_thesis_positions=missing, tier="behavioral")
    initial = [q for q in queue if q["kind"] == "initial_thesis"]
    assert [q["ticker"] for q in initial] == ["USX"], \
        f"the TWD candidate with no FX rate must never enter the ranked queue: {initial}"
    assert {"id": review_engine._initial_thesis_id("TWX#2026-01-01#1"),
            "kind": "initial_thesis", "cycle_id": "TWX#2026-01-01#1",
            "reason": "fx_unavailable"} in report["rejected"]
    assert all(set(row) == {"id", "kind", "cycle_id", "reason"} for row in report["rejected"])


def _evaluation_row(evaluation_id, ticker, created, reason=None, decision="open"):
    """A trade_evaluations.jsonl row shaped like consider's own writer.

    When a context is present it carries both `reason` and `why_now`, because
    schemas/decision-context.schema.json requires both -- a row with only one
    is a shape `consider` cannot write, and a fixture that uses it would be
    testing against an impossible record. When there is no context the key is
    omitted rather than nulled: review's identity seed keys on that presence
    test, so a stored `context: null` is a different row than no context."""
    row = {"evaluation_id": evaluation_id, "created": created,
           "premise": {"ticker": ticker, "side": "buy", "qty": 10.0, "price": 100.0,
                       "date": created, "currency": "USD"},
           "basis": {"state_version": "csv-v1:seed"}, "consequence": {}, "rule_collisions": [],
           "decision": decision, "decided_on": created}
    if reason is not None:
        row["context"] = {"reason": reason, "why_now": f"why-now for {evaluation_id}"}
    return row


def test_initial_thesis_recalls_what_the_user_already_said_before_entering():
    """#636: a holding the user discussed through `consider` before entering is
    not asked to reconstruct a motive from memory. The stem quotes their own
    stored words verbatim, dated, and carries the provenance to check the quote
    against. A statement recorded *after* the entry is not an entry thesis and
    must not be quoted."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True)
        early = "Order visibility runs into the next build cycle."
        latest = "Guidance was cut but the multiple already prices it."
        after = "Chasing the breakout after the gap."
        rows = [
            # AAA: two statements before the 2026-01-01 entry. The later one is
            # the user's current account of why they entered; `decision: acted`
            # proves recall does not inherit the reconciliation's open-only filter.
            _evaluation_row("eval-aaa-early", "AAA", "2025-12-20", early),
            _evaluation_row("eval-aaa-latest", "AAA", "2025-12-28", latest, decision="acted"),
            # BBB: recorded two months *after* its entry — a different decision
            # than the one this question asks about.
            _evaluation_row("eval-bbb-after", "BBB", "2026-03-01", after),
        ]
        (root / "trade_evaluations.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        positions = {"AAA": _pos("AAA", 5000), "BBB": _pos("BBB", 4000)}
        card, state = _density_artifacts(tmp, "recall", positions, thesis_questions=[])
        run = _run("prepare", "--root", root, "--language", "en", "--route", "first_review",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        asked = {q.get("ticker"): q for q in plan["question_queue"]
                 if q.get("kind") == "initial_thesis"}
        assert set(asked) == {"AAA", "BBB"}, "both holdings are still asked exactly once"

        aaa = asked["AAA"]
        assert latest in aaa["question"], "the stem quotes the user's own words verbatim"
        assert early not in aaa["question"], \
            "a superseded earlier statement is not the account of why they entered"
        assert "2025-12-28" in aaa["question"], "the quote is dated, so it reads as a record"
        assert aaa["recalled_statement"] == {
            "evaluation_id": "eval-aaa-latest", "created": "2025-12-28", "quoted": latest}

        bbb = asked["BBB"]
        assert after not in bbb["question"], \
            "a statement recorded after the entry is not the entry thesis"
        assert "recalled_statement" not in bbb
        assert "what was your thesis?" in bbb["question"], \
            "with nothing to recall the question falls back unchanged"

        # The recall replaces the wording of an existing question; it never adds
        # one, so the route's #291 density band is untouched.
        assert len(plan["question_queue"]) <= review_engine.QUESTION_POLICY["first_review"]["max"]

        # A question row is `additionalProperties: false`, so a new field that
        # is not declared makes the emitted plan invalid against the published
        # schema -- silently, because nothing else in the offline suite feeds a
        # first_review question queue through it. Pinned here with the same
        # manual idiom test_consider.py uses (the suite carries no jsonschema
        # dependency).
        item_schema = json.loads(
            (pathlib.Path(review_engine.__file__).resolve().parent.parent
             / "schemas" / "review-plan.schema.json").read_text(encoding="utf-8")
        )["properties"]["question_queue"]["items"]
        assert item_schema["additionalProperties"] is False
        allowed = set(item_schema["properties"])
        for row in plan["question_queue"]:
            assert set(row) <= allowed, f"undeclared question fields: {set(row) - allowed}"
        recalled_schema = item_schema["properties"]["recalled_statement"]
        assert set(recalled_schema["required"]) <= set(aaa["recalled_statement"])
        assert set(aaa["recalled_statement"]) <= set(recalled_schema["properties"])


def test_initial_thesis_recall_fails_closed_on_a_re_entry_cycle():
    """#636: a ticker fully exited and re-entered is a new position with its own
    reason (owner ruling: per cycle, not per ticker). A cycle id carries no
    lower bound, so every statement made before the *first* entry also satisfies
    `created <= start` for the second. Rather than attribute the previous
    position's reason to this one, a re-entry recalls nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True)
        first_cycle_reason = "Bought the first time for the backlog."
        (root / "trade_evaluations.jsonl").write_text(
            json.dumps(_evaluation_row("eval-old-cycle", "AAA", "2025-11-01",
                                       first_cycle_reason)) + "\n", encoding="utf-8")
        positions = {"AAA": dict(_pos("AAA", 5000), cycle_id="AAA#2026-01-01#2")}
        card, state = _density_artifacts(tmp, "reentry", positions, thesis_questions=[])
        run = _run("prepare", "--root", root, "--language", "en", "--route", "first_review",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        asked = [q for q in plan["question_queue"]
                 if q.get("kind") == "initial_thesis" and q.get("ticker") == "AAA"]
        assert asked, "the re-entered holding is still asked its entry thesis"
        assert first_cycle_reason not in asked[0]["question"], \
            "the previous position's reason is not this position's entry thesis"
        assert "recalled_statement" not in asked[0]


def test_cycle_entry_rejects_a_non_canonical_cycle_id():
    """#636: trade_recap.CYCLE_ID_RE is the shape's single source of truth. A
    `split("#")` would accept `AAA#2026-01-01#garbage` and fail open into a
    valid-looking entry date -- on a path that decides whether to attribute the
    user's own words to a position, fail-open is the wrong direction."""
    assert review_engine._cycle_entry("AAA#2026-01-01#1") == (dt.date(2026, 1, 1), 1)
    # The shapes a permissive `split("#")` would also reject, because int() or
    # fromisoformat() raises on them anyway.
    for bad in ("AAA#2026-01-01#garbage", "AAA#unknown", "AAA", "", None,
                "AAA#2026-13-01#1", "AAA#2026-01-01"):
        assert review_engine._cycle_entry(bad) == (None, None), bad
    # The shapes only the regex rejects. Without these the strictness is
    # decorative: a split-based helper parses each of them into a real date
    # and a real sequence, and the caller would attribute the user's words to
    # a cycle id trade_recap can never have produced.
    for forged in ("A A#2026-01-01#1", "#2026-01-01#1", " AAA#2026-01-01#1",
                   "AAA#2026-01-01#1 "):
        assert review_engine._cycle_entry(forged) == (None, None), forged


def test_initial_thesis_recall_ignores_a_statement_with_no_words():
    """#636: `consider` runs fine with no --decision-context, so an evaluation
    may carry no reason at all. A recalled blank is worse than the canned
    question it would replace, so it is dropped rather than quoted empty."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True)
        (root / "trade_evaluations.jsonl").write_text(
            json.dumps(_evaluation_row("eval-bare", "AAA", "2025-12-20")) + "\n", encoding="utf-8")
        recall = review_engine._evaluation_recall(str(root))
        assert recall == {}, "an evaluation with no stored words is not a recallable statement"


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
        final = _run_finalize("--root", root, "--session-id", plan["session_id"],
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
        retry = _run_finalize("--root", root, "--session-id", plan["session_id"],
                     "--answers", good_path, "--narrative", n_path)
        assert retry.returncode == 0 and json.loads(retry.stdout)["status"] in ("committed", "no-op")
        again = [json.loads(line) for line in
                 (root / "initial_theses.jsonl").read_text(encoding="utf-8").splitlines()]
        assert again == projected, "an idempotent finalize retry must not duplicate rows"


def test_planned_entry_capture_declared_by_the_contract_reaches_preview():
    """#667: `planned_entry`'s own `question_opportunity.answer_contract` must
    name what `_validate_thesis_completeness` will demand, so an agent that
    follows the *declared* contract literally -- not one that already knows
    the validator's cross-field rule -- can still submit a passing answer in
    the same exchange.

    This reads `requirements_by_choice["planned_entry"]` off the real plan
    rather than assuming its shape, resolves it into `thesis_updates` fields,
    and fills only those. Before #667's fix the declared requirement was `[]`
    and this exact construction -- follow the contract, add nothing the
    contract did not ask for -- reached preview's refusal instead."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        positions = {"AAA": _pos("AAA", 5000), "BBB": _pos("BBB", 4000)}
        card, state = _density_artifacts(tmp, "contract", positions, thesis_questions=[])
        run = _run("prepare", "--root", root, "--language", "en", "--route", "first_review",
                   "--card-json", card, "--state-json", state)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _pending_plan(root, run.stdout)
        aaa_q = next(q for q in plan["question_queue"]
                     if q["kind"] == "initial_thesis" and q["ticker"] == "AAA")
        bbb_q = next(q for q in plan["question_queue"]
                     if q["kind"] == "initial_thesis" and q["ticker"] == "BBB")

        contract = aaa_q["question_opportunity"]["answer_contract"]
        required_paths = contract["requirements_by_choice"]["planned_entry"]
        assert required_paths, \
            "planned_entry must declare a requirement for this fixture to test anything (#667)"
        fields = {path.split(".", 1)[1] for path in required_paths
                  if path.startswith("thesis_updates.")}
        assert fields, f"declared requirement must resolve into thesis_updates fields: {required_paths}"

        base = {"session_id": plan["session_id"], "observations": [],
                "commitment": {"choice": "candidate_0"},
                "answers": [{"question_id": aaa_q["id"], "choice": "planned_entry"},
                            {"question_id": bbb_q["id"], "choice": "no_clear_thesis"}]}
        for q in plan["question_queue"]:
            if q["kind"] != "initial_thesis":
                base["answers"].append({"question_id": q["id"], "choice": "deliberate_plan"})
        n_path = pathlib.Path(tmp) / "c-narrative.json"
        n_path.write_text(json.dumps(_narrative("en"), ensure_ascii=False), encoding="utf-8")

        # Literally the contract and nothing else: why/exit_trigger get real
        # content, and maturity -- the one field that turns a real capture
        # into a legal planned_entry record -- is any non-inferred value from
        # the engine's own vocabulary, never invented by this test.
        capture = {"ticker": "AAA", "cycle_id": aaa_q["cycle_id"],
                   "why": "I bought after three quarters of accelerating backlog growth",
                   "exit_trigger": "Backlog growth reverses for two consecutive quarters",
                   "horizon": None}
        if "maturity" in fields:
            capture["maturity"] = sorted(thesis_engine.MATURITY_VALUES - {"inferred"})[0]
        assert set(capture) >= fields, f"fixture must supply every declared field: {fields}"

        # The gate stays strict first: the same cycle, contract-following on
        # why/exit_trigger but left at the default inferred maturity, is still
        # refused (#291's honesty rule is unchanged by this fix).
        bad = json.loads(json.dumps(base))
        bad["thesis_updates"] = [dict(capture, maturity="inferred"),
                                 _thesis_update("BBB", maturity="inferred")]
        bad_path = pathlib.Path(tmp) / "c-bad.json"
        bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        rejected = _run("preview", "--root", root, "--session-id", plan["session_id"],
                        "--answers", bad_path, "--narrative", n_path)
        assert rejected.returncode == 2, rejected.stdout + rejected.stderr
        assert "planned_entry" in json.loads(rejected.stdout)["error"]

        # Now the declared contract, satisfied literally: preview succeeds.
        good = json.loads(json.dumps(base))
        good["thesis_updates"] = [capture, _thesis_update("BBB", maturity="inferred")]
        good_path = pathlib.Path(tmp) / "c-good.json"
        good_path.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        accepted = _run("preview", "--root", root, "--session-id", plan["session_id"],
                        "--answers", good_path, "--narrative", n_path)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr


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


def test_set_cap_moves_dim_size_and_too_heavy_tag_together():
    """#477: dim_size and ticker_diagnosis's too_heavy tag must judge the same
    position against the same trigger. Before this fix, ticker_diagnosis
    stayed hardcoded at the 25% universal default even after `set-cap` raised
    the user's own ceiling, so a 27%-weighted position was a hole in
    dim_size's verdict and in the too_heavy tag under the default -- but only
    stopped being a hole in dim_size once the cap was raised; the instrument
    tag never moved (#324's unification missed this fourth reader).

    Drives the real `set-cap` CLI and the real `_position_cap_override`
    reader (the exact #324 chain: profile.json -> review._position_cap_override
    -> trade_recap.effective_oversize_trigger), not a hand-built override, so
    this proves the user-visible contract rather than just the constant."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        # AAA is 27% of the book and the single largest risk position. SPY is
        # a broad-market ETF (allocation-exempt, #172/#334) so its value
        # counts toward the portfolio total but never toward risk_weights or
        # the too_heavy check -- AAA alone drives both readers' verdict.
        held = {"AAA": (10.0, 2500.0), "SPY": (10.0, 7300.0)}
        last_px = {"AAA": 270.0, "SPY": 730.0}

        # Baseline: no override on disk yet -- universal 25% default applies,
        # and 27% > 25% must trip both readers.
        assert review_engine._position_cap_override(root) is None
        base_size = tr.dim_size([], held, last_px, None)
        assert abs(base_size["max_pct"] - 0.27) < 1e-9 and base_size["max_ticker"] == "AAA"
        assert base_size["triggered"] is True, "27% must trip the 25% universal default"
        base_tdiag = tr.ticker_diagnosis([], {}, held, last_px, max_pos_override=None,
                                         sizing_weights=base_size["weights"])
        base_aaa = next(d for d in base_tdiag if d["ticker"] == "AAA")
        assert any(t["code"] == "too_heavy" for t in base_aaa["tags"]), \
            "27% position must carry too_heavy under the 25% universal default"

        # Raise the cap to 30% through the real CLI.
        run = _run("set-cap", "--root", root, "--pct", "0.30")
        assert run.returncode == 0, run.stdout + run.stderr
        override = review_engine._position_cap_override(root)
        assert override == 0.30

        # Both readers, fed the same override, must agree the 27% position is
        # no longer a hole -- the user-visible contract, not just the
        # constant they both used to read.
        raised_size = tr.dim_size([], held, last_px, override)
        assert raised_size["triggered"] is False, \
            "sizing dimension must respect the raised 30% cap"
        raised_tdiag = tr.ticker_diagnosis([], {}, held, last_px, max_pos_override=override,
                                           sizing_weights=raised_size["weights"])
        raised_aaa = next(d for d in raised_tdiag if d["ticker"] == "AAA")
        assert not any(t["code"] == "too_heavy" for t in raised_aaa["tags"]), \
            "instrument tag must also respect the raised 30% cap (#477) -- " \
            "before this fix it stayed too_heavy at any cap"


def test_set_cap_moves_dim_size_and_too_heavy_tag_together_with_a_cost_fallback_denominator():
    """#477 second half: the test above proves cap-*threshold* parity on an
    all-priced book, which cannot expose a denominator split -- both readers
    already summed the same two priced values. This drives the same real
    `set-cap` CLI over a book with one priced ticker and one held-but-unpriced
    ticker (cost-basis only), the exact shape where dim_size's denominator
    (price-or-cost) and ticker_diagnosis's old denominator (priced only,
    #477's original bug) used to disagree. Both dim_size's own weight and
    ticker_diagnosis's too_heavy percentage must be the identical number, at
    both the universal default and the raised override."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        # NOPX is unpriced (cost-basis only) and the larger of the two by
        # cost; AAA is priced. NOPX has no realized round-trip of its own in
        # this fixture, so give it one small closed lot purely so it clears
        # ticker_diagnosis's |impact| >= $1 filter and appears in the output
        # -- the tag/weight assertions below are what this test is for.
        held = {"AAA": (10.0, 1000.0), "NOPX": (10.0, 3500.0)}
        last_px = {"AAA": 100.0}
        rts = [{"ticker": "NOPX", "buy_px": 10.0, "sell_px": 12.0, "qty": 5,
                "ret": 0.2, "hold": 10, "entry": dt.date(2026, 1, 1), "exit": dt.date(2026, 2, 1)}]
        total = 1000.0 + 3500.0   # AAA priced 1000 + NOPX cost-fallback 3500

        base_size = tr.dim_size([], held, last_px, None)
        assert base_size["applicable"] is True
        base_weight = base_size["weights"]["NOPX"]
        assert abs(base_weight - 3500.0 / total) < 1e-9
        base_tdiag = tr.ticker_diagnosis(rts, {}, held, last_px,
                                         max_pos_override=None, sizing_weights=base_size["weights"])
        base_nopx = next(d for d in base_tdiag if d["ticker"] == "NOPX")
        base_hits = [t for t in base_nopx["tags"] if t["code"] == "too_heavy"]
        assert base_hits and base_hits[0]["params"]["wpct"] == base_weight, \
            "at the universal 25% default, NOPX's cost-based 77.8% weight must be a hole " \
            "AND the too_heavy tag must carry that exact same weight"

        run = _run("set-cap", "--root", root, "--pct", "0.80")
        assert run.returncode == 0, run.stdout + run.stderr
        override = review_engine._position_cap_override(root)
        assert override == 0.80

        raised_size = tr.dim_size([], held, last_px, override)
        assert raised_size["triggered"] is False, "77.8% must clear a raised 80% cap"
        raised_weight = raised_size["weights"]["NOPX"]
        assert raised_weight == base_weight, \
            "the weight itself never depends on the cap -- only whether it trips the trigger"
        raised_tdiag = tr.ticker_diagnosis(rts, {}, held, last_px,
                                          max_pos_override=override, sizing_weights=raised_size["weights"])
        raised_nopx = next(d for d in raised_tdiag if d["ticker"] == "NOPX")
        assert not any(t["code"] == "too_heavy" for t in raised_nopx["tags"]), \
            "too_heavy must also clear at the raised 80% cap for the cost-fallback ticker -- " \
            "this is the exact reader split #477's second half fixed"


# #501: the pre-append refusal is a message the user actually reads (main()
# emits str(exc) as the whole error field), so it may not name the internal
# machinery the owner decision explicitly ruled out of user-facing copy.
_FROZEN_INTERNALS = ("state_version", "valuation_frame", "ValuationFrame", "PortfolioBasis",
                     "fingerprint", "receipt", "sha256", "overlay", "Traceback")


def _frozen_fixture(tmp, frozen_dir, name="coach", rows=None, prices=None):
    """Freeze one candidate CSV plus the live ledger, exactly as prepare does."""
    root = pathlib.Path(tmp) / name
    csv_path = pathlib.Path(tmp) / f"{name}-weekly.csv"
    csv_path.write_text(
        rows or "Symbol,Action,Quantity,Price,TradeDate,RecordType\nA,BUY,2,10,2026-07-01,Trade\n",
        encoding="utf-8")
    inputs = review_engine._freeze_transaction_inputs(str(root), [str(csv_path)], frozen_dir)
    batches, _skipped, _future = review_engine._parse_frozen_candidates(inputs["frozen_paths"])
    frame = review_engine.portfolio_basis.build_valuation_frame(
        as_of="2026-07-02", positions={"A": {"currency": "USD"}},
        prices=prices if prices is not None else {"A": 11},
        aggregate_currency="USD", fx_to_aggregate={},
        price_provenance="test", fx_provenance="test").to_dict()
    state = {"date_end": "2026-07-02", "valuation_frame": frame}
    overlay, receipt = review_engine._virtual_review_basis(inputs, batches, state)
    return {"root": root, "csv": csv_path, "inputs": inputs, "batches": batches,
            "overlay": overlay, "receipt": receipt, "state": state}


def _refuses(fixture, root=None):
    """Run the locked gate, require a refusal, and return its user-facing text."""
    try:
        review_engine._verify_and_ingest_frozen_trades(
            str(root or fixture["root"]), fixture["inputs"], fixture["batches"],
            fixture["overlay"], fixture["receipt"], {}, fixture["state"])
    except review_engine.ReviewError as exc:
        return str(exc)
    raise AssertionError("the gate must refuse before any append")


def test_every_frozen_lane_refusal_speaks_product_language():
    """The owner decision for #501 rules internal machinery out of user-facing
    copy, and `main()` emits `str(exc)` as the entire error field, so every raise
    on this lane is user-facing — not only the one that carries the A1 message.
    Asserting the token ban against a single message would be a guard that
    cannot fail for the paths that break the rule."""
    import ast
    frozen_lane = {
        "_candidate_receipt", "_freeze_transaction_inputs", "_parse_frozen_candidates",
        "_virtual_valuation_frame", "_virtual_review_basis", "_basis_reference",
        "_verify_and_ingest_frozen_trades",
    }
    tree = ast.parse(pathlib.Path(review_engine.__file__).read_text(encoding="utf-8"))
    checked, offenders = 0, []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in frozen_lane):
            continue
        frozen_lane.discard(node.name)
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "ReviewError" and call.args):
                continue
            # Only literal copy is readable here; a ReviewError(str(exc)) forwards
            # another layer's message and is that layer's contract, not this one's.
            parts = [v.value for v in ast.walk(call.args[0])
                     if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            if not parts:
                continue
            checked += 1
            text = " ".join(parts)
            named = [token for token in _FROZEN_INTERNALS if token in text]
            if named:
                offenders.append((node.name, call.lineno, named, text[:70]))
    assert not frozen_lane, f"the frozen lane lost functions this guard names: {sorted(frozen_lane)}"
    assert checked >= 8, f"expected the frozen lane's raises to be readable, saw {checked}"
    assert not offenders, "frozen-lane refusals name internals: " + str(offenders)


def test_frozen_virtual_basis_rejects_changed_candidate_before_any_append():
    with tempfile.TemporaryDirectory() as tmp, \
            tempfile.TemporaryDirectory(prefix="fomo501-test-") as frozen_dir:
        fixture = _frozen_fixture(tmp, frozen_dir)
        assert fixture["receipt"]["basis"]["state_version"] == fixture["receipt"]["basis_state_version"]
        # A whitespace-only edit changes no parsed trade, but it is still a
        # different engine input than the one the frozen receipt describes.
        fixture["csv"].write_text(fixture["csv"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
        message = _refuses(fixture)
        assert message == review_engine.BASIS_CHANGED_MESSAGE, message
        leaked = [token for token in _FROZEN_INTERNALS if token in message]
        assert not leaked, f"pre-append refusal copy names internals: {leaked}"
        assert not (fixture["root"] / "ledger.jsonl").exists()


def test_frozen_virtual_basis_verifies_equal_input_and_appends_once():
    with tempfile.TemporaryDirectory() as tmp, \
            tempfile.TemporaryDirectory(prefix="fomo501-test-") as frozen_dir:
        fixture = _frozen_fixture(tmp, frozen_dir)
        result, _card, _state = review_engine._verify_and_ingest_frozen_trades(
            str(fixture["root"]), fixture["inputs"], fixture["batches"],
            fixture["overlay"], fixture["receipt"], {}, fixture["state"])
        events, skipped = ledger_engine.load_ledger(str(fixture["root"] / "ledger.jsonl"))
        # `appended` counts the candidate trades; the recorded book (#549) rides
        # the same locked transaction and is reported separately.
        assert result["appended"] == 1 and skipped == 0
        assert [event["type"] for event in events] == ["trade", "snapshot"]
        assert result["recorded_book"]["status"] == "projected"


def test_virtual_basis_frame_marks_anchor_only_holding_missing_instead_of_forging_price():
    anchor = {"type": "snapshot", "as_of": "2026-07-01", "is_complete": True,
              "positions": [{"ticker": "ANCHOR", "shares": 1, "avg_cost": 5,
                             "market": "US", "currency": "USD"}], "cash": None}
    candidate = {"type": "trade", "date": "2026-07-02", "ticker": "NEW", "action": "buy",
                 "qty": 1, "price": 10, "market": "US", "currency": "USD"}
    source_frame = review_engine.portfolio_basis.build_valuation_frame(
        as_of="2026-07-02", positions={"NEW": {"currency": "USD"}}, prices={"NEW": 11},
        aggregate_currency="USD", fx_to_aggregate={}, price_provenance="test", fx_provenance="test").to_dict()
    # `splits` is required rather than defaulted (#558 follow-up), so a caller
    # with nothing to supply says so. This book holds no split either way.
    virtual_frame, book_as_of = review_engine._virtual_valuation_frame(
        [anchor, candidate], source_frame, splits=None)
    assert virtual_frame["coverage"]["missing_price"] == [{"ticker": "ANCHOR", "currency": "USD"}]
    assert virtual_frame["prices"] == {"NEW": source_frame["prices"]["NEW"]}
    # The book's own effective date is returned so staleness is measured from
    # the book, never from the price bar (which is routinely older).
    assert book_as_of == "2026-07-02"


def test_frozen_gate_rejects_live_ledger_change_without_appending_candidate():
    """A book-affecting write landing between the frozen receipt and the locked
    reread must abort before the first append (owner decision A1), leaving the
    ledger byte-identical rather than mixing this attempt's candidate into a
    book the analysis never saw."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as frozen:
        fixture = _frozen_fixture(tmp, frozen)
        root = fixture["root"]
        root.mkdir(parents=True, exist_ok=True)
        raw = (b'{"type":"trade","date":"2026-06-01","ticker":"OLD","action":"buy",'
               b'"qty":1,"price":1,"market":"US","currency":"USD"}\n')
        (root / "ledger.jsonl").write_bytes(raw)
        message = _refuses(fixture)
        assert message == review_engine.BASIS_CHANGED_MESSAGE, message
        assert (root / "ledger.jsonl").read_bytes() == raw, \
            "a refused attempt may not leave one candidate byte behind"


def test_frozen_gate_fails_closed_on_a_corrupt_live_ledger_before_appending():
    """#462's contract on the #501 lane: a corrupt row appearing in the live
    ledger must block the import rather than let the locked recompute run over a
    silently shortened read. This refusal is a different situation from a race,
    so it keeps the diagnostic corruption text instead of the A1 message."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as frozen:
        root = pathlib.Path(tmp) / "coach"
        root.mkdir(parents=True)
        good = (b'{"type":"trade","date":"2026-06-01","ticker":"OLD","action":"buy",'
                b'"qty":1,"price":1,"market":"US","currency":"USD"}\n')
        (root / "ledger.jsonl").write_bytes(good)
        fixture = _frozen_fixture(tmp, frozen, name="coach")
        assert fixture["inputs"]["ledger_receipt"]["bytes_n"] == len(good), \
            "the fixture must have frozen the existing ledger, not an empty root"
        corrupt = good + b'{"type":"trade","date":"nope"\n'
        (root / "ledger.jsonl").write_bytes(corrupt)
        message = _refuses(fixture, root=root)
        assert "unreadable row" in message, message
        assert (root / "ledger.jsonl").read_bytes() == corrupt, \
            "a corrupt ledger must not be appended to"


def test_frozen_basis_refuses_a_missing_or_invalid_valuation_frame_before_any_write():
    """The frozen basis is only meaningful against a valid frame. Both a state
    that carries no frame and a structurally broken frame must fail during
    analysis -- before the locked lane, so no ledger file is ever created."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as frozen:
        root = pathlib.Path(tmp) / "coach"
        csv_path = pathlib.Path(tmp) / "weekly.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType\nA,BUY,2,10,2026-07-01,Trade\n",
            encoding="utf-8")
        inputs = review_engine._freeze_transaction_inputs(str(root), [str(csv_path)], frozen)
        batches, _skipped, _future = review_engine._parse_frozen_candidates(inputs["frozen_paths"])
        for label, state in (("missing", {"date_end": "2026-07-02"}),
                             ("invalid", {"date_end": "2026-07-02",
                                          "valuation_frame": {"contract_version": "not-a-frame"}})):
            try:
                review_engine._virtual_review_basis(inputs, batches, state)
                raise AssertionError(f"a {label} valuation frame must refuse")
            except review_engine.ReviewError:
                pass
        assert not (root / "ledger.jsonl").exists()


def test_frozen_gate_releases_the_projection_lock_after_a_pre_append_refusal():
    """A refusal must not leave the root-wide transaction held. If it did, the
    rerun the A1 message asks the user to perform would block forever instead of
    producing a review -- a hang is a worse outcome than the conflict itself."""
    import fcntl
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as frozen:
        fixture = _frozen_fixture(tmp, frozen)
        fixture["csv"].write_text(fixture["csv"].read_text(encoding="utf-8") + "\n",
                                  encoding="utf-8")
        assert _refuses(fixture) == review_engine.BASIS_CHANGED_MESSAGE
        locks = list(fixture["root"].rglob(".projections.lock"))
        assert locks, "the gate must have created the root-wide projection lock"
        # Non-blocking on purpose: a leaked flock makes this raise instead of
        # hanging the suite the way a blocking reacquire would.
        handle = os.open(str(locks[0]), os.O_RDWR)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError as exc:
            raise AssertionError(f"refusal leaked the projection lock: {exc}") from exc
        finally:
            os.close(handle)


def test_frozen_prepare_is_the_real_user_lane_and_a_rerun_appends_no_duplicate():
    """The frozen transaction -- not `_ingest_trades` -- is what a real user
    reaches (a plain `prepare trades.csv --root ...` with no injected
    artifacts). Every other ingest-detail test in this file injects
    --card-json/--state-json and therefore exercises the legacy lane only, so
    this is the coverage for the new lane's counts, its idempotent retry, and
    the privacy of the receipt it freezes."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        csv_path = pathlib.Path(tmp) / "real.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "ACME,BUY,10,50,2026-03-02,Trade,US,USD\n"
            "ACME,SELL,10,55,2026-03-03,Trade,US,USD\n",
            encoding="utf-8")
        env = _offline_engine_env(tmp)
        first = _run("prepare", csv_path, "--root", root, "--route", "weekly_review",
                     "--session-nonce", "frozen-1", env=env)
        assert first.returncode == 0, first.stdout + first.stderr
        ingest = json.loads(first.stdout)["review_plan"]["input"]["ledger_ingest"]
        assert ingest["appended"] == 2 and ingest["skipped_dup"] == 0, ingest
        rows = (pathlib.Path(root) / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(rows) == 2

        # The frozen receipt lives only in memory, for the length of the gate.
        # It reaches neither the agent surface nor durable state: it carries the
        # ledger's byte digest, and session_id_from_state() hashes engine_state,
        # so persisting it would give the same CSV a new session id as soon as
        # its own trades landed.
        assert "virtual_review_basis" not in first.stdout
        plan = _pending_plan(root, first.stdout)
        assert "virtual_review_basis" not in plan["engine_state"]

        # A retry of the same facts converges: the candidate deduplicates against
        # the ledger it already produced instead of doubling the book.
        again = _run("prepare", csv_path, "--root", root, "--route", "weekly_review",
                     "--session-nonce", "frozen-2", env=env)
        assert again.returncode == 0, again.stdout + again.stderr
        repeat = json.loads(again.stdout)["review_plan"]["input"]["ledger_ingest"]
        assert repeat["appended"] == 0 and repeat["skipped_dup"] == 2, repeat
        assert (pathlib.Path(root) / "ledger.jsonl").read_text(
            encoding="utf-8").splitlines() == rows, "a retry may not rewrite the book"


def test_frozen_test_drive_runs_the_same_validation_and_persists_nothing():
    """Test drive must differ from a real review only at the effect boundary:
    the same parse / virtual basis / frame / locked verification chain runs, and
    only the append is skipped. A test drive that quietly took a shorter path
    would stop being evidence about the production lane."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        csv_path = pathlib.Path(tmp) / "demo.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "ACME,BUY,10,50,2026-03-02,Trade,US,USD\n"
            "ACME,SELL,10,55,2026-03-03,Trade,US,USD\n",
            encoding="utf-8")
        env = _offline_engine_env(tmp)
        run = _run("prepare", csv_path, "--test-drive", "--root", root,
                   "--session-nonce", "demo-1", env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        assert plan["persist"] is False
        ingest = plan["input"]["ledger_ingest"]
        assert ingest is not None and ingest["appended"] == 0, \
            f"the frozen verification must still run under test drive: {ingest}"
        assert ingest["skipped_dup"] == 0 and ingest["skipped_non_trade"] == 0, ingest
        assert not (pathlib.Path(root) / "ledger.jsonl").exists(), \
            "test drive cannot persist real trade facts"
        assert "virtual_review_basis" not in _pending_plan(root, run.stdout)["engine_state"]


def test_frozen_prepare_survives_a_book_newer_than_the_last_price_bar():
    """A price frame is dated at the last close, and the recorded book is
    routinely newer than that -- a weekend review, or the documented onboarding
    order where a holdings snapshot is declared and an earlier CSV week is
    reviewed after it. Feeding the frame's date in as the staleness reference
    made that ordinary case abort the whole review instead of reporting zero
    staleness, and the anchor branch it dies in had no coverage at all."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        (pathlib.Path(root) / "ledger.jsonl").write_text(json.dumps({
            "type": "snapshot", "as_of": "2026-07-27", "source": "user_declared",
            "is_complete": True, "cash": None,
            "positions": [{"ticker": "ACME", "shares": 10, "avg_cost": 50,
                           "market": "US", "currency": "USD"}]}) + "\n", encoding="utf-8")
        csv_path = pathlib.Path(tmp) / "week.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "ACME,BUY,5,52,2026-07-09,Trade,US,USD\n"
            "ACME,SELL,5,55,2026-07-10,Trade,US,USD\n",
            encoding="utf-8")
        run = _run("prepare", csv_path, "--root", root, "--route", "weekly_review",
                   "--session-nonce", "newer-book", env=_offline_engine_env(tmp))
        assert run.returncode == 0, run.stdout + run.stderr
        plan = json.loads(run.stdout)["review_plan"]
        assert plan["input"]["ledger_ingest"]["appended"] == 2, plan["input"]["ledger_ingest"]
        # The reconciliation branch this fixture reaches -- an anchored ledger
        # plus fresh candidates -- is the one the frozen lane had never run.
        assert "holdings_reconciliation" in plan["input"]["ledger_ingest"]


def test_frozen_prepare_keeps_one_session_identity_for_the_same_input():
    """#166: session identity is content-addressed so an interrupted session is
    recoverable and finalize stays idempotent. The frozen receipt carries the
    ledger's byte digest, so persisting it into engine_state would hand the same
    CSV a new identity as soon as its own trades landed."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        csv_path = pathlib.Path(tmp) / "same.csv"
        csv_path.write_text(
            "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
            "ACME,BUY,10,50,2026-03-02,Trade,US,USD\n"
            "ACME,SELL,10,55,2026-03-03,Trade,US,USD\n",
            encoding="utf-8")
        env = _offline_engine_env(tmp)
        ids = []
        for _attempt in (1, 2):
            run = _run("prepare", csv_path, "--root", root, "--route", "weekly_review",
                       "--session-nonce", "same", env=env)
            assert run.returncode == 0, run.stdout + run.stderr
            ids.append(json.loads(run.stdout)["review_plan"]["session_id"])
            # Drop the pending bundle so the second run recomputes the identity
            # instead of resuming, which is what finalize leaves behind.
            shutil.rmtree(pathlib.Path(root) / ".pending", ignore_errors=True)
        assert ids[0] == ids[1], \
            f"the same CSV and nonce must keep one session identity, got {ids}"


# ───────── #630: a weekly file covering part of the book is not the book ─────────
#
# The whole section drives the real `review.py` subprocess over a real `mock/`
# fixture — no `--card-json` injection — because the defect lived in exactly the
# step injection skips: `prepare` reconciling what the engine derived from the
# supplied rows against the book the ledger already holds.

_BOOK_FIXTURE = ROOT / "skills" / "fomo-kernel" / "mock" / "sample_ai_holder.csv"
# One ticker, dated after every row in the fixture. This is an ordinary weekly
# export: nothing in flows/weekly-review.md asks for a cumulative file.
_ONE_TICKER_WEEK = (
    "Symbol,Quantity,Price,Action,Description,TradeDate,SettledDate,"
    "Interest,Amount,Commission,Fee,CUSIP,RecordType\n"
    "MSFT,10,410.00,BUY,BOUGHT MICROSOFT CORP,2024-11-04,2024-11-06,0,-4100.00,0,0,,Trade\n"
    "MSFT,5,420.00,BUY,BOUGHT MICROSOFT CORP,2024-11-07,2024-11-11,0,-2100.00,0,0,,Trade\n")


def _book_review(tmp, root, csv_path, env, tag, route, extra=(), prices=None):
    """One real prepare+finalize over `csv_path`, every question skipped.

    These reviews run offline, so no close is retrievable and #623 refuses a
    card that reports unretrievable prices when recovery was never attempted.
    Declaring the dead end is the sanctioned clearance (same as
    `tests/test_preview_gate.py`) and keeps the subject of these tests the book
    the figures were measured over, not the price. Weights fall back to cost
    basis either way, which is what these assertions read.

    `prices` swaps that declaration for a supplied envelope — the two are
    contradictory claims about the same run. A mixed-currency book needs one,
    because #612 refuses an aggregate whose held-currency rate is missing
    rather than converting it at 1.0.
    """
    price_argv = (["--prices", str(prices)] if prices else
                  ["--prices-unavailable", "offline test fixture: no provider is reachable"])
    run = _run("prepare", csv_path, "--root", root, "--route", route, "--language", "en",
               "--session-nonce", tag, *price_argv, *extra, env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    plan = _pending_plan(root, run.stdout)
    answers = {
        "session_id": plan["session_id"], "observations": [],
        "commitment": {"choice": "skip"},
        "answers": [{"question_id": q["id"], "choice": "skip"}
                    for q in plan["question_queue"]],
        "thesis_updates": [{
            "ticker": row["ticker"], "cycle_id": row["cycle_id"],
            "why": "The imported history suggests a role that remains inferred",
            "horizon": None,
            "exit_trigger": "A later review contradicts the inferred role",
            "target_size": "bounded", "driver": "imported history",
            "maturity": "inferred", "source_type": "other",
            "source_name": "imported history", "source_confidence": "candidate",
        } for row in plan["missing_thesis_positions"]],
    }
    answers_path = pathlib.Path(tmp) / f"answers_{tag}.json"
    answers_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    narrative = {"headline": "Test headline", "mirror": "Test mirror"}
    keys = plan["card_plan"]["required_honesty_keys"]
    if keys:
        narrative["honesty"] = {
            key: "This limitation is stated plainly rather than treated as a zero."
            for key in keys}
    narrative_path = pathlib.Path(tmp) / f"narrative_{tag}.json"
    narrative_path.write_text(json.dumps(narrative, ensure_ascii=False), encoding="utf-8")
    # #628: finalize refuses a session with no preview receipt, because a card
    # the user never saw cannot be committed. These reviews only exist to seed
    # and inspect a book, but they go through the real lifecycle to do it.
    previewed = _run("preview", "--root", root, "--session-id", plan["session_id"],
                     "--answers", answers_path, "--narrative", narrative_path, env=env)
    assert previewed.returncode == 0, previewed.stdout + previewed.stderr
    final = _run("finalize", "--root", root, "--session-id", plan["session_id"],
                 "--answers", answers_path, "--narrative", narrative_path, env=env)
    assert final.returncode == 0, final.stdout + final.stderr
    return plan


def _sizing_dim(plan):
    for row in plan["engine_card"].get("dims_raw") or []:
        if row.get("dim") == "部位 sizing":
            return row
    return None


def test_a_weekly_file_covering_part_of_the_book_is_not_measured_as_the_whole_book():
    """#630: the account is six positions; the week traded one.

    `trade_recap` builds its current book from the supplied rows (FIFO over the
    CSV), and `prepare` only ever reconciled that against the ledger when the
    root carried a *declared* holdings snapshot. A root whose book came from an
    earlier CSV import has only #549's `trades_derived` row, so an ordinary
    weekly export was measured as if it were the whole account: `max_pct` 1.0,
    `risk_weights {MSFT: 1.0}`, and `sizing_coverage.scope full_current_book`
    with `total_holdings: 1` — the engine asserting a whole-book measurement,
    not reporting a partial view it knew about.

    Concentration is only where it was noticed. Everything keyed on those same
    narrowed holdings was wrong the same way and just as silently, so this
    asserts the whole family: the durable metrics, the rule the user is asked
    to commit to, the prescription, and the breach recorded against them.
    """
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        env = _offline_engine_env(tmp)
        _book_review(tmp, root, _BOOK_FIXTURE, env, "b1", "first_review")

        # What the ledger holds after the week is ingested — the canonical book
        # every current-view claim below owes its denominator to.
        week = pathlib.Path(tmp) / "one_ticker_week.csv"
        week.write_text(_ONE_TICKER_WEEK, encoding="utf-8")
        plan = _book_review(tmp, root, week, env, "b2", "weekly_review")
        events, _ = ledger_engine.load_ledger(os.path.join(root, "ledger.jsonl"))
        book = ledger_engine.derive_holdings(events)["holdings"]
        assert len(book) == 6, f"fixture must leave a six-position book: {sorted(book)}"

        card, state = plan["engine_card"], plan["engine_state"]
        assert plan["input"]["paths"] == [str(week)], \
            "this review must be the ordinary case: only this period's trades"

        # 1. No book-wide concentration claim measured over one ticker.
        sizing = _sizing_dim(plan)
        if sizing is not None:
            assert sizing.get("max_pct") != 1.0, sizing
            coverage = sizing.get("sizing_coverage") or {}
            assert coverage.get("scope") != "full_current_book" \
                or coverage.get("total_holdings") == len(book), \
                f"a whole-book scope over {coverage.get('total_holdings')} holdings: {coverage}"

        # 2. Durable state: the metrics the next review reconciles against, and
        #    the book itself. `None` is the honest value for a dimension this
        #    file cannot support; 1.0 is not.
        for key in ("max_pos_pct", "top3_pct", "ai_pct", "max_sector_pct"):
            assert state["metrics"].get(key) != 1.0, \
                f"metrics.{key} still reports the single-ticker week as the whole book"
        assert sorted((state.get("holdings") or {}).get("positions") or {}) == sorted(book), \
            "durable holdings must be the canonical ledger book, not this file's slice"
        assert state.get("n_held") == len(book)

        # 3. The surface where the user is asked to commit, and the prescription
        #    beside it, must not carry the fabricated weight either.
        for row in card.get("candidate_rules") or []:
            assert (row.get("params") or {}).get("max_pct") != 1.0, row
        for row in card.get("prescriptions") or []:
            assert (row.get("params") or {}).get("max_pct") != 1.0, row
        for hole in card.get("top_holes") or []:
            assert (hole.get("raw") or {}).get("max_pct") != 1.0, hole

        # 4. No breach event recorded from a narrower-than-book measurement. A
        #    user with a standing single-position cap got "最大單注 100%" written
        #    into problems.jsonl against a position that is a small part of the
        #    book.
        for event in state.get("problem_events") or []:
            assert event.get("key") not in {"oversize", "concentration"}, \
                f"a structural breach recorded from a partial view: {event}"

        # 5. The gap is disclosed rather than silently dropped.
        detail = (card.get("data_integrity") or {}).get("accounting_reconciliation")
        assert isinstance(detail, dict) and detail.get("mismatches"), \
            "the reconciliation that withdrew these figures must say so on the card"
        assert detail.get("canonical_positions_n") == len(book)


def test_a_weekly_file_that_does_cover_the_book_still_computes_concentration():
    """#630 counterweight, and the reason the fix is not "measure nothing".

    A cumulative weekly file derives the same six positions the ledger holds, so
    its concentration is a real whole-book reading and must survive — with
    `full_current_book` earned rather than assumed. Without this, gating every
    weekly review would pass the test above while destroying the product.

    It also pins the two comparisons the derived lane must *not* make. Both
    sides here are readings of the same trade rows: cost basis differs by
    method (`derive_holdings` keeps a moving average, the card's own
    accumulation is FIFO — 23250 against 24900 for this fixture's NVDA), and
    market/currency are not written onto a holding by `trade_recap.build_state`
    at all. Comparing either would withdraw the figures from every ordinary
    review, and for the second one from every non-US book in particular.
    """
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        env = _offline_engine_env(tmp)
        first = _book_review(tmp, root, _BOOK_FIXTURE, env, "c1", "first_review")
        assert (_sizing_dim(first) or {}).get("max_pct"), \
            "the fixture must produce a real first-review concentration reading"

        cumulative = pathlib.Path(tmp) / "cumulative_week.csv"
        cumulative.write_text(
            _BOOK_FIXTURE.read_text(encoding="utf-8")
            + _ONE_TICKER_WEEK.split("\n", 1)[1], encoding="utf-8")
        plan = _book_review(tmp, root, cumulative, env, "c2", "weekly_review")

        sizing = _sizing_dim(plan)
        assert sizing is not None, \
            "a file that does cover the book must still be measured against it"
        coverage = sizing["sizing_coverage"]
        assert coverage["total_holdings"] == 6, coverage
        assert coverage["book_basis"] == "recorded_book", coverage
        assert coverage["scope"] == "full_current_book", coverage
        assert 0 < sizing["max_pct"] < 1.0, sizing
        assert plan["engine_state"]["metrics"]["max_pos_pct"] == sizing["max_pct"]
        assert not ((plan["engine_card"].get("data_integrity") or {})
                    .get("accounting_reconciliation") or {}).get("mismatches"), \
            "a cumulative file must not be gated as if it disagreed with the book"


def test_the_recorded_book_reconciliation_is_idempotent_across_a_re_entrant_prepare():
    """#630: the reconciliation may not read state this command itself writes.

    `prepare` appends the CSV to `ledger.jsonl` and records the book it derived.
    A predicate over the *pre-import* ledger therefore answers differently on the
    two runs — false on a fresh root's first review, true on every later run of
    the identical file — so the same input reconciled on one pass and not the
    other, and the second pass came back with a different book: `cost`/`avg_cost`
    moved (`derive_holdings` keeps a moving average where the card's own
    accumulation is FIFO) and `origin`/`market`/`currency` appeared.

    That is not a cosmetic difference. The session id is content-addressed from
    engine state, so it moved too, and `add-cash` — which re-enters this exact
    pipeline to add an anchor to a session the user has *already answered
    against*, and refuses when anything but the anchor moved — could never
    succeed on a trades-only root. #624's tests caught it; this one names the
    property, so a future change to the reconciliation reddens something that
    says why.

    Two halves are required and this asserts both: the predicate reads the
    post-import book, and on the derived lane a reconciliation that *agrees*
    adopts nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        env = _offline_engine_env(tmp)

        def prepare_once():
            run = _run("prepare", _BOOK_FIXTURE, "--root", root, "--language", "en", env=env)
            assert run.returncode == 0, run.stdout + run.stderr
            return _pending_plan(root, run.stdout)

        first = prepare_once()
        # Drop the pending bundle so the second call recomputes rather than
        # resuming by fingerprint — the same thing `add-cash` does with an
        # anchor added, minus the anchor.
        shutil.rmtree(root / ".pending", ignore_errors=True)
        second = prepare_once()

        assert (first["engine_state"].get("holdings") or {}).get("positions"), \
            "this fixture must actually leave open positions, or nothing is being compared"
        assert first["session_id"] == second["session_id"], (
            "the id is content-addressed from engine state, so a book that moved "
            "between two runs of the identical file shows up here first")
        assert first["engine_state"] == second["engine_state"], \
            "re-preparing the identical file may not produce a different book"
        for key in ("question_queue", "missing_thesis_positions"):
            assert first[key] == second[key], key
        assert (((first["input"].get("ledger_ingest") or {}).get("holdings_reconciliation"))
                == ((second["input"].get("ledger_ingest") or {}).get("holdings_reconciliation"))), (
            "the reconciliation must report the same thing on both passes, or the "
            "predicate is still reading the ledger this command writes")

        # The other half, named separately because either one alone hides the
        # defect: a file that covers the book keeps the card's own cost basis.
        # `derive_holdings` keeps a moving average and the card's accumulation is
        # FIFO, so adopting a book the reconciliation *agrees with* would move
        # `cost`/`avg_cost` for every ordinary review — a methodology change with
        # no defect behind it. This fixture's PLTR has a partial sell, which is
        # where the two methods diverge (11200 FIFO against 11786.67).
        positions = first["engine_state"]["holdings"]["positions"]
        events, _ = ledger_engine.load_ledger(os.path.join(root, "ledger.jsonl"))
        canonical = ledger_engine.derive_holdings(events)["holdings"]
        divergent = [t for t in positions
                     if abs(float(positions[t]["cost"])
                            - float(canonical[t]["cost_total"])) > 0.05]
        assert divergent, (
            "this fixture must actually contain a position where FIFO and the "
            "ledger's moving average disagree, or the assertion below is vacuous")
        assert first["engine_state"]["holdings"]["derived_from"] == "trades_csv", (
            "a review whose file covers the book keeps its own book; adopting the "
            "ledger's restatement here silently changes every user's avg_cost")


def test_a_non_us_book_is_not_read_as_misclassified_by_the_derived_lane():
    """#630's second false positive, which the first fix shipped with.

    `_overlay_ledger_holdings` compares market and currency, defaulting the raw
    side to US/USD because "transaction artifacts historically omit these
    fields". Against a *declared* holdings view that is the point — it catches a
    misclassified non-US position. Against the engine's own restatement of the
    same trades it is unconditional: `trade_recap.build_state` writes neither
    field onto a holding, so every `.TW`/`.TWO` position reads as misclassified
    and every mixed-currency book has its current view withdrawn every week.

    The persona sweep catches this through `tw_mixed`, four surfaces deep. This
    says it directly, so the reason survives next to the branch it justifies.

    The book is mixed-currency, so it carries the persona's committed FX
    envelope: without a held-currency rate #612 refuses the aggregate outright
    and this test would never reach the classification it is about.
    """
    mock_dir = ROOT / "skills" / "fomo-kernel" / "mock"
    source = (mock_dir / "sample_tw_mixed.csv").read_text(
        encoding="utf-8").strip().splitlines()
    prices = mock_dir / "sample_tw_mixed.prices.json"
    header, body = source[0], source[1:]
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        env = _offline_engine_env(tmp)
        first_half = pathlib.Path(tmp) / "tw_week1.csv"
        first_half.write_text("\n".join([header] + body[:len(body) // 2]) + "\n",
                              encoding="utf-8")
        whole = pathlib.Path(tmp) / "tw_week2.csv"
        whole.write_text("\n".join(source) + "\n", encoding="utf-8")

        _book_review(tmp, root, first_half, env, "tw1", "first_review", prices=prices)
        plan = _book_review(tmp, root, whole, env, "tw2", "weekly_review", prices=prices)

        events, _ = ledger_engine.load_ledger(os.path.join(root, "ledger.jsonl"))
        book = ledger_engine.derive_holdings(events)["holdings"]
        assert any(ticker.endswith((".TW", ".TWO")) for ticker in book), \
            f"this fixture must actually hold a non-US position: {sorted(book)}"

        detail = ((plan["engine_card"].get("data_integrity") or {})
                  .get("accounting_reconciliation") or {})
        assert not detail.get("mismatches"), (
            "a non-US book whose file covers it must not be reported as "
            f"misclassified: {detail.get('mismatches')}")
        sizing = _sizing_dim(plan)
        assert sizing is not None and sizing["sizing_coverage"]["total_holdings"] == len(book)


_FX_HEADER = "Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency"
_FX_TWD_ROWS = ["2330.TW,BUY,1000,550.00,2024-01-05,Trade,TW,TWD",
                "2330.TW,SELL,200,600.00,2024-02-05,Trade,TW,TWD"]
_FX_USD_ROWS = ["AAPL,BUY,100,155.00,2024-01-08,Trade,US,USD",
                "AAPL,SELL,20,180.00,2024-02-08,Trade,US,USD",
                "MSFT,BUY,30,285.00,2024-01-20,Trade,US,USD"]
_FX_CLOSES = [{"ticker": "2330.TW", "close": 1050.0, "date": "2026-07-30", "currency": "TWD"},
              {"ticker": "AAPL", "close": 210.0, "date": "2026-07-30", "currency": "USD"},
              {"ticker": "MSFT", "close": 430.0, "date": "2026-07-30", "currency": "USD"}]


def _fx_case(tmp, name, rows, fx=None, prices=_FX_CLOSES):
    """A fictional book plus the envelope it is reviewed with."""
    csv_path = pathlib.Path(tmp) / f"{name}.csv"
    csv_path.write_text("\n".join([_FX_HEADER] + rows) + "\n", encoding="utf-8")
    envelope = pathlib.Path(tmp) / f"{name}.prices.json"
    payload = {"schema_version": 1, "as_of": "2026-07-30", "source": "test fixture",
               "prices": [dict(row) for row in prices]}
    if fx:
        payload["fx"] = [{"currency": currency, "usd_per_unit": rate, "date": "2026-07-30"}
                         for currency, rate in sorted(fx.items())]
    envelope.write_text(json.dumps(payload), encoding="utf-8")
    return csv_path, envelope


def _fx_plan(root):
    return json.loads(next(pathlib.Path(root).glob(".pending/*/plan.json")).read_text())


def _fx_sizing(plan):
    return next(dim for dim in plan["engine_card"]["dims_raw"] if "weights" in dim)


def test_a_review_refuses_a_held_currency_it_has_no_rate_for_and_converts_when_it_does():
    """#612, driven through the real CLI rather than through `usd_view`.

    `trade_recap.usd_view` used to resolve a currency absent from `fx` as a
    factor of 1.0, so a 840,000 TWD position entered a USD denominator at face
    value. On this fixture that reads as ~97% of the book instead of ~47%, which
    is not a gap in the numbers — it is a different number, and every aggregate
    downstream (weights, concentration, the diagnosis order, `what_if`, the
    persisted metrics) is built on it. #602 already made `consider` refuse this
    book; the review lane never inherited it.

    Both halves are here on purpose. A refusal test alone stays green when the
    supply side stops delivering rates at all, which is the decorative shape
    this repository keeps paying for; the converted counterweight is what makes
    the pair mean "the rate arrived and was used".
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _offline_engine_env(tmp)
        mixed = _FX_TWD_ROWS + _FX_USD_ROWS
        csv_path, no_rate = _fx_case(tmp, "mixed_no_rate", mixed)
        refused_root = pathlib.Path(tmp) / "refused"
        run = _run("prepare", csv_path, "--root", refused_root, "--language", "en",
                   "--prices", no_rate, env=env)

        # One controlled CLI error, not a traceback and not a partial artifact.
        assert run.returncode == 2, run.stdout + run.stderr
        payload = json.loads(run.stdout)
        assert payload["status"] == "error"
        assert "TWD" in payload["error"], payload["error"]
        assert "--prices" in payload["error"] and "fx" in payload["error"], payload["error"]
        assert "Traceback" not in run.stdout and "Traceback" not in run.stderr
        # #611's machine-readable cause is quoted rather than provider prose
        # being parsed for it.
        assert "fx_unavailable" in payload["error"], payload["error"]

        # Nothing canonical was written: no session, no ledger, no card. The
        # empty projection lock is the transaction boundary itself, which is
        # taken before the engine runs and carries no review content.
        written = {str(path.relative_to(refused_root))
                   for path in refused_root.rglob("*")} if refused_root.exists() else set()
        assert not [name for name in written if name.startswith(".pending")], written
        assert "ledger.jsonl" not in written, written
        assert not [name for name in written if "card" in name or "session" in name.rstrip("s")
                    and name.endswith(".json")], written

        # The same book, the same run, with the rate supplied.
        _csv2, with_rate = _fx_case(tmp, "mixed_rate", mixed, fx={"TWD": 0.0317})
        priced_root = pathlib.Path(tmp) / "priced"
        ok = _run("prepare", csv_path, "--root", priced_root, "--language", "en",
                  "--prices", with_rate, env=env)
        assert ok.returncode == 0, ok.stdout + ok.stderr
        plan = _fx_plan(priced_root)
        sizing = _fx_sizing(plan)
        # 800 x 1050 TWD is 840,000 face value beside 16,800 and 12,900 USD.
        # Converted it is ~26,600 USD: the largest holding, but under half the
        # book rather than nearly all of it.
        assert sizing["max_ticker"] == "2330.TW"
        assert 0.45 < sizing["max_pct"] < 0.50, sizing["max_pct"]
        assert sizing["weights"]["AAPL"] > 0.25, sizing["weights"]
        assert plan["engine_card"]["currency_meta"]["fx"] == {"TWD": 0.0317}
        # The disclosure this refusal replaces is gone rather than always empty
        # (#429, the same removal #600 made on the consider side).
        assert "fx_gaps" not in (plan["engine_card"].get("data_integrity") or {})
        assert (priced_root / "ledger.jsonl").exists()


def test_ticker_diagnosis_price_note_carries_the_original_currency_not_the_usd_view():
    """#750, driven through the real CLI. `ticker_diagnosis`'s #347 disclosure
    fields (`px`/`avg_cost` -- the numbers `price_note` prints beside `cur_ret`
    on the card, e.g. "現 {px}／均 {avg_cost}") rode the same `usd_view()`
    aggregate the ranking/`too_heavy` threshold legitimately needs, in
    violation of `usd_view()`'s own docstring contract: "per-ticker
    presentation (ticker_diagnosis / best_worst / the card's single-ticker
    numbers) always uses the original-currency, original objects." A TWD
    position bought at 550/share and now worth 1050 printed an "avg cost" of
    ~17.4 -- the USD-equivalent divided by the display fx rate -- silently
    contradicting the correct TWD total sitting beside it on the same card
    line, and every reader's most natural sanity check ("does this match what
    I paid?") failed.

    This must run on a genuinely mixed TWD+USD book, not a pure-USD one: for a
    USD ticker `usd_view()`'s factor is `fx["USD"] == 1.0`, an identity, so a
    pure-USD fixture cannot tell "correctly carries its own currency" apart
    from "the currency silently collapsed to the USD-aggregate value" -- both
    read the same number. That is exactly why the report saw AAPL unaffected
    and only the TWD ticker corrupted; AAPL is this test's control for the
    same reason the sibling #612 test above (currency it has no rate for /
    converts when it does) runs both a refusal and a converted counterweight:
    "both halves are here on purpose... the converted counterweight is what
    makes the pair mean the rate arrived and was used."
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _offline_engine_env(tmp)
        mixed = _FX_TWD_ROWS + _FX_USD_ROWS
        csv_path, envelope = _fx_case(tmp, "ticker_diag_fx", mixed, fx={"TWD": 0.0317})
        root = pathlib.Path(tmp) / "root"
        run = _run("prepare", csv_path, "--root", root, "--language", "en",
                   "--prices", envelope, env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        plan = _fx_plan(root)

        diagnosis = {row["ticker"]: row for row in plan["engine_card"]["ticker_diagnosis"]}
        assert "2330.TW" in diagnosis, sorted(diagnosis)

        def priced_params(ticker):
            tags = {tag["code"]: tag["params"] for tag in diagnosis[ticker]["tags"]}
            for code in ("disciplined_hold", "deep_underwater",
                        "suspected_averaging_down_losing"):
                if code in tags and tags[code].get("px") is not None:
                    return tags[code]
            raise AssertionError(f"{ticker} carries no #347 priced tag: {tags}")

        # 1000 bought @550, 200 sold @600 (FIFO leaves the remaining 800 shares
        # at their own 550 cost) -> 800 held @550 avg cost; current close 1050
        # (both TWD, from _fx_case's default price envelope). cur_ret = +90.9%,
        # so this fires `disciplined_hold`.
        tw = priced_params("2330.TW")
        assert abs(tw["px"] - 1050.0) < 1e-6, tw
        assert abs(tw["avg_cost"] - 550.0) < 1e-6, tw
        # The corrupted values this bug produced: the USD-aggregate view
        # (original x 0.0317) divided a second time by nothing -- i.e. the
        # figure a reader would see is the *converted* price, not the paid
        # one. Assert the bug's actual output is absent, not just "some other
        # number": a regression that produces a different wrong value must
        # still fail this test.
        assert abs(tw["px"] - 1050.0 * 0.0317) > 1.0, tw
        assert abs(tw["avg_cost"] - 550.0 * 0.0317) > 1.0, tw

        # Control (#612's sibling argument): AAPL is a USD ticker, so
        # usd_view()'s factor was always an identity for it -- this must have
        # been correct even with the bug present, or the test proves nothing
        # about which currency actually rode the aggregate.
        # 100 bought @155, 20 sold @180 -> 80 held @155; current close 210.
        us = priced_params("AAPL")
        assert abs(us["px"] - 210.0) < 1e-6, us
        assert abs(us["avg_cost"] - 155.0) < 1e-6, us


def test_the_612_refusal_is_repaired_by_fx_alone_with_no_closes_supplied():
    """#642. The refusal above names its own repair -- "Supply the rate through
    --prices (the `fx` block...)" -- but the schema required `prices` with
    `minItems: 1`, so there was no way to hand back an envelope carrying only
    the missing rate. The two gaps are independent: a host that can read one
    public FX rate off a central bank or exchange page often cannot also
    transcribe every instrument's close in the same pass, and demanding both to
    clear a refusal that is purely about the rate pushes toward inventing
    prices, which `references/price-feed.md` forbids in its strongest terms.

    This is the round trip: the same book, still refused with no envelope
    (proven by the sibling test above), unblocked here by an envelope that
    supplies `fx` and nothing else -- `prices` genuinely absent, not merely
    empty-and-ignored.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _offline_engine_env(tmp)
        mixed = _FX_TWD_ROWS + _FX_USD_ROWS
        csv_path, fx_only = _fx_case(tmp, "mixed_fx_only", mixed,
                                     fx={"TWD": 0.0317}, prices=[])
        payload = json.loads(pathlib.Path(fx_only).read_text(encoding="utf-8"))
        assert "prices" not in payload or payload["prices"] == [], (
            "this envelope must genuinely carry no closes, or the round trip "
            f"below proves nothing about the fx-only repair: {payload}")

        root = pathlib.Path(tmp) / "fx_only"
        run = _run("prepare", csv_path, "--root", root, "--language", "en",
                   "--prices", fx_only, env=env)
        assert run.returncode == 0, run.stdout + run.stderr

        plan = _fx_plan(root)
        sizing = _fx_sizing(plan)
        # The rate alone is enough to fix the denominator: 2330.TW is still the
        # largest position, at its real converted share -- not the ~97% a
        # TWD-treated-as-USD face value would read as (the bug #612 fixed), and
        # not the fully-priced sibling test's ~47% either, because no closes
        # were supplied here. Every holding honestly falls back to cost, named
        # in sizing_coverage rather than silently computed as if it were a
        # current market value.
        assert sizing["max_ticker"] == "2330.TW"
        assert 0.35 < sizing["max_pct"] < 0.45, sizing["max_pct"]
        assert sizing["sizing_coverage"]["priced"] == [], sizing["sizing_coverage"]
        assert sorted(sizing["sizing_coverage"]["cost_fallback"]) == ["2330.TW", "AAPL", "MSFT"], \
            sizing["sizing_coverage"]
        assert plan["engine_card"]["currency_meta"]["fx"] == {"TWD": 0.0317}
        assert "fx_gaps" not in (plan["engine_card"].get("data_integrity") or {})

        # No closes were supplied, so the review stays honestly degraded for
        # price-dependent numbers -- an fx-only envelope clears the currency
        # refusal, it does not manufacture prices nobody sent. `recovery`
        # still reads "supplied": an envelope arrived, whatever it covered.
        feed_status = plan["input"]["price_feed"]
        assert feed_status["provenance"]["mode"] == "unavailable", feed_status
        assert feed_status["provenance"]["fx"] == "feed", feed_status
        assert feed_status["recovery"] == {"attempted": True, "outcome": "supplied"}, feed_status
        assert (root / "ledger.jsonl").exists()


"""#649's fixture: a US-only stock book whose only foreign currency arrives in a
cash-flow row. Synthetic throughout. The shape is ordinary — a Taiwanese
sub-brokerage account trading US stocks with interest posting in TWD, an IBKR
account whose base-currency interest accrues beside a single-market book, any
account with an FX-conversion fee logged at home. It takes one such row."""
_CASH_CCY_HEADER = "TradeDate,Action,Symbol,Quantity,Price,Amount,Currency,RecordType"
_CASH_CCY_TRADES = ["2025-01-06,BUY,AAPL,100,200.00,-20000.00,USD,Trade",
                    "2025-01-06,BUY,MSFT,50,380.00,-19000.00,USD,Trade",
                    "2025-03-10,SELL,AAPL,40,240.00,9600.00,USD,Trade"]
_CASH_CCY_TWD_INTEREST = "2025-04-01,INTEREST,,,,1000000.00,TWD,Interest"
_CASH_CCY_USD_INTEREST = "2025-04-01,INTEREST,,,,120.00,USD,Interest"
_CASH_CCY_CLOSES = [{"ticker": "AAPL", "close": 210.0, "date": "2026-07-30", "currency": "USD"},
                    {"ticker": "MSFT", "close": 430.0, "date": "2026-07-30", "currency": "USD"}]
_CASH_CCY_ANCHOR = json.dumps([{"currency": "TWD", "amount": 1000000, "as_of": "2025-04-02"},
                               {"currency": "USD", "amount": 5000, "as_of": "2025-04-02"}])


def _cash_ccy_case(tmp, name, rows, fx=None, prices=_CASH_CCY_CLOSES):
    csv_path = pathlib.Path(tmp) / f"{name}.csv"
    csv_path.write_text("\n".join([_CASH_CCY_HEADER] + rows) + "\n", encoding="utf-8")
    envelope = pathlib.Path(tmp) / f"{name}.prices.json"
    payload = {"schema_version": 1, "as_of": "2026-07-30", "source": "test fixture",
               "prices": [dict(row) for row in prices]}
    if fx:
        payload["fx"] = [{"currency": currency, "usd_per_unit": rate, "date": "2026-07-30"}
                         for currency, rate in sorted(fx.items())]
    envelope.write_text(json.dumps(payload), encoding="utf-8")
    return csv_path, envelope


def test_a_currency_only_a_cash_flow_row_carries_is_fetched_and_refused_when_absent():
    """#649, through the real CLI.

    #612 put the FX refusal in the primitive that converts *holdings*, and its
    domain was `cur_map` — which `load()` fills from BUY/SELL rows only.
    `load_cash_flows` reads a separate per-row `Currency`, so a currency present
    only there was invisible to the refusal *and* to the fetch decision that
    shares the same set. On this book that was not a rate that failed to arrive:
    none was ever requested. `cash_position` then added 1,000,000 raw TWD into a
    USD total at 1.0 — a balance 28x its real size — while every honesty surface
    said the opposite: `currency_meta.mixed: false`, `fx: "not_needed"`,
    `basis.fx_approx: false`, `unanchored: []`, `cash_source: "anchored"`. That
    is a strictly worse posture than the pre-#612 holdings case, which at least
    degraded into a disclosed `fx_gaps` list.

    Both halves on purpose, matching the #612 pair above: a refusal test alone
    stays green when the supply side stops delivering rates at all, and the
    converted counterweight is what makes the pair mean "TWD was asked for, the
    rate arrived, and it was used".
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _offline_engine_env(tmp)
        rows = _CASH_CCY_TRADES + [_CASH_CCY_TWD_INTEREST]

        # 1. No rate: refused before anything is computed or persisted.
        csv_path, no_rate = _cash_ccy_case(tmp, "cash_ccy_no_rate", rows)
        refused_root = pathlib.Path(tmp) / "refused"
        run = _run("prepare", csv_path, "--root", refused_root, "--language", "en",
                   "--prices", no_rate, "--cash", _CASH_CCY_ANCHOR, env=env)
        assert run.returncode == 2, run.stdout + run.stderr
        payload = json.loads(run.stdout)
        assert payload["status"] == "error"
        assert "TWD" in payload["error"], payload["error"]
        assert "--prices" in payload["error"] and "fx" in payload["error"], payload["error"]
        assert "Traceback" not in run.stdout and "Traceback" not in run.stderr
        # `_from_feed` emits `fx_unavailable` per *requested* currency, so this
        # code is itself the evidence that TWD entered the acquisition request —
        # the half of #649 that a refusal message alone would not prove.
        assert "fx_unavailable" in payload["error"], payload["error"]
        written = {str(path.relative_to(refused_root))
                   for path in refused_root.rglob("*")} if refused_root.exists() else set()
        assert not [name for name in written if name.startswith(".pending")], written
        assert "ledger.jsonl" not in written, written

        # 2. The same book with the rate supplied: converted, and honest about it.
        _csv2, with_rate = _cash_ccy_case(tmp, "cash_ccy_rate", rows, fx={"TWD": 0.030849})
        priced_root = pathlib.Path(tmp) / "priced"
        ok = _run("prepare", csv_path, "--root", priced_root, "--language", "en",
                  "--prices", with_rate, "--cash", _CASH_CCY_ANCHOR, env=env)
        assert ok.returncode == 0, ok.stdout + ok.stderr
        card = _fx_plan(priced_root)["engine_card"]
        meta = card["currency_meta"]
        assert meta["mixed"] is True and meta["currencies"] == ["TWD", "USD"], meta
        assert meta["fx"] == {"TWD": 0.030849}, meta
        assert card["price_provenance"]["fx"] == "feed", card["price_provenance"]
        # 1,000,000 TWD is 30,849 USD beside 5,000 USD: 35,849, not 1,005,000.
        cash = card["cash"]
        assert abs(cash["balance"] - 35849.0) < 0.01, cash
        assert 0.50 < cash["weight"] < 0.53, cash                 # not the 0.967 of the 1.0 sum
        assert cash["by_currency"]["TWD"]["balance"] == 1000000.0, cash   # per-currency stays raw
        # The mix is now stated rather than denied.
        assert [entry for entry in card["honesty_ledger"]
                if entry["key"] == "currency_mix"], card["honesty_ledger"]
        assert (priced_root / "ledger.jsonl").exists()


def test_a_book_whose_cash_rows_share_the_stock_currency_is_byte_stable():
    """#649's regression half: a genuinely single-currency book must not change.

    The universe now spans cash-flow rows, so the book that must be proven
    unchanged is not only a trade-only one — it is a book that *has* cash-flow
    rows in the same currency it trades in. Neither of these may request a rate,
    report a mix, or convert anything, whichever currency it is denominated in.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _offline_engine_env(tmp)
        twd_closes = [{"ticker": "2330.TW", "close": 1050.0,
                       "date": "2026-07-30", "currency": "TWD"}]
        twd_rows = ["2024-01-05,BUY,2330.TW,1000,550.00,-550000.00,TWD,Trade",
                    "2024-02-05,SELL,2330.TW,200,600.00,120000.00,TWD,Trade",
                    "2024-03-01,INTEREST,,,,900.00,TWD,Interest"]
        for name, rows, closes, aggregate in (
                ("cash_pure_usd", _CASH_CCY_TRADES + [_CASH_CCY_USD_INTEREST],
                 _CASH_CCY_CLOSES, "USD"),
                ("cash_pure_twd", twd_rows, twd_closes, "TWD")):
            csv_path, envelope = _cash_ccy_case(tmp, name, rows, prices=closes)
            root = pathlib.Path(tmp) / name
            run = _run("prepare", csv_path, "--root", root, "--language", "en",
                       "--prices", envelope, env=env)
            assert run.returncode == 0, name + ": " + run.stdout + run.stderr
            card = _fx_plan(root)["engine_card"]
            meta = card["currency_meta"]
            assert meta["mixed"] is False and meta["fx"] is None, (name, meta)
            assert meta["currencies"] == [aggregate], (name, meta)
            assert meta["aggregate_currency"] == aggregate, (name, meta)
            # No rate was requested, so none can be reported missing: the
            # "單一幣別組合...零行為變化" convention, still intact.
            assert card["price_provenance"]["fx"] == "not_needed", (name, card["price_provenance"])
            assert not [entry for entry in card["honesty_ledger"]
                        if entry["key"] == "currency_mix"], (name, card["honesty_ledger"])


def test_single_currency_and_display_only_gaps_are_untouched_by_the_fx_refusal():
    """#612's two compatibility halves, through the CLI.

    A single-currency book — including a pure non-USD one — aggregates itself
    self-consistently and never requested a rate. And `TR_DISPLAY_CURRENCY` is a
    presentation preference, not a held currency: `fx_request_currencies` widens
    the *request* for rendering, so a missing display rate must stay a rendering
    degradation. Both would be false positives of a predicate written over the
    requested currencies instead of the held ones.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _offline_engine_env(tmp)
        for name, rows, closes in (
                ("pure_usd", _FX_USD_ROWS, [row for row in _FX_CLOSES if row["currency"] == "USD"]),
                ("pure_twd", _FX_TWD_ROWS, [row for row in _FX_CLOSES if row["currency"] == "TWD"])):
            csv_path, envelope = _fx_case(tmp, name, rows, prices=closes)
            root = pathlib.Path(tmp) / name
            run = _run("prepare", csv_path, "--root", root, "--language", "en",
                       "--prices", envelope, env=env)
            assert run.returncode == 0, name + ": " + run.stdout + run.stderr
            meta = _fx_plan(root)["engine_card"]["currency_meta"]
            assert meta["mixed"] is False and meta["fx"] is None, meta
            assert meta["aggregate_currency"] == ("USD" if name == "pure_usd" else "TWD"), meta

        # zh-CN asks for CNY, which this book neither holds nor supplies. The
        # held book (TWD + USD) is fully covered, so the review completes and
        # only the display conversion degrades.
        csv_path, envelope = _fx_case(tmp, "display_only", _FX_TWD_ROWS + _FX_USD_ROWS,
                                      fx={"TWD": 0.0317})
        root = pathlib.Path(tmp) / "display_only"
        run = _run("prepare", csv_path, "--root", root, "--language", "zh-CN",
                   "--prices", envelope, env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        meta = _fx_plan(root)["engine_card"]["currency_meta"]
        assert meta["requested_display_currency"] == "CNY", meta
        assert meta["display_fx_source"] == "unavailable", meta
        assert _fx_sizing(_fx_plan(root))["max_ticker"] == "2330.TW"
        assert 0.45 < _fx_sizing(_fx_plan(root))["max_pct"] < 0.50, \
            "a display-only gap must not disturb the held-currency aggregate"


def test_only_the_adapter_lane_skips_the_recorded_book_reconciliation():
    """#630's one deliberate asymmetry, pinned so it stays deliberate.

    `cmd_prepare` reaches `_ingest_trades` only when the caller supplied
    `--card-json`/`--state-json`: there `engine_state.holdings` was asserted by
    that caller and is not a derivation of the CSVs beside it, so reconciling
    the two would compare an artifact with an unrelated file rather than a book
    with itself. Every review a real user runs freezes its inputs and takes
    `_verify_and_ingest_frozen_trades`, which does reconcile.

    This fails if a plain CSV review ever stops taking that lane — the way the
    defect would come back — and it drives both shapes through the real CLI
    rather than reading the branch conditions.
    """
    week_rows = _ONE_TICKER_WEEK
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        env = _offline_engine_env(tmp)
        _book_review(tmp, root, _BOOK_FIXTURE, env, "d1", "first_review")
        week = pathlib.Path(tmp) / "week.csv"
        week.write_text(week_rows, encoding="utf-8")
        plain = _book_review(tmp, root, week, env, "d2", "weekly_review")
        assert ((plain["engine_card"].get("data_integrity") or {})
                .get("accounting_reconciliation") or {}).get("mismatches"), \
            "a CSV review with no injected artifacts must reconcile against the book"

    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        env = _offline_engine_env(tmp)
        _book_review(tmp, root, _BOOK_FIXTURE, env, "e1", "first_review")
        week = pathlib.Path(tmp) / "week.csv"
        week.write_text(week_rows, encoding="utf-8")
        card_path, state_path = _artifacts(tmp)
        run = _run("prepare", week, "--root", root, "--route", "weekly_review",
                   "--language", "en", "--session-nonce", "e2",
                   "--card-json", card_path, "--state-json", state_path, env=env)
        assert run.returncode == 0, run.stdout + run.stderr
        adapter = _pending_plan(root, run.stdout)
        assert not ((adapter["engine_card"].get("data_integrity") or {})
                    .get("accounting_reconciliation")), \
            "the adapter lane asserts its own artifacts; reconciling them is out of scope"


# ── #670: the card shows the magnitude that ranked the list ──────────────────
# layout-constraints.md §6 ruling 3 — "everything ranks by size of money
# impact, never by percentage return" — was implemented on the sort side and
# violated on the render side: the focus line printed three rates and never
# the figure that ordered it.


def _backlog_bundle(items, language="en"):
    return {"session_id": "backlog", "route": "weekly_review", "language": language,
            "review_plan": {"question_queue": [], "state_snapshot": {"exit_backlog": {
                "summary": {"count": len(items), "full": len(items), "reduce": 0,
                            "span": {}, "top_tickers": []},
                "items": items}}},
            "engine_card": {}, "engine_state": {}}


def _focus_lines(items, language="en"):
    _pairs, lines = card_renderer._exit_followup_entries(
        _backlog_bundle(items, language), card_renderer.load_copy(language))
    return [line for line in lines if "Backlog focus" in line or "優先回看" in line]


def test_backlog_focus_prints_the_magnitude_that_ranked_it_and_no_rate():
    """The mechanism, not this call site: ruling 3 bans a rate as the figure.

    Asserting the absence of any rate glyph is what generalizes — a future
    template that reintroduces a return through some other copy key fails this
    without the check having to name that key. `design-guidelines.md` §5 is
    explicit that a rule naming a field only blocks one trigger, and naming a
    field is exactly how ruling 3 came to be violated here.
    """
    line = _focus_lines([{"ticker": "AAA", "kind": "full", "exit_date": "2026-02-03",
                          "impact": -18400.0, "currency": "TWD",
                          "compare": {"swap_net_pp": 1.0, "orig_ret": 0.1, "swap_ret": 0.2}}])[0]
    assert "TWD 18,400" in line, f"the ranked money figure must be on the line: {line}"
    assert "%" not in line and "pp" not in line, \
        f"ruling 3: the focus line must not carry a return rate: {line}"
    assert "18,400" in line and "0.1" not in line, \
        "the money figure is the engine's `impact`, not a rate restated"


def test_the_focus_figure_is_measured_in_the_instruments_own_currency():
    """A TWD parcel restated with the aggregate's symbol would be a false number."""
    line = _focus_lines([{"ticker": "AAA", "kind": "full", "exit_date": "2026-02-03",
                          "impact": -18400.0, "currency": "TWD", "compare": {}}])[0]
    assert "TWD" in line and "$18,400" not in line, \
        f"the parcel's own currency must survive to the card: {line}"


def test_a_trim_and_a_full_exit_of_the_same_move_rank_and_read_differently():
    """`impact = notional x net move`, so a small parcel of a large mover
    reports a small figure without the line ever explaining the fraction."""
    import revisit as revisit_engine
    # Every checkpoint closed before this queue started tracking them, which is
    # what makes an exit historical backlog rather than a due revisit (#170).
    # Non-USD deliberately: the renderer falls back to "USD" for a missing
    # currency, so a USD fixture cannot tell a carried currency from a dropped
    # one — which is exactly what let the engine-side field go unproved.
    common = {"exit_date": "2026-02-03", "cycle_id": "X#2026-01-01#1",
              "exit_price": 100.0, "currency": "TWD", "swaps": [],
              "enqueued_at": "2026-07-14",
              # `idle_cash` is `not swaps` on the real enqueue path, so proceeds
              # that bought nothing carry an opportunity cost the engine prices.
              "idle_cash": True,
              "due": {"30": "2026-03-05", "60": "2026-04-04", "90": "2026-05-04"}}
    revisits = {
        "full-rid": dict(common, revisit_id="full-rid", ticker="FULL",
                         kind="full", shares_sold=100, shares_before=100),
        "trim-rid": dict(common, revisit_id="trim-rid", ticker="TRIM",
                         kind="reduce", shares_sold=5, shares_before=100),
    }
    items, summary, _total = revisit_engine.scan_backlog(
        revisits, [], prices={"FULL": 200.0, "TRIM": 200.0})
    by_ticker = {item["ticker"]: item for item in items}
    assert by_ticker["FULL"]["impact"] is not None and by_ticker["TRIM"]["impact"] is not None
    assert abs(by_ticker["TRIM"]["impact"]) < abs(by_ticker["FULL"]["impact"]), \
        "the same underlying move on a 5% parcel must not price like the whole position"
    assert items[0]["ticker"] == "FULL", "the larger money impact leads the list"
    assert all(item["currency"] == "TWD" for item in items), \
        "the engine must carry each parcel's own currency onto the item it ranks"
    line = _focus_lines(items)[0]
    assert "TWD" in line and "$" not in line, \
        f"an engine-built item must reach the card in its own currency: {line}"
    # The pooled hindsight figure is a full-exit statistic; a trim's post-exit
    # move measures the parcel that left while the shares that stayed captured
    # the same move, so pooling the two averages different quantities.
    assert summary["priced"] == 1, \
        f"only the full exit is price-covered for the pooled average: {summary}"


def test_a_backlog_item_without_impact_still_renders():
    """A bundle prepared before #670 degrades to the original-move sentence
    rather than losing the line — the key is absent, not empty."""
    line = _focus_lines([{"ticker": "AAA", "kind": "full", "exit_date": "2026-02-03",
                          "compare": {"orig_ret": 0.12}}])[0]
    assert "+12.0%" in line, f"the pre-#670 shape must still say something: {line}"


# ── #714: value before the first question ────────────────────────────────────
# The route asked three to five questions before anything the engine had found
# was visible, so a first-time user completed an interview to learn whether the
# product found anything at all. These pin both halves of the repair: the
# opening the user reads first, and the per-question statement of what
# answering buys.


def test_opening_value_leads_a_first_review_before_any_question():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        card_path, state_path = _artifacts(tmp)
        run = _run("prepare", _trade_csv(tmp), "--root", root, "--language", "en",
                   "--card-json", card_path, "--state-json", state_path)
        assert run.returncode == 0, run.stdout + run.stderr
        projected = json.loads(run.stdout)["review_plan"]
        assert projected["route"] == "first_review"
        opening = projected.get("opening_value")
        assert opening, \
            "a first review must state what the engine already found before it interviews the user"
        assert opening["schema_version"] == 1
        assert opening["finding"]["line"].strip(), "the finding must carry a real sentence"
        assert opening["label"].strip(), "the opening must be visibly labelled preliminary"
        required = [q for q in projected["question_queue"] if q.get("required")]
        assert opening["questions_required"] == len(required), \
            "the stated count is the queue's own required rows, never a separate guess"
        assert opening["questions_line"].strip()
        # A second route with a different band: this fixture selects three
        # required rows, so `== len(required)` alone still passes a hardcoded 3.
        drive = _run("prepare", "--test-drive", "--language", "en")
        assert drive.returncode == 0, drive.stdout + drive.stderr
        drive_plan = json.loads(drive.stdout)["review_plan"]
        drive_opening = drive_plan["opening_value"]
        drive_required = [q for q in drive_plan["question_queue"] if q.get("required")]
        assert drive_opening["questions_required"] == len(drive_required) \
            and drive_opening["questions_required"] != opening["questions_required"], \
            "the count must track each route's own queue, not one route's number twice"
        # The opening exists so the agent never needs the card it came from.
        assert "engine_card" not in projected and "engine_state" not in projected, \
            "showing value early must not become a reason to hand the agent the raw card"


def test_opening_value_repeats_the_cards_own_leading_finding():
    """It projects facts the renderer already ranked; a second pick could disagree."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _csv, _card, _state = _prepare_with_trades(tmp, root, language="en")
        opening = plan["opening_value"]
        expected = ""
        for hole in plan["engine_card"].get("top_holes") or []:
            expected = card_renderer._hole_line(hole, "en")
            if expected:
                break
        assert opening["finding"]["line"] == expected, \
            "the opening must quote the leading hole the card would print, not rank its own"
        ledger = plan["engine_card"].get("honesty_ledger") or []
        honesty = card_renderer._honesty_lines({"engine_card": plan["engine_card"]},
                                               card_renderer.load_copy("en"))
        # Not `if "boundary" in opening`: a mutation that drops the leading
        # entry would delete the block and pass. State the expectation for
        # both outcomes instead.
        speakable = [entry["key"] for entry in ledger if entry.get("key") in honesty]
        if speakable:
            assert opening.get("boundary"), \
                "a triggered, speakable ledger entry must reach the opening"
            assert opening["boundary"]["key"] == speakable[0], \
                "the boundary follows the ledger's own order, not a relevance judgment"
        else:
            assert "boundary" not in opening, \
                "with nothing triggered the opening states no limitation at all"


def test_opening_value_is_localized_in_every_supported_locale():
    seen = {}
    with tempfile.TemporaryDirectory() as tmp:
        for language in ("en", "zh-TW", "zh-CN"):
            root = pathlib.Path(tmp) / f"coach-{language}"
            plan, _csv, _card, _state = _prepare_with_trades(tmp, root, language=language)
            opening = plan["opening_value"]
            assert opening["label"].strip() and opening["questions_line"].strip(), \
                f"{language}: the opening must not fall back to an empty catalog entry"
            seen[language] = (opening["label"], opening["questions_line"])
    assert len(set(seen.values())) == 3, \
        "each locale carries its own wording, not one language's copy rendered three times"


def test_answer_effect_reaches_every_wired_required_question():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, _csv, _card, _state = _prepare_with_trades(tmp, root, language="en")
        wired = [q for q in plan["question_queue"]
                 if q.get("required") and q["kind"] in review_engine.ANSWER_EFFECT_KINDS]
        assert wired, "this fixture must select at least one wired required question"
        for question in wired:
            effect = question.get("answer_effect")
            assert effect and effect.strip(), \
                f"{question['kind']}: a required question must state what answering changes"
            assert effect != question.get("asked_because"), \
                f"{question['kind']}: why it was asked and what it changes are different facts"


def test_answer_effect_kinds_are_exactly_the_wired_question_consumers():
    """#714/#429: an effect sentence is a promise, so only a wired kind may make it."""
    import ast
    tree = ast.parse((ROOT / "evals" / "run_episodes.py").read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) in ("QUESTION_CONSUMERS", "KNOWN_UNWIRED"):
                    found[target.id] = {ast.literal_eval(k) for k in node.value.keys}
    assert set(found) == {"QUESTION_CONSUMERS", "KNOWN_UNWIRED"}, \
        "both registers must stay readable from run_episodes.py source"
    assert set(review_engine.ANSWER_EFFECT_KINDS) == found["QUESTION_CONSUMERS"] - found["KNOWN_UNWIRED"], \
        ("answer_effect must cover exactly the kinds whose answer provably reaches a surface: "
         "a kind that becomes wired earns a sentence, one that stops being read loses it")


def test_no_answer_effect_where_nothing_reads_the_answer():
    for kind in ("initial_thesis", "exit_consistency"):
        assert review_engine._answer_effect(kind, "en") is None, \
            f"{kind}: #429 says nothing reads this answer; promising an effect would be a lie"
    for language in ("en", "zh-TW", "zh-CN"):
        table = card_renderer.load_copy(language).get("answer_effect") or {}
        assert set(table) == set(review_engine.ANSWER_EFFECT_KINDS), \
            f"{language}: the catalog carries a sentence for every wired kind and no other"


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
