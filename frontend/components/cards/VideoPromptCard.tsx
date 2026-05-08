"use client";

import { useState } from "react";
import { api } from "../../lib/api";

interface Props {
  data: {
    clip_index: number;
    prompt: string;
    start_image_path: string;
    duration_seconds: number;
    cost_summary?: { total_usd: number };
  };
  iteration: number;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function VideoPromptCard({ data, iteration, status, sessionId, onAction }: Props) {
  const [veoModel, setVeoModel] = useState<"fast" | "standard">("fast");
  const [feedback, setFeedback] = useState("");
  const [showChange, setShowChange] = useState(false);
  const [loading, setLoading] = useState(false);
  const approved = status === "approved";

  const costEstimate = veoModel === "fast"
    ? `~$${(data.duration_seconds * 0.10).toFixed(2)}`
    : `~$${(data.duration_seconds * 0.20).toFixed(2)}`;

  const handleApproveAndGenerate = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "prompt_approve", "video_generation", {
        clip_index: data.clip_index,
        veo_model: veoModel,
      });
      onAction?.();
    } finally { setLoading(false); }
  };

  const handleRestore = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "prompt_restore", "video_generation", {
        clip_index: data.clip_index,
        version: iteration,
      });
      onAction?.();
    } finally { setLoading(false); }
  };

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-4 max-w-2xl w-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-white font-semibold">Video Prompt — Clip {data.clip_index}</h3>
        <span className="text-zinc-500 text-xs">v{iteration} · {data.duration_seconds}s</span>
      </div>

      {/* Anchor frame */}
      <div className="mb-3">
        <p className="text-zinc-500 text-xs mb-1">Anchor frame</p>
        <div className="aspect-[9/16] bg-zinc-900 rounded overflow-hidden max-h-96 mx-auto">
          <img src={api.assetUrl(sessionId, data.start_image_path)} alt="Anchor" className="w-full h-full object-cover" />
        </div>
      </div>

      {/* Prompt text */}
      <pre className="bg-zinc-900 rounded-lg p-3 text-xs text-zinc-300 whitespace-pre-wrap font-mono leading-relaxed overflow-x-auto max-h-48">
        {data.prompt}
      </pre>
      {data.cost_summary && (
        <div className="mt-2 text-[11px] text-zinc-400">Prompt cost: ${data.cost_summary.total_usd.toFixed(4)}</div>
      )}

      {onAction !== undefined && (
        <div className="mt-4 space-y-3">
          {/* Veo model toggle */}
          <div className="flex items-center gap-2">
            <span className="text-zinc-400 text-xs">Veo model:</span>
            <button
              onClick={() => setVeoModel("fast")}
              className={`px-3 py-1 rounded text-xs ${veoModel === "fast" ? "bg-indigo-600 text-white" : "bg-zinc-700 text-zinc-300"}`}
            >
              Fast ({costEstimate})
            </button>
            <button
              onClick={() => setVeoModel("standard")}
              className={`px-3 py-1 rounded text-xs ${veoModel === "standard" ? "bg-indigo-600 text-white" : "bg-zinc-700 text-zinc-300"}`}
            >
              Standard
            </button>
          </div>

          {showChange ? (
            <div className="space-y-2">
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Describe changes to the video prompt…"
                className="w-full bg-zinc-900 text-white rounded-lg p-2 text-xs resize-none h-16 border border-zinc-600 focus:outline-none focus:border-indigo-500"
              />
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    if (!feedback.trim()) return;
                    setLoading(true);
                    try {
                      await api.sendAction(sessionId, "prompt_change", "video_generation", { clip_index: data.clip_index, feedback });
                      setFeedback(""); setShowChange(false); onAction?.();
                    } finally { setLoading(false); }
                  }}
                  disabled={loading || !feedback.trim()}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm"
                >Submit</button>
                <button onClick={() => setShowChange(false)} className="px-4 py-2 bg-zinc-700 text-white rounded-lg text-sm">Cancel</button>
              </div>
            </div>
          ) : (
            <div className="flex gap-2">
              {!approved && (
                <button onClick={status === "previous" ? handleRestore : handleApproveAndGenerate} disabled={loading} className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
                  {status === "previous" ? "Use this version" : "Approve and Generate"}
                </button>
              )}
              <button onClick={() => setShowChange(true)} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">Change</button>
            </div>
          )}
        </div>
      )}
      {approved && <div className="mt-3 text-green-400 text-xs font-medium">✓ Approved</div>}
      {status === "previous" && <div className="mt-3 text-zinc-500 text-xs font-medium">Previous version</div>}
    </div>
  );
}
