import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import * as api from "../src/lib/api";
import type { AskEvent, Me } from "../src/lib/types";
import { answered } from "./fixtures";

const analyst: Me = {
  username: "ana.analyst",
  roles: ["analyst"],
  entities: ["HAL-DE"],
  tenant_id: "t",
  acr: "1",
  strong_authentication: false,
};
const approver: Me = { ...analyst, username: "amit.approver", roles: ["approver"] };

afterEach(() => vi.restoreAllMocks());

describe("App", () => {
  it("sends a signed-out visitor to the real login, not a form of its own", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue(null);
    render(<App />);
    const link = await screen.findByRole("link", { name: /sign in/i });
    expect(link).toHaveAttribute("href", "/auth/login");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows who you are and which entities you can see", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue(analyst);
    render(<App />);
    expect(await screen.findByText("ana.analyst")).toBeInTheDocument();
    expect(screen.getByText(/HAL-DE/)).toBeInTheDocument();
  });

  it("hides the approvals tab from people who cannot approve", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue(analyst);
    render(<App />);
    await screen.findByText("ana.analyst");
    expect(screen.queryByRole("tab", { name: /approvals/i })).toBeNull();
  });

  it("shows the approvals tab to approvers", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue(approver);
    vi.spyOn(api, "approvalQueue").mockResolvedValue([]);
    render(<App />);
    await userEvent.click(await screen.findByRole("tab", { name: /approvals/i }));
    expect(await screen.findByText(/nothing is waiting/i)).toBeInTheDocument();
  });

  it("streams progress, then shows the answer", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue(analyst);
    vi.spyOn(api, "ask").mockImplementation(async (_q, onEvent: (e: AskEvent) => void) => {
      onEvent({ event: "step", data: { step: "plan" } });
      onEvent({ event: "answer", data: answered });
      onEvent({ event: "done", data: {} });
    });
    render(<App />);
    await userEvent.type(await screen.findByRole("textbox", { name: /question/i }), "How many?{enter}");
    expect(await screen.findByText(/12 booked transactions/)).toBeInTheDocument();
    expect(api.ask).toHaveBeenCalledWith("How many?", expect.any(Function), expect.anything());
  });

  it("shows a mapped message when asking fails, and keeps the question usable", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue(analyst);
    vi.spyOn(api, "ask").mockRejectedValue(new api.ApiError("RATE_LIMITED", 429));
    render(<App />);
    await userEvent.type(await screen.findByRole("textbox", { name: /question/i }), "q{enter}");
    expect(await screen.findByText(/asking too quickly/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox", { name: /question/i })).toBeEnabled());
  });

  it("returns to sign-in when the session has ended mid-conversation", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue(analyst);
    vi.spyOn(api, "ask").mockRejectedValue(new api.ApiError("UNAUTHENTICATED", 401));
    render(<App />);
    await userEvent.type(await screen.findByRole("textbox", { name: /question/i }), "q{enter}");
    expect(await screen.findByRole("link", { name: /sign in/i })).toBeInTheDocument();
  });
});
