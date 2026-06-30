"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { extractPois, getSession, planTrip, reviseTrip, updatePois } from "@/lib/api";
import type { SessionData } from "@/lib/types";
import { ItineraryCard } from "@/components/ItineraryCard";
import { POIConfirmTable } from "@/components/POIConfirmTable";
import { RevisionPanel } from "@/components/RevisionPanel";

export default function TripPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const [session, setSession] = useState<SessionData | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

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

  async function handlePoiChange(decisions: Array<{ poi_id: string; decision: string; manual_name?: string }>) {
    setStatus("正在保存地点选择");
    const result = await updatePois(sessionId, decisions);
    if (!result.ok) {
      setError(result.error?.message || "保存失败");
      setStatus("");
      return;
    }
    await load();
    setStatus("");
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
    setStatus("正在重新整理地点");
    setError("");
    const result = await extractPois(sessionId);
    if (!result.ok) {
      setError(result.error?.message || "地点整理失败");
      setStatus("");
      return;
    }
    await load();
    setStatus("");
  }

  async function handleRevise(instruction: string, quick?: string) {
    setStatus("正在调整路线");
    const result = await reviseTrip(sessionId, instruction, quick);
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
                <div className="text-xs text-muted">酒店区域</div>
                <div className="mt-1 font-semibold">{session.user_profile.hotel_area || "未填写"}</div>
              </div>
              <div className="metric">
                <div className="text-xs text-muted">同行人</div>
                <div className="mt-1 font-semibold">{session.user_profile.travelers.type}</div>
              </div>
              <div className="metric">
                <div className="text-xs text-muted">地点</div>
                <div className="mt-1 font-semibold">{session.pois.length} 个</div>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary" onClick={handleExtract} disabled={Boolean(status)}>
              重新整理地点
            </button>
            <button className="btn-primary" onClick={handlePlan} disabled={Boolean(status)}>
              生成 / 更新路线
            </button>
          </div>
        </div>
        {status && <p className="mt-4 rounded-2xl bg-surface px-4 py-3 text-sm text-ink">{status}</p>}
        {error && <p className="mt-4 rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</p>}
      </section>

      <POIConfirmTable pois={session.pois} onChange={handlePoiChange} />
      <ItineraryCard itinerary={session.itinerary_state?.itinerary} />
      <RevisionPanel onRevise={handleRevise} disabled={!session.itinerary_state || Boolean(status)} />
    </main>
  );
}
