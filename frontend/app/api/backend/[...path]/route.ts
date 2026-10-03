import { NextResponse } from "next/server";

// Route planning can include bounded LLM retries and live map lookups.
// Keep the proxy alive longer than the backend's normal worst-case workflow.
export const maxDuration = 600;

const configuredBackendBase = process.env.BACKEND_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL;

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

function resolveBackendBase(): string | null {
  if (configuredBackendBase?.startsWith("http")) {
    return configuredBackendBase.replace(/\/+$/, "");
  }
  if (process.env.NODE_ENV !== "production") {
    return "http://127.0.0.1:8000";
  }
  return null;
}

async function proxy(request: Request, context: RouteContext) {
  const backendBase = resolveBackendBase();
  if (!backendBase) {
    return NextResponse.json(
      {
        ok: false,
        data: null,
        error: {
          code: "missing_configuration",
          message: "当前部署未配置 BACKEND_API_BASE_URL，无法连接后端服务。"
        },
        step_status: {}
      },
      { status: 503 }
    );
  }

  const { path } = await context.params;
  const url = new URL(request.url);
  const target = `${backendBase}/${path.map(encodeURIComponent).join("/")}${url.search}`;

  try {
    const response = await fetch(target, {
      method: request.method,
      headers: {
        "Content-Type": request.headers.get("Content-Type") || "application/json",
        Accept: request.headers.get("Accept") || "application/json"
      },
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
      cache: "no-store"
    });

    return new Response(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("Content-Type") || "application/json"
      }
    });
  } catch {
    const isPlanningRequest = path.at(-1) === "plan";
    return NextResponse.json(
      {
        ok: false,
        data: null,
        error: {
          code: "network_error",
          message: isPlanningRequest
            ? "路线生成连接意外中断，请稍后重试；服务端可能仍在处理。"
            : process.env.NODE_ENV === "production"
              ? "无法连接后端服务，请检查 BACKEND_API_BASE_URL 是否指向可访问的后端地址。"
              : "整理服务暂时不可用，请确认本地服务已启动后再重试。"
        },
        step_status: {}
      },
      { status: 502 }
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
