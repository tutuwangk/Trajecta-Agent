import type {
  AgentV3Delivery,
  AgentV3Event,
  AgentV3Run,
  AgentV3Workspace,
} from "./agent-v3-types";

const API_BASE = "/api/backend/api/v3";

function responseMessage(payload: unknown): string {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error: unknown }).error;
    if (error && typeof error === "object" && "message" in error && typeof error.message === "string") return error.message;
  }
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "请检查旅行信息后再试。";
  }
  return "旅行规划服务暂时无法连接，请稍后重试。";
}

async function request<T>(path: string, init?: RequestInit, allowMissing = false): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  init?.signal?.addEventListener("abort", abort, { once: true });
  if (init?.signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, 20000);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
    if (allowMissing && response.status === 404) return null as T;
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new Error(responseMessage(payload));
    if (payload === null) throw new Error("服务返回的内容不完整，请稍后重试。");
    return payload as T;
  } catch (reason) {
    if (init?.signal?.aborted) throw reason;
    if (controller.signal.aborted) throw new Error("连接等待超时，请重试。");
    if (reason instanceof TypeError) throw new Error("暂时无法连接旅行服务，请检查连接后重试。");
    throw reason;
  } finally {
    clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abort);
  }
}

export function getAgentV3Delivery(runId: string, signal?: AbortSignal): Promise<AgentV3Delivery | null> {
  return request(`/agent-runs/${encodeURIComponent(runId)}/delivery`, { signal }, true);
}

export function createAgentV3Workspace(input: {
  raw_request: string;
  destination: string;
  start_date: string;
  days: number;
}) {
  return request<{ workspace_id: string; workspace: AgentV3Workspace }>("/trip-workspaces", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createAgentV3Run(workspaceId: string, idempotencyKey = crypto.randomUUID()) {
  return request<{ run: AgentV3Run }>(`/trip-workspaces/${encodeURIComponent(workspaceId)}/runs`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
}

export function getAgentV3Workspace(workspaceId: string, signal?: AbortSignal) {
  return request<{ workspace: AgentV3Workspace }>(`/trip-workspaces/${encodeURIComponent(workspaceId)}`, { signal });
}

export function getAgentV3Run(runId: string, signal?: AbortSignal) {
  return request<{ run: AgentV3Run; clarification_questions: string[] }>(
    `/agent-runs/${encodeURIComponent(runId)}`, { signal },
  );
}

export function getAgentV3Events(runId: string, afterEventId = 0, signal?: AbortSignal) {
  return request<{ events: AgentV3Event[] }>(
    `/agent-runs/${encodeURIComponent(runId)}/events?after_event_id=${afterEventId}`, { signal },
  );
}

export function cancelAgentV3Run(runId: string) {
  return request<{ run: AgentV3Run }>(`/agent-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
}

export function resumeAgentV3Run(runId: string) {
  return request<{ run: AgentV3Run; accepted: boolean }>(
    `/agent-runs/${encodeURIComponent(runId)}/resume`,
    { method: "POST" },
  );
}

export function answerAgentV3Clarification(runId: string, answers: Record<string, string>) {
  return request<{ run: AgentV3Run; accepted: boolean }>(`/agent-runs/${encodeURIComponent(runId)}/answers`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

export function createAgentV3Revision(workspaceId: string, rawRequest: string, idempotencyKey = crypto.randomUUID()) {
  return request<{ run: AgentV3Run }>(`/trip-workspaces/${encodeURIComponent(workspaceId)}/revisions`, {
    method: "POST",
    body: JSON.stringify({ raw_request: rawRequest, idempotency_key: idempotencyKey }),
  });
}
