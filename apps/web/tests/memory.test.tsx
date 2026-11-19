import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HistoryPanel } from "../src/components/HistoryPanel";
import { MemoryPanel } from "../src/components/MemoryPanel";
import * as api from "../src/lib/api";

afterEach(() => vi.restoreAllMocks());

const item = (over: Partial<api.MemoryItemView> = {}): api.MemoryItemView => ({
  pref_id: "p1",
  kind: "preference",
  name: null,
  text: "I usually mean HAL-DE",
  created_at: "2026-06-30T10:00:00Z",
  ...over,
});

describe("MemoryPanel", () => {
  it("shows what is remembered and who controls it", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([item()]);
    render(<MemoryPanel />);
    expect(await screen.findByText("I usually mean HAL-DE")).toBeInTheDocument();
    expect(screen.getByText(/you control it/i)).toBeInTheDocument();
  });

  it("says plainly when nothing is remembered", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([]);
    render(<MemoryPanel />);
    expect(await screen.findByText(/nothing is remembered/i)).toBeInTheDocument();
  });

  it("saves a preference the user typed", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([]);
    const add = vi.spyOn(api, "addMemory").mockResolvedValue(item({ text: "Show EUR" }));
    render(<MemoryPanel />);
    await userEvent.type(await screen.findByLabelText(/what should i remember/i), "Show EUR");
    await userEvent.click(screen.getByRole("button", { name: /^remember$/i }));
    await waitFor(() => expect(add).toHaveBeenCalledWith({ kind: "preference", text: "Show EUR" }));
    expect(await screen.findByText("Show EUR")).toBeInTheDocument();
  });

  it("asks for a name only for a saved definition", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([]);
    const add = vi.spyOn(api, "addMemory").mockResolvedValue(item({ kind: "saved_definition", name: "big payment" }));
    render(<MemoryPanel />);
    expect(screen.queryByLabelText(/^name$/i)).toBeNull();
    await userEvent.selectOptions(await screen.findByLabelText(/kind/i), "saved_definition");
    await userEvent.type(screen.getByLabelText(/^name$/i), "big payment");
    await userEvent.type(screen.getByLabelText(/what should i remember/i), "amount below -1000000");
    await userEvent.click(screen.getByRole("button", { name: /^remember$/i }));
    await waitFor(() =>
      expect(add).toHaveBeenCalledWith({
        kind: "saved_definition",
        name: "big payment",
        text: "amount below -1000000",
      }),
    );
  });

  it("deletes one item", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([item()]);
    const del = vi.spyOn(api, "deleteMemory").mockResolvedValue();
    render(<MemoryPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /forget.*usually mean/i }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(screen.queryByText("I usually mean HAL-DE")).toBeNull());
  });

  it("explains a refused item in plain words, without showing what was typed back as an error", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([]);
    vi.spyOn(api, "addMemory").mockRejectedValue(new api.ApiError("INVALID_MEMORY", 422));
    render(<MemoryPanel />);
    await userEvent.type(await screen.findByLabelText(/what should i remember/i), "DE89 3704");
    await userEvent.click(screen.getByRole("button", { name: /^remember$/i }));
    expect(await screen.findByText(/can't be remembered/i)).toBeInTheDocument();
  });

  it("says when the limit has been reached", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([]);
    vi.spyOn(api, "addMemory").mockRejectedValue(new api.ApiError("MEMORY_LIMIT", 409));
    render(<MemoryPanel />);
    await userEvent.type(await screen.findByLabelText(/what should i remember/i), "one more");
    await userEvent.click(screen.getByRole("button", { name: /^remember$/i }));
    expect(await screen.findByText(/forget something first/i)).toBeInTheDocument();
  });

  it("clears everything on request", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([item()]);
    const clear = vi.spyOn(api, "clearMemory").mockResolvedValue();
    render(<MemoryPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /forget everything/i }));
    await waitFor(() => expect(clear).toHaveBeenCalled());
    expect(await screen.findByText(/nothing is remembered/i)).toBeInTheDocument();
  });
});

describe("HistoryPanel", () => {
  const turns: api.HistoryTurn[] = [
    { conversation_id: "conv-0001-aaaa", question: "Show the five largest outflows", status: "answered", summary: "answered: 5 rows, data as of 2026-06-30", trace_id: null, created_at: "2026-06-30T10:00:00Z" },
    { conversation_id: "conv-0001-aaaa", question: "Predict next year", status: "refused", summary: "refused: unsupported", trace_id: null, created_at: "2026-06-30T09:00:00Z" },
  ];

  it("lists earlier questions with how each ended", async () => {
    vi.spyOn(api, "getHistory").mockResolvedValue(turns);
    render(<HistoryPanel onAsk={() => {}} />);
    expect(await screen.findByText("Show the five largest outflows")).toBeInTheDocument();
    expect(screen.getByText(/refused: unsupported/)).toBeInTheDocument();
  });

  it("offers to ask a question again", async () => {
    vi.spyOn(api, "getHistory").mockResolvedValue(turns);
    const onAsk = vi.fn();
    render(<HistoryPanel onAsk={onAsk} />);
    await userEvent.click((await screen.findAllByRole("button", { name: /ask again/i }))[0]!);
    expect(onAsk).toHaveBeenCalledWith("Show the five largest outflows");
  });

  it("clears the history", async () => {
    vi.spyOn(api, "getHistory").mockResolvedValue(turns);
    const clear = vi.spyOn(api, "clearHistory").mockResolvedValue();
    render(<HistoryPanel onAsk={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: /clear history/i }));
    await waitFor(() => expect(clear).toHaveBeenCalled());
    expect(await screen.findByText(/no earlier questions/i)).toBeInTheDocument();
  });

  it("says that history expires", async () => {
    vi.spyOn(api, "getHistory").mockResolvedValue([]);
    render(<HistoryPanel onAsk={() => {}} />);
    expect(await screen.findByText(/24 hours/i)).toBeInTheDocument();
  });
});
