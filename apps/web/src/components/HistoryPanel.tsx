import { useEffect, useState } from "react";
import { ApiError, clearHistory, getHistory } from "../lib/api";
import type { HistoryTurn } from "../lib/api";
import { errorCopy } from "../lib/copy";

export function HistoryPanel({ onAsk }: { onAsk: (question: string) => void }) {
  const [turns, setTurns] = useState<HistoryTurn[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHistory()
      .then(setTurns)
      .catch((e) => {
        setTurns([]);
        setError(errorCopy(e instanceof ApiError ? e.code : "INTERNAL"));
      });
  }, []);

  async function clear() {
    try {
      await clearHistory();
      setTurns([]);
    } catch (e) {
      setError(errorCopy(e instanceof ApiError ? e.code : "INTERNAL"));
    }
  }

  return (
    <section aria-label="History" className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">Your recent questions</h2>
          <p className="text-xs text-slate-500">Questions are kept for 24 hours, then forgotten.</p>
        </div>
        {turns && turns.length > 0 && (
          <button type="button" onClick={() => void clear()} className="text-xs text-slate-500 hover:text-rose-700 hover:underline">
            Clear history
          </button>
        )}
      </div>
      {turns === null && <p className="text-sm text-slate-500">Loading…</p>}
      {turns?.length === 0 && <p className="text-sm text-slate-500">No earlier questions.</p>}
      <ul className="space-y-2">
        {turns?.map((t, i) => (
          <li key={`${t.created_at}-${i}`} className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3 text-sm">
            <div>
              <p className="font-medium">{t.question}</p>
              <p className="text-xs text-slate-500">{t.summary}</p>
            </div>
            <button type="button" onClick={() => onAsk(t.question)} className="shrink-0 text-xs font-medium text-indigo-700 hover:underline">
              Ask again
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="text-sm text-rose-800">
          {error}
        </p>
      )}
    </section>
  );
}
