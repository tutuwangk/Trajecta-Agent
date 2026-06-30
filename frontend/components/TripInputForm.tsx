"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createSession, extractPois } from "@/lib/api";
import { InfoTip } from "./InfoTip";

const preferenceOptions = ["美食", "拍照", "城市漫步", "购物", "历史文化", "休闲"];
const intensityOptions = [
  { label: "特种兵", value: "特种兵" },
  { label: "常规", value: "常规" },
  { label: "躺平式旅游", value: "躺平式旅游" }
];

export function TripInputForm() {
  const router = useRouter();
  const [destination, setDestination] = useState("成都");
  const [days, setDays] = useState("2");
  const [hotelArea, setHotelArea] = useState("春熙路");
  const [travelers, setTravelers] = useState("情侣");
  const [budget, setBudget] = useState("中等预算");
  const [physicalIntensity, setPhysicalIntensity] = useState("常规");
  const [mustVisit, setMustVisit] = useState("");
  const [avoidVisit, setAvoidVisit] = useState("");
  const [preferences, setPreferences] = useState<string[]>(["美食", "拍照"]);
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [sessionId, setSessionId] = useState("");

  async function submit() {
    setError("");
    setStatus("正在创建行程");
    const rawInput = [
      `目的地：${destination}`,
      `计划 ${days} 天，酒店在${hotelArea}附近。`,
      `同行人：${travelers}`,
      `预算：${budget}`,
      `偏好：${preferences.join("、")}`,
      `行程强度：${physicalIntensity}`,
      mustVisit ? `必去 ${mustVisit}` : "",
      avoidVisit ? `不想去 ${avoidVisit}` : ""
    ]
      .filter(Boolean)
      .join("\n");

    const created = await createSession({ raw_input: rawInput, notes });
    if (!created.ok || !created.data) {
      setError(created.error?.message || "创建行程失败");
      setStatus("");
      return;
    }
    setSessionId(created.data.session_id);
    setStatus("正在整理笔记里的地点");
    const extracted = await extractPois(created.data.session_id);
    if (!extracted.ok) {
      setError(extracted.error?.message || "地点整理失败。行程已创建，可进入详情页后重试。");
      setStatus("");
      return;
    }
    router.push(`/trip/${created.data.session_id}`);
  }

  return (
    <div className="space-y-6">
      <section className="px-1 pt-4 sm:pt-8">
        <p className="eyebrow">旅行路线整理</p>
        <h1 className="mt-4 max-w-3xl text-4xl font-semibold tracking-[-0.03em] text-ink sm:text-5xl">
          把小红书笔记整理成可执行路线
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-7 text-muted">
          粘贴旅行笔记，确认想去的地点，再生成每天怎么走。
        </p>
      </section>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,440px)_1fr]">
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
            <input className="field mt-1" value={days} onChange={(event) => setDays(event.target.value)} />
          </label>
          <label className="text-sm font-medium text-ink">
            酒店区域
            <input className="field mt-1" value={hotelArea} onChange={(event) => setHotelArea(event.target.value)} />
          </label>
          <label className="text-sm font-medium text-ink">
            同行人
            <input className="field mt-1" value={travelers} onChange={(event) => setTravelers(event.target.value)} />
          </label>
        </div>

        <label className="text-sm font-medium text-ink">
          预算
          <select className="field mt-1" value={budget} onChange={(event) => setBudget(event.target.value)}>
            <option>低预算</option>
            <option>中等预算</option>
            <option>高预算</option>
          </select>
        </label>

        <label className="text-sm font-medium text-ink">
          <span className="inline-flex items-center gap-2">
            行程强度
            <InfoTip label="会按从酒店出发到晚上回到酒店的全部外出时间控制每天节奏。" />
          </span>
          <select className="field mt-1" value={physicalIntensity} onChange={(event) => setPhysicalIntensity(event.target.value)}>
            {intensityOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

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

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="text-sm font-medium text-ink">
            必去地点
            <input className="field mt-1" value={mustVisit} onChange={(event) => setMustVisit(event.target.value)} />
          </label>
          <label className="text-sm font-medium text-ink">
            不想去地点
            <input className="field mt-1" value={avoidVisit} onChange={(event) => setAvoidVisit(event.target.value)} />
          </label>
        </div>

        <button className="btn-primary w-full" onClick={submit} disabled={!destination || !notes || Boolean(status)}>
          开始整理
        </button>
        {status && <p className="rounded-2xl bg-surface px-4 py-3 text-sm text-ink">{status}</p>}
        {error && (
          <div className="rounded-2xl border border-line bg-white px-4 py-3 text-sm text-ink">
            <p>{error}</p>
            {sessionId && (
              <button className="mt-2 font-medium underline" onClick={() => router.push(`/trip/${sessionId}`)}>
                打开已创建的行程
              </button>
            )}
          </div>
        )}
      </section>

      <section className="panel flex min-h-[560px] flex-col">
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-[-0.02em]">旅行笔记</h2>
          </div>
          <span className="rounded-full bg-surface px-3 py-1 text-xs text-muted">{notes.length} 字</span>
        </div>
        <textarea
          className="field min-h-[500px] flex-1 resize-none leading-6"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="粘贴一篇或多篇笔记。"
        />
      </section>
      </div>
    </div>
  );
}
