const INTERNAL_TEXT_PATTERN =
  /\b(?:must_include|must_visit|user_override|final_decision|system_decision|arrange_nearby|needs_confirmation|unresolved|exclude|optional|include)\b\s*[，,、。；;:：]?\s*/gi;
const TECHNICAL_NOTICE_PATTERN =
  /\b(?:estimated_duration_min|route\s*matrix|cache\s*hit|fallback\s*nearby|poi_id|risk_notes|revision_notes|json)\b/i;

export function cleanUserFacingText(value?: string | null) {
  if (!value) return "";
  let text = value
    .replace(/<(?:think|analysis|reasoning)\b[^>]*>[\s\S]*?<\/(?:think|analysis|reasoning)>/gi, "")
    .replace(/```(?:think|analysis|reasoning)\s*[\s\S]*?```/gi, "");
  if (/(?:思考|分析|推理)(?:过程|内容)?\s*[:：]/.test(text)) {
    const parts = text.split(/(?:最终(?:安排|建议|结论)|给用户的(?:安排|建议)|答复)\s*[:：]\s*/);
    text = parts.length > 1
      ? parts[parts.length - 1]
      : text.replace(/^(?:思考|分析|推理)(?:过程|内容)?\s*[:：].*(?:\n|$)/i, "");
  }
  return text
    .replace(/<\/?(?:think|analysis|reasoning)\b[^>]*>/gi, "")
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
