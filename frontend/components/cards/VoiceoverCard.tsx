"use client";

import { useState } from "react";
import { api } from "../../lib/api";

interface Props {
  data: {
    audio_path: string;
    gender: string;
    voice_id?: string;
    model_id?: string;
    language_code?: string;
    tts_speed?: number;
    cost_summary?: { total_usd: number };
  };
  iteration: number;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function VoiceoverCard({ data, iteration, status, sessionId, onAction }: Props) {
  const [loading, setLoading] = useState(false);
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(data.tts_speed ?? 1.2);
  const approved = status === "approved";
  const audioUrl = api.assetUrl(sessionId, data.audio_path);

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "approve", "voiceover", {});
      onAction?.();
    } finally { setLoading(false); }
  };

  const handleRegenerate = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "regenerate", "voiceover", { speed });
      onAction?.();
    } finally { setLoading(false); }
  };

  const handleCustomVoice = async () => {
    if (!voiceId.trim()) return;
    setLoading(true);
    try {
      await api.sendAction(sessionId, "regenerate", "voiceover", { voice_id: voiceId.trim(), speed });
      setVoiceId("");
      onAction?.();
    } finally { setLoading(false); }
  };

  const handleRestore = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "restore", "voiceover", { version: iteration });
      onAction?.();
    } finally { setLoading(false); }
  };

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-4 max-w-2xl w-full">
      <div className="flex items-start justify-between gap-3 mb-3">
        <h3 className="text-white font-semibold">Voiceover</h3>
        <span className="text-zinc-500 text-xs text-right max-w-md">
          v{iteration} · {data.gender}
          {data.voice_id ? ` · ${data.voice_id}` : ""}
          {data.model_id ? ` · ${data.model_id}` : ""}
          {data.language_code ? ` · ${data.language_code}` : ""}
          {data.tts_speed ? ` · speed ${data.tts_speed}` : ""}
        </span>
      </div>

      <audio controls src={audioUrl} className="w-full h-10" />

      <div className="mt-3 flex items-center gap-2 text-xs text-zinc-400">
        <span>Speed</span>
        <input
          type="number"
          min="0.7"
          max="1.2"
          step="0.05"
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="w-20 rounded bg-zinc-900 border border-zinc-700 px-2 py-1 text-zinc-100"
        />
        {data.cost_summary && <span className="ml-auto">Cost: ${data.cost_summary.total_usd.toFixed(4)}</span>}
      </div>

      {onAction !== undefined && (
        <div className="mt-4 space-y-2">
          <div className="flex gap-2">
            {!approved && <button onClick={status === "previous" ? handleRestore : handleApprove} disabled={loading} className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{status === "previous" ? "Use this version" : "Approve"}</button>}
            <button onClick={handleRegenerate} disabled={loading} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white rounded-lg text-sm">Regenerate</button>
          </div>
          <div className="flex gap-2">
            <input
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value)}
              placeholder="Paste another voice ID"
              className="min-w-0 flex-1 rounded bg-zinc-900 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
            />
            <button onClick={handleCustomVoice} disabled={loading || !voiceId.trim()} className="px-3 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white rounded-lg text-sm">Use ID</button>
          </div>
        </div>
      )}
      {approved && <div className="mt-3 text-green-400 text-xs font-medium">✓ Approved</div>}
      {status === "previous" && <div className="mt-3 text-zinc-500 text-xs font-medium">Previous version</div>}
    </div>
  );
}
