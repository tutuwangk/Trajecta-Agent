"use client";

import { useState } from "react";

export function RevisionPanel({
  onRevise,
  disabled,
  showSuccess,
}: {
  onRevise: (instruction: string) => Promise<void>;
  disabled?: boolean;
  showSuccess?: boolean;
}) {
  const [instruction, setInstruction] = useState("");

  return (
    <section className="panel space-y-4">
      <div>
        <h2 className="text-2xl font-semibold tracking-[-0.02em]">调整路线</h2>
        <p className="subtle mt-2">直接告诉我你想怎么改，我会按你的要求重新整理整条路线。</p>
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
          disabled={disabled || !instruction.trim()}
          onClick={async () => {
            await onRevise(instruction.trim());
            setInstruction("");
          }}
        >
          确认调整
        </button>
      </div>
      {showSuccess && (
        <div className="flex items-center gap-2 text-sm font-medium text-emerald-700">
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
          已按要求调整路线
        </div>
      )}
    </section>
  );
}
