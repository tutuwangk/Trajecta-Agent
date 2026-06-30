import type { DayRoute } from "@/lib/types";
import { POICard } from "./POICard";

export function DayRouteCard({ day }: { day: DayRoute }) {
  const playMinutes = day.items.reduce((sum, item) => sum + (item.duration_min || 0), 0);
  const transferMinutes = day.items.reduce((sum, item) => sum + (item.transport_to_next?.duration_min || 0), 0);
  const outingMinutes = outingTimeMinutes(day);
  const intensity = outingMinutes <= 300 ? "躺平" : outingMinutes <= 540 ? "常规" : "特种兵";

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
      <div className="space-y-3">
        {day.items.map((item, index) => (
          <POICard key={`${item.poi_id}-${index}`} item={item} />
        ))}
      </div>
      {Boolean(day.removed_pois?.length) && (
        <div className="mt-4 rounded-3xl border border-line bg-surface p-4 text-sm">
          <div className="font-semibold text-ink">未安排地点</div>
          <ul className="mt-2 space-y-1 text-muted">
            {day.removed_pois?.map((poi) => (
              <li key={poi.name}>{poi.name}：{poi.reason}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function outingTimeMinutes(day: DayRoute) {
  const explicit = day.total_outing_min ?? day.total_outing_minutes ?? day.outing_duration_min ?? day.outing_duration_minutes;
  if (explicit) return explicit;
  const itemMinutes = day.items.reduce((sum, item) => sum + (item.duration_min || 0) + (item.transport_to_next?.duration_min || 0), 0);
  const hotelTransport =
    (day.hotel_departure_transport_min || day.hotel_to_first_transport_min || 0) +
    (day.hotel_return_transport_min || day.last_to_hotel_transport_min || 0);
  const mealMinutes = (day.meal_breaks || []).reduce((sum, meal) => sum + (meal.duration_min || meal.duration_minutes || 0), 0);
  return itemMinutes + hotelTransport + mealMinutes;
}
