import type { PlanningIntervention } from "@/lib/types";

export function PlanningInterventionCard({
  intervention,
  disabled,
  onChoose,
}: {
  intervention?: PlanningIntervention | null;
  disabled?: boolean;
  onChoose: (choiceId: string) => Promise<void>;
}) {
  if (!intervention) return null;

  return (
    <section className="panel border-amber-200 bg-amber-50/70">
      <p className="eyebrow text-amber-700">需要你取舍</p>
      <h2 className="mt-3 text-2xl font-semibold tracking-[-0.02em] text-ink">{intervention.question}</h2>
      {intervention.display_issues?.length ? (
        <div className="mt-4 space-y-2">
          {intervention.display_issues.slice(0, 3).map((issue, index) => (
            <div key={`${issue.type}-${index}`} className="rounded-2xl border border-amber-200 bg-white/80 px-4 py-3 text-sm text-ink">
              <div className="font-medium">
                {issue.day ? `第 ${issue.day} 天` : "当前路线"}
                {issue.poi_name ? ` · ${issue.poi_name}` : ""}
              </div>
              <div className="mt-1">{issue.message}</div>
              {issue.suggestion && <div className="mt-1 text-muted">取舍影响：{issue.suggestion}</div>}
            </div>
          ))}
        </div>
      ) : null}
      <div className="mt-4 grid gap-2">
        {intervention.options.map((option) => (
          <button
            key={option.id}
            className="rounded-2xl border border-amber-200 bg-white px-4 py-3 text-left transition hover:border-amber-300 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={disabled}
            onClick={() => onChoose(option.id)}
          >
            <span className="block font-semibold text-ink">{option.label}</span>
            {option.description && <span className="mt-1 block text-sm text-muted">{option.description}</span>}
          </button>
        ))}
      </div>
    </section>
  );
}
