import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  addMemory,
  ask,
  clearHistory,
  clearMemory,
  decide,
  deleteMemory,
  getHistory,
  getMe,
  listMemory,
  parseSse,
} from "../src/lib/api";
import type { AskEvent } from "../src/lib/types";

function sse(...events: Array<[string, unknown]>): Response {
  const body = events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

describe("parseSse", () => {
  it("emits complete events and keeps a partial one for the next chunk", () => {
    const first = parseSse("", 'event: step\ndata: {"step":"plan"}\n\nevent: ans');
    expect(first.events).toEqual([{ event: "step", data: { step: "plan" } }]);
    const second = parseSse(first.rest, 'wer\ndata: {"status":"answered"}\n\n');
    expect(second.events).toEqual([{ event: "answer", data: { status: "answered" } }]);
    expect(second.rest).toBe("");
  });

  it("ignores malformed events instead of throwing", () => {
    const parsed = parseSse("", "event: step\ndata: {not json\n\nevent: done\ndata: {}\n\n");
    expect(parsed.events).toEqual([{ event: "done", data: {} }]);
  });
});

describe("api", () => {
  beforeEach(() => {
    document.cookie = "copilot_csrf=tok123";
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "copilot_csrf=; max-age=0";
  });

  it("getMe returns null when signed out", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"error_code":"UNAUTHENTICATED"}', { status: 401 })));
    expect(await getMe()).toBeNull();
  });

  it("sends the CSRF token and cookies with every state-changing call", async () => {
    const fetchMock = vi.fn(async () => sse(["done", {}]));
    vi.stubGlobal("fetch", fetchMock);
    await ask("How many?", () => {});
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/ask");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
  });

  it("streams steps then the answer, in order", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sse(["step", { step: "plan" }], ["step", { step: "execute" }], ["answer", { status: "answered", text: "42" }], ["done", {}]),
      ),
    );
    const seen: AskEvent[] = [];
    await ask("q", (e) => seen.push(e));
    expect(seen.map((e) => e.event)).toEqual(["step", "step", "answer", "done"]);
  });

  it("raises a mapped error for a refused call, never raw text", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"error_code":"RATE_LIMITED"}', { status: 429 })));
    await expect(ask("q", () => {})).rejects.toMatchObject({ code: "RATE_LIMITED" });
    await expect(ask("q", () => {})).rejects.toBeInstanceOf(ApiError);
  });

  it("surfaces the step-up url from a decision refusal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response('{"error_code":"STEP_UP_REQUIRED","step_up_url":"/auth/step-up?return=%2F"}', { status: 403 }),
      ),
    );
    await expect(decide("d", "approve")).rejects.toMatchObject({
      code: "STEP_UP_REQUIRED",
      stepUpUrl: "/auth/step-up?return=%2F",
    });
  });
});

describe("memory and history api", () => {
  beforeEach(() => {
    document.cookie = "copilot_csrf=tok123";
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "copilot_csrf=; max-age=0";
  });

  it("sends the conversation id with a question", async () => {
    const fetchMock = vi.fn(async () => sse(["done", {}]));
    vi.stubGlobal("fetch", fetchMock);
    await ask("And the next 5?", () => {}, undefined, "conv-0001-aaaa");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      question: "And the next 5?",
      conversation_id: "conv-0001-aaaa",
    });
  });

  it("asks without a conversation id when there is none", async () => {
    const fetchMock = vi.fn(async () => sse(["done", {}]));
    vi.stubGlobal("fetch", fetchMock);
    await ask("q", () => {});
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ question: "q" });
  });

  it("lists history and saved memory", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/history"
          ? new Response(JSON.stringify({ turns: [{ question: "q", status: "answered" }] }))
          : new Response(JSON.stringify({ items: [{ pref_id: "p", text: "t" }] })),
      ),
    );
    expect(await getHistory()).toHaveLength(1);
    expect(await listMemory()).toHaveLength(1);
  });

  it("changes memory only with the CSRF token", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ item: { pref_id: "p" } }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    await addMemory({ kind: "preference", text: "Show EUR" });
    await deleteMemory("00000000-0000-4000-8000-000000000001");
    await clearMemory();
    await clearHistory();
    for (const call of fetchMock.mock.calls as unknown as Array<[string, RequestInit]>) {
      expect((call[1].headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
    }
    expect(fetchMock.mock.calls.map((c) => (c as unknown as [string, RequestInit])[1].method)).toEqual([
      "POST",
      "DELETE",
      "DELETE",
      "DELETE",
    ]);
  });

  it("surfaces a refused memory item as a mapped code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"error_code":"INVALID_MEMORY"}', { status: 422 })));
    await expect(addMemory({ kind: "preference", text: "x" })).rejects.toMatchObject({
      code: "INVALID_MEMORY",
    });
  });
});
