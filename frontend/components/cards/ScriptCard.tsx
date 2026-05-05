"use client";

import { useState } from "react";
import { ScriptData } from "../../lib/types";
import { api } from "../../lib/api";

interface Props {
  data: ScriptData;
  iteration: number;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function ScriptCard({ data, iteration, status, sessionId, onAction }: Props) {
  const [feedback, setFeedback] = useState("");
  const [showChange, setShowChange] = useState(false);
  const [loading, setLoading] = useState(false);
  const hookMode = data.hook_subtype_used ?? data.hook_formula_used;
  const fragmentCount = typeof data.self_check?.fragment_count === "number" ? data.self_check.fragment_count : null;
  const anchorCount = typeof data.self_check?.specific_anchors_count === "number" ? data.self_check.specific_anchors_count : null;
  const scriptParts = data.structure
    ? [
        ["Hook", data.structure.hook],
        ["Setup", data.structure.setup],
        ["Build", data.structure.build],
        ["Landing", data.structure.landing],
      ]
    : null;

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "approve", "script", {});
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  const approved = status === "approved";

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-4 max-w-2xl w-full">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h3 className="text-white font-semibold">Script</h3>
          <p className="text-zinc-500 text-xs mt-0.5">
            {data.hook_category} hook · {hookMode} · {data.perspective.replaceAll("_", " ")} · {data.estimated_word_count} words · ~{data.estimated_duration_seconds}s
          </p>
        </div>
        <span className="text-zinc-500 text-xs">v{iteration}</span>
      </div>

      {(fragmentCount !== null || anchorCount !== null) && (
        <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-zinc-400">
          {fragmentCount !== null && <span className="rounded bg-zinc-900 px-2 py-1">{fragmentCount} fragments</span>}
          {anchorCount !== null && <span className="rounded bg-zinc-900 px-2 py-1">{anchorCount} anchors</span>}
        </div>
      )}

      {scriptParts ? (
        <div className="mt-3 space-y-2">
          {scriptParts.map(([label, text]) => (
            <div key={label}>
              <div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div>
              <p className="text-zinc-200 text-sm leading-relaxed whitespace-pre-wrap font-serif">{text}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-zinc-200 text-sm leading-relaxed whitespace-pre-wrap mt-3 font-serif">
          {data.full_text}
        </p>
      )}

      {!approved && onAction !== undefined && (
        <div className="mt-4 space-y-2">
          {showChange ? (
            <div className="space-y-2">
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Describe changes in plain English…"
                className="w-full bg-zinc-900 text-white rounded-lg p-3 text-sm resize-none h-20 border border-zinc-600 focus:outline-none focus:border-indigo-500"
              />
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    if (!feedback.trim()) return;
                    setLoading(true);
                    try {
                      await api.sendAction(sessionId, "change", "script", { feedback });
                      setFeedback(""); setShowChange(false); onAction?.();
                    } finally { setLoading(false); }
                  }}
                  disabled={loading || !feedback.trim()}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm"
                >Submit</button>
                <button onClick={() => setShowChange(false)} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">Cancel</button>
              </div>
            </div>
          ) : (
            <div className="flex gap-2">
              <button onClick={handleApprove} disabled={loading} className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium">Approve</button>
              <button onClick={() => setShowChange(true)} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">Change</button>
            </div>
          )}
        </div>
      )}
      {approved && <div className="mt-3 text-green-400 text-xs font-medium">✓ Approved</div>}
    </div>
  );
}
