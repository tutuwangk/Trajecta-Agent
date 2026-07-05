const INTERNAL_TEXT_PATTERN =
  /\b(?:must_include|must_visit|user_override|final_decision|system_decision|arrange_nearby|needs_confirmation|unresolved|exclude|optional|include)\b\s*[，,、。；;:：]?\s*/gi;

export function cleanUserFacingText(value?: string | null) {
  if (!value) return "";
  return value
    .replace(INTERNAL_TEXT_PATTERN, "")
    .replace(/^[\s，,、。；;:：]+/, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}
