"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "../components/Sidebar";
import { api } from "../lib/api";

export default function Home() {
  const router = useRouter();
  const [creating, setCreating] = useState(false);

  const handleNew = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const meta = await api.createSession("New Session");
      router.push(`/sessions/${meta.session_id}`);
    } catch (e) {
      alert(`Failed to create session: ${e}`);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 flex items-center justify-center bg-zinc-950">
        <div className="text-center space-y-4">
          <h1 className="text-3xl font-bold text-white">Instomator</h1>
          <p className="text-zinc-400">Turn a name and photo into an Instagram reel</p>
          <button
            onClick={handleNew}
            disabled={creating}
            className="px-6 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-xl text-base font-medium transition-colors"
          >
            {creating ? "Creating…" : "+ New Session"}
          </button>
        </div>
      </main>
    </div>
  );
}
