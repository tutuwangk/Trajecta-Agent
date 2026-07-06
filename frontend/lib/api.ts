import type { ApiResponse, SessionData, UserProfile } from "./types";

const API_BASE = "/api/backend";

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
  const payload = (await response.json().catch(() => null)) as ApiResponse<T> | null;
  if (!response.ok) {
    return {
      ok: false,
      data: payload?.data ?? null,
      error: payload?.error ?? { code: String(response.status), message: "请求失败，请稍后再试。" },
      step_status: payload?.step_status ?? {}
    };
  }
  return payload ?? {
    ok: false,
    data: null,
    error: { code: "invalid_response", message: "服务返回了无法识别的结果。" },
    step_status: {}
  };
}

export function createSession(payload: { raw_input: string; notes: string; user_profile?: UserProfile }) {
  return request<{ session_id: string; user_profile: Record<string, unknown> }>("/sessions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function recognizePlaces(sessionId: string) {
  return request<Record<string, unknown>>(`/sessions/${sessionId}/recognize-places`, { method: "POST" });
}

export function extractPois(sessionId: string) {
  return recognizePlaces(sessionId);
}

export function getSession(sessionId: string) {
  return request<SessionData>(`/sessions/${sessionId}`);
}

export function updatePois(sessionId: string, decisions: Array<{ poi_id: string; decision: string; manual_name?: string }>) {
  return updatePlaceOverrides(sessionId, decisions);
}

export function updatePlaceOverrides(sessionId: string, decisions: Array<{ poi_id: string; decision: string; manual_name?: string }>) {
  return request<{ pois: SessionData["pois"] }>(`/sessions/${sessionId}/place-overrides`, {
    method: "POST",
    body: JSON.stringify({ decisions })
  });
}

export function planTrip(sessionId: string) {
  return request<Record<string, unknown>>(`/sessions/${sessionId}/plan`, { method: "POST" });
}

export function reviseTrip(sessionId: string, instruction: string) {
  return request<Record<string, unknown>>(`/sessions/${sessionId}/revise`, {
    method: "POST",
    body: JSON.stringify({ instruction })
  });
}
