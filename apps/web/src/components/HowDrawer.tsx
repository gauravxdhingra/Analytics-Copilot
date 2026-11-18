import { useEffect, useState } from "react";
import { getTrace } from "../lib/api";
import { STEP_LABEL } from "../lib/copy";
import type { Answer, Span } from "../lib/types";

export function HowDrawer({ answer, onClose }: { answer: Answer; onClose: () => void }) {
  const [spans, setSpans] = useState<Span[] | null>(null);
  const [traceFailed, setTraceFailed] = useState(false);

  useEffect(() => {
    if (!answer.trace_id) return;
    let live = true;
    getTrace(answer.trace_id)
      .then((s) => live && setSpans(s))
      .catch(() => live && setTraceFailed(true));
    return () => {
      live = false;
    };
  }, [answer.trace_id]);

  const versions = answer.agent_result?.versions ?? {};
  const origin = spans && spans.length > 0 ? Math.min(...spans.map((s) => s.start_us)) : 0;
  const total = spans && spans.length > 0 ? Math.max(...spans.map((s) => s.start_us + s.duration_us)) - origin : 1;

  return (
    <aside role="dialog" aria-label="How I got this" className="fixed inset-y-0 right-0 z-10 w-full max-w-md overflow-y-auto border-l border-slate-200 bg-white p-5 shadow-xl">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-base font-semibold">How I got this</h2>
        <button type="button" onClick={onClose} className="text-sm text-slate-500 hover:text-slate-900" aria-label="Close">
          Close
        </button>
      </div>

      <section className="mb-5">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Steps</h3>
        <ol className="space-y-1 text-sm">
          {answer.steps.map((s, i) => (
            <li key={`${s}-${i}`} className="flex gap-2">
              <span className="font-mono text-xs text-slate-400">{i + 1}</span>
              <span>{s}</span>
              <span className="text-slate-400">{STEP_LABEL[s] ? `· ${STEP_LABEL[s]}` : ""}</span>
            </li>
          ))}
        </ol>
      </section>

      {answer.sql && (
        <section className="mb-5">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Query (verified before it ran)</h3>
          <pre className="overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-100">{answer.sql}</pre>
          <p className="mt-2 text-xs text-slate-500">
            Parameters: <span className="font-mono">{JSON.stringify(answer.params)}</span>
          </p>
          {answer.table && (
            <p className="mt-1 text-xs text-slate-500">
              {answer.table.row_count} row(s){answer.table.as_of ? `, data as of ${answer.table.as_of}` : ""}.
            </p>
          )}
        </section>
      )}

      {Object.keys(versions).length > 0 && (
        <section className="mb-5">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Versions</h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            {Object.entries(versions).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-slate-500">{k}</dt>
                <dd className="font-mono">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Trace</h3>
        {traceFailed && <p className="text-xs text-amber-700">The trace is unavailable right now.</p>}
        {!traceFailed && !answer.trace_id && <p className="text-xs text-slate-500">No trace was recorded.</p>}
        {spans && spans.length === 0 && <p className="text-xs text-slate-500">No spans yet.</p>}
        {spans && spans.length > 0 && (
          <ul className="space-y-1">
            {[...spans]
              .sort((a, b) => a.start_us - b.start_us)
              .map((s) => (
                <li key={s.span_id} className="text-xs">
                  <div className="flex justify-between">
                    <span className="truncate">{s.name}</span>
                    <span className="text-slate-400">{(s.duration_us / 1000).toFixed(0)} ms</span>
                  </div>
                  <div className="h-1.5 rounded bg-slate-100">
                    <div
                      className="h-1.5 rounded bg-indigo-500"
                      style={{
                        marginLeft: `${((s.start_us - origin) / total) * 100}%`,
                        width: `${Math.max((s.duration_us / total) * 100, 1)}%`,
                      }}
                    />
                  </div>
                </li>
              ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
