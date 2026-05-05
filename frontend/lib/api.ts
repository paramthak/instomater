import { SessionMetadata, SessionListItem, ChatMessage } from "./types";

function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) return process.env.NEXT_PUBLIC_API_BASE;
  if (typeof window === "undefined") return "http://127.0.0.1:8000";
  return `${window.location.protocol}//${window.location.hostname}:8000`;
}

const BASE = apiBase();

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export const api = {
  // ── Health ─────────────────────────────────────────────────────────────────
  health: () => req<{ status: string }>("GET", "/health"),

  // ── Sessions ───────────────────────────────────────────────────────────────
  createSession: (name: string, context?: string) =>
    req<SessionMetadata>("POST", "/sessions", { name, context }),

  listSessions: () => req<SessionListItem[]>("GET", "/sessions"),

  getSession: (id: string) =>
    req<{ metadata: SessionMetadata; chat_history: ChatMessage[] }>("GET", `/sessions/${id}`),

  deleteSession: (id: string) => req<void>("DELETE", `/sessions/${id}`),

  // ── Photo ──────────────────────────────────────────────────────────────────
  uploadPhoto: async (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/sessions/${id}/photo`, { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? `HTTP ${res.status}`);
    }
    return res.json();
  },

  confirmPhoto: (id: string) => req<{ status: string }>("POST", `/sessions/${id}/photo/confirm`),

  // ── Session start (who is this person) ────────────────────────────────────
  startSession: (id: string, name: string, context?: string) =>
    req<{ status: string }>("POST", `/sessions/${id}/start`, { name, context }),

  // ── Stage actions ──────────────────────────────────────────────────────────
  sendAction: (id: string, action: string, stage: string, payload: Record<string, unknown> = {}) =>
    req<Record<string, unknown>>("POST", `/sessions/${id}/action`, { action, stage, payload }),

  // ── Assembly ───────────────────────────────────────────────────────────────
  startAssembly: (id: string) => req<{ status: string }>("POST", `/sessions/${id}/assemble`),

  // ── Redo clip ──────────────────────────────────────────────────────────────
  redoClip: (id: string, clipIndex: number) =>
    req<{ status: string }>("POST", `/sessions/${id}/redo-clip/${clipIndex}`),

  // ── Asset URL helper ───────────────────────────────────────────────────────
  assetUrl: (id: string, path: string) => `${BASE}/sessions/${id}/assets/${path}`,
};
