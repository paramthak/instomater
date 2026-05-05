"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "../../../components/Sidebar";
import { ChatHistoryComponent } from "../../../components/ChatHistory";
import { Composer } from "../../../components/Composer";
import { useSession } from "../../../hooks/useSession";
import { api } from "../../../lib/api";

const STAGE_LABELS: Record<string, string> = {
  topic_brief: "Stage 1 of 11 — Topic Brief",
  photo_upload: "Stage 2 of 11 — Photo Upload",
  script: "Stage 3 of 11 — Script",
  voiceover: "Stage 4 of 11 — Voiceover",
  alignment: "Stage 5 of 11 — Alignment",
  storyboard: "Stage 6 of 11 — Storyboard",
  clarifying_questions: "Stage 7 of 11 — Clarifying Questions",
  image_generation: "Stage 8 of 11 — Image Generation",
  video_generation: "Stage 9 of 11 — Video Generation",
  assembly: "Stage 10 of 11 — Assembly",
  final_review: "Stage 11 of 11 — Final Review",
};

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { metadata, messages, loading, reload } = useSession(id);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [sessionStartPending, setSessionStartPending] = useState(false);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Redirect to final review when done
  useEffect(() => {
    if (metadata?.current_stage === "final_review") {
      router.push(`/final/${id}`);
    }
  }, [metadata?.current_stage, id, router]);

  const stage = metadata?.current_stage;
  const sessionStarted = sessionStartPending || messages.length > 0 || (metadata?.completed_stages?.length ?? 0) > 0;

  // Conditions for showing inline widgets
  const showNameComposer = !sessionStarted && stage === "topic_brief";
  const showPhotoUpload = stage === "photo_upload" && !messages.some(
    (m) => m.msg_type === "app_question" &&
      (m as { widget?: { type: string } }).widget?.type === "photo_confirm"
  );
  const showPhotoConfirm = messages.some(
    (m) => m.msg_type === "app_question" &&
      (m as { widget?: { type: string } }).widget?.type === "photo_confirm"
  ) && stage === "photo_upload" && !metadata?.completed_stages?.includes("photo_upload");

  // Handle initial name submission
  const handleComposer = async (text: string) => {
    if (!text.trim()) return;
    setSessionStartPending(true);
    try {
      await api.startSession(id, text.trim());
      await reload();
    } catch (e) {
      setSessionStartPending(false);
      alert(`Failed to start session: ${e}`);
    }
  };

  // Handle photo file selection
  const handlePhotoSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) setPhotoFile(file);
  };

  // Handle photo upload
  const handlePhotoUpload = async () => {
    if (!photoFile) return;
    setUploadLoading(true);
    try {
      await api.uploadPhoto(id, photoFile);
      setPhotoFile(null);
      // Reset file input so the same file can be re-selected if needed
      if (fileInputRef.current) fileInputRef.current.value = "";
      reload();
    } catch (e) {
      alert(`Upload failed: ${e}`);
    } finally {
      setUploadLoading(false);
    }
  };

  // Handle "Use this photo" confirmation
  const handlePhotoConfirm = async () => {
    setUploadLoading(true);
    try {
      await api.confirmPhoto(id);
      reload();
    } catch (e) {
      alert(`Failed to confirm photo: ${e}`);
    } finally {
      setUploadLoading(false);
    }
  };

  // Handle widget button clicks (voice selection, etc.)
  const handleWidgetAction = useCallback(async (widgetType: string, option: string) => {
    if (widgetType === "buttons") {
      // Determine action from option value
      const lowerOpt = option.toLowerCase();
      if (lowerOpt === "male" || lowerOpt === "female") {
        setActionLoading(true);
        try {
          await api.sendAction(id, "select_voice", "voiceover", { gender: lowerOpt });
          reload();
        } catch (e) {
          alert(`Failed to select voice: ${e}`);
        } finally {
          setActionLoading(false);
        }
      }
      if (lowerOpt === "assemble") {
        setActionLoading(true);
        try {
          await api.startAssembly(id);
          reload();
        } catch (e) {
          alert(`Failed to start assembly: ${e}`);
        } finally {
          setActionLoading(false);
        }
      }
    }
  }, [id, reload]);

  if (loading && !metadata) {
    return (
      <div className="flex h-screen items-center justify-center bg-zinc-950">
        <div className="text-zinc-400">Loading session…</div>
      </div>
    );
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 bg-zinc-950">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800 shrink-0">
          <span className="text-white font-medium">{metadata?.person_name ?? "Session"}</span>
          <span className="text-zinc-500 text-sm">{STAGE_LABELS[stage ?? ""] ?? ""}</span>
        </div>

        {/* Chat area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto">

          {/* Initial prompt before session starts */}
          {!sessionStarted && messages.length === 0 && (
            <div className="flex flex-col items-start p-4">
              <div className="bg-zinc-800 rounded-2xl rounded-tl-sm px-4 py-2.5 max-w-lg">
                <p className="text-zinc-100 text-sm">Who is the person? Type their name and any extra context (role, company, etc.)</p>
              </div>
            </div>
          )}

          <ChatHistoryComponent
            messages={messages}
            sessionId={id}
            currentStage={stage}
            onAction={reload}
            onWidgetAction={handleWidgetAction}
          />

          {/* Photo upload widget */}
          {showPhotoUpload && (
            <div className="px-4 pb-4">
              <div
                className="bg-zinc-800/60 border-2 border-dashed border-zinc-600 rounded-xl p-8 max-w-sm text-center cursor-pointer hover:border-zinc-500 transition-colors"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const file = e.dataTransfer.files?.[0];
                  if (file) setPhotoFile(file);
                }}
              >
                <div className="text-4xl mb-3">📷</div>
                <p className="text-zinc-400 text-sm mb-1">Drag & drop or click to select</p>
                <p className="text-zinc-600 text-xs">JPEG, PNG, WebP — min 512px, max 10 MB</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/heic"
                  className="hidden"
                  onChange={handlePhotoSelect}
                />
                {photoFile && (
                  <div className="mt-4 space-y-2" onClick={(e) => e.stopPropagation()}>
                    <p className="text-zinc-300 text-xs">Selected: {photoFile.name}</p>
                    <button
                      onClick={handlePhotoUpload}
                      disabled={uploadLoading}
                      className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
                    >
                      {uploadLoading ? "Uploading…" : "Upload Photo"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Photo confirm widget */}
          {showPhotoConfirm && metadata?.photo_ext && (
            <div className="px-4 pb-4">
              <div className="flex gap-4 items-start max-w-sm">
                <div className="w-28 h-28 rounded-xl overflow-hidden bg-zinc-800 shrink-0">
                  <img
                    src={api.assetUrl(id, `uploaded_photo.${metadata.photo_ext}`)}
                    alt="Uploaded photo"
                    className="w-full h-full object-cover"
                  />
                </div>
                <div className="flex flex-col gap-2 justify-center pt-1">
                  <button
                    onClick={handlePhotoConfirm}
                    disabled={uploadLoading}
                    className="px-4 py-2 bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
                  >
                    {uploadLoading ? "Confirming…" : "✓ Use this photo"}
                  </button>
                  <button
                    onClick={() => {
                      setPhotoFile(null);
                      if (fileInputRef.current) fileInputRef.current.value = "";
                      fileInputRef.current?.click();
                    }}
                    className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm"
                  >
                    Re-upload
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Global action loading indicator */}
          {actionLoading && (
            <div className="px-4 pb-2 text-zinc-500 text-xs animate-pulse">Processing…</div>
          )}
        </div>

        {/* Name input composer — only shown before session starts */}
        {showNameComposer && (
          <Composer
            onSend={handleComposer}
            placeholder="e.g. Sundar Pichai, CEO of Google…"
          />
        )}
      </div>
    </div>
  );
}
