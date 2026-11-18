import { useEffect, useState } from "react";
import { ApiError, approvalQueue, decide } from "../lib/api";
import { errorCopy } from "../lib/copy";
import type { Draft } from "../lib/types";

export function Approvals() {
  const [queue, setQueue] = useState<Draft[] | null>(null);
  const [selected, setSelected] = useState<Draft | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<{ code: string; stepUpUrl?: string } | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    approvalQueue()
      .then(setQueue)
      .catch((e) => {
        setQueue([]);
        setError({ code: e instanceof ApiError ? e.code : "INTERNAL" });
      });
  }, []);

  async function submit(decision: "approve" | "reject") {
    if (!selected) return;
    setError(null);
    try {
      await decide(selected.draft_id, decision, comment || undefined);
      setDone(`Draft ${decision === "approve" ? "approved" : "rejected"}.`);
      setQueue((q) => (q ?? []).filter((d) => d.draft_id !== selected.draft_id));
      setSelected(null);
      setComment("");
    } catch (e) {
      setError(e instanceof ApiError ? { code: e.code, stepUpUrl: e.stepUpUrl } : { code: "INTERNAL" });
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-[18rem_1fr]">
      <section aria-label="Approval queue">
        <h2 className="mb-2 text-sm font-semibold">Waiting for a decision</h2>
        {queue === null && <p className="text-sm text-slate-500">Loading…</p>}
        {queue?.length === 0 && <p className="text-sm text-slate-500">Nothing is waiting.</p>}
        <ul className="space-y-2">
          {queue?.map((d) => (
            <li key={d.draft_id}>
              <button
                type="button"
                onClick={() => {
                  setSelected(d);
                  setError(null);
                  setDone(null);
                }}
                className={`w-full rounded-lg border p-3 text-left text-sm ${selected?.draft_id === d.draft_id ? "border-indigo-400 bg-indigo-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}
              >
                <span className="block font-medium">{d.title}</span>
                <span className="text-xs text-slate-500">{d.entity_id}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Draft" className="rounded-lg border border-slate-200 bg-white p-4">
        {done && <p className="mb-3 text-sm text-emerald-700">{done}</p>}
        {!selected && !done && <p className="text-sm text-slate-500">Select a draft to review it.</p>}
        {selected && (
          <div className="space-y-3 text-sm">
            <h3 className="text-base font-semibold">{selected.title}</h3>
            <p>{selected.payload.summary}</p>
            {selected.payload.recommended_actions.length > 0 && (
              <ul className="list-disc pl-5 text-slate-700">
                {selected.payload.recommended_actions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            )}
            <p className="text-xs text-slate-500">
              The copilot prepared this draft. Nothing happens until a person decides, and only you can.
            </p>
            <label className="block text-xs font-medium text-slate-600" htmlFor="comment">
              Comment
            </label>
            <textarea
              id="comment"
              value={comment}
              maxLength={1000}
              onChange={(e) => setComment(e.target.value)}
              className="w-full rounded border border-slate-300 p-2 text-sm"
              rows={2}
            />
            <div className="flex gap-2">
              <button type="button" onClick={() => submit("approve")} className="rounded bg-emerald-600 px-3 py-1.5 text-white hover:bg-emerald-700">
                Approve
              </button>
              <button type="button" onClick={() => submit("reject")} className="rounded border border-slate-300 px-3 py-1.5 hover:bg-slate-50">
                Reject
              </button>
            </div>
          </div>
        )}
        {error && (
          <div role="alert" className="mt-3 rounded bg-rose-50 p-3 text-sm text-rose-900">
            <p>{errorCopy(error.code)}</p>
            {error.stepUpUrl && (
              <a href={error.stepUpUrl} className="mt-1 inline-block font-medium underline">
                Verify it&apos;s you
              </a>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
