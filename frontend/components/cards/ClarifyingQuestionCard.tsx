"use client";

import { useState } from "react";
import { ClarifyingQuestionsData } from "../../lib/types";
import { api } from "../../lib/api";

interface Props {
  data: ClarifyingQuestionsData;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function ClarifyingQuestionCard({ data, status, sessionId, onAction }: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [customInputs, setCustomInputs] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const answered = status === "approved";

  const questions = data.questions ?? [];
  const allAnswered = questions.every((q) => answers[q.id]);

  const handleSelect = (qId: string, option: string) => {
    if (answered) return;
    setAnswers((prev) => ({ ...prev, [qId]: option }));
  };

  const handleSubmit = async () => {
    if (!allAnswered) return;
    // Resolve "Custom" answers
    const resolved = Object.fromEntries(
      Object.entries(answers).map(([k, v]) => [
        k,
        v === "Custom: write your own" ? customInputs[k] || v : v,
      ])
    );
    setLoading(true);
    try {
      await api.sendAction(sessionId, "answer", "clarifying_questions", { answers: resolved });
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-4 max-w-2xl w-full space-y-4">
      <h3 className="text-white font-semibold">Visual Style Questions</h3>

      {questions.map((q) => {
        const selected = answers[q.id];
        const isAnswered = answered && selected;
        return (
          <div key={q.id} className="space-y-2">
            <p className="text-zinc-200 text-sm">{q.question_text}</p>
            {isAnswered ? (
              <p className="text-zinc-400 text-xs">
                <span className="text-zinc-500">Q: {q.question_text} </span>
                <span className="text-green-400">A: {selected}</span>
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {q.options.map((opt) => (
                  <button
                    key={opt}
                    onClick={() => handleSelect(q.id, opt)}
                    className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                      selected === opt
                        ? "bg-indigo-600 border-indigo-500 text-white"
                        : "bg-zinc-700 border-zinc-600 text-zinc-300 hover:bg-zinc-600"
                    }`}
                  >
                    {opt}
                  </button>
                ))}
                {selected === "Custom: write your own" && (
                  <input
                    type="text"
                    value={customInputs[q.id] ?? ""}
                    onChange={(e) => setCustomInputs((p) => ({ ...p, [q.id]: e.target.value }))}
                    placeholder="Type your answer…"
                    className="mt-1 w-full bg-zinc-900 text-white rounded px-2 py-1 text-xs border border-zinc-600 focus:outline-none"
                  />
                )}
              </div>
            )}
          </div>
        );
      })}

      {!answered && onAction !== undefined && (
        <button
          onClick={handleSubmit}
          disabled={!allAnswered || loading}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-lg text-sm font-medium"
        >
          Confirm Answers
        </button>
      )}
    </div>
  );
}
