import type { ApiResponse, SessionData } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {})
      },
      cache: "no-store"
    });
  } catch {
    return {
      ok: false,
      data: null,
      error: { code: "network_error", message: "暂时无法连接服务，请稍后重试。" },
      step_status: {}
    };
  }
  if (!response.ok) {
    return {
      ok: false,
      data: null,
      error: { code: String(response.status), message: "请求失败，请稍后再试。" },
      step_status: {}
    };
  }
  return response.json();
}

export function createSession(payload: { raw_input: string; notes: string }) {
  return request<{ session_id: string; user_profile: Record<string, unknown> }>("/sessions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function extractPois(sessionId: string) {
  return request<Record<string, unknown>>(`/sessions/${sessionId}/extract-pois`, { method: "POST" });
}

export function getSession(sessionId: string) {
  return request<SessionData>(`/sessions/${sessionId}`);
}

export function updatePois(sessionId: string, decisions: Array<{ poi_id: string; decision: string; manual_name?: string }>) {
  return request<{ pois: SessionData["pois"] }>(`/sessions/${sessionId}/pois`, {
    method: "PATCH",
    body: JSON.stringify({ decisions })
  });
}

export function planTrip(sessionId: string) {
  return request<Record<string, unknown>>(`/sessions/${sessionId}/plan`, { method: "POST" });
}

export function reviseTrip(sessionId: string, instruction: string, quick_action?: string) {
  return request<Record<string, unknown>>(`/sessions/${sessionId}/revise`, {
    method: "POST",
    body: JSON.stringify({ instruction, quick_action })
  });
}
