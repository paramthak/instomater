"use client";

import { useState } from "react";
import { api } from "../../lib/api";

interface Props {
  data: {
    clip_index: number;
    video_path: string;
    veo_model: string;
    duration_seconds: number;
    cost_summary?: { total_usd: number };
  };
  iteration: number;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function VideoCard({ data, iteration, status, sessionId, onAction }: Props) {
  const [feedback, setFeedback] = useState("");
  const [showChange, setShowChange] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const approved = status === "approved";
  const videoUrl = api.assetUrl(sessionId, data.video_path);

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "approve", "video_generation", { clip_index: data.clip_index });
      onAction?.();
    } finally { setLoading(false); }
  };

  const handleRestore = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "restore", "video_generation", { clip_index: data.clip_index, version: iteration });
      onAction?.();
    } finally { setLoading(false); }
  };

  return (
    <>
      <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl overflow-hidden max-w-xs w-full">
        <div className="relative">
          <div className="absolute top-2 left-2 z-10 bg-black/60 rounded px-1.5 py-0.5 text-white text-xs font-mono">v{iteration}</div>
          <div className="absolute top-2 right-2 z-10 bg-black/60 rounded px-1.5 py-0.5 text-zinc-300 text-xs">
            Clip {data.clip_index} · {data.veo_model}
          </div>
          <div className="aspect-[9/16] bg-zinc-900 cursor-zoom-in" onClick={() => setFullscreen(true)}>
            <video src={videoUrl} controls muted className="w-full h-full object-cover" />
          </div>
        </div>

        {data.cost_summary && (
          <div className="px-3 pt-2 text-[11px] text-zinc-400">Cost: ${data.cost_summary.total_usd.toFixed(4)}</div>
        )}

        {onAction !== undefined && (
          <div className="p-3 space-y-2">
            {showChange ? (
              <div className="space-y-2">
                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="Describe changes in plain English…"
                  className="w-full bg-zinc-900 text-white rounded p-2 text-xs resize-none h-16 border border-zinc-600 focus:outline-none focus:border-indigo-500"
                />
                <div className="flex gap-2">
                  <button
                    onClick={async () => {
                      if (!feedback.trim()) return;
                      setLoading(true);
                      try {
                        await api.sendAction(sessionId, "change", "video_generation", { clip_index: data.clip_index, feedback });
                        setFeedback(""); setShowChange(false); onAction?.();
                      } finally { setLoading(false); }
                    }}
                    disabled={loading || !feedback.trim()}
                    className="flex-1 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded text-xs"
                  >Submit</button>
                  <button onClick={() => setShowChange(false)} className="flex-1 py-1.5 bg-zinc-700 text-white rounded text-xs">Cancel</button>
                </div>
              </div>
            ) : (
              <div className="flex gap-2">
                {!approved && <button onClick={status === "previous" ? handleRestore : handleApprove} disabled={loading} className="flex-1 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded text-xs font-medium">{status === "previous" ? "Use this version" : "Approve"}</button>}
                <button onClick={() => setShowChange(true)} className="flex-1 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded text-xs">Change</button>
              </div>
            )}
          </div>
        )}
        {approved && <div className="px-3 py-2 text-green-400 text-xs font-medium">✓ Approved</div>}
        {status === "previous" && <div className="px-3 py-2 text-zinc-500 text-xs font-medium">Previous version</div>}
      </div>

      {fullscreen && (
        <div className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center" onClick={() => setFullscreen(false)}>
          <video src={videoUrl} controls className="max-h-screen max-w-full" />
        </div>
      )}
    </>
  );
}
