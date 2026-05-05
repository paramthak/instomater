"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "../../../components/Sidebar";
import { ChatHistoryComponent } from "../../../components/ChatHistory";
import { useSession } from "../../../hooks/useSession";
import { api } from "../../../lib/api";
import { StoryboardData } from "../../../lib/types";

type SidePanel = "history" | "clips";

export default function FinalReviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { metadata, messages, loading: sessionLoading, reload } = useSession(id);
  const [storyboard, setStoryboard] = useState<StoryboardData | null>(null);
  const [assemblyLoading, setAssemblyLoading] = useState(false);
  const [sidePanel, setSidePanel] = useState<SidePanel>("history");

  useEffect(() => {
    const loadStoryboard = async () => {
      try {
        const res = await fetch(api.assetUrl(id, "storyboard_approved.json"));
        if (res.ok) setStoryboard(await res.json());
      } catch {
        setStoryboard(null);
      }
    };
    void loadStoryboard();
  }, [id]);

  const handleRedoClip = async (clipIndex: number) => {
    await api.redoClip(id, clipIndex);
    router.push(`/sessions/${id}`);
  };

  const handleReassemble = async () => {
    setAssemblyLoading(true);
    const previousVersion = metadata?.approval_state.final_reel?.version ?? 0;
    try {
      await api.startAssembly(id);
      const check = setInterval(async () => {
        const { metadata: meta } = await api.getSession(id);
        const nextVersion = meta.approval_state.final_reel?.version ?? 0;
        if (meta.approval_state.final_reel?.assembled && nextVersion > previousVersion) {
          clearInterval(check);
          await reload();
          setAssemblyLoading(false);
        }
      }, 3000);
    } catch (e) {
      alert(`Assembly failed: ${e}`);
      setAssemblyLoading(false);
    }
  };

  const version = metadata?.approval_state.final_reel?.version ?? 1;
  const videoUrl = api.assetUrl(id, `final/reel_v${version}.mp4`);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800 bg-zinc-950 shrink-0">
          <span className="text-white font-medium">
            {metadata?.person_name} — Final Review
          </span>
          <div className="flex items-center gap-3">
            {assemblyLoading && <span className="text-zinc-400 text-sm animate-pulse">Assembling…</span>}
            <button
              onClick={handleReassemble}
              disabled={assemblyLoading || sessionLoading}
              className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white rounded-lg text-sm"
            >
              Re-assemble
            </button>
            <a
              href={videoUrl}
              download={`${metadata?.person_name ?? "reel"}_v${version}.mp4`}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium"
            >
              Download MP4
            </a>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 flex overflow-hidden">
          {/* Video player — left 60% */}
          <div className="w-3/5 flex items-center justify-center bg-zinc-900 p-6">
            <div className="aspect-[9/16] h-full max-h-full rounded-xl overflow-hidden bg-black">
              <video
                key={videoUrl}
                src={videoUrl}
                controls
                className="w-full h-full object-contain"
              />
            </div>
          </div>

          {/* Review panel — right 40% */}
          <div className="w-2/5 min-w-[360px] border-l border-zinc-800 bg-zinc-950 flex flex-col">
            <div className="p-3 border-b border-zinc-800 shrink-0">
              <div className="grid grid-cols-2 rounded-lg bg-zinc-900 p-1">
                <button
                  onClick={() => setSidePanel("history")}
                  className={`px-3 py-2 rounded-md text-sm transition-colors ${
                    sidePanel === "history"
                      ? "bg-zinc-700 text-white"
                      : "text-zinc-400 hover:text-white"
                  }`}
                >
                  Conversation
                </button>
                <button
                  onClick={() => setSidePanel("clips")}
                  className={`px-3 py-2 rounded-md text-sm transition-colors ${
                    sidePanel === "clips"
                      ? "bg-zinc-700 text-white"
                      : "text-zinc-400 hover:text-white"
                  }`}
                >
                  Clips
                </button>
              </div>
            </div>

            {sidePanel === "history" ? (
              <div className="flex-1 overflow-y-auto">
                <ChatHistoryComponent
                  messages={messages}
                  sessionId={id}
                  currentStage={metadata?.current_stage}
                  onAction={reload}
                />
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                <h3 className="text-zinc-400 text-xs uppercase tracking-wider">Clips</h3>
                {storyboard?.scenes.map((scene, i) => {
                  const clipIndex = i + 1;
                  const clipPath = `videos/clip_${String(clipIndex).padStart(2, "0")}_approved.mp4`;
                  return (
                    <div key={clipIndex} className="bg-zinc-800 rounded-xl overflow-hidden">
                      <div className="flex gap-3 p-3">
                        <div className="w-16 aspect-[9/16] bg-zinc-900 rounded overflow-hidden shrink-0">
                          <video
                            src={api.assetUrl(id, clipPath)}
                            className="w-full h-full object-cover"
                            muted
                          />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-white text-sm font-medium">Clip {clipIndex} of {storyboard.total_scenes}</p>
                          <p className="text-zinc-400 text-xs mt-0.5">{scene.duration_seconds}s</p>
                          <p className="text-zinc-500 text-xs mt-1">
                            in: {scene.transition_in} · out: {scene.transition_out} {scene.transition_duration_seconds}s
                          </p>
                          <button
                            onClick={() => handleRedoClip(clipIndex)}
                            className="mt-2 px-3 py-1 bg-zinc-700 hover:bg-zinc-600 text-white rounded text-xs"
                          >
                            Re-do this clip
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
                {!storyboard && (
                  <div className="text-zinc-500 text-sm">No approved storyboard found.</div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
