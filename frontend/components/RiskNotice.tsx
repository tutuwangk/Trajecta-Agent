export function RiskNotice({ risks, notes }: { risks?: string[]; notes?: string[] }) {
  const items = [...(risks || []), ...(notes || [])].filter(Boolean);
  if (!items.length) return null;
  return (
    <details className="panel border-amber-100 bg-amber-50/80 shadow-none">
      <summary className="cursor-pointer text-xl font-semibold tracking-[-0.02em] text-amber-950">出行提醒</summary>
      <ul className="mt-3 space-y-1 text-sm leading-6 text-amber-900">
        {items.map((item, index) => (
          <li key={`${item}-${index}`}>{item}</li>
        ))}
      </ul>
    </details>
  );
}
