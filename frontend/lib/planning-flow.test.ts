import assert from "node:assert/strict";
import test from "node:test";

import { planningControlsDisabled, resolvePlanningFlow } from "./planning-flow.ts";

test("planning flow returns a readable failure", () => {
  const outcome = resolvePlanningFlow(
    { ok: false, data: null, error: { code: "failed", message: "路线生成失败", step: "plan" }, step_status: {} },
    "请稍后重试"
  );

  assert.deepEqual(outcome, { kind: "failed", message: "路线生成失败", blockers: undefined });
});

test("planning flow preserves needs-user-choice state", () => {
  const intervention = {
    id: "i1",
    status: "needs_user_choice" as const,
    question: "优先保留哪项？",
    options: [{ id: "keep_time", label: "保留时间", description: "减少其他地点" }],
    issues: [],
  };
  const outcome = resolvePlanningFlow(
    { ok: true, data: { status: "needs_user_choice", planning_intervention: intervention }, error: null, step_status: {} },
    "失败"
  );

  assert.equal(outcome.kind, "needs_user_choice");
});

test("planning flow accepts completed result", () => {
  const outcome = resolvePlanningFlow(
    {
      ok: true,
      data: {
        status: "completed",
        runtime_pois: [],
        route_matrix: [],
        itinerary: { destination: "成都", days: [], global_risks: [], uncertain_pois: [], revision_notes: [] },
        verification: { passed: true, issues: [] },
      },
      error: null,
      step_status: {},
    },
    "失败"
  );

  assert.equal(outcome.kind, "completed");
});

test("ordinary notice text does not disable planning controls", () => {
  const notice = "地点已更新，请重新生成路线";

  assert.equal(planningControlsDisabled(""), false, notice);
  assert.equal(planningControlsDisabled("正在生成路线"), true);
});
