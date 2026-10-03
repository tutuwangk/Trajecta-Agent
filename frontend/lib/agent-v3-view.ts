import type {
  AgentV3Candidate,
  AgentV3Delivery,
  AgentV3Run,
  AgentV3TimelineLeg,
  AgentV3TimelineStop,
} from "./agent-v3-types";

export function deliveryMatchesRun(delivery: AgentV3Delivery, run: AgentV3Run): boolean {
  if (delivery.run_id !== run.run_id || delivery.run_status !== run.status) return false;
  const candidate = delivery.candidate;
  if (candidate && candidate.producing_run_id !== run.run_id) return false;
  return [delivery.assessment, delivery.release].every((snapshot) => !snapshot ||
    !!candidate && snapshot.producing_run_id === run.run_id && snapshot.candidate_snapshot_id === candidate.candidate_snapshot_id);
}

export function deliveryHeading(delivery: AgentV3Delivery): string {
  if (delivery.release || delivery.candidate) return "你的行程";
  if (delivery.delivery_state === "working") return "正在安排你的旅程";
  return delivery.run_status === "cancelled" ? "规划已停止" : "本次规划未完成";
}

export function reservationReminders(candidate: AgentV3Candidate, dayNumber?: number) {
  const names = new Map(candidate.timeline.days.filter((day) => dayNumber === undefined || day.day_number === dayNumber).flatMap((day) => day.stops.map((stop) => [stop.stop_id, stop.name] as const)));
  return candidate.operational_facts.flatMap((fact) => {
    const name = names.get(fact.stop_id);
    if (!name) return [];
    return fact.claims.flatMap((claim, index) => {
      if (claim.field !== "reservation" || !claim.value.trim()) return [];
      const sources = fact.sources.filter((source) => claim.source_ids.includes(source.source_id) && /^https?:\/\//i.test(source.uri));
      return sources.length ? [{ id: `${fact.fact_id}-${index}`, name, value: claim.value, sources }] : [];
    });
  });
}

export function formatClock(value: string): string {
  const match = value.match(/T(\d{2}:\d{2})/);
  return match?.[1] || value;
}

export type TimelineRow =
  | { kind: "stop"; value: AgentV3TimelineStop }
  | { kind: "leg"; value: AgentV3TimelineLeg };

export function interleaveTimeline(
  stops: AgentV3TimelineStop[],
  legs: AgentV3TimelineLeg[],
): TimelineRow[] {
  const rows: TimelineRow[] = [];
  stops.forEach((stop, index) => {
    rows.push({ kind: "stop", value: stop });
    const leg = legs.find((item) => item.from_stop_id === stop.stop_id && item.to_stop_id === stops[index + 1]?.stop_id);
    if (leg) rows.push({ kind: "leg", value: leg });
  });
  return rows;
}

export function hasMapLocation(stop: AgentV3TimelineStop): boolean {
  const point = stop.location;
  return !!point && Number.isFinite(point.longitude) && Number.isFinite(point.latitude)
    && Math.abs(point.longitude) <= 180 && Math.abs(point.latitude) <= 90;
}

export function projectMapStops(stops: AgentV3TimelineStop[]) {
  const located = stops.flatMap((stop, index) => hasMapLocation(stop)
    ? [{ stop, number: index + 1, longitude: stop.location!.longitude, latitude: stop.location!.latitude }] : []);
  if (!located.length) return [];
  const meanLatitude = located.reduce((sum, point) => sum + point.latitude, 0) / located.length;
  const longitudeScale = Math.max(0.05, Math.cos(meanLatitude * Math.PI / 180));
  const minX = Math.min(...located.map((point) => point.longitude * longitudeScale));
  const maxX = Math.max(...located.map((point) => point.longitude * longitudeScale));
  const minY = Math.min(...located.map((point) => point.latitude));
  const maxY = Math.max(...located.map((point) => point.latitude));
  const range = Math.max(maxX - minX, maxY - minY, 0.008);
  const scale = 340 / range;
  return located.map((point) => ({ ...point,
    x: 300 + (point.longitude * longitudeScale - (minX + maxX) / 2) * scale,
    y: 235 - (point.latitude - (minY + maxY) / 2) * scale,
  }));
}

export function mapNavigationUrl(stop: AgentV3TimelineStop): string | null {
  if (!hasMapLocation(stop)) return null;
  return `https://uri.amap.com/marker?position=${stop.location!.longitude},${stop.location!.latitude}&name=${encodeURIComponent(stop.name)}&coordinate=gaode&callnative=0`;
}
