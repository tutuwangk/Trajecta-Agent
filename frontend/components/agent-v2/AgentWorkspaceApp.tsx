"use client";

import { useCallback, useEffect, useState } from "react";
import {
  answerAgentClarification,
  cancelAgentRun,
  createAgentRevision,
  createAgentRun,
  createAgentWorkspace,
  getAgentEvents,
  getAgentRun,
  getAgentWorkspace,
} from "@/lib/agent-v2-api";
import type {
  AgentV2Event,
  AgentV2Interruption,
  AgentV2Narrative,
  AgentV2Release,
  AgentV2Run,
  AgentV2Workspace,
} from "@/lib/types";
import { AgentProgressStream } from "./AgentProgressStream";
import { ClarificationBatchCard } from "./ClarificationBatchCard";
import { WorkspacePanel } from "./WorkspacePanel";
import { ReleaseStatusCard } from "./ReleaseStatusCard";

const terminal = new Set(["published", "incomplete", "failed", "cancelled"]);

export function AgentWorkspaceApp() {
  const [rawRequest, setRawRequest] = useState("");
  const [destination, setDestination] = useState("");
  const [startDate, setStartDate] = useState("");
  const [days, setDays] = useState("2");
  const [workspaceId, setWorkspaceId] = useState("");
  const [run, setRun] = useState<AgentV2Run | null>(null);
  const [workspace, setWorkspace] = useState<AgentV2Workspace | null>(null);
  const [events, setEvents] = useState<AgentV2Event[]>([]);
  const [interruption, setInterruption] = useState<AgentV2Interruption | null>(null);
  const [release, setRelease] = useState<AgentV2Release | null>(null);
  const [narrative, setNarrative] = useState<AgentV2Narrative | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const active = !!run && !terminal.has(run.status);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const savedWorkspaceId = params.get("workspace");
    const savedRunId = params.get("run");
    if (!savedWorkspaceId || !savedRunId || workspaceId || run) return;
    setWorkspaceId(savedWorkspaceId);
    void getAgentRun(savedRunId)
      .then((payload) => {
        setRun(payload.run);
        setInterruption(payload.interruption);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "无法恢复运行"));
  }, [run, workspaceId]);

  const refresh = useCallback(async () => {
    if (!run?.run_id || !workspaceId) return;
    const [runPayload, eventPayload, workspacePayload] = await Promise.all([
      getAgentRun(run.run_id),
      getAgentEvents(run.run_id),
      getAgentWorkspace(workspaceId),
    ]);
    setRun(runPayload.run);
    setInterruption(runPayload.interruption);
    setEvents(eventPayload.events);
    setWorkspace(workspacePayload.workspace);
    setRawRequest((current) => current || workspacePayload.workspace.goal_ledger.goal.raw_request);
    setDestination((current) => current || workspacePayload.workspace.goal_ledger.goal.destination || "");
    setStartDate((current) => current || workspacePayload.workspace.goal_ledger.goal.start_date || "");
    setDays((current) => current || String(workspacePayload.workspace.goal_ledger.goal.days || 2));
    setRelease(workspacePayload.releases.at(-1) || null);
    setNarrative(workspacePayload.narratives.at(-1) || null);
  }, [run?.run_id, workspaceId]);

  useEffect(() => {
    if (!run?.run_id) return;
    void refresh().catch((reason) => setError(reason instanceof Error ? reason.message : "刷新失败"));
    if (!active) return;
    const timer = window.setInterval(() => {
      void refresh().catch((reason) => setError(reason instanceof Error ? reason.message : "刷新失败"));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [active, refresh, run?.run_id]);

  async function start() {
    setError("");
    setSubmitting(true);
    try {
      const created = await createAgentWorkspace({
        raw_request: rawRequest,
        destination: destination || undefined,
        start_date: startDate || undefined,
        days: Number(days) || undefined,
      });
      const scheduled = await createAgentRun(created.workspace_id);
      setWorkspaceId(created.workspace_id);
      setWorkspace(created.workspace);
      setRun(scheduled.run);
      setEvents([]);
      window.history.replaceState(
        {},
        "",
        `/?workspace=${encodeURIComponent(created.workspace_id)}&run=${encodeURIComponent(scheduled.run.run_id)}`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法启动 Agent");
    } finally {
      setSubmitting(false);
    }
  }

  async function revise() {
    if (!workspaceId) return;
    setError("");
    const scheduled = await createAgentRevision(workspaceId, rawRequest);
    setRun(scheduled.run);
    setEvents([]);
    setInterruption(null);
    window.history.replaceState(
      {},
      "",
      `/?workspace=${encodeURIComponent(workspaceId)}&run=${encodeURIComponent(scheduled.run.run_id)}`,
    );
  }

  return (
    <main className="mx-auto min-h-[100dvh] max-w-7xl px-4 py-8 sm:px-6">
      <header>
        <p className="eyebrow">Trajecta · Agent V2</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">把完整旅行目标直接交给 Agent</h1>
        <p className="subtle mt-3 max-w-3xl">地点识别、消歧、事实查询和路线规划都在同一个运行中完成；只有会实质改变结果的问题才会暂停询问。</p>
      </header>

      <div className="mt-7 grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
        <div className="space-y-5">
          <section className="panel space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="text-sm font-medium">
                目的地
                <input className="field mt-1" placeholder="目的地" value={destination} disabled={active} onChange={(event) => setDestination(event.target.value)} />
              </label>
              <label className="text-sm font-medium">
                出发日期
                <input data-testid="agent-start-date" className="field mt-1" type="date" value={startDate} disabled={active} onChange={(event) => setStartDate(event.target.value)} />
              </label>
              <label className="text-sm font-medium">
                天数
                <input className="field mt-1" type="number" min="1" max="60" value={days} disabled={active} onChange={(event) => setDays(event.target.value)} />
              </label>
            </div>
            <textarea className="field min-h-52 resize-y" placeholder="例如：8月去成都两天，武侯祠必须去，第二天下午有不可调整的预约，希望节奏轻松……" value={rawRequest} disabled={active} onChange={(event) => setRawRequest(event.target.value)} />
            <div className="flex flex-wrap gap-2">
              {!workspaceId ? (
                <button className="btn-primary" disabled={!rawRequest.trim() || submitting} onClick={start}>{submitting ? "正在创建…" : "启动规划 Agent"}</button>
              ) : active ? (
                <button className="btn-secondary" onClick={() => run && cancelAgentRun(run.run_id).then((value) => setRun(value.run))}>取消运行</button>
              ) : (
                <button className="btn-primary" disabled={!rawRequest.trim()} onClick={revise}>基于当前工作区发起修改</button>
              )}
              {run ? <span className="rounded-full bg-surface px-3 py-2 text-sm text-muted">状态：{run.status}</span> : null}
            </div>
            {error ? <p className="text-sm text-red-700">{error}</p> : null}
            {run?.error_message ? <p className="text-sm text-amber-800">{run.error_message}</p> : null}
          </section>

          {interruption && run ? (
            <ClarificationBatchCard
              interruption={interruption}
              onSubmit={async (answers) => {
                await answerAgentClarification(run.run_id, answers);
                setInterruption(null);
                await refresh();
              }}
            />
          ) : null}
          <WorkspacePanel workspace={workspace} />
          {release ? <ReleaseStatusCard release={release} narrative={narrative} /> : null}
        </div>
        <AgentProgressStream events={events} />
      </div>
    </main>
  );
}
