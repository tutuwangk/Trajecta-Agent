"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { getSession, planTrip, recognizePlaces, reviseTrip, submitPlanningDecision, updatePlaceOverrides } from "@/lib/api";
import { planningControlsDisabled, resolvePlanningFlow } from "@/lib/planning-flow";
import { waitForPlanningCompletion } from "@/lib/planning-recovery";
import type { PlanningBlocker, PlanningIntervention, PoiDecisionInput, SessionData } from "@/lib/types";
import { ItineraryCard } from "@/components/ItineraryCard";
import { PlanningInterventionCard } from "@/components/PlanningInterventionCard";
import { PlacePool } from "@/components/PlacePool";
import { RevisionPanel } from "@/components/RevisionPanel";

export default function TripPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const [session, setSession] = useState<SessionData | null>(null);
  const [planningIntervention, setPlanningIntervention] = useState<PlanningIntervention | null>(null);
  const [busyMessage, setBusyMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const planningRecoveryAbortRef = useRef<AbortController | null>(null);
  const hasItinerary = Boolean(session?.itinerary_state?.itinerary);
  const isBusy = planningControlsDisabled(busyMessage);
  const recognizing = busyMessage === "正在识别地点";
  const generatingRoute = busyMessage === "正在生成路线" || busyMessage === "路线仍在生成";

  async function load() {
    const result = await getSession(sessionId);
    if (result.ok && result.data) {
      setSession(result.data);
      setPlanningIntervention(result.data.planning_intervention || null);
      setError("");
    } else {
      setError(result.error?.message || "读取行程失败");
    }
  }

  useEffect(() => {
    void load();
    return () => {
      planningRecoveryAbortRef.current?.abort();
      planningRecoveryAbortRef.current = null;
    };
  }, [sessionId]);

  async function handlePoiChange(decisions: PoiDecisionInput[]) {
    const hadItinerary = Boolean(session?.itinerary_state?.itinerary);
    setBusyMessage("正在保存地点选择");
    const result = await updatePlaceOverrides(sessionId, decisions);
    if (!result.ok) {
      setError(result.error?.message || "保存失败");
      setBusyMessage("");
      return;
    }
    await load();
    setBusyMessage("");
    setNotice(hadItinerary ? "地点已更新，请重新生成路线" : "");
  }

  async function handlePlan() {
    const previousRunId = session?.latest_planning_run?.id || null;
    planningRecoveryAbortRef.current?.abort();
    const recoveryController = new AbortController();
    planningRecoveryAbortRef.current = recoveryController;
    setBusyMessage("正在生成路线");
    setNotice("");
    setError("");
    const result = await planTrip(sessionId);
    if (!result.ok && result.error?.code === "network_error") {
      setBusyMessage("路线仍在生成");
      setNotice("连接已中断，但路线可能仍在后台生成，正在自动等待结果。请勿重复点击。");
      const recovery = await waitForPlanningCompletion(sessionId, getSession, recoveryController.signal, { previousRunId });
      if (planningRecoveryAbortRef.current !== recoveryController || recovery.kind === "cancelled") return;
      planningRecoveryAbortRef.current = null;
      if (recovery.kind === "completed") {
        setSession(recovery.session);
        setPlanningIntervention(recovery.session.planning_intervention || null);
        setNotice("路线已生成");
        setError("");
      } else {
        setError(recovery.message);
        setNotice("");
      }
      setBusyMessage("");
      return;
    }
    planningRecoveryAbortRef.current = null;
    const outcome = resolvePlanningFlow(result, "路线生成失败");
    if (outcome.kind === "failed") {
      setError(formatPlanningError(outcome.message, outcome.blockers));
      setBusyMessage("");
      return;
    }
    if (outcome.kind === "needs_user_choice") {
      setPlanningIntervention(outcome.result.planning_intervention);
      setBusyMessage("");
      return;
    }
    setPlanningIntervention(null);
    await load();
    setBusyMessage("");
  }

  async function handlePlanningChoice(choiceId: string) {
    if (!planningIntervention) return;
    setBusyMessage("正在按你的选择调整路线");
    setNotice("");
    setError("");
    const outcome = resolvePlanningFlow(
      await submitPlanningDecision(sessionId, planningIntervention.id, choiceId),
      "保存选择失败"
    );
    if (outcome.kind === "failed") {
      setError(formatPlanningError(outcome.message, outcome.blockers));
      setBusyMessage("");
      return;
    }
    if (outcome.kind === "needs_user_choice") {
      setPlanningIntervention(outcome.result.planning_intervention);
      setBusyMessage("");
      return;
    }
    setPlanningIntervention(null);
    await load();
    setBusyMessage("");
  }

  async function handleExtract() {
    const hadItinerary = Boolean(session?.itinerary_state?.itinerary);
    setBusyMessage("正在识别地点");
    setNotice("");
    setError("");
    const result = await recognizePlaces(sessionId);
    if (!result.ok) {
      setError(result.error?.message || "地点识别失败");
      setBusyMessage("");
      return;
    }
    await load();
    setBusyMessage("");
    setNotice(hadItinerary ? "地点已更新，请重新生成路线" : "");
  }

  async function handleRevise(instruction: string) {
    setBusyMessage("正在调整路线");
    setNotice("");
    setError("");
    const outcome = resolvePlanningFlow(await reviseTrip(sessionId, instruction), "调整失败");
    if (outcome.kind === "failed") {
      setError(formatPlanningError(outcome.message, outcome.blockers));
      setBusyMessage("");
      return;
    }
    if (outcome.kind === "needs_user_choice") {
      setPlanningIntervention(outcome.result.planning_intervention);
      setBusyMessage("");
      return;
    }
    setPlanningIntervention(null);
    await load();
    setBusyMessage("");
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
            <button className="btn-secondary gap-2" onClick={handleExtract} disabled={isBusy}>
              <LoadingButtonLabel loading={recognizing} label="识别地点" loadingLabel="正在识别" dark={false} />
            </button>
            <button className="btn-primary gap-2" onClick={handlePlan} disabled={isBusy}>
              <LoadingButtonLabel loading={generatingRoute} label="生成路线" loadingLabel="正在生成" dark />
            </button>
          </div>
        </div>
        {(busyMessage || notice) && <p className="mt-4 rounded-2xl bg-surface px-4 py-3 text-sm text-ink">{busyMessage || notice}</p>}
        {error && <p className="mt-4 rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</p>}
      </section>

      <PlanningInterventionCard
        intervention={planningIntervention}
        disabled={isBusy}
        onChoose={handlePlanningChoice}
      />

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
        disabled={!session.itinerary_state || isBusy}
        showSuccess={Boolean(session.revision_history?.length)}
      />
    </main>
  );
}

function formatPlanningError(message: string, blockers?: PlanningBlocker[]) {
  const visibleBlockers = (blockers || []).filter((blocker) => blocker.message || blocker.action_hint).slice(0, 2);
  if (!visibleBlockers.length) return message;
  const details = visibleBlockers
    .map((blocker) => {
      const place = blocker.affected_poi_name ? `${blocker.affected_poi_name}：` : "";
      return `${place}${blocker.message || blocker.action_hint}`;
    })
    .join("；");
  return `${message} ${details}`;
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
