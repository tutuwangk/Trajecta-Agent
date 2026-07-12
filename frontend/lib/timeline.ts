import type { DayRoute, ItineraryItem } from "./types";
import { cleanUserFacingText } from "./displayText.ts";

export type TimelineEntry =
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
    }
  | {
      kind: "hotel_rest";
      time: string;
      title: string;
      detail: string;
    };

export function buildTimelineEntries(day: DayRoute): TimelineEntry[] {
  if (!day.items.length) return [];
  if (day.segments?.length) return buildSegmentedTimelineEntries(day);
  return buildLinearTimelineEntries(day);
}

function buildLinearTimelineEntries(day: DayRoute): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  const meals = sortedMeals(day);
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
    const arrival = roundedArrival(item) ?? currentTime;
    const stayMinutes = displayStayMinutes(item.duration_min);
    const departureTime = arrival === null ? null : arrival + stayMinutes;

    entries.push({
      kind: "place",
      time: arrival === null ? item.time_block || "待定" : formatTime(arrival),
      title: timelineMealTitle(item),
      detail: timelinePlaceDetail(item, stayMinutes),
      leaveTime: departureTime === null ? undefined : formatTime(departureTime),
    });
    addIncludedMeals(entries, meals, usedMeals, item);

    const nextItem = day.items[index + 1];
    const nextArrival = nextItem ? roundedArrival(nextItem) : null;
    const afterMeals = addExternalMeals(entries, meals, usedMeals, departureTime, nextArrival);

    if (nextItem) {
      const transferMinutes = item.transport_to_next?.duration_min;
      let transferStart = afterMeals;
      if (transferMinutes && nextArrival !== null) {
        transferStart = nextArrival - displayTransferMinutes(transferMinutes);
      }
      if (transferMinutes) {
        const displayMinutes = displayTransferMinutes(transferMinutes);
        entries.push({
          kind: "transfer",
          time: transferStart === null ? "路上" : formatTime(transferStart),
          title: `前往 ${nextItem.name}`,
          detail: `交通预留约 ${displayMinutes} 分钟。`,
        });
      }
      currentTime = nextArrival ?? (transferStart === null || !transferMinutes ? afterMeals : transferStart + displayTransferMinutes(transferMinutes));
      return;
    }

    currentTime = afterMeals;
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

function buildSegmentedTimelineEntries(day: DayRoute): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  const segments = day.segments || [];
  const meals = sortedMeals(day);
  const usedMeals = new Set<number>();
  const itemsById = new Map(day.items.map((item) => [item.poi_id, item]));
  const nextItemById = new Map(day.items.slice(0, -1).map((item, index) => [item.poi_id, day.items[index + 1]]));
  const hotelBreakByAfter = new Map((day.hotel_rest_breaks || []).map((hotelBreak) => [hotelBreak.after_poi_id, hotelBreak]));
  const firstOuting = segments.find((segment) => segment.kind === "outing");
  const firstPoi = firstOuting?.kind === "outing" ? itemsById.get(firstOuting.poi_ids[0] || "") : undefined;
  const firstArrival = firstPoi ? roundedArrival(firstPoi) : null;
  const departureTransport = firstNumber(day.hotel_departure_transport_min, day.hotel_to_first_transport_min);
  let currentTime = firstArrival;

  if (firstArrival !== null && departureTransport !== null) {
    entries.push({
      kind: "start",
      time: formatTime(firstArrival - displayTransferMinutes(departureTransport)),
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

  segments.forEach((segment) => {
    if (segment.kind !== "outing") return;
    const poiIds = segment.poi_ids || [];
    poiIds.forEach((poiId, index) => {
      const item = itemsById.get(poiId);
      if (!item) return;
      const arrival = roundedArrival(item) ?? currentTime;
      const stayMinutes = displayStayMinutes(item.duration_min);
      const departureTime = arrival === null ? null : arrival + stayMinutes;

      entries.push({
        kind: "place",
        time: arrival === null ? item.time_block || "待定" : formatTime(arrival),
        title: timelineMealTitle(item),
        detail: timelinePlaceDetail(item, stayMinutes),
        leaveTime: departureTime === null ? undefined : formatTime(departureTime),
      });
      addIncludedMeals(entries, meals, usedMeals, item);

      const hotelBreak = hotelBreakByAfter.get(item.poi_id);
      const nextItem = index < poiIds.length - 1
        ? itemsById.get(poiIds[index + 1])
        : hotelBreak
          ? undefined
          : nextItemById.get(item.poi_id);
      const nextArrival = nextItem ? roundedArrival(nextItem) : null;
      const afterMeals = addExternalMeals(entries, meals, usedMeals, departureTime, nextArrival);

      if (nextItem) {
        const transferMinutes = item.transport_to_next?.duration_min;
        let transferStart = afterMeals;
        if (transferMinutes && nextArrival !== null) {
          transferStart = nextArrival - displayTransferMinutes(transferMinutes);
        }
        if (transferMinutes) {
          const displayMinutes = displayTransferMinutes(transferMinutes);
          entries.push({
            kind: "transfer",
            time: transferStart === null ? "路上" : formatTime(transferStart),
            title: `前往 ${nextItem.name}`,
            detail: `交通预留约 ${displayMinutes} 分钟。`,
          });
        }
        currentTime = nextArrival ?? (transferStart === null || !transferMinutes ? afterMeals : transferStart + displayTransferMinutes(transferMinutes));
        return;
      }

      currentTime = afterMeals;
      if (!hotelBreak) return;
      const returnTransport = displayTransferMinutes(hotelBreak.return_to_hotel_transport_min || 0);
      const hotelArrival = parseClockTime(hotelBreak.hotel_arrival_time) ?? (currentTime === null ? null : currentTime + returnTransport);
      entries.push({
        kind: "transfer",
        time: currentTime === null ? "返程" : formatTime(currentTime),
        title: "回酒店休息",
        detail: `回酒店预留约 ${returnTransport} 分钟。`,
      });
      entries.push({
        kind: "hotel_rest",
        time: hotelArrival === null ? "休息" : formatTime(hotelArrival),
        title: "酒店休息",
        detail: hotelRestDetail(hotelBreak),
      });
      currentTime = parseClockTime(hotelBreak.next_departure_time) ?? parseClockTime(hotelBreak.rest_end_time) ?? hotelArrival;
    });
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

function sortedMeals(day: DayRoute) {
  return [...(day.meal_breaks || [])].sort((a, b) => (parseClockTime(a.start_time) ?? 9999) - (parseClockTime(b.start_time) ?? 9999));
}

function addIncludedMeals(
  entries: TimelineEntry[],
  meals: NonNullable<DayRoute["meal_breaks"]>,
  usedMeals: Set<number>,
  item: ItineraryItem,
) {
  const itemStart = roundedArrival(item);
  const itemEnd = itemStart === null ? null : itemStart + displayStayMinutes(item.duration_min);
  meals.forEach((meal, index) => {
    const start = parseClockTime(meal.start_time);
    const explicitlyInside = meal.included_in_item_duration && meal.within_poi_id === item.poi_id;
    const overlapsVisit = !meal.included_in_item_duration
      && start !== null
      && itemStart !== null
      && itemEnd !== null
      && itemStart <= start
      && start < itemEnd;
    if (usedMeals.has(index) || (!explicitlyInside && !overlapsVisit)) return;
    entries.push({
      kind: "break",
      time: start === null ? nearbyMealTitle(meal) : formatTime(start),
      title: nearbyMealTitle(meal),
      detail: `${explicitlyInside ? "在此附近" : "就近"}预留约 ${displayStayMinutes(meal.duration_min || meal.duration_minutes || 60)} 分钟。`,
    });
    usedMeals.add(index);
  });
}

function addExternalMeals(
  entries: TimelineEntry[],
  meals: NonNullable<DayRoute["meal_breaks"]>,
  usedMeals: Set<number>,
  currentTime: number | null,
  nextAnchor: number | null,
) {
  let nextTime = currentTime;
  meals.forEach((meal, index) => {
    if (usedMeals.has(index) || meal.included_in_item_duration) return;
    const start = parseClockTime(meal.start_time);
    if (nextTime === null || start === null) return;
    if (start < nextTime) return;
    if (nextAnchor !== null && start >= nextAnchor) return;
    const duration = displayStayMinutes(meal.duration_min || meal.duration_minutes || 60);
    entries.push({
      kind: "break",
      time: formatTime(start),
      title: nearbyMealTitle(meal),
      detail: `就近预留约 ${duration} 分钟。`,
    });
    nextTime = start + duration;
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

function timelineMealTitle(item: ItineraryItem) {
  const roles = item.meal_roles || [];
  if (!roles.length) return item.name;
  return `${roles.map(slotLabel).join(" / ")}：${item.name}`;
}

function timelinePlaceDetail(item: ItineraryItem, stayMinutes: number) {
  const roles = item.meal_roles || [];
  if (!roles.length) return `停留约 ${stayMinutes} 分钟。`;
  return `这里安排${roles.map(slotLabel).join("和")}，停留约 ${stayMinutes} 分钟。`;
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

function slotLabel(slot: "breakfast" | "lunch" | "dinner") {
  if (slot === "breakfast") return "早餐";
  if (slot === "dinner") return "晚餐";
  return "午餐";
}

function nearbyMealTitle(
  meal: NonNullable<DayRoute["meal_breaks"]>[number],
) {
  const slot = meal.slot;
  if (slot === "breakfast" || slot === "lunch" || slot === "dinner") {
    return `${slotLabel(slot)}：就近用餐`;
  }
  return meal.label || "就近用餐";
}

function hotelRestDetail(hotelBreak: NonNullable<DayRoute["hotel_rest_breaks"]>[number]) {
  const parts: string[] = [];
  const reason = cleanUserFacingText(hotelBreak.reason);
  if (reason) parts.push(reason);
  if (typeof hotelBreak.duration_min === "number") parts.push(`休息约 ${displayStayMinutes(hotelBreak.duration_min)} 分钟`);
  if (hotelBreak.next_departure_time) parts.push(`预计 ${hotelBreak.next_departure_time} 再次出发`);
  return parts.join("，") || "回酒店短暂休息后再出发。";
}
