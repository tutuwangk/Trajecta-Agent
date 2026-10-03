"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Clock3, LoaderCircle, Pause, Square, Wrench, CircleAlert, ArrowDown } from "lucide-react";
import type { AgentV3Event, AgentV3Run } from "@/lib/agent-v3-types";
import { currentActivity, formatDuration, progressRows, runElapsed } from "@/lib/agent-v3-progress";

export function AgentProgress({ run, events, eventError, busy, onCancel, onResume, onRetry }: {
  run: AgentV3Run; events: AgentV3Event[]; eventError: string; busy: boolean;
  onCancel: () => void; onResume: () => void; onRetry: () => void;
}) {
  const active = run.status === "active" || run.status === "created";
  const [now, setNow] = useState(0);
  const [following, setFollowing] = useState(true);
  const stream = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const rows = progressRows(events, active);
  const activity = currentActivity(run, events);
  useEffect(() => {
    if (active) panel.current?.focus({ preventScroll: true });
  }, [run.run_id, active]);
  useEffect(() => {
    setNow(Date.now());
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  useEffect(() => {
    if (following && stream.current) stream.current.scrollTop = stream.current.scrollHeight;
  }, [events, following]);
  const StatusIcon = active ? LoaderCircle : run.status === "succeeded" ? Check : run.status === "failed" ? CircleAlert : Pause;
  return <section ref={panel} tabIndex={-1} className="agent-progress" aria-label="规划工作状态">
    <div className="progress-heading"><span className="progress-symbol"><StatusIcon size={22} className={active ? "spin" : ""} /></span><div><p className="progress-kicker">{active ? "正在为你规划" : "规划记录"}</p><h2>把想去的地方，串成旅程</h2></div></div>
    <div className="progress-current">
      <div className="progress-live-label" role="status" aria-live="polite"><span className={active ? "live-dot" : "state-dot"} />{activity}</div>
      <div className="progress-controls"><span className="progress-time"><Clock3 size={14} />累计运行 {formatDuration(runElapsed(events, active, now))}</span>{active ? <button type="button" className="btn-secondary progress-stop" disabled={busy} onClick={onCancel}><Square size={12} />{busy ? "停止中" : "停止"}</button> : run.status === "needs_resume" ? <button type="button" className="btn-primary" disabled={busy} onClick={onResume}>{busy ? "继续中" : "继续规划"}</button> : null}</div>
    </div>
    {eventError ? <div className="progress-error" role="alert">工作记录连接中断。<button type="button" onClick={onRetry}>重新连接</button></div> : null}
    <div className="progress-stream" ref={stream} onScroll={() => { const el = stream.current; if (el) setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 48); }}>
      {rows.length ? <ol className="progress-rail">{rows.map((row) => {
        const running = row.state === "running";
        const Icon = running ? LoaderCircle : row.state === "retry" ? CircleAlert : row.state === "interrupted" ? Pause : row.kind === "tool" ? Wrench : Check;
        const elapsed = running ? formatDuration(now - row.startedAt) : row.durationMs !== undefined ? row.durationMs < 1000 ? `${(row.durationMs / 1000).toFixed(2)} 秒` : formatDuration(row.durationMs) : null;
        return <li key={row.id} className={`progress-row ${running ? "is-running" : ""}`}><span className="progress-row-icon"><Icon size={14} className={running ? "spin" : ""} /></span>{row.kind === "tool" ? <details><summary><span>{row.label}{row.detail ? <small>{row.detail}</small> : null}</span><span className="progress-row-meta">{elapsed}<ChevronDown size={13} /></span></summary><div className="tool-evidence"><code>{row.toolName}</code><p>{running ? "正在执行" : row.state === "retry" ? "输入需要修正，模型将重新处理" : row.state === "interrupted" ? "执行已中断" : "执行完成"}{elapsed ? ` · ${elapsed}` : ""}</p></div></details> : <div className="progress-milestone"><span>{row.label}{row.detail ? <small>{row.detail}</small> : null}</span>{elapsed ? <span className="progress-row-meta">{elapsed}</span> : row.state === "done" ? <Check size={12} /> : <Pause size={12} />}</div>}</li>;
      })}</ol> : <p className="progress-empty">{eventError ? "连接恢复后显示工作记录。" : active ? "请求已受理，等待第一条工作记录。" : "本次运行暂无工作记录。"}</p>}
    </div>
    {!following ? <button type="button" className="progress-follow" onClick={() => setFollowing(true)}><ArrowDown size={14} />回到最新动态</button> : null}
    <div className="progress-footer"><span>{rows.filter((row) => row.kind === "tool").length} 次工具调用</span><span>{active ? "工作记录持续更新" : "工作记录已保存"}</span></div>
  </section>;
}
