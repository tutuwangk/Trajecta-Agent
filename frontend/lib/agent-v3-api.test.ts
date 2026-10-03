import assert from "node:assert/strict";
import { test } from "node:test";
import { createAgentV3Run, createAgentV3Revision, getAgentV3Delivery, getAgentV3Run } from "./agent-v3-api.ts";

const originalFetch = globalThis.fetch;

test("travel requests handle missing delivery, structured errors and invalid responses", async () => {
  try {
    globalThis.fetch = async () => new Response(null, { status: 404 });
    assert.equal(await getAgentV3Delivery("missing"), null);
    globalThis.fetch = async () => Response.json({ detail: [{ msg: "invalid" }] }, { status: 422 });
    await assert.rejects(getAgentV3Run("invalid"), /请检查旅行信息/);
    globalThis.fetch = async () => new Response("unavailable", { status: 502 });
    await assert.rejects(getAgentV3Run("unavailable"), /旅行规划服务暂时无法连接/);
    globalThis.fetch = async () => Response.json({ error: { code: "network_error", message: "本地旅行规划服务未启动，请启动服务后重试。" } }, { status: 502 });
    await assert.rejects(getAgentV3Run("backend-offline"), /本地旅行规划服务未启动/);
    globalThis.fetch = async () => Response.json({ error: { message: 123 } }, { status: 502 });
    await assert.rejects(getAgentV3Run("invalid-error"), /旅行规划服务暂时无法连接/);
    globalThis.fetch = async () => new Response("invalid", { status: 200 });
    await assert.rejects(getAgentV3Run("invalid-json"), /内容不完整/);
    globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
    await assert.rejects(getAgentV3Run("offline"), /检查连接/);
  } finally { globalThis.fetch = originalFetch; }
});

test("read requests escape identifiers and propagate cancellation", async () => {
  try {
    const controller = new AbortController();
    let requestedUrl = "";
    globalThis.fetch = async (input, init) => {
      requestedUrl = String(input);
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    };
    const request = getAgentV3Run("run/with?separator", controller.signal);
    controller.abort();
    await assert.rejects(request, { name: "AbortError" });
    assert.match(requestedUrl, /run%2Fwith%3Fseparator$/);
  } finally { globalThis.fetch = originalFetch; }
});

test("retrying a submitted plan preserves its idempotency key", async () => {
  const bodies: Array<Record<string, string>> = [];
  try {
    globalThis.fetch = async (_input, init) => {
      bodies.push(JSON.parse(String(init?.body)));
      if (bodies.length === 1) throw new TypeError("response lost");
      return Response.json({ run: { run_id: "same-run" } });
    };
    await assert.rejects(createAgentV3Run("workspace", "retry-key"));
    await createAgentV3Run("workspace", "retry-key");
    await createAgentV3Revision("workspace", "调整下午安排", "revision-key");
    await createAgentV3Revision("workspace", "调整下午安排", "revision-key");
    assert.equal(bodies[0].idempotency_key, bodies[1].idempotency_key);
    assert.equal(bodies[2].idempotency_key, bodies[3].idempotency_key);
  } finally { globalThis.fetch = originalFetch; }
});
