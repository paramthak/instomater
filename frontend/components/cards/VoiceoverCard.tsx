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
    audio_tempo?: number;
  };
  iteration: number;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function VoiceoverCard({ data, iteration, status, sessionId, onAction }: Props) {
  const [loading, setLoading] = useState(false);
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
      await api.sendAction(sessionId, "regenerate", "voiceover", {});
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
          {data.audio_tempo ? ` · tempo ${data.audio_tempo}` : ""}
        </span>
      </div>

      <audio controls src={audioUrl} className="w-full h-10" />

      {!approved && onAction !== undefined && (
        <div className="flex gap-2 mt-4">
          <button onClick={handleApprove} disabled={loading} className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium">Approve</button>
          <button onClick={handleRegenerate} disabled={loading} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white rounded-lg text-sm">Regenerate</button>
        </div>
      )}
      {approved && <div className="mt-3 text-green-400 text-xs font-medium">✓ Approved</div>}
    </div>
  );
}
