import assert from "node:assert/strict";
import test from "node:test";
import type { AgentV3Event, AgentV3Run } from "./agent-v3-types.ts";
import { currentActivity, eventTime, mergeRunEvents, progressRows, runElapsed } from "./agent-v3-progress.ts";

const event = (event_id: number, type: string, second: number, extra = {}): AgentV3Event => ({ event_id, type, created_at: `2026-10-03 01:00:${String(second).padStart(2, "0")}`, ...extra });
const run = (status: AgentV3Run["status"]): AgentV3Run => ({ run_id: "current", workspace_id: "workspace", goal_revision_id: "goal", status });
const time = (second: number) => Date.UTC(2026, 9, 3, 1, 0, second);

test("incremental and restored event pages deduplicate and retain chronological IDs", () => {
  const first = event(1, "run_started", 0);
  const second = event(2, "requirements_ready", 2);
  assert.deepEqual(mergeRunEvents([second], [first, second]), [first, second]);
  assert.equal(eventTime(first.created_at), time(0));
  assert.equal(eventTime("2026-10-03T09:00:00+08:00"), time(0));
});

test("runtime excludes time waiting for user and preserves elapsed time after resume or reload", () => {
  const events = [event(1, "run_started", 0), event(2, "clarification_requested", 10), event(3, "run_started", 40)];
  assert.equal(runElapsed(events.slice(0, 2), false, time(30)), 10000);
  assert.equal(runElapsed(events, true, time(45)), 15000);
  assert.equal(runElapsed([...events, event(4, "release_published", 50)], false, time(59)), 20000);
});

test("parallel tools remain active independently and retries preserve measured outcomes", () => {
  const events = [event(1, "tool_started", 0, { tool_name: "read_grounding_target", call_id: "a" }), event(2, "tool_started", 1, { tool_name: "read_grounding_target", call_id: "b" }), event(3, "tool_finished", 2, { call_id: "a", duration_ms: 1280, outcome: "retry" })];
  const rows = progressRows(events, true);
  assert.equal(rows[0].state, "retry");
  assert.equal(rows[0].durationMs, 1280);
  assert.equal(rows[1].state, "running");
  assert.equal(currentActivity(run("active"), events), "寻找合适的地点");
  assert.equal(currentActivity(run("waiting_user"), events), "需要你补充一些信息");
});

test("interrupted tools stay interrupted after resuming the same run", () => {
  const events = [event(1, "run_started", 0), event(2, "tool_started", 2, { call_id: "a" }), event(3, "run_interrupted", 5), event(4, "run_started", 10)];
  assert.equal(progressRows(events, true).find((row) => row.kind === "tool")?.state, "interrupted");
  assert.equal(currentActivity(run("active"), events), "正在读取旅行资料");
});

test("unknown events and model notifications never invent completed tool calls", () => {
  const events = [event(1, "unrecognized_event", 0), event(2, "model_request_started", 1)];
  assert.deepEqual(progressRows(events, true), []);
  assert.equal(currentActivity(run("active"), events), "正在推敲下一步安排");
});

test("source understanding stays in progress until requirements are ready", () => {
  const events = [event(1, "run_started", 0), event(2, "requirements_started", 1)];
  assert.equal(progressRows(events, true)[1].state, "running");
  const complete = progressRows([...events, event(3, "requirements_ready", 8)], true)[1];
  assert.equal(complete.state, "done");
  assert.equal(complete.durationMs, 7000);
});


test("travel progress uses known activity labels and keeps diagnostic payloads private", () => {
  const raw = "provider error: route fact failed";
  const rows = progressRows([
    event(1, "tool_started", 0, { tool_name: raw, object_name: raw, error: raw, context: { day_number: 2 }, call_id: "a" }),
    event(2, "tool_finished", 1, { call_id: "a", outcome: "retry", message: raw }),
    event(3, "candidate_assessed", 2, { message: raw }),
    event(4, "fact_resolution_blocked", 3, { gap_count: 2, message: raw }),
    event(5, "provider_blocked", 4, { message: raw }),
  ], false);
  assert.equal(rows[0].label, "整理行程安排");
  assert.equal(rows[0].detail, "第 2 天");
  assert.equal(rows[0].state, "retry");
  assert.ok(!JSON.stringify(rows).includes(raw));
  assert.ok(!JSON.stringify(rows).includes("核验"));
  assert.ok(!JSON.stringify(rows).includes("toolName"));
});
