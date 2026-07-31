#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coach.py data-status / data-export / data-reset(#165)測試 — 全離線、確定性、免裝 pytest。

蓋什麼:
  A. data-status:空 root / 部分存在 / dir 型(cards)計數,不印交易內容本身。
  B. data-export:zip 內容與 present 清單一致,空 root 拒收。
  C. data-reset:--dry-run 不動檔案、--confirm 才真的刪、兩者互斥、裸執行(無旗標)拒收。
  D. --root 覆寫生效,絕不誤觸真正的 ~/.trade-coach/(隔離驗證的機械版本)。
  E. Registry completeness (#452): every coach-root path the engine's own
     source constructs is registered in coach.DATA_FILES -- generated from
     the source, not a second hand-authored list of expected files.
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "skills", "fomo-kernel", "engine")
COACH = os.path.join(ENGINE, "coach.py")
sys.path.insert(0, ENGINE)
import coach  # noqa: E402 -- only for DATA_FILES; every CLI behavior test below still shells out


def _run(*args):
    r = subprocess.run([sys.executable, COACH, *args], capture_output=True, text=True)
    return r


def _seed(root):
    """建一批假資料,涵蓋 json / jsonl / text / dir 四種 kind。"""
    os.makedirs(os.path.join(root, "cards"), exist_ok=True)
    with open(os.path.join(root, "last_state.json"), "w", encoding="utf-8") as f:
        f.write('{"a":1}')
    with open(os.path.join(root, "log.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"x":1}\n{"y":2}\n')
    with open(os.path.join(root, "profile.md"), "w", encoding="utf-8") as f:
        f.write("# profile\n")
    with open(os.path.join(root, "cards", "2026-07-13.md"), "w", encoding="utf-8") as f:
        f.write("card body\n")


# ─────────────── A. data-status ───────────────

def test_status_empty_root_all_absent():
    with tempfile.TemporaryDirectory() as tmp:
        r = _run("data-status", "--root", tmp)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["present_count"] == 0
        assert all(not e["exists"] for e in out["files"])


def test_status_reports_size_lines_and_dir_count():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp)
        r = _run("data-status", "--root", tmp)
        out = json.loads(r.stdout)
        by_name = {e["name"]: e for e in out["files"]}
        assert out["present_count"] == 4, out["present_count"]
        assert by_name["log.jsonl"]["lines"] == 2, "jsonl 要回報行數"
        assert by_name["last_state.json"]["size_bytes"] > 0
        assert by_name["cards"]["kind"] == "dir" and by_name["cards"]["count"] == 1
        assert by_name["theses.jsonl"]["exists"] is False, "沒建的檔要如實回報不存在"
        # 不把交易內容本身印到終端——status 只給結構化的存在/大小/筆數,不讀檔內容
        assert "card body" not in r.stdout, "status 輸出不該包含卡片的實際文字內容"


# ─────────────── B. data-export ───────────────

def test_export_empty_root_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        r = _run("data-export", "--root", tmp, "--out", os.path.join(tmp, "b.zip"))
        assert r.returncode != 0, "空 root 匯出應該拒收,不能生一個空 zip 假裝有備份"


def test_export_zip_matches_present_files():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp)
        out_zip = os.path.join(tmp, "..", "backup.zip")
        out_zip = os.path.abspath(out_zip)
        r = _run("data-export", "--root", tmp, "--out", out_zip)
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert set(payload["included"]) == {"last_state.json", "log.jsonl", "profile.md", "cards"}
        assert "敏感" in r.stderr, "匯出要在 stderr 明確警示含敏感資料"
        with zipfile.ZipFile(out_zip) as zf:
            names = set(zf.namelist())
        assert "last_state.json" in names and "cards/2026-07-13.md" in names
        os.remove(out_zip)


# ─────────────── C. data-reset ───────────────

def test_reset_no_flag_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp)
        r = _run("data-reset", "--root", tmp)
        assert r.returncode != 0, "裸執行(不帶 --dry-run/--confirm)不能有預設刪除行為"
        assert os.path.exists(os.path.join(tmp, "log.jsonl")), "拒收時不能動到任何檔案"


def test_reset_dry_run_and_confirm_mutually_exclusive():
    with tempfile.TemporaryDirectory() as tmp:
        r = _run("data-reset", "--root", tmp, "--dry-run", "--confirm")
        assert r.returncode != 0, "--dry-run 與 --confirm 必須互斥"


def test_reset_dry_run_does_not_delete():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp)
        r = _run("data-reset", "--root", tmp, "--dry-run")
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert len(out["would_delete"]) == 4
        assert os.path.exists(os.path.join(tmp, "log.jsonl")), "dry-run 後檔案必須原封不動"
        assert os.path.exists(os.path.join(tmp, "cards", "2026-07-13.md"))


def test_reset_confirm_deletes_everything_known():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp)
        r = _run("data-reset", "--root", tmp, "--confirm")
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert len(out["deleted"]) == 4
        for name in ("last_state.json", "log.jsonl", "profile.md", "cards"):
            assert not os.path.exists(os.path.join(tmp, name)), f"{name} 應已刪除"


def test_reset_confirm_on_empty_root_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        r = _run("data-reset", "--root", tmp, "--confirm")
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["deleted"] == []


def test_reset_and_status_cover_ux_trace_dir():
    """#239: the cross-client presentation trace lives under <root>/ux/ and must be a
    tracked footprint -- visible in data-status and cleared by data-reset --confirm --
    so the "placement keeps it safe" guarantee includes user-controlled deletion."""
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "ux"), exist_ok=True)
        with open(os.path.join(tmp, "ux", "session-1.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"event":"capabilities_declared","session_id":"session-1"}\n')
        status = json.loads(_run("data-status", "--root", tmp).stdout)
        by_name = {e["name"]: e for e in status["files"]}
        assert by_name["ux"]["exists"] and by_name["ux"]["count"] == 1, "ux/ must be a tracked footprint"
        r = _run("data-reset", "--root", tmp, "--confirm")
        assert r.returncode == 0, r.stderr
        assert not os.path.exists(os.path.join(tmp, "ux")), "reset --confirm must clear the ux/ trace"


def test_headline_motive_projection_is_status_export_and_reset_managed():
    """#294: the durable motive projection is private user data, so every
    data-control operation must discover it from coach.DATA_FILES."""
    with tempfile.TemporaryDirectory() as tmp:
        motive_path = os.path.join(tmp, "headline_motives.jsonl")
        with open(motive_path, "w", encoding="utf-8") as f:
            f.write('{"event":"headline_motive_decision","decision":"deliberate_plan"}\n')

        status = json.loads(_run("data-status", "--root", tmp).stdout)
        by_name = {entry["name"]: entry for entry in status["files"]}
        assert status["present_count"] == 1
        assert by_name["headline_motives.jsonl"]["exists"]
        assert by_name["headline_motives.jsonl"]["lines"] == 1

        out_zip = os.path.join(tmp, "backup.zip")
        exported = _run("data-export", "--root", tmp, "--out", out_zip)
        assert exported.returncode == 0, exported.stderr
        payload = json.loads(exported.stdout)
        assert payload["included"] == ["headline_motives.jsonl"]
        with zipfile.ZipFile(out_zip) as zf:
            assert zf.namelist() == ["headline_motives.jsonl"]

        reset = _run("data-reset", "--root", tmp, "--confirm")
        assert reset.returncode == 0, reset.stderr
        assert json.loads(reset.stdout)["deleted"] == [motive_path]
        assert not os.path.exists(motive_path)


def test_behavior_verdicts_projection_is_status_export_and_reset_managed():
    """#446 cut 1: verdicts.jsonl is private user data (a said-vs-done
    judgment about the user's own trading), so every data-control operation
    must discover it from coach.DATA_FILES -- the exact gap #452 named for
    condition_checks.jsonl, not repeated here."""
    with tempfile.TemporaryDirectory() as tmp:
        verdicts_path = os.path.join(tmp, "verdicts.jsonl")
        with open(verdicts_path, "w", encoding="utf-8") as f:
            f.write('{"rule":{"id":"horizon_contradiction","version":1},"outcome":"held_too_long"}\n')

        status = json.loads(_run("data-status", "--root", tmp).stdout)
        by_name = {entry["name"]: entry for entry in status["files"]}
        assert status["present_count"] == 1
        assert by_name["verdicts.jsonl"]["exists"]
        assert by_name["verdicts.jsonl"]["lines"] == 1

        out_zip = os.path.join(tmp, "backup.zip")
        exported = _run("data-export", "--root", tmp, "--out", out_zip)
        assert exported.returncode == 0, exported.stderr
        payload = json.loads(exported.stdout)
        assert payload["included"] == ["verdicts.jsonl"]
        with zipfile.ZipFile(out_zip) as zf:
            assert zf.namelist() == ["verdicts.jsonl"]

        reset = _run("data-reset", "--root", tmp, "--confirm")
        assert reset.returncode == 0, reset.stderr
        assert json.loads(reset.stdout)["deleted"] == [verdicts_path]
        assert not os.path.exists(verdicts_path)


# ─────────────── D. --root 覆寫隔離(不誤觸真正的 ~/.trade-coach/)───────────────

def test_root_override_reported_exactly_as_passed():
    """--root 給什麼路徑,回報的 root 就必須是那個路徑,一字不差——
    這是 #165「demo/test-drive 零落盤正式狀態」防線的機械基礎:試駕模式靠 SKILL 把
    --root/--state/--log 等覆寫指到臨時目錄,前提是這些覆寫真的生效、不會被靜默忽略
    退回預設 ~/.trade-coach/。跟其餘測試都只在覆寫路徑內操作合起來看,證明了這件事。"""
    with tempfile.TemporaryDirectory() as fake_root:
        _seed(fake_root)
        out = json.loads(_run("data-status", "--root", fake_root).stdout)
        assert out["root"] == fake_root
        assert out["present_count"] == 4


def test_no_root_flag_defaults_to_trade_coach_home():
    """不帶 --root 時,預設路徑必須是 ~/.trade-coach(不是別的、也不是空字串)。
    用假 HOME 環境變數驗證,不讀真正的 ~/.trade-coach——同 repo 既有慣例
    「不落盤 ≠ 不讀盤」:測試絕不能碰真實使用者的本機資料,唯讀也不行。"""
    with tempfile.TemporaryDirectory() as fake_home:
        env = dict(os.environ, HOME=fake_home)
        r = subprocess.run([sys.executable, COACH, "data-status"],
                           capture_output=True, text=True, env=env)
        out = json.loads(r.stdout)
        assert out["root"] == os.path.join(fake_home, ".trade-coach")


def test_condition_checks_projection_is_status_export_and_reset_managed():
    """#452: condition_checks.jsonl is private user data (the per-period record
    of whether each of the user's own falsifier conditions was checked, what
    was observed, and how they answered -- conditions.py, #412's second half),
    so every data-control operation must discover it from coach.DATA_FILES --
    the exact gap this issue was filed for (mirrors the verdicts.jsonl and
    headline_motives.jsonl tests above, #446/#294)."""
    with tempfile.TemporaryDirectory() as tmp:
        checks_path = os.path.join(tmp, "condition_checks.jsonl")
        with open(checks_path, "w", encoding="utf-8") as f:
            f.write('{"slot_id":"slot-0","check_id":"chk-0","lookup_status":"ok"}\n')

        status = json.loads(_run("data-status", "--root", tmp).stdout)
        by_name = {entry["name"]: entry for entry in status["files"]}
        assert status["present_count"] == 1
        assert by_name["condition_checks.jsonl"]["exists"]
        assert by_name["condition_checks.jsonl"]["lines"] == 1

        out_zip = os.path.join(tmp, "backup.zip")
        exported = _run("data-export", "--root", tmp, "--out", out_zip)
        assert exported.returncode == 0, exported.stderr
        payload = json.loads(exported.stdout)
        assert payload["included"] == ["condition_checks.jsonl"]
        with zipfile.ZipFile(out_zip) as zf:
            assert zf.namelist() == ["condition_checks.jsonl"]

        reset = _run("data-reset", "--root", tmp, "--confirm")
        assert reset.returncode == 0, reset.stderr
        assert json.loads(reset.stdout)["deleted"] == [checks_path]
        assert not os.path.exists(checks_path)


def test_position_rationales_are_status_export_and_reset_managed():
    """#403: position_rationales.jsonl holds the user's own words for why they
    still hold a position -- the most personal thing this product stores, and
    the one a user is most likely to want a copy of or gone. The registry is how
    every data-control operation finds it (mirrors the condition_checks.jsonl
    and verdicts.jsonl tests above, #412/#446)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "position_rationales.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"event_id":"position-rationale-0","act":"statement",'
                    '"subject":{"cycle_id":"ACME#2026-06-30#1"}}\n')

        status = json.loads(_run("data-status", "--root", tmp).stdout)
        by_name = {entry["name"]: entry for entry in status["files"]}
        assert status["present_count"] == 1
        assert by_name["position_rationales.jsonl"]["exists"]
        assert by_name["position_rationales.jsonl"]["lines"] == 1

        out_zip = os.path.join(tmp, "backup.zip")
        exported = _run("data-export", "--root", tmp, "--out", out_zip)
        assert exported.returncode == 0, exported.stderr
        assert json.loads(exported.stdout)["included"] == ["position_rationales.jsonl"]
        with zipfile.ZipFile(out_zip) as zf:
            assert zf.namelist() == ["position_rationales.jsonl"]

        reset = _run("data-reset", "--root", tmp, "--confirm")
        assert reset.returncode == 0, reset.stderr
        assert json.loads(reset.stdout)["deleted"] == [path]
        assert not os.path.exists(path)


# ─── E. registry completeness: the next omission fails the suite (#452) ────
#
# DATA_FILES stays hand-authored -- each entry's description is user-facing
# prose deciding what someone exports or deletes, and no script should write
# that sentence. What must stop being hand-maintained is knowing whether the
# list is *complete*. The check below generates its expectation from the
# engine's own source (docs/development-guide.md section 1's "prefer
# generating a synchronized surface over hand-mirroring it",
# tools/design_bundle.py precedent) instead of hand-listing "files I expect to
# exist" a second time, which is the same mirror the missing entry itself was.
#
# It parses every non-test module directly under skills/fomo-kernel/engine/
# for the one idiom every coach-root writer in this codebase already uses:
# `os.path.join(<coach-root-like>, "<literal>", ...)`. "Coach-root-like" is a
# bare `root` reference, or a call whose (possibly dotted) name contains
# "root" -- session.default_root(), coach._coach_root(args), and
# fetch_cache.py's own _root(root) indirection all match that convention
# already; nothing here needed to change for them to be seen. review.py's
# _engine_version() was the one place in engine/ binding a *different* thing
# (the skill's own checkout, to read its VERSION file/git SHA -- never the
# user's coach data root) to a variable literally named `root`; it is renamed
# to repo_root in this same change specifically so this scan needs no
# hand-written exclusion list -- fixing the ambiguity at its source rather
# than teaching the checker to special-case it.
#
# Scope, named rather than silently assumed: this sees skills/fomo-kernel/
# engine/*.py only. Two already-registered entries are written from outside
# that scope and this check cannot see either: profile.md (written directly
# by the agent per SKILL.md, never by engine code) and ux/ (written by
# skills/fomo-kernel/tools/ux_receipt.py's pathlib `root / "ux" / ...`, a
# different shape than os.path.join). Both stay correct because a dedicated
# test exercises each -- profile.md via _seed()'s tests above,
# test_reset_and_status_cover_ux_trace_dir for ux/ -- not because this check
# reaches them. What this check adds is the direction #452 actually broke on:
# a literal constructed inside engine/ with no test written for it at all.

def _dotted_call_name(node):
    """`os.path.join` -> "os.path.join"; anything not a plain dotted chain
    (a subscript, a comprehension, ...) -> None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_call_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _module_string_constants(tree):
    """`NAME = "literal"` assignments anywhere in the module -- resolves
    fetch_cache.py's `_DIR = "cache"` indirection so
    `os.path.join(_root(root), _DIR, ...)` still yields "cache" instead of
    being silently skipped for not being a literal in the call itself."""
    consts = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            consts[node.targets[0].id] = node.value.value
    return consts


def _looks_like_coach_root(node):
    """A bare `root` name, or a call whose (possibly dotted) name contains
    "root" -- see the section comment above for which real call sites that
    covers and why review.py has no remaining exception."""
    if isinstance(node, ast.Name) and node.id == "root":
        return True
    if isinstance(node, ast.Call):
        return "root" in (_dotted_call_name(node.func) or "")
    return False


def _coach_root_literals(source_path):
    """Every literal top-level name this module builds via
    `os.path.join(<coach-root-like>, "<literal>", ...)`, anywhere in the file
    -- assignment, return, dict value, or kwarg; ast.walk does not care which."""
    with open(source_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=source_path)
    consts = _module_string_constants(tree)
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _dotted_call_name(node.func) == "os.path.join"):
            continue
        if len(node.args) < 2 or not _looks_like_coach_root(node.args[0]):
            continue
        literal = node.args[1]
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
            found.add(literal.value)
        elif isinstance(literal, ast.Name) and literal.id in consts:
            found.add(consts[literal.id])
    return found


def test_data_files_registry_covers_every_engine_written_path():
    """#452: nothing may construct a coach-root top-level path in
    skills/fomo-kernel/engine/ that coach.DATA_FILES does not know about --
    the mechanical version of the sweep this issue asked for, so the next
    stream cannot repeat condition_checks.jsonl's silence. See the section
    comment above for exactly what this does and does not see."""
    engine_files = sorted(f for f in os.listdir(ENGINE)
                          if f.endswith(".py") and not f.startswith("test_"))
    assert engine_files, "engine directory scan found nothing -- ENGINE path is wrong"
    constructed = set()
    for fname in engine_files:
        constructed |= _coach_root_literals(os.path.join(ENGINE, fname))
    registered = {name for name, _kind, _desc in coach.DATA_FILES}
    missing = sorted(constructed - registered)
    assert not missing, (
        f"{missing} are built as coach-root paths somewhere in "
        f"skills/fomo-kernel/engine/ but are not in coach.DATA_FILES -- "
        f"data-status/export/reset cannot see them (#452)."
    )


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
