import type { AgentV2Event } from "@/lib/types";

const labels: Record<string, string> = {
  run_started: "开始理解旅行目标",
  place_mentions_analyzed: "已识别地点提及",
  place_candidates_found: "已查询真实地点候选",
  place_resolved: "已完成地点消歧",
  place_resolution_changed: "已保留地点歧义或排除项",
  place_facts_acquired: "已补充地点事实",
  route_facts_acquired: "已查询路线事实",
  visit_profile_estimated: "已估算游览时长",
  draft_changed: "已更新当前草案",
  candidate_simulated: "已完成确定性时间线模拟",
  candidate_rejected: "候选未通过发布门禁，Agent 正在修正",
  narrative_degraded: "发布说明已安全降级为模板",
  clarification_requested: "需要你确认一个关键问题",
  run_resumed: "已按你的回答继续规划",
  candidate_published: "行程已通过发布门禁",
  run_cancelled: "运行已取消",
};

export function AgentProgressStream({ events }: { events: AgentV2Event[] }) {
  return (
    <section className="panel-flat">
      <h2 className="text-lg font-semibold">Agent 进度</h2>
      <div className="mt-4 space-y-3">
        {events.length === 0 ? <p className="subtle">正在创建运行…</p> : null}
        {events.map((event) => (
          <div key={event.event_id} className="flex gap-3 text-sm">
            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-ink" />
            <div>
              <p className="font-medium">{labels[event.type] || event.type}</p>
              <p className="text-xs text-muted">{new Date(event.created_at).toLocaleTimeString()}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
