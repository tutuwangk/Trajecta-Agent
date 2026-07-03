import type { ItineraryItem } from "@/lib/types";
import { AmapLinkButton } from "./AmapLinkButton";

export function POICard({ item }: { item: ItineraryItem }) {
  return (
    <div className="rounded-3xl border border-line bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-medium text-muted">
            {item.time_block} {item.arrival_time ? `/ ${item.arrival_time}` : ""}
          </div>
          <h4 className="mt-1 text-lg font-semibold tracking-[-0.01em] text-ink">{item.name}</h4>
        </div>
        <div className="flex flex-wrap gap-2">
          <AmapLinkButton href={item.amap_link} />
          <AmapLinkButton href={item.transport_to_next?.amap_navigation_link} label="去下一站" />
        </div>
      </div>
      <p className="mt-3 text-sm leading-6 text-ink/80">停留约 {item.duration_min} 分钟。{item.reason}</p>
      {item.transport_to_next && (
        <p className="mt-3 rounded-2xl bg-surface px-3 py-2 text-xs text-muted">
          前往下一站：{transportMode(item.transport_to_next.mode)}
          {item.transport_to_next.duration_min ? `约 ${item.transport_to_next.duration_min} 分钟` : ""}
          {item.transport_to_next.distance_m ? `，${item.transport_to_next.distance_m} 米` : ""}
        </p>
      )}
      {Boolean(item.risk_notes?.length) && (
        <div className="mt-3 rounded-2xl bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">提醒：{item.risk_notes?.join("；")}</div>
      )}
    </div>
  );
}

function transportMode(mode: string) {
  return { walking: "步行", driving: "打车/驾车", transit: "公交" }[mode] || mode;
}
