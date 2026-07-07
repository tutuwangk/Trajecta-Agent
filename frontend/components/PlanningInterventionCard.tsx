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
