import type { ApiResponse, PlanResult, PlanningBlocker } from "./types";

export type PlanningFlowOutcome =
  | { kind: "failed"; message: string; blockers?: PlanningBlocker[] }
  | { kind: "running"; result: Extract<PlanResult, { status: "running" }> }
  | { kind: "completed"; result: Extract<PlanResult, { status: "completed" }> }
  | { kind: "needs_user_choice"; result: Extract<PlanResult, { status: "needs_user_choice" }> };

export function resolvePlanningFlow(response: ApiResponse<PlanResult>, fallbackMessage: string): PlanningFlowOutcome {
  if (!response.ok || !response.data) {
    return {
      kind: "failed",
      message: response.error?.message || fallbackMessage,
      blockers: response.error?.blockers,
    };
  }
  if (response.data.status === "needs_user_choice") {
    return { kind: "needs_user_choice", result: response.data };
  }
  if (response.data.status === "running") {
    return { kind: "running", result: response.data };
  }
  if (response.data.status === "failed") {
    return {
      kind: "failed",
      message: response.data.error_message || fallbackMessage,
    };
  }
  return { kind: "completed", result: response.data };
}

export function planningControlsDisabled(busyMessage: string): boolean {
  return Boolean(busyMessage);
}
