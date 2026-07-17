"use client";

import { useState } from "react";
import type { PoiRow } from "@/lib/types";

function poiId(row: PoiRow) {
  return row.grounded_poi.amap_id ? `amap_${row.grounded_poi.amap_id}` : `raw_${row.grounded_poi.raw_name}`;
}

export function POIConfirmTable({
  pois,
  onChange
}: {
  pois: PoiRow[];
  onChange: (decisions: Array<{ poi_id: string; decision: string; manual_name?: string }>) => void | Promise<void>;
}) {
  const [manualNames, setManualNames] = useState<Record<number, string>>({});

  if (!pois.length) {
    return <div className="panel subtle">还没有可确认的地点。请回到上一步补充旅行笔记。</div>;
  }

  function update(row: PoiRow, decision: string, manual_name?: string) {
    onChange([{ poi_id: poiId(row), decision, manual_name }]);
  }

  return (
    <section className="panel overflow-hidden">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-[-0.02em]">确认地点</h2>
        </div>
        <span className="rounded-full bg-surface px-3 py-1 text-sm text-muted">{pois.length} 个地点</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th className="py-3 pr-4 font-medium">笔记里写的</th>
              <th className="py-3 pr-4 font-medium">地图地点</th>
              <th className="py-3 pr-4 font-medium">状态</th>
              <th className="py-3 pr-4 font-medium">位置</th>
              <th className="py-3 pr-4 font-medium">安排</th>
              <th className="py-3 pr-4 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {pois.map((row) => {
              const value = manualNames[row.id] ?? row.manual_name ?? row.grounded_poi.standard_name ?? row.grounded_poi.raw_name;
              const evidence = row.grounded_poi.contexts?.[0] || firstString(row.raw_poi.contexts);
              return (
                <tr key={row.id} className="border-b border-line/80 align-top">
                  <td className="py-4 pr-4">
                    <div className="font-medium text-ink">{row.grounded_poi.raw_name}</div>
                    {evidence && <div className="mt-1 max-w-[220px] text-xs leading-5 text-muted">{evidence}</div>}
                  </td>
                  <td className="py-4 pr-4">
                    <div className="flex min-w-[260px] gap-2">
                      <input
                        className="field py-2"
                        value={value}
                        onChange={(event) => setManualNames((current) => ({ ...current, [row.id]: event.target.value }))}
                      />
                      <button className="btn-secondary shrink-0 px-3 py-2" onClick={() => update(row, row.decision, value)}>
                        换一个地点
                      </button>
                    </div>
                    <div className="mt-1 max-w-[320px] text-xs leading-5 text-muted">{row.grounded_poi.address || "地址待确认"}</div>
                  </td>
                  <td className="py-4 pr-4">
                    <span className={`rounded-full px-3 py-1 text-xs font-medium ${statusClass(row.grounded_poi.match_status)}`}>
                      {statusLabel(row.grounded_poi.match_status)}
                    </span>
                  </td>
                  <td className="py-4 pr-4 text-muted">
                    <div>{row.grounded_poi.district || row.grounded_poi.city || "-"}</div>
                    <div className="mt-1 text-xs">{row.grounded_poi.category_normalized || row.grounded_poi.category_raw || "-"}</div>
                  </td>
                  <td className="py-4 pr-4">
                    <span className="rounded-full bg-surface px-3 py-1 text-xs font-medium text-ink">{decisionLabel(row.decision)}</span>
                  </td>
                  <td className="py-4 pr-4">
                    <div className="flex flex-wrap gap-2">
                      <button className="btn-secondary px-3 py-1.5" onClick={() => update(row, "keep")}>安排</button>
                      <button className="btn-secondary px-3 py-1.5" onClick={() => update(row, "must_visit")}>必去</button>
                      <button className="btn-secondary px-3 py-1.5" onClick={() => update(row, "optional")}>可选</button>
                      <button className="btn-secondary px-3 py-1.5" onClick={() => update(row, "delete")}>删除</button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function statusClass(status: string) {
  if (status === "matched") return "bg-emerald-50 text-emerald-700";
  if (status === "ambiguous") return "bg-amber-50 text-amber-700";
  return "bg-rose-50 text-rose-700";
}

function statusLabel(status: string) {
  return { matched: "已确认", ambiguous: "需确认", unmatched: "待修正" }[status] || status;
}

function decisionLabel(decision: string) {
  return { keep: "安排", delete: "已删除", must_visit: "必去", optional: "可选" }[decision] || decision;
}

function firstString(value: unknown) {
  return Array.isArray(value) && typeof value[0] === "string" ? value[0] : "";
}
