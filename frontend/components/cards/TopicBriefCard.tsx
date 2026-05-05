"use client";

import { useState } from "react";
import { TopicBriefData } from "../../lib/types";
import { api } from "../../lib/api";

interface Props {
  data: TopicBriefData;
  iteration: number;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function TopicBriefCard({ data, iteration, status, sessionId, onAction }: Props) {
  const [feedback, setFeedback] = useState("");
  const [showChange, setShowChange] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "approve", "topic_brief", {});
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  const handleChange = async () => {
    if (!feedback.trim()) return;
    setLoading(true);
    try {
      await api.sendAction(sessionId, "change", "topic_brief", { feedback });
      setFeedback("");
      setShowChange(false);
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  const approved = status === "approved";

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-4 max-w-2xl w-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-white font-semibold">Topic Brief</h3>
        <span className="text-zinc-500 text-xs">v{iteration}</span>
      </div>

      <div className="space-y-3 text-sm">
        <div>
          <p className="text-zinc-400 text-xs uppercase tracking-wide mb-1">Person</p>
          <p className="text-white">{data.person_name} — {data.origin_city}, {data.origin_country}</p>
          <p className="text-zinc-300 text-xs mt-0.5">{data.current_role_or_legacy}</p>
        </div>

        <div>
          <p className="text-zinc-400 text-xs uppercase tracking-wide mb-1">Key Milestones</p>
          <ul className="space-y-0.5">
            {data.key_life_milestones?.map((m, i) => (
              <li key={i} className="text-zinc-300 text-xs">
                <span className="text-zinc-500 mr-2">{m.year}</span>{m.event}
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="text-zinc-400 text-xs uppercase tracking-wide mb-1">Narrative Arc</p>
          <p className="text-zinc-200 text-xs">{data.selected_narrative_arc}</p>
        </div>

        <div>
          <p className="text-zinc-400 text-xs uppercase tracking-wide mb-1">Tone</p>
          <p className="text-zinc-200 text-xs">{data.selected_tone}</p>
        </div>

        <div>
          <p className="text-zinc-400 text-xs uppercase tracking-wide mb-1">Visual Anchors</p>
          <ul className="space-y-0.5">
            {data.factual_anchors_for_visuals?.map((a, i) => (
              <li key={i} className="text-zinc-300 text-xs">• {a}</li>
            ))}
          </ul>
        </div>
      </div>

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
                  onClick={handleChange}
                  disabled={loading || !feedback.trim()}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm"
                >
                  Submit
                </button>
                <button onClick={() => setShowChange(false)} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-2">
              <button
                onClick={handleApprove}
                disabled={loading}
                className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
              >
                Approve
              </button>
              <button
                onClick={() => setShowChange(true)}
                className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm"
              >
                Change
              </button>
            </div>
          )}
        </div>
      )}
      {approved && (
        <div className="mt-3 text-green-400 text-xs font-medium">✓ Approved</div>
      )}
    </div>
  );
}
