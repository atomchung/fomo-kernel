#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""敘事 judge harness 的離線驗活(judge_narrative.py + run_judge_eval.py)。

那兩支是 opt-in、要付費、非確定性的,所以本體永遠不會進 tests/run_all.py。但它們
身上有幾道閘門是**純邏輯**——manifest 夠不夠格、拒答怎麼處理、送出去的 request 長
什麼樣——這些不該只有拿得到 API key 的人才驗得到。evals/judge_episodes.py 的
grade_answer() 已經立過這個先例(見該檔 docstring:「An interlock only reachable by
someone holding an API key is an interlock nobody re-verifies」),這支把同一條規矩
補到 tests/agent 這半邊。

驗三件事,每件都附「對應突變會紅」的證明:

1. **_blocked():這批 fixture 有沒有能力證明 judge 是活的。**
   兩種假綠燈都會被 `n_ok == n_total` 判成通過:一條都沒跑(0/0),或只剩單邊期望值
   (全是 expect=pass 時,一個「什麼都說 pass」的壞 judge 也拿滿分)。順便把 committed
   manifest 自己釘住:有人把壞卡 fixture 刪光,這裡就紅。
2. **judge() 的拒答處理。** 拒答回的是 HTTP 200 + 空/半截 content,所以分支必須在讀
   content 之前。這裡用「一被迭代就炸」的假 content 證明程式碼真的沒碰它,而不是靠
   讀順序看起來對。
3. **送出去的 request 與 tool schema。** 模型 ID、effort 位置、max_tokens 額度、
   strict schema 的形狀,以及「不准出現已移除參數」。

anthropic 套件不需要安裝:judge() 是延遲 import,這裡塞一個 stub 進 sys.modules。

跑法:python3 tests/test_judge_harness_offline.py
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "tests" / "agent"
FIXTURES = AGENT_DIR / "fixtures"

# 在 import 之前就把 state root 導到暫存目錄:這支測試會呼叫 _main(),而 _main()
# 會 append 紀錄。測試絕不准碰到真實的 ~/.trade-coach。
_TMP_HOME = tempfile.mkdtemp(prefix="judge-harness-test-")
os.environ["TRADE_COACH_HOME"] = _TMP_HOME

sys.path.insert(0, str(AGENT_DIR))
import judge_narrative as J          # noqa: E402  (延遲 import anthropic,離線可載)
import run_judge_eval as R           # noqa: E402

_fails = []


def ok(cond, label):
    if not cond:
        _fails.append(label)
    print(f"{'✅' if cond else '❌'} {label}")


# ── anthropic stub:只回我們寫死的回應,不連網 ──────────────────────────────

class _APIError(Exception):
    pass


class _Block:
    def __init__(self, type_, input_=None):
        self.type = type_
        self.input = input_


class _Resp:
    def __init__(self, stop_reason, content, stop_details=None):
        self.stop_reason = stop_reason
        self.content = content
        self.stop_details = stop_details


class _ExplodingContent(list):
    """一被迭代就炸——證明 refusal 分支真的沒有讀 content。"""

    def __iter__(self):
        raise AssertionError("stop_reason=='refusal' 時仍讀取了 resp.content")


_sent = []


def _install_stub(response):
    """把 stub 裝進 sys.modules;judge() 內的 import anthropic 會拿到它。"""
    class _Messages:
        def create(self, **kwargs):
            _sent.append(kwargs)
            return response

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    stub = types.ModuleType("anthropic")
    stub.Anthropic = _Client
    stub.APIError = _APIError
    sys.modules["anthropic"] = stub


AXES = ("coherent_story", "strength_first", "concrete_evidence",
        "plain_language", "quote_not_lecture")
GOOD_INPUT = {axis: {"score": 5, "reason": "r"} for axis in AXES} | {"overall": 5}


# ── 1. _blocked():manifest 夠不夠格 ─────────────────────────────────────────

def test_manifest_gate_alive():
    committed = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    cases = committed.get("cases") or []
    ok(R._blocked(cases) is None,
       "committed manifest 通得過閘(好卡壞卡兩邊都在)")

    # 以下每條都是「修好之前會靜默拿綠燈」的突變
    ok(R._blocked([]) is not None,
       "_blocked 擋空清單(0/0 是沒評到,不是都對)")
    ok(R._blocked([c for c in cases if c["expect"] == "pass"]) is not None,
       "_blocked 擋只剩 expect=pass(壞 judge 也會滿分)")
    ok(R._blocked([c for c in cases if c["expect"] == "fail"]) is not None,
       "_blocked 擋只剩 expect=fail")
    ok("fail" in (R._blocked([c for c in cases if c["expect"] == "pass"]) or ""),
       "_blocked 的訊息講得出缺哪一邊")


def test_manifest_gate_runs_before_spending():
    """閘門要在任何 API 呼叫之前——沒 key 也擋得住,才擋得到錢。"""
    def _explode(_text):
        raise AssertionError("manifest 不合格卻仍呼叫了 judge()")

    original_judge = R.judge
    R.judge = _explode
    try:
        sys.modules.pop("anthropic", None)   # 沒裝 SDK 也要走得到 return 1
        rc, _ = _run_main_with_cases([])
        ok(rc == 1, "空 manifest:exit 1 且一次 judge 都沒呼叫")
        rc, _ = _run_main_with_cases(
            [{"file": "card_good.txt", "expect": "pass", "label": "x"}])
        ok(rc == 1, "單邊 manifest:exit 1 且一次 judge 都沒呼叫")
    finally:
        R.judge = original_judge


def _run_main_with_cases(cases):
    """跑 _main(),但讓它讀到我們給的 manifest——絕不動 committed 那份。

    做法是把 R.FIXTURES 指到暫存目錄,不是 monkeypatch json.loads:紀錄行本身也
    帶 "cases" 鍵,攔 json.loads 會連帶弄壞 _read_history()。
    """
    import shutil
    original = R.FIXTURES
    with tempfile.TemporaryDirectory() as tmp:
        (pathlib.Path(tmp) / "manifest.json").write_text(
            json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
        for card in FIXTURES.glob("*.txt"):      # 真卡片內容,manifest 才是合成的
            shutil.copy(card, pathlib.Path(tmp) / card.name)
        R.FIXTURES = pathlib.Path(tmp)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = R._main([])
        finally:
            R.FIXTURES = original
    return rc, buf.getvalue()


# ── 2. judge() 的拒答與錯誤路徑 ─────────────────────────────────────────────

def test_refusal_is_handled_before_content():
    _install_stub(_Resp("refusal", _ExplodingContent(),
                        types.SimpleNamespace(category="cyber")))
    try:
        J.judge("a card")
        ok(False, "拒答必須拋錯,不能回傳")
    except AssertionError as exc:                      # _ExplodingContent 炸了
        ok(False, f"拒答分支順序錯:{exc}")
    except RuntimeError as exc:
        ok(True, "拒答在讀 content 之前就拋 RuntimeError")
        ok("cyber" in str(exc), "拒答訊息帶得出 stop_details.category")

    # stop_details 可能是 None——不能因此變成 AttributeError
    _install_stub(_Resp("refusal", _ExplodingContent(), None))
    try:
        J.judge("a card")
        ok(False, "stop_details=None 的拒答仍要拋錯")
    except RuntimeError:
        ok(True, "stop_details=None 的拒答不會炸成 AttributeError")


def test_missing_tool_use_is_diagnosed():
    _install_stub(_Resp("max_tokens", [_Block("text")]))
    try:
        J.judge("a card")
        ok(False, "沒有 tool_use 區塊要拋錯")
    except RuntimeError as exc:
        ok("max_tokens" in str(exc),
           "缺 tool_use 時錯誤訊息帶得出 stop_reason(截斷才看得出是額度問題)")


def test_ungraded_is_not_a_pass():
    """judge() 拋錯 = 這張 fixture 沒評到;沒評到不能算通過。"""
    original = R.judge
    R.judge = lambda _text: (_ for _ in ()).throw(RuntimeError("judge 模型拒答了這張卡"))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            record = R._run_case({"file": "card_good.txt", "expect": "pass", "label": "x"})
        ok(record["ok"] is False, "judge 拋錯時該格 ok=False(沒評到≠通過)")
        ok(record["got"] == "ungraded",
           "沒評到會如實記成 ungraded,不會被寫成一個假的 verdict")
    finally:
        R.judge = original


# ── 4. 記錄面:每次跑留一行,而且讀得回來 ────────────────────────────────────

def test_history_root_mirrors_the_engine():
    """_state_root() 是 engine/session.default_root() 的鏡像,不准漂移。

    漂移的後果不是崩潰,是 /fomo-qa 的隔離 root 對這支失效——紀錄默默寫進真實的
    ~/.trade-coach。所以這條用機制擋,不靠人記得。
    """
    sys.path.insert(0, str(ROOT / "skills" / "fomo-kernel" / "engine"))
    import session                                        # noqa: E402
    ok(str(R._state_root()) == session.default_root(),
       "_state_root() 與 engine/session.default_root() 解析結果一致")
    ok(str(R._state_root()) == _TMP_HOME,
       "TRADE_COACH_HOME 真的改得動記錄位置(隔離 root 生效)")


def test_every_run_is_recorded_and_readable():
    before = len(R._read_history())
    rc, out = _run_main_with_cases([])                     # 被閘門擋下的一次
    rows = R._read_history()
    ok(rc == 1 and len(rows) == before + 1,
       "被擋下的跑也留一行(歷史空窗才分得出『沒人跑』和『跑了沒評到』)")
    ok(rows[-1].get("blocked") and rows[-1]["total"] == 0,
       "擋下的那行記得出原因、且 total=0")
    ok(rows[-1].get("model") == J.MODEL,
       "紀錄帶得出模型(換模型後舊紀錄不能跟新的混著讀)")

    # 一次「評得出來」的跑:好卡 5 分、壞卡 1 分
    good = (FIXTURES / "card_good.txt").read_text(encoding="utf-8")
    original = R.judge
    R.judge = lambda text: {"overall": 5 if text == good else 1}
    try:
        rc, out = _run_main_with_cases([
            {"file": "card_good.txt", "expect": "pass", "label": "x"},
            {"file": "card_bad_vague.txt", "expect": "fail", "label": "y"},
        ])
    finally:
        R.judge = original
    row = R._read_history()[-1]
    ok(rc == 0 and row["verified"] == 2 and row["total"] == 2, "正常跑記下 2/2")
    ok([c["scores"] for c in row["cases"]] == [[5, 5], [1, 1]],
       "每張 fixture 的實際分數都留著(漂移要看分數,不是只看 pass/fail)")
    ok("已記錄到" in out, "跑完會告訴人紀錄寫到哪")


def test_history_reader_shows_drift():
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        R._show_history(20)
    text = buf.getvalue()
    ok("card_good" in text.replace("good", "card_good") or "good=" in text,
       "--history 印得出每張 fixture 的判決")

    # 壞卡從 fail 漂到 pass:下一次讀歷史必須標出來
    R._record({"run_at": "2026-01-01T00:00:00Z", "model": "m", "effort": "high",
               "n_runs": 2, "verified": 1, "total": 1,
               "cases": [{"file": "card_bad_vague.txt", "expect": "fail",
                          "got": "pass", "scores": [4, 4], "ok": False}]})
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        R._show_history(20)
    ok("⚠" in buf.getvalue(), "judge 對同一張卡改了判決 → --history 標 ⚠")


def test_recording_failure_never_kills_the_run():
    """寫不進去只警告並回 False，不能 crash 或偽裝成已持久化。"""
    original = R._history_path
    R._history_path = lambda: pathlib.Path("/proc/nonexistent-dir/x.jsonl")
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            recorded = R._record({"run_at": "x"})
        ok(recorded is False, "記錄寫失敗時 _record 回 False")
        ok("沒記錄成功" in buf.getvalue(), "記錄寫失敗時警告而不是拋例外")
    except Exception as exc:                               # noqa: BLE001
        ok(False, f"記錄失敗不該拋例外,卻拋了 {exc!r}")
    finally:
        R._history_path = original


def test_green_run_without_durable_history_is_not_evidence():
    """#466: a green judge result is not evidence until its audit row exists."""
    good = (FIXTURES / "card_good.txt").read_text(encoding="utf-8")
    original_judge, original_path, original_fsync = R.judge, R._history_path, R._fsync_dir
    R.judge = lambda text: {"overall": 5 if text == good else 1}
    with tempfile.TemporaryDirectory() as tmp:
        R._history_path = lambda: pathlib.Path(tmp) / "new-root" / "judge" / "narrative-runs.jsonl"
        R._fsync_dir = lambda _path: (_ for _ in ()).throw(OSError("directory fsync failed"))
        try:
            rc, out = _run_main_with_cases([
                {"file": "card_good.txt", "expect": "pass", "label": "x"},
                {"file": "card_bad_vague.txt", "expect": "fail", "label": "y"},
            ])
        finally:
            R.judge, R._history_path, R._fsync_dir = original_judge, original_path, original_fsync
    ok(rc == 1, "otherwise-green judge run loses exit 0 when history persistence fails")
    ok("沒記錄成功" in out and "不是可採信的 evidence" in out,
       "persistence failure is warned and explicitly marked non-evidence")
    ok("已記錄到" not in out, "failed persistence never claims durable success")


def test_directory_entries_are_synced_and_failure_is_not_success():
    original_path, original_fsync = R._history_path, R._fsync_dir
    with tempfile.TemporaryDirectory() as tmp:
        history = pathlib.Path(tmp) / "state" / "judge" / "narrative-runs.jsonl"
        calls = []
        R._history_path = lambda: history
        R._fsync_dir = lambda path: calls.append(pathlib.Path(path))
        try:
            ok(R._record({"run_at": "dir-sync"}) is True,
               "directory-synced record reports durable success")
            ok(history.parent.parent in calls and history.parent in calls,
               "new directory entries and final history parent are fsynced")
        finally:
            R._history_path, R._fsync_dir = original_path, original_fsync


def test_record_success_is_durable_and_readable():
    before = len(R._read_history())
    row = {"run_at": "2099-01-01T00:00:00Z", "model": "test", "effort": "low",
           "verified": 1, "total": 1, "cases": []}
    ok(R._record(row) is True, "successful append reports durable success")
    rows = R._read_history()
    ok(len(rows) == before + 1 and rows[-1] == row,
       "successful append remains readable from the history surface")


def test_history_on_empty_store_is_friendly():
    original = R._history_path
    with tempfile.TemporaryDirectory() as tmp:
        R._history_path = lambda: pathlib.Path(tmp) / "nope.jsonl"
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                rc = R._show_history(20)
            ok(rc == 0 and "還沒有任何紀錄" in buf.getvalue(),
               "沒有歷史時 --history 給指引而不是 traceback")
        finally:
            R._history_path = original


# ── 3. request 與 tool schema 的形狀 ────────────────────────────────────────

def test_request_shape():
    _sent.clear()
    _install_stub(_Resp("tool_use", [_Block("text"), _Block("tool_use", GOOD_INPUT)]))
    ok(J.judge("a card") == GOOD_INPUT, "正常路徑回傳 tool_use.input")
    sent = _sent[-1]

    ok(sent["model"] == J.MODEL, "request 的 model 來自 MODEL")
    ok("-2025" not in J.MODEL and "-2024" not in J.MODEL,
       f"模型 ID 沒有日期後綴(現在是 {J.MODEL})")
    ok(sent.get("output_config") == {"effort": J.EFFORT},
       "effort 放在 output_config 內,不是頂層")
    ok(sent["tool_choice"] == {"type": "tool", "name": "score_narrative"},
       "強制 tool_choice 還在")
    removed = [p for p in ("temperature", "top_p", "top_k", "thinking") if p in sent]
    ok(not removed, f"request 不含已移除/會被拒的參數(發現:{removed})")
    # max_tokens 是「思考 + 回覆」的總上限,這代模型預設會思考
    ok(sent["max_tokens"] >= 16000,
       f"max_tokens 留得下思考 + tool call 的額度(現在 {sent['max_tokens']})")


def test_tool_schema_is_strict_shaped():
    schema = json.loads(json.dumps(J.SCORE_TOOL))     # 必須能序列化成 JSON
    ok(schema.get("strict") is True, "strict 放在 tool 上,不是 tool_choice 上")
    top = schema["input_schema"]
    ok(top.get("additionalProperties") is False, "頂層 additionalProperties=false")
    ok(set(top["required"]) == set(top["properties"]),
       "required 與 properties 完全對齊(少一軸不會靜默通過)")
    ok(set(top["required"]) == set(AXES) | {"overall"}, "五軸 + overall 都在")

    banned = {"minimum", "maximum", "multipleOf"}
    found = banned & set(_keys(schema))
    ok(not found, f"沒有 strict 不支援的數值區間約束(發現:{sorted(found)})")

    for axis in AXES:
        node = top["properties"][axis]
        ok(node.get("additionalProperties") is False
           and sorted(node["required"]) == ["reason", "score"]
           and node["properties"]["score"]["enum"] == [0, 1, 2, 3, 4, 5],
           f"{axis} 是 strict 形狀且分數是 0–5 enum")
    ok(top["properties"]["overall"]["enum"] == [0, 1, 2, 3, 4, 5],
       "overall 是 0–5 enum")


def _keys(node):
    """遞迴吐出 schema 裡所有的 key 名。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def main():
    import shutil
    try:
        test_manifest_gate_alive()
        test_manifest_gate_runs_before_spending()
        test_refusal_is_handled_before_content()
        test_missing_tool_use_is_diagnosed()
        test_ungraded_is_not_a_pass()
        test_request_shape()
        test_tool_schema_is_strict_shaped()
        test_history_root_mirrors_the_engine()
        test_every_run_is_recorded_and_readable()
        test_history_reader_shows_drift()
        test_recording_failure_never_kills_the_run()
        test_green_run_without_durable_history_is_not_evidence()
        test_directory_entries_are_synced_and_failure_is_not_success()
        test_record_success_is_durable_and_readable()
        test_history_on_empty_store_is_friendly()
    finally:
        shutil.rmtree(_TMP_HOME, ignore_errors=True)
    print()
    if _fails:
        print(f"❌ {len(_fails)} 條驗活失敗 —— judge harness 的閘門有問題,先修再拿去判真卡。")
        return 1
    print("✅ judge harness 離線閘門全部驗活通過(不需要 API key,不花錢)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
