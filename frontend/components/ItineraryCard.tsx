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
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-2xl font-semibold tracking-[-0.02em]">路线概览</h2>
          <div className="flex flex-wrap gap-2">
            <ResultBadge status={itinerary.fact_status || itinerary.result_status} />
            <ExperienceBadge status={itinerary.experience_status || itinerary.release_decision?.experience_status} />
          </div>
        </div>
        <p className="subtle mt-2">{cleanUserFacingText(summary?.main_message) || "已为你整理出可执行路线。"}</p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <div className="metric">
            <div className="text-xs text-muted">已安排</div>
            <div className="mt-1 text-lg font-semibold">{summary?.scheduled_places_count ?? scheduledCount(itinerary)}</div>
          </div>
          <div className="metric">
            <div className="text-xs text-muted">备选/未安排</div>
            <div className="mt-1 text-lg font-semibold">{summary?.unscheduled_places_count ?? itinerary.unscheduled_places?.length ?? 0}</div>
          </div>
        </div>
      </section>
      {itinerary.result_status === "degraded" && itinerary.release_decision?.degradation_reasons?.length ? (
        <section className="panel border border-amber-200 bg-amber-50/70">
          <h3 className="font-semibold text-amber-900">部分事实需要复核</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900">
            {itinerary.release_decision.degradation_reasons.map((reason) => <li key={reason}>{cleanUserFacingText(reason)}</li>)}
          </ul>
          <p className="mt-2 text-sm text-amber-800">出发前请复核标记的交通、地点或营业信息。</p>
        </section>
      ) : null}
      {itinerary.release_decision?.experience_status && itinerary.release_decision.experience_status !== "good" && itinerary.release_decision.experience_reasons?.length ? (
        <section className="panel border border-orange-200 bg-orange-50/70">
          <h3 className="font-semibold text-orange-900">路线已生成，部分偏好仍需调整</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-orange-900">
            {itinerary.release_decision.experience_reasons.map((reason) => <li key={reason}>{cleanUserFacingText(reason)}</li>)}
          </ul>
        </section>
      ) : null}
      <RiskNotice risks={itinerary.global_risks} />
      {itinerary.days.map((day) => (
        <DayRouteCard key={day.day} day={day} />
      ))}
      <PlaceDetails title="没放进路线的地点" items={itinerary.unscheduled_places || []} />
    </div>
  );
}

function ResultBadge({ status }: { status?: Itinerary["result_status"] }) {
  if (!status) return null;
  const text = status === "verified" ? "事实已核验" : status === "degraded" ? "部分事实待复核" : "事实未通过";
  const style = status === "verified" ? "bg-emerald-50 text-emerald-700" : status === "degraded" ? "bg-amber-50 text-amber-700" : "bg-red-50 text-red-700";
  return <span className={`rounded-full px-3 py-1 text-xs font-medium ${style}`}>{text}</span>;
}

function ExperienceBadge({ status }: { status?: "good" | "needs_adjustment" | "conflict" }) {
  if (!status || status === "good") return null;
  const text = status === "conflict" ? "偏好存在取舍" : "体验可再优化";
  return <span className="rounded-full bg-orange-50 px-3 py-1 text-xs font-medium text-orange-700">{text}</span>;
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
