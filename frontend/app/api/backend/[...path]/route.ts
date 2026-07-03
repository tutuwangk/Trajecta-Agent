import { NextResponse } from "next/server";

const configuredBackendBase = process.env.BACKEND_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL;
const BACKEND_BASE = configuredBackendBase?.startsWith("http") ? configuredBackendBase : "http://127.0.0.1:8000";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

async function proxy(request: Request, context: RouteContext) {
  const { path } = await context.params;
  const url = new URL(request.url);
  const target = `${BACKEND_BASE}/${path.map(encodeURIComponent).join("/")}${url.search}`;

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
    return NextResponse.json({
      ok: false,
      data: null,
      error: { code: "network_error", message: "整理服务暂时不可用，请确认本地服务已启动后再重试。" },
      step_status: {}
    });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
