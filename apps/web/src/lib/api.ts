import type { AskEvent, Draft, HistoryTurn, MemoryItemView, Me, NewMemoryItem, Span } from "./types";

export type { HistoryTurn, MemoryItemView, NewMemoryItem } from "./types";

/** A refusal or failure, as a mapped code. Raw server text never reaches the UI. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly stepUpUrl?: string,
  ) {
    super(code);
  }
}

function csrfToken(): string {
  const match = document.cookie.split("; ").find((c) => c.startsWith("copilot_csrf="));
  return match ? decodeURIComponent(match.slice("copilot_csrf=".length)) : "";
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  const response = await fetch(path, { ...init, method, headers, credentials: "same-origin" });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

async function failure(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error_code?: string; step_up_url?: string };
    return new ApiError(body.error_code ?? "INTERNAL", response.status, body.step_up_url);
  } catch {
    return new ApiError(response.status === 503 ? "UNAVAILABLE" : "INTERNAL", response.status);
  }
}

export async function getMe(): Promise<Me | null> {
  try {
    return await call<Me>("/api/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function logout(): Promise<string> {
  return (await call<{ redirect: string }>("/auth/logout", { method: "POST" })).redirect;
}

/** Parses as much of an SSE buffer as is complete; returns the unfinished tail. */
export function parseSse(rest: string, chunk: string): { events: AskEvent[]; rest: string } {
  const blocks = (rest + chunk).split("\n\n");
  const tail = blocks.pop() ?? "";
  const events: AskEvent[] = [];
  for (const block of blocks) {
    const fields = Object.fromEntries(
      block
        .split("\n")
        .map((line) => line.split(/: (.*)/s))
        .filter((parts): parts is [string, string, string] => parts.length >= 2 && parts[0] !== ""),
    ) as Record<string, string>;
    try {
      if (fields.event && fields.data !== undefined) {
        events.push({ event: fields.event, data: JSON.parse(fields.data) } as AskEvent);
      }
    } catch {
      // A malformed event is dropped; the stream carries on.
    }
  }
  return { events, rest: tail };
}

export async function ask(
  question: string,
  onEvent: (e: AskEvent) => void,
  signal?: AbortSignal,
  conversationId?: string,
): Promise<void> {
  const response = await fetch("/api/ask", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
    body: JSON.stringify(conversationId ? { question, conversation_id: conversationId } : { question }),
    signal,
  });
  if (!response.ok || !response.body) throw await failure(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let rest = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const parsed = parseSse(rest, decoder.decode(value, { stream: true }));
    rest = parsed.rest;
    parsed.events.forEach(onEvent);
  }
}

export const listDrafts = async (): Promise<Draft[]> => (await call<{ drafts: Draft[] }>("/api/drafts")).drafts;
export const submitDraft = (id: string): Promise<Draft> => call(`/api/drafts/${id}/submit`, { method: "POST" });
export const approvalQueue = async (): Promise<Draft[]> =>
  (await call<{ drafts: Draft[] }>("/api/approvals/queue")).drafts;
export const decide = (id: string, decision: "approve" | "reject", comment?: string): Promise<Draft> =>
  call(`/api/approvals/${id}/decision`, { method: "POST", body: JSON.stringify({ decision, comment }) });
export const getTrace = async (id: string): Promise<Span[]> =>
  (await call<{ spans: Span[] }>(`/api/traces/${id}`)).spans;

export const getHistory = async (): Promise<HistoryTurn[]> => (await call<{ turns: HistoryTurn[] }>("/api/history")).turns;
export const clearHistory = async (): Promise<void> => {
  await call("/api/history", { method: "DELETE" });
};
export const listMemory = async (): Promise<MemoryItemView[]> =>
  (await call<{ items: MemoryItemView[] }>("/api/memory")).items;
export const addMemory = async (item: NewMemoryItem): Promise<MemoryItemView> =>
  (await call<{ item: MemoryItemView }>("/api/memory", { method: "POST", body: JSON.stringify(item) })).item;
export const deleteMemory = async (id: string): Promise<void> => {
  await call(`/api/memory/${id}`, { method: "DELETE" });
};
export const clearMemory = async (): Promise<void> => {
  await call("/api/memory", { method: "DELETE" });
};
