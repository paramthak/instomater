"use client";

import { StatusPill as StatusPillType } from "../lib/types";

export function StatusPillComponent({ msg }: { msg: StatusPillType }) {
  if (msg.resolved) {
    return (
      <div className="flex items-center gap-2 text-zinc-500 text-sm py-1 px-2">
        <span className="text-green-500">✓</span>
        <span className="italic">{msg.message.replace("…", "")}</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 text-zinc-400 text-sm py-1 px-2">
      <span className="inline-block w-3 h-3 border-2 border-zinc-500 border-t-indigo-400 rounded-full animate-spin" />
      <span className="italic">{msg.message}</span>
    </div>
  );
}
