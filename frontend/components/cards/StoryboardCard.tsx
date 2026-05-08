"use client";

import { Fragment, useState } from "react";
import { StoryboardData } from "../../lib/types";
import { api } from "../../lib/api";

interface Props {
  data: StoryboardData;
  iteration: number;
  status: string;
  sessionId: string;
  onAction?: () => void;
}

export function StoryboardCard({ data, iteration, status, sessionId, onAction }: Props) {
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [feedback, setFeedback] = useState("");
  const [showChange, setShowChange] = useState(false);
  const [loading, setLoading] = useState(false);
  const approved = status === "approved";
  const totalScenes = data.total_scenes ?? data.total_clips ?? data.scenes?.length ?? 0;

  const toggle = (id: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "approve", "storyboard", {});
      onAction?.();
    } finally { setLoading(false); }
  };

  const handleRestore = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "restore", "storyboard", { version: iteration });
      onAction?.();
    } finally { setLoading(false); }
  };

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-4 max-w-3xl w-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-white font-semibold">Storyboard</h3>
        <span className="text-zinc-500 text-xs">v{iteration} · {totalScenes} scenes · {data.total_duration_seconds?.toFixed(1)}s</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-500 border-b border-zinc-700">
              <th className="text-left pb-2 pr-3 w-16">Time</th>
              <th className="text-left pb-2 pr-3 w-8">Scene</th>
              <th className="text-left pb-2 pr-3">Voiceover</th>
              <th className="text-left pb-2 pr-3">Image</th>
              <th className="text-left pb-2">Transition</th>
            </tr>
          </thead>
          <tbody>
            {data.scenes?.map((s) => (
              <Fragment key={s.scene_id}>
                <tr
                  className="border-b border-zinc-800/50 cursor-pointer hover:bg-zinc-700/20"
                  onClick={() => toggle(s.scene_id)}
                >
                  <td className="py-2 pr-3 text-zinc-400 whitespace-nowrap">{s.start_time.toFixed(1)}–{s.end_time.toFixed(1)}s</td>
                  <td className="py-2 pr-3 text-zinc-300 font-mono">{s.scene_id}</td>
                  <td className="py-2 pr-3 text-zinc-200">{s.voiceover_words ?? s.voiceover_text}</td>
                  <td className="py-2 pr-3 text-zinc-400 whitespace-nowrap font-mono">{s.image_slot}</td>
                  <td className="py-2 text-zinc-500">{typeof s.transition_out === 'object' ? s.transition_out.type : s.transition_out} {typeof s.transition_out === 'object' ? s.transition_out.duration_seconds : s.transition_duration_seconds}s</td>
                </tr>
                {expandedRows.has(s.scene_id) && (
                  <tr key={`${s.scene_id}-expand`} className="bg-zinc-900/40">
                    <td colSpan={5} className="px-2 py-2 text-zinc-300 text-xs leading-relaxed space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-zinc-400 font-mono">{s.shot_type} · {s.camera_motion}</span>
                        {s.image_description?.camera_angle && (
                          <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 text-[10px] font-mono">{s.image_description.camera_angle}</span>
                        )}
                        {s.era_year != null && (
                          <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-amber-300 text-[10px] font-mono">era {s.era_year}</span>
                        )}
                        {s.face_reference_mode && (
                          <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-indigo-300 text-[10px] font-mono">
                            face: {s.face_reference_mode}
                            {s.face_reference_mode === "age_down_to" && s.face_reference_target_age != null ? ` → ${s.face_reference_target_age}` : ""}
                          </span>
                        )}
                      </div>
                      {(s.setting_category || s.location_anchor) && (
                        <div className="text-zinc-400"><span className="text-zinc-500">Setting: </span>{s.setting_category} — {s.location_anchor}</div>
                      )}
                      {s.visual_beat && (
                        <div className="text-zinc-300"><span className="text-zinc-500">Beat: </span>{s.visual_beat}</div>
                      )}
                      {s.image_description?.subject_and_pose && (
                        <div className="text-zinc-400"><span className="text-zinc-500">Frame: </span>{s.image_description.subject_and_pose} · {s.image_description.environment}</div>
                      )}
                      {s.image_description?.era_constraints && (
                        <div className="text-zinc-400"><span className="text-zinc-500">Era constraints: </span>{s.image_description.era_constraints}</div>
                      )}
                      {s.subject_life_stage && (
                        <div className="text-zinc-400"><span className="text-zinc-500">Age: </span>{s.subject_life_stage}{s.age_continuity_note ? ` — ${s.age_continuity_note}` : ''}</div>
                      )}
                      {s.motion_arc && (
                        <div className="text-zinc-400">
                          <span className="text-zinc-500">Motion: </span>
                          {[s.motion_arc.camera_move, s.motion_arc.subject_action, s.motion_arc.traversal].filter(Boolean).join(" · ")}
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {data.cost_summary && (
        <div className="mt-3 text-[11px] text-zinc-400">Cost: ${data.cost_summary.total_usd.toFixed(4)}</div>
      )}

      {onAction !== undefined && (
        <div className="mt-4 space-y-2">
          {showChange ? (
            <div className="space-y-2">
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Describe changes…"
                className="w-full bg-zinc-900 text-white rounded-lg p-3 text-sm resize-none h-16 border border-zinc-600 focus:outline-none focus:border-indigo-500"
              />
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    if (!feedback.trim()) return;
                    setLoading(true);
                    try {
                      await api.sendAction(sessionId, "change", "storyboard", { feedback, version: iteration });
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
              {!approved && <button onClick={status === "previous" ? handleRestore : handleApprove} disabled={loading} className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{status === "previous" ? "Use this version" : "Approve"}</button>}
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
