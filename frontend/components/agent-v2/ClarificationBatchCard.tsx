"use client";

import { useState } from "react";
import type { AgentV2Interruption } from "@/lib/types";

export function ClarificationBatchCard({
  interruption,
  onSubmit,
}: {
  interruption: AgentV2Interruption;
  onSubmit: (answers: Record<string, string>) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const complete = interruption.questions.every((question) => answers[question.question_id]?.trim());
  return (
    <section className="rounded-[28px] border border-amber-200 bg-amber-50 p-5">
      <h2 className="text-lg font-semibold">需要你的关键确认</h2>
      <div className="mt-4 space-y-5">
        {interruption.questions.map((question) => (
          <div key={question.question_id}>
            <p className="font-medium">{question.prompt}</p>
            <p className="mt-1 text-sm text-muted">{question.reason}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {question.options.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={answers[question.question_id] === option ? "btn-primary" : "btn-secondary"}
                  onClick={() => setAnswers((current) => ({ ...current, [question.question_id]: option }))}
                >
                  {option}
                </button>
              ))}
            </div>
            {question.allow_other ? (
              <input
                className="field mt-2"
                placeholder="也可以输入更具体的答案"
                value={answers[question.question_id] || ""}
                onChange={(event) => setAnswers((current) => ({ ...current, [question.question_id]: event.target.value }))}
              />
            ) : null}
          </div>
        ))}
      </div>
      <button
        className="btn-primary mt-5"
        disabled={!complete || submitting}
        onClick={async () => {
          setSubmitting(true);
          try { await onSubmit(answers); } finally { setSubmitting(false); }
        }}
      >
        {submitting ? "正在继续…" : "提交并继续原运行"}
      </button>
    </section>
  );
}
