import type { AgentV3Event, AgentV3Run } from "./agent-v3-types";

export const toolLabels: Record<string, string> = {
  list_grounding_targets: "整理待查地点",
  read_grounding_target: "搜索地点候选",
  submit_grounding_decision: "确认地点",
  submit_grounding_decisions: "批量确认地点",
  finalize_grounding: "汇总地点信息",
  read_planning_context: "读取行程与偏好",
  submit_plan: "编排行程草案",
  assess_candidate: "查询路线并核验行程",
  finalize_candidate: "检查交付条件并生成结果",
  request_clarification: "整理待确认问题",
};

const eventLabels: Record<string, string> = {
  run_started: "开始规划",
  requirements_started: "读取旅行资料",
  requirements_ready: "旅行需求已整理",
  candidate_group_ready: "地点候选已找到",
  grounding_decision_submitted: "地点选择已记录",
  grounding_registry_compiled: "地点信息已汇总",
  working_draft_committed: "行程草案已保存",
  candidate_assessed: "行程核验已完成",
  release_published: "行程已生成",
  fact_resolution_blocked: "部分信息需要补充",
  clarification_requested: "等待你确认信息",
  checkpoint_restored: "已恢复保存的进度",
  checkpoint_candidates_reused_after_revision: "已复用查到的地点",
  run_cancelled: "规划已取消",
  run_cancelled_observed: "规划已停止",
  run_failed: "本次规划未完成",
  run_interrupted: "规划已暂停",
  run_needs_resume: "进度已保存，可继续",
  planning_budget_paused: "进度已保存，可继续",
  planning_repair_paused: "草案修正已暂停",
  planning_time_budget_exhausted: "本轮用时已到上限",
  provider_blocked: "外部服务暂不可用",
};
const pauseEvents = new Set(["clarification_requested", "run_cancelled", "run_cancelled_observed", "run_failed", "run_interrupted", "run_needs_resume", "planning_budget_paused", "planning_repair_paused", "planning_time_budget_exhausted", "provider_blocked", "release_published"]);

export function eventTime(value: string): number {
  // SQLite CURRENT_TIMESTAMP is UTC and has no timezone suffix.
  const normalized = value.replace(" ", "T");
  return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/.test(normalized) ? normalized : `${normalized}Z`);
}

export function mergeRunEvents(current: AgentV3Event[], incoming: AgentV3Event[]): AgentV3Event[] {
  const byId = new Map(current.map((event) => [event.event_id, event]));
  incoming.forEach((event) => byId.set(event.event_id, event));
  return [...byId.values()].sort((a, b) => a.event_id - b.event_id);
}

export function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒` : `${seconds} 秒`;
}

export function runElapsed(events: AgentV3Event[], active: boolean, now: number): number {
  let start: number | null = null;
  let elapsed = 0;
  for (const event of events) {
    const time = eventTime(event.created_at);
    if (!Number.isFinite(time)) continue;
    if (event.type === "run_started" && start === null) start = time;
    if (pauseEvents.has(event.type) && start !== null) {
      elapsed += Math.max(0, time - start);
      start = null;
    }
  }
  if (start !== null) {
    const last = eventTime(events.at(-1)?.created_at || "");
    elapsed += Math.max(0, (active ? now : Number.isFinite(last) ? last : start) - start);
  }
  return elapsed;
}

export type ProgressRow = {
  id: number; label: string; kind: "tool" | "milestone"; detail?: string;
  toolName?: string; startedAt: number; durationMs?: number;
  state: "running" | "done" | "retry" | "interrupted";
};

export function progressRows(events: AgentV3Event[], active: boolean): ProgressRow[] {
  const finishes = new Map(events.filter((event) => event.type === "tool_finished").map((event) => [event.call_id, event]));
  return events.flatMap((event): ProgressRow[] => {
    if (event.type === "tool_started") {
      const finished = finishes.get(event.call_id);
      const interrupted = events.some((next) => next.event_id > event.event_id && pauseEvents.has(next.type));
      const toolName = String(event.tool_name || "");
      const context = event.context as { day_number?: number } | undefined;
      return [{ id: event.event_id, kind: "tool", label: toolLabels[toolName] || "执行规划工具", toolName,
        detail: typeof event.object_name === "string" ? event.object_name : context?.day_number ? `第 ${context.day_number} 天` : undefined,
        startedAt: eventTime(event.created_at),
        durationMs: typeof finished?.duration_ms === "number" ? finished.duration_ms : undefined,
        state: finished ? finished.outcome === "retry" ? "retry" : finished.outcome === "succeeded" ? "done" : "interrupted" : active && !interrupted ? "running" : "interrupted" }];
    }
    const label = eventLabels[event.type];
    if (!label) return [];
    const reading = event.type === "requirements_started";
    const readFinished = reading ? events.find((next) => next.event_id > event.event_id && next.type === "requirements_ready") : undefined;
    const readInterrupted = reading && events.some((next) => next.event_id > event.event_id && pauseEvents.has(next.type));
    const detail = event.type === "requirements_ready" && typeof event.place_count === "number" ? `提取 ${event.place_count} 个地点与 ${event.constraint_count ?? 0} 项偏好`
      : event.type === "candidate_group_ready" && typeof event.retained_candidate_count === "number" ? `保留 ${event.retained_candidate_count} 个候选`
      : event.type === "provider_blocked" ? "服务恢复后，可继续当前规划"
      : event.type === "fact_resolution_blocked" && typeof event.gap_count === "number" ? `${event.gap_count} 项信息待核验` : undefined;
    return [{ id: event.event_id, kind: "milestone", label, detail, startedAt: eventTime(event.created_at),
      durationMs: readFinished ? Math.max(0, eventTime(readFinished.created_at) - eventTime(event.created_at)) : undefined,
      state: reading && !readFinished ? active && !readInterrupted ? "running" : "interrupted" : "done" }];
  });
}

export function currentActivity(run: AgentV3Run, events: AgentV3Event[]): string {
  const statusLabels: Record<string, string> = { created: "等待开始规划", waiting_user: "需要你补充一些信息", needs_resume: "进度已保存，等待继续", succeeded: "你的行程已准备好", cancelled: "规划已取消", failed: "本次规划未完成" };
  if (statusLabels[run.status]) return statusLabels[run.status];
  const running = progressRows(events, true).filter((row) => row.state === "running");
  if (running.length) return running.length > 1 ? `正在并行处理 ${running.length} 项工具任务` : `${running[0].label}${running[0].detail ? ` · ${running[0].detail}` : ""}`;
  const latest = events.at(-1);
  if (latest?.type === "requirements_started" || latest?.type === "run_started") return "正在读取旅行资料";
  if (latest?.type === "model_request_started") return "正在推敲下一步安排";
  return "正在整理信息与规划下一步";
}
