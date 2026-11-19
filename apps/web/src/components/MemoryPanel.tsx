import { type FormEvent, useEffect, useState } from "react";
import { ApiError, addMemory, clearMemory, deleteMemory, listMemory } from "../lib/api";
import type { MemoryItemView } from "../lib/api";
import { errorCopy } from "../lib/copy";

export function MemoryPanel() {
  const [items, setItems] = useState<MemoryItemView[] | null>(null);
  const [kind, setKind] = useState<"preference" | "saved_definition">("preference");
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listMemory()
      .then(setItems)
      .catch((e) => {
        setItems([]);
        setError(errorCopy(e instanceof ApiError ? e.code : "INTERNAL"));
      });
  }, []);

  const fail = (e: unknown) => setError(errorCopy(e instanceof ApiError ? e.code : "INTERNAL"));

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const made = await addMemory(kind === "saved_definition" ? { kind, name: name.trim(), text } : { kind, text });
      setItems((all) => [...(all ?? []), made]);
      setText("");
      setName("");
    } catch (e) {
      fail(e);
    }
  }

  async function forget(id: string) {
    setError(null);
    try {
      await deleteMemory(id);
      setItems((all) => (all ?? []).filter((i) => i.pref_id !== id));
    } catch (e) {
      fail(e);
    }
  }

  async function forgetAll() {
    setError(null);
    try {
      await clearMemory();
      setItems([]);
    } catch (e) {
      fail(e);
    }
  }

  return (
    <section aria-label="What I remember" className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">What I remember about you</h2>
        <p className="text-xs text-slate-500">
          You control it. Nothing here is written by the assistant. It only shapes how your questions are read; it never
          widens what you can see.
        </p>
      </div>

      {items === null && <p className="text-sm text-slate-500">Loading…</p>}
      {items?.length === 0 && <p className="text-sm text-slate-500">Nothing is remembered.</p>}
      <ul className="space-y-2">
        {items?.map((i) => (
          <li key={i.pref_id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3 text-sm">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">
                {i.kind === "saved_definition" ? `Definition: ${i.name}` : "Preference"}
              </p>
              <p>{i.text}</p>
            </div>
            <button
              type="button"
              aria-label={`Forget: ${i.text}`}
              onClick={() => void forget(i.pref_id)}
              className="shrink-0 text-xs text-slate-500 hover:text-rose-700 hover:underline"
            >
              Forget
            </button>
          </li>
        ))}
      </ul>
      {items && items.length > 0 && (
        <button type="button" onClick={() => void forgetAll()} className="text-xs text-slate-500 hover:text-rose-700 hover:underline">
          Forget everything
        </button>
      )}

      <form onSubmit={(e) => void save(e)} className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
        <div className="flex flex-wrap gap-3">
          <label className="text-xs font-medium text-slate-600">
            Kind
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as "preference" | "saved_definition")}
              className="ml-2 rounded border border-slate-300 p-1 text-sm"
            >
              <option value="preference">Preference</option>
              <option value="saved_definition">Saved definition</option>
            </select>
          </label>
          {kind === "saved_definition" && (
            <label className="text-xs font-medium text-slate-600">
              Name
              <input
                value={name}
                maxLength={60}
                onChange={(e) => setName(e.target.value)}
                className="ml-2 rounded border border-slate-300 p-1 text-sm"
              />
            </label>
          )}
        </div>
        <label className="block text-xs font-medium text-slate-600" htmlFor="remember-text">
          What should I remember?
        </label>
        <input
          id="remember-text"
          value={text}
          maxLength={300}
          onChange={(e) => setText(e.target.value)}
          className="w-full rounded border border-slate-300 p-2 text-sm"
        />
        <button
          type="submit"
          disabled={!text.trim() || (kind === "saved_definition" && !name.trim())}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          Remember
        </button>
      </form>
      {error && (
        <p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-900">
          {error}
        </p>
      )}
    </section>
  );
}
