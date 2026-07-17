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
import instruments  # noqa: E402
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
        elif question["kind"] == "rule_breach":
            answers.append({"question_id": question["id"], "choice": "keep_tracking"})
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
    assert "TW 部位報酬 20%" in text and "同期 ^TWII 10%" in text and "β 1.10" in text
    assert "US 部位報酬 5%" in text and "同期 SPY 8%" in text and "β 0.80" in text
    assert "TW 贏大盤的 +10 個百分點" in text
    assert "市場／賽道配置 +4 個百分點" in text and "標的選擇 +6 個百分點" in text
    assert text.count("賽道與選股拆帳不完整") == 1, \
        "the engine-triggered attribution caveat must be placed exactly once"
    assert "US 贏大盤" not in text, "a losing market must not be described as beating its benchmark"
    assert "999%" not in text and "99.00" not in text and "PRIVATE_MARKET" not in text, \
        "mixed cards must never render the top-level scope row as a combined third result"


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


def test_recent_exit_capture_is_ranked_bounded_canonical_and_private_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "coach"
        plan, csv_path, card_path, state_path = _prepare_with_trades(tmp, root)
        assert plan["card_plan"]["question_limit"] == 3
        assert len(plan["question_queue"]) == 3, "one review asks at most three important questions"
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
    for fragment in ("SPY window +3.0%", "inferred thesis horizon was weeks",
                     "prices frozen on 2026-07-15", "swap net -15.0 pp",
                     "Historical exit backlog: 4",
                     "Across 3 price-covered exits, the average post-exit move was -3.0 pp; 1 later rose",
                     "Backlog focus: FOCUSSECRET, full exit on 2025-06-01",
                     "Proceeds stayed idle while the original moved +25.0% using prices frozen on 2026-07-15",
                     "Averaging-down boundary", "SECRET lesson"):
        assert fragment in private, fragment
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
    queue = review_engine._question_queue(
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
    return json.loads(run.stdout)["review_plan"]


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
                "recent_count", "recent_amount", "trend", "horizon_marker"):
        assert key in item["properties"], key
    bundle_schema = json.loads((SCHEMAS / "session-bundle.schema.json").read_text(encoding="utf-8"))
    resolutions = bundle_schema["properties"]["revisit_resolutions"]
    assert set(resolutions["items"]["properties"]["status"]["enum"]) == {"still_valid", "modified", "falsified"}
    # Absent-when-empty is the replay-compatibility contract, so it must stay optional.
    assert "revisit_resolutions" not in bundle_schema["required"]
    breach = bundle_schema["properties"]["rule_breach_decisions"]
    assert set(breach["items"]["properties"]["decision"]["enum"]) == \
        {"keep_tracking", "revise_rule", "exception"}
    assert "rule_breach_decisions" not in bundle_schema["required"]


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
