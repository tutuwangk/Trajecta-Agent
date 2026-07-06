"use client";

import { useState } from "react";
import type { PoiRow } from "@/lib/types";

function placeId(row: PoiRow) {
  return row.place_pool_item?.id || (row.grounded_poi.amap_id ? `amap_${row.grounded_poi.amap_id}` : `raw_${row.grounded_poi.raw_name}`);
}

export function PlacePool({
  pois,
  onChange,
  onGenerateRoute,
  generateLoading,
  generateDisabled,
  generateError
}: {
  pois: PoiRow[];
  onChange?: (decisions: Array<{ poi_id: string; decision: string; manual_name?: string }>) => void | Promise<void>;
  onGenerateRoute?: () => void | Promise<void>;
  generateLoading?: boolean;
  generateDisabled?: boolean;
  generateError?: string;
}) {
  const [editingId, setEditingId] = useState<string>("");
  const [manualNames, setManualNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<Record<string, string>>({});
  const visiblePois = pois.filter((row) => row.final_decision !== "exclude");

  if (!visiblePois.length) {
    return (
      <section className="panel">
        <h2 className="text-2xl font-semibold tracking-[-0.02em]">识别出的地点</h2>
        <div className="mt-4 rounded-3xl border border-line bg-white/70 p-4 font-medium text-ink">识别出的地点会显示在这里。</div>
        {onGenerateRoute && (
          <>
            <button className="btn-primary mt-4 w-full gap-2" onClick={onGenerateRoute} disabled={generateDisabled}>
              <GenerateButtonLabel loading={Boolean(generateLoading)} />
            </button>
            <GenerateError message={generateError} />
          </>
        )}
      </section>
    );
  }

  async function update(row: PoiRow, decision: string, manual_name?: string) {
    if (!onChange) return;
    const id = placeId(row);
    setLoading((current) => ({ ...current, [id]: loadingTextFor(decision, manual_name) }));
    try {
      await onChange([{ poi_id: id, decision, manual_name }]);
      setEditingId("");
    } finally {
      setLoading((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
    }
  }

  return (
    <section className="panel">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-[-0.02em]">识别出的地点</h2>
        </div>
        <span className="rounded-full bg-surface px-3 py-1 text-sm text-muted">{visiblePois.length} 个地点</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {visiblePois.map((row) => {
          const item = row.place_pool_item;
          const id = placeId(row);
          const value = manualNames[id] ?? row.manual_name ?? item.display_name;
          const loadingText = loading[id];
          const isMust = row.user_override === "must_include";
          const isOptional = row.user_override === "optional" || row.final_decision === "optional";
          const isArrangeNearby = row.user_override === "arrange_nearby";
          const canArrangeNearby = item.primary_actions.includes("顺路安排");
          const canMarkMust = item.primary_actions.includes("必去");
          const canMarkOptional = item.primary_actions.includes("待定");
          const canRemove = item.primary_actions.includes("移除");
          const canRename = item.primary_actions.includes("改名");
          return (
            <article key={row.id} className={`rounded-3xl bg-white/70 p-4 ${cardClass(isMust, isOptional)}`}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-base font-semibold text-ink">{item.display_name}</div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-full bg-surface px-2.5 py-1 text-muted">{item.type_label}</span>
                  </div>
                </div>
                {loadingText && (
                  <div className="flex shrink-0 items-center gap-2 text-xs text-muted">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-ink" />
                    {loadingText}
                  </div>
                )}
              </div>
              {editingId === id && (
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <input
                    className="field py-2"
                    value={value}
                    onChange={(event) => setManualNames((current) => ({ ...current, [id]: event.target.value }))}
                  />
                  <button className="btn-primary shrink-0" disabled={Boolean(loadingText)} onClick={() => update(row, "rename_confirm", value)}>
                    重新搜索
                  </button>
                </div>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                {canArrangeNearby && (
                  <button className="btn-primary px-3 py-1.5" disabled={Boolean(loadingText)} onClick={() => update(row, "arrange_nearby")}>
                    顺路安排
                  </button>
                )}
                {canMarkMust && (
                  <button className={isMust ? "btn-primary px-3 py-1.5" : "btn-secondary px-3 py-1.5"} disabled={Boolean(loadingText)} onClick={() => update(row, "must_include")}>
                    必去
                  </button>
                )}
                {canMarkOptional && (
                  <button className={isOptional && !isMust ? "btn-primary px-3 py-1.5" : "btn-secondary px-3 py-1.5"} disabled={Boolean(loadingText)} onClick={() => update(row, "optional")}>
                    待定
                  </button>
                )}
                {canRemove && (
                  <button className="btn-secondary px-3 py-1.5" disabled={Boolean(loadingText)} onClick={() => update(row, "remove")}>
                    移除
                  </button>
                )}
                {canRename && (
                  <button className="btn-secondary px-3 py-1.5" disabled={Boolean(loadingText)} onClick={() => setEditingId(editingId === id ? "" : id)}>
                    改名
                  </button>
                )}
              </div>
              {isArrangeNearby && <div className="mt-3 text-sm text-muted">已按顺路候选处理</div>}
            </article>
          );
        })}
      </div>
      {onGenerateRoute && (
        <>
          <button className="btn-primary mt-4 w-full gap-2" onClick={onGenerateRoute} disabled={generateDisabled}>
            <GenerateButtonLabel loading={Boolean(generateLoading)} />
          </button>
          <GenerateError message={generateError} />
        </>
      )}
    </section>
  );
}

function GenerateButtonLabel({ loading }: { loading: boolean }) {
  return (
    <>
      {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
      {loading ? "正在生成" : "生成路线"}
    </>
  );
}

function GenerateError({ message }: { message?: string }) {
  if (!message) return null;
  return <div className="mt-3 rounded-2xl border border-line bg-white px-4 py-3 text-sm text-ink">{message}</div>;
}

function loadingTextFor(decision: string, manualName?: string) {
  if (manualName) return "正在重新搜索";
  if (decision === "arrange_nearby") return "正在记录顺路候选";
  return "正在确认";
}

function cardClass(isMust: boolean, isOptional: boolean) {
  if (isMust) return "border-2 border-ink";
  if (isOptional) return "border-2 border-ink/40";
  return "border border-line";
}
