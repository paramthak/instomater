"use client";

import { useState, useRef, KeyboardEvent } from "react";

interface Props {
  onSend: (text: string) => void;
  placeholder?: string;
  disabled?: boolean;
}

export function Composer({ onSend, placeholder = "Type a message…", disabled = false }: Props) {
  const [text, setText] = useState("");

  const handleSend = () => {
    if (!text.trim() || disabled) return;
    onSend(text.trim());
    setText("");
  };

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-zinc-800 p-3 bg-zinc-950">
      <div className="flex gap-2 items-end">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKey}
          placeholder={placeholder}
          disabled={disabled}
          rows={2}
          className="flex-1 bg-zinc-800 text-white rounded-xl px-4 py-2.5 text-sm resize-none border border-zinc-700 focus:outline-none focus:border-indigo-500 disabled:opacity-40"
        />
        <button
          onClick={handleSend}
          disabled={!text.trim() || disabled}
          className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-xl text-sm font-medium"
        >
          Send
        </button>
      </div>
    </div>
  );
}
