import assert from "node:assert/strict";
import test from "node:test";

import { cleanUserFacingText } from "./displayText.ts";

test("user-facing text removes explicit reasoning containers", () => {
  assert.equal(cleanUserFacingText("<think>比较路线。</think>建议晚饭后前往。"), "建议晚饭后前往。");
  assert.equal(cleanUserFacingText("分析过程：先排序。\n最终安排：下午游览。"), "下午游览。");
  assert.equal(cleanUserFacingText("```reasoning\ninternal notes\n```注意闭馆时间。"), "注意闭馆时间。");
});
