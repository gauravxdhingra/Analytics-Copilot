import type { Answer } from "../lib/types";
import { EvidenceChips } from "./EvidenceChips";

const format = (value: unknown): string => (value === null || value === undefined ? "" : String(value));

export function AnswerView({ answer, onHow }: { answer: Answer; onHow: () => void }) {
  if (answer.status === "refused" || answer.status === "failed") {
    return (
      <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
        <p className="mb-1 font-medium">{answer.status === "refused" ? "Blocked" : "Could not answer"}</p>
        <p>{answer.text}</p>
      </div>
    );
  }
  if (answer.status === "clarify") {
    return <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900">{answer.clarification ?? answer.text}</p>;
  }
  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 text-sm shadow-sm">
      {/* React escapes text: model output is data, never markup. */}
      <p className="whitespace-pre-wrap text-slate-900">{answer.text}</p>
      {answer.table && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr>
                {answer.table.columns.map((c) => (
                  <th key={c} className="px-2 py-1 font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {answer.table.rows.map((row, i) => (
                <tr key={i} className="border-b border-slate-100">
                  {row.map((cell, j) => (
                    <td key={j} className="px-2 py-1 tabular-nums">
                      {format(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {answer.table.truncated && (
            <p className="mt-1 text-xs text-slate-500">Showing {answer.table.rows.length} of {answer.table.row_count} rows.</p>
          )}
        </div>
      )}
      {answer.caveats.map((c) => (
        <p key={c} className="text-xs text-slate-500">
          {c}
        </p>
      ))}
      {answer.agent_result && <EvidenceChips claims={answer.agent_result.claims} />}
      <div className="flex items-center gap-3 pt-1">
        <button type="button" onClick={onHow} className="text-xs font-medium text-indigo-700 hover:underline">
          How I got this
        </button>
        {answer.draft_id && <span className="text-xs text-emerald-700">Draft created for review</span>}
        {answer.draft_error && <span className="text-xs text-amber-700">A draft could not be created</span>}
      </div>
    </div>
  );
}
