"use client";

import { useState } from "react";
import type { PoiDecisionInput, PoiRow } from "@/lib/types";

function placeId(row: PoiRow) {
  return row.place_pool_item?.id || (row.grounded_poi.amap_id ? `amap_${row.grounded_poi.amap_id}` : `raw_${row.grounded_poi.raw_name}`);
}

export function PlacePool({
  pois,
  hotelName,
  onChange,
  onGenerateRoute,
  generateLoading,
  generateDisabled,
  generateError
}: {
  pois: PoiRow[];
  hotelName?: string;
  onChange?: (decisions: PoiDecisionInput[]) => void | Promise<void>;
  onGenerateRoute?: () => void | Promise<void>;
  generateLoading?: boolean;
  generateDisabled?: boolean;
  generateError?: string;
}) {
  const [editingId, setEditingId] = useState<string>("");
  const [arrangingId, setArrangingId] = useState<string>("");
  const [showExcluded, setShowExcluded] = useState(false);
  const [manualNames, setManualNames] = useState<Record<string, string>>({});
  const [selectedAnchors, setSelectedAnchors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<Record<string, string>>({});
  const groupedPois = groupPois(pois);
  const visibleCount = pois.length;

  if (!visibleCount) {
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

  async function update(row: PoiRow, decision: string, manual_name?: string, anchor_poi_id?: string) {
    if (!onChange) return;
    const id = placeId(row);
    setLoading((current) => ({ ...current, [id]: loadingTextFor(decision, manual_name, anchor_poi_id) }));
    try {
      await onChange([{ poi_id: id, decision, manual_name, anchor_poi_id }]);
      setEditingId("");
      setArrangingId("");
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
        <span className="rounded-full bg-surface px-3 py-1 text-sm text-muted">{visibleCount} 个地点</span>
      </div>
      <div className="space-y-5">
        {groupedPois.map((group) => {
          if (group.key === "excluded" && !showExcluded) {
            return (
              <section key={group.key} className="rounded-3xl border border-line bg-white/60 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-base font-semibold text-ink">{group.label}</div>
                    <div className="mt-1 text-sm text-muted">这些地点目前不会进入正式路线，但你随时可以恢复。</div>
                  </div>
                  <button className="btn-secondary px-3 py-1.5" onClick={() => setShowExcluded(true)}>
                    展开 {group.rows.length} 个地点
                  </button>
                </div>
              </section>
            );
          }
          return (
            <section key={group.key}>
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="text-base font-semibold text-ink">{group.label}</div>
                <span className="rounded-full bg-surface px-3 py-1 text-xs text-muted">{group.rows.length} 个</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {group.rows.map((row) => {
          const item = row.place_pool_item;
          const id = placeId(row);
          const value = manualNames[id] ?? row.manual_name ?? item.display_name;
          const arrangeValue = selectedAnchors[id] ?? "";
          const loadingText = loading[id];
          const isMust = row.user_override === "must_include";
          const isOptional = row.user_override === "optional" || row.final_decision === "optional";
          const canArrangeNearby =
            item.primary_actions.includes("顺路规划") ||
            item.primary_actions.includes("顺路安排") ||
            (row.grounded_poi.is_chain === true && row.grounded_poi.chain_status !== "resolved" && row.final_decision === "unresolved");
          const canMarkMust = item.primary_actions.includes("必去");
          const canMarkOptional = item.primary_actions.includes("待定");
          const canRemove = item.primary_actions.includes("移除");
          const canRename = item.primary_actions.includes("改名");
          const anchorOptions = buildAnchorOptions(pois, row, hotelName);
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
              {arrangingId === id && (
                <div className="mt-3 space-y-2 rounded-2xl border border-line bg-surface/70 p-3">
                  <div className="text-sm text-ink">选择一个参考地点，系统会匹配离它最近的门店。</div>
                  <select
                    className="field py-2"
                    value={arrangeValue}
                    onChange={(event) => setSelectedAnchors((current) => ({ ...current, [id]: event.target.value }))}
                  >
                    <option value="">请选择参考地点</option>
                    {anchorOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <div className="flex flex-wrap gap-2">
                    <button
                      className="btn-primary px-3 py-1.5"
                      disabled={Boolean(loadingText) || !arrangeValue}
                      onClick={() => update(row, "confirm_arrange_nearby", undefined, arrangeValue)}
                    >
                      确认顺路规划
                    </button>
                    <button className="btn-secondary px-3 py-1.5" disabled={Boolean(loadingText)} onClick={() => setArrangingId("")}>
                      取消
                    </button>
                  </div>
                </div>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                {canArrangeNearby && (
                  <button
                    className="btn-primary px-3 py-1.5"
                    disabled={Boolean(loadingText)}
                    onClick={() => setArrangingId(arrangingId === id ? "" : id)}
                  >
                    顺路规划
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
              {row.decision_reason && <div className="mt-3 text-sm text-muted">{row.decision_reason}</div>}
              {row.grounded_poi.chain_status !== "resolved" && row.grounded_poi.is_chain && <div className="mt-3 text-sm text-muted">还没有确定具体门店</div>}
              {row.grounded_poi.chain_status === "resolved" && row.grounded_poi.resolved_from_anchor_name && (
                <div className="mt-3 text-sm text-muted">按“{row.grounded_poi.resolved_from_anchor_name}”顺路匹配</div>
              )}
            </article>
          );
                })}
              </div>
            </section>
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

function groupPois(pois: PoiRow[]) {
  return [
    { key: "included", label: "已纳入", rows: pois.filter((row) => row.final_decision === "include") },
    { key: "optional", label: "待定", rows: pois.filter((row) => row.final_decision === "optional") },
    { key: "unresolved", label: "需确认", rows: pois.filter((row) => row.final_decision === "unresolved") },
    { key: "excluded", label: "未纳入", rows: pois.filter((row) => row.final_decision === "exclude") },
  ].filter((group) => group.rows.length);
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

function buildAnchorOptions(pois: PoiRow[], currentRow: PoiRow, hotelName?: string) {
  const options = pois
    .filter((row) => row.id !== currentRow.id)
    .filter((row) => row.final_decision !== "exclude")
    .filter((row) => row.grounded_poi.match_status === "matched")
    .filter((row) => !row.grounded_poi.is_chain)
    .map((row) => ({ value: placeId(row), label: row.place_pool_item.display_name }));
  if (hotelName) {
    options.unshift({ value: "hotel_anchor", label: `酒店：${hotelName}` });
  }
  return options;
}

function loadingTextFor(decision: string, manualName?: string, anchorPoiId?: string) {
  if (manualName) return "正在重新搜索";
  if (decision === "confirm_arrange_nearby" && anchorPoiId) return "正在匹配顺路门店";
  return "正在确认";
}

function cardClass(isMust: boolean, isOptional: boolean) {
  if (isMust) return "border-2 border-ink";
  if (isOptional) return "border-2 border-ink/40";
  return "border border-line";
}
