"use client";

import { useState } from "react";
import { ErrorCard as ErrorCardType } from "../lib/types";
import { api } from "../lib/api";

interface Props {
  msg: ErrorCardType;
  sessionId: string;
  onAction?: () => void;
}

export function ErrorCardComponent({ msg, sessionId, onAction }: Props) {
  const [showChange, setShowChange] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [loading, setLoading] = useState(false);

  const actionPayload = (extra?: Record<string, unknown>) => {
    const payload: Record<string, unknown> = {
      substage_index: msg.substage_index,
      ...extra,
    };
    if (msg.substage_index != null) {
      if (msg.stage === "video_generation") payload.clip_index = msg.substage_index;
      if (msg.stage === "image_generation") payload.image_index = msg.substage_index;
    }
    return payload;
  };

  const handleRetry = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "retry", msg.stage, actionPayload());
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-red-950/40 border border-red-800/60 rounded-lg p-4 text-sm">
      <p className="text-red-300 font-medium mb-1">Generation failed</p>
      <p className="text-red-400 text-xs font-mono whitespace-pre-wrap">{msg.error_message}</p>
      <div className="flex gap-2 mt-3">
        {msg.allow_retry && (
          <button
            onClick={handleRetry}
            disabled={loading}
            className="px-3 py-1.5 bg-red-800 hover:bg-red-700 text-white rounded text-xs"
          >
            Retry
          </button>
        )}
        {msg.allow_change && (
          <button
            onClick={() => setShowChange(!showChange)}
            className="px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 text-white rounded text-xs"
          >
            Change
          </button>
        )}
      </div>
      {showChange && (
        <div className="mt-3">
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="Describe what to change…"
            className="w-full bg-zinc-800 text-white rounded p-2 text-xs resize-none h-16 border border-zinc-700"
          />
          <button
            onClick={async () => {
              if (!feedback.trim()) return;
              setLoading(true);
              try {
                await api.sendAction(sessionId, "change", msg.stage, actionPayload({ feedback }));
                onAction?.();
              } finally {
                setLoading(false);
              }
            }}
            disabled={loading || !feedback.trim()}
            className="mt-1 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs"
          >
            Apply
          </button>
        </div>
      )}
    </div>
  );
}
