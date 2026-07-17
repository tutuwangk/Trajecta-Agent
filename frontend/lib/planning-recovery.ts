import type { ApiResponse, SessionData } from "./types";

type ReadSession = (sessionId: string) => Promise<ApiResponse<SessionData>>;
type ResumeRun = (sessionId: string, runId: string) => Promise<unknown>;

export type PlanningRecoveryResult =
  | { kind: "completed"; session: SessionData }
  | { kind: "failed"; message: string }
  | { kind: "unreachable"; message: string }
  | { kind: "timeout"; message: string }
  | { kind: "cancelled" };

export async function waitForPlanningCompletion(
  sessionId: string,
  readSession: ReadSession,
  signal: AbortSignal,
  options: {
    intervalMs?: number;
    timeoutMs?: number;
    maxConsecutiveNetworkErrors?: number;
    previousRunId?: string | null;
    resumeRun?: ResumeRun;
  } = {}
): Promise<PlanningRecoveryResult> {
  const intervalMs = options.intervalMs ?? 3_000;
  const timeoutMs = options.timeoutMs ?? 20 * 60_000;
  const maxConsecutiveNetworkErrors = options.maxConsecutiveNetworkErrors ?? 3;
  const startedAt = Date.now();
  let consecutiveNetworkErrors = 0;
  const resumeRequested = new Set<string>();

  while (!signal.aborted && Date.now() - startedAt < timeoutMs) {
    const result = await readSession(sessionId);
    if (signal.aborted) return { kind: "cancelled" };
    if (!result.ok || !result.data) {
      consecutiveNetworkErrors += 1;
      if (consecutiveNetworkErrors >= maxConsecutiveNetworkErrors) {
        return {
          kind: "unreachable",
          message: result.error?.message || "暂时无法连接整理服务，请确认本地服务正常后重试。"
        };
      }
      if (!(await abortableDelay(intervalMs, signal))) return { kind: "cancelled" };
      continue;
    }

    consecutiveNetworkErrors = 0;
    const session = result.data;
    const run = session.latest_planning_run;
    const isCurrentRun = Boolean(run?.id) && run?.id !== options.previousRunId;
    if (isCurrentRun && run?.status === "running" && run.id && options.resumeRun && !resumeRequested.has(run.id)) {
      resumeRequested.add(run.id);
      await options.resumeRun(sessionId, run.id);
    }
    if (isCurrentRun && run?.status === "completed" && session.itinerary_state?.itinerary) {
      return { kind: "completed", session };
    }
    if (isCurrentRun && run?.status === "failed") {
      return { kind: "failed", message: planningRunFailureMessage(run.error_code, run.error_message, run.blockers) };
    }
    if (!(await abortableDelay(intervalMs, signal))) return { kind: "cancelled" };
  }

  if (signal.aborted) return { kind: "cancelled" };
  return { kind: "timeout", message: "路线仍在后台生成，但等待时间较长。请稍后刷新本页查看结果。" };
}

export function planningRunFailureMessage(code?: string | null, message?: string | null, blockers = [] as Array<{ message?: string; action_hint?: string }>) {
  const primary = blockers.find((blocker) => blocker.message?.trim());
  if (message?.trim()) {
    const detail = primary?.message?.trim();
    const action = primary?.action_hint?.trim();
    return [message.trim(), detail, action].filter((item, index, values) => item && values.indexOf(item) === index).join(" ");
  }
  const messages: Record<string, string> = {
    plan_capacity_unresolved: "必去地点在当前天数内过于拥挤，请增加天数或将部分地点改为备选。",
    plan_meal_unresolved: "当前路线的用餐时段仍有冲突，请调整地点或用餐要求。",
    plan_route_fact_unresolved: "部分实际采用路段暂时缺少可靠交通时间。",
    plan_time_constraints_unresolved: "部分地点的游览时段仍有冲突，请调整地点或时间要求后重试。",
    plan_poi_fact_unresolved: "部分地点尚未可靠核验，请先确认地点。",
    plan_factual_constraints_unresolved: "路线仍有无法自动解决的硬约束，请根据提示调整后重试。",
    llm_request_failed: "路线整理服务暂时繁忙，请稍后重试。",
    itinerary_publish_blocked: "路线事实校验未通过，请检查地点信息后重试。"
  };
  return messages[code || ""] || `路线生成未完成${code ? `（${code}）` : ""}，请稍后重试。`;
}

function abortableDelay(ms: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
