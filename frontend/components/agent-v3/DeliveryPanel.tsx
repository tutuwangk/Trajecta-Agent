"use client";

import { useRef, useState } from "react";
import { ChevronDown, MapPin, Utensils, BedDouble, Footprints, Camera, ShoppingBag, Plane, Car, Train } from "lucide-react";
import type { AgentV3Candidate, AgentV3Delivery } from "@/lib/agent-v3-types";
import { deliveryHeading, formatClock, interleaveTimeline, reservationReminders } from "@/lib/agent-v3-view";
import { TripMap } from "./TripMap";

const modeLabels: Record<string, string> = { walk: "步行", walking: "步行", taxi: "打车", driving: "驾车", transit: "公共交通", public_transport: "公共交通", stay: "原地" };
const stopKinds = { lodging: { label: "酒店", Icon: BedDouble }, airport: { label: "机场", Icon: Plane }, visit: { label: "景点", Icon: MapPin }, meal: { label: "餐饮", Icon: Utensils }, shopping: { label: "购物", Icon: ShoppingBag }, photo: { label: "拍照", Icon: Camera } };

export function DeliveryPanel({ delivery, destination, demo = false }: { delivery: AgentV3Delivery; destination?: string; demo?: boolean }) {
  if (!delivery.candidate) return <section data-testid="agent-v3-delivery" className="px-6 py-12 text-center"><MapPin size={28} className="mx-auto text-muted" /><h2 className="mt-4 text-lg font-semibold">{deliveryHeading(delivery)}</h2></section>;
  return <Itinerary key={delivery.candidate.candidate_snapshot_id} candidate={delivery.candidate} destination={destination} demo={demo} />;
}

function Itinerary({ candidate, destination, demo }: { candidate: AgentV3Candidate; destination?: string; demo: boolean }) {
  const days = candidate.timeline.days;
  const [dayNumber, setDayNumber] = useState(days[0]?.day_number ?? 1);
  const day = days.find((item) => item.day_number === dayNumber) ?? days[0];
  const [selectedStopId, setSelectedStopId] = useState<string | null>(day?.stops[0]?.stop_id ?? null);
  const [mobileView, setMobileView] = useState("itinerary");
  const stopRows = useRef(new Map<string, HTMLLIElement>());
  const selected = day?.stops.some((stop) => stop.stop_id === selectedStopId) ? selectedStopId : day?.stops[0]?.stop_id ?? null;
  const selectStop = (id: string, scroll = false) => {
    setSelectedStopId(id);
    if (scroll) stopRows.current.get(id)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" });
  };
  return <section className="itinerary-layout" data-mobile-view={mobileView} data-testid="agent-v3-delivery">
    <div className="itinerary-mobile-toggle" role="group" aria-label="切换行程与地图">{[{ id: "itinerary", label: "行程" }, { id: "map", label: "地图" }].map((item) => <button type="button" className="itinerary-view-button" key={item.id} aria-pressed={mobileView === item.id} onClick={() => setMobileView(item.id)}>{item.label}</button>)}</div>
    <div className="itinerary-column">
      <div className="itinerary-heading">
      <header className="mb-6"><div className="flex items-center gap-2"><h2 className="text-[27px] font-semibold tracking-tight">{destination || "我的行程"}</h2>{demo && <span className="rounded border border-line px-1.5 py-0.5 text-[10px] text-muted">示例</span>}</div><p className="mt-2 text-xs text-muted">{days[0]?.calendar_date}{days.length > 1 ? ` — ${days[days.length - 1].calendar_date}` : ""} · {days.length} 天</p></header>
      <nav aria-label="选择行程日期" className="itinerary-daytabs mb-6 flex gap-2 overflow-x-auto pb-1">{days.map((item) => <button type="button" key={item.day_number} aria-pressed={day?.day_number === item.day_number} onClick={() => { setDayNumber(item.day_number); setSelectedStopId(item.stops[0]?.stop_id ?? null); }} className={`shrink-0 rounded-md px-4 py-2.5 text-sm font-medium transition-colors ${day?.day_number === item.day_number ? "bg-primary text-white" : "bg-surface text-muted hover:text-primary"}`}>第 {item.day_number} 天</button>)}</nav>
      </div>
      <div className="itinerary-scroll">
      {day ? <>{day.stops.length ? <ol>{interleaveTimeline(day.stops, day.legs).map((row) => {
        if (row.kind === "leg") {
          const mode = row.value.mode;
          const Icon = mode === "taxi" || mode === "driving" ? Car : mode === "transit" || mode === "public_transport" ? Train : Footprints;
          return <li key={row.value.leg_id} className="ml-[15px] border-l border-line py-3 pl-7"><p className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted"><Icon size={13} />{modeLabels[mode] || "交通"} · {row.value.duration_min} 分钟</p></li>;
        }
        const stop = row.value;
        const number = day.stops.findIndex((item) => item.stop_id === stop.stop_id) + 1;
        const kind = stopKinds[stop.kind as keyof typeof stopKinds] || { label: "地点", Icon: MapPin };
        return <li key={stop.stop_id} id={`trip-stop-${stop.stop_id}`} ref={(node) => { if (node) stopRows.current.set(stop.stop_id, node); else stopRows.current.delete(stop.stop_id); }}><button type="button" aria-pressed={selected === stop.stop_id} onClick={() => selectStop(stop.stop_id)} className={`trip-stop-button grid w-full grid-cols-[30px_minmax(0,1fr)_48px] items-start gap-3 rounded-lg px-2 py-3 text-left transition-colors ${selected === stop.stop_id ? "bg-[#fff0eb]" : "hover:bg-surface"}`}><span className={`flex h-[30px] w-[30px] items-center justify-center rounded-full text-xs font-semibold ${selected === stop.stop_id ? "bg-accent text-white" : "bg-primary text-white"}`}>{number}</span><span className="min-w-0"><span className="block break-words text-sm font-semibold leading-5">{stop.name}</span><span className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted"><span className="flex items-center gap-1"><kind.Icon size={12} />{kind.label}</span>{stop.stay_duration_min > 0 && <span>{stop.stay_duration_min} 分钟</span>}</span>{stop.address && !demo && <span className="mt-1.5 block truncate text-[11px] text-muted">{stop.address}</span>}</span><span className="text-right text-[11px] leading-5 tabular-nums"><span className="block">{formatClock(stop.arrival_at)}</span>{stop.departure_at !== stop.arrival_at && <span className="block text-muted">{formatClock(stop.departure_at)}</span>}</span></button></li>;
      })}</ol> : <p className="py-6 text-sm text-muted">当天暂无安排。</p>}</> : <p className="py-6 text-sm text-muted">暂无每日安排。</p>}
      <DeliveryNotes candidate={candidate} dayNumber={day?.day_number} />
      </div>
    </div>
    <div className="itinerary-map">{day ? <TripMap key={day.day_number} stops={day.stops} selectedStopId={selected} onSelect={(id) => selectStop(id, true)} dayNumber={day.day_number} fullHeight /> : <div className="flex h-full items-center justify-center text-sm text-muted">暂无地点</div>}</div>
  </section>;
}

function DeliveryNotes({ candidate, dayNumber }: { candidate: AgentV3Candidate; dayNumber?: number }) {
  const reservations = reservationReminders(candidate, dayNumber);
  if (!reservations.length) return null;
  return <details className="group mt-6 border-t border-line py-3"><summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium">预约提醒<ChevronDown size={14} className="transition-transform group-open:rotate-180" /></summary><div className="mt-3 space-y-3 text-xs leading-5 text-muted">{reservations.map((item) => <div key={item.id}><p><span className="font-medium text-primary">{item.name}：</span>{item.value}</p><p className="mt-1">{item.sources.map((source) => <a key={source.source_id} className="mr-3 underline" href={source.uri} target="_blank" rel="noreferrer">{source.title || "查看预约信息"}</a>)}</p></div>)}</div></details>;
}
