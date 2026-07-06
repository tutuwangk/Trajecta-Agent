const INTERNAL_TEXT_PATTERN =
  /\b(?:must_include|must_visit|user_override|final_decision|system_decision|arrange_nearby|needs_confirmation|unresolved|exclude|optional|include)\b\s*[，,、。；;:：]?\s*/gi;
const TECHNICAL_NOTICE_PATTERN =
  /\b(?:estimated_duration_min|route\s*matrix|cache\s*hit|fallback\s*nearby|poi_id|risk_notes|revision_notes|json)\b/i;

export function cleanUserFacingText(value?: string | null) {
  if (!value) return "";
  return value
    .replace(INTERNAL_TEXT_PATTERN, "")
    .replace(/^[\s，,、。；;:：]+/, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

export function cleanNoticeText(value?: string | null) {
  const text = cleanUserFacingText(value);
  if (!text) return "";
  if (TECHNICAL_NOTICE_PATTERN.test(text)) return "";
  if (!/[\u4e00-\u9fff]/.test(text) && /[A-Za-z]{3,}/.test(text)) return "";
  return text;
}
