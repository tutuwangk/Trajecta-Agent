import type { Itinerary } from "@/lib/types";
import { DayRouteCard } from "./DayRouteCard";
import { RiskNotice } from "./RiskNotice";

export function ItineraryCard({ itinerary }: { itinerary?: Itinerary | null }) {
  if (!itinerary) {
    return <section className="panel subtle">先确认地点，再生成路线。</section>;
  }
  return (
    <div className="space-y-4">
      <RiskNotice risks={itinerary.global_risks} notes={itinerary.revision_notes} />
      {itinerary.days.map((day) => (
        <DayRouteCard key={day.day} day={day} />
      ))}
      {Boolean(itinerary.uncertain_pois?.length) && (
        <section className="panel">
          <h2 className="text-xl font-semibold tracking-[-0.02em]">待确认地点</h2>
          <p className="subtle mt-1">这些地点还需要你确认，不会直接安排进路线。</p>
          <ul className="mt-3 space-y-2 text-sm text-muted">
            {itinerary.uncertain_pois.map((poi, index) => (
              <li key={index} className="rounded-2xl bg-surface px-3 py-2">
                {uncertainName(poi)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function uncertainName(poi: Record<string, unknown>) {
  return String(poi.standard_name || poi.raw_name || poi.name || "待确认地点");
}
