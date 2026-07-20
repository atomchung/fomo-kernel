import test from "node:test";
import assert from "node:assert/strict";
import { DEMO_CHOICES, demoSurface, hostLocale, localizedCopy, selectedDemoChoice } from "../lib/policy.js";

test("uses an explicit supported locale and never infers it on the server", () => {
  assert.deepEqual(demoSurface("zh-TW", "card"), { demo: true, locale: "zh-TW", kind: "card" });
  assert.throws(() => demoSurface("zh-CN", "card"), /Unsupported locale/);
  assert.equal(hostLocale("zh-Hant-TW"), "zh-TW");
  assert.equal(hostLocale("en-US"), "en");
});

test("demo choice returns only a fixed canonical value", () => {
  assert.deepEqual(selectedDemoChoice({ locale: "zh-TW", questionId: "codex_ui_probe_choice", choice: "rule_a" }), { demo: true, locale: "zh-TW", question_id: "codex_ui_probe_choice", choice: "rule_a" });
  assert.throws(() => selectedDemoChoice({ locale: "zh-TW", questionId: "anything_else", choice: "rule_a" }), /Unknown demo question/);
  assert.deepEqual(DEMO_CHOICES.map((choice) => choice.value), ["rule_a", "rule_b"]);
});

test("Traditional Chinese is a complete localized surface", () => {
  const copy = localizedCopy("zh-TW");
  assert.match(copy.cardTitle, /介面測試/);
  assert.match(copy.questionTitle, /可點選/);
});
