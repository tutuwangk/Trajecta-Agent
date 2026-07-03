"use client";

import { useState } from "react";

const quickActions = [
  "多安排美食",
  "增加夜景",
  "删掉某个地点",
  "换酒店名",
  "下雨方案"
];

export function RevisionPanel({ onRevise, disabled }: { onRevise: (instruction: string, quick?: string) => Promise<void>; disabled?: boolean }) {
  const [instruction, setInstruction] = useState("");

  return (
    <section className="panel space-y-4">
      <div>
        <h2 className="text-2xl font-semibold tracking-[-0.02em]">调整路线</h2>
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
          placeholder="告诉我你想怎么改"
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
