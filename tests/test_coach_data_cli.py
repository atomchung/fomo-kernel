#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coach.py data-status / data-export / data-reset(#165)測試 — 全離線、確定性、免裝 pytest。

蓋什麼:
  A. data-status:空 root / 部分存在 / dir 型(cards)計數,不印交易內容本身。
  B. data-export:zip 內容與 present 清單一致,空 root 拒收。
  C. data-reset:--dry-run 不動檔案、--confirm 才真的刪、兩者互斥、裸執行(無旗標)拒收。
  D. --root 覆寫生效,絕不誤觸真正的 ~/.trade-coach/(隔離驗證的機械版本)。
"""
import json
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "skills", "fomo-kernel", "engine")
COACH = os.path.join(ENGINE, "coach.py")


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
