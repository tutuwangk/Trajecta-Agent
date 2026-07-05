import type { DayRoute, ItineraryItem } from "@/lib/types";

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
    }
  | {
      kind: "break";
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

function buildTimelineEntries(day: DayRoute): TimelineEntry[] {
  if (!day.items.length) return [];

  const entries: TimelineEntry[] = [];
  const meals = [...(day.meal_breaks || [])].sort((a, b) => (parseClockTime(a.start_time) ?? 9999) - (parseClockTime(b.start_time) ?? 9999));
  const usedMeals = new Set<number>();
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
    const explicitArrival = index === 0 ? roundedArrival(item) : null;
    const arrival = explicitArrival ?? currentTime;
    const stayMinutes = displayStayMinutes(item.duration_min);
    const departureTime = arrival === null ? null : arrival + stayMinutes;
    const leaveTime = departureTime === null ? undefined : formatTime(departureTime);

    entries.push({
      kind: "place",
      time: arrival === null ? item.time_block || "待定" : formatTime(arrival),
      title: item.name,
      detail: `停留约 ${stayMinutes} 分钟。`,
      leaveTime,
    });
    addIncludedMeals(entries, meals, usedMeals, item);

    if (index < day.items.length - 1) {
      const nextItem = day.items[index + 1];
      const transferMinutes = item.transport_to_next?.duration_min;
      let nextComputedTime = addExternalMeals(entries, meals, usedMeals, departureTime);
      if (transferMinutes) {
        const displayMinutes = displayTransferMinutes(transferMinutes);
        entries.push({
          kind: "transfer",
          time: nextComputedTime === null ? "路上" : formatTime(nextComputedTime),
          title: `前往 ${nextItem.name}`,
          detail: `交通预留约 ${displayMinutes} 分钟。`,
        });
        nextComputedTime = nextComputedTime === null ? null : nextComputedTime + displayMinutes;
      }

      currentTime = nextComputedTime;
    } else {
      currentTime = addExternalMeals(entries, meals, usedMeals, departureTime);
    }
  });

  const returnTransport = firstNumber(day.hotel_return_transport_min, day.last_to_hotel_transport_min);
  if (returnTransport !== null) {
    const displayMinutes = displayTransferMinutes(returnTransport);
    const arrivalText = currentTime === null ? "" : `，预计 ${formatTime(currentTime + displayMinutes)} 回到酒店`;
    entries.push({
      kind: "transfer",
      time: currentTime === null ? "返程" : formatTime(currentTime),
      title: "返回酒店",
      detail: `交通预留约 ${displayMinutes} 分钟${arrivalText}。`,
    });
  } else {
    entries.push({
      kind: "transfer",
      time: currentTime === null ? "返程" : formatTime(currentTime),
      title: "返回酒店",
      detail: "返程时间会根据最后一站结束时间和当天交通情况调整。",
    });
  }

  return entries;
}

function addIncludedMeals(
  entries: TimelineEntry[],
  meals: NonNullable<DayRoute["meal_breaks"]>,
  usedMeals: Set<number>,
  item: ItineraryItem,
) {
  meals.forEach((meal, index) => {
    if (usedMeals.has(index) || !meal.included_in_item_duration || meal.within_poi_id !== item.poi_id) return;
    const start = parseClockTime(meal.start_time);
    entries.push({
      kind: "break",
      time: start === null ? meal.label || "用餐" : formatTime(start),
      title: meal.label || "用餐",
      detail: `在 ${item.name} 内预留约 ${displayStayMinutes(meal.duration_min || meal.duration_minutes || 60)} 分钟。`,
    });
    usedMeals.add(index);
  });
}

function addExternalMeals(
  entries: TimelineEntry[],
  meals: NonNullable<DayRoute["meal_breaks"]>,
  usedMeals: Set<number>,
  currentTime: number | null,
) {
  let nextTime = currentTime;
  meals.forEach((meal, index) => {
    if (usedMeals.has(index) || meal.included_in_item_duration) return;
    const start = parseClockTime(meal.start_time);
    if (nextTime === null || start === null || start > nextTime) return;
    const displayStart = Math.max(nextTime, start);
    const duration = displayStayMinutes(meal.duration_min || meal.duration_minutes || 60);
    entries.push({
      kind: "break",
      time: formatTime(displayStart),
      title: meal.label || "用餐和休息",
      detail: `就近预留约 ${duration} 分钟。`,
    });
    nextTime = displayStart + duration;
    usedMeals.add(index);
  });
  return nextTime;
}

function roundedArrival(item: ItineraryItem) {
  const minutes = parseClockTime(item.arrival_time);
  return minutes === null ? null : roundUpToFive(minutes);
}

function displayStayMinutes(minutes: number) {
  return roundUpToFive(minutes || 0);
}

function displayTransferMinutes(minutes: number) {
  return roundUpToFive(minutes || 0);
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
  if (kind === "break") return "bg-orange-400 ring-orange-100";
  return "bg-emerald-500 ring-emerald-100";
}

function cardClass(kind: TimelineEntry["kind"]) {
  if (kind === "start") return "border-sky-100";
  if (kind === "transfer") return "border-amber-100";
  if (kind === "break") return "border-orange-100";
  return "border-emerald-100";
}
