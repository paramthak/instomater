"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "../../../components/Sidebar";
import { ChatHistoryComponent } from "../../../components/ChatHistory";
import { useSession } from "../../../hooks/useSession";
import { api } from "../../../lib/api";
import { CostLedger } from "../../../lib/types";

export default function FinalReviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { metadata, messages, loading: sessionLoading, reload } = useSession(id);
  const [costs, setCosts] = useState<CostLedger | null>(null);

  useEffect(() => {
    const loadCosts = async () => {
      try {
        setCosts(await api.getCosts(id));
      } catch {
        setCosts(null);
      }
    };
    void loadCosts();
  }, [id]);

  const version = metadata?.approval_state.final_reel?.version ?? 1;
  const [videoUrl, setVideoUrl] = useState("");
  useEffect(() => {
    setVideoUrl(api.assetUrl(id, `final/reel_v${version}.mp4`));
  }, [id, version]);

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
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
                <h3 className="text-white font-semibold">Costs</h3>
                {costs ? (
                  <div className="mt-3 space-y-3 text-sm">
                    <div className="flex justify-between text-zinc-100">
                      <span>Total</span>
                      <span>${costs.summary.total_usd.toFixed(4)}</span>
                    </div>
                    {Object.entries(costs.summary.by_provider ?? {}).map(([provider, value]) => (
                      <div key={provider} className="flex justify-between text-zinc-400 text-xs">
                        <span>{provider}</span>
                        <span>${value.toFixed(4)}</span>
                      </div>
                    ))}
                    <div className="pt-2 border-t border-zinc-800 text-zinc-500 text-xs">
                      {costs.summary.entry_count ?? costs.entries.length} provider attempts logged.
                    </div>
                  </div>
                ) : (
                  <p className="mt-2 text-zinc-500 text-sm">No cost ledger found.</p>
                )}
              </div>

              <div className="bg-zinc-900 border border-zinc-800 rounded-xl">
                <div className="px-4 py-3 border-b border-zinc-800">
                  <h3 className="text-white font-semibold">Conversation</h3>
                </div>
                <ChatHistoryComponent
                  messages={messages}
                  sessionId={id}
                  currentStage={metadata?.current_stage}
                  assemblyLocked={true}
                  onAction={reload}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
