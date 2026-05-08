"use client";

import { ChatMessage, AssetCard, ScriptData, StoryboardData } from "../lib/types";
import { StatusPillComponent } from "./StatusPill";
import { ErrorCardComponent } from "./ErrorCard";
import { ScriptCard } from "./cards/ScriptCard";
import { VoiceoverCard } from "./cards/VoiceoverCard";
import { StoryboardCard } from "./cards/StoryboardCard";
import { ImageCard } from "./cards/ImageCard";
import { VideoPromptCard } from "./cards/VideoPromptCard";
import { VideoCard } from "./cards/VideoCard";

// Map stage name → asset card subtypes that are active at that stage
const STAGE_ACTIVE_SUBTYPES: Record<string, string[]> = {
  script: ["script"],
  voiceover: ["voiceover"],
  storyboard: ["storyboard"],
  image_generation: ["image", "video_prompt"],
  video_generation: ["video", "video_prompt"],
};

interface Props {
  messages: ChatMessage[];
  sessionId: string;
  currentStage?: string;
  assemblyLocked?: boolean;
  onAction?: () => void;
  onWidgetAction?: (widgetType: string, option: string, msg: ChatMessage) => void;
}

function renderAssetCard(card: AssetCard, sessionId: string, onAction?: () => void, isActive = false) {
  const { subtype, iteration, data, status } = card;
  const props = { iteration, status, sessionId, onAction };

  switch (subtype) {
    case "script":
      return <ScriptCard data={data as unknown as ScriptData} {...props} isActive={isActive} />;
    case "voiceover":
      return <VoiceoverCard data={data as { audio_path: string; gender: string }} {...props} />;
    case "storyboard":
      return <StoryboardCard data={data as unknown as StoryboardData} {...props} />;
    case "image":
      return <ImageCard data={data as Parameters<typeof ImageCard>[0]["data"]} {...props} />;
    case "video_prompt":
      return <VideoPromptCard data={data as Parameters<typeof VideoPromptCard>[0]["data"]} {...props} />;
    case "video":
      return <VideoCard data={data as Parameters<typeof VideoCard>[0]["data"]} {...props} />;
    default:
      return <div className="text-zinc-500 text-xs">{subtype}: {JSON.stringify(data).slice(0, 100)}</div>;
  }
}

export function ChatHistoryComponent({ messages, sessionId, currentStage, assemblyLocked, onAction, onWidgetAction }: Props) {
  // Only asset cards whose subtype is active at the current stage get action buttons
  const activeSubtypes = new Set(currentStage ? (STAGE_ACTIVE_SUBTYPES[currentStage] ?? []) : []);

  // Within active subtypes, only the LAST card per subtype gets actions
  const lastCardBySubtype: Record<string, number> = {};
  messages.forEach((msg, i) => {
    if (msg.msg_type === "asset_card") {
      const card = msg as AssetCard;
      if (activeSubtypes.has(card.subtype)) lastCardBySubtype[card.subtype] = i;
    }
  });

  // Find the last app_question with buttons widget index
  let lastButtonsWidgetIdx = -1;
  messages.forEach((msg, i) => {
    if (msg.msg_type === "app_question") {
      const q = msg as { question: string; widget?: { type: string } };
      if (q.widget?.type === "buttons" || q.widget?.type === "voice_select") lastButtonsWidgetIdx = i;
    }
  });

  return (
    <div className="flex flex-col gap-3 py-4 px-4">
      {messages.map((msg, i) => {
        switch (msg.msg_type) {
          case "system":
            return (
              <p key={i} className="text-zinc-500 text-xs italic text-center">{(msg as { message: string }).message}</p>
            );

          case "status_pill":
            return <StatusPillComponent key={`${(msg as { pill_id: string }).pill_id}-${i}`} msg={msg} />;

          case "app_question": {
            const q = msg as { question: string; widget?: { type: string; options?: string[] } };
            const widget = q.widget;
            return (
              <div key={i} className="flex flex-col gap-2 max-w-lg">
                <div className="bg-zinc-800 rounded-2xl rounded-tl-sm px-4 py-2.5">
                  <p className="text-zinc-100 text-sm">{q.question}</p>
                </div>
                {/* Render button options for widgets that need user selection */}
                {(widget?.type === "buttons" || widget?.type === "voice_select") && widget.options && onWidgetAction && i === lastButtonsWidgetIdx && (
                  <div className="flex flex-wrap gap-2 pl-1">
                    {widget.options.map((opt) => (
                      <button
                        key={opt}
                        onClick={() => onWidgetAction(widget.type, opt, msg)}
                        className="px-4 py-2 bg-zinc-700 hover:bg-indigo-600 text-white rounded-lg text-sm border border-zinc-600 hover:border-indigo-500 transition-colors"
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          }

          case "user_reply":
            return (
              <div key={i} className="flex justify-end">
                <div className="bg-indigo-600 rounded-2xl rounded-tr-sm px-4 py-2.5 max-w-sm">
                  <p className="text-white text-sm">{(msg as { text: string }).text}</p>
                </div>
              </div>
            );

          case "asset_card": {
            const card = msg as AssetCard;
            const isActive = lastCardBySubtype[card.subtype] === i;
            return (
              <div key={i} className="w-full">
                {renderAssetCard(
                  card,
                  sessionId,
                  !assemblyLocked && (
                    isActive ||
                    card.status === "approved" ||
                    card.status === "previous" ||
                    card.status === "rejected" ||
                    (card.subtype === "script" && card.status === "pending_approval")
                  ) ? onAction : undefined,
                  isActive
                )}
              </div>
            );
          }

          case "error_card":
            if ("resolved" in msg && msg.resolved) return null;
            return (
              <div key={i} className="max-w-2xl w-full">
                <ErrorCardComponent msg={msg} sessionId={sessionId} onAction={onAction} />
              </div>
            );

          default:
            return null;
        }
      })}
    </div>
  );
}
