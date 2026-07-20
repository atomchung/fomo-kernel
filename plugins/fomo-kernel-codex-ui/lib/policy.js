export const SUPPORTED_LOCALES = Object.freeze(["zh-TW", "en"]);

export function isSupportedLocale(locale) {
  return SUPPORTED_LOCALES.includes(locale);
}

export function hostLocale(documentLanguage) {
  return String(documentLanguage || "").toLowerCase().startsWith("zh")
    ? "zh-TW"
    : "en";
}

export function demoSurface(locale, kind) {
  if (!isSupportedLocale(locale)) {
    throw new Error(`Unsupported locale: ${locale}`);
  }
  if (kind !== "card" && kind !== "question") {
    throw new Error(`Unsupported demo surface: ${kind}`);
  }
  return { demo: true, locale, kind };
}

export function selectedDemoChoice({ locale, questionId, choice }) {
  if (!isSupportedLocale(locale)) throw new Error(`Unsupported locale: ${locale}`);
  if (questionId !== "codex_ui_probe_choice") {
    throw new Error("Unknown demo question.");
  }
  if (!DEMO_CHOICES.some((candidate) => candidate.value === choice)) {
    throw new Error("Unknown demo choice.");
  }
  return { demo: true, locale, question_id: questionId, choice };
}

export const DEMO_CHOICES = Object.freeze([
  { value: "rule_a", zhTW: "A. 先寫下規則，再行動", en: "A. Write the rule before acting" },
  { value: "rule_b", zhTW: "B. 暫停，明天再決定", en: "B. Pause and decide tomorrow" }
]);

export function localizedCopy(locale) {
  const zh = locale === "zh-TW";
  return {
    badge: zh ? "合成測試資料" : "Synthetic test data",
    cardTitle: zh ? "Review Card · 介面測試" : "Review Card · UI probe",
    cardSubtitle: zh
      ? "此卡片只用來驗證 Codex 的視覺呈現；不含真實交易或 review session。"
      : "This card only verifies visual delivery in Codex; it contains no trade or review-session data.",
    cardLabel: zh ? "卡片可見性" : "Card visibility",
    cardValue: zh ? "待 owner 確認" : "Awaiting owner confirmation",
    questionTitle: zh ? "你看得到兩個可點選的選項嗎？" : "Can you see two clickable options?",
    questionDetail: zh
      ? "點選任一選項，widget 應回傳固定的 canonical value。"
      : "Select either option; the widget should return a fixed canonical value.",
    selected: zh ? "已送出測試選擇：" : "Test selection submitted: ",
    unavailable: zh ? "此 host 沒有可用的 Apps bridge。" : "This host has no available Apps bridge.",
    submitError: zh ? "送出失敗，請改用文字 fallback。" : "Submission failed; use the text fallback."
  };
}
