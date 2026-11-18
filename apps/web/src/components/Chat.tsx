import { useEffect, useRef, useState } from "react";
import { ApiError, ask } from "../lib/api";
import { errorCopy, STEP_LABEL } from "../lib/copy";
import type { Answer } from "../lib/types";
import { AnswerView } from "./AnswerView";
import { HowDrawer } from "./HowDrawer";

type Turn = { id: number; question: string; answer?: Answer; error?: string; steps: string[]; pending: boolean };

export function Chat({ onSignedOut }: { onSignedOut: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Answer | null>(null);
  const next = useRef(1);
  const controller = useRef(new AbortController());
  // Leaving the page stops the stream, and with it the spend on the user's behalf.
  useEffect(() => () => controller.current.abort(), []);

  const patch = (id: number, change: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...(typeof change === "function" ? change(t) : change) } : t)));

  async function send() {
    const question = text.trim();
    if (!question || busy) return;
    const id = next.current++;
    setText("");
    setBusy(true);
    controller.current = new AbortController();
    setTurns((all) => [...all, { id, question, steps: [], pending: true }]);
    try {
      await ask(
        question,
        (event) => {
          if (event.event === "step") patch(id, (t) => ({ steps: [...t.steps, event.data.step] }));
          else if (event.event === "answer") patch(id, { answer: event.data });
          else if (event.event === "error") patch(id, { error: errorCopy(event.data.error_code) });
        },
        controller.current.signal,
      );
    } catch (error) {
      if (error instanceof ApiError && error.code === "UNAUTHENTICATED") return onSignedOut();
      patch(id, { error: errorCopy(error instanceof ApiError ? error.code : "INTERNAL") });
    } finally {
      patch(id, { pending: false });
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-6 overflow-y-auto pb-4" aria-live="polite">
        {turns.length === 0 && (
          <p className="text-sm text-slate-500">
            Ask about payments, balances or exposures. Try: “How many booked transactions are there for each entity I can see?”
          </p>
        )}
        {turns.map((t) => (
          <article key={t.id} className="space-y-2">
            <p className="ml-auto w-fit max-w-[85%] rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white">{t.question}</p>
            {t.pending && !t.answer && (
              <p className="text-xs text-slate-500">{t.steps.length ? STEP_LABEL[t.steps[t.steps.length - 1]!] ?? t.steps[t.steps.length - 1] : "Working"}…</p>
            )}
            {t.answer && <AnswerView answer={t.answer} onHow={() => setOpen(t.answer!)} />}
            {t.error && (
              <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-900">
                {t.error}
              </p>
            )}
          </article>
        ))}
      </div>
      <form
        className="flex gap-2 border-t border-slate-200 pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          aria-label="Question"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          maxLength={2000}
          placeholder="Ask a question…"
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-indigo-500 disabled:bg-slate-100"
        />
        <button type="submit" disabled={busy || !text.trim()} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50">
          Ask
        </button>
      </form>
      {open && <HowDrawer answer={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
