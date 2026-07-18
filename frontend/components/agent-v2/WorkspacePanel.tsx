import type { AgentV2Workspace } from "@/lib/types";

export function WorkspacePanel({ workspace }: { workspace: AgentV2Workspace | null }) {
  if (!workspace) return <section className="panel-flat subtle">工作区正在初始化。</section>;
  const names = new Map(workspace.place_candidates.map((item) => [item.candidate_id, item.name]));
  return (
    <section className="panel-flat space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">旅行工作区</h2>
        <span className="rounded-full bg-surface px-3 py-1 text-xs text-muted">
          v{workspace.version} · facts {workspace.fact_version}
        </span>
      </div>
      <div>
        <p className="eyebrow">地点</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {workspace.place_candidates.map((item) => (
            <span key={item.candidate_id} className="rounded-full border border-line bg-white px-3 py-1 text-sm">
              {item.name}
            </span>
          ))}
          {workspace.place_candidates.length === 0 ? <span className="subtle">Agent 尚在识别与查询。</span> : null}
        </div>
      </div>
      <div>
        <p className="eyebrow">当前草案</p>
        <div className="mt-2 space-y-3">
          {workspace.current_draft?.days.map((day) => (
            <div key={day.day_index} className="rounded-2xl border border-line bg-white p-4">
              <p className="font-medium">第 {day.day_index} 天 · {day.date}</p>
              {day.hotel_candidate_id ? (
                <p className="mt-1 text-xs text-muted">
                  酒店：{names.get(day.hotel_candidate_id) || day.hotel_candidate_id}
                  {day.return_to_hotel ? " · 当日返回" : ""}
                </p>
              ) : null}
              <ol className="mt-2 space-y-1 text-sm text-muted">
                {day.visits.map((visit) => (
                  <li key={visit.visit_id}>{names.get(visit.place_candidate_id) || visit.place_candidate_id} · {visit.duration_min} 分钟</li>
                ))}
              </ol>
              {day.meals?.length ? (
                <p className="mt-2 text-xs text-muted">
                  用餐占用：{day.meals.map((meal) => `${meal.kind} ${meal.duration_min} 分钟`).join("、")}
                </p>
              ) : null}
            </div>
          ))}
          {!workspace.current_draft ? <p className="subtle">Agent 会尽早形成可执行草案。</p> : null}
        </div>
      </div>
    </section>
  );
}
