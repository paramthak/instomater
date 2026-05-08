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
  isActive?: boolean;
}

function stripScriptAnnotations(text: string): string {
  return text
    .replace(/\s+---\s+(?:\*\*)?(?:Beat breakdown|Hook formula used|Pivot shape|Mirror closes on|No-redundancy check|Specificity check)[\s\S]*$/i, "")
    .split(/\n(?=(?:---\s*)?(?:\*\*)?(?:Beat breakdown|Hook formula used|Pivot shape|Mirror closes on|No-redundancy check|Specificity check)\b)/i)[0]
    .split(/\n(?=\s*\|?\s*Beat\s*\|)/i)[0]
    .split(/\n(?=\s*\*{0,2}Word count:)/i)[0]
    .trim();
}

export function ScriptCard({ data, iteration, status, sessionId, onAction, isActive = false }: Props) {
  const [feedback, setFeedback] = useState("");
  const [showChange, setShowChange] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [editText, setEditText] = useState(stripScriptAnnotations(data.full_text || data.display_text || ""));
  const [loading, setLoading] = useState(false);
  const scriptText = stripScriptAnnotations(data.display_text || data.full_text || "");

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "approve", "script", {});
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  const handleRestore = async () => {
    setLoading(true);
    try {
      await api.sendAction(sessionId, "restore", "script", { version: iteration });
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  const handleEditSubmit = async () => {
    if (!editText.trim()) return;
    setLoading(true);
    try {
      await api.sendAction(sessionId, "edit", "script", { script: editText, version: iteration });
      setShowEdit(false);
      onAction?.();
    } finally {
      setLoading(false);
    }
  };

  const openEdit = () => {
    setEditText(stripScriptAnnotations(data.full_text || data.display_text || ""));
    setShowChange(false);
    setShowEdit(true);
  };

  const approved = status === "approved";
  const shouldUseVersion = status === "previous" || status === "rejected" || (!approved && !isActive);

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-4 max-w-2xl w-full">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h3 className="text-white font-semibold">Script</h3>
        </div>
        <span className="text-zinc-500 text-xs">v{iteration}</span>
      </div>

      {showEdit ? (
        <div className="mt-3 space-y-2">
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            className="w-full bg-zinc-900 text-zinc-100 rounded-lg p-3 text-sm leading-relaxed font-serif resize-y min-h-56 border border-zinc-600 focus:outline-none focus:border-indigo-500"
          />
          <div className="flex gap-2">
            <button
              onClick={handleEditSubmit}
              disabled={loading || !editText.trim()}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm"
            >
              Submit edit
            </button>
            <button
              onClick={() => setShowEdit(false)}
              disabled={loading}
              className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white rounded-lg text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <p className="text-zinc-200 text-sm leading-relaxed whitespace-pre-wrap mt-3 font-serif">
          {scriptText}
        </p>
      )}

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
                placeholder="Describe changes in plain English…"
                className="w-full bg-zinc-900 text-white rounded-lg p-3 text-sm resize-none h-20 border border-zinc-600 focus:outline-none focus:border-indigo-500"
              />
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    if (!feedback.trim()) return;
                    setLoading(true);
                    try {
                      await api.sendAction(sessionId, "change", "script", { feedback, version: iteration });
                      setFeedback(""); setShowChange(false); onAction?.();
                    } finally { setLoading(false); }
                  }}
                  disabled={loading || !feedback.trim()}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm"
                >Submit</button>
                <button onClick={() => setShowChange(false)} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">Cancel</button>
              </div>
            </div>
          ) : !showEdit ? (
            <div className="flex gap-2">
              {!approved && <button onClick={shouldUseVersion ? handleRestore : handleApprove} disabled={loading} className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{shouldUseVersion ? "Use this version" : "Approve"}</button>}
              <button onClick={() => { setShowEdit(false); setShowChange(true); }} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">Change</button>
              <button onClick={openEdit} className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm">Edit</button>
            </div>
          ) : (
            null
          )}
        </div>
      )}
      {approved && <div className="mt-3 text-green-400 text-xs font-medium">✓ Approved</div>}
      {status === "previous" && <div className="mt-3 text-zinc-500 text-xs font-medium">Previous version</div>}
      {status === "rejected" && <div className="mt-3 text-zinc-500 text-xs font-medium">Rejected version</div>}
    </div>
  );
}
