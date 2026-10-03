import type { AgentV3Delivery, AgentV3TimelineStop, AgentV3TimelineLeg } from "./agent-v3-types";

// Offline interface fixture. Coordinates and timings are illustrative, never verified travel facts.
function stop(id: string, name: string, kind: string, day: number, arrival: string, departure: string, duration: number, longitude: number, latitude: number): AgentV3TimelineStop {
  return { stop_id: id, candidate_id: `demo-${id}`, obligation_ids: [], name, kind,
    arrival_at: `2026-10-${9 + day}T${arrival}:00`,
    departure_at: `2026-10-${9 + day}T${departure}:00`,
    stay_duration_min: duration, location: { longitude, latitude }, coordinate_system: "gcj02", address: "成都 · 示例点位，请以实际地点信息为准" };
}
const first = [stop("demo-1", "从市中心出发", "lodging", 1, "09:00", "09:20", 20, 104.0657, 30.6595), stop("demo-2", "人民公园 · 一杯盖碗茶", "visit", 1, "09:40", "11:10", 90, 104.0559, 30.6580), stop("demo-3", "午餐 · 川味小馆", "meal", 1, "11:30", "12:30", 60, 104.0502, 30.6620), stop("demo-4", "宽窄巷子 · 慢慢走", "visit", 1, "12:50", "15:00", 130, 104.0516, 30.6690)];
const second = [stop("demo-5", "武侯祠 · 上午游览", "visit", 2, "09:00", "11:00", 120, 104.0486, 30.6460), stop("demo-6", "锦里 · 街巷与午餐", "meal", 2, "11:15", "12:45", 90, 104.0491, 30.6453), stop("demo-7", "浣花溪 · 留一段散步时间", "visit", 2, "13:15", "15:15", 120, 104.0238, 30.6600)];
function legs(stops: AgentV3TimelineStop[]): AgentV3TimelineLeg[] {
  return stops.slice(1).map((item, index) => ({ leg_id: `demo-leg-${item.stop_id}`, from_stop_id: stops[index].stop_id, to_stop_id: item.stop_id, departure_at: stops[index].departure_at, arrival_at: item.arrival_at, duration_min: index === 1 && item.stop_id === "demo-7" ? 30 : item.stop_id === "demo-6" ? 15 : 20, mode: item.stop_id === "demo-7" ? "taxi" : "walk", fact_id: "demo-unverified", fact_source: "offline_demo", fact_status: "estimated" }));
}
export const demoDelivery: AgentV3Delivery = {
  run_id: "offline-interface-demo", run_status: "succeeded", delivery_state: "review_required", release: null,
  candidate: { candidate_snapshot_id: "offline-interface-demo", producing_run_id: "offline-interface-demo", fact_status: "degraded", experience_status: "good",
    timeline: { snapshot_id: "offline-demo-timeline", days: [
      { day_number: 1, calendar_date: "2026-10-10", title: "公园茶香，走进老成都", stops: first, legs: legs(first) },
      { day_number: 2, calendar_date: "2026-10-11", title: "街巷与绿意，留一点闲暇", stops: second, legs: legs(second) },
    ] },
    coverage: { explicit_place_count: 1, disposed_place_count: 1, coverage_ratio: 1, open_obligation_ids: [], entries: [{ obligation_id: "demo-panda", mention: "大熊猫繁育研究基地", role: "visit", priority: "optional", status: "not_scheduled", rationale: "示例将时间留给市区慢游；如想看熊猫，可单独安排半天。" }] },
    grounding_decisions: [], unresolved_places: [], operational_facts: [], fact_gap_report: { fact_need_plan_id: "demo-no-facts", gaps: [] },
  },
  assessment: { candidate_snapshot_id: "offline-interface-demo", producing_run_id: "offline-interface-demo", state: "review_required", may_publish: false, issues: [{ code: "offline_demo", severity: "review", message: "这是用于体验界面的离线行程。", recommendation: "点位、交通时长、营业和预约信息尚未核验，实际出行前需重新规划。", day_numbers: [1, 2], obligation_ids: [], place_names: [] }] },
};
