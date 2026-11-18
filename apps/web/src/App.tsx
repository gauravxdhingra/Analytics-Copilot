import { useEffect, useState } from "react";
import { Approvals } from "./components/Approvals";
import { Chat } from "./components/Chat";
import { Drafts } from "./components/Drafts";
import { getMe, logout } from "./lib/api";
import type { Me } from "./lib/types";

type Tab = "chat" | "drafts" | "approvals";

function SignIn() {
  return (
    <main className="mx-auto mt-24 max-w-sm space-y-4 rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
      <h1 className="text-xl font-semibold">Analytics Copilot</h1>
      <p className="text-sm text-slate-600">Ask questions about treasury data you are allowed to see.</p>
      {/* Sign-in happens at the identity provider; this app never sees a password. */}
      <a href="/auth/login" className="block rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
        Sign in
      </a>
    </main>
  );
}

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [tab, setTab] = useState<Tab>("chat");
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => {
        setUnavailable(true);
        setMe(null);
      });
  }, []);

  if (me === undefined) return <p className="p-8 text-sm text-slate-500">Loading…</p>;
  if (me === null)
    return (
      <>
        {unavailable && <p role="alert" className="bg-amber-50 p-3 text-center text-sm text-amber-900">Copilot is unavailable right now.</p>}
        <SignIn />
      </>
    );

  const tabs: Array<{ id: Tab; label: string }> = [
    { id: "chat", label: "Ask" },
    { id: "drafts", label: "My drafts" },
    ...(me.roles.includes("approver") ? [{ id: "approvals" as const, label: "Approvals" }] : []),
  ];

  async function signOut() {
    window.location.assign(await logout());
  }

  return (
    <div className="mx-auto flex h-screen max-w-4xl flex-col px-4 py-4">
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Analytics Copilot</h1>
          <p className="text-xs text-slate-500">
            <span className="font-medium text-slate-700">{me.username}</span> · {me.roles.join(", ")} · {me.entities.join(", ") || "no entities"}
          </p>
        </div>
        <button type="button" onClick={() => void signOut()} className="text-sm text-slate-600 hover:underline">
          Sign out
        </button>
      </header>
      <div role="tablist" className="mb-4 flex gap-1 border-b border-slate-200">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm ${tab === t.id ? "border-b-2 border-indigo-600 font-medium text-indigo-700" : "text-slate-600"}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <main className="min-h-0 flex-1">
        {tab === "chat" && <Chat onSignedOut={() => setMe(null)} />}
        {tab === "drafts" && <Drafts />}
        {tab === "approvals" && <Approvals />}
      </main>
    </div>
  );
}
