"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createSession, planTrip, recognizePlaces, updatePlaceOverrides } from "@/lib/api";
import { InfoTip } from "./InfoTip";
import { PlacePool } from "./PlacePool";
import type { PoiRow, UserProfile } from "@/lib/types";

const preferenceOptions = ["美食", "拍照", "城市漫步", "购物", "历史文化", "休闲"];
const intensityOptions = [
  { label: "特种兵", value: "特种兵" },
  { label: "常规", value: "常规" },
  { label: "躺平式旅游", value: "躺平式旅游" }
];
const transportOptions = ["步行", "打车", "地铁公交"];
const routeGoalOptions = ["均衡安排", "美食优先", "拍照优先"];

export function TripInputForm() {
  const router = useRouter();
  const [destination, setDestination] = useState("");
  const [days, setDays] = useState("");
  const [hotelName, setHotelName] = useState("");
  const [travelerCount, setTravelerCount] = useState("");
  const [budget, setBudget] = useState("");
  const [physicalIntensity, setPhysicalIntensity] = useState("");
  const [transportPreference, setTransportPreference] = useState<string[]>([]);
  const [routeGoal, setRouteGoal] = useState("");
  const [preferences, setPreferences] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [routeError, setRouteError] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [pois, setPois] = useState<PoiRow[]>([]);
  const recognizing = status === "正在识别地点" || status === "正在确认位置";
  const generatingRoute = status === "正在整理路线";

  async function recognize() {
    setError("");
    setRouteError("");
    setStatus("正在识别地点");
    const rawInput = [
      destination ? `目的地：${destination}` : "",
      days ? `计划 ${days} 天。` : "",
      hotelName ? `酒店名：${hotelName}` : "",
      travelerCount ? `出行人数：${travelerCount}人` : "",
      budget ? `预算：${budget}` : "",
      preferences.length ? `偏好：${preferences.join("、")}` : "",
      transportPreference.length ? `交通偏好：${transportPreference.join("、")}` : "",
      routeGoal ? `路线目标：${routeGoal}` : "",
      physicalIntensity ? `行程强度：${physicalIntensity}` : "",
    ]
      .filter(Boolean)
      .join("\n");

    const created = await createSession({
      raw_input: rawInput,
      notes,
      user_profile: buildUserProfile({
        destination,
        days,
        hotelName,
        travelerCount,
        budget,
        physicalIntensity,
        transportPreference,
        routeGoal,
        preferences,
      }),
    });
    if (!created.ok || !created.data) {
      setError(created.error?.message || "创建行程失败");
      setStatus("");
      return;
    }
    setSessionId(created.data.session_id);
    setStatus("正在确认位置");
    const extracted = await recognizePlaces(created.data.session_id);
    if (!extracted.ok) {
      setError(extracted.error?.message || "地点识别失败。行程已创建，可进入详情页后重试。");
      setStatus("");
      return;
    }
    const nextPois = Array.isArray(extracted.data?.pois) ? (extracted.data.pois as PoiRow[]) : [];
    setPois(nextPois);
    setStatus("");
  }

  async function handlePlaceChange(decisions: Array<{ poi_id: string; decision: string; manual_name?: string }>) {
    if (!sessionId) return;
    setError("");
    setRouteError("");
    setStatus("正在保存地点调整");
    const result = await updatePlaceOverrides(sessionId, decisions);
    if (!result.ok || !result.data) {
      setError(result.error?.message || "保存地点调整失败");
      setStatus("");
      return;
    }
    setPois(result.data.pois);
    setStatus("");
  }

  async function generateRoute() {
    if (!sessionId) return;
    setRouteError("");
    setError("");
    setStatus("正在整理路线");
    const result = await planTrip(sessionId);
    if (!result.ok) {
      setRouteError(result.error?.message || "路线生成失败");
      setStatus("");
      return;
    }
    router.push(`/trip/${sessionId}`);
  }

  return (
    <div className="space-y-6">
      <section className="px-1 pt-4 sm:pt-8">
        <p className="eyebrow">Trajecta-Agent</p>
        <h1 className="mt-4 max-w-none text-4xl font-semibold tracking-[-0.03em] text-ink sm:text-5xl">
          你的J人旅行搭子：AI定制化旅游攻略
        </h1>
      </section>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section className="panel space-y-5">
          <div>
            <h2 className="text-xl font-semibold tracking-[-0.02em]">行程设置</h2>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-ink">
              目的地
              <input className="field mt-1" value={destination} onChange={(event) => setDestination(event.target.value)} />
            </label>
            <label className="text-sm font-medium text-ink">
              天数
              <input className="field mt-1" type="number" min="1" value={days} onChange={(event) => setDays(event.target.value)} />
            </label>
            <label className="text-sm font-medium text-ink">
              酒店名
              <input className="field mt-1" value={hotelName} onChange={(event) => setHotelName(event.target.value)} />
            </label>
            <label className="text-sm font-medium text-ink">
              出行人数
              <input className="field mt-1" type="number" min="1" value={travelerCount} onChange={(event) => setTravelerCount(event.target.value)} />
            </label>
          </div>

          <label className="text-sm font-medium text-ink">
            预算
            <select className="field mt-1" value={budget} onChange={(event) => setBudget(event.target.value)}>
              <option value="" disabled>
                请选择
              </option>
              <option>低预算</option>
              <option>中等预算</option>
              <option>高预算</option>
            </select>
          </label>

          <label className="text-sm font-medium text-ink">
            路线目标
            <select className="field mt-1" value={routeGoal} onChange={(event) => setRouteGoal(event.target.value)}>
              <option value="" disabled>
                请选择
              </option>
              {routeGoalOptions.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          </label>

          <label className="text-sm font-medium text-ink">
            <span className="inline-flex items-center gap-2">
              行程强度
              <InfoTip label="会按从酒店出发到晚上回到酒店的全部外出时间控制每天节奏。" />
            </span>
            <select className="field mt-1" value={physicalIntensity} onChange={(event) => setPhysicalIntensity(event.target.value)}>
              <option value="" disabled>
                请选择
              </option>
              {intensityOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <div>
            <div className="mb-2 text-sm font-medium text-ink">交通偏好</div>
            <div className="flex flex-wrap gap-2">
              {transportOptions.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={transportPreference.includes(option) ? "btn-primary" : "btn-secondary"}
                  onClick={() =>
                    setTransportPreference((current) =>
                      current.includes(option) ? current.filter((item) => item !== option) : [...current, option]
                    )
                  }
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 text-sm font-medium text-ink">兴趣偏好</div>
            <div className="flex flex-wrap gap-2">
              {preferenceOptions.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={preferences.includes(option) ? "btn-primary" : "btn-secondary"}
                  onClick={() =>
                    setPreferences((current) =>
                      current.includes(option) ? current.filter((item) => item !== option) : [...current, option]
                    )
                  }
                >
                  {option}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="panel flex min-h-[560px] flex-col">
          <div className="mb-3 flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 className="text-xl font-semibold tracking-[-0.02em]">资料与地点</h2>
              <p className="subtle mt-1">攻略、地点清单、餐厅推荐、酒店/交通信息、自由描述</p>
            </div>
            <span className="shrink-0 whitespace-nowrap rounded-full bg-surface px-3 py-1 text-center text-xs leading-none text-muted">
              {notes.length} 字
            </span>
          </div>
          <textarea
            className="field min-h-[420px] flex-1 resize-none leading-6"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="粘贴攻略、笔记、地点清单、餐厅推荐、酒店地址或你的旅行想法。"
          />
          <button className="btn-primary mt-4 w-full gap-2" onClick={recognize} disabled={!destination || !notes || Boolean(status)}>
            {recognizing && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
            {recognizing ? "正在识别" : "识别地点"}
          </button>
          {status && <p className="mt-3 rounded-2xl bg-surface px-4 py-3 text-sm text-ink">{status}</p>}
          {error && (
            <div className="mt-3 rounded-2xl border border-line bg-white px-4 py-3 text-sm text-ink">
              <p>{error}</p>
              {sessionId && (
                <button className="mt-2 font-medium underline" onClick={() => router.push(`/trip/${sessionId}`)}>
                  打开已创建的行程
                </button>
              )}
            </div>
          )}
        </section>
      </div>
      {Boolean(sessionId) && (
        <PlacePool
          pois={pois}
          onChange={handlePlaceChange}
          onGenerateRoute={generateRoute}
          generateLoading={generatingRoute}
          generateDisabled={!pois.length || Boolean(status)}
          generateError={routeError}
        />
      )}
    </div>
  );
}

function buildUserProfile({
  destination,
  days,
  hotelName,
  travelerCount,
  budget,
  physicalIntensity,
  transportPreference,
  routeGoal,
  preferences,
}: {
  destination: string;
  days: string;
  hotelName: string;
  travelerCount: string;
  budget: string;
  physicalIntensity: string;
  transportPreference: string[];
  routeGoal: string;
  preferences: string[];
}): UserProfile {
  const dayCount = clampNumber(days, 1, 5, 1);
  const hotel = hotelName.trim() || null;
  return {
    destination: destination.trim(),
    days: dayCount,
    nights: Math.max(dayCount - 1, 0),
    hotel_name: hotel,
    hotel_area: null,
    travelers: { count: clampNumber(travelerCount, 1, 20, 1), type: "未说明" },
    budget_level: budgetLevel(budget),
    transport_preference: transportPreference.length
      ? transportPreference.map(transportValue)
      : ["walking", "taxi", "public_transport"],
    route_goal: routeGoalValue(routeGoal),
    preferences: preferenceWeights(preferences),
    constraints: {
      avoid_too_tired: physicalIntensity === "躺平式旅游",
      physical_intensity: intensityValue(physicalIntensity),
      must_visit: [],
      avoid_visit: [],
    },
  };
}

function clampNumber(value: string, min: number, max: number, fallback: number) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(Math.max(parsed, min), max);
}

function budgetLevel(value: string): UserProfile["budget_level"] {
  if (value === "低预算") return "low";
  if (value === "高预算") return "high";
  return "medium";
}

function transportValue(value: string) {
  if (value === "步行") return "walking";
  if (value === "打车") return "taxi";
  if (value === "地铁公交") return "public_transport";
  return value;
}

function routeGoalValue(value: string) {
  if (value === "美食优先") return "food_first";
  if (value === "拍照优先") return "photo_first";
  return "balanced";
}

function intensityValue(value: string): NonNullable<UserProfile["constraints"]["physical_intensity"]> {
  if (value === "特种兵") return "high";
  if (value === "躺平式旅游") return "low";
  return "medium";
}

function preferenceWeights(selected: string[]) {
  return {
    food: selected.includes("美食") ? 5 : 3,
    photo: selected.includes("拍照") ? 5 : 3,
    citywalk: selected.includes("城市漫步") ? 5 : 3,
    shopping: selected.includes("购物") ? 5 : 3,
    history: selected.includes("历史文化") ? 5 : 3,
    relaxation: selected.includes("休闲") ? 5 : 3,
  };
}
