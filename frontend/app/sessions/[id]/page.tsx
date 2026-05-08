"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "../../../components/Sidebar";
import { ChatHistoryComponent } from "../../../components/ChatHistory";
import { Composer } from "../../../components/Composer";
import { useSession } from "../../../hooks/useSession";
import { api } from "../../../lib/api";

const STICK_TO_BOTTOM_THRESHOLD_PX = 96;
const USER_SCROLL_INTENT_MS = 2500;
const SCROLL_RECONCILE_MS = 300;

const STAGE_LABELS: Record<string, string> = {
  script: "Stage 1 of 9 — Script",
  photo_upload: "Stage 2 of 9 — Photo Upload",
  voiceover: "Stage 3 of 9 — Voiceover",
  alignment: "Stage 4 of 9 — Alignment",
  storyboard: "Stage 5 of 9 — Storyboard",
  image_generation: "Stage 6 of 9 — Image Generation",
  video_generation: "Stage 7 of 9 — Video Generation",
  assembly: "Stage 8 of 9 — Assembly",
  final_review: "Stage 9 of 9 — Final Review",
};

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { metadata, messages, loading, reload } = useSession(id);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const shouldStickToBottomRef = useRef(true);
  const hasLoadedMessagesRef = useRef(false);
  const previousMessageSignatureRef = useRef("");
  const previousMessageCountRef = useRef(0);
  const lastScrollTopRef = useRef(0);
  const lastUserScrollAtRef = useRef(0);

  const [sessionStartPending, setSessionStartPending] = useState(false);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [customVoiceId, setCustomVoiceId] = useState("");
  const [voiceSpeed, setVoiceSpeed] = useState(1.2);

  const isNearBottom = useCallback((el: HTMLDivElement) => (
    el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_TO_BOTTOM_THRESHOLD_PX
  ), []);

  const isEditableElement = useCallback((el: Element) => (
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLInputElement ||
    el instanceof HTMLSelectElement ||
    (el instanceof HTMLElement && el.isContentEditable)
  ), []);

  const isEditingInsideChat = useCallback(() => {
    const active = document.activeElement;
    if (!active || !scrollRef.current?.contains(active)) return false;
    return isEditableElement(active);
  }, [isEditableElement]);

  const blurNonEditableFocusInsideChat = useCallback(() => {
    const active = document.activeElement;
    if (!active || !scrollRef.current?.contains(active) || isEditableElement(active)) return;
    if (active instanceof HTMLElement) active.blur();
  }, [isEditableElement]);

  const markChatScrollIntent = useCallback(() => {
    lastUserScrollAtRef.current = Date.now();
    blurNonEditableFocusInsideChat();
  }, [blurNonEditableFocusInsideChat]);

  const restoreReaderScroll = useCallback((el: HTMLDivElement) => {
    const maxScrollTop = Math.max(0, el.scrollHeight - el.clientHeight);
    el.scrollTop = Math.min(lastScrollTopRef.current, maxScrollTop);
  }, []);

  const reconcileChatScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    const userInitiated = Date.now() - lastUserScrollAtRef.current < USER_SCROLL_INTENT_MS;
    const movedAwayFromBottom = el.scrollTop < lastScrollTopRef.current - 4;
    const movedTowardBottom = el.scrollTop > lastScrollTopRef.current + 4;

    if (!userInitiated && !shouldStickToBottomRef.current && movedTowardBottom) {
      restoreReaderScroll(el);
      return;
    }

    if (userInitiated || movedAwayFromBottom || shouldStickToBottomRef.current) {
      shouldStickToBottomRef.current = isNearBottom(el);
      lastScrollTopRef.current = el.scrollTop;
    }
  }, [isNearBottom, restoreReaderScroll]);

  const handleChatScroll = useCallback(() => {
    reconcileChatScroll();
  }, [reconcileChatScroll]);

  useEffect(() => {
    const interval = window.setInterval(reconcileChatScroll, SCROLL_RECONCILE_MS);
    return () => window.clearInterval(interval);
  }, [reconcileChatScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const handleNativeIntent = () => markChatScrollIntent();
    const handleNativeScroll = () => reconcileChatScroll();

    el.addEventListener("wheel", handleNativeIntent, { capture: true, passive: true });
    el.addEventListener("touchmove", handleNativeIntent, { capture: true, passive: true });
    el.addEventListener("pointerdown", handleNativeIntent, { capture: true });
    el.addEventListener("keydown", handleNativeIntent, { capture: true });
    el.addEventListener("scroll", handleNativeScroll, { passive: true });

    return () => {
      el.removeEventListener("wheel", handleNativeIntent, { capture: true });
      el.removeEventListener("touchmove", handleNativeIntent, { capture: true });
      el.removeEventListener("pointerdown", handleNativeIntent, { capture: true });
      el.removeEventListener("keydown", handleNativeIntent, { capture: true });
      el.removeEventListener("scroll", handleNativeScroll);
    };
  }, [markChatScrollIntent, reconcileChatScroll]);

  useEffect(() => {
    shouldStickToBottomRef.current = true;
    hasLoadedMessagesRef.current = false;
    previousMessageSignatureRef.current = "";
    previousMessageCountRef.current = 0;
    lastScrollTopRef.current = 0;
    lastUserScrollAtRef.current = 0;
  }, [id]);

  // Follow new messages only when the reader is already at the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || messages.length === 0) return;

    const signature = messages.map((msg) => `${msg.msg_type}:${msg.timestamp}`).join("|");
    const previousSignature = previousMessageSignatureRef.current;
    const previousCount = previousMessageCountRef.current;
    const firstLoad = !hasLoadedMessagesRef.current;
    const hasChanged = previousSignature !== signature;
    const appendedMessage = previousCount > 0 && messages.length > previousCount;
    const wasAtBottom = shouldStickToBottomRef.current;
    const isAtBottom = isNearBottom(el);
    const shouldFollow = firstLoad || (
      hasChanged &&
      !isEditingInsideChat() &&
      (isAtBottom || (wasAtBottom && appendedMessage))
    );

    hasLoadedMessagesRef.current = true;
    previousMessageSignatureRef.current = signature;
    previousMessageCountRef.current = messages.length;

    if (!shouldFollow) {
      shouldStickToBottomRef.current = isAtBottom;
      if (!isAtBottom) restoreReaderScroll(el);
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
      shouldStickToBottomRef.current = true;
      lastScrollTopRef.current = el.scrollTop;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [isEditingInsideChat, isNearBottom, messages, restoreReaderScroll]);

  // Redirect to final review when done
  useEffect(() => {
    if (metadata?.current_stage === "final_review") {
      router.push(`/final/${id}`);
    }
  }, [metadata?.current_stage, id, router]);

  const stage = metadata?.current_stage;
  const sessionStarted = sessionStartPending || messages.length > 0 || (metadata?.completed_stages?.length ?? 0) > 0;

  // Conditions for showing inline widgets
  const showNameComposer = !sessionStarted && stage === "script";
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
    if (widgetType === "buttons" || widgetType === "voice_select") {
      // Determine action from option value
      const lowerOpt = option.toLowerCase();
      if (lowerOpt === "male" || lowerOpt === "female") {
        setActionLoading(true);
        try {
          await api.sendAction(id, "select_voice", "voiceover", { gender: lowerOpt, speed: voiceSpeed });
          reload();
        } catch (e) {
          alert(`Failed to select voice: ${e}`);
        } finally {
          setActionLoading(false);
        }
      }
      if (lowerOpt === "avengers assemble") {
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
  }, [id, reload, voiceSpeed]);

  const toggleAutopilot = async (enabled: boolean) => {
    setActionLoading(true);
    try {
      await api.sendAction(id, "autopilot", "settings", { enabled, voice_speed: voiceSpeed });
      await reload();
    } catch (e) {
      alert(`Failed to update Autopilot: ${e}`);
    } finally {
      setActionLoading(false);
    }
  };

  const submitCustomVoice = async () => {
    if (!customVoiceId.trim()) return;
    setActionLoading(true);
    try {
      await api.sendAction(id, "select_voice", "voiceover", { voice_id: customVoiceId.trim(), speed: voiceSpeed });
      setCustomVoiceId("");
      await reload();
    } catch (e) {
      alert(`Failed to use voice ID: ${e}`);
    } finally {
      setActionLoading(false);
    }
  };

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
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-zinc-400">
              <input
                type="checkbox"
                checked={Boolean(metadata?.settings?.autopilot_enabled)}
                disabled={Boolean(metadata?.assembly_locked)}
                onChange={(e) => toggleAutopilot(e.target.checked)}
              />
              Autopilot
            </label>
            <span className="text-zinc-500 text-sm">{metadata?.assembly_locked ? "Locked after Avengers Assemble" : STAGE_LABELS[stage ?? ""] ?? ""}</span>
          </div>
        </div>

        {/* Chat area */}
        <div
          ref={scrollRef}
          onScroll={handleChatScroll}
          onWheelCapture={markChatScrollIntent}
          onTouchMoveCapture={markChatScrollIntent}
          onPointerDownCapture={markChatScrollIntent}
          onKeyDownCapture={markChatScrollIntent}
          className="flex-1 overflow-y-auto"
        >

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
            assemblyLocked={metadata?.assembly_locked}
            onAction={reload}
            onWidgetAction={handleWidgetAction}
          />

          {stage === "voiceover" && !metadata?.approval_state.voiceover.iterations && !metadata?.assembly_locked && (
            <div className="px-4 pb-4 max-w-lg space-y-2">
              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <span>Speed</span>
                <input
                  type="number"
                  min="0.7"
                  max="1.2"
                  step="0.05"
                  value={voiceSpeed}
                  onChange={(e) => setVoiceSpeed(Number(e.target.value))}
                  className="w-20 rounded bg-zinc-900 border border-zinc-700 px-2 py-1 text-zinc-100"
                />
              </div>
              <div className="flex gap-2">
                <input
                  value={customVoiceId}
                  onChange={(e) => setCustomVoiceId(e.target.value)}
                  placeholder="Paste ElevenLabs voice ID"
                  className="min-w-0 flex-1 rounded bg-zinc-900 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
                />
                <button
                  onClick={submitCustomVoice}
                  disabled={!customVoiceId.trim() || actionLoading}
                  className="px-3 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white rounded-lg text-sm"
                >
                  Use ID
                </button>
              </div>
            </div>
          )}

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
