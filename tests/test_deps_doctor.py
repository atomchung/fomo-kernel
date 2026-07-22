#!/usr/bin/env python3
"""#322: the skill ships a runtime dependency manifest, and `review.py doctor`
reports and gates on the optional runtime dependencies — so a freshly installed
user is not silently degraded (prices / P&L / alpha / market context dropped)
with the cause misattributed to their data.

Offline, standard library only. `doctor` merely imports (or fails to import)
each module, so the exit-code paths are driven deterministically with PYTHONPATH
stubs, independent of what is actually installed in the environment.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "fomo-kernel")
REVIEW = os.path.join(SKILL, "engine", "review.py")
MANIFEST = os.path.join(SKILL, "requirements.txt")


def _stub_dir(tmp, name, modules):
    """A dir with one stub module per entry (empty body = importable)."""
    path = os.path.join(tmp, name)
    os.makedirs(path, exist_ok=True)
    for module, body in modules.items():
        with open(os.path.join(path, module + ".py"), "w", encoding="utf-8") as f:
            f.write(body)
    return path


def _doctor(pythonpath):
    env = dict(os.environ, PYTHONPATH=pythonpath)
    return subprocess.run([sys.executable, REVIEW, "doctor"],
                          env=env, capture_output=True, text=True, timeout=60)


def test_skill_dir_ships_runtime_only_manifest():
    assert os.path.exists(MANIFEST), \
        "skills/fomo-kernel/requirements.txt must ship with the installable skill"
    # Parse real dependency lines only (strip inline and whole-line comments) so
    # a package named in a comment is not mistaken for a pinned dependency.
    lines = [ln.split("#", 1)[0].strip()
             for ln in open(MANIFEST, encoding="utf-8").read().splitlines()]
    names = {ln.split(">=")[0].split("==")[0].split("[")[0].strip().lower()
             for ln in lines if ln}
    for dep in ("yfinance", "pandas", "rich"):
        assert dep in names, f"runtime manifest must pin {dep}"
    for dev_only in ("anthropic", "python-dotenv"):
        assert dev_only not in names, \
            f"{dev_only} is test-only and must not be imposed on skill installs"


def test_doctor_passes_when_all_present():
    with tempfile.TemporaryDirectory() as tmp:
        present = _stub_dir(tmp, "ok", {"yfinance": "", "pandas": "", "rich": ""})
        r = _doctor(present)
        assert r.returncode == 0, r.stdout + r.stderr
        for dep in ("yfinance", "pandas", "rich"):
            assert dep in r.stdout
        assert "MISS" not in r.stdout


def test_doctor_exits_nonzero_when_full_experience_dep_missing():
    with tempfile.TemporaryDirectory() as tmp:
        stubs = _stub_dir(tmp, "no_yf", {
            "yfinance": 'raise ImportError("offline stub")\n', "pandas": "", "rich": ""})
        r = _doctor(stubs)
        assert r.returncode == 1, f"missing yfinance must gate non-zero:\n{r.stdout}{r.stderr}"
        assert "MISS" in r.stdout and "yfinance" in r.stdout
        assert "pip install -r skills/fomo-kernel/requirements.txt" in r.stdout


def test_doctor_does_not_gate_on_rich_alone():
    with tempfile.TemporaryDirectory() as tmp:
        # rich import fails, but the full-experience deps are present -> exit 0.
        stubs = _stub_dir(tmp, "no_rich", {
            "yfinance": "", "pandas": "", "rich": 'raise ImportError("offline stub")\n'})
        r = _doctor(stubs)
        assert r.returncode == 0, f"rich is optional for the v2 card:\n{r.stdout}{r.stderr}"


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ✅ {test.__name__}")
    print(f"\n✅ #322 deps doctor / manifest: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
