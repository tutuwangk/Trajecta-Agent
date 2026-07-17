import { Info } from "lucide-react";

export function InfoTip({ label }: { label: string }) {
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-line bg-white text-muted transition hover:border-ink/30 hover:text-ink"
        aria-label={label}
      >
        <Info size={13} />
      </button>
      <span className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-64 -translate-x-1/2 rounded-2xl border border-line bg-white px-3 py-2 text-xs leading-5 text-ink shadow-[0_12px_32px_rgba(0,0,0,0.10)] group-focus-within:block group-hover:block">
        {label}
      </span>
    </span>
  );
}
