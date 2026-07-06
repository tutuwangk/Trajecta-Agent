"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getSession, planTrip, recognizePlaces, reviseTrip, updatePlaceOverrides } from "@/lib/api";
import type { PoiDecisionInput, SessionData } from "@/lib/types";
import { ItineraryCard } from "@/components/ItineraryCard";
import { PlacePool } from "@/components/PlacePool";
import { RevisionPanel } from "@/components/RevisionPanel";

export default function TripPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const [session, setSession] = useState<SessionData | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const hasItinerary = Boolean(session?.itinerary_state?.itinerary);
  const recognizing = status === "正在识别地点";
  const generatingRoute = status === "正在生成路线";

  async function load() {
    const result = await getSession(sessionId);
    if (result.ok && result.data) {
      setSession(result.data);
      setError("");
    } else {
      setError(result.error?.message || "读取行程失败");
    }
  }

  useEffect(() => {
    void load();
  }, [sessionId]);

  async function handlePoiChange(decisions: PoiDecisionInput[]) {
    const hadItinerary = Boolean(session?.itinerary_state?.itinerary);
    setStatus("正在保存地点选择");
    const result = await updatePlaceOverrides(sessionId, decisions);
    if (!result.ok) {
      setError(result.error?.message || "保存失败");
      setStatus("");
      return;
    }
    await load();
    setStatus(hadItinerary ? "地点已更新，请重新生成路线" : "");
  }

  async function handlePlan() {
    setStatus("正在生成路线");
    setError("");
    const result = await planTrip(sessionId);
    if (!result.ok) {
      setError(result.error?.message || "路线生成失败");
      setStatus("");
      return;
    }
    await load();
    setStatus("");
  }

  async function handleExtract() {
    const hadItinerary = Boolean(session?.itinerary_state?.itinerary);
    setStatus("正在识别地点");
    setError("");
    const result = await recognizePlaces(sessionId);
    if (!result.ok) {
      setError(result.error?.message || "地点识别失败");
      setStatus("");
      return;
    }
    await load();
    setStatus(hadItinerary ? "地点已更新，请重新生成路线" : "");
  }

  async function handleRevise(instruction: string) {
    setStatus("正在调整路线");
    setError("");
    const result = await reviseTrip(sessionId, instruction);
    if (!result.ok) {
      setError(result.error?.message || "调整失败");
      setStatus("");
      return;
    }
    await load();
    setStatus("");
  }

  if (!session) {
    return <main className="mx-auto max-w-6xl px-4 py-8 text-sm text-muted">{error || "正在读取行程"}</main>;
  }

  return (
    <main className="mx-auto min-h-[100dvh] max-w-7xl space-y-5 px-4 py-6 sm:px-6 lg:px-8">
      <section className="panel">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="eyebrow">路线工作台</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-ink sm:text-4xl">
              {session.user_profile.destination || "旅行"}路线安排
            </h1>
            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="metric">
                <div className="text-xs text-muted">天数</div>
                <div className="mt-1 font-semibold">{session.user_profile.days} 天</div>
              </div>
              <div className="metric">
                <div className="text-xs text-muted">酒店名</div>
                <div className="mt-1 font-semibold">{session.user_profile.hotel_name || session.user_profile.hotel_area || "未填写"}</div>
              </div>
              <div className="metric">
                <div className="text-xs text-muted">出行人数</div>
                <div className="mt-1 font-semibold">{session.user_profile.travelers.count || 1} 人</div>
              </div>
              <div className="metric">
                <div className="text-xs text-muted">地点</div>
                <div className="mt-1 font-semibold">{session.pois.length} 个</div>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary gap-2" onClick={handleExtract} disabled={Boolean(status)}>
              <LoadingButtonLabel loading={recognizing} label="识别地点" loadingLabel="正在识别" dark={false} />
            </button>
            <button className="btn-primary gap-2" onClick={handlePlan} disabled={Boolean(status)}>
              <LoadingButtonLabel loading={generatingRoute} label="生成路线" loadingLabel="正在生成" dark />
            </button>
          </div>
        </div>
        {status && <p className="mt-4 rounded-2xl bg-surface px-4 py-3 text-sm text-ink">{status}</p>}
        {error && <p className="mt-4 rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</p>}
      </section>

      {hasItinerary ? (
        <>
          <ItineraryCard itinerary={session.itinerary_state?.itinerary} />
          <PlacePool pois={session.pois} hotelName={session.user_profile.hotel_name || session.user_profile.hotel_area || ""} onChange={handlePoiChange} />
        </>
      ) : (
        <>
          <PlacePool pois={session.pois} hotelName={session.user_profile.hotel_name || session.user_profile.hotel_area || ""} onChange={handlePoiChange} />
          <ItineraryCard itinerary={session.itinerary_state?.itinerary} />
        </>
      )}
      <RevisionPanel
        onRevise={handleRevise}
        disabled={!session.itinerary_state || Boolean(status)}
        showSuccess={Boolean(session.revision_history?.length)}
      />
    </main>
  );
}

function LoadingButtonLabel({
  loading,
  label,
  loadingLabel,
  dark
}: {
  loading: boolean;
  label: string;
  loadingLabel: string;
  dark: boolean;
}) {
  const spinnerClass = dark ? "border-white/40 border-t-white" : "border-line border-t-ink";
  return (
    <>
      {loading && <span className={`h-4 w-4 animate-spin rounded-full border-2 ${spinnerClass}`} />}
      {loading ? loadingLabel : label}
    </>
  );
}
