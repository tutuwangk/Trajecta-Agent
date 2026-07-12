import assert from "node:assert/strict";
import test from "node:test";

import { planningRunFailureMessage, waitForPlanningCompletion } from "./planning-recovery.ts";
import type { ApiResponse, SessionData } from "./types.ts";

test("planning recovery polls the existing run until the saved itinerary is ready", async () => {
  let calls = 0;
  const readSession = async (): Promise<ApiResponse<SessionData>> => {
    calls += 1;
    return response(session(calls === 1 ? "running" : "completed", calls > 1));
  };

  const result = await waitForPlanningCompletion("s1", readSession, new AbortController().signal, {
    intervalMs: 0,
    timeoutMs: 1_000
  });

  assert.equal(result.kind, "completed");
  assert.equal(calls, 2);
});

test("planning recovery surfaces the backend run failure instead of retrying POST", async () => {
  const result = await waitForPlanningCompletion(
    "s1",
    async () => response(session("failed", false, "plan_failed", "地图事实不足")),
    new AbortController().signal,
    { intervalMs: 0, timeoutMs: 1_000 }
  );

  assert.deepEqual(result, { kind: "failed", message: "地图事实不足" });
});

test("planning recovery reports a persistently unreachable backend", async () => {
  let calls = 0;
  const result = await waitForPlanningCompletion(
    "s1",
    async () => {
      calls += 1;
      return { ok: false, data: null, error: { code: "network_error", message: "后端不可达" }, step_status: {} };
    },
    new AbortController().signal,
    { intervalMs: 0, timeoutMs: 1_000, maxConsecutiveNetworkErrors: 3 }
  );

  assert.deepEqual(result, { kind: "unreachable", message: "后端不可达" });
  assert.equal(calls, 3);
});

test("planning recovery does not mistake a previous completed run for the interrupted request", async () => {
  let calls = 0;
  const readSession = async (): Promise<ApiResponse<SessionData>> => {
    calls += 1;
    const data = session("completed", true);
    data.latest_planning_run = { id: calls === 1 ? "old-run" : "new-run", status: "completed" };
    return response(data);
  };

  const result = await waitForPlanningCompletion("s1", readSession, new AbortController().signal, {
    intervalMs: 0,
    timeoutMs: 1_000,
    previousRunId: "old-run"
  });

  assert.equal(result.kind, "completed");
  assert.equal(calls, 2);
});

test("planning recovery can be cancelled when the page changes", async () => {
  const controller = new AbortController();
  controller.abort();

  const result = await waitForPlanningCompletion("s1", async () => response(session("running")), controller.signal);

  assert.deepEqual(result, { kind: "cancelled" });
});

test("planning failure code has a readable fallback", () => {
  assert.equal(planningRunFailureMessage("llm_request_failed"), "路线整理服务暂时繁忙，请稍后重试。");
});

function response(data: SessionData): ApiResponse<SessionData> {
  return { ok: true, data, error: null, step_status: {} };
}

function session(
  status: "running" | "completed" | "failed",
  withItinerary = false,
  errorCode?: string,
  errorMessage?: string
): SessionData {
  return {
    session_id: "s1",
    raw_input: "",
    notes: "",
    user_profile: {
      destination: "成都",
      days: 1,
      nights: 0,
      travelers: { count: 1, type: "未说明" },
      budget_level: "medium",
      transport_preference: [],
      preferences: {},
      constraints: { avoid_too_tired: false, must_visit: [], avoid_visit: [] }
    },
    pois: [],
    revision_history: [],
    latest_planning_run: { id: "r1", status, error_code: errorCode, error_message: errorMessage },
    itinerary_state: withItinerary
      ? {
          runtime_pois: [],
          route_matrix: [],
          itinerary: { destination: "成都", days: [], global_risks: [], uncertain_pois: [], revision_notes: [] },
          verification: { passed: true, issues: [] }
        }
      : null
  };
}
