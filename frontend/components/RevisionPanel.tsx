"use client";

import { useState } from "react";

const quickActions = [
  "更松弛一点",
  "多安排美食",
  "少一点拍照点",
  "加一个夜景",
  "少排队",
  "预算低一点",
  "删掉某个地点",
  "换了酒店区域",
  "按雨天调整"
];

export function RevisionPanel({ onRevise, disabled }: { onRevise: (instruction: string, quick?: string) => Promise<void>; disabled?: boolean }) {
  const [instruction, setInstruction] = useState("");

  return (
    <section className="panel space-y-4">
      <div>
        <h2 className="text-2xl font-semibold tracking-[-0.02em]">调整路线</h2>
        <p className="subtle mt-1">直接说你想怎么改。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {quickActions.map((action) => (
          <button key={action} className="btn-secondary" disabled={disabled} onClick={() => onRevise(action, action)}>
            {action}
          </button>
        ))}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          className="field"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          placeholder="例如：删掉建设路，或者住到宽窄巷子附近"
        />
        <button
          className="btn-primary shrink-0"
          disabled={disabled || !instruction}
          onClick={async () => {
            await onRevise(instruction);
            setInstruction("");
          }}
        >
          确认调整
        </button>
      </div>
    </section>
  );
}
