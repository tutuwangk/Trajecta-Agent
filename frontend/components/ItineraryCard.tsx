import type { Itinerary } from "@/lib/types";
import { cleanUserFacingText } from "@/lib/displayText";
import { DayRouteCard } from "./DayRouteCard";
import { RiskNotice } from "./RiskNotice";

export function ItineraryCard({ itinerary }: { itinerary?: Itinerary | null }) {
  if (!itinerary) {
    return <section className="panel subtle">先识别地点，再生成路线。</section>;
  }
  const summary = itinerary.route_summary;
  return (
    <div className="space-y-4">
      <section className="panel">
        <h2 className="text-2xl font-semibold tracking-[-0.02em]">路线概览</h2>
        <p className="subtle mt-2">{cleanUserFacingText(summary?.main_message) || "已为你整理出可执行路线。"}</p>
        <div className="mt-4 grid grid-cols-3 gap-2">
          <div className="metric">
            <div className="text-xs text-muted">已安排</div>
            <div className="mt-1 text-lg font-semibold">{summary?.scheduled_places_count ?? scheduledCount(itinerary)}</div>
          </div>
          <div className="metric">
            <div className="text-xs text-muted">备选/未安排</div>
            <div className="mt-1 text-lg font-semibold">{summary?.unscheduled_places_count ?? itinerary.unscheduled_places?.length ?? 0}</div>
          </div>
          <div className="metric">
            <div className="text-xs text-muted">需确认</div>
            <div className="mt-1 text-lg font-semibold">{summary?.attention_required_count ?? attentionCount(itinerary)}</div>
          </div>
        </div>
      </section>
      <RiskNotice risks={itinerary.global_risks} notes={itinerary.revision_notes} />
      {itinerary.days.map((day) => (
        <DayRouteCard key={day.day} day={day} />
      ))}
      <PlaceDetails title="没放进路线的地点" items={itinerary.unscheduled_places || []} />
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
            {item.reason ? `：${cleanUserFacingText(item.reason)}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

function scheduledCount(itinerary: Itinerary) {
  return itinerary.days.reduce((sum, day) => sum + day.items.length, 0);
}

function attentionCount(itinerary: Itinerary) {
  return attentionItems(itinerary).length;
}

function attentionItems(itinerary: Itinerary) {
  if (itinerary.attention_places?.length) return itinerary.attention_places;
  return (itinerary.uncertain_pois || []).map((poi) => ({
    name: String(poi.standard_name || poi.raw_name || poi.name || "待确认地点"),
    reason: String(poi.decision_reason || "地点还需要确认")
  }));
}
