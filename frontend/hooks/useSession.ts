"use client";

import { useState, useEffect, useCallback } from "react";
import { SessionMetadata, ChatMessage, WSMessage } from "../lib/types";
import { api } from "../lib/api";
import { useWebSocket } from "./useWebSocket";

export function useSession(sessionId: string | null) {
  const [metadata, setMetadata] = useState<SessionMetadata | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const { metadata: meta, chat_history } = await api.getSession(sessionId);
      setMetadata(meta);
      setMessages(chat_history as ChatMessage[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load session");
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  const handleWS = useCallback((msg: WSMessage) => {
    if (msg.type === "status") {
      const pill: ChatMessage = {
        msg_type: "status_pill",
        pill_id: msg.pill_id ?? `pill_${Date.now()}`,
        message: msg.message ?? "",
        stage: msg.stage,
        substage_index: msg.substage_index ?? null,
        resolved: false,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => {
        // Replace existing pill with same pill_id if present
        if (msg.pill_id) {
          const idx = prev.findIndex(
            (m) => m.msg_type === "status_pill" && (m as typeof pill).pill_id === msg.pill_id
          );
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = pill;
            return next;
          }
        }
        return [...prev, pill];
      });
      void Promise.resolve().then(load);
    }

    if (msg.type === "asset_ready") {
      // Mark any matching pill as resolved, then reload full session
      if (msg.pill_id) {
        setMessages((prev) =>
          prev.map((m) =>
            m.msg_type === "status_pill" && (m as { pill_id: string }).pill_id === msg.pill_id
              ? { ...m, resolved: true }
              : m
          )
        );
      }
      load();
    }

    if (msg.type === "error") {
      if (msg.pill_id) {
        setMessages((prev) =>
          prev.map((m) =>
            m.msg_type === "status_pill" && (m as { pill_id: string }).pill_id === msg.pill_id
              ? { ...m, resolved: true }
              : m
          )
        );
      }
      load(); // reload to show error card
    }
  }, [load]);

  useWebSocket(sessionId, handleWS);

  return { metadata, messages, loading, error, reload: load };
}
