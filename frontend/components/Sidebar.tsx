"use client";

import { useState, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { SessionListItem } from "../lib/types";
import { api } from "../lib/api";

const STAGE_LABELS: Record<string, string> = {
  topic_brief: "Topic brief",
  photo_upload: "Photo upload",
  script: "Script",
  voiceover: "Voiceover",
  alignment: "Alignment",
  storyboard: "Storyboard",
  clarifying_questions: "Clarifying questions",
  image_generation: "Generating images",
  video_generation: "Generating videos",
  assembly: "Assembly",
  final_review: "Final review",
};

export function Sidebar() {
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [backendUp, setBackendUp] = useState(true);
  const [creating, setCreating] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    const load = async () => {
      try {
        await api.health();
        setBackendUp(true);
        const data = await api.listSessions();
        setSessions(data);
      } catch {
        setBackendUp(false);
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleNew = async () => {
    if (creating) return;
    setCreating(true);
    try {
      // Create with placeholder name — user will set real name in the chat
      const meta = await api.createSession("New Session");
      router.push(`/sessions/${meta.session_id}`);
    } catch (e) {
      alert(`Failed to create session: ${e}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm("Delete this session? This cannot be undone.")) return;
    const prev = sessions;
    setSessions((s) => s.filter((x) => x.session_id !== id));
    try {
      await api.deleteSession(id);
      if (pathname?.includes(id)) router.push("/");
    } catch (e) {
      setSessions(prev); // revert on error
      alert(`Failed to delete: ${e}`);
    }
  };

  if (collapsed) {
    return (
      <div className="w-10 h-full bg-zinc-900 flex flex-col items-center py-3 border-r border-zinc-800">
        <button onClick={() => setCollapsed(false)} className="text-zinc-400 hover:text-white text-sm">▶</button>
      </div>
    );
  }

  return (
    <div className="w-64 h-full bg-zinc-900 flex flex-col border-r border-zinc-800 shrink-0">
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <span className="text-white font-semibold tracking-tight">Instomator</span>
        <button onClick={() => setCollapsed(true)} className="text-zinc-400 hover:text-white text-xs">◀</button>
      </div>

      {!backendUp && (
        <div className="mx-3 mt-3 px-3 py-2 bg-red-900/50 border border-red-700 rounded text-red-300 text-xs">
          Backend not running. Start: <code className="font-mono">uvicorn main:app --port 8000</code>
        </div>
      )}

      <div className="px-3 pt-3">
        <button
          onClick={handleNew}
          disabled={creating || !backendUp}
          className="w-full py-2 px-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-md text-sm font-medium transition-colors"
        >
          {creating ? "Creating…" : "+ New Session"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto mt-3 px-2">
        {sessions.length === 0 ? (
          <p className="text-zinc-500 text-xs px-2 mt-2">No sessions yet. Click + New Session to start.</p>
        ) : (
          sessions.map((s) => {
            const active = pathname?.includes(s.session_id);
            const done = s.current_stage === "final_review";
            return (
              <div
                key={s.session_id}
                onClick={() => router.push(`/sessions/${s.session_id}`)}
                className={`flex items-start justify-between p-2 rounded-md cursor-pointer mb-1 group ${active ? "bg-zinc-700" : "hover:bg-zinc-800"}`}
              >
                <div className="min-w-0">
                  <p className="text-white text-sm truncate">{s.person_name}</p>
                  <p className="text-zinc-400 text-xs mt-0.5 flex items-center gap-1">
                    {done && <span className="text-green-400">✓</span>}
                    {STAGE_LABELS[s.current_stage] ?? s.current_stage}
                  </p>
                </div>
                <button
                  onClick={(e) => handleDelete(e, s.session_id)}
                  className="opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-red-400 text-xs ml-2 mt-0.5 shrink-0"
                >
                  ✕
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
