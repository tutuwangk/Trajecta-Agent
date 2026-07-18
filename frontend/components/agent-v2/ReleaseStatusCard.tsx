import type { AgentV2Narrative, AgentV2Release } from "@/lib/types";

export function ReleaseStatusCard({
  release,
  narrative,
}: {
  release: AgentV2Release;
  narrative: AgentV2Narrative | null;
}) {
  return (
    <section className="panel-flat space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">不可变发布版本</h2>
        <div className="flex gap-2 text-xs">
          <span className="rounded-full bg-surface px-3 py-1">事实：{release.fact_status}</span>
          <span className="rounded-full bg-surface px-3 py-1">体验：{release.experience_status}</span>
        </div>
      </div>
      {narrative ? (
        <div>
          <p className="text-sm leading-6">{narrative.overview}</p>
          <div className="mt-3 space-y-2">
            {narrative.days.map((day) => (
              <div key={day.day_index} className="rounded-2xl bg-white p-4">
                <p className="font-medium">{day.theme}</p>
                <p className="mt-1 text-sm text-muted">{day.summary}</p>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="subtle">发布事实已冻结，说明文案正在生成或已降级为模板。</p>
      )}
      {release.issue_codes.length ? (
        <p className="text-xs text-muted">注意：{release.issue_codes.join("、")}</p>
      ) : null}
    </section>
  );
}
