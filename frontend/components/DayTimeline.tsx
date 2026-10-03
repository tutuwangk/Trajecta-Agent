import type { DayRoute } from "@/lib/types";
import { buildTimelineEntries, type TimelineEntry } from "@/lib/timeline";

export function DayTimeline({ day }: { day: DayRoute }) {
  const entries = buildTimelineEntries(day);
  if (!entries.length) return null;

  return (
    <div className="mb-5 rounded-3xl border border-sky-100 bg-gradient-to-br from-sky-50/85 via-white to-emerald-50/75 p-4">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h4 className="text-base font-semibold tracking-[-0.01em] text-ink">今日时间线</h4>
          <p className="mt-1 text-xs leading-5 text-muted">时间为预估，按当天路线顺序推算。</p>
        </div>
        <span className="rounded-full bg-white/80 px-3 py-1 text-xs font-medium text-sky-700">
          {day.items.length} 站
        </span>
      </div>
      <ol className="space-y-0">
        {entries.map((entry, index) => (
          <li key={`${entry.kind}-${entry.title}-${index}`} className="grid grid-cols-[3.75rem_1.25rem_minmax(0,1fr)] gap-3">
            <div className="pt-1 text-right text-sm font-semibold tabular-nums text-ink">{entry.time}</div>
            <div className="relative flex justify-center">
              {index < entries.length - 1 && <span className="absolute top-5 h-full w-px bg-sky-200" />}
              <span className={`relative mt-1 h-3 w-3 rounded-full ring-4 ${dotClass(entry.kind)}`} />
            </div>
            <div className="pb-5">
              <div className={`rounded-2xl border bg-white/80 px-3 py-2 ${cardClass(entry.kind)}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-sm font-semibold text-ink">{entry.title}</div>
                  {entry.kind === "place" && entry.leaveTime && (
                    <div className="shrink-0 text-xs font-medium text-emerald-700">{entry.leaveTime} 左右离开</div>
                  )}
                </div>
                <div className="mt-1 text-xs leading-5 text-muted">{entry.detail}</div>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function dotClass(kind: TimelineEntry["kind"]) {
  if (kind === "start") return "bg-sky-500 ring-sky-100";
  if (kind === "transfer") return "bg-amber-400 ring-amber-100";
  if (kind === "hotel_rest") return "bg-violet-400 ring-violet-100";
  if (kind === "break") return "bg-orange-400 ring-orange-100";
  return "bg-emerald-500 ring-emerald-100";
}

function cardClass(kind: TimelineEntry["kind"]) {
  if (kind === "start") return "border-sky-100";
  if (kind === "transfer") return "border-amber-100";
  if (kind === "hotel_rest") return "border-violet-100";
  if (kind === "break") return "border-orange-100";
  return "border-emerald-100";
}
