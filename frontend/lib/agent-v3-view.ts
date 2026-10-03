import type {
  AgentV3Delivery,
  AgentV3TimelineLeg,
  AgentV3TimelineStop,
} from "./agent-v3-types";

export function deliveryHeading(delivery: AgentV3Delivery): string {
  if (delivery.release) return "已核验发布方案";
  if (delivery.delivery_state === "publishable") return "行程已准备好";
  if (delivery.delivery_state === "review_required") return "方案待调整";
  if (delivery.delivery_state === "blocked") return "行程预览 · 仍需完善";
  if (delivery.delivery_state === "working") return "正在形成可执行方案";
  return delivery.run_status === "cancelled" ? "本次运行已取消，未交付" : "本次运行未形成交付";
}

export function coverageStatusLabel(status: string): string {
  if (status === "scheduled") return "已安排";
  if (status === "pending_confirmation") return "待确认";
  if (status === "not_scheduled" || status === "excluded") return "不安排";
  return "未处置";
}

export function groundingStatusLabel(status: string): string {
  if (status === "selected") return "已选定";
  if (status === "needs_confirmation") return "待确认";
  return "未匹配";
}

export function runStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    created: "已受理",
    active: "运行中",
    waiting_user: "等待用户确认",
    needs_resume: "已暂停，可继续",
    succeeded: "行程已发布",
    failed: "运行失败，未发布",
    cancelled: "已取消，未发布",
  };
  return labels[status] || status;
}

export function factStatusLabel(status: string): string {
  if (status === "verified") return "已核验";
  if (status === "degraded") return "存在事实缺口";
  if (status === "failed") return "事实核验失败";
  return status;
}

export function experienceStatusLabel(status: string): string {
  if (status === "good") return "合理";
  if (status === "needs_adjustment") return "需要调整";
  if (status === "conflict") return "存在冲突";
  return status;
}

export function providerBlockerMessage(reasonCode: unknown): string {
  const labels: Record<string, string> = {
    provider_balance_insufficient: "模型账户余额不足；补充余额后可从当前检查点继续。",
    provider_quota_exhausted: "地点服务今日额度已用完，额度恢复后可继续。",
    provider_configuration_required: "旅行服务配置尚未完成，配置后可继续。",
    provider_authentication_failed: "模型服务认证未通过，更新服务配置后可继续。",
  };
  return typeof reasonCode === "string" && labels[reasonCode] || "外部服务暂不可用，可稍后从当前检查点继续。";
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
