import { useEffect, useState } from "react";
import { ApiError, listDrafts, submitDraft } from "../lib/api";
import { errorCopy } from "../lib/copy";
import type { Draft } from "../lib/types";

export function Drafts() {
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDrafts()
      .then(setDrafts)
      .catch((e) => {
        setDrafts([]);
        setError(errorCopy(e instanceof ApiError ? e.code : "INTERNAL"));
      });
  }, []);

  async function submit(id: string) {
    try {
      const updated = await submitDraft(id);
      setDrafts((all) => (all ?? []).map((d) => (d.draft_id === id ? updated : d)));
    } catch (e) {
      setError(errorCopy(e instanceof ApiError ? e.code : "INTERNAL"));
    }
  }

  return (
    <section aria-label="My drafts" className="space-y-3">
      <h2 className="text-sm font-semibold">Drafts the copilot prepared for you</h2>
      {error && <p role="alert" className="text-sm text-rose-800">{error}</p>}
      {drafts?.length === 0 && <p className="text-sm text-slate-500">No drafts yet. Ask the copilot to draft a review.</p>}
      <ul className="space-y-2">
        {drafts?.map((d) => (
          <li key={d.draft_id} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white p-3 text-sm">
            <div>
              <p className="font-medium">{d.title}</p>
              <p className="text-xs text-slate-500">{d.entity_id} · {d.status}</p>
            </div>
            {d.status === "draft" && (
              <button type="button" onClick={() => void submit(d.draft_id)} className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50">
                Submit for approval
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
