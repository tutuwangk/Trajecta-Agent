import type {
  AgentV2Event,
  AgentV2Interruption,
  AgentV2Narrative,
  AgentV2Release,
  AgentV2Run,
  AgentV2Workspace,
} from "./types";

const API_BASE = "/api/backend/api/v2";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.detail || "Agent 服务请求失败");
  }
  return payload as T;
}

export function createAgentWorkspace(input: {
  raw_request: string;
  destination?: string;
  start_date?: string;
  days?: number;
}) {
  return request<{ workspace_id: string; workspace: AgentV2Workspace }>("/trip-workspaces", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createAgentRun(workspaceId: string) {
  return request<{ run: AgentV2Run }>(`/trip-workspaces/${workspaceId}/runs`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
  });
}

export function getAgentRun(runId: string) {
  return request<{ run: AgentV2Run; interruption: AgentV2Interruption | null }>(
    `/agent-runs/${runId}`,
  );
}

export function getAgentEvents(runId: string, afterEventId = 0) {
  return request<{ events: AgentV2Event[] }>(
    `/agent-runs/${runId}/events?after_event_id=${afterEventId}`,
  );
}

export function getAgentWorkspace(workspaceId: string) {
  return request<{
    workspace: AgentV2Workspace;
    claims: unknown[];
    releases: AgentV2Release[];
    narratives: AgentV2Narrative[];
  }>(
    `/trip-workspaces/${workspaceId}`,
  );
}

export function answerAgentClarification(runId: string, answers: Record<string, string>) {
  return request<{ accepted: boolean }>(`/agent-runs/${runId}/answers`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

export function cancelAgentRun(runId: string) {
  return request<{ run: AgentV2Run }>(`/agent-runs/${runId}/cancel`, { method: "POST" });
}

export function createAgentRevision(workspaceId: string, rawRequest: string) {
  return request<{ run: AgentV2Run }>(`/trip-workspaces/${workspaceId}/revisions`, {
    method: "POST",
    body: JSON.stringify({ raw_request: rawRequest, idempotency_key: crypto.randomUUID() }),
  });
}
