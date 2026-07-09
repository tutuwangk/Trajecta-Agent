import type { Itinerary } from "@/lib/types";
import { DayRouteCard } from "./DayRouteCard";
import { RiskNotice } from "./RiskNotice";

export function ItineraryCard({ itinerary }: { itinerary?: Itinerary | null }) {
  if (!itinerary) {
    return <section className="panel subtle">先识别地点，再生成路线。</section>;
  }
  return (
    <div className="space-y-4">
      {itinerary.days.map((day) => (
        <DayRouteCard key={day.day} day={day} />
      ))}
      <RiskNotice risks={itinerary.global_risks} notes={itinerary.revision_notes} />
      <PlaceDetails title="需要确认的地点" items={attentionItems(itinerary)} />
    </div>
  );
}

function PlaceDetails({ title, items }: { title: string; items: Array<{ name: string; reason?: string }> }) {
  if (!items.length) return null;
  return (
    <details className="panel">
      <summary className="cursor-pointer text-xl font-semibold tracking-[-0.02em]">{title}</summary>
      <ul className="mt-3 space-y-2 text-sm text-muted">
        {items.map((item, index) => (
          <li key={`${item.name}-${index}`} className="rounded-2xl bg-surface px-3 py-2">
            {item.name}
            {item.reason ? `：${item.reason}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

function attentionItems(itinerary: Itinerary) {
  if (itinerary.attention_places?.length) return itinerary.attention_places;
  return (itinerary.uncertain_pois || []).map((poi) => ({
    name: String(poi.standard_name || poi.raw_name || poi.name || "待确认地点"),
    reason: String(poi.decision_reason || "地点还需要确认")
  }));
}
