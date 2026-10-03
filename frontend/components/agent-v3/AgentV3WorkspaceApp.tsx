"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, CalendarDays, Check, Compass, MapPin, Plus, Route, Sparkles, X, LoaderCircle } from "lucide-react";
import { answerAgentV3Clarification, cancelAgentV3Run, createAgentV3Revision, createAgentV3Run, createAgentV3Workspace, getAgentV3Delivery, getAgentV3Events, getAgentV3Run, getAgentV3Workspace, resumeAgentV3Run } from "@/lib/agent-v3-api";
import type { AgentV3Delivery, AgentV3Event, AgentV3Run } from "@/lib/agent-v3-types";
import { demoDelivery } from "@/lib/agent-v3-demo";
import { mergeRunEvents } from "@/lib/agent-v3-progress";
import { deliveryMatchesRun } from "@/lib/agent-v3-view";
import { AgentProgress } from "./AgentProgress";
import { DeliveryPanel } from "./DeliveryPanel";

const activeStatuses = new Set(["created", "active"]);
const terminalStatuses = new Set(["succeeded", "failed", "cancelled"]);
const actionMessages: Record<string, string> = {
  "开始规划": "行程暂时无法开始，请重试。",
  "恢复旅程": "暂时无法读取已保存的旅程，请重试。",
  "调整中": "行程调整暂时未完成，请重试。",
  "取消中": "规划暂时无法停止，请重试。",
  "继续中": "规划暂时无法继续，请重试。",
  "确认中": "旅行信息暂时未提交，请重试。",
};

export function AgentV3WorkspaceApp() {
  const [rawRequest, setRawRequest] = useState("");
  const [destination, setDestination] = useState("");
  const [startDate, setStartDate] = useState("");
  const [days, setDays] = useState("3");
  const [workspaceId, setWorkspaceId] = useState("");
  const [run, setRun] = useState<AgentV3Run | null>(null);
  const [delivery, setDelivery] = useState<AgentV3Delivery | null>(null);
  const [questions, setQuestions] = useState<string[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [demo, setDemo] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef<HTMLElement>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const [events, setEvents] = useState<AgentV3Event[]>([]);
  const [eventError, setEventError] = useState("");
  const operation = useRef(false);
  const generation = useRef(0);
  const runRef = useRef<AgentV3Run | null>(null);
  const pendingStart = useRef<{ fingerprint: string; key: string; workspaceId?: string } | null>(null);
  const pendingRevision = useRef<{ fingerprint: string; key: string } | null>(null);
  const active = !!run && activeStatuses.has(run.status);
  const locked = active || !!busy || run?.status === "waiting_user";

  useEffect(() => {
    if (run?.run_id) window.scrollTo({ top: 0, behavior: "instant" });
  }, [run?.run_id]);

  const applyRun = useCallback((value: AgentV3Run) => {
    const current = runRef.current;
    if (current?.run_id === value.run_id && terminalStatuses.has(current.status) && value.status !== current.status) return;
    runRef.current = value;
    setRun(value);
    if (value.status !== "waiting_user") setQuestions([]);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const savedWorkspaceId = params.get("workspace");
    const savedRunId = params.get("run");
    if (!savedWorkspaceId || !savedRunId) return;
    const controller = new AbortController();
    const token = ++generation.current;
    operation.current = true;
    setBusy("恢复旅程");
    void Promise.all([getAgentV3Workspace(savedWorkspaceId, controller.signal), getAgentV3Run(savedRunId, controller.signal)])
      .then(([workspacePayload, runPayload]) => {
        if (controller.signal.aborted || token !== generation.current) return;
        if (runPayload.run.workspace_id !== savedWorkspaceId) throw new Error("保存的旅程信息不匹配，请重新规划。");
        setWorkspaceId(savedWorkspaceId);
        applyRun(runPayload.run);
        setQuestions(runPayload.run.status === "waiting_user" ? runPayload.clarification_questions : []);
        const source = [...workspacePayload.workspace.sources].reverse().find((item) => item.kind === "user_request" || item.kind === "user_revision");
        setRawRequest(source?.content || "");
        setDestination(workspacePayload.workspace.goal.destination);
        setStartDate(workspacePayload.workspace.goal.start_date);
        setDays(String(workspacePayload.workspace.goal.days));
      }).catch((reason) => { if (!controller.signal.aborted && token === generation.current) setError("暂时无法读取行程，请重试。"); })
      .finally(() => { if (token === generation.current) { operation.current = false; setBusy(""); } });
    return () => controller.abort();
  }, [applyRun]);

  useEffect(() => {
    if (!run?.run_id) return;
    const id = run.run_id;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    let failures = 0;
    const token = generation.current;
    async function poll() {
      try {
        const runPayload = await getAgentV3Run(id, controller.signal);
        if (controller.signal.aborted || token !== generation.current || operation.current || runRef.current?.run_id !== id) return;
        applyRun(runPayload.run);
        setQuestions(runRef.current?.status === "waiting_user" && runPayload.run.status === "waiting_user" ? runPayload.clarification_questions : []);
        const deliveryPayload = await getAgentV3Delivery(id, controller.signal);
        if (controller.signal.aborted || token !== generation.current || operation.current || runRef.current?.run_id !== id) return;
        const matches = !deliveryPayload || !!runRef.current && deliveryMatchesRun(deliveryPayload, runRef.current);
        setDelivery(matches ? deliveryPayload : null);
        failures = 0;
        attempts += 1;
        if (!matches || activeStatuses.has(runRef.current?.status || "")) {
          if (attempts >= 120) { setError(activeStatuses.has(runRef.current?.status || "") ? "规划仍在继续，点击重试查看结果。" : "行程结果同步未完成，请重试。"); return; }
          timer = setTimeout(() => void poll(), 2500);
        }
      } catch (reason) {
        if (controller.signal.aborted || token !== generation.current || operation.current || runRef.current?.run_id !== id) return;
        failures += 1;
        if (failures >= 3 || !active) { setError("暂时无法读取行程，请重试。"); return; }
        timer = setTimeout(() => void poll(), failures * 3000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [run?.run_id, active, applyRun, pollVersion]);

  useEffect(() => {
    if (!run?.run_id) return;
    const id = run.run_id;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    let failures = 0;
    async function pollEvents() {
      try {
        const payload = await getAgentV3Events(id, cursor, controller.signal);
        if (controller.signal.aborted || runRef.current?.run_id !== id) return;
        setEvents((current) => mergeRunEvents(current, payload.events));
        cursor = Math.max(cursor, ...payload.events.map((event) => event.event_id));
        setEventError(""); failures = 0;
        if (active) timer = setTimeout(() => void pollEvents(), 1500);
      } catch (reason) {
        if (controller.signal.aborted || runRef.current?.run_id !== id) return;
        setEventError("暂时无法更新旅行进度"); failures += 1;
        if (active) timer = setTimeout(() => void pollEvents(), Math.min(15000, failures * 3000));
      }
    }
    void pollEvents();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [run?.run_id, active, pollVersion]);

  async function perform(label: string, action: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    generation.current += 1;
    setBusy(label); setError("");
    try { await action(); } catch { setError(actionMessages[label] || "操作暂时未完成，请重试。"); }
    finally { operation.current = false; setBusy(""); setPollVersion((value) => value + 1); }
  }

  function remember(id: string, value: AgentV3Run) {
    setEvents([]); setEventError("");
    setWorkspaceId(id); applyRun(value); setDemo(false); setDrawerOpen(false); setDelivery(null);  setQuestions([]); setAnswers({});
    window.history.replaceState({}, "", `${window.location.pathname}?workspace=${encodeURIComponent(id)}&run=${encodeURIComponent(value.run_id)}`);
  }

  function validate() {
    if (!destination.trim()) return "先填一个想去的城市。";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(startDate) || !Number.isFinite(Date.parse(startDate)) || new Date(`${startDate}T00:00:00Z`).toISOString().slice(0, 10) !== startDate) return "请选择有效的出发日期。";
    if (!/^\d+$/.test(days) || Number(days) < 1 || Number(days) > 30) return "旅行天数请填写 1 到 30 的整数。";
    if (!rawRequest.trim()) return "写下想去的地点或旅行偏好。";
    return "";
  }

  function start() {
    const issue = validate(); if (issue) { setError(issue); return; }
    void perform("开始规划", async () => {
      const input = { raw_request: rawRequest.trim(), destination: destination.trim(), start_date: startDate, days: Number(days) };
      const fingerprint = JSON.stringify(input);
      if (pendingStart.current?.fingerprint !== fingerprint) pendingStart.current = { fingerprint, key: crypto.randomUUID() };
      const attempt = pendingStart.current;
      if (!attempt.workspaceId) attempt.workspaceId = (await createAgentV3Workspace(input)).workspace_id;
      const scheduled = await createAgentV3Run(attempt.workspaceId, attempt.key);
      remember(attempt.workspaceId, scheduled.run);
      pendingStart.current = null;
    });
  }

  async function revise() {
    const request = rawRequest.trim();
    const fingerprint = JSON.stringify([workspaceId, request]);
    if (pendingRevision.current?.fingerprint !== fingerprint) pendingRevision.current = { fingerprint, key: crypto.randomUUID() };
    const response = await createAgentV3Revision(workspaceId, request, pendingRevision.current.key);
    remember(workspaceId, response.run);
    pendingRevision.current = null;
  }

  function showDemo() {
    if (operation.current || active) return;
    setEvents([]); setEventError("");
    generation.current += 1; runRef.current = null; setRun(null); setWorkspaceId(""); setQuestions([]);  setAnswers({}); setError("");  setDemo(true); setDrawerOpen(false); setDelivery(demoDelivery);
    setDestination("成都"); setDays(String(demoDelivery.candidate?.timeline.days.length || 3)); setStartDate(demoDelivery.candidate?.timeline.days[0]?.calendar_date || "");
    setRawRequest(demoDelivery.candidate?.coverage.entries.map((entry) => entry.mention).join("、") || "");
    window.history.replaceState({}, "", window.location.pathname);
  }

  const hasItinerary = !!delivery?.candidate && !active;

  useEffect(() => {
    if (!drawerOpen) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflowY;
    document.body.style.overflowY = "hidden";
    drawerRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") { setDrawerOpen(false); return; }
      if (event.key !== "Tab") return;
      const elements = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]') || []);
      if (!elements.length) { event.preventDefault(); return; }
      const first = elements[0]; const last = elements[elements.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === drawerRef.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === drawerRef.current)) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => { document.body.style.overflowY = previousOverflow; document.removeEventListener("keydown", onKeyDown); previousFocus?.focus(); };
  }, [drawerOpen]);

  function newTrip() {
    if (operation.current || active) return;
    setEvents([]); setEventError("");
    generation.current += 1; runRef.current = null; pendingStart.current = null; pendingRevision.current = null;
    setRun(null); setWorkspaceId(""); setDelivery(null); setQuestions([]); setAnswers({}); setError(""); setDemo(false); setDrawerOpen(false);
    setDestination(""); setStartDate(""); setDays("3"); setRawRequest("");
    window.history.replaceState({}, "", window.location.pathname);
  }

  const cancel = () => void perform("取消中", async () => { if (run) applyRun((await cancelAgentV3Run(run.run_id)).run); });
  const resume = () => void perform("继续中", async () => { if (run) { applyRun((await resumeAgentV3Run(run.run_id)).run); setDelivery(null); setAnswers({}); } });
  const clarification = run?.status === "waiting_user" && questions.length ? (
    <section className="clarification-panel" aria-label="待确认信息">
      <h2 className="mb-3 font-semibold">待确认</h2>
      <div className="space-y-3">{questions.map((question) => <label key={question} className="form-label">{question}<input className="field mt-2" value={answers[question] || ""} disabled={!!busy} onChange={(event) => setAnswers((current) => ({ ...current, [question]: event.target.value }))} /></label>)}
        <button className="btn-primary w-full" disabled={!!busy || questions.some((question) => !answers[question]?.trim())} onClick={() => void perform("确认中", async () => { if (run) { const response = await answerAgentV3Clarification(run.run_id, answers); applyRun(response.run); setDelivery(null); setQuestions([]); setAnswers({}); } })}>{busy || "确认"}</button>
      </div>
    </section>
  ) : null;
  const errorFeedback = error ? <div role="alert" className={`error-feedback ${!hasItinerary || drawerOpen ? "form-error-feedback" : ""}`}>{error}{run ? <button className="ml-2 underline underline-offset-4" disabled={!!busy} onClick={() => { setError(""); setPollVersion((value) => value + 1); }}>重试</button> : null}</div> : null;
  const planningForm = (
    <section className="planning-panel">
      {workspaceId || hasItinerary ? <div className="form-heading"><span className="form-heading-icon"><Compass size={20} /></span><div><h2>{hasItinerary ? "调整你的行程" : "这次旅程"}</h2><p>{active ? "正在根据这些信息安排行程" : run?.status === "waiting_user" ? "请补充下方待确认的信息" : run?.status === "needs_resume" ? "旅行信息已保存，可继续规划" : "写下想要修改的安排"}</p></div></div> : <div className="new-trip-intro"><span className="intro-label"><Compass size={16} />从一个想去的地方开始</span><h1>下一站，<br />去哪里<span>？</span></h1><p>把收藏的地点和旅行想法交给迹旅，<br className="desktop-break" />一起安排每天的路线与节奏。</p></div>}
      <form noValidate onSubmit={(event) => { event.preventDefault(); if (!workspaceId) start(); else if (!locked && rawRequest.trim()) void perform("调整中", revise); }} className="trip-form">
        <label className="form-label">目的地<div className="input-with-icon"><MapPin size={18} /><input className="field field-with-icon" placeholder="想去哪个城市？" autoComplete="off" value={destination} disabled={locked || !!workspaceId} onChange={(event) => setDestination(event.target.value)} /></div></label>
        <div className="planning-date-row"><label className="form-label">出发日期<div className="input-with-icon"><CalendarDays size={18} /><input className="field field-with-icon" type="date" data-empty={!startDate} value={startDate} disabled={locked || !!workspaceId} onChange={(event) => setStartDate(event.target.value)} /></div></label><label className="form-label">旅行天数<div className="days-field"><input className="field" type="number" min="1" max="30" step="1" inputMode="numeric" value={days} disabled={locked || !!workspaceId} onChange={(event) => setDays(event.target.value)} /><span>天</span></div></label></div>
        <label className="form-label">{workspaceId ? "旅行资料与调整要求" : "你的旅行想法"}<textarea className="field travel-input" placeholder={"想去的景点、收藏的餐厅、已订的酒店……\n也可以直接粘贴你的旅行笔记。"} value={rawRequest} disabled={locked || run?.status === "needs_resume"} onChange={(event) => setRawRequest(event.target.value)} /></label>
        {!workspaceId ? <div className="preference-chips" aria-label="快速添加旅行偏好">{["节奏轻松一些", "多尝当地美食", "留出拍照时间"].map((preference) => <button type="button" key={preference} disabled={locked} onClick={() => setRawRequest((current) => current.includes(preference) ? current : `${current.trim()}${current.trim() ? "\n" : ""}${preference}`)} className="preference-chip" aria-pressed={rawRequest.includes(preference)}>{rawRequest.includes(preference) ? <Check size={12} /> : <Plus size={12} />}{preference}</button>)}</div> : null}
        {errorFeedback}
        {!workspaceId ? <button type="submit" className="btn-primary plan-submit" disabled={!!busy}>{busy ? <LoaderCircle size={18} className="spin" /> : <Sparkles size={18} />}<span>{busy ? busy : "开始规划我的旅程"}</span>{!busy ? <ArrowRight size={18} className="button-arrow" /> : null}</button> : active ? <p className="submitted-note"><Check size={14} />旅行信息已提交</p> : run?.status === "needs_resume" ? <button type="button" className="btn-primary w-full" disabled={!!busy} onClick={resume}>{busy || "继续规划"}<ArrowRight size={16} className="button-arrow" /></button> : run?.status !== "waiting_user" ? <button type="submit" className="btn-primary w-full" disabled={!!busy || !rawRequest.trim()}>{busy || "更新行程"}<ArrowRight size={16} className="button-arrow" /></button> : null}
      </form>
      {!workspaceId && !hasItinerary ? <button type="button" className="sample-link" disabled={!!busy} onClick={showDemo}>先看看成都 {demoDelivery.candidate?.timeline.days.length || 3} 日行程<ArrowRight size={14} /></button> : null}
    </section>
  );

  return (
    <main className="travel-workspace">
      <div inert={drawerOpen}>
        <header className="travel-header">
          <a href="/" className="travel-brand" aria-label="Trajecta 迹旅首页"><Route size={22} strokeWidth={1.8} /><span>Trajecta<span className="brand-chinese">迹旅</span></span></a>
          <nav className="header-actions" aria-label="行程操作">
            {active ? <><span role="status" className="planning-status">规划中</span><button className="btn-secondary" disabled={!!busy} onClick={cancel}>取消</button></> : null}
            {run?.status === "needs_resume" ? <button className="btn-primary" disabled={!!busy} onClick={resume}>继续</button> : null}
            {hasItinerary ? <button className="btn-secondary" disabled={!!busy} onClick={() => setDrawerOpen(true)}>调整行程</button> : !run ? <button className="btn-secondary" disabled={!!busy} onClick={showDemo}>体验示例<ArrowRight size={15} className="button-arrow" /></button> : null}
            {hasItinerary || workspaceId ? <button className="btn-primary" disabled={!!busy || active || run?.status === "waiting_user"} onClick={newTrip}><Plus size={16} />新行程</button> : null}
          </nav>
        </header>
        {hasItinerary && clarification ? <div className="clarification-floating">{clarification}</div> : null}
        <div className={`travel-columns ${hasItinerary ? "has-itinerary" : run ? "is-planning" : "is-start"}`}>
          {!hasItinerary ? <aside className="planning-column">{planningForm}{clarification}</aside> : null}
          <section className="travel-result" aria-label="行程">
            {hasItinerary && delivery ? <DeliveryPanel delivery={delivery} destination={demo ? "成都" : destination} demo={demo} /> : run ? <AgentProgress run={run} events={events} eventError={eventError} busy={!!busy} onCancel={cancel} onResume={resume} onRetry={() => setPollVersion((value) => value + 1)} /> : <JourneyPreview destination={destination} startDate={startDate} days={days} />}
          </section>
        </div>
      </div>
      {drawerOpen ? <div className="drawer-overlay" onClick={() => setDrawerOpen(false)}><section ref={drawerRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="调整行程" className="planning-drawer" onClick={(event) => event.stopPropagation()}><button className="drawer-close" aria-label="关闭" onClick={() => setDrawerOpen(false)}><X size={20} /></button>{planningForm}</section></div> : null}
    </main>
  );
}


function JourneyPreview({ destination, startDate, days }: { destination: string; startDate: string; days: string }) {
  return <div className="journey-preview">
    <div className="preview-copy"><h2>{destination.trim() ? `一起去${destination.trim()}` : "下一站，等你出发"}</h2><p>地点、路线、每日安排，都在一张行程里。</p></div>
    <svg className="journey-illustration" viewBox="0 0 520 370" fill="none" aria-hidden="true"><path d="M-30 175C68 123 98 175 170 104S339 78 389 136s133 8 171-49" stroke="#e8d8cc" strokeWidth="34"></path><path d="M-30 175C68 123 98 175 170 104S339 78 389 136s133 8 171-49" stroke="#fff9f3" strokeWidth="24"></path><path d="M67 320c38-72 13-139 78-163s110 84 177 28 101-1 153-49" stroke="#ef6a4b" strokeWidth="3" strokeLinecap="round" strokeDasharray="7 8"></path><path d="m74 112 32-54 32 54H74Z" fill="#c9d6c6"></path><path d="m119 119 25-42 25 42h-50Z" fill="#dbe3d5"></path><path d="M92 112v23m51-16v22" stroke="#8ca08b" strokeWidth="4" strokeLinecap="round"></path><path d="m347 283 34-61 34 61h-68Z" fill="#c9d6c6"></path><path d="m404 297 25-45 25 45h-50Z" fill="#dbe3d5"></path><path d="M380 283v23m49-9v16" stroke="#8ca08b" strokeWidth="4" strokeLinecap="round"></path><rect x="211" y="85" width="70" height="64" rx="5" fill="#ead0b5"></rect><path d="m202 88 44-33 44 33h-88Z" fill="#c47759"></path><path d="M236 149v-30h19v30" fill="#fff9f3"></path><rect x="221" y="98" width="13" height="13" rx="2" fill="#fff9f3"></rect><rect x="258" y="98" width="13" height="13" rx="2" fill="#fff9f3"></rect><ellipse cx="134" cy="271" rx="40" ry="9" fill="#dccbbd" opacity=".45"></ellipse><path d="M113 254v-38c0-11 10-20 21-20s21 9 21 20v38" fill="#faf8f2" stroke="#bfa88e" strokeWidth="2"></path><rect x="103" y="229" width="62" height="31" rx="7" fill="#c47759"></rect><path d="M125 204v56m18-56v56" stroke="#e5bd9e" strokeWidth="3"></path><circle cx="324" cy="182" r="21" fill="#fdfcf8"></circle><circle cx="324" cy="182" r="12" fill="#ef6a4b"></circle><circle cx="324" cy="182" r="4" fill="#fdfcf8"></circle><path d="M439 119c0-13 10-23 23-23s23 10 23 23c0 17-23 36-23 36s-23-19-23-36Z" fill="#ef6a4b"></path><circle cx="462" cy="119" r="8" fill="#fff9f3"></circle><path d="m64 319-11-10m11 10 13-9" stroke="#ef6a4b" strokeWidth="3" strokeLinecap="round"></path><path d="m338 51 4-10 4 10 10 4-10 4-4 10-4-10-10-4 10-4Z" fill="#d4ad89"></path></svg>
    <div className="trip-preview-summary"><span className="preview-icon"><MapPin size={20} /></span><div><span className="preview-summary-label">你的下一站</span><strong>{destination.trim() || "等你选一个目的地"}</strong><p>{startDate ? startDate.replaceAll("-", ".") : "出发日期待定"} · {/^[0-9]+$/.test(days) && Number(days) >= 1 && Number(days) <= 30 ? `${days} 天的旅行` : "旅行天数待定"}</p></div><Compass size={28} className="preview-compass" /></div>
    <div className="preview-caption"><Route size={15} /><span>从旅行想法到每天的路线</span></div>
  </div>;
}
