import type { DayRoute } from "@/lib/types";
import { dayIntensityLabel, outingTimeMinutes, transportTimeMinutes } from "@/lib/itinerary-metrics";
import { DayTimeline } from "./DayTimeline";
import { POICard } from "./POICard";

export function DayRouteCard({ day }: { day: DayRoute }) {
  const playMinutes = day.items.reduce((sum, item) => sum + (item.duration_min || 0), 0);
  const transferMinutes = transportTimeMinutes(day);
  const outingMinutes = outingTimeMinutes(day);
  const intensity = dayIntensityLabel(outingMinutes);

  return (
    <section className="panel">
      <div className="mb-5">
        <div className="text-sm font-medium text-muted">第 {day.day} 天</div>
        <h3 className="mt-1 text-2xl font-semibold tracking-[-0.02em] text-ink">{day.theme}</h3>
        <p className="subtle mt-2">{day.summary}</p>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="metric">
            <div className="text-xs text-muted">地点</div>
            <div className="mt-1 text-lg font-semibold">{day.items.length}</div>
          </div>
          <div className="metric">
            <div className="text-xs text-muted">外出</div>
            <div className="mt-1 text-lg font-semibold">{Math.round(outingMinutes / 60 * 10) / 10}h</div>
          </div>
          <div className="metric">
            <div className="text-xs text-muted">路上</div>
            <div className="mt-1 text-lg font-semibold">{Math.round(transferMinutes / 60 * 10) / 10}h</div>
          </div>
          <div className="metric">
            <div className="text-xs text-muted">节奏</div>
            <div className="mt-1 text-lg font-semibold">{intensity}</div>
          </div>
        </div>
      </div>
      <DayTimeline day={day} />
      <div className="space-y-3">
        {day.items.map((item, index) => (
          <POICard key={`${item.poi_id}-${index}`} item={item} />
        ))}
      </div>
    </section>
  );
}
