import type { DayRoute, ItineraryItem } from "@/lib/types";

const FLEX_BUFFER_MIN = 5;

type TimelineEntry =
  | {
      kind: "start";
      time: string;
      title: string;
      detail: string;
    }
  | {
      kind: "place";
      time: string;
      title: string;
      detail: string;
      leaveTime?: string;
    }
  | {
      kind: "transfer";
      time: string;
      title: string;
      detail: string;
    };

export function DayTimeline({ day }: { day: DayRoute }) {
  const entries = buildTimelineEntries(day);
  if (!entries.length) return null;

  return (
    <div className="mb-5 rounded-3xl border border-sky-100 bg-gradient-to-br from-sky-50/85 via-white to-emerald-50/75 p-4">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h4 className="text-base font-semibold tracking-[-0.01em] text-ink">今日时间线</h4>
          <p className="mt-1 text-xs leading-5 text-muted">时间为预估，已按 5 分钟留出余量。</p>
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

function buildTimelineEntries(day: DayRoute): TimelineEntry[] {
  if (!day.items.length) return [];

  const entries: TimelineEntry[] = [];
  const firstArrival = roundedArrival(day.items[0]);
  const departureTransport = firstNumber(day.hotel_departure_transport_min, day.hotel_to_first_transport_min);
  let currentTime = firstArrival;

  if (firstArrival !== null && departureTransport !== null) {
    const departureTime = firstArrival - displayTransferMinutes(departureTransport);
    entries.push({
      kind: "start",
      time: formatTime(departureTime),
      title: "从酒店出发",
      detail: `到第一站预留约 ${displayTransferMinutes(departureTransport)} 分钟。`,
    });
  } else {
    entries.push({
      kind: "start",
      time: "出发",
      title: "从酒店出发",
      detail: "出发时间会根据第一站到达时间和当天交通情况调整。",
    });
  }

  day.items.forEach((item, index) => {
    const explicitArrival = roundedArrival(item);
    const arrival = explicitArrival ?? currentTime;
    const stayMinutes = displayStayMinutes(item.duration_min);
    const leaveTime = arrival === null ? undefined : formatTime(arrival + stayMinutes);

    entries.push({
      kind: "place",
      time: arrival === null ? item.time_block || "待定" : formatTime(arrival),
      title: item.name,
      detail: `停留约 ${stayMinutes} 分钟。`,
      leaveTime,
    });

    const transferMinutes = item.transport_to_next?.duration_min;
    if (transferMinutes && index < day.items.length - 1) {
      const displayMinutes = displayTransferMinutes(transferMinutes);
      entries.push({
        kind: "transfer",
        time: "路上",
        title: `前往 ${day.items[index + 1].name}`,
        detail: `交通预留约 ${displayMinutes} 分钟。`,
      });
      currentTime = arrival === null ? null : arrival + stayMinutes + displayMinutes;
    } else {
      currentTime = arrival === null ? null : arrival + stayMinutes;
    }
  });

  return entries;
}

function roundedArrival(item: ItineraryItem) {
  const minutes = parseClockTime(item.arrival_time);
  return minutes === null ? null : roundUpToFive(minutes);
}

function displayStayMinutes(minutes: number) {
  return roundUpToFive(minutes || 0);
}

function displayTransferMinutes(minutes: number) {
  return roundUpToFive((minutes || 0) + FLEX_BUFFER_MIN);
}

function parseClockTime(value?: string) {
  if (!value) return null;
  const match = value.match(/(\d{1,2}):(\d{2})/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  return hours * 60 + minutes;
}

function roundUpToFive(minutes: number) {
  return Math.ceil(Math.max(minutes, 0) / 5) * 5;
}

function formatTime(totalMinutes: number) {
  const normalized = ((totalMinutes % 1440) + 1440) % 1440;
  const hours = Math.floor(normalized / 60);
  const minutes = normalized % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function firstNumber(...values: Array<number | undefined>) {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function dotClass(kind: TimelineEntry["kind"]) {
  if (kind === "start") return "bg-sky-500 ring-sky-100";
  if (kind === "transfer") return "bg-amber-400 ring-amber-100";
  return "bg-emerald-500 ring-emerald-100";
}

function cardClass(kind: TimelineEntry["kind"]) {
  if (kind === "start") return "border-sky-100";
  if (kind === "transfer") return "border-amber-100";
  return "border-emerald-100";
}
